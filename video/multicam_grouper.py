"""Filename-based multicam grouping (Version 5.3.1).

Recognizes AutoCutAI's suggested raw-footage naming convention --

    <date><separator><time><separator><camera tag>.<ext>

e.g. ``20260914_153000_거치1cam.mp4``, ``2026-09-14_15-30-05_pov1cam.mp4``,
``20260914 153002 pov2cam.MOV`` -- and groups every clip whose embedded
timestamp falls within a short window of each other into one
:class:`~models.multicam_group.MulticamGroup`. In practice this means:
start every camera at roughly the same moment (pressing record within a
couple of seconds of each other is normal and expected -- exact
frame-sync is not), and this groups them back together automatically no
matter what order they're picked up from disk in.

This module only groups files by name; it does not open, probe, or
otherwise touch the video files themselves (that's
:class:`video.folder_importer.FolderImporter`'s job). Filenames that
don't match the convention are never an error -- they're returned
ungrouped, exactly like a solo clip filmed on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from models.multicam_group import MulticamClip, MulticamGroup

# Matches an 8-digit or dashed date immediately followed (with an
# optional separator) by a 4- or 6-digit, optionally colon/dash-punctuated
# time -- wherever it appears in the filename stem.
_TIMESTAMP_PATTERN = re.compile(
    r"(?P<date>\d{4}[-_]?\d{2}[-_]?\d{2})"
    r"[_\-\s]?"
    r"(?P<time>\d{2}[-_:]?\d{2}[-_:]?\d{2}|\d{2}[-_:]?\d{2})"
)

# Default: cameras started within this many seconds of each other still
# count as "the same take". Wide enough to cover someone walking around
# pressing record on 2-3 cameras by hand; narrow enough not to lump
# together two genuinely separate takes an hour apart.
DEFAULT_SYNC_TOLERANCE_SECONDS = 5.0


@dataclass(slots=True)
class ParsedFilename:
    """One file's parsed timestamp + camera tag (or lack thereof)."""

    file_path: Path
    timestamp: Optional[datetime]
    camera_tag: str


def parse_filename(file_path: Path) -> ParsedFilename:
    """Extract a timestamp + camera tag from one file's name.

    Whatever remains after stripping the matched date/time token becomes
    the camera tag (``거치1cam``, ``pov1cam``, ...). When no date+time
    token is found at all, ``timestamp`` is ``None`` and the whole
    filename stem is used as the camera tag, so the file still gets a
    sensible label -- it's just treated as ungrouped.
    """
    stem = file_path.stem
    match = _TIMESTAMP_PATTERN.search(stem)
    if not match:
        return ParsedFilename(file_path=file_path, timestamp=None, camera_tag=stem)

    date_digits = re.sub(r"\D", "", match.group("date"))
    time_digits = re.sub(r"\D", "", match.group("time"))
    if len(time_digits) == 4:  # HHMM -> HHMM00
        time_digits += "00"

    try:
        timestamp = datetime.strptime(date_digits + time_digits, "%Y%m%d%H%M%S")
    except ValueError:
        return ParsedFilename(file_path=file_path, timestamp=None, camera_tag=stem)

    camera_tag = (stem[: match.start()] + stem[match.end():]).strip("_- ")
    return ParsedFilename(file_path=file_path, timestamp=timestamp, camera_tag=camera_tag or "cam")


def group_by_timestamp(
    parsed_files: Sequence[ParsedFilename],
    tolerance_seconds: float = DEFAULT_SYNC_TOLERANCE_SECONDS,
) -> Tuple[List[MulticamGroup], List[MulticamClip]]:
    """Cluster parsed filenames into multicam takes.

    Args:
        parsed_files: Output of :func:`parse_filename` for a batch of files.
        tolerance_seconds: Max gap, from the first clip in a cluster, for
            another clip to be folded into the same take.

    Returns:
        ``(groups, solo_clips)`` -- ``groups`` holds every cluster of 2+
        clips (an actual multicam take); ``solo_clips`` holds every clip
        that didn't match anything else, whether because its filename
        didn't parse at all or because no other clip shares its moment.
    """
    dated = sorted((p for p in parsed_files if p.timestamp is not None), key=lambda p: p.timestamp)
    undated = [p for p in parsed_files if p.timestamp is None]

    clusters: List[List[ParsedFilename]] = []
    for item in dated:
        if clusters and (item.timestamp - clusters[-1][0].timestamp).total_seconds() <= tolerance_seconds:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    groups: List[MulticamGroup] = []
    solo_clips: List[MulticamClip] = [
        MulticamClip(file_path=p.file_path, camera_tag=p.camera_tag, timestamp=p.timestamp) for p in undated
    ]

    for cluster in clusters:
        clips = [
            MulticamClip(file_path=p.file_path, camera_tag=p.camera_tag, timestamp=p.timestamp) for p in cluster
        ]
        if len(clips) >= 2:
            groups.append(MulticamGroup(timestamp=cluster[0].timestamp, clips=clips))
        else:
            solo_clips.extend(clips)

    return groups, solo_clips


class MulticamGrouper:
    """Convenience wrapper: file paths in, groups + leftovers out."""

    def group_files(
        self,
        file_paths: Sequence[Path],
        tolerance_seconds: float = DEFAULT_SYNC_TOLERANCE_SECONDS,
    ) -> Tuple[List[MulticamGroup], List[MulticamClip]]:
        parsed = [parse_filename(path) for path in file_paths]
        return group_by_timestamp(parsed, tolerance_seconds=tolerance_seconds)
