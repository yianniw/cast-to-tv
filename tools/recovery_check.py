"""Exercise E09 in real Tk; live receiver interruptions require explicit flags."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from cast_to_tv.recovery import RecoveryError, failure_message
from cast_to_tv.receivers.discovery import wait_for_cast
from cast_to_tv.settings import load_settings, save_settings, settings_path
from tools.gui_support import GuiHarness, isolated_settings, select
from tools.smoke_check import parse_args, require, verify_receiver_released


class CheckSession:
    """Controlled workers using the real app lifecycle and real Tk controls."""
    instances = []
    stop_gate = None
    start_gate = None

    def __init__(self, device, display, on_failure, diagnostics=None,
                 include_audio=True, silent_audio=False, quality=None):
        self.cast_device = device
        self.display = display
        self.on_failure = on_failure
        self.diagnostics = diagnostics
        self.include_audio = include_audio and not silent_audio
        self.quality = quality
        self.cancel = threading.Event()
        self.instances.append(self)

    def start(self):
        gate = type(self).start_gate
        while gate is not None and not gate.wait(.05):
            if self.cancel.is_set():
                return

    def stop(self):
        gate = type(self).stop_gate
        if gate is not None and not gate.wait(10):
            return ["Check cleanup gate timed out"]
        return []

    def cancel_start(self):
        self.cancel.set()


def interrupt(session, kind="audio"):
    session.on_failure(failure_message(RecoveryError("Controlled " + kind + " interruption", kind)))


class CheckNativeSession(CheckSession):
    pass


class CheckCompatibilitySession(CheckSession):
    pass


def offline_check(report):
    device = Mock(cast_info=SimpleNamespace(uuid=UUID("11111111-2222-4333-8444-555555555555"),
                                          friendly_name="Recovery-check TV", host="192.0.2.1"))
    device.socket_client.ident = None
    device.socket_client.is_alive.return_value = False
    with patch("cast_to_tv.app.NativeCastSession", CheckNativeSession), \
            patch("cast_to_tv.app.CastSession", CheckCompatibilitySession):
        for mode in ("Low delay (experimental)", "Compatibility"):
            with GuiHarness(devices=[device], visible=True) as gui:
                app = gui.app
                select(app, app.tv_box, 0)
                select(app, app.display_box, 0)
                app.mode_choice.set(mode)
                app.include_audio.set(True)
                save_settings({"include_audio": True})
                saved = load_settings()
                app.start_button.invoke()
                gui.wait(lambda: not app.busy, "initial start")
                first = app.session
                interrupt(first)
                gui.wait(lambda: app.session is not first and not app.busy, "automatic recovery")
                second = app.session
                require(second is not None, "Automatic restart lost the session")
                interrupt(second)
                gui.wait(lambda: app._recovery_request is not None, "recovery controls")
                require(app.retry_button.winfo_viewable(), "Retry is hidden")
                require(app.video_only_button.winfo_viewable(), "Audio fallback is hidden")
                app.video_only_button.invoke()
                gui.wait(lambda: not app.busy, "video-only restart")
                require(app.session is not None and not app._active_request.include_audio, "Fallback retained audio")
                require(app.include_audio.get() and load_settings() == saved, "Fallback changed preferences")
                app.stop_button.invoke()
                gui.wait(lambda: app.session is None and not app.busy, "Stop after fallback")

                # Hold cleanup so the real Stop button can cancel the queued recovery.
                app.start_button.invoke()
                gui.wait(lambda: not app.busy, "second start")
                gate = threading.Event()
                CheckSession.stop_gate = gate
                try:
                    old = app.session
                    interrupt(old, "connection")
                    gui.wait(lambda: app._recovery_cleanup, "recovery cleanup")
                    require(not app.stop_button.instate(["disabled"]), "Stop disabled during recovery cleanup")
                    app.stop_button.invoke()
                    gate.set()
                    gui.wait(lambda: app.session is None and not app.busy, "cancelled recovery cleanup")
                    require(app._recovery_request is None, "Stop left a queued retry")
                finally:
                    gate.set()
                    CheckSession.stop_gate = None

                app.start_button.invoke()
                gui.wait(lambda: not app.busy, "start before reconnect cancellation")
                gate = threading.Event()
                CheckSession.start_gate = gate
                try:
                    old = app.session
                    interrupt(old, "connection")
                    gui.wait(lambda: app.session is not old and app.busy, "automatic reconnect")
                    app.stop_button.invoke()
                    gate.set()
                    gui.wait(lambda: app.session is None and not app.busy, "Stop during reconnect")
                    require(app._recovery_request is None, "Stop during reconnect left a queued retry")
                finally:
                    gate.set()
                    CheckSession.start_gate = None

                # A real Close during asynchronous cleanup must never restart.
                app.start_button.invoke()
                gui.wait(lambda: not app.busy, "third start")
                gate = threading.Event()
                CheckSession.stop_gate = gate
                try:
                    interrupt(app.session)
                    gui.wait(lambda: app._recovery_cleanup, "cleanup before Close")
                    app.close()
                    gate.set()
                    gui.wait(lambda: app._closed, "Close cancels recovery")
                finally:
                    gate.set()
                    CheckSession.stop_gate = None
            report["checks"].append(mode + ": automatic recovery, visible Retry/audio fallback, unchanged preferences, Stop during reconnect, Stop/Close during cleanup")


def live_check(tv_uuid, report):
    with GuiHarness(live=True, visible=True) as gui:
        app = gui.app
        index = next((i for i, device in enumerate(app.devices)
                      if str(device.cast_info.uuid) == tv_uuid), None)
        if index is None:
            report.update(result="unverified", reason="Requested TV was not discovered")
            return
        select(app, app.tv_box, index)
        select(app, app.display_box, 0)
        require(not app.include_audio.get(), "Live checks require video only")
        for mode in ("Low delay (experimental)", "Compatibility"):
            target = app.devices[index]
            if target.socket_client.ident is not None and not target.socket_client.is_alive():
                # _begin_cast recreates stopped sockets; use a fresh observer to verify idle first.
                import pychromecast
                target = pychromecast.get_chromecast_from_cast_info(target.cast_info, app.browser.zc)
                app.devices[index] = target
            wait_for_cast(target, threading.Event(), timeout=10)
            if target.app_id:
                report.update(result="unverified", reason="Receiver is busy; preserved existing playback")
                return
            info = target.cast_info
            app.mode_choice.set(mode)
            app.start_button.invoke()
            gui.wait(lambda: not app.busy, mode + " initial playback", 60)
            first = app.session
            require(first is not None, "Initial playback failed")
            # End the actual Cast socket; app detection must trigger the recovery.
            first.cast_device.socket_client.disconnect()
            gui.wait(lambda: app.session is not first and not app.busy, mode + " reconnect after control loss", 80)
            require(app.session is not None, "Automatic recovery did not resume playback: " + app.status.get())
            require(app._active_request.device.cast_info.uuid == info.uuid, "Recovery changed TV identity")
            require(app._automatic_recovery_used, "Control loss did not trigger automatic recovery")
            recovered = app.session
            recovered.cast_device.socket_client.disconnect()
            gui.wait(lambda: app._recovery_request is not None and not app.busy, mode + " bounded second interruption", 60)
            require(app.session is None, "Second interruption restarted without user input")
            app.retry_button.invoke()
            gui.wait(lambda: not app.busy, mode + " manual retry", 60)
            require(app.session is not None, "Manual retry did not resume playback")
            app.session.cast_device.socket_client.disconnect()
            gui.wait(lambda: app._recovery_request is not None and not app.busy, mode + " interruption before Stop", 60)
            app.stop_button.invoke()
            gui.wait(lambda: app.session is None and not app.busy, mode + " Stop", 45)
            verify_receiver_released(info)
            report["checks"].append(mode + ": Display 1/video only, actual control disconnect, automatic reconnect, second disconnect waits for Retry, manual retry, Stop while waiting for Retry/receiver release")
        report["limitations"] = "Physical audio-device removal and prolonged network outages require manual observation"


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    original = settings_path()
    before = original.read_bytes() if original.exists() else None
    report = {"result": "passed", "mode": "live-tv" if args.live_tv else "offline-gui", "checks": []}
    try:
        with isolated_settings():
            save_settings({"include_audio": False, "measure_delay": False, "keep_screen_on": False})
            if args.live_tv:
                live_check(args.tv_uuid, report)
            else:
                offline_check(report)
    except Exception as exc:
        report.update(result="failed", error=repr(exc))
        logging.exception("Recovery check failed")
    finally:
        report["user_settings_unchanged"] = (original.read_bytes() if original.exists() else None) == before
        if not report["user_settings_unchanged"]:
            report["result"] = "failed"
        path = Path(__file__).resolve().parents[1] / ".tmp" / ("recovery-live-check.json" if args.live_tv else "recovery-gui-check.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    return {"passed": 0, "failed": 1, "unverified": 2}[report["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
