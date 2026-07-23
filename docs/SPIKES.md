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
  - [ ] **S6-C import + find.** `importFiles([path])` then recover the new
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

## LIVE RESULTS

- **S6 spike round — RUN BY THE OWNER 2026-07-23 (panel v0.8.2, host
  premierepro 26.3.0, uxp 9.3.0; A/B/D/E answered, C partial).** Machine not
  identified in the log — the `Z:\` media paths suggest the Windows PC, but
  Premiere+UDT were first installed on the Mac; v0.8.3's panel logs
  `os.platform()` at boot and in Spike C, so the next paste settles it.
  - **S6-A `ws://` — PASS. Tier 2 is GO.** `ws://localhost:8188/ws` opened
    from inside Premiere under the scoped localhost `network.domains` array,
    received ComfyUI's own status message (full round trip:
    `{"type": "status", ...,"sid": "cprb-spike"}`), closed clean (1000).
    Cleartext ws to localhost is permitted on the OS this ran on. (If this
    was the PC, the macOS question stays open but low-stakes — same-machine
    PC is the primary deployment; cross-machine comes later, as with cpsb.)
  - **S6-B — PASS (second confirmation; first was 2026-07-22).**
    `project.lockedAccess()` + `executeTransaction()` created the bin.
  - **S6-C — PARTIAL.** `importFiles([path])` returned `true` every time.
    The STATIC `ClipProjectItem.findItemsMatchingMediaPath` is **not a
    function** on 26.3 (docs question answered: INSTANCE method only). The
    instance call ran but matched **0 items** in all three attempts — two of
    which had Explorer "Copy as path" QUOTES baked into the path, one clean.
    Unknowns: import may be async (find ran immediately), stored path form
    may differ (separators/casing), or the path was invalid on the machine
    that ran it (`Z:\...` on a Mac would no-op). **v0.8.3 refines the spike:**
    strips wrapping quotes, logs `os.platform()`, enumerates every clip's
    `getMediaFilePath()` verbatim after import (the ground-truth line),
    retries find at 0/750/1500/3000ms with a forward-slash variant, and
    reports PASS-VIA-ENUMERATION when the import is visible but find-by-path
    can't match — in which case M1 simply enumerates its own bin and
    find-by-path is not required.
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
