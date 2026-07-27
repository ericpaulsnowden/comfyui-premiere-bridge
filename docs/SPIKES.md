# SPIKES.md — live verifications gating Premiere-facing claims

Same discipline as comfyui-photoshop-bridge: anything that can only be
proven against real Adobe software stays listed here, unchecked, until a
live session proves it; code touching an unproven surface carries a
`VERIFY(spike-SN)` comment. Checking a box requires recording WHO/WHEN/HOW
under LIVE RESULTS.

These spikes need **Adobe Premiere Pro** (Eric's PC; version must be noted —
UXP spikes need ≥ 25.6). None of them block Tier 1 development; S1/S2 gate
calling the XML output "verified" in the README.

- [x] **S1 — golden XML import.** `File > Import` a
  `premiere_timelines/<name>/<name>.xml` produced by Save Premiere Timeline
  on the SAME machine as the media paths. Expect: a new sequence appears,
  every clip online (no relink dialog), cuts at the expected frames,
  sequence timebase per PROTOCOL §4.2. **PASSED — Eric, 2026-07-19** (see
  LIVE RESULTS). This retires the "import verification pending" caveat the
  README carried on Save Premiere Timeline.
- [x] **S2 — UNC pathurl.** Same import where media lives on
  `\\nas\share\…`. Records which pathurl form Premiere links
  (`file://nas/share/…` vs `file://localhost/\\nas\…` vs mapped drive).
  **PASSED — Eric, 2026-07-20** with the v0.5.0 Link-in-place media mode
  (see LIVE RESULTS).
- [ ] **S3 — ProRes materialization.** Verify ComfyUI's VIDEO `save_to`
  (PyAV) can encode ProRes (`prores_ks`) on the PC install; if yes, add a
  `codec` widget to Save Premiere Timeline (PROTOCOL §3.3 follow-up).
- [ ] **S4 — OTIO import.** Whether Eric's Premiere (stable or Beta) offers
  OpenTimelineIO import, and whether our `.otio` (when `write_otio`) links
  media. Research says Beta-only as of mid-2026.
- [ ] **S5 — linked audio.** After S1 passes: hand-extend a golden XML with
  audio clipitems linked to the same files; confirm Premiere links video+
  audio as one clip. Gates PROTOCOL §4.1's audio follow-up.
