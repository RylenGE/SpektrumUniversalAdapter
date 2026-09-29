import json
import threading
import uuid
from pathlib import Path


def slugify(name):
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    cleaned = "_".join(cleaned.split())
    return cleaned or "Profile"


def new_id(prefix="input"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


DEFAULT_PROFILE = {
    "schema_version": 1,
    "name": "Default",
    "settings": {
        "watchdog_ms": 150,
    },
    "inputs": [],
}


class ProfileManager:
    def __init__(self, profiles_dir, event_bus=None):
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.event_bus = event_bus
        self.lock = threading.RLock()
        self.active_path = None
        self.active = None

    def list_profiles(self):
        return sorted(path.stem for path in self.profiles_dir.glob("*.json"))

    def create(self, name, profile=None):
        with self.lock:
            data = json.loads(json.dumps(profile or DEFAULT_PROFILE))
            data["name"] = name
            path = self.profiles_dir / f"{slugify(name)}.json"

            counter = 2
            while path.exists():
                path = self.profiles_dir / f"{slugify(name)}_{counter}.json"
                counter += 1

            path.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self.load(path.stem)
            return path.stem

    def ensure_default(self):
        profiles = self.list_profiles()
        if profiles:
            if self.active is None:
                self.load(profiles[0])
            return

        self.create("Default")

    def load(self, stem):
        with self.lock:
            path = self.profiles_dir / f"{stem}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("schema_version", 1)
            data.setdefault("name", stem)
            data.setdefault("settings", {"watchdog_ms": 150})
            data.setdefault("inputs", [])
            self.active_path = path
            self.active = data

        if self.event_bus:
            self.event_bus.emit("profile.loaded", profile=self.get())
        return self.get()

    def get(self):
        with self.lock:
            return json.loads(json.dumps(self.active or DEFAULT_PROFILE))

    def replace(self, profile, save=True):
        with self.lock:
            self.active = json.loads(json.dumps(profile))
            if save:
                self.save()

        if self.event_bus:
            self.event_bus.emit("profile.changed", profile=self.get())

    def save(self):
        with self.lock:
            if self.active_path is None:
                raise RuntimeError("No active profile")
            self.active_path.write_text(json.dumps(self.active, indent=4), encoding="utf-8")

        if self.event_bus:
            self.event_bus.emit("profile.saved", profile=self.get())

    def add_input(self, definition, save=True):
        with self.lock:
            item = json.loads(json.dumps(definition))
            item.setdefault("id", new_id())
            item.setdefault("enabled", True)
            self.active.setdefault("inputs", []).append(item)
            if save:
                self.save()
            return json.loads(json.dumps(item))

    def update_input(self, input_id, definition, save=True):
        with self.lock:
            for index, item in enumerate(self.active.setdefault("inputs", [])):
                if item.get("id") == input_id:
                    updated = json.loads(json.dumps(definition))
                    updated["id"] = input_id
                    self.active["inputs"][index] = updated
                    if save:
                        self.save()
                    return json.loads(json.dumps(updated))
        raise KeyError(input_id)

    def delete_input(self, input_id, save=True):
        with self.lock:
            before = len(self.active.setdefault("inputs", []))
            self.active["inputs"] = [
                item for item in self.active["inputs"]
                if item.get("id") != input_id
            ]
            changed = len(self.active["inputs"]) != before
            if changed and save:
                self.save()
            return changed

    def delete_profile(self, stem):
        with self.lock:
            path = self.profiles_dir / f"{stem}.json"
            if path.exists():
                path.unlink()
            if self.active_path == path:
                self.active = None
                self.active_path = None


def migrate_legacy_if_present(root_dir, profile_manager):
    """
    Converts the previous project's spektrum_calibration.json +
    controller_settings.json into a normal object profile once.
    """
    root = Path(root_dir)
    old_cal = root / "spektrum_calibration.json"
    old_settings = root / "controller_settings.json"

    if not old_cal.exists() or not old_settings.exists():
        return None

    if any(profile_manager.profiles_dir.glob("*.json")):
        return None

    try:
        cal = json.loads(old_cal.read_text(encoding="utf-8"))
        settings = json.loads(old_settings.read_text(encoding="utf-8"))
    except Exception:
        return None

    inputs = []

    mapping = [
        ("Throttle", "range", "throttle", 1, {
            "minimum": cal.get("throttle", {}).get("down"),
            "maximum": cal.get("throttle", {}).get("up"),
        }),
        ("Roll", "centered_axis", "roll", 2, {
            "negative": cal.get("roll", {}).get("left"),
            "center": cal.get("roll", {}).get("center"),
            "positive": cal.get("roll", {}).get("right"),
        }),
        ("Pitch", "centered_axis", "pitch", 3, {
            "negative": cal.get("pitch", {}).get("down"),
            "center": cal.get("pitch", {}).get("center"),
            "positive": cal.get("pitch", {}).get("up"),
        }),
        ("Yaw", "centered_axis", "yaw", 4, {
            "negative": cal.get("yaw", {}).get("left"),
            "center": cal.get("yaw", {}).get("center"),
            "positive": cal.get("yaw", {}).get("right"),
        }),
    ]

    for name, kind, old_key, channel, calibration in mapping:
        old_axis = settings.get("axes", {}).get(old_key, {})
        transform = {
            "deadzone": old_axis.get("deadzone", 0.0),
            "expo": old_axis.get("expo", 0.0),
            "sensitivity": old_axis.get("sensitivity", 1.0),
            "invert": old_axis.get("invert", False),
        }
        output = {"type": "axis", "target": old_axis.get("output", "left_y")}
        inputs.append({
            "id": new_id(),
            "name": name,
            "type": kind,
            "enabled": True,
            "source": {"channel": channel},
            "calibration": calibration,
            "transform": transform,
            "output": output,
        })

    trigger = settings.get("controls", {}).get("red_trigger", {})
    inputs.append({
        "id": new_id(),
        "name": "Red Trigger",
        "type": "button",
        "enabled": True,
        "source": {"channel": trigger.get("channel", 6)},
        "calibration": {
            "released": trigger.get("released", 10880),
            "pressed": trigger.get("pressed", 54528),
        },
        "output": _legacy_output(trigger.get("output", "right_trigger")),
    })

    switch = settings.get("controls", {}).get("three_position_switch", {})
    positions = switch.get("positions", {"0": 54528, "1": 32736, "2": 10880})
    states = []
    for pos, label in (("0", "Down"), ("1", "Middle"), ("2", "Up")):
        states.append({
            "label": label,
            "value": positions.get(pos, positions.get(int(pos), 0)),
            "output": _legacy_output(switch.get(f"position_{pos}_output", "none")),
        })

    inputs.append({
        "id": new_id(),
        "name": "3-Position Switch",
        "type": "multi_state",
        "enabled": True,
        "source": {"channel": switch.get("channel", 5)},
        "states": states,
    })

    profile = {
        "schema_version": 1,
        "name": "Migrated Controller",
        "settings": {
            "watchdog_ms": settings.get("options", {}).get("signal_watchdog_ms", 150),
        },
        "inputs": inputs,
    }

    return profile_manager.create("Migrated Controller", profile)


def _legacy_output(name):
    if not name or name == "none":
        return {"type": "none", "target": None}
    if name in ("left_trigger", "right_trigger"):
        return {"type": "trigger", "target": name}
    if name in ("left_x", "left_y", "right_x", "right_y"):
        return {"type": "axis", "target": name}
    return {"type": "button", "target": name}
