"""Configurable, extendable set of editing rules (Version 3.5).

Rules are no longer hardcoded inside prompt text: callers can add, remove,
enable/disable, or completely replace the rule set, and
:class:`ai.prompt_builder.PromptBuilder` renders whatever is currently
enabled into the prompt automatically.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

from models.editing_rule import EditingRule
from services.logging_service import LoggingService
from utils.constants import EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


class EditingRules:
    """A named, mutable collection of :class:`EditingRule` objects.

    :meth:`default` builds the standard rule set -- equivalent to Version
    3's hardcoded editing principles -- and is used automatically by
    :class:`ai.prompt_builder.PromptBuilder` when no explicit rules are
    supplied, so existing Version 3 callers see unchanged behavior.
    """

    def __init__(
        self,
        name: str = "default",
        rules: Optional[Iterable[EditingRule]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.name = name
        self._rules: List[EditingRule] = list(rules) if rules else []
        self._logger = logger or LoggingService.get_logger("ai.editing_rules")

    @classmethod
    def default(cls) -> "EditingRules":
        """The standard rule set matching Version 3's built-in editing principles."""
        return cls(
            name="default",
            rules=[
                EditingRule("Silence", EDIT_ACTION_REMOVE, -10, "Dead air with no speech or meaningful sound."),
                EditingRule("Dead Air", EDIT_ACTION_REMOVE, -8, "Pauses or lulls with nothing happening."),
                EditingRule("Waiting", EDIT_ACTION_REMOVE, -5, "Waiting for something to load or start."),
                EditingRule("Loading Screens", EDIT_ACTION_REMOVE, -6, "Game or app loading screens."),
                EditingRule("Long Walking", EDIT_ACTION_REMOVE, -3, "Extended, uneventful walking or traveling."),
                EditingRule(
                    "Repeated Sentences", EDIT_ACTION_REMOVE, -4, "Restarted or duplicated lines of speech."
                ),
                EditingRule("Reactions", EDIT_ACTION_KEEP, 4, "Genuine reactions -- surprise, shock, excitement."),
                EditingRule("Laughter", EDIT_ACTION_KEEP, 5, "Laughing or other genuinely funny moments."),
                EditingRule("Funny Moments", EDIT_ACTION_KEEP, 5, "Comedic or entertaining moments."),
                EditingRule(
                    "Important Gameplay", EDIT_ACTION_KEEP, 3, "Meaningful, skillful, or pivotal gameplay."
                ),
                EditingRule("Story Progress", EDIT_ACTION_KEEP, 5, "Moments that move a narrative forward."),
                EditingRule("Boss Fights", EDIT_ACTION_KEEP, 5, "Boss battles or major challenge encounters."),
                EditingRule(
                    "Important Conversations", EDIT_ACTION_KEEP, 4, "Meaningful dialogue or discussion."
                ),
            ],
        )

    @property
    def rules(self) -> List[EditingRule]:
        return list(self._rules)

    def enabled_rules(self) -> List[EditingRule]:
        return [rule for rule in self._rules if rule.enabled]

    def keep_rules(self) -> List[EditingRule]:
        return [rule for rule in self.enabled_rules() if rule.is_keep_rule]

    def remove_rules(self) -> List[EditingRule]:
        return [rule for rule in self.enabled_rules() if not rule.is_keep_rule]

    def add_rule(self, rule: EditingRule) -> None:
        self._rules.append(rule)
        self._logger.info("Editing rule added: %s (%s, score=%+.1f)", rule.name, rule.action, rule.score)

    def remove_rule(self, name: str) -> None:
        before = len(self._rules)
        self._rules = [rule for rule in self._rules if rule.name != name]
        if len(self._rules) != before:
            self._logger.info("Editing rule removed: %s", name)

    def set_enabled(self, name: str, enabled: bool) -> None:
        for rule in self._rules:
            if rule.name == name:
                rule.enabled = enabled
                self._logger.info("Editing rule '%s' %s", name, "enabled" if enabled else "disabled")
                return

    def to_prompt_text(self) -> str:
        """Render the enabled rules as numbered prompt instructions.

        Used by :class:`ai.prompt_builder.PromptBuilder` in place of a
        hardcoded rules string, so editing behavior can be tuned without
        touching any prompt-assembly code.
        """
        remove_rules = sorted(self.remove_rules(), key=lambda rule: rule.score)
        keep_rules = sorted(self.keep_rules(), key=lambda rule: -rule.score)

        lines = ["Remove (most important first):"]
        lines.extend(f"  - {rule.name} (score {rule.score:+.0f}): {rule.description}" for rule in remove_rules)
        lines.append("Keep (most important first):")
        lines.extend(f"  - {rule.name} (score {rule.score:+.0f}): {rule.description}" for rule in keep_rules)

        self._logger.info(
            "Rule selection: rule_set=%s enabled=%d (keep=%d, remove=%d)",
            self.name, len(self.enabled_rules()), len(keep_rules), len(remove_rules),
        )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"name": self.name, "rules": [rule.to_dict() for rule in self._rules]}

    def fingerprint(self) -> str:
        """A short, stable hash of the enabled rules (Feature 12/16).

        Used as part of an EditPlan cache key so that changing the active
        rule set (game profile, personal preferences, style) -- without
        touching the video, Whisper settings, or transcript -- correctly
        invalidates only the cached *plan*, never the cached *analysis*
        (transcript/frames), which is unaffected by editing rules.
        """
        import hashlib
        import json

        payload = json.dumps(
            [rule.to_dict() for rule in sorted(self.enabled_rules(), key=lambda r: r.name)],
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def merge_overlay(self, overlay: "EditingRules") -> "EditingRules":
        """Return a new :class:`EditingRules` with ``overlay`` layered on top of ``self``.

        Version 4.5 (Feature 16): rules in ``overlay`` take priority over a
        rule of the *same name* in ``self`` (overlay replaces it entirely);
        rules that exist only in one side are kept as-is. Neither input is
        mutated. Used to layer, in increasing priority:
        default rules -> game/style profile -> personal preferences ->
        persistent user instructions -> this-video-only instructions.
        """
        merged: dict[str, EditingRule] = {rule.name: rule for rule in self._rules}
        for rule in overlay.rules:
            merged[rule.name] = rule
        combined_name = f"{self.name}+{overlay.name}"
        return EditingRules(name=combined_name, rules=list(merged.values()), logger=self._logger)
