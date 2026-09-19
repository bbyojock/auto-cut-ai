"""AI-generated editing plan model (Version 3)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from models.segment_scores import SegmentScores
from utils.constants import (
    DEFAULT_COMPRESSION_SPEED_FACTOR,
    EDIT_ACTION_COMPRESS,
    EDIT_ACTION_KEEP,
    MAX_COMPRESSION_SPEED_FACTOR,
    MIN_COMPRESSION_SPEED_FACTOR,
)


@dataclass(slots=True)
class EditPlanSegment:
    """One decision the AI editing brain made about a stretch of source video.

    ``action`` is ``"keep"``, ``"remove"``, or ``"compress"`` -- see
    ``utils.constants.VALID_EDIT_ACTIONS``.

    Version 4.5 (Feature 11 -- Explainability) adds three optional fields
    on top of the original ``reason`` string: ``confidence`` (how sure the
    AI was about *this specific* decision, separate from the plan-level
    ``EditPlan.confidence``), ``factors`` (which rules/instructions/scores
    likely influenced it), and ``warnings`` (segment-specific caveats).

    Version 4.6 (Features 5/7/9) adds two more, both optional: ``scores``
    (the Feature 5 multi-axis :class:`models.segment_scores.SegmentScores`
    for this segment) and ``compression_speed_factor`` (only meaningful
    when ``action == "compress"`` -- how much faster than 1.0x this
    stretch should play, e.g. ``2.5`` = 2.5x speed). All new fields default
    to ``None``/empty, so every plan produced before 4.6 -- and every
    provider response that doesn't include them -- keeps working unchanged.
    """

    start_seconds: float
    end_seconds: float
    action: str
    reason: str
    confidence: Optional[float] = None
    factors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    scores: Optional[SegmentScores] = None
    compression_speed_factor: Optional[float] = None
    event_type: Optional[str] = None

    def __post_init__(self) -> None:
        if self.action == EDIT_ACTION_COMPRESS:
            factor = self.compression_speed_factor
            if factor is None or not (factor == factor) or factor <= 1.0:  # NaN-safe
                factor = DEFAULT_COMPRESSION_SPEED_FACTOR
            self.compression_speed_factor = max(MIN_COMPRESSION_SPEED_FACTOR, min(MAX_COMPRESSION_SPEED_FACTOR, factor))
        else:
            self.compression_speed_factor = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def effective_duration_seconds(self) -> float:
        """How much run time this segment actually contributes to the final export.

        ``keep`` contributes its full duration, ``remove`` contributes
        none, and ``compress`` contributes ``duration / speed_factor``
        (Feature 5/9's whole point: a compressed stretch is shorter in the
        output than in the source, but never zero).
        """
        if self.action == EDIT_ACTION_KEEP:
            return self.duration_seconds
        if self.action == EDIT_ACTION_COMPRESS:
            factor = self.compression_speed_factor or DEFAULT_COMPRESSION_SPEED_FACTOR
            return self.duration_seconds / factor
        return 0.0

    @property
    def is_keep(self) -> bool:
        return self.action == EDIT_ACTION_KEEP

    @property
    def is_compress(self) -> bool:
        return self.action == EDIT_ACTION_COMPRESS

    def with_range(self, start_seconds: float, end_seconds: float) -> "EditPlanSegment":
        """A copy of this segment with a different [start, end), everything else preserved.

        Used anywhere a segment needs to be trimmed/split (validation
        repairs, the refiner) so compression/scores/explainability
        metadata is never silently dropped the way a bare
        ``EditPlanSegment(start, end, action, reason)`` reconstruction
        would drop it.
        """
        return EditPlanSegment(
            start_seconds=start_seconds, end_seconds=end_seconds, action=self.action, reason=self.reason,
            confidence=self.confidence, factors=list(self.factors), warnings=list(self.warnings),
            scores=self.scores, compression_speed_factor=self.compression_speed_factor, event_type=self.event_type,
        )

    def to_dict(self) -> dict:
        return {
            "start": self.start_seconds,
            "end": self.end_seconds,
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "factors": list(self.factors),
            "warnings": list(self.warnings),
            "scores": self.scores.to_dict() if self.scores is not None else None,
            "compression_speed_factor": self.compression_speed_factor,
            "event_type": self.event_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlanSegment":
        raw_scores = data.get("scores")
        return cls(
            start_seconds=float(data["start"]),
            end_seconds=float(data["end"]),
            action=str(data["action"]),
            reason=str(data.get("reason", "")),
            confidence=(float(data["confidence"]) if data.get("confidence") is not None else None),
            factors=list(data.get("factors", []) or []),
            warnings=list(data.get("warnings", []) or []),
            scores=SegmentScores.from_dict(raw_scores) if raw_scores else None,
            compression_speed_factor=(
                float(data["compression_speed_factor"]) if data.get("compression_speed_factor") is not None else None
            ),
            event_type=data.get("event_type"),
        )


@dataclass(slots=True)
class MoodRecommendation:
    """The AI editing brain's overall mood/atmosphere + background-music
    suggestion for the video as a whole (Version 5.6).

    Generated once per :class:`EditPlan` (not per segment), from the same
    provider response that produces the KEEP/REMOVE/COMPRESS decisions --
    no extra API call. Entirely optional: a provider response that omits
    it, or any plan created/saved before this feature existed, simply
    leaves :attr:`EditPlan.mood_recommendation` as ``None``, and nothing
    downstream (export, Resolve/XML, UI) requires it to be present.
    """

    overall_mood: str
    music_genre_suggestions: List[str] = field(default_factory=list)
    tempo_description: str = ""
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "overall_mood": self.overall_mood,
            "music_genre_suggestions": list(self.music_genre_suggestions),
            "tempo_description": self.tempo_description,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MoodRecommendation":
        return cls(
            overall_mood=str(data.get("overall_mood", "")),
            music_genre_suggestions=[str(genre) for genre in (data.get("music_genre_suggestions") or [])],
            tempo_description=str(data.get("tempo_description", "")),
            reasoning=str(data.get("reasoning", "")),
        )


@dataclass(slots=True)
class EditPlan:
    """A complete, professional editing plan produced by the AI editing brain.

    Produced by :class:`ai.edit_planner.EditPlanner` from an
    :class:`models.analysis_result.AnalysisResult`. This is pure decision
    data: Version 3 never edits video, renders anything, or talks to
    DaVinci Resolve -- see the ``ai`` package docstring for the full scope
    note. A future version consumes this plan to actually perform the cut.
    """

    target_length_seconds: float
    keep_segments: List[EditPlanSegment] = field(default_factory=list)
    remove_segments: List[EditPlanSegment] = field(default_factory=list)
    # Version 4.6 (Features 5/7/9): segments that are neither fully kept nor
    # fully removed -- played back sped-up instead. Kept as its own list
    # (rather than folding into keep_segments) so every pre-4.6 caller that
    # only ever looked at keep_segments/remove_segments keeps working
    # exactly as before; anything that needs *every* segment already goes
    # through ``all_segments_sorted``, which was updated to include these.
    compress_segments: List[EditPlanSegment] = field(default_factory=list)
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    processing_seconds: float = 0.0
    model_used: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    # Feature: clip reordering. When set, this is the desired *playback
    # order* of the final export, expressed as a list of (start_seconds,
    # end_seconds) pairs that must exactly match -- as a set, any order --
    # the ranges that would otherwise be kept chronologically (see
    # :mod:`davinci.edit_plan_applier`'s ``process_edit_plan``, which is
    # what actually validates and applies this). ``None`` (the default)
    # means "keep everything in original chronological order", exactly
    # the pre-existing behavior -- so every plan created before this
    # feature, and every plan that never asks to be reordered, is
    # completely unaffected.
    output_order: Optional[List[Tuple[float, float]]] = None
    # Version 5.6: the AI editing brain's suggested overall mood/atmosphere
    # and background-music style for the finished video. See
    # MoodRecommendation's docstring -- always optional/backward-compatible.
    mood_recommendation: Optional[MoodRecommendation] = None

    @property
    def all_segments_sorted(self) -> List[EditPlanSegment]:
        """Every segment (keep + remove + compress), sorted by start time."""
        return sorted(
            self.keep_segments + self.remove_segments + self.compress_segments, key=lambda seg: seg.start_seconds
        )

    @property
    def kept_duration_seconds(self) -> float:
        """Total duration of every segment marked ``"keep"`` (compress segments not included).

        Kept for backward compatibility with every pre-4.6 caller. For an
        accurate estimate of the final exported length when COMPRESS is in
        use, see :attr:`effective_output_duration_seconds`.
        """
        return sum(segment.duration_seconds for segment in self.keep_segments)

    @property
    def effective_output_duration_seconds(self) -> float:
        """Estimated final export length: full KEEP duration + sped-up COMPRESS duration."""
        return sum(segment.effective_duration_seconds for segment in self.keep_segments + self.compress_segments)

    def to_dict(self) -> dict:
        """Serialize to a plain, JSON-friendly dictionary."""
        return {
            "target_length": self.target_length_seconds,
            "keep_segments": [segment.to_dict() for segment in self.keep_segments],
            "remove_segments": [segment.to_dict() for segment in self.remove_segments],
            "compress_segments": [segment.to_dict() for segment in self.compress_segments],
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "processing_time": self.processing_seconds,
            "model_used": self.model_used,
            "created_at": self.created_at.isoformat(),
            "output_order": (
                [[s, e] for s, e in self.output_order] if self.output_order is not None else None
            ),
            "mood_recommendation": self.mood_recommendation.to_dict() if self.mood_recommendation else None,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a JSON string, e.g. for saving to disk or a Logs view."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlan":
        """Reconstruct an :class:`EditPlan` from :meth:`to_dict`'s output.

        Used by :class:`services.edit_plan_io_service.EditPlanIOService`
        (Version 4, Feature 13: Save/Load EditPlan) and
        :class:`services.edit_plan_cache_service.EditPlanCacheService`
        (Feature 14: EditPlan Cache) to round-trip a plan through disk.
        """
        created_at_raw = data.get("created_at")
        try:
            created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.now()
        except ValueError:
            created_at = datetime.now()

        raw_output_order = data.get("output_order")
        raw_mood = data.get("mood_recommendation")
        return cls(
            target_length_seconds=float(data["target_length"]),
            keep_segments=[EditPlanSegment.from_dict(s) for s in data.get("keep_segments", [])],
            remove_segments=[EditPlanSegment.from_dict(s) for s in data.get("remove_segments", [])],
            compress_segments=[EditPlanSegment.from_dict(s) for s in data.get("compress_segments", [])],
            confidence=float(data.get("confidence", 0.0)),
            reasons=list(data.get("reasons", [])),
            warnings=list(data.get("warnings", [])),
            processing_seconds=float(data.get("processing_time", 0.0)),
            model_used=str(data.get("model_used", "")),
            created_at=created_at,
            output_order=(
                [(float(pair[0]), float(pair[1])) for pair in raw_output_order]
                if raw_output_order
                else None
            ),
            mood_recommendation=MoodRecommendation.from_dict(raw_mood) if raw_mood else None,
        )

    @classmethod
    def from_json(cls, raw: str) -> "EditPlan":
        return cls.from_dict(json.loads(raw))
