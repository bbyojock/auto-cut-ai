"""Manual functional test for Version 4.5 features (not part of the shipped project).

Run: python3 _manual_test_v45.py
"""
import sys
import tempfile
from pathlib import Path

import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ai.base_provider import AIProvider
from ai.edit_planner import EditPlanner
from ai.editing_rules import EditingRules
from ai.game_profiles import get_game_profile, available_game_profiles
from ai.rule_merger import build_effective_rules
from models.frame import FrameCollection
from models.video_project import VideoProject
from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan, EditPlanSegment
from models.editing_instructions import EditingInstructionSet
from models.editing_rule import EditingRule
from models.personal_profile import PersonalEditingProfile, PreferenceStrength
from models.transcript import Transcript, TranscriptSegment
from services.edit_plan_cache_service import EditPlanCacheService
from services.edit_plan_chat_service import EditPlanChatService
from services.edit_plan_revision_service import EditPlanRevisionService
from services.editing_instructions_service import EditingInstructionsService
from services.personal_profile_service import PersonalProfileService


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


# ---------------------------------------------------------------------
# Feature 13/14: personal preference gradual learning
# ---------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp())
pps = PersonalProfileService(profile_path=tmp / "personal_profile.json")
for _ in range(3):
    pps.record_feedback("Combat", agrees_with_keep=True)
pref = pps.profile.get_or_create("Combat")
check("Personal preference strengthens after 3 consistent confirmations", pref.strength == PreferenceStrength.HIGH)
check("Personal preference confidence > 0 after feedback", pref.confidence > 0)

pps2 = PersonalProfileService(profile_path=tmp / "personal_profile.json")
check("Personal profile reloads from disk", pps2.profile.get_or_create("Combat").strength == PreferenceStrength.HIGH)

# A single contradicting correction should NOT flip strength immediately.
pps.record_feedback("Combat", agrees_with_keep=False)
check(
    "A single contradicting correction does not immediately flip strength",
    pps.profile.get_or_create("Combat").strength == PreferenceStrength.HIGH,
)

# ---------------------------------------------------------------------
# Feature 1/13: instruction classification (temporary vs persistent)
# ---------------------------------------------------------------------
check("Temporary instruction classified correctly", not PersonalProfileService.is_persistent_instruction("Make this video faster."))
check("Persistent instruction classified correctly", PersonalProfileService.is_persistent_instruction("Always keep clutch moments."))

instr_service = EditingInstructionsService(personal_profile_service=pps, storage_path=tmp / "persistent_instructions.json")
instr_service.add_instruction("Never remove kills.", known_topics=["Kill", "Combat"])
instr_service.add_instruction("Make the intro especially fast.")
prompt_text = instr_service.to_prompt_text()
check("Persistent instruction appears in prompt text", "Never remove kills." in prompt_text)
check("Video-only instruction appears in prompt text", "Make the intro especially fast." in prompt_text)
check(
    "Persistent instruction learned into personal profile",
    pps.profile.get_or_create("Kill").strength == PreferenceStrength.VERY_HIGH,
)

# New project should drop video-only instructions but keep persistent ones
instr_service.new_project()
check("new_project() clears video-only instructions", len(instr_service.current.video_instructions()) == 0)
check("new_project() keeps persistent instructions", len(instr_service.current.persistent_instructions()) == 1)

# ---------------------------------------------------------------------
# Feature 6/16: game profile + personal profile + rule priority merge
# ---------------------------------------------------------------------
base = EditingRules.default()
game = get_game_profile("minecraft_bedwars")
check("Bedwars profile has rules", len(game.rules) > 0)
personal = PersonalEditingProfile()
personal.set_explicit("Bed Destroyed", PreferenceStrength.LOW)  # user disagrees with the game-profile default (HIGH)
effective = build_effective_rules(base_rules=base, game_profile_rules=game, personal_profile=personal)
bed_rule = next(r for r in effective.rules if r.name == "Bed Destroyed")
check(
    "Personal preference overrides game-profile rule of the same name (Feature 16 priority)",
    bed_rule.score == PreferenceStrength.LOW.score,
)
check("Game profile rules not overridden by personal profile stay intact", any(r.name == "Kill" for r in effective.rules))
check(">9 available game profiles registered", len(available_game_profiles()) >= 9)

