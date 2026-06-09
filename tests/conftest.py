"""
Bootstrap sys.modules so all nikobus modules can be imported and tested
without a full Home Assistant installation.
"""

import importlib.machinery
import importlib.util
import sys
import types
from datetime import datetime, timezone as _tz
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).parent.parent
COMP = ROOT / "custom_components" / "nikobus"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mod(name: str, **attrs) -> types.ModuleType:
    """Create and register a minimal stub module.

    Sets __spec__ so that importlib.util.find_spec() doesn't raise ValueError
    when it encounters this stub in sys.modules (Python 3.11+ strict check).
    """
    m = types.ModuleType(name)
    m.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def _load(pkg: str, path: Path) -> types.ModuleType:
    """Load a Python source file as a named module in the correct package."""
    spec = importlib.util.spec_from_file_location(pkg, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = ".".join(pkg.split(".")[:-1])
    sys.modules[pkg] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Home Assistant stubs
# ---------------------------------------------------------------------------

def _ha_callback(fn):
    """Passthrough stub for homeassistant.core.callback."""
    return fn


_mod("homeassistant")
_mod(
    "homeassistant.core",
    HomeAssistant=type("HomeAssistant", (), {}),
    Event=type("Event", (), {}),
    CALLBACK_TYPE=object,
    callback=_ha_callback,
    ServiceCall=type("ServiceCall", (), {}),
    ServiceResponse=dict,
    SupportsResponse=type("SupportsResponse", (), {"ONLY": "only", "NONE": "none"}),
)
class _ConfigEntry:
    def __class_getitem__(cls, item):
        return cls


class _ConfigFlow:
    # Allow ``class X(ConfigFlow, domain="nikobus")`` subclassing.
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


class _OptionsFlow:
    pass


class _ConfigEntryState:
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


_mod(
    "homeassistant.config_entries",
    ConfigEntry=_ConfigEntry,
    ConfigFlow=_ConfigFlow,
    OptionsFlow=_OptionsFlow,
    ConfigEntryState=_ConfigEntryState,
)
class _HomeAssistantError(Exception):
    """Test stub that accepts the same kwargs as the real
    HomeAssistantError (``translation_domain``, ``translation_key``,
    ``translation_placeholders``) so coordinator code raising it can
    be exercised in tests."""

    def __init__(self, *args, translation_domain=None, translation_key=None,
                 translation_placeholders=None, **kwargs):
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


_mod(
    "homeassistant.exceptions",
    HomeAssistantError=_HomeAssistantError,
    ConfigEntryNotReady=type("ConfigEntryNotReady", (_HomeAssistantError,), {}),
    ServiceValidationError=type(
        "ServiceValidationError", (_HomeAssistantError,), {}
    ),
)
_mod("homeassistant.helpers.typing", ConfigType=dict)

# homeassistant.helpers.dispatcher
_mod("homeassistant.helpers")
_mod(
    "homeassistant.helpers.dispatcher",
    async_dispatcher_send=lambda *a, **kw: None,
    async_dispatcher_connect=lambda *a, **kw: (lambda: None),
)

# homeassistant.helpers.device_registry — DeviceInfo is a thin dict wrapper
class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)

_mod(
    "homeassistant.helpers.device_registry",
    DeviceInfo=_DeviceInfo,
    DeviceEntry=type("DeviceEntry", (), {}),
    async_get=lambda hass: None,
)

# homeassistant.helpers.entity_registry
_mod("homeassistant.helpers.entity_registry", async_get=lambda hass: None)

# homeassistant.helpers.area_registry
_mod("homeassistant.helpers.area_registry", async_get=lambda hass: None)

# homeassistant.helpers.issue_registry — used by coordinator.refresh_repair_issues
class _IssueSeverity:
    WARNING = "warning"
    ERROR = "error"


_mod(
    "homeassistant.helpers.issue_registry",
    IssueSeverity=_IssueSeverity,
    async_create_issue=lambda *a, **kw: None,
    async_delete_issue=lambda *a, **kw: None,
)

# homeassistant.helpers.storage — for NikobusButtonStorage / NikobusModuleStorage.
# Coordinator only instantiates these; tests that need persistence override via
# drop-in fakes, so a minimal no-op Store suffices here.
class _Store:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, version, key):
        self._data = None

    async def async_load(self):
        return None

    async def async_save(self, data):
        self._data = data