- [ ] **S6 — Tier-2 M0 spike round** (supersedes the old single-item S6;
  roadmap: `research/roadmap-premiere-tier2.md` M0). The spike panel SHIPS
  in this repo — `premiere_plugin/` (v0.8.0), dev-installed via UXP
  Developer Tool — with one button per spike and a copyable log:
  - [x] **S6-A `ws://` cleartext (THE go/no-go gate).** Open
    `ws://localhost:<port>/ws` (ComfyUI's own websocket) from inside
    Premiere under the scoped `network.domains` localhost array. Research
    raised the prior AGAINST this on macOS (a live Adobe bug report shows
    cleartext `http://` fetch blocked there even under `domains:"all"`,
    while InDesign passes) — so a PASS on macOS is strong evidence, and
    the PC (Windows) should be at least as permissive. FAIL fallback:
    `wss://` self-signed or an https relay, per the roadmap.
  - [x] **S6-B action pattern.** Create a bin inside
    `project.lockedAccess()` + `executeTransaction()` (the 26.3-enforced
    shape) and confirm ONE labeled Edit ▸ Undo step.
  - [x] **S6-C import + find.** `importFiles([path])` then recover the new
    item via `findItemsMatchingMediaPath` — records which calling idiom
    works (docs say instance method; a static call is tried first).
  - [x] **S6-D frame-export probe.** Enumerate the real export surface at
    the playhead (module keys, Sequence methods, EncoderManager methods) —
    M2 wires whichever call this surfaces.
  - [x] **S6-E ground truth.** `Object.keys(require("premierepro"))` (docs
    are provably incomplete), `WorkAreaUtils` presence, and whether
    `Properties.getProperties()` accepts a `ClipProjectItem` (if yes: our
    cleanest bridge-bookkeeping store).
  - The old S6(b) question — `EncoderManager.exportSequence(...,
    exportFull=false)` honoring sequence in/out — moves to M2, informed by
    S6-D's probe.
- [x] **S7 — frame export (THE M2 gate).** S6-D enumerated
  `EncoderManager`'s method NAMES and proved `Sequence` has no per-frame
  export; S7 had to produce an actual still frame from the playhead. The M1
  panel (v0.9.1–v0.9.7) carried an **"S7: frame-export probe"** button that
  logged `Exporter`'s own key surface (never probed), export-shaped
  `Constants`, the `exportSequence`/`encodeFile`/`encodeProjectItem` arities,
  and then ATTEMPTED minimal `exportSequence` calls. The owner ran it
  2026-07-24 and it PASSED (LIVE RESULTS below).
  **The button was REMOVED in v0.10.0 when M2 shipped** — deliberately, not
  by tidying: its attempt ladder called `manager.exportSequence(...)` and
  broke on the first shape that did not throw, so on a build where any of
  them is valid a misclick beside the new SEND TO COMFYUI buttons would have
  started a whole-sequence render or an AME queue (`ExportType.QUEUE_TO_AME`
  was enumerated on this very build), and it never took export_source.js's
  busy flag so it could interleave with a real export. Its READ-ONLY
  successor is `cprbLogExporterProbe` (typeof + arity of
  `exportSequenceFrame`), which every "Frame → ComfyUI" click logs for free,
  before any read that can fail.
  - [x] **S7-b — `exportSequenceFrame`'s own SIGNATURE** — PASSED LIVE 2026-07-26 (the "NEXT for M2"
    item S7's LIVE RESULTS left open). **Originally resolved from documentation
    only, not yet from a live run** (at write time this bullet said it "stays
    unchecked, per this file's rule, and the M2 build is what will check it")
    — **the M2 build (v0.10.0) then did check it live**, and the LIVE RESULTS
    entry below ("S7-b — FRAME EXPORT PASSED", owner, 2026-07-26) is that
    confirmation, which is why the box above is now checked. What the research established
    (Adobe's `adobe/premierepro-types` `ExporterStatic` declaration, the
    official 26.3 `Exporter` class page recommitted 2026-06-11 for 26.3, and
    three real shipping callers — Adobe's own `premiere-api` sample panel,
    `mikechambers/adb-mcp`, and `thegreeneyl`'s face-detect plugin, which
    exports stills to a temp dir for downstream analysis, i.e. our exact use
    case):
    - `exportSequenceFrame(sequence, time, filename, filepath, width, height)
      -> Promise<boolean>`, `Since: 25.6`. **The documented arity of 6 is NOT
      observable at runtime** — see the S7-b live result below: the binding
      reports `.length === 0` while exporting correctly. Only
      `typeof === 'function'` is a usable check.
    - `filename` is a **BASENAME ONLY**, `filepath` a **DIRECTORY** — the
      official parameter table's `filename` example (a full path) is WRONG:
      a full path there makes Premiere write to a bad combined path and
      silently produce NOTHING. All three real callers split dir/base.
    - `width`/`height` from `await sequence.getFrameSize()` → `RectF
      {width, height}` (floats, round them).
    - **No `lockedAccess`/`executeTransaction`** — it is a read; all three
      callers call it bare.
    - **The boolean lies in both directions**: it can return before the file
      is on disk, and under rapid back-to-back exports it can return `true`
      and never write at all. Unique filename per export + retry, and the
      existence check on the ComfyUI side. Also: no colons in the name
      (illegal on Windows ⇒ returns `false`), and the double-extension
      defect (`abcd.jpg` → `abcd.jpg.jpg`) was fixed in 26.2.2 — one patch
      before the owner's 26.3.0 — so the server resolves plain-vs-doubled
      rather than trusting either.
    - Decision recorded so it is not re-litigated: `premiere_plugin/
      manifest.json` stays `"localFileSystem": "request"`. Both reference
      callers use `"fullAccess"`, but only to verify their own writes from
      the plugin; the native exporter takes a plain string path and needs no
      UXP file token, and PROTOCOL §11.4 verifies server-side where Python
      has no sandbox. No user-visible permission escalation for a check the
      backend does for free.
    - Full contract now in **PROTOCOL.md §11.7** (frame export AND the
      clip-read chain), with §11.1–§11.6 for the wire, node and panel halves.
    - **CHECKS THIS BOX:** the owner's first "Frame → ComfyUI" click
      producing a real PNG in `<ComfyUI input>/premiere_frames/` that
      `PremiereFrameSource` then decodes. Anything else — a `false` return, a
      `true` with no file, a doubled extension on 26.3.0 — is the spike data;
      record it here verbatim.
    - **v0.10.0 makes each of those outcomes self-reporting.** The panel
      cannot stat its own output (`localFileSystem: "request"` refuses an
      arbitrary path, so its poll ladder answers "cannot tell" and never
      fires), so the SERVER stats the path as it relays `export_ready` and
      adds `path_exists` / `resolved_path` (PROTOCOL §11.2). A `true` return
      with no file therefore surfaces as a WARNING toast within a second of
      the click instead of as a confusing failure at Run — and a doubled
      extension resolves silently and correctly. `getFrameSize()` is the one
      remaining unproven read in the frame path; when both it and
      `getSettings()` fail the panel now dumps `Sequence`'s own and prototype
      property names, so the real method name arrives in the same log.
  - [x] **S7-c — clip read (`Clip → ComfyUI`).** — PASSED LIVE 2026-07-26 The whole chain is
    documented and executed verbatim in Adobe's own sample
    (`sequence.getSelection()` → `.getTrackItems()` → `item.getProjectItem()`
    RAW → `pr.ClipProjectItem.cast(raw)` → `.getMediaFilePath()`, plus
    `getInPoint()`/`getOutPoint()` for SOURCE in/out), so no probe gates the
    BUILD — but four things the 26.3 docs are silent on can only be answered
    live, and the panel logs each so one owner run settles them:
    - whether `getSelection()` sees the Timeline selection at all once focus
      moves into the UXP panel, and whether `getIsSelected()` (no Adobe
      sample exercises it) answers on real track items. v0.10.0 makes the
      empty-selection path SELF-DIAGNOSING rather than merely instructive: it
      logs what each route saw (items returned by `getSelection()`; tracks
      walked, clips examined and how many could answer `getIsSelected()`) and
      asks for the log back if a clip IS selected — so one click settles this
      instead of dead-ending on "nothing is selected";
    - whether a linked A/V click returns one track item or two, and in what
      order (there is no static `.cast()` on either track-item class; the
      video one is duck-typed on `createAddVideoTransitionAction`);
    - whether `getInPoint()` is file-relative or absolute source timecode on
      media whose Media Start is NOT `00:00:00:00` (R3D/MXF/broadcast).
      Adobe's own sample COMMENT says file-relative — exactly what PyAV
      wants — but a comment is not a spec, so test one such clip before
      trusting the trim;
    - whether a SUBCLIP's in/out is relative to the subclip or the original
      file, while `getMediaFilePath()` still returns the full file;
    - whether `getOutPoint()` is INCLUSIVE or EXCLUSIVE — undocumented, and
      the backend commits to EXCLUSIVE so `Get Shot`'s
      `frame_count = out - in` comes out right. v0.10.0 logs `in`, `out`,
      `frame_count` and `source_fps` at INFO on EVERY clip run, so one
      comparison against Premiere's own clip-duration readout settles it. If
      it turns out inclusive the fix is confined to `build_clip_shot`'s two
      `round(seconds * fps)` lines plus a §11.7 amendment.
    - whether the Timeline selection survives focus moving into the UXP
      panel (no selection-changed event exists on 26.3, so the panel reads
      selection at click time and falls back to a `getIsSelected()` sweep —
      saying WHICH route it used, and reporting what BOTH routes saw when
      neither finds anything. There is deliberately no playhead or
      project-panel fallback: both would guess at what the user meant).
    Note the mirror-image cast lesson: the 2026-07-25 insert-at-playhead
    result proved a cast wrapper is REJECTED as an argument, while here the
    cast is MANDATORY because `ProjectItem` has no `getMediaFilePath`. Keep
    both handles. **CHECKS THIS BOX:** a clip sent to ComfyUI whose decoded
    range matches what the Timeline shows, plus the invariant
    `(out-in) ≈ timeline duration × speed` holding in the panel log.

## LIVE RESULTS

- **S7-b — FRAME EXPORT PASSED (owner, 2026-07-26, panel v0.10.0, Premiere
  26.3.0 / win32).** The whole M2 round trip closed: four stills exported, then
  a ComfyUI-inverted result imported back into the bin. Every open question
  answered in one session:
  - **The A1 call shape is CORRECT and is the only one needed.**
    `exportSequenceFrame(sequence, position, BASENAME, DIRECTORY, w, h)`
    returned `true` on attempt 1/4 every single time; shape A2 (trailing
    separator) was never reached and can be considered dead weight.
  - **`getFrameSize()` WORKS** — reported `704x704` for the owner's sequence.
    That retires the last unproven read in the frame path; the `getSettings()`
    fallback and the prototype-dump diagnostic were never needed.
  - **`exportSequenceFrame.length === 0`, NOT 6.** The binding exports
    perfectly while reporting no arity — native UXP bindings do not publish a
    parameter count. The panel's own probe line used to read
    `arity=0 (expect function / 6)`, which looked like a failure during a
    completely successful run; corrected in v0.10.2 to say only `typeof`
    matters. **Never gate anything on `Function.length` for these bindings.**
  - **Both panel-side file probes refused, exactly as predicted**, which
    VALIDATES the decision to verify server-side rather than escalate the
    manifest permission: `getEntryWithUrl` → "Could not find an entry of
    'file:///E:%5C...'" and `fs.lstatSync` → "Unimplemented method:
    lstatSync". Under `localFileSystem: "request"` the panel simply cannot see
    its own output, so `cprbLocateExport` returns `unknown` on every real
    install and the retry ladder below it is unreachable. §11.2's server-side
    `path_exists` / `resolved_path` is the check that actually runs.
  - The frames directory was a LOCAL disk (`E:\ComfyUI Working\Input\
    premiere_frames\`) and Premiere wrote to it without complaint. The
    `CPRB_FRAMES_DIR` override was therefore not exercised; the NAS-share case
    remains untested but the escape hatch is in place.
  - No doubled extension on 26.3.0 (the 26.2.2 fix holds); the guard stays.

- **S7-c — CLIP READ PASSED (owner, 2026-07-26, same session).** Every one of
  the four undocumented behaviours this box was waiting on is now answered:
  - **Timeline selection SURVIVES focus moving into the UXP panel.**
    `getSelection()` returned a live selection at click time — the
    `getIsSelected()` sweep fallback was never needed.
  - **A linked A/V click returns TWO track items**, and the
    `createAddVideoTransitionAction` duck-type correctly picked the video one
    ("one video clip selected (via timeline selection)").
  - **`getInPoint()`/`getOutPoint()` are SOURCE-relative**, as Adobe's sample
    comment claimed: a full clip read `0.000..20.042s`, and the invariant held
    exactly — `span 20.042s, timeline duration 20.042s, speed 1,
    reversed=false`.
  - **`getOutPoint()` is EXCLUSIVE — CONFIRMED by arithmetic.** The node
    emitted `in=0 out=481 frame_count=481 at 24fps`; Premiere's own duration
    readout for that clip was **20 s + 1 frame = 20x24+1 = 481 frames**. Exact
    match, so `frame_count = out - in` is right and no off-by-one correction is
    needed. This was the one thing the design had committed to without proof.
  - **A UNC/NAS source path round-tripped fine**
    (`\\ugreen-nas...\ComfyUI Working\Input\...mp4`) — the clip half needs
    no local media.
  - Still untested, and now the only clip unknowns worth naming: a SUBCLIP,
    and media whose Media Start is not `00:00:00:00` (R3D/MXF/broadcast).

- **INSERT-AT-PLAYHEAD SOLVED (owner, 2026-07-25, v0.9.6 probe, 26.3).** The
  nine-shape probe answered it in ONE click. The winning call, now the only
  branch in the code:
  ```js
  editor.createOverwriteItemAction(RAW_ProjectItem, playheadTickTime, videoTrackIndex, 0)
  ```
  inside `lockedAccess` + `executeTransaction`; read-back confirmed the target
  track's clip count 0 -> 1 at the playhead. **The one thing that mattered was
  the ITEM HANDLE: the raw `ProjectItem` off the bin enumeration works, the
  `ClipProjectItem.cast(...)` wrapper throws "Invalid parameter."** — the two
  cast shapes failed first, both raw shapes would have succeeded. `audio 0`
  was never the problem and the `-1` "touch no audio track" variant was not
  needed. Adobe's own sequenceEditor.ts passes raw items, which is why that
  hypothesis was ranked second and the probe tried it third.
  **GENERAL LESSON: a cast wrapper is fine as an action FACTORY (the same
  cast item's `createSetColorLabelAction` works live) but is rejected when
  passed as an ARGUMENT into another object's factory. Pass raw items.**
  - Same run also recorded, verbatim: editor prototype =
    ["constructor","createAddItemAction","createAddItemsAction","createCloneTrackItemAction","createInsertProjectItemAction","createOverwriteItemAction","createRemoveItemsAction","insertMogrtFromLibrary","insertMogrtFromPath"];
    `createOverwriteItemAction.length=4`, `createInsertProjectItemAction.length=5`;
    TickTime statics = ["TIME_INVALID","TIME_MAX","TIME_MIN","TIME_ONE_HOUR","TIME_ONE_MINUTE","TIME_ONE_SECOND","TIME_ZERO","createWithFrameAndFrameRate","createWithSeconds","createWithTicks",...]
    (**`createWithSeconds` / `createWithFrameAndFrameRate` exist — M2 needs
    them to turn a frame or a time into a TickTime**); a TickTime instance
    carries `.ticks` (STRING), `.seconds` (number), `.ticksNumber`, and a
    prototype of [add,alignToFrame,alignToNearestFrame,divide,equals,multiply,seconds,subtract,ticks,ticksNumber].
  - **Two more VERIFY flags retired by the same log:** `Properties` SET
    genuinely persists — "tagged via createSetValueAction(name, value,
    PERSISTENT) (read-back verified)" — so bridge bookkeeping is real, not
    aspirational; and the ingest guard's documented shape works — "readable
    via ProjectSettings.getIngestSettings (documented shape)".
  - **UNC/NAS media round-trips**: a Tailscale NAS path with SPACES
    (`\\ugreen-nas.tail0ecd21.ts.net\ComfyUI Repository\ComfyUI Working\Input\...`)
    imported and linked in place with no quoting problems.

- **S7 — PASSED / M2 UNBLOCKED (owner, 2026-07-24, panel v0.9.3, Premiere
  26.3, win32).** The probe found the frame-export call that S6-D could not:
  **`pr.Exporter.exportSequenceFrame` EXISTS** — enumerated own keys were
  `["arguments","caller","exportSequenceFrame","length","name","prototype"]`
  (a STATIC on `Exporter`; `Exporter.prototype` carries only `constructor`).
  This is what M2's "Frame -> ComfyUI" wires; `EncoderManager` is NOT the
  frame path.
  - Supporting ground truth from the same run: `Constants.ExportType =
    {"QUEUE_TO_AME":"com.adobe.mezzanine.export.queue.ame",
    "QUEUE_TO_APP":"com.adobe.mezzanine.export.queue.batch",
    "IMMEDIATELY":"com.adobe.mezzanine.export.immediately"}`;
    `Constants.EncoderEvent` has RENDER_COMPLETE/ERROR/CANCEL/QUEUE/PROGRESS
    (the progress surface M2's "Clip -> ComfyUI" needs);
    `Constants.AAFExportAudioFormat = {"AIFF":0,"WAV":1}`. Playhead read
    again fine (`ticks=39065544000000`).
  - `EncoderManager` arities all report `.length=0` (native bindings —
    expected, not a signal). `exportSequence(sequence)` threw **"Not Enough
    Parameters"**; `exportSequence(sequence, outPath)` RETURNED `false` (a
    relative outPath, since `os.tmpdir()` was unavailable in the panel).
    **Lesson recorded: the native bindings distinguish arity errors ("Not
    Enough Parameters") from type/value errors ("Invalid parameter") — that
    distinction is a diagnostic tool, use it.**
  - NEXT for M2: probe `exportSequenceFrame`'s own signature (arity + what
    it wants for time/path/preset) before writing any M2 code against it.

- **S6 VERIFY flags RETIRED by the same run (M1 live, v0.9.1-v0.9.3).** The
  import half of M1 ran ~6 times on real media (a PNG and `.m4v`/`.mp4`
  videos), all importing successfully into a user-named bin:
  - **3-arg `importFiles(paths, suppressUI, targetBin)` WORKS on 26.3** —
    "imported directly into bin (3-arg importFiles; .length=0)" on every
    run. Promoted from probe to primary path (2-arg kept as fallback). This
    also means the bin-enumeration recovery dance is a fallback, not the
    normal path.
  - **`FolderItem` exposes `.name` as a PROPERTY** — "FolderItem name reads
    via the .name property". `getName()` kept as fallback only.
  - **`Constants.ProjectItemColorLabel` EXISTS and is the authority** —
    "color label: lavender (index 3, via
    Constants.ProjectItemColorLabel.LAVENDER)"; also enumerated
    `["MarkerColor","ProjectItemColorLabel"]`. The owner confirmed the
    FEATURE works ("This works"). The hardcoded name->index map is now
    last-resort only.
  - **`IngestSettings` has NO reachable getter in any probed shape** — "no
    IngestSettings getter matched -- importing without it" on every run. The
    guard is reduced to something quiet and honest rather than four
    speculative probes per import. Ingest could not be read on 26.3;
    acceptable because M1 imports files ComfyUI already wrote.
  - **Properties SET is still NOT proven** — "tagged via direct assignment
    (persistence unverified)" means both real setter shapes were absent and
    the code fell through to assigning onto the object `getProperties()`
    returned, which probably does not persist. A read-back verification now
    decides honestly instead of implying success. `getProperties()` itself
    (GET) remains proven (S6-E).
  - **`uxp.versions.*` and panel `localStorage` work on Premiere UXP** (the
    log carries `uxp uxp-9.3.0-local` and the server address persisted) —
    those two VERIFY flags retired.

- **STILL FAILING after the M1 round: insert-at-playhead.** With
  `insert_at_playhead` on, `editor.createOverwriteItemAction(item, position,
  videoTrackIndex, audioTrackIndex)` threw **"Invalid parameter."** for BOTH
  attempted video track indices (3, then 2 — a 3-video-track sequence), on
  every run, for a still AND a video; nothing landed on the timeline. Per
  the arity-vs-type lesson above, the call received the right NUMBER of
  arguments but at least one has the wrong TYPE/value — the playhead+insert
  COMBINATION was always INFERRED, never shown together in an Adobe sample
  (`r2-project-media-commands.md` §3.1 flags exactly this). v0.9.4 replaces
  the step with an S7-style PROBE: it enumerates the editor's real method
  surface + arities, logs the exact type of every argument it is about to
  pass, then tries parameter shapes in order (playhead vs `TickTime`
  statics, topmost vs index 0, cast vs raw project item,
  `createInsertProjectItemAction`) logging each throw verbatim. The step
  stays gated off by default, so shipping a probing version is safe.

- **S6 spike round — COMPLETE. Run by the owner on the WINDOWS PC (panel
  v0.8.2→v0.8.3, host premierepro 26.3.0, uxp 9.3.0, `os.platform()` =
  `win32`); A/B/C/D/E all answered.** Machine confirmed by v0.8.3's platform
  log: `win32` — so the whole round, including the S6-A ws:// PASS, is on the
  PC (the same machine ComfyUI runs on = the primary deployment; the macOS
  cleartext question is now moot for M1 and only matters for a future
  cross-machine mode). Every Tier-2 unknown M1 depends on is proven.
  - **S6-A `ws://` — PASS. Tier 2 is GO.** `ws://localhost:8188/ws` opened
    from inside Premiere under the scoped localhost `network.domains` array,
    received ComfyUI's own status message (full round trip:
    `{"type": "status", ...,"sid": "cprb-spike"}`), closed clean (1000).
    Cleartext ws to localhost is permitted on the OS this ran on. (If this
    was the PC, the macOS question stays open but low-stakes — same-machine
    PC is the primary deployment; cross-machine comes later, as with cpsb.)
  - **S6-B — PASS (second confirmation; first was 2026-07-22).**
    `project.lockedAccess()` + `executeTransaction()` created the bin.
  - **S6-C — PASS-VIA-ENUMERATION (v0.8.3, 2026-07-23, win32).** The
    conclusive answer M1 needs: `importFiles(["C:\Users\eric\Documents\
    ComfyUI\output\krea2_identity_edit_01262_.png"])` returned `true`, and the
    file was then present in the project by ENUMERATION at its exact stored
    path (`getMediaFilePath()` == the input). So **the ComfyUI-output →
    Premiere import round trip WORKS**, which is the whole point.
    `findItemsMatchingMediaPath` (instance idiom — the static one is not a
    function on 26.3) still returned **0** across all delays (0/750/1500/
    3000ms) and both separator variants, even for the clean, quote-stripped,
    same-machine path. Best theory (untested, and not worth chasing): it is
    genuinely an INSTANCE method scoped to the clip's own bin, and we called
    it on `clips[0]` — a pre-existing clip in a different bin than the fresh
    import — so the search scope never included the new item. Also observed:
    of 19 enumerated ClipProjectItems, **11 had EMPTY `getMediaFilePath()`**
    (sequences / non-media / offline items in other bins) — irrelevant to M1,
    which enumerates only the bin it creates. **DECISION: M1 does NOT use
    findItemsMatchingMediaPath. It creates its own bin (S6-B), imports into a
    known context, enumerates that bin's children (proven here), and tags them
    via `Properties` (S6-E). find-by-path can't block M1.** Quote-stripping
    (Explorer "Copy as path" wraps in `"`) confirmed working.
  - **S6-D — PROBED.** Module export/encode keys: `AAFExportOptions`,
    `EncoderManager`, `Exporter`. `Sequence` has NO per-frame export method
    (only `getFrameSize`) — the CEP-era `exportFramePNG` does not exist here.
    `EncoderManager` methods: `addEventListener, constructor, dispatchEvent,
    encodeFile, encodeProjectItem, exportSequence, isAMEInstalled,
    launchEncoder, removeEventListener, setEmbeddedXMPEnabled,
    setSidecarXMPEnabled, startBatchEncode, subscribeToEvent`. Playhead read
    works (`getPlayerPosition().ticks`). **M2 implication:** sequence frames
    go through `EncoderManager.exportSequence` (range export; also probe
    `Exporter`'s own keys next), while SOURCE-clip frames need no Premiere
    export at all — the backend already gets `getMediaFilePath()` and owns
    PyAV frame extraction (cprb `frame_extract.py`).
  - **S6-E — PROBED.** `premierepro` module keys (70, verbatim):
    `AAFExportOptions, Action, AddTransitionOptions, AppPreference,
    Application, AudioClipTrackItem, AudioComponentChain,
    AudioFilterComponent, AudioFilterFactory, AudioTrack, C2PAService,
    CaptionTrack, ClipProjectItem, CloseProjectOptions, Color, Component,
    ComponentFactory, CompoundAction, Constants, EncoderManager,
    EventManager, Exporter, FolderItem, FootageInterpretation, FrameRate,
    Guid, IngestSettings, Keyframe, Marker, Markers, Media, Metadata,
    ObjectMaskUtils, OpenProjectOptions, OperationCompleteEvent,
    PRProduction, PointF, PointKeyframe, Project, ProjectClosedEvent,
    ProjectConverter, ProjectEvent, ProjectItem, ProjectItemSelection,
    ProjectSettings, ProjectUtils, Properties, RectF, ScratchDiskSettings,
    Sequence, SequenceEditor, SequenceEvent, SequenceSettings, SequenceUtils,
    SnapEvent, SourceMonitor, TextSegments, TickTime, TrackItemSelection,
    Transcript, TransitionFactory, UniqueSerializeable, Utils,
    VideoClipTrackItem, VideoComponentChain, VideoFilterComponent,
    VideoFilterFactory, VideoTrack, VideoTransition, eventRoot`.
    `WorkAreaUtils` is **NOT present** (exists in Adobe sample code, not in
    26.3's module — never rely on it). **`Properties.getProperties(
    ClipProjectItem)` WORKS** (object returned) — the bridge-bookkeeping
    store is confirmed (M1 tags its own imported items with it).
  - **Panel layout saga resolved:** v0.8.0/v0.8.1 unusable (no scroll, raw
    controls double-bordered); v0.8.2 mirrored the Photoshop plugin's proven
    shape (html/body 100% + 100%-height scrolling root + capped scrolling
    log) — owner checked `t2-panel-loads` and copy/pasted the full log,
    which is itself the proof. Layout diag: `panel 260x300, sizing: css,
    resize signal: ResizeObserver`.


- **S1 — PASSED (Eric, 2026-07-19).** A `Save Premiere Timeline` `.xml`
  imported into Premiere on the PC (Premiere 26.0): the sequence appeared
  with clips at the expected cuts and media online, no relink dialog. The
  ComfyUI→XML→Premiere round trip is real. (Verified alongside
  `Load Premiere Timeline` parsing a real Premiere FCP7-XML export back
  into a correct shot list — the reverse direction — same session.)
- **S2 — PASSED (Eric, 2026-07-20).** The 2026-07-19 first pass surfaced a
  design change (media was COPIED into `media/`; Eric wanted LINK-vs-COLLECT
  — shipped v0.5.0 as the media mode widget). Retested with **Link in
  place**: Premiere imported the XML with NAS/UNC media linked online.
  Recorded on the test checklist 2026-07-20 ("SPIKE S2: import links media
  that lives on the NAS (UNC path)"); this file's entry updated 2026-07-21.
