# Native mirroring helper

This helper powers the app's **Low delay (experimental)** mode on Windows.
It uses Open Screen's `SenderSession` and frame
sender, including AES encryption, Cast RTP/RTCP, packet feedback and retransmission.
Python supplies discovery and the existing Cast TLS/control connection through
Open Screen's `MessagePort` interface. Chrome is not a runtime dependency.

The helper supports a paced all-keyframe VP8 IVF test clip and live desktop
capture. Live capture uses Desktop Duplication through an owned FFmpeg child,
software VP8 with half-second keyframes, and a bounded four-frame queue. Skipped
dependencies recover at the next keyframe. Native WASAPI captures system audio
and libopus encodes 20 ms stereo packets through a bounded five-packet queue.
The GUI defaults to the native backend as **Low delay (experimental)**, with
Compatibility available as a manual fallback.

## Build and run

Install Visual Studio 2022's Desktop development with C++ workload, CMake tools,
Git, and the project's Python requirements. From the project root:

```powershell
.\native\build.ps1 -Fetch
python -m cast_to_tv.native_probe --tv tv_name --duration 120
python -m cast_to_tv.native_probe --tv tv_name --desktop --display 1 --duration 120 --system-audio
```

The dependency script fetches the exact commits in `dependencies.json`. Core
dependencies match the pinned Open Screen `DEPS`; Opus is separately pinned to
the official 1.6.1 release. Protobuf and its UTF-8 library are vendored in that
checkout. Only Chromium's two platform build headers are downloaded; the full
Chromium checkout and toolchain are unnecessary. Build products stay in `.tmp`.
The CMake wrapper selects the pinned sender targets from GN, supplies Windows
clock/task/IPv4 UDP implementations, and uses the C++ JSON and RTCP parsers.
Its build-tree overlay adds one optional field to `SenderSession::Configuration`
and forwards it into the existing `SessionConfig::max_in_flight_media_duration`.
The helper requests a bounded 150 ms in-flight budget: the upstream default caused
frame rejections and discarded dependency chains on this receiver. Exact pinned
contexts are checked; dependency checkouts remain pristine. MSVC uses a local
`__PRETTY_FUNCTION__=__FUNCSIG__` definition and consistent static runtimes.

FFmpeg with `libvpx` generates a moving 640×360 pattern with a timestamp. Compare
the PC clock window and TV timestamp at the same instant. The PC clock begins
when negotiation completes; bridge/scheduling uncertainty is a few clock update
intervals, so this method is approximate. Negotiated playout and round-trip time
are diagnostics, **not measured visible latency**.

`--playout-ms 200` requests a target; the TV may impose its own constraints.
Reports default to `dist/diagnostics/native-probe.json`; `--report PATH` selects
another location. The report saves sanitized control milestones and
sender feedback, excluding OFFER encryption secrets. Closing the PC clock window
cancels playback. The bridge stops the helper and quits only the mirroring app
it launched if that app still owns its route.

The initial adapter supports unicast IPv4 only, as used by the tested TV. No
hardware encoding or adaptive bitrate is provided yet. The all-keyframe test
pattern consumes more bandwidth than the desktop stream. Visual playback must be checked on the TV;
successful negotiation alone does not establish a picture or low latency.

Live capture resolves the selected display's physical rectangle against DXGI
outputs, including negative coordinates. A monitor on another graphics adapter
can use `--capture gdigrab`; the GUI currently uses Desktop Duplication. Use
`--max-width`/`--max-height` to change output limits, up to 1920×1080. FFmpeg runs
in a Windows job that kills its capture child if the helper exits or crashes.

`--synthetic-audio` sends generated Opus ticks, whereas `--system-audio` captures
the default Windows playback device. Add `--play-system-ticks` to play quiet ticks
through the PC output for an actual loopback check. The latter plays a temporary
WAV, stops it on exit, and does not change device selection or volume. Missing or
invalidated audio devices produce a clear failure instead of silently switching
to a slower backend. Silence keeps the negotiated audio track alive when the
Windows rendering engine has no active packets.

## Dependency licenses

Preserve the upstream license files when distributing a linked helper:

| Component | License source within the pinned checkout |
| --- | --- |
| Open Screen | `LICENSE` (BSD) |
| Chromium build headers | Chromium BSD header notices |
| Abseil | `third_party/abseil/src/LICENSE` (Apache 2.0) |
| BoringSSL | `third_party/boringssl/src/LICENSE` (multiple notices) |
| BoringSSL fiat | `third_party/boringssl/src/third_party/fiat/LICENSE` (MIT) |
| JSONCpp | `third_party/jsoncpp/src/LICENSE` (MIT/public-domain notices) |
| Protobuf | `third_party/protobuf/LICENSE` (BSD) |
| UTF-8 range | `third_party/protobuf/third_party/utf8_range/LICENSE` (MIT) |
| modp_b64 | `third_party/modp_b64/LICENSE` (BSD) |
| Opus | `third_party/opus/src/COPYING` (BSD) |

`build.ps1` requires the reviewed helper and includes its collected native notices
in the executable's offline license viewer and `dist/third-party/licenses/native.txt`.
Distribute `dist/CastToTV.exe` with the complete `dist/third-party` companion folder.
The helper statically links its dependencies and C++
runtime; its remaining DLL dependencies are Windows system libraries. Running
the application does not require Chrome, Visual Studio or Python. A clean-machine
test beyond this development PC remains pending.
