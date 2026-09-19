"""Stage 3: transcribe extracted audio with faster-whisper."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from faster_whisper import WhisperModel

from core.exceptions import TranscriptionError
from models.transcript import Transcript, TranscriptSegment
from services.logging_service import LoggingService
from utils.constants import (
    DEFAULT_WHISPER_COMPUTE_TYPE,
    DEFAULT_WHISPER_MODEL_SIZE,
    GPU_RUNTIME_ERROR_SIGNATURES,
    WHISPER_BACKEND_AUTO,
    WHISPER_BACKEND_CPU,
)
from video.whisper_runtime_manager import WhisperRuntimeManager


class _GpuRuntimeFailure(Exception):
    """Internal signal only: a GPU/CUDA library problem was detected at
    model-load or transcription time (as opposed to a genuine transcription
    error). Never escapes :class:`WhisperTranscriber` -- callers only ever
    see a successful :class:`Transcript` or a :class:`TranscriptionError`.
    """


class WhisperTranscriber:
    """Transcribes audio with faster-whisper, auto-detecting the language.

    The underlying ``WhisperModel`` is loaded lazily on first use and
    cached on the instance -- loading it is the single most expensive step
    in the whole pipeline, so it should only ever happen once per session
    even if several videos are analyzed one after another.

    GPU/CPU selection is fully automatic and safe: :class:`WhisperRuntimeManager`
    trusts ctranslate2's CUDA capability report. If the actual Whisper model
    load or transcription fails on GPU, :meth:`transcribe` logs the exact
    runtime error, forces a CPU fallback, and retries once automatically.
    """

    def __init__(
        self,
        model_size: str = DEFAULT_WHISPER_MODEL_SIZE,
        compute_type: str = DEFAULT_WHISPER_COMPUTE_TYPE,
        logger: Optional[logging.Logger] = None,
        backend: str = WHISPER_BACKEND_AUTO,
        runtime_manager: Optional[WhisperRuntimeManager] = None,
    ) -> None:
        self._model_size = model_size
        self._cpu_compute_type = compute_type
        self._logger = logger or LoggingService.get_logger("video.transcriber")
        self._backend_preference = backend
        self._runtime_manager = runtime_manager or WhisperRuntimeManager()
        self._model: Optional[WhisperModel] = None
        self._active_device: Optional[str] = None
        self._forced_cpu = False

    def transcribe(self, audio_path: Path) -> Transcript:
        """Transcribe ``audio_path`` into a full, timestamped :class:`Transcript`.

        Language is auto-detected by faster-whisper (no ``language=`` is
        passed in). Timestamps come straight from the model's own segment
        boundaries, so they stay precise to the millisecond.

        Raises:
            TranscriptionError: if transcription fails for a reason other
                than a recoverable GPU runtime problem (which is instead
                handled by falling back to CPU and retrying automatically).
        """
        self._logger.info("Running Whisper...")
        try:
            model = self._load_model()
            return self._run_transcription(model, audio_path)
        except _GpuRuntimeFailure as exc:
            if self._forced_cpu:
                # Already retried on CPU once; a second failure is a real error.
                raise TranscriptionError(
                    f"Whisper transcription failed even after falling back to CPU: {exc}"
                ) from exc
            self._logger.warning(
                "GPU runtime problem detected while running Whisper (%s). "
                "Automatically switching to CPU and retrying.", exc,
                exc_info=True,
            )
            self._force_cpu_and_reset()
            model = self._load_model()
            return self._run_transcription(model, audio_path)

    def _run_transcription(self, model: WhisperModel, audio_path: Path) -> Transcript:
        try:
            segments_iter, info = model.transcribe(str(audio_path), vad_filter=True)
            segments = [
                TranscriptSegment(
                    start_seconds=segment.start,
                    end_seconds=segment.end,
                    text=segment.text.strip(),
                )
                for segment in segments_iter
                if segment.text and segment.text.strip()
            ]
        except Exception as exc:  # noqa: BLE001 - classified below
            if self._active_device == "cuda" and self._looks_like_gpu_runtime_error(exc):
                raise _GpuRuntimeFailure(str(exc)) from exc
            raise TranscriptionError(f"Whisper transcription failed: {exc}") from exc

        transcript = Transcript(
            language=info.language,
            language_probability=info.language_probability,
            segments=segments,
        )
        self._logger.info(
            "Transcription complete: language=%s (%.0f%% confidence), %d segment(s)",
            transcript.language,
            transcript.language_probability * 100,
            len(transcript.segments),
        )
        return transcript

    def _load_model(self) -> WhisperModel:
        """Load and cache the WhisperModel, only doing real work once."""
        if self._model is None:
            decision = self._runtime_manager.resolve_device_and_compute_type(self._backend_preference)
            compute_type = self._cpu_compute_type if decision.device == "cpu" else decision.compute_type
            self._active_device = decision.device
            self._logger.info(
                "Whisper backend resolved: device=%s compute_type=%s (%s)",
                decision.device, compute_type, decision.reason,
            )
            try:
                self._model = WhisperModel(self._model_size, device=decision.device, compute_type=compute_type)
            except Exception as exc:  # noqa: BLE001
                if decision.device == "cuda" and self._looks_like_gpu_runtime_error(exc):
                    self._logger.warning(
                        "Whisper GPU model load failed; CPU fallback will be attempted. "
                        "device=%s compute_type=%s error=%s",
                        decision.device, compute_type, exc, exc_info=True,
                    )
                    raise _GpuRuntimeFailure(str(exc)) from exc
                raise TranscriptionError(f"Failed to load Whisper model: {exc}") from exc
        return self._model

    def _force_cpu_and_reset(self) -> None:
        """Discard the current (broken-on-GPU) model and force CPU from now on."""
        self._runtime_manager.invalidate()
        self._forced_cpu = True
        self._backend_preference = WHISPER_BACKEND_CPU
        self._model = None
        self._active_device = None

    @staticmethod
    def _looks_like_gpu_runtime_error(exc: BaseException) -> bool:
        """Whether ``exc`` looks like a missing/broken CUDA library, not a
        generic transcription failure -- e.g. the classic
        CUDA runtime errors reported by ctranslate2."""
        text = str(exc).lower()
        return any(signature in text for signature in GPU_RUNTIME_ERROR_SIGNATURES)
