"""Fetch only the pinned dependencies needed by the Windows feasibility build."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import urllib.request
from pathlib import Path


def fetch(root: Path) -> None:
    pins = json.loads(Path(__file__).with_name("dependencies.json").read_text())
    for name, pin in pins.items():
        if name == "build_headers":
            destination = root / "build"
            destination.mkdir(exist_ok=True)
            for filename in pin["files"]:
                url = f'{pin["url"]}/+/{pin["revision"]}/{filename}?format=TEXT'
                with urllib.request.urlopen(url, timeout=60) as response:
                    (destination / filename).write_bytes(base64.b64decode(response.read()))
            continue
        destination = root / pin["path"]
        if not (destination / ".git").exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1",
                            pin["url"], str(destination)], check=True)
        revision = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
        if revision != pin["revision"]:
            # Refuse to replace local edits in an existing checkout.
            if subprocess.check_output(["git", "-C", str(destination), "diff", "HEAD"], text=True):
                raise RuntimeError(f"Local changes in {destination}")
            subprocess.run(["git", "-C", str(destination), "fetch", "--depth", "1",
                            "origin", pin["revision"]], check=True)
            subprocess.run(["git", "-C", str(destination), "checkout", "--detach", pin["revision"]], check=True)
        print(f"{name}: {pin['revision']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".tmp/native-build/openscreen"))
    fetch(parser.parse_args().root.resolve())
