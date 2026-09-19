"""Per-stage timing report for one end-to-end generation run (Version 4, Feature 16)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class StageTiming:
    """Wall-clock time spent in a single pipeline stage."""

    stage: str
    seconds: float

    def to_dict(self) -> dict:
        return {"stage": self.stage, "seconds": round(self.seconds, 3)}


@dataclass(slots=True)
class BenchmarkReport:
    """Every stage timing for one run, plus the overall total."""

    stages: List[StageTiming] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(stage.seconds for stage in self.stages)

    def add(self, stage: str, seconds: float) -> None:
        self.stages.append(StageTiming(stage=stage, seconds=seconds))

    def to_dict(self) -> dict:
        return {
            "stages": [stage.to_dict() for stage in self.stages],
            "total_seconds": round(self.total_seconds, 3),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_text_report(self) -> str:
        lines = ["Benchmark:"]
        for stage in self.stages:
            pct = (stage.seconds / self.total_seconds * 100.0) if self.total_seconds > 0 else 0.0
            lines.append(f"  {stage.stage:<22} {stage.seconds:8.2f}s  ({pct:5.1f}%)")
        lines.append(f"  {'TOTAL':<22} {self.total_seconds:8.2f}s")
        return "\n".join(lines)
