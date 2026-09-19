"""Interactive EditPlan Chat (Version 4.5, Features 2 & 17).

Lets the user correct an already-generated EditPlan in natural language
("03:15~04:00 is too heavily cut." / "Keep it, the conversation there is
important.") without ever re-running Whisper or re-extracting frames --
the existing :class:`models.analysis_result.AnalysisResult` (transcript,
frames) is reused as-is, and only the affected EditPlan segments are
regenerated (Feature 2).

Conversation memory is session/project-scoped (Feature 17): rather than
resending the entire chat transcript to the provider on every turn (which
would grow the prompt unboundedly), a short rolling summary of prior
turns is kept and sent instead, still giving the model enough context to
resolve references like "it" to the most recently discussed segment.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ai.base_provider import AIProvider
from core.exceptions import AutoCutAIError, ResponseParsingError
from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan, EditPlanSegment
from models.message import Message, MessageRole
from services.edit_plan_revision_service import EditPlanRevisionService
from services.logging_service import LoggingService
from services.personal_profile_service import PersonalProfileService
from utils.constants import EDIT_ACTION_COMPRESS, EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE, VALID_EDIT_ACTIONS

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_MAX_ROLLING_TURNS = 6  # how many past (user, assistant) turn summaries are kept -- Feature 17
_TRANSCRIPT_CONTEXT_CHARS = 6000  # cap so a long video's full transcript never dominates the prompt


class EditPlanChatError(AutoCutAIError):
    """Raised when the AI's EditPlan-chat response can't be parsed/applied."""


@dataclass
class EditPlanChatTurn:
    """Everything one chat exchange produced."""

    user_message: str
    assistant_reply: str
    changed_segments: List[EditPlanSegment]
    updated_plan: EditPlan


@dataclass
class _RollingTurn:
    user_message: str
    assistant_reply: str


@dataclass
class EditPlanChatService:
    """One instance per open EditPlan (create fresh via :meth:`start` for each project)."""

    provider: AIProvider
    model: str
    revision_service: Optional[EditPlanRevisionService] = None
    personal_profile_service: Optional[PersonalProfileService] = None
    logger: Optional[logging.Logger] = None

    _analysis: Optional[AnalysisResult] = field(default=None, init=False, repr=False)
    _plan: Optional[EditPlan] = field(default=None, init=False, repr=False)
    _turns: List[_RollingTurn] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = LoggingService.get_logger("services.edit_plan_chat")

    # ------------------------------------------------------------------
    def start(self, analysis: AnalysisResult, plan: EditPlan) -> None:
        """Begin (or reset) the chat session for a freshly generated/loaded EditPlan."""
        self._analysis = analysis
        self._plan = plan
        self._turns = []
        if self.revision_service is not None:
            self.revision_service.start(plan, source="initial")

    @property
    def current_plan(self) -> Optional[EditPlan]:
        return self._plan

    def send(self, user_message: str) -> EditPlanChatTurn:
        """Send one message and apply whatever localized EditPlan change the AI proposes."""
        if self._plan is None or self._analysis is None:
            raise EditPlanChatError("No EditPlan is loaded -- call start() first.")
        user_message = user_message.strip()
        if not user_message:
            raise ValueError("Cannot send an empty message.")

        prompt = self._build_prompt(user_message)
        try:
            raw_response = self.provider.generate_reply([Message(role=MessageRole.USER, content=prompt)], self.model)
        except AutoCutAIError as exc:
            self.logger.error("EditPlan chat provider request failed: %s", exc)
            raise

        reply_text, changes, reorder = self._parse_response(raw_response)
        updated_plan, changed_segments = self._apply_changes(self._plan, changes, reorder)
        self._plan = updated_plan

        self._turns.append(_RollingTurn(user_message=user_message, assistant_reply=reply_text))
        self._turns = self._turns[-_MAX_ROLLING_TURNS:]

        change_summary = reply_text or f"{len(changed_segments)} segment(s) updated."
        if self.revision_service is not None:
            self.revision_service.record_change(updated_plan, change_summary, source="chat")

        if self.personal_profile_service is not None:
            self._maybe_learn_from_turn(user_message, changed_segments)

        self.logger.info(
            "EditPlan chat turn applied: %d segment(s) changed for message %r", len(changed_segments), user_message
        )
        return EditPlanChatTurn(
            user_message=user_message, assistant_reply=reply_text,
            changed_segments=changed_segments, updated_plan=updated_plan,
        )

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------
    def _build_prompt(self, user_message: str) -> str:
        assert self._plan is not None and self._analysis is not None
        plan_lines = [
            f'{{"start":{s.start_seconds:.2f},"end":{s.end_seconds:.2f},"action":"{s.action}","reason":{json.dumps(s.reason)}}}'
            for s in self._plan.all_segments_sorted
        ]
        transcript_text = self._analysis.transcript.to_formatted_text()
        if len(transcript_text) > _TRANSCRIPT_CONTEXT_CHARS:
            transcript_text = transcript_text[:_TRANSCRIPT_CONTEXT_CHARS] + "\n... (transcript truncated)"

        history_lines = []
        for turn in self._turns:
            history_lines.append(f"User: {turn.user_message}")
            history_lines.append(f"You: {turn.assistant_reply}")
        history_block = "\n".join(history_lines) if history_lines else "(no prior messages this session)"

        if self._plan.output_order:
            order_lines = [f'  {{"start":{s:.2f},"end":{e:.2f}}}' for s, e in self._plan.output_order]
            current_order_block = (
                "The final export is currently set to play in this custom order (not original "
                "chronological order):\n" + "\n".join(order_lines)
            )
        else:
            current_order_block = "The final export currently plays in original chronological (source) order."

        return (
            "You are the same professional video editor, now in a follow-up conversation "
            "about an EditPlan you already produced. The user wants to adjust specific "
            "parts of it. You are NOT re-analyzing the video from scratch -- reuse the "
            "transcript and current plan given below, and only change the segments that "
            "need to change.\n\n"
            f"Current EditPlan (every segment, in order):\n{chr(10).join(plan_lines)}\n\n"
            f"{current_order_block}\n\n"
            f"Source transcript (for context on what's actually being discussed):\n{transcript_text}\n\n"
            f"Recent conversation in this session (may refer to 'it'/'that part' from here):\n{history_block}\n\n"
            f"The user's new message: {user_message}\n\n"
            "Respond with ONLY raw JSON, no markdown fences, no extra text, in exactly this shape:\n"
            "{\n"
            '  "reply": <short, human string explaining what you changed and why -- '
            "or why you didn't change anything>,\n"
            '  "changes": [\n'
            "    {\n"
            '      "start": <number, seconds>,\n'
            '      "end": <number, seconds>,\n'
            '      "action": "keep" | "remove" | "compress",\n'
            '      "reason": <specific, human-readable reason for this segment>,\n'
            '      "compression": <REQUIRED when action is "compress", otherwise omit -- '
            '{"speed_factor": <number, e.g. 2.5 = play at 2.5x speed>}>\n'
            "    }\n"
            "  ],\n"
            '  "reorder": <OPTIONAL -- ONLY include this if the user asked to change the '
            "PLAYBACK ORDER of the final export (e.g. \"move this part to the front\", "
            "\"put the ending first\", \"swap these two scenes\"). If included, it MUST be "
            "the COMPLETE list of every KEPT/COMPRESSED range (same ranges as the current "
            "EditPlan's keep/compress segments, after applying 'changes' above -- not a "
            "partial list, not new ranges), listed in the NEW desired playback order:\n"
            "  [ {\"start\": <number>, \"end\": <number>}, ... ]\n"
            'Omit "reorder" entirely if the user did not ask to change the order.>\n'
            "}\n"
            "'changes' should contain ONLY the segments you are adding or modifying -- do "
            "not repeat segments that stay the same. Each change's [start, end) range "
            "replaces whatever existing segment(s) currently occupy that range. Use "
            "'compress' (not a hard keep/remove) for ordinary/repetitive stretches the "
            "user wants shortened but still visible -- see the earlier core-goal guidance "
            "about not keeping or removing long ordinary stretches wholesale. If the "
            "user's request doesn't require any change (e.g. they're just asking a "
            "question), return an empty 'changes' list."
        )

    # ------------------------------------------------------------------
    # Response parsing / application
    # ------------------------------------------------------------------
    def _parse_response(self, raw_response: str) -> Tuple[str, list, Optional[list]]:
        text = raw_response.strip()
        text = _CODE_FENCE_RE.sub("", text).strip()
        if not text.startswith("{"):
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EditPlanChatError(f"AI response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise EditPlanChatError("AI response JSON must be an object.")

        reply = str(payload.get("reply", "")).strip()
        raw_changes = payload.get("changes", [])
        if not isinstance(raw_changes, list):
            raise EditPlanChatError("'changes' must be a list.")

        changes = []
        for index, raw in enumerate(raw_changes):
            if not isinstance(raw, dict):
                raise EditPlanChatError(f"Change {index} is not a JSON object.")
            missing = [key for key in ("start", "end", "action", "reason") if key not in raw]
            if missing:
                raise EditPlanChatError(f"Change {index} is missing field(s): {', '.join(missing)}.")
            if raw["action"] not in VALID_EDIT_ACTIONS:
                raise EditPlanChatError(f"Change {index} has invalid action '{raw['action']}'.")
            if float(raw["end"]) <= float(raw["start"]):
                raise EditPlanChatError(f"Change {index} has end <= start.")
            changes.append(raw)

        reorder: Optional[list] = None
        raw_reorder = payload.get("reorder")
        if raw_reorder is not None:
            if not isinstance(raw_reorder, list):
                raise EditPlanChatError("'reorder' must be a list.")
            reorder = []
            for index, raw in enumerate(raw_reorder):
                if not isinstance(raw, dict) or "start" not in raw or "end" not in raw:
                    raise EditPlanChatError(f"Reorder entry {index} must be an object with 'start' and 'end'.")
                if float(raw["end"]) <= float(raw["start"]):
                    raise EditPlanChatError(f"Reorder entry {index} has end <= start.")
                reorder.append((float(raw["start"]), float(raw["end"])))

        return reply, changes, reorder

    @staticmethod
    def _apply_changes(
        plan: EditPlan, changes: list, reorder: Optional[list] = None
    ) -> Tuple[EditPlan, List[EditPlanSegment]]:
        """Replace whatever segment(s) occupy each change's range (Feature 2: localized regeneration).

        ``reorder``, when given, becomes the plan's new
        :attr:`EditPlan.output_order` (the desired final playback order --
        see that field's docstring). When omitted, whatever order the
        plan already had (if any) carries over unchanged, so a reorder
        requested in an earlier turn survives later content-only edits.
        The actual validation that ``reorder`` really is a permutation of
        the final KEEP/COMPRESS ranges happens downstream in
        :func:`davinci.edit_plan_applier.process_edit_plan`, which is the
        single source of truth for what's actually exportable -- this
        method just carries the request along.
        """
        segments = list(plan.all_segments_sorted)
        changed_segments: List[EditPlanSegment] = []

        for raw in changes:
            start, end = float(raw["start"]), float(raw["end"])
            action = str(raw["action"])
            # Drop any existing segment that overlaps [start, end) at all --
            # this is what makes the AI's change authoritative for that range.
            segments = [s for s in segments if s.end_seconds <= start or s.start_seconds >= end]
            compression = raw.get("compression") if action == EDIT_ACTION_COMPRESS else None
            speed_factor = None
            if isinstance(compression, dict) and isinstance(compression.get("speed_factor"), (int, float)):
                speed_factor = float(compression["speed_factor"])
            new_segment = EditPlanSegment(
                start_seconds=start, end_seconds=end, action=action, reason=str(raw["reason"]).strip(),
                compression_speed_factor=speed_factor,
            )
            segments.append(new_segment)
            changed_segments.append(new_segment)

        segments.sort(key=lambda s: s.start_seconds)
        keep_segments = [s for s in segments if s.action == EDIT_ACTION_KEEP]
        remove_segments = [s for s in segments if s.action == EDIT_ACTION_REMOVE]
        compress_segments = [s for s in segments if s.action == EDIT_ACTION_COMPRESS]

        updated_plan = EditPlan(
            target_length_seconds=(
                sum(s.effective_duration_seconds for s in keep_segments + compress_segments) or plan.target_length_seconds
            ),
            keep_segments=keep_segments,
            remove_segments=remove_segments,
            compress_segments=compress_segments,
            confidence=plan.confidence,
            reasons=plan.reasons,
            warnings=plan.warnings,
            processing_seconds=plan.processing_seconds,
            model_used=plan.model_used,
            output_order=reorder if reorder is not None else plan.output_order,
        )
        return updated_plan, changed_segments

    # ------------------------------------------------------------------
    # Feature 14: learn from corrections made through the chat
    # ------------------------------------------------------------------
    def _maybe_learn_from_turn(self, user_message: str, changed_segments: List[EditPlanSegment]) -> None:
        """Best-effort: if the user's correction clearly names a known topic, record feedback.

        Deliberately conservative -- if no topic can be confidently
        identified from the message, nothing is recorded rather than
        guessing (Feature 14: "Do not blindly change preferences from one
        correction").
        """
        if self.personal_profile_service is None or not changed_segments:
            return
        lowered = user_message.lower()
        wants_keep = any(w in lowered for w in ("keep", "restore", "don't remove", "do not remove", "important"))
        wants_remove = any(w in lowered for w in ("remove", "cut", "delete", "too long", "boring"))
        if wants_keep == wants_remove:
            return  # ambiguous -- don't guess

        for segment in changed_segments:
            topic = self._infer_topic(segment.reason)
            if topic:
                self.personal_profile_service.record_feedback(topic, agrees_with_keep=wants_keep)

    @staticmethod
    def _infer_topic(reason: str) -> Optional[str]:
        """Very small heuristic: use the first clause of the reason as a topic label.

        This intentionally stays simple; a mis-identified topic only costs
        one low-weight feedback point (see Feature 14 gradual confidence),
        never an immediate behavior change.
        """
        cleaned = reason.strip().split(" -- ")[0].split(".")[0].strip()
        if not cleaned or len(cleaned) > 60:
            return None
        return cleaned
