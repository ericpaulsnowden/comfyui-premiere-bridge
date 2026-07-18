"""Tests for cprb.nodes_save.PremiereSaveTimeline (PROTOCOL.md §3).

``cprb.probe.probe_media`` is monkeypatched throughout (a fake that only
requires the target path to actually exist on disk, so materialized VIDEO
outputs -- real files written by :class:`FakeVideo`'s own ``save_to`` --
probe successfully while a genuinely-missing ``paths`` entry still fails
exactly like the real prober would). This keeps these tests about
:mod:`cprb.nodes_save`'s OWN responsibilities (ordering, materialization,
file placement, error wording) rather than re-testing PyAV or
:mod:`cprb.timeline_write` (already covered in ``test_probe.py`` /
``test_timeline_write.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cprb import nodes_save, timeline_write
from cprb import probe as probe_module
from cprb.context import BridgeContext
from cprb.nodes_save import PremiereSaveTimeline
from cprb.probe import MediaInfo, ProbeError

FAKE_FRAMES = 48
FAKE_FPS = 24.0
FAKE_WIDTH = 64
FAKE_HEIGHT = 48


def _fake_probe_media(path: str) -> MediaInfo:
    """Stands in for real PyAV probing: succeeds iff *path* exists on disk."""
    if not Path(path).exists():
        raise ProbeError(f"could not open media file for probing: {path}")
    return MediaInfo(
        frames=FAKE_FRAMES, fps=FAKE_FPS, width=FAKE_WIDTH, height=FAKE_HEIGHT,
        duration_seconds=FAKE_FRAMES / FAKE_FPS,
    )


class FakeVideo:
    """A duck-typed stand-in for a ComfyUI VIDEO object: records save_to calls
    and writes a (fake-content) file so the path exists for probing."""

    def __init__(self) -> None:
        self.save_to_calls: list[str] = []

    def save_to(self, path: str) -> None:
        self.save_to_calls.append(path)
        Path(path).write_bytes(b"fake-mp4-bytes")


@pytest.fixture(autouse=True)
def _configure_context(context: BridgeContext) -> None:
    nodes_save.set_context(context)


@pytest.fixture(autouse=True)
def _fake_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_module, "probe_media", _fake_probe_media)


def _write_path_line(tmp_path: Path, name: str, content: bytes = b"x") -> Path:
    path = tmp_path / "input" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- §3.1 ordering + materialization -----------------------------------------


def test_execute_orders_video_inputs_then_paths_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """video_1..4 (ascending, skipping unconnected) THEN paths lines top-to-bottom."""
    captured: dict[str, list[timeline_write.ClipSpec]] = {}

    def fake_build_xmeml(_sequence_name: str, _fps_key: str, clips: list) -> str:
        captured["clips"] = list(clips)
        return "<xmeml/>"

    monkeypatch.setattr(timeline_write, "build_xmeml", fake_build_xmeml)

    video_2, video_4 = FakeVideo(), FakeVideo()
    clip_a = _write_path_line(tmp_path, "clipA.mov")
    clip_b = _write_path_line(tmp_path, "clipB.mov")

    node = PremiereSaveTimeline()
    node.execute(
        sequence_name="Order Test",
        fps="24",
        paths=f"{clip_a}\n{clip_b}\n",
        write_edl=False,
        write_otio=False,
        video_2=video_2,
        video_4=video_4,
    )

    names = [clip.name for clip in captured["clips"]]
    assert names == ["002_video_2", "004_video_4", "clipA", "clipB"]
    # video_1/video_2/video_3/video_4's own save_to were called (not skipped):
    assert len(video_2.save_to_calls) == 1
    assert len(video_4.save_to_calls) == 1
    assert isinstance(video_2.save_to_calls[0], str)  # duck-typed contract: str, not Path


def test_execute_skips_unconnected_video_slots_without_gaps_in_media_numbering(
    context: BridgeContext,
) -> None:
    video_3 = FakeVideo()
    node = PremiereSaveTimeline()

    result = node.execute(
        sequence_name="Gap Test", fps="24", paths="", write_edl=False, write_otio=False,
        video_3=video_3,
    )

    media_dir = context.output_dir / "premiere_timelines" / "Gap Test" / "media"
    # Only video_3 was connected; its OWN socket index (3) names the file,
    # even though it's the only (1st materialized) file.
    assert (media_dir / "003_video_3.mp4").exists()
    assert Path(result["result"][0]).exists()


def test_execute_video_without_save_to_raises_clear_error_naming_the_input() -> None:
    class NotAVideo:
        pass

    node = PremiereSaveTimeline()
    with pytest.raises(TypeError, match="video_1"):
        node.execute(
            sequence_name="Bad Video",
            fps="24",
            paths="",
            write_edl=False,
            write_otio=False,
            video_1=NotAVideo(),
        )


# --- paths widget parsing -----------------------------------------------------


def test_execute_missing_path_line_names_the_line_number(tmp_path: Path) -> None:
    good = _write_path_line(tmp_path, "good.mov")
    missing = tmp_path / "input" / "missing.mov"  # deliberately never created

    # line 1: good (kept) / line 2: comment (skipped) / line 3: blank (skipped)
    # / line 4: missing (kept, and fails).
    paths_text = f"{good}\n# a comment\n\n{missing}\n"

    node = PremiereSaveTimeline()
    with pytest.raises(ProbeError, match="paths line 4"):
        node.execute(
            sequence_name="Missing Path", fps="24", paths=paths_text,
            write_edl=False, write_otio=False,
        )


def test_execute_blank_and_comment_path_lines_are_ignored(tmp_path: Path) -> None:
    good = _write_path_line(tmp_path, "good.mov")
    paths_text = f"\n# comment\n   \n{good}\n"

    node = PremiereSaveTimeline()
    result = node.execute(
        sequence_name="Comments", fps="24", paths=paths_text,
        write_edl=False, write_otio=False,
    )

    assert Path(result["result"][0]).exists()


# --- empty-everything error ---------------------------------------------------


def test_execute_raises_when_nothing_connected_and_paths_is_empty() -> None:
    node = PremiereSaveTimeline()
    with pytest.raises(ValueError, match="no clips"):
        node.execute(sequence_name="Empty", fps="24", paths="", write_edl=False, write_otio=False)


def test_execute_raises_when_paths_is_only_blanks_and_comments() -> None:
    node = PremiereSaveTimeline()
    with pytest.raises(ValueError, match="no clips"):
        node.execute(
            sequence_name="Empty", fps="24", paths="# nothing\n\n   \n",
            write_edl=False, write_otio=False,
        )


# --- overwrite-in-place (§2 re-run) -------------------------------------------


def test_execute_rerun_with_same_sequence_name_overwrites_in_place(
    context: BridgeContext, tmp_path: Path
) -> None:
    clip_a = _write_path_line(tmp_path, "a.mov")
    clip_b = _write_path_line(tmp_path, "b.mov")
    node = PremiereSaveTimeline()

    result_1 = node.execute(
        sequence_name="Rerun", fps="24", paths=str(clip_a), write_edl=False, write_otio=False
    )
    xml_path_1 = result_1["result"][0]
    content_1 = Path(xml_path_1).read_text()

    result_2 = node.execute(
        sequence_name="Rerun", fps="24", paths=str(clip_b), write_edl=False, write_otio=False
    )
    xml_path_2 = result_2["result"][0]
    content_2 = Path(xml_path_2).read_text()

    assert xml_path_1 == xml_path_2, "same sequence_name must resolve to the same .xml path"
    assert content_1 != content_2, "the second run's content must reflect the new inputs"
    assert "<name>a</name>" in content_1 and "<name>a</name>" not in content_2
    assert "<name>b</name>" in content_2 and "<name>b</name>" not in content_1


# --- otio soft dependency ------------------------------------------------------


def test_execute_write_otio_missing_warns_but_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_build_otio(*_args: object, **_kwargs: object) -> str:
        raise ImportError("simulated: opentimelineio not installed")

    monkeypatch.setattr(timeline_write, "build_otio", fake_build_otio)

    clip_a = _write_path_line(tmp_path, "a.mov")
    node = PremiereSaveTimeline()

    result = node.execute(
        sequence_name="Otio Missing", fps="24", paths=str(clip_a),
        write_edl=False, write_otio=True,
    )

    summary = "\n".join(result["ui"]["text"])
    assert "otio skipped (not installed)" in summary
    assert Path(result["result"][0]).exists()  # xml still written; run did not fail
    assert not any(line.strip().endswith(".otio") for line in result["ui"]["text"])


def test_execute_write_otio_success_writes_otio_file_and_lists_it(
    monkeypatch: pytest.MonkeyPatch, context: BridgeContext, tmp_path: Path
) -> None:
    def fake_build_otio(_sequence_name: str, _fps_key: str, _clips: list) -> str:
        return '{"OTIO_SCHEMA": "Timeline.1"}'

    monkeypatch.setattr(timeline_write, "build_otio", fake_build_otio)

    clip_a = _write_path_line(tmp_path, "a.mov")
    node = PremiereSaveTimeline()

    result = node.execute(
        sequence_name="Otio Ok", fps="24", paths=str(clip_a), write_edl=False, write_otio=True
    )

    otio_path = context.output_dir / "premiere_timelines" / "Otio Ok" / "Otio Ok.otio"
    assert otio_path.exists()
    assert otio_path.read_text() == '{"OTIO_SCHEMA": "Timeline.1"}'
    assert any(str(otio_path) in line for line in result["ui"]["text"])


# --- end-to-end happy path (real timeline_write, fake probe) ------------------


def test_execute_happy_path_writes_expected_files_with_edl(
    context: BridgeContext, tmp_path: Path
) -> None:
    video_1 = FakeVideo()
    extra = _write_path_line(tmp_path, "extra.mov")
    node = PremiereSaveTimeline()

    result = node.execute(
        sequence_name="Happy Path", fps="24", paths=str(extra),
        write_edl=True, write_otio=False, video_1=video_1,
    )

    out_dir = context.output_dir / "premiere_timelines" / "Happy Path"
    xml_path = out_dir / "Happy Path.xml"
    edl_path = out_dir / "Happy Path.edl"
    media_path = out_dir / "media" / "001_video_1.mp4"

    assert Path(result["result"][0]) == xml_path
    assert xml_path.exists()
    assert edl_path.exists()
    assert media_path.exists()
    assert video_1.save_to_calls == [str(media_path)]

    xml_text = xml_path.read_text()
    assert "<name>001_video_1</name>" in xml_text
    assert "<name>extra</name>" in xml_text
