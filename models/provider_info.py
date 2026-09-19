"""Describes one AI provider for registry/UI purposes (Version 3.5).

Lets a future Settings screen list every provider AutoCutAI knows about --
including ones with no concrete implementation yet -- without importing
provider classes that may not exist. See
:class:`ai.provider_factory.ProviderFactory`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ProviderInfo:
    """Metadata about one AI provider, available or merely planned."""

    provider_id: str
    display_name: str
    is_available: bool
    requires_api_key: bool = True
    requires_base_url: bool = False
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.provider_id,
            "display_name": self.display_name,
            "is_available": self.is_available,
            "requires_api_key": self.requires_api_key,
            "requires_base_url": self.requires_base_url,
            "description": self.description,
        }
