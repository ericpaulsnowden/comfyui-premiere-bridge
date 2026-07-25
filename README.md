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

A second tier adds a Premiere UXP panel (a sibling of
[comfyui-photoshop-bridge](https://github.com/ericpaulsnowden/comfyui-photoshop-bridge)'s
plugin) for one-click round trips in both directions — results land in a
Premiere bin, and a frame or clip comes back out to ComfyUI. The file-based
workflow above is the floor that always works, and it never depends on the
panel.

> **Status: pre-release, shipping feature by feature.** Available today:
> **Save Premiere Timeline**, **Load Premiere Timeline + Get Shot**,
> **Send to Premiere**, **Frame → ComfyUI / Clip → ComfyUI**, and the
> **ComfyUI Bridge panel** (all below). Contracts are specified in
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

## Send to Premiere (shipped — Tier-2 M1)

`Premiere Bridge → Send to Premiere`: wire a **VIDEO** output (a WAN/I2V
generation, a VHS combine — audio comes along) and/or an **IMAGE** output
into it. Every run, the result lands in Premiere's project panel in a
**"ComfyUI Results" bin** — created the first time, reused forever after —
with no export step and no File ▸ Import.

- **Videos are the headline.** An existing video file is **linked in
  place** (no copying multi-GB files around); only in-memory video, trimmed
  inputs, or files sitting in ComfyUI's temp folder get written out first
  (into `output/premiere_results/`, collision-free names, audio preserved).
- **Never blocks, never fails on Premiere's absence:** with no panel
  connected the node still writes/reports the file path — the summary says
  exactly what to import manually. The plugin is the better version, never
  the only version.
- `label` names the clip (empty keeps the filename); `bin_name` picks the
  bin; `color_label` gives a run's results their own label colour in the bin.
- **`insert_at_playhead` now works** (proven live on Premiere 26.3). Off by
  default, so results only land in the bin. Switch it on and the clip is also
  dropped onto the active sequence at your playhead, on the track above, as
  **one** undo step — and it's skipped with a logged line, never a guess, if
  no sequence is open.

## Frame → ComfyUI / Clip → ComfyUI (shipped — Tier-2 M2; live Premiere check pending, SPIKES S7-b/S7-c)

The return direction. Two buttons in the panel's **SEND TO COMFYUI** section
hand Premiere's own content to your graph:

- **Frame → ComfyUI** exports the still at your playhead and drops its path
  into a **Frame from Premiere** node — which gives you the `image` plus its
  `width`, `height` and `path`, ready for any image workflow.
- **Clip → ComfyUI** exports nothing at all. It reads the selected timeline
  clip's *own* media file and its in/out points, so a **Clip from Premiere**
  node hands you the original file and the exact range — instant even for
  multi-GB media, and no re-encode ever touches your footage.

**Clip from Premiere** emits a one-shot `shots` list in the same shape
**Load Premiere Timeline** produces, so it plugs straight into the **Get
Shot**, **Get Shot Frame** and **Iterate Shots** nodes you already have —
plus plain `path` / `start_seconds` / `end_seconds` outputs for
VideoHelperSuite-style loaders.

Two deliberate behaviours. Clicking a button updates **every** matching source
node in the open graph (and warns you when there are none, rather than
succeeding into thin air). And it **never queues a run** — you press Run when
you're ready, so exporting a frame just to look at it costs nothing.

Together with **Send to Premiere** above, that's the whole loop: frame out,
work, result back in the bin. **[`examples/premiere_roundtrip.json`](examples/premiere_roundtrip.json)**
is that loop in four nodes, using nothing but this pack and one core ComfyUI
node — the inverted colours make a successful round trip obvious at a glance.
See [examples/README.md](examples/README.md) for what to click, in order.

Same-machine, like the rest of the panel: the two sides exchange file paths,
not file bytes.

**Where the frames go.** Exported stills land in
`<ComfyUI input folder>/premiere_frames/`, so they're already somewhere
ComfyUI can read (and `LoadImage` can browse) with nothing copied. Only the
**200 newest** are kept — every click writes a new full-resolution PNG, so
without that the folder would grow forever. If Premiere can't write there —
an input folder on a NAS share is the case to watch — set the environment
variable **`CPRB_FRAMES_DIR`** to any absolute local path before starting
ComfyUI and frames go there instead. ComfyUI's console prints the folder it's
actually using each time the panel connects.

Two failures worth knowing about, because both are handled rather than
guessed at: Premiere's frame export can report success and write nothing, so
**ComfyUI checks the file itself** and warns you immediately instead of at
Run; and **Clip → ComfyUI** never invents a selection — with nothing selected
it tells you what it looked at and what to select.

## ComfyUI Bridge panel (shipped — runs inside Premiere)

The UXP panel at [`premiere_plugin/`](premiere_plugin/) is a real status
window, built to match the Photoshop bridge's panel conventions:

- **A status pill** — connected / connecting / disconnected / standing by —
  and **one Connect/Disconnect button**. It finds ComfyUI at
  `localhost:8188` by itself and retries with a calm "retrying in Ns"
  countdown; **Disconnect really disconnects** (it parks the retry loop
  until you press Connect, rather than yanking you back online a second
  later).
- **An activity log** showing each import as it lands — this is where you
  watch results arrive.
- **An Advanced section** with the plugin/server versions (amber if they
  ever disagree — that means one side is stale), the server address field
  for a different host/port, and the live panel size.

- **A SEND TO COMFYUI section** with the **Frame → ComfyUI** and
  **Clip → ComfyUI** buttons described above — the panel's half of the
  return direction.

It performs the actual Premiere-side work for **Send to Premiere**:
find-or-create the bin, import, tag, colour-label, and — when you enable
the toggle — drop the clip at your playhead. One panel per server: open a
second and the first stands by rather than fighting over the connection.

Dev-install via Adobe's **UXP Developer Tool**: Add Plugin → pick
`premiere_plugin/manifest.json` → Load (**Premiere ≥ 26.3** with Developer
Mode enabled). Every Premiere-facing claim here is tracked in
[docs/SPIKES.md](docs/SPIKES.md) until a live session proves it. The XML
nodes above remain the fully supported, panel-free path.

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
