"""Unit tests for PowerManagerCoordinator business logic."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a coordinator without a real Home Assistant instance
# ---------------------------------------------------------------------------

def _make_state(value: str, unit: str = "W") -> MagicMock:
    """Create a mock HA state object."""
    st = MagicMock()
    st.state = value
    st.attributes = {"unit_of_measurement": unit}
    return st


def _make_hass(states: dict | None = None, now: float = 1000.0) -> MagicMock:
    """Return a minimal mock HomeAssistant."""
    hass = MagicMock()
    hass.loop.time.return_value = now
    hass.services.async_call = AsyncMock()
    state_map = states or {}
    hass.states.get = lambda entity_id: state_map.get(entity_id)
    return hass


def _make_coordinator(
    hass: MagicMock,
    producers: list | None = None,
    consumers: list | None = None,
    running: bool = True,
) -> "PowerManagerCoordinator":
    """Instantiate a coordinator with mocked HA plumbing."""
    # Lazy import so the module is importable without HA installed when tests
    # patch the HA base class.
    from custom_components.power_manager.coordinator import (
        ConsumerRuntime,
        PowerManagerCoordinator,
    )
    from custom_components.power_manager.const import MODE_AUTO

    # Patch DataUpdateCoordinator.__init__ so we don't need a real HA event loop.
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        entry = MagicMock()
        entry.data = {}
        entry.options = {}
        entry.entry_id = "test_entry"

        coord = PowerManagerCoordinator.__new__(PowerManagerCoordinator)

    # Manually populate what __init__ would have set.
    coord.hass = hass
    coord._base_load_entity = "sensor.base_load"
    coord._base_load_name = "Base load"
    coord._producers = list(producers or [])
    coord._consumers = list(consumers or [])
    coord._runtime = {c["name"]: ConsumerRuntime() for c in (consumers or [])}
    coord.running = running
    coord.update_interval = timedelta(seconds=10)
    coord._warmup_remaining = 0
    coord._last_turn_on_ts = 0.0
    coord._last_turn_on_cooldown = 300.0
    coord._store = MagicMock()
    coord._store.async_save = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    coord.logger = MagicMock()

    return coord


# ---------------------------------------------------------------------------
# _state_float tests
# ---------------------------------------------------------------------------

class TestStateFloat:
    def test_entity_not_found_returns_zero(self):
        from custom_components.power_manager.coordinator import PowerManagerCoordinator
        hass = _make_hass(states={})
        coord = _make_coordinator(hass)
        assert coord._state_float("sensor.missing") == 0.0

    def test_invalid_state_returns_zero(self):
        hass = _make_hass(states={"sensor.bad": _make_state("unavailable")})
        coord = _make_coordinator(hass)
        assert coord._state_float("sensor.bad") == 0.0

    def test_watts_passthrough(self):
        hass = _make_hass(states={"sensor.pv": _make_state("500", "W")})
        coord = _make_coordinator(hass)
        assert coord._state_float("sensor.pv") == 500.0

    def test_kw_normalised_to_watts(self):
        hass = _make_hass(states={"sensor.pv": _make_state("2.5", "kW")})
        coord = _make_coordinator(hass)
        assert coord._state_float("sensor.pv") == pytest.approx(2500.0)

    def test_mw_normalised_to_watts(self):
        hass = _make_hass(states={"sensor.grid": _make_state("0.001", "MW")})
        coord = _make_coordinator(hass)
        assert coord._state_float("sensor.grid") == pytest.approx(1000.0)

    def test_no_unit_passthrough(self):
        hass = _make_hass(states={"sensor.pv": _make_state("300", "")})
        coord = _make_coordinator(hass)
        assert coord._state_float("sensor.pv") == 300.0


# ---------------------------------------------------------------------------
# Surplus allocation tests (_async_update_data)
# ---------------------------------------------------------------------------

class TestSurplusAllocation:
    """Tests for the core control loop."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _consumer(
        self,
        name: str,
        priority: int = 1,
        expected_power: float = 500.0,
        min_run_minutes: float = 0.0,
        switch: str | None = None,
        power_entity: str = "",
    ) -> dict:
        return {
            "name": name,
            "switch_entity": switch or f"switch.{name.lower()}",
            "power_entity": power_entity,
            "priority": priority,
            "expected_power": expected_power,
            "min_run_minutes": min_run_minutes,
        }

    def test_consumer_turned_on_when_surplus_sufficient(self):
        states = {
            "sensor.base_load": _make_state("200", "W"),
            "sensor.pv": _make_state("1000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        result = self._run(coord._async_update_data())

        assert result["total_production"] == pytest.approx(1000.0)
        assert result["surplus"] == pytest.approx(800.0)
        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_consumer_turned_off_when_surplus_insufficient(self):
        states = {
            "sensor.base_load": _make_state("900", "W"),
            "sensor.pv": _make_state("1000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_priority_ordering_allocates_higher_priority_first(self):
        # 600W surplus — only enough for one 500W consumer.
        # Consumer A has priority 1 (higher), B has priority 2 (lower).
        states = {
            "sensor.base_load": _make_state("400", "W"),
            "sensor.pv": _make_state("1000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            self._consumer("A", priority=1, expected_power=500.0, switch="switch.a"),
            self._consumer("B", priority=2, expected_power=500.0, switch="switch.b"),
        ]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        self._run(coord._async_update_data())

        # Phase 2 may issue corrective turn_off calls for already-OFF consumers.
        # Filter to only turn_on calls to verify priority ordering.
        on_calls = [
            c for c in hass.services.async_call.await_args_list
            if c.args[1] == "turn_on"
        ]
        assert len(on_calls) == 1
        assert on_calls[0].args[2]["entity_id"] == "switch.a"

    def test_force_on_ignores_surplus(self):
        from custom_components.power_manager.const import MODE_FORCE_ON
        from custom_components.power_manager.coordinator import ConsumerRuntime

        states = {
            "sensor.base_load": _make_state("1000", "W"),
            "sensor.pv": _make_state("100", "W"),  # deeply negative surplus
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(mode=MODE_FORCE_ON)

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_force_off_ignores_surplus(self):
        from custom_components.power_manager.const import MODE_FORCE_OFF
        from custom_components.power_manager.coordinator import ConsumerRuntime

        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),  # plenty of surplus
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(mode=MODE_FORCE_OFF)

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_min_runtime_holds_consumer_on(self):
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        # Consumer was turned on, min runtime expires at now+300s (5 min)
        states = {
            "sensor.base_load": _make_state("900", "W"),
            "sensor.pv": _make_state("1000", "W"),  # only 100W surplus < 500W expected
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0, min_run_minutes=5.0)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        # Simulate that consumer was previously turned on with 5-minute lock
        coord._runtime["Boiler"] = ConsumerRuntime(on_until_ts=now + 300)

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_coordinator_not_running_skips_switch_calls(self):
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("2000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers, running=False)

        self._run(coord._async_update_data())

        hass.services.async_call.assert_not_awaited()

    def test_deactivated_skips_switch_and_surplus(self):
        from custom_components.power_manager.const import MODE_DEACTIVATED
        from custom_components.power_manager.coordinator import ConsumerRuntime

        # 1000W surplus — enough for a 500W consumer, but it is deactivated.
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("1000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(mode=MODE_DEACTIVATED)

        result = self._run(coord._async_update_data())

        # Switch must not be touched
        hass.services.async_call.assert_not_awaited()
        # Surplus must not be deducted
        assert result["remaining_surplus"] == pytest.approx(1000.0)

    def test_hysteresis_prevents_turn_on_below_threshold(self):
        # Surplus is exactly at expected_power (500W) but consumer is OFF.
        # With 5% hysteresis, turn-on threshold = 525W → should stay off.
        states = {
            "sensor.base_load": _make_state("500", "W"),
            "sensor.pv": _make_state("1000", "W"),  # surplus = 500W
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        # is_on=False (default) → turn-on threshold = 500 * 1.05 = 525W

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_hysteresis_keeps_on_above_lower_threshold(self):
        # Consumer is ON. Surplus drops to 480W (< 500W expected).
        # Lower threshold = 500 * 0.95 = 475W → 480W >= 475W → should stay on.
        from custom_components.power_manager.coordinator import ConsumerRuntime

        states = {
            "sensor.base_load": _make_state("520", "W"),
            "sensor.pv": _make_state("1000", "W"),  # surplus = 480W
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0)]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)  # was on last cycle

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_on", {"entity_id": "switch.boiler"}, blocking=True
        )

    def test_hysteresis_turns_off_below_lower_threshold(self):
        # Consumer is ON drawing 500W.  Surplus = -60W.
        # Stay-on threshold = -(500 * 0.05) = -25W.
        # -60W < -25W → deficit too large → should turn off.
        from custom_components.power_manager.coordinator import ConsumerRuntime

        states = {
            "sensor.base_load": _make_state("1060", "W"),
            "sensor.pv": _make_state("1000", "W"),  # surplus = -60W
            "sensor.boiler_power": _make_state("500", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [self._consumer("Boiler", expected_power=500.0,
                                    power_entity="sensor.boiler_power")]
        hass = _make_hass(states=states)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)

        self._run(coord._async_update_data())

        hass.services.async_call.assert_awaited_once_with(
            "homeassistant", "turn_off", {"entity_id": "switch.boiler"}, blocking=True
        )
