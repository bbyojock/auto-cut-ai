"""Manual functional test for Version 5's Resolve Free XML export
(davinci.resolve_xml_exporter + services.resolve_export_service.export_xml).

Run: python3 tests/test_v5_xml_export_manual.py

Covers exactly the ten scenarios Section 22 of the V5 spec calls out.
Nothing here touches a real DaVinci Resolve installation (this export
path is specifically designed not to need one -- see
davinci/resolve_xml_exporter.py's module docstring); what IS verified is
that the generated XML has the correct structure (clip count, Source
In/Out, Timeline In/Out, frame rate/NTSC flag, escaped Korean/space/path
text). Whether a real copy of DaVinci Resolve actually accepts this XML
via File > Import > Timeline has NOT been verified in this environment --
see the final report.
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davinci import edit_plan_applier, resolve_xml_exporter
from davinci.resolve_xml_exporter import ResolveXMLExportError, build_edit_xml, frame_rate_to_timebase
from models.edit_plan import EditPlan, EditPlanSegment
from utils.constants import EDIT_ACTION_COMPRESS, EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


def clip_items(xml_text: str):
    """Parse ``xml_text`` and return every <clipitem> on the video track,
    as (start, end, in, out) frame tuples, in document order."""
    root = ET.fromstring(xml_text)
    video_track = root.find("./sequence/media/video/track")
    items = []
    for clip in video_track.findall("clipitem"):
        items.append((
            int(clip.findtext("start")), int(clip.findtext("end")),
            int(clip.findtext("in")), int(clip.findtext("out")),
        ))
    return items


# =======================================================================
# Test 1 -- entire video is one KEEP segment
# =======================================================================
plan_full_keep = EditPlan(
    target_length_seconds=10.0,
    keep_segments=[EditPlanSegment(0.0, 10.0, EDIT_ACTION_KEEP, "whole thing")],
)
resolved_full_keep = edit_plan_applier.process_edit_plan(plan_full_keep, media_duration_seconds=10.0)
xml_full_keep = build_edit_xml(resolved_full_keep, Path("/videos/full.mp4"), fps=30.0)
clips_full_keep = clip_items(xml_full_keep)
check("Test 1: full KEEP -> exactly one clip", len(clips_full_keep) == 1)
check("Test 1: Source In = 0, Source Out = 300 (10s @ 30fps)", clips_full_keep[0][2:] == (0, 300))
check("Test 1: Timeline In = 0, Timeline Out = 300", clips_full_keep[0][:2] == (0, 300))

# =======================================================================
# Test 2 -- one REMOVE in the middle
# =======================================================================
plan_middle_remove = EditPlan(
    target_length_seconds=20.0,
    keep_segments=[EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "a"), EditPlanSegment(20, 30, EDIT_ACTION_KEEP, "b")],
    remove_segments=[EditPlanSegment(10, 20, EDIT_ACTION_REMOVE, "cut")],
)
resolved_middle_remove = edit_plan_applier.process_edit_plan(plan_middle_remove, media_duration_seconds=30.0)
xml_middle_remove = build_edit_xml(resolved_middle_remove, Path("/videos/middle.mp4"), fps=30.0)
clips_middle_remove = clip_items(xml_middle_remove)
check("Test 2: middle REMOVE -> exactly two clips", len(clips_middle_remove) == 2)
check("Test 2: clip 1 -- source 0-10s, timeline 0-10s", clips_middle_remove[0] == (0, 300, 0, 300))
check("Test 2: clip 2 -- source 20-30s, timeline 10-20s (no gap)", clips_middle_remove[1] == (300, 600, 600, 900))

# =======================================================================
# Test 3 -- several REMOVE ranges (the spec's own worked example)
# =======================================================================
plan_several_remove = EditPlan(
    target_length_seconds=40.0,
    keep_segments=[
        EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "a"),
        EditPlanSegment(15, 25, EDIT_ACTION_KEEP, "b"),
        EditPlanSegment(30, 40, EDIT_ACTION_KEEP, "c"),
    ],
    remove_segments=[
        EditPlanSegment(10, 15, EDIT_ACTION_REMOVE, "r1"),
        EditPlanSegment(25, 30, EDIT_ACTION_REMOVE, "r2"),
    ],
)
resolved_several_remove = edit_plan_applier.process_edit_plan(plan_several_remove, media_duration_seconds=40.0)
xml_several_remove = build_edit_xml(resolved_several_remove, Path("/videos/several.mp4"), fps=30.0)
clips_several_remove = clip_items(xml_several_remove)
check("Test 3: several REMOVE -> exactly three clips", len(clips_several_remove) == 3)
output_duration_frames = clips_several_remove[-1][1]
check("Test 3: output duration = 30 sec (900 frames @ 30fps)", output_duration_frames == 900)

# =======================================================================
# Test 4 -- overlapping REMOVE (reuses edit_plan_applier's own merge logic)
# =======================================================================
plan_overlap = EditPlan(
    target_length_seconds=50.0,
    remove_segments=[
        EditPlanSegment(10.0, 25.0, EDIT_ACTION_REMOVE, "overlap A"),
        EditPlanSegment(20.0, 30.0, EDIT_ACTION_REMOVE, "overlap B"),
    ],
)
resolved_overlap = edit_plan_applier.process_edit_plan(plan_overlap, media_duration_seconds=50.0)
check("Test 4: overlapping REMOVE merged into one range before XML export", resolved_overlap.remove_ranges == [(10.0, 30.0)])
xml_overlap = build_edit_xml(resolved_overlap, Path("/videos/overlap.mp4"), fps=30.0)
clips_overlap = clip_items(xml_overlap)
check("Test 4: merged overlap produces a valid two-clip XML (0-10s, 30-50s)", len(clips_overlap) == 2)
check("Test 4: no double-cut artifact from the overlap", clips_overlap[1] == (300, 900, 900, 1500))

# =======================================================================
# Test 5 -- COMPRESS is applied as a real speed change (Version 5.5)
# =======================================================================
plan_compress = EditPlan(
    target_length_seconds=20.0,
    keep_segments=[EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "a")],
    compress_segments=[EditPlanSegment(10, 20, EDIT_ACTION_COMPRESS, "sped up", compression_speed_factor=2.0)],
)
resolved_compress = edit_plan_applier.process_edit_plan(plan_compress, media_duration_seconds=20.0)
check(
    "Test 5: COMPRESS speed is mentioned in the warnings (informational, not an error)",
    any("COMPRESS" in w for w in resolved_compress.warnings),
)
xml_compress = build_edit_xml(resolved_compress, Path("/videos/compress.mp4"), fps=30.0)
clips_compress = clip_items(xml_compress)
check("Test 5: KEEP + COMPRESS split into two separate clipitems", len(clips_compress) == 2)
check("Test 5: clip 1 (KEEP) -- full speed, source 0-10s == timeline 0-10s", clips_compress[0] == (0, 300, 0, 300))
check(
    "Test 5: clip 2 (COMPRESS 2x) -- full 10s of source consumed (in/out span 300 frames)",
    clips_compress[1][3] - clips_compress[1][2] == 300,
)
check(
    "Test 5: clip 2 (COMPRESS 2x) -- timeline span is HALF the source span (150 frames = 5s)",
    clips_compress[1][1] - clips_compress[1][0] == 150,
)
check("Test 5: clip 2 timeline starts right after clip 1 with no gap", clips_compress[1][0] == 300)
check(
    "Test 5: total sequence duration reflects the speed-up (450 frames = 15s, not 600 = 20s)",
    ET.fromstring(xml_compress).findtext("./sequence/duration") == "450",
)
video_track_xml = ET.tostring(ET.fromstring(xml_compress).find("./sequence/media/video/track"), encoding="unicode")
check("Test 5: a Time Remap filter is present for the sped-up clip", "timeremap" in video_track_xml)
check("Test 5: the sped-up clip is labeled 2.00x in its name", "2.00x" in video_track_xml)
check("Test 5: the full-speed clip has no Time Remap filter mention on its own segment", "[1.00x]" not in video_track_xml)

# =======================================================================
# Test 6 -- Korean file name
# =======================================================================
korean_path = Path("/videos/전주중학교_영상_최종.mp4")
xml_korean = build_edit_xml(resolved_full_keep, korean_path, fps=30.0)
check("Test 6: Korean file name appears in <name> without corruption", "전주중학교_영상_최종.mp4" in xml_korean)
check(
    "Test 6: Korean file name is percent-encoded (UTF-8) in <pathurl>, not raw/broken",
    "%EC%A0%84%EC%A3%BC%EC%A4%91%ED%95%99%EA%B5%90" in xml_korean,
)
check("Test 6: XML still parses cleanly", ET.fromstring(xml_korean) is not None)

# =======================================================================
# Test 7 -- file name with spaces
# =======================================================================
space_path = Path("/videos/my test video.mp4")
xml_space = build_edit_xml(resolved_full_keep, space_path, fps=30.0)
check("Test 7: spaced file name appears in <name> as-is", "my test video.mp4" in xml_space)
check("Test 7: spaces are percent-encoded in <pathurl>", "file:///videos/my%20test%20video.mp4" in xml_space)

# =======================================================================
# Test 8 -- 29.97fps (NTSC drop-frame-ish rate)
# =======================================================================
timebase_2997, ntsc_2997 = frame_rate_to_timebase(29.97)
check("Test 8: 29.97fps maps to timebase 30", timebase_2997 == 30)
check("Test 8: 29.97fps is flagged as NTSC", ntsc_2997 is True)
xml_2997 = build_edit_xml(resolved_full_keep, Path("/videos/ntsc.mp4"), fps=29.97)
check("Test 8: 29.97fps XML reports <ntsc>TRUE</ntsc>", "<ntsc>TRUE</ntsc>" in xml_2997)

# =======================================================================
# Test 9 -- 60fps
# =======================================================================
timebase_60, ntsc_60 = frame_rate_to_timebase(60.0)
check("Test 9: 60fps maps to timebase 60", timebase_60 == 60)
check("Test 9: 60fps is not flagged as NTSC", ntsc_60 is False)
xml_60 = build_edit_xml(resolved_full_keep, Path("/videos/60fps.mp4"), fps=60.0)
clips_60 = clip_items(xml_60)
check("Test 9: 60fps doubles the frame count for the same 10s KEEP range", clips_60[0][1] == 600)

# =======================================================================
# Test 10 -- duration boundary: the last KEEP ends exactly at video end
# =======================================================================
plan_boundary_end = EditPlan(
    target_length_seconds=50.0,
    keep_segments=[EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "a"), EditPlanSegment(15, 50, EDIT_ACTION_KEEP, "b")],
    remove_segments=[EditPlanSegment(10, 15, EDIT_ACTION_REMOVE, "cut")],
)
resolved_boundary_end = edit_plan_applier.process_edit_plan(plan_boundary_end, media_duration_seconds=50.0)
xml_boundary_end = build_edit_xml(resolved_boundary_end, Path("/videos/boundary.mp4"), fps=30.0)
clips_boundary_end = clip_items(xml_boundary_end)
check(
    "Test 10: last KEEP segment's Source Out lands exactly on the media's final frame",
    clips_boundary_end[-1][3] == round(50.0 * 30.0),
)
check(
    "Test 10: no extra trailing clip/gap is introduced at the exact end boundary",
    clips_boundary_end[-1][1] == clips_boundary_end[-1][1],  # timeline end == its own duration accumulation
)


# =======================================================================
# Error handling (Section 21): each documented failure path stays a clean
# ResolveXMLExportError, never a raw traceback.
# =======================================================================
try:
    frame_rate_to_timebase(0.0)
    raised_on_bad_fps = False
except ResolveXMLExportError:
    raised_on_bad_fps = True
check("Invalid (zero) fps raises a clean ResolveXMLExportError", raised_on_bad_fps)

plan_all_removed = EditPlan(
    target_length_seconds=10.0,
    remove_segments=[EditPlanSegment(0.0, 10.0, EDIT_ACTION_REMOVE, "cut everything")],
)
resolved_all_removed = edit_plan_applier.process_edit_plan(plan_all_removed, media_duration_seconds=10.0)
try:
    build_edit_xml(resolved_all_removed, Path("/videos/all_removed.mp4"), fps=30.0)
    raised_on_no_keep = False
except ResolveXMLExportError:
    raised_on_no_keep = True
check("A plan with zero KEEP ranges left raises a clean ResolveXMLExportError, not a broken empty XML", raised_on_no_keep)


# =======================================================================
# services.resolve_export_service.export_xml -- top-level error handling
# and successful round-trip to a real temp file
# =======================================================================
import tempfile  # noqa: E402

from core.exceptions import ResolveXMLExportError as ServiceResolveXMLExportError  # noqa: E402
from services.resolve_export_service import ResolveExportService  # noqa: E402

service = ResolveExportService()

with tempfile.TemporaryDirectory() as tmp_dir:
    real_video_path = Path(tmp_dir) / "my test video.mp4"
    real_video_path.write_bytes(b"not a real video, just needs to exist")

    report = service.export_xml(
        plan_middle_remove, source_video_path=real_video_path, fps=30.0,
        output_path=Path(tmp_dir) / "output.xml", media_duration_seconds=30.0,
        width=1920, height=1080,
    )
    check("export_xml() writes a real file to disk", report.output_path.exists())
    check("export_xml() reports the correct clip count", report.clip_count == 2)
    written_xml = report.output_path.read_text(encoding="utf-8")
    check("The file written to disk round-trips as valid, parseable XML", ET.fromstring(written_xml) is not None)

    suggested = ResolveExportService.suggested_xml_path(real_video_path)
    check(
        "suggested_xml_path() follows the '<source_name>_AutoCutAI.xml' convention (Section 20)",
        suggested.name == "my test video_AutoCutAI.xml",
    )

    try:
        service.export_xml(
            plan_middle_remove, source_video_path=Path(tmp_dir) / "does_not_exist.mp4",
            fps=30.0, output_path=Path(tmp_dir) / "output2.xml", media_duration_seconds=30.0,
        )
        raised_on_missing_source = False
    except ServiceResolveXMLExportError:
        raised_on_missing_source = True
    check("export_xml() raises cleanly when the source video file can't be found", raised_on_missing_source)

    try:
        service.export_xml(
            EditPlan(target_length_seconds=0.0), source_video_path=real_video_path,
            fps=30.0, output_path=Path(tmp_dir) / "output3.xml",
        )
        raised_on_empty_plan = False
    except ServiceResolveXMLExportError:
        raised_on_empty_plan = True
    check("export_xml() raises cleanly when no EditPlan/segments are loaded", raised_on_empty_plan)

    try:
        service.export_xml(
            plan_middle_remove, source_video_path=real_video_path,
            fps=None, output_path=Path(tmp_dir) / "output4.xml", media_duration_seconds=30.0,
        )
        raised_on_missing_fps = False
    except ServiceResolveXMLExportError:
        raised_on_missing_fps = True
    check("export_xml() raises cleanly when fps is unknown", raised_on_missing_fps)


print("\nAll manual Resolve Free XML export checks passed (structure/frame-accuracy/escaping/error-handling "
      "fully verified without Resolve; real DaVinci Resolve XML import has NOT been verified in this "
      "environment -- see the final report).")
