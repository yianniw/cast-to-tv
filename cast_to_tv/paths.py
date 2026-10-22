"""Writable application files live beside the executable, never the working directory."""
from __future__ import annotations

import sys
from pathlib import Path


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1] / "dist"


def temporary_directory() -> Path:
    directory = application_directory() / "temp"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
