"""Parses a Premiere-exported Final Cut Pro XML (xmeml) timeline (PROTOCOL.md §6.1).

PURE stdlib -- only :mod:`xml.etree.ElementTree` and :mod:`urllib.parse`.
No ComfyUI imports, no filesystem access: the node layer
(``cprb/nodes_load.py``) reads the file and hands this module its text, so
:func:`parse_timeline` is trivially testable with inline XML strings as well
as the fixtures under ``tests/fixtures/``.

Real Premiere exports are considerably noisier than the xmeml this pack's own
writer emits (PROTOCOL.md §4). Researched against real Premiere-exported
xmeml samples (not project data of ours -- the fixtures under
``tests/fixtures/`` are handcrafted) before writing this parser:

* `OpenTimelineIO/otio-fcp-adapter <https://github.com/OpenTimelineIO/otio-fcp-adapter>`_,
  ``tests/sample_data/premiere_example.xml``, ``premiere_enable_property.xml``
  and ``premiere_generators.xml`` -- real Premiere Pro FCP7 XML exports used
  as OpenTimelineIO's own adapter test fixtures. This is where every quirk
  handled below was confirmed:

  - ``pproTicksIn``/``pproTicksOut`` appear as siblings of the frame-based
    ``in``/``out`` a clipitem always also carries -- noise to ignore, never
    the field to read.
  - A clipitem's OWN ``<rate>`` can genuinely diverge from its ``<file>``'s
    rate (``premiere_example.xml``, ``clipitem-1``: clip ``<rate><timebase>
    15</timebase></rate>`` vs. its file's ``<rate><timebase>30</timebase>
    </rate>``) -- see :func:`_resolve_source_fps`.
  - ``<file id="X"/>`` (no children) is a self-closing reference to an
    earlier full ``<file id="X">...</file>`` definition -- see
    :func:`_resolve_file_element`.
  - ``<generatoritem>`` (color mattes/bars/slugs) and ``<transitionitem>``
    are siblings of ``<clipitem>`` directly under the same ``<track>``, not
    clipitems themselves (``premiere_generators.xml``).
  - A ``<file>`` can carry no ``<pathurl>`` at all, only ``<mediaSource>
    Slug</mediaSource>`` -- Premiere's internal name for a generator clip
    with no on-disk media (``premiere_example.xml``, ``file-4``).
  - A compound/nested clip: a ``<clipitem>`` whose payload is a whole
    nested ``<sequence>`` (with its own ``<media>/<video>/<track>/
    <clipitem>``) instead of a ``<file>`` (``premiere_example.xml``,
    ``clipitem-5``) -- see :func:`parse_timeline`'s docstring for why the
    track/clipitem walk is deliberately non-recursive.
  - ``<start>``/``<end>`` recorded as ``-1`` on the two clipitems immediately
    adjacent to a ``<transitionitem>`` (``premiere_example.xml``,
    ``clipitem-4``/``clipitem-11``/``clipitem-12``) -- see
    :func:`_resolve_track_spans`.

* A gist of a full Premiere export
  (https://gist.github.com/boredstiff/63c9d4f8f7bca48c3e5441326ae8ce69) --
  cross-check for the same ``pproTicksIn``/``pproTicksOut`` + frame ``in``/
  ``out`` co-occurrence and multi-track stereo audio shape.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit


class TimelineParseError(ValueError):
    """*text* is not a usable Premiere-exported FCP7 XML timeline.

    Covers both failure modes PROTOCOL.md §6.1 calls out: XML that doesn't
    even parse, and XML that parses fine but has no ``<sequence>`` (or no
    video ``<clipitem>`` anywhere on the first one) -- "a file with zero
    video clipitems errors loudly (wrong file, not an empty result)".
    """


@dataclass
class ParsedTimeline:
    """:func:`parse_timeline`'s return value.

    Attributes:
        sequence_name: The first ``<sequence>``'s ``<name>`` text, or ``""``
            if absent/blank -- tolerant per §6.1; nothing downstream
            requires a non-empty sequence name today.
        sequence_fps: The first ``<sequence>``'s own frame rate (PROTOCOL.md
            §4.2 timebase+ntsc math, inverted), independent of any single
            clip's rate. Falls back to ``24.0`` if the sequence carries no
            parseable ``<rate>`` at all.
        shots: One dict per video ``<clipitem>``, ascending ``start``
            (track document-order on ties). Keys per PROTOCOL.md §6.2: the
            frozen ``name, path, start, end, in, out, sequence_fps,
            source_fps, enabled``, plus (added 2026-07-19, an append-only
            §8 extension) ``width``/``height`` from the clip's resolved
            ``<file><media><video><samplecharacteristics>`` (``0``/``0``
            when absent). Includes disabled clips (``enabled: False``) --
            filtering them out is ``PremiereLoadTimeline``'s
            ``skip_disabled`` widget's job, not this module's.
    """

    sequence_name: str
    sequence_fps: float
    shots: list[dict[str, Any]]


def parse_timeline(text: str) -> ParsedTimeline:
    """Parse *text* (a whole Premiere-exported FCP7 XML document) into a :class:`ParsedTimeline`.

    Per PROTOCOL.md §6.1: every ``<clipitem>`` on every VIDEO track of the
    FIRST ``<sequence>`` in the document, ascending ``start`` (track order
    on ties). Audio tracks, ``<transitionitem>``, and ``<generatoritem>``
    (color mattes/bars -- confirmed real shape, not a ``<clipitem>`` at all,
    see module docstring) are all ignored by construction: none of them is
    the tag this function ever asks for.

    "The first ``<sequence>``" is found via a recursive, document-order
    search (:meth:`~xml.etree.ElementTree.Element.iter`) rather than a
    direct-child-only lookup, so a project/bin-wrapped export
    (``<xmeml><project><children><bin>...<sequence>``) resolves the same as
    a bare ``<xmeml><sequence>``. Everything read OFF that sequence (its
    ``<media>/<video>/<track>/<clipitem>`` walk) then switches to
    direct-child-only lookups (``Element.find``/``findall`` with a bare tag
    name, never ``.//``) deliberately: a compound/nested clip's
    ``<clipitem>`` can itself contain a whole nested ``<sequence>`` with its
    own ``<track>``/``<clipitem>`` elements (confirmed real shape, module
    docstring), and a recursive walk would wrongly pull those deeper,
    unrelated clips into THIS timeline's shot list.

    Args:
        text: The full contents of a ``.xml`` file exported via Premiere's
            File > Export > Final Cut Pro XML (or a hand-crafted xmeml
            string in the same shape, e.g. in tests).

    Returns:
        The parsed timeline.

    Raises:
        TimelineParseError: *text* isn't well-formed XML, or it is but no
            ``<sequence>`` -- or no video ``<clipitem>`` anywhere on the
            first one -- was found.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise TimelineParseError(f"Not well-formed XML: {exc}") from exc

    sequence = next(root.iter("sequence"), None)
    if sequence is None:
        raise TimelineParseError(
            "No <sequence> element found -- this doesn't look like a Final Cut "
            "Pro XML export (Premiere: File > Export > Final Cut Pro XML)."
        )

    sequence_name = _text(sequence.find("name"))
    sequence_fps = _parse_rate(sequence.find("rate"), default=24.0)

    raw_shots = _collect_raw_shots(sequence, sequence_fps)
    if not raw_shots:
        raise TimelineParseError(
            "No <clipitem> elements found on any video track of the first "
            "<sequence> -- wrong file, an audio-only sequence, or an empty timeline."
        )

    raw_shots.sort(key=lambda record: (record["start"], record["_track_index"]))

    shots: list[dict[str, Any]] = []
    for i, record in enumerate(raw_shots, start=1):
        name = record["name"] or record["_file_name"] or f"clip {i}"
        shots.append(
            {
                "name": name,
                "path": record["path"],
                "start": record["start"],
                "end": record["end"],
                "in": record["in"],
                "out": record["out"],
                "sequence_fps": sequence_fps,
                "source_fps": record["source_fps"],
                "enabled": record["enabled"],
                "width": record["width"],
                "height": record["height"],
            }
        )

    return ParsedTimeline(sequence_name=sequence_name, sequence_fps=sequence_fps, shots=shots)


def _collect_raw_shots(sequence: ET.Element, sequence_fps: float) -> list[dict[str, Any]]:
    """Every ``<clipitem>`` on every direct video ``<track>`` of *sequence*, doc order.

    A single ``file_registry`` is threaded across every track (in document
    order) so a ``<file id="X"/>`` self-closing reference on track 2
    resolves against a full ``<file id="X">`` definition first seen on
    track 1 -- confirmed real shape, module docstring.
    """
    video = sequence.find("media/video")
    if video is None:
        return []

    file_registry: dict[str, ET.Element] = {}
    raw_shots: list[dict[str, Any]] = []
    for track_index, track in enumerate(video.findall("track")):
        track_raw = [
            _extract_clip(clip, file_registry, sequence_fps) for clip in track.findall("clipitem")
        ]
        _resolve_track_spans(track_raw)
        for record in track_raw:
            record["_track_index"] = track_index
        raw_shots.extend(track_raw)
    return raw_shots


def _extract_clip(
    clip: ET.Element, file_registry: dict[str, ET.Element], sequence_fps: float
) -> dict[str, Any]:
    """One ``<clipitem>``'s raw fields.

    Everything :func:`parse_timeline` needs before the cross-track sort and
    the final ``name``/``"clip N"`` fallback -- both of which need the
    complete shot list first, so they happen after this returns.
    """
    file_el = _resolve_file_element(clip, file_registry)
    file_name = _text(file_el.find("name")) if file_el is not None else ""
    pathurl = _text(file_el.find("pathurl")) if file_el is not None else ""
    width, height = _resolve_dimensions(file_el)

    return {
        "name": _text(clip.find("name")),
        "_file_name": file_name,
        "path": _decode_pathurl(pathurl),
        "start": _int_or(clip.find("start"), -1),
        "end": _int_or(clip.find("end"), -1),
        "in": _int_or(clip.find("in"), 0),
        "out": _int_or(clip.find("out"), 0),
        "source_fps": _resolve_source_fps(clip, file_el, sequence_fps),
        "enabled": _text(clip.find("enabled")).upper() != "FALSE",
        "width": width,
        "height": height,
    }


def _resolve_source_fps(clip: ET.Element, file_el: ET.Element | None, sequence_fps: float) -> float:
    """clipitem rate, else file rate, else sequence rate.

    Checked in that order because a real clipitem's own ``<rate>`` can
    genuinely diverge from its file's (module docstring: OTIO's
    ``premiere_example.xml``, ``clipitem-1`` is timebase 15 against a
    timebase-30 file) -- consulting it first is a strict refinement of
    PROTOCOL.md §6.1's own summary ("file rate when present, else sequence
    rate"), which doesn't separately call out this tier because its prose
    is describing the common case, not excluding this one.
    """
    clip_rate = clip.find("rate")
    if clip_rate is not None:
        return _parse_rate(clip_rate, default=sequence_fps)
    file_rate = file_el.find("rate") if file_el is not None else None
    if file_rate is not None:
        return _parse_rate(file_rate, default=sequence_fps)
    return sequence_fps


def _resolve_dimensions(file_el: ET.Element | None) -> tuple[int, int]:
    """``(width, height)`` from *file_el*'s ``<media><video><samplecharacteristics>``.

    *file_el* is the SAME element :func:`_resolve_source_fps` already
    resolved through the id-reference registry (module docstring;
    :func:`_resolve_file_element`) -- no separate lookup, so a clip whose
    ``<file id="X"/>`` is a self-closing reference gets the referenced
    definition's dimensions exactly like it gets that definition's rate.
    ``(0, 0)`` when *file_el* is ``None`` (a compound/nested-sequence clip,
    or a ``<file>`` with only ``<mediaSource>``, module docstring) or the
    export simply omits the block -- tolerant per §6.1, same convention as
    every other optional field this parser reads.
    """
    if file_el is None:
        return 0, 0
    samplechar = file_el.find("media/video/samplecharacteristics")
    if samplechar is None:
        return 0, 0
    return _int_or(samplechar.find("width"), 0), _int_or(samplechar.find("height"), 0)


def _resolve_file_element(
    clip: ET.Element, file_registry: dict[str, ET.Element]
) -> ET.Element | None:
    """The ``<file>`` element that actually describes *clip*'s source, if any.

    Handles xmeml's id-reference convention (confirmed real shape, module
    docstring): a ``<file id="X">`` with children is a DEFINITION
    (registered here for later reuse); a bare, childless ``<file id="X"/>``
    is a REFERENCE to a definition seen earlier in document order. A
    reference to an id never seen yet (shouldn't happen per the convention,
    but tolerated per §6.1) degrades to ``None`` -- the same as a clip with
    no ``<file>`` at all (a compound/nested-sequence clip, module
    docstring).
    """
    file_el = clip.find("file")
    if file_el is None:
        return None
    file_id = file_el.get("id")
    if len(file_el) == 0:
        return file_registry.get(file_id) if file_id else None
    if file_id:
        file_registry[file_id] = file_el
    return file_el


def _resolve_track_spans(track_raw: list[dict[str, Any]]) -> None:
    """Fill in ``-1`` ``start``/``end`` placeholders, in place, per PROTOCOL.md §6.1.

    Premiere writes ``-1`` for whichever end of a clip touches a
    ``<transitionitem>`` (confirmed real shape: OTIO's
    ``premiere_example.xml`` has three such clips). The PRIMARY resolution
    uses the clip's own, always-reliable ``in``/``out``: duration = out -
    in is invariant across a transition, only the timeline position of the
    shared edge is ambiguous. Verified against that same real sample:
    recomputing the missing boundary this way reproduces the adjacent
    ``<transitionitem>``'s own recorded edge exactly, in both directions
    (``clipitem-4``'s recovered ``end`` == the following transition's
    ``end``; ``clipitem-12``'s recovered ``start`` == the preceding
    transition's ``start``).

    The FALLBACK -- PROTOCOL.md §6.1's "resolve from neighbors when
    possible" -- copies the adjacent clip's already-resolved boundary on
    the same track, in document order, for the rarer case where a clip's
    own ``in``/``out`` can't supply a usable duration. Anything still
    unresolved after both passes is left as literal ``-1`` -- itself the
    flag (never a valid frame number) that ``PremiereLoadTimeline`` surfaces
    per-shot in its ``summary`` output.
    """
    for record in track_raw:
        start, end = record["start"], record["end"]
        if start == -1 and end == -1:
            continue  # neither edge known; nothing to derive a duration from
        duration = record["out"] - record["in"]
        if duration <= 0:
            continue  # no usable duration; leave to the neighbor-copy pass below
        if start == -1:
            record["start"] = end - duration
        elif end == -1:
            record["end"] = start + duration

    for i, record in enumerate(track_raw):
        if record["start"] == -1 and i > 0 and track_raw[i - 1]["end"] != -1:
            record["start"] = track_raw[i - 1]["end"]
        if record["end"] == -1 and i < len(track_raw) - 1 and track_raw[i + 1]["start"] != -1:
            record["end"] = track_raw[i + 1]["start"]


def _decode_pathurl(pathurl: str) -> str:
    r"""PROTOCOL.md §4.3 pathurl decoding, tolerant of the forms real Premiere emits.

    - ``file://localhost/C%3a/renders/shot%2001.mp4`` (this pack's own §4.3
      writer output, and the form every real sample this parser was
      researched against uses) -> ``C:/renders/shot 01.mp4``.
    - ``file:///Users/eric/out.mp4`` (empty authority, same meaning as
      ``localhost``) / ``file://localhost/Users/eric/out.mp4`` ->
      ``/Users/eric/out.mp4``.
    - ``file://nas/share/clip.mov`` (UNC, host in the authority slot, §4.3
      UNCONFIRMED-by-spike form) -> ``\\nas\share\clip.mov``.

    Percent-decoding (``%20`` -> space, etc.) applies to every form. Returns
    ``""`` for an empty/missing *pathurl* (a compound/nested-sequence clip,
    or a ``<file>`` that only carries ``<mediaSource>`` -- e.g. Premiere's
    ``Slug`` generator clips, confirmed real shape, module docstring)
    rather than raising: PROTOCOL.md §6.1 is explicit that missing optional
    metadata never fails the parse.
    """
    if not pathurl:
        return ""
    parsed = urlsplit(pathurl)
    if parsed.scheme != "file":
        return unquote(pathurl)

    host = parsed.netloc
    path = unquote(parsed.path)
    if host and host.lower() != "localhost":
        return "\\\\" + host + "\\" + path.lstrip("/").replace("/", "\\")
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        return path[1:]  # "/C:/..." -> "C:/..." (drive-letter-as-first-segment form)
    return path


def _text(element: ET.Element | None) -> str:
    """*element*'s stripped text, or ``""`` if the element is ``None``/empty."""
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def _int_or(element: ET.Element | None, default: int) -> int:
    """*element*'s text as an int, or *default* if missing/blank/unparseable."""
    text = _text(element)
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _parse_rate(rate_el: ET.Element | None, default: float) -> float:
    """A ``<rate><timebase>/<ntsc></rate>`` element as fps (PROTOCOL.md §4.2, inverted).

    ``timebase`` T with ``ntsc`` TRUE means fps = T * 1000/1001 (23.976,
    29.97, 59.94); ``ntsc`` FALSE/absent means fps = T exactly. Falls back
    to *default* if *rate_el* is ``None`` or its ``<timebase>`` is
    missing/blank/unparseable.
    """
    if rate_el is None:
        return default
    timebase_text = _text(rate_el.find("timebase"))
    if not timebase_text:
        return default
    try:
        timebase = float(timebase_text)
    except ValueError:
        return default
    if _text(rate_el.find("ntsc")).upper() == "TRUE":
        return timebase * 1000.0 / 1001.0
    return timebase
