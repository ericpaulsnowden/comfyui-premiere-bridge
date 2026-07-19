/**
 * @file Fetch helpers + logging for the cprb frontend (PROTOCOL.md §7).
 * `getJson`/`postJson` share one error-unwrapping shape — ported from
 * comfyui-epsnodes' `lora_library/api.js` (same author, same pattern): a
 * non-2xx response rejects with an `Error` whose `.message` is the
 * server's `error` field (or `HTTP <status>` when the body wasn't JSON, or
 * had no `error` field), plus `.status` (the HTTP status code) and `.data`
 * (the parsed body, if any) for callers that need more than the message —
 * e.g. `PremiereSaveTimeline`'s "Open output folder" button reads
 * `.data.exists` off a *successful* `/cprb/timeline_dir` response, not off
 * an error, but the same `unwrap` return value shape is what makes both
 * paths uniform.
 */

import { api } from '../../../scripts/api.js'

export { FRONTEND_VERSION } from './version.js'

const PREFIX = '[cprb]'

export function warn(message, error) {
  if (error !== undefined) console.warn(PREFIX, message, error)
  else console.warn(PREFIX, message)
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
