"""Tests for cprb.probe (PROTOCOL.md §3.3).

Happy paths use REAL media synthesized with PyAV (the rig venv ships ``av``
18); the handful of edge cases that are impractical to coax real encoders
into producing (no video stream, an unset frame rate, a container exposing
neither a frame count nor any duration) monkeypatch ``av.open`` with small
fake container/stream objects that mimic just the attributes
``cprb.probe`` reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cprb.probe import MediaInfo, ProbeError, probe_media


def _write_tiny_video(
    path: Path,
    *,
    frame_count: int = 8,
    fps: int = 24,
    fmt: str | None = None,
    width: int = 32,
    height: int = 24,
) -> None:
    """A tiny real h264-encoded video at *path*, via PyAV."""
    import av
    import numpy as np

    container = av.open(str(path), mode="w", format=fmt)
    stream = container.add_stream("h264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for i in range(frame_count):
        frame = av.VideoFrame.from_ndarray(
            np.full((height, width, 3), (i * 10) % 256, dtype=np.uint8), format="rgb24"
        )
        frame = frame.reformat(format=stream.pix_fmt)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


# --- happy paths, real media -------------------------------------------------


def test_probe_real_mp4_reports_exact_frames_fps_and_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "tiny.mp4"
    _write_tiny_video(path, frame_count=8, fps=24, width=32, height=24)

    info = probe_media(str(path))

    assert info == MediaInfo(frames=8, fps=24.0, width=32, height=24, duration_seconds=8 / 24)


def test_probe_duration_seconds_is_always_frames_over_fps(tmp_path: Path) -> None:
    path = tmp_path / "tiny.mp4"
    _write_tiny_video(path, frame_count=12, fps=30, width=16, height=16)

    info = probe_media(str(path))

    assert info.duration_seconds == pytest.approx(info.frames / info.fps)


def test_probe_frames_unknown_falls_back_to_duration_times_fps(tmp_path: Path) -> None:
    """mpegts is a real, PyAV-writable container that does not store a frame
    count in its header (``stream.frames == 0``), so this exercises the
    documented ``duration * fps`` fallback against genuine media rather than
    a mock."""
    path = tmp_path / "tiny.ts"
    _write_tiny_video(path, frame_count=8, fps=24, fmt="mpegts")

    info = probe_media(str(path))

    assert info.frames == 8
    assert info.fps == 24.0


# --- error paths, real media --------------------------------------------------


def test_probe_missing_file_raises_probe_error_naming_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.mp4"

    with pytest.raises(ProbeError, match=str(missing)):
        probe_media(str(missing))


def test_probe_unreadable_file_raises_probe_error_naming_the_path(tmp_path: Path) -> None:
    garbage = tmp_path / "not_really_a_video.mp4"
    garbage.write_bytes(b"this is not a video file, just plain bytes")

    with pytest.raises(ProbeError, match=str(garbage)):
        probe_media(str(garbage))


# --- error/fallback paths that need a fake container/stream ------------------


class _FakeStream:
    def __init__(
        self,
        average_rate: float | None = 24.0,
        frames: int = 8,
        width: int = 32,
        height: int = 24,
        duration: int | None = None,
        time_base: float | None = None,
    ) -> None:
        self.average_rate = average_rate
        self.frames = frames
        self.width = width
        self.height = height
        self.duration = duration
        self.time_base = time_base


class _FakeStreams:
    def __init__(self, video: list[_FakeStream]) -> None:
        self.video = video


class _FakeContainer:
    def __init__(self, video_streams: list[_FakeStream], duration: int | None = None) -> None:
        self.streams = _FakeStreams(video_streams)
        self.duration = duration
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _patch_av_open(monkeypatch: pytest.MonkeyPatch, container: _FakeContainer) -> None:
    import av

    monkeypatch.setattr(av, "open", lambda *_a, **_k: container)


def test_probe_no_video_stream_raises_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_av_open(monkeypatch, _FakeContainer(video_streams=[]))

    with pytest.raises(ProbeError, match="no video stream"):
        probe_media("fake/path.mp4")


def test_probe_missing_fps_raises_clearly_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A falsy average_rate is a hard error -- never a silent fallback to
    ``guessed_rate`` or a duration-derived guess (cprb build brief)."""
    _patch_av_open(monkeypatch, _FakeContainer([_FakeStream(average_rate=0)]))

    with pytest.raises(ProbeError, match="frame rate"):
        probe_media("fake/path.mp4")


def test_probe_frames_and_duration_both_unavailable_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _FakeStream(frames=0, duration=None, time_base=None)
    _patch_av_open(monkeypatch, _FakeContainer([stream], duration=None))

    with pytest.raises(ProbeError, match="neither a frame count nor a duration"):
        probe_media("fake/path.mp4")


def test_probe_frames_fallback_uses_container_duration_when_stream_duration_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream itself exposes no duration/time_base, but the CONTAINER
    does (microseconds) -- the fallback still resolves frames from that."""
    stream = _FakeStream(average_rate=24.0, frames=0, duration=None, time_base=None)
    container = _FakeContainer([stream], duration=1_000_000)  # 1.0 second, in microseconds

    _patch_av_open(monkeypatch, container)

    info = probe_media("fake/path.mp4")

    assert info.frames == 24  # round(1.0s * 24fps)
    assert container.closed


def test_probe_zero_effective_frames_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # duration_seconds ~= 1e-6s * 24fps ~= 0.000024 -> rounds to 0 frames.
    stream = _FakeStream(average_rate=24.0, frames=0, duration=1, time_base=1 / 1_000_000)
    _patch_av_open(monkeypatch, _FakeContainer([stream]))

    with pytest.raises(ProbeError, match="zero effective frames"):
        probe_media("fake/path.mp4")


def test_probe_missing_dimensions_raises_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_av_open(monkeypatch, _FakeContainer([_FakeStream(width=0)]))

    with pytest.raises(ProbeError, match="dimensions"):
        probe_media("fake/path.mp4")


def test_probe_closes_container_even_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _FakeContainer([_FakeStream()])
    _patch_av_open(monkeypatch, container)

    probe_media("fake/path.mp4")

    assert container.closed


def test_probe_closes_container_even_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _FakeContainer([_FakeStream(width=0)])
    _patch_av_open(monkeypatch, container)

    with pytest.raises(ProbeError):
        probe_media("fake/path.mp4")

    assert container.closed
