"""Manual functional test for Version 5 (real DaVinci Resolve cut-editing).

Run: python3 tests/test_v5_resolve_manual.py

Two layers are tested:

1. ``davinci.edit_plan_applier`` -- pure EditPlan validation/range logic.
   No Resolve, no video file, no network needed; this is the safety-critical
   layer (never delete the wrong range, never trust a bad timestamp) and is
   fully exercised here.
2. ``davinci.timeline_editor`` -- the split+ripple-delete algorithm that
   actually cuts a Resolve timeline, exercised against a small in-memory
   fake of the Resolve Scripting API's Timeline/TimelineItem objects (same
   method names/semantics: GetStart/GetEnd/SplitClip/DeleteClips/
   GetItemListInTrack/DuplicateTimeline), reproducing exactly the
   Section 13 scenario from the V5 spec (0-10=A, 10-20=B(removed),
   20-30=C, 30-40=D(removed) -> result should be A+C).

IMPORTANT -- what this file does NOT test: a real, running copy of DaVinci
Resolve. This sandbox has no Resolve installation, so
``davinci.resolve_connection.connect()`` is only tested for its documented
*failure* path (Resolve not reachable -> a clean ``ResolveNotRunningError``,
never a crash). Actually cutting a real Resolve project's timeline has not
been verified and must be done by hand -- see the final report.
"""
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.exceptions import EditPlanResolveApplyError, ResolveNotRunningError
from davinci import edit_plan_applier, resolve_connection, timeline_editor
from davinci.resolve_connection import ResolveHandle
from models.edit_plan import EditPlan, EditPlanSegment
from utils.constants import EDIT_ACTION_COMPRESS, EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


# ---------------------------------------------------------------------
# Fake Resolve Scripting API objects (same method surface as the real
# Timeline/TimelineItem -- see davinci/timeline_editor.py's usage of them).
# ---------------------------------------------------------------------
class FakeItem:
    def __init__(self, start: int, end: int, track_type: str, track_index: int):
        self.start, self.end = start, end
        self.track_type, self.track_index = track_type, track_index

    def GetStart(self) -> int:
        return self.start

    def GetEnd(self) -> int:
        return self.end


class FakeTimeline:
    def __init__(self, tracks: Dict[Tuple[str, int], List[FakeItem]], name: str = "Timeline 1"):
        self.tracks = tracks
        self.name = name

    def GetName(self) -> str:
        return self.name

    def GetStartFrame(self) -> int:
        return 0

    def GetSetting(self, key: str):
        return 30.0 if key == "timelineFrameRate" else None

    def GetTrackCount(self, track_type: str) -> int:
        return max((idx for (t, idx) in self.tracks if t == track_type), default=0)

    def GetItemListInTrack(self, track_type: str, index: int) -> List[FakeItem]:
        return sorted(self.tracks.get((track_type, index), []), key=lambda item: item.start)

    def SplitClip(self, item: FakeItem, frame: int) -> bool:
        key = (item.track_type, item.track_index)
        lst = self.tracks[key]
        if not (item.start < frame < item.end):
            return False
        lst.remove(item)
        lst.append(FakeItem(item.start, frame, item.track_type, item.track_index))
        lst.append(FakeItem(frame, item.end, item.track_type, item.track_index))
        return True

    def DeleteClips(self, items: List[FakeItem], ripple: bool) -> bool:
        by_track: Dict[Tuple[str, int], List[FakeItem]] = {}
        for item in items:
            by_track.setdefault((item.track_type, item.track_index), []).append(item)
        for key, group in by_track.items():
            lst = self.tracks[key]
            removed_end = max(item.end for item in group)
            removed_len = sum(item.end - item.start for item in group)
            for item in group:
                lst.remove(item)
            if ripple:
                for other in lst:
                    if other.start >= removed_end:
                        other.start -= removed_len
                        other.end -= removed_len
        return True

    def DuplicateTimeline(self, name: str) -> "FakeTimeline":
        cloned = {
            key: [FakeItem(i.start, i.end, i.track_type, i.track_index) for i in items]
            for key, items in self.tracks.items()
        }
        return FakeTimeline(cloned, name=name)


