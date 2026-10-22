import queue
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import test_display_switching as switching
from cast_to_tv.casting import CastSession
from cast_to_tv.errors import CastingCancelled, CastingError
from cast_to_tv.native_casting import NativeCastSession
from cast_to_tv.native_probe import run_probe
from cast_to_tv.recovery import ReceiverCleanupSession, ReceiverOwnership, RecoveryError, failure_message, native_failure, prepare_recovery_receiver


class RecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = switching.DisplaySwitchingTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.app = self.fixture.app
        self.app.recovery_controls = Mock()
        self.app.retry_button = Mock()
        self.app.video_only_button = Mock()
        self.app._reset_recovery()
        self.request = replace(self.app._active_request, include_audio=True)
        self.app._active_request = self.request

    def fail(self, session=None, kind="audio"):
        self.app._post_session_event(session or self.app.session, "capture_error",
                                     failure_message(RecoveryError("Simulated interruption", kind)))
        self.app._process_events()

    def worker(self):
        self.fixture.run_worker()

    def test_both_modes_restart_once_and_keep_all_active_choices(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.app._reset_recovery()
                self.request = replace(self.request, native=native)
                self.app._active_request = self.request
                old = self.app.session
                self.fail(kind="connection")
                self.assertIs(self.app.session, old)
                self.assertTrue(self.app._recovery_cleanup)
                self.worker()
                self.assertIsNot(self.app.session, old)
                self.assertEqual(self.app._active_request, self.request)
                self.assertTrue(self.app.session.recovering)
                self.worker()
                self.assertFalse(self.app.busy)
                self.fail(kind="connection")
                self.worker()
                self.assertIsNone(self.app.session)
                self.assertEqual(self.app._recovery_request, self.request)
                self.app.video_only_button.grid_remove.assert_called()
                self.assertFalse(self.app._recovering)
                self.app.retry_cast()
                self.worker()
        self.fixture.save.assert_not_called()
        self.fixture.error.assert_not_called()

    def offer_audio_recovery(self):
        self.fail()
        self.worker()
        self.worker()
        self.fail()
        self.worker()
        self.assertIsNone(self.app.session)

    def test_video_only_fallback_in_both_modes_does_not_save_or_toggle_audio(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.app._reset_recovery()
                self.app._active_request = replace(self.request, native=native)
                self.app.session = self.fixture.old
                self.offer_audio_recovery()
                self.app.video_only_button.grid.assert_called()
                self.app.retry_cast(without_audio=True)
                self.assertFalse(self.app._active_request.include_audio)
                self.assertTrue(self.app._active_request.native == native)
                self.worker()
                self.assertTrue(self.app.include_audio.get())
                self.app.include_audio.set.assert_not_called()
                self.assertIn("Video only", self.app.status.set.call_args.args[0])
        self.fixture.save.assert_not_called()

    def test_stop_during_failure_cleanup_cancels_restart(self):
        self.fail()
        self.app.stop_cast()
        self.worker()
        self.assertIsNone(self.app.session)
        self.assertIsNone(self.app._recovery_request)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_stop_during_automatic_start_signals_cancellation_and_never_restarts(self):
        self.fail()
        self.worker()
        replacement = self.app.session
        start = self.fixture.thread.call_args.kwargs
        self.app.stop_cast()
        replacement.cancel_start.assert_called_once()
        start["target"](*start["args"])
        self.app._process_events()
        self.worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(len(switching.FakeNativeSession.instances), 2)

    def test_close_during_failure_cleanup_cancels_restart(self):
        self.fail()
        self.app.close()
        self.worker()
        self.assertIsNone(self.app.session)
        self.assertTrue(self.app._shutdown_started)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_cleanup_failure_blocks_automatic_restart(self):
        self.app.session.stop.return_value = ["Worker is still closing"]
        self.fail()
        self.worker()
        self.assertIs(self.app.session, self.fixture.old)
        self.assertIsNone(self.app._recovery_request)
        self.assertFalse(self.app._recovering)
        self.app.session.stop.return_value = []
        self.app.stop_cast()
        self.worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(len(switching.FakeNativeSession.instances), 1)

    def test_initial_connection_failure_retries_then_failed_start_offers_controls(self):
        old = self.app.session
        old.start.side_effect = RecoveryError("Cannot connect", "connection")
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.app._start_worker(old)
        self.app._process_events()
        replacement = self.app.session
        replacement.start.side_effect = RecoveryError("Still cannot connect", "connection")
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.worker()
        self.assertIsNone(self.app.session)
        self.assertIsNotNone(self.app._recovery_request)
        self.assertEqual(len(switching.FakeNativeSession.instances), 2)

    def test_manual_retry_is_bounded_and_old_events_are_ignored(self):
        old = self.app.session
        self.offer_audio_recovery()
        self.app.retry_cast()
        replacement = self.app.session
        self.worker()
        self.fail(old)
        self.assertIs(self.app.session, replacement)
        self.assertFalse(self.app.busy)
        self.fail()
        self.worker()
        self.assertIsNone(self.app.session)
        self.assertIsNotNone(self.app._recovery_request)
        self.assertEqual(len(switching.FakeNativeSession.instances), 3)

    def test_stop_dismisses_offer_and_close_does_not_leave_controls(self):
        self.offer_audio_recovery()
        self.app.stop_cast()
        self.assertIsNone(self.app._recovery_request)
        self.app.retry_cast()
        self.assertIsNone(self.app.session)
        self.app.close()
        self.app.recovery_controls.grid_remove.assert_called()

    def test_new_tv_selection_dismisses_the_previous_recovery_offer(self):
        self.offer_audio_recovery()
        self.app._tv_selected()
        self.assertIsNone(self.app._recovery_request)
        self.app.retry_cast()
        self.assertIsNone(self.app.session)

    def test_receiver_ownership_survives_cleanup_and_reaches_replacement(self):
        ownership = ReceiverOwnership("app", "route")
        self.app.session.recovery_receiver = ownership
        self.fail(kind="connection")
        self.worker()
        self.assertEqual(self.app._active_request.recovery_receiver, ownership)
        self.assertEqual(self.app.session.previous_receiver, ownership)

    def test_stop_after_second_control_loss_releases_the_previous_receiver(self):
        self.app.session.recovery_receiver = ReceiverOwnership("app", "route")
        self.fail(kind="connection")
        self.worker()
        self.worker()
        self.fail(kind="connection")
        self.worker()
        self.app.stop_cast()
        self.assertIsInstance(self.app.session, ReceiverCleanupSession)
        with patch.object(self.app.session, "stop", return_value=[]) as release:
            self.worker()
        release.assert_called_once()
        self.assertIsNone(self.app.session)

    def test_display_switch_does_not_reset_the_automatic_retry_allowance(self):
        self.fail()
        self.worker()
        self.worker()
        self.fixture.select(1)
        self.worker()
        self.worker()
        self.assertTrue(self.app._automatic_recovery_used)
        self.fail()
        self.worker()
        self.assertIsNone(self.app.session)
        self.assertIsNotNone(self.app._recovery_request)

    def test_preparation_failure_during_recovery_offers_retry(self):
        self.fail()
        with patch("cast_to_tv.app.NativeCastSession", side_effect=OSError("Preparation failed")):
            with self.assertLogs("cast_to_tv.app", level="ERROR"):
                self.worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(self.app._recovery_request, self.request)


class RecoveryDetectionTests(unittest.TestCase):
    def test_encoder_pipe_failure_does_not_offer_audio_recovery(self):
        failed = Mock()
        session = CastSession(Mock(), {}, failed)
        session.process = Mock()
        session.process.stdin.write.side_effect = BrokenPipeError("Encoder exited")
        loopback = MagicMock()
        loopback.recorder.return_value.__enter__.return_value.record.return_value = [0]
        with patch("cast_to_tv.casting.audio_to_pcm16", return_value=b"pcm"):
            with self.assertLogs("cast_to_tv.casting", level="ERROR"):
                session._capture_audio(loopback)
        session._stop_event = Mock()
        session._stop_event.wait.return_value = False
        session._monitor_capture()
        self.assertIsNone(getattr(failed.call_args.args[0], "kind", None))

    def test_media_verification_waits_for_fresh_status_and_honors_cancel(self):
        owner = ReceiverOwnership("app", "route", "original-url")
        device = Mock(app_id="app")
        device.socket_client.destination_id = "route"
        device.media_controller.status.content_id = None
        device.media_controller.update_status.side_effect = lambda callback_function: callback_function(
            True, {"status": [{"media": {"contentId": "original-url"}}]},
        )
        prepare_recovery_receiver(device, owner)
        device.quit_app.assert_called_once()
        device.quit_app.reset_mock()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CastingCancelled):
            prepare_recovery_receiver(device, owner, cancel)
        device.quit_app.assert_not_called()

    def test_receiver_cleanup_recreates_closed_socket_and_preserves_other_playback(self):
        old = Mock()
        old.socket_client.ident = 123
        old.socket_client.is_alive.return_value = False
        new = Mock(app_id="different-app")
        cleanup = ReceiverCleanupSession(old, None, ReceiverOwnership("app", "route"))
        with patch("pychromecast.get_chromecast_from_cast_info", return_value=new), \
                patch("cast_to_tv.receivers.discovery.wait_for_cast"):
            self.assertEqual(cleanup.stop(), [])
        self.assertIs(cleanup.cast_device, new)
        new.quit_app.assert_not_called()

    def test_recovery_releases_only_the_same_receiver_route_and_media(self):
        owner = ReceiverOwnership("our-app", "our-route", "our-url")
        for app_id, route, content, allowed in (
            (None, "", "", True), ("our-app", "our-route", "our-url", True),
            ("another-app", "our-route", "our-url", False),
            ("our-app", "another-route", "our-url", False),
            ("our-app", "our-route", "another-url", False),
        ):
            with self.subTest(app_id=app_id, route=route, content=content):
                device = Mock(app_id=app_id)
                device.socket_client.destination_id = route
                device.media_controller.status.content_id = content
                device.media_controller.update_status.side_effect = lambda callback_function: callback_function(
                    True, {"status": [{"media": {"contentId": content}}]},
                )
                if allowed:
                    prepare_recovery_receiver(device, owner)
                    self.assertEqual(device.quit_app.call_count, int(bool(app_id)))
                else:
                    with self.assertRaisesRegex(CastingError, "TV is busy"):
                        prepare_recovery_receiver(device, owner)
                    device.quit_app.assert_not_called()

    def test_compatibility_can_finish_local_cleanup_after_control_loss(self):
        device = Mock(app_id="CC1AD845")
        device.socket_client.is_connected = False
        device.media_controller.status.content_id = "our-url"
        session = CastSession(device, {}, Mock())
        session._cast_playback_started = True
        session._loaded_stream_url = "our-url"
        session.recovery_receiver = ReceiverOwnership("CC1AD845", "our-route", "our-url")
        self.assertEqual(session.stop(), [])
        device.quit_app.assert_not_called()
        self.assertIsNotNone(session.recovery_receiver)

    def test_native_recovery_does_not_launch_over_another_receiver(self):
        device = Mock(app_id="other-app")
        with tempfile.TemporaryDirectory() as directory, \
                patch("cast_to_tv.native_probe.wait_for_cast"), \
                patch("cast_to_tv.native_probe.subprocess.Popen") as helper:
            with self.assertRaisesRegex(CastingError, "TV is busy"):
                run_probe(device, Path("helper"), Path(), 200, queue.Queue(), threading.Event(),
                          Path(directory) / "report.json", preserve_receiver=True)
            helper.assert_not_called()
            device.quit_app.assert_not_called()

    def test_native_start_connection_failure_retains_recovery_category(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("cast_to_tv.native_probe.wait_for_cast", side_effect=TimeoutError("unreachable")):
            with self.assertRaises(RecoveryError) as error:
                run_probe(Mock(), Path("helper"), Path(), 200, queue.Queue(), threading.Event(),
                          Path(directory) / "report.json")
            self.assertEqual(error.exception.kind, "connection")

    def test_compatibility_detects_disconnect_and_prioritizes_audio_over_encoder_exit(self):
        for kind in ("connection", "audio"):
            with self.subTest(kind=kind):
                failed = Mock()
                session = CastSession(Mock(), {}, failed)
                session.process = Mock()
                session._stop_event = Mock()
                session._stop_event.wait.return_value = False
                session.cast_device.socket_client.is_connected = kind != "connection"
                if kind == "audio":
                    session._audio_thread = Mock(is_alive=Mock(return_value=False))
                    session._audio_errors.append("Output device was removed")
                session._monitor_capture()
                self.assertEqual(failed.call_args.args[0].kind, kind)

    def test_native_audio_classifier_excludes_general_capture_and_protocol_errors(self):
        for message in ("Windows playback device was disconnected or disabled.",
                        "No default Windows playback device; disabled", "Read system audio (HRESULT 0x1)"):
            self.assertEqual(native_failure(message).kind, "audio")
        for message in ("Desktop FFmpeg exited with code 1", "Unsupported native helper protocol"):
            self.assertNotIsInstance(native_failure(message), RecoveryError)

    def test_native_startup_preserves_recovery_category_and_busy_receiver_guard(self):
        error = RecoveryError("No audio endpoint", "audio")
        with patch("cast_to_tv.native_casting.native_helper", return_value=Path("helper")), \
                patch("cast_to_tv.native_casting.shutil.which", return_value="ffmpeg"), \
                patch("cast_to_tv.native_casting.run_probe", side_effect=error) as bridge:
            session = NativeCastSession(Mock(), dict(width=1920, height=1080), Mock())
            session.recovering = True
            try:
                with self.assertLogs("cast_to_tv.native_casting", level="ERROR"):
                    with self.assertRaises(RecoveryError) as raised:
                        session.start()
                self.assertEqual(raised.exception.kind, "audio")
                self.assertTrue(bridge.call_args.kwargs["preserve_receiver"])
            finally:
                session.stop()

    def test_compatibility_recovery_does_not_take_over_a_busy_receiver(self):
        device = Mock(app_id="other-app")
        session = CastSession(device, {}, Mock())
        session.recovering = True
        with patch("cast_to_tv.casting.wait_for_cast"), patch("cast_to_tv.casting.ffmpeg_executable") as ffmpeg:
            with self.assertRaisesRegex(CastingError, "TV is busy"):
                session.start()
            ffmpeg.assert_not_called()
            device.quit_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
