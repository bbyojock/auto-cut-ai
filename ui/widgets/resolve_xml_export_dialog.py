"""Resolve Free XML Export dialog (Version 5, "Resolve Free support").

Same read-only-preview + explicit-action-button shape as
:class:`ui.widgets.resolve_export_dialog.ResolveExportDialog` (its Resolve
Studio counterpart) -- Section 19 of the spec explicitly asks for the Dry
Run preview to be reused rather than re-implemented, so this dialog renders
:class:`services.resolve_export_service.ResolveXMLExportReport`'s
``preview_lines`` the same way.

Unlike ``ResolveExportDialog``, nothing here ever contacts DaVinci Resolve
itself: "Export XML" only asks where to save a file, then writes it. Safe
to open even if Resolve isn't installed at all (Section 17).
"""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from core.exceptions import ResolveError
from models.edit_plan import EditPlan
from models.video_project import VideoProject
from services.resolve_export_service import ResolveExportService, ResolveXMLExportReport


class ResolveXMLExportDialog(ctk.CTkToplevel):
    """Preview what would be exported, then let the user save a Resolve-
    importable XML file next to (or wherever they choose relative to)
    the source video."""

    def __init__(
        self,
        master,
        service: ResolveExportService,
        plan: EditPlan,
        source_video_path: Optional[Path],
        video_meta: Optional[VideoProject],
        source_video_name: str = "",
    ) -> None:
        super().__init__(master)
        self.title("Export Resolve XML")
        self.geometry("640x560")
        self._service = service
        self._plan = plan
        self._source_video_path = source_video_path
        self._video_meta = video_meta
        self._source_video_name = source_video_name
        self._build()
        self.transient(master)

    def _build(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(self, text="Resolve XML Export Preview", font=ctk.CTkFont(size=18, weight="bold"))
        header.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")

        subheader_text = "Creates a Final Cut Pro XML file you import into DaVinci Resolve (Free or Studio)."
        if self._source_video_name:
            subheader_text = f"{self._source_video_name}\n{subheader_text}"
        subheader = ctk.CTkLabel(self, text=subheader_text, text_color="gray60", anchor="w", justify="left")
        subheader.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="w")

        self._preview_box = ctk.CTkTextbox(self, wrap="word", font=ctk.CTkFont(family="Courier", size=12))
        self._preview_box.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="nsew")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=3, column=0, padx=20, pady=(0, 20), sticky="ew")
        button_frame.grid_columnconfigure((0, 1), weight=1)

        self._export_button = ctk.CTkButton(
            button_frame, text="Export XML", fg_color="#3a6ea5", hover_color="#2f5a86",
            command=self._export_clicked,
        )
        self._export_button.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        close_button = ctk.CTkButton(button_frame, text="Cancel", fg_color="gray30", hover_color="gray20", command=self.destroy)
        close_button.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self._render_preview()

    # ------------------------------------------------------------------
    def _fps(self) -> Optional[float]:
        return self._video_meta.fps if self._video_meta else None

    def _duration(self) -> Optional[float]:
        return self._video_meta.duration_seconds if self._video_meta else None

    def _width(self) -> Optional[int]:
        return self._video_meta.width if self._video_meta else None

    def _height(self) -> Optional[int]:
        return self._video_meta.height if self._video_meta else None

    def _source_start_timecode_seconds(self) -> float:
        """The source file's own probed EMBEDDED timecode (Section: see
        ``resolve_xml_exporter.build_edit_xml``'s docstring). Without this,
        a source file whose embedded timecode track doesn't start at
        ``00:00:00:00`` (very common on screen recordings/capture-card
        footage -- ``01:00:00:00`` is a widespread convention, and is a
        *different* value from the stream start_time used elsewhere for
        A/V sync) produces an XML Resolve rejects on import/relink with a
        ``"No overlap"`` error, even though the media path itself is
        correct."""
        if self._video_meta is None:
            return 0.0
        return self._video_meta.source_timecode_seconds or 0.0

    def _render_preview(self) -> None:
        lines = []
        if self._source_video_name:
            lines.append(f"Source:  {self._source_video_name}")
        lines.append(f"FPS:     {self._fps():.3f}" if self._fps() else "FPS:     (unknown)")
        if self._duration():
            lines.append(f"Duration: {self._duration():.2f} sec")
        start_tc_seconds = self._source_start_timecode_seconds()
        if start_tc_seconds:
            lines.append(f"Source start timecode: {start_tc_seconds:.3f} sec (non-zero; will be embedded in the XML)")
        lines.append("")

        try:
            preview = self._service.preview(self._plan, self._duration())
        except Exception as exc:  # noqa: BLE001 - preview must never crash the dialog
            lines.append(f"Could not build preview: {exc}")
            self._set_preview_text(lines)
            return

        lines.extend(preview.preview_lines)

        if self._plan.compress_segments:
            lines.append("")
            lines.append("Note: COMPRESS segments are preserved at normal speed in this version.")

        self._set_preview_text(lines)

    def _set_preview_text(self, lines) -> None:
        self._preview_box.configure(state="normal")
        self._preview_box.delete("1.0", "end")
        self._preview_box.insert("1.0", "\n".join(lines))
        self._preview_box.configure(state="disabled")

    # ------------------------------------------------------------------
    def _export_clicked(self) -> None:
        if self._source_video_path is None or not Path(self._source_video_path).exists():
            messagebox.showerror(
                "Export Resolve XML",
                "Could not find the source media file.\nPlease verify that the original video still exists.",
            )
            return
        if not self._fps():
            messagebox.showerror("Export Resolve XML", "Could not determine source frame rate.")
            return

        default_path = ResolveExportService.suggested_xml_path(self._source_video_path)
        path_str = filedialog.asksaveasfilename(
            title="Export Resolve XML",
            initialfile=default_path.name,
            initialdir=str(default_path.parent),
            defaultextension=".xml",
            filetypes=[("Final Cut Pro XML", "*.xml"), ("All files", "*.*")],
        )
        if not path_str:
            return

        self._export_button.configure(state="disabled", text="Exporting...")
        self.update_idletasks()
        try:
            report = self._service.export_xml(
                self._plan,
                source_video_path=self._source_video_path,
                fps=self._fps(),
                output_path=Path(path_str),
                media_duration_seconds=self._duration(),
                width=self._width(),
                height=self._height(),
                source_start_timecode_seconds=self._source_start_timecode_seconds(),
            )
        except ResolveError as exc:
            self._export_button.configure(state="normal", text="Export XML")
            messagebox.showerror("Export Resolve XML", str(exc))
            return
        except OSError as exc:
            self._export_button.configure(state="normal", text="Export XML")
            messagebox.showerror("Export Resolve XML", f"Failed to export Resolve XML.\n\nDetails:\n{exc}")
            return

        self._export_button.configure(state="normal", text="Export XML")
        self._show_success(report)

    def _show_success(self, report: ResolveXMLExportReport) -> None:
        lines = [
            f"Applied to timeline: {report.clip_count} clip(s) from KEEP segments.",
            "",
        ]
        lines.extend(report.preview_lines)
        if report.warnings:
            lines.append("")
            lines.append(f"Warnings ({len(report.warnings)}):")
            lines.extend(f"  - {warning}" for warning in report.warnings)
        self._set_preview_text(lines)

        messagebox.showinfo(
            "Export Resolve XML",
            "Resolve XML exported successfully.\n\n"
            f"File:\n{report.output_path}\n\n"
            "Open DaVinci Resolve and import this XML to create the edited timeline.",
        )


__all__ = ["ResolveXMLExportDialog"]
