"""Combines default rules, a game/style profile, and personal preferences
into one :class:`ai.editing_rules.EditingRules` set, honoring Feature 16's
priority order.

Recommended priority (highest first), per the Version 4.5 spec:

    1. Explicit instruction for the current video
    2. Explicit (persistent) user instruction
    3. Personal learned preferences
    4. Selected editing profile (game/style)
    5. Default editing rules

Free-text instructions (1 & 2) are *not* expressed as scored rules -- they
are natural language and go into the prompt directly via
``ai.prompt_templates.PromptTemplates.build_instructions_block``, which is
explicitly told it outranks the rules text (see
:class:`ai.prompt_builder.PromptBuilder`). This module handles the
remaining three layers (3, 4, 5), which *are* naturally expressed as
scored KEEP/REMOVE rules, by merging them in ascending priority order so a
personal preference always wins over a same-named game-profile or default
rule.
"""

from __future__ import annotations

from typing import Optional

from ai.editing_rules import EditingRules
from models.editing_rule import EditingRule
from models.personal_profile import PersonalEditingProfile
from utils.constants import EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


def personal_profile_to_rules(profile: PersonalEditingProfile) -> EditingRules:
    """Turn a user's learned/explicit preferences into an :class:`EditingRules` overlay."""
    rules = []
    for pref in profile.enabled_preferences():
        action = EDIT_ACTION_KEEP if pref.action_hint == "keep" else EDIT_ACTION_REMOVE
        rules.append(
            EditingRule(
                name=pref.topic,
                action=action,
                score=pref.strength.score,
                description=f"Learned personal preference ({pref.source}, confidence={pref.confidence:.0%}).",
                source="personal_profile",
            )
        )
    return EditingRules(name="personal_profile", rules=rules)


def build_effective_rules(
    base_rules: Optional[EditingRules] = None,
    game_profile_rules: Optional[EditingRules] = None,
    personal_profile: Optional[PersonalEditingProfile] = None,
) -> EditingRules:
    """Merge default rules -> game/style profile -> personal preferences, in that priority order.

    Any argument may be omitted; omitting all of them reproduces plain
    :meth:`ai.editing_rules.EditingRules.default` behavior, so this is a
    strict superset of pre-4.5 rule building.
    """
    effective = base_rules or EditingRules.default()

    if game_profile_rules is not None and game_profile_rules.rules:
        effective = effective.merge_overlay(game_profile_rules)

    if personal_profile is not None:
        personal_rules = personal_profile_to_rules(personal_profile)
        if personal_rules.rules:
            effective = effective.merge_overlay(personal_rules)

    effective.name = "effective"
    return effective
