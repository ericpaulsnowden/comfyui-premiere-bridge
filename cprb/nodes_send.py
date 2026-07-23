"""The ``PremiereSendResult`` ComfyUI node (PROTOCOL.md §10.5).

M1's headline: a finished workflow result lands in a Premiere bin without
the user leaving Premiere. This node resolves each wired input to ONE
durable on-disk file -- linking a VIDEO's existing source file in place
whenever that file IS the input (multi-GB results must be instant, and
Premiere links media in place), otherwise writing into
``<comfy output>/premiere_results/`` under a collision-free name -- and
hands each path to :func:`cprb.routes.push_result` for the connected
plugin to import (§10.3 ``pr_result``). No plugin connected is NOT an
error: the file is still on disk, and the node's own summary says where to
import it from manually (§1's ethos: the plugin is a better version, never
the only version).

Like the other feature modules, this one is configured once via
:func:`set_context` from the pack's ``__init__.py`` and never imports
ComfyUI itself at module scope -- ``folder_paths`` (the temp-dir check) is
imported lazily inside :func:`_comfy_temp_dir`, and torch/numpy/PIL only
ever inside :func:`_tensor_to_pil`, so simply importing ``cprb.nodes_send``
requires none of them (pack convention; see ``nodes_save``'s identical
posture for PyAV).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import routes
from .context import BridgeContext, sanitize_name
from .nodes_save import materialize_video

logger = logging.getLogger("cprb")

#: PROTOCOL.md §10.5: everything this node WRITES (temp-copied videos,
#: in-memory videos, PNGs) lands under ``<comfy output>/premiere_results/``.
#: Sibling of §2's ``premiere_timelines`` tree, but with the OPPOSITE naming
#: rule: §2's deterministic overwrite-in-place makes re-IMPORT painless,
#: while every push here is a NEW import into Premiere -- overwriting a
#: previously-pushed file would silently change media already cut into his
#: project, so names are collision-free instead (:func:`_allocate_result_path`).
RESULTS_DIRNAME = "premiere_results"

DEFAULT_BIN_NAME = "ComfyUI Results"

#: §10.3/§10.5: the color_label COMBO's options. "None" (the default) means
#: "don't label" and is sent as "" on the wire (the plugin skips empty —
#: there IS no "None" member in Premiere's 15-color label enum, so mapping
#: it to an index would be wrong by construction). The color names mirror
#: the plugin's own name map (import_recipe.js CPRB_LABEL_COLOR_INDEX),
#: which itself prefers a live `Constants` enum match when one exists.
COLOR_LABEL_NONE = "None"
COLOR_LABEL_OPTIONS = [
    COLOR_LABEL_NONE,
    "violet",
    "iris",
    "caribbean",
    "lavender",
    "cerulean",
    "forest",
    "rose",
    "mango",
    "purple",
    "blue",
    "teal",
    "magenta",
    "tan",
    "green",
    "brown",
    "yellow",
]

_context: BridgeContext | None = None


def set_context(context: BridgeContext | None) -> None:
    """Wire the shared :class:`~cprb.context.BridgeContext` into this module.

    Called once from the top-level ``__init__.py`` (real runs); tests call it
    directly. Accepts ``None`` so tests can reset the module-level global
    between cases without leaking state (mirrors ``nodes_load.set_context``).
    """
    global _context
    _context = context


def _comfy_temp_dir() -> Path | None:
    """ComfyUI's temp directory (resolved), or ``None`` when unknowable.

    The §10.5 durability check: a VIDEO source path under this directory is
    COPIED before pushing (ComfyUI cleans temp up later; Premiere links
    media in place, so a linked temp file would go offline in his project).
    ``folder_paths`` is imported lazily -- only importable inside ComfyUI --
    and any failure means "no temp dir to compare against" rather than an
    error, so non-ComfyUI runs (tests, tooling) fall through to link-in-
    place. A module-level seam (like ``routes._is_windows``) precisely so
    tests monkeypatch it to exercise the copy branch without ComfyUI.
    """
    try:
        import folder_paths  # ComfyUI's module; only importable inside ComfyUI
    except ImportError:
        return None
    try:
        return Path(folder_paths.get_temp_directory()).resolve()
    except Exception:
        logger.warning("cprb send_result: could not resolve ComfyUI's temp directory")
        return None


def _video_source_path(video: Any) -> Path | None:
    """The existing on-disk file *video* streams from, or ``None``.

    Duck-typed over ComfyUI core's ``VideoInput.get_stream_source()`` (str =
    a file path, BytesIO = in-memory). ``None`` means "no verifiable source
    file" and sends the caller down the materialize branch -- never an
    error, since every VIDEO can still be written via ``save_to``.

    Guard: core's BASE-class ``get_stream_source`` default answers by
    ENCODING the whole video into an in-memory buffer (``comfy_api``
    ``video_types.py``) -- for a components-backed input that is exactly
    the multi-GB cost this probe exists to avoid, and the answer would be
    "no path" anyway. An object that merely inherits that default (its
    method's ``__qualname__`` still starts with ``VideoInput.``) is
    therefore answered ``None`` without the call; ``VideoFromFile`` and any
    real override have their own qualname and are consulted normally.
    """
    get_stream_source = getattr(video, "get_stream_source", None)
    if not callable(get_stream_source):
        return None
    if getattr(get_stream_source, "__qualname__", "").startswith("VideoInput."):
        return None
    try:
        source = get_stream_source()
    except Exception:
        logger.warning("cprb send_result: video.get_stream_source() failed", exc_info=True)
        return None
    if not isinstance(source, (str, os.PathLike)):
        return None  # BytesIO (or anything else): in-memory
    path = Path(source)
    if not path.is_file():
        return None
    return path.resolve()


def _video_is_trimmed(video: Any) -> bool:
    """Whether *video* carries an active trim window (so its source file is
    NOT the video the graph wired).

    ComfyUI core's ``VideoInput.get_active_trim_window()`` returns
    ``(start_time, duration)`` with ``(0.0, 0.0)`` meaning untrimmed; a
    ``VideoFromFile`` produced by ``as_trimmed(...)`` still reports its
    ORIGINAL file from ``get_stream_source()``, so linking that path as-is
    would silently import the whole untrimmed source into Premiere. A
    missing method reads as untrimmed (older cores had no trim mechanism);
    a FAILING one reads as trimmed -- when the question can't be answered,
    materializing via ``save_to`` is the branch that is correct either way.
    """
    get_window = getattr(video, "get_active_trim_window", None)
    if not callable(get_window):
        return False
    try:
        start_time, duration = get_window()
    except Exception:
        logger.warning(
            "cprb send_result: video.get_active_trim_window() failed -- "
            "writing a fresh copy instead of linking the source file",
            exc_info=True,
        )
        return True
    return bool(start_time) or bool(duration)


def _allocate_result_path(results_dir: Path, label: str, suffix: str) -> Path:
    """A collision-free ``premiere_results/`` destination (PROTOCOL.md §10.5).

    ``<sanitized label>_<timestamp><suffix>``, with a ``_2``/``_3``/…
    counter inserted when the same second already produced that name. NEVER
    an existing file: every push is a NEW import into Premiere, so unlike
    §2's deliberately-deterministic timeline paths, a re-run must never
    overwrite media an earlier push already placed in his project.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize_name(label, fallback="result")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = results_dir / f"{stem}_{timestamp}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = results_dir / f"{stem}_{timestamp}_{counter}{suffix}"
        counter += 1
    return candidate


def _tensor_to_pil(image: Any) -> Any:
    """First frame of a ComfyUI ``IMAGE`` tensor (float32, [0, 1], NHWC) as a
    PIL image.

    Ported from cpsb's helper of the same name; imports stay inside the
    function (pack convention -- module import never requires numpy/PIL).
    """
    import numpy as np
    from PIL import Image

    frame = image[0]
    array = frame.cpu().numpy() if hasattr(frame, "cpu") else np.asarray(frame)
    array = np.clip(array * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(array, mode="RGB")


def _resolve_video_file(video: Any, results_dir: Path, label: str) -> tuple[Path, list[str]]:
    """*video* as ONE durable on-disk file (PROTOCOL.md §10.5 durability rules).

    In order:

    1. An existing, untrimmed source file OUTSIDE ComfyUI's temp dir is
       returned as-is -- LINK IN PLACE, zero copy, the instant path for the
       owner's multi-GB video results.
    2. A source file INSIDE the temp dir is byte-copied (original extension
       kept -- a copy never re-encodes) into ``premiere_results/`` first:
       Premiere links media in place, and a temp file cleaned up later goes
       offline in his project.
    3. Anything else (in-memory VIDEO, trimmed input, no usable source) is
       written to ``premiere_results/`` as an mp4 via the pack's one shared
       VIDEO-to-file mechanism (:func:`cprb.nodes_save.materialize_video`,
       §3.3) -- ComfyUI core's ``save_to``, which preserves audio, so the
       owner's ``*-audio.mp4`` I2V results keep their soundtrack on every
       branch.

    Returns the resolved path plus any human-readable notes for the node's
    UI summary. Raises ``TypeError`` naming the ``video`` input when the
    object supports none of this (no ``save_to``).
    """
    notes: list[str] = []
    source = _video_source_path(video)
    if source is not None:
        if not _video_is_trimmed(video):
            temp_dir = _comfy_temp_dir()
            if temp_dir is not None and source.is_relative_to(temp_dir):
                dest = _allocate_result_path(results_dir, label, source.suffix)
                shutil.copy2(source, dest)
                notes.append(
                    "video: copied out of ComfyUI's temp folder (a linked temp file "
                    "would go offline in Premiere once ComfyUI cleans up)"
                )
                return dest, notes
            return source, notes
        notes.append(
            "video: input carries a trim window -- wrote the trimmed video instead "
            "of linking the whole source file"
        )
    dest = _allocate_result_path(results_dir, label, ".mp4")
    materialize_video(video, dest, node_name="PremiereSendResult", input_name="video")
    return dest, notes


def _resolve_image_file(image: Any, results_dir: Path, label: str) -> tuple[Path, list[str]]:
    """*image*'s first frame written to ``premiere_results/`` as a PNG (§10.5).

    A batched IMAGE (N > 1) writes the FIRST frame and says so in the notes
    -- list-mode fan-outs (e.g. §6.4's Iterate Shots) already run this node
    once per item, so a batch reaching one call is a single result whose
    extra frames the user almost certainly didn't mean to send separately.
    """
    notes: list[str] = []
    frames = int(image.shape[0])
    if frames > 1:
        notes.append(f"image: batch of {frames} -- wrote the first frame only")
    dest = _allocate_result_path(results_dir, label, ".png")
    _tensor_to_pil(image).save(str(dest))
    return dest, notes


class PremiereSendResult:
    """Sends a finished result to Premiere's project panel (PROTOCOL.md §10.5).

    Wire ``video`` (VIDEO) and/or ``image`` (IMAGE) -- at least one, both
    allowed (both are pushed in one run; video is the primary
    ``written_path``). Each input resolves to one durable on-disk file
    (:func:`_resolve_video_file` / :func:`_resolve_image_file`: link an
    existing video source in place, copy it out of ComfyUI's temp dir, or
    write into ``premiere_results/`` under a collision-free name) and is
    pushed to the connected Tier 2 plugin via
    :func:`cprb.routes.push_result`, which tells Premiere to import it into
    ``bin_name`` labeled ``label``. With no plugin connected/ready the run
    still SUCCEEDS -- the summary switches to "import manually" with the
    same path (§1's ethos: ComfyUI-only must work; the plugin is a better
    version, never the only version).

    ``label`` names both the pushed clip (empty = the plugin keeps the
    filename) and the stem of any file this node writes; ``bin_name`` is
    the Premiere bin the plugin imports into (default "ComfyUI Results").
    Later versions add ``color_label``/``insert_at_playhead`` widgets --
    the §10.3 message already carries both fields (empty/False), so those
    land as two ``execute`` kwargs with no protocol change.

    ``OUTPUT_NODE = True``: this node exists for its side effects. Returns
    ``written_path`` (the video's resolved path when both inputs are wired,
    else the single result's) and a UI text summary -- one "Sent to
    Premiere"/"import manually" line per pushed file, plus any notes
    (temp-copy, trim, batched image).

    Raises ``ValueError`` when neither input is wired, and ``TypeError``
    (naming the input) when ``video`` is not a usable VIDEO object.
    """

    CATEGORY = "Premiere Bridge"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("written_path",)
    FUNCTION = "execute"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {},
            "optional": {
                "video": ("VIDEO",),
                "image": ("IMAGE",),
                "label": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "Optional name for the clip in Premiere (also names the file "
                            "when this node writes one). Empty keeps the filename."
                        ),
                    },
                ),
                "bin_name": (
                    "STRING",
                    {
                        "default": DEFAULT_BIN_NAME,
                        "tooltip": "The Premiere bin the plugin imports this result into.",
                    },
                ),
                # APPENDED after bin_name on purpose: ComfyUI restores saved
                # widget values BY POSITION, so new widgets must only ever be
                # added at the END (the §8 stability rule; cpsb learned this
                # the hard way in its v0.5.21).
                "color_label": (
                    COLOR_LABEL_OPTIONS,
                    {
                        "default": COLOR_LABEL_NONE,
                        "tooltip": (
                            "Premiere label color for the imported clip — makes a "
                            "run's results visually distinct in the bin. None = "
                            "leave the clip's label alone."
                        ),
                    },
                ),
                # Appended after color_label (same position rule as above).
                # OFF by default — the highest-blast-radius step in M1: it
                # touches the user's actual sequence. One labeled undo step
                # on the Premiere side when it runs.
                "insert_at_playhead": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Also drop the imported clip onto the active sequence "
                            "at the playhead, on the track above (one undo step). "
                            "Off = the result only lands in the bin."
                        ),
                    },
                ),
            },
        }

    def execute(
        self,
        video: Any = None,
        image: Any = None,
        label: str = "",
        bin_name: str = DEFAULT_BIN_NAME,
        color_label: str = COLOR_LABEL_NONE,
        insert_at_playhead: bool = False,
    ) -> dict[str, Any]:
        if _context is None:
            raise RuntimeError(
                "PremiereSendResult: no BridgeContext configured (set_context was never called)"
            )
        if video is None and image is None:
            raise ValueError(
                "PremiereSendResult: nothing to send -- wire a video and/or an image input"
            )

        results_dir = _context.output_dir / RESULTS_DIRNAME
        # §10.3: "None" (the widget default) travels as "" — the plugin's
        # documented skip-when-empty value; a real color name passes through
        # for the plugin's Constants-enum/name-map lookup.
        wire_color = "" if color_label == COLOR_LABEL_NONE else color_label
        lines: list[str] = []
        resolved: list[Path] = []

        for input_value, resolver in ((video, _resolve_video_file), (image, _resolve_image_file)):
            if input_value is None:
                continue
            path, notes = resolver(input_value, results_dir, label)
            resolved.append(path)
            # Called through the module object (not a from-import) so tests
            # monkeypatching `cprb.routes.push_result` intercept this path
            # too -- cpsb's exact convention for its launch seam.
            pushed = routes.push_result(
                path=str(path),
                label=label,
                bin_name=bin_name,
                color_label=wire_color,
                insert_at_playhead=bool(insert_at_playhead),
            )
            if pushed:
                lines.append(f"Sent to Premiere: {path}")
            else:
                lines.append(f"Plugin not connected — import manually: {path}")
            lines.extend(f"  {note}" for note in notes)

        logger.info("cprb send_result: %s", "; ".join(line.strip() for line in lines))
        return {"ui": {"text": lines}, "result": (str(resolved[0]),)}
