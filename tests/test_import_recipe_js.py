"""Which project item does the Premiere panel treat as "the import"?
(release review 2026-09-13 item 9; PROTOCOL.md §10.3 "Which item is the
import").

``importFiles`` returns a bare boolean, so ``premiere_plugin/import_recipe.js``
has to FIND the item it just created. It used to identify items by MEDIA
PATH, so when an older item already referenced the same file (the owner's
case: an untrimmed source clip that Send to Premiere links in place, re-sent
after an earlier send) the OLD item won and tagging, colouring and the
playhead insert all hit it -- old in/out trim included. The fix tracks
``ProjectItem.getId()`` across the import and prefers the item absent from
the pre-import snapshot, in the bin lookup AND both fallback lookups (project
root, capped tree walk).

This loads the REAL ``helpers.js`` + ``import_recipe.js`` into a Node ``vm``
context (same harness idea as the review branch's export_source test: no
npm, no node_modules) against a small fake of the ``premierepro`` module.
The fake mints a FRESH wrapper object on every access, so nothing here can
pass by accident on JS object identity -- only the documented id is stable.
Each scenario gets its own context, so module-level session state
(bin-create guard, ingest probe, identity-mode log) never leaks between
scenarios. Skips cleanly where ``node`` is not on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS_JS = REPO_ROOT / "premiere_plugin" / "helpers.js"
IMPORT_RECIPE_JS = REPO_ROOT / "premiere_plugin" / "import_recipe.js"

_DRIVER_JS = r"""
'use strict';
const vm = require('vm');
const fs = require('fs');

const helpersSrc = fs.readFileSync(process.argv[2], 'utf8');
const recipeSrc = fs.readFileSync(process.argv[3], 'utf8');

const P = 'C:\\media\\source_untrimmed.mp4';
const P_OTHER = 'C:\\media\\something_else.mp4';
const BIN = 'ComfyUI Results';

/** Fresh panel context: helpers.js then import_recipe.js, as index.html
 * loads them, with a capturing #log element and an instant setTimeout (so
 * findClipInBin's 0/500/1500/3000 ms retry rounds cost nothing). */
function makeContext() {
  const lines = [];
  const logEl = {
    children: [],
    appendChild(line) { this.children.push(line); lines.push(String(line.textContent)); },
    removeChild() { this.children.shift(); },
    get firstChild() { return this.children[0]; },
    scrollTop: 0,
    scrollHeight: 0,
  };
  const sandbox = {
    console,
    document: {
      getElementById: (id) => (id === 'log' ? logEl : null),
      createElement: () => ({ className: '', textContent: '' }),
    },
    setTimeout: (fn) => setImmediate(fn),
  };
  vm.createContext(sandbox);
  vm.runInContext(helpersSrc, sandbox, { filename: 'helpers.js' });
  vm.runInContext(recipeSrc, sandbox, { filename: 'import_recipe.js' });
  return { sb: sandbox, lines };
}

/** A tiny premierepro fake. `spec.tree` is the root's children; a folder
 * entry is {folder: true, id, name, children}, a clip {id, path, colorLabel}.
 * spec.getId: 'ok' (default) | 'absent' | 'constant' (same id for every item).
 * spec.importLands: where a TARGETED 3-arg import lands ('bin' = the target
 * bin, a folder id, 'root', or 'none' = no new item); spec.threeArg === false
 * makes the 3-arg call fail; spec.untargetedLands: where the 2-arg import
 * lands (default 'root'). spec.move: undefined | 'works' | 'noop'. */
