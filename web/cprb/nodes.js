/**
 * @file File-bar UI for `PremiereLoadTimeline` / `PremiereSaveTimeline`
 * nodes (PROTOCOL.md §7.3): a small `addDOMWidget` bar under each node's
 * own widgets, gated by §7.2's `GET /cprb/config` `is_local` (§7.1 — the
 * picker and folder-reveal routes are loopback-only, so a remote viewer
 * never sees buttons for them; everything else about either node keeps
 * working unchanged).
 *
 * - `PremiereLoadTimeline` gets `Browse…` (a modal picker over `GET
 *   /cprb/fs/list`, `.xml` only) and `Open folder` (`POST
 *   /cprb/open_folder` on the current `file_path` value).
 * - `PremiereSaveTimeline` gets one `Open output folder` button (`GET
 *   /cprb/timeline_dir` → `POST /cprb/open_folder`, or an inline note when
 *   the sequence hasn't been run yet).
 *
 * Ported from comfyui-epsnodes' `web/lora_library/notebook.js` (same
 * author, same patterns; this pack can't import across repos, so the
 * approach is re-implemented here, trimmed to what these two small nodes
 * need — no two-pane editor, no drag/rename/multi-select/categories).
 * Specifically ported:
 *
 *  - The modal picker: attached to `document.body`, not nested inside the
 *    node's own DOM, so it isn't clipped or resized along with a small
 *    node — see openPicker()/closePicker() below. Same singleton-overlay
 *    design too (one module-level `PICKER_OVERLAY_ID`, closed by Escape, a
 *    backdrop click, opening another picker, or either node's own
 *    removal).
 *  - The `GET /cprb/config` cache: module-level, ~60s TTL, concurrent
 *    callers de-dupe onto one in-flight promise (fetchConfig()/getConfig()
 *    below) — every attached node shares one fetch instead of one each,
 *    and a config fetch failure fails OPEN (treated as local) rather than
 *    disabling the buttons over a network hiccup.
 *  - `node.comfyClass` with a `node.constructor.comfyClass` fallback to
 *    identify a node's Python class id from `nodeCreated` (nodeClassOf()
 *    below) — the sibling file's header traces this to ComfyUI's
 *    `services/litegraphService.ts`, which sets `comfyClass` on both the
 *    class and its prototype for exactly this feature-detection purpose.
 *  - Two findings from that file's header carry over unchanged: (1)
 *    pointer events over a DOM widget's own elements are NOT swallowed by
 *    the litegraph canvas underneath (canvas and DOM widgets are DOM
 *    siblings, never nested), so plain `addEventListener('click', ...)` on
 *    our buttons needs no special handling to "reach" them; (2)
 *    `widget.disabled` blanks a disabled TEXT widget's VALUE entirely on
 *    this litegraph fork rather than just graying it out — which is why
 *    gating below only ever hides/shows BUTTONS (`style.display`) and
 *    never touches `file_path` or `sequence_name` themselves. A remote
 *    viewer keeps a fully editable `file_path`/`sequence_name` and just
 *    types paths by hand (PROTOCOL.md §7.1).
 *
 * Changed from the sibling: no read-only edit-guard on any widget (Tier 1
 * doesn't need one here — only the buttons are host-only, never the
 * widgets they sit next to); one small `.cprb-bar` DOM widget per node
 * instead of a two-pane editor; the picker passes a server-side `ext`
 * allowlist (`.xml`) as a query param instead of a fixed extension baked
 * into the route.
 *
 * Vanilla ES modules, no build step, matching the rest of this pack.
 */

import * as api from './api.js'

/** PROTOCOL.md §8 — frozen node class ids. */
const LOAD_CLASS = 'PremiereLoadTimeline'
const SAVE_CLASS = 'PremiereSaveTimeline'

const WIDGET_NAME = 'cprb_file_bar'
const WIDGET_TYPE = 'cprb_file_bar'

/** One short row of buttons + a status line — kept fixed so the DOM
 * widget never balloons a small node's default size (both min and max are
 * set to this same value; see attachBarWidget()). */
const BAR_HEIGHT = 30

/** §7.2's `fs/list` extension allowlist for the Load node's picker. */
const PICKER_EXT = '.xml'

const STYLE_TAG_ID = 'cprb-node-ui-styles'
const PICKER_OVERLAY_ID = 'cprb-picker-overlay'

