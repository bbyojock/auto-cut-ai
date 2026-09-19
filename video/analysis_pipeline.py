"""Orchestrates the full Version 2 analysis pipeline.

    Video -> Audio -> Whisper -> Frames -> AnalysisResult

This class exists so callers (a future UI page, a CLI script, or v3's
AI-editing logic) have a single entry point instead of wiring the four
stages together by hand each time. Nothing in this module edits the source
video, calls Gemini, or talks to DaVinci Resolve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

from core.cancellation import CancellationToken
from core.exceptions import AutoCutAIError, OperationCancelledError
from models.analysis_result import AnalysisResult
from models.generation_progress import GenerationStage
from services.logging_service import LoggingService
from utils.constants import DEFAULT_FRAME_INTERVAL_SECONDS
from video.audio_extractor import AudioExtractor
from video.frame_extractor import FrameExtractor
from video.timeline_sync import apply_av_offset_to_transcript
from video.transcriber import WhisperTranscriber
from video.video_importer import VideoImporter

ProgressCallback = Callable[[GenerationStage], None]

# Version 5.3 (folder-based batch import): ``(file_index, total_files,
# current_file_path, stage)``, invoked once per pipeline stage per file so
# a UI can render e.g. "Analyzing 3/12: interview_04.mp4 - Running Whisper".
BatchProgressCallback = Callable[[int, int, Path, GenerationStage], None]


@dataclass(slots=True)
class BatchAnalysisItem:
    """One file's outcome from :meth:`AnalysisPipeline.run_batch`.

    Exactly one of ``result``/``error`` is set. Modeled as a result object
    rather than raising per-file so a single corrupt/unreadable clip in a
    folder of dozens never aborts the rest of the batch.
    """

    file_path: Path
    result: Optional[AnalysisResult] = None
    error: Optional[str] = None


class AnalysisPipeline:
    """Runs Video -> Audio -> Whisper -> Frames and returns an AnalysisResult.

    Every stage is injected (with a sensible default) so callers -- and
    unit tests -- can swap any one of them out independently, e.g. a fake
    ``WhisperTranscriber`` in tests that shouldn't load a real model.

    Version 4 adds two purely optional parameters to :meth:`run`:
    ``on_progress`` (Feature 2: the AI Progress Window) and ``cancel_token``
    (Feature 3: Cancel button). Neither changes what a caller that omits
    them experiences -- ``on_progress`` defaults to a no-op and
    ``cancel_token`` defaults to ``None`` (never checked), so every
    pre-Version-4 call site (e.g. ``scripts/analyze_and_plan.py``) behaves
    exactly as before.
    """

    def __init__(
        self,
        importer: Optional[VideoImporter] = None,
        audio_extractor: Optional[AudioExtractor] = None,
        transcriber: Optional[WhisperTranscriber] = None,
        frame_extractor: Optional[FrameExtractor] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._importer = importer or VideoImporter()
        self._audio_extractor = audio_extractor or AudioExtractor()
        self._transcriber = transcriber or WhisperTranscriber()
        self._frame_extractor = frame_extractor or FrameExtractor()
        self._logger = logger or LoggingService.get_logger("video.pipeline")

    def run(
        self,
        file_path: Union[str, Path],
        frame_interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS,
        keep_audio_file: bool = False,
        on_progress: Optional[ProgressCallback] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> AnalysisResult:
        """Run every stage against ``file_path`` and return the AnalysisResult.

        Args:
            file_path: Path to the source video (mp4/mov/mkv/avi).
            frame_interval_seconds: How often to sample a preview frame.
            keep_audio_file: If False (default), the temporary WAV file is
                deleted once transcription finishes. Pass True to keep it
                (e.g. for debugging); the caller then owns its cleanup.
            on_progress: Optional callback invoked with a
                :class:`models.generation_progress.GenerationStage` right
                before each stage starts (Version 4, Feature 2).
            cancel_token: Optional :class:`core.cancellation.CancellationToken`,
                checked between stages (Version 4, Feature 3). A stage
                already in progress always finishes -- cancellation takes
                effect at the next stage boundary, never mid-stage, so
                nothing is ever left half-written.

        Returns:
            The completed :class:`AnalysisResult`.

        Raises:
            core.exceptions.AutoCutAIError: (or a subclass) if any stage
                fails. Temp files created by earlier, already-successful
                stages are cleaned up before re-raising.
            core.exceptions.OperationCancelledError: if ``cancel_token``
                was cancelled before a stage boundary was reached.
        """
        notify = on_progress or (lambda _stage: None)
        started_at = datetime.now()
        audio_path: Optional[Path] = None

        try:
            notify(GenerationStage.LOADING_VIDEO)
            project = self._importer.import_video(file_path)

            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            notify(GenerationStage.EXTRACTING_AUDIO)
            audio_path = self._audio_extractor.extract(project)

            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            notify(GenerationStage.RUNNING_WHISPER)
            transcript = self._transcriber.transcribe(audio_path)
            # Timestamp-pipeline consistency fix: Whisper's timestamps are
            # relative to the extracted WAV's own start, which is the audio
            # stream's start_time -- not necessarily the video stream's.
            # Re-base onto the video's own timeline (the one FrameExtractor
            # and everything downstream already assumes) before this
            # transcript goes anywhere else. A no-op when the two streams
            # already share the same start_time (the common case).
            av_offset = None
            if project.audio_start_time_seconds is not None:
                av_offset = project.audio_start_time_seconds - project.video_start_time_seconds
            transcript = apply_av_offset_to_transcript(transcript, av_offset, logger=self._logger)

            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            notify(GenerationStage.EXTRACTING_FRAMES)
            frames, visual_activity = self._frame_extractor.extract_adaptive(project, frame_interval_seconds)
        except AutoCutAIError:
            if audio_path is not None:
                self._audio_extractor.cleanup(audio_path)
            raise

        if not keep_audio_file:
            self._audio_extractor.cleanup(audio_path)
            audio_path = None

        result = AnalysisResult(
            video=project,
            transcript=transcript,
            frames=frames,
            audio_path=audio_path,
            started_at=started_at,
            finished_at=datetime.now(),
            visual_activity=visual_activity,
        )

        self._logger.info(
            "Analysis Complete. duration=%.2fs language=%s segments=%d frames=%d activity_windows=%d processing=%.2fs",
            project.duration_seconds or 0.0,
            transcript.language,
            len(transcript.segments),
            len(frames),
            len(visual_activity),
            result.processing_seconds or 0.0,
        )
        return result

    def run_batch(
        self,
        file_paths: Sequence[Union[str, Path]],
        frame_interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS,
        keep_audio_file: bool = False,
        on_progress: Optional[BatchProgressCallback] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> List[BatchAnalysisItem]:
        """Run :meth:`run` over every file in ``file_paths`` (Version 5.3).

        Powers the AI Editor page's folder-import flow
        (:class:`services.folder_analysis_service.FolderAnalysisService`):
        a folder can easily contain one file with a bad codec or a
        corrupted header, and that must never take the other 49 clips
        down with it, so per-file failures are collected into the
        returned list's ``error`` field instead of raised.

        Cancellation is still fatal to the whole batch (a user hitting
        Cancel wants everything to stop, not just the current file), and
        propagates immediately as :class:`core.exceptions.OperationCancelledError`.

        Args:
            file_paths: Video files to analyze, in the order they should
                be processed (and the order results are returned in).
            frame_interval_seconds: Forwarded to :meth:`run` for every file.
            keep_audio_file: Forwarded to :meth:`run` for every file.
            on_progress: Optional callback invoked before each stage of
                each file with ``(file_index, total_files, file_path, stage)``,
                1-indexed.
            cancel_token: Optional, checked between files and (via :meth:`run`)
                between stages within a file.

        Returns:
            One :class:`BatchAnalysisItem` per input file, in order.
        """
        notify = on_progress or (lambda *_args: None)
        total = len(file_paths)
        items: List[BatchAnalysisItem] = []

        for index, raw_path in enumerate(file_paths, start=1):
            path = Path(raw_path)
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()

            def _relay_stage(stage: GenerationStage, _index: int = index, _path: Path = path) -> None:
                notify(_index, total, _path, stage)

            try:
                result = self.run(
                    path,
                    frame_interval_seconds=frame_interval_seconds,
                    keep_audio_file=keep_audio_file,
                    on_progress=_relay_stage,
                    cancel_token=cancel_token,
                )
                items.append(BatchAnalysisItem(file_path=path, result=result))
            except OperationCancelledError:
                raise
            except AutoCutAIError as exc:
                self._logger.warning("Batch analysis failed for %s: %s", path, exc)
                items.append(BatchAnalysisItem(file_path=path, error=str(exc)))

        self._logger.info(
            "Batch analysis complete: %d/%d files analyzed successfully",
            sum(1 for item in items if item.result is not None), total,
        )
        return items
