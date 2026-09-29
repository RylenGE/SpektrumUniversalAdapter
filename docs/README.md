# Spektrum Universal Adapter v3

This is the generalized version of the project.

The adapter is no longer hard-coded around throttle/roll/pitch/yaw or six channels. Its architecture is:

**DSMX/SRXL2 receiver → Arduino bridge → dynamic raw channels → user-defined input objects → virtual Xbox controller**

## Built-in input objects

- **Range / Slider** — min + max. Works for throttle sticks, knobs, sliders, levers, etc.
- **Centered Axis** — negative + center + positive.
- **Button** — released + pressed.
- **Multi-State Switch** — any 2–8 recorded states.
- **Delta / Step** — detects step-like changes such as trim buttons. This is intentionally advanced because a moving analog axis can sometimes resemble a step.

Calibration records every detected channel and automatically chooses the channel with the largest relevant change.

## Dynamic channels

Firmware protocol v2 sends a 32-bit channel mask and only the values that exist. The desktop app generates the raw-channel list dynamically.

The desktop parser remains backward compatible with the previous fixed-six-channel v1 firmware, so the app can still run before you flash the new firmware.

## Profiles

Profiles are human-readable JSON files in:

`profiles/`

Each input object is independent and can be added, removed, recalibrated, renamed, remapped, or edited by addons.

## Addon/API architecture

The API is deliberately first-class:

- editable Python addons in `plugins/`
- event bus
- input-type registry
- UI registry
- profile read/write API
- receiver/controller actions
- localhost JSON HTTP API at `http://127.0.0.1:8765/api/v1`

Read `API.md`.

The example addon in `plugins/example_addon.py` demonstrates a custom tab, event subscription, and menu action.

## Existing setup migration

If you put the previous:

- `spektrum_calibration.json`
- `controller_settings.json`

next to this version on first run, and there are no profiles yet, the program automatically creates a `Migrated Controller` profile containing the old axes, CH5 switch, and CH6 trigger.

## Signal-loss watchdog

Each profile has `settings.watchdog_ms` (default 150 ms).

Because generic inputs no longer have hard-coded semantics, the app does **not** assume every range is throttle. For a throttle-like range, edit it and enable:

**On signal loss, drive this range to minimum**

All other axes/buttons/triggers return to neutral/released during watchdog safe state.

## Running

1. Install dependencies:
   `install_dependencies.bat`
2. Run:
   `run_adapter.bat`
3. Bind/power the DSMX receiver.
4. Connect.
5. Add/calibrate input objects.
6. Start the virtual controller.

## Windows EXE

Run:

`build_windows_release.bat`

It creates an editable release folder containing:

- `Spektrum Universal Adapter.exe`
- `plugins/`
- `profiles/`
- `API.md`
- `README.md`

The external `plugins` and `profiles` folders stay editable without rebuilding the EXE.


## Live Inputs display

v4 adds a dedicated **Live Inputs** tab.

Every configured object gets a live display:

- Range / Slider: 0–100% style bar, or -1..+1 when mapped to a stick axis
- Centered Axis: centered -1..+1 bar
- Button: live released/pressed indicator
- Multi-State Switch: segmented state display
- Delta / Step: live delta readout
- Custom addon input: can provide a `monitor=` callback through the API

Analog tuning in the input editor now uses actual sliders for expo, sensitivity,
and deadzone. Those changes preview live while the editor is open; Save persists
them and Cancel rolls the preview back.
