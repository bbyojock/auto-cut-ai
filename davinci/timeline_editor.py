"""Turns validated REMOVE ranges (from :mod:`davinci.edit_plan_applier`) into
real cuts on a DaVinci Resolve timeline, using only the official Resolve
Scripting API (``Timeline.SplitClip`` / ``Timeline.DeleteClips``).

Algorithm (why it avoids the "timestamp drifts after the first cut" bug)
--------------------------------------------------------------------------
Every REMOVE range is expressed in :mod:`davinci.edit_plan_applier` as an
absolute position on the *original, untouched* source timeline (Section 9 of
the V5 spec). A ripple delete shifts every clip *after* the deleted range
earlier -- so if ranges were applied in chronological order, the second
range's originally-computed frame numbers would already be wrong by the
time it was applied.

The fix is the same one real Resolve auto-cut tools use: apply the ranges
in **descending order of start time** (latest cut first). Deleting a later
range never moves anything before it, so every earlier range's
frame numbers computed up front (once, from the untouched timeline) stay
valid for the entire operation -- no re-measuring, no accumulated drift.

For each range, on each track (video and audio, so picture and sound never
drift apart):

1. Split any clip that straddles the range's start or end frame
   (``Timeline.SplitClip``), so the range's boundaries land exactly on clip
   edges.
2. Collect every clip now fully contained within ``[start_frame, end_frame)``.
3. Delete all of them in one ``Timeline.DeleteClips(..., ripple=True)`` call
   so every track closes the gap together, keeping picture and sound in
   sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Tuple

from core.exceptions import EditPlanResolveApplyError
from davinci.resolve_connection import ResolveHandle
from services.logging_service import LoggingService
from utils.constants import RESOLVE_EDIT_TIMELINE_PREFIX

_logger = LoggingService.get_logger("davinci.timeline_editor")

TimeRange = Tuple[float, float]


@dataclass(slots=True)
class ApplyResult:
    """What actually happened when REMOVE ranges were applied to a timeline."""

    edit_timeline_name: str
    ranges_applied: int
    ranges_requested: int
    clips_deleted: int


def duplicate_timeline(handle: ResolveHandle, source_video_name: str = "") -> Any:
    """Duplicate the current timeline so the original is never touched
    (Section 3 of the V5 spec), returning the new Timeline object.

    Names the duplicate ``AutoCutAI_Edit``, or ``AutoCutAI_Edit_001``,
    ``AutoCutAI_Edit_002``, ... if that name is already taken by an earlier
    AutoCutAI run -- exactly the naming scheme the spec asks for.
    """
    project = handle.project
    existing_names = _existing_timeline_names(project)

    base_name = RESOLVE_EDIT_TIMELINE_PREFIX
    candidate = base_name
    suffix = 0
    while candidate in existing_names:
        suffix += 1
        candidate = f"{base_name}_{suffix:03d}"

    new_timeline = handle.timeline.DuplicateTimeline(candidate)
    if new_timeline is None:
        raise EditPlanResolveApplyError(
            f"DaVinci Resolve refused to duplicate the current timeline as '{candidate}'. "
            "The original timeline was not modified."
        )
    _logger.info("Duplicated timeline '%s' -> '%s'", _safe_name(handle.timeline), candidate)
    return new_timeline


def apply_remove_ranges(handle: ResolveHandle, timeline: Any, remove_ranges: List[TimeRange]) -> ApplyResult:
    """Cut every range in ``remove_ranges`` out of ``timeline`` in place.

    ``timeline`` must already be the duplicate (see :func:`duplicate_timeline`)
    -- this function has no opinion about which timeline it's given and will
    happily cut the original if handed it, so callers must not skip
    duplication.
    """
    if not remove_ranges:
        return ApplyResult(edit_timeline_name=_safe_name(timeline) or "", ranges_applied=0,
                            ranges_requested=0, clips_deleted=0)

    fps = handle.timeline_frame_rate
    try:
        timeline_start_frame = int(timeline.GetStartFrame())
    except Exception as exc:  # noqa: BLE001
        raise EditPlanResolveApplyError(f"Could not read the timeline's start frame: {exc}") from exc

    # Descending by start time -- see the module docstring for why this
    # ordering is what prevents ripple-delete drift across multiple cuts.
    ordered_ranges = sorted(remove_ranges, key=lambda r: r[0], reverse=True)

    track_types = [t for t in ("video", "audio") if _track_count(timeline, t) > 0]
    if not track_types:
        raise EditPlanResolveApplyError("The duplicated timeline has no video or audio tracks to cut.")

    total_deleted = 0
    ranges_applied = 0
    for start_seconds, end_seconds in ordered_ranges:
        start_frame = timeline_start_frame + round(start_seconds * fps)
        end_frame = timeline_start_frame + round(end_seconds * fps)
        if end_frame <= start_frame:
            _logger.warning("Skipping a REMOVE range that rounded to zero frames: %.3f-%.3fs", start_seconds, end_seconds)
            continue

        try:
            deleted_here = _cut_range_on_all_tracks(timeline, track_types, start_frame, end_frame)
        except EditPlanResolveApplyError:
            raise
        except Exception as exc:  # noqa: BLE001 - Resolve's scripting API can raise almost anything
            raise EditPlanResolveApplyError(
                f"DaVinci Resolve rejected the cut at {start_seconds:.3f}-{end_seconds:.3f}s: {exc}"
            ) from exc

        total_deleted += deleted_here
        if deleted_here:
            ranges_applied += 1

    return ApplyResult(
        edit_timeline_name=_safe_name(timeline) or "",
        ranges_applied=ranges_applied,
        ranges_requested=len(remove_ranges),
        clips_deleted=total_deleted,
    )


def _cut_range_on_all_tracks(timeline: Any, track_types: List[str], start_frame: int, end_frame: int) -> int:
    """Split + collect the clips inside ``[start_frame, end_frame)`` on every
    track, then delete them all in one ripple call so tracks stay in sync."""
    to_delete: List[Any] = []

    for track_type in track_types:
        track_count = _track_count(timeline, track_type)
        for track_index in range(1, track_count + 1):
            _split_track_at_boundary(timeline, track_type, track_index, start_frame)
            _split_track_at_boundary(timeline, track_type, track_index, end_frame)

            for item in timeline.GetItemListInTrack(track_type, track_index) or []:
                item_start, item_end = int(item.GetStart()), int(item.GetEnd())
                if item_start >= start_frame and item_end <= end_frame:
                    to_delete.append(item)

    if not to_delete:
        return 0

    if not timeline.DeleteClips(to_delete, True):
        raise EditPlanResolveApplyError(
            f"DaVinci Resolve refused to delete {len(to_delete)} clip(s) for the "
            f"[{start_frame}, {end_frame}) frame range."
        )
    return len(to_delete)


def _split_track_at_boundary(timeline: Any, track_type: str, track_index: int, frame: int) -> None:
    """Split whichever clip on this track currently straddles ``frame``, if any.

    A no-op if ``frame`` already falls on a clip boundary (start/end of an
    existing item) or outside all clips (e.g. past the end of content).
    """
    for item in timeline.GetItemListInTrack(track_type, track_index) or []:
        item_start, item_end = int(item.GetStart()), int(item.GetEnd())
        if item_start < frame < item_end:
            if not timeline.SplitClip(item, frame):
                raise EditPlanResolveApplyError(
                    f"DaVinci Resolve refused to split a clip on {track_type} track {track_index} at frame {frame}."
                )
            return  # at most one clip can straddle a single frame position


def _track_count(timeline: Any, track_type: str) -> int:
    try:
        return int(timeline.GetTrackCount(track_type) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _existing_timeline_names(project: Any) -> set:
    names = set()
    try:
        count = int(project.GetTimelineCount() or 0)
        for i in range(1, count + 1):
            timeline = project.GetTimelineByIndex(i)
            if timeline is not None:
                names.add(timeline.GetName())
    except Exception:  # noqa: BLE001
        pass
    return names


def _safe_name(obj: Any):
    if obj is None:
        return None
    try:
        return obj.GetName()
    except Exception:  # noqa: BLE001
        return None
