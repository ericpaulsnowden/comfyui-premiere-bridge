/**
 * @file The M2 RETURN-direction relay (PROTOCOL.md §11): takes the
 * `cprb.export_ready` frontend event the server relays out of the Premiere
 * panel and writes its path (plus a clip's source in/out) into the matching
 * source node(s) in the open graph.
 *
 * Why a frontend module at all: the panel's "Frame → ComfyUI" / "Clip →
 * ComfyUI" buttons produce a PATH, but a path is only useful once it is
 * sitting in a node's widget. Nothing on the Python side can reach into the
 * user's live graph, so the hop from event → widget has to happen here. The
 * §11 nodes (`PremiereFrameSource`, `PremiereClipSource`) keep their `path`
 * widget ORDINARY and VISIBLE — this module writes it, but the user can
 * still read it and type it.
 *
 * The listener + toast shape is `send_result.js`'s, and for the same reason
 * its header records: NOTHING in ComfyUI renders a node's `ui.text`, so a
 * toast is the only reliable way to tell the user what just happened. Here
 * the visible `path` widget is a second, non-transient confirmation channel,
 * which is why the widget is deliberately not hidden. (If a toast ever
 * proves too easy to miss, `send_result.js`'s persistent on-node status line
 * is the established upgrade path; it is not duplicated here, so this module
 * owns no node chrome.)
 *
 * ── TWO KNOWN, DELIBERATE CONSEQUENCES OF THIS DESIGN ──────────────────
 *
 * 1. EVERY OPEN BROWSER TAB GETS UPDATED. The server relays `export_ready`
 *    via `PromptServer.send_sync`, which broadcasts to every connected
 *    frontend client; there is no tab-targeting mechanism in the ComfyUI
 *    websocket protocol (no "which tab asked for this?" — the request came
 *    from Premiere, not from a tab at all). So if the user has three
 *    ComfyUI tabs open, all three graphs get the path. Accepted: the
 *    single-tab case is the norm, the write is idempotent, and a stale
 *    background tab holding a fresh path is harmless because we never
 *    queue anything (see 3).
 *
 * 2. EVERY MATCHING NODE GETS THE SAME PATH. The §11 policy is "update
 *    every match in the graph", not "update the selected one" — a graph
 *    with two `Frame from Premiere` nodes feeding two different branches
 *    almost always wants the same incoming frame in both, and picking a
 *    winner would require a selection concept the event does not carry.
 *    Accepted and harmless: worst case a node the user didn't mean gets a
 *    path it will simply re-read on the next Run.
 *
 * 3. WE AUTO-RUN BY DEFAULT (owner decision 2026-07-27, reversing the
 *    original never-auto-queue policy): a button press in Premiere fills the
 *    node AND queues the workflow, exactly like the Photoshop bridge — "the
 *    user doesn't need to intervene." The `cprb.autoRun` setting (Settings →
 *    Premiere Bridge) turns it back off. What keeps consequence 1 benign now
 *    is the NONCE: the panel stamps each export_ready with a unique `nonce`,
 *    and only the FIRST tab to claim it in localStorage — which is shared
 *    across same-origin tabs, unlike anything else in this event's path —
 *    queues. A payload without a nonce (an older panel) still auto-runs;
 *    with several tabs open on mismatched panel/pack versions that can
 *    queue once per tab, accepted until the panel updates (single-tab is
 *    the norm, and the fill itself was always broadcast anyway).
 *
 * Nothing in here may throw into the event handler: a UI relay that breaks
 * the extension is worse than one that misses an update, so every step is
 * wrapped and every failure degrades to a `warn` (+ a toast where the user
 * can act on it) with the file still safely on disk.
 */

import { app } from '../../../scripts/app.js'
import { api } from '../../../scripts/api.js'
import { warn } from './api.js'
import { getAutoRun } from './settings.js'

/**
 * §11's `kind` → the frozen node class id it targets (§8: class ids are
 * FROZEN once shipped, so this map is a contract, not a convenience).
 * `display` is the node's menu name, used verbatim in the zero-match toast
 * so the user can search for the exact string in the node picker.
 * `hasInOut` marks the kind that carries `start_seconds`/`end_seconds`, so
 * the branch below keys off this table rather than re-testing a class-id
 * string in two places.
 * `emptyPathHint` explains an empty `path` in terms of the thing that
 * actually produces it: for a frame that is a failed `exportSequenceFrame`
 * (which can return true and write nothing); for a clip it is a project
 * item with no media file, by far the likeliest real-world failure —
 * SPIKES S6-C measured 11 of 19 `ClipProjectItem`s with an empty
 * `getMediaFilePath()`.
 */
