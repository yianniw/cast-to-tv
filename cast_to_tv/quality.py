"""Video limits shared by both mirroring backends."""
from __future__ import annotations

from dataclasses import dataclass

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080)}
FRAME_RATES = (15, 30)


@dataclass(frozen=True)
class VideoQuality:
    resolution: str = "1080p"
    frame_rate: int = 30

    def __post_init__(self) -> None:
        if self.resolution not in RESOLUTIONS:
            raise ValueError("Choose a 720p or 1080p resolution limit.")
        if type(self.frame_rate) is not int or self.frame_rate not in FRAME_RATES:
            raise ValueError("Choose a frame rate of 15 or 30 fps.")

    def output_size(self, display: dict) -> tuple[int, int]:
        """Fit the source inside the limit, using even encoder dimensions."""
        max_width, max_height = RESOLUTIONS[self.resolution]
        ratio = min(max_width / display["width"], max_height / display["height"], 1)
        return (max(2, int(display["width"] * ratio) // 2 * 2),
                max(2, int(display["height"] * ratio) // 2 * 2))


DEFAULT_QUALITY = VideoQuality()
