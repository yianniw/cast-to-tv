import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from types import SimpleNamespace
from uuid import UUID

from cast_to_tv.app import CastToTVApp


class DiscoveryLifetimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = CastToTVApp.__new__(CastToTVApp)
        self.app.events = queue.Queue()
        self.app.devices = []
        self.app.browser = None
        self.app.session = None
        self.app._active_request = None
        self.app._pending_switch = None
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
        self.app.busy = False
        self.app._scan_failed = False
        self.app._saved_tv_uuid = None
        self.app._saved_display_id = None
        self.app.root = Mock()
        self.app.tv_box = MagicMock()
        self.app.tv_box.current.return_value = -1
        self.app.tv_choice = Mock()
        self.app.display_box = Mock(current=Mock(return_value=-1))
        self.app.mode_box = Mock()
        self.app.resolution_box = Mock()
        self.app.frame_rate_box = Mock()
        self.app.measure_delay_checkbox = Mock()
        self.app.include_audio_checkbox = Mock()
        self.app.identify_button = Mock()
        self.app.stop_button = Mock()
        self.app.displays = []
        self.app.start_button = Mock()
        self.app.status = Mock()
        self.app.scan_button = Mock()
        self.app._screen_awake = Mock()
        self.app._display_identifiers = Mock()
        self.app.keep_screen_on = Mock(get=Mock(return_value=False))

    @patch("cast_to_tv.app.discover_cast_devices")
    def test_discovery_stays_available_for_a_later_connection(self, discover: Mock) -> None:
        browser = Mock()
        device = Mock()
        # Model a device that resolves its address only when wait() is called.
        device.wait.side_effect = lambda: self.assertFalse(browser.stop_discovery.called)
        discover.return_value = ([device], browser)

        self.app._scan_worker()
        self.app._process_events()
        self.app.devices[0].wait()

        self.assertIs(self.app.browser, browser)
        browser.stop_discovery.assert_not_called()

    def test_rescan_disconnects_old_devices_before_closing_their_browser(self) -> None:
        calls = []
        old_device = Mock()
        old_device.disconnect.side_effect = lambda: calls.append("disconnect")
        old_browser = Mock()
        old_browser.stop_discovery.side_effect = lambda: calls.append("close browser")
        new_browser = Mock()
        self.app.devices = [old_device]
        self.app.browser = old_browser
        self.app.events.put(("devices", ([], new_browser)))

        self.app._process_events()

        self.assertEqual(calls, ["disconnect", "close browser"])
        self.assertIs(self.app.browser, new_browser)
        new_browser.stop_discovery.assert_not_called()

    @patch("cast_to_tv.app.threading.Thread")
    def test_exit_releases_connections_and_discovery(self, thread: Mock) -> None:
        thread.return_value.is_alive.return_value = False
        device = Mock()
        device.socket_client.is_alive.side_effect = [True, False]
        browser = Mock()
        self.app.devices = [device]
        self.app.browser = browser

        self.app.close()
        self.app.root.destroy.assert_not_called()
        work = thread.call_args.kwargs
        work['target'](*work['args'])
        self.app._process_events()

        device.disconnect.assert_called_once_with(timeout=5)
        browser.stop_discovery.assert_called_once()
        self.assertIsNone(self.app.browser)
        self.app.root.destroy.assert_called_once()