class FakeProject:
    def __init__(self):
        self._timelines: List[FakeTimeline] = []

    def GetTimelineCount(self) -> int:
        return len(self._timelines)

    def GetTimelineByIndex(self, index: int) -> FakeTimeline:
        return self._timelines[index - 1]

    def GetSetting(self, key: str):
        return None


def make_single_clip_timeline(duration_frames: int) -> FakeTimeline:
    """A timeline with one full-length clip on video track 1 and audio track 1
    (the common case: a timeline made directly from one imported source video)."""
    return FakeTimeline({
        ("video", 1): [FakeItem(0, duration_frames, "video", 1)],
        ("audio", 1): [FakeItem(0, duration_frames, "audio", 1)],
    })


# =======================================================================
# Layer 1: davinci.edit_plan_applier (pure logic, no Resolve involved)
# =======================================================================

# --- Test: KEEP / REMOVE / KEEP converts to the correct REMOVE range -----
plan_simple = EditPlan(
    target_length_seconds=25.0,
    keep_segments=[EditPlanSegment(0, 12, EDIT_ACTION_KEEP, "intro"), EditPlanSegment(18, 35, EDIT_ACTION_KEEP, "main")],
    remove_segments=[EditPlanSegment(12, 18, EDIT_ACTION_REMOVE, "dead air")],
)
resolved_simple = edit_plan_applier.process_edit_plan(plan_simple, media_duration_seconds=35.0)
check("KEEP/REMOVE/KEEP -> exactly one REMOVE range", resolved_simple.remove_ranges == [(12.0, 18.0)])
check("KEEP/REMOVE/KEEP -> keep ranges fill the gaps", resolved_simple.keep_ranges == [(0.0, 12.0), (18.0, 35.0)])
check("No warnings for a clean plan", resolved_simple.warnings == [])

# --- Test: multiple REMOVE segments never drift relative to each other ---
plan_multi = EditPlan(
    target_length_seconds=60.0,
    remove_segments=[
        EditPlanSegment(10, 20, EDIT_ACTION_REMOVE, "r1"),
        EditPlanSegment(70, 80, EDIT_ACTION_REMOVE, "r3"),
        EditPlanSegment(40, 50, EDIT_ACTION_REMOVE, "r2"),  # given out of order on purpose
    ],
)
resolved_multi = edit_plan_applier.process_edit_plan(plan_multi, media_duration_seconds=90.0)
check(
    "Multiple REMOVE segments are sorted and each kept at its own original timestamps",
    resolved_multi.remove_ranges == [(10.0, 20.0), (40.0, 50.0), (70.0, 80.0)],
)
check("Total removed matches the sum of each independent range", resolved_multi.total_removed_seconds == 30.0)

# --- Test: boundary values (0s start, video end, very short REMOVE) ------
plan_boundaries = EditPlan(
    target_length_seconds=10.0,
    remove_segments=[
        EditPlanSegment(0.0, 2.0, EDIT_ACTION_REMOVE, "from the very start"),
        EditPlanSegment(38.0, 40.0, EDIT_ACTION_REMOVE, "to the very end"),
        EditPlanSegment(20.0, 20.005, EDIT_ACTION_REMOVE, "sub-frame sliver"),
    ],
)
resolved_boundaries = edit_plan_applier.process_edit_plan(plan_boundaries, media_duration_seconds=40.0)
check("A REMOVE starting at 0s is kept", (0.0, 2.0) in resolved_boundaries.remove_ranges)
check("A REMOVE ending exactly at media duration is kept", (38.0, 40.0) in resolved_boundaries.remove_ranges)
check("A too-short REMOVE sliver is skipped, not silently applied", (20.0, 20.005) not in resolved_boundaries.remove_ranges)
check("The too-short sliver produced a warning explaining why", any("too short" in w for w in resolved_boundaries.warnings))

# --- Test: overlapping REMOVE segments are merged, not double-applied ----
plan_overlap = EditPlan(
    target_length_seconds=50.0,
    remove_segments=[
        EditPlanSegment(10.0, 25.0, EDIT_ACTION_REMOVE, "overlap A"),
        EditPlanSegment(20.0, 30.0, EDIT_ACTION_REMOVE, "overlap B"),
    ],
)
resolved_overlap = edit_plan_applier.process_edit_plan(plan_overlap, media_duration_seconds=50.0)
check("Overlapping REMOVE segments merge into a single range", resolved_overlap.remove_ranges == [(10.0, 30.0)])
check("A merge warning is produced", any("Merged" in w for w in resolved_overlap.warnings))

