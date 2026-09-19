"""Structured results for the Runtime Diagnostics page.

Each individual check (Python, FFmpeg, Whisper, CUDA, providers,
configuration, ...) becomes one :class:`DiagnosticCheck` with a traffic-light
:class:`DiagnosticStatus`, so the UI never needs provider- or
library-specific logic to decide what color to show.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List


class DiagnosticStatus(str, Enum):
    """Traffic-light status for one diagnostic check."""

    OK = "ok"          # green
    WARNING = "warning"  # yellow
    ERROR = "error"     # red


@dataclass(slots=True)
class DiagnosticCheck:
    """One row of the Runtime Diagnostics report."""

    name: str
    status: DiagnosticStatus
    summary: str
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass(slots=True)
class RuntimeDiagnosticsReport:
    """The full Runtime Diagnostics page contents, generated on demand."""

    checks: List[DiagnosticCheck] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)

    @property
    def overall_status(self) -> DiagnosticStatus:
        """Worst status across every check (ERROR > WARNING > OK)."""
        if any(check.status == DiagnosticStatus.ERROR for check in self.checks):
            return DiagnosticStatus.ERROR
        if any(check.status == DiagnosticStatus.WARNING for check in self.checks):
            return DiagnosticStatus.WARNING
        return DiagnosticStatus.OK

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "overall_status": self.overall_status.value,
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_text_report(self) -> str:
        """Plain-text report suitable for the page's "Copy Report" button."""
        lines = [
            "AutoCutAI Runtime Diagnostics",
            f"Generated: {self.generated_at.isoformat(timespec='seconds')}",
            f"Overall status: {self.overall_status.value.upper()}",
            "",
        ]
        for check in self.checks:
            lines.append(f"[{check.status.value.upper()}] {check.name}: {check.summary}")
            if check.details:
                lines.append(f"    {check.details}")
        return "\n".join(lines)
