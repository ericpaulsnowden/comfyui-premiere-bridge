/**
 * @file Fetch helper + logging for the cprb frontend (PROTOCOL.md §7).
 */

import { api } from '../../../scripts/api.js'

export { FRONTEND_VERSION } from './version.js'

const PREFIX = '[cprb]'

export function warn(message, error) {
  if (error !== undefined) console.warn(PREFIX, message, error)
  else console.warn(PREFIX, message)
}

/**
 * GET a cprb route. Resolves to parsed JSON; rejects with the server's
 * `error` message on non-2xx.
 * @param {string} path - e.g. `/cprb/version`
 */
export async function getJson(path) {
  const response = await api.fetchApi(path)
  let data = null
  try {
    data = await response.json()
  } catch {
    // Non-JSON body — fall through to status check.
  }
  if (!response.ok) {
    throw new Error(data && data.error ? data.error : `HTTP ${response.status}`)
  }
  return data
}
