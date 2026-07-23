/*
 * M0 spike panel (roadmap-premiere-tier2.md). Buildless plain JS on purpose —
 * same posture as the Photoshop plugin: no bundler, files at plugin ROOT
 * (the cpsb round proved UXP resolves sub-resources against the main doc,
 * so nesting under src/ 404s). Every spike appends structured lines to the
 * in-panel log; "Copy results" hands the whole run back for SPIKES.md.
 */
'use strict';

const logEl = document.getElementById('log');

function log(msg, cls) {
  const line = document.createElement('div');
  if (cls) line.className = cls;
  const ts = new Date().toISOString().slice(11, 19);
  line.textContent = `[${ts}] ${msg}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}
const ok = (m) => log(`OK  ${m}`, 'ok');
const bad = (m) => log(`ERR ${m}`, 'bad');

function fail(label, error) {
  bad(`${label}: ${error && error.message ? error.message : error}`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** One pair of wrapping quotes off + trim — the owner's first Spike-C runs
 * pasted Explorer "Copy as path" values, whose literal quotes became part of
 * the imported path. Same cleanup rule the EPS Frame Saver's paste path uses. */
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

/** Separator/case-insensitive path equality — Premiere may store the media
 * path in a different separator or casing than the user typed; if the
 * IMPORTED item is visible but findItemsMatchingMediaPath returns 0, this is
 * how we prove it (and learn the stored form M1 should match against). */
function samePath(a, b) {
  const norm = (p) => String(p || '').replace(/\\/g, '/').toLowerCase();
  return norm(a) === norm(b);
}

/** All ClipProjectItems in the project tree (breadth-first), capped. */
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

function ppro() {
  // Required lazily inside each spike so a require failure is a logged
  // result, not a dead panel.
  return require('premierepro');
}

/* ---------------- Spike A: cleartext ws:// (THE gate) ---------------- */

document.getElementById('spike-a').addEventListener('click', () => {
  const target = document.getElementById('ws-target').value.trim();
  const url = `ws://${target}/ws?clientId=cprb-spike`;
  log(`SPIKE A: opening ${url} …`);
  let settled = false;
  let socket;
  try {
    socket = new WebSocket(url);
  } catch (error) {
    // A constructor throw is the "permission/scheme rejected" shape (the
    // cpsb plugin's UXP behaved this way for disallowed origins).
    fail('SPIKE A constructor threw (permission/scheme rejected?)', error);
    return;
  }
  const timer = setTimeout(() => {
    if (settled) return;
    settled = true;
    bad('SPIKE A: no open/error after 8s — treat as blocked or server not running');
    try { socket.close(); } catch (_) { /* already dead */ }
  }, 8000);

  socket.addEventListener('open', () => {
    ok('SPIKE A: socket OPEN — cleartext ws:// is permitted from Premiere UXP on this OS');
  });
  socket.addEventListener('message', (event) => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    const head = String(event.data).slice(0, 120);
    ok(`SPIKE A: server message received (full round trip): ${head}`);
    ok('SPIKE A RESULT: PASS');
    socket.close(1000, 'spike done');
  });
  socket.addEventListener('error', () => {
    // UXP WebSocket error events carry no detail; the close code says more.
    log('SPIKE A: error event (see close code next)');
  });
  socket.addEventListener('close', (event) => {
    if (settled) { log(`SPIKE A: closed (code ${event.code})`); return; }
    settled = true;
    clearTimeout(timer);
    bad(`SPIKE A RESULT: FAIL — closed before any message (code ${event.code}, reason "${event.reason}")`);
  });
});

/* ------------- Spike B: lockedAccess + executeTransaction ------------- */

document.getElementById('spike-b').addEventListener('click', async () => {
  log('SPIKE B: create bin via lockedAccess/executeTransaction …');
  try {
    const pr = ppro();
    const project = await pr.Project.getActiveProject();
    if (!project) { bad('SPIKE B: no active project — open any project first'); return; }
    const root = await project.getRootItem();
    let txnOk = false;
    project.lockedAccess(() => {
      txnOk = project.executeTransaction((compound) => {
        compound.addAction(root.createBinAction('ComfyUI Spike', true));
      }, 'Create ComfyUI spike bin');
    });
    if (txnOk) {
      ok('SPIKE B RESULT: PASS — bin created; check Edit ▸ Undo shows one "Create ComfyUI spike bin" step');
    } else {
      bad('SPIKE B RESULT: executeTransaction returned false');
    }
  } catch (error) {
    fail('SPIKE B RESULT: FAIL', error);
  }
});

