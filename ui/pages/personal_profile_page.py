"""Personal Editing Profile page (Version 4.5, Feature 15).

Shows every learned/explicit :class:`models.personal_profile.PersonalPreference`
with its strength, confidence, and confirmation/correction counts, and lets
the user enable/disable, edit, or reset preferences -- individually or all
at once. Nothing here is ever silently deleted (Feature 15's explicit
requirement); reset actions always confirm first.
"""

from __future__ import annotations

from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from models.personal_profile import PersonalPreference, PreferenceStrength
from services.logging_service import LoggingService
from ui.pages.base_page import BasePage

_LOGGER = LoggingService.get_logger("ui.personal_profile_page")

_STRENGTH_LABELS = {
    PreferenceStrength.VERY_LOW: "Very Low",
    PreferenceStrength.LOW: "Low",
    PreferenceStrength.NEUTRAL: "Neutral",
    PreferenceStrength.HIGH: "High",
    PreferenceStrength.VERY_HIGH: "Very High",
}
_STRENGTH_COLORS = {
    PreferenceStrength.VERY_LOW: "#f85149",
    PreferenceStrength.LOW: "#f0a35a",
    PreferenceStrength.NEUTRAL: "gray60",
    PreferenceStrength.HIGH: "#7ee787",
    PreferenceStrength.VERY_HIGH: "#3fb950",
}


class PersonalProfilePage(BasePage):
    """Read/write view of the user's :class:`services.personal_profile_service.PersonalProfileService`."""

    def build(self) -> None:
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._service = self.context.personal_profile_service

        header = ctk.CTkLabel(self, text="Personal Editing Profile", font=ctk.CTkFont(size=24, weight="bold"))
        header.grid(row=0, column=0, padx=30, pady=(30, 4), sticky="w")

        subheader = ctk.CTkLabel(
            self,
            text="AutoCutAI gradually learns your editing taste from the EditPlan Chat and from "
            "standing instructions (\"Always keep clutch moments.\"). Nothing here changes silently -- "
            "reset actions always ask first.",
            text_color="gray60", anchor="w", justify="left", wraplength=760,
        )
        subheader.grid(row=1, column=0, padx=30, pady=(0, 10), sticky="w")

        add_row = ctk.CTkFrame(self, fg_color="transparent")
        add_row.grid(row=2, column=0, padx=30, pady=(0, 10), sticky="ew")
        self._topic_entry = ctk.CTkEntry(add_row, placeholder_text="Topic (e.g. Combat, Walking, Silence)", width=260)
        self._topic_entry.grid(row=0, column=0, padx=(0, 8))
        self._strength_menu = ctk.CTkOptionMenu(add_row, values=[_STRENGTH_LABELS[s] for s in PreferenceStrength])
        self._strength_menu.set(_STRENGTH_LABELS[PreferenceStrength.HIGH])
        self._strength_menu.grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(add_row, text="Set Preference", command=self._add_or_update_clicked).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            add_row, text="Reset Entire Profile", fg_color="#7a2d2d", hover_color="#5a1f1f", command=self._reset_all_clicked,
        ).grid(row=0, column=3)

        self._list_frame = ctk.CTkScrollableFrame(self, label_text="")
        self._list_frame.grid(row=3, column=0, padx=30, pady=(0, 20), sticky="nsew")
        self._list_frame.grid_columnconfigure(0, weight=1)

        self._refresh()

    def on_show(self) -> None:
        self._refresh()

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        for child in self._list_frame.winfo_children():
            child.destroy()

        preferences = sorted(self._service.profile.preferences.values(), key=lambda p: p.topic.lower())
        if not preferences:
            ctk.CTkLabel(
                self._list_frame, text="No learned preferences yet -- they'll appear here as you use the EditPlan Chat.",
                text_color="gray50",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        for index, pref in enumerate(preferences):
            self._render_row(index, pref)

    def _render_row(self, index: int, pref: PersonalPreference) -> None:
        row = ctk.CTkFrame(self._list_frame)
        row.grid(row=index, column=0, sticky="ew", pady=4)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=pref.topic, font=ctk.CTkFont(weight="bold"), anchor="w", width=180).grid(
            row=0, column=0, padx=(12, 8), pady=10, sticky="w"
        )

        detail_frame = ctk.CTkFrame(row, fg_color="transparent")
        detail_frame.grid(row=0, column=1, sticky="ew", pady=10)
        ctk.CTkLabel(
            detail_frame, text=_STRENGTH_LABELS[pref.strength], text_color=_STRENGTH_COLORS[pref.strength],
            font=ctk.CTkFont(weight="bold"),
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            detail_frame,
            text=f"{pref.confirmations} confirmation(s) · {pref.corrections} correction(s) · "
            f"{pref.confidence * 100:.0f}% confidence · source: {pref.source}",
            text_color="gray60",
        ).pack(side="left")

        controls = ctk.CTkFrame(row, fg_color="transparent")
        controls.grid(row=0, column=2, padx=(8, 12), pady=8, sticky="e")

        enabled_var = ctk.BooleanVar(value=pref.enabled)
        ctk.CTkSwitch(
            controls, text="Enabled", variable=enabled_var,
            command=lambda: self._toggle_enabled(pref.topic, enabled_var.get()),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            controls, text="Reset", width=70, fg_color="gray30", hover_color="#7a2d2d",
            command=lambda: self._reset_one_clicked(pref.topic),
        ).pack(side="left")

    # ------------------------------------------------------------------
    def _add_or_update_clicked(self) -> None:
        topic = self._topic_entry.get().strip()
        if not topic:
            return
        label_to_strength = {label: strength for strength, label in _STRENGTH_LABELS.items()}
        strength = label_to_strength[self._strength_menu.get()]
        self._service.set_explicit(topic, strength)
        LoggingService.log_user_action(_LOGGER, "set_personal_preference", topic=topic, strength=strength.value)
        self._topic_entry.delete(0, "end")
        self._refresh()

    def _toggle_enabled(self, topic: str, enabled: bool) -> None:
        self._service.set_enabled(topic, enabled)

    def _reset_one_clicked(self, topic: str) -> None:
        if messagebox.askyesno("Reset Preference", f'Reset the learned preference for "{topic}"? This cannot be undone.'):
            self._service.reset_preference(topic)
            LoggingService.log_user_action(_LOGGER, "reset_personal_preference", topic=topic)
            self._refresh()

    def _reset_all_clicked(self) -> None:
        if messagebox.askyesno(
            "Reset Entire Profile", "Reset your ENTIRE personal editing profile? Every learned preference will be lost. This cannot be undone.",
        ):
            self._service.reset_all()
            LoggingService.log_user_action(_LOGGER, "reset_personal_profile")
            self._refresh()
