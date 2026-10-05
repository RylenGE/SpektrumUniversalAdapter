"""
USB Joystick Input Plugin
=========================
Turns any USB-connected RC transmitter (TX15 Max, EdgeTX, OpenTX, FrSky, etc.
in USB Joystick mode) into a direct channel source, fully replacing the
SRXL2 Arduino bridge.  No Arduino needed.

Each joystick axis becomes a channel automatically:
  Axis 0 -> CH1,  Axis 1 -> CH2,  Axis 2 -> CH3,  ... and so on.

Raw values are scaled to the standard SRXL2 range (approx 10944-54592) so the
Inputs tab calibration wizard works exactly as it does with the Arduino --
just move each control and click Record.

Setup
-----
1. Put your transmitter in USB Joystick mode.
   EdgeTX: System > USB Mode > USB Joystick
2. Open the USB Input tab, select the device, click Start.
3. Open the Inputs tab.  Live channel values update from the transmitter.
4. Add / recalibrate input objects using the normal wizard.
"""

import json
import threading
import time
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("usb_joystick_plugin.json")

# Axis value -1.0 -> raw 10944,  0.0 -> 32768,  +1.0 -> 54592
_CENTER = 32768
_HALF   = 21824

DEFAULT_CONFIG = {
    "device_name":  None,
    "poll_rate_hz": 60,
}

_config      = dict(DEFAULT_CONFIG)
_poll_thread = None
_stop_event  = threading.Event()


# -- config -------------------------------------------------------------------

def _load_config():
    global _config
    try:
        if CONFIG_PATH.exists():
            _config = {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text("utf-8"))}
    except Exception:
        pass


def _save_config():
    try:
        CONFIG_PATH.write_text(json.dumps(_config, indent=4), "utf-8")
    except Exception:
        pass


# -- pygame -------------------------------------------------------------------

def _init_pygame():
    """Return (pygame_module, None) or (None, error_string)."""
    try:
        import os
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        import pygame
        if not pygame.get_init():
            pygame.init()
        if not pygame.joystick.get_init():
            pygame.joystick.init()
        return pygame, None
    except Exception as exc:
        return None, str(exc)


def list_devices():
    """{index, name, axes} for every detected joystick."""
    pg, _ = _init_pygame()
    if pg is None:
        return []
    try:
        pg.joystick.quit()
        pg.joystick.init()
        out = []
        for i in range(pg.joystick.get_count()):
            j = pg.joystick.Joystick(i)
            j.init()
            out.append({"index": i, "name": j.get_name(), "axes": j.get_numaxes()})
            j.quit()
        return out
    except Exception:
        return []


# -- poll loop ----------------------------------------------------------------

def _is_running():
    return _poll_thread is not None and _poll_thread.is_alive()


def _poll_loop(api, joystick, stop_event, poll_hz):
    import pygame
    interval = 1.0 / max(1, poll_hz)
    try:
        while not stop_event.is_set():
            t0 = time.monotonic()
            try:
                pygame.event.pump()
            except Exception:
                pass

            channels = {}
            for i in range(joystick.get_numaxes()):
                val = joystick.get_axis(i)
                raw = max(0, min(65535, int(_CENTER + val * _HALF)))
                channels[i + 1] = raw     # axis 0 -> CH1, axis 1 -> CH2, ...

            if channels:
                api.set_injected_channels(channels)

            wait = interval - (time.monotonic() - t0)
            if wait > 0:
                stop_event.wait(wait)
    except Exception:
        pass
    finally:
        try:
            api.clear_injected_channels()
        except Exception:
            pass
        try:
            joystick.quit()
        except Exception:
            pass


# -- start / stop -------------------------------------------------------------

def start(api):
    """Returns (True, info_msg) or (False, error_msg)."""
    global _poll_thread, _stop_event

    if _is_running():
        return True, "Already running."

    pg, err = _init_pygame()
    if pg is None:
        return False, f"pygame unavailable: {err}\nRun: pip install pygame"

    name = _config.get("device_name")
    if not name:
        return False, "No device selected."

    devices = list_devices()
    match   = next((d for d in devices if d["name"] == name), None)
    if match is None:
        available = ", ".join(d["name"] for d in devices) or "none found"
        return False, f"'{name}' not found.\nConnected: {available}"

    try:
        joy = pg.joystick.Joystick(match["index"])
        joy.init()
    except Exception as exc:
        return False, f"Could not open device: {exc}"

    _stop_event  = threading.Event()
    _poll_thread = threading.Thread(
        target=_poll_loop,
        args=(api, joy, _stop_event, int(_config.get("poll_rate_hz", 60))),
        daemon=True,
        name="usb_joy_poll",
    )
    _poll_thread.start()
    return True, f"Running -- {name}  ({match['axes']} axes -> CH1-CH{match['axes']})"


def stop():
    global _poll_thread
    _stop_event.set()
    if _poll_thread:
        _poll_thread.join(timeout=2.0)
        _poll_thread = None


# -- plugin entry point -------------------------------------------------------