const KINDS = {
  frame: {
    nodeClass: 'PremiereFrameSource',
    display: 'Frame from Premiere',
    hasInOut: false,
    emptyPathHint:
      'The Premiere panel reported an exported frame but sent no file path — ' +
      'the export itself most likely failed. Try "Frame → ComfyUI" again, and ' +
      'check the panel log.'
  },
  clip: {
    nodeClass: 'PremiereClipSource',
    display: 'Clip from Premiere',
    hasInOut: true,
    emptyPathHint:
      'The selected clip has no media file on disk — nested sequences, titles, ' +
      'colour mattes and adjustment layers all report an empty path. Select a ' +
      'clip backed by a real file and send it again.'
  }
}

// ---------------------------------------------------------------------------
// Node / widget lookups (nodes.js's helpers, kept local — that module owns
// the §7.3 file bar and exports none of these)
// ---------------------------------------------------------------------------

/**
 * A node's Python class id. `comfyClass` is what ComfyUI's
 * `services/litegraphService.ts` stamps onto both the generated class and
 * its prototype for exactly this purpose, so it is checked first (both
 * spellings, as `nodes.js` does); `type` is the litegraph-level fallback,
 * which is what a node deserialized from a saved workflow carries before
 * anything re-registers it.
 * @param {object} node
 * @returns {string|null}
 */
function nodeClassOf(node) {
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
function findWidget(node, name) {
  return node.widgets?.find((w) => w && w.name === name)
}

/** Every node in the open graph, across both litegraph field spellings. */
function graphNodes() {
  return app.graph?._nodes ?? app.graph?.nodes ?? []
}

/**
 * Writes *value* through the widget's value AND its callback — the
 * project-family convention for a programmatic widget write (see
 * `nodes.js`'s `setWidgetValue`, ported from notebook.js): the callback is
 * where a widget's own side effects live, so skipping it makes a
 * programmatic write behave differently from the user typing the same
 * thing. Written unconditionally rather than short-circuiting on an
 * unchanged value, because re-sending the SAME clip (whose path is its own
 * media file, so it never varies) must still count as an update.
 * @returns {boolean} true if the write landed.
 */
function setWidgetValue(widget, value) {
  if (!widget) return false
  try {
    widget.value = value
  } catch (error) {
    warn(`could not set the ${widget.name} widget`, error)
    return false
  }
  try {
    widget.callback?.(value)
  } catch (error) {
    // The value is already in place; a throwing callback must not undo it.
    warn(`${widget.name} widget callback threw`, error)
  }
  return true
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

function toast(options) {
  try {
    app.extensionManager?.toast?.add?.(options)
  } catch (error) {
    warn('toast failed', error)
  }
}

/** Trailing path component, for a toast summary that fits on one line. */
function basename(path) {
  const parts = String(path || '').split(/[\\/]/)
  return parts[parts.length - 1] || String(path || '')
}

/** `n` nodes, pluralized: "1 node" / "3 nodes". */
function nodeCountLabel(n) {
  return `${n} node${n === 1 ? '' : 's'}`
}

/** A finite number formatted to 2dp, or null when the field wasn't sent. */
function seconds(value) {
  return Number.isFinite(value) ? Number(value).toFixed(2) : null
}

// ---------------------------------------------------------------------------
// Auto-run (§11.3 policy, owner decision 2026-07-27)
// ---------------------------------------------------------------------------

/** localStorage key prefix for claimed nonces; entries are pruned so the
 * store never accumulates (only the newest few claims matter). */
const NONCE_PREFIX = 'cprb-autorun:'
const NONCE_KEEP = 20

/**
 * Atomically-enough claims *nonce* for THIS tab: returns true when this tab
 * is the first to see it. localStorage is shared across same-origin tabs, so
 * whichever tab's event handler runs first wins and the rest see the claim.
 * (A true race window of a few microseconds exists between get and set —
 * accepted: the alternative is a SharedWorker, and the failure mode is one
 * duplicate queue, not corruption.) Storage failures (private windows,
 * quota) claim successfully so a broken store degrades to "every tab
 * queues", never to "nothing runs".
 * @param {unknown} nonce
 * @returns {boolean}
 */
function claimNonce(nonce) {
  if (typeof nonce !== 'string' || !nonce) return true // older panel: no dedupe possible
  const key = NONCE_PREFIX + nonce
  try {
    if (localStorage.getItem(key) !== null) return false
    localStorage.setItem(key, String(Date.now()))
    // Prune: keep the newest NONCE_KEEP claims, drop the rest.
    const mine = []
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i)
      if (k && k.startsWith(NONCE_PREFIX)) mine.push(k)
    }
    if (mine.length > NONCE_KEEP) {
      mine
        .sort((a, b) => Number(localStorage.getItem(a)) - Number(localStorage.getItem(b)))
        .slice(0, mine.length - NONCE_KEEP)
        .forEach((k) => localStorage.removeItem(k))
    }
    return true
  } catch (error) {
    warn('nonce claim failed — auto-running anyway', error)
    return true
  }
}

