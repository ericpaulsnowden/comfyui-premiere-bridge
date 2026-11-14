"""web/cprb/graph_nodes.js must reach nodes inside ComfyUI subgraphs.

``premiere_source.js`` routes Premiere's returned path to every matching source
node; a node inside a subgraph used to be missed (flat ``app.graph._nodes``
walk) and the user got a bogus "add a node" warning. The helper is import-free,
so this loads the REAL file under Node via a data: URL (no npm, nothing to
install). Skips when ``node`` is not on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

GRAPH_NODES_JS = Path(__file__).resolve().parent.parent / "web" / "cprb" / "graph_nodes.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

_DRIVER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const url = 'data:text/javascript;base64,' + Buffer.from(src).toString('base64');
import(url).then((m) => {
  const shared = { _nodes: [{ id: 'shared' }] };
  const graph = {
    _nodes: [
      { id: 1 },
      { id: 2, subgraph: { _nodes: [{ id: 'a' }, { id: 'b', subgraph: { nodes: [{ id: 'deep' }] } }] } },
      { id: 3, subgraph: shared },
      { id: 4, subgraph: shared },   // same definition instanced twice
    ],
  };
  process.stdout.write(JSON.stringify({
    ids: m.collectGraphNodes(graph).map((n) => String(n.id)),
    legacy: m.collectGraphNodes({ nodes: [{ id: 9 }] }).map((n) => String(n.id)),
    empty: m.collectGraphNodes(null).length,
  }));
});
"""


def test_collect_graph_nodes_recurses_into_subgraphs() -> None:
    proc = subprocess.run(
        ["node", "-e", _DRIVER, str(GRAPH_NODES_JS)],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert {"1", "2", "a", "b", "deep"} <= set(out["ids"])
    assert out["ids"].count("shared") == 1
    assert out["legacy"] == ["9"]
    assert out["empty"] == 0
