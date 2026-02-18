# Home Assistant Power Manager (Custom Integration)

A native Home Assistant custom integration that replaces the Power-Manager_BackEnd logic inside HA.

## Features

- Native HA integration (`custom_components/power_manager`)
- Periodic surplus control loop
- Consumer priority + minimum runtime lock
- Manual consumer modes: `auto`, `force_on`, `force_off`
- Persistent configuration storage (survives Home Assistant restarts)
- Services:
  - `power_manager.set_running`
  - `power_manager.set_consumer_mode`
  - `power_manager.add_producer`
  - `power_manager.remove_producer`
  - `power_manager.add_consumer`
  - `power_manager.update_consumer`
  - `power_manager.remove_consumer`
- Entities:
  - `switch.power_manager_running`
  - `sensor.power_manager_total_production`
  - `sensor.power_manager_base_load`
  - `sensor.power_manager_power_surplus`

## Installation

1. Copy `custom_components/power_manager` into your HA config directory.
2. Restart Home Assistant.
3. Add integration: **Settings → Devices & Services → Add Integration → Power Manager**.

## Configuration format (JSON)

### Producers
```json
[
  {"name": "PV", "entity_id": "sensor.pv_power"}
]
```

### Consumers
```json
[
  {
    "name": "Boiler",
    "switch_entity": "switch.boiler",
    "power_entity": "sensor.boiler_power",
    "priority": 1,
    "expected_power": 1200,
    "min_run_minutes": 10
  }
]
```

## Managing devices (add/update/delete)

Use **Developer Tools → Actions** and call these services:

- Add producer: `power_manager.add_producer`
- Remove producer: `power_manager.remove_producer`
- Add consumer: `power_manager.add_consumer`
- Update consumer: `power_manager.update_consumer`
- Remove consumer: `power_manager.remove_consumer`

Changes are persisted immediately and applied without re-adding the integration.

## Notes

- Device management is currently service-driven (no dedicated Lovelace editor card yet).
- A richer visual editor can be added in a next release.
