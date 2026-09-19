"""Final output of the Version 2 analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.frame import FrameCollection
from models.transcript import Transcript
from models.video_project import VideoProject
from models.visual_activity import VisualActivityTimeline


@dataclass(slots=True)
class AnalysisResult:
    """Bundles every artifact produced while analyzing a single video.

    This is what :class:`video.analysis_pipeline.AnalysisPipeline` returns.
    Version 2 stops here on purpose: nothing edits the video, calls Gemini,
    or talks to DaVinci Resolve. This object is the clean, structured input
    later versions will build automated editing on top of.
    """

    video: VideoProject
    transcript: Transcript
    frames: FrameCollection
    audio_path: Optional[Path] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Version 4.6, Feature 8: real per-interval motion evidence from
    # adaptive frame sampling. Optional (``None``) for analysis produced
    # the old way (fixed-interval extraction, no motion data) so every
    # pre-4.6 cached AnalysisResult still loads correctly.
    visual_activity: Optional[VisualActivityTimeline] = None

    @property
    def processing_seconds(self) -> Optional[float]:
        """Wall-clock time the pipeline took to produce this result, if known."""
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "video": self.video.to_dict(),
            "transcript": self.transcript.to_dict(),
            "frames": self.frames.to_dict(),
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "processing_seconds": self.processing_seconds,
            "visual_activity": self.visual_activity.to_dict() if self.visual_activity is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisResult":
        """Reconstruct an :class:`AnalysisResult` from :meth:`to_dict`'s output.

        Used by :class:`services.edit_plan_cache_service.EditPlanCacheService`
        (Version 4, Feature 14) to skip re-running Whisper/frame extraction
        for a video that's already been analyzed. ``started_at``/
        ``finished_at`` aren't preserved by ``to_dict`` (only the derived
        ``processing_seconds`` is), so they're left ``None`` here; nothing
        downstream depends on them once analysis is already complete.
        """
        raw_activity = data.get("visual_activity")
        return cls(
            video=VideoProject.from_dict(data["video"]),
            transcript=Transcript.from_dict(data["transcript"]),
            frames=FrameCollection.from_dict(data["frames"]),
            audio_path=Path(data["audio_path"]) if data.get("audio_path") else None,
            visual_activity=VisualActivityTimeline.from_dict(raw_activity) if raw_activity else None,
        )
