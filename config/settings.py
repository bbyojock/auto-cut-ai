"""Typed schema for everything AutoCutAI persists to disk.

Each dataclass knows how to convert itself to/from a plain ``dict`` so the
:class:`config.config_manager.ConfigManager` can serialize the whole tree to
JSON without any section needing to know about JSON itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from models.provider_settings import ProviderSettings
from utils.constants import (
    BUILTIN_PROVIDER_IDS,
    BUILTIN_PROVIDER_TYPES,
    DEFAULT_COLOR_THEME,
    DEFAULT_FRAME_INTERVAL_SECONDS,
    DEFAULT_GEMINI_MODEL,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_PROVIDER,
    DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    DEFAULT_PROVIDER_BASE_URLS,
    DEFAULT_PROVIDER_MODEL_HINTS,
    DEFAULT_PROMPT_STYLE,
    DEFAULT_THEME,
    DEFAULT_WHISPER_BACKEND,
    DEFAULT_WHISPER_MODEL_SIZE,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    OUTDATED_MODEL_REPLACEMENTS,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_GEMINI,
    PROVIDER_TYPE_GEMINI,
)


@dataclass
class GeminiSettings:
    """Legacy (pre-multi-provider) Gemini-only settings.

    Kept purely for backward compatibility: an old ``config.json`` written
    by Version 1-3.5 still has a top-level ``"gemini"`` key, and
    :meth:`AppConfig.from_dict` reads it once to seed the new
    ``providers`` list the first time an old config is loaded (see
    :func:`_migrate_legacy_gemini_settings`). Nothing in the application
    reads this field going forward -- ``providers`` is the single source
    of truth now.
    """

    api_keys: List[str] = field(default_factory=list)
    selected_model: str = DEFAULT_GEMINI_MODEL

    def to_dict(self) -> Dict[str, Any]:
        return {"api_keys": list(self.api_keys), "selected_model": self.selected_model}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GeminiSettings":
        return cls(
            api_keys=list(data.get("api_keys", [])),
            selected_model=data.get("selected_model", DEFAULT_GEMINI_MODEL),
        )


@dataclass
class WhisperSettings:
    """Whisper transcription preferences.

    ``model_size`` is a free-text field on purpose (tiny/base/small/medium/
    large-v3, or any future model faster-whisper adds) -- never a fixed
    dropdown -- so a new Whisper release works without an AutoCutAI update.
    """

    backend: str = DEFAULT_WHISPER_BACKEND
    model_size: str = DEFAULT_WHISPER_MODEL_SIZE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WhisperSettings":
        return cls(
            backend=data.get("backend", DEFAULT_WHISPER_BACKEND),
            model_size=data.get("model_size", DEFAULT_WHISPER_MODEL_SIZE),
        )


@dataclass
class WindowSettings:
    """Window geometry persisted between sessions."""

    width: int = DEFAULT_WINDOW_WIDTH
    height: int = DEFAULT_WINDOW_HEIGHT

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowSettings":
        return cls(
            width=int(data.get("width", DEFAULT_WINDOW_WIDTH)),
            height=int(data.get("height", DEFAULT_WINDOW_HEIGHT)),
        )


@dataclass
class LastUsedSettings:
    """The last provider/model/page the user was working with."""

    provider: str = DEFAULT_PROVIDER
    page: str = "home"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LastUsedSettings":
        return cls(
            provider=data.get("provider", DEFAULT_PROVIDER),
            page=data.get("page", "home"),
        )


def _default_provider_settings() -> List[ProviderSettings]:
    """Build the standard, freshly-installed provider list.

    Gemini is enabled out of the box (matching pre-multi-provider
    behavior); every other builtin provider is pre-created so it shows up
    in Settings immediately, but stays disabled and keyless until the user
    fills it in.
    """
    providers = []
    for provider_id in BUILTIN_PROVIDER_IDS:
        providers.append(
            ProviderSettings(
                provider_id=provider_id,
                provider_type=BUILTIN_PROVIDER_TYPES[provider_id],
                display_name=PROVIDER_DISPLAY_NAMES.get(provider_id, provider_id.title()),
                api_keys=[],
                base_url=DEFAULT_PROVIDER_BASE_URLS.get(provider_id, ""),
                model=DEFAULT_PROVIDER_MODEL_HINTS.get(provider_id, ""),
                enabled=(provider_id == PROVIDER_GEMINI),
                timeout_seconds=(
                    GEMINI_REQUEST_TIMEOUT_SECONDS
                    if provider_id == PROVIDER_GEMINI
                    else DEFAULT_PROVIDER_TIMEOUT_SECONDS
                ),
            )
        )
    return providers


def _migrate_legacy_gemini_settings(gemini: GeminiSettings, providers: List[ProviderSettings]) -> None:
    """Fold an old top-level ``gemini`` section into the new ``providers`` list.

    Only runs when the config file predates the multi-provider
    architecture (no ``providers`` key at all); once migrated, the config
    is saved with a real ``providers`` array and this never runs again for
    that file. The user's existing keys and model selection are preserved
    exactly -- this is additive, never destructive.
    """
    for provider in providers:
        if provider.provider_id == PROVIDER_GEMINI:
            if gemini.api_keys:
                provider.api_keys = list(gemini.api_keys)
            if gemini.selected_model:
                provider.model = gemini.selected_model
            provider.enabled = True
            return

    providers.append(
        ProviderSettings(
            provider_id=PROVIDER_GEMINI,
            provider_type=PROVIDER_TYPE_GEMINI,
            display_name=PROVIDER_DISPLAY_NAMES.get(PROVIDER_GEMINI, "Gemini"),
            api_keys=list(gemini.api_keys),
            base_url=DEFAULT_PROVIDER_BASE_URLS.get(PROVIDER_GEMINI, ""),
            model=gemini.selected_model or DEFAULT_GEMINI_MODEL,
            enabled=True,
        )
    )


def suggest_model_migration(provider_type: str, model: str) -> Optional[str]:
    """Return a suggested replacement if ``model`` is known to be outdated.

    Currently only Gemini model names are tracked (see
    ``utils.constants.OUTDATED_MODEL_REPLACEMENTS``); returns ``None`` for
    anything not on that list, including every non-Gemini provider, since
    "outdated" isn't a concept AutoCutAI can know about for free-text
    third-party model names.
    """
    if provider_type != PROVIDER_TYPE_GEMINI:
        return None
    return OUTDATED_MODEL_REPLACEMENTS.get(model)


def migrate_gemini_model(provider_type: str, model: str) -> str:
    """Return the current Gemini model while preserving all other values."""
    if provider_type != PROVIDER_TYPE_GEMINI:
        return model
    return OUTDATED_MODEL_REPLACEMENTS.get(model, model or DEFAULT_GEMINI_MODEL)


@dataclass
class AppConfig:
    """Root configuration object, mirrors the on-disk JSON structure."""

    theme: str = DEFAULT_THEME
    color_theme: str = DEFAULT_COLOR_THEME
    window: WindowSettings = field(default_factory=WindowSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    last_used: LastUsedSettings = field(default_factory=LastUsedSettings)
    providers: List[ProviderSettings] = field(default_factory=_default_provider_settings)
    whisper: WhisperSettings = field(default_factory=WhisperSettings)
    frame_interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS
    prompt_style: str = DEFAULT_PROMPT_STYLE
    game_profile: str = "none"

    # ------------------------------------------------------------------
    # Provider helpers -- the UI and app.py go through these rather than
    # manipulating ``providers`` directly, so list-shape invariants (no
    # duplicate ids) live in exactly one place.
    # ------------------------------------------------------------------
    def get_provider(self, provider_id: str) -> Optional[ProviderSettings]:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider
        return None

    def get_enabled_providers(self) -> List[ProviderSettings]:
        return [provider for provider in self.providers if provider.enabled]

    def add_provider(self, provider: ProviderSettings) -> None:
        if self.get_provider(provider.provider_id) is not None:
            raise ValueError(f"A provider with id '{provider.provider_id}' already exists.")
        self.providers.append(provider)

    def remove_provider(self, provider_id: str) -> None:
        self.providers = [provider for provider in self.providers if provider.provider_id != provider_id]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theme": self.theme,
            "color_theme": self.color_theme,
            "window": self.window.to_dict(),
            "gemini": self.gemini.to_dict(),
            "last_used": self.last_used.to_dict(),
            "providers": [provider.to_dict() for provider in self.providers],
            "whisper": self.whisper.to_dict(),
            "frame_interval_seconds": self.frame_interval_seconds,
            "prompt_style": self.prompt_style,
            "game_profile": self.game_profile,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        gemini = GeminiSettings.from_dict(data.get("gemini", {}))
        raw_providers = data.get("providers")

        if raw_providers:
            providers = [ProviderSettings.from_dict(entry) for entry in raw_providers]
        else:
            # No "providers" key at all: either a brand-new install, or an
            # old (pre-multi-provider) config file. Start from the default
            # set and, if this looks like an old file (it has real Gemini
            # settings), migrate them in -- see _migrate_legacy_gemini_settings.
            providers = _default_provider_settings()
            if gemini.api_keys or gemini.selected_model != DEFAULT_GEMINI_MODEL:
                _migrate_legacy_gemini_settings(gemini, providers)

        gemini.selected_model = migrate_gemini_model(PROVIDER_TYPE_GEMINI, gemini.selected_model)
        for provider in providers:
            provider.model = migrate_gemini_model(provider.provider_type, provider.model)
            if provider.provider_type == PROVIDER_TYPE_GEMINI:
                provider.timeout_seconds = max(provider.timeout_seconds, GEMINI_REQUEST_TIMEOUT_SECONDS)

        return cls(
            theme=data.get("theme", DEFAULT_THEME),
            color_theme=data.get("color_theme", DEFAULT_COLOR_THEME),
            window=WindowSettings.from_dict(data.get("window", {})),
            gemini=gemini,
            last_used=LastUsedSettings.from_dict(data.get("last_used", {})),
            providers=providers,
            whisper=WhisperSettings.from_dict(data.get("whisper", {})),
            frame_interval_seconds=float(data.get("frame_interval_seconds", DEFAULT_FRAME_INTERVAL_SECONDS)),
            prompt_style=data.get("prompt_style", DEFAULT_PROMPT_STYLE),
            game_profile=data.get("game_profile", "none"),
        )
