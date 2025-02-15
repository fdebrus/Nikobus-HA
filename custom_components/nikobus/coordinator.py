"""Coordinator for Nikobus integration."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from .nkbAPI import NikobusAPI
from .nkbconnect import NikobusConnect
from .nkbconfig import NikobusConfig
from .nkblistener import NikobusEventListener
from .nkbcommand import NikobusCommandHandler
from .nkbactuator import NikobusActuator
from .nkbdiscovery import NikobusDiscovery

from .const import (
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)
from .exceptions import NikobusConnectionError, NikobusDataError

_LOGGER = logging.getLogger(__name__)


class NikobusDataCoordinator(DataUpdateCoordinator):
    """Coordinator for managing asynchronous updates and connections to the Nikobus system."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator with Home Assistant and configuration entry."""
        self.hass = hass
        self.api = None

        self.config_entry = config_entry
        self.connection_string = config_entry.data.get(CONF_CONNECTION_STRING)
        self._refresh_interval = config_entry.data.get(CONF_REFRESH_INTERVAL, 120)
        self._has_feedback_module = config_entry.data.get(
            CONF_HAS_FEEDBACK_MODULE, False
        )

        # Set update_interval to None if feedback module is present, disabling periodic updates
        self._update_interval = (
            None
            if self._has_feedback_module
            else timedelta(seconds=self._refresh_interval)
        )

        super().__init__(
            self.hass,
            _LOGGER,
            name="Nikobus",
            update_method=self._async_update_data,
            update_interval=self._update_interval,
        )

        self.nikobus_connection = NikobusConnect(self.connection_string)
        self.nikobus_config = NikobusConfig(self.hass)

        self.dict_module_data = {}
        self.dict_button_data = {}
        self.dict_scene_data = {}
        self.nikobus_module_states = {}

        self.nikobus_actuator = None
        self.nikobus_listener = None
        self.nikobus_command = None
        self.nikobus_discovery = None
        self._discovery_running = False
        self._discovery_module = None
        self.discovery_module_address = None

    @property
    def discovery_running(self):
        """Return whether device discovery is in progress."""
        return self._discovery_running

    @property
    def discovery_module(self):
        """Return whether device discovery is in progress from a module not PCLink."""
        return self._discovery_module

    @discovery_running.setter
    def discovery_running(self, value):
        self._discovery_running = value

    async def connect(self):
        """Connect to the Nikobus system."""
        try:
            await self.nikobus_connection.connect()
        except NikobusConnectionError as e:
            _LOGGER.error(f"Failed to connect to Nikobus: {e}")
            raise
        else:
            try:
                # Load JSON configuration for modules, buttons, and scenes
                self.dict_module_data = await self.nikobus_config.load_json_data(
                    "nikobus_module_config.json", "module"
                )
                # Load button configuration; default to {"nikobus_button": {}} if file not found.
                self.dict_button_data = (
                    await self.nikobus_config.load_json_data("nikobus_button_config.json", "button")
                    or {"nikobus_button": {}}
                )
                self.dict_scene_data = await self.nikobus_config.load_json_data(
                    "nikobus_scene_config.json", "scene"
                )

                # Initialize module state tracking
                for modules in self.dict_module_data.values():
                    for address, module_info in modules.items():
                        self.nikobus_module_states[address] = bytearray(12)

                # Instantiate main Nikobus components
                self.nikobus_actuator = NikobusActuator(
                    self.hass, self, self.dict_button_data, self.dict_module_data
                )

                self.nikobus_discovery = NikobusDiscovery(self.hass, self)

                self.nikobus_listener = NikobusEventListener(
                    self.hass,
                    self.config_entry,
                    self,
                    self.nikobus_actuator,
                    self.nikobus_connection,
                    self.nikobus_discovery,
                    self.process_feedback_data,
                )

                self.nikobus_command = NikobusCommandHandler(
                    self.hass,
                    self,
                    self.nikobus_connection,
                    self.nikobus_listener,
                    self.nikobus_module_states,
                )

                # Expose API to Home Assistant
                self.api = NikobusAPI(self.hass, self)

                # Start event listener and command handler
                await self.nikobus_command.start()
                await self.nikobus_listener.start()

                # Perform an initial data refresh
                await self.async_refresh()
            except HomeAssistantError as e:
                _LOGGER.error("Failed to initialize Nikobus components: %s", e)
                raise

    async def discover_devices(self, module_address):
        """Discover available module inventory."""
        if self._discovery_running:
            _LOGGER.warning("Device discovery is already running.")
            return
        self._discovery_running = True
        _LOGGER.debug("Starting device discovery from Nikobus")
        if module_address:
            self._discovery_module = True
            self.discovery_module_address = module_address
            await self.nikobus_discovery.query_module_inventory(module_address)
        else:
            self._discovery_module = False
            # Get the PCLink address from Nikobus and read its data
            await self.nikobus_command.queue_command("#A")

    async def _async_update_data(self):
        """Fetch the latest data from the Nikobus system."""
        try:
            _LOGGER.debug("Refreshing Nikobus data")
            return await self._refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching Nikobus data: {e}")

    async def _refresh_nikobus_data(self) -> bool:
        """Refresh data from all Nikobus modules."""
        for module_type in ["switch_module", "dimmer_module", "roller_module"]:
            if module_type in self.dict_module_data:
                await self._refresh_module_type(self.dict_module_data[module_type])
        return True

    async def _refresh_module_type(self, modules_dict):
        """Refresh data for a specific type of module."""
        for address, module_data in modules_dict.items():
            _LOGGER.debug(f"Refreshing data for module address: {address}")
            state = ""

            channel_count = len(module_data.get("channels", []))
            groups_to_query = [1] if channel_count <= 6 else [1, 2]

            for group in groups_to_query:
                try:
                    group_state = (
                        await self.nikobus_command.get_output_state(address, group)
                        or ""
                    )
                    _LOGGER.debug(
                        f"State for group {group}: {group_state} (Address: {address})"
                    )
                    state += group_state
                except Exception as e:
                    _LOGGER.error(
                        f"Error retrieving state for address {address}, group {group}: {e}"
                    )

            try:
                self.nikobus_module_states[address] = bytearray.fromhex(state)
                _LOGGER.debug(
                    f"Updated module state for {address}: {self.nikobus_module_states[address]}"
                )
            except ValueError:
                _LOGGER.error(
                    f"Invalid hex state received for {address}, setting default state."
                )
                self.nikobus_module_states[address] = bytearray(
                    12
                )  # Default to all zeros

    async def process_feedback_data(self, module_group, data):
        """Process feedback data from Nikobus."""
        try:
            module_address_raw = data[3:7]
            module_address = module_address_raw[2:] + module_address_raw[:2]
            module_type = self.get_module_type(module_address)
            module_state_raw = data[9:21]

            _LOGGER.debug(
                f"Processing feedback data: module_type={module_type}, module_address={module_address}, group={module_group}, module_state={module_state_raw}"
            )

            if module_address not in self.nikobus_module_states:
                self.nikobus_module_states[module_address] = bytearray(12)

            if module_group == 1:
                self.nikobus_module_states[module_address][:6] = bytearray.fromhex(
                    module_state_raw
                )
            elif module_group == 2:
                self.nikobus_module_states[module_address][6:] = bytearray.fromhex(
                    module_state_raw
                )
            else:
                raise ValueError(f"Invalid module group: {module_group}")

            await self.async_event_handler(
                "nikobus_refreshed",
                {
                    "impacted_module_address": module_address,
                    "impacted_module_group": module_group,
                },
            )

        except Exception as e:
            _LOGGER.error(f"Error processing feedback data: {e}", exc_info=True)

    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Get the state of a specific channel, ensuring defaults if missing."""
        return self.nikobus_module_states.get(address, bytearray(12))[channel - 1]

    def get_bytearray_group_state(self, address: str, group: int) -> int:
        """Get the state of a specific group."""
        if address in self.nikobus_module_states:
            return (
                self.nikobus_module_states[address][:6]
                if int(group) == 1
                else self.nikobus_module_states[address][6:12]
            )
        _LOGGER.error(f"Module address {address} not found, returning empty bytearray.")
        return bytearray(6)

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of a specific channel safely."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.warning(f"Module {address} not found, creating new state array.")
            self.nikobus_module_states[address] = bytearray(12)
            self.nikobus_module_states[address][channel - 1] = value

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update the state of a specific group safely."""
        byte_value = bytearray.fromhex(value)
        if address in self.nikobus_module_states:
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = byte_value
            _LOGGER.debug(
                f"Updated state for {address}: {self.nikobus_module_states[address]}"
            )
        else:
            _LOGGER.warning(f"Module {address} not found, creating new state array.")
            self.nikobus_module_states[address] = bytearray(12)
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = byte_value

    async def async_event_handler(self, event, data):
        """Handle events with improved logging."""
        _LOGGER.debug(f"Handling event: {event} with data: {data}")
        if event == "ha_button_pressed":
            await self._handle_ha_button_pressed(data)
        elif event == "nikobus_refreshed":
            await self._handle_nikobus_refreshed(data)
        self.async_update_listeners()

    async def async_reconfigure(self, entry: ConfigEntry) -> None:
        """Handle configuration changes via reconfigure."""
        _LOGGER.info("Reconfiguring Nikobus integration.")

        self.connection_string = entry.data.get(CONF_CONNECTION_STRING)
        self._refresh_interval = entry.data.get(CONF_REFRESH_INTERVAL, 120)
        self._has_feedback_module = entry.data.get(CONF_HAS_FEEDBACK_MODULE, False)

        self._update_interval = (
            None
            if self._has_feedback_module
            else timedelta(seconds=self._refresh_interval)
        )

        await self.connect()
        await self.async_refresh()

    async def _handle_ha_button_pressed(self, data):
        """Handle HA button press events."""
        address = data.get("address")
        operation_time = data.get("operation_time")
        _LOGGER.debug(
            f"HA Button {address} pressed with operation_time: {operation_time}"
        )
        await self.nikobus_command.queue_command(f"#N{address}\r#E1")

    async def _handle_nikobus_refreshed(self, data):
        """Handle Nikobus refreshed events."""
        # Place holder, no need to process anything here the refresh of data is managed in process_feedback_data()
        impacted_module_address = data.get("impacted_module_address")
        impacted_module_group = data.get("impacted_module_group")
        _LOGGER.debug(
            f"Nikobus refreshed for module {impacted_module_address} group {impacted_module_group}"
        )

    def get_module_type(self, module_id: str) -> str:
        """Determine the module type based on the module ID."""
        for module_type, modules in self.dict_module_data.items():
            if module_id in modules:
                return module_type
        _LOGGER.error(f"Module ID {module_id} not found in known module types")
        return "unknown"

    def get_light_state(self, address: str, channel: int) -> bool:
        """Get the state of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel) != 0x00

    def get_switch_state(self, address: str, channel: int) -> bool:
        """Get the state of a switch based on its address and channel."""
        return self.get_bytearray_state(address, channel) == 0xFF

    def get_light_brightness(self, address: str, channel: int) -> int:
        """Get the brightness of a light based on its address and channel."""
        return self.get_bytearray_state(address, channel)

    def get_cover_state(self, address: str, channel: int) -> int:
        """Get the state of a cover based on its address and channel."""
        return self.get_bytearray_state(address, channel)
