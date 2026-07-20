"""HTTP routes (PROTOCOL.md §7): config, the picker feed, reveal, timeline_dir.

Every filesystem-touching route is host-machine-only (§7.1); a forwarded
request stands in for "a browser on another machine" throughout. The reveal
helper is monkeypatched everywhere — tests must never spawn real Finder or
Explorer windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cprb import routes as cprb_routes
from cprb.context import BridgeContext
from cprb.routes import build_routes
from cprb.version import __version__

REMOTE_HEADERS = {"X-Forwarded-For": "192.168.1.50"}


@pytest.fixture
async def client(context: BridgeContext, aiohttp_client):
    app = web.Application()
    app.add_routes(build_routes(context))
    return await aiohttp_client(app)


# ------------------------------------------------------------------ version


async def test_version_matches_the_package(client) -> None:
    data = await (await client.get("/cprb/version")).json()
    assert data["version"] == __version__


# ------------------------------------------------------------------- config


async def test_config_reports_is_local_true_for_loopback(
    client, context: BridgeContext
) -> None:
    data = await (await client.get("/cprb/config")).json()
    assert data["is_local"] is True
    assert data["output_dir"] == str(context.output_dir)
    assert data["input_dir"] == str(context.input_dir)


async def test_config_reports_is_local_false_for_forwarded_caller(client) -> None:
    data = await (await client.get("/cprb/config", headers=REMOTE_HEADERS)).json()
    assert data["is_local"] is False


# ------------------------------------------------------------------ fs/list
#
# STANDARD-fs-browse.md (2026-07-19): the shared cross-plugin "server
# filesystem Browse" contract with cpsb/epsnodes. Reshaped from bare-string
# `dirs`/`files` to names-only `{"name": ...}` (`{"name", "size", "mtime"}`
# for files) entries, added `sep` + `truncated`, and the `ROOTS` sentinel now
# returns a labeled roots list (default dir + Home + platform roots) on
# EVERY platform, not just Windows.


async def test_fs_list_defaults_to_output_dir_and_filters_xml(
    client, context: BridgeContext
) -> None:
    (context.output_dir / "edit.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / "notes.txt").write_text("x", encoding="utf-8")
    (context.output_dir / "premiere_timelines").mkdir()
    data = await (await client.get("/cprb/fs/list")).json()
    assert data["dir"] == str(context.output_dir)
    assert data["sep"] == os.sep
    assert data["truncated"] is False
    assert data["dirs"] == [{"name": "premiere_timelines"}]
    assert len(data["files"]) == 1
    assert data["files"][0]["name"] == "edit.xml"
    assert data["files"][0]["size"] > 0
    assert isinstance(data["files"][0]["mtime"], float)
    assert data["parent"] == str(context.output_dir.parent)


async def test_fs_list_skips_dotfiles(client, context: BridgeContext) -> None:
    (context.output_dir / "edit.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / ".hidden.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / ".hidden_dir").mkdir()
    data = await (await client.get("/cprb/fs/list")).json()
    assert [entry["name"] for entry in data["files"]] == ["edit.xml"]
    assert [entry["name"] for entry in data["dirs"]] == []


async def test_fs_list_matches_extensions_case_insensitively(
    client, context: BridgeContext
) -> None:
    (context.output_dir / "SHOUTY.XML").write_text("<xmeml/>", encoding="utf-8")
    data = await (await client.get("/cprb/fs/list")).json()
    assert [entry["name"] for entry in data["files"]] == ["SHOUTY.XML"]


async def test_fs_list_honors_an_explicit_ext_allowlist(
    client, context: BridgeContext
) -> None:
    (context.output_dir / "a.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / "b.edl").write_text("TITLE: x", encoding="utf-8")
    (context.output_dir / "c.otio").write_text("{}", encoding="utf-8")
    data = await (await client.get("/cprb/fs/list", params={"ext": "edl,.otio"})).json()
    assert [entry["name"] for entry in data["files"]] == ["b.edl", "c.otio"]


async def test_fs_list_blank_ext_falls_back_to_the_default_allowlist(
    client, context: BridgeContext
) -> None:
    # A stray `ext=` must not turn the timeline picker into a general
    # file browser (see _parse_extensions).
    (context.output_dir / "a.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / "b.txt").write_text("x", encoding="utf-8")
    data = await (await client.get("/cprb/fs/list", params={"ext": " , "})).json()
    assert [entry["name"] for entry in data["files"]] == ["a.xml"]


async def test_fs_list_navigates_an_explicit_absolute_dir(client, tmp_path: Path) -> None:
    elsewhere = tmp_path / "exports"
    elsewhere.mkdir()
    (elsewhere / "cut.xml").write_text("<xmeml/>", encoding="utf-8")
    data = await (
        await client.get("/cprb/fs/list", params={"dir": str(elsewhere)})
    ).json()
    assert [entry["name"] for entry in data["files"]] == ["cut.xml"]


async def test_fs_list_sorted_case_insensitively_dirs_then_files(
    client, context: BridgeContext
) -> None:
    (context.output_dir / "zeta.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / "Alpha.xml").write_text("<xmeml/>", encoding="utf-8")
    (context.output_dir / "Zeta").mkdir()
    (context.output_dir / "alpha_dir").mkdir()
    data = await (await client.get("/cprb/fs/list")).json()
    assert [entry["name"] for entry in data["dirs"]] == ["alpha_dir", "Zeta"]
    assert [entry["name"] for entry in data["files"]] == ["Alpha.xml", "zeta.xml"]


async def test_fs_list_truncates_over_500_entries(client, context: BridgeContext) -> None:
    for index in range(501):
        (context.output_dir / f"{index:04d}.xml").touch()
    data = await (await client.get("/cprb/fs/list")).json()
    assert data["truncated"] is True
    assert len(data["files"]) == 500


async def test_fs_list_is_loopback_only(client) -> None:
    response = await client.get("/cprb/fs/list", headers=REMOTE_HEADERS)
    assert response.status == 403


async def test_fs_list_rejects_a_relative_dir(client) -> None:
    response = await client.get("/cprb/fs/list", params={"dir": "not/absolute"})
    assert response.status == 400


async def test_fs_list_rejects_a_file_path(client, tmp_path: Path) -> None:
    a_file = tmp_path / "not_a_directory.xml"
    a_file.write_text("<xmeml/>", encoding="utf-8")
    response = await client.get("/cprb/fs/list", params={"dir": str(a_file)})
    assert response.status == 400


async def test_fs_list_unreadable_dir_is_400(client, tmp_path: Path) -> None:
    response = await client.get(
        "/cprb/fs/list", params={"dir": str(tmp_path / "nope" / "missing")}
    )
    assert response.status == 400


# ------------------------------------------------ fs/list: ROOTS & drive roots
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further.
# Drive enumeration and `os.name` aren't real on this (macOS) test machine,
# so every Windows-flavored case goes through the `_is_windows` /
# `_list_windows_drives` monkeypatch seams (routes.py §"fs/list: ROOTS &
# drives") rather than touching real drives.


async def test_fs_list_roots_sentinel_lists_windows_drives_when_monkeypatched(
    client, monkeypatch, context: BridgeContext
) -> None:
    monkeypatch.setattr(cprb_routes, "_is_windows", lambda: True)
    monkeypatch.setattr(cprb_routes, "_list_windows_drives", lambda: ["C:\\", "D:\\", "U:\\"])
    response = await client.get("/cprb/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None
    assert data["sep"] == os.sep
    assert data["files"] == []
    assert data["truncated"] is False

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["ComfyUI Output"] == str(context.output_dir.resolve())
    assert by_name["Home"] == str(Path.home().resolve())
    assert by_name["C:"] == "C:\\"
    assert by_name["D:"] == "D:\\"
    assert by_name["U:"] == "U:\\"
    # Standard's declared ROOTS order: default dir, Home, then platform roots.
    assert [entry["name"] for entry in data["dirs"]] == [
        "ComfyUI Output", "Home", "C:", "D:", "U:"
    ]


async def test_fs_list_roots_sentinel_is_still_loopback_only(client, monkeypatch) -> None:
    monkeypatch.setattr(cprb_routes, "_is_windows", lambda: True)
    response = await client.get(
        "/cprb/fs/list", params={"dir": "ROOTS"}, headers=REMOTE_HEADERS
    )
    assert response.status == 403


async def test_fs_list_roots_sentinel_includes_default_dir_and_home_on_posix(
    client, context: BridgeContext
) -> None:
    response = await client.get("/cprb/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    assert data["dir"] == "ROOTS"
    assert data["parent"] is None

    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["ComfyUI Output"] == str(context.output_dir.resolve())
    assert by_name["Home"] == str(Path.home().resolve())


async def test_fs_list_roots_sentinel_lists_macos_volumes_when_monkeypatched(
    client, monkeypatch
) -> None:
    monkeypatch.setattr(
        cprb_routes, "_list_macos_volumes", lambda: ["/Volumes/Macintosh HD", "/Volumes/Backup"]
    )
    response = await client.get("/cprb/fs/list", params={"dir": "ROOTS"})
    data = await response.json()
    by_name = {entry["name"]: entry["path"] for entry in data["dirs"]}
    assert by_name["Macintosh HD"] == "/Volumes/Macintosh HD"
    assert by_name["Backup"] == "/Volumes/Backup"


async def test_fs_list_drive_root_parent_climbs_to_roots_under_monkeypatched_windows(
    client, monkeypatch
) -> None:
    # "/" doubles as our only real, listable filesystem root on this test
    # machine — `_is_windows` alone decides which label a root's parent
    # gets, so patching just that seam is enough to exercise the branch.
    monkeypatch.setattr(cprb_routes, "_is_windows", lambda: True)
    response = await client.get("/cprb/fs/list", params={"dir": "/"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] == "ROOTS"


async def test_fs_list_posix_root_parent_is_null_without_monkeypatching(client) -> None:
    response = await client.get("/cprb/fs/list", params={"dir": "/"})
    data = await response.json()
    assert data["dir"] == "/"
    assert data["parent"] is None


def test_is_unc_share_root_detects_a_share_root() -> None:
    assert cprb_routes._is_unc_share_root(Path(r"\\server\share")) is True


def test_is_unc_share_root_rejects_a_drive_root() -> None:
    assert cprb_routes._is_unc_share_root(Path("C:\\")) is False


def test_fs_root_parent_is_roots_for_a_drive_root_on_windows() -> None:
    assert cprb_routes._fs_root_parent(Path("C:\\"), windows=True) == "ROOTS"


def test_fs_root_parent_is_null_for_a_unc_share_root_even_on_windows() -> None:
    # No portable way to enumerate a server's other shares (PROTOCOL.md
    # §7.2) — a share root has nothing to climb to, unlike a drive root.
    assert cprb_routes._fs_root_parent(Path(r"\\server\share"), windows=True) is None


def test_fs_root_parent_is_null_on_posix() -> None:
    assert cprb_routes._fs_root_parent(Path("/"), windows=False) is None


# -------------------------------------------------------------- open_folder


async def test_open_folder_reveals_a_files_parent(
    client, context: BridgeContext, monkeypatch
) -> None:
    revealed = []
    monkeypatch.setattr(cprb_routes, "_reveal_folder", revealed.append)
    target = context.output_dir / "edit.xml"
    target.write_text("<xmeml/>", encoding="utf-8")
    response = await client.post("/cprb/open_folder", json={"path": str(target)})
    assert response.status == 200
    assert await response.json() == {"ok": True}
    assert revealed == [context.output_dir]


async def test_open_folder_reveals_a_directory_itself(
    client, context: BridgeContext, monkeypatch
) -> None:
    revealed = []
    monkeypatch.setattr(cprb_routes, "_reveal_folder", revealed.append)
    response = await client.post("/cprb/open_folder", json={"path": str(context.output_dir)})
    assert response.status == 200
    assert revealed == [context.output_dir]


async def test_open_folder_is_loopback_only(client, context: BridgeContext, monkeypatch) -> None:
    revealed = []
    monkeypatch.setattr(cprb_routes, "_reveal_folder", revealed.append)
    response = await client.post(
        "/cprb/open_folder", json={"path": str(context.output_dir)}, headers=REMOTE_HEADERS
    )
    assert response.status == 403
    assert revealed == []


async def test_open_folder_requires_a_path(client) -> None:
    response = await client.post("/cprb/open_folder", json={})
    assert response.status == 400


async def test_open_folder_missing_folder_is_404(client, tmp_path: Path) -> None:
    response = await client.post(
        "/cprb/open_folder", json={"path": str(tmp_path / "gone" / "edit.xml")}
    )
    assert response.status == 404


async def test_open_folder_spawn_failure_is_500_with_the_reason(
    client, context: BridgeContext, monkeypatch
) -> None:
    def boom(_path: Path) -> None:
        raise RuntimeError("no file manager here")

    monkeypatch.setattr(cprb_routes, "_reveal_folder", boom)
    response = await client.post("/cprb/open_folder", json={"path": str(context.output_dir)})
    assert response.status == 500
    assert "no file manager here" in (await response.json())["error"]


# ------------------------------------------------------------ timeline_dir


async def test_timeline_dir_resolves_without_creating_the_folder(
    client, context: BridgeContext
) -> None:
    data = await (
        await client.get("/cprb/timeline_dir", params={"sequence_name": "My Edit"})
    ).json()
    expected = context.output_dir / "premiere_timelines" / "My Edit"
    assert data["dir"] == str(expected)
    assert data["exists"] is False
    # Asking the question must not answer it (PROTOCOL.md §7.2).
    assert not expected.exists()


async def test_timeline_dir_reports_exists_once_written(
    client, context: BridgeContext
) -> None:
    context.timeline_dir("My Edit")
    data = await (
        await client.get("/cprb/timeline_dir", params={"sequence_name": "My Edit"})
    ).json()
    assert data["exists"] is True


async def test_timeline_dir_sanitizes_like_the_writer(
    client, context: BridgeContext
) -> None:
    # The frontend never re-implements sanitize_name; it asks (§7.2).
    data = await (
        await client.get("/cprb/timeline_dir", params={"sequence_name": 'Bad:Name?/x'})
    ).json()
    assert data["dir"] == str(context.resolve_timeline_dir("Bad:Name?/x"))
    assert ":" not in Path(data["dir"]).name


# --------------------------------------------- timeline_dir: output_dir (§3.2)
#
# 2026-07-20: PremiereSaveTimeline gained an optional `output_dir` override
# (owner ask: give Save the same Browse…/Open folder bar Load has). This
# route must resolve the IDENTICAL effective folder the node itself would
# write to, given the SAME (possibly absent/blank/invalid) `output_dir` --
# both go through the one `BridgeContext.resolve_timeline_dir`.


async def test_timeline_dir_output_dir_absolute_overrides_the_default_base(
    client, tmp_path: Path
) -> None:
    custom_base = tmp_path / "nas_project"
    data = await (
        await client.get(
            "/cprb/timeline_dir",
            params={"sequence_name": "NAS Cut", "output_dir": str(custom_base)},
        )
    ).json()
    assert data["dir"] == str(custom_base / "NAS Cut")
    assert "premiere_timelines" not in data["dir"]
    assert data["exists"] is False


async def test_timeline_dir_output_dir_matches_resolve_timeline_dir(
    client, context: BridgeContext, tmp_path: Path
) -> None:
    custom_base = tmp_path / "elsewhere"
    data = await (
        await client.get(
            "/cprb/timeline_dir",
            params={"sequence_name": "Match Me", "output_dir": str(custom_base)},
        )
    ).json()
    assert data["dir"] == str(context.resolve_timeline_dir("Match Me", str(custom_base)))


async def test_timeline_dir_output_dir_non_absolute_falls_back_to_default_without_400(
    client, context: BridgeContext
) -> None:
    # Unlike `fs/list`'s `dir` param, a bad `output_dir` here never 400s --
    # it silently resolves to exactly what the node itself would fall back
    # to (the node is the one that surfaces a "rejected" warning; this
    # route only ever mirrors the effective result).
    response = await client.get(
        "/cprb/timeline_dir",
        params={"sequence_name": "Fallback", "output_dir": "not/absolute"},
    )
    assert response.status == 200
    data = await response.json()
    expected = context.output_dir / "premiere_timelines" / "Fallback"
    assert data["dir"] == str(expected)


async def test_timeline_dir_output_dir_blank_matches_omitted(
    client, context: BridgeContext
) -> None:
    with_blank = await (
        await client.get(
            "/cprb/timeline_dir", params={"sequence_name": "Same", "output_dir": ""}
        )
    ).json()
    omitted = await (
        await client.get("/cprb/timeline_dir", params={"sequence_name": "Same"})
    ).json()
    assert with_blank == omitted
    assert with_blank["dir"] == str(context.output_dir / "premiere_timelines" / "Same")
