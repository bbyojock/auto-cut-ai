"""API key model used by AI providers that support key rotation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

# Cooldown applied to transient failures (rate limit / timeout) before a key
# is considered usable again. Quota-exceeded and invalid keys are not put on
# a cooldown; they stay excluded for the rest of the runtime session.
TRANSIENT_FAILURE_COOLDOWN = timedelta(seconds=30)


class APIKeyStatus(str, Enum):
    """Current health status of an API key."""

    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    QUOTA_EXCEEDED = "quota_exceeded"
    INVALID = "invalid"
    REQUEST_ERROR = "request_error"
    SERVER_ERROR = "server_error"
    NETWORK_ERROR = "network_error"


@dataclass(slots=True)
class APIKey:
    """A single API key tracked by an :class:`ai.key_manager.APIKeyManager`.

    The key keeps track of its own health so the manager can decide, without
    any external state, whether the key should be tried again.
    """

    value: str
    status: APIKeyStatus = APIKeyStatus.ACTIVE
    fail_count: int = 0
    last_used_at: Optional[datetime] = None
    last_error: Optional[str] = None
    cooldown_until: Optional[datetime] = None

    def mark_used(self) -> None:
        """Record that this key was just used for a request."""
        self.last_used_at = datetime.now()

    def mark_success(self) -> None:
        """Reset failure state after a successful call."""
        self.status = APIKeyStatus.ACTIVE
        self.fail_count = 0
        self.last_error = None
        self.cooldown_until = None

    def mark_failed(self, status: APIKeyStatus, error: str) -> None:
        """Record a failure and apply a cooldown for transient errors."""
        self.status = status
        self.fail_count += 1
        self.last_error = error
        if status in (
            APIKeyStatus.RATE_LIMITED,
            APIKeyStatus.TIMEOUT,
            APIKeyStatus.SERVER_ERROR,
            APIKeyStatus.NETWORK_ERROR,
        ):
            self.cooldown_until = datetime.now() + TRANSIENT_FAILURE_COOLDOWN
        else:
            self.cooldown_until = None

    def is_usable(self) -> bool:
        """Whether this key can currently be selected for a new request."""
        if self.status == APIKeyStatus.INVALID:
            return False
        if self.status == APIKeyStatus.QUOTA_EXCEEDED:
            return False
        if self.cooldown_until is not None and datetime.now() < self.cooldown_until:
            return False
        return True

    def masked(self) -> str:
        """A safe, partially-masked representation for display/logging."""
        if len(self.value) <= 8:
            return "*" * len(self.value)
        return f"{self.value[:4]}{'*' * (len(self.value) - 8)}{self.value[-4:]}"
