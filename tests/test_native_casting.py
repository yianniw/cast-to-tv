import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cast_to_tv.casting import CastingError
from cast_to_tv.native_casting import NativeCastSession
from cast_to_tv.quality import VideoQuality


class NativeSessionTests(unittest.TestCase):
    @patch("cast_to_tv.native_casting.native_helper", return_value=Path("helper.exe"))
    @patch("cast_to_tv.native_casting.shutil.which", return_value="ffmpeg.exe")
    @patch("cast_to_tv.native_casting.run_probe")
    def test_all_quality_combinations_reach_native_capture(self, bridge, _ffmpeg, _helper):
        def run(*args, **kwargs):
            kwargs["on_event"]({"event": "stats", "sent": 15, "released": 14, "round_trip_ms": 4})
            args[5].wait(5)

        bridge.side_effect = run
        for resolution, size in (("720p", (404, 720)), ("1080p", (606, 1080))):
            for rate in (15, 30):
                with self.subTest(resolution=resolution, rate=rate):
                    display = dict(left=-1080, top=156, width=1080, height=1920)
                    session = NativeCastSession(Mock(), display, Mock(), include_audio=False,
                                                quality=VideoQuality(resolution, rate))
                    try:
                        session.start()
                        capture = bridge.call_args.args[7]
                        self.assertEqual((capture["output_width"], capture["output_height"]), size)
                        self.assertEqual(capture["fps"], rate)
                        self.assertEqual(capture["display"], display)
                        self.assertEqual(capture["capture"], "ddagrab")
                    finally:
                        self.assertEqual(session.stop(), [])

    def setUp(self) -> None:
        self.display = {"left": -1920, "top": 156, "width": 1920, "height": 1080}

    @patch("cast_to_tv.native_casting.native_helper", return_value=Path("helper.exe"))
    @patch("cast_to_tv.native_casting.shutil.which", return_value="ffmpeg.exe")
    @patch("cast_to_tv.native_casting.run_probe")
    def test_ready_requires_receiver_feedback_and_stop_releases_worker(self, bridge, _ffmpeg, _helper) -> None:
        entered = threading.Event()

        def run(*args, **kwargs):
            entered.set()
            callback = kwargs["on_event"]
            callback({"event": "negotiated", "playout_delay_ms": 200})
            self.assertFalse(session._ready.is_set())
            callback({"event": "stats", "sent": 30, "released": 29,
                      "round_trip_ms": 4, "audio_round_trip_ms": 3})
            args[5].wait(5)

        bridge.side_effect = run
        session = NativeCastSession(Mock(), self.display, Mock())
        try:
            session.start()
            self.assertTrue(entered.is_set())
            self.assertTrue(bridge.call_args.kwargs["system_audio"])
            capture = bridge.call_args.args[7]
            self.assertEqual(capture["display"]["left"], -1920)
            self.assertNotIn("duration", capture)
            self.assertEqual(session.stop(), [])
            self.assertFalse(session._worker.is_alive())
            self.assertIsNone(session._temporary)
        finally:
            session.stop()

    @patch("cast_to_tv.native_casting.native_helper", return_value=Path("helper.exe"))
    @patch("cast_to_tv.native_casting.shutil.which", return_value="ffmpeg.exe")
    @patch("cast_to_tv.native_casting.run_probe", side_effect=RuntimeError("negotiation failed"))
    def test_failed_negotiation_reports_startup_error_and_cleans_up(self, _bridge, _ffmpeg, _helper) -> None:
        failure = Mock()
        session = NativeCastSession(Mock(), self.display, failure)
        try:
            with self.assertLogs("cast_to_tv.native_casting", level="ERROR"):
                with self.assertRaisesRegex(CastingError, "negotiation failed"):
                    session.start()
            failure.assert_not_called()
        finally:
            session.stop()
        self.assertIsNone(session._temporary)

    @patch("cast_to_tv.native_casting.native_helper", return_value=Path("helper.exe"))
    @patch("cast_to_tv.native_casting.shutil.which", return_value="ffmpeg.exe")
    @patch("cast_to_tv.native_casting.run_probe")
    def test_helper_failure_after_start_reaches_gui_callback(self, bridge, _ffmpeg, _helper) -> None:
        crash = threading.Event()
        reported = threading.Event()
        failure = Mock(side_effect=lambda _: reported.set())

        def run(*args, **kwargs):
            kwargs["on_event"]({"event": "stats", "sent": 30, "released": 29,
                                "round_trip_ms": 4})
            crash.wait(5)
            raise RuntimeError("helper crashed")

        bridge.side_effect = run
        session = NativeCastSession(Mock(), self.display, failure, include_audio=False)
        try:
            with self.assertLogs("cast_to_tv.native_casting", level="ERROR"):
                session.start()
                crash.set()
                self.assertTrue(reported.wait(2))
            failure.assert_called_once_with("helper crashed")
        finally:
            crash.set()
            session.stop()

    @patch("cast_to_tv.native_casting.native_helper", return_value=Path("helper.exe"))
    @patch("cast_to_tv.native_casting.shutil.which", return_value="ffmpeg.exe")
    @patch("cast_to_tv.native_casting.run_probe")
    def test_receiver_cleanup_failure_is_returned_to_gui(self, bridge, _ffmpeg, _helper) -> None:
        def run(*args, **kwargs):
            kwargs["on_event"]({"event": "stats", "sent": 30, "released": 29, "round_trip_ms": 4})
            args[5].wait(5)
            kwargs["on_event"]({"event": "cleanup_error", "message": "TV quit timed out"})
        bridge.side_effect = run
        session = NativeCastSession(Mock(), self.display, Mock(), include_audio=False)
        try:
            session.start()
            self.assertEqual(session.stop(), ["TV quit timed out"])
        finally:
            session.stop()


if __name__ == "__main__":
    unittest.main()
