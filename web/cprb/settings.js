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
    id: 'cprb.versions',
    category: ['Premiere Bridge', 'About', 'Versions'],
    name: 'Backend / frontend versions',
    type: () => versionRow(),
    defaultValue: ''
  }
]

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
