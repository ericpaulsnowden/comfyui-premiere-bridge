"""``PremiereFrameSource`` + ``PremiereClipSource`` ComfyUI nodes (PROTOCOL.md §11).

M2's RETURN direction: Premiere hands ComfyUI something to work ON, where
M1 (``nodes_send``) only ever handed Premiere a finished result. The panel's
"SEND TO COMFYUI" buttons produce an ``export_ready`` message (§11), the
frontend (``web/cprb/premiere_source.js``) writes its payload into these
nodes' widgets, and the USER presses Run -- nothing here ever auto-queues.

Those widgets are ORDINARY VISIBLE widgets, deliberately: nothing hides
them and nothing should. They serialize into a saved workflow, they can be
hand-typed with no panel connected at all (§1's ethos), and -- the reason
that matters most in practice -- a path sitting in plain sight is a second
confirmation channel independent of the frontend's toast. The relay finds
them BY NAME on ``node.widgets``, so any future "hide" must keep them in
that list (a draw-time hide, never a splice).

Two flavors, because Premiere gives two genuinely different things:

* **Frame from Premiere** -- a still Premiere exported for us
  (``Exporter.exportSequenceFrame``, written into the ``frames_dir`` the
  server reports in ``hello_ack``). This node just reads that file.
* **Clip from Premiere** -- NO export at all: the selected clip's OWN media
  file plus its source in/out. The heavy lifting (decode, trim) is left to
  the existing ecosystem, which is why this node emits the pack's already-
  shipped ``CPRB_SEGMENT_LIST`` (PROTOCOL.md §6.2) as its primary output: one
  shot, in the FROZEN §6.2 shape, so it plugs straight into the shipped
  ``Get Segment`` / ``Get Segment Frame`` / ``Iterate Segments`` nodes with no new
  consumer written for it (§1's ethos: existing nodes first).

Module-scope imports stay ComfyUI-free and dependency-light, exactly like
every other feature module here: ``torch``/``numpy``/``PIL`` load only
inside :func:`load_image_file`, and ``av`` only inside
:func:`cprb.probe.probe_media` (its own lazy import), so ``import
cprb.nodes_source`` needs none of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .probe import ProbeError, probe_media

if TYPE_CHECKING:
    from .context import BridgeContext

logger = logging.getLogger("cprb")

#: Both nodes here are bucket 2: the Premiere panel is the ONLY producer of
#: their input (§11.3's export_ready relay). They still RUN on a hand-typed
#: path, but that is a fallback, not the feature.
CATEGORY_NAME = "Premiere Bridge/Handoffs (requires Premiere)"

#: PROTOCOL.md §6.2's ``start``/``end`` (a shot's position on the TIMELINE)
#: for a segment that has no timeline position to report.
#: :class:`PremiereClipSource` is exactly that case: §11's ``export_ready``
#: clip payload carries the clip's SOURCE in/out and nothing about where it
#: sits in the sequence, so inventing ``start=0`` would be a fabricated
#: fact. ``-1`` is the pack's OWN pre-existing sentinel for this -- see
#: ``timeline_read._resolve_track_spans`` (which keeps a literal ``-1`` when
#: it cannot recover a span) and ``nodes_load._summary_line`` (which prints
#: ``[TIMELINE POSITION UNRESOLVED]`` for it) -- so reusing it means every
#: existing consumer already handles this segment correctly. Nothing in
#: ``Get Segment`` / ``Iterate Segments`` / ``Get Segment Frame`` reads ``start``/
#: ``end`` at all; they work purely off ``in``/``out``/``source_fps``.
UNRESOLVED_TIMELINE_FRAME = -1

#: Declared ceiling for the two seconds widgets. ~11.5 days of media -- wide
#: enough that no real clip can hit it, finite so the widget is sane.
MAX_SECONDS = 1_000_000.0

#: PROTOCOL.md §11: the frontend fills these widgets in from the panel's
#: push, so the friendly message for an empty one must say which BUTTON to
#: press -- never "type a path", which is not how this node is driven.
UNSET_FRAME_MESSAGE = (
    "No frame from Premiere yet. In Premiere's ComfyUI panel, park the playhead on "
    'the frame you want and click "Frame → ComfyUI" -- this node\'s path fills in '
    "by itself, there is nothing to type."
)

UNSET_CLIP_MESSAGE = (
    "No clip from Premiere yet. In Premiere, select a clip in the Timeline and click "
    '"Clip → ComfyUI" in the ComfyUI panel -- this node\'s path and in/out fill in '
    "by itself, there is nothing to type."
)

_context: BridgeContext | None = None


def set_context(context: BridgeContext | None) -> None:
    """Wire the shared :class:`~cprb.context.BridgeContext` into this module.

    Called once from the top-level ``__init__.py`` (real runs); tests may
    call it directly or skip it. Neither node below actually reads *context*
    -- both are driven by ABSOLUTE paths the plugin already resolved (the
    frame lands in the ``frames_dir`` the server reported in ``hello_ack``,
    PROTOCOL.md §11; a clip's media path is Premiere's own) -- but the
    loader calls ``set_context`` on every configured feature module
    unconditionally, so this must exist regardless. ``None`` is accepted so
    tests can reset the module global between cases (mirrors
    ``nodes_load.set_context``).
    """
    global _context
    _context = context


def _as_float(value: Any, default: float = 0.0) -> float:
    """*value* as a float, or *default* when it isn't numeric at all.

    ComfyUI passes real floats for a FLOAT widget, but these widgets are
    written PROGRAMMATICALLY by the frontend from a websocket payload
    (PROTOCOL.md §11), and a saved workflow can restore whatever was stored
    last. Coercing defensively keeps a junk value out of
    :meth:`PremiereClipSource.VALIDATE_INPUTS` -- whose whole job is to
    produce a friendly message rather than a traceback.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _existing_file(raw: Any) -> Path | None:
    """*raw* as an existing file, or ``None`` (empty, missing, not a file)."""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    try:
        return path if path.is_file() else None
    except OSError:
        # A malformed path (embedded NULs, an over-long name) is "not a file",
        # never an exception out of a validation helper.
        return None


