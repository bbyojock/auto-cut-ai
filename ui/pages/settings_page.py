"""Settings page: manage every AI provider, Whisper backend, and appearance.

Every AI model name and Base URL on this page is a plain editable text
field -- never a fixed dropdown -- so a brand-new model release or a
custom endpoint always works without an AutoCutAI update.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional
from urllib.parse import urlparse

import customtkinter as ctk
from tkinter import messagebox

from config.settings import migrate_gemini_model, suggest_model_migration
from models.provider_settings import ProviderSettings
from services.logging_service import LoggingService
from ui.pages.base_page import BasePage
from utils.constants import (
    AVAILABLE_PROVIDER_TYPES,
    AVAILABLE_THEMES,
    AVAILABLE_WHISPER_BACKENDS,
    DEFAULT_PROVIDER_BASE_URLS,
    DEFAULT_PROVIDER_MODEL_HINTS,
    DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_TYPE_GEMINI,
)

_LOGGER = LoggingService.get_logger("ui.settings_page")


class _ProviderCardWidgets:
    """Live widget/variable references for one provider card.

    Grouped in one place so :class:`SettingsPage` can read every field
    back out at Save time without a wall of individual attributes.
    """

    def __init__(self) -> None:
        self.enabled_var: Optional[ctk.BooleanVar] = None
        self.display_name_var: Optional[ctk.StringVar] = None
        self.base_url_var: Optional[ctk.StringVar] = None
        self.model_var: Optional[ctk.StringVar] = None
        self.timeout_var: Optional[ctk.StringVar] = None
        self.max_retries_var: Optional[ctk.StringVar] = None
        self.keys_frame: Optional[ctk.CTkBaseClass] = None
        self.test_label: Optional[ctk.CTkLabel] = None
        self.test_button: Optional[ctk.CTkButton] = None
        self.migration_frame: Optional[ctk.CTkBaseClass] = None


class SettingsPage(BasePage):
    """Lets the user manage every AI provider, Whisper backend, and appearance."""

    def build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._provider_widgets: Dict[str, _ProviderCardWidgets] = {}
        self._active_provider_var = ctk.StringVar(
            value=self.context.config_manager.config.last_used.provider
        )

        header = ctk.CTkLabel(self, text="Settings", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, padx=30, pady=(30, 10), sticky="w")

        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.grid(row=1, column=0, padx=30, pady=(0, 20), sticky="nsew")
        self._body.grid_columnconfigure(0, weight=1)

        self._providers_section = ctk.CTkFrame(self._body, fg_color="transparent")
        self._providers_section.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        self._providers_section.grid_columnconfigure(0, weight=1)

        self._build_whisper_section(self._body, row=1)
        self._build_theme_section(self._body, row=2)

        save_button = ctk.CTkButton(self, text="Save Settings", command=self._save_settings)
        save_button.grid(row=2, column=0, padx=30, pady=(0, 30), sticky="e")

        self._refresh_providers_section()

    def on_show(self) -> None:
        self._refresh_providers_section()

    # ------------------------------------------------------------------
    # AI Providers
    # ------------------------------------------------------------------
    def _refresh_providers_section(self) -> None:
        for child in self._providers_section.winfo_children():
            child.destroy()
        self._provider_widgets.clear()

        title = ctk.CTkLabel(self._providers_section, text="AI Providers", font=ctk.CTkFont(size=16, weight="bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 4))

        description = ctk.CTkLabel(
            self._providers_section,
            text="Configure as many providers as you like. Pick one active provider below "
                 "for Chat and the AI Editor to use. Base URL and Model are always free text.",
            text_color="gray60", justify="left",
        )
        description.grid(row=1, column=0, sticky="w", pady=(0, 10))

        config = self.context.config_manager.config
        for index, provider_settings in enumerate(config.providers):
            self._build_provider_card(self._providers_section, provider_settings, row=2 + index)

        add_row = 2 + len(config.providers)
        add_frame = ctk.CTkFrame(self._providers_section, fg_color="transparent")
        add_frame.grid(row=add_row, column=0, sticky="ew", pady=(4, 0))
        add_button = ctk.CTkButton(add_frame, text="+ Add Provider", command=self._open_add_provider_dialog)
        add_button.pack(anchor="w")

    def _build_provider_card(self, parent, settings: ProviderSettings, row: int) -> None:
        widgets = _ProviderCardWidgets()
        self._provider_widgets[settings.provider_id] = widgets

        card = ctk.CTkFrame(parent)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(1, weight=1)

        # --- Header row: active radio, enabled switch, display name, delete ---
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 4))
        header.grid_columnconfigure(1, weight=1)

        active_radio = ctk.CTkRadioButton(
            header, text="Active", variable=self._active_provider_var, value=settings.provider_id, width=1,
        )
        active_radio.grid(row=0, column=0, padx=(0, 12))

        widgets.display_name_var = ctk.StringVar(value=settings.display_name or settings.provider_id.title())
        name_entry = ctk.CTkEntry(header, textvariable=widgets.display_name_var, font=ctk.CTkFont(weight="bold"))
        name_entry.grid(row=0, column=1, sticky="ew", padx=(0, 12))

        widgets.enabled_var = ctk.BooleanVar(value=settings.enabled)
        enabled_switch = ctk.CTkSwitch(header, text="Enabled", variable=widgets.enabled_var)
        enabled_switch.grid(row=0, column=2, padx=(0, 12))

        delete_button = ctk.CTkButton(
            header, text="Delete", width=70, fg_color="#8b2e2e", hover_color="#6e2424",
            command=lambda pid=settings.provider_id: self._delete_provider(pid),
        )
        delete_button.grid(row=0, column=3)

        type_label = ctk.CTkLabel(
            card, text=f"id: {settings.provider_id}  ·  type: {settings.provider_type}", text_color="gray60"
        )
        type_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 8))

        # --- Migration banner (only if this provider's model is outdated) ---
        suggestion = suggest_model_migration(settings.provider_type, settings.model)
        if suggestion:
            widgets.migration_frame = ctk.CTkFrame(card, fg_color="#4a3b14")
            widgets.migration_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))
            msg = ctk.CTkLabel(
                widgets.migration_frame,
                text=f"'{settings.model}' looks outdated. Suggested: '{suggestion}'.",
                text_color="#f5d36b",
            )
            msg.pack(side="left", padx=12, pady=8)
            migrate_button = ctk.CTkButton(
                widgets.migration_frame, text="Use suggested model", width=160,
                command=lambda pid=settings.provider_id, s=suggestion: self._apply_model_suggestion(pid, s),
            )
            migrate_button.pack(side="right", padx=12, pady=8)

        # --- Base URL / Model fields ---
        fields = ctk.CTkFrame(card, fg_color="transparent")
        fields.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))
        fields.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(fields, text="Base URL", width=90, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        widgets.base_url_var = ctk.StringVar(value=settings.base_url)
        ctk.CTkEntry(
            fields, textvariable=widgets.base_url_var,
            placeholder_text=DEFAULT_PROVIDER_BASE_URLS.get(settings.provider_id, "https://..."),
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)

        ctk.CTkLabel(fields, text="Model", width=90, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        widgets.model_var = ctk.StringVar(value=settings.model)
        ctk.CTkEntry(
            fields, textvariable=widgets.model_var,
            placeholder_text=DEFAULT_PROVIDER_MODEL_HINTS.get(settings.provider_id, "any model name..."),
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)

        ctk.CTkLabel(fields, text="Request timeout", width=90, anchor="w").grid(
            row=2, column=0, sticky="w", pady=4
        )
        widgets.timeout_var = ctk.StringVar(value=str(settings.timeout_seconds))
        ctk.CTkEntry(
            fields, textvariable=widgets.timeout_var, placeholder_text="seconds",
        ).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=4)
        ctk.CTkLabel(
            fields,
            text="Gemini uses this for edit planning; Test Connection always uses 30 seconds.",
            text_color="gray60",
        ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        ctk.CTkLabel(fields, text="Max retries", width=90, anchor="w").grid(
            row=4, column=0, sticky="w", pady=4
        )
        widgets.max_retries_var = ctk.StringVar(value=str(settings.max_retries))
        ctk.CTkEntry(
            fields, textvariable=widgets.max_retries_var, placeholder_text="attempts per key",
        ).grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=4)
        ctk.CTkLabel(
            fields,
            text="How many times to retry one API key on a rate limit/timeout before rotating to the next key.",
            text_color="gray60",
        ).grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(0, 4))

        # --- API keys ---
        keys_title = ctk.CTkLabel(card, text="API Keys", text_color="gray60")
        keys_title.grid(row=4, column=0, columnspan=2, sticky="w", padx=16, pady=(4, 0))

        widgets.keys_frame = ctk.CTkFrame(card, fg_color="transparent")
        widgets.keys_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 4))
        widgets.keys_frame.grid_columnconfigure(0, weight=1)
        self._refresh_key_list(settings.provider_id)

        add_key_row = ctk.CTkFrame(card, fg_color="transparent")
        add_key_row.grid(row=6, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))
        add_key_row.grid_columnconfigure(0, weight=1)
        new_key_entry = ctk.CTkEntry(add_key_row, placeholder_text="Paste an API key...", show="*")
        new_key_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            add_key_row, text="Add Key", width=90,
            command=lambda pid=settings.provider_id, entry=new_key_entry: self._add_key(pid, entry),
        ).grid(row=0, column=1)

        # --- Test connection ---
        test_row = ctk.CTkFrame(card, fg_color="transparent")
        test_row.grid(row=7, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))
        widgets.test_button = ctk.CTkButton(
            test_row, text="Test Connection", width=140,
            command=lambda pid=settings.provider_id: self._test_connection(pid),
        )
        widgets.test_button.pack(side="left")
        widgets.test_label = ctk.CTkLabel(test_row, text="", anchor="w")
        widgets.test_label.pack(side="left", padx=12)

    def _refresh_key_list(self, provider_id: str) -> None:
        widgets = self._provider_widgets.get(provider_id)
        if widgets is None or widgets.keys_frame is None:
            return
        for child in widgets.keys_frame.winfo_children():
            child.destroy()

        settings = self.context.config_manager.config.get_provider(provider_id)
        keys = settings.api_keys if settings else []

        if not keys:
            ctk.CTkLabel(widgets.keys_frame, text="No keys configured yet.", text_color="gray60").grid(
                row=0, column=0, sticky="w", pady=2
            )
            return

        for index, key_value in enumerate(keys):
            row_frame = ctk.CTkFrame(widgets.keys_frame, fg_color="transparent")
            row_frame.grid(row=index, column=0, sticky="ew", pady=2)
            row_frame.grid_columnconfigure(0, weight=1)

            masked = self._mask_key(key_value)
            ctk.CTkLabel(row_frame, text=masked, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0, 8))

            up_state = "normal" if index > 0 else "disabled"
            down_state = "normal" if index < len(keys) - 1 else "disabled"
            ctk.CTkButton(
                row_frame, text="▲", width=32, state=up_state,
                command=lambda pid=provider_id, i=index: self._move_key(pid, i, -1),
            ).grid(row=0, column=1, padx=2)
            ctk.CTkButton(
                row_frame, text="▼", width=32, state=down_state,
                command=lambda pid=provider_id, i=index: self._move_key(pid, i, 1),
            ).grid(row=0, column=2, padx=2)
            ctk.CTkButton(
                row_frame, text="Remove", width=70, fg_color="gray30", hover_color="gray20",
                command=lambda pid=provider_id, i=index: self._remove_key(pid, i),
            ).grid(row=0, column=3, padx=(2, 0))

    @staticmethod
    def _mask_key(value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"

    def _add_key(self, provider_id: str, entry: ctk.CTkEntry) -> None:
        value = entry.get().strip()
        if not value:
            return
        settings = self.context.config_manager.config.get_provider(provider_id)
        if settings is None:
            return
        if value not in settings.api_keys:
            settings.api_keys.append(value)
        self._sync_active_key_manager(provider_id)
        entry.delete(0, "end")
        LoggingService.log_user_action(_LOGGER, "add_api_key", provider=provider_id)
        self._refresh_key_list(provider_id)

    def _remove_key(self, provider_id: str, index: int) -> None:
        settings = self.context.config_manager.config.get_provider(provider_id)
        if settings is None or not (0 <= index < len(settings.api_keys)):
            return
        del settings.api_keys[index]
        self._sync_active_key_manager(provider_id)
        LoggingService.log_user_action(_LOGGER, "remove_api_key", provider=provider_id)
        self._refresh_key_list(provider_id)

    def _move_key(self, provider_id: str, index: int, direction: int) -> None:
        settings = self.context.config_manager.config.get_provider(provider_id)
        if settings is None:
            return
        target = index + direction
        if not (0 <= target < len(settings.api_keys)):
            return
        settings.api_keys[index], settings.api_keys[target] = settings.api_keys[target], settings.api_keys[index]
        self._sync_active_key_manager(provider_id)
        self._refresh_key_list(provider_id)

    def _sync_active_key_manager(self, provider_id: str) -> None:
        """Mirror a key-list edit into the live, in-use key manager immediately.

        Only relevant for whichever provider is *currently* powering Chat/
        the AI Editor -- editing an inactive provider's keys only needs to
        update the saved config, since nothing live is using it yet.
        """
        if provider_id != self.context.config_manager.config.last_used.provider:
            return
        settings = self.context.config_manager.config.get_provider(provider_id)
        if settings is not None:
            self.context.key_manager.replace_keys(settings.api_keys)

    # ------------------------------------------------------------------
    # Add / delete providers
    # ------------------------------------------------------------------
    def _open_add_provider_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Provider")
        dialog.geometry("420x320")
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Provider ID (unique, e.g. 'groq')").pack(anchor="w", padx=16, pady=(16, 2))
        id_entry = ctk.CTkEntry(dialog)
        id_entry.pack(fill="x", padx=16)

        ctk.CTkLabel(dialog, text="Display Name").pack(anchor="w", padx=16, pady=(12, 2))
        name_entry = ctk.CTkEntry(dialog)
        name_entry.pack(fill="x", padx=16)

        ctk.CTkLabel(dialog, text="Provider Type").pack(anchor="w", padx=16, pady=(12, 2))
        type_var = ctk.StringVar(value=AVAILABLE_PROVIDER_TYPES[0])
        ctk.CTkOptionMenu(dialog, values=list(AVAILABLE_PROVIDER_TYPES), variable=type_var).pack(
            anchor="w", padx=16
        )

        ctk.CTkLabel(dialog, text="Base URL").pack(anchor="w", padx=16, pady=(12, 2))
        base_url_entry = ctk.CTkEntry(dialog, placeholder_text="https://...")
        base_url_entry.pack(fill="x", padx=16)

        def on_create() -> None:
            provider_id = id_entry.get().strip().lower().replace(" ", "_")
            if not provider_id:
                messagebox.showerror("AutoCutAI", "Provider ID is required.")
                return
            config = self.context.config_manager.config
            if config.get_provider(provider_id) is not None:
                messagebox.showerror("AutoCutAI", f"A provider with id '{provider_id}' already exists.")
                return

            config.add_provider(
                ProviderSettings(
                    provider_id=provider_id,
                    provider_type=type_var.get(),
                    display_name=name_entry.get().strip() or provider_id.title(),
                    api_keys=[],
                    base_url=base_url_entry.get().strip(),
                    model="",
                    enabled=False,
                )
            )
            LoggingService.log_user_action(_LOGGER, "add_provider", provider=provider_id)
            dialog.destroy()
            self._refresh_providers_section()

        ctk.CTkButton(dialog, text="Add Provider", command=on_create).pack(pady=20)

    def _delete_provider(self, provider_id: str) -> None:
        if not messagebox.askyesno("AutoCutAI", f"Delete provider '{provider_id}'? This cannot be undone."):
            return
        config = self.context.config_manager.config
        config.remove_provider(provider_id)
        if config.last_used.provider == provider_id and config.providers:
            self._active_provider_var.set(config.providers[0].provider_id)
        LoggingService.log_user_action(_LOGGER, "delete_provider", provider=provider_id)
        self._refresh_providers_section()

    def _apply_model_suggestion(self, provider_id: str, suggestion: str) -> None:
        """Pre-fill the suggested model -- never saved until the user hits Save."""
        widgets = self._provider_widgets.get(provider_id)
        if widgets and widgets.model_var:
            widgets.model_var.set(suggestion)
        if widgets and widgets.migration_frame:
            widgets.migration_frame.destroy()
            widgets.migration_frame = None
        messagebox.showinfo(
            "AutoCutAI", f"Model field updated to '{suggestion}'. Click 'Save Settings' to apply it."
        )

    # ------------------------------------------------------------------
    # Connection test
    # ------------------------------------------------------------------
    def _test_connection(self, provider_id: str) -> None:
        widgets = self._provider_widgets.get(provider_id)
        if widgets is None:
            return

        base_url = widgets.base_url_var.get().strip()
        model = widgets.model_var.get().strip()
        settings = self.context.config_manager.config.get_provider(provider_id)
        if settings is None:
            return
        if not model:
            widgets.test_label.configure(text="Enter a model name first.", text_color="#e08b3a")
            return

        test_settings = ProviderSettings(
            provider_id=settings.provider_id,
            provider_type=settings.provider_type,
            display_name=widgets.display_name_var.get().strip() or settings.provider_id,
            api_keys=list(settings.api_keys),
            base_url=base_url,
            model=model,
            enabled=True,
            timeout_seconds=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
        )

        widgets.test_button.configure(state="disabled")
        widgets.test_label.configure(text="Testing...", text_color="gray60")

        def run_test() -> None:
            try:
                provider = self.context.provider_factory.create_from_settings(test_settings)
                result = provider.test_connection(model)
            except Exception as exc:  # noqa: BLE001 - never show a raw traceback
                message = f"Could not test connection: {exc}"
                _LOGGER.exception("Connection test failed for provider '%s'", provider_id)
                self.after(0, lambda message=message: self._show_test_result(provider_id, False, message))
                return
            detail = result.message
            if result.status_code:
                detail = f"{detail} (HTTP {result.status_code})"
            if result.model:
                detail = f"{detail} model={result.model}"
            if result.latency_seconds is not None:
                detail = f"{detail} latency={result.latency_seconds:.2f}s"
            self.after(0, lambda detail=detail: self._show_test_result(provider_id, result.success, detail))

        threading.Thread(target=run_test, daemon=True).start()

    def _show_test_result(self, provider_id: str, success: bool, message: str) -> None:
        widgets = self._provider_widgets.get(provider_id)
        if widgets is None:
            return
        widgets.test_button.configure(state="normal")
        widgets.test_label.configure(text=message, text_color="#3ddc84" if success else "#ff6b6b")

    # ------------------------------------------------------------------
    # Whisper backend
    # ------------------------------------------------------------------
    def _build_whisper_section(self, parent, row: int) -> None:
        section = ctk.CTkFrame(parent)
        section.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        section.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(section, text="Whisper (Transcription)", font=ctk.CTkFont(size=16, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, padx=16, pady=(14, 10), sticky="w")

        whisper = self.context.config_manager.config.whisper

        ctk.CTkLabel(section, text="Backend", width=90, anchor="w").grid(row=1, column=0, padx=16, pady=4, sticky="w")
        self._whisper_backend_var = ctk.StringVar(value=whisper.backend)
        ctk.CTkOptionMenu(section, values=list(AVAILABLE_WHISPER_BACKENDS), variable=self._whisper_backend_var).grid(
            row=1, column=1, padx=16, pady=4, sticky="w"
        )

        ctk.CTkLabel(section, text="Model", width=90, anchor="w").grid(row=2, column=0, padx=16, pady=(4, 14), sticky="w")
        self._whisper_model_var = ctk.StringVar(value=whisper.model_size)
        ctk.CTkEntry(section, textvariable=self._whisper_model_var, placeholder_text="tiny / base / small / medium / large-v3").grid(
            row=2, column=1, padx=16, pady=(4, 14), sticky="ew"
        )

        note = ctk.CTkLabel(
            section,
            text="'Auto' uses a verified GPU if one is fully supported (CUDA + cuBLAS + cuDNN), "
                 "and always falls back to CPU automatically otherwise -- see the Diagnostics page.",
            text_color="gray60", justify="left", wraplength=520,
        )
        note.grid(row=3, column=0, columnspan=2, padx=16, pady=(0, 14), sticky="w")

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def _build_theme_section(self, parent, row: int) -> None:
        section = ctk.CTkFrame(parent)
        section.grid(row=row, column=0, sticky="ew", pady=(0, 16))

        title = ctk.CTkLabel(section, text="Appearance", font=ctk.CTkFont(size=16, weight="bold"))
        title.grid(row=0, column=0, padx=16, pady=(14, 10), sticky="w")

        current_theme = self.context.config_manager.config.theme
        self._theme_var = ctk.StringVar(value=current_theme)
        theme_menu = ctk.CTkOptionMenu(section, values=list(AVAILABLE_THEMES), variable=self._theme_var)
        theme_menu.grid(row=1, column=0, padx=16, pady=(0, 16), sticky="w")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _save_settings(self) -> None:
        config = self.context.config_manager.config

        # Commit every provider card's Base URL / Model / Display Name /
        # Enabled back into config.providers.
        for provider_id, widgets in self._provider_widgets.items():
            settings = config.get_provider(provider_id)
            if settings is None:
                continue
            settings.display_name = widgets.display_name_var.get().strip() or provider_id.title()
            settings.base_url = widgets.base_url_var.get().strip()
            settings.model = widgets.model_var.get().strip()
            settings.enabled = bool(widgets.enabled_var.get())
            try:
                timeout_seconds = int(widgets.timeout_var.get().strip())
            except (TypeError, ValueError):
                messagebox.showerror("AutoCutAI", f"'{settings.display_name}' timeout must be an integer.")
                return
            minimum_timeout = (
                GEMINI_REQUEST_TIMEOUT_SECONDS
                if settings.provider_type == PROVIDER_TYPE_GEMINI
                else 1
            )
            if not minimum_timeout <= timeout_seconds <= 3600:
                messagebox.showerror(
                    "AutoCutAI",
                    f"'{settings.display_name}' timeout must be between "
                    f"{minimum_timeout} and 3600 seconds.",
                )
                return
            settings.timeout_seconds = timeout_seconds

            try:
                max_retries = int(widgets.max_retries_var.get().strip())
            except (TypeError, ValueError):
                messagebox.showerror("AutoCutAI", f"'{settings.display_name}' max retries must be an integer.")
                return
            if not 1 <= max_retries <= 10:
                messagebox.showerror("AutoCutAI", f"'{settings.display_name}' max retries must be between 1 and 10.")
                return
            settings.max_retries = max_retries

            if settings.provider_type == PROVIDER_TYPE_GEMINI:
                settings.model = migrate_gemini_model(settings.provider_type, settings.model)
                if settings.enabled and not settings.api_keys:
                    messagebox.showerror("AutoCutAI", "Gemini requires at least one API key.")
                    return
                if settings.base_url:
                    parsed = urlparse(settings.base_url)
                    if parsed.scheme not in ("http", "https") or not parsed.netloc:
                        messagebox.showerror(
                            "AutoCutAI",
                            "Gemini Base URL must be empty (SDK default) or a valid http(s) URL.",
                        )
                        return
                if suggest_model_migration(settings.provider_type, widgets.model_var.get().strip()):
                    messagebox.showwarning(
                        "AutoCutAI",
                        f"Deprecated Gemini model replaced with '{settings.model}'.",
                    )

        new_active_id = self._active_provider_var.get()
        active_settings = config.get_provider(new_active_id)

        if active_settings is None or not active_settings.enabled:
            messagebox.showerror(
                "AutoCutAI",
                "Select an enabled provider as 'Active' before saving (or enable the "
                "currently selected one).",
            )
            return
        if not active_settings.model:
            messagebox.showerror("AutoCutAI", f"'{active_settings.display_name}' has no model configured.")
            return

        config.last_used.provider = new_active_id
        config.whisper.backend = self._whisper_backend_var.get()
        config.whisper.model_size = self._whisper_model_var.get().strip() or config.whisper.model_size
        config.theme = self._theme_var.get()

        # Rebuild the live provider instance from the (possibly just-edited)
        # active provider settings and point Chat/AI Editor at it.
        try:
            provider = self.context.provider_factory.create_from_settings(active_settings)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via dialog
            _LOGGER.error("Failed to activate provider '%s': %s", new_active_id, exc)
            messagebox.showerror("AutoCutAI", f"Could not activate '{active_settings.display_name}':\n{exc}")
            return

        self.context.chat_service.set_provider(provider)
        self.context.chat_service.set_model(active_settings.model)
        self.context.edit_planner.set_provider(provider, active_settings.model)
        # Swap the *reference* itself (not just its contents) so
        # context.key_manager always is, not just currently matches, the
        # active provider's own key manager -- otherwise health tracking
        # (rate-limited/invalid/...) could drift apart again the moment a
        # request runs after switching providers.
        live_key_manager = getattr(provider, "key_manager", None)
        if live_key_manager is not None:
            self.context.key_manager = live_key_manager

        ctk.set_appearance_mode(config.theme)

        try:
            self.context.config_manager.save()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via dialog
            _LOGGER.error("Failed to save settings: %s", exc)
            messagebox.showerror("AutoCutAI", f"Could not save settings:\n{exc}")
            return

        LoggingService.log_user_action(_LOGGER, "save_settings", active_provider=new_active_id)
        messagebox.showinfo("AutoCutAI", "Settings saved.")
        self._refresh_providers_section()
