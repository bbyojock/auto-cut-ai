"""The result of automatically deciding which camera angle plays when,
across one synced multicam take (Version 5.4).

Produced by :mod:`ai.multicam_angle_planner`, consumed by
:func:`davinci.multicam_xml_exporter.build_multicam_edit_xml`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List


@dataclass(slots=True)
class AngleSegment:
    """One contiguous stretch of the take where a single camera is active.

    ``start_seconds``/``end_seconds`` are **take-relative** -- seconds
    since the earliest camera in the take started recording, not seconds
    within ``file_path`` itself. Convert to that camera's own local file
    time by subtracting its offset (see
    :attr:`AngleSwitchPlan.camera_offsets_seconds`) before using these
    to seek/cut the actual file.
    """

    start_seconds: float
    end_seconds: float
    camera_tag: str
    file_path: Path
    reason: str

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass(slots=True)
class AngleSwitchPlan:
    """Every angle decision for one multicam take, in timeline order."""

    take_timestamp: datetime
    segments: List[AngleSegment] = field(default_factory=list)
    # camera_tag -> seconds after take_timestamp that camera started
    # recording (0.0 for whichever camera started first).
    camera_offsets_seconds: Dict[str, float] = field(default_factory=dict)

    @property
    def total_duration_seconds(self) -> float:
        return self.segments[-1].end_seconds if self.segments else 0.0

    @property
    def switch_count(self) -> int:
        """Number of angle changes (segment count - 1, floor 0)."""
        return max(0, len(self.segments) - 1)

    @property
    def cameras_used(self) -> List[str]:
        seen: List[str] = []
        for segment in self.segments:
            if segment.camera_tag not in seen:
                seen.append(segment.camera_tag)
        return seen
