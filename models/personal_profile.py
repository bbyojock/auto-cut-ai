"""Personal editing style memory models (Version 4.5, Feature 13/14/15).

AutoCutAI should "remember the user's editing preferences" across videos.
:class:`PersonalPreference` is one learned (or user-declared) preference
about a *topic* (e.g. "Combat", "Funny reactions", "Walking"); confidence
grows gradually with repeated, consistent feedback (Feature 14: "Do not
blindly change preferences from one correction") and never from a single
data point. :class:`PersonalEditingProfile` is the full set of preferences
for one user, persisted as structured data (JSON), never as raw chat
history, per the spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class PreferenceStrength(str, Enum):
    """How strongly a topic should be weighted, from the user's point of view."""

    VERY_LOW = "very_low"
    LOW = "low"
    NEUTRAL = "neutral"
    HIGH = "high"
    VERY_HIGH = "very_high"

    @property
    def score(self) -> float:
        """A numeric score usable directly as an :class:`models.editing_rule.EditingRule` score."""
        return {
            PreferenceStrength.VERY_LOW: -8.0,
            PreferenceStrength.LOW: -4.0,
            PreferenceStrength.NEUTRAL: 0.0,
            PreferenceStrength.HIGH: 4.0,
            PreferenceStrength.VERY_HIGH: 8.0,
        }[self]

    @classmethod
    def from_score(cls, score: float) -> "PreferenceStrength":
        if score <= -6:
            return cls.VERY_LOW
        if score < 0:
            return cls.LOW
        if score == 0:
            return cls.NEUTRAL
        if score < 6:
            return cls.HIGH
        return cls.VERY_HIGH

    def step_toward(self, direction: int) -> "PreferenceStrength":
        """Move one step up (``direction > 0``) or down (``direction < 0``) the scale."""
        order = [
            PreferenceStrength.VERY_LOW,
            PreferenceStrength.LOW,
            PreferenceStrength.NEUTRAL,
            PreferenceStrength.HIGH,
            PreferenceStrength.VERY_HIGH,
        ]
        index = order.index(self)
        index = max(0, min(len(order) - 1, index + (1 if direction > 0 else -1)))
        return order[index]


# Confirmations needed at the *current* strength before it is allowed to
# step further in the same direction (Feature 14: gradual confidence, not
# a single-correction overreaction).
_CONFIRMATIONS_TO_STEP = 3


@dataclass
class PersonalPreference:
    """One learned/declared editing preference for a single topic.

    ``topic`` is a short, human-readable label (e.g. ``"Combat"``,
    ``"Funny reactions"``, ``"Silence"``, ``"Walking/navigation"``) that
    doubles as the :class:`models.editing_rule.EditingRule` name generated
    from it, so a learned preference maps 1:1 onto a rule the prompt can
    render.
    """

    topic: str
    strength: PreferenceStrength = PreferenceStrength.NEUTRAL
    action_hint: str = "keep"  # "keep" or "remove" -- which action this topic favors when strength != NEUTRAL
    confirmations: int = 0  # lifetime count, for display (Feature 15)
    corrections: int = 0  # lifetime count, for display (Feature 15)
    streak: int = 0  # consecutive *consistent* feedback since the last strength change -- drives stepping only
    source: str = "learned"  # "learned" | "explicit" (user set it directly in the profile page)
    enabled: bool = True
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def confidence(self) -> float:
        """A simple 0..1 confidence score from how much consistent feedback backs this up."""
        total = self.confirmations + self.corrections
        if total == 0:
            return 0.0
        agree = max(self.confirmations, self.corrections)
        consistency = agree / total
        volume_factor = min(1.0, total / (2 * _CONFIRMATIONS_TO_STEP))
        return round(consistency * volume_factor, 2)

    def record_feedback(self, agrees_with_keep: bool) -> None:
        """Record one piece of feedback and, if consistent evidence has
        accumulated, gradually shift ``strength`` (Feature 14).

        ``agrees_with_keep=True`` means the user's action favored KEEPing
        this kind of content (e.g. restored a removed segment because "the
        conversation there is important"); ``False`` means they favored
        REMOVE (e.g. removed a kept segment of low-action navigation).
        """
        direction = 1 if agrees_with_keep else -1
        currently_matches = (
            (self.strength.score > 0 and direction > 0)
            or (self.strength.score < 0 and direction < 0)
            or self.strength == PreferenceStrength.NEUTRAL
        )
        if agrees_with_keep:
            self.confirmations += 1
        else:
            self.corrections += 1

        if currently_matches:
            self.streak += 1
        else:
            # A contradicting signal resets the streak so the strength
            # doesn't keep climbing in the old direction from stale evidence.
            self.streak = 0

        if self.streak >= _CONFIRMATIONS_TO_STEP:
            self.strength = self.strength.step_toward(direction)
            self.action_hint = "keep" if self.strength.score >= 0 else "remove"
            self.streak = 0
        self.updated_at = datetime.now()

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "strength": self.strength.value,
            "action_hint": self.action_hint,
            "confirmations": self.confirmations,
            "corrections": self.corrections,
            "streak": self.streak,
            "source": self.source,
            "enabled": self.enabled,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalPreference":
        try:
            updated_at = datetime.fromisoformat(data.get("updated_at", ""))
        except ValueError:
            updated_at = datetime.now()
        return cls(
            topic=str(data["topic"]),
            strength=PreferenceStrength(data.get("strength", PreferenceStrength.NEUTRAL.value)),
            action_hint=str(data.get("action_hint", "keep")),
            confirmations=int(data.get("confirmations", 0)),
            corrections=int(data.get("corrections", 0)),
            streak=int(data.get("streak", 0)),
            source=str(data.get("source", "learned")),
            enabled=bool(data.get("enabled", True)),
            updated_at=updated_at,
        )


@dataclass
class PersonalEditingProfile:
    """The full set of :class:`PersonalPreference` entries for one user."""

    preferences: Dict[str, PersonalPreference] = field(default_factory=dict)

    def get_or_create(self, topic: str) -> PersonalPreference:
        key = topic.strip().lower()
        if key not in self.preferences:
            self.preferences[key] = PersonalPreference(topic=topic.strip())
        return self.preferences[key]

    def set_explicit(self, topic: str, strength: PreferenceStrength) -> PersonalPreference:
        """Directly set a preference from the Personal Profile page (Feature 15)."""
        pref = self.get_or_create(topic)
        pref.strength = strength
        pref.action_hint = "keep" if strength.score >= 0 else "remove"
        pref.source = "explicit"
        pref.updated_at = datetime.now()
        return pref

    def reset(self, topic: str) -> None:
        key = topic.strip().lower()
        self.preferences.pop(key, None)

    def reset_all(self) -> None:
        self.preferences.clear()

    def enabled_preferences(self) -> List[PersonalPreference]:
        return [p for p in self.preferences.values() if p.enabled and p.strength != PreferenceStrength.NEUTRAL]

    def to_dict(self) -> dict:
        return {"preferences": [p.to_dict() for p in self.preferences.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalEditingProfile":
        profile = cls()
        for raw in data.get("preferences", []):
            pref = PersonalPreference.from_dict(raw)
            profile.preferences[pref.topic.strip().lower()] = pref
        return profile
