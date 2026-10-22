import functools
import http.server
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock

from cast_to_tv.casting import CONTENT_TYPE, _StreamRequestHandler


class CastStreamHTTPTests(unittest.TestCase):
    def test_diagnostic_events_count_served_bytes_and_head_has_no_payload(self) -> None:
        diagnostics = Mock()
        self.server.RequestHandlerClass = functools.partial(
            _StreamRequestHandler, directory=self.directory.name, diagnostics=diagnostics)
        for method in ("GET", "HEAD"):
            with urllib.request.urlopen(urllib.request.Request(
                    f"{self.base_url}/chunk-stream0-00001.m4s", method=method), timeout=2) as response:
                response.read()
            # Complete is recorded after the HTTP response is copied; wait for
            # the server thread before inspecting telemetry.
            for _ in range(50):
                calls = [call for call in diagnostics.record.call_args_list
                         if call.args == ("http_complete",) and call.kwargs["method"] == method]
                if calls:
                    break
                threading.Event().wait(.01)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].kwargs["status"], 200)
            self.assertEqual(calls[0].kwargs["payload_bytes"], 17 if method == "GET" else 0)
            self.assertGreaterEqual(calls[0].kwargs["duration_ms"], 0)

    def test_encoder_can_replace_a_manifest_before_the_receiver_finishes_reading(self) -> None:
        root = Path(self.directory.name)
        manifest = root / "desktop.mpd"
        replacement = root / "desktop.mpd.tmp"
        original = manifest.read_bytes()
        replacement.write_bytes(b'<MPD type="dynamic" />')

        handler = _StreamRequestHandler.__new__(_StreamRequestHandler)
        handler.directory = str(root)
        handler.path = "/desktop.mpd"
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        with handler.send_head() as reader:
            replacement.replace(manifest)
            self.assertEqual(reader.read(), original)

        with urllib.request.urlopen(f"{self.base_url}/desktop.mpd", timeout=2) as response:
            self.assertEqual(response.read(), b'<MPD type="dynamic" />')

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "desktop.mpd").write_text("<MPD />", encoding="utf-8")
        (root / "chunk-stream0-00001.m4s").write_bytes(b"test MP4 fragment")
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            functools.partial(_StreamRequestHandler, directory=str(root)),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.origin = "https://www.gstatic.com"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def test_receiver_can_read_manifest_and_video_from_its_web_origin(self) -> None:
        for path, content_type in (("desktop.mpd", CONTENT_TYPE), ("chunk-stream0-00001.m4s", "video/mp4")):
            with self.subTest(path=path):
                request = urllib.request.Request(
                    f"{self.base_url}/{path}", headers={"Origin": self.origin},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.origin)
                    self.assertEqual(response.headers["Vary"], "Origin")
                    self.assertEqual(response.headers.get_content_type(), content_type)
                    self.assertIn("no-store", response.headers["Cache-Control"])
                    self.assertTrue(response.read())

    def test_receiver_preflight_allows_stream_headers(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/desktop.mpd", method="OPTIONS",
            headers={"Origin": self.origin, "Access-Control-Request-Method": "GET",
                     "Access-Control-Request-Headers": "Range, Content-Type"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.origin)
            allowed = response.headers["Access-Control-Allow-Headers"].lower()
            self.assertIn("range", allowed)
            self.assertIn("content-type", allowed)
            self.assertIn("GET", response.headers["Access-Control-Allow-Methods"])
            self.assertEqual(response.read(), b"")

    def test_errors_are_visible_to_the_receiver_instead_of_hidden_by_cors(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/missing.m4s", headers={"Origin": self.origin},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=2)
        error = raised.exception
        try:
            self.assertEqual(error.code, 404)
            self.assertEqual(error.headers["Access-Control-Allow-Origin"], self.origin)
        finally:
            error.close()


if __name__ == "__main__":
    unittest.main()
