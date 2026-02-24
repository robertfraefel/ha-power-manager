"""
Integration entry point for the Power Manager custom component.

Layer: BACKEND

Responsibilities
----------------
1. async_setup        — Register all HA services (action calls from Developer
                        Tools or automations), register the WebSocket API
                        handlers, and mount the sidebar panel static file.

2. async_setup_entry  — Instantiate and initialise PowerManagerCoordinator,
                        forward setup to each entity platform (sensor, switch,
                        select).

3. async_unload_entry — Save coordinator state to storage before teardown,
                        then unload entity platforms.

4. async_migrate_entry — Handle config-entry version migrations.

WebSocket API (power_manager/*)
--------------------------------
All commands follow the pattern:
    frontend sends  → { type: "power_manager/<cmd>", ...fields }
    backend replies → { id, type: "result", success: true, result: <config_payload> }

Registered commands:
    get_config          Return full config snapshot.
    set_base            Update base-load sensor entity.
    add_producer        Add a producer.
    update_producer     Update producer entity / rename producer.
    remove_producer     Remove a producer.
    add_consumer        Add a consumer.
    update_consumer     Update consumer fields (all optional except name).
    remove_consumer     Remove a consumer.

Panel
-----
The frontend JS file (panel/power-manager-panel.js) is served as a static
file at /power_manager_static/power-manager-panel.js and registered as a
custom sidebar panel under the URL path "power-manager".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, INTEGRATION_VERSION, PLATFORMS, VALID_MODES
from .coordinator import PowerManagerCoordinator

# ── service name constants ─────────────────────────────────────────────────

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
SERVICE_CLEAR_PRODUCERS = "clear_producers"
SERVICE_CLEAR_CONSUMERS = "clear_consumers"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# ── panel constants ────────────────────────────────────────────────────────

# URL path under which the panel is reachable in the HA frontend.
PANEL_URL_PATH = "power-manager"
# Static URL prefix used to serve frontend assets.
PANEL_STATIC_URL = "/power_manager_static"
# Filename of the Web Component (relative to the panel/ subdirectory).
PANEL_JS_NAME = "power-manager-panel.js"


# ── private helpers ────────────────────────────────────────────────────────

def _coordinator(hass: HomeAssistant) -> PowerManagerCoordinator | None:
    """Return the active coordinator, or None if not yet loaded."""
    return hass.data.get(DOMAIN, {}).get("coordinator")


def _config_payload(coordinator: PowerManagerCoordinator) -> dict[str, Any]:
    """Build the JSON payload returned to the frontend after every WS command.

    Static config (producers, consumers, base-load) comes from coordinator.get_config()
    — the authoritative in-memory state — so the panel always sees the correct lists
    even when coordinator.data is stale or None (e.g. during startup or after a
    failed poll cycle).  Runtime values (surplus, production) come from coordinator.data.
    """
    data = coordinator.data or {}
    cfg = coordinator.get_config()
    return {
        "integration_version": INTEGRATION_VERSION,
        "running": cfg["running"],
        "base_load_entity": cfg["base_load_entity"],
        "base_load_name": cfg["base_load_name"],
        "base_load_current_w": data.get("base_load", 0),
        "base_load": data.get("base_load", 0),
        "total_production": data.get("total_production", 0),
        "surplus": data.get("surplus", 0),
        "remaining_surplus": data.get("remaining_surplus", 0),
        "scan_interval_seconds": data.get("scan_interval_seconds", 0),
        "producers": cfg["producers"],
        "producer_states": data.get("producer_states", {}),
        "consumers": cfg["consumers"],
        "consumer_states": data.get("consumer_states", {}),
    }


# ── panel registration ─────────────────────────────────────────────────────

async def _register_panel(hass: HomeAssistant) -> None:
    """Register the Power Manager sidebar panel (idempotent).

    Serves panel/power-manager-panel.js as a static file and registers it
    as a custom panel in the HA sidebar.  The guard flag prevents duplicate
    registration on config-entry reload.
    """
    if hass.data[DOMAIN].get("panel_registered"):
        return

    panel_dir = Path(__file__).parent / "panel"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_STATIC_URL,
                str(panel_dir),
                cache_headers=False,
            )
        ]
    )

    await async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name="power-manager-panel",
        sidebar_title="Power Manager",
        sidebar_icon="mdi:flash",
        module_url=f"{PANEL_STATIC_URL}/{PANEL_JS_NAME}?v={INTEGRATION_VERSION}",
        js_url=f"{PANEL_STATIC_URL}/{PANEL_JS_NAME}?v={INTEGRATION_VERSION}",
        embed_iframe=False,
        trust_external=False,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    hass.data[DOMAIN]["panel_registered"] = True


# ── WebSocket API ──────────────────────────────────────────────────────────

async def _register_ws(hass: HomeAssistant) -> None:
    """Register all WebSocket command handlers (idempotent).

    Handlers are defined as inner functions so they capture the outer hass
    reference via closure while still being passed to
    websocket_api.async_register_command as named callables.
    """
    if hass.data[DOMAIN].get("ws_registered"):
        return

    @websocket_api.websocket_command({"type": "power_manager/get_config"})
    @websocket_api.async_response
    async def ws_get_config(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Return a full config snapshot, triggering a coordinator refresh first."""
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        try:
            await coordinator.async_refresh()
        except Exception:
            # Refresh failed (e.g. entity unavailable). Return config from memory
            # so the panel is never left with a WS error instead of a result.
            pass
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/set_base",
            "base_load_entity": str,
            vol.Optional("base_load_name"): str,
        }
    )
    @websocket_api.async_response
    async def ws_set_base(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Update the base-load sensor entity ID and optional display name."""
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_update_base_load_entity(
            msg["base_load_entity"],
            name=msg.get("base_load_name"),
        )
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/add_producer",
            "name": str,
            "entity_id": str,
        }
    )
    @websocket_api.async_response
    async def ws_add_producer(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Add a new producer power sensor."""
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
            vol.Optional("new_name"): str,
        }
    )
    @websocket_api.async_response
    async def ws_update_producer(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Update a producer's entity ID and optionally rename it."""
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_update_producer(msg["name"], msg["entity_id"], msg.get("new_name"))
        connection.send_result(msg["id"], _config_payload(coordinator))

    @websocket_api.websocket_command(
        {
            "type": "power_manager/remove_producer",
            "name": str,
        }
    )
    @websocket_api.async_response
    async def ws_remove_producer(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Remove a producer by name."""
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
            "priority": vol.Coerce(int),
            "expected_power": vol.Coerce(float),
            "min_run_minutes": vol.Coerce(float),
        }
    )
    @websocket_api.async_response
    async def ws_add_consumer(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Add a new consumer load."""
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
            vol.Optional("new_name"): str,
            vol.Optional("switch_entity"): str,
            vol.Optional("power_entity"): str,
            vol.Optional("priority"): vol.Coerce(int),
            vol.Optional("expected_power"): vol.Coerce(float),
            vol.Optional("min_run_minutes"): vol.Coerce(float),
            vol.Optional("mode"): str,
        }
    )
    @websocket_api.async_response
    async def ws_update_consumer(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Update one or more fields of an existing consumer."""
        coordinator = _coordinator(hass)
        if not coordinator:
            connection.send_error(msg["id"], "not_loaded", "Power Manager not loaded")
            return
        await coordinator.async_update_consumer(
            name=msg["name"],
            new_name=msg.get("new_name"),
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
    async def ws_remove_consumer(
        hass: HomeAssistant,
        connection: websocket_api.ActiveConnection,
        msg: dict[str, Any],
    ) -> None:
        """Remove a consumer by name."""
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


# ── HA integration lifecycle ───────────────────────────────────────────────

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Global setup: register HA services, WebSocket API, and the sidebar panel.

    Called once by HA when the integration is first loaded.  Config-entry
    specific setup (coordinator, entity platforms) happens in async_setup_entry.
    """
    hass.data.setdefault(DOMAIN, {})

    # ── service handlers ───────────────────────────────────────────────────

    async def _set_running(call: ServiceCall) -> None:
        """Start or stop the control loop."""
        coordinator = _coordinator(hass)
        if coordinator:
            coordinator.running = call.data["running"]
            await coordinator.async_save_state()
            await coordinator.async_request_refresh()

    async def _set_consumer_mode(call: ServiceCall) -> None:
        """Set a consumer's operating mode."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_set_consumer_mode(call.data["consumer"], call.data["mode"])

    async def _add_producer(call: ServiceCall) -> None:
        """Add a producer via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_add_producer(call.data["name"], call.data["entity_id"])

    async def _remove_producer(call: ServiceCall) -> None:
        """Remove a producer via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_remove_producer(call.data["name"])

    async def _update_producer(call: ServiceCall) -> None:
        """Update a producer via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_update_producer(
                name=call.data["name"],
                entity_id=call.data["entity_id"],
            )

    async def _add_consumer(call: ServiceCall) -> None:
        """Add a consumer via HA service call."""
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

    async def _update_consumer(call: ServiceCall) -> None:
        """Update consumer fields via HA service call."""
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

    async def _remove_consumer(call: ServiceCall) -> None:
        """Remove a consumer via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_remove_consumer(call.data["name"])

    async def _get_consumers(call: ServiceCall) -> None:
        """Display configured consumers in a persistent HA notification."""
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

    async def _get_producers(call: ServiceCall) -> None:
        """Display configured producers in a persistent HA notification."""
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

    async def _get_config(call: ServiceCall) -> None:
        """Display the full configuration in a persistent HA notification."""
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

    async def _get_version(call: ServiceCall) -> None:
        """Display the loaded integration version in a persistent HA notification."""
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

    async def _get_base_load_entity(call: ServiceCall) -> None:
        """Display the current base-load sensor entity in a persistent notification."""
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

    async def _update_base_load_entity(call: ServiceCall) -> None:
        """Update the base-load sensor entity via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_update_base_load_entity(call.data["entity_id"])

    async def _clear_producers(call: ServiceCall) -> None:
        """Remove all producers via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_clear_producers()

    async def _clear_consumers(call: ServiceCall) -> None:
        """Remove all consumers via HA service call."""
        coordinator = _coordinator(hass)
        if coordinator:
            await coordinator.async_clear_consumers()

    # ── service registration ───────────────────────────────────────────────

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
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_PRODUCERS, _clear_producers, schema=vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_CONSUMERS, _clear_consumers, schema=vol.Schema({}))

    await _register_panel(hass)
    await _register_ws(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Power Manager config entry.

    Creates and initialises the coordinator, performs the first data refresh,
    then forwards setup to each entity platform defined in PLATFORMS.
    """
    coordinator = PowerManagerCoordinator(hass, entry)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN]["coordinator"] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Power Manager config entry.

    Saves coordinator state to storage before teardown so no configuration
    is lost during an integration reload or HA shutdown.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        await coordinator.async_save_state()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a config entry to the current schema version.

    Version history:
        1 → 2  No data migration needed; version bump only.
    """
    if entry.version > 2:
        return False

    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2)

    return True
