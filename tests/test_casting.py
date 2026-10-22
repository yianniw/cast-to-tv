import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pychromecast.error import RequestTimeout
from cast_to_tv.quality import VideoQuality

from cast_to_tv.casting import (
    CONTENT_TYPE,
    CastSession,
    CastingError,
    audio_to_pcm16,
    ffmpeg_command,
    ffmpeg_executable,
    stream_is_ready,
)


class FFmpegCommandTests(unittest.TestCase):
    def test_quality_controls_capture_output_and_two_second_keyframes(self):
        display = dict(left=-2560, top=156, width=2560, height=1440)
        for resolution, size in (("720p", "1280:720"), ("1080p", "1920:1080")):
            for rate in (15, 30):
                with self.subTest(resolution=resolution, rate=rate):
                    command = ffmpeg_command("ffmpeg", display, Path("stream"),
                                             quality=VideoQuality(resolution, rate))
                    for flag in ("-framerate", "-r"):
                        self.assertEqual(command[command.index(flag) + 1], str(rate))
                    for flag in ("-g", "-keyint_min"):
                        self.assertEqual(command[command.index(flag) + 1], str(rate * 2))
                    self.assertEqual(command[command.index("-vf") + 1], f"scale={size}")
                    self.assertIn("2560x1440", command)
                    self.assertEqual(command[command.index("-offset_x") + 1], "-2560")

    def test_explicit_silent_baseline_keeps_desktop_input_and_does_not_read_pcm(self) -> None:
        display = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
        command = ffmpeg_command("ffmpeg", display, Path("stream"), silent_audio=True)
        self.assertIn("gdigrab", command)
        self.assertIn("-1920", command)
        self.assertIn("anullsrc=r=48000:cl=stereo", command)
        self.assertNotIn("pipe:0", command)
        self.assertNotIn("s16le", command)

    def test_builds_live_h264_aac_dash_for_selected_display(self) -> None:
        display = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
        with tempfile.TemporaryDirectory() as temp_dir:
            command = ffmpeg_command("ffmpeg", display, Path(temp_dir))

        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-f", command)
        self.assertIn("gdigrab", command)
        self.assertIn("s16le", command)
        self.assertIn("pipe:0", command)
        self.assertEqual(command[command.index("-vf") + 1], "scale=1920:1080")
        self.assertIn("2560x1440", command)
        self.assertIn("libx264", command)
        self.assertIn("aac", command)
        self.assertEqual(command[-1], str(Path(temp_dir) / "desktop.mpd"))
        self.assertEqual(command[command.index("-seg_duration") + 1], "2")
        self.assertEqual(CONTENT_TYPE, "application/dash+xml")

    def test_audio_to_pcm16_interleaves_clipped_stereo_samples(self) -> None:
        import numpy

        samples = numpy.array(
            [[-1.0, 1.0], [0.5, -0.5]],
            dtype=numpy.float32,
        )
        pcm = numpy.frombuffer(audio_to_pcm16(samples), dtype="<i2")
        numpy.testing.assert_array_equal(pcm, [-32767, 32767, 16383, -16383])

    def test_audio_to_pcm16_rejects_mono_capture(self) -> None:
        import numpy

        with self.assertRaisesRegex(CastingError, "at least two audio channels"):
            audio_to_pcm16(numpy.zeros((5, 1), dtype=numpy.float32))

    @patch("cast_to_tv.casting.shutil.which", return_value=None)
    def test_reports_missing_ffmpeg(self, _which: object) -> None:
        with self.assertRaisesRegex(CastingError, "FFmpeg was not found"):
            ffmpeg_executable()


