"""Assembles the final prompt sent to the AI editing brain provider."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from ai.context_builder import ContextBuilder
from ai.editing_rules import EditingRules
from ai.prompt_templates import PromptTemplates
from services.logging_service import LoggingService
from services.token_usage_service import PromptReduction, TokenUsageService
from utils.constants import AVAILABLE_PROMPT_STYLES, DEFAULT_PROMPT_STYLE


@dataclass(slots=True)
class PromptBuildResult:
    """A built prompt plus the size/reduction stats behind it (Feature 4).

    ``system_prompt``/``user_prompt`` mirror the shape a Prompt Debug
    window (Feature 15) wants to show separately; AutoCutAI's providers
    only accept a single flat conversation history (see
    :class:`ai.base_provider.AIProvider`), so ``full_prompt`` -- the two
    concatenated -- is what's actually sent, exactly as before Version 4.
    """

    full_prompt: str
    system_prompt: str
    user_prompt: str
    reduction: PromptReduction


class PromptBuilder:
    """Builds the single prompt string sent to the provider for one edit plan.

    The prompt is deliberately assembled as one self-contained user
    message (persona + style guidance + editing rules + cutting mechanics
    + strict JSON output contract + the video's transcript context)
    instead of relying on a provider-specific "system role".
    :class:`ai.base_provider.AIProvider` only exposes a plain conversation
    history, so building everything into one message keeps this class
    fully provider-agnostic -- it works identically whether the underlying
    provider is Gemini or anything implemented later.

    Version 3.5 adds two optional, backward-compatible knobs:

    - ``rules``: a :class:`ai.editing_rules.EditingRules` set. Defaults to
      :meth:`ai.editing_rules.EditingRules.default`, which reproduces
      Version 3's built-in editing principles, so existing 2-argument
      calls (``build(context, target_length_seconds)``) behave the same
      as before.
    - ``style``: a prompt template/content style (gaming, vlog, tutorial,
      podcast, reaction, short-form, or the default general style).
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("ai.prompt_builder")

    def build(
        self,
        context: dict,
        target_length_seconds: Optional[float] = None,
        rules: Optional[EditingRules] = None,
        style: str = DEFAULT_PROMPT_STYLE,
        instructions_text: Optional[str] = None,
    ) -> str:
        """Build the full prompt string for one edit-planning request.

        Thin backward-compatible wrapper around :meth:`build_with_stats`
        for callers that only want the prompt text itself.

        Args:
            context: The dict produced by
                :class:`ai.context_builder.ContextBuilder` (compact or
                verbose shape -- both are accepted; this method only ever
                serializes it, never inspects its keys).
            target_length_seconds: Optional desired final video length. If
                omitted, the AI is instructed to choose its own length.
            rules: The editing rules to render into the prompt. Defaults to
                :meth:`ai.editing_rules.EditingRules.default` if omitted.
            style: Which content-style template to use (see
                ``utils.constants.AVAILABLE_PROMPT_STYLES``). Falls back to
                the default general style for an unrecognized value.
            instructions_text: Optional rendered Feature 1/16 user
                instructions block (see
                ``models.editing_instructions.EditingInstructionSet.to_prompt_text``).
        """
        return self.build_with_stats(context, target_length_seconds, rules, style, instructions_text=instructions_text).full_prompt

    def build_with_stats(
        self,
        context: dict,
        target_length_seconds: Optional[float] = None,
        rules: Optional[EditingRules] = None,
        style: str = DEFAULT_PROMPT_STYLE,
        compact: bool = True,
        instructions_text: Optional[str] = None,
    ) -> PromptBuildResult:
        """Build the prompt plus size/reduction stats (Version 4, Feature 4).

        ``compact`` only controls whether the *legend line* explaining the
        short JSON keys is included -- the ``context`` dict itself was
        already built compact-or-not by :class:`ai.context_builder.ContextBuilder`;
        this method just adapts its own wording to match so the model is
        never left guessing what ``"s"``/``"e"``/``"t"`` mean.
        """
        if style not in AVAILABLE_PROMPT_STYLES:
            self._logger.warning("Unknown prompt style '%s'; falling back to '%s'.", style, DEFAULT_PROMPT_STYLE)
            style = DEFAULT_PROMPT_STYLE
        self._logger.info("Prompt template selected: %s", style)

        active_rules = rules or EditingRules.default()
        rules_text = active_rules.to_prompt_text()  # also logs rule selection

        target_length_instruction = PromptTemplates.build_target_length_instruction(target_length_seconds)
        style_instruction = PromptTemplates.build_style_instruction(style)
        cut_intensity_instruction = PromptTemplates.build_cut_intensity_instruction(style)
        instructions_block = PromptTemplates.build_instructions_block(instructions_text)
        context_json = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        is_compact_shape = "seg" in context

        instructions_section = f"{instructions_block}\n\n" if instructions_block else ""
        system_prompt = (
            f"{PromptTemplates.PERSONA}\n\n"
            f"{style_instruction}\n\n"
            f"{cut_intensity_instruction}\n\n"
            f"{rules_text}\n\n"
            f"{instructions_section}"
            f"{PromptTemplates.CUTTING_MECHANICS_GUIDELINES}\n\n"
            f"{PromptTemplates.OUTPUT_CONTRACT}"
        )

        schema_line = (
            f"Source video transcript/metadata as JSON. {ContextBuilder.compact_schema_legend()}\n"
            if is_compact_shape
            else "Here is the source video's transcript and metadata as JSON "
            "(duration_seconds, language, and timestamped segments in seconds):\n"
        )
        user_prompt = f"{target_length_instruction}\n\n{schema_line}{context_json}"

        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        # Reduction is measured against what the exact same content would
        # cost using the original (pre-Version-4) verbose wording/keys, so
        # the percentage reflects Feature 4's actual, honest impact rather
        # than an arbitrary baseline.
        verbose_context_json = context_json if not is_compact_shape else json.dumps(
            self._expand_compact_context(context), ensure_ascii=False, separators=(",", ":")
        )
        # This mirrors *exactly* the prompt shape AutoCutAI generated before
        # Version 4 (see git history of this method): same template pieces,
        # same order, only the context JSON's keys/merging differ. Using
        # the real prior shape (not a padded strawman) is what makes
        # ``reduction_percent`` an honest, defensible number.
        verbose_prompt = (
            f"{PromptTemplates.PERSONA}\n\n{style_instruction}\n\n{rules_text}\n\n{instructions_section}"
            f"{PromptTemplates.CUTTING_MECHANICS_GUIDELINES}\n\n{target_length_instruction}\n\n"
            "Here is the source video's transcript and metadata as JSON "
            "(duration_seconds, language, and timestamped segments in seconds):\n"
            f"{verbose_context_json}\n\n{PromptTemplates.OUTPUT_CONTRACT}"
        )
        reduction = TokenUsageService.measure_reduction(verbose_prompt, full_prompt)

        self._logger.info(
            "Prompt generated: %d chars (~%d estimated tokens) | baseline=%d chars (~%d tokens) | "
            "reduction=%.1f%% | context=%d chars",
            len(full_prompt),
            TokenUsageService.estimate_tokens(full_prompt),
            len(verbose_prompt),
            TokenUsageService.estimate_tokens(verbose_prompt),
            reduction.reduction_percent,
            len(context_json),
        )
        return PromptBuildResult(
            full_prompt=full_prompt, system_prompt=system_prompt, user_prompt=user_prompt, reduction=reduction
        )

    @staticmethod
    def _expand_compact_context(context: dict) -> dict:
        """Reconstruct the verbose ``{"segments": [{"start", "end", "text"}]}`` shape.

        Used only to measure Feature 4's size reduction against a fair,
        equivalent baseline -- never sent to a provider.
        """
        return {
            "duration_seconds": context.get("d"),
            "language": context.get("lang"),
            "segment_count": len(context.get("seg", [])),
            "segments": [
                {"start": seg.get("s"), "end": seg.get("e"), "text": seg.get("t")}
                for seg in context.get("seg", [])
            ],
        }
