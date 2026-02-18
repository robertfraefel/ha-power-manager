from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, INTEGRATION_VERSION, PLATFORMS, VALID_MODES
from .coordinator import PowerManagerCoordinator

SERVICE_SET_RUNNING = "set_running"
SERVICE_SET_CONSUMER_MODE = "set_consumer_mode"
SERVICE_ADD_PRODUCER = "add_producer"
SERVICE_REMOVE_PRODUCER = "remove_producer"
SERVICE_UPDATE_PRODUCER = "update_producer"
SERVICE_ADD_CONSUMER = "add_consumer"
SERVICE_UPDATE_CONSUMER = "update_consumer"
SERVICE_REMOVE_CONSUMER = "remove_consumer"
SERVICE_GET_CONSUMERS = "get_consumers"
SERVICE_GET_PRODUCERS = "get_producers"
SERVICE_GET_CONFIG = "get_config"
SERVICE_GET_VERSION = "get_version"
SERVICE_GET_BASE_LOAD_ENTITY = "get_base_load_entity"
SERVICE_UPDATE_BASE_LOAD_ENTITY = "update_base_load_entity"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    def _coordinator() -> PowerManagerCoordinator | None:
        return hass.data[DOMAIN].get("coordinator")

    async def _set_running(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            coordinator.running = call.data["running"]
            await coordinator.async_save_state()
            await coordinator.async_request_refresh()

    async def _set_consumer_mode(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_set_consumer_mode(call.data["consumer"], call.data["mode"])

    async def _add_producer(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_add_producer(call.data["name"], call.data["entity_id"])

    async def _remove_producer(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_remove_producer(call.data["name"])

    async def _update_producer(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_update_producer(
                name=call.data["name"],
                entity_id=call.data["entity_id"],
            )

    async def _add_consumer(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_add_consumer(
                name=call.data["name"],
                switch_entity=call.data["switch_entity"],
                power_entity=call.data["power_entity"],
                priority=call.data["priority"],
                expected_power=call.data["expected_power"],
                min_run_minutes=call.data["min_run_minutes"],
            )

    async def _update_consumer(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_update_consumer(
                name=call.data["name"],
                switch_entity=call.data.get("switch_entity"),
                power_entity=call.data.get("power_entity"),
                priority=call.data.get("priority"),
                expected_power=call.data.get("expected_power"),
                min_run_minutes=call.data.get("min_run_minutes"),
                mode=call.data.get("mode"),
            )

    async def _remove_consumer(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_remove_consumer(call.data["name"])

    async def _get_consumers(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            consumers = coordinator.data.get("consumers", []) if coordinator.data else []
            text = "\n".join(
                [
                    f"- {c.get('name')} | switch={c.get('switch_entity')} | power={c.get('power_entity')} | prio={c.get('priority')} | expected={c.get('expected_power')}W | min={c.get('min_run_minutes')}min"
                    for c in consumers
                ]
            )
            if not text:
                text = "No consumers configured."

            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Power Manager - Consumers",
                    "message": text,
                    "notification_id": "power_manager_consumers",
                },
                blocking=True,
            )

    async def _get_producers(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            producers = coordinator.data.get("producers", []) if coordinator.data else []
            text = "\n".join(
                [
                    f"- {p.get('name')} | entity={p.get('entity_id')}"
                    for p in producers
                ]
            )
            if not text:
                text = "No producers configured."

            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Power Manager - Producers",
                    "message": text,
                    "notification_id": "power_manager_producers",
                },
                blocking=True,
            )

    async def _get_config(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            data = coordinator.data or {}
            producers = data.get("producers", [])
            producer_states = data.get("producer_states", {})
            consumers = data.get("consumers", [])
            base_load_entity = data.get("base_load_entity", "unknown")
            base_load_current = data.get("base_load", "unknown")
            interval = data.get("scan_interval_seconds", "unknown")
            running = data.get("running", False)

            producer_lines = "\n".join(
                [
                    f"  - {p.get('name')} | entity={p.get('entity_id')} | current_w={producer_states.get(p.get('name'), {}).get('power', 'unknown')}"
                    for p in producers
                ]
            ) or "  - none"
            consumer_lines = "\n".join(
                [
                    f"  - {c.get('name')} | switch={c.get('switch_entity')} | power={c.get('power_entity')} | prio={c.get('priority')} | expected={c.get('expected_power')}W | min={c.get('min_run_minutes')}min"
                    for c in consumers
                ]
            ) or "  - none"

            text = (
                f"integration_version: {INTEGRATION_VERSION}\n"
                f"running: {running}\n"
                f"base_load_entity: {base_load_entity}\n"
                f"base_load_current_w: {base_load_current}\n"
                f"scan_interval_seconds: {interval}\n\n"
                f"producers:\n{producer_lines}\n\n"
                f"consumers:\n{consumer_lines}"
            )

            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Power Manager - Full Config",
                    "message": text,
                    "notification_id": "power_manager_config",
                },
                blocking=True,
            )

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_PRODUCER,
        _add_producer,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Required("entity_id"): cv.entity_id,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_PRODUCER,
        _remove_producer,
        schema=vol.Schema({vol.Required("name"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_PRODUCER,
        _update_producer,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Required("entity_id"): cv.entity_id,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_CONSUMER,
        _add_consumer,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Required("switch_entity"): cv.entity_id,
                vol.Required("power_entity"): cv.entity_id,
                vol.Required("priority"): vol.All(int, vol.Range(min=1, max=999)),
                vol.Required("expected_power"): vol.Coerce(float),
                vol.Required("min_run_minutes"): vol.Coerce(float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CONSUMER,
        _update_consumer,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Optional("switch_entity"): cv.entity_id,
                vol.Optional("power_entity"): cv.entity_id,
                vol.Optional("priority"): vol.All(int, vol.Range(min=1, max=999)),
                vol.Optional("expected_power"): vol.Coerce(float),
                vol.Optional("min_run_minutes"): vol.Coerce(float),
                vol.Optional("mode"): vol.In(VALID_MODES),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_CONSUMER,
        _remove_consumer,
        schema=vol.Schema({vol.Required("name"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CONSUMERS,
        _get_consumers,
        schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PRODUCERS,
        _get_producers,
        schema=vol.Schema({}),
    )
    async def _get_version(call: ServiceCall):
        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Power Manager - Version",
                "message": f"integration_version: {INTEGRATION_VERSION}",
                "notification_id": "power_manager_version",
            },
            blocking=True,
        )

    async def _get_base_load_entity(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            data = coordinator.data or {}
            entity = data.get("base_load_entity", "unknown")
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Power Manager - Base Load Entity",
                    "message": f"base_load_entity: {entity}",
                    "notification_id": "power_manager_base_load_entity",
                },
                blocking=True,
            )

    async def _update_base_load_entity(call: ServiceCall):
        coordinator = _coordinator()
        if coordinator:
            await coordinator.async_update_base_load_entity(call.data["entity_id"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CONFIG,
        _get_config,
        schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_VERSION,
        _get_version,
        schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_BASE_LOAD_ENTITY,
        _get_base_load_entity,
        schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_BASE_LOAD_ENTITY,
        _update_base_load_entity,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}),
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = PowerManagerCoordinator(hass, entry)
    await coordinator.async_initialize()
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Keep compatibility across config flow revisions.
    if entry.version > 2:
        return False

    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2)

    return True
