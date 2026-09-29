import threading
from collections import defaultdict


class EventBus:
    """Tiny thread-safe event bus used by the core, UI, HTTP API, and addons."""

    def __init__(self):
        self._lock = threading.RLock()
        self._handlers = defaultdict(list)

    def subscribe(self, event_name, callback):
        with self._lock:
            self._handlers[event_name].append(callback)

        def unsubscribe():
            with self._lock:
                try:
                    self._handlers[event_name].remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def emit(self, event_name, **payload):
        with self._lock:
            handlers = list(self._handlers.get(event_name, ()))
            handlers += list(self._handlers.get("*", ()))

        for callback in handlers:
            try:
                callback(event_name, payload)
            except Exception:
                # Addons must never be able to crash the adapter core.
                pass
