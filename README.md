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

> **Status: pre-release scaffolding.** Contracts are specified in
> [docs/PROTOCOL.md](docs/PROTOCOL.md); nodes are landing feature by
> feature, and Premiere-facing claims stay flagged until the
> [docs/SPIKES.md](docs/SPIKES.md) live imports pass. This README describes
> each capability only once it actually ships.

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