/** Nodes we've already attached to — guards against a double `nodeCreated`. */
const attachedNodes = new WeakSet()

// ---------------------------------------------------------------------------
// Styles — one injected <style> tag, guarded so re-registration (hot
// reload, multiple nodes) never duplicates it. ComfyUI theme variables with
// literal fallbacks so the bar still looks intentional on a frontend old
// enough not to define them, and reads sanely in both light and dark themes.
// ---------------------------------------------------------------------------

let stylesInjected = false

const CSS_TEXT = `
.cprb-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  padding: 4px 6px;
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 4px;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
  overflow: hidden;
}
.cprb-buttons {
  flex: 0 0 auto;
  display: flex;
  gap: 4px;
}
.cprb-btn {
  flex: 0 0 auto;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 3px 8px;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
}
.cprb-btn:hover:not(:disabled) { background: var(--content-hover-bg, #2a2a2a); }
.cprb-btn:disabled { opacity: 0.5; cursor: default; }
.cprb-btn-small { padding: 2px 8px; }
.cprb-note {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-style: italic;
  color: var(--descrip-text, #999);
}
.cprb-note:empty { display: none; }
.cprb-status {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-align: right;
  color: var(--descrip-text, #999);
}
.cprb-status:empty { display: none; }
.cprb-status-error { color: var(--error-text, #ff4444); }
.cprb-picker-backdrop {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.5);
  z-index: 10000;
}
.cprb-picker {
  display: flex;
  flex-direction: column;
  width: min(480px, 90vw);
  max-height: min(520px, 80vh);
  background: var(--comfy-menu-bg, #262626);
  border: 1px solid var(--border-color, #444);
  border-radius: 6px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  font-family: inherit;
  font-size: 11px;
  color: var(--input-text, #ccc);
}
.cprb-picker-header {
  flex: 0 0 auto;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color, #444);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--descrip-text, #999);
}
.cprb-picker-list {
  flex: 1 1 auto;
  min-height: 120px;
  overflow-y: auto;
  padding: 4px;
}
.cprb-picker-row {
  padding: 5px 8px;
  border-radius: 3px;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cprb-picker-row:hover { background: var(--content-hover-bg, #2a2a2a); }
.cprb-picker-status,
.cprb-picker-empty {
  padding: 10px;
  color: var(--descrip-text, #999);
  font-style: italic;
}
.cprb-picker-error { color: var(--error-text, #ff4444); font-style: normal; }
.cprb-picker-footer {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  padding: 6px 8px;
  border-top: 1px solid var(--border-color, #444);
}
`

