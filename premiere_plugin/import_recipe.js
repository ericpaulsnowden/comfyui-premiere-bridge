/*
 * import_recipe.js -- M1's whole point: a `pr_result` message becomes media
 * in a "ComfyUI Results" bin. Steps, each its own function, each fail-SOFT
 * past the import itself (a failed stamp/color/insert never undoes a
 * successful import):
 *
 *   findOrCreateBin -> importIntoBin -> stampProperties -> setColorLabel ->
 *   insertAtPlayhead
 *
 * Proven ground (docs/SPIKES.md LIVE RESULTS, S6 round -- Premiere 26.3,
 * uxp 9.3, win32):
 *   - the lockedAccess + executeTransaction action pattern (S6-B);
 *   - `importFiles([path], true)` returns true and the item is then
 *     ENUMERABLE at its exact stored path (S6-C);
 *   - the S6-C recorded DECISION: never findItemsMatchingMediaPath --
 *     enumerate the bin we control instead;
 *   - `Properties.getProperties(ClipProjectItem)` returns an object (S6-E).
 * Every OTHER Premiere call in this file is unproven on 26.3 and carries a
 * `VERIFY(...)` flag per the SPIKES.md discipline: probed defensively, a
 * throw degrades to a logged line.
 *
 * The protocol fields `color_label` and `insert_at_playhead` arrive as
 * ""/false from the M1 server (the ComfyUI node only grows those widgets in
 * later versions) -- the steps are implemented NOW and gate themselves on
 * the fields, so the panel side is already done when the node catches up.
 *
 * Results may arrive back-to-back (a ComfyUI batch): `cprbHandleResult`
 * serializes them through a promise queue so two imports can never
 * interleave their bin-create/enumerate phases (a race there would mint
 * duplicate bins).
 *
 * Depends on helpers.js (log/ok/bad/logDebug/sleep/cleanImportPath/samePath/
 * normalizePathForCompare/basename/collectClips/ppro).
 */
'use strict';

/** The bin name used when a pr_result omits/blanks `bin_name`. */
const CPRB_DEFAULT_BIN_NAME = 'ComfyUI Results';

/** Premiere's label colors in Preferences > Label Colors order, name ->
 * index for `createSetColorLabelAction(index)`.
 * VERIFY(spike-S6-followup): both the index order and the action itself are
 * unproven on 26.3 -- a live `pr.Constants` enum, when one matches, is
 * preferred over this map (see setColorLabel). Names are matched
 * case-insensitively. */
const CPRB_LABEL_COLOR_INDEX = {
  violet: 0, iris: 1, caribbean: 2, lavender: 3, cerulean: 4, forest: 5,
  rose: 6, mango: 7, purple: 8, blue: 9, teal: 10, magenta: 11, tan: 12,
  green: 13, brown: 14, yellow: 15
};

/* ------------------------- step: find/create bin ------------------------- */

/** How FolderItem names turned out to be readable, once discovered --
 * logged a single time, not per folder per result. */
let cprbFolderNameMode = null;

function cprbNoteFolderNameMode(mode) {
  if (cprbFolderNameMode === null) logDebug(`FolderItem name reads via ${mode}`);
  cprbFolderNameMode = mode;
}

/**
 * Reads a FolderItem's display name, or '' when unreadable.
 * VERIFY(spike-S6C-followup): whether 26.3's FolderItem exposes `.name` as a
 * property or `getName()` as a method is unverified -- property is probed
 * first, method as fallback, and which one worked is logged (once).
 */
async function folderDisplayName(folder) {
  try {
    const prop = folder.name;
    if (typeof prop === 'string' && prop) {
      cprbNoteFolderNameMode('the .name property');
      return prop;
    }
    if (prop && typeof prop.then === 'function') {
      const value = await prop;
      if (typeof value === 'string' && value) {
        cprbNoteFolderNameMode('the .name property (promise-valued)');
        return value;
      }
    }
  } catch (_) { /* fall through to the method probe */ }
  try {
    if (typeof folder.getName === 'function') {
      const value = await folder.getName();
      if (typeof value === 'string' && value) {
        cprbNoteFolderNameMode('the getName() method');
        return value;
      }
    }
  } catch (_) { /* no name readable for this folder */ }
  return '';
}

