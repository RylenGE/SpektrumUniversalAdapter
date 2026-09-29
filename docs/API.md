# Spektrum Universal Adapter API

The project deliberately treats the API as a first-class layer. The desktop UI itself is built on the same core/profile/registry interfaces that addons receive.

## Two addon paths

### 1. Python addons

Put a `.py` file in the editable `plugins` folder next to the app/EXE.

It must define:

```python
def setup(api):
    ...
```

`api` is an `AdapterAPI` instance.

Current API version: `2`

Useful methods and properties:

```python
api.get_state()
api.get_channels()
api.get_profile()
api.list_profiles()

api.replace_profile(profile)
api.create_profile(name, profile=None)
api.load_profile(name)
api.save_profile()

api.add_input(definition)
api.update_input(input_id, definition)
api.delete_input(input_id)

api.connect(port)
api.disconnect()
api.start_controller()
api.stop_controller()

api.set_profile_input_passthrough(enabled, allow_external_when_blocked=None)
api.get_profile_input_passthrough()
api.reset_virtual_output(throttle_target=None)

api.set_external_frame(source_id, frame)
api.clear_external_frame(source_id)
api.get_external_frames()
api.register_external_frame_source(source_id, callback, priority=100)
api.unregister_external_frame_source(source_id)
api.build_frame(axes=None, triggers=None, buttons=None)

api.events.subscribe("receiver.packet", callback)
api.events.subscribe("channels.changed", callback)
api.events.subscribe("profile.changed", callback)
api.events.subscribe("*", callback)

api.register_input_type(...)
api.register_tab(...)
api.register_dashboard_card(...)
api.register_menu_action(...)
```

The registries are intentionally exposed directly:

```python
api.input_types
api.ui
api.events
```

That means an addon can inspect already-registered types/UI extensions as well as register its own.

## Custom input types

```python
def evaluator(definition, channels, runtime_state):
    ch = definition["source"]["channel"]
    raw = channels.get(ch)
    if raw is None:
        return {}

    # Return any subset of the virtual frame:
    return {
        "axes": {"left_x": 0.25},
        "triggers": {"right_trigger": 0.0},
        "buttons": {"a"},
    }

api.register_input_type(
    "my_input",
    evaluator,
    display_name="My Custom Input",
)
```

Optional `editor_factory` and `calibration_factory` hooks let an addon own its editing/calibration UI too.

## UI extension API

Add a tab:

```python
def build_tab(parent, api):
    ...

api.register_tab("My Tools", build_tab)
```

Add a dashboard card:

```python
api.register_dashboard_card("Telemetry", build_card)
```

Add a menu action:

```python
api.register_menu_action("Do Thing", callback)
```

The factories receive normal Tkinter parent widgets, so addons can build arbitrary editable UI.

## Feed cut / hard neutral APIs

Addons can now cut profile feed without modifying profile JSON:

```python
# Block normal profile-driven input.
api.set_profile_input_passthrough(False)

# Force virtual controller to default state immediately.
api.reset_virtual_output()

# Re-enable normal profile evaluation later.
api.set_profile_input_passthrough(True)
```

When passthrough is disabled, all profile inputs are ignored by the runtime evaluator.

## External input injection APIs

Addons and local tools can inject extra controller input on top of profile output.

### Static frame injection

```python
api.set_external_frame(
    "my.tool",
    {
        "axes": {"left_x": 0.2},
        "triggers": {"right_trigger": 1.0},
        "buttons": {"a"},
    },
)

# Remove when done:
api.clear_external_frame("my.tool")
```

### Dynamic source injection

```python
def provider(channels, runtime_state):
    # channels = latest receiver raw channels
    # runtime_state = mutable dict scoped to this source id
    return {
        "axes": {"right_x": 0.5},
        "buttons": {"x"},
    }

api.register_external_frame_source("my.provider", provider, priority=200)
```

Lower `priority` values merge first.

## Local HTTP JSON API

The app exposes a localhost-only API:

`http://127.0.0.1:8765/api/v1`

It binds to `127.0.0.1`, not the LAN.

Endpoints:

- `GET /api/v1`
- `GET /api/v1/state`
- `GET /api/v1/channels`
- `GET /api/v1/profile`
- `GET /api/v1/profiles`
- `GET /api/v1/input-types`
- `GET /api/v1/controller/passthrough`
- `GET /api/v1/controller/external-input`
- `PUT /api/v1/profile`
- `PUT /api/v1/controller/passthrough`
- `POST /api/v1/inputs`
- `POST /api/v1/controller/reset`
- `POST /api/v1/controller/external-input/{source_id}`
- `PUT /api/v1/inputs/{id}`
- `DELETE /api/v1/inputs/{id}`
- `DELETE /api/v1/controller/external-input/{source_id}`
- `POST /api/v1/controller/start`
- `POST /api/v1/controller/stop`

This lets addons be written in C#, JavaScript, Rust, PowerShell, etc. without importing the Python application.

Example PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/v1/channels
```

Set passthrough mode via HTTP:

```powershell
Invoke-RestMethod -Method Put `
    -Uri http://127.0.0.1:8765/api/v1/controller/passthrough `
    -ContentType "application/json" `
    -Body '{"enabled": false, "allow_external_when_blocked": true}'
```

Inject external frame via HTTP:

```powershell
Invoke-RestMethod -Method Post `
    -Uri http://127.0.0.1:8765/api/v1/controller/external-input/my-tool `
    -ContentType "application/json" `
    -Body '{"axes":{"left_x":0.5},"buttons":["a"]}'
```

## Profile files are also an API

Profiles live as readable JSON in `profiles/*.json`.

The engine intentionally does not hide calibration or mappings in a binary database. A tool can generate/edit profile files directly and then load them through the API.

## Stability rule

Addons should prefer the `AdapterAPI` object and JSON/HTTP API rather than importing private UI/core implementation files. `AdapterAPI.API_VERSION` is currently `2`.


## Live input monitor hook

Custom input types can participate in the built-in **Live Inputs** display.

Pass a `monitor=` callback when registering the type:

```python
def monitor(definition, channels, runtime_state):
    ch = definition["source"]["channel"]
    raw = channels.get(ch)
    return {
        "kind": "range",        # axis, range, button, state, delta, text
        "value": 0.42,
        "raw": raw,
        "label": "42.0%",
        "minimum": 0.0,
        "maximum": 1.0,
    }

api.register_input_type(
    "my_input",
    evaluator,
    display_name="My Custom Input",
    monitor=monitor,
)
```

This means addons do not have to replace the whole Live Inputs screen just to make
their custom control visible.
