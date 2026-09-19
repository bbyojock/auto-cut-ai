"""Persists and updates the user's Personal Editing Profile (Version 4.5,
Features 13/14/15).

Stored as structured JSON (never raw chat history) at
``config/personal_profile.json`` under the project root, alongside
``config/config.json`` -- consistent with how :class:`config.config_manager.ConfigManager`
persists application settings. One profile per AutoCutAI installation,
matching the spec's "the application should learn these preferences and
build a personal editing profile" (singular, cross-video).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from models.personal_profile import PersonalEditingProfile, PersonalPreference, PreferenceStrength
from services.logging_service import LoggingService
from utils.file_utils import ensure_directory, get_project_root

_PROFILE_FILE_NAME = "personal_profile.json"


class PersonalProfileService:
    """Owns the lifecycle of one :class:`PersonalEditingProfile`."""

    def __init__(self, profile_path: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        self._profile_path = profile_path or (get_project_root() / "config" / _PROFILE_FILE_NAME)
        self._logger = logger or LoggingService.get_logger("services.personal_profile")
        self._profile: PersonalEditingProfile = PersonalEditingProfile()
        self.load()

    @property
    def profile(self) -> PersonalEditingProfile:
        return self._profile

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load(self) -> PersonalEditingProfile:
        if not self._profile_path.exists():
            self._profile = PersonalEditingProfile()
            return self._profile
        try:
            data = json.loads(self._profile_path.read_text(encoding="utf-8"))
            self._profile = PersonalEditingProfile.from_dict(data)
            self._logger.info("Personal editing profile loaded (%d preference(s)).", len(self._profile.preferences))
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            self._logger.warning("Could not load personal profile (%s); starting fresh.", exc)
            self._profile = PersonalEditingProfile()
        return self._profile

    def save(self) -> None:
        try:
            ensure_directory(self._profile_path.parent)
            self._profile_path.write_text(
                json.dumps(self._profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._logger.info("Personal editing profile saved.")
        except OSError:
            self._logger.exception("Failed to save personal editing profile (non-fatal).")

    # ------------------------------------------------------------------
    # Feature 13/14: learning from instructions and corrections
    # ------------------------------------------------------------------
    _PERSISTENT_MARKERS = (
        "always", "never", "from now on", "every video", "remember",
        "in general", "by default", "usually", "typically", "going forward",
        "every time",
    )

    @classmethod
    def is_persistent_instruction(cls, text: str) -> bool:
        """Heuristic classification of Feature 13's "temporary vs persistent" distinction.

        A simple, explainable keyword heuristic rather than a black-box
        model: any of a handful of standing-preference markers ("always",
        "never", "from now on", ...) marks an instruction as persistent;
        everything else defaults to this-video-only. This deliberately
        errs toward *not* generalizing an instruction, since applying a
        one-off request to every future video is the more harmful mistake.
        """
        lowered = text.lower()
        return any(marker in lowered for marker in cls._PERSISTENT_MARKERS)

    def learn_from_instruction(self, text: str, known_topics: Optional[list] = None) -> Optional[PersonalPreference]:
        """If ``text`` is a persistent instruction naming a known topic, record it directly.

        Example: "Always keep clutch moments." with topic "Clutch" in
        ``known_topics`` immediately sets that preference to at least
        HIGH, since an explicit standing instruction is stronger evidence
        than an inferred correction. Returns ``None`` if the instruction
        isn't persistent or doesn't clearly name a known topic (in which
        case it still reaches the AI as a plain instruction -- see
        :mod:`models.editing_instructions` -- just without updating the
        learned profile).
        """
        if not self.is_persistent_instruction(text):
            return None
        lowered = text.lower()
        topics = known_topics or []
        for topic in topics:
            if topic.lower() in lowered:
                wants_remove = any(w in lowered for w in ("remove", "cut", "delete", "never keep"))
                strength = PreferenceStrength.VERY_HIGH if not wants_remove else PreferenceStrength.VERY_LOW
                if "never remove" in lowered or "always keep" in lowered:
                    strength = PreferenceStrength.VERY_HIGH
                elif "never keep" in lowered or "always remove" in lowered or "always cut" in lowered:
                    strength = PreferenceStrength.VERY_LOW
                pref = self._profile.set_explicit(topic, strength)
                pref.source = "learned"  # from an instruction, not the profile page -- still editable there
                self.save()
                self._logger.info("Learned persistent preference from instruction: %s -> %s", topic, strength.value)
                return pref
        return None

    def record_feedback(self, topic: str, agrees_with_keep: bool) -> PersonalPreference:
        """Record one EditPlan-chat correction/confirmation for ``topic`` (Feature 14).

        Confidence only grows gradually -- see
        :meth:`models.personal_profile.PersonalPreference.record_feedback`
        -- so a single correction never flips behavior for future videos.
        """
        pref = self._profile.get_or_create(topic)
        pref.record_feedback(agrees_with_keep)
        self.save()
        self._logger.info(
            "Feedback recorded for '%s': agrees_with_keep=%s -> strength=%s (confidence=%.0f%%)",
            topic, agrees_with_keep, pref.strength.value, pref.confidence * 100,
        )
        return pref

    # ------------------------------------------------------------------
    # Feature 15: profile management
    # ------------------------------------------------------------------
    def set_explicit(self, topic: str, strength: PreferenceStrength) -> PersonalPreference:
        pref = self._profile.set_explicit(topic, strength)
        self.save()
        return pref

    def set_enabled(self, topic: str, enabled: bool) -> None:
        pref = self._profile.preferences.get(topic.strip().lower())
        if pref is not None:
            pref.enabled = enabled
            self.save()

    def reset_preference(self, topic: str) -> None:
        """Reset (delete) one preference. Never silent -- callers should confirm with the user first."""
        self._profile.reset(topic)
        self.save()

    def reset_all(self) -> None:
        """Reset the entire personal profile. Never silent -- callers should confirm with the user first."""
        self._profile.reset_all()
        self.save()
