"""
USB Joystick Input Plugin
=========================
Reads from any USB-connected RC transmitter acting as a USB HID joystick
(TX15 Max, any EdgeTX / OpenTX radio in "USB Joystick" mode, Spektrum with
USB adapter, FrSky, etc.) and injects its channel values into the adapter as
a complete drop-in replacement for the SRXL2 Arduino bridge.

How it works
------------
The transmitter's USB joystick axes are mapped to channel numbers that the
adapter's profile system already understands.  The injected channel values
are scaled to the same raw range used by the SRXL2 firmware (≈10944–54592),
so existing profile calibrations stay valid.  All other adapter features
(profile inputs, Feed Cut plugin, virtual Xbox output) work unchanged.

When the USB source is running, the Arduino receiver is bypassed completely.
When stopped, the Arduino receiver resumes automatically.

EdgeTX / OpenTX default USB channel order
------------------------------------------
  Axis 0 → CH1   (usually Aileron / Roll)
  Axis 1 → CH2   (usually Elevator / Pitch)
  Axis 2 → CH3   (usually Throttle)
  Axis 3 → CH4   (usually Rudder / Yaw)
  Axis 4 → CH5   (AUX1 — switches, knobs)
  Axis 5 → CH6   (AUX2)
  …

After switching sources, open the Inputs tab and re-run calibration so the
adapter learns the TX15 Max's exact raw values for each input.

Requires
--------
    pip install pygame
  or run scripts/install_dependencies.bat after adding pygame to requirements.

Config: plugins/usb_joystick_plugin.json
"""

import json
import threading
import time
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("usb_joystick_plugin.json")

# Raw value formula: raw = CENTER ± axis_value * HALF_RANGE
# Maps pygame -1.0..1.0  →  10944..54592  (SRXL2 midrange, DX6-compatible)
_CENTER    = 32768
_HALF      = 21824
_POLL_HZ   = 60

DEFAULT_CONFIG = {
    "enabled":          False,
    "device_name":      None,
    # axis index (str) → channel number (int)
    "axis_channel_map": {str(i): i + 1 for i in range(8)},
    "center_value":     _CENTER,
    "half_range":       _HALF,
    "poll_rate_hz":     _POLL_HZ,
}

_config     = dict(DEFAULT_CONFIG)
_poll_thread: threading.Thread = None
_stop_event:  threading.Event  = threading.Event()
_status_var   = None   # tkinter StringVar; set from UI


# ── config I/O ────────────────────────────────────────────────────────────────

def _load_config():
    global _config
    try:
        if CONFIG_PATH.exists():
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_CONFIG)
            merged["axis_channel_map"] = dict(DEFAULT_CONFIG["axis_channel_map"])
            merged.update(loaded)
            # axis_channel_map keys must be strings
            merged["axis_channel_map"] = {
                str(k): int(v) for k, v in merged["axis_channel_map"].items()
            }
            _config = merged
    except Exception:
        pass


def _save_config():
    try:
        CONFIG_PATH.write_text(json.dumps(_config, indent=4), encoding="utf-8")
    except Exception:
        pass


# ── pygame helpers ────────────────────────────────────────────────────────────

def _try_import_pygame():
    try:
        import pygame  # noqa: F401
        return True
    except ImportError:
        return False


def _pygame_init():
    """Init only the joystick subsystem; avoids opening any SDL window."""
    import pygame
    if not pygame.get_init():
        # Init display-free; silence any ALSA/AUDIODEV noise on Linux
        import os
        os.environ.setdefault("SDL_VIDEODRIVER",  "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER",  "dummy")
        pygame.init()
    if not pygame.joystick.get_init():
        pygame.joystick.init()


def list_joystick_devices():
    """Return a list of {index, name, axes} dicts for every detected joystick."""
    if not _try_import_pygame():
        return []
    try:
        import pygame
        _pygame_init()
        pygame.joystick.quit()
        pygame.joystick.init()
        devices = []
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            j.init()
            devices.append({
                "index": i,
                "name":  j.get_name(),
                "axes":  j.get_numaxes(),
            })
            j.quit()
        return devices
    except Exception:
        return []


