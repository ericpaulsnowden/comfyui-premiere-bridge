"""Tests for :mod:`cprb.timeline_read` (PROTOCOL.md §6.1).

Two sources of XML: the handcrafted fixtures under ``tests/fixtures/`` (the
"shaped like a real noisy Premiere export" and "clean §4-writer-shaped"
cases), and small inline xmeml strings for the narrower, self-contained
behaviors (the ``-1``-span resolution math, the track tie-break rule) that
don't need a whole fixture file to exercise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cprb.timeline_read import TimelineParseError, parse_timeline

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_text(filename: str) -> str:
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def _find(shots: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """The single shot named *name*, or fails loudly if there isn't exactly one."""
    matches = [shot for shot in shots if shot["name"] == name]
    assert len(matches) == 1, f"expected exactly one shot named {name!r}, found {len(matches)}"
    return matches[0]


def _wrap(tracks_xml: str, timebase: int = 24, ntsc: str = "FALSE") -> str:
    """A minimal one-sequence xmeml document wrapping *tracks_xml* verbatim under ``<video>``."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence>
    <name>Inline Test Sequence</name>
    <rate><timebase>{timebase}</timebase><ntsc>{ntsc}</ntsc></rate>
    <media>
      <video>
{tracks_xml}
      </video>
    </media>
  </sequence>
</xmeml>"""


# --- fixture (a): clean_two_clip.xml -----------------------------------------------


def test_clean_two_clip_sequence_metadata():
    parsed = parse_timeline(_fixture_text("clean_two_clip.xml"))
    assert parsed.sequence_name == "Clean Two Clip Sequence"
    assert parsed.sequence_fps == pytest.approx(24.0)
    assert len(parsed.shots) == 2


def test_clean_two_clip_shot_contents_and_order():
    parsed = parse_timeline(_fixture_text("clean_two_clip.xml"))
    intro, outro = parsed.shots

    # Neither clipitem's <file> in this fixture carries a
    # <media><video><samplecharacteristics> block, so width/height default
    # to 0 -- the "absent" half of the width/height contract (PROTOCOL.md
    # §6.1); the "present" half is covered against noisy_premiere_export.xml
    # and the inline tests below.
    assert intro == {
        "name": "intro.mp4",
        "path": "/Users/eric/media/intro.mp4",
        "start": 0,
        "end": 48,
        "in": 0,
        "out": 48,
        "sequence_fps": pytest.approx(24.0),
        "source_fps": pytest.approx(24.0),
        "enabled": True,
        "width": 0,
        "height": 0,
    }
    assert outro == {
        "name": "outro.mp4",
        "path": "/Users/eric/media/outro.mp4",
        "start": 48,
        "end": 120,
        "in": 0,
        "out": 72,
        "sequence_fps": pytest.approx(24.0),
        "source_fps": pytest.approx(24.0),
        "enabled": True,
        "width": 0,
        "height": 0,
    }


# --- fixture (b): noisy_premiere_export.xml ----------------------------------------


def test_noisy_shot_count_excludes_generatoritem_and_audio():
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    # 4 video clipitems (2 tracks); the generatoritem (color matte) and the
    # audio track's clipitem are both real elements in the file but neither
    # is a video <clipitem>, so neither may appear.
    assert len(parsed.shots) == 4
    names = {shot["name"] for shot in parsed.shots}
    assert "Black Video" not in names  # <generatoritem>, not a <clipitem>
    assert "dialogue.wav" not in names  # audio track, ignored entirely


def test_noisy_ordering_across_two_tracks():
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    # track 1 (V1): Interview A @0, Bad Take @90, Interview A (reprise) @150.
    # track 2 (V2): B-Roll Overlay @60, overlapping both of the above.
    # Ascending start, interleaved across tracks:
    assert [shot["name"] for shot in parsed.shots] == [
        "Interview A",
        "B-Roll Overlay",
        "Bad Take",
        "Interview A (reprise)",
    ]


def test_noisy_disabled_clip_is_kept_and_flagged():
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    bad_take = _find(parsed.shots, "Bad Take")
    assert bad_take["enabled"] is False