/* --------------- Spike C: importFiles + find-by-path ----------------- */

document.getElementById('spike-c').addEventListener('click', async () => {
  /* v0.8.3 refinement. The 2026-07-23 owner run answered the idiom question
   * (static findItemsMatchingMediaPath is NOT a function on 26.3 — instance
   * only) but returned 0 matches even though importFiles said true. Three
   * suspects, and this version distinguishes them in one click:
   *   1. quoted paths — the field held Explorer "Copy as path" values with
   *      literal quotes (now stripped);
   *   2. import is ASYNC — the item may register after importFiles returns
   *      (now: retry rounds at 0/750/1500/3000ms);
   *   3. stored-path form — Premiere may store a different separator/casing
   *      than typed (now: every clip's getMediaFilePath() is LOGGED verbatim
   *      and compared separator/case-insensitively; forward-slash variant is
   *      also tried). If the item is visible by enumeration but find still
   *      says 0, M1 simply enumerates its own bin instead — not a blocker.
   * Also logs the OS platform: the run's Z:\ paths suggested the Windows PC,
   * but which machine ran the spikes matters for what Spike A proved. */
  const rawPath = document.getElementById('import-path').value;
  const path = cleanImportPath(rawPath);
  if (!path) { bad('SPIKE C: enter an absolute media file path first'); return; }
  if (path !== rawPath.trim()) log('SPIKE C: stripped wrapping quotes from the pasted path');
  let platform = 'unknown';
  try { platform = require('os').platform(); } catch (_) { /* keep unknown */ }
  log(`SPIKE C: platform ${platform}; importFiles(["${path}"]) …`);
  try {
    const pr = ppro();
    const project = await pr.Project.getActiveProject();
    if (!project) { bad('SPIKE C: no active project'); return; }
    const imported = await project.importFiles([path], true);
    log(`SPIKE C: importFiles returned ${imported}`);

    // Ground truth first: what does the project ACTUALLY contain, and how
    // does Premiere store each item's media path? (This is the line that
    // explains any 0-match result.)
    const clips = await collectClips(pr, project, 20);
    log(`SPIKE C: project now holds ${clips.length} clip item(s); stored media paths:`);
    let enumMatch = null;
    for (const clip of clips) {
      let stored = '(getMediaFilePath failed)';
      try { stored = await clip.getMediaFilePath(); } catch (_) { /* keep placeholder */ }
      log(`  stored: ${stored}`);
      if (enumMatch === null && samePath(stored, path)) enumMatch = stored;
    }
    if (enumMatch !== null) ok(`SPIKE C: enumeration match — import IS in the project as: ${enumMatch}`);

    if (!clips.length) { bad('SPIKE C RESULT: PARTIAL — importFiles said true but the project holds no clip items (wrong-machine path?)'); return; }

    // find-by-path, instance idiom (static is absent on 26.3 — prior ground
    // truth), retried across delays and separator variants.
    const variants = [path];
    const fwd = path.replace(/\\/g, '/');
    if (fwd !== path) variants.push(fwd);
    const delays = [0, 750, 1500, 3000];
    let found = null;
    for (const delay of delays) {
      if (delay) await sleep(delay);
      for (const variant of variants) {
        const items = await clips[0].findItemsMatchingMediaPath(variant, false);
        if (items && items.length) { found = { items, variant, delay }; break; }
      }
      if (found) break;
    }

    if (found) {
      ok(`SPIKE C RESULT: PASS — ${found.items.length} item(s) matched (variant "${found.variant}", after ${found.delay}ms)`);
      for (const item of found.items) {
        const clip = pr.ClipProjectItem.cast(item);
        if (clip) log(`  match: ${await clip.getMediaFilePath()}`);
      }
    } else if (enumMatch !== null) {
      ok('SPIKE C RESULT: PASS-VIA-ENUMERATION — the import lands and is enumerable, but findItemsMatchingMediaPath never matches this path form (0 across all delays/variants). M1 will enumerate its own bin; find-by-path is not required.');
    } else {
      bad('SPIKE C RESULT: PARTIAL — no find match AND no enumerated path equals the input (compare the stored: lines above against what you typed; likely a wrong-machine or not-mounted path).');
    }
  } catch (error) {
    fail('SPIKE C RESULT: FAIL', error);
  }
});

/* ----------------- Spike D: frame-export surface probe ---------------- */

