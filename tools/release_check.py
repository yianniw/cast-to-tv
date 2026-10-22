"""Offline checks of the existing packaged release; never connects to a TV.

Library modifications are confined to generated release files and restored in
finally blocks. Do not run while the packaged application is in use.
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

from tools.release import EXTERNAL, external_entry, validate_release


def check(root: Path) -> dict:
    executable = root / "dist/CastToTV.exe"
    release = root / "dist/third-party"
    manifest = validate_release(release)
    work = root / ".tmp/packaging"
    archive = CArchiveReader(str(executable))
    assert not any(external_entry((name, "", "")) for name in archive.toc), "Embedded LGPL files"
    pyz_name = next(name for name in archive.toc if name.endswith(".pyz"))
    pyz = archive.open_embedded_archive(pyz_name)
    assert not any(external_entry((name, "", "")) for name in pyz.toc), "Embedded LGPL modules"
    notices = next(name for name in archive.toc if name.replace("\\", "/") == "third-party/NOTICES.txt")
    assert archive.extract(notices) == (release / "NOTICES.txt").read_bytes(), "Embedded notices mismatch"
    helper = next(name for name in archive.toc if name.replace("\\", "/") == "native/cast_streaming_probe.exe")
    assert hashlib.sha256(archive.extract(helper)).hexdigest() == manifest["native_helper_sha256"], "Native helper mismatch"

    def probe(path=executable, *, success=True):
        report = work / "runtime-check.json"
        report.unlink(missing_ok=True)
        process = subprocess.run([str(path), "--check-runtime", str(report)],
                                 cwd=root / "docs", timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
        assert report.is_file(), f"Runtime did not produce a report (exit {process.returncode})"
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["ok"] == success, result
        assert process.returncode == (0 if success else 1), result
        return result

    original = probe()
    checks = ["No LGPL modules or extensions embedded", "Embedded notices and native helper match the manifest",
              "Application and external library imports from unrelated working directory"]
    for name in EXTERNAL:
        directory = release / "runtime" / name
        init = directory / "__init__.py"
        contents = init.read_bytes()
        try:
            init.write_bytes(contents + b'\n__version__ = "replacement-check"\n')
            result = probe()
            assert result["libraries"][name]["version"] == "replacement-check", result
            checks.append(f"Modified {name} loaded without version or hash restriction")
            init.write_bytes(contents + b'\nraise ImportError("Deliberately unloadable replacement")\n')
            failure = probe(success=False)
            assert "Restore third-party/runtime" in failure["error"], failure
            checks.append(f"Unloadable {name} produces restoration instructions")
        finally:
            init.write_bytes(contents)
        backup = directory.with_name(name + ".release-check-backup")
        if backup.exists():
            raise RuntimeError(f"Unexpected backup exists: {backup}")
        directory.rename(backup)
        try:
            failure = probe(success=False)
            assert "missing or incomplete" in failure["error"], failure
            checks.append(f"Missing {name} produces restoration instructions")
        finally:
            backup.rename(directory)

    # A junction launches the existing distribution through a path with spaces;
    # no second executable or alternate build is created.
    junction = work / "release path with spaces"
    if junction.exists():
        raise RuntimeError(f"Unexpected launch-path fixture exists: {junction}")
    quoted = lambda path: "'" + str(path).replace("'", "''") + "'"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"New-Item -ItemType Junction -Path {quoted(junction)} "
                    f"-Target {quoted(executable.parent)} | Out-Null"],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        probe(junction / executable.name)
        checks.append("Existing executable launched through a path containing spaces")
    finally:
        if junction.is_junction() and junction.resolve() == executable.parent.resolve():
            os.rmdir(junction)
        else:
            raise RuntimeError("Launch fixture changed unexpectedly; refusing cleanup")
    validate_release(release)
    report = {"result": "passed", "checks": checks, "libraries": original["libraries"],
              "release_files_restored": True, "live_tv": False}
    (work / "release-check.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(check(Path(__file__).resolve().parents[1]), indent=2))