def test_noisy_enabled_absent_defaults_true():
    # clipitem-4 ("B-Roll Overlay") carries no <enabled> element at all.
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    broll = _find(parsed.shots, "B-Roll Overlay")
    assert broll["enabled"] is True


def test_noisy_file_id_reference_resolution():
    # clipitem-3 ("Interview A (reprise)") has a self-closing <file id="file-1"/>
    # referring back to clipitem-1's full <file id="file-1"> definition -- both
    # the path AND the file's rate must resolve through that reference.
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    reprise = _find(parsed.shots, "Interview A (reprise)")
    assert reprise["path"] == "D:/Media/Interview Take 1.mov"
    assert reprise["source_fps"] == pytest.approx(30.0)
    assert reprise["in"] == 90  # non-zero in -- also exercised by test_nodes_load's GetShot test
    assert reprise["out"] == 150


@pytest.mark.parametrize(
    ("shot_name", "expected_path"),
    [
        # file://localhost/D%3a/... + %20-encoded spaces -> Windows drive path.
        ("Interview A", "D:/Media/Interview Take 1.mov"),
        # file://<host>/... (non-localhost authority) -> UNC form.
        ("Bad Take", "\\\\nas02\\share\\footage\\badtake.mov"),
        # file://localhost/<posix path>, no drive letter -> unchanged.
        ("B-Roll Overlay", "/Volumes/Footage/broll_001.mov"),
    ],
)
def test_noisy_pathurl_forms(shot_name: str, expected_path: str):
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    assert _find(parsed.shots, shot_name)["path"] == expected_path


def test_noisy_sequence_ntsc_2997_rate():
    # sequence <rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate> -> 29.97.
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    assert parsed.sequence_fps == pytest.approx(29.97, abs=1e-3)
    # "Bad Take"'s file has no <rate> at all, so it falls all the way back
    # to the sequence rate -- exercises the same 30/TRUE math a second way.
    bad_take = _find(parsed.shots, "Bad Take")
    assert bad_take["source_fps"] == pytest.approx(29.97, abs=1e-3)


def test_noisy_source_fps_rate_tiers():
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    # Tier 2: clipitem has no <rate> of its own -> falls back to the FILE's
    # <rate> (30/FALSE = 30.0), not the sequence's (29.97).
    interview_a = _find(parsed.shots, "Interview A")
    assert interview_a["source_fps"] == pytest.approx(30.0)
    # Tier 1: clipitem's OWN <rate> (24/FALSE = 24.0) wins even though its
    # file's <rate> says something else (30/TRUE = 29.97).
    broll = _find(parsed.shots, "B-Roll Overlay")
    assert broll["source_fps"] == pytest.approx(24.0)


def test_noisy_width_height_from_file_samplecharacteristics():
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    # file-1 carries <media><video><samplecharacteristics><width>1920</width>
    # <height>1080</height></samplecharacteristics></video></media>.
    interview_a = _find(parsed.shots, "Interview A")
    assert interview_a["width"] == 1920
    assert interview_a["height"] == 1080
    # "Interview A (reprise)" references file-1 via a self-closing
    # <file id="file-1"/> -- dimensions must resolve through the SAME
    # registry entry _resolve_source_fps already uses for source_fps.
    reprise = _find(parsed.shots, "Interview A (reprise)")
    assert reprise["width"] == 1920
    assert reprise["height"] == 1080
    # file-2 ("Bad Take") and file-3 ("B-Roll Overlay") carry no
    # <media><video><samplecharacteristics> at all -- absent defaults to 0.
    bad_take = _find(parsed.shots, "Bad Take")
    assert bad_take["width"] == 0
    assert bad_take["height"] == 0
    broll = _find(parsed.shots, "B-Roll Overlay")
    assert broll["width"] == 0
    assert broll["height"] == 0


def test_noisy_skip_disabled_semantics_at_parse_level():
    # timeline_read itself never filters -- PremiereLoadTimeline's
    # skip_disabled widget does that. Confirms the disabled clip really is
    # present in the raw parsed list for the node layer to filter.
    parsed = parse_timeline(_fixture_text("noisy_premiere_export.xml"))
    assert any(shot["name"] == "Bad Take" and not shot["enabled"] for shot in parsed.shots)


# --- fixture (c): empty_sequence.xml (error case) -----------------------------------


