"""Top-level application window."""

from __future__ import annotations

from typing import Dict, Type

import customtkinter as ctk

from core.app_context import AppContext
from services.logging_service import LoggingService
from ui.pages.base_page import BasePage
from ui.pages.chat_page import ChatPage
from ui.pages.diagnostics_page import DiagnosticsPage
from ui.pages.edit_plan_page import EditPlanPage
from ui.pages.home_page import HomePage
from ui.pages.logs_page import LogsPage
from ui.pages.personal_profile_page import PersonalProfilePage
from ui.pages.settings_page import SettingsPage
from ui.widgets.sidebar import Sidebar
from utils.constants import (
    APP_NAME,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    PAGE_CHAT,
    PAGE_DIAGNOSTICS,
    PAGE_EDIT_PLAN,
    PAGE_HOME,
    PAGE_LOGS,
    PAGE_PERSONAL_PROFILE,
    PAGE_SETTINGS,
)

_LOGGER = LoggingService.get_logger("ui.main_window")

# Registry of navigable pages. Adding a new page is a two-line change here
# and a new module under ui/pages/ -- nothing else needs to change.
_PAGE_REGISTRY: Dict[str, Type[BasePage]] = {
    PAGE_HOME: HomePage,
    PAGE_CHAT: ChatPage,
    PAGE_EDIT_PLAN: EditPlanPage,
    PAGE_PERSONAL_PROFILE: PersonalProfilePage,
    PAGE_SETTINGS: SettingsPage,
    PAGE_DIAGNOSTICS: DiagnosticsPage,
    PAGE_LOGS: LogsPage,
}

_NAV_ITEMS = [
    (PAGE_HOME, "Home"),
    (PAGE_CHAT, "AI Chat"),
    (PAGE_EDIT_PLAN, "AI Editor"),
    (PAGE_PERSONAL_PROFILE, "My Editing Style"),
    (PAGE_SETTINGS, "Settings"),
    (PAGE_DIAGNOSTICS, "Diagnostics"),
    (PAGE_LOGS, "Logs"),
]


class MainWindow(ctk.CTk):
    """The application's single top-level window."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context

        config = context.config_manager.config
        self.title(APP_NAME)
        self.geometry(f"{config.window.width}x{config.window.height}")
        self.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._sidebar = Sidebar(self, nav_items=_NAV_ITEMS, on_navigate=self.show_page)
        self._sidebar.grid(row=0, column=0, sticky="nsw")

        self._content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self._content_frame.grid(row=0, column=1, sticky="nsew")
        self._content_frame.grid_rowconfigure(0, weight=1)
        self._content_frame.grid_columnconfigure(0, weight=1)

        self._pages: Dict[str, BasePage] = {}
        self._current_page_id: str | None = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        initial_page = config.last_used.page if config.last_used.page in _PAGE_REGISTRY else PAGE_HOME
        self.show_page(initial_page)

    def show_page(self, page_id: str) -> None:
        """Switch the visible content page, creating it lazily on first use."""
        if page_id not in _PAGE_REGISTRY:
            _LOGGER.warning("Attempted to navigate to unknown page: %s", page_id)
            return

        if self._current_page_id is not None and self._current_page_id in self._pages:
            self._pages[self._current_page_id].grid_remove()
            self._pages[self._current_page_id].on_hide()

        if page_id not in self._pages:
            page_class = _PAGE_REGISTRY[page_id]
            self._pages[page_id] = page_class(self._content_frame, self.context)
            self._pages[page_id].grid(row=0, column=0, sticky="nsew")

        page = self._pages[page_id]
        page.grid()
        page.on_show()

        self._sidebar.set_active(page_id)
        self._current_page_id = page_id
        self.context.config_manager.config.last_used.page = page_id
        LoggingService.log_user_action(_LOGGER, "navigate", page=page_id)

    def _on_close(self) -> None:
        config = self.context.config_manager.config
        config.window.width = self.winfo_width()
        config.window.height = self.winfo_height()
        try:
            self.context.config_manager.save()
        except Exception:  # noqa: BLE001 - never block app shutdown on a save failure
            _LOGGER.exception("Failed to save configuration on close")
        self.destroy()