# ── poll loop (background thread) ─────────────────────────────────────────────

def _poll_loop(api, joystick, stop_event, cfg):
    interval   = 1.0 / max(1, int(cfg.get("poll_rate_hz", _POLL_HZ)))
    axis_map   = {int(k): int(v) for k, v in cfg.get("axis_channel_map", {}).items()}
    center     = int(cfg.get("center_value", _CENTER))
    half       = int(cfg.get("half_range",   _HALF))

    import pygame

    try:
        while not stop_event.is_set():
            t0 = time.monotonic()

            # Pump the SDL event queue so joystick axis values refresh.
            # On Windows this works from any thread; on macOS it must be main.
            try:
                pygame.event.pump()
            except Exception:
                pass

            channels = {}
            for ax_idx, ch_num in axis_map.items():
                if ax_idx < joystick.get_numaxes():
                    val = joystick.get_axis(ax_idx)          # -1.0 … +1.0
                    raw = int(center + val * half)
                    channels[ch_num] = max(0, min(65535, raw))

            if channels:
                api.set_injected_channels(channels)

            elapsed = time.monotonic() - t0
            wait    = interval - elapsed
            if wait > 0:
                stop_event.wait(wait)

    except Exception:
        pass
    finally:
        # When the thread exits, clear injection so the SRXL2 receiver resumes.
        try:
            api.clear_injected_channels()
        except Exception:
            pass
        try:
            joystick.quit()
        except Exception:
            pass
        if _status_var is not None:
            try:
                _status_var.set("Stopped")
            except Exception:
                pass


# ── start / stop ──────────────────────────────────────────────────────────────

def _is_running():
    return _poll_thread is not None and _poll_thread.is_alive()


def start_usb_source(api):
    """Start polling. Returns (True, message) or (False, error_message)."""
    global _poll_thread, _stop_event

    if _is_running():
        return True, "Already running"

    if not _try_import_pygame():
        return False, (
            "pygame not installed.\n"
            "Run: pip install pygame\n"
            "or add it to requirements.txt and run install_dependencies.bat"
        )

    target = _config.get("device_name")
    if not target:
        return False, "No device selected. Choose one from the dropdown and save."

    import pygame
    try:
        _pygame_init()
    except Exception as e:
        return False, f"pygame init failed: {e}"

    devices = list_joystick_devices()
    match   = next((d for d in devices if d["name"] == target), None)
    if match is None:
        names = ", ".join(d["name"] for d in devices) or "none found"
        return False, f"'{target}' not found.\nConnected devices: {names}"

    try:
        joystick = pygame.joystick.Joystick(match["index"])
        joystick.init()
    except Exception as e:
        return False, f"Could not open joystick: {e}"

    _stop_event = threading.Event()
    _poll_thread = threading.Thread(
        target=_poll_loop,
        args=(api, joystick, _stop_event, dict(_config)),
        daemon=True,
        name="usb_joystick_poll",
    )
    _poll_thread.start()
    return True, f"Polling '{target}'  ({match['axes']} axes)"


def stop_usb_source():
    """Stop polling and clear injection."""
    global _poll_thread
    _stop_event.set()
    if _poll_thread:
        _poll_thread.join(timeout=2.0)
        _poll_thread = None


# ── plugin entry point ────────────────────────────────────────────────────────