def resolve_frame_path(raw: Any) -> Path | None:
    """The exported still *raw* actually names on disk, or ``None``.

    Normally just *raw* itself. The fallback exists for ONE documented
    Premiere defect: ``Exporter.exportSequenceFrame`` used to append the
    format to the filename it was given (``abcd.jpg`` → ``abcd.jpg.jpg``).
    That was fixed in Premiere 26.2.2 -- one patch release before the build
    this pack targets -- but Adobe's own sample panel still documents the
    OLD behavior, so a regression would otherwise surface here as a
    baffling "file not found" for a frame the user can plainly see in the
    folder. Checking the doubled name costs one ``stat`` and only ever runs
    when the reported path is missing, so it can never shadow a real file.
    Verifying server-side like this is also what keeps the plugin's
    ``localFileSystem: "request"`` manifest permission unchanged -- Python
    has no UXP sandbox to negotiate.
    """
    path = _existing_file(raw)
    if path is not None:
        return path
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.suffix:
        return None
    doubled = candidate.with_name(candidate.name + candidate.suffix)
    try:
        return doubled if doubled.is_file() else None
    except OSError:
        return None


def _stat_token(path: Path | None) -> str:
    """``mtime_ns:size`` for *path* — an ``IS_CHANGED`` token (PROTOCOL.md §11).

    Both source nodes need this: the panel can export a NEW frame to the
    SAME path, or the user can re-render the media behind a clip, with
    every node input string unchanged. Without this the second push would
    be served from ComfyUI's cache and silently show the first frame.
    ``"missing"`` for anything unstat-able, so the node still runs and
    raises its own clear error instead of failing inside ``IS_CHANGED``.
    """
    if path is None:
        return "missing"
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def load_image_file(path: Path) -> tuple[Any, int, int]:
    """*path* as a ComfyUI ``IMAGE`` tensor plus its pixel width/height.

    Returns a ``torch.float32`` tensor of shape ``[1, H, W, 3]`` (batch of
    1, HWC, RGB) with values in ``[0, 1]`` -- the standard ComfyUI IMAGE
    shape/dtype, identical to what :func:`cprb.frame_extract.extract_frame`
    already produces for ``Get Segment Frame``. Greyscale, palette and RGBA
    sources are converted to RGB (an alpha channel is dropped, not
    composited -- Premiere's PNG frame export is opaque).

    ``torch``/``numpy``/``PIL`` are imported HERE, never at module scope
    (pack convention -- see the module docstring).

    Raises:
        ValueError: *path* cannot be opened, decoded, or converted to RGB.
            This is also the shape a HALF-WRITTEN file takes: Premiere's
            exporter can return success before the bytes are on disk, so a
            truncated/zero-byte PNG is a real, expected failure here -- the
            message names the file and the user simply exports again.
    """
    import numpy as np
    import torch
    from PIL import Image

    try:
        with Image.open(path) as opened:
            rgb = opened if opened.mode == "RGB" else opened.convert("RGB")
            array = np.array(rgb, dtype=np.float32)
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Frame from Premiere: could not read the exported frame: {path} ({exc}). "
            "If Premiere had only just written it, export it again."
        ) from exc

    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(
            f"Frame from Premiere: unexpected image shape {array.shape} for {path} "
            "(expected height x width x RGB)"
        )

    array /= 255.0
    tensor = torch.from_numpy(array).unsqueeze(0)  # [1, H, W, 3]
    return tensor, int(tensor.shape[2]), int(tensor.shape[1])


