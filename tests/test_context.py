"""Tests for cprb.context._atomic_write (roadmap P0-2: atomic timeline saves).

All three ``PremiereSaveTimeline`` outputs (``.xml``/``.edl``/``.otio``) land
through this helper, so what these tests pin down is the whole point of its
existence: content round-trips exactly, the intermediate temp file NEVER
survives (success or failure), and a failure at any stage -- the data write
itself, or the final rename -- leaves a pre-existing destination
byte-for-byte untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cprb.context import _atomic_write


def _entries(directory: Path) -> set[str]:
    """Every name in *directory* -- catches any leaked ``.<name>.*.tmp``."""
    return {entry.name for entry in directory.iterdir()}


def test_text_round_trips_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "seq.xml"
    _atomic_write(target, "héllo <timeline/>\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "héllo <timeline/>\n"
    assert _entries(tmp_path) == {"seq.xml"}


def test_bytes_round_trip_and_leave_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    payload = bytes(range(256))
    _atomic_write(target, payload)
    assert target.read_bytes() == payload
    assert _entries(tmp_path) == {"blob.bin"}


def test_overwrites_existing_target_completely(tmp_path: Path) -> None:
    """A shorter rewrite fully replaces the file -- no stale tail bytes."""
    target = tmp_path / "seq.edl"
    target.write_text("old content, quite long indeed", encoding="utf-8")
    _atomic_write(target, "new", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "new"
    assert _entries(tmp_path) == {"seq.edl"}


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "seq.xml"
    _atomic_write(target, "content", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "content"
    assert _entries(target.parent) == {"seq.xml"}


def test_failed_data_write_keeps_old_content_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure while writing the temp file: old file intact, temp removed."""
    target = tmp_path / "seq.xml"
    target.write_text("old", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    # Patched AFTER the setup write above; read_text below stays unpatched.
    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        _atomic_write(target, "new", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "old"
    assert _entries(tmp_path) == {"seq.xml"}


def test_failed_replace_keeps_old_content_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure at the final rename: old file intact, temp removed."""
    target = tmp_path / "seq.xml"
    target.write_text("old", encoding="utf-8")

    def boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("cprb.context.os.replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        _atomic_write(target, "new", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "old"
    assert _entries(tmp_path) == {"seq.xml"}