class ConnectionTests(unittest.TestCase):
    def playing_session(self):
        device = Mock()
        device.app_id = "CC1AD845"
        device.media_controller.status.content_id = "http://192.0.2.1:1234/desktop.mpd"
        session = CastSession(device, {}, Mock())
        session._loaded_stream_url = device.media_controller.status.content_id
        session._cast_playback_started = True
        return session, device

    def test_stop_quits_owned_compatibility_receiver(self) -> None:
        session, device = self.playing_session()
        self.assertEqual(session.stop(), [])
        device.quit_app.assert_called_once_with(timeout=10)
        self.assertFalse(session._cast_playback_started)

    def test_stop_does_not_quit_unrelated_receiver_or_other_senders_media(self) -> None:
        for changed_app, changed_content in ((True, False), (False, True)):
            with self.subTest(changed_app=changed_app, changed_content=changed_content):
                session, device = self.playing_session()
                if changed_app:
                    device.app_id = "another-app"
                if changed_content:
                    device.media_controller.status.content_id = "https://example.com/another-stream"
                self.assertEqual(session.stop(), [])
                device.quit_app.assert_not_called()
                device.media_controller.stop.assert_not_called()

    def test_stop_receiver_failure_can_be_retried(self) -> None:
        session, device = self.playing_session()
        device.quit_app.side_effect = RequestTimeout("quit app", 10)
        with self.assertLogs("cast_to_tv.casting", level="WARNING"):
            errors = session.stop()
        self.assertTrue(errors)
        self.assertTrue(session._cast_playback_started)
        device.quit_app.side_effect = None
        self.assertEqual(session.stop(), [])
        self.assertEqual(device.quit_app.call_count, 2)
        self.assertFalse(session._cast_playback_started)

    def test_restarts_a_previous_default_receiver(self) -> None:
        device = Mock()
        device.status.app_id = "CC1AD845"
        session = CastSession(device, {}, Mock())

        session._reset_media_receiver()

        device.quit_app.assert_called_once_with(timeout=10)

    def test_does_not_reset_an_unrelated_receiver_app(self) -> None:
        device = Mock()
        device.status.app_id = "other-app"
        session = CastSession(device, {}, Mock())

        session._reset_media_receiver()

        device.quit_app.assert_not_called()

    def test_receiver_reset_failure_prevents_starting_capture(self) -> None:
        device = Mock()
        device.status.app_id = "CC1AD845"
        device.quit_app.side_effect = RequestTimeout("quit app", 10)
        session = CastSession(device, {}, Mock())

        with patch("cast_to_tv.casting.ffmpeg_executable") as ffmpeg:
            with self.assertRaisesRegex(CastingError, "Could not reset"):
                session.start()
        ffmpeg.assert_not_called()
        self.assertIsNone(session.process)

    @patch("cast_to_tv.casting.ffmpeg_executable")
    def test_connection_timeout_is_reported_before_starting_capture(self, ffmpeg: Mock) -> None:
        device = Mock()
        device.cast_info.host = "192.0.2.10"
        device.wait.side_effect = RequestTimeout("wait", 10)
        session = CastSession(device, {}, Mock())

        with patch("cast_to_tv.casting.wait_for_cast", side_effect=RequestTimeout("wait", 10)):
            with self.assertRaisesRegex(CastingError, "Could not connect to the TV at 192.0.2.10"):
                session.start()

        ffmpeg.assert_not_called()
        self.assertIsNone(session.process)
        self.assertIsNone(session.server)
        self.assertIsNone(session.temp_dir)


