"""Resolve Export dialog (Version 5, Section 10: Dry Run / Preview).

Shows exactly what an EditPlan would cut before anything touches Resolve,
then lets the user actually apply it to a new, duplicated timeline. Follows
the same read-only-preview + explicit-action-button shape as
:class:`ui.widgets.prompt_debug_window.PromptDebugWindow`.
"""

from __future__ import annotations

from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from core.exceptions import ResolveError
from models.edit_plan import EditPlan
from services.resolve_export_service import ResolveApplyReport, ResolveExportService


class ResolveExportDialog(ctk.CTkToplevel):
    """Read-only Dry Run preview with an explicit "Apply to Resolve" action."""

    def __init__(
        self,
        master,
        service: ResolveExportService,
        plan: EditPlan,
        media_duration_seconds: Optional[float],
        source_video_name: str = "",
    ) -> None:
        super().__init__(master)
        self.title("Apply EditPlan to DaVinci Resolve")
        self.geometry("640x560")
        self._service = service
        self._plan = plan
        self._media_duration_seconds = media_duration_seconds
        self._source_video_name = source_video_name
        self._build()
        self.transient(master)

    def _build(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(self, text="AutoCutAI Edit Preview", font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")

        subheader_text = "Nothing has been applied to Resolve yet -- this is a preview only."
        if self._source_video_name:
            subheader_text = f"{self._source_video_name}\n{subheader_text}"
        subheader = ctk.CTkLabel(self, text=subheader_text, text_color="gray60", anchor="w", justify="left")
        subheader.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="w")

        self._preview_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Courier", size=12))
        self._preview_box.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="ew")
        button_frame.grid_columnconfigure((0, 1), weight=1)

        self._apply_button = ctk.CTkButton(
            button_frame, text="Apply to Resolve...", fg_color="#2f6f4f", hover_color="#255a3f",
            command=self._apply_clicked,
        )
        self._apply_button.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        close_button = ctk.CTkButton(button_frame, text="Close", fg_color="gray30", hover_color="gray20", command=self.destroy)
        close_button.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self._render_preview()

    def _render_preview(self) -> None:
        report = self._service.preview(self._plan, self._media_duration_seconds)
        self._show_report(report, applied=False)

    def _show_report(self, report: ResolveApplyReport, applied: bool) -> None:
        lines = []
        if applied:
            lines.append(f"Applied to timeline: {report.edit_timeline_name}")
            lines.append(f"Cuts applied: {report.ranges_applied}/{report.ranges_requested}  "
                          f"({report.clips_deleted} clip(s) deleted)")
            lines.append("")
        lines.extend(report.preview_lines)
        if report.warnings:
            lines.append("")
            lines.append(f"Warnings ({len(report.warnings)}):")
            lines.extend(f"  - {warning}" for warning in report.warnings)

        self._preview_box.configure(state="normal")
        self._preview_box.delete("1.0", "end")
        self._preview_box.insert("1.0", "\n".join(lines))
        self._preview_box.configure(state="disabled")

    def _apply_clicked(self) -> None:
        confirmed = messagebox.askokcancel(
            "Apply to Resolve",
            "This will connect to the currently running DaVinci Resolve, duplicate the current "
            "timeline, and cut the REMOVE segments out of the copy. The original timeline will "
            "not be modified.\n\nContinue?",
        )
        if not confirmed:
            return

        self._apply_button.configure(state="disabled", text="Applying...")
        self.update_idletasks()
        try:
            report = self._service.apply(self._plan, self._media_duration_seconds)
        except ResolveError as exc:
            self._apply_button.configure(state="normal", text="Apply to Resolve...")
            messagebox.showerror("Apply to Resolve", str(exc))
            return

        self._apply_button.configure(state="disabled", text="Applied")
        self._show_report(report, applied=True)
        messagebox.showinfo(
            "Apply to Resolve",
            f"Done. Created timeline '{report.edit_timeline_name}' with "
            f"{report.ranges_applied} REMOVE segment(s) cut ({report.clips_deleted} clip(s) deleted). "
            "The original timeline was not modified.",
        )
