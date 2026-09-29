Testing and Debug HTTP endpoints

New helper endpoints (local-only) for testing, debugging, and AI-driven development:

- POST /api/v1/test/inject-channels
  Body: { "channels": { "0": 1500, "1": 1200 } }
  Injects a fake receiver snapshot. Replaces live receiver data until cleared.

- DELETE /api/v1/test/inject-channels
  Clears any injected snapshot and resumes using the live receiver.

- GET /api/v1/test/injected-channels
  Returns current injected channels or null.

- GET /api/v1/debug
  Returns an aggregated debug object with:
    - state: api.get_state()
    - last_frame: last evaluated virtual frame (or null)
    - injected: current injected channels
    - debug_log: recent debug events

Security/notes:
- These endpoints are localhost-only and not exposed to the network.
- Use them only for development/testing; they allow full simulation of receiver inputs.
