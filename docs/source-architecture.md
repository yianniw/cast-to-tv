# Source responsibilities

The refactor separates capture, delivery, receiver control, and presentation.
Existing Python entry points, callable signatures, exported errors, and native
public headers remain available. The command-line invocations are unchanged:
`python -m cast_to_tv`, `python -m cast_to_tv.native_probe`, and
`python -m cast_to_tv.baseline`.

## Python

| Location | Responsibility |
| --- | --- |
| `app.py` | GUI state, saved selections, discovery and session orchestration, display switching, asynchronous shutdown |
| `ui/main_window.py` | Main-thread construction and binding of the main window's widgets |
| `casting.py` | Compatibility session lifecycle and historical helper exports |
| `errors.py` | Shared cancellation and user-facing casting errors |
| `capture/displays.py` | Physical capture monitor rectangles |
| `capture/audio.py` | Default loopback device selection and stereo PCM conversion |
| `capture/ffmpeg.py` | FFmpeg capability checks and Compatibility encoding arguments |
| `compatibility/manifest.py` | DASH startup readiness and complete segment validation |
| `compatibility/server.py` | DASH HTTP serving, CORS, bounded manifest snapshots, request diagnostics |
| `receivers/discovery.py` | mDNS discovery, cleanup, identity verification, cancellable readiness, local routing |
| `native_casting.py` | GUI session adapter for native mirroring |
| `native_probe.py` | Runtime helper process, control forwarding, cancellation, cleanup, bounded reports; historical CLI facade |
| `native/protocol.py` | Versioned control validation and bounded Cast controller queue |
| `native/patterns.py` | Experimental timestamped VP8 pattern generation |
| `native/cli.py` | Opt-in experimental arguments, clock UI, audio ticks, and experiment orchestration |

The existing display identity/badge, quality, settings, power, tray, about/build
information, diagnostics, and diagnostics-window modules already have cohesive
responsibilities and remain in place. `baseline.py` remains the opt-in DASH
measurement tool. Runtime native mirroring no longer imports the experimental
CLI's Tk clock and tick-playback dependencies.

Facade aliases preserve shared exception and controller identities. Discovery
wrappers also preserve the existing dependency overrides at `casting.CastBrowser`
and `casting.get_device_info`. Stream-server and discovery logs keep their
historical `cast_to_tv.casting` logger name.

## Native helper

| Location | Responsibility |
| --- | --- |
| `probe.cpp` | Command-line configuration and component lifetimes |
| `sender/probe.h`, `sender/probe.cpp` | Negotiation callbacks, audio/video pacing, frame dependencies, sender statistics |
| `media/video_clip.h`, `media/video_clip.cpp` | Encoded VP8 input from IVF or live desktop capture |
| `control/protocol.h`, `control/protocol.cpp` | JSON parsing/emission and the Open Screen message-port adapter |
| `control/input.h`, `control/input.cpp` | Bounded stdin polling and command dispatch on the task runner |
| `capture/command.h`, `capture/command.cpp` | Capture backend selection, DXGI output matching, FFmpeg arguments |
| `desktop_source.h`, `desktop_source.cpp` | Owned capture process, IVF pipe reader, bounded frame queue |
| `system_audio.h`, `system_audio.cpp` | Windows loopback capture and Opus encoding |
| `opus_clip.h` | Validated synthetic Ogg Opus input |
| `windows_platform.h`, `platform/task_runner.cpp` | Serial callback scheduling and shutdown |
| `platform/udp_socket.cpp` | Winsock UDP transport and callback delivery |
| `windows_platform.cpp` | Open Screen clock and logging hooks |

`CMakeLists.txt` lists all implementation units explicitly. Existing native build,
dependency-fetching, and license-collection scripts retain their paths and roles.
Capture queues, pacing thresholds, control limits, wire protocol, helper options,
and output executable paths are unchanged by extraction.