function makePremiere(spec) {
  let seq = 0;
  const nodes = {};
  const mk = (kind, props) => ({
    kind,
    id: props.id || `auto-${++seq}`,
    name: props.name || props.id || '',
    path: props.path || '',
    children: [],
    parent: null,
    colorLabel: props.colorLabel === undefined ? null : props.colorLabel,
  });
  const build = (parent, specs) => {
    for (const s of specs) {
      const n = mk(s.folder ? 'folder' : 'clip', s);
      n.parent = parent;
      parent.children.push(n);
      nodes[n.id] = n;
      if (s.folder) build(n, s.children || []);
    }
  };
  const root = mk('folder', { id: 'root' });
  build(root, spec.tree);

  const idOf = (n) => (spec.getId === 'constant' ? 'same-for-everyone' : n.id);
  // Every access mints a NEW wrapper: no stable JS object identity.
  const rawOf = (n) => {
    const raw = { _node: n, name: n.name };
    if (spec.getId !== 'absent') raw.getId = () => idOf(n);
    return raw;
  };
  const clipOf = (n) => ({
    _node: n,
    name: n.name,
    getMediaFilePath: async () => n.path,
    createSetColorLabelAction: (index) => () => { n.colorLabel = index; },
  });
  const moveInto = (target, n) => {
    n.parent.children.splice(n.parent.children.indexOf(n), 1);
    target.children.push(n);
    n.parent = target;
  };
  const folderOf = (n) => {
    const f = { _node: n, name: n.name, getItems: async () => n.children.map(rawOf) };
    if (spec.move) {
      f.createMoveItemAction = (item) => () => {
        if (spec.move === 'works') moveInto(n, item._node);
      };
    }
    return f;
  };
  const land = (where) => {
    if (where === 'none') return;
    const target = where === 'root' ? root : nodes[where];
    const n = mk('clip', { id: 'fresh', path: P });
    n.parent = target;
    target.children.push(n);
    nodes.fresh = n;
  };
  const placed = [];
  const track = { getTrackItems: async () => placed.slice() };
  const sequence = {
    getPlayerPosition: async () => ({ ticks: '0' }),
    getVideoTrackCount: async () => 1,
    getVideoTrack: async () => track,
  };
  const project = {
    getRootItem: async () => folderOf(root),
    getActiveSequence: async () => sequence,
    importFiles: async (paths, suppressUI, targetBin) => {
      if (targetBin !== undefined) {
        if (spec.threeArg === false) return false;
        const where = spec.importLands || 'bin';
        land(where === 'bin' ? targetBin._node.id : where);
        return true;
      }
      land(spec.untargetedLands || 'root');
      return true;
    },
    lockedAccess: (fn) => fn(),
    executeTransaction: (cb) => { cb({ addAction: (action) => action() }); return true; },
  };
  const pr = {
    ProjectItem: { cast: (x) => (x && x._node ? rawOf(x._node) : null) },
    ClipProjectItem: {
      cast: (x) => (x && x._node && x._node.kind === 'clip' ? clipOf(x._node) : null),
    },
    FolderItem: {
      cast: (x) => (x && x._node && x._node.kind === 'folder' ? folderOf(x._node) : null),
    },
    Constants: {
      ProjectItemColorLabel: { VIOLET: 0, IRIS: 1, LAVENDER: 3 },
      TrackItemType: { CLIP: 1 },
    },
    SequenceEditor: {
      getEditor: async () => ({
        createOverwriteItemAction: (raw) => () => { placed.push(raw._node.id); },
      }),
    },
    Project: { getActiveProject: async () => project },
  };
  return { pr, project, nodes, placed, binOf: (id) => folderOf(nodes[id]) };
}

const idOfHandle = (h) => (h && h._node ? h._node.id : null);

async function viaImportIntoBin(spec, binId) {
  const { sb, lines } = makeContext();
  const { pr, project, nodes, binOf } = makePremiere(spec);
  const bin = binId ? binOf(binId) : null;
  const out = await sb.importIntoBin(pr, project, bin, P);
  return {
    where: out.where,
    item: idOfHandle(out.item),
    raw: idOfHandle(out.raw),
    fresh_parent: nodes.fresh ? nodes.fresh.parent.id : null,
    lines,
  };
}

