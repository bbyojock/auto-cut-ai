"""Version Next: HTTP API for Android/mobile clients.

This module keeps the existing desktop editing logic intact and exposes a
small asynchronous job API so mobile clients can upload videos, poll progress,
and download EditPlan-compatible outputs.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from ai.claude_provider import ClaudeProvider
from ai.edit_planner import EditPlanner
from ai.gemini_provider import GeminiProvider
from ai.key_manager import APIKeyManager
from ai.openai_compatible_provider import OpenAICompatibleProvider
from ai.provider_factory import ProviderFactory
from config.config_manager import ConfigManager
from core.cancellation import CancellationToken
from core.exceptions import AutoCutAIError, OperationCancelledError, ProviderConfigurationError
from models.generation_progress import ProgressEvent
from services.ai_request_log_service import AIRequestLogService
from services.edit_generation_service import EditGenerationResult, EditGenerationService
from services.edit_plan_cache_service import EditPlanCacheService
from services.export_service import ExportService
from services.logging_service import LoggingService
from services.resolve_export_service import ResolveExportService
from utils.constants import (
    DEFAULT_FRAME_INTERVAL_SECONDS,
    DEFAULT_PROMPT_STYLE,
    PROVIDER_REQUIRES_API_KEY,
    PROVIDER_TYPE_ANTHROPIC,
    PROVIDER_TYPE_GEMINI,
    PROVIDER_TYPE_OPENAI_COMPATIBLE,
)
from utils.file_utils import ensure_directory, get_project_root


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class JobStateResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    message: str
    created_at: float
    updated_at: float
    video_file_name: str
    output_json_path: Optional[str] = None
    output_xml_path: Optional[str] = None
    error: Optional[str] = None


@dataclass(slots=True)
class MobileJob:
    job_id: str
    video_path: Path
    original_filename: str
    target_length_seconds: Optional[float]
    style: str
    instructions_text: Optional[str]
    use_cache: bool
    whisper_backend: str
    whisper_model: str
    frame_interval_seconds: float
    game_profile: str
    status: str = "queued"
    stage: str = "queued"
    message: str = "Queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    output_json_path: Optional[Path] = None
    output_xml_path: Optional[Path] = None
    error: Optional[str] = None
    cancel_token: CancellationToken = field(default_factory=CancellationToken)
    result: Optional[EditGenerationResult] = None

    def to_response(self) -> JobStateResponse:
        return JobStateResponse(
            job_id=self.job_id,
            status=self.status,
            stage=self.stage,
            message=self.message,
            created_at=self.created_at,
            updated_at=self.updated_at,
            video_file_name=self.original_filename,
            output_json_path=str(self.output_json_path) if self.output_json_path else None,
            output_xml_path=str(self.output_xml_path) if self.output_xml_path else None,
            error=self.error,
        )


class MobileJobManager:
    """In-process queue/worker for mobile edit-generation jobs."""

    def __init__(self, uploads_dir: Path, exports_dir: Path) -> None:
        self._logger = LoggingService.get_logger("services.mobile_api")
        self._uploads_dir = ensure_directory(uploads_dir)
        self._exports_dir = ensure_directory(exports_dir)

        self._jobs: Dict[str, MobileJob] = {}
        self._lock = threading.Lock()
        self._queue: Queue[str] = Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="mobile-job-worker")

        self._edit_generation_service = self._build_edit_generation_service()
        self._resolve_export_service = ResolveExportService()
        self._worker.start()

    def create_job(
        self,
        uploaded_video_path: Path,
        original_filename: str,
        target_length_seconds: Optional[float],
        style: str,
        instructions_text: Optional[str],
        use_cache: bool,
    ) -> MobileJob:
        config = ConfigManager().load()
        job_id = uuid.uuid4().hex
        job = MobileJob(
            job_id=job_id,
            video_path=uploaded_video_path,
            original_filename=original_filename,
            target_length_seconds=target_length_seconds,
            style=style or config.prompt_style or DEFAULT_PROMPT_STYLE,
            instructions_text=instructions_text,
            use_cache=use_cache,
            whisper_backend=config.whisper.backend,
            whisper_model=config.whisper.model_size,
            frame_interval_seconds=config.frame_interval_seconds or DEFAULT_FRAME_INTERVAL_SECONDS,
            game_profile=config.game_profile,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._queue.put(job_id)
        return job

    def list_jobs(self) -> List[JobStateResponse]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.to_response() for job in sorted(jobs, key=lambda current: current.created_at, reverse=True)]

    def get_job(self, job_id: str) -> MobileJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def cancel_job(self, job_id: str) -> JobStateResponse:
        job = self.get_job(job_id)
        job.cancel_token.cancel()
        self._update_job(job, stage="cancelled", message="Cancellation requested.", status="cancelled")
        return job.to_response()

    def _update_job(
        self,
        job: MobileJob,
        *,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            if stage is not None:
                job.stage = stage
            if message is not None:
                job.message = message
            if status is not None:
                job.status = status
            if error is not None:
                job.error = error
            job.updated_at = time.time()

    def _build_edit_generation_service(self) -> EditGenerationService:
        config = ConfigManager().load()
        provider_factory = ProviderFactory()
        provider_factory.register_type(
            PROVIDER_TYPE_GEMINI,
            lambda settings: GeminiProvider(
                APIKeyManager(settings.api_keys),
                base_url=settings.base_url or None,
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )
        provider_factory.register_type(
            PROVIDER_TYPE_OPENAI_COMPATIBLE,
            lambda settings: OpenAICompatibleProvider(
                display_name=settings.display_name or settings.provider_id.title(),
                base_url=settings.base_url,
                key_manager=APIKeyManager(settings.api_keys),
                requires_api_key=PROVIDER_REQUIRES_API_KEY.get(settings.provider_id, True),
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )
        provider_factory.register_type(
            PROVIDER_TYPE_ANTHROPIC,
            lambda settings: ClaudeProvider(
                base_url=settings.base_url,
                key_manager=APIKeyManager(settings.api_keys),
                timeout_seconds=settings.timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )

        candidates = []
        preferred = config.get_provider(config.last_used.provider)
        if preferred is not None and preferred.enabled:
            candidates.append(preferred)
        candidates.extend(provider for provider in config.get_enabled_providers() if provider not in candidates)

        last_error: Exception = ProviderConfigurationError(
            "No AI provider is enabled. Configure one in Settings before starting the mobile API."
        )
        for settings in candidates:
            try:
                provider = provider_factory.create_from_settings(settings)
                planner = EditPlanner(provider=provider, model=settings.model)
                return EditGenerationService(
                    edit_planner=planner,
                    cache_service=EditPlanCacheService(),
                    ai_request_log_service=AIRequestLogService(),
                )
            except AutoCutAIError as exc:
                last_error = exc
                continue

        raise ProviderConfigurationError(str(last_error))

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                job = self.get_job(job_id)
            except KeyError:
                continue
            if job.cancel_token.is_cancelled():
                self._update_job(job, status="cancelled", stage="cancelled", message="Cancelled before start.")
                continue

            self._update_job(job, status="running", stage="starting", message="Starting analysis.")
            try:
                def on_progress(event: ProgressEvent) -> None:
                    self._update_job(
                        job,
                        stage=event.stage.value,
                        message=event.message or event.stage_label,
                        status="running",
                    )

                result = self._edit_generation_service.generate(
                    video_path=job.video_path,
                    on_progress=on_progress,
                    cancel_token=job.cancel_token,
                    whisper_backend=job.whisper_backend,
                    whisper_model=job.whisper_model,
                    frame_interval_seconds=job.frame_interval_seconds,
                    style=job.style,
                    target_length_seconds=job.target_length_seconds,
                    use_cache=job.use_cache,
                    instructions_text=job.instructions_text,
                    game_profile=job.game_profile,
                )
                output_json = self._exports_dir / f"{job.job_id}.editplan.json"
                output_json.write_text(result.plan.to_json(indent=2), encoding="utf-8")

                output_xml = self._exports_dir / f"{job.job_id}.resolve.xml"
                result_video = result.analysis.video
                self._resolve_export_service.export_xml(
                    plan=result.plan,
                    source_video_path=job.video_path,
                    fps=result_video.fps,
                    output_path=output_xml,
                    media_duration_seconds=result_video.duration_seconds,
                    width=result_video.width,
                    height=result_video.height,
                    source_start_timecode_seconds=result_video.source_timecode_seconds,
                )

                with self._lock:
                    job.result = result
                    job.output_json_path = output_json
                    job.output_xml_path = output_xml
                self._update_job(job, status="completed", stage="completed", message="Completed.")
            except OperationCancelledError:
                self._update_job(job, status="cancelled", stage="cancelled", message="Cancelled.")
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("Mobile job %s failed", job.job_id)
                self._update_job(
                    job,
                    status="failed",
                    stage="error",
                    message="Failed.",
                    error=str(exc),
                )


def create_app(base_dir: Optional[Path] = None) -> FastAPI:
    root = base_dir or (get_project_root() / "mobile")
    uploads_dir = ensure_directory(root / "uploads")
    exports_dir = ensure_directory(root / "exports")
    manager = MobileJobManager(uploads_dir=uploads_dir, exports_dir=exports_dir)
    app = FastAPI(title="AutoCutAI Mobile API", version="vNext")

    @app.get("/api/vnext/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/vnext/jobs")
    def list_jobs() -> List[JobStateResponse]:
        return manager.list_jobs()

    @app.post("/api/vnext/jobs", response_model=JobCreateResponse)
    async def create_job(
        video: UploadFile = File(...),
        target_length_seconds: Optional[float] = Form(default=None),
        style: str = Form(default=DEFAULT_PROMPT_STYLE),
        instructions_text: Optional[str] = Form(default=None),
        use_cache: bool = Form(default=True),
    ) -> JobCreateResponse:
        suffix = Path(video.filename or "").suffix or ".mp4"
        upload_path = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
        content = await video.read()
        upload_path.write_bytes(content)
        job = manager.create_job(
            uploaded_video_path=upload_path,
            original_filename=video.filename or upload_path.name,
            target_length_seconds=target_length_seconds,
            style=style,
            instructions_text=instructions_text,
            use_cache=use_cache,
        )
        return JobCreateResponse(job_id=job.job_id, status=job.status)

    @app.get("/api/vnext/jobs/{job_id}", response_model=JobStateResponse)
    def get_job(job_id: str) -> JobStateResponse:
        try:
            return manager.get_job(job_id).to_response()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc

    @app.post("/api/vnext/jobs/{job_id}/cancel", response_model=JobStateResponse)
    def cancel_job(job_id: str) -> JobStateResponse:
        try:
            return manager.cancel_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc

    @app.get("/api/vnext/jobs/{job_id}/plan")
    def get_plan(job_id: str) -> JSONResponse:
        try:
            job = manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        if job.result is None:
            raise HTTPException(status_code=409, detail="Job is not completed yet.")
        return JSONResponse(content=job.result.plan.to_dict())

    @app.get("/api/vnext/jobs/{job_id}/report")
    def get_report(job_id: str, fmt: str = "json") -> PlainTextResponse:
        try:
            job = manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        if job.result is None:
            raise HTTPException(status_code=409, detail="Job is not completed yet.")

        plan = job.result.plan
        if fmt == "json":
            return PlainTextResponse(ExportService.to_json(plan), media_type="application/json")
        if fmt == "txt":
            return PlainTextResponse(ExportService.to_txt(plan, source_video_name=job.original_filename))
        if fmt == "md":
            return PlainTextResponse(ExportService.to_markdown(plan, source_video_name=job.original_filename))
        raise HTTPException(status_code=400, detail="fmt must be one of: json, txt, md")

    @app.get("/api/vnext/jobs/{job_id}/resolve-xml")
    def get_resolve_xml(job_id: str) -> FileResponse:
        try:
            job = manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        if job.output_xml_path is None or not job.output_xml_path.exists():
            raise HTTPException(status_code=409, detail="Resolve XML is not available yet.")
        return FileResponse(
            path=job.output_xml_path,
            media_type="application/xml",
            filename=f"{job.job_id}.resolve.xml",
        )

    @app.get("/api/vnext/jobs/{job_id}/sync-bundle")
    def get_sync_bundle(job_id: str) -> JSONResponse:
        try:
            job = manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found.") from exc
        if job.result is None:
            raise HTTPException(status_code=409, detail="Job is not completed yet.")
        bundle = {
            "job_id": job.job_id,
            "video_file_name": job.original_filename,
            "created_at": job.created_at,
            "edit_plan": job.result.plan.to_dict(),
            "analysis": {
                "duration_seconds": job.result.analysis.video.duration_seconds,
                "fps": job.result.analysis.video.fps,
                "width": job.result.analysis.video.width,
                "height": job.result.analysis.video.height,
            },
            "paths": {
                "edit_plan_json": str(job.output_json_path) if job.output_json_path else None,
                "resolve_xml": str(job.output_xml_path) if job.output_xml_path else None,
            },
        }
        return JSONResponse(content=json.loads(json.dumps(bundle, ensure_ascii=False)))

    return app
