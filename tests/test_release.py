import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cast_to_tv import runtime_libraries as runtime
from tools import release


class RuntimeLibraryTests(unittest.TestCase):
    def test_source_run_does_not_change_import_path(self):
        before = list(sys.path)
        with patch.object(sys, "frozen", False, create=True):
            runtime.configure_runtime()
        self.assertEqual(sys.path, before)

    def test_frozen_runtime_is_relative_to_executable_not_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="release path ") as directory:
            root = Path(directory)
            for name in runtime.LIBRARIES:
                package = root / "third-party/runtime" / name
                package.mkdir(parents=True)
                (package / "__init__.py").touch()
            with patch.object(sys, "frozen", True, create=True), \
                    patch.object(sys, "executable", str(root / "CastToTV.exe")), \
                    patch.object(sys, "path", list(sys.path)), \
                    patch.object(sys, "dont_write_bytecode", False):
                runtime.configure_runtime()
                self.assertEqual(Path(sys.path[0]), root / "third-party/runtime")
                self.assertTrue(sys.dont_write_bytecode)

    def test_missing_libraries_are_reported_without_opening_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            with patch.object(runtime, "configure_runtime", side_effect=RuntimeError("missing package")), \
                    patch.object(runtime, "check_libraries") as check:
                self.assertEqual(runtime.launch(["--check-runtime", str(report)]), 1)
            check.assert_not_called()
            data = json.loads(report.read_text())
            self.assertFalse(data["ok"])
            self.assertIn("Restore third-party/runtime", data["error"])

    def test_replacement_versions_are_not_restricted(self):
        module = SimpleNamespace(__file__=__file__, __version__="user-replacement")
        with patch.object(sys, "frozen", False, create=True), \
                patch.object(runtime.importlib, "import_module", return_value=module):
            result = runtime.check_libraries()
        self.assertTrue(all(item["version"] == "user-replacement" for item in result.values()))

    def test_embedded_libraries_cannot_silently_override_external_packages(self):
        module = SimpleNamespace(__file__=__file__, __version__="test")
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(runtime.importlib, "import_module", return_value=module), \
                patch.object(runtime, "third_party_root", return_value=Path("unrelated")):
            with self.assertRaisesRegex(RuntimeError, "outside"):
                runtime.check_libraries()


class ReleaseMaterialTests(unittest.TestCase):
    def test_external_filter_covers_python_extensions_and_metadata(self):
        for name in ["pystray", "pystray._win32", "zeroconf/_dns.cp313-win_amd64.pyd",
                     "zeroconf\\_services\\browser.pyd", "pystray-0.19.5.dist-info/COPYING"]:
            self.assertTrue(release.external_entry((name, "unused", "unused")), name)
        for name in ["pychromecast", "native/helper.exe", "third-party/NOTICES.txt", "six"]:
            self.assertFalse(release.external_entry((name, "unused", "unused")), name)

    def test_cached_archive_corruption_is_rejected_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".tmp/packaging/downloads"
            cache.mkdir(parents=True)
            (cache / "source.zip").write_bytes(b"corrupted")
            with patch.object(release.urllib.request, "urlopen") as network:
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    release.artifact(root, {"filename": "source.zip", "sha256": "wrong", "url": "unused"})
            network.assert_not_called()

    def test_source_must_match_distributed_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "example/__init__.py"
            file.parent.mkdir()
            file.write_text("changed = True\n")
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("source/example/__init__.py", "changed = False\n")
            distribution = SimpleNamespace(files=[Path("example/__init__.py")], locate_file=lambda p: root / p)
            with self.assertRaisesRegex(RuntimeError, "does not correspond"):
                release.verify_source("example", distribution, archive, "source/")

    def test_version_drift_and_missing_notices_fail_before_building(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            lock = {"python": sys.version.split()[0], "abi": sys.implementation.cache_tag,
                    "architecture": "win_amd64", "packages": {"example": {"version": "1", "notices": []}}}
            release.write_json(root / "tools/release-lock.json", lock)
            with patch.object(release.metadata, "distribution", return_value=SimpleNamespace(version="2")):
                with self.assertRaisesRegex(RuntimeError, "Version drift"):
                    release.load_lock(root)
            with patch.object(release.metadata, "distribution", return_value=SimpleNamespace(version="1")):
                with self.assertRaisesRegex(RuntimeError, "Missing notices"):
                    release.load_lock(root)

    def test_release_rejects_missing_or_changed_support_material(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release.write_json(root / "manifest.json", {"support_files": {"missing.txt": "hash"}})
            with self.assertRaisesRegex(RuntimeError, "Missing or changed release material"):
                release.validate_release(root)

    def test_release_rejects_unaccounted_files_and_missing_required_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in runtime.LIBRARIES:
                path = root / "runtime" / name / "__init__.py"
                path.parent.mkdir(parents=True)
                path.touch()
            manifest = {"support_files": {p.relative_to(root).as_posix(): release.digest(p)
                        for p in root.rglob("*") if p.is_file()},
                        "components": [{"name": "certifi", "notices": ["license"]}]}
            release.write_json(root / "manifest.json", manifest)
            with self.assertRaisesRegex(RuntimeError, "Missing required source"):
                release.validate_release(root)
            (root / "unreviewed.pyd").touch()
            with self.assertRaisesRegex(RuntimeError, "Unaccounted files"):
                release.validate_release(root)

    def test_unreviewed_analysis_component_fails_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            distribution = SimpleNamespace(metadata={"Name": "unreviewed"},
                files=[Path("unknown.py")], locate_file=lambda p: root / p)
            analysis = SimpleNamespace(pure=[("unknown", str(root / "unknown.py"), "PYMODULE")],
                                       binaries=[], datas=[], scripts=[])
            with patch.object(release, "load_lock", return_value={"packages": {}}), \
                    patch.object(release.metadata, "distributions", return_value=[distribution]):
                with self.assertRaisesRegex(RuntimeError, "Unreviewed bundled distribution"):
                    release.finish_release(root, root, analysis)


if __name__ == "__main__":
    unittest.main()
