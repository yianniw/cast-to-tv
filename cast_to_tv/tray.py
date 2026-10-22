"""Windows tray integration. Worker callbacks only post queue events, never Tk."""
from __future__ import annotations

import threading
from typing import Callable


class TrayIcon:
    def __init__(self, post: Callable) -> None:
        # Load optional desktop integrations only when first hiding the app.
        import pystray
        from PIL import Image, ImageDraw

        self._post = post
        self._can_stop = False
        self._stop_requested = threading.Event()
        self._failure_lock = threading.Lock()
        self.failed = False
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 9, 59, 47), radius=5, fill="#2076bd")
        draw.rectangle((10, 14, 54, 41), fill="#f0f6fc")
        draw.rectangle((28, 47, 36, 53), fill="#2076bd")
        draw.rounded_rectangle((19, 53, 45, 58), radius=2, fill="#2076bd")
        self._icon = pystray.Icon(
            "CastToTV", image, "Cast to TV", menu=pystray.Menu(
                pystray.MenuItem("Show", self._action("show"), default=True),
                pystray.MenuItem("Stop casting", self._action("stop"), enabled=lambda _item: self._can_stop),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._action("exit")),
            ),
        )
        self._thread = threading.Thread(target=self._run, name="CastToTV tray", daemon=True)

    def _emit(self, action: str, message: str = "") -> None:
        self._post(("tray", (self, action, message)))

    def _action(self, action: str):
        def clicked(_icon, _item):
            self._emit(action)
        return clicked

    def _fail(self, message: str) -> None:
        with self._failure_lock:
            if not self.failed and not self._stop_requested.is_set():
                self.failed = True
                self._emit("failed", message)

    def _setup(self, icon) -> None:
        try:
            if self._stop_requested.is_set():
                icon.stop()
                return
            icon.visible = True
            self._emit("ready")
        except Exception as exc:
            self._fail(str(exc))
            icon.stop()

    def _run(self) -> None:
        try:
            self._icon.run(setup=self._setup)
        except Exception as exc:
            self._fail(str(exc))
        finally:
            self._fail("The tray service stopped unexpectedly.")

    def start(self) -> None:
        self._thread.start()

    def set_can_stop(self, enabled: bool) -> None:
        if enabled != self._can_stop:
            self._can_stop = enabled
            try:
                self._icon.update_menu()
            except Exception as exc:
                self._fail(str(exc))

    def stop(self) -> list[str]:
        """Called by the app's shutdown worker, including during tray startup."""
        self._stop_requested.set()
        errors = []
        try:
            self._icon.stop()
        except Exception as exc:
            errors.append(f"Could not stop the tray icon: {exc}")
        if self._thread.ident is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                errors.append("The tray service is still finishing cleanup.")
        return errors
