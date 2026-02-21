"""
Select platform for the Power Manager integration.

Layer: BACKEND
Creates one HA select entity per configured consumer:

    select.power_manager_<consumer_name>_mode

The entity exposes the four operating modes (auto / force_on / force_off /
deactivated) and writes back to the coordinator when the user changes the
selection — from any HA interface: dashboards, automations, voice assistants.

Dynamic consumers
-----------------
Consumers can be added at runtime via the sidebar panel.  A coordinator
listener detects new consumers after each refresh and registers their select
entities on the fly without requiring an integration reload.  Entities for
deleted consumers become unavailable automatically.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTO, MODE_DEACTIVATED, MODE_FORCE_OFF, MODE_FORCE_ON
from .coordinator import PowerManagerCoordinator

# Fixed option order shown in the HA UI dropdown.
_MODE_OPTIONS = [MODE_AUTO, MODE_FORCE_ON, MODE_FORCE_OFF, MODE_DEACTIVATED]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Consumer Mode select entities from a config entry.

    A closure tracks which consumer names already have entities so that newly
    added consumers get their entity registered automatically.
    """
    coordinator: PowerManagerCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    def _check_new_consumers() -> None:
        """Register select entities for consumers that don't have one yet."""
        consumers = (coordinator.data or {}).get("consumers", [])
        new_names = {c["name"] for c in consumers} - known
        if new_names:
            async_add_entities(
                [ConsumerModeSelect(coordinator, name) for name in sorted(new_names)]
            )
            known.update(new_names)

    # Register entities for consumers that exist at setup time.
    _check_new_consumers()
    # Re-run the check after every coordinator refresh to catch new consumers.
    entry.async_on_unload(coordinator.async_add_listener(_check_new_consumers))


class ConsumerModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity controlling a single consumer's operating mode."""

    _attr_options = _MODE_OPTIONS
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator: PowerManagerCoordinator, consumer_name: str) -> None:
        """
        Initialise the select entity.

        Args:
            coordinator:   The shared PowerManagerCoordinator instance.
            consumer_name: Name of the consumer this entity controls.
        """
        super().__init__(coordinator)
        self._consumer_name = consumer_name
        self._attr_name = f"Power Manager {consumer_name} Mode"
        self._attr_unique_id = f"power_manager_consumer_{consumer_name}_mode"

    @property
    def current_option(self) -> str | None:
        """Return the consumer's current runtime mode."""
        states = (self.coordinator.data or {}).get("consumer_states", {})
        return states.get(self._consumer_name, {}).get("mode", MODE_AUTO)

    @property
    def available(self) -> bool:
        """Return False when the consumer has been deleted from the config."""
        consumers = (self.coordinator.data or {}).get("consumers", [])
        return any(c["name"] == self._consumer_name for c in consumers)

    async def async_select_option(self, option: str) -> None:
        """Apply a new mode to the consumer via the coordinator."""
        await self.coordinator.async_set_consumer_mode(self._consumer_name, option)