/** Scans the project ROOT's DIRECT children for a bin named `name`
 * (exact match). Direct children only, by design -- the bridge's bin lives
 * at the top level, and a same-named bin nested elsewhere is the user's. */
async function findBinByName(pr, root, name) {
  const items = await root.getItems();
  for (const item of items) {
    const folder = pr.FolderItem.cast(item);
    if (!folder) continue;
    if ((await folderDisplayName(folder)) === name) return folder;
  }
  return null;
}

/** Guards against a pathological session minting a bin per result if bin
 * names ever turn out unreadable (create succeeds, re-find fails): after one
 * create whose re-enumeration missed, this session stops creating. */
let cprbBinCreateMissed = false;

/**
 * Returns the FolderItem for `name` at the project root, creating it (ONE
 * labeled undo step) when absent. Returns null when neither find nor create
 * produced a usable handle -- the import then proceeds without a target bin.
 */
async function findOrCreateBin(pr, project, name) {
  const root = await project.getRootItem();
  const existing = await findBinByName(pr, root, name);
  if (existing) return existing;
  if (cprbBinCreateMissed) {
    logDebug(`bin "${name}" was created earlier but cannot be re-found by name -- not creating another`);
    return null;
  }
  let txnOk = false;
  // PROVEN shape (S6-B): lockedAccess + executeTransaction, one undo step.
  project.lockedAccess(() => {
    txnOk = project.executeTransaction((compound) => {
      // makeUnique = false ON PURPOSE (the S6-B spike passed `true` to keep
      // its reruns visible as separate bins): M1 must reuse ONE
      // "ComfyUI Results" bin forever, never mint "ComfyUI Results 2".
      compound.addAction(root.createBinAction(name, false));
    }, 'Create ComfyUI Results bin');
  });
  if (!txnOk) {
    logDebug(`create-bin transaction returned false for "${name}"`);
    return null;
  }
  // createBinAction returns an ACTION, not the bin -- re-enumerate for the
  // FolderItem handle.
  const created = await findBinByName(pr, root, name);
  if (created) {
    log(`created bin "${name}"`, 'dim');
  } else {
    cprbBinCreateMissed = true;
    logDebug(`bin "${name}" was created but not found on re-enumeration`);
  }
  return created;
}

/* --------------------------- step: import ---------------------------- */

/**
 * Best-effort "turn ingest off around the import" guard. Premiere's ingest
 * (transcode/copy on import) would silently reprocess every bridge result.
 * VERIFY(spike-S6-followup): `IngestSettings` IS a 26.3 module key (S6-E's
 * enumeration) but its API shape is unconfirmed -- every getter/setter probe
 * below is speculative and guarded. Rules:
 *   - only DISABLE when the current state was READABLE and === enabled
 *     (never blind-write a setting we could not read);
 *   - the returned restore function re-enables in the same discovered shape;
 *   - any dead end logs (dim) and the import proceeds -- this guard must
 *     never block an import.
 * @returns {Promise<null | (() => Promise<void>)>} restore fn, or null.
 */
