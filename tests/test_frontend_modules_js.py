"""The ComfyUI frontend modules load together and share one set of helpers.

``web/cprb/*.js`` import ComfyUI's own ``scripts/app.js`` / ``scripts/api.js``,
so they cannot be imported from this repo as they sit. This test copies ``web/``
into a temp tree shaped like a ComfyUI install (``extensions/<pack>/`` beside
``scripts/``) with two stub scripts, then imports the real entry point under
Node. That proves every import between the modules resolves -- a helper moved
to ``api.js`` but not imported where it is used fails here, not in a user's
browser -- and exercises the shared helpers through a real ``cprb.send_result``
event. Skips when ``node`` is not on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

_APP_STUB = """
export const app = {
  registered: [],
  toasts: [],
  graph: { _nodes: [] },
  registerExtension(extension) { this.registered.push(extension) },
  extensionManager: { toast: { add(options) { app.toasts.push(options) } } },
}
"""

_API_STUB = """
export const api = {
  listeners: {},
  addEventListener(name, handler) { this.listeners[name] = handler },
  async fetchApi() { throw new Error('no backend in this test') },
}
"""

_DRIVER = r"""
const path = require('path');
const { pathToFileURL } = require('url');
const root = process.argv[1];
const load = (rel) => import(pathToFileURL(path.join(root, rel)).href);
(async () => {
  const { app } = await load('scripts/app.js');
  const { api } = await load('scripts/api.js');
  await load('extensions/pack/cprb.js');
  const shared = await load('extensions/pack/cprb/api.js');

  // setup() subscribes the two relays; initSettings fails (no backend) and
  // must not stop them.
  await app.registered[0].setup();
  api.listeners['cprb.send_result']({
    detail: {
      bin_name: 'Results',
      results: [
        { path: 'C:\\out\\clip one.mp4', pushed: true },
        { path: '/mnt/nas/still.png', pushed: false },
      ],
    },
  });

  const widget = { name: 'path' };
  process.stdout.write(JSON.stringify({
    extensions: app.registered.map((e) => e.name),
    listeners: Object.keys(api.listeners).sort(),
    toasts: app.toasts.map((t) => ({ severity: t.severity, detail: t.detail })),
    classes: [
      shared.nodeClassOf({ comfyClass: 'A', type: 'B' }),
      shared.nodeClassOf({ constructor: { comfyClass: 'C' }, type: 'B' }),
      shared.nodeClassOf({ type: 'B' }),
      shared.nodeClassOf({}),
      shared.nodeClassOf(null),
    ],
    basenames: [
      shared.basename('C:\\a\\b.png'),
      shared.basename('/a/b.png'),
      shared.basename('b.png'),
      shared.basename(''),
    ],
    widgetFound: shared.findWidget({ widgets: [null, { name: 'x' }, widget] }, 'path') === widget,
    widgetMissing: shared.findWidget({}, 'path') === undefined,
  }));
})().catch((error) => { console.error(error); process.exit(1); });
"""


@pytest.fixture(scope="module")
def loaded(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("comfy_web")
    shutil.copytree(WEB_DIR, root / "extensions" / "pack")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "app.js").write_text(_APP_STUB, encoding="utf-8")
    (scripts / "api.js").write_text(_API_STUB, encoding="utf-8")
    # The copied files are ES modules; tell Node so without touching the repo.
    (root / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    proc = subprocess.run(
        ["node", "-e", _DRIVER, str(root)],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_every_module_loads_and_the_extension_registers(loaded: dict) -> None:
    assert loaded["extensions"] == ["cprb.PremiereBridge"]
    assert loaded["listeners"] == ["cprb.export_ready", "cprb.send_result"]


def test_send_result_toasts_name_the_file_and_keep_the_failed_path(loaded: dict) -> None:
    by_severity = {toast["severity"]: toast["detail"] for toast in loaded["toasts"]}
    assert set(by_severity) == {"info", "warn"}
    # The confirmation fits on one line: file name only, Windows separators too.
    assert "clip one.mp4" in by_severity["info"]
    assert "C:\\out" not in by_severity["info"]
    # The failure is a to-do, so it keeps the full path to import by hand.
    assert "/mnt/nas/still.png" in by_severity["warn"]


def test_node_class_prefers_comfy_class_then_falls_back_to_type(loaded: dict) -> None:
    assert loaded["classes"] == ["A", "C", "B", None, None]


def test_basename_handles_both_separators(loaded: dict) -> None:
    assert loaded["basenames"] == ["b.png", "b.png", "b.png", ""]


def test_find_widget_skips_holes_and_reports_absence(loaded: dict) -> None:
    assert loaded["widgetFound"] is True
    assert loaded["widgetMissing"] is True
