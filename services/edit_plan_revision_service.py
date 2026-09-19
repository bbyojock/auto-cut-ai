"""Owns the EditPlan revision history for the current session (Version 4.5, Feature 3).

Mirrors :class:`core.session.ConversationSession`: revision history is
scoped to the currently open EditPlan for the lifetime of the running
application. A saved ``.editplan.json`` file (see
:class:`services.edit_plan_io_service.EditPlanIOService`) always reflects
the plan at the *current* cursor position, so saving after undoing is
exactly "save what I'm looking at" -- history itself isn't round-tripped
to disk, keeping the on-disk format simple and match Version 4's.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from models.edit_plan import EditPlan
from models.edit_plan_revision import EditPlanDiffEntry, EditPlanHistory, EditPlanRevision
from services.logging_service import LoggingService


class EditPlanRevisionService:
    """Thin, testable wrapper around one :class:`EditPlanHistory`."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("services.edit_plan_revision")
        self._history: EditPlanHistory = EditPlanHistory()

    @property
    def history(self) -> EditPlanHistory:
        return self._history

    def start(self, plan: EditPlan, source: str = "initial") -> EditPlanRevision:
        """Begin a fresh history for a newly generated/loaded EditPlan."""
        revision = self._history.push_initial(plan, source=source)
        self._logger.info("EditPlan revision history started (%s).", source)
        return revision

    def record_change(self, plan: EditPlan, change_summary: str, source: str = "chat") -> EditPlanRevision:
        revision = self._history.push(plan, change_summary, source=source)
        self._logger.info("EditPlan revision recorded: %s", change_summary)
        return revision

    @property
    def current_plan(self) -> Optional[EditPlan]:
        current = self._history.current
        return current.plan if current else None

    def can_undo(self) -> bool:
        return self._history.can_undo

    def can_redo(self) -> bool:
        return self._history.can_redo

    def undo(self) -> Optional[EditPlan]:
        revision = self._history.undo()
        if revision:
            LoggingService.log_user_action(self._logger, "edit_plan_undo")
        return revision.plan if revision else None

    def redo(self) -> Optional[EditPlan]:
        revision = self._history.redo()
        if revision:
            LoggingService.log_user_action(self._logger, "edit_plan_redo")
        return revision.plan if revision else None

    def restore(self, index: int) -> Optional[EditPlan]:
        revision = self._history.restore(index)
        if revision:
            LoggingService.log_user_action(self._logger, "edit_plan_restore", index=index)
        return revision.plan if revision else None

    def list_revisions(self) -> List[EditPlanRevision]:
        return list(self._history.revisions)

    def compare(self, index_a: int, index_b: int) -> List[EditPlanDiffEntry]:
        return self._history.compare(index_a, index_b)
