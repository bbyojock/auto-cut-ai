"""Timestamp-pipeline consistency: ffprobe cross-check + audio/video offset.

Context (why this module exists)
---------------------------------
Every stage before this one measures time against its *own* zero point:

- :class:`video.video_importer.VideoImporter` derives ``duration_seconds``
  from OpenCV's ``total_frames / fps`` alone, with nothing to catch a wrong
  answer on a variable-frame-rate (VFR) file.
- :class:`video.audio_extractor.AudioExtractor` shells out to plain
  ``ffmpeg -i <video> ... audio.wav``. ffmpeg's default (no ``-copyts``)
  re-bases the extracted audio so its *own* first sample is WAV time 0 --
  it does **not** know or care what the video stream's start_time was.
- :class:`video.transcriber.WhisperTranscriber` then reports Whisper
  segment timestamps relative to *that* WAV file, i.e. relative to the
  audio stream's own start, not the video's.

If a container's audio and video streams don't start at exactly the same
point (extremely common -- screen recorders, phone cameras, and OBS all
routinely produce a few milliseconds to a few hundred milliseconds of
skew between the two), every transcript timestamp is silently offset by
that skew once it's compared against the video's own frame timeline
(the one :class:`video.frame_extractor.FrameExtractor` uses, where
``timestamp=0.0`` means the first video frame).

This module provides the one authoritative correction for that specific,
verified mechanism:

    video_timeline_seconds = whisper_wav_seconds
                              + audio_stream_start_time
                              - video_stream_start_time

It also cross-checks OpenCV's duration/fps against `ffprobe`, since
``total_frames / fps`` is a derived estimate that can be quietly wrong on
VFR footage, and flags VFR itself so downstream frame-timestamp callers
know their own numbers deserve less trust.

What this module deliberately does NOT do
------------------------------------------
It does not apply a guessed/fixed offset (e.g. "+2s"). Every number here
comes from the container's own stream metadata via ``ffprobe``. If
``ffprobe`` is unavailable or a stream is missing, the affected fields are
``None``/``False`` and callers fall back to the pre-existing OpenCV-only
behavior -- this module only ever adds information, it never blocks the
pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from models.transcript import Transcript, TranscriptSegment
from services.logging_service import LoggingService
from utils.constants import (
    AV_SYNC_OFFSET_EPSILON_SECONDS,
    DURATION_MISMATCH_WARNING_THRESHOLD_SECONDS,
    FFPROBE_TIMEOUT_SECONDS,
    VFR_FRAME_RATE_RELATIVE_TOLERANCE,
)

_logger = LoggingService.get_logger("video.timeline_sync")


@dataclass(slots=True)
class MediaTiming:
    """What `ffprobe` reports about a container's own timing, if anything.

    ``probed`` is False whenever ffprobe couldn't be run at all (missing
    binary, timeout, non-zero exit, unparsable output) -- every other
    field is then meaningless and callers must not use them.
    """

    probed: bool = False
    probe_error: Optional[str] = None

    video_start_time_seconds: Optional[float] = None
    video_stream_duration_seconds: Optional[float] = None
    r_frame_rate: Optional[str] = None
    avg_frame_rate: Optional[str] = None
    is_vfr: bool = False

    audio_start_time_seconds: Optional[float] = None
    audio_stream_duration_seconds: Optional[float] = None
    has_audio_stream: bool = False

    format_duration_seconds: Optional[float] = None

    @property
    def av_sync_offset_seconds(self) -> Optional[float]:
        """``audio_start_time - video_start_time``, or ``None`` if either is unknown.

        Positive means the audio stream starts *later* than the video
        stream (on the container's own clock); negative means audio
        starts earlier. See the module docstring for how this is applied.
        """
        if self.video_start_time_seconds is None or self.audio_start_time_seconds is None:
            return None
        return self.audio_start_time_seconds - self.video_start_time_seconds


def probe_media_timing(file_path: "Path | str", logger: Optional[logging.Logger] = None) -> MediaTiming:
    """Run `ffprobe` against ``file_path`` and extract stream-level timing.

    Never raises: any failure (ffprobe missing, timeout, bad exit code,
    unparsable JSON, no streams) is captured in the returned
    :class:`MediaTiming` as ``probed=False`` / ``probe_error=<reason>``.
    """
    log = logger or _logger
    command = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_entries", "stream=index,codec_type,start_time,duration,r_frame_rate,avg_frame_rate",
        "-show_entries", "format=duration",
        str(file_path),
    ]

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError:
        log.warning("ffprobe not found on PATH; skipping timestamp cross-check.")
        return MediaTiming(probed=False, probe_error="ffprobe not found on PATH")
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out after %ss; skipping timestamp cross-check.", FFPROBE_TIMEOUT_SECONDS)
        return MediaTiming(probed=False, probe_error="ffprobe timed out")

    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip()[-300:]
        log.warning("ffprobe failed (exit %s): %s", result.returncode, stderr_tail)
        return MediaTiming(probed=False, probe_error=f"ffprobe exit {result.returncode}: {stderr_tail}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        log.warning("ffprobe produced unparsable JSON: %s", exc)
        return MediaTiming(probed=False, probe_error=f"unparsable ffprobe JSON: {exc}")

    streams: List[dict] = payload.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    format_info = payload.get("format") or {}

    timing = MediaTiming(probed=True)
    timing.format_duration_seconds = _safe_float(format_info.get("duration"))

    if video_stream is not None:
        timing.video_start_time_seconds = _safe_float(video_stream.get("start_time")) or 0.0
        timing.video_stream_duration_seconds = _safe_float(video_stream.get("duration"))
        timing.r_frame_rate = video_stream.get("r_frame_rate")
        timing.avg_frame_rate = video_stream.get("avg_frame_rate")
        timing.is_vfr = _is_vfr(timing.r_frame_rate, timing.avg_frame_rate)
    else:
        log.warning("ffprobe found no video stream; timestamp cross-check limited to format-level duration.")

    if audio_stream is not None:
        timing.has_audio_stream = True
        timing.audio_start_time_seconds = _safe_float(audio_stream.get("start_time")) or 0.0
        timing.audio_stream_duration_seconds = _safe_float(audio_stream.get("duration"))

    return timing


def cross_check_duration(
    opencv_duration_seconds: Optional[float],
    timing: MediaTiming,
    logger: Optional[logging.Logger] = None,
) -> Optional[float]:
    """Return the duration :class:`~video.video_importer.VideoImporter` should use.

    If ffprobe wasn't available, or reported no usable duration, this
    returns ``opencv_duration_seconds`` unchanged (no behavior change from
    before this fix). Otherwise, ffprobe's container-level
    ``format.duration`` is preferred whenever it disagrees with OpenCV's
    ``total_frames / fps`` estimate by more than
    ``DURATION_MISMATCH_WARNING_THRESHOLD_SECONDS`` -- ffprobe reads the
    container's own duration field directly, while OpenCV's number is
    derived and known to be unreliable on VFR files.
    """
    log = logger or _logger
    if not timing.probed or timing.format_duration_seconds is None:
        return opencv_duration_seconds

    if opencv_duration_seconds is None:
        return timing.format_duration_seconds

    diff = abs(timing.format_duration_seconds - opencv_duration_seconds)
    if diff <= DURATION_MISMATCH_WARNING_THRESHOLD_SECONDS:
        return opencv_duration_seconds

    log.warning(
        "OpenCV duration (total_frames/fps=%.3fs) disagrees with ffprobe's container "
        "duration (%.3fs) by %.3fs (> %.1fs tolerance). Using the ffprobe duration.",
        opencv_duration_seconds, timing.format_duration_seconds, diff,
        DURATION_MISMATCH_WARNING_THRESHOLD_SECONDS,
    )
    return timing.format_duration_seconds


def apply_av_offset_to_transcript(
    transcript: Transcript,
    offset_seconds: Optional[float],
    logger: Optional[logging.Logger] = None,
) -> Transcript:
    """Shift every segment in ``transcript`` onto the video's own timeline.

    ``offset_seconds`` is ``audio_start_time - video_start_time`` (see
    :attr:`MediaTiming.av_sync_offset_seconds`). If it's ``None`` or
    negligible (``<= AV_SYNC_OFFSET_EPSILON_SECONDS``), ``transcript`` is
    returned unchanged (same object) -- this is the common case (audio and
    video streams sharing the same start_time) and must never introduce
    floating-point noise into every timestamp for nothing.

    Segments are clamped to ``start >= 0.0`` after the shift: a transcript
    segment can never legitimately start before the first video frame.
    """
    log = logger or _logger
    if offset_seconds is None or abs(offset_seconds) <= AV_SYNC_OFFSET_EPSILON_SECONDS:
        return transcript

    shifted_segments = [
        TranscriptSegment(
            start_seconds=max(0.0, segment.start_seconds + offset_seconds),
            end_seconds=max(0.0, segment.end_seconds + offset_seconds),
            text=segment.text,
        )
        for segment in transcript.segments
    ]
    log.warning(
        "Audio/video start_time mismatch detected (offset=%+.3fs); shifted %d transcript "
        "segment(s) onto the video's own timeline.",
        offset_seconds, len(shifted_segments),
    )
    return Transcript(
        language=transcript.language,
        language_probability=transcript.language_probability,
        segments=shifted_segments,
    )


def _is_vfr(r_frame_rate: Optional[str], avg_frame_rate: Optional[str]) -> bool:
    r = _parse_frame_rate(r_frame_rate)
    avg = _parse_frame_rate(avg_frame_rate)
    if r is None or avg is None or r <= 0:
        return False
    return abs(avg - r) / r > VFR_FRAME_RATE_RELATIVE_TOLERANCE


def probe_embedded_timecode(file_path: "Path | str", logger: Optional[logging.Logger] = None) -> Optional[str]:
    """Read the source file's own embedded SMPTE timecode (the ``tmcd``/
    ``timecode`` metadata track many screen recorders and capture cards
    write into mp4/mov files), if present.

    This is a **completely different piece of metadata** from each
    stream's ``start_time`` (see :func:`probe_media_timing` /
    :class:`MediaTiming.video_start_time_seconds`): ``start_time``
    reflects the container's own presentation timestamps and is almost
    always ``0.0`` for an ordinary mp4/mov, while this embedded timecode
    track records what timecode the recording software itself burned in
    -- and it is extremely common for that to start at ``01:00:00:00``
    even though ``start_time`` is still ``0.0`` for the very same file.
    Confusing the two (i.e. using ``video_start_time_seconds`` as a proxy
    for "the file's real starting timecode") silently reports a real
    ``01:00:00:00`` offset as ``0.0``.

    Returns ``None`` if ffprobe is unavailable, times out, or the file
    has no embedded timecode metadata (the common case for ordinary
    consumer video -- ``00:00:00:00``/no offset is then the correct
    assumption).
    """
    log = logger or _logger
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream_tags=timecode:format_tags=timecode",
        "-of", "default=noprint_wrappers=1",
        str(file_path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError:
        log.warning("ffprobe not found on PATH; skipping embedded-timecode probe.")
        return None
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out after %ss; skipping embedded-timecode probe.", FFPROBE_TIMEOUT_SECONDS)
        return None

    if result.returncode != 0:
        return None

    for line in (result.stdout or "").splitlines():
        if line.startswith("TAG:timecode="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    return None


_TIMECODE_STRING_RE = re.compile(r"^(\d+):(\d+):(\d+)[:;](\d+)$")


def timecode_string_to_seconds(timecode: str, fps: float) -> Optional[float]:
    """Parse an ``HH:MM:SS:FF`` (or drop-frame ``HH:MM:SS;FF``) timecode
    string -- as returned by :func:`probe_embedded_timecode` -- into
    seconds, given the file's own nominal fps.

    Returns ``None`` if ``timecode`` doesn't match the expected shape or
    ``fps`` is invalid. Drop-frame vs. non-drop-frame counting isn't
    distinguished here (both separators are accepted and treated the
    same); this is exact for whole-number frame rates like 24/25/30fps
    and only introduces sub-frame-scale imprecision for 29.97/59.94,
    which downstream re-quantizes to an exact frame count anyway.
    """
    if not timecode or fps <= 0:
        return None
    match = _TIMECODE_STRING_RE.match(timecode.strip())
    if not match:
        return None
    hh, mm, ss, ff = (int(group) for group in match.groups())
    total_frames = (hh * 3600 + mm * 60 + ss) * round(fps) + ff
    return total_frames / fps


def _parse_frame_rate(value: Optional[str]) -> Optional[float]:
    """Parse an ffprobe ``"num/den"`` frame-rate string into a float."""
    if not value or "/" not in value:
        return None
    num_str, _, den_str = value.partition("/")
    try:
        num, den = float(num_str), float(den_str)
    except ValueError:
        return None
    if den == 0:
        return None
    return num / den


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
