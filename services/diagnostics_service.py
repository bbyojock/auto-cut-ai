"""Generates the Runtime Diagnostics report.

Every check here is defensive: a failure while probing one thing (e.g. a
broken FFmpeg install) never prevents the rest of the report from being
generated, and nothing ever raises a raw traceback out to the UI --
that's the whole point of a diagnostics page.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import List, Optional

from config.settings import AppConfig, suggest_model_migration
from models.connection_test_result import ConnectionTestResult
from models.runtime_diagnostics import DiagnosticCheck, DiagnosticStatus, RuntimeDiagnosticsReport
from services.logging_service import LoggingService
from video.whisper_runtime_manager import WhisperRuntimeManager


class RuntimeDiagnosticsService:
    """Runs every startup/runtime health check and assembles the report."""

    def __init__(self, runtime_manager: Optional[WhisperRuntimeManager] = None, logger: Optional[logging.Logger] = None) -> None:
        self._runtime_manager = runtime_manager or WhisperRuntimeManager()
        self._logger = logger or LoggingService.get_logger("services.diagnostics")

    def run(self, config: AppConfig, connection_result: Optional[ConnectionTestResult] = None) -> RuntimeDiagnosticsReport:
        """Build the full report.

        Args:
            config: The current application configuration.
            connection_result: Version 4, Feature 11 -- a freshly-run
                :meth:`ai.base_provider.AIProvider.test_connection` result
                for the active provider, if the user has clicked "Test
                Provider Connection" on the Diagnostics page. ``None``
                (the default) renders that row as "Not tested yet" rather
                than performing a live network call itself -- diagnostics
                must stay instant and side-effect-free unless the user
                explicitly asks for a live connectivity check.
        """
        self._logger.info("Running runtime diagnostics...")
        checks: List[DiagnosticCheck] = [
            self._check_python(),
            self._check_ffmpeg(),
            self._check_whisper_package(),
            *self._check_gpu(config),
            self._check_whisper_model(config),
            self._check_providers(config),
            self._check_connection_status(config, connection_result),
            self._check_configuration(config),
        ]
        report = RuntimeDiagnosticsReport(checks=checks)
        self._logger.info("Diagnostics complete: overall=%s", report.overall_status.value)
        return report

    def _check_python(self) -> DiagnosticCheck:
        version = sys.version.split()[0]
        ok = sys.version_info >= (3, 11)
        return DiagnosticCheck(
            "Python",
            DiagnosticStatus.OK if ok else DiagnosticStatus.WARNING,
            f"Python {version}",
            "" if ok else "AutoCutAI targets Python 3.11+; some features may not work correctly.",
        )

    def _check_ffmpeg(self) -> DiagnosticCheck:
        path = shutil.which("ffmpeg")
        if not path:
            return DiagnosticCheck(
                "FFmpeg", DiagnosticStatus.ERROR, "Not found on PATH.",
                "Audio extraction (Version 2 analysis) will fail. Install ffmpeg and restart AutoCutAI.",
            )
        try:
            result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=5, check=False)
            first_line = result.stdout.splitlines()[0] if result.stdout else "ffmpeg"
            return DiagnosticCheck("FFmpeg", DiagnosticStatus.OK, first_line, path)
        except Exception as exc:  # noqa: BLE001
            return DiagnosticCheck("FFmpeg", DiagnosticStatus.WARNING, "Found, but could not run it.", str(exc))

    def _check_whisper_package(self) -> DiagnosticCheck:
        try:
            import ctranslate2
            import faster_whisper  # noqa: F401

            return DiagnosticCheck(
                "Whisper", DiagnosticStatus.OK,
                f"faster-whisper installed (CTranslate2 {ctranslate2.__version__}).",
            )
        except Exception as exc:  # noqa: BLE001
            return DiagnosticCheck(
                "Whisper", DiagnosticStatus.ERROR, "faster-whisper is not installed correctly.", str(exc)
            )

    def _check_gpu(self, config: AppConfig) -> List[DiagnosticCheck]:
        status = self._runtime_manager.detect_gpu_status(force=True)
        decision = self._runtime_manager.resolve_device_and_compute_type(config.whisper.backend)

        if not status.gpu_present:
            gpu_check = DiagnosticCheck(
                "GPU", DiagnosticStatus.WARNING, "No NVIDIA GPU detected.",
                "AutoCutAI will use CPU for transcription -- fully supported, just slower.",
            )
        elif status.fully_supported:
            gpu_check = DiagnosticCheck(
                "GPU", DiagnosticStatus.OK,
                f"{status.cuda_device_count} CUDA device(s) detected, compute capability "
                f"{status.compute_capability or 'unknown'}.",
            )
        else:
            gpu_check = DiagnosticCheck(
                "GPU", DiagnosticStatus.WARNING, "An NVIDIA GPU was detected, but is not fully usable.",
                status.error or "ctranslate2 did not report usable CUDA support.",
            )

        def _lib_check(name: str, available: bool) -> DiagnosticCheck:
            if not status.gpu_present:
                return DiagnosticCheck(name, DiagnosticStatus.OK, "Not applicable (no GPU detected).")
            if available:
                return DiagnosticCheck(name, DiagnosticStatus.OK, "Available.")
            return DiagnosticCheck(
                name, DiagnosticStatus.ERROR, "Not reported by ctranslate2.",
                "The ctranslate2 CUDA capability check did not confirm this backend requirement.",
            )

        cublas_check = _lib_check("cuBLAS", status.cublas_available)
        cudnn_check = _lib_check("cuDNN", status.cudnn_available)
        vram_check = DiagnosticCheck(
            "VRAM",
            DiagnosticStatus.OK if (status.vram_total_mb or not status.gpu_present) else DiagnosticStatus.WARNING,
            f"{status.vram_total_mb:.0f} MB" if status.vram_total_mb else "Unknown / not applicable.",
        )
        backend_check = DiagnosticCheck(
            "Whisper Backend", DiagnosticStatus.OK,
            f"{decision.device.upper()} / {decision.compute_type} (setting: {config.whisper.backend})",
            decision.reason,
        )
        return [gpu_check, cublas_check, cudnn_check, vram_check, backend_check]

    def _check_whisper_model(self, config: AppConfig) -> DiagnosticCheck:
        model = config.whisper.model_size or "(not set)"
        return DiagnosticCheck(
            "Whisper Model", DiagnosticStatus.OK if config.whisper.model_size else DiagnosticStatus.WARNING,
            model, "Configured in Settings -> Whisper. Any faster-whisper model name is accepted.",
        )

    def _check_connection_status(
        self, config: AppConfig, connection_result: Optional[ConnectionTestResult]
    ) -> DiagnosticCheck:
        active = config.get_provider(config.last_used.provider)
        provider_label = active.display_name if active else "the active provider"

        if connection_result is None:
            return DiagnosticCheck(
                "Connection Status", DiagnosticStatus.WARNING, "Not tested yet.",
                f"Click 'Test Provider Connection' to send one live request to {provider_label} and measure latency.",
            )
        if connection_result.success:
            return DiagnosticCheck(
                "Connection Status", DiagnosticStatus.OK, connection_result.message,
                f"Latency: {connection_result.latency_seconds:.2f}s",
            )
        return DiagnosticCheck(
            "Connection Status", DiagnosticStatus.ERROR, "Connection test failed.", connection_result.message,
        )

    def _check_providers(self, config: AppConfig) -> DiagnosticCheck:
        enabled = config.get_enabled_providers()
        if not enabled:
            return DiagnosticCheck(
                "Providers", DiagnosticStatus.ERROR, "No AI provider is enabled.",
                "Open Settings and enable + configure at least one provider.",
            )

        active = config.get_provider(config.last_used.provider)
        if active is None or not active.enabled:
            return DiagnosticCheck(
                "Providers", DiagnosticStatus.WARNING, "The active provider is not enabled.",
                "Choose an enabled provider as 'Active' in Settings.",
            )
        if not active.model:
            return DiagnosticCheck(
                "Providers", DiagnosticStatus.WARNING, f"'{active.display_name}' has no model configured.",
                "Enter a model name in Settings.",
            )
        if not active.api_keys and active.provider_id != "local_ai":
            return DiagnosticCheck(
                "Providers", DiagnosticStatus.WARNING, f"'{active.display_name}' has no API keys configured.",
                "Add at least one key in Settings, or use 'Test Connection' after adding one.",
            )
        return DiagnosticCheck(
            "Providers", DiagnosticStatus.OK,
            f"Active: {active.display_name} (model: {active.model}).",
            f"{len(enabled)} provider(s) enabled in total.",
        )

    def _check_configuration(self, config: AppConfig) -> DiagnosticCheck:
        outdated = [p for p in config.providers if suggest_model_migration(p.provider_type, p.model)]
        if outdated:
            names = ", ".join(f"{p.display_name} ('{p.model}')" for p in outdated)
            return DiagnosticCheck(
                "Configuration", DiagnosticStatus.WARNING, f"Outdated model(s) detected: {names}.",
                "Open Settings to apply the suggested migration.",
            )
        return DiagnosticCheck("Configuration", DiagnosticStatus.OK, "Configuration looks up to date.")