async function disableIngestBestEffort(pr, project) {
  try {
    // -- acquire a settings object --
    let settings = null;
    let acquiredVia = '';
    if (pr.IngestSettings && typeof pr.IngestSettings.getSettings === 'function') {
      try {
        settings = await pr.IngestSettings.getSettings(project);
        acquiredVia = 'IngestSettings.getSettings(project)';
      } catch (_) { /* next shape */ }
    }
    if (!settings && pr.IngestSettings && typeof pr.IngestSettings.getIngestSettings === 'function') {
      try {
        settings = await pr.IngestSettings.getIngestSettings(project);
        acquiredVia = 'IngestSettings.getIngestSettings(project)';
      } catch (_) { /* next shape */ }
    }
    if (!settings && typeof project.getIngestSettings === 'function') {
      try {
        settings = await project.getIngestSettings();
        acquiredVia = 'project.getIngestSettings()';
      } catch (_) { /* fall through */ }
    }
    if (!settings) {
      logDebug('ingest guard: no IngestSettings getter matched -- importing without it');
      return null;
    }
    // -- read the current enabled state --
    let enabled = null;
    try {
      if (typeof settings.getIsIngestEnabled === 'function') enabled = await settings.getIsIngestEnabled();
      else if (typeof settings.isIngestEnabled === 'function') enabled = await settings.isIngestEnabled();
      else if (typeof settings.isIngestEnabled === 'boolean') enabled = settings.isIngestEnabled;
      else if (typeof settings.ingestEnabled === 'boolean') enabled = settings.ingestEnabled;
    } catch (_) { /* unreadable */ }
    if (enabled !== true) {
      // false = nothing to guard; null/undefined = unreadable, and writing a
      // setting we could not read is exactly the blast radius to avoid.
      logDebug(`ingest guard: ${enabled === false ? 'ingest already off' : 'enabled-state unreadable -- leaving it alone'} (via ${acquiredVia})`);
      return null;
    }
    // -- find a setter --
    let setEnabled = null;
    let setterName = '';
    if (typeof settings.setIsIngestEnabled === 'function') {
      setEnabled = (value) => settings.setIsIngestEnabled(value);
      setterName = 'setIsIngestEnabled';
    } else if (typeof settings.setIngestEnabled === 'function') {
      setEnabled = (value) => settings.setIngestEnabled(value);
      setterName = 'setIngestEnabled';
    }
    if (!setEnabled) {
      logDebug('ingest guard: ingest is ON but no setter shape matched -- importing anyway');
      return null;
    }
    await setEnabled(false);
    log(`ingest guard: ingest disabled during import (via ${acquiredVia} + ${setterName}; will restore)`, 'dim');
    return async () => {
      await setEnabled(true);
      logDebug('ingest guard: ingest restored');
    };
  } catch (error) {
    logDebug(`ingest guard probe failed (${error && error.message ? error.message : error}) -- importing without it`);
    return null;
  }
}

/**
 * Finds a ClipProjectItem in `bin` whose stored media path equals `path`,
 * enumerating the BIN's OWN children (S6-C's recorded DECISION -- never
 * findItemsMatchingMediaPath) across retry rounds, because import
 * registration can lag the importFiles call. An item that was NOT in the
 * before-import snapshot wins over a pre-existing same-path item (the
 * re-send case).
 * @returns {Promise<null | {clip: object, preexisting: boolean}>}
 */
async function findClipInBin(pr, bin, path, beforePaths, delays) {
  const rounds = delays || [0, 500, 1500, 3000];
  for (const delay of rounds) {
    if (delay) await sleep(delay);
    let items;
    try {
      items = await bin.getItems();
    } catch (error) {
      logDebug(`bin enumeration failed (${error && error.message ? error.message : error})`);
      return null;
    }
    let preexistingMatch = null;
    for (const item of items) {
      const clip = pr.ClipProjectItem.cast(item);
      if (!clip) continue;
      let stored = '';
      try { stored = await clip.getMediaFilePath(); } catch (_) { continue; }
      if (!samePath(stored, path)) continue;
      if (!beforePaths.has(normalizePathForCompare(stored))) {
        return { clip, preexisting: false };
      }
      if (!preexistingMatch) preexistingMatch = { clip, preexisting: true };
    }
    if (preexistingMatch) return preexistingMatch;
  }
  return null;
}

/** Finds a same-path clip among the project ROOT's direct children (where an
 * untargeted import lands), then -- last resort -- anywhere in the tree. */