@dataclass
class ClipShot:
    """One Premiere clip selection resolved into PROTOCOL.md §6.2 shape.

    Attributes:
        shot: The §6.2 segment dict -- FROZEN keys, so it wires straight into
            ``Get Segment`` / ``Get Segment Frame`` / ``Iterate Segments``.
        start_seconds: The EFFECTIVE source in point actually used (after
            clamping/fallback), not necessarily the raw widget value.
        end_seconds: The EFFECTIVE source out point actually used.
        notes: Human-readable lines for anything that had to be defaulted or
            clamped. :meth:`PremiereClipSource.execute` logs them -- §11's
            "never invent a value silently".
    """

    shot: dict[str, Any]
    start_seconds: float
    end_seconds: float
    notes: list[str] = field(default_factory=list)


def build_clip_shot(media: Path, start_seconds: float, end_seconds: float) -> ClipShot:
    """A single-shot PROTOCOL.md §6.2 record for a Premiere clip selection.

    §11's clip payload carries only ``path`` + ``start_seconds`` /
    ``end_seconds`` (the clip's in/out in its SOURCE media, per Premiere's
    ``getInPoint``/``getOutPoint``, which are already media-relative). §6.2
    wants frame numbers, a frame rate and a resolution too, so the media
    file itself is probed with the pack's EXISTING
    :func:`cprb.probe.probe_media` -- no new probing code, no new
    dependency.

    Field by field, with every DEFAULT stated (§11: never invent a value
    silently -- each one below is also reported in :attr:`ClipShot.notes`
    when it actually kicks in):

    * ``in``/``out`` -- ``round(seconds * source_fps)``. ``end_seconds`` is
      treated as EXCLUSIVE (the out point is one frame past the last frame
      the user selected), which is what makes ``Get Segment``'s
      ``frame_count = out - in`` come out right and matches Premiere's own
      model.
    * ``source_fps`` -- the probed rate. ``sequence_fps`` -- DEFAULTED to
      the same value: a clip selection says nothing about the sequence's
      rate. Nothing in §6.3's consumers reads ``sequence_fps`` (they all
      work in ``source_fps``, since ``in``/``out`` are source positions),
      so this default cannot skew any downstream number.
    * ``width``/``height`` -- probed.
    * ``name`` -- the media file's stem. §11's ``label`` (Premiere's own
      clip name, which may be a timeline rename) is deliberately NOT a
      widget on this node, so the file stem is the honest answer.
    * ``start``/``end`` -- :data:`UNRESOLVED_TIMELINE_FRAME`; see there.
    * ``enabled`` -- ``True``: the user explicitly selected this clip.

    Raises:
        ValueError: the media can't be probed (offline, or a codec ffmpeg
            can't read even though Premiere can -- R3D and friends). Hard
            failure on purpose: without a rate and a resolution there is no
            honest §6.2 shot to emit, and every downstream loader would
            fail on that same file anyway. Also raised for an out point at
            or before the in point (see also
            :meth:`PremiereClipSource.VALIDATE_INPUTS`, which catches it
            before the prompt is even queued).
    """
    notes: list[str] = []
    try:
        info = probe_media(str(media))
    except ProbeError as exc:
        raise ValueError(
            f"Clip from Premiere: could not read the clip's media file ({exc}). "
            "A shot needs its frame rate and resolution; check the clip is online "
            "and in a format ffmpeg can open."
        ) from exc

    source_fps = float(info.fps)
    start = _as_float(start_seconds)
    if start < 0:
        notes.append(f"start_seconds was negative ({start}) -- clamped to 0")
        start = 0.0

    end = _as_float(end_seconds)
    whole_file = end <= 0
    if whole_file:
        end = float(info.duration_seconds)
        notes.append(
            "end_seconds was not set -- using the media's full probed duration "
            f"({end:.3f}s) as the out point"
        )
    if end <= start:
        # Two different user-visible causes land here; say which one, because
        # the fix differs (re-trim the clip vs. the wrong media/in point).
        cause = (
            f"the in point ({start:g}s) is at or past the end of the clip's media ({end:.3f}s)"
            if whole_file
            else f"the clip's out point ({end:g}s) is at or before its in point ({start:g}s)"
        )
        raise ValueError(
            f"Clip from Premiere: {cause} -- re-select the clip in Premiere and send it again."
        )

    in_frame = round(start * source_fps)
    out_frame = round(end * source_fps)
    if out_frame <= in_frame:
        # A sub-frame span (both ends inside one frame) would otherwise emit
        # frame_count == 0 and load nothing downstream.
        out_frame = in_frame + 1
        notes.append(
            f"the {end - start:.4f}s span is shorter than one frame at "
            f"{source_fps:g}fps -- emitting a single frame"
        )

    notes.append(
        f"sequence_fps defaulted to the source rate ({source_fps:g}) -- a Premiere "
        "clip selection carries no sequence rate"
    )

    shot: dict[str, Any] = {
        # PROTOCOL.md §6.2's FROZEN key set, in §6.1's order.
        "name": media.stem,
        "path": str(media),
        "start": UNRESOLVED_TIMELINE_FRAME,
        "end": UNRESOLVED_TIMELINE_FRAME,
        "in": in_frame,
        "out": out_frame,
        "sequence_fps": source_fps,
        "source_fps": source_fps,
        "enabled": True,
        "width": int(info.width),
        "height": int(info.height),
    }
    return ClipShot(shot=shot, start_seconds=start, end_seconds=end, notes=notes)


