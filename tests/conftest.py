import sys
import types
from pathlib import Path


def _ensure_module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


# Stub external dependencies used during module import so unit tests can run
voluptuous = _ensure_module("voluptuous")
voluptuous.Schema = lambda *args, **kwargs: None
voluptuous.Optional = lambda *args, **kwargs: None

homeassistant = _ensure_module("homeassistant")
homeassistant.core = _ensure_module("homeassistant.core")
homeassistant.config_entries = _ensure_module("homeassistant.config_entries")
homeassistant.exceptions = _ensure_module("homeassistant.exceptions")

homeassistant.core.HomeAssistant = type("HomeAssistant", (), {})
homeassistant.core.ServiceCall = type("ServiceCall", (), {})
homeassistant.config_entries.ConfigEntry = type("ConfigEntry", (), {})
homeassistant.exceptions.ConfigEntryNotReady = type(
    "ConfigEntryNotReady", (Exception,), {}
)
homeassistant.exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})

helpers = _ensure_module("homeassistant.helpers")
helpers.config_validation = _ensure_module("homeassistant.helpers.config_validation")
helpers.device_registry = _ensure_module("homeassistant.helpers.device_registry")
helpers.entity_registry = _ensure_module("homeassistant.helpers.entity_registry")
helpers.update_coordinator = _ensure_module("homeassistant.helpers.update_coordinator")
helpers.__path__ = []
helpers.config_validation.string = lambda value: value
helpers.update_coordinator.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (), {}
)
helpers.update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})

components = _ensure_module("homeassistant.components")
components.switch = _ensure_module("homeassistant.components.switch")
components.light = _ensure_module("homeassistant.components.light")
components.cover = _ensure_module("homeassistant.components.cover")
components.binary_sensor = _ensure_module("homeassistant.components.binary_sensor")
components.button = _ensure_module("homeassistant.components.button")
components.scene = _ensure_module("homeassistant.components.scene")
components.switch.DOMAIN = "switch"
components.light.DOMAIN = "light"
components.cover.DOMAIN = "cover"
components.binary_sensor.DOMAIN = "binary_sensor"
components.button.DOMAIN = "button"
components.scene.DOMAIN = "scene"

sys.modules.setdefault("serial_asyncio_fast", types.ModuleType("serial_asyncio_fast"))
aiofiles = types.ModuleType("aiofiles")


async def _noop_open(*args, **kwargs):
    return None


aiofiles.open = _noop_open
sys.modules.setdefault("aiofiles", aiofiles)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