async function findClipAtRoot(pr, project, path) {
  try {
    const root = await project.getRootItem();
    for (const item of await root.getItems()) {
      const clip = pr.ClipProjectItem.cast(item);
      if (!clip) continue;
      try {
        if (samePath(await clip.getMediaFilePath(), path)) return clip;
      } catch (_) { /* skip unreadable items */ }
    }
    for (const clip of await collectClips(pr, project, 200)) {
      try {
        if (samePath(await clip.getMediaFilePath(), path)) return clip;
      } catch (_) { /* ditto */ }
    }
  } catch (error) {
    logDebug(`root scan failed (${error && error.message ? error.message : error})`);
  }
  return null;
}

/**
 * Moves `item` into `bin`, probing action shapes.
 * VERIFY(spike-S6C-followup): NO move-item action is proven on 26.3 -- each
 * shape below is speculative; a throw moves to the next shape, and after a
 * transaction reports success the bin is re-enumerated to CONFIRM before
 * claiming the move happened.
 * @returns {Promise<boolean>} true only on a CONFIRMED move.
 */
async function moveItemIntoBin(pr, project, bin, item, path) {
  const shapes = [];
  if (typeof bin.createMoveItemAction === 'function') {
    shapes.push({ name: 'bin.createMoveItemAction(item)', make: () => bin.createMoveItemAction(item) });
  }
  if (typeof project.createMoveItemAction === 'function') {
    shapes.push({ name: 'project.createMoveItemAction(item, bin)', make: () => project.createMoveItemAction(item, bin) });
  }
  if (typeof item.createMoveToAction === 'function') {
    shapes.push({ name: 'item.createMoveToAction(bin)', make: () => item.createMoveToAction(bin) });
  }
  if (!shapes.length) {
    log('no move-item API found -- imported at project root; drag it into the bin manually', 'dim');
    return false;
  }
  for (const shape of shapes) {
    try {
      let txnOk = false;
      project.lockedAccess(() => {
        txnOk = project.executeTransaction((compound) => {
          compound.addAction(shape.make());
        }, 'Move ComfyUI result into bin');
      });
      if (!txnOk) {
        logDebug(`move via ${shape.name}: transaction returned false`);
        continue;
      }
      const confirmed = await findClipInBin(pr, bin, path, new Set(), [0, 500]);
      if (confirmed) {
        logDebug(`moved into the bin via ${shape.name}`);
        return true;
      }
      // The transaction claimed success -- do NOT stack further move
      // attempts on top of whatever it actually did.
      logDebug(`move via ${shape.name}: transaction ok but the item is not in the bin -- drag it in manually`);
      return false;
    } catch (error) {
      logDebug(`move via ${shape.name} threw: ${error && error.message ? error.message : error}`);
    }
  }
  log('no move-item shape worked -- imported at project root; drag it into the bin manually', 'dim');
  return false;
}

/**
 * Imports `path` and recovers the new item's handle.
 * @param {object|null} bin - target FolderItem, or null (import lands at root).
 * @returns {Promise<{imported: boolean, item: object|null,
 *   where: 'bin'|'moved'|'root'|'missing'|'failed', detail?: string}>}
 */
