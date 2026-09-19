"""Prompt Debug window: shows the exact prompt sent to the AI (Version 4, Feature 15)."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from ai.prompt_builder import PromptBuildResult
from services.token_usage_service import TokenUsageService


class PromptDebugWindow(ctk.CTkToplevel):
    """Read-only viewer for the system/user prompt actually sent to the provider."""

    def __init__(self, master, prompt_result: PromptBuildResult) -> None:
        super().__init__(master)
        self.title("Prompt Debug")
        self.geometry("720x600")
        self._prompt_result = prompt_result
        self._build()
        self.transient(master)

    def _build(self) -> None:
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(self, text="Prompt Debug", font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")

        reduction = self._prompt_result.reduction
        stats_text = (
            f"Characters: {reduction.optimized_chars} (baseline {reduction.original_chars})   |   "
            f"Estimated tokens: {reduction.optimized_tokens} (baseline {reduction.original_tokens})   |   "
            f"Reduction: {reduction.reduction_percent:.1f}%"
        )
        stats_label = ctk.CTkLabel(self, text=stats_text, text_color="gray60", anchor="w")
        stats_label.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="w")

        tabs = ctk.CTkTabview(self)
        tabs.grid(row=2, column=0, rowspan=2, padx=20, pady=(0, 12), sticky="nsew")
        tabs.add("Full Prompt")
        tabs.add("System")
        tabs.add("User / Context")

        self._full_box = self._make_textbox(tabs.tab("Full Prompt"), self._prompt_result.full_prompt)
        self._system_box = self._make_textbox(tabs.tab("System"), self._prompt_result.system_prompt)
        self._user_box = self._make_textbox(tabs.tab("User / Context"), self._prompt_result.user_prompt)

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="ew")
        button_frame.grid_columnconfigure((0, 1, 2), weight=1)

        copy_button = ctk.CTkButton(button_frame, text="Copy Full Prompt", command=self._copy_full_prompt)
        copy_button.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        save_button = ctk.CTkButton(button_frame, text="Save to File...", command=self._save_to_file)
        save_button.grid(row=0, column=1, padx=6, sticky="ew")

        close_button = ctk.CTkButton(button_frame, text="Close", fg_color="gray30", hover_color="gray20", command=self.destroy)
        close_button.grid(row=0, column=2, padx=(6, 0), sticky="ew")

    def _make_textbox(self, parent, text: str) -> ctk.CTkTextbox:
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        box = ctk.CTkTextbox(parent, wrap="word", font=ctk.CTkFont(family="Courier", size=12))
        box.grid(row=0, column=0, sticky="nsew")
        box.insert("1.0", text)
        box.configure(state="disabled")
        return box

    def _copy_full_prompt(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._prompt_result.full_prompt)

    def _save_to_file(self) -> None:
        path_str = filedialog.asksaveasfilename(
            parent=self, title="Save Prompt", defaultextension=".txt", filetypes=[("Text file", "*.txt")]
        )
        if not path_str:
            return
        Path(path_str).write_text(self._prompt_result.full_prompt, encoding="utf-8")
