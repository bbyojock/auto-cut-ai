"""Left-hand navigation sidebar."""

from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import customtkinter as ctk

from utils.constants import APP_NAME, APP_VERSION

# (page_id, display_label)
NavItem = Tuple[str, str]


class Sidebar(ctk.CTkFrame):
    """Vertical navigation bar. Purely presentational -- delegates clicks
    to an ``on_navigate`` callback supplied by :class:`ui.main_window.MainWindow`.
    """

    def __init__(self, master: ctk.CTk, nav_items: List[NavItem], on_navigate: Callable[[str], None]) -> None:
        super().__init__(master, width=200, corner_radius=0)
        self._on_navigate = on_navigate
        self._buttons: Dict[str, ctk.CTkButton] = {}

        self.grid_rowconfigure(len(nav_items) + 1, weight=1)

        title_label = ctk.CTkLabel(
            self, text=APP_NAME, font=ctk.CTkFont(size=20, weight="bold")
        )
        title_label.grid(row=0, column=0, padx=20, pady=(24, 4), sticky="w")

        version_label = ctk.CTkLabel(
            self, text=f"v{APP_VERSION}", font=ctk.CTkFont(size=11), text_color="gray60"
        )
        version_label.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        for index, (page_id, label) in enumerate(nav_items, start=2):
            button = ctk.CTkButton(
                self,
                text=label,
                anchor="w",
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray25"),
                command=lambda pid=page_id: self._handle_click(pid),
            )
            button.grid(row=index, column=0, padx=12, pady=4, sticky="ew")
            self._buttons[page_id] = button

    def _handle_click(self, page_id: str) -> None:
        self._on_navigate(page_id)

    def set_active(self, page_id: str) -> None:
        """Highlight the button belonging to the currently visible page."""
        for pid, button in self._buttons.items():
            if pid == page_id:
                button.configure(fg_color=("gray75", "gray30"))
            else:
                button.configure(fg_color="transparent")
