"""Generate timestamped VP8 patterns for opt-in receiver experiments."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def generate_clip(path: Path, duration: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg with libvpx is required for the test pattern")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Every frame is independent so overload can drop obsolete frames safely.
    overlay = ("drawtext=fontfile='C\\:/Windows/Fonts/consola.ttf':"
               "text='%{pts\\:hms}':fontsize=48:fontcolor=white:"
               "box=1:boxcolor=black:x=20:y=20")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=30", "-t", str(duration),
                    "-vf", overlay, "-an", "-c:v", "libvpx", "-deadline", "realtime",
                    "-cpu-used", "8", "-threads", "4", "-b:v", "4M", "-g", "1",
                    "-lag-in-frames", "0", "-auto-alt-ref", "0", "-f", "ivf", str(path)],
                   check=True, timeout=max(60, duration * 2), creationflags=subprocess.CREATE_NO_WINDOW)
