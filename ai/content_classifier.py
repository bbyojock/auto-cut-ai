"""Heuristic raw-footage content-type classifier (Version 5.3).

Added alongside folder-based batch import so an unattended "drop a folder
of footage and analyze it" flow (see
:class:`services.folder_analysis_service.FolderAnalysisService`) can pick
a sensible default :mod:`ai.game_profiles` profile -- documentary or
variety/entertainment -- without the user having to tag every clip by
hand first.

This is deliberately a cheap, explainable heuristic over signal AutoCutAI
already computes for every clip (transcript pacing from Version 2, visual
motion from Version 4.6's adaptive frame sampling) -- not a trained
classifier and not a vision-language model call. It is good enough to
choose a starting profile; it never overrides a profile the user picked
by hand, and a user can always change the detected profile afterwards
exactly like any other :mod:`ai.game_profiles` selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from models.analysis_result import AnalysisResult

CONTENT_TYPE_DOCUMENTARY = "documentary"
CONTENT_TYPE_VARIETY = "variety"
CONTENT_TYPE_UNKNOWN = "unknown"

# Cheap text-based proxy for "this line was said with energy/laughter" --
# real emotion/laughter detection would need an audio classifier this
# project doesn't have, but Korean variety-show transcripts are reliably
# full of these markers, and exclamation marks work as a rough
# language-agnostic fallback.
_REACTION_MARKERS = ("ㅋㅋ", "ㅎㅎ", "하하", "히히", "크크", "!")


@dataclass(slots=True)
class ContentTypeGuess:
    """One clip's best-effort content-type guess, with the reasoning shown."""

    content_type: str
    confidence: float  # 0.0 (a coin flip) .. 1.0 (every signal agreed)
    reasons: List[str] = field(default_factory=list)


def _segment_stats(result: AnalysisResult) -> Dict[str, float]:
    segments = result.transcript.segments
    duration = result.video.duration_seconds or 0.0
    if not segments or duration <= 0:
        return {"avg_segment_seconds": 0.0, "segments_per_minute": 0.0, "reaction_ratio": 0.0}

    total_speech = sum(s.duration_seconds for s in segments)
    minutes = duration / 60.0
    reaction_hits = sum(1 for s in segments if any(marker in s.text for marker in _REACTION_MARKERS))

    return {
        "avg_segment_seconds": total_speech / len(segments),
        "segments_per_minute": (len(segments) / minutes) if minutes > 0 else 0.0,
        "reaction_ratio": reaction_hits / len(segments),
    }


def _motion_stats(result: AnalysisResult) -> Dict[str, float]:
    activity = result.visual_activity
    duration = result.video.duration_seconds or 0.0
    if activity is None or not activity.segments or duration <= 0:
        return {"avg_motion": 0.0, "scene_changes_per_minute": 0.0}

    minutes = duration / 60.0
    scene_changes = sum(1 for s in activity.segments if s.is_scene_change)
    return {
        "avg_motion": sum(s.motion_score for s in activity.segments) / len(activity.segments),
        "scene_changes_per_minute": (scene_changes / minutes) if minutes > 0 else 0.0,
    }


def classify_content_type(result: AnalysisResult) -> ContentTypeGuess:
    """Guess whether ``result`` looks like documentary or variety-show footage.

    Signals used (all already produced by earlier pipeline stages):

    - **Speech pacing** -- long, sparse narration/interview segments lean
      documentary; short, frequent, exclamation-heavy bursts lean variety.
    - **Visual motion** -- long static/handheld continuous shots with few
      scene changes lean documentary; frequent scene changes and high
      average motion lean variety (multicam switches, reaction shots).
    - **Raw take length** -- a single unedited take of 5+ minutes is far
      more typical of raw documentary footage than a variety-show feed.

    Returns :data:`CONTENT_TYPE_UNKNOWN` when there isn't enough signal
    either way (e.g. a near-silent clip with no visual activity data),
    which callers should treat as "leave the profile as-is".
    """
    seg = _segment_stats(result)
    mot = _motion_stats(result)
    reasons: List[str] = []
    documentary_score = 0.0
    variety_score = 0.0

    if seg["avg_segment_seconds"] >= 6.0:
        documentary_score += 2.0
        reasons.append("Long, continuous narration/interview segments")
    elif 0 < seg["avg_segment_seconds"] <= 3.0:
        variety_score += 1.5
        reasons.append("Short, rapid-fire speech bursts")

    if seg["segments_per_minute"] >= 12:
        variety_score += 1.5
        reasons.append("Frequent speaker turns")
    elif 0 < seg["segments_per_minute"] <= 6:
        documentary_score += 1.0
        reasons.append("Sparse, unhurried speech pacing")

    if seg["reaction_ratio"] >= 0.2:
        variety_score += 2.0
        reasons.append("High rate of exclamations/laughter in speech")

    if mot["scene_changes_per_minute"] >= 8:
        variety_score += 1.5
        reasons.append("Frequent visual scene changes")
    elif mot["scene_changes_per_minute"] <= 2:
        documentary_score += 1.0
        reasons.append("Long, mostly continuous shots")

    if mot["avg_motion"] >= 0.5:
        variety_score += 1.0
    elif 0 < mot["avg_motion"] <= 0.2:
        documentary_score += 0.5

    if (result.video.duration_seconds or 0.0) >= 300:
        documentary_score += 0.5
        reasons.append("Long unedited take (5+ min), typical of raw documentary footage")

    total = documentary_score + variety_score
    if total <= 0:
        return ContentTypeGuess(CONTENT_TYPE_UNKNOWN, 0.0, ["Not enough signal to classify"])
    if documentary_score == variety_score:
        return ContentTypeGuess(CONTENT_TYPE_UNKNOWN, 0.0, ["Documentary and variety signals tied"])

    if documentary_score > variety_score:
        return ContentTypeGuess(CONTENT_TYPE_DOCUMENTARY, round((documentary_score - variety_score) / total, 2), reasons)
    return ContentTypeGuess(CONTENT_TYPE_VARIETY, round((variety_score - documentary_score) / total, 2), reasons)


def dominant_content_type(guesses: List[ContentTypeGuess]) -> str:
    """Majority vote across a folder's clips (ties/no-signal -> unknown)."""
    counts = {CONTENT_TYPE_DOCUMENTARY: 0, CONTENT_TYPE_VARIETY: 0}
    for guess in guesses:
        if guess.content_type in counts:
            counts[guess.content_type] += 1

    if counts[CONTENT_TYPE_DOCUMENTARY] == counts[CONTENT_TYPE_VARIETY]:
        return CONTENT_TYPE_UNKNOWN
    return max(counts, key=counts.get)


def recommended_game_profile(content_type: str) -> str:
    """The :mod:`ai.game_profiles` id to pre-select for a detected content type."""
    # Local import: game_profiles doesn't need to know about this module,
    # only the other way around, and this avoids any import-order coupling.
    from ai.game_profiles import GAME_PROFILE_DOCUMENTARY, GAME_PROFILE_NONE, GAME_PROFILE_VARIETY

    return {
        CONTENT_TYPE_DOCUMENTARY: GAME_PROFILE_DOCUMENTARY,
        CONTENT_TYPE_VARIETY: GAME_PROFILE_VARIETY,
    }.get(content_type, GAME_PROFILE_NONE)
