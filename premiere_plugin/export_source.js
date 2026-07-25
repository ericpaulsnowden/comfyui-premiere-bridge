/*
 * export_source.js -- M2's plugin half: the RETURN direction. Two panel
 * buttons ("SEND TO COMFYUI" section) turn what the user is looking at in
 * Premiere into an `export_ready` websocket message (PROTOCOL.md §11, the
 * message shape §10.4 already accepts and relays as the frontend event
 * `cprb.export_ready`):
 *
 *   Frame -> ComfyUI  exports the still under the playhead into the server's
 *                     `frames_dir` and sends kind="frame" + ticks/seconds.
 *   Clip  -> ComfyUI  reads the SELECTED timeline clip's own media file path
 *                     and its SOURCE in/out and sends kind="clip". NO export,
 *                     NO re-encode -- the file already exists on disk and
 *                     ComfyUI owns the decode (cprb/frame_extract.py).
 *
 * Posture, inherited from import_recipe.js: every step fail-SOFT and LOUD.
 * Nothing here throws out of a click handler; every failure is a specific
 * line naming exactly what was missing. Anything not yet proven on the
 * owner's 26.3 build carries a VERIFY(...) flag, and unproven call shapes are
 * PROBED IN RANKED ORDER with each exact throw/false logged verbatim -- the
 * pattern insertAtPlayhead's v0.9.6 probe used to settle
 * createOverwriteItemAction in a single click. Throws are DATA.
 *
 * PROVEN ground this file builds on (docs/SPIKES.md LIVE RESULTS, owner's
 * Premiere 26.3.0 / uxp 9.3.0 / win32):
 *   - `pr.Exporter.exportSequenceFrame` is a STATIC on `Exporter` (S7).
 *   - `sequence.getPlayerPosition()` returns a TickTime whose `.ticks` is a
 *     STRING and whose `.seconds` is a number.
 *   - `createOverwriteItemAction` demands the RAW ProjectItem and REJECTS a
 *     `ClipProjectItem.cast(...)` wrapper. The clip read below is the MIRROR
 *     of that lesson: `getMediaFilePath()` lives ONLY on ClipProjectItem, so
 *     the cast is mandatory *there*. Both handles are kept and every read
 *     says which one it used.
 *   - S6-C: of 19 enumerated ClipProjectItems, 11 had an EMPTY
 *     `getMediaFilePath()` -- an empty media path is the single most likely
 *     real-world clip failure, so it is a named error, never an empty `path`
 *     on the wire.
 *
 * Depends on helpers.js (log/ok/bad/logDebug/fail/describeError/sleep/
 * cleanImportPath/basename/ppro) and connection.js (the `cprbConnection`
 * singleton). Defines the globals main.js wires to buttons:
 * `cprbSendFrameToComfyUI`, `cprbSendClipToComfyUI`, `cprbRunClick`.
 *
 * Load order (index.html): helpers.js -> connection.js -> import_recipe.js ->
 * export_source.js -> main.js -> layout.js.
 */
'use strict';

/** Adobe's own ticks-per-second constant (uxp-premiere-pro-samples
 * sequence.ts: `const ticksPerSec = 254016000000;`). Only used to DERIVE
 * seconds when a TickTime's `.seconds` is unreadable -- `.seconds` is the
 * proven primary. */
const CPRB_TICKS_PER_SECOND = 254016000000;

/** Retry/poll budget for confirming an exported still landed on disk.
 * RESEARCH A ground truth from a shipping plugin doing this exact job:
 * `exportSequenceFrame` "can return before the file is on disk" AND
 * "occasionally returns ok ... but never writes the file (flaky under rapid
 * back-to-back exports)". Poll, then re-export under a FRESH name. */
const CPRB_EXPORT_POLL_TRIES = 8;
const CPRB_EXPORT_POLL_MS = 120;

/** Serializes the two buttons against themselves and each other. Premiere
 * "races with itself still writing the previous file" when exports overlap
 * (RESEARCH A), and a double-click must not produce two half-exports. */
let cprbExportBusy = false;

/* ------------------------- frames_dir resolution ------------------------ */
/*
 * PROTOCOL.md §11.1: `hello_ack` carries `frames_dir` -- an absolute path to
 * `<ComfyUI input dir>/premiere_frames/`, created by the SERVER on demand.
 * The panel writes exports there and NOWHERE else: it must never invent a
 * path and must never invent a subfolder (nothing plugin-side can create a
 * directory under `localFileSystem: "request"`, so an invented path would
 * fail silently -- exportSequenceFrame just returns false when the directory
 * is absent).
 */

/**
 * The server-provided frames directory as `{dir, via}`, or null.
 *
 * ONE source of truth: connection.js captures `frames_dir` off `hello_ack`
 * into `framesDir` and surfaces it in `getState()` (it resets alongside
 * `serverVersion` on every disconnect/reconnect, so a stale path can never
 * outlive the handshake that produced it). An earlier build sniffed the
 * message by shadowing the connection singleton's private `_handleMessage`;
 * that worked but made this feature silently depend on a private method
 * name -- a rename would have bricked "Frame -> ComfyUI" while leaving
 * "Clip -> ComfyUI" fine, i.e. a half-broken panel with one debug line to
 * explain it. `window.cprbFramesDir` stays as a hand-set escape hatch for a
 * debugging session (the supported way to redirect exports is the SERVER's
 * `CPRB_FRAMES_DIR`, PROTOCOL.md §11.1).
 */
function cprbResolveFramesDir() {
  try {
    const state = (typeof cprbConnection !== 'undefined' && cprbConnection
      && typeof cprbConnection.getState === 'function') ? cprbConnection.getState() : null;
    if (state && typeof state.framesDir === 'string' && state.framesDir) {
      return { dir: cleanImportPath(state.framesDir), via: 'hello_ack (connection state)' };
    }
  } catch (_) { /* fall through to the override */ }
  try {
    if (typeof window !== 'undefined' && typeof window.cprbFramesDir === 'string'
      && window.cprbFramesDir) {
      return { dir: cleanImportPath(window.cprbFramesDir), via: 'window.cprbFramesDir' };
    }
  } catch (_) { /* nothing available */ }
  return null;
}

