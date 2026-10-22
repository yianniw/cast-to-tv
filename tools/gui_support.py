"""Reusable main-thread GUI checks with isolated preferences and clean shutdown."""
from __future__ import annotations

import tempfile
import threading
import time
import tkinter as tk
import traceback
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from cast_to_tv.app import CastToTVApp

# Background workers can otherwise trigger cyclic GC of old Tk objects. Retain
# closed apps until this short-lived check process exits on the main thread.
_retired_apps: list[CastToTVApp] = []


def require_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("GUI checks must run on the main thread")


@contextmanager
def isolated_settings():
    """Keep the original settings file untouched, even when a check raises."""
    require_main_thread()
    with tempfile.TemporaryDirectory(prefix="cast-to-tv-check-") as directory:
        with patch("cast_to_tv.paths.application_directory", return_value=Path(directory)):
            yield directory


def pump_until(app, predicate, timeout: float, description: str, check=None) -> None:
    """Process GUI events until a condition is true, including asynchronous close."""
    require_main_thread()
    deadline = time.monotonic() + timeout
    while not predicate():
        if check is not None:
            check()
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out: {description}; status={app.status.get()}")
        app.root.update()
        time.sleep(0.05)
    # Tk has already been destroyed when a shutdown predicate becomes true.
    if not app._closed:
        app.root.update_idletasks()
    if check is not None:
        check()


def select(app, box, index: int) -> None:
    require_main_thread()
    box.current(index)
    box.event_generate("<<ComboboxSelected>>")
    app.root.update()


def cleanup(app, timeout: float = 60) -> None:
    require_main_thread()
    if not app._closed:
        app.close()
        pump_until(app, lambda: app._closed, timeout, "asynchronous app shutdown")


class GuiHarness:
    """Manage one app inside isolated_settings(); real discovery is opt-in.

    Keep the context active through shutdown so error dialogs and discovery
    patches remain in place until every app worker has released its resources.
    """

    def __init__(self, *, live: bool = False, devices=None, visible: bool = False):
        self.live = live
        self.devices = list(devices or [])
        self.visible = visible
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.callbacks: list[str] = []
        self.app = None
        self._stack = ExitStack()

    def check(self) -> None:
        problems = self.errors + self.warnings + self.callbacks
        if problems:
            raise AssertionError("GUI errors/warnings: " + " | ".join(problems))

    def wait(self, predicate, description: str, timeout: float = 45) -> None:
        pump_until(self.app, predicate, timeout, description, self.check)

    def __enter__(self):
        require_main_thread()
        root = None
        try:
            for name, target in (("showerror", self.errors), ("showwarning", self.warnings)):
                self._stack.enter_context(patch(
                    f"cast_to_tv.app.messagebox.{name}",
                    side_effect=lambda *args, _target=target, **kwargs: _target.append(str((args, kwargs))),
                ))
            if not self.live:
                self._stack.enter_context(patch(
                    "cast_to_tv.app.discover_cast_devices", return_value=(self.devices, None),
                ))
            root = tk.Tk()
            root.withdraw()
            root.report_callback_exception = lambda *exc: self.callbacks.append(
                "".join(traceback.format_exception(*exc)))
            self.app = CastToTVApp(root)
            _retired_apps.append(self.app)
            self.wait(lambda: not self.app.busy, "TV discovery", 25)
            if self.visible:
                root.deiconify()
                root.update()
            self.check()
            return self
        except BaseException:
            try:
                if self.app is not None:
                    cleanup(self.app)
                elif root is not None:
                    root.destroy()
            finally:
                self._stack.close()
            raise

    def __exit__(self, exc_type, exc, tb):
        require_main_thread()
        try:
            cleanup(self.app)
            if exc_type is None:
                self.check()
        finally:
            self._stack.close()