# --- Test: an EditPlan timestamp outside the media's duration is clamped, not crashed on ---
plan_out_of_range = EditPlan(
    target_length_seconds=10.0,
    remove_segments=[EditPlanSegment(8.0, 15.0, EDIT_ACTION_REMOVE, "runs past the end")],
)
resolved_oor = edit_plan_applier.process_edit_plan(plan_out_of_range, media_duration_seconds=10.0)
check("Out-of-range REMOVE is clamped to media duration", resolved_oor.remove_ranges == [(8.0, 10.0)])
check("Clamping produces a warning", any("clamped" in w for w in resolved_oor.warnings))

# --- Test: an invalid REMOVE segment (end <= start) is skipped safely ----
plan_invalid = EditPlan(
    target_length_seconds=10.0,
    remove_segments=[EditPlanSegment(5.0, 5.0, EDIT_ACTION_REMOVE, "zero-length"), EditPlanSegment(1.0, 2.0, EDIT_ACTION_REMOVE, "valid")],
)
resolved_invalid = edit_plan_applier.process_edit_plan(plan_invalid, media_duration_seconds=10.0)
check("A zero-length REMOVE is skipped, not applied", resolved_invalid.remove_ranges == [(1.0, 2.0)])
check("The invalid segment produced a warning", any("invalid REMOVE" in w for w in resolved_invalid.warnings))

# --- Test: an empty EditPlan is handled without raising ------------------
resolved_empty = edit_plan_applier.process_edit_plan(EditPlan(target_length_seconds=0.0))
check("Empty EditPlan produces zero REMOVE ranges", resolved_empty.remove_ranges == [])
check("Empty EditPlan produces an explanatory warning", any("no segments" in w for w in resolved_empty.warnings))
check("Empty EditPlan has_removals is False", resolved_empty.has_removals is False)

# --- Test: a COMPRESS-only EditPlan is handled safely without data loss --
plan_compress_only = EditPlan(
    target_length_seconds=20.0,
    compress_segments=[EditPlanSegment(0.0, 20.0, EDIT_ACTION_COMPRESS, "whole thing sped up", compression_speed_factor=2.0)],
)
resolved_compress_only = edit_plan_applier.process_edit_plan(plan_compress_only, media_duration_seconds=20.0)
check("COMPRESS-only plan removes nothing (V5 doesn't apply speed changes)", resolved_compress_only.remove_ranges == [])
check("COMPRESS-only plan is clearly flagged as unsupported-but-safe", any("COMPRESS" in w for w in resolved_compress_only.warnings))
check("COMPRESS segment count is reported", resolved_compress_only.compress_segment_count == 1)

# --- Test: Dry Run preview text is generated without touching anything ---
preview_lines = edit_plan_applier.build_preview_lines(resolved_simple)
preview_text = "\n".join(preview_lines)
check("Dry Run preview mentions the REMOVE range", "REMOVE" in preview_text and "00:12" in preview_text)
check("Dry Run preview reports total removed time", "Total removed: 6.000 sec" in preview_text)


# =======================================================================
# Layer 2: davinci.timeline_editor (split + ripple-delete against a fake
# Resolve Timeline reproducing the V5 spec's Section 13 scenario)
# =======================================================================

# 0-10s=A, 10-20s=B(removed), 20-30s=C, 30-40s=D(removed) @ 30fps -> A+C
fps = 30.0
duration_frames = int(40 * fps)
fake_timeline = make_single_clip_timeline(duration_frames)
fake_project = FakeProject()
handle = ResolveHandle(resolve=None, project=fake_project, timeline=fake_timeline)

plan_av_bc_d = EditPlan(
    target_length_seconds=20.0,
    keep_segments=[EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "A"), EditPlanSegment(20, 30, EDIT_ACTION_KEEP, "C")],
    remove_segments=[EditPlanSegment(10, 20, EDIT_ACTION_REMOVE, "B"), EditPlanSegment(30, 40, EDIT_ACTION_REMOVE, "D")],
)
resolved_av = edit_plan_applier.process_edit_plan(plan_av_bc_d, media_duration_seconds=40.0)

