import queue
import threading
import unittest
from unittest.mock import Mock, patch

from cast_to_tv.app import CastToTVApp
from cast_to_tv.tray import TrayIcon


class TrayBackendTests(unittest.TestCase):
    def setUp(self):
        self.factory = patch("pystray.Icon")
        self.icon = self.factory.start().return_value
        self.addCleanup(self.factory.stop)
        self.events = queue.Queue()
        self.tray = TrayIcon(self.events.put)

    def test_menu_actions_only_enqueue_from_worker_threads(self):
        worker = threading.Thread(target=self.tray._action("stop"), args=(None, None))
        worker.start()
        worker.join()
        self.assertEqual(self.events.get_nowait(), ("tray", (self.tray, "stop", "")))

    def test_readiness_is_reported_after_icon_becomes_visible(self):
        self.tray._setup(self.icon)
        self.assertTrue(self.icon.visible)
        self.assertEqual(self.events.get_nowait(), ("tray", (self.tray, "ready", "")))

    def test_exit_during_startup_prevents_hiding_and_stops_when_setup_arrives(self):
        self.assertEqual(self.tray.stop(), [])
        self.icon.reset_mock()
        self.tray._setup(self.icon)
        self.icon.stop.assert_called_once()
        self.assertTrue(self.events.empty())

    def test_setup_failure_is_reported_once_and_stops_backend(self):
        with patch.object(type(self.icon), "visible", new_callable=unittest.mock.PropertyMock, create=True) as visible:
            visible.side_effect = OSError("shell unavailable")
            self.tray._setup(self.icon)
        self.tray._fail("second failure")
        self.assertTrue(self.tray.failed)
        self.assertIn("shell unavailable", self.events.get_nowait()[1][2])
        self.assertTrue(self.events.empty())
        self.icon.stop.assert_called_once()

    def test_normal_shutdown_does_not_report_unexpected_exit(self):
        self.tray.stop()
        self.tray._run()
        self.assertTrue(self.events.empty())

    def test_menu_state_updates_only_when_changed(self):
        self.tray.set_can_stop(False)
        self.icon.update_menu.assert_not_called()
        self.tray.set_can_stop(True)
        self.tray.set_can_stop(True)
        self.icon.update_menu.assert_called_once()


class TrayAppTests(unittest.TestCase):
    def setUp(self):
        self.app = CastToTVApp.__new__(CastToTVApp)
        self.app.root = Mock()
        self.app.events = queue.Queue()
        self.app._closed = False
        self.app._closing = False
        self.app._tray = None
        self.app._tray_ready = False
        self.app._tray_startup_timer = None
        self.app._hide_pending = False
        self.app._hidden_to_tray = False
        self.app._about_dialog = None
        self.app._diagnostics_dialog = None
        self.app._display_identifiers = Mock()
        self.app.close_to_tray = Mock(get=Mock(return_value=False))

    def test_window_x_defaults_to_exit_and_enabled_option_hides(self):
        with patch.object(self.app, "close") as close, patch.object(self.app, "hide_to_tray") as hide:
            self.app._window_close()
            close.assert_called_once()
            hide.assert_not_called()
            self.app.close_to_tray.get.return_value = True
            self.app._window_close()
            hide.assert_called_once()
            self.app._closing = True
            self.app._window_close()
            self.assertEqual(close.call_count, 2)

    @patch("cast_to_tv.app.TrayIcon")
    def test_hide_waits_for_readiness_and_reuses_icon(self, factory):
        tray = factory.return_value
        tray.failed = False
        self.app.hide_to_tray()
        self.app.root.withdraw.assert_not_called()
        self.app._tray_event(tray, "ready", "")
        self.assertTrue(self.app._hidden_to_tray)
        self.app.root.withdraw.assert_called_once()
        self.app.show_window()
        self.app.hide_to_tray()
        factory.assert_called_once()
        tray.start.assert_called_once()
        self.assertEqual(self.app.root.withdraw.call_count, 2)

    @patch("cast_to_tv.app.TrayIcon")
    def test_show_cancels_pending_hide_before_tray_is_ready(self, factory):
        tray = factory.return_value
        tray.failed = False
        self.app.hide_to_tray()
        self.app.show_window()
        self.app._tray_event(tray, "ready", "")
        self.app.root.withdraw.assert_not_called()

    def test_exit_uses_real_cleanup_even_when_close_to_tray_is_enabled(self):
        self.app._tray = Mock()
        self.app.close_to_tray.get.return_value = True
        with patch.object(self.app, "close") as close:
            self.app._tray_event(self.app._tray, "exit", "")
        close.assert_called_once()
        self.app.root.deiconify.assert_called_once()

    def test_stop_is_dispatched_on_gui_event_processing_and_old_icons_are_ignored(self):
        self.app._tray = Mock()
        with patch.object(self.app, "stop_cast") as stop:
            self.app._tray_event(Mock(), "stop", "")
            stop.assert_not_called()
            self.app._tray_event(self.app._tray, "stop", "")
            stop.assert_called_once()

    @patch("cast_to_tv.app.TrayIcon", side_effect=OSError("no shell"))
    def test_failed_tray_initialization_keeps_window_accessible(self, factory):
        with patch.object(self.app, "_show_warning") as warning:
            self.app.hide_to_tray()
        self.assertFalse(self.app._hide_pending)
        self.app.root.withdraw.assert_not_called()
        self.app.root.deiconify.assert_called_once()
        warning.assert_called_once()

    def test_tray_failure_restores_hidden_window(self):
        self.app._tray = Mock()
        self.app._hidden_to_tray = True
        with patch.object(self.app, "_show_warning") as warning:
            self.app._tray_event(self.app._tray, "failed", "shell exited")
        self.assertFalse(self.app._hidden_to_tray)
        self.assertFalse(self.app._tray_ready)
        self.app.root.deiconify.assert_called_once()
        warning.assert_called_once()

    def test_hide_and_late_ready_do_not_hide_a_closing_app(self):
        self.app._tray = Mock(failed=False)
        self.app._closing = True
        self.app._hide_pending = True
        self.app.hide_to_tray()
        self.app._tray_event(self.app._tray, "ready", "")
        self.app.root.withdraw.assert_not_called()

    def test_shutdown_worker_stops_tray_before_posting_completion(self):
        self.app._tray = Mock()
        self.app._tray.stop.side_effect = lambda: (
            self.assertTrue(self.app.events.empty()) or [])
        self.app._shutdown_worker([], None)
        self.app._tray.stop.assert_called_once()
        self.assertEqual(self.app.events.get_nowait(), ("shutdown_complete", []))

    def test_failed_tray_cleanup_is_reported_for_retry(self):
        self.app._tray = Mock()
        self.app._tray.stop.return_value = ["Tray service still closing"]
        self.app._shutdown_worker([], None)
        self.assertEqual(self.app.events.get_nowait(), ("shutdown_complete", ["Tray service still closing"]))

    def test_errors_restore_hidden_window_before_displaying_error(self):
        self.app._hidden_to_tray = True
        with patch("cast_to_tv.app.messagebox.showerror") as error, self.assertLogs("cast_to_tv.app", level="ERROR"):
            error.side_effect = lambda *_args: self.assertFalse(self.app._hidden_to_tray)
            self.app._show_error("Casting failed", "capture lost")


if __name__ == "__main__":
    unittest.main()
