"""Write the version and UTC timestamp embedded by build.ps1."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__


def write_build_info(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({
        "version": __version__,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    write_build_info(parser.parse_args().destination)