/**
 * Queues the workflow after a successful widget fill, when policy allows:
 * the `cprb.autoRun` setting is on AND this tab won the nonce claim. Uses
 * the documented `app.queuePrompt(number, batchCount = 1)` overload with
 * `0` ("append normally") — the identical call and failure handling as the
 * sibling pack's `maybeAutoQueue` (cpsb pasteback.js). A queue failure
 * (e.g. the graph has some OTHER invalid node) surfaces as a toast, not a
 * silent nothing: the user was told "running", so a non-run must be loud.
 * @param {object} spec - the `KINDS` entry, for the toast.
 * @param {unknown} nonce - `payload.nonce`, panel-stamped (§11.2).
 * @returns {boolean} whether a queue was actually attempted.
 */
function maybeAutoRun(spec, nonce) {
  if (!getAutoRun()) return false
  if (!claimNonce(nonce)) return false
  app.queuePrompt(0).catch((error) => {
    warn('auto-run after Premiere send failed', error)
    toast({
      severity: 'warn',
      summary: `${spec.display}: workflow did not start`,
      detail:
        'The node was filled in, but queueing the workflow failed — usually ' +
        'another node in the graph is invalid. Fix it and press Run.\n' +
        `(${error?.message || error})`,
      life: 10000
    })
  })
  return true
}

// ---------------------------------------------------------------------------
// The relay
// ---------------------------------------------------------------------------

/**
 * Writes one §11 payload into every matching node.
 * @param {object} spec - the `KINDS` entry for this payload's `kind`.
 * @param {object} payload - the `cprb.export_ready` payload.
 * @param {string} path - the path to write (§11.2's `resolved_path` when the
 *   server resolved a doubled extension, else the payload's own `path`).
 * @returns {{matched: number, updated: number}}
 */
function writeToNodes(spec, payload, path) {
  let matched = 0
  let updated = 0

  for (const node of graphNodes()) {
    if (nodeClassOf(node) !== spec.nodeClass) continue
    matched += 1

    const pathWidget = findWidget(node, 'path')
    if (!pathWidget) {
      // A matching class with no `path` widget means the graph holds a node
      // from a different pack version than the one registered — worth a log,
      // never worth throwing.
      warn(`${spec.nodeClass} node ${node?.id} has no "path" widget; skipped`)
      continue
    }
    if (!setWidgetValue(pathWidget, path)) continue
    updated += 1
    // Per-node repaint as well as the graph-level one below: `send_result.js`
    // uses this call and it is the more reliable of the two. A write that
    // lands but doesn't visibly change the node leaves the transient toast as
    // the only evidence — the exact failure shape to avoid here.
    node.setDirtyCanvas?.(true, true)

    if (spec.hasInOut) {
      // Source in/out (§11: the clip's in/out in its SOURCE media, so a
      // downstream trim needs no further arithmetic). Only written when the
      // plugin actually sent them — a missing field must leave whatever the
      // widget already had rather than zeroing a valid range.
      for (const name of ['start_seconds', 'end_seconds']) {
        if (!Number.isFinite(payload[name])) continue
        const widget = findWidget(node, name)
        if (widget) setWidgetValue(widget, Number(payload[name]))
        else warn(`${spec.nodeClass} node ${node?.id} has no "${name}" widget; skipped`)
      }
    }
  }

  if (updated) {
    app.graph?.setDirtyCanvas?.(true, true)
    app.canvas?.setDirty?.(true, true)
  }
  return { matched, updated }
}

/**
 * Handles one `cprb.export_ready` payload:
 * `{kind, path, label, ticks?, seconds?, start_seconds?, end_seconds?,
 *   path_exists?, resolved_path?}` (PROTOCOL.md §11.2).
 *
 * The last two are the SERVER's — it is the only hop that both sees the
 * message and can look at the disk (the panel's own manifest permission
 * cannot stat an arbitrary path), and `exportSequenceFrame` is documented to
 * return true and sometimes write nothing. `path_exists === false` is
 * therefore a real, reachable case and gets a WARNING rather than the usual
 * "press Run when ready", which would otherwise send the user to a Run that
 * can only fail. An ABSENT `path_exists` means unverified (an older server,
 * or a stat that timed out) — treated as fine, exactly as before.
 */
