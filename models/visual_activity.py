"""Per-interval visual motion evidence from frame sampling (Version 4.6, Feature 8).

Produced by :meth:`video.frame_extractor.FrameExtractor.extract_adaptive`
from *real* frame-to-frame pixel differences (not a stand-in/placeholder --
see that module), and consumed by :mod:`ai.event_recognizer` (Feature 7)
and :mod:`ai.context_builder` (fed into the AI prompt) so the AI editing
brain finally has *some* visual signal to work with. Before this feature,
:class:`ai.context_builder.ContextBuilder` sent the transcript only --
every decision was audio/speech-only, which is a root cause of the
reported bug (silent-but-important combat looking identical to silent,
truly boring stretches).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class VisualActivitySegment:
    """Average motion intensity across one sampled interval of the video."""

    start_seconds: float
    end_seconds: float
    motion_score: float  # 0.0 (static) .. 1.0 (maximal frame-to-frame change)
    is_scene_change: bool = False
    sample_count: int = 1

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    def to_dict(self) -> dict:
        return {
            "start": self.start_seconds,
            "end": self.end_seconds,
            "motion": round(self.motion_score, 4),
            "scene_change": self.is_scene_change,
            "samples": self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VisualActivitySegment":
        return cls(
            start_seconds=float(data["start"]),
            end_seconds=float(data["end"]),
            motion_score=float(data.get("motion", 0.0)),
            is_scene_change=bool(data.get("scene_change", False)),
            sample_count=int(data.get("samples", 1)),
        )


@dataclass(slots=True)
class VisualActivityTimeline:
    """An ordered set of :class:`VisualActivitySegment` covering a video."""

    segments: List[VisualActivitySegment] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.segments)

    def __iter__(self):
        return iter(self.segments)

    def average_motion(self, start_seconds: float, end_seconds: float) -> float:
        """Duration-weighted average motion score overlapping ``[start, end)``."""
        total_weight = 0.0
        weighted_sum = 0.0
        for seg in self.segments:
            overlap = min(seg.end_seconds, end_seconds) - max(seg.start_seconds, start_seconds)
            if overlap <= 0:
                continue
            total_weight += overlap
            weighted_sum += overlap * seg.motion_score
        return (weighted_sum / total_weight) if total_weight > 0 else 0.0

    def scene_changes_between(self, start_seconds: float, end_seconds: float) -> int:
        return sum(
            1 for seg in self.segments
            if seg.is_scene_change and start_seconds <= seg.start_seconds < end_seconds
        )

    def to_dict(self) -> dict:
        return {"segments": [s.to_dict() for s in self.segments]}

    @classmethod
    def from_dict(cls, data: dict) -> "VisualActivityTimeline":
        return cls(segments=[VisualActivitySegment.from_dict(s) for s in data.get("segments", [])])
