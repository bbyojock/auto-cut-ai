"""Simulates the effect of an EditPlan without touching any video (Version 3.5)."""

from __future__ import annotations

import logging
from typing import Optional

from models.edit_plan import EditPlan
from models.edit_simulation_result import EditSimulationResult
from services.logging_service import LoggingService


class EditSimulator:
    """Computes what an EditPlan *would* produce, purely from its timestamps.

    This never opens, reads, or writes the source video file -- every
    number is derived from :class:`models.edit_plan.EditPlan` segment
    timestamps, so it's safe to run on any plan before a future version
    commits to an actual render.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("ai.edit_simulator")

    def simulate(self, plan: EditPlan, video_duration_seconds: float) -> EditSimulationResult:
        """Compute length/pacing/coverage statistics for ``plan``."""
        keep_durations = [segment.duration_seconds for segment in plan.keep_segments]
        # Version 4.6, Feature 5/9: the final length now accounts for
        # COMPRESS segments' sped-up (shorter) contribution, not just
        # full-speed KEEP -- see EditPlanSegment.effective_duration_seconds.
        final_length = plan.effective_output_duration_seconds
        removed_percentage = (
            max(0.0, (video_duration_seconds - final_length) / video_duration_seconds * 100.0)
            if video_duration_seconds > 0
            else 0.0
        )
        compressed_seconds_saved = sum(
            segment.duration_seconds - segment.effective_duration_seconds for segment in plan.compress_segments
        )

        result = EditSimulationResult(
            original_length_seconds=video_duration_seconds,
            estimated_final_length_seconds=final_length,
            removed_percentage=removed_percentage,
            cut_count=len(plan.keep_segments) + len(plan.compress_segments),
            average_clip_length_seconds=(final_length / len(keep_durations)) if keep_durations else 0.0,
            longest_clip_seconds=max(keep_durations) if keep_durations else 0.0,
            shortest_clip_seconds=min(keep_durations) if keep_durations else 0.0,
            ai_confidence=plan.confidence,
            validation_warnings=list(plan.warnings),
            compressed_segment_count=len(plan.compress_segments),
            compressed_seconds_saved=compressed_seconds_saved,
        )

        self._logger.info(
            "Simulation complete: %.1fs -> %.1fs (%.1f%% removed), %d cut(s), %d compressed "
            "(saved %.1fs), avg clip %.1fs",
            result.original_length_seconds,
            result.estimated_final_length_seconds,
            result.removed_percentage,
            result.cut_count,
            result.compressed_segment_count,
            result.compressed_seconds_saved,
            result.average_clip_length_seconds,
        )
        return result
