"""Application-wide constants.

Keeping these in one place avoids magic strings/numbers scattered across the
codebase and makes future changes (new provider, new theme, ...) a one-line
edit instead of a search-and-replace.
"""

from __future__ import annotations

APP_NAME = "AutoCutAI"
APP_VERSION = "5.6.0"

# --- Filesystem layout -----------------------------------------------------
CONFIG_DIR_NAME = "config"
CONFIG_FILE_NAME = "config.json"
LOGS_DIR_NAME = "logs"
LOG_FILE_NAME = "app.log"

# --- Window defaults ---------------------------------------------------------
DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 720
MIN_WINDOW_WIDTH = 900
MIN_WINDOW_HEIGHT = 600

# --- Theme -------------------------------------------------------------------
THEME_DARK = "dark"
THEME_LIGHT = "light"
THEME_SYSTEM = "system"
AVAILABLE_THEMES = (THEME_DARK, THEME_LIGHT, THEME_SYSTEM)
DEFAULT_THEME = THEME_DARK
DEFAULT_COLOR_THEME = "blue"

# --- AI providers --------------------------------------------------------
PROVIDER_GEMINI = "gemini"
DEFAULT_PROVIDER = PROVIDER_GEMINI

# --- Gemini specific ---------------------------------------------------------
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
AVAILABLE_GEMINI_MODELS = (
    "gemini-3.6-flash",
)
GEMINI_REQUEST_TIMEOUT_SECONDS = 180
GEMINI_HEALTH_CHECK_TIMEOUT_SECONDS = 30
GEMINI_STREAM_TIMEOUT_SECONDS = 300
GEMINI_MAX_RETRIES_PER_KEY = 1

# --- Outdated model migration (v4 pre-work) ----------------------------------
# Model names known to be retired/outdated. If a user's saved config still
# points at one of these, Settings offers a one-click migration to the
# mapped replacement -- the field is pre-filled, but nothing is overwritten
# until the user explicitly saves (see ui/pages/settings_page.py).
OUTDATED_MODEL_REPLACEMENTS = {
    "gemini-2.5-flash": DEFAULT_GEMINI_MODEL,
    "gemini-2.5-pro": DEFAULT_GEMINI_MODEL,
    "gemini-2.0-flash": DEFAULT_GEMINI_MODEL,
    "gemini-1.0-pro": DEFAULT_GEMINI_MODEL,
    "gemini-pro": DEFAULT_GEMINI_MODEL,
    "gemini-1.5-flash": DEFAULT_GEMINI_MODEL,
    "gemini-1.5-flash-001": DEFAULT_GEMINI_MODEL,
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-001": "gemini-2.5-pro",
}

# --- Navigation pages ------------------------------------------------------
PAGE_HOME = "home"
PAGE_CHAT = "chat"
PAGE_EDIT_PLAN = "edit_plan"
PAGE_SETTINGS = "settings"
PAGE_DIAGNOSTICS = "diagnostics"
PAGE_LOGS = "logs"
PAGE_PERSONAL_PROFILE = "personal_profile"  # Version 4.5, Feature 15

# --- Video analysis pipeline (v2) -------------------------------------------
SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi")
DEFAULT_FRAME_INTERVAL_SECONDS = 5.0
TEMP_DIR_NAME = "autocutai_temp"

# --- Whisper (v2) --------------------------------------------------------------
DEFAULT_WHISPER_MODEL_SIZE = "base"
DEFAULT_WHISPER_COMPUTE_TYPE = "int8"
WHISPER_AUDIO_SAMPLE_RATE_HZ = 16000

# --- Whisper GPU/CPU runtime management (stability update) ------------------
WHISPER_BACKEND_AUTO = "auto"
WHISPER_BACKEND_GPU = "gpu"
WHISPER_BACKEND_CPU = "cpu"
AVAILABLE_WHISPER_BACKENDS = (WHISPER_BACKEND_AUTO, WHISPER_BACKEND_GPU, WHISPER_BACKEND_CPU)
DEFAULT_WHISPER_BACKEND = WHISPER_BACKEND_AUTO
# Compute type used only when the runtime manager decides a verified GPU
# should be used; CPU keeps using DEFAULT_WHISPER_COMPUTE_TYPE ("int8")
# exactly as before, so behavior on machines without a GPU never changes.
DEFAULT_WHISPER_GPU_COMPUTE_TYPE = "float16"
# Substrings that, if present in an exception raised while loading or
# running Whisper on a GPU device, identify a GPU/CUDA runtime problem
# (as opposed to an unrelated transcription error) so the CPU fallback
# path is used only when it's actually the GPU's fault.
GPU_RUNTIME_ERROR_SIGNATURES = ("cublas", "cudnn", "cuda", ".dll", "libcu", "shared object file")

