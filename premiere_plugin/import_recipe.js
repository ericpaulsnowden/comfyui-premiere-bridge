/*
 * import_recipe.js -- M1's whole point: a `pr_result` message becomes media
 * in a "ComfyUI Results" bin. Steps, each its own function, each fail-SOFT
 * past the import itself (a failed stamp/color/insert never undoes a
 * successful import):
 *
 *   findOrCreateBin -> importIntoBin -> stampProperties -> setColorLabel ->
 *   insertAtPlayhead
 *
 * Proven ground (docs/SPIKES.md LIVE RESULTS: the S6 round, plus the
 * owner's first full M1 recipe run, 2026-07-24 -- Premiere 26.3, uxp 9.3,
 * win32):
 *   - lockedAccess + executeTransaction, one labeled undo step (S6-B);
 *   - FolderItem `.name` reads as a plain string property (26.3 live);
 *   - 3-arg `importFiles([path], true, bin)` returns true and the item
 *     lands IN the bin (26.3 live); the S6-C-proven 2-arg call stays as
 *     the fallback, and items are recovered by enumerating the bin we
 *     control (the S6-C DECISION: never findItemsMatchingMediaPath);
 *   - `Properties.getProperties(ClipProjectItem)` returns an object (S6-E);
 *   - `Constants.ProjectItemColorLabel` + `createSetColorLabelAction`
 *     really set the label color (26.3 live: lavender, index 3).
 * DISPROVEN by the same 2026-07-24 run: every speculative IngestSettings
 * acquisition shape (see disableIngestBestEffort), the `setProperty`/`set`
 * Properties setters (see stampProperties, which now read-back-verifies
 * instead of trusting a write), and every createOverwriteItemAction shape
 * we shipped -- "Invalid parameter." on all attempts -- so insertAtPlayhead
 * is now a DIAGNOSTIC PROBE (its docblock has the full plan). Anything
 * still unproven on 26.3 carries a `VERIFY(...)` flag per the SPIKES.md
 * discipline: probed defensively, a throw degrades to a logged line.
 *
 * The protocol fields `color_label` (node v0.9.2+) and `insert_at_playhead`
 * (node v0.9.3+) gate their steps themselves: "" / false -- the node
 * defaults -- mean skip, so older nodes simply never trigger them.
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

/** Premiere's 16 label colors in Preferences > Label Colors order, name ->
 * index for `createSetColorLabelAction(index)`. LAST-RESORT fallback for a
 * build without the live enum: `Constants.ProjectItemColorLabel` is PROVEN
 * on 26.3 (owner live run 2026-07-24: LAVENDER -> index 3, which matches
 * this map) and is always preferred (see setColorLabel). The doc site's
 * enum listing has only 15 names -- no caribbean -- but the live index 3
 * for lavender confirms the 16-slot UI order encoded here. Names are
 * matched case-insensitively. */
const CPRB_LABEL_COLOR_INDEX = {
  violet: 0, iris: 1, caribbean: 2, lavender: 3, cerulean: 4, forest: 5,
  rose: 6, mango: 7, purple: 8, blue: 9, teal: 10, magenta: 11, tan: 12,
  green: 13, brown: 14, yellow: 15
};

/** error -> printable message (new-code helper; older lines keep their
 * original inline form). */
function cprbErrMsg(error) {
  return error && error.message ? error.message : String(error);
}

/* ------------------------- step: find/create bin ------------------------- */

/** The `.name` property is the PROVEN 26.3 read (owner live run
 * 2026-07-24) -- silent. A FALLBACK mode engaging would be news: logged
 * once per session at debug. */
let cprbFolderNameFallbackLogged = false;

function cprbNoteFolderNameMode(mode) {
  if (mode === 'the .name property') return;
  if (cprbFolderNameFallbackLogged) return;
  cprbFolderNameFallbackLogged = true;
  logDebug(`FolderItem name read via ${mode} (expected the proven .name property)`);
}

/**
 * Reads a FolderItem's display name, or '' when unreadable. PROVEN on 26.3
 * (owner live run 2026-07-24): `.name` is a plain string property -- the
 * primary path. getName() stays as a guarded fallback for other builds.
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
 * Ingest-guard session memory. The owner's 2026-07-24 live run PROVED the
 * three speculative acquisition shapes this file used to probe on every
 * import -- `IngestSettings.getSettings(project)`,
 * `IngestSettings.getIngestSettings(project)`, `project.getIngestSettings()`
 * -- do not exist on 26.3 ("no IngestSettings getter matched"). They are
 * gone. What that run never probed is the shape Adobe's reference actually
 * documents (research/premiere-tier2/r2 §2.3):
 * `ProjectSettings.getIngestSettings(project)` -> IngestSettings, whose
 * WHOLE documented surface is getIsIngestEnabled() / setIngestEnabled(b).
 * VERIFY(spike-S6-followup): that documented shape is still unproven live,
 * so it is probed once, remembered for the session, and never logged
 * per-import again -- at most ONE debug line per session either way.
 * 'unknown' = not probed yet; 'ok' = accessor works; 'absent' = unusable on
 * this build, in which case imports proceed unguarded. That is acceptable:
 * ingest is off by default, every live import so far linked in place, and
 * the guard only matters for users running Media Browser ingest in a
 * Copy/Transcode mode.
 */
