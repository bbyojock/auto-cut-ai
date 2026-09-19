"""Orchestrates Version 5.3's folder-based batch import + analysis flow.

    Folder -> [import every video] -> [analyze every video] ->
    [guess documentary/variety per clip] -> [group multicam takes by
    filename] -> [suggest a clip order]

This is the entry point behind the AI Editor page's "Select Folder...
(Batch)" button: drop in a folder of raw footage and get back every clip
already analyzed, tagged with a best-effort content-type guess and, when
filenames follow the date+time+camera-tag convention (see
:mod:`video.multicam_grouper`), grouped into multicam takes. Nothing here
edits video, cuts anything, switches camera angles, or talks to DaVinci
Resolve; it hands back a
:class:`~models.folder_analysis_result.FolderAnalysisResult` that the UI
(or, per-clip, the existing single-video EditPlan flow) builds on.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from ai.content_classifier import (
    CONTENT_TYPE_UNKNOWN,
    ContentTypeGuess,
    classify_content_type,
    dominant_content_type,
    recommended_game_profile,
)
from core.cancellation import CancellationToken
from models.folder_analysis_result import FolderAnalysisResult, FolderClipEntry
from models.multicam_group import MulticamGroup
from services.edit_plan_cache_service import EditPlanCacheService
from services.logging_service import LoggingService
from utils.constants import DEFAULT_FRAME_INTERVAL_SECONDS
from video.analysis_pipeline import AnalysisPipeline, BatchProgressCallback
from video.folder_importer import FolderImporter
from video.multicam_grouper import DEFAULT_SYNC_TOLERANCE_SECONDS, MulticamGrouper


class FolderAnalysisService:
    """Ties :class:`FolderImporter` + :class:`AnalysisPipeline` + content-type
    and multicam detection into the single call the AI Editor page's
    folder flow needs.
    """

    def __init__(
        self,
        folder_importer: Optional[FolderImporter] = None,
        analysis_pipeline: Optional[AnalysisPipeline] = None,
        multicam_grouper: Optional[MulticamGrouper] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._folder_importer = folder_importer or FolderImporter()
        self._pipeline = analysis_pipeline or AnalysisPipeline()
        self._multicam_grouper = multicam_grouper or MulticamGrouper()
        self._logger = logger or LoggingService.get_logger("services.folder_analysis")

    def analyze_folder(
        self,
        folder_path: Union[str, Path],
        recursive: bool = False,
        frame_interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS,
        multicam_sync_tolerance_seconds: float = DEFAULT_SYNC_TOLERANCE_SECONDS,
        on_progress: Optional[BatchProgressCallback] = None,
        cancel_token: Optional[CancellationToken] = None,
        cache_service: Optional[EditPlanCacheService] = None,
        whisper_backend: Optional[str] = None,
        whisper_model: Optional[str] = None,
    ) -> FolderAnalysisResult:
        """Import + analyze every video in ``folder_path`` and classify each one.

        Never raises for an individual bad/unanalyzable file -- those are
        reported in the result's ``skipped`` list instead (see
        :class:`~models.folder_analysis_result.FolderAnalysisResult`).
        Raises only for a missing/invalid ``folder_path`` itself, or if
        ``cancel_token`` is cancelled mid-run.

        Args:
            multicam_sync_tolerance_seconds: How many seconds apart two
                clips' filename timestamps can be and still count as the
                same multicam take (cameras started by hand rarely start
                in the exact same frame). See :mod:`video.multicam_grouper`.
            cache_service: Optional -- when given (together with
                ``whisper_backend``/``whisper_model``), each clip's freshly
                computed :class:`~models.analysis_result.AnalysisResult` is
                also written to :class:`services.edit_plan_cache_service.EditPlanCacheService`'s
                analysis cache, so opening that same clip afterwards in the
                normal single-video AI Editor flow reuses this batch's
                Whisper/frame-extraction work instead of redoing it.
        """
        import_result = self._folder_importer.import_folder(folder_path, recursive=recursive)

        if not import_result.videos:
            return FolderAnalysisResult(
                folder_path=Path(folder_path),
                clips=[],
                skipped=list(import_result.skipped),
                dominant_content_type=CONTENT_TYPE_UNKNOWN,
                recommended_profile=recommended_game_profile(CONTENT_TYPE_UNKNOWN),
            )

        # Filenames alone decide multicam grouping -- it doesn't depend on
        # (or wait for) the Whisper/frame analysis below, so do it first.
        multicam_groups, solo_clips = self._multicam_grouper.group_files(
            [video.file_path for video in import_result.videos],
            tolerance_seconds=multicam_sync_tolerance_seconds,
        )
        camera_tag_by_path: Dict[Path, str] = {}
        group_index_by_path: Dict[Path, int] = {}
        for group_index, group in enumerate(multicam_groups):
            for clip in group.clips:
                camera_tag_by_path[clip.file_path] = clip.camera_tag
                group_index_by_path[clip.file_path] = group_index
        for clip in solo_clips:
            camera_tag_by_path[clip.file_path] = clip.camera_tag

        batch_items = self._pipeline.run_batch(
            [video.file_path for video in import_result.videos],
            frame_interval_seconds=frame_interval_seconds,
            on_progress=on_progress,
            cancel_token=cancel_token,
        )

        clips: List[FolderClipEntry] = []
        guesses: List[ContentTypeGuess] = []
        analysis_failures = list(import_result.skipped)

        for item in batch_items:
            if item.result is None:
                self._logger.warning("Dropping %s from folder analysis: %s", item.file_path, item.error)
                analysis_failures.append((item.file_path, item.error or "Unknown analysis error"))
                continue
            if cache_service is not None and whisper_backend and whisper_model:
                try:
                    key = cache_service.analysis_cache_key(
                        item.file_path, whisper_backend, whisper_model, frame_interval_seconds,
                    )
                    cache_service.save_analysis(key, item.result)
                except Exception:  # noqa: BLE001 - caching is an optimization, never fatal
                    self._logger.warning("Could not warm the analysis cache for %s", item.file_path)

            guess = classify_content_type(item.result)
            guesses.append(guess)
            clips.append(
                FolderClipEntry(
                    analysis=item.result,
                    content_type=guess.content_type,
                    confidence=guess.confidence,
                    reasons=guess.reasons,
                    camera_tag=camera_tag_by_path.get(item.file_path),
                    multicam_group_index=group_index_by_path.get(item.file_path),
                )
            )

        dominant = dominant_content_type(guesses)
        clips = self._compute_suggested_order(clips, multicam_groups)

        self._logger.info(
            "Folder analysis complete: %s -> %d clips, dominant_type=%s, "
            "%d multicam take(s) (%d skipped)",
            folder_path, len(clips), dominant, len(multicam_groups), len(analysis_failures),
        )

        return FolderAnalysisResult(
            folder_path=Path(folder_path),
            clips=clips,
            skipped=analysis_failures,
            dominant_content_type=dominant,
            recommended_profile=recommended_game_profile(dominant),
            multicam_groups=multicam_groups,
        )

    @staticmethod
    def _compute_suggested_order(
        clips: List[FolderClipEntry], multicam_groups: List[MulticamGroup],
    ) -> List[FolderClipEntry]:
        """Order clips into a rough assembly-cut suggestion.

        Preference order for each clip's sort key, most to least reliable:

        1. Its multicam take's filename timestamp (see
           :mod:`video.multicam_grouper`) -- cameras belonging to the same
           take sort together, in filename-timestamp order across takes,
           and by camera tag (alphabetical) within a take so the same
           camera lands in the same relative slot every time.
        2. Embedded source timecode (many cameras/capture cards burn a
           real time-of-day timecode into the file -- see
           :attr:`models.video_project.VideoProject.source_timecode_seconds`).
        3. Filesystem modification time -- still usually reflects
           recording order for footage copied straight off a card.
        4. Filename -- so ordering is always fully deterministic even
           with zero usable metadata.

        This is a *suggestion*, not a cut: nothing here trims, joins, or
        switches between camera angles -- it only sets each entry's
        ``suggested_order`` for the UI to display and, later, for
        Resolve/XML export to honor.
        """
        group_timestamp_by_index = {index: group.timestamp for index, group in enumerate(multicam_groups)}

        def sort_key(entry: FolderClipEntry):
            if entry.multicam_group_index is not None:
                group_timestamp = group_timestamp_by_index[entry.multicam_group_index]
                return (0, group_timestamp, (entry.camera_tag or "").lower())

            video = entry.analysis.video
            if video.source_timecode_seconds is not None:
                return (1, video.source_timecode_seconds, video.file_name.lower())
            try:
                mtime = video.file_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (2, mtime, video.file_name.lower())

        ordered = sorted(clips, key=sort_key)
        for index, entry in enumerate(ordered):
            entry.suggested_order = index
        return ordered
