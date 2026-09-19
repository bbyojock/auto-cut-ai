"""Manual regression test for clip reordering (Chat about EditPlan: "이 구간을
맨 앞으로 옮겨줘" / rearranging the final playback order of KEEP clips).

Run: python3 tests/test_reorder_feature_manual.py

Covers, end to end:
1. ``EditPlan.output_order`` round-trips through to_dict/from_dict.
2. ``edit_plan_applier.process_edit_plan`` applies a valid reorder request
   to ``ResolveEditPlan.keep_order`` while leaving ``keep_ranges``
   (chronological -- still used for the Dry Run preview / total-time math)
   untouched.
3. An invalid reorder request (wrong count, or a range that doesn't match
   any actual KEEP range) is safely rejected with a warning instead of
   corrupting the export -- never silently drops or duplicates footage.
4. ``resolve_xml_exporter.build_edit_xml`` actually lays clips out on the
   timeline in the requested order (not chronological) when a valid
   reorder is present, and behaves exactly as before (chronological) when
   it isn't -- zero behavior change for every existing plan.
5. ``EditPlanChatService`` end to end: a dummy AI response containing a
   top-level ``"reorder"`` field is parsed and produces an ``EditPlan``
   whose ``output_order`` matches, which then flows correctly through
   process_edit_plan + build_edit_xml.
"""
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.base_provider import AIProvider
from davinci import edit_plan_applier
from davinci.resolve_xml_exporter import build_edit_xml
from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan, EditPlanSegment
from models.frame import FrameCollection
from models.transcript import Transcript, TranscriptSegment
from models.video_project import VideoProject
from services.edit_plan_chat_service import EditPlanChatService
from utils.constants import EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


# ---------------------------------------------------------------------
# Shared fixture: 3 KEEP ranges (chronological: A, B, C) separated by
# REMOVE gaps, standing in for "3 scenes from a vlog/documentary".
# ---------------------------------------------------------------------
SCENE_A = (0.0, 10.0)
SCENE_B = (20.0, 30.0)
SCENE_C = (40.0, 50.0)
DURATION = 50.0


def make_plan(output_order=None) -> EditPlan:
    keep_segments = [
        EditPlanSegment(*SCENE_A, EDIT_ACTION_KEEP, "Scene A"),
        EditPlanSegment(*SCENE_B, EDIT_ACTION_KEEP, "Scene B"),
        EditPlanSegment(*SCENE_C, EDIT_ACTION_KEEP, "Scene C"),
    ]
    remove_segments = [
        EditPlanSegment(SCENE_A[1], SCENE_B[0], EDIT_ACTION_REMOVE, "gap 1"),
        EditPlanSegment(SCENE_B[1], SCENE_C[0], EDIT_ACTION_REMOVE, "gap 2"),
    ]
    return EditPlan(
        target_length_seconds=DURATION, keep_segments=keep_segments, remove_segments=remove_segments,
        output_order=output_order,
    )


# ---------------------------------------------------------------------
# 1. EditPlan.output_order round-trips through to_dict/from_dict
# ---------------------------------------------------------------------
plan_with_order = make_plan(output_order=[SCENE_C, SCENE_A, SCENE_B])
round_tripped = EditPlan.from_dict(plan_with_order.to_dict())
check("output_order survives to_dict/from_dict", round_tripped.output_order == [SCENE_C, SCENE_A, SCENE_B])

plan_no_order = make_plan(output_order=None)
check("output_order stays None through to_dict/from_dict when unset", EditPlan.from_dict(plan_no_order.to_dict()).output_order is None)

# ---------------------------------------------------------------------
# 2. process_edit_plan applies a VALID reorder to keep_order, leaves
#    keep_ranges chronological.
# ---------------------------------------------------------------------
resolved = edit_plan_applier.process_edit_plan(make_plan(output_order=[SCENE_C, SCENE_A, SCENE_B]), media_duration_seconds=DURATION)
check("keep_ranges stays chronological (A, B, C)", resolved.keep_ranges == [SCENE_A, SCENE_B, SCENE_C])
check("keep_order reflects the requested reorder (C, A, B)", resolved.keep_order == [SCENE_C, SCENE_A, SCENE_B])
check("No warning for a valid reorder", not any("reorder" in w.lower() for w in resolved.warnings))

# ---------------------------------------------------------------------
# 3. INVALID reorder requests are rejected safely (fallback to
#    chronological order + a warning), never corrupt the export.
# ---------------------------------------------------------------------
# 3a. Wrong count (only 2 of the 3 real ranges).
resolved_bad_count = edit_plan_applier.process_edit_plan(
    make_plan(output_order=[SCENE_C, SCENE_A]), media_duration_seconds=DURATION,
)
check("Wrong-count reorder falls back to chronological keep_order", resolved_bad_count.keep_order == [SCENE_A, SCENE_B, SCENE_C])
check("Wrong-count reorder produces a warning", any("reorder" in w.lower() for w in resolved_bad_count.warnings))

# 3b. A range that doesn't match any real KEEP range at all.
resolved_bad_range = edit_plan_applier.process_edit_plan(
    make_plan(output_order=[SCENE_C, SCENE_A, (100.0, 110.0)]), media_duration_seconds=DURATION,
)
check("Bogus-range reorder falls back to chronological keep_order", resolved_bad_range.keep_order == [SCENE_A, SCENE_B, SCENE_C])
check("Bogus-range reorder produces a warning", any("reorder" in w.lower() for w in resolved_bad_range.warnings))

