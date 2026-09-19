"""Manages an unlimited pool of API keys and rotates between them.

This class is provider-agnostic: any provider that authenticates with simple
bearer/API-key strings (Gemini today, potentially OpenRouter tomorrow) can
reuse it instead of re-implementing rotation logic.
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional

from core.exceptions import NoAPIKeysConfiguredError
from models.api_key import APIKey, APIKeyStatus
from services.logging_service import LoggingService


class APIKeyManager:
    """Round-robin key rotation with automatic exclusion of unhealthy keys.

    The manager never "gives up": as long as at least one key is
    :meth:`APIKey.is_usable`, :meth:`get_next_key` returns it. Callers use
    :meth:`report_success`/:meth:`report_failure` to feed results back so the
    manager can keep its health tracking accurate.
    """

    def __init__(self, api_keys: List[str], logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("ai.key_manager")
        self._lock = threading.Lock()
        self._keys: List[APIKey] = [APIKey(value=key) for key in api_keys if key.strip()]
        self._cursor = 0

    @property
    def keys(self) -> List[APIKey]:
        """A snapshot list of every tracked key (for the Settings/Logs UI)."""
        return list(self._keys)

    def has_any_keys(self) -> bool:
        return len(self._keys) > 0

    def usable_key_count(self) -> int:
        return sum(1 for key in self._keys if key.is_usable())

    def get_next_key(self) -> APIKey:
        """Return the next usable key in round-robin order.

        Raises:
            NoAPIKeysConfiguredError: if no keys were configured at all.
        """
        with self._lock:
            if not self._keys:
                raise NoAPIKeysConfiguredError("No Gemini API keys are configured.")

            total = len(self._keys)
            for offset in range(total):
                index = (self._cursor + offset) % total
                candidate = self._keys[index]
                if candidate.is_usable():
                    self._cursor = (index + 1) % total
                    candidate.mark_used()
                    return candidate

            # Nothing usable right now; the caller (provider) is expected to
            # treat this as "exhausted for this attempt" via the exception
            # below being raised by get_next_key's sibling helper.
            raise NoAPIKeysConfiguredError(
                "All configured API keys are currently unusable (invalid or quota exceeded)."
            )

    def report_success(self, key: APIKey) -> None:
        with self._lock:
            key.mark_success()

    def report_failure(self, key: APIKey, status: APIKeyStatus, error: str) -> None:
        with self._lock:
            key.mark_failed(status, error)
        LoggingService.log_key_switch(self._logger, reason=status.value, masked_key=key.masked())

    def add_key(self, value: str) -> None:
        with self._lock:
            if any(existing.value == value for existing in self._keys):
                return
            self._keys.append(APIKey(value=value))

    def remove_key(self, value: str) -> None:
        with self._lock:
            self._keys = [key for key in self._keys if key.value != value]

    def replace_keys(self, values: List[str]) -> None:
        """Replace the entire key pool, e.g. after editing keys in Settings."""
        with self._lock:
            self._keys = [APIKey(value=value) for value in values if value.strip()]
            self._cursor = 0
