import time


AXES = ("left_x", "left_y", "right_x", "right_y")
TRIGGERS = ("left_trigger", "right_trigger")


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def apply_expo(value, expo):
    expo = clamp(float(expo), 0.0, 1.0)
    return (1.0 - expo) * value + expo * (value ** 3)


def empty_frame():
    return {
        "axes": {name: 0.0 for name in AXES},
        "triggers": {name: 0.0 for name in TRIGGERS},
        "buttons": set(),
    }


def merge_frame(target, partial):
    for name, value in partial.get("axes", {}).items():
        if name in target["axes"]:
            target["axes"][name] = clamp(float(value), -1.0, 1.0)

    for name, value in partial.get("triggers", {}).items():
        if name in target["triggers"]:
            target["triggers"][name] = clamp(float(value), 0.0, 1.0)

    target["buttons"].update(partial.get("buttons", set()))


def output_value(output, value):
    if not output:
        return {}

    kind = output.get("type")
    target = output.get("target")

    if kind == "axis" and target in AXES:
        return {"axes": {target: clamp(value, -1.0, 1.0)}}

    if kind == "trigger" and target in TRIGGERS:
        return {"triggers": {target: clamp(value, 0.0, 1.0)}}

    if kind == "button" and target:
        if value >= 0.5:
            return {"buttons": {target}}
        return {}

    return {}


def _source_channel(definition):
    return int(definition.get("source", {}).get("channel", 0))


def eval_range(definition, channels, state):
    ch = _source_channel(definition)
    if ch not in channels:
        return {}

    cal = definition.get("calibration", {})
    lo = cal.get("minimum")
    hi = cal.get("maximum")
    if lo is None or hi is None or hi == lo:
        return {}

    t = clamp((channels[ch] - lo) / (hi - lo), 0.0, 1.0)

    transform = definition.get("transform", {})
    if transform.get("invert", False):
        t = 1.0 - t

    # Range can feed an axis (-1..1) or a trigger (0..1).
    output = definition.get("output", {})
    if output.get("type") == "axis":
        value = t * 2.0 - 1.0
        value = apply_expo(value, transform.get("expo", 0.0))
        value = clamp(value * float(transform.get("sensitivity", 1.0)), -1.0, 1.0)
    else:
        value = clamp(t * float(transform.get("sensitivity", 1.0)), 0.0, 1.0)

    return output_value(output, value)


def eval_centered_axis(definition, channels, state):
    ch = _source_channel(definition)
    if ch not in channels:
        return {}

    cal = definition.get("calibration", {})
    negative = cal.get("negative")
    center = cal.get("center")
    positive = cal.get("positive")

    if None in (negative, center, positive):
        return {}

    raw = channels[ch]

    if raw == center:
        value = 0.0
    elif (raw - center) * (negative - center) > 0:
        denom = negative - center
        value = -clamp((raw - center) / denom, 0.0, 1.0)
    else:
        denom = positive - center
        value = clamp((raw - center) / denom, 0.0, 1.0)

    transform = definition.get("transform", {})
    deadzone = clamp(float(transform.get("deadzone", 0.0)), 0.0, 0.49)

    magnitude = abs(value)
    if magnitude <= deadzone:
        value = 0.0
    elif magnitude > 0.0:
        value = (1 if value > 0 else -1) * ((magnitude - deadzone) / (1.0 - deadzone))

    if transform.get("invert", False):
        value = -value

    value = apply_expo(value, transform.get("expo", 0.0))
    value = clamp(value * float(transform.get("sensitivity", 1.0)), -1.0, 1.0)

    return output_value(definition.get("output", {}), value)


def eval_button(definition, channels, state):
    ch = _source_channel(definition)
    if ch not in channels:
        return {}

    cal = definition.get("calibration", {})
    released = cal.get("released")
    pressed = cal.get("pressed")
    if released is None or pressed is None or released == pressed:
        return {}

    amount = clamp((channels[ch] - released) / (pressed - released), 0.0, 1.0)
    return output_value(definition.get("output", {}), amount)


