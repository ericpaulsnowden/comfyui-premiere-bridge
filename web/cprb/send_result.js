/**
 * @file Toasts for "Send to Premiere" runs (PROTOCOL.md §10.6).
 *
 * Why this exists: `PremiereSendResult` returns a `ui.text` summary saying
 * either "Sent to Premiere: <path>" or "Plugin not connected — import
 * manually: <path>", but NOTHING in ComfyUI renders an arbitrary `ui.text`
 * payload — so a run whose push silently failed looked exactly like a
 * successful one (owner, 2026-07-24: "The run finished, but I didn't see a
 * message anywhere that it didn't work"). The backend now emits a
 * `cprb.send_result` event per run and this module turns it into a toast.
 *
 * Deliberately asymmetric, because the two cases need different things from
 * the user:
 *   - PUSHED: a short info toast. Confirmation only — the real feedback is
 *     the clip appearing in Premiere's bin.
 *   - NOT PUSHED: a warning toast that stays up long enough to read and
 *     CARRIES THE PATH, because the user's next action is to import that
 *     file by hand. This is the whole point of the module.
 *
 * Toast surface + `severity`/`life` conventions match `settings.js`'s
 * version-mismatch toast (`app.extensionManager?.toast?.add?.(...)`, every
 * hop optional-chained — a missing toast surface on some frontend build must
 * never throw into an event handler).
 */

import { app } from '../../../scripts/app.js'
import { api } from '../../../scripts/api.js'
import { warn } from './api.js'

/** Trailing path component, for a toast summary that fits on one line. */
function basename(path) {
  const parts = String(path || '').split(/[\\/]/)
  return parts[parts.length - 1] || String(path || '')
}

/** `n` items, pluralized: "1 result" / "3 results". */
function countLabel(n) {
  return `${n} result${n === 1 ? '' : 's'}`
}

function toast(options) {
  try {
    app.extensionManager?.toast?.add?.(options)
  } catch (error) {
    warn('toast failed', error)
  }
}

/**
 * Handles one `cprb.send_result` payload:
 * `{results: [{path, pushed}, ...], bin_name}`.
 *
 * One toast per OUTCOME (not per file): a run with both a video and an image
 * that both landed is one confirmation, not two. A mixed run (one pushed,
 * one not) produces both toasts — the failure half still needs its path.
 */
function onSendResult(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : []
  if (!results.length) return
  const pushed = results.filter((r) => r?.pushed)
  const failed = results.filter((r) => r && !r.pushed)

  if (pushed.length) {
    toast({
      severity: 'info',
      summary: 'Sent to Premiere',
      detail:
        `${countLabel(pushed.length)} → ${payload?.bin_name || 'ComfyUI Results'} bin` +
        ` (${pushed.map((r) => basename(r.path)).join(', ')})`,
      life: 4000
    })
  }

  if (failed.length) {
    // The important one: no panel was listening, so the user has to import
    // this themselves. Full paths, and a long life — this is a to-do, not a
    // status blip.
    toast({
      severity: 'warn',
      summary: 'Premiere panel not connected',
      detail:
        `${countLabel(failed.length)} saved but NOT imported — open the ComfyUI ` +
        'Bridge panel in Premiere (or import manually):\n' +
        failed.map((r) => r.path).join('\n'),
      life: 15000
    })
  }
}

/** Subscribes to the backend event. Called once from the extension's setup. */
export function initSendResultToasts() {
  api.addEventListener('cprb.send_result', (event) => {
    try {
      onSendResult(event?.detail)
    } catch (error) {
      warn('send_result toast failed', error)
    }
  })
}
