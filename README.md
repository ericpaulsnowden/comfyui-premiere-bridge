# comfyui-premiere-bridge

Move video, stills and edits between ComfyUI and Adobe Premiere Pro — on the
same machine, by exchanging file paths rather than copying bytes around.

Eight nodes under the **Premiere Bridge** category, plus one optional panel that
runs inside Premiere.

---

## Do I need to install anything in Premiere?

**For five of the eight nodes: no — nothing at all.** They read and write files,
and you move those files with menus Premiere already has (`File > Import`,
`File > Export > Final Cut Pro XML`). That half is the floor, and it always
works.

The other three are faster with the **ComfyUI Bridge panel** — an optional
plugin you load into Premiere yourself ([install below](#2-the-comfyui-bridge-panel--optional)).
It removes the manual export/import step. Three honest levels:

| Node | What it does | Panel |
|---|---|---|
| [**Save Premiere Timeline**](#save-premiere-timeline) | Your segments → a Premiere-importable sequence | not needed |
| [**Load Premiere Timeline**](#load-premiere-timeline) | Your Premiere edit → a segment list | not needed |
| [**Get Premiere Segment**](#get-premiere-segment) | One segment from the list, as paths + frames + seconds | not needed |
| [**Iterate Premiere Segments**](#iterate-premiere-segments) | *Every* segment at once, so one Run does the whole edit | not needed |
| [**Get Premiere Segment Frame**](#get-premiere-segment-frame) | A preview frame from one segment | not needed |
| [**Send to Premiere**](#send-to-premiere) | Your result → straight into a Premiere bin | **optional** |
| [**Frame from Premiere**](#frame-from-premiere) | The still at Premiere's playhead → your graph | **needed in practice** |
| [**Clip from Premiere**](#clip-from-premiere) | The clip you selected in Premiere → your graph, as a ready-to-wire VIDEO | **needed in practice** |

Those three levels map onto the two node-menu buckets: *not needed* and
*optional* both live under **Handoffs**, because both run to completion without
Premiere; *needed in practice* is **Handoffs (requires Premiere)**.

**What the three levels mean:**

- **not needed** — the node never talks to Premiere. Nothing is installed, and
  nothing is different with or without the panel.
- **optional** — the node does its whole job either way. **Send to Premiere**
  always resolves your result to a real file and reports the path; the panel only
  adds the automatic import into a bin. With no panel you import that file
  yourself, and the run still succeeds. (Its three Premiere-side options —
  `bin_name`, `color_label`, `insert_at_playhead` — are the panel's work, so they
  do nothing without it.)
- **needed in practice** — the panel is the *only* thing that produces this
  node's input. The node will happily read a path you type by hand, but then
  you're exporting from Premiere manually, and ComfyUI's own `LoadImage` would
  serve just as well. Treat these two as panel features.

So: **the file-based half installs nothing into Premiere. The one-click half is
the panel, and it is genuinely optional — you lose convenience, not capability,
except for the last two nodes, which are the convenience.**

### The route with nothing installed in Premiere

1. **Into ComfyUI:** in Premiere, `File > Export > Final Cut Pro XML`. Point
   **Load Premiere Timeline** at that `.xml` and you have your whole edit as a
   segment list. **Iterate Premiere Segments** then fans your graph out over every cut, so one
   Run restyles the entire edit; **Get Premiere Segment** and **Get Premiere Segment Frame** handle one
   cut at a time.
2. **Back into Premiere:** wire your results into **Save Premiere Timeline**. It
   writes a folder containing the media plus an importable FCP7 XML timeline. In
   Premiere, `File > Import` that `.xml` and the assembled sequence appears with
   media already linked.

---

## Install

### 1. The node pack — required

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ericpaulsnowden/comfyui-premiere-bridge
```

Restart ComfyUI. You should see `cprb vX.Y.Z loaded (8 nodes)` in the server log
and a **Premiere Bridge** section in Settings.

**No pip install step.** The pack declares no dependencies of its own, and
everything it imports — aiohttp, PyAV, PyTorch, Pillow, NumPy — already ships
with ComfyUI.

One optional extra: `pip install opentimelineio` (into ComfyUI's Python) enables
`.otio` output on **Save Premiere Timeline**. Without it that one checkbox is
skipped with a note in the node's summary, never an error.

### 2. The ComfyUI Bridge panel — optional

**Skip this entirely unless you want Send to Premiere's automatic import, or the
Frame/Clip from Premiere nodes.** Everything else works without it.

Requires **Premiere 26.3 or newer**. It is a *developer* load, not a Marketplace
install:

1. In Premiere: **Preferences → Plugins → Enable Developer Mode** (restart
   Premiere if it was off).
2. Open Adobe's **UXP Developer Tool** → **Add Plugin** → select
   `custom_nodes/comfyui-premiere-bridge/premiere_plugin/manifest.json`.
3. Click **Load**. The panel appears, labelled **ComfyUI Bridge** (it's listed as
   *ComfyUI for Premiere* in Premiere's plugin list).

It finds ComfyUI at `localhost:8188` by itself; the ADVANCED section has a field
if yours is elsewhere. This pack's HTTP routes and WebSocket endpoint register
directly onto that same ComfyUI server, so exposing ComfyUI beyond localhost
exposes those too — the same no-authentication trust model as ComfyUI's own routes.

> **A UDT-loaded plugin does not survive a Premiere restart.** That's how
> developer loads work — reopen the UXP Developer Tool and press **Load** again
> after restarting Premiere.

Every Premiere-facing claim in this README is tracked in
[docs/SPIKES.md](docs/SPIKES.md) until a live session proves it.

---

## The nodes

The node menu splits them into **two buckets**, so you can see at a glance what
works for you:

- **Premiere Bridge › Handoffs** — runs with **no Premiere installed at all**.
  The five file-based nodes, plus **Send to Premiere** (it always writes the
  file and reports the path; only its bin/colour/playhead options need the
  panel).
- **Premiere Bridge › Handoffs (requires Premiere)** — **Frame from Premiere**
  and **Clip from Premiere**. These technically accept a hand-typed path, but
  the panel is the only thing that fills them in, so treat them as panel
  features.

The **bold name** below is what you search for in the node menu; the
`code name` beside it is the class id you'll see in a saved workflow's JSON and
in error messages.

Four of them speak a custom socket type, **`CPRB_SEGMENT_LIST`** — the segment list
that **Load Premiere Timeline** and **Clip from Premiere** produce, and that
**Get Premiere Segment**, **Iterate Premiere Segments** and **Get Premiere Segment Frame** consume. It only connects
to those sockets; a third-party node simply won't accept the wire.

### Save Premiere Timeline

> **Plugin: not needed.** · `PremiereSaveTimeline`

Assembles finished segments into a **Premiere-importable timeline**. Wire in as many
VIDEO outputs as you like — a new `video_N` socket appears each time you connect
the last one, like ComfyUI's image-batch nodes — and/or paste absolute file
paths, one per line. The node writes everything into one folder and hands back
the `.xml` path.

In Premiere: `File > Import` the `.xml`, and the sequence appears with your clips
back-to-back on V1 and media already linked (no relink dialog, because the XML
references media by absolute path).

This is an **output node**, so it can sit at the end of a graph with nothing
wired after it.

> **The imported sequence is video-only.** The timeline is written with an empty
> audio track, so your clips land on V1 with no audio on the timeline. The media
> files themselves keep their audio — so if you need it, drag the clip from the
> bin onto an audio track, or use [**Send to Premiere**](#send-to-premiere)
> instead, which imports the file whole.

**Inputs.**

| Input | Type | Default | Meaning |
|---|---|---|---|
| `sequence_name` | STRING | `ComfyUI Timeline` | Names the sequence, the output folder and the file stems. |
| `fps` | choice of 8 | `24` | The sequence rate: 23.976 / 24 / 25 / 29.97 / 30 / 50 / 59.94 / 60. Each clip keeps its own native rate. |
| `media` | choice | `Link in place` | *Link in place* references your `paths` files where they are; *Collect into folder* copies them in. |
| `paths` | STRING, multiline | empty | Extra media, one absolute path per line. |
| `write_edl` | BOOLEAN | off | Also write a CMX3600 `.edl`. |
| `write_otio` | BOOLEAN | off | Also write `.otio` (needs `opentimelineio`). |
| `video_1…` | VIDEO sockets | — | Grow on demand; always written into `media/`. |
| `output_dir` | STRING | empty | Optional absolute folder to write into instead of the default. |

**Outputs.** `timeline_path` (STRING) — the `.xml` that was written. The node
also shows a summary of every file it wrote.

**Where files land.** `output/premiere_timelines/<sequence_name>/` by default:

- `<name>.xml` — the FCP7 XML timeline (the file you import).
- `media/` — connected VIDEO inputs are written here as mp4, named after the
  **socket** they came from (`003_video_3.mp4`), not after the generation.
  `paths` entries are referenced in place by default, or copied in and numbered
  by their line if you pick *Collect into folder*.
- `<name>.edl` / `<name>.otio` — only when you tick those boxes.

The folder name and file stems are sanitized, so illegal characters in
`sequence_name` are replaced rather than failing. `output_dir` redirects all of
it anywhere (a project folder, a NAS share) and the timeline still gets its own
`<sequence_name>` subfolder inside; a path that isn't absolute is ignored with a
note rather than an error.

**The `paths` widget.** One path per line. Blank lines and `#` comments are
ignored, so you can annotate the list. Every entry is probed with ffmpeg — an
unreadable file is a clear error naming that file and its line number, not a
silently dropped clip.

**On-node buttons.** **Browse…** picks the output folder; **Open folder** reveals
the effective folder in your file manager (Explorer, Finder or `xdg-open`) —
exactly where you go next to import it. Both buttons hide themselves, leaving the
path typeable, when you're viewing ComfyUI from a *different* machine than the one
it runs on, since they'd be browsing the wrong filesystem.

**Gotchas.**

- **The sequence's resolution is taken from the first clip only.** Mixed-
  resolution inputs all land in a sequence sized like clip 1, so put your
  intended format first (or fix the sequence settings in Premiere after).
- Re-running the same `sequence_name` **overwrites in place** — deliberate, so
  iterating doesn't litter your disk. But it overwrites *per file*: a re-run with
  **fewer** clips leaves the previous run's extra `media/` files behind. Use a new
  name if you want a clean folder.
- For 29.97 and 59.94 the timeline is **declared** drop-frame (Premiere reads it
  as DF), but the timecodes written into the EDL use non-drop rollover math. It
  imports correctly; don't treat the EDL's timecode strings as broadcast-exact.
- In **Link in place** mode nothing is copied and `media/` may not even be
  created — so the timeline depends on those source files staying exactly where
  they are. Use *Collect into folder* if you'll move or archive it.
- Nothing connected *and* an empty `paths` is a clear error ("no clips to
  write"), not an empty timeline.

### Load Premiere Timeline

> **Plugin: not needed.** · `PremiereLoadTimeline`

Reads your Premiere edit into ComfyUI as a **segment list**. Export it from Premiere
first (`File > Export > Final Cut Pro XML`), then point this node at the `.xml`.
This is the "restyle my whole edit" starting point: parse once, then process each
cut through any video workflow you already have.

**Inputs.**

| Input | Type | Default | Meaning |
|---|---|---|---|
| `file_path` | STRING | empty | The `.xml` Premiere exported. |
| `skip_disabled` | BOOLEAN | **on** | Leave out clips you disabled in Premiere. |

**Outputs.** `segments` (`CPRB_SEGMENT_LIST`) — the edit in timeline order across all
video tracks · `count` (INT) · `summary` (STRING) — one readable line per segment,
so wiring it into any text-preview node gives you a free segment sheet.

**On-node buttons.** **Browse…** picks the `.xml` from a folder browser instead
of pasting a path; **Open folder** reveals it on disk. Both hide themselves,
leaving the path typeable, when you're viewing ComfyUI from a different machine
than the one it runs on.

**What it copes with.** Real Premiere export noise: `pproTicks*` attributes,
labels and filters, audio tracks, disabled clips, file-by-id references,
generator clips with no media, and the `-1` boundaries Premiere writes around
transitions. A compound/nested clip is returned as **one** segment — the clipitems
*inside* it are not walked, so a nested sequence never leaks its contents into
your segment list as extra cuts.

**Gotchas.**

- It reads Premiere's *exported XML*, not a `.prproj` — pointing it at a project
  file gives you instructions rather than a parse error.
- **Only the first sequence in the file is read.** If your export contains
  several, the ones after the first are ignored. Export the one you want.
- Re-exporting over the **same filename** really does re-run the node (it tracks
  the file's timestamp and size), so you don't have to rename anything to pick up
  an updated edit.
- A few segments can legitimately come back with an empty `path` — a compound clip,
  or a generator with no media on disk. Likewise `width`/`height` of `0` means
  "the export didn't say", not "zero pixels".
- An XML with no video clips at all is a hard error naming the likely cause,
  rather than a silently empty segment list.

### Get Premiere Segment

> **Plugin: not needed.** · `PremiereGetShot`

Pulls **one segment out of a segment list** by number, in every form a video loader
might want. This is the node that connects a Premiere edit to the rest of the
ecosystem.

**Inputs.** `segments` (`CPRB_SEGMENT_LIST` socket) · `index` (INT, default `0`,
0-based).

**Outputs**, in socket order: `path` (STRING) · `duration_seconds` (FLOAT) ·
`in_seconds` (FLOAT) · `frame_count` (INT) · `in_frame` (INT) · `fps` (FLOAT) ·
`name` (STRING) · `width` (INT) · `height` (INT).

Seconds for time-based loaders; frames for VideoHelperSuite's
`Load Video (Path)` — `in_frame` → `skip_first_frames` and `frame_count` →
`frame_load_cap`. `frame_count` already accounts for an exclusive out point, so
there's no off-by-one to correct.

**Gotchas.** `index` has no upper limit, so overshooting the segment count is the
realistic mistake — you get an error naming the valid range, but only once the
run reaches this node, not when you press Run. An **empty** segment list is a hard
error here (use [**Iterate Premiere Segments**](#iterate-shots) if you want an empty edit to
simply do nothing downstream). All values are computed at the segment's *own* frame
rate, never the sequence rate, so for a clip Premiere conformed to a different
rate prefer `in_seconds` / `duration_seconds` over the frame numbers.

### Iterate Premiere Segments

> **Plugin: not needed.** · `PremiereIterateShots`

**Every segment at once, as lists** — so ComfyUI fans the rest of your graph out
over every cut and one Run processes the whole edit. This is the node that
actually delivers "restyle my whole edit"; **Get Premiere Segment** handles one cut, this one
handles all of them. No index, no loop node, no widgets — just wire `segments` in.

**Input.** `segments` (`CPRB_SEGMENT_LIST` socket).

**Outputs**, in socket order — the same nine as Get Premiere Segment, each one a *list* with
an entry per segment: `path` · `duration_seconds` · `in_seconds` · `frame_count` ·
`in_frame` · `fps` · `name` · `width` · `height`.

**Gotchas.** An empty segment list is *not* an error here — every output becomes an
empty list and the graph downstream simply does nothing, which can look like a
Run that silently did nothing at all. It also has no `skip_disabled` of its own:
that filter belongs to **Load Premiere Timeline**, so if you turn it off there,
disabled clips get processed here too.

### Get Premiere Segment Frame

> **Plugin: not needed.** · `PremiereShotFrame`

Decodes **one frame** from a segment as an IMAGE — a thumbnail at that segment's in
point. Good for eyeballing what you're about to process, or for feeding a single
frame from your edit into an image workflow.

**Inputs.** `segments` (`CPRB_SEGMENT_LIST` socket) · `index` (INT, default `0`).

**Output.** `image` (IMAGE). This node shows nothing by itself — wire `image`
into a **Preview Image** (or anything else) or you'll see no frame.

**Gotchas.**

- It is **not frame-exact by design**: the seek is keyframe-granular, so you get
  the closest available frame rather than a guaranteed exact one. Fine for a
  preview, not a basis for frame-accurate work.
- An in point past the end of the media gives you the last decodable frame rather
  than an error.
- Its `index` is completely independent of any **Get Premiere Segment** node's `index`, so
  it's easy to preview segment 2 while processing segment 5.
- It takes a `segments` list, which **Iterate Premiere Segments** does not output — so it can't
  be fanned out that way. Drive it with an `index` instead.

### Send to Premiere

> **Plugin: optional** — the node does its whole job without it. ·
> `PremiereSendResult`

The outbound one-click: wire a **VIDEO** output (a WAN/I2V generation, a VHS
combine — audio comes along) and/or an **IMAGE** output into this node. With the
panel connected the result lands in Premiere's project panel in a **"ComfyUI
Results" bin** — created the first time, reused forever after. No export step, no
`File > Import`.

**Videos are the headline.** An existing video file is **linked in place**, so
multi-GB results are instant and nothing is copied. Only in-memory video, trimmed
inputs, or files sitting in ComfyUI's temp folder get written out first (into
`output/premiere_results/`, collision-free names, audio preserved).

This is an **output node**, so it can sit at the end of a graph.

**Inputs** — all optional; wire at least one of `video` / `image`.

| Input | Type | Default | Panel? | Meaning |
|---|---|---|---|---|
| `video` | VIDEO socket | — | no | The video to hand over. |
| `image` | IMAGE socket | — | no | The still to hand over. |
| `label` | STRING | empty | no | Names the clip in Premiere and the file when one is written; empty keeps the filename. |
| `bin_name` | STRING | `ComfyUI Results` | **yes** | Which bin to import into. |
| `color_label` | choice | `Default` | **yes** | Premiere label colour, so one run's results stand out in the bin. |
| `insert_at_playhead` | BOOLEAN | **off** | **yes** | Also drop the clip onto the active sequence at the playhead. |

The three marked **yes** are performed *by the panel*, so with no panel connected
they have no effect — the file is still written and reported.

**Outputs.** `written_path` (STRING) — the file that was handed to Premiere. For
a linked-in-place video this is the **original source path**, not a copy under
`premiere_results/` (nothing was copied, so there's nothing new to point at).

**`insert_at_playhead`** is **off by default**, so results only ever land in the
bin and your sequence is untouched. Switch it on and the clip is *also* dropped
onto the active sequence at your playhead, on the track above, as **one** undo
step. With no sequence open it's skipped with a logged line — never a guess.
(Proven live on Premiere 26.3.)

**Without the panel.** The node still resolves your result to a real file and
tells you exactly what to import, and the run **succeeds** — a missing panel is
never an error. You get two signals so a failed hand-off is never silent: a
warning toast carrying the full path, and a persistent status line on the node
itself (`⚠ Premiere` / `✓ Premiere`). And a send that failed **re-sends itself**
on your next Run once the panel is back — you don't have to touch the node.

**Gotchas.**

- **A batched IMAGE sends only the first frame**, and says so in the summary. To
  get every frame into Premiere, fan the graph out so this node runs once per
  image (or send a VIDEO instead).
- Wiring **both** `video` and `image` sends **both** files in one run, but
  `written_path` reports only the video's.
- The no-op colour option is spelled **`Default`**, not "None" — every Premiere
  clip carries some label, so there is no "no colour" state to pick.
- The re-send above re-runs the whole node, so on the branches that *write* a
  file (in-memory video, a trimmed input, a temp-folder source, an image) a
  failed-then-retried send leaves one unused file behind in
  `output/premiere_results/`. Names never collide, so nothing is overwritten.

### Frame from Premiere

> **Plugin: needed in practice** — the panel is what fills this node in. ·
> `PremiereFrameSource`

Hands your graph **the still under Premiere's playhead**. Click
**Frame → ComfyUI** in the panel and this node's `path` fills in by itself —
**and the workflow runs**, no trip back to ComfyUI. One button in Premiere is
the whole gesture; with **Send to Premiere** at the end of the graph, the result
is back in your bin before you've switched windows. Prefer to press Run
yourself? Turn off **Settings → Premiere Bridge → Auto-run** and the button only
fills the node.

**Inputs.** `path` (STRING, default empty) — an ordinary, visible text field. The
panel writes it; you can also read it (a second confirmation that something
arrived) and type it.

**Outputs.** `image` (IMAGE) · `width` (INT) · `height` (INT) · `path` (STRING) —
the file actually opened, which can differ from the widget by one documented
Premiere quirk (see below).

`width`/`height` are handy for matching a generation to your sequence's own
resolution without a second node.

**Where files land.** `<ComfyUI input folder>/premiere_frames/`, so exported
stills are already somewhere ComfyUI can read and `LoadImage` can browse. The
folder is created when the panel connects, and **only the 200 newest are kept** —
every click writes a new full-resolution PNG, so without that cap it would grow
forever.

**If Premiere can't write there** — an input folder on a NAS share is the case to
watch — set the environment variable **`CPRB_FRAMES_DIR`** to any absolute local
path before starting ComfyUI, and frames go there instead. ComfyUI's console
prints the folder it's actually using each time the panel connects.

**Without the panel.** The node runs on any path you type, but nothing fills it
in, and an empty `path` stops the graph before it queues with a message telling
you to press the panel button. If you're not running the panel, export a still
from Premiere yourself and use ComfyUI's `LoadImage` — it does the same job.

**Gotchas.** Premiere's frame export can report success and write nothing, so
**ComfyUI checks the file itself** and warns you immediately — and a missing
file never auto-runs, so one bad export can't become two errors. If Premiere
doubled the file extension (a defect fixed in 26.2.2), the `path` *output* is
the file that really exists while the widget still shows what Premiere claimed.
Clicking the button updates **every** matching node in **every** open ComfyUI
tab (only one tab runs the workflow), and tells you when there are none rather
than succeeding into thin air. Alpha is dropped rather than composited, and the
result is always a single image, never a batch.

### Clip from Premiere

> **Plugin: needed in practice** — the panel is what fills this node in. ·
> `PremiereClipSource`

Hands your graph **the clip you selected on Premiere's timeline** — and **exports
nothing at all**. It reads the clip's *own* media file plus its in/out points, so
this is instant even for multi-GB footage and no re-encode ever touches your
source.

**Inputs.**

| Input | Type | Default | Meaning |
|---|---|---|---|
| `path` | STRING | empty | The clip's own media file. |
| `start_seconds` | FLOAT | `0.0` | Its in point *inside that source media*. |
| `end_seconds` | FLOAT | `0.0` | Its out point. **`0` means the whole file.** |

All three are ordinary visible fields the panel writes.

Like Frame → ComfyUI above, the panel button **also runs the workflow** unless
you turn off **Settings → Premiere Bridge → Auto-run**.

**Outputs.** `segments` (`CPRB_SEGMENT_LIST`) · `path` (STRING) · `start_seconds`
(FLOAT) · `end_seconds` (FLOAT) · `video` (VIDEO) — the three middle ones report
the values actually used after any clamping.

**`video` is the one to reach for first**: a real, ready-to-wire VIDEO — the
clip's file plus its exact in/out, opened lazily, nothing decoded until a
downstream node asks. It plugs **directly** into anything with a plain VIDEO
input: **Send to Premiere**, core's **Save Video**, or a video-editing model
node (the Gemini/Veo-style API nodes). No load node, no path juggling — select
the clip in Premiere, click the button, and the video is already a socket in
your graph.

`segments` is for the segment-list world: a **one-segment list in exactly the same shape
Load Premiere Timeline produces**, so a clip lifted off your timeline plugs
straight into **Get Premiere Segment**, **Iterate Premiere Segments** and **Get Premiere Segment Frame** — and
through Get Premiere Segment into VideoHelperSuite-style loaders. The plain `path` /
`start_seconds` / `end_seconds` outputs are there for loaders that just want a
file and a range.

**Without the panel.** Type the clip's media path and its in/out in seconds. That
works, but you're reading those numbers off Premiere by hand.

**Gotchas.**

- **The media has to be readable by ffmpeg**, not just by Premiere. A clip
  Premiere plays happily in a format ffmpeg can't open (R3D and friends) is a
  hard error naming the file, because there's no honest frame rate or resolution
  to report without probing it.
- A **retimed or reversed** clip arrives as a plain forward source range — speed
  and direction aren't carried. **Merged/multicam** clips have several sources
  behind one item and Premiere reports one of them; the panel warns in its log
  rather than pretending otherwise.
- Titles, colour mattes, adjustment layers and nested sequences have no media
  file, so they're refused **by name** instead of sending an empty path. With
  nothing selected the panel tells you what it looked at and what to select — it
  never guesses at the clip under the playhead.
- The segment's `name` is the **media file's stem**, not Premiere's clip name, so a
  clip you renamed on the timeline shows up downstream under its file name.
- The out point is treated as **exclusive**, which is what makes `frame_count`
  come out right downstream. Every run logs `in`/`out`/`frame_count`/fps to
  ComfyUI's console, so you can compare against Premiere's own duration readout.

---

## The ComfyUI Bridge panel

The optional panel at [`premiere_plugin/`](premiere_plugin/) is a real status
window, built to match [comfyui-photoshop-bridge](https://github.com/ericpaulsnowden/comfyui-photoshop-bridge)'s
panel conventions. Top to bottom:

- **A status pill** — connected / connecting / disconnected / standing by — and
  **one Connect/Disconnect button**. It retries with a calm "retrying in Ns"
  countdown, and **Disconnect really disconnects**: it parks the retry loop until
  you press Connect rather than yanking you back online a second later.
- **SEND TO COMFYUI** — the **Frame → ComfyUI** and **Clip → ComfyUI** buttons.
  Both stay clickable at all times: a precondition is *reported* ("press Connect
  first", "no sequence open"), never greyed out, because a dead button explains
  nothing.
- **ACTIVITY** — a running log of everything the bridge does in both directions,
  with **Copy log** / **Clear log**.
- **ADVANCED** (collapsed) — plugin and server versions (amber if they ever
  disagree, meaning one side is stale), the server address field for a different
  host/port, and the live panel size.

Premiere-side, the panel is what performs the actual work for **Send to
Premiere**: find-or-create the bin, import, tag, colour-label, and — when you
enable the toggle — drop the clip at your playhead.

**One panel per server.** Open a second and the first stands by, rather than the
two fighting over the connection.

---

## Example workflow

[**`examples/premiere_roundtrip.json`**](examples/premiere_roundtrip.json) is the
whole loop in four nodes, using nothing but this pack and one core ComfyUI node:

```
Frame from Premiere ──▶ Invert Image Colors ──▶ Send to Premiere
   (the still at your playhead)                  (bin: "ComfyUI Results")
```

The inverted colours make a successful round trip obvious at a glance. See
[examples/README.md](examples/README.md) for what to click, in order. This is the
one example that genuinely needs the panel — it *is* the panel round trip.

---

## Same machine only (the panel's features)

The panel and ComfyUI exchange **file paths, not file bytes**, in both
directions. That's what makes multi-GB video instant, and it means the panel
features assume Premiere and ComfyUI are running on the same computer — a panel
on another machine would send paths ComfyUI cannot open.

The file-based nodes have no such constraint beyond the obvious one: the media a
timeline references has to be reachable from wherever you import it. A NAS/UNC
path works, and is tested.

---

## Versioning

Backend and frontend versions are shown in **Settings → Premiere Bridge**, and
the panel shows plugin-vs-server in its ADVANCED section. A mismatch means you
pulled an update but haven't restarted the server (or need a hard refresh).

```bash
cd ComfyUI/custom_nodes/comfyui-premiere-bridge
git pull
```

Then restart ComfyUI **and** hard-refresh the browser tab. Every push bumps the
version and is tagged; the contracts behind all of the above are specified in
[docs/PROTOCOL.md](docs/PROTOCOL.md).

## Running the tests

Runtime dependencies are stdlib plus whatever ComfyUI itself ships — this pack
declares none of its own (see [Install](#1-the-node-pack--required)). The test
suite goes further and exercises real image/tensor/media code paths, which need
`pillow`, `torch` and `av` (PyAV) actually importable. Without them, those
specific tests **skip with a reason** instead of failing, so running the suite
with a bare `pytest` install is expected to show skips, never failures.

Simplest way to a fully green run (no skips): a venv with
`pip install pytest pytest-aiohttp pytest-asyncio ruff pillow av` plus a
ComfyUI environment's own `torch` (a bare `pip install torch` risks pulling the
wrong build for your machine) — or just run the suite with ComfyUI's own venv.
Then, from the repo root:

```bash
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
