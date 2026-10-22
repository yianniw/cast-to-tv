"""Collect a timed DASH baseline with an on-screen clock and JSON trace."""
from __future__ import annotations

import argparse
import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path

from .casting import CastSession
from .capture.displays import list_displays
from .diagnostics import LatencyMarker, SessionDiagnostics
from .paths import application_directory
from .receivers.discovery import discover_cast_devices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tv", required=True, help="Exact Chromecast friendly name")
    parser.add_argument("--display", type=int, default=1, help="Display number, starting at 1")
    parser.add_argument("--duration", type=float, default=120, help="Playback duration in seconds")
    parser.add_argument("--output", type=Path, default=application_directory() / "diagnostics")
    parser.add_argument("--silent-audio", action="store_true", help="Use generated silence; does not test system audio")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("Duration must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    displays = list_displays()
    if not 1 <= args.display <= len(displays):
        parser.error("The selected display is unavailable")
    devices, browser = discover_cast_devices(timeout=6)
    root = None
    marker = None
    diagnostics = None
    session = None
    worker = None
    messages: queue.Queue[Exception | None] = queue.Queue()
    cancelled = threading.Event()
    failures: list[str] = []
    try:
        device = next((item for item in devices if item.cast_info.friendly_name == args.tv), None)
        if device is None:
            raise RuntimeError(f"TV {args.tv!r} was not discovered")
        diagnostics = SessionDiagnostics(args.output.resolve())
        session = CastSession(device, displays[args.display - 1], failures.append, diagnostics,
                              silent_audio=args.silent_audio)
        diagnostics.record("baseline_requested", duration_seconds=args.duration,
                           silent_audio=args.silent_audio)
        root = tk.Tk()
        root.withdraw()
        marker = LatencyMarker(root, displays[args.display - 1], diagnostics)

        def cancel() -> None:
            cancelled.set()
            session._stop_event.set()

        marker.window.protocol("WM_DELETE_WINDOW", cancel)

        def collect() -> None:
            error = None
            try:
                session.start()
                print(f"Baseline active for {args.duration:g}s; compare the PC and TV timers.", flush=True)
                deadline = time.monotonic() + args.duration
                while time.monotonic() < deadline and not cancelled.wait(.25):
                    if failures:
                        raise RuntimeError(failures[-1])
            except Exception as exc:
                error = exc
            finally:
                cleanup = session.stop()
                if cleanup and error is None:
                    error = RuntimeError("; ".join(cleanup))
                messages.put(error)

        result: list[Exception | None] = []

        def poll() -> None:
            try:
                result.append(messages.get_nowait())
            except queue.Empty:
                root.after(100, poll)
                return
            marker.close()
            root.quit()

        worker = threading.Thread(target=collect, daemon=True)
        worker.start()
        root.after(100, poll)
        root.mainloop()
        if result and result[0] is not None:
            raise result[0]
        print(f"Trace: {diagnostics.trace_path}", flush=True)
        print(f"Report: {diagnostics.directory / 'summary.json'}", flush=True)
    finally:
        cancelled.set()
        if session is not None:
            session._stop_event.set()
        if worker is not None:
            worker.join(timeout=45)
        if session is not None and (worker is None or not worker.is_alive()):
            session.stop()
        if marker is not None:
            marker.close()
        if root is not None:
            root.destroy()
        if diagnostics is not None and worker is None:
            diagnostics.close()
        for device in devices:
            if device.socket_client.is_alive():
                device.disconnect()
        browser.stop_discovery()


if __name__ == "__main__":
    main()
