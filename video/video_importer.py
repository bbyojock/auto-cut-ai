"""Stage 1: import a video file and detect its basic metadata."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import cv2

from core.exceptions import VideoImportError
from models.video_project import VideoProject
from services.logging_service import LoggingService
from utils.constants import SUPPORTED_VIDEO_EXTENSIONS
from video.timeline_sync import (
    cross_check_duration,
    probe_embedded_timecode,
    probe_media_timing,
    timecode_string_to_seconds,
)


class VideoImporter:
    """Validates a video file and builds a :class:`VideoProject` from it.

    Supported containers: mp4, mov, mkv, avi (see
    ``utils.constants.SUPPORTED_VIDEO_EXTENSIONS``). Metadata is read with
    OpenCV, which needs no external binary and keeps this stage dependency
    -light.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("video.importer")

    def import_video(self, file_path: Union[str, Path]) -> VideoProject:
        """Validate ``file_path`` and detect its metadata.

        Args:
            file_path: Path to an mp4/mov/mkv/avi file on disk.

        Returns:
            A fully-populated :class:`VideoProject`.

        Raises:
            VideoImportError: if the file doesn't exist, its extension is
                unsupported, or OpenCV can't open/read it.
        """
        path = Path(file_path).expanduser().resolve()
        self._logger.info("Loading Video...")

        if not path.exists():
            raise VideoImportError(f"Video file not found: {path}")
        if not path.is_file():
            raise VideoImportError(f"Not a file: {path}")
        if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
            raise VideoImportError(
                f"Unsupported video format '{path.suffix}'. Supported formats: {supported}"
            )

        project = VideoProject(file_path=path)
        self._detect_metadata(project)

        self._logger.info(
            "Video loaded: %s (%.2fs, %.2f fps, %dx%d, %d frames)",
            project.file_name,
            project.duration_seconds,
            project.fps,
            project.width,
            project.height,
            project.total_frames,
        )
        return project

    def _detect_metadata(self, project: VideoProject) -> None:
        """Populate ``project``'s metadata fields via OpenCV. Mutates in place."""
        capture = cv2.VideoCapture(str(project.file_path))
        if not capture.isOpened():
            capture.release()
            raise VideoImportError(f"OpenCV could not open video file: {project.file_path}")

        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        finally:
            capture.release()

        if fps <= 0:
            raise VideoImportError(f"Could not detect a valid frame rate for: {project.file_path}")
        if width <= 0 or height <= 0:
            raise VideoImportError(f"Could not detect a valid resolution for: {project.file_path}")

        project.fps = fps
        project.total_frames = total_frames
        project.width = width
        project.height = height
        opencv_duration = total_frames / fps

        # Timestamp-pipeline consistency fix: cross-check OpenCV's derived
        # duration against ffprobe's own container metadata, and record the
        # video/audio stream start_times needed to keep Whisper timestamps
        # aligned to this project's timeline (see video.timeline_sync).
        # Never fatal: if ffprobe is unavailable, behavior is identical to
        # before this fix.
        timing = probe_media_timing(project.file_path, logger=self._logger)
        project.duration_seconds = cross_check_duration(opencv_duration, timing, logger=self._logger)
        if timing.probed:
            project.video_start_time_seconds = timing.video_start_time_seconds or 0.0
            project.audio_start_time_seconds = timing.audio_start_time_seconds
            project.is_vfr = timing.is_vfr
            if timing.is_vfr:
                self._logger.warning(
                    "Video appears to be variable-frame-rate (r_frame_rate=%s, avg_frame_rate=%s). "
                    "Frame-index/timestamp-based seeking (OpenCV CAP_PROP_POS_MSEC) may be less "
                    "accurate on this file; not otherwise corrected by this fix.",
                    timing.r_frame_rate, timing.avg_frame_rate,
                )
            if timing.has_audio_stream and timing.av_sync_offset_seconds is not None:
                self._logger.info(
                    "Stream start_times: video=%.3fs audio=%.3fs (offset=%+.3fs)",
                    project.video_start_time_seconds, timing.audio_start_time_seconds,
                    timing.av_sync_offset_seconds,
                )

        # Resolve-XML-export fix: separately probe the file's EMBEDDED
        # timecode track (distinct from the stream start_time read above
        # -- see timeline_sync.probe_embedded_timecode's docstring). Many
        # screen recordings/capture-card files start their embedded
        # timecode at 01:00:00:00 while start_time is still 0.0; missing
        # this made every Resolve XML export of such a file claim the
        # source starts at 00:00:00:00 when it actually doesn't, which is
        # what causes Resolve to reject the import with "No overlap".
        embedded_timecode = probe_embedded_timecode(project.file_path, logger=self._logger)
        if embedded_timecode:
            project.source_timecode_seconds = timecode_string_to_seconds(embedded_timecode, fps)
            if project.source_timecode_seconds:
                self._logger.info(
                    "Detected embedded source timecode: %s (%.3fs)",
                    embedded_timecode, project.source_timecode_seconds,
                )