/** Path separator for a host path -- backslash the moment one appears
 * (win32 is the owner's box; UNC `\\server\share` paths are PROVEN to work
 * end-to-end, so the backslash form must survive untouched). */
function cprbPathSeparator(p) {
  return String(p || '').indexOf('\\') !== -1 ? '\\' : '/';
}

/** `dir` with every trailing separator removed. RESEARCH A: all three real
 * callers pass a bare directory (`nativePath` / `path.parse().dir`), even
 * though the reference doc's example shows 'C:/temp/'. Shape A2 below puts
 * one back deliberately, as a probe. */
function cprbTrimTrailingSeparators(dir) {
  let out = String(dir || '');
  while (out.length > 1 && (out[out.length - 1] === '\\' || out[out.length - 1] === '/')) {
    out = out.slice(0, -1);
  }
  return out;
}

/* --------------------------- shared preflight -------------------------- */

/**
 * True when the socket is up and the handshake completed -- checked BEFORE
 * doing any Premiere work so we never export a still nobody will receive
 * (`cprbConnection.send` drops silently-but-dimly on a closed socket).
 */
function cprbRequireConnection(what) {
  let state = null;
  try {
    state = (typeof cprbConnection !== 'undefined' && cprbConnection
      && typeof cprbConnection.getState === 'function') ? cprbConnection.getState() : null;
  } catch (error) {
    fail(`${what}: could not read the connection state`, error);
    return false;
  }
  if (!state) {
    bad(`${what}: the panel's connection object is missing -- reload the panel`);
    return false;
  }
  if (state.status !== 'connected' || !state.ready) {
    const where = state.standby
      ? `standing by (${state.standby})`
      : state.status;
    bad(`${what}: not connected to ComfyUI -- ${where}. Press Connect, wait for the green dot, then try again.`);
    return false;
  }
  return true;
}

/**
 * Sends one `export_ready` message, re-checking the socket FIRST so a
 * connection that dropped mid-work is reported instead of silently swallowed
 * (`cprbConnection.send` only drops-and-dims). Returns whether it went out --
 * the caller's green headline is gated on this, because "sent" must mean sent.
 */
function cprbSendExportReady(what, message) {
  let state = null;
  try {
    state = (typeof cprbConnection !== 'undefined' && cprbConnection
      && typeof cprbConnection.getState === 'function') ? cprbConnection.getState() : null;
  } catch (_) { /* treated as not connected below */ }
  if (!state || state.status !== 'connected' || !state.ready) {
    bad(`${what}: the ComfyUI connection dropped before the message could be sent -- press `
      + `Connect and try again (the file is still there: ${message.path})`);
    return false;
  }
  cprbConnection.send(message);
  return true;
}

/**
 * `{pr, project, sequence}` for the active sequence, or null after logging
 * exactly which of the two was missing.
 */
async function cprbActiveSequence(what) {
  const pr = ppro();
  let project = null;
  try {
    project = await pr.Project.getActiveProject();
  } catch (error) {
    fail(`${what}: reading the active project`, error);
    return null;
  }
  if (!project) {
    bad(`${what}: no project is open in Premiere -- open one, then try again`);
    return null;
  }
  let sequence = null;
  try {
    sequence = await project.getActiveSequence();
  } catch (error) {
    fail(`${what}: reading the active sequence`, error);
    return null;
  }
  if (!sequence) {
    bad(`${what}: no active sequence -- open a sequence in the Timeline, then try again`);
    return null;
  }
  return { pr, project, sequence };
}

/** A display name off any host object: the plain `.name` property first
 * (PROVEN read shape on 26.3 for FolderItem -- import_recipe.js
 * folderDisplayName), then a promise-valued `.name`, then `getName()`.
 * Returns '' when nothing is readable. */
async function cprbDisplayName(object) {
  try {
    const prop = object.name;
    if (typeof prop === 'string' && prop) return prop;
    if (prop && typeof prop.then === 'function') {
      const value = await prop;
      if (typeof value === 'string' && value) return value;
    }
  } catch (_) { /* fall through to the method probe */ }
  try {
    if (typeof object.getName === 'function') {
      const value = await object.getName();
      if (typeof value === 'string' && value) return value;
    }
  } catch (_) { /* no readable name */ }
  return '';
}

/** Seconds from a TickTime: `.seconds` (PROVEN) first, otherwise derived
 * from ticks via Adobe's own ticks-per-second constant. null when neither
 * is readable -- callers must treat that as a hard failure rather than
 * silently sending 0. */
function cprbTickSeconds(tickTime) {
  if (!tickTime) return null;
  try {
    const seconds = Number(tickTime.seconds);
    if (Number.isFinite(seconds)) return seconds;
  } catch (_) { /* fall through */ }
  try {
    const raw = tickTime.ticksNumber !== undefined ? tickTime.ticksNumber : tickTime.ticks;
    const ticks = Number(raw);
    if (Number.isFinite(ticks)) return ticks / CPRB_TICKS_PER_SECOND;
  } catch (_) { /* nothing readable */ }
  return null;
}

/** A TickTime's ticks as the STRING the protocol carries (PROVEN: `.ticks`
 * IS a string like "4265352000000"); '' when unreadable. */
function cprbTickString(tickTime) {
  if (!tickTime) return '';
  try {
    if (typeof tickTime.ticks === 'string' && tickTime.ticks) return tickTime.ticks;
    if (tickTime.ticks !== undefined && tickTime.ticks !== null) return String(tickTime.ticks);
    if (tickTime.ticksNumber !== undefined) return String(tickTime.ticksNumber);
  } catch (_) { /* unreadable */ }
  return '';
}

