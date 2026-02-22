# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests (no HA installation required)
pip install voluptuous pytest
python -m pytest tests/ -v

# Run a single test
python -m pytest tests/test_coordinator.py::TestStateFloat::test_kw_normalised_to_watts -v

# Lint (matches CI)
pip install ruff
ruff check custom_components
```

CI runs `hassfest` (HA manifest validation) + `ruff` lint on every push.

## Workflow rules

- **Always** bump `INTEGRATION_VERSION` in [const.py](custom_components/power_manager/const.py) **and** `version` in [manifest.json](custom_components/power_manager/manifest.json) when any backend Python file changes.
- **Always** bump `PANEL_VERSION` constant at the top of [panel/power-manager-panel.js](custom_components/power_manager/panel/power-manager-panel.js) when the JS changes.
- **Always** commit and push after making changes.

## Architecture

The integration has two completely separate layers that never import each other:

**Backend (Python)** — standard HA custom component pattern:
- [coordinator.py](custom_components/power_manager/coordinator.py) — `DataUpdateCoordinator` subclass; owns all business logic (surplus allocation, hysteresis, min-runtime, switch control). All mutable state lives here (`_producers`, `_consumers`, `_runtime`, `_base_load_entity`, `_base_load_name`). Persists to `.storage/power_manager` (fixed key = `DOMAIN`) via HA `Store`.
- [__init__.py](custom_components/power_manager/__init__.py) — entry point; registers HA services, WebSocket API handlers, and the sidebar panel static file. `_config_payload()` is the single function that serialises coordinator state for the frontend.
- [const.py](custom_components/power_manager/const.py) — all shared constants including `INTEGRATION_VERSION`.
- [sensor.py](custom_components/power_manager/sensor.py) / [switch.py](custom_components/power_manager/switch.py) / [select.py](custom_components/power_manager/select.py) — thin `CoordinatorEntity` wrappers that expose production/surplus/running/mode as HA entities.

**Frontend (JavaScript)** — single vanilla Web Component, no build step:
- [panel/power-manager-panel.js](custom_components/power_manager/panel/power-manager-panel.js) — `<power-manager-panel>` custom element. Communicates with the backend **only** via WebSocket (`this._hass.callWS()`). Served as a static file; cache-busted with `?v={INTEGRATION_VERSION}` in the URL registered in `__init__.py`.

### Authoritative config vs. polled snapshot

`coordinator.get_config()` returns the authoritative in-memory state (`self._producers`, `self._consumers`, `self._base_load_entity`). This is what `_config_payload()` uses for structural fields. `coordinator.data` is the polled snapshot produced by `_async_update_data()` — it can be `None` or stale during startup or after a failed poll cycle. **Never read producers/consumers from `coordinator.data`.**

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

Consumer modes (`auto` / `force_on` / `force_off` / `deactivated`) live in `ConsumerRuntime.mode` (in-memory, persisted as `runtime_modes` in storage). The `on_until_ts` field uses **asyncio monotonic time** (`hass.loop.time()`). It is converted to Unix time before being sent to the frontend: `unix_now + max(0, on_until_ts - now)`. The frontend uses `state.is_on` (the coordinator's actual decision) as ground truth for detecting min-run hold — it does not recompute from timestamps.

### Unsaved-edit preservation (panel)

`_renderConsumers` / `_renderProducers` attach `input` event listeners to every `[data-k]` field on render. Edits are written in real-time into `this._pendingEdits[name]` / `this._pendingProdEdits[name]`. On the next poll-timer re-render, values are restored from these maps before the DOM is cleared. The maps are only cleared on a successful save or delete. This means background reloads never clobber in-progress user edits.

### Simulation

The `simulation/` directory contains HA package YAML (`power_manager_sim.yaml`) and a matching Lovelace dashboard (`pm_sim_dashboard.yaml`) for testing without real hardware. Helpers follow HA's slug-generation convention: entity IDs exactly match the lower-cased, special-char-stripped version of their names (e.g., `input_boolean.sim_boiler_switch` for "Sim · Boiler Switch").
