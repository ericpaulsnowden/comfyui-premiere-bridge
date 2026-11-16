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


# --- shares that refuse to rename onto an existing name ---------------------
#
# gvfs/FUSE network mounts (a Linux box with an SMB share mounted through the
# desktop) raise FileExistsError from os.replace when the destination exists,
# although POSIX says rename replaces. A rename to a FREE name does work there.


@pytest.fixture()
def refusing_share(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make os.replace and os.rename refuse an existing destination."""
    import os

    real_replace, real_rename = os.replace, os.rename

    def replace(src: object, dst: object) -> None:
        if Path(str(dst)).exists():
            raise FileExistsError(17, "File exists", str(dst))
        real_replace(src, dst)

    def rename(src: object, dst: object) -> None:
        if Path(str(dst)).exists():
            raise FileExistsError(17, "File exists", str(dst))
        real_rename(src, dst)

    monkeypatch.setattr("cprb.context.os.replace", replace)
    monkeypatch.setattr("cprb.context.os.rename", rename)


def test_overwrite_succeeds_on_a_share_that_refuses_replace(
    tmp_path: Path, refusing_share: None
) -> None:
    target = tmp_path / "seq.xml"
    target.write_text("old", encoding="utf-8")

    _atomic_write(target, "new", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "new"
    assert _entries(tmp_path) == {"seq.xml"}


def test_first_write_is_unaffected_on_a_share_that_refuses_replace(
    tmp_path: Path, refusing_share: None
) -> None:
    target = tmp_path / "seq.xml"

    _atomic_write(target, "new", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "new"
    assert _entries(tmp_path) == {"seq.xml"}


def test_old_content_comes_back_when_the_new_file_cannot_take_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The share refuses the replace AND then refuses the temp's rename."""
    import os

    target = tmp_path / "seq.xml"
    target.write_text("old", encoding="utf-8")
    real_rename = os.rename

    def replace(src: object, dst: object) -> None:
        raise FileExistsError(17, "File exists", str(dst))

    def rename(src: object, dst: object) -> None:
        if str(src).endswith(".tmp"):
            raise OSError("share went away")
        real_rename(src, dst)

    monkeypatch.setattr("cprb.context.os.replace", replace)
    monkeypatch.setattr("cprb.context.os.rename", rename)
    with pytest.raises(OSError, match="share went away"):
        _atomic_write(target, "new", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "old"
    assert _entries(tmp_path) == {"seq.xml"}


def test_media_overwrite_succeeds_on_a_share_that_refuses_replace(
    tmp_path: Path, refusing_share: None
) -> None:
    from cprb.nodes_save import write_into_place

    target = tmp_path / "001_video_1.mp4"
    target.write_bytes(b"old clip")

    write_into_place(target, lambda tmp: Path(tmp).write_bytes(b"new clip"))

    assert target.read_bytes() == b"new clip"
    assert _entries(tmp_path) == {"001_video_1.mp4"}


def test_a_failed_move_back_says_where_the_previous_version_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused replace, then the new file AND the move back both fail.

    The previous version cannot be put back, so the error must name the
    sibling it now lives in -- otherwise it is a hidden file nobody knows of.
    """
    import os

    target = tmp_path / "seq.xml"
    target.write_text("old", encoding="utf-8")
    real_rename = os.rename

    def replace(src: object, dst: object) -> None:
        raise FileExistsError(17, "File exists", str(dst))

    def rename(src: object, dst: object) -> None:
        if str(dst) == str(target):
            raise OSError("share went away")
        real_rename(src, dst)

    monkeypatch.setattr("cprb.context.os.replace", replace)
    monkeypatch.setattr("cprb.context.os.rename", rename)
    with pytest.raises(OSError) as raised:
        _atomic_write(target, "new", encoding="utf-8")

    survivors = _entries(tmp_path)
    assert len(survivors) == 1
    kept = tmp_path / survivors.pop()
    assert kept.read_text(encoding="utf-8") == "old"
    assert str(kept) in str(raised.value)


def test_a_previous_version_that_cannot_be_removed_is_reported(
    tmp_path: Path,
    refusing_share: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The save succeeded; a leftover copy must be named, not hidden."""
    target = tmp_path / "seq.xml"
    target.write_text("old", encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith(".bak"):
            raise PermissionError("locked")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with caplog.at_level("WARNING", logger="cprb"):
        _atomic_write(target, "new", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "new"
    leftovers = _entries(tmp_path) - {"seq.xml"}
    assert len(leftovers) == 1
    assert str(tmp_path / leftovers.pop()) in caplog.text
