/**
 * @file "Premiere Bridge" settings section (PROTOCOL.md §7): backend +
 * frontend version display; a mismatch means "pulled but not restarted"
 * (the comfyui-photoshop-bridge pattern).
 */

import { app } from '../../../scripts/app.js'
import * as api from './api.js'

let backendVersion = null

export const SETTINGS = [
  {
    id: 'cprb.autoRun',
    name: 'Run the workflow when Premiere sends a frame or clip',
    type: 'boolean',
    defaultValue: true,
    category: ['Premiere Bridge', 'General', 'Auto-run'],
    tooltip:
      'When on: clicking "Frame → ComfyUI" or "Clip → ComfyUI" in the ' +
      'Premiere panel fills the matching node AND queues the workflow — no ' +
      'trip back to ComfyUI to press Run. When off: the node is filled in ' +
      'and you press Run yourself.'
  },
  {
    id: 'cprb.versions',
    category: ['Premiere Bridge', 'About', 'Versions'],
    name: 'Backend / frontend versions',
    type: () => versionRow(),
    defaultValue: ''
  }
]

/**
 * Current value of `cprb.autoRun` (default `true`). Read defensively at
 * EVENT time, never cached — cpsb's `readBooleanSetting` pattern verbatim:
 * a missing/throwing settings API degrades to the default rather than
 * breaking the relay.
 * @returns {boolean}
 */
export function getAutoRun() {
  const settingApi = app.extensionManager?.setting
  if (!settingApi || typeof settingApi.get !== 'function') return true
  try {
    const value = settingApi.get('cprb.autoRun')
    return typeof value === 'boolean' ? value : true
  } catch (error) {
    api.warn('failed to read setting "cprb.autoRun", using default', error)
    return true
  }
}

/** In-flight `/cprb/version` fetch, so overlapping callers (setup racing a
 * quickly-opened settings dialog) de-dupe onto one request — and therefore
 * at most one mismatch toast. */
let versionFetchPromise = null

/** Fetches the backend version, remembers it, and fires the mismatch toast
 * on the FIRST successful read — wherever that happens (setup, or a later
 * settings-dialog open via versionRow()'s recovery path). Rejects on an
 * unreachable backend; callers decide how loudly to say so. */
function fetchBackendVersion() {
  if (!versionFetchPromise) {
    versionFetchPromise = api
      .getJson('/cprb/version')
      .then((data) => {
        backendVersion = data.version
        if (backendVersion && backendVersion !== api.FRONTEND_VERSION) {
          app.extensionManager?.toast?.add?.({
            severity: 'warn',
            summary: 'Premiere Bridge version mismatch',
            detail:
              `backend v${backendVersion}, frontend v${api.FRONTEND_VERSION} — if you ` +
              'just updated, restart the ComfyUI server (backend) or hard-refresh ' +
              'the browser (frontend).',
            life: 8000
          })
        }
      })
      .finally(() => {
        versionFetchPromise = null
      })
  }
  return versionFetchPromise
}

/** One-time setup: fetch the backend version; toast on mismatch. */
export async function initSettings() {
  try {
    await fetchBackendVersion()
  } catch (error) {
    api.warn('backend version unreachable', error)
  }
}

function versionRow() {
  const el = document.createElement('div')
  el.style.opacity = '0.85'
  renderVersionRow(el)
  if (backendVersion === null) {
    // Recovery window (2026-08-08 audit): a backend that was restarting
    // during page load used to leave this row saying "unreachable" for the
    // whole session AND permanently swallow the mismatch toast — the exact
    // restart window the drift surface exists to cover. Opening the
    // settings dialog is the natural moment to look again; on success the
    // row updates in place and fetchBackendVersion() fires the toast.
    fetchBackendVersion()
      .then(() => renderVersionRow(el))
      .catch((error) => api.warn('backend version still unreachable', error))
  }
  return el
}

function renderVersionRow(el) {
  el.textContent = backendVersion
    ? `backend v${backendVersion} · frontend v${api.FRONTEND_VERSION}` +
      (backendVersion === api.FRONTEND_VERSION ? '' : '  ⚠ mismatch — restart server or hard-refresh')
    : `frontend v${api.FRONTEND_VERSION} · backend unreachable`
}
