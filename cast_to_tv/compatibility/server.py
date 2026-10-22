"""Serve live DASH files without blocking manifest replacement on Windows."""
from __future__ import annotations

import http.server
import io
import logging
import time
from pathlib import Path
from typing import BinaryIO, Callable

from ..diagnostics import SessionDiagnostics

LOGGER = logging.getLogger("cast_to_tv.casting")
CONTENT_TYPE = "application/dash+xml"


class _StreamRequestHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".mpd": CONTENT_TYPE,
        ".m4s": "video/mp4",
    }

    def __init__(self, *args: object, directory: str,
                 diagnostics: SessionDiagnostics | None = None, **kwargs: object) -> None:
        self.diagnostics = diagnostics
        self._payload_bytes = 0
        self._response_status = 0
        super().__init__(*args, directory=directory, **kwargs)

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_status = int(code)
        super().send_response(code, message)

    def copyfile(self, source: BinaryIO, outputfile: BinaryIO) -> None:
        if self.diagnostics is None:
            super().copyfile(source, outputfile)
            return
        while data := source.read(64 * 1024):
            outputfile.write(data)
            self._payload_bytes += len(data)

    def _timed_request(self, handler: Callable[[], None]) -> None:
        if self.diagnostics is None:
            handler()
            return
        started = time.monotonic()
        self._payload_bytes = 0
        self._response_status = 0
        error = None
        self.diagnostics.record("http_started", method=self.command, path=self.path)
        try:
            handler()
        except OSError as exc:
            error = str(exc)
            raise
        finally:
            self.diagnostics.record("http_complete", method=self.command, path=self.path,
                                    status=self._response_status, payload_bytes=self._payload_bytes,
                                    duration_ms=(time.monotonic() - started) * 1000, error=error)

    def do_GET(self) -> None:
        self._timed_request(super().do_GET)

    def do_HEAD(self) -> None:
        self._timed_request(super().do_HEAD)

    def send_head(self) -> BinaryIO | None:
        path = Path(self.translate_path(self.path))
        if path.suffix.lower() != ".mpd":
            return super().send_head()
        try:
            # Close the file before sending to a potentially slow receiver.
            # Windows will otherwise block FFmpeg's next manifest replacement.
            contents = path.read_bytes()
        except OSError:
            self.send_error(http.HTTPStatus.NOT_FOUND, "Manifest not found")
            return None
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(contents)))
        self.end_headers()
        return io.BytesIO(contents)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        # The Cast receiver loads the stream from a different web origin.
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept-Encoding, Range")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(http.HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format_string: str, *args: object) -> None:
        LOGGER.debug("Stream server: " + format_string, *args)
