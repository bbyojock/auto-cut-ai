"""Runtime Diagnostics page: a traffic-light health report you can copy.

Never shows a raw Python traceback -- every failure mode is caught and
turned into a plain-language row instead.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

import customtkinter as ctk

from models.connection_test_result import ConnectionTestResult
from models.runtime_diagnostics import DiagnosticStatus, RuntimeDiagnosticsReport
from services.diagnostics_service import RuntimeDiagnosticsService
from services.logging_service import LoggingService
from ui.pages.base_page import BasePage

_LOGGER = LoggingService.get_logger("ui.diagnostics_page")

_STATUS_COLORS = {
    DiagnosticStatus.OK: "#3ddc84",
    DiagnosticStatus.WARNING: "#f5d36b",
    DiagnosticStatus.ERROR: "#ff6b6b",
}
_STATUS_DOTS = {
    DiagnosticStatus.OK: "\U0001F7E2",
    DiagnosticStatus.WARNING: "\U0001F7E1",
    DiagnosticStatus.ERROR: "\U0001F534",
}


class DiagnosticsPage(BasePage):
    """Runs Python/FFmpeg/Whisper/GPU/provider/config checks on demand."""

    _POLL_INTERVAL_MS = 150

    def build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._service = RuntimeDiagnosticsService()
        self._report: Optional[RuntimeDiagnosticsReport] = None
        self._connection_result: Optional[ConnectionTestResult] = None
        self._result_queue: "queue.Queue[tuple]" = queue.Queue()
        self._connection_queue: "queue.Queue[ConnectionTestResult]" = queue.Queue()
        self._is_running = False
        self._is_testing_connection = False

        header = ctk.CTkLabel(self, text="Runtime Diagnostics", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, padx=30, pady=(30, 10), sticky="w")

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.grid(row=1, column=0, padx=30, pady=(0, 10), sticky="ew")

        self._run_button = ctk.CTkButton(controls, text="Run Diagnostics", command=self._run_clicked)
        self._run_button.pack(side="left")
        self._test_connection_button = ctk.CTkButton(
            controls, text="Test Provider Connection", fg_color="gray30", hover_color="gray20",
            command=self._test_connection_clicked,
        )
        self._test_connection_button.pack(side="left", padx=(10, 0))
        self._copy_button = ctk.CTkButton(controls, text="Copy Report", command=self._copy_report, state="disabled")
        self._copy_button.pack(side="left", padx=(10, 0))
        self._status_label = ctk.CTkLabel(controls, text="", text_color="gray60")
        self._status_label.pack(side="left", padx=12)

        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.grid(row=2, column=0, padx=30, pady=(0, 30), sticky="nsew")
        self._body.grid_columnconfigure(0, weight=1)

    def on_show(self) -> None:
        if self._report is None:
            self._run_clicked()

    def _run_clicked(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._run_button.configure(state="disabled")
        self._status_label.configure(text="Running checks...", text_color="gray60")
        threading.Thread(target=self._run_diagnostics, daemon=True).start()
        self.after(self._POLL_INTERVAL_MS, self._poll_result)

    def _run_diagnostics(self) -> None:
        """Runs entirely on a background thread -- must never touch Tk directly.

        The result (or error) is handed back through a thread-safe queue;
        only :meth:`_poll_result`, running on the main/Tk thread via
        ``after()``, is allowed to update any widget.
        """
        config = self.context.config_manager.config
        try:
            report = self._service.run(config, connection_result=self._connection_result)
            self._result_queue.put(("done", report, None))
        except Exception as exc:  # noqa: BLE001 - never let a raw traceback reach the UI
            _LOGGER.exception("Diagnostics failed")
            self._result_queue.put(("error", None, str(exc)))

    def _test_connection_clicked(self) -> None:
        """Feature 11: send one live request to the active provider and show its latency.

        Runs on a background thread (Feature 18: never freeze the GUI for
        a network call) via :meth:`ai.base_provider.AIProvider.test_connection`,
        which is already implemented once for every provider.
        """
        if self._is_testing_connection:
            return
        self._is_testing_connection = True
        self._test_connection_button.configure(state="disabled", text="Testing...")

        provider = self.context.edit_planner.provider
        model = self.context.edit_planner.model

        def worker() -> None:
            try:
                result = provider.test_connection(model)
            except Exception as exc:  # noqa: BLE001 - never let a raw traceback reach the UI
                from models.connection_test_result import ConnectionTestResult

                result = ConnectionTestResult(success=False, message=f"Unexpected error: {exc}", latency_seconds=0.0)
            self._connection_queue.put(result)

        threading.Thread(target=worker, daemon=True).start()
        self.after(self._POLL_INTERVAL_MS, self._poll_connection_result)

    def _poll_connection_result(self) -> None:
        try:
            result = self._connection_queue.get_nowait()
        except queue.Empty:
            self.after(self._POLL_INTERVAL_MS, self._poll_connection_result)
            return

        self._connection_result = result
        self._is_testing_connection = False
        self._test_connection_button.configure(state="normal", text="Test Provider Connection")
        LoggingService.log_user_action(_LOGGER, "test_provider_connection", success=result.success)
        self._run_clicked()  # re-run diagnostics so the Connection Status row reflects the fresh result

    def _poll_result(self) -> None:
        try:
            kind, report, error_message = self._result_queue.get_nowait()
        except queue.Empty:
            self.after(self._POLL_INTERVAL_MS, self._poll_result)
            return

        self._is_running = False
        if kind == "done":
            self._render(report, None)
        else:
            self._render(None, error_message)

    def _render(self, report: Optional[RuntimeDiagnosticsReport], error_message: Optional[str]) -> None:
        self._run_button.configure(state="normal")
        for child in self._body.winfo_children():
            child.destroy()

        if report is None:
            self._status_label.configure(text="Diagnostics failed.", text_color=_STATUS_COLORS[DiagnosticStatus.ERROR])
            ctk.CTkLabel(
                self._body, text=f"Could not run diagnostics: {error_message}", text_color="#ff6b6b", anchor="w"
            ).pack(anchor="w", pady=4)
            return

        self._report = report
        self._copy_button.configure(state="normal")
        overall = report.overall_status
        self._status_label.configure(text=f"Overall: {overall.value.upper()}", text_color=_STATUS_COLORS[overall])

        for check in report.checks:
            row = ctk.CTkFrame(self._body)
            row.pack(fill="x", pady=4)

            ctk.CTkLabel(row, text=_STATUS_DOTS[check.status], width=28).pack(side="left", padx=(10, 4), pady=8)

            text_frame = ctk.CTkFrame(row, fg_color="transparent")
            text_frame.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=6)
            ctk.CTkLabel(
                text_frame, text=f"{check.name}: {check.summary}", anchor="w", justify="left",
                font=ctk.CTkFont(weight="bold"),
            ).pack(anchor="w")
            if check.details:
                ctk.CTkLabel(
                    text_frame, text=check.details, anchor="w", justify="left", text_color="gray60", wraplength=700
                ).pack(anchor="w")

    def _copy_report(self) -> None:
        if self._report is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self._report.to_text_report())
        LoggingService.log_user_action(_LOGGER, "copy_diagnostics_report")
        self._status_label.configure(text="Report copied to clipboard.", text_color="gray60")