class ScreenOnPreferenceTests(unittest.TestCase):
    def test_new_app_instances_restore_both_saved_choices(self) -> None:
        def variable(*, value=""):
            return Mock(get=Mock(return_value=value))

        with tempfile.TemporaryDirectory() as directory, \
                patch("cast_to_tv.paths.application_directory", return_value=Path(directory)), \
                patch("cast_to_tv.app.tk.BooleanVar", side_effect=variable), \
                patch("cast_to_tv.app.tk.StringVar", side_effect=variable), \
                patch.multiple("cast_to_tv.app.ttk", Frame=MagicMock(), Label=MagicMock(),
                               Combobox=MagicMock(), Button=MagicMock(), Checkbutton=MagicMock()), \
                patch("cast_to_tv.app.list_displays", return_value=[]), \
                patch.object(CastToTVApp, "scan_devices"):
            for previous, selected in (
                ((False, True, False), (True, False, True)),
                ((True, False, True), (False, True, False)),
                ((False, True, False), (False, True, False)),
            ):
                app = CastToTVApp(Mock())
                for name, before, after in zip(
                    ("keep_screen_on", "include_audio", "measure_delay"), previous, selected,
                ):
                    variable = getattr(app, name)
                    self.assertIs(variable.get(), before)
                    variable.get.return_value = after
                    if name == "keep_screen_on":
                        app._keep_screen_on_changed()
                    else:
                        app._save_preference(name, after)
                app.close()

    def setUp(self) -> None:
        self.app = CastToTVApp.__new__(CastToTVApp)
        self.app.events = queue.Queue()
        self.app.root = Mock()
        self.app.devices = [Mock()]
        self.app.browser = None
        self.app.displays = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
        self.app.session = None
        self.app._active_request = None
        self.app._pending_switch = None
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
        self.app.busy = False
        self.app._scan_failed = False
        self.app._saved_tv_uuid = None
        self.app._saved_display_id = None
        self.app.tv_box = Mock(current=Mock(return_value=0))
        self.app.display_box = Mock(current=Mock(return_value=0))
        self.app.start_button = Mock()
        self.app.scan_button = Mock()
        self.app.stop_button = Mock()
        self.app.identify_button = Mock()
        self.app.status = Mock()
        self.app._screen_awake = Mock()
        self.app.keep_screen_on = Mock(get=Mock(return_value=True))
        self.app.measure_delay = Mock(get=Mock(return_value=False))
        self.app.include_audio = Mock(get=Mock(return_value=True))
        self.app.mode_choice = Mock(get=Mock(return_value="Compatibility"))
        self.app.resolution_choice = Mock(get=Mock(return_value="1080p"))
        self.app.frame_rate_choice = Mock(get=Mock(return_value="30"))
        self.app.mode_box = Mock()
        self.app.resolution_box = Mock()
        self.app.frame_rate_box = Mock()
        self.app.measure_delay_checkbox = Mock()
        self.app.include_audio_checkbox = Mock()

    @patch("cast_to_tv.app.save_settings")
    def test_enabling_while_idle_only_saves_the_preference(self, save: Mock) -> None:
        self.app._keep_screen_on_changed()

        save.assert_called_once_with({"keep_screen_on": True})
        self.app._screen_awake.set_enabled.assert_called_once_with(False)

    @patch("cast_to_tv.app.save_settings")
    def test_toggle_applies_immediately_to_an_active_cast(self, save: Mock) -> None:
        self.app.session = Mock()
        for enabled in (True, False):
            self.app.keep_screen_on.get.return_value = enabled
            self.app._keep_screen_on_changed()
            self.app._screen_awake.set_enabled.assert_called_with(enabled)
            save.assert_called_with({"keep_screen_on": enabled})

    @patch("cast_to_tv.app.save_settings")
    def test_other_checkboxes_save_without_changing_active_cast(self, save: Mock) -> None:
        self.app.session = Mock()
        for name in ("include_audio", "measure_delay"):
            for enabled in (False, True):
                self.app._save_preference(name, enabled)
                save.assert_called_with({name: enabled})
        self.assertEqual(self.app.session.mock_calls, [])
        self.app._screen_awake.set_enabled.assert_not_called()

    @patch("cast_to_tv.app.threading.Thread")
    def test_both_modes_request_screen_on_before_starting_the_worker(self, thread: Mock) -> None:
        for mode, target in (("Compatibility", "CastSession"),
                             ("Low delay (experimental)", "NativeCastSession")):
            with self.subTest(mode=mode), patch(f"cast_to_tv.app.{target}"), \
                    patch("cast_to_tv.app.list_displays", return_value=self.app.displays):
                self.app.busy = False
                self.app.session = None
                self.app.mode_choice.get.return_value = mode
                thread.return_value.start.side_effect = lambda: (
                    self.app._screen_awake.set_enabled.assert_called_with(True))
                self.app.start_cast()
                thread.return_value.start.assert_called()

    def test_disabled_preference_does_not_request_screen_on(self) -> None:
        self.app.keep_screen_on.get.return_value = False
        self.app._update_screen_awake(True)
        self.app._screen_awake.set_enabled.assert_called_once_with(False)

    @patch("cast_to_tv.app.messagebox.showerror")
    def test_stopping_or_a_startup_error_releases_the_request(self, showerror: Mock) -> None:
        for event, value in (("stopped", []), ("error", "connection failed")):
            with self.subTest(event=event):
                self.app.session = Mock(diagnostics=None)
                self.app._screen_awake.reset_mock()
                self.app.events.put((event, value))
                self.app._process_events()
                self.app._screen_awake.set_enabled.assert_called_once_with(False)
                self.assertIsNone(self.app.session)

    @patch("cast_to_tv.app.threading.Thread")
    def test_capture_failure_releases_before_worker_cleanup(self, thread: Mock) -> None:
        self.app.session = Mock()
        thread.return_value.start.side_effect = lambda: (
            self.app._screen_awake.set_enabled.assert_called_once_with(False))
        self.app.events.put(("capture_error", "capture failed"))

        self.app._process_events()

        thread.return_value.start.assert_called_once()

    @patch("cast_to_tv.app.threading.Thread")
    def test_closing_releases_before_session_cleanup(self, thread: Mock) -> None:
        thread.return_value.is_alive.return_value = False
        self.app._display_identifiers = Mock()
        self.app.session = Mock()
        self.app.session.cast_device.socket_client.is_alive.return_value = False
        self.app.devices[0].socket_client.is_alive.return_value = False
        self.app.session.stop.return_value = []
        self.app.session.stop.side_effect = lambda: (
            self.app._screen_awake.set_enabled.assert_called_once_with(False)) or []

        self.app.close()
        self.app.session.stop.assert_not_called()
        for _ in range(2):
            work = thread.call_args.kwargs
            work['target'](*work['args'])
            self.app._process_events()

        self.app.root.destroy.assert_called_once()
        self.app._display_identifiers.close.assert_called_once()

    @patch("cast_to_tv.app.messagebox.showwarning")
    @patch("cast_to_tv.app.save_settings", side_effect=OSError("disk full"))
    def test_save_failure_warns_but_still_applies_to_current_cast(self, save: Mock, warning: Mock) -> None:
        self.app.session = Mock()
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.app._keep_screen_on_changed()
        self.app._screen_awake.set_enabled.assert_called_once_with(True)
        warning.assert_called_once()

    @patch("cast_to_tv.app.messagebox.showwarning")
    def test_power_request_failure_is_reported(self, warning: Mock) -> None:
        self.app._screen_awake.set_enabled.side_effect = OSError("request rejected")
        with self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.app._update_screen_awake(True)
        warning.assert_called_once()