def test_empty_sequence_raises_clear_error():
    with pytest.raises(TimelineParseError, match="clipitem"):
        parse_timeline(_fixture_text("empty_sequence.xml"))


# --- malformed input -----------------------------------------------------------------


def test_malformed_xml_raises_clear_error():
    with pytest.raises(TimelineParseError, match="XML"):
        parse_timeline("<xmeml version=<this is not well-formed")


def test_valid_xml_without_a_sequence_raises_clear_error():
    with pytest.raises(TimelineParseError, match="sequence"):
        parse_timeline("<root><foo>not a timeline at all</foo></root>")


# --- -1 start/end resolution (PROTOCOL.md §6.1) --------------------------------------


def test_minus_one_span_resolved_from_own_in_out():
    # Clip B's <end> and clip C's <start> are recorded as -1, exactly as
    # real Premiere does for the two clips flanking a transition (see
    # cprb.timeline_read's module docstring for the real sample this is
    # modeled on). Both are recoverable from the clip's OWN in/out duration.
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-a">
            <name>Clip A</name>
            <start>0</start><end>100</end><in>0</in><out>100</out>
          </clipitem>
          <clipitem id="clipitem-b">
            <name>Clip B</name>
            <start>100</start><end>-1</end><in>0</in><out>50</out>
          </clipitem>
          <clipitem id="clipitem-c">
            <name>Clip C</name>
            <start>-1</start><end>200</end><in>10</in><out>60</out>
          </clipitem>
        </track>
        <track>
          <clipitem id="clipitem-d">
            <name>Clip D</name>
            <start>-1</start><end>-1</end><in>5</in><out>5</out>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)

    assert _find(parsed.shots, "Clip B")["end"] == 150  # 100 + (50 - 0)
    assert _find(parsed.shots, "Clip C")["start"] == 150  # 200 - (60 - 10)

    # Clip D is alone on its track (no neighbor to fall back on) and its own
    # in/out give a zero duration (no self-resolution either) -- both ends
    # stay literal -1, which is itself the "flag in summary" PROTOCOL.md
    # §6.1 asks for (see cprb.nodes_load._summary_line).
    clip_d = _find(parsed.shots, "Clip D")
    assert clip_d["start"] == -1
    assert clip_d["end"] == -1


def test_transitionitem_is_ignored_not_a_shot():
    # <transitionitem> is a sibling of <clipitem> directly under <track> in
    # real Premiere exports (confirmed real shape, cprb.timeline_read's
    # module docstring) -- it must never itself become a shot, and must not
    # disturb parsing of the clips flanking it.
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-a">
            <name>Clip A</name>
            <start>0</start><end>100</end><in>0</in><out>100</out>
          </clipitem>
          <transitionitem>
            <start>90</start>
            <end>110</end>
            <effect>
              <name>Cross Dissolve</name>
              <effectid>Cross Dissolve</effectid>
              <effectcategory>Dissolve</effectcategory>
              <effecttype>transition</effecttype>
            </effect>
          </transitionitem>
          <clipitem id="clipitem-b">
            <name>Clip B</name>
            <start>100</start><end>200</end><in>0</in><out>100</out>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert [shot["name"] for shot in parsed.shots] == ["Clip A", "Clip B"]


def test_compound_clip_nested_sequence_is_not_recursed_into():
    # A clipitem can itself be a "nested sequence"/compound clip: instead of
    # a <file>, its payload is a WHOLE nested <sequence> with its own
    # <media>/<video>/<track>/<clipitem> (confirmed real shape: OTIO's
    # premiere_example.xml, clipitem-5). The outer clipitem is one shot on
    # THIS timeline; the inner nested sequence's own clipitem must not leak
    # into this timeline's shot list as a second, unrelated shot -- this is
    # exactly why parse_timeline's track/clipitem walk uses direct-child
    # lookups instead of a recursive ".//clipitem" search.
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-outer">
            <name>Nested Sequence Clip</name>
            <start>0</start><end>50</end><in>0</in><out>50</out>
            <sequence id="sequence-2">
              <name>Inner Sequence</name>
              <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
              <media>
                <video>
                  <track>
                    <clipitem id="clipitem-inner">
                      <name>Should Not Appear</name>
                      <start>0</start><end>50</end><in>0</in><out>50</out>
                    </clipitem>
                  </track>
                </video>
              </media>
            </sequence>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert [shot["name"] for shot in parsed.shots] == ["Nested Sequence Clip"]
    # No <file> on the outer clip (its payload is the nested sequence, not a
    # file) -- path degrades to "" rather than raising.
    assert parsed.shots[0]["path"] == ""


