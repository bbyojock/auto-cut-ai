"""Small filesystem helpers used across the application."""

from __future__ import annotations

import tempfile
import os
import sys
from pathlib import Path

from utils.constants import TEMP_DIR_NAME


def get_project_root() -> Path:
    """Return the absolute path of the AutoCutAI project root directory."""
    return Path(__file__).resolve().parent.parent


def get_data_root() -> Path:
    """Return the persistent writable data directory for the current install.

    A frozen portable build stores settings beside ``Saeron.exe`` so moving
    the portable folder preserves the user's configuration. If that folder is
    read-only (for example under ``Program Files``), fall back to the normal
    per-user application-data directory instead of silently resetting settings.
    """
    override = os.environ.get("SAERON_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        try:
            executable_dir.mkdir(parents=True, exist_ok=True)
            probe = executable_dir / ".saeron_write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
            return executable_dir
        except OSError:
            pass

        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "Saeron"

    return get_project_root()


def ensure_directory(path: Path) -> Path:
    """Create ``path`` (and parents) if it doesn't exist yet, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_temp_dir() -> Path:
    """Return AutoCutAI's dedicated scratch directory under the OS temp dir.

    Extracted audio and preview frames (see the ``video`` package) are
    written here instead of scattered ``tempfile.mkstemp`` locations, so
    every temporary artifact AutoCutAI has ever produced lives in one place
    and is trivial to find or clear.
    """
    return ensure_directory(Path(tempfile.gettempdir()) / TEMP_DIR_NAME)


def read_text_tail(path: Path, max_lines: int = 500) -> str:
    """Read up to the last ``max_lines`` lines of a text file.

    Returns an empty string if the file does not exist yet, instead of
    raising, since callers (e.g. the Logs page) should be able to display a
    friendly "no logs yet" message.
    """
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return "".join(lines[-max_lines:])
