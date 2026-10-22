"""Versioned Cast control routes and bounded inbound message handling."""
from __future__ import annotations

import queue
import threading

from pychromecast.controllers import BaseController

NAMESPACE = "urn:x-cast:com.google.cast.webrtc"
APP_ID = "0F5096E8"
PROTOCOL_VERSION = 1


class StreamingController(BaseController):
    def __init__(self) -> None:
        super().__init__(NAMESPACE, APP_ID, app_must_match=True)
        self.connected = threading.Event()
        self.messages: queue.Queue[dict] = queue.Queue(maxsize=256)
        self.failed = threading.Event()

    def channel_connected(self) -> None:
        self.connected.set()

    def channel_disconnected(self) -> None:
        self.connected.clear()

    def receive_message(self, message, data: dict) -> bool:
        try:
            self.messages.put_nowait({"version": PROTOCOL_VERSION, "command": "message",
                                     "source": message.source_id, "namespace": message.namespace,
                                     "payload": data})
        except queue.Full:
            self.failed.set()
        return True


def validate_event(event: dict, destination: str) -> None:
    if not isinstance(event, dict) or event.get("version") != PROTOCOL_VERSION:
        raise RuntimeError("Unsupported native helper protocol")
    if event.get("event") == "message":
        if event.get("namespace") != NAMESPACE or event.get("destination") != destination:
            raise RuntimeError("Native helper attempted an unexpected Cast control route")
        if not isinstance(event.get("payload"), dict):
            raise RuntimeError("Invalid native control payload")
    elif event.get("event") not in {"negotiated", "stats", "finished", "error"}:
        raise RuntimeError("Unknown native helper event")
