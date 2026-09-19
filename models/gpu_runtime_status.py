"""Describes whether GPU-accelerated Whisper transcription is safe to use."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(slots=True)
class GpuRuntimeStatus:
    """Result of ctranslate2's CUDA capability check.

    The cuBLAS/cuDNN fields are retained for diagnostics compatibility, but
    their values come from ctranslate2 rather than DLL probing.
    """

    cuda_device_count: int = 0
    cublas_available: bool = False
    cudnn_available: bool = False
    checked_libraries: List[str] = field(default_factory=list)
    missing_libraries: List[str] = field(default_factory=list)
    vram_total_mb: Optional[float] = None
    compute_capability: Optional[str] = None
    error: Optional[str] = None

    @property
    def gpu_present(self) -> bool:
        return self.cuda_device_count > 0

    @property
    def fully_supported(self) -> bool:
        """Whether ctranslate2 reports a usable CUDA backend."""
        return self.gpu_present and self.cublas_available and self.cudnn_available

    def to_dict(self) -> dict:
        return {
            "cuda_device_count": self.cuda_device_count,
            "gpu_present": self.gpu_present,
            "cublas_available": self.cublas_available,
            "cudnn_available": self.cudnn_available,
            "fully_supported": self.fully_supported,
            "checked_libraries": list(self.checked_libraries),
            "missing_libraries": list(self.missing_libraries),
            "vram_total_mb": self.vram_total_mb,
            "compute_capability": self.compute_capability,
            "error": self.error,
        }


@dataclass(slots=True)
class WhisperRuntimeDecision:
    """What device/compute type Whisper should actually run with, and why."""

    device: str  # "cuda" or "cpu"
    compute_type: str
    reason: str
    used_fallback: bool = False

    def to_dict(self) -> dict:
        return {
            "device": self.device,
            "compute_type": self.compute_type,
            "reason": self.reason,
            "used_fallback": self.used_fallback,
        }
