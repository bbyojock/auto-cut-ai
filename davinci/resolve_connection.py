"""Connects to a running DaVinci Resolve instance via Blackmagic's official
DaVinci Resolve Scripting API.

This project had no pre-existing Resolve integration (``davinci/__init__.py``
was an intentionally empty placeholder for exactly this version -- see its
docstring). Per Section 4 of the V5 spec, this module deliberately uses
Resolve's own documented scripting bridge (the ``DaVinciResolveScript``
module Resolve ships with itself) rather than standing up a new MCP server
or custom bridge process: it is the stable, Blackmagic-supported interface
every Resolve automation tool is built on, and nothing more stable is
available to build a new bridge on top of.

How the scripting module is normally made importable
------------------------------------------------------
Resolve does not put ``DaVinciResolveScript`` on the default Python path.
Two environment variables, when set, tell Python where to find it --
Resolve's own installer/setup documentation asks users to set these once:

- ``RESOLVE_SCRIPT_API``: the ``.../Scripting`` folder containing a
  ``Modules`` subfolder with ``DaVinciResolveScript.py``.
- ``RESOLVE_SCRIPT_LIB``: the compiled ``fusionscript`` library
  (``.dll``/``.so``/``.dylib``) that module loads internally.

If those aren't set (a very common case -- most users never configure
them), this module falls back to the well-known default install locations
for each OS, so a fresh Resolve install still works out of the box.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

from core.exceptions import (
    ResolveNotRunningError,
    ResolveProjectNotFoundError,
    ResolveTimelineNotFoundError,
)
from services.logging_service import LoggingService

_logger = LoggingService.get_logger("davinci.resolve_connection")

# Well-known default install locations, used only if RESOLVE_SCRIPT_API /
# RESOLVE_SCRIPT_LIB aren't already set in the environment.
_DEFAULT_PATHS = {
    "darwin": {
        "api": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        "lib": "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
    },
    "win32": {
        "api": os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
            "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting",
        ),
        "lib": os.path.join(
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll",
        ),
    },
    "linux": {
        "api": "/opt/resolve/Developer/Scripting",
        "lib": "/opt/resolve/libs/Fusion/fusionscript.so",
    },
}


def _default_paths() -> dict:
    if sys.platform.startswith("darwin"):
        return _DEFAULT_PATHS["darwin"]
    if sys.platform.startswith("win"):
        return _DEFAULT_PATHS["win32"]
    return _DEFAULT_PATHS["linux"]


def _import_resolve_script_module() -> Any:
    """Import and return the ``DaVinciResolveScript`` module, or raise
    :class:`core.exceptions.ResolveNotRunningError` with a message that
    tells the user exactly what to check."""
    defaults = _default_paths()
    api_path = os.environ.get("RESOLVE_SCRIPT_API") or defaults["api"]
    lib_path = os.environ.get("RESOLVE_SCRIPT_LIB") or defaults["lib"]

    modules_path = os.path.join(api_path, "Modules")
    if modules_path not in sys.path:
        sys.path.append(modules_path)
    # Some Resolve installs load fusionscript via this variable rather than
    # a plain sys.path import; setting it is harmless if unused.
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib_path)

    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ResolveNotRunningError(
            "Could not import DaVinci Resolve's scripting module. Make sure DaVinci Resolve is "
            "installed and that 'External scripting using' is enabled in "
            "Resolve > Preferences > System > General. If Resolve is installed in a non-default "
            "location, set the RESOLVE_SCRIPT_API and RESOLVE_SCRIPT_LIB environment variables "
            f"(tried: RESOLVE_SCRIPT_API={api_path}). Original error: {exc}"
        ) from exc

    return dvr_script


@dataclass(slots=True)
class ResolveHandle:
    """The three Resolve scripting objects everything else needs.

    Held together so a caller (:class:`davinci.timeline_editor.TimelineEditor`,
    :class:`services.resolve_export_service.ResolveExportService`) only ever
    has to pass around one object instead of threading three through every
    call.
    """

    resolve: Any
    project: Any
    timeline: Any

    @property
    def timeline_frame_rate(self) -> float:
        """The current timeline's frame rate, used to convert EditPlan
        seconds into Resolve timeline frame numbers."""
        from utils.constants import RESOLVE_FALLBACK_FRAME_RATE

        for getter in (
            lambda: self.timeline.GetSetting("timelineFrameRate"),
            lambda: self.project.GetSetting("timelineFrameRate"),
        ):
            try:
                value = getter()
                if value:
                    return float(value)
            except Exception:  # noqa: BLE001 - scripting API objects raise all sorts of things
                continue
        _logger.warning(
            "Could not read the timeline frame rate from Resolve; falling back to %.2f fps.",
            RESOLVE_FALLBACK_FRAME_RATE,
        )
        return RESOLVE_FALLBACK_FRAME_RATE


def connect(require_timeline: bool = True) -> ResolveHandle:
    """Connect to a running Resolve, its current project, and (optionally) its
    current timeline.

    Raises:
        core.exceptions.ResolveNotRunningError: Resolve isn't running, isn't
            scriptable, or the scripting module couldn't be imported/reached.
        core.exceptions.ResolveProjectNotFoundError: Resolve is running but
            no project is currently open.
        core.exceptions.ResolveTimelineNotFoundError: ``require_timeline`` is
            True and the open project has no current timeline.
    """
    dvr_script = _import_resolve_script_module()

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        raise ResolveNotRunningError(
            "DaVinci Resolve does not appear to be running (scriptapp('Resolve') returned None). "
            "Start Resolve and open a project before applying an EditPlan."
        )

    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject() if project_manager is not None else None
    if project is None:
        raise ResolveProjectNotFoundError(
            "DaVinci Resolve is running, but no project is currently open. Open the project "
            "containing your imported footage first."
        )

    timeline: Optional[Any] = project.GetCurrentTimeline()
    if timeline is None and require_timeline:
        raise ResolveTimelineNotFoundError(
            "The current Resolve project has no active timeline. Create/open the timeline "
            "containing your source video before applying an EditPlan."
        )

    _logger.info(
        "Connected to Resolve: project=%r timeline=%r",
        _safe_name(project), _safe_name(timeline),
    )
    return ResolveHandle(resolve=resolve, project=project, timeline=timeline)


def _safe_name(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return obj.GetName()
    except Exception:  # noqa: BLE001
        return "<unknown>"
