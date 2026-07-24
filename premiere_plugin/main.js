/*
 * main.js -- M1 panel controller: the connection status area (pill +
 * Connect/Disconnect toggle + standby/error/retry lines), the ADVANCED
 * disclosure (version line, server-address field, target URL, error
 * detail), the activity log's Copy/Clear, and the ONE remaining
 * spike-style button: S7, the frame-export probe that gates M2. The M0
 * spike buttons (S6 A-E) are gone -- that round is complete and recorded
 * in docs/SPIKES.md LIVE RESULTS.
 *
 * The panel's design mirrors the Photoshop plugin's production panel
 * (cpsb panel.html/panel.js -- the standard-bearer, owner directive
 * 2026-07-24): same pill wording ("Connected"/"Connecting..."/
 * "Disconnected"/"Standing by"), the same ONE-toggle connection control
 * (Connect is the panel's single cta and shows only while standing by;
 * Disconnect is secondary), the same JS-driven Advanced disclosure, the
 * same blocking-vs-transient error split, and the same once-a-second
 * "retrying in Ns" countdown. Premiere-legitimate differences remain:
 * the activity log stays top-level (in M1 the log IS the product -- it is
 * where imports are confirmed -- where cpsb's log is pure diagnostics),
 * and there is no handoff list / Live Mode / send flow to render.
 *
 * Load order (index.html): helpers.js -> connection.js -> import_recipe.js
 * -> main.js -> layout.js. This file only wires the DOM to globals the
 * earlier scripts define; it defines nothing the others need. All wiring
 * runs inside cprbBootPanel()'s try/catch, so a boot-time throw lands in
 * the #fatal surface (helpers.js cprbShowFatal) instead of dying silently.
 */
'use strict';

/* ------------------------------ version ------------------------------- */

/**
 * The manifest version at runtime, for panel display -- so the
 * orchestrator's central version bumps show up with no code edit, and no
 * visible version can ever go stale (owner rule: a visible version number
 * must always answer "am I in sync?"). PROVEN on Premiere UXP (owner live
 * session 2026-07-24 -- the old VERIFY(spike-S6-followup) is retired).
 * Depends on nothing but require('uxp'), so the proof-of-life paint below
 * works even if the connection singleton failed to load. '?' fallback is
 * display-only; the wire `hello` uses connection.js's cprbPluginVersion().
 */
function cprbManifestVersion() {
  try {
    const v = require('uxp').versions.plugin;
    if (v) return String(v);
  } catch (_) { /* leave the fallback */ }
  return '?';
}

/* --------------------------- connection area --------------------------- */

/** Pill wording, cpsb's exact labels. */
const CPRB_STATUS_LABELS = {
  disconnected: 'Disconnected',
  connecting: 'Connecting…',
  connected: 'Connected'
};

/** The 1s interval keeping the "retrying in Ns" countdown live while
 * disconnected; null whenever connected or standing by. */
let cprbRetryTicker = null;

/** Renders the Advanced version line: plugin version alone while not
 * connected; "Plugin vX * Server vY" once connected, with an amber
 * "update available" accent when they differ (versions bump in lockstep,
 * so a mismatch means a stale copy -- informational, never red: the pill
 * owns connection truth). *state* may be null (proof-of-life paint). */
function cprbRenderVersionLine(state) {
  const el = document.getElementById('plugin-version');
  if (!el) return;
  const label = `Plugin v${cprbManifestVersion()}`;
  if (state && state.status === 'connected' && state.serverVersion) {
    const mismatch = state.serverVersion !== cprbManifestVersion();
    el.textContent = mismatch
      ? `${label} • Server v${state.serverVersion} · update available`
      : `${label} • Server v${state.serverVersion}`;
    el.className = mismatch ? 'version-line version-mismatch' : 'version-line';
    return;
  }
  el.textContent = label;
  el.className = 'version-line';
}

/** The retry/connecting line for a non-connected, non-standby state
 * (empty string = nothing to show). Phrased as waiting, not failing: a
 * not-yet-reachable ComfyUI (still starting up) is the common case. */
