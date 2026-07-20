/**
 * fs-browse dialog v1 — synced from STANDARD-fs-browse.md
 *
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
 * 2026-07-19: two additions on top of the port, both local to this file,
 * both in service of PROTOCOL.md §7.2's same-day `ROOTS` sentinel fix
 * (the picker could reach the top of C:\ but no further): (1) `..`/`dirs`
 * rows now branch on `FS_ROOTS` so a drive root's `parent` climbs to the
 * drive list instead of dead-ending, and a drive-list row navigates
 * straight to that drive rather than joining it onto the literal string
 * `"ROOTS"`; (2) a path bar pinned above the navigable area — its own
 * `.cprb-picker-content` child now, so replacing that child on every
 * navigation never wipes the bar — where typing/pasting ANY absolute path
 * (a UNC share, another drive) jumps there directly, independent of
 * what `ROOTS` happens to enumerate on this machine; a bad path 400s into
 * the same inline error state the rest of the picker already used.
 *
 * STANDARDIZED 2026-07-19 (../../STANDARD-fs-browse.md, the cross-plugin
 * "server filesystem Browse" contract shared with cpsb's/epsnodes' own
 * pickers): `GET /cprb/fs/list` reshaped its response to NAMES-ONLY
 * `dirs`/`files` entries (`{"name"}` / `{"name","size","mtime"}`) plus a
 * `sep` field — this picker now joins `dir` + `sep` + `name` itself
 * (`joinServerPath()`, now preferring the server-reported `sep` over its old
 * heuristic) instead of receiving bare path strings. The `ROOTS` sentinel's
 * listing is unchanged in spirit (still a synthetic top-level list a drive
 * root's `parent` climbs back to) but now ALSO includes this pack's own
 * default output dir and "Home" (labeled, `{"name","path"}` entries —
 * STANDARD-fs-browse.md's documented ROOTS extension) ahead of the platform
 * drives/volumes, and (2026-07-19) is synthesized on macOS/POSIX too, not
 * just Windows. Locality: cprb's `FS_LIST_LOCAL_ONLY` build-time flag stays
 * `True` (unchanged loopback-only posture, PROTOCOL.md §7.1).
 *
 * 2026-07-19: added growing `video_N` INPUT sockets on PremiereSaveTimeline
 * only (PROTOCOL.md §7.3 "Growing video inputs" — owner report: "I can only
 * connect one video; a new connection replaces the previous one"). The
 * backend already accepted unbounded `video_N` (§3.1's
 * `_FlexibleOptionalVideoInputs`); only the socket itself needed to grow.
 * See `wireVideoInputGrowth()`/`convergeVideoInputs()` below for the
 * algorithm; both cite the exact litegraph fork source lines
 * (`addInput`/`removeInput`/`onConnectionsChange`/`configure`, all in
 * `LGraphNode.ts`) this was checked against, plus core's own
 * `PrimitiveNode` (`widgetInputs.ts`) for the closest in-tree precedent —
 * rgthree's Power Lora Loader was NOT usable as a reference here (per the
 * task that produced this addition) since it grows WIDGETS, not sockets.
 * Load/Get Shot are untouched; this is Save-only.
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
const BAR_HEIGHT = 34

/** §7.2's `fs/list` extension allowlist for the Load node's picker. */
const PICKER_EXT = '.xml'

/** STANDARD-fs-browse.md's `fs/list` sentinel for "the top level" — this
 * pack's own default output dir (labeled) + Home, then every Windows drive
 * or every macOS `/Volumes` mount. A drive root's own `parent` echoes this
 * back too, so the picker's `..` row climbs here instead of getting stuck at
 * the top of one drive (the 2026-07-19 fix). */
const FS_ROOTS = 'ROOTS'

const STYLE_TAG_ID = 'cprb-node-ui-styles'
const PICKER_OVERLAY_ID = 'cprb-picker-overlay'

/** PROTOCOL.md §3.1's unbounded `video_N` socket names on
 * PremiereSaveTimeline — matched against `INodeInputSlot.name`, never
 * against the backend's own key order, so a socket's ARRAY position in
 * `node.inputs` is never assumed to equal its numeric `N` (see
 * videoInputEntries() below). */
