"""Bounded support reports and a nonmodal diagnostics window."""
from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .about import AppInfo
from . import paths

MAX_LOG_BYTES = 32 * 1024
MAX_LOG_LINES = 100


def app_log_path() -> Path:
    return paths.application_directory() / "logs" / "CastToTV.log"


def log_excerpt(path: Path) -> str:
    """Read only the bounded tail, even if a log is unexpectedly large."""
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - MAX_LOG_BYTES)
            stream.seek(start)
            data = stream.read(MAX_LOG_BYTES)
        if start:
            # The first bytes can be a partial UTF-8 character or log line.
            remainder = data.partition(b"\n")[2]
            if remainder:
                data = remainder
        text = "\n".join(data.decode("utf-8", errors="replace").splitlines()[-MAX_LOG_LINES:])
        return text or "The log is empty."
    except FileNotFoundError:
        return "No log file is available yet."
    except OSError as exc:
        return f"Could not read the log: {exc}"


def build_report(info: AppInfo, context: str, problem: str | None, path: Path) -> str:
    return (
        f"{info.as_text()}\n\nCurrent state\n{context}\n\n"
        f"Latest error or warning (this launch)\n{problem or 'None recorded.'}\n\n"
        f"Recent log (up to {MAX_LOG_LINES} lines / 32 KiB)\n{log_excerpt(path)}"
    )


class DiagnosticsDialog:
    def __init__(self, root: tk.Tk, get_report: Callable[[], str], path: Path) -> None:
        self.get_report = get_report
        self.path = path
        self.report = ""
        self.window = tk.Toplevel(root)
        self.window.title("Cast to TV diagnostics")
        self.window.transient(root)
        self.window.geometry("700x440")
        self.window.minsize(600, 330)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        frame = ttk.Frame(self.window, padding=12)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.details = tk.Text(frame, wrap="word", state="disabled", width=72, height=18)
        self.details.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.details.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.details.configure(yscrollcommand=scrollbar.set)
        self.details.bind("<Control-a>", self.select_all)
        self.status = tk.StringVar(master=self.window)
        ttk.Label(frame, textvariable=self.status, wraplength=560).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=6,
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e")
        self.refresh_button = ttk.Button(buttons, text="Refresh", command=self.refresh)
        self.open_button = ttk.Button(buttons, text="Open log", command=self.open_log)
        self.copy_button = ttk.Button(buttons, text="Copy details", command=self.copy_details)
        self.close_button = ttk.Button(buttons, text="Close", command=self.close)
        for column, button in enumerate((self.refresh_button, self.open_button, self.copy_button, self.close_button)):
            button.grid(row=0, column=column, padx=(8, 0))

    def select_all(self, _event=None) -> str:
        self.details.tag_add("sel", "1.0", "end-1c")
        return "break"

    def refresh(self) -> None:
        report = self.get_report()
        if report != self.report:
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", report)
            self.details.configure(state="disabled")
            self.report = report
        self.status.set("Details refreshed.")

    def show(self) -> None:
        self.refresh()
        self.window.deiconify()
        self.window.lift()

    def open_log(self) -> None:
        try:
            if not self.path.is_file():
                self.status.set("No log file is available yet.")
                return
            os.startfile(self.path)
            self.status.set("Log opened in your default viewer.")
        except OSError as exc:
            self.status.set(f"Could not open the log: {exc}")

    def copy_details(self) -> None:
        self.refresh()
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.report)
            self.status.set("Details copied.")
        except tk.TclError:
            self.status.set("Could not copy. Select the details and copy the text instead.")

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()
