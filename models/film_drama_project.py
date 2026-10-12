"""Structured data for screenplay-driven film and drama editing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass(slots=True)
class ScriptScene:
    """One scene heading and its dialogue/action text."""

    scene_number: str
    heading: str
    text: str = ""


@dataclass(slots=True)
class StoryboardPanel:
    """A storyboard image optionally associated with a scene."""

    file_path: Path
    scene_number: Optional[str] = None
    panel_number: Optional[str] = None


@dataclass(slots=True)
class FootageTake:
    """A raw footage file and the scene/take identifiers inferred from its name."""

    file_path: Path
    scene_number: Optional[str] = None
    take_number: Optional[str] = None


@dataclass(slots=True)
class FilmDramaProject:
    """The analyzed inputs for a screenplay-driven editing project."""

    name: str
    script_path: Path
    storyboard_path: Path
    footage_path: Path
    scenes: List[ScriptScene] = field(default_factory=list)
    storyboard_panels: List[StoryboardPanel] = field(default_factory=list)
    footage_takes: List[FootageTake] = field(default_factory=list)

    @property
    def matched_scene_numbers(self) -> List[str]:
        """Scene identifiers present in both the script and footage names."""
        script_numbers = {scene.scene_number for scene in self.scenes}
        footage_numbers = {take.scene_number for take in self.footage_takes if take.scene_number}
        return sorted(script_numbers & footage_numbers, key=_scene_sort_key)

    @property
    def unmatched_scene_numbers(self) -> List[str]:
        """Script scenes for which no scene-numbered footage was found."""
        footage_numbers = {take.scene_number for take in self.footage_takes if take.scene_number}
        return [
            scene.scene_number
            for scene in self.scenes
            if scene.scene_number not in footage_numbers
        ]


def _scene_sort_key(value: str) -> tuple[int, str]:
    try:
        return (0, f"{int(value):08d}")
    except ValueError:
        return (1, value.lower())
