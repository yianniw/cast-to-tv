"""Keep Windows awake without changing the user's power plan."""
from __future__ import annotations

import ctypes

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class ScreenAwake:
    """Own a power request; call every method from the same (GUI) thread."""

    def __init__(self) -> None:
        self._enabled = False

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        kernel32 = ctypes.WinDLL("kernel32")
        set_state = kernel32.SetThreadExecutionState
        set_state.argtypes = [ctypes.c_uint32]
        set_state.restype = ctypes.c_uint32
        flags = ES_CONTINUOUS
        if enabled:
            flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
        if not set_state(flags):
            raise OSError("Windows rejected the screen-on power request.")
        self._enabled = enabled
