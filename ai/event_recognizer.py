"""Real event recognition from transcript + motion evidence (Version 4.6, Feature 7).

No vision model is available in this pipeline (Version 3's architecture is
deliberately text-only for cost reasons -- see :mod:`ai.context_builder`),
so this module does the honest thing given that constraint: it combines
keyword spotting against a controlled vocabulary (seeded with Bedwars,
per the bug report) with the *real* motion evidence from
:meth:`video.frame_extractor.FrameExtractor.extract_adaptive` (Feature 8),
and never claims more certainty than the evidence supports.

A keyword match makes an event ``"probable"`` unless the transcript states
it in a way that leaves no reasonable ambiguity (e.g. "the bed is
destroyed" for bed_destroyed), in which case it's ``"confirmed"``.
Motion-only detections (a big activity spike with no matching keyword
nearby) are always ``"probable"`` and labeled generically -- Feature 7 is
explicit that an unconfirmed event must never be presented as fact.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from models.analysis_result import AnalysisResult
from models.detected_event import DetectedEvent
from models.transcript import TranscriptSegment
from models.visual_activity import VisualActivityTimeline
from services.logging_service import LoggingService
from utils.constants import (
    EVENT_BED_DESTROYED,
    EVENT_BUILDING,
    EVENT_CLUTCH,
    EVENT_COMBAT,
    EVENT_CONFIDENCE_CONFIRMED,
    EVENT_CONFIDENCE_PROBABLE,
    EVENT_DEATH,
    EVENT_DEFEAT,
    EVENT_EXPLORATION,
    EVENT_FINAL_KILL,
    EVENT_INVENTORY,
    EVENT_ITEM_OBTAINED,
    EVENT_KILL,
    EVENT_NAVIGATION,
    EVENT_SHOP,
    EVENT_VICTORY,
    EVENT_WAITING,
)

# (event_type, [keyword/phrase, ...], is_unambiguous)
# `is_unambiguous=True` phrases are specific enough to call "confirmed"
# rather than merely "probable" when matched verbatim.
_BEDWARS_KEYWORDS = [
    (EVENT_BED_DESTROYED, ["bed is destroyed", "bed's destroyed", "broke the bed", "bed is broken", "no more bed"], True),
    (EVENT_FINAL_KILL, ["final kill", "that's game", "gg that's it", "match point kill"], True),
    (EVENT_VICTORY, ["we won", "victory royale", "gg we won", "we win"], True),
    (EVENT_DEFEAT, ["we lost", "gg we lost", "we died as a team", "game over for us"], True),
    (EVENT_CLUTCH, ["clutch", "1v2", "1v3", "one versus two", "one versus three", "comeback"], False),
    (EVENT_KILL, ["got the kill", "got him", "killed him", "killed her", "he's dead", "nice kill", "got a kill"], False),
    (EVENT_DEATH, ["i died", "i'm dead", "killed me", "i just died"], False),
    (EVENT_COMBAT, ["fighting", "in a fight", "pvp", "attacking", "let's fight"], False),
    (EVENT_ITEM_OBTAINED, ["got a diamond", "picked up", "got an emerald", "found a", "grabbed the"], False),
    (EVENT_SHOP, ["let me buy", "going shopping", "at the shop", "buying armor", "buying a"], False),
    (EVENT_INVENTORY, ["checking my inventory", "sorting my items", "organizing my inventory"], False),
    (EVENT_BUILDING, ["bridging", "building", "let's build", "placing blocks"], False),
    (EVENT_EXPLORATION, ["let's explore", "exploring", "checking this out", "let's go look"], False),
    (EVENT_WAITING, ["waiting for", "hold on", "loading"], False),
    (EVENT_NAVIGATION, ["heading to", "walking to", "running to", "let's go to", "collecting more resources", "gather some wood"], False),
]

# A big, unmatched motion spike (Feature 8 evidence) with no corroborating
# keyword still deserves a flag -- it's exactly the "silent but visually
# important" case that caused the reported bug (KEEP-worthy action with no
# speech to key off of).
_MOTION_SPIKE_THRESHOLD = 0.30
_MOTION_EVENT_LABEL = EVENT_COMBAT


class EventRecognizer:
    """Detects candidate Bedwars-style events from transcript + motion evidence."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or LoggingService.get_logger("ai.event_recognizer")

    def detect(self, analysis: AnalysisResult) -> List[DetectedEvent]:
        events: List[DetectedEvent] = []
        events.extend(self._detect_from_transcript(analysis.transcript.segments))
        if analysis.visual_activity is not None:
            events.extend(self._detect_from_motion(analysis.visual_activity, events))
        events.sort(key=lambda e: e.start_seconds)
        self._logger.info("Detected %d candidate event(s) (%d from keywords, motion-only included).", len(events), len(events))
        return events

    # ------------------------------------------------------------------
    def _detect_from_transcript(self, segments: List[TranscriptSegment]) -> List[DetectedEvent]:
        events: List[DetectedEvent] = []
        for segment in segments:
            lowered = segment.text.lower()
            for event_type, phrases, unambiguous in _BEDWARS_KEYWORDS:
                for phrase in phrases:
                    if phrase in lowered:
                        confidence = EVENT_CONFIDENCE_CONFIRMED if unambiguous else EVENT_CONFIDENCE_PROBABLE
                        events.append(
                            DetectedEvent(
                                start_seconds=segment.start_seconds, end_seconds=segment.end_seconds,
                                event_type=event_type, confidence=confidence,
                                evidence=f'transcript: "{segment.text.strip()}"',
                            )
                        )
                        break  # one match per event_type per segment is enough evidence
        return events

    def _detect_from_motion(
        self, activity: VisualActivityTimeline, existing_events: List[DetectedEvent],
    ) -> List[DetectedEvent]:
        events: List[DetectedEvent] = []
        for window in activity.segments:
            if window.motion_score < _MOTION_SPIKE_THRESHOLD:
                continue
            covered = any(
                e.start_seconds <= window.start_seconds < e.end_seconds
                or e.start_seconds < window.end_seconds <= e.end_seconds
                for e in existing_events
            )
            if covered:
                continue  # a keyword already explains this window -- don't double-report
            events.append(
                DetectedEvent(
                    start_seconds=window.start_seconds, end_seconds=window.end_seconds,
                    event_type=_MOTION_EVENT_LABEL, confidence=EVENT_CONFIDENCE_PROBABLE,
                    evidence=f"motion spike (score={window.motion_score:.2f}) with no matching speech",
                )
            )
        return events
