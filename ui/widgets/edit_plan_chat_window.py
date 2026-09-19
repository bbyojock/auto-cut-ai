"""EditPlan Chat window (Version 4.5, Features 2 & 17).

Lets the user correct an EditPlan in natural language ("03:15~04:00 is
too heavily cut." -> "Keep it, the conversation there is important.").
Runs :class:`services.edit_plan_chat_service.EditPlanChatService` on a
background thread and polls a queue back onto the Tk thread, exactly like
:class:`ui.pages.chat_page.ChatPage` does for the general AI Chat page --
so a slow AI provider never freezes the window.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from tkinter import messagebox
from typing import Callable, Literal, Optional

import customtkinter as ctk

from core.exceptions import AutoCutAIError
from models.edit_plan import EditPlan
from services.edit_plan_chat_service import EditPlanChatError, EditPlanChatService
from services.logging_service import LoggingService

_LOGGER = LoggingService.get_logger("ui.edit_plan_chat_window")
_EventKind = Literal["done", "error"]


@dataclass
class _ChatEvent:
    kind: _EventKind
    reply: str = ""
    updated_plan: Optional[EditPlan] = None
    changed_count: int = 0
    error: str = ""


class EditPlanChatWindow(ctk.CTkToplevel):
    """A small chat window scoped to one EditPlan session."""

    _POLL_INTERVAL_MS = 100

    def __init__(
        self,
        master,
        chat_service: EditPlanChatService,
        on_plan_updated: Callable[[EditPlan], None],
    ) -> None:
        super().__init__(master)
        self.title("Chat about this EditPlan")
        self.geometry("640x560")
        self._chat_service = chat_service
        self._on_plan_updated = on_plan_updated
        self._result_queue: "queue.Queue[_ChatEvent]" = queue.Queue()
        self._is_sending = False
        self._build()
        self.transient(master)

    def _build(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkLabel(
            self, text="Chat about this EditPlan", font=ctk.CTkFont(size=18, weight="bold"),
        )
        header.grid(row=0, column=0, padx=20, pady=(20, 4), sticky="w")

        subheader = ctk.CTkLabel(
            self,
            text='e.g. "03:15~04:00 is too heavily cut." then "Keep it, the conversation there is important."',
            text_color="gray60", anchor="w", wraplength=580,
        )
        subheader.grid(row=0, column=0, padx=20, pady=(46, 0), sticky="w")

        self._transcript_box = ctk.CTkTextbox(self, wrap="word")
        self._transcript_box.grid(row=1, column=0, padx=20, pady=(50, 10), sticky="nsew")
        self._transcript_box.configure(state="disabled")

        entry_row = ctk.CTkFrame(self, fg_color="transparent")
        entry_row.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="ew")
        entry_row.grid_columnconfigure(0, weight=1)

        self._entry = ctk.CTkEntry(entry_row, placeholder_text="Type a message...")
        self._entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._entry.bind("<Return>", lambda _event: self._send_clicked())

        self._send_button = ctk.CTkButton(entry_row, text="Send", width=80, command=self._send_clicked)
        self._send_button.grid(row=0, column=1)

    def _append(self, speaker: str, text: str) -> None:
        self._transcript_box.configure(state="normal")
        if self._transcript_box.get("1.0", "end").strip():
            self._transcript_box.insert("end", "\n\n")
        self._transcript_box.insert("end", f"{speaker}: {text}")
        self._transcript_box.configure(state="disabled")
        self._transcript_box.see("end")

    def _send_clicked(self) -> None:
        if self._is_sending:
            return
        message = self._entry.get().strip()
        if not message:
            return
        self._entry.delete(0, "end")
        self._append("You", message)
        self._is_sending = True
        self._send_button.configure(state="disabled", text="Thinking...")

        thread = threading.Thread(target=self._run_send, args=(message,), daemon=True)
        thread.start()
        self.after(self._POLL_INTERVAL_MS, self._poll_result)

    def _run_send(self, message: str) -> None:
        """Runs on a background thread -- never touches Tk widgets directly."""
        try:
            turn = self._chat_service.send(message)
            self._result_queue.put(
                _ChatEvent(kind="done", reply=turn.assistant_reply, updated_plan=turn.updated_plan, changed_count=len(turn.changed_segments))
            )
        except (EditPlanChatError, AutoCutAIError) as exc:
            _LOGGER.error("EditPlan chat failed: %s", exc)
            self._result_queue.put(_ChatEvent(kind="error", error=str(exc)))
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors too
            _LOGGER.exception("Unexpected error in EditPlan chat")
            self._result_queue.put(_ChatEvent(kind="error", error=f"Unexpected error: {exc}"))

    def _poll_result(self) -> None:
        try:
            event = self._result_queue.get_nowait()
        except queue.Empty:
            self.after(self._POLL_INTERVAL_MS, self._poll_result)
            return

        self._is_sending = False
        self._send_button.configure(state="normal", text="Send")

        if event.kind == "done":
            reply = event.reply or (
                f"Updated {event.changed_count} segment(s)." if event.changed_count else "No change was needed."
            )
            self._append("AI", reply)
            if event.updated_plan is not None and event.changed_count:
                self._on_plan_updated(event.updated_plan)
        else:
            self._append("AI", f"Something went wrong: {event.error}")
            messagebox.showerror("Chat about EditPlan", event.error or "An unknown error occurred.")