_mod("homeassistant.helpers.storage", Store=_Store)

# homeassistant.helpers.event
_mod("homeassistant.helpers.event", async_call_later=lambda *a, **kw: (lambda: None))

# homeassistant.helpers.entity_platform
_mod(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=type("AddEntitiesCallback", (), {}),
)

# homeassistant.helpers.update_coordinator
class _DataUpdateCoordinatorStub:
    """Minimal stub for DataUpdateCoordinator."""

    # Support DataUpdateCoordinator[None] generic syntax used in coordinator.py.
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_method=None, update_interval=None):
        self.hass = hass
        self._logger = logger

    def async_update_listeners(self):
        pass


class _CoordinatorEntityStub:
    """Minimal stub for CoordinatorEntity."""

    available = True  # NikobusEntity.available reads super().available

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator, *args, **kwargs):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        pass

    def async_on_remove(self, fn):
        pass

    def async_write_ha_state(self):
        pass

    def _handle_coordinator_update(self):
        # Real CoordinatorEntity writes HA state on every update.
        self.async_write_ha_state()


_mod(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinatorStub,
    UpdateFailed=type("UpdateFailed", (Exception,), {}),
    CoordinatorEntity=_CoordinatorEntityStub,
)

# homeassistant.util.dt — used by discovery/fileio.py and discovery/discovery.py
_mod("homeassistant.util")
_mod(
    "homeassistant.util.dt",
    now=lambda tz=None: datetime.now(_tz.utc),
    utcnow=lambda: datetime.now(_tz.utc),
    as_local=lambda dt: dt,
)

# homeassistant.components — stubs for sensor (and future platforms)
_mod("homeassistant.components")
class _SensorDeviceClass:
    ENUM = "enum"


class _SensorStateClass:
    MEASUREMENT = "measurement"


_mod(
    "homeassistant.components.sensor",
    SensorEntity=type("SensorEntity", (), {}),
    SensorDeviceClass=_SensorDeviceClass,
    SensorStateClass=_SensorStateClass,
    DOMAIN="sensor",
)
_mod(
    "homeassistant.helpers.entity",
    EntityCategory=type("EntityCategory", (), {"DIAGNOSTIC": "diagnostic", "CONFIG": "config"}),
)
_mod(
    "homeassistant.const",
    PERCENTAGE="%",
    EntityCategory=type("EntityCategory", (), {"DIAGNOSTIC": "diagnostic", "CONFIG": "config"}),
)
_mod(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=type("AddEntitiesCallback", (), {}),
    AddConfigEntryEntitiesCallback=type("AddConfigEntryEntitiesCallback", (), {}),
)
_mod("homeassistant.components.binary_sensor", BinarySensorEntity=type("BinarySensorEntity", (), {}), DOMAIN="binary_sensor")
_mod("homeassistant.components.switch", SwitchEntity=type("SwitchEntity", (), {}), DOMAIN="switch")
_mod("homeassistant.components.button", ButtonEntity=type("ButtonEntity", (), {}), DOMAIN="button")
_mod(
    "homeassistant.components.cover",
    CoverEntity=type("CoverEntity", (), {}),
    CoverDeviceClass=type("CoverDeviceClass", (), {"SHUTTER": "shutter"}),
    CoverEntityFeature=type(
        "CoverEntityFeature", (), {"OPEN": 1, "CLOSE": 2, "STOP": 4, "SET_POSITION": 8}
    ),
    ATTR_CURRENT_POSITION="current_position",
    ATTR_POSITION="position",
    DOMAIN="cover",
)
_mod(
    "homeassistant.components.light",
    LightEntity=type("LightEntity", (), {}),
    ColorMode=type("ColorMode", (), {"BRIGHTNESS": "brightness", "ONOFF": "onoff"}),
    ATTR_BRIGHTNESS="brightness",
    DOMAIN="light",
)
_mod("homeassistant.components.scene", Scene=type("Scene", (), {}), DOMAIN="scene")

