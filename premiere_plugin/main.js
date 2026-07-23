/*
 * main.js -- M1 panel controller: header version fill, the connection card
 * (status pill + server field + Connect), the activity log's Copy/Clear,
 * and the ONE remaining spike-style button: S7, the frame-export probe that
 * gates M2. The M0 spike buttons (S6 A-E) are gone -- that round is complete
 * and recorded in docs/SPIKES.md LIVE RESULTS.
 *
 * Load order (index.html): helpers.js -> connection.js -> import_recipe.js
 * -> main.js -> layout.js. This file only wires the DOM to globals the
 * earlier scripts define; it defines nothing the others need.
 */
'use strict';

/* ------------------------------- header ------------------------------- */

(function fillHeaderVersion() {
  // The header version comes from the MANIFEST at runtime, so the
  // orchestrator's central version bumps show up here with no code edit.
  // VERIFY(spike-S6-followup): uxp.versions.plugin unproven on Premiere UXP
  // (proven on Photoshop) -- '?' is the harmless fallback.
  let version = '?';
  try {
    version = require('uxp').versions.plugin || '?';
  } catch (_) { /* leave '?' */ }
  const el = document.getElementById('plugin-version');
  if (el) el.textContent = version;
})();

/* --------------------------- connection card -------------------------- */

const cprbServerField = document.getElementById('server-base');
const cprbConnectBtn = document.getElementById('connect-btn');
const cprbPillEl = document.getElementById('conn-pill');
const cprbConnTextEl = document.getElementById('conn-text');
const cprbConnDetailEl = document.getElementById('conn-detail');

/** Renders one connection-state snapshot into the pill + status texts.
 * Grey = connecting, green = connected(+ready), red = disconnected. */
function renderConnectionState(state) {
  try {
    let pillClass = 'pill pill-grey';
    let pillText = 'connecting';
    let text = `connecting to ${state.serverBase} ...`;
    if (state.status === 'connected') {
      pillClass = 'pill pill-green';
      pillText = 'connected';
      text = `server ${state.serverVersion || '(unversioned)'} -- results will land in the bin`;
    } else if (state.status === 'disconnected') {
      pillClass = 'pill pill-red';
      pillText = 'disconnected';
      text = state.lastError || 'not connected';
      if (state.nextRetryAt) text += ` -- retrying (attempt ${state.attempts + 1})`;
    }
    if (cprbPillEl) {
      cprbPillEl.className = pillClass;
      cprbPillEl.textContent = pillText;
    }
    if (cprbConnTextEl) cprbConnTextEl.textContent = text;
    if (cprbConnDetailEl) cprbConnDetailEl.textContent = `target ${state.url}`;
  } catch (_) { /* a render hiccup must never take the panel down */ }
}

/** Connect button / Enter in the field: normalize + persist the address and
 * force a fresh connection (also the manual "reconnect now" and the way to
 * reclaim the server's single panel slot). */
function cprbConnectFromField() {
  try {
    cprbConnection.setServerBase(cprbServerField ? cprbServerField.value : '');
    // Reflect normalization ("http://localhost:8199/x" -> "localhost:8199").
    if (cprbServerField) cprbServerField.value = cprbConnection.getServerBase();
  } catch (error) {
    bad(`server address: ${error && error.message ? error.message : error}`);
  }
}

if (cprbConnectBtn) cprbConnectBtn.addEventListener('click', cprbConnectFromField);
if (cprbServerField) {
  cprbServerField.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') cprbConnectFromField();
  });
}

/* ------------------- S7: frame-export probe (M2 gate) ------------------ */
/*
 * PROBE, not a feature: its job is to produce a log Eric pastes back into
 * docs/SPIKES.md, not to succeed. S6-D enumerated EncoderManager's methods
 * and proved Sequence has no per-frame export; what M2 still needs is (a)
 * pr.Exporter's own surface (S6-D never probed Exporter itself), (b) the
 * exportSequence/encodeFile/encodeProjectItem arities, and (c) the EXACT
 * throw text of minimal exportSequence calls -- every throw is DATA.
 * The `export_ready` wire message stays reserved for M2; nothing here sends
 * anything to the server.
 */