let cprbIngestAccess = 'unknown';

/**
 * Best-effort "turn ingest off around the import" guard. Premiere's ingest
 * (transcode/copy on import) would silently reprocess every bridge result.
 * Rules (unchanged):
 *   - only DISABLE when the current state was READABLE and === enabled
 *     (never blind-write a setting we could not read);
 *   - the returned restore function re-enables the same way;
 *   - any dead end degrades quietly and the import proceeds -- this guard
 *     must never block an import.
 * @returns {Promise<null | (() => Promise<void>)>} restore fn, or null.
 */
async function disableIngestBestEffort(pr, project) {
  try {
    if (cprbIngestAccess === 'absent') return null;
    let settings = null;
    try {
      if (pr.ProjectSettings && typeof pr.ProjectSettings.getIngestSettings === 'function') {
        settings = await pr.ProjectSettings.getIngestSettings(project);
      }
    } catch (error) {
      if (cprbIngestAccess === 'unknown') {
        logDebug(`ingest guard: ProjectSettings.getIngestSettings threw (${cprbErrMsg(error)}) -- imports proceed unguarded this session`);
      }
      cprbIngestAccess = 'absent';
      return null;
    }
    if (!settings || typeof settings.getIsIngestEnabled !== 'function') {
      if (cprbIngestAccess === 'unknown') {
        logDebug('ingest guard: ingest settings unreadable on this build -- imports proceed unguarded this session');
      }
      cprbIngestAccess = 'absent';
      return null;
    }
    let enabled = null;
    try {
      enabled = await settings.getIsIngestEnabled();
    } catch (error) {
      if (cprbIngestAccess === 'unknown') {
        logDebug(`ingest guard: getIsIngestEnabled threw (${cprbErrMsg(error)}) -- imports proceed unguarded this session`);
      }
      cprbIngestAccess = 'absent';
      return null;
    }
    if (cprbIngestAccess === 'unknown') {
      // One line per session: the documented shape WORKS -- spike data that
      // resolves this guard's VERIFY on the next paste-back.
      logDebug('ingest guard: readable via ProjectSettings.getIngestSettings (documented shape) -- will guard whenever ingest is on');
      cprbIngestAccess = 'ok';
    }
    if (enabled !== true) return null; // off (the normal state): nothing to guard, nothing to log
    if (typeof settings.setIngestEnabled !== 'function') {
      // Rare and worth seeing on every affected import: the user runs
      // ingest and this panel cannot pause it.
      log('ingest guard: ingest is ON but this build has no setIngestEnabled -- importing anyway (the result may be copied/transcoded)', 'dim');
      return null;
    }
    await settings.setIngestEnabled(false);
    log('ingest guard: ingest was ON -- disabled during import (will restore)', 'dim');
    return async () => {
      await settings.setIngestEnabled(true);
      logDebug('ingest guard: ingest restored');
    };
  } catch (error) {
    logDebug(`ingest guard failed (${cprbErrMsg(error)}) -- importing without it`);
    return null;
  }
}

/* ------------------ item identity (which item is the import?) ------------------ */

/*
 * WHY THIS EXISTS (release review 2026-09-13, item 9). importFiles returns a
 * bare boolean, so the recipe has to FIND the item it just created. Until
 * 2026-11-03 it identified items by MEDIA PATH: a before-import snapshot of
 * the bin's paths, and "first same-path item" at the root / in the tree. Two
 * items can share a path -- the owner's real case is an untrimmed source clip
 * that Send to Premiere LINKS IN PLACE (§10.5) and that was already imported
 * by an earlier send -- and then both looked pre-existing and the first OLD
 * item won: tagging, colouring and the playhead insert all hit the old item,
 * old in/out trim included.
 *
 * THE RULE: an item is NEW when it is absent BY IDENTITY from the pre-import
 * snapshot, and identity is `ProjectItem.getId()` -- the only identifier the
 * Premiere UXP API documents for a project item ("Get id of projectItem",
 * returns string, Since 25.6, on the ProjectItem BASE class; this manifest
 * requires 26.3; Adobe's own premiere-api sample, projectPanel.ts, calls it
 * un-awaited on a raw item from rootItem.getItems()). It is read off the RAW
 * item (the documented receiver) and, for collectClips' cast-only handles,
 * through `ProjectItem.cast(clip)`; awaited in case a build returns a promise.
 *
 * NOT object identity ("absent by reference"): nothing documents a UXP item
 * wrapper as the SAME JS object across two getItems() calls, and the cast()
 * wrappers every lookup uses are minted per call. If references were fresh
 * per enumeration, every item would look new and the old item would be
 * picked while being labeled "new" -- the bug, hidden. Not a composite
 * (name/label/in-out) either: a re-send of an untouched earlier import is
 * indistinguishable from it on every one of those fields.
 *
 * VERIFY(spike-S6-followup): getId() has never been probed live by this
 * panel. Degradation is explicit and conservative: if ANY snapshotted clip's
 * id is unreadable, or two distinct items in one listing share an id (so it
 * is not an identity), the snapshot is marked untrusted and every lookup
 * falls back to the pre-2026-11-03 path behaviour, with one log line per
 * session saying so. Collision-free cases (no other item references the
 * file) pick the same item under either rule.
 */

