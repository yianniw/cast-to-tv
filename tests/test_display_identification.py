import unittest
from unittest.mock import Mock, patch

from cast_to_tv.app import CastToTVApp
from cast_to_tv.display_identification import DisplayIdentifiers


class DisplayIdentificationTests(unittest.TestCase):
    def setUp(self):
        self.root = Mock()
        self.identifiers = DisplayIdentifiers(self.root)
        self.displays = [
            {"left": -1920, "top": 156, "width": 1920, "height": 1080},
            {"left": 1920, "top": -47, "width": 1024, "height": 1280},
        ]
        for name in ("tk.Toplevel", "tk.Label", "_show_on_display"):
            patcher = patch(f"cast_to_tv.display_identification.{name}")
            setattr(self, name.split(".")[-1], patcher.start())
            self.addCleanup(patcher.stop)
        self.windows = []

        def make_window(root):
            window = Mock()
            self.windows.append(window)
            return window

        self.Toplevel.side_effect = make_window

    def test_repeating_identify_replaces_badges_and_restarts_expiration(self):
        self.identifiers.show(self.displays)
        first = list(self.identifiers._windows)
        timer = self.root.after.return_value
        self.root.after.assert_called_with(3000, self.identifiers.close)
        self.identifiers.show(self.displays)
        for window in first:
            window.destroy.assert_called_once()
        self.root.after_cancel.assert_called_once_with(timer)
        self.assertEqual(len(self.identifiers._windows), 2)
        self.assertFalse(any(window in first for window in self.identifiers._windows))

    def test_clicking_any_badge_dismisses_every_badge_and_cancels_expiration(self):
        self.identifiers.show(self.displays)
        windows = list(self.identifiers._windows)
        click = windows[1].bind.call_args.args[1]
        click(Mock())
        self.root.after_cancel.assert_called_once()
        for window in windows:
            window.destroy.assert_called_once()
        self.assertEqual(self.identifiers._windows, [])
        self.identifiers.close()
        for window in windows:
            window.destroy.assert_called_once()

    def test_placement_failure_removes_partial_badges_without_scheduling_expiration(self):
        self._show_on_display.side_effect = [None, OSError("display unavailable")]
        with self.assertRaisesRegex(OSError, "display unavailable"):
            self.identifiers.show(self.displays)
        self.assertEqual(len(self.windows), 2)
        for window in self.windows:
            window.destroy.assert_called_once()
        self.assertEqual(self.identifiers._windows, [])
        self.root.after.assert_not_called()

    def test_identify_does_not_change_active_cast_or_save_preferences(self):
        app = CastToTVApp.__new__(CastToTVApp)
        app.displays = self.displays
        app._display_identifiers = self.identifiers
        session = app.session = Mock()
        app.busy = True
        with patch("cast_to_tv.app.save_settings") as save:
            app.identify_displays()
        self.assertIs(app.session, session)
        self.assertTrue(app.busy)
        session.assert_not_called()
        self.assertEqual(session.mock_calls, [])
        save.assert_not_called()
        self.assertEqual(len(self.identifiers._windows), 2)

    def test_identification_error_warns_without_changing_casting_status(self):
        app = CastToTVApp.__new__(CastToTVApp)
        app.displays = self.displays
        app._display_identifiers = Mock()
        app.status = Mock()
        app._hidden_to_tray = False
        app._display_identifiers.show.side_effect = OSError("monitor unavailable")
        with patch("cast_to_tv.app.messagebox.showwarning") as warning:
            with self.assertLogs("cast_to_tv.app", level="ERROR"):
                app.identify_displays()
        warning.assert_called_once()
        app.status.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