def setup(api):
    _load_config()

    api.events.subscribe("controller.stopped", lambda n, p: stop())

    try:
        import tkinter as tk
        from tkinter import ttk, messagebox

        def _build_tab(parent, api):
            parent.columnconfigure(0, weight=1)

            ttk.Label(parent, text="USB Joystick Input", font=("Segoe UI", 13, "bold")).grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(
                parent,
                text=(
                    "Use any USB-connected transmitter as the channel source instead of the "
                    "Arduino bridge.  Each axis maps directly to a channel "
                    "(Axis 0 = CH1, Axis 1 = CH2, ...).  Start the source, then calibrate "
                    "inputs normally in the Inputs tab -- no Arduino required."
                ),
                wraplength=780, justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(4, 12))

            # -- device picker ------------------------------------------------
            pick = ttk.LabelFrame(parent, text="Device", padding=8)
            pick.grid(row=2, column=0, sticky="ew", pady=(0, 8))
            pick.columnconfigure(0, weight=1)

            _devs      = []
            device_var = tk.StringVar(value=_config.get("device_name") or "")
            info_var   = tk.StringVar(value="")

            combo = ttk.Combobox(pick, textvariable=device_var, state="readonly")
            combo.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
            ttk.Label(pick, textvariable=info_var, foreground="#555").grid(
                row=1, column=0, sticky="w"
            )

            def _refresh():
                nonlocal _devs
                pg, err = _init_pygame()
                if pg is None:
                    messagebox.showwarning("USB Input",
                        f"pygame unavailable: {err}\nRun: pip install pygame")
                    return
                _devs = list_devices()
                names = [d["name"] for d in _devs]
                combo["values"] = names
                if device_var.get() not in names and names:
                    device_var.set(names[0])
                _update_info()

            def _update_info(*_):
                m = next((d for d in _devs if d["name"] == device_var.get()), None)
                info_var.set(f"{m['axes']} axes -> CH1-CH{m['axes']}" if m else "")

            combo.bind("<<ComboboxSelected>>", _update_info)
            ttk.Button(pick, text="Refresh", command=_refresh).grid(
                row=2, column=0, sticky="w", pady=(6, 0)
            )

            # -- start / stop -------------------------------------------------
            ctrl = ttk.Frame(parent)
            ctrl.grid(row=3, column=0, sticky="ew", pady=(0, 8))

            status_var = tk.StringVar(value="Stopped")
            ttk.Label(ctrl, textvariable=status_var, width=55).pack(side="left")
            start_btn = ttk.Button(ctrl, text="Start", width=10)
            stop_btn  = ttk.Button(ctrl, text="Stop",  width=10, state="disabled")
            start_btn.pack(side="left", padx=(8, 4))
            stop_btn.pack(side="left")

            def _on_start():
                _config["device_name"] = device_var.get()
                _save_config()
                ok, msg = start(api)
                status_var.set(msg)
                if ok:
                    start_btn.config(state="disabled")
                    stop_btn.config(state="normal")
                    _schedule_live()
                else:
                    messagebox.showerror("USB Input", msg, parent=parent)

            def _on_stop():
                stop()
                status_var.set("Stopped")
                start_btn.config(state="normal")
                stop_btn.config(state="disabled")
                for bar, lbl in _bars:
                    bar["value"] = 50
                    lbl.set("--")

            start_btn.config(command=_on_start)
            stop_btn.config(command=_on_stop)
            if _is_running():
                start_btn.config(state="disabled")
                stop_btn.config(state="normal")
                status_var.set(f"Running -- {_config.get('device_name', '')}")

            # -- live channel bars --------------------------------------------
            live = ttk.LabelFrame(parent, text="Live channel values", padding=8)
            live.grid(row=4, column=0, sticky="ew")
            live.columnconfigure(1, weight=1)

            _bars = []

            def _build_bars(n):
                for w in live.winfo_children():
                    w.destroy()
                _bars.clear()
                for i in range(n):
                    ttk.Label(live, text=f"CH{i+1}", width=6).grid(
                        row=i, column=0, sticky="w", pady=1
                    )
                    bar = ttk.Progressbar(live, orient="horizontal",
                                          mode="determinate", maximum=100, length=240)
                    bar.grid(row=i, column=1, sticky="ew", padx=(4, 4), pady=1)
                    lbl = tk.StringVar(value="--")
                    ttk.Label(live, textvariable=lbl, width=8).grid(
                        row=i, column=2, sticky="w"
                    )
                    _bars.append((bar, lbl))

            _live_active = [False]

            def _schedule_live():
                if not _live_active[0]:
                    _live_active[0] = True
                    _tick_live()

            def _tick_live():
                if not _is_running():
                    _live_active[0] = False
                    return
                inj = api.get_injected_channels()
                if inj:
                    n = max(inj.keys()) if inj else 0
                    if len(_bars) != n:
                        _build_bars(n)
                    for i, (bar, lbl) in enumerate(_bars):
                        raw  = inj.get(i + 1, _CENTER)
                        frac = (raw - (_CENTER - _HALF)) / (2 * _HALF)
                        bar["value"] = max(0.0, min(100.0, frac * 100))
                        norm = (raw - _CENTER) / _HALF
                        lbl.set(f"{norm:+.3f}")
                try:
                    parent.after(80, _tick_live)
                except Exception:
                    _live_active[0] = False

            if _is_running():
                _schedule_live()

            _refresh()

        api.register_tab("USB Input", _build_tab, order=150)

    except Exception:
        pass

    return {"name": "USB Joystick Input", "version": "2.0"}