dup = timeline_editor.duplicate_timeline(handle)
check("duplicate_timeline names the copy AutoCutAI_Edit", dup.GetName() == "AutoCutAI_Edit")
check("The original timeline's clip is untouched after duplication", fake_timeline.tracks[("video", 1)][0].GetEnd() == duration_frames)

apply_result = timeline_editor.apply_remove_ranges(handle, dup, resolved_av.remove_ranges)
check("Both REMOVE ranges were applied", apply_result.ranges_applied == 2 and apply_result.ranges_requested == 2)

video_items = sorted(dup.GetItemListInTrack("video", 1), key=lambda i: i.GetStart())
audio_items = sorted(dup.GetItemListInTrack("audio", 1), key=lambda i: i.GetStart())
expected_frames = [(0, int(10 * fps)), (int(10 * fps), int(20 * fps))]  # A (0-10s) followed by C (10-20s in the new timeline)
check(
    "Video track after cutting is exactly A followed by C, with no drift (Section 9/13)",
    [(i.GetStart(), i.GetEnd()) for i in video_items] == expected_frames,
)
check(
    "Audio track stayed in sync with video after the same cuts",
    [(i.GetStart(), i.GetEnd()) for i in audio_items] == expected_frames,
)

# --- Test: duplicate naming avoids collisions on repeated runs -----------
fake_project._timelines.append(dup)  # simulate the project now containing "AutoCutAI_Edit"
second_dup = timeline_editor.duplicate_timeline(handle)
check("A second run names the timeline AutoCutAI_Edit_001 instead of colliding", second_dup.GetName() == "AutoCutAI_Edit_001")

# --- Test: an EditPlan with no REMOVE ranges still duplicates cleanly, cuts nothing ---
empty_apply = timeline_editor.apply_remove_ranges(handle, dup, [])
check("Applying zero ranges is a safe no-op", empty_apply.ranges_applied == 0 and empty_apply.clips_deleted == 0)

# --- Test: a REMOVE range that rounds to zero frames is skipped, not crashed on ---
tiny_timeline = make_single_clip_timeline(int(5 * fps))
tiny_handle = ResolveHandle(resolve=None, project=FakeProject(), timeline=tiny_timeline)
tiny_result = timeline_editor.apply_remove_ranges(tiny_handle, tiny_timeline, [(1.0, 1.0001)])
check("A sub-frame REMOVE range is skipped without raising", tiny_result.ranges_applied == 0)


# =======================================================================
# Layer 3: davinci.resolve_connection -- graceful failure when Resolve
# genuinely isn't reachable (the only Resolve-connection scenario this
# sandbox, with no DaVinci Resolve installed, can actually exercise).
# =======================================================================
try:
    resolve_connection.connect()
    raised_correctly = False
except ResolveNotRunningError:
    raised_correctly = True
except Exception:  # noqa: BLE001
    raised_correctly = False
check("connect() fails with a clean ResolveNotRunningError when Resolve isn't reachable (not a raw crash)", raised_correctly)


# =======================================================================
# Layer 4: services.resolve_export_service -- error handling at the top level
# =======================================================================
from services.resolve_export_service import ResolveExportService  # noqa: E402

service = ResolveExportService()
preview_report = service.preview(plan_simple, media_duration_seconds=35.0)
check("ResolveExportService.preview() never touches Resolve and returns the same ranges", preview_report.plan.remove_ranges == [(12.0, 18.0)])
check("preview_lines works from the report", "REMOVE" in "\n".join(preview_report.preview_lines))

try:
    service.apply(EditPlan(target_length_seconds=0.0))
    raised_on_empty = False
except EditPlanResolveApplyError:
    raised_on_empty = True
check("apply() on a completely empty EditPlan raises a clear error instead of doing nothing silently", raised_on_empty)

try:
    service.apply(plan_simple, media_duration_seconds=35.0)
    raised_without_resolve = False
except ResolveNotRunningError:
    raised_without_resolve = True
except Exception:  # noqa: BLE001
    raised_without_resolve = False
check("apply() surfaces the same clean ResolveNotRunningError when Resolve isn't running", raised_without_resolve)


print("\nAll manual V5 checks passed (edit_plan_applier + timeline_editor split/ripple algorithm, "
      "fully verified without Resolve; resolve_connection/resolve_export_service verified for their "
      "documented failure path only -- see the final report for what still needs a real Resolve session).")
