"""Input parsing and lightweight matching for film/drama projects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

from models.film_drama_project import FootageTake, ScriptScene, StoryboardPanel
from utils.constants import SUPPORTED_VIDEO_EXTENSIONS

_SCENE_HEADING = re.compile(
    r"^(?:(?:INT|EXT|INT/EXT|I/E)\.?\s+|(?:씬|장면)\s*)(.*)$",
    re.IGNORECASE,
)
_NUMBERED_HEADING = re.compile(r"^(?:scene\s*)?(\d+)[\s.:-]", re.IGNORECASE)
_SCENE_TOKEN = re.compile(r"(?:scene|씬|sc|s)[_\-\s.]*(\d+)", re.IGNORECASE)
_TAKE_TOKEN = re.compile(r"(?:take|tk|테이크|t)[_\-\s.]*(\d+)", re.IGNORECASE)
_PANEL_TOKEN = re.compile(r"(?:panel|컷|p)[_\-\s.]*(\d+)", re.IGNORECASE)
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


class FilmDramaService:
    """Parses optional screenplay/planning inputs and scene/take filenames."""

    @staticmethod
    def read_text_file(path: Path) -> str:
        """Read a user-provided screenplay or shooting-plan text file."""
        return path.read_text(encoding="utf-8-sig")

    def parse_script(self, path: Path) -> List[ScriptScene]:
        text = self.read_text_file(path)
        scenes: List[ScriptScene] = []
        current: ScriptScene | None = None
        generated_number = 1

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _SCENE_HEADING.match(line) or _NUMBERED_HEADING.match(line)
            if match:
                numbered_heading = _NUMBERED_HEADING.match(line)
                explicit_number = numbered_heading.group(1) if numbered_heading else None
                number = explicit_number or str(generated_number)
                generated_number += 1
                if current is not None:
                    scenes.append(current)
                current = ScriptScene(scene_number=number, heading=line)
            elif current is not None:
                current.text = f"{current.text}\n{line}".strip()

        if current is not None:
            scenes.append(current)
        return scenes

    def scan_storyboard(self, folder: Path) -> List[StoryboardPanel]:
        return [
            StoryboardPanel(
                file_path=path,
                scene_number=self._find_token(path.stem, _SCENE_TOKEN),
                panel_number=self._find_token(path.stem, _PANEL_TOKEN),
            )
            for path in self._iter_files(folder, _IMAGE_EXTENSIONS)
        ]

    def scan_footage(self, folder: Path) -> List[FootageTake]:
        return [
            FootageTake(
                file_path=path,
                scene_number=self._find_token(path.stem, _SCENE_TOKEN),
                take_number=self._find_token(path.stem, _TAKE_TOKEN),
            )
            for path in self._iter_files(folder, SUPPORTED_VIDEO_EXTENSIONS)
        ]

    @staticmethod
    def _iter_files(folder: Path, extensions: Iterable[str]) -> List[Path]:
        allowed = {extension.lower() for extension in extensions}
        return sorted(
            (path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in allowed),
            key=lambda path: str(path).lower(),
        )

    @staticmethod
    def _find_token(value: str, pattern: re.Pattern[str]) -> str | None:
        match = pattern.search(value)
        return match.group(1) if match else None
