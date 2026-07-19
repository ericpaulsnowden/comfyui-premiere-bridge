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
- [ ] **S2 — UNC pathurl.** Same import where media lives on
  `\\nas\share\…`. Records which pathurl form Premiere links
  (`file://nas/share/…` vs `file://localhost/\\nas\…` vs mapped drive).
  PROTOCOL §4.3 marks the current emission UNCONFIRMED.
- [ ] **S3 — ProRes materialization.** Verify ComfyUI's VIDEO `save_to`
  (PyAV) can encode ProRes (`prores_ks`) on the PC install; if yes, add a
  `codec` widget to Save Premiere Timeline (PROTOCOL §3.3 follow-up).
- [ ] **S4 — OTIO import.** Whether Eric's Premiere (stable or Beta) offers
  OpenTimelineIO import, and whether our `.otio` (when `write_otio`) links
  media. Research says Beta-only as of mid-2026.
- [ ] **S5 — linked audio.** After S1 passes: hand-extend a golden XML with
  audio clipitems linked to the same files; confirm Premiere links video+
  audio as one clip. Gates PROTOCOL §4.1's audio follow-up.
- [ ] **S6 — UXP panel gate (Tier 2).** In Premiere ≥ 25.6 with UXP
  Developer Tool: (a) a manifest-v5 panel with
  `network.domains: ["ws://localhost:8188", "http://localhost:8188"]`
  opens a plain-`ws://` WebSocket to a local ComfyUI (the exact question
  the Photoshop plugin answered YES to on the same UXP runtime); (b)
  `EncoderManager.exportSequence(..., exportFull=false)` honors sequence
  in/out vs needing `encodeFile(inPoint, outPoint)`.

## LIVE RESULTS

- **S1 — PASSED (Eric, 2026-07-19).** A `Save Premiere Timeline` `.xml`
  imported into Premiere on the PC (Premiere 26.0): the sequence appeared
  with clips at the expected cuts and media online, no relink dialog. The
  ComfyUI→XML→Premiere round trip is real. (Verified alongside
  `Load Premiere Timeline` parsing a real Premiere FCP7-XML export back
  into a correct shot list — the reverse direction — same session.)
- **S2 — still open, with a design change (Eric, 2026-07-19).** Pointing at
  NAS media worked but the node COPIED it into `media/`; Eric wants a
  LINK-vs-COLLECT choice. Being added (PROTOCOL §3.3 media mode); the UNC
  pathurl form (§4.3) is retested once media is LINKED, not collected.
