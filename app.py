"""AutoCutAI application entry point (composition root).

This module is intentionally the *only* place that constructs concrete
service/manager instances and wires them together. Every other module
receives its collaborators through constructor parameters (see
:class:`core.app_context.AppContext`), which keeps the codebase easy to
test and easy to extend with new AI providers, pages or persistence
backends without touching unrelated code.

Run with:

    python app.py
"""

from __future__ import annotations

import sys
import tkinter.messagebox as messagebox

import customtkinter as ctk

from ai.claude_provider import ClaudeProvider
from ai.edit_planner import EditPlanner
from ai.gemini_provider import GeminiProvider
from ai.key_manager import APIKeyManager
from ai.openai_compatible_provider import OpenAICompatibleProvider
from ai.provider_factory import ProviderFactory
from config.config_manager import ConfigManager
from core.app_context import AppContext
from core.exceptions import AutoCutAIError, ProviderConfigurationError
from core.session import ConversationSession
from models.provider_settings import ProviderSettings
from services.ai_request_log_service import AIRequestLogService
from services.chat_service import ChatService
from services.edit_generation_service import EditGenerationService
from services.edit_plan_cache_service import EditPlanCacheService
from services.edit_plan_io_service import EditPlanIOService
from services.logging_service import LoggingService
from utils.constants import (
    PROVIDER_GROQ,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_REQUIRES_API_KEY,
    PROVIDER_TYPE_ANTHROPIC,
    PROVIDER_TYPE_GEMINI,
    PROVIDER_TYPE_OPENAI_COMPATIBLE,
)


