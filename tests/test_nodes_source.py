"""Tests for :mod:`cprb.nodes_source` (PROTOCOL.md §11): M2's return direction.

Real files, real decodes -- same rig convention as ``test_probe.py`` /
``test_frame_extract.py``: PNGs are written with PIL and clip media with
PyAV, then read straight back through the nodes. Nothing about
``PremiereFrameSource``'s tensor or ``PremiereClipSource``'s §6.2 shot dict
is mocked, because those two shapes ARE the contract other nodes consume.

``CPRB_SEGMENT_LIST`` compatibility is asserted the only way that actually
proves it: by feeding the emitted shot into the SHIPPED
``PremiereGetShot``/``PremiereIterateShots`` from :mod:`cprb.nodes_load`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cprb.nodes_source as _nodes_source_module
from cprb.context import BridgeContext
from cprb.nodes_load import PremiereGetShot, PremiereIterateShots
from cprb.nodes_source import (
    UNRESOLVED_TIMELINE_FRAME,
    PremiereClipSource,
    PremiereFrameSource,
    build_clip_shot,
    resolve_frame_path,
    set_context,
)

#: PROTOCOL.md §6.2's FROZEN shot-dict key set (§6.1 lists them). A clip
#: source shot must carry EXACTLY these -- no extras, none missing -- or it
#: is not a CPRB_SEGMENT_LIST.
SHOT_KEYS = {
    "name",
    "path",
    "start",
    "end",
    "in",
    "out",
    "sequence_fps",
    "source_fps",
    "enabled",
    "width",
    "height",
}

CLIP_FPS = 24
CLIP_FRAMES = 96  # 4.0s at 24fps
CLIP_WIDTH = 32
CLIP_HEIGHT = 24

#: ``load_image_file`` (cprb/nodes_source.py) imports numpy/torch/PIL lazily,
#: all three at once -- ships with ComfyUI's own environment, not necessarily
#: this bare test venv. ``_write_png`` below needs only PIL (to write the
#: fixture); anything that also calls ``PremiereFrameSource().execute()``
#: needs torch too.
_PIL_REASON = "Pillow ships with ComfyUI's own environment, not this bare test venv"
_TORCH_REASON = "torch ships with ComfyUI's own environment, not this bare test venv"


#: The REAL factory, captured at import time -- before the autouse fixture
#: below replaces the module attribute -- so the error-path test can still
#: reach it.
_REAL_VIDEO_FACTORY = _nodes_source_module._video_from_file_class


class _StubVideoFromFile:
    """Stands in for core's ``VideoFromFile`` (unimportable without ComfyUI).

    Records the constructor arguments so tests can assert the §11.5 trim
    arithmetic -- the ONLY thing the node contributes on top of core's class.
    """

    def __init__(self, file: str, *, start_time: float = 0, duration: float = 0):
        self.file = file
        self.start_time = start_time
        self.duration = duration

    def get_active_trim_window(self) -> tuple[float, float]:
        return float(self.start_time), float(self.duration)


@pytest.fixture(autouse=True)
def _stub_video_from_file(monkeypatch: pytest.MonkeyPatch):
    """``comfy_api`` is not importable outside a running ComfyUI, so the
    factory seam is stubbed for every test (see ``_video_from_file_class``)."""
    monkeypatch.setattr(
        _nodes_source_module, "_video_from_file_class", lambda: _StubVideoFromFile
    )


@pytest.fixture(autouse=True)
def _reset_module_context():
    """Every test starts and ends with a clean module-level ``_context``."""
    yield
    set_context(None)


def _write_png(path: Path, size: tuple[int, int] = (6, 4), mode: str = "RGB", color=None) -> Path:
    """A real image file at *path* in *mode* (RGB/RGBA/L/P all exercised below)."""
    pytest.importorskip("PIL", reason=_PIL_REASON)
    from PIL import Image

    if color is None:
        color = {"RGB": (10, 20, 30), "RGBA": (10, 20, 30, 128), "L": 128, "P": 3}[mode]
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path)
    return path


def _write_tiny_video(
    path: Path,
    *,
    frame_count: int = CLIP_FRAMES,
    fps: int = CLIP_FPS,
    width: int = CLIP_WIDTH,
    height: int = CLIP_HEIGHT,
) -> Path:
    """A tiny real h264 video at *path* (mirrors test_probe.py's helper)."""
    import av
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("h264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        frame = av.VideoFrame.from_ndarray(
            np.full((height, width, 3), (i * 2) % 256, dtype=np.uint8), format="rgb24"
        )
        frame = frame.reformat(format=stream.pix_fmt)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    return path


# --- set_context (DI seam) -----------------------------------------------------------


def test_set_context_accepts_a_real_context_and_none(context: BridgeContext) -> None:
    set_context(context)  # must not raise
    set_context(None)  # nor this -- tests reset between cases this way


# --- PremiereFrameSource: the IMAGE tensor (§11) --------------------------------------


def test_frame_source_returns_comfy_image_tensor_shape_dtype_and_range(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch", reason=_TORCH_REASON)

    path = _write_png(tmp_path / "frames" / "frame_a.png", size=(6, 4))
    image, width, height, out_path = PremiereFrameSource().execute(str(path))

    assert isinstance(image, torch.Tensor)
    assert image.dtype == torch.float32
    assert image.shape == (1, 4, 6, 3)  # [1, H, W, 3] -- NHWC, batch of 1
    assert float(image.min()) >= 0.0
    assert float(image.max()) <= 1.0
    assert (width, height) == (6, 4)  # width/height outputs, NOT (H, W)
    assert out_path == str(path)


def test_frame_source_pixel_values_are_the_file_scaled_to_unit_range(tmp_path: Path) -> None:
    pytest.importorskip("torch", reason=_TORCH_REASON)
    path = _write_png(tmp_path / "frame.png", size=(2, 2), color=(255, 0, 51))
    image, _w, _h, _p = PremiereFrameSource().execute(str(path))

    assert pytest.approx(float(image[0, 0, 0, 0]), abs=1e-6) == 1.0
    assert pytest.approx(float(image[0, 0, 0, 1]), abs=1e-6) == 0.0
    assert pytest.approx(float(image[0, 0, 0, 2]), abs=1e-6) == 51 / 255.0


@pytest.mark.parametrize("mode", ["RGBA", "L", "P"])
def test_frame_source_converts_non_rgb_sources_to_three_channels(
    tmp_path: Path, mode: str
) -> None:
    """RGBA / greyscale / palette all become a 3-channel RGB IMAGE (§11)."""
    pytest.importorskip("torch", reason=_TORCH_REASON)
    path = _write_png(tmp_path / f"frame_{mode}.png", size=(5, 3), mode=mode)
    image, width, height, _p = PremiereFrameSource().execute(str(path))

    assert image.shape == (1, 3, 5, 3)
    assert (width, height) == (5, 3)


def test_frame_source_greyscale_replicates_the_single_channel(tmp_path: Path) -> None:
    pytest.importorskip("torch", reason=_TORCH_REASON)
    path = _write_png(tmp_path / "grey.png", size=(2, 2), mode="L", color=128)
    image, _w, _h, _p = PremiereFrameSource().execute(str(path))

    pixel = image[0, 0, 0]
    assert float(pixel[0]) == float(pixel[1]) == float(pixel[2])
    assert pytest.approx(float(pixel[0]), abs=1e-6) == 128 / 255.0


# --- PremiereFrameSource: VALIDATE_INPUTS (§11's friendly errors) ---------------------


def test_frame_source_validate_empty_path_names_the_panel_button() -> None:
    message = PremiereFrameSource.VALIDATE_INPUTS("")

    assert isinstance(message, str)  # a string blocks the queue with THIS text
    assert "Frame → ComfyUI" in message
    assert "Premiere" in message
    assert "nothing to type" in message  # never "type a path" -- the panel fills it


def test_frame_source_validate_blank_whitespace_path_is_treated_as_empty() -> None:
    assert PremiereFrameSource.VALIDATE_INPUTS("   ") == PremiereFrameSource.VALIDATE_INPUTS("")


def test_frame_source_validate_missing_file_says_so_with_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "frames" / "never_written.png"
    message = PremiereFrameSource.VALIDATE_INPUTS(str(missing))

    assert isinstance(message, str)
    assert str(missing) in message
    assert "not found" in message


def test_frame_source_validate_passes_for_a_real_file(tmp_path: Path) -> None:
    path = _write_png(tmp_path / "frame.png")
    assert PremiereFrameSource.VALIDATE_INPUTS(str(path)) is True


def test_validate_passes_through_a_linked_widget_value_of_none() -> None:
    """ComfyUI hands validation ``None`` for a link it cannot evaluate yet --
    distinct from an unset widget, which arrives as ``""``."""
    assert PremiereFrameSource.VALIDATE_INPUTS(None) is True
    assert PremiereClipSource.VALIDATE_INPUTS(None) is True


def test_frame_source_execute_raises_the_same_friendly_text_for_an_empty_path() -> None:
    with pytest.raises(ValueError, match="Frame → ComfyUI"):
        PremiereFrameSource().execute("")


def test_frame_source_execute_raises_naming_a_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "gone.png"
    with pytest.raises(ValueError, match="not found"):
        PremiereFrameSource().execute(str(missing))


def test_frame_source_execute_on_a_truncated_file_names_it(tmp_path: Path) -> None:
    """Premiere's exporter can report success before the bytes land (§11)."""
    pytest.importorskip("PIL", reason=_PIL_REASON)
    pytest.importorskip("torch", reason=_TORCH_REASON)
    half_written = tmp_path / "frames" / "half.png"
    half_written.parent.mkdir(parents=True)
    half_written.write_bytes(b"")  # zero-byte: exactly what a race leaves behind

    with pytest.raises(ValueError, match="could not read the exported frame"):
        PremiereFrameSource().execute(str(half_written))


def test_frame_source_resolves_premieres_doubled_extension_name(tmp_path: Path) -> None:
    """Pre-26.2.2 Premiere wrote `frame.png.png`; the node finds it either way."""
    pytest.importorskip("torch", reason=_TORCH_REASON)
    doubled = _write_png(tmp_path / "frames" / "frame.png.png", size=(3, 2))
    reported = tmp_path / "frames" / "frame.png"  # what the plugin would report

    assert resolve_frame_path(str(reported)) == doubled
    assert PremiereFrameSource.VALIDATE_INPUTS(str(reported)) is True
    _image, _w, _h, out_path = PremiereFrameSource().execute(str(reported))
    assert out_path == str(doubled)  # the `path` output is the file actually READ


def test_frame_source_prefers_the_exact_name_over_the_doubled_one(tmp_path: Path) -> None:
    pytest.importorskip("torch", reason=_TORCH_REASON)
    exact = _write_png(tmp_path / "frame.png", size=(4, 2))
    _write_png(tmp_path / "frame.png.png", size=(8, 6))

    assert resolve_frame_path(str(exact)) == exact
    _image, width, height, _p = PremiereFrameSource().execute(str(exact))
    assert (width, height) == (4, 2)


# --- PremiereFrameSource: IS_CHANGED (§11) -------------------------------------------


def test_frame_source_is_changed_tracks_mtime_and_size(tmp_path: Path) -> None:
    path = _write_png(tmp_path / "frame.png", size=(4, 4))
    first = PremiereFrameSource.IS_CHANGED(str(path))

    assert PremiereFrameSource.IS_CHANGED(str(path)) == first  # stable while untouched

    # A RE-export to the same path: same widget string, different pixels.
    _write_png(path, size=(8, 8))
    import os

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert PremiereFrameSource.IS_CHANGED(str(path)) != first


def test_frame_source_is_changed_is_missing_for_empty_and_absent_paths(tmp_path: Path) -> None:
    assert PremiereFrameSource.IS_CHANGED("") == "missing"
    assert PremiereFrameSource.IS_CHANGED(str(tmp_path / "nope.png")) == "missing"


# --- PremiereClipSource: the §6.2 shot dict ------------------------------------------


def test_clip_source_emits_exactly_the_frozen_shot_keys(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot_01.mp4")
    shots, path, start, end, _video = PremiereClipSource().execute(str(media), 1.0, 2.0)

    assert isinstance(shots, list)
    assert len(shots) == 1  # §11: exactly one shot
    assert set(shots[0]) == SHOT_KEYS  # no extras, none missing
    assert path == str(media)
    assert (start, end) == (1.0, 2.0)


def test_clip_source_shot_values_are_derived_from_the_probed_media(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot_01.mp4")
    shots, _path, _start, _end, _video = PremiereClipSource().execute(str(media), 1.0, 2.5)
    shot = shots[0]

    assert shot["name"] == "shot_01"  # the file stem
    assert shot["path"] == str(media)
    assert shot["in"] == 24  # round(1.0 * 24)
    assert shot["out"] == 60  # round(2.5 * 24), EXCLUSIVE
    assert shot["source_fps"] == float(CLIP_FPS)
    assert shot["sequence_fps"] == float(CLIP_FPS)  # documented default
    assert shot["width"] == CLIP_WIDTH
    assert shot["height"] == CLIP_HEIGHT
    assert shot["enabled"] is True
    # No timeline position exists in a clip selection -- the pack's own
    # "unresolved" sentinel, never a fabricated 0.
    assert shot["start"] == UNRESOLVED_TIMELINE_FRAME == -1
    assert shot["end"] == UNRESOLVED_TIMELINE_FRAME


def test_clip_source_shot_plugs_into_the_shipped_get_shot_node(tmp_path: Path) -> None:
    """The whole point of emitting CPRB_SEGMENT_LIST (§6.2 / §1's reuse ethos)."""
    media = _write_tiny_video(tmp_path / "media" / "shot_01.mp4")
    shots, _p, _s, _e, _video = PremiereClipSource().execute(str(media), 1.0, 2.0)

    (
        path,
        duration_seconds,
        in_seconds,
        frame_count,
        in_frame,
        fps,
        name,
        width,
        height,
    ) = PremiereGetShot().execute(shots, 0)

    assert path == str(media)
    assert in_frame == 24
    assert frame_count == 24  # 1.0s at 24fps
    assert pytest.approx(in_seconds) == 1.0
    assert pytest.approx(duration_seconds) == 1.0
    assert fps == float(CLIP_FPS)
    assert name == "shot_01"
    assert (width, height) == (CLIP_WIDTH, CLIP_HEIGHT)


def test_clip_source_shot_plugs_into_the_shipped_iterate_shots_node(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot_01.mp4")
    shots, _p, _s, _e, _video = PremiereClipSource().execute(str(media), 0.0, 1.0)

    columns = PremiereIterateShots().execute(shots)

    assert len(columns) == len(PremiereIterateShots.RETURN_TYPES)
    assert columns[0] == [str(media)]  # one-element lists: a one-shot fan-out
    assert columns[3] == [24]  # frame_count


def test_clip_source_unset_end_seconds_falls_back_to_the_full_duration(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "whole.mp4")
    shots, _path, start, end, _video = PremiereClipSource().execute(str(media), 0.0, 0.0)

    assert pytest.approx(end) == CLIP_FRAMES / CLIP_FPS  # 4.0s
    assert start == 0.0
    assert shots[0]["in"] == 0
    assert shots[0]["out"] == CLIP_FRAMES  # the whole file


def test_clip_source_logs_every_defaulted_value(tmp_path: Path, caplog) -> None:
    """§11: never invent a value silently."""
    media = _write_tiny_video(tmp_path / "media" / "whole.mp4")
    with caplog.at_level("INFO", logger="cprb"):
        PremiereClipSource().execute(str(media), 0.0, 0.0)

    logged = caplog.text
    assert "sequence_fps defaulted to the source rate" in logged
    assert "end_seconds was not set" in logged


def test_clip_source_clamps_a_negative_start(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")
    resolved = build_clip_shot(media, -3.0, 1.0)

    assert resolved.start_seconds == 0.0
    assert resolved.shot["in"] == 0
    assert any("clamped to 0" in note for note in resolved.notes)


def test_clip_source_sub_frame_span_still_yields_one_frame(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")
    resolved = build_clip_shot(media, 1.0, 1.0 + (1 / CLIP_FPS) / 4)

    assert resolved.shot["out"] == resolved.shot["in"] + 1
    assert any("shorter than one frame" in note for note in resolved.notes)
    # And Get Shot reads that as exactly one frame, never zero.
    assert PremiereGetShot().execute([resolved.shot], 0)[3] == 1


def test_clip_source_unprobeable_media_raises_a_clear_error(tmp_path: Path) -> None:
    not_media = tmp_path / "media" / "notes.txt"
    not_media.parent.mkdir(parents=True)
    not_media.write_text("this is not a video", encoding="utf-8")

    with pytest.raises(ValueError, match="could not read the clip's media file"):
        PremiereClipSource().execute(str(not_media), 0.0, 1.0)


def test_clip_source_inverted_in_out_raises_rather_than_guessing(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")
    with pytest.raises(ValueError, match="at or before its in point"):
        PremiereClipSource().execute(str(media), 2.0, 1.0)


def test_clip_source_in_point_past_the_media_end_says_which_cause(tmp_path: Path) -> None:
    """The whole-file fallback must not silently emit a backwards range."""
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")  # 4.0s long
    with pytest.raises(ValueError, match="at or past the end of the clip's media"):
        PremiereClipSource().execute(str(media), 9.0, 0.0)


# --- PremiereClipSource: VALIDATE_INPUTS / IS_CHANGED --------------------------------


def test_clip_source_validate_empty_path_names_the_panel_button() -> None:
    message = PremiereClipSource.VALIDATE_INPUTS("")

    assert isinstance(message, str)
    assert "Clip → ComfyUI" in message
    assert "nothing to type" in message


def test_clip_source_validate_missing_media_says_so_with_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "media" / "offline.mov"
    message = PremiereClipSource.VALIDATE_INPUTS(str(missing))

    assert isinstance(message, str)
    assert str(missing) in message
    assert "not found" in message


def test_clip_source_validate_rejects_an_inverted_in_out(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")
    message = PremiereClipSource.VALIDATE_INPUTS(str(media), 5.0, 2.0)

    assert isinstance(message, str)
    assert "at or before" in message


def test_clip_source_validate_allows_a_zero_end_and_a_real_range(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")

    assert PremiereClipSource.VALIDATE_INPUTS(str(media), 0.0, 0.0) is True  # whole file
    assert PremiereClipSource.VALIDATE_INPUTS(str(media), 1.0, 2.0) is True


def test_clip_source_validate_tolerates_non_numeric_seconds(tmp_path: Path) -> None:
    """A restored workflow / hand-edited API prompt must not traceback here."""
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")
    assert PremiereClipSource.VALIDATE_INPUTS(str(media), "oops", None) is True


def test_clip_source_is_changed_tracks_the_media_file(tmp_path: Path) -> None:
    media = _write_tiny_video(tmp_path / "media" / "shot.mp4")
    first = PremiereClipSource.IS_CHANGED(str(media), 0.0, 1.0)

    assert PremiereClipSource.IS_CHANGED(str(media), 0.0, 1.0) == first
    assert PremiereClipSource.IS_CHANGED("") == "missing"
    assert PremiereClipSource.IS_CHANGED(str(tmp_path / "nope.mp4")) == "missing"

    media.write_bytes(media.read_bytes() + b"\x00")  # re-rendered under the same name
    assert PremiereClipSource.IS_CHANGED(str(media), 0.0, 1.0) != first


# --- node registration surface --------------------------------------------------------


def test_both_nodes_declare_the_contracted_category_and_outputs() -> None:
    assert PremiereFrameSource.CATEGORY == "Premiere Bridge"
    assert PremiereClipSource.CATEGORY == "Premiere Bridge"

    assert PremiereFrameSource.RETURN_TYPES == ("IMAGE", "INT", "INT", "STRING")
    assert PremiereFrameSource.RETURN_NAMES == ("image", "width", "height", "path")
    assert PremiereClipSource.RETURN_TYPES == (
        "CPRB_SEGMENT_LIST",
        "STRING",
        "FLOAT",
        "FLOAT",
        "VIDEO",
    )
    assert PremiereClipSource.RETURN_NAMES == (
        "shots",
        "path",
        "start_seconds",
        "end_seconds",
        "video",
    )


def test_widgets_are_declared_with_the_contracted_names_and_order() -> None:
    frame_inputs: dict[str, Any] = PremiereFrameSource.INPUT_TYPES()["required"]
    assert list(frame_inputs) == ["path"]
    assert frame_inputs["path"][0] == "STRING"
    assert frame_inputs["path"][1]["default"] == ""

    clip_inputs: dict[str, Any] = PremiereClipSource.INPUT_TYPES()["required"]
    assert list(clip_inputs) == ["path", "start_seconds", "end_seconds"]
    assert clip_inputs["path"][0] == "STRING"
    for name in ("start_seconds", "end_seconds"):
        spec, options = clip_inputs[name]
        assert spec == "FLOAT"
        assert options["default"] == 0.0
        assert options["min"] == 0.0
        # round=False: a frame is ~0.0417s at 23.976fps, so the frontend's
        # usual 3-decimal rounding could move the in point a whole frame.
        assert options["round"] is False


def test_no_heavy_dependency_is_imported_at_module_scope() -> None:
    """Pack convention: torch/numpy/PIL/av load only inside the functions using them.

    Checked structurally (top-level ``import`` statements in the AST) rather
    than by string matching, so an indented import inside ``load_image_file``
    -- which is exactly where they belong -- can never trip it.
    """
    import ast

    import cprb.nodes_source as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    top_level: set[str] = set()
    for node in tree.body:  # tree.body == module scope only
        if isinstance(node, ast.Import):
            top_level.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module.split(".")[0])

    assert top_level.isdisjoint({"torch", "numpy", "PIL", "av"})


def test_the_relays_kinds_table_matches_the_registered_node_specs() -> None:
    """§8's FROZEN class ids, enforced across the Python/JS boundary.

    ``web/cprb/premiere_source.js`` hard-codes the class id AND the display
    name for each ``kind``, and nothing else can enforce that they still
    match ``__init__.py``'s ``_NODE_SPECS``. A rename or typo on either side
    breaks the relay for every saved workflow in the quietest possible way:
    the export succeeds, the panel says "sent", and the toast claims there is
    no such node in the graph. Parsed from source, in the same spirit as the
    module-scope import check above.
    """
    import ast
    import re

    relay = Path(__file__).resolve().parent.parent / "web" / "cprb" / "premiere_source.js"
    text = relay.read_text(encoding="utf-8")
    kinds = re.findall(
        r"nodeClass:\s*'([A-Za-z0-9_]+)'\s*,\s*\n\s*display:\s*'([^']+)'", text
    )
    assert len(kinds) == 2, f"expected 2 KINDS entries in premiere_source.js, got {kinds}"

    init = Path(__file__).resolve().parent.parent / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"))
    specs: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "_NODE_SPECS"
            for target in node.targets
        ):
            continue
        assert isinstance(node.value, ast.List)
        for element in node.value.elts:
            assert isinstance(element, ast.Tuple)
            _module, class_id, display = (ast.literal_eval(part) for part in element.elts)
            specs[class_id] = display

    assert specs, "could not parse _NODE_SPECS out of __init__.py"
    for class_id, display in kinds:
        assert class_id in specs, (
            f"premiere_source.js targets {class_id!r}, which __init__.py does not register"
        )
        assert specs[class_id] == display, (
            f"{class_id}: the relay's toast says {display!r} but the node menu says "
            f"{specs[class_id]!r} — the user would search for a name that does not exist"
        )


def test_clip_source_always_logs_the_frames_it_derived(tmp_path: Path, caplog) -> None:
    """The in/out numbers must be in the log on EVERY run, not just on a fallback.

    Whether Premiere's ``getOutPoint()`` is inclusive or exclusive is
    undocumented and this node commits to EXCLUSIVE; these are the numbers
    that let one live run settle it against Premiere's own duration readout.
    """
    media = _write_tiny_video(tmp_path / "clip.mp4")
    with caplog.at_level("INFO", logger="cprb"):
        PremiereClipSource().execute(path=str(media), start_seconds=1.0, end_seconds=2.0)

    line = next(m for m in caplog.messages if "in=" in m)
    assert "in=24" in line
    assert "out=48" in line
    assert "frame_count=24" in line
    assert "EXCLUSIVE" in line


# --------------------------------------------------- §11.5 the VIDEO output


def test_clip_source_video_output_carries_the_trim_window(tmp_path: Path) -> None:
    """A trimmed clip yields VideoFromFile(path, start_time=in, duration=span)."""
    media = _write_tiny_video(tmp_path / "clip.mp4")
    *_rest, video = PremiereClipSource().execute(str(media), 1.0, 2.5)

    assert video.file == str(media)
    assert video.start_time == 1.0
    assert video.duration == 1.5  # 2.5 - 1.0: the span, not the out point


def test_clip_source_whole_file_video_is_honestly_untrimmed(tmp_path: Path) -> None:
    """end_seconds=0 must map to core's own (0, 0) whole-file sentinel.

    Send to Premiere's link-in-place branch keys off get_active_trim_window()
    == (0, 0); substituting the probed duration would make every whole-file
    clip report itself trimmed and force a needless re-encode on the way back.
    """
    media = _write_tiny_video(tmp_path / "clip.mp4")
    *_rest, video = PremiereClipSource().execute(str(media), 0.0, 0.0)

    assert video.get_active_trim_window() == (0.0, 0.0)


def test_clip_source_start_only_trim_keeps_duration_zero(tmp_path: Path) -> None:
    """In point set, out point unset: trim from start_time until the end."""
    media = _write_tiny_video(tmp_path / "clip.mp4")
    *_rest, video = PremiereClipSource().execute(str(media), 1.0, 0.0)

    assert video.start_time == 1.0
    assert video.duration == 0.0  # core's "until the end"


def test_video_factory_error_names_the_fix() -> None:
    """Without ComfyUI the real factory must fail with advice, not a bare
    ImportError -- via the import-time capture, since the autouse fixture
    replaces the module attribute before any test body runs."""
    with pytest.raises(RuntimeError, match="update ComfyUI"):
        _REAL_VIDEO_FACTORY()