function injectStyles() {
  if (stylesInjected) return
  stylesInjected = true
  if (document.getElementById(STYLE_TAG_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_TAG_ID
  style.textContent = CSS_TEXT
  document.head.appendChild(style)
}

// ---------------------------------------------------------------------------
// Tiny DOM builder — this pack is vanilla JS with no templating engine.
// ---------------------------------------------------------------------------

/**
 * @param {string} tag
 * @param {{className?: string, text?: string, attrs?: Record<string,string>}} [options]
 * @param {(Node|string)[]} [children]
 * @returns {HTMLElement}
 */
function el(tag, options = {}, children = []) {
  const node = document.createElement(tag)
  if (options.className) node.className = options.className
  if (options.text !== undefined) node.textContent = options.text
  if (options.attrs) {
    for (const [key, value] of Object.entries(options.attrs)) {
      node.setAttribute(key, value)
    }
  }
  for (const child of children) {
    if (child == null) continue
    node.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
  return node
}

/**
 * *text* if it already fits in *maxChars*, else `'…' + the last
 * (maxChars - 1) characters` — i.e. truncated from the FRONT so the tail
 * (usually the most useful part of a filesystem path — the folder you're
 * actually in) stays visible. Used instead of a pure-CSS trick: a
 * `direction: rtl` + `unicode-bidi: plaintext` container (the approach
 * notebook.js's file header describes for exactly this) turned out, when
 * checked live in this pack's own preview browser, to still ellipsize the
 * TAIL and keep the head — the opposite of what's wanted here — so the
 * picker header truncates the string itself instead of leaning on that
 * CSS behavior. `text-overflow: ellipsis` stays on `.cprb-picker-header`
 * purely as a safety net for a *maxChars* that still doesn't fit some
 * unusually narrow viewport.
 * @param {string} text
 * @param {number} [maxChars]
 */
function frontTruncate(text, maxChars = 56) {
  const value = String(text ?? '')
  if (value.length <= maxChars) return value
  return `…${value.slice(-(maxChars - 1))}`
}

// ---------------------------------------------------------------------------
// Node / widget lookups
// ---------------------------------------------------------------------------

/**
 * @param {object} node
 * @returns {string|null} the node's ComfyUI class id, or null if it can't
 * be determined.
 */
function nodeClassOf(node) {
  if (!node) return null
  if (node.comfyClass) return node.comfyClass
  if (node.constructor && node.constructor.comfyClass) return node.constructor.comfyClass
  return null
}

/**
 * @param {object} node
 * @param {string} name
 */
function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

/**
 * Attaches the §7.3 file bar to *node* when it is a PremiereLoadTimeline or
 * PremiereSaveTimeline node; no-op for every other node type (incl.
 * PremiereGetShot, which PROTOCOL.md §7.3 doesn't touch). Never throws —
 * every failure is logged via `api.warn` and leaves the node's own widgets
 * fully functional on their own.
 * @param {object} node - LiteGraph node instance.
 */
export function attachNodeUi(node) {
  try {
    if (!node) return
    const cls = nodeClassOf(node)
    if (cls !== LOAD_CLASS && cls !== SAVE_CLASS) return
    if (attachedNodes.has(node)) return
    if (typeof node.addDOMWidget !== 'function') {
      api.warn('this ComfyUI frontend has no addDOMWidget; file bar not attached')
      return
    }
    attachedNodes.add(node)
    injectStyles()

    if (cls === LOAD_CLASS) attachLoadUi(node)
    else attachSaveUi(node)
  } catch (error) {
    api.warn('attachNodeUi failed', error)
  }
}

// ---------------------------------------------------------------------------
// Remote gating (PROTOCOL.md §7.1/§7.2) — `GET /cprb/config` is cached at
// MODULE scope (every attached node shares one fetch) with a short TTL, and
// concurrent callers de-dupe onto one in-flight promise. Ported from
// notebook.js's identical fetchConfig()/getConfig().
// ---------------------------------------------------------------------------

const CONFIG_CACHE_TTL_MS = 60000

let cachedConfig = null
let cachedConfigAt = 0
let cachedConfigPromise = null

function fetchConfig() {
  if (cachedConfigPromise) return cachedConfigPromise
  cachedConfigPromise = api
    .getJson('/cprb/config')
    .then((data) => {
      cachedConfig = data
      cachedConfigAt = Date.now()
      return data
    })
    .finally(() => {
      cachedConfigPromise = null
    })
  return cachedConfigPromise
}

function getConfig() {
  if (cachedConfig && Date.now() - cachedConfigAt < CONFIG_CACHE_TTL_MS) {
    return Promise.resolve(cachedConfig)
  }
  return fetchConfig()
}

/**
 * Refreshes `state.isLocal` from (cached) `/cprb/config` and applies it to
 * the bar's buttons. Never throws: a failed fetch is logged and leaves
 * `state.isLocal` whatever it already was (`null`/unknown reads as "local"
 * everywhere this is checked with `=== false`) — this fails OPEN rather
 * than hiding the buttons over a network hiccup.
 */
async function refreshGating(state) {
  let config
  try {
    config = await getConfig()
  } catch (error) {
    api.warn('could not load /cprb/config; treating this node as local', error)
    return
  }
  state.isLocal = config?.is_local !== false
  applyGating(state)
}

function applyGating(state) {
  const remote = state.isLocal === false
  for (const button of state.gatedButtons) {
    button.style.display = remote ? 'none' : ''
  }
  if (state.noteEl) {
    state.noteEl.textContent = remote ? 'Host machine only' : ''
    state.noteEl.title = remote
      ? 'File browsing and folder reveal only work when ComfyUI is running on this machine (PROTOCOL.md §7.1).'
      : ''
  }
}

// ---------------------------------------------------------------------------
// Shared bar chrome: buttons row + note + status line, and the
// addDOMWidget wiring both nodes attach it through.
// ---------------------------------------------------------------------------

function setStatus(state, message, isError = false) {
  if (!state.statusEl) return
  state.statusEl.textContent = message || ''
  state.statusEl.title = message || ''
  state.statusEl.classList.toggle('cprb-status-error', Boolean(isError))
}

/**
 * @param {object} state
 * @param {HTMLElement[]} buttonEls - in display order; all are hidden
 * together when `applyGating` decides this viewer is remote.
 * @returns {HTMLElement} the bar's root element.
 */
function buildBar(state, buttonEls) {
  state.noteEl = el('div', { className: 'cprb-note' })
  state.statusEl = el('div', { className: 'cprb-status' })
  state.gatedButtons = buttonEls
  const buttons = el('div', { className: 'cprb-buttons' }, buttonEls)
  return el('div', { className: 'cprb-bar' }, [buttons, state.noteEl, state.statusEl])
}

/**
 * Wraps `node.addDOMWidget` and excludes the bar from both the workflow
 * JSON and the API prompt — it holds no value of its own, it's purely a
 * frontend affordance over the node's real widgets (PROTOCOL.md §7.3's
 * "node class ids and widget names are untouched"). Same two independent
 * non-serialization flags notebook.js's attachDomWidget() uses: `serialize:
 * false` in options (checked by the API-prompt path) and
 * `domWidget.serialize`/`serializeValue` (checked by workflow
 * serialize/configure).
 */
function attachBarWidget(node, barEl) {
  const domWidget = node.addDOMWidget(WIDGET_NAME, WIDGET_TYPE, barEl, {
    hideOnZoom: true,
    serialize: false,
    getMinHeight: () => BAR_HEIGHT,
    getMaxHeight: () => BAR_HEIGHT
  })
  domWidget.serialize = false
  domWidget.serializeValue = () => undefined
  return domWidget
}

/** Chains `node.onRemoved` to close a picker this node might have open —
 * mirrors notebook.js's wireNodeCleanup()/teardown(). The picker overlay is
 * a single module-level singleton (see PICKER_OVERLAY_ID), so this simply
 * closes "the" picker if one happens to be open; ported as-is from the
 * sibling, which has the exact same singleton-picker teardown. */
function wireCleanup(state) {
  const node = state.node
  const originalOnRemoved = node.onRemoved
  node.onRemoved = function (...args) {
    let result
    if (typeof originalOnRemoved === 'function') {
      try {
        result = originalOnRemoved.apply(this, args)
      } catch (error) {
        api.warn('original node onRemoved threw', error)
      }
    }
    try {
      closePicker(state)
    } catch (error) {
      api.warn('cprb file bar teardown failed', error)
    }
    return result
  }
}

/** Runs *fn* with *button* disabled for the duration, guarding against a
 * double-click firing a second overlapping request. Plain DOM
 * `button.disabled` — unrelated to the litegraph `widget.disabled` finding
 * in the file header; this is a real `<button>` inside our own DOM widget,
 * not a litegraph text widget. */
async function runButtonAction(button, fn) {
  if (button.disabled) return
  button.disabled = true
  try {
    await fn()
  } finally {
    button.disabled = false
  }
}

// ---------------------------------------------------------------------------
// Server-path helpers — best-effort join/split that tolerates either a
// POSIX or a Windows (incl. UNC) server, since the frontend has no other
// way to know which. Ported from notebook.js's identical joinServerPath()/
// dirnameOfServerPath(); looksAbsolutePath() is new, needed for PROTOCOL.md
// §7.3's "starting dir" rule.
// ---------------------------------------------------------------------------

function looksAbsolutePath(value) {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  if (!trimmed) return false
  if (trimmed.startsWith('/')) return true // POSIX
  if (/^[a-zA-Z]:[\\/]/.test(trimmed)) return true // Windows drive, e.g. C:\ or C:/
  if (trimmed.startsWith('\\\\')) return true // UNC, e.g. \\server\share
  return false
}

function joinServerPath(dir, name) {
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/'
  return dir.endsWith(sep) ? `${dir}${name}` : `${dir}${sep}${name}`
}

function dirnameOfServerPath(path) {
  const trimmed = typeof path === 'string' ? path.trim() : ''
  if (!trimmed) return null
  const sep = trimmed.includes('\\') && !trimmed.includes('/') ? '\\' : '/'
  const idx = trimmed.lastIndexOf(sep)
  if (idx <= 0) return null
  return trimmed.slice(0, idx)
}

// ---------------------------------------------------------------------------
// Modal picker (PROTOCOL.md §7.3's Browse…) — attached to `document.body`,
// not nested inside the node's own DOM (the node can be as small as
// BAR_HEIGHT, far too cramped for a file browser, and litegraph can
// reposition/clip a DOM widget during pan/zoom). Ported from notebook.js's
// openBrowsePicker()/closeBrowsePicker()/loadPickerDir()/
// renderPickerDialog()/buildPickerFooter(), trimmed to files-only (no
// "new folder"/rename) and parameterized on PICKER_EXT.
// ---------------------------------------------------------------------------

function closePicker(state) {
  document.getElementById(PICKER_OVERLAY_ID)?.remove()
  if (state.pickerKeydownHandler) {
    window.removeEventListener('keydown', state.pickerKeydownHandler)
    state.pickerKeydownHandler = null
  }
}

function openPicker(state) {
  closePicker(state) // only one picker at a time, ever

  const backdrop = el('div', { className: 'cprb-picker-backdrop', attrs: { id: PICKER_OVERLAY_ID } })
  const dialog = el('div', { className: 'cprb-picker' })
  backdrop.append(dialog)
  backdrop.addEventListener('mousedown', (event) => {
    if (event.target === backdrop) closePicker(state)
  })
  dialog.addEventListener('mousedown', (event) => event.stopPropagation())
  document.body.append(backdrop)

  state.pickerKeydownHandler = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      closePicker(state)
    }
  }
  window.addEventListener('keydown', state.pickerKeydownHandler)

  // PROTOCOL.md §7.3: "starting dir: the widget's current value's folder
  // when it looks absolute, else omit dir (server defaults to output_dir)".
  const currentValue = state.fileWidget.value
  const startDir = looksAbsolutePath(currentValue) ? dirnameOfServerPath(currentValue) : null
  loadPickerDir(state, dialog, startDir)
}

async function loadPickerDir(state, dialog, dir) {
  dialog.replaceChildren(el('div', { className: 'cprb-picker-status', text: 'Loading…' }))
  let data
  try {
    data = await api.getJson('/cprb/fs/list', dir ? { dir, ext: PICKER_EXT } : { ext: PICKER_EXT })
  } catch (error) {
    api.warn('fs/list failed', error)
    dialog.replaceChildren(
      el('div', { className: 'cprb-picker-header', text: 'Browse' }),
      el('div', {
        className: 'cprb-picker-status cprb-picker-error',
        text: `Could not list folder: ${error.message}`
      }),
      buildPickerFooter(state)
    )
    return
  }
  renderPickerDialog(state, dialog, data)
}

function renderPickerDialog(state, dialog, data) {
  const header = el('div', {
    className: 'cprb-picker-header',
    text: frontTruncate(data.dir),
    attrs: { title: data.dir }
  })
  const list = el('div', { className: 'cprb-picker-list' })

  if (data.parent) {
    const upRow = el('div', { className: 'cprb-picker-row', text: '.. (parent folder)' })
    upRow.addEventListener('click', () => loadPickerDir(state, dialog, data.parent))
    list.append(upRow)
  }
  for (const name of data.dirs || []) {
    // Trailing slash marks a folder row — no emoji, plain and unambiguous.
    const row = el('div', { className: 'cprb-picker-row', text: `${name}/` })
    row.addEventListener('click', () => loadPickerDir(state, dialog, joinServerPath(data.dir, name)))
    list.append(row)
  }
  for (const name of data.files || []) {
    const row = el('div', { className: 'cprb-picker-row', text: name })
    row.addEventListener('click', () => {
      closePicker(state)
      setFileWidgetValue(state, joinServerPath(data.dir, name))
    })
    list.append(row)
  }
  if (!data.parent && !(data.dirs || []).length && !(data.files || []).length) {
    list.append(el('div', { className: 'cprb-picker-empty', text: `No subfolders or ${PICKER_EXT} files here.` }))
  }

  dialog.replaceChildren(header, list, buildPickerFooter(state))
}

function buildPickerFooter(state) {
  const cancelBtn = el('button', { className: 'cprb-btn cprb-btn-small', text: 'Cancel' })
  cancelBtn.addEventListener('click', () => closePicker(state))
  return el('div', { className: 'cprb-picker-footer' }, [cancelBtn])
}

/** Writes *value* through the `file_path` widget's real setter + callback
 * (PROTOCOL.md §7.3), so picking a file behaves exactly like typing it in.
 * Ported from notebook.js's identical setFileWidgetValue(). */
function setFileWidgetValue(state, value) {
  const widget = state.fileWidget
  if (widget.value === value) return
  widget.value = value
  try {
    widget.callback?.(value)
  } catch (error) {
    api.warn('file_path widget callback threw', error)
  }
  state.node.graph?.setDirtyCanvas(true, true)
}

// ---------------------------------------------------------------------------
// PremiereLoadTimeline: Browse… + Open folder
// ---------------------------------------------------------------------------

function attachLoadUi(node) {
  const fileWidget = findWidget(node, 'file_path')
  if (!fileWidget) {
    api.warn('PremiereLoadTimeline node is missing its file_path widget; file bar not attached')
    return
  }

  const state = {
    node,
    fileWidget,
    isLocal: null,
    pickerKeydownHandler: null,
    noteEl: null,
    statusEl: null,
    gatedButtons: [],
    browseBtn: null,
    openFolderBtn: null
  }

  state.browseBtn = el('button', {
    className: 'cprb-btn',
    text: 'Browse…',
    attrs: { title: 'Pick a Premiere-exported .xml file on the server' }
  })
  state.openFolderBtn = el('button', {
    className: 'cprb-btn',
    text: 'Open folder',
    attrs: { title: "Reveal this file's folder on the server machine" }
  })

  state.browseBtn.addEventListener('click', () => {
    if (state.browseBtn.disabled) return
    openPicker(state)
  })
  state.openFolderBtn.addEventListener('click', () => {
    runButtonAction(state.openFolderBtn, () => onOpenFolderClickLoad(state)).catch((error) =>
      api.warn('open folder failed', error)
    )
  })

  const bar = buildBar(state, [state.browseBtn, state.openFolderBtn])
  attachBarWidget(node, bar)
  wireCleanup(state)

  refreshGating(state).catch((error) => api.warn('initial config load failed', error))
}

async function onOpenFolderClickLoad(state) {
  const path = String(state.fileWidget.value || '').trim()
  if (!path) {
    // PROTOCOL.md §7.3: "empty path ⇒ a short inline message, never a
    // thrown error" — checked client-side so we never even call the route.
    setStatus(state, 'Pick a file first — nothing to open yet.')
    return
  }
  try {
    await api.postJson('/cprb/open_folder', { path })
    setStatus(state, '')
  } catch (error) {
    api.warn('open_folder failed', error)
    setStatus(state, error.message || 'Could not open folder.', true)
  }
}

// ---------------------------------------------------------------------------
// PremiereSaveTimeline: Open output folder
// ---------------------------------------------------------------------------

function attachSaveUi(node) {
  const sequenceNameWidget = findWidget(node, 'sequence_name')
  if (!sequenceNameWidget) {
    api.warn('PremiereSaveTimeline node is missing its sequence_name widget; file bar not attached')
    return
  }

  const state = {
    node,
    sequenceNameWidget,
    isLocal: null,
    pickerKeydownHandler: null,
    noteEl: null,
    statusEl: null,
    gatedButtons: [],
    openOutputFolderBtn: null
  }

  state.openOutputFolderBtn = el('button', {
    className: 'cprb-btn',
    text: 'Open output folder',
    attrs: { title: "Reveal this sequence's output folder on the server machine" }
  })
  state.openOutputFolderBtn.addEventListener('click', () => {
    runButtonAction(state.openOutputFolderBtn, () => onOpenOutputFolderClick(state)).catch((error) =>
      api.warn('open output folder failed', error)
    )
  })

  const bar = buildBar(state, [state.openOutputFolderBtn])
  attachBarWidget(node, bar)
  wireCleanup(state)

  refreshGating(state).catch((error) => api.warn('initial config load failed', error))
}

async function onOpenOutputFolderClick(state) {
  const sequenceName = String(state.sequenceNameWidget.value || '')
  try {
    const data = await api.getJson('/cprb/timeline_dir', { sequence_name: sequenceName })
    if (!data || data.exists === false) {
      // PROTOCOL.md §7.3: "Before the first run the folder may not exist
      // yet: the button says so rather than erroring."
      setStatus(state, 'Not created yet — run the node first.')
      return
    }
    await api.postJson('/cprb/open_folder', { path: data.dir })
    setStatus(state, '')
  } catch (error) {
    api.warn('open output folder failed', error)
    setStatus(state, error.message || 'Could not open folder.', true)
  }
}
