"""The ``PremiereSaveTimeline`` ComfyUI node (PROTOCOL.md §3).

Assembles whatever combination of connected ``video_N`` VIDEO inputs
(unbounded, PROTOCOL.md §3.1) and ``paths`` lines the user connected/typed
into an ordered list of :class:`~cprb.timeline_write.ClipSpec`, probes each
one, and hands them to :mod:`cprb.timeline_write` (the pure serializer this
module never duplicates any formatting logic from) to write ``.xml``
(always), ``.edl``/``.otio`` (per widget) into
``context.timeline_dir(sequence_name)`` (PROTOCOL.md §2). ``video_N`` inputs
are always materialized into ``media/``; ``paths`` entries are either
referenced in place or copied into ``media/``, per the ``media`` widget
(§3.2 Link vs Collect).

Like :mod:`cprb.routes`, this module is configured once via
:func:`set_context` from the pack's ``__init__.py`` rather than importing
ComfyUI itself. Unlike ``av``-dependent :mod:`cprb.probe`, THIS module's own
imports stay av-free -- :func:`PremiereSaveTimeline.execute` imports
:mod:`cprb.probe` lazily, at call time, so simply importing
``cprb.nodes_save`` (e.g. to read ``RATE_TABLE`` for the ``fps`` widget's
choices) never requires PyAV to be installed.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from . import timeline_write
from .context import BridgeContext, output_dir_override_is_rejected, sanitize_name

logger = logging.getLogger("cprb")

#: PROTOCOL.md §3.1: an UNBOUNDED number of optional ``video_N`` VIDEO
#: sockets, matched by socket number rather than a hardcoded range. Shared by
#: :class:`_FlexibleOptionalVideoInputs` (INPUT_TYPES validation) and
#: :func:`_video_kwargs` (execute-time collection) so both agree on what
#: counts as a video slot.
_VIDEO_INPUT_PATTERN = re.compile(r"video_\d+")

#: §3.2's fps COMBO, in RATE_TABLE's own order -- the widget and the writer
#: share one source of truth so they can never drift out of sync.
FPS_CHOICES: list[str] = list(timeline_write.RATE_TABLE)
DEFAULT_FPS = "24"
DEFAULT_SEQUENCE_NAME = "ComfyUI Timeline"

#: §3.2's ``media`` COMBO: "Link in place" references each ``paths`` entry at
#: its original absolute path (zero copy); "Collect into folder" byte-copies
#: it into ``media/`` and references the copy instead. ``video_N`` inputs are
#: ALWAYS materialized into ``media/`` regardless of this widget -- a
#: generated VIDEO has no source file to link -- so ``media`` only ever
#: changes what happens to ``paths`` entries.
MEDIA_LINK = "Link in place"
MEDIA_COLLECT = "Collect into folder"
MEDIA_CHOICES = [MEDIA_LINK, MEDIA_COLLECT]
DEFAULT_MEDIA = MEDIA_LINK

_context: BridgeContext | None = None


class _FlexibleOptionalVideoInputs(dict):
    """The ``optional`` half of INPUT_TYPES: accepts ANY ``video_N`` key.

    PROTOCOL.md §3.1's unbounded ``video_N`` needs ComfyUI's own input
    validation -- which checks ``input_name in class_inputs['optional']``
    (the ``in`` operator, i.e. ``__contains__``) before letting a workflow
    wire a given input on this node -- to say yes to ``video_5``,
    ``video_37``, etc. even though only ``video_1`` is ever actually stored
    in this dict. Modeled on rgthree-comfy's ``FlexibleOptionalInputType``
    trick (reimplemented locally here -- this pack does not depend on
    rgthree): override ``__contains__`` (and, for safety, ``__getitem__`` in
    case something subscripts rather than uses ``in``/``.get``) to treat any
    key matching :data:`_VIDEO_INPUT_PATTERN` as present with type
    ``("VIDEO",)``. Plain dict iteration/``.items()``/``.keys()`` is left
    untouched, so it still only yields whatever was actually inserted
    (``video_1``) -- which is what the ``/object_info`` endpoint (and thus
    the frontend's default socket rendering) sees, giving the node exactly
    one visible socket out of the box.
    """

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str) and _VIDEO_INPUT_PATTERN.fullmatch(key):
            return True
        return super().__contains__(key)

    def __getitem__(self, key: str) -> Any:
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str) and _VIDEO_INPUT_PATTERN.fullmatch(key):
            return ("VIDEO",)
        raise KeyError(key)


def set_context(context: BridgeContext) -> None:
    """Wire the running :class:`BridgeContext` in (called once from ``__init__.py``)."""
    global _context
    _context = context


def _materialize_video(video_dir: Path, index: int, video: Any) -> Path:
    """Save *video* (the ``video_{index}`` input) to ``video_dir`` as an mp4.

    PROTOCOL.md §3.3: VIDEO inputs are materialized via the object's OWN
    ``save_to`` -- duck-typed and feature-detected (never an
    ``isinstance`` check against a specific ComfyUI class) so any VIDEO
    implementation with the right shape works, matching this pack's
    existing-nodes-first ethos. An object missing a callable ``save_to``
    fails with a clear error naming the offending input.

    Args:
        video_dir: The timeline's ``media/`` directory (created if missing).
        index: The input's 1-based socket number (``video_{index}``) -- used
            in the destination filename so a written file is traceable back
            to the socket that produced it.
        video: Whatever object ComfyUI connected to ``video_{index}``.

    Returns:
        The absolute path written.

    Raises:
        TypeError: *video* has no callable ``save_to`` method.
    """
    save_to = getattr(video, "save_to", None)
    if not callable(save_to):
        raise TypeError(
            f"PremiereSaveTimeline: video_{index} does not support save_to(...) "
            f"(got a {type(video).__name__!r} object, not a ComfyUI VIDEO)"
        )
    video_dir.mkdir(parents=True, exist_ok=True)
    dest = video_dir / f"{index:03d}_{sanitize_name(f'video_{index}')}.mp4"
    save_to(str(dest))
    return dest


def _video_kwargs(kwargs: dict[str, Any]) -> list[tuple[int, Any]]:
    """``(index, video)`` for every present, non-``None`` ``video_N`` kwarg.

    Sorted by ``N`` ascending (PROTOCOL.md §3.1), independent of *kwargs*'
    own iteration order. Replaces the old ``range(1, MAX_VIDEO_INPUTS + 1)``
    walk now that ``N`` is unbounded (§3.1's "grow like image nodes"): an
    unconnected slot is simply a key that's absent or ``None`` from *kwargs*
    (ComfyUI omits unconnected optional sockets on some call paths and passes
    ``None`` on others; both mean "not connected", matching the old fixed-
    range behavior's own ``if video is None: continue``).
    """
    indexed: list[tuple[int, Any]] = []
    for key, value in kwargs.items():
        if value is None:
            continue
        match = _VIDEO_INPUT_PATTERN.fullmatch(key)
        if match:
            indexed.append((int(key[len("video_") :]), value))
    indexed.sort(key=lambda pair: pair[0])
    return indexed


def _clip_from_probe(name: str, path: str, info: Any) -> timeline_write.ClipSpec:
    return timeline_write.ClipSpec(
        name=name,
        path=path,
        frames=info.frames,
        fps=info.fps,
        width=info.width,
        height=info.height,
    )


def _iter_path_lines(paths: str) -> list[tuple[int, str]]:
    """``(line_number, path)`` for every non-blank, non-comment line of *paths*.

    PROTOCOL.md §3.1: "blank lines and ``#``-prefixed lines ignored". Line
    numbers are the RAW 1-based position in *paths* (blank/comment lines
    still count towards the number), matching what a user editing the
    multiline widget actually sees -- so an error naming "line 4" points at
    the 4th line of the widget's text, not the 4th non-blank line.
    """
    result = []
    for line_no, raw_line in enumerate(paths.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#"):
            result.append((line_no, stripped))
    return result


def _collect_media_path(media_dir: Path, number: int, source: Path) -> Path:
    """Destination for a COLLECTED ``paths`` entry (§3.2 ``media`` = Collect).

    Named like :func:`_materialize_video`'s own convention -- zero-padded
    ``NNN_<sanitized stem>`` -- but keeps *source*'s own extension (§3.3:
    "preserve extension") since collecting is a byte copy
    (:func:`shutil.copy2`, in :meth:`PremiereSaveTimeline.execute`), never a
    re-encode. ``number`` is the ``paths`` widget's own 1-based line number
    (the same number a probe failure on this line would cite as "paths line
    N"), so a collected file's name is traceable back to the widget line
    that produced it.
    """
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir / f"{number:03d}_{sanitize_name(source.stem)}{source.suffix}"


class PremiereSaveTimeline:
    """Writes a Premiere-importable timeline from ComfyUI media (PROTOCOL.md §3).

    Inputs are an UNBOUNDED number of ``video_N`` (all optional VIDEO
    sockets, §3.1 -- ``video_1`` is the only one INPUT_TYPES declares by
    name; any other ``video_N`` is accepted via
    :class:`_FlexibleOptionalVideoInputs`) and a ``paths`` multiline STRING
    widget (one absolute path per line); the final clip order is ``video_N``
    ascending first, then ``paths`` lines top to bottom (§3.1), laid back-
    to-back from ``00:00:00:00`` on video track 1. VIDEO inputs are ALWAYS
    materialized into ``media/`` via their own ``save_to`` (§3.3); ``paths``
    entries are either referenced in place or copied into ``media/``, per
    the ``media`` widget (§3.2: ``"Link in place"`` vs ``"Collect into
    folder"``). Every clip is then probed (:mod:`cprb.probe`) for its real
    frame count/fps/resolution, and the assembled
    :class:`~cprb.timeline_write.ClipSpec` list is handed to
    :mod:`cprb.timeline_write` to write ``<sequence_name>.xml`` (always;
    PROTOCOL.md §4), plus ``.edl``/``.otio`` when ``write_edl``/
    ``write_otio`` are set (§5 / soft-dependency OTIO).

    ``output_dir`` (optional STRING, default ``""``, PROTOCOL.md §3.2, owner
    ask 2026-07-20): empty keeps writing under the §2 default
    (``<comfy output>/premiere_timelines/<sanitized sequence_name>/``); a
    non-empty, ABSOLUTE value replaces that base directly
    (``<output_dir>/<sanitized sequence_name>/`` -- no ``premiere_timelines``
    middle folder, and never straight into ``output_dir``'s own root). A
    non-empty value that ISN'T absolute is rejected CLEANLY: a warning (both
    the server log and this run's own summary text) and the default base is
    used instead -- never a hard failure over a hand-typed path mistake. See
    :func:`cprb.context.BridgeContext.resolve_timeline_dir`, which the
    ``GET /cprb/timeline_dir`` route also calls with the SAME *output_dir* so
    the frontend's "Open folder" button resolves the identical effective
    path this method will actually write to.

    ``OUTPUT_NODE = True``: this node exists for its filesystem side effects.
    Returns ``timeline_path`` (the written ``.xml``'s absolute path) and a UI
    text summary listing every file written, how many ``paths`` entries were
    linked vs collected, plus any warnings (currently: "otio skipped (not
    installed)" when ``write_otio`` is set but ``opentimelineio`` isn't, and
    a rejected ``output_dir`` per above).

    Raises (never silently drops a clip or a file -- a bad ``output_dir`` is
    the one widget mistake this node forgives rather than raises on, per
    above):

    * :class:`cprb.probe.ProbeError` -- a materialized ``video_N`` or a
      ``paths`` line couldn't be probed; the message names the input
      (``"video_2: ..."``) or the ``paths`` line number (``"paths line 4:
      ..."``).
    * ``TypeError`` -- a connected ``video_N`` has no ``save_to`` (not a
      real VIDEO object).
    * ``ValueError`` -- no clips at all (nothing connected AND ``paths`` is
      empty/all-comments), or ``media`` isn't one of :data:`MEDIA_CHOICES`.

    Re-running with the same ``sequence_name`` overwrites this timeline's
    files in place (PROTOCOL.md §2): every path this node writes to is a
    deterministic function of ``sequence_name`` and each clip's own
    position/socket index (a COLLECTED ``paths`` entry's filename comes from
    its widget LINE number instead -- same "deterministic per input"
    principle), never a freshly-allocated "next free slot", so a second run
    with identical inputs reproduces byte-identical files and a second run
    with DIFFERENT inputs simply overwrites them with the new content --
    exactly the re-import-painless behavior §2 calls for.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("timeline_path",)
    FUNCTION = "execute"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        optional = _FlexibleOptionalVideoInputs(
            {
                "video_1": ("VIDEO",),
                # PROTOCOL.md §3.2 (owner ask 2026-07-20, parity with
                # PremiereLoadTimeline's file bar): an OPTIONAL absolute-path
                # override for the §2 output base. Lives in `optional` (not
                # `required`, unlike this node's other widgets) so an
                # API-format prompt written before this widget existed --
                # or any other caller that never mentions it -- keeps
                # working with no "required input missing" error; the
                # `output_dir=""` default in `execute()` below is the SAME
                # fallback either way. `web/cprb/nodes.js`'s Browse… writes
                # a chosen folder here through the widget's real setter,
                # exactly like Load's Browse… does for `file_path`.
                "output_dir": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "Optional: an absolute folder to write this timeline under, "
                            "instead of <ComfyUI output>/premiere_timelines/ (still gets its "
                            "own <sequence_name> subfolder either way). Leave empty for the "
                            "default."
                        ),
                    },
                ),
            }
        )
        return {
            "required": {
                "sequence_name": ("STRING", {"default": DEFAULT_SEQUENCE_NAME}),
                "fps": (FPS_CHOICES, {"default": DEFAULT_FPS}),
                "media": (MEDIA_CHOICES, {"default": DEFAULT_MEDIA}),
                "paths": ("STRING", {"default": "", "multiline": True, "forceInput": False}),
                "write_edl": ("BOOLEAN", {"default": False}),
                "write_otio": ("BOOLEAN", {"default": False}),
            },
            "optional": optional,
        }

    def execute(
        self,
        sequence_name: str,
        fps: str,
        paths: str,
        write_edl: bool,
        write_otio: bool,
        media: str = DEFAULT_MEDIA,
        output_dir: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if _context is None:
            raise RuntimeError(
                "PremiereSaveTimeline: no BridgeContext configured (set_context was never called)"
            )
        if media not in MEDIA_CHOICES:
            raise ValueError(
                f"PremiereSaveTimeline: unknown media {media!r} (expected one of {MEDIA_CHOICES})"
            )
        # Lazy: keeps this module importable (e.g. for FPS_CHOICES) without PyAV.
        from .probe import ProbeError, probe_media

        clips: list[timeline_write.ClipSpec] = []
        written: list[str] = []
        warnings: list[str] = []

        # PROTOCOL.md §3.2: a non-empty `output_dir` that ISN'T absolute is
        # rejected CLEANLY -- never a hard failure over what's very likely a
        # hand-typed typo in an otherwise-fine run -- a server-log warning
        # plus a line in this run's own UI summary (same visibility the
        # `write_otio`-missing soft-dependency warning below already gets),
        # then `BridgeContext.timeline_dir` independently falls back to the
        # default base on its own (it re-checks validity itself; this block
        # exists ONLY to be loud about that fallback, not to compute it).
        if output_dir_override_is_rejected(output_dir):
            logger.warning(
                "cprb save_timeline: output_dir %r is not an absolute path; "
                "using the default output folder instead",
                output_dir,
            )
            warnings.append(
                f"output_dir {output_dir!r} is not an absolute path -- wrote to the default "
                "output folder instead"
            )

        out_dir = _context.timeline_dir(sequence_name, output_dir)
        media_dir = out_dir / "media"

        for index, video in _video_kwargs(kwargs):
            dest = _materialize_video(media_dir, index, video)
            written.append(str(dest))
            try:
                info = probe_media(str(dest))
            except ProbeError as exc:
                raise ProbeError(f"video_{index}: {exc}") from exc
            clips.append(_clip_from_probe(dest.stem, str(dest), info))

        # §3.2: paths entries are probed at their ORIGINAL location either
        # way -- Collect copies the already-probed bytes, it never re-probes
        # the copy.
        linked_count = 0
        collected_count = 0
        path_lines = _iter_path_lines(paths)
        for line_no, path in path_lines:
            try:
                info = probe_media(path)
            except ProbeError as exc:
                raise ProbeError(f"paths line {line_no}: {exc}") from exc

            if media == MEDIA_COLLECT:
                dest = _collect_media_path(media_dir, line_no, Path(path))
                shutil.copy2(path, dest)
                written.append(str(dest))
                collected_count += 1
                clips.append(_clip_from_probe(dest.stem, str(dest), info))
            else:
                linked_count += 1
                clips.append(_clip_from_probe(Path(path).stem, path, info))

        if not clips:
            raise ValueError(
                "PremiereSaveTimeline: no clips to write -- connect a video_N input or add "
                "at least one path to the paths widget"
            )

        xml_path = out_dir / f"{sanitize_name(sequence_name)}.xml"
        xml_path.write_text(timeline_write.build_xmeml(sequence_name, fps, clips), encoding="utf-8")
        written.append(str(xml_path))

        if write_edl:
            edl_path = out_dir / f"{sanitize_name(sequence_name)}.edl"
            edl_text = timeline_write.build_edl(sequence_name, fps, clips)
            edl_path.write_text(edl_text, encoding="utf-8")
            written.append(str(edl_path))

        if write_otio:
            try:
                otio_text = timeline_write.build_otio(sequence_name, fps, clips)
            except ImportError:
                warnings.append("otio skipped (not installed)")
                logger.warning(
                    "cprb save_timeline: write_otio was set but opentimelineio is not installed; "
                    "skipping .otio output"
                )
            else:
                otio_path = out_dir / f"{sanitize_name(sequence_name)}.otio"
                otio_path.write_text(otio_text, encoding="utf-8")
                written.append(str(otio_path))

        summary = [f"Wrote {len(written)} file(s):"]
        summary.extend(f"  {path}" for path in written)
        if path_lines:
            summary.append(
                f"Media: {linked_count} file(s) linked in place, "
                f"{collected_count} file(s) collected into media/"
            )
        if warnings:
            summary.append("Warnings:")
            summary.extend(f"  {warning}" for warning in warnings)

        logger.info("cprb save_timeline: %s", "; ".join(summary))
        return {"ui": {"text": summary}, "result": (str(xml_path),)}
