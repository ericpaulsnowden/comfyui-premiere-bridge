# PROTOCOL.md — the binding contract for comfyui-premiere-bridge

This document is BINDING, in the comfyui-photoshop-bridge sense: the backend
(`cprb/`), the frontend (`web/`), the on-disk outputs, and (later) the UXP
panel must all match what is written here. Any interface change amends this
file FIRST, in the same commit as the code. Cite sections in code comments
as `PROTOCOL.md §N`.

Contents: §1 scope & tiers · §2 output conventions · §3 Save Premiere
Timeline · §4 emitted FCP7 XML (xmeml) · §5 emitted EDL · §6 Load Premiere
Timeline & Get Shot · §7 routes & frontend · §8 versioning & stability ·
§9 spikes · §10 Tier 2 plugin websocket.
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
round-trip work (a shot list re-emitted as a timeline keeps its in/out).

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
- Outputs: `shots` (custom type `CPRB_SHOT_LIST`), `count` (INT),
  `summary` (STRING — one line per shot: index, name, source path, in/out
  timecode) — wire `summary` into a Show Text node for a free shot sheet.
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
  from `shots`/`count`.
- Tolerant parser: missing optional metadata never fails; a file with zero
  video clipitems errors loudly (wrong file, not an empty result).
- `IS_CHANGED` → file mtime/size so a re-export re-runs.

### §6.2 `CPRB_SHOT_LIST`

A plain python `list[dict]` with the §6.1 keys. Custom-typed so it can only
wire into cprb consumers; contents are documented here and FROZEN.

### §6.3 `PremiereGetShot` (display: "Get Shot")

- Inputs: `shots` (CPRB_SHOT_LIST); widget `index` (INT, 0-based, default
  0; out of range ⇒ clear error naming the valid range).
- Outputs, in this order (owner reorder 2026-07-19 — the "seconds" pair and
  the "frame" pair each swapped so the load-cap value leads its partner,
  matching how they wire into VHS): `path` (STRING), `duration_seconds`
  (FLOAT), `in_seconds` (FLOAT — source in ÷ source_fps), `frame_count`
  (INT), `in_frame` (INT), `fps` (FLOAT — source fps), `name` (STRING),
  `width` (INT), `height` (INT). ⚠ Reordering outputs shifts socket
  indices, so a workflow saved before this change re-wires by position on
  load — acceptable pre-release; re-check any existing Get Shot wiring once.
- The frame outputs feed VHS `Load Video (Path)`'s `frame_load_cap`
  (`frame_count`) / `skip_first_frames` (`in_frame`) directly; the seconds
  outputs suit core loaders; `width`/`height` feed resize/crop or a Create
  Video (ethos §1: existing nodes do the reading). The frame outputs are
  counted in `source_fps` (§6.1's three-tier rate); for a clip Premiere
  conformed to a different rate, prefer the SECONDS outputs, always
  real-time-correct.

### §6.4 `PremiereIterateShots` (display: "Iterate Shots")

The answer to "how do I process every shot" (owner ask 2026-07-19) — ComfyUI
has no for-loop, so this fans out via list execution, exactly like EPSNodes'
multi-select notebook.

- Input: `shots` (CPRB_SHOT_LIST); widget `skip_disabled` is unnecessary
  (Load already filtered) — none.
- Outputs mirror Get Shot's set (`path, duration_seconds, in_seconds,
  frame_count, in_frame, fps, name, width, height`) but ALL declared
  `OUTPUT_IS_LIST` — one element per shot, in shot order. ComfyUI then runs
  every downstream node once per shot from a SINGLE queue: wire `path`+the
  frame outputs into VHS `Load Video (Path)` and one Run processes the
  whole edit shot by shot. An empty shot list yields empty lists (downstream
  simply doesn't run) — not an error.

### §6.5 `PremiereShotFrame` (display: "Get Shot Frame")

Optional preview thumbnail (owner ask 2026-07-19 "can we pull a preview
frame… if easy/reliable"). SEPARATE node so the decode cost/failure never
touches Get Shot's cheap metadata path.

- Inputs: `shots` (CPRB_SHOT_LIST); widget `index` (INT, like Get Shot).
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

## §8 Versioning & stability

- `cprb/version.py` (source of truth) + `pyproject.toml` +
  `web/cprb/version.js`, lockstepped by `scripts/bump_version.py`; every
  push bumps ≥ patch and is tagged `vX.Y.Z`; docs-only changes don't bump.
- FROZEN once shipped: node class ids, route paths, §4/§5 file semantics,
  §6.2 shot-dict keys. New fields may be added; existing ones never change
  meaning.

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
version. `OUTPUT_NODE = True`; no `IS_CHANGED` override.

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
that it didn't work"). `web/cprb/send_result.js` turns this event into a
toast — a short info one on success, and on failure a long-lived WARNING
carrying the full path, because the user's next action is to import that
file by hand. Failure to emit or render is swallowed: a UI notification
never fails a finished run.
