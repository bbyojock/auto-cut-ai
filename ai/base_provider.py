"""Abstract contract every AI provider must satisfy."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable, List

from core.exceptions import AutoCutAIError
from models.connection_test_result import ConnectionTestResult
from models.message import Message, MessageRole


class AIProvider(ABC):
    """Common interface for all AI providers (Gemini, OpenRouter, Claude, local, ...).

    Concrete implementations are responsible for their own resilience
    (timeouts, retries, key rotation) and must translate provider-specific
    failures into subclasses of :class:`core.exceptions.AutoCutAIError` so
    the UI layer can handle every provider identically.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name, e.g. ``"Gemini"``."""
        raise NotImplementedError

    @property
    def requires_api_key(self) -> bool:
        """Whether this provider needs at least one API key configured.

        Concrete (non-abstract) with a default of ``True``, since that
        covers every provider implemented so far. Override for something
        like a fully local provider that needs no key at all. Used by
        :class:`ai.provider_factory.ProviderFactory`/``models.provider_info.ProviderInfo``
        so a future Settings screen can adapt its form per provider.
        """
        return True

    @property
    def requires_base_url(self) -> bool:
        """Whether this provider needs a user-supplied base URL.

        Concrete with a default of ``False`` (fine for hosted providers
        with a fixed endpoint, like Gemini). Override to ``True`` for
        things like local (Ollama/LM Studio) or generic OpenAI-compatible
        endpoints.
        """
        return False

    @abstractmethod
    def list_models(self) -> List[str]:
        """Return the list of model identifiers this provider supports."""
        raise NotImplementedError

    @abstractmethod
    def generate_reply(self, history: List[Message], model: str) -> str:
        """Generate the assistant's next reply given the full conversation history.

        Args:
            history: The full conversation so far, oldest message first.
            model: The provider-specific model identifier to use.

        Returns:
            The assistant's reply as plain text.

        Raises:
            core.exceptions.AutoCutAIError: on any unrecoverable failure.
        """
        raise NotImplementedError

    @abstractmethod
    def generate_reply_stream(
        self, history: List[Message], model: str, on_chunk: Callable[[str], None]
    ) -> str:
        """Generate the assistant's next reply, streaming partial text as it arrives.

        Args:
            history: The full conversation so far, oldest message first.
            model: The provider-specific model identifier to use.
            on_chunk: Invoked once per incoming text fragment, in order, from
                whatever thread this method runs on. Implementations must call
                it synchronously (not from a separate thread) so callers can
                safely marshal chunks back to a UI thread themselves.

        Returns:
            The full assistant reply (equivalent to concatenating every chunk
            passed to ``on_chunk``).

        Raises:
            core.exceptions.AutoCutAIError: on any unrecoverable failure.
        """
        raise NotImplementedError

    def test_connection(self, model: str) -> ConnectionTestResult:
        """Verify this provider's API key(s), base URL, and model all work.

        Concrete (non-abstract): implemented once, here, on top of
        :meth:`generate_reply`, so every provider -- current and future --
        gets a working "Test Connection" feature for free, with no
        provider-specific code required anywhere else. It sends one
        minimal request and reports success/failure in plain language,
        never a raw traceback.
        """
        started = time.monotonic()
        try:
            reply = self.generate_reply(
                [Message(role=MessageRole.USER, content="Reply with only the word: ok")], model
            )
        except AutoCutAIError as exc:
            return ConnectionTestResult(success=False, message=str(exc), latency_seconds=time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 - never let a raw traceback reach the UI
            return ConnectionTestResult(
                success=False, message=f"Unexpected error: {exc}", latency_seconds=time.monotonic() - started
            )

        latency = time.monotonic() - started
        if not reply or not reply.strip():
            return ConnectionTestResult(
                success=False, message="Provider returned an empty response.", latency_seconds=latency
            )
        return ConnectionTestResult(
            success=True,
            message=f"Connected successfully to {self.name} (model '{model}') in {latency:.2f}s.",
            latency_seconds=latency,
        )
