"""Home / dashboard page: high level status of the application."""

from __future__ import annotations

import customtkinter as ctk

from ui.pages.base_page import BasePage
from utils.constants import APP_NAME, APP_VERSION


class HomePage(BasePage):
    """Landing page showing a quick status overview."""

    def build(self) -> None:
        self.grid_columnconfigure((0, 1, 2), weight=1)

        header = ctk.CTkLabel(
            self,
            text=f"{APP_NAME}",
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        header.grid(row=0, column=0, columnspan=3, padx=30, pady=(30, 4), sticky="w")

        subtitle = ctk.CTkLabel(
            self,
            text="AI-powered automatic video editor for DaVinci Resolve — multi-provider, GPU-safe, diagnostics-ready",
            font=ctk.CTkFont(size=14),
            text_color="gray60",
        )
        subtitle.grid(row=1, column=0, columnspan=3, padx=30, pady=(0, 24), sticky="w")

        self._provider_card = self._build_stat_card(row=2, column=0, title="AI Provider")
        self._keys_card = self._build_stat_card(row=2, column=1, title="Configured Keys")
        self._model_card = self._build_stat_card(row=2, column=2, title="Selected Model")

        roadmap_frame = ctk.CTkFrame(self)
        roadmap_frame.grid(row=3, column=0, columnspan=3, padx=30, pady=(24, 30), sticky="nsew")
        self.grid_rowconfigure(3, weight=1)

        roadmap_title = ctk.CTkLabel(
            roadmap_frame, text="Roadmap", font=ctk.CTkFont(size=16, weight="bold")
        )
        roadmap_title.pack(anchor="w", padx=20, pady=(16, 8))

        for text in (
            "v1 — Foundation: multi-key provider(s), config, logging, GUI shell",
            "v2 — Analysis pipeline: video import, audio extraction, Whisper transcription, "
            "frame sampling (backend ready)",
            "v3 — AI editing brain: transcript-driven EditPlan generation, connected to "
            "the app end to end (AppContext.edit_planner + the AI Editor page)",
            "v3.5 — EditPlan validation & auto-repair, edit simulation, a configurable "
            "editing-rules engine, scoring, and prompt styles",
            "Stability update — multi-provider architecture (Gemini/OpenRouter/OpenAI/"
            "Claude/local, all free-text model+URL), a Whisper GPU runtime manager that "
            "never crashes on missing CUDA, a Runtime Diagnostics page, and outdated-model "
            "migration (this build)",
            "v4 — Actually performing the cut from a validated EditPlan",
            "v5 — Fully automated rough-cut generation + DaVinci Resolve scripting bridge",
        ):
            item = ctk.CTkLabel(roadmap_frame, text=f"•  {text}", anchor="w", justify="left")
            item.pack(anchor="w", padx=20, pady=2, fill="x")

        version_label = ctk.CTkLabel(
            roadmap_frame, text=f"Version {APP_VERSION}", text_color="gray60"
        )
        version_label.pack(anchor="w", padx=20, pady=(12, 16))

    def _build_stat_card(self, row: int, column: int, title: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(self)
        card.grid(row=row, column=column, padx=(30 if column == 0 else 10, 10), pady=10, sticky="nsew")

        title_label = ctk.CTkLabel(card, text=title, text_color="gray60", font=ctk.CTkFont(size=12))
        title_label.pack(anchor="w", padx=16, pady=(14, 0))

        value_label = ctk.CTkLabel(card, text="—", font=ctk.CTkFont(size=20, weight="bold"))
        value_label.pack(anchor="w", padx=16, pady=(0, 14))
        return value_label

    def on_show(self) -> None:
        key_manager = self.context.key_manager
        chat_service = self.context.chat_service

        self._provider_card.configure(text=chat_service.provider_name)
        self._model_card.configure(text=chat_service.model)

        total_keys = len(key_manager.keys)
        usable_keys = key_manager.usable_key_count()
        self._keys_card.configure(text=f"{usable_keys} / {total_keys} usable")
