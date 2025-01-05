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

from .const import (
    DOMAIN,
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
)

from .exceptions import (
    NikobusConnectionError,
    NikobusDataError,
)

_LOGGER = logging.getLogger(__name__)


class NikobusDataCoordinator(DataUpdateCoordinator):
    """Coordinator for managing asynchronous updates and connections to the Nikobus system."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator with Home Assistant and configuration entry."""
        self.hass = hass
        self.api = None

        self.config_entry = config_entry
        self.connection_string = config_entry.options.get(
            CONF_CONNECTION_STRING, config_entry.data.get(CONF_CONNECTION_STRING)
        )
        self._refresh_interval = config_entry.options.get(
            CONF_REFRESH_INTERVAL, config_entry.data.get(CONF_REFRESH_INTERVAL, 120)
        )
        self._has_feedback_module = config_entry.options.get(
            CONF_HAS_FEEDBACK_MODULE,
            config_entry.data.get(CONF_HAS_FEEDBACK_MODULE, False),
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
        self._unsub_update_listener = None

        self.nikobus_connection = NikobusConnect(self.connection_string)
        self.nikobus_config = NikobusConfig(self.hass)

        self.dict_module_data = {}
        self.dict_button_data = {}
        self.dict_scene_data = {}
        self.nikobus_module_states = {}

        self.nikobus_actuator = None
        self.nikobus_listener = None

        self.nikobus_command_handler = None

    async def connect(self):
        """Connect to the Nikobus system."""
        try:
            await self.nikobus_connection.connect()
        except NikobusConnectionError as e:
            _LOGGER.error(f"Failed to connect to Nikobus: {e}")
            raise
        else:
            try:
                self.dict_module_data = await self.nikobus_config.load_json_data(
                    "nikobus_module_config.json", "module"
                )
                self.dict_button_data = await self.nikobus_config.load_json_data(
                    "nikobus_button_config.json", "button"
                )
                self.dict_scene_data = await self.nikobus_config.load_json_data(
                    "nikobus_scene_config.json", "scene"
                )

                for module_type, modules in self.dict_module_data.items():
                    for address, module_info in modules.items():
                        module_address = module_info["address"]
                        self.nikobus_module_states[module_address] = bytearray(12)

                self.nikobus_actuator = NikobusActuator(
                    self.hass,
                    self,
                    self.dict_button_data,
                    self.dict_module_data,
                    self.async_event_handler,
                )

                self.nikobus_listener = NikobusEventListener(
                    self.hass,
                    self.config_entry,
                    self.nikobus_actuator,
                    self.nikobus_connection,
                    self.process_feedback_data,
                )

                self.nikobus_command_handler = NikobusCommandHandler(
                    self.hass,
                    self,
                    self.nikobus_connection,
                    self.nikobus_listener,
                    self.nikobus_module_states,
                )

                self.api = NikobusAPI(self.hass, self)

                self.hass.data[DOMAIN] = {
                    "coordinator": self,
                    "api": self.api,
                    "actuator": self.nikobus_actuator,
                    "listener": self.nikobus_listener,
                    "command": self.nikobus_command_handler,
                }

                # Start command handler and event listener
                await self.nikobus_command_handler.start()
                await self.nikobus_listener.start()

                # Perform an initial data refresh
                await self._async_config_entry_first_refresh()
            except HomeAssistantError as e:
                _LOGGER.error("Failed to initialize Nikobus components: %s", e)
                raise

    async def _async_config_entry_first_refresh(self):
        """Handle the first data refresh and set up the update listener."""
        await self.async_refresh()
        self._unsub_update_listener = self.config_entry.add_update_listener(
            self.async_config_entry_updated
        )

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
        if "switch_module" in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data["switch_module"])

        if "dimmer_module" in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data["dimmer_module"])

        if "roller_module" in self.dict_module_data:
            await self._refresh_module_type(self.dict_module_data["roller_module"])

        return True

    async def _refresh_module_type(self, modules_dict):
        """Refresh data for a specific type of module."""
        for address, module_data in modules_dict.items():
            _LOGGER.debug(f"Refreshing data for module address: {address}")
            state = ""
            channel_count = len(module_data.get("channels", []))
            groups_to_query = [1] if channel_count <= 6 else [1, 2]

            for group in groups_to_query:
                group_state = (
                    await self.nikobus_command_handler.get_output_state(address, group)
                    or ""
                )
                _LOGGER.debug(
                    f"State for group {group}: {group_state} address : {address} ***"
                )
                state += group_state

            self.nikobus_module_states[address] = bytearray.fromhex(state)
            _LOGGER.debug(f"{self.nikobus_module_states[address]}")

    async def process_feedback_data(self, module_group, data):
        """Process feedback data from Nikobus."""
        module_address_raw = data[3:7]
        module_address = module_address_raw[2:] + module_address_raw[:2]
        module_type = self.get_module_type(module_address)
        module_address = module_address_raw[2:] + module_address_raw[:2]
        module_state_raw = data[9:21]

        try:
            _LOGGER.debug(
                f"Processing feedback module data: module_type={module_type}, module_address={module_address}, group={module_group}, module_state={module_state_raw}"
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
                "nikobus_refreshed", {"impacted_module_address": module_address}
            )

        except Exception as e:
            _LOGGER.error(f"Error processing feedback data: {e}", exc_info=True)

    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Get the state of a specific channel."""
        return self.nikobus_module_states.get(address, bytearray())[channel - 1]

    def get_bytearray_group_state(self, address: str, group: int) -> int:
        """Get the state of a specific group."""
        if int(group) == 1:
            return self.nikobus_module_states[address][:6]
        elif int(group) == 2:
            return self.nikobus_module_states[address][6:12]

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of a specific channel."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.error(f"Address {address} not found in Nikobus module")

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update the state of a specific group."""
        byte_value = bytearray.fromhex(value)
        if address in self.nikobus_module_states:
            if int(group) == 1:
                self.nikobus_module_states[address][:6] = byte_value
            elif int(group) == 2:
                self.nikobus_module_states[address][6:12] = byte_value
            _LOGGER.debug(
                f"New value set for array {self.nikobus_module_states[address]}."
            )
        else:
            _LOGGER.error(f"Address {address} not found in Nikobus module")

    async def async_event_handler(self, event, data):
        """Handle events."""
        if event == "ha_button_pressed":
            await self._handle_ha_button_pressed(data)
        elif event == "nikobus_button_pressed":
            await self._handle_nikobus_button_pressed(data)
        elif event == "nikobus_refreshed":
            await self._handle_nikobus_refreshed(data)
        self.async_update_listeners()

    async def _handle_ha_button_pressed(self, data):
        """Handle HA button press events."""
        address = data.get("address")
        operation_time = data.get("operation_time")
        _LOGGER.debug(
            f"HA Button {address} pressed with operation_time: {operation_time}"
        )
        await self.nikobus_command_handler.queue_command(f"#N{address}\r#E1")

    async def _handle_nikobus_button_pressed(self, data):
        """Handle Nikobus button press events."""
        address = data.get("address")
        operation_time = data.get("operation_time")
        impacted_module_address = data.get("impacted_module_address")
        _LOGGER.debug(
            f"Nikobus button pressed at address {address}, operation_time: {operation_time}, impacted_module_address: {impacted_module_address}"
        )
        self.hass.bus.async_fire(
            "nikobus_button_pressed",
            {
                "address": address,
                "operation_time": operation_time,
                "impacted_module_address": impacted_module_address,
            },
        )

    async def _handle_nikobus_refreshed(self, data):
        """Handle Nikobus refreshed events."""
        impacted_module_address = data.get("impacted_module_address")
        _LOGGER.debug(
            f"Nikobus has been refreshed for module {impacted_module_address}"
        )

    async def async_config_entry_updated(self, entry: ConfigEntry, *args) -> None:
        """Handle updates to the configuration entry."""
        connection_string = entry.options.get(
            CONF_CONNECTION_STRING, self.connection_string
        )
        refresh_interval = entry.options.get(CONF_REFRESH_INTERVAL, 120)
        has_feedback_module = entry.options.get(CONF_HAS_FEEDBACK_MODULE, False)

        connection_changed = connection_string != self.connection_string
        refresh_interval_changed = refresh_interval != self._refresh_interval
        feedback_module_changed = has_feedback_module != self._has_feedback_module

        if connection_changed or refresh_interval_changed or feedback_module_changed:
            self.connection_string = connection_string
            self._refresh_interval = refresh_interval
            self._has_feedback_module = has_feedback_module

            await self._async_update_coordinator_settings()

            if connection_changed:
                await self.connect()
                title = f"Nikobus - {connection_string}"
                self.hass.config_entries.async_update_entry(entry, title=title)

            _LOGGER.info(
                f"Configuration updated: connection_string={self.connection_string}, "
                f"refresh_interval={self._refresh_interval}, has_feedback_module={self._has_feedback_module}"
            )

    async def _async_update_coordinator_settings(self):
        """Update the coordinator's update method and interval."""
        self._update_interval = (
            None
            if self._has_feedback_module
            else timedelta(seconds=self._refresh_interval)
        )
        await self._async_refresh()

    def get_module_type(self, module_id: str) -> str:
        """Determine the module type based on the module ID."""
        if "switch_module" in self.dict_module_data:
            if module_id in self.dict_module_data["switch_module"]:
                return "switch"
        if "dimmer_module" in self.dict_module_data:
            if module_id in self.dict_module_data["dimmer_module"]:
                return "dimmer"
        if "roller_module" in self.dict_module_data:
            if module_id in self.dict_module_data["roller_module"]:
                return "cover"
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
