"""Run with python -m tools.smoke_check; TV access requires explicit CLI flags."""
from __future__ import annotations

import argparse
import json
import logging
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import pychromecast
from pychromecast.controllers.media import APP_MEDIA_RECEIVER
from pychromecast.dial import get_device_info
from pychromecast.models import HostServiceInfo

from cast_to_tv.native_probe import APP_ID
from cast_to_tv.quality import VideoQuality
from cast_to_tv.settings import load_settings, save_settings, settings_path
from tools.gui_support import GuiHarness, isolated_settings, select


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_diagnostics(app):
    app.diagnostics_button.invoke()
    app.root.update()
    dialog = app._diagnostics_dialog
    require(dialog.window.winfo_viewable(), "Diagnostics did not open")
    require(dialog.window.grab_current() is None, "Diagnostics blocked the main window")
    require(str(dialog.details.cget("state")) == "disabled", "Diagnostic report is editable")
    dialog.select_all()
    require(dialog.details.tag_ranges("sel"), "Diagnostic text cannot be selected")
    dialog.copy_button.invoke()
    require(app.root.clipboard_get() == dialog.report, "Copy details did not copy the current report")
    require(dialog.status.get() == "Details copied.", "Copy success feedback missing")
    app.diagnostics_button.invoke()
    require(app._diagnostics_dialog is dialog, "Repeated Diagnostics created a duplicate window")
    return dialog


def check_diagnostics_failures(gui, report):
    """Exercise real Tk controls using isolated logs and expected error dialogs."""
    app = gui.app
    with tempfile.TemporaryDirectory(prefix="cast-to-tv-log-check-") as directory:
        path = Path(directory) / "check.log"
        with patch("cast_to_tv.app.app_log_path", return_value=path):
            dialog = check_diagnostics(app)
            require("No log file" in dialog.report, "Missing log was not explained")
            dialog.open_button.invoke()
            require("No log file" in dialog.status.get(), "Missing log action was not handled")
            path.write_text("Isolated GUI diagnostic log\n", encoding="utf-8")
            dialog.refresh_button.invoke()
            require("Isolated GUI diagnostic log" in dialog.report, "Refresh did not update the log excerpt")
            with patch("cast_to_tv.diagnostics_window.os.startfile") as startfile:
                dialog.open_button.invoke()
                startfile.assert_called_once_with(path)
                startfile.side_effect = OSError("Simulated viewer failure")
                dialog.open_button.invoke()
                require("Could not open the log" in dialog.status.get(), "Viewer failure was not handled")
            with patch.object(dialog.window, "clipboard_append", side_effect=tk.TclError("busy")):
                dialog.copy_button.invoke()
                require("Could not copy" in dialog.status.get(), "Clipboard failure was not handled")
            # Expected errors still use the normal application event paths;
            # suppress only their modal notifications during automation.
            with patch("cast_to_tv.app.messagebox.showerror"):
                for event, message in (("scan_error", "Simulated discovery failure"),
                                       ("error", "Simulated casting failure")):
                    app.events.put((event, message))
                    gui.wait(lambda: message in (app._last_problem or ""), event)
                    dialog.copy_button.invoke()
                    require(message in app.root.clipboard_get(), "Latest error missing from copied diagnostics")
            dialog.window.focus_force()
            dialog.window.event_generate("<Escape>")
            app.root.update()
            require(not dialog.window.winfo_exists(), "Escape did not dismiss Diagnostics")
            reopened = check_diagnostics(app)
            require(reopened is not dialog, "Closed Diagnostics window was not recreated")
            reopened.close_button.invoke()
    report["checks"].append("Diagnostics selection/copy, Refresh, missing log, viewer dispatch/failure, clipboard failure, discovery/casting errors and Escape/reopen")


