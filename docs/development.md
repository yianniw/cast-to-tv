# Cast to TV developer guide

For everyday use with the packaged app, start with the [README](../README.md).

Run the commands below in PowerShell from the project root.

## Application files

Packaged runs keep `settings.json`, `logs/CastToTV.log` (1 MB plus one rotated backup), temporary stream/configuration files in `temp/`, and opt-in developer reports in `diagnostics/` beside the executable. Source runs use the project's `dist/` folder for the same files. Paths do not depend on the working directory. Temporary session files are removed when their sessions stop; the folder may remain.

Settings are read only from the local `settings.json`; a missing file uses the defaults. Test tools isolate the application directory. Explicit CLI report/output paths still override the defaults. PyInstaller's bundled runtime extraction continues to use Windows' temporary folder.

## Run from source

You need Windows 10 or 11, Python 3.11 or later with working Tcl/Tk, and FFmpeg on `PATH`. FFmpeg needs `gdigrab` and `libx264` for Compatibility, plus `ddagrab` and `libvpx` for Low delay.

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

For **Low delay (experimental)**, install Visual Studio 2022's **Desktop development with C++** workload, CMake tools, and Git, then build the native helper once:

```powershell
.\native\build.ps1 -Fetch
python -m cast_to_tv
```

See the [native build instructions](../native/README.md) for prerequisites, pinned dependencies, transport details, and experimental CLI options. Preserve `.tmp/native-build`: source runs and packaging need its helper.

To run without the native helper, use `python -m cast_to_tv` and select **Compatibility** before starting. Low delay remains the GUI default; failures do not automatically switch modes. Both modes require a compatible receiver on the same private network for casting, and system audio requires an enabled default Windows playback device.

## Build the Windows package

Release builds require **64-bit Python 3.13.5** and the exact reviewed dependency versions. Install them in an isolated environment:

```powershell
python -m pip install -r tools/release-requirements.txt
```

Development requirements remain separate. `requirements-build.txt` contains the optional packaging tools (`python -m pip install -r requirements-build.txt`), but release packaging must use the reviewed versions above.

From a fresh checkout, first build the native helper with `.\native\build.ps1 -Fetch` using Visual Studio 2022's C++ workload. Packaging requires the resulting reviewed helper so both modes remain available. Then build:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The executable is written to `dist\CastToTV.exe`. **Distribute it with the entire `dist\third-party` folder beside it.** The executable alone is incomplete. That folder contains replaceable LGPL libraries, all collected notices, corresponding source archives, and offline instructions. FFmpeg remains a separate system requirement on `PATH`. The package requires Windows 10 or later, but no separately installed Python, Visual Studio, or Chrome. See the [third-party distribution instructions](third-party-distribution.txt).

The app version is defined in [`cast_to_tv/__init__.py`](../cast_to_tv/__init__.py). `build.ps1` embeds it and the UTC packaging time for the About window.

### Release lock and source materials

`tools/release-lock.json` pins reviewed package versions, notice hashes, native revisions/helper hash, and upstream source/license archive URLs and SHA-256 hashes. A normal build never updates the lock. Source archives must match the distributed source-form library files.

New dependency versions, rebuilt native helpers, changed notices, or Python runtime upgrades require reviewing and updating the lock before release. Missing notices, required source, or unaccounted components fail the build.

The helper checksum pins one reviewed binary, not just its source revision.
A fresh native compilation can therefore fail packaging with **Native helper
differs from the reviewed release lock**, even when the source revisions match.
The maintainer must validate the rebuilt helper and update
`native_helper_sha256` in `tools/release-lock.json` before packaging it. The
current scripts do not provide an unattended fresh-clone package build.

Network access is needed on the first build to fetch locked artifacts. Later builds use the verified `.tmp/packaging/downloads` cache. To populate it explicitly, run:

```powershell
python -m tools.release fetch
```

### Validate a package

Close the packaged app first, then run:

```powershell
python -m tools.release validate
python -m tools.release_check
```

The first command checks the executable/companion pairing and release materials. The second inspects the actual executable archives, verifies external imports and modified library replacements, tests missing/unloadable packages, and launches through a path containing spaces. It temporarily changes generated library files and restores them. These checks do not contact a TV or change saved preferences.

