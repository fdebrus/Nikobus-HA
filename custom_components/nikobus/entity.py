"""Shared entity helpers for the Nikobus integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN, HUB_IDENTIFIER
from .coordinator import NikobusDataCoordinator

# Sentinel return for ``_render_state``: this entity opts out of
# write-diffing and writes on every coordinator update (the default).
_NO_DIFF = object()


def command_error(err: Exception) -> HomeAssistantError:
    """A failed bus command as a translated HA error.

    Shared by the output platforms (switch / light / cover / scene): each
    entity action wraps a failed bus command with
    ``raise command_error(err) from err`` so the user sees a clean message
    instead of the raw library exception.
    """
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="communication_error",
        translation_placeholders={"error": str(err)},
    )


def hub_device_info() -> dr.DeviceInfo:
    """DeviceInfo for the Nikobus bridge (hub).

    Single source of truth for the bridge device — the ``via_device``
    parent of the category devices and the device the bridge-level
    entities (connection/discovery status, action buttons) attach to.
    Shared by the button and sensor platforms and the hub registration
    in ``__init__`` so they can't drift.
    """
    return dr.DeviceInfo(
        identifiers={(DOMAIN, HUB_IDENTIFIER)},
        name="Nikobus Bridge",
        manufacturer=BRAND,
        model="PC-Link Bridge",
    )


class NikobusEntity(CoordinatorEntity[NikobusDataCoordinator]):
    """Base entity for Nikobus devices with targeted refresh support."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        address: str,
        name: str,
        model: str,
        via_device: tuple[str, str] | None = None,
        device_identifier: str | None = None,
    ) -> None:
        """Initialize the entity with shared device information.

        ``address`` is the bus address used for state-refresh signal
        routing and the module-address attribute. ``device_identifier``
        overrides the HA *device* this entity belongs to; it defaults to
        ``address`` (one device per module). Pass a distinct value to
        give an entity its own device even though it shares a bus address
        with another — e.g. a Central Function scene whose ``address`` is
        also a physical button, so the scene gets its own device named by
        the CF rather than being merged into the button's device.
        """
        super().__init__(coordinator)
        self._address = address
        self._device_name = name
        self._device_model = model

        # Group every channel of a module under one physical device.
        device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, device_identifier or self._address)},
            name=self._device_name,
            manufacturer=BRAND,
            model=self._device_model,
        )
        if via_device is not None:
            device_info["via_device"] = via_device
        self._attr_device_info = device_info

        #: Last ``(available, render_state)`` actually written, for diffing.
        self._last_render: tuple[bool, Any] | None = None

    def _invalidate_optimistic(self) -> None:
        """Drop optimistic caches before the real state is read (override)."""

    def _render_state(self) -> Any:
        """Return the displayed state used to skip redundant writes.

        Override to opt this entity into write-diffing (returning a
        hashable that captures what the user sees). The default opts out
        — the entity writes on every coordinator update.
        """
        return _NO_DIFF

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write HA state on a coordinator update, skipping the write when
        nothing the user sees has changed.

        Each output module is polled every cycle and entities are woken
        per module; without this, every channel re-rendered (and recom-
        puted its attributes) every cycle even when its byte was
        unchanged. Diffing on ``(available, render_state)`` collapses an
        unchanged cycle to a cheap comparison.
        """
        self._invalidate_optimistic()
        state = self._render_state()
        if state is not _NO_DIFF:
            signature = (self.available, state)
            if signature == self._last_render:
                return
            self._last_render = signature
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return True only when the coordinator is healthy and the connection is live."""
        return super().available and self.coordinator.nikobus_connection.is_connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return shared state attributes safely."""
        return {
            "nikobus_module_address": self._address,
            "nikobus_module_model": self._device_model,
        }

    async def async_added_to_hass(self) -> None:
        """Register targeted signal listener for this specific module address."""
        await super().async_added_to_hass()

        signal = f"{DOMAIN}_update_{self._address}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._handle_coordinator_update)
        )


def device_entry_diagnostics(device: dr.DeviceEntry) -> dict[str, Any]:
    """Return diagnostics data for a Nikobus device entry."""
    return {
        "id": device.id,
        "name": device.name,
        "model": device.model,
        "manufacturer": device.manufacturer,
        "identifiers": sorted(device.identifiers),
    }