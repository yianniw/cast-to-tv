# Working in Cast to TV

## Project map
- Windows Python/Tkinter application; entry point: `python -m cast_to_tv`.
- `cast_to_tv/app.py`: GUI, saved selections, startup/stop/switch/close lifecycle.
- `cast_to_tv/casting.py`: Compatibility session lifecycle and historical helper exports.
- `cast_to_tv/capture/`, `compatibility/`, `receivers/`: capture configuration, DASH delivery, and Cast discovery/connection helpers.
- `cast_to_tv/ui/`: main-window widget construction; lifecycle stays in `app.py`.
- `cast_to_tv/native_casting.py`: GUI adapter for the native mirroring helper.
- `cast_to_tv/native_probe.py`: live native bridge and historical CLI entry point; runtime code uses it.
- `cast_to_tv/native/`: protocol validation/controller, pattern generation, and experimental CLI.
- `cast_to_tv/displays.py`, `display_identification.py`: monitor identity and badges.
- `cast_to_tv/settings.py`, `power.py`: saved preferences and screen-awake behavior.
- `cast_to_tv/tray.py`: Windows tray worker; callbacks post actions to the GUI queue.
- `cast_to_tv/about.py`, `build_info.py`: version and packaged build information.
- `cast_to_tv/diagnostics.py`: production timing/diagnostics, not disposable reports.
- `cast_to_tv/diagnostics_window.py`: bounded support reports and the nonmodal Diagnostics window.
- `native/`: C++ mirroring helper, build scripts, dependency licenses; `capture/`, `control/`, `media/`, `platform/`, and `sender/` isolate helper responsibilities. See `docs/source-architecture.md`.
- `tests/`: automated unittest suite; `tools/`: reusable opt-in GUI/live-TV checks.
- `docs/`: research and measured results; README covers usage and prerequisites.

## Keep context small
- Start with `git status --short` and targeted `rg` searches in source/tests.
- Read relevant functions or bounded file sections before entire large modules.
- Summarize test/build outcomes; inspect focused log excerpts when checks fail.
- Normally skip `.tmp/`, `diagnostics/`, `build/`, `dist/`, caches and dependencies.
- These exclusions guide exploration; ignored files remain readable when needed.
- Do not recursively inventory generated files or dump raw traces into the chat.
- Root `diagnostics/` contains historical evidence referenced by reports; preserve it.
- Preserve `.tmp/native-build`: source runs and packaging need its native helper.
- Preserve native dependency notices and historical experiments unless cleanup is requested.
- Use tracked `tools.gui_support` helpers for new GUI checks, not old `.tmp` scripts.

## Validation
- Unit tests: `python -m unittest discover -s tests` (no TV or Tk window required).
- Offline real-GUI check: `python -m tools.smoke_check` (discovery mocked).
- Live check: `python -m tools.smoke_check --live-tv --tv-uuid UUID`.
- Tray checks: `python -m tools.tray_check` (same live-TV opt-in flags).
- If a change could affect live-TV behavior, attempt real receiver discovery and appropriate Live-TV validation. This is standing authorization to use the live-TV test flags with an available, idle receiver; do not request confirmation again.
- If an attempted live check finds no available TV, explicitly report that limitation. Distinguish no discovered TV, unreachable or busy receivers, and discovery/network/sandbox failures. If live testing is not needed for the change, do not mention it in the report.
- Live checks use Display 1 and video only; require an available, idle receiver.
- Test tools isolate preferences; never overwrite the user's saved settings.
- Keep all Tk operations on the main thread and wait for asynchronous shutdown.
- GUI and packaging may require execution outside the sandbox for Tcl/Tk access.
- Build: `powershell -ExecutionPolicy Bypass -File .\build.ps1`.
- Existing output is `dist/CastToTV.exe`; do not create alternate builds/folders.
- If rebuilding fails, report the cause, fix recoverable issues, and retry within the existing build directories. Request user input only when a required action needs it.

## Delivery
- Read `ENHANCEMENTS.md` for the feature selection and acceptance workflow.
- Work on one selected feature or agreed maintenance task at a time.
- Preserve both casting modes, saved preferences, and device identity behavior.
- Report tests, GUI/live-TV limitations, and build outcome before awaiting acceptance.
- Commit only after explicit acceptance; keep unrelated changes out of the commit.
- Remove completed enhancement entries after acceptance; leave remaining IDs unchanged.
- End the session after acceptance and commit; wait for the next selection.
