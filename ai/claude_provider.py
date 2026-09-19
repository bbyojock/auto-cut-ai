"""Anthropic Claude provider (Messages API).

Claude does not speak the OpenAI chat-completions wire format, so it gets
its own small implementation -- but it's still just an HTTP POST with
``requests``, no heavyweight SDK dependency required, and it plugs into
the exact same :class:`ai.base_provider.AIProvider` interface as every
other provider.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, List, Optional, Tuple

import requests

from ai.base_provider import AIProvider
from ai.key_manager import APIKeyManager
from core.exceptions import (
    AllKeysExhaustedError,
    NoAPIKeysConfiguredError,
    OperationCancelledError,
    ProviderRequestError,
)
from models.api_key import APIKeyStatus
from models.message import Message, MessageRole
from services.logging_service import LoggingService
from utils.constants import ANTHROPIC_API_VERSION, DEFAULT_PROVIDER_MAX_RETRIES, DEFAULT_PROVIDER_TIMEOUT_SECONDS

_DEFAULT_MAX_TOKENS = 4096


class _HttpError(Exception):
    """Internal: an HTTP-level failure, carrying the status code (if any)."""

    def __init__(self, status_code: Optional[int], message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class ClaudeProvider(AIProvider):
    """AIProvider implementation for Anthropic's Claude Messages API."""

    def __init__(
        self,
        base_url: str,
        key_manager: APIKeyManager,
        logger: Optional[logging.Logger] = None,
        timeout_seconds: int = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_PROVIDER_MAX_RETRIES,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._key_manager = key_manager
        self._logger = logger or LoggingService.get_logger("ai.claude")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(1, max_retries)

    @property
    def name(self) -> str:
        return "Claude"

    @property
    def key_manager(self) -> APIKeyManager:
        """The underlying key manager, shared with AppContext so key health
        tracking never diverges between the two."""
        return self._key_manager

    @property
    def requires_base_url(self) -> bool:
        return True

    def list_models(self) -> List[str]:
        return []  # free-text model entry; never hardcoded here

    def generate_reply(self, history: List[Message], model: str) -> str:
        system_prompt, messages = self._build_messages(history)
        payload = self._build_payload(model, system_prompt, messages, stream=False)
        return self._run_with_key_rotation(model, lambda api_key: self._call(api_key, payload))

    def generate_reply_stream(
        self, history: List[Message], model: str, on_chunk: Callable[[str], None]
    ) -> str:
        system_prompt, messages = self._build_messages(history)
        payload = self._build_payload(model, system_prompt, messages, stream=True)
        return self._run_with_key_rotation(model, lambda api_key: self._call_stream(api_key, payload, on_chunk))

    def _run_with_key_rotation(self, model: str, call: Callable[[str], str]) -> str:
        if not self._base_url:
            raise ProviderRequestError("Claude has no Base URL configured.")
        if not self._key_manager.has_any_keys():
            raise NoAPIKeysConfiguredError("No Claude API keys configured. Add at least one key in Settings.")

        total_keys = len(self._key_manager.keys)
        last_error: Optional[BaseException] = None

        for _ in range(total_keys):
            try:
                api_key = self._key_manager.get_next_key()
            except NoAPIKeysConfiguredError as exc:
                last_error = exc
                break

            LoggingService.log_api_request(self._logger, provider=self.name, model=model, key=api_key.masked())
            for attempt in range(self._max_retries):
                try:
                    result = call(api_key.value)
                    self._key_manager.report_success(api_key)
                    return result
                except Exception as exc:  # noqa: BLE001 - classified below
                    if isinstance(exc, OperationCancelledError):
                        raise
                    status, message = self._classify_error(exc)
                    last_error = exc
                    if attempt + 1 < self._max_retries and status in (APIKeyStatus.RATE_LIMITED, APIKeyStatus.TIMEOUT):
                        self._logger.warning(
                            "Claude request attempt %d/%d failed (%s), retrying: %s",
                            attempt + 1, self._max_retries, status.value, message,
                        )
                        continue
                    self._logger.warning(
                        "Claude request failed with key %s (%s): %s", api_key.masked(), status.value, message
                    )
                    self._key_manager.report_failure(api_key, status, message)
                    break

        raise AllKeysExhaustedError(
            f"All {total_keys} Claude API key(s) failed to produce a response. Last error: {last_error}"
        )

    @staticmethod
    def _build_messages(history: List[Message]) -> Tuple[Optional[str], List[dict]]:
        """Split ``history`` into Claude's separate system-prompt + messages shape."""
        system_parts = [message.content for message in history if message.role == MessageRole.SYSTEM]
        messages = [
            {"role": "assistant" if message.role == MessageRole.ASSISTANT else "user", "content": message.content}
            for message in history
            if message.role != MessageRole.SYSTEM
        ]
        system_prompt = "\n\n".join(system_parts) if system_parts else None
        return system_prompt, messages

    @staticmethod
    def _build_payload(model: str, system_prompt: Optional[str], messages: List[dict], stream: bool) -> dict:
        payload = {"model": model, "max_tokens": _DEFAULT_MAX_TOKENS, "messages": messages, "stream": stream}
        if system_prompt:
            payload["system"] = system_prompt
        return payload

    def _headers(self, api_key: str) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        }

    def _post(self, api_key: str, payload: dict, stream: bool) -> requests.Response:
        url = f"{self._base_url}/v1/messages"
        try:
            response = requests.post(
                url, headers=self._headers(api_key), json=payload, timeout=self._timeout_seconds, stream=stream
            )
        except requests.exceptions.Timeout as exc:
            raise _HttpError(None, f"Request to {self._base_url} timed out after {self._timeout_seconds}s.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise _HttpError(None, f"Could not connect to {self._base_url}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise _HttpError(None, f"Request to {self._base_url} failed: {exc}") from exc

        if response.status_code >= 400:
            raise _HttpError(response.status_code, self._extract_error_message(response))
        return response

    def _call(self, api_key: str, payload: dict) -> str:
        response = self._post(api_key, payload, stream=False)
        try:
            data = response.json()
            blocks = data["content"]
            text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderRequestError(f"Claude returned an unexpected response shape: {exc}") from exc

        if not text or not text.strip():
            raise ProviderRequestError("Claude returned an empty response.")
        return text

    def _call_stream(self, api_key: str, payload: dict, on_chunk: Callable[[str], None]) -> str:
        response = self._post(api_key, payload, stream=True)
        collected: List[str] = []
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                data_str = raw_line[len("data:"):].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                except ValueError:
                    continue
                if event.get("type") == "content_block_delta":
                    delta_text = event.get("delta", {}).get("text")
                    if delta_text:
                        collected.append(delta_text)
                        on_chunk(delta_text)
        except requests.exceptions.RequestException:
            if collected:
                return "".join(collected)
            raise

        full_text = "".join(collected)
        if not full_text:
            raise ProviderRequestError("Claude returned an empty streamed response.")
        return full_text

    @staticmethod
    def _extract_error_message(response: requests.Response) -> str:
        try:
            data = response.json()
            error = data.get("error")
            message = (error.get("message") if isinstance(error, dict) else error) or data.get("message")
            if message:
                return str(message)
        except ValueError:
            pass
        return f"HTTP {response.status_code}: {response.text[:300]}"

    @staticmethod
    def _classify_error(exc: Exception) -> Tuple[APIKeyStatus, str]:
        message = str(exc)
        lower_message = message.lower()
        status_code = getattr(exc, "status_code", None)

        if status_code == 429 or "rate_limit" in lower_message or "rate limit" in lower_message:
            return APIKeyStatus.RATE_LIMITED, message
        if status_code in (401, 403) or "authentication_error" in lower_message or "invalid x-api-key" in lower_message:
            return APIKeyStatus.INVALID, message
        if status_code == 402 or "credit balance" in lower_message or "quota" in lower_message:
            return APIKeyStatus.QUOTA_EXCEEDED, message
        if status_code in (408, 504) or "timed out" in lower_message or "timeout" in lower_message:
            return APIKeyStatus.TIMEOUT, message
        return APIKeyStatus.RATE_LIMITED, message
