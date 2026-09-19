"""Turns a resolved EditPlan (KEEP ranges only) into a Final Cut Pro XML
(XMEML v5) file that DaVinci Resolve -- including the free edition, which
has no scripting API -- can Import... directly into a new Timeline.

This module has **zero dependency on the Resolve Scripting API** (unlike
:mod:`davinci.resolve_connection`/:mod:`davinci.timeline_editor`, which
require a running, scriptable Resolve). It only needs:

- the already-resolved KEEP ranges from :mod:`davinci.edit_plan_applier`
  (the same module Version 5's Resolve Studio path already uses -- Section
  26 of the V5 spec asks for this reuse explicitly, so REMOVE-merging,
  clamping, and short-sliver-skipping behave identically on both paths),
- the source video's frame rate/resolution/duration (already computed by
  :mod:`video.video_importer`), and
- the source video's path on disk.

No video is read, re-encoded, or rendered here -- this module only writes
an XML text file that *references* the original media (Section 6 of the
V5 spec: "재인코딩하지 말 것").

Timestamp/frame policy
-----------------------
Exactly the same ``round(seconds * fps)`` conversion
:mod:`davinci.timeline_editor` already uses for the Resolve Studio path
(Section 8: "기존 프로젝트의 timestamp 정책과... 일관되게 적용"), so a
KEEP range behaves identically whether it's cut live in Resolve Studio or
imported from this XML in Resolve Free.

Timeline time vs. source time (Section 11)
-------------------------------------------
Each KEEP range becomes one ``<clipitem>`` whose Source In/Out
(``<in>``/``<out>``) are the range's own position in the *original*
source video, while its Timeline In/Out (``<start>``/``<end>``) are its
position after every earlier KEEP range has been played back-to-back with
no gaps -- i.e. the two coordinate systems are tracked completely
separately, exactly as the spec requires.

Clip order (reordering)
------------------------
Clips are laid out on the timeline in whatever order
``resolved.keep_order`` lists them in -- normally identical to
``resolved.keep_ranges`` (original chronological order), but when the
EditPlan requested a reorder (``EditPlan.output_order``, applied and
validated by :func:`davinci.edit_plan_applier.process_edit_plan`), this
is the requested playback order instead. Source In/Out are unaffected
either way -- only where each clip lands on the *timeline* changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote
from xml.sax.saxutils import escape

from davinci.edit_plan_applier import PlaybackSegment, ResolveEditPlan

TimeRange = Tuple[float, float]

# Fallback resolution used only if the caller genuinely couldn't determine
# the source video's dimensions (should not normally happen -- Section 21
# still requires a graceful, non-crashing result if it does).
_FALLBACK_WIDTH = 1920
_FALLBACK_HEIGHT = 1080

# Fallback audio format used only if the caller didn't pass real values
# probed from the source file. These are the overwhelmingly common values
# for screen-recorded/game-capture footage (this exporter's main use
# case), and -- critically -- DaVinci Resolve re-conforms audio playback
# from the actual media at ``<pathurl>`` rather than trusting these
# numbers blindly, so a mismatch here degrades gracefully instead of
# producing silence (unlike the missing <link>/<sourcetrack> bug this
# fixes, which produced silence unconditionally from the 2nd KEEP clip
# on).
_FALLBACK_AUDIO_SAMPLE_RATE = 48000
_FALLBACK_AUDIO_DEPTH = 16
_FALLBACK_AUDIO_CHANNELS = 2

# (nominal fps, XMEML <timebase>, <ntsc>) -- the standard NTSC/whole-number
# frame rates DaVinci Resolve expects an XMEML <rate> block to match.
_KNOWN_FRAME_RATES: List[Tuple[float, int, bool]] = [
    (23.976, 24, True), (24.0, 24, False),
    (25.0, 25, False),
    (29.97, 30, True), (30.0, 30, False),
    (50.0, 50, False),
    (59.94, 60, True), (60.0, 60, False),
]


@dataclass(slots=True)
class ClipFrames:
    """One playback segment's frame-accurate position in both coordinate
    systems -- speed-aware since Version 5.5.

    ``timeline_end_frame - timeline_start_frame`` is the number of frames
    this clip occupies in the *exported timeline* -- shorter than
    ``source_out_frame - source_in_frame`` (the source footage actually
    consumed) whenever ``speed_factor > 1.0``. For a normal (``1.0``)
    speed clip the two spans are identical, exactly as before this field
    existed.
    """

    timeline_start_frame: int
    timeline_end_frame: int
    source_in_frame: int
    source_out_frame: int
    speed_factor: float = 1.0


class ResolveXMLExportError(Exception):
    """Raised for problems specific to building/writing the XML itself.

    Kept independent of :mod:`core.exceptions` (Resolve is only ever
    *referenced*, never contacted here) -- :mod:`services.resolve_export_service`
    catches this and re-raises it as ``core.exceptions.ResolveXMLExportError``
    so the UI layer only ever has to catch one family of error, exactly
    like the Resolve Studio path already does for ``ResolveError``.
    """


def frame_rate_to_timebase(fps: float) -> Tuple[int, bool]:
    """Map a source fps to the nearest XMEML ``(timebase, ntsc)`` pair.

    Section 22, Tests 8/9: 29.97fps -> (30, True), 60fps -> (60, False).
    An fps that doesn't resemble any known broadcast rate still produces a
    safe, valid result (rounded to the nearest whole frame rate, ``ntsc``
    False) rather than raising.
    """
    if fps <= 0:
        raise ResolveXMLExportError("Could not determine source frame rate.")
    nominal, timebase, ntsc = min(_KNOWN_FRAME_RATES, key=lambda row: abs(fps - row[0]))
    if abs(fps - nominal) < 0.06:
        return timebase, ntsc
    return max(1, round(fps)), False


def seconds_to_frame(seconds: float, fps: float) -> int:
    """The same ``round(seconds * fps)`` policy used by
    :func:`davinci.timeline_editor.apply_remove_ranges`, kept in one place
    so both the Resolve Studio and Resolve Free (XML) paths can never
    silently drift apart (Section 8 of the V5 spec)."""
    return max(0, round(seconds * fps))


def _frame_to_timecode_string(frame: int, timebase: int, ntsc: bool) -> str:
    """Render a frame count as an ``HH:MM:SS:FF`` (or drop-frame
    ``HH:MM:SS;FF``) timecode string for XMEML's ``<file><timecode><string>``.

    This is what lets a source file's *real* embedded starting timecode
    (e.g. ``01:00:00:00`` -- extremely common on screen recordings and
    capture-card footage, which frequently start their internal timecode
    track at 1 hour rather than 0) be declared in the exported XML instead
    of silently assumed to be ``00:00:00:00``.

    Only 29.97fps/59.94fps (``ntsc`` True, ``timebase`` 30/60) use drop-frame
    counting, matching how DaVinci Resolve itself displays those rates.
    """
    frame = max(0, frame)
    if not ntsc or timebase not in (30, 60):
        fps = timebase
        total_seconds, ff = divmod(frame, fps)
        hh, rem = divmod(total_seconds, 3600)
        mm, ss = divmod(rem, 60)
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    # Drop-frame timecode (SMPTE): skip frame numbers 0 and 1 of every
    # minute except every 10th minute, so the displayed HH:MM:SS:FF stays
    # in sync with real-world wall-clock time despite 29.97/59.94 not
    # being a whole number of frames per second.
    drop_frames = 2 if timebase == 30 else 4
    frames_per_min = timebase * 60 - drop_frames
    frames_per_10min = timebase * 60 * 10 - drop_frames * 9

    ten_min_chunks, remainder = divmod(frame, frames_per_10min)
    if remainder < drop_frames:
        adjusted = frame + drop_frames * 9 * ten_min_chunks
    else:
        extra_minutes = (remainder - drop_frames) // frames_per_min
        adjusted = frame + drop_frames * 9 * ten_min_chunks + drop_frames * extra_minutes

    ff = adjusted % timebase
    ss = (adjusted // timebase) % 60
    mm = (adjusted // (timebase * 60)) % 60
    hh = (adjusted // (timebase * 3600)) % 24
    return f"{hh:02d}:{mm:02d}:{ss:02d};{ff:02d}"


def _path_to_file_url(path: Path) -> str:
    """A ``file://`` URL DaVinci Resolve reliably recognizes for
    ``<pathurl>``, safe for Windows paths, spaces, and non-ASCII (e.g.
    Korean) file names (Section 10 of the V5 spec).

    ``Path.as_posix()`` turns a Windows path like
    ``C:\\Videos\\bedwars complete.mp4`` into ``C:/Videos/bedwars complete.mp4``
    without touching the drive letter; prefixing a bare leading slash
    before a drive letter and percent-encoding the rest (while keeping
    ``/`` and ``:`` unescaped) produces exactly the
    ``file:///C:/Videos/bedwars%20complete.mp4`` shape the spec calls out.
    """
    posix = path.as_posix()
    if len(posix) >= 2 and posix[1] == ":":  # e.g. "C:/Videos/..."
        posix = "/" + posix
    return "file://" + quote(posix, safe="/:")


def _resolve_ranges_to_clip_frames(playback_segments: List[PlaybackSegment], fps: float) -> List[ClipFrames]:
    """Convert absolute-source-time playback segments into (timeline, source)
    frame pairs, playing every segment back-to-back with no gaps.

    For a normal-speed (``1.0``) segment the timeline span exactly equals
    the source span, byte-for-byte the same arithmetic this function has
    always done. For a COMPRESSed segment (``speed_factor > 1.0``), the
    *full* source frame range is still consumed (``source_out_frame -
    source_in_frame`` is never shortened -- no footage is skipped), but
    the timeline span is ``round(source_frames / speed_factor)`` -- fewer
    frames, played faster. This asymmetry (source span the NLE, and
    Resolve specifically, reads more source frames than there are
    timeline frames to hold them) is standard XMEML retiming: it's what
    causes Resolve to actually conform the footage to a higher playback
    rate, independent of the ``<filter>`` label added at export time.

    ``source_in_frame``/``source_out_frame`` are plain frame counts from
    the physical start of the file (frame 0), independent of whatever
    starting timecode the file's own ``<timecode>`` block declares --
    that's standard XMEML: the ``<file><timecode>`` only labels what
    real-world timecode frame 0 corresponds to; it does not shift where
    ``<in>``/``<out>`` themselves point.
    """
    clips: List[ClipFrames] = []
    timeline_cursor = 0
    for segment in playback_segments:
        source_in = seconds_to_frame(segment.start_seconds, fps)
        source_out = seconds_to_frame(segment.end_seconds, fps)
        if source_out <= source_in:
            # A segment that rounds to zero frames at this fps -- skip it
            # rather than emit a zero-length <clipitem> Resolve might
            # reject (mirrors timeline_editor's handling of the same case
            # on the Resolve Studio path).
            continue
        source_frames = source_out - source_in
        speed = segment.speed_factor if segment.speed_factor and segment.speed_factor > 0 else 1.0
        timeline_frames = source_frames if abs(speed - 1.0) < 1e-9 else max(1, round(source_frames / speed))
        clips.append(ClipFrames(
            timeline_start_frame=timeline_cursor,
            timeline_end_frame=timeline_cursor + timeline_frames,
            source_in_frame=source_in,
            source_out_frame=source_out,
            speed_factor=speed,
        ))
        timeline_cursor += timeline_frames
    return clips


def _link_pair_block(video_id: str, audio_id: str, clip_index: int) -> str:
    """The pair of ``<link>`` entries DaVinci Resolve needs to treat a
    video clipitem and an audio clipitem as one linked A/V clip.

    Both entries are identical and get embedded in *both* the video and
    the audio clipitem (standard XMEML practice) -- without this, Resolve
    has no way to know ``clipitem-v2``/``clipitem-a2`` belong together
    once more than one clipitem pair references the same ``<file>``,
    which is exactly why only the first KEEP clip's audio played and
    every clip after it was silent.
    """
    return f"""<link>