# --- Timestamp pipeline / A-V sync (timestamp consistency fix) --------------
# How long to wait for the `ffprobe` cross-check before giving up on it and
# falling back to OpenCV-only metadata (ffprobe missing/hanging must never
# block importing a video).
FFPROBE_TIMEOUT_SECONDS = 15
# If OpenCV's total_frames/fps duration and ffprobe's own container duration
# disagree by more than this, prefer ffprobe's duration and log a warning
# (this is the authoritative, container-level number; OpenCV's is a derived
# estimate that can be wrong for VFR files).
DURATION_MISMATCH_WARNING_THRESHOLD_SECONDS = 0.5
# A video is flagged as variable-frame-rate when its container's average
# frame rate (avg_frame_rate) differs from its nominal frame rate
# (r_frame_rate) by more than this fraction. VFR content makes any
# frame-index/fps-based timestamp math (including OpenCV's own duration
# and seek calculations) unreliable, so this is surfaced as a warning
# rather than silently trusted.
VFR_FRAME_RATE_RELATIVE_TOLERANCE = 0.01
# Below this, an audio/video start_time mismatch is treated as measurement
# noise, not a real offset worth correcting.
AV_SYNC_OFFSET_EPSILON_SECONDS = 0.001

# --- AI editing brain (v3) ---------------------------------------------------
EDIT_ACTION_KEEP = "keep"
EDIT_ACTION_REMOVE = "remove"
# Version 4.6, Feature 5/7/8/9 core fix: a third action between "keep the
# whole thing" and "delete the whole thing" -- COMPRESS plays the segment
# back at a higher speed (see ``compression_speed_factor`` on
# ``models.edit_plan.EditPlanSegment``) instead of a binary decision. This
# is what lets ordinary/repetitive gameplay (long building, farming,
# navigation stretches) be shortened while staying visible and connected,
# instead of the AI being forced into "keep the whole boring stretch" or
# "remove the whole boring stretch" -- the exact bug report that prompted
# this feature.
EDIT_ACTION_COMPRESS = "compress"
VALID_EDIT_ACTIONS = (EDIT_ACTION_KEEP, EDIT_ACTION_REMOVE, EDIT_ACTION_COMPRESS)
DEFAULT_EDIT_CONFIDENCE = 0.5
# How far a plan's total coverage may drift from the source duration, or two
# segments may overlap, before ResponseParser raises a (non-fatal) warning.
EDIT_PLAN_COVERAGE_TOLERANCE_SECONDS = 0.5

# --- DaVinci Resolve cut-editing integration (v5) ----------------------------
# Version 5: turns an EditPlan into a real Resolve timeline instead of stopping
# at JSON. See davinci/edit_plan_applier.py and davinci/timeline_editor.py.
RESOLVE_EDIT_TIMELINE_PREFIX = "AutoCutAI_Edit"
# Two REMOVE segments closer than this are treated as touching/overlapping
# (merged into one) rather than leaving a slivered, sub-frame KEEP segment
# between them that would just be re-split and re-deleted for no benefit.
RESOLVE_SEGMENT_MERGE_EPSILON_SECONDS = 0.02
# A REMOVE segment shorter than this (after clamping to media bounds) is
# skipped rather than applied -- sub-frame-at-typical-fps noise, not a real
# edit decision, and not worth a split+delete on the timeline.
RESOLVE_MIN_REMOVE_DURATION_SECONDS = 0.01
# Fallback timeline frame rate used only if Resolve's own timeline/project
# settings don't report one (should not normally happen).
RESOLVE_FALLBACK_FRAME_RATE = 30.0

# Resolve Free XML export (Section 20 of the V5 spec): default output file
# is "<source_name>_AutoCutAI.xml" next to the source video.
RESOLVE_XML_EXPORT_FILENAME_SUFFIX = "_AutoCutAI"

