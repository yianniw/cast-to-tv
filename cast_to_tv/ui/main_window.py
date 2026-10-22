"""Construct the main window's controls on the Tk thread."""
from __future__ import annotations

from tkinter import ttk
from typing import TYPE_CHECKING

from ..quality import FRAME_RATES, RESOLUTIONS

if TYPE_CHECKING:
    from ..app import CastToTVApp


def build_main_window(app: CastToTVApp) -> None:
    """Attach controls to the app, preserving its widget attributes and callbacks."""
    root = app.root
    frame = ttk.Frame(root, padding=16)
    frame.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="TV").grid(row=0, column=0, sticky="w", pady=5)
    app.tv_box = ttk.Combobox(
        frame,
        textvariable=app.tv_choice,
        state="readonly",
        width=34,
    )
    app.tv_box.grid(row=0, column=1, sticky="ew", pady=5)
    app.tv_box.bind("<<ComboboxSelected>>", app._tv_selected)
    app.scan_button = ttk.Button(
        frame,
        text="Scan",
        command=app.scan_devices,
    )
    app.scan_button.grid(row=0, column=2, padx=(8, 0), pady=5)

    ttk.Label(frame, text="Display").grid(row=1, column=0, sticky="w", pady=5)
    app.display_box = ttk.Combobox(
        frame,
        textvariable=app.display_choice,
        state="readonly",
        width=34,
    )
    app.display_box.grid(row=1, column=1, sticky="ew", pady=5)
    app.display_box.bind("<<ComboboxSelected>>", app._display_selected)
    app.identify_button = ttk.Button(frame, text="Identify", command=app.identify_displays)
    app.identify_button.grid(row=1, column=2, padx=(8, 0), pady=5)

    ttk.Label(frame, text="Mode").grid(row=2, column=0, sticky="w", pady=5)
    app.mode_box = ttk.Combobox(frame, textvariable=app.mode_choice, state="readonly",
                               values=("Low delay (experimental)", "Compatibility"))
    app.mode_box.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
    ttk.Label(frame, text="Resolution").grid(row=3, column=0, sticky="w", pady=5)
    quality_controls = ttk.Frame(frame)
    quality_controls.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)
    quality_controls.columnconfigure(0, weight=1)
    app.resolution_box = ttk.Combobox(
        quality_controls, textvariable=app.resolution_choice, state="readonly", values=tuple(RESOLUTIONS),
    )
    app.resolution_box.grid(row=0, column=0, sticky="ew")
    app.resolution_box.bind("<<ComboboxSelected>>", lambda _event: app._quality_selected("resolution"))
    ttk.Label(quality_controls, text="FPS").grid(row=0, column=1, padx=(12, 8))
    app.frame_rate_box = ttk.Combobox(
        quality_controls, textvariable=app.frame_rate_choice, state="readonly",
        values=tuple(str(rate) for rate in FRAME_RATES), width=5,
    )
    app.frame_rate_box.grid(row=0, column=2)
    app.frame_rate_box.bind("<<ComboboxSelected>>", lambda _event: app._quality_selected("frame_rate"))
    controls = ttk.Frame(frame)
    controls.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 8))
    app.start_button = ttk.Button(
        controls,
        text="Start mirroring",
        command=app.start_cast,
    )
    app.start_button.grid(row=0, column=0, padx=(0, 8))
    app.stop_button = ttk.Button(
        controls,
        text="Stop",
        command=app.stop_cast,
        state="disabled",
    )
    app.stop_button.grid(row=0, column=1)
    app.about_button = ttk.Button(controls, text="About", command=app.show_about)
    app.about_button.grid(row=0, column=2, padx=(8, 0))
    app.diagnostics_button = ttk.Button(controls, text="Diagnostics", command=app.show_diagnostics)
    app.diagnostics_button.grid(row=0, column=3)

    app.measure_delay_checkbox = ttk.Checkbutton(
        frame, text="Measure mirroring delay", variable=app.measure_delay,
        command=lambda: app._save_preference("measure_delay", app.measure_delay.get()),
    )
    app.measure_delay_checkbox.grid(
        row=5, column=0, columnspan=3, sticky="w",
    )
    app.include_audio_checkbox = ttk.Checkbutton(
        frame, text="Include system audio", variable=app.include_audio,
        command=lambda: app._save_preference("include_audio", app.include_audio.get()),
    )
    app.include_audio_checkbox.grid(
        row=6, column=0, columnspan=3, sticky="w",
    )
    ttk.Checkbutton(
        frame, text="Keep PC screen on while casting", variable=app.keep_screen_on,
        command=app._keep_screen_on_changed,
    ).grid(row=7, column=0, columnspan=3, sticky="w")
    app.close_to_tray_checkbox = ttk.Checkbutton(
        frame, text="Close to system tray", variable=app.close_to_tray,
        command=lambda: app._save_preference("close_to_tray", app.close_to_tray.get()),
    )
    app.close_to_tray_checkbox.grid(row=8, column=0, columnspan=3, sticky="w")
    ttk.Label(
        frame,
        textvariable=app.status,
        wraplength=410,
    ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 0))
    app.recovery_controls = ttk.Frame(frame)
    app.recovery_controls.grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))
    app.retry_button = ttk.Button(app.recovery_controls, text="Retry", command=app.retry_cast)
    app.retry_button.grid(row=0, column=0, padx=(0, 8))
    app.video_only_button = ttk.Button(
        app.recovery_controls, text="Continue without audio", command=lambda: app.retry_cast(without_audio=True),
    )
    app.video_only_button.grid(row=0, column=1)
    app.recovery_controls.grid_remove()