const SCENARIOS = {
  // The owner's case end to end: the bin already holds an earlier import of
  // the same file (with its own colour), the user's original sits at the
  // root, and the re-send must colour + insert the FRESH item only.
  async owner_resend_end_to_end() {
    const { sb, lines } = makeContext();
    const { pr, nodes, placed } = makePremiere({
      tree: [
        { id: 'orig', path: P },
        { folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P, colorLabel: 1 }] },
      ],
    });
    await sb.handlePrResult(pr, {
      type: 'pr_result', path: P, label: 'retake', bin_name: BIN,
      color_label: 'lavender', insert_at_playhead: true, sent_ts: 1,
    });
    return {
      colors: {
        fresh: nodes.fresh.colorLabel,
        old: nodes.old.colorLabel,
        orig: nodes.orig.colorLabel,
      },
      placed,
      lines,
    };
  },

  // findClipInBin itself: the fresh item is selected AND marked not-preexisting.
  async bin_lookup_marks_fresh_not_preexisting() {
    const { sb, lines } = makeContext();
    const { pr, project, binOf } = makePremiere({
      tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P }] }],
    });
    const bin = binOf('bin');
    const before = await sb.cprbSnapshotBeforeImport(pr, project, bin);
    await project.importFiles([P], true, bin);
    const found = await sb.findClipInBin(pr, bin, P, before, [0]);
    return {
      item: idOfHandle(found.clip),
      raw: idOfHandle(found.raw),
      preexisting: found.preexisting,
      lines,
    };
  },

  // Fallback 1 (project root): the user's original is enumerated first.
  root_fallback_prefers_fresh: () => viaImportIntoBin({
    tree: [{ id: 'orig', path: P }, { id: 'unrelated', path: P_OTHER }],
  }, null),

  // Fallback 2 (collectClips tree walk): an old same-file item in an earlier folder.
  tree_fallback_prefers_fresh: () => viaImportIntoBin({
    tree: [
      { folder: true, id: 'F', children: [{ id: 'old', path: P }] },
      { folder: true, id: 'G', children: [] },
    ],
    untargetedLands: 'G',
  }, null),

  // A NEW item deep in the tree beats a PRE-EXISTING one at the root.
  tree_fresh_beats_root_old: () => viaImportIntoBin({
    tree: [{ id: 'orig', path: P }, { folder: true, id: 'G', children: [] }],
    untargetedLands: 'G',
  }, null),

  // Bin holds an old same-file item, the 3-arg import fails and the 2-arg one
  // lands at the root: the fresh root item beats the old bin item.
  bin_old_but_fresh_at_root: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P }] }],
    threeArg: false,
  }, 'bin'),

  // ...and a move that claims success but moves nothing is NOT confirmed by
  // the old same-file item already sitting in the bin.
  move_noop_not_confirmed_by_old_item: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P }] }],
    threeArg: false,
    move: 'noop',
  }, 'bin'),

  move_that_works_is_confirmed: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P }] }],
    threeArg: false,
    move: 'works',
  }, 'bin'),

  // Collision-free cases: unchanged.
  collision_free_empty_bin: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [] }],
  }, 'bin'),

  collision_free_other_file_in_bin: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'other', path: P_OTHER }] }],
  }, 'bin'),

  // importFiles says true but no new item appears (dedupe): the existing
  // item is used, and the log says so.
  no_new_item_uses_existing: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P }] }],
    importLands: 'none',
  }, 'bin'),

  // Degraded builds: no getId() -> the pre-fix path rule, announced.
  no_getid_falls_back_to_path_rule: () => viaImportIntoBin({
    tree: [{ folder: true, id: 'bin', name: BIN, children: [{ id: 'old', path: P }] }],
    getId: 'absent',
  }, 'bin'),

  // A getId() that is not unique is not an identity -> same fallback.
  non_unique_getid_falls_back: () => viaImportIntoBin({
    tree: [{
      folder: true, id: 'bin', name: BIN,
      children: [{ id: 'old', path: P }, { id: 'other', path: P_OTHER }],
    }],
    getId: 'constant',
  }, 'bin'),
};

