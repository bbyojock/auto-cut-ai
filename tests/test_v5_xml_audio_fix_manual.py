"""Manual regression test for the Resolve Free XML export AUDIO bug fix.

Run: python3 tests/test_v5_xml_audio_fix_manual.py

Reproduces the exact bug report:

- source: "2026-09-11 17-16-13.mp4", 60 fps
- KEEP clips: 0-480, 1050-2760, 3270-3720, 6180-8220 (source frames)
- symptom: 1st KEEP clip's audio played fine in Resolve; every KEEP clip
  from the 2nd one onward played video normally but with silent audio.

Root cause (confirmed by inspecting the previously generated XML):

1. The shared ``<file>`` definition's ``<media><audio/>`` was empty --
   no real audio media info at all.
2. Video clipitem (``clipitem-vN``) and audio clipitem (``clipitem-aN``)
   for the same KEEP range had no ``<link>`` relationship, so Resolve had
   no way to know they were the same A/V clip once more than one pair
   referenced the same ``<file>``.
3. Audio clipitems had no ``<sourcetrack>``, so Resolve could not tell
   which source audio track to pull samples from.

This test asserts the fix directly against ``build_edit_xml``'s output:
every KEEP clip (not just the first) must produce a video/audio clipitem
pair with matching start/end/in/out, a <link> tying them together, a
<sourcetrack> on the audio clipitem, and non-empty audio media info on
the shared <file>.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davinci import edit_plan_applier
from davinci.resolve_xml_exporter import build_edit_xml
from models.edit_plan import EditPlan, EditPlanSegment
from utils.constants import EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


FPS = 60.0

# The exact KEEP ranges from the bug report, expressed in seconds (the
# EditPlan/edit_plan_applier layer works in seconds; frames are only a
# presentation detail of the XML exporter).
KEEP_FRAME_RANGES = [
    (0, 480),
    (1050, 2760),
    (3270, 3720),
    (6180, 8220),
]
keep_segments = [
    EditPlanSegment(start / FPS, end / FPS, EDIT_ACTION_KEEP, f"keep {i}")
    for i, (start, end) in enumerate(KEEP_FRAME_RANGES, start=1)
]
# edit_plan_applier derives keep_ranges as the complement of remove_ranges
# over the media duration, so the gaps between the reported KEEP ranges
# must be expressed as REMOVE segments to reproduce 4 separate KEEP
# clips (matching how a real EditPlan from this app would represent it).
remove_segments = [
    EditPlanSegment(KEEP_FRAME_RANGES[i][1] / FPS, KEEP_FRAME_RANGES[i + 1][0] / FPS, EDIT_ACTION_REMOVE, f"gap {i + 1}")
    for i in range(len(KEEP_FRAME_RANGES) - 1)
]
plan = EditPlan(
    target_length_seconds=KEEP_FRAME_RANGES[-1][1] / FPS,
    keep_segments=keep_segments,
    remove_segments=remove_segments,
)
resolved = edit_plan_applier.process_edit_plan(plan, media_duration_seconds=KEEP_FRAME_RANGES[-1][1] / FPS)

xml_text = build_edit_xml(
    resolved, Path("2026-09-11 17-16-13.mp4"), fps=FPS, width=1920, height=1080,
)
root = ET.fromstring(xml_text)

# ---------------------------------------------------------------------
# 1. XML structure validation
# ---------------------------------------------------------------------
check("XML parses cleanly", root is not None)

video_track = root.find("./sequence/media/video/track")
audio_track = root.find("./sequence/media/audio/track")
video_clips = video_track.findall("clipitem")
audio_clips = audio_track.findall("clipitem")

# ---------------------------------------------------------------------
# 2/3. Video/audio clipitem counts match (one pair per KEEP range)
# ---------------------------------------------------------------------
check("4 KEEP ranges -> 4 video clipitems", len(video_clips) == 4)
check("Video clipitem count == audio clipitem count", len(video_clips) == len(audio_clips))

# ---------------------------------------------------------------------
# The <file>'s <media><audio> now carries real samplecharacteristics
# (previously an empty <audio/>).
# ---------------------------------------------------------------------
file_audio = root.find("./sequence/media/video/track/clipitem/file/media/audio")
check("<file>'s <media><audio> is no longer empty", file_audio is not None and len(list(file_audio)) > 0)
check(
    "<file> audio media has real samplecharacteristics (samplerate/depth)",
    file_audio.findtext("samplecharacteristics/samplerate") is not None
    and file_audio.findtext("samplecharacteristics/depth") is not None,
)
check("<file> audio media has a channelcount", file_audio.findtext("channelcount") is not None)

# ---------------------------------------------------------------------
# 4. Each video/audio pair's start/end/in/out match exactly, for ALL
#    four KEEP ranges -- not just the first (this is the specific bug:
#    "두 번째 KEEP 클립부터 이후 오디오가 무음").
# ---------------------------------------------------------------------
for i, (v, a) in enumerate(zip(video_clips, audio_clips), start=1):
    for field in ("start", "end", "in", "out"):
        v_val, a_val = v.findtext(field), a.findtext(field)
        check(f"Pair {i}: <{field}> matches between video ({v_val}) and audio ({a_val})", v_val == a_val)

# Specifically verify clip 2's source In/Out from the bug report (1050/2760).
check(
    "Clip 2 video: source In=1050, Out=2760",
    video_clips[1].findtext("in") == "1050" and video_clips[1].findtext("out") == "2760",
)
check(
    "Clip 2 audio: source In=1050, Out=2760 (matches video)",
    audio_clips[1].findtext("in") == "1050" and audio_clips[1].findtext("out") == "2760",
)

# ---------------------------------------------------------------------
# Every audio clipitem has a <sourcetrack> pointing at the source file's
# audio track 1 (previously missing entirely).
# ---------------------------------------------------------------------
for i, a in enumerate(audio_clips, start=1):
    sourcetrack = a.find("sourcetrack")
    check(f"Audio clipitem {i} has a <sourcetrack>", sourcetrack is not None)
    if sourcetrack is not None:
        check(
            f"Audio clipitem {i} <sourcetrack> is mediatype=audio, trackindex=1",
            sourcetrack.findtext("mediatype") == "audio" and sourcetrack.findtext("trackindex") == "1",
        )

# ---------------------------------------------------------------------
# Every video/audio pair (not just clip 1) is tied together with <link>
# entries referencing both clipitem ids -- this is what actually fixes
# "silent from the 2nd clip on".
# ---------------------------------------------------------------------
for i, (v, a) in enumerate(zip(video_clips, audio_clips), start=1):
    v_id, a_id = v.get("id"), a.get("id")
    v_links = v.findall("link")
    a_links = a.findall("link")
    check(f"Pair {i}: video clipitem has 2 <link> entries", len(v_links) == 2)
    check(f"Pair {i}: audio clipitem has 2 <link> entries", len(a_links) == 2)
    v_refs = {link.findtext("linkclipref") for link in v_links}
    a_refs = {link.findtext("linkclipref") for link in a_links}
    check(f"Pair {i}: video clipitem's <link> references both {v_id} and {a_id}", v_refs == {v_id, a_id})
    check(f"Pair {i}: audio clipitem's <link> references both {v_id} and {a_id}", a_refs == {v_id, a_id})

# ---------------------------------------------------------------------
# Only the FIRST clipitem pair defines the full <file> block; every
# later pair reuses it by id="file-1" reference only (unchanged
# behavior -- must not regress into duplicate <file> definitions).
# ---------------------------------------------------------------------
full_file_defs = [c for c in video_clips if c.find("file/media") is not None]
check("Only clip 1's video clipitem carries the full <file> definition", len(full_file_defs) == 1)
check("Clip 1 is the one with the full <file> definition", full_file_defs[0] is video_clips[0])
for a in audio_clips:
    check("Audio clipitems always reference <file id='file-1'/> only (no duplicate <media>)", a.find("file/media") is None)

print(
    "\nAll audio-fix regression checks passed: every KEEP clip (including the previously-silent "
    "2nd/3rd/4th clips) now produces a properly linked video+audio clipitem pair with matching "
    "In/Out, a <sourcetrack>, and real <file> audio media info. Real DaVinci Resolve 20 Free "
    "Import Timeline behavior still needs manual verification -- see tests/fixtures/README.md."
)
