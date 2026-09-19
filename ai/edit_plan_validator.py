"""Validates and, where possible, automatically repairs an EditPlan (Version 3.5)."""

from __future__ import annotations

import logging
from typing import List, Optional

from core.exceptions import EditPlanValidationError
from models.edit_plan import EditPlan, EditPlanSegment
from services.logging_service import LoggingService
from utils.constants import (
    DEFAULT_COMPRESSION_SPEED_FACTOR,
    EDIT_ACTION_COMPRESS,
    EDIT_ACTION_KEEP,
    EDIT_ACTION_REMOVE,
    MAX_COMPRESSION_SPEED_FACTOR,
    MIN_COMPRESSION_SPEED_FACTOR,
    MIN_EDIT_CLIP_SECONDS,
    TIMESTAMP_EPSILON_SECONDS,
    VALID_EDIT_ACTIONS,
)


class EditPlanValidator:
    """Checks every EditPlan for structural problems before it's accepted.

    Detects: overlapping segments, duplicated segments, invalid
    timestamps, ``end <= start``, extremely short clips, timeline gaps,
    invalid actions, and impossible durations (segments outside the
    source video's length). Whenever a problem has an unambiguous, safe
    fix, it's repaired automatically and recorded in the returned plan's
    ``warnings``; if nothing usable is left after repair,
    :class:`core.exceptions.EditPlanValidationError` is raised with a
    clear message instead of silently returning a broken plan.
    """

    def __init__(
        self,
        min_clip_seconds: float = MIN_EDIT_CLIP_SECONDS,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._min_clip_seconds = min_clip_seconds
        self._logger = logger or LoggingService.get_logger("ai.edit_plan_validator")

    def validate(self, plan: EditPlan, video_duration_seconds: Optional[float] = None) -> EditPlan:
        """Validate (and repair where possible) ``plan``. Returns a cleaned copy.

        Raises:
            EditPlanValidationError: if ``plan`` starts with no segments,
                or ends up with none after repair attempts.
        """
        repairs: List[str] = []
        segments = list(plan.all_segments_sorted)
        if not segments:
            raise EditPlanValidationError("EditPlan has no segments at all.")

        segments = self._fix_invalid_actions(segments, repairs)
        segments = self._fix_invalid_timestamps(segments, repairs, video_duration_seconds)
        segments = self._fix_compression_speed(segments, repairs)
        segments = self._dedupe(segments, repairs)
        segments = self._fix_overlaps(segments, repairs)
        segments = self._merge_short_clips(segments, repairs)
        segments = self._fill_gaps(segments, repairs, video_duration_seconds)

        if not segments:
            raise EditPlanValidationError("EditPlan had no valid segments left after repair attempts.")

        validated = EditPlan(
            target_length_seconds=plan.target_length_seconds,
            keep_segments=[s for s in segments if s.action == EDIT_ACTION_KEEP],
            remove_segments=[s for s in segments if s.action == EDIT_ACTION_REMOVE],
            compress_segments=[s for s in segments if s.action == EDIT_ACTION_COMPRESS],
            confidence=plan.confidence,
            reasons=list(plan.reasons),
            warnings=list(plan.warnings) + repairs,
            processing_seconds=plan.processing_seconds,
            model_used=plan.model_used,
            created_at=plan.created_at,
        )

        if repairs:
            self._logger.warning("Validation repaired %d issue(s): %s", len(repairs), "; ".join(repairs))
        else:
            self._logger.info("Validation passed with no repairs needed.")
        return validated

    # ------------------------------------------------------------------
    # Individual repair passes -- each takes/returns a sorted segment list
    # ------------------------------------------------------------------
    @staticmethod
    def _fix_invalid_actions(segments: List[EditPlanSegment], repairs: List[str]) -> List[EditPlanSegment]:
        fixed = []
        for segment in segments:
            if segment.action not in VALID_EDIT_ACTIONS:
                repairs.append(
                    f"Segment {segment.start_seconds:.2f}-{segment.end_seconds:.2f}s had invalid "
                    f"action '{segment.action}'; treated as '{EDIT_ACTION_REMOVE}'."
                )
                fixed.append(
                    EditPlanSegment(segment.start_seconds, segment.end_seconds, EDIT_ACTION_REMOVE, segment.reason)
                )
            else:
                fixed.append(segment)
        return fixed

    @staticmethod
    def _fix_compression_speed(segments: List[EditPlanSegment], repairs: List[str]) -> List[EditPlanSegment]:
        """Every COMPRESS segment must have a usable speed factor (Feature 5/9).

        ``EditPlanSegment.__post_init__`` already clamps/defaults this, so
        in practice this pass mostly exists to surface the fact as a
        visible repair rather than silently accepting whatever the AI (or
        an EditPlan Chat patch) sent.
        """
        fixed = []
        for segment in segments:
            if segment.action == EDIT_ACTION_COMPRESS and (
                segment.compression_speed_factor is None
                or not (MIN_COMPRESSION_SPEED_FACTOR <= segment.compression_speed_factor <= MAX_COMPRESSION_SPEED_FACTOR)
            ):
                repairs.append(
                    f"Compress segment {segment.start_seconds:.2f}-{segment.end_seconds:.2f}s had no/invalid "
                    f"speed factor; defaulted to {DEFAULT_COMPRESSION_SPEED_FACTOR:.1f}x."
                )
                fixed.append(
                    EditPlanSegment(
                        segment.start_seconds, segment.end_seconds, segment.action, segment.reason,
                        confidence=segment.confidence, factors=list(segment.factors), warnings=list(segment.warnings),
                        scores=segment.scores, compression_speed_factor=DEFAULT_COMPRESSION_SPEED_FACTOR,
                        event_type=segment.event_type,
                    )
                )
            else:
                fixed.append(segment)
        return fixed

    @staticmethod
    def _is_finite(value: float) -> bool:
        return value == value and value not in (float("inf"), float("-inf"))  # NaN check + inf check

    def _fix_invalid_timestamps(
        self,
        segments: List[EditPlanSegment],
        repairs: List[str],
        video_duration_seconds: Optional[float],
    ) -> List[EditPlanSegment]:
        fixed = []
        for segment in segments:
            start, end = segment.start_seconds, segment.end_seconds
            if not (self._is_finite(start) and self._is_finite(end)):
                repairs.append(f"Dropped segment with non-finite timestamp(s): start={start}, end={end}.")
                continue

            new_start = max(0.0, start)
            if new_start != start:
                repairs.append(f"Clamped negative start {start:.2f}s to 0.00s.")

            new_end = end
            if video_duration_seconds is not None and new_end > video_duration_seconds:
                new_end = video_duration_seconds
                repairs.append(f"Clamped end {end:.2f}s to video duration {video_duration_seconds:.2f}s.")

            if new_end <= new_start:
                repairs.append(
                    f"Dropped segment {start:.2f}-{end:.2f}s: not a valid duration (impossible timestamps)."
                )
                continue

            fixed.append(segment.with_range(new_start, new_end))
        return fixed

    @staticmethod
    def _dedupe(segments: List[EditPlanSegment], repairs: List[str]) -> List[EditPlanSegment]:
        seen = set()
        fixed = []
        for segment in segments:
            key = (round(segment.start_seconds, 2), round(segment.end_seconds, 2), segment.action)
            if key in seen:
                repairs.append(
                    f"Removed duplicate segment at {segment.start_seconds:.2f}-{segment.end_seconds:.2f}s "
                    f"({segment.action})."
                )
                continue
            seen.add(key)
            fixed.append(segment)
        return fixed

    @staticmethod
    def _fix_overlaps(segments: List[EditPlanSegment], repairs: List[str]) -> List[EditPlanSegment]:
        if not segments:
            return segments
        segments = sorted(segments, key=lambda s: s.start_seconds)
        fixed = [segments[0]]
        for segment in segments[1:]:
            previous = fixed[-1]
            if segment.start_seconds < previous.end_seconds:
                new_start = previous.end_seconds
                if new_start >= segment.end_seconds:
                    repairs.append(
                        f"Dropped segment {segment.start_seconds:.2f}-{segment.end_seconds:.2f}s: "
                        "fully overlapped by the previous segment."
                    )
                    continue
                repairs.append(
                    f"Trimmed overlapping segment start from {segment.start_seconds:.2f}s to {new_start:.2f}s."
                )
                segment = segment.with_range(new_start, segment.end_seconds)
            fixed.append(segment)
        return fixed

    def _merge_short_clips(self, segments: List[EditPlanSegment], repairs: List[str]) -> List[EditPlanSegment]:
        if len(segments) <= 1:
            return segments
        fixed = list(segments)
        index = 0
        while index < len(fixed) and len(fixed) > 1:
            duration = fixed[index].end_seconds - fixed[index].start_seconds
            if duration >= self._min_clip_seconds:
                index += 1
                continue

            if index > 0:
                previous = fixed[index - 1]
                repairs.append(
                    f"Merged extremely short clip ({duration:.2f}s at {fixed[index].start_seconds:.2f}s) "
                    "into the previous segment."
                )
                fixed[index - 1] = previous.with_range(previous.start_seconds, fixed[index].end_seconds)
                del fixed[index]
            else:
                nxt = fixed[index + 1]
                repairs.append(
                    f"Merged extremely short leading clip ({duration:.2f}s) into the next segment."
                )
                fixed[index + 1] = nxt.with_range(fixed[index].start_seconds, nxt.end_seconds)
                del fixed[index]
        return fixed

    @staticmethod
    def _fill_gaps(
        segments: List[EditPlanSegment],
        repairs: List[str],
        video_duration_seconds: Optional[float],
    ) -> List[EditPlanSegment]:
        """Fill any uncovered stretch of the timeline with a 'remove' segment.

        This is the safest possible default for time the AI didn't classify
        at all: it simply won't appear in the final edit, rather than being
        silently dropped from the plan's bookkeeping.
        """
        if not segments:
            return segments
        segments = sorted(segments, key=lambda s: s.start_seconds)
        filled: List[EditPlanSegment] = []
        cursor = 0.0

        for segment in segments:
            if segment.start_seconds > cursor + TIMESTAMP_EPSILON_SECONDS:
                gap_start, gap_end = cursor, segment.start_seconds
                repairs.append(f"Filled timeline gap {gap_start:.2f}-{gap_end:.2f}s as 'remove'.")
                filled.append(
                    EditPlanSegment(gap_start, gap_end, EDIT_ACTION_REMOVE, "Auto-filled gap (unclassified by AI)")
                )
            filled.append(segment)
            cursor = max(cursor, segment.end_seconds)

        if video_duration_seconds is not None and cursor < video_duration_seconds - TIMESTAMP_EPSILON_SECONDS:
            repairs.append(f"Filled trailing gap {cursor:.2f}-{video_duration_seconds:.2f}s as 'remove'.")
            filled.append(
                EditPlanSegment(
                    cursor, video_duration_seconds, EDIT_ACTION_REMOVE,
                    "Auto-filled trailing gap (unclassified by AI)",
                )
            )
        return filled
