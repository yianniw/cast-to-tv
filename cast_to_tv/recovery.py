"""Typed failure messages retained across worker-to-GUI event queues."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from .errors import CastingError, check_cancelled


class RecoveryError(CastingError):
    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class ReceiverBusyError(CastingError):
    """The previous receiver route or media is now owned by someone else."""


@dataclass(frozen=True)
class ReceiverOwnership:
    app_id: str
    destination: str
    content_id: str | None = None


def prepare_recovery_receiver(device, previous: ReceiverOwnership | None,
                              cancel: threading.Event | None = None) -> None:
    """Release only the previous stream when reconnecting to an occupied TV."""
    if not device.app_id:
        return
    def same_route():
        return (previous is not None and device.app_id == previous.app_id
                and device.socket_client.destination_id == previous.destination)

    busy = "The TV is busy with another application. Stop it before choosing Retry."
    if not same_route():
        raise ReceiverBusyError(busy)
    if previous.content_id is not None:
        # Receiver readiness precedes media status on a freshly opened socket.
        # Request a current response before comparing the unique stream URL.
        response = threading.Event()
        replies = []

        def received(success, data):
            replies.append((success, data))
            response.set()

        device.media_controller.update_status(callback_function=received)
        deadline = time.monotonic() + 3
        while not response.wait(.1):
            if cancel is not None:
                check_cancelled(cancel)
            if time.monotonic() >= deadline:
                raise CastingError("Could not verify the TV's current playback. Choose Retry when it is reachable.")
        if cancel is not None:
            check_cancelled(cancel)
        success, data = replies[-1]
        if not success or not isinstance(data, dict) or not isinstance(data.get("status"), list):
            raise CastingError("Could not verify the TV's current playback. Choose Retry when it is reachable.")
        statuses = data["status"]
        if len(statuses) != 1 or statuses[0].get("media", {}).get("contentId") != previous.content_id:
            raise ReceiverBusyError(busy)
    if not same_route():
        raise ReceiverBusyError(busy)
    if cancel is not None:
        check_cancelled(cancel)
    device.quit_app(timeout=10)


class ReceiverCleanupSession:
    """Release an interrupted receiver when the user cancels a pending retry.

    Capture has already stopped. Recreate its closed control connection so Stop
    and Close can still release the old receiver, checking route/media ownership.
    The app's normal asynchronous stop path owns this session and its socket.
    """
    diagnostics = None

    def __init__(self, device, zconf, ownership: ReceiverOwnership) -> None:
        self.cast_device = device
        self.zconf = zconf
        self.ownership = ownership

    def stop(self) -> list[str]:
        import pychromecast
        from .receivers.discovery import wait_for_cast

        try:
            device = self.cast_device
            if device.socket_client.ident is not None and not device.socket_client.is_alive():
                device = pychromecast.get_chromecast_from_cast_info(device.cast_info, self.zconf)
                self.cast_device = device
            wait_for_cast(device, threading.Event(), timeout=10)
            try:
                prepare_recovery_receiver(device, self.ownership)
            except ReceiverBusyError:
                # Another sender owns playback now; our resources are released.
                pass
            return []
        except Exception as exc:
            return [f"Could not release the interrupted TV receiver: {exc}"]


class FailureMessage(str):
    """Keep historical string callbacks while carrying a recovery category."""
    def __new__(cls, message: object, kind: str | None = None):
        value = super().__new__(cls, str(message))
        value.kind = kind
        return value


def failure_message(error: object) -> FailureMessage:
    return FailureMessage(error, getattr(error, "kind", None))


def native_failure(message: str) -> CastingError:
    """Classify the existing native helper's documented audio error messages.

    The v1 helper has no category field. Match only messages emitted by its
    audio component; general FFmpeg/protocol failures must not trigger recovery.
    """
    audio_prefixes = (
        "Windows playback device was disconnected or disabled.",
        "Initialize audio COM", "Enumerate playback devices",
        "No default Windows playback device;", "Open loopback audio",
        "Initialize stereo 48 kHz loopback", "Cannot create audio ",
        "Set audio capture event", "Open loopback capture client",
        "Cannot create Opus encoder", "Cannot configure Opus encoder",
        "Start system audio capture", "Opus audio encoding failed:",
        "Audio capture event wait failed", "Check loopback audio packet",
        "Read system audio", "Release audio packet", "Check next loopback audio packet",
        "Opus audio was not selected",
    )
    if message.startswith(audio_prefixes):
        return RecoveryError(message, "audio")
    return CastingError(message)
