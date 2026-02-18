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
    coordinator: PowerManagerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PMSensor(coordinator, "total_production", "Total Production"),
        PMSensor(coordinator, "base_load", "Base Load"),
        PMSensor(coordinator, "surplus", "Power Surplus"),
    ]
    async_add_entities(entities)


class PMSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: PowerManagerCoordinator, key: str, name: str):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = f"Power Manager {name}"
        self._attr_unique_id = f"power_manager_{key}"
        self._attr_native_unit_of_measurement = "W"

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)
