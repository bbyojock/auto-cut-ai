"""Thin orchestration layer over Version 5.4's multicam angle-switching
engine: build a plan, then export it as a Resolve-importable XML.

Kept separate from :class:`ai.multicam_angle_planner` (pure decision
logic, no I/O) and :mod:`davinci.multicam_xml_exporter` (pure XML
string-building, no I/O) so each stays independently testable -- this
class exists only to be the one thing the UI layer needs to call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from ai.multicam_angle_planner import (
    DEFAULT_BUCKET_SECONDS,
    DEFAULT_MIN_SEGMENT_SECONDS,
    DEFAULT_SWITCH_MARGIN,
    build_angle_switch_plan,
)
from davinci.multicam_xml_exporter import write_multicam_edit_xml
from models.analysis_result import AnalysisResult
from models.angle_switch_plan import AngleSwitchPlan
from models.multicam_group import MulticamGroup
from services.logging_service import LoggingService


class MulticamEditService:
    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("services.multicam_edit")

    def build_plan(
        self,
        group: MulticamGroup,
        analysis_by_path: Dict[Path, AnalysisResult],
        bucket_seconds: float = DEFAULT_BUCKET_SECONDS,
        min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
        switch_margin: float = DEFAULT_SWITCH_MARGIN,
    ) -> AngleSwitchPlan:
        plan = build_angle_switch_plan(
            group, analysis_by_path,
            bucket_seconds=bucket_seconds,
            min_segment_seconds=min_segment_seconds,
            switch_margin=switch_margin,
        )
        self._logger.info(
            "Angle-switch plan built for take @ %s: %d segments, %d switches, cameras=%s",
            group.timestamp, len(plan.segments), plan.switch_count, plan.cameras_used,
        )
        return plan

    def export_xml(self, plan: AngleSwitchPlan, output_path: Path, fps: float, **kwargs) -> Path:
        written = write_multicam_edit_xml(plan, output_path, fps=fps, **kwargs)
        self._logger.info("Multicam edit XML written: %s", written)
        return written
