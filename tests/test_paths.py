import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cast_to_tv import paths
from cast_to_tv.app import main
from cast_to_tv.diagnostics import SessionDiagnostics
from cast_to_tv.diagnostics_window import app_log_path
from cast_to_tv.settings import settings_path


class ApplicationPathTests(unittest.TestCase):
    def test_source_runs_use_project_dist(self):
        with patch("sys.frozen", False, create=True):
            self.assertEqual(paths.application_directory(), Path(paths.__file__).resolve().parents[1] / "dist")

    def test_packaged_runs_use_executable_folder_instead_of_extraction_or_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "portable app"
            with patch("sys.frozen", True, create=True), \
                    patch("sys.executable", str(package / "CastToTV.exe")), \
                    patch("sys._MEIPASS", str(Path(directory) / "extracted"), create=True), \
                    patch("os.getcwd", return_value=str(Path(directory) / "elsewhere")):
                self.assertEqual(paths.application_directory(), package)
                self.assertEqual(settings_path(), package / "settings.json")
                self.assertEqual(app_log_path(), package / "logs" / "CastToTV.log")

    def test_temporary_files_and_default_reports_stay_beside_app(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "portable app"
            with patch("cast_to_tv.paths.application_directory", return_value=package):
                temporary_root = paths.temporary_directory()
                self.assertEqual(temporary_root, package / "temp")
                with tempfile.TemporaryDirectory(dir=temporary_root) as session:
                    (Path(session) / "capture.json").write_text("{}", encoding="utf-8")
                self.assertEqual(list(temporary_root.iterdir()), [])
                recorder = SessionDiagnostics()
                try:
                    recorder.record("capture_started")
                finally:
                    recorder.close()
                self.assertEqual(recorder.directory.parent, package / "diagnostics")
                self.assertTrue(recorder.trace_path.is_file())
                self.assertTrue((recorder.directory / "summary.json").is_file())

    def test_unwritable_app_folder_reports_problem_without_falling_back_to_external_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "blocked"
            blocked.write_text("file, not a folder", encoding="utf-8")
            root = Mock()
            with patch("cast_to_tv.app.tk.Tk", return_value=root), \
                    patch("cast_to_tv.app.app_log_path", return_value=blocked / "CastToTV.log"), \
                    patch("cast_to_tv.app.messagebox.showerror") as error, \
                    patch("cast_to_tv.app.CastToTVApp") as app:
                main()
            app.assert_not_called()
            root.destroy.assert_called_once()
            self.assertIn("writable folder", error.call_args.args[1])
            self.assertEqual(list(Path(directory).iterdir()), [blocked])


if __name__ == "__main__":
    unittest.main()
