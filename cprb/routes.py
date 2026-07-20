"""HTTP routes (PROTOCOL.md §7).

Tier 1's routes are all in service of the frontend's file affordances: the
version/config rows the panel reads, a server-filesystem browser feeding the
`Browse…` picker, an OS-file-manager reveal, and the resolver that answers
"where would this sequence name write?" without the frontend re-implementing
:func:`~cprb.context.sanitize_name`.

Everything that touches the SERVER's filesystem is loopback-only (§7.1): a
browser on another machine can still type paths and run the nodes, it just
can't browse or reveal folders on the host. Handlers close over the injected
:class:`~cprb.context.BridgeContext`, so tests build an
``aiohttp.web.Application`` from :func:`build_routes` directly — no ComfyUI.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import string
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from aiohttp import web

from .context import BridgeContext
from .version import __version__

logger = logging.getLogger("cprb")

#: PROTOCOL.md §7.2 — the picker's default extension allowlist (Load
#: Premiere Timeline reads Premiere's Final Cut Pro XML export).
DEFAULT_EXTENSIONS = (".xml",)

#: ../../STANDARD-fs-browse.md's `dir` sentinel meaning "the virtual top
#: level": this pack's own default output directory (labeled), "Home", and
#: every drive on Windows / every `/Volumes` mount on macOS.
ROOTS = "ROOTS"

#: STANDARD-fs-browse.md's locality policy for THIS pack, as an explicit,
#: documented, build-time flag (not a request-time param -- flipping this via
#: a query string would let any caller downgrade their own security posture).
#: `True` here is cprb's pre-existing posture (PROTOCOL.md §7.1: everything
#: that touches the SERVER's filesystem is loopback-only) -- porting to the
#: shared contract must never silently flip a pack's posture. Contrast cpsb's
#: own (deliberately `False`) flag for the same route shape.
FS_LIST_LOCAL_ONLY: bool = True

#: STANDARD-fs-browse.md ROOTS listing label for this pack's own default
#: fs/list directory (`context.output_dir` -- ComfyUI's own output dir, not a
#: cprb-owned folder; PROTOCOL.md §7.2).
_FS_LIST_DEFAULT_DIR_LABEL = "ComfyUI Output"

#: Same for the user's home directory (always present, regardless of platform).
_FS_LIST_HOME_LABEL = "Home"

#: Cap on combined `dirs` + `files` entries returned by one `/cprb/fs/list`
#: listing (root or directory) -- so a directory with an enormous number of
#: children can't turn one request into a multi-megabyte response. Counts
#: only entries actually emitted -- a huge pile of hidden dotfiles or
#: extension-filtered-out files never consumes a slot.
_FS_LIST_MAX_ENTRIES = 500


def request_is_loopback(request: web.Request) -> bool:
    """True when *request* comes from this machine (PROTOCOL.md §7.1).

    A forwarded request (``X-Forwarded-For``) is never loopback — the proxy
    hop hides the real origin, so it gets the restricted tier. ``remote``
    being absent (unix sockets, aiohttp test clients) counts as loopback:
    both mean "not a foreign machine".
    """
    if "X-Forwarded-For" in request.headers:
        return False
    remote = request.remote
    if remote is None:
        return True
    try:
        return ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def error_response(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


# --------------------------------------------------- fs/list: ROOTS & drives
#
# 2026-07-19 fix: the §7.2 picker could reach the top of C:\ but no further
# — drive roots reported `parent: null`, which reads as "nothing above
# here" and traps the user instead of offering a way to another drive or a
# NAS/UNC path. The pieces below are their own functions (rather than
# inlined in the handler) specifically so tests can monkeypatch the
# Windows-only bits from macOS/Linux CI — real drive enumeration and real
# `os.name` are both unavailable there.


def _is_windows() -> bool:
    """True on a real Windows host (PROTOCOL.md §7.2's drive-letter world).

    A seam, not a bare ``os.name`` check inlined in the handler, so tests
    can monkeypatch it to exercise the Windows branches (drive enumeration,
    drive-root → "ROOTS" parent) without depending on the dev/CI machine's
    own platform.
    """
    return os.name == "nt"


def _list_windows_drives() -> list[str]:
    """Every existing drive's root, e.g. ``["C:\\\\", "D:\\\\"]``.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on Windows
    (:func:`_platform_root_entries`). Probes A-Z with ``Path.exists()`` —
    factored out to its own function so tests replace it wholesale instead of
    needing real drives to exist.
    """
    return [
        f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()
    ]


def _list_macos_volumes() -> list[str]:
    """Every mounted volume under ``/Volumes`` on a macOS (or other POSIX) host.

    Backs the ``dir="ROOTS"`` sentinel's platform tail on POSIX
    (:func:`_platform_root_entries`) -- ``/Volumes`` always contains at least
    a symlink back to the boot volume (e.g. ``Macintosh HD``) plus one entry
    per externally-mounted disk/network share, exactly the set a user reaches
    for when they mean "browse by volume" the way Finder's own sidebar does.
    Hidden entries are skipped (same convention the directory-listing branch
    of ``get_fs_list`` uses) and a stat failure on any one entry is skipped
    rather than aborting the whole root listing. Factored out to its own
    function, like :func:`_list_windows_drives`, so tests replace it wholesale.
    """
    volumes_dir = Path("/Volumes")
    if not volumes_dir.is_dir():
        return []
    try:
        entries = sorted(volumes_dir.iterdir(), key=lambda p: p.name.casefold())
    except OSError:
        return []
    volumes = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            volumes.append(str(entry))
    return volumes


def _fs_entry(name: str, path: Path) -> dict[str, str]:
    """A labeled, directly-navigable ROOTS entry: ``{"name", "path"}``.

    STANDARD-fs-browse.md's general contract is names-only (the client joins
    ``dir``+``sep``+``name`` for a REAL directory listing), but a ROOTS entry
    (this pack's default output dir, "Home", a `/Volumes` mount, a Windows
    drive) has no single parent directory to join against -- each one is
    independently rooted, so the server hands back its actual absolute path
    directly. A deliberate, documented, additive extension of the base
    schema: any consumer that only reads ``name`` still gets a sensible label.
    """
    return {"name": name, "path": str(path)}


def _platform_root_entries(windows: bool) -> list[dict[str, str]]:
    """STANDARD-fs-browse.md ROOTS listing's platform-specific tail.

    Every existing drive letter on Windows (:func:`_list_windows_drives`,
    labeled by its short drive-letter form, e.g. ``"C:"``), or every mounted
    ``/Volumes`` entry on macOS/other POSIX (:func:`_list_macos_volumes`,
    labeled by its bare volume name, e.g. ``"Macintosh HD"``).
    """
    if windows:
        return [_fs_entry(raw.rstrip("\\"), Path(raw)) for raw in _list_windows_drives()]
    return [_fs_entry(Path(raw).name, Path(raw)) for raw in _list_macos_volumes()]


def _fs_list_roots(context: BridgeContext, *, windows: bool) -> list[dict[str, str]]:
    """The top-level entries for ``dir="ROOTS"`` (STANDARD-fs-browse.md).

    Always: this pack's own default fs/list directory (labeled
    :data:`_FS_LIST_DEFAULT_DIR_LABEL`) and the user's home directory, then
    :func:`_platform_root_entries`'s platform-specific tail -- the standard's
    exact ROOTS ordering ("the pack's default dir first (labeled) ... 'Home',
    then platform roots"). 2026-07-19: previously POSIX's ``ROOTS`` resolved
    straight to a real listing of ``/``; this labeled-roots shape (already
    used on Windows) now applies uniformly on every platform.
    """
    roots = [
        _fs_entry(_FS_LIST_DEFAULT_DIR_LABEL, context.output_dir.resolve()),
        _fs_entry(_FS_LIST_HOME_LABEL, Path.home().resolve()),
    ]
    roots.extend(_platform_root_entries(windows))
    return roots


def _is_unc_share_root(directory: Path) -> bool:
    """True when *directory* is a UNC share root (``\\\\server\\share``).

    Re-parsed with ``PureWindowsPath`` rather than trusting the ambient
    ``Path`` flavor, so this is exercisable on any platform: a share root's
    "drive" is the ``\\\\server\\share`` string itself (no drive LETTER),
    which is exactly what distinguishes it from a real drive root like
    ``C:\\`` — the two need different ``parent`` answers (see below).
    """
    drive = PureWindowsPath(str(directory)).drive
    return bool(drive) and not (len(drive) == 2 and drive[1] == ":")


def _fs_root_parent(directory: Path, *, windows: bool) -> str | None:
    """PROTOCOL.md §7.2 ``parent`` for a *directory* that IS a filesystem root.

    - Windows drive root (``C:\\``): climbs to the drive list (``"ROOTS"``)
      — the fix. Today this reported ``null`` and trapped the user.
    - UNC share root (``\\\\server\\share``): reports ``null`` even on
      Windows — there is no portable way to enumerate a server's other
      shares, so there is nothing to climb to.
    - POSIX root (``/``): reports ``null`` — it has no sibling to climb to.
    """
    if windows and not _is_unc_share_root(directory):
        return ROOTS
    return None


def _reveal_folder(path: Path) -> None:
    """Open *path* in the OS file manager, without blocking the server.

    Split out as a module-level function so tests monkeypatch it rather
    than spawning real Explorer/Finder windows.
    """
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _parse_extensions(raw: str) -> tuple[str, ...]:
    """PROTOCOL.md §7.2 ``ext``: a comma-separated, case-insensitive allowlist.

    Entries may be given with or without the leading dot. An empty/blank
    value means "the default allowlist", never "everything" — the picker
    exists to find timeline XML, and a stray ``ext=`` shouldn't silently
    turn it into a general file browser.
    """
    parts = [part.strip().lower() for part in (raw or "").split(",")]
    cleaned = tuple(f".{p.lstrip('.')}" for p in parts if p.strip(". "))
    return cleaned or DEFAULT_EXTENSIONS


def _register_all(context: BridgeContext, routes: web.RouteTableDef) -> None:
    @routes.get("/cprb/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})

    @routes.get("/cprb/config")
    async def get_config(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "is_local": request_is_loopback(request),
                "output_dir": str(context.output_dir),
                "input_dir": str(context.input_dir),
            }
        )

    @routes.get("/cprb/fs/list")
    async def get_fs_list(request: web.Request) -> web.Response:
        """STANDARD-fs-browse.md's shared cross-plugin contract. Gated by
        :data:`FS_LIST_LOCAL_ONLY` (``True`` for cprb -- PROTOCOL.md §7.1).
        The loopback check runs before ROOTS/absolute-path handling below:
        ROOTS is an exception to the absolute-path requirement, never to
        this one.

        Query params:
            dir: Optional. Empty/omitted resolves to this pack's own default
                directory (``context.output_dir``); the literal ``"ROOTS"``
                (:data:`ROOTS`) returns the virtual top-level listing
                (:func:`_fs_list_roots`). Any other value MUST be an absolute
                path naming an existing, listable directory.
            ext: Optional, comma-separated, case-insensitive
                (:func:`_parse_extensions`) -- defaults to
                :data:`DEFAULT_EXTENSIONS`.

        Returns 200 with ``{"dir", "parent", "sep", "dirs", "files",
        "truncated"}`` (STANDARD-fs-browse.md) -- names-only ``dirs``/
        ``files`` entries for a real directory listing (the client joins
        with ``dir``+``sep``); ROOTS entries additionally carry ``path``
        (:func:`_fs_entry`). 403 when :data:`FS_LIST_LOCAL_ONLY` and the
        caller isn't loopback; 400 for a relative/non-existent/non-directory
        ``dir``.
        """
        if FS_LIST_LOCAL_ONLY and not request_is_loopback(request):
            return error_response(403, "file browsing is host-machine-only — PROTOCOL.md §7.1")
        raw = (request.query.get("dir") or "").strip()
        windows = _is_windows()
        if raw == ROOTS:
            return web.json_response(
                {
                    "dir": ROOTS,
                    "parent": None,
                    "sep": os.sep,
                    "dirs": _fs_list_roots(context, windows=windows),
                    "files": [],
                    "truncated": False,
                }
            )
        directory = Path(raw) if raw else context.output_dir
        if not directory.is_absolute():
            return error_response(400, f"dir must be an absolute path (got {raw!r})")
        extensions = _parse_extensions(request.query.get("ext", ""))
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            return error_response(400, f"could not list {directory}: {exc}")

        dirs: list[dict[str, str]] = []
        files: list[dict[str, object]] = []
        count = 0
        truncated = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if count >= _FS_LIST_MAX_ENTRIES:
                    truncated = True
                    break
                dirs.append({"name": entry.name})
                count += 1
                continue
            if entry.suffix.lower() not in extensions:
                continue
            try:
                stat_result = entry.stat()
            except OSError:
                continue
            if count >= _FS_LIST_MAX_ENTRIES:
                truncated = True
                break
            files.append(
                {"name": entry.name, "size": stat_result.st_size, "mtime": stat_result.st_mtime}
            )
            count += 1

        at_root = directory.parent == directory
        parent = _fs_root_parent(directory, windows=windows) if at_root else str(directory.parent)
        return web.json_response(
            {
                "dir": str(directory),
                "parent": parent,
                "sep": os.sep,
                "dirs": dirs,
                "files": files,
                "truncated": truncated,
            }
        )

    @routes.post("/cprb/open_folder")
    async def post_open_folder(request: web.Request) -> web.Response:
        if not request_is_loopback(request):
            return error_response(
                403, "revealing folders is host-machine-only — PROTOCOL.md §7.1"
            )
        try:
            body = await request.json()
        except Exception:  # malformed body is a client error
            return error_response(400, "body must be JSON")
        raw = str((body or {}).get("path") or "").strip()
        if not raw:
            return error_response(400, "path is required")
        path = Path(raw)
        folder = path if path.is_dir() else path.parent
        if not folder.is_dir():
            return error_response(404, f"no such folder: {folder}")
        try:
            _reveal_folder(folder)
        except Exception as exc:
            logger.exception("cprb: could not reveal %s", folder)
            return error_response(500, f"could not open the folder: {exc}")
        return web.json_response({"ok": True})

    @routes.get("/cprb/timeline_dir")
    async def get_timeline_dir(request: web.Request) -> web.Response:
        sequence_name = request.query.get("sequence_name", "")
        directory = context.resolve_timeline_dir(sequence_name)
        return web.json_response({"dir": str(directory), "exists": directory.is_dir()})


def build_routes(context: BridgeContext) -> web.RouteTableDef:
    """Every cprb route on a fresh table — the tests' entry point."""
    routes = web.RouteTableDef()
    _register_all(context, routes)
    return routes


def register(context: BridgeContext) -> None:
    """Attach routes to the running ComfyUI server (called from
    ``__init__.py``; the only function in the pack that touches PromptServer).

    Registers onto ``PromptServer.instance.routes`` — NOT directly onto the
    aiohttp app — because ComfyUI mirrors exactly that table under the
    ``/api`` prefix at startup (server.py: "Prefix every route with /api"),
    and the frontend's `fetchApi` calls `/api/cprb/...`.
    """
    from server import PromptServer  # ComfyUI's module; import only inside ComfyUI

    _register_all(context, PromptServer.instance.routes)
    logger.info("cprb: routes registered")
