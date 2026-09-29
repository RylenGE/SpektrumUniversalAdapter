import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class LocalAPIServer:
    """
    Localhost-only JSON API for addons written in any language.

    This intentionally binds to 127.0.0.1, never all interfaces.
    """

    def __init__(self, api, port=8765):
        self.api = api
        self.port = int(port)
        self.server = None
        self.thread = None

    def start(self):
        if self.server:
            return

        api = self.api

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def _json(self, status, payload):
                data = json.dumps(payload, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0:
                    return {}
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def do_GET(self):
                path = urlparse(self.path).path

                if path == "/api/v1":
                    self._json(200, {
                        "api_version": api.API_VERSION,
                        "endpoints": [
                            "GET /api/v1/state",
                            "GET /api/v1/channels",
                            "GET /api/v1/profile",
                            "GET /api/v1/profiles",
                            "GET /api/v1/input-types",
                            "GET /api/v1/controller/passthrough",
                            "GET /api/v1/controller/external-input",
                            "PUT /api/v1/profile",
                            "PUT /api/v1/controller/passthrough",
                            "POST /api/v1/inputs",
                            "POST /api/v1/controller/reset",
                            "POST /api/v1/controller/external-input/{source_id}",
                            "PUT /api/v1/inputs/{id}",
                            "DELETE /api/v1/inputs/{id}",
                            "DELETE /api/v1/controller/external-input/{source_id}",
                            "POST /api/v1/controller/start",
                            "POST /api/v1/controller/stop",
                            "POST /api/v1/test/inject-channels",
                            "DELETE /api/v1/test/inject-channels",
                            "GET /api/v1/test/injected-channels",
                            "GET /api/v1/debug",
                        ],
                    })
                elif path == "/api/v1/state":
                    self._json(200, api.get_state())
                elif path == "/api/v1/channels":
                    self._json(200, api.get_channels())
                elif path == "/api/v1/profile":
                    self._json(200, api.get_profile())
                elif path == "/api/v1/profiles":
                    self._json(200, api.list_profiles())
                elif path == "/api/v1/input-types":
                    self._json(200, {
                        key: {
                            "id": info["id"],
                            "display_name": info["display_name"],
                        }
                        for key, info in api.input_types.all().items()
                    })
                elif path == "/api/v1/controller/passthrough":
                    self._json(200, api.get_profile_input_passthrough())
                elif path == "/api/v1/controller/external-input":
                    self._json(200, api.get_external_frames())
                elif path == "/api/v1/test/injected-channels":
                    self._json(200, {"injected": api.get_injected_channels()})
                elif path == "/api/v1/debug":
                    self._json(200, {
                        "state": api.get_state(),
                        "last_frame": api.get_last_frame(),
                        "injected": api.get_injected_channels(),
                        "debug_log": api.get_debug_log(limit=50),
                    })
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                path = urlparse(self.path).path

                try:
                    body = self._body()

                    if path == "/api/v1/inputs":
                        self._json(201, api.add_input(body, save=True))
                    elif path == "/api/v1/controller/start":
                        api.start_controller()
                        self._json(200, {"ok": True})
                    elif path == "/api/v1/controller/stop":
                        api.stop_controller()
                        self._json(200, {"ok": True})
                    elif path == "/api/v1/controller/reset":
                        throttle_target = body.get("throttle_target")
                        api.reset_virtual_output(throttle_target=throttle_target)
                        self._json(200, {"ok": True})
                    elif path.startswith("/api/v1/controller/external-input/"):
                        source_id = path[len("/api/v1/controller/external-input/"):]
                        api.set_external_frame(source_id, body)
                        self._json(200, {"ok": True, "source_id": source_id})
                    elif path == "/api/v1/test/inject-channels":
                        # body: { "channels": {"0": 1500, "1": 1500} }
                        channels = body.get("channels", {})
                        api.set_injected_channels(channels)
                        self._json(200, {"ok": True, "injected": api.get_injected_channels()})
                    else:
                        self._json(404, {"error": "not found"})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})

            def do_PUT(self):
                path = urlparse(self.path).path

                try:
                    body = self._body()

                    if path == "/api/v1/profile":
                        self._json(200, api.replace_profile(body, save=True))
                        return

                    if path == "/api/v1/controller/passthrough":
                        enabled = bool(body.get("enabled", True))
                        allow_external = body.get("allow_external_when_blocked")
                        api.set_profile_input_passthrough(
                            enabled,
                            allow_external_when_blocked=allow_external,
                        )
                        self._json(200, api.get_profile_input_passthrough())
                        return

                    prefix = "/api/v1/inputs/"
                    if path.startswith(prefix):
                        input_id = path[len(prefix):]
                        self._json(200, api.update_input(input_id, body, save=True))
                        return

                    self._json(404, {"error": "not found"})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})

            def do_DELETE(self):
                path = urlparse(self.path).path
                prefix = "/api/v1/inputs/"

                if path.startswith(prefix):
                    input_id = path[len(prefix):]
                    self._json(200, {"deleted": api.delete_input(input_id, save=True)})
                elif path.startswith("/api/v1/controller/external-input/"):
                    source_id = path[len("/api/v1/controller/external-input/"):]
                    self._json(200, {
                        "cleared": api.clear_external_frame(source_id),
                        "source_id": source_id,
                    })
                elif path == "/api/v1/test/inject-channels":
                    cleared = api.clear_injected_channels()
                    self._json(200, {"ok": True, "cleared": True})
                else:
                    self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.server = None
        self.thread = None
