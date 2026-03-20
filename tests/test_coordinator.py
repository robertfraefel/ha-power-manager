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


class TestCooldown:
    """Tests for per-consumer cooldown logic."""

    @staticmethod
    def _consumer(name, priority=1, expected_power=500.0, cooldown_seconds=300, **kw):
        return {
            "name": name,
            "switch_entity": f"switch.{name.lower().replace(' ', '_')}",
            "power_entity": kw.get("power_entity", f"sensor.{name.lower().replace(' ', '_')}_power"),
            "priority": priority,
            "expected_power": expected_power,
            "min_run_minutes": kw.get("min_run_minutes", 0),
            "cooldown_seconds": cooldown_seconds,
        }

    @staticmethod
    def _run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_only_one_consumer_turns_on_per_cycle(self):
        """Phase 3 should turn on at most one fresh consumer per cycle."""
        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            self._consumer("Boiler", priority=1, cooldown_seconds=120),
            self._consumer("Washer", priority=2, cooldown_seconds=60),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        self._run(coord._async_update_data())

        calls = hass.services.async_call.await_args_list
        on_calls = [c for c in calls if c.args[1] == "turn_on"]
        assert len(on_calls) == 1, f"Expected 1 turn_on, got {len(on_calls)}"
        assert on_calls[0].args[2]["entity_id"] == "switch.boiler"
        assert coord._last_turn_on_cooldown == 120.0
        assert coord._last_turn_on_ts == now

    def test_cooldown_expired_allows_next_consumer(self):
        """After cooldown expires, the next consumer can turn on."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            self._consumer("Boiler", priority=1, cooldown_seconds=60),
            self._consumer("Washer", priority=2, cooldown_seconds=60),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        # Boiler already ON, cooldown expired 1s ago.
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)
        coord._last_turn_on_ts = now - 61
        coord._last_turn_on_cooldown = 60.0

        self._run(coord._async_update_data())

        calls = hass.services.async_call.await_args_list
        on_calls = [c for c in calls if c.args[1] == "turn_on"]
        # Boiler re-affirmed + Washer fresh turn-on = 2
        assert len(on_calls) == 2

    def test_cooldown_active_blocks_next_consumer(self):
        """While cooldown is active, next consumer stays off despite surplus."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            self._consumer("Boiler", priority=1, cooldown_seconds=300),
            self._consumer("Washer", priority=2, cooldown_seconds=60),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        # Boiler ON 100s ago, cooldown 300s → 200s remaining.
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)
        coord._last_turn_on_ts = now - 100
        coord._last_turn_on_cooldown = 300.0

        result = self._run(coord._async_update_data())

        calls = hass.services.async_call.await_args_list
        on_calls = [c for c in calls if c.args[1] == "turn_on"]
        assert len(on_calls) == 1  # only Boiler re-affirmed
        assert on_calls[0].args[2]["entity_id"] == "switch.boiler"
        assert result["consumer_states"]["Washer"]["is_on"] is False

    def test_per_consumer_cooldown_uses_turned_on_consumers_value(self):
        """The cooldown duration recorded is from the consumer that just turned on."""
        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            self._consumer("SmallDevice", priority=1, cooldown_seconds=30),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        self._run(coord._async_update_data())

        assert coord._last_turn_on_cooldown == 30.0

    def test_default_cooldown_when_field_missing(self):
        """Consumers without cooldown_seconds use DEFAULT_COOLDOWN_SECONDS."""
        from custom_components.power_manager.const import DEFAULT_COOLDOWN_SECONDS

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        # Consumer dict without cooldown_seconds (simulating old storage format).
        consumers = [{
            "name": "OldBoiler",
            "switch_entity": "switch.old_boiler",
            "power_entity": "sensor.old_boiler_power",
            "priority": 1,
            "expected_power": 500.0,
            "min_run_minutes": 0,
        }]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        self._run(coord._async_update_data())

        assert coord._last_turn_on_cooldown == DEFAULT_COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# Helper shared across new test classes
# ---------------------------------------------------------------------------

def _consumer(name, priority=1, expected_power=500.0, cooldown_seconds=300, **kw):
    return {
        "name": name,
        "switch_entity": kw.get("switch_entity", f"switch.{name.lower().replace(' ', '_')}"),
        "power_entity": kw.get("power_entity", f"sensor.{name.lower().replace(' ', '_')}_power"),
        "priority": priority,
        "expected_power": expected_power,
        "min_run_minutes": kw.get("min_run_minutes", 0),
        "cooldown_seconds": cooldown_seconds,
    }


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Priority Preemption (Phase 1.5)
# ---------------------------------------------------------------------------