/** Last identity-mode line logged (one line per mode per session). */
let cprbIdentityModeLogged = '';

function cprbNoteIdentityMode(mode, message) {
  if (cprbIdentityModeLogged === mode) return;
  cprbIdentityModeLogged = mode;
  logDebug(message);
}

/** `ProjectItem.getId()` as a string key ('id:<value>'), or null when no id
 * is readable. `handle` is a raw ProjectItem or a cast ClipProjectItem; the
 * raw receiver is tried first, then `ProjectItem.cast(handle)`. */
async function cprbItemIdentity(pr, handle) {
  const read = async (obj) => {
    if (!obj || typeof obj.getId !== 'function') return '';
    const value = await obj.getId();
    if (typeof value === 'string') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    return '';
  };
  try {
    const id = await read(handle);
    if (id) return `id:${id}`;
  } catch (_) { /* try the cast receiver */ }
  try {
    if (pr.ProjectItem && typeof pr.ProjectItem.cast === 'function') {
      const id = await read(pr.ProjectItem.cast(handle));
      if (id) return `id:${id}`;
    }
  } catch (_) { /* no readable id */ }
  return null;
}

/** A snapshot under which every item counts as new (the starting point
 * cprbSnapshotBeforeImport fills in). */
function cprbEmptySnapshot() {
  return { ids: new Set(), binPaths: new Set(), idsTrusted: true };
}

/**
 * BEFORE-import snapshot covering every scope a post-import lookup searches:
 * the target bin's children (uncapped, plus their stored paths for the
 * legacy rule), the project root's direct children (uncapped), and the same
 * capped tree walk the last-resort lookup uses (collectClips, 200).
 * @returns {Promise<{ids: Set<string>, binPaths: Set<string>, idsTrusted: boolean}>}
 */
async function cprbSnapshotBeforeImport(pr, project, bin) {
  const before = cprbEmptySnapshot();
  let unreadable = 0;
  let duplicated = false;
  const record = async (handles) => {
    const listingIds = new Set();
    for (const handle of handles) {
      const key = await cprbItemIdentity(pr, handle);
      if (!key) { unreadable += 1; continue; }
      if (listingIds.has(key)) duplicated = true; // two items, one "id"
      listingIds.add(key);
      before.ids.add(key);
    }
  };
  if (bin) {
    try {
      const raws = [];
      for (const item of await bin.getItems()) {
        const clip = pr.ClipProjectItem.cast(item);
        if (!clip) continue;
        raws.push(item);
        try {
          before.binPaths.add(normalizePathForCompare(await clip.getMediaFilePath()));
        } catch (_) { /* unreadable item -- skip */ }
      }
      await record(raws);
    } catch (error) {
      logDebug(`bin snapshot failed (${cprbErrMsg(error)})`);
      before.idsTrusted = false;
    }
  }
  try {
    const root = await project.getRootItem();
    await record((await root.getItems()).filter((item) => pr.ClipProjectItem.cast(item)));
    await record(await collectClips(pr, project, 200));
  } catch (error) {
    logDebug(`project snapshot failed (${cprbErrMsg(error)})`);
    before.idsTrusted = false;
  }
  if (unreadable || duplicated) before.idsTrusted = false;
  if (before.idsTrusted) {
    cprbNoteIdentityMode('id', 'item identity: ProjectItem.getId() -- a re-send of a file '
      + 'already in the project picks the freshly imported item');
  } else {
    cprbNoteIdentityMode('path', 'item identity: ProjectItem.getId() '
      + `${duplicated ? 'is not unique' : 'is unreadable'} on this build -- falling back to `
      + 'media-path matching (a re-send of a file already in the project may pick the OLDER item)');
  }
  return before;
}

/** true = NEW (absent by identity from `before`), false = pre-existing,
 * null = undecidable (snapshot untrusted, or this item has no readable id). */
async function cprbIsNewItem(pr, before, handle) {
  if (!before || !before.idsTrusted) return null;
  const key = await cprbItemIdentity(pr, handle);
  if (!key) return null;
  return !before.ids.has(key);
}

