"""
Stub out Home Assistant modules so the coordinator can be imported and tested
without a full HA installation.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from unittest.mock import MagicMock


def _stub_module(name: str) -> MagicMock:
    mod = MagicMock()
    sys.modules[name] = mod
    return mod


# Stub every HA sub-package that our source files import.
_HA_MODULES = [
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.http",
    "homeassistant.components.panel_custom",
    "homeassistant.components.sensor",
    "homeassistant.components.websocket_api",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.config_validation",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.update_coordinator",
]

for _name in _HA_MODULES:
    if _name not in sys.modules:
        _stub_module(_name)

# ---- concrete stubs that the coordinator actually uses ----------------------

# UpdateFailed needs to be a real exception class
class _UpdateFailed(Exception):
    pass


_coordinator_mod = sys.modules["homeassistant.helpers.update_coordinator"]
_coordinator_mod.UpdateFailed = _UpdateFailed


# DataUpdateCoordinator base class
class _DataUpdateCoordinator:
    def __init__(self, hass, logger, name, update_interval, **kwargs):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None

    def __class_getitem__(cls, item):
        return cls


_coordinator_mod.DataUpdateCoordinator = _DataUpdateCoordinator

# UnitOfPower constants
_const_mod = sys.modules["homeassistant.const"]
_const_mod.UnitOfPower = MagicMock()
_const_mod.UnitOfPower.WATT = "W"

# SensorDeviceClass
_sensor_mod = sys.modules["homeassistant.components.sensor"]
_sensor_mod.SensorDeviceClass = MagicMock()
_sensor_mod.SensorDeviceClass.POWER = "power"
