import queue
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
from uuid import UUID

from pychromecast.error import RequestTimeout

from cast_to_tv.casting import CastSession, CastingCancelled, CastingError, wait_for_cast
from cast_to_tv.native_casting import NativeCastSession
from cast_to_tv.native_probe import run_probe


class StartupCancellationTests(unittest.TestCase):
    def test_reconnect_uses_verified_address_when_receiver_withdraws_discovery_service(self):
        device = Mock()
        device.cast_info = SimpleNamespace(host='192.0.2.1', port=8009,
                                          uuid=UUID('11111111-2222-4333-8444-555555555555'))
        shared_services = device.socket_client.services = set()
        with patch('cast_to_tv.casting.get_device_info', return_value=SimpleNamespace(uuid=device.cast_info.uuid)):
            wait_for_cast(device, threading.Event(), 10)
        self.assertEqual(shared_services, set())
        self.assertEqual({(service.host, service.port) for service in device.socket_client.services},
                         {('192.0.2.1', 8009)})
        device.wait.assert_called_once()

    def test_reconnect_does_not_connect_to_a_different_tv_at_the_old_address(self):
        device = Mock()
        device.socket_client.services = set()
        with patch('cast_to_tv.casting.get_device_info', return_value=SimpleNamespace(uuid='other')):
            with self.assertRaisesRegex(CastingError, 'address could not be verified'):
                wait_for_cast(device, threading.Event(), 10)
        device.wait.assert_not_called()

    def test_cancel_during_address_verification_prevents_connection(self):
        device = Mock()
        device.socket_client.services = set()
        cancel = threading.Event()
        with patch('cast_to_tv.casting.get_device_info', side_effect=lambda *args, **kwargs: cancel.set()):
            with self.assertRaises(CastingCancelled):
                wait_for_cast(device, cancel, 10)
        device.wait.assert_not_called()

    def test_connection_wait_can_be_cancelled_between_short_readiness_polls(self):
        device = Mock()
        cancel = threading.Event()

        def waiting(timeout):
            self.assertLessEqual(timeout, .2)
            cancel.set()
            raise RequestTimeout("wait", timeout)

        device.wait.side_effect = waiting
        with self.assertRaises(CastingCancelled):
            wait_for_cast(device, cancel, 15)
        device.wait.assert_called_once()

    def test_connection_readiness_does_not_override_a_concurrent_cancel(self):
        device = Mock()
        cancel = threading.Event()
        device.wait.side_effect = lambda timeout: cancel.set()
        with self.assertRaises(CastingCancelled):
            wait_for_cast(device, cancel, 15)

    def test_connection_polling_retains_the_overall_timeout(self):
        device = Mock()
        device.wait.side_effect = RequestTimeout("wait", .2)
        with patch("cast_to_tv.casting.time.monotonic", side_effect=[0, 0, 16]):
            with self.assertRaises(RequestTimeout):
                wait_for_cast(device, threading.Event(), 15)

    def test_cancel_before_start_allocates_nothing_in_either_mode(self):
        for session_type in (CastSession, NativeCastSession):
            with self.subTest(session=session_type.__name__):
                device = Mock()
                session = session_type(device, {}, Mock())
                session.cancel_start()
                with self.assertRaises(CastingCancelled):
                    session.start()
                device.wait.assert_not_called()
                self.assertEqual(session.stop(), [])

    def test_cancel_during_capability_check_prevents_capture_allocation(self):
        session = CastSession(Mock(), {}, Mock(), silent_audio=True)
        with patch("cast_to_tv.casting.ffmpeg_executable", side_effect=lambda: session.cancel_start()), \
                patch("cast_to_tv.casting.tempfile.mkdtemp") as temporary:
            with self.assertRaises(CastingCancelled):
                session.start()
        temporary.assert_not_called()
        session.cast_device.media_controller.play_media.assert_not_called()

    def test_cancel_during_playback_buffering_does_not_report_a_receiver_error(self):
        session = CastSession(Mock(), {}, Mock())
        session.cancel_start()
        with self.assertRaises(CastingCancelled):
            session._wait_for_playback("http://pc/desktop.mpd")
        session.cast_device.media_controller.update_status.assert_not_called()

    def test_compatibility_audio_startup_cancel_releases_server_and_encoder(self):
        session = CastSession(Mock(), {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}, Mock())
        audio_entered = threading.Event()
        errors = []
        process = Mock(stdin=None, stderr=io.BytesIO())
        process.poll.return_value = None
        process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)

        def audio(_loopback):
            audio_entered.set()
            session._stop_event.wait(3)

        def start():
            try:
                session.start()
            except Exception as exc:
                errors.append(exc)

        with patch('cast_to_tv.casting.ffmpeg_executable', return_value='ffmpeg'), \
                patch('cast_to_tv.casting.default_loopback_microphone', return_value=Mock()), \
                patch('cast_to_tv.casting.local_address_for', return_value='127.0.0.1'), \
                patch('cast_to_tv.casting.subprocess.Popen', return_value=process), \
                patch.object(session, '_capture_audio', side_effect=audio):
            worker = threading.Thread(target=start)
            worker.start()
            try:
                self.assertTrue(audio_entered.wait(2))
                session.cancel_start()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], CastingCancelled)
                self.assertEqual(session.stop(), [])
                process.terminate.assert_called_once()
                session.cast_device.media_controller.play_media.assert_not_called()
                self.assertIsNone(session.server)
                self.assertIsNone(session.temp_dir)
                self.assertFalse(session._audio_thread.is_alive())
                self.assertFalse(session._stderr_thread.is_alive())
            finally:
                session.cancel_start()
                worker.join(3)
                session.stop()

    def test_compatibility_launch_cancel_does_not_queue_a_media_load_and_quits_empty_receiver(self):
        device = Mock()
        device.app_id = None
        device.media_controller.status.content_id = None
        session = CastSession(device, {}, Mock())

        def launch(**kwargs):
            session.cancel_start()
            device.app_id = "CC1AD845"
            kwargs['callback_function'](True, {})

        device.media_controller.launch.side_effect = launch
        with self.assertRaises(CastingCancelled):
            session._launch_media_receiver()
        self.assertEqual(session.stop(), [])
        device.media_controller.play_media.assert_not_called()
        device.quit_app.assert_called_once_with(timeout=10)

    def test_cancelled_launch_preserves_another_senders_media(self):
        device = Mock()
        device.app_id = "CC1AD845"
        device.media_controller.status.content_id = "https://example.com/other"
        session = CastSession(device, {}, Mock())
        session._receiver_launch_requested = True
        session._receiver_launch_succeeded = True
        session._receiver_launch_response.set()
        session.cancel_start()
        self.assertEqual(session.stop(), [])
        device.quit_app.assert_not_called()

    def test_native_cancel_during_connection_never_launches_receiver_or_helper(self):
        device = Mock()
        cancel = threading.Event()
        device.wait.side_effect = lambda timeout: cancel.set()
        with tempfile.TemporaryDirectory() as directory, \
                patch("cast_to_tv.native_probe.subprocess.Popen") as helper:
            run_probe(device, Path('helper'), Path(), 200, queue.Queue(), cancel,
                      Path(directory) / 'report.json')
        helper.assert_not_called()
        device.register_handler.assert_not_called()
        device.quit_app.assert_not_called()

    @patch('cast_to_tv.native_casting.native_helper', return_value=Path('helper'))
    @patch('cast_to_tv.native_casting.shutil.which', return_value='ffmpeg')
    def test_native_cancel_signals_startup_then_cleanup_joins_worker(self, _which, _helper):
        entered = threading.Event()
        completed = threading.Event()
        failure = Mock()
        session = NativeCastSession(Mock(), {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}, failure)

        def run(*args, **kwargs):
            entered.set()
            args[5].wait(3)

        errors = []

        def start():
            try:
                session.start()
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        with patch('cast_to_tv.native_casting.run_probe', side_effect=run):
            worker = threading.Thread(target=start)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                session.cancel_start()
                self.assertTrue(completed.wait(2))
                self.assertIsInstance(errors[0], CastingCancelled)
                self.assertEqual(session.stop(), [])
                self.assertFalse(session._worker.is_alive())
                self.assertIsNone(session._temporary)
                failure.assert_not_called()
            finally:
                session.cancel_start()
                worker.join(3)
                session.stop()


if __name__ == '__main__':
    unittest.main()
