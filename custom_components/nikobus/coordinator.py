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
from .discovery import NikobusDiscovery

from .const import (
    CONF_CONNECTION_STRING,
    CONF_REFRESH_INTERVAL,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    DOMAIN,
)
from .exceptions import NikobusConnectionError, NikobusDataError

_LOGGER = logging.getLogger(__name__)
_MODULE_TYPES = ("switch_module", "dimmer_module", "roller_module")


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
        self._prior_gen3 = config_entry.data.get(CONF_PRIOR_GEN3, False)
        self._update_interval = self._get_update_interval()

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

    def _get_update_interval(self) -> timedelta | None:
        """Compute the update interval based on configuration."""
        if self._has_feedback_module or self._prior_gen3:
            return None
        return timedelta(seconds=self._refresh_interval)

    @property
    def discovery_running(self) -> bool:
        return self._discovery_running

    @property
    def discovery_module(self):
        return self._discovery_module

    @discovery_running.setter
    def discovery_running(self, value: bool) -> None:
        self._discovery_running = value

    @discovery_module.setter
    def discovery_module(self, value) -> None:
        self._discovery_module = value

    async def connect(self) -> None:
        """Connect to the Nikobus system."""
        try:
            await self.nikobus_connection.connect()
        except NikobusConnectionError as e:
            _LOGGER.error("Failed to connect to Nikobus: %s", e)
            raise
        else:
            try:
                # Load JSON configuration for modules, buttons, and scenes
                self.dict_module_data = await self.nikobus_config.load_json_data(
                    "nikobus_module_config.json", "module"
                )
                self.dict_button_data = await self.nikobus_config.load_json_data(
                    "nikobus_button_config.json", "button"
                ) or {"nikobus_button": {}}
                self.dict_scene_data = await self.nikobus_config.load_json_data(
                    "nikobus_scene_config.json", "scene"
                )

                # Initialize module state tracking dynamically based on channels
                for modules in self.dict_module_data.values():
                    for address, module_info in modules.items():
                        channels = module_info.get("channels", [])
                        self.nikobus_module_states[address] = bytearray(len(channels))

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
                _LOGGER.exception("Failed to initialize Nikobus components: %s", e)
                raise

    async def discover_devices(self, module_address) -> None:
        """Discover available module / button."""
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": "Nikobus discovery is in progress. Please wait...",
                "title": "Nikobus Discovery",
                "notification_id": "nikobus_discovery",
            },
            blocking=True,
        )

        self._discovery_running = True
        _LOGGER.debug("Starting device discovery from Nikobus")
        try:
            if module_address:
                self._discovery_module = True
                self.discovery_module_address = module_address
                await self.nikobus_discovery.query_module_inventory(module_address)
            else:
                self._discovery_module = False
                await self.nikobus_command.queue_command("#A")
        except Exception as e:
            _LOGGER.exception("Error during discovery: %s", e)
            raise
        finally:
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": "nikobus_discovery"},
                blocking=True,
            )

    async def _async_update_data(self):
        """Fetch the latest data from the Nikobus system."""
        try:
            if not self._discovery_running:
                _LOGGER.debug("Refreshing Nikobus data")
                return await self._refresh_nikobus_data()
        except NikobusDataError as e:
            _LOGGER.error("Error fetching Nikobus data: %s", e)
            raise UpdateFailed(f"Error fetching Nikobus data: {e}")

    async def _refresh_nikobus_data(self) -> bool:
        """Refresh data from all Nikobus modules."""
        for module_type in _MODULE_TYPES:
            if module_type in self.dict_module_data:
                await self._refresh_module_type(self.dict_module_data[module_type])
        return True

    async def _refresh_module_type(self, modules_dict) -> None:
        """Refresh data for a specific type of module."""
        for address, module_data in modules_dict.items():
            _LOGGER.debug("Refreshing data for module address: %s", address)
            channels = module_data.get("channels", [])
            expected_channels = len(channels)
            groups_to_query = (1,) if expected_channels <= 6 else (1, 2)
            group_states = []
            for group in groups_to_query:
                try:
                    group_state = (
                        await self.nikobus_command.get_output_state(address, group)
                        or ""
                    )
                    _LOGGER.debug(
                        "State for group %s: %s (Address: %s)",
                        group,
                        group_state,
                        address,
                    )
                    group_states.append(group_state)
                except Exception as e:
                    _LOGGER.error(
                        "Error retrieving state for address %s, group %s: %s",
                        address,
                        group,
                        e,
                    )
            state_hex = "".join(group_states)
            expected_hex_length = expected_channels * 2
            if len(state_hex) < expected_hex_length:
                state_hex = state_hex.ljust(expected_hex_length, "0")
                _LOGGER.debug(
                    "Padded state_hex for module %s to: %s", address, state_hex
                )
            try:
                self.nikobus_module_states[address] = bytearray.fromhex(state_hex)
                _LOGGER.debug(
                    "Updated module state for %s: %s",
                    address,
                    self.nikobus_module_states[address].hex(),
                )
            except ValueError:
                _LOGGER.error(
                    "Invalid hex state received for %s, setting default state.",
                    address,
                )
                self.nikobus_module_states[address] = bytearray(expected_channels)

    async def process_feedback_data(self, module_group, data) -> None:
        """Process feedback data from Nikobus."""
        try:
            module_address_raw = data[3:7]
            module_address = module_address_raw[2:] + module_address_raw[:2]
            module_type = self.get_module_type(module_address)
            module_state_raw = data[9:21]

            _LOGGER.debug(
                "Processing feedback data: module_type=%s, module_address=%s, "
                "group=%s, module_state=%s",
                module_type,
                module_address,
                module_group,
                module_state_raw,
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
                raise ValueError("Invalid module group: %s" % module_group)

            await self.async_event_handler(
                "nikobus_refreshed",
                {
                    "impacted_module_address": module_address,
                    "impacted_module_group": module_group,
                },
            )

        except Exception as e:
            _LOGGER.exception("Error processing feedback data: %s", e)

    def get_bytearray_state(self, address: str, channel: int) -> int:
        """Get the state of a specific channel, ensuring defaults if missing."""
        num_channels = self.get_module_channel_count(address)
        state = self.nikobus_module_states.get(address, bytearray(num_channels))
        if channel - 1 >= len(state) or channel - 1 < 0:
            _LOGGER.error(
                "Channel index %d out of range for module %s (max channels: %d)",
                channel,
                address,
                len(state),
            )
            return 0
        return state[channel - 1]

    def get_bytearray_group_state(self, address: str, group: int) -> bytearray:
        """Get the state of a specific group."""
        if address in self.nikobus_module_states:
            return (
                self.nikobus_module_states[address][:6]
                if int(group) == 1
                else self.nikobus_module_states[address][6:12]
            )
        _LOGGER.error(
            "Module address %s not found, returning empty bytearray.", address
        )
        return bytearray(6)

    def set_bytearray_state(self, address: str, channel: int, value: int) -> None:
        """Set the state of a specific channel safely."""
        if address in self.nikobus_module_states:
            self.nikobus_module_states[address][channel - 1] = value
        else:
            _LOGGER.warning("Module %s not found, creating new state array.", address)
            self.nikobus_module_states[address] = bytearray(12)
            self.nikobus_module_states[address][channel - 1] = value

    def set_bytearray_group_state(self, address: str, group: int, value: str) -> None:
        """Update the state of a specific group safely using the actual channel count."""
        group = int(group)
        num_channels = self.get_module_channel_count(address)
        if address not in self.nikobus_module_states:
            _LOGGER.warning("Module %s not found, creating new state array.", address)
            self.nikobus_module_states[address] = bytearray(num_channels)

        state = self.nikobus_module_states[address]
        byte_value = bytearray.fromhex(value)

        if group == 1:
            end_index = min(6, num_channels)
            state[0:end_index] = byte_value[:end_index]
        elif group == 2:
            if num_channels > 6:
                state[6:num_channels] = byte_value[: num_channels - 6]
            else:
                _LOGGER.error(
                    "Module %s has only %d channels; skipping group 2 update.",
                    address,
                    num_channels,
                )
                return
        _LOGGER.debug("Updated state for %s: %s", address, state.hex())

    async def async_event_handler(self, event, data) -> None:
        """Handle events with improved logging."""
        _LOGGER.debug("Handling event: %s with data: %s", event, data)
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
        self._prior_gen3 = entry.data.get(CONF_PRIOR_GEN3, False)
        self._update_interval = self._get_update_interval()
        self.update_interval = self._update_interval

        await self.connect()
        await self.async_refresh()

    async def _handle_ha_button_pressed(self, data) -> None:
        """Handle HA button press events."""
        address = data.get("address")
        operation_time = data.get("operation_time")
        _LOGGER.debug(
            "HA Button %s pressed with operation_time: %s",
            address,
            operation_time,
        )
        await self.nikobus_command.queue_command(f"#N{address}\r#E1")

    async def _handle_nikobus_refreshed(self, data) -> None:
        """Handle Nikobus refreshed events."""
        impacted_module_address = data.get("impacted_module_address")
        impacted_module_group = data.get("impacted_module_group")
        _LOGGER.debug(
            "Nikobus refreshed for module %s group %s",
            impacted_module_address,
            impacted_module_group,
        )

    def get_module_type(self, module_id: str) -> str:
        """Determine the module type based on the module ID."""
        for module_type, modules in self.dict_module_data.items():
            if module_id in modules:
                return module_type
        _LOGGER.error("Module ID %s not found in known module types", module_id)
        return "unknown"

    def get_module_channel_count(self, module_id: str) -> int:
        for modules in self.dict_module_data.values():
            if module_id in modules:
                module_data = modules[module_id]
                return len(module_data.get("channels", []))
        _LOGGER.error("Module ID %s not found in module configuration", module_id)
        return 0

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

    async def stop(self) -> None:
        """Stop the coordinator and its running tasks."""
        _LOGGER.debug("Stopping NikobusDataCoordinator")
        if self.nikobus_listener:
            try:
                await self.nikobus_listener.stop()
                _LOGGER.debug("Nikobus listener stopped.")
            except Exception as e:
                _LOGGER.error("Error stopping Nikobus listener: %s", e)
        if self.nikobus_command:
            try:
                await self.nikobus_command.stop()
                _LOGGER.debug("Nikobus command handler stopped.")
            except Exception as e:
                _LOGGER.error("Error stopping Nikobus command handler: %s", e)
        if self.nikobus_connection:
            try:
                await self.nikobus_connection.disconnect()
                _LOGGER.debug("Nikobus connection disconnected.")
            except Exception as e:
                _LOGGER.error("Error disconnecting Nikobus connection: %s", e)

    # DISCOVERY SPECIFICS

    def get_all_module_addresses(self):
        """Return a list of all module addresses from the module configuration."""
        return [
            address
            for modules in self.dict_module_data.values()
            for address in modules.keys()
        ]

    def get_button_channels(self, main_address: str):
        """Return the discovery channels for a given button discovered_info address."""
        buttons = self.dict_button_data.get("nikobus_button", {})
        return next(
            (
                info.get("channels")
                for button in buttons.values()
                for info in (button.get("discovered_info") or [])
                if isinstance(info, dict) and info.get("address") == main_address
            ),
            None,
        )

    def get_known_entity_unique_ids(self) -> set[str]:
        """Return the set of valid unique_ids for all Nikobus entities
        based on current JSON configuration."""

        known: set[str] = set()

        # -----------------------
        # 1) MODULE-BASED ENTITIES
        #    - dimmer_module  -> nikobus_light_{address}_{channel}
        #    - switch_module  -> nikobus_switch_{address}_{channel}
        #    - roller_module  -> nikobus_cover_{address}_{channel} unless use_as_switch
        # -----------------------
        for module_type, modules in self.dict_module_data.items():
            for address, module_data in modules.items():
                for index, ch_info in enumerate(module_data.get("channels", []), start=1):
                    desc = ch_info.get("description", "")
                    if desc.startswith("not_in_use"):
                        continue

                    if module_type == "dimmer_module":
                        known.add(f"{DOMAIN}_light_{address}_{index}")
                    elif module_type == "switch_module":
                        known.add(f"{DOMAIN}_switch_{address}_{index}")
                    elif module_type == "roller_module":
                        if ch_info.get("use_as_switch", False):
                            known.add(f"{DOMAIN}_switch_{address}_{index}")
                        else:
                            known.add(f"{DOMAIN}_cover_{address}_{index}")
                    else:
                        known.add(f"{DOMAIN}_{address}_{index}")

        # -----------------------
        # 2) Button sensors
        # using: nikobus_button_sensor_{address}
        # -----------------------
        for button in self.dict_button_data.get("nikobus_button", {}).values():
            addr = button.get("address")
            if addr:
                known.add(f"{DOMAIN}_button_sensor_{addr}")
                # -----------------------
                # 3) Push buttons
                # using: nikobus_push_button_{address}
                # -----------------------
                known.add(f"{DOMAIN}_push_button_{addr}")

        # -----------------------
        # 4) Scenes
        # using: nikobus_scene_{scene_id}
        # -----------------------
        scene_list = self.dict_scene_data.get("scene", [])
        for scene in scene_list:
            sid = scene.get("id")
            if sid:
                known.add(f"{DOMAIN}_scene_{sid}")

        return known
