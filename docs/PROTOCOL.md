# PROTOCOL.md — the binding contract for comfyui-premiere-bridge

This document is BINDING, in the comfyui-photoshop-bridge sense: the backend
(`cprb/`), the frontend (`web/`), the on-disk outputs, and (later) the UXP
panel must all match what is written here. Any interface change amends this
file FIRST, in the same commit as the code. Cite sections in code comments
as `PROTOCOL.md §N`.

Contents: §1 scope & tiers · §2 output conventions · §3 Save Premiere
Timeline · §4 emitted FCP7 XML (xmeml) · §5 emitted EDL · §6 Load Premiere
Timeline & Get Premiere Segment · §7 routes & frontend · §8 versioning & stability ·
§9 spikes · §10 Tier 2 plugin websocket (M1: ComfyUI → Premiere) ·
§11 Tier 2 M2: Premiere → ComfyUI.
(§10.6 adds the frontend's own `cprb.send_result` toast event.)

---

## §1 Scope & tiers

Per the product ethos (existing nodes first; ComfyUI-only must work; the
Adobe-side plugin is a better version, never the only version):

- **Tier 1 (this document, shipping now):** pure ComfyUI nodes + file
  interchange. Media in via existing loaders (core LoadVideo, VHS); media
  out via existing savers; cprb adds ONLY what the ecosystem lacks — writing
  a Premiere-importable timeline, and reading a Premiere-exported one.
  The user's gestures are Premiere's own `File > Import` / `File > Export`.
- **Tier 2 (§10, shipping with M1):** a Premiere UXP panel (websocket
  client of ComfyUI's server, sibling of the Photoshop plugin). Was gated
  on the SPIKES.md live-Premiere spikes; the S6 round (2026-07-23, owner's
  PC) proved every unknown M1 depends on. Nothing in Tier 1 may depend
  on it.

## §2 Output conventions

Everything Save Premiere Timeline writes lands under ComfyUI's normal
output tree BY DEFAULT (§3.2's `output_dir` widget can redirect the base to
a folder of the user's choosing; see that section):

```
<comfy output>/premiere_timelines/<sanitized sequence_name>/
  <sanitized sequence_name>.xml     # §4 — always written
  <sanitized sequence_name>.edl     # §5 — when write_edl
  <sanitized sequence_name>.otio    # OTIO JSON — when write_otio AND the
                                    # optional `opentimelineio` import works
  media/
    001_<clipname>.mp4              # §3.3 — materialized VIDEO inputs
```

- `sanitized` per `cprb.context.sanitize_name` (Windows-safe; empty →
  `timeline`).
- Re-running with the same `sequence_name` OVERWRITES the directory's files
  in place (deterministic paths are what makes re-import painless); users
  who want history put a date in the name. Overwrite is per-file, not a
  directory sync: a re-run with FEWER clips leaves the earlier run's extra
  `media/` files behind (harmless; documented v1 behavior).
- All timeline files reference media by ABSOLUTE path (§4.3) — same-machine
  or shared-drive import links without relinking.

Tier 2's `PremiereSendResult` (§10.5) writes under a SIBLING tree,
`<comfy output>/premiere_results/`, with the OPPOSITE naming rule —
collision-free names, never overwrite — because every push is a NEW import
into Premiere, not a re-import of the same timeline. Full rules in §10.5.

## §3 `PremiereSaveTimeline` (display: "Save Premiere Timeline")

### §3.1 Inputs

- `video_1`, `video_2`, … (VIDEO, all optional) — core ComfyUI VIDEO
  objects. DYNAMIC (owner ask 2026-07-19 "why only 4 slots, grow like image
  nodes"): the backend accepts an UNBOUNDED number of `video_N` (validated
  via a flexible optional-inputs dict so any `video_N` passes), and the
  frontend grows the visible sockets — a fresh empty `video_N+1` appears
  whenever the last one is connected, and trailing empties collapse
  (rgthree/image-batch pattern). The old fixed `video_1..4` is retired.
- `paths` (STRING, multiline, optional) — additional media, one absolute
  path per line (blank lines and `#`-prefixed lines ignored). They must
  exist and be probeable at execution time or the run errors naming the
  offending line. Referenced in place or copied per §3.2 `media`.
- Clip order on the timeline: connected `video_N` in ascending N first,
  then `paths` lines top to bottom, back-to-back from 00:00:00:00 on video
  track 1.

### §3.2 Widgets

- `sequence_name` (STRING, default `"ComfyUI Timeline"`).
- `fps` (COMBO of strings: `23.976, 24, 25, 29.97, 30, 50, 59.94, 60`;
  default `24`) — the SEQUENCE rate; §4.2 maps it to timebase+ntsc.
- `media` (COMBO, owner ask 2026-07-19): **"Link in place"** (default) vs
  **"Collect into folder"**. Link = `paths` entries are referenced at their
  original location (zero copy, the S1-verified behavior). Collect = each
  `paths` entry is COPIED into `media/` and the timeline references the
  copy — for handing the whole timeline folder to someone else, or NAS
  media you don't want to depend on staying put. VIDEO inputs are always
  materialized into `media/` regardless (a generated VIDEO has no source
  file to link). The owner's NAS finding (S2) was Collect behavior applied
  when Link was wanted — this widget is the fix.
- `write_edl` (BOOLEAN, default False) — see §5 (an alternate interchange
  file; most users leave it off).
- `write_otio` (BOOLEAN, default False; missing `opentimelineio` ⇒ warning
  in the node result, not a failure — soft dependency; see the OTIO note
  in §5).
- `output_dir` (optional STRING, default `""`, owner ask 2026-07-20): empty
  keeps the §2 default base (`<comfy output>/premiere_timelines/`); a
  non-empty, ABSOLUTE value replaces that base directly — the timeline
  still gets its own `<sanitized sequence_name>/` subfolder either way
  (never straight into `output_dir`'s own root), so this becomes
  `<output_dir>/<sanitized sequence_name>/` with no `premiere_timelines`
  middle folder — letting a timeline land on a project or NAS folder of the
  user's choosing. A non-empty value that ISN'T absolute is rejected
  cleanly (a warning in the server log and the node's own UI summary text;
  the default base is used instead — never a hard failure over a
  hand-typed path mistake). §7.3's Browse… (directory-choose mode) writes
  here; §7.2's `GET /cprb/timeline_dir` accepts the same param so "Open
  folder" always resolves the identical effective path this widget would
  actually write to.

### §3.3 Behavior

- VIDEO inputs are materialized into `media/` via the VIDEO object's own
  `save_to(...)` (h264/mp4, ComfyUI's default encode path) as
  `NNN_<sanitized name>.mp4`. `paths` entries: LINKED in place under
  `media: "Link in place"` (zero re-encode, zero copy), or COPIED verbatim
  into `media/` under `media: "Collect into folder"` (byte copy, no
  re-encode). ProRes materialization is a follow-up pending SPIKES §S3.
- Every clip's duration comes from probing the on-disk file with PyAV
  (frames + native fps); the clip occupies `round(seconds * sequence_fps)`
  sequence frames. Sources whose native fps differs from the sequence fps
  are still cut at their real-time length (Premiere plays them at native
  speed inside a conformed sequence).
- Returns: `timeline_path` (STRING — the `.xml` absolute path) and a UI
  text summary listing every file written plus any warnings (skipped otio,
  fps notes).
- `OUTPUT_NODE = True` (it exists for its side effects).

## §4 Emitted FCP7 XML (xmeml)

The reliable import target for stable Premiere (research: OTIO import has
never verifiably left Premiere Beta; xmeml import is documented and
long-lived).

### §4.1 Document shape

`<?xml version="1.0" encoding="UTF-8"?>` + `<!DOCTYPE xmeml>` +
`<xmeml version="4">` containing exactly one `<sequence>`:

```
sequence
  uuid, duration, rate(timebase,ntsc), name
  media
    video
      format/samplecharacteristics (rate = SEQUENCE rate;
                                    width/height ← from clip 1)
      track
        clipitem (one per clip; see §4.4)
    audio                                                   ← v1: EMPTY track
  timecode (rate, string "00:00:00:00", frame 0, displayformat NDF/DF)
```

The format block describes the SEQUENCE's editing format, so its `rate` is
the §4.2 sequence rate (matching what real Premiere exports carry there);
only the pixel dimensions are borrowed from clip 1, v1's stand-in for a
dedicated resolution widget.

Audio: v1 writes an empty audio track (video-only edit). Linked audio is a
contracted follow-up (§9 S5) — do NOT half-emit `<audio>` clipitems.

### §4.2 Rate mapping (fps → timebase + ntsc)

| fps widget | timebase | ntsc |
|---|---|---|
| 23.976 | 24 | TRUE |
| 24 | 24 | FALSE |
| 25 | 25 | FALSE |
| 29.97 | 30 | TRUE |
| 30 | 30 | FALSE |
| 50 | 50 | FALSE |
| 59.94 | 60 | TRUE |
| 60 | 60 | FALSE |

`displayformat` is `DF` for 29.97/59.94, else `NDF`.

### §4.3 pathurl encoding

`file://localhost/` + the absolute path, URL-encoded per segment
(`urllib.parse.quote`, safe `"/"`), with Windows drive colons encoded
(`C:` → `C%3a`) and backslashes first normalized to `/`:

- Windows: `C:\renders\shot 01.mp4` → `file://localhost/C%3a/renders/shot%2001.mp4`
- macOS: `/Users/eric/out.mp4` → `file://localhost/Users/eric/out.mp4`
- UNC paths (`\\nas\share\…`) are written as
  `file://nas/share/…` (host in the authority slot). Flagged UNCONFIRMED
  until SPIKES §S2 verifies against a real Premiere import.

### §4.4 clipitem

Per clip `i` (1-based), with `S` = start frame on the timeline, `D` =
duration in sequence frames:

- `id="clipitem-i"`, `name` (media file stem), `enabled` TRUE, `duration`
  D, `rate` = sequence rate, `start` S, `end` S+D, `in` 0, `out` D,
  `<file id="file-i">` with `name`, `pathurl` (§4.3), `rate` = the file's
  NATIVE rate (probed), `duration` (native frames), and
  `media/video/samplecharacteristics` (probed width/height). A file
  referenced by several clips still gets one `<file>` definition per
  clipitem with the SAME id (xmeml id-reference convention: subsequent
  occurrences may be `<file id="file-i"/>` self-closing references).

v1 emits `in=0, out=D` (whole file). Sub-range clips arrive with §6's
round-trip work (a segment list re-emitted as a timeline keeps its in/out).

## §5 Emitted EDL (CMX3600)

Belt-and-braces fallback (EDL carries no paths — Premiere relinks by
reel/name):

```
TITLE: <sequence_name>
FCM: NON-DROP FRAME            ← or DROP FRAME per §4.2 displayformat

001  AX       V     C        00:00:00:00 00:00:05:00 00:00:00:00 00:00:05:00
* FROM CLIP NAME: <clipname.ext>
* SOURCE FILE: <absolute path>
```

- Event numbers `001`-based; reel is always `AX` (aux source); channel `V`;
  transition `C` (cut). Source in/out = `00:00:00:00` → clip duration;
  record in/out = the clip's timeline span. Timecodes at sequence fps
  (frames column = `round(remainder)`, drop-frame semicolon NOT used — DF
  is declared via `FCM:` only, v1).
- The `* SOURCE FILE:` comment carries the absolute path for humans and for
  Premiere's relink search.

## §6 `PremiereLoadTimeline` + `PremiereGetShot`

The reverse direction: Premiere `File > Export > Final Cut Pro XML` → these
nodes → per-shot processing ("restyle my whole edit").

### §6.1 `PremiereLoadTimeline` (display: "Load Premiere Timeline")

- Widget: `file_path` (STRING — absolute path of a Premiere-exported
  `.xml`).
- Outputs: `segments` (custom type `CPRB_SEGMENT_LIST`), `count` (INT),
  `summary` (STRING — one line per segment: index, name, source path, in/out
  timecode) — wire `summary` into a Show Text node for a free segment sheet.
- Parses EVERY `clipitem` on every video track of the first `<sequence>`,
  in ascending `start` order (track 1 first on ties). Per shot it captures:
  `name`, `path` (decoded §4.3 pathurl; percent-decoding + `file://` and
  `file://localhost/` forms), `start`/`end` (timeline frames), `in`/`out`
  (source frames), `sequence_fps`, `source_fps` (three-tier: the clipitem's
  own rate when present — a real Premiere clipitem's rate can genuinely
  diverge from its file's — else the file's rate, else the sequence rate;
  this is the rate the clip's `in`/`out` frame numbers are counted in),
  `enabled`, and (added 2026-07-19) `width`/`height` from the clip's
  `<file><media><video><samplecharacteristics>` (`0` when the export omits
  them — real Premiere exports and our own writer include them; §8 permits
  adding keys).
- Disabled clipitems are kept (flagged `enabled: false`) — the summary
  marks them; a `skip_disabled` BOOLEAN widget (default True) excludes them
  from `segments`/`count`.
- Tolerant parser: missing optional metadata never fails; a file with zero
  video clipitems errors loudly (wrong file, not an empty result).
- `IS_CHANGED` → file mtime/size so a re-export re-runs.

### §6.2 `CPRB_SEGMENT_LIST`

A plain python `list[dict]` with the §6.1 keys. Custom-typed so it can only
wire into cprb consumers; contents are documented here and FROZEN.

### §6.3 `PremiereGetShot` (display: "Get Premiere Segment")

- Inputs: `segments` (CPRB_SEGMENT_LIST); widget `index` (INT, 0-based, default
  0; out of range ⇒ clear error naming the valid range).
- Outputs, in this order (owner reorder 2026-07-19 — the "seconds" pair and
  the "frame" pair each swapped so the load-cap value leads its partner,
  matching how they wire into VHS): `path` (STRING), `duration_seconds`
  (FLOAT), `in_seconds` (FLOAT — source in ÷ source_fps), `frame_count`
  (INT), `in_frame` (INT), `fps` (FLOAT — source fps), `name` (STRING),
  `width` (INT), `height` (INT). ⚠ Reordering outputs shifts socket
  indices, so a workflow saved before this change re-wires by position on
  load — acceptable pre-release; re-check any existing Get Premiere Segment wiring once.
- The frame outputs feed VHS `Load Video (Path)`'s `frame_load_cap`
  (`frame_count`) / `skip_first_frames` (`in_frame`) directly; the seconds
  outputs suit core loaders; `width`/`height` feed resize/crop or a Create
  Video (ethos §1: existing nodes do the reading). The frame outputs are
  counted in `source_fps` (§6.1's three-tier rate); for a clip Premiere
  conformed to a different rate, prefer the SECONDS outputs, always
  real-time-correct.

### §6.4 `PremiereIterateShots` (display: "Iterate Premiere Segments")

The answer to "how do I process every segment" (owner ask 2026-07-19) — ComfyUI
has no for-loop, so this fans out via list execution, exactly like EPSNodes'
multi-select notebook.

- Input: `segments` (CPRB_SEGMENT_LIST); widget `skip_disabled` is unnecessary
  (Load already filtered) — none.
- Outputs mirror Get Premiere Segment's set (`path, duration_seconds, in_seconds,
  frame_count, in_frame, fps, name, width, height`) but ALL declared
  `OUTPUT_IS_LIST` — one element per segment, in shot order. ComfyUI then runs
  every downstream node once per segment from a SINGLE queue: wire `path`+the
  frame outputs into VHS `Load Video (Path)` and one Run processes the
  whole edit shot by shot. An empty segment list yields empty lists (downstream
  simply doesn't run) — not an error.

### §6.5 `PremiereShotFrame` (display: "Get Premiere Segment Frame")

Optional preview thumbnail (owner ask 2026-07-19 "can we pull a preview
frame… if easy/reliable"). SEPARATE node so the decode cost/failure never
touches Get Premiere Segment's cheap metadata path.

- Inputs: `segments` (CPRB_SEGMENT_LIST); widget `index` (INT, like Get Premiere Segment).
- Output: `image` (IMAGE) — one frame decoded via PyAV at the shot's
  `in` point (seek to `in_seconds`, decode the nearest frame, return HWC
  RGB float [0,1], batch 1). Best-effort: media offline/undecodable ⇒ a
  clear error naming the file (the owner's fallback is "rely on VHS", so a
  hard error here is fine — he simply won't wire this node). No decode
  happens unless this node is in the graph.

## §7 Routes & frontend

Routes register on `PromptServer.instance.routes` (never the app directly)
so ComfyUI's `/api` prefix mirror serves them — the frontend's `fetchApi`
always calls `/api/cprb/...`.

**§7.1 Host-machine posture.** The picker and reveal routes below act on
the SERVER's filesystem, so they are **loopback-only**: a request whose
`request.remote` isn't a loopback address (or that carries an
`X-Forwarded-For` header — a proxy hop hides the real origin) gets
`403 {"error": ...}`. A remote browser (the Mac viewing the PC's ComfyUI)
therefore hides those buttons and types paths by hand; nothing else about
the nodes changes. Same rule and rationale as EPSNodes' FORMAT.md §2.

**§7.2 Routes.** JSON in/out; errors are `{"error": "<human message>"}`.

| Route | → |
|---|---|
| `GET /cprb/version` | `{"version": "X.Y.Z"}` |
| `GET /cprb/config` | `{"is_local": bool, "output_dir": <abs>, "input_dir": <abs>}` — `is_local` is the §7.1 verdict for THIS caller (gates the buttons); the dirs seed the picker's starting location |
| `GET /cprb/fs/list?dir=&ext=` | **loopback-only** (`FS_LIST_LOCAL_ONLY=True`). Conforms to the cross-pack **`../../STANDARD-fs-browse.md`** contract (shared with cpsb + epsnodes; v0.5.1). Empty/missing `dir` ⇒ `output_dir`. `dir="ROOTS"` ⇒ the labeled top level: **"ComfyUI Output"** + **"Home"** + platform tail (Windows drives `C:\`/`D:\`/`U:\`…, or macOS `/Volumes/*`). `ext` = a comma-separated allowlist (default `.xml`; case-insensitive). → `{"dir", "parent" (abs / "ROOTS" / null), "sep", "dirs":[{name}], "files":[{name,size,mtime}], "truncated"}` — **names-only** entries (client joins with `dir`+`sep`; ROOTS entries also carry an absolute `path`), case-insensitively sorted, dotfiles + stat-failures skipped, 500-entry cap ⇒ `truncated:true`. A directory at a drive root reports `parent:"ROOTS"` so the picker can climb to the top level (the 2026-07-19 "stuck at top of C:\" fix); a UNC path lists normally, its share root `parent:null`; non-absolute `dir` (other than `ROOTS`) ⇒ 400; unreadable ⇒ 400 |
| `POST /cprb/open_folder` `{"path"}` | **loopback-only.** Reveals *path* in the OS file manager ON THE SERVER MACHINE (Explorer/Finder): a file reveals its parent folder, a directory reveals itself. Missing ⇒ 404; spawn failure ⇒ 500; `{"ok": true}` |
| `GET /cprb/timeline_dir?sequence_name=&output_dir=` | `{"dir": <abs>, "exists": bool}` — the §2 output folder this `sequence_name` resolves to, computed server-side so the frontend never re-implements `sanitize_name`. `output_dir` (2026-07-20, §3.2) is optional and mirrors the node's own widget of the same name — passing it resolves the SAME effective folder `PremiereSaveTimeline` would write to with that override; omitted/blank behaves exactly as before it existed. Never 400s on a non-absolute `output_dir` — it is silently treated as blank (the node is the one that surfaces the "rejected" warning; this route only ever mirrors what the node would actually do) |
| `GET /cprb/ws` | websocket upgrade — the Tier 2 plugin connection. Not JSON-in/JSON-out like the rows above; the full message contract lives in §10 |

**§7.3 Frontend.** `web/cprb.js`: one
`app.registerExtension('cprb.PremiereBridge')` with the About-panel badge
and the "Premiere Bridge" settings section showing backend+frontend
versions (mismatch = pulled-but-not-restarted; cpsb pattern), plus:

- **Load Premiere Timeline** gains a file bar under its widgets: `Browse…`
  opens a picker dialog over §7.2 `fs/list` (navigate folders, `..` row,
  `.xml` files only; picking one writes the `file_path` widget through its
  real setter) and `Open folder` reveals the selected file's folder. Both
  buttons are HIDDEN when `config.is_local` is false (§7.1).
- **Save Premiere Timeline** gains a file bar matching Load's (owner ask
  2026-07-20: "Save … does not have the same Browse…/Open folder as the
  Load node does" — the prior intentional asymmetry is REVERSED; give it
  parity). Two buttons, same styling/placement as Load:
  - `Browse…` opens the §7.2 `fs/list` picker in **directory-choose mode**
    (folders only, no `.xml` file filter, an explicit "Choose this folder"
    action) and writes the chosen absolute path to a new **optional**
    `output_dir` STRING widget on the node. Empty `output_dir` = the
    existing default (`<comfy output>/premiere_timelines/<sequence_name>/`);
    a set `output_dir` writes the timeline folder under THAT base instead
    (so timelines can land on a project/NAS folder). Backend: §3 gains an
    optional `output_dir`; when non-empty and absolute it replaces the
    comfy-output base, still appending the sanitized `sequence_name` folder
    (never write straight into a user dir's root). Loopback-only + is_local
    gated exactly like Load's picker.
  - `Open folder` resolves the effective output folder (via `timeline_dir`,
    now `output_dir`-aware) and reveals it; before the first run the folder
    may not exist yet — the button says so rather than erroring. Renamed
    from "Open output folder" to "Open folder" for parity with Load.
  Both buttons hidden when `config.is_local` is false, same as Load.
  `fs/list` gains a documented **`dirsonly`/directory-choose** affordance
  for this (the picker already lists dirs; the mode just drops the file
  filter and surfaces a choose-current-folder control) — extend §7.2's
  entry + `STANDARD-fs-browse.md` if the shared contract needs it.
- **Growing video inputs on Save Premiere Timeline** (owner report
  2026-07-19: "I can only connect one video; a new connection replaces the
  previous"). The backend already accepts unbounded `video_N` (§3.1); the
  frontend must GROW the visible sockets so more than one can be wired:
  on `onConnectionsChange` (input side), after the last `video_N` slot gets
  a connection, add a fresh empty `video_(N+1)` input; when trailing empty
  `video_N` slots pile up beyond one, remove the extras so there's always
  exactly one spare — the rgthree / core image-batch pattern. Must survive
  workflow save/reload (rebuild the right number of slots from the restored
  connections) and never renumber a CONNECTED slot (a connected `video_2`
  keeps its name/link). Verify against the frontend build ComfyUI 0.28.1
  actually ships, not just the dev rig's.
- Node class ids and widget names are untouched by all of this: the
  buttons are frontend affordances over the SAME `file_path` /
  `sequence_name` widgets, so API-driven and remote use are unaffected.

## §8a Node categories (the two buckets)

Every node declares one of exactly two CATEGORY values, mirroring the sibling
Photoshop pack's `Photoshop Bridge/Handoffs` + `... (requires Photoshop)`
split:

| CATEGORY | Nodes | Rule |
|---|---|---|
| `Premiere Bridge/Handoffs` | Save Premiere Timeline, Load Premiere Timeline, Get Premiere Segment, Iterate Premiere Segments, Get Premiere Segment Frame, **Send to Premiere** | The node RUNS TO COMPLETION with no Premiere installed and no panel connected |
| `Premiere Bridge/Handoffs (requires Premiere)` | Frame from Premiere, Clip from Premiere | The panel is the ONLY producer of this node's input |

The split criterion is **"does this run without Premiere?"**, not "does the
name mention Premiere". Hence the one entry that looks surprising:

- **Send to Premiere is bucket 1.** With no panel it still resolves the wired
  VIDEO/IMAGE to a real durable file, reports `written_path`, and SUCCEEDS —
  that is §1's ComfyUI-only floor, not a degraded mode. Only its three
  Premiere-side options (`bin_name`, `color_label`, `insert_at_playhead`) are
  inert without the panel.
- **Frame/Clip from Premiere are bucket 2** even though they, too, technically
  run on a hand-typed path: the panel's `export_ready` relay (§11.3) is the
  only thing that fills them in normal use, so a user browsing bucket 1 must
  not find them there and assume they work standalone.

CATEGORY is a node-browser grouping only — it is NOT part of the frozen
surface in §8, since ComfyUI matches saved workflows on class id, never on
category. It may be re-organised without breaking a saved graph.

## §8 Versioning & stability

- `cprb/version.py` (source of truth) + `pyproject.toml` +
  `web/cprb/version.js`, lockstepped by `scripts/bump_version.py`; every
  push bumps ≥ patch and is tagged `vX.Y.Z`; docs-only changes don't bump.
- FROZEN once shipped: node class ids, route paths, §4/§5 file semantics,
  §6.2 segment-dict keys, **and every INPUT socket name — unless a load-time
  migration ships in the same release** (below). New fields may be added;
  existing ones never change meaning.
- **Why socket names are load-bearing (measured).** ComfyUI reconciles a
  saved workflow's links against the live node definition BY INPUT NAME.
  Measured on the rig 2026-07-27: with the name changed and everything else
  identical, a saved link loads as `link: null` **and `has_errors: false`** —
  the wire is silently gone with no warning anywhere. The socket TYPE is *not*
  part of that match (a type-only mismatch keeps the link), which is why
  `CPRB_SHOT_LIST` → `CPRB_SEGMENT_LIST` was safe in v0.12.0 on its own.
- **The `shots` → `segments` rename shipped in two steps.** v0.12.0 kept the
  socket NAME as `shots` and showed a `segments` LABEL on live nodes — which
  the owner then correctly rejected (2026-07-29): the node-library hover
  preview renders from the DEFINITION's socket names, which no per-instance
  label can reach, so it still said `shots`. v0.13.0 therefore renames the
  REAL socket names (`shots` → `segments` on all five segment-speaking
  classes, inputs and outputs, including the Python kwargs) and ships the
  required migration: `web/cprb/nodes.js migrateSegmentSocketNames`, called
  from the extension's `beforeConfigureGraph` hook, rewrites a pre-v0.13.0
  save's socket names in memory BEFORE reconciliation ever sees them, so old
  workflows load with their wires intact. Verified against a pre-rename
  fixture on the rig.
- **Known migration limit:** an API-FORMAT export ("Export (API)") is keyed by
  the Python input names and never passes through the frontend hook — one
  saved before v0.13.0 fails validation ("required input missing: segments")
  and must be re-exported from the reopened workflow. Accepted: the UI format
  is what the owner and the bundled example use.
- The remaining internal `shot` vocabulary is the truly frozen surface: the
  class ids (`PremiereGetShot`, `PremiereIterateShots`, `PremiereShotFrame`)
  and §6.2's dict keys. Everything a user can read says **segment**.

## §9 Spikes (details in docs/SPIKES.md)

- S1: golden `.xml` imports into real Premiere (Eric's PC) and links media.
- S2: UNC-path pathurl form Premiere actually accepts.
- S3: ProRes via VIDEO `save_to` / PyAV encoder availability.
- S4: OTIO output imports into Premiere Beta / Resolve.
- S5: linked-audio clipitems (v1.1 feature, needs S1 first).
- S6: Premiere UXP panel — plain `ws://` permission + EncoderManager range
  semantics (Tier 2 gate; mirrors the cpsb spike discipline).

## §10 Tier 2 — plugin websocket (M1)

The Premiere UXP panel (`premiere_plugin/`) is a websocket client of
ComfyUI's own server, sibling of comfyui-photoshop-bridge's plugin, proven
by the SPIKES.md S6 round (plain `ws://localhost` from inside Premiere
26.3: PASS, 2026-07-23, owner's PC). M1's contract is deliberately
minimal: the plugin connects and handshakes, and the server pushes
finished results for it to import ("ComfyUI results land in a Premiere
bin"). Everything else — frame export, progress, keepalive — is M2+.

### §10.1 Route & single-plugin slot

| Route | → |
|---|---|
| `GET /cprb/ws` | websocket upgrade (also mirrored under `/api/`, like every §7 route) |

- ONE plugin connection at a time. A second connection supersedes the
  first: the server closes the old socket with code **4000** / message
  `replaced by a new connection` (cpsb's exact convention — the plugin
  treats 4000 as "another panel took over" and does not auto-reconnect),
  then installs the new one. A superseded socket's late cleanup never
  clears its replacement's slot.
- Disconnect clears the slot; there is no session state to resume — a
  reconnecting plugin simply re-handshakes from `hello`.
- **SAME-MACHINE-ONLY (M1).** Contrast cpsb's REMOTE mode: `pr_result`
  carries HOST-filesystem paths the plugin reads/imports directly, and the
  message says nothing about transporting bytes — a plugin on another
  machine is out of scope for M1 (the primary deployment is the owner's
  PC, running both ComfyUI and Premiere; a cross-machine mode, if ever, is
  a later § the way cpsb grew one). The route is not loopback-ENFORCED in
  M1 — enforcement is a noted hardening item alongside §10.2's keepalive —
  but nothing works cross-machine because the paths don't.

### §10.2 Handshake (plugin → server unless marked)

| Message | Fields | Server behavior |
|---|---|---|
| `hello` | `plugin_version` | records it; replies **`hello_ack`** (server → plugin) carrying `server_version` (= `cprb/version.py`) |
| `ready` | — | marks the connection READY; only a ready connection receives §10.3 pushes |
| `pong` | — | accepted and ignored: M1 sends no pings (a server-side keepalive/staleness loop is a noted future hardening item — cpsb runs one), but accepting `pong` now means adding it later needs no plugin change |
| anything else | — | logged at debug and ignored — never a disconnect (§8's additive-only stability rule applies to this surface too) |

Non-JSON frames are logged and ignored. The server never disconnects a
plugin over a bad message; version skew must stay pairable.

### §10.3 `pr_result` (server → plugin)

Sent by `PremiereSendResult` (§10.5) through `cprb.routes.push_result`,
once per resolved file:

| Field | Meaning |
|---|---|
| `type` | `"pr_result"` |
| `path` | ABSOLUTE host-filesystem path of the media to import (§10.1 posture) |
| `label` | clip name for the imported item; empty = keep the filename |
| `bin_name` | project-panel bin to import into (plugin creates it if missing; node default `ComfyUI Results`) |
| `color_label` | ALWAYS present; `""` until a later node version adds the widget — the plugin skips absent/EMPTY values |
| `insert_at_playhead` | ALWAYS present; `false` until a later node version adds the widget — same skip-when-unset rule |
| `sent_ts` | server Unix time the push was sent (float seconds) |

Delivery contract: `push_result` is called on ComfyUI's prompt WORKER
thread and is bounded — the cross-thread send onto the server's event loop
has **5 s** to complete (and is refused outright if the caller somehow IS
the event-loop thread, where waiting would deadlock), else it cancels
best-effort, logs, and returns `False`. A push therefore never blocks the
prompt queue and never raises into a running workflow: `False` (no plugin,
not ready, timeout, dead socket) surfaces only as the node's
"import manually" summary line. There is no application-level ack in M1 —
delivery to the socket is the contract; import success/failure lives in
the plugin's own panel log.

### §10.4 `export_ready` (plugin → server — M2's inbound half)

Accepted now because it is cheap and additive: the message's payload
(every field except `type`) is logged and — when the running context has a
frontend emitter — relayed VERBATIM as a `cprb.export_ready` frontend
event (`PromptServer.send_sync`; `BridgeContext.send_event`). The
CONSUMING frontend listener ships with M2, which also fixes this message's
field schema; nothing in M1 sends or depends on it.

### §10.5 `PremiereSendResult` (display: "Send to Premiere")

Inputs (ALL optional — `required` is empty):

- `video` (VIDEO) and/or `image` (IMAGE) — at least one must be wired
  (else a clear error); both wired = both pushed in one run, video first.
- `label` (STRING, default `""`) — §10.3's `label`, and the stem of any
  file this node writes (`cprb.context.sanitize_name`, empty → `result`).
- `bin_name` (STRING, default `"ComfyUI Results"`).
- `color_label` (COMBO, since v0.9.2, APPENDED after `bin_name` — widgets
  are position-restored, §8): `None` default (sent as `""` — the plugin's
  skip value; there is no "None" member in Premiere's label enum) or one of
  the 15 label-color names + `yellow`, passed through verbatim for the
  plugin's Constants-enum/name-map lookup.
- `insert_at_playhead` (BOOLEAN, since v0.9.3, appended after
  `color_label`): OFF by default — results only land in the bin. On, the
  plugin also overwrites the clip onto the ACTIVE sequence at the playhead
  on the track above (one labeled undo step; skipped with a logged line
  when no sequence is open or the track count is unreadable — it never
  guesses a track). The Premiere-side action is VERIFY-flagged pending the
  owner's live run.

Resolution rules (the §2-amending `premiere_results/` conventions):

- Everything this node WRITES lands under
  `<comfy output>/premiere_results/` as
  `<sanitized label>_<YYYYMMDD-HHMMSS>[_N]<ext>` — COLLISION-FREE, never
  deterministic-overwrite. Opposite rule from §2's overwrite-in-place
  timelines, deliberately: a re-imported timeline should replace itself,
  but every push here is a NEW import, and overwriting would silently
  swap media already cut into a Premiere project.
- VIDEO with an existing, untrimmed source file (ComfyUI core's
  `get_stream_source()` naming a real path):
  - outside ComfyUI's temp dir → LINKED IN PLACE: the source path is
    pushed as-is, zero copy — multi-GB results are instant;
  - inside the temp dir → byte-COPIED into `premiere_results/` first
    (original extension kept; a copy never re-encodes), because Premiere
    links media in place and a temp file cleaned up later goes offline in
    the project.
- VIDEO that is in-memory, TRIMMED (an active trim window means the source
  file on disk is not the video the graph wired), or otherwise unlinkable
  → written to `premiere_results/` as mp4 via the object's own `save_to`
  (§3.3's shared mechanism). Audio survives on every branch — link and
  copy never touch the bytes, and core's `save_to` carries audio streams —
  so `*-audio.mp4` I2V results keep their soundtrack. A `video` input with
  no usable `save_to` is a clear error naming the input.
- IMAGE → first frame written as PNG. A batched IMAGE (N>1) writes the
  FIRST frame and says so in the summary (list-mode fan-outs, §6.4,
  already run this node once per item).

Outputs: `written_path` (STRING — the video's resolved path when both
inputs are wired, else the single result's; for a linked-in-place video
this is the ORIGINAL source path, the one Premiere imports) plus a UI text
summary: per file, `Sent to Premiere: <path>` or `Plugin not connected —
import manually: <path>`, with any notes (temp-copy, trim, batched image)
indented beneath. No plugin connected is NOT an error — §1's ethos:
ComfyUI-only must work; the plugin is a better version, never the only
version. `OUTPUT_NODE = True`.

`IS_CHANGED` (since v0.9.7) returns this node's MONOTONIC failed-delivery
count, so a push that never reached Premiere earns exactly ONE retry on the
next queue and a successful one leaves the node cached. Owner bug: a run made
while Premiere was closed wrote the file, failed the push, and was then
cached as done — "the plugin wasn't sending videos into Pr until after I
reset the node even though the plugin was connected". Whether a push can
succeed depends on state outside every input (is a panel connected right
now?), which is exactly what `IS_CHANGED` is for. The count is deliberately
never reset: an always-dirty token instead produced one EXTRA run when a
retry succeeded (recovering from it is itself a change), i.e. a duplicate
clip in the bin — caught by a live round-trip test, not by unit tests.

### §10.6 `cprb.send_result` (server → ComfyUI frontend)

Emitted once per `PremiereSendResult` run (`context.send_event`, i.e.
`PromptServer.send_sync` — thread-safe, so the node's worker thread emits
it directly):

| Field | Meaning |
|---|---|
| `results` | list of `{path, pushed}` — one entry per resolved file, in push order (video first when both inputs are wired) |
| `bin_name` | the bin the push asked for (echoed for the toast's wording) |

WHY it exists: the node's `ui.text` summary is not rendered by anything in
ComfyUI, so a run whose push failed looked identical to one that worked
(owner, 2026-07-24: "The run finished, but I didn't see a message anywhere
that it didn't work"). Since v0.9.7 the payload also carries `node_id` (from
the node's hidden `UNIQUE_ID`), because the owner reported the same blindness
a SECOND time on 2026-07-25: a toast depends on the host frontend having a
working toast surface and on the user looking at that moment, and it vanishes
either way. So the AUTHORITATIVE surface is now a persistent status line
painted on the node that did the work; the toast is the glanceable extra.
`web/cprb/send_result.js` does both — a short info one on success, and on failure a long-lived WARNING
carrying the full path, because the user's next action is to import that
file by hand. Failure to emit or render is swallowed: a UI notification
never fails a finished run.

## §11 Tier 2 — M2: Premiere → ComfyUI

§10 is the outbound half: ComfyUI pushes a finished result and the panel
imports it. **M2 is the RETURN direction** — the panel hands ComfyUI
something out of the open project, and a source node in the graph picks it
up. Together the two halves are the round trip that
`examples/premiere_roundtrip.json` demonstrates end to end.

M2 adds **no new route and no new message type**: it reuses §10.1's
`GET /cprb/ws`, adds one additive field to §10.2's `hello_ack`, and fixes
the field schema of §10.4's already-accepted `export_ready` (which M1
shipped as an accept-and-relay stub for exactly this reason).

**SAME-MACHINE ONLY**, unchanged from §10.1 and for the same reason: no
bytes cross the websocket in either direction. Every M2 message carries an
absolute host-filesystem path, and ComfyUI opens that path itself. A panel
on another machine would send paths ComfyUI cannot read.

Two gestures, deliberately ASYMMETRIC because Premiere's own capabilities
are:

| Panel button | What Premiere does | What ComfyUI receives |
|---|---|---|
| **Frame → ComfyUI** | exports ONE still at the playhead of the active sequence (`pr.Exporter.exportSequenceFrame`, §11.7) | a fresh PNG inside §11.1's `frames_dir` |
| **Clip → ComfyUI** | **nothing** — reads the selected clip's own `getMediaFilePath()` plus its SOURCE in/out points | the ORIGINAL media file's path plus a time range. Zero export, zero re-encode, instant for multi-GB media |

The clip half is free because the backend already decodes media itself
(`cprb/frame_extract.py`, `cprb/probe.py`) — §1's ethos again: the pack adds
only what the ecosystem lacks, and "hand a loader a path and an in/out" is
something the ecosystem already does very well.

The two new nodes are `PremiereFrameSource` (display "Frame from Premiere",
§11.4) and `PremiereClipSource` (display "Clip from Premiere", §11.5), both
CATEGORY `"Premiere Bridge/Handoffs (requires Premiere)"` (§8a). Per §8 both class
ids — and the widget ORDER on each — are **FROZEN once shipped**: saved
workflows reference nodes by id and restore widget values BY POSITION, so
later widgets are appended at the END only.

### §11.1 `hello_ack` gains `frames_dir` (server → plugin)

§10.2's `hello_ack` gains ONE additive field. Additive is the whole point: an
M1 plugin ignores it and keeps working, and an M2 plugin talking to an M1
server sees it missing and REFUSES to export — naming the reason in its log
("the ComfyUI side is older than the M2 server — update it") rather than
inventing an output path that would fail silently. "Clip → ComfyUI" needs no
`frames_dir` at all and stays fully usable.

| Field | Meaning |
|---|---|
| `type` | `"hello_ack"` |
| `server_version` | unchanged (§10.2) — `cprb/version.py` |
| `frames_dir` | **new.** Absolute path of the folder the panel writes frame exports into — `<ComfyUI input dir>/premiere_frames/` by default, or `$CPRB_FRAMES_DIR` when that names an ABSOLUTE path — **created by the server on demand** as the handshake is answered |

- Under ComfyUI's INPUT dir (not output) because these are inputs to a
  graph — the same tree `LoadImage` and the video loaders already browse.
  Sibling naming to §2's `premiere_timelines` and §10.5's `premiere_results`,
  both of which are output-side.
- The server creates it at `hello`, so it is writable the instant the plugin
  finishes handshaking. The plugin never creates it — with
  `localFileSystem: "request"` it cannot create a folder the user never
  picked in a dialog — and **never invents a subfolder inside it** (no
  per-sequence, per-date nesting), because Premiere's export writes nothing
  at all when its target directory is missing (§11.7).
- Asking where the folder is must not be the thing that creates it, so
  `resolve_frames_dir` (compute) and `ensure_frames_dir` (create) are
  separate — the same split §7.2's `timeline_dir` route already uses.
- **`CPRB_FRAMES_DIR` redirects it.** An ABSOLUTE path in that environment
  variable wins over the default; anything else is ignored with a log line
  and never an error — the same posture §3.2's `output_dir` takes on
  `PremiereSaveTimeline`, and for the same reason. ComfyUI's input dir is
  often a NAS/UNC share on a real install, whether Premiere's exporter can
  write a PNG over SMB is not something this pack can prove for every setup,
  and the plugin deliberately refuses to invent a path — so without an escape
  hatch a share that refuses the write would brick "Frame → ComfyUI" short of
  moving ComfyUI's entire input directory. The effective folder is logged at
  INFO on every `hello`, so it is visible in the ComfyUI console and not only
  in the panel.
- **Preparing the folder runs OFF the event loop, bounded** (5 s). `mkdir`
  and the prune pass below are blocking syscalls, and against a sleeping SMB
  share a blocking `mkdir` on aiohttp's loop stalls ComfyUI *entirely* —
  every HTTP request, the frontend websocket, queue progress — presenting as
  "ComfyUI froze when I connected the Premiere panel". On timeout the
  handshake is answered anyway with the resolved path.
- **Retention: the newest 200 `*.png` survive; older ones are deleted** on
  each `hello`, in that same off-loop task. A contract, not a surprise: every
  click writes a fresh full-resolution still (unique names are *required* —
  §11.7 — which is exactly what stops the folder self-limiting), a 4K PNG is
  10-25 MB, and nothing else would ever clean it up. Only `*.png` is touched.
- Directory-creation failure is logged and NOT raised: the handshake matters
  more than the folder, and `hello_ack` still reports the resolved path so
  the failure stays diagnosable (the plugin's export error names it, and
  §11.4's `VALIDATE_INPUTS` says the file is missing) rather than handing the
  plugin nowhere to write.

### §11.2 `export_ready` (plugin → server)

Already accepted and relayed VERBATIM (minus `type`) by
`cprb.routes._handle_plugin_message` since M1 (§10.4). M2 fixes its payload:

| Field | Present for | Meaning |
|---|---|---|
| `type` | always | `"export_ready"` |
| `kind` | always | `"frame"` or `"clip"` — selects which node class consumes it (§11.3) |
| `path` | always | ABSOLUTE host path. `kind=frame`: the exported still, inside `frames_dir`. `kind=clip`: the clip's OWN media file (`ClipProjectItem.getMediaFilePath()`) — never a copy, never an export |
| `label` | always | Human name for toasts and logs: the sequence name for a frame, the clip's `getName()` for a clip. Never used to build a path |
| `ticks` | frame only | Playhead `TickTime.ticks` — a STRING (§10's ground truth: ticks overflow a JS number) |
| `seconds` | frame only | Playhead `TickTime.seconds` — a number; where in the SEQUENCE the still came from |
| `start_seconds` | clip only | The clip's in point **inside its SOURCE media**, seconds (`getInPoint()`, never `getStartTime()`) |
| `end_seconds` | clip only | The clip's out point inside its source media, seconds (`getOutPoint()`). **EXCLUSIVE** — see §11.7 |
| `nonce` | always (panels ≥ v0.11.0) | Unique per button press. `send_sync` broadcasts to EVERY open ComfyUI tab, and §11.3's auto-run must queue ONCE — the first tab to claim the nonce in `localStorage` (shared across same-origin tabs) runs; the rest only fill widgets. Absent (older panel): every tab auto-runs; accepted skew behaviour |

**The server checks the path and says so, additively.** Two fields are
ADDED to the relayed payload (nothing the plugin sent is ever rewritten):

| Field | Meaning |
|---|---|
| `path_exists` | `true`/`false` — does a file exist there? ABSENT means unverified (the check timed out, or an older server) |
| `resolved_path` | present only when a `kind=frame` path resolved to a DIFFERENT name than reported — i.e. the doubled-extension fallback below found the file. The frontend writes this into the widget |

- This hop is the ONLY one that both sees the message and can look at the
  disk. The panel cannot: its `localFileSystem: "request"` manifest grants
  access only to entries the user picked in a dialog, so its own probe
  answers "cannot tell" for any path under ComfyUI's input dir — which makes
  its whole retry-under-a-fresh-name ladder unreachable on a real install.
  Meanwhile `exportSequenceFrame` is documented to return `true` and
  sometimes write nothing (§11.7). Without this check the panel says "sent",
  the frontend toasts "press Run when ready", and the user only finds out at
  Run time — from advice that would loop forever, because nothing upstream
  can detect the failure. §11.4's `VALIDATE_INPUTS` stays as the second belt.
- A `kind=frame` path gets the doubled-extension fallback; a `kind=clip` path
  is taken LITERALLY. That defect belongs to `exportSequenceFrame` alone, and
  guessing at alternate names for Premiere's own media file would be
  inventing facts.
- The check is off-loop and bounded (5 s), for §11.1's reasons. A missing
  file is also logged at WARNING server-side.
- Otherwise the relay is VERBATIM: unknown extra fields ride along untouched
  (§8's additive rule) — a newer panel may send more than an older frontend
  reads.

### §11.3 `cprb.export_ready` (server → ComfyUI frontend)

The relayed payload above, emitted as the frontend event
`cprb.export_ready` via `context.send_event` (`PromptServer.send_sync`).
`web/cprb/premiere_source.js` listens — registered from `web/cprb.js`
alongside §10.6's `send_result.js` — and does exactly this:

1. Find EVERY node in the CURRENT graph whose class matches `kind`:
   `frame` → `PremiereFrameSource`, `clip` → `PremiereClipSource`.
2. Write the payload into each match's widgets through their real setters —
   `path` for a frame; `path` + `start_seconds` + `end_seconds` for a clip —
   then mark the canvas dirty (per node AND per graph) so the nodes repaint.
   §11.2's `resolved_path` wins over `path` when present, so the node opens
   the file that EXISTS rather than the one Premiere claimed to write.
3. Toast (`app.extensionManager?.toast?.add?.(...)`, every hop
   optional-chained, §10.6's pattern) naming `label` and how many nodes were
   updated.

Policy — decided, and not for implementation to reopen:

- **Update every match**, not just the first. A graph with two frame sources
  (an A/B compare) updates both; silently picking one would be a mystery.
- **ZERO matches ⇒ a WARNING toast** telling the user which node to add, by
  its exact menu name, and carrying the full path. Nothing else in the UI
  would show that an export succeeded and landed nowhere — and nothing is
  lost, because the file is already on disk.
- **AUTO-RUN, on by default** — owner decision 2026-07-27, REVERSING this
  section's original "never auto-queue" policy: the round trip must work
  like the Photoshop bridge, where "the user doesn't need to intervene."
  After a successful widget fill the relay calls `app.queuePrompt(0)` (the
  identical call and failure handling as cpsb's `maybeAutoQueue`), so the
  Premiere button press IS the whole gesture: export → fill → run → result
  back in the bin. The `cprb.autoRun` setting (Settings → Premiere Bridge,
  boolean, default true) restores the old press-Run-yourself behaviour.
  Guard rails, in order:
  - **Never on a missing file**: `path_exists === false` keeps its warning
    toast and queues nothing — auto-running a run that can only fail would
    turn one bad export into two errors.
  - **Once across tabs**: `send_sync` reaches every open tab; the panel's
    `nonce` (§11.2) is claimed in `localStorage` — the one store those tabs
    share — and only the claiming tab queues. Claims are pruned to the
    newest 20.
  - **A queue failure is LOUD**: the user was just told "Running the
    workflow…", so a rejected `queuePrompt` (typically some other invalid
    node in the graph) raises its own warning toast naming the cause,
    never a silent nothing.
- **`path_exists === false` ⇒ a WARNING toast, not "press Run when ready".**
  The widgets are still filled in (the path is the best record of what
  Premiere claimed, and a retry then needs only the button), but the success
  toast would otherwise send the user to a Run that can only fail, with
  nothing to say the export — not the graph — was the problem. An ABSENT
  `path_exists` means unverified and is treated as fine.
- **Version skew degrades to a warning toast that still carries the path**,
  never a silent drop and never a throw: an unrecognized `kind` (a newer
  panel than the pack), a matching node with no `path` widget, and an empty
  `path` on the wire each get their own message naming the likely cause. An
  absent `start_seconds`/`end_seconds` leaves the widget's existing value
  alone rather than zeroing a valid range.
- Nothing in the relay may throw into the event handler: every step is
  wrapped, and a failure degrades to a `warn` with the file still on disk.

### §11.4 `PremiereFrameSource` (display: "Frame from Premiere")

- Widget: `path` (STRING) — an ORDINARY, VISIBLE widget that §11.3's relay
  fills in. The user never types it in normal use, but it is deliberately not
  hidden: it serializes (a saved workflow reopens pointing at the last frame
  it received), hand-typing works with no panel connected at all, and a path
  sitting in plain sight is a second confirmation channel independent of the
  transient toast. The relay finds it BY NAME on `node.widgets`, so any
  future visual hide must be draw-time and KEEP it in that list — splicing it
  out, or moving it into an INPUT_TYPES `hidden` section, silently kills the
  entire return direction.
- Outputs, in this order: `image` (IMAGE), `width` (INT), `height` (INT),
  `path` (STRING). `image` is the still decoded to ComfyUI's normal HWC RGB
  float [0,1], batch 1; `width`/`height` are its decoded pixel dimensions
  (feed a resize, a latent, a Create Video); `path` is the RESOLVED path
  actually opened — see the doubled-extension fallback below — for anything
  that wants the file itself.
- **Path resolution** happens here, not in the panel: normally `path` as
  given, and if that is missing, the doubled-extension name (`foo.png` →
  `foo.png.png`) that Premiere wrote before 26.2.2 (§11.7). One extra `stat`,
  only when the reported path is absent, so it can never shadow a real file.
- `VALIDATE_INPUTS` carries the friendly upfront errors: an empty `path` ⇒
  name the BUTTON to press (never "type a path" — that is not how the node is
  driven); a path that resolves to nothing ⇒ the missing path, named, plus
  "click Frame → ComfyUI again". That is also the guard against §11.7's
  documented Premiere behavior of reporting success before — or without —
  writing the file. A `path` of `None` (the widget converted to a socket, a
  link ComfyUI cannot evaluate at validation time) passes through to
  `execute`, which raises there.
- `IS_CHANGED` → the resolved file's mtime/size, so re-exporting to the same
  path re-runs the graph (§6.1 does the same for a re-exported XML).

### §11.5 `PremiereClipSource` (display: "Clip from Premiere")

- Widgets, in this FROZEN order, all ORDINARY and VISIBLE (§11.3's relay
  fills them in; hand-typing works with no panel connected, and §11.4's
  keep-them-in-`node.widgets` rule applies identically): `path` (STRING),
  `start_seconds` (FLOAT), `end_seconds` (FLOAT).
  Both FLOATs are declared `round: False` — a frame is ~0.0417 s at 23.976,
  so the frontend's usual 3-decimal rounding could shift an in point across a
  frame boundary.
- Outputs, in this order: `segments` (`CPRB_SEGMENT_LIST`), `path` (STRING),
  `start_seconds` (FLOAT), `end_seconds` (FLOAT), `video` (VIDEO — appended
  in v0.11.0; §8's append-only rule is why it is LAST). `path`/
  `start_seconds`/`end_seconds` report the **EFFECTIVE** values actually
  used — after the clamping and whole-file fallback below — not necessarily
  the raw widget values.
- **`video` is core's own `VideoFromFile`** (`comfy_api.input_impl`), built
  lazily from the media path plus the clip's trim window — nothing decodes
  until a downstream node asks. This is what makes the clip wire DIRECTLY
  into any plain `VIDEO` input (`Send to Premiere`, core `SaveVideo`, API
  video-editing nodes) — none of which accept a bare path string, which was
  the M2 gap the owner hit on 2026-07-27. It is built from the RAW widget
  values, not the effective ones: `end_seconds <= 0` maps to core's own
  `(0, 0)` "whole file" trim sentinel, so an untrimmed clip stays honestly
  untrimmed and `Send to Premiere` can still LINK IT IN PLACE instead of
  re-encoding (its trim check keys off `get_active_trim_window()`); a set
  out point maps to `start_time=start, duration=end-start`. The class is
  reached through the `_video_from_file_class` seam — the pack's ONLY
  ComfyUI-internal class dependency; everything else duck-types — so tests
  stub it and a pre-VIDEO ComfyUI gets a clear "update ComfyUI" error.
- `segments` is the reuse that matters: **one** segment in §6.2's frozen dict
  shape, so a clip lifted off the Premiere timeline wires straight into the
  already-shipped `Get Premiere Segment`, `Get Premiere Segment Frame` and
  `Iterate Premiere Segments` nodes with no new consumer code (and through
  `Get Premiere Segment` into VHS's
  `Load Video (Path)`, exactly as a loaded timeline's shots already do). The
  plain `path` / `start_seconds` / `end_seconds` outputs exist for
  seconds-based loaders that want a path and a range directly.
- Building that segment dict — §6.2's keys VERBATIM (`name, path, start, end,
  in, out, sequence_fps, source_fps, enabled, width, height`). The panel
  sends a path and two times, so the rest comes from probing the file with
  `cprb.probe.probe_media`, the same probe `Save Premiere Timeline` uses:

  | key | value |
  |---|---|
  | `name` | the media file's STEM. §11.2's `label` (Premiere's own clip name, possibly a timeline rename) is deliberately NOT a widget here, so the stem is the honest answer |
  | `path` | the resolved media file |
  | `in` | `round(start_seconds * source_fps)` — SOURCE frames, §6.1's meaning |
  | `out` | `round(end_seconds * source_fps)` — source frames, `end_seconds` EXCLUSIVE, which is what makes `Get Premiere Segment`'s `frame_count = out - in` come out right |
  | `start` / `end` | `-1` — UNRESOLVED. §6.1's `start`/`end` are TIMELINE frames and a clip selection carries no timeline position; `-1` is the same "no value" marker §6.1's parser already yields for a missing `<start>`, and no §6.3/§6.4/§6.5 consumer reads either key |
  | `source_fps` | probed native rate (`MediaInfo.fps`) |
  | `sequence_fps` | DEFAULTED to the same value as `source_fps` |
  | `enabled` | `True` — the user explicitly selected this clip |
  | `width` / `height` | probed native pixels |

  `sequence_fps` is a default, not a fact: the sequence rate is not
  transported (it is not in §11.2's payload and inventing one would be a
  fiction), and every §6.3 consumer derives its outputs from `source_fps` —
  never `sequence_fps` — so the substitution cannot skew a downstream number.
- `execute` ALWAYS logs the derived numbers at INFO — `in`, `out`,
  `frame_count`, `source_fps`, the effective source span — not just when
  something is defaulted. Whether Premiere's `getOutPoint()` is inclusive or
  exclusive is undocumented and this node commits to EXCLUSIVE; that line is
  what makes one live run settle it against Premiere's own clip-duration
  readout, instead of an off-by-one frame surfacing invisibly downstream in
  `Get Premiere Segment`'s `frame_count`.
- Defaults and clamps, each one **stated out loud** in the server log rather
  than applied silently (this node has no UI summary; its segment list IS the
  payload): a negative `start_seconds` clamps to 0; `end_seconds <= 0` means
  **whole file** and becomes the media's full probed duration; a span shorter
  than one frame emits a single frame (`out = in + 1`) rather than a
  `frame_count` of 0 that would load nothing.
- `VALIDATE_INPUTS`: empty `path` ⇒ name the BUTTON to press; missing file ⇒
  named, with "the clip may be offline in Premiere, or its media may live on
  a drive this machine cannot see"; an out point at or before the in point
  (`0 < end_seconds <= start_seconds`) ⇒ named with both values. It
  deliberately does NOT probe — opening the file with ffmpeg once per queue
  for a check `execute` already makes is the same trade
  `PremiereLoadTimeline.VALIDATE_INPUTS` declines for XML parsing. A
  `path` of `None` passes through, as in §11.4. An unprobeable file is a hard
  `ValueError` from `execute` naming the file: without a rate and a
  resolution there is no honest §6.2 shot to emit.
- `IS_CHANGED` → the media file's mtime/size only. The two seconds widgets
  are accepted but NOT folded in, for the reason
  `PremiereLoadTimeline.IS_CHANGED` leaves `skip_disabled` out: ComfyUI
  already re-runs a node whose literal widget value changed, so this token
  covers only what it cannot see — the FILE changing under an unchanged path.
- Known limitations, accepted for M2 and stated here rather than discovered:
  a **retimed** clip (speed ≠ 100%) or a **reversed** clip arrives as a
  plain forward source range — the payload carries no speed or direction.
  **Merged / multicam** clips have several sources behind one project item
  and `getMediaFilePath()` returns one of them; the panel warns in its log
  rather than pretending otherwise.

### §11.6 Panel: the "SEND TO COMFYUI" section

A new section in `premiere_plugin/index.html`, below M1's status and log,
with two buttons — **"Frame → ComfyUI"** and **"Clip → ComfyUI"** — each an
`sp-button` with an explicit `variant=` and NOT `cta` (Connect owns the
panel's single `cta`, per the plugin conventions).

- **Both stay clickable at all times; preconditions are reported, not
  greyed out.** Deliberate, and the opposite of disabling: a dead button
  explains nothing, while a click that answers "press Connect first" or
  "the server never sent `frames_dir` — refusing to invent an output path"
  tells the user exactly what to do. Every precondition is checked in order
  on click — connection, `frames_dir` (frame only), active sequence,
  playhead, frame size — and each failure is its own named `bad(...)` line.
- **The two buttons are serialized against themselves and each other** by a
  single busy flag: overlapping exports make Premiere race with its own
  previous write (§11.7), so a double-click logs "another send is still
  running — ignored" instead of producing two half-exports.
- Every failure is a named line in the panel log through the existing
  `log`/`bad`/`describeError`/`fail` helpers — never a silent no-op, and
  never a throw out of the click handler. The cases that must be named: no
  project, no active sequence, an unreadable playhead or frame size (a
  guessed width/height silently produces a stretched frame — abort instead),
  no clip selected, and a clip whose `getMediaFilePath()` is EMPTY (nested
  sequences, titles, colour mattes, adjustment layers, offline media —
  SPIKES S6-C measured 11 of 19 enumerated `ClipProjectItem`s with an empty
  path, so this is the likely real-world failure, not a theoretical one). An
  empty `path` must never go out on the wire.
- The panel says WHICH route produced the clip (timeline selection, or a
  per-item `getIsSelected()` sweep) so a surprising result is diagnosable.
  There is deliberately **NO playhead or project-panel fallback** — both
  would guess at what the user meant; with nothing selected the panel says
  what to select instead.
- **An empty selection is SELF-DIAGNOSING, not just an instruction.** It
  reports what each route actually saw — how many items `getSelection()`
  returned, and how many tracks/clips the sweep walked with how many able to
  answer `getIsSelected()` — and then invites the log back if a clip IS
  selected. "Nothing is selected" while a clip is visibly selected is the
  single most likely M2 failure (`getIsSelected()` has no Adobe sample, and
  whether Timeline selection survives focus moving into a UXP panel is
  undocumented), so it must not produce an empty log.
- Premiere 26.3 exposes no selection-changed event, so the selection is read
  at click time.
- **No spike buttons live in this panel.** S7's button in particular was
  removed once M2 shipped rather than left as a curiosity: its ladder called
  `manager.exportSequence(...)` and stopped at the first shape that did not
  throw, so on a build where any of them is valid a misclick beside these two
  buttons would start a whole-sequence render or an AME queue — and it never
  took the busy flag, so it could interleave with a real export. Its
  read-only successor is the free `typeof`/arity probe of
  `exportSequenceFrame`, logged on EVERY "Frame → ComfyUI" click before any
  read that can fail (so a playhead or frame-size failure cannot suppress the
  one line that diagnoses the export API).

### §11.7 Premiere-side call shapes (the plugin's half)

Recorded here because M2's code cites `PROTOCOL.md §11` at these call sites,
and because both surfaces have documented traps. Spike history and sourcing:
SPIKES.md S7.

**Frame export.** Confirmed signature (Adobe's own `premierepro-types`
`ExporterStatic` plus the 26.3 class reference), and PROVEN live on 26.3.0
(SPIKES S7-b, 2026-07-26):

```
pr.Exporter.exportSequenceFrame(sequence, time, filename, filepath, width, height)
  -> Promise<boolean>
```

- **`Function.length` is NOT a usable check.** The documented parameter count
  is 6, but the live binding reports `exportSequenceFrame.length === 0` while
  exporting perfectly — native UXP bindings do not publish an arity. An
  earlier version of this section claimed `=== 6` was "a free runtime check";
  it would have rejected a working build. Only `typeof === 'function'` means
  anything, and the panel's probe says so in its own log line.

- `filename` is a **BASENAME ONLY**; `filepath` is a **DIRECTORY** with no
  trailing separator. The official parameter table's `filename` example is a
  full path and is WRONG — a full path there makes Premiere write to a bad
  combined path and silently produce nothing. Split the target.
- `width`/`height` come from `await sequence.getFrameSize()` → `RectF
  {width, height}` (floats — round them). They do NOT preserve aspect ratio;
  pass a matching pair. A total read failure ABORTS the export (a guessed
  size silently yields a stretched frame) and dumps `Sequence`'s own and
  prototype property names on the way out, so the real method name is one
  click away rather than a dead end.
- `.png` — the extension in `filename` selects the format.
- **This is a READ.** No `lockedAccess`, no `executeTransaction`: §10's
  every-mutation-is-wrapped rule does not apply to it.
- **A `true` return is not proof the file exists.** It can return before the
  write completes, and under rapid back-to-back exports it can return `true`
  and never write at all. Therefore: a **UNIQUE filename per export**
  (reusing a name races with Premiere still writing the previous one), the
  whole export retried a few times with a fresh name, and the existence
  check done SERVER-SIDE — first at the §11.2 relay (which warns immediately)
  and again in §11.4's `VALIDATE_INPUTS` at queue time.
- No colons in the filename (illegal on Windows ⇒ the call simply returns
  `false`), so frames are never named by timecode.
- The double-extension defect (`abcd.jpg` → `abcd.jpg.jpg`) was fixed in
  26.2.2, one patch release before the owner's 26.3.0. The panel sends
  exactly ONE `path` — the exact name it passed to `exportSequenceFrame`; the
  SERVER resolves the doubled name when that one is missing (§11.2's
  `resolved_path`, `nodes_source.resolve_frame_path`).
- Pass the Sequence straight from `project.getActiveSequence()` — never a
  cast or re-wrapped object (§10's "Invalid parameter." lesson).

**Clip read.** `sequence.getSelection()` → `.getTrackItems()` →
`item.getProjectItem()` (RAW) → `pr.ClipProjectItem.cast(raw)` →
`.getMediaFilePath()`, plus `item.getInPoint()` / `item.getOutPoint()`.

- The cast here is MANDATORY, and is the exact MIRROR of §10's lesson:
  `ProjectItem` has no `getMediaFilePath`, so reading media facts needs the
  `ClipProjectItem` wrapper, while anything passing a project item as an
  ARGUMENT into another object's factory needs the RAW item. Keep both
  handles.
- `getInPoint()`/`getOutPoint()` are SOURCE-media times — the ones we want.
  `getStartTime()`/`getEndTime()` are TIMELINE times — the ones we do not.
  Both return `TickTime`, so `.seconds` is the wire value directly.
- `end_seconds` is treated as **EXCLUSIVE** (out = one frame past the last
  frame). Decided here so §11.5's `out` and any downstream trim agree; the
  26.3 reference never states it either way.
- A linked A/V click can return both the video and the audio track item, in
  an undocumented order, and neither track-item class has a static `.cast()`
  — the video one is identified by the presence of
  `createAddVideoTransitionAction`.
- Also a READ path: Adobe's own samples read the selection outside any lock.