def offline_check(report: dict) -> None:
    identity = UUID("11111111-2222-4333-8444-555555555555")
    device = Mock(cast_info=SimpleNamespace(uuid=identity, friendly_name="Smoke-check TV", host="192.0.2.1"))
    device.socket_client.ident = None
    device.socket_client.is_alive.return_value = False
    with GuiHarness(devices=[device], visible=True) as gui:
        app = gui.app
        require(app.root.winfo_viewable(), "Main window did not become visible")
        require(app.displays, "No displays available")
        require(app.tv_box.current() == -1, "Isolated settings unexpectedly selected a TV")
        require(app.display_box.current() == -1, "Isolated settings unexpectedly selected a display")
        select(app, app.tv_box, 0)
        select(app, app.display_box, 0)
        require(str(app.start_button.cget("state")) == "normal", "Selections did not enable Start")
        require(not app.include_audio.get(), "Video-only setting was not loaded")
        require(app.resolution_choice.get() == "1080p" and app.frame_rate_choice.get() == "30",
                "Default quality changed")
        app.about_button.invoke()
        app.root.update()
        require(app._about_dialog.window.winfo_viewable(), "About did not open")
        require([name for name, _ in app._about_dialog.info.fields()] == ["Version", "Build date"],
                "Unexpected About fields")
        about = app._about_dialog
        about.licenses_button.invoke()
        app.root.update()
        require(about.licenses_window.winfo_viewable(), "Third-party licenses did not open")
        require(about.licenses_window.grab_current() is None, "Licenses blocked the main window")
        require(str(about.licenses_text.cget("state")) == "disabled", "Notices are editable")
        require("pystray" in about.licenses_text.get("1.0", "end"), "Notices missing pystray")
        about.licenses_text.tag_add("sel", "1.0", "1.10")
        about.licenses_text.event_generate("<<Copy>>")
        app.root.update()
        require(bool(app.root.clipboard_get()), "License text could not be copied")
        about.licenses_text.yview_moveto(1.0)
        require(about.licenses_text.yview()[0] > 0, "Notices did not scroll")
        about.licenses_window.event_generate("<Escape>")
        app.root.update()
        require(not about.licenses_window.winfo_exists(), "Escape did not close licenses")
        require(about.window.winfo_exists(), "Escape closed About too")
        about.licenses_button.invoke()
        app.root.update()
        license_window = about.licenses_window
        app._about_dialog.close_button.invoke()
        require(not license_window.winfo_exists(), "License window survived About closing")
        report["checks"].append("Offline license viewer, selection/copy, scrolling, Escape/reopen and parent cleanup")
        check_diagnostics_failures(gui, report)
        check_session_settings(gui, report)
        saved = load_settings()
        report["checks"].append("Visible GUI, selectors, saved selections, video-only preference and About")
    with GuiHarness(devices=[device], visible=True) as gui:
        require(gui.app.tv_box.current() == 0, "TV selection did not restore")
        require(gui.app.resolution_choice.get() == saved["resolution"] == "720p",
                "Resolution did not restore")
        require(gui.app.frame_rate_choice.get() == str(saved["frame_rate"]) == "15",
                "Frame rate did not restore")
        # A display without a unique identity deliberately requires manual selection.
        if saved["display_id"] is not None:
            require(gui.app.display_box.current() == 0, "Display selection did not restore")
        check_diagnostics(gui.app)
        report["checks"].append("Second GUI launch restored identifiable selections and closed cleanly")
    require(gui.app._diagnostics_dialog is None, "App shutdown retained the diagnostics window")
    device.wait.assert_not_called()
    device.quit_app.assert_not_called()
    check_monitor_changes(device, report)


