"""EditPlan -> concrete REMOVE ranges, with zero dependency on the Resolve API.

This module answers exactly one question -- "given this EditPlan, which
absolute source-video time ranges should actually be cut?" -- and answers it
the same way whether or not DaVinci Resolve, or even a real video file, is
anywhere nearby. Keeping that decision here (instead of inline in
:mod:`davinci.timeline_editor`) means the hardest, most safety-critical part
of Version 5 (never deleting the wrong range, never trusting an invalid
timestamp, never silently dropping data) can be fully exercised by a plain
``pytest``/manual test run, with no Resolve installation, no GPU, and no
video file required -- see ``tests/test_v5_resolve_manual.py``.

Design choices that matter for correctness:

- Only ``EditPlan.remove_segments`` is ever used to decide what to cut.
  ``keep_segments``/``compress_segments`` are informational only. If a plan's
  segments don't perfectly tile the source duration (gaps, a stray
  overlap from an upstream bug), the untouched stretches are left as KEEP
  by default -- this module only ever removes what was explicitly marked
  ``remove``, matching Section 3's "never destroy the user's footage"
  principle: when in doubt, keep.
- COMPRESS is applied as an actual speed change in the exported Resolve
  XML (see :attr:`ResolveEditPlan.playback_order` and
  :mod:`davinci.resolve_xml_exporter`) -- Version 5.5. It is still *not*
  applied by the direct "Apply to Resolve" path
  (:mod:`davinci.timeline_editor`'s Resolve Studio scripting, which only
  ever ripple-deletes REMOVE ranges): a COMPRESS segment applied that way
  plays at normal speed, and ``process_edit_plan`` says so in its
  warnings every time COMPRESS segments are present, regardless of which
  path the caller ends up using.
- All arithmetic is done once, up front, entirely in the EditPlan's own
  absolute source-time coordinate system (Section 9's requirement) --
  nothing here ever re-measures a "current" position after a partial cut,
  which is exactly the class of bug that produces drifting timestamps
  across multiple REMOVE segments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from models.edit_plan import EditPlan
from utils.constants import (
    DEFAULT_COMPRESSION_SPEED_FACTOR,
    EDIT_ACTION_COMPRESS,
    RESOLVE_MIN_REMOVE_DURATION_SECONDS,
    RESOLVE_SEGMENT_MERGE_EPSILON_SECONDS,
)

TimeRange = Tuple[float, float]
# (start_seconds, end_seconds, speed_factor) -- the validated,
# extent-clipped form of a COMPRESS segment, before it's spliced into a
# PlaybackSegment sequence.
CompressRange = Tuple[float, float, float]


@dataclass(slots=True)
class PlaybackSegment:
    """One contiguous stretch of the final export, in source-time coordinates.

    ``speed_factor`` is ``1.0`` for an ordinary full-speed KEEP stretch,
    or the segment's ``compression_speed_factor`` (always ``> 1.0``) for a
    COMPRESSed stretch. A REMOVEd range is never represented here -- it
    simply isn't part of the sequence.

    This is the speed-aware sibling of the plain ``(start, end)``
    ``TimeRange`` tuples ``keep_ranges``/``keep_order`` use: those two
    stay exactly as they always were (every non-removed stretch, speed
    ignored) for backward compatibility with every caller/test that
    predates Version 5.5; ``playback_order`` is what
    :mod:`davinci.resolve_xml_exporter` actually builds clips from now.
    """

    start_seconds: float
    end_seconds: float
    speed_factor: float = 1.0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)

    @property
    def output_duration_seconds(self) -> float:
        """How long this stretch plays for in the final export, after speed."""
        factor = self.speed_factor if self.speed_factor and self.speed_factor > 0 else 1.0
        return self.duration_seconds / factor

    @property
    def is_compressed(self) -> bool:
        return self.speed_factor > 1.0 + 1e-9


@dataclass(slots=True)
class ResolveEditPlan:
    """The fully validated, Resolve-ready result of processing an EditPlan.

    ``remove_ranges`` is the only field :mod:`davinci.timeline_editor` needs
    to actually cut a timeline: a sorted, non-overlapping list of
    ``(start_seconds, end_seconds)`` pairs in the source video's own
    absolute timeline, each guaranteed ``0 <= start < end`` and (when
    ``media_duration_seconds`` was known) ``end <= media_duration_seconds``.
    """

    remove_ranges: List[TimeRange] = field(default_factory=list)
    keep_ranges: List[TimeRange] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    media_duration_seconds: Optional[float] = None
    compress_segment_count: int = 0
    # Clip reordering. Always the exact same set of ranges as
    # ``keep_ranges`` -- just possibly in a different order when the
    # EditPlan requested one (see ``EditPlan.output_order``). Exporters
    # (e.g. :mod:`davinci.resolve_xml_exporter`) should build the final
    # timeline from THIS list, not ``keep_ranges`` directly, so a
    # requested reorder actually takes effect; when no reorder was
    # requested (the overwhelming common case, and every plan from
    # before this feature existed) this is identical to ``keep_ranges``.
    keep_order: List[TimeRange] = field(default_factory=list)
    # Version 5.5: the speed-aware final segment sequence -- every
    # non-removed stretch, in final playback order (reorder-aware, same
    # as keep_order), split at COMPRESS boundaries and tagged with each
    # piece's speed factor. Always covers exactly the same footage as
    # keep_order, just at finer granularity when COMPRESS is in play.
    # This -- not keep_ranges/keep_order -- is what
    # :mod:`davinci.resolve_xml_exporter` builds clips from.
    playback_order: List[PlaybackSegment] = field(default_factory=list)

    @property
    def has_removals(self) -> bool:
        return len(self.remove_ranges) > 0

    @property
    def total_removed_seconds(self) -> float:
        return sum(end - start for start, end in self.remove_ranges)

    @property
    def total_kept_seconds(self) -> float:
        return sum(end - start for start, end in self.keep_ranges)

    @property
    def total_output_seconds(self) -> float:
        """Estimated final export length, honoring COMPRESS speed."""
        return sum(segment.output_duration_seconds for segment in self.playback_order)


_REORDER_MATCH_EPSILON_SECONDS = 0.05  # float rounding tolerance when matching AI-proposed ranges back to real KEEP ranges


def _resolve_keep_order(
    keep_ranges: List[TimeRange], requested_order: Optional[List[TimeRange]], warnings: List[str]
) -> List[TimeRange]:
    """Validate ``requested_order`` and turn it into the final clip order.

    ``requested_order`` must be an exact reordering (a permutation) of
    ``keep_ranges`` -- same ranges, same total footage, just listed in a
    different sequence. This never invents new footage and never drops
    any: if the requested order doesn't match the actual KEEP ranges
    (wrong count, a range that doesn't correspond to anything actually
    kept), the request is rejected with a warning and the safe default
    (original chronological order) is used instead -- consistent with
    this module's "never destroy/misplace the user's footage, warn
    instead" philosophy.
    """
    if not requested_order:
        return list(keep_ranges)
    if len(requested_order) != len(keep_ranges):
        warnings.append(
            f"Requested clip order has {len(requested_order)} range(s) but there are "
            f"{len(keep_ranges)} actual KEEP range(s) -- ignoring the reorder request and "
            "using original chronological order instead."
        )
        return list(keep_ranges)

    remaining = list(keep_ranges)
    ordered: List[TimeRange] = []
    for start, end in requested_order:
        match_index = None
        for i, (k_start, k_end) in enumerate(remaining):
            if abs(k_start - start) <= _REORDER_MATCH_EPSILON_SECONDS and abs(k_end - end) <= _REORDER_MATCH_EPSILON_SECONDS:
                match_index = i
                break
        if match_index is None:
            warnings.append(
                f"Requested clip order includes a range ({start:.3f}s-{end:.3f}s) that doesn't match any "
                "actual KEEP range -- ignoring the reorder request and using original chronological order instead."
            )
            return list(keep_ranges)
        ordered.append(remaining.pop(match_index))
    return ordered


def _validate_compress_ranges(
    compress_segments: List, full_extent_ranges: List[TimeRange],
    media_duration_seconds: Optional[float], warnings: List[str],
) -> List[CompressRange]:
    """Validate + clip COMPRESS segments to the actual KEEP-complement territory.

    Mirrors the REMOVE-segment validation in :func:`process_edit_plan`
    (skip invalid, clamp to media bounds) and additionally clips each
    compress range to ``full_extent_ranges`` -- the territory not already
    claimed by a REMOVE range -- so an AI-proposed COMPRESS range that
    overlaps a REMOVE range never resurrects removed footage; only its
    non-removed portion is compressed, and a warning is recorded when
    that clipping changes anything.
    """
    ranges: List[CompressRange] = []
    for segment in compress_segments:
        start, end = float(segment.start_seconds), float(segment.end_seconds)
        speed = segment.compression_speed_factor or DEFAULT_COMPRESSION_SPEED_FACTOR
        if not (end > start):
            warnings.append(f"Skipped an invalid COMPRESS segment ({start:.3f}s-{end:.3f}s): end must be after start.")
            continue

        if media_duration_seconds is not None:
            clamped_start = max(0.0, min(start, media_duration_seconds))
            clamped_end = max(0.0, min(end, media_duration_seconds))
            if (clamped_start, clamped_end) != (start, end):
                warnings.append(
                    f"COMPRESS segment {start:.3f}s-{end:.3f}s was outside the media's "
                    f"{media_duration_seconds:.3f}s duration and was clamped to "
                    f"{clamped_start:.3f}s-{clamped_end:.3f}s."
                )
            start, end = clamped_start, clamped_end
            if not (end > start):
                continue

        pieces = [
            (max(start, extent_start), min(end, extent_end))
            for extent_start, extent_end in full_extent_ranges
        ]
        pieces = [(piece_start, piece_end) for piece_start, piece_end in pieces if piece_end > piece_start]

        if not pieces:
            warnings.append(
                f"COMPRESS segment {start:.3f}s-{end:.3f}s falls entirely inside a REMOVE range "
                "and was dropped -- a removed stretch can't also be compressed."
            )
            continue

        covered_seconds = sum(piece_end - piece_start for piece_start, piece_end in pieces)
        if covered_seconds < (end - start) - RESOLVE_SEGMENT_MERGE_EPSILON_SECONDS:
            warnings.append(
                f"COMPRESS segment {start:.3f}s-{end:.3f}s partially overlapped a REMOVE range -- "
                f"only the non-removed part ({covered_seconds:.3f}s) was compressed."
            )
        ranges.extend((piece_start, piece_end, speed) for piece_start, piece_end in pieces)

    ranges.sort(key=lambda r: r[0])
    return ranges


def _split_into_playback_segments(
    outer_ranges: List[TimeRange], compress_ranges: List[CompressRange],
) -> List[PlaybackSegment]:
    """Expand each outer (KEEP-complement) range into KEEP/COMPRESS
    sub-segments, in chronological order, using whichever validated
    ``compress_ranges`` fall inside it.

    ``outer_ranges`` is normally ``ResolveEditPlan.keep_order`` -- already
    reordered if the plan requested one -- so reordering happens at that
    coarser granularity (same as it always has) and this function only
    ever splits *within* each already-placed outer stretch; a compress
    sub-range's position relative to the rest of that stretch is always
    chronological regardless of where the stretch itself landed in the
    overall sequence.
    """
    epsilon = RESOLVE_SEGMENT_MERGE_EPSILON_SECONDS
    segments: List[PlaybackSegment] = []
    for outer_start, outer_end in outer_ranges:
        inner = sorted(
            (c_start, c_end, speed) for c_start, c_end, speed in compress_ranges
            if c_start >= outer_start - epsilon and c_end <= outer_end + epsilon
        )
        cursor = outer_start
        for c_start, c_end, speed in inner:
            if c_start > cursor + epsilon:
                segments.append(PlaybackSegment(cursor, c_start, 1.0))
            segments.append(PlaybackSegment(max(cursor, c_start), c_end, speed))
            cursor = max(cursor, c_end)
        if cursor < outer_end - epsilon:
            segments.append(PlaybackSegment(cursor, outer_end, 1.0))
    return segments


def process_edit_plan(plan: EditPlan, media_duration_seconds: Optional[float] = None) -> ResolveEditPlan:
    """Validate ``plan`` and compute the exact REMOVE ranges Resolve should apply.

    Never raises: every problem (an invalid segment, an out-of-range
    timestamp, overlapping REMOVE segments, an EditPlan with nothing in it)
    is recorded as a human-readable warning and handled as safely as
    possible (skip the bad segment, clamp to media bounds, merge
    overlaps) rather than aborting the whole plan. Callers that want to
    treat "nothing usable came out of this" as an error (e.g. before
    touching Resolve) should check :attr:`ResolveEditPlan.has_removals`
    themselves -- an EditPlan that legitimately keeps everything is not,
    by itself, a failure.
    """
    warnings: List[str] = []

    all_segments = plan.all_segments_sorted
    if not all_segments:
        warnings.append("EditPlan has no segments at all -- nothing to apply.")
        return ResolveEditPlan(remove_ranges=[], keep_ranges=[], warnings=warnings,
                                media_duration_seconds=media_duration_seconds, compress_segment_count=0)

    compress_count = len(plan.compress_segments)
    if compress_count:
        warnings.append(
            f"{compress_count} COMPRESS segment(s) present with a playback speed factor -- honored "
            "as an actual speed change when exporting a Resolve XML (Export XML), but NOT when using "
            "'Apply to Resolve' directly (Resolve Studio scripting doesn't retime clips yet); those "
            "segments play at normal speed if applied that way."
        )

    raw_ranges: List[TimeRange] = []
    for segment in plan.remove_segments:
        start, end = float(segment.start_seconds), float(segment.end_seconds)
        if not (end > start):
            warnings.append(f"Skipped an invalid REMOVE segment ({start:.3f}s-{end:.3f}s): end must be after start.")
            continue

        if media_duration_seconds is not None:
            clamped_start = max(0.0, min(start, media_duration_seconds))
            clamped_end = max(0.0, min(end, media_duration_seconds))
            if clamped_start != start or clamped_end != end:
                warnings.append(
                    f"REMOVE segment {start:.3f}s-{end:.3f}s was outside the media's "
                    f"{media_duration_seconds:.3f}s duration and was clamped to "
                    f"{clamped_start:.3f}s-{clamped_end:.3f}s."
                )
            start, end = clamped_start, clamped_end
            if not (end > start):
                warnings.append(f"Skipped a REMOVE segment that fell entirely outside the media duration.")
                continue

        if (end - start) < RESOLVE_MIN_REMOVE_DURATION_SECONDS:
            warnings.append(
                f"Skipped a REMOVE segment shorter than {RESOLVE_MIN_REMOVE_DURATION_SECONDS}s "
                f"({start:.3f}s-{end:.3f}s) -- too short to be a real cut."
            )
            continue

        raw_ranges.append((start, end))

    merged_ranges, merge_count = _merge_ranges(raw_ranges)
    if merge_count:
        warnings.append(f"Merged {merge_count} overlapping/touching REMOVE segment(s) into a single cut each.")

    duration_hint = media_duration_seconds
    if duration_hint is None and all_segments:
        duration_hint = max(seg.end_seconds for seg in all_segments)

    keep_ranges = _complement(merged_ranges, duration_hint) if duration_hint is not None else []
    keep_order = _resolve_keep_order(keep_ranges, plan.output_order, warnings)

    compress_ranges = _validate_compress_ranges(plan.compress_segments, keep_ranges, media_duration_seconds, warnings)
    playback_order = _split_into_playback_segments(keep_order, compress_ranges)

    return ResolveEditPlan(
        remove_ranges=merged_ranges,
        keep_ranges=keep_ranges,
        warnings=warnings,
        media_duration_seconds=media_duration_seconds,
        compress_segment_count=compress_count,
        keep_order=keep_order,
        playback_order=playback_order,
    )


def build_preview_lines(result: ResolveEditPlan) -> List[str]:
    """Human-readable Dry Run lines, e.g. for the "AutoCutAI Edit Preview" view.

    One line per contiguous stretch (KEEP or REMOVE), covering the full
    known duration when it's known, followed by a removed-time summary.
    Mirrors :class:`services.export_service.ExportService`'s existing
    formatting conventions so the two feel like the same product.
    """
    lines: List[str] = []
    combined: List[Tuple[str, float, float]] = [("REMOVE", s, e) for s, e in result.remove_ranges]
    combined += [("KEEP", s, e) for s, e in result.keep_ranges]
    combined.sort(key=lambda item: item[1])

    for label, start, end in combined:
        lines.append(f"{label:<8}  {_format_seconds(start)} - {_format_seconds(end)}")

    if not combined and result.media_duration_seconds:
        lines.append(f"KEEP      {_format_seconds(0.0)} - {_format_seconds(result.media_duration_seconds)}")

    lines.append("")
    lines.append(f"Total removed: {result.total_removed_seconds:.3f} sec ({len(result.remove_ranges)} cut(s))")
    if result.media_duration_seconds:
        lines.append(f"Total kept:    {result.total_kept_seconds:.3f} sec")
    return lines


def _merge_ranges(ranges: List[TimeRange]) -> Tuple[List[TimeRange], int]:
    """Sort + merge overlapping/near-touching ranges. Returns (merged, merge_count)."""
    if not ranges:
        return [], 0
    ordered = sorted(ranges, key=lambda r: r[0])
    merged: List[TimeRange] = [ordered[0]]
    merge_count = 0
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + RESOLVE_SEGMENT_MERGE_EPSILON_SECONDS:
            merged[-1] = (last_start, max(last_end, end))
            merge_count += 1
        else:
            merged.append((start, end))
    return merged, merge_count


def _complement(remove_ranges: List[TimeRange], duration_seconds: float) -> List[TimeRange]:
    """The KEEP ranges implied by ``remove_ranges`` over ``[0, duration_seconds]``."""
    keep: List[TimeRange] = []
    cursor = 0.0
    for start, end in remove_ranges:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration_seconds:
        keep.append((cursor, duration_seconds))
    return keep


def _format_seconds(seconds: float) -> str:
    minutes, remainder = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{remainder:06.3f}"


__all__ = ["EDIT_ACTION_COMPRESS", "PlaybackSegment", "ResolveEditPlan", "process_edit_plan", "build_preview_lines"]
