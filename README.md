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
> **Load Premiere Timeline + Get Shot** (below). Save Premiere Timeline is
> in final review. Contracts are specified in
> [docs/PROTOCOL.md](docs/PROTOCOL.md); Premiere-facing claims stay flagged
> until the [docs/SPIKES.md](docs/SPIKES.md) live imports pass. This README
> describes each capability only once it actually ships.

## Load Premiere Timeline + Get Shot (shipped)

Export your edit from Premiere (`File > Export > Final Cut Pro XML`), point
**Load Premiere Timeline** at the `.xml`, and you get:

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
