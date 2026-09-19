"""Whisper transcript models.

A :class:`Transcript` is an ordered list of timestamped
:class:`TranscriptSegment` objects plus the auto-detected language, exactly
matching what :class:`video.transcriber.WhisperTranscriber` produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class TranscriptSegment:
    """One timestamped chunk of speech.

    Attributes:
        start_seconds: Segment start time in seconds (sub-second precision,
            matching faster-whisper's own output).
        end_seconds: Segment end time in seconds.
        text: The transcribed text for this segment, already stripped.
    """

    start_seconds: float
    end_seconds: float
    text: str

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds as ``MM:SS.mmm`` (e.g. ``00:01.220``)."""
        seconds = max(0.0, seconds)
        minutes, remainder = divmod(seconds, 60)
        return f"{int(minutes):02d}:{remainder:06.3f}"

    @property
    def start_timestamp(self) -> str:
        """Start time formatted as ``MM:SS.mmm``, e.g. ``00:01.220``."""
        return self._format_timestamp(self.start_seconds)

    @property
    def end_timestamp(self) -> str:
        """End time formatted as ``MM:SS.mmm``."""
        return self._format_timestamp(self.end_seconds)

    def to_dict(self) -> dict:
        return {
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptSegment":
        return cls(
            start_seconds=float(data["start_seconds"]),
            end_seconds=float(data["end_seconds"]),
            text=str(data.get("text", "")),
        )


@dataclass(slots=True)
class Transcript:
    """A full transcript: detected language plus every timestamped segment."""

    language: str
    language_probability: float
    segments: List[TranscriptSegment] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """The entire transcript as one plain-text string, no timestamps."""
        return " ".join(segment.text for segment in self.segments).strip()

    def to_formatted_text(self) -> str:
        """Render using the ``MM:SS.mmm`` / text block layout, e.g.::

            00:01.220
            Hello everyone.

            00:03.550
            Today we...
        """
        blocks = [f"{segment.start_timestamp}\n{segment.text}" for segment in self.segments]
        return "\n\n".join(blocks)

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "language_probability": self.language_probability,
            "segments": [segment.to_dict() for segment in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        return cls(
            language=str(data.get("language", "")),
            language_probability=float(data.get("language_probability", 0.0)),
            segments=[TranscriptSegment.from_dict(s) for s in data.get("segments", [])],
        )
