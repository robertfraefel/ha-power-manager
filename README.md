<p align="center">
  <img src="custom_components/power_manager/panel/logo.svg" alt="Power Manager Logo" width="80">
</p>

# HA Power Manager

A Home Assistant custom integration that automatically manages renewable-energy loads based on available solar surplus. When solar production exceeds the household base load, consumers (boiler, washing machine, EV charger, …) are switched on in priority order. When surplus drops they are switched off again.

---

## Architecture

The codebase is split into two distinct layers:

```
ha-power-manager/
└── custom_components/power_manager/
    │
    ├── ── BACKEND (Python) ──────────────────────────────────────────────────
    ├── const.py            Shared constants — domain, config keys, mode names
    ├── coordinator.py      Core engine — polls sensors, allocates surplus,
    │                       controls switches, persists state
    ├── __init__.py         Integration entry point — HA services, WebSocket API,
    │                       sidebar panel registration
    ├── config_flow.py      HA UI flow for initial setup and options editing
    ├── sensor.py           Exposes production / base-load / surplus as HA sensors
    ├── switch.py           Exposes the running state as an HA switch entity
    ├── select.py           Exposes each consumer's mode as an HA select entity
    ├── manifest.json       HA integration metadata (domain, version, deps)
    └── services.yaml       HA service definitions (used by Developer Tools)
    │
    └── panel/              ── FRONTEND (JavaScript) ─────────────────────────
        └── power-manager-panel.js
                            Custom web component rendered in the HA sidebar.
                            Communicates with the backend exclusively via the
                            WebSocket API (power_manager/* commands).
```

### Frontend ↔ Backend communication

```
Browser (panel JS)
      │
      │  WebSocket  power_manager/get_config
      │             power_manager/set_base
      │             power_manager/add_producer / update_producer / remove_producer
      │             power_manager/add_consumer / update_consumer / remove_consumer
      ▼
HA WebSocket API  (__init__.py → _register_ws)
      │
      ▼
PowerManagerCoordinator  (coordinator.py)
      │
      ├── reads   HA entity states (sensor / switch)
      ├── writes  HA switch entities (homeassistant.turn_on/off)
      └── saves   .storage/power_manager_<entry_id>  (persistent config)
```

Entity states (sensor readings, switch states) are also read **directly** by the panel JS from `hass.states` for real-time display without an extra WS round-trip.

---

## How it works

1. **Polling** — The coordinator runs every N seconds (default 10 s, configurable). It reads all producer sensor values, sums them into `total_production`, reads the base-load sensor, and computes:

   ```
   surplus = total_production − base_load
   ```

2. **Surplus allocation** — Consumers are iterated in ascending priority order. In `auto` mode each consumer is switched ON if the remaining surplus meets its `expected_power` threshold, and OFF otherwise.

3. **Hysteresis** — To prevent rapid toggling when solar output fluctuates, the turn-ON threshold is 5 % higher than the turn-OFF threshold (`SURPLUS_HYSTERESIS_FACTOR = 0.05`).

4. **Minimum runtime** — Once a consumer is switched ON it stays on for at least `min_run_minutes` regardless of surplus changes.

5. **Turn-on cooldown** — After a consumer is switched ON, the coordinator waits 5 minutes (`TURN_ON_COOLDOWN_SECONDS = 300`) before switching ON the next consumer. This gives the smart meter / base-load sensor time to reflect the new load. Without this delay the coordinator would see stale surplus values and potentially switch on too many consumers at once. Consumers that are already running are not affected by the cooldown — only fresh OFF → ON transitions are delayed.

6. **Consumer modes**

   | Mode | Behaviour |
   |------|-----------|
   | `auto` | Controlled by the surplus algorithm |
   | `force_on` | Always ON, surplus is still deducted |
   | `force_off` | Always OFF |
   | `deactivated` | Excluded entirely — switch is not touched, surplus not deducted |

7. **Persistence** — All configuration (producers, consumers, modes, running state) is stored in HA's `.storage/power_manager_<entry_id>` file. This file is **not** part of the integration code, so it survives code updates from GitHub or HACS.

---

## HA entities created

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.power_manager_total_production` | Sensor | Sum of all producer values (W) |
| `sensor.power_manager_base_load` | Sensor | Current base-load reading (W) |
| `sensor.power_manager_power_surplus` | Sensor | Production minus base load (W) |
| `switch.power_manager_running` | Switch | Start / stop the control loop |
| `select.power_manager_<name>_mode` | Select | Per-consumer mode control |

The select entities allow controlling consumer modes from automations, dashboards, or voice assistants without opening the Power Manager panel.

---

## Installation

1. Copy `custom_components/power_manager/` into your HA `config/custom_components/` folder.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for *Power Manager*.
4. Configure the base-load sensor and scan interval.
5. Open the **Power Manager** sidebar panel to add producers and consumers.

---

## Managing producers and consumers

| Method | How |
|--------|-----|
| **Sidebar panel** | Open ⚡ Power Manager in the HA sidebar. Add, edit, and delete producers/consumers inline. |
| **Options flow** | Settings → Devices & Services → Power Manager → Configure |
| **Developer Tools** | Call any `power_manager.*` service directly |

---

## Available services

| Service | Description |
|---------|-------------|
| `set_running` | Start or stop the control loop |
| `set_consumer_mode` | Set mode of a consumer (`auto` / `force_on` / `force_off` / `deactivated`) |
| `add_producer` / `update_producer` / `remove_producer` | Manage producers |
| `add_consumer` / `update_consumer` / `remove_consumer` | Manage consumers |
| `get_config` / `get_producers` / `get_consumers` | Show current config as persistent notification |
| `get_version` | Show loaded integration version |
| `update_base_load_entity` | Change the base-load sensor entity |

---

## Development

### Backend (Python)
All Python files follow the standard HA custom integration pattern. Unit tests live in `tests/` and use `unittest.mock` — no HA installation required.

```bash
pip install voluptuous
python -m pytest tests/ -v
```

### Frontend (JavaScript)
The panel is a vanilla Web Component (`<power-manager-panel>`), served as a static file by HA's HTTP layer. No build step is required — edit `panel/power-manager-panel.js` directly and hard-reload the browser (`Ctrl+Shift+R`) to pick up changes.
