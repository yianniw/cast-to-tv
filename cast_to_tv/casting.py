"""Compatibility session lifecycle and backward-compatible capture/receiver exports."""
from __future__ import annotations

import functools
import http.server
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import mss
import numpy
import pychromecast
import soundcard
import zeroconf
from pychromecast.controllers.media import APP_MEDIA_RECEIVER, STREAM_TYPE_LIVE
from pychromecast.discovery import CastBrowser, SimpleCastListener
from pychromecast.dial import get_device_info
from pychromecast.error import ChromecastConnectionError, RequestTimeout
from pychromecast.models import HostServiceInfo

from .capture.audio import audio_to_pcm16, default_loopback_microphone
from .recovery import ReceiverOwnership, RecoveryError, failure_message, prepare_recovery_receiver
from .capture.displays import list_displays
from .capture.ffmpeg import ffmpeg_command, ffmpeg_executable
from .compatibility.manifest import DASH_NAMESPACE, STARTUP_SEGMENTS, stream_is_ready
from .compatibility.server import CONTENT_TYPE, _StreamRequestHandler
from .errors import CastingCancelled, CastingError, check_cancelled
from .receivers import discovery as _discovery
from .receivers.discovery import local_address_for

from .diagnostics import SessionDiagnostics
from .paths import temporary_directory
from .quality import DEFAULT_QUALITY, VideoQuality

LOGGER = logging.getLogger(__name__)


def discover_cast_devices(
    timeout: float = 5,
) -> tuple[list[pychromecast.Chromecast], CastBrowser]:
    """Discover receivers using the historical casting module entry point."""
    return _discovery.discover_cast_devices(timeout, browser_factory=CastBrowser)


def wait_for_cast(device, cancel: threading.Event, timeout: float) -> None:
    """Wait for a verified receiver connection, with cancellation support."""
    _discovery.wait_for_cast(device, cancel, timeout, device_info=get_device_info)