def check_monitor_changes(device, report):
    """Drive actual Tk timers/selectors with monitor snapshots, not OS changes."""
    from cast_to_tv.capture.displays import list_displays

    physical = list_displays()
    require(physical, "No physical display for monitor GUI checks")
    first = dict(physical[0], unique_id="e10-monitor-a")
    second = dict(first, unique_id="e10-monitor-b", left=first["left"] + first["width"])
    snapshot = [first]
    with isolated_settings(), patch("cast_to_tv.app.list_displays", side_effect=lambda: list(snapshot)), \
            GuiHarness(devices=[device], visible=True) as gui:
        app = gui.app
        select(app, app.tv_box, 0)
        select(app, app.display_box, 0)
        saved = load_settings()
        snapshot = [second, first]
        gui.wait(lambda: len(app.displays) == 2, "automatic monitor connect")
        require(app.display_box.current() == 1, "Reordered selection lost its identity")
        app.identify_button.invoke()
        require(len(app._display_identifiers._windows) == 2, "Identify missed a connected display")
        snapshot = [second]
        gui.wait(lambda: len(app.displays) == 1, "automatic monitor disconnect")
        require(not app._display_identifiers._windows, "Stale Identify badges survived a topology change")
        require(app.display_box.current() == -1 and app.start_button.instate(["disabled"]),
                "Disconnected selection still enables Start")
        snapshot = []
        gui.wait(lambda: not app.displays, "all monitors disconnected")
        require(app.identify_button.instate(["disabled"]), "Identify enabled without monitors")
        snapshot = [first]
        gui.wait(lambda: app.display_box.current() == 0, "saved monitor reconnect")
        require(app.start_button.instate(["!disabled"]) and app.session is None,
                "Reconnect did not require an explicit Start")
        require(load_settings() == saved, "Topology changes overwrote saved preferences")

        for mode, session_class in (("Compatibility", "CastSession"),
                                    ("Low delay (experimental)", "NativeCastSession")):
            with patch(f"cast_to_tv.app.{session_class}.start"), \
                    patch(f"cast_to_tv.app.{session_class}.stop", return_value=[]), \
                    patch.object(app._screen_awake, "set_enabled"):
                app.mode_choice.set(mode)
                app.start_button.invoke()
                gui.wait(lambda: not app.busy, f"{mode} mocked startup")
                session = app.session
                require(session is not None, "Mocked capture did not start")
                snapshot = [second, first]
                gui.wait(lambda: app.display_box.current() == 1, f"{mode} active reorder")
                require(app.session is session and "Display 2" in app.status.get(),
                        "Active reorder restarted capture or left the wrong display number")
                snapshot = [second]
                gui.wait(lambda: app.session is None and not app.busy, f"{mode} disconnect cleanup")
                require("cast display disconnected" in app.status.get(), "Monitor-stop explanation missing")
                snapshot = [first]
                gui.wait(lambda: app.display_box.current() == 0, f"{mode} reconnect")
                require(app.session is None, "Reconnect restarted capture automatically")
                app.start_button.invoke()
                gui.wait(lambda: not app.busy, f"{mode} explicit restart")
                require(app.session is not None, "Explicit restart failed")
                snapshot = [dict(first, width=first["width"] - 1)]
                gui.wait(lambda: app.session is None and not app.busy, f"{mode} capture geometry change")
                snapshot = [first]
                gui.wait(lambda: app.displays == snapshot, f"{mode} geometry restored")
        require(load_settings() == saved, "Monitor interruption changed saved preferences")
    require(app._display_timer is None, "Monitor timer survived asynchronous shutdown")
    report["checks"].append("Actual Tk monitor polling: connect/disconnect/all-disconnected/reconnect, identity reorder, Identify badge cleanup; both mocked casting modes stop on monitor loss/geometry changes, require explicit restart and preserve preferences")


def check_session_settings(gui, report):
    """Check actual disabled clicks while session workers wait for release."""
    app = gui.app
    checkboxes = (app.measure_delay_checkbox, app.include_audio_checkbox)
    screen_on = next(widget for widget in checkboxes[0].master.winfo_children()
                     if widget.winfo_class() == "TCheckbutton"
                     and widget.cget("text") == "Keep PC screen on while casting")

    def check_locked():
        saved = load_settings()
        values = (app.measure_delay.get(), app.include_audio.get())
        quality = app._selected_quality()
        for box in (app.resolution_box, app.frame_rate_box):
            require(box.instate(["disabled"]), "Quality setting is still enabled")
            box.event_generate("<Button-1>", x=box.winfo_width() - 5, y=5)
            box.event_generate("<ButtonRelease-1>", x=box.winfo_width() - 5, y=5)
        app.root.update()
        require(app._selected_quality() == quality, "Disabled click changed video quality")
        for checkbox in checkboxes:
            require(checkbox.instate(["disabled"]), "Session setting is still enabled")
            checkbox.invoke()
        require(values == (app.measure_delay.get(), app.include_audio.get()), "Disabled click changed an option")
        require(load_settings() == saved, "Disabled click changed saved preferences")

    def wait_for_release(gate):
        require(gate.wait(15), "Timed out waiting for mocked worker release")
        return []

    combinations = [(mode, session_class, resolution, rate)
                    for mode, session_class in (("Compatibility", "CastSession"),
                                                ("Low delay (experimental)", "NativeCastSession"))
                    for resolution in ("1080p", "720p") for rate in (30, 15)]
    for mode, session_class, resolution, rate in combinations:
        measure_delay = resolution == "720p" and rate == 15
        if app.measure_delay.get() != measure_delay:
            app.measure_delay_checkbox.invoke()
        before = load_settings()
        select(app, app.resolution_box, 0 if resolution == "720p" else 1)
        require(load_settings() == {**before, "resolution": resolution}, "Resolution save changed another preference")
        before = load_settings()
        select(app, app.frame_rate_box, 0 if rate == 15 else 1)
        require(load_settings() == {**before, "frame_rate": rate}, "Frame-rate save changed another preference")
        start_gate, stop_gate = threading.Event(), threading.Event()
        with patch(f"cast_to_tv.app.{session_class}.start", side_effect=lambda: wait_for_release(start_gate)), \
                patch(f"cast_to_tv.app.{session_class}.stop", side_effect=lambda: wait_for_release(stop_gate)), \
                patch.object(app._screen_awake, "set_enabled") as power, \
                patch("cast_to_tv.diagnostics.SessionDiagnostics.__init__",
                      side_effect=AssertionError("GUI delay measurement started a trace recorder")):
            try:
                app.mode_choice.set(mode)
                app.start_button.invoke()
                require(app.session is not None, "Session did not start")
                require(app.session.diagnostics is None, "GUI session enabled trace recording")
                marker = app._diagnostic_marker
                require(bool(marker) == measure_delay, "Delay clock does not match the checkbox")
                if marker is not None:
                    require(marker.diagnostics is None, "Delay clock enabled trace recording")
                    require(marker.window.winfo_exists(), "Delay clock did not open")
                require(app.session.quality == VideoQuality(resolution, rate), "Session used the wrong quality")
                check_locked()
                start_gate.set()
                gui.wait(lambda: not app.busy, "mocked session startup")
                check_locked()
                require(not screen_on.instate(["disabled"]), "Live screen-on setting is disabled")
                for enabled in (True, False):
                    screen_on.invoke()
                    require(app.keep_screen_on.get() == enabled, "Screen-on checkbox did not toggle")
                    power.assert_called_with(enabled)
                app.stop_button.invoke()
                check_locked()
                stop_gate.set()
                gui.wait(lambda: app.session is None, "mocked session cleanup")
                require(app._diagnostic_marker is None, "Delay clock survived Stop")
                if marker is not None:
                    require(not marker.window.winfo_exists(), "Delay clock window survived Stop")
                require(app.status.get() == "Mirroring stopped.", "Stop still advertises saved measurements")
                require(all(not checkbox.instate(["disabled"]) for checkbox in checkboxes),
                        "Session settings did not re-enable after cleanup")
                require(all(box.instate(["readonly", "!disabled"])
                            for box in (app.resolution_box, app.frame_rate_box)),
                        "Quality settings did not re-enable after cleanup")
            finally:
                start_gate.set()
                stop_gate.set()
    report["checks"].append("Both modes, all four resolution/fps combinations: independent saves and session options; quality/audio/delay settings locked through startup, mirroring and cleanup; disabled clicks preserve preferences; screen-on toggle applies immediately")
    report["checks"].append("Delay clock opens and closes in both modes without trace recording or a saved-measurements message")


