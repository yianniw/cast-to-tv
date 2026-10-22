"""FFmpeg capability checks and Compatibility encoder arguments."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..errors import CastingError
from ..quality import DEFAULT_QUALITY, VideoQuality


def ffmpeg_executable() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise CastingError(
            "FFmpeg was not found. Install a Windows FFmpeg build with "
            "gdigrab and libx264 support, then restart this app."
        )
    check = subprocess.run(
        [executable, "-hide_banner", "-devices"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    capabilities = check.stdout + check.stderr
    missing = ["gdigrab"] if "gdigrab" not in capabilities else []
    encoder_check = subprocess.run(
        [executable, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if "libx264" not in encoder_check.stdout + encoder_check.stderr:
        missing.append("libx264")
    if check.returncode or encoder_check.returncode or missing:
        details = ", ".join(missing) if missing else "FFmpeg capability query failed"
        raise CastingError(
            f"The installed FFmpeg build is missing required support: {details}."
        )
    return executable


def ffmpeg_command(
    executable: str,
    display: dict[str, int],
    output_dir: Path,
    silent_audio: bool = False,
    quality: VideoQuality = DEFAULT_QUALITY,
) -> list[str]:
    manifest = output_dir / "desktop.mpd"
    width, height = quality.output_size(display)
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "gdigrab",
        "-framerate",
        str(quality.frame_rate),
        "-draw_mouse",
        "1",
        "-offset_x",
        str(display["left"]),
        "-offset_y",
        str(display["top"]),
        "-video_size",
        f'{display["width"]}x{display["height"]}',
        "-i",
        "desktop",
        "-thread_queue_size",
        "512",
        "-f",
        "s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        f"scale={width}:{height}",
        "-r",
        str(quality.frame_rate),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-profile:v",
        "baseline",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        "5000k",
        "-maxrate",
        "5500k",
        "-bufsize",
        "10000k",
        "-g",
        str(quality.frame_rate * 2),
        "-keyint_min",
        str(quality.frame_rate * 2),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-f",
        "dash",
        "-seg_duration",
        "2",
        "-window_size",
        "15",
        "-extra_window_size",
        "5",
        "-use_template",
        "1",
        "-use_timeline",
        "1",
        "-adaptation_sets",
        "id=0,streams=v id=1,streams=a",
        "-init_seg_name",
        "init-stream$RepresentationID$.m4s",
        "-media_seg_name",
        "chunk-stream$RepresentationID$-$Number%05d$.m4s",
        str(manifest),
    ]
    if silent_audio:
        command[command.index("-thread_queue_size"):command.index("-map")] = [
            "-re", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        ]
    return command