class AutoCutAIApp:
    """Builds the dependency graph and owns the application's lifecycle."""

    def __init__(self) -> None:
        self._logger = LoggingService.get_logger("app")

    def run(self) -> None:
        """Load configuration, assemble services, and start the GUI loop."""
        try:
            context = self._build_context()
        except AutoCutAIError as exc:
            self._logger.exception("Fatal error while starting AutoCutAI")
            self._show_fatal_error(str(exc))
            sys.exit(1)

        self._apply_appearance(context)

        # Local import avoids a circular dependency: ui.main_window imports
        # ui.pages.*, which in turn import core.app_context.
        from ui.main_window import MainWindow

        window = MainWindow(context)
        window.mainloop()

    def _build_context(self) -> AppContext:
        """Construct every collaborator and bundle them into an AppContext."""
        config_manager = ConfigManager()
        config = config_manager.load()

        provider_factory = self._build_provider_factory()

        provider, provider_settings = self._activate_provider(config, provider_factory)

        # Conversation memory is deliberately created fresh every run and
        # never persisted -- see core/session.py for the rationale.
        session = ConversationSession()

        # Multi-provider architecture: ChatService and EditPlanner both
        # read their model from *the same* ProviderSettings entry
        # (provider_settings.model) -- there is exactly one place a model
        # name is configured for whichever provider is active, and it is
        # always free text from Settings, never a hardcoded string.
        chat_service = ChatService(
            provider=provider,
            session=session,
            model=provider_settings.model,
        )

        edit_planner = EditPlanner(
            provider=provider,
            model=provider_settings.model,
        )

        # Version 4: structured AI request logging, EditPlan caching, and
        # the orchestrator that ties the analysis pipeline + cache + AI
        # planner together for the AI Editor page. All log/cache to the
        # standard project ``logs/``/``cache/`` directories unless told
        # otherwise -- never hardcoded elsewhere.
        ai_request_log_service = AIRequestLogService()
        edit_plan_cache_service = EditPlanCacheService()
        edit_plan_io_service = EditPlanIOService()
        edit_generation_service = EditGenerationService(
            edit_planner=edit_planner,
            cache_service=edit_plan_cache_service,
            ai_request_log_service=ai_request_log_service,
        )

        # Reuse the provider's own key manager instance (every provider
        # implemented so far exposes one) instead of building a second,
        # separately-tracked one -- otherwise a key's health (rate
        # limited/invalid/...) would drift out of sync between what the
        # provider actually uses and what Home/Settings display.
        key_manager = getattr(provider, "key_manager", None) or APIKeyManager(provider_settings.api_keys)

        self._logger.info(
            "AutoCutAI started (provider=%s [%s], model=%s, keys=%d)",
            provider.name,
            provider_settings.provider_id,
            provider_settings.model,
            len(provider_settings.api_keys),
        )

        return AppContext(
            config_manager=config_manager,
            provider_factory=provider_factory,
            key_manager=key_manager,
            session=session,
            chat_service=chat_service,
            edit_planner=edit_planner,
            ai_request_log_service=ai_request_log_service,
            edit_plan_cache_service=edit_plan_cache_service,
            edit_plan_io_service=edit_plan_io_service,
            edit_generation_service=edit_generation_service,
        )

    def _build_provider_factory(self) -> ProviderFactory:
        """Register how to build each *type* of provider, and any not-yet-implemented ones.

        No provider-specific branching happens outside of this registration
        step and the provider classes themselves -- everything else in the
        app just asks the factory for "the provider for this ProviderSettings".
        """
        provider_factory = ProviderFactory()

        provider_factory.register_type(
            PROVIDER_TYPE_GEMINI,
            lambda settings: GeminiProvider(
                APIKeyManager(settings.api_keys),
                base_url=settings.base_url or None,
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )
        provider_factory.register_type(
            PROVIDER_TYPE_OPENAI_COMPATIBLE,
            lambda settings: OpenAICompatibleProvider(
                display_name=settings.display_name or settings.provider_id.title(),
                base_url=settings.base_url,
                key_manager=APIKeyManager(settings.api_keys),
                requires_api_key=PROVIDER_REQUIRES_API_KEY.get(settings.provider_id, True),
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )
        provider_factory.register_type(
            PROVIDER_TYPE_ANTHROPIC,
            lambda settings: ClaudeProvider(
                base_url=settings.base_url,
                key_manager=APIKeyManager(settings.api_keys),
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )

        # Metadata-only entries: known providers with no dedicated,
        # separately-branded implementation yet. Both are already fully
        # usable today as a custom "openai_compatible" ProviderSettings
        # entry pointed at the right base URL (Groq: an OpenAI-compatible
        # endpoint; "openai_compatible" itself is the generic custom
        # option) -- these are just discoverability metadata.
        provider_factory.register_planned(
            PROVIDER_GROQ, "Groq",
            description="Ultra-fast inference for open models (OpenAI-compatible endpoint).",
        )
        provider_factory.register_planned(
            PROVIDER_OPENAI_COMPATIBLE, "Custom OpenAI-compatible API",
            requires_base_url=True,
            description="Add any other OpenAI-compatible endpoint as a custom provider.",
        )

        return provider_factory

    def _activate_provider(self, config, provider_factory: ProviderFactory):
        """Pick and instantiate the provider Chat/EditPlanner should use.

        Prefers ``config.last_used.provider`` if it's enabled and
        configured; otherwise falls back to the first enabled provider.
        Raises a clear, user-facing error if nothing is usable yet (e.g. a
        brand-new install before Settings has been touched) instead of
        crashing with an obscure stack trace.
        """
        candidates = []
        preferred = config.get_provider(config.last_used.provider)
        if preferred is not None and preferred.enabled:
            candidates.append(preferred)
        candidates.extend(p for p in config.get_enabled_providers() if p not in candidates)

        last_error: Exception = ProviderConfigurationError(
            "No AI provider is enabled yet. Open Settings, add at least one API key "
            "to a provider (e.g. Gemini), and make sure it's enabled."
        )
        for settings in candidates:
            try:
                provider = provider_factory.create_from_settings(settings)
                # Every provider gets registered under the legacy
                # (provider_id -> zero-arg builder) API too, so anything
                # still using ProviderFactory.create()/available_providers()
                # keeps working unchanged.
                provider_factory.register(
                    settings.provider_id,
                    (lambda instance: (lambda: instance))(provider),
                    display_name=settings.display_name or provider.name,
                    requires_api_key=provider.requires_api_key,
                    requires_base_url=provider.requires_base_url,
                )
                return provider, settings
            except AutoCutAIError as exc:
                self._logger.warning("Could not activate provider '%s': %s", settings.provider_id, exc)
                last_error = exc
                continue

        raise last_error

    @staticmethod
    def _apply_appearance(context: AppContext) -> None:
        """Apply the persisted theme/color-theme before the window is built."""
        config = context.config_manager.config
        ctk.set_appearance_mode(config.theme)
        ctk.set_default_color_theme(config.color_theme)

    @staticmethod
    def _show_fatal_error(message: str) -> None:
        """Best-effort GUI error dialog for failures during startup."""
        try:
            root = ctk.CTk()
            root.withdraw()
            messagebox.showerror("AutoCutAI - Startup Error", message)
            root.destroy()
        except Exception:  # noqa: BLE001 - fall back to stderr if Tk itself fails
            print(f"AutoCutAI failed to start: {message}", file=sys.stderr)


def main() -> None:
    """Module-level convenience entry point (``python app.py``)."""
    AutoCutAIApp().run()


if __name__ == "__main__":
    main()
