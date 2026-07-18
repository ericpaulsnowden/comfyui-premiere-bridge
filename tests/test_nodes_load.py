"""Tests for :mod:`cprb.nodes_load` (PROTOCOL.md §6.1/§6.3): the ComfyUI-facing
wrapper around :mod:`cprb.timeline_read` -- widgets, ``IS_CHANGED``,
``VALIDATE_INPUTS``, the ``summary`` string, and ``PremiereGetShot``'s math.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cprb.context import BridgeContext
from cprb.nodes_load import PremiereGetShot, PremiereLoadTimeline, set_context

FIXTURES_DIR = Path(__file__).parent / "fixtures"
NOISY_XML = FIXTURES_DIR / "noisy_premiere_export.xml"
CLEAN_XML = FIXTURES_DIR / "clean_two_clip.xml"


@pytest.fixture(autouse=True)
def _reset_module_context():
    """Every test starts from a clean module-level ``_context`` and leaves one behind."""
    yield
    set_context(None)


# --- set_context (DI seam) -----------------------------------------------------------


def test_set_context_accepts_a_real_context_and_none(context: BridgeContext):
    set_context(context)  # must not raise
    set_context(None)  # must not raise either -- tests reset between cases this way


# --- PremiereLoadTimeline.execute -----------------------------------------------------


def test_execute_default_skip_disabled_excludes_the_disabled_clip():
    node = PremiereLoadTimeline()
    shots, count, summary = node.execute(str(NOISY_XML))  # skip_disabled defaults True

    assert count == 3
    assert len(shots) == 3
    assert [shot["name"] for shot in shots] == [
        "Interview A",
        "B-Roll Overlay",
        "Interview A (reprise)",
    ]
    assert "Bad Take" not in summary
    assert "[DISABLED]" not in summary


def test_execute_summary_format_is_exact():
    node = PremiereLoadTimeline()
    _shots, _count, summary = node.execute(str(NOISY_XML), skip_disabled=True)

    expected = "\n".join(
        [
            "[0] Interview A | D:/Media/Interview Take 1.mov | 0-90 @30fps",
            "[1] B-Roll Overlay | /Volumes/Footage/broll_001.mov | 10-90 @24fps",
            "[2] Interview A (reprise) | D:/Media/Interview Take 1.mov | 90-150 @30fps",
        ]
    )
    assert summary == expected


def test_execute_skip_disabled_false_keeps_and_marks_the_disabled_clip():
    node = PremiereLoadTimeline()
    shots, count, summary = node.execute(str(NOISY_XML), skip_disabled=False)

    assert count == 4
    names = [shot["name"] for shot in shots]
    assert names == ["Interview A", "B-Roll Overlay", "Bad Take", "Interview A (reprise)"]

    # "Bad Take" is at index 2 of the RETURNED list -- the summary's leading
    # [i] must match that (the index PremiereGetShot would need), not its
    # position in some other, unfiltered ordering.
    lines = summary.splitlines()
    assert lines[2].startswith("[2] Bad Take |")
    assert lines[2].endswith("[DISABLED]")


def test_execute_missing_file_raises_file_not_found():
    node = PremiereLoadTimeline()
    with pytest.raises(FileNotFoundError):
        node.execute("/no/such/file/exists.xml")


def test_execute_zero_clip_file_raises_clear_error():
    empty_xml = FIXTURES_DIR / "empty_sequence.xml"
    node = PremiereLoadTimeline()
    with pytest.raises(ValueError, match="clipitem"):
        node.execute(str(empty_xml))


def test_execute_on_clean_writer_shaped_fixture():
    node = PremiereLoadTimeline()
    shots, count, _summary = node.execute(str(CLEAN_XML))
    assert count == 2
    assert shots[0]["path"] == "/Users/eric/media/intro.mp4"
    assert shots[1]["path"] == "/Users/eric/media/outro.mp4"


# --- PremiereLoadTimeline.VALIDATE_INPUTS ---------------------------------------------


def test_validate_inputs_rejects_empty_path():
    result = PremiereLoadTimeline.VALIDATE_INPUTS("")
    assert result is not True
    assert "empty" in result.lower()


def test_validate_inputs_rejects_missing_file():
    result = PremiereLoadTimeline.VALIDATE_INPUTS("/no/such/file/exists.xml")
    assert isinstance(result, str)
    assert "not found" in result.lower()


def test_validate_inputs_accepts_an_existing_file():
    assert PremiereLoadTimeline.VALIDATE_INPUTS(str(NOISY_XML)) is True


# --- PremiereLoadTimeline.IS_CHANGED ---------------------------------------------------


def test_is_changed_differs_when_the_file_is_touched(tmp_path: Path):
    xml_path = tmp_path / "timeline.xml"
    xml_path.write_text(_fixture_body("short"), encoding="utf-8")
    before = PremiereLoadTimeline.IS_CHANGED(str(xml_path))

    # A real re-export overwrites the same filename with different (here:
    # differently-sized) content -- IS_CHANGED must change so the node re-runs.
    xml_path.write_text(_fixture_body("a fair bit longer than before"), encoding="utf-8")
    after = PremiereLoadTimeline.IS_CHANGED(str(xml_path))

    assert before != after


def test_is_changed_missing_file_does_not_raise():
    result = PremiereLoadTimeline.IS_CHANGED("/no/such/file/exists.xml")
    assert isinstance(result, str)


def _fixture_body(marker: str) -> str:
    return f"<!-- {marker} -->"


# --- PremiereGetShot.execute -----------------------------------------------------------


def test_get_shot_math_with_a_non_zero_in():
    node = PremiereLoadTimeline()
    shots, _count, _summary = node.execute(str(NOISY_XML))  # skip_disabled=True

    get_shot = PremiereGetShot()
    # index 2 == "Interview A (reprise)": in=90, out=150, source_fps=30.0.
    path, in_seconds, duration_seconds, in_frame, frame_count, fps, name = get_shot.execute(
        shots, 2
    )

    assert name == "Interview A (reprise)"
    assert path == "D:/Media/Interview Take 1.mov"
    assert in_frame == 90
    assert frame_count == 60  # 150 - 90
    assert fps == pytest.approx(30.0)
    assert in_seconds == pytest.approx(3.0)  # 90 / 30
    assert duration_seconds == pytest.approx(2.0)  # 60 / 30


def test_get_shot_index_out_of_range_names_valid_range():
    node = PremiereLoadTimeline()
    shots, count, _summary = node.execute(str(NOISY_XML))  # 3 shots, valid range 0..2

    get_shot = PremiereGetShot()
    with pytest.raises(ValueError, match=r"0\.\.2"):
        get_shot.execute(shots, count)  # one past the end


def test_get_shot_negative_index_out_of_range():
    node = PremiereLoadTimeline()
    shots, _count, _summary = node.execute(str(NOISY_XML))

    get_shot = PremiereGetShot()
    with pytest.raises(ValueError, match="out of range"):
        get_shot.execute(shots, -1)


def test_get_shot_empty_list_raises():
    get_shot = PremiereGetShot()
    with pytest.raises(ValueError, match="empty"):
        get_shot.execute([], 0)


# ------------------------------------------------------------------ .prproj


def test_validate_inputs_names_the_prproj_mistake(tmp_path) -> None:
    """Pointing at Premiere's own project file gets export instructions, not
    a generic XML error (owner hit this live, 2026-07-18)."""
    prproj = tmp_path / "My Edit.prproj"
    prproj.write_bytes(b"\x1f\x8b" + b"\x00" * 16)
    verdict = PremiereLoadTimeline.VALIDATE_INPUTS(str(prproj))
    assert isinstance(verdict, str)
    assert "Final Cut Pro XML" in verdict and ".prproj" in verdict


def test_execute_rejects_prproj_by_extension(tmp_path) -> None:
    prproj = tmp_path / "edit.prproj"
    prproj.write_bytes(b"\x1f\x8b" + b"\x00" * 16)
    node = PremiereLoadTimeline()
    with pytest.raises(ValueError, match="Final Cut Pro XML"):
        node.execute(str(prproj))


def test_execute_rejects_gzip_content_even_with_xml_extension(tmp_path) -> None:
    """A renamed project file still gets the helpful message: detection is
    by gzip magic bytes, not just the extension."""
    sneaky = tmp_path / "renamed.xml"
    sneaky.write_bytes(b"\x1f\x8b" + b"\x00" * 16)
    node = PremiereLoadTimeline()
    with pytest.raises(ValueError, match="Final Cut Pro XML"):
        node.execute(str(sneaky))