def _video_from_file_class() -> Any:
    """ComfyUI core's ``VideoFromFile`` -- the class behind every VIDEO socket.

    A seam, not an inline import: this is the pack's ONLY dependency on a
    ComfyUI-internal class (everything else duck-types, per §1's ethos), and
    the test suite runs without ComfyUI on ``sys.path``, so tests monkeypatch
    this function with a stub and assert the trim arithmetic against it.
    Imported lazily for the same reason as ``torch``/``PIL`` above.

    Raises:
        RuntimeError: this ComfyUI predates the core VIDEO type (the same
            2025+ floor the README's install section already states).
    """
    try:
        from comfy_api.input_impl import VideoFromFile
    except ImportError as exc:
        raise RuntimeError(
            "Clip from Premiere: this ComfyUI build has no core VIDEO type "
            "(comfy_api.input_impl.VideoFromFile) -- update ComfyUI to a 2025+ "
            "build to use the video output."
        ) from exc
    return VideoFromFile


def _clip_video(media: Path, start_seconds: Any, end_seconds: Any) -> Any:
    """*media* as a core ``VIDEO`` object, trimmed to the clip's in/out.

    Built from the RAW widget values (clamped, not the effective values
    ``build_clip_shot`` resolves) so the untrimmed case stays HONESTLY
    untrimmed: core's ``VideoInput.get_active_trim_window()`` uses
    ``(0.0, 0.0)`` as its own "whole file" sentinel, and ``Send to
    Premiere``'s link-in-place branch keys off exactly that -- substituting
    the probed duration for an unset out point would make every whole-file
    clip report itself trimmed and force a needless re-encode on the way
    back into Premiere. ``VideoFromFile`` is LAZY: nothing opens the file
    here, so this costs nothing until a downstream node asks for pixels.
    """
    start = max(_as_float(start_seconds), 0.0)
    end = _as_float(end_seconds)
    # end <= 0 means "whole file" (§11.2) -> duration 0, core's own sentinel
    # for "until the end". A positive end is EXCLUSIVE-by-seconds already, so
    # the duration is a plain difference; build_clip_shot has raised before
    # this point whenever end <= start.
    duration = (end - start) if end > 0 else 0.0
    return _video_from_file_class()(str(media), start_time=start, duration=duration)


