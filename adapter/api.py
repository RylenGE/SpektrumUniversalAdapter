from .input_engine import empty_frame


class AdapterAPI:
    """
    Public addon API.

    Addons receive one AdapterAPI instance in setup(api).

    The API intentionally exposes the registries and event bus as first-class
    objects so extensions can read and extend every layer rather than needing
    private imports.
    """

    API_VERSION = 2

    def __init__(self, core):
        self.core = core
        self.events = core.events
        self.input_types = core.input_types
        self.ui = core.ui_registry

    # ----- Readable state -----

    def get_state(self):
        return self.core.state()

    def get_channels(self):
        return self.core.channels()

    def get_profile(self):
        return self.core.profiles.get()

    def list_profiles(self):
        return self.core.profiles.list_profiles()

    # ----- Editable profile data -----

    def replace_profile(self, profile, save=True):
        self.core.profiles.replace(profile, save=save)
        return self.get_profile()

    def save_profile(self):
        self.core.profiles.save()

    def create_profile(self, name, profile=None):
        return self.core.profiles.create(name, profile)

    def load_profile(self, name):
        return self.core.profiles.load(name)

    def add_input(self, definition, save=True):
        return self.core.profiles.add_input(definition, save=save)

    def update_input(self, input_id, definition, save=True):
        return self.core.profiles.update_input(input_id, definition, save=save)

    def delete_input(self, input_id, save=True):
        return self.core.profiles.delete_input(input_id, save=save)

    # ----- Controller / receiver actions -----

    def connect(self, port, baud=1_000_000):
        self.core.connect(port, baud)

    def disconnect(self):
        self.core.disconnect()

    def start_controller(self):
        self.core.start_controller()

    def stop_controller(self):
        self.core.stop_controller()

    def set_profile_input_passthrough(self, enabled, *, allow_external_when_blocked=None):
        self.core.set_profile_input_passthrough(
            enabled,
            allow_external_when_blocked=allow_external_when_blocked,
        )

    def get_profile_input_passthrough(self):
        return self.core.profile_input_passthrough()

    def reset_virtual_output(self, throttle_target=None):
        self.core.reset_virtual_output(throttle_target=throttle_target)

    def set_external_frame(self, source_id, frame):
        self.core.set_external_frame(source_id, frame)

    # ----- Testing / debug helpers -----
    def set_injected_channels(self, channels):
        """Inject a fake channels snapshot for testing. channels is a dict mapping channel->raw value."""
        self.core.set_injected_channels(channels)

    def clear_injected_channels(self):
        self.core.clear_injected_channels()

    def get_injected_channels(self):
        return self.core.get_injected_channels()

    def get_last_frame(self):
        """Return the last evaluated virtual controller frame as a JSON-safe dict."""
        frame = self.core._last_frame
        if frame is None:
            return None
        return {
            "axes": dict(frame.get("axes", {})),
            "triggers": dict(frame.get("triggers", {})),
            "buttons": sorted(frame.get("buttons", set())),
        }

    def get_debug_log(self, limit=100):
        return self.core.get_debug_log(limit=limit)

    def clear_external_frame(self, source_id):
        return self.core.clear_external_frame(source_id)

    def get_external_frames(self):
        return self.core.external_frames()

    def register_external_frame_source(self, source_id, callback, *, priority=100):
        self.core.register_external_frame_source(source_id, callback, priority=priority)

    def unregister_external_frame_source(self, source_id):
        return self.core.unregister_external_frame_source(source_id)

    def build_frame(self, *, axes=None, triggers=None, buttons=None):
        frame = empty_frame()

        if axes:
            frame["axes"].update(axes)

        if triggers:
            frame["triggers"].update(triggers)

        if buttons:
            frame["buttons"] = set(buttons)

        return frame

    # ----- Extension registration -----

    def register_input_type(
        self,
        type_id,
        evaluator,
        *,
        display_name=None,
        editor_factory=None,
        calibration_factory=None,
        monitor=None,
    ):
        self.input_types.register(
            type_id,
            evaluator,
            display_name=display_name,
            editor_factory=editor_factory,
            calibration_factory=calibration_factory,
            monitor=monitor,
        )

    def register_tab(self, title, factory, *, order=100):
        self.ui.register_tab(title, factory, order=order)

    def register_dashboard_card(self, title, factory, *, order=100):
        self.ui.register_dashboard_card(title, factory, order=order)

    def register_menu_action(self, label, callback, *, menu="Addons"):
        self.ui.register_menu_action(label, callback, menu=menu)
