"""Experimental Chrome-free Open Screen pattern and live desktop sender."""
from __future__ import annotations

import argparse
import logging
import queue
import shutil
import subprocess
import tempfile
import math
import struct
import wave
import winsound
import threading
import time
import tkinter as tk
from pathlib import Path

from ..capture.displays import list_displays
from ..native_probe import run_probe
from ..paths import application_directory, temporary_directory
from ..receivers.discovery import discover_cast_devices
from .patterns import generate_clip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tv", required=True)
    parser.add_argument("--helper", type=Path,
                        default=Path(".tmp/native-build/cmake/Release/cast_streaming_probe.exe"))
    parser.add_argument("--clip", type=Path)
    parser.add_argument("--desktop", action="store_true", help="Capture a live monitor instead of the pattern")
    parser.add_argument("--synthetic-audio", action="store_true", help="Play short generated ticks; does not capture system audio")
    parser.add_argument("--system-audio", action="store_true", help="Capture the default Windows playback device")
    parser.add_argument("--play-system-ticks", action="store_true", help="Play test ticks through the PC output for loopback validation")
    parser.add_argument("--display", type=int, default=1)
    parser.add_argument("--capture", choices=("ddagrab", "gdigrab"), default="ddagrab")
    parser.add_argument("--max-width", type=int, default=1920)
    parser.add_argument("--max-height", type=int, default=1080)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--playout-ms", type=int, default=200)
    parser.add_argument("--report", type=Path, default=application_directory() / "diagnostics" / "native-probe.json")
    args = parser.parse_args()
    if args.system_audio and args.synthetic_audio:
        parser.error("Choose system audio or generated ticks")
    if args.play_system_ticks and not args.system_audio:
        parser.error("System tick playback requires --system-audio")
    if not args.helper.is_file():
        parser.error("Build the native helper first; see native/README.md")
    if not 0 < args.duration <= 600 or not 100 <= args.playout_ms <= 1000:
        parser.error("Duration must be 0..600 seconds and playout 100..1000 milliseconds")
    desktop = None
    display = None
    if args.desktop:
        if args.clip is not None:
            parser.error("Choose either a desktop or an IVF clip")
        displays = list_displays()
        if not 1 <= args.display <= len(displays):
            parser.error("The selected display is unavailable")
        display = displays[args.display - 1]
        if not 2 <= args.max_width <= 1920 or not 2 <= args.max_height <= 1080:
            parser.error("Output limits must fit 1920×1080 and be at least two pixels")
        ratio = min(args.max_width / display["width"], args.max_height / display["height"], 1)
        desktop = {"ffmpeg": shutil.which("ffmpeg"), "display": display,
                   "output_width": max(2, int(display["width"] * ratio) // 2 * 2),
                   "output_height": max(2, int(display["height"] * ratio) // 2 * 2),
                   "fps": 30, "capture": args.capture, "duration": str(args.duration)}
        if desktop["ffmpeg"] is None:
            parser.error("FFmpeg is required for live capture")
    clip = args.clip or application_directory() / "diagnostics" / "pattern.ivf"
    if args.clip is None and desktop is None:
        print("Generating the timestamped VP8 test pattern...", flush=True)
        generate_clip(clip, args.duration)
    audio_clip = None
    if args.synthetic_audio:
        audio_clip = application_directory() / "diagnostics" / "ticks.ogg"
        audio_clip.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "aevalsrc=if(lt(mod(t\\,5)\\,0.06)\\,0.1*sin(2*PI*880*t)\\,0):s=48000",
                        "-t", str(args.duration), "-ac", "2", "-c:a", "libopus", "-application", "lowdelay",
                        "-frame_duration", "20", "-b:a", "128k", "-f", "ogg", str(audio_clip)],
                       check=True, timeout=max(60, args.duration * 2), creationflags=subprocess.CREATE_NO_WINDOW)
    logging.basicConfig(level=logging.WARNING)
    devices, browser = discover_cast_devices(timeout=6)
    root = None
    worker = None
    cancel = threading.Event()
    events: queue.Queue = queue.Queue()
    result = []
    tick_directory = None
    try:
        device = next((item for item in devices if item.cast_info.friendly_name == args.tv), None)
        if device is None:
            raise RuntimeError(f"TV {args.tv!r} was not discovered")
        root = tk.Tk()
        root.title("Native Cast test — PC clock")
        root.attributes("-topmost", True)
        label = tk.Label(root, text="Connecting…", font=("Consolas", 48), padx=25, pady=25)
        label.pack()
        if display is not None:
            root.update_idletasks()
            # Use physical Windows coordinates, including negative positions.
            import ctypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            window = user32.GetAncestor(root.winfo_id(), 2)
            user32.SetWindowPos(window, ctypes.c_void_p(-1), display["left"] + 24,
                               display["top"] + 24, 0, 0, 0x0001 | 0x0010)
        tk.Label(root, text="Compare this clock with the timestamped pattern on the TV.").pack()
        root.protocol("WM_DELETE_WINDOW", cancel.set)
        origin = None
        if args.play_system_ticks:
            tick_directory = tempfile.TemporaryDirectory(prefix="CastToTV-audio-test-", dir=temporary_directory())
            tick_file = Path(tick_directory.name) / "ticks.wav"
            with wave.open(str(tick_file), "wb") as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(48000)
                tone = b"".join(struct.pack("<hh", value, value) for i in range(240000)
                                for value in [int(3276 * math.sin(2 * math.pi * 880 * i / 48000)) if i < 2880 else 0])
                output.writeframes(tone)

        def collect() -> None:
            error = None
            try:
                run_probe(device, args.helper, clip, args.playout_ms, events, cancel, args.report, desktop, audio_clip,
                          system_audio=args.system_audio)
            except Exception as exc:
                error = exc
            events.put(("done", error))

        def update() -> None:
            nonlocal origin
            while True:
                try:
                    kind, value = events.get_nowait()
                except queue.Empty:
                    break
                if kind == "clock_start":
                    origin = value
                    if args.play_system_ticks:
                        root.after(100, lambda: winsound.PlaySound(str(tick_file), winsound.SND_FILENAME |
                                                                  winsound.SND_ASYNC | winsound.SND_LOOP))
                elif kind == "done":
                    result.append(value)
                    root.quit()
                    return
            if origin is not None:
                elapsed = max(0, time.perf_counter() - origin)
                label.config(text=f"{elapsed:06.1f}", bg="white" if elapsed % 5 < .08 else "#dddddd")
            root.after(50, update)

        worker = threading.Thread(target=collect, daemon=True)
        worker.start()
        root.after(50, update)
        root.mainloop()
        if result and result[0] is not None:
            raise result[0]
        print(f"Report: {args.report.resolve()}", flush=True)
    finally:
        cancel.set()
        if worker is not None:
            worker.join(timeout=30)
        if root is not None:
            root.destroy()
        if args.play_system_ticks:
            winsound.PlaySound(None, 0)
        if tick_directory is not None:
            tick_directory.cleanup()
        for device in devices:
            if device.socket_client.is_alive():
                device.disconnect()
        browser.stop_discovery()



if __name__ == "__main__":
    main()