# --- config-flow / repairs import surface -------------------------------
# voluptuous + the HA flow/selector helpers aren't installed in this env;
# stub just enough to import config_flow.py / repairs.py and exercise
# their pure helpers (schema builders themselves are not invoked).
_mod(
    "voluptuous",
    Invalid=type("Invalid", (Exception,), {}),
    Schema=lambda *a, **k: (a[0] if a else None),
    Optional=lambda *a, **k: (a[0] if a else None),
    Required=lambda *a, **k: (a[0] if a else None),
    All=lambda *a, **k: a,
    Range=lambda *a, **k: None,
    Coerce=lambda *a, **k: None,
    In=lambda *a, **k: None,
    Length=lambda *a, **k: None,
)
_mod(
    "homeassistant.helpers.config_validation",
    positive_int=int,
    string=str,
    ensure_list=lambda v: v if isinstance(v, list) else [v],
    config_entry_only_config_schema=lambda *a, **k: None,
)
_mod(
    "homeassistant.helpers.selector",
    SelectSelector=lambda *a, **k: None,
    SelectSelectorConfig=lambda *a, **k: None,
    SelectSelectorMode=type(
        "SelectSelectorMode", (), {"LIST": "list", "DROPDOWN": "dropdown"}
    ),
    SelectOptionDict=lambda **k: dict(**k),
    NumberSelector=lambda *a, **k: None,
    NumberSelectorConfig=lambda *a, **k: None,
    NumberSelectorMode=type("NumberSelectorMode", (), {"BOX": "box", "SLIDER": "slider"}),
    TextSelector=lambda *a, **k: None,
    TextSelectorConfig=lambda *a, **k: None,
    FileSelector=lambda *a, **k: None,
    FileSelectorConfig=lambda *a, **k: None,
)
_mod("homeassistant.data_entry_flow", FlowResult=dict)
# homeassistant.components.file_upload — process_uploaded_file is patched
# per-test; stub the module so config_flow's lazy import resolves.
_mod("homeassistant.components.file_upload", process_uploaded_file=None)
_mod("homeassistant.components.repairs", RepairsFlow=type("RepairsFlow", (), {}))


class _RestoreEntityStub:
    """Minimal stub for RestoreEntity."""

    async def async_get_last_state(self):  # pragma: no cover - overridden in tests
        return None

    async def async_added_to_hass(self):  # pragma: no cover
        return None


_mod("homeassistant.helpers.restore_state", RestoreEntity=_RestoreEntityStub)

# ---------------------------------------------------------------------------
# Third-party stubs
# ---------------------------------------------------------------------------
_mod(
    "serial_asyncio",
    open_serial_connection=AsyncMock(return_value=(AsyncMock(), AsyncMock())),
)
_mod("aiofiles", **{"open": AsyncMock()})

# ---------------------------------------------------------------------------
# Nikobus package skeleton (bypass __init__.py which requires voluptuous/HA)
# ---------------------------------------------------------------------------
_pkg = _mod("custom_components")
_pkg.__path__ = [str(ROOT / "custom_components")]

_niko = _mod("custom_components.nikobus")
_niko.__path__ = [str(COMP)]

# ---------------------------------------------------------------------------
# Load nikobus modules in dependency order
# ---------------------------------------------------------------------------
_load("custom_components.nikobus.exceptions", COMP / "exceptions.py")
_load("custom_components.nikobus.const", COMP / "const.py")

# Discovery lives in the standalone nikobus-connect PyPI package
# (`pip install nikobus-connect`, imported as `nikobus_connect`); the
# integration imports it directly, so there's nothing to load here.

_load("custom_components.nikobus.nkbactuator", COMP / "nkbactuator.py")
_load("custom_components.nikobus.nkbconfig", COMP / "nkbconfig.py")
_load("custom_components.nikobus.router", COMP / "router.py")
_load("custom_components.nikobus.coordinator", COMP / "coordinator.py")
_load("custom_components.nikobus.sensor", COMP / "sensor.py")

# ---------------------------------------------------------------------------
# Load the package __init__ itself, last (services, setup, migration live
# there). The bare skeleton module above existed only so the submodules
# could be loaded first; replace it with the real package module so
# ``from custom_components.nikobus import async_migrate_entry`` works.
# ``submodule_search_locations`` keeps it a proper package (correct
# ``__path__``/``__package__`` for the relative imports inside it).
# ---------------------------------------------------------------------------
_init_spec = importlib.util.spec_from_file_location(
    "custom_components.nikobus",
    COMP / "__init__.py",
    submodule_search_locations=[str(COMP)],
)
_init_mod = importlib.util.module_from_spec(_init_spec)
sys.modules["custom_components.nikobus"] = _init_mod
_init_spec.loader.exec_module(_init_mod)
