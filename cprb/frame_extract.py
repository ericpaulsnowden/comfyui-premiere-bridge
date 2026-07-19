"""Decodes one preview frame from a media file via PyAV (PROTOCOL.md §6.5).

Split out from :mod:`cprb.probe` (a pure metadata probe, no pixel decode at
all) into its OWN module specifically so its two heavy runtime dependencies
-- ``av`` AND ``torch`` -- never load unless
:class:`cprb.nodes_load.PremiereShotFrame` is actually wired into a graph
and run. Both imports are LAZY, inside :func:`extract_frame` itself (the
same convention :mod:`cprb.probe` already uses for ``av`` alone): nothing at
module scope touches either, so this module -- and therefore
:mod:`cprb.nodes_load`, which imports :func:`extract_frame` at its own
module scope -- stays importable under a bare ``pytest`` on a machine that
has installed neither.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch


def extract_frame(path: str, in_seconds: float) -> torch.Tensor:
    """Decode the frame nearest *in_seconds* into *path*, as a ComfyUI IMAGE tensor.

    Seeks the container to *in_seconds* (converted to the video stream's own
    ``time_base`` units) and decodes forward from there, keeping the latest
    frame seen until one reaches or passes the seek target -- "the nearest
    frame at *in_seconds*" a container that seeks only to keyframes actually
    supports (confirmed empirically against this rig's PyAV 18: a short,
    single-GOP clip seeks back to frame 0 no matter the target, so decoding
    forward from there -- rather than returning the very first post-seek
    frame -- is what makes this land near *in_seconds* instead of always
    returning the file's first frame). If the stream ends before any frame's
    ``pts`` reaches the target, the last frame decoded is returned (closest
    available, never an error purely for "*in_seconds* ran past the end").

    Best-effort per PROTOCOL.md §6.5: this is the one node in the pack that
    can fail on media :class:`~cprb.nodes_load.PremiereGetShot`/
    :class:`~cprb.nodes_load.PremiereIterateShots` handle just fine (their
    path is pure dict lookups, no decode at all) -- offline drives, codecs
    ffmpeg can't decode, zero-frame streams, etc. All surface here as a
    plain, clearly-labeled :class:`ValueError` naming *path* rather than a
    raw PyAV/ffmpeg traceback; per §6.5 the owner's own fallback when this
    fails is simply not wiring this node and relying on VHS instead, so a
    hard error (never a placeholder image) is the right behavior.

    Args:
        path: Absolute path to the shot's source media file.
        in_seconds: Seek target, in seconds (PROTOCOL.md §6.3's
            ``in_seconds`` math: the shot's source ``in`` frame ÷ its
            ``source_fps`` -- never ``sequence_fps``). Negative values clamp
            to ``0.0``.

    Returns:
        A ``torch.float32`` tensor, shape ``[1, H, W, 3]`` (batch of 1, HWC,
        RGB), values scaled to ``[0, 1]`` -- the standard ComfyUI IMAGE
        shape/dtype, ready to wire into any core node that accepts IMAGE.

    Raises:
        ValueError: *path* can't be opened, has no video stream, or no frame
            could be decoded from it at all. Every message names *path*.
    """
    import av
    import torch

    try:
        container = av.open(path)
    except Exception as exc:  # any av/ffmpeg open failure becomes a ValueError.
        raise ValueError(f"Get Shot Frame: could not open media file: {path} ({exc})") from exc

    try:
        video_streams = container.streams.video
        if not video_streams:
            raise ValueError(f"Get Shot Frame: no video stream found in: {path}")
        stream = video_streams[0]

        target_ts = max(in_seconds, 0.0) / stream.time_base
        with contextlib.suppress(Exception):
            container.seek(round(target_ts), stream=stream)  # else: decode from the top instead.

        frame = None
        for candidate in container.decode(stream):
            frame = candidate
            if candidate.pts is not None and candidate.pts >= target_ts:
                break
        if frame is None:
            raise ValueError(f"Get Shot Frame: could not decode any frame from: {path}")

        array = frame.to_ndarray(format="rgb24")  # HWC, uint8, RGB.
    except ValueError:
        raise
    except Exception as exc:  # any other av/ffmpeg decode failure becomes a ValueError.
        raise ValueError(f"Get Shot Frame: could not decode media file: {path} ({exc})") from exc
    finally:
        container.close()

    tensor = torch.from_numpy(array).float() / 255.0
    return tensor.unsqueeze(0)
