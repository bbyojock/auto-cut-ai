"""Structured record of one AI provider request (Version 4, Features 7 & 9).

Populated by :class:`ai.edit_planner.EditPlanner` (and available to
:class:`services.chat_service.ChatService`) after every request, and kept by
:class:`services.ai_request_log_service.AIRequestLogService` for the Logs
page ("AI Logs") to display, filter, and copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class AIRequestLogEntry:
    """One AI request's full observability record.

    Every numeric field is best-effort: ``input_tokens``/``output_tokens``
    come from :class:`services.token_usage_service.TokenUsageService`'s
    heuristic estimator (providers used here don't return exact token
    counts), and ``estimated_cost_usd`` is ``None`` whenever the
    provider/model combination has no known pricing -- callers must never
    treat these as billing-accurate figures, only as a useful estimate.
    """

    task: str  # e.g. "edit_plan" or "chat"
    provider: str
    model: str
    started_at: datetime = field(default_factory=datetime.now)
    latency_seconds: float = 0.0
    streaming_seconds: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Optional[float] = None
    retry_count: int = 0
    finish_reason: str = "stop"
    success: bool = True
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "latency_seconds": round(self.latency_seconds, 3),
            "streaming_seconds": round(self.streaming_seconds, 3) if self.streaming_seconds is not None else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "retry_count": self.retry_count,
            "finish_reason": self.finish_reason,
            "success": self.success,
            "error": self.error,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_summary_line(self) -> str:
        """One-line, human-readable summary for the Logs page list view."""
        cost = f"${self.estimated_cost_usd:.5f}" if self.estimated_cost_usd is not None else "n/a"
        status = "OK" if self.success else f"FAILED ({self.error})"
        stream = f", stream={self.streaming_seconds:.2f}s" if self.streaming_seconds is not None else ""
        return (
            f"[{self.started_at.strftime('%Y-%m-%d %H:%M:%S')}] {self.task} | {self.provider}:{self.model} | "
            f"latency={self.latency_seconds:.2f}s{stream} | in={self.input_tokens} out={self.output_tokens} "
            f"tokens={self.total_tokens} cost={cost} | retries={self.retry_count} "
            f"finish={self.finish_reason} | {status}"
        )
