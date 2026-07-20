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


def output_dir_override_is_valid(output_dir: str) -> bool:
    """True iff *output_dir* is non-empty (after stripping) AND absolute.

    PROTOCOL.md §3.2's bar for :meth:`BridgeContext.resolve_timeline_dir` to
    honor a ``PremiereSaveTimeline.output_dir`` override instead of falling
    back to the default output tree. Exported (not a private ``_``-prefixed
    name) because :func:`output_dir_override_is_rejected` -- and, through
    it, :meth:`~cprb.nodes_save.PremiereSaveTimeline.execute`'s own
    "should I warn about this" check -- shares this exact predicate, so the
    two can never disagree about what counts as acceptable. Platform-
    correct for free: ``Path.is_absolute()`` is evaluated with whatever
    ``Path`` flavor is ambient on the machine actually running this code, so
    a Windows-style ``C:\\...`` override is absolute on a Windows ComfyUI
    host and (correctly) NOT absolute anywhere else, with no extra
    ``os.name`` branching needed here.
    """
    stripped = (output_dir or "").strip()
    return bool(stripped) and Path(stripped).is_absolute()


def output_dir_override_is_rejected(output_dir: str) -> bool:
    """True iff *output_dir* was GIVEN (non-empty after stripping) but is
    NOT :func:`output_dir_override_is_valid` -- PROTOCOL.md §3.2's "reject a
    non-absolute output_dir cleanly" case, which
    :class:`~cprb.nodes_save.PremiereSaveTimeline` warns about (server log +
    its own UI summary text) before falling back to the default output
    folder. False for an empty/``None`` override (nothing was given -- the
    ordinary default-output-folder case, never worth a warning) and false
    for a valid one.
    """
    return bool((output_dir or "").strip()) and not output_dir_override_is_valid(output_dir)


@dataclass
class BridgeContext:
    """Paths for one running cprb instance.

    Args:
        output_dir: ComfyUI's output directory. Everything cprb writes lives
            under ``<output_dir>/premiere_timelines/<name>/`` (PROTOCOL.md
            §2) BY DEFAULT -- ``PremiereSaveTimeline``'s optional
            ``output_dir`` widget (§3.2) can redirect the base; see
            :meth:`resolve_timeline_dir`.
        input_dir: ComfyUI's input directory (upload-relative reads).
    """

    output_dir: Path
    input_dir: Path

    def resolve_timeline_dir(self, sequence_name: str, output_dir: str = "") -> Path:
        """``<base>/<sanitized name>/`` — computed, NOT created.

        Split out from :meth:`timeline_dir` because PROTOCOL.md §7.2's
        ``timeline_dir`` route reports whether that folder ``exists`` yet:
        asking the question must not answer it by creating the folder. That
        same route also accepts *output_dir* (query param) so it resolves
        the IDENTICAL effective path :meth:`timeline_dir` would actually
        write to -- this method is the one place both agree.

        PROTOCOL.md §3.2's ``PremiereSaveTimeline.output_dir`` override: when
        *output_dir* :func:`output_dir_override_is_valid` (non-empty,
        absolute), it REPLACES ``self.output_dir`` as the base directly --
        ``<output_dir>/<sanitized name>/``, no ``premiere_timelines`` middle
        folder, since the override already IS the folder the caller chose
        to hold this one sequence's own subfolder (never write straight
        into ITS root, but never re-impose cprb's own default tree shape on
        top of it either). Anything else -- empty, or given but NOT
        absolute -- resolves exactly like omitting it entirely:
        ``self.output_dir/premiere_timelines/<sanitized name>/``. A
        REJECTED (non-empty, non-absolute) override is silently absorbed
        HERE -- warning about it is
        :meth:`~cprb.nodes_save.PremiereSaveTimeline.execute`'s job (via
        :func:`output_dir_override_is_rejected`), never this pure path
        function's.
        """
        if output_dir_override_is_valid(output_dir):
            return Path(output_dir.strip()) / sanitize_name(sequence_name)
        return self.output_dir / TIMELINES_DIRNAME / sanitize_name(sequence_name)

    def timeline_dir(self, sequence_name: str, output_dir: str = "") -> Path:
        """:meth:`resolve_timeline_dir`, created."""
        directory = self.resolve_timeline_dir(sequence_name, output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