/* ------------------- file-existence probe (verification) ---------------- */
/*
 * The panel's manifest declares `localFileSystem: "request"`, which grants
 * access only to entries the USER picked in a picker -- so statting an
 * arbitrary absolute path under ComfyUI's input dir is EXPECTED to be
 * refused here (RESEARCH A). That is fine and deliberate: the manifest
 * permission is NOT escalated for a check Python can do for free, and
 * PremiereFrameSource verifies server-side. But when a build DOES let us
 * look, looking is free -- and the alternative ("returned true" == success)
 * is exactly the lie RESEARCH A documents. So: probe, and be honest about
 * the three distinct outcomes.
 */

/** Session memory so an access-denied fs probe says so ONCE, not per attempt. */
let cprbFsProbeNote = null;

function cprbNoteFsProbe(note) {
  if (cprbFsProbeNote === note) return;
  cprbFsProbeNote = note;
  logDebug(`frame: file check -- ${note}`);
}

/** Does this error text mean "the file is not there" (as opposed to "you may
 * not look")? The distinction decides retry-vs-give-up. */
function cprbLooksMissing(message) {
  return /ENOENT|no such file|not\s*found|cannot find|does not exist|doesn't exist/i.test(
    String(message || '')
  );
}

/**
 * true (exists) / false (provably absent) / null (cannot tell -- no fs
 * access on this build+manifest). Two readers, each fully guarded.
 * VERIFY(M2-live): neither reader is proven on Premiere 26.3 under
 * `localFileSystem: "request"`; `null` is the expected answer there and is
 * handled honestly by the caller.
 */
async function cprbFileExists(fullPath) {
  let opaque = false;

  // (1) UXP storage -- the documented way to reach an absolute path, and the
  // one that needs `fullAccess` (hence the likely refusal).
  try {
    const uxp = require('uxp');
    const lfs = uxp && uxp.storage && uxp.storage.localFileSystem;
    if (lfs && typeof lfs.getEntryWithUrl === 'function') {
      try {
        const entry = await lfs.getEntryWithUrl(`file:${fullPath}`);
        if (entry) return true;
        opaque = true;
        cprbNoteFsProbe('getEntryWithUrl returned nothing');
      } catch (error) {
        const message = describeError(error);
        if (cprbLooksMissing(message)) return false;
        opaque = true;
        cprbNoteFsProbe(`getEntryWithUrl refused: ${message} (expected under `
          + 'localFileSystem "request" -- ComfyUI verifies the file instead)');
      }
    }
  } catch (_) { /* no uxp storage here */ }

  // (2) The uxp `fs` shim, if this build has one.
  try {
    const fs = require('fs');
    const stat = fs && (fs.lstatSync || fs.statSync);
    if (typeof stat === 'function') {
      try {
        if (stat.call(fs, fullPath)) return true;
        opaque = true;
      } catch (error) {
        const message = describeError(error);
        if (cprbLooksMissing(message)) return false;
        opaque = true;
        cprbNoteFsProbe(`fs stat refused: ${message}`);
      }
    }
  } catch (_) { /* no fs module here */ }

  if (!opaque) cprbNoteFsProbe('no filesystem reader available in this panel');
  return null;
}

/**
 * Looks for an exported still among its candidate names, polling because the
 * export call "can return before the file is on disk" (RESEARCH A).
 * Returns `{state, path}` where state is:
 *   'confirmed' -- `path` exists on disk (send THIS one)
 *   'missing'   -- provably absent after the full poll budget (retry)
 *   'unknown'   -- the panel may not look (send the exact name, say so)
 */
async function cprbLocateExport(candidatePaths) {
  for (let attempt = 0; attempt < CPRB_EXPORT_POLL_TRIES; attempt += 1) {
    for (const candidate of candidatePaths) {
      const exists = await cprbFileExists(candidate);
      if (exists === true) return { state: 'confirmed', path: candidate };
      // A refusal will not become an allowance on the next tick -- stop.
      if (exists === null) return { state: 'unknown', path: null };
    }
    await sleep(CPRB_EXPORT_POLL_MS);
  }
  return { state: 'missing', path: null };
}

/* -------------------------- Frame -> ComfyUI ---------------------------- */

/** Monotonic part of the export filename. Names are NEVER reused: reusing
 * one "races with Premiere still writing the previous file" (RESEARCH A). */
let cprbFrameSeq = 0;

/** A filename stem safe on win32: colons in particular make the export
 * return false outright (so a timecode-shaped name silently fails), and
 * spaces buy nothing here. */
function cprbSanitizeStem(raw) {
  const cleaned = String(raw || '')
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/^[_.]+/, '')
    .replace(/_+$/, '');
  return (cleaned || 'frame').slice(0, 40);
}

/** A fresh, unique, extension-selects-the-format filename. `.png` because
 * RESEARCH A has it empirically confirmed as an 8-bit non-interlaced PNG. */
function cprbUniqueFrameName(stem) {
  cprbFrameSeq += 1;
  return `${stem}_${Date.now().toString(36)}_${cprbFrameSeq}.png`;
}

/** One-time arity/type probe. RESEARCH A shape 4: a free confirmation that
 * the documented 6-arg signature really is this build's signature, so a
 * future Adobe change is one log line to diagnose instead of a
 * re-investigation. */
let cprbExporterProbeLogged = false;

function cprbLogExporterProbe(pr) {
  if (cprbExporterProbeLogged) return;
  cprbExporterProbeLogged = true;
  try {
    const fn = pr && pr.Exporter && pr.Exporter.exportSequenceFrame;
    logDebug(`frame: Exporter.exportSequenceFrame typeof=${typeof fn}`
      + ` arity=${typeof fn === 'function' ? fn.length : 'n/a'} (expect function / 6)`);
  } catch (error) {
    logDebug(`frame: Exporter probe threw: ${describeError(error)}`);
  }
}

