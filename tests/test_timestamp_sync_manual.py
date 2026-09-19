"""Manual regression test for the timestamp-pipeline consistency fix.

Run: python3 tests/test_timestamp_sync_manual.py

Covers the mechanism identified and fixed in this change:

    A verified audio/video stream start_time mismatch (ffprobe-confirmed on
    the existing bedwars_test.mp4 fixture, ~23ms) causes Whisper timestamps
    -- which are relative to the *audio* stream's own start -- to land at
    the wrong point on the video's own frame timeline (the one
    FrameExtractor/ContextBuilder/EditPlan all assume). This is corrected
    by video.timeline_sync.apply_av_offset_to_transcript.

Also covers the two supporting, non-fatal cross-checks added alongside it:
- ffprobe-vs-OpenCV duration cross-check (VideoImporter).
- Variable-frame-rate (VFR) detection/flagging (informational only; VFR's
  effect on OpenCV's own CAP_PROP_POS_MSEC frame-seek accuracy is NOT
  fixed by this change -- see the final report's "not yet verified"
  section).

Fixtures used (see tests/fixtures/README.md for exactly how each was built):
- bedwars_test.mp4:      real small A/V start_time mismatch (~23ms), already
                          existed for other tests; used here for the
                          "realistic, small" end of the correction.
- av_offset_test.mp4:    a deliberate, large (~2s) audio start_time delay,
                          built specifically for this fix, to make the
                          correction obvious and easy to verify by eye.
- vfr_test.mp4:          r_frame_rate != avg_frame_rate by >1%, built
                          specifically for this fix, to verify VFR is
                          detected and flagged (not "fixed" -- flagged).

Whisper itself is NOT exercised here (this sandbox cannot reach
huggingface.co to download the model -- same limitation called out in
tests/test_v46_features_manual.py). A minimal fake transcriber stands in
for WhisperTranscriber in the full-pipeline test below; everything it
returns is then run through the REAL AnalysisPipeline / VideoImporter /
video.timeline_sync code, so the correction itself is exercised for real,
only the actual speech-to-text model call is stubbed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.transcript import Transcript, TranscriptSegment
from video.analysis_pipeline import AnalysisPipeline
from video.audio_extractor import AudioExtractor
from video.frame_extractor import FrameExtractor
from video.timeline_sync import (
    apply_av_offset_to_transcript,
    cross_check_duration,
    probe_media_timing,
)
from video.video_importer import VideoImporter

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BEDWARS_VIDEO = FIXTURES / "bedwars_test.mp4"
AV_OFFSET_VIDEO = FIXTURES / "av_offset_test.mp4"
VFR_VIDEO = FIXTURES / "vfr_test.mp4"


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


# ---------------------------------------------------------------------
# 1. ffprobe cross-check finds the REAL, small A/V mismatch that started
#    this investigation (bedwars_test.mp4: video starts at 0.023s, audio
#    at 0.000s -- confirmed independently with a raw ffprobe call during
#    this investigation).
# ---------------------------------------------------------------------
bedwars_timing = probe_media_timing(BEDWARS_VIDEO)
check("ffprobe cross-check runs successfully on a real file", bedwars_timing.probed)
check(
    "bedwars_test.mp4 has a small (~23ms) real A/V start_time offset",
    bedwars_timing.av_sync_offset_seconds is not None and -0.030 < bedwars_timing.av_sync_offset_seconds < -0.015,
)
check("bedwars_test.mp4 is correctly identified as constant-frame-rate (not VFR)", bedwars_timing.is_vfr is False)

# ---------------------------------------------------------------------
# 2. A deliberate, large (~2s) audio start_time delay is detected and the
#    correction shifts a transcript by (almost) exactly that amount.
# ---------------------------------------------------------------------
offset_timing = probe_media_timing(AV_OFFSET_VIDEO)
check("Deliberate-offset fixture probes successfully", offset_timing.probed)
check(
    "Deliberate ~2s audio delay is measured accurately by ffprobe",
    offset_timing.av_sync_offset_seconds is not None and 1.9 < offset_timing.av_sync_offset_seconds < 2.05,
)

raw_transcript = Transcript(
    language="en", language_probability=0.95,
    segments=[TranscriptSegment(0.0, 1.0, "first"), TranscriptSegment(3.0, 4.0, "second")],
)
corrected = apply_av_offset_to_transcript(raw_transcript, offset_timing.av_sync_offset_seconds)
expected_shift = offset_timing.av_sync_offset_seconds
check(
    "Transcript segments are shifted onto the video timeline by the measured offset",
    abs(corrected.segments[0].start_seconds - (0.0 + expected_shift)) < 0.01
    and abs(corrected.segments[1].end_seconds - (4.0 + expected_shift)) < 0.01,
)
check("Original transcript object is left untouched (no accidental mutation)", raw_transcript.segments[0].start_seconds == 0.0)

# A negligible/zero offset must be a true no-op (same object back, no drift
# introduced into every timestamp for nothing).
untouched = apply_av_offset_to_transcript(raw_transcript, 0.0)
check("Zero offset returns the exact same Transcript object (no-op)", untouched is raw_transcript)
untouched_none = apply_av_offset_to_transcript(raw_transcript, None)
check("Unknown (None) offset returns the exact same Transcript object (no-op)", untouched_none is raw_transcript)

# ---------------------------------------------------------------------
# 3. VFR is detected and flagged (informational), never silently ignored.
# ---------------------------------------------------------------------
vfr_timing = probe_media_timing(VFR_VIDEO)
check("VFR fixture probes successfully", vfr_timing.probed)
check("A real VFR file (r_frame_rate != avg_frame_rate by >1%) is flagged", vfr_timing.is_vfr is True)
non_vfr_timing = probe_media_timing(BEDWARS_VIDEO)
check("A genuinely CFR file is NOT flagged as VFR (no false positives)", non_vfr_timing.is_vfr is False)

# ---------------------------------------------------------------------
# 4. Duration cross-check: ffprobe's duration is preferred only when it
#    genuinely disagrees with OpenCV's beyond tolerance; otherwise OpenCV's
#    number is left completely alone (no unnecessary behavior change).
# ---------------------------------------------------------------------
check(
    "Duration cross-check leaves OpenCV's duration alone when ffprobe agrees",
    cross_check_duration(56.0, bedwars_timing) == 56.0,
)
check(
    "Duration cross-check overrides a badly wrong OpenCV duration with ffprobe's",
    cross_check_duration(9999.0, bedwars_timing) != 9999.0,
)


class _FailedProbeTiming:
    """Stand-in for a MediaTiming where ffprobe could not run at all."""
    probed = False
    format_duration_seconds = None


check(
    "Duration cross-check is a no-op when ffprobe itself is unavailable (never blocks import)",
    cross_check_duration(12.34, _FailedProbeTiming()) == 12.34,
)

# ---------------------------------------------------------------------
# 5. VideoImporter end-to-end: the real, small bedwars_test.mp4 offset is
#    picked up and stored on the VideoProject exactly as
#    AnalysisPipeline.run() expects to find it.
# ---------------------------------------------------------------------
project = VideoImporter().import_video(BEDWARS_VIDEO)
check("VideoImporter still reports the correct approximate duration", 54 < project.duration_seconds < 58)
check("VideoImporter records the video stream's start_time", abs(project.video_start_time_seconds - 0.022982) < 0.001)
check("VideoImporter records the audio stream's start_time", project.audio_start_time_seconds == 0.0)
check("VideoImporter does not flag a genuinely CFR file as VFR", project.is_vfr is False)

offset_project = VideoImporter().import_video(AV_OFFSET_VIDEO)
check(
    "VideoImporter picks up the large deliberate audio offset too",
    offset_project.audio_start_time_seconds is not None and offset_project.audio_start_time_seconds > 1.9,
)


# ---------------------------------------------------------------------
# 6. Full pipeline integration: AnalysisPipeline actually applies the
#    correction to whatever the transcriber returns -- not just the
#    standalone helper function in isolation. A fake transcriber stands in
#    for Whisper (network-blocked in this sandbox); everything else
#    (VideoImporter, AudioExtractor, FrameExtractor, the offset math) is
#    the real production code.
# ---------------------------------------------------------------------
class _FakeTranscriberReturningZeroBasedSegments:
    """Simulates Whisper reporting a word right at the start of the WAV
    file -- i.e. at the *audio* stream's start_time, not the video's."""

    def transcribe(self, audio_path):
        return Transcript(
            language="en", language_probability=0.99,
            segments=[TranscriptSegment(0.0, 0.5, "word_at_wav_start")],
        )


pipeline = AnalysisPipeline(
    importer=VideoImporter(),
    audio_extractor=AudioExtractor(),
    transcriber=_FakeTranscriberReturningZeroBasedSegments(),
    frame_extractor=FrameExtractor(),
)
result = pipeline.run(AV_OFFSET_VIDEO, frame_interval_seconds=2.0)
check(
    "AnalysisPipeline shifts the fake transcript by the real measured audio/video offset",
    result.transcript.segments[0].start_seconds > 1.9,
)
check(
    "The shifted segment now lines up with the video's own frame timeline, not the raw WAV timeline",
    abs(result.transcript.segments[0].start_seconds - offset_project.audio_start_time_seconds) < 0.05,
)

# Sanity: running the same pipeline against a file with a negligible/no
# real offset must NOT introduce a spurious shift.
pipeline_no_offset = AnalysisPipeline(
    importer=VideoImporter(),
    audio_extractor=AudioExtractor(),
    transcriber=_FakeTranscriberReturningZeroBasedSegments(),
    frame_extractor=FrameExtractor(),
)
result_bedwars = pipeline_no_offset.run(BEDWARS_VIDEO, frame_interval_seconds=2.0)
check(
    # start_seconds was 0.0 and the real offset here is -0.023s; clamped to
    # 0.0 (a segment can never start before the first video frame) -- but
    # end_seconds (0.5 -> ~0.477) is NOT clamped and shows the shift really
    # was applied, not skipped.
    "A real-but-tiny (~23ms) offset still gets applied (clamped start, shifted end)",
    result_bedwars.transcript.segments[0].start_seconds == 0.0
    and 0.47 < result_bedwars.transcript.segments[0].end_seconds < 0.48,
)

print("\nAll timestamp-sync regression checks passed.")
