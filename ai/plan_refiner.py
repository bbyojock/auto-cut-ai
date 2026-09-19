"""Deterministic anti-monolith-cut safety net (Version 4.6, Features 5/7/9).

This module exists because of a real reported bug: with only binary
KEEP/REMOVE, the AI would either keep an entire long stretch of ordinary
gameplay (because *something* in it looked mildly interesting) or remove
an entire long stretch (because it wasn't obviously a highlight) -- both
wrong for "ordinary but not worthless" gameplay like building, farming,
or navigating in Bedwars.

:class:`ai.prompt_templates.PromptTemplates` already *asks* the AI not to
do this and to use COMPRESS for exactly this case (see
``REASON_QUALITY_GUIDANCE`` / ``CORE_GOAL_GUIDANCE``). But an LLM
following a prompt instruction is not a guarantee, and the user's bug
report is explicitly about this failing in practice. ``PlanRefiner`` is
the actual, code-level guarantee: given the Feature 5 multi-axis scores
the AI *did* return for each segment, it deterministically downgrades any
run that is both (a) longer than the active style's threshold and (b)
scored low/moderate, into a KEEP-COMPRESS-KEEP (or straight COMPRESS)
pattern -- regardless of whether the AI's own segmentation already tried
to do this.

If a segment has no ``scores`` at all (an older cached plan, or a
provider that ignored the scores field), the refiner intentionally does
nothing to it -- guessing at editorial merit with zero evidence would be
worse than leaving the AI's original decision alone. This is a
conscious, documented trade-off, not an oversight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from models.edit_plan import EditPlan, EditPlanSegment
from services.logging_service import LoggingService
from utils.constants import EDIT_ACTION_COMPRESS, EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE


@dataclass(frozen=True)
class CutIntensityParams:
    """Version 4.6, Feature 9: how aggressively a style compresses ordinary footage.

    All thresholds operate on Feature 5's 0-10 ``overall`` score.
    """

    max_full_keep_seconds: float       # a KEEP run longer than this is compress-eligible
    low_score_ceiling: float           # ...if its overall score is below this
    max_full_remove_seconds: float     # a REMOVE run longer than this is compress-eligible
    moderate_score_floor: float        # ...if its overall score is above this (i.e. not truly worthless)
    default_compression_speed: float
    max_compression_speed: float
    edge_keep_seconds: float = 2.5     # bookend kept at full speed for a natural transition
    description: str = ""


# Version 4.6, Feature 9: numeric pacing parameters per style, actually
# consumed by PlanRefiner (and echoed into the prompt by
# ai.prompt_templates) -- not just descriptive text the AI might ignore.
STYLE_CUT_PARAMS = {
    "fast_gaming": CutIntensityParams(12, 6.0, 6, 2.5, 3.0, 6.0, 2.0, "Aggressive: compress ordinary gameplay hard and fast."),
    "gaming": CutIntensityParams(15, 5.5, 8, 2.5, 2.5, 5.0, 2.5, "Balanced gaming pacing: compress repetitive stretches, keep highlights full-speed."),
    "relaxed_gaming": CutIntensityParams(35, 4.0, 15, 1.5, 1.5, 2.5, 3.0, "Gentle: only compress clearly dead/repetitive stretches."),
    "documentary": CutIntensityParams(60, 3.0, 20, 1.5, 1.3, 2.0, 3.0, "Preserve context; compress only long, low-value stretches."),
    "minimal_cuts": CutIntensityParams(1_000_000, 0.0, 1_000_000, 10.0, 1.25, 1.5, 1.0, "Almost never compress or remove -- preserve nearly everything."),
    "general": CutIntensityParams(20, 5.0, 10, 2.0, 2.0, 4.0, 2.5, "Balanced default pacing."),
    "vlog": CutIntensityParams(25, 4.5, 12, 2.0, 1.75, 3.0, 2.5, "Natural pacing with light compression of dead moments."),
    "tutorial": CutIntensityParams(30, 4.0, 15, 1.5, 1.5, 2.5, 2.0, "Preserve explanations; compress redundant/dead stretches."),
    "podcast": CutIntensityParams(40, 3.5, 20, 1.5, 1.3, 2.0, 2.0, "Conversation-friendly; compress tangents/repeats lightly."),
    "reaction": CutIntensityParams(15, 5.0, 8, 2.0, 2.0, 4.0, 2.0, "Keep reactions full-speed; compress silent watching."),
    "short_form": CutIntensityParams(8, 7.0, 5, 3.0, 4.0, 6.0, 1.0, "Extremely tight pacing for short-form content."),
}
_DEFAULT_CUT_PARAMS = STYLE_CUT_PARAMS["general"]


def get_cut_params(style: str) -> CutIntensityParams:
    return STYLE_CUT_PARAMS.get(style, _DEFAULT_CUT_PARAMS)


class PlanRefiner:
    """Applies :class:`CutIntensityParams` thresholds to a validated EditPlan."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("ai.plan_refiner")

    def refine(self, plan: EditPlan, style: str = "general") -> EditPlan:
        """Return a new plan with long, low-merit runs downgraded to COMPRESS.

        Never invents new information: only acts on segments that already
        carry Feature 5 ``scores``. Every change is recorded in the
        returned plan's ``warnings`` for Feature 11 (Explainability).
        """
        params = get_cut_params(style)
        notes: List[str] = []
        new_segments: List[EditPlanSegment] = []

        for segment in plan.all_segments_sorted:
            if segment.scores is None:
                new_segments.append(segment)
                continue

            overall = segment.scores.computed_overall()
            if segment.action == EDIT_ACTION_KEEP and segment.duration_seconds >= params.max_full_keep_seconds and overall < params.low_score_ceiling:
                new_segments.extend(self._downgrade_to_compress(segment, params, overall, notes, keep_edges=True))
            elif segment.action == EDIT_ACTION_REMOVE and segment.duration_seconds >= params.max_full_remove_seconds and overall > params.moderate_score_floor:
                new_segments.extend(self._downgrade_to_compress(segment, params, overall, notes, keep_edges=False))
            else:
                new_segments.append(segment)

        refined = EditPlan(
            target_length_seconds=plan.target_length_seconds,
            keep_segments=[s for s in new_segments if s.action == EDIT_ACTION_KEEP],
            remove_segments=[s for s in new_segments if s.action == EDIT_ACTION_REMOVE],
            compress_segments=[s for s in new_segments if s.action == EDIT_ACTION_COMPRESS],
            confidence=plan.confidence,
            reasons=list(plan.reasons),
            warnings=list(plan.warnings) + notes,
            processing_seconds=plan.processing_seconds,
            model_used=plan.model_used,
            created_at=plan.created_at,
        )
        if notes:
            self._logger.info("PlanRefiner (%s): applied %d compression override(s).", style, len(notes))
        return refined

    def _downgrade_to_compress(
        self, segment: EditPlanSegment, params: CutIntensityParams, overall: float, notes: List[str], keep_edges: bool,
    ) -> List[EditPlanSegment]:
        # Speed scales with how far below/above the threshold the score is,
        # so a truly dull 2/10 stretch compresses harder than a borderline
        # 4.5/10 one -- clamped to the style's own bounds (Feature 9).
        severity = max(0.0, (params.low_score_ceiling - overall) / max(params.low_score_ceiling, 1.0))
        speed = params.default_compression_speed + severity * (params.max_compression_speed - params.default_compression_speed)
        speed = max(params.default_compression_speed, min(params.max_compression_speed, speed))

        start, end = segment.start_seconds, segment.end_seconds
        edge = params.edge_keep_seconds if keep_edges else 0.0
        verb = "ordinary/repetitive" if segment.action == EDIT_ACTION_KEEP else "borderline (not fully dead)"
        note = (
            f"Refiner: {start:.2f}-{end:.2f}s was a {segment.duration_seconds:.1f}s {verb} "
            f"{segment.action.upper()} run (overall score {overall:.1f}/10) -- converted to COMPRESS "
            f"at {speed:.1f}x so it stays visible without dominating the edit."
        )
        notes.append(note)
        compress_reason = f"{segment.reason} (compressed {speed:.1f}x -- {verb} stretch, score {overall:.1f}/10)."

        if not keep_edges or segment.duration_seconds <= 2 * edge:
            return [
                EditPlanSegment(
                    start, end, EDIT_ACTION_COMPRESS, compress_reason,
                    confidence=segment.confidence, factors=list(segment.factors) + ["plan_refiner"],
                    scores=segment.scores, compression_speed_factor=speed, event_type=segment.event_type,
                )
            ]

        lead_reason = f"{segment.reason} (kept at full speed for a natural lead-in to the compressed stretch)."
        tail_reason = f"{segment.reason} (kept at full speed for a natural lead-out from the compressed stretch)."
        return [
            EditPlanSegment(start, start + edge, EDIT_ACTION_KEEP, lead_reason, scores=segment.scores, event_type=segment.event_type),
            EditPlanSegment(
                start + edge, end - edge, EDIT_ACTION_COMPRESS, compress_reason,
                confidence=segment.confidence, factors=list(segment.factors) + ["plan_refiner"],
                scores=segment.scores, compression_speed_factor=speed, event_type=segment.event_type,
            ),
            EditPlanSegment(end - edge, end, EDIT_ACTION_KEEP, tail_reason, scores=segment.scores, event_type=segment.event_type),
        ]
