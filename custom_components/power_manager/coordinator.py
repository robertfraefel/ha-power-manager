from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
    VALID_MODES,
)

STORAGE_VERSION = 1


@dataclass
class ConsumerRuntime:
    mode: str = MODE_AUTO
    on_until_ts: float = 0.0


class PowerManagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        merged = {**entry.data, **entry.options}
        self._base_load_entity = merged.get(CONF_BASE_LOAD_ENTITY, "sensor.house_total_power")
        self._producers: list[dict[str, Any]] = json.loads(merged.get(CONF_PRODUCERS, "[]"))
        self._consumers: list[dict[str, Any]] = json.loads(merged.get(CONF_CONSUMERS, "[]"))
        self._runtime: dict[str, ConsumerRuntime] = {
            c["name"]: ConsumerRuntime() for c in self._consumers
        }
        self.running = True

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}"
        )

        interval = int(merged.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def async_initialize(self) -> None:
        stored = await self._store.async_load()
        if not stored:
            return

        self._base_load_entity = stored.get("base_load_entity", self._base_load_entity)
        self._producers = stored.get("producers", self._producers)
        self._consumers = stored.get("consumers", self._consumers)
        self.running = bool(stored.get("running", self.running))

        runtime_modes = stored.get("runtime_modes", {})
        current_runtime: dict[str, ConsumerRuntime] = {}
        for c in self._consumers:
            name = c["name"]
            mode = runtime_modes.get(name, MODE_AUTO)
            if mode not in VALID_MODES:
                mode = MODE_AUTO
            current_runtime[name] = ConsumerRuntime(mode=mode)
        self._runtime = current_runtime

    def _state_float(self, entity_id: str) -> float:
        st = self.hass.states.get(entity_id)
        if not st:
            return 0.0
        try:
            return float(st.state)
        except Exception:
            return 0.0

    async def _set_switch(self, entity_id: str, on: bool) -> None:
        service = "turn_on" if on else "turn_off"
        await self.hass.services.async_call(
            "homeassistant",
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def _async_save(self) -> None:
        payload = {
            "base_load_entity": self._base_load_entity,
            "producers": self._producers,
            "consumers": self._consumers,
            "running": self.running,
            "runtime_modes": {name: rt.mode for name, rt in self._runtime.items()},
        }
        await self._store.async_save(payload)

    async def async_save_state(self) -> None:
        await self._async_save()

    def _sync_runtime(self) -> None:
        valid_names = {c["name"] for c in self._consumers}
        self._runtime = {
            name: rt for name, rt in self._runtime.items() if name in valid_names
        }
        for name in valid_names:
            self._runtime.setdefault(name, ConsumerRuntime())

    @staticmethod
    def _find_idx_by_name(items: list[dict[str, Any]], name: str) -> int:
        for idx, item in enumerate(items):
            if item.get("name") == name:
                return idx
        return -1

    async def async_set_consumer_mode(self, consumer_name: str, mode: str) -> None:
        if mode not in VALID_MODES:
            raise UpdateFailed(f"invalid mode: {mode}")
        if consumer_name not in self._runtime:
            raise UpdateFailed(f"unknown consumer: {consumer_name}")
        self._runtime[consumer_name].mode = mode
        await self._async_save()
        await self.async_request_refresh()

    async def async_add_producer(self, name: str, entity_id: str) -> None:
        if self._find_idx_by_name(self._producers, name) >= 0:
            raise UpdateFailed(f"producer already exists: {name}")
        self._producers.append({"name": name, "entity_id": entity_id})
        await self._async_save()
        await self.async_request_refresh()

    async def async_remove_producer(self, name: str) -> None:
        idx = self._find_idx_by_name(self._producers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown producer: {name}")
        self._producers.pop(idx)
        await self._async_save()
        await self.async_request_refresh()

    async def async_add_consumer(
        self,
        name: str,
        switch_entity: str,
        power_entity: str,
        priority: int,
        expected_power: float,
        min_run_minutes: float,
    ) -> None:
        if self._find_idx_by_name(self._consumers, name) >= 0:
            raise UpdateFailed(f"consumer already exists: {name}")
        self._consumers.append(
            {
                "name": name,
                "switch_entity": switch_entity,
                "power_entity": power_entity,
                "priority": int(priority),
                "expected_power": float(expected_power),
                "min_run_minutes": float(min_run_minutes),
            }
        )
        self._sync_runtime()
        await self._async_save()
        await self.async_request_refresh()

    async def async_update_consumer(
        self,
        name: str,
        switch_entity: str | None = None,
        power_entity: str | None = None,
        priority: int | None = None,
        expected_power: float | None = None,
        min_run_minutes: float | None = None,
        mode: str | None = None,
    ) -> None:
        idx = self._find_idx_by_name(self._consumers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown consumer: {name}")

        c = self._consumers[idx]
        if switch_entity is not None:
            c["switch_entity"] = switch_entity
        if power_entity is not None:
            c["power_entity"] = power_entity
        if priority is not None:
            c["priority"] = int(priority)
        if expected_power is not None:
            c["expected_power"] = float(expected_power)
        if min_run_minutes is not None:
            c["min_run_minutes"] = float(min_run_minutes)

        if mode is not None:
            if mode not in VALID_MODES:
                raise UpdateFailed(f"invalid mode: {mode}")
            self._runtime.setdefault(name, ConsumerRuntime()).mode = mode

        self._sync_runtime()
        await self._async_save()
        await self.async_request_refresh()

    async def async_remove_consumer(self, name: str) -> None:
        idx = self._find_idx_by_name(self._consumers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown consumer: {name}")
        self._consumers.pop(idx)
        self._sync_runtime()
        await self._async_save()
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            total_production = sum(
                self._state_float(p["entity_id"]) for p in self._producers
            )
            base_load = self._state_float(self._base_load_entity)
            surplus = total_production - base_load

            consumer_states: dict[str, dict[str, Any]] = {}

            self._sync_runtime()
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
                        runtime.on_until_ts = max(
                            runtime.on_until_ts, now + min_run_minutes * 60
                        )
                        if runtime.mode == MODE_AUTO:
                            surplus -= expected
                    elif runtime.on_until_ts <= now:
                        await self._set_switch(switch_entity, False)

                    consumer_states[name] = {
                        "power": current_power,
                        "mode": runtime.mode,
                        "on_until": runtime.on_until_ts,
                        "switch_entity": switch_entity,
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
