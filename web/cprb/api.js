/**
 * @file Fetch helpers + logging for the cprb frontend (PROTOCOL.md §7).
 * `getJson`/`postJson` share one error-unwrapping shape — ported from
 * comfyui-epsnodes' `lora_library/api.js` (same author, same pattern): a
 * non-2xx response rejects with an `Error` whose `.message` is the
 * server's `error` field (or `HTTP <status>` when the body wasn't JSON, or
 * had no `error` field), plus `.status` (the HTTP status code) and `.data`
 * (the parsed body, if any) for callers that need more than the message —
 * e.g. `PremiereSaveTimeline`'s "Open folder" button reads
 * `.data.exists` off a *successful* `/cprb/timeline_dir` response, not off
 * an error, but the same `unwrap` return value shape is what makes both
 * paths uniform.
 */

import { app } from '../../../scripts/app.js'
import { api } from '../../../scripts/api.js'

export { FRONTEND_VERSION } from './version.js'

const PREFIX = '[cprb]'

export function warn(message, error) {
  if (error !== undefined) console.warn(PREFIX, message, error)
  else console.warn(PREFIX, message)
}

/**
 * A node's Python class id. `comfyClass` is what ComfyUI's
 * `services/litegraphService.ts` stamps onto both the generated class and
 * its prototype for exactly this purpose, so it is checked first (both
 * spellings); `type` is the litegraph-level fallback, which is what a node
 * deserialized from a saved workflow carries before anything re-registers it.
 * @param {object} node
 * @returns {string|null}
 */
export function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  if (node.type) return node.type
  return null
}

/**
 * @param {object} node
 * @param {string} name
 */
export function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

export function toast(options) {
  try {
    app.extensionManager?.toast?.add?.(options)
  } catch (error) {
    warn('toast failed', error)
  }
}

/** Trailing path component, for a toast summary that fits on one line. */
export function basename(path) {
  const parts = String(path || '').split(/[\\/]/)
  return parts[parts.length - 1] || String(path || '')
}

/**
 * GET a cprb route. Resolves to parsed JSON; rejects (see file header) on
 * non-2xx.
 * @param {string} path - e.g. `/cprb/version`
 * @param {Record<string, string>} [params] - query params, e.g. `{dir: '/x'}`
 */
export async function getJson(path, params) {
  const query = params ? `?${new URLSearchParams(params)}` : ''
  const response = await api.fetchApi(`${path}${query}`)
  return unwrap(response)
}

/**
 * POST JSON to a cprb route. Resolves to parsed JSON; rejects (see file
 * header) on non-2xx.
 * @param {string} path - e.g. `/cprb/open_folder`
 * @param {object} [body]
 */
export async function postJson(path, body) {
  const response = await api.fetchApi(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {})
  })
  return unwrap(response)
}

async function unwrap(response) {
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body (proxy error page etc.) — fall through to status check.
  }
  if (!response.ok) {
    const message = data && data.error ? data.error : `HTTP ${response.status}`
    const error = new Error(message)
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}
