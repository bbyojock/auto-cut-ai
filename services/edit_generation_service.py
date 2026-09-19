"""Orchestrates the full Version 4 generation flow behind one call.

    cache lookup -> [Video -> Audio -> Whisper -> Frames] -> AI edit plan

The UI layer (``ui.pages.edit_plan_page.EditPlanPage``) only ever calls
:meth:`EditGenerationService.generate` from a background thread and
forwards the :class:`models.generation_progress.ProgressEvent` stream to
its progress dialog -- every other Version 4 concern (cache hit/miss,
which pipeline stage is running, when the AI request starts, when to
persist results to the cache and the AI request log) lives here, keeping
the page itself a thin, dumb view (Feature 18: a responsive UI is a lot
easier to keep correct when it does no business logic of its own).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ai.edit_planner import EditPlanner
from ai.editing_rules import EditingRules
from ai.prompt_builder import PromptBuildResult
from core.cancellation import CancellationToken
from models.analysis_result import AnalysisResult
from models.benchmark import BenchmarkReport
from models.edit_plan import EditPlan
from models.generation_progress import GenerationStage, ProgressEvent
from services.ai_request_log_service import AIRequestLogService
from services.edit_plan_cache_service import EditPlanCacheService
from services.logging_service import LoggingService
from services.token_usage_service import TokenUsageService
from utils.constants import DEFAULT_FRAME_INTERVAL_SECONDS, DEFAULT_PROMPT_STYLE
from video.analysis_pipeline import AnalysisPipeline
from video.transcriber import WhisperTranscriber


@dataclass(slots=True)
class EditGenerationResult:
    """Everything one end-to-end generation run produced."""

    analysis: AnalysisResult
    plan: EditPlan
    benchmark: BenchmarkReport
    used_cache: bool
    analysis_from_cache: bool = False
    prompt_chars: int = 0
    prompt_estimated_tokens: int = 0
    prompt_reduction_percent: float = 0.0
    prompt_debug: Optional[PromptBuildResult] = None
    """The full prompt actually sent (Feature 15). ``None`` is never
    returned in practice -- even a cache hit reconstructs one (with 0%
    reduction, since there's no fresh baseline to compare against) from
    the prompt text that was cached alongside the plan, so the Prompt
    Debug window always has something to show."""


class EditGenerationService:
    """Ties :class:`video.analysis_pipeline.AnalysisPipeline`,
    :class:`services.edit_plan_cache_service.EditPlanCacheService`, and
    :class:`ai.edit_planner.EditPlanner` together for one video.
    """

    def __init__(
        self,
        edit_planner: EditPlanner,
        cache_service: Optional[EditPlanCacheService] = None,
        ai_request_log_service: Optional[AIRequestLogService] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._edit_planner = edit_planner
        self._cache_service = cache_service or EditPlanCacheService()
        self._ai_request_log_service = ai_request_log_service
        self._logger = logger or LoggingService.get_logger("services.edit_generation")

    def generate(
        self,
        video_path: Path,
        on_progress: Callable[[ProgressEvent], None],
        cancel_token: CancellationToken,
        whisper_backend: str,
        whisper_model: str,
        frame_interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS,
        style: str = DEFAULT_PROMPT_STYLE,
        target_length_seconds: Optional[float] = None,
        use_cache: bool = True,
        rules: Optional[EditingRules] = None,
        instructions_text: Optional[str] = None,
        instructions_fingerprint: str = "",
        game_profile: str = "",
    ) -> EditGenerationResult:
        """Run cache lookup -> pipeline -> AI edit planning for ``video_path``.

        Version 4.5 (Feature 12 fix): caching is now two independent
        layers -- ``analysis`` (Whisper transcript + frames, unaffected by
        editing rules/instructions/style) and ``plan`` (the AI's decision,
        which depends on all of those). Changing only ``rules``/
        ``instructions_text``/``style``/``target_length_seconds`` between
        calls for the same video now only re-runs the AI request, never
        Whisper or frame extraction.

        Raises:
            core.exceptions.OperationCancelledError: if cancelled at any
                stage boundary (pipeline) or mid-stream (AI generation).
            core.exceptions.AutoCutAIError: (or a subclass) on any
                unrecoverable pipeline/provider failure.
        """
        run_started = time.monotonic()
        benchmark = BenchmarkReport()

        active_rules = rules or self._edit_planner.rules
        analysis_key = self._cache_service.analysis_cache_key(
            video_path, whisper_backend, whisper_model, frame_interval_seconds
        )
        plan_key = self._cache_service.plan_cache_key(
            analysis_key, style, target_length_seconds,
            rules_fingerprint=active_rules.fingerprint(),
            instructions_fingerprint=instructions_fingerprint,
            game_profile=game_profile,
        )

        if use_cache:
            cached = self._cache_service.get_plan(plan_key)
            if cached is not None:
                benchmark.add("cache_lookup", time.monotonic() - run_started)
                on_progress(
                    ProgressEvent(
                        stage=GenerationStage.COMPLETED,
                        message="Loaded from cache -- Whisper and the AI request were both skipped.",
                        elapsed_seconds=time.monotonic() - run_started,
                    )
                )
                self._logger.info("EditGenerationService plan-cache HIT for %s (key=%s)", video_path, plan_key)
                reduction = TokenUsageService.measure_reduction(cached.prompt_text, cached.prompt_text)
                cached_prompt_debug = PromptBuildResult(
                    full_prompt=cached.prompt_text,
                    system_prompt="(Not split into system/user parts for a cached prompt -- see Full Prompt tab.)",
                    user_prompt=cached.prompt_text,
                    reduction=reduction,
                )
                return EditGenerationResult(
                    analysis=cached.analysis,
                    plan=cached.plan,
                    benchmark=benchmark,
                    used_cache=True,
                    analysis_from_cache=True,
                    prompt_chars=len(cached.prompt_text),
                    prompt_estimated_tokens=TokenUsageService.estimate_tokens(cached.prompt_text),
                    prompt_reduction_percent=0.0,
                    prompt_debug=cached_prompt_debug,
                )

        cancel_token.raise_if_cancelled()

        # Feature 12 fix: check the analysis cache independently -- a plan
        # cache miss (e.g. instructions changed) no longer implies Whisper
        # and frame extraction need to run again.
        pipeline_started = time.monotonic()
        analysis = self._cache_service.get_analysis(analysis_key) if use_cache else None
        analysis_from_cache = analysis is not None

        if analysis is None:
            pipeline = AnalysisPipeline(
                transcriber=WhisperTranscriber(model_size=whisper_model, backend=whisper_backend)
            )

            def pipeline_progress(stage: GenerationStage) -> None:
                on_progress(ProgressEvent(stage=stage, elapsed_seconds=time.monotonic() - run_started))

            analysis = pipeline.run(
                video_path,
                frame_interval_seconds=frame_interval_seconds,
                on_progress=pipeline_progress,
                cancel_token=cancel_token,
            )
            if use_cache:
                self._cache_service.save_analysis(analysis_key, analysis)
        else:
            self._logger.info("Analysis cache HIT for %s -- Whisper/frame extraction skipped.", video_path)
            on_progress(
                ProgressEvent(
                    stage=GenerationStage.GENERATING_EDIT_PLAN,
                    message="Reusing cached transcript/frames -- Whisper was skipped.",
                    elapsed_seconds=time.monotonic() - run_started,
                )
            )
        benchmark.add("analysis_pipeline", time.monotonic() - pipeline_started)

        cancel_token.raise_if_cancelled()

        if rules is not None:
            self._edit_planner.set_rules(rules)

        ai_started = time.monotonic()
        plan, prompt_result, ai_log_entry = self._edit_planner.create_edit_plan_streaming(
            analysis,
            on_progress=on_progress,
            cancel_token=cancel_token,
            target_length_seconds=target_length_seconds,
            style=style,
            instructions_text=instructions_text,
        )
        benchmark.add("ai_edit_planning", time.monotonic() - ai_started)

        if self._ai_request_log_service is not None:
            self._ai_request_log_service.record(ai_log_entry)

        if use_cache:
            self._cache_service.save_plan(plan_key, analysis, plan, prompt_result.full_prompt)

        self._logger.info(
            "EditGenerationService completed for %s in %.2fs (plan cache=miss, analysis cache=%s)",
            video_path, time.monotonic() - run_started, "hit" if analysis_from_cache else "miss",
        )
        return EditGenerationResult(
            analysis=analysis,
            plan=plan,
            benchmark=benchmark,
            used_cache=False,
            analysis_from_cache=analysis_from_cache,
            prompt_chars=len(prompt_result.full_prompt),
            prompt_estimated_tokens=TokenUsageService.estimate_tokens(prompt_result.full_prompt),
            prompt_reduction_percent=prompt_result.reduction.reduction_percent,
            prompt_debug=prompt_result,
        )
