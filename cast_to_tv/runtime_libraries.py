"""Locate replaceable libraries before importing the GUI or Cast stack."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

LIBRARIES = ("pystray", "zeroconf")


def third_party_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "third-party"
    return Path(__file__).resolve().parents[1] / "dist" / "third-party"


def configure_runtime() -> None:
    if not getattr(sys, "frozen", False):
        return
    sys.dont_write_bytecode = True
    runtime = third_party_root() / "runtime"
    if not all((runtime / name / "__init__.py").is_file() for name in LIBRARIES):
        raise RuntimeError(
            "The required third-party/runtime folder is missing or incomplete. "
            "Restore it beside CastToTV.exe from the complete distribution."
        )
    sys.path.insert(0, str(runtime))
    importlib.invalidate_caches()


def check_libraries() -> dict:
    result = {}
    for name in LIBRARIES:
        module = importlib.import_module(name)
        location = Path(module.__file__).resolve()
        if getattr(sys, "frozen", False):
            runtime = (third_party_root() / "runtime").resolve()
            if not location.is_relative_to(runtime):
                raise RuntimeError(f"{name} was loaded outside {runtime}: {location}")
        version = getattr(module, "__version__", None)
        if version is None:
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"
        if isinstance(version, tuple):
            version = ".".join(map(str, version))
        result[name] = {"path": str(location), "version": str(version or "unknown")}
    # Do not require private modules or version fields from replacements.
    # Importing the public packages loads their compiled code and platform
    # backend without opening sockets or creating a tray icon.
    return result


def launch(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    diagnostic = len(args) == 2 and args[0] == "--check-runtime"
    report = {"ok": False, "libraries": {}}
    try:
        configure_runtime()
        report["libraries"] = check_libraries()
        if diagnostic:
            # Imports only: constructors/main perform discovery, settings I/O
            # and GUI creation. Exercise the packaged application dependencies.
            importlib.import_module("cast_to_tv.app")
        report["ok"] = True
    except Exception as exc:
        report["error"] = (
            f"Could not load the replaceable libraries: {exc}\n\n"
            "Restore third-party/runtime beside CastToTV.exe from the complete "
            "distribution, or see third-party/README.txt for replacement instructions."
        )
    if diagnostic:
        Path(args[1]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0 if report["ok"] else 1
    if not report["ok"]:
        # Startup errors must also work before Tk has initialized.
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, report["error"], "Cast to TV", 0x10)
        return 1
    if args:
        raise SystemExit("Usage: CastToTV.exe [--check-runtime REPORT_PATH]")
    from .app import main
    main()
    return 0