function onExportReady(payload) {
  const kind = payload?.kind
  // `hasOwnProperty`, not a plain `KINDS[kind]`: `kind` arrives off a
  // websocket, and a payload of `kind: "constructor"` would otherwise resolve
  // to an inherited Object.prototype member and sail past the guard below.
  const spec =
    typeof kind === 'string' && Object.prototype.hasOwnProperty.call(KINDS, kind)
      ? KINDS[kind]
      : undefined
  const path = typeof payload?.path === 'string' ? payload.path : ''

  if (!spec) {
    // A newer plugin sending a kind this build doesn't know. The file exists;
    // say so rather than dropping it silently.
    warn(`export_ready with an unrecognized kind: ${JSON.stringify(kind)}`)
    toast({
      severity: 'warn',
      summary: 'Premiere sent something this build cannot place',
      detail:
        `Export kind "${String(kind)}" is not recognized by this version of the ` +
        'Premiere Bridge — update the ComfyUI-side pack to match the panel.' +
        (path ? `\nThe file is on disk at:\n${path}` : ''),
      life: 15000
    })
    return
  }

  if (!path) {
    // §11 requires an absolute path on the wire, so this is a panel-side bug
    // that slipped its own guard. The cause differs per kind — see
    // `emptyPathHint` — and the user can act on both, so say which.
    warn(`export_ready (kind=${kind}) arrived with no path`)
    toast({
      severity: 'warn',
      summary: `${spec.display}: no file path`,
      detail: spec.emptyPathHint,
      life: 15000
    })
    return
  }

  const label = payload?.label ? String(payload.label) : basename(path)
  // The server's resolved path wins when it has one: for a frame that is the
  // doubled-extension name it actually found on disk (§11.4), so the node
  // opens the file that exists rather than the one Premiere claimed to write.
  const writePath =
    typeof payload?.resolved_path === 'string' && payload.resolved_path
      ? payload.resolved_path
      : path
  const { matched, updated } = writeToNodes(spec, payload, writePath)

  if (matched === 0) {
    // The one case the user MUST act on, so: warning severity, a long life,
    // the exact node name to add, and the full path — the export already
    // happened, so nothing is lost by adding the node and sending again (or
    // by pasting the path in by hand).
    toast({
      severity: 'warn',
      summary: `No "${spec.display}" node in this graph`,
      detail:
        `Premiere sent "${label}" but this graph has no ${spec.display} node to ` +
        `receive it. Add one (double-click the canvas and search for ` +
        `"${spec.display}", under Premiere Bridge), then send again.\n` +
        `Nothing is lost — the file is already on disk at:\n${writePath}`,
      life: 20000
    })
    return
  }

  if (updated === 0) {
    // Matched by class but nothing was writable — a version skew, not a
    // user mistake. Still carries the path so the user can proceed by hand.
    toast({
      severity: 'warn',
      summary: `Could not fill the ${spec.display} node`,
      detail:
        `Found ${nodeCountLabel(matched)} of the right type, but none accepted the ` +
        'path — the node in the graph is probably from a different version of ' +
        `the pack. Re-add it from the node menu.\nThe file is on disk at:\n${writePath}`,
      life: 20000
    })
    return
  }

  // The widgets are filled in either way — the path is the best record of
  // what Premiere claimed — but if the server looked and found nothing there,
  // saying "press Run when ready" would send the user to a Run that can only
  // fail, with no way to tell that the export, not the graph, was the problem.
  if (payload?.path_exists === false) {
    warn(`export_ready (kind=${kind}) names a file that is not on disk: ${writePath}`)
    toast({
      severity: 'warn',
      summary: `${spec.display}: nothing was written`,
      detail:
        `Premiere reported ${spec.hasInOut ? 'a clip' : 'an export'} at:\n${writePath}\n` +
        'but ComfyUI cannot find that file. ' +
        (spec.hasInOut
          ? 'The clip may be offline in Premiere, or its media may live on a drive ' +
            'this machine cannot see.'
          : "Premiere's frame export can report success without writing anything — " +
            'click "Frame → ComfyUI" again, and check the panel log.') +
        `\nThe path was still filled into ${nodeCountLabel(updated)}, so a retry ` +
        'only needs the button.',
      life: 20000
    })
    return
  }

  // Success. `where` gives the one detail the path alone doesn't: for a frame,
  // the sequence time it came from; for a clip, its source in/out span.
  let where = ''
  if (spec.hasInOut) {
    const from = seconds(payload?.start_seconds)
    const to = seconds(payload?.end_seconds)
    if (from !== null && to !== null) where = ` [${from}s–${to}s]`
  } else {
    const at = seconds(payload?.seconds)
    if (at !== null) where = ` @ ${at}s`
  }

  const queued = maybeAutoRun(spec, payload?.nonce)

  toast({
    severity: 'info',
    summary: `${spec.display}: ${label}`,
    detail:
      `${basename(writePath)}${where} → ${nodeCountLabel(updated)} updated. ` +
      (queued ? 'Running the workflow…' : 'Press Run when ready.'),
    life: 5000
  })
}

/** Subscribes to the backend event. Called once from the extension's setup. */
export function initPremiereSourceRelay() {
  api.addEventListener('cprb.export_ready', (event) => {
    try {
      onExportReady(event?.detail)
    } catch (error) {
      warn('export_ready relay failed', error)
    }
  })
}