# 3c. No footage is ever lost or duplicated even when reorder is rejected.
check(
    "Rejected reorder still keeps the exact same total footage",
    sorted(resolved_bad_count.keep_order) == sorted(resolved.keep_ranges),
)

# ---------------------------------------------------------------------
# 4. resolve_xml_exporter lays clips out in keep_order, not source order.
# ---------------------------------------------------------------------
tmp_video = Path(tempfile.gettempdir()) / "reorder_test_source.mp4"
xml_reordered = build_edit_xml(resolved, tmp_video, fps=30.0, width=1920, height=1080)
root = ET.fromstring(xml_reordered)
video_clips = root.findall("./sequence/media/video/track/clipitem")
source_ins = [int(c.findtext("in")) for c in video_clips]
# 30fps: Scene C starts at 40s -> frame 1200; Scene A at 0s -> frame 0; Scene B at 20s -> frame 600.
check("Exported clip order follows keep_order (C, A, B), not chronological", source_ins == [1200, 0, 600])

# And the default (no reorder requested) path is completely unchanged:
resolved_default = edit_plan_applier.process_edit_plan(make_plan(output_order=None), media_duration_seconds=DURATION)
xml_default = build_edit_xml(resolved_default, tmp_video, fps=30.0, width=1920, height=1080)
default_ins = [int(c.findtext("in")) for c in ET.fromstring(xml_default).findall("./sequence/media/video/track/clipitem")]
check("No reorder requested -> export stays chronological (A, B, C)", default_ins == [0, 600, 1200])

# ---------------------------------------------------------------------
# 5. EditPlanChatService: AI response with a "reorder" field end-to-end.
# ---------------------------------------------------------------------
class ReorderDummyProvider(AIProvider):
    @property
    def name(self):
        return "dummy"

    def list_models(self):
        return ["dummy-model"]

    def generate_reply(self, history, model):
        return (
            '{"reply": "Moved Scene C to the front as requested.", '
            '"changes": [], '
            f'"reorder": [{{"start": {SCENE_C[0]}, "end": {SCENE_C[1]}}}, '
            f'{{"start": {SCENE_A[0]}, "end": {SCENE_A[1]}}}, '
            f'{{"start": {SCENE_B[0]}, "end": {SCENE_B[1]}}}]}}'
        )

    def generate_reply_stream(self, history, model, on_chunk):
        text = self.generate_reply(history, model)
        on_chunk(text)
        return text


transcript = Transcript(
    language="en", language_probability=0.99,
    segments=[TranscriptSegment(start_seconds=0, end_seconds=DURATION, text="scene a, scene b, scene c")],
)
video = VideoProject(file_path=tmp_video, duration_seconds=DURATION)
analysis = AnalysisResult(video=video, transcript=transcript, frames=FrameCollection(interval_seconds=5.0))

chat = EditPlanChatService(provider=ReorderDummyProvider(), model="dummy-model")
chat.start(analysis, make_plan(output_order=None))
turn = chat.send("Move scene C (40s-50s) to the very front.")

check("Chat's updated plan has the requested output_order", turn.updated_plan.output_order == [SCENE_C, SCENE_A, SCENE_B])

resolved_from_chat = edit_plan_applier.process_edit_plan(turn.updated_plan, media_duration_seconds=DURATION)
check("Chat-driven reorder flows through process_edit_plan correctly", resolved_from_chat.keep_order == [SCENE_C, SCENE_A, SCENE_B])

xml_from_chat = build_edit_xml(resolved_from_chat, tmp_video, fps=30.0, width=1920, height=1080)
chat_ins = [int(c.findtext("in")) for c in ET.fromstring(xml_from_chat).findall("./sequence/media/video/track/clipitem")]
check("Chat-driven reorder produces the correct exported clip order (C, A, B)", chat_ins == [1200, 0, 600])

# A later content-only chat turn (no "reorder" key at all) must NOT reset
# the order that was set previously -- it should carry over.
class NoReorderDummyProvider(AIProvider):
    @property
    def name(self):
        return "dummy"

    def list_models(self):
        return ["dummy-model"]

    def generate_reply(self, history, model):
        return '{"reply": "No changes needed.", "changes": []}'

    def generate_reply_stream(self, history, model, on_chunk):
        text = self.generate_reply(history, model)
        on_chunk(text)
        return text


chat2 = EditPlanChatService(provider=NoReorderDummyProvider(), model="dummy-model")
chat2.start(analysis, turn.updated_plan)
turn2 = chat2.send("Actually never mind, that's fine.")
check("A content-only follow-up turn preserves the earlier reorder", turn2.updated_plan.output_order == [SCENE_C, SCENE_A, SCENE_B])

print(
    "\nAll clip-reordering checks passed: EditPlan.output_order round-trips, process_edit_plan "
    "validates and safely falls back on bad reorder requests, resolve_xml_exporter lays out clips "
    "in the requested order (unchanged when no reorder is requested), and EditPlanChatService can "
    "both set a new order from a 'reorder' AI response and preserve it across later content-only turns."
)
