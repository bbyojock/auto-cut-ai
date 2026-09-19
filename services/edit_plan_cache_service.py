"""Caches AnalysisResult + EditPlan (+ prompt/metadata) per source video.

Re-running Whisper transcription and an AI edit-planning request every time
the *same* video is reopened wastes both processing time and API cost.
:class:`EditPlanCacheService` keys a cache entry off the video file's path,
size, and modification time plus every setting that could change the
result (Whisper backend/model, frame interval, prompt style, target
length) -- so the cache is used whenever it's genuinely still valid, and
silently bypassed (never served stale/wrong data) the moment any of those
inputs change, including the video file itself being re-exported/edited.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from models.analysis_result import AnalysisResult
from models.edit_plan import EditPlan
from services.logging_service import LoggingService
from utils.file_utils import ensure_directory, get_project_root

_CACHE_DIR_NAME = "cache"  # excluded from shipped ZIPs -- see Version 4 packaging rules
_CACHE_SUBDIR = "edit_plans"
_ANALYSIS_SUBDIR = "analysis"
_CACHE_FORMAT_VERSION = 1
_ANALYSIS_CACHE_FORMAT_VERSION = 1


@dataclass(slots=True)
class EditPlanCacheEntry:
    """A cache hit: everything needed to skip Whisper + the AI request entirely."""

    analysis: AnalysisResult
    plan: EditPlan
    prompt_text: str
    cached_at: str


class EditPlanCacheService:
    """Reads/writes one cache entry per (video, settings) combination."""

    def __init__(self, cache_root: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        self._cache_root = cache_root or (get_project_root() / _CACHE_DIR_NAME / _CACHE_SUBDIR)
        self._analysis_root = (cache_root or (get_project_root() / _CACHE_DIR_NAME)) / _ANALYSIS_SUBDIR
        self._logger = logger or LoggingService.get_logger("services.edit_plan_cache")

    # ------------------------------------------------------------------
    # Version 4.5 (Feature 12 fix): analysis cache, independent of the AI
    # editing brain's inputs (rules/instructions/style/target length).
    # Splitting this out from the combined plan cache below means changing
    # ONLY the editing instructions, style, game profile, or personal
    # preferences never re-triggers Whisper transcription or frame
    # extraction -- exactly "if only the editing instructions change, do
    # not rerun Whisper" from the spec. The old combined `cache_key`/`get`/
    # `save` methods are kept below, unchanged, for any caller that still
    # wants one-shot combined caching.
    # ------------------------------------------------------------------
    def analysis_cache_key(
        self, video_path: Path, whisper_backend: str, whisper_model: str, frame_interval_seconds: float,
    ) -> str:
        """Deterministic key covering only what actually affects Whisper/frame extraction."""
        try:
            stat = video_path.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = -1, -1.0

        payload = "|".join(
            str(part)
            for part in (
                _ANALYSIS_CACHE_FORMAT_VERSION,
                str(video_path.resolve()),
                size,
                mtime,
                whisper_backend,
                whisper_model,
                round(frame_interval_seconds, 3),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get_analysis(self, key: str) -> Optional[AnalysisResult]:
        path = self._analysis_root / key / "analysis.json"
        if not path.exists():
            return None
        try:
            analysis = AnalysisResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            self._logger.warning("Analysis cache entry '%s' is corrupt (%s); treating as a miss.", key, exc)
            return None
        self._logger.info("Analysis cache HIT for key=%s (Whisper/frame extraction skipped).", key)
        return analysis

    def save_analysis(self, key: str, analysis: AnalysisResult) -> None:
        try:
            entry_dir = ensure_directory(self._analysis_root / key)
            (entry_dir / "analysis.json").write_text(
                json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._logger.info("Analysis cache SAVE for key=%s", key)
        except OSError:
            self._logger.exception("Failed to write analysis cache entry '%s' (non-fatal).", key)

    def plan_cache_key(
        self,
        analysis_key: str,
        style: str,
        target_length_seconds: Optional[float],
        rules_fingerprint: str = "",
        instructions_fingerprint: str = "",
        game_profile: str = "",
    ) -> str:
        """Deterministic key covering everything that affects the AI editing brain's output.

        Depends on ``analysis_key`` (so a changed video/Whisper setting
        still invalidates this too) plus every AI-side input: style,
        target length, active rules (game profile + personal preferences,
        via ``rules_fingerprint``), and free-text editing instructions
        (``instructions_fingerprint``). Changing *only* one of these no
        longer re-runs Whisper -- only the AI request is repeated.
        """
        payload = "|".join(
            str(part)
            for part in (
                _CACHE_FORMAT_VERSION,
                analysis_key,
                style,
                target_length_seconds if target_length_seconds is not None else "auto",
                rules_fingerprint,
                instructions_fingerprint,
                game_profile,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get_plan(self, key: str) -> Optional["EditPlanCacheEntry"]:
        return self.get(key)

    def save_plan(self, key: str, analysis: AnalysisResult, plan: EditPlan, prompt_text: str) -> None:
        self.save(key, analysis, plan, prompt_text)

    # ------------------------------------------------------------------
    # Pre-4.5 combined cache (kept for backward compatibility)
    # ------------------------------------------------------------------
    def cache_key(
        self,
        video_path: Path,
        whisper_backend: str,
        whisper_model: str,
        frame_interval_seconds: float,
        prompt_style: str,
        target_length_seconds: Optional[float],
    ) -> str:
        """Deterministic key: identical inputs always produce the same key,
        and *any* changed input (including the video file itself being
        modified) produces a different one -- this is the entire cache
        invalidation mechanism, no separate "is this still valid?" check
        is needed.
        """
        try:
            stat = video_path.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = -1, -1.0

        payload = "|".join(
            str(part)
            for part in (
                _CACHE_FORMAT_VERSION,
                str(video_path.resolve()),
                size,
                mtime,
                whisper_backend,
                whisper_model,
                round(frame_interval_seconds, 3),
                prompt_style,
                target_length_seconds if target_length_seconds is not None else "auto",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> Optional[EditPlanCacheEntry]:
        """Return the cached entry for ``key``, or ``None`` on any miss/corruption."""
        entry_dir = self._cache_root / key
        analysis_path = entry_dir / "analysis.json"
        plan_path = entry_dir / "plan.json"
        meta_path = entry_dir / "meta.json"
        prompt_path = entry_dir / "prompt.txt"

        if not (analysis_path.exists() and plan_path.exists()):
            return None

        try:
            analysis = AnalysisResult.from_dict(json.loads(analysis_path.read_text(encoding="utf-8")))
            plan = EditPlan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
            prompt_text = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            self._logger.warning("Cache entry '%s' is corrupt (%s); ignoring and treating as a miss.", key, exc)
            return None

        self._logger.info("Cache HIT for key=%s (cached_at=%s)", key, meta.get("cached_at", "unknown"))
        return EditPlanCacheEntry(
            analysis=analysis, plan=plan, prompt_text=prompt_text, cached_at=meta.get("cached_at", "")
        )

    def save(self, key: str, analysis: AnalysisResult, plan: EditPlan, prompt_text: str) -> None:
        """Persist ``analysis``/``plan``/``prompt_text`` under ``key``. Never raises --
        a failed cache write should never break an otherwise-successful generation.
        """
        entry_dir = self._cache_root / key
        try:
            ensure_directory(entry_dir)
            (entry_dir / "analysis.json").write_text(
                json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (entry_dir / "plan.json").write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (entry_dir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
            from datetime import datetime

            (entry_dir / "meta.json").write_text(
                json.dumps({"cached_at": datetime.now().isoformat(), "key": key}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._logger.info("Cache SAVE for key=%s", key)
        except OSError:
            self._logger.exception("Failed to write cache entry '%s' (non-fatal).", key)

    def clear_all(self) -> None:
        """Delete every cached entry. Never raises."""
        try:
            if self._cache_root.exists():
                shutil.rmtree(self._cache_root)
            self._logger.info("Cache cleared.")
        except OSError:
            self._logger.exception("Failed to clear cache (non-fatal).")
