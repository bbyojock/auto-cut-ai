"""Result of testing whether a provider is reachable and usable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class ConnectionTestResult:
    """Outcome of :meth:`ai.base_provider.AIProvider.test_connection`.

    ``success`` is the only field a caller strictly needs; ``message`` is
    always a short, user-friendly sentence -- never a raw traceback -- fit
    to display directly in the Settings UI.
    """

    success: bool
    message: str
    latency_seconds: Optional[float] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    status_code: Optional[int] = None
    response: Optional[str] = None
    checked_at: datetime = None  # set by the caller/provider

    def __post_init__(self) -> None:
        if self.checked_at is None:
            self.checked_at = datetime.now()

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "latency_seconds": self.latency_seconds,
            "provider": self.provider,
            "model": self.model,
            "status_code": self.status_code,
            "response": self.response,
            "checked_at": self.checked_at.isoformat(),
        }