/**
 * The sequence's pixel dimensions as `{width, height, via}`, or null.
 * `getFrameSize()` -> RectF `{width, height}` (floats, hence the rounding) is
 * the documented and cross-checked reader; `getSettings()` is an UNPROVEN
 * secondary probe. Both are logged when they misbehave, and a total failure
 * ABORTS the export rather than guessing a size -- a mismatched pair yields a
 * stretched frame with no error, which is worse than no frame at all.
 * VERIFY(M2-live): getFrameSize()'s live shape on 26.3.
 */
async function cprbReadFrameSize(sequence) {
  try {
    if (typeof sequence.getFrameSize === 'function') {
      const size = await sequence.getFrameSize();
      const width = Number(size && size.width);
      const height = Number(size && size.height);
      if (Number.isFinite(width) && Number.isFinite(height) && width >= 1 && height >= 1) {
        return { width: Math.round(width), height: Math.round(height), via: 'getFrameSize()' };
      }
      logDebug(`frame: getFrameSize() returned an unusable value: ${JSON.stringify(size)}`);
    } else {
      logDebug('frame: sequence.getFrameSize is not a function on this build');
    }
  } catch (error) {
    logDebug(`frame: getFrameSize() threw: ${describeError(error)}`);
  }
  // VERIFY(M2-live): unproven secondary reader; probed only because the
  // primary failing would otherwise dead-end the whole feature.
  try {
    if (typeof sequence.getSettings === 'function') {
      const settings = await sequence.getSettings();
      const width = Number(settings && (settings.videoFrameWidth !== undefined
        ? settings.videoFrameWidth : settings.width));
      const height = Number(settings && (settings.videoFrameHeight !== undefined
        ? settings.videoFrameHeight : settings.height));
      if (Number.isFinite(width) && Number.isFinite(height) && width >= 1 && height >= 1) {
        return { width: Math.round(width), height: Math.round(height), via: 'getSettings()' };
      }
      logDebug('frame: getSettings() carried no usable frame size');
    }
  } catch (error) {
    logDebug(`frame: getSettings() threw: ${describeError(error)}`);
  }
  // Both readers failed, which ABORTS the export -- so dump the surface on the
  // way out rather than dead-ending. Without this the owner gets "could not
  // read the frame size" and no way to find the real method name; with it, one
  // click names it. Same enumeration pattern main.js used to settle S7's
  // Exporter statics.
  try {
    const proto = Object.getPrototypeOf(sequence);
    logDebug('frame: Sequence prototype keys: '
      + JSON.stringify(Object.getOwnPropertyNames(proto || {}).sort()));
    logDebug('frame: Sequence own keys: '
      + JSON.stringify(Object.getOwnPropertyNames(sequence || {}).sort()));
  } catch (error) {
    logDebug(`frame: could not enumerate Sequence: ${describeError(error)}`);
  }
  return null;
}

/**
 * Exports the still under the playhead into `framesDir`, probing RESEARCH A's
 * ranked call shapes in order and stopping at the FIRST one that produces a
 * file. Returns `{path, confirmed, shape}` on success, or null after logging
 * a decisive summary of every attempt.
 *
 * THE CALL (PROTOCOL.md §11, RESEARCH A -- confirmed against Adobe's own
 * `ExporterStatic` declaration and three real callers):
 *
 *   pr.Exporter.exportSequenceFrame(sequence, time, filename, filepath,
 *                                   width, height) -> Promise<boolean>
 *
 * with `filename` a BARE BASENAME and `filepath` a DIRECTORY. A full path in
 * `filename` makes Premiere "write to a bad combined path and silently
 * produce nothing" -- the worst possible failure mode -- so that shape is
 * DELIBERATELY NOT in the ladder, and neither is `0, 0` for width/height
 * (documented nowhere, ruled out by RESEARCH A). No `lockedAccess` /
 * `executeTransaction`: this is a read, confirmed by three callers calling it
 * bare.
 *
 * `sequence` and `position` are passed EXACTLY as the host handed them over
 * (`getActiveSequence()` / `getPlayerPosition()`) -- never cast, wrapped or
 * rebuilt. That is the createOverwriteItemAction lesson generalized: a native
 * binding taking an object argument rejects a re-wrapped one with
 * "Invalid parameter.". If this ever throws that, those two are suspects #1
 * and #2.
 */
