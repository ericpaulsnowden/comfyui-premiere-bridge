# Installing comfyui-premiere-bridge

See the [README](../README.md) for what each node does and which of them need
the optional Premiere panel. Short version: **six of the eight nodes need
nothing installed in Premiere**, so step 2 below is genuinely optional.

## Requirements

- **ComfyUI** (a 2025+ build with the core VIDEO nodes). Everything the pack
  imports — aiohttp, PyAV, PyTorch, Pillow, NumPy — already ships with it, so
  there is no pip step for the pack itself.
- **Adobe Premiere Pro** for the other end of the exchange. For the file-based
  nodes, any modern version that imports Final Cut Pro XML will do, and
  **nothing is installed into Premiere**. The optional panel (step 2) needs
  **Premiere 26.3 or newer**.
- Optional: `pip install opentimelineio` inside ComfyUI's Python if you want
  `.otio` output alongside the XML from **Save Premiere Timeline**. Without it,
  that checkbox is skipped with a note rather than failing.

## 1. The node pack — required

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ericpaulsnowden/comfyui-premiere-bridge
```

Restart ComfyUI. You should see `cprb vX.Y.Z loaded (8 nodes)` in the server log
and a **Premiere Bridge** section in Settings. The nodes appear in the node menu
under two buckets: **Premiere Bridge > Handoffs** (six nodes that need nothing
installed in Premiere) and **Premiere Bridge > Handoffs (requires Premiere)**
(Frame from Premiere and Clip from Premiere, which the panel fills in).

At this point everything file-based works: **Save Premiere Timeline**, **Load
Premiere Timeline**, **Get Premiere Segment**, **Iterate Premiere Segments**,
**Get Premiere Segment Frame**, and the file half of **Send to Premiere**.

## 2. The ComfyUI Bridge panel — optional

Skip this unless you want **Send to Premiere**'s automatic import into a bin, or
the **Frame from Premiere** / **Clip from Premiere** nodes.

Requires **Premiere ≥ 26.3**. It is a *developer* load, not a Marketplace
install:

1. In Premiere: **Preferences → Plugins → Enable Developer Mode** (restart
   Premiere if it was off).
2. Open **Adobe UXP Developer Tool** → **Add Plugin** → select
   `custom_nodes/comfyui-premiere-bridge/premiere_plugin/manifest.json`.
3. Click **Load**. The panel appears, labelled **ComfyUI Bridge** (the plugin is
   listed as *ComfyUI for Premiere*).

The panel connects to ComfyUI at `localhost:8188` by itself; its **ADVANCED**
section has a host:port field if yours is elsewhere, and shows the plugin and
server versions side by side (amber when they disagree, meaning one side is
stale).

**A UDT-loaded plugin does not survive a Premiere restart.** That is how
developer loads work — reopen the UXP Developer Tool and press **Load** again
after restarting Premiere.

Panel features are **same-machine only**: the two sides exchange absolute file
paths, never bytes, so ComfyUI has to be able to open what Premiere names.

Premiere-facing behaviour is tracked in [SPIKES.md](SPIKES.md) until a live
session proves it; the wire contracts are in [PROTOCOL.md](PROTOCOL.md).

## Update

```bash
cd ComfyUI/custom_nodes/comfyui-premiere-bridge
git pull
```

Restart ComfyUI **and** hard-refresh the browser tab; the two versions in
Settings → Premiere Bridge must match. If you run the panel, re-**Load** it in
UDT too — the plugin and server versions bump in lockstep, and the panel's
ADVANCED line turns amber when one side is behind.