# --- COMPRESS speed bounds (v4.6, Feature 5/9) -------------------------------
MIN_COMPRESSION_SPEED_FACTOR = 1.25
MAX_COMPRESSION_SPEED_FACTOR = 6.0
DEFAULT_COMPRESSION_SPEED_FACTOR = 2.0

# --- EditPlan validation & repair (v3.5) -------------------------------------
# Clips shorter than this are merged into a neighboring segment rather than
# kept as their own (near-unusable) cut.
MIN_EDIT_CLIP_SECONDS = 0.3
# Timestamp differences smaller than this are treated as equal (rounding
# noise), used when detecting gaps/overlaps/zero-length segments.
TIMESTAMP_EPSILON_SECONDS = 0.01

# --- Prompt styles / templates (v3.5) ----------------------------------------
PROMPT_STYLE_GENERAL = "general"
PROMPT_STYLE_GAMING = "gaming"
PROMPT_STYLE_VLOG = "vlog"
PROMPT_STYLE_TUTORIAL = "tutorial"
PROMPT_STYLE_PODCAST = "podcast"
PROMPT_STYLE_REACTION = "reaction"
PROMPT_STYLE_SHORT_FORM = "short_form"
# Version 4.6, Feature 9: two more presets to match the spec's named
# examples ("Documentary", "Minimal Cuts") that the original 7 didn't cover.
PROMPT_STYLE_DOCUMENTARY = "documentary"
PROMPT_STYLE_MINIMAL_CUTS = "minimal_cuts"
AVAILABLE_PROMPT_STYLES = (
    PROMPT_STYLE_GENERAL,
    PROMPT_STYLE_GAMING,
    PROMPT_STYLE_VLOG,
    PROMPT_STYLE_TUTORIAL,
    PROMPT_STYLE_PODCAST,
    PROMPT_STYLE_REACTION,
    PROMPT_STYLE_SHORT_FORM,
    PROMPT_STYLE_DOCUMENTARY,
    PROMPT_STYLE_MINIMAL_CUTS,
)
DEFAULT_PROMPT_STYLE = PROMPT_STYLE_GENERAL

# --- Multi-provider architecture (stability update) --------------------------
# provider_type identifies which AIProvider *implementation class*
# ai.provider_factory.ProviderFactory should instantiate for a given
# models.provider_settings.ProviderSettings entry. This is deliberately
# separate from provider_id (see below): several differently-configured
# provider_id entries (openrouter, openai, local_ai, a user's custom entry)
# can all share the same provider_type ("openai_compatible") because they
# speak the same wire format -- only base_url/api_keys/model differ.
PROVIDER_TYPE_GEMINI = "gemini"
PROVIDER_TYPE_OPENAI_COMPATIBLE = "openai_compatible"
PROVIDER_TYPE_ANTHROPIC = "anthropic"
AVAILABLE_PROVIDER_TYPES = (PROVIDER_TYPE_GEMINI, PROVIDER_TYPE_OPENAI_COMPATIBLE, PROVIDER_TYPE_ANTHROPIC)