async function importIntoBin(pr, project, bin, path) {
  // BEFORE-import snapshot of the bin's stored paths: lets the finder prefer
  // the freshly-imported item and name the "this file was already in the
  // bin" re-send case.
  const beforePaths = new Set();
  if (bin) {
    try {
      for (const item of await bin.getItems()) {
        const clip = pr.ClipProjectItem.cast(item);
        if (!clip) continue;
        try {
          beforePaths.add(normalizePathForCompare(await clip.getMediaFilePath()));
        } catch (_) { /* unreadable item -- skip */ }
      }
    } catch (error) {
      logDebug(`bin snapshot failed (${error && error.message ? error.message : error})`);
    }
  }

  const restoreIngest = await disableIngestBestEffort(pr, project);
  let imported = false;
  let importedDirect = false;
  try {
    if (bin) {
      // VERIFY(spike-S6C-followup): Adobe samples suggest a 3-arg
      // importFiles(paths, suppressUI, targetBin); unproven on 26.3. Probed
      // first because landing directly in the bin skips the whole
      // root-fallback/move dance. `.length` is informational only (native
      // bindings often report 0) -- the real test is the call itself; a
      // throw or a non-true return falls back to the PROVEN 2-arg call.
      let fnLen = '?';
      try { fnLen = String(project.importFiles.length); } catch (_) { /* informational */ }
      try {
        const result3 = await project.importFiles([path], true, bin);
        if (result3 === true) {
          imported = true;
          importedDirect = true;
          logDebug(`imported directly into bin (3-arg importFiles; .length=${fnLen})`);
        } else {
          logDebug(`3-arg importFiles returned ${String(result3)} (.length=${fnLen}) -- falling back to 2-arg`);
        }
      } catch (error) {
        logDebug(`3-arg importFiles threw (${error && error.message ? error.message : error}) -- falling back to 2-arg`);
      }
    }
    if (!imported) {
      // PROVEN (S6-C): 2-arg importFiles returns true and the item is then
      // enumerable at its stored path.
      const result2 = await project.importFiles([path], true);
      if (result2 !== true) {
        return { imported: false, item: null, where: 'failed', detail: `importFiles returned ${String(result2)}` };
      }
      imported = true;
    }
  } catch (error) {
    return { imported: false, item: null, where: 'failed', detail: (error && error.message ? error.message : String(error)) };
  } finally {
    if (restoreIngest) {
      try {
        await restoreIngest();
      } catch (error) {
        // The one ingest-guard failure that must be LOUD: ingest was turned
        // off and could not be turned back on.
        bad(`ingest guard: ingest was disabled and could NOT be restored (${error && error.message ? error.message : error}) -- check File > Project Settings > Ingest`);
      }
    }
  }

  // Recover the item handle: the bin's own children first...
  if (bin) {
    const found = await findClipInBin(pr, bin, path, beforePaths);
    if (found) {
      if (found.preexisting && !importedDirect) {
        logDebug('this file was already in the bin -- using the existing item');
      }
      return { imported: true, item: found.clip, where: 'bin' };
    }
  }
  // ...then the project root (import landed there instead), with a move
  // attempt when we do have a bin.
  const atRoot = await findClipAtRoot(pr, project, path);
  if (atRoot) {
    if (bin && (await moveItemIntoBin(pr, project, bin, atRoot, path))) {
      return { imported: true, item: atRoot, where: 'moved' };
    }
    return { imported: true, item: atRoot, where: 'root' };
  }
  // importFiles said true but nothing matches the path anywhere -- the
  // S6-C "PARTIAL" shape (typically a path not visible on this machine).
  return { imported: true, item: null, where: 'missing' };
}

/* ------------------------ step: stamp properties ---------------------- */

/**
 * Write-only bridge bookkeeping on the imported item: `comfy.label`,
 * `comfy.sent_ts`, `comfy.source`. GET is PROVEN (S6-E:
 * Properties.getProperties(ClipProjectItem) returns an object); every SETTER
 * shape is VERIFY(spike-S6E-set) -- probed with the first field, the winner
 * reused for the rest, and whichever worked (or that none did) is logged.
 * MUST fail soft: tagging never blocks an import.
 */
