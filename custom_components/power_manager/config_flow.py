from __future__ import annotations

import json

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


class PowerManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                json.loads(user_input[CONF_PRODUCERS])
                json.loads(user_input[CONF_CONSUMERS])
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
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                json.loads(user_input[CONF_PRODUCERS])
                json.loads(user_input[CONF_CONSUMERS])
                return self.async_create_entry(title="", data=user_input)
            except Exception:
                errors["base"] = "invalid_json"

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_BASE_LOAD_ENTITY,
                    default=current.get(CONF_BASE_LOAD_ENTITY, "sensor.house_total_power"),
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): int,
                vol.Required(
                    CONF_PRODUCERS,
                    default=current.get(CONF_PRODUCERS, "[]"),
                ): str,
                vol.Required(
                    CONF_CONSUMERS,
                    default=current.get(CONF_CONSUMERS, "[]"),
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