def eval_multi_state(definition, channels, state):
    ch = _source_channel(definition)
    if ch not in channels:
        return {}

    states = definition.get("states", [])
    if not states:
        return {}

    raw = channels[ch]
    current = min(states, key=lambda item: abs(raw - int(item.get("value", 0))))
    return output_value(current.get("output", {}), 1.0)


def eval_delta(definition, channels, state):
    ch = _source_channel(definition)
    if ch not in channels:
        return {}

    raw = channels[ch]
    key = definition.get("id", str(id(definition)))
    previous = state.setdefault("delta_previous", {}).get(key)
    state["delta_previous"][key] = raw

    if previous is None:
        return {}

    delta = raw - previous
    cal = definition.get("calibration", {})
    positive_step = abs(float(cal.get("positive_step", 0)))
    negative_step = abs(float(cal.get("negative_step", positive_step)))
    tolerance = float(cal.get("tolerance", 0.35))
    now = time.monotonic()

    cooldown_ms = float(definition.get("options", {}).get("cooldown_ms", 80))
    last_fire = state.setdefault("delta_last_fire", {}).get(key, 0.0)
    if (now - last_fire) * 1000.0 < cooldown_ms:
        return {}

    if positive_step > 0 and abs(delta - positive_step) <= positive_step * tolerance:
        state["delta_last_fire"][key] = now
        return output_value(definition.get("positive_output", {}), 1.0)

    if negative_step > 0 and abs(delta + negative_step) <= negative_step * tolerance:
        state["delta_last_fire"][key] = now
        return output_value(definition.get("negative_output", {}), 1.0)

    return {}



def _transform_centered_for_monitor(definition, raw):
    cal = definition.get("calibration", {})
    negative = cal.get("negative")
    center = cal.get("center")
    positive = cal.get("positive")
    if None in (negative, center, positive):
        return None

    if raw == center:
        value = 0.0
    elif (raw - center) * (negative - center) > 0:
        denom = negative - center
        if denom == 0:
            return None
        value = -clamp((raw - center) / denom, 0.0, 1.0)
    else:
        denom = positive - center
        if denom == 0:
            return None
        value = clamp((raw - center) / denom, 0.0, 1.0)

    transform = definition.get("transform", {})
    deadzone = clamp(float(transform.get("deadzone", 0.0)), 0.0, 0.49)
    magnitude = abs(value)

    if magnitude <= deadzone:
        value = 0.0
    elif magnitude > 0.0:
        value = (1 if value > 0 else -1) * ((magnitude - deadzone) / (1.0 - deadzone))

    if transform.get("invert", False):
        value = -value

    value = apply_expo(value, transform.get("expo", 0.0))
    return clamp(value * float(transform.get("sensitivity", 1.0)), -1.0, 1.0)


def monitor_range(definition, channels, state):
    ch = _source_channel(definition)
    raw = channels.get(ch)
    cal = definition.get("calibration", {})
    lo = cal.get("minimum")
    hi = cal.get("maximum")

    if raw is None or lo is None or hi is None or hi == lo:
        return {
            "kind": "range",
            "value": None,
            "raw": raw,
            "label": "Not calibrated",
            "minimum": 0.0,
            "maximum": 1.0,
        }

    t = clamp((raw - lo) / (hi - lo), 0.0, 1.0)
    transform = definition.get("transform", {})
    if transform.get("invert", False):
        t = 1.0 - t

    output = definition.get("output", {})
    if output.get("type") == "axis":
        value = t * 2.0 - 1.0
        value = apply_expo(value, transform.get("expo", 0.0))
        value = clamp(value * float(transform.get("sensitivity", 1.0)), -1.0, 1.0)
        return {
            "kind": "axis",
            "value": value,
            "raw": raw,
            "label": f"{value:+.3f}",
            "minimum": -1.0,
            "maximum": 1.0,
        }

    value = clamp(t * float(transform.get("sensitivity", 1.0)), 0.0, 1.0)
    return {
        "kind": "range",
        "value": value,
        "raw": raw,
        "label": f"{value:.3f}",
        "minimum": 0.0,
        "maximum": 1.0,
    }


