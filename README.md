# Cast to TV

Share your Windows desktop on a Chromecast-enabled TV or compatible Google Cast device. Cast to TV aims to be a lightweight alternative for desktop casting, without needing Chrome.

## Requirements

- A Windows 10/11.
- A Chromecast-enabled TV, or a compatible Google Cast receiver connected to a screen.
- Your PC and TV connected to the same private home network.
- FFmpeg

## Start casting

1. **Keep the app package together.** Keep `CastToTV.exe` and the complete `third-party` folder next to each other. The executable needs that folder to run.
2. **Set up FFmpeg.** Get a Windows build through the [FFmpeg download page](https://ffmpeg.org/download.html), under **Windows EXE Files**, and extract it. Add the folder containing `ffmpeg.exe` to your user `Path`.
3. **Open `CastToTV.exe`.**
4. **Choose your TV and display.**
5. **Click Start mirroring.**

Display choices refresh automatically when monitors connect or disconnect. The app follows your selected monitor even if Windows changes its display number. If that monitor disconnects or its size or position changes while casting, mirroring stops safely. Reconnect it or choose another display, then click **Start mirroring** to resume.

## More information

- [Developer guide](docs/development.md): running from source, building, and tests.
- [Planned enhancements](ENHANCEMENTS.md) and [third-party distribution information](docs/third-party-distribution.txt).
