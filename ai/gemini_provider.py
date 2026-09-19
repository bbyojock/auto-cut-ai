"""Google Gemini provider using the current Interactions API.

Clients are deliberately request-scoped.  A closed ``genai.Client`` is never
stored or reused, which is important for key rotation and background UI tests.
"""

from __future__ import annotations

import logging
import json
import socket
import ssl
import time
import math
from typing import Callable, List, Optional, Tuple

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from httpx import ConnectError, HTTPError, ReadTimeout, TimeoutException

from ai.base_provider import AIProvider
from ai.key_manager import APIKeyManager
from core.exceptions import (
    AllKeysExhaustedError,
    NoAPIKeysConfiguredError,
    OperationCancelledError,
    ProviderRequestError,
)
from models.api_key import APIKey, APIKeyStatus
from models.connection_test_result import ConnectionTestResult
from models.message import Message, MessageRole
from services.logging_service import LoggingService
from utils.constants import (
    AVAILABLE_GEMINI_MODELS,
    DEFAULT_PROVIDER_MAX_RETRIES,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    GEMINI_HEALTH_CHECK_TIMEOUT_SECONDS,
    GEMINI_STREAM_TIMEOUT_SECONDS,
)

_ROLE_LABELS = {MessageRole.USER: "User", MessageRole.ASSISTANT: "Assistant"}


