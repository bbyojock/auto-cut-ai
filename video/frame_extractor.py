"""Stage 4: extract preview frames from a video (Version 2 fixed-interval;
Version 4.6 adds adaptive, motion-aware sampling -- Feature 8).
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional, Tuple

import cv2
import numpy as np

from core.exceptions import FrameExtractionError
from models.frame import Frame, FrameCollection
from models.video_project import VideoProject
from models.visual_activity import VisualActivitySegment, VisualActivityTimeline
from services.logging_service import LoggingService
from utils.constants import DEFAULT_FRAME_INTERVAL_SECONDS
from utils.file_utils import get_temp_dir

# Feature 8 tuning. A coarse first pass samples every `_COARSE_PROBE_SECONDS`
# to find where motion happens at all, cheaply, before spending any extra
# reads on dense re-sampling -- so adaptive sampling never costs much more
# than the old fixed-interval pass on a mostly-static video.
_COARSE_PROBE_SECONDS = 1.0
_MOTION_DOWNSCALE_SIZE = (64, 36)  # tiny grayscale frames are plenty for a diff score
_SCENE_CHANGE_MOTION_THRESHOLD = 0.35


class FrameExtractor:
    """Samples preview frames from a video, either at a fixed interval or adaptively.

    :meth:`extract` (Version 2) is unchanged and still the default for any
    caller that doesn't need motion data. :meth:`extract_adaptive`
    (Version 4.6, Feature 8) additionally samples more densely around
    motion/scene-change windows and less densely during static stretches,
    and returns a :class:`models.visual_activity.VisualActivityTimeline`
    alongside the frames -- real per-window motion scores computed from
    actual frame-to-frame pixel differences, not a placeholder.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("video.frame_extractor")

    def extract(
        self,
        project: VideoProject,
        interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS,
    ) -> FrameCollection:
        """Extract one frame every ``interval_seconds`` from ``project``.

        Args:
            project: A :class:`VideoProject` that has already been imported
                (i.e. ``project.is_analyzed`` is True).
            interval_seconds: How often to sample a frame. Must be > 0.

        Raises:
            FrameExtractionError: if ``interval_seconds`` isn't positive,
                the project has no metadata yet, or the video can't be
                reopened for reading.
        """
        self._validate_project(project, interval_seconds)
        self._logger.info("Extracting Frames...")

        capture = self._open_capture(project)
        output_dir = self._new_output_dir()
        collection = FrameCollection(interval_seconds=interval_seconds)
        try:
            timestamp = 0.0
            index = 0
            while timestamp < project.duration_seconds:
                frame = self._read_frame_at(capture, timestamp)
                if frame is not None:
                    frame_path = output_dir / f"frame_{index:05d}.jpg"
                    cv2.imwrite(str(frame_path), frame)
                    collection.add(Frame(index=index, timestamp_seconds=timestamp, file_path=frame_path))
                    index += 1
                timestamp += interval_seconds
        finally:
            capture.release()

        self._logger.info(
            "Extracted %d preview frame(s) every %.1fs into %s", len(collection), interval_seconds, output_dir,
        )
        return collection

    def extract_adaptive(
        self,
        project: VideoProject,
        base_interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS,
        min_interval_seconds: Optional[float] = None,
        max_interval_seconds: Optional[float] = None,
        motion_threshold: float = 0.12,
    ) -> Tuple[FrameCollection, VisualActivityTimeline]:
        """Motion-aware sampling (Version 4.6, Feature 8).

        Two passes:

        1. A cheap coarse scan every :data:`_COARSE_PROBE_SECONDS`,
           computing a real frame-to-frame motion score (mean absolute
           difference of tiny downscaled grayscale frames, 0..1) --
           this is what tells us *where* the interesting parts of the
           video are, at minimal cost.
        2. A final sampling pass that uses ``min_interval_seconds`` inside
           any window whose coarse motion score exceeds
           ``motion_threshold`` (combat, scene changes, sudden movement),
           and ``max_interval_seconds`` everywhere else (static/calm
           stretches) -- exactly "sample more frequently around
           combat/explosions/sudden movement... sample less frequently
           during static scenes" from the spec.

        Returns:
            ``(frames, activity)`` where ``activity`` carries a real
            motion score for every sampled interval, later fed into
            :mod:`ai.event_recognizer` and :class:`ai.context_builder.ContextBuilder`.
        """
        min_interval = min_interval_seconds or max(0.5, base_interval_seconds / 4.0)
        max_interval = max_interval_seconds or (base_interval_seconds * 2.0)
        self._validate_project(project, base_interval_seconds)
        self._logger.info(
            "Extracting Frames (adaptive: min=%.2fs max=%.2fs threshold=%.2f)...",
            min_interval, max_interval, motion_threshold,
        )

        coarse_scores = self._coarse_motion_scan(project)
        final_timestamps = self._plan_adaptive_timestamps(
            project.duration_seconds, coarse_scores, min_interval, max_interval, motion_threshold,
        )

        capture = self._open_capture(project)
        output_dir = self._new_output_dir()
        collection = FrameCollection(interval_seconds=base_interval_seconds)
        activity_segments: List[VisualActivitySegment] = []
        try:
            previous_gray = None
            for index, timestamp in enumerate(final_timestamps):
                frame = self._read_frame_at(capture, timestamp)
                if frame is None:
                    continue
                frame_path = output_dir / f"frame_{index:05d}.jpg"
                cv2.imwrite(str(frame_path), frame)
                collection.add(Frame(index=index, timestamp_seconds=timestamp, file_path=frame_path))

                gray_small = self._to_small_gray(frame)
                motion = self._motion_score(previous_gray, gray_small)
                previous_gray = gray_small

                if index > 0:
                    prev_ts = final_timestamps[index - 1]
                    activity_segments.append(
                        VisualActivitySegment(
                            start_seconds=prev_ts, end_seconds=timestamp, motion_score=motion,
                            is_scene_change=motion >= _SCENE_CHANGE_MOTION_THRESHOLD,
                        )
                    )
        finally:
            capture.release()

        self._logger.info(
            "Extracted %d preview frame(s) adaptively (%d dense/motion window(s) detected) into %s",
            len(collection), sum(1 for s in coarse_scores if s[1] >= motion_threshold), output_dir,
        )
        return collection, VisualActivityTimeline(segments=activity_segments)

    # ------------------------------------------------------------------
    @staticmethod
    def cleanup(frames: FrameCollection) -> None:
        """Best-effort deletion of every temp frame file produced by :meth:`extract`."""
        output_dir = frames.frames[0].file_path.parent if frames.frames else None
        for frame in frames.frames:
            try:
                frame.file_path.unlink(missing_ok=True)
            except OSError:
                pass
        if output_dir is not None:
            try:
                output_dir.rmdir()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_project(project: VideoProject, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise FrameExtractionError("interval_seconds must be greater than zero.")
        if not project.is_analyzed or not project.fps or not project.duration_seconds:
            raise FrameExtractionError(
                "VideoProject has no metadata yet; import the video before extracting frames."
            )

    @staticmethod
    def _open_capture(project: VideoProject) -> cv2.VideoCapture:
        capture = cv2.VideoCapture(str(project.file_path))
        if not capture.isOpened():
            capture.release()
            raise FrameExtractionError(f"Could not reopen video for frame extraction: {project.file_path}")
        return capture

    @staticmethod
    def _new_output_dir():
        output_dir = get_temp_dir() / f"frames_{uuid.uuid4().hex}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @staticmethod
    def _read_frame_at(capture: cv2.VideoCapture, timestamp_seconds: float):
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_seconds * 1000.0)
        success, image = capture.read()
        return image if success else None

    @staticmethod
    def _to_small_gray(frame) -> "np.ndarray":
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, _MOTION_DOWNSCALE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0

    @staticmethod
    def _motion_score(previous_gray, current_gray) -> float:
        """Mean absolute pixel difference between two small grayscale frames, 0..1."""
        if previous_gray is None:
            return 0.0
        diff = np.abs(current_gray - previous_gray)
        return float(min(1.0, diff.mean() * 4.0))  # *4 spreads the typically-small raw diff over 0..1 more usefully

    def _coarse_motion_scan(self, project: VideoProject) -> List[Tuple[float, float]]:
        """Returns ``[(timestamp, motion_score), ...]`` sampled every ``_COARSE_PROBE_SECONDS``."""
        capture = self._open_capture(project)
        scores: List[Tuple[float, float]] = []
        try:
            timestamp = 0.0
            previous_gray = None
            while timestamp < project.duration_seconds:
                frame = self._read_frame_at(capture, timestamp)
                if frame is not None:
                    gray_small = self._to_small_gray(frame)
                    motion = self._motion_score(previous_gray, gray_small)
                    scores.append((timestamp, motion))
                    previous_gray = gray_small
                timestamp += _COARSE_PROBE_SECONDS
        finally:
            capture.release()
        return scores

    @staticmethod
    def _plan_adaptive_timestamps(
        duration_seconds: float,
        coarse_scores: List[Tuple[float, float]],
        min_interval: float,
        max_interval: float,
        motion_threshold: float,
    ) -> List[float]:
        """Builds the final list of timestamps to actually extract frames at.

        Walks the timeline in ``_COARSE_PROBE_SECONDS`` steps; while inside
        a window whose coarse motion score cleared ``motion_threshold``,
        emits timestamps every ``min_interval`` (dense); otherwise every
        ``max_interval`` (sparse).
        """
        if not coarse_scores:
            return [0.0]

        def motion_at(t: float) -> float:
            # Nearest coarse sample at/after t (coarse scores are ordered).
            for ts, score in coarse_scores:
                if ts >= t - 1e-6:
                    return score
            return coarse_scores[-1][1]

        timestamps: List[float] = [0.0]
        cursor = 0.0
        while True:
            is_hot = motion_at(cursor) >= motion_threshold
            step = min_interval if is_hot else max_interval
            cursor += step
            if cursor >= duration_seconds:
                break
            timestamps.append(cursor)
        return timestamps
