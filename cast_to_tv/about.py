"""Application identity and a nonmodal About window."""
from __future__ import annotations

import json
import logging
import sys
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .runtime_libraries import third_party_root

LOGGER = logging.getLogger(__name__)
_BUILD_INFO_PATH = Path(__file__).with_name("build_info.json")


@dataclass(frozen=True)
class AppInfo:
    version: str
    build_date: str

    def fields(self) -> list[tuple[str, str]]:
        return [("Version", self.version), ("Build date", self.build_date)]

    def as_text(self) -> str:
        return "Cast to TV\n" + "\n".join(f"{name}: {value}" for name, value in self.fields())


def get_app_info() -> AppInfo:
    if not getattr(sys, "frozen", False):
        return AppInfo(__version__, "Not packaged")
    build_date = "Unavailable"
    try:
        metadata = json.loads(_BUILD_INFO_PATH.read_text(encoding="utf-8"))
        if metadata["version"] != __version__:
            raise ValueError("Build metadata version does not match the application")
        timestamp = datetime.fromisoformat(metadata["built_at_utc"])
        if timestamp.tzinfo is None:
            raise ValueError("Build timestamp has no time zone")
        build_date = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        LOGGER.warning("Could not read application build information: %s", exc)
    return AppInfo(__version__, build_date)


class AboutDialog:
    def __init__(self, root: tk.Tk, info: AppInfo) -> None:
        self.info = info
        self.licenses_window: tk.Toplevel | None = None
        self.window = tk.Toplevel(root)
        self.window.title("About Cast to TV")
        self.window.transient(root)
        self.window.resizable(True, False)
        self.window.minsize(400, 0)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        frame = ttk.Frame(self.window, padding=16)
        frame.grid(sticky="nsew")
        self.window.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Cast to TV", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12),
        )
        self.values: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        for row, (name, value) in enumerate(info.fields(), start=1):
            ttk.Label(frame, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            variable = tk.StringVar(master=self.window, value=value)
            entry = ttk.Entry(frame, textvariable=variable, state="readonly", width=32)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self.values[name] = variable
            self.entries[name] = entry
        row = len(self.values) + 1
        self.status = tk.StringVar(master=self.window)
        ttk.Label(frame, textvariable=self.status).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        buttons = ttk.Frame(frame)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        self.copy_button = ttk.Button(buttons, text="Copy details", command=self.copy_details)
        self.copy_button.grid(row=0, column=0, padx=(0, 8))
        self.licenses_button = ttk.Button(buttons, text="Third-party licenses", command=self.show_licenses)
        self.licenses_button.grid(row=0, column=1, padx=(0, 8))
        self.close_button = ttk.Button(buttons, text="Close", command=self.close)
        self.close_button.grid(row=0, column=2)

    def show_licenses(self) -> None:
        if self.licenses_window is not None and self.licenses_window.winfo_exists():
            self.licenses_window.lift()
            return
        window = self.licenses_window = tk.Toplevel(self.window)
        window.title("Third-party licenses")
        window.transient(self.window)
        window.geometry("800x600")
        window.minsize(450, 300)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        self.licenses_text = ScrolledText(window, wrap="word", padx=12, pady=12)
        self.licenses_text.grid(row=0, column=0, sticky="nsew")
        try:
            if getattr(sys, "frozen", False):
                path = Path(__file__).resolve().parents[1] / "third-party" / "NOTICES.txt"
            else:
                path = third_party_root() / "NOTICES.txt"
            content = path.read_text(encoding="utf-8")
        except OSError:
            LOGGER.warning("Could not read third-party notices", exc_info=True)
            content = (
                "Third-party notices are unavailable. Restore the complete distribution.\n"
                "For source runs, build the release with build.ps1 to generate the full notices."
            )
        self.licenses_text.insert("1.0", content)
        self.licenses_text.configure(state="disabled")
        def close_licenses(_event=None):
            window.destroy()
            return "break"

        self.licenses_close_button = ttk.Button(window, text="Close", command=close_licenses)
        self.licenses_close_button.grid(row=1, column=0, sticky="e", padx=12, pady=12)
        window.bind("<Escape>", close_licenses)
        self.licenses_text.focus_set()

    def show(self) -> None:
        self.window.deiconify()
        self.window.lift()

    def copy_details(self) -> None:
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.info.as_text())
            self.status.set("Details copied.")
        except tk.TclError:
            LOGGER.warning("Could not copy About details", exc_info=True)
            self.status.set("Could not copy. Select a field and copy its text instead.")

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()