class GeminiProvider(AIProvider):
    """AIProvider implementation backed by Google's Gemini Interactions API."""

    def __init__(
        self,
        key_manager: APIKeyManager,
        logger: Optional[logging.Logger] = None,
        base_url: Optional[str] = None,
        timeout_seconds: int = GEMINI_REQUEST_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_PROVIDER_MAX_RETRIES,
    ) -> None:
        self._key_manager = key_manager
        self._logger = logger or LoggingService.get_logger("ai.gemini")
        self._base_url = base_url.strip() if base_url else None
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(1, max_retries)

    @property
    def name(self) -> str:
        return "Gemini"

    @property
    def key_manager(self) -> APIKeyManager:
        return self._key_manager

    def list_models(self) -> List[str]:
        return list(AVAILABLE_GEMINI_MODELS)

    def generate_reply(self, history: List[Message], model: str) -> str:
        prompt = self._build_prompt(history)
        return self._run_with_key_rotation(
            model, lambda api_key, retry_count: self._call_gemini(api_key, model, prompt, retry_count)
        )

    def generate_reply_stream(
        self, history: List[Message], model: str, on_chunk: Callable[[str], None]
    ) -> str:
        prompt = self._build_prompt(history)
        return self._run_with_key_rotation(
            model,
            lambda api_key, retry_count: self._call_gemini_stream(
                api_key, model, prompt, on_chunk, retry_count
            ),
        )

    def test_connection(self, model: str) -> ConnectionTestResult:
        """Run a minimal Interactions request and expose actionable diagnostics."""
        started = time.monotonic()
        if not self._key_manager.has_any_keys():
            return self._connection_result(
                model, started, False, "No Gemini API key configured."
            )
        api_key: Optional[APIKey] = None
        try:
            api_key = self._key_manager.get_next_key()
            response = self._request(
                api_key.value, model, "Reply with only: OK", GEMINI_HEALTH_CHECK_TIMEOUT_SECONDS
            )
            text = self._response_text(response)
            latency = time.monotonic() - started
            self._key_manager.report_success(api_key)
            self._log_response(model, latency, response, text)
            if not text:
                return self._connection_result(model, started, False, "Gemini returned an empty response.")
            return self._connection_result(model, started, True, "Connected successfully.", response, text)
        except Exception as exc:  # noqa: BLE001 - diagnostics must report provider errors
            status, message = self._classify_error(exc)
            if api_key is not None:
                self._key_manager.report_failure(api_key, status, message)
            self._logger.exception("Gemini health check failed (%s): %s", status.value, message)
            return self._connection_result(model, started, False, message, error=exc)

    def _run_with_key_rotation(self, model: str, call: Callable[[str, int], str]) -> str:
        if not self._key_manager.has_any_keys():
            raise NoAPIKeysConfiguredError(
                "No Gemini API keys configured. Add at least one key in Settings."
            )

        total_keys = len(self._key_manager.keys)
        last_error: Optional[BaseException] = None
        for _ in range(total_keys):
            try:
                api_key = self._key_manager.get_next_key()
            except NoAPIKeysConfiguredError as exc:
                last_error = exc
                break
            LoggingService.log_api_request(
                self._logger, provider=self.name, model=model, key=api_key.masked(), api="interactions"
            )
            attempt = 0
            timeout_retries = 0
            while True:
                try:
                    result = call(api_key.value, timeout_retries)
                    self._key_manager.report_success(api_key)
                    return result
                except Exception as exc:  # noqa: BLE001 - classified below
                    if isinstance(exc, OperationCancelledError):
                        # Never retry/rotate keys for a user-requested cancel
                        # (Version 4, Feature 3) -- propagate immediately so
                        # the UI can restore itself without a scary error.
                        raise
                    attempt += 1
                    status, message = self._classify_error(exc)
                    last_error = exc
                    elapsed = getattr(exc, "elapsed_seconds", None)
                    self._logger.warning(
                        "Gemini request failed key=%s status=%s retry_count=%d timeout=%ss elapsed=%ss: %s",
                        api_key.masked(), status.value, timeout_retries, self._timeout_seconds,
                        f"{elapsed:.2f}" if isinstance(elapsed, float) else "n/a", message,
                    )
                    if status == APIKeyStatus.REQUEST_ERROR:
                        raise ProviderRequestError(message) from exc
                    if status == APIKeyStatus.TIMEOUT and timeout_retries == 0:
                        timeout_retries += 1
                        backoff = math.pow(2, timeout_retries - 1)
                        self._logger.warning(
                            "Retrying Gemini request after timeout retry_count=%d backoff=%.1fs "
                            "timeout=%ss",
                            timeout_retries, backoff, self._timeout_seconds,
                        )
                        time.sleep(backoff)
                        continue
                    if attempt < self._max_retries and status in (
                        APIKeyStatus.RATE_LIMITED,
                        APIKeyStatus.TIMEOUT,
                        APIKeyStatus.SERVER_ERROR,
                        APIKeyStatus.NETWORK_ERROR,
                    ):
                        continue
                    self._key_manager.report_failure(api_key, status, message)
                    break

        raise AllKeysExhaustedError(
            f"All {total_keys} Gemini API key(s) failed. Last error: {last_error}"
        )

    def _client(self, api_key: str, timeout_seconds: int) -> genai.Client:
        # An empty base URL intentionally leaves SDK endpoint selection intact.
        http_options = genai_types.HttpOptions(
            timeout=timeout_seconds * 1000,
            **({"base_url": self._base_url} if self._base_url else {}),
        )
        return genai.Client(api_key=api_key, http_options=http_options)

    def _request(
        self, api_key: str, model: str, prompt: str, timeout_seconds: int, retry_count: int = 0
    ):
        started = time.monotonic()
        self._logger.info(
            "Gemini request start provider=%s model=%s timeout=%ss retry_count=%d",
            self.name, model, timeout_seconds, retry_count,
        )
        client = self._client(api_key, timeout_seconds)
        try:
            return client.interactions.create(
                model=model,
                input=prompt,
            )
        finally:
            elapsed = time.monotonic() - started
            self._logger.info(
                "Gemini request finished provider=%s model=%s timeout=%ss elapsed=%.2fs",
                self.name, model, timeout_seconds, elapsed,
            )
            client.close()

    def _call_gemini(self, api_key: str, model: str, prompt: str, retry_count: int = 0) -> str:
        started = time.monotonic()
        response = self._request(api_key, model, prompt, self._timeout_seconds, retry_count)
        text = self._response_text(response)
        self._log_response(model, time.monotonic() - started, response, text)
        if not text:
            raise ProviderRequestError("Gemini returned an empty response.")
        return text

    def _call_gemini_stream(
        self,
        api_key: str,
        model: str,
        prompt: str,
        on_chunk: Callable[[str], None],
        retry_count: int = 0,
    ) -> str:
        started = time.monotonic()
        self._logger.info(
            "Gemini streaming request start provider=%s model=%s timeout=%ss retry_count=%d",
            self.name, model, GEMINI_STREAM_TIMEOUT_SECONDS, retry_count,
        )
        client = self._client(api_key, GEMINI_STREAM_TIMEOUT_SECONDS)
        collected: List[str] = []
        try:
            stream = client.interactions.create(model=model, input=prompt, stream=True)
            for event in stream:
                delta = getattr(event, "delta", None)
                text = getattr(delta, "text", None)
                if text:
                    collected.append(text)
                    on_chunk(text)
        finally:
            self._logger.info(
                "Gemini streaming request finished provider=%s model=%s timeout=%ss "
                "elapsed=%.2fs retry_count=%d",
                self.name,
                model,
                GEMINI_STREAM_TIMEOUT_SECONDS,
                time.monotonic() - started,
                retry_count,
            )
            client.close()
        result = "".join(collected)
        self._log_response(model, time.monotonic() - started, None, result)
        if not result:
            raise ProviderRequestError("Gemini returned an empty response.")
        return result

    @staticmethod
    def _build_prompt(history: List[Message]) -> str:
        turns = []
        for message in history:
            label = _ROLE_LABELS.get(message.role)
            if label:
                turns.append(f"{label}: {message.content}")
        return "\n\n".join(turns)

    @staticmethod
    def _response_text(response: object) -> str:
        text = getattr(response, "output_text", None)
        return text.strip() if isinstance(text, str) else ""

    def _log_response(self, model: str, latency: float, response: object, text: str) -> None:
        status_code = getattr(response, "status_code", None) if response else None
        LoggingService.log_api_result(
            self._logger, self.name, model, latency, status_code, text[:500] if text else None
        )

    def _connection_result(
        self,
        model: str,
        started: float,
        success: bool,
        message: str,
        response: object = None,
        text: Optional[str] = None,
        error: Optional[Exception] = None,
    ) -> ConnectionTestResult:
        status_code = getattr(error, "code", None) if error else None
        detail = message if error is None else f"{message}"
        return ConnectionTestResult(
            success=success,
            message=f"{self.name} {'OK' if success else 'error'}: {detail}",
            latency_seconds=time.monotonic() - started,
            provider=self.name,
            model=model,
            status_code=status_code,
            response=text or (str(response) if response else None),
        )

    @staticmethod
    def _classify_error(exc: Exception) -> Tuple[APIKeyStatus, str]:
        message = str(exc) or exc.__class__.__name__
        lower = message.lower()
        code = getattr(exc, "code", None)
        if isinstance(exc, genai_errors.APIError):
            if code == 401:
                return APIKeyStatus.INVALID, f"HTTP 401: {message}"
            if code == 403:
                return APIKeyStatus.INVALID, f"HTTP 403: {message}"
            if code == 404:
                return APIKeyStatus.REQUEST_ERROR, f"HTTP 404: {message}"
            if code == 429:
                return APIKeyStatus.RATE_LIMITED, f"HTTP 429: {message}"
            if code in (500, 503):
                return APIKeyStatus.SERVER_ERROR, f"HTTP {code}: {message}"
            if code == 408 or "timeout" in lower or "deadline" in lower:
                return APIKeyStatus.TIMEOUT, f"HTTP {code}: {message}" if code else message
            if "quota" in lower or "resource_exhausted" in lower:
                return APIKeyStatus.QUOTA_EXCEEDED, message
            return APIKeyStatus.REQUEST_ERROR, f"HTTP {code}: {message}" if code else message
        if isinstance(exc, (TimeoutException, ReadTimeout, TimeoutError)):
            return APIKeyStatus.TIMEOUT, f"Network timeout: {message}"
        if isinstance(exc, (ConnectError, ConnectionError, socket.gaierror)):
            return APIKeyStatus.NETWORK_ERROR, f"Connection/DNS error: {message}"
        if isinstance(exc, ssl.SSLError):
            return APIKeyStatus.NETWORK_ERROR, f"SSL error: {message}"
        if isinstance(exc, HTTPError):
            return APIKeyStatus.NETWORK_ERROR, f"Network error: {message}"
        if isinstance(exc, json.JSONDecodeError):
            return APIKeyStatus.REQUEST_ERROR, f"Invalid JSON response: {message}"
        if isinstance(exc, ProviderRequestError):
            return APIKeyStatus.REQUEST_ERROR, message
        return APIKeyStatus.REQUEST_ERROR, message
