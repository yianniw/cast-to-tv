"""Validate that a live DASH manifest has enough complete media to start."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

STARTUP_SEGMENTS = 3
DASH_NAMESPACE = "{urn:mpeg:dash:schema:mpd:2011}"


def stream_is_ready(manifest: Path) -> bool:
    """Require the initialization files and complete segments for both tracks."""
    try:
        root = ET.fromstring(manifest.read_bytes())
        if root.get("type") != "dynamic":
            return False
        track_types = set()
        for representation in root.iter(f"{DASH_NAMESPACE}Representation"):
            track_types.add(representation.attrib["mimeType"].split("/")[0])
            stream_id = representation.attrib["id"]
            template = representation.find(f"{DASH_NAMESPACE}SegmentTemplate")
            if template is None:
                return False
            timeline = template.find(f"{DASH_NAMESPACE}SegmentTimeline")
            if timeline is None:
                return False
            segment_count = sum(
                1 + int(entry.get("r", "0"))
                for entry in timeline.findall(f"{DASH_NAMESPACE}S")
            )
            if segment_count < STARTUP_SEGMENTS:
                return False
            initialization = template.attrib["initialization"].replace("$RepresentationID$", stream_id)
            media = template.attrib["media"].replace("$RepresentationID$", stream_id)
            start_number = int(template.get("startNumber", "1"))
            files = [manifest.parent / initialization] + [
                manifest.parent / media.replace("$Number%05d$", f"{number:05d}")
                for number in range(start_number, start_number + STARTUP_SEGMENTS)
            ]
            if not all(file.is_file() and file.stat().st_size > 0 for file in files):
                return False
        return {"video", "audio"}.issubset(track_types)
    except (OSError, ET.ParseError, KeyError, ValueError):
        return False
