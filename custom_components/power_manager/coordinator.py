from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_BASE_LOAD_ENTITY,
    CONF_CONSUMERS,
    CONF_PRODUCERS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_AUTO,
    MODE_FORCE_OFF,
    MODE_FORCE_ON,
)


@dataclass
class ConsumerRuntime:
    mode: str = MODE_AUTO
    on_until_ts: float = 0.0


class PowerManagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        merged = {**entry.data, **entry.options}
        self._base_load_entity = merged.get(CONF_BASE_LOAD_ENTITY)
        self._producers = json.loads(merged.get(CONF_PRODUCERS, "[]"))
        self._consumers = json.loads(merged.get(CONF_CONSUMERS, "[]"))
        self._runtime: dict[str, ConsumerRuntime] = {
            c["name"]: ConsumerRuntime() for c in self._consumers
        }
        self.running = True

        interval = int(merged.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def async_set_consumer_mode(self, consumer_name: str, mode: str):
        if consumer_name not in self._runtime:
            raise UpdateFailed(f"unknown consumer: {consumer_name}")
        self._runtime[consumer_name].mode = mode
        await self.async_request_refresh()

    def _state_float(self, entity_id: str) -> float:
        st = self.hass.states.get(entity_id)
        if not st:
            return 0.0
        try:
            return float(st.state)
        except Exception:
            return 0.0

    async def _set_switch(self, entity_id: str, on: bool):
        domain = "homeassistant"
        service = "turn_on" if on else "turn_off"
        await self.hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            total_production = sum(
                self._state_float(p["entity_id"]) for p in self._producers
            )
            base_load = self._state_float(self._base_load_entity)
            surplus = total_production - base_load

            consumer_states: dict[str, dict[str, Any]] = {}

            if self.running:
                sorted_consumers = sorted(
                    self._consumers,
                    key=lambda c: int(c.get("priority", 999)),
                )

                now = self.hass.loop.time()
                for c in sorted_consumers:
                    name = c["name"]
                    switch_entity = c["switch_entity"]
                    expected = float(c.get("expected_power", 0))
                    min_run_minutes = float(c.get("min_run_minutes", 0))
                    runtime = self._runtime.setdefault(name, ConsumerRuntime())

                    current_power = self._state_float(c.get("power_entity", ""))
                    is_on = (self.hass.states.get(switch_entity) or {}).state == "on"

                    should_on = False
                    if runtime.mode == MODE_FORCE_ON:
                        should_on = True
                    elif runtime.mode == MODE_FORCE_OFF:
                        should_on = False
                    else:
                        if surplus >= expected:
                            should_on = True
                        elif runtime.on_until_ts > now:
                            should_on = True

                    if should_on:
                        await self._set_switch(switch_entity, True)
                        runtime.on_until_ts = max(runtime.on_until_ts, now + min_run_minutes * 60)
                        if runtime.mode == MODE_AUTO:
                            surplus -= expected
                    else:
                        if runtime.on_until_ts <= now:
                            await self._set_switch(switch_entity, False)

                    consumer_states[name] = {
                        "power": current_power,
                        "mode": runtime.mode,
                        "on_until": runtime.on_until_ts,
                    }

            return {
                "running": self.running,
                "total_production": total_production,
                "base_load": base_load,
                "surplus": surplus,
                "consumer_states": consumer_states,
                "producers": self._producers,
                "consumers": self._consumers,
                "unit": UnitOfPower.WATT,
                "device_class": SensorDeviceClass.POWER,
            }
        except Exception as err:
            raise UpdateFailed(str(err)) from err
