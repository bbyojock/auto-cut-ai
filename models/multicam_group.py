"""A set of clips shot simultaneously across multiple cameras (Version 5.3.1).

Grouping is derived purely from filenames via
:mod:`video.multicam_grouper` -- see that module's docstring for the
naming convention this expects (date + time + camera tag).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass(slots=True)
class MulticamClip:
    """One camera's file within a multicam take (or a lone, unmatched clip)."""

    file_path: Path
    camera_tag: str
    timestamp: Optional[datetime] = None

    @property
    def file_name(self) -> str:
        return self.file_path.name


@dataclass(slots=True)
class MulticamGroup:
    """Every clip whose filename timestamp puts it in the same take."""

    timestamp: datetime
    clips: List[MulticamClip] = field(default_factory=list)

    @property
    def camera_count(self) -> int:
        return len(self.clips)

    @property
    def camera_tags(self) -> List[str]:
        return [clip.camera_tag for clip in self.clips]
