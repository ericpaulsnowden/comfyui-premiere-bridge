# Example workflows

Five workflows, simple to advanced. **Drag the `.json` onto the ComfyUI canvas**, or use
**Workflow → Open**.

Every one of them is annotated on the canvas: a note at the top telling you what to do, and
a note beside each part of the graph explaining what that part is for and what to change.
You shouldn't need this page open to follow along.

| # | File | Needs the Premiere panel? | Needs a model? | Runs as shipped? |
|---|------|---------------------------|----------------|------------------|
| 01 | [`01_read_a_premiere_edit.json`](01_read_a_premiere_edit.json) | no | no | yes — pick `sample_timeline.xml` |
| 02 | [`02_build_a_timeline_for_premiere.json`](02_build_a_timeline_for_premiere.json) | no | no | yes — press Run |
| 03 | [`03_preview_and_send_a_segment.json`](03_preview_and_send_a_segment.json) | no | no | yes — run 02 first |
| 04 | [`04_premiere_roundtrip.json`](04_premiere_roundtrip.json) | **yes** | no | no — the panel drives it |
| 05 | [`05_clip_from_premiere.json`](05_clip_from_premiere.json) | **yes** | no | no — the panel drives it |

No workflow here needs a checkpoint, a LoRA, or a download of any kind, and none of them use
a third-party node pack. Between them they cover all eight nodes in this pack.

Also in this folder: **`sample_timeline.xml`** — a hand-trimmed stand-in for a real
`File → Export → Final Cut Pro XML`. Demo 01 reads it. Its clips point at media that does not
exist on your machine, which is deliberate: the nodes that read it never open a media file.
The XML has comments in it explaining what it's built to exercise.

---

## 01 — Read a Premiere edit

**Nodes:** Load Premiere Timeline → Get Premiere Segment / Iterate Premiere Segments

Takes an edit you exported out of Premiere and tells you what's in it: every cut, in order,
with its source file, in/out points, frame rate and pixel size. Then shows you the two ways to
use that list — pulling one segment out by number, or fanning out over every segment so the
rest of your workflow runs once per cut.

**Needs:** nothing. Click **Browse…** on the Load node and pick `sample_timeline.xml` from this
folder, or your own exported XML. On another machine than the one running ComfyUI, Browse is
hidden — paste the full path into the widget instead.

**Expect:** three text previews. A summary of the whole edit (3 segments from the sample —
4 if you switch `skip_disabled` off), one segment's source path, and one preview box per
segment showing its name.

---

## 02 — Build a timeline Premiere can import

**Nodes:** Save Premiere Timeline

The other direction. Two one-second clips are generated inside ComfyUI and laid end to end on
V1, and the Save node writes a folder that Premiere can import with its media already linked —
no relinking, no dragging clips into a bin.

**Needs:** nothing. It uses ComfyUI's own `example.png`, so it runs untouched. The two clip
generators are placeholders — swap in whatever actually makes your video.

**Expect:** `output/premiere_timelines/Bridge Demo/` containing `Bridge Demo.xml`,
`Bridge Demo.edl` and a `media/` folder with two `.mp4`s. Click **Open folder** on the Save node
to go straight there, then `File → Import` the `.xml` in Premiere.

---

## 03 — Preview a segment, then hand it back

**Nodes:** Load Premiere Timeline → Get Premiere Segment Frame → Send to Premiere

Decodes a real frame out of a real segment — the frame the editor cut *to*, not the head of the
file — inverts it, and hands the result back. It's the panel-free half of the round trip, and it
proves the thing people don't expect: **Send to Premiere works with Premiere closed.** The node
writes the file and tells you exactly where it put it.

**Needs:** an edit whose footage genuinely exists. Run **02** first, then Browse to
`output/premiere_timelines/Bridge Demo/Bridge Demo.xml`. (Demo 01's `sample_timeline.xml` won't
work here — its clips are fictional and this graph has to open one.) On another machine than the
one running ComfyUI, Browse is hidden — paste the full path into the widget instead.

**Expect:** the segment's first frame in a Preview Image node, and a status line on Send to
Premiere labelled `✓ Premiere` or `⚠ Premiere`. With no panel connected you also get a warning
toast carrying the full path of the file it wrote.

---

## 04 — Premiere round trip

**Nodes:** Frame from Premiere → Send to Premiere

The smallest possible proof that the panel round trip works, and the file to reach for when
something feels broken. A frame goes out of Premiere, an inverted frame comes back into the
`ComfyUI Results` bin. Colour inversion is the point: you don't have to squint at the result to
know whether it made it.

**Needs:** the **ComfyUI Bridge panel running in Premiere** (`premiere_plugin/`, Premiere ≥ 26.3),
connected, with ComfyUI and Premiere on the **same machine** — the two sides exchange file paths,
not file bytes. There is nothing to press in ComfyUI: the panel's **Frame → ComfyUI** button
fills in the path *and* starts the run.

**Expect:** one new clip called `inverted frame`, obviously inverted, in the `ComfyUI Results`
bin. That single clip proves both directions at once.

---

## 05 — Clip from Premiere

**Nodes:** Clip from Premiere → Get Premiere Segment / Send to Premiere

Select a clip in Premiere, click **Clip → ComfyUI**, and ComfyUI opens that footage where it
already sits — no export, no render, no wait. The node gives you the clip two ways at once: as a
segment list (everything from demo 01 works on it) and as a ready VIDEO you can feed a video
model or send straight back.

**Needs:** the **ComfyUI Bridge panel**, same requirements as 04. Its three widgets ship empty
because the panel fills them in.

**Expect:** the clip's frame count in a text preview, and the clip itself back in your
`ComfyUI Results` bin, untouched. Put a real video workflow between the two nodes and you have a
working loop.

---

### If a run finishes and nothing shows up in Premiere

Look at the **Send to Premiere** node. It grows a status line labelled `✓ Premiere` when the
hand-off worked and `⚠ Premiere` when it didn't, and a failure also raises a warning toast
carrying the full path of the file it saved. A failed hand-off is never silent here, and the
file is always written either way.