def setup(api):
    _load_config()

    # If it was left enabled, don't auto-start without user confirmation —
    # just leave the controller in normal receiver mode until they press Start.

    # ── UI tab ────────────────────────────────────────────────────────────────
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox

        def _build_tab(parent, api):
            global _status_var

            parent.columnconfigure(0, weight=1)
            row = [0]

            def _nrow(span=1, **kw):
                r = row[0]
                row[0] += 1
                return r

            ttk.Label(parent, text="USB Joystick Input", font=("Segoe UI", 13, "bold")).grid(
                row=_nrow(), column=0, sticky="w"
            )
            ttk.Label(
                parent,
                text=(
                    "Use any USB-connected transmitter (TX15 Max, EdgeTX, OpenTX…) as the "
                    "channel source instead of the Arduino bridge.  Select the device, click "
                    "Start, then re-calibrate your profile inputs for the new source."
                ),
                wraplength=780, justify="left",
            ).grid(row=_nrow(), column=0, sticky="w", pady=(4, 12))

            # ── device selection ──────────────────────────────────────────────
            dev_frame = ttk.LabelFrame(parent, text="Device", padding=8)
            dev_frame.grid(row=_nrow(), column=0, sticky="ew", pady=(0, 8))
            dev_frame.columnconfigure(1, weight=1)

            _devices   = []
            device_var = tk.StringVar(value=_config.get("device_name") or "")

            device_combo = ttk.Combobox(dev_frame, textvariable=device_var, state="readonly", width=48)
            device_combo.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

            axes_var  = tk.StringVar(value="")
            ttk.Label(dev_frame, textvariable=axes_var, foreground="#555").grid(
                row=1, column=0, columnspan=2, sticky="w"
            )

            def _refresh_devices():
                nonlocal _devices
                if not _try_import_pygame():
                    messagebox.showwarning("USB Joystick", "pygame not installed.\nRun: pip install pygame")
                    return
                _devices = list_joystick_devices()
                names = [d["name"] for d in _devices]
                device_combo["values"] = names
                if device_var.get() not in names and names:
                    device_var.set(names[0])
                _update_axes_label()

            def _update_axes_label(*_):
                name = device_var.get()
                match = next((d for d in _devices if d["name"] == name), None)
                if match:
                    axes_var.set(f"{match['axes']} axes detected")
                    _rebuild_axis_map(match["axes"])
                else:
                    axes_var.set("")

            device_combo.bind("<<ComboboxSelected>>", _update_axes_label)
            ttk.Button(dev_frame, text="Refresh", command=_refresh_devices).grid(
                row=2, column=0, sticky="w", pady=(6, 0)
            )

            # ── axis → channel mapping ────────────────────────────────────────
            map_frame = ttk.LabelFrame(parent, text="Axis → Channel mapping", padding=8)
            map_frame.grid(row=_nrow(), column=0, sticky="ew", pady=(0, 8))

            _axis_rows  = []   # list of (axis_label, ch_spinbox, ch_var)
            _map_frame_inner = ttk.Frame(map_frame)
            _map_frame_inner.grid(row=0, column=0, sticky="ew")

            def _rebuild_axis_map(num_axes):
                for w in _map_frame_inner.winfo_children():
                    w.destroy()
                _axis_rows.clear()

                saved_map = _config.get("axis_channel_map", {})
                cols = 4  # show 4 pairs per row
                for ax_i in range(num_axes):
                    col_base = (ax_i % cols) * 3
                    row_i    = ax_i // cols
                    ch_val   = saved_map.get(str(ax_i), ax_i + 1)
                    ch_var   = tk.IntVar(value=int(ch_val))
                    ttk.Label(_map_frame_inner, text=f"Axis {ax_i} →").grid(
                        row=row_i, column=col_base, sticky="e", padx=(8, 2), pady=2
                    )
                    ttk.Spinbox(
                        _map_frame_inner, from_=1, to=32, textvariable=ch_var, width=5
                    ).grid(row=row_i, column=col_base + 1, sticky="w", padx=(0, 8), pady=2)
                    _axis_rows.append((ax_i, ch_var))

            # ── status & start/stop ───────────────────────────────────────────
            ctrl_frame = ttk.Frame(parent)
            ctrl_frame.grid(row=_nrow(), column=0, sticky="ew", pady=(0, 8))

            _status_var = tk.StringVar(value="Stopped")
            status_lbl  = ttk.Label(ctrl_frame, textvariable=_status_var, width=52)
            status_lbl.pack(side="left")

            start_btn = ttk.Button(ctrl_frame, text="▶  Start", width=12)
            stop_btn  = ttk.Button(ctrl_frame, text="■  Stop",  width=12, state="disabled")
            start_btn.pack(side="left", padx=(8, 4))
            stop_btn.pack(side="left")

            def _on_start():
                # Save current mapping first
                new_map = {str(ax): int(var.get()) for ax, var in _axis_rows}
                _config["device_name"]      = device_var.get()
                _config["axis_channel_map"] = new_map
                _save_config()

                ok, msg = start_usb_source(api)
                _status_var.set(msg)
                if ok:
                    start_btn.config(state="disabled")
                    stop_btn.config(state="normal")
                    _schedule_live_update()
                else:
                    messagebox.showerror("USB Joystick", msg, parent=parent)

            def _on_stop():
                stop_usb_source()
                _status_var.set("Stopped")
                start_btn.config(state="normal")
                stop_btn.config(state="disabled")

            start_btn.config(command=_on_start)
            stop_btn.config(command=_on_stop)

            if _is_running():
                start_btn.config(state="disabled")
                stop_btn.config(state="normal")
                _status_var.set(f"Polling '{_config.get('device_name')}'")

            # ── live axis display ─────────────────────────────────────────────
            live_frame = ttk.LabelFrame(parent, text="Live axis values  (when running)", padding=8)
            live_frame.grid(row=_nrow(), column=0, sticky="ew")
            live_frame.columnconfigure(1, weight=1)

            _live_bars  = []
            _live_label = tk.StringVar(value="Not running")
            ttk.Label(live_frame, textvariable=_live_label).grid(
                row=0, column=0, columnspan=3, sticky="w"
            )

            def _update_live_bars(num):
                for w in live_frame.winfo_children():
                    w.destroy()
                _live_bars.clear()
                for i in range(num):
                    ttk.Label(live_frame, text=f"Axis {i}", width=8).grid(
                        row=i, column=0, sticky="w", padx=(0, 6), pady=1
                    )
                    bar = ttk.Progressbar(live_frame, orient="horizontal",
                                          mode="determinate", maximum=100, length=220)
                    bar.grid(row=i, column=1, sticky="ew", pady=1)
                    val_var = tk.StringVar(value="—")
                    ttk.Label(live_frame, textvariable=val_var, width=7).grid(
                        row=i, column=2, sticky="w", padx=(6, 0)
                    )
                    _live_bars.append((bar, val_var))

            _live_scheduled = [False]

            def _schedule_live_update():
                if not _live_scheduled[0]:
                    _live_scheduled[0] = True
                    _do_live_update()

            def _do_live_update():
                if not _is_running():
                    _live_scheduled[0] = False
                    for bar, vv in _live_bars:
                        bar["value"] = 50
                        vv.set("—")
                    return

                injected = api.get_injected_channels()
                if injected and len(_live_bars) == 0:
                    # First update — build bars
                    name  = _config.get("device_name", "")
                    match = next((d for d in _devices if d["name"] == name), None)
                    n_ax  = match["axes"] if match else len(injected)
                    _update_live_bars(n_ax)

                if injected:
                    ax_map = {int(v): i for i, (_, v) in enumerate(
                        sorted(_config.get("axis_channel_map", {}).items(), key=lambda x: int(x[0]))
                    )}
                    for ax_i, (bar, vv) in enumerate(_live_bars):
                        ch = _config.get("axis_channel_map", {}).get(str(ax_i))
                        if ch is not None:
                            raw   = injected.get(int(ch), _CENTER)
                            frac  = (raw - (_CENTER - _HALF)) / (2 * _HALF)
                            pct   = max(0.0, min(100.0, frac * 100))
                            norm  = (raw - _CENTER) / _HALF   # -1..+1
                            bar["value"] = pct
                            vv.set(f"{norm:+.3f}")

                try:
                    parent.after(80, _do_live_update)
                except Exception:
                    _live_scheduled[0] = False

            # Kick off live update if already running when tab opens
            if _is_running():
                _schedule_live_update()

            # Initial device scan
            _refresh_devices()

        api.register_tab("USB Input", _build_tab, order=150)

    except Exception:
        pass   # No UI if tkinter is unavailable

    return {"name": "USB Joystick Input", "version": "1.0"}
