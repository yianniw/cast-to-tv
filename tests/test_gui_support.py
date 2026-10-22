import io
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from cast_to_tv import app as app_module
from cast_to_tv.settings import load_settings, save_settings, settings_path
from tools.gui_support import GuiHarness, cleanup, isolated_settings, pump_until, select
from tools.smoke_check import live_check, parse_args


class GuiSupportTests(unittest.TestCase):
    def make_app(self):
        app = SimpleNamespace(root=Mock(), status=Mock(), _closed=False, busy=False, close=Mock())
        app.status.get.return_value = "Still connecting"
        return app

    def test_timeout_includes_operation_and_status(self):
        app = self.make_app()
        with self.assertRaisesRegex(AssertionError, "Timed out: startup; status=Still connecting"):
            pump_until(app, lambda: False, 0, "startup")
        app.root.update.assert_not_called()

    def test_async_close_pumps_until_destroyed_without_using_destroyed_tk(self):
        app = self.make_app()
        app.root.update.side_effect = lambda: setattr(app, "_closed", True)
        cleanup(app)
        app.close.assert_called_once()
        app.root.update.assert_called_once()
        app.root.update_idletasks.assert_not_called()
        cleanup(app)
        app.close.assert_called_once()

    def test_pump_propagates_captured_errors_before_updating(self):
        app = self.make_app()
        check = Mock(side_effect=AssertionError("GUI callback failed"))
        with self.assertRaisesRegex(AssertionError, "callback failed"):
            pump_until(app, lambda: False, 1, "startup", check)
        app.root.update.assert_not_called()

    def test_selection_sends_real_event_then_processes_it(self):
        app = self.make_app()
        box = Mock()
        select(app, box, 2)
        box.current.assert_called_once_with(2)
        box.event_generate.assert_called_once_with("<<ComboboxSelected>>")
        app.root.update.assert_called_once()

    def test_worker_thread_is_rejected_before_tk_access(self):
        app = self.make_app()
        errors = []

        def run():
            try:
                cleanup(app)
            except RuntimeError as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=run)
        worker.start()
        worker.join()
        self.assertEqual(errors, ["GUI checks must run on the main thread"])
        app.close.assert_not_called()

    def test_nested_settings_isolation_restores_file_and_path_after_failure(self):
        previous = settings_path()
        with isolated_settings():
            save_settings({"include_audio": True, "keep_screen_on": True})
            outer_path = settings_path()
            before = outer_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "check failed"):
                with isolated_settings() as directory:
                    inner_path = settings_path()
                    save_settings({"include_audio": False})
                    self.assertFalse(load_settings()["include_audio"])
                    self.assertNotEqual(inner_path, outer_path)
                    raise ValueError("check failed")
            self.assertFalse(Path(directory).exists())
            self.assertEqual(settings_path(), outer_path)
            self.assertEqual(outer_path.read_bytes(), before)
        self.assertEqual(settings_path(), previous)

    def test_harness_defaults_to_mocked_discovery_and_keeps_patch_through_close(self):
        app = self.make_app()
        app.root.update.side_effect = lambda: setattr(app, "_closed", True)
        real_discovery = app_module.discover_cast_devices
        device = object()
        with patch("tools.gui_support.tk.Tk", return_value=app.root), \
                patch("tools.gui_support.CastToTVApp", return_value=app), \
                patch("tools.gui_support._retired_apps", []):
            with GuiHarness(devices=[device]):
                self.assertEqual(app_module.discover_cast_devices(), ([device], None))

                def close():
                    self.assertIsNot(app_module.discover_cast_devices, real_discovery)

                app.close.side_effect = close
        self.assertIs(app_module.discover_cast_devices, real_discovery)
        self.assertTrue(app._closed)

    def test_harness_captures_dialog_and_callback_errors_and_still_cleans_up(self):
        app = self.make_app()
        app.root.update.side_effect = lambda: setattr(app, "_closed", True)
        with patch("tools.gui_support.tk.Tk", return_value=app.root), \
                patch("tools.gui_support.CastToTVApp", return_value=app), \
                patch("tools.gui_support._retired_apps", []):
            with self.assertRaisesRegex(AssertionError, "bad capture.*callback failed"):
                with GuiHarness() as gui:
                    app_module.messagebox.showerror("Failure", "bad capture")
                    app.root.report_callback_exception(ValueError, ValueError("callback failed"), None)
                    self.assertEqual(len(gui.errors), 1)
        self.assertTrue(app._closed)

    def test_harness_restores_patches_when_app_construction_fails(self):
        root = Mock()
        original = app_module.messagebox.showerror
        with patch("tools.gui_support.tk.Tk", return_value=root), \
                patch("tools.gui_support.CastToTVApp", side_effect=RuntimeError("construction failed")):
            with self.assertRaisesRegex(RuntimeError, "construction failed"):
                with GuiHarness():
                    self.fail("Failed construction entered body")
        root.destroy.assert_called_once()
        self.assertIs(app_module.messagebox.showerror, original)

    def test_harness_cleans_up_when_check_body_raises(self):
        app = self.make_app()
        app.root.update.side_effect = lambda: setattr(app, "_closed", True)
        with patch("tools.gui_support.tk.Tk", return_value=app.root), \
                patch("tools.gui_support.CastToTVApp", return_value=app), \
                patch("tools.gui_support._retired_apps", []):
            with self.assertRaisesRegex(ValueError, "body failed"):
                with GuiHarness():
                    raise ValueError("body failed")
        self.assertTrue(app._closed)


class SmokeCheckTests(unittest.TestCase):
    UUID = "11111111-2222-4333-8444-555555555555"

    def test_live_access_requires_both_flags_and_a_valid_uuid(self):
        self.assertFalse(parse_args([]).live_tv)
        self.assertEqual(parse_args(["--live-tv", "--tv-uuid", self.UUID]).tv_uuid, self.UUID)
        for argv in (["--live-tv"], ["--tv-uuid", self.UUID], ["--live-tv", "--tv-uuid", "invalid"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    parse_args(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_live_check_unavailable_tv_does_not_cast(self):
        app = SimpleNamespace(devices=[], start_button=Mock())
        harness = Mock(app=app)
        with patch("tools.smoke_check.GuiHarness") as factory:
            factory.return_value.__enter__.return_value = harness
            report = {}
            live_check(self.UUID, report)
        self.assertEqual(report["result"], "unverified")
        app.start_button.invoke.assert_not_called()

    def test_live_check_preserves_an_existing_receiver_app(self):
        target = Mock(cast_info=SimpleNamespace(uuid=UUID(self.UUID)), app_id="EXISTING_APP")
        app = SimpleNamespace(devices=[target], displays=[{}], tv_box=Mock(), display_box=Mock(),
                              mode_choice=Mock(), include_audio=Mock(get=Mock(return_value=False)), start_button=Mock())
        harness = Mock(app=app)
        with patch("tools.smoke_check.GuiHarness") as factory, patch("tools.smoke_check.select"):
            factory.return_value.__enter__.return_value = harness
            report = {}
            live_check(self.UUID, report)
        self.assertEqual(report["result"], "unverified")
        target.quit_app.assert_not_called()
        app.start_button.invoke.assert_not_called()
