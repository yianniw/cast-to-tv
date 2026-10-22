from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from tkinter import messagebox, ttk

import pychromecast
from pychromecast.discovery import CastBrowser

from .about import AboutDialog, get_app_info
from .casting import CastSession
from .capture.displays import list_displays
from .diagnostics import LatencyMarker
from .diagnostics_window import DiagnosticsDialog, app_log_path, build_report
from .display_identification import DisplayIdentifiers
from .displays import list_display_ids, remap_display_index, same_capture_area
from .errors import CastingCancelled, CastingError
from .native_casting import NativeCastSession
from .power import ScreenAwake
from .quality import DEFAULT_QUALITY, FRAME_RATES, RESOLUTIONS, VideoQuality
from .recovery import FailureMessage, ReceiverCleanupSession, ReceiverOwnership, failure_message
from .receivers.discovery import discover_cast_devices
from .settings import SettingValue, load_settings, save_settings
from .tray import TrayIcon
from .ui.main_window import build_main_window

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CastRequest:
    device: pychromecast.Chromecast
    display: dict
    display_index: int
    native: bool
    include_audio: bool
    measure_delay: bool
    quality: VideoQuality = DEFAULT_QUALITY
    recovery_receiver: ReceiverOwnership | None = None


class CastToTVApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Cast to TV")
        self.root.minsize(440, 200)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.devices: list[pychromecast.Chromecast] = []
        self.browser: CastBrowser | None = None
        self.session: CastSession | NativeCastSession | ReceiverCleanupSession | None = None
        self._active_request: CastRequest | None = None
        self._pending_switch: CastRequest | None = None
        self._recovery_request: CastRequest | None = None
        self._recovery_kind: str | None = None
        self._automatic_recovery_used = False
        self._recovering = False
        self._recovery_cleanup = False
        self._cancel_recovery = False
        self._stopping = False
        self._cancelling_start = False
        self._closing = False
        self._shutdown_started = False
        self._shutdown_thread: threading.Thread | None = None
        self._closed = False
        self.busy = False
        self._scan_failed = False
        self._diagnostic_marker: LatencyMarker | None = None
        self._display_identifiers = DisplayIdentifiers(root)
        self._display_timer: str | None = None
        self._display_refresh_error: str | None = None
        self._display_stop_reason: str | None = None
        self._about_dialog: AboutDialog | None = None
        self._diagnostics_dialog: DiagnosticsDialog | None = None
        self._last_problem: str | None = None
        self._tray: TrayIcon | None = None
        self._tray_ready = False
        self._tray_startup_timer: str | None = None
        self._hide_pending = False
        self._hidden_to_tray = False
        self._screen_awake = ScreenAwake()

        self.tv_choice = tk.StringVar()
        self.display_choice = tk.StringVar()
        self.status = tk.StringVar(value="Choose a display and scan for TVs.")
        settings = load_settings()
        self._saved_tv_uuid = settings["tv_uuid"]
        self._saved_display_id = settings["display_id"]
        self.measure_delay = tk.BooleanVar(value=settings["measure_delay"])
        self.mode_choice = tk.StringVar(value="Low delay (experimental)")
        self.resolution_choice = tk.StringVar(value=settings["resolution"])
        self.frame_rate_choice = tk.StringVar(value=str(settings["frame_rate"]))
        self.include_audio = tk.BooleanVar(value=settings["include_audio"])
        self.keep_screen_on = tk.BooleanVar(value=settings["keep_screen_on"])
        self.close_to_tray = tk.BooleanVar(value=settings["close_to_tray"])

        build_main_window(self)

        self.displays: list[dict] = []
        self._display_ids: list[str | None] = []
        self._refresh_displays()
        self._display_timer = self.root.after(1000, self._poll_displays)
        self.start_button.configure(state="disabled")

        self.root.protocol("WM_DELETE_WINDOW", self._window_close)
        self.root.after(100, self._process_events)
        self.scan_devices()

    def _window_close(self) -> None:
        if self.close_to_tray.get() and not self._closing:
            self.hide_to_tray()
        else:
            self.close()

    def show_window(self) -> None:
        if self._closed:
            return
        self._hide_pending = False
        self._hidden_to_tray = False
        self.root.deiconify()
        self.root.lift()

    def hide_to_tray(self) -> None:
        if self._closing:
            return
        if self._tray is not None and self._tray.failed:
            self._show_warning("Tray unavailable", "The window was kept open. Turn off Close to system tray to exit.")
            return
        self._hide_pending = True
        if self._tray_ready:
            self._withdraw_to_tray()
        elif self._tray is None:
            try:
                self._tray = TrayIcon(self.events.put)
                self._tray.start()
                self._tray_startup_timer = self.root.after(5000, self._check_tray_startup)
            except Exception as exc:
                if self._tray is not None:
                    self._tray.failed = True
                self._hide_pending = False
                self.show_window()
                self._show_warning("Tray unavailable", f"The window was kept open: {exc}. Turn off Close to system tray to exit.")

    def _check_tray_startup(self) -> None:
        self._tray_startup_timer = None
        if not self._closed and not self._closing and not self._tray_ready and self._tray is not None:
            self._tray._fail("The tray icon did not become ready in time.")

    def _cancel_tray_timer(self) -> None:
        if self._tray_startup_timer is not None:
            self.root.after_cancel(self._tray_startup_timer)
            self._tray_startup_timer = None

    def _withdraw_to_tray(self) -> None:
        self._hide_pending = False
        for name in ("_about_dialog", "_diagnostics_dialog"):
            dialog = getattr(self, name)
            if dialog is not None:
                dialog.close()
                setattr(self, name, None)
        self._display_identifiers.close()
        self.root.withdraw()
        self._hidden_to_tray = True

    def _tray_event(self, source, action: str, message: str) -> None:
        if source is not self._tray:
            return
        if action == "ready" and not source.failed:
            self._cancel_tray_timer()
            self._tray_ready = True
            if self._hide_pending and not self._closing:
                self._withdraw_to_tray()
        elif action == "show":
            self.show_window()
        elif action == "stop" and not self._closing:
            self.stop_cast()
        elif action == "exit":
            self.show_window()
            self.close()
        elif action == "failed":
            self._cancel_tray_timer()
            self._tray_ready = False
            self.show_window()
            if not self._closing:
                self._show_warning("Tray unavailable", f"The window was restored: {message}. Turn off Close to system tray to exit.")

    def show_about(self) -> None:
        if self._closing:
            return
        if self._about_dialog is None or not self._about_dialog.window.winfo_exists():
            self._about_dialog = AboutDialog(self.root, get_app_info())
        self._about_dialog.show()

    def show_diagnostics(self) -> None:
        if self._closing:
            return
        if self._diagnostics_dialog is None or not self._diagnostics_dialog.window.winfo_exists():
            self._diagnostics_dialog = DiagnosticsDialog(self.root, self._diagnostic_report, app_log_path())
        self._diagnostics_dialog.show()

    def _diagnostic_report(self) -> str:
        request = self._active_request
        index = self.tv_box.current()
        device = request.device if request is not None else (
            self.devices[index] if 0 <= index < len(self.devices) else None)
        tv = (f"{device.cast_info.friendly_name or 'Unnamed Cast device'} ({device.cast_info.host})"
              if device is not None else "Not selected")
        display_index = request.display_index if request is not None else self.display_box.current()
        display = request.display if request is not None else (
            self.displays[display_index] if 0 <= display_index < len(self.displays) else None)
        display_label = f"Display {display_index + 1}" if display_index >= 0 else "Disconnected display"
        display_text = (f"{display_label} ({display['width']} x {display['height']})"
                        if display is not None else "Not selected")
        mode = ("Low delay (experimental)" if request.native else "Compatibility") if request else self.mode_choice.get()
        audio = request.include_audio if request is not None else self.include_audio.get()
        delay = request.measure_delay if request is not None else self.measure_delay.get()
        quality = request.quality if request is not None else self._selected_quality()
        output = quality.output_size(display) if display is not None else None
        output_text = f"{output[0]} x {output[1]}" if output is not None else "Not selected"
        context = (
            f"Status: {self.status.get()}\nTV: {tv}\nDisplay: {display_text}\nMode: {mode}\n"
            f"Resolution limit: {quality.resolution}\nFrame rate: {quality.frame_rate} fps\n"
            f"Video output: {output_text}\n"
            f"System audio: {'On' if audio else 'Off'}\nMeasure mirroring delay: {'On' if delay else 'Off'}\n"
            f"Keep PC screen on while casting: {'On' if self.keep_screen_on.get() else 'Off'}"
        )
        return build_report(get_app_info(), context, self._last_problem, app_log_path())

    def _remember_problem(self, title: str, message: str, level: int = logging.ERROR) -> None:
        if self._hidden_to_tray:
            self.show_window()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self._last_problem = f"{timestamp} - {title}\n{message}"
        LOGGER.log(level, "%s: %s", title, message)

    def _show_error(self, title: str, message: str) -> None:
        self._remember_problem(title, message)
        messagebox.showerror(title, message)

    def _show_warning(self, title: str, message: str) -> None:
        self._remember_problem(title, message, logging.WARNING)
        messagebox.showwarning(title, message)

    def identify_displays(self) -> None:
        try:
            self._display_identifiers.show(self.displays)
        except Exception as exc:
            LOGGER.exception("Could not identify displays")
            self._show_warning("Could not identify displays", str(exc))

    def _poll_displays(self) -> None:
        self._display_timer = None
        if self._closing or self._closed:
            return
        self._refresh_displays()
        self._display_timer = self.root.after(1000, self._poll_displays)

    def _refresh_displays(self) -> bool:
        """Enumerate and reconcile on Tk; capture workers keep their own snapshot."""
        if self._closing or self._closed:
            return False
        try:
            displays = list_displays()
        except Exception as exc:
            message = f"Could not refresh displays: {exc}"
            if message != getattr(self, "_display_refresh_error", None):
                self._remember_problem("Display refresh failed", str(exc), logging.WARNING)
            self._display_refresh_error = message
            self.identify_button.configure(state="disabled")
            if self.session is not None or getattr(self, "_recovery_request", None) is not None:
                self._stop_for_display_change("The cast display could not be verified.")
            self._update_selection_state()
            return False
        recovered = getattr(self, "_display_refresh_error", None) is not None
        self._display_refresh_error = None
        previous = self.displays
        self.identify_button.configure(state="normal" if displays else "disabled")
        if displays == previous and not recovered:
            return True
        selected = remap_display_index(self.display_box.current(), previous, displays)
        identities = list_display_ids(displays)
        if selected < 0 and self._saved_display_id is not None and self._saved_display_id in identities:
            selected = identities.index(self._saved_display_id)
        invalid_request = False
        for name in ("_active_request", "_pending_switch", "_recovery_request"):
            request = getattr(self, name, None)
            if request is None:
                continue
            index = remap_display_index(request.display_index, previous, displays)
            if index < 0 or not same_capture_area(request.display, displays[index]):
                invalid_request = True
            setattr(self, name, replace(request, display_index=index))
        self._display_identifiers.close()
        self.displays = displays
        self._display_ids = identities
        self.display_box["values"] = [
            f"Display {index + 1} - {item['width']} x {item['height']}"
            for index, item in enumerate(displays)
        ]
        self.display_box.set("")
        if selected >= 0:
            self.display_box.current(selected)
        if invalid_request:
            self._stop_for_display_change("The cast display disconnected or its capture area changed.")
        elif (self.session is not None and self._active_request is not None and not self.busy
              and str(self.status.get()).startswith(("Low-delay mirroring ", "Mirroring Display "))):
            # Renumber a playing stream without replacing cleanup/error feedback.
            self._set_mirroring_status()
        self._update_selection_state()
        return True

    def _stop_for_display_change(self, reason: str) -> None:
        if getattr(self, "_display_stop_reason", None) is None:
            self._display_stop_reason = reason
            self._remember_problem("Monitor change", reason, logging.WARNING)
        self._close_diagnostic_marker()
        # stop_cast owns cancellation, pending switches and receiver ownership.
        # In particular, never clear a recovery offer before it releases the TV.
        self.stop_cast()
        if self.session is None and not self.busy:
            self._reset_recovery()
            self._set_cast_selectors_enabled(True)
            self.scan_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
        self._update_selection_state()
        if self.session is not None:
            self.status.set(f"{self._display_stop_reason} Stopping mirroring safely...")

    def _keep_screen_on_changed(self) -> None:
        self._update_screen_awake(self.session is not None)
        self._save_preference("keep_screen_on", self.keep_screen_on.get())

    def _tv_selected(self, _event: object = None) -> None:
        self._dismiss_recovery()
        index = self.tv_box.current()
        if 0 <= index < len(self.devices):
            self._saved_tv_uuid = str(self.devices[index].cast_info.uuid)
            self._save_preference("tv_uuid", self._saved_tv_uuid)
            self._update_selection_state()

    def _display_selected(self, _event: object = None) -> None:
        if self.session is not None and self.busy:
            request = self._pending_switch or self._active_request
            if request is not None and 0 <= request.display_index < len(self.displays):
                self.display_box.current(request.display_index)
            return
        self._dismiss_recovery()
        index = self.display_box.current()
        if 0 <= index < len(self.displays):
            self._saved_display_id = self._display_ids[index]
            self._save_preference("display_id", self._saved_display_id)
            if self._saved_display_id is None:
                self._show_warning(
                    "Display cannot be remembered",
                    "Windows did not provide a unique identity for this display. "
                    "You can use it now, but will need to select it again next time.",
                )
            self._update_selection_state()
            if (self.session is not None and self._active_request is not None
                    and index != self._active_request.display_index):
                self._pending_switch = replace(
                    self._active_request, display=dict(self.displays[index]), display_index=index,
                )
                self.busy = True
                self._stopping = True
                self._set_cast_selectors_enabled(False)
                self.stop_button.configure(state="normal")
                self.status.set(f"Switching to Display {index + 1}: stopping the current stream...")
                threading.Thread(target=self._stop_worker, args=(self.session,), daemon=True).start()

    def _set_cast_selectors_enabled(self, enabled: bool) -> None:
        state = "readonly" if enabled and not self._closing else "disabled"
        for selector in (self.tv_box, self.display_box, self.mode_box):
            selector.configure(state=state)
        # These options are captured at startup, unlike the live screen-on toggle.
        editable = self.session is None and not self._closing and getattr(self, "_recovery_request", None) is None
        settings_state = "normal" if editable else "disabled"
        quality_state = "readonly" if editable else "disabled"
        for selector in (self.resolution_box, self.frame_rate_box):
            selector.configure(state=quality_state)
        for checkbox in (self.measure_delay_checkbox, self.include_audio_checkbox):
            checkbox.configure(state=settings_state)

    def _selected_quality(self) -> VideoQuality:
        return VideoQuality(self.resolution_choice.get(), int(self.frame_rate_choice.get()))

    def _quality_selected(self, name: str) -> None:
        if self.session is not None or self._closing:
            return
        quality = self._selected_quality()
        self._save_preference(name, getattr(quality, name))

    def _update_selection_state(self) -> None:
        tv_selected = 0 <= self.tv_box.current() < len(self.devices)
        display_selected = 0 <= self.display_box.current() < len(self.displays)
        idle = not self.busy and self.session is None and not self._closing
        if getattr(self, "_recovery_request", None) is not None:
            self.start_button.configure(state="disabled")
            return
        refresh_error = getattr(self, "_display_refresh_error", None)
        self.start_button.configure(state="normal" if idle and tv_selected and display_selected and not refresh_error
                                    else "disabled")
        if refresh_error and idle:
            self.status.set(refresh_error + " Retrying automatically; mirroring is disabled.")
            return
        if not idle or self._scan_failed:
            return
        prompts = []
        if not tv_selected:
            if self._saved_tv_uuid is not None:
                prompts.append("Saved TV is unavailable; choose another TV or scan again.")
            elif not self.devices:
                prompts.append("No Chromecast devices found. Check that the TV is on and scan again.")
            else:
                prompts.append("Choose a TV.")
        if not display_selected:
            prompts.append("Saved display is unavailable; choose another display."
                           if self._saved_display_id is not None else "Choose a display.")
        reason = getattr(self, "_display_stop_reason", None)
        ready = "Ready to mirror; choose Start." if reason else "Ready to mirror."
        self.status.set((f"{reason} Mirroring stopped. " if reason else "")
                        + (" ".join(prompts) if prompts else ready))

    def _save_preference(self, name: str, enabled: SettingValue) -> None:
        try:
            save_settings({name: enabled})
        except OSError as exc:
            LOGGER.exception("Could not save the %s preference", name)
            self._show_warning(
                "Setting could not be saved",
                f"Your choice applies for this session, but could not be saved for the next launch: {exc}",
            )

    def _update_screen_awake(self, casting: bool) -> None:
        # Execution-state requests belong to the calling thread. Keep both
        # acquisition and release here on the GUI thread, not in cast workers.
        try:
            self._screen_awake.set_enabled(casting and not self._closing and self.keep_screen_on.get())
        except OSError as exc:
            LOGGER.exception("Could not update the screen-on power request")
            self._show_warning("Screen power request failed", str(exc))

    def scan_devices(self) -> None:
        if self.busy or self._closing:
            return
        self._dismiss_recovery()
        self.busy = True
        self._scan_failed = False
        self.scan_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Searching for Chromecast devices on the local network...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        browser = None
        try:
            devices, browser = discover_cast_devices(timeout=6)
            # The devices still use this browser's Zeroconf instance to connect
            # and reconnect. Transfer ownership to the UI until rescan or exit.
            self.events.put(("devices", (devices, browser)))
            browser = None
        except CastingError as exc:
            self.events.put(("scan_error", str(exc)))
        except Exception as exc:
            self.events.put(("scan_error", f"Chromecast discovery failed: {exc}"))
        finally:
            if browser is not None:
                try:
                    browser.stop_discovery()
                except Exception:
                    LOGGER.exception("Could not stop Chromecast discovery")
            self.events.put(("scan_finished", None))

    def start_cast(self) -> None:
        if self.busy or self.session is not None or self._closing:
            return
        if not self._refresh_displays():
            return
        if not 0 <= self.tv_box.current() < len(self.devices):
            self._show_error(
                "No TV selected",
                "Scan for Chromecast devices and select your TV first.",
            )
            return
        display_index = self.display_box.current()
        if display_index < 0 or display_index >= len(self.displays):
            self._show_error("No display selected", "Select a display first.")
            return

        self._reset_recovery()
        self._begin_cast(CastRequest(
            device=self.devices[self.tv_box.current()],
            display=dict(self.displays[display_index]), display_index=display_index,
            native=self.mode_choice.get().startswith("Low delay"),
            include_audio=self.include_audio.get(), measure_delay=self.measure_delay.get(),
            quality=self._selected_quality(),
        ))

    def _set_recovery_controls(self, visible: bool, audio: bool = False) -> None:
        # Some support clients construct the lifecycle without building widgets.
        frame = getattr(self, "recovery_controls", None)
        if frame is None:
            return
        if visible and not self._closing:
            frame.grid()
            self.retry_button.configure(state="normal")
            if audio:
                self.video_only_button.grid()
            else:
                self.video_only_button.grid_remove()
        else:
            frame.grid_remove()

    def _reset_recovery(self) -> None:
        self._recovery_request = None
        self._recovery_kind = None
        self._automatic_recovery_used = False
        self._recovering = False
        self._recovery_cleanup = False
        self._cancel_recovery = False
        self._set_recovery_controls(False)

    def _dismiss_recovery(self) -> None:
        if getattr(self, "_recovery_request", None) is not None:
            self._reset_recovery()
            self.stop_button.configure(state="disabled")

    def retry_cast(self, without_audio: bool = False) -> None:
        request = getattr(self, "_recovery_request", None)
        if request is None or self.busy or self.session is not None or self._closing:
            return
        if without_audio:
            if self._recovery_kind != "audio" or not request.include_audio:
                return
            request = replace(request, include_audio=False)
        self._recovery_request = None
        self._recovering = True
        self._cancel_recovery = False
        self._set_recovery_controls(False)
        self._begin_cast(request)

    def _recover_or_offer(self, request: CastRequest, message: FailureMessage) -> None:
        """Called on Tk only, after all old capture and socket cleanup succeeds."""
        self._remember_problem("Mirroring interrupted", str(message))
        self._recovery_cleanup = False
        self._recovery_kind = message.kind
        if message.kind in {"connection", "audio"} and not getattr(self, "_automatic_recovery_used", False):
            self._automatic_recovery_used = True
            self._recovering = True
            self._begin_cast(request)
            return
        self._recovering = False
        self._recovery_request = request
        self._set_cast_selectors_enabled(False)
        self.scan_button.configure(state="disabled")
        self._set_recovery_controls(True, message.kind == "audio" and request.include_audio)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        choices = ("Retry, Continue without audio, or Stop"
                   if message.kind == "audio" and request.include_audio else "Retry or Stop")
        self.status.set(f"Mirroring interrupted. {message} Choose {choices}.")

    def _begin_cast(self, request: CastRequest, switching: bool = False) -> None:
        previous = self.displays
        refreshed = self._refresh_displays()
        index = remap_display_index(request.display_index, previous, self.displays)
        if not refreshed or index < 0 or not same_capture_area(request.display, self.displays[index]):
            if request.recovery_receiver is not None and self.session is None:
                self._recovery_request = request
            self._stop_for_display_change("The cast display disconnected or its capture area changed.")
            return
        request = replace(request, display_index=index)
        self._display_stop_reason = None
        try:
            # A stopped Python socket thread cannot be started again. Keep the
            # discovered TV selection, but recreate its control connection.
            device = request.device
            if device.socket_client.ident is not None and not device.socket_client.is_alive():
                device = pychromecast.get_chromecast_from_cast_info(
                    device.cast_info, self.browser.zc if self.browser is not None else None,
                )
                for index, discovered in enumerate(self.devices):
                    if discovered.cast_info.uuid == request.device.cast_info.uuid:
                        self.devices[index] = device
                request = replace(request, device=device)
            if request.measure_delay:
                self._diagnostic_marker = LatencyMarker(self.root, request.display)
            session_class = NativeCastSession if request.native else CastSession
            audio_options = {"include_audio": request.include_audio} if request.native else {
                "silent_audio": not request.include_audio}
            session = session_class(
                request.device, request.display,
                lambda text: self._post_session_event(session, "capture_error", text),
                quality=request.quality, **audio_options,
            )
            session.recovering = getattr(self, "_recovering", False)
            session.previous_receiver = request.recovery_receiver
        except Exception as exc:
            LOGGER.exception("Could not prepare desktop casting")
            self._close_diagnostic_marker()
            self._active_request = None
            self._pending_switch = None
            self._stopping = False
            self.busy = False
            self._update_screen_awake(False)
            self._set_cast_selectors_enabled(True)
            self._update_selection_state()
            self.scan_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status.set("Casting failed.")
            if getattr(self, "_recovering", False):
                self._recover_or_offer(request, failure_message(exc))
            else:
                self._show_error("Casting failed", str(exc))
            return
        self.session = session
        self._active_request = request
        self._stopping = False
        self._cancelling_start = False
        self.busy = True
        self.start_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_cast_selectors_enabled(False)
        self.status.set(f"Switching to Display {request.display_index + 1}: connecting to the TV..."
                        if switching else "Starting desktop capture and connecting to the TV...")
        if getattr(self, "_recovering", False):
            self.status.set("Recovering mirroring: connecting to the same TV..."
                            + (" Video only for this run." if not request.include_audio else ""))
        self._update_screen_awake(True)
        threading.Thread(target=self._start_worker, args=(session,), daemon=True).start()

    def _post_session_event(self, session, event: str, value: object) -> None:
        self.events.put(("session", (session, event, value)))

    @staticmethod
    def _cleanup_session(session) -> list[str]:
        try:
            errors = session.stop()
            if errors:
                return errors
            # The receiver can retain its casting UI while the sender's Cast
            # control socket remains open, even after the media app has quit.
            device = session.cast_device
            if device.socket_client.is_alive():
                device.disconnect(timeout=5)
                if device.socket_client.is_alive():
                    return ["The TV's Cast control connection did not close. Choose Stop to retry."]
            return []
        except Exception as exc:
            LOGGER.exception("Could not release casting resources")
            return [f"Could not release casting resources: {exc}"]

    def _start_worker(self, session) -> None:
        try:
            session.start()
            self._post_session_event(session, "started", None)
        except Exception as exc:
            if not isinstance(exc, CastingCancelled):
                LOGGER.exception("Could not start desktop casting")
            cleanup_errors = self._cleanup_session(session)
            message = failure_message(exc)
            if cleanup_errors:
                message = FailureMessage(f"{message}\nCleanup warning(s): {'; '.join(cleanup_errors)}", message.kind)
            self._post_session_event(session, "error", (message, cleanup_errors))

    def stop_cast(self) -> None:
        if getattr(self, "_recovery_request", None) is not None:
            request = self._recovery_request
            self._reset_recovery()
            if request.recovery_receiver is None:
                self.stop_button.configure(state="disabled")
                self._set_cast_selectors_enabled(True)
                self.scan_button.configure(state="normal")
                self._update_selection_state()
                self.status.set("Mirroring stopped.")
                return
            self.session = ReceiverCleanupSession(
                request.device, self.browser.zc if self.browser is not None else None, request.recovery_receiver,
            )
            self._active_request = request
        if getattr(self, "_recovery_cleanup", False):
            self._cancel_recovery = True
            self.stop_button.configure(state="disabled")
            self.status.set("Cancelling recovery and releasing capture resources...")
            return
        if self._pending_switch is not None:
            # Cleanup is already running. Cancel its queued restart without
            # launching a second worker against the same old session.
            self._pending_switch = None
            self.stop_button.configure(state="disabled")
            self.status.set("Stopping playback and releasing capture resources...")
            return
        if self.session is None or self._stopping:
            return
        if self.busy:
            # Startup owns resource allocation and cleanup until it finishes.
            # Signal only; a second stop worker could race with new resources.
            self._cancelling_start = True
            self._stopping = True
            self.session.cancel_start()
            self._update_screen_awake(False)
            self.stop_button.configure(state="disabled")
            self.status.set("Cancelling connection and releasing capture resources...")
            return
        self.busy = True
        self._stopping = True
        self.stop_button.configure(state="disabled")
        self._set_cast_selectors_enabled(False)
        self.status.set("Stopping playback and releasing capture resources...")
        threading.Thread(target=self._stop_worker, args=(self.session,), daemon=True).start()

    def _stop_worker(self, session) -> None:
        errors = self._cleanup_session(session)
        self._post_session_event(session, "stopped", errors)

    def _close_diagnostic_marker(self) -> None:
        marker = getattr(self, "_diagnostic_marker", None)
        if marker is not None:
            marker.close()
            self._diagnostic_marker = None

    def _set_mirroring_status(self) -> None:
        display = (f"Display {self._active_request.display_index + 1}"
                   if self._active_request is not None else "desktop")
        if isinstance(self.session, NativeCastSession):
            self.status.set(f"Low-delay mirroring {display} to the TV." + (
                " Video only." if not self.session.include_audio else ""))
        else:
            audio = self._active_request is None or self._active_request.include_audio
            self.status.set(f"Mirroring {display} to the TV. "
                            + ("Picture and audio may be delayed." if audio
                               else "Video only. Picture may be delayed."))

    def _process_events(self) -> None:
        if self._closed:
            return
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "tray":
                    self._tray_event(*value)
                    continue
                if event == "session":
                    source, event, value = value
                    if source is not self.session:
                        continue
                if event == "shutdown_complete":
                    # Do not destroy Tk while this worker still holds the app.
                    # The event can be consumed just before Thread.run returns.
                    if self._shutdown_thread is not None and self._shutdown_thread.is_alive():
                        self.root.after(10, lambda errors=value: self.events.put(("shutdown_complete", errors)))
                        continue
                    self._shutdown_thread = None
                    self._shutdown_started = False
                    self.busy = False
                    if value:
                        self.status.set("Could not finish closing. " + " ".join(value)
                                        + " Close the window again to retry.")
                        self._remember_problem("Could not finish closing", " ".join(value))
                        continue
                    self.devices = []
                    self.browser = None
                    self._tray = None
                    self._closed = True
                    self.root.destroy()
                    return
                if event == "devices":
                    previous_devices = self.devices
                    previous_browser = self.browser
                    self.devices, self.browser = value
                    for device in previous_devices:
                        try:
                            self._disconnect_device(device)
                        except Exception:
                            LOGGER.exception("Could not disconnect previous TV scan")
                    self._close_browser(previous_browser)
                    names = [
                        device.cast_info.friendly_name or "Unnamed Cast device" for device in self.devices
                    ]
                    counts = Counter(names)
                    names = [
                        f"{name} ({device.cast_info.host}, {index + 1})" if counts[name] > 1 else name
                        for index, (name, device) in enumerate(zip(names, self.devices))
                    ]
                    self.tv_box["values"] = names
                    self.tv_choice.set("")
                    for index, device in enumerate(self.devices):
                        if str(device.cast_info.uuid) == self._saved_tv_uuid:
                            self.tv_box.current(index)
                            break
                elif event == "scan_finished":
                    self.busy = False
                    self.scan_button.configure(state="disabled" if self._closing else "normal")
                    self._update_selection_state()
                elif event == "scan_error":
                    self._scan_failed = True
                    if not self._closing:
                        self.status.set("Chromecast discovery failed.")
                        self._show_error("Chromecast discovery failed", str(value))
                elif event == "started":
                    if self._cancelling_start:
                        # Start completed just before the GUI requested cancel.
                        # Its worker has returned, so cleanup can now run safely.
                        threading.Thread(target=self._stop_worker, args=(self.session,), daemon=True).start()
                        continue
                    if self._stopping:
                        continue
                    self.busy = False
                    self._set_cast_selectors_enabled(True)
                    self.stop_button.configure(state="normal")
                    self._set_mirroring_status()
                elif event == "stopped":
                    cancelled = self._cancelling_start
                    self._cancelling_start = False
                    pending = self._pending_switch
                    self._pending_switch = None
                    self._stopping = False
                    errors = value if isinstance(value, list) else []
                    if errors:
                        self._reset_recovery()
                        self._remember_problem("Could not release casting resources", " ".join(errors))
                        self._update_screen_awake(False)
                        self.busy = False
                        self.stop_button.configure(state="normal")
                        self.status.set(("Could not fully cancel connection. " if cancelled
                                         else "Could not fully stop mirroring. ") + " ".join(errors)
                                        + (" Choose Stop or close the window again to retry cleanup."
                                           if self._closing else " Choose Stop to retry cleanup."))
                        if self._active_request is not None and 0 <= self._active_request.display_index < len(self.displays):
                            self.display_box.current(self._active_request.display_index)
                        if pending is not None:
                            self._show_error("Display switch failed", self.status.get())
                        continue
                    self._close_diagnostic_marker()
                    self.session = None
                    self._active_request = None
                    self.busy = False
                    if pending is not None:
                        self._begin_cast(pending, switching=True)
                        continue
                    self._reset_recovery()
                    self._update_screen_awake(False)
                    self._set_cast_selectors_enabled(True)
                    self._update_selection_state()
                    self.scan_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    status = "Connection cancelled." if cancelled else "Mirroring stopped."
                    self.status.set(status)
                    if getattr(self, "_display_stop_reason", None):
                        self._update_selection_state()
                elif event == "capture_error":
                    if self.session is None or self._stopping:
                        continue
                    self._pending_switch = None
                    recoverable = getattr(value, "kind", None) in {"connection", "audio"}
                    self._recovery_cleanup = recoverable or getattr(self, "_recovering", False)
                    self._cancel_recovery = False
                    self._stopping = True
                    self._update_screen_awake(False)
                    self.busy = True
                    self.start_button.configure(state="disabled")
                    self.scan_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
                    if self._recovery_cleanup:
                        self.stop_button.configure(state="normal")
                    self._set_cast_selectors_enabled(False)
                    self.status.set("Desktop capture stopped unexpectedly.")
                    if self._recovery_cleanup:
                        self.status.set("Mirroring interrupted: releasing resources before recovery. Choose Stop to cancel.")
                    threading.Thread(
                        target=self._report_capture_failure,
                        args=(self.session, value),
                        daemon=True,
                    ).start()
                elif event == "error":
                    message, cleanup_errors = value if isinstance(value, tuple) else (str(value), [])
                    message = failure_message(message)
                    if self._cancelling_start or self._closing or getattr(self, "_cancel_recovery", False):
                        ownership = getattr(self.session, "recovery_receiver", None)
                        if not isinstance(ownership, ReceiverOwnership) and self._active_request is not None:
                            ownership = self._active_request.recovery_receiver
                        if (not cleanup_errors and isinstance(ownership, ReceiverOwnership)
                                and (getattr(self, "_recovering", False) or getattr(self, "_recovery_cleanup", False))):
                            self.session = ReceiverCleanupSession(
                                self.session.cast_device, self.browser.zc if self.browser is not None else None, ownership,
                            )
                            self._recovery_cleanup = False
                            self._cancel_recovery = False
                            threading.Thread(target=self._stop_worker, args=(self.session,), daemon=True).start()
                            continue
                        # The startup worker has already performed cleanup.
                        # Cancellation is expected, including a late startup error.
                        self._post_session_event(self.session, "stopped", cleanup_errors)
                        continue
                    self._update_screen_awake(False)
                    self.busy = False
                    self._pending_switch = None
                    self._stopping = False
                    request = self._active_request
                    ownership = getattr(self.session, "recovery_receiver", None)
                    if request is not None and isinstance(ownership, ReceiverOwnership):
                        request = replace(request, recovery_receiver=ownership)
                    if cleanup_errors and self.session is not None:
                        self._reset_recovery()
                        self._set_cast_selectors_enabled(False)
                        self.start_button.configure(state="disabled")
                        self.scan_button.configure(state="disabled")
                        self.stop_button.configure(state="normal")
                        self.status.set("Casting failed. Choose Stop to retry cleanup.")
                        self._show_error("Casting failed", message + "\nChoose Stop to retry cleanup.")
                        continue
                    self._close_diagnostic_marker()
                    self.session = None
                    self._active_request = None
                    self._set_cast_selectors_enabled(True)
                    self._update_selection_state()
                    self.scan_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status.set("Casting failed.")
                    if request is not None and (message.kind in {"connection", "audio"}
                                                or getattr(self, "_recovering", False)):
                        self._recover_or_offer(request, message)
                    else:
                        self._recovery_cleanup = False
                        self._show_error("Casting failed", message)
                if self._closing and not self.busy and self.session is None:
                    self._begin_shutdown()
        except queue.Empty:
            pass
        if self._tray is not None and self._tray_ready:
            self._tray.set_can_stop(not self._closing and str(self.stop_button.cget("state")) == "normal")
        self.root.after(100, self._process_events)

    def _report_capture_failure(self, session, message: str) -> None:
        cleanup_errors = self._cleanup_session(session)
        message = failure_message(message)
        if cleanup_errors:
            message = FailureMessage(str(message) + "\nCleanup warning(s): " + "; ".join(cleanup_errors), message.kind)
        self._post_session_event(session, "error", (message, cleanup_errors))

    def close(self) -> None:
        if self._closed or self._shutdown_started or (self._closing and self.busy):
            return
        self._closing = True
        request = getattr(self, "_recovery_request", None)
        if request is not None and request.recovery_receiver is not None:
            self.session = ReceiverCleanupSession(
                request.device, self.browser.zc if self.browser is not None else None, request.recovery_receiver,
            )
            self._active_request = request
        self._recovery_request = None
        self._set_recovery_controls(False)
        self._cancel_tray_timer()
        if getattr(self, "_display_timer", None) is not None:
            self.root.after_cancel(self._display_timer)
            self._display_timer = None
        if self._hidden_to_tray:
            self.show_window()
        self._hide_pending = False
        self._update_screen_awake(False)
        self._close_diagnostic_marker()
        self._display_identifiers.close()
        if self._about_dialog is not None:
            self._about_dialog.close()
            self._about_dialog = None
        if self._diagnostics_dialog is not None:
            self._diagnostics_dialog.close()
            self._diagnostics_dialog = None
        self._set_cast_selectors_enabled(False)
        self.start_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.identify_button.configure(state="disabled")
        self.about_button.configure(state="disabled")
        self.diagnostics_button.configure(state="disabled")
        self.close_to_tray_checkbox.configure(state="disabled")
        if self.session is not None:
            self.stop_cast()
            self.stop_button.configure(state="disabled")
            self.status.set("Closing: cancelling casting and releasing resources...")
        elif self.busy:
            # Discovery is bounded and owns its browser until it posts results.
            # Keep pumping Tk; close those resources as soon as it hands them off.
            self.status.set("Closing: waiting for TV discovery to finish...")
        else:
            self._begin_shutdown()

    def _begin_shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.busy = True
        self.stop_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.status.set("Closing: disconnecting TVs and stopping discovery...")
        self._shutdown_thread = threading.Thread(
            target=self._shutdown_worker, args=(tuple(self.devices), self.browser), daemon=True,
        )
        self._shutdown_thread.start()

    def _shutdown_worker(self, devices, browser) -> None:
        errors = []
        for device in devices:
            try:
                if device.socket_client.is_alive():
                    device.disconnect(timeout=5)
                    if device.socket_client.is_alive():
                        raise RuntimeError("A TV control connection is still closing.")
            except Exception as exc:
                LOGGER.exception("Could not disconnect Chromecast device")
                errors.append(str(exc))
        if not errors and browser is not None:
            try:
                browser.stop_discovery()
            except Exception as exc:
                LOGGER.exception("Could not stop Chromecast discovery")
                errors.append(str(exc))
        if not errors and self._tray is not None:
            errors.extend(self._tray.stop())
        self.events.put(("shutdown_complete", errors))

    @staticmethod
    def _close_browser(browser: CastBrowser | None) -> None:
        if browser is not None:
            try:
                browser.stop_discovery()
            except Exception:
                LOGGER.exception("Could not stop Chromecast discovery")

    @staticmethod
    def _disconnect_device(device: pychromecast.Chromecast) -> None:
        if device.socket_client.is_alive():
            device.disconnect()


def main() -> None:
    root = tk.Tk()
    log_file = app_log_path()
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    except OSError as exc:
        root.withdraw()
        messagebox.showerror(
            "Cannot save application files",
            f"Cast to TV needs a writable folder beside its executable. "
            f"Move the complete app package to a folder you can write to and try again.\n\n{exc}",
            parent=root,
        )
        root.destroy()
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            file_handler,
        ],
    )
    LOGGER.info("Starting Cast to TV; diagnostics: %s", log_file)
    app = CastToTVApp(root)
    try:
        root.mainloop()
    finally:
        app._update_screen_awake(False)
