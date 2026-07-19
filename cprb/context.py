"""Dependency-injection seam: everything ComfyUI-specific enters through here.

The rest of ``cprb/`` (timeline writers/parsers, nodes) receives a
:class:`BridgeContext` and never imports ComfyUI modules itself, so the whole
package stays importable — and therefore testable — without ComfyUI. The real
context is built exactly once, in the pack's ``__init__.py``; tests build
fake ones over ``tmp_path``. Same pattern as comfyui-photoshop-bridge's
``cpsb/context.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TIMELINES_DIRNAME = "premiere_timelines"

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_name(name: str, fallback: str = "timeline") -> str:
    """*name* reduced to a safe cross-platform directory/file stem.

    Windows-reserved characters and control codes become ``_``; leading and
    trailing dots/spaces (illegal on Windows) are stripped; an empty result
    falls back to *fallback*.
    """
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip(". ")
    return cleaned or fallback


@dataclass
class BridgeContext:
    """Paths for one running cprb instance.

    Args:
        output_dir: ComfyUI's output directory. Everything cprb writes lives
            under ``<output_dir>/premiere_timelines/<name>/`` (PROTOCOL.md
            §2) so results appear in the standard output tree the user
            already knows.
        input_dir: ComfyUI's input directory (upload-relative reads).
    """

    output_dir: Path
    input_dir: Path

    def resolve_timeline_dir(self, sequence_name: str) -> Path:
        """``<output>/premiere_timelines/<sanitized name>/`` — computed, NOT
        created.

        Split out from :meth:`timeline_dir` because PROTOCOL.md §7.2's
        ``timeline_dir`` route reports whether that folder ``exists`` yet:
        asking the question must not answer it by creating the folder.
        """
        return self.output_dir / TIMELINES_DIRNAME / sanitize_name(sequence_name)

    def timeline_dir(self, sequence_name: str) -> Path:
        """``<output>/premiere_timelines/<sanitized name>/``, created."""
        directory = self.resolve_timeline_dir(sequence_name)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
