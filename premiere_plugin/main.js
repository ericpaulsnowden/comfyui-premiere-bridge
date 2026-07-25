/*
 * main.js -- M1 panel controller: the connection status area (pill +
 * Connect/Disconnect toggle + standby/error/retry lines), the ADVANCED
 * disclosure (version line, server-address field, target URL, error
 * detail), the activity log's Copy/Clear, and the SEND TO COMFYUI pair (M2's
 * return direction -- the actions themselves live in export_source.js).
 *
 * NO spike buttons remain. S6 A-E and S7 all PASSED and are recorded in
 * docs/SPIKES.md LIVE RESULTS. S7's button in particular is gone on purpose
 * rather than left as a curiosity: its attempt ladder called
 * `manager.exportSequence(...)` and broke on the first call that did NOT
 * throw, so on a build where any of those shapes is valid a misclick beside
 * the SEND TO COMFYUI buttons would have started a whole-sequence render or
 * an AME queue -- and it never touched export_source.js's busy flag, so it
 * could also interleave with a real export. Its read-only successor is
 * `cprbLogExporterProbe` in export_source.js, which every Frame -> ComfyUI
 * click runs for free.
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
 * -> export_source.js -> main.js -> layout.js. This file only wires the DOM
 * to globals the earlier scripts define; it defines nothing the others need.
 * All wiring runs inside cprbBootPanel()'s try/catch, so a boot-time throw
 * lands in the #fatal surface (helpers.js cprbShowFatal) instead of dying
 * silently.
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

  // SEND TO COMFYUI -- M2's return direction (export_source.js; PROTOCOL.md
  // §11). Both actions contain their own errors; cprbRunClick is the second
  // belt so neither a synchronous throw nor a rejected promise can escape a
  // click listener.
  const sendFrame = document.getElementById('send-frame');
  if (sendFrame) {
    sendFrame.addEventListener('click', () => cprbRunClick('Frame -> ComfyUI', cprbSendFrameToComfyUI));
  }
  const sendClip = document.getElementById('send-clip');
  if (sendClip) {
    sendClip.addEventListener('click', () => cprbRunClick('Clip -> ComfyUI', cprbSendClipToComfyUI));
  }

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
