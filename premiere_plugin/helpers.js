/*
 * helpers.js -- shared panel globals for the M1 panel. Buildless plain JS on
 * purpose, same posture as the Photoshop plugin: no bundler, files at plugin
 * ROOT, and index.html's <script> tag order IS the module system
 * (helpers -> connection -> import_recipe -> main -> layout). Everything
 * here is a top-level `function` declaration so every later script sees it
 * as a global.
 *
 * The Premiere-facing utilities (cleanImportPath / samePath / collectClips /
 * ppro) are carried over verbatim-in-behavior from the M0 spike panel --
 * they are the helpers the S6 round proved live (docs/SPIKES.md).
 */
'use strict';

/** Upper bound on in-panel activity-log lines. The panel can sit connected
 * through a whole workday of pr_results; the DOM must not grow unbounded.
 * Oldest lines drop first (the full history is never load-bearing -- the
 * "Copy log" button exists for capturing a specific run). */
const CPRB_MAX_LOG_LINES = 500;

/** Appends one timestamped line to the activity log. `cls` is an optional
 * CSS class: 'ok' (green), 'bad' (red), 'dim' (detail/debug grey). Looked up
 * lazily and null-guarded so a logging call can never take the panel down,
 * even if it fires before/without the DOM. */
function log(msg, cls) {
  const el = document.getElementById('log');
  if (!el) return;
  const line = document.createElement('div');
  if (cls) line.className = cls;
  const ts = new Date().toISOString().slice(11, 19);
  line.textContent = `[${ts}] ${msg}`;
  el.appendChild(line);
  while (el.children.length > CPRB_MAX_LOG_LINES) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

/** Success line (spike-log style prefix, kept for grep-ability of pasted logs). */
function ok(msg) { log(`OK  ${msg}`, 'ok'); }

/** Error line. */
function bad(msg) { log(`ERR ${msg}`, 'bad'); }

/** Detail/debug line -- dim grey. Always visible (this is a dev-phase panel;
 * the bridge's own control traffic is light, so dim lines cannot flood). */
function logDebug(msg) { log(msg, 'dim'); }

/** Error line from a caught exception, message extracted. */
function fail(label, error) {
  bad(`${label}: ${error && error.message ? error.message : error}`);
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

/** One pair of wrapping quotes off + trim -- the owner's first Spike-C runs
 * pasted Explorer "Copy as path" values, whose literal quotes became part of
 * the imported path. Server-sent `pr_result.path` values are programmatic and
 * never carry wrapping quotes, so for them this is just a trim -- harmless,
 * and it keeps ONE path-cleanup rule for every path that enters the recipe. */
function cleanImportPath(raw) {
  let path = (raw || '').trim();
  if (
    path.length >= 2 &&
    ((path[0] === '"' && path[path.length - 1] === '"') ||
      (path[0] === "'" && path[path.length - 1] === "'"))
  ) {
    path = path.slice(1, -1).trim();
  }
  return path;
}

/** Canonical form for path comparison: forward slashes, lowercased. Exposed
 * separately from samePath so the import recipe can build Sets of stored
 * paths (its before-import bin snapshot) with the identical normalization. */
function normalizePathForCompare(p) {
  return String(p || '').replace(/\\/g, '/').toLowerCase();
}

/** Separator/case-insensitive path equality -- S6-C proved Premiere stores
 * `getMediaFilePath()` in the exact input form on win32, but this stays
 * tolerant so a future macOS/UNC/mapped-drive form can't silently break the
 * "find my import" step. */
function samePath(a, b) {
  return normalizePathForCompare(a) === normalizePathForCompare(b);
}

/** Last path segment (either separator); used when a pr_result has no label. */
function basename(p) {
  const parts = String(p || '').split(/[\\/]/);
  return parts[parts.length - 1] || String(p || '');
}

/** All ClipProjectItems in the project tree (breadth-first), capped. The
 * recipe's LAST-resort scan (its primary lookup enumerates only the bin it
 * controls -- S6-C's recorded DECISION); also kept for future probes. */
async function collectClips(pr, project, cap) {
  const root = await project.getRootItem();
  const queue = await root.getItems();
  const clips = [];
  while (queue.length && clips.length < cap) {
    const item = queue.shift();
    const asClip = pr.ClipProjectItem.cast(item);
    if (asClip) { clips.push(asClip); continue; }
    const asFolder = pr.FolderItem.cast(item);
    if (asFolder) queue.push(...(await asFolder.getItems()));
  }
  return clips;
}

/** The premierepro module, required lazily inside each caller so a require
 * failure is a logged line, not a dead panel (M0-proven posture). */
function ppro() {
  return require('premierepro');
}