# ---------------------------------------------------------------------
# Feature 3: revision history (undo/redo/restore/compare)
# ---------------------------------------------------------------------
seg1 = EditPlanSegment(start_seconds=0, end_seconds=10, action="keep", reason="Intro.")
seg2 = EditPlanSegment(start_seconds=10, end_seconds=20, action="remove", reason="Silence.")
plan_v1 = EditPlan(target_length_seconds=10, keep_segments=[seg1], remove_segments=[seg2], confidence=0.9, reasons=["r"], warnings=[])

rev = EditPlanRevisionService()
rev.start(plan_v1)
seg2_kept = EditPlanSegment(start_seconds=10, end_seconds=20, action="keep", reason="User requested the conversation to remain.")
plan_v2 = EditPlan(target_length_seconds=20, keep_segments=[seg1, seg2_kept], remove_segments=[], confidence=0.9, reasons=["r"], warnings=[])
rev.record_change(plan_v2, "User restored 00:10-00:20.")

check("Revision history has 2 revisions", len(rev.list_revisions()) == 2)
check("Current plan is v2 after recording a change", rev.current_plan.kept_duration_seconds == 20)
check("can_undo is True after a change", rev.can_undo())
undone = rev.undo()
check("Undo restores v1", undone.kept_duration_seconds == 10)
check("can_redo is True after undo", rev.can_redo())
redone = rev.redo()
check("Redo restores v2", redone.kept_duration_seconds == 20)

diffs = rev.compare(0, 1)
check("compare() finds the changed segment", len(diffs) == 1 and diffs[0].before_action == "remove" and diffs[0].after_action == "keep")

# ---------------------------------------------------------------------
# Feature 2/17: EditPlan chat applies a localized patch, no Whisper rerun
# ---------------------------------------------------------------------
class DummyProvider(AIProvider):
    @property
    def name(self):
        return "dummy"

    def list_models(self):
        return ["dummy-model"]

    def generate_reply(self, history, model):
        return (
            '{"reply": "Restored 00:10-00:20 because the conversation there is important.", '
            '"changes": [{"start": 10, "end": 20, "action": "keep", '
            '"reason": "User requested the conversation to remain."}]}'
        )

    def generate_reply_stream(self, history, model, on_chunk):
        text = self.generate_reply(history, model)
        on_chunk(text)
        return text


transcript = Transcript(language="en", language_probability=0.99, segments=[TranscriptSegment(start_seconds=0, end_seconds=20, text="hello there, this is a test")])
video = VideoProject(file_path=tmp / "video.mp4", duration_seconds=20)
analysis = AnalysisResult(video=video, transcript=transcript, frames=FrameCollection(interval_seconds=5.0))

chat_rev = EditPlanRevisionService()
pps3 = PersonalProfileService(profile_path=tmp / "personal_profile_chat.json")
chat = EditPlanChatService(provider=DummyProvider(), model="dummy-model", revision_service=chat_rev, personal_profile_service=pps3)
chat.start(analysis, plan_v1)
turn = chat.send("Keep 03:15~04:00, the conversation there is important.")
check("Chat turn produced exactly 1 changed segment", len(turn.changed_segments) == 1)
check("Chat turn's changed segment is now 'keep'", turn.changed_segments[0].action == "keep")
check("Updated plan reflects the change", turn.updated_plan.kept_duration_seconds == 20)
check("Revision was recorded for the chat turn", len(chat_rev.list_revisions()) == 2)

# ---------------------------------------------------------------------
# Feature 12: split cache -- analysis cache independent of plan cache
# ---------------------------------------------------------------------
video_file = tmp / "video2.mp4"
video_file.write_bytes(b"fake video bytes")
cache = EditPlanCacheService(cache_root=tmp / "cache")
akey = cache.analysis_cache_key(video_file, "faster-whisper", "base", 5.0)
cache.save_analysis(akey, analysis)
check("Analysis cache hit after save", cache.get_analysis(akey) is not None)

rules_a = EditingRules(name="a", rules=[EditingRule(name="x", action="keep", score=1, description="d")])
rules_b = EditingRules(name="b", rules=[EditingRule(name="x", action="keep", score=5, description="d")])
key1 = cache.plan_cache_key(akey, "general", None, rules_fingerprint=rules_a.fingerprint())
key2 = cache.plan_cache_key(akey, "general", None, rules_fingerprint=rules_b.fingerprint())
check("Different rules -> different plan cache key (instructions change invalidates plan only)", key1 != key2)

key3 = cache.plan_cache_key(akey, "general", None, rules_fingerprint=rules_a.fingerprint())
check("Same inputs -> same plan cache key (deterministic)", key1 == key3)

print("\nAll manual V4.5 checks passed.")
