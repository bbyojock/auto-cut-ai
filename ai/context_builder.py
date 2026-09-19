"""Converts an AnalysisResult into a compact, token-efficient AI context."""

from __future__ import annotations

import logging
from typing import List, Optional

from ai.event_recognizer import EventRecognizer
from models.analysis_result import AnalysisResult
from models.detected_event import DetectedEvent
from models.transcript import TranscriptSegment
from models.visual_activity import VisualActivityTimeline
from services.logging_service import LoggingService

# Two consecutive transcript segments whose gap is smaller than this are
# treated as one continuous utterance arbitrarily split by Whisper's VAD,
# and merged into a single context entry (Version 4, Feature 4: prompt
# optimization). This is deliberately tiny -- far below any real pause a
# human speaker takes -- so merging only ever removes redundant JSON
# structure, never a genuine candidate cut point the AI editing brain
# would otherwise have been able to use.
_MERGE_GAP_THRESHOLD_SECONDS = 0.05

# Version 4.6, Feature 8: motion is bucketed into a few named levels rather
# than sent as raw floats, both to save tokens and because the AI reasons
# better over "high/medium/low" than over "0.37" vs "0.41".
_MOTION_BUCKET_THRESHOLDS = (("low", 0.15), ("medium", 0.35), ("high", 1.01))


def _motion_bucket(score: float) -> str:
    for label, ceiling in _MOTION_BUCKET_THRESHOLDS:
        if score < ceiling:
            return label
    return "high"


class ContextBuilder:
    """Builds the minimal structured data the AI editing brain actually needs.

    Version 3 sent the transcript only. Version 4.6 (Features 7/8) adds two
    more evidence sources, both derived from real signal (not invented):

    - ``act``: a coarse per-window *motion* level (low/medium/high) from
      :class:`models.visual_activity.VisualActivityTimeline` (Feature 8's
      adaptive frame sampling) -- the first non-transcript signal this
      pipeline has ever given the AI, and the direct fix for "the AI can't
      tell silent-but-important combat from silent-and-boring waiting"
      without this.
    - ``ev``: candidate events from :class:`ai.event_recognizer.EventRecognizer`
      (Feature 7), each honestly marked ``probable`` or ``confirmed``.

    Raw frame *images* are still never sent -- see the Version 3 rationale
    below, which continues to apply to pixel data specifically.
    """

    _ROUND_DIGITS = 2

    def __init__(
        self, event_recognizer: Optional[EventRecognizer] = None, logger: Optional[logging.Logger] = None,
    ) -> None:
        self._event_recognizer = event_recognizer or EventRecognizer()
        self._logger = logger or LoggingService.get_logger("ai.context_builder")

    def build(self, analysis: AnalysisResult, compact: bool = True) -> dict:
        """Return a dict ready to be embedded in the AI prompt.

        Args:
            analysis: The completed :class:`AnalysisResult` (Version 2's
                transcript + Version 4.6's optional visual activity).
            compact: If True (default), use single-letter keys and merge
                negligible-gap segments to minimize prompt size (Feature 4).
                If False, produce the original verbose shape.
        """
        segments = self._merge_negligible_gaps(analysis.transcript.segments) if compact else list(
            analysis.transcript.segments
        )

        duration = self._round(analysis.video.duration_seconds or 0.0)
        language = analysis.transcript.language

        events = self._event_recognizer.detect(analysis)
        activity_buckets = self._activity_buckets(analysis.visual_activity, duration) if analysis.visual_activity else []

        if compact:
            context = {
                "d": duration,
                "lang": language,
                "seg": [
                    {"s": self._round(seg.start_seconds), "e": self._round(seg.end_seconds), "t": seg.text}
                    for seg in segments
                ],
                "act": [{"s": self._round(a[0]), "e": self._round(a[1]), "m": a[2]} for a in activity_buckets],
                "ev": [
                    {"s": self._round(e.start_seconds), "e": self._round(e.end_seconds), "type": e.event_type, "conf": e.confidence}
                    for e in events
                ],
            }
        else:
            context = {
                "duration_seconds": duration,
                "language": language,
                "segment_count": len(segments),
                "segments": [
                    {"start": self._round(seg.start_seconds), "end": self._round(seg.end_seconds), "text": seg.text}
                    for seg in segments
                ],
                "visual_activity": [{"start": a[0], "end": a[1], "motion": a[2]} for a in activity_buckets],
                "detected_events": [e.to_dict() for e in events],
            }

        self._logger.info(
            "Context built: duration=%.1fs language=%s segments=%d activity_windows=%d events=%d (compact=%s, merged_from=%d)",
            duration, language, len(segments), len(activity_buckets), len(events), compact, len(analysis.transcript.segments),
        )
        return context

    @staticmethod
    def _activity_buckets(activity: VisualActivityTimeline, duration: float):
        """Merge consecutive same-bucket windows into fewer, larger entries (token savings)."""
        if not activity.segments:
            return []
        buckets = []
        for window in sorted(activity.segments, key=lambda w: w.start_seconds):
            label = _motion_bucket(window.motion_score)
            if buckets and buckets[-1][2] == label and window.start_seconds - buckets[-1][1] <= 0.5:
                buckets[-1] = (buckets[-1][0], window.end_seconds, label)
            else:
                buckets.append((window.start_seconds, window.end_seconds, label))
        return buckets

    def _merge_negligible_gaps(self, segments: List[TranscriptSegment]) -> List[TranscriptSegment]:
        """Merge consecutive segments separated by a negligible gap.

        Preserves every word of text (concatenated) and both outer
        timestamps -- nothing the AI could use as a genuine cut point is
        discarded, only redundant per-segment JSON structure.
        """
        if not segments:
            return []

        ordered = sorted(segments, key=lambda seg: seg.start_seconds)
        merged: List[TranscriptSegment] = [ordered[0]]

        for current in ordered[1:]:
            previous = merged[-1]
            gap = current.start_seconds - previous.end_seconds
            if gap <= _MERGE_GAP_THRESHOLD_SECONDS and gap >= -_MERGE_GAP_THRESHOLD_SECONDS:
                merged[-1] = TranscriptSegment(
                    start_seconds=previous.start_seconds,
                    end_seconds=max(previous.end_seconds, current.end_seconds),
                    text=f"{previous.text} {current.text}".strip(),
                )
            else:
                merged.append(current)

        return merged

    @staticmethod
    def compact_schema_legend() -> str:
        """Short legend describing the compact JSON schema, for the prompt.

        Kept here (next to the code that actually produces the shape)
        rather than in :class:`ai.prompt_templates.PromptTemplates`, so the
        two can never drift out of sync.
        """
        return (
            "JSON keys: d=duration_seconds, lang=language, seg=transcript segments (each: "
            "s=start_seconds, e=end_seconds, t=text); act=visual motion windows (each: "
            "s=start_seconds, e=end_seconds, m=motion level 'low'/'medium'/'high' -- real "
            "frame-to-frame pixel difference, not a guess); ev=candidate detected events "
            "(each: s=start_seconds, e=end_seconds, type=event type, conf='probable' or "
            "'confirmed' -- never treat a 'probable' event as certain)."
        )

    def _round(self, value: float) -> float:
        return round(value, self._ROUND_DIGITS)
