"""Turns an :class:`~models.angle_switch_plan.AngleSwitchPlan` into a Final
Cut Pro XML (XMEML v5) file DaVinci Resolve (including the free edition)
can Import... directly into a new Timeline -- a real cut sequence that
switches between camera files at the moments
:mod:`ai.multicam_angle_planner` decided, without re-encoding anything.

This is the multi-source sibling of :mod:`davinci.resolve_xml_exporter`:
that module builds a timeline from ONE source file's KEEP ranges; this
one builds a timeline where each clipitem can reference a DIFFERENT
source file (one per camera). It reuses that module's low-level,
already-tested helpers (frame-rate/timecode conversion, file:// URL
encoding, the video/audio `<link>` pairing block) rather than
duplicating them.

Every camera in the plan is assumed to share the same frame rate and
frame size (true for essentially every real multicam setup -- matching
camera models, or at least matching capture settings). If your cameras
genuinely differ, transcode/conform them to a common format before using
this exporter; XMEML has no clean way to mix frame rates on one track.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List
from xml.sax.saxutils import escape

from davinci.resolve_xml_exporter import (
    ResolveXMLExportError,
    _link_pair_block,
    _path_to_file_url,
    frame_rate_to_timebase,
    seconds_to_frame,
)
from models.angle_switch_plan import AngleSwitchPlan

_FALLBACK_WIDTH = 1920
_FALLBACK_HEIGHT = 1080
_FALLBACK_AUDIO_SAMPLE_RATE = 48000
_FALLBACK_AUDIO_DEPTH = 16
_FALLBACK_AUDIO_CHANNELS = 2


def build_multicam_edit_xml(
    plan: AngleSwitchPlan,
    fps: float,
    sequence_name: str = "AutoCutAI_Multicam_Edit",
    width: int = None,
    height: int = None,
    include_audio: bool = True,
    audio_sample_rate: int = None,
    audio_depth: int = None,
    audio_channels: int = None,
) -> str:
    """Build a complete XMEML v5 document string that plays ``plan.segments``
    back-to-back, cutting to each segment's own camera file.

    Each :class:`~models.angle_switch_plan.AngleSegment`'s take-relative
    start/end is converted back to that camera's own local file time using
    ``plan.camera_offsets_seconds`` before being written as the clip's
    Source In/Out -- Timeline In/Out are the segment's position after
    every earlier segment has played back-to-back with no gaps, exactly
    like :func:`davinci.resolve_xml_exporter.build_edit_xml`.

    Raises:
        ResolveXMLExportError: ``fps`` is invalid, or the plan has no
            segments to export.
    """
    if not plan.segments:
        raise ResolveXMLExportError("Angle-switch plan has no segments -- nothing to export.")

    timebase, ntsc = frame_rate_to_timebase(fps)
    width = width or _FALLBACK_WIDTH
    height = height or _FALLBACK_HEIGHT
    audio_sample_rate = audio_sample_rate or _FALLBACK_AUDIO_SAMPLE_RATE
    audio_depth = audio_depth or _FALLBACK_AUDIO_DEPTH
    audio_channels = audio_channels or _FALLBACK_AUDIO_CHANNELS

    ntsc_str = "TRUE" if ntsc else "FALSE"
    rate_block = f"<rate>\n<timebase>{timebase}</timebase>\n<ntsc>{ntsc_str}</ntsc>\n</rate>"
    file_audio_block = (
        f"""<audio>
<samplecharacteristics>
<depth>{audio_depth}</depth>
<samplerate>{audio_sample_rate}</samplerate>
</samplecharacteristics>
<channelcount>{audio_channels}</channelcount>
</audio>"""
        if include_audio
        else "<audio/>"
    )

    # One <file> block per unique camera file, defined the first time it's
    # referenced and reused by id every time after -- same convention
    # resolve_xml_exporter.build_edit_xml uses for repeated clips of one
    # file, just keyed per-camera here instead of always being the same file.
    file_ids: Dict[Path, str] = {}
    file_definitions: Dict[Path, str] = {}

    def _file_block_for(file_path: Path) -> str:
        if file_path in file_ids:
            return f'<file id="{file_ids[file_path]}"/>'
        file_id = f"file-{len(file_ids) + 1}"
        file_ids[file_path] = file_id
        definition = f"""<file id="{file_id}">
