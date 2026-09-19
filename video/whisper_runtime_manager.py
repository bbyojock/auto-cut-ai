"""Selects the Whisper backend using ctranslate2 as the CUDA source of truth."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional, Tuple

from models.gpu_runtime_status import GpuRuntimeStatus, WhisperRuntimeDecision
from services.logging_service import LoggingService
from utils.constants import (
    DEFAULT_WHISPER_COMPUTE_TYPE,
    DEFAULT_WHISPER_GPU_COMPUTE_TYPE,
    WHISPER_BACKEND_AUTO,
    WHISPER_BACKEND_CPU,
    WHISPER_BACKEND_GPU,
)


class WhisperRuntimeManager:
    """Decides GPU vs. CPU using ctranslate2's own capability report.

    Detection is cached after the first call (GPU/CUDA availability doesn't
    change mid-run) but can be forced to re-check, e.g. after a runtime
    failure invalidates a previously "verified" GPU.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("video.whisper_runtime")
        self._cached_status: Optional[GpuRuntimeStatus] = None

    def detect_gpu_status(self, force: bool = False) -> GpuRuntimeStatus:
        """Ask ctranslate2 whether its CUDA backend is usable."""
        if self._cached_status is not None and not force:
            return self._cached_status

        status = GpuRuntimeStatus()
        try:
            import ctranslate2

            status.cuda_device_count = ctranslate2.get_cuda_device_count()
            compute_types = ctranslate2.get_supported_compute_types("cuda")
        except Exception as exc:  # noqa: BLE001 - detection must never raise
            status.error = f"ctranslate2 CUDA capability check failed: {exc}"
            self._logger.warning(
                "Whisper GPU unavailable: ctranslate2 could not verify CUDA support: %s",
                exc,
                exc_info=True,
            )
            self._cached_status = status
            return status

        if status.cuda_device_count == 0 or not compute_types:
            status.error = (
                "ctranslate2 reports no CUDA device."
                if status.cuda_device_count == 0
                else "ctranslate2 reports no supported CUDA compute types."
            )
            self._logger.info("Whisper GPU unavailable: %s", status.error)
            self._cached_status = status
            return status

        # ctranslate2 can only report these capabilities after initializing
        # its CUDA backend, so this is the authoritative capability result.
        status.cublas_available = True
        status.cudnn_available = True
        status.vram_total_mb, status.compute_capability = self._query_nvidia_smi()

        self._logger.info(
            "CUDA detection via ctranslate2: %d device(s), supported_compute_types=%s, "
            "cuBLAS=True, cuDNN=True, VRAM=%s MB, compute capability=%s",
            status.cuda_device_count,
            sorted(compute_types),
            status.vram_total_mb,
            status.compute_capability,
        )
        self._cached_status = status
        return status

    def invalidate(self) -> None:
        """Force the next :meth:`detect_gpu_status` call to re-probe from scratch.

        Called after a GPU actually fails at runtime, so a "verified"
        result from startup doesn't keep being trusted forever.
        """
        self._cached_status = None

    def resolve_device_and_compute_type(self, backend_preference: str) -> WhisperRuntimeDecision:
        """Decide the actual device/compute type for a given backend preference.

        Args:
            backend_preference: One of ``utils.constants.AVAILABLE_WHISPER_BACKENDS``
                (``"auto"``, ``"gpu"``, or ``"cpu"``).
        """
        if backend_preference == WHISPER_BACKEND_CPU:
            return WhisperRuntimeDecision(
                "cpu", DEFAULT_WHISPER_COMPUTE_TYPE, "CPU backend selected in Settings.", False
            )

        status = self.detect_gpu_status()

        if backend_preference == WHISPER_BACKEND_GPU:
            if status.fully_supported:
                return WhisperRuntimeDecision(
                    "cuda", DEFAULT_WHISPER_GPU_COMPUTE_TYPE,
                    "GPU backend selected in Settings; ctranslate2 CUDA support verified.", False,
                )
            reason = self._explain_gpu_unavailable(status)
            self._logger.warning("GPU backend requested but unavailable (%s). Falling back to CPU.", reason)
            return WhisperRuntimeDecision(
                "cpu", DEFAULT_WHISPER_COMPUTE_TYPE, f"{reason} Falling back to CPU.", True
            )

        # AUTO: prefer GPU when ctranslate2 reports CUDA compute support.
        if status.fully_supported:
            return WhisperRuntimeDecision(
                "cuda", DEFAULT_WHISPER_GPU_COMPUTE_TYPE, "Auto-detected a fully supported GPU.", False
            )
        reason = self._explain_gpu_unavailable(status)
        self._logger.info("Whisper backend 'auto': %s Using CPU.", reason)
        return WhisperRuntimeDecision("cpu", DEFAULT_WHISPER_COMPUTE_TYPE, reason, status.gpu_present)

    @staticmethod
    def _query_nvidia_smi() -> Tuple[Optional[float], Optional[str]]:
        """Best-effort VRAM/compute-capability lookup via ``nvidia-smi``.

        Purely informational (shown on the Diagnostics page) -- absence of
        ``nvidia-smi`` never affects the GPU/CPU decision itself, which is
        based only on ctranslate2's CUDA capability check.
        """
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return None, None
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=memory.total,compute_cap", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                first_line = result.stdout.strip().splitlines()[0]
                parts = [part.strip() for part in first_line.split(",")]
                if len(parts) == 2:
                    return float(parts[0]), parts[1]
        except Exception:  # noqa: BLE001 - diagnostics only, never fatal
            pass
        return None, None

    @staticmethod
    def _explain_gpu_unavailable(status: GpuRuntimeStatus) -> str:
        if status.error:
            return f"GPU check failed ({status.error})."
        if not status.gpu_present:
            return "No NVIDIA GPU detected."
        if not status.cublas_available:
            return "ctranslate2 did not report CUDA support (cuBLAS capability unavailable)."
        if not status.cudnn_available:
            return "ctranslate2 did not report the required CUDA compute support."
        return "GPU is not fully supported."
