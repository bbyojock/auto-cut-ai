"""Keeps a rolling history of AI provider requests for the Logs page.

Separate from :class:`services.logging_service.LoggingService` (which
handles free-text application logging to ``logs/app.log``): this service
holds *structured* :class:`models.ai_request_log.AIRequestLogEntry`
records -- provider, model, latency, token counts, cost, retries,
streaming time, finish reason -- so the "AI Logs" view (Feature 7) can
render a proper table/list instead of grepping text, and so entries can be
copied as clean JSON (Feature 9). Every entry is also mirrored to the
regular text log via :meth:`services.logging_service.LoggingService.log_api_result`-style
lines, so ``logs/app.log`` alone remains a complete record even if the
structured file is ever lost.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import List, Optional

from models.ai_request_log import AIRequestLogEntry
from services.logging_service import LoggingService
from utils.file_utils import ensure_directory, get_project_root

_LOG_FILE_NAME = "ai_requests.jsonl"
_MAX_IN_MEMORY_ENTRIES = 500


class AIRequestLogService:
    """Thread-safe in-memory ring buffer of AI requests, persisted as JSONL.

    A single instance is shared across the app (constructed once in
    ``app.py`` and handed out via :class:`core.app_context.AppContext`,
    exactly like every other service) so every page always sees the same
    history regardless of which page triggered a given request.
    """

    def __init__(self, log_dir: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("services.ai_request_log")
        self._lock = threading.Lock()
        self._entries: List[AIRequestLogEntry] = []
        log_dir = log_dir or (get_project_root() / "logs")
        ensure_directory(log_dir)
        self._log_path = log_dir / _LOG_FILE_NAME
        self._load_existing()

    @property
    def log_path(self) -> Path:
        return self._log_path

    def record(self, entry: AIRequestLogEntry) -> None:
        """Add ``entry`` to the in-memory history and append it to disk."""
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > _MAX_IN_MEMORY_ENTRIES:
                self._entries = self._entries[-_MAX_IN_MEMORY_ENTRIES:]
        self._append_to_disk(entry)
        self._logger.info("AI_REQUEST_LOG | %s", entry.to_summary_line())

    def entries(self) -> List[AIRequestLogEntry]:
        """A snapshot list, most recent last."""
        with self._lock:
            return list(self._entries)

    def latest(self) -> Optional[AIRequestLogEntry]:
        with self._lock:
            return self._entries[-1] if self._entries else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def to_copyable_text(self) -> str:
        """Every entry, one JSON object per line -- ready for the clipboard."""
        return "\n".join(json.dumps(entry.to_dict(), ensure_ascii=False) for entry in self.entries())

    def _append_to_disk(self, entry: AIRequestLogEntry) -> None:
        try:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            self._logger.exception("Failed to persist AI request log entry to disk (non-fatal).")

    def _load_existing(self) -> None:
        """Best-effort restore of recent entries from a previous session."""
        if not self._log_path.exists():
            return
        try:
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-_MAX_IN_MEMORY_ENTRIES:]:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                self._entries.append(_entry_from_dict(data))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue


def _entry_from_dict(data: dict) -> AIRequestLogEntry:
    from datetime import datetime

    return AIRequestLogEntry(
        task=data.get("task", ""),
        provider=data.get("provider", ""),
        model=data.get("model", ""),
        started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else datetime.now(),
        latency_seconds=float(data.get("latency_seconds", 0.0)),
        streaming_seconds=data.get("streaming_seconds"),
        input_tokens=int(data.get("input_tokens", 0)),
        output_tokens=int(data.get("output_tokens", 0)),
        estimated_cost_usd=data.get("estimated_cost_usd"),
        retry_count=int(data.get("retry_count", 0)),
        finish_reason=data.get("finish_reason", "stop"),
        success=bool(data.get("success", True)),
        error=data.get("error"),
    )
