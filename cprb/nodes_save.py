"""The ``PremiereSaveTimeline`` ComfyUI node (PROTOCOL.md §3).

Assembles whatever combination of ``video_1..video_4`` VIDEO inputs and
``paths`` lines the user connected/typed into an ordered list of
:class:`~cprb.timeline_write.ClipSpec`, probes each one, and hands them to
:mod:`cprb.timeline_write` (the pure serializer this module never
duplicates any formatting logic from) to write ``.xml`` (always),
``.edl``/``.otio`` (per widget) into ``context.timeline_dir(sequence_name)``
(PROTOCOL.md §2).

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
from pathlib import Path
from typing import Any

from . import timeline_write
from .context import BridgeContext, sanitize_name

logger = logging.getLogger("cprb")

#: PROTOCOL.md §3.1: exactly four optional VIDEO sockets, ``video_1..video_4``.
MAX_VIDEO_INPUTS = 4

#: §3.2's fps COMBO, in RATE_TABLE's own order -- the widget and the writer
#: share one source of truth so they can never drift out of sync.
FPS_CHOICES: list[str] = list(timeline_write.RATE_TABLE)
DEFAULT_FPS = "24"
DEFAULT_SEQUENCE_NAME = "ComfyUI Timeline"

_context: BridgeContext | None = None


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


class PremiereSaveTimeline:
    """Writes a Premiere-importable timeline from ComfyUI media (PROTOCOL.md §3).

    Inputs are ``video_1..video_4`` (all optional VIDEO sockets) and a
    ``paths`` multiline STRING widget (one absolute path per line); the
    final clip order is ``video_1..4`` first, then ``paths`` lines top to
    bottom (§3.1), laid back-to-back from ``00:00:00:00`` on video track 1.
    VIDEO inputs are materialized into ``media/`` via their own ``save_to``
    (§3.3); ``paths`` entries are referenced in place, never copied. Every
    clip is then probed (:mod:`cprb.probe`) for its real frame
    count/fps/resolution, and the assembled
    :class:`~cprb.timeline_write.ClipSpec` list is handed to
    :mod:`cprb.timeline_write` to write ``<sequence_name>.xml`` (always;
    PROTOCOL.md §4), plus ``.edl``/``.otio`` when ``write_edl``/
    ``write_otio`` are set (§5 / soft-dependency OTIO).

    ``OUTPUT_NODE = True``: this node exists for its filesystem side effects.
    Returns ``timeline_path`` (the written ``.xml``'s absolute path) and a UI
    text summary listing every file written plus any warnings (currently
    only "otio skipped (not installed)", when ``write_otio`` is set but
    ``opentimelineio`` isn't).

    Raises (never silently drops a clip or a file):

    * :class:`cprb.probe.ProbeError` -- a materialized ``video_N`` or a
      ``paths`` line couldn't be probed; the message names the input
      (``"video_2: ..."``) or the ``paths`` line number (``"paths line 4:
      ..."``).
    * ``TypeError`` -- a connected ``video_N`` has no ``save_to`` (not a
      real VIDEO object).
    * ``ValueError`` -- no clips at all (nothing connected AND ``paths`` is
      empty/all-comments) -- there is nothing to write a timeline from.

    Re-running with the same ``sequence_name`` overwrites this timeline's
    files in place (PROTOCOL.md §2): every path this node writes to is a
    deterministic function of ``sequence_name`` and each clip's own
    position/socket index, never a freshly-allocated "next free slot", so a
    second run with identical inputs reproduces byte-identical files and a
    second run with DIFFERENT inputs simply overwrites them with the new
    content -- exactly the re-import-painless behavior §2 calls for.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("timeline_path",)
    FUNCTION = "execute"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        optional = {f"video_{i}": ("VIDEO",) for i in range(1, MAX_VIDEO_INPUTS + 1)}
        return {
            "required": {
                "sequence_name": ("STRING", {"default": DEFAULT_SEQUENCE_NAME}),
                "fps": (FPS_CHOICES, {"default": DEFAULT_FPS}),
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
        **kwargs: Any,
    ) -> dict[str, Any]:
        if _context is None:
            raise RuntimeError(
                "PremiereSaveTimeline: no BridgeContext configured (set_context was never called)"
            )
        # Lazy: keeps this module importable (e.g. for FPS_CHOICES) without PyAV.
        from .probe import ProbeError, probe_media

        out_dir = _context.timeline_dir(sequence_name)
        media_dir = out_dir / "media"

        clips: list[timeline_write.ClipSpec] = []
        written: list[str] = []
        warnings: list[str] = []

        for index in range(1, MAX_VIDEO_INPUTS + 1):
            video = kwargs.get(f"video_{index}")
            if video is None:
                continue
            dest = _materialize_video(media_dir, index, video)
            written.append(str(dest))
            try:
                info = probe_media(str(dest))
            except ProbeError as exc:
                raise ProbeError(f"video_{index}: {exc}") from exc
            clips.append(_clip_from_probe(dest.stem, str(dest), info))

        for line_no, path in _iter_path_lines(paths):
            try:
                info = probe_media(path)
            except ProbeError as exc:
                raise ProbeError(f"paths line {line_no}: {exc}") from exc
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
        if warnings:
            summary.append("Warnings:")
            summary.extend(f"  {warning}" for warning in warnings)

        logger.info("cprb save_timeline: %s", "; ".join(summary))
        return {"ui": {"text": summary}, "result": (str(xml_path),)}
