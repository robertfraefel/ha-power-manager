# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests (no HA installation required)
pip install voluptuous pytest
python -m pytest tests/ -v

# Run a single test
python -m pytest tests/test_coordinator.py::test_state_float_entity_not_found -v

# Lint (matches CI)
pip install ruff
ruff check custom_components
```

## Workflow rules

- **Always** bump `INTEGRATION_VERSION` in [const.py](custom_components/power_manager/const.py) **and** `version` in [manifest.json](custom_components/power_manager/manifest.json) when any backend Python file changes.
- **Always** bump `PANEL_VERSION` constant at the top of [panel/power-manager-panel.js](custom_components/power_manager/panel/power-manager-panel.js) when the JS changes.
- **Always** commit and push after making changes.
- The HA machine does **not** auto-update `panel/` via `git pull` — the user must `scp` changed files manually after each push.

## Architecture

The integration has two completely separate layers that never import each other:

**Backend (Python)** — standard HA custom component pattern:
- [coordinator.py](custom_components/power_manager/coordinator.py) — `DataUpdateCoordinator` subclass; owns all business logic (surplus allocation, hysteresis, min-runtime, switch control). All mutable state lives here (`_producers`, `_consumers`, `_runtime`, `_base_load_entity`, `_base_load_name`). Persists to `.storage/power_manager_<entry_id>` via HA `Store`.
- [__init__.py](custom_components/power_manager/__init__.py) — entry point; registers HA services, WebSocket API handlers, and the sidebar panel static file. `_config_payload()` is the single function that serialises coordinator state for the frontend.
- [const.py](custom_components/power_manager/const.py) — all shared constants including `INTEGRATION_VERSION`.

**Frontend (JavaScript)** — single vanilla Web Component, no build step:
- [panel/power-manager-panel.js](custom_components/power_manager/panel/power-manager-panel.js) — `<power-manager-panel>` custom element. Communicates with the backend **only** via WebSocket (`this._hass.callWS()`). Served as a static file; cache-busted with `?v={INTEGRATION_VERSION}` in the URL registered in `__init__.py`.

### Refresh strategy (panel)
Two timers run in parallel:
1. `_pollTimer` — full WS `get_config` round-trip at `scan_interval_seconds` (min 5 s); re-renders all sections.
2. `_lightTimer` — 2 s tick calling `_updateLiveValues()`; updates DOM elements tagged `data-live-watt` / `data-live-switch` directly from `hass.states` without a WS call.

### Key WebSocket commands
| Command | What it does |
|---|---|
| `power_manager/get_config` | Forces `coordinator.async_refresh()` then returns full snapshot |
| `power_manager/set_base` | Updates base-load entity + optional display name |
| `power_manager/add_consumer` / `update_consumer` / `remove_consumer` | CRUD for consumers |
| `power_manager/add_producer` / `update_producer` / `remove_producer` | CRUD for producers |

### Consumer runtime state
Consumer modes (`auto` / `force_on` / `force_off` / `deactivated`) live in `ConsumerRuntime.mode` (in-memory, persisted as `runtime_modes` in storage). The `on_until_ts` field uses **asyncio monotonic time** (`hass.loop.time()`). It is converted to Unix time before being sent to the frontend: `unix_now + max(0, on_until_ts - now)`.

### Unsaved-edit preservation
`_renderConsumers` tags each `<tr>` with `data-consumer="<name>"` and captures all `[data-k]` input values before clearing the DOM. After re-rendering it restores those values, so background `_pollTimer` reloads do not clobber in-flight user edits.