<linkclipref>{video_id}</linkclipref>
<mediatype>video</mediatype>
<trackindex>1</trackindex>
<clipindex>{clip_index}</clipindex>
</link>
<link>
<linkclipref>{audio_id}</linkclipref>
<mediatype>audio</mediatype>
<trackindex>1</trackindex>
<clipindex>{clip_index}</clipindex>
<groupindex>1</groupindex>
</link>"""


def _speed_filter_block(speed_factor: float) -> str:
    """The FCP7/XMEML "Time Remap" filter block that labels a clip's speed
    change for apps (Premiere, Resolve) that read it explicitly, on top
    of the in/out-vs-start/end frame mismatch that does the actual
    retiming (see :func:`_resolve_ranges_to_clip_frames`). Omitted
    entirely for a normal-speed (``1.0x``) clip -- exactly the XML this
    function has always produced for every clip before Version 5.5.
    """
    if abs(speed_factor - 1.0) < 1e-6:
        return ""
    percent = speed_factor * 100.0
    return f"""<filter>
<effect>
<name>Time Remap</name>
<effectid>timeremap</effectid>
<effectcategory>motion</effectcategory>
<effecttype>motion</effecttype>
<mediatype>video</mediatype>
<parameter>
<parameterid>speed</parameterid>
<name>speed</name>
<value>{percent:.4f}</value>
</parameter>
<parameter>
<parameterid>reverse</parameterid>
<name>reverse</name>
<value>FALSE</value>
</parameter>
<parameter>
<parameterid>frameblending</parameterid>
<name>frame blending</name>
<value>FALSE</value>
</parameter>
</effect>
</filter>"""


def build_edit_xml(
    resolved: ResolveEditPlan,
    source_video_path: Path,
    fps: float,
    sequence_name: str = "AutoCutAI_Edit",
    width: Optional[int] = None,
    height: Optional[int] = None,
    include_audio: bool = True,
    audio_sample_rate: Optional[int] = None,
    audio_depth: Optional[int] = None,
    audio_channels: Optional[int] = None,
    source_start_timecode_seconds: float = 0.0,
) -> str:
    """Build a complete XMEML v5 document string for ``resolved``'s final
    playback sequence (``resolved.playback_order``, speed-aware since
    Version 5.5 -- falls back to ``resolved.keep_order``/``keep_ranges``
    at 1.0x if a caller-built ``ResolveEditPlan`` never set it).

    Any segment with a speed factor other than ``1.0`` (a COMPRESS
    segment -- see :mod:`models.edit_plan`) is exported as an actually
    shorter clip on the timeline than its source span, plus a "Time
    Remap" filter block labeling the speed -- see
    :func:`_resolve_ranges_to_clip_frames` and :func:`_speed_filter_block`.
    This is the Export XML path only: the direct "Apply to Resolve"
    (Resolve Studio scripting) path does not retime clips, and
    :func:`davinci.edit_plan_applier.process_edit_plan` warns about that
    difference whenever COMPRESS segments are present.

    ``audio_sample_rate``/``audio_depth``/``audio_channels`` describe the
    *source* file's actual audio format when the caller has it (e.g. from
    an ffprobe pass); when omitted, sane defaults are used -- Resolve
    re-conforms playback from the real media at ``<pathurl>`` either way,
    so this only affects how the format is *labeled* in the XML, never
    whether audio plays.

    ``source_start_timecode_seconds`` is the source file's own EMBEDDED
    starting timecode -- typically
    :attr:`models.video_project.VideoProject.source_timecode_seconds`,
    read from the file's ``tmcd``/timecode metadata track (see
    :func:`video.timeline_sync.probe_embedded_timecode`). This is a
    different piece of metadata from a stream's ``start_time``: a file
    can easily have ``start_time=0.0`` while its embedded timecode track
    still starts at ``01:00:00:00`` (an extremely common convention for
    screen recordings/capture-card footage). It only affects the
    ``<file><timecode>`` block below -- ``<in>``/``<out>`` on every clip
    stay plain frame counts from 0 either way, which is standard XMEML.
    Leaving this at the default ``0.0`` while the real file's embedded
    timecode starts elsewhere causes Resolve to see a mismatch between
    what the XML declares and what it reads from the real media on
    import/relink, and it rejects the clips with a ``"No overlap"``
    error.

    Raises:
        ResolveXMLExportError: ``fps`` is invalid, or there are no KEEP
            ranges at all to export (an EditPlan that would remove
            everything -- nothing would be left in the timeline).
    """
    timebase, ntsc = frame_rate_to_timebase(fps)

    playback_segments = resolved.playback_order or [
        PlaybackSegment(start, end, 1.0) for start, end in (resolved.keep_order or resolved.keep_ranges)
    ]
    clips = _resolve_ranges_to_clip_frames(playback_segments, fps)
    if not clips:
        raise ResolveXMLExportError(
            "EditPlan contains invalid time ranges.\n"
            "No KEEP segments remain to export -- nothing would be left in the timeline."
        )

    width = width or _FALLBACK_WIDTH
    height = height or _FALLBACK_HEIGHT
    audio_sample_rate = audio_sample_rate or _FALLBACK_AUDIO_SAMPLE_RATE
    audio_depth = audio_depth or _FALLBACK_AUDIO_DEPTH
    audio_channels = audio_channels or _FALLBACK_AUDIO_CHANNELS

    total_frames = clips[-1].timeline_end_frame
    file_name = escape(source_video_path.name)
    file_path_url = escape(_path_to_file_url(source_video_path))
    file_duration_frames = max(
        seconds_to_frame(resolved.media_duration_seconds, fps) if resolved.media_duration_seconds else total_frames,
        total_frames,
    )

    ntsc_str = "TRUE" if ntsc else "FALSE"
    rate_block = f"<rate>\n<timebase>{timebase}</timebase>\n<ntsc>{ntsc_str}</ntsc>\n</rate>"

    # <audio> media info for the <file> block. Filled in with the file's
    # real (or best-guess) audio format -- previously this was an empty
    # ``<audio/>`` tag, which is what left "실제 audio media 정보가 비어
    # 있음" (no actual audio media info) for Resolve to work with.
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

    # The source file's own starting timecode, as read from its embedded
    # timecode track (Section: see this function's docstring for why this
    # is different from a stream's start_time). <in>/<out> above are NOT
    # shifted by this -- they stay plain frame counts from 0 -- this block
    # only labels what real-world timecode frame 0 of the file itself
    # corresponds to, so Resolve's own probe of the real media (which
    # reads this same embedded timecode) matches what the XML declares
    # instead of assuming 00:00:00:00.
    start_timecode_frame = seconds_to_frame(source_start_timecode_seconds, fps)
    start_timecode_string = _frame_to_timecode_string(start_timecode_frame, timebase, ntsc)
    timecode_block = f"""<timecode>
{rate_block}
<string>{start_timecode_string}</string>
<frame>{start_timecode_frame}</frame>
<displayformat>{"DF" if ntsc else "NDF"}</displayformat>
</timecode>"""

    # The <file> element is only fully defined once; every later clipitem
    # referencing the same source media reuses it by id (standard XMEML
    # practice DaVinci Resolve expects -- avoids Resolve treating repeated
    # full <file> blocks as separate media items).
    file_id = "file-1"
    file_definition = f"""<file id="{file_id}">
