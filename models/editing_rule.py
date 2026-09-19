"""A single configurable editing rule (Version 3.5)."""

from __future__ import annotations

from dataclasses import dataclass

from utils.constants import EDIT_ACTION_KEEP


@dataclass(slots=True)
class EditingRule:
    """One rule the AI editing brain should follow.

    ``action`` says whether moments matching ``name`` should generally be
    kept or removed; ``score`` is how strongly this rule should weigh in
    the AI's decision (see :class:`ai.editing_scorer.EditingScorer`) --
    positive favors keeping, negative favors removing, and magnitude is
    priority. Rules are plain data so :class:`ai.editing_rules.EditingRules`
    can add, remove, enable, or disable them freely at runtime.
    """

    name: str
    action: str
    score: float
    description: str
    enabled: bool = True

    # Version 4.5 (Feature 16 -- Style + Personal Preference Priority):
    # where this rule came from, so a rule merge can enforce
    #   video instruction > user instruction > personal preference
    #   > game/style profile > default
    # without losing track of provenance. Purely additive/optional so
    # every pre-4.5 call site that only ever set (name, action, score,
    # description[, enabled]) keeps working unchanged.
    source: str = "default"
    priority: int = 0

    @property
    def is_keep_rule(self) -> bool:
        return self.action == EDIT_ACTION_KEEP

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "action": self.action,
            "score": self.score,
            "description": self.description,
            "enabled": self.enabled,
            "source": self.source,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditingRule":
        return cls(
            name=str(data["name"]),
            action=str(data["action"]),
            score=float(data["score"]),
            description=str(data.get("description", "")),
            enabled=bool(data.get("enabled", True)),
            source=str(data.get("source", "default")),
            priority=int(data.get("priority", 0)),
        )
