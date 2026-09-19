"""Result of importing every video found in a folder (Version 5.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

from models.video_project import VideoProject


@dataclass(slots=True)
class BatchImportResult:
    """What :class:`video.folder_importer.FolderImporter.import_folder` returns.

    A single unreadable file is never fatal for the whole folder -- it is
    recorded in ``skipped`` (path + reason) instead, so a folder of 50
    clips with one corrupt file still yields 49 usable
    :class:`~models.video_project.VideoProject` instances.
    """

    folder_path: Path
    videos: List[VideoProject] = field(default_factory=list)
    skipped: List[Tuple[Path, str]] = field(default_factory=list)

    @property
    def imported_count(self) -> int:
        return len(self.videos)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)
