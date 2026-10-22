import queue
import unittest
from dataclasses import replace
from unittest.mock import Mock, call, patch

from cast_to_tv.app import CastRequest, CastToTVApp
from cast_to_tv.casting import CastingCancelled
from cast_to_tv.quality import DEFAULT_QUALITY, VideoQuality


class FakeNativeSession:
    instances = []

    def __init__(self, device, display, on_failure, diagnostics=None, include_audio=True, quality=DEFAULT_QUALITY):
        self.cast_device = device
        self.display = display
        self.on_failure = on_failure
        self.diagnostics = diagnostics
        self.include_audio = include_audio
        self.quality = quality
        self.start = Mock()
        self.stop = Mock(return_value=[])
        self.cancel_start = Mock()
        self.instances.append(self)


class FakeCompatibilitySession:
    instances = []

    def __init__(self, device, display, on_failure, diagnostics=None, silent_audio=False, quality=DEFAULT_QUALITY):
        self.cast_device = device
        self.display = display
        self.on_failure = on_failure
        self.diagnostics = diagnostics
        self.silent_audio = silent_audio
        self.quality = quality
        self.start = Mock()
        self.stop = Mock(return_value=[])
        self.cancel_start = Mock()
        self.instances.append(self)


class DisplaySwitchingTests(unittest.TestCase):
    def setUp(self):
        FakeNativeSession.instances = []
        FakeCompatibilitySession.instances = []
        for target, replacement in (
            ("NativeCastSession", FakeNativeSession), ("CastSession", FakeCompatibilitySession),
        ):
            patcher = patch(f"cast_to_tv.app.{target}", replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.thread = self.start_patch("threading.Thread")
        self.thread.return_value.is_alive.return_value = False
        self.save = self.start_patch("save_settings")
        self.error = self.start_patch("messagebox.showerror")
        self.app = CastToTVApp.__new__(CastToTVApp)
        self.app.root = Mock()
        self.app.events = queue.Queue()
        self.app.busy = False
        self.app._stopping = False
        self.app._cancelling_start = False
        self.app._closing = False
        self.app._about_dialog = None
        self.app._diagnostics_dialog = None
        self.app._last_problem = None
        self.app._tray = None
        self.app._tray_ready = False
        self.app._tray_startup_timer = None
        self.app._hide_pending = False
        self.app._hidden_to_tray = False
        self.app.close_to_tray_checkbox = Mock()
        self.app.about_button = Mock()
        self.app.diagnostics_button = Mock()
        self.app._shutdown_started = False
        self.app._closed = False
        self.app._pending_switch = None
        self.app._scan_failed = False
        self.app._saved_tv_uuid = None
        self.app._saved_display_id = "monitor-a"
        self.app._diagnostic_marker = None
        self.app._display_identifiers = Mock()
        self.app.identify_button = Mock()
        self.app._screen_awake = Mock()
        self.app.keep_screen_on = Mock(get=Mock(return_value=True))
        self.app.include_audio = Mock(get=Mock(return_value=True))
        self.app.measure_delay = Mock(get=Mock(return_value=True))
        self.app.mode_choice = Mock(get=Mock(return_value="Compatibility"))
        self.app.mode_box = Mock()
        self.app.resolution_choice = Mock(get=Mock(return_value="1080p"))
        self.app.frame_rate_choice = Mock(get=Mock(return_value="30"))
        self.app.resolution_box = Mock()
        self.app.frame_rate_box = Mock()
        self.app.measure_delay_checkbox = Mock()
        self.app.include_audio_checkbox = Mock()
        self.tv = Mock()
        self.tv.socket_client.ident = None
        self.tv.socket_client.is_alive.return_value = False
        self.other_tv = Mock()
        self.other_tv.socket_client.is_alive.return_value = False
        self.app.devices = [self.tv, self.other_tv]
        self.app.browser = None
        self.app.tv_box = Mock(current=Mock(return_value=1))
        self.app.displays = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": -1920, "top": 156, "width": 1920, "height": 1080},
            {"left": 1920, "top": -47, "width": 1024, "height": 1280},
        ]
        self.app._display_ids = ["monitor-a", "monitor-b", "monitor-c"]
        self.enumerate_displays = self.start_patch("list_displays")
        self.enumerate_displays.side_effect = lambda: [dict(display) for display in self.app.displays]
        self.index = 0
        self.app.display_box = Mock()
        self.app.display_box.current.side_effect = self.current
        for name in ("start_button", "scan_button", "stop_button", "status"):
            setattr(self.app, name, Mock())
        self.app._active_request = CastRequest(self.tv, self.app.displays[0], 0, True, False, False)
        self.old = FakeNativeSession(
            self.tv, self.app.displays[0],
            lambda text: self.app._post_session_event(self.old, "capture_error", text),
            include_audio=False,
        )
        self.app.session = self.old

    def start_patch(self, name):
        patcher = patch(f"cast_to_tv.app.{name}")
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def current(self, index=None):
        if index is not None:
            self.index = index
        return self.index

    def select(self, index):
        self.current(index)
        self.app._display_selected()

    def assert_selector_state(self, state):
        for selector in (self.app.tv_box, self.app.display_box, self.app.mode_box):
            selector.configure.assert_called_with(state=state)
        settings_state = "normal" if self.app.session is None and not self.app._closing else "disabled"
        for checkbox in (self.app.measure_delay_checkbox, self.app.include_audio_checkbox):
            checkbox.configure.assert_called_with(state=settings_state)
        quality_state = "readonly" if settings_state == "normal" else "disabled"
        for selector in (self.app.resolution_box, self.app.frame_rate_box):
            selector.configure.assert_called_with(state=quality_state)

    def test_display_switch_retains_active_quality_in_both_modes(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.app.busy = False
                self.app._active_request = replace(self.app._active_request, native=native,
                                                   quality=VideoQuality("720p", 15))
                self.index = 0
                self.app._active_request = replace(self.app._active_request, display_index=0)
                self.select(1)
                self.run_worker()
                self.assertEqual(self.app.session.quality, VideoQuality("720p", 15))
                self.run_worker()

    def test_start_captures_both_independent_choices_in_both_modes(self):
        for native in (True, False):
            for resolution in ("720p", "1080p"):
                for rate in (15, 30):
                    with self.subTest(native=native, resolution=resolution, rate=rate):
                        self.app.resolution_choice.get.return_value = resolution
                        self.app.frame_rate_choice.get.return_value = str(rate)
                        session = self.begin_startup(native)
                        self.assertEqual(session.quality, VideoQuality(resolution, rate))
                        self.assertEqual(self.app._active_request.quality, session.quality)
                        self.run_worker()
                        self.app.stop_cast()
                        self.run_worker()


    def run_worker(self):
        arguments = self.thread.call_args.kwargs
        arguments["target"](*arguments.get("args", ()))
        self.app._process_events()

    def test_switch_stops_before_restarting_and_preserves_active_tv_mode_and_audio(self):
        self.select(1)
        self.assertTrue(self.app.busy)
        self.assert_selector_state("disabled")
        self.assertEqual(len(FakeNativeSession.instances), 1)
        self.old.stop.assert_not_called()
        self.save.assert_called_once_with({"display_id": "monitor-b"})

        self.run_worker()

        self.old.stop.assert_called_once()
        self.assert_selector_state("disabled")
        replacement = self.app.session
        self.assertIsInstance(replacement, FakeNativeSession)
        self.assertIs(replacement.cast_device, self.tv)
        self.assertEqual(replacement.display, self.app.displays[1])
        self.assertFalse(replacement.include_audio)
        self.assertIsNone(replacement.diagnostics)
        replacement.start.assert_not_called()
        self.assertNotIn(call(False), self.app._screen_awake.set_enabled.call_args_list)

        self.run_worker()

        replacement.start.assert_called_once()
        self.assertFalse(self.app.busy)
        self.assert_selector_state("readonly")
        self.assertIn("Display 2", self.app.status.set.call_args.args[0])

    def test_switch_works_for_compatibility_without_using_pending_gui_mode_or_audio(self):
        self.old = FakeCompatibilitySession(self.tv, self.app.displays[0], Mock(), silent_audio=False)
        self.app.session = self.old
        self.app._active_request = replace(self.app._active_request, native=False, include_audio=True)
        self.app.mode_choice.get.return_value = "Low delay (experimental)"
        self.app.include_audio.get.return_value = False
        self.select(2)
        self.run_worker()
        replacement = self.app.session
        self.assertIsInstance(replacement, FakeCompatibilitySession)
        self.assertFalse(replacement.silent_audio)
        self.assertIs(replacement.cast_device, self.tv)
        self.run_worker()
        self.assertIn("Display 3", self.app.status.set.call_args.args[0])

    def test_selecting_same_display_does_not_restart(self):
        self.select(0)
        self.thread.assert_not_called()
        self.old.stop.assert_not_called()
        self.assertIs(self.app.session, self.old)

    def test_repeated_selection_during_switch_cannot_queue_a_second_restart(self):
        self.select(1)
        self.select(2)
        self.assertEqual(self.current(), 1)
        self.thread.assert_called_once()
        self.save.assert_called_once_with({"display_id": "monitor-b"})
        self.assertEqual(self.app._pending_switch.display_index, 1)

    def test_stop_cancels_restart_while_cleanup_is_in_progress(self):
        self.select(1)
        self.app.stop_cast()
        self.assertIsNone(self.app._pending_switch)
        self.thread.assert_called_once()
        self.run_worker()
        self.assertIsNone(self.app.session)
        self.assertFalse(self.app.busy)
        self.assertEqual(len(FakeNativeSession.instances), 1)
        self.app._screen_awake.set_enabled.assert_called_with(False)
        self.assert_selector_state("readonly")

    def test_cleanup_failure_blocks_restart_and_allows_stop_to_retry(self):
        self.old.stop.return_value = ["Helper is still finishing cleanup"]
        self.select(1)
        self.run_worker()
        self.assertIs(self.app.session, self.old)
        self.assertEqual(len(FakeNativeSession.instances), 1)
        self.assertFalse(self.app.busy)
        self.error.assert_called_once()
        self.app.stop_button.configure.assert_called_with(state="normal")
        self.assert_selector_state("disabled")
        self.assertEqual(self.current(), 0)
        self.old.stop.return_value = []
        self.app.stop_cast()
        self.run_worker()
        self.assertIsNone(self.app.session)
        self.assertEqual(self.old.stop.call_count, 2)
        self.assert_selector_state("readonly")

    def test_cleanup_exception_is_reported_without_starting_replacement(self):
        self.old.stop.side_effect = RuntimeError("could not terminate helper")
        self.select(1)
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.run_worker()
        self.assertIs(self.app.session, self.old)
        self.assertEqual(len(FakeNativeSession.instances), 1)
        self.error.assert_called_once()

    def test_replacement_start_failure_releases_power_and_restores_idle_controls(self):
        self.select(1)
        self.run_worker()
        replacement = self.app.session
        replacement.start.side_effect = RuntimeError("receiver refused the new stream")
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.run_worker()
        replacement.stop.assert_called_once()
        self.assertIsNone(self.app.session)
        self.assertIsNone(self.app._pending_switch)
        self.assertFalse(self.app.busy)
        self.app._screen_awake.set_enabled.assert_called_with(False)
        self.assert_selector_state("readonly")
        self.error.assert_called_once()

    def test_late_events_from_old_session_cannot_stop_replacement(self):
        self.select(1)
        self.old.on_failure("old capture is closing")
        self.app._process_events()
        self.thread.assert_called_once()
        self.run_worker()
        replacement = self.app.session
        self.run_worker()
        self.app._post_session_event(self.old, "capture_error", "late error")
        self.app._post_session_event(self.old, "error", "late cleanup result")
        self.app._post_session_event(self.old, "stopped", [])
        self.app._process_events()
        self.assertIs(self.app.session, replacement)
        replacement.stop.assert_not_called()
        self.assertFalse(self.app.busy)
        self.error.assert_not_called()

    def test_replacement_start_failure_with_cleanup_error_keeps_session_for_retry(self):
        self.select(1)
        self.run_worker()
        replacement = self.app.session
        replacement.start.side_effect = RuntimeError("receiver unavailable")
        replacement.stop.return_value = ["Helper is still finishing cleanup"]
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.run_worker()
        self.assertIs(self.app.session, replacement)
        self.app.stop_button.configure.assert_called_with(state="normal")
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.assertFalse(self.app.busy)
        replacement.stop.return_value = []
        self.app.stop_cast()
        self.run_worker()
        self.assertIsNone(self.app.session)

    def test_session_preparation_failure_after_old_cleanup_restores_idle_controls(self):
        self.select(1)
        with patch("cast_to_tv.app.NativeCastSession", side_effect=RuntimeError("could not prepare stream")):
            with self.assertLogs("cast_to_tv.app", level="ERROR"):
                self.run_worker()
        self.assertIsNone(self.app.session)
        self.assertIsNone(self.app._active_request)
        self.assertFalse(self.app.busy)
        self.app._screen_awake.set_enabled.assert_called_with(False)
        self.assert_selector_state("readonly")
        self.error.assert_called_once()

    def test_failure_in_replacement_is_still_reported_and_cleaned_up(self):
        self.select(1)
        self.run_worker()
        replacement = self.app.session
        self.run_worker()
        replacement.on_failure("capture device disappeared")
        self.app._process_events()
        self.run_worker()
        replacement.stop.assert_called_once()
        self.assertIsNone(self.app.session)
        self.error.assert_called_once()

    def test_started_event_after_capture_failure_cannot_reenable_switching_during_cleanup(self):
        self.old.on_failure("capture failed just as startup completed")
        self.app._post_session_event(self.old, "started", None)
        self.app._process_events()
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app._stopping)
        self.assert_selector_state("disabled")
        self.app.stop_button.configure.assert_called_with(state="disabled")
        self.thread.assert_called_once()

    def test_delay_clock_moves_to_selected_display_without_recording_in_both_modes(self):
        marker_factory = self.start_patch("LatencyMarker")
        for native in (True, False):
            with self.subTest(native=native):
                marker_factory.reset_mock()
                old_marker = Mock()
                self.app._diagnostic_marker = old_marker
                self.app._active_request = replace(self.app._active_request,
                                                   native=native, measure_delay=True)
                index = 1 if native else 0
                self.select(index)
                self.run_worker()
                old_marker.close.assert_called_once()
                marker_factory.assert_called_once_with(self.app.root, self.app.displays[index])
                self.assertIsNone(self.app.session.diagnostics)
                self.assertIs(self.app._diagnostic_marker, marker_factory.return_value)
                self.run_worker()
        self.app.stop_cast()
        self.run_worker()
        self.app.status.set.assert_called_with("Mirroring stopped.")

    def test_cleanup_closes_control_connection_after_stopping_stream(self):
        calls = []
        self.old.stop.side_effect = lambda: calls.append("stream stopped") or []
        self.tv.socket_client.is_alive.side_effect = [True, False]
        self.tv.disconnect.side_effect = lambda timeout: calls.append(("disconnected", timeout))
        self.assertEqual(self.app._cleanup_session(self.old), [])
        self.assertEqual(calls, ["stream stopped", ("disconnected", 5)])

    def test_cleanup_does_not_disconnect_while_stream_cleanup_is_unresolved(self):
        self.old.stop.return_value = ["helper still running"]
        self.assertEqual(self.app._cleanup_session(self.old), ["helper still running"])
        self.tv.disconnect.assert_not_called()

    def test_control_connection_timeout_blocks_switch_until_stop_retry(self):
        self.tv.socket_client.is_alive.return_value = True
        self.select(1)
        self.run_worker()
        self.tv.disconnect.assert_called_once_with(timeout=5)
        self.assertIs(self.app.session, self.old)
        self.assertEqual(len(FakeNativeSession.instances), 1)
        self.error.assert_called_once()
        self.tv.socket_client.is_alive.return_value = False
        self.app.stop_cast()
        self.run_worker()
        self.assertIsNone(self.app.session)

    def test_control_connection_exception_is_reported(self):
        self.tv.socket_client.is_alive.return_value = True
        self.tv.disconnect.side_effect = OSError("disconnect failed")
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            errors = self.app._cleanup_session(self.old)
        self.assertIn("disconnect failed", errors[0])

    def test_switch_recreates_closed_connection_and_updates_discovered_device(self):
        self.tv.socket_client.ident = 123
        fresh = Mock(cast_info=self.tv.cast_info)
        fresh.socket_client.ident = None
        fresh.socket_client.is_alive.return_value = False
        browser = Mock()
        self.app.browser = browser
        with patch("cast_to_tv.app.pychromecast.get_chromecast_from_cast_info", return_value=fresh) as factory:
            self.select(1)
            self.run_worker()
        factory.assert_called_once_with(self.tv.cast_info, browser.zc)
        self.assertIs(self.app.session.cast_device, fresh)
        self.assertIs(self.app.devices[0], fresh)
        self.assertIs(self.app._active_request.device, fresh)
        self.run_worker()
        self.assertFalse(self.app.busy)

    def test_manual_start_after_stop_recreates_connection_without_rescanning(self):
        self.tv.socket_client.ident = 123
        self.app.stop_cast()
        self.run_worker()
        fresh = Mock(cast_info=self.tv.cast_info)
        fresh.socket_client.ident = None
        self.app.tv_box.current.return_value = 0
        self.app.mode_choice.get.return_value = "Low delay (experimental)"
        self.app.include_audio.get.return_value = False
        self.app.measure_delay.get.return_value = False
        with patch("cast_to_tv.app.pychromecast.get_chromecast_from_cast_info", return_value=fresh) as factory:
            self.app.start_cast()
        factory.assert_called_once_with(self.tv.cast_info, None)
        self.assertIs(self.app.session.cast_device, fresh)

    def test_reconnection_preparation_failure_leaves_app_usable(self):
        self.tv.socket_client.ident = 123
        self.select(1)
        with patch("cast_to_tv.app.pychromecast.get_chromecast_from_cast_info", side_effect=RuntimeError("device unavailable")):
            with self.assertLogs("cast_to_tv.app", level="ERROR"):
                self.run_worker()
        self.assertIsNone(self.app.session)
        self.assertFalse(self.app.busy)
        self.assert_selector_state("readonly")
        self.error.assert_called_once()

    def begin_startup(self, native=True):
        self.app.session = None
        self.app.tv_box.current.return_value = 0
        self.app.measure_delay.get.return_value = False
        self.app.mode_choice.get.return_value = "Low delay (experimental)" if native else "Compatibility"
        self.app.start_cast()
        self.assertTrue(self.app.busy)
        self.app.stop_button.configure.assert_called_with(state="normal")
        return self.app.session

    def test_stop_during_startup_signals_only_then_worker_cleans_up_in_both_modes(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.thread.reset_mock()
                session = self.begin_startup(native)
                session.start.side_effect = CastingCancelled("Connection cancelled.")
                self.app.stop_cast()
                session.cancel_start.assert_called_once()
                session.stop.assert_not_called()
                self.thread.assert_called_once()
                self.assertTrue(self.app.busy)
                self.assert_selector_state("disabled")
                self.app._screen_awake.set_enabled.assert_called_with(False)
                self.app.stop_cast()
                session.cancel_start.assert_called_once()
                self.run_worker()
                session.stop.assert_called_once()
                self.assertFalse(self.app.busy)
                self.assertIsNone(self.app.session)
                self.assert_selector_state("readonly")
                self.assertEqual(self.app.status.set.call_args.args[0], "Connection cancelled.")
                self.error.assert_not_called()

    def test_stop_after_worker_success_before_gui_event_still_cleans_up_once(self):
        session = self.begin_startup()
        self.app._start_worker(session)
        session.stop.assert_not_called()
        self.app.stop_cast()
        self.app._process_events()
        self.assertTrue(self.app.busy)
        self.assertIs(self.app.session, session)
        self.run_worker()
        session.stop.assert_called_once()
        self.assertIsNone(self.app.session)
        self.assertEqual(self.app.status.set.call_args.args[0], "Connection cancelled.")
        self.error.assert_not_called()

    def test_cancelling_display_switch_reconnection_does_not_start_another_stream(self):
        self.select(1)
        self.run_worker()
        replacement = self.app.session
        replacement.start.side_effect = CastingCancelled("Connection cancelled.")
        self.app.stop_cast()
        self.run_worker()
        replacement.cancel_start.assert_called_once()
        replacement.stop.assert_called_once()
        self.assertIsNone(self.app.session)
        self.assertIsNone(self.app._pending_switch)
        self.assertEqual(self.current(), 1)
        self.error.assert_not_called()

    def test_cancelled_start_cleanup_failure_keeps_stop_retry_and_blocks_new_cast(self):
        session = self.begin_startup()
        session.start.side_effect = CastingCancelled("Connection cancelled.")
        session.stop.return_value = ["cleanup still pending"]
        self.app.stop_cast()
        self.run_worker()
        self.assertIs(self.app.session, session)
        self.assert_selector_state("disabled")
        self.app.stop_button.configure.assert_called_with(state="normal")
        session.stop.return_value = []
        self.app.stop_cast()
        self.run_worker()
        self.assertIsNone(self.app.session)
        self.assert_selector_state("readonly")

    def test_close_during_startup_cancels_then_exits_after_background_cleanup_in_both_modes(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.app._closed = self.app._closing = False
                self.app.devices = [self.tv, self.other_tv]
                self.app.root.destroy.reset_mock()
                session = self.begin_startup(native)
                session.start.side_effect = CastingCancelled("Connection cancelled.")
                self.app.close()
                session.cancel_start.assert_called_once()
                session.stop.assert_not_called()
                self.app.root.destroy.assert_not_called()
                self.app.start_cast()
                self.assertIs(self.app.session, session)
                self.run_worker()
                session.stop.assert_called_once()
                self.assertTrue(self.app._shutdown_started)
                self.app.root.destroy.assert_not_called()
                self.app.root.after.reset_mock()
                self.run_worker()
                self.app.root.destroy.assert_called_once()
                self.app.root.after.assert_not_called()
                self.assertTrue(self.app._closed)
                self.error.assert_not_called()

    def test_repeated_close_during_cancellation_keeps_one_startup_owner(self):
        session = self.begin_startup()
        self.thread.reset_mock()
        self.app.close()
        self.app.close()
        self.app.close()
        session.cancel_start.assert_called_once()
        self.thread.assert_not_called()
        session.stop.assert_not_called()
        self.app.root.destroy.assert_not_called()

    def test_close_after_startup_success_was_queued_runs_cleanup_before_exit(self):
        session = self.begin_startup()
        self.app._start_worker(session)
        self.app.close()
        self.app._process_events()
        session.stop.assert_not_called()
        self.run_worker()
        self.assertTrue(self.app._shutdown_started)
        self.run_worker()
        session.stop.assert_called_once()
        self.app.root.destroy.assert_called_once()

    def test_close_during_display_switch_cleanup_cancels_restart_before_exit(self):
        self.select(1)
        self.app.close()
        self.assertIsNone(self.app._pending_switch)
        self.run_worker()
        self.assertEqual(len(FakeNativeSession.instances), 1)
        self.run_worker()
        self.old.stop.assert_called_once()
        self.app.root.destroy.assert_called_once()

    def test_close_cleanup_failure_keeps_window_open_and_can_retry(self):
        session = self.begin_startup()
        session.start.side_effect = CastingCancelled("Connection cancelled.")
        session.stop.return_value = ["cleanup still pending"]
        self.app.close()
        self.run_worker()
        self.assertIs(self.app.session, session)
        self.assert_selector_state("disabled")
        self.app.root.destroy.assert_not_called()
        self.app.start_button.configure.assert_called_with(state="disabled")
        session.stop.return_value = []
        self.app.close()
        self.run_worker()
        self.run_worker()
        self.assertEqual(session.stop.call_count, 2)
        self.app.root.destroy.assert_called_once()

    def test_close_retains_discovery_until_all_connections_finish_and_can_retry(self):
        self.app.session = None
        self.other_tv.socket_client.is_alive.return_value = False
        self.tv.socket_client.is_alive.return_value = True
        self.tv.disconnect.side_effect = OSError("connection still closing")
        browser = self.app.browser = Mock()
        self.app.close()
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.run_worker()
        self.app.root.destroy.assert_not_called()
        browser.stop_discovery.assert_not_called()
        self.assertFalse(self.app.busy)
        self.tv.socket_client.is_alive.return_value = False
        self.app.close()
        self.run_worker()
        browser.stop_discovery.assert_called_once()
        self.app.root.destroy.assert_called_once()

    def test_close_during_discovery_waits_for_handoff_without_error_dialog(self):
        self.app.session = None
        self.app.busy = True
        self.app.close()
        self.thread.assert_not_called()
        self.app.events.put(("scan_error", "network unavailable"))
        self.app.events.put(("scan_finished", None))
        self.app._process_events()
        self.assertTrue(self.app._shutdown_started)
        self.other_tv.socket_client.is_alive.return_value = False
        self.run_worker()
        self.error.assert_not_called()
        self.app.root.destroy.assert_called_once()

    def test_shutdown_event_waits_for_worker_return_before_destroying_tk(self):
        self.app.session = None
        self.app.close()
        self.thread.return_value.is_alive.return_value = True
        self.run_worker()
        self.app.root.destroy.assert_not_called()
        self.assertTrue(self.app._shutdown_started)
        deferred = next(call.args[1] for call in self.app.root.after.call_args_list if call.args[0] == 10)
        self.thread.return_value.is_alive.return_value = False
        deferred()
        self.app._process_events()
        self.app.root.destroy.assert_called_once()

    def test_close_after_stop_was_requested_keeps_existing_cleanup_owner(self):
        session = self.begin_startup()
        session.start.side_effect = CastingCancelled("Connection cancelled.")
        self.app.stop_cast()
        self.app.close()
        session.cancel_start.assert_called_once()
        self.run_worker()
        self.run_worker()
        session.stop.assert_called_once()
        self.app.root.destroy.assert_called_once()

    def test_discovery_shutdown_failure_leaves_window_open_for_close_retry(self):
        self.app.session = None
        browser = self.app.browser = Mock()
        browser.stop_discovery.side_effect = OSError("discovery still closing")
        self.app.close()
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.run_worker()
        self.app.root.destroy.assert_not_called()
        self.assertIs(self.app.browser, browser)
        browser.stop_discovery.side_effect = None
        self.app.close()
        self.run_worker()
        self.app.root.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