class SavedSelectionTests(unittest.TestCase):
    TV_A = "11111111-2222-4333-8444-555555555555"
    TV_B = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    def setUp(self) -> None:
        self.app = CastToTVApp.__new__(CastToTVApp)
        self.app.root = Mock()
        self.app.events = queue.Queue()
        self.app.devices = []
        self.app.browser = None
        self.app.session = None
        self.app._active_request = None
        self.app._pending_switch = None
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
        self.app.busy = False
        self.app._scan_failed = False
        self.app._saved_tv_uuid = None
        self.app._saved_display_id = None
        self.app._display_ids = ["monitor-a", "monitor-b"]
        self.app.displays = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        self.tv_index = -1
        self.display_index = -1
        self.app.tv_box = MagicMock()
        self.app.tv_box.current.side_effect = self.tv_current
        self.app.tv_choice = Mock()
        self.app.tv_choice.set.side_effect = lambda _: self.tv_current(-1)
        self.app.display_box = Mock()
        self.app.display_box.current.side_effect = self.display_current
        self.app.start_button = Mock()
        self.app.scan_button = Mock()
        self.app.status = Mock()
        self.save_patch = patch("cast_to_tv.app.save_settings")
        self.save = self.save_patch.start()
        self.addCleanup(self.save_patch.stop)

    def tv_current(self, index=None):
        if index is not None:
            self.tv_index = index
        return self.tv_index

    def display_current(self, index=None):
        if index is not None:
            self.display_index = index
        return self.display_index

    def device(self, identity, name="Living room", host="192.168.1.10"):
        return Mock(cast_info=SimpleNamespace(uuid=UUID(identity), friendly_name=name, host=host))

    def scan(self, devices):
        self.app.events.put(("devices", (devices, Mock())))
        self.app.events.put(("scan_finished", None))
        self.app._process_events()

    def test_saved_tv_restores_after_discovery_order_name_and_address_change(self) -> None:
        self.app._saved_tv_uuid = self.TV_A
        self.scan([self.device(self.TV_B), self.device(self.TV_A, "New TV name", "192.168.1.20")])
        self.assertEqual(self.app.tv_box.current(), 1)
        self.scan([self.device(self.TV_A), self.device(self.TV_B)])
        self.assertEqual(self.app.tv_box.current(), 0)
        self.save.assert_not_called()

    def test_missing_saved_tv_stays_unselected_and_restores_when_it_returns(self) -> None:
        self.app._saved_tv_uuid = self.TV_A
        self.app._saved_display_id = "monitor-a"
        self.display_current(0)
        self.scan([self.device(self.TV_B)])
        self.assertEqual(self.app.tv_box.current(), -1)
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.assertIn("Saved TV is unavailable", self.app.status.set.call_args.args[0])
        self.assertEqual(self.app._saved_tv_uuid, self.TV_A)
        self.scan([self.device(self.TV_B), self.device(self.TV_A)])
        self.assertEqual(self.app.tv_box.current(), 1)
        self.app.start_button.configure.assert_called_with(state="normal")
        self.save.assert_not_called()

    def test_first_run_requires_explicit_selection_and_duplicate_names_are_distinct(self) -> None:
        self.scan([self.device(self.TV_A), self.device(self.TV_B, host="192.168.1.20")])
        self.assertEqual(self.app.tv_box.current(), -1)
        names = self.app.tv_box.__setitem__.call_args.args[1]
        self.assertEqual(len(set(names)), 2)
        self.app.start_button.configure.assert_called_with(state="disabled")

    def test_explicit_choices_save_immediately_and_enable_start_only_when_both_are_valid(self) -> None:
        self.scan([self.device(self.TV_A), self.device(self.TV_B)])
        self.tv_current(1)
        self.app._tv_selected()
        self.save.assert_called_with({"tv_uuid": self.TV_B})
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.display_current(1)
        self.app._display_selected()
        self.save.assert_called_with({"display_id": "monitor-b"})
        self.app.start_button.configure.assert_called_with(state="normal")

    def test_missing_display_has_clear_status_and_requires_replacement(self) -> None:
        self.app._saved_tv_uuid = self.TV_A
        self.app._saved_display_id = "disconnected-monitor"
        self.scan([self.device(self.TV_A)])
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.assertIn("Saved display is unavailable", self.app.status.set.call_args.args[0])
        self.display_current(0)
        self.app._display_selected()
        self.save.assert_called_with({"display_id": "monitor-a"})
        self.app.start_button.configure.assert_called_with(state="normal")

    def test_selecting_tv_during_a_cast_saves_next_selection_without_restarting(self) -> None:
        self.app.devices = [self.device(self.TV_A)]
        self.app.session = Mock()
        self.tv_current(0)
        self.app._tv_selected()
        self.assertEqual(self.app.session.mock_calls, [])
        self.app.start_button.configure.assert_called_with(state="disabled")
        self.app.status.set.assert_not_called()

    @patch("cast_to_tv.app.messagebox.showwarning")
    def test_unidentifiable_display_warns_and_clears_old_saved_display(self, warning: Mock) -> None:
        self.app._saved_display_id = "old-monitor"
        self.app._display_ids = [None, "monitor-b"]
        self.display_current(0)
        self.app._display_selected()
        self.assertIsNone(self.app._saved_display_id)
        self.save.assert_called_with({"display_id": None})
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
