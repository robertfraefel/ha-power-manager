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
- [coordinator.py](custom_components/power_manager/coordinator.py) — `DataUpdateCoordinator` subclass; owns all business logic (surplus allocation, hysteresis, min-runtime, switch control). All mutable state lives here (`_producers`, `_consumers`, `_runtime`, `_base_load_entity`, `_base_load_name`). Persists to `.storage/power_manager` (fixed key = `DOMAIN`, not entry_id, so config survives remove-and-re-add) via HA `Store`.
- [__init__.py](custom_components/power_manager/__init__.py) — entry point; registers HA services, WebSocket API handlers, and the sidebar panel static file. `_config_payload()` is the single function that serialises coordinator state for the frontend.
- [const.py](custom_components/power_manager/const.py) — all shared constants including `INTEGRATION_VERSION`.
- [sensor.py](custom_components/power_manager/sensor.py) / [switch.py](custom_components/power_manager/switch.py) / [select.py](custom_components/power_manager/select.py) — thin `CoordinatorEntity` wrappers that expose production/surplus/running/mode as HA entities.

**Frontend (JavaScript)** — single vanilla Web Component, no build step:
- [panel/power-manager-panel.js](custom_components/power_manager/panel/power-manager-panel.js) — `<power-manager-panel>` custom element. Communicates with the backend **only** via WebSocket (`this._hass.callWS()`). Served as a static file; cache-busted with `?v={INTEGRATION_VERSION}` in the URL registered in `__init__.py`.

### Authoritative config vs. polled snapshot

`coordinator.get_config()` returns the authoritative in-memory state (`self._producers`, `self._consumers`, `self._base_load_entity`). This is what `_config_payload()` uses for structural fields. `coordinator.data` is the polled snapshot produced by `_async_update_data()` — it can be `None` or stale during startup or after a failed poll cycle. **Never read producers/consumers from `coordinator.data`.**

### Surplus algorithm

```
surplus = total_production - base_load
```

`base_load` is typically a whole-house smart meter that **includes** the draw of managed consumers when they are running. The algorithm accounts for this.

Hysteresis (`SURPLUS_HYSTERESIS_FACTOR = 5%`) prevents toggling when production fluctuates near a consumer's threshold. The two conditions are asymmetric by design:

- **Turn ON**: `remaining_surplus ≥ expected * 1.05` — needs a clear margin above expected draw; uses `expected` because the consumer is OFF and its actual draw is unknown.
- **Stay ON**: `remaining_surplus ≥ -(current_power * 0.05)` — tolerates a deficit of up to 5% of the actual measured draw; uses `current_power` (not `expected`) because that is what `base_load` reflects. When `current_power = 0` (not yet measured) the threshold is 0.

The asymmetric band: turn on at +5% of expected, turn off only when deficit exceeds 5% of actual draw.

Budget deduction: `remaining_surplus -= expected` only on fresh turn-ons within the same cycle. Already-running consumers are not re-deducted because their draw is already in `base_load`.

Min-run lock (`on_until_ts`, monotonic): the `extend_timer` flag in `_async_update_data` is `True` only when surplus drives the ON decision, never while the consumer is in hold. This prevents the timer from resetting to its full duration on every scan cycle during a hold.

### Refresh strategy (panel)

Two timers run in parallel:
1. `_pollTimer` — full WS `get_config` round-trip at `scan_interval_seconds` (min 5 s); re-renders all sections.
2. `_lightTimer` — 2 s tick calling `_updateLiveValues()`; updates DOM elements tagged `data-live-watt` / `data-live-switch` directly from `hass.states` without a WS call.

HA can call `set hass()` before `connectedCallback()`, so the panel uses an `_initialized` guard in `set hass()` to run first-render setup (initialise `_pendingEdits`, `_pendingProdEdits`, call `_renderShell`, `_bind`, `_load`) exactly once.

### Key WebSocket commands

| Command | What it does |
|---|---|
| `power_manager/get_config` | Forces `coordinator.async_refresh()` then returns full snapshot |
| `power_manager/set_base` | Updates base-load entity + optional display name |
| `power_manager/add_consumer` / `update_consumer` / `remove_consumer` | CRUD for consumers |
| `power_manager/add_producer` / `update_producer` / `remove_producer` | CRUD for producers |

### HA service actions (useful for debugging)

All registered under the `power_manager` domain. Call from Developer Tools → Actions.

| Service | Fields | Purpose |
|---|---|---|
| `get_config` | — | Show full config in a persistent HA notification |
| `get_producers` / `get_consumers` | — | List producers / consumers in a notification |
| `get_version` | — | Show loaded integration version |
| `clear_producers` / `clear_consumers` / `clear_base_load` | — | Wipe all producers / consumers / base-load entity (useful when storage is corrupted) |
| `add_producer` | `name`, `entity_id` | Add a producer |
| `remove_producer` | `name` | Remove a producer by name |
| `add_consumer` | `name`, `switch_entity`, `power_entity`, `priority`, `expected_power`, `min_run_minutes` | Add a consumer |
| `remove_consumer` | `name` | Remove a consumer by name |
| `set_consumer_mode` | `consumer`, `mode` | Set mode (`auto`/`force_on`/`force_off`/`deactivated`) |
| `set_running` | `running` (bool) | Start or stop the control loop |

Note: the HA service UI converts an empty string field to `null`. To remove an entry with an empty-string name, use `clear_producers` / `clear_consumers` instead.

### Consumer runtime state

Consumer modes (`auto` / `force_on` / `force_off` / `deactivated`) live in `ConsumerRuntime.mode` (in-memory, persisted as `runtime_modes` in storage). The decision chain evaluates modes in this order: `stopped → deactivated → force_on → force_off → daily_limit → auto`. Force modes bypass the daily runtime limit because they represent an explicit user decision (e.g. a boiler booster script). The `on_until_ts` field uses **asyncio monotonic time** (`hass.loop.time()`). It is converted to Unix time before being sent to the frontend: `unix_now + max(0, on_until_ts - now)`. The frontend uses `state.is_on` (the coordinator's actual decision) as ground truth for detecting min-run hold — it does not recompute from timestamps.

### Unsaved-edit preservation (panel)

`_renderConsumers` / `_renderProducers` attach `input` event listeners to every `[data-k]` field on render. Edits are written in real-time into `this._pendingEdits[name]` / `this._pendingProdEdits[name]`. On the next poll-timer re-render, values are restored from these maps before the DOM is cleared. The maps are only cleared on a successful save or delete. This means background reloads never clobber in-progress user edits.

### Simulation

The `simulation/` directory contains HA package YAML (`power_manager_sim.yaml`) and a matching Lovelace dashboard (`pm_sim_dashboard.yaml`) for testing without real hardware. Helpers follow HA's slug-generation convention: entity IDs exactly match the lower-cased, special-char-stripped version of their names (e.g., `input_boolean.sim_boiler_switch` for "Sim · Boiler Switch").

Use `sensor.sim_effective_base_load` as the Power Manager base-load entity (not `input_number.sim_base_load` directly). The effective sensor adds each consumer's draw to the background slider when its switch is ON, simulating a whole-house smart meter — which is the typical real-world setup the algorithm is designed for.
