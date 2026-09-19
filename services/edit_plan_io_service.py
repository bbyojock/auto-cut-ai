"""Saves/loads an :class:`EditPlan` as a standalone ``*.editplan.json`` file.

Loading a previously-saved plan is meant to skip Whisper transcription and
the AI request entirely (Version 4, Feature 13) -- the caller only needs
to re-import the video (cheap: metadata only, via
:class:`video.video_importer.VideoImporter`) to know its current duration
for display/validation purposes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.exceptions import AutoCutAIError
from models.edit_plan import EditPlan
from services.logging_service import LoggingService

EDIT_PLAN_FILE_SUFFIX = ".editplan.json"
_FILE_FORMAT_VERSION = 1


class EditPlanFileError(AutoCutAIError):
    """Raised when a ``.editplan.json`` file can't be read/written/parsed."""


@dataclass(slots=True)
class LoadedEditPlanFile:
    """An :class:`EditPlan` loaded from disk, plus the metadata saved with it."""

    plan: EditPlan
    source_video_path: Optional[str]
    saved_at: Optional[str]


class EditPlanIOService:
    """Reads/writes the Feature 13 ``video.editplan.json`` sidecar file."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("services.edit_plan_io")

    @staticmethod
    def suggested_path(video_path: Path) -> Path:
        """The default save location for a video: ``<video>.editplan.json`` next to it."""
        return video_path.with_suffix("").with_suffix(EDIT_PLAN_FILE_SUFFIX)

    def save(self, path: Path, plan: EditPlan, source_video_path: Optional[Path] = None) -> None:
        payload = {
            "format_version": _FILE_FORMAT_VERSION,
            "source_video_path": str(source_video_path) if source_video_path else None,
            "edit_plan": plan.to_dict(),
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            raise EditPlanFileError(f"Could not save EditPlan to {path}: {exc}") from exc
        self._logger.info("EditPlan saved to %s", path)

    def load(self, path: Path) -> LoadedEditPlanFile:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EditPlanFileError(f"Could not read EditPlan file {path}: {exc}") from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EditPlanFileError(f"{path} is not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise EditPlanFileError(f"{path} does not contain a JSON object.")

        # Backward/forward tolerant: accept either the Feature 13 envelope
        # shape ({"edit_plan": {...}, ...}) or a bare EditPlan.to_dict()
        # (e.g. a plan exported via Feature 17's "Export EditPlan JSON").
        plan_data = payload.get("edit_plan", payload)
        try:
            plan = EditPlan.from_dict(plan_data)
        except (KeyError, ValueError, TypeError) as exc:
            raise EditPlanFileError(f"{path} does not contain a valid EditPlan: {exc}") from exc

        self._logger.info("EditPlan loaded from %s", path)
        return LoadedEditPlanFile(
            plan=plan,
            source_video_path=payload.get("source_video_path"),
            saved_at=plan_data.get("created_at"),
        )
