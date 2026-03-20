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
      │             power_manager/set_scan_interval
      ▼
HA WebSocket API  (__init__.py → _register_ws)
      │
      ▼
PowerManagerCoordinator  (coordinator.py)
      │
      ├── reads   HA entity states (sensor / switch)
      ├── writes  HA switch entities (homeassistant.turn_on/off)
      └── saves   .storage/power_manager  (persistent config)
```

Entity states (sensor readings, switch states) are also read **directly** by the panel JS from `hass.states` for real-time display without an extra WS round-trip.

---

## How it works

The coordinator runs a **4-phase update cycle** every N seconds (default 10 s, configurable in the dashboard):

### Phase 1 — Decision

Consumers are evaluated in **ascending priority order** (P1 first). For each consumer in `auto` mode:

1. **Already ON → stay ON** when `remaining_surplus ≥ -(current_power × 5%)`. The base load already includes this consumer's draw, so a small negative margin is tolerated.
2. **OFF → turn ON** when `remaining_surplus ≥ expected_power × 1.05` **and** the global cooldown has expired. The 5% margin prevents toggling near the threshold.
3. **Min-run hold** — if surplus drops but `on_until_ts` has not elapsed, the consumer stays ON regardless.
4. **Cooldown** — if surplus is sufficient but a recent turn-on is still within its cooldown window, the consumer waits.

Fresh turn-ons deduct `expected_power` from `remaining_surplus` so lower-priority consumers see the correct reduced budget.

### Phase 1.5 — Priority preemption

After all decisions are computed: if a **higher-priority** auto consumer is OFF (insufficient surplus) and a **lower-priority** auto consumer is ON (staying on via hysteresis), the lower-priority consumer is **preempted** — forced OFF so its load frees up surplus for the next cycle. Consumers in their min-run hold window or in `force_on` mode are exempt from preemption.

### Phase 2 — Turn OFF (incremental shedding)

Consumers decided OFF are turned off in **reverse priority order** (lowest priority first). **At most one active consumer is shed per cycle.** Remaining OFF-candidates are deferred — their `runtime.is_on` stays `True` so the next cycle's hysteresis evaluates them with the updated (higher) surplus before deciding whether further shedding is needed. This prevents unnecessarily cycling high-priority consumers when shedding a lower-priority one suffices.

Consumers that are already OFF (from a previous cycle) are corrected unconditionally but do **not** consume the one-shed-per-cycle slot.

### Phase 3 — Turn ON

Consumers decided ON are turned on in **forward priority order** (highest priority first). **At most one fresh OFF→ON turn-on per cycle.** After turning on a consumer, the cooldown is armed (using that consumer's `cooldown_seconds`) and remaining candidates are deferred to the next cycle. Already-running consumers are re-affirmed without consuming the turn-on slot.

### Phase 4 — State update

`runtime.is_on` is updated for each consumer, and the `consumer_states` dict is built for the API/panel.

---

## Consumer configuration

Each consumer has the following fields:

| Field | Description | Default |
|-------|-------------|---------|
| **Name** | Unique display name | — |
| **Switch entity** | HA switch entity to control (e.g. `switch.boiler`) | — |
| **Power sensor entity** | HA sensor measuring actual draw (W) | — |
| **Priority** | Lower number = higher priority (P1 first). Must be unique. | — |
| **Expected power (W)** | Estimated draw used for surplus budgeting | — |
| **Min run time (min)** | Minimum on-time per activation to protect appliances | 0 |
| **Cooldown (s)** | Seconds to block *all* other turn-ons after this consumer starts. Gives the smart meter time to reflect the new load. | 300 |
| **Mode** | `auto` / `force_on` / `force_off` / `deactivated` | `auto` |

### Hysteresis

To prevent rapid toggling when solar output fluctuates near a consumer's threshold:

- **Turn ON** requires `surplus ≥ expected × 1.05` (5% above expected draw)
- **Stay ON** tolerates `surplus ≥ -(current_power × 0.05)` (5% deficit of actual draw)

The asymmetric band provides a dead zone between the on/off thresholds.

### Consumer modes

| Mode | Behaviour |
|------|-----------|
| `auto` | Controlled by the surplus algorithm |
| `force_on` | Always ON — surplus is still deducted from budget |
| `force_off` | Always OFF |
| `deactivated` | Excluded entirely — switch is not touched, surplus not deducted |

### Cooldown

After a consumer is switched ON, the coordinator blocks all other turn-ons for that consumer's `cooldown_seconds` (default 300s / 5 minutes). This gives the smart meter / base-load sensor time to reflect the new load. Without this delay the coordinator would see stale surplus values and potentially cascade too many consumers at once.

- Already-running consumers are **not affected** by the cooldown — only fresh OFF→ON transitions are delayed
- Each consumer can have a different cooldown value (e.g. 30s for a small device, 300s for a heat pump)

### Startup warmup

After a HA reboot, the coordinator skips all switch decisions for the first 2 scan cycles. This gives entity states time to settle before the algorithm acts on potentially stale values.

---

## Logging

Switch events (ON/OFF) are logged at **WARNING** level so they appear in `ha core logs` without extra configuration:

```
Consumer 'Boiler' (P1) turned ON — on: surplus 850.0W >= 630.0W |
  production=5250W, base_load=4400W, surplus=850W, min_run=15min, cooldown=300s

Consumer 'Poolpumpe' (P3) turned OFF — off: surplus -150.0W < -29.5W |
  production=1200W, base_load=1350W, surplus=-150W
```

View on HA OS:
```bash
ha core logs | grep "turned ON\|turned OFF"
```

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

## Persistence

All configuration (producers, consumers, modes, running state, scan interval) is stored in HA's `.storage/power_manager` file. This file uses a **fixed storage key** (not tied to the config entry ID), so configuration survives a remove-and-re-add of the integration during updates.

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
| `add_consumer` / `update_consumer` / `remove_consumer` | Manage consumers (including `cooldown_seconds`) |
| `get_config` / `get_producers` / `get_consumers` | Show current config as persistent notification |
| `get_version` | Show loaded integration version |
| `update_base_load_entity` | Change the base-load sensor entity |
| `clear_producers` / `clear_consumers` / `clear_base_load` | Wipe all entries (useful for corrupted storage) |

---

## Development

### Backend (Python)
All Python files follow the standard HA custom integration pattern. Unit tests live in `tests/` and use `unittest.mock` — no HA installation required.

```bash
pip install voluptuous pytest
python -m pytest tests/ -v
```

67 tests covering: surplus allocation, hysteresis, priority preemption, incremental shedding, cooldown (per-consumer), startup warmup, priority uniqueness, consumer/producer CRUD, budget deduction, min-run timer, edge cases, and persistence.

### Linting

```bash
pip install ruff
ruff check custom_components
```

### Frontend (JavaScript)
The panel is a vanilla Web Component (`<power-manager-panel>`), served as a static file by HA's HTTP layer. No build step is required — edit `panel/power-manager-panel.js` directly and hard-reload the browser (`Ctrl+Shift+R`) to pick up changes. The panel URL is cache-busted with `?v={INTEGRATION_VERSION}`.
