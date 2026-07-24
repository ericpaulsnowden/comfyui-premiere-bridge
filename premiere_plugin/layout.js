/*
 * Panel-layout diagnostics + sizing fallback (landed in the v0.8.2 spike
 * round). Owner report on v0.8.0 AND v0.8.1: the panel "can't be resized
 * or scrolled". v0.8.2's index.html mirrors the ONE layout proven in real
 * UXP on the owner's machines (the Photoshop plugin's panel: html/body
 * 100% + a 100%-height #root with overflow-y:auto + a capped scrolling
 * log); this file adds the two things CSS can't:
 *
 * 1. A JS SIZING FALLBACK: if the wrapper still collapses (root much shorter
 *    than the window viewport -- the local browser check caught exactly this
 *    shape, root at 20px), pin root.style.height/width to the viewport and
 *    keep them pinned on every re-measure. Engages only when needed; the
 *    startup log line says which mode is active.
 * 2. GROUND TRUTH on resize: a live "W x H" readout (#dims, now under the
 *    panel's ADVANCED disclosure). Drag a panel divider / floating-window
 *    edge: numbers change => the host resize reaches the plugin DOM and
 *    layout is my problem; numbers frozen => host-side (there is a reported
 *    Premiere bug about UXP renderer bounds not resizing vertically) --
 *    dock the panel / restart Premiere, and report it: that is itself a
 *    spike result.
 *
 * Deliberately separate from main.js (spike logic untouched). ASCII-only
 * strings on purpose. Every entry point is try/catch'd -- a layout probe
 * must never take the spikes down.
 */
'use strict';

(function () {
  const root = document.getElementById('root');
  const dimsEl = document.getElementById('dims');
  const logEl = document.getElementById('log');
  if (!root) return;

  let mode = 'css'; // flips to 'js-fallback' if the wrapper collapses

  function appendLog(text) {
    // Prefer the shared logger (helpers.js loads first) so layout lines get
    // the same timestamp/cap/scroll treatment as everything else; keep the
    // bare-DOM append as a last resort -- a layout probe must never depend
    // on another script having survived boot.
    try {
      if (typeof log === 'function') {
        log(text, 'dim');
        return;
      }
    } catch (_) { /* fall through to the direct append */ }
    if (!logEl) return;
    const line = document.createElement('div');
    line.textContent = text;
    logEl.appendChild(line);
  }

  function viewport() {
    // window.innerWidth/Height = the panel's webview viewport in UXP hosts
    // and browsers alike; 0/undefined-safe.
    const w = (typeof window !== 'undefined' && window.innerWidth) || 0;
    const h = (typeof window !== 'undefined' && window.innerHeight) || 0;
    return { w, h };
  }

  function applyFallbackIfCollapsed() {
    try {
      const vp = viewport();
      if (vp.h < 100) return; // no trustworthy viewport number; leave CSS alone
      const short = vp.h - root.clientHeight;
      if (mode === 'js-fallback' || short > 8) {
        // Explicit height wins over the top/bottom stretch; width likewise.
        root.style.height = vp.h + 'px';
        root.style.width = vp.w + 'px';
        if (mode !== 'js-fallback') {
          mode = 'js-fallback';
          appendLog('layout: CSS fill collapsed (root ' +
            (root.clientHeight) + 'px vs viewport ' + vp.h + 'px) -> JS sizing engaged');
        }
      }
    } catch (_) { /* never break the panel over sizing */ }
  }

  let last = { w: -1, h: -1 };
  function update() {
    try {
      applyFallbackIfCollapsed();
      const s = { w: root.clientWidth, h: root.clientHeight };
      if (s.w === last.w && s.h === last.h) return;
      last = s;
      if (dimsEl) dimsEl.textContent = s.w + 'x' + s.h;
    } catch (_) { /* ditto */ }
  }

  // Prefer ResizeObserver, fall back to the window resize event, and ALWAYS
  // keep a slow poll as the backstop -- UXP hosts differ on which of these
  // actually fire, and the poll costs nothing at this cadence.
  let signal = 'poll-only';
  try {
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => update()).observe(root);
      signal = 'ResizeObserver';
    }
    if (typeof window !== 'undefined' && window.addEventListener) {
      window.addEventListener('resize', () => update());
      if (signal === 'poll-only') signal = 'window.resize';
    }
  } catch (_) { /* fall through to the poll */ }
  setInterval(update, 750);

  // First paint + one settled re-measure, then the startup diagnostic line
  // (rides along in "Copy results").
  update();
  setTimeout(() => {
    update();
    let uxpVer = 'n/a';
    try { uxpVer = require('uxp').versions.uxp; } catch (_) { /* not UXP */ }
    let platform = 'unknown';
    try { platform = require('os').platform(); } catch (_) { /* not UXP */ }
    // No version claim here ON PURPOSE: this line once hardcoded one, which
    // went stale and told the owner he was in sync when he wasn't (real bug,
    // 2026-07-24). The boot line in main.js carries the manifest version at
    // runtime, so every pasted log still self-identifies its build.
    appendLog('layout: panel ' + last.w + 'x' + last.h +
      ', sizing: ' + mode + ', resize signal: ' + signal +
      ' (+750ms poll), uxp ' + uxpVer + ', platform ' + platform +
      '. Drag a panel divider and watch the W x H under ADVANCED: if the ' +
      'numbers never change, the host is not resizing the plugin (dock the ' +
      'panel / restart Premiere) -- report that.');
  }, 400);
})();
