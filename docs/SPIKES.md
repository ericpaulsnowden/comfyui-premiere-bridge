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
  - [ ] **S6-A `ws://` cleartext (THE go/no-go gate).** Open
    `ws://localhost:<port>/ws` (ComfyUI's own websocket) from inside
    Premiere under the scoped `network.domains` localhost array. Research
    raised the prior AGAINST this on macOS (a live Adobe bug report shows
    cleartext `http://` fetch blocked there even under `domains:"all"`,
    while InDesign passes) — so a PASS on macOS is strong evidence, and
    the PC (Windows) should be at least as permissive. FAIL fallback:
    `wss://` self-signed or an https relay, per the roadmap.
  - [ ] **S6-B action pattern.** Create a bin inside
    `project.lockedAccess()` + `executeTransaction()` (the 26.3-enforced
    shape) and confirm ONE labeled Edit ▸ Undo step.
  - [ ] **S6-C import + find.** `importFiles([path])` then recover the new
    item via `findItemsMatchingMediaPath` — records which calling idiom
    works (docs say instance method; a static call is tried first).
  - [ ] **S6-D frame-export probe.** Enumerate the real export surface at
    the playhead (module keys, Sequence methods, EncoderManager methods) —
    M2 wires whichever call this surfaces.
  - [ ] **S6-E ground truth.** `Object.keys(require("premierepro"))` (docs
    are provably incomplete), `WorkAreaUtils` presence, and whether
    `Properties.getProperties()` accepts a `ClipProjectItem` (if yes: our
    cleanest bridge-bookkeeping store).
  - The old S6(b) question — `EncoderManager.exportSequence(...,
    exportFull=false)` honoring sequence in/out — moves to M2, informed by
    S6-D's probe.

## LIVE RESULTS

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
