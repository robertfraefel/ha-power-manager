"""
Sensor platform for the Power Manager integration.

Layer: BACKEND
Exposes three read-only HA sensor entities derived from the coordinator's
computed data:

  - Power Manager Total Production  (sum of all producer entity values, W)
  - Power Manager Base Load         (base-load sensor reading, W)
  - Power Manager Power Surplus     (production minus base load, W)

These sensors update automatically whenever the coordinator refreshes.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PowerManagerCoordinator
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Power Manager sensor entities from a config entry."""
    coordinator: PowerManagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PMSensor(coordinator, "total_production", "Total Production"),
        PMSensor(coordinator, "base_load", "Base Load"),
        PMSensor(coordinator, "surplus", "Power Surplus"),
    ]
    async_add_entities(entities)


class PMSensor(CoordinatorEntity, SensorEntity):
    """A sensor entity that reflects a single key from the coordinator's data dict."""

    def __init__(self, coordinator: PowerManagerCoordinator, key: str, name: str) -> None:
        """
        Initialise the sensor.

        Args:
            coordinator: The shared PowerManagerCoordinator instance.
            key:         The coordinator.data key whose value this sensor exposes.
            name:        Human-readable suffix appended to "Power Manager ".
        """
        super().__init__(coordinator)
        self._key = key
        self._attr_name = f"Power Manager {name}"
        self._attr_unique_id = f"power_manager_{key}"
        self._attr_native_unit_of_measurement = "W"

    @property
    def native_value(self):
        """Return the current sensor value from coordinator data."""
        return self.coordinator.data.get(self._key)
