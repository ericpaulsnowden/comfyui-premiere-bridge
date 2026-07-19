"""``PremiereLoadTimeline`` + ``PremiereGetShot`` + ``PremiereIterateShots`` +
``PremiereShotFrame`` ComfyUI nodes (PROTOCOL.md §6).

No ComfyUI imports anywhere in this module: ``file_path`` is read as a plain
absolute path (PROTOCOL.md §6.1) and ``CPRB_SHOT_LIST`` (§6.2) is a plain
``list[dict]`` value, so nothing here needs ``folder_paths``/``server``/etc.
Parsing itself lives in :mod:`cprb.timeline_read`; this module is only the
ComfyUI-shaped wrapper around it (widgets, ``IS_CHANGED``, ``VALIDATE_INPUTS``,
the human-readable ``summary`` string) -- same module split as every other
cprb/cpsb/lora_library feature. ``PremiereShotFrame`` (§6.5) is the one node
here that produces a tensor, but even it imports nothing heavy AT THIS
SCOPE: the actual ``av``/``torch`` decode lives in, and is lazily imported
by, :mod:`cprb.frame_extract`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .frame_extract import extract_frame
from .timeline_read import ParsedTimeline, parse_timeline

if TYPE_CHECKING:
    from .context import BridgeContext

_context: BridgeContext | None = None

#: One message for the one wrong file everyone will try first (PROTOCOL.md
#: §6.1 reads Premiere's EXPORT, never its project file).
_PRPROJ_MESSAGE = (
    "This is a Premiere project file (.prproj) -- Premiere's internal format, "
    "which this node can't read. In Premiere, open your sequence and use "
    "File > Export > Final Cut Pro XML, then point this node at the exported "
    ".xml file."
)


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
        if file_path.strip().lower().endswith(".prproj"):
            # The single most natural wrong file (the owner reached for it
            # first): Premiere's own project file, a gzipped internal format
            # this node cannot read. Say what to do instead of "bad XML".
            return _PRPROJ_MESSAGE
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

        raw = path.read_bytes()
        # .prproj (or any gzip payload, magic 1f 8b) is Premiere's internal
        # project format, not the FCP7 XML export this node reads. Catch it
        # by extension AND by content so a renamed project file still gets
        # the helpful message instead of "not well-formed XML" (owner hit
        # exactly this, 2026-07-18).
        if path.suffix.lower() == ".prproj" or raw[:2] == b"\x1f\x8b":
            raise ValueError(_PRPROJ_MESSAGE)

        text = raw.decode("utf-8", errors="replace")
        parsed: ParsedTimeline = parse_timeline(text)

        included = [shot for shot in parsed.shots if shot["enabled"] or not skip_disabled]
        summary = "\n".join(_summary_line(i, shot) for i, shot in enumerate(included))
        return included, len(included), summary


def _in_seconds(shot: dict[str, Any]) -> float:
    """*shot*'s ``in`` frame converted to seconds at its own ``source_fps``.

    Shared by :class:`PremiereGetShot`/:class:`PremiereIterateShots` (their
    ``in_seconds`` output) and :class:`PremiereShotFrame` (its PyAV seek
    target, via :func:`cprb.frame_extract.extract_frame`) so all three
    compute the identical number from the identical rule: a shot's
    ``in``/``out`` are positions inside its SOURCE file, always counted at
    ``source_fps`` -- never ``sequence_fps`` (PROTOCOL.md §6.3).
    """
    fps = float(shot["source_fps"])
    return int(shot["in"]) / fps if fps else 0.0


def _get_shot_fields(
    shot: dict[str, Any],
) -> tuple[str, float, float, int, int, float, str, int, int]:
    """One shot's ``Get Shot``-shaped 9-tuple (PROTOCOL.md §6.3).

    ``(path, duration_seconds, in_seconds, frame_count, in_frame, fps, name,
    width, height)`` -- shared VERBATIM by :class:`PremiereGetShot` (one
    shot) and :class:`PremiereIterateShots` (every shot, column-wise) so the
    two can never drift apart on either the math or the output order.
    """
    fps = float(shot["source_fps"])
    in_frame = int(shot["in"])
    frame_count = int(shot["out"]) - in_frame
    in_seconds = _in_seconds(shot)
    duration_seconds = frame_count / fps if fps else 0.0
    return (
        shot["path"],
        duration_seconds,
        in_seconds,
        frame_count,
        in_frame,
        fps,
        shot["name"],
        int(shot["width"]),
        int(shot["height"]),
    )


class PremiereGetShot:
    """Pulls one shot's path/timing out of a ``CPRB_SHOT_LIST`` by index (PROTOCOL.md §6.3).

    ``in_frame``/``frame_count`` feed VHS's ``Load Video (Path)``
    (``skip_first_frames``/``frame_load_cap``) directly; ``in_seconds``/
    ``duration_seconds`` suit core loaders that work in time rather than
    frames; ``width``/``height`` feed a resize/crop node or a Create Video
    node. All are derived from the same source frame numbers at the shot's
    own ``source_fps`` -- never ``sequence_fps``, since a shot's ``in``/
    ``out`` are positions inside its SOURCE file, not the timeline.

    Output order (PROTOCOL.md §6.3, owner reorder 2026-07-19): ``path,
    duration_seconds, in_seconds, frame_count, in_frame, fps, name, width,
    height`` -- the "seconds" pair and the "frame" pair each lead with the
    value that feeds VHS's load-cap widgets, matching how they're wired.
    ⚠ This reorders + extends a previously-shipped node's outputs: a
    workflow saved before this change re-wires its Get Shot connections BY
    POSITION on load (§6.3) -- re-check any existing Get Shot wiring once.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("STRING", "FLOAT", "FLOAT", "INT", "INT", "FLOAT", "STRING", "INT", "INT")
    RETURN_NAMES = (
        "path",
        "duration_seconds",
        "in_seconds",
        "frame_count",
        "in_frame",
        "fps",
        "name",
        "width",
        "height",
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
    ) -> tuple[str, float, float, int, int, float, str, int, int]:
        if not shots:
            raise ValueError("Get Shot: the shot list is empty -- nothing to index into")
        if not (0 <= index < len(shots)):
            raise ValueError(
                f"Get Shot: index {index} out of range -- valid range is 0..{len(shots) - 1}"
            )
        return _get_shot_fields(shots[index])