async function cprbExportFrameStill(pr, sequence, position, framesDir, stem, size) {
  if (!pr.Exporter || typeof pr.Exporter.exportSequenceFrame !== 'function') {
    bad('Frame -> ComfyUI: pr.Exporter.exportSequenceFrame is missing on this Premiere build '
      + '-- M2 frame export needs it (PROVEN present on 26.3 by spike S7)');
    return null;
  }
  cprbLogExporterProbe(pr);

  const separator = cprbPathSeparator(framesDir);
  const bareDir = cprbTrimTrailingSeparators(framesDir);
  const shapes = [
    {
      name: 'A1 basename + directory',
      note: 'RESEARCH A shape 1/2 -- CONFIRMED (Adobe premierepro-types + 3 live callers)',
      dir: bareDir,
      attempts: 4
    },
    {
      name: 'A2 basename + directory WITH trailing separator',
      note: "the reference doc's literal filepath example ('C:/temp/')",
      dir: bareDir + separator,
      attempts: 2
    }
  ];

  const summary = [];
  for (const shape of shapes) {
    for (let attempt = 1; attempt <= shape.attempts; attempt += 1) {
      // A FRESH name every single attempt, never a reuse (RESEARCH A).
      const filename = cprbUniqueFrameName(stem);
      const exactPath = `${bareDir}${separator}${filename}`;
      // Pre-26.2.2 doubled the extension ("abcd.jpg" -> "abcd.jpg.jpg").
      // Fixed in 26.2.2, one patch before the owner's 26.3.0 -- guarded
      // anyway, because three lines buys immunity to a regression.
      const doubledPath = `${exactPath}.png`;
      const label = `${shape.name} attempt ${attempt}/${shape.attempts}`;

      let returned;
      try {
        returned = await pr.Exporter.exportSequenceFrame(
          sequence, position, filename, shape.dir, size.width, size.height
        );
      } catch (error) {
        // Verbatim -- "Not Enough Parameters" (wrong arity) vs "Invalid
        // parameter." (wrong type/value) is the diagnostic.
        const message = describeError(error);
        log(`frame: ${label} THREW verbatim: ${message}`, 'dim');
        summary.push(`${shape.name}: threw "${message}"`);
        // A throw is a shape error, not flakiness -- retrying is pointless.
        break;
      }

      logDebug(`frame: ${label} returned ${JSON.stringify(returned)} for ${filename}`);
      if (returned === false) {
        summary.push(`${shape.name}: returned false`);
        await sleep(150);
        continue;
      }

      const located = await cprbLocateExport([exactPath, doubledPath]);
      if (located.state === 'confirmed') {
        if (located.path !== exactPath) {
          logDebug(`frame: the file landed as ${basename(located.path)} `
            + '(doubled extension -- pre-26.2.2 behavior)');
        }
        return { path: located.path, confirmed: true, shape: `${shape.name} (${shape.note})` };
      }
      if (located.state === 'unknown') {
        // Cannot look, and the call did not say false: send the EXACT name
        // (correct on 26.3.0) and be plain that this is unverified here.
        return {
          path: exactPath,
          confirmed: false,
          shape: `${shape.name} (${shape.note})`,
          altPath: doubledPath
        };
      }
      // 'missing': the documented "returned ok, wrote nothing" mode.
      log(`frame: ${label} returned ${JSON.stringify(returned)} but no file appeared at `
        + `${exactPath} after ${CPRB_EXPORT_POLL_TRIES} checks -- retrying under a new name`, 'dim');
      summary.push(`${shape.name}: returned ${JSON.stringify(returned)}, no file on disk`);
      await sleep(150);
    }
  }

  bad('Frame -> ComfyUI FAILED -- every export shape was tried:');
  for (const line of summary) bad(`  · ${line}`);
  bad(`  · target directory was ${bareDir} -- does it exist, and is it writable? `
    + '(the ComfyUI server creates it; reconnect if it was just added)');
  return null;
}

/**
 * "Frame -> ComfyUI": export the still under the playhead and announce it as
 * `export_ready` kind="frame" (PROTOCOL.md §11.2). Never throws.
 */
async function cprbSendFrameToComfyUI() {
  const what = 'Frame -> ComfyUI';
  if (cprbExportBusy) {
    log(`${what}: another send is still running -- ignored (Premiere races with itself `
      + 'on overlapping exports)', 'dim');
    return;
  }
  cprbExportBusy = true;
  try {
    if (!cprbRequireConnection(what)) return;

    const frames = cprbResolveFramesDir();
    if (!frames || !frames.dir) {
      bad(`${what}: the ComfyUI server has not told this panel where to write frames.`);
      bad('  · frames_dir arrives with hello_ack (PROTOCOL.md §11.1) and is created by the server.');
      bad('  · Press Connect to redo the handshake; if it still does not arrive, the ComfyUI '
        + 'side is older than the M2 server -- update it.');
      bad('  · Refusing to invent an output path (an unwritable one fails silently).');
      return;
    }

    const context = await cprbActiveSequence(what);
    if (!context) return;
    const { pr, sequence } = context;

    // FIRST, before any read that can fail: the free typeof/arity confirmation
    // of the one call this whole feature rests on. It used to live inside
    // cprbExportFrameStill, which meant a playhead or frame-size failure
    // suppressed the single most useful diagnostic line in the file. Now every
    // click records it no matter where the run later dies.
    cprbLogExporterProbe(pr);

    // Playhead -- a PROVEN read (S6-D / S7): TickTime, .ticks a STRING.
    let position = null;
    try {
      position = await sequence.getPlayerPosition();
    } catch (error) {
      fail(`${what}: reading the playhead position`, error);
      return;
    }
    if (!position) {
      bad(`${what}: getPlayerPosition() returned nothing -- cannot tell which frame to export`);
      return;
    }
    const ticks = cprbTickString(position);
    const seconds = cprbTickSeconds(position);
    if (seconds === null) {
      bad(`${what}: the playhead TickTime carried no readable seconds/ticks -- aborting rather `
        + 'than sending a bogus timestamp');
      return;
    }

    const size = await cprbReadFrameSize(sequence);
    if (!size) {
      bad(`${what}: could not read the sequence frame size (getFrameSize / getSettings both `
        + 'failed -- see the lines above). Aborting: a guessed width/height silently produces '
        + 'a stretched frame.');
      return;
    }

    const sequenceName = await cprbDisplayName(sequence);
    const label = sequenceName || 'Premiere frame';
    logDebug(`${what}: sequence "${label}" ${size.width}x${size.height} (via ${size.via}), `
      + `playhead ${seconds.toFixed(3)}s / ticks ${ticks || '(unreadable)'}, `
      + `frames_dir via ${frames.via}`);

    const exported = await cprbExportFrameStill(
      pr, sequence, position, frames.dir, cprbSanitizeStem(sequenceName || 'frame'), size
    );
    if (!exported) return;

    const sent = cprbSendExportReady(what, {
      type: 'export_ready',
      kind: 'frame',
      path: exported.path,
      label,
      ticks,
      seconds
    });
    if (!sent) return;

    if (exported.confirmed) {
      ok(`${what}: sent "${label}" @ ${seconds.toFixed(3)}s -> ${basename(exported.path)} `
        + '(file confirmed on disk) -- add a "Frame from Premiere" node and press Run');
    } else {
      ok(`${what}: sent "${label}" @ ${seconds.toFixed(3)}s -> ${basename(exported.path)} `
        + '-- Premiere reported success; this panel cannot read the folder to confirm it '
        + '(localFileSystem "request"), so ComfyUI verifies the file');
      logDebug(`${what}: if the node reports "frame not found", also check `
        + `${basename(exported.altPath || '')} (doubled-extension regression)`);
    }
    logDebug(`${what}: full path ${exported.path} (via ${exported.shape})`);
  } catch (error) {
    // Belt-and-braces: nothing may escape a click handler.
    fail(`${what} failed`, error);
  } finally {
    cprbExportBusy = false;
  }
}

