"""Common base class for every page shown in the main window."""

from __future__ import annotations

from abc import abstractmethod

import customtkinter as ctk

from core.app_context import AppContext


class BasePage(ctk.CTkFrame):
    """A full-window content page.

    Subclasses build their widgets in :meth:`build`, which runs once at
    construction time, and may override :meth:`on_show`/:meth:`on_hide` to
    react to navigation (e.g. refreshing log contents each time the Logs
    page becomes visible).
    """

    def __init__(self, master: ctk.CTkBaseClass, context: AppContext) -> None:
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.context = context
        self.build()

    @abstractmethod
    def build(self) -> None:
        """Construct and lay out this page's widgets. Called once."""
        raise NotImplementedError

    def on_show(self) -> None:
        """Hook called every time this page is navigated to. Optional override."""

    def on_hide(self) -> None:
        """Hook called every time this page is navigated away from. Optional override."""
