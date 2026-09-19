"""EditPlan revision history models (Version 4.5, Feature 3).

Every time an EditPlan is modified (via the EditPlan Chat, Feature 2, or
any other future editing action), a new :class:`EditPlanRevision` is
appended to a project's :class:`EditPlanHistory`. Revisions are stored as
full plan snapshots (simplest possible correct implementation -- EditPlans
are small JSON, so the cost of not diffing is negligible) plus a short,
human-readable ``change_summary`` describing what changed and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from models.edit_plan import EditPlan


@dataclass
class EditPlanRevision:
    plan: EditPlan
    change_summary: str
    source: str = "chat"  # "initial" | "chat" | "manual"
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "plan": self.plan.to_dict(),
            "change_summary": self.change_summary,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlanRevision":
        try:
            created_at = datetime.fromisoformat(data.get("created_at", ""))
        except ValueError:
            created_at = datetime.now()
        return cls(
            plan=EditPlan.from_dict(data["plan"]),
            change_summary=str(data.get("change_summary", "")),
            source=str(data.get("source", "chat")),
            created_at=created_at,
        )


@dataclass
class EditPlanDiffEntry:
    """One human-readable difference between two revisions, for the "Compare revisions" view."""

    start_seconds: float
    end_seconds: float
    before_action: Optional[str]
    after_action: Optional[str]
    before_reason: Optional[str]
    after_reason: Optional[str]
    before_speed_factor: Optional[float] = None
    after_speed_factor: Optional[float] = None

    def describe(self) -> str:
        def fmt(seconds: float) -> str:
            minutes, secs = divmod(max(0.0, seconds), 60)
            return f"{int(minutes):02d}:{secs:05.2f}"

        def label(action: Optional[str], speed: Optional[float]) -> str:
            if action is None:
                return "?"
            if action == "compress" and speed:
                return f"COMPRESS {speed:.1f}x"
            return action.upper()

        rng = f"{fmt(self.start_seconds)} -> {fmt(self.end_seconds)}"
        if self.before_action is None:
            return f"{rng}: added as {label(self.after_action, self.after_speed_factor)} ({self.after_reason})"
        if self.after_action is None:
            return f"{rng}: removed from plan (was {label(self.before_action, self.before_speed_factor)})"
        if self.before_action != self.after_action or self.before_speed_factor != self.after_speed_factor:
            return (
                f"{rng}: {label(self.before_action, self.before_speed_factor)} -> "
                f"{label(self.after_action, self.after_speed_factor)} ({self.after_reason})"
            )
        return f"{rng}: reason updated -- \"{self.before_reason}\" -> \"{self.after_reason}\""


@dataclass
class EditPlanHistory:
    """A per-project undo/redo stack of :class:`EditPlanRevision` snapshots."""

    revisions: List[EditPlanRevision] = field(default_factory=list)
    cursor: int = -1  # index of the currently active revision within `revisions`

    def push_initial(self, plan: EditPlan, source: str = "initial") -> EditPlanRevision:
        self.revisions = [EditPlanRevision(plan=plan, change_summary="Initial EditPlan generated.", source=source)]
        self.cursor = 0
        return self.revisions[0]

    def push(self, plan: EditPlan, change_summary: str, source: str = "chat") -> EditPlanRevision:
        """Append a new revision. Any "future" (redo) revisions are discarded,
        matching standard undo/redo semantics."""
        revision = EditPlanRevision(plan=plan, change_summary=change_summary, source=source)
        self.revisions = self.revisions[: self.cursor + 1]
        self.revisions.append(revision)
        self.cursor = len(self.revisions) - 1
        return revision

    @property
    def current(self) -> Optional[EditPlanRevision]:
        if 0 <= self.cursor < len(self.revisions):
            return self.revisions[self.cursor]
        return None

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self.revisions) - 1

    def undo(self) -> Optional[EditPlanRevision]:
        if not self.can_undo:
            return None
        self.cursor -= 1
        return self.current

    def redo(self) -> Optional[EditPlanRevision]:
        if not self.can_redo:
            return None
        self.cursor += 1
        return self.current

    def restore(self, index: int) -> Optional[EditPlanRevision]:
        if 0 <= index < len(self.revisions):
            self.cursor = index
            return self.current
        return None

    def compare(self, index_a: int, index_b: int) -> List[EditPlanDiffEntry]:
        """Segment-level differences between two revisions, keyed by (start, end)."""
        if not (0 <= index_a < len(self.revisions)) or not (0 <= index_b < len(self.revisions)):
            return []
        plan_a = self.revisions[index_a].plan
        plan_b = self.revisions[index_b].plan

        segments_a = {(round(s.start_seconds, 2), round(s.end_seconds, 2)): s for s in plan_a.all_segments_sorted}
        segments_b = {(round(s.start_seconds, 2), round(s.end_seconds, 2)): s for s in plan_b.all_segments_sorted}

        diffs: List[EditPlanDiffEntry] = []
        for key in sorted(set(segments_a) | set(segments_b)):
            seg_a = segments_a.get(key)
            seg_b = segments_b.get(key)
            if seg_a is not None and seg_b is not None:
                if (
                    seg_a.action == seg_b.action and seg_a.reason == seg_b.reason
                    and seg_a.compression_speed_factor == seg_b.compression_speed_factor
                ):
                    continue
                diffs.append(
                    EditPlanDiffEntry(
                        start_seconds=key[0], end_seconds=key[1],
                        before_action=seg_a.action, after_action=seg_b.action,
                        before_reason=seg_a.reason, after_reason=seg_b.reason,
                        before_speed_factor=seg_a.compression_speed_factor, after_speed_factor=seg_b.compression_speed_factor,
                    )
                )
            elif seg_a is None and seg_b is not None:
                diffs.append(
                    EditPlanDiffEntry(
                        start_seconds=key[0], end_seconds=key[1],
                        before_action=None, after_action=seg_b.action,
                        before_reason=None, after_reason=seg_b.reason,
                        after_speed_factor=seg_b.compression_speed_factor,
                    )
                )
            elif seg_a is not None and seg_b is None:
                diffs.append(
                    EditPlanDiffEntry(
                        start_seconds=key[0], end_seconds=key[1],
                        before_action=seg_a.action, after_action=None,
                        before_reason=seg_a.reason, after_reason=None,
                        before_speed_factor=seg_a.compression_speed_factor,
                    )
                )
        return diffs

    def to_dict(self) -> dict:
        return {"revisions": [r.to_dict() for r in self.revisions], "cursor": self.cursor}

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlanHistory":
        return cls(
            revisions=[EditPlanRevision.from_dict(r) for r in data.get("revisions", [])],
            cursor=int(data.get("cursor", -1)),
        )