PROVIDER_OPENROUTER = "openrouter"
PROVIDER_OPENAI = "openai"
PROVIDER_CLAUDE = "claude"
PROVIDER_GROQ = "groq"
PROVIDER_LOCAL_AI = "local_ai"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"  # generic custom OpenAI-compatible entry
# Every provider AutoCutAI ships a ready-made (but freely editable) entry
# for. Users can still add arbitrary extra entries of any provider_type.
BUILTIN_PROVIDER_IDS = (
    PROVIDER_GEMINI, PROVIDER_OPENROUTER, PROVIDER_OPENAI, PROVIDER_CLAUDE, PROVIDER_LOCAL_AI,
)
BUILTIN_PROVIDER_TYPES = {
    PROVIDER_GEMINI: PROVIDER_TYPE_GEMINI,
    PROVIDER_OPENROUTER: PROVIDER_TYPE_OPENAI_COMPATIBLE,
    PROVIDER_OPENAI: PROVIDER_TYPE_OPENAI_COMPATIBLE,
    PROVIDER_CLAUDE: PROVIDER_TYPE_ANTHROPIC,
    PROVIDER_LOCAL_AI: PROVIDER_TYPE_OPENAI_COMPATIBLE,
}
PROVIDER_DISPLAY_NAMES = {
    PROVIDER_GEMINI: "Gemini",
    PROVIDER_OPENROUTER: "OpenRouter",
    PROVIDER_OPENAI: "OpenAI",
    PROVIDER_CLAUDE: "Claude",
    PROVIDER_GROQ: "Groq",
    PROVIDER_LOCAL_AI: "Local AI (Ollama / LM Studio / vLLM)",
    PROVIDER_OPENAI_COMPATIBLE: "OpenAI-compatible API",
}
# Never hardcoded into a provider class -- these are just the starting
# values a brand-new ProviderSettings entry is pre-filled with, and every
# field (base_url, model, ...) stays a plain editable text field in
# Settings (see ui/pages/settings_page.py).
DEFAULT_PROVIDER_BASE_URLS = {
    PROVIDER_GEMINI: "https://generativelanguage.googleapis.com",
    PROVIDER_OPENROUTER: "https://openrouter.ai/api/v1",
    PROVIDER_OPENAI: "https://api.openai.com/v1",
    PROVIDER_CLAUDE: "https://api.anthropic.com",
    PROVIDER_LOCAL_AI: "http://localhost:11434/v1",
}
DEFAULT_PROVIDER_MODEL_HINTS = {
    PROVIDER_GEMINI: DEFAULT_GEMINI_MODEL,
    PROVIDER_OPENROUTER: "anthropic/claude-sonnet-4.5",
    PROVIDER_OPENAI: "gpt-5",
    PROVIDER_CLAUDE: "claude-sonnet-4-5",
    PROVIDER_LOCAL_AI: "llama3.1",
}
PROVIDER_REQUIRES_API_KEY = {
    PROVIDER_GEMINI: True,
    PROVIDER_OPENROUTER: True,
    PROVIDER_OPENAI: True,
    PROVIDER_CLAUDE: True,
    PROVIDER_LOCAL_AI: False,
}
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 30
DEFAULT_PROVIDER_MAX_RETRIES = 1
ANTHROPIC_API_VERSION = "2023-06-01"

# --- Planned AI providers (v3.5) -- architecture-ready, not implemented yet --
# Kept for backward compatibility with Version 3.5 (Groq and a generic
# "openai_compatible" placeholder remain registered as metadata-only until
# a user configures a real entry for them; every other provider in this
# tuple now has a full implementation -- see ai/provider_factory.py).
PLANNED_PROVIDERS = (
    PROVIDER_GROQ,
    PROVIDER_OPENAI_COMPATIBLE,
)

# --- Event recognition (v4.6, Feature 7) -------------------------------------
# A controlled vocabulary of event types the AI editing brain can reason
# about explicitly, seeded with Bedwars (the reported bug's test case) but
# generic enough to reuse for other game profiles. Kept here (not inside
# ai/event_recognizer.py) so utils.constants stays the single source of
# truth for every controlled string this codebase compares against.
EVENT_KILL = "kill"
EVENT_DEATH = "death"
EVENT_BED_DESTROYED = "bed_destroyed"
EVENT_FINAL_KILL = "final_kill"
EVENT_CLUTCH = "clutch"
EVENT_COMBAT = "combat"
EVENT_ITEM_OBTAINED = "item_obtained"
EVENT_SHOP = "shop"
EVENT_INVENTORY = "inventory"
EVENT_BUILDING = "building"
EVENT_EXPLORATION = "exploration"
EVENT_WAITING = "waiting"
EVENT_NAVIGATION = "navigation"
EVENT_VICTORY = "victory"
EVENT_DEFEAT = "defeat"
BEDWARS_EVENT_TYPES = (
    EVENT_KILL, EVENT_DEATH, EVENT_BED_DESTROYED, EVENT_FINAL_KILL, EVENT_CLUTCH,
    EVENT_COMBAT, EVENT_ITEM_OBTAINED, EVENT_SHOP, EVENT_INVENTORY, EVENT_BUILDING,
    EVENT_EXPLORATION, EVENT_WAITING, EVENT_NAVIGATION, EVENT_VICTORY, EVENT_DEFEAT,
)

# An event detected only from a heuristic (keyword match or a motion spike
# with no corroborating transcript) is "probable"; one stated unambiguously
# in the transcript is "confirmed". Never the reverse -- see
# ai.event_recognizer.EventRecognizer and Feature 7's explicit requirement
# to never present an inferred event as confirmed fact.
EVENT_CONFIDENCE_PROBABLE = "probable"
EVENT_CONFIDENCE_CONFIRMED = "confirmed"
