"""``PremiereLoadTimeline`` + ``PremiereGetShot`` ComfyUI nodes (PROTOCOL.md §6).

No ComfyUI imports anywhere in this module: ``file_path`` is read as a plain
absolute path (PROTOCOL.md §6.1) and ``CPRB_SHOT_LIST`` (§6.2) is a plain
``list[dict]`` value, so nothing here needs ``folder_paths``/``server``/
tensors/etc. Parsing itself lives in :mod:`cprb.timeline_read`; this module
is only the ComfyUI-shaped wrapper around it (widgets, ``IS_CHANGED``,
``VALIDATE_INPUTS``, the human-readable ``summary`` string) -- same module
split as every other cprb/cpsb/lora_library feature.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .timeline_read import ParsedTimeline, parse_timeline

if TYPE_CHECKING:
    from .context import BridgeContext

_context: BridgeContext | None = None


def set_context(context: BridgeContext | None) -> None:
    """Wire the shared :class:`~cprb.context.BridgeContext` into this module.

    Called once from the top-level ``__init__.py`` (real runs); tests may
    call it directly, or skip it entirely -- neither node below actually
    reads *context* today (``file_path`` is a literal absolute path,
    PROTOCOL.md §6.1, and ``CPRB_SHOT_LIST`` is a pure in-memory value), but
    the top-level loader calls ``set_context`` on every configured feature
    module unconditionally (mirroring ``lora_library.nodes_sets.set_context``
    and ``cpsb.nodes.configure``), so this must exist regardless. Accepts
    ``None`` so tests can reset the module-level global between cases
    without leaking state.
    """
    global _context
    _context = context


def _fmt_fps(fps: float) -> str:
    """*fps* as a short, familiar string: ``24``, ``29.97``, ``23.976``."""
    text = f"{fps:.3f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _summary_line(index: int, shot: dict[str, Any]) -> str:
    """One ``summary`` line (PROTOCOL.md §6.1): ``[i] name | path | in-out @fps``.

    Appends a visible marker for each of the two conditions §6.1 asks to
    flag on the shot rather than fail the whole parse over: a disabled clip
    that made it into the list anyway (``skip_disabled=False``), and a
    ``start``/``end`` that :mod:`cprb.timeline_read` could not recover from
    its ``-1`` placeholder (kept as literal ``-1`` there -- never a valid
    frame number, so it doubles as its own flag; see
    ``timeline_read._resolve_track_spans``). Neither marker touches the
    FROZEN shot dict itself (PROTOCOL.md §6.2) -- both are computed fresh
    here from values already in it.
    """
    marker = ""
    if not shot["enabled"]:
        marker += " [DISABLED]"
    if shot["start"] == -1 or shot["end"] == -1:
        marker += " [TIMELINE POSITION UNRESOLVED]"
    fps = _fmt_fps(shot["source_fps"])
    body = f"[{index}] {shot['name']} | {shot['path']} | {shot['in']}-{shot['out']} @{fps}fps"
    return body + marker


class PremiereLoadTimeline:
    """Reads a Premiere-exported Final Cut Pro XML into a shot list (PROTOCOL.md §6.1).

    ``file_path`` is the absolute path of the exported ``.xml`` (Premiere:
    File > Export > Final Cut Pro XML). Parsing itself is
    :func:`cprb.timeline_read.parse_timeline`; this class adds only the
    ComfyUI-facing shape: the ``skip_disabled`` filter, the ``count``/
    ``summary`` conveniences, and cache-busting via :meth:`IS_CHANGED` so a
    re-export (same filename, new content) actually re-runs.

    ``skip_disabled`` (default ``True``) drops clips Premiere marked
    disabled from BOTH ``shots`` and ``count``; when ``False`` they're kept,
    with a ``[DISABLED]`` marker on their ``summary`` line
    (:func:`_summary_line`). Either way, every ``summary`` line's leading
    ``[i]`` matches exactly the ``index`` :class:`PremiereGetShot` needs to
    pull that same shot back out of THIS node's ``shots`` output -- the
    index is assigned after filtering, not before.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("CPRB_SHOT_LIST", "INT", "STRING")
    RETURN_NAMES = ("shots", "count", "summary")
    FUNCTION = "execute"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "file_path": ("STRING", {"default": ""}),
                "skip_disabled": ("BOOLEAN", {"default": True}),
            }
        }

    @classmethod
    def VALIDATE_INPUTS(cls, file_path: str) -> bool | str:
        """Friendly upfront check, mirroring ``PhotoshopLoadPSD.VALIDATE_INPUTS``.

        Confirms *file_path* is non-empty and points at an existing file
        before the prompt is even queued, rather than surfacing a raw
        ``FileNotFoundError`` mid-run. Deliberately does NOT parse the XML
        here too -- that would parse every file twice per queue for no
        benefit most workflows would ever notice. A parse failure (malformed
        XML, zero video clipitems) still surfaces as a clear error straight
        from :meth:`execute`, per PROTOCOL.md §6.1.
        """
        if not file_path or not file_path.strip():
            return "file_path is empty -- point it at a Premiere-exported .xml file"
        if not Path(file_path).is_file():
            return f"File not found: {file_path}"
        return True

    @classmethod
    def IS_CHANGED(cls, file_path: str, skip_disabled: bool = True) -> str:
        """mtime+size of *file_path* (PROTOCOL.md §6.1), so a re-export re-runs.

        *skip_disabled* is accepted -- ComfyUI passes every declared input
        here -- but not folded into the token: ComfyUI already re-runs this
        node whenever a literal widget value like ``skip_disabled`` itself
        changes, independently of ``IS_CHANGED``. This token only needs to
        cover the change ``IS_CHANGED`` exists FOR: disk content changing
        under an unchanged ``file_path`` string (e.g. a fresh export
        overwriting the same filename).
        """
        try:
            stat = Path(file_path).stat()
        except OSError:
            return "missing"
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def execute(
        self, file_path: str, skip_disabled: bool = True
    ) -> tuple[list[dict[str, Any]], int, str]:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Premiere timeline file not found: {file_path}")

        text = path.read_text(encoding="utf-8", errors="replace")
        parsed: ParsedTimeline = parse_timeline(text)

        included = [shot for shot in parsed.shots if shot["enabled"] or not skip_disabled]
        summary = "\n".join(_summary_line(i, shot) for i, shot in enumerate(included))
        return included, len(included), summary


