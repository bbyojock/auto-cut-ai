"""Progress reporting model for the AI Progress Window (Version 4, Feature 2).

:class:`GenerationStage` enumerates every stage of the combined
Video -> Audio -> Whisper -> Frames -> Prompt -> AI -> Parse -> Validate ->
Simulate pipeline (see the Version 4 spec) in the exact order the pipeline
runs them, so a UI can render a simple ordered checklist. Emitting a
:class:`ProgressEvent` never blocks and never touches Tk directly --
callers (background threads) hand events to the UI thread through a
thread-safe queue, exactly like every other background operation in
AutoCutAI (see e.g. ``ui.pages.chat_page.ChatPage``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class GenerationStage(str, Enum):
    """One step of the end-to-end analysis + AI editing pipeline."""

    LOADING_VIDEO = "loading_video"
    EXTRACTING_AUDIO = "extracting_audio"
    RUNNING_WHISPER = "running_whisper"
    EXTRACTING_FRAMES = "extracting_frames"
    PREPARING_PROMPT = "preparing_prompt"
    GENERATING_EDIT_PLAN = "generating_edit_plan"
    PARSING_JSON = "parsing_json"
    VALIDATION = "validation"
    SIMULATION = "simulation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


# Display order + label, single source of truth for the progress window's
# checklist (see ui/widgets/progress_dialog.py). CANCELLED/ERROR are
# terminal-only states and are deliberately not part of the checklist.
STAGE_ORDER = (
    GenerationStage.LOADING_VIDEO,
    GenerationStage.EXTRACTING_AUDIO,
    GenerationStage.RUNNING_WHISPER,
    GenerationStage.EXTRACTING_FRAMES,
    GenerationStage.PREPARING_PROMPT,
    GenerationStage.GENERATING_EDIT_PLAN,
    GenerationStage.PARSING_JSON,
    GenerationStage.VALIDATION,
    GenerationStage.SIMULATION,
    GenerationStage.COMPLETED,
)

STAGE_LABELS = {
    GenerationStage.LOADING_VIDEO: "Loading Video",
    GenerationStage.EXTRACTING_AUDIO: "Extracting Audio",
    GenerationStage.RUNNING_WHISPER: "Running Whisper",
    GenerationStage.EXTRACTING_FRAMES: "Extracting Frames",
    GenerationStage.PREPARING_PROMPT: "Preparing Prompt",
    GenerationStage.GENERATING_EDIT_PLAN: "Generating Edit Plan",
    GenerationStage.PARSING_JSON: "Parsing JSON",
    GenerationStage.VALIDATION: "Validation",
    GenerationStage.SIMULATION: "Simulation",
    GenerationStage.COMPLETED: "Completed",
    GenerationStage.CANCELLED: "Cancelled",
    GenerationStage.ERROR: "Error",
}


@dataclass(slots=True)
class ProgressEvent:
    """One progress update, handed from a worker thread to the UI thread.

    Attributes:
        stage: Which pipeline stage this event describes.
        message: Short, human-readable detail (e.g. "Received 512 chars").
        fraction: Optional 0.0-1.0 completion estimate *within* this stage
            (e.g. streamed characters vs. an expected total). ``None`` when
            no meaningful estimate is available -- the UI then shows an
            indeterminate/"working" indicator for that stage instead.
        elapsed_seconds: Wall-clock time since generation started.
        eta_seconds: Best-effort estimated remaining time, or ``None`` if
            it can't be estimated yet (e.g. before enough data has streamed
            in to extrapolate from).
    """

    stage: GenerationStage
    message: str = ""
    fraction: Optional[float] = None
    elapsed_seconds: float = 0.0
    eta_seconds: Optional[float] = None

    @property
    def stage_label(self) -> str:
        return STAGE_LABELS.get(self.stage, self.stage.value)

    @property
    def stage_index(self) -> int:
        """0-based index of :attr:`stage` within :data:`STAGE_ORDER`, or -1."""
        try:
            return STAGE_ORDER.index(self.stage)
        except ValueError:
            return -1
