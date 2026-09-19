"""Centralized logging setup for the whole application.

Every module should obtain its logger via :meth:`LoggingService.get_logger`
instead of calling ``logging.getLogger`` directly, so the naming stays
consistent (``AutoCutAI.<module>``) and handlers are only configured once.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from utils.constants import APP_NAME, LOG_FILE_NAME
from utils.file_utils import ensure_directory, get_project_root

_ROOT_LOGGER_NAME = APP_NAME
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class LoggingService:
    """Configures and hands out loggers for the application.

    This is a small, explicit class (not a hidden global) that is
    instantiated once in ``app.py`` and passed around / imported where
    needed via :meth:`get_logger`.
    """

    _configured: bool = False
    _log_path: Optional[Path] = None

    @classmethod
    def configure(cls, log_dir: Optional[Path] = None, level: int = logging.INFO) -> Path:
        """Set up the root ``AutoCutAI`` logger. Safe to call multiple times."""
        log_dir = log_dir or (get_project_root() / "logs")
        ensure_directory(log_dir)
        log_path = log_dir / LOG_FILE_NAME
        cls._log_path = log_path

        root_logger = logging.getLogger(_ROOT_LOGGER_NAME)
        if cls._configured:
            return log_path

        root_logger.setLevel(level)
        formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

        file_handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        root_logger.propagate = False
        cls._configured = True
        return log_path

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """Return a namespaced child logger, e.g. ``get_logger('ai.gemini')``."""
        if not cls._configured:
            cls.configure()
        return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")

    @classmethod
    def log_path(cls) -> Path:
        """Path to the active log file (configures with defaults if needed)."""
        if cls._log_path is None:
            cls.configure()
        return cls._log_path  # type: ignore[return-value]

    @staticmethod
    def log_user_action(logger: logging.Logger, action: str, **details: object) -> None:
        """Convenience helper for a consistently-formatted user action log line."""
        extra = " ".join(f"{key}={value}" for key, value in details.items())
        logger.info("USER_ACTION | %s | %s", action, extra)

    @staticmethod
    def log_api_request(logger: logging.Logger, provider: str, model: str, **details: object) -> None:
        """Convenience helper for a consistently-formatted API request log line."""
        extra = " ".join(f"{key}={value}" for key, value in details.items())
        logger.info("API_REQUEST | provider=%s model=%s | %s", provider, model, extra)

    @staticmethod
    def log_api_result(
        logger: logging.Logger,
        provider: str,
        model: str,
        latency_seconds: float,
        status_code: Optional[int] = None,
        response: Optional[str] = None,
    ) -> None:
        logger.info(
            "API_RESPONSE | provider=%s model=%s latency=%.3fs http_code=%s response=%s",
            provider, model, latency_seconds, status_code or "n/a", response or "<empty>",
        )

    @staticmethod
    def log_key_switch(logger: logging.Logger, reason: str, masked_key: str) -> None:
        """Convenience helper for a consistently-formatted key-rotation log line."""
        logger.warning("KEY_SWITCH | reason=%s | key=%s", reason, masked_key)
