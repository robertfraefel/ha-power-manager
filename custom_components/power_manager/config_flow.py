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
)


def _merged(entry: config_entries.ConfigEntry) -> dict[str, Any]:
    return {**entry.data, **entry.options}


class PowerManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._base_load_entity = "sensor.house_total_power"
        self._scan_interval = DEFAULT_SCAN_INTERVAL
        self._producer_count = 1
        self._consumer_count = 1
        self._producers: list[dict[str, Any]] = []
        self._consumers: list[dict[str, Any]] = []

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._base_load_entity = user_input[CONF_BASE_LOAD_ENTITY]
            self._scan_interval = user_input[CONF_SCAN_INTERVAL]
            self._producer_count = user_input["producer_count"]
            self._consumer_count = user_input["consumer_count"]
            self._producers = []
            self._consumers = []
            return await self.async_step_producer()

        schema = vol.Schema(
            {
                vol.Optional(CONF_BASE_LOAD_ENTITY, default="sensor.house_total_power"): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    int, vol.Range(min=2, max=300)
                ),
                vol.Required("producer_count", default=1): vol.All(
                    int, vol.Range(min=1, max=20)
                ),
                vol.Required("consumer_count", default=1): vol.All(
                    int, vol.Range(min=1, max=40)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_producer(self, user_input=None):
        idx = len(self._producers) + 1
        if user_input is not None:
            self._producers.append(
                {
                    "name": user_input["name"],
                    "entity_id": user_input["entity_id"],
                }
            )
            if len(self._producers) < self._producer_count:
                return await self.async_step_producer()
            return await self.async_step_consumer()

        schema = vol.Schema(
            {
                vol.Required("name", default=f"Producer {idx}"): str,
                vol.Required("entity_id", default="sensor.pv_power"): str,
            }
        )
        return self.async_show_form(
            step_id="producer",
            data_schema=schema,
            description_placeholders={"index": str(idx), "count": str(self._producer_count)},
        )

    async def async_step_consumer(self, user_input=None):
        idx = len(self._consumers) + 1
        if user_input is not None:
            self._consumers.append(
                {
                    "name": user_input["name"],
                    "switch_entity": user_input["switch_entity"],
                    "power_entity": user_input["power_entity"],
                    "priority": user_input["priority"],
                    "expected_power": user_input["expected_power"],
                    "min_run_minutes": user_input["min_run_minutes"],
                }
            )
            if len(self._consumers) < self._consumer_count:
                return await self.async_step_consumer()

            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            payload = {
                CONF_BASE_LOAD_ENTITY: self._base_load_entity,
                CONF_SCAN_INTERVAL: self._scan_interval,
                CONF_PRODUCERS: json.dumps(self._producers),
                CONF_CONSUMERS: json.dumps(self._consumers),
            }
            return self.async_create_entry(title="Power Manager", data=payload)

        schema = vol.Schema(
            {
                vol.Required("name", default=f"Consumer {idx}"): str,
                vol.Required("switch_entity", default="switch.boiler"): str,
                vol.Required("power_entity", default="sensor.boiler_power"): str,
                vol.Required("priority", default=idx): vol.All(int, vol.Range(min=1, max=999)),
                vol.Required("expected_power", default=1000): vol.All(
                    vol.Coerce(float), vol.Range(min=0)
                ),
                vol.Required("min_run_minutes", default=10): vol.All(
                    vol.Coerce(float), vol.Range(min=0)
                ),
            }
        )
        return self.async_show_form(
            step_id="consumer",
            data_schema=schema,
            description_placeholders={"index": str(idx), "count": str(self._consumer_count)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PowerManagerOptionsFlow(config_entry)


class PowerManagerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry
        current = _merged(config_entry)
        self._base_load_entity = current.get(CONF_BASE_LOAD_ENTITY, "sensor.house_total_power")
        self._scan_interval = current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._producers = json.loads(current.get(CONF_PRODUCERS, "[]"))
        self._consumers = json.loads(current.get(CONF_CONSUMERS, "[]"))

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self._base_load_entity = user_input[CONF_BASE_LOAD_ENTITY]
            self._scan_interval = user_input[CONF_SCAN_INTERVAL]
            return await self.async_step_edit_entities()

        schema = vol.Schema(
            {
                vol.Optional(CONF_BASE_LOAD_ENTITY, default=self._base_load_entity): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=self._scan_interval): vol.All(
                    int, vol.Range(min=2, max=300)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_edit_entities(self, user_input=None):
        if user_input is not None:
            if user_input["action"] == "keep":
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_BASE_LOAD_ENTITY: self._base_load_entity,
                        CONF_SCAN_INTERVAL: self._scan_interval,
                        CONF_PRODUCERS: json.dumps(self._producers),
                        CONF_CONSUMERS: json.dumps(self._consumers),
                    },
                )
            if user_input["action"] == "reset":
                self._producers = []
                self._consumers = []
                return await self.async_step_add_counts()

        schema = vol.Schema(
            {
                vol.Required("action", default="keep"): vol.In({"keep": "Keep existing producer/consumer list", "reset": "Reset and re-enter list"}),
            }
        )
        return self.async_show_form(step_id="edit_entities", data_schema=schema)

    async def async_step_add_counts(self, user_input=None):
        if user_input is not None:
            self._producer_count = user_input["producer_count"]
            self._consumer_count = user_input["consumer_count"]
            return await self.async_step_add_producer()

        schema = vol.Schema(
            {
                vol.Required("producer_count", default=1): vol.All(int, vol.Range(min=1, max=20)),
                vol.Required("consumer_count", default=1): vol.All(int, vol.Range(min=1, max=40)),
            }
        )
        return self.async_show_form(step_id="add_counts", data_schema=schema)

    async def async_step_add_producer(self, user_input=None):
        idx = len(self._producers) + 1
        if user_input is not None:
            self._producers.append({"name": user_input["name"], "entity_id": user_input["entity_id"]})
            if len(self._producers) < self._producer_count:
                return await self.async_step_add_producer()
            return await self.async_step_add_consumer()

        schema = vol.Schema(
            {
                vol.Required("name", default=f"Producer {idx}"): str,
                vol.Required("entity_id", default="sensor.pv_power"): str,
            }
        )
        return self.async_show_form(step_id="add_producer", data_schema=schema)

    async def async_step_add_consumer(self, user_input=None):
        idx = len(self._consumers) + 1
        if user_input is not None:
            self._consumers.append(
                {
                    "name": user_input["name"],
                    "switch_entity": user_input["switch_entity"],
                    "power_entity": user_input["power_entity"],
                    "priority": user_input["priority"],
                    "expected_power": user_input["expected_power"],
                    "min_run_minutes": user_input["min_run_minutes"],
                }
            )
            if len(self._consumers) < self._consumer_count:
                return await self.async_step_add_consumer()

            return self.async_create_entry(
                title="",
                data={
                    CONF_BASE_LOAD_ENTITY: self._base_load_entity,
                    CONF_SCAN_INTERVAL: self._scan_interval,
                    CONF_PRODUCERS: json.dumps(self._producers),
                    CONF_CONSUMERS: json.dumps(self._consumers),
                },
            )

        schema = vol.Schema(
            {
                vol.Required("name", default=f"Consumer {idx}"): str,
                vol.Required("switch_entity", default="switch.boiler"): str,
                vol.Required("power_entity", default="sensor.boiler_power"): str,
                vol.Required("priority", default=idx): vol.All(int, vol.Range(min=1, max=999)),
                vol.Required("expected_power", default=1000): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required("min_run_minutes", default=10): vol.All(vol.Coerce(float), vol.Range(min=0)),
            }
        )
        return self.async_show_form(step_id="add_consumer", data_schema=schema)
