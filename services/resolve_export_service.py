"""Orchestrates Version 5's full EditPlan -> Resolve timeline flow behind one
call, the same way :class:`services.edit_generation_service.EditGenerationService`
orchestrates Version 4's generation flow.

    EditPlan -> davinci.edit_plan_applier (validate/compute REMOVE ranges)
             -> davinci.resolve_connection (connect to Resolve)
             -> davinci.timeline_editor (duplicate + cut)

Kept as its own service (rather than folding into ``EditGenerationService``)
because it has an entirely different failure mode: every other service in
this project fails because of bad AI output or a bad video file, while this
one also has to gracefully handle "Resolve isn't even running" -- something
none of the existing error handling was written to expect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.exceptions import EditPlanResolveApplyError, ResolveError, ResolveXMLExportError
from davinci import edit_plan_applier, resolve_connection, resolve_xml_exporter, timeline_editor
from davinci.edit_plan_applier import ResolveEditPlan
from davinci.resolve_xml_exporter import ResolveXMLExportError as _XMLBuildError
from models.edit_plan import EditPlan
from services.logging_service import LoggingService
from utils.constants import RESOLVE_EDIT_TIMELINE_PREFIX, RESOLVE_XML_EXPORT_FILENAME_SUFFIX


@dataclass(slots=True)
class ResolveApplyReport:
    """Everything one Version 5 "apply to Resolve" run produced."""

    dry_run: bool
    plan: ResolveEditPlan
    edit_timeline_name: Optional[str] = None
    ranges_applied: int = 0
    ranges_requested: int = 0
    clips_deleted: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def preview_lines(self) -> List[str]:
        return edit_plan_applier.build_preview_lines(self.plan)


@dataclass(slots=True)
class ResolveXMLExportReport:
    """Everything one "Export Resolve XML" run produced.

    Deliberately mirrors :class:`ResolveApplyReport`'s shape (``plan`` +
    ``warnings`` + a ``preview_lines`` property) so
    :class:`ui.widgets.resolve_xml_export_dialog.ResolveXMLExportDialog`
    can reuse the exact same Dry Run preview rendering as
    :class:`ui.widgets.resolve_export_dialog.ResolveExportDialog` (Section
    19 of the V5 spec: "기존 Preview 기능을 중복 구현하지 말고 재사용").
    """

    output_path: Path
    plan: ResolveEditPlan
    clip_count: int
    fps: float
    warnings: List[str] = field(default_factory=list)

    @property
    def preview_lines(self) -> List[str]:
        return edit_plan_applier.build_preview_lines(self.plan)


class ResolveExportService:
    """Stateless: every call takes exactly the EditPlan (and known media
    duration) it needs, and either previews or actually touches Resolve."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("services.resolve_export")

    def preview(self, plan: EditPlan, media_duration_seconds: Optional[float] = None) -> ResolveApplyReport:
        """Dry Run (Section 10): compute exactly what would be cut, touching
        nothing in Resolve. Safe to call even if Resolve isn't running."""
        resolved = edit_plan_applier.process_edit_plan(plan, media_duration_seconds)
        return ResolveApplyReport(dry_run=True, plan=resolved, warnings=list(resolved.warnings))

    def apply(self, plan: EditPlan, media_duration_seconds: Optional[float] = None) -> ResolveApplyReport:
        """Actually cut the REMOVE ranges into a new, duplicated Resolve timeline.

        Raises:
            core.exceptions.ResolveError (or a subclass): Resolve isn't
                running, has no project/timeline, or rejected the edit.
                Never lets a raw Resolve-scripting-API exception escape --
                every failure path is wrapped so the UI layer only ever has
                to catch ``ResolveError``.
        """
        resolved = edit_plan_applier.process_edit_plan(plan, media_duration_seconds)
        warnings = list(resolved.warnings)

        if not plan.all_segments_sorted:
            raise EditPlanResolveApplyError("This EditPlan has no segments at all -- nothing to apply to Resolve.")

        handle = resolve_connection.connect(require_timeline=True)

        if not resolved.has_removals:
            # A legitimate outcome (e.g. every segment is KEEP/COMPRESS) --
            # still duplicate the timeline so the workflow is consistent and
            # the user gets a named, protected copy either way.
            self._logger.info("EditPlan has no REMOVE segments to apply; duplicating timeline unchanged.")

        new_timeline = timeline_editor.duplicate_timeline(handle)

        try:
            apply_result = timeline_editor.apply_remove_ranges(handle, new_timeline, resolved.remove_ranges)
        except ResolveError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let a raw Resolve API exception escape to the UI
            raise EditPlanResolveApplyError(f"Applying the EditPlan to Resolve failed: {exc}") from exc

        self._logger.info(
            "Applied EditPlan to Resolve timeline '%s': %d/%d range(s) cut, %d clip(s) deleted.",
            apply_result.edit_timeline_name, apply_result.ranges_applied, apply_result.ranges_requested,
            apply_result.clips_deleted,
        )

        return ResolveApplyReport(
            dry_run=False,
            plan=resolved,
            edit_timeline_name=apply_result.edit_timeline_name,
            ranges_applied=apply_result.ranges_applied,
            ranges_requested=apply_result.ranges_requested,
            clips_deleted=apply_result.clips_deleted,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # DaVinci Resolve Free support: XML export (no Resolve API involved --
    # see davinci.resolve_xml_exporter's module docstring). Kept on this
    # same service (rather than a brand-new one) because it shares the
    # exact same "resolve an EditPlan into ranges, then hand it to a
    # Resolve-facing backend" shape as preview()/apply() above; only the
    # backend differs (write a file vs. call the Scripting API).
    # ------------------------------------------------------------------

    @staticmethod
    def suggested_xml_path(source_video_path: Path) -> Path:
        """Default save location (Section 20): ``<source_name>_AutoCutAI.xml``
        next to the source video."""
        source_video_path = Path(source_video_path)
        return source_video_path.with_name(f"{source_video_path.stem}{RESOLVE_XML_EXPORT_FILENAME_SUFFIX}.xml")

    def export_xml(
        self,
        plan: EditPlan,
        source_video_path: Path,
        fps: Optional[float],
        output_path: Path,
        media_duration_seconds: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        audio_sample_rate: Optional[int] = None,
        audio_depth: Optional[int] = None,
        audio_channels: Optional[int] = None,
        source_start_timecode_seconds: Optional[float] = None,
    ) -> ResolveXMLExportReport:
        """Export ``plan`` as a Resolve-importable Final Cut Pro XML file.

        Never touches the Resolve Scripting API and never requires Resolve
        to be installed or running (Section 17 of the V5 spec) -- only
        ``plan``, the source video's own path/fps/duration, and a place to
        write the ``.xml`` file are needed.

        ``source_start_timecode_seconds`` should be the source file's own
        starting timecode -- typically
        :attr:`models.video_project.VideoProject.video_start_time_seconds`,
        already probed via ``ffprobe`` earlier in the pipeline (see
        :mod:`video.timeline_sync`). Passing ``None``/omitting it defaults
        to ``0.0`` (assume the file starts at ``00:00:00:00``), which is
        wrong for any source whose embedded timecode starts elsewhere
        (commonly ``01:00:00:00`` on screen recordings/capture-card
        footage) and is exactly what causes Resolve to reject the whole
        import with ``"No overlap"`` once it reads the real media.

        Raises:
            core.exceptions.ResolveXMLExportError: no EditPlan/segments,
                the source media file can't be found, the frame rate is
                unknown, the plan resolves to zero KEEP ranges, or the
                file could not be written. Every failure path is wrapped
                so the UI layer only ever has to catch ``ResolveError``,
                the same as the Resolve Studio ``apply()`` path.
        """
        if not plan.all_segments_sorted:
            raise ResolveXMLExportError(
                "No EditPlan is currently loaded.\nGenerate or load an EditPlan first."
            )

        source_video_path = Path(source_video_path) if source_video_path else None
        if source_video_path is None or not source_video_path.exists():
            raise ResolveXMLExportError(
                "Could not find the source media file.\nPlease verify that the original video still exists."
            )

        if fps is None or fps <= 0:
            raise ResolveXMLExportError("Could not determine source frame rate.")

        resolved = edit_plan_applier.process_edit_plan(plan, media_duration_seconds)

        try:
            xml_text = resolve_xml_exporter.build_edit_xml(
                resolved, source_video_path, fps,
                sequence_name=RESOLVE_EDIT_TIMELINE_PREFIX, width=width, height=height,
                audio_sample_rate=audio_sample_rate, audio_depth=audio_depth, audio_channels=audio_channels,
                source_start_timecode_seconds=source_start_timecode_seconds or 0.0,
            )
        except _XMLBuildError as exc:
            raise ResolveXMLExportError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - never let a raw XML-building exception escape to the UI
            raise ResolveXMLExportError(f"Failed to export Resolve XML.\n\nDetails:\n{exc}") from exc

        output_path = Path(output_path)
        try:
            output_path.write_text(xml_text, encoding="utf-8")
        except OSError as exc:
            raise ResolveXMLExportError(f"Failed to export Resolve XML.\n\nDetails:\n{exc}") from exc

        self._logger.info(
            "Exported Resolve XML to %s (%d clip(s), %d REMOVE range(s) cut, fps=%.3f)",
            output_path, len(resolved.playback_order or resolved.keep_ranges), len(resolved.remove_ranges), fps,
        )

        return ResolveXMLExportReport(
            output_path=output_path,
            plan=resolved,
            clip_count=len(resolved.playback_order or resolved.keep_ranges),
            fps=fps,
            warnings=list(resolved.warnings),
        )
