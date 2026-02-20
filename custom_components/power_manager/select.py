from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTO, MODE_DEACTIVATED, MODE_FORCE_OFF, MODE_FORCE_ON
from .coordinator import PowerManagerCoordinator

_MODE_OPTIONS = [MODE_AUTO, MODE_FORCE_ON, MODE_FORCE_OFF, MODE_DEACTIVATED]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PowerManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    def _check_new_consumers() -> None:
        consumers = (coordinator.data or {}).get("consumers", [])
        new_names = {c["name"] for c in consumers} - known
        if new_names:
            async_add_entities(
                [ConsumerModeSelect(coordinator, name) for name in sorted(new_names)]
            )
            known.update(new_names)

    _check_new_consumers()
    entry.async_on_unload(coordinator.async_add_listener(_check_new_consumers))


class ConsumerModeSelect(CoordinatorEntity, SelectEntity):
    _attr_options = _MODE_OPTIONS
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator: PowerManagerCoordinator, consumer_name: str) -> None:
        super().__init__(coordinator)
        self._consumer_name = consumer_name
        self._attr_name = f"Power Manager {consumer_name} Mode"
        self._attr_unique_id = f"power_manager_consumer_{consumer_name}_mode"

    @property
    def current_option(self) -> str | None:
        states = (self.coordinator.data or {}).get("consumer_states", {})
        return states.get(self._consumer_name, {}).get("mode", MODE_AUTO)

    @property
    def available(self) -> bool:
        consumers = (self.coordinator.data or {}).get("consumers", [])
        return any(c["name"] == self._consumer_name for c in consumers)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_consumer_mode(self._consumer_name, option)
