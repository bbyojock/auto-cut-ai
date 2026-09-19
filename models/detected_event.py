"""A single detected in-video event (Version 4.6, Feature 7)."""

from __future__ import annotations

from dataclasses import dataclass

from utils.constants import EVENT_CONFIDENCE_PROBABLE


@dataclass(slots=True)
class DetectedEvent:
    """One candidate event, from keyword and/or motion evidence.

    ``confidence`` is always ``"probable"`` unless the transcript states
    the event unambiguously (see :mod:`ai.event_recognizer`) -- Feature 7
    explicitly requires never presenting an inferred event as confirmed.
    """

    start_seconds: float
    end_seconds: float
    event_type: str
    confidence: str = EVENT_CONFIDENCE_PROBABLE
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "start": self.start_seconds,
            "end": self.end_seconds,
            "type": self.event_type,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DetectedEvent":
        return cls(
            start_seconds=float(data["start"]),
            end_seconds=float(data["end"]),
            event_type=str(data["type"]),
            confidence=str(data.get("confidence", EVENT_CONFIDENCE_PROBABLE)),
            evidence=str(data.get("evidence", "")),
        )