document.getElementById('spike-s7').addEventListener('click', async () => {
  log('S7: frame-export probe (M2 gate) -- every line below, throws included, is spike DATA; Copy log and paste it back.');
  try {
    const pr = ppro();
    const project = await pr.Project.getActiveProject();
    if (!project) { bad('S7: no active project -- open one, then re-run'); return; }
    const sequence = await project.getActiveSequence();
    if (!sequence) { bad('S7: no active sequence -- open one, then re-run'); return; }

    // Playhead (PROVEN read, S6-D) -- the still M2 would export.
    const position = await sequence.getPlayerPosition();
    log(`S7: playhead ticks=${position && position.ticks !== undefined ? position.ticks : String(position)}`);

    // (a) Exporter's own static surface -- S6-D never probed Exporter itself.
    try {
      if (pr.Exporter) {
        log(`S7: Exporter own keys: ${JSON.stringify(Object.getOwnPropertyNames(pr.Exporter).sort())}`);
        if (pr.Exporter.prototype) {
          log(`S7: Exporter.prototype keys: ${JSON.stringify(Object.getOwnPropertyNames(pr.Exporter.prototype).sort())}`);
        }
      } else {
        log('S7: pr.Exporter is absent on this build');
      }
    } catch (error) { fail('S7: Exporter probe', error); }

    // Export-shaped Constants -- a preset/type enum would name the missing
    // exportSequence argument.
    try {
      if (pr.Constants) {
        const keys = Object.keys(pr.Constants).filter((k) => /export|encod|preset|frame/i.test(k)).sort();
        log(`S7: Constants keys matching /export|encod|preset|frame/i: ${JSON.stringify(keys)}`);
        for (const key of keys) {
          try { log(`S7: Constants.${key} = ${JSON.stringify(pr.Constants[key])}`); } catch (_) { /* unserializable */ }
        }
      } else {
        log('S7: pr.Constants is absent on this build');
      }
    } catch (error) { fail('S7: Constants probe', error); }

    // (b) EncoderManager arities (methods themselves enumerated in S6-D).
    if (!pr.EncoderManager) { bad('S7: no EncoderManager on the module'); return; }
    const manager = await pr.EncoderManager.getManager();
    for (const name of ['exportSequence', 'encodeFile', 'encodeProjectItem']) {
      try {
        const fn = manager[name];
        log(`S7: manager.${name}: ${typeof fn}${typeof fn === 'function' ? ` (.length=${fn.length})` : ''}`);
      } catch (error) { fail(`S7: manager.${name}`, error); }
    }

    // (c) Minimal exportSequence attempts, aiming at a still frame. Stop at
    // the first call that does NOT throw; otherwise harvest exact messages.
    let outDir = '';
    try {
      const os = require('os');
      if (typeof os.tmpdir === 'function') outDir = os.tmpdir() || '';
    } catch (_) { /* no os.tmpdir here */ }
    const sep = outDir.indexOf('\\') !== -1 ? '\\' : '/';
    const outPath = outDir ? `${outDir}${sep}cprb_s7_frame.png` : 'cprb_s7_frame.png';
    log(`S7: attempt output path: ${outPath}`);
    const attempts = [
      { name: 'exportSequence(sequence)', run: () => manager.exportSequence(sequence) },
      { name: 'exportSequence(sequence, outPath)', run: () => manager.exportSequence(sequence, outPath) },
      { name: 'exportSequence(sequence, outPath, "")', run: () => manager.exportSequence(sequence, outPath, '') }
    ];
    for (const attempt of attempts) {
      try {
        const result = await attempt.run();
        ok(`S7: ${attempt.name} RETURNED ${JSON.stringify(result)} -- check whether ${outPath} exists!`);
        break;
      } catch (error) {
        log(`S7: ${attempt.name} threw: ${error && error.message ? error.message : error}`);
      }
    }
    ok('S7 RESULT: PROBED -- Copy log and paste it back for SPIKES.md.');
  } catch (error) {
    fail('S7 RESULT: FAIL', error);
  }
});

/* -------------------------------- misc -------------------------------- */

document.getElementById('copy-log').addEventListener('click', async () => {
  const logEl = document.getElementById('log');
  const text = Array.from(logEl ? logEl.children : []).map((n) => n.textContent).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    ok('log copied to clipboard');
  } catch (error) {
    fail('clipboard copy', error);
  }
});

document.getElementById('clear-log').addEventListener('click', () => {
  const logEl = document.getElementById('log');
  if (logEl) logEl.replaceChildren();
});

/* ------------------------------- startup ------------------------------ */

if (cprbServerField) cprbServerField.value = cprbConnection.getServerBase();
cprbConnection.onStateChange = renderConnectionState;
renderConnectionState(cprbConnection.getState());
log('ComfyUI Bridge panel loaded -- run a Send-to-Premiere workflow and results land in the bin.');
cprbConnection.start();