def monitor_centered_axis(definition, channels, state):
    ch = _source_channel(definition)
    raw = channels.get(ch)
    value = None if raw is None else _transform_centered_for_monitor(definition, raw)

    return {
        "kind": "axis",
        "value": value,
        "raw": raw,
        "label": "—" if value is None else f"{value:+.3f}",
        "minimum": -1.0,
        "maximum": 1.0,
    }


def monitor_button(definition, channels, state):
    ch = _source_channel(definition)
    raw = channels.get(ch)
    cal = definition.get("calibration", {})
    released = cal.get("released")
    pressed = cal.get("pressed")

    if raw is None or released is None or pressed is None or released == pressed:
        active = None
    else:
        amount = clamp((raw - released) / (pressed - released), 0.0, 1.0)
        active = amount >= 0.5

    return {
        "kind": "button",
        "value": active,
        "raw": raw,
        "label": "—" if active is None else ("PRESSED" if active else "Released"),
        "minimum": 0.0,
        "maximum": 1.0,
    }


def monitor_multi_state(definition, channels, state):
    ch = _source_channel(definition)
    raw = channels.get(ch)
    states = definition.get("states", [])

    if raw is None or not states:
        label = "—"
        value = None
    else:
        current = min(states, key=lambda item: abs(raw - int(item.get("value", 0))))
        label = str(current.get("label", "State"))
        try:
            value = states.index(current)
        except ValueError:
            value = 0

    return {
        "kind": "state",
        "value": value,
        "raw": raw,
        "label": label,
        "minimum": 0.0,
        "maximum": max(1.0, float(len(states) - 1)),
        "states": [str(item.get("label", f"State {i+1}")) for i, item in enumerate(states)],
    }


def monitor_delta(definition, channels, state):
    ch = _source_channel(definition)
    raw = channels.get(ch)
    key = definition.get("id", str(id(definition)))
    previous = state.setdefault("monitor_delta_previous", {}).get(key)
    state["monitor_delta_previous"][key] = raw

    delta = None
    if raw is not None and previous is not None:
        delta = raw - previous

    label = "—" if delta is None else f"Δ {delta:+d}"

    return {
        "kind": "delta",
        "value": delta,
        "raw": raw,
        "label": label,
        "minimum": -1.0,
        "maximum": 1.0,
    }

def register_builtin_input_types(registry):
    registry.register(
        "range",
        eval_range,
        display_name="Range / Slider",
        monitor=monitor_range,
    )
    registry.register(
        "centered_axis",
        eval_centered_axis,
        display_name="Centered Axis",
        monitor=monitor_centered_axis,
    )
    registry.register(
        "button",
        eval_button,
        display_name="Button",
        monitor=monitor_button,
    )
    registry.register(
        "multi_state",
        eval_multi_state,
        display_name="Multi-State Switch",
        monitor=monitor_multi_state,
    )
    registry.register(
        "delta",
        eval_delta,
        display_name="Delta / Step",
        monitor=monitor_delta,
    )


class InputEngine:
    def __init__(self, registry, event_bus=None):
        self.registry = registry
        self.event_bus = event_bus
        self.runtime_state = {}

    def evaluate(self, profile, channels):
        frame = empty_frame()

        for definition in profile.get("inputs", []):
            if not definition.get("enabled", True):
                continue

            type_info = self.registry.get(definition.get("type"))
            if not type_info:
                continue

            try:
                partial = type_info["evaluator"](definition, channels, self.runtime_state) or {}
                merge_frame(frame, partial)
            except Exception as exc:
                if self.event_bus:
                    self.event_bus.emit(
                        "input.error",
                        input_id=definition.get("id"),
                        error=str(exc),
                    )

        return frame
