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

/** One-time setup: fetch the backend version; toast on mismatch. */
export async function initSettings() {
  try {
    const data = await api.getJson('/cprb/version')
    backendVersion = data.version
  } catch (error) {
    api.warn('backend version unreachable', error)
    return
  }
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
}

function versionRow() {
  const el = document.createElement('div')
  el.style.opacity = '0.85'
  el.textContent = backendVersion
    ? `backend v${backendVersion} · frontend v${api.FRONTEND_VERSION}` +
      (backendVersion === api.FRONTEND_VERSION ? '' : '  ⚠ mismatch — restart server or hard-refresh')
    : `frontend v${api.FRONTEND_VERSION} · backend unreachable`
  return el
}
