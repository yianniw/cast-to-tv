"""Create the notices shipped with the statically linked native helper."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / ".tmp/native-build/openscreen"
files = {
    "Open Screen": "LICENSE",
    "Abseil": "third_party/abseil/src/LICENSE",
    "BoringSSL": "third_party/boringssl/src/LICENSE",
    "BoringSSL fiat": "third_party/boringssl/src/third_party/fiat/LICENSE",
    "JSONCpp": "third_party/jsoncpp/src/LICENSE",
    "Protobuf": "third_party/protobuf/LICENSE",
    "UTF-8 range": "third_party/protobuf/third_party/utf8_range/LICENSE",
    "modp_b64": "third_party/modp_b64/LICENSE",
    "Opus": "third_party/opus/src/COPYING",
    "Opus IPR information": "third_party/opus/src/LICENSE_PLEASE_READ.txt",
}
sections = ["Cast to TV native helper — third-party notices\n\nPinned source revisions:\n" +
            Path(__file__).with_name("dependencies.json").read_text()]
for name, relative in files.items():
    sections.append(f"\n{'=' * 72}\n{name}\nSource: {relative}\n\n" +
                    (source / relative).read_text(encoding="utf-8"))
for header in ("build_config.h", "buildflag.h"):
    relative = "build/" + header
    sections.append(f"\nChromium header notice: {relative}\n" +
                    "\n".join((source / relative).read_text(encoding="utf-8").splitlines()[:3]) + "\n")
sections.append("\nChromium build headers use the Chromium BSD license above.\n"
                "The local CMake overlay exposes SessionConfig's in-flight limit through SenderSession.\n"
                "Windows embedding, desktop capture and WASAPI audio adapters are local project code.\n"
                "FFmpeg is a separate installed executable and is not redistributed in this package.\n")
destination = root / "native/licenses/THIRD-PARTY-NOTICES.txt"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text("\n".join(sections), encoding="utf-8")
print(destination)
