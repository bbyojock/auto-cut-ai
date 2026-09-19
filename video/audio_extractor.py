"""Stage 2: extract the audio track from a video into a temporary WAV file."""

from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from core.exceptions import AudioExtractionError
from models.video_project import VideoProject
from services.logging_service import LoggingService
from utils.constants import WHISPER_AUDIO_SAMPLE_RATE_HZ
from utils.file_utils import get_temp_dir


class AudioExtractor:
    """Extracts mono 16kHz WAV audio from a video using the ``ffmpeg`` binary.

    Requires ``ffmpeg`` to be installed and available on ``PATH``. Output is
    written under AutoCutAI's dedicated temp directory
    (:func:`utils.file_utils.get_temp_dir`) with a random file name, so
    concurrent analyses never collide and the source video is never
    touched. Callers own the returned file and should call :meth:`cleanup`
    once they're done with it (the pipeline does this automatically -- see
    :class:`video.analysis_pipeline.AnalysisPipeline`).
    """

    _TIMEOUT_SECONDS = 600

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("video.audio_extractor")

    def extract(self, project: VideoProject) -> Path:
        """Extract ``project``'s audio track and return the temp WAV path.

        Raises:
            AudioExtractionError: if ``ffmpeg`` is missing, times out, or
                fails (e.g. the source video has no audio track).
        """
        self._logger.info("Extracting Audio...")

        output_path = get_temp_dir() / f"audio_{uuid.uuid4().hex}.wav"
        command = [
            "ffmpeg",
            "-y",
            "-i", str(project.file_path),
            "-vn",
            "-ac", "1",
            "-ar", str(WHISPER_AUDIO_SAMPLE_RATE_HZ),
            "-acodec", "pcm_s16le",
            str(output_path),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AudioExtractionError(
                "ffmpeg was not found on PATH. Install ffmpeg to enable audio extraction."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioExtractionError("Audio extraction timed out.") from exc

        if result.returncode != 0 or not output_path.exists():
            stderr_tail = result.stderr.strip()[-500:] if result.stderr else "unknown error"
            raise AudioExtractionError(
                f"ffmpeg failed to extract audio (exit code {result.returncode}): {stderr_tail}"
            )

        self._logger.info("Audio extracted to temporary file: %s", output_path)
        return output_path

    @staticmethod
    def cleanup(audio_path: Path) -> None:
        """Best-effort deletion of a temp audio file produced by :meth:`extract`."""
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass
