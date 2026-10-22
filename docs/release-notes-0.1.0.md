# Cast to TV 0.1.0

First release of Cast to TV for 64-bit Windows 10 and 11.

Share a selected desktop display with a Chromecast-enabled TV or compatible
Google Cast receiver, without running Chrome.

- Low delay (experimental) native mirroring and Compatibility casting modes.
- Optional system audio, resolution and frame-rate controls.
- Saved TV, display and checkbox preferences.
- Monitor connection and layout change handling.
- Connection recovery, system-tray controls and diagnostic details.

## Download and run

1. Download `CastToTV-0.1.0-windows-x64.zip` and extract it completely.
2. Keep `CastToTV.exe` and the entire `third-party` folder together.
3. Install FFmpeg and add the folder containing `ffmpeg.exe` to your `Path`.
4. Connect the PC and receiver to the same private network.
5. Open `CastToTV.exe`, choose a TV and display, then click **Start mirroring**.

FFmpeg is a separate requirement and is not included in the ZIP. Python,
Visual Studio and Chrome are not required to run the packaged application.

Low delay remains experimental. Compatibility is available as an alternative.

The accompanying `.zip.sha256` file contains the download's SHA-256 checksum.
Third-party notices, applicable licenses, source archives and library
replacement instructions are included in the `third-party` folder.

The original application code is available under the MIT License, included
as `LICENSE` in the download. Third-party components retain their own licenses.

## Building from source

See `docs/development.md` in the repository for prerequisites and commands.
The current package build requires a separately compiled native helper and
maintainer review of its binary checksum; it is not an unattended build from
a fresh clone.
