"""Cooperative cancellation for long-running background operations.

Nothing in AutoCutAI ever kills a thread outright (that's unsafe in
Python and can corrupt project state -- see the Version 4 requirements).
Instead, a :class:`CancellationToken` is threaded through the pipeline and
AI-generation call stack; long-running / chunked work checks
:meth:`CancellationToken.raise_if_cancelled` at safe points (between
pipeline stages, after every streamed chunk) and unwinds cleanly via
:class:`core.exceptions.OperationCancelledError` when the user cancels.
"""

from __future__ import annotations

import threading

from core.exceptions import OperationCancelledError


class CancellationToken:
    """A simple thread-safe flag, shared between the UI thread and a worker."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. Safe to call from any thread, any number of times."""
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`OperationCancelledError` if :meth:`cancel` was called.

        Called by cooperating long-running code at safe checkpoints -- this
        is the only mechanism by which cancellation actually stops work.
        """
        if self._event.is_set():
            raise OperationCancelledError("The operation was cancelled by the user.")