<name>{file_name}</name>
<pathurl>{file_path_url}</pathurl>
{rate_block}
<duration>{file_duration_frames}</duration>
{timecode_block}
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
    file_reference = f'<file id="{file_id}"/>'

    video_clipitems = []
    audio_clipitems = []
    for index, clip in enumerate(clips, start=1):
        file_block = file_definition if index == 1 else file_reference
        clip_name = f"{file_name} ({index})"
        if clip.speed_factor > 1.0 + 1e-9:
            clip_name += f" [{clip.speed_factor:.2f}x]"
        speed_filter = _speed_filter_block(clip.speed_factor)
        video_id = f"clipitem-v{index}"
        audio_id = f"clipitem-a{index}"
        # Same pair of <link> entries embedded in both clipitems of this
        # KEEP range -- see _link_pair_block's docstring for why this is
        # what actually keeps audio playing past the first clip.
        link_block = _link_pair_block(video_id, audio_id, index) if include_audio else ""
        video_clipitems.append(f"""<clipitem id="{video_id}">
<name>{clip_name}</name>
{rate_block}
<duration>{clip.timeline_end_frame - clip.timeline_start_frame}</duration>
<start>{clip.timeline_start_frame}</start>
<end>{clip.timeline_end_frame}</end>
<in>{clip.source_in_frame}</in>
<out>{clip.source_out_frame}</out>
{file_block}
{speed_filter}
{link_block}
</clipitem>""")
        if include_audio:
            audio_clipitems.append(f"""<clipitem id="{audio_id}">
<name>{clip_name}</name>
{rate_block}
<duration>{clip.timeline_end_frame - clip.timeline_start_frame}</duration>
<start>{clip.timeline_start_frame}</start>
<end>{clip.timeline_end_frame}</end>
<in>{clip.source_in_frame}</in>
<out>{clip.source_out_frame}</out>
{file_reference}
<sourcetrack>
<mediatype>audio</mediatype>
<trackindex>1</trackindex>
</sourcetrack>
{speed_filter}
{link_block}
</clipitem>""")

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
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
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
    return xml


