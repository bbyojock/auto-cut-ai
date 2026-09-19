"""Application-wide exception hierarchy.

Using a dedicated hierarchy (instead of raising bare ``Exception``/``ValueError``)
lets calling code (UI layer, services) catch precisely what it expects and lets
the logging service record structured, meaningful errors.
"""

from __future__ import annotations


class AutoCutAIError(Exception):
    """Base class for every exception raised by AutoCutAI."""


class ConfigurationError(AutoCutAIError):
    """Raised when the configuration file cannot be read, written or parsed."""


class NoAPIKeysConfiguredError(AutoCutAIError):
    """Raised when an AI provider is asked to run without any configured key."""


class AllKeysExhaustedError(AutoCutAIError):
    """Raised when every available API key has failed and none are usable."""


class ProviderNotFoundError(AutoCutAIError):
    """Raised when requesting an AI provider name that isn't registered."""


class ProviderRequestError(AutoCutAIError):
    """Raised when a single provider request fails for a reason unrelated to keys."""


# --- Video analysis pipeline (v2) -------------------------------------------


class VideoImportError(AutoCutAIError):
    """Raised when a video file cannot be imported or its metadata detected."""


class AudioExtractionError(AutoCutAIError):
    """Raised when audio cannot be extracted from a video."""


class TranscriptionError(AutoCutAIError):
    """Raised when Whisper transcription fails."""


class FrameExtractionError(AutoCutAIError):
    """Raised when preview frame extraction fails."""


class AnalysisPipelineError(AutoCutAIError):
    """Raised for pipeline-level failures not specific to a single stage."""


# --- AI editing brain (v3) ---------------------------------------------------


class ResponseParsingError(AutoCutAIError):
    """Raised when the AI's edit-plan response is missing, malformed, or invalid JSON."""


class EditPlanningError(AutoCutAIError):
    """Raised for edit-planning failures not specific to prompt/context/parsing."""


# --- AI editing brain quality & safety (v3.5) --------------------------------


class EditPlanValidationError(AutoCutAIError):
    """Raised when an EditPlan has problems that could not be safely auto-repaired."""


class ProviderNotImplementedError(AutoCutAIError):
    """Raised when creating a provider that is registered but not yet implemented."""


# --- Multi-provider architecture (stability update) --------------------------


class ProviderConfigurationError(AutoCutAIError):
    """Raised when a ProviderSettings entry is invalid (bad type, missing fields, ...)."""


class ConnectionTestError(AutoCutAIError):
    """Raised only internally by connection-test helpers; normally caught and
    turned into a ConnectionTestResult rather than propagated."""


# --- Whisper GPU/CPU runtime management (stability update) -------------------


class WhisperRuntimeError(AutoCutAIError):
    """Raised when Whisper cannot run on *either* GPU or CPU (last resort only)."""


# --- Progress / cancellation (Version 4) -------------------------------------


class OperationCancelledError(AutoCutAIError):
    """Raised when a user-requested cancellation stops an in-progress operation.

    Never indicates a real failure -- callers (e.g. the AI Editor page)
    catch this specifically to restore the UI quietly, without logging it
    as an error or showing a scary message box.
    """


# --- DaVinci Resolve cut-editing integration (Version 5) ---------------------
# All of these are caught at the UI boundary (ui.pages.edit_plan_page) and
# shown as a clear message box -- none of them should ever crash the app.


class ResolveError(AutoCutAIError):
    """Base class for every Version 5 DaVinci Resolve integration failure."""


class ResolveNotRunningError(ResolveError):
    """Raised when the Resolve scripting API module/app cannot be reached at all
    (Resolve isn't running, isn't scriptable, or the scripting API isn't
    installed/discoverable on this machine)."""


class ResolveProjectNotFoundError(ResolveError):
    """Raised when Resolve is running but has no project currently open."""


class ResolveTimelineNotFoundError(ResolveError):
    """Raised when the current project has no timeline (or none could be created)."""


class ResolveClipNotFoundError(ResolveError):
    """Raised when the expected source clip can't be found on the timeline."""


class EditPlanResolveApplyError(ResolveError):
    """Raised when an EditPlan cannot be safely converted/applied to a Resolve
    timeline (e.g. every segment invalid, plan empty of any usable segment)."""


# --- DaVinci Resolve Free XML export (Version 5, "Resolve Free support") -----
# Unlike everything above, this path never contacts the Resolve Scripting
# API at all -- it only builds/writes a Final Cut Pro XML file the user
# then imports into Resolve (Free or Studio) by hand. Still a ResolveError
# subclass so ui.pages.edit_plan_page can catch every Resolve-related
# failure (Studio apply *or* XML export) the same way.


class ResolveXMLExportError(ResolveError):
    """Raised when an EditPlan cannot be exported as a Resolve-importable
    XML file (invalid fps, no KEEP ranges left, or the file could not be
    written to disk)."""
