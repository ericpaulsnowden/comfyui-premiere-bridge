"""Tests for :mod:`cprb.frame_extract` (PROTOCOL.md §6.5).

Real PyAV end-to-end, same rig convention as ``test_probe.py``: a tiny
h264-encoded video is synthesized on disk (one solid, distinct color per
frame) and decoded back through :func:`extract_frame` for real -- no
``av``/``torch`` mocking here (``cprb.nodes_load``'s own tests monkeypatch
this function instead, to isolate ``PremiereShotFrame``'s wiring from this
module's actual decode, which is what THIS file tests for real). The
missing/unreadable-file cases need no real, decodable media at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cprb.frame_extract import extract_frame

FRAME_COUNT = 8
FPS = 8  # one frame per second -- convenient in_seconds math below.
WIDTH = 32
HEIGHT = 24


def _write_tiny_video(path: Path) -> None:
    """A tiny real h264-encoded video at *path*, via PyAV (mirrors test_probe.py).

    Each frame is a solid, distinct color (``(i * 20) % 256``) so a
    successfully-seeked decode is visibly different from frame to frame.
    """
    import av
    import numpy as np

    container = av.open(str(path), mode="w")
    stream = container.add_stream("h264", rate=FPS)
    stream.width = WIDTH
    stream.height = HEIGHT
    stream.pix_fmt = "yuv420p"
    for i in range(FRAME_COUNT):
        frame = av.VideoFrame.from_ndarray(
            np.full((HEIGHT, WIDTH, 3), (i * 20) % 256, dtype=np.uint8), format="rgb24"
        )
        frame = frame.reformat(format=stream.pix_fmt)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


# --- happy path, real media ----------------------------------------------------------


def test_extract_frame_returns_expected_shape_dtype_and_range(tmp_path: Path) -> None:
    import torch

    path = tmp_path / "tiny.mp4"
    _write_tiny_video(path)

    tensor = extract_frame(str(path), in_seconds=0.5)

    assert isinstance(tensor, torch.Tensor)
    assert tensor.shape == (1, HEIGHT, WIDTH, 3)
    assert tensor.dtype == torch.float32
    assert float(tensor.min()) >= 0.0
    assert float(tensor.max()) <= 1.0


def test_extract_frame_in_seconds_zero_decodes_the_first_frame(tmp_path: Path) -> None:
    path = tmp_path / "tiny.mp4"
    _write_tiny_video(path)

    tensor = extract_frame(str(path), in_seconds=0.0)

    # Frame 0 was encoded as solid (0, 0, 0) -- h264/yuv420p round-tripping
    # introduces at most trivial rounding noise for a flat color.
    pixel = tensor[0, 0, 0]
    assert float(pixel.max()) < 0.05


def test_extract_frame_seeks_towards_the_requested_point(tmp_path: Path) -> None:
    """Not a frame-exact assertion (PROTOCOL.md §6.5 is explicit this is
    "nearest frame", best-effort) -- just confirms *in_seconds* actually
    changes which frame comes back, rather than always the first one."""
    import torch

    path = tmp_path / "tiny.mp4"
    _write_tiny_video(path)

    early = extract_frame(str(path), in_seconds=0.0)
    late = extract_frame(str(path), in_seconds=(FRAME_COUNT - 1) / FPS)

    assert early.shape == late.shape
    assert not torch.equal(early, late)


# --- error paths, real (missing/unreadable) media -------------------------------------


def test_extract_frame_missing_file_raises_value_error_naming_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.mp4"

    with pytest.raises(ValueError, match=str(missing)):
        extract_frame(str(missing), in_seconds=0.0)


def test_extract_frame_unreadable_file_raises_value_error_naming_the_path(tmp_path: Path) -> None:
    garbage = tmp_path / "not_really_a_video.mp4"
    garbage.write_bytes(b"this is not a video file, just plain bytes")

    with pytest.raises(ValueError, match=str(garbage)):
        extract_frame(str(garbage), in_seconds=0.0)
