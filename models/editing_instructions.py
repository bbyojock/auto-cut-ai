"""Free-text editing instructions (Version 4.5, Feature 1).

An :class:`EditingInstruction` is one sentence the user typed in the
"Editing Instructions" panel, e.g. "Never remove kills." or "Make the
pacing faster." ``scope`` distinguishes a *temporary* instruction ("Make
THIS video faster") from a *persistent* one ("Always keep clutch
moments.") -- see :mod:`services.editing_instructions_service` for the
classification logic (Feature 13). Only ``scope == "persistent"``
instructions should ever influence a *different* video later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

INSTRUCTION_SCOPE_VIDEO = "video"          # this project/video only
INSTRUCTION_SCOPE_PERSISTENT = "persistent"  # should influence future videos too


@dataclass
class EditingInstruction:
    text: str
    scope: str = INSTRUCTION_SCOPE_VIDEO
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "scope": self.scope,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditingInstruction":
        try:
            created_at = datetime.fromisoformat(data.get("created_at", ""))
        except ValueError:
            created_at = datetime.now()
        return cls(
            text=str(data["text"]),
            scope=str(data.get("scope", INSTRUCTION_SCOPE_VIDEO)),
            enabled=bool(data.get("enabled", True)),
            created_at=created_at,
        )


@dataclass
class EditingInstructionSet:
    """Every instruction entered for one project, plus the persistent ones."""

    instructions: List[EditingInstruction] = field(default_factory=list)

    def add(self, text: str, scope: str = INSTRUCTION_SCOPE_VIDEO) -> EditingInstruction:
        instruction = EditingInstruction(text=text.strip(), scope=scope)
        self.instructions.append(instruction)
        return instruction

    def enabled_instructions(self) -> List[EditingInstruction]:
        return [i for i in self.instructions if i.enabled and i.text.strip()]

    def persistent_instructions(self) -> List[EditingInstruction]:
        return [i for i in self.enabled_instructions() if i.scope == INSTRUCTION_SCOPE_PERSISTENT]

    def video_instructions(self) -> List[EditingInstruction]:
        return [i for i in self.enabled_instructions() if i.scope == INSTRUCTION_SCOPE_VIDEO]

    def to_prompt_text(self) -> str:
        """Render as a prompt block, this-video instructions listed after
        (so they visually/positionally win over) persistent ones -- see
        Feature 16 priority order."""
        lines: List[str] = []
        persistent = self.persistent_instructions()
        video_only = self.video_instructions()
        if persistent:
            lines.append("Standing user preferences (apply unless overridden below):")
            lines.extend(f"  - {i.text}" for i in persistent)
        if video_only:
            lines.append("Instructions for THIS video specifically (these take priority):")
            lines.extend(f"  - {i.text}" for i in video_only)
        return "\n".join(lines)

    def fingerprint(self) -> str:
        import hashlib

        payload = "|".join(f"{i.scope}:{i.text}" for i in self.enabled_instructions())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"instructions": [i.to_dict() for i in self.instructions]}

    @classmethod
    def from_dict(cls, data: dict) -> "EditingInstructionSet":
        return cls(instructions=[EditingInstruction.from_dict(i) for i in data.get("instructions", [])])
