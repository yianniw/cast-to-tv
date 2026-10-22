"""Capture monitor rectangles in physical desktop coordinates."""
from __future__ import annotations

import mss


def list_displays() -> list[dict[str, int]]:
    with mss.MSS() as capture:
        return [dict(monitor) for monitor in capture.monitors[1:]]
