"""ComfyUI entry point for comfyui-premiere-bridge.

This is the only file in the pack that touches ComfyUI's own modules
(``folder_paths``, ``server``). It builds the real
:class:`~cprb.context.BridgeContext`, registers the version route and the
nodes, and exposes the standard ``NODE_CLASS_MAPPINGS`` / ``WEB_DIRECTORY``
attributes ComfyUI's loader looks for. Everything under ``cprb/`` stays
importable (and tested) without ComfyUI — see ``cprb/context.py``.

Feature modules are imported DEFENSIVELY: a broken or not-yet-present
feature logs loudly and is skipped, and the rest of the pack still loads.
"""

import importlib
import logging
from pathlib import Path

try:
    from .cprb import routes as _routes
    from .cprb.context import BridgeContext
    from .cprb.version import __version__

    _PACKAGE_PREFIX = f"{__name__}.cprb"
except ImportError:
    # Imported without package context (pytest rootdir setups etc.); ComfyUI
    # itself always loads this file as a package via the branch above.
    from cprb import routes as _routes
    from cprb.context import BridgeContext
    from cprb.version import __version__

    _PACKAGE_PREFIX = "cprb"

logger = logging.getLogger("cprb")


def _build_context() -> BridgeContext:
    import folder_paths  # ComfyUI's own module; only importable inside ComfyUI
    from server import PromptServer

    def _send_event(event: str, payload: dict) -> None:
        # send_sync is thread-safe (call_soon_threadsafe internally), so any
        # thread may emit frontend events through it -- the same precedent
        # comfyui-photoshop-bridge's __init__.py cites for its own emitter.
        # PROTOCOL.md §10.4: today's only sender is the websocket layer
        # relaying the plugin's `export_ready` as `cprb.export_ready`.
        PromptServer.instance.send_sync(event, payload)

    return BridgeContext(
        output_dir=Path(folder_paths.get_output_directory()),
        input_dir=Path(folder_paths.get_input_directory()),
        send_event=_send_event,
    )


_context = _build_context()
_routes.register(_context)

# Class ids are FROZEN once shipped (PROTOCOL.md §8): saved workflows
# reference nodes by id; renaming one silently breaks every workflow that
# contains it. The "Premiere" prefix avoids collisions with other packs.
_NODE_SPECS = [
    ("nodes_save", "PremiereSaveTimeline", "Save Premiere Timeline"),
    ("nodes_load", "PremiereLoadTimeline", "Load Premiere Timeline"),
    ("nodes_load", "PremiereGetShot", "Get Shot"),
    ("nodes_load", "PremiereIterateShots", "Iterate Shots"),
    ("nodes_load", "PremiereShotFrame", "Get Shot Frame"),
    ("nodes_send", "PremiereSendResult", "Send to Premiere"),
]

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_configured_modules = set()
for _module_name, _class_id, _display in _NODE_SPECS:
    try:
        _module = importlib.import_module(f"{_PACKAGE_PREFIX}.{_module_name}")
        if _module_name not in _configured_modules:
            _module.set_context(_context)
            _configured_modules.add(_module_name)
        NODE_CLASS_MAPPINGS[_class_id] = getattr(_module, _class_id)
        NODE_DISPLAY_NAME_MAPPINGS[_class_id] = _display
    except Exception:  # skip the feature, keep the pack alive
        logger.exception("cprb: feature module %s failed to load", _module_name)

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY", "__version__"]

logger.info("cprb v%s loaded (%d nodes)", __version__, len(NODE_CLASS_MAPPINGS))
