"""Persists free-text Editing Instructions (Version 4.5, Feature 1).

Persistent instructions (scope ``"persistent"``) are stored globally at
``config/persistent_instructions.json`` and automatically included for
every project; this-video-only instructions live only in memory for the
current session (they're saved *with the project* once an EditPlan is
saved via :class:`services.edit_plan_io_service.EditPlanIOService`, which
already serializes arbitrary metadata alongside a plan -- see that
service for the save format).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from models.editing_instructions import INSTRUCTION_SCOPE_PERSISTENT, EditingInstructionSet
from services.logging_service import LoggingService
from services.personal_profile_service import PersonalProfileService
from utils.file_utils import ensure_directory, get_project_root

_PERSISTENT_FILE_NAME = "persistent_instructions.json"


class EditingInstructionsService:
    """Owns the current project's instructions plus the persistent, cross-project set."""

    def __init__(
        self,
        personal_profile_service: Optional[PersonalProfileService] = None,
        storage_path: Optional[Path] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._storage_path = storage_path or (get_project_root() / "config" / _PERSISTENT_FILE_NAME)
        self._logger = logger or LoggingService.get_logger("services.editing_instructions")
        self._personal_profile_service = personal_profile_service

        # `current` holds every instruction (persistent + this-video) that
        # applies to the project currently open in the AI Editor page.
        self.current: EditingInstructionSet = EditingInstructionSet()
        self._load_persistent_into_current()

    def _load_persistent_into_current(self) -> None:
        persistent = self._load_persistent_only()
        self.current = EditingInstructionSet(instructions=list(persistent.instructions))

    def _load_persistent_only(self) -> EditingInstructionSet:
        if not self._storage_path.exists():
            return EditingInstructionSet()
        try:
            data = json.loads(self._storage_path.read_text(encoding="utf-8"))
            return EditingInstructionSet.from_dict(data)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            self._logger.warning("Could not load persistent instructions (%s); starting fresh.", exc)
            return EditingInstructionSet()

    def _save_persistent(self) -> None:
        persistent_only = EditingInstructionSet(instructions=self.current.persistent_instructions())
        try:
            ensure_directory(self._storage_path.parent)
            self._storage_path.write_text(
                json.dumps(persistent_only.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            self._logger.exception("Failed to save persistent editing instructions (non-fatal).")

    def new_project(self) -> None:
        """Reset this-video-only instructions when a new video is opened; keep persistent ones."""
        self._load_persistent_into_current()

    def add_instruction(self, text: str, known_topics: Optional[list] = None) -> None:
        """Add one instruction, auto-classifying temporary vs persistent (Feature 13).

        If it's classified persistent and a :class:`PersonalProfileService`
        was provided, also attempts to learn a structured preference from
        it immediately (Feature 13's "the system should distinguish...
        only persistent preferences should affect future videos").
        """
        text = text.strip()
        if not text:
            return
        is_persistent = PersonalProfileService.is_persistent_instruction(text)
        scope = INSTRUCTION_SCOPE_PERSISTENT if is_persistent else "video"
        self.current.add(text, scope=scope)
        LoggingService.log_user_action(
            self._logger, "add_editing_instruction", scope=scope, length=len(text)
        )
        if is_persistent:
            self._save_persistent()
            if self._personal_profile_service is not None:
                self._personal_profile_service.learn_from_instruction(text, known_topics=known_topics)

    def remove_instruction(self, index: int) -> None:
        if 0 <= index < len(self.current.instructions):
            removed = self.current.instructions.pop(index)
            if removed.scope == INSTRUCTION_SCOPE_PERSISTENT:
                self._save_persistent()

    def to_prompt_text(self) -> str:
        return self.current.to_prompt_text()

    def fingerprint(self) -> str:
        return self.current.fingerprint()