class PremiereFrameSource:
    """The frame Premiere just exported, as an IMAGE (PROTOCOL.md §11).

    ``path`` is an ordinary STRING widget that the frontend
    (``web/cprb/premiere_source.js``) fills in from the panel's
    ``export_ready`` push, so the user's whole gesture is "click
    Frame → ComfyUI in Premiere, then Run" -- but it stays visible and
    hand-typeable, which is what keeps this node usable with no plugin
    connected (§1's ethos) and gives the user a second, non-transient
    confirmation that a path arrived.

    Outputs ``image`` plus the frame's ``width``/``height`` (handy for
    matching a generation to the sequence's own resolution without a second
    node) and ``path`` -- the file actually READ, which can differ from the
    widget by one documented Premiere quirk (:func:`resolve_frame_path`).
    """

    CATEGORY = CATEGORY_NAME
    RETURN_TYPES = ("IMAGE", "INT", "INT", "STRING")
    RETURN_NAMES = ("image", "width", "height", "path")
    OUTPUT_TOOLTIPS = (
        "The exported frame.",
        "The frame's pixel width.",
        "The frame's pixel height.",
        "The exported file actually read — may differ from the path widget by one "
        "known Premiere naming quirk.",
    )
    FUNCTION = "execute"
    DESCRIPTION = (
        'The still Premiere exported when you clicked "Frame → ComfyUI" in the panel, as '
        "an IMAGE plus its pixel size. The path fills in by itself, and the workflow runs "
        "automatically unless you turn that off in Settings → Premiere Bridge. You can also "
        "point path at any image file by hand — no plugin connection required."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": (
                            "Filled in automatically by the Premiere panel's "
                            '"Frame → ComfyUI" button (the absolute path of the '
                            "exported still). You can also type a path yourself."
                        ),
                    },
                ),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, path: str) -> bool | str:
        """Friendly upfront check (PROTOCOL.md §11), before the prompt queues.

        The two things that actually go wrong get their own message: the
        widget was never filled in (say which BUTTON to press), and the file
        named is not there (say the path -- Premiere's exporter can report
        success without writing anything, so this is a REAL case, not a
        theoretical one).

        ``None`` means the widget was CONVERTED to a socket and something
        upstream feeds it: ComfyUI hands validation ``None`` for a link it
        cannot evaluate yet (``execution.get_input_data``'s ``mark_missing``),
        which is not the same as "empty" (an unset widget arrives as ``""``).
        That case is passed through -- :meth:`execute` sees the real value and
        raises there if it is unusable.
        """
        if path is None:
            return True
        text = str(path or "").strip()
        if not text:
            return UNSET_FRAME_MESSAGE
        if resolve_frame_path(text) is None:
            return (
                f"Frame file not found: {text} -- Premiere reported it, but it is not "
                'on disk. Click "Frame → ComfyUI" again.'
            )
        return True

    @classmethod
    def IS_CHANGED(cls, path: str = "") -> str:
        """mtime+size of the frame file, so a RE-export to the same path re-runs.

        The panel writes a fresh unique filename per export today, but a
        node whose widget string is unchanged is otherwise cached forever --
        and "export the same frame again after tweaking Premiere" is a
        gesture users will make. See :func:`_stat_token`.
        """
        return _stat_token(resolve_frame_path(path))

    def execute(self, path: str = "") -> tuple[Any, int, int, str]:
        resolved = resolve_frame_path(path)
        if resolved is None:
            text = str(path or "").strip()
            raise ValueError(
                UNSET_FRAME_MESSAGE if not text else f"Frame from Premiere: file not found: {text}"
            )
        image, width, height = load_image_file(resolved)
        return image, width, height, str(resolved)


