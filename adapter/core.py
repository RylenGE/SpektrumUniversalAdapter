import json
import threading
import time
from pathlib import Path

from .events import EventBus
from .gamepad import VirtualGamepad
from .input_engine import InputEngine, empty_frame, merge_frame, register_builtin_input_types
from .profile_manager import ProfileManager, migrate_legacy_if_present
from .receiver import SerialReceiver
from .registries import InputTypeRegistry, UIRegistry


class AdapterCore:
    def __init__(self, root_dir):
        self.root_dir = Path(root_dir)
        self.events = EventBus()
        self.input_types = InputTypeRegistry()
        self.ui_registry = UIRegistry()

        register_builtin_input_types(self.input_types)

        self.profiles = ProfileManager(self.root_dir / "profiles", self.events)
        migrate_legacy_if_present(self.root_dir, self.profiles)
        self.profiles.ensure_default()

        self.input_engine = InputEngine(self.input_types, self.events)
        self.gamepad = VirtualGamepad(self.events)

        self.receiver = None
        self.receiver_port = None
        self.receiver_baud = 1_000_000

        self._lock = threading.RLock()
        self._last_frame = None
        self._watchdog_engaged = False
        self._profile_input_passthrough = True
        self._allow_external_when_profile_blocked = False
        self._external_static_frames = {}
        self._external_frame_sources = {}
        self._external_source_state = {}

        # Testing / debug helpers
        # An injected snapshot replaces live receiver data when present.
        self._injected_snapshot = None
        # A simple log of recent debug events (keeps small)
        self._debug_log = []
        # Channel history: populated from both USB injection and live receiver.
        # Used by the calibration wizard when no SRXL2 receiver is connected.
        self._channel_history = []
        self._channel_history_lock = threading.RLock()

    # --- Injected channels API ---
    def set_injected_channels(self, channels):
        """Set a fake receiver snapshot (channels is a dict mapping int->int).
        This is intended for testing and debugging only.
        """
        now = time.monotonic()
        int_channels = {int(k): int(v) for k, v in (channels or {}).items()}
        snapshot = {
            "time": now,
            "channels": int_channels,
        }
        with self._lock:
            self._injected_snapshot = snapshot
            self._debug_log.append((now, "injected_set", int_channels))
            if len(self._debug_log) > 200:
                self._debug_log.pop(0)

        # Keep a rolling history so the calibration wizard can sample USB data.
        with self._channel_history_lock:
            self._channel_history.append({"time": now, "channels": dict(int_channels)})
            if len(self._channel_history) > 500:
                self._channel_history.pop(0)

        # Fire channels.changed so plugins (e.g. feed_cut_plugin) react to
        # injected data exactly as they would to live receiver data.
        self.events.emit("channels.changed", channels=int_channels)

    def clear_injected_channels(self):
        with self._lock:
            self._injected_snapshot = None
            self._debug_log.append((time.monotonic(), "injected_cleared", None))

    def get_injected_channels(self):
        with self._lock:
            return dict(self._injected_snapshot["channels"]) if self._injected_snapshot else None

    def channel_history_since(self, t):
        """Return [{time, channels}] entries captured since monotonic time t.
        Works for both USB injection and (if receiver is live) SRXL2 data.
        """
        # Prefer live receiver history when connected.
        if self.receiver and self.receiver.is_alive():
            return self.receiver.history_since(t)
        with self._channel_history_lock:
            return [e for e in self._channel_history if e["time"] >= t]

    def get_debug_log(self, limit=100):
        with self._lock:
            return list(self._debug_log[-abs(int(limit)):])

    def connect(self, port, baud=1_000_000):
        self.disconnect()
        self.receiver_port = port
        self.receiver_baud = baud
        self.receiver = SerialReceiver(
            port,
            baud,
            event_bus=self.events,
            on_disconnect=self._receiver_dropped,
        )
        self.receiver.start()
        self.events.emit("receiver.connecting", port=port)

    def disconnect(self):
        if self.receiver:
            self.receiver.stop()
        self.receiver = None
        self.receiver_port = None
        self._engage_watchdog()
        self.events.emit("receiver.disconnected")

    def _receiver_dropped(self):
        self.receiver = None
        self.receiver_port = None
        self._engage_watchdog()
        self.events.emit("receiver.disconnected")

    def start_controller(self):
        self.gamepad.start()

    def stop_controller(self):
        self.gamepad.stop()

    def set_profile_input_passthrough(self, enabled, allow_external_when_blocked=None):
        with self._lock:
            self._profile_input_passthrough = bool(enabled)
            if allow_external_when_blocked is not None:
                self._allow_external_when_profile_blocked = bool(
                    allow_external_when_blocked
                )

        self.events.emit(
            "controller.passthrough.changed",
            enabled=self._profile_input_passthrough,
            allow_external_when_blocked=self._allow_external_when_profile_blocked,
        )

    def profile_input_passthrough(self):
        with self._lock:
            return {
                "enabled": self._profile_input_passthrough,
                "allow_external_when_blocked": self._allow_external_when_profile_blocked,
            }

    def reset_virtual_output(self, throttle_target=None):
        self.gamepad.safe_state(throttle_target=throttle_target)
        self.events.emit("controller.output.reset")

    def register_external_frame_source(self, source_id, callback, priority=100):
        source_id = str(source_id)
        with self._lock:
            self._external_frame_sources[source_id] = {
                "callback": callback,
                "priority": int(priority),
            }
            self._external_source_state.setdefault(source_id, {})

        self.events.emit(
            "external_input.source.registered",
            source_id=source_id,
            priority=int(priority),
        )

    def unregister_external_frame_source(self, source_id):
        source_id = str(source_id)
        removed = False

        with self._lock:
            if source_id in self._external_frame_sources:
                del self._external_frame_sources[source_id]
                removed = True
            self._external_source_state.pop(source_id, None)

        if removed:
            self.events.emit("external_input.source.unregistered", source_id=source_id)

        return removed

    def set_external_frame(self, source_id, frame):
        source_id = str(source_id)
        normalized = self._normalize_external_frame(frame)

        with self._lock:
            self._external_static_frames[source_id] = normalized

        self.events.emit(
            "external_input.frame.updated",
            source_id=source_id,
        )

    def clear_external_frame(self, source_id):
        source_id = str(source_id)
        removed = False

        with self._lock:
            if source_id in self._external_static_frames:
                del self._external_static_frames[source_id]
                removed = True

        if removed:
            self.events.emit("external_input.frame.cleared", source_id=source_id)

        return removed

    def external_frames(self):
        with self._lock:
            static_ids = sorted(self._external_static_frames.keys())
            dynamic_ids = sorted(self._external_frame_sources.keys())

        return {
            "static_sources": static_ids,
            "dynamic_sources": dynamic_ids,
        }

    def _normalize_external_frame(self, frame):
        frame = frame or {}
        normalized = empty_frame()

        merge_frame(normalized, {
            "axes": frame.get("axes", {}),
            "triggers": frame.get("triggers", {}),
            "buttons": frame.get("buttons", ()),
        })

        return normalized

    def _evaluate_external_frame(self, channels):
        # Start sparse — only axes/triggers explicitly set by sources will be merged
        # into the profile frame. Starting with empty_frame() would zero out every
        # axis that no external source touches, overwriting profile output.
        merged = {"axes": {}, "triggers": {}, "buttons": set()}

        with self._lock:
            static_frames = list(self._external_static_frames.values())
            providers = sorted(
                self._external_frame_sources.items(),
                key=lambda pair: pair[1]["priority"],
            )

        for partial in static_frames:
            merge_frame(merged, partial)

        for source_id, info in providers:
            callback = info.get("callback")
            if callback is None:
                continue

            with self._lock:
                source_state = self._external_source_state.setdefault(source_id, {})

            try:
                partial = callback(channels, source_state) or {}
                merge_frame(merged, self._normalize_external_frame(partial))
            except Exception as exc:
                self.events.emit(
                    "external_input.error",
                    source_id=source_id,
                    error=str(exc),
                )

        return merged

    def tick(self):
        # Injected snapshot takes priority over the live receiver so tests work
        # without hardware connected.  Refresh its timestamp every tick so the
        # watchdog never expires while injection is active.
        with self._lock:
            injected = self._injected_snapshot

        if injected is not None:
            injected["time"] = time.monotonic()
            snapshot = injected
        else:
            receiver = self.receiver
            if receiver is None or not receiver.is_alive():
                self._engage_watchdog()
                return

            snapshot = receiver.snapshot()
            if snapshot is None:
                self._engage_watchdog()
                return

            watchdog_ms = float(self.profiles.get().get("settings", {}).get("watchdog_ms", 150))
            age_ms = (time.monotonic() - snapshot["time"]) * 1000.0
            if age_ms > watchdog_ms:
                self._engage_watchdog()
                return

        self._watchdog_engaged = False
        profile = self.profiles.get()
        with self._lock:
            passthrough = self._profile_input_passthrough
            allow_external_when_blocked = self._allow_external_when_profile_blocked

        if passthrough:
            frame = self.input_engine.evaluate(profile, snapshot["channels"])
        else:
            frame = empty_frame()

        if passthrough or allow_external_when_blocked:
            external_frame = self._evaluate_external_frame(snapshot["channels"])
            merge_frame(frame, external_frame)

        self._last_frame = frame
        self.gamepad.apply(frame)

    def _engage_watchdog(self):
        if self._watchdog_engaged:
            return
        self._watchdog_engaged = True

        throttle_target = None
        profile = self.profiles.get()

        for item in profile.get("inputs", []):
            if (
                item.get("enabled", True)
                and item.get("type") == "range"
                and item.get("output", {}).get("type") == "axis"
            ):
                # We cannot know semantically whether a generic range is throttle.
                # Users can mark exactly one range as safe_min=true.
                if item.get("options", {}).get("safe_min", False):
                    target = item.get("output", {}).get("target")
                    inverted = item.get("transform", {}).get("invert", False)
                    throttle_target = (target, 1.0 if inverted else -1.0)
                    break

        self.gamepad.safe_state(throttle_target)
        self.events.emit("watchdog.engaged")

    def state(self):
        receiver = self.receiver
        snapshot = receiver.snapshot() if receiver else None
        stats = receiver.stats() if receiver else {"valid": 0, "bad": 0, "missing": 0, "rate": 0.0}

        return {
            "receiver": {
                "connected": bool(receiver and receiver.is_alive()),
                "port": self.receiver_port,
                "packet_rate": stats["rate"],
                "usb_bad": stats["bad"],
                "sequence_missing": stats["missing"],
                "latest": snapshot,
            },
            "controller": {
                "active": self.gamepad.active,
                "watchdog_engaged": self._watchdog_engaged,
                "profile_input_passthrough": self.profile_input_passthrough(),
                "external_input": self.external_frames(),
            },
            "profile": self.profiles.get(),
            "input_types": {
                key: {
                    "id": info["id"],
                    "display_name": info["display_name"],
                }
                for key, info in self.input_types.all().items()
            },
        }

    def channels(self):
        # When USB injection is active, expose those channels so the live
        # display, calibration wizard, and API all see the USB joystick data.
        with self._lock:
            injected = self._injected_snapshot
        if injected is not None:
            return dict(injected["channels"])
        if not self.receiver:
            return {}
        return self.receiver.channels()
