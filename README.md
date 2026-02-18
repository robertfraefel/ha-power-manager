# Home Assistant Power Manager (Custom Integration)

A native Home Assistant custom integration that replaces the Power-Manager_BackEnd logic inside HA.

## Features

- Native HA integration (`custom_components/power_manager`)
- Periodic surplus control loop
- Consumer priority + minimum runtime lock
- Manual consumer modes: `auto`, `force_on`, `force_off`
- Services:
  - `power_manager.set_running`
  - `power_manager.set_consumer_mode`
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

## Notes

- This first release focuses on scheduler logic and services.
- Next step can add a richer UI editor for producers/consumers and full diagnostics.
