"""
Feed Cut Plugin
===============
Monitors one channel (the "gate switch") and gates all profile input
to the virtual controller based on the channel's position.

  Switch ON  (channel >= threshold) → normal inputs flow through
  Switch OFF (channel <  threshold) → inputs blocked, controller held at neutral

The virtual controller stays active the whole time — no connect/disconnect
delay.  This is the fix for the original use case: flip a switch on the RC
transmitter to go between keyboard-only (drone in flight) and controller mode
(piloting the drone).

Config file: plugins/feed_cut_plugin.json
  {
      "enabled": true,
      "channel": 5,          # which RC channel to watch
      "on_threshold": 32768, # raw value; channel >= threshold means ON
      "start_blocked": true  # block until switch is explicitly ON on startup
  }
"""

import json
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("feed_cut_plugin.json")

DEFAULT_CONFIG = {
    "enabled": True,
    "channel": 5,
    "on_threshold": 32768,
    "start_blocked": True,
}

_config = dict(DEFAULT_CONFIG)
_current_state = None   # None = not yet determined


def _load_config():
    global _config
    try:
        if CONFIG_PATH.exists():
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_CONFIG)
            merged.update(loaded)
            _config = merged
    except Exception:
        pass


def _save_config():
    try:
        CONFIG_PATH.write_text(
            json.dumps(_config, indent=4),
            encoding="utf-8",
        )
    except Exception:
        pass


def _gate(api, should_pass):
    """Apply or remove the gate and update controller output accordingly."""
    global _current_state

    if should_pass == _current_state:
        return

    _current_state = should_pass
    api.set_profile_input_passthrough(should_pass)

    if not should_pass:
        api.reset_virtual_output()


def _on_channels(api, event_name, payload):
    """Called every time a new receiver packet arrives."""
    if not _config.get("enabled", True):
        _gate(api, True)
        return

    channels = payload.get("channels", {})
    ch = int(_config.get("channel", 5))
    threshold = int(_config.get("on_threshold", 32768))

    raw = channels.get(ch)
    if raw is None:
        return

    _gate(api, bool(raw >= threshold))

    if not _config.get("enabled", True):
        _gate(api, True)
        return

    channels = payload.get("channels", {})
    ch = int(_config.get("channel", 5))
    threshold = int(_config.get("on_threshold", 32768))

    raw = channels.get(ch)
    if raw is None:
        return

    _gate(api, bool(raw >= threshold))


def setup(api):
    _load_config()

    global _current_state
    # Start blocked until the switch is proven ON (avoids surprise inputs)
    if _config.get("start_blocked", True):
        _current_state = None   # forces the first gate call to actually apply
        api.set_profile_input_passthrough(False)
        api.reset_virtual_output()

    api.events.subscribe(
        "channels.changed",
        lambda name, payload: _on_channels(api, name, payload),
    )

    # Restore full pass-through when controller stops so the app stays usable
    api.events.subscribe(
        "controller.stopped",
        lambda name, payload: api.set_profile_input_passthrough(True),
    )

    # Reset gate state when profile changes so the switch is re-evaluated
    def _on_profile(name, payload):
        global _current_state
        _current_state = None
    api.events.subscribe("profile.loaded", _on_profile)
    api.events.subscribe("profile.changed", _on_profile)

    # ── Optional UI tab ──────────────────────────────────────────────────────
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox

        def _build_tab(parent, api):
            parent.columnconfigure(0, weight=1)

            ttk.Label(
                parent,
                text="Feed Cut Plugin",
                font=("Segoe UI", 13, "bold"),
            ).grid(row=0, column=0, sticky="w")

            ttk.Label(
                parent,
                text=(
                    "Gates all RC inputs to the virtual controller via one channel.\n"
                    "Channel >= threshold  →  inputs pass through (drone mode).\n"
                    "Channel <  threshold  →  inputs blocked, controller held neutral (keyboard mode)."
                ),
                wraplength=760,
                justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(6, 14))

            form = ttk.Frame(parent)
            form.grid(row=2, column=0, sticky="ew")
            form.columnconfigure(1, weight=1)

            enabled_var = tk.BooleanVar(value=bool(_config.get("enabled", True)))
            start_blocked_var = tk.BooleanVar(value=bool(_config.get("start_blocked", True)))
            channel_var = tk.StringVar(value=str(_config.get("channel", 5)))
            threshold_var = tk.StringVar(value=str(_config.get("on_threshold", 32768)))

            ttk.Checkbutton(form, text="Enabled", variable=enabled_var).grid(
                row=0, column=0, columnspan=2, sticky="w", pady=4
            )
            ttk.Checkbutton(form, text="Start blocked (safe)", variable=start_blocked_var).grid(
                row=1, column=0, columnspan=2, sticky="w", pady=4
            )
            ttk.Label(form, text="Gate channel (0-indexed)").grid(row=2, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=channel_var, width=10).grid(row=2, column=1, sticky="w", padx=8)
            ttk.Label(form, text="ON threshold (raw ≥ value)").grid(row=3, column=0, sticky="w", pady=4)
            ttk.Entry(form, textvariable=threshold_var, width=10).grid(row=3, column=1, sticky="w", padx=8)

            status_var = tk.StringVar(value="")

            def _save():
                global _current_state
                try:
                    _config["enabled"] = bool(enabled_var.get())
                    _config["start_blocked"] = bool(start_blocked_var.get())
                    _config["channel"] = int(channel_var.get())
                    _config["on_threshold"] = int(threshold_var.get())
                    _current_state = None
                    _save_config()
                    status_var.set("Saved.")
                except ValueError as e:
                    messagebox.showerror("Feed Cut Plugin", f"Invalid value: {e}", parent=parent)

            ttk.Button(form, text="Save", command=_save).grid(
                row=4, column=0, sticky="w", pady=(12, 0)
            )
            ttk.Label(parent, textvariable=status_var).grid(row=3, column=0, sticky="w", pady=(8, 0))

        api.register_tab("Feed Cut", _build_tab, order=200)
    except Exception:
        pass  # No UI if tkinter not available

    return {
        "name": "Feed Cut Plugin",
        "version": "1.0",
    }
