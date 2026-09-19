"""Video analysis pipeline (Version 2).

    Video -> Audio -> Whisper -> Frames -> AnalysisResult

Each stage below is its own class with a single responsibility, so future
versions can swap or extend any one of them without touching the others:

- :class:`video.video_importer.VideoImporter` -- validates a file and
  detects duration/fps/resolution/frame count.
- :class:`video.audio_extractor.AudioExtractor` -- pulls a mono 16kHz WAV
  track out of the video into a temp file.
- :class:`video.transcriber.WhisperTranscriber` -- runs faster-whisper with
  automatic language detection and precise timestamps.
- :class:`video.frame_extractor.FrameExtractor` -- samples preview frames
  at a configurable interval.
- :class:`video.analysis_pipeline.AnalysisPipeline` -- runs all four stages
  in order and returns a single :class:`models.analysis_result.AnalysisResult`.

Version 2 scope is intentionally limited to *analysis only*: this package
never edits video, never calls Gemini, and never talks to DaVinci Resolve.
"""
