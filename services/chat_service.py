"""Orchestrates conversation state and AI provider calls for the UI layer.

The UI never talks to an :class:`ai.base_provider.AIProvider` or
:class:`core.session.ConversationSession` directly; it goes through this
service, keeping the UI layer free of business logic (separation of
concerns).
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from ai.base_provider import AIProvider
from core.session import ConversationSession
from models.message import Message
from services.logging_service import LoggingService


class ChatService:
    """Sends user messages to the active provider and records the reply."""

    def __init__(
        self,
        provider: AIProvider,
        session: ConversationSession,
        model: str,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._provider = provider
        self._session = session
        self._model = model
        self._logger = logger or LoggingService.get_logger("services.chat")

    @property
    def model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        self._logger.info("Chat model changed to %s", model)
        self._model = model

    def set_provider(self, provider: AIProvider) -> None:
        self._logger.info("Chat provider changed to %s", provider.name)
        self._provider = provider

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def history(self) -> List[Message]:
        return self._session.history

    def new_conversation(self) -> None:
        LoggingService.log_user_action(self._logger, "new_conversation")
        self._session.clear()

    def send_message(self, text: str) -> str:
        """Send ``text`` as a user message and return the assistant's reply.

        This call is blocking (it performs a network request) and is meant
        to be invoked from a background thread by the UI layer so the
        interface stays responsive.
        """
        text = text.strip()
        if not text:
            raise ValueError("Cannot send an empty message.")

        LoggingService.log_user_action(self._logger, "send_message", length=len(text))
        self._session.add_user_message(text)

        reply_text = self._provider.generate_reply(self._session.history, self._model)
        self._session.add_assistant_message(reply_text)
        return reply_text

    def send_message_stream(self, text: str, on_chunk: Callable[[str], None]) -> str:
        """Like :meth:`send_message`, but streams partial text via ``on_chunk``.

        ``on_chunk`` is invoked synchronously on the calling thread once per
        text fragment as it arrives from the provider. This call is still
        blocking overall (it only returns once the full reply is known) and
        is meant to be run from a background thread, exactly like
        :meth:`send_message`.
        """
        text = text.strip()
        if not text:
            raise ValueError("Cannot send an empty message.")

        LoggingService.log_user_action(self._logger, "send_message_stream", length=len(text))
        self._session.add_user_message(text)

        reply_text = self._provider.generate_reply_stream(self._session.history, self._model, on_chunk)
        self._session.add_assistant_message(reply_text)
        return reply_text
