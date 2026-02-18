DOMAIN = "power_manager"
PLATFORMS = ["sensor", "switch"]

CONF_SCAN_INTERVAL = "scan_interval"
CONF_BASE_LOAD_ENTITY = "base_load_entity"
CONF_PRODUCERS = "producers"
CONF_CONSUMERS = "consumers"

DEFAULT_SCAN_INTERVAL = 10

MODE_AUTO = "auto"
MODE_FORCE_ON = "force_on"
MODE_FORCE_OFF = "force_off"
VALID_MODES = {MODE_AUTO, MODE_FORCE_ON, MODE_FORCE_OFF}