class CastSession:
    """Own one Compatibility stream; callers must stop it even if startup fails."""

    def __init__(
        self,
        cast_device: pychromecast.Chromecast,
        display: dict[str, int],
        on_failure: Callable[[str], None],
        diagnostics: SessionDiagnostics | None = None,
        silent_audio: bool = False,
        quality: VideoQuality = DEFAULT_QUALITY,
    ) -> None:
        self.cast_device = cast_device
        self.display = display
        self.on_failure = on_failure
        self.diagnostics = diagnostics
        self.silent_audio = silent_audio
        self.quality = quality
        self.recovering = False
        self.previous_receiver: ReceiverOwnership | None = None
        self.recovery_receiver: ReceiverOwnership | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.server: http.server.ThreadingHTTPServer | None = None
        self.temp_dir: str | None = None
        self._stop_event = threading.Event()
        self._stderr_thread: threading.Thread | None = None
        self._ffmpeg_errors: list[str] = []
        self._lock = threading.Lock()
        self._cast_playback_started = False
        self._loaded_stream_url: str | None = None
        self._receiver_launch_response = threading.Event()
        self._receiver_launch_requested = False
        self._receiver_launch_succeeded = False
        self._receiver_destination: str | None = None
        self._startup_cancelled = False
        self._monitor_thread: threading.Thread | None = None
        self._audio_thread: threading.Thread | None = None
        self._audio_ready = threading.Event()
        self._audio_errors: list[str] = []
        self._audio_pipe_failed = False
        self._progress_thread: threading.Thread | None = None
        self._diagnostic_thread: threading.Thread | None = None

    def start(self) -> None:
        check_cancelled(self._stop_event)
        host = self.cast_device.cast_info.host
        try:
            wait_for_cast(self.cast_device, self._stop_event, timeout=10)
        except CastingCancelled:
            raise
        except Exception as exc:
            raise RecoveryError(
                f"Could not connect to the TV at {host}: {exc}. "
                "Check that the TV is awake and reachable on the same local network.", "connection"
            ) from exc
        if self.recovering:
            prepare_recovery_receiver(self.cast_device, self.previous_receiver, self._stop_event)
        self._reset_media_receiver()
        check_cancelled(self._stop_event)
        executable = ffmpeg_executable()
        check_cancelled(self._stop_event)
        loopback = None if self.silent_audio else default_loopback_microphone()
        check_cancelled(self._stop_event)
        local_address = local_address_for(host)
        check_cancelled(self._stop_event)
        self.temp_dir = tempfile.mkdtemp(prefix="cast-to-tv-", dir=temporary_directory())
        server_address = (local_address, 0)
        handler = functools.partial(
            _StreamRequestHandler,
            directory=self.temp_dir,
            diagnostics=self.diagnostics,
        )
        self.server = http.server.ThreadingHTTPServer(server_address, handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        check_cancelled(self._stop_event)

        command = ffmpeg_command(executable, self.display, Path(self.temp_dir), self.silent_audio, self.quality)
        if self.diagnostics is not None:
            command[4:4] = ["-progress", "pipe:1", "-stats_period", "0.5", "-nostats"]
            self.diagnostics.record("capture_launch", display=self.display, command=command,
                                    audio_source="generated_silence" if self.silent_audio else "system_loopback")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL if self.silent_audio else subprocess.PIPE,
                stdout=subprocess.PIPE if self.diagnostics is not None else subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creation_flags,
                # FFmpeg resolves DASH initialization/segment names relative
                # to its working directory, even with an absolute manifest.
                cwd=self.temp_dir,
            )
        except OSError as exc:
            raise CastingError(f"Could not start FFmpeg: {exc}") from exc

        self._stderr_thread = threading.Thread(
            target=self._read_ffmpeg_errors,
            daemon=True,
        )
        self._stderr_thread.start()
        if self.diagnostics is not None:
            self.diagnostics.record("capture_started")
            self._progress_thread = threading.Thread(target=self._read_progress, daemon=True)
            self._progress_thread.start()
            self._diagnostic_thread = threading.Thread(
                target=self.diagnostics.watch_manifest,
                args=(Path(self.temp_dir) / "desktop.mpd", self._stop_event), daemon=True,
            )
            self._diagnostic_thread.start()
        if loopback is not None:
            self._audio_thread = threading.Thread(target=self._capture_audio, args=(loopback,), daemon=True)
            self._audio_thread.start()
        else:
            self._audio_ready.set()

        audio_deadline = time.monotonic() + 10
        while not self._audio_ready.wait(timeout=0.1):
            check_cancelled(self._stop_event)
            if self.process.poll() is not None:
                raise CastingError(self._ffmpeg_failure())
            if time.monotonic() >= audio_deadline:
                raise RecoveryError(
                    "System-audio loopback did not start within 10 seconds.", "audio"
                )
        check_cancelled(self._stop_event)
        if self._audio_pipe_failed:
            raise CastingError(self._ffmpeg_failure())
        if self.process.poll() is not None:
            raise CastingError(self._ffmpeg_failure())
        if self._audio_thread is not None and not self._audio_thread.is_alive() and not self._audio_errors:
            raise RecoveryError(
                "System-audio loopback stopped before capture could start.", "audio"
            )
        if self._audio_errors:
            raise RecoveryError(self._audio_errors[-1], "audio")

        manifest = Path(self.temp_dir) / "desktop.mpd"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            check_cancelled(self._stop_event)
            if self.process.poll() is not None:
                raise CastingError(self._ffmpeg_failure())
            if stream_is_ready(manifest):
                if self.diagnostics is not None:
                    self.diagnostics.record("stream_ready")
                break
            self._stop_event.wait(0.2)
        else:
            raise CastingError(
                "FFmpeg did not produce a live stream. "
                + self._ffmpeg_failure()
            )

        try:
            check_cancelled(self._stop_event)
            self._launch_media_receiver()
            check_cancelled(self._stop_event)
            stream_url = (
                f"http://{local_address}:{self.server.server_port}/desktop.mpd"
            )
            LOGGER.info("Loading desktop stream on %s: %s", host, stream_url)
            self._loaded_stream_url = stream_url
            self.recovery_receiver = ReceiverOwnership(
                APP_MEDIA_RECEIVER, self._receiver_destination, stream_url,
            )
            if self.diagnostics is not None:
                self.diagnostics.stream_url = stream_url
                self.diagnostics.attach(self.cast_device.media_controller)
                self.diagnostics.record("load_sent", stream_url=stream_url)
            self.cast_device.media_controller.play_media(
                stream_url,
                CONTENT_TYPE,
                title="Windows desktop",
                stream_type=STREAM_TYPE_LIVE,
                media_info={
                    "mediaCategory": "VIDEO",
                    "duration": -1,
                },
            )
            self._cast_playback_started = True
            self._wait_for_playback(stream_url)
            check_cancelled(self._stop_event)
            self._monitor_thread = threading.Thread(
                target=self._monitor_capture,
                daemon=True,
            )
            self._monitor_thread.start()
        except CastingError:
            raise
        except Exception as exc:
            raise CastingError(
                f"Could not start playback on the TV: {exc}. "
                "Check that Windows Firewall allows this app on the active network."
            ) from exc

    def _launch_media_receiver(self) -> None:
        """Wait for a ready route before LOAD, avoiding a deferred LOAD after cancel."""
        controller = self.cast_device.media_controller

        def launched(success: bool, _data) -> None:
            self._receiver_launch_succeeded = success
            self._receiver_launch_response.set()

        self._receiver_launch_requested = True
        controller.launch(force_launch=True, callback_function=launched)
        deadline = time.monotonic() + 20
        while True:
            check_cancelled(self._stop_event)
            if self._receiver_launch_response.is_set() and not self._receiver_launch_succeeded:
                raise CastingError("The TV rejected launching its media receiver.")
            if (controller.is_active and self.cast_device.app_id == APP_MEDIA_RECEIVER
                    and self.cast_device.socket_client.destination_id):
                self._receiver_destination = self.cast_device.socket_client.destination_id
                return
            if time.monotonic() >= deadline:
                raise CastingError("The TV did not finish launching its media receiver.")
            self._stop_event.wait(.05)

    def _reset_media_receiver(self) -> None:
        # Restart the receiver to clear decoder state left by a previous live
        # stream after STOP, which can leave the next stream stuck in BUFFERING.
        if self.cast_device.status.app_id != APP_MEDIA_RECEIVER:
            return
        LOGGER.info("Restarting the default Cast receiver before desktop playback")
        try:
            self.cast_device.quit_app(timeout=10)
        except Exception as exc:
            raise CastingError(f"Could not reset the TV's previous Cast receiver: {exc}") from exc

    def _wait_for_playback(self, stream_url: str, timeout: float = 30) -> None:
        deadline = time.monotonic() + timeout
        controller = self.cast_device.media_controller
        status = controller.status
        first_playing_time: float | None = None
        next_status_request = 0.0
        while time.monotonic() < deadline:
            check_cancelled(self._stop_event)
            if self.process is not None and self.process.poll() is not None:
                raise CastingError(self._ffmpeg_failure())
            if self._audio_errors:
                if self._audio_pipe_failed:
                    raise CastingError(self._ffmpeg_failure())
                raise RecoveryError(self._audio_errors[-1], "audio")
            now = time.monotonic()
            status = controller.status
            # Wait for LOAD's asynchronous receiver launch before polling;
            # sending GET_STATUS too early can launch the receiver again.
            if status.content_id == stream_url and now >= next_status_request:
                controller.update_status()
                next_status_request = now + 1
            # An old session or an IDLE/BUFFERING session does not confirm video.
            if status.content_id == stream_url:
                if status.player_state == "IDLE" and status.idle_reason == "ERROR":
                    raise CastingError(
                        "The TV rejected the live stream. Its receiver reported a "
                        "playback error while loading the video."
                    )
                if status.player_state == "PLAYING":
                    if first_playing_time is None:
                        first_playing_time = status.current_time
                    elif status.current_time > first_playing_time + 0.5:
                        LOGGER.info("TV playback is advancing: %s", stream_url)
                        if self.diagnostics is not None:
                            self.diagnostics.record("playback_advancing", playhead_seconds=status.current_time)
                        return
                else:
                    first_playing_time = None
            else:
                first_playing_time = None
            self._stop_event.wait(0.1)
        raise CastingError(
            f"The TV did not start playing the video with an advancing playback "
            f"time within {timeout:g} seconds "
            f"(receiver state: {status.player_state}). "
            "Check that the TV can reach the PC's stream and that the app is "
            "allowed through Windows Firewall on the active network."
        )

    def _read_ffmpeg_errors(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for raw_line in self.process.stderr:
            message = raw_line.decode("utf-8", errors="replace").strip()
            if message:
                with self._lock:
                    self._ffmpeg_errors.append(message)
                LOGGER.warning("FFmpeg: %s", message)
                if self.diagnostics is not None:
                    self.diagnostics.record("encoder_warning", message=message)

    def _read_progress(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        assert self.diagnostics is not None
        progress = {}
        for raw_line in self.process.stdout:
            key, separator, value = raw_line.decode("utf-8", errors="replace").strip().partition("=")
            if not separator:
                continue
            progress[key] = value
            if key == "progress":
                self.diagnostics.record("encoder_progress", values=progress)
                progress = {}

    def _capture_audio(self, loopback: soundcard.Microphone) -> None:
        assert self.process is not None and self.process.stdin is not None
        frames = 0
        last_report = time.monotonic()
        max_write_ms = 0.0
        try:
            with loopback.recorder(
                samplerate=48000,
                channels=[0, 1],
                blocksize=1920,
            ) as recorder:
                while not self._stop_event.is_set():
                    samples = recorder.record(numframes=960)
                    pcm = audio_to_pcm16(samples)
                    write_started = time.monotonic()
                    try:
                        self.process.stdin.write(pcm)
                    except OSError:
                        self._audio_pipe_failed = True
                        raise
                    frames += len(samples)
                    max_write_ms = max(max_write_ms, (time.monotonic() - write_started) * 1000)
                    if self.diagnostics is not None and time.monotonic() - last_report >= 1:
                        self.diagnostics.record("audio_progress", recorded_seconds=frames / 48000,
                                                max_pipe_write_ms=max_write_ms)
                        last_report = time.monotonic()
                        max_write_ms = 0.0
                    self._audio_ready.set()
        except Exception as exc:
            if not self._stop_event.is_set():
                message = f"System-audio loopback capture failed: {exc}"
                LOGGER.exception("%s", message)
                self._audio_errors.append(message)
                self._audio_ready.set()
        finally:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass

    def _ffmpeg_failure(self) -> str:
        with self._lock:
            details = "\n".join(self._ffmpeg_errors[-5:])
        suffix = f"\nFFmpeg details: {details}" if details else ""
        return (
            "FFmpeg could not capture this display and system audio. "
            "Check Windows capture permissions and the selected display."
            + suffix
        )

    def _monitor_capture(self) -> None:
        assert self.process is not None
        while not self._stop_event.wait(0.5):
            if not self.cast_device.socket_client.is_connected:
                self.on_failure(failure_message(RecoveryError("The connection to the TV was lost.", "connection")))
                return
            if self._audio_pipe_failed:
                self.on_failure(self._ffmpeg_failure())
                return
            if self._audio_thread is not None and not self._audio_thread.is_alive():
                message = (self._audio_errors[-1] if self._audio_errors
                           else "System-audio loopback stopped unexpectedly.")
                self.on_failure(failure_message(RecoveryError(message, "audio")))
                return
            if self.diagnostics is not None:
                # Status events arrive asynchronously through the listener.
                # Request updates so the trace does not rely on stale clocks.
                try:
                    self.cast_device.media_controller.update_status()
                except Exception as exc:
                    self.diagnostics.record("status_request_failed", message=str(exc))
            return_code = self.process.poll()
            if return_code is not None:
                self.on_failure(
                    f"The desktop capture stream stopped unexpectedly "
                    f"(FFmpeg exit code {return_code}). {self._ffmpeg_failure()}"
                )
                return
            status = self.cast_device.media_controller.status
            if status.player_state == "IDLE" and status.idle_reason == "ERROR":
                self.on_failure("The TV reported a playback error and stopped the live stream.")
                return

    def cancel_start(self) -> None:
        """Signal startup without joining workers or touching network resources."""
        self._startup_cancelled = True
        self._stop_event.set()

    def stop(self) -> list[str]:
        cleanup_errors: list[str] = []
        self._stop_event.set()
        if self._receiver_launch_requested and self._receiver_destination is None:
            # Let an in-flight LAUNCH settle before deciding whether we own an
            # empty receiver. No LOAD is queued behind this launch.
            if not self._receiver_launch_response.wait(timeout=10):
                cleanup_errors.append("The TV has not confirmed the cancelled receiver launch. Choose Stop to retry.")
        if self._cast_playback_started or self._receiver_launch_requested:
            # Stop the receiver only while it is still playing our unique local
            # URL. Another sender may have taken over since this cast started.
            content = self.cast_device.media_controller.status.content_id
            our_stream = self._loaded_stream_url is not None and content == self._loaded_stream_url
            our_empty_receiver = (self._receiver_launch_requested and self._receiver_launch_succeeded
                                  and not content and (self._receiver_destination is None
                                  or self.cast_device.socket_client.destination_id == self._receiver_destination))
            owned = self.cast_device.app_id == APP_MEDIA_RECEIVER and (our_stream or our_empty_receiver)
            if owned and self.cast_device.socket_client.is_connected:
                try:
                    self.cast_device.quit_app(timeout=10)
                except Exception as exc:
                    cleanup_errors.append(f"Could not stop TV playback: {exc}")
                    LOGGER.warning(cleanup_errors[-1])
            elif owned and self.diagnostics is not None:
                self.diagnostics.record("receiver_cleanup_deferred", reason="control_connection_lost")
            if not cleanup_errors:
                self._cast_playback_started = False
                self._receiver_launch_requested = False
        if self._audio_thread is not None:
            self._audio_thread.join(timeout=3)
            if self._audio_thread.is_alive():
                LOGGER.warning("System-audio capture did not stop within 3 seconds")
        if self.process is not None and self.process.poll() is None:
            if self._startup_cancelled:
                try:
                    self.process.terminate()
                except OSError as exc:
                    cleanup_errors.append(f"Could not stop FFmpeg: {exc}")
            if self.process.stdin is not None and not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        self.process.kill()
                        self.process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        cleanup_errors.append(f"Could not stop FFmpeg: {exc}")
                        LOGGER.warning(cleanup_errors[-1])
                except OSError as exc:
                    cleanup_errors.append(f"Could not stop FFmpeg: {exc}")
                    LOGGER.warning(cleanup_errors[-1])
            except OSError as exc:
                cleanup_errors.append(f"Could not stop FFmpeg: {exc}")
                LOGGER.warning(cleanup_errors[-1])
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except OSError as exc:
                cleanup_errors.append(f"Could not stop the stream server: {exc}")
                LOGGER.warning(cleanup_errors[-1])
            finally:
                self.server = None
        if self.temp_dir is not None:
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=False)
            except OSError as exc:
                cleanup_errors.append(f"Could not remove temporary stream files: {exc}")
                LOGGER.warning(cleanup_errors[-1])
            else:
                self.temp_dir = None
        for thread in (self._progress_thread, self._diagnostic_thread):
            if thread is not None:
                thread.join(timeout=2)
        for thread in (self._audio_thread, self._monitor_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)
                if thread.is_alive():
                    cleanup_errors.append("A capture worker is still finishing cleanup. Choose Stop to retry.")
        if self.diagnostics is not None:
            self.diagnostics.close()
        return cleanup_errors