class PremiereClipSource:
    """The clip selected in Premiere, as a one-segment ``CPRB_SEGMENT_LIST`` (§11).

    NO export and NO re-encode happens for this: the panel sends the
    selected clip's OWN media file path plus its source in/out, and this
    node turns that into the pack's already-shipped ``CPRB_SEGMENT_LIST``
    (PROTOCOL.md §6.2, exactly one segment) -- so ``Get Segment``,
    ``Get Segment Frame`` and ``Iterate Segments`` consume a Premiere selection
    with no new node written for it, and ``Get Segment``'s outputs feed VHS's
    ``Load Video (Path)`` (``skip_first_frames``/``frame_load_cap``) the
    same way a loaded timeline's shots already do.

    ``path``/``start_seconds``/``end_seconds`` are ordinary visible widgets
    the frontend fills in from the panel's push, so hand-typing still works
    with no plugin connected and the arrived values stay on screen.
    ``start_seconds``/``end_seconds`` are ALSO emitted directly, for
    VHS-style consumers that take seconds, and ``path`` for anything that
    just wants the file. Those three outputs report the EFFECTIVE values
    (see :func:`build_clip_shot` for the one fallback that can change them:
    an unset out point becomes the media's full duration).

    ``video`` is a real ComfyUI ``VIDEO`` socket -- core's own
    ``VideoFromFile``, LAZY and zero-copy (nothing is decoded until a
    downstream node asks for pixels), so it plugs directly into
    ``Send to Premiere``, a ``SaveVideo``, or any third-party node that
    declares a plain ``VIDEO`` input (a video-editing model node, for
    instance) -- none of which accept a bare path string. Built from the
    RAW widget values, not the effective ones ``build_clip_shot`` resolves,
    so it keeps the SAME "0 means the whole file" sentinel core's own
    ``VideoInput.get_active_trim_window()`` documents: an unset out point
    yields an untrimmed ``VideoFromFile`` that ``Send to Premiere`` can
    still LINK IN PLACE, rather than one that always reports itself
    trimmed and forces a needless re-encode.
    """

    CATEGORY = CATEGORY_NAME
    RETURN_TYPES = ("CPRB_SEGMENT_LIST", "STRING", "FLOAT", "FLOAT", "VIDEO")
    RETURN_NAMES = ("shots", "path", "start_seconds", "end_seconds", "video")
    OUTPUT_TOOLTIPS = (
        "A one-segment segment list for this clip — wire it into Get Premiere Segment, Get "
        "Premiere Segment Frame, or Iterate Premiere Segments.",
        "The clip's source media file, as an absolute path.",
        "The clip's in-point in its source media, in seconds.",
        "The clip's out-point in its source media, in seconds.",
        "The clip, ready to wire into Send to Premiere, Save Video, or any VIDEO input.",
    )
    FUNCTION = "execute"
    DESCRIPTION = (
        "The clip you selected in Premiere's Timeline, as a one-segment segment list (wire it "
        "into Get Premiere Segment / Get Premiere Segment Frame / Iterate Premiere Segments), "
        "its media path and source in/out in seconds, and a ready-to-wire VIDEO. Nothing is "
        "exported or re-encoded — this just reads the clip's existing media file. The path "
        'and in/out fill in by themselves when you click "Clip → ComfyUI" in the panel, and '
        "the workflow runs automatically unless you turn that off in Settings → Premiere "
        "Bridge; you can also type them by hand."
    )

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        seconds_tooltip = (
            "Filled in automatically by the Premiere panel's "
            '"Clip → ComfyUI" button: the clip\'s in/out inside its SOURCE '
            "media, in seconds. An out point of 0 means the whole file."
        )
        return {
            "required": {
                "path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": (
                            "Filled in automatically by the Premiere panel's "
                            '"Clip → ComfyUI" button (the clip\'s own media file -- '
                            "Premiere exports nothing for this)."
                        ),
                    },
                ),
                "start_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": MAX_SECONDS,
                        "step": 0.001,
                        # round=False keeps the exact value the panel sent: a
                        # frame is ~0.0417s at 23.976fps, so rounding to the
                        # frontend's usual 3 decimals could shift the in point
                        # across a frame boundary.
                        "round": False,
                        "tooltip": seconds_tooltip,
                    },
                ),
                "end_seconds": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": MAX_SECONDS,
                        "step": 0.001,
                        "round": False,
                        "tooltip": seconds_tooltip,
                    },
                ),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(
        cls, path: str, start_seconds: float = 0.0, end_seconds: float = 0.0
    ) -> bool | str:
        """Friendly upfront checks (PROTOCOL.md §11) -- never a traceback.

        Empty widget, missing media, and an inverted in/out each get their
        own message. The media is deliberately NOT probed here: probing
        opens the file with ffmpeg, and doing that once per queue -- twice
        counting :meth:`execute` -- for a check ``execute`` already makes is
        exactly the trade ``PremiereLoadTimeline.VALIDATE_INPUTS`` declines
        for XML parsing.

        ``path is None`` (a converted widget fed by a link, which ComfyUI
        cannot evaluate at validation time) is passed through, exactly as in
        :meth:`PremiereFrameSource.VALIDATE_INPUTS`.
        """
        if path is None:
            return True
        text = str(path or "").strip()
        if not text:
            return UNSET_CLIP_MESSAGE
        if _existing_file(text) is None:
            return (
                f"Clip media not found: {text} -- the clip may be offline in Premiere, "
                "or its media may live on a drive this machine cannot see."
            )
        start = max(_as_float(start_seconds), 0.0)
        end = _as_float(end_seconds)
        if 0 < end <= start:
            return (
                f"Clip out point ({end}s) is at or before its in point ({start}s) -- "
                "re-select the clip in Premiere and send it again."
            )
        return True

    @classmethod
    def IS_CHANGED(
        cls, path: str = "", start_seconds: float = 0.0, end_seconds: float = 0.0
    ) -> str:
        """mtime+size of the clip's media file, so re-rendered media re-runs.

        The two seconds widgets are accepted (ComfyUI passes every declared
        input) but not folded into the token, for the same reason
        ``PremiereLoadTimeline.IS_CHANGED`` leaves ``skip_disabled`` out:
        ComfyUI already re-runs a node whose literal widget value changed.
        This token only covers what it cannot see -- the FILE changing under
        an unchanged path.
        """
        return _stat_token(_existing_file(path))

    def execute(
        self, path: str = "", start_seconds: float = 0.0, end_seconds: float = 0.0
    ) -> tuple[list[dict[str, Any]], str, float, float, Any]:
        media = _existing_file(path)
        if media is None:
            text = str(path or "").strip()
            raise ValueError(
                UNSET_CLIP_MESSAGE
                if not text
                else f"Clip from Premiere: media file not found: {text}"
            )
        resolved = build_clip_shot(media, _as_float(start_seconds), _as_float(end_seconds))
        shot = resolved.shot
        video = _clip_video(media, start_seconds, end_seconds)
        # ALWAYS logged, not just on a fallback: whether Premiere's
        # getOutPoint() is inclusive or exclusive is undocumented, and this
        # node commits to EXCLUSIVE (see build_clip_shot). These are the exact
        # numbers that settle it -- compare frame_count against the duration
        # Premiere itself shows for the clip. Without this line the resulting
        # off-by-one frame is invisible: it only surfaces downstream, in
        # Get Segment's frame_count, with nothing to compare against.
        logger.info(
            "cprb clip_source: %s -> in=%d out=%d frame_count=%d at %.6gfps "
            "(source %.6gs..%.6gs, out point treated as EXCLUSIVE)",
            shot["name"],
            shot["in"],
            shot["out"],
            shot["out"] - shot["in"],
            shot["source_fps"],
            resolved.start_seconds,
            resolved.end_seconds,
        )
        if resolved.notes:
            # §11: every defaulted/clamped value is stated out loud. This node
            # has no UI summary output (its segment list IS the payload), so the
            # server log is where they land.
            logger.info("cprb clip_source: %s", "; ".join(resolved.notes))
        return [shot], str(media), resolved.start_seconds, resolved.end_seconds, video
