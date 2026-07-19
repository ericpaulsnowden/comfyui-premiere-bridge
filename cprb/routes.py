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
import subprocess
import sys
from pathlib import Path

from aiohttp import web

from .context import BridgeContext
from .version import __version__

logger = logging.getLogger("cprb")

#: PROTOCOL.md §7.2 — the picker's default extension allowlist (Load
#: Premiere Timeline reads Premiere's Final Cut Pro XML export).
DEFAULT_EXTENSIONS = (".xml",)


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
        if not request_is_loopback(request):
            return error_response(403, "file browsing is host-machine-only — PROTOCOL.md §7.1")
        raw = (request.query.get("dir") or "").strip()
        directory = Path(raw) if raw else context.output_dir
        if not directory.is_absolute():
            return error_response(400, f"dir must be an absolute path (got {raw!r})")
        extensions = _parse_extensions(request.query.get("ext", ""))
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            return error_response(400, f"could not list {directory}: {exc}")
        return web.json_response(
            {
                "dir": str(directory),
                "parent": str(directory.parent) if directory.parent != directory else None,
                "dirs": [p.name for p in entries if p.is_dir()],
                "files": [
                    p.name
                    for p in entries
                    if p.is_file() and p.suffix.lower() in extensions
                ],
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