<name>{escape(file_path.name)}</name>
<pathurl>{escape(_path_to_file_url(file_path))}</pathurl>
{rate_block}
<media>
<video>
<samplecharacteristics>
<width>{width}</width>
<height>{height}</height>
</samplecharacteristics>
</video>
{file_audio_block}
</media>
</file>"""
        file_definitions[file_path] = definition
        return definition

    video_clipitems: List[str] = []
    audio_clipitems: List[str] = []
    timeline_cursor = 0

    for index, segment in enumerate(plan.segments, start=1):
        offset = plan.camera_offsets_seconds.get(segment.camera_tag, 0.0)
        local_in = seconds_to_frame(segment.start_seconds - offset, fps)
        local_out = seconds_to_frame(segment.end_seconds - offset, fps)
        if local_out <= local_in:
            continue  # a segment that rounds to zero frames at this fps

        duration_frames = local_out - local_in
        timeline_start = timeline_cursor
        timeline_end = timeline_cursor + duration_frames
        timeline_cursor = timeline_end

        file_block = _file_block_for(segment.file_path)
        clip_name = escape(f"{segment.camera_tag} ({segment.file_path.name})")
        video_id = f"clipitem-v{index}"
        audio_id = f"clipitem-a{index}"
        link_block = _link_pair_block(video_id, audio_id, index) if include_audio else ""

        video_clipitems.append(f"""<clipitem id="{video_id}">
<name>{clip_name}</name>
{rate_block}
<duration>{duration_frames}</duration>
<start>{timeline_start}</start>
<end>{timeline_end}</end>
<in>{local_in}</in>
<out>{local_out}</out>
{file_block}
{link_block}
</clipitem>""")

        if include_audio:
            audio_clipitems.append(f"""<clipitem id="{audio_id}">
<name>{clip_name}</name>
{rate_block}
<duration>{duration_frames}</duration>
<start>{timeline_start}</start>
<end>{timeline_end}</end>
<in>{local_in}</in>
<out>{local_out}</out>
<file id="{file_ids[segment.file_path]}"/>
<sourcetrack>
<mediatype>audio</mediatype>
<trackindex>1</trackindex>
</sourcetrack>
{link_block}
</clipitem>""")

    if not video_clipitems:
        raise ResolveXMLExportError("Every segment rounded to zero frames -- nothing would be left in the timeline.")

    total_frames = timeline_cursor
    audio_track_block = ""
    if include_audio:
        audio_track_block = f"""<audio>
<format>
<samplecharacteristics>
<depth>{audio_depth}</depth>
<samplerate>{audio_sample_rate}</samplerate>
</samplecharacteristics>
</format>
<track>
{chr(10).join(audio_clipitems)}
</track>
</audio>"""

    sequence_name_escaped = escape(sequence_name)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
<sequence>
<name>{sequence_name_escaped}</name>
<duration>{total_frames}</duration>
{rate_block}
<media>
<video>
<format>
<samplecharacteristics>
<width>{width}</width>
<height>{height}</height>
</samplecharacteristics>
</format>
<track>
{chr(10).join(video_clipitems)}
</track>
</video>
{audio_track_block}
</media>
</sequence>
</xmeml>
"""


def write_multicam_edit_xml(plan: AngleSwitchPlan, output_path: Path, fps: float, **kwargs) -> Path:
    """Build and write the XML in one call, mirroring
    :func:`davinci.resolve_xml_exporter.write_edit_xml`."""
    xml = build_multicam_edit_xml(plan, fps=fps, **kwargs)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml, encoding="utf-8")
    return output_path
