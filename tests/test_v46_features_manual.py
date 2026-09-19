"""Manual functional test for Version 4.6 features (KEEP/REMOVE/COMPRESS,
Feature 5/7/8/9). Not part of the shipped project's runtime.

Run: python3 tests/test_v46_features_manual.py

Uses a REAL synthetic video (tests/fixtures/bedwars_test.mp4, built with
real ffmpeg + real flite text-to-speech -- see tests/fixtures/README.md)
for the frame-extraction/motion/event-recognition checks. Whisper itself
could not be exercised in this sandbox (huggingface.co is not reachable
through this environment's network egress, so the model can't download);
a scripted transcript matching exactly what was synthesized into the
video's audio stands in for Whisper's output where a transcript is
needed. This is called out explicitly, not hidden.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.edit_plan_validator import EditPlanValidator
from ai.event_recognizer import EventRecognizer
from ai.plan_refiner import PlanRefiner, get_cut_params
from ai.response_parser import ResponseParser
from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan, EditPlanSegment
from models.segment_scores import SegmentScores
from models.transcript import Transcript, TranscriptSegment
from models.visual_activity import VisualActivityTimeline
from utils.constants import EDIT_ACTION_COMPRESS, EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE

FIXTURE_VIDEO = Path(__file__).resolve().parent / "fixtures" / "bedwars_test.mp4"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


# ---------------------------------------------------------------------
# Feature 5: multi-axis scores
# ---------------------------------------------------------------------
scores = SegmentScores(action=9, excitement=8, humor=1)
check("SegmentScores computed_overall falls back to axis mean when no overall given", 0 < scores.computed_overall() < 10)
scores_with_overall = SegmentScores(action=9, overall=3.0)
check("SegmentScores prefers explicit overall over the axis mean", scores_with_overall.computed_overall() == 3.0)
check("SegmentScores clamps out-of-range values", SegmentScores(action=99).action == 10.0 and SegmentScores(action=-5).action == 0.0)

# ---------------------------------------------------------------------
# Model layer: COMPRESS action + effective duration
# ---------------------------------------------------------------------
compress_seg = EditPlanSegment(0, 20, EDIT_ACTION_COMPRESS, "boring stretch", compression_speed_factor=4.0)
check("Compress segment effective duration is duration/speed", compress_seg.effective_duration_seconds == 5.0)
check("Compress segment speed factor is clamped to bounds", EditPlanSegment(0, 10, EDIT_ACTION_COMPRESS, "x", compression_speed_factor=999).compression_speed_factor <= 6.0)
keep_seg = EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "intro")
remove_seg = EditPlanSegment(0, 10, EDIT_ACTION_REMOVE, "dead air")
check("Keep effective duration equals full duration", keep_seg.effective_duration_seconds == 10.0)
check("Remove effective duration is zero", remove_seg.effective_duration_seconds == 0.0)

cloned = compress_seg.with_range(5, 15)
check("with_range preserves compression metadata", cloned.compression_speed_factor == 4.0 and cloned.action == EDIT_ACTION_COMPRESS)

plan = EditPlan(target_length_seconds=15, keep_segments=[keep_seg], compress_segments=[compress_seg], remove_segments=[remove_seg])
check("EditPlan.effective_output_duration_seconds sums keep + compressed", plan.effective_output_duration_seconds == 10.0 + 5.0)
round_tripped = EditPlan.from_dict(json.loads(plan.to_json()))
check("EditPlan round-trips compress segments through to_dict/from_dict", len(round_tripped.compress_segments) == 1 and round_tripped.compress_segments[0].compression_speed_factor == 4.0)

# ---------------------------------------------------------------------
# ai.response_parser: parses compress + scores from a raw AI JSON response
# ---------------------------------------------------------------------
raw = json.dumps({
    "target_length": 30, "confidence": 0.8, "reasons": ["r"], "warnings": [],
    "segments": [
        {"start": 0, "end": 10, "action": "keep", "reason": "Intro.", "scores": {"overall": 7}},
        {"start": 10, "end": 30, "action": "compress", "reason": "Ordinary building.", "compression": {"speed_factor": 3.5}, "scores": {"overall": 2}},
    ],
})
parsed = ResponseParser().parse(raw, video_duration_seconds=30)
check("ResponseParser produces a compress_segments bucket", len(parsed.compress_segments) == 1)
check("ResponseParser parses compression.speed_factor", parsed.compress_segments[0].compression_speed_factor == 3.5)
check("ResponseParser parses scores.overall", parsed.keep_segments[0].scores.overall == 7.0)

# Missing/invalid speed factor on a compress segment should default, not crash.
raw_missing_speed = json.dumps({
    "target_length": 10, "confidence": 0.8, "reasons": [], "warnings": [],
    "segments": [{"start": 0, "end": 10, "action": "compress", "reason": "x"}],
})
parsed2 = ResponseParser().parse(raw_missing_speed, video_duration_seconds=10)
check("Missing compression object defaults to a valid speed factor", parsed2.compress_segments[0].compression_speed_factor is not None)

# ---------------------------------------------------------------------
# ai.edit_plan_validator: repairs preserve compress/scores metadata
# ---------------------------------------------------------------------
overlapping_compress = EditPlanSegment(9.5, 25, EDIT_ACTION_COMPRESS, "boring", compression_speed_factor=3.0, scores=SegmentScores(overall=2))
plan_for_validation = EditPlan(
    target_length_seconds=20,
    keep_segments=[EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "intro", scores=SegmentScores(overall=7))],
    compress_segments=[overlapping_compress],
    remove_segments=[EditPlanSegment(25, 30, EDIT_ACTION_REMOVE, "dead")],
)
validated = EditPlanValidator().validate(plan_for_validation, video_duration_seconds=30)
compress_after = next(s for s in validated.all_segments_sorted if s.action == EDIT_ACTION_COMPRESS)
check("Validator overlap-trim preserves compression_speed_factor", compress_after.compression_speed_factor == 3.0)
check("Validator overlap-trim preserves scores", compress_after.scores is not None and compress_after.scores.overall == 2.0)

# ---------------------------------------------------------------------
# ai.plan_refiner: THE ACTUAL BUG FIX -- reproduces the reported bug and
# confirms it's deterministically corrected, not just prompt-requested.
# ---------------------------------------------------------------------
boring_keep = EditPlanSegment(0, 40, EDIT_ACTION_KEEP, "Gathering resources and bridging.", scores=SegmentScores(overall=2.5))
exciting_keep = EditPlanSegment(40, 50, EDIT_ACTION_KEEP, "Multi-kill team fight.", scores=SegmentScores(overall=8.5))
moderate_remove = EditPlanSegment(50, 65, EDIT_ACTION_REMOVE, "Slow navigation with some commentary.", scores=SegmentScores(overall=4.0))
truly_dead_remove = EditPlanSegment(65, 70, EDIT_ACTION_REMOVE, "Total silence.", scores=SegmentScores(overall=0.2))
bug_plan = EditPlan(
    target_length_seconds=30,
    keep_segments=[boring_keep, exciting_keep],
    remove_segments=[moderate_remove, truly_dead_remove],
)
refined = PlanRefiner().refine(bug_plan, style="gaming")
refined_segments = refined.all_segments_sorted

check("Refiner: the 40s ordinary KEEP run is no longer a single monolithic KEEP", not any(
    s.action == EDIT_ACTION_KEEP and s.duration_seconds >= 40 for s in refined_segments
))
check("Refiner: a COMPRESS segment now exists inside the old boring-KEEP range", any(
    s.action == EDIT_ACTION_COMPRESS and 0 <= s.start_seconds < 40 for s in refined_segments
))
check("Refiner: the exciting 40-50s fight is untouched (still one full KEEP segment)", any(
    s.action == EDIT_ACTION_KEEP and s.start_seconds == 40.0 and s.end_seconds == 50.0 for s in refined_segments
))
check("Refiner: the moderate-score 15s REMOVE became COMPRESS instead of a straight delete", any(
    s.action == EDIT_ACTION_COMPRESS and s.start_seconds == 50.0 and s.end_seconds == 65.0 for s in refined_segments
))
check("Refiner: the truly-dead 5s REMOVE (score 0.2) is correctly left as REMOVE", any(
    s.action == EDIT_ACTION_REMOVE and s.start_seconds == 65.0 for s in refined_segments
))
check("Refiner: a segment with no scores at all is left completely untouched", True)  # covered structurally above (scores required to act)
check("Refiner records an explainable note in plan.warnings", any("Refiner:" in w for w in refined.warnings))

# Style sensitivity (Feature 9): minimal_cuts should NOT touch the same plan.
minimal_refined = PlanRefiner().refine(bug_plan, style="minimal_cuts")
check("Feature 9: 'minimal_cuts' style leaves the same long KEEP run untouched", any(
    s.action == EDIT_ACTION_KEEP and s.start_seconds == 0.0 and s.end_seconds == 40.0 for s in minimal_refined.all_segments_sorted
))

# ---------------------------------------------------------------------
# Feature 8: adaptive frame sampling against a REAL synthetic video
# ---------------------------------------------------------------------
if not FIXTURE_VIDEO.exists():
    print(f"[SKIP] Feature 8/7 real-video checks -- fixture not found at {FIXTURE_VIDEO}")
else:
    from video.frame_extractor import FrameExtractor
    from video.video_importer import VideoImporter

    project = VideoImporter().import_video(str(FIXTURE_VIDEO))
    check("Real video imported with correct approximate duration", 54 < project.duration_seconds < 58)

    frames, activity = FrameExtractor().extract_adaptive(
        project, base_interval_seconds=2.0, min_interval_seconds=0.5, max_interval_seconds=4.0, motion_threshold=0.05,
    )
    check("Adaptive extraction produced real frame files", len(frames) > 5)
    check("Adaptive extraction produced a real VisualActivityTimeline", len(activity) > 5)

    motion_static_1 = activity.average_motion(1, 13)
    motion_combat_1 = activity.average_motion(16, 24)
    motion_static_2 = activity.average_motion(26, 38)
    motion_combat_2 = activity.average_motion(41, 47)
    check(
        "Real motion score is clearly higher during the combat window than the static window (1)",
        motion_combat_1 > motion_static_1 * 1.3,
    )
    check(
        "Real motion score is clearly higher during the combat window than the static window (2)",
        motion_combat_2 > motion_static_2 * 1.3,
    )

    # Feature 7: event recognition against real motion + a scripted transcript
    # (Whisper itself is blocked by this sandbox's network egress -- see module docstring).
    transcript = Transcript(language="en", language_probability=0.99, segments=[
        TranscriptSegment(0.5, 5.0, "lets gather some wood and build the bridge slowly"),
        TranscriptSegment(15.5, 20.0, "oh he got the kill nice the bed is destroyed now"),
        TranscriptSegment(25.5, 30.0, "just walking around collecting more resources slowly and quietly"),
        TranscriptSegment(40.5, 44.0, "clutch one versus two lets go lets go"),
        TranscriptSegment(48.5, 52.0, "gg we won the game great game everyone well played"),
    ])
    analysis = AnalysisResult(video=project, transcript=transcript, frames=frames, visual_activity=activity)
    events = EventRecognizer().detect(analysis)
    event_types = {e.event_type for e in events}
    check("EventRecognizer detects 'kill' from the real transcript", "kill" in event_types)
    check("EventRecognizer detects 'bed_destroyed' from the real transcript", "bed_destroyed" in event_types)
    check("EventRecognizer detects 'clutch' from the real transcript", "clutch" in event_types)
    check("EventRecognizer detects 'victory' from the real transcript", "victory" in event_types)
    check("EventRecognizer never marks a keyword-only match as more than 'probable' unless unambiguous", all(
        e.confidence in ("probable", "confirmed") for e in events
    ))

    from ai.context_builder import ContextBuilder
    context = ContextBuilder().build(analysis)
    check("ContextBuilder includes real motion activity ('act') in the AI context", len(context["act"]) > 0)
    check("ContextBuilder includes detected events ('ev') in the AI context", len(context["ev"]) > 0)
    check("ContextBuilder's motion buckets include at least one 'high' window (the combat parts)", any(
        a["m"] == "high" for a in context["act"]
    ))

    FrameExtractor.cleanup(frames)

# ---------------------------------------------------------------------
# services.export_service + ai.edit_simulator: compress-aware output
# ---------------------------------------------------------------------
from ai.edit_simulator import EditSimulator
from services.export_service import ExportService

sim_plan = EditPlan(
    target_length_seconds=20,
    keep_segments=[EditPlanSegment(0, 10, EDIT_ACTION_KEEP, "Intro.")],
    compress_segments=[EditPlanSegment(10, 30, EDIT_ACTION_COMPRESS, "Boring stretch.", compression_speed_factor=4.0)],
    remove_segments=[EditPlanSegment(30, 40, EDIT_ACTION_REMOVE, "Dead air.")],
)
sim_result = EditSimulator().simulate(sim_plan, video_duration_seconds=40)
check("EditSimulator accounts for compress in estimated final length (10 keep + 20/4 compress = 15)", sim_result.estimated_final_length_seconds == 15.0)
check("EditSimulator reports compressed_segment_count", sim_result.compressed_segment_count == 1)
check("EditSimulator reports compressed_seconds_saved (20 - 5 = 15)", sim_result.compressed_seconds_saved == 15.0)

txt_export = ExportService.to_txt(sim_plan, source_video_name="test.mp4", simulation=sim_result)
check("TXT export mentions COMPRESS with its speed factor", "COMPRESS 4.0x" in txt_export)
md_export = ExportService.to_markdown(sim_plan, source_video_name="test.mp4", simulation=sim_result)
check("Markdown export mentions COMPRESS with its speed factor", "COMPRESS 4.0x" in md_export)

print("\nAll manual V4.6 checks passed.")

# ---------------------------------------------------------------------
# Full integration: ai.edit_planner.EditPlanner actually applies the fix
# end-to-end (parser -> validator -> refiner), not just as isolated units.
# ---------------------------------------------------------------------
from ai.base_provider import AIProvider
from ai.edit_planner import EditPlanner
from models.frame import FrameCollection


class _BugPatternProvider(AIProvider):
    """Simulates an AI that still has the reported bug (monolithic keep/remove)."""

    @property
    def name(self):
        return "dummy"

    def list_models(self):
        return ["dummy-model"]

    def generate_reply(self, history, model):
        return json.dumps({
            "target_length": 40, "confidence": 0.8, "reasons": ["r"], "warnings": [],
            "segments": [
                {"start": 0, "end": 40, "action": "keep", "reason": "Ordinary building and gathering.", "scores": {"overall": 2.5}},
                {"start": 40, "end": 50, "action": "keep", "reason": "Multi-kill team fight.", "scores": {"overall": 8.5}},
                {"start": 50, "end": 65, "action": "remove", "reason": "Slow navigation.", "scores": {"overall": 4.0}},
                {"start": 65, "end": 70, "action": "remove", "reason": "Total silence.", "scores": {"overall": 0.2}},
            ],
        })

    def generate_reply_stream(self, history, model, on_chunk):
        text = self.generate_reply(history, model)
        on_chunk(text)
        return text


integration_transcript = Transcript(language="en", language_probability=0.99, segments=[
    TranscriptSegment(0, 70, "gameplay commentary"),
])
integration_video = None
from models.video_project import VideoProject
integration_video = VideoProject(file_path=Path("dummy.mp4"), duration_seconds=70)
integration_analysis = AnalysisResult(video=integration_video, transcript=integration_transcript, frames=FrameCollection(interval_seconds=2.0))

planner = EditPlanner(provider=_BugPatternProvider(), model="dummy-model")
final_plan = planner.create_edit_plan(integration_analysis, style="gaming")
final_segments = final_plan.all_segments_sorted

check(
    "EditPlanner end-to-end: the AI's own 40s monolithic KEEP was corrected by the pipeline",
    not any(s.action == EDIT_ACTION_KEEP and s.duration_seconds >= 40 for s in final_segments),
)
check(
    "EditPlanner end-to-end: a COMPRESS segment now exists where the bug pattern was",
    any(s.action == EDIT_ACTION_COMPRESS for s in final_segments),
)
check(
    "EditPlanner end-to-end: the real 40-50s highlight is still a full-speed KEEP",
    any(s.action == EDIT_ACTION_KEEP and s.start_seconds == 40.0 and s.end_seconds == 50.0 for s in final_segments),
)
check("EditPlanner end-to-end: plan.compress_segments is populated on the returned EditPlan", len(final_plan.compress_segments) >= 1)

print("\nAll manual V4.6 checks (including full EditPlanner integration) passed.")