def verify_receiver_released(info, app_id=APP_ID) -> None:
    device_info = get_device_info(info.host, timeout=3)
    require(device_info is not None and device_info.uuid == info.uuid, "Could not verify receiver identity after Stop")
    observer = pychromecast.get_chromecast_from_cast_info(
        replace(info, services={HostServiceInfo(info.host, info.port)}), None)
    try:
        observer.wait(timeout=10)
        require(observer.app_id != app_id, "Mirroring receiver is still active after Stop")
    finally:
        observer.disconnect(timeout=5)


def live_check(tv_uuid: str, report: dict) -> None:
    with GuiHarness(live=True, visible=True) as gui:
        app = gui.app
        index = next((i for i, device in enumerate(app.devices)
                      if str(device.cast_info.uuid) == tv_uuid), None)
        if index is None:
            report["result"] = "unverified"
            report["reason"] = "Requested TV was not discovered; no casting attempted"
            return
        require(app.displays, "Display 1 is unavailable")
        select(app, app.tv_box, index)
        select(app, app.display_box, 0)
        app.mode_choice.set("Low delay (experimental)")
        require(app.include_audio.get() is False, "Refusing live check with system audio enabled")
        target = app.devices[index]
        target.wait(timeout=10)
        require(target.status is not None, "Receiver status is unavailable; refusing to start")
        if target.app_id:
            report["result"] = "unverified"
            report["reason"] = "Receiver already has an active application; preserved existing playback"
            return
        info = target.cast_info
        app.start_button.invoke()
        dialog = check_diagnostics(app)
        require("Starting desktop capture" in dialog.report, "Connecting status missing from diagnostics")
        gui.wait(lambda: not app.busy, "native startup and receiver packet feedback")
        session = app.session
        require(session is not None and session._started and session._ready.is_set(), "Native startup did not complete")
        dialog = check_diagnostics(app)
        require("System audio: Off" in dialog.report and "Display 1" in dialog.report,
                "Active stream settings missing from diagnostics")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.root.update()
            gui.check()
            require(app.session is session and session._error is None, "Stream failed during smoke check")
            time.sleep(0.05)
        app.stop_button.invoke()
        gui.wait(lambda: not app.busy, "Stop cleanup")
        require(app.session is None, "Stop retained the session")
        require(session._worker is None or not session._worker.is_alive(), "Native worker is still running")
        require(session._temporary is None, "Native temporary resources were not released")
        require(not target.socket_client.is_alive(), "Cast control connection was not released")
        verify_receiver_released(info)
        check_diagnostics(app)
        report["checks"].append("Display 1 native video packet feedback, five-second stream, Stop and receiver release")
        report["checks"].append("Nonmodal diagnostics and clipboard access during connecting, casting and after Stop")
        check_live_monitor_changes(gui, report)
        report["limitations"] = "Visible picture quality and physical latency require user observation"
    require(gui.app._diagnostics_dialog is None, "App shutdown retained the diagnostics window")