class StreamStartupTests(unittest.TestCase):
    def write_stream(self, root: Path, count: int = 3, start_number: int = 1) -> Path:
        representations = []
        for stream_id, track_type in ((0, "video"), (1, "audio")):
            representations.append(
                f'<Representation id="{stream_id}" mimeType="{track_type}/mp4">'
                '<SegmentTemplate initialization="init-stream$RepresentationID$.m4s" '
                'media="chunk-stream$RepresentationID$-$Number%05d$.m4s" '
                f'startNumber="{start_number}"><SegmentTimeline>'
                f'<S t="0" d="2" r="{count - 1}" />'
                '</SegmentTimeline></SegmentTemplate></Representation>'
            )
            (root / f"init-stream{stream_id}.m4s").write_bytes(b"initialization")
            for number in range(start_number, start_number + count):
                (root / f"chunk-stream{stream_id}-{number:05d}.m4s").write_bytes(b"media")
        manifest = root / "desktop.mpd"
        manifest.write_text(
            '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="dynamic">'
            '<Period><AdaptationSet>' + ''.join(representations)
            + '</AdaptationSet></Period></MPD>', encoding="utf-8",
        )
        return manifest

    def test_requires_complete_audio_and_video_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(stream_is_ready(root / "desktop.mpd"))
            self.assertFalse(stream_is_ready(self.write_stream(root, count=2)))
            manifest = self.write_stream(root)
            audio = root / "chunk-stream1-00003.m4s"
            audio.unlink()
            self.assertFalse(stream_is_ready(manifest))
            audio.write_bytes(b"")
            self.assertFalse(stream_is_ready(manifest))
            audio.write_bytes(b"media")
            self.assertTrue(stream_is_ready(manifest))

    def test_requires_initialization_file_in_the_served_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.write_stream(root)
            (root / "init-stream0.m4s").unlink()
            self.assertFalse(stream_is_ready(manifest))
            (root / "init-stream0.m4s").write_bytes(b"initialization")
            self.assertTrue(stream_is_ready(manifest))

    def test_reads_a_manifest_after_the_live_window_has_advanced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(stream_is_ready(self.write_stream(Path(directory), start_number=20)))

    def test_does_not_accept_an_incomplete_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "desktop.mpd"
            manifest.write_text('<MPD type="dynamic">', encoding="utf-8")
            self.assertFalse(stream_is_ready(manifest))

    def make_session(self, state: str, idle_reason: str | None = None) -> CastSession:
        device = Mock()
        device.media_controller.status = SimpleNamespace(
            content_id="http://pc/desktop.mpd", player_state=state, idle_reason=idle_reason,
            current_time=0.0,
        )
        return CastSession(device, {}, Mock())

    def test_an_active_loading_session_does_not_count_as_playback(self) -> None:
        session = self.make_session("IDLE")
        session.cast_device.media_controller.session_active_event.set()
        with self.assertRaisesRegex(CastingError, "did not start playing"):
            session._wait_for_playback("http://pc/desktop.mpd", timeout=0.01)

    def test_playing_an_old_stream_does_not_count_as_playback(self) -> None:
        session = self.make_session("PLAYING")
        with self.assertRaisesRegex(CastingError, "did not start playing"):
            session._wait_for_playback("http://pc/new.mpd", timeout=0.01)
        session.cast_device.media_controller.update_status.assert_not_called()

    def test_reports_receiver_error_even_when_the_session_is_active(self) -> None:
        session = self.make_session("IDLE", "ERROR")
        with self.assertRaisesRegex(CastingError, "TV rejected"):
            session._wait_for_playback("http://pc/desktop.mpd")

    def test_accepts_actual_playback(self) -> None:
        session = self.make_session("PLAYING")

        def advance_playback(_timeout: float) -> None:
            session.cast_device.media_controller.status.current_time += 1

        with patch.object(session._stop_event, "wait", side_effect=advance_playback):
            session._wait_for_playback("http://pc/desktop.mpd")

    def test_playing_with_a_frozen_clock_does_not_count_as_playback(self) -> None:
        session = self.make_session("PLAYING")
        with self.assertRaisesRegex(CastingError, "did not start playing"):
            session._wait_for_playback("http://pc/desktop.mpd", timeout=0.01)
        session.cast_device.media_controller.update_status.assert_called_once()

    def test_waits_through_buffering_until_video_plays(self) -> None:
        session = self.make_session("BUFFERING")

        def start_playing(_timeout: float) -> None:
            session.cast_device.media_controller.status.player_state = "PLAYING"
            session.cast_device.media_controller.status.current_time += 1

        with patch.object(session._stop_event, "wait", side_effect=start_playing) as wait:
            session._wait_for_playback("http://pc/desktop.mpd")
        self.assertEqual(wait.call_count, 2)

    def test_reports_receiver_error_after_playback_has_started(self) -> None:
        session = self.make_session("IDLE", "ERROR")
        session.process = Mock()
        session.process.poll.return_value = None

        with patch.object(session._stop_event, "wait", return_value=False):
            session._monitor_capture()

        session.on_failure.assert_called_once_with(
            "The TV reported a playback error and stopped the live stream."
        )


if __name__ == "__main__":
    unittest.main()