(async () => {
  const results = {};
  for (const [name, run] of Object.entries(SCENARIOS)) {
    try {
      results[name] = await run();
    } catch (error) {
      results[name] = { error: String(error && error.stack ? error.stack : error) };
    }
  }
  process.stdout.write(JSON.stringify(results));
})();
"""


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH -- import_recipe.js identity checks skipped")
    driver = tmp_path_factory.mktemp("import_recipe_js") / "driver.js"
    driver.write_text(_DRIVER_JS, encoding="utf-8")
    proc = subprocess.run(
        [node, str(driver), str(HELPERS_JS), str(IMPORT_RECIPE_JS)],
        capture_output=True,
        # node writes UTF-8; text=True alone decodes with the locale codec
        # (cp1252 on Windows), which would garble any non-ASCII and can raise.
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"node driver failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _scenario(results: dict, name: str) -> dict:
    data = results[name]
    assert "error" not in data, data.get("error")
    return data


def test_owner_resend_colours_and_inserts_the_fresh_item(results) -> None:
    data = _scenario(results, "owner_resend_end_to_end")
    # LAVENDER (3) lands on the fresh import only; the earlier import keeps
    # its own label (IRIS, 1) and the user's original stays untouched.
    assert data["colors"] == {"fresh": 3, "old": 1, "orig": None}
    assert data["placed"] == ["fresh"]
    assert any("picks the freshly imported item" in line for line in data["lines"])


def test_bin_lookup_selects_fresh_item_and_marks_it_not_preexisting(results) -> None:
    data = _scenario(results, "bin_lookup_marks_fresh_not_preexisting")
    assert data["item"] == "fresh"
    assert data["raw"] == "fresh"
    assert data["preexisting"] is False


def test_root_fallback_prefers_the_fresh_item(results) -> None:
    data = _scenario(results, "root_fallback_prefers_fresh")
    assert (data["where"], data["item"], data["raw"]) == ("root", "fresh", "fresh")


def test_tree_fallback_prefers_the_fresh_item(results) -> None:
    data = _scenario(results, "tree_fallback_prefers_fresh")
    # collectClips yields cast handles only, so there is no raw item here.
    assert (data["where"], data["item"], data["raw"]) == ("root", "fresh", None)


def test_fresh_item_in_tree_beats_preexisting_item_at_root(results) -> None:
    data = _scenario(results, "tree_fresh_beats_root_old")
    assert data["item"] == "fresh"


def test_fresh_item_at_root_beats_old_same_file_item_in_bin(results) -> None:
    data = _scenario(results, "bin_old_but_fresh_at_root")
    assert (data["where"], data["item"]) == ("root", "fresh")


def test_noop_move_is_not_confirmed_by_an_old_same_file_item(results) -> None:
    data = _scenario(results, "move_noop_not_confirmed_by_old_item")
    assert (data["where"], data["item"], data["fresh_parent"]) == ("root", "fresh", "root")


def test_working_move_is_confirmed(results) -> None:
    data = _scenario(results, "move_that_works_is_confirmed")
    assert (data["where"], data["item"], data["fresh_parent"]) == ("moved", "fresh", "bin")


@pytest.mark.parametrize(
    "name", ["collision_free_empty_bin", "collision_free_other_file_in_bin"]
)
def test_collision_free_imports_are_unchanged(results, name: str) -> None:
    data = _scenario(results, name)
    assert (data["where"], data["item"], data["raw"]) == ("bin", "fresh", "fresh")
    assert not any("no new item" in line for line in data["lines"])


def test_no_new_item_falls_back_to_the_existing_one_and_says_so(results) -> None:
    data = _scenario(results, "no_new_item_uses_existing")
    assert (data["where"], data["item"]) == ("bin", "old")
    assert any("no new item for this file appeared" in line for line in data["lines"])


def test_without_getid_the_pre_fix_path_rule_applies_and_is_announced(results) -> None:
    # Documents the DEGRADED mode, not a desired outcome: with no readable
    # id the panel cannot tell the two items apart and keeps the old
    # first-match behaviour -- but it now says so in the log.
    data = _scenario(results, "no_getid_falls_back_to_path_rule")
    assert (data["where"], data["item"]) == ("bin", "old")
    assert any("falling back to media-path matching" in line for line in data["lines"])
    assert any("may be the OLDER item" in line for line in data["lines"])


def test_non_unique_getid_is_not_trusted_as_identity(results) -> None:
    data = _scenario(results, "non_unique_getid_falls_back")
    assert any("is not unique" in line for line in data["lines"])
