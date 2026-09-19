"""Scores EditPlan segments against a set of EditingRules (Version 3.5).

The AI is told each rule's score directly in the prompt (see
:meth:`ai.editing_rules.EditingRules.to_prompt_text`) so it can weigh
decisions accordingly while generating a plan. This class additionally
does a lightweight, best-effort scoring of the AI's own segment reasons
*after the fact* -- useful for logging/diagnostics and for surfacing which
rules likely drove each decision, without requiring the AI to echo scores
back itself.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from ai.editing_rules import EditingRules
from models.edit_plan import EditPlan, EditPlanSegment
from services.logging_service import LoggingService


class EditingScorer:
    """Best-effort keyword-based scoring of EditPlan segments."""

    def __init__(self, rules: Optional[EditingRules] = None, logger: Optional[logging.Logger] = None) -> None:
        self._rules = rules or EditingRules.default()
        self._logger = logger or LoggingService.get_logger("ai.editing_scorer")

    def score_segment(self, segment: EditPlanSegment) -> float:
        """Score for one segment.

        Version 4.6, Feature 5: if the AI supplied multi-axis ``scores``
        for this segment, its ``computed_overall()`` (already actually
        used to drive KEEP/REMOVE/COMPRESS decisions -- see
        :mod:`ai.plan_refiner`) is authoritative. Only falls back to the
        original keyword-matching heuristic for segments/providers that
        didn't return scores at all, so nothing that depended on the old
        behavior for score-less plans changes.
        """
        if segment.scores is not None:
            return segment.scores.computed_overall()
        reason_lower = segment.reason.lower()
        return sum(
            rule.score for rule in self._rules.enabled_rules() if rule.name.lower() in reason_lower
        )

    def score_plan(self, plan: EditPlan) -> Dict[str, float]:
        """Score every segment in ``plan``. Returns ``{"start-end": score}``."""
        scores = {
            f"{segment.start_seconds:.2f}-{segment.end_seconds:.2f}": self.score_segment(segment)
            for segment in plan.all_segments_sorted
        }
        total = sum(scores.values())
        self._logger.info(
            "Score calculation complete: %d segment(s) scored, total=%.1f, rule_set=%s",
            len(scores), total, self._rules.name,
        )
        return scores
