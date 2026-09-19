"""Factory that decouples provider *creation* from provider *usage*.

Adding a brand-new provider (OpenRouter, a local model, Claude, ...) is a
matter of writing a class that implements :class:`ai.base_provider.AIProvider`
and calling :meth:`ProviderFactory.register` once at startup -- no existing
call site has to change (Open/Closed Principle).

Multi-provider architecture update: :meth:`create_from_settings` builds a
provider purely from a :class:`models.provider_settings.ProviderSettings`
object, dispatching on ``provider_type`` via :meth:`register_type`. This
means *no* provider-specific branching exists anywhere outside the
provider classes themselves -- ``ProviderFactory`` never checks "is this
Gemini?" or "is this OpenRouter?"; it only ever asks "which constructor is
registered for this ``provider_type``?".
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ai.base_provider import AIProvider
from core.exceptions import ProviderConfigurationError, ProviderNotFoundError, ProviderNotImplementedError
from models.provider_info import ProviderInfo
from models.provider_settings import ProviderSettings

ProviderBuilder = Callable[[], AIProvider]
ProviderTypeConstructor = Callable[[ProviderSettings], AIProvider]


class ProviderFactory:
    """Registry + factory for :class:`AIProvider` implementations.

    Three registration styles coexist:

    - :meth:`register` -- a fully working provider with a zero-argument
      builder function, exactly as in earlier versions. :meth:`create`
      instantiates it normally. (Kept for backward compatibility.)
    - :meth:`register_planned` (Version 3.5) -- metadata for a provider
      AutoCutAI knows about but hasn't implemented yet. :meth:`create`
      raises a clear :class:`core.exceptions.ProviderNotImplementedError`
      for these.
    - :meth:`register_type` (multi-provider architecture) -- registers a
      *kind* of provider (``"gemini"``, ``"openai_compatible"``,
      ``"anthropic"``, ...) that :meth:`create_from_settings` can build
      for any number of differently-configured
      :class:`models.provider_settings.ProviderSettings` entries sharing
      that type.
    """

    def __init__(self) -> None:
        self._builders: Dict[str, ProviderBuilder] = {}
        self._info: Dict[str, ProviderInfo] = {}
        self._type_constructors: Dict[str, ProviderTypeConstructor] = {}

    def register(
        self,
        provider_id: str,
        builder: ProviderBuilder,
        display_name: Optional[str] = None,
        requires_api_key: bool = True,
        requires_base_url: bool = False,
        description: str = "",
    ) -> None:
        """Register a zero-argument builder function under ``provider_id``.

        Only ``provider_id`` and ``builder`` are required -- every other
        parameter is optional metadata, so every existing 2-argument call
        site keeps working unchanged.
        """
        self._builders[provider_id] = builder
        self._info[provider_id] = ProviderInfo(
            provider_id=provider_id,
            display_name=display_name or provider_id.replace("_", " ").title(),
            is_available=True,
            requires_api_key=requires_api_key,
            requires_base_url=requires_base_url,
            description=description,
        )

    def register_planned(
        self,
        provider_id: str,
        display_name: str,
        requires_api_key: bool = True,
        requires_base_url: bool = False,
        description: str = "",
    ) -> None:
        """Register a provider AutoCutAI knows about but hasn't implemented yet.

        Does nothing if ``provider_id`` is already registered (planned or
        real) -- an already-working provider is never downgraded to
        "planned" by a stray call.
        """
        if provider_id in self._info:
            return
        self._info[provider_id] = ProviderInfo(
            provider_id=provider_id,
            display_name=display_name,
            is_available=False,
            requires_api_key=requires_api_key,
            requires_base_url=requires_base_url,
            description=description,
        )

    def register_type(self, provider_type: str, constructor: ProviderTypeConstructor) -> None:
        """Register how to build an :class:`AIProvider` for a ``provider_type``.

        ``constructor`` receives one :class:`ProviderSettings` and must
        return a ready-to-use :class:`AIProvider`. Registering a new
        ``provider_type`` here is the *only* place new provider
        implementations need to be wired in; everything downstream
        (:meth:`create_from_settings`, ``EditPlanner``, ``ChatService``)
        works unchanged.
        """
        self._type_constructors[provider_type] = constructor

    def create_from_settings(self, settings: ProviderSettings) -> AIProvider:
        """Build an :class:`AIProvider` purely from a :class:`ProviderSettings`.

        Raises:
            ProviderConfigurationError: if ``settings.provider_type`` has
                no registered constructor, or the constructor itself
                rejects the settings (e.g. missing base URL).
        """
        constructor = self._type_constructors.get(settings.provider_type)
        if constructor is None:
            raise ProviderConfigurationError(
                f"No provider implementation registered for provider_type='{settings.provider_type}' "
                f"(provider_id='{settings.provider_id}')."
            )
        try:
            return constructor(settings)
        except ProviderConfigurationError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear configuration error
            raise ProviderConfigurationError(
                f"Could not build provider '{settings.provider_id}': {exc}"
            ) from exc

    def create(self, provider_id: str) -> AIProvider:
        """Instantiate the provider registered under ``provider_id`` (legacy path)."""
        builder = self._builders.get(provider_id)
        if builder is None:
            info = self._info.get(provider_id)
            if info is not None and not info.is_available:
                raise ProviderNotImplementedError(
                    f"Provider '{provider_id}' ({info.display_name}) is on the roadmap "
                    "but has no working implementation yet."
                )
            raise ProviderNotFoundError(f"No AI provider registered as '{provider_id}'.")
        return builder()

    def available_providers(self) -> List[str]:
        """Return the ids of every provider with a working implementation."""
        return list(self._builders.keys())

    def list_providers(self) -> List[ProviderInfo]:
        """Return info for every known provider, available or merely planned."""
        return list(self._info.values())
