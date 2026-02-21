"""
Constants for the Power Manager integration.

Layer: BACKEND
Shared across coordinator, entity platforms, config flow, and the WS API.

Modes
-----
MODE_AUTO        Surplus-controlled (default).
MODE_FORCE_ON    Always on regardless of surplus.
MODE_FORCE_OFF   Always off regardless of surplus.
MODE_DEACTIVATED Excluded from surplus budgeting; switch not touched.
"""
from __future__ import annotations

DOMAIN = "power_manager"
PLATFORMS = ["sensor", "switch", "select"]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_BASE_LOAD_ENTITY = "base_load_entity"
CONF_PRODUCERS = "producers"
CONF_CONSUMERS = "consumers"

INTEGRATION_VERSION = "0.2.1"

DEFAULT_SCAN_INTERVAL = 10

MODE_AUTO = "auto"
MODE_FORCE_ON = "force_on"
MODE_FORCE_OFF = "force_off"
MODE_DEACTIVATED = "deactivated"
VALID_MODES = {MODE_AUTO, MODE_FORCE_ON, MODE_FORCE_OFF, MODE_DEACTIVATED}
