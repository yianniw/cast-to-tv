# Cast to TV

Share your Windows desktop on a Chromecast-enabled TV or compatible Google Cast device. Cast to TV aims to be a lightweight alternative for desktop casting, without needing Chrome.

## Requirements

- Windows 10/11 (64-bit).
- A Chromecast-enabled TV, or a compatible Google Cast receiver connected to a screen.
- Your PC and TV connected to the same private home network.
- FFmpeg

## Start casting

Download the Windows ZIP from [Releases](https://github.com/yianniw/cast-to-tv/releases)
and extract it completely before starting the app.

1. **Keep the app package together.** Keep `CastToTV.exe` and the complete `third-party` folder next to each other. The executable needs that folder to run.
2. **Set up FFmpeg.** Get a Windows build through the [FFmpeg download page](https://ffmpeg.org/download.html), under **Windows EXE Files**, and extract it. Add the folder containing `ffmpeg.exe` to your user `Path`.
3. **Open `CastToTV.exe`.**
4. **Choose your TV and display.**
5. **Click Start mirroring.**

Display choices refresh automatically when monitors connect or disconnect. The app follows your selected monitor even if Windows changes its display number. If that monitor disconnects or its size or position changes while casting, mirroring stops safely. Reconnect it or choose another display, then click **Start mirroring** to resume.

## More information

- [Developer guide](docs/development.md): running from source, building, tests, and release packaging.
- [Planned enhancements](ENHANCEMENTS.md) and [third-party distribution information](docs/third-party-distribution.txt).

## License

The original application code is available under the [MIT License](LICENSE).
Bundled third-party components retain their own licenses; see the included
notices and source archives.