/**
 * Finds a ClipProjectItem in `bin` whose stored media path equals `path`,
 * enumerating the BIN's OWN children (S6-C's recorded DECISION -- never
 * findItemsMatchingMediaPath) across retry rounds, because import
 * registration can lag the importFiles call. A NEW item (absent by identity
 * from `before`, see cprbSnapshotBeforeImport) wins over a pre-existing
 * same-path item -- the re-send case. While identity can decide, a round
 * that only finds pre-existing same-path items keeps polling (the fresh one
 * may not be registered yet) and the first pre-existing match is the answer
 * only after the last round. Undecidable items take the legacy rule ("new"
 * = path absent from the bin before), which also returns at once. `raw` is
 * the UN-CAST ProjectItem handle from the bin enumeration -- the insert
 * probe needs it (Adobe's own sample passes raw ProjectItems, never cast
 * wrappers, to SequenceEditor actions).
 * @returns {Promise<null | {clip: object, raw: object, preexisting: boolean|null}>}
 * `preexisting`: false = new; true = identity says it predates the import;
 * null = the legacy path rule could not tell (the bin already held this file).
 */
async function findClipInBin(pr, bin, path, before, delays) {
  const rounds = delays || [0, 500, 1500, 3000];
  let preexistingMatch = null;
  for (const delay of rounds) {
    if (delay) await sleep(delay);
    let items;
    try {
      items = await bin.getItems();
    } catch (error) {
      logDebug(`bin enumeration failed (${error && error.message ? error.message : error})`);
      return preexistingMatch;
    }
    let roundMatch = null;
    let undecided = false;
    for (const item of items) {
      const clip = pr.ClipProjectItem.cast(item);
      if (!clip) continue;
      let stored = '';
      try { stored = await clip.getMediaFilePath(); } catch (_) { continue; }
      if (!samePath(stored, path)) continue;
      const isNew = await cprbIsNewItem(pr, before, item);
      if (isNew === true) return { clip, raw: item, preexisting: false };
      if (isNew === null) {
        // Legacy path rule: a path the bin did not hold before is new; one
        // it did hold is AMBIGUOUS (this item, or an older same-file one).
        undecided = true;
        if (!before.binPaths.has(normalizePathForCompare(stored))) {
          return { clip, raw: item, preexisting: false };
        }
      }
      if (!roundMatch) roundMatch = { clip, raw: item, preexisting: isNew === false ? true : null };
    }
    if (roundMatch && !preexistingMatch) preexistingMatch = roundMatch;
    if (preexistingMatch && undecided) return preexistingMatch;
  }
  return preexistingMatch;
}

/** Finds a same-path clip among the project ROOT's direct children (where an
 * untargeted import lands), then -- last resort -- anywhere in the tree.
 * Same identity rule as findClipInBin: a NEW item anywhere beats a
 * pre-existing one (the user's own original clip often sits at the root);
 * an undecidable item is taken as found, exactly as before 2026-11-03; a
 * pre-existing match is returned only when no new or undecidable one exists.
 * @returns {Promise<null | {clip: object, raw: object|null, preexisting: boolean|null}>}
 * `raw` is the un-cast ProjectItem when the scan had one (the collectClips
 * fallback yields cast handles only, so raw is null there); `preexisting`
 * is null when identity could not decide. */
async function findClipAtRoot(pr, project, path, before) {
  let preexistingMatch = null;
  const judge = async (handle, clip, raw) => {
    const isNew = await cprbIsNewItem(pr, before, handle);
    if (isNew !== false) return { clip, raw, preexisting: isNew === null ? null : false };
    if (!preexistingMatch) preexistingMatch = { clip, raw, preexisting: true };
    return null;
  };
  try {
    const root = await project.getRootItem();
    for (const item of await root.getItems()) {
      const clip = pr.ClipProjectItem.cast(item);
      if (!clip) continue;
      try {
        if (!samePath(await clip.getMediaFilePath(), path)) continue;
      } catch (_) { continue; /* skip unreadable items */ }
      const found = await judge(item, clip, item);
      if (found) return found;
    }
    for (const clip of await collectClips(pr, project, 200)) {
      try {
        if (!samePath(await clip.getMediaFilePath(), path)) continue;
      } catch (_) { continue; /* ditto */ }
      const found = await judge(clip, clip, null);
      if (found) return found;
    }
  } catch (error) {
    logDebug(`root scan failed (${error && error.message ? error.message : error})`);
  }
  return preexistingMatch;
}

/**
 * Moves `item` into `bin`, probing action shapes.
 * VERIFY(spike-S6C-followup): NO move-item action is proven on 26.3 (the
 * 2026-07-24 live run imported straight into the bin, so this path never
 * ran) -- each shape below is speculative; a throw moves to the next shape,
 * and after a transaction reports success the bin is re-enumerated to
 * CONFIRM before claiming the move happened. `before` is importIntoBin's
 * pre-import snapshot (see cprbSnapshotBeforeImport).
 * @returns {Promise<boolean>} true only on a CONFIRMED move.
 */
