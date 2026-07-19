# PROTOCOL.md — the binding contract for comfyui-premiere-bridge

This document is BINDING, in the comfyui-photoshop-bridge sense: the backend
(`cprb/`), the frontend (`web/`), the on-disk outputs, and (later) the UXP
panel must all match what is written here. Any interface change amends this
file FIRST, in the same commit as the code. Cite sections in code comments
as `PROTOCOL.md §N`.

Contents: §1 scope & tiers · §2 output conventions · §3 Save Premiere
Timeline · §4 emitted FCP7 XML (xmeml) · §5 emitted EDL · §6 Load Premiere
Timeline & Get Shot · §7 routes & frontend · §8 versioning & stability ·
§9 spikes.

---

## §1 Scope & tiers

Per the product ethos (existing nodes first; ComfyUI-only must work; the
Adobe-side plugin is a better version, never the only version):

- **Tier 1 (this document, shipping now):** pure ComfyUI nodes + file
  interchange. Media in via existing loaders (core LoadVideo, VHS); media
  out via existing savers; cprb adds ONLY what the ecosystem lacks — writing
  a Premiere-importable timeline, and reading a Premiere-exported one.
  The user's gestures are Premiere's own `File > Import` / `File > Export`.
- **Tier 2 (future §, not yet specified):** a Premiere UXP panel (websocket
  client of ComfyUI's server, sibling of the Photoshop plugin). Gated on
  the SPIKES.md live-Premiere spikes. Nothing in Tier 1 may depend on it.

## §2 Output conventions

Everything Save Premiere Timeline writes lands under ComfyUI's normal
output tree:

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

## §3 `PremiereSaveTimeline` (display: "Save Premiere Timeline")

### §3.1 Inputs

- `video_1` … `video_4` (VIDEO, all optional) — core ComfyUI VIDEO objects.
- `paths` (STRING, multiline, optional) — additional media, one absolute
  path per line (blank lines and `#`-prefixed lines ignored). These are
  referenced IN PLACE (never copied); they must exist and be probeable at
  execution time or the run errors naming the offending line.
- Clip order on the timeline: `video_1..4` first, then `paths` lines, top
  to bottom, back-to-back from 00:00:00:00 on video track 1.

### §3.2 Widgets

- `sequence_name` (STRING, default `"ComfyUI Timeline"`).
- `fps` (COMBO of strings: `23.976, 24, 25, 29.97, 30, 50, 59.94, 60`;
  default `24`) — the SEQUENCE rate; §4.2 maps it to timebase+ntsc.
- `write_edl` (BOOLEAN, default False).
- `write_otio` (BOOLEAN, default False; missing `opentimelineio` ⇒ warning
  in the node result, not a failure — soft dependency).

### §3.3 Behavior

- VIDEO inputs are materialized into `media/` via the VIDEO object's own
  `save_to(...)` (h264/mp4, ComfyUI's default encode path) as
  `NNN_<sanitized name>.mp4`. v1 deliberately does not re-encode `paths`
  entries (zero-generation-loss reference in place). ProRes materialization
  is a follow-up pending SPIKES §S3.
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
  `enabled`.
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
- Outputs: `path` (STRING), `in_seconds` (FLOAT — source in ÷ source_fps),
  `duration_seconds` (FLOAT), `in_frame` (INT), `frame_count` (INT),
  `fps` (FLOAT — source fps), `name` (STRING).
- The frame outputs feed VHS `Load Video (Path)`'s `skip_first_frames` /
  `frame_load_cap` directly; the seconds outputs suit core loaders —
  existing nodes do the actual media reading (ethos §1). Note: the frame
  outputs are counted in `source_fps` (§6.1's three-tier rate — the rate
  the edit expressed them in). For normal footage that equals the file's
  native rate; for a clip Premiere conformed to a different rate, prefer
  the SECONDS outputs, which are always real-time-correct.

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
| `GET /cprb/fs/list?dir=&ext=` | **loopback-only.** Server-filesystem browser. Empty/missing `dir` ⇒ `output_dir`. `ext` = a comma-separated extension allowlist (default `.xml`; always case-insensitive). → `{"dir": <abs>, "parent": <abs or null>, "dirs": [names], "files": [names]}`, entries sorted case-insensitively; non-absolute `dir` ⇒ 400; unreadable ⇒ 400 |
| `POST /cprb/open_folder` `{"path"}` | **loopback-only.** Reveals *path* in the OS file manager ON THE SERVER MACHINE (Explorer/Finder): a file reveals its parent folder, a directory reveals itself. Missing ⇒ 404; spawn failure ⇒ 500; `{"ok": true}` |
| `GET /cprb/timeline_dir?sequence_name=` | `{"dir": <abs>, "exists": bool}` — the §2 output folder this `sequence_name` resolves to, computed server-side so the frontend never re-implements `sanitize_name` |

**§7.3 Frontend.** `web/cprb.js`: one
`app.registerExtension('cprb.PremiereBridge')` with the About-panel badge
and the "Premiere Bridge" settings section showing backend+frontend
versions (mismatch = pulled-but-not-restarted; cpsb pattern), plus:

- **Load Premiere Timeline** gains a file bar under its widgets: `Browse…`
  opens a picker dialog over §7.2 `fs/list` (navigate folders, `..` row,
  `.xml` files only; picking one writes the `file_path` widget through its
  real setter) and `Open folder` reveals the selected file's folder. Both
  buttons are HIDDEN when `config.is_local` is false (§7.1).
- **Save Premiere Timeline** gains one `Open output folder` button that
  resolves its `sequence_name` through §7.2 `timeline_dir` and reveals
  that folder — the natural next step after a run (go import it into
  Premiere). Before the first run the folder may not exist yet: the button
  says so rather than erroring. Hidden when not local, same as above.
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
