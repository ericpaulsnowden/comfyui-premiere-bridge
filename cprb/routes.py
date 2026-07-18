"""HTTP routes (PROTOCOL.md §7): Tier 1 exposes only the version row."""

from __future__ import annotations

import logging

from aiohttp import web

from .context import BridgeContext
from .version import __version__

logger = logging.getLogger("cprb")


def build_routes(_context: BridgeContext) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/cprb/version")
    async def get_version(_request: web.Request) -> web.Response:
        return web.json_response({"version": __version__})

    return routes


def register(context: BridgeContext) -> None:
    """Attach routes to the running ComfyUI server (called from
    ``__init__.py``; the only line in the pack that touches PromptServer)."""
    from server import PromptServer  # ComfyUI's module; import only inside ComfyUI

    PromptServer.instance.app.add_routes(build_routes(context))
    logger.info("cprb: routes registered")
