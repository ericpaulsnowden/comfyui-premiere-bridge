"""Cross-checks the PLUGIN's frame filename generator against the SERVER's
ownership regex (RELEASE-REVIEW 2026-09-13 finding #1 / PROTOCOL.md §11.1).

This repo has no JS test harness (README's own admission), so rather than
hand-transcribe ``cprbUniqueFrameName``/``cprbSanitizeStem`` into Python and
risk the two drifting apart, this loads the REAL
``premiere_plugin/export_source.js`` source into a Node ``vm`` context via a
tiny driver script — no npm, no ``node_modules``, nothing to install. Every
top-level statement in that file is a ``function``/``const``/``let``
declaration (no eager calls), so evaluating the whole file is safe and needs
no UXP/Premiere stubs at all for the two functions this test calls.

Skips cleanly wherever ``node`` is not on ``PATH`` — this must never be the
thing that fails CI on a machine without Node, only a check that runs when
it can.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cprb import routes as cprb_routes

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT_SOURCE_JS = REPO_ROOT / "premiere_plugin" / "export_source.js"

#: Loads export_source.js into a fresh sandbox context, then calls
#: cprbSanitizeStem + cprbUniqueFrameName for each name passed on argv,
#: printing the resulting filenames as a JSON array. No require()/import of
#: the plugin file itself -- it is a plain UXP <script>, not a CJS/ESM module.
_DRIVER_JS = r"""
const vm = require('vm');
const fs = require('fs');

const src = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'export_source.js' });

if (typeof sandbox.cprbUniqueFrameName !== 'function'
    || typeof sandbox.cprbSanitizeStem !== 'function') {
  throw new Error('export_source.js did not define the expected globals');
}

const rawNames = JSON.parse(process.argv[3]);
const out = rawNames.map((raw) => sandbox.cprbUniqueFrameName(sandbox.cprbSanitizeStem(raw)));
process.stdout.write(JSON.stringify(out));
"""

#: Sequence names chosen to exercise cprbSanitizeStem's rules: spaces,
#: unicode, Windows-illegal characters (colons especially -- PROTOCOL.md
#: §11.7 says a colon makes the export call return false outright), an
#: empty name (falls back to "frame"), and a name that is already the
#: bridge's own prefix (must not double up or otherwise confuse matching).
SEQUENCE_NAMES = [
    "My Sequence",
    "séquence #1 (draft) — v2",
    "Timeline_001",
    'a/b\\c:d*e?f"g<h>i|j',
    "   leading and trailing spaces   ",
    "",
    "cprb_already_prefixed_by_coincidence",
]


@pytest.fixture(scope="module")
def node_path() -> str:
    path = shutil.which("node")
    if path is None:
        pytest.skip("node is not on PATH -- JS name-format check skipped")
    return path


def test_cprb_unique_frame_name_matches_the_python_ownership_regex(
    node_path: str, tmp_path: Path
) -> None:
    """Every name the plugin generates must be recognized as OWNED server-side.

    This is the seam the actual data-loss bug lived in: the plugin decides
    the filename, the server decides what it is allowed to delete, and
    nothing before this fix connected the two. If either side's pattern
    ever drifts, this test -- not a production handshake -- is where it
    should be caught.
    """
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER_JS, encoding="utf-8")

    result = subprocess.run(
        [node_path, str(driver), str(EXPORT_SOURCE_JS), json.dumps(SEQUENCE_NAMES)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"node driver failed:\n{result.stderr}"
    names = json.loads(result.stdout)

    assert len(names) == len(SEQUENCE_NAMES)
    assert len(set(names)) == len(names)  # cprbFrameSeq keeps every name unique

    for raw, name in zip(SEQUENCE_NAMES, names, strict=True):
        assert name.endswith(".png"), (raw, name)
        assert name.startswith(cprb_routes.FRAME_OWNERSHIP_PREFIX), (raw, name)
        assert cprb_routes._OWNED_FRAME_NAME_RE.match(name), (raw, name)
        # A prefixed name must never ALSO read as "legacy" -- the two pools
        # are pruned under different rules in a custom CPRB_FRAMES_DIR.
        assert not cprb_routes._LEGACY_FRAME_NAME_RE.match(name), (raw, name)
        # No character cprbSanitizeStem is documented to strip should have
        # survived (spaces, unicode, the Windows-illegal set).
        assert " " not in name
        assert ":" not in name
