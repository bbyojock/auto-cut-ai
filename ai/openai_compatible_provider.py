"""Generic OpenAI-compatible chat completions provider.

Handles any service that speaks the same wire format as OpenAI's Chat
Completions API (``POST {base_url}/chat/completions``): OpenAI itself,
OpenRouter, Groq, and self-hosted servers like Ollama, LM Studio, and
vLLM. Adding a brand-new OpenAI-compatible service is just a new
:class:`models.provider_settings.ProviderSettings` entry (base URL, model,
keys) -- never a new provider class, and never a change to
:class:`ai.edit_planner.EditPlanner` or :class:`services.chat_service.ChatService`.
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
from utils.constants import DEFAULT_PROVIDER_MAX_RETRIES, DEFAULT_PROVIDER_TIMEOUT_SECONDS

_ROLE_MAP = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
}


class _HttpError(Exception):
    """Internal: an HTTP-level failure, carrying the status code (if any)
    so :meth:`OpenAICompatibleProvider._classify_error` can tell a rate
    limit apart from an invalid key apart from a network problem."""

    def __init__(self, status_code: Optional[int], message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenAICompatibleProvider(AIProvider):
    """AIProvider implementation for any OpenAI-compatible chat completions API.

    ``base_url`` and ``model`` are always plain configuration -- never
    hardcoded -- so this one class transparently supports OpenAI,
    OpenRouter, Groq, and any local server without needing to know which
    one it's talking to.
    """

    def __init__(
        self,
        display_name: str,
        base_url: str,
        key_manager: Optional[APIKeyManager] = None,
        requires_api_key: bool = True,
        logger: Optional[logging.Logger] = None,
        timeout_seconds: int = DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_PROVIDER_MAX_RETRIES,
    ) -> None:
        self._display_name = display_name
        self._base_url = (base_url or "").rstrip("/")
        self._key_manager = key_manager or APIKeyManager([])
        self._requires_api_key = requires_api_key
        self._logger = logger or LoggingService.get_logger(f"ai.openai_compatible.{display_name.lower()}")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(1, max_retries)

    @property
    def name(self) -> str:
        return self._display_name

    @property
    def key_manager(self) -> APIKeyManager:
        """The underlying key manager, shared with AppContext so key health
        tracking never diverges between the two."""
        return self._key_manager

    @property
    def requires_api_key(self) -> bool:
        return self._requires_api_key

    @property
    def requires_base_url(self) -> bool:
        return True

    def list_models(self) -> List[str]:
        # Model is a free-text field (see Settings) -- there is no fixed
        # list to enumerate, and none is ever hardcoded here.
        return []

    def generate_reply(self, history: List[Message], model: str) -> str:
        payload = self._build_payload(history, model, stream=False)
        return self._run_with_key_rotation(model, lambda api_key: self._call(api_key, payload))

    def generate_reply_stream(
        self, history: List[Message], model: str, on_chunk: Callable[[str], None]
    ) -> str:
        payload = self._build_payload(history, model, stream=True)
        return self._run_with_key_rotation(model, lambda api_key: self._call_stream(api_key, payload, on_chunk))

    def _run_with_key_rotation(self, model: str, call: Callable[[Optional[str]], str]) -> str:
        if not self._base_url:
            raise ProviderRequestError(f"{self.name} has no Base URL configured.")

        if not self._key_manager.has_any_keys():
            if self._requires_api_key:
                raise NoAPIKeysConfiguredError(
                    f"No API keys configured for {self.name}. Add at least one key in Settings."
                )
            # A genuinely keyless local server (e.g. a default Ollama setup).
            LoggingService.log_api_request(self._logger, provider=self.name, model=model, key="(no key required)")
            try:
                return call(None)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, OperationCancelledError):
                    raise
                raise ProviderRequestError(f"{self.name} request failed: {exc}") from exc

        total_keys = len(self._key_manager.keys)
        last_error: Optional[BaseException] = None

        for _ in range(total_keys):
            try:
                api_key = self._key_manager.get_next_key()
            except NoAPIKeysConfiguredError as exc:
                last_error = exc
                break

            LoggingService.log_api_request(
                self._logger, provider=self.name, model=model, key=api_key.masked()
            )
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
                            "%s request attempt %d/%d failed (%s), retrying: %s",
                            self.name, attempt + 1, self._max_retries, status.value, message,
                        )
                        continue
                    self._logger.warning(
                        "%s request failed with key %s (%s): %s", self.name, api_key.masked(), status.value, message
                    )
                    self._key_manager.report_failure(api_key, status, message)
                    break

        raise AllKeysExhaustedError(
            f"All {total_keys} {self.name} API key(s) failed to produce a response. Last error: {last_error}"
        )

    def _build_payload(self, history: List[Message], model: str, stream: bool) -> dict:
        messages = [{"role": _ROLE_MAP.get(message.role, "user"), "content": message.content} for message in history]
        return {"model": model, "messages": messages, "stream": stream}

    def _headers(self, api_key: Optional[str]) -> dict:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _call(self, api_key: Optional[str], payload: dict) -> str:
        response = self._post(api_key, payload, stream=False)
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError(f"{self.name} returned an unexpected response shape: {exc}") from exc

        if not text or not text.strip():
            raise ProviderRequestError(f"{self.name} returned an empty response.")
        return text

    def _call_stream(self, api_key: Optional[str], payload: dict, on_chunk: Callable[[str], None]) -> str:
        response = self._post(api_key, payload, stream=True)
        collected: List[str] = []
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                data_str = raw_line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    delta = json.loads(data_str)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    collected.append(delta)
                    on_chunk(delta)
        except requests.exceptions.RequestException:
            if collected:
                # Partial text already reached the caller; treat it as the
                # (possibly incomplete) reply instead of failing outright.
                return "".join(collected)
            raise

        full_text = "".join(collected)
        if not full_text:
            raise ProviderRequestError(f"{self.name} returned an empty streamed response.")
        return full_text

    def _post(self, api_key: Optional[str], payload: dict, stream: bool) -> requests.Response:
        url = f"{self._base_url}/chat/completions"
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

        if status_code == 429 or "rate limit" in lower_message:
            return APIKeyStatus.RATE_LIMITED, message
        if status_code in (401, 403) or any(
            phrase in lower_message for phrase in ("invalid api key", "incorrect api key", "unauthorized")
        ):
            return APIKeyStatus.INVALID, message
        if status_code == 402 or any(phrase in lower_message for phrase in ("insufficient_quota", "quota", "billing")):
            return APIKeyStatus.QUOTA_EXCEEDED, message
        if status_code in (408, 504) or "timed out" in lower_message or "timeout" in lower_message:
            return APIKeyStatus.TIMEOUT, message
        # Unknown error: treat as transient so it gets a cooldown instead
        # of being permanently blacklisted.
        return APIKeyStatus.RATE_LIMITED, message
