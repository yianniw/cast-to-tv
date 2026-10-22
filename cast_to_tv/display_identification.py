"""Temporary monitor badges, numbered in the same order as the display selector."""
from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Mapping, Sequence


def _show_on_display(window: tk.Toplevel, display: Mapping[str, int]) -> None:
    """Center in desktop coordinates, including monitors left of/above primary."""
    window.update_idletasks()
    width, height = window.winfo_width(), window.winfo_height()
    x = display["left"] + (display["width"] - width) // 2
    y = display["top"] + (display["height"] - height) // 2
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongW.restype = wintypes.LONG
        user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
        user.SetWindowLongW.restype = wintypes.LONG
        user.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user.SetWindowPos.restype = wintypes.BOOL
        handle = user.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        # Keep badges out of the taskbar and avoid activating another monitor.
        style = user.GetWindowLongW(handle, -20)  # GWL_EXSTYLE
        user.SetWindowLongW(handle, -20, style | 0x08000000 | 0x00000080)
        window.deiconify()
        if not user.SetWindowPos(handle, -1, x, y, width, height, 0x0010 | 0x0040):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        # Tk interprets negative geometry offsets relative to the screen edge.
        # The explicit '+' keeps a negative desktop coordinate absolute.
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()


class DisplayIdentifiers:
    DURATION_MS = 3000

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._windows: list[tk.Toplevel] = []
        self._after: str | None = None

    def show(self, displays: Sequence[Mapping[str, int]]) -> None:
        self.close()
        try:
            for number, display in enumerate(displays, start=1):
                window = tk.Toplevel(self.root)
                self._windows.append(window)
                window.withdraw()
                window.title(f"Display {number}")
                window.overrideredirect(True)
                window.attributes("-topmost", True)
                window.geometry(f"{min(200, display['width'])}x{min(160, display['height'])}")
                window.configure(bg="#172033")
                tk.Label(window, text="Display", font=("Segoe UI", 12),
                         bg="#172033", fg="white").pack(pady=(12, 0))
                tk.Label(window, text=str(number), font=("Segoe UI", 52, "bold"),
                         bg="#172033", fg="white").pack(expand=True)
                tk.Label(window, text="Click to dismiss", font=("Segoe UI", 9),
                         bg="#172033", fg="white").pack(pady=(0, 12))
                window.bind("<Button-1>", lambda _event: self.close())
                _show_on_display(window, display)
            if self._windows:
                self._after = self.root.after(self.DURATION_MS, self.close)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        for window in self._windows:
            window.destroy()
        self._windows.clear()
