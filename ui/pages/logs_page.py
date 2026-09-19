"""Logs page: the application's text log, plus structured AI request logs.

Two tabs:

- **Application Log** (unchanged from Version 3): the tail of the
  rotating ``logs/app.log`` file.
- **AI Requests** (Version 4, Features 7 & 9): every AI provider request
  made this session (and restored from previous sessions), with provider,
  model, latency, token counts, estimated cost, retry count, streaming
  time, and finish reason -- each copyable individually, or all at once.
"""

from __future__ import annotations

import customtkinter as ctk

from services.logging_service import LoggingService
from ui.pages.base_page import BasePage
from utils.file_utils import read_text_tail


class LogsPage(BasePage):
    """Read-only viewer for the application log and the AI request history."""

    def build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=30, pady=(30, 10), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(header_frame, text="Logs", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, sticky="w")

        refresh_button = ctk.CTkButton(header_frame, text="Refresh", width=100, command=self._refresh)
        refresh_button.grid(row=0, column=1, padx=(0, 8), sticky="e")

        self._copy_button = ctk.CTkButton(header_frame, text="Copy All", width=100, command=self._copy_active_tab)
        self._copy_button.grid(row=0, column=2, sticky="e")

        self._tabs = ctk.CTkTabview(self, command=self._on_tab_changed)
        self._tabs.grid(row=1, column=0, padx=30, pady=(0, 30), sticky="nsew")
        self._tabs.add("Application Log")
        self._tabs.add("AI Requests")

        self._build_app_log_tab(self._tabs.tab("Application Log"))
        self._build_ai_requests_tab(self._tabs.tab("AI Requests"))

    def _build_app_log_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        self._log_path_label = ctk.CTkLabel(tab, text="", text_color="gray60", anchor="w")
        self._log_path_label.grid(row=0, column=0, sticky="new", pady=(4, 4))

        self._log_box = ctk.CTkTextbox(tab, wrap="none", state="disabled", font=ctk.CTkFont(family="Courier", size=12))
        self._log_box.grid(row=1, column=0, sticky="nsew")

    def _build_ai_requests_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        self._ai_log_path_label = ctk.CTkLabel(tab, text="", text_color="gray60", anchor="w")
        self._ai_log_path_label.grid(row=0, column=0, sticky="new", pady=(4, 4))

        self._ai_requests_frame = ctk.CTkScrollableFrame(tab, label_text="")
        self._ai_requests_frame.grid(row=1, column=0, sticky="nsew")
        self._ai_requests_frame.grid_columnconfigure(0, weight=1)

    def on_show(self) -> None:
        self._refresh()

    def _on_tab_changed(self) -> None:
        self._copy_button.configure(
            text="Copy Log" if self._tabs.get() == "Application Log" else "Copy All"
        )

    def _refresh(self) -> None:
        self._refresh_app_log()
        self._refresh_ai_requests()
        self._on_tab_changed()

    def _refresh_app_log(self) -> None:
        log_path = LoggingService.log_path()
        self._log_path_label.configure(text=str(log_path))

        content = read_text_tail(log_path, max_lines=1000)
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.insert("1.0", content if content else "No log entries yet.")
        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _refresh_ai_requests(self) -> None:
        for child in self._ai_requests_frame.winfo_children():
            child.destroy()

        log_service = self.context.ai_request_log_service
        self._ai_log_path_label.configure(text=str(log_service.log_path))

        entries = list(reversed(log_service.entries()))
        if not entries:
            ctk.CTkLabel(
                self._ai_requests_frame, text="No AI requests yet -- generate an edit plan or send a chat message.",
                text_color="gray60", anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        for row, entry in enumerate(entries):
            self._build_entry_row(self._ai_requests_frame, row, entry)

    def _build_entry_row(self, parent, row: int, entry) -> None:
        status_color = "#3fb950" if entry.success else "#f85149"
        card = ctk.CTkFrame(parent)
        card.grid(row=row, column=0, sticky="ew", pady=4)
        card.grid_columnconfigure(0, weight=1)

        title = f"{entry.started_at.strftime('%Y-%m-%d %H:%M:%S')}  ·  {entry.task}  ·  {entry.provider}:{entry.model}"
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 0)
        )

        cost = f"${entry.estimated_cost_usd:.5f}" if entry.estimated_cost_usd is not None else "n/a"
        stream = f", streaming={entry.streaming_seconds:.2f}s" if entry.streaming_seconds is not None else ""
        detail = (
            f"latency={entry.latency_seconds:.2f}s{stream}  |  in={entry.input_tokens} out={entry.output_tokens} "
            f"total={entry.total_tokens} tokens  |  cost={cost}  |  retries={entry.retry_count}  |  "
            f"finish={entry.finish_reason}"
        )
        ctk.CTkLabel(card, text=detail, text_color="gray60", anchor="w").grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 4 if entry.error else 8)
        )

        if entry.error:
            ctk.CTkLabel(card, text=f"Error: {entry.error}", text_color=status_color, anchor="w", wraplength=700).grid(
                row=2, column=0, sticky="w", padx=12, pady=(0, 8)
            )

        copy_button = ctk.CTkButton(
            card, text="Copy", width=70, fg_color="gray30", hover_color="gray20",
            command=lambda e=entry: self._copy_text(e.to_json()),
        )
        copy_button.grid(row=0, column=1, rowspan=2, padx=12, pady=8, sticky="e")

    def _copy_active_tab(self) -> None:
        if self._tabs.get() == "Application Log":
            self._copy_text(self._log_box.get("1.0", "end"))
        else:
            self._copy_text(self.context.ai_request_log_service.to_copyable_text())

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
