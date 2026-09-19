"""Stage 1b (Version 5.3): import every supported video found in a folder.

This is a thin batch wrapper around :class:`video.video_importer.VideoImporter`
-- it adds nothing to how a single file is validated/probed, it just scans
a directory and imports each match, collecting failures instead of
letting one bad file abort the whole folder. It is the entry point for
the AI Editor page's "Select Folder... (Batch)" flow, feeding
:class:`services.folder_analysis_service.FolderAnalysisService`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Union

from models.batch_import_result import BatchImportResult
from models.video_project import VideoProject
from services.logging_service import LoggingService
from utils.constants import SUPPORTED_VIDEO_EXTENSIONS
from video.video_importer import VideoImporter


class FolderImporter:
    """Scans a folder for supported video files and imports every one."""

    def __init__(self, importer: Optional[VideoImporter] = None, logger: Optional[logging.Logger] = None) -> None:
        self._importer = importer or VideoImporter()
        self._logger = logger or LoggingService.get_logger("video.folder_importer")

    def scan(self, folder_path: Union[str, Path], recursive: bool = False) -> List[Path]:
        """Return every supported video file under ``folder_path``, name-sorted.

        Raises:
            NotADirectoryError: if ``folder_path`` doesn't exist or isn't a directory.
        """
        folder = Path(folder_path).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            raise NotADirectoryError(f"Not a folder: {folder}")

        pattern = "**/*" if recursive else "*"
        matches = [
            path for path in folder.glob(pattern)
            if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        ]
        return sorted(matches, key=lambda path: path.name.lower())

    def import_folder(self, folder_path: Union[str, Path], recursive: bool = False) -> BatchImportResult:
        """Import every supported video under ``folder_path``.

        Args:
            folder_path: Directory to scan.
            recursive: If True, also scan subfolders (off by default --
                most raw-footage dumps are a single flat folder per card/
                shoot day, and recursing by default risks silently
                sweeping in unrelated clips from nested project folders).

        Returns:
            A :class:`~models.batch_import_result.BatchImportResult` --
            never raises for an individual bad file, only for a missing/
            invalid ``folder_path`` itself (see :meth:`scan`).
        """
        paths = self.scan(folder_path, recursive=recursive)
        videos: List[VideoProject] = []
        skipped: List[tuple[Path, str]] = []

        for path in paths:
            try:
                videos.append(self._importer.import_video(path))
            except Exception as exc:  # noqa: BLE001 - one bad file must never abort the batch
                self._logger.warning("Skipping unreadable file in folder import: %s (%s)", path, exc)
                skipped.append((path, str(exc)))

        self._logger.info(
            "Folder import complete: %s (%d imported, %d skipped)",
            folder_path, len(videos), len(skipped),
        )
        return BatchImportResult(folder_path=Path(folder_path), videos=videos, skipped=skipped)
