"""Result of Version 5.3's folder-based batch import + analysis flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from models.analysis_result import AnalysisResult
from models.multicam_group import MulticamGroup


@dataclass(slots=True)
class FolderClipEntry:
    """One successfully analyzed clip from a folder, with its content-type guess."""

    analysis: AnalysisResult
    content_type: str
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    # Set by FolderAnalysisService._compute_suggested_order for every
    # clip -- the suggested position (0-indexed) for a rough assembly cut,
    # chronological within/across multicam takes when filenames parse,
    # falling back to embedded timecode/mtime/filename otherwise.
    suggested_order: Optional[int] = None
    # Version 5.3.1 (filename-based multicam grouping): set when this
    # clip's filename matched the date+time+camera-tag convention (see
    # video.multicam_grouper), regardless of whether it ended up in a
    # multicam group or standing alone.
    camera_tag: Optional[str] = None
    # Index into FolderAnalysisResult.multicam_groups, or None if this
    # clip wasn't part of a multicam take (no other clip shared its
    # filename timestamp within the sync tolerance).
    multicam_group_index: Optional[int] = None

    @property
    def file_name(self) -> str:
        return self.analysis.video.file_name

    @property
    def is_multicam(self) -> bool:
        return self.multicam_group_index is not None


@dataclass(slots=True)
class FolderAnalysisResult:
    """Everything produced by analyzing one folder of raw footage."""

    folder_path: Path
    clips: List[FolderClipEntry] = field(default_factory=list)
    # (path, reason) for every file that failed to import or analyze.
    skipped: List[Tuple[Path, str]] = field(default_factory=list)
    dominant_content_type: str = "unknown"
    recommended_profile: str = "none"
    # Version 5.3.1: every detected multicam take (2+ clips sharing a
    # filename timestamp). Empty when no clips followed the naming
    # convention, or none of them clustered together.
    multicam_groups: List[MulticamGroup] = field(default_factory=list)

    @property
    def is_documentary(self) -> bool:
        return self.dominant_content_type == "documentary"

    @property
    def is_variety(self) -> bool:
        return self.dominant_content_type == "variety"

    @property
    def is_multicam(self) -> bool:
        return len(self.multicam_groups) > 0

    @property
    def clip_count(self) -> int:
        return len(self.clips)
