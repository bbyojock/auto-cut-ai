"""Video project model: the source file plus its detected metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class VideoProject:
    """A single imported video and everything known about it so far.

    Instances are created empty (metadata fields ``None``) by
    :class:`video.video_importer.VideoImporter` and then filled in during
    import. Keeping this model separate from the transcript/frame data
    (see :class:`models.analysis_result.AnalysisResult`) means a
    ``VideoProject`` always answers exactly one question: "what is this
    video file?".
    """

    file_path: Path
    imported_at: datetime = field(default_factory=datetime.now)

    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    total_frames: Optional[int] = None

    # Timestamp-pipeline consistency fix: ffprobe-derived stream timing,
    # used to keep Whisper/frame timestamps on the video's own absolute
    # timeline. All default to "unknown"/"no correction needed" so any
    # pre-existing VideoProject (e.g. loaded from an old cache file via
    # from_dict) behaves exactly as it did before this fix.
    video_start_time_seconds: float = 0.0
    audio_start_time_seconds: Optional[float] = None
    is_vfr: bool = False

    # Resolve-XML-export fix: the source file's own EMBEDDED starting
    # timecode (the "tmcd"/timecode metadata track many screen recorders
    # and capture cards write), expressed in seconds. This is NOT the
    # same thing as video_start_time_seconds above (that's the
    # container's PTS start_time, almost always 0.0) -- a file can very
    # commonly have start_time=0.0 *and* an embedded timecode of
    # 01:00:00:00 at the same time. None means "no embedded timecode
    # found" (equivalent to 0.0/00:00:00:00).
    source_timecode_seconds: Optional[float] = None

    @property
    def file_name(self) -> str:
        """The video's file name, e.g. ``interview.mp4``."""
        return self.file_path.name

    @property
    def extension(self) -> str:
        """The lowercase file extension, e.g. ``.mp4``."""
        return self.file_path.suffix.lower()

    @property
    def is_analyzed(self) -> bool:
        """Whether metadata detection has already populated this project."""
        return self.duration_seconds is not None and self.fps is not None

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary (logging/debugging use only)."""
        return {
            "file_path": str(self.file_path),
            "file_name": self.file_name,
            "imported_at": self.imported_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "total_frames": self.total_frames,
            "video_start_time_seconds": self.video_start_time_seconds,
            "audio_start_time_seconds": self.audio_start_time_seconds,
            "is_vfr": self.is_vfr,
            "source_timecode_seconds": self.source_timecode_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VideoProject":
        """Reconstruct a :class:`VideoProject` from :meth:`to_dict`'s output.

        Used only by the Version 4 EditPlan cache/save-load services --
        never by the live analysis pipeline, which always builds a fresh
        ``VideoProject`` via :class:`video.video_importer.VideoImporter`.
        """
        imported_at_raw = data.get("imported_at")
        try:
            imported_at = datetime.fromisoformat(imported_at_raw) if imported_at_raw else datetime.now()
        except ValueError:
            imported_at = datetime.now()
        return cls(
            file_path=Path(data["file_path"]),
            imported_at=imported_at,
            duration_seconds=data.get("duration_seconds"),
            fps=data.get("fps"),
            width=data.get("width"),
            height=data.get("height"),
            total_frames=data.get("total_frames"),
            video_start_time_seconds=data.get("video_start_time_seconds", 0.0) or 0.0,
            audio_start_time_seconds=data.get("audio_start_time_seconds"),
            is_vfr=bool(data.get("is_vfr", False)),
            source_timecode_seconds=data.get("source_timecode_seconds"),
        )
