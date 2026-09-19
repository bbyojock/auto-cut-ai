"""Runtime GUI smoke test under Xvfb (not part of the shipped project).

Actually constructs MainWindow + every page with a dummy provider and a
temp config dir, to catch anything a pure import-level check can't (Tk
widget construction errors, page __init__ crashes, etc.). Never touches
the real ~/.autocutai config.
"""
import sys
import tempfile
from pathlib import Path

import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ai.base_provider import AIProvider
from ai.edit_planner import EditPlanner
from ai.provider_factory import ProviderFactory
from ai.key_manager import APIKeyManager
from config.config_manager import ConfigManager
from core.app_context import AppContext
from core.session import ConversationSession
from services.chat_service import ChatService
from utils.constants import PAGE_HOME, PAGE_CHAT, PAGE_EDIT_PLAN, PAGE_SETTINGS, PAGE_DIAGNOSTICS, PAGE_LOGS, PAGE_PERSONAL_PROFILE


class DummyProvider(AIProvider):
    @property
    def name(self):
        return "dummy"

    def list_models(self):
        return ["dummy-model"]

    def generate_reply(self, history, model):
        return "ok"

    def generate_reply_stream(self, history, model, on_chunk):
        on_chunk("ok")
        return "ok"


tmp = Path(tempfile.mkdtemp())
cm = ConfigManager(config_path=tmp / "config.json") if "config_path" in ConfigManager.__init__.__code__.co_varnames else ConfigManager()
cm.load()

provider = DummyProvider()
session = ConversationSession()
chat = ChatService(provider=provider, session=session, model="dummy-model")
planner = EditPlanner(provider=provider, model="dummy-model")

ctx = AppContext(
    config_manager=cm,
    provider_factory=ProviderFactory(),
    key_manager=APIKeyManager([]),
    session=session,
    chat_service=chat,
    edit_planner=planner,
)
ctx.personal_profile_service = ctx.personal_profile_service.__class__(profile_path=tmp / "personal_profile.json")
ctx.editing_instructions_service = ctx.editing_instructions_service.__class__(
    personal_profile_service=ctx.personal_profile_service, storage_path=tmp / "persistent_instructions.json"
)

from ui.main_window import MainWindow

window = MainWindow(ctx)
print("[PASS] MainWindow constructed")

for page_id in (PAGE_HOME, PAGE_CHAT, PAGE_EDIT_PLAN, PAGE_SETTINGS, PAGE_DIAGNOSTICS, PAGE_LOGS, PAGE_PERSONAL_PROFILE):
    window.show_page(page_id)
    window.update()
    print(f"[PASS] Page '{page_id}' constructed and shown without raising")

# --- Version 4.5 interaction smoke tests -----------------------------
edit_plan_page = window._pages[PAGE_EDIT_PLAN]

# Feature 1: type + add an editing instruction, confirm it renders.
edit_plan_page._instruction_entry.insert(0, "Never remove kills.")
edit_plan_page._add_instruction_clicked()
window.update()
assert len(edit_plan_page._instructions_service.current.instructions) == 1
print("[PASS] Editing Instructions panel: add instruction works")

edit_plan_page._instruction_entry.insert(0, "Always keep clutch moments.")
edit_plan_page._add_instruction_clicked()
window.update()
assert len(edit_plan_page._instructions_service.current.instructions) == 2
print("[PASS] Editing Instructions panel: second (persistent) instruction works")

edit_plan_page._remove_instruction(0)
window.update()
assert len(edit_plan_page._instructions_service.current.instructions) == 1
print("[PASS] Editing Instructions panel: remove instruction works")

# Feature 3: undo/redo buttons should be safely disabled with no plan yet.
edit_plan_page._undo_clicked()
edit_plan_page._redo_clicked()
window.update()
print("[PASS] Undo/Redo are no-ops (not crashing) before any EditPlan exists")

# Feature 15: Personal Profile page -- add and reset a preference.
profile_page = window._pages[PAGE_PERSONAL_PROFILE]
profile_page._topic_entry.insert(0, "Combat")
profile_page._add_or_update_clicked()
window.update()
assert profile_page._service.profile.get_or_create("Combat").strength.value == "high"
print("[PASS] Personal Profile page: set an explicit preference")

profile_page._service.reset_preference("Combat")
profile_page._refresh()
window.update()
print("[PASS] Personal Profile page: reset a preference without crashing")

# Feature 2/17: open the EditPlan Chat window with a synthetic plan+analysis.
from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan, EditPlanSegment
from models.frame import FrameCollection
from models.transcript import Transcript, TranscriptSegment
from models.video_project import VideoProject

transcript = Transcript(language="en", language_probability=0.99, segments=[TranscriptSegment(0, 20, "hello there")])
video = VideoProject(file_path=tmp / "video.mp4", duration_seconds=20)
analysis = AnalysisResult(video=video, transcript=transcript, frames=FrameCollection(interval_seconds=5.0))
seg1 = EditPlanSegment(start_seconds=0, end_seconds=10, action="keep", reason="Intro.")
seg2 = EditPlanSegment(start_seconds=10, end_seconds=20, action="remove", reason="Silence.")
plan = EditPlan(target_length_seconds=10, keep_segments=[seg1], remove_segments=[seg2], confidence=0.9, reasons=["r"], warnings=[])

edit_plan_page._current_analysis = analysis
edit_plan_page._current_plan = plan
ctx.edit_plan_chat_service.start(analysis, plan)

from ui.widgets.edit_plan_chat_window import EditPlanChatWindow
chat_window = EditPlanChatWindow(window, chat_service=ctx.edit_plan_chat_service, on_plan_updated=edit_plan_page._on_chat_plan_updated)
window.update()
chat_window.destroy()
window.update()
print("[PASS] EditPlanChatWindow constructs without raising")

# Version 4.6: render a plan that includes a COMPRESS segment (the new
# three-way action) to confirm the timeline UI handles it without raising.
from models.segment_scores import SegmentScores
compress_plan = EditPlan(
    target_length_seconds=10,
    keep_segments=[EditPlanSegment(0, 5, "keep", "Intro.", scores=SegmentScores(overall=7))],
    compress_segments=[EditPlanSegment(5, 25, "compress", "Ordinary building.", compression_speed_factor=3.0, scores=SegmentScores(overall=2))],
    remove_segments=[EditPlanSegment(25, 30, "remove", "Dead air.")],
)
edit_plan_page._render_loaded_plan(compress_plan, "compress_test.mp4")
window.update()
print("[PASS] EditPlan timeline renders a COMPRESS segment without raising")

window.destroy()
print("\nAll GUI smoke checks passed.")
