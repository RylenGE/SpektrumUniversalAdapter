import copy
import json
import statistics
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import serial.tools.list_ports

from .gamepad import BUTTON_NAMES
from .profile_manager import new_id


AXIS_TARGETS = ["left_x", "left_y", "right_x", "right_y"]
TRIGGER_TARGETS = ["left_trigger", "right_trigger"]
BUTTON_TARGETS = BUTTON_NAMES
OUTPUT_KINDS = ["none", "axis", "trigger", "button"]


def output_text(output):
    if not output:
        return "None"
    kind = output.get("type", "none")
    target = output.get("target")
    return "None" if kind == "none" else f"{kind}: {target}"


class SnapshotWizard(tk.Toplevel):
    """
    Generic calibration wizard that records ALL channels at every step.
    Finalizers can therefore auto-detect which channel moved.
    """

    def __init__(self, app, title, steps, finish_callback):
        super().__init__(app.root)
        self.app = app
        self.steps = steps
        self.finish_callback = finish_callback
        self.index = 0
        self.records = {}

        self.title(title)
        self.transient(app.root)
        self.grab_set()
        self.resizable(False, False)

        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        self.instruction = ttk.Label(outer, text="", font=("Segoe UI", 13, "bold"), wraplength=500)
        self.instruction.pack(anchor="w")

        self.detail = ttk.Label(
            outer,
            text="Set the physical control as requested, keep it still, then click Record.",
            wraplength=500,
        )
        self.detail.pack(anchor="w", pady=(8, 12))

        self.progress = ttk.Label(outer, text="")
        self.progress.pack(anchor="w")

        self.result = ttk.Label(outer, text="")
        self.result.pack(anchor="w", pady=(8, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(anchor="e", pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=(0, 8))
        self.record_btn = ttk.Button(buttons, text="Record", command=self.record)
        self.record_btn.pack(side="left")

        self.show_step()

    def show_step(self):
        if self.index >= len(self.steps):
            try:
                self.finish_callback(self.records)
                self.destroy()
            except Exception as exc:
                messagebox.showerror("Calibration", str(exc), parent=self)
            return

        step = self.steps[self.index]
        self.instruction.config(text=step["instruction"])
        self.progress.config(text=f"Step {self.index + 1} of {len(self.steps)}")
        self.result.config(text="")
        self.record_btn.config(text="Record", state="normal")

    def record(self):
        has_receiver = bool(self.app.core.receiver and self.app.core.receiver.is_alive())
        has_injection = self.app.core.get_injected_channels() is not None
        if not has_receiver and not has_injection:
            messagebox.showerror(
                "Calibration",
                "No signal source is active.\n\n"
                "Connect the Arduino receiver  OR  start USB Input from the USB Input tab.",
                parent=self,
            )
            return

        self.record_btn.config(text="Sampling…", state="disabled")
        started = time.monotonic()

        def finish():
            history = self.app.core.channel_history_since(started)
            if len(history) < 5:
                self.result.config(text="Not enough packets. Try again.")
                self.record_btn.config(text="Record", state="normal")
                return

            all_channels = set()
            for item in history:
                all_channels.update(item["channels"])

            medians = {}
            for ch in sorted(all_channels):
                values = [
                    item["channels"][ch]
                    for item in history
                    if ch in item["channels"]
                ]
                if values:
                    medians[ch] = int(statistics.median(values))

            self.records[self.steps[self.index]["key"]] = medians
            self.result.config(text=f"Recorded {len(medians)} channels")
            self.index += 1
            self.after(250, self.show_step)

        self.after(650, finish)


def detect_channel(records):
    channels = set()
    for snapshot in records.values():
        channels.update(snapshot)

    best_channel = None
    best_span = -1

    for ch in channels:
        values = [snapshot[ch] for snapshot in records.values() if ch in snapshot]
        if len(values) < 2:
            continue
        span = max(values) - min(values)
        if span > best_span:
            best_span = span
            best_channel = ch

    if best_channel is None or best_span <= 0:
        raise RuntimeError("No changing channel was detected.")

    return best_channel


class OutputEditor(ttk.Frame):
    def __init__(self, master, initial=None, allowed=None):
        super().__init__(master)
        initial = initial or {"type": "none", "target": None}
        self.allowed = allowed or OUTPUT_KINDS

        self.kind = tk.StringVar(value=initial.get("type", "none"))
        self.target = tk.StringVar(value=initial.get("target") or "")

        self.kind_combo = ttk.Combobox(self, textvariable=self.kind, values=self.allowed, state="readonly", width=10)
        self.kind_combo.pack(side="left")

        self.target_combo = ttk.Combobox(self, textvariable=self.target, state="readonly", width=18)
        self.target_combo.pack(side="left", padx=(6, 0))

        self.kind_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_targets())
        self.refresh_targets()

    def refresh_targets(self):
        kind = self.kind.get()
        if kind == "axis":
            values = AXIS_TARGETS
        elif kind == "trigger":
            values = TRIGGER_TARGETS
        elif kind == "button":
            values = BUTTON_TARGETS
        else:
            values = [""]

        self.target_combo["values"] = values
        if kind == "none":
            self.target.set("")
        elif self.target.get() not in values:
            self.target.set(values[0] if values else "")

    def get(self):
        kind = self.kind.get()
        return {
            "type": kind,
            "target": None if kind == "none" else self.target.get(),
        }


class InputEditor(tk.Toplevel):
    def __init__(self, app, definition, on_save):
        super().__init__(app.root)
        self.app = app
        self.definition = copy.deepcopy(definition)
        self.original_definition = copy.deepcopy(definition)
        self.on_save = on_save
        self.title("Edit Input")
        self.transient(app.root)
        self.grab_set()

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        self.name_var = tk.StringVar(value=self.definition.get("name", "Input"))
        self.enabled_var = tk.BooleanVar(value=self.definition.get("enabled", True))

        ttk.Label(outer, text="Name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(outer, textvariable=self.name_var, width=32).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Checkbutton(outer, text="Enabled", variable=self.enabled_var).grid(row=0, column=2, padx=8)

        ttk.Label(outer, text=f"Type: {self.definition.get('type')}").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))
        ttk.Label(outer, text=f"Source channel: CH{self.definition.get('source', {}).get('channel', '?')}").grid(row=2, column=0, columnspan=3, sticky="w")

        self.widgets = {}
        kind = self.definition.get("type")

        row = 3

        if kind in ("range", "centered_axis"):
            transform = self.definition.setdefault("transform", {})
            self.widgets["invert"] = tk.BooleanVar(value=bool(transform.get("invert", False)))
            self.widgets["expo"] = tk.DoubleVar(value=float(transform.get("expo", 0.0)) * 100)
            self.widgets["sensitivity"] = tk.DoubleVar(value=float(transform.get("sensitivity", 1.0)))
            self.widgets["deadzone"] = tk.DoubleVar(value=float(transform.get("deadzone", 0.0)) * 100)

            ttk.Checkbutton(
                outer,
                text="Invert",
                variable=self.widgets["invert"],
                command=self.preview_live_changes,
            ).grid(row=row, column=0, sticky="w", pady=4)
            row += 1

            ttk.Label(outer, text="Expo").grid(row=row, column=0, sticky="w")
            expo_scale = ttk.Scale(
                outer,
                from_=0,
                to=100,
                variable=self.widgets["expo"],
                command=lambda _=None: self.preview_live_changes(),
            )
            expo_scale.grid(row=row, column=1, sticky="ew", padx=(6, 8))
            self.widgets["expo_label"] = tk.StringVar()
            ttk.Label(outer, textvariable=self.widgets["expo_label"], width=9).grid(row=row, column=2, sticky="w")
            row += 1

            ttk.Label(outer, text="Sensitivity").grid(row=row, column=0, sticky="w")
            sensitivity_scale = ttk.Scale(
                outer,
                from_=0.1,
                to=2.0,
                variable=self.widgets["sensitivity"],
                command=lambda _=None: self.preview_live_changes(),
            )
            sensitivity_scale.grid(row=row, column=1, sticky="ew", padx=(6, 8))
            self.widgets["sensitivity_label"] = tk.StringVar()
            ttk.Label(outer, textvariable=self.widgets["sensitivity_label"], width=9).grid(row=row, column=2, sticky="w")
            row += 1

            if kind == "centered_axis":
                ttk.Label(outer, text="Deadzone").grid(row=row, column=0, sticky="w")
                deadzone_scale = ttk.Scale(
                    outer,
                    from_=0,
                    to=25,
                    variable=self.widgets["deadzone"],
                    command=lambda _=None: self.preview_live_changes(),
                )
                deadzone_scale.grid(row=row, column=1, sticky="ew", padx=(6, 8))
                self.widgets["deadzone_label"] = tk.StringVar()
                ttk.Label(outer, textvariable=self.widgets["deadzone_label"], width=9).grid(row=row, column=2, sticky="w")
                row += 1

            outer.columnconfigure(1, weight=1)
            self.preview_live_changes()

            if kind == "range":
                self.widgets["safe_min"] = tk.BooleanVar(
                    value=bool(self.definition.setdefault("options", {}).get("safe_min", False))
                )
                ttk.Checkbutton(
                    outer,
                    text="On signal loss, drive this range to minimum (use for throttle)",
                    variable=self.widgets["safe_min"],
                ).grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
                row += 1

            ttk.Label(outer, text="Output").grid(row=row, column=0, sticky="w")
            allowed = ["axis", "trigger"] if kind == "range" else ["axis"]
            self.output_editor = OutputEditor(outer, self.definition.get("output"), allowed=allowed)
            self.output_editor.grid(row=row, column=1, columnspan=2, sticky="w", pady=4)
            row += 1

        elif kind == "button":
            ttk.Label(outer, text="Output").grid(row=row, column=0, sticky="w")
            self.output_editor = OutputEditor(outer, self.definition.get("output"), allowed=["button", "trigger"])
            self.output_editor.grid(row=row, column=1, columnspan=2, sticky="w", pady=4)
            row += 1

        elif kind == "multi_state":
            ttk.Label(outer, text="State mappings").grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))
            row += 1
            self.state_editors = []
            for state in self.definition.get("states", []):
                ttk.Label(outer, text=f"{state.get('label', 'State')}  ({state.get('value')})").grid(row=row, column=0, sticky="w", pady=2)
                editor = OutputEditor(outer, state.get("output"), allowed=["none", "button", "trigger"])
                editor.grid(row=row, column=1, columnspan=2, sticky="w")
                self.state_editors.append((state, editor))
                row += 1

        elif kind == "delta":
            ttk.Label(outer, text="Positive step output").grid(row=row, column=0, sticky="w")
            self.pos_editor = OutputEditor(outer, self.definition.get("positive_output"), allowed=["none", "button"])
            self.pos_editor.grid(row=row, column=1, columnspan=2, sticky="w")
            row += 1
            ttk.Label(outer, text="Negative step output").grid(row=row, column=0, sticky="w")
            self.neg_editor = OutputEditor(outer, self.definition.get("negative_output"), allowed=["none", "button"])
            self.neg_editor.grid(row=row, column=1, columnspan=2, sticky="w")
            row += 1

        custom = self.app.core.input_types.get(kind)
        if custom and custom.get("editor_factory"):
            custom["editor_factory"](outer, self.definition, self.app.api)

        buttons = ttk.Frame(outer)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left")

    def cancel(self):
        # Undo unsaved live-preview tuning.
        profile = self.app.core.profiles.get()
        for i, item in enumerate(profile.get("inputs", [])):
            if item.get("id") == self.original_definition.get("id"):
                profile["inputs"][i] = copy.deepcopy(self.original_definition)
                self.app.core.profiles.replace(profile, save=False)
                break
        self.destroy()

    def preview_live_changes(self):
        """Apply analog tuning to the in-memory active profile while this editor is open."""
        kind = self.definition.get("type")
        if kind not in ("range", "centered_axis"):
            return

        try:
            transform = self.definition.setdefault("transform", {})
            transform["invert"] = bool(self.widgets["invert"].get())
            transform["expo"] = max(0.0, min(1.0, float(self.widgets["expo"].get()) / 100.0))
            transform["sensitivity"] = max(0.1, min(2.0, float(self.widgets["sensitivity"].get())))

            if kind == "centered_axis":
                transform["deadzone"] = max(
                    0.0,
                    min(0.49, float(self.widgets["deadzone"].get()) / 100.0),
                )

            if "expo_label" in self.widgets:
                self.widgets["expo_label"].set(f"{float(self.widgets['expo'].get()):.1f}%")
            if "sensitivity_label" in self.widgets:
                self.widgets["sensitivity_label"].set(f"{float(self.widgets['sensitivity'].get()):.2f}x")
            if "deadzone_label" in self.widgets:
                self.widgets["deadzone_label"].set(f"{float(self.widgets['deadzone'].get()):.2f}%")

            # Update the active profile without touching disk. The controller and
            # Live Inputs tab immediately use the new values.
            profile = self.app.core.profiles.get()
            for i, item in enumerate(profile.get("inputs", [])):
                if item.get("id") == self.definition.get("id"):
                    profile["inputs"][i] = copy.deepcopy(self.definition)
                    self.app.core.profiles.replace(profile, save=False)
                    break
        except Exception:
            pass

    def save(self):
        self.definition["name"] = self.name_var.get().strip() or "Input"
        self.definition["enabled"] = bool(self.enabled_var.get())

        kind = self.definition.get("type")

        if kind in ("range", "centered_axis"):
            transform = self.definition.setdefault("transform", {})
            transform["invert"] = bool(self.widgets["invert"].get())
            transform["expo"] = max(0.0, min(1.0, float(self.widgets["expo"].get()) / 100.0))
            transform["sensitivity"] = max(0.1, min(2.0, float(self.widgets["sensitivity"].get())))
            if kind == "centered_axis":
                transform["deadzone"] = max(0.0, min(0.49, float(self.widgets["deadzone"].get()) / 100.0))
            if kind == "range":
                self.definition.setdefault("options", {})["safe_min"] = bool(self.widgets["safe_min"].get())
            self.definition["output"] = self.output_editor.get()

        elif kind == "button":
            self.definition["output"] = self.output_editor.get()

        elif kind == "multi_state":
            for state, editor in self.state_editors:
                state["output"] = editor.get()

        elif kind == "delta":
            self.definition["positive_output"] = self.pos_editor.get()
            self.definition["negative_output"] = self.neg_editor.get()

        self.on_save(self.definition)
        self.destroy()


class AddInputDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Add Input")
        self.transient(app.root)
        self.grab_set()
        self.resizable(False, False)

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        self.name_var = tk.StringVar(value="New Input")
        types = app.core.input_types.all()
        self.display_to_id = {
            info["display_name"]: type_id
            for type_id, info in types.items()
        }

        self.type_var = tk.StringVar(value=next(iter(self.display_to_id), ""))

        ttk.Label(outer, text="Name").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(outer, textvariable=self.name_var, width=30).grid(row=0, column=1, sticky="ew")

        ttk.Label(outer, text="Input type").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(
            outer,
            textvariable=self.type_var,
            values=list(self.display_to_id),
            state="readonly",
            width=28,
        ).grid(row=1, column=1, sticky="ew")

        self.state_count = tk.IntVar(value=3)
        ttk.Label(outer, text="Multi-state count").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(outer, from_=2, to=8, textvariable=self.state_count, width=8).grid(row=2, column=1, sticky="w")

        ttk.Label(
            outer,
            text="The calibration wizard records all available channels and automatically chooses the one that actually moved.",
            wraplength=420,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(outer)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Create + Calibrate", command=self.begin).pack(side="left")

    def begin(self):
        type_id = self.display_to_id.get(self.type_var.get())
        if not type_id:
            return

        name = self.name_var.get().strip() or "Input"
        state_count = max(2, min(8, int(self.state_count.get())))
        self.destroy()
        self.app.calibrate_new_input(type_id, name, state_count)


class UniversalUI:
    def __init__(self, root, core, api, plugin_loader, http_server):
        self.root = root
        self.core = core
        self.api = api
        self.plugin_loader = plugin_loader
        self.http_server = http_server

        self.root.title("Spektrum Universal Adapter")
        self.root.minsize(960, 680)

        self.port_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Disconnected")
        self.controller_var = tk.StringVar(value="Stopped")
        self.profile_var = tk.StringVar()
        self.packet_rate_var = tk.StringVar(value="0/s")
        self.bad_var = tk.StringVar(value="0")
        self.missing_var = tk.StringVar(value="0")
        self.loss_var = tk.StringVar(value="—")
        self.rssi_var = tk.StringVar(value="—")
        self.protocol_var = tk.StringVar(value="—")

        # Dynamically created live input monitor widgets.
        self.live_input_widgets = {}
        self.live_input_signature = None

        self._build_menu()
        self._build_ui()
        self.refresh_ports()
        self.refresh_profiles()
        self.refresh_inputs()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(20, self.tick)
        self.root.after(1500, self.auto_connect_tick)

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Profile", command=self.save_profile)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)

        addon_menu = tk.Menu(menubar, tearoff=0)
        for item in self.core.ui_registry.menu_actions():
            addon_menu.add_command(label=item["label"], command=lambda cb=item["callback"]: cb(self.api))
        menubar.add_cascade(label="Addons", menu=addon_menu)

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.dashboard_tab = ttk.Frame(notebook, padding=12)
        self.live_tab = ttk.Frame(notebook, padding=12)
        self.inputs_tab = ttk.Frame(notebook, padding=12)
        self.channels_tab = ttk.Frame(notebook, padding=12)
        self.api_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.dashboard_tab, text="Dashboard")
        notebook.add(self.live_tab, text="Live Inputs")
        notebook.add(self.inputs_tab, text="Inputs")
        notebook.add(self.channels_tab, text="Raw Channels")
        notebook.add(self.api_tab, text="API / Addons")

        self._dashboard()
        self._live_inputs()
        self._inputs()
        self._channels()
        self._api_tab()

        for item in self.core.ui_registry.tabs():
            frame = ttk.Frame(notebook, padding=12)
            notebook.add(frame, text=item["title"])
            try:
                item["factory"](frame, self.api)
            except Exception as exc:
                ttk.Label(frame, text=f"Addon UI error:\n{exc}").pack(anchor="w")

    def _dashboard(self):
        tab = self.dashboard_tab
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)

        conn = ttk.LabelFrame(tab, text="Connection", padding=10)
        conn.grid(row=0, column=0, columnspan=2, sticky="ew")
        conn.columnconfigure(1, weight=1)

        ttk.Label(conn, text="Serial device").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(conn, textvariable=self.port_var, state="readonly", width=42)
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(conn, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=(0, 8))
        self.connect_btn = ttk.Button(conn, text="Connect", command=self.toggle_connection)
        self.connect_btn.grid(row=0, column=3)

        ttk.Label(conn, text="Receiver").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(conn, textvariable=self.status_var).grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))

        ttk.Label(conn, text="Virtual Xbox").grid(row=1, column=2, sticky="e", pady=(8, 0))
        ttk.Label(conn, textvariable=self.controller_var).grid(row=1, column=3, sticky="w", pady=(8, 0))

        self.controller_btn = ttk.Button(conn, text="Start Controller", command=self.toggle_controller)
        self.controller_btn.grid(row=2, column=3, sticky="e", pady=(8, 0))

        profile = ttk.LabelFrame(tab, text="Profile", padding=10)
        profile.grid(row=1, column=0, sticky="nsew", padx=(0, 5), pady=(10, 0))
        profile.columnconfigure(1, weight=1)

        ttk.Label(profile, text="Active").grid(row=0, column=0, sticky="w")
        self.profile_combo = ttk.Combobox(profile, textvariable=self.profile_var, state="readonly")
        self.profile_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda e: self.load_selected_profile())

        ttk.Button(profile, text="New", command=self.new_profile).grid(row=1, column=0, pady=(8, 0))
        ttk.Button(profile, text="Duplicate", command=self.duplicate_profile).grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Button(profile, text="Delete", command=self.delete_profile).grid(row=1, column=2, pady=(8, 0))

        stats = ttk.LabelFrame(tab, text="Live Status", padding=10)
        stats.grid(row=1, column=1, sticky="nsew", padx=(5, 0), pady=(10, 0))

        rows = [
            ("Bridge protocol", self.protocol_var),
            ("Packet rate", self.packet_rate_var),
            ("USB bad packets", self.bad_var),
            ("Missing PC sequence", self.missing_var),
            ("Receiver frame loss", self.loss_var),
            ("RSSI metadata", self.rssi_var),
        ]
        for r, (label, var) in enumerate(rows):
            ttk.Label(stats, text=label + ":").grid(row=r, column=0, sticky="w", pady=3)
            ttk.Label(stats, textvariable=var).grid(row=r, column=1, sticky="w", padx=(10, 0), pady=3)

        addon_area = ttk.Frame(tab)
        addon_area.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        for item in self.core.ui_registry.dashboard_cards():
            card = ttk.LabelFrame(addon_area, text=item["title"], padding=8)
            card.pack(fill="x", pady=4)
            try:
                item["factory"](card, self.api)
            except Exception as exc:
                ttk.Label(card, text=f"Addon error: {exc}").pack(anchor="w")

    def _live_inputs(self):
        tab = self.live_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        ttk.Label(
            tab,
            text=(
                "Every configured input appears here live. Analog/range inputs use the same "
                "centered or 0–100% style bars as the earlier adapter, while buttons and "
                "multi-state controls show their current state."
            ),
            wraplength=860,
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        # Canvas + inner frame lets a transmitter expose lots of controls without
        # forcing the whole app window to grow.
        holder = ttk.Frame(tab)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.live_canvas = tk.Canvas(holder, highlightthickness=0)
        self.live_canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.live_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.live_canvas.configure(yscrollcommand=scrollbar.set)

        self.live_inner = ttk.Frame(self.live_canvas)
        self.live_window = self.live_canvas.create_window((0, 0), window=self.live_inner, anchor="nw")

        self.live_inner.bind(
            "<Configure>",
            lambda e: self.live_canvas.configure(scrollregion=self.live_canvas.bbox("all")),
        )
        self.live_canvas.bind(
            "<Configure>",
            lambda e: self.live_canvas.itemconfigure(self.live_window, width=e.width),
        )

        self.rebuild_live_inputs(force=True)

    def _profile_input_signature(self):
        profile = self.core.profiles.get()
        signature = []
        for item in profile.get("inputs", []):
            signature.append(
                (
                    item.get("id"),
                    item.get("name"),
                    item.get("type"),
                    bool(item.get("enabled", True)),
                    item.get("source", {}).get("channel"),
                )
            )
        return tuple(signature)

    def rebuild_live_inputs(self, force=False):
        signature = self._profile_input_signature()
        if not force and signature == self.live_input_signature:
            return

        self.live_input_signature = signature

        for child in self.live_inner.winfo_children():
            child.destroy()
        self.live_input_widgets.clear()

        profile = self.core.profiles.get()
        inputs = profile.get("inputs", [])

        if not inputs:
            ttk.Label(
                self.live_inner,
                text="No inputs yet. Open the Inputs tab and click + Add Input.",
            ).pack(anchor="w", pady=8)
            return

        for definition in inputs:
            card = ttk.LabelFrame(
                self.live_inner,
                text=definition.get("name", "Input"),
                padding=10,
            )
            card.pack(fill="x", pady=5)

            card.columnconfigure(1, weight=1)

            type_info = self.core.input_types.get(definition.get("type"))
            type_name = type_info["display_name"] if type_info else definition.get("type", "Unknown")
            channel = definition.get("source", {}).get("channel", "?")

            subtitle = ttk.Label(
                card,
                text=f"{type_name}   •   CH{channel}   •   {output_text(definition.get('output')) if definition.get('type') not in ('multi_state', 'delta') else 'custom mapping'}",
            )
            subtitle.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))

            raw_var = tk.StringVar(value="raw —")
            value_var = tk.StringVar(value="—")

            kind = definition.get("type")

            if kind in ("range", "centered_axis"):
                bar = ttk.Progressbar(card, orient="horizontal", mode="determinate", maximum=100)
                bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 10))

                # A center marker makes centered axes readable at a glance.
                marker_var = tk.StringVar(value="")
                ttk.Label(card, textvariable=marker_var, width=12).grid(row=1, column=2, sticky="e")
                ttk.Label(card, textvariable=raw_var, width=14).grid(row=1, column=3, sticky="e")

                self.live_input_widgets[definition["id"]] = {
                    "bar": bar,
                    "value_var": value_var,
                    "marker_var": marker_var,
                    "raw_var": raw_var,
                    "definition": definition,
                }

            elif kind == "button":
                indicator = tk.Label(
                    card,
                    text="Released",
                    relief="groove",
                    width=16,
                    padx=8,
                    pady=4,
                )
                indicator.grid(row=1, column=0, sticky="w")
                ttk.Label(card, textvariable=raw_var, width=14).grid(row=1, column=3, sticky="e")

                self.live_input_widgets[definition["id"]] = {
                    "indicator": indicator,
                    "value_var": value_var,
                    "raw_var": raw_var,
                    "definition": definition,
                }

            elif kind == "multi_state":
                states = definition.get("states", [])
                state_frame = ttk.Frame(card)
                state_frame.grid(row=1, column=0, columnspan=3, sticky="w")

                labels = []
                for state in states:
                    lbl = tk.Label(
                        state_frame,
                        text=state.get("label", "State"),
                        relief="groove",
                        padx=10,
                        pady=4,
                    )
                    lbl.pack(side="left", padx=(0, 5))
                    labels.append(lbl)

                ttk.Label(card, textvariable=raw_var, width=14).grid(row=1, column=3, sticky="e")

                self.live_input_widgets[definition["id"]] = {
                    "state_labels": labels,
                    "value_var": value_var,
                    "raw_var": raw_var,
                    "definition": definition,
                }

            else:
                ttk.Label(card, textvariable=value_var).grid(row=1, column=0, columnspan=2, sticky="w")
                ttk.Label(card, textvariable=raw_var, width=14).grid(row=1, column=3, sticky="e")

                self.live_input_widgets[definition["id"]] = {
                    "value_var": value_var,
                    "raw_var": raw_var,
                    "definition": definition,
                }

    def update_live_inputs(self, channels):
        self.rebuild_live_inputs()

        for input_id, widgets in self.live_input_widgets.items():
            definition = widgets["definition"]
            type_info = self.core.input_types.get(definition.get("type"))
            monitor = type_info.get("monitor") if type_info else None

            if monitor is None:
                ch = int(definition.get("source", {}).get("channel", 0))
                raw = channels.get(ch)
                widgets["raw_var"].set(f"raw {raw}" if raw is not None else "raw —")
                widgets["value_var"].set("No live monitor registered")
                continue

            try:
                info = monitor(definition, channels, self.core.input_engine.runtime_state) or {}
            except Exception as exc:
                widgets["value_var"].set(f"Monitor error: {exc}")
                continue

            raw = info.get("raw")
            widgets["raw_var"].set(f"raw {raw}" if raw is not None else "raw —")
            widgets["value_var"].set(str(info.get("label", "—")))

            kind = info.get("kind")
            value = info.get("value")

            if "bar" in widgets:
                if value is None:
                    widgets["bar"]["value"] = 50 if kind == "axis" else 0
                    widgets["marker_var"].set("—")
                elif kind == "axis":
                    widgets["bar"]["value"] = (float(value) + 1.0) * 50.0
                    widgets["marker_var"].set(f"{float(value):+.3f}")
                else:
                    widgets["bar"]["value"] = float(value) * 100.0
                    widgets["marker_var"].set(f"{float(value) * 100.0:5.1f}%")

            if "indicator" in widgets:
                active = bool(value)
                widgets["indicator"].config(
                    text="PRESSED" if active else "Released",
                    relief="sunken" if active else "groove",
                )

            if "state_labels" in widgets:
                current_index = int(value) if isinstance(value, (int, float)) else -1
                for i, lbl in enumerate(widgets["state_labels"]):
                    lbl.config(relief="sunken" if i == current_index else "groove")

    def _inputs(self):
        tab = self.inputs_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        columns = ("name", "type", "channel", "output", "enabled")
        self.input_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for col, title, width in [
            ("name", "Name", 180),
            ("type", "Type", 140),
            ("channel", "Source", 80),
            ("output", "Output", 240),
            ("enabled", "Enabled", 70),
        ]:
            self.input_tree.heading(col, text=title)
            self.input_tree.column(col, width=width, anchor="w")

        self.input_tree.grid(row=0, column=0, sticky="nsew")
        self.input_tree.bind("<Double-1>", lambda e: self.edit_selected_input())

        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.input_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.input_tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(tab)
        buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        ttk.Button(buttons, text="+ Add Input", command=lambda: AddInputDialog(self)).pack(side="left")
        ttk.Button(buttons, text="Edit", command=self.edit_selected_input).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Recalibrate", command=self.recalibrate_selected).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Delete", command=self.delete_selected_input).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Save Profile", command=self.save_profile).pack(side="right")

    def _channels(self):
        tab = self.channels_tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        self.channel_tree = ttk.Treeview(tab, columns=("channel", "value"), show="headings")
        self.channel_tree.heading("channel", text="Channel")
        self.channel_tree.heading("value", text="Raw Value")
        self.channel_tree.column("channel", width=100)
        self.channel_tree.column("value", width=180)
        self.channel_tree.grid(row=0, column=0, sticky="nsew")

        ttk.Label(
            tab,
            text="Channels appear automatically as the bridge discovers them. Protocol v2 carries a 32-bit channel mask, so the PC does not assume a fixed channel count.",
            wraplength=760,
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

    def _api_tab(self):
        tab = self.api_tab
        tab.columnconfigure(0, weight=1)

        ttk.Label(
            tab,
            text="Addon API",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            tab,
            text=(
                "Python addons are loaded from the editable plugins folder next to the app/EXE. "
                "They receive AdapterAPI and can read/edit profiles, register input types, subscribe "
                "to events, add tabs/cards/menu actions, and control the receiver/controller."
            ),
            wraplength=820,
        ).grid(row=1, column=0, sticky="w", pady=(6, 10))

        ttk.Label(tab, text="Local JSON API: http://127.0.0.1:8765/api/v1").grid(row=2, column=0, sticky="w")

        ttk.Label(
            tab,
            text="The HTTP API binds only to 127.0.0.1 so local tools in any language can inspect and edit adapter state without exposing it to the network.",
            wraplength=820,
        ).grid(row=3, column=0, sticky="w", pady=(4, 12))

        loaded = ", ".join(self.plugin_loader.loaded) or "(none)"
        ttk.Label(tab, text=f"Loaded addons: {loaded}").grid(row=4, column=0, sticky="w")

        if self.plugin_loader.errors:
            text = tk.Text(tab, height=12)
            text.grid(row=5, column=0, sticky="nsew", pady=(10, 0))
            for name, err in self.plugin_loader.errors.items():
                text.insert("end", f"{name}\n{err}\n\n")
            tab.rowconfigure(5, weight=1)

    # ---------- Profiles ----------

    def refresh_profiles(self):
        names = self.core.profiles.list_profiles()
        self.profile_combo["values"] = names

        if self.core.profiles.active_path:
            stem = self.core.profiles.active_path.stem
            self.profile_var.set(stem)
        elif names:
            self.profile_var.set(names[0])

    def load_selected_profile(self):
        name = self.profile_var.get()
        if name:
            self.core.profiles.load(name)
            self.refresh_inputs()

    def save_profile(self):
        self.core.profiles.save()
        self.refresh_inputs()

    def new_profile(self):
        name = simpledialog.askstring("New Profile", "Profile name:", parent=self.root)
        if not name:
            return
        stem = self.core.profiles.create(name)
        self.refresh_profiles()
        self.profile_var.set(stem)
        self.refresh_inputs()

    def duplicate_profile(self):
        name = simpledialog.askstring("Duplicate Profile", "New profile name:", parent=self.root)
        if not name:
            return
        stem = self.core.profiles.create(name, self.core.profiles.get())
        self.refresh_profiles()
        self.profile_var.set(stem)
        self.refresh_inputs()

    def delete_profile(self):
        current = self.profile_var.get()
        if not current:
            return
        if not messagebox.askyesno("Delete Profile", f"Delete '{current}'?"):
            return
        self.core.profiles.delete_profile(current)
        self.core.profiles.ensure_default()
        self.refresh_profiles()
        self.refresh_inputs()

    # ---------- Inputs ----------

    def refresh_inputs(self):
        if hasattr(self, "live_input_signature"):
            self.live_input_signature = None

        for item in self.input_tree.get_children():
            self.input_tree.delete(item)

        profile = self.core.profiles.get()

        for definition in profile.get("inputs", []):
            kind = definition.get("type", "")
            channel = definition.get("source", {}).get("channel", "?")

            if kind == "multi_state":
                out = "; ".join(
                    f"{state.get('label', 'State')}→{output_text(state.get('output'))}"
                    for state in definition.get("states", [])
                )
            elif kind == "delta":
                out = (
                    f"+→{output_text(definition.get('positive_output'))}; "
                    f"-→{output_text(definition.get('negative_output'))}"
                )
            else:
                out = output_text(definition.get("output"))

            self.input_tree.insert(
                "",
                "end",
                iid=definition.get("id"),
                values=(
                    definition.get("name", "Input"),
                    kind,
                    f"CH{channel}",
                    out,
                    "Yes" if definition.get("enabled", True) else "No",
                ),
            )

    def selected_input(self):
        selected = self.input_tree.selection()
        if not selected:
            return None
        input_id = selected[0]
        for item in self.core.profiles.get().get("inputs", []):
            if item.get("id") == input_id:
                return item
        return None

    def edit_selected_input(self):
        definition = self.selected_input()
        if not definition:
            return

        def save(updated):
            self.core.profiles.update_input(definition["id"], updated, save=True)
            self.refresh_inputs()

        InputEditor(self, definition, save)

    def delete_selected_input(self):
        definition = self.selected_input()
        if not definition:
            return
        if messagebox.askyesno("Delete Input", f"Delete '{definition.get('name')}'?"):
            self.core.profiles.delete_input(definition["id"], save=True)
            self.refresh_inputs()

    def calibrate_new_input(self, type_id, name, state_count=3):
        self._run_calibration(type_id, name, state_count, existing=None)

    def recalibrate_selected(self):
        definition = self.selected_input()
        if not definition:
            return
        self._run_calibration(
            definition.get("type"),
            definition.get("name", "Input"),
            len(definition.get("states", [])) or 3,
            existing=definition,
        )

    def _run_calibration(self, type_id, name, state_count, existing):
        has_receiver  = bool(self.core.receiver and self.core.receiver.is_alive()
                             and self.core.receiver.snapshot() is not None)
        has_injection = self.core.get_injected_channels() is not None
        if not has_receiver and not has_injection:
            messagebox.showerror(
                "Calibration",
                "No signal source is active.\n\n"
                "Connect the Arduino receiver  OR  start USB Input from the USB Input tab.",
            )
            return

        custom = self.core.input_types.get(type_id)
        if custom and custom.get("calibration_factory"):
            custom["calibration_factory"](self.root, self.api, existing)
            return

        if type_id == "range":
            steps = [
                {"key": "minimum", "instruction": "Move the control to its MINIMUM end"},
                {"key": "maximum", "instruction": "Move the control to its MAXIMUM end"},
            ]
        elif type_id == "centered_axis":
            steps = [
                {"key": "center", "instruction": "Release/hold the control at CENTER"},
                {"key": "negative", "instruction": "Move the control fully in the NEGATIVE direction"},
                {"key": "positive", "instruction": "Move the control fully in the POSITIVE direction"},
            ]
        elif type_id == "button":
            steps = [
                {"key": "released", "instruction": "Leave the button RELEASED"},
                {"key": "pressed", "instruction": "PRESS AND HOLD the button"},
            ]
        elif type_id == "multi_state":
            steps = [
                {"key": f"state_{i}", "instruction": f"Put the switch/control in STATE {i + 1} of {state_count}"}
                for i in range(state_count)
            ]
        elif type_id == "delta":
            steps = [
                {"key": "before", "instruction": "Leave the step control alone at its current state"},
                {"key": "positive", "instruction": "Press/turn the POSITIVE step ONCE, then release"},
                {"key": "negative", "instruction": "Press/turn the NEGATIVE step ONCE, then release"},
            ]
        else:
            messagebox.showerror("Calibration", f"No built-in calibration wizard for '{type_id}'.")
            return

        def finish(records):
            channel = detect_channel(records)

            if existing:
                definition = copy.deepcopy(existing)
            else:
                definition = {
                    "id": new_id(),
                    "name": name,
                    "type": type_id,
                    "enabled": True,
                }

            definition["source"] = {"channel": channel}

            if type_id == "range":
                definition["calibration"] = {
                    "minimum": records["minimum"][channel],
                    "maximum": records["maximum"][channel],
                }
                definition.setdefault("transform", {"invert": False, "expo": 0.0, "sensitivity": 1.0})
                definition.setdefault("options", {"safe_min": False})
                definition.setdefault("output", {"type": "axis", "target": "left_y"})

            elif type_id == "centered_axis":
                definition["calibration"] = {
                    "center": records["center"][channel],
                    "negative": records["negative"][channel],
                    "positive": records["positive"][channel],
                }
                definition.setdefault("transform", {
                    "invert": False,
                    "expo": 0.0,
                    "sensitivity": 1.0,
                    "deadzone": 0.0,
                })
                definition.setdefault("output", {"type": "axis", "target": "right_x"})

            elif type_id == "button":
                definition["calibration"] = {
                    "released": records["released"][channel],
                    "pressed": records["pressed"][channel],
                }
                definition.setdefault("output", {"type": "button", "target": "a"})

            elif type_id == "multi_state":
                states = []
                old_states = definition.get("states", [])
                for i in range(state_count):
                    output = (
                        old_states[i].get("output", {"type": "none", "target": None})
                        if i < len(old_states)
                        else {"type": "none", "target": None}
                    )
                    states.append({
                        "label": f"State {i + 1}",
                        "value": records[f"state_{i}"][channel],
                        "output": output,
                    })
                definition["states"] = states

            elif type_id == "delta":
                before = records["before"][channel]
                pos = records["positive"][channel]
                neg = records["negative"][channel]
                definition["calibration"] = {
                    "positive_step": abs(pos - before),
                    "negative_step": abs(neg - pos) if neg != pos else abs(neg - before),
                    "tolerance": 0.35,
                }
                definition.setdefault("options", {"cooldown_ms": 80})
                definition.setdefault("positive_output", {"type": "button", "target": "dpad_right"})
                definition.setdefault("negative_output", {"type": "button", "target": "dpad_left"})

            if existing:
                self.core.profiles.update_input(definition["id"], definition, save=True)
            else:
                self.core.profiles.add_input(definition, save=True)

            self.refresh_inputs()

            # Open the editor immediately so the user maps the new input.
            InputEditor(
                self,
                definition,
                lambda updated: (
                    self.core.profiles.update_input(definition["id"], updated, save=True),
                    self.refresh_inputs(),
                ),
            )

        SnapshotWizard(self, f"Calibrate {name}", steps, finish)

    # ---------- Serial/controller ----------

    def refresh_ports(self):
        ports = list(serial.tools.list_ports.comports())
        values = [f"{p.device} — {p.description}" for p in ports]
        self.port_combo["values"] = values

        preferred = None
        for value in values:
            lower = value.lower()
            if "ch340" in lower or "usb-serial" in lower or "arduino" in lower:
                preferred = value
                break
        if preferred is None and values:
            preferred = values[0]

        if preferred and self.port_var.get() not in values:
            self.port_var.set(preferred)

    def selected_port(self):
        text = self.port_var.get()
        return text.split(" — ", 1)[0] if text else None

    def toggle_connection(self):
        if self.core.receiver and self.core.receiver.is_alive():
            self.core.disconnect()
        else:
            port = self.selected_port()
            if port:
                self.core.connect(port)

    def toggle_controller(self):
        if self.core.gamepad.active:
            self.core.stop_controller()
        else:
            try:
                self.core.start_controller()
            except Exception as exc:
                messagebox.showerror("Virtual Controller", str(exc))

    def auto_connect_tick(self):
        if not self.core.receiver or not self.core.receiver.is_alive():
            self.refresh_ports()
            port = self.selected_port()
            if port:
                try:
                    self.core.connect(port)
                except Exception:
                    pass
        self.root.after(1800, self.auto_connect_tick)

    def tick(self):
        self.core.tick()
        state = self.core.state()
        receiver = state["receiver"]
        controller = state["controller"]

        if receiver["connected"]:
            latest = receiver["latest"]
            if latest:
                age_ms = (time.monotonic() - latest["time"]) * 1000.0
                watchdog = state["profile"].get("settings", {}).get("watchdog_ms", 150)
                if age_ms > watchdog:
                    self.status_var.set(f"SIGNAL LOST — {age_ms:.0f} ms")
                else:
                    self.status_var.set(f"Connected — {receiver['port']}")
                self.loss_var.set(str(latest["frame_loss"]))
                self.rssi_var.set(str(latest["rssi"]))
                self.protocol_var.set(f"v{latest['version']}")
            else:
                self.status_var.set(f"Connected — waiting for receiver data")
        else:
            self.status_var.set("Disconnected")

        self.packet_rate_var.set(f"{receiver['packet_rate']:.0f}/s")
        self.bad_var.set(str(receiver["usb_bad"]))
        self.missing_var.set(str(receiver["sequence_missing"]))

        self.controller_var.set(
            "Running — SAFE" if controller["active"] and controller["watchdog_engaged"]
            else "Running" if controller["active"]
            else "Stopped"
        )
        self.controller_btn.config(text="Stop Controller" if controller["active"] else "Start Controller")
        self.connect_btn.config(text="Disconnect" if receiver["connected"] else "Connect")

        channels = self.core.channels()
        self._refresh_channel_tree(channels)
        self.update_live_inputs(channels)

        self.root.after(30, self.tick)

    def _refresh_channel_tree(self, channels):
        existing = set(self.channel_tree.get_children())
        desired = {f"ch{ch}" for ch in channels}

        for iid in existing - desired:
            self.channel_tree.delete(iid)

        for ch in sorted(channels):
            iid = f"ch{ch}"
            values = (f"CH{ch}", channels[ch])
            if iid in existing:
                self.channel_tree.item(iid, values=values)
            else:
                self.channel_tree.insert("", "end", iid=iid, values=values)

    def close(self):
        self.http_server.stop()
        self.core.stop_controller()
        self.core.disconnect()
        self.root.destroy()
