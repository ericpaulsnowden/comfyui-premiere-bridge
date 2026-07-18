# Installing comfyui-premiere-bridge

## Requirements

- ComfyUI (a 2025+ build with the core VIDEO nodes).
- Adobe Premiere Pro for the other end of the file exchange (any version
  that imports Final Cut Pro XML — i.e. every modern version). Nothing is
  installed into Premiere for Tier 1.
- Optional: `pip install opentimelineio` inside ComfyUI's Python if you
  want `.otio` output alongside the XML.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/ericpaulsnowden/comfyui-premiere-bridge
```

Restart ComfyUI. You should see `cprb vX.Y.Z loaded` in the server log and
a **Premiere Bridge** section in Settings.

## Update

```bash
cd ComfyUI/custom_nodes/comfyui-premiere-bridge
git pull
```

Restart ComfyUI **and** hard-refresh the browser tab; the two versions in
Settings → Premiere Bridge must match.
