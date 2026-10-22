"""Contracts kept by the historical modules after domain extraction."""
import inspect
import unittest
from pathlib import Path

from cast_to_tv import app, casting, native_probe
from cast_to_tv.capture import audio, displays, ffmpeg
from cast_to_tv.compatibility import manifest, server
from cast_to_tv.errors import CastingCancelled, CastingError, check_cancelled
from cast_to_tv.native import patterns, protocol


class PublicApiTests(unittest.TestCase):
    def test_historical_exports_share_types_and_implementations(self):
        # Identity matters: errors from extracted code must still be catchable
        # by clients importing from casting, and controller subclasses must match.
        exports = {
            "CastingError": CastingError,
            "CastingCancelled": CastingCancelled,
            "check_cancelled": check_cancelled,
            "audio_to_pcm16": audio.audio_to_pcm16,
            "default_loopback_microphone": audio.default_loopback_microphone,
            "list_displays": displays.list_displays,
            "ffmpeg_command": ffmpeg.ffmpeg_command,
            "ffmpeg_executable": ffmpeg.ffmpeg_executable,
            "stream_is_ready": manifest.stream_is_ready,
            "_StreamRequestHandler": server._StreamRequestHandler,
        }
        for name, implementation in exports.items():
            with self.subTest(name=name):
                self.assertIs(getattr(casting, name), implementation)
        self.assertIs(native_probe.StreamingController, protocol.StreamingController)
        self.assertIs(native_probe.validate_event, protocol.validate_event)
        self.assertIs(native_probe.generate_clip, patterns.generate_clip)
        self.assertEqual(native_probe.APP_ID, protocol.APP_ID)
        self.assertEqual(native_probe.NAMESPACE, protocol.NAMESPACE)
        self.assertEqual(native_probe.PROTOCOL_VERSION, protocol.PROTOCOL_VERSION)
        self.assertEqual(casting.CONTENT_TYPE, server.CONTENT_TYPE)
        self.assertTrue(issubclass(casting.CastingCancelled, casting.CastingError))

    def test_existing_positional_and_keyword_call_shapes_remain_valid(self):
        display = dict(left=0, top=0, width=1280, height=720)
        signature = inspect.signature(casting.ffmpeg_command)
        signature.bind("ffmpeg", display, Path("stream"), True, app.DEFAULT_QUALITY)
        signature.bind("ffmpeg", display, Path("stream"), silent_audio=True,
                       quality=app.DEFAULT_QUALITY)
        self.assertEqual(tuple(inspect.signature(casting.discover_cast_devices).parameters), ("timeout",))
        self.assertEqual(tuple(inspect.signature(casting.wait_for_cast).parameters),
                         ("device", "cancel", "timeout"))
        signature = inspect.signature(native_probe.run_probe)
        self.assertEqual(tuple(signature.parameters), (
            "device", "helper", "clip", "playout", "events", "cancel", "report",
            "desktop", "audio_clip", "on_event", "system_audio", "preserve_receiver",
        ))
        for name in ("desktop", "audio_clip", "on_event"):
            self.assertIsNone(signature.parameters[name].default)
        self.assertIs(signature.parameters["system_audio"].default, False)
        self.assertIs(signature.parameters["preserve_receiver"].default, False)
        self.assertTrue(callable(app.CastToTVApp))
        self.assertTrue(callable(app.CastRequest))
        self.assertTrue(callable(app.main))
        self.assertTrue(callable(native_probe.main))


if __name__ == "__main__":
    unittest.main()