class PremiereGetShot:
    """Pulls one shot's path/timing out of a ``CPRB_SHOT_LIST`` by index (PROTOCOL.md §6.3).

    ``in_frame``/``frame_count`` feed VHS's ``Load Video (Path)``
    (``skip_first_frames``/``frame_load_cap``) directly; ``in_seconds``/
    ``duration_seconds`` suit core loaders that work in time rather than
    frames. Both pairs are derived from the same source frame numbers at the
    shot's own ``source_fps`` -- never ``sequence_fps``, since a shot's
    ``in``/``out`` are positions inside its SOURCE file, not the timeline.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("STRING", "FLOAT", "FLOAT", "INT", "INT", "FLOAT", "STRING")
    RETURN_NAMES = (
        "path",
        "in_seconds",
        "duration_seconds",
        "in_frame",
        "frame_count",
        "fps",
        "name",
    )
    FUNCTION = "execute"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "shots": ("CPRB_SHOT_LIST",),
                "index": ("INT", {"default": 0, "min": 0}),
            }
        }

    def execute(
        self, shots: list[dict[str, Any]], index: int
    ) -> tuple[str, float, float, int, int, float, str]:
        if not shots:
            raise ValueError("Get Shot: the shot list is empty -- nothing to index into")
        if not (0 <= index < len(shots)):
            raise ValueError(
                f"Get Shot: index {index} out of range -- valid range is 0..{len(shots) - 1}"
            )

        shot = shots[index]
        fps = float(shot["source_fps"])
        in_frame = int(shot["in"])
        frame_count = int(shot["out"]) - in_frame
        in_seconds = in_frame / fps if fps else 0.0
        duration_seconds = frame_count / fps if fps else 0.0
        return (
            shot["path"],
            in_seconds,
            duration_seconds,
            in_frame,
            frame_count,
            fps,
            shot["name"],
        )
