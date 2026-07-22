# comfyui-premiere-bridge

Move video between ComfyUI and Adobe Premiere Pro with plain files — no
Adobe-side install required.

- **Save Premiere Timeline** — wire generated or processed shots (VIDEO
  inputs and/or file paths) into one node and get a folder containing the
  media plus a **Premiere-importable timeline** (FCP7 XML, optionally EDL /
  OTIO). In Premiere: `File > Import`, and the assembled sequence appears
  with media already linked.
- **Load Premiere Timeline + Get Shot** — export your edit from Premiere
  (`File > Export > Final Cut Pro XML`) and read it back in ComfyUI as a
  shot list: every cut's source path, in/out points, and fps, ready to feed
  per-shot processing ("restyle my whole edit") through the video loaders
  you already use.

A future tier adds a Premiere UXP panel (a sibling of
[comfyui-photoshop-bridge](https://github.com/ericpaulsnowden/comfyui-photoshop-bridge)'s
plugin) for one-click round trips — the file-based workflow above is the
floor that always works, and it never depends on the panel.

> **Status: pre-release, shipping feature by feature.** Available today:
> **Save Premiere Timeline** and **Load Premiere Timeline + Get Shot**
> (both below). Contracts are specified in
> [docs/PROTOCOL.md](docs/PROTOCOL.md); Premiere-facing claims stay flagged
> until the [docs/SPIKES.md](docs/SPIKES.md) live imports pass. This README
> describes each capability only once it actually ships.

## Save Premiere Timeline (shipped; Premiere import verified — SPIKES S1 passed 2026-07-19)

Wire in as many VIDEO inputs as you like (a new `video_N` socket appears
each time you connect the last one — like the image-batch nodes) and/or
paste absolute file paths (one per line), pick a sequence rate (23.976–60,
drop-frame aware), and the node writes everything under
`output/premiere_timelines/<sequence name>/`:

- `<name>.xml` — an FCP7 XML timeline with your clips back-to-back on V1,
  referencing media by absolute path so a same-machine `File > Import` in
  Premiere links without a relink dialog.
- `media/` — connected VIDEO inputs are materialized here (mp4); `paths`
  entries are referenced **in place** (default) or copied in, per the
  `media` widget (*Link in place* vs *Collect into folder*).
- `<name>.edl` (optional) — CMX3600 fallback with `* SOURCE FILE:` path
  comments.
- `<name>.otio` (optional) — written when `opentimelineio` is installed;
  skipped with a warning otherwise (soft dependency).

Output is deterministic (re-running the same name overwrites in place) and
the node returns the `.xml` path as a STRING plus a written-files summary.
By default it writes under ComfyUI's output folder, but the optional
`output_dir` widget can redirect it anywhere — **Browse…** picks a folder
(the timeline still gets its own `<sequence_name>` subfolder inside it), so
timelines can land on a project or NAS folder. **Open folder** on the node
jumps straight to the effective folder in Explorer/Finder — the next step is
importing it into Premiere. (A non-absolute `output_dir` is ignored with a
note, never an error, and falls back to the default.)

## Load Premiere Timeline + Get Shot (shipped)

Export your edit from Premiere (`File > Export > Final Cut Pro XML`), point
**Load Premiere Timeline** at the `.xml` — click **Browse…** to pick it
from a folder browser instead of pasting a path (**Open folder** reveals it
on disk) — and you get:

- `shots` — the edit as a shot list (every video clip's source path, in/out
  points, rate, enabled state), in timeline order across all video tracks.
- `count` + `summary` — one readable line per shot; wire `summary` into any
  text-preview node for a free shot sheet.

**Get Shot** pulls one shot by index: `path`, `in_seconds` /
`duration_seconds` for time-based loaders, `in_frame` / `frame_count` for
VideoHelperSuite's `Load Video (Path)` (`skip_first_frames` /
`frame_load_cap`), plus `fps` and `name`. That's the "restyle my whole
edit" building block: parse once, process each shot through any video
workflow, using the loaders you already have.

The parser handles real Premiere export noise — `pproTicks*` attributes,
labels/filters, audio tracks, disabled clips (`skip_disabled` widget),
file-by-id references, nested/compound clips (excluded rather than leaked),
generator clips without media, and the `-1` boundaries Premiere writes
around transitions.

## Tier-2 UXP panel (in progress — spike round shipped)

A panel that runs **inside Premiere** and talks to ComfyUI live — no
File ▸ Import/Export step — is in development (roadmap:
`research/roadmap-premiere-tier2.md` in the planning repo; spike
definitions in [docs/SPIKES.md](docs/SPIKES.md) §S6). What ships today is
the **M0 spike panel** at [`premiere_plugin/`](premiere_plugin/): five
buttons that answer the roadmap's gating unknowns (cleartext `ws://` to a
local ComfyUI, the 26.3-safe action pattern, import+find, the frame-export
surface, API ground truth) with a copyable log. Dev-install it via Adobe's
**UXP Developer Tool**: Add Plugin → pick `premiere_plugin/manifest.json` →
Load (Premiere ≥ 25.6 with Developer Mode enabled). The XML nodes above
remain the fully supported, panel-free path — the panel will only ever be
the *better* version of flows that already work without it.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ericpaulsnowden/comfyui-premiere-bridge
```

Restart ComfyUI. No pip requirements (PyAV comes with ComfyUI; installing
`opentimelineio` is optional and only enables `.otio` output).

## Versioning

Backend and frontend versions are shown in **Settings → Premiere Bridge**;
a mismatch means you pulled an update but haven't restarted the server (or
need a hard refresh). Every push bumps the version and is tagged.

## License

MIT — see [LICENSE](LICENSE).
