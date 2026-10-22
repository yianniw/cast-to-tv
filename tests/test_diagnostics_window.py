import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cast_to_tv.about import AppInfo
from cast_to_tv.app import CastRequest, CastToTVApp
from cast_to_tv.diagnostics_window import (
    MAX_LOG_BYTES, MAX_LOG_LINES, DiagnosticsDialog, build_report, log_excerpt,
)


class ReportTests(unittest.TestCase):
    def test_log_tail_is_bounded_in_bytes_and_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.log"
            lines = [f"line {index}: " + "x" * 400 for index in range(250)]
            path.write_text("\n".join(lines), encoding="utf-8")
            excerpt = log_excerpt(path)
        self.assertTrue(excerpt.endswith(lines[-1]))
        self.assertNotIn("line 0:", excerpt)
        self.assertLessEqual(len(excerpt.encode("utf-8")), MAX_LOG_BYTES)
        self.assertLessEqual(len(excerpt.splitlines()), MAX_LOG_LINES)

    def test_log_tail_handles_missing_empty_unreadable_and_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.log"
            self.assertIn("No log file", log_excerpt(path))
            path.touch()
            self.assertIn("empty", log_excerpt(path))
            path.write_bytes(b"old\ninvalid: \xff\nlatest: done\n")
            self.assertIn("latest: done", log_excerpt(path))
            with patch.object(Path, "open", side_effect=PermissionError("locked")):
                self.assertIn("Could not read the log: locked", log_excerpt(path))

    def test_single_oversized_log_line_still_produces_a_useful_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.log"
            path.write_text("x" * (MAX_LOG_BYTES * 2) + "last error\n", encoding="utf-8")
            self.assertTrue(log_excerpt(path).endswith("last error"))

    def test_report_combines_app_identity_problem_and_current_context_without_log(self):
        with tempfile.TemporaryDirectory() as directory:
            report = build_report(AppInfo("1.2", "test build"), "TV: Office\nSystem audio: Off",
                                  "Startup failed: receiver unreachable", Path(directory) / "missing")
        for expected in ("Version: 1.2", "Build date: test build", "TV: Office", "System audio: Off",
                         "Startup failed: receiver unreachable", "No log file"):
            self.assertIn(expected, report)


class DialogActionTests(unittest.TestCase):
    def setUp(self):
        self.dialog = DiagnosticsDialog.__new__(DiagnosticsDialog)
        self.dialog.window = Mock()
        self.dialog.details = Mock()
        self.dialog.status = Mock()
        self.dialog.get_report = Mock(return_value="new report")
        self.dialog.report = "old report"

    def test_copy_refreshes_and_copies_current_report(self):
        self.dialog.copy_details()
        self.dialog.window.clipboard_append.assert_called_once_with("new report")
        self.dialog.details.configure.assert_called_with(state="disabled")
        self.dialog.status.set.assert_called_with("Details copied.")

    def test_clipboard_failure_keeps_selectable_details_and_reports_failure(self):
        self.dialog.window.clipboard_append.side_effect = tk.TclError("clipboard busy")
        self.dialog.copy_details()
        self.assertEqual(self.dialog.report, "new report")
        self.assertIn("Could not copy", self.dialog.status.set.call_args.args[0])

    def test_unchanged_refresh_preserves_selection(self):
        self.dialog.report = "new report"
        self.dialog.refresh()
        self.dialog.details.delete.assert_not_called()

    def test_open_log_handles_missing_viewer_and_success(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("cast_to_tv.diagnostics_window.os.startfile", create=True) as startfile:
            self.dialog.path = Path(directory) / "test.log"
            self.dialog.open_log()
            startfile.assert_not_called()
            self.assertIn("No log file", self.dialog.status.set.call_args.args[0])
            self.dialog.path.touch()
            startfile.side_effect = OSError("no viewer")
            self.dialog.open_log()
            self.assertIn("Could not open the log: no viewer", self.dialog.status.set.call_args.args[0])
            startfile.side_effect = None
            self.dialog.open_log()
            startfile.assert_called_with(self.dialog.path)
            self.assertIn("Log opened", self.dialog.status.set.call_args.args[0])


class AppDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.app = CastToTVApp.__new__(CastToTVApp)
        self.app.root = Mock()
        self.app._closing = False
        self.app._diagnostics_dialog = None
        self.app._last_problem = None
        self.app._hidden_to_tray = False
        self.app._active_request = None
        self.app.devices = []
        self.app.displays = []
        self.app.tv_box = Mock(current=Mock(return_value=-1))
        self.app.display_box = Mock(current=Mock(return_value=-1))
        self.app.status = Mock(get=Mock(return_value="Choose a TV"))
        self.app.mode_choice = Mock(get=Mock(return_value="Compatibility"))
        self.app.resolution_choice = Mock(get=Mock(return_value="720p"))
        self.app.frame_rate_choice = Mock(get=Mock(return_value="15"))
        self.app.include_audio = Mock(get=Mock(return_value=True))
        self.app.measure_delay = Mock(get=Mock(return_value=False))
        self.app.keep_screen_on = Mock(get=Mock(return_value=False))

    def test_report_handles_unselected_devices_and_retains_latest_problem(self):
        with patch("cast_to_tv.app.messagebox.showerror"), self.assertLogs("cast_to_tv.app", level="ERROR"):
            self.app._show_error("Casting failed", "network unavailable")
        report = self.app._diagnostic_report()
        for expected in ("TV: Not selected", "Display: Not selected", "Casting failed", "network unavailable"):
            self.assertIn(expected, report)

    def test_active_context_uses_cast_settings_instead_of_edited_next_cast_preferences(self):
        device = SimpleNamespace(cast_info=SimpleNamespace(friendly_name="Actual TV", host="192.0.2.1"))
        self.app._active_request = CastRequest(device, {"width": 1080, "height": 1920}, 2, True, False, True)
        report = self.app._diagnostic_report()
        for expected in ("Actual TV", "Display 3 (1080 x 1920)", "Low delay (experimental)",
                         "System audio: Off", "Measure mirroring delay: On", "Resolution limit: 1080p",
                         "Frame rate: 30 fps", "Video output: 606 x 1080"):
            self.assertIn(expected, report)

    @patch("cast_to_tv.app.DiagnosticsDialog")
    def test_open_reuses_existing_window_recreates_closed_window_and_ignores_closing(self, factory):
        dialog = factory.return_value
        dialog.window.winfo_exists.return_value = True
        self.app.show_diagnostics()
        self.app.show_diagnostics()
        factory.assert_called_once()
        self.assertEqual(dialog.show.call_count, 2)
        dialog.window.winfo_exists.return_value = False
        self.app.show_diagnostics()
        self.assertEqual(factory.call_count, 2)
        self.app._closing = True
        self.app.show_diagnostics()
        self.assertEqual(factory.call_count, 2)


if __name__ == "__main__":
    unittest.main()
