try:
    import vgamepad as vg
except ImportError:
    vg = None


BUTTON_NAMES = [
    "a", "b", "x", "y",
    "left_shoulder", "right_shoulder",
    "left_thumb", "right_thumb",
    "start", "back",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
]

if vg is not None:
    BUTTON_MAP = {
        "a": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
        "b": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
        "x": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
        "y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
        "left_shoulder": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
        "right_shoulder": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "left_thumb": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
        "right_thumb": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
        "start": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
        "back": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
        "dpad_up": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
        "dpad_down": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
        "dpad_left": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
        "dpad_right": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    }
else:
    BUTTON_MAP = {}


class VirtualGamepad:
    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self.pad = None
        self.active = False
        self.last_buttons = set()

    def start(self):
        if vg is None:
            raise RuntimeError("vgamepad is not installed")
        if self.active:
            return
        self.pad = vg.VX360Gamepad()
        self.active = True
        self.last_buttons = set()
        if self.event_bus:
            self.event_bus.emit("controller.started")

    def stop(self):
        if self.pad is not None:
            try:
                self.pad.reset()
                self.pad.update()
            except Exception:
                pass
        self.pad = None
        self.active = False
        self.last_buttons = set()
        if self.event_bus:
            self.event_bus.emit("controller.stopped")

    def safe_state(self, throttle_target=None):
        if not self.active or self.pad is None:
            return

        axes = {
            "left_x": 0.0,
            "left_y": 0.0,
            "right_x": 0.0,
            "right_y": 0.0,
        }

        if throttle_target:
            target, value = throttle_target
            if target in axes:
                axes[target] = value

        frame = {
            "axes": axes,
            "triggers": {"left_trigger": 0.0, "right_trigger": 0.0},
            "buttons": set(),
        }
        self.apply(frame)

    def apply(self, frame):
        if not self.active or self.pad is None:
            return

        axes = frame.get("axes", {})
        triggers = frame.get("triggers", {})
        buttons = set(frame.get("buttons", set()))

        self.pad.left_joystick_float(
            x_value_float=float(axes.get("left_x", 0.0)),
            y_value_float=float(axes.get("left_y", 0.0)),
        )
        self.pad.right_joystick_float(
            x_value_float=float(axes.get("right_x", 0.0)),
            y_value_float=float(axes.get("right_y", 0.0)),
        )

        self.pad.left_trigger(value=int(round(float(triggers.get("left_trigger", 0.0)) * 255)))
        self.pad.right_trigger(value=int(round(float(triggers.get("right_trigger", 0.0)) * 255)))

        for name in self.last_buttons - buttons:
            if name in BUTTON_MAP:
                self.pad.release_button(button=BUTTON_MAP[name])

        for name in buttons - self.last_buttons:
            if name in BUTTON_MAP:
                self.pad.press_button(button=BUTTON_MAP[name])

        self.last_buttons = buttons
        self.pad.update()
