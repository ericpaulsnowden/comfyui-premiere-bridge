"""Tests for cprb.nodes_send.PremiereSendResult (PROTOCOL.md §10.5).

The fake VIDEO objects mirror ``test_nodes_save.FakeVideo``'s duck-typed
surface (``save_to`` records calls and writes real bytes) and extend it
with ComfyUI core's ``get_stream_source``/``get_active_trim_window`` where
a branch needs them — no ComfyUI import anywhere. ``cprb.routes.push_result``
is monkeypatched through the routes module (the seam ``execute`` calls
through) for the "plugin connected" cases; the "no plugin" cases use the
REAL push_result, which correctly reports False against the reset slot.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cprb import nodes_send
from cprb import routes as cprb_routes
from cprb.context import BridgeContext
from cprb.nodes_send import PremiereSendResult


def _image_batch(count: int):
    """A ComfyUI-shaped IMAGE batch: (N, H, W, C) float32 in [0, 1], all black."""
    import numpy as np

    return np.zeros((count, 4, 4, 3), dtype=np.float32)


class FakeVideoSaveToOnly:
    """test_nodes_save.FakeVideo's exact surface: only ``save_to``, which
    records the call and writes a (fake-content) file."""

    def __init__(self) -> None:
        self.save_to_calls: list[str] = []

    def save_to(self, path: str) -> None:
        self.save_to_calls.append(path)
        Path(path).write_bytes(b"fake-mp4-bytes")


class FakeVideoWithSource(FakeVideoSaveToOnly):
    """Adds core ``VideoFromFile``'s surface: a real source path + trim window."""

    def __init__(self, source: Path, trim: tuple[float, float] = (0.0, 0.0)) -> None:
        super().__init__()
        self._source = source
        self._trim = trim

    def get_stream_source(self) -> str:
        return str(self._source)

    def get_active_trim_window(self) -> tuple[float, float]:
        return self._trim


class FakeVideoInMemory(FakeVideoSaveToOnly):
    """Core's in-memory shape: ``get_stream_source`` yields a BytesIO."""

    def get_stream_source(self) -> io.BytesIO:
        return io.BytesIO(b"in-memory-container-bytes")


class VideoInput:
    """Stands in for comfy core's abstract base AT MODULE SCOPE, so an
    inheriting fake's un-overridden method has ``__qualname__ ==
    "VideoInput.get_stream_source"`` — exactly what the real base's shows."""

    def get_stream_source(self) -> Any:
        raise AssertionError(
            "the base-class default must never be called: it would encode the "
            "whole video into RAM just to answer the source-path probe"
        )


class FakeComponentsVideo(VideoInput, FakeVideoSaveToOnly):
    """A components-backed VIDEO: inherits the base's get_stream_source."""


@pytest.fixture(autouse=True)
def _configure_context(context: BridgeContext):
    nodes_send.set_context(context)
    yield
    nodes_send.set_context(None)


@pytest.fixture(autouse=True)
def _reset_plugin_slot():
    """push_result reads routes' module-level slot; keep tests order-proof."""
    cprb_routes._connection = None
    cprb_routes._loop = None
    yield
    cprb_routes._connection = None
    cprb_routes._loop = None