class PremiereIterateShots:
    """Fans a whole ``CPRB_SHOT_LIST`` out through ComfyUI's list execution (PROTOCOL.md §6.4).

    ComfyUI has no for-loop; list execution is its actual answer to "run
    this subgraph once per item" -- the same mechanism EPSNodes' notebook
    multi-select node relies on. This node's OWN ``execute`` still runs
    exactly ONCE per queue: it returns nine PARALLEL PLAIN LISTS (one
    element per shot, in shot order) and declares ``OUTPUT_IS_LIST =
    (True,) * 9``. ComfyUI's graph executor is what turns that declaration
    into "one run per shot" for everything wired downstream -- not this
    class, which never loops over anything except *shots* itself.

    Confirmed against ComfyUI's own ``execution.py`` (checked in this rig at
    ``comfyui-env/ComfyUI/execution.py``):

    * ``merge_result_data()`` reads THIS node's ``OUTPUT_IS_LIST`` and, for
      every output flagged ``True``, ``list.extend()``s this call's
      returned list into the cached output -- so the cache ends up holding
      one genuine flat list per output (length == shot count), never a
      one-element list wrapping a sub-list.
    * ``_async_map_node_over_list()`` is what every DOWNSTREAM node's own
      execution goes through. A downstream node that does NOT itself
      declare ``INPUT_IS_LIST`` (the common case -- e.g. VHS's ``Load Video
      (Path)``) falls into that function's plain branch: ``max_len_input``
      becomes the length of the list-valued input(s) it just received, and
      ``for i in range(max_len_input): slice_dict(input_data_all, i)``
      calls that node's ``FUNCTION`` once per index -- one execution per
      shot, in order, fanned out from this one node's single run.

    Outputs mirror :class:`PremiereGetShot`'s set/order exactly (``path,
    duration_seconds, in_seconds, frame_count, in_frame, fps, name, width,
    height``); wire ``path`` + the frame outputs into VHS ``Load Video
    (Path)`` and one Run processes the whole edit shot by shot. An empty
    ``shots`` list is not an error: every output is simply an empty list, so
    nothing downstream runs (PROTOCOL.md §6.4). ``skip_disabled`` is
    deliberately NOT a widget here -- ``PremiereLoadTimeline`` already
    applied that filter before *shots* ever reaches this node.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = PremiereGetShot.RETURN_TYPES
    RETURN_NAMES = PremiereGetShot.RETURN_NAMES
    OUTPUT_IS_LIST = (True,) * len(RETURN_TYPES)
    FUNCTION = "execute"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {"required": {"shots": ("CPRB_SHOT_LIST",)}}

    def execute(self, shots: list[dict[str, Any]]) -> tuple[list[Any], ...]:
        """Nine parallel lists, one element per shot, in shot order.

        Column count comes from ``self.RETURN_TYPES`` (not a hardcoded
        ``9``) so a future §6.3 append-only extension to ``PremiereGetShot``
        can never silently desync the two nodes' arity.
        """
        columns: tuple[list[Any], ...] = tuple([] for _ in self.RETURN_TYPES)
        for shot in shots:
            for column, value in zip(columns, _get_shot_fields(shot), strict=True):
                column.append(value)
        return columns


class PremiereShotFrame:
    """Decodes one preview frame at a shot's in-point via PyAV (PROTOCOL.md §6.5).

    SEPARATE from :class:`PremiereGetShot`/:class:`PremiereIterateShots`
    specifically so the decode cost -- and its one real failure mode, media
    that's offline or that ffmpeg can't decode -- never touches their
    cheap, pure-dict-lookup path ("SEPARATE node so the decode cost/failure
    never touches Get Shot's cheap metadata path", §6.5). The actual decode
    is :func:`cprb.frame_extract.extract_frame`; nothing in THIS module
    imports ``av`` or ``torch`` -- see that module's own lazy-import
    docstring. No decode happens unless this node is actually in the graph.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "execute"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "shots": ("CPRB_SHOT_LIST",),
                "index": ("INT", {"default": 0, "min": 0}),
            }
        }

    def execute(self, shots: list[dict[str, Any]], index: int) -> tuple[Any]:
        if not shots:
            raise ValueError("Get Shot Frame: the shot list is empty -- nothing to index into")
        if not (0 <= index < len(shots)):
            raise ValueError(
                f"Get Shot Frame: index {index} out of range -- valid range is 0..{len(shots) - 1}"
            )

        shot = shots[index]
        image = extract_frame(shot["path"], _in_seconds(shot))
        return (image,)