const VIDEO_INPUT_RE = /^video_(\d+)$/

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
.cprb-picker-pathbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color, #444);
}
.cprb-picker-path-input {
  flex: 1 1 auto;
  min-width: 0;
  background: var(--comfy-input-bg, #1e1e1e);
  border: 1px solid var(--border-color, #444);
  color: var(--input-text, #ccc);
  border-radius: 4px;
  padding: 4px 6px;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 10.5px;
}
.cprb-picker-path-input:focus {
  outline: 1px solid var(--input-focus-border, #5c9dff);
}
.cprb-picker-content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
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
  // Fixed compact height, the ROBUST way. `getMinHeight`/`getMaxHeight`
  // alone are IGNORED for a small standalone DOM widget on the Vue-node
  // renderer (verified live 2026-07-19: the bar collapsed to ~7px and its
  // buttons clipped past the node's bottom edge — the "broken visuals" the
  // owner reported). This frontend sizes a widget from the classic
  // `computeSize`/`computedHeight` pair instead, and the `size-full` DOM
  // wrapper then needs the element itself to carry the height. Setting all
  // three is what actually reserves the row. (notebook.js's file panel only
  // looked fine because it rides inside that node's large FILL widget.)
  domWidget.computeSize = (width) => [width, BAR_HEIGHT]
  domWidget.computedHeight = BAR_HEIGHT
  barEl.style.height = `${BAR_HEIGHT}px`
  barEl.style.minHeight = `${BAR_HEIGHT}px`
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

/**
 * @param {string} dir
 * @param {string} name
 * @param {string} [sep] - STANDARD-fs-browse.md's server-reported `sep`
 * (`GET /cprb/fs/list`'s response field) — preferred when given, since it's
 * authoritative for the machine actually being browsed. Falls back to the
 * old heuristic (present-backslash-without-forward-slash) only for call
 * sites with no response to read a `sep` from (e.g. `dirnameOfServerPath`
 * below, seeding the picker from a widget's already-typed value).
 */
function joinServerPath(dir, name, sep) {
  const separator = sep || (dir.includes('\\') && !dir.includes('/') ? '\\' : '/')
  return dir.endsWith(separator) ? `${dir}${name}` : `${dir}${separator}${name}`
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
  state.pickerPathInputEl = null
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

  // The navigable area is its own child so the path bar built next (which
  // sits above it, for the picker's whole lifetime) is never wiped out by
  // loadPickerDir()'s replaceChildren() calls.
  const content = el('div', { className: 'cprb-picker-content' })
  dialog.append(buildPickerPathBar(state, content), content)

  // PROTOCOL.md §7.3: "starting dir: the widget's current value's folder
  // when it looks absolute, else omit dir (server defaults to output_dir)".
  const currentValue = state.fileWidget.value
  const startDir = looksAbsolutePath(currentValue) ? dirnameOfServerPath(currentValue) : null
  loadPickerDir(state, content, startDir)
}

/**
 * The picker's "type/paste any absolute path" escape hatch — a UNC share
 * or another drive, independent of §7.2's drive enumeration (which only
 * ever lists drives that already exist on the SERVER machine; a mapped
 * NAS path typed here doesn't need to be one of them). Pinned above
 * *content* for the picker's whole lifetime. Go and Enter both navigate
 * through the SAME loadPickerDir() every row uses, so a bad path surfaces
 * the same inline error as any other failed navigation (never closes the
 * dialog).
 * @param {object} state
 * @param {HTMLElement} content - the picker's navigable-area container.
 * @returns {HTMLElement} the path bar's root element.
 */
function buildPickerPathBar(state, content) {
  const input = el('input', {
    className: 'cprb-picker-path-input',
    attrs: {
      type: 'text',
      placeholder: String.raw`\\server\share or D:\clips`,
      spellcheck: 'false',
      autocomplete: 'off'
    }
  })
  const goBtn = el('button', {
    className: 'cprb-btn cprb-btn-small',
    text: 'Go',
    attrs: { title: 'Go to this path' }
  })

  const goToTypedPath = () => {
    const typed = input.value.trim()
    if (!typed) return
    loadPickerDir(state, content, typed)
  }
  goBtn.addEventListener('click', goToTypedPath)
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    goToTypedPath()
  })

  state.pickerPathInputEl = input
  return el('div', { className: 'cprb-picker-pathbar' }, [input, goBtn])
}

async function loadPickerDir(state, content, dir) {
  content.replaceChildren(el('div', { className: 'cprb-picker-status', text: 'Loading…' }))
  let data
  try {
    data = await api.getJson('/cprb/fs/list', dir ? { dir, ext: PICKER_EXT } : { ext: PICKER_EXT })
  } catch (error) {
    api.warn('fs/list failed', error)
    // PROTOCOL.md §7.2: a bad/unreadable `dir` (typed by hand or not) is a
    // 400 — shown inline, right here, and the dialog otherwise stays put.
    content.replaceChildren(
      el('div', {
        className: 'cprb-picker-header',
        text: frontTruncate(dir || 'Browse'),
        attrs: { title: dir || '' }
      }),
      el('div', {
        className: 'cprb-picker-status cprb-picker-error',
        text: `Could not list folder: ${error.message}`
      }),
      buildPickerFooter(state)
    )
    return
  }
  renderPickerDialog(state, content, data)
}

function renderPickerDialog(state, content, data) {
  // Keep the path bar in sync with where navigation landed — clicking
  // through folder rows shouldn't leave stale typed text sitting above
  // them. The synthetic ROOTS listing isn't a real path to show/retype,
  // so clear it instead.
  if (state.pickerPathInputEl) {
    state.pickerPathInputEl.value = data.dir === FS_ROOTS ? '' : data.dir
  }

  const isRootsList = data.dir === FS_ROOTS
  // "Top Level" (not "Drives" — 2026-07-19: the ROOTS listing now also
  // carries this pack's own default output dir + "Home" ahead of the
  // platform drives/volumes, so "Drives" alone stopped being accurate).
  const headerText = isRootsList ? 'Top Level' : data.dir
  const header = el('div', {
    className: 'cprb-picker-header',
    text: frontTruncate(headerText),
    attrs: { title: headerText }
  })
  const list = el('div', { className: 'cprb-picker-list' })

  if (data.parent !== null) {
    const upRow = el('div', { className: 'cprb-picker-row', text: '.. (parent folder)' })
    // `data.parent` is either an absolute path or the FS_ROOTS sentinel (a
    // drive root climbing back to the drive list) — either way it's just
    // the next `dir=` to fetch, no special-casing needed here.
    upRow.addEventListener('click', () => loadPickerDir(state, content, data.parent))
    list.append(upRow)
  }
  for (const dir of data.dirs || []) {
    // STANDARD-fs-browse.md: at the ROOTS listing, each entry is
    // independently rooted (this pack's default output dir, "Home", a
    // drive/volume) and carries its own `path` — navigate there as-is.
    // Everywhere else, entries are names-only: shown with a trailing slash
    // (marks a folder row — no emoji, plain and unambiguous) and joined
    // onto the current `dir` + the server-reported `sep`.
    const row = el('div', { className: 'cprb-picker-row', text: isRootsList ? dir.name : `${dir.name}/` })
    const target = isRootsList ? dir.path : joinServerPath(data.dir, dir.name, data.sep)
    row.addEventListener('click', () => loadPickerDir(state, content, target))
    list.append(row)
  }
  for (const file of data.files || []) {
    const row = el('div', { className: 'cprb-picker-row', text: file.name })
    row.addEventListener('click', () => {
      closePicker(state)
      setFileWidgetValue(state, joinServerPath(data.dir, file.name, data.sep))
    })
    list.append(row)
  }
  if (data.parent === null && !(data.dirs || []).length && !(data.files || []).length) {
    list.append(el('div', { className: 'cprb-picker-empty', text: `No subfolders or ${PICKER_EXT} files here.` }))
  }

  content.replaceChildren(header, list, buildPickerFooter(state))
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
    pickerPathInputEl: null,
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
// PremiereSaveTimeline: growing `video_N` inputs (PROTOCOL.md §7.3 "Growing
// video inputs"). rgthree/core image-batch pattern: exactly one trailing
// EMPTY `video_N` socket exists at all times; connecting it grows a fresh
// one after it; disconnecting collapses a run of trailing empties back to
// one. A CONNECTED slot is never renamed or removed, even mid-range (a
// disconnected middle slot is left as a gap, per spec).
//
// litegraph fork APIs this was checked against (this pack's own scratchpad
// checkout of ComfyUI_frontend):
//   - `LGraphNode.addInput(name, type, extra_info?)` — LGraphNode.ts:1700.
//   - `LGraphNode.removeInput(slot: number)` — LGraphNode.ts:1726; splices
//     `node.inputs` by ARRAY POSITION and disconnects first if linked.
//   - `onConnectionsChange(type, index, isConnected, link_info,
//     inputOrOutput)` — signature at LGraphNode.ts:623; fired from (at
//     least) connect() (:3007-3021), disconnectInput()/disconnectOutput()
//     (:3156-3169, :3208-3225, :3321-3334), AND configure() (:882-893, see
//     below).
// ---------------------------------------------------------------------------

/**
 * *node*'s current `video_N` sockets, one entry per slot whose name matches
 * `VIDEO_INPUT_RE`. `idx` is the slot's position in `node.inputs` (what
 * `removeInput(idx)` wants); `n` is its parsed number (what "trailing"
 * below is computed from) — kept separate because this file never assumes
 * the two stay in lockstep (a hand-edited workflow JSON could in principle
 * save them out of order; `configure()`'s own input-merge logic also
 * appends "extra" saved inputs after the class-def ones by NAME lookup, not
 * by number).
 * @param {object} node
 * @returns {{idx: number, n: number, input: object, connected: boolean}[]}
 */
function videoInputEntries(node) {
  const entries = []
  const inputs = node.inputs || []
  for (let idx = 0; idx < inputs.length; idx++) {
    const input = inputs[idx]
    const match = input && VIDEO_INPUT_RE.exec(input.name)
    if (!match) continue
    entries.push({ idx, n: Number(match[1]), input, connected: input.link != null })
  }
  return entries
}

/**
 * Adds a fresh, empty `video_{n}` VIDEO input. Clones `type`/`shape` off
 * *template* (normally the existing `video_1`) instead of hardcoding them,
 * so a dynamically added slot looks/behaves exactly like the one ComfyUI's
 * own class-def machinery created for `video_1` (`{shape:
 * RenderShape.HollowCircle}` for every optional input, per this fork's
 * `litegraphService.ts` `addInputSocket()`) without this bundler-free
 * vanilla-JS file needing to import that enum just to repeat the same
 * numeric value.
 * @param {object} node
 * @param {number} n
 * @param {object|null} template - an existing `video_N` input slot, or
 * `null` on the defensive first-run path where none exists yet.
 */
function addVideoInput(node, n, template) {
  const type = template?.type ?? 'VIDEO'
  const extraInfo = template && template.shape !== undefined ? { shape: template.shape } : undefined
  return node.addInput(`video_${n}`, type, extraInfo)
}

/**
 * Grows/shrinks *node*'s `video_N` sockets to this file's one invariant:
 * every CONNECTED `video_N` keeps its name/link untouched (never
 * renumbered, never removed — a disconnected MIDDLE slot is left exactly
 * as a gap), and exactly ONE trailing EMPTY slot exists, numbered one past
 * the highest connected `video_N` (`video_1` itself, empty, when nothing
 * is connected at all).
 *
 * Idempotent — every call site below calls this unconditionally rather
 * than trying to decide "did this particular event actually need a
 * change", so calling it redundantly (e.g. on a node that's already
 * converged) is the expected common case, not an edge case.
 * @param {object} node
 */
function convergeVideoInputs(node) {
  if (!node.inputs) return
  const entries = videoInputEntries(node)
  if (entries.length === 0) {
    // Defensive only: PremiereSaveTimeline's INPUT_TYPES always declares
    // `video_1` (cprb/nodes_save.py's _FlexibleOptionalVideoInputs), so
    // ComfyUI's own node construction gives every node this one slot
    // before nodeCreated (and this function) ever runs. Kept in case a
    // future INPUT_TYPES change ever drops that default.
    addVideoInput(node, 1, null)
    return
  }

  let highestConnectedN = 0
  for (const entry of entries) {
    if (entry.connected && entry.n > highestConnectedN) highestConnectedN = entry.n
  }
  const desiredSpareN = highestConnectedN + 1
  const trailingEmpties = entries.filter((entry) => !entry.connected && entry.n > highestConnectedN)

  if (trailingEmpties.length === 1 && trailingEmpties[0].n === desiredSpareN) {
    return // Already converged — the common case on every no-op call.
  }

  // Highest array index first: removeInput() splices `node.inputs` by
  // POSITION and shifts every later slot's link bookkeeping down by one
  // (LGraphNode.ts:1726-1746), so removing several in one pass low-to-high
  // would invalidate the remaining queued indices.
  const removeIdxs = trailingEmpties.map((entry) => entry.idx).sort((a, b) => b - a)
  for (const idx of removeIdxs) node.removeInput(idx)

  addVideoInput(node, desiredSpareN, entries[0].input)
}

/**
 * Chains *node*'s `configure` and `onConnectionsChange` so its `video_N`
 * sockets converge per convergeVideoInputs() above. Called once from
 * attachSaveUi(), PremiereSaveTimeline only.
 *
 * Two chained hooks, not one:
 *
 * - `onConnectionsChange` reacts to LIVE connect/disconnect (dragging a
 *   link on/off a `video_N` slot) — the ordinary case.
 * - `configure` (LGraphNode.ts's own restore path — used for a workflow
 *   load, undo/redo, AND copy/paste; all three route through this one
 *   method) is chained separately because its restore loop
 *   (LGraphNode.ts:882-893) calls `onConnectionsChange` once per restored
 *   input slot with `isConnected` hardcoded `true` — literally
 *   `this.onConnectionsChange?.(NodeSlotType.INPUT, i, true, link, input)`
 *   — even for a slot whose own `input.link` is null (i.e. it lied about
 *   being connected). Worse, it does this while still iterating
 *   `this.inputs.entries()` LIVE off the very array `removeInput()` would
 *   need to splice, so reacting mid-loop risks corrupting the in-flight
 *   restore, not just misreading it.
 *
 *   `state.restoring` blanks onConnectionsChange's reaction for exactly
 *   the duration of the ORIGINAL configure() call, and a single
 *   convergeVideoInputs() pass runs in `finally`, once `this.inputs` is
 *   completely stable. Same guard shape core's own `PrimitiveNode` uses
 *   around `app.configuringGraph` (`widgetInputs.ts`'s
 *   `onConnectionsChange`, `if (app.configuringGraph) return`) — scoped to
 *   this one node's own `configure()` instead of the whole graph's
 *   load, so copy/paste and undo/redo converge too, not only a fresh
 *   workflow load, and without this file needing to import `app` at all.
 * @param {object} node
 */
function wireVideoInputGrowth(node) {
  const state = { restoring: false }

  const originalConfigure = node.configure
  node.configure = function (...args) {
    state.restoring = true
    try {
      return originalConfigure.apply(this, args)
    } finally {
      state.restoring = false
      try {
        convergeVideoInputs(this)
      } catch (error) {
        api.warn('convergeVideoInputs (post-configure) failed', error)
      }
    }
  }

  const originalOnConnectionsChange = node.onConnectionsChange
  node.onConnectionsChange = function (type, index, isConnected, linkInfo, inputOrOutput) {
    let result
    if (typeof originalOnConnectionsChange === 'function') {
      result = originalOnConnectionsChange.apply(this, arguments)
    }
    // Outputs are never named video_N (PremiereSaveTimeline's only output
    // is `timeline_path`), and onConnectionsChange always fires on the
    // node whose OWN slot changed (LGraphNode.ts's call sites invoke it as
    // `this.onConnectionsChange?.(...)` / `target.onConnectionsChange?.(...)`
    // — always the node that owns the slot) — so matching *this node's*
    // slot by name discriminates input-side video_N changes without this
    // file needing the NodeSlotType enum (LGraphNode.ts:623-630) at all.
    if (!state.restoring && VIDEO_INPUT_RE.test(inputOrOutput?.name || '')) {
      try {
        convergeVideoInputs(this)
      } catch (error) {
        api.warn('convergeVideoInputs (onConnectionsChange) failed', error)
      }
    }
    return result
  }

  // A brand-new node already satisfies the invariant via its class-def
  // default `video_1` socket — a no-op here, cheap insurance against a
  // future INPUT_TYPES default change.
  convergeVideoInputs(node)
}

// ---------------------------------------------------------------------------
// PremiereSaveTimeline: Open output folder
// ---------------------------------------------------------------------------

function attachSaveUi(node) {
  // Independent of the file bar below (and wired first, ahead of its
  // sequence_name-widget guard) — PROTOCOL.md §7.3 "Growing video inputs"
  // is its own concern from the Browse…/Open-folder bar, so a future bar
  // regression can never take socket growth down with it, and vice versa.
  try {
    wireVideoInputGrowth(node)
  } catch (error) {
    api.warn('wireVideoInputGrowth failed', error)
  }

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
    pickerPathInputEl: null,
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
