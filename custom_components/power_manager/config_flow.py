from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_BASE_LOAD_ENTITY,
    CONF_CONSUMERS,
    CONF_PRODUCERS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    VALID_MODES,
)


def _loads_list(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


class PowerManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                _loads_list(user_input[CONF_PRODUCERS])
                _loads_list(user_input[CONF_CONSUMERS])
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Power Manager", data=user_input)
            except Exception:
                errors["base"] = "invalid_json"

        schema = vol.Schema(
            {
                vol.Optional(CONF_BASE_LOAD_ENTITY, default="sensor.house_total_power"): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
                vol.Required(
                    CONF_PRODUCERS,
                    default='[{"name":"PV","entity_id":"sensor.pv_power"}]',
                ): str,
                vol.Required(
                    CONF_CONSUMERS,
                    default='[{"name":"Boiler","switch_entity":"switch.boiler","power_entity":"sensor.boiler_power","priority":1,"expected_power":1200,"min_run_minutes":10}]',
                ): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PowerManagerOptionsFlow(config_entry)


class PowerManagerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        self._config_entry = config_entry
        current = {**config_entry.data, **config_entry.options}
        self._base_load_entity: str = current.get(CONF_BASE_LOAD_ENTITY, "sensor.house_total_power")
        self._scan_interval: int = int(current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        self._producers: list[dict[str, Any]] = _loads_list(current.get(CONF_PRODUCERS, "[]"))
        self._consumers: list[dict[str, Any]] = _loads_list(current.get(CONF_CONSUMERS, "[]"))

    def _save_payload(self) -> dict[str, Any]:
        return {
            CONF_BASE_LOAD_ENTITY: self._base_load_entity,
            CONF_SCAN_INTERVAL: self._scan_interval,
            CONF_PRODUCERS: json.dumps(self._producers),
            CONF_CONSUMERS: json.dumps(self._consumers),
        }

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            action = user_input["action"]
            if action == "finish":
                return self.async_create_entry(title="", data=self._save_payload())
            if action == "base":
                return await self.async_step_base()
            if action == "add_producer":
                return await self.async_step_add_producer()
            if action == "update_producer":
                return await self.async_step_update_producer_select()
            if action == "remove_producer":
                return await self.async_step_remove_producer()
            if action == "add_consumer":
                return await self.async_step_add_consumer()
            if action == "update_consumer":
                return await self.async_step_update_consumer_select()
            if action == "remove_consumer":
                return await self.async_step_remove_consumer()

        schema = vol.Schema(
            {
                vol.Required("action", default="base"): vol.In(
                    {
                        "base": "Update base settings",
                        "add_producer": "Add producer",
                        "update_producer": "Update producer",
                        "remove_producer": "Remove producer",
                        "add_consumer": "Add consumer",
                        "update_consumer": "Update consumer",
                        "remove_consumer": "Remove consumer",
                        "finish": "Save and close",
                    }
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_base(self, user_input=None):
        if user_input is not None:
            self._base_load_entity = user_input[CONF_BASE_LOAD_ENTITY]
            self._scan_interval = user_input[CONF_SCAN_INTERVAL]
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_LOAD_ENTITY, default=self._base_load_entity): str,
                vol.Required(CONF_SCAN_INTERVAL, default=self._scan_interval): vol.All(
                    int, vol.Range(min=2, max=300)
                ),
            }
        )
        return self.async_show_form(step_id="base", data_schema=schema)

    async def async_step_add_producer(self, user_input=None):
        errors = {}
        if user_input is not None:
            name = user_input["name"]
            if any(p.get("name") == name for p in self._producers):
                errors["base"] = "name_exists"
            else:
                self._producers.append(
                    {"name": name, "entity_id": user_input["entity_id"]}
                )
                return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required("name"): str,
                vol.Required("entity_id"): str,
            }
        )
        return self.async_show_form(step_id="add_producer", data_schema=schema, errors=errors)

    async def async_step_update_producer_select(self, user_input=None):
        if not self._producers:
            return await self.async_step_init()

        if user_input is not None:
            self._selected_producer = user_input["name"]
            return await self.async_step_update_producer_edit()

        names = {p["name"]: p["name"] for p in self._producers if p.get("name")}
        schema = vol.Schema({vol.Required("name"): vol.In(names)})
        return self.async_show_form(step_id="update_producer_select", data_schema=schema)

    async def async_step_update_producer_edit(self, user_input=None):
        idx = next(
            (i for i, p in enumerate(self._producers) if p.get("name") == self._selected_producer),
            -1,
        )
        if idx < 0:
            return await self.async_step_init()

        current = self._producers[idx]
        if user_input is not None:
            self._producers[idx]["entity_id"] = user_input["entity_id"]
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required("entity_id", default=current.get("entity_id", "")): str,
            }
        )
        return self.async_show_form(step_id="update_producer_edit", data_schema=schema)

    async def async_step_remove_producer(self, user_input=None):
        if not self._producers:
            return await self.async_step_init()

        if user_input is not None:
            name = user_input["name"]
            self._producers = [p for p in self._producers if p.get("name") != name]
            return await self.async_step_init()

        names = {p["name"]: p["name"] for p in self._producers if p.get("name")}
        schema = vol.Schema({vol.Required("name"): vol.In(names)})
        return self.async_show_form(step_id="remove_producer", data_schema=schema)

    async def async_step_add_consumer(self, user_input=None):
        errors = {}
        if user_input is not None:
            name = user_input["name"]
            if any(c.get("name") == name for c in self._consumers):
                errors["base"] = "name_exists"
            else:
                self._consumers.append(
                    {
                        "name": name,
                        "switch_entity": user_input["switch_entity"],
                        "power_entity": user_input["power_entity"],
                        "priority": user_input["priority"],
                        "expected_power": user_input["expected_power"],
                        "min_run_minutes": user_input["min_run_minutes"],
                    }
                )
                return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required("name"): str,
                vol.Required("switch_entity"): str,
                vol.Required("power_entity"): str,
                vol.Required("priority", default=1): vol.All(int, vol.Range(min=1, max=999)),
                vol.Required("expected_power", default=1000): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required("min_run_minutes", default=10): vol.All(vol.Coerce(float), vol.Range(min=0)),
            }
        )
        return self.async_show_form(step_id="add_consumer", data_schema=schema, errors=errors)

    async def async_step_update_consumer_select(self, user_input=None):
        if not self._consumers:
            return await self.async_step_init()

        if user_input is not None:
            self._selected_consumer = user_input["name"]
            return await self.async_step_update_consumer_edit()

        names = {c["name"]: c["name"] for c in self._consumers if c.get("name")}
        schema = vol.Schema({vol.Required("name"): vol.In(names)})
        return self.async_show_form(step_id="update_consumer_select", data_schema=schema)

    async def async_step_update_consumer_edit(self, user_input=None):
        idx = next(
            (i for i, c in enumerate(self._consumers) if c.get("name") == self._selected_consumer),
            -1,
        )
        if idx < 0:
            return await self.async_step_init()

        current = self._consumers[idx]
        if user_input is not None:
            current["switch_entity"] = user_input["switch_entity"]
            current["power_entity"] = user_input["power_entity"]
            current["priority"] = user_input["priority"]
            current["expected_power"] = user_input["expected_power"]
            current["min_run_minutes"] = user_input["min_run_minutes"]
            current["mode"] = user_input["mode"]
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required("switch_entity", default=current.get("switch_entity", "")): str,
                vol.Required("power_entity", default=current.get("power_entity", "")): str,
                vol.Required("priority", default=current.get("priority", 1)): vol.All(int, vol.Range(min=1, max=999)),
                vol.Required("expected_power", default=current.get("expected_power", 1000)): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required("min_run_minutes", default=current.get("min_run_minutes", 10)): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required("mode", default=current.get("mode", "auto")): vol.In({m: m for m in sorted(VALID_MODES)}),
            }
        )
        return self.async_show_form(step_id="update_consumer_edit", data_schema=schema)

    async def async_step_remove_consumer(self, user_input=None):
        if not self._consumers:
            return await self.async_step_init()

        if user_input is not None:
            name = user_input["name"]
            self._consumers = [c for c in self._consumers if c.get("name") != name]
            return await self.async_step_init()

        names = {c["name"]: c["name"] for c in self._consumers if c.get("name")}
        schema = vol.Schema({vol.Required("name"): vol.In(names)})
        return self.async_show_form(step_id="remove_consumer", data_schema=schema)
