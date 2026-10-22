"""Portable preferences shared by source and packaged launches."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import TypedDict
from uuid import UUID

from .quality import DEFAULT_QUALITY, FRAME_RATES, RESOLUTIONS
from . import paths

LOGGER = logging.getLogger(__name__)


class Settings(TypedDict):
    keep_screen_on: bool
    include_audio: bool
    measure_delay: bool
    close_to_tray: bool
    tv_uuid: str | None
    display_id: str | None
    resolution: str
    frame_rate: int


SettingValue = bool | str | int | None
DEFAULT_SETTINGS: Settings = {
    "keep_screen_on": False,
    "include_audio": True,
    "measure_delay": False,
    "close_to_tray": False,
    "tv_uuid": None,
    "display_id": None,
    "resolution": DEFAULT_QUALITY.resolution,
    "frame_rate": DEFAULT_QUALITY.frame_rate,
}


def _valid_setting(name: str, value: SettingValue) -> bool:
    if name not in DEFAULT_SETTINGS:
        return False
    if name in ("keep_screen_on", "include_audio", "measure_delay", "close_to_tray"):
        return isinstance(value, bool)
    if name == "resolution":
        return isinstance(value, str) and value in RESOLUTIONS
    if name == "frame_rate":
        return type(value) is int and value in FRAME_RATES
    if value is None:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    if name == "tv_uuid":
        try:
            UUID(value)
        except ValueError:
            return False
    return True


def settings_path() -> Path:
    return paths.application_directory() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path if path is not None else settings_path()
    result = DEFAULT_SETTINGS.copy()
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("Settings must be a JSON object")
        for name in DEFAULT_SETTINGS:
            if name in settings:
                if _valid_setting(name, settings[name]):
                    value = settings[name]
                    result[name] = str(UUID(value)) if name == "tv_uuid" and value is not None else value
                else:
                    LOGGER.warning("Ignoring invalid %s setting in %s", name, path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        LOGGER.warning("Could not read settings from %s: %s", path, exc)
    return result


def save_settings(changes: dict[str, SettingValue], path: Path | None = None) -> None:
    path = path if path is not None else settings_path()
    for name, value in changes.items():
        if not _valid_setting(name, value):
            raise ValueError(f"Invalid preference: {name}")
    # Merge only changed preferences with the latest file, preserving changes
    # made by another open instance since startup.
    settings = load_settings(path)
    settings.update(changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        # Replace a complete file so interrupted or concurrent saves cannot
        # leave a partially written preference for the next launch.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix="settings-", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(settings, temporary, indent=2)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