def test_track_order_wins_ties_on_start():
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-x">
            <name>Track1 Clip</name>
            <start>50</start><end>100</end><in>0</in><out>50</out>
          </clipitem>
        </track>
        <track>
          <clipitem id="clipitem-y">
            <name>Track2 Clip</name>
            <start>50</start><end>100</end><in>0</in><out>50</out>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert [shot["name"] for shot in parsed.shots] == ["Track1 Clip", "Track2 Clip"]


def test_ntsc_23976_rate_math():
    # A second ntsc data point beyond the required 30/TRUE -> 29.97: confirms
    # the *1000/1001 math isn't hardcoded to one timebase.
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-z">
            <name>NTSC24 Clip</name>
            <rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate>
            <start>0</start><end>48</end><in>0</in><out>48</out>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert _find(parsed.shots, "NTSC24 Clip")["source_fps"] == pytest.approx(23.976, abs=1e-3)


# --- width/height (PROTOCOL.md §6.1, added 2026-07-19) ------------------------------


def test_width_height_read_from_file_samplecharacteristics():
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-1">
            <name>Clip A</name>
            <start>0</start><end>48</end><in>0</in><out>48</out>
            <file id="file-1">
              <name>clip_a.mov</name>
              <media>
                <video>
                  <samplecharacteristics>
                    <width>1280</width>
                    <height>720</height>
                  </samplecharacteristics>
                </video>
              </media>
            </file>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert parsed.shots[0]["width"] == 1280
    assert parsed.shots[0]["height"] == 720


def test_width_height_absent_defaults_to_zero():
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-1">
            <name>Clip A (file, no samplecharacteristics)</name>
            <start>0</start><end>48</end><in>0</in><out>48</out>
            <file id="file-1">
              <name>clip_a.mov</name>
            </file>
          </clipitem>
          <clipitem id="clipitem-2">
            <name>Clip B (no file at all)</name>
            <start>48</start><end>96</end><in>0</in><out>48</out>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert parsed.shots[0]["width"] == 0
    assert parsed.shots[0]["height"] == 0
    assert parsed.shots[1]["width"] == 0
    assert parsed.shots[1]["height"] == 0


def test_width_height_resolved_through_file_id_reference():
    # Same id-reference convention _resolve_source_fps relies on (module
    # docstring) -- clipitem-2's self-closing <file id="file-1"/> must
    # resolve dimensions through the SAME registered file-1 definition.
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-1">
            <name>Clip A</name>
            <start>0</start><end>48</end><in>0</in><out>48</out>
            <file id="file-1">
              <name>clip_a.mov</name>
              <media>
                <video>
                  <samplecharacteristics>
                    <width>1920</width>
                    <height>1080</height>
                  </samplecharacteristics>
                </video>
              </media>
            </file>
          </clipitem>
          <clipitem id="clipitem-2">
            <name>Clip A (reprise)</name>
            <start>48</start><end>96</end><in>0</in><out>48</out>
            <file id="file-1"/>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert parsed.shots[1]["width"] == 1920
    assert parsed.shots[1]["height"] == 1080


def test_missing_name_falls_back_to_file_name_then_clip_n():
    text = _wrap(
        """
        <track>
          <clipitem id="clipitem-1">
            <start>0</start><end>24</end><in>0</in><out>24</out>
            <file id="file-1">
              <name>only_the_file_has_a_name.mov</name>
            </file>
          </clipitem>
          <clipitem id="clipitem-2">
            <start>24</start><end>48</end><in>0</in><out>24</out>
          </clipitem>
        </track>
        """
    )
    parsed = parse_timeline(text)
    assert parsed.shots[0]["name"] == "only_the_file_has_a_name.mov"
    assert parsed.shots[1]["name"] == "clip 2"  # no <name> anywhere -- falls to "clip N"
