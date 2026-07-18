"""Shared fixtures: a fully fake BridgeContext wired to tmp_path directories.

No ComfyUI anywhere: the context-injection pattern (``cprb/context.py``)
means every test gets real behavior against throwaway directories. Same
approach as comfyui-photoshop-bridge's test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cprb.context import BridgeContext


@pytest.fixture
def context(tmp_path: Path) -> BridgeContext:
    """A BridgeContext over fresh tmp_path output/input dirs."""
    output_dir = tmp_path / "output"
    input_dir = tmp_path / "input"
    output_dir.mkdir()
    input_dir.mkdir()
    return BridgeContext(output_dir=output_dir, input_dir=input_dir)