The executable also supports `CastToTV.exe --check-runtime REPORT_PATH` for a standalone import report.

### Prepare a GitHub release

After building and validating the package, create the release download:

```powershell
python -m tools.release archive
```

This creates `dist/CastToTV-VERSION-windows-x64.zip` and its `.zip.sha256`
checksum file. The ZIP contains the paired executable, validated `third-party`
files, and the project's `LICENSE` file when present. Saved settings, logs,
temporary files and developer reports beside the executable are excluded.

Create a GitHub release tagged `vVERSION` from the accepted source commit and
attach both generated files. Use the version in `cast_to_tv/__init__.py`.
Publish the complete ZIP rather than the standalone executable. Release notes
should describe the Windows and FFmpeg requirements, both casting modes, and
the experimental status of Low delay. Retain all bundled third-party materials.

The original application code is licensed under MIT; keep the root `LICENSE`
file in the release ZIP. Review the Git author identity that will be public.
The native build maps source paths to `CastToTV` so compiler diagnostics do not
expose the builder's Windows home directory; package checks verify this.
Third-party notices apply separately to their respective components.

## Automated and GUI checks

Run unit tests without a TV or Tk window:

```powershell
python -m unittest discover -s tests
```

Reusable developer checks live in `tools/`. New GUI checks should import `tools.gui_support` rather than historical scripts under `.tmp`. GUI checks require a Windows desktop with working Tcl/Tk; GUI and packaging may require execution outside the sandbox for Tcl/Tk access.

For a real GUI check with mocked TV discovery and isolated preferences:

```powershell
python -m tools.smoke_check
```

Offline checks exercise Diagnostics with simulated discovery/casting errors and clipboard/viewer failures. Viewer dispatch is mocked so checks do not launch an external editor; use **Open log** manually to verify your viewer.

### Live receiver check

Use an available, idle receiver and explicitly opt in with its Cast UUID:

```powershell
python -m tools.smoke_check --live-tv --tv-uuid YOUR-TV-UUID
```

The live check mirrors Display 1 without audio for five seconds, stops, and checks that resources and the native receiver were released. It preserves existing receiver playback and uses temporary settings. It needs the native helper and FFmpeg; picture quality still requires observation on the TV.

Results are summarized in `.tmp/smoke-check.json`, overwritten on each run. Exit code 0 means passed, 1 means failed, and 2 means the TV check was unverified because the TV was unavailable or already in use. Recovery and tray checks use the same exit codes and preference isolation.

### Recovery and tray checks

```powershell
python -m tools.recovery_check
python -m tools.tray_check
```

Recovery checks exercise actual Tk controls in both modes with controlled session workers: bounded automatic recovery, video-only fallback without preference changes, and Stop/Close during cleanup. Add `--live-tv --tv-uuid YOUR-TV-UUID` to disconnect the real Cast control socket and verify automatic recovery, a second interruption waiting for Retry, manual retry, and Stop in both modes. Live checks require an idle receiver and use Display 1 without audio. Reports are `.tmp/recovery-gui-check.json` and `.tmp/recovery-live-check.json`. Physical audio-device removal and prolonged network outages still need manual checks.

Add the same live flags to `tools.tray_check` to test connecting cancellation, hidden video-only casting, screen-awake behavior, tray Stop, and Exit against an idle TV. Reports use `.tmp/tray-check.json`. Automation invokes actual menu callbacks from a worker and sends a native primary-click message; verify right-click menu interaction manually in the rebuilt executable.

## Architecture and maintenance

- [AGENTS.md](../AGENTS.md) provides the concise project map and working instructions. Keep Tk operations on the main thread and wait for asynchronous shutdown.
- [Source architecture](source-architecture.md) describes the Python and native helper responsibilities.
- [ENHANCEMENTS.md](../ENHANCEMENTS.md) tracks planned features, acceptance checks, results, and the one-feature-at-a-time workflow.
- The [native helper guide](../native/README.md) covers the current capture/transport pipeline, build dependencies, CLI options, and licenses. Compatibility uses FFmpeg H.264/AAC encoding and DASH delivery through Google's default media receiver.
