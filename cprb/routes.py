"""HTTP routes (PROTOCOL.md §7): Tier 1 exposes only the version row."""

from __future__ import annotations

import logging

from aiohttp import web

from .context import BridgeContext
from .version import __version__

logger = logging.getLogger("cprb")


def _register_all(_context: BridgeContext, routes: web.RouteTableDef) -> None:
    @routes.get("/cprb/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})


def build_routes(context: BridgeContext) -> web.RouteTableDef:
    routes = web.RouteTableDef()
    _register_all(context, routes)
    return routes


def register(context: BridgeContext) -> None:
    """Attach routes to the running ComfyUI server (called from
    ``__init__.py``; the only function in the pack that touches PromptServer).

    Registers onto ``PromptServer.instance.routes`` — NOT directly onto the
    aiohttp app — because ComfyUI mirrors exactly that table under the
    ``/api`` prefix at startup (server.py: "Prefix every route with /api"),
    and the frontend's ``api.fetchApi`` calls ``/api/cprb/...``.
    """
    from server import PromptServer  # ComfyUI's module; import only inside ComfyUI

    _register_all(context, PromptServer.instance.routes)
    logger.info("cprb: routes registered")
