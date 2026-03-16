"""
Core coordinator for the Power Manager integration.

Layer: BACKEND

This module contains the central logic of the integration:

  PowerManagerCoordinator
      Inherits from DataUpdateCoordinator and is the single source of truth
      for all runtime state.  It polls HA entity states on a configurable
      interval, computes the renewable-energy surplus, and controls consumer
      switches accordingly.

  ConsumerRuntime
      Lightweight dataclass that tracks per-consumer transient state
      (current mode, minimum-runtime hold timestamp, last on/off decision).
      This data is NOT persisted — it is rebuilt from saved modes on startup.

Surplus algorithm (auto mode)
------------------------------
1. Sum all producer sensor values  → total_production
2. Read the base-load sensor       → base_load
3. surplus = total_production - base_load
4. Sort consumers by ascending priority.
5. For each consumer:
     - deactivated : skip entirely (switch not touched, surplus not deducted)
     - force_on    : turn on unconditionally
     - force_off   : turn off unconditionally
     - auto        : apply hysteresis threshold; turn on if surplus allows,
                     enforce min-runtime lock, deduct expected_power from
                     remaining surplus if on.
6. Priority preemption: if a higher-priority auto consumer cannot turn on,
   any lower-priority auto consumer that is ON (and not in min-run hold)
   is turned off so it frees its load for the next cycle.

Hysteresis (SURPLUS_HYSTERESIS_FACTOR = 5 %)
---------------------------------------------
Prevents rapid toggling when production fluctuates near the consumer's threshold.
Two different conditions apply depending on whether the consumer is already running:

  Consumer OFF → turn ON  when  remaining_surplus >= expected * 1.05
      Requires a clear positive margin before switching on.

  Consumer ON  → stay ON  when  remaining_surplus >= -(current_power * FACTOR)
      base_load already includes the consumer's draw.  A small negative margin
      (5 % of the actual measured draw) is tolerated before switching off, so
      brief sub-zero fluctuations in production do not trigger an immediate
      turn-off.  current_power is used — not expected — because the actual draw
      is what base_load reflects.

The asymmetric band provides hysteresis:
  turn on  when gross surplus > expected * 1.05 (5 % above expected draw)
  turn off when surplus deficit > current_power * 0.05 (5 % of actual draw)

Persistence
-----------
Configuration (producers, consumers, runtime modes, running flag) is saved to
HA's Store (.storage/power_manager_<entry_id>) after every mutation and on
integration unload.  Transient runtime values (is_on, on_until_ts) are NOT
saved — they reset on each restart.
"""
from __future__ import annotations

import json
import logging
import time
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
    MODE_DEACTIVATED,
    MODE_FORCE_OFF,
    MODE_FORCE_ON,
    VALID_MODES,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Hysteresis band applied to the auto-mode surplus threshold.
# OFF → ON requires surplus >= expected * (1 + FACTOR).
# ON  → OFF requires surplus <  expected * (1 - FACTOR).
# This prevents rapid toggling when solar output fluctuates near the threshold.
SURPLUS_HYSTERESIS_FACTOR = 0.05  # 5 %

# Number of update cycles to observe before making any switch decisions after
# startup.  Prevents consumers from being switched on immediately after a HA
# reboot when entity states may not yet reflect actual hardware conditions.
STARTUP_WARMUP_CYCLES = 2


@dataclass
class ConsumerRuntime:
    """Transient per-consumer state tracked between coordinator cycles.

    Attributes:
        mode:        Current operating mode (auto / force_on / force_off /
                     deactivated).  Persisted to storage.
        on_until_ts: Monotonic timestamp (hass.loop.time()) until which the
                     consumer must stay on to honour min_run_minutes.
                     Resets to 0 on restart.
        is_on:       Whether the coordinator decided ON in the last cycle.
                     Used for hysteresis; resets to False on restart.
    """

    mode: str = MODE_AUTO
    on_until_ts: float = 0.0
    is_on: bool = False  # tracks last-cycle decision for hysteresis


class PowerManagerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central coordinator that manages the surplus control loop.

    Responsibilities
    ----------------
    - Poll HA entity states on a timer (DataUpdateCoordinator).
    - Compute surplus and allocate it to consumers by priority.
    - Call homeassistant.turn_on / turn_off for each consumer switch.
    - Persist configuration to HA Store after every mutation.
    - Expose a data dict consumed by sensor / switch / select platforms and
      the WebSocket API.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise coordinator from a config entry.

        Merges entry.data and entry.options so both the initial setup values
        and any later options-flow overrides are respected.
        """
        merged = {**entry.data, **entry.options}
        self._base_load_entity: str = merged.get(CONF_BASE_LOAD_ENTITY, "sensor.house_total_power")
        self._base_load_name: str = "Base load"
        self._producers: list[dict[str, Any]] = json.loads(merged.get(CONF_PRODUCERS, "[]"))
        self._consumers: list[dict[str, Any]] = json.loads(merged.get(CONF_CONSUMERS, "[]"))
        self._runtime: dict[str, ConsumerRuntime] = {
            c["name"]: ConsumerRuntime() for c in self._consumers
        }
        self.running: bool = True
        # Counts down from STARTUP_WARMUP_CYCLES to 0; consumer switching is
        # deferred while > 0 so HA has time to settle entity states after reboot.
        self._warmup_remaining: int = STARTUP_WARMUP_CYCLES

        # Fixed storage key — not tied to entry_id so config survives
        # remove-and-re-add of the integration during updates.
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, DOMAIN
        )

        interval = int(merged.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def async_initialize(self) -> None:
        """Load persisted state from HA Store and rebuild runtime structures.

        Called once from async_setup_entry before the first coordinator
        refresh.  If no stored state exists the coordinator starts with the
        values from the config entry.
        """
        stored = await self._store.async_load()
        if not stored:
            _LOGGER.debug("No stored state found, using defaults")
            return

        self._base_load_entity = stored.get("base_load_entity", self._base_load_entity)
        self._base_load_name = stored.get("base_load_name", self._base_load_name)
        self._producers = stored.get("producers", self._producers)
        self._consumers = stored.get("consumers", self._consumers)
        self.running = bool(stored.get("running", self.running))
        if "scan_interval_seconds" in stored:
            self.update_interval = timedelta(seconds=int(stored["scan_interval_seconds"]))

        # Rebuild ConsumerRuntime objects from the saved mode map.
        runtime_modes = stored.get("runtime_modes", {})
        current_runtime: dict[str, ConsumerRuntime] = {}
        for c in self._consumers:
            name = c["name"]
            mode = runtime_modes.get(name, MODE_AUTO)
            if mode not in VALID_MODES:
                mode = MODE_AUTO
            current_runtime[name] = ConsumerRuntime(mode=mode)
        self._runtime = current_runtime

        _LOGGER.debug(
            "Restored state: base_load_entity=%s, %d producers, %d consumers, running=%s",
            self._base_load_entity,
            len(self._producers),
            len(self._consumers),
            self.running,
        )

    # ── private helpers ────────────────────────────────────────────────────

    def _state_float(self, entity_id: str) -> float:
        """Read an HA entity state as watts, handling kW/MW unit conversion.

        Returns 0.0 if the entity is unavailable or its state is not numeric.
        """
        st = self.hass.states.get(entity_id)
        if not st:
            _LOGGER.debug("Entity not found or not loaded: %s", entity_id)
            return 0.0
        try:
            value = float(st.state)
        except (ValueError, TypeError):
            _LOGGER.debug("Cannot convert state to float for %s: %r", entity_id, st.state)
            return 0.0

        unit = str(st.attributes.get("unit_of_measurement", "")).strip().lower()
        if unit in {"kw", "kilowatt", "kilowatts"}:
            return value * 1000.0
        if unit in {"mw", "megawatt", "megawatts"}:
            return value * 1_000_000.0

        return value  # assumed watts

    async def _set_switch(self, entity_id: str, on: bool) -> None:
        """Call homeassistant.turn_on or turn_off for a switch entity."""
        service = "turn_on" if on else "turn_off"
        await self.hass.services.async_call(
            "homeassistant",
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def _async_save(self) -> None:
        """Persist current configuration and runtime modes to HA Store."""
        payload = {
            "base_load_entity": self._base_load_entity,
            "base_load_name": self._base_load_name,
            "producers": self._producers,
            "consumers": self._consumers,
            "running": self.running,
            "scan_interval_seconds": int(self.update_interval.total_seconds()),
            # Only modes are persisted; transient fields (is_on, on_until_ts) are not.
            "runtime_modes": {name: rt.mode for name, rt in self._runtime.items()},
        }
        await self._store.async_save(payload)

    async def async_save_state(self) -> None:
        """Public alias for _async_save — called by async_unload_entry."""
        await self._async_save()

    def get_config(self) -> dict[str, Any]:
        """Return current configuration directly from coordinator memory.

        This is the authoritative source for static config (producers, consumers,
        base-load entity).  Prefer this over coordinator.data["producers"] etc.,
        which is a polled snapshot that may be stale or None during startup.
        """
        return {
            "base_load_entity": self._base_load_entity,
            "base_load_name": self._base_load_name,
            "producers": self._producers,
            "consumers": self._consumers,
            "running": self.running,
        }

    def _sync_runtime(self) -> None:
        """Keep _runtime in sync with _consumers after add/remove operations.

        - Removes runtime entries for consumers that no longer exist.
        - Adds a default ConsumerRuntime for newly added consumers.
        """
        valid_names = {c["name"] for c in self._consumers}
        self._runtime = {
            name: rt for name, rt in self._runtime.items() if name in valid_names
        }
        for name in valid_names:
            self._runtime.setdefault(name, ConsumerRuntime())

    @staticmethod
    def _find_idx_by_name(items: list[dict[str, Any]], name: str) -> int:
        """Return the index of the first item whose 'name' key matches, or -1."""
        for idx, item in enumerate(items):
            if item.get("name") == name:
                return idx
        return -1

    # ── public mutation API ────────────────────────────────────────────────

    async def async_set_consumer_mode(self, consumer_name: str, mode: str) -> None:
        """Set the operating mode of a consumer and trigger a refresh.

        Args:
            consumer_name: Name of the consumer to update.
            mode:          One of the VALID_MODES constants.

        Raises:
            UpdateFailed: If the mode string is invalid or the consumer is unknown.
        """
        if mode not in VALID_MODES:
            raise UpdateFailed(f"invalid mode: {mode}")
        if consumer_name not in self._runtime:
            raise UpdateFailed(f"unknown consumer: {consumer_name}")
        self._runtime[consumer_name].mode = mode
        _LOGGER.info("Consumer %r mode set to %s", consumer_name, mode)
        await self._async_save()
        await self.async_request_refresh()

    async def async_update_base_load_entity(self, entity_id: str, name: str | None = None) -> None:
        """Change the base-load sensor entity ID (and optionally its display name) and trigger a refresh."""
        self._base_load_entity = entity_id
        if name is not None:
            self._base_load_name = name
        _LOGGER.info("Base load entity updated to %s (name=%r)", entity_id, self._base_load_name)
        await self._async_save()
        await self.async_request_refresh()

    async def async_set_scan_interval(self, interval_seconds: int) -> None:
        """Change the coordinator polling interval and persist the new value.

        Raises:
            UpdateFailed: If interval_seconds is less than 5.
        """
        if interval_seconds < 5:
            raise UpdateFailed("scan interval must be at least 5 seconds")
        self.update_interval = timedelta(seconds=interval_seconds)
        _LOGGER.info("Scan interval updated to %d s", interval_seconds)
        await self._async_save()
        await self.async_request_refresh()

    async def async_add_producer(self, name: str, entity_id: str) -> None:
        """Add a new producer power sensor.

        Raises:
            UpdateFailed: If name is empty or a producer with this name already exists.
        """
        if not name.strip():
            raise UpdateFailed("producer name cannot be empty")
        if self._find_idx_by_name(self._producers, name) >= 0:
            raise UpdateFailed(f"producer already exists: {name}")
        self._producers.append({"name": name, "entity_id": entity_id})
        _LOGGER.info("Producer added: name=%r entity_id=%s", name, entity_id)
        await self._async_save()
        await self.async_request_refresh()

    async def async_remove_producer(self, name: str) -> None:
        """Remove a producer by name.

        Raises:
            UpdateFailed: If no producer with this name exists.
        """
        idx = self._find_idx_by_name(self._producers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown producer: {name}")
        self._producers.pop(idx)
        _LOGGER.info("Producer removed: %r", name)
        await self._async_save()
        await self.async_request_refresh()

    async def async_update_producer(
        self, name: str, entity_id: str, new_name: str | None = None
    ) -> None:
        """Update a producer's entity_id and optionally rename it.

        Args:
            name:      Current name of the producer to update.
            entity_id: New sensor entity ID.
            new_name:  If provided and different from name, rename the producer.

        Raises:
            UpdateFailed: If the producer is not found or new_name already exists.
        """
        idx = self._find_idx_by_name(self._producers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown producer: {name}")
        if new_name is not None and new_name != name:
            if self._find_idx_by_name(self._producers, new_name) >= 0:
                raise UpdateFailed(f"producer already exists: {new_name}")
            self._producers[idx]["name"] = new_name
        self._producers[idx]["entity_id"] = entity_id
        _LOGGER.info("Producer updated: name=%r entity_id=%s", new_name or name, entity_id)
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
        """Add a new consumer load.

        Args:
            name:            Unique display name.
            switch_entity:   HA switch entity ID to control.
            power_entity:    HA sensor entity ID measuring actual consumption.
            priority:        Lower number = higher priority (allocated first).
            expected_power:  Estimated power draw used for surplus budgeting (W).
            min_run_minutes: Minimum on-time per cycle to protect appliances (min).

        Raises:
            UpdateFailed: If a consumer with this name already exists or
                          the priority is already in use.
        """
        if not name.strip():
            raise UpdateFailed("consumer name cannot be empty")
        if self._find_idx_by_name(self._consumers, name) >= 0:
            raise UpdateFailed(f"consumer already exists: {name}")
        existing_priorities = {int(c.get("priority", 999)) for c in self._consumers}
        if int(priority) in existing_priorities:
            raise UpdateFailed(f"priority {priority} is already used by another consumer")
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
        _LOGGER.info(
            "Consumer added: name=%r switch=%s power=%s priority=%d expected=%gW min_run=%gmin",
            name, switch_entity, power_entity, priority, expected_power, min_run_minutes,
        )
        await self._async_save()
        await self.async_request_refresh()

    async def async_update_consumer(
        self,
        name: str,
        new_name: str | None = None,
        switch_entity: str | None = None,
        power_entity: str | None = None,
        priority: int | None = None,
        expected_power: float | None = None,
        min_run_minutes: float | None = None,
        mode: str | None = None,
    ) -> None:
        """Update one or more fields of an existing consumer.

        All parameters except name are optional; omitted ones are left unchanged.

        Args:
            name:            Current name of the consumer to update.
            new_name:        Rename the consumer if provided and different.
            switch_entity:   New switch entity ID.
            power_entity:    New power sensor entity ID.
            priority:        New priority value.
            expected_power:  New expected power draw (W).
            min_run_minutes: New minimum runtime (min).
            mode:            New operating mode.

        Raises:
            UpdateFailed: If consumer not found, new_name conflicts, or mode invalid.
        """
        idx = self._find_idx_by_name(self._consumers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown consumer: {name}")

        c = self._consumers[idx]

        if new_name is not None and new_name != name:
            if self._find_idx_by_name(self._consumers, new_name) >= 0:
                raise UpdateFailed(f"consumer already exists: {new_name}")
            c["name"] = new_name
            # Carry runtime state over to the new name.
            if name in self._runtime:
                self._runtime[new_name] = self._runtime.pop(name)

        if switch_entity is not None:
            c["switch_entity"] = switch_entity
        if power_entity is not None:
            c["power_entity"] = power_entity
        if priority is not None:
            existing_priorities = {
                int(other.get("priority", 999))
                for other in self._consumers
                if other.get("name") != name
            }
            if int(priority) in existing_priorities:
                raise UpdateFailed(f"priority {priority} is already used by another consumer")
            c["priority"] = int(priority)
        if expected_power is not None:
            c["expected_power"] = float(expected_power)
        if min_run_minutes is not None:
            c["min_run_minutes"] = float(min_run_minutes)

        if mode is not None:
            if mode not in VALID_MODES:
                raise UpdateFailed(f"invalid mode: {mode}")
            runtime_name = new_name if new_name is not None and new_name != name else name
            self._runtime.setdefault(runtime_name, ConsumerRuntime()).mode = mode

        self._sync_runtime()
        _LOGGER.info("Consumer updated: %r", new_name or name)
        await self._async_save()
        await self.async_request_refresh()

    async def async_remove_consumer(self, name: str) -> None:
        """Remove a consumer by name.

        Raises:
            UpdateFailed: If no consumer with this name exists.
        """
        idx = self._find_idx_by_name(self._consumers, name)
        if idx < 0:
            raise UpdateFailed(f"unknown consumer: {name}")
        self._consumers.pop(idx)
        self._sync_runtime()
        _LOGGER.info("Consumer removed: %r", name)
        await self._async_save()
        await self.async_request_refresh()

    async def async_clear_base_load(self) -> None:
        """Reset the base-load entity to empty."""
        self._base_load_entity = ""
        self._base_load_name = "Base load"
        _LOGGER.info("Base load entity cleared")
        await self._async_save()
        await self.async_request_refresh()

    async def async_clear_producers(self) -> None:
        """Remove all producers."""
        count = len(self._producers)
        self._producers.clear()
        _LOGGER.info("All producers cleared (%d removed)", count)
        await self._async_save()
        await self.async_request_refresh()

    async def async_clear_consumers(self) -> None:
        """Remove all consumers and their runtime state."""
        count = len(self._consumers)
        self._consumers.clear()
        self._runtime.clear()
        _LOGGER.info("All consumers cleared (%d removed)", count)
        await self._async_save()
        await self.async_request_refresh()

    # ── coordinator update cycle ───────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch sensor states, compute surplus, and control consumer switches.

        Called automatically by DataUpdateCoordinator on every scan interval.
        Returns a dict that becomes coordinator.data, consumed by all entity
        platforms and the WebSocket API.

        Returns:
            dict with keys: running, base_load_entity, scan_interval_seconds,
            total_production, base_load, surplus, remaining_surplus,
            producers, producer_states, consumers, consumer_states,
            unit, device_class.

        Raises:
            UpdateFailed: On any unexpected error during the update cycle.
        """
        try:
            # ── producers ──────────────────────────────────────────────────
            producer_states: dict[str, dict[str, Any]] = {}
            total_production = 0.0
            for p in self._producers:
                name = p.get("name", "unknown")
                entity_id = p.get("entity_id", "")
                current_power = self._state_float(entity_id)
                total_production += current_power
                producer_states[name] = {
                    "entity_id": entity_id,
                    "power": current_power,
                }

            # ── base load and surplus ──────────────────────────────────────
            base_load = self._state_float(self._base_load_entity)
            surplus = total_production - base_load
            remaining_surplus = surplus

            _LOGGER.debug(
                "Power update: total_production=%.1fW base_load=%.1fW surplus=%.1fW",
                total_production, base_load, surplus,
            )

            # ── consumers ─────────────────────────────────────────────────
            consumer_states: dict[str, dict[str, Any]] = {}

            self._sync_runtime()
            sorted_consumers = sorted(
                self._consumers,
                key=lambda c: int(c.get("priority", 999)),
            )

            now = self.hass.loop.time()
            unix_now = time.time()

            # Phase 1: compute decisions in ascending priority order so that
            # the remaining_surplus budget is deducted in the correct sequence.
            decisions: list[dict[str, Any]] = []
            for c in sorted_consumers:
                name = c["name"]
                expected = float(c.get("expected_power", 0))
                runtime = self._runtime.setdefault(name, ConsumerRuntime())
                current_power = self._state_float(c.get("power_entity", ""))
                currently_on = runtime.is_on

                should_on = False
                extend_timer = False
                skip_switch = False  # True for deactivated or paused
                reason = ""

                if not self.running:
                    skip_switch = True
                    reason = "stopped"
                elif runtime.mode == MODE_DEACTIVATED:
                    runtime.is_on = False
                    skip_switch = True
                    reason = "deactivated"
                    _LOGGER.debug("Consumer %r: deactivated – skipped", name)
                else:
                    reason = "off: insufficient surplus"

                    if runtime.mode == MODE_FORCE_ON:
                        should_on = True
                        extend_timer = not currently_on  # arm on fresh turn-on only
                        reason = "on: force_on"
                    elif runtime.mode == MODE_FORCE_OFF:
                        should_on = False
                        reason = "off: force_off"
                    else:
                        # Auto mode with hysteresis.
                        #
                        # Turn ON  requires surplus >= expected * 1.05 — a clear
                        # positive margin prevents toggling near the threshold.
                        #
                        # Stay ON  tolerates a small deficit of up to 5 % of the
                        # consumer's actual measured draw.  current_power is used
                        # (not expected) because base_load reflects the real draw.
                        # When current_power is 0 (consumer just turned on and not
                        # yet measured) the threshold falls back to 0.
                        threshold = (
                            -(current_power * SURPLUS_HYSTERESIS_FACTOR)
                            if currently_on
                            else expected * (1.0 + SURPLUS_HYSTERESIS_FACTOR)
                        )
                        if remaining_surplus >= threshold:
                            should_on = True
                            extend_timer = not currently_on  # arm on OFF→ON transition only
                            reason = f"on: surplus {remaining_surplus:.1f}W >= {threshold:.1f}W"
                        elif runtime.on_until_ts > now:
                            # Min-runtime protection: surplus dropped before timer elapsed.
                            should_on = True
                            extend_timer = False
                            reason = f"on: holding min runtime ({(runtime.on_until_ts - now):.0f}s left)"
                        else:
                            reason = f"off: surplus {remaining_surplus:.1f}W < {threshold:.1f}W"

                    _LOGGER.debug("Consumer %r: %s", name, reason)

                    # Deduct from budget for fresh auto turn-ons (priority order preserved).
                    if should_on and runtime.mode == MODE_AUTO and not currently_on:
                        remaining_surplus -= expected

                decisions.append({
                    "c": c,
                    "runtime": runtime,
                    "should_on": should_on,
                    "extend_timer": extend_timer,
                    "skip_switch": skip_switch,
                    "current_power": current_power,
                    "reason": reason,
                })

            # Phase 1.5: Priority preemption.
            # If a higher-priority (lower number) auto consumer cannot get
            # surplus, shed lower-priority auto consumers that are ON but
            # not in their min-run hold window, so they release their load
            # and let higher-priority consumers claim it on the next cycle.
            # Consumers in min-run hold are exempt — interrupting them would
            # defeat the purpose of the hold (e.g. a mid-cycle washing machine).
            if self.running:
                auto_off_min_priority = min(
                    (int(d["c"].get("priority", 999))
                     for d in decisions
                     if not d["should_on"]
                     and not d["skip_switch"]
                     and d["runtime"].mode == MODE_AUTO),
                    default=None,
                )
                if auto_off_min_priority is not None:
                    for d in decisions:
                        if (
                            d["should_on"]
                            and not d["skip_switch"]
                            and d["runtime"].mode == MODE_AUTO
                            and d["runtime"].on_until_ts <= now  # not in min-run hold
                            and int(d["c"].get("priority", 999)) > auto_off_min_priority
                        ):
                            d["should_on"] = False
                            d["reason"] = f"preempted (P{auto_off_min_priority} needs surplus)"
                            _LOGGER.debug(
                                "Consumer %r preempted: P%d needs surplus first",
                                d["c"]["name"], auto_off_min_priority,
                            )

            # Startup warmup: defer all switch commands for the first
            # STARTUP_WARMUP_CYCLES cycles so HA entity states can settle
            # after a reboot before the coordinator makes any decisions.
            warming_up = self._warmup_remaining > 0
            if warming_up:
                self._warmup_remaining -= 1
                _LOGGER.debug(
                    "Startup warmup: %d cycle(s) remaining — consumer switching deferred",
                    self._warmup_remaining,
                )

            # Phase 2: turn OFF in reverse priority order (lowest priority off first).
            # At most one *active* consumer is shed per cycle.  After shedding the
            # lowest-priority ON consumer the remaining ON-candidates are deferred:
            # their runtime.is_on is kept True so the next measurement cycle
            # evaluates them with the updated (higher) surplus before deciding
            # whether further shedding is needed.  This prevents unnecessarily
            # cycling high-priority consumers when shedding a lower one suffices.
            #
            # Consumers that are already OFF (runtime.is_on=False) are corrected
            # unconditionally but do NOT consume the one-shed-per-cycle slot —
            # otherwise a lower-priority consumer that is already off would block
            # shedding of a higher-priority consumer that is still running.
            if not warming_up:
                shed_done = False
                for d in reversed(decisions):
                    if d["skip_switch"] or d["should_on"]:
                        continue
                    if d["runtime"].on_until_ts <= now:
                        was_on = d["runtime"].is_on
                        if was_on and shed_done:
                            # Defer to next cycle — preserve ON state for hysteresis.
                            d["should_on"] = True
                            d["skip_switch"] = True
                            d["reason"] = "deferred (shedding in progress)"
                            _LOGGER.debug(
                                "Consumer %r OFF deferred: waiting for surplus to settle after shed",
                                d["c"]["name"],
                            )
                        else:
                            await self._set_switch(d["c"]["switch_entity"], False)
                            if was_on:
                                shed_done = True

            # Phase 3: turn ON in forward priority order (highest priority on first).
            if not warming_up:
                for d in decisions:
                    if d["skip_switch"] or not d["should_on"]:
                        continue
                    runtime = d["runtime"]
                    await self._set_switch(d["c"]["switch_entity"], True)
                    if d["extend_timer"]:
                        # Arm the min-runtime lock from the moment of turn-on.
                        # Only set once per turn-on (extend_timer is False while
                        # already running), so the timer counts down naturally.
                        runtime.on_until_ts = now + float(d["c"].get("min_run_minutes", 0)) * 60

            # Phase 4: update runtime state and build consumer_states.
            for d in decisions:
                c = d["c"]
                runtime = d["runtime"]
                if not d["skip_switch"]:
                    runtime.is_on = d["should_on"]
                remaining_secs = max(0.0, runtime.on_until_ts - now)
                consumer_states[c["name"]] = {
                    "power": d["current_power"],
                    "mode": runtime.mode,
                    "on_until": unix_now + remaining_secs,  # Unix time, comparable to Date.now()/1000
                    "switch_entity": c["switch_entity"],
                    "is_on": runtime.is_on,
                    "reason": d["reason"],
                }

            return {
                "running": self.running,
                "base_load_entity": self._base_load_entity,
                "base_load_name": self._base_load_name,
                "scan_interval_seconds": int(self.update_interval.total_seconds()),
                "total_production": total_production,
                "base_load": base_load,
                "surplus": surplus,
                "remaining_surplus": remaining_surplus,
                "consumer_states": consumer_states,
                "producers": self._producers,
                "producer_states": producer_states,
                "consumers": self._consumers,
                "unit": UnitOfPower.WATT,
                "device_class": SensorDeviceClass.POWER,
            }
        except Exception as err:
            raise UpdateFailed(str(err)) from err