@pytest.fixture
def pushes(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record push_result calls and report the plugin as connected."""
    calls: list[dict] = []

    def fake_push(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(cprb_routes, "push_result", fake_push)
    return calls


def _results_dir(context: BridgeContext) -> Path:
    return context.output_dir / "premiere_results"


# --- node contract -------------------------------------------------------------


def test_node_class_contract() -> None:
    assert PremiereSendResult.CATEGORY == "Premiere Bridge"
    assert PremiereSendResult.RETURN_TYPES == ("STRING",)
    assert PremiereSendResult.RETURN_NAMES == ("written_path",)
    assert PremiereSendResult.FUNCTION == "execute"
    assert PremiereSendResult.OUTPUT_NODE is True


def test_input_types_required_is_empty_and_everything_is_optional() -> None:
    spec = PremiereSendResult.INPUT_TYPES()
    assert spec["required"] == {}
    assert set(spec["optional"]) == {"video", "image", "label", "bin_name"}
    assert spec["optional"]["video"][0] == "VIDEO"
    assert spec["optional"]["image"][0] == "IMAGE"
    assert spec["optional"]["label"][1]["default"] == ""
    assert spec["optional"]["bin_name"][1]["default"] == "ComfyUI Results"


def test_execute_raises_when_nothing_is_wired() -> None:
    node = PremiereSendResult()
    with pytest.raises(ValueError, match="wire a video and/or an image"):
        node.execute()


def test_execute_without_context_raises() -> None:
    nodes_send.set_context(None)
    node = PremiereSendResult()
    with pytest.raises(RuntimeError, match="set_context"):
        node.execute(image=_image_batch(1))


# --- IMAGE → PNG ----------------------------------------------------------------


def test_execute_image_writes_png_and_says_import_manually_without_a_plugin(
    context: BridgeContext,
) -> None:
    node = PremiereSendResult()
    result = node.execute(image=_image_batch(1), label="My Frame")

    written = Path(result["result"][0])
    assert written.parent == _results_dir(context)
    assert written.name.startswith("My Frame_")
    assert written.suffix == ".png"

    from PIL import Image

    with Image.open(written) as img:
        assert img.size == (4, 4)
        assert img.getpixel((0, 0)) == (0, 0, 0)

    summary = "\n".join(result["ui"]["text"])
    assert f"Plugin not connected — import manually: {written}" in summary
    assert "Sent to Premiere" not in summary


def test_execute_image_default_label_falls_back_to_result_stem(
    context: BridgeContext,
) -> None:
    node = PremiereSendResult()
    written = Path(node.execute(image=_image_batch(1))["result"][0])
    assert written.name.startswith("result_")


def test_written_names_never_collide_even_within_one_second(
    context: BridgeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every push is a NEW import (§10.5): identical label + timestamp must
    produce distinct files, never overwrite the earlier one."""
    monkeypatch.setattr(nodes_send.time, "strftime", lambda _fmt: "20260723-120000")
    node = PremiereSendResult()

    first = Path(node.execute(image=_image_batch(1), label="Same")["result"][0])
    second = Path(node.execute(image=_image_batch(1), label="Same")["result"][0])

    assert first.name == "Same_20260723-120000.png"
    assert second.name == "Same_20260723-120000_2.png"
    assert first.exists() and second.exists()


def test_execute_batched_image_writes_first_frame_and_says_so(
    context: BridgeContext,
) -> None:
    node = PremiereSendResult()
    result = node.execute(image=_image_batch(3))

    summary = "\n".join(result["ui"]["text"])
    assert "batch of 3 -- wrote the first frame only" in summary
    written = Path(result["result"][0])
    from PIL import Image

    with Image.open(written) as img:
        assert img.size == (4, 4)  # one frame, not a sheet


# --- VIDEO durability branches (§10.5) ------------------------------------------


def test_execute_video_with_durable_source_links_in_place(
    context: BridgeContext, tmp_path: Path, pushes: list[dict]
) -> None:
    source = tmp_path / "renders" / "shot-audio.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"multi-GB stand-in")
    video = FakeVideoWithSource(source)

    node = PremiereSendResult()
    result = node.execute(video=video, label="Shot 1", bin_name="My Bin")

    assert pushes == [
        {"path": str(source.resolve()), "label": "Shot 1", "bin_name": "My Bin"}
    ]
    assert result["result"][0] == str(source.resolve())
    assert video.save_to_calls == []  # zero copy, zero re-encode
    assert not _results_dir(context).exists()  # nothing was written at all

    summary = "\n".join(result["ui"]["text"])
    assert f"Sent to Premiere: {source.resolve()}" in summary


def test_execute_video_under_comfy_temp_is_copied_to_a_durable_path(
    context: BridgeContext, tmp_path: Path, pushes: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "comfy_temp"
    source = temp_dir / "wan_00001-audio.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"temp-render-bytes")
    monkeypatch.setattr(nodes_send, "_comfy_temp_dir", lambda: temp_dir.resolve())
    video = FakeVideoWithSource(source)

    node = PremiereSendResult()
    result = node.execute(video=video, label="Temp Clip")

    pushed = Path(pushes[0]["path"])
    assert pushed.parent == _results_dir(context)
    assert pushed.suffix == ".mp4"  # the ORIGINAL extension, byte-copied
    assert pushed.read_bytes() == b"temp-render-bytes"
    assert source.exists()  # original untouched
    assert video.save_to_calls == []  # copy, never a re-encode
    assert result["result"][0] == str(pushed)

    summary = "\n".join(result["ui"]["text"])
    assert "copied out of ComfyUI's temp folder" in summary


def test_execute_in_memory_video_is_saved_via_save_to(
    context: BridgeContext, pushes: list[dict]
) -> None:
    video = FakeVideoInMemory()
    node = PremiereSendResult()
    result = node.execute(video=video, label="Generated")

    written = Path(result["result"][0])
    assert written.parent == _results_dir(context)
    assert written.suffix == ".mp4"
    assert video.save_to_calls == [str(written)]
    assert written.exists()
    assert pushes[0]["path"] == str(written)


def test_execute_video_with_only_save_to_uses_the_shared_mechanism(
    context: BridgeContext, pushes: list[dict]
) -> None:
    """The minimum §3.3 surface (exactly what nodes_save consumes) is enough."""
    video = FakeVideoSaveToOnly()
    node = PremiereSendResult()
    result = node.execute(video=video)

    written = Path(result["result"][0])
    assert written.parent == _results_dir(context)
    assert video.save_to_calls == [str(written)]


def test_execute_trimmed_video_is_materialized_not_linked(
    context: BridgeContext, tmp_path: Path, pushes: list[dict]
) -> None:
    """An active trim window means the source file is NOT the wired video —
    linking it would import the whole untrimmed clip into Premiere."""
    source = tmp_path / "long_take.mp4"
    source.write_bytes(b"two full minutes")
    video = FakeVideoWithSource(source, trim=(1.0, 2.0))

    node = PremiereSendResult()
    result = node.execute(video=video, label="Trimmed")

    written = Path(result["result"][0])
    assert written != source.resolve()
    assert written.parent == _results_dir(context)
    assert video.save_to_calls == [str(written)]

    summary = "\n".join(result["ui"]["text"])
    assert "trim window" in summary


def test_execute_video_source_that_no_longer_exists_falls_back_to_save_to(
    context: BridgeContext, tmp_path: Path, pushes: list[dict]
) -> None:
    video = FakeVideoWithSource(tmp_path / "cleaned_up.mp4")  # never created
    node = PremiereSendResult()
    result = node.execute(video=video)

    written = Path(result["result"][0])
    assert written.parent == _results_dir(context)
    assert video.save_to_calls == [str(written)]


def test_execute_never_calls_the_base_class_stream_source_probe(
    context: BridgeContext, pushes: list[dict]
) -> None:
    """Core's BASE get_stream_source encodes the whole video into RAM just to
    answer; an object that merely inherits it must go straight to save_to."""
    video = FakeComponentsVideo()
    node = PremiereSendResult()
    result = node.execute(video=video)  # AssertionError inside if it's called

    written = Path(result["result"][0])
    assert video.save_to_calls == [str(written)]


def test_execute_video_without_save_to_raises_naming_the_input() -> None:
    class NotAVideo:
        pass

    node = PremiereSendResult()
    with pytest.raises(TypeError, match="PremiereSendResult: video does not support"):
        node.execute(video=NotAVideo())


# --- both inputs in one run ------------------------------------------------------


def test_execute_both_inputs_push_both_and_video_path_is_primary(
    context: BridgeContext, tmp_path: Path, pushes: list[dict]
) -> None:
    source = tmp_path / "final-audio.mp4"
    source.write_bytes(b"video bytes")
    video = FakeVideoWithSource(source)

    node = PremiereSendResult()
    result = node.execute(
        video=video, image=_image_batch(1), label="Pair", bin_name="Both Bin"
    )

    assert len(pushes) == 2
    assert pushes[0]["path"] == str(source.resolve())  # video first
    assert pushes[1]["path"].endswith(".png")
    assert all(push["bin_name"] == "Both Bin" for push in pushes)
    assert all(push["label"] == "Pair" for push in pushes)

    # written_path is the VIDEO's path when both are sent (PROTOCOL.md §10.5).
    assert result["result"][0] == str(source.resolve())

    summary = "\n".join(result["ui"]["text"])
    assert summary.count("Sent to Premiere: ") == 2
