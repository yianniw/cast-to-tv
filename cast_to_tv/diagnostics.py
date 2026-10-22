"""Opt-in timing traces and visible latency measurements for mirroring."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import queue
import statistics
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

LOGGER = logging.getLogger(__name__)
NS = "{urn:mpeg:dash:schema:mpd:2011}"


def manifest_tracks(path: Path) -> list[dict[str, Any]]:
    """Read media timestamps from the same manifest the receiver uses."""
    root = ET.fromstring(path.read_bytes())
    tracks = []
    for representation in root.iter(f"{NS}Representation"):
        template = representation.find(f"{NS}SegmentTemplate")
        if template is None:
            continue
        timeline = template.find(f"{NS}SegmentTimeline")
        if timeline is None:
            continue
        scale = int(template.get("timescale", "1"))
        if scale <= 0:
            raise ValueError("Invalid DASH timescale")
        stream_id = representation.attrib["id"]
        media = template.attrib["media"].replace("$RepresentationID$", stream_id)
        number = int(template.get("startNumber", "1"))
        position = 0
        segments = []
        for entry in timeline.findall(f"{NS}S"):
            position = int(entry.get("t", str(position)))
            duration = int(entry.attrib["d"])
            repeats = int(entry.get("r", "0"))
            if duration <= 0 or not 0 <= repeats <= 10000:
                raise ValueError("Unsupported DASH timeline")
            for _ in range(repeats + 1):
                name = media.replace("$Number%05d$", f"{number:05d}")
                segments.append({"number": number, "file": name,
                                 "start_seconds": position / scale,
                                 "end_seconds": (position + duration) / scale})
                position += duration
                number += 1
        tracks.append({"id": stream_id, "mime_type": representation.get("mimeType"),
                       "end_seconds": position / scale, "segments": segments})
    return tracks


def distribution(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"count": len(values), "median": statistics.median(values),
            "p95": ordered[math.ceil(len(ordered) * .95) - 1],
            "minimum": ordered[0], "maximum": ordered[-1]}


def build_report(events: list[dict[str, Any]], samples: Path | None = None) -> dict[str, Any]:
    first = {}
    for event in events:
        first.setdefault(event["event"], event["elapsed_seconds"])
    physical = []
    if samples is not None:
        with samples.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                delay = float(row["pc_seconds"]) - float(row["tv_seconds"])
                if not math.isfinite(delay) or delay < 0:
                    raise ValueError("Latency samples must contain finite PC/TV times with PC >= TV")
                physical.append(delay)
    requests = [event for event in events if event["event"] == "http_complete"]
    gaps = [event["manifest_end_minus_playhead_seconds"] for event in events
            if event["event"] == "receiver_status" and event.get("state") == "PLAYING"
            and event.get("manifest_end_minus_playhead_seconds") is not None]
    output_gaps = []
    audio_writes = []
    for event in events:
        if event["event"] == "encoder_progress" and "capture_started" in first:
            try:
                output_seconds = int(event["values"]["out_time_us"]) / 1_000_000
                output_gaps.append(event["elapsed_seconds"] - first["capture_started"] - output_seconds)
            except (KeyError, ValueError):
                pass
        if event["event"] == "audio_progress":
            audio_writes.append(event["max_pipe_write_ms"])
    report = {
        "schema_version": 1,
        "capture_configuration": next(({key: event.get(key) for key in ("display", "audio_source")}
                                       for event in events if event["event"] == "capture_launch"), None),
        "milestones_seconds": {key: first[key] for key in
            ("capture_started", "stream_ready", "load_sent", "playback_advancing", "session_stopped")
            if key in first},
        "http_requests": len(requests),
        "http_duration_ms": distribution([event["duration_ms"] for event in requests]),
        "http_failures": sum(event.get("status", 0) >= 400 or event.get("error") is not None
                             for event in requests),
        "manifest_end_minus_playhead_seconds": distribution(gaps),
        "output_timestamp_vs_capture_elapsed_gap_seconds": distribution(output_gaps),
        "maximum_audio_pipe_write_ms_per_interval": distribution(audio_writes),
        "visible_latency_seconds": distribution(physical),
        "visible_latency_method": "simultaneous PC/TV timer readings" if physical else "not measured",
        "notes": ["Manifest/playhead gaps are media-timeline diagnostics, not measured visible latency.",
                  "Segment availability is first observed by a 250 ms sampler.",
                  "FFmpeg output progress is not a per-frame capture timestamp.",
                  "Visible latency requires simultaneous readings of the PC and TV clocks."],
    }
    return report


class SessionDiagnostics:
    def __init__(self, directory: Path | None = None) -> None:
        base = directory or paths.application_directory() / "diagnostics"
        base.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="session-", dir=base))
        self.trace_path = self.directory / "events.jsonl"
        self.started = time.monotonic()
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=10000)
        self._lock = threading.Lock()
        self._closed = False
        self._write_failed = False
        self._listener: DiagnosticStatusListener | None = None
        self._manifest_end: float | None = None
        self.stream_url: str | None = None
        self._writer = threading.Thread(target=self._write, daemon=True)
        self._writer.start()
        self.record("diagnostics_started", schema_version=1)

    def record(self, event: str, **fields: Any) -> None:
        with self._lock:
            if self._closed or self._write_failed:
                return
            try:
                self._queue.put_nowait({"event": event, "elapsed_seconds": time.monotonic() - self.started,
                                       "utc": datetime.now(timezone.utc).isoformat(), **fields})
            except queue.Full:
                self._write_failed = True
                LOGGER.warning("Diagnostic trace queue overflowed; recording stopped")

    def _write(self) -> None:
        try:
            with self.trace_path.open("w", encoding="utf-8") as target:
                while True:
                    event = self._queue.get()
                    if event is None:
                        break
                    target.write(json.dumps(event) + "\n")
                    if self._queue.empty():
                        target.flush()
        except OSError:
            self._write_failed = True
            LOGGER.exception("Could not write diagnostic trace")

    def attach(self, controller: Any) -> None:
        # PyChromecast provides registration but no unregister API. Keep one
        # reusable forwarding listener per controller instead of leaking a
        # recorder every time diagnostics starts and stops.
        listener = getattr(controller, "_cast_to_tv_diagnostics_listener", None)
        if not isinstance(listener, DiagnosticStatusListener):
            listener = DiagnosticStatusListener()
            controller.register_status_listener(listener)
            controller._cast_to_tv_diagnostics_listener = listener
        listener.diagnostics = self
        self._listener = listener

    def watch_manifest(self, manifest: Path, stop: threading.Event) -> None:
        version = None
        seen_numbers: dict[str, int] = {}
        while not stop.wait(.25):
            try:
                stamp = manifest.stat().st_mtime_ns
                if stamp == version:
                    continue
                tracks = manifest_tracks(manifest)
                for track in tracks:
                    for segment in track["segments"]:
                        if segment["number"] <= seen_numbers.get(track["id"], 0):
                            continue
                        file = manifest.parent / segment["file"]
                        if not file.is_file():
                            continue
                        self.record("segment_available", track=track["id"], mime_type=track["mime_type"],
                                    bytes=file.stat().st_size, **segment)
                        seen_numbers[track["id"]] = segment["number"]
                ends = [track["end_seconds"] for track in tracks]
                self._manifest_end = min(ends) if len(ends) >= 2 else None
                self.record("manifest_updated", tracks=[{key: value for key, value in track.items()
                            if key != "segments"} for track in tracks])
                version = stamp
            except (OSError, ET.ParseError, ValueError, KeyError):
                continue

    def new_media_status(self, status: Any) -> None:
        if self.stream_url is None or status.content_id != self.stream_url:
            return
        gap = None if self._manifest_end is None else self._manifest_end - status.current_time
        updated = status.last_updated
        age = None if updated is None else (datetime.now(timezone.utc) - updated).total_seconds() * 1000
        self.record("receiver_status", state=status.player_state, playhead_seconds=status.current_time,
                    playback_rate=status.playback_rate, status_age_ms=age,
                    manifest_end_minus_playhead_seconds=gap, idle_reason=status.idle_reason)

    def close(self) -> None:
        self.record("session_stopped")
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._listener is not None and self._listener.diagnostics is self:
                self._listener.diagnostics = None
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
        self._writer.join(timeout=5)
        if self._writer.is_alive():
            LOGGER.warning("Diagnostic writer is still finishing: %s", self.trace_path)
            return
        try:
            events = [json.loads(line) for line in self.trace_path.read_text(encoding="utf-8").splitlines()]
            (self.directory / "summary.json").write_text(json.dumps(build_report(events), indent=2), encoding="utf-8")
        except (OSError, ValueError):
            LOGGER.exception("Could not summarize diagnostics")


class DiagnosticStatusListener:
    def __init__(self) -> None:
        self.diagnostics: SessionDiagnostics | None = None

    def new_media_status(self, status: Any) -> None:
        diagnostics = self.diagnostics
        if diagnostics is not None:
            diagnostics.new_media_status(status)


class LatencyMarker:
    """A visible clock; trace recording requires an explicit developer recorder."""
    def __init__(self, root: Any, display: dict[str, int],
                 diagnostics: SessionDiagnostics | None = None) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.diagnostics = diagnostics
        self.started = diagnostics.started if diagnostics is not None else time.monotonic()
        self.window = tk.Toplevel(root)
        self.window.title("Mirroring delay measurement")
        self.window.attributes("-topmost", True)
        self.window.geometry("650x250")
        self.label = tk.Label(self.window, font=("Consolas", 40), bg="#172033", fg="white")
        self.label.pack(fill="both", expand=True)
        ttk.Label(self.window, text="Compare this timer on the PC and TV at the same instant.").pack(pady=4)
        self.audio = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.window, text="Sound a short tick with each flash", variable=self.audio).pack()
        self.window.update_idletasks()
        x, y = display["left"] + 24, display["top"] + 24
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user.GetAncestor.restype = wintypes.HWND
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user.SetWindowPos.restype = wintypes.BOOL
            handle = user.GetAncestor(self.window.winfo_id(), 2)
            user.SetWindowPos(handle, -1, x, y, self.window.winfo_width(), self.window.winfo_height(), 0x10)
        else:
            self.window.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._last_flash = -1
        self._after: str | None = None
        self._tick()

    def _tick(self) -> None:
        elapsed = time.monotonic() - self.started
        flash = int(elapsed / 5)
        self.label.configure(text=f"{elapsed:08.1f} s\nflash {flash:04d}",
                             bg="white" if elapsed % 5 < .2 else "#172033",
                             fg="#172033" if elapsed % 5 < .2 else "white")
        if flash != self._last_flash:
            self._last_flash = flash
            if elapsed % 5 < .2 and self.diagnostics is not None:
                self.diagnostics.record("marker_flash", flash=flash, clock_seconds=elapsed)
            if elapsed % 5 < .2 and self.audio.get() and os.name == "nt":
                threading.Thread(target=self._beep, daemon=True).start()
        self._after = self.window.after(100, self._tick)

    def _beep(self) -> None:
        import winsound
        if self.diagnostics is not None:
            self.diagnostics.record("marker_audio_tick")
        try:
            winsound.Beep(880, 60)
        except RuntimeError:
            LOGGER.warning("Diagnostic audio tick was unavailable")

    def close(self) -> None:
        if self.window.winfo_exists():
            if self._after is not None:
                self.window.after_cancel(self._after)
            self.window.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a mirroring timing trace")
    parser.add_argument("trace", type=Path, help="Path to events.jsonl")
    parser.add_argument("--samples", type=Path, help="CSV of simultaneous pc_seconds,tv_seconds readings")
    args = parser.parse_args()
    events = [json.loads(line) for line in args.trace.read_text(encoding="utf-8").splitlines()]
    print(json.dumps(build_report(events, args.samples), indent=2))


if __name__ == "__main__":
    main()
