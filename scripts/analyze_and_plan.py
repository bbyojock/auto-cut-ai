#!/usr/bin/env python3
"""Standalone integration script for the full Version 2 + Version 3 pipeline.

    AnalysisPipeline.run() -> AnalysisResult -> EditPlanner.create_edit_plan() -> EditPlan

This is the "does the whole pipeline actually work end to end" check:
video in, EditPlan out, nothing edited or rendered. Run from anywhere:

    python scripts/analyze_and_plan.py path/to/video.mp4
    python scripts/analyze_and_plan.py path/to/video.mp4 --target-length 120 --interval 3

It reuses whichever provider is currently active (Settings > "Active"),
including its Base URL, model, and API keys, and whichever Whisper
backend/model are configured -- read through the exact same
:class:`config.config_manager.ConfigManager` and
:class:`ai.provider_factory.ProviderFactory` the GUI app itself uses.
Nothing here is hardcoded: no provider, no model, no URL.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this script directly (``python scripts/analyze_and_plan.py``)
# without having to install the package or set PYTHONPATH manually.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.claude_provider import ClaudeProvider  # noqa: E402
from ai.edit_planner import EditPlanner  # noqa: E402
from ai.gemini_provider import GeminiProvider  # noqa: E402
from ai.key_manager import APIKeyManager  # noqa: E402
from ai.openai_compatible_provider import OpenAICompatibleProvider  # noqa: E402
from ai.provider_factory import ProviderFactory  # noqa: E402
from config.config_manager import ConfigManager  # noqa: E402
from core.exceptions import AutoCutAIError  # noqa: E402
from utils.constants import (  # noqa: E402
    PROVIDER_REQUIRES_API_KEY,
    PROVIDER_TYPE_ANTHROPIC,
    PROVIDER_TYPE_GEMINI,
    PROVIDER_TYPE_OPENAI_COMPATIBLE,
)
from video.analysis_pipeline import AnalysisPipeline  # noqa: E402
from video.transcriber import WhisperTranscriber  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full AutoCutAI analysis + AI edit-planning pipeline on one video."
    )
    parser.add_argument("video", type=Path, help="Path to a video file (mp4/mov/mkv/avi).")
    parser.add_argument(
        "--interval", type=float, default=None,
        help="Preview frame sampling interval, in seconds. Defaults to the configured value.",
    )
    parser.add_argument(
        "--target-length", type=float, default=None,
        help="Desired final video length in seconds. Omit to let the AI choose.",
    )
    return parser.parse_args()


def _build_provider_factory() -> ProviderFactory:
    """Mirror app.py's provider registration -- see its docstring for why
    this lives in one place per provider *type*, never per named provider."""
    factory = ProviderFactory()
    factory.register_type(
        PROVIDER_TYPE_GEMINI,
        lambda s: GeminiProvider(APIKeyManager(s.api_keys), base_url=s.base_url or None,
                                  timeout_seconds=s.timeout_seconds, max_retries=s.max_retries),
    )
    factory.register_type(
        PROVIDER_TYPE_OPENAI_COMPATIBLE,
        lambda s: OpenAICompatibleProvider(
            display_name=s.display_name or s.provider_id.title(), base_url=s.base_url,
            key_manager=APIKeyManager(s.api_keys),
            requires_api_key=PROVIDER_REQUIRES_API_KEY.get(s.provider_id, True),
            timeout_seconds=s.timeout_seconds, max_retries=s.max_retries,
        ),
    )
    factory.register_type(
        PROVIDER_TYPE_ANTHROPIC,
        lambda s: ClaudeProvider(base_url=s.base_url, key_manager=APIKeyManager(s.api_keys),
                                  timeout_seconds=s.timeout_seconds, max_retries=s.max_retries),
    )
    return factory


def main() -> int:
    args = parse_args()

    config_manager = ConfigManager()
    config = config_manager.load()

    active_settings = config.get_provider(config.last_used.provider)
    if active_settings is None or not active_settings.enabled:
        print(
            "No active AI provider is enabled. Open the app's Settings page, configure a "
            "provider (e.g. Gemini), mark it 'Active', and save before running this script.",
            file=sys.stderr,
        )
        return 1
    if not active_settings.model:
        print(f"Provider '{active_settings.display_name}' has no model configured.", file=sys.stderr)
        return 1

    print(f"Step 1/2: Running the Version 2 analysis pipeline on '{args.video}' ...")
    try:
        transcriber = WhisperTranscriber(model_size=config.whisper.model_size, backend=config.whisper.backend)
        interval = args.interval if args.interval is not None else config.frame_interval_seconds
        analysis = AnalysisPipeline(transcriber=transcriber).run(str(args.video), frame_interval_seconds=interval)
    except AutoCutAIError as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"  -> {analysis.video.duration_seconds:.1f}s, language={analysis.transcript.language}, "
        f"{len(analysis.transcript.segments)} transcript segment(s), {len(analysis.frames)} frame(s)."
    )

    print(
        f"Step 2/2: Running the Version 3 AI editing brain "
        f"(provider={active_settings.display_name}, model={active_settings.model}) ..."
    )
    provider_factory = _build_provider_factory()
    try:
        provider = provider_factory.create_from_settings(active_settings)
    except AutoCutAIError as exc:
        print(f"Could not activate provider '{active_settings.provider_id}': {exc}", file=sys.stderr)
        return 1

    planner = EditPlanner(provider=provider, model=active_settings.model)

    try:
        plan = planner.create_edit_plan(analysis, target_length_seconds=args.target_length)
    except AutoCutAIError as exc:
        print(f"Edit planning failed: {exc}", file=sys.stderr)
        return 1

    print()
    print("=== EditPlan ===")
    print(plan.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
