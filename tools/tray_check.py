"""Real Windows tray checks; use --live-tv --tv-uuid UUID to opt in to casting."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from cast_to_tv.settings import load_settings, save_settings, settings_path
from tools.gui_support import GuiHarness, isolated_settings, select
from tools.smoke_check import parse_args, require, verify_receiver_released


def tray_action(gui, label):
    """Run the actual menu callback on a worker, as the tray backend does."""
    tray = gui.app._tray
    item = next(item for item in tray._icon.menu.items if item.text == label)
    require(item.enabled, f"Tray action {label} is disabled")
    errors = []

    def invoke():
        try:
            item(tray._icon)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=invoke)
    worker.start()
    worker.join(timeout=1)
    require(not worker.is_alive() and not errors, f"Tray callback failed: {errors}")


def hide(gui):
    require(gui.app.close_to_tray.get(), "Close to system tray must be enabled before using X to hide")
    gui.app.root.tk.call(gui.app.root.protocol("WM_DELETE_WINDOW"))
    gui.wait(lambda: gui.app._hidden_to_tray, "hide after tray readiness", 8)
    require(gui.app.root.state() == "withdrawn", "Main window remains visible")


def restore(gui):
    tray_action(gui, "Show")
    gui.wait(lambda: not gui.app._hidden_to_tray and gui.app.root.winfo_viewable(), "tray Show")


def offline(report):
    device = Mock(cast_info=SimpleNamespace(uuid=UUID("11111111-2222-4333-8444-555555555555"),
                                          friendly_name="Tray check TV", host="192.0.2.1"))
    device.socket_client.is_alive.return_value = False
    with GuiHarness(devices=[device], visible=True) as gui:
        app = gui.app
        require(app.close_to_tray_checkbox.winfo_viewable(), "Close to system tray checkbox is not visible")
        require(not app.close_to_tray.get(), "Default X behavior changed")
        original = load_settings()
        app.close_to_tray_checkbox.invoke()
        require(load_settings() == {**original, "close_to_tray": True}, "Checkbox did not save independently")
        app.show_about()
        app.show_diagnostics()
        hide(gui)
        tray = app._tray
        require(app._about_dialog is None and app._diagnostics_dialog is None, "Utility windows survived hiding")
        require(not app._closing, "Hiding began shutdown")
        require(not next(item for item in tray._icon.menu.items if item.text == "Stop casting").enabled,
                "Stop should be disabled while idle")
        # Send an actual Windows primary-click message to the real backend.
        from pystray._util import win32
        win32.PostMessage(tray._icon._hwnd, win32.WM_NOTIFY, 0, win32.WM_LBUTTONUP)
        gui.wait(lambda: not app._hidden_to_tray, "native primary-click Show")
        app.root.tk.call(app.root.protocol("WM_DELETE_WINDOW"))
        gui.wait(lambda: app._hidden_to_tray, "X hides when enabled")
        require(app._tray is tray, "Repeated hiding created another tray icon")
        restore(gui)
        app.root.iconify()
        app.root.update()
        require(not app._hidden_to_tray and not app._closing, "Normal minimize behavior changed")
        app.show_window()
        hide(gui)
        with patch("cast_to_tv.app.messagebox.showwarning"):
            app._show_warning("Simulated hidden error", "Window must restore")
        require(not app._hidden_to_tray and app.root.winfo_viewable(), "Errors left the window hidden")
        hide(gui)
        tray_action(gui, "Exit")
        gui.wait(lambda: app._closed, "tray Exit cleanup", 60)
        require(not tray._thread.is_alive(), "Tray backend survived Exit")
    with GuiHarness(devices=[device], visible=True) as gui:
        app = gui.app
        require(app.close_to_tray.get(), "Close to system tray did not restore across launches")
        app.close_to_tray_checkbox.invoke()
        app.root.tk.call(app.root.protocol("WM_DELETE_WINDOW"))
        gui.wait(lambda: app._closed, "normal X exits when unchecked", 60)
        require(app._tray is None, "Normal X created a tray icon")
    with GuiHarness(devices=[device], visible=True) as gui:
        app = gui.app
        with patch("cast_to_tv.app.TrayIcon", side_effect=OSError("Simulated shell failure")), \
                patch("cast_to_tv.app.messagebox.showwarning"):
            app.close_to_tray_checkbox.invoke()
            app.root.tk.call(app.root.protocol("WM_DELETE_WINDOW"))
        require(not app._hidden_to_tray and not app._closing and app.root.winfo_viewable(),
                "Tray setup failure made the window inaccessible")
        app.close_to_tray_checkbox.invoke()
    report["checks"].append("Real tray icon, native primary-click Show, repeated hide/show, X checkbox persistence, normal minimize/X, hidden-error restore, setup failure and tray Exit")


def live(report, identity):
    with GuiHarness(live=True, visible=True) as gui:
        app = gui.app
        index = next((i for i, device in enumerate(app.devices) if str(device.cast_info.uuid) == identity), None)
        if index is None:
            report.update(result="unverified", reason="Requested TV was not discovered")
            return
        require(app.displays, "Display 1 is unavailable")
        select(app, app.tv_box, index)
        select(app, app.display_box, 0)
        device = app.devices[index]
        device.wait(timeout=10)
        require(device.status is not None, "Receiver status unavailable")
        if device.app_id:
            report.update(result="unverified", reason="Receiver in use; preserved existing playback")
            return
        info = device.cast_info
        app.close_to_tray_checkbox.invoke()
        hide(gui)
        tray = app._tray
        restore(gui)
        app.start_button.invoke()
        hide(gui)
        require(app.busy, "Startup completed before cancellation check")
        # Allow the GUI poll to publish the current Stop state to the tray.
        gui.wait(lambda: tray._can_stop, "tray Stop enabled while connecting")
        tray_action(gui, "Stop casting")
        gui.wait(lambda: not app.busy and app.session is None, "tray connecting cancellation", 60)
        verify_receiver_released(info)
        restore(gui)
        app.start_button.invoke()
        gui.wait(lambda: not app.busy, "video packet feedback")
        require(app.session is not None, "Cast did not start")
        session = app.session
        hide(gui)
        require(app._screen_awake._enabled, "Hiding released the active screen-awake request")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.root.update()
            gui.check()
            require(app.session is session and session._error is None, "Hidden cast failed")
            time.sleep(0.05)
        tray_action(gui, "Stop casting")
        gui.wait(lambda: not app.busy and app.session is None, "tray Stop cleanup", 60)
        require(not app._screen_awake._enabled, "Stop retained the screen-awake request")
        require(session._worker is None or not session._worker.is_alive(), "Stop retained native worker")
        verify_receiver_released(info)
        restore(gui)
        app.start_button.invoke()
        hide(gui)
        require(app.busy, "Startup completed before Exit check")
        tray_action(gui, "Exit")
        gui.wait(lambda: app._closed, "tray Exit during startup", 60)
        require(not tray._thread.is_alive(), "Exit retained the tray backend")
        require(app.session is None and not app._screen_awake._enabled, "Exit retained casting resources")
        verify_receiver_released(info)
    report["checks"].append("Connecting cancellation, five-second hidden native video, screen-awake preservation, tray Stop and Exit during startup, receiver and tray cleanup")
    report["limitations"] = "Visible picture quality and physical latency require user observation"


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    original_path = settings_path()
    before = original_path.read_bytes() if original_path.exists() else None
    report = {"result": "passed", "mode": "live-tv" if args.live_tv else "offline-gui", "checks": []}
    try:
        with isolated_settings():
            save_settings({"include_audio": False, "measure_delay": False,
                           "keep_screen_on": args.live_tv, "close_to_tray": False})
            if args.live_tv:
                live(report, args.tv_uuid)
            else:
                offline(report)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(result="failed", error=repr(exc))
        logging.exception("Tray check failed")
    finally:
        after = original_path.read_bytes() if original_path.exists() else None
        report["user_settings_unchanged"] = after == before
        if after != before:
            report.update(result="failed", settings_error="Original settings changed during the check")
        path = Path(__file__).resolve().parents[1] / ".tmp" / "tray-check.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return {"passed": 0, "failed": 1, "unverified": 2}[report["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