async function stampProperties(pr, item, meta) {
  try {
    if (!pr.Properties || typeof pr.Properties.getProperties !== 'function') {
      logDebug('properties: no Properties.getProperties on this build -- items left untagged');
      return;
    }
    let props = null;
    try {
      props = await pr.Properties.getProperties(item);
    } catch (error) {
      logDebug(`properties: getProperties failed (${error && error.message ? error.message : error}) -- items left untagged`);
      return;
    }
    if (!props) {
      logDebug('properties: getProperties returned null -- items left untagged');
      return;
    }
    const fields = [
      ['comfy.label', String(meta.label == null ? '' : meta.label)],
      ['comfy.sent_ts', String(meta.sent_ts == null ? '' : meta.sent_ts)],
      ['comfy.source', 'comfyui-premiere-bridge']
    ];
    // VERIFY(spike-S6E-set): none of these setter shapes is proven on 26.3.
    const shapes = [
      {
        name: 'setProperty(name, value)',
        usable: typeof props.setProperty === 'function',
        apply: async (key, value) => { await props.setProperty(key, value); return true; }
      },
      {
        name: 'set(name, value)',
        usable: typeof props.set === 'function',
        apply: async (key, value) => { await props.set(key, value); return true; }
      },
      {
        name: 'direct assignment (persistence unverified)',
        usable: true,
        apply: async (key, value) => { props[key] = value; return props[key] === value; }
      }
    ];
    let chosen = null;
    const [firstKey, firstValue] = fields[0];
    for (const shape of shapes) {
      if (!shape.usable) continue;
      try {
        if (await shape.apply(firstKey, firstValue)) { chosen = shape; break; }
        logDebug(`properties: ${shape.name} did not take`);
      } catch (error) {
        logDebug(`properties: ${shape.name} threw: ${error && error.message ? error.message : error}`);
      }
    }
    if (!chosen) {
      log('properties: no setter shape worked -- item imported untagged (VERIFY spike-S6E-set)', 'dim');
      return;
    }
    for (let i = 1; i < fields.length; i++) {
      try {
        await chosen.apply(fields[i][0], fields[i][1]);
      } catch (error) {
        logDebug(`properties: ${fields[i][0]} via ${chosen.name} threw: ${error && error.message ? error.message : error}`);
      }
    }
    logDebug(`properties: tagged via ${chosen.name}`);
  } catch (error) {
    logDebug(`properties step failed (${error && error.message ? error.message : error}) -- import unaffected`);
  }
}

/* ------------------------- step: color label -------------------------- */

/**
 * Sets the item's label color by human name; an empty/absent name means
 * SKIP, per the protocol ("" = no labeling). A live `pr.Constants` enum
 * whose key matches the name wins over the hardcoded index map (and what
 * the Constants probe finds is logged -- it is spike data).
 * VERIFY(spike-S6-followup): createSetColorLabelAction is unproven live.
 * Fails soft; the import is never undone.
 */
async function setColorLabel(pr, project, item, colorName) {
  const name = String(colorName == null ? '' : colorName).trim().toLowerCase();
  if (!name) return;
  try {
    let index = null;
    let via = '';
    try {
      if (pr.Constants) {
        const enumKeys = Object.keys(pr.Constants).filter((k) => /label|color/i.test(k));
        if (enumKeys.length) {
          logDebug(`color label: Constants keys matching /label|color/i: ${JSON.stringify(enumKeys)}`);
        }
        for (const enumKey of enumKeys) {
          const enumObj = pr.Constants[enumKey];
          if (!enumObj || typeof enumObj !== 'object') continue;
          for (const key of Object.keys(enumObj)) {
            if (key.toLowerCase() === name) {
              index = enumObj[key];
              via = `Constants.${enumKey}.${key}`;
              break;
            }
          }
          if (index !== null) break;
        }
      }
    } catch (_) { /* fall back to the name map */ }
    if (index === null && Object.prototype.hasOwnProperty.call(CPRB_LABEL_COLOR_INDEX, name)) {
      index = CPRB_LABEL_COLOR_INDEX[name];
      via = 'name map';
    }
    if (index === null) {
      log(`color label: unknown color "${colorName}" -- skipped`, 'dim');
      return;
    }
    if (typeof item.createSetColorLabelAction !== 'function') {
      log('color label: no createSetColorLabelAction on this item -- skipped (VERIFY spike-S6-followup)', 'dim');
      return;
    }
    let txnOk = false;
    project.lockedAccess(() => {
      txnOk = project.executeTransaction((compound) => {
        compound.addAction(item.createSetColorLabelAction(index));
      }, 'Set ComfyUI color label');
    });
    if (txnOk) {
      log(`color label: ${name} (index ${JSON.stringify(index)}, via ${via})`, 'dim');
    } else {
      log('color label: transaction returned false -- skipped', 'dim');
    }
  } catch (error) {
    log(`color label failed (${error && error.message ? error.message : error}) -- import unaffected`, 'dim');
  }
}

