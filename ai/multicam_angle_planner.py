"""Automatic multicam angle-switching decision engine (Version 5.4).

Version 5.3.1 grouped clips that were filmed at the same moment (see
:mod:`video.multicam_grouper`) but never decided which camera should
actually be on screen when. This module closes that gap: given a
:class:`~models.multicam_group.MulticamGroup` plus each camera's already
computed :class:`~models.analysis_result.AnalysisResult`, it produces an
:class:`~models.angle_switch_plan.AngleSwitchPlan` -- a full, contiguous,
timestamped decision of which camera plays at every point in the take.

Like :mod:`ai.content_classifier`, this is a heuristic over signal the
pipeline already computed (transcript timing/text, visual motion) -- not
an AI provider call. Nothing here has audio-level or actual face/subject
detection to work with, so the scoring is a readable, tunable proxy:

- **Speech coverage** in a time window -- a camera whose transcript has
  someone actively talking during that window is favored (most simply,
  a POV/handheld camera pointed at whoever is currently speaking tends
  to have the most complete pickup of their speech).
- **Reaction markers** (exclamations/laughter in the transcript) -- a
  strong signal for "cut to this camera, something happened here."
- **Visual motion** -- movement/scene changes, weighted lightly since
  motion alone (e.g. camera shake) isn't a reliable "something
  interesting is happening" signal on its own.
- **A small bonus for the take's primary/wide camera** (tag containing
  "거치", "fixed", "wide", or "main") as a tie-breaker, so the plan
  rests on the establishing shot rather than flickering between
  visually-similar POV angles when nothing else distinguishes them.

Hysteresis keeps the result editable rather than flickering: the engine
only switches away from the current camera when a challenger's score
clears it by a margin, and holds each cut for a minimum duration once
made -- except when the current camera's own footage runs out, which
always forces an immediate switch to whatever's available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ai.content_classifier import _REACTION_MARKERS  # reuse the same proxy signal
from models.analysis_result import AnalysisResult
from models.angle_switch_plan import AngleSegment, AngleSwitchPlan
from models.multicam_group import MulticamGroup

_PRIMARY_TAG_HINTS = ("거치", "fixed", "wide", "main")

# Tuning knobs, kept as module constants (not magic numbers buried in the
# scoring function) so a future version can expose them as settings
# without touching the scoring logic itself.
DEFAULT_BUCKET_SECONDS = 2.0
DEFAULT_MIN_SEGMENT_SECONDS = 3.0
DEFAULT_SWITCH_MARGIN = 0.2  # challenger must beat the active camera by 20%

_WEIGHT_SPEECH = 1.0
_WEIGHT_REACTION = 1.5
_WEIGHT_MOTION = 0.5
_PRIMARY_CAMERA_BONUS = 0.1


def _is_primary_camera_tag(tag: str) -> bool:
    lowered = tag.lower()
    return any(hint in lowered or hint in tag for hint in _PRIMARY_TAG_HINTS)


def _window_score(analysis: AnalysisResult, local_start: float, local_end: float, camera_tag: str) -> float:
    """How much this camera "deserves" to be on screen for [local_start, local_end)."""
    window_length = max(1e-6, local_end - local_start)

    speech_seconds = 0.0
    has_reaction = False
    for segment in analysis.transcript.segments:
        overlap = min(segment.end_seconds, local_end) - max(segment.start_seconds, local_start)
        if overlap <= 0:
            continue
        speech_seconds += overlap
        if any(marker in segment.text for marker in _REACTION_MARKERS):
            has_reaction = True

    motion_total = 0.0
    motion_count = 0
    if analysis.visual_activity is not None:
        for segment in analysis.visual_activity.segments:
            overlap = min(segment.end_seconds, local_end) - max(segment.start_seconds, local_start)
            if overlap <= 0:
                continue
            motion_total += segment.motion_score
            motion_count += 1

    score = (speech_seconds / window_length) * _WEIGHT_SPEECH
    if has_reaction:
        score += _WEIGHT_REACTION
    if motion_count:
        score += (motion_total / motion_count) * _WEIGHT_MOTION
    if _is_primary_camera_tag(camera_tag):
        score += _PRIMARY_CAMERA_BONUS
    return score


def build_angle_switch_plan(
    group: MulticamGroup,
    analysis_by_path: Dict[Path, AnalysisResult],
    bucket_seconds: float = DEFAULT_BUCKET_SECONDS,
    min_segment_seconds: float = DEFAULT_MIN_SEGMENT_SECONDS,
    switch_margin: float = DEFAULT_SWITCH_MARGIN,
) -> AngleSwitchPlan:
    """Decide which camera plays at every moment of ``group``'s take.

    Args:
        group: The multicam take to plan (see :mod:`video.multicam_grouper`).
        analysis_by_path: Every camera's :class:`AnalysisResult`, keyed by
            its file path -- normally the same dict a
            :class:`~models.folder_analysis_result.FolderAnalysisResult`'s
            clips came from. A camera in ``group`` missing from this dict
            is silently excluded from consideration (its footage can't be
            scored, so it can never be picked).
        bucket_seconds: Granularity of the scoring pass. Smaller reacts
            faster to reactions/speaker changes; larger is calmer.
        min_segment_seconds: Minimum time a cut is held once made, before
            another switch is allowed (except when the active camera's
            own footage runs out, which always forces an immediate cut).
        switch_margin: How much a challenger camera's score must exceed
            the active camera's score, as a fraction, before the engine
            switches to it. Higher = fewer, more decisive cuts.

    Returns:
        A plan covering the whole take with no gaps, chronologically
        ordered, take-relative :class:`~models.angle_switch_plan.AngleSegment`
        entries.
    """
    cameras = [clip for clip in group.clips if clip.file_path in analysis_by_path]
    if not cameras:
        return AngleSwitchPlan(take_timestamp=group.timestamp, segments=[], camera_offsets_seconds={})

    offsets: Dict[str, float] = {
        clip.camera_tag: (clip.timestamp - group.timestamp).total_seconds() for clip in cameras
    }
    durations: Dict[str, float] = {
        clip.camera_tag: analysis_by_path[clip.file_path].video.duration_seconds or 0.0 for clip in cameras
    }
    path_by_tag: Dict[str, Path] = {clip.camera_tag: clip.file_path for clip in cameras}
    total_duration = max(offsets[tag] + durations[tag] for tag in offsets)

    segments: List[AngleSegment] = []
    active_tag: Optional[str] = None
    active_since = 0.0
    cursor = 0.0

    while cursor < total_duration:
        bucket_end = min(cursor + bucket_seconds, total_duration)

        scored: List[tuple] = []
        for clip in cameras:
            tag = clip.camera_tag
            local_start = cursor - offsets[tag]
            local_end = bucket_end - offsets[tag]
            # Camera hasn't started yet, or has already ended -- no
            # footage exists for this window, so it can't be a candidate.
            if local_end <= 0 or local_start >= durations[tag]:
                continue
            clamped_start = max(0.0, local_start)
            clamped_end = min(durations[tag], local_end)
            score = _window_score(analysis_by_path[path_by_tag[tag]], clamped_start, clamped_end, tag)
            scored.append((score, tag))

        if not scored:
            # No camera has footage in this window at all (a genuine gap
            # in the take) -- hold whatever was active rather than break
            # contiguity; there is nothing better to show.
            cursor = bucket_end
            continue

        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best_tag = scored[0]
        available_tags = {tag for _, tag in scored}

        reason = "구간 유지"
        if active_tag is None:
            active_tag, active_since, reason = best_tag, cursor, "취재분 시작"
        elif active_tag not in available_tags:
            # Forced switch: the camera that was on screen has run out of
            # footage here, regardless of hysteresis.
            active_tag, active_since = best_tag, cursor
            reason = f"'{active_tag}' 카메라 분량 종료로 전환"
        elif (cursor - active_since) >= min_segment_seconds:
            active_score = next(score for score, tag in scored if tag == active_tag)
            if best_tag != active_tag and best_score > active_score * (1.0 + switch_margin):
                active_tag, active_since = best_tag, cursor
                reason = "리액션/발화 활동 감지" if best_score >= _WEIGHT_REACTION else "발화자 전환"

        if segments and segments[-1].camera_tag == active_tag:
            segments[-1].end_seconds = bucket_end
        else:
            segments.append(
                AngleSegment(
                    start_seconds=cursor,
                    end_seconds=bucket_end,
                    camera_tag=active_tag,
                    file_path=path_by_tag[active_tag],
                    reason=reason,
                )
            )

        cursor = bucket_end

    return AngleSwitchPlan(take_timestamp=group.timestamp, segments=segments, camera_offsets_seconds=offsets)
