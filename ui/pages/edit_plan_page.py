"""AI Editor page (Version 4): select a video and generate an EditPlan.

Orchestration (cache lookup, the analysis pipeline, streaming AI
generation) all lives in :class:`services.edit_generation_service.EditGenerationService`;
this page is a thin view that runs it on a background thread, forwards
progress to a :class:`ui.widgets.progress_dialog.ProgressDialog`, and
renders the result. Nothing here edits video, renders anything, generates
subtitles, or talks to DaVinci Resolve -- that's unchanged from Version 3.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Literal, Optional

import customtkinter as ctk

from ai.editing_rules import EditingRules
from ai.game_profiles import get_game_profile
from ai.rule_merger import build_effective_rules
from core.cancellation import CancellationToken
from core.exceptions import AutoCutAIError, OperationCancelledError
from models.analysis_result import AnalysisResult
from models.angle_switch_plan import AngleSwitchPlan
from models.edit_plan import EditPlan
from models.folder_analysis_result import FolderAnalysisResult, FolderClipEntry
from models.generation_progress import GenerationStage, ProgressEvent
from services.edit_generation_service import EditGenerationResult
from services.edit_plan_io_service import EditPlanFileError, EditPlanIOService
from services.export_service import ExportService
from services.logging_service import LoggingService
from ui.pages.base_page import BasePage
from ui.widgets.folder_results_dialog import FolderResultsDialog
from ui.widgets.progress_dialog import ProgressDialog
from ui.widgets.prompt_debug_window import PromptDebugWindow
from ui.widgets.resolve_export_dialog import ResolveExportDialog
from ui.widgets.resolve_xml_export_dialog import ResolveXMLExportDialog
from utils.constants import EDIT_ACTION_COMPRESS, SUPPORTED_VIDEO_EXTENSIONS
from video.video_importer import VideoImporter

_LOGGER = LoggingService.get_logger("ui.edit_plan_page")

_EventKind = Literal["progress", "done", "cancelled", "error"]
_FolderEventKind = Literal["progress", "done", "cancelled", "error"]


@dataclass
class _GenerationMessage:
    """One item handed from the background thread to the Tk polling loop."""

    kind: _EventKind
    progress: Optional[ProgressEvent] = None
    result: Optional[EditGenerationResult] = None
    error: Optional[str] = None


@dataclass
class _FolderMessage:
    """One item from the folder-analysis background thread (Version 5.3)."""

    kind: _FolderEventKind
    status_text: str = ""
    result: Optional[FolderAnalysisResult] = None
    error: Optional[str] = None


class EditPlanPage(BasePage):
    """Version 4 AI Editor: video in, human-readable EditPlan out.

    Flow: select a video -> :meth:`services.edit_generation_service.EditGenerationService.generate`
    (cache lookup, then Video -> Audio -> Whisper -> Frames -> AI, all with
    live progress + cancel support) -> render the result. Runs on a
    background thread exactly like :class:`ui.pages.chat_page.ChatPage`
    does for chat, so the GUI never freezes (Feature 18).
    """

    _POLL_INTERVAL_MS = 100

    def build(self) -> None:
        self.grid_rowconfigure(5, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._video_path: Optional[Path] = None
        self._is_running = False
        self._cancel_token: Optional[CancellationToken] = None
        self._progress_dialog: Optional[ProgressDialog] = None
        self._result_queue: "queue.Queue[_GenerationMessage]" = queue.Queue()
        self._current_analysis: Optional[AnalysisResult] = None
        self._current_plan: Optional[EditPlan] = None
        self._current_result: Optional[EditGenerationResult] = None
        # Version 5.3: folder-based batch import/analysis state.
        self._folder_queue: "queue.Queue[_FolderMessage]" = queue.Queue()
        self._folder_cancel_token: Optional[CancellationToken] = None
        self._is_folder_running = False
        self._last_folder_result: Optional[FolderAnalysisResult] = None
        self._edit_plan_io = self.context.edit_plan_io_service
        self._instructions_service = self.context.editing_instructions_service
        self._revision_service = self.context.edit_plan_revision_service
        self._chat_service = self.context.edit_plan_chat_service
        self._resolve_export_service = self.context.resolve_export_service

        header = ctk.CTkLabel(self, text="AI Editor", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, padx=30, pady=(30, 4), sticky="w")

        subheader = ctk.CTkLabel(
            self,
            text="Analyze a video and let the AI decide what to keep and remove. "
            "Nothing is edited or rendered yet.",
            text_color="gray60",
            anchor="w",
        )
        subheader.grid(row=1, column=0, padx=30, pady=(0, 10), sticky="w")

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=2, column=0, padx=30, pady=(0, 10), sticky="ew")
        controls.grid_columnconfigure(1, weight=1)

        self._select_button = ctk.CTkButton(controls, text="Select Video...", command=self._select_video)
        self._select_button.grid(row=0, column=0, padx=(0, 10), pady=4)

        self._file_label = ctk.CTkLabel(controls, text="No video selected.", anchor="w", text_color="gray60")
        self._file_label.grid(row=0, column=1, sticky="ew", pady=4)

        self._load_button = ctk.CTkButton(
            controls, text="Load EditPlan...", width=140, fg_color="gray30", hover_color="gray20",
            command=self._load_edit_plan_clicked,
        )
        self._load_button.grid(row=0, column=2, padx=(10, 0), pady=4)

        # Version 5.3: drop a whole folder of raw footage in at once. If it
        # looks like documentary footage, clips are auto-arranged into a
        # rough chronological order; either way, each clip gets an
        # automatically recommended editing profile (documentary or
        # variety/예능) before it's handed to the normal single-video flow.
        self._folder_button = ctk.CTkButton(
            controls, text="Select Folder... (Batch)", fg_color="#3a6ea5", hover_color="#2f5a86",
            command=self._select_folder,
        )
        self._folder_button.grid(row=0, column=3, padx=(10, 0), pady=4)

        self._run_button = ctk.CTkButton(
            controls, text="Generate Edit Plan", command=self._run_clicked, state="disabled",
        )
        self._run_button.grid(row=1, column=0, columnspan=3, pady=(10, 0), sticky="ew")

        self._status_label = ctk.CTkLabel(controls, text="", text_color="gray60", anchor="w")
        self._status_label.grid(row=2, column=0, columnspan=3, pady=(8, 0), sticky="w")

        # Version 4.5, Feature 1 -- free-text Editing Instructions panel.
        # Persistent instructions (e.g. "Always keep clutch moments.") are
        # auto-detected and remembered across videos (Feature 13); this
        # panel just lets the user type them and see what's currently
        # active for this video.
        instructions_panel = ctk.CTkFrame(self)
        instructions_panel.grid(row=3, column=0, padx=30, pady=(0, 10), sticky="ew")
        instructions_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            instructions_panel, text="Editing Instructions", font=ctk.CTkFont(weight="bold"), anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            instructions_panel,
            text='Describe how you want this video edited, e.g. "Never remove kills." or "Make the intro faster."',
            text_color="gray60", anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 6))

        entry_row = ctk.CTkFrame(instructions_panel, fg_color="transparent")
        entry_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))
        entry_row.grid_columnconfigure(0, weight=1)
        self._instruction_entry = ctk.CTkEntry(entry_row, placeholder_text="Type an editing instruction...")
        self._instruction_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._instruction_entry.bind("<Return>", lambda _event: self._add_instruction_clicked())
        ctk.CTkButton(entry_row, text="Add", width=70, command=self._add_instruction_clicked).grid(row=0, column=1)

        self._instructions_list_frame = ctk.CTkFrame(instructions_panel, fg_color="transparent")
        self._instructions_list_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))
        self._instructions_list_frame.grid_columnconfigure(0, weight=1)
        self._refresh_instructions_list()

        # Feature 13/15/16/17 action row -- only meaningful once a plan exists.
        self._action_bar = ctk.CTkFrame(self, fg_color="transparent")
        self._action_bar.grid(row=4, column=0, padx=30, pady=(0, 10), sticky="ew")
        for col in range(10):
            self._action_bar.grid_columnconfigure(col, weight=1)

        self._save_button = ctk.CTkButton(self._action_bar, text="Save EditPlan...", command=self._save_edit_plan_clicked)
        self._prompt_debug_button = ctk.CTkButton(self._action_bar, text="Prompt Debug", command=self._show_prompt_debug)
        self._export_json_button = ctk.CTkButton(self._action_bar, text="Export JSON", command=lambda: self._export("json"))
        self._export_txt_button = ctk.CTkButton(self._action_bar, text="Export TXT", command=lambda: self._export("txt"))
        self._export_md_button = ctk.CTkButton(self._action_bar, text="Export Markdown", command=lambda: self._export("md"))
        # Version 4.5, Features 2/3: chat with the EditPlan + undo/redo.
        self._chat_button = ctk.CTkButton(self._action_bar, text="Chat about EditPlan", fg_color="#2f6f4f", hover_color="#255a3f", command=self._open_edit_plan_chat)
        self._undo_button = ctk.CTkButton(self._action_bar, text="Undo", width=70, fg_color="gray30", hover_color="gray20", command=self._undo_clicked)
        self._redo_button = ctk.CTkButton(self._action_bar, text="Redo", width=70, fg_color="gray30", hover_color="gray20", command=self._redo_clicked)
        # Version 5: real cut-editing in DaVinci Resolve.
        self._resolve_button = ctk.CTkButton(self._action_bar, text="Apply to Resolve...", fg_color="#8a4b1f", hover_color="#6f3c18", command=self._open_resolve_export)
        # Version 5 (Resolve Free support): XML export needs no Resolve API/installation at all.
        self._resolve_xml_button = ctk.CTkButton(self._action_bar, text="Export Resolve XML...", fg_color="#3a6ea5", hover_color="#2f5a86", command=self._open_resolve_xml_export)
        for i, button in enumerate(
            (
                self._save_button, self._prompt_debug_button, self._export_json_button, self._export_txt_button,
                self._export_md_button, self._chat_button, self._undo_button, self._redo_button, self._resolve_button,
                self._resolve_xml_button,
            )
        ):
            button.grid(row=0, column=i, padx=4, sticky="ew")
        self._set_action_bar_enabled(False)

        self._benchmark_label = ctk.CTkLabel(self, text="", text_color="gray60", anchor="w", justify="left", font=ctk.CTkFont(family="Courier", size=11))
        self._benchmark_label.grid(row=6, column=0, padx=30, pady=(0, 8), sticky="w")

        self._result_frame = ctk.CTkScrollableFrame(self, label_text="")
        self._result_frame.grid(row=5, column=0, padx=30, pady=(0, 8), sticky="nsew")
        self._result_frame.grid_columnconfigure(0, weight=1)
        self._show_placeholder("Select a video, or load a previously saved EditPlan, to get started.")

    def on_show(self) -> None:
        if not self._is_running:
            self._update_idle_status()

    # ------------------------------------------------------------------
    # Video selection
    # ------------------------------------------------------------------
    def _select_video(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in SUPPORTED_VIDEO_EXTENSIONS)
        path_str = filedialog.askopenfilename(
            title="Select a video", filetypes=[("Video files", extensions), ("All files", "*.*")],
        )
        if not path_str:
            return

        self._video_path = Path(path_str)
        self._file_label.configure(text=self._video_path.name, text_color=("gray10", "gray90"))
        self._run_button.configure(state="normal")
        # Version 4.5, Feature 1/13: a new video starts a fresh set of
        # this-video-only instructions; persistent (standing) preferences
        # carry over automatically.
        self._instructions_service.new_project()
        self._refresh_instructions_list()
        LoggingService.log_user_action(_LOGGER, "select_video", file=self._video_path.name)

    # ------------------------------------------------------------------
    # Version 5.3: folder-based batch import + analysis
    # ------------------------------------------------------------------
    def _select_folder(self) -> None:
        if self._is_running or self._is_folder_running:
            return

        folder_str = filedialog.askdirectory(title="Select a folder of raw footage")
        if not folder_str:
            return

        LoggingService.log_user_action(_LOGGER, "select_folder", folder=folder_str)
        self._folder_cancel_token = CancellationToken()
        self._is_folder_running = True
        self._set_running(True)
        self._status_label.configure(text="Scanning folder...", text_color="gray60")

        thread = threading.Thread(target=self._run_folder_analysis, args=(folder_str,), daemon=True)
        thread.start()
        self.after(self._POLL_INTERVAL_MS, self._poll_folder_result)

    def _run_folder_analysis(self, folder_path: str) -> None:
        """Runs on a background thread -- never touches Tk widgets directly."""
        cancel_token = self._folder_cancel_token
        assert cancel_token is not None

        def on_progress(index: int, total: int, path, stage: GenerationStage) -> None:
            from models.generation_progress import STAGE_LABELS

            text = f"Analyzing {index}/{total}: {path.name} -- {STAGE_LABELS.get(stage, stage.value)}"
            self._folder_queue.put(_FolderMessage(kind="progress", status_text=text))

        try:
            config = self.context.config_manager.config
            result = self.context.folder_analysis_service.analyze_folder(
                folder_path,
                recursive=False,
                frame_interval_seconds=config.frame_interval_seconds,
                on_progress=on_progress,
                cancel_token=cancel_token,
                cache_service=self.context.edit_plan_cache_service,
                whisper_backend=config.whisper.backend,
                whisper_model=config.whisper.model_size,
            )
            self._folder_queue.put(_FolderMessage(kind="done", result=result))
        except OperationCancelledError:
            self._folder_queue.put(_FolderMessage(kind="cancelled"))
        except (NotADirectoryError, AutoCutAIError) as exc:
            _LOGGER.error("Folder analysis failed: %s", exc)
            self._folder_queue.put(_FolderMessage(kind="error", error=str(exc)))
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors too
            _LOGGER.exception("Unexpected error during folder analysis")
            self._folder_queue.put(_FolderMessage(kind="error", error=f"Unexpected error: {exc}"))

    def _poll_folder_result(self) -> None:
        drained_terminal = False
        try:
            while True:
                message = self._folder_queue.get_nowait()
                if message.kind == "progress":
                    self._status_label.configure(text=message.status_text, text_color="gray60")
                    continue

                drained_terminal = True
                self._handle_folder_terminal_message(message)
                break
        except queue.Empty:
            pass

        if not drained_terminal:
            self.after(self._POLL_INTERVAL_MS, self._poll_folder_result)

    def _handle_folder_terminal_message(self, message: _FolderMessage) -> None:
        self._is_folder_running = False
        self._set_running(False)

        if message.kind == "done" and message.result is not None:
            self._last_folder_result = message.result
            self._update_idle_status()
            if not message.result.clips:
                messagebox.showinfo(
                    "Folder Analysis",
                    "No supported video files could be analyzed in that folder.",
                )
                return
            FolderResultsDialog(
                self.winfo_toplevel(), message.result,
                on_use_clip=self._use_folder_clip,
                on_build_multicam_plan=self._export_multicam_plan,
            )
        elif message.kind == "cancelled":
            self._update_idle_status()
        elif message.kind == "error":
            self._update_idle_status()
            messagebox.showerror("Folder Analysis", message.error or "An unknown error occurred.")

    def _use_folder_clip(self, clip: FolderClipEntry) -> None:
        """Load a clip picked from :class:`FolderResultsDialog` into the normal flow.

        Applies the folder's auto-recommended :mod:`ai.game_profiles`
        profile (documentary/variety) to the persisted config so the next
        "Generate Edit Plan" click for this clip uses it automatically --
        exactly as if the user had picked that profile by hand.
        """
        assert self._last_folder_result is not None

        self._video_path = clip.analysis.video.file_path
        self._file_label.configure(text=self._video_path.name, text_color=("gray10", "gray90"))
        self._run_button.configure(state="normal")
        self._instructions_service.new_project()
        self._refresh_instructions_list()

        recommended_profile = self._last_folder_result.recommended_profile
        config = self.context.config_manager.config
        if recommended_profile and recommended_profile != config.game_profile:
            config.game_profile = recommended_profile
            self.context.config_manager.save()

        LoggingService.log_user_action(
            _LOGGER, "use_folder_clip", file=self._video_path.name, profile=recommended_profile,
        )

    def _export_multicam_plan(self, group_index: int) -> None:
        """Build & export an automatic angle-switching plan for one multicam
        take (Version 5.4), triggered from :class:`FolderResultsDialog`.

        This is a quick, synchronous, local computation (no AI provider
        call, no re-analysis) -- see :mod:`ai.multicam_angle_planner` --
        so it runs directly on the UI thread rather than through the
        background-thread/queue machinery the folder scan itself uses.
        """
        result = self._last_folder_result
        if result is None or not (0 <= group_index < len(result.multicam_groups)):
            return
        group = result.multicam_groups[group_index]

        analysis_by_path = {
            clip.analysis.video.file_path: clip.analysis
            for clip in result.clips
            if clip.multicam_group_index == group_index
        }
        if len(analysis_by_path) < 2:
            messagebox.showinfo("Multicam Auto-Cut", "이 촬영분에는 분석된 카메라가 1개뿐이라 앵글 전환이 필요 없습니다.")
            return

        try:
            plan: AngleSwitchPlan = self.context.multicam_edit_service.build_plan(group, analysis_by_path)
        except Exception as exc:  # noqa: BLE001 - surface any planner error to the user
            messagebox.showerror("Multicam Auto-Cut", f"앵글 전환 계획을 만들 수 없습니다: {exc}")
            return

        if not plan.segments:
            messagebox.showinfo("Multicam Auto-Cut", "이 촬영분에서 겹치는 구간을 찾지 못했습니다.")
            return

        default_name = f"multicam_{group.timestamp.strftime('%Y%m%d_%H%M%S')}.xml"
        output_str = filedialog.asksaveasfilename(
            title="Save Multicam Edit XML", defaultextension=".xml", initialfile=default_name,
            filetypes=[("Final Cut Pro XML", "*.xml"), ("All files", "*.*")],
        )
        if not output_str:
            return

        fps = next(
            (analysis.video.fps for analysis in analysis_by_path.values() if analysis.video.fps),
            30.0,
        )
        try:
            written_path = self.context.multicam_edit_service.export_xml(plan, Path(output_str), fps=fps)
        except AutoCutAIError as exc:
            messagebox.showerror("Multicam Auto-Cut", f"XML을 내보낼 수 없습니다: {exc}")
            return

        LoggingService.log_user_action(
            _LOGGER, "export_multicam_xml", switches=plan.switch_count, cameras=len(plan.cameras_used),
        )
        messagebox.showinfo(
            "Multicam Auto-Cut",
            f"카메라 {len(plan.cameras_used)}대, 앵글 전환 {plan.switch_count}회로 편집을 만들었습니다.\n\n"
            f"DaVinci Resolve에서 File > Import > Timeline...으로 아래 파일을 불러오세요:\n{written_path}",
        )

    # ------------------------------------------------------------------
    # Version 4.5, Feature 1: Editing Instructions panel
    # ------------------------------------------------------------------
    def _add_instruction_clicked(self) -> None:
        text = self._instruction_entry.get().strip()
        if not text:
            return
        known_topics = [rule.name for rule in EditingRules.default().rules]
        self._instructions_service.add_instruction(text, known_topics=known_topics)
        self._instruction_entry.delete(0, "end")
        self._refresh_instructions_list()

    def _refresh_instructions_list(self) -> None:
        for child in self._instructions_list_frame.winfo_children():
            child.destroy()

        instructions = self._instructions_service.current.instructions
        if not instructions:
            ctk.CTkLabel(
                self._instructions_list_frame, text="No editing instructions yet.", text_color="gray50", anchor="w",
            ).grid(row=0, column=0, sticky="w")
            return

        for index, instruction in enumerate(instructions):
            row = ctk.CTkFrame(self._instructions_list_frame, fg_color="transparent")
            row.grid(row=index, column=0, sticky="ew", pady=1)
            row.grid_columnconfigure(1, weight=1)
            badge = "🔁 Always" if instruction.scope == "persistent" else "🎬 This video"
            ctk.CTkLabel(row, text=badge, text_color="gray50", width=90, anchor="w").grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(row, text=instruction.text, anchor="w", justify="left", wraplength=560).grid(
                row=0, column=1, sticky="w", padx=(4, 8)
            )
            ctk.CTkButton(
                row, text="✕", width=28, fg_color="gray30", hover_color="#7a2d2d",
                command=lambda i=index: self._remove_instruction(i),
            ).grid(row=0, column=2, sticky="e")

    def _remove_instruction(self, index: int) -> None:
        self._instructions_service.remove_instruction(index)
        self._refresh_instructions_list()

    # ------------------------------------------------------------------
    # Version 4.5, Feature 3: Undo / Redo
    # ------------------------------------------------------------------
    def _undo_clicked(self) -> None:
        plan = self._revision_service.undo()
        if plan is not None:
            self._apply_revised_plan(plan)

    def _redo_clicked(self) -> None:
        plan = self._revision_service.redo()
        if plan is not None:
            self._apply_revised_plan(plan)

    def _apply_revised_plan(self, plan: EditPlan) -> None:
        self._current_plan = plan
        source_name = self._video_path.name if self._video_path else ""
        self._render_loaded_plan(plan, source_name or "current EditPlan")
        self._update_undo_redo_buttons()

    # ------------------------------------------------------------------
    # Version 4.5, Features 2/17: EditPlan Chat
    # ------------------------------------------------------------------
    def _open_edit_plan_chat(self) -> None:
        if self._current_analysis is None or self._current_plan is None:
            messagebox.showinfo(
                "Chat about EditPlan",
                "The EditPlan Chat needs the original transcript, which isn't available for a "
                "plan loaded from a file. Generate a fresh EditPlan to use this feature.",
            )
            return

        from ui.widgets.edit_plan_chat_window import EditPlanChatWindow

        EditPlanChatWindow(
            self.winfo_toplevel(), chat_service=self._chat_service, on_plan_updated=self._on_chat_plan_updated,
        )

    def _on_chat_plan_updated(self, plan: EditPlan) -> None:
        """Called (on the Tk thread) whenever the EditPlan Chat window applies a change."""
        self._current_plan = plan
        source_name = self._video_path.name if self._video_path else ""
        self._render_loaded_plan(plan, f"{source_name} · updated via EditPlan Chat" if source_name else "Updated via EditPlan Chat")
        self._update_undo_redo_buttons()

    # ------------------------------------------------------------------
    # Run generation (Features 1, 2, 3, 14)
    # ------------------------------------------------------------------
    def _run_clicked(self) -> None:
        if self._is_running or self._video_path is None:
            return

        self._cancel_token = CancellationToken()
        self._set_running(True)
        self._progress_dialog = ProgressDialog(self.winfo_toplevel(), on_cancel=self._cancel_token.cancel)

        thread = threading.Thread(target=self._run_generation, args=(self._video_path,), daemon=True)
        thread.start()
        self.after(self._POLL_INTERVAL_MS, self._poll_result)

    def _run_generation(self, video_path: Path) -> None:
        """Runs on a background thread -- never touches Tk widgets directly."""
        cancel_token = self._cancel_token
        assert cancel_token is not None

        def on_progress(event: ProgressEvent) -> None:
            self._result_queue.put(_GenerationMessage(kind="progress", progress=event))

        try:
            config = self.context.config_manager.config
            instructions_text = self._instructions_service.to_prompt_text()
            instructions_fingerprint = self._instructions_service.fingerprint()
            effective_rules = build_effective_rules(
                base_rules=EditingRules.default(),
                game_profile_rules=get_game_profile(config.game_profile),
                personal_profile=self.context.personal_profile_service.profile,
            )
            result = self.context.edit_generation_service.generate(
                video_path,
                on_progress=on_progress,
                cancel_token=cancel_token,
                whisper_backend=config.whisper.backend,
                whisper_model=config.whisper.model_size,
                frame_interval_seconds=config.frame_interval_seconds,
                style=config.prompt_style,
                target_length_seconds=None,
                use_cache=True,
                rules=effective_rules,
                instructions_text=instructions_text or None,
                instructions_fingerprint=instructions_fingerprint,
                game_profile=config.game_profile,
            )
            self._result_queue.put(_GenerationMessage(kind="done", result=result))
        except OperationCancelledError:
            self._result_queue.put(_GenerationMessage(kind="cancelled"))
        except AutoCutAIError as exc:
            _LOGGER.error("Edit generation failed: %s", exc)
            self._result_queue.put(_GenerationMessage(kind="error", error=str(exc)))
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors too
            _LOGGER.exception("Unexpected error during edit generation")
            self._result_queue.put(_GenerationMessage(kind="error", error=f"Unexpected error: {exc}"))

    def _poll_result(self) -> None:
        drained_terminal = False
        try:
            while True:
                message = self._result_queue.get_nowait()
                if message.kind == "progress" and message.progress is not None:
                    if self._progress_dialog is not None:
                        self._progress_dialog.update_progress(message.progress)
                    continue

                # Terminal message: stop polling after handling it.
                drained_terminal = True
                self._handle_terminal_message(message)
                break
        except queue.Empty:
            pass

        if not drained_terminal:
            self.after(self._POLL_INTERVAL_MS, self._poll_result)

    def _handle_terminal_message(self, message: _GenerationMessage) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None

        if message.kind == "done" and message.result is not None:
            self._on_generation_success(message.result)
        elif message.kind == "cancelled":
            self._show_placeholder("Cancelled. No changes were made -- select a video and try again whenever you're ready.")
        elif message.kind == "error":
            self._show_placeholder(f"Generation failed:\n\n{message.error}")
            messagebox.showerror("AI Editor", message.error or "An unknown error occurred.")

        self._set_running(False)

    def _on_generation_success(self, result: EditGenerationResult) -> None:
        self._current_analysis = result.analysis
        self._current_plan = result.plan
        self._current_result = result
        # Version 4.5, Features 2/3/17: a freshly generated plan starts a
        # brand-new EditPlan Chat + revision history session.
        self._chat_service.start(result.analysis, result.plan)
        self._render_plan(result)
        self._set_action_bar_enabled(True)
        self._render_benchmark(result)

    # ------------------------------------------------------------------
    # Feature 13: Save / Load EditPlan
    # ------------------------------------------------------------------
    def _save_edit_plan_clicked(self) -> None:
        if self._current_plan is None:
            return
        default_path = (
            EditPlanIOService.suggested_path(self._video_path) if self._video_path else Path("edit_plan.editplan.json")
        )
        path_str = filedialog.asksaveasfilename(
            title="Save EditPlan",
            initialfile=default_path.name,
            defaultextension=".editplan.json",
            filetypes=[("AutoCutAI EditPlan", "*.editplan.json"), ("All files", "*.*")],
        )
        if not path_str:
            return
        try:
            self._edit_plan_io.save(Path(path_str), self._current_plan, source_video_path=self._video_path)
            messagebox.showinfo("AI Editor", f"EditPlan saved to:\n{path_str}")
        except EditPlanFileError as exc:
            messagebox.showerror("AI Editor", f"Could not save EditPlan:\n{exc}")

    def _load_edit_plan_clicked(self) -> None:
        if self._is_running:
            return
        path_str = filedialog.askopenfilename(
            title="Load EditPlan",
            filetypes=[("AutoCutAI EditPlan", "*.editplan.json"), ("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path_str:
            return
        try:
            loaded = self._edit_plan_io.load(Path(path_str))
        except EditPlanFileError as exc:
            messagebox.showerror("AI Editor", f"Could not load EditPlan:\n{exc}")
            return

        self._current_plan = loaded.plan
        self._current_analysis = None
        self._current_result = None

        # Loading a plan skips Whisper and the AI request entirely (Feature
        # 13) -- the only work left is a cheap, metadata-only video import
        # (if the original file is still where it was) purely so the
        # viewer can show the source video's name/duration.
        source_name = ""
        if loaded.source_video_path:
            source_path = Path(loaded.source_video_path)
            self._video_path = source_path if source_path.exists() else self._video_path
            source_name = source_path.name
            if source_path.exists():
                try:
                    VideoImporter().import_video(source_path)
                except AutoCutAIError:
                    pass  # display-only; a missing/changed video never blocks viewing the plan

        self._render_loaded_plan(loaded.plan, source_name)
        self._set_action_bar_enabled(True)
        # A loaded plan has no AnalysisResult (no transcript), so the
        # EditPlan Chat -- which needs the transcript for context -- isn't
        # available for it; revision history (undo/redo) still works.
        self._revision_service.start(loaded.plan, source="loaded")
        self._chat_button.configure(state="disabled")
        self._update_undo_redo_buttons()
        self._benchmark_label.configure(text="(No benchmark data -- this EditPlan was loaded from a file, not freshly generated.)")
        LoggingService.log_user_action(_LOGGER, "load_edit_plan", file=Path(path_str).name)

    # ------------------------------------------------------------------
    # Feature 15: Prompt Debug
    # ------------------------------------------------------------------
    def _show_prompt_debug(self) -> None:
        if self._current_result is None or self._current_result.prompt_debug is None:
            messagebox.showinfo(
                "Prompt Debug", "No prompt is available for this EditPlan (it was loaded from a file)."
            )
            return
        PromptDebugWindow(self.winfo_toplevel(), self._current_result.prompt_debug)

    # ------------------------------------------------------------------
    # Version 5: real cut-editing in DaVinci Resolve
    # ------------------------------------------------------------------
    def _open_resolve_export(self) -> None:
        if self._current_plan is None:
            return
        media_duration = self._current_analysis.video.duration_seconds if self._current_analysis else None
        source_name = self._video_path.name if self._video_path else ""
        ResolveExportDialog(
            self.winfo_toplevel(), self._resolve_export_service, self._current_plan, media_duration, source_name,
        )
        LoggingService.log_user_action(_LOGGER, "open_resolve_export", file=source_name)

    # ------------------------------------------------------------------
    # Version 5 (Resolve Free support): XML export, no Resolve API needed
    # ------------------------------------------------------------------
    def _resolve_video_metadata(self):
        """Best-effort :class:`models.video_project.VideoProject` for the
        currently loaded video, for the XML exporter's fps/duration/resolution.

        A freshly generated plan already has this via ``_current_analysis``.
        A plan loaded from a ``.editplan.json`` file (Feature 13) has no
        ``AnalysisResult`` (see ``_load_edit_plan_clicked``), so this falls
        back to a cheap, metadata-only re-import -- exactly what
        ``_load_edit_plan_clicked`` already does for display purposes,
        just not discarded this time. Never raises: a missing/unreadable
        video simply means no metadata is available yet, and the dialog
        itself reports "Could not determine source frame rate."/"Could not
        find the source media file." as appropriate.
        """
        if self._current_analysis is not None:
            return self._current_analysis.video
        if self._video_path and self._video_path.exists():
            try:
                return VideoImporter().import_video(self._video_path)
            except AutoCutAIError:
                return None
        return None

    def _open_resolve_xml_export(self) -> None:
        if self._current_plan is None:
            return
        source_name = self._video_path.name if self._video_path else ""
        ResolveXMLExportDialog(
            self.winfo_toplevel(), self._resolve_export_service, self._current_plan,
            self._video_path, self._resolve_video_metadata(), source_name,
        )
        LoggingService.log_user_action(_LOGGER, "open_resolve_xml_export", file=source_name)

    # ------------------------------------------------------------------
    # Feature 17: Export
    # ------------------------------------------------------------------
    def _export(self, fmt: str) -> None:
        if self._current_plan is None:
            return
        plan = self._current_plan
        source_name = self._video_path.name if self._video_path else ""
        simulation = None
        if self._current_analysis is not None:
            try:
                simulation = self.context.edit_planner.simulate(plan, self._current_analysis)
            except Exception:  # noqa: BLE001 - export must never crash on a diagnostic failure
                simulation = None

        if fmt == "json":
            content, default_ext, filetypes = ExportService.to_json(plan), ".json", [("JSON", "*.json")]
        elif fmt == "txt":
            content, default_ext, filetypes = ExportService.to_txt(plan, source_name, simulation), ".txt", [("Text", "*.txt")]
        else:
            content, default_ext, filetypes = ExportService.to_markdown(plan, source_name, simulation), ".md", [("Markdown", "*.md")]

        path_str = filedialog.asksaveasfilename(
            title=f"Export EditPlan as {fmt.upper()}", defaultextension=default_ext, filetypes=filetypes + [("All files", "*.*")],
        )
        if not path_str:
            return
        try:
            Path(path_str).write_text(content, encoding="utf-8")
            messagebox.showinfo("AI Editor", f"Exported to:\n{path_str}")
        except OSError as exc:
            messagebox.showerror("AI Editor", f"Could not export: {exc}")

    # ------------------------------------------------------------------
    # Feature 16: Benchmark
    # ------------------------------------------------------------------
    def _render_benchmark(self, result: EditGenerationResult) -> None:
        if result.used_cache:
            self._benchmark_label.configure(text="⚡ Loaded from cache -- Whisper and the AI request were both skipped.")
            return
        self._benchmark_label.configure(text=result.benchmark.to_text_report())

    # ------------------------------------------------------------------
    # Feature 8: Human-readable EditPlan viewer
    # ------------------------------------------------------------------
    def _render_plan(self, result: EditGenerationResult) -> None:
        analysis, plan = result.analysis, result.plan
        source_note = (
            f"{analysis.video.file_name} ({analysis.video.duration_seconds:.1f}s, language={analysis.transcript.language})"
        )
        cache_note = " · loaded from cache" if result.used_cache else ""
        self._render_plan_common(
            plan,
            source_note + cache_note,
            f"~{result.prompt_estimated_tokens} estimated prompt tokens ({result.prompt_reduction_percent:.1f}% smaller than baseline)"
            if not result.used_cache else None,
        )

    def _render_loaded_plan(self, plan: EditPlan, source_name: str) -> None:
        note = f"Loaded from file{f' (source video: {source_name})' if source_name else ''}"
        self._render_plan_common(plan, note, None)

    def _render_plan_common(self, plan: EditPlan, source_note: str, prompt_note: Optional[str]) -> None:
        for child in self._result_frame.winfo_children():
            child.destroy()

        summary = ctk.CTkFrame(self._result_frame)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        summary.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(summary, text=source_note, anchor="w", text_color="gray60").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(summary, text=f"Model: {plan.model_used}", anchor="w", text_color="gray60").grid(row=1, column=0, sticky="w", padx=12)
        if prompt_note:
            ctk.CTkLabel(summary, text=prompt_note, anchor="w", text_color="gray60").grid(row=2, column=0, sticky="w", padx=12)

        stats_row = ctk.CTkFrame(summary, fg_color="transparent")
        stats_row.grid(row=3, column=0, sticky="ew", padx=12, pady=8)
        ctk.CTkLabel(
            stats_row, text=f"Target length: {plan.target_length_seconds:.1f}s", font=ctk.CTkFont(weight="bold")
        ).pack(side="left", padx=(0, 20))
        ctk.CTkLabel(
            stats_row, text=f"Confidence: {plan.confidence * 100:.0f}%", font=ctk.CTkFont(weight="bold")
        ).pack(side="left", padx=(0, 20))
        ctk.CTkLabel(
            stats_row, text=f"Keep: {len(plan.keep_segments)}  ·  Compress: {len(plan.compress_segments)}  ·  Remove: {len(plan.remove_segments)}"
        ).pack(side="left")

        if plan.reasons:
            reasons_text = "Summary: " + " ".join(plan.reasons)
            ctk.CTkLabel(summary, text=reasons_text, anchor="w", justify="left", wraplength=760).grid(
                row=4, column=0, sticky="w", padx=12, pady=(0, 10)
            )

        if plan.mood_recommendation:
            mood = plan.mood_recommendation
            mood_frame = ctk.CTkFrame(summary, fg_color="#1f2d3a")
            mood_frame.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 10))
            ctk.CTkLabel(
                mood_frame, text=f"🎵 Suggested mood: {mood.overall_mood}",
                font=ctk.CTkFont(weight="bold"), text_color="#8fc8f0", anchor="w",
            ).pack(anchor="w", padx=12, pady=(8, 0))
            detail_bits = []
            if mood.tempo_description:
                detail_bits.append(f"Tempo: {mood.tempo_description}")
            if mood.music_genre_suggestions:
                detail_bits.append(f"BGM: {', '.join(mood.music_genre_suggestions)}")
            if detail_bits:
                ctk.CTkLabel(
                    mood_frame, text="  ·  ".join(detail_bits), text_color="#8fc8f0", anchor="w",
                ).pack(anchor="w", padx=12, pady=(2, 0))
            if mood.reasoning:
                ctk.CTkLabel(
                    mood_frame, text=mood.reasoning, text_color="gray70", anchor="w", justify="left", wraplength=740,
                ).pack(anchor="w", padx=12, pady=(2, 8))
            else:
                ctk.CTkLabel(mood_frame, text="").pack(pady=(0, 4))

        if plan.warnings:
            warnings_frame = ctk.CTkFrame(self._result_frame, fg_color="#4a3a1a")
            warnings_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
            ctk.CTkLabel(warnings_frame, text=f"⚠ Warnings ({len(plan.warnings)})", font=ctk.CTkFont(weight="bold"), text_color="#f0c674").pack(anchor="w", padx=12, pady=(8, 0))
            for warning in plan.warnings:
                ctk.CTkLabel(warnings_frame, text=f"  - {warning}", text_color="#f0c674", anchor="w", justify="left", wraplength=760).pack(anchor="w", padx=12, pady=(0, 4))

        timeline_row = 2
        for index, segment in enumerate(plan.all_segments_sorted):
            if segment.action == EDIT_ACTION_COMPRESS:
                row_color, badge_color = "#3a331f", "#e3b341"
                badge = f"⏩ COMPRESS {segment.compression_speed_factor:.1f}x" if segment.compression_speed_factor else "⏩ COMPRESS"
            elif segment.is_keep:
                row_color, badge_color, badge = "#1f3a24", "#3fb950", "✅ KEEP"
            else:
                row_color, badge_color, badge = "#3a1f1f", "#f85149", "✂️ REMOVE"

            row_frame = ctk.CTkFrame(self._result_frame, fg_color=row_color)
            row_frame.grid(row=timeline_row + index, column=0, sticky="ew", pady=3)
            row_frame.grid_columnconfigure(2, weight=1)

            ctk.CTkLabel(
                row_frame, text=self._format_range(segment.start_seconds, segment.end_seconds),
                font=ctk.CTkFont(family="Courier", size=12), width=140,
            ).grid(row=0, column=0, padx=(12, 8), pady=8, sticky="w")
            ctk.CTkLabel(row_frame, text=badge, text_color=badge_color, font=ctk.CTkFont(weight="bold"), width=130).grid(
                row=0, column=1, padx=(0, 8), pady=8, sticky="w"
            )
            reason_text = segment.reason
            if segment.confidence is not None:
                reason_text += f"  ({segment.confidence * 100:.0f}% confident)"
            ctk.CTkLabel(row_frame, text=reason_text, anchor="w", justify="left", wraplength=480).grid(
                row=0, column=2, padx=(0, 12), pady=8, sticky="w"
            )
            if segment.factors:
                ctk.CTkLabel(
                    row_frame, text="Factors: " + ", ".join(segment.factors), anchor="w", justify="left",
                    text_color="gray60", wraplength=480, font=ctk.CTkFont(size=10),
                ).grid(row=1, column=2, padx=(0, 12), pady=(0, 6), sticky="w")

    @staticmethod
    def _format_range(start: float, end: float) -> str:
        def fmt(seconds: float) -> str:
            minutes, secs = divmod(max(0.0, seconds), 60)
            return f"{int(minutes):02d}:{secs:05.2f}"

        return f"{fmt(start)} -> {fmt(end)}"

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def _show_placeholder(self, text: str) -> None:
        for child in self._result_frame.winfo_children():
            child.destroy()
        ctk.CTkLabel(self._result_frame, text=text, text_color="gray60", anchor="w", justify="left", wraplength=760).grid(
            row=0, column=0, sticky="w", padx=12, pady=12
        )
        self._benchmark_label.configure(text="")

    def _set_action_bar_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (
            self._save_button, self._prompt_debug_button, self._export_json_button,
            self._export_txt_button, self._export_md_button, self._chat_button, self._resolve_button,
            self._resolve_xml_button,
        ):
            button.configure(state=state)
        self._update_undo_redo_buttons()

    def _update_undo_redo_buttons(self) -> None:
        self._undo_button.configure(state="normal" if self._revision_service.can_undo() else "disabled")
        self._redo_button.configure(state="normal" if self._revision_service.can_redo() else "disabled")

    def _set_running(self, running: bool) -> None:
        self._is_running = running
        self._select_button.configure(state="disabled" if running else "normal")
        self._folder_button.configure(state="disabled" if running else "normal")
        self._load_button.configure(state="disabled" if running else "normal")
        self._run_button.configure(state="disabled" if (running or self._video_path is None) else "normal")
        if running:
            self._status_label.configure(text="Working...")
        else:
            self._update_idle_status()

    def _update_idle_status(self) -> None:
        provider = self.context.edit_planner.provider_name
        model = self.context.edit_planner.model
        self._status_label.configure(text=f"{provider} · {model}")
