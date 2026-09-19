"""Game/content-aware editing profiles (Version 4.5, Feature 6).

A :class:`GameProfile` is just a named :class:`ai.editing_rules.EditingRules`
overlay: a small set of extra KEEP/REMOVE rules specific to a game or
content type (e.g. "Bed Destroyed" for Minecraft Bedwars, "Suspense" for
horror games). Profiles never touch the core planner -- they are layered
on top of :meth:`ai.editing_rules.EditingRules.default` via
:meth:`ai.editing_rules.EditingRules.merge_overlay`, exactly like personal
preferences and editing instructions (see :mod:`ai.rule_merger`), so
nothing about game awareness is hardcoded into :class:`ai.edit_planner.EditPlanner`
or :class:`ai.prompt_builder.PromptBuilder`.

Profiles are data, not code: :data:`GAME_PROFILES` is a plain registry that
callers (e.g. a future Settings page) can extend at runtime with
:func:`register_profile` -- "Profiles should be configurable and
extensible" per the Version 4.5 spec.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from ai.editing_rules import EditingRules
from models.editing_rule import EditingRule
from utils.constants import EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE

GAME_PROFILE_NONE = "none"
GAME_PROFILE_MINECRAFT_BEDWARS = "minecraft_bedwars"
GAME_PROFILE_MINECRAFT_SURVIVAL = "minecraft_survival"
GAME_PROFILE_VALORANT = "valorant"
GAME_PROFILE_LEAGUE_OF_LEGENDS = "league_of_legends"
GAME_PROFILE_HORROR = "horror"
GAME_PROFILE_REACTION = "reaction"
GAME_PROFILE_TUTORIAL = "tutorial"
GAME_PROFILE_PODCAST = "podcast"
GAME_PROFILE_VLOG = "vlog"
GAME_PROFILE_DOCUMENTARY = "documentary"
GAME_PROFILE_VARIETY = "variety"


def _rule(name: str, action: str, score: float, description: str) -> EditingRule:
    return EditingRule(name=name, action=action, score=score, description=description, source="game_profile")


def _profile(name: str, rules: Iterable[EditingRule]) -> EditingRules:
    return EditingRules(name=name, rules=list(rules))


GAME_PROFILES: Dict[str, EditingRules] = {
    GAME_PROFILE_NONE: _profile(GAME_PROFILE_NONE, []),
    GAME_PROFILE_MINECRAFT_BEDWARS: _profile(
        GAME_PROFILE_MINECRAFT_BEDWARS,
        [
            _rule("Bed Destroyed", EDIT_ACTION_KEEP, 7, "A team's bed was broken -- a major objective."),
            _rule("Kill", EDIT_ACTION_KEEP, 6, "A player elimination."),
            _rule("Clutch", EDIT_ACTION_KEEP, 7, "A high-pressure comeback or skillful multi-kill."),
            _rule("Final Fight", EDIT_ACTION_KEEP, 8, "The last fight of the match."),
            _rule("Fall", EDIT_ACTION_KEEP, 3, "A notable fall/void death, funny or dramatic."),
            _rule("Important Item", EDIT_ACTION_KEEP, 4, "Obtaining a game-changing item (e.g. diamond gear, trap)."),
            _rule("Victory", EDIT_ACTION_KEEP, 8, "The team wins the match."),
            _rule("Defeat", EDIT_ACTION_KEEP, 5, "The team's bed/base falls or the team is eliminated."),
            _rule("Funny Reaction", EDIT_ACTION_KEEP, 5, "A funny reaction to a Bedwars-specific moment."),
            _rule("Resource Grinding", EDIT_ACTION_REMOVE, -5, "Routine, uneventful resource collection."),
            _rule("Bridge Building", EDIT_ACTION_REMOVE, -3, "Long, uneventful bridging with no combat."),
        ],
    ),
    GAME_PROFILE_MINECRAFT_SURVIVAL: _profile(
        GAME_PROFILE_MINECRAFT_SURVIVAL,
        [
            _rule("Boss Fight", EDIT_ACTION_KEEP, 7, "Fighting a boss (Ender Dragon, Wither, etc.)."),
            _rule("Rare Discovery", EDIT_ACTION_KEEP, 6, "Finding a rare item, structure, or biome."),
            _rule("Building Progress", EDIT_ACTION_KEEP, 3, "Visible, meaningful progress on a build."),
            _rule("Near-Death Moment", EDIT_ACTION_KEEP, 5, "A close call that nearly ended the playthrough."),
            _rule("Mining/Farming Grind", EDIT_ACTION_REMOVE, -5, "Long, repetitive resource gathering."),
            _rule("Long Navigation", EDIT_ACTION_REMOVE, -4, "Extended travel with nothing happening."),
            _rule("Menu/Inventory Fiddling", EDIT_ACTION_REMOVE, -3, "Sorting inventory or menus with no commentary."),
        ],
    ),
    GAME_PROFILE_VALORANT: _profile(
        GAME_PROFILE_VALORANT,
        [
            _rule("Clutch Round", EDIT_ACTION_KEEP, 8, "Winning a round outnumbered."),
            _rule("Ace", EDIT_ACTION_KEEP, 8, "Eliminating the entire enemy team single-handedly."),
            _rule("Multi-Kill", EDIT_ACTION_KEEP, 6, "Two or more kills in quick succession."),
            _rule("Round Win", EDIT_ACTION_KEEP, 3, "A round won, even without a standout play."),
            _rule("Match Point", EDIT_ACTION_KEEP, 6, "The round that decides the match."),
            _rule("Buy Phase", EDIT_ACTION_REMOVE, -5, "Routine weapon/ability purchasing with no discussion."),
            _rule("Rotating/Walking", EDIT_ACTION_REMOVE, -4, "Uneventful rotation with no engagement."),
        ],
    ),
    GAME_PROFILE_LEAGUE_OF_LEGENDS: _profile(
        GAME_PROFILE_LEAGUE_OF_LEGENDS,
        [
            _rule("Team Fight", EDIT_ACTION_KEEP, 7, "A multi-player fight, especially a decisive one."),
            _rule("Objective Secured", EDIT_ACTION_KEEP, 6, "Baron, dragon, or turret taken."),
            _rule("Outplay", EDIT_ACTION_KEEP, 7, "A skillful individual play or juke."),
            _rule("Pentakill", EDIT_ACTION_KEEP, 9, "A five-kill streak in one fight."),
            _rule("Laning Farm", EDIT_ACTION_REMOVE, -5, "Routine minion farming with no action."),
            _rule("Backing/Shopping", EDIT_ACTION_REMOVE, -4, "Returning to base to shop with no commentary."),
        ],
    ),
    GAME_PROFILE_HORROR: _profile(
        GAME_PROFILE_HORROR,
        [
            _rule("Suspense", EDIT_ACTION_KEEP, 6, "Building tension before a scare or reveal."),
            _rule("Reactions", EDIT_ACTION_KEEP, 7, "Genuine fright reactions -- jumps, screams, nervous laughter."),
            _rule("Important Discovery", EDIT_ACTION_KEEP, 5, "Finding a key item, note, or clue."),
            _rule("Scary Moment", EDIT_ACTION_KEEP, 8, "A jump scare or dread-inducing event."),
            _rule("Story Progression", EDIT_ACTION_KEEP, 6, "Plot-relevant dialogue or cutscenes."),
            _rule("Slow Wandering", EDIT_ACTION_REMOVE, -4, "Aimless exploration with no tension or discovery."),
        ],
    ),
    GAME_PROFILE_REACTION: _profile(
        GAME_PROFILE_REACTION,
        [
            _rule("Genuine Reaction", EDIT_ACTION_KEEP, 8, "A strong, authentic reaction to the content."),
            _rule("Commentary", EDIT_ACTION_KEEP, 5, "Insightful or funny commentary over the content."),
            _rule("Silent Watching", EDIT_ACTION_REMOVE, -6, "Extended silent watching with no reaction or comment."),
        ],
    ),
    GAME_PROFILE_TUTORIAL: _profile(
        GAME_PROFILE_TUTORIAL,
        [
            _rule("Key Step", EDIT_ACTION_KEEP, 7, "A necessary step the viewer must follow."),
            _rule("Clarifying Explanation", EDIT_ACTION_KEEP, 4, "An explanation that adds real understanding."),
            _rule("Mistake/Retake", EDIT_ACTION_REMOVE, -6, "A flubbed take redone immediately after."),
            _rule("Redundant Explanation", EDIT_ACTION_REMOVE, -5, "Repeating information already clearly stated."),
            _rule("Long Thinking Pause", EDIT_ACTION_REMOVE, -4, "Silent thinking with no on-screen progress."),
        ],
    ),
    GAME_PROFILE_PODCAST: _profile(
        GAME_PROFILE_PODCAST,
        [
            _rule("Insightful Exchange", EDIT_ACTION_KEEP, 6, "A genuinely interesting or funny exchange."),
            _rule("Tangent", EDIT_ACTION_REMOVE, -3, "An unrelated digression that doesn't add value."),
            _rule("Repeated Point", EDIT_ACTION_REMOVE, -4, "A point already made earlier in the conversation."),
        ],
    ),
    GAME_PROFILE_VLOG: _profile(
        GAME_PROFILE_VLOG,
        [
            _rule("Story Beat", EDIT_ACTION_KEEP, 6, "A moment that moves the day's story forward."),
            _rule("Emotional Moment", EDIT_ACTION_KEEP, 6, "A genuine emotional beat."),
            _rule("Rambling", EDIT_ACTION_REMOVE, -4, "Unfocused rambling with no story or entertainment value."),
        ],
    ),
    # Version 5.3: added alongside folder-based batch import/analysis so
    # raw documentary and variety-show footage each get rules tuned to how
    # that footage is actually shot, instead of falling back to the
    # gaming-flavored default set. See ai.content_classifier for the
    # heuristic that auto-selects one of these two for unattended folder
    # analysis; either can also be picked by hand for a single video like
    # any other profile.
    GAME_PROFILE_DOCUMENTARY: _profile(
        GAME_PROFILE_DOCUMENTARY,
        [
            _rule("Key Narration", EDIT_ACTION_KEEP, 6, "Narration or interview speech that advances the story or explains a fact."),
            _rule("Emotional Testimony", EDIT_ACTION_KEEP, 7, "A subject's genuine, emotionally significant statement."),
            _rule("Establishing Shot", EDIT_ACTION_KEEP, 3, "A scenic/establishing shot that sets location or mood, even if silent."),
            _rule("Reveal/Turning Point", EDIT_ACTION_KEEP, 8, "A fact, twist, or moment that changes the viewer's understanding."),
            _rule("Archival/B-Roll Detail", EDIT_ACTION_KEEP, 4, "Illustrative b-roll or archival footage tied to what's being said."),
            _rule("Off-Topic Chatter", EDIT_ACTION_REMOVE, -4, "Crew/subject conversation unrelated to the documentary's subject."),
            _rule("Repeated Take", EDIT_ACTION_REMOVE, -6, "The same line or beat re-recorded and said again shortly after."),
            _rule("Long Unbroken Silence", EDIT_ACTION_REMOVE, -5, "Extended quiet with no narration and no notable visual event."),
            _rule("Camera/Mic Setup", EDIT_ACTION_REMOVE, -7, "Visible equipment adjustment, mic checks, or 'are we rolling' moments."),
        ],
    ),
    GAME_PROFILE_VARIETY: _profile(
        GAME_PROFILE_VARIETY,
        [
            _rule("Big Reaction", EDIT_ACTION_KEEP, 8, "Loud laughter, shock, or an exaggerated reaction from a cast member."),
            _rule("Punchline/Running Gag", EDIT_ACTION_KEEP, 7, "A joke lands, or a running gag pays off."),
            _rule("Cast Banter", EDIT_ACTION_KEEP, 5, "Lively back-and-forth between cast members."),
            _rule("Game/Mission Twist", EDIT_ACTION_KEEP, 7, "A rule reveal, twist, or turning point in a mission/game segment."),
            _rule("Confessional/Interview Cut", EDIT_ACTION_KEEP, 5, "A to-camera confessional reacting to what just happened."),
            _rule("Awkward Silence", EDIT_ACTION_REMOVE, -5, "A flat pause with no reaction, joke, or commentary."),
            _rule("Rules Explanation Repeat", EDIT_ACTION_REMOVE, -4, "Game/segment rules being re-explained after they were already covered."),
            _rule("Dead Downtime", EDIT_ACTION_REMOVE, -4, "Waiting between segments with no banter or reaction."),
        ],
    ),
}

GAME_PROFILE_DISPLAY_NAMES = {
    GAME_PROFILE_NONE: "None (default rules only)",
    GAME_PROFILE_MINECRAFT_BEDWARS: "Minecraft Bedwars",
    GAME_PROFILE_MINECRAFT_SURVIVAL: "Minecraft Survival",
    GAME_PROFILE_VALORANT: "Valorant",
    GAME_PROFILE_LEAGUE_OF_LEGENDS: "League of Legends",
    GAME_PROFILE_HORROR: "Horror Games",
    GAME_PROFILE_REACTION: "Reaction",
    GAME_PROFILE_TUTORIAL: "Tutorial",
    GAME_PROFILE_PODCAST: "Podcast",
    GAME_PROFILE_VLOG: "Vlog",
    GAME_PROFILE_DOCUMENTARY: "Documentary",
    GAME_PROFILE_VARIETY: "Variety / Entertainment (예능)",
}


def available_game_profiles() -> Dict[str, str]:
    """``{profile_id: display_name}`` for every registered profile."""
    return dict(GAME_PROFILE_DISPLAY_NAMES)


def get_game_profile(profile_id: Optional[str]) -> EditingRules:
    """Return the :class:`EditingRules` overlay for ``profile_id``.

    Falls back to the empty "none" profile for an unrecognized or missing
    id rather than raising, since an unknown profile should never block
    edit-plan generation.
    """
    if not profile_id or profile_id not in GAME_PROFILES:
        return GAME_PROFILES[GAME_PROFILE_NONE]
    return GAME_PROFILES[profile_id]


def register_profile(profile_id: str, display_name: str, rules: EditingRules) -> None:
    """Register a new (or replace an existing) game/content profile at runtime.

    This is what makes profiles "configurable and extensible" rather than
    hardcoded: a future Settings/Profile-editor page can call this to add
    a custom profile without any change to this module.
    """
    GAME_PROFILES[profile_id] = rules
    GAME_PROFILE_DISPLAY_NAMES[profile_id] = display_name
