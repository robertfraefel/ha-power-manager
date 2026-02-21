"""
Switch platform for the Power Manager integration.

Layer: BACKEND
Exposes the coordinator's *running* flag as an HA switch entity
(switch.power_manager_running).  Turning the switch on/off starts or stops
the surplus control loop without requiring a service call.
"""
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
    """Set up the Power Manager running switch from a config entry."""
    coordinator: PowerManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PMRunningSwitch(coordinator)])


class PMRunningSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity that starts / stops the Power Manager control loop."""

    _attr_name = "Power Manager Running"
    _attr_unique_id = "power_manager_running"

    def __init__(self, coordinator: PowerManagerCoordinator) -> None:
        """Initialise with the shared coordinator."""
        super().__init__(coordinator)

    @property
    def is_on(self) -> bool:
        """Return True when the control loop is active."""
        return bool(self.coordinator.data.get("running", False))

    async def async_turn_on(self, **kwargs) -> None:
        """Start the control loop."""
        self.coordinator.running = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the control loop (switches are not touched immediately)."""
        self.coordinator.running = False
        await self.coordinator.async_request_refresh()
