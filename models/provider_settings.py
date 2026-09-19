"""Independent, per-provider configuration (multi-provider architecture).

Each configured AI provider -- Gemini, OpenRouter, OpenAI, Claude, a local
OpenAI-compatible server, or any custom entry a user adds -- is one
:class:`ProviderSettings` instance. ``provider_type`` (not ``provider_id``)
decides which concrete :class:`ai.base_provider.AIProvider` implementation
:class:`ai.provider_factory.ProviderFactory` builds -- see
``utils.constants.AVAILABLE_PROVIDER_TYPES`` -- so adding a brand-new
*named* provider (e.g. "Groq") is just adding another ``ProviderSettings``
entry with ``provider_type="openai_compatible"`` and the right base URL; no
new provider class or ProviderFactory change is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from utils.constants import (
    DEFAULT_PROVIDER_MAX_RETRIES,
    DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    PROVIDER_TYPE_GEMINI,
)


@dataclass
class ProviderSettings:
    """Everything needed to construct and use one configured AI provider."""

    provider_id: str
    provider_type: str = PROVIDER_TYPE_GEMINI
    display_name: str = ""
    api_keys: List[str] = field(default_factory=list)
    base_url: str = ""
    model: str = ""
    enabled: bool = True
    timeout_seconds: int = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_PROVIDER_MAX_RETRIES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "display_name": self.display_name,
            "api_keys": list(self.api_keys),
            "base_url": self.base_url,
            "model": self.model,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderSettings":
        provider_type = data.get("provider_type", PROVIDER_TYPE_GEMINI)
        timeout_seconds = int(data.get("timeout_seconds", DEFAULT_PROVIDER_TIMEOUT_SECONDS))
        if provider_type == PROVIDER_TYPE_GEMINI and timeout_seconds < GEMINI_REQUEST_TIMEOUT_SECONDS:
            timeout_seconds = GEMINI_REQUEST_TIMEOUT_SECONDS
        return cls(
            provider_id=data["provider_id"],
            provider_type=provider_type,
            display_name=data.get("display_name", ""),
            api_keys=list(data.get("api_keys", [])),
            base_url=data.get("base_url", ""),
            model=data.get("model", ""),
            enabled=bool(data.get("enabled", True)),
            timeout_seconds=timeout_seconds,
            max_retries=int(data.get("max_retries", DEFAULT_PROVIDER_MAX_RETRIES)),
        )
