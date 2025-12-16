from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, BRAND
from .coordinator import NikobusDataCoordinator


class NikobusEntity(CoordinatorEntity):
    """Base Nikobus entity tied to a module/channel."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: NikobusDataCoordinator,
        *,
        module_address: str | None = None,
        channel: int | None = None,
        name: str | None = None,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._module_address = module_address
        self._channel = channel

        if name is not None:
            self._attr_name = name

        if icon is not None:
            self._attr_icon = icon

        # IMPORTANT:
        # We do NOT set _attr_unique_id here to avoid breaking existing entities.
        # Each platform keeps its own unique_id format.

    @property
    def module_address(self) -> str | None:
        """Return Nikobus module address, if any."""
        return self._module_address

    @property
    def channel(self) -> int | None:
        """Return Nikobus channel number, if any."""
        return self._channel

    @property
    def available(self) -> bool:
        """Entity availability based on coordinator / connection state."""
        if hasattr(self.coordinator, "connected"):
            return bool(self.coordinator.connected)
        return super().available

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info so entities are grouped per module in the UI."""
        gateway_id = getattr(self.coordinator, "gateway_id", "nikobus_gateway")

        if self._module_address is None:
            return DeviceInfo(
                identifiers={(DOMAIN, gateway_id)},
                name="Nikobus Gateway",
                manufacturer=BRAND,
                model="Nikobus Interface",
            )

        # Device per module
        model = (
            self.coordinator.get_module_model(self._module_address)
            if hasattr(self.coordinator, "get_module_model")
            else "Nikobus module"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._module_address)},
            name=f"Nikobus module {self._module_address}",
            manufacturer=BRAND,
            model=model,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose useful debug attributes."""
        attrs: dict[str, Any] = {}

        if self._module_address is not None:
            attrs["module_address"] = self._module_address
        if self._channel is not None:
            attrs["channel"] = self._channel

        raw_state = None
        if hasattr(self.coordinator, "get_raw_channel_state"):
            raw_state = self.coordinator.get_raw_channel_state(
                self._module_address, self._channel
            )
        if raw_state is not None:
            attrs["raw_state"] = raw_state

        return attrs