def write_edit_xml(
    resolved: ResolveEditPlan,
    source_video_path: Path,
    fps: float,
    output_path: Path,
    sequence_name: str = "AutoCutAI_Edit",
    width: Optional[int] = None,
    height: Optional[int] = None,
    audio_sample_rate: Optional[int] = None,
    audio_depth: Optional[int] = None,
    audio_channels: Optional[int] = None,
    source_start_timecode_seconds: float = 0.0,
) -> None:
    """Build the XML (see :func:`build_edit_xml`) and write it to ``output_path``.

    Raises:
        ResolveXMLExportError: building the XML failed, or the file could
            not be written.
    """
    xml_text = build_edit_xml(
        resolved, source_video_path, fps, sequence_name=sequence_name, width=width, height=height,
        audio_sample_rate=audio_sample_rate, audio_depth=audio_depth, audio_channels=audio_channels,
        source_start_timecode_seconds=source_start_timecode_seconds,
    )
    try:
        Path(output_path).write_text(xml_text, encoding="utf-8")
    except OSError as exc:
        raise ResolveXMLExportError(f"Failed to export Resolve XML.\n\nDetails:\n{exc}") from exc


__all__ = [
    "ResolveXMLExportError",
    "ClipFrames",
    "frame_rate_to_timebase",
    "seconds_to_frame",
    "build_edit_xml",
    "write_edit_xml",
]
