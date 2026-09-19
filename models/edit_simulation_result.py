"""Non-destructive analysis of an EditPlan's effect (Version 3.5).

Produced by :class:`ai.edit_simulator.EditSimulator`. Nothing here touches
the source video -- every number is computed purely from an
:class:`models.edit_plan.EditPlan`'s segment timestamps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class EditSimulationResult:
    """What an EditPlan *would* produce, without editing anything."""

    original_length_seconds: float
    estimated_final_length_seconds: float
    removed_percentage: float
    cut_count: int
    average_clip_length_seconds: float
    longest_clip_seconds: float
    shortest_clip_seconds: float
    ai_confidence: float
    validation_warnings: List[str] = field(default_factory=list)
    # Version 4.6, Feature 5/9: how much COMPRESS contributed. Defaults to
    # 0 so any pre-4.6 caller/comparison of this dataclass is unaffected.
    compressed_segment_count: int = 0
    compressed_seconds_saved: float = 0.0

    def to_dict(self) -> dict:
        return {
            "original_length_seconds": self.original_length_seconds,
            "estimated_final_length_seconds": self.estimated_final_length_seconds,
            "removed_percentage": self.removed_percentage,
            "cut_count": self.cut_count,
            "average_clip_length_seconds": self.average_clip_length_seconds,
            "longest_clip_seconds": self.longest_clip_seconds,
            "shortest_clip_seconds": self.shortest_clip_seconds,
            "ai_confidence": self.ai_confidence,
            "validation_warnings": list(self.validation_warnings),
            "compressed_segment_count": self.compressed_segment_count,
            "compressed_seconds_saved": self.compressed_seconds_saved,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