/* --------------------------- Clip -> ComfyUI --------------------------- */
/*
 * NO export and NO re-encode by design (docs/SPIKES.md S6-D DECISION): the
 * clip's media already exists on disk, so the panel hands ComfyUI the path
 * plus the SOURCE in/out and the backend owns extraction
 * (cprb/frame_extract.py).
 *
 * THE in/out distinction (RESEARCH B, settled by the reference's own
 * wording): getInPoint()/getOutPoint() are "relative to the start time of the
 * project item referenced by this track item" == SOURCE media time, which is
 * what a decoder wants. getStartTime()/getEndTime() are TIMELINE positions --
 * NOT what we send. Adobe's own sample pins it further: getInPoint() is
 * already an offset from the media's start, so media start is NOT added.
 * VERIFY(M2-live): a clip whose Media Start is not 00:00:00:00 (R3D/MXF/
 * broadcast) is the one case that would disprove that -- the invariant line
 * logged below makes it obvious if it does.
 */

/** Which of a selection's items is the VIDEO one. Neither track-item class
 * exposes a static `.cast()`, so the documented-surface discriminator is
 * duck-typing on `createAddVideoTransitionAction` -- present on
 * VideoClipTrackItem, absent from AudioClipTrackItem's documented method
 * list. VERIFY(M2-live): a linked A/V click's item count and order are
 * undocumented. */
function cprbIsVideoTrackItem(item) {
  return !!(item && typeof item.createAddVideoTransitionAction === 'function');
}

/** What the last sweep saw, so the "nothing is selected" message can report
 * it instead of just asserting. Reset at the start of every sweep. */
let cprbSweepStats = { tracks: 0, items: 0, readable: 0, ran: false };

/**
 * Route 1: the timeline selection, the documented and Adobe-sampled path.
 * `sequence.getSelection()` -> TrackItemSelection -> `getTrackItems()`.
 * Returns an array (possibly empty); never throws.
 */
async function cprbSelectedTrackItems(sequence) {
  try {
    if (typeof sequence.getSelection !== 'function') {
      logDebug('clip: sequence.getSelection is not a function on this build');
      return [];
    }
    const selection = await sequence.getSelection();
    if (!selection) {
      logDebug('clip: getSelection() returned nothing');
      return [];
    }
    // getTrackItems, NOT getItems -- it landed in 25.6; ProjectItemSelection
    // is the class with getItems (pre-25.6 forum snippets confuse the two).
    if (typeof selection.getTrackItems !== 'function') {
      logDebug('clip: TrackItemSelection.getTrackItems is missing (needs Premiere 25.6+)');
      return [];
    }
    const items = await selection.getTrackItems();
    const list = Array.isArray(items) ? items : (items ? Array.from(items) : []);
    // UNCONDITIONAL: "a selection exists but reports zero items" is the exact
    // shape of the failure this feature is most likely to hit (getIsSelected
    // has no Adobe sample, and whether Timeline selection survives focus moving
    // into a UXP panel is undocumented). Logging only the falsy cases meant
    // that failure produced an empty log and a misleading instruction.
    logDebug(`clip: getSelection() -> selection present, getTrackItems() returned `
      + `${list.length} item(s)`);
    return list;
  } catch (error) {
    logDebug(`clip: getSelection()/getTrackItems() threw: ${describeError(error)}`);
    return [];
  }
}

/**
 * Route 2: sweep the video tracks for a track item whose own
 * `getIsSelected()` is true -- the safety net for a selection that
 * getSelection() reports as empty (there is NO selection-changed event
 * anywhere in the 26.3 reference, and whether Timeline selection survives
 * focus moving into a UXP panel is undocumented). STILL selection-based, so
 * it is not a guess. Topmost track first; every call here is already PROVEN
 * live by import_recipe.js insertAtPlayhead / cprbCountClipsOnTrack.
 * VERIFY(M2-live): getIsSelected() has no Adobe sample exercising it.
 */
async function cprbSelectedTrackItemsBySweep(pr, sequence) {
  const found = [];
  // Counted so the empty-selection message can SAY what the sweep saw. "0 of 14
  // clips on 3 tracks reported selection" and "0 clips found at all" are
  // different bugs with different fixes, and the difference is invisible
  // without these.
  cprbSweepStats = { tracks: 0, items: 0, readable: 0, ran: true };
  try {
    if (typeof sequence.getVideoTrackCount !== 'function') {
      cprbSweepStats.ran = false;
      logDebug('clip: sequence.getVideoTrackCount is not a function -- cannot sweep tracks');
      return found;
    }
    if (!pr.Constants || !pr.Constants.TrackItemType
      || pr.Constants.TrackItemType.CLIP === undefined) {
      cprbSweepStats.ran = false;
      logDebug('clip: no Constants.TrackItemType.CLIP -- cannot sweep tracks');
      return found;
    }
    const count = await sequence.getVideoTrackCount();
    if (!(count >= 1)) return found;
    for (let index = count - 1; index >= 0; index -= 1) {
      const track = await sequence.getVideoTrack(index);
      if (!track || typeof track.getTrackItems !== 'function') continue;
      cprbSweepStats.tracks += 1;
      // Documented SYNC on VideoTrack (vs async on TrackItemSelection);
      // awaiting a non-promise is harmless and import_recipe.js already does.
      const items = await track.getTrackItems(pr.Constants.TrackItemType.CLIP, false);
      for (const item of (items || [])) {
        cprbSweepStats.items += 1;
        try {
          if (typeof item.getIsSelected !== 'function') continue;
          const selected = await item.getIsSelected();
          cprbSweepStats.readable += 1;
          if (selected) found.push(item);
        } catch (_) { /* this item cannot report selection -- skip it */ }
      }
      if (found.length) return found;
    }
  } catch (error) {
    logDebug(`clip: selection sweep threw: ${describeError(error)}`);
  }
  logDebug(`clip: sweep scanned ${cprbSweepStats.tracks} video track(s), examined `
    + `${cprbSweepStats.items} clip item(s), ${cprbSweepStats.readable} could answer `
    + `getIsSelected(), ${found.length} selected`);
  return found;
}

