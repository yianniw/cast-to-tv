"""GUI session adapter for experimentally validated native desktop/audio mirroring."""
from __future__ import annotations

import logging
import queue
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .errors import CastingError, check_cancelled
from .diagnostics import SessionDiagnostics
from .paths import temporary_directory
from .native_probe import APP_ID, run_probe
from .quality import DEFAULT_QUALITY, VideoQuality
from .recovery import ReceiverOwnership, failure_message

LOGGER = logging.getLogger(__name__)


def native_helper() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    packaged = root / "native" / "cast_streaming_probe.exe"
    development = root / ".tmp" / "native-build" / "cmake" / "Release" / "cast_streaming_probe.exe"
    for candidate in (packaged, development):
        if candidate.is_file():
            return candidate
    raise CastingError("The low-delay helper is missing. Use a build that includes it, or choose Compatibility mode.")


class NativeCastSession:
    """Native live desktop video and optional Windows system audio."""
    def __init__(self, cast_device, display: dict, on_failure: Callable[[str], None],
                 diagnostics: SessionDiagnostics | None = None, include_audio: bool = True,
                 quality: VideoQuality = DEFAULT_QUALITY) -> None:
        self.cast_device = cast_device
        self.display = display
        self.on_failure = on_failure
        self.diagnostics = diagnostics
        self.include_audio = include_audio
        self.quality = quality
        self.recovering = False
        self.previous_receiver: ReceiverOwnership | None = None
        self.recovery_receiver: ReceiverOwnership | None = None
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._worker: threading.Thread | None = None
        self._error: Exception | None = None
        self._started = False
        self._state_lock = threading.Lock()
        self._temporary = None
        self._cleanup_errors: list[str] = []

    def start(self) -> None:
        check_cancelled(self._stop_event)
        helper = native_helper()
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise CastingError("FFmpeg with Desktop Duplication and libvpx is required for low-delay mirroring.")
        width, height = self.quality.output_size(self.display)
        capture = {"ffmpeg": ffmpeg, "display": self.display,
                   "fps": self.quality.frame_rate, "capture": "ddagrab",
                   "output_width": width, "output_height": height}
        if self.diagnostics is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="CastToTV-native-", dir=temporary_directory())
            report = Path(self._temporary.name) / "native-summary.json"
        else:
            report = self.diagnostics.directory / "native-summary.json"
            self.diagnostics.record("native_capture_requested", display=self.display,
                                    resolution=self.quality.resolution, fps=self.quality.frame_rate,
                                    output_width=width, output_height=height,
                                    audio="system" if self.include_audio else "none")
        check_cancelled(self._stop_event)

        def event(record: dict) -> None:
            if record["event"] == "receiver_ready":
                self.recovery_receiver = ReceiverOwnership(APP_ID, record["destination"])
            if record["event"] == "cleanup_error":
                self._cleanup_errors.append(record["message"])
            if self.diagnostics is not None:
                self.diagnostics.record("native_" + record["event"],
                                        **{key: value for key, value in record.items()
                                           if key not in {"event", "elapsed_seconds"}})
            if (record["event"] == "stats" and record.get("sent", 0) > 0
                    and record.get("released", 0) > 0 and record.get("round_trip_ms", 0) > 0
                    and (not self.include_audio or record.get("audio_round_trip_ms", 0) > 0)):
                self._ready.set()

        def run() -> None:
            try:
                run_probe(self.cast_device, helper, Path(), 200, queue.Queue(),
                          self._stop_event, report, capture, on_event=event, system_audio=self.include_audio,
                          preserve_receiver=(self.previous_receiver or True) if self.recovering else False)
                if not self._stop_event.is_set():
                    raise CastingError("The low-delay stream ended unexpectedly.")
            except Exception as exc:
                with self._state_lock:
                    self._error = exc
                    started = self._started
                if not self._stop_event.is_set():
                    LOGGER.exception("Native mirroring failed")
                if started and not self._stop_event.is_set():
                    self.on_failure(failure_message(exc))
            finally:
                self._ready.set()
                if self.diagnostics is not None:
                    self.diagnostics.close()

        self._worker = threading.Thread(target=run, daemon=True)
        self._worker.start()
        deadline = time.monotonic() + 35
        while not self._ready.wait(.1):
            check_cancelled(self._stop_event)
            if time.monotonic() > deadline:
                raise CastingError("The TV did not acknowledge native video before startup timed out.")
        check_cancelled(self._stop_event)
        with self._state_lock:
            if self._error is not None:
                if isinstance(self._error, CastingError):
                    raise self._error
                raise CastingError(str(self._error)) from self._error
            if self._stop_event.is_set() or not self._worker.is_alive():
                raise CastingError("The low-delay stream stopped during startup.")
            self._started = True

    def cancel_start(self) -> None:
        """Let the startup owner finish cleanup; never join from the GUI thread."""
        self._stop_event.set()

    def stop(self) -> list[str]:
        self._stop_event.set()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=20)
            if self._worker.is_alive():
                return self._cleanup_errors + ["The low-delay helper is still finishing cleanup."]
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if self.diagnostics is not None:
            self.diagnostics.close()
        return list(self._cleanup_errors)
