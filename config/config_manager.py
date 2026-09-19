"""Reads and writes the application's persisted configuration file."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from config.settings import AppConfig
from core.exceptions import ConfigurationError
from utils.constants import CONFIG_FILE_NAME
from utils.file_utils import ensure_directory, get_project_root


class ConfigManager:
    """Owns the lifecycle of :class:`config.settings.AppConfig`.

    Responsibilities are intentionally narrow: load from disk (creating
    sensible defaults on first run), save back to disk, and hand out the
    current in-memory config. Nothing else in the app should read/write the
    config JSON file directly.
    """

    def __init__(self, config_path: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        self._config_path = config_path or (get_project_root() / "config" / CONFIG_FILE_NAME)
        self._logger = logger or logging.getLogger("AutoCutAI.config")
        self._config: AppConfig = AppConfig()

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def config(self) -> AppConfig:
        """The currently loaded configuration object."""
        return self._config

    def load(self) -> AppConfig:
        """Load configuration from disk, creating defaults if needed."""
        ensure_directory(self._config_path.parent)
        if not self._config_path.exists():
            self._logger.info("No existing config found, creating defaults at %s", self._config_path)
            self._config = AppConfig()
            self.save()
            return self._config

        try:
            raw_text = self._config_path.read_text(encoding="utf-8")
            data = json.loads(raw_text) if raw_text.strip() else {}
            self._config = AppConfig.from_dict(data)
            self._logger.info("Configuration loaded from %s", self._config_path)
        except (OSError, json.JSONDecodeError) as exc:
            self._logger.error("Failed to load configuration: %s", exc)
            raise ConfigurationError(f"Could not read configuration file: {exc}") from exc

        return self._config

    def save(self) -> None:
        """Persist the current in-memory configuration to disk."""
        ensure_directory(self._config_path.parent)
        try:
            self._config_path.write_text(
                json.dumps(self._config.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._logger.info("Configuration saved to %s", self._config_path)
        except OSError as exc:
            self._logger.error("Failed to save configuration: %s", exc)
            raise ConfigurationError(f"Could not write configuration file: {exc}") from exc

    def replace(self, new_config: AppConfig) -> None:
        """Replace the entire in-memory configuration (does not save automatically)."""
        self._config = new_config
