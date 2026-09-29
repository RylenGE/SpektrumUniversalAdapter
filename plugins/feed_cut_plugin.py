"""
Feed Cut Plugin
===============
Uses one of the profile's configured Multi-State Switch inputs as a gate.
One specific state means "inputs ON" — any other state blocks all inputs and
holds the virtual controller at neutral.

This means you pick a switch you have already calibrated in the Inputs tab,
choose which position (state) should enable the controller, and the plugin
does the rest.  No raw channel numbers or thresholds to guess at.

  Selected state  →  inputs pass through to virtual controller (drone mode)
  Any other state →  inputs blocked, controller held at neutral (keyboard mode)

The virtual controller stays active at all times — no Steam disconnect/reconnect.

Config: plugins/feed_cut_plugin.json
  {
      "enabled": true,
      "input_id": "<id of a multi_state input in the profile>",
      "pass_state_index": 0,
      "start_blocked": true
  }
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("feed_cut_plugin.json")

DEFAULT_CONFIG = {
    "enabled": True,
    "input_id": None,
    "pass_state_index": 0,
    "start_blocked": True,
}

_config = dict(DEFAULT_CONFIG)
_gate_state = None  # True = passing, False = blocked, None = not yet applied


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _get_multi_state_inputs(api):
    return [
        inp for inp in api.get_profile().get("inputs", [])
        if inp.get("type") == "multi_state"
    ]


def _find_input(api):
    """Return the configured multi_state input definition, or None."""
    target = _config.get("input_id")
    if not target:
        return None
    for inp in _get_multi_state_inputs(api):
        if inp.get("id") == target:
            return inp
    return None


def _active_state_index(definition, channels):
    """Return the index of the nearest state for the current channel value."""
    ch = int(definition.get("source", {}).get("channel", 0))
    raw = channels.get(ch)
    if raw is None:
        return None
    states = definition.get("states", [])
    if not states:
        return None
    return min(range(len(states)), key=lambda i: abs(raw - int(states[i].get("value", 0))))


def _gate(api, should_pass):
    """Open or close the input gate; only acts when state actually changes."""
    global _gate_state
    if should_pass == _gate_state:
        return
    _gate_state = should_pass
    api.set_profile_input_passthrough(should_pass)
    if not should_pass:
        api.reset_virtual_output()


# ── event handler ─────────────────────────────────────────────────────────────

def _on_channels(api, _event, payload):
    if not _config.get("enabled", True):
        _gate(api, True)
        return

    definition = _find_input(api)
    if definition is None:
        # No switch configured → always pass through
        _gate(api, True)
        return

    channels = payload.get("channels", {})
    idx = _active_state_index(definition, channels)
    if idx is None:
        return  # channel not present in this packet yet

    _gate(api, idx == int(_config.get("pass_state_index", 0)))


# ── plugin entry point ────────────────────────────────────────────────────────

def setup(api):
    _load_config()

    global _gate_state
    if _config.get("start_blocked", True):
        _gate_state = None
        api.set_profile_input_passthrough(False)
        api.reset_virtual_output()

    api.events.subscribe("channels.changed", lambda n, p: _on_channels(api, n, p))
    api.events.subscribe("controller.stopped", lambda n, p: api.set_profile_input_passthrough(True))

    def _reset_gate(n, p):
        global _gate_state
        _gate_state = None
    api.events.subscribe("profile.loaded", _reset_gate)
    api.events.subscribe("profile.changed", _reset_gate)

    # ── UI tab ────────────────────────────────────────────────────────────────
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox

        def _build_tab(parent, api):
            parent.columnconfigure(0, weight=1)

            ttk.Label(parent, text="Feed Cut", font=("Segoe UI", 13, "bold")).grid(
                row=0, column=0, sticky="w"
            )
            ttk.Label(
                parent,
                text=(
                    "Pick one of your calibrated Multi-State Switch inputs and choose "
                    "which state should allow inputs through to the virtual controller.  "
                    "Every other state blocks inputs and holds the controller at neutral."
                ),
                wraplength=780, justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(4, 12))

            # ── controls ──────────────────────────────────────────────────────
            form = ttk.Frame(parent)
            form.grid(row=2, column=0, sticky="ew")
            form.columnconfigure(1, weight=1)

            enabled_var = tk.BooleanVar(value=bool(_config.get("enabled", True)))
            start_blocked_var = tk.BooleanVar(value=bool(_config.get("start_blocked", True)))

            ttk.Checkbutton(form, text="Enabled", variable=enabled_var).grid(
                row=0, column=0, columnspan=2, sticky="w", pady=2
            )
            ttk.Checkbutton(
                form, text="Start blocked until switch is ON", variable=start_blocked_var
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

            ttk.Label(form, text="Switch input").grid(row=2, column=0, sticky="w", pady=(10, 4))
            input_var = tk.StringVar()
            input_combo = ttk.Combobox(form, textvariable=input_var, state="readonly", width=40)
            input_combo.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(10, 4))

            ttk.Label(form, text="ON state").grid(row=3, column=0, sticky="w", pady=4)
            state_var = tk.StringVar()
            state_combo = ttk.Combobox(form, textvariable=state_var, state="readonly", width=40)
            state_combo.grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=4)

            # ── helpers ───────────────────────────────────────────────────────
            _inputs_by_label = {}

            def _refresh_states(*_):
                inp = _inputs_by_label.get(input_var.get())
                if not inp:
                    state_combo["values"] = []
                    state_var.set("")
                    return
                states = inp.get("states", [])
                labels = [
                    f"[{i}]  {s.get('label', f'State {i+1}')}  (raw {s.get('value', '?')})"
                    for i, s in enumerate(states)
                ]
                state_combo["values"] = labels
                wanted = int(_config.get("pass_state_index", 0))
                wanted = max(0, min(wanted, len(labels) - 1))
                if labels:
                    state_var.set(labels[wanted])

            def _refresh_inputs():
                nonlocal _inputs_by_label
                inputs = _get_multi_state_inputs(api)
                _inputs_by_label = {
                    f"{inp.get('name', 'Switch')}  (CH{inp.get('source', {}).get('channel', '?')})": inp
                    for inp in inputs
                }
                labels = list(_inputs_by_label.keys())
                input_combo["values"] = labels

                # pre-select saved input
                saved_id = _config.get("input_id")
                match = next(
                    (lbl for lbl, inp in _inputs_by_label.items() if inp.get("id") == saved_id),
                    labels[0] if labels else "",
                )
                input_var.set(match)
                _refresh_states()

            input_combo.bind("<<ComboboxSelected>>", _refresh_states)

            def _save():
                global _gate_state
                inp = _inputs_by_label.get(input_var.get())
                if inp is None:
                    messagebox.showerror("Feed Cut", "Select a Multi-State input first.", parent=parent)
                    return
                state_labels = list(state_combo["values"])
                try:
                    state_idx = state_labels.index(state_var.get())
                except ValueError:
                    state_idx = 0
                _config["enabled"] = bool(enabled_var.get())
                _config["start_blocked"] = bool(start_blocked_var.get())
                _config["input_id"] = inp.get("id")
                _config["pass_state_index"] = state_idx
                _gate_state = None
                _save_config()
                messagebox.showinfo("Feed Cut", "Settings saved.", parent=parent)

            btn_row = ttk.Frame(parent)
            btn_row.grid(row=3, column=0, sticky="w", pady=(12, 0))
            ttk.Button(btn_row, text="Refresh", command=_refresh_inputs).pack(side="left")
            ttk.Button(btn_row, text="Save", command=_save).pack(side="left", padx=(8, 0))

            _refresh_inputs()

        api.register_tab("Feed Cut", _build_tab, order=200)
    except Exception:
        pass

    return {"name": "Feed Cut Plugin", "version": "2.0"}
