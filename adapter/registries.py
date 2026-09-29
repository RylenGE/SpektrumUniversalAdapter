import threading


class InputTypeRegistry:
    """
    Addons can register new input types without editing the core.

    evaluator signature:
        evaluator(definition, channels, runtime_state) -> partial virtual frame dict

    editor_factory/calibration_factory are optional UI hooks.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._types = {}

    def register(
        self,
        type_id,
        evaluator,
        *,
        display_name=None,
        editor_factory=None,
        calibration_factory=None,
        monitor=None,
    ):
        """
        Register an input type.

        monitor is optional and powers the generic live-input UI.
        Signature:
            monitor(definition, channels, runtime_state) -> {
                "kind": "axis" | "range" | "button" | "state" | "delta" | "text",
                "value": float | bool | str | None,
                "raw": int | None,
                "label": str,
                "minimum": float,
                "maximum": float,
            }
        """
        with self._lock:
            self._types[type_id] = {
                "id": type_id,
                "display_name": display_name or type_id,
                "evaluator": evaluator,
                "editor_factory": editor_factory,
                "calibration_factory": calibration_factory,
                "monitor": monitor,
            }

    def get(self, type_id):
        with self._lock:
            return self._types.get(type_id)

    def all(self):
        with self._lock:
            return dict(self._types)


class UIRegistry:
    """Public UI extension registry used by addons."""

    def __init__(self):
        self._tabs = []
        self._dashboard_cards = []
        self._menu_actions = []

    def register_tab(self, title, factory, *, order=100):
        self._tabs.append({"title": title, "factory": factory, "order": order})
        self._tabs.sort(key=lambda x: x["order"])

    def register_dashboard_card(self, title, factory, *, order=100):
        self._dashboard_cards.append({"title": title, "factory": factory, "order": order})
        self._dashboard_cards.sort(key=lambda x: x["order"])

    def register_menu_action(self, label, callback, *, menu="Addons"):
        self._menu_actions.append({"label": label, "callback": callback, "menu": menu})

    def tabs(self):
        return list(self._tabs)

    def dashboard_cards(self):
        return list(self._dashboard_cards)

    def menu_actions(self):
        return list(self._menu_actions)
