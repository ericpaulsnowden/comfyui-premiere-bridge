# Example workflows

## `premiere_roundtrip.json` — Premiere → ComfyUI → Premiere

The smallest possible proof that the round trip works. It uses **only** this
pack's nodes plus one core ComfyUI node — no VideoHelperSuite, no rgthree, no
models, no downloads.

### Flow

```
Frame from Premiere ──▶ Invert Image Colors ──▶ Send to Premiere
   (the still at your playhead)                  (bin: "ComfyUI Results")
```

Colour inversion is the point: you don't have to squint at the result to know
whether it round-tripped. It comes back obviously, unmistakably inverted.

### How to open it

- **Workflow menu → Open** and pick this `.json`, **or**
- **drag the `.json` onto the ComfyUI canvas**.

### What to click, in order

1. In **Premiere**, open a sequence and park the playhead on a frame.
2. In the **ComfyUI Bridge panel**, confirm the status pill says connected,
   then click **"Frame → ComfyUI"**. The exported frame's path lands in the
   **Frame from Premiere** node and a toast says so. *Nothing queues* — the
   bridge never runs a graph for you.
3. Back in ComfyUI, press **Run**.
4. In **Premiere**, open the **"ComfyUI Results"** bin.

### What success looks like

A new clip in the **ComfyUI Results** bin (created the first time, reused
after), named `inverted frame`, whose colours are inverted. That single clip
proves both directions: the frame got out of Premiere and the result got back
in.

If the run finishes but nothing appears, read the status line on the **Send to
Premiere** node — it says either `Sent to Premiere: <path>` or
`Plugin not connected — import manually: <path>`, so a failed hand-off is
never silent.

### Optional extras

- **`insert_at_playhead`** on the Send to Premiere node is **off** in this
  file (the safe default: results only land in the bin). Switch it on and the
  clip also drops onto the active sequence at your playhead, as one undo step.
- The **Frame from Premiere** node also outputs `width`, `height` and `path`,
  unwired here — handy if you swap the invert for a real generation and need
  the frame's dimensions.
- Swapping **Invert Image Colors** for any image workflow is the whole idea;
  the two Premiere nodes are the only parts that need to stay.

### Requirements

- **This node pack** installed in `ComfyUI/custom_nodes`, so
  `PremiereFrameSource` and `PremiereSendResult` resolve.
- **The ComfyUI Bridge panel running in Premiere** (`premiere_plugin/`,
  Premiere ≥ 26.3), connected. This example is the one workflow here that
  genuinely needs the panel — it *is* the panel round trip. The panel-free
  path is Save/Load Premiere Timeline in the main
  [README](../README.md).
- ComfyUI and Premiere on the **same machine**: the two sides exchange file
  paths, not file bytes.
