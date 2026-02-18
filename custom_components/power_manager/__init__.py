from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS, VALID_MODES
from .coordinator import PowerManagerCoordinator

SERVICE_SET_RUNNING = "set_running"
SERVICE_SET_CONSUMER_MODE = "set_consumer_mode"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def _set_running(call: ServiceCall):
        coordinator: PowerManagerCoordinator = hass.data[DOMAIN].get("coordinator")
        if coordinator:
            coordinator.running = call.data["running"]
            await coordinator.async_request_refresh()

    async def _set_consumer_mode(call: ServiceCall):
        coordinator: PowerManagerCoordinator = hass.data[DOMAIN].get("coordinator")
        if coordinator:
            await coordinator.async_set_consumer_mode(call.data["consumer"], call.data["mode"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_RUNNING,
        _set_running,
        schema=vol.Schema({vol.Required("running"): cv.boolean}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CONSUMER_MODE,
        _set_consumer_mode,
        schema=vol.Schema(
            {
                vol.Required("consumer"): cv.string,
                vol.Required("mode"): vol.In(VALID_MODES),
            }
        ),
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = PowerManagerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN]["coordinator"] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