async function moveItemIntoBin(pr, project, bin, item, path, before) {
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
      // Confirm against the PRE-IMPORT snapshot: only an item that is new by
      // identity counts, so an older same-file item already in the bin can
      // never pass for the moved one.
      const confirmed = await findClipInBin(pr, bin, path, before, [0, 500]);
      if (confirmed && confirmed.preexisting !== true) {
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
 * @returns {Promise<{imported: boolean, item: object|null, raw: object|null,
 *   where: 'bin'|'moved'|'root'|'missing'|'failed', detail?: string}>}
 *   `item` is the cast ClipProjectItem; `raw` the un-cast ProjectItem
 *   handle when the recovery path had one (the insert probe wants both).
 */
async function importIntoBin(pr, project, bin, path) {
  // BEFORE-import identity snapshot (bin, root, capped tree): lets every
  // finder below prefer the freshly-imported item over an older one that
  // references the SAME file, and name the "this file was already in the
  // bin" case. See cprbSnapshotBeforeImport for the identity rule.
  const before = await cprbSnapshotBeforeImport(pr, project, bin);

  const restoreIngest = await disableIngestBestEffort(pr, project);
  let imported = false;
  let importedDirect = false;
  try {
    if (bin) {
      // PROVEN on 26.3 (owner live run 2026-07-24): 3-arg
      // importFiles(paths, suppressUI, targetBin) returns true and the item
      // lands directly in the bin -- the primary path, silent on success.
      // (Its `.length` reports 0: normal for native bindings, not a
      // signal.) A throw or a non-true return falls back to the
      // S6-C-proven 2-arg call, for builds without the 3-arg shape.
      try {
        const result3 = await project.importFiles([path], true, bin);
        if (result3 === true) {
          imported = true;
          importedDirect = true;
        } else {
          logDebug(`3-arg importFiles returned ${String(result3)} -- falling back to 2-arg`);
        }
      } catch (error) {
        logDebug(`3-arg importFiles threw (${cprbErrMsg(error)}) -- falling back to 2-arg`);
      }
    }
    if (!imported) {
      // PROVEN (S6-C): 2-arg importFiles returns true and the item is then
      // enumerable at its stored path.
      const result2 = await project.importFiles([path], true);
      if (result2 !== true) {
        return { imported: false, item: null, raw: null, where: 'failed', detail: `importFiles returned ${String(result2)}` };
      }
      imported = true;
    }
  } catch (error) {
    return { imported: false, item: null, raw: null, where: 'failed', detail: cprbErrMsg(error) };
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
  let oldInBin = null;
  if (bin) {
    const found = await findClipInBin(pr, bin, path, before);
    if (found && found.preexisting !== true) {
      if (found.preexisting === null) {
        logDebug(importedDirect
          ? 'this file is in the bin more than once and item identity is unavailable -- '
            + 'using the first match (it may be the OLDER item)'
          : 'this file was already in the bin -- using the existing item');
      }
      return { imported: true, item: found.clip, raw: found.raw, where: 'bin' };
    }
    // null, or an item identity PROVES predates this import: the fresh one
    // may have landed at the root instead -- look there before settling.
    oldInBin = found;
  }
  // ...then the project root (import landed there instead), with a move
  // attempt when we do have a bin. A NEW item there beats an old bin item.
  const atRoot = await findClipAtRoot(pr, project, path, before);
  if (atRoot && !(oldInBin && atRoot.preexisting !== false)) {
    if (atRoot.preexisting === true) {
      // Identity-decided: no new item anywhere, only one that predates this
      // import (often the user's own original clip) -- use it, but never
      // move the user's item into the results bin.
      logDebug('no new item for this file appeared -- using the one already in the project '
        + '(left where it is; its own in/out and label apply)');
      return { imported: true, item: atRoot.clip, raw: atRoot.raw, where: 'root' };
    }
    if (bin && (await moveItemIntoBin(pr, project, bin, atRoot.clip, path, before))) {
      return { imported: true, item: atRoot.clip, raw: atRoot.raw, where: 'moved' };
    }
    return { imported: true, item: atRoot.clip, raw: atRoot.raw, where: 'root' };
  }
  if (oldInBin) {
    // Identity-decided: importFiles said true, yet no NEW item for this
    // file registered anywhere within the retry window.
    logDebug('no new item for this file appeared in the bin -- using the one already there '
      + '(its own in/out and label apply)');
    return { imported: true, item: oldInBin.clip, raw: oldInBin.raw, where: 'bin' };
  }
  // importFiles said true but nothing matches the path anywhere -- the
  // S6-C "PARTIAL" shape (typically a path not visible on this machine).
  return { imported: true, item: null, raw: null, where: 'missing' };
}

/* ------------------------ step: stamp properties ---------------------- */

/**
 * Reads `key` back from a FRESH Properties fetch, so a claimed write is
 * never trusted on the say-so of the object we wrote to.
 * @returns {Promise<boolean|null>} true = value came back, false = it did
 * not, null = read-back itself unavailable (persistence unknowable).
 */
async function cprbReadBackTag(pr, item, key, expected) {
  try {
    const fresh = await pr.Properties.getProperties(item);
    if (!fresh) return null;
    if (typeof fresh.getValue === 'function') {
      try {
        if ((await fresh.getValue(key)) === expected) return true;
      } catch (_) { /* fall through to the plain read */ }
    }
    try {
      if (fresh[key] === expected) return true;
    } catch (_) { /* unreadable that way too */ }
    return false;
  } catch (_) {
    return null;
  }
}

/**
 * Write-only bridge bookkeeping on the imported item: `comfy.label`,
 * `comfy.sent_ts`, `comfy.source`. GET is PROVEN (S6-E:
 * Properties.getProperties(ClipProjectItem) returns an object). The owner's
 * 2026-07-24 live run DISPROVED the speculative `setProperty(name, value)`
 * and `set(name, value)` setter shapes (absent on 26.3 -- removed), and its
 * "tagged via direct assignment (persistence unverified)" line was exactly
 * that: unverified. This step therefore now VERIFIES by READ-BACK -- after
 * writing, getProperties() is fetched FRESH and the constant sentinel field
 * (`comfy.source`) is read back; the log states plainly whether the tag
 * actually stuck. Setter shapes, in order:
 *   1. VERIFY(spike-S6E-set): `props.createSetValueAction(name, value,
 *      Constants.PropertyType.PERSISTENT)` inside one labeled transaction
 *      -- the DOCUMENTED setter (Adobe's own properties.ts sample;
 *      research/premiere-tier2/r2 §1.6d), which no live run has probed yet.
 *      PERSISTENT is the documented survives-save/reload flag.
 *   2. direct assignment -- known to "take" in-session on 26.3; whether a
 *      fresh fetch still sees it is exactly what the read-back decides.
 * Read-back honesty note: a fresh getProperties() proves the value
 * outlived OUR write handle, not that it survives a project save/reload
 * cycle -- that stronger claim needs a reopen test, not panel code.
 * MUST fail soft: tagging never blocks an import.
 */
async function stampProperties(pr, project, item, meta) {
  try {
    if (!pr.Properties || typeof pr.Properties.getProperties !== 'function') {
      logDebug('properties: no Properties.getProperties on this build -- items left untagged');
      return;
    }
    let props = null;
    try {
      props = await pr.Properties.getProperties(item);
    } catch (error) {
      logDebug(`properties: getProperties failed (${cprbErrMsg(error)}) -- items left untagged`);
      return;
    }
    if (!props) {
      logDebug('properties: getProperties returned null -- items left untagged');
      return;
    }
    const fields = [
      ['comfy.label', String(meta.label == null ? '' : meta.label)],
      ['comfy.sent_ts', String(meta.sent_ts == null ? '' : meta.sent_ts)],
      // Constant, never empty -- the read-back sentinel.
      ['comfy.source', 'comfyui-premiere-bridge']
    ];
    const sentinelKey = fields[2][0];
    const sentinelValue = fields[2][1];
    const persistent =
      pr.Constants && pr.Constants.PropertyType ? pr.Constants.PropertyType.PERSISTENT : undefined;
    const shapes = [];
    if (typeof props.createSetValueAction === 'function' && persistent !== undefined) {
      shapes.push({
        name: 'createSetValueAction(name, value, PERSISTENT)',
        apply: async () => {
          let txnOk = false;
          project.lockedAccess(() => {
            txnOk = project.executeTransaction((compound) => {
              for (const [key, value] of fields) {
                compound.addAction(props.createSetValueAction(key, value, persistent));
              }
            }, 'Tag ComfyUI result');
          });
          return txnOk;
        }
      });
    }
    shapes.push({
      name: 'direct assignment',
      apply: async () => {
        for (const [key, value] of fields) props[key] = value;
        return true;
      }
    });
    for (const shape of shapes) {
      let applied = false;
      try {
        applied = await shape.apply();
      } catch (error) {
        logDebug(`properties: ${shape.name} threw: ${cprbErrMsg(error)}`);
        continue;
      }
      if (!applied) {
        logDebug(`properties: ${shape.name} did not take`);
        continue;
      }
      const verdict = await cprbReadBackTag(pr, item, sentinelKey, sentinelValue);
      if (verdict === true) {
        logDebug(`properties: tagged via ${shape.name} (read-back verified)`);
        return;
      }
      if (verdict === null) {
        logDebug(`properties: wrote via ${shape.name} but read-back is unavailable -- persistence UNKNOWN`);
        return;
      }
      logDebug(`properties: ${shape.name} wrote but the tag did NOT survive read-back`);
    }
    // Plain truth, per the 2026-07-24 finding: do not imply the item is
    // tagged when no write survived a fresh fetch.
    log('properties: no setter shape survived read-back -- items are UNTAGGED on this build', 'dim');
  } catch (error) {
    logDebug(`properties step failed (${cprbErrMsg(error)}) -- import unaffected`);
  }
}

/* ------------------------- step: color label -------------------------- */

/**
 * Sets the item's label color by human name; an empty/absent name means
 * SKIP, per the protocol ("" = no labeling). PROVEN live (owner,
 * 2026-07-24, 26.3): `Constants.ProjectItemColorLabel.LAVENDER` -> index 3
 * applied through `createSetColorLabelAction` and visible in the UI. The
 * live enum stays the authority; the hardcoded name map is a last resort
 * for builds without it. Fails soft; the import is never undone.
 */
async function setColorLabel(pr, project, item, colorName) {
  const name = String(colorName == null ? '' : colorName).trim().toLowerCase();
  if (!name) return;
  try {
    let index = null;
    let via = '';
    try {
      // Target Constants.ProjectItemColorLabel DIRECTLY (proven the right
      // enum on 26.3). The old /label|color/i scan of all Constants keys
      // also matched Constants.MarkerColor -- and iterated it FIRST -- so
      // shared names like "yellow"/"blue"/"green" could have resolved
      // against the WRONG color table. Never scan when the answer is known.
      const enumObj = pr.Constants && pr.Constants.ProjectItemColorLabel;
      if (enumObj && typeof enumObj === 'object') {
        for (const key of Object.keys(enumObj)) {
          if (key.toLowerCase() === name) {
            index = enumObj[key];
            via = `Constants.ProjectItemColorLabel.${key}`;
            break;
          }
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
      log('color label: no createSetColorLabelAction on this item -- skipped', 'dim');
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

/** Clip count on video track `index`, or null when unreadable (missing
 * Constants.TrackItemType, no track handle, or a throw). The READ-BACK: a
 * landed overwrite raises the target track's count. Kept from the v0.9.6
 * probe because a transaction reporting success is not proof anything
 * actually landed -- the properties step earned that rule the hard way. */
async function cprbCountClipsOnTrack(pr, sequence, index) {
  try {
    if (!pr.Constants || !pr.Constants.TrackItemType || pr.Constants.TrackItemType.CLIP === undefined) {
      return null;
    }
    const track = await sequence.getVideoTrack(index);
    if (!track || typeof track.getTrackItems !== 'function') return null;
    const items = await track.getTrackItems(pr.Constants.TrackItemType.CLIP, false);
    return items && typeof items.length === 'number' ? items.length : null;
  } catch (_) {
    return null;
  }
}

/**
 * Overwrites the imported item onto the active sequence at the playhead, on
 * the topmost existing video track. Runs only when the message says
 * `insert_at_playhead: true` (the node's widget defaults OFF).
 *
 * PROVEN on 26.3 (owner live run 2026-07-25) -- the v0.9.6 probe tried nine
 * one-variable-at-a-time shapes and this is the exact one that landed:
 *
 *     editor.createOverwriteItemAction(RAW ProjectItem, playheadTickTime,
 *                                      videoTrackIndex, 0)
 *
 * inside `lockedAccess` + `executeTransaction`. Read-back confirmed the
 * target track's clip count 0 -> 1 at the playhead.
 *
 * THE ONE THING THAT MATTERED: the item must be the **RAW `ProjectItem`**
 * straight off the bin enumeration -- passing the `ClipProjectItem.cast(...)`
 * wrapper (which every other step in this file uses happily, and which works
 * fine as an action FACTORY, e.g. `createSetColorLabelAction`) threw
 * "Invalid parameter." every time. Adobe's own sequenceEditor.ts sample
 * passes raw items too. `audioTrackIndex = 0` was never the problem; the -1
 * "touch no audio track" variant was not needed. Every other probe branch is
 * deleted -- keeping them would only re-litigate a settled question.
 *
 * Still defensive about the things the probe did NOT settle: the track count
 * has two possible readers, and with NO readable count this SKIPS rather than
 * guessing an index (an overwrite onto a wrongly-guessed track destroys
 * content). Fail-soft throughout: a failure here never touches the
 * already-successful import.
 */
async function insertAtPlayhead(pr, project, rawItem) {
  try {
    if (!rawItem) {
      log('insert: no raw ProjectItem handle recovered for this import -- skipped '
        + '(the cast ClipProjectItem is rejected by createOverwriteItemAction)', 'dim');
      return;
    }
    const sequence = await project.getActiveSequence();
    if (!sequence) {
      log('insert: no active sequence -- skipped (the clip is still in the bin)', 'dim');
      return;
    }
    const position = await sequence.getPlayerPosition();
    if (!pr.SequenceEditor || typeof pr.SequenceEditor.getEditor !== 'function') {
      log('insert: no SequenceEditor.getEditor on this build -- skipped', 'dim');
      return;
    }
    const editor = await pr.SequenceEditor.getEditor(sequence);
    if (!editor || typeof editor.createOverwriteItemAction !== 'function') {
      log('insert: no createOverwriteItemAction on the editor -- skipped', 'dim');
      return;
    }

    // Topmost EXISTING video track. Two readers, because only
    // getVideoTrackCount() is proven; with neither readable we skip rather
    // than guess (see the docblock).
    let trackCount = null;
    let countVia = '';
    try {
      if (typeof sequence.getVideoTrackCount === 'function') {
        trackCount = await sequence.getVideoTrackCount();
        countVia = 'getVideoTrackCount()';
      }
    } catch (error) {
      logDebug(`insert: getVideoTrackCount threw: ${cprbErrMsg(error)}`);
    }
    if (typeof trackCount !== 'number') {
      try {
        const tracks = sequence.videoTracks;
        if (tracks && typeof tracks.length === 'number') {
          trackCount = tracks.length;
          countVia = 'videoTracks.length';
        }
      } catch (_) { /* no second reader either */ }
    }
    if (typeof trackCount !== 'number' || !(trackCount >= 1)) {
      log('insert: could not read the video track count -- skipped rather than guess a track', 'dim');
      return;
    }
    const videoTrackIndex = trackCount - 1;

    const before = await cprbCountClipsOnTrack(pr, sequence, videoTrackIndex);
    let txnOk = false;
    try {
      project.lockedAccess(() => {
        txnOk = project.executeTransaction((compound) => {
          // Created INSIDE the lock/transaction, per the 26.3 rule.
          compound.addAction(
            editor.createOverwriteItemAction(rawItem, position, videoTrackIndex, 0)
          );
        }, 'Insert ComfyUI result at playhead');
      });
    } catch (error) {
      bad(`insert failed: ${cprbErrMsg(error)} -- the clip is still in the bin`);
      return;
    }
    if (!txnOk) {
      log('insert: the transaction returned false -- nothing added (the clip is still in the bin)', 'dim');
      return;
    }

    // Read-back: believe the timeline, not the return value.
    let after = await cprbCountClipsOnTrack(pr, sequence, videoTrackIndex);
    if (before !== null && after === before) {
      await sleep(400); // in case the edit lands a beat later
      after = await cprbCountClipsOnTrack(pr, sequence, videoTrackIndex);
    }
    if (before === null || after === null) {
      ok(`insert: placed at the playhead on V${videoTrackIndex + 1} `
        + '(clip-count read-back unavailable on this build -- check the timeline)');
      return;
    }
    if (after > before) {
      ok(`insert: placed at the playhead on V${videoTrackIndex + 1} `
        + `(track clip count ${before} -> ${after}; one Edit > Undo removes it)`);
      return;
    }
    // txnOk yet no new clip: either an exact-overlap overwrite REPLACED one
    // (count legitimately unchanged) or a silent no-op. Say so honestly.
    log(`insert: the transaction succeeded but V${videoTrackIndex + 1}'s clip count stayed `
      + `${before} -- it either replaced an exactly-overlapping clip or did nothing; `
      + 'check the timeline', 'dim');
  } catch (error) {
    log(`insert failed (${cprbErrMsg(error)}) -- import unaffected`, 'dim');
  }
}

/* --------------------------- orchestration ---------------------------- */

/**
 * The full M1 recipe for one `pr_result`: bin -> import -> stamp -> color ->
 * insert. Exactly ONE headline activity-log line per result ("Imported: ..."
 * or the specific failure); degraded steps add dim detail lines. (While the
 * insert PROBE lasts, a decisive insert result adds one more headline --
 * that is the datum the probe exists to surface.) Per-step errors are
 * contained -- a failed stamp/color/insert never undoes a successful import.
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
  // Headline names the actual FILE too whenever the user's label differs
  // (the owner's live log said "Imported: test image" for 1.m4v -- correct,
  // but unhelpful when eyeballing which file just arrived).
  const fileName = basename(path);
  ok(`Imported: ${label}${label === fileName ? '' : ` (${fileName})`}${outcome.where === 'root' ? ' (at project root)' : ''}`);

  if (!outcome.item) {
    logDebug('no item handle recovered -- skipping tag/color/insert');
    return;
  }
  await stampProperties(pr, project, outcome.item, {
    label: msg.label,
    sent_ts: msg.sent_ts
  });
  await setColorLabel(pr, project, outcome.item, msg.color_label);
  if (msg.insert_at_playhead === true) {
    // RAW ProjectItem only -- createOverwriteItemAction rejects the cast
    // ClipProjectItem wrapper (proven 2026-07-25; see insertAtPlayhead).
    await insertAtPlayhead(pr, project, outcome.raw || null);
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
