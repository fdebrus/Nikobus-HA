"""
Bootstrap sys.modules so all nikobus modules can be imported and tested
without a full Home Assistant installation.
"""

import asyncio
import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).parent.parent
COMP = ROOT / "custom_components" / "nikobus"
DISCO = COMP / "discovery"


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
    callback=_ha_callback,
)
_mod("homeassistant.config_entries", ConfigEntry=type("ConfigEntry", (), {}))
_mod(
    "homeassistant.exceptions",
    HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
)
_mod("homeassistant.helpers")
_mod(
    "homeassistant.helpers.dispatcher",
    async_dispatcher_send=lambda *a, **kw: None,
)

# homeassistant.util.dt — used by discovery/fileio.py and discovery/discovery.py
from datetime import datetime, timezone as _tz

_mod("homeassistant.util")
_mod(
    "homeassistant.util.dt",
    now=lambda tz=None: datetime.now(_tz.utc),
    utcnow=lambda: datetime.now(_tz.utc),
    as_local=lambda dt: dt,
)


class _DataUpdateCoordinatorStub:
    """Minimal stub for homeassistant.helpers.update_coordinator.DataUpdateCoordinator."""

    # Support DataUpdateCoordinator[None] generic syntax used in coordinator.py.
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_method=None, update_interval=None):
        self.hass = hass
        self._logger = logger

    def async_update_listeners(self):
        pass


_mod(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_DataUpdateCoordinatorStub,
    UpdateFailed=type("UpdateFailed", (Exception,), {}),
)

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

_disco_pkg = _mod("custom_components.nikobus.discovery")
_disco_pkg.__path__ = [str(DISCO)]

# ---------------------------------------------------------------------------
# Load nikobus modules in dependency order
# ---------------------------------------------------------------------------
_load("custom_components.nikobus.nkbprotocol", COMP / "nkbprotocol.py")
_load("custom_components.nikobus.exceptions", COMP / "exceptions.py")
_load("custom_components.nikobus.const", COMP / "const.py")

# Discovery sub-modules (no external HA deps beyond homeassistant.util.dt)
_load("custom_components.nikobus.discovery.base", DISCO / "base.py")
_load("custom_components.nikobus.discovery.mapping", DISCO / "mapping.py")
_load("custom_components.nikobus.discovery.protocol", DISCO / "protocol.py")
_load("custom_components.nikobus.discovery.chunk_decoder", DISCO / "chunk_decoder.py")
_load("custom_components.nikobus.discovery.switch_decoder", DISCO / "switch_decoder.py")
_load("custom_components.nikobus.discovery.dimmer_decoder", DISCO / "dimmer_decoder.py")
_load("custom_components.nikobus.discovery.shutter_decoder", DISCO / "shutter_decoder.py")
_load("custom_components.nikobus.discovery.fileio", DISCO / "fileio.py")
_disco_mod = _load("custom_components.nikobus.discovery.discovery", DISCO / "discovery.py")

# Expose NikobusDiscovery on the package so both `from .discovery import NikobusDiscovery`
# (coordinator) and `from custom_components.nikobus.discovery.discovery import NikobusDiscovery`
# (test_inventory_parsing) resolve correctly.
_disco_pkg.NikobusDiscovery = _disco_mod.NikobusDiscovery

_load("custom_components.nikobus.nkblistener", COMP / "nkblistener.py")
_load("custom_components.nikobus.nkbcommand", COMP / "nkbcommand.py")
_load("custom_components.nikobus.nkbAPI", COMP / "nkbAPI.py")
_load("custom_components.nikobus.nkbactuator", COMP / "nkbactuator.py")
_load("custom_components.nikobus.nkbconnect", COMP / "nkbconnect.py")
_load("custom_components.nikobus.nkbconfig", COMP / "nkbconfig.py")
_load("custom_components.nikobus.router", COMP / "router.py")
_load("custom_components.nikobus.coordinator", COMP / "coordinator.py")
