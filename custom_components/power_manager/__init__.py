from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
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

PANEL_URL_PATH = "power-manager"
PANEL_STATIC_URL = "/power_manager_static"
PANEL_JS_NAME = "power-manager-panel.js"


def _coordinator(hass: HomeAssistant) -> PowerManagerCoordinator | None:
    return hass.data.get(DOMAIN, {}).get("coordinator")


def _config_payload(coordinator: PowerManagerCoordinator) -> dict[str, Any]:
    data = coordinator.data or {}
    return {
        "integration_version": INTEGRATION_VERSION,
        "running": data.get("running", False),
        "base_load_entity": data.get("base_load_entity", ""),
        "base_load_current_w": data.get("base_load", 0),
        "scan_interval_seconds": data.get("scan_interval_seconds", 0),
        "producers": data.get("producers", []),
        "producer_states": data.get("producer_states", {}),
        "consumers": data.get("consumers", []),
    }


async def _register_panel(hass: HomeAssistant) -> None:
    if hass.data[DOMAIN].get("panel_registered"):
        return

    panel_file = Path(__file__).parent / "panel" / PANEL_JS_NAME
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"{PANEL_STATIC_URL}/{PANEL_JS_NAME}",
                str(panel_file),
                cache_headers=False,
            )
        ]
    )

    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Power Manager",
        sidebar_icon="mdi:flash",
        frontend_url_path=PANEL_URL_PATH,
        config={
            "module_url": f"{PANEL_STATIC_URL}/{PANEL_JS_NAME}",
            "name": "power-manager-panel",
        },
        require_admin=True,
    )
    hass.data[DOMAIN]["panel_registered"] = True


async def _register_ws(hass: HomeAssistant) -> None:
    if hass.data[DOMAIN].get("ws_registered"):
        return

    @websocket_api.websocket_command({"type": "power_manager/get_config"})
    @websocket_api.async_response
    async def ws_get_config(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_request_refresh()
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/set_base",
            "base_load_entity": str,
        }
    )
    @websocket_api.async_response
    async def ws_set_base(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_update_base_load_entity(msg["base_load_entity"])
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/add_producer",
            "name": str,
            "entity_id": str,
        }
    )
    @websocket_api.async_response
    async def ws_add_producer(hass, connection, msg):
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_add_producer(msg["name"], msg["entity_id"])
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/update_producer",
            "name": str,
            "entity_id": str,
        }
    )
    @websocket_api.async_response
    async def ws_update_producer(hass, connection, msg):
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_update_producer(msg["name"], msg["entity_id"])
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/remove_producer",
            "name": str,
        }
    )
    @websocket_api.async_response
    async def ws_remove_producer(hass, connection, msg):
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_remove_producer(msg["name"])
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/add_consumer",
            "name": str,
            "switch_entity": str,
            "power_entity": str,
            "priority": int,
            "expected_power": float,
            "min_run_minutes": float,
        }
    )
    @websocket_api.async_response
    async def ws_add_consumer(hass, connection, msg):
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_add_consumer(
            msg["name"],
            msg["switch_entity"],
            msg["power_entity"],
            msg["priority"],
            msg["expected_power"],
            msg["min_run_minutes"],
        )
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/update_consumer",
            "name": str,
            vol.Optional("switch_entity"): str,
            vol.Optional("power_entity"): str,
            vol.Optional("priority"): int,
            vol.Optional("expected_power"): float,
            vol.Optional("min_run_minutes"): float,
            vol.Optional("mode"): str,
        }
    )
    @websocket_api.async_response
    async def ws_update_consumer(hass, connection, msg):
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_update_consumer(
            name=msg["name"],
            switch_entity=msg.get("switch_entity"),
            power_entity=msg.get("power_entity"),
            priority=msg.get("priority"),
            expected_power=msg.get("expected_power"),
            min_run_minutes=msg.get("min_run_minutes"),
            mode=msg.get("mode"),
        )
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/remove_consumer",
            "name": str,
        }
    )
    @websocket_api.async_response
    async def ws_remove_consumer(hass, connection, msg):
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_remove_consumer(msg["name"])
        connection.send_result(msg["id"], _config_payload(coordinator))

    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_set_base)
    websocket_api.async_register_command(hass, ws_add_producer)
    websocket_api.async_register_command(hass, ws_update_producer)
    websocket_api.async_register_command(hass, ws_remove_producer)
    websocket_api.async_register_command(hass, ws_add_consumer)
    websocket_api.async_register_command(hass, ws_update_consumer)
    websocket_api.async_register_command(hass, ws_remove_consumer)

    hass.data[DOMAIN]["ws_registered"] = True


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def _set_running(call: ServiceCall):
        coordinator = _coordinator(hass)
        if coordinator:
            coordinator.running = call.data["running"]
            await coordinator.async_save_state()
            await coordinator.async_request_refresh()

    async def _set_consumer_mode(call: ServiceCall):
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_set_consumer_mode(call.data["consumer"], call.data["mode"])

    async def _add_producer(call: ServiceCall):
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_add_producer(call.data["name"], call.data["entity_id"])

    async def _remove_producer(call: ServiceCall):
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_remove_producer(call.data["name"])

    async def _update_producer(call: ServiceCall):
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_update_producer(
                name=call.data["name"],
                entity_id=call.data["entity_id"],
            )

    async def _add_consumer(call: ServiceCall):
        coordinator = _coordinator(hass)
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
        coordinator = _coordinator(hass)
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
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_remove_consumer(call.data["name"])

    async def _get_consumers(call: ServiceCall):
        coordinator = _coordinator(hass)
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
        coordinator = _coordinator(hass)
        if coordinator:
            producers = coordinator.data.get("producers", []) if coordinator.data else []
            text = "\n".join([f"- {p.get('name')} | entity={p.get('entity_id')}" for p in producers])
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
        coordinator = _coordinator(hass)
        if coordinator:
            data = _config_payload(coordinator)
            producer_lines = "\n".join(
                [
                    f"  - {p.get('name')} | entity={p.get('entity_id')} | current_w={data.get('producer_states', {}).get(p.get('name'), {}).get('power', 'unknown')}"
                    for p in data.get("producers", [])
                ]
            ) or "  - none"
            consumer_lines = "\n".join(
                [
                    f"  - {c.get('name')} | switch={c.get('switch_entity')} | power={c.get('power_entity')} | prio={c.get('priority')} | expected={c.get('expected_power')}W | min={c.get('min_run_minutes')}min"
                    for c in data.get("consumers", [])
                ]
            ) or "  - none"

            text = (
                f"integration_version: {data.get('integration_version')}\n"
                f"running: {data.get('running')}\n"
                f"base_load_entity: {data.get('base_load_entity')}\n"
                f"base_load_current_w: {data.get('base_load_current_w')}\n"
                f"scan_interval_seconds: {data.get('scan_interval_seconds')}\n\n"
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
        coordinator = _coordinator(hass)
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
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_update_base_load_entity(call.data["entity_id"])

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
        schema=vol.Schema({vol.Required("name"): cv.string, vol.Required("entity_id"): cv.entity_id}),
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
        schema=vol.Schema({vol.Required("name"): cv.string, vol.Required("entity_id"): cv.entity_id}),
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
    hass.services.async_register(DOMAIN, SERVICE_GET_CONSUMERS, _get_consumers, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_GET_PRODUCERS, _get_producers, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_GET_CONFIG, _get_config, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_GET_VERSION, _get_version, schema=vol.Schema({}))
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

    await _register_panel(hass)
    await _register_ws(hass)

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
    if entry.version > 2:
        return False

    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2)

    return True
