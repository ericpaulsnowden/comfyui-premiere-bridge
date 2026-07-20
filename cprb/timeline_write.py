"""Pure timeline serializers: xmeml (§4), EDL (§5), and optional OTIO.

PURE means exactly that: no ``av``, no ComfyUI import anywhere in this
module, at any scope. Every function here is a plain string-in/string-out
(or dataclass-in) transform over already-probed data, so the whole module
imports and runs under a bare ``pytest`` with none of ComfyUI's or PyAV's
runtime present -- :mod:`cprb.nodes_save` is the only thing that touches
:mod:`cprb.probe` (and only lazily, inside its own ``execute()``).

Research note (PROTOCOL.md's build brief): the xmeml shape below follows
PROTOCOL.md §4's own field list, cross-checked against (a) the archived
Apple FCP7 XML DTD (element child lists, ``pathurl`` escaping rules) and (b)
a real ``File > Export > Final Cut Pro XML`` produced by Premiere itself
(a public gist of a "Bars and Tone" default-sequence export) for the
RELATIVE ORDER fields appear in -- ``sequence``: uuid, duration, rate, name,
media, timecode; ``clipitem``: name, enabled, duration, rate, start, end,
in, out, file; ``file``: name, pathurl/mediaSource, rate, duration, media.
PROTOCOL.md §4 already prescribes which of those fields v1 emits (a strict
subset of what a real export contains -- no masterclipid, pproTicks*,
alphatype, link, logginginfo, labels, or per-file timecode); this module
emits exactly that subset, in the researched order.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote

#: Fixed seed for :func:`build_xmeml`'s per-sequence ``<uuid>``. Computed once
#: via ``uuid.uuid5(uuid.NAMESPACE_DNS, "comfyui-premiere-bridge.cprb")`` and
#: hardcoded so it can never silently drift (e.g. if this module were ever
#: renamed) -- deterministic repeat builds (PROTOCOL.md §4: "uuid5 from
#: sequence name") need the SAME namespace forever, not just within one process.
_UUID_NAMESPACE = uuid.UUID("55b5ab78-09ff-597b-b19c-44379b8df40d")

#: fps widget string -> (timebase, ntsc, drop_frame), PROTOCOL.md §4.2 exactly.
#: Insertion order is the §3.2 COMBO order; :mod:`cprb.nodes_save` builds its
#: ``fps`` widget's choice list from ``list(RATE_TABLE)`` rather than
#: repeating the 8 strings, so the widget and the writer can never drift apart.
RATE_TABLE: dict[str, tuple[int, bool, bool]] = {
    "23.976": (24, True, False),
    "24": (24, False, False),
    "25": (25, False, False),
    "29.97": (30, True, True),
    "30": (30, False, False),
    "50": (50, False, False),
    "59.94": (60, True, True),
    "60": (60, False, False),
}

_XML_PROLOG = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


@dataclass
class ClipSpec:
    """One media clip destined for the emitted timeline (PROTOCOL.md §3.1/§4.4).

    Assembled entirely by :mod:`cprb.nodes_save` (from a materialized
    ``video_N`` or a ``paths`` line, after probing) -- this module only ever
    consumes the finished result, never derives any of these fields itself.

    Attributes:
        name: Display name -- becomes both the xmeml ``<clipitem><name>``
            and ``<file><name>`` (PROTOCOL.md §4.4: "name (media file
            stem)"). Callers pass the on-disk file's stem.
        path: Absolute path to the media. Encoded per §4.3 for ``pathurl``;
            used verbatim in the EDL's ``* SOURCE FILE:`` comment.
        frames: Native frame count (probed).
        fps: Native frame rate (probed).
        width: Native pixel width (probed).
        height: Native pixel height (probed).
    """

    name: str
    path: str
    frames: int
    fps: float
    width: int
    height: int


def _bool_str(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _native_rate(fps: float) -> tuple[int, bool]:
    """Best-effort ``(timebase, ntsc)`` for an arbitrary PROBED fps.

    :data:`RATE_TABLE` only covers the 8 fixed SEQUENCE-fps widget strings
    (§4.2); a probed FILE's native fps (§4.4: "rate = the file's NATIVE rate
    (probed)") can be any float PyAV's ``average_rate`` reports. This
    generalizes the same NTSC convention: a probed rate within a tight
    tolerance of the standard ``timebase * 1000/1001`` ratio is reported as
    NTSC at that timebase (so a genuine 23.976/29.97/59.94-ish source round-
    trips to the same ``(timebase, ntsc)`` pair §4.2 assigns its matching
    widget string); anything else rounds to the nearest whole timebase with
    ``ntsc=False``. This never raises: an odd VFR average (e.g. 24.03) simply
    rounds to the closest plain timebase, which is the most a DTD ``<rate>``
    (one integer + one boolean) can ever represent.

    The 0.01 tolerance is deliberately tighter than the ~0.024fps gap between
    a plain integer rate and its NTSC neighbor (e.g. 24 vs 23.976023976...),
    so an exact plain rate is never misclassified as NTSC or vice versa.
    """
    timebase = max(round(fps), 1)
    ntsc_value = timebase * 1000 / 1001
    if abs(fps - ntsc_value) < 0.01:
        return timebase, True
    return timebase, False


def _encode_pathurl(path: str) -> str:
    """*path* as an xmeml/OTIO ``file://`` URL per PROTOCOL.md §4.3.

    Deliberately pure string/regex manipulation -- never :mod:`pathlib` --
    so a Windows-style or UNC path string encodes identically no matter
    which OS actually runs this function (this repo's dev/test machine is
    not necessarily the machine that will ever open the resulting file).

    Three forms, matched on the BACKSLASH-NORMALIZED string:

    * UNC (``\\\\host\\share\\...``) -> ``file://host/share/...``, host in
      the authority slot. VERIFY(spike-S2): SPIKES.md S2 -- which pathurl
      form Premiere actually links for UNC media is unconfirmed; this is the
      form PROTOCOL.md §4.3 currently specifies.
    * Windows drive (``C:\\...``) -> ``file://localhost/C%3a/...``: the
      drive letter's colon is encoded to a LOWERCASE ``%3a`` specifically
      (``urllib.parse.quote`` alone would produce uppercase ``%3A``; §4.3's
      own example is lowercase, so that one character is hand-encoded rather
      than left to ``quote``).
    * Anything else (POSIX absolute path) -> ``file://localhost/<path>``,
      ``quote``-encoded with ``safe="/"`` so path separators survive
      unescaped while spaces/unicode/etc. do not.
    """
    normalized = path.replace("\\", "/")

    if normalized.startswith("//"):
        host, _, tail = normalized[2:].partition("/")
        return f"file://{host}/{quote(tail, safe='/')}"

    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        drive, tail = normalized[0], normalized[2:]
        return f"file://localhost/{drive}%3a{quote(tail, safe='/')}"

    return f"file://localhost{quote(normalized, safe='/')}"


def _basename(path: str) -> str:
    """Final path component of *path*, tolerant of ``/`` and ``\\`` alike.

    Used for the EDL's ``* FROM CLIP NAME:`` comment (PROTOCOL.md §5, which
    wants the basename WITH extension, unlike :attr:`ClipSpec.name`'s
    extension-less stem). Not :meth:`pathlib.Path.name`, for the same
    cross-platform-string reason as :func:`_encode_pathurl`.
    """
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _sequence_positions(
    clips: Sequence[ClipSpec], sequence_fps: float
) -> list[tuple[int, int, int]]:
    """``(start, end, duration)`` in SEQUENCE frames, back-to-back from 0.

    PROTOCOL.md §3.3: "the clip occupies ``round(seconds * sequence_fps)``
    sequence frames" where ``seconds = frames / fps`` is the clip's own
    real-world duration -- native fps differences from the sequence rate are
    NOT resampled, only used to compute how many sequence frames the clip's
    real-time length fills. Each clip starts exactly where the previous one
    ended (§3.1: "back-to-back from 00:00:00:00 on video track 1").
    """
    positions = []
    cursor = 0
    for clip in clips:
        duration = round((clip.frames / clip.fps) * sequence_fps)
        start = cursor
        end = start + duration
        positions.append((start, end, duration))
        cursor = end
    return positions


def _require_clips(clips: Sequence[ClipSpec], fps_key: str, who: str) -> None:
    if not clips:
        raise ValueError(f"{who}: at least one clip is required")
    if fps_key not in RATE_TABLE:
        raise ValueError(f"{who}: unknown fps {fps_key!r} (expected one of {list(RATE_TABLE)})")


def _add_rate(parent: ET.Element, timebase: int, ntsc: bool) -> ET.Element:
    rate_el = ET.SubElement(parent, "rate")
    ET.SubElement(rate_el, "timebase").text = str(timebase)
    ET.SubElement(rate_el, "ntsc").text = _bool_str(ntsc)
    return rate_el


def _add_clipitem(
    track_el: ET.Element,
    index: int,
    clip: ClipSpec,
    start: int,
    end: int,
    duration: int,
    timebase: int,
    ntsc: bool,
) -> None:
    """One ``<clipitem>`` + its one ``<file>``, per PROTOCOL.md §4.4.

    v1 always emits a FULL ``<file>`` block (never the DTD-legal self-closing
    ``<file id="file-i"/>`` back-reference): this module's file ids are
    ``file-i`` keyed to the CLIPITEM's own index, one-to-one, so there is
    never a second clipitem that needs to re-point at an already-defined id
    -- the self-closing form only matters for the linked-audio-clipitem
    pattern (§9 S5), out of scope for v1's video-only track.

    ``in``/``out`` are ``0``/``duration`` (matching ``start``/``end`` being
    ``S``/``S+D``) -- an EXCLUSIVE-style range throughout, not "last frame
    index"; PROTOCOL.md §4.4 specifies exactly these values.
    """
    clipitem_el = ET.SubElement(track_el, "clipitem", id=f"clipitem-{index}")
    ET.SubElement(clipitem_el, "name").text = clip.name
    ET.SubElement(clipitem_el, "enabled").text = "TRUE"
    ET.SubElement(clipitem_el, "duration").text = str(duration)
    _add_rate(clipitem_el, timebase, ntsc)
    ET.SubElement(clipitem_el, "start").text = str(start)
    ET.SubElement(clipitem_el, "end").text = str(end)
    ET.SubElement(clipitem_el, "in").text = "0"
    ET.SubElement(clipitem_el, "out").text = str(duration)

    file_el = ET.SubElement(clipitem_el, "file", id=f"file-{index}")
    ET.SubElement(file_el, "name").text = clip.name
    ET.SubElement(file_el, "pathurl").text = _encode_pathurl(clip.path)
    native_timebase, native_ntsc = _native_rate(clip.fps)
    _add_rate(file_el, native_timebase, native_ntsc)
    ET.SubElement(file_el, "duration").text = str(clip.frames)
    file_samplechar_el = ET.SubElement(
        ET.SubElement(ET.SubElement(file_el, "media"), "video"), "samplecharacteristics"
    )
    ET.SubElement(file_samplechar_el, "width").text = str(clip.width)
    ET.SubElement(file_samplechar_el, "height").text = str(clip.height)


def build_xmeml(sequence_name: str, fps_key: str, clips: Sequence[ClipSpec]) -> str:
    """The FCP7 XML (xmeml) document for this timeline (PROTOCOL.md §4).

    Deterministic: the same arguments always produce byte-identical output
    (the ``<uuid>`` is a :func:`uuid.uuid5` of *sequence_name* against the
    fixed :data:`_UUID_NAMESPACE` -- no randomness, no wall-clock timestamp
    anywhere in this module).

    Args:
        sequence_name: Both the ``<sequence><name>`` text and the uuid5 seed.
        fps_key: One of :data:`RATE_TABLE`'s 8 keys -- the SEQUENCE rate.
        clips: In final timeline order (§3.1: connected ``video_N`` in
            ascending N, then ``paths`` lines top to bottom); must be
            non-empty.

    Returns:
        The complete ``.xml`` file content (with trailing newline).

    Raises:
        ValueError: *clips* is empty, or *fps_key* isn't a recognized rate.
    """
    _require_clips(clips, fps_key, "build_xmeml")
    timebase, ntsc, drop_frame = RATE_TABLE[fps_key]
    sequence_fps = float(fps_key)
    positions = _sequence_positions(clips, sequence_fps)
    total_duration = positions[-1][1]

    xmeml_el = ET.Element("xmeml", version="4")
    sequence_el = ET.SubElement(xmeml_el, "sequence", id="sequence-1")
    ET.SubElement(sequence_el, "uuid").text = str(uuid.uuid5(_UUID_NAMESPACE, sequence_name))
    ET.SubElement(sequence_el, "duration").text = str(total_duration)
    _add_rate(sequence_el, timebase, ntsc)
    ET.SubElement(sequence_el, "name").text = sequence_name

    media_el = ET.SubElement(sequence_el, "media")
    video_el = ET.SubElement(media_el, "video")
    samplechar_el = ET.SubElement(ET.SubElement(video_el, "format"), "samplecharacteristics")
    # PROTOCOL.md §4.1: the format block describes the SEQUENCE's editing
    # format, so its rate is the §4.2 SEQUENCE rate (matching what real
    # Premiere exports carry there); only the pixel dimensions are borrowed
    # from clip 1, v1's stand-in for a dedicated resolution widget. Clip
    # NATIVE rates appear only in their own <file> blocks (§4.4).
    first = clips[0]
    _add_rate(samplechar_el, timebase, ntsc)
    ET.SubElement(samplechar_el, "width").text = str(first.width)
    ET.SubElement(samplechar_el, "height").text = str(first.height)

    track_el = ET.SubElement(video_el, "track")
    for index, (clip, (start, end, duration)) in enumerate(
        zip(clips, positions, strict=True), start=1
    ):
        _add_clipitem(track_el, index, clip, start, end, duration, timebase, ntsc)

    # v1: EMPTY track (PROTOCOL.md §4.1) -- video-only edit, no audio clipitems.
    ET.SubElement(ET.SubElement(media_el, "audio"), "track")

    timecode_el = ET.SubElement(sequence_el, "timecode")
    _add_rate(timecode_el, timebase, ntsc)
    ET.SubElement(timecode_el, "string").text = "00:00:00:00"
    ET.SubElement(timecode_el, "frame").text = "0"
    ET.SubElement(timecode_el, "displayformat").text = "DF" if drop_frame else "NDF"

    ET.indent(xmeml_el, space="  ")
    return _XML_PROLOG + ET.tostring(xmeml_el, encoding="unicode") + "\n"


def _frames_to_timecode(frame: int, timebase: int) -> str:
    """``frame`` at ``timebase`` fps as ``HH:MM:SS:FF``, with hour rollover.

    Plain (non-drop) rollover math regardless of whether the sequence is
    "drop frame" per §4.2 -- PROTOCOL.md §5 is explicit that v1's EDL never
    computes real SMPTE drop-frame skipped-numbers; drop frame is declared
    only via the ``FCM:`` header line, and colons are used throughout (never
    the drop-frame ``;`` separator convention).
    """
    frames_per_hour = timebase * 3600
    frames_per_minute = timebase * 60
    hours, remainder = divmod(frame, frames_per_hour)
    hours %= 24
    minutes, remainder = divmod(remainder, frames_per_minute)
    seconds, frames = divmod(remainder, timebase)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def build_edl(sequence_name: str, fps_key: str, clips: Sequence[ClipSpec]) -> str:
    """The CMX3600 EDL for this timeline (PROTOCOL.md §5).

    Deterministic and pathless (EDL carries no absolute paths in its
    structured columns -- only in the ``* SOURCE FILE:`` comment): reel is
    always ``AX``, channel always ``V``, transition always ``C`` (§5), so
    the only per-event variables are the event number and the four
    timecodes. Record-in/out are the SAME back-to-back sequence positions
    :func:`build_xmeml` uses (:func:`_sequence_positions`), so a clip's
    record-in always equals the previous clip's record-out -- continuity
    across clips falls out of sharing this one position function rather
    than being asserted separately.

    Args:
        sequence_name: The ``TITLE:`` line.
        fps_key: One of :data:`RATE_TABLE`'s 8 keys.
        clips: Same order as :func:`build_xmeml`; must be non-empty.

    Returns:
        The complete ``.edl`` file content (with trailing newline). Event
        blocks are separated by one blank line each; there is no blank line
        after the final block.

    Raises:
        ValueError: *clips* is empty, or *fps_key* isn't a recognized rate.
    """
    _require_clips(clips, fps_key, "build_edl")
    timebase, _ntsc, drop_frame = RATE_TABLE[fps_key]
    sequence_fps = float(fps_key)
    positions = _sequence_positions(clips, sequence_fps)

    fcm = "DROP FRAME" if drop_frame else "NON-DROP FRAME"
    lines = [f"TITLE: {sequence_name}", f"FCM: {fcm}", ""]
    for index, (clip, (start, end, duration)) in enumerate(
        zip(clips, positions, strict=True), start=1
    ):
        src_in = _frames_to_timecode(0, timebase)
        src_out = _frames_to_timecode(duration, timebase)
        rec_in = _frames_to_timecode(start, timebase)
        rec_out = _frames_to_timecode(end, timebase)
        lines.append(f"{index:03d}  AX       V     C        {src_in} {src_out} {rec_in} {rec_out}")
        lines.append(f"* FROM CLIP NAME: {_basename(clip.path)}")
        lines.append(f"* SOURCE FILE: {clip.path}")
        if index != len(clips):
            lines.append("")

    return "\n".join(lines) + "\n"


def build_otio(sequence_name: str, fps_key: str, clips: Sequence[ClipSpec]) -> str:
    """Native OTIO JSON for this timeline (PROTOCOL.md §3.2 ``write_otio``).

    Soft dependency: ``opentimelineio`` is imported LAZILY, right here, and
    NOT caught -- a missing install surfaces as a plain :class:`ImportError`
    that :mod:`cprb.nodes_save` catches around its call to this function,
    turning it into the "otio skipped (not installed)" warning rather than
    failing the whole node (PROTOCOL.md §3.2). This module never imports
    ``opentimelineio`` at module scope, so the rest of ``timeline_write``
    (``build_xmeml``/``build_edl``, always available) is unaffected by
    whether it's installed.

    Builds one V1 video track via the public ``opentimelineio`` API
    (``Timeline``/``Track``/``Clip``/``ExternalReference`` +
    ``opentime.RationalTime``/``TimeRange``). Per OTIO semantics,
    ``Clip.source_range`` is expressed in the MEDIA REFERENCE's own time
    coordinates -- NOT the parent timeline's -- so each clip's
    ``source_range`` is the full source at its NATIVE fps, identical to the
    reference's ``available_range`` (v1 always cuts whole files; a
    sequence-fps range could exceed ``available_range`` for conformed rates
    and would be semantically wrong). Timeline placement needs no explicit
    start frames: OTIO derives it from track composition order, and each
    clip's real-time length is preserved automatically
    (``frames / native_fps`` -- the same seconds :func:`build_xmeml`'s §3.3
    math rounds into sequence frames). The SEQUENCE rate still appears in
    the document as the ``Timeline``'s ``global_start_time`` (frame 0 at the
    §4.2 sequence fps -- the same "starts at 00:00:00:00, at sequence rate"
    fact xmeml's ``<timecode>`` block encodes). ``target_url`` reuses
    :func:`_encode_pathurl` so every emitted format points at media with the
    same ``file://`` convention.

    Args:
        sequence_name: The ``Timeline``'s name.
        fps_key: One of :data:`RATE_TABLE`'s 8 keys.
        clips: Same order as :func:`build_xmeml`; must be non-empty.

    Returns:
        ``Timeline.to_json_string()`` -- the native ``.otio`` file content.

    Raises:
        ValueError: *clips* is empty, or *fps_key* isn't a recognized rate.
        ImportError: ``opentimelineio`` is not installed.
    """
    _require_clips(clips, fps_key, "build_otio")
    import opentimelineio as otio  # lazy + optional; see docstring.

    # VERIFY(spike-S4): whether Premiere (Beta) actually imports this .otio
    # and links its media is unconfirmed until SPIKES.md S4 runs live.
    sequence_fps = float(fps_key)
    rational_time = otio.opentime.RationalTime
    time_range = otio.opentime.TimeRange

    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    for clip in clips:
        # source_range is in the media reference's coordinates (docstring):
        # the full source at NATIVE fps, byte-identical to available_range.
        full_source = time_range(
            rational_time(0, clip.fps), rational_time(clip.frames, clip.fps)
        )
        media_reference = otio.schema.ExternalReference(
            target_url=_encode_pathurl(clip.path),
            available_range=full_source,
        )
        otio_clip = otio.schema.Clip(
            name=clip.name,
            media_reference=media_reference,
            source_range=full_source,
        )
        track.append(otio_clip)

    timeline = otio.schema.Timeline(
        name=sequence_name,
        global_start_time=rational_time(0, sequence_fps),
    )
    timeline.tracks.append(track)
    return timeline.to_json_string()
