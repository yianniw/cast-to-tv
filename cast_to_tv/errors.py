"""Errors shared by casting modes and receiver connections."""
from __future__ import annotations

import threading


class CastingError(RuntimeError):
    """An error that is suitable to show in the user interface."""


class CastingCancelled(CastingError):
    """Startup was cancelled by the user."""


def check_cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise CastingCancelled("Connection cancelled.")
