"""AI Chat page: send messages to the active AI provider."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Literal, Optional

import customtkinter as ctk

from core.exceptions import AutoCutAIError
from models.message import MessageRole
from services.logging_service import LoggingService
from ui.pages.base_page import BasePage

_LOGGER = LoggingService.get_logger("ui.chat_page")

_EventKind = Literal["chunk", "done", "error"]


@dataclass
class _StreamEvent:
    """A single event produced by the background generation thread.

    ``kind == "chunk"``: ``text`` holds one incremental text fragment to
    append to the in-progress assistant message.
    ``kind == "done"``: the reply finished successfully; ``text`` is unused
    (the full reply was already assembled from the chunks).
    ``kind == "error"``: ``text`` holds a user-facing error message.
    """

    kind: _EventKind
    text: Optional[str] = None


class ChatPage(BasePage):
    """Simple chat interface: message list + input box + send button.

    Network calls run on a daemon thread; results are handed back to the
    Tk main thread through a thread-safe queue polled via ``after()``, which
    is the standard safe pattern for Tkinter/CustomTkinter UIs.
    """

    _POLL_INTERVAL_MS = 100

    def build(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._result_queue: "queue.Queue[_StreamEvent]" = queue.Queue()
        self._is_generating = False
        self._assistant_reply_started = False

        self._transcript = ctk.CTkTextbox(self, wrap="word", state="disabled")
        self._transcript.grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 10), sticky="nsew")

        self._status_label = ctk.CTkLabel(self, text="", text_color="gray60", anchor="w")
        self._status_label.grid(row=1, column=0, columnspan=2, padx=20, sticky="w")

        self._input_box = ctk.CTkTextbox(self, height=70, wrap="word")
        self._input_box.grid(row=2, column=0, padx=(20, 10), pady=(6, 20), sticky="ew")
        self._input_box.bind("<Return>", self._on_enter_pressed)
        self._input_box.bind("<Shift-Return>", lambda _event: None)

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=2, column=1, padx=(0, 20), pady=(6, 20), sticky="ns")

        self._send_button = ctk.CTkButton(button_frame, text="Send", command=self._send_clicked)
        self._send_button.pack(fill="x", pady=(0, 6))

        self._new_chat_button = ctk.CTkButton(
            button_frame, text="New Chat", fg_color="gray30", hover_color="gray20",
            command=self._new_chat_clicked,
        )
        self._new_chat_button.pack(fill="x")

    def on_show(self) -> None:
        self._render_history()
        self._update_status_idle()

    def _on_enter_pressed(self, _event: object) -> str:
        self._send_clicked()
        return "break"  # prevent inserting a literal newline

    def _send_clicked(self) -> None:
        if self._is_generating:
            return
        text = self._input_box.get("1.0", "end").strip()
        if not text:
            return

        self._input_box.delete("1.0", "end")
        self._append_line("You", text)
        self._set_generating(True)

        thread = threading.Thread(target=self._run_generation, args=(text,), daemon=True)
        thread.start()
        self.after(self._POLL_INTERVAL_MS, self._poll_result)

    def _run_generation(self, text: str) -> None:
        def on_chunk(delta: str) -> None:
            # Called synchronously from this background thread by the
            # provider; just hand the fragment off to the Tk thread via the
            # thread-safe queue, same as the final done/error events.
            self._result_queue.put(_StreamEvent(kind="chunk", text=delta))

        try:
            self.context.chat_service.send_message_stream(text, on_chunk)
            self._result_queue.put(_StreamEvent(kind="done"))
        except AutoCutAIError as exc:
            _LOGGER.error("Chat generation failed: %s", exc)
            self._result_queue.put(_StreamEvent(kind="error", text=str(exc)))
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors to the user too
            _LOGGER.exception("Unexpected error during chat generation")
            self._result_queue.put(_StreamEvent(kind="error", text=f"Unexpected error: {exc}"))

    def _poll_result(self) -> None:
        # Drain everything currently available in one pass so a burst of
        # chunks renders in a single UI update instead of one per tick.
        while True:
            try:
                event = self._result_queue.get_nowait()
            except queue.Empty:
                break

            if event.kind == "chunk" and event.text:
                self._append_stream_chunk(event.text)
            elif event.kind == "error":
                self._finish_stream_reply()
                self._append_line("Error", event.text or "Unknown error.")
                self._set_generating(False)
            elif event.kind == "done":
                self._finish_stream_reply()
                self._set_generating(False)

        if self._is_generating:
            self.after(self._POLL_INTERVAL_MS, self._poll_result)

    def _new_chat_clicked(self) -> None:
        self.context.chat_service.new_conversation()
        self._render_history()
        self._update_status_idle()

    def _render_history(self) -> None:
        self._transcript.configure(state="normal")
        self._transcript.delete("1.0", "end")
        self._transcript.configure(state="disabled")
        for message in self.context.chat_service.history():
            speaker = "You" if message.role == MessageRole.USER else self.context.chat_service.provider_name
            self._append_line(speaker, message.content)

    def _append_line(self, speaker: str, text: str) -> None:
        self._transcript.configure(state="normal")
        self._transcript.insert("end", f"{speaker}: {text}\n\n")
        self._transcript.configure(state="disabled")
        self._transcript.see("end")

    def _append_stream_chunk(self, delta: str) -> None:
        """Append one streamed text fragment, showing the speaker label once."""
        self._transcript.configure(state="normal")
        if not self._assistant_reply_started:
            speaker = self.context.chat_service.provider_name
            self._transcript.insert("end", f"{speaker}: ")
            self._assistant_reply_started = True
        self._transcript.insert("end", delta)
        self._transcript.configure(state="disabled")
        self._transcript.see("end")

    def _finish_stream_reply(self) -> None:
        """Close off the in-progress streamed assistant message, if any."""
        if not self._assistant_reply_started:
            return
        self._transcript.configure(state="normal")
        self._transcript.insert("end", "\n\n")
        self._transcript.configure(state="disabled")
        self._transcript.see("end")
        self._assistant_reply_started = False

    def _set_generating(self, generating: bool) -> None:
        self._is_generating = generating
        self._send_button.configure(state="disabled" if generating else "normal")
        if generating:
            self._status_label.configure(text="Generating reply...")
        else:
            self._update_status_idle()

    def _update_status_idle(self) -> None:
        model = self.context.chat_service.model
        provider = self.context.chat_service.provider_name
        self._status_label.configure(text=f"{provider} · {model}")