def check_live_monitor_changes(gui, report):
    """Exercise monitor-loss cleanup against an idle TV without changing Windows."""
    app = gui.app
    original = list(app.displays)
    saved = load_settings()
    for mode, app_id in (("Low delay (experimental)", APP_ID), ("Compatibility", APP_MEDIA_RECEIVER)):
        info = app.devices[app.tv_box.current()].cast_info
        observer = pychromecast.get_chromecast_from_cast_info(
            replace(info, services={HostServiceInfo(info.host, info.port)}), None)
        try:
            observer.wait(timeout=10)
            require(observer.status is not None, "Receiver status unavailable before monitor-loss check")
            if observer.app_id:
                report["result"] = "unverified"
                report["reason"] = "Receiver became busy; preserved playback and skipped monitor-loss checks"
                return
        finally:
            observer.disconnect(timeout=5)
        app.mode_choice.set(mode)
        app.start_button.invoke()
        gui.wait(lambda: not app.busy, f"{mode} live monitor-loss startup")
        session = app.session
        require(session is not None, f"{mode} live capture did not start")
        target = session.cast_device
        # Give the real capture and receiver time to deliver video before loss.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.root.update()
            gui.check()
            require(app.session is session and not app.busy, f"{mode} stream interrupted before monitor loss")
            time.sleep(0.05)
        with patch("cast_to_tv.app.list_displays", return_value=original[1:]):
            gui.wait(lambda: app.session is None and not app.busy, f"{mode} automatic monitor-loss cleanup")
            require(app.display_box.current() == -1, "Lost monitor silently selected another display")
            require("cast display disconnected" in app.status.get(), "Monitor-loss explanation missing")
        if mode.startswith("Low delay"):
            require(session._worker is None or not session._worker.is_alive(), "Native worker survived monitor loss")
            require(session._temporary is None, "Native resources survived monitor loss")
        else:
            require((session.process is None or session.process.poll() is not None)
                    and session.server is None and session.temp_dir is None,
                    "Compatibility resources survived monitor loss")
        require(not target.socket_client.is_alive(), "TV control connection survived monitor loss")
        verify_receiver_released(info, app_id)
        gui.wait(lambda: app.display_box.current() == 0, f"{mode} saved display restoration")
        require(app.session is None, "Monitor restoration restarted casting automatically")
        require(load_settings() == saved, "Live monitor interruption changed preferences")
        report["checks"].append(f"Display 1 {mode}: real video-only stream, simulated monitor loss through automatic polling, capture/control cleanup, receiver release and reconnect without automatic restart")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-tv", action="store_true", help="Opt in to discovery and real video casting")
    parser.add_argument("--tv-uuid", type=lambda value: str(UUID(value)), help="Exact receiver UUID; required with --live-tv")
    args = parser.parse_args(argv)
    if args.live_tv != bool(args.tv_uuid):
        parser.error("Use --live-tv and --tv-uuid UUID together")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    original_path = settings_path()
    original_bytes = original_path.read_bytes() if original_path.exists() else None
    report = {"result": "passed", "mode": "live-tv" if args.live_tv else "offline-gui", "checks": []}
    try:
        with isolated_settings():
            save_settings({"include_audio": False, "measure_delay": False, "keep_screen_on": False})
            if args.live_tv:
                live_check(args.tv_uuid, report)
            else:
                offline_check(report)
    except (Exception, KeyboardInterrupt) as exc:
        report["result"] = "failed"
        report["error"] = repr(exc)
        logging.exception("Smoke check failed")
    finally:
        current_bytes = original_path.read_bytes() if original_path.exists() else None
        report["user_settings_unchanged"] = current_bytes == original_bytes
        if not report["user_settings_unchanged"]:
            report["result"] = "failed"
            report["settings_error"] = "Original settings file changed during the check"
        path = Path(__file__).resolve().parents[1] / ".tmp" / "smoke-check.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return {"passed": 0, "failed": 1, "unverified": 2}[report["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