/* ----------------------- step: insert at playhead ---------------------- */

/**
 * Overwrites the item onto the active sequence at the playhead, on "the
 * track above": topmost existing index + 1 when the API accepts targeting a
 * not-yet-existing track, else the topmost existing track -- exactly which
 * was chosen is logged. Runs ONLY when the message says
 * `insert_at_playhead: true` (the M1 server always sends false, so this
 * ships gated off -- it is the highest-blast-radius step).
 * VERIFY(spike-S6-followup): SequenceEditor.getEditor /
 * createOverwriteItemAction / track-count reads are all unproven live; only
 * getPlayerPosition() is proven (S6-D). Fails soft at every stage.
 */
async function insertAtPlayhead(pr, project, item) {
  try {
    const sequence = await project.getActiveSequence();
    if (!sequence) {
      log('insert: no active sequence -- skipped', 'dim');
      return;
    }
    const position = await sequence.getPlayerPosition(); // PROVEN read (S6-D)
    if (!pr.SequenceEditor || typeof pr.SequenceEditor.getEditor !== 'function') {
      log('insert: no SequenceEditor.getEditor on this build -- skipped (VERIFY spike-S6-followup)', 'dim');
      return;
    }
    const editor = await pr.SequenceEditor.getEditor(sequence);
    if (!editor || typeof editor.createOverwriteItemAction !== 'function') {
      log('insert: no createOverwriteItemAction on the editor -- skipped (VERIFY spike-S6-followup)', 'dim');
      return;
    }
    // Track count: probe shapes; with NO readable count, skip rather than
    // guess -- an overwrite on a wrongly-guessed track can destroy content.
    let trackCount = null;
    let countVia = '';
    try {
      if (typeof sequence.getVideoTrackCount === 'function') {
        trackCount = await sequence.getVideoTrackCount();
        countVia = 'getVideoTrackCount()';
      }
    } catch (error) {
      logDebug(`insert: getVideoTrackCount threw: ${error && error.message ? error.message : error}`);
    }
    if (trackCount === null || typeof trackCount !== 'number') {
      try {
        const tracks = sequence.videoTracks;
        if (tracks && typeof tracks.length === 'number') {
          trackCount = tracks.length;
          countVia = 'videoTracks.length';
        }
      } catch (_) { /* no second shape either */ }
    }
    if (typeof trackCount !== 'number' || !(trackCount >= 0)) {
      log('insert: could not read the video track count -- skipped rather than guess a track', 'dim');
      return;
    }
    const audioTrackIndex = 0; // stills carry no audio; index 0 is the benign placeholder
    // "The track above": try index == trackCount (a NOT-yet-existing track
    // above the topmost -- content-safe if the API auto-creates it), then
    // fall back to the topmost existing track.
    const candidates = [trackCount];
    if (trackCount - 1 >= 0) candidates.push(trackCount - 1);
    for (const videoTrackIndex of candidates) {
      try {
        let txnOk = false;
        project.lockedAccess(() => {
          txnOk = project.executeTransaction((compound) => {
            compound.addAction(
              editor.createOverwriteItemAction(item, position, videoTrackIndex, audioTrackIndex)
            );
          }, 'Insert ComfyUI result at playhead');
        });
        if (txnOk) {
          const which = videoTrackIndex === trackCount ? 'new track above topmost' : 'topmost existing track';
          log(`insert: overwrote onto video track index ${videoTrackIndex} (${which}; count ${trackCount} via ${countVia}) at the playhead`, 'dim');
          return;
        }
        logDebug(`insert: transaction returned false for video track index ${videoTrackIndex}`);
      } catch (error) {
        logDebug(`insert: video track index ${videoTrackIndex} threw: ${error && error.message ? error.message : error}`);
      }
    }
    log('insert: no track index accepted the overwrite -- skipped (import unaffected)', 'dim');
  } catch (error) {
    log(`insert failed (${error && error.message ? error.message : error}) -- import unaffected`, 'dim');
  }
}

