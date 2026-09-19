"""Validates and parses the AI editing brain's raw JSON response into an EditPlan."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from core.exceptions import ResponseParsingError
from models.edit_plan import EditPlan, EditPlanSegment, MoodRecommendation
from models.segment_scores import SegmentScores
from services.logging_service import LoggingService
from utils.constants import (
    DEFAULT_COMPRESSION_SPEED_FACTOR,
    DEFAULT_EDIT_CONFIDENCE,
    EDIT_ACTION_COMPRESS,
    EDIT_ACTION_KEEP,
    EDIT_ACTION_REMOVE,
    EDIT_PLAN_COVERAGE_TOLERANCE_SECONDS,
    MAX_COMPRESSION_SPEED_FACTOR,
    MIN_COMPRESSION_SPEED_FACTOR,
    VALID_EDIT_ACTIONS,
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ResponseParser:
    """Parses and validates the provider's raw text response into an EditPlan.

    The provider is instructed (see
    :class:`ai.prompt_templates.PromptTemplates.OUTPUT_CONTRACT`) to return
    ONLY JSON, but real models occasionally wrap it in markdown code fences
    or add stray whitespace -- this parser tolerates cosmetic issues like
    that, but rejects anything that isn't valid, schema-conformant JSON
    outright rather than guessing at a malformed or incomplete response.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("ai.response_parser")

    def parse(self, raw_response: str, video_duration_seconds: Optional[float] = None) -> EditPlan:
        """Parse ``raw_response`` into a fully-validated :class:`EditPlan`.

        ``processing_seconds`` and ``model_used`` are left at their default
        values (``0.0`` / ``""``) -- :class:`ai.edit_planner.EditPlanner`
        fills those in afterwards, since a response parser has no business
        knowing request timing or which model produced the response.

        Args:
            raw_response: The provider's raw text response.
            video_duration_seconds: The source video's duration, if known,
                used only to add a non-fatal warning if the plan's segments
                don't cover the full video.

        Raises:
            ResponseParsingError: if the response is not valid JSON, or is
                valid JSON that doesn't satisfy the required schema.
        """
        payload = self._extract_json(raw_response)
        self._validate_top_level(payload)

        segments = self._parse_segments(payload["segments"])
        keep_segments = [segment for segment in segments if segment.action == EDIT_ACTION_KEEP]
        remove_segments = [segment for segment in segments if segment.action == EDIT_ACTION_REMOVE]
        compress_segments = [segment for segment in segments if segment.action == EDIT_ACTION_COMPRESS]

        warnings: List[str] = [str(warning) for warning in (payload.get("warnings") or [])]
        warnings.extend(self._validate_coverage(segments, video_duration_seconds))

        confidence = self._validate_confidence(payload.get("confidence"), warnings)
        mood_recommendation = self._parse_mood_recommendation(payload.get("mood_recommendation"), warnings)

        plan = EditPlan(
            target_length_seconds=float(payload["target_length"]),
            keep_segments=keep_segments,
            remove_segments=remove_segments,
            compress_segments=compress_segments,
            confidence=confidence,
            reasons=[str(reason) for reason in (payload.get("reasons") or [])],
            warnings=warnings,
            mood_recommendation=mood_recommendation,
        )

        self._logger.info(
            "Response parsed successfully: keep=%d remove=%d compress=%d confidence=%.2f warnings=%d",
            len(keep_segments),
            len(remove_segments),
            len(compress_segments),
            plan.confidence,
            len(warnings),
        )
        return plan

    def _extract_json(self, raw_response: str) -> Any:
        """Strip cosmetic wrapping (code fences, stray prose) and json.loads() it."""
        text = raw_response.strip()
        text = _CODE_FENCE_RE.sub("", text).strip()

        if not text.startswith("{"):
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._logger.error("Failed to parse AI response as JSON: %s", exc)
            raise ResponseParsingError(f"AI response is not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise ResponseParsingError("AI response JSON must be an object, not a list or scalar.")
        return payload

    @staticmethod
    def _validate_top_level(payload: dict) -> None:
        if "target_length" not in payload:
            raise ResponseParsingError("AI response is missing required field 'target_length'.")
        target_length = payload["target_length"]
        if not isinstance(target_length, (int, float)) or isinstance(target_length, bool) or target_length <= 0:
            raise ResponseParsingError("'target_length' must be a positive number.")

        if "segments" not in payload:
            raise ResponseParsingError("AI response is missing required field 'segments'.")
        if not isinstance(payload["segments"], list) or not payload["segments"]:
            raise ResponseParsingError("'segments' must be a non-empty list.")

    @staticmethod
    def _parse_segments(raw_segments: list) -> List[EditPlanSegment]:
        segments: List[EditPlanSegment] = []
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict):
                raise ResponseParsingError(f"Segment {index} is not a JSON object.")

            missing = [key for key in ("start", "end", "action", "reason") if key not in raw]
            if missing:
                raise ResponseParsingError(f"Segment {index} is missing field(s): {', '.join(missing)}.")

            start, end, action, reason = raw["start"], raw["end"], raw["action"], raw["reason"]

            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or isinstance(start, bool)
                or isinstance(end, bool)
            ):
                raise ResponseParsingError(f"Segment {index} has non-numeric 'start'/'end'.")
            if float(start) < 0:
                raise ResponseParsingError(f"Segment {index} has a negative 'start' ({start}).")
            if float(end) <= float(start):
                raise ResponseParsingError(f"Segment {index} has end ({end}) <= start ({start}).")
            if action not in VALID_EDIT_ACTIONS:
                raise ResponseParsingError(
                    f"Segment {index} has invalid action '{action}'; expected one of {VALID_EDIT_ACTIONS}."
                )
            if not isinstance(reason, str) or not reason.strip():
                raise ResponseParsingError(f"Segment {index} is missing a non-empty 'reason'.")

            segment_confidence = raw.get("confidence")
            if (
                not isinstance(segment_confidence, (int, float))
                or isinstance(segment_confidence, bool)
                or not (0.0 <= float(segment_confidence) <= 1.0)
            ):
                segment_confidence = None
            else:
                segment_confidence = float(segment_confidence)

            raw_factors = raw.get("factors")
            factors = [str(f) for f in raw_factors] if isinstance(raw_factors, list) else []
            raw_seg_warnings = raw.get("warnings")
            seg_warnings = [str(w) for w in raw_seg_warnings] if isinstance(raw_seg_warnings, list) else []

            scores = ResponseParser._parse_scores(raw.get("scores"))
            compression_speed_factor = ResponseParser._parse_compression(raw.get("compression"), action, index)

            segments.append(
                EditPlanSegment(
                    start_seconds=float(start),
                    end_seconds=float(end),
                    action=action,
                    reason=reason.strip(),
                    confidence=segment_confidence,
                    factors=factors,
                    warnings=seg_warnings,
                    scores=scores,
                    compression_speed_factor=compression_speed_factor,
                )
            )

        return sorted(segments, key=lambda segment: segment.start_seconds)

    @staticmethod
    def _parse_scores(raw_scores: Any) -> Optional[SegmentScores]:
        """Best-effort parse of the optional Feature 5 ``scores`` object.

        Never raises: a malformed/missing scores object simply means this
        segment has no scores (``None``), which every downstream consumer
        (the scorer, the plan refiner) already treats as "no evidence,
        don't touch this segment" rather than an error.
        """
        if not isinstance(raw_scores, dict):
            return None
        try:
            return SegmentScores.from_dict(raw_scores)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_compression(raw_compression: Any, action: str, index: int) -> Optional[float]:
        """Parse the ``compression.speed_factor`` field, required for ``action == "compress"``.

        Missing/invalid on a compress segment is tolerated (not a hard
        failure) -- ``EditPlanSegment.__post_init__`` already clamps to a
        sane default -- consistent with this parser's general philosophy
        of rejecting only what's truly unusable and repairing the rest.
        """
        if action != EDIT_ACTION_COMPRESS:
            return None
        if not isinstance(raw_compression, dict):
            return DEFAULT_COMPRESSION_SPEED_FACTOR
        factor = raw_compression.get("speed_factor")
        if not isinstance(factor, (int, float)) or isinstance(factor, bool):
            return DEFAULT_COMPRESSION_SPEED_FACTOR
        return max(MIN_COMPRESSION_SPEED_FACTOR, min(MAX_COMPRESSION_SPEED_FACTOR, float(factor)))

    @staticmethod
    def _parse_mood_recommendation(raw_mood: Any, warnings: List[str]) -> Optional[MoodRecommendation]:
        """Parse the optional whole-video mood/BGM suggestion (Version 5.6).

        Deliberately lenient: this field is a nice-to-have on top of the
        actual edit decisions, never a reason to fail the whole response.
        A missing field is silent (older prompts/providers simply won't
        include it); a malformed one is a warning, not a
        :class:`~core.exceptions.ResponseParsingError`.
        """
        if raw_mood is None:
            return None
        if not isinstance(raw_mood, dict):
            warnings.append(f"Ignored invalid 'mood_recommendation' value (expected an object): {raw_mood!r}")
            return None

        overall_mood = str(raw_mood.get("overall_mood") or "").strip()
        if not overall_mood:
            warnings.append("Ignored 'mood_recommendation': missing or empty 'overall_mood'.")
            return None

        raw_genres = raw_mood.get("music_genre_suggestions") or []
        if not isinstance(raw_genres, list):
            raw_genres = []
        genres = [str(genre).strip() for genre in raw_genres if str(genre).strip()]

        return MoodRecommendation(
            overall_mood=overall_mood,
            music_genre_suggestions=genres,
            tempo_description=str(raw_mood.get("tempo_description") or "").strip(),
            reasoning=str(raw_mood.get("reasoning") or "").strip(),
        )

    @staticmethod
    def _validate_confidence(raw_confidence: Any, warnings: List[str]) -> float:
        if raw_confidence is None:
            return DEFAULT_EDIT_CONFIDENCE
        if (
            not isinstance(raw_confidence, (int, float))
            or isinstance(raw_confidence, bool)
            or not (0.0 <= float(raw_confidence) <= 1.0)
        ):
            warnings.append(f"Ignored invalid 'confidence' value: {raw_confidence!r}")
            return DEFAULT_EDIT_CONFIDENCE
        return float(raw_confidence)

    @staticmethod
    def _validate_coverage(
        segments: List[EditPlanSegment], video_duration_seconds: Optional[float]
    ) -> List[str]:
        """Return non-fatal warnings about gaps/overlaps/incomplete coverage.

        These are deliberately warnings, not hard failures: a plan that is
        slightly imperfect (e.g. a half-second gap) is still usable and far
        more useful to the caller than discarding an otherwise good response.
        """
        warnings: List[str] = []
        tolerance = EDIT_PLAN_COVERAGE_TOLERANCE_SECONDS
        previous_end = 0.0

        for segment in segments:
            if segment.start_seconds < previous_end - tolerance:
                warnings.append(f"Segments overlap near {segment.start_seconds:.2f}s.")
            elif segment.start_seconds > previous_end + tolerance:
                warnings.append(
                    f"Gap in coverage between {previous_end:.2f}s and {segment.start_seconds:.2f}s."
                )
            previous_end = max(previous_end, segment.end_seconds)

        if video_duration_seconds is not None and previous_end < video_duration_seconds - tolerance:
            warnings.append(
                f"Segments end at {previous_end:.2f}s but the video is "
                f"{video_duration_seconds:.2f}s long."
            )
        return warnings