function cprbRetryText(state) {
  if (state.status === 'connecting') return 'Connecting to ComfyUI…';
  if (state.nextRetryAt != null) {
    const seconds = Math.max(0, Math.ceil((state.nextRetryAt - Date.now()) / 1000));
    return `Waiting for ComfyUI — retrying in ${seconds}s`;
  }
  return '';
}

/** Renders one connection-state snapshot into the pill, toggle button,
 * standby/error/retry lines, and the Advanced diagnostics. Call with no
 * argument to re-read live state (the countdown ticker does). */
function renderConnectionState(state) {
  try {
    if (!state) state = cprbConnection.getState();
    const standby = state.standby; // 'superseded' | 'manual' | null

    cprbRenderVersionLine(state);
    const urlEl = document.getElementById('server-url');
    if (urlEl) urlEl.textContent = `target ${state.url}`;

    // The toggle flips by intent: Connect while standing by (idle, awaiting
    // the user), Disconnect otherwise (stop a retry loop / bow out). Loud
    // (cta) ONLY when Connect is the action -- this button holds the
    // panel's single reserved cta slot (cpsb rule).
    const toggle = document.getElementById('connect-toggle');
    if (toggle) {
      toggle.textContent = standby ? 'Connect' : 'Disconnect';
      toggle.setAttribute('variant', standby ? 'cta' : 'secondary');
    }

    // Status pill. Standby is idle, NOT a fault: neutral grey dot.
    const dot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    if (dot && statusText) {
      if (standby) {
        dot.className = 'dot';
        statusText.textContent = standby === 'superseded' ? 'Standing by' : 'Disconnected';
      } else {
        dot.className = `dot dot-${state.status}`;
        statusText.textContent = CPRB_STATUS_LABELS[state.status] || state.status;
      }
    }

    // Standby explanation -- a state, not an error.
    const standbyEl = document.getElementById('conn-standby');
    if (standbyEl) {
      if (standby === 'superseded') {
        standbyEl.textContent =
          'Another panel is connected to this ComfyUI. This one is standing by — ' +
          'press Connect to take over.';
        standbyEl.style.display = 'block';
      } else if (standby === 'manual') {
        standbyEl.textContent = 'Disconnected. Press Connect to reconnect.';
        standbyEl.style.display = 'block';
      } else {
        standbyEl.style.display = 'none';
      }
    }

    const errorEl = document.getElementById('conn-error');
    const detailEl = document.getElementById('conn-error-detail');
    const retryEl = document.getElementById('conn-retry');

    // Connected, or standing by (idle -- nothing retrying): no error/retry
    // chatter to show; clear it all and stop the countdown ticker.
    if (state.status === 'connected' || standby) {
      if (errorEl) errorEl.style.display = 'none';
      if (detailEl) detailEl.style.display = 'none';
      if (retryEl) retryEl.style.display = 'none';
      if (cprbRetryTicker) {
        clearInterval(cprbRetryTicker);
        cprbRetryTicker = null;
      }
      return;
    }

    // A BLOCKING error (permission-shaped -- the user must act) breaks out
    // at the top in red. A TRANSIENT one (server not up yet) stays calm:
    // the "Waiting for ComfyUI" line is the top-level signal, and the raw
    // detail lives in Advanced + the log (cpsb's split).
    if (errorEl) {
      if (state.lastError && state.lastErrorBlocking) {
        errorEl.textContent = `Action needed: ${state.lastError}`;
        errorEl.style.display = 'block';
      } else {
        errorEl.style.display = 'none';
      }
    }
    if (detailEl) {
      if (state.lastError) {
        detailEl.textContent = `Last connection error: ${state.lastError}`;
        detailEl.style.display = 'block';
      } else {
        detailEl.style.display = 'none';
      }
    }
    if (retryEl) {
      const retry = cprbRetryText(state);
      retryEl.textContent = retry;
      retryEl.style.display = retry ? 'block' : 'none';
    }
    // Tick once a second while not connected so the countdown counts down
    // instead of freezing at its first value.
    if (!cprbRetryTicker) {
      cprbRetryTicker = setInterval(() => renderConnectionState(), 1000);
    }
  } catch (_) { /* a render hiccup must never take the panel down */ }
}

