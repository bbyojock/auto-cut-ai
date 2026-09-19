"""Multi-axis segment scoring (Version 4.6, Feature 5).

The spec calls for scoring every important segment on several axes
(Humor, Action, Story Progress, Excitement, Information, Emotion,
Originality, Conversation, Energy, Visual Importance, Overall) so the AI's
decisions are more consistent, and so those scores can be *used* --  not
just displayed -- by the planner. Each axis is 0-10 (0 = none, 10 =
extremely high). ``overall`` is preferably supplied by the AI directly
(it has context a simple average can't capture -- e.g. a segment can be
very high Action but low Overall if it's also highly repetitive); when
omitted, :meth:`SegmentScores.computed_overall` falls back to the mean of
whichever axes were actually provided.

This is what :mod:`ai.plan_refiner` reads to decide whether a long KEEP
run of "ordinary gameplay" should be compressed instead of kept whole --
see that module's docstring for how the bug report ("일반 gameplay 구간을
너무 길게 KEEP") is actually fixed, not just described in a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SCORE_AXES = (
    "humor", "action", "story_progress", "excitement", "information",
    "emotion", "originality", "conversation", "energy", "visual_importance",
)
SCORE_MIN = 0.0
SCORE_MAX = 10.0


def _clamp(value: float) -> float:
    return max(SCORE_MIN, min(SCORE_MAX, float(value)))


@dataclass(slots=True)
class SegmentScores:
    """A segment's score on each Feature 5 axis, all 0-10."""

    humor: float = 0.0
    action: float = 0.0
    story_progress: float = 0.0
    excitement: float = 0.0
    information: float = 0.0
    emotion: float = 0.0
    originality: float = 0.0
    conversation: float = 0.0
    energy: float = 0.0
    visual_importance: float = 0.0
    overall: Optional[float] = None

    def __post_init__(self) -> None:
        for axis in SCORE_AXES:
            setattr(self, axis, _clamp(getattr(self, axis)))
        if self.overall is not None:
            self.overall = _clamp(self.overall)

    def computed_overall(self) -> float:
        """``overall`` if the AI supplied one, else the mean of the other axes."""
        if self.overall is not None:
            return self.overall
        values = [getattr(self, axis) for axis in SCORE_AXES]
        return sum(values) / len(values) if values else 0.0

    def to_dict(self) -> dict:
        data = {axis: getattr(self, axis) for axis in SCORE_AXES}
        data["overall"] = self.overall
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SegmentScores":
        kwargs = {axis: float(data[axis]) for axis in SCORE_AXES if axis in data and data[axis] is not None}
        overall = data.get("overall")
        return cls(overall=float(overall) if overall is not None else None, **kwargs)

    @classmethod
    def axis_names(cls) -> tuple:
        return SCORE_AXES
