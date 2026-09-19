"""Orchestrates the Version 3 AI editing brain.

    AnalysisResult -> ContextBuilder -> PromptBuilder -> AIProvider
                    -> ResponseParser -> EditPlanValidator -> EditPlan

This module decides HOW a video should be edited. It never edits the
video, never renders anything, and never talks to DaVinci Resolve --
:class:`EditPlanner` only ever returns a :class:`models.edit_plan.EditPlan`
for a later version to act on.

Version 3.5 adds three more (fully optional, backward-compatible) stages
after the response is parsed: an :class:`ai.edit_plan_validator.EditPlanValidator`
pass that repairs structural problems before the plan is ever returned, an
:class:`ai.edit_simulator.EditSimulator` run purely for logging/diagnostics,
and an :class:`ai.editing_scorer.EditingScorer` pass, also for logging.
Neither of the last two changes what :meth:`EditPlanner.create_edit_plan`
returns -- still a plain :class:`models.edit_plan.EditPlan` -- so every
Version 3 caller keeps working unchanged.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional, Tuple

from ai.base_provider import AIProvider
from ai.context_builder import ContextBuilder
from ai.edit_plan_validator import EditPlanValidator
from ai.edit_simulator import EditSimulator
from ai.editing_rules import EditingRules
from ai.editing_scorer import EditingScorer
from ai.plan_refiner import PlanRefiner
from ai.prompt_builder import PromptBuilder, PromptBuildResult
from ai.response_parser import ResponseParser
from core.cancellation import CancellationToken
from core.exceptions import AutoCutAIError, EditPlanValidationError, OperationCancelledError, ResponseParsingError
from models.ai_request_log import AIRequestLogEntry
from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan
from models.generation_progress import GenerationStage, ProgressEvent
from models.message import Message, MessageRole
from services.logging_service import LoggingService
from services.streaming_json_parser import StreamingEditPlanParser
from services.token_usage_service import TokenUsageService
from utils.constants import DEFAULT_PROMPT_STYLE


class EditPlanner:
    """Turns an :class:`AnalysisResult` into a professional :class:`EditPlan`.

    Depends only on the abstract :class:`ai.base_provider.AIProvider`
    interface -- exactly like :class:`services.chat_service.ChatService` in
    Version 1 -- never on a concrete provider. Gemini is simply the first
    implementation wired up in ``app.py``; swapping in a different provider
    or model never requires changing this class or any of its
    collaborators (:class:`ContextBuilder`, :class:`PromptBuilder`,
    :class:`ResponseParser`, :class:`ai.edit_plan_validator.EditPlanValidator`).
    """

    def __init__(
        self,
        provider: AIProvider,
        model: str,
        context_builder: Optional[ContextBuilder] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        response_parser: Optional[ResponseParser] = None,
        logger: Optional[logging.Logger] = None,
        validator: Optional[EditPlanValidator] = None,
        simulator: Optional[EditSimulator] = None,
        scorer: Optional[EditingScorer] = None,
        rules: Optional[EditingRules] = None,
        refiner: Optional[PlanRefiner] = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._context_builder = context_builder or ContextBuilder()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._response_parser = response_parser or ResponseParser()
        self._logger = logger or LoggingService.get_logger("ai.edit_planner")

        # Version 3.5 collaborators. All optional and all default to the
        # standard behavior, so existing (pre-3.5) call sites are unaffected.
        self._rules = rules or EditingRules.default()
        self._validator = validator or EditPlanValidator()
        self._simulator = simulator or EditSimulator()
        self._scorer = scorer or EditingScorer(self._rules)
        # Version 4.6, Feature 5/7/9: deterministic anti-monolith-cut pass,
        # run immediately after validation (see ai.plan_refiner's docstring
        # for why this is a code-level guarantee, not just a prompt hope).
        self._refiner = refiner or PlanRefiner()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def provider(self) -> AIProvider:
        """The underlying :class:`ai.base_provider.AIProvider` instance.

        Exposed (Version 4) so the Diagnostics page can call
        :meth:`ai.base_provider.AIProvider.test_connection` directly for
        its "Connection Status"/"Latency" checks (Feature 11) without
        EditPlanner needing to grow diagnostics-specific logic of its own.
        """
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def rules(self) -> EditingRules:
        """The :class:`EditingRules` currently used to build prompts."""
        return self._rules

    def set_rules(self, rules: EditingRules) -> None:
        """Swap the active editing rules (also re-scopes the scorer to match)."""
        self._rules = rules
        self._scorer = EditingScorer(rules)
        self._logger.info("Editing rules changed to '%s'.", rules.name)

    def set_model(self, model: str) -> None:
        """Change the model used for future edit plans (e.g. after a Settings change).

        Mirrors :meth:`services.chat_service.ChatService.set_model` so both
        services can be kept in sync with the single configured model in
        ``config.gemini.selected_model`` -- there is deliberately no
        separate "edit planning model" setting.
        """
        self._logger.info("Edit-planning model changed to %s", model)
        self._model = model

    def set_provider(self, provider: AIProvider, model: Optional[str] = None) -> None:
        """Swap the underlying provider/model, e.g. after a Settings change."""
        self._provider = provider
        if model is not None:
            self._model = model
        self._logger.info("Edit-planning provider changed to %s (%s)", provider.name, self._model)

    def create_edit_plan(
        self,
        analysis: AnalysisResult,
        target_length_seconds: Optional[float] = None,
        style: str = DEFAULT_PROMPT_STYLE,
        instructions_text: Optional[str] = None,
    ) -> EditPlan:
        """Generate a professional, validated :class:`EditPlan` for ``analysis``.

        Args:
            analysis: The completed Version 2 :class:`AnalysisResult` to
                base editing decisions on.
            target_length_seconds: Optional desired final video length. If
                omitted, the AI chooses its own length.
            style: Which content-style prompt template to use (Version
                3.5) -- see ``utils.constants.AVAILABLE_PROMPT_STYLES``.
                Defaults to the general-purpose style, matching Version 3
                behavior for existing callers.

        Raises:
            core.exceptions.AutoCutAIError: (or a subclass) if the provider
                request fails.
            core.exceptions.ResponseParsingError: if the provider's
                response is not valid, schema-conformant JSON.
            core.exceptions.EditPlanValidationError: if the parsed plan has
                structural problems (Version 3.5) that could not be safely
                auto-repaired -- see :class:`ai.edit_plan_validator.EditPlanValidator`.
        """
        started_at = datetime.now()

        context = self._context_builder.build(analysis)
        prompt = self._prompt_builder.build(
            context, target_length_seconds, rules=self._rules, style=style, instructions_text=instructions_text
        )

        LoggingService.log_api_request(
            self._logger, provider=self._provider.name, model=self._model, task="edit_plan"
        )

        try:
            raw_response = self._provider.generate_reply(
                [Message(role=MessageRole.USER, content=prompt)], self._model
            )
        except AutoCutAIError as exc:
            self._logger.error("Edit-plan provider request failed: %s", exc)
            raise

        response_seconds = (datetime.now() - started_at).total_seconds()
        self._logger.info("Provider responded in %.2fs", response_seconds)

        try:
            plan = self._response_parser.parse(
                raw_response, video_duration_seconds=analysis.video.duration_seconds
            )
        except ResponseParsingError as exc:
            self._logger.error("Failed to parse edit-plan response: %s", exc)
            raise

        plan.processing_seconds = (datetime.now() - started_at).total_seconds()
        plan.model_used = f"{self._provider.name}:{self._model}"

        # Version 3.5: validate/repair before the plan is ever handed back.
        try:
            plan = self._validator.validate(plan, video_duration_seconds=analysis.video.duration_seconds)
        except EditPlanValidationError as exc:
            self._logger.error("Edit plan failed validation and could not be repaired: %s", exc)
            raise

        # Version 4.6, Feature 5/7/9: deterministic compress-instead-of-
        # monolithic-keep/remove pass (see ai.plan_refiner).
        plan = self._refiner.refine(plan, style=style)

        # Best-effort diagnostics only -- logged, never allowed to fail the
        # overall request or change the returned EditPlan.
        self._run_diagnostics(plan, analysis)

        self._logger.info(
            "Edit plan created: target_length=%.1fs keep=%d remove=%d compress=%d confidence=%.2f "
            "processing=%.2fs model=%s",
            plan.target_length_seconds,
            len(plan.keep_segments),
            len(plan.remove_segments),
            len(plan.compress_segments),
            plan.confidence,
            plan.processing_seconds,
            plan.model_used,
        )
        return plan

    def create_edit_plan_streaming(
        self,
        analysis: AnalysisResult,
        on_progress: Optional[Callable[[ProgressEvent], None]] = None,
        cancel_token: Optional[CancellationToken] = None,
        target_length_seconds: Optional[float] = None,
        style: str = DEFAULT_PROMPT_STYLE,
        instructions_text: Optional[str] = None,
    ) -> Tuple[EditPlan, PromptBuildResult, AIRequestLogEntry]:
        """Version 4 entry point: streaming, cancellable, progress-reporting edit planning.

        Behaviorally equivalent to :meth:`create_edit_plan` (same
        context/prompt/parse/validate/simulate pipeline, same return
        contract for the plan itself) but additionally:

        - Streams the provider's response instead of blocking until it
          fully arrives (Feature 1), recovering complete segments as they
          arrive via :class:`services.streaming_json_parser.StreamingEditPlanParser`
          (Feature 10).
        - Reports a :class:`models.generation_progress.ProgressEvent` for
          every stage, including live updates while streaming (Feature 2).
        - Checks ``cancel_token`` between stages and after every streamed
          chunk, so a user-requested cancel (Feature 3) stops generation
          within a fraction of a second instead of only between stages.
        - Uses the token-optimized compact prompt (Feature 4) and returns
          a :class:`models.ai_request_log.AIRequestLogEntry` with
          estimated token usage/cost (Features 7 & 9) for the caller to
          hand to :class:`services.ai_request_log_service.AIRequestLogService`.

        :meth:`create_edit_plan` is left completely unchanged for existing
        callers (e.g. ``scripts/analyze_and_plan.py``); this method is
        purely additive.
        """
        notify: Callable[[ProgressEvent], None] = on_progress or (lambda _event: None)
        token: CancellationToken = cancel_token or CancellationToken()
        run_started = time.monotonic()

        def elapsed() -> float:
            return time.monotonic() - run_started

        def emit(stage: GenerationStage, message: str = "", fraction: Optional[float] = None) -> None:
            notify(ProgressEvent(stage=stage, message=message, fraction=fraction, elapsed_seconds=elapsed()))

        started_at = datetime.now()

        emit(GenerationStage.PREPARING_PROMPT)
        context = self._context_builder.build(analysis, compact=True)
        prompt_result = self._prompt_builder.build_with_stats(
            context, target_length_seconds, rules=self._rules, style=style, compact=True,
            instructions_text=instructions_text,
        )
        self._logger.info(
            "Prompt ready for streaming request: %d chars (~%d tokens, %.1f%% smaller than pre-v4 baseline)",
            len(prompt_result.full_prompt),
            TokenUsageService.estimate_tokens(prompt_result.full_prompt),
            prompt_result.reduction.reduction_percent,
        )
        token.raise_if_cancelled()

        LoggingService.log_api_request(
            self._logger, provider=self._provider.name, model=self._model, task="edit_plan_stream"
        )

        emit(GenerationStage.GENERATING_EDIT_PLAN, "Waiting for the first response chunk...")
        stream_parser = StreamingEditPlanParser()
        stream_started = time.monotonic()
        segments_seen = 0

        def on_chunk(delta: str) -> None:
            nonlocal segments_seen
            token.raise_if_cancelled()
            new_segments = stream_parser.feed(delta)
            segments_seen += len(new_segments)
            emit(
                GenerationStage.GENERATING_EDIT_PLAN,
                f"Received {len(stream_parser.raw_text)} chars, {segments_seen} segment(s) so far...",
            )

        finish_reason = "stop"
        try:
            raw_response = self._provider.generate_reply_stream(
                [Message(role=MessageRole.USER, content=prompt_result.full_prompt)], self._model, on_chunk
            )
        except OperationCancelledError:
            emit(GenerationStage.CANCELLED, "Cancelled by user.")
            self._logger.info("Edit-plan streaming generation cancelled by user after %.2fs.", elapsed())
            raise
        except AutoCutAIError as exc:
            finish_reason = "error"
            emit(GenerationStage.ERROR, str(exc))
            self._logger.error("Edit-plan streaming provider request failed: %s", exc)
            self._log_request(
                prompt_result, "", started_at, stream_started, finish_reason="error", success=False, error=str(exc)
            )
            raise

        streaming_seconds = time.monotonic() - stream_started
        response_seconds = (datetime.now() - started_at).total_seconds()
        self._logger.info("Provider streamed full response in %.2fs (total %.2fs)", streaming_seconds, response_seconds)

        emit(GenerationStage.PARSING_JSON)
        try:
            plan = self._response_parser.parse(raw_response, video_duration_seconds=analysis.video.duration_seconds)
        except ResponseParsingError as exc:
            finish_reason = "error"
            emit(GenerationStage.ERROR, str(exc))
            self._logger.error("Failed to parse streamed edit-plan response: %s", exc)
            self._log_request(
                prompt_result, raw_response, started_at, stream_started, finish_reason="error", success=False,
                error=str(exc),
            )
            raise

        plan.processing_seconds = (datetime.now() - started_at).total_seconds()
        plan.model_used = f"{self._provider.name}:{self._model}"

        token.raise_if_cancelled()
        emit(GenerationStage.VALIDATION)
        try:
            plan = self._validator.validate(plan, video_duration_seconds=analysis.video.duration_seconds)
        except EditPlanValidationError as exc:
            finish_reason = "error"
            emit(GenerationStage.ERROR, str(exc))
            self._logger.error("Streamed edit plan failed validation and could not be repaired: %s", exc)
            self._log_request(
                prompt_result, raw_response, started_at, stream_started, finish_reason="error", success=False,
                error=str(exc),
            )
            raise

        emit(GenerationStage.SIMULATION)
        plan = self._refiner.refine(plan, style=style)
        self._run_diagnostics(plan, analysis)

        ai_log_entry = self._log_request(
            prompt_result, raw_response, started_at, stream_started, finish_reason=finish_reason, success=True
        )

        emit(GenerationStage.COMPLETED, "Edit plan generated successfully.")
        self._logger.info(
            "Streaming edit plan created: target_length=%.1fs keep=%d remove=%d compress=%d confidence=%.2f "
            "processing=%.2fs model=%s",
            plan.target_length_seconds,
            len(plan.keep_segments),
            len(plan.remove_segments),
            len(plan.compress_segments),
            plan.confidence,
            plan.processing_seconds,
            plan.model_used,
        )
        return plan, prompt_result, ai_log_entry

    def _log_request(
        self,
        prompt_result: PromptBuildResult,
        raw_response: str,
        started_at: datetime,
        stream_started: float,
        finish_reason: str,
        success: bool,
        error: Optional[str] = None,
    ) -> AIRequestLogEntry:
        """Build the :class:`AIRequestLogEntry` for one streaming request (Features 7 & 9).

        ``retry_count`` is always ``0`` here: the current
        :class:`ai.base_provider.AIProvider` interface doesn't surface how
        many internal retries/key-rotations a request needed (that detail
        stays encapsulated inside each provider's ``_run_with_key_rotation``).
        Exposing it would require a small, additive interface change --
        left as a documented follow-up rather than done speculatively here.
        """
        usage = TokenUsageService.estimate_usage(
            self._provider.name, self._model, prompt_result.full_prompt, raw_response
        )
        return AIRequestLogEntry(
            task="edit_plan",
            provider=self._provider.name,
            model=self._model,
            started_at=started_at,
            latency_seconds=(datetime.now() - started_at).total_seconds(),
            streaming_seconds=time.monotonic() - stream_started,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
            retry_count=0,
            finish_reason=finish_reason,
            success=success,
            error=error,
        )

    def simulate(self, plan: EditPlan, analysis: AnalysisResult):
        """Explicitly run :class:`ai.edit_simulator.EditSimulator` on ``plan``.

        Convenience wrapper for callers (e.g. a future preview screen) who
        want the :class:`models.edit_simulation_result.EditSimulationResult`
        directly, on demand, separate from the automatic diagnostic run
        inside :meth:`create_edit_plan`.
        """
        return self._simulator.simulate(plan, analysis.video.duration_seconds or 0.0)

    def _run_diagnostics(self, plan: EditPlan, analysis: AnalysisResult) -> None:
        """Run the simulator + scorer purely for logging; never raises."""
        try:
            self._simulator.simulate(plan, analysis.video.duration_seconds or 0.0)
        except Exception:  # noqa: BLE001 - diagnostics must never break the main flow
            self._logger.exception("Edit-plan simulation failed (non-fatal).")

        try:
            self._scorer.score_plan(plan)
        except Exception:  # noqa: BLE001
            self._logger.exception("Edit-plan score calculation failed (non-fatal).")
