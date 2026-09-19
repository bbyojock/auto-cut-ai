"""The AI Progress Window: a modal dialog showing pipeline/generation progress.

Used by :class:`ui.pages.edit_plan_page.EditPlanPage` (Version 4, Feature 2)
to display every stage from "Loading Video" through "Completed", a
progress bar, elapsed/estimated-remaining time, and a Cancel button
(Feature 3). This widget only ever renders what it's told via
:meth:`ProgressDialog.update_progress` -- all state (which stage is
active, elapsed time, cancellation) is owned by the calling page, keeping
this a "dumb" view exactly like every other page/widget in the codebase.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

import customtkinter as ctk

from models.generation_progress import STAGE_LABELS, STAGE_ORDER, GenerationStage, ProgressEvent

_PENDING_ICON = "○"
_ACTIVE_ICON = "●"
_DONE_ICON = "✓"
_ERROR_ICON = "✕"


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


class ProgressDialog(ctk.CTkToplevel):
    """Modal progress window covering the full analysis + AI generation run."""

    def __init__(self, master, on_cancel: Callable[[], None], title: str = "Generating Edit Plan") -> None:
        super().__init__(master)
        self.title(title)
        self.geometry("500x460")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel_clicked)

        self._on_cancel = on_cancel
        self._cancel_requested = False
        self._stage_rows: Dict[GenerationStage, ctk.CTkLabel] = {}

        self._build()

        # Modal: grabs input focus so the user can't interact with the rest
        # of the app while generation is running (also keeps Tk's mainloop
        # updates flowing to this window specifically via .update()).
        self.transient(master)
        self.after(50, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 - grab can fail transiently on some window managers
            pass

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(self, text="Generating Edit Plan", font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")

        self._detail_label = ctk.CTkLabel(
            self, text="Starting...", text_color="gray60", anchor="w", wraplength=440, justify="left"
        )
        self._detail_label.grid(row=1, column=0, padx=20, pady=(0, 12), sticky="ew")

        self._progress_bar = ctk.CTkProgressBar(self, mode="determinate")
        self._progress_bar.set(0.0)
        self._progress_bar.grid(row=2, column=0, padx=20, pady=(0, 4), sticky="ew")

        time_frame = ctk.CTkFrame(self, fg_color="transparent")
        time_frame.grid(row=3, column=0, padx=20, pady=(0, 16), sticky="ew")
        time_frame.grid_columnconfigure((0, 1), weight=1)
        self._elapsed_label = ctk.CTkLabel(time_frame, text="Elapsed: 00:00", anchor="w")
        self._elapsed_label.grid(row=0, column=0, sticky="w")
        self._eta_label = ctk.CTkLabel(time_frame, text="Remaining: estimating...", anchor="e")
        self._eta_label.grid(row=0, column=1, sticky="e")

        stage_frame = ctk.CTkFrame(self)
        stage_frame.grid(row=4, column=0, padx=20, pady=(0, 16), sticky="nsew")
        self.grid_rowconfigure(4, weight=1)

        for stage in STAGE_ORDER:
            row = ctk.CTkLabel(
                stage_frame, text=f"{_PENDING_ICON}  {STAGE_LABELS[stage]}", anchor="w", justify="left"
            )
            row.pack(anchor="w", padx=16, pady=4, fill="x")
            self._stage_rows[stage] = row

        self._cancel_button = ctk.CTkButton(
            self, text="Cancel", fg_color="firebrick3", hover_color="firebrick4", command=self._on_cancel_clicked
        )
        self._cancel_button.grid(row=5, column=0, padx=20, pady=(0, 20), sticky="ew")

    def _on_cancel_clicked(self) -> None:
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self._cancel_button.configure(state="disabled", text="Cancelling...")
        self._detail_label.configure(text="Cancelling... waiting for the current step to stop safely.")
        self._on_cancel()

    def update_progress(self, event: ProgressEvent) -> None:
        """Reflect one :class:`ProgressEvent` in the window. Never raises."""
        if event.stage in (GenerationStage.CANCELLED, GenerationStage.ERROR):
            self._mark_terminal(event)
            return

        index = event.stage_index
        total = len(STAGE_ORDER)
        for i, stage in enumerate(STAGE_ORDER):
            row = self._stage_rows[stage]
            label_text = STAGE_LABELS[stage]
            if i < index:
                row.configure(text=f"{_DONE_ICON}  {label_text}", text_color="#3fb950")
            elif i == index:
                row.configure(text=f"{_ACTIVE_ICON}  {label_text}", text_color=("gray10", "gray90"))
            else:
                row.configure(text=f"{_PENDING_ICON}  {label_text}", text_color="gray60")

        if index >= 0:
            fraction_within_stage = event.fraction if event.fraction is not None else 0.5
            self._progress_bar.set(min(1.0, (index + fraction_within_stage) / total))

        if event.message:
            self._detail_label.configure(text=event.message)
        self._elapsed_label.configure(text=f"Elapsed: {_format_duration(event.elapsed_seconds)}")
        if event.eta_seconds is not None:
            self._eta_label.configure(text=f"Remaining: ~{_format_duration(event.eta_seconds)}")
        else:
            self._eta_label.configure(text="Remaining: estimating...")

        if event.stage == GenerationStage.COMPLETED:
            self._progress_bar.set(1.0)
            self._cancel_button.configure(state="disabled", text="Done")

    def _mark_terminal(self, event: ProgressEvent) -> None:
        if event.stage == GenerationStage.CANCELLED:
            self._detail_label.configure(text=event.message or "Cancelled.", text_color="orange")
            self._cancel_button.configure(state="disabled", text="Cancelled")
        else:
            self._detail_label.configure(text=f"{_ERROR_ICON} {event.message or 'An error occurred.'}", text_color="firebrick3")
            self._cancel_button.configure(state="normal", text="Close", fg_color="gray30", hover_color="gray20")
            self._cancel_button.configure(command=self.close)

    def close(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
