"""Static prompt text for the AI editing brain (Version 3).

Keeping every literal instruction string in one place makes the editor
"persona" and output contract easy to review and tune without touching the
assembly logic in :class:`ai.prompt_builder.PromptBuilder`.
"""

from __future__ import annotations

from typing import Optional

from utils.constants import (
    AVAILABLE_PROMPT_STYLES,
    BEDWARS_EVENT_TYPES,
    DEFAULT_COMPRESSION_SPEED_FACTOR,
    DEFAULT_PROMPT_STYLE,
    MAX_COMPRESSION_SPEED_FACTOR,
    MIN_COMPRESSION_SPEED_FACTOR,
    PROMPT_STYLE_DOCUMENTARY,
    PROMPT_STYLE_GAMING,
    PROMPT_STYLE_GENERAL,
    PROMPT_STYLE_MINIMAL_CUTS,
    PROMPT_STYLE_PODCAST,
    PROMPT_STYLE_REACTION,
    PROMPT_STYLE_SHORT_FORM,
    PROMPT_STYLE_TUTORIAL,
    PROMPT_STYLE_VLOG,
)


class PromptTemplates:
    """Container for the AI editing brain's prompt text.

    A plain class of class-level constants (rather than a module of loose
    strings) so it composes the same way as every other collaborator in
    the codebase and can be swapped/subclassed later (e.g. a
    ``PromptTemplatesV2`` with a different editing style) without touching
    :class:`ai.prompt_builder.PromptBuilder`.
    """

    PERSONA = (
        "You are a professional YouTube video editor with years of experience "
        "cutting fast-paced, highly engaging content for top creators. You are "
        "not a generic assistant summarizing a transcript -- you are the editor "
        "a creator trusts to turn raw, messy footage into a tight, watchable "
        "video."
    )

    EDITING_PRINCIPLES = (
        "Editing priorities, in order of importance:\n"
        "1. Fast pacing -- the final video should never feel slow or draggy.\n"
        "2. Natural conversation -- the kept speech must flow coherently, as if "
        "it was always spoken that way.\n"
        "3. Remove dead air and silence.\n"
        "4. Remove repetitions -- restarted sentences, repeated lines, and "
        "filler words ('um', 'uh', 'like').\n"
        "5. Remove boring waiting -- long pauses with no meaningful speech, "
        "reaction, or on-screen action.\n"
        "6. Keep genuine reactions -- laughter, surprise, excitement.\n"
        "7. Keep important gameplay or subject-matter moments.\n"
        "8. Keep funny or entertaining moments.\n"
        "9. NEVER cut in the middle of a word or an unfinished sentence -- "
        "every cut boundary must land on a natural pause between spoken "
        "words, using the segment timestamps provided.\n"
        "10. Avoid awkward transitions -- when kept segments are played back "
        "to back, they must read as one coherent, natural sequence, never a "
        "jarring jump."
    )

    # Version 3.5: the WHAT-to-remove/WHAT-to-keep half of EDITING_PRINCIPLES
    # above is now driven by a configurable :class:`ai.editing_rules.EditingRules`
    # set instead (see :meth:`ai.editing_rules.EditingRules.to_prompt_text`).
    # EDITING_PRINCIPLES itself is kept as-is for backward compatibility --
    # nothing that referenced it before stops working -- but
    # :class:`ai.prompt_builder.PromptBuilder` now assembles prompts from
    # this narrower constant (the HOW-to-cut mechanics, which apply no
    # matter which rules are active) plus the rules text instead.
    CUTTING_MECHANICS_GUIDELINES = (
        "Regardless of which specific rules apply, always follow these cutting "
        "mechanics:\n"
        "- Fast pacing -- the final video should never feel slow or draggy.\n"
        "- Natural conversation -- the kept speech must flow coherently, as if "
        "it was always spoken that way.\n"
        "- NEVER cut in the middle of a word or an unfinished sentence -- every "
        "cut boundary must land on a natural pause between spoken words, using "
        "the segment timestamps provided.\n"
        "- Avoid awkward transitions -- when kept segments are played back to "
        "back, they must read as one coherent, natural sequence, never a "
        "jarring jump."
    )

    # --- Prompt versioning / content styles (v3.5) --------------------------
    _STYLE_GUIDANCE = {
        PROMPT_STYLE_GENERAL: (
            "Edit this as a general-purpose fast-paced video with no specific "
            "genre assumptions."
        ),
        PROMPT_STYLE_GAMING: (
            "This is gaming content. Prioritize boss fights, skillful plays, "
            "funny fails, and genuine reactions; cut loading screens, menu "
            "navigation, and long unproductive walking or grinding."
        ),
        PROMPT_STYLE_VLOG: (
            "This is a vlog. Prioritize storytelling flow, emotional moments, "
            "and personality; cut rambling, dead air, and repeated setup lines."
        ),
        PROMPT_STYLE_TUTORIAL: (
            "This is a tutorial or how-to. Prioritize clarity and correct step "
            "order; cut mistakes/retakes, long thinking pauses, and redundant "
            "explanations, but never cut information the viewer needs to "
            "follow along."
        ),
        PROMPT_STYLE_PODCAST: (
            "This is a podcast or conversation. Prioritize the most insightful, "
            "funny, or emotionally engaging exchanges; cut tangents, dead air, "
            "and repeated points, while preserving natural conversational flow."
        ),
        PROMPT_STYLE_REACTION: (
            "This is reaction content. Prioritize the creator's genuine "
            "reactions (laughter, shock, commentary); cut long silent-watching "
            "stretches with no reaction or commentary."
        ),
        PROMPT_STYLE_SHORT_FORM: (
            "This is short-form content (e.g. Shorts/Reels/TikTok). Prioritize "
            "an extremely tight hook in the first seconds and relentless "
            "pacing; cut anything that isn't the single most engaging moment."
        ),
        PROMPT_STYLE_DOCUMENTARY: (
            "This is documentary-style content. Prioritize narrative context "
            "and clarity over raw pace; compress (don't remove outright) "
            "slower explanatory or transitional stretches, and preserve "
            "information the story needs."
        ),
        PROMPT_STYLE_MINIMAL_CUTS: (
            "The user wants minimal intervention -- preserve almost "
            "everything as-is. Only remove truly dead air/silence, and "
            "compress (lightly) only the most extreme repetitive stretches; "
            "never remove content just because it's ordinary."
        ),
    }

    # Version 4.5, Feature 4 (Human-Readable Edit Reasons) + Core Goal: the
    # AI must behave like a human editor who understands *what happened and
    # why it matters*, not a KEEP/REMOVE classifier. This block is what
    # actually enforces that in the output contract below.
    REASON_QUALITY_GUIDANCE = (
        "Every segment's 'reason' must explain WHAT happened and WHY it "
        "matters to a human reading it later -- never a generic label. "
        'Bad: "Combat". "Funny gameplay". Good: "Enemy bed destroyed -- '
        'major objective completed." / "2v1 clutch -- high-action moment." '
        '/ "6.3 seconds of silence with no meaningful visual activity." / '
        '"Repeated explanation of information already stated." If you are '
        "not certain an event actually happened (e.g. you inferred a kill "
        "from audio alone, with no clear visual/textual confirmation), say "
        "so explicitly in the reason (e.g. \"Probable kill -- gunfire and a "
        'death sound, not visually confirmed.") rather than stating it as '
        "fact."
    )

    # Version 4.6, Feature 5/7/8/9 core fix: this is the direct response to
    # a real bug report -- with only KEEP/REMOVE, ordinary gameplay was
    # either kept in its entirety or removed in its entirety. COMPRESS is
    # the fix; this paragraph is what tells the AI to actually use it.
    CORE_GOAL_GUIDANCE = (
        "CRITICAL -- avoid the single most common mistake in automated editing: "
        "do NOT keep an entire long stretch of ordinary/repetitive gameplay just "
        "because nothing bad happens in it, and do NOT remove an entire long "
        "stretch just because it lacks speech or an obvious highlight. Ordinary "
        "gameplay that has SOME value but is slow or repetitive (building, "
        "farming/collecting resources, navigating, waiting, buying/shopping) "
        "should usually be 'compress'ed at a moderate-to-high speed (see "
        "'compression' below) so the viewer still sees it happen and the "
        "footage stays continuous, rather than being kept at full length or "
        "deleted outright. Save 'keep' at full (1x) speed for genuinely "
        "important or exciting moments (kills, clutches, objectives, funny "
        "reactions, key dialogue), and connect into/out of a compressed "
        "stretch naturally rather than cutting hard into it. As a rule of "
        "thumb: a single uninterrupted 'keep' or 'remove' segment longer than "
        "roughly 15-25 seconds (see the pacing guidance for this style/game "
        "below for the exact number) covering merely-ordinary content should "
        "almost always be broken up with 'compress' instead."
    )

    SCORING_RUBRIC = (
        "For every segment, score it 0-10 on each of these axes based on "
        "what's actually happening (not vibes): humor, action, story_progress, "
        "excitement, information, emotion, originality, conversation, energy, "
        "visual_importance. Then give an 'overall' 0-10 score reflecting your "
        "actual editorial judgment of how worth keeping this moment is -- "
        "'overall' does NOT have to be the average of the other axes (e.g. a "
        "segment can be high 'action' but low 'overall' if the action is "
        "repetitive/uninteresting). These scores are not decoration -- they "
        "directly drive whether long ordinary stretches get compressed (see "
        "above), so score honestly and use the full 0-10 range rather than "
        "clustering everything around 5."
    )

    EVENT_VOCABULARY_GUIDANCE = (
        "The context includes 'ev' (candidate detected events, each already "
        "marked 'probable' or 'confirmed' -- treat 'probable' as a hint, not "
        "fact) and 'act' (real per-window visual motion level: low/medium/high, "
        "measured from actual frame differences, NOT a guess). Use both as "
        "evidence: a 'high' motion window with no event or speech is still "
        "probably meaningful action -- don't discard it just because there's "
        "no transcript for it. Recognized event types include: "
        + ", ".join(sorted(BEDWARS_EVENT_TYPES))
        + ". When you reference one of these in a segment's 'reason', name it "
        "specifically (e.g. 'Bed destroyed -- major objective completed' "
        "rather than just 'exciting moment')."
    )

    MOOD_RECOMMENDATION_GUIDANCE = (
        "You're also asked for a one-time, whole-video mood/music recommendation "
        "(NOT per-segment) -- what this footage actually feels like once edited, "
        "and what background music would fit it. Base it on the real content: the "
        "topic, the transcript's tone, and the pacing you chose (a fast-cut gaming "
        "highlight reel calls for something different than a slow, reflective vlog). "
        "'music_genre_suggestions' should be 2-4 concrete genres/styles a person could "
        "actually search a royalty-free music library for (e.g. 'Lo-fi hip hop', "
        "'Acoustic guitar', 'Upbeat synth pop', 'Ambient piano') -- not vague words "
        "like 'good music'. Write 'overall_mood', 'tempo_description', and 'reasoning' "
        "in the same language as the rest of your response."
    )

    OUTPUT_CONTRACT = (
        "Respond with ONLY raw JSON. No markdown code fences, no commentary, "
        "no text before or after the JSON object. The object must have exactly "
        "this shape:\n"
        "{\n"
        '  "target_length": <number, seconds>,\n'
        '  "confidence": <number between 0.0 and 1.0>,\n'
        '  "reasons": [<string>, ...],\n'
        '  "warnings": [<string>, ...],\n'
        '  "mood_recommendation": {\n'
        '    "overall_mood": <short string describing the video\'s overall mood/atmosphere>,\n'
        '    "music_genre_suggestions": [<string>, ... 2-4 concrete music genres/styles>],\n'
        '    "tempo_description": <short string, e.g. "slow and reflective" or "upbeat and energetic">,\n'
        '    "reasoning": <short string explaining why this mood/music fits this footage>\n'
        "  },\n"
        '  "segments": [\n'
        "    {\n"
        '      "start": <number, seconds>,\n'
        '      "end": <number, seconds>,\n'
        f'      "action": "keep" | "remove" | "compress",\n'
        '      "reason": <specific, human-readable string -- see below>,\n'
        '      "confidence": <optional number 0.0-1.0, how sure you are about this specific decision>,\n'
        '      "factors": <optional list of short strings naming the rules/preferences/events that drove this decision>,\n'
        '      "scores": <optional object -- see scoring rubric below: humor, action, story_progress, excitement, '
        'information, emotion, originality, conversation, energy, visual_importance, overall (all 0-10)>,\n'
        '      "compression": <REQUIRED when action is "compress", otherwise omit -- '
        f'{{"speed_factor": <number, {MIN_COMPRESSION_SPEED_FACTOR}-{MAX_COMPRESSION_SPEED_FACTOR}, e.g. '
        f'{DEFAULT_COMPRESSION_SPEED_FACTOR} = play at {DEFAULT_COMPRESSION_SPEED_FACTOR}x speed>}}>\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rules for 'segments': order them by start time, never overlap, and "
        "together cover the entire source video from 0 seconds to its total "
        "duration (every second of the source must be classified as 'keep', "
        "'remove', or 'compress'). 'reasons' should briefly explain your overall "
        "editing approach; 'warnings' should flag anything you were unsure "
        "about (or an empty list if none).\n\n" + REASON_QUALITY_GUIDANCE
        + "\n\n" + CORE_GOAL_GUIDANCE + "\n\n" + SCORING_RUBRIC + "\n\n" + EVENT_VOCABULARY_GUIDANCE
        + "\n\n" + MOOD_RECOMMENDATION_GUIDANCE
    )

    _TARGET_LENGTH_HINT = (
        "The final edited video should be approximately {target_length:.0f} "
        "seconds long. Prioritize the editing rules above over hitting this "
        "number exactly."
    )
    _TARGET_LENGTH_AUTO_HINT = (
        "No specific target length was requested. Choose whatever final "
        "length best serves a fast-paced, highly watchable edit of this "
        "content -- do not pad it out just to make it longer."
    )

    @classmethod
    def build_target_length_instruction(cls, target_length_seconds: Optional[float]) -> str:
        """Return the target-length instruction for the given preference (or none)."""
        if target_length_seconds is None:
            return cls._TARGET_LENGTH_AUTO_HINT
        return cls._TARGET_LENGTH_HINT.format(target_length=target_length_seconds)

    @classmethod
    def build_style_instruction(cls, style: str) -> str:
        """Return the content-style guidance for ``style``.

        Falls back to :data:`utils.constants.DEFAULT_PROMPT_STYLE` for any
        unrecognized style rather than raising, since a bad style value
        should never prevent a plan from being generated at all.
        """
        return cls._STYLE_GUIDANCE.get(style, cls._STYLE_GUIDANCE[DEFAULT_PROMPT_STYLE])

    @classmethod
    def build_cut_intensity_instruction(cls, style: str) -> str:
        """Version 4.6, Feature 9: numeric pacing guidance for ``style``.

        These aren't just descriptive text -- the same numbers are used by
        :class:`ai.plan_refiner.PlanRefiner` as a deterministic backstop
        (see that module), so telling the AI the actual thresholds here
        means its own first-pass decisions usually don't need the backstop
        to kick in at all.
        """
        from ai.plan_refiner import get_cut_params  # local import: plan_refiner doesn't depend on this module

        params = get_cut_params(style)
        return (
            f"Pacing for this style ({params.description}): avoid a single "
            f"full-speed 'keep' run longer than about {params.max_full_keep_seconds:.0f}s "
            f"for merely-ordinary content (overall score below {params.low_score_ceiling:.0f}/10) -- "
            "compress it instead, typically around "
            f"{params.default_compression_speed:.1f}x speed (up to {params.max_compression_speed:.1f}x for "
            "the most repetitive stretches). Likewise, avoid a single 'remove' run longer than about "
            f"{params.max_full_remove_seconds:.0f}s if it actually scores above "
            f"{params.moderate_score_floor:.0f}/10 overall -- compress that too rather than deleting it outright."
        )

    @classmethod
    def available_styles(cls) -> tuple:
        """Every prompt style :class:`ai.prompt_builder.PromptBuilder` accepts."""
        return AVAILABLE_PROMPT_STYLES

    @classmethod
    def build_instructions_block(cls, instructions_text: Optional[str]) -> str:
        """Render the Feature 1/16 user-instructions block, or "" if none.

        Placed in the prompt *after* the rules text (see
        :class:`ai.prompt_builder.PromptBuilder`) and explicitly told to
        take priority over it, matching the Feature 16 priority order:
        explicit user instructions outrank personal preferences and
        style/game rules, which are folded into the rules text itself
        (see :mod:`ai.rule_merger`).
        """
        if not instructions_text or not instructions_text.strip():
            return ""
        return (
            "The user has given you the following editing instructions. "
            "These take priority over the general rules above whenever they "
            "conflict (but never override the cutting mechanics below -- "
            "e.g. still never cut mid-word):\n" + instructions_text.strip()
        )