class TestPriorityPreemption:
    """Tests for Phase 1.5: lower-priority consumers are preempted when
    a higher-priority consumer cannot get surplus."""

    def test_lower_priority_preempted_for_higher(self):
        """P3 ON should be preempted when P1 cannot turn on."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        # P3 is ON (4000W draw included in base_load of 4700W).
        # Surplus = 5250 - 4700 = 550W.  P1 needs 630W → can't turn on.
        states = {
            "sensor.base_load": _make_state("4700", "W"),
            "sensor.pv": _make_state("5250", "W"),
            "sensor.ev_power": _make_state("4000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=600),
            _consumer("EV", priority=3, expected_power=4000, power_entity="sensor.ev_power"),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        # EV is ON (from previous force_on→auto switch), timer expired.
        coord._runtime["EV"] = ConsumerRuntime(is_on=True)

        result = _run(coord._async_update_data())

        # EV should be preempted OFF.
        assert result["consumer_states"]["EV"]["is_on"] is False
        # Reason should mention preemption.
        assert "preempted" in result["consumer_states"]["EV"]["reason"].lower()

    def test_min_run_hold_exempt_from_preemption(self):
        """A consumer in min-run hold should NOT be preempted."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("4700", "W"),
            "sensor.pv": _make_state("5250", "W"),
            "sensor.ev_power": _make_state("4000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=600),
            _consumer("EV", priority=3, expected_power=4000, power_entity="sensor.ev_power"),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        # EV is ON with min-run hold still active (expires at now+60).
        coord._runtime["EV"] = ConsumerRuntime(is_on=True, on_until_ts=now + 60)

        result = _run(coord._async_update_data())

        # EV should stay ON (protected by min-run hold).
        assert result["consumer_states"]["EV"]["is_on"] is True

    def test_force_on_not_preempted(self):
        """force_on consumers should never be preempted."""
        from custom_components.power_manager.coordinator import ConsumerRuntime
        from custom_components.power_manager.const import MODE_FORCE_ON

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("4700", "W"),
            "sensor.pv": _make_state("5250", "W"),
            "sensor.ev_power": _make_state("4000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=600),
            _consumer("EV", priority=3, expected_power=4000, power_entity="sensor.ev_power"),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["EV"] = ConsumerRuntime(is_on=True, mode=MODE_FORCE_ON)

        result = _run(coord._async_update_data())

        # EV stays ON — force_on overrides preemption.
        assert result["consumer_states"]["EV"]["is_on"] is True


# ---------------------------------------------------------------------------
# Incremental Shedding (Phase 2)
# ---------------------------------------------------------------------------

class TestIncrementalShedding:
    """Tests for Phase 2: at most one active consumer is shed per cycle."""

    def test_only_one_consumer_shed_per_cycle(self):
        """When both P1 and P2 need to turn off, only P2 (lower prio) is shed."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        # Both ON, surplus deeply negative.
        states = {
            "sensor.base_load": _make_state("2000", "W"),
            "sensor.pv": _make_state("1000", "W"),  # surplus = -1000W
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=600),
            _consumer("Washer", priority=2, expected_power=600),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)
        coord._runtime["Washer"] = ConsumerRuntime(is_on=True)

        result = _run(coord._async_update_data())

        # Washer (P2) shed, Boiler (P1) deferred — stays ON in runtime.
        assert result["consumer_states"]["Washer"]["is_on"] is False
        assert result["consumer_states"]["Boiler"]["is_on"] is True

    def test_deferred_consumer_preserves_runtime_is_on(self):
        """A deferred consumer's runtime.is_on stays True for next-cycle hysteresis."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("2000", "W"),
            "sensor.pv": _make_state("1000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=600),
            _consumer("Washer", priority=2, expected_power=600),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)
        coord._runtime["Washer"] = ConsumerRuntime(is_on=True)

        _run(coord._async_update_data())

        # Boiler was deferred → runtime.is_on must still be True.
        assert coord._runtime["Boiler"].is_on is True

    def test_already_off_consumer_does_not_consume_shed_slot(self):
        """An already-OFF consumer must not block shedding of ON consumers.
        This was a real bug: P3 (OFF) consumed the shed slot, P2 (ON) was deferred."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("2000", "W"),
            "sensor.pv": _make_state("1000", "W"),  # surplus = -1000W
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=600),
            _consumer("Washer", priority=2, expected_power=600),
            _consumer("EV", priority=3, expected_power=4000),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)
        coord._runtime["Washer"] = ConsumerRuntime(is_on=True)
        # EV is already OFF (default is_on=False).

        result = _run(coord._async_update_data())

        # Washer (P2) should be shed — NOT blocked by EV (P3, already off).
        assert result["consumer_states"]["Washer"]["is_on"] is False
        # Boiler (P1) deferred — stays ON.
        assert result["consumer_states"]["Boiler"]["is_on"] is True


# ---------------------------------------------------------------------------
# Startup Warmup
# ---------------------------------------------------------------------------

class TestStartupWarmup:
    """Tests for startup warmup: no switching for the first N cycles."""

    def test_warmup_skips_switching(self):
        """During warmup, no switch calls are made even with surplus."""
        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [_consumer("Boiler", priority=1, expected_power=500)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._warmup_remaining = 2  # 2 cycles to go

        _run(coord._async_update_data())

        # No switch calls during warmup.
        hass.services.async_call.assert_not_awaited()
        assert coord._warmup_remaining == 1

    def test_warmup_counts_down_to_zero(self):
        """Warmup counter decrements each cycle and switching starts at zero."""
        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [_consumer("Boiler", priority=1, expected_power=500)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._warmup_remaining = 1  # last warmup cycle

        # Cycle 1: still warming up.
        _run(coord._async_update_data())
        hass.services.async_call.assert_not_awaited()
        assert coord._warmup_remaining == 0

        # Cycle 2: warmup done → switching happens.
        hass.services.async_call.reset_mock()
        _run(coord._async_update_data())
        assert hass.services.async_call.await_count > 0


# ---------------------------------------------------------------------------
# Priority Uniqueness
# ---------------------------------------------------------------------------

class TestPriorityUniqueness:
    """Tests for priority uniqueness validation on add/update."""

    def test_add_consumer_rejects_duplicate_priority(self):
        from custom_components.power_manager.coordinator import PowerManagerCoordinator
        from homeassistant.helpers.update_coordinator import UpdateFailed

        now = 1000.0
        states = {}
        producers = []
        consumers = [_consumer("Boiler", priority=1)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        with pytest.raises(Exception, match="priority 1 is already used"):
            _run(coord.async_add_consumer(
                name="Washer",
                switch_entity="switch.washer",
                power_entity="sensor.washer_power",
                priority=1,
                expected_power=500,
                min_run_minutes=0,
            ))

    def test_add_consumer_allows_unique_priority(self):
        now = 1000.0
        states = {}
        producers = []
        consumers = [_consumer("Boiler", priority=1)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        # Should not raise.
        _run(coord.async_add_consumer(
            name="Washer",
            switch_entity="switch.washer",
            power_entity="sensor.washer_power",
            priority=2,
            expected_power=500,
            min_run_minutes=0,
        ))
        assert len(coord._consumers) == 2

    def test_update_consumer_same_priority_allowed(self):
        """Saving a consumer with its own unchanged priority must not fail."""
        now = 1000.0
        states = {}
        consumers = [
            _consumer("Boiler", priority=1),
            _consumer("Washer", priority=2),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=[], consumers=consumers)

        # Update Boiler with priority=1 (unchanged) — should not raise.
        _run(coord.async_update_consumer(name="Boiler", priority=1))
        assert coord._consumers[0]["priority"] == 1

    def test_update_consumer_rejects_conflicting_priority(self):
        now = 1000.0
        consumers = [
            _consumer("Boiler", priority=1),
            _consumer("Washer", priority=2),
        ]
        hass = _make_hass(states={}, now=now)
        coord = _make_coordinator(hass, producers=[], consumers=consumers)

        with pytest.raises(Exception, match="priority 2 is already used"):
            _run(coord.async_update_consumer(name="Boiler", priority=2))


# ---------------------------------------------------------------------------
# Reason field in consumer_states
# ---------------------------------------------------------------------------

class TestReasonField:
    """Tests for the 'reason' field in consumer_states API response."""

    def test_reason_present_in_consumer_states(self):
        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [_consumer("Boiler", priority=1, expected_power=500)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)

        result = _run(coord._async_update_data())

        assert "reason" in result["consumer_states"]["Boiler"]
        assert "surplus" in result["consumer_states"]["Boiler"]["reason"].lower()

    def test_stopped_reason(self):
        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [_consumer("Boiler", priority=1)]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers, running=False)

        result = _run(coord._async_update_data())

        assert result["consumer_states"]["Boiler"]["reason"] == "stopped"

    def test_cooldown_reason(self):
        """Consumer blocked by cooldown should have a reason mentioning cooldown."""
        from custom_components.power_manager.coordinator import ConsumerRuntime

        now = 1000.0
        states = {
            "sensor.base_load": _make_state("0", "W"),
            "sensor.pv": _make_state("5000", "W"),
        }
        producers = [{"name": "PV", "entity_id": "sensor.pv"}]
        consumers = [
            _consumer("Boiler", priority=1, expected_power=500, cooldown_seconds=300),
            _consumer("Washer", priority=2, expected_power=500, cooldown_seconds=60),
        ]
        hass = _make_hass(states=states, now=now)
        coord = _make_coordinator(hass, producers=producers, consumers=consumers)
        coord._runtime["Boiler"] = ConsumerRuntime(is_on=True)
        coord._last_turn_on_ts = now - 100
        coord._last_turn_on_cooldown = 300.0

        result = _run(coord._async_update_data())

        assert "cooldown" in result["consumer_states"]["Washer"]["reason"].lower()
