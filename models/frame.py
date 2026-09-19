"""Extracted preview frame models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List


@dataclass(slots=True)
class Frame:
    """A single preview frame sampled from a video at a given timestamp."""

    index: int
    timestamp_seconds: float
    file_path: Path

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp_seconds": self.timestamp_seconds,
            "file_path": str(self.file_path),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Frame":
        return cls(
            index=int(data["index"]),
            timestamp_seconds=float(data["timestamp_seconds"]),
            file_path=Path(data["file_path"]),
        )


@dataclass(slots=True)
class FrameCollection:
    """An ordered set of preview frames plus the interval they were sampled at."""

    interval_seconds: float
    frames: List[Frame] = field(default_factory=list)

    def add(self, frame: Frame) -> None:
        self.frames.append(frame)

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self) -> Iterator[Frame]:
        return iter(self.frames)

    def to_dict(self) -> dict:
        return {
            "interval_seconds": self.interval_seconds,
            "frame_count": len(self.frames),
            "frames": [frame.to_dict() for frame in self.frames],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FrameCollection":
        """Reconstruct a :class:`FrameCollection` from :meth:`to_dict`'s output.

        Note: the underlying JPEG files referenced by each :class:`Frame`
        are temporary and are *not* preserved by the Version 4 EditPlan
        cache/save-load services -- only frame *metadata* (count, interval,
        timestamps) round-trips. Version 3's AI editing brain never reads
        frame images anyway (see :class:`ai.context_builder.ContextBuilder`),
        so this is sufficient for cache/reload purposes.
        """
        return cls(
            interval_seconds=float(data.get("interval_seconds", 0.0)),
            frames=[Frame.from_dict(f) for f in data.get("frames", [])],
        )
