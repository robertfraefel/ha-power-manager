from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities([PMRunningSwitch(coordinator)])


class PMRunningSwitch(CoordinatorEntity, SwitchEntity):
    _attr_name = "Power Manager Running"
    _attr_unique_id = "power_manager_running"

    def __init__(self, coordinator: PowerManagerCoordinator):
        super().__init__(coordinator)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("running", False))

    async def async_turn_on(self, **kwargs):
        self.coordinator.running = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.running = False
        await self.coordinator.async_request_refresh()
