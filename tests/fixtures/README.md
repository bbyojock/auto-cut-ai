# bedwars_test.mp4

A ~56-second synthetic test video used by `tests/test_v46_features_manual.py`
to test Feature 8 (adaptive frame sampling) and Feature 7 (event
recognition) against a **real** video file, not mocked data.

Built entirely with `ffmpeg` (lavfi video sources + the `flite` text-to-speech
filter for real synthesized speech), structured to mimic the reported bug
scenario:

| Time      | Visual                          | Speech (via flite TTS)                                              |
|-----------|----------------------------------|-----------------------------------------------------------------------|
| 0-15s     | Static gray (low motion)         | "lets gather some wood and build the bridge slowly"                  |
| 15-25s    | `testsrc2` (high motion)         | "oh he got the kill nice the bed is destroyed now"                   |
| 25-40s    | Static color (low motion)        | "just walking around collecting more resources slowly and quietly"   |
| 40-48s    | `testsrc2` (high motion)         | "clutch one versus two lets go lets go"                              |
| 48-56s    | Static color (low motion)        | "gg we won the game great game everyone well played"                 |

This lets tests assert, against real pixel data, that adaptive sampling
produces denser frame sampling and a higher measured motion score during
the "combat" windows than the "boring gameplay" windows -- and that
`ai.event_recognizer.EventRecognizer` correctly picks out kill/bed_destroyed/
clutch/victory events.

**Whisper was not used to transcribe this file.** This sandbox's network
egress does not allow reaching `huggingface.co`, so the `faster-whisper`
model could not be downloaded here. Tests that need a transcript use a
scripted `Transcript` whose text matches exactly what was passed to `flite`
above, as a stand-in for what Whisper would very likely have produced.
Regenerating this fixture (or reproducing this limitation) requires only
`ffmpeg` built with `--enable-libflite`.

Note (timestamp-pipeline consistency fix): `ffprobe` on this file shows a
small, real mismatch between its streams' `start_time` -- video starts at
`0.022982s`, audio at `0.000000s`. This was not intentionally built into
the fixture; it's a byproduct of how `ffmpeg`/`flite` muxed it, and it's
exactly the kind of real-world A/V start_time skew described in the
"timestamp consistency" investigation. `tests/test_timestamp_sync_manual.py`
uses this fixture specifically to verify that small, real-world offset is
detected and corrected.

# av_offset_test.mp4

A 10-second fixture built specifically for the timestamp-pipeline
consistency fix, with a **deliberate, large (~1.976s)** delay applied to
the audio stream's start_time relative to the video stream, so the
correction is obvious and easy to verify by eye (as opposed to
`bedwars_test.mp4`'s much smaller ~23ms real-world skew).

Built with:

```
ffmpeg -f lavfi -i "testsrc2=size=160x120:rate=25:duration=10" \
       -itsoffset 2.0 -f lavfi -i "sine=frequency=440:duration=8" \
       -map 0:v -map 1:a -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
       av_offset_test.mp4
```

`ffprobe` confirms: video `start_time=0.000000`, audio `start_time=1.976009`
(the `-itsoffset 2.0` doesn't land at exactly 2.0s because of container/
timebase rounding -- this is normal and is exactly why the fix reads the
real, measured offset from `ffprobe` rather than assuming any fixed value).

# vfr_test.mp4

A 6-second fixture built specifically to test variable-frame-rate (VFR)
detection. Built by dropping 2 out of every 3 frames from a 30fps source
and stretching timestamps, producing a stream where `r_frame_rate` (10/1)
and `avg_frame_rate` (900/89 ≈ 10.11) disagree by just over 1%:

```
ffmpeg -f lavfi -i "testsrc2=size=160x120:rate=30:duration=6" \
       -vf "select='not(mod(n\,3))',setpts=N/(10*TB)" -vsync vfr \
       -c:v libx264 -pix_fmt yuv420p vfr_test.mp4
```

Used to verify VFR is *flagged* (`VideoProject.is_vfr`); the fix does not
claim to correct OpenCV's own frame-seek accuracy on VFR content -- see
the main report's "확실하지 않음"/unverified section.

# Resolve Free XML export -- AUDIO fix (V5)

`tests/test_v5_xml_audio_fix_manual.py` reproduces the exact bug report
(source `2026-09-11 17-16-13.mp4` @ 60fps, KEEP ranges `0-480`,
`1050-2760`, `3270-3720`, `6180-8220` in source frames) and asserts the
generated XML structure directly:

- video clipitem count == audio clipitem count (one pair per KEEP range)
- every pair's `<start>/<end>/<in>/<out>` match exactly, for **all**
  clips, not just the first
- every audio clipitem has a `<sourcetrack><mediatype>audio</mediatype>
  <trackindex>1</trackindex></sourcetrack>`
- every video/audio pair has matching `<link>` entries tying them
  together as one A/V clip
- the shared `<file>`'s `<media><audio>` carries real
  `<samplecharacteristics>`/`<channelcount>` instead of an empty
  `<audio/>`

This fully verifies the XML *structure* without needing Resolve
installed. It does **not** replace an actual DaVinci Resolve 20 Free
`File > Import > Timeline` test -- do that by hand:

1. Run `python3 tests/test_v5_xml_audio_fix_manual.py` (or export a real
   XML via `services.resolve_export_service.ResolveExportService
   .export_xml(...)` against your own EditPlan/source video) to produce
   a `.xml` file next to your real source `.mp4`.
2. In Resolve 20 Free: `File > Import > Timeline...`, pick the `.xml`,
   and make sure "Source clips are relative to the imported XML file"
   (or the equivalent "Automatically import source clips into media
   pool" option) is left on so it finds the referenced `.mp4` by its
   `<pathurl>`.
3. Open the new timeline. On the timeline toolbar, enable audio
   waveforms (right-click the A1 track header -> "Waveform" or the
   timeline view options) so silent vs. non-silent clips are visible at
   a glance without needing to play each one.
4. Scrub through **every** KEEP clip, not just the first -- click into
   clip 2 (source `1050-2760`) specifically, since that's the exact
   clip the bug report called out, and confirm both a visible waveform
   and audible sound.
5. In the Mixer or by soloing A1, confirm no clip's fader/mute state
   differs from the others (rules out a per-clip mute rather than a
   missing-audio-data issue).
6. If everything through step 4 was silent before this fix and is now
   audible on every clip, the fix is confirmed end-to-end.

Steps 2-6 have not been run against a real Resolve 20 Free installation
in this sandbox (no GUI/Resolve available here) -- only the XML
structure itself (step 1's output) has been verified automatically.