/** Best-effort boolean read off a host object; null when the method is
 * absent or throws. Used for the advisory clip guards -- an unreadable guard
 * must never block a send. */
async function cprbTryBool(object, method) {
  try {
    if (typeof object[method] !== 'function') return null;
    return !!(await object[method]());
  } catch (_) {
    return null;
  }
}

/**
 * "Clip -> ComfyUI": read the selected timeline clip's own media file and
 * SOURCE in/out and announce it as `export_ready` kind="clip"
 * (PROTOCOL.md §11.2). Never throws.
 */
async function cprbSendClipToComfyUI() {
  const what = 'Clip -> ComfyUI';
  if (cprbExportBusy) {
    log(`${what}: another send is still running -- ignored`, 'dim');
    return;
  }
  cprbExportBusy = true;
  try {
    if (!cprbRequireConnection(what)) return;

    const context = await cprbActiveSequence(what);
    if (!context) return;
    const { pr, sequence } = context;

    // Read paths need no lockedAccess -- Adobe's own samples read the
    // selection outside any lock, and 26.3's breaking change is scoped to
    // create*Action. VERIFY(M2-live): if a read here ever throws a lock
    // error, wrapping it in lockedAccess is the first thing to try.
    let route = 'timeline selection';
    let items = await cprbSelectedTrackItems(sequence);
    const selectionCount = items.length;
    if (!items.length) {
      const swept = await cprbSelectedTrackItemsBySweep(pr, sequence);
      if (swept.length) {
        items = swept;
        route = 'per-item getIsSelected() sweep';
      }
    }
    if (!items.length) {
      // NO playhead / project-panel fallback ON PURPOSE: both would GUESS at
      // what the user meant. Say what to do instead -- and say what both routes
      // actually SAW, because "nothing is selected" while a clip is visibly
      // selected is the failure this feature is most likely to hit (S7-c:
      // getIsSelected has no Adobe sample, and Timeline selection surviving
      // focus moving into a UXP panel is undocumented). One click then settles
      // it instead of dead-ending.
      const sweepSummary = cprbSweepStats.ran
        ? `a sweep of ${cprbSweepStats.tracks} video track(s) found 0 of `
          + `${cprbSweepStats.items} clip(s) reporting selection `
          + `(${cprbSweepStats.readable} could answer getIsSelected)`
        : 'the track sweep could not run on this build';
      bad(`${what}: nothing is selected -- select a clip on the timeline first, then press `
        + '"Clip -> ComfyUI"');
      bad(`  · getSelection() reported ${selectionCount} item(s); ${sweepSummary}.`);
      bad('  · If a clip IS selected in the Timeline right now, that is a real finding -- '
        + 'Copy log and paste it back (the selection API is behaving differently than '
        + 'documented).');
      return;
    }

    // A linked A/V click yields both items in an undocumented order; the
    // video one is what a ComfyUI graph wants.
    const videoItems = items.filter(cprbIsVideoTrackItem);
    const picked = videoItems[0] || items[0];
    if (videoItems.length > 1) {
      logDebug(`clip: ${videoItems.length} video clips selected -- using the first `
        + `(via ${route})`);
    } else if (!videoItems.length) {
      logDebug(`clip: selection looks audio-only (via ${route}) -- reading it anyway, `
        + 'the media file is the same');
    } else {
      logDebug(`clip: one video clip selected (via ${route})`);
    }

    const label = (await cprbDisplayName(picked)) || 'Premiere clip';

    // THE raw-vs-cast rule, in BOTH directions (this is the mirror of the
    // createOverwriteItemAction lesson): getProjectItem() hands back a RAW
    // ProjectItem, which has NO getMediaFilePath -- that lives on
    // ClipProjectItem -- so reading media facts REQUIRES the cast wrapper
    // here, exactly where passing a project item as an ARGUMENT requires the
    // raw one. Both handles are kept and the log names which is which.
    let rawItem = null;
    try {
      rawItem = await picked.getProjectItem();
    } catch (error) {
      fail(`${what}: getProjectItem() for "${label}"`, error);
      return;
    }
    if (!rawItem) {
      bad(`${what}: "${label}" has no project item behind it (a synthetic clip -- title, `
        + 'colour matte, adjustment layer?). Select a clip backed by a real file.');
      return;
    }
    logDebug(`clip: RAW ProjectItem handle obtained for "${label}" `
      + '(raw = what action factories take as an ARGUMENT)');

    let clipItem = null;
    try {
      // `await` deliberately: helpers.js collectClips proves cast() is SYNC on
      // 26.3, but Adobe's own sample writes `await ppro.ClipProjectItem.cast(...)`
      // -- awaiting a non-promise is free and makes this correct either way.
      clipItem = await pr.ClipProjectItem.cast(rawItem);
    } catch (error) {
      fail(`${what}: ClipProjectItem.cast() for "${label}"`, error);
      return;
    }
    if (!clipItem) {
      bad(`${what}: "${label}" is not a media clip (ClipProjectItem.cast returned null) `
        + '-- nested sequences, titles and colour mattes cannot be sent');
      return;
    }
    logDebug(`clip: CAST ClipProjectItem handle obtained for "${label}" `
      + '(cast = what getMediaFilePath / in-out READS live on)');

    let mediaPath = '';
    try {
      mediaPath = await clipItem.getMediaFilePath();
    } catch (error) {
      fail(`${what}: getMediaFilePath() for "${label}" (read off the CAST handle)`, error);
      return;
    }
    mediaPath = cleanImportPath(mediaPath);
    if (!mediaPath) {
      // S6-C measured this on 11 of 19 real ClipProjectItems -- the single
      // most likely clip failure, so it is a named error, never an empty
      // `path` on the wire.
      bad(`${what}: "${label}" has no media file on disk -- nested sequences, titles, colour `
        + 'mattes, adjustment layers and other synthetics return an empty path. Select a clip '
        + 'backed by a real file.');
      return;
    }

    // SOURCE in/out (NOT getStartTime/getEndTime, which are timeline times).
    let inPoint = null;
    let outPoint = null;
    try {
      inPoint = await picked.getInPoint();
      outPoint = await picked.getOutPoint();
    } catch (error) {
      fail(`${what}: reading the clip's source in/out for "${label}"`, error);
      return;
    }
    const startSeconds = cprbTickSeconds(inPoint);
    const endSeconds = cprbTickSeconds(outPoint);
    if (startSeconds === null || endSeconds === null) {
      bad(`${what}: "${label}" gave no readable source in/out (in=${JSON.stringify(startSeconds)}, `
        + `out=${JSON.stringify(endSeconds)}) -- aborting rather than sending a bogus range`);
      return;
    }
    if (!(endSeconds > startSeconds)) {
      bad(`${what}: "${label}" has an empty source range `
        + `(${startSeconds.toFixed(3)}s -> ${endSeconds.toFixed(3)}s) -- nothing to send`);
      return;
    }

    // Advisory guards. Offline is a hard stop (the path exists but the media
    // does not); the rest are warnings -- a guard this panel cannot read must
    // never block a send.
    const offline = await cprbTryBool(clipItem, 'isOffline');
    if (offline === true) {
      bad(`${what}: "${label}" is OFFLINE in Premiere -- relink it, then try again `
        + `(${mediaPath})`);
      return;
    }
    const adjustment = await cprbTryBool(picked, 'isAdjustmentLayer');
    if (adjustment === true) {
      bad(`${what}: "${label}" is an adjustment layer -- it has no media of its own`);
      return;
    }
    if ((await cprbTryBool(clipItem, 'isMergedClip')) === true
      || (await cprbTryBool(clipItem, 'isMulticamClip')) === true) {
      log(`clip: "${label}" is a merged/multicam clip -- getMediaFilePath() returns ONE of its `
        + 'sources; check that this is the one you meant', 'dim');
    }
    if ((await cprbTryBool(clipItem, 'hasProxy')) === true) {
      logDebug(`clip: "${label}" has a proxy attached -- sending the FULL-RES media path`);
    }

    // The invariant worth logging every time: source span should equal the
    // timeline duration times the speed. When it does not, the in/out are not
    // what we think they are (subclip offset, non-zero media start, retime).
    // start/end carry no speed or direction, so a retimed or reversed clip
    // decodes as a plain forward range in ComfyUI -- a known M2 limitation.
    try {
      const speed = typeof picked.getSpeed === 'function' ? await picked.getSpeed() : null;
      const duration = typeof picked.getDuration === 'function'
        ? cprbTickSeconds(await picked.getDuration()) : null;
      const reversed = await cprbTryBool(picked, 'isSpeedReversed');
      const span = endSeconds - startSeconds;
      logDebug(`clip: source ${startSeconds.toFixed(3)}..${endSeconds.toFixed(3)}s `
        + `(span ${span.toFixed(3)}s), timeline duration `
        + `${duration === null ? '?' : duration.toFixed(3)}s, speed ${JSON.stringify(speed)}, `
        + `reversed=${JSON.stringify(reversed)} -- expect span ~= duration * speed`);
      if (reversed === true || (typeof speed === 'number' && Math.abs(speed - 1) > 0.001)) {
        log(`clip: "${label}" is retimed (speed ${speed}${reversed === true ? ', reversed' : ''}) `
          + '-- ComfyUI receives the plain forward source range; the retime is not carried', 'dim');
      }
    } catch (error) {
      logDebug(`clip: invariant log skipped (${describeError(error)})`);
    }

    const sent = cprbSendExportReady(what, {
      type: 'export_ready',
      kind: 'clip',
      path: mediaPath,
      label,
      start_seconds: startSeconds,
      end_seconds: endSeconds
    });
    if (!sent) return;
    ok(`${what}: sent "${label}" ${startSeconds.toFixed(3)}s-${endSeconds.toFixed(3)}s `
      + `-> ${basename(mediaPath)} -- add a "Clip from Premiere" node and press Run`);
    logDebug(`${what}: full path ${mediaPath} (source in/out read off the CAST `
      + `ClipProjectItem; found via ${route})`);
  } catch (error) {
    fail(`${what} failed`, error);
  } finally {
    cprbExportBusy = false;
  }
}

/* ------------------------------ click glue ----------------------------- */

/**
 * Runs an async panel action from a click listener so NOTHING escapes:
 * a synchronous throw and a rejected promise both land as one logged line.
 * (Both actions above already contain their own errors; this is the second
 * belt, and it keeps main.js's wiring to one line per button.)
 */
function cprbRunClick(label, action) {
  try {
    const result = action();
    if (result && typeof result.catch === 'function') {
      result.catch((error) => fail(label, error));
    }
  } catch (error) {
    fail(label, error);
  }
}