document.getElementById('spike-d').addEventListener('click', async () => {
  log('SPIKE D: probing the frame-export surface (probe first, never guess an API) …');
  try {
    const pr = ppro();
    const exportish = Object.keys(pr).filter((k) => /export|encod/i.test(k)).sort();
    log(`SPIKE D: module keys matching /export|encod/i: ${JSON.stringify(exportish)}`);
    const project = await pr.Project.getActiveProject();
    const sequence = project ? await project.getActiveSequence() : null;
    if (!sequence) { bad('SPIKE D: no active sequence — open one, then re-run'); return; }
    const position = await sequence.getPlayerPosition();
    log(`SPIKE D: playhead ticks=${position && position.ticks !== undefined ? position.ticks : String(position)}`);
    const proto = Object.getPrototypeOf(sequence);
    const seqExport = Object.getOwnPropertyNames(proto).filter((k) => /export|frame/i.test(k)).sort();
    log(`SPIKE D: Sequence methods matching /export|frame/i: ${JSON.stringify(seqExport)}`);
    if (pr.EncoderManager) {
      const mgr = await pr.EncoderManager.getManager();
      const mgrKeys = Object.getOwnPropertyNames(Object.getPrototypeOf(mgr)).sort();
      log(`SPIKE D: EncoderManager methods: ${JSON.stringify(mgrKeys)}`);
    } else {
      log('SPIKE D: no EncoderManager export on the module');
    }
    ok('SPIKE D RESULT: PROBED — paste this log into SPIKES.md; M2 wires whichever call the probe surfaced');
  } catch (error) {
    fail('SPIKE D RESULT: FAIL', error);
  }
});

/* --------------------- Spike E: API ground truth ---------------------- */

document.getElementById('spike-e').addEventListener('click', async () => {
  log('SPIKE E: ground-truth probes (docs are provably incomplete; keys() is the authority) …');
  try {
    const uxp = require('uxp');
    log(`SPIKE E: host ${uxp.host.name} ${uxp.host.version} · uxp ${uxp.versions ? uxp.versions.uxp : '?'}`);
    const pr = ppro();
    log(`SPIKE E: premierepro module keys (${Object.keys(pr).length}): ${JSON.stringify(Object.keys(pr).sort())}`);

    // WorkAreaUtils exists in Adobe sample code but has no doc page.
    if (pr.WorkAreaUtils) {
      const project = await pr.Project.getActiveProject();
      const sequence = project ? await project.getActiveSequence() : null;
      if (sequence) {
        const inPoint = await pr.WorkAreaUtils.getWorkAreaInPoint(sequence);
        ok(`SPIKE E: WorkAreaUtils EXISTS; work-area in = ${inPoint && inPoint.seconds !== undefined ? inPoint.seconds : String(inPoint)}`);
      } else {
        ok('SPIKE E: WorkAreaUtils EXISTS (no active sequence to query)');
      }
    } else {
      bad('SPIKE E: WorkAreaUtils NOT on the module here');
    }

    // Properties on a ClipProjectItem: the r2 research UNKNOWN — if this
    // works it is our cleanest bridge-bookkeeping store.
    if (pr.Properties) {
      const project = await pr.Project.getActiveProject();
      if (project) {
        const root = await project.getRootItem();
        const queue = await root.getItems();
        let clip = null;
        while (queue.length && !clip) {
          const item = queue.shift();
          const asClip = pr.ClipProjectItem.cast(item);
          if (asClip) { clip = asClip; break; }
          const asFolder = pr.FolderItem.cast(item);
          if (asFolder) queue.push(...(await asFolder.getItems()));
        }
        if (clip) {
          try {
            const props = await pr.Properties.getProperties(clip);
            ok(`SPIKE E: Properties.getProperties(ClipProjectItem) WORKS (${props ? 'object returned' : 'null'}) — usable for bridge bookkeeping`);
          } catch (propErr) {
            log(`SPIKE E: Properties.getProperties(ClipProjectItem) rejected: ${propErr.message} — sequence-only, as docs implied`);
          }
        } else {
          log('SPIKE E: no clip in project to try Properties on');
        }
      }
    } else {
      log('SPIKE E: no Properties export on the module');
    }
    ok('SPIKE E RESULT: PROBED — copy the full log into SPIKES.md LIVE RESULTS');
  } catch (error) {
    fail('SPIKE E RESULT: FAIL', error);
  }
});

/* ------------------------------- misc -------------------------------- */

document.getElementById('copy-log').addEventListener('click', async () => {
  const text = Array.from(logEl.children).map((n) => n.textContent).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    ok('log copied to clipboard');
  } catch (error) {
    fail('clipboard copy', error);
  }
});

document.getElementById('clear-log').addEventListener('click', () => {
  logEl.replaceChildren();
});

log('panel loaded — run spikes top to bottom; A is the go/no-go gate.');