/** Advanced "Apply / Connect": normalize + persist the address and force a
 * fresh connection (also the manual "reconnect now"). Validation errors
 * (empty/malformed input) show inline under the field, cpsb-style; on
 * success the field is rewritten with the normalized value and the normal
 * statechange rendering shows the result. */
function cprbApplyServerBase() {
  const field = document.getElementById('server-base');
  const errEl = document.getElementById('server-error');
  try {
    cprbConnection.setServerBase(field ? field.value : '');
    // Reflect normalization ("http://localhost:8199/x" -> "localhost:8199").
    if (field) field.value = cprbConnection.getServerBase();
    if (errEl) {
      errEl.textContent = '';
      errEl.style.display = 'none';
    }
  } catch (error) {
    if (errEl) {
      errEl.textContent = describeError(error);
      errEl.style.display = 'block';
    }
  }
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

async function cprbRunS7Probe() {
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
}

/* ------------------------------- startup ------------------------------ */

/** Wires the whole panel. Runs once, inside the guarded call below. */
function cprbBootPanel() {
  // Proof of life FIRST (cpsb index.js pattern): the version line depends
  // on nothing but require('uxp') + the DOM, so it paints even if the
  // wiring below throws -- a reload that shows a fresh version but an
  // error means "code loaded, boot broke"; a stale "Plugin loading..."
  // means the scripts themselves never ran.
  cprbRenderVersionLine(null);

  // Connect/Disconnect toggle: Connect when standing by (resume/reclaim),
  // otherwise Disconnect (stop the loop and stay off). Reads live state at
  // click time so a mid-render click is safe.
  const toggle = document.getElementById('connect-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      if (cprbConnection.getState().standby) {
        cprbConnection.connect();
      } else {
        cprbConnection.disconnect();
      }
    });
  }

  // Advanced disclosure (JS-driven: <details> is unsupported in UXP).
  const advToggle = document.getElementById('advanced-toggle');
  const advBody = document.getElementById('advanced-body');
  const advCaret = document.getElementById('advanced-caret');
  if (advToggle && advBody) {
    advToggle.addEventListener('click', () => {
      const collapsed = advBody.className.indexOf('collapsed') !== -1;
      advBody.className = collapsed ? '' : 'collapsed';
      if (advCaret) advCaret.textContent = collapsed ? '▾' : '▸';
    });
  }

  // Server-address field: prefill with the persisted base; Apply (or Enter
  // in the field) normalizes, persists, and reconnects.
  const serverField = document.getElementById('server-base');
  if (serverField) {
    serverField.value = cprbConnection.getServerBase();
    serverField.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') cprbApplyServerBase();
    });
  }
  const serverApply = document.getElementById('server-apply');
  if (serverApply) serverApply.addEventListener('click', cprbApplyServerBase);
  const serverError = document.getElementById('server-error');
  if (serverError) serverError.style.display = 'none';

  // S7 probe (M2 gate) -- logic unchanged from the run that produced the
  // owner's 2026-07-24 log.
  const s7 = document.getElementById('spike-s7');
  if (s7) s7.addEventListener('click', cprbRunS7Probe);

  // Activity-log actions.
  const copyBtn = document.getElementById('copy-log');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const logEl = document.getElementById('log');
      const text = Array.from(logEl ? logEl.children : []).map((n) => n.textContent).join('\n');
      try {
        await navigator.clipboard.writeText(text);
        ok('log copied to clipboard');
      } catch (error) {
        fail('clipboard copy', error);
      }
    });
  }
  const clearBtn = document.getElementById('clear-log');
  if (clearBtn) {
    clearBtn.addEventListener('click', () => {
      const logEl = document.getElementById('log');
      if (logEl) logEl.replaceChildren();
    });
  }

  // Go live: render current state, announce (the version in this line means
  // every pasted log self-identifies its build), connect.
  cprbConnection.onStateChange = renderConnectionState;
  renderConnectionState(cprbConnection.getState());
  log(`ComfyUI Bridge panel v${cprbManifestVersion()} loaded -- run a Send to Premiere workflow and results land in the bin.`);
  cprbConnection.start();
}

try {
  cprbBootPanel();
} catch (error) {
  // A visible fatal beats a silently bare panel (cpsb's boot guard).
  cprbShowFatal(`panel boot failed: ${describeError(error)}`);
}