/* --------------------------- orchestration ---------------------------- */

/**
 * The full M1 recipe for one `pr_result`: bin -> import -> stamp -> color ->
 * insert. Exactly ONE headline activity-log line per result ("Imported: ..."
 * or the specific failure); degraded steps add dim detail lines. Per-step
 * errors are contained -- a failed stamp/color/insert never undoes a
 * successful import.
 */
async function handlePrResult(pr, msg) {
  const path = cleanImportPath(msg && msg.path);
  const label = msg && msg.label ? String(msg.label) : basename(path);
  if (!path) {
    bad('pr_result with an empty path -- ignored');
    return;
  }
  logDebug(`pr_result: ${path}`);

  let project = null;
  try {
    project = await pr.Project.getActiveProject();
  } catch (error) {
    bad(`Import failed for "${label}": ${error && error.message ? error.message : error}`);
    return;
  }
  if (!project) {
    bad(`Import failed for "${label}": no project is open in Premiere`);
    return;
  }

  const binName =
    msg && typeof msg.bin_name === 'string' && msg.bin_name.trim()
      ? msg.bin_name.trim()
      : CPRB_DEFAULT_BIN_NAME;
  let bin = null;
  try {
    bin = await findOrCreateBin(pr, project, binName);
  } catch (error) {
    logDebug(`bin step failed (${error && error.message ? error.message : error}) -- importing without a target bin`);
  }
  if (!bin) logDebug(`no "${binName}" bin handle -- the import will land at the project root`);

  const outcome = await importIntoBin(pr, project, bin, path);
  if (!outcome.imported) {
    bad(`Import failed: ${label} (${outcome.detail || 'see the lines above'})`);
    return;
  }
  if (outcome.where === 'missing') {
    bad(`Import reported success but "${label}" is nowhere in the project -- is ${path} visible on this machine?`);
    return;
  }
  ok(`Imported: ${label}${outcome.where === 'root' ? ' (at project root)' : ''}`);

  if (!outcome.item) {
    logDebug('no item handle recovered -- skipping tag/color/insert');
    return;
  }
  await stampProperties(pr, outcome.item, {
    label: msg.label,
    sent_ts: msg.sent_ts
  });
  await setColorLabel(pr, project, outcome.item, msg.color_label);
  if (msg.insert_at_playhead === true) {
    await insertAtPlayhead(pr, project, outcome.item);
  }
}

/* ----------------------- global entry (queued) ------------------------ */

/** Serializes pr_results in arrival order; the caught tail keeps the chain
 * alive after any failure. */
let cprbResultQueue = Promise.resolve();

/**
 * THE global entry point connection.js dispatches `pr_result` messages to.
 * Returns the queued promise (already error-contained) so callers may await
 * or fire-and-forget.
 */
function cprbHandleResult(msg) {
  cprbResultQueue = cprbResultQueue
    .then(() => handlePrResult(ppro(), msg))
    .catch((error) => {
      bad(`Import failed for "${(msg && (msg.label || msg.path)) || 'result'}": ${error && error.message ? error.message : error}`);
    });
  return cprbResultQueue;
}

if (typeof window !== 'undefined') {
  window.cprbHandleResult = cprbHandleResult;
}
