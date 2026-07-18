"""Media probing via PyAV (PROTOCOL.md §3.3): frames, fps, resolution, duration.

PyAV (``av``) ships with ComfyUI itself, so this module is safe to use from
inside a running ComfyUI process -- but it must also stay IMPORTABLE from a
plain ``pytest`` run on a machine that never installed ComfyUI (the same
promise ``cprb/context.py`` makes for the rest of the package). The import is
therefore LAZY, inside :func:`probe_media` itself, exactly like
``cpsb``'s own lazy-import convention for optional heavy dependencies: nothing
at module scope touches ``av``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import av


class ProbeError(Exception):
    """A media file could not be probed for timeline use (PROTOCOL.md §3.3).

    Always carries the offending *path* in its message: callers
    (:mod:`cprb.nodes_save`) probe several files per run (every materialized
    ``video_N`` plus every ``paths`` line), so a bare "no video stream" with
    no path would leave the user guessing which clip failed. Distinct from a
    plain ``OSError``/``av`` exception so callers can catch probing failures
    specifically without also swallowing unrelated bugs.
    """


@dataclass
class MediaInfo:
    """Everything :mod:`cprb.timeline_write` needs about one probed file.

    Attributes:
        frames: Frame count. When the container doesn't store this directly
            (``stream.frames == 0`` -- common for e.g. mpegts, some
            fragmented mp4s), it is derived as ``round(duration * fps)`` from
            whatever duration IS available (see :func:`probe_media`), so this
            is always a real, usable integer, never 0 for a valid clip.
        fps: The stream's ``average_rate`` as a plain float -- PyAV's own
            answer for "one representative frame rate" even for VFR-ish
            sources (there is no single correct fps for true variable frame
            rate; ``average_rate`` is the best single number PyAV offers, and
            PROTOCOL.md §3.3 only ever cuts clips at their real-time length
            using ONE fps value, never a per-frame VFR timeline).
        width: Native pixel width.
        height: Native pixel height.
        duration_seconds: Always exactly ``frames / fps`` (computed AFTER
            ``frames`` is resolved, whether directly from the container or
            via the fallback above) -- never a second, independently-probed
            duration. This is a deliberate invariant: it guarantees
            ``round(duration_seconds * fps) == frames`` exactly, which is
            what keeps PROTOCOL.md §3.3's "the clip occupies
            ``round(seconds * sequence_fps)`` sequence frames" self-
            consistent for a clip cut at the SEQUENCE's own fps (no drift
            purely from how duration was rounded here).
    """

    frames: int
    fps: float
    width: int
    height: int
    duration_seconds: float


def _duration_seconds_from_container(
    container: av.container.InputContainer, stream: av.video.stream.VideoStream
) -> float | None:
    """Best-effort real-world duration in seconds, for the ``frames == 0`` fallback only.

    Prefers the STREAM's own duration (``stream.duration`` is in units of
    ``stream.time_base``, so it is converted explicitly) since that is
    specific to the video stream being probed; falls back to the
    CONTAINER's duration (microseconds, PyAV's ``AV_TIME_BASE``) when the
    stream doesn't expose one. Returns ``None`` (never raises) when neither
    is available -- :func:`probe_media` turns that into a clear
    :class:`ProbeError` rather than guessing.
    """
    if stream.duration is not None and stream.time_base is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return container.duration / 1_000_000
    return None


def probe_media(path: str) -> MediaInfo:
    """Probe *path* with PyAV and return its :class:`MediaInfo`.

    Args:
        path: Absolute (or otherwise resolvable) path to a media file.

    Returns:
        The file's frame count, representative fps, native resolution, and
        ``frames / fps`` duration.

    Raises:
        ProbeError: *path* can't be opened at all (missing file, unreadable,
            unrecognized container -- whatever PyAV/ffmpeg raises is wrapped
            with *path* added); it has no video stream; its frame rate is
            unavailable (``stream.average_rate`` falsy -- deliberately NOT
            papered over by falling back to ``guessed_rate`` or any other
            heuristic: an unplayable rate is worth failing loudly on, per
            PROTOCOL.md §3.3); its frame count is both unset (``0``) AND has
            no usable duration to derive one from; or its resolved frame
            count/dimensions come back non-positive.

    Every raise includes *path* in the message (the class docstring's
    contract), so a batch probe over several clips (§3.1 ordering) always
    names the specific offender.
    """
    import av  # lazy: see module docstring.

    try:
        container = av.open(path)
    except Exception as exc:  # any av/ffmpeg failure becomes a ProbeError.
        raise ProbeError(f"could not open media file for probing: {path} ({exc})") from exc

    try:
        video_streams = container.streams.video
        if not video_streams:
            raise ProbeError(f"no video stream found in: {path}")
        stream = video_streams[0]

        # VFR-ish sources still get ONE representative fps from PyAV itself;
        # a missing rate is a hard error, never silently guessed at.
        if not stream.average_rate:
            raise ProbeError(f"media file has no usable frame rate: {path}")
        fps = float(stream.average_rate)

        frames = stream.frames
        if not frames:
            duration_seconds = _duration_seconds_from_container(container, stream)
            if duration_seconds is None:
                raise ProbeError(
                    f"media file exposes neither a frame count nor a duration: {path}"
                )
            frames = round(duration_seconds * fps)
        if frames <= 0:
            raise ProbeError(f"media file has zero effective frames: {path}")

        width, height = stream.width, stream.height
        if not width or not height:
            raise ProbeError(f"media file has no usable dimensions: {path}")

        return MediaInfo(
            frames=frames,
            fps=fps,
            width=width,
            height=height,
            duration_seconds=frames / fps,
        )
    finally:
        container.close()
