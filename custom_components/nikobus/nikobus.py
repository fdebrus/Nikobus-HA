import logging
import select
import asyncio
import re
import os
import json
import textwrap
from pathlib import Path
import aiofiles

from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect

from .helpers import (
    int_to_hex, 
    hex_to_int, 
    int_to_dec, 
    dec_to_int, 
    calc_crc1, 
    calc_crc2, 
    append_crc1, 
    append_crc2, 
    make_pc_link_command, 
    calculate_group_output_number, 
    calculate_group_number
)

_LOGGER = logging.getLogger(__name__)

class Nikobus:
    def __init__(self, hass, host, port):
        self._hass = hass
        self._host = host
        self._port = port
        self._response_queue = asyncio.Queue()
        self._event_listener_task = None
        self._nikobus_reader = None
        self._nikobus_writer = None
        self._answer = None
        self.json_config_data = {}
        self.json_state_data = {}
        self.json_button_data = {}
        self._nikobus_writer_lock = asyncio.Lock()

    @classmethod
    async def create(cls, hass, host: str, port: str):
        # Instantiate the class with the provided host and port arguments
        instance = cls(hass, host, port)
        
        # Await the connection establishment
        await instance.connect()
        
        # Return the instantiated and connected instance
        return instance

    async def load_json_config_data(self):
        # Get the path to the JSON config file
        config_file_path = self._hass.config.path("nikobus_config.json")
    
        # Read the file asynchronously
        async with aiofiles.open(config_file_path, mode='r') as file:
            # Load JSON data
            self.json_config_data = json.loads(await file.read())

    async def load_json_button_data(self):
        # Get the path to the JSON config file
        config_file_path = self._hass.config.path("nikobus_button_config.json")
    
        # Read the file asynchronously
        async with aiofiles.open(config_file_path, mode='r') as file:
            # Load JSON data
            self.json_button_data = json.loads(await file.read())

    async def write_json_button_data(self):
        # Define the path to the JSON button config file
        button_config_file_path = self._hass.config.path("nikobus_button_config.json")
    
        # Write the updated data asynchronously
        async with aiofiles.open(button_config_file_path, mode='w') as file:
            await file.write(json.dumps(self.json_button_data, indent=4))

    async def convert_from_openhab(self):
        openhab_config_file_path = self._hass.config.path("org.openhab.core.thing.Thing.json")
        async with aiofiles.open(openhab_config_file_path, mode='r') as file:
            self.json_openhab_button_data = json.loads(await file.read())
        for key, value in self.json_openhab_button_data.items():
            if "nikobus:push-button" in key:
                new_button = {
                    "description": value["value"]["label"],
                    "address": value["value"]["configuration"]["address"],
                    "impacted_module": [{"address": value["value"]["configuration"].get("impactedModules", "N/A"), "group": ""}]
                }
                self.json_button_data.setdefault("nikobus_button", []).append(new_button)
        await self.write_json_button_data()

    async def connect(self):
        _LOGGER.debug("----- Nikobus.connect() enter -----")
        try:
            # Attempt to establish a connection
            self._nikobus_reader, self._nikobus_writer = await asyncio.open_connection(self._host, self._port)
            _LOGGER.debug("Connection established, starting event listener...")
            self._event_listener_task = asyncio.create_task(self.listen_for_events())
        except OSError as err:
            # Handle connection failure
            _LOGGER.error(f"Unable to connect to {self._host} on port {self._port}: {err}")
            return
    
        # Define commands to be sent after connection
        commands = ["++++\r", "ATH0\r", "ATZ\r", "$10110000B8CF9D\r", "#L0\r", "#E0\r", "#L0\r", "#E1\r"]
    
        # Send each command
        for command in commands:
            try:
                self._nikobus_writer.write(command.encode())
                await self._nikobus_writer.drain()
            except OSError as err:
                # Handle payload sending failure
                _LOGGER.error(f"Unable to send payload {command!r} to {self._host} on port {self._port}: {err}")
                return
        try:
            # Wait for a response from the queue with a timeout
            raw_response = await asyncio.wait_for(self._response_queue.get(), timeout=3)
            _LOGGER.debug(f"Connected with {raw_response}")
        except asyncio.TimeoutError:
            # Handle timeout waiting for a response
            _LOGGER.warning(f"Timeout (3 second(s)) waiting for a response after {self._host} on port {self._port}")

    async def listen_for_events(self):
        _LOGGER.warning("Event Listener started")
        delimiter = b'\r'
        try:
            while True:
                try:
                    #data = await asyncio.wait_for(self._nikobus_reader.readuntil(delimiter), timeout=5)
                    data = await asyncio.wait_for(self._nikobus_reader.read(64), timeout=5)
                    if not data:
                        _LOGGER.warning("Nikobus connection closed")
                        break
                    message = data.decode('utf-8').strip()
                    await self.handle_message(message)
                except asyncio.TimeoutError:
                    _LOGGER.debug("Read operation timed out. Waiting for next data...")
        except asyncio.CancelledError:
            _LOGGER.info("Event listener was cancelled.")
        except Exception as e:
            _LOGGER.error("Error in event listener: %s", str(e), exc_info=True)

    async def handle_message(self, message):
        _button_command_prefix = '#N'  # The prefix of a button
        if message.startswith(_button_command_prefix):
            address = message[2:8] 
            await self.button_discovery(address)
        else:
            _LOGGER.info(f"Posting message: {message}")
            await self._response_queue.put(message)

    async def refresh_nikobus_data(self):
        # Load JSON configuration data
        await self.load_json_config_data()
        await self.load_json_button_data()
    
        # Initialize an empty dictionary to store the result
        result_dict = {}
    
        # Iterate over module types
        for module_type in self.json_config_data:
            # Iterate over entries in the current module type
            for entry in self.json_config_data.get(module_type, []):
                # Get the actual address from the entry
                actual_address = entry.get("address")
            
                # Log a debug message indicating refresh for the current address
                _LOGGER.debug('*** Refreshing data for module address %s ***', actual_address)
            
                # Get the output state for group 1
                state_group = await self.get_output_state(address=actual_address, group=1)
                _LOGGER.debug("state_group: %s", state_group)
            
                # If the number of channels is greater than 6, get the output state for group 2 as well
                if len(entry.get('channels', [])) > 6:
                    state_group += await self.get_output_state(address=actual_address, group=2)
                    _LOGGER.debug("state_group2: %s", state_group)
                
                # Split the state group into a dictionary with index as keys and items as values
                state_group_array = {index: item for index, item in enumerate(textwrap.wrap(state_group, width=2))}
            
                # Store the state group array in the result dictionary with the actual address as key
                result_dict[actual_address] = state_group_array
                
        # Update the JSON state data attribute with the result dictionary
        self.json_state_data = result_dict
    
        # Log a debug message indicating the JSON state data
        _LOGGER.debug("json: %s", self.json_state_data)
    
        # Return True to indicate successful refresh
        return True
    
    async def button_discovery(self, address):
        _LOGGER.debug(f"Found a button at {address}")

        # Flag to check if the button has been handled
        button_handled = False

        # Iterate over the button configurations
        for button in self.json_button_data['nikobus_button']:
            if button['address'] == address:
                # Button is configured, handle the press and send event to HA
                self._hass.bus.async_fire('nikobus_button_pressed', {'address': address})
                for module in button['impacted_module']:
                    impacted_module_address = module['address']
                    impacted_group = module['group']
                    if 'command' in module:
                        cover_command = module['command']
                        cover_button = True
                    # Ensure both impacted module address and group are specified
                    if impacted_module_address and impacted_group:
                        try:
                            if cover_button:
                                await self.button_press_cover(impacted_module_address, impacted_group, cover_command)
                                cover_button = False
                            else:
                                await self.get_output_state(impacted_module_address, impacted_group)
                            _LOGGER.debug(f"Handled button press for module {impacted_module_address} in group {impacted_group}.")
                        except Exception as e:
                            _LOGGER.error(f"Error handling button press for address {address}: {e}")
                button_handled = True
                break

        # If no existing configuration matches the button press, add a new configuration
        if not button_handled:
            new_button = {
                "description": f"Nikobus Button #N{address}",
                "address": address,
                "impacted_module": [{"address": "", "group": ""}]  # Placeholder for new configuration
            }
            _LOGGER.warning(f"No configuration found for button with address {address}. Adding new configuration.")
            self.json_button_data["nikobus_button"].append(new_button)
            await self.write_json_button_data()
            # await self.convert_from_openhab()
            _LOGGER.debug("New button configuration added: %s", new_button)

    def button_press_cover(self, address, impacted_group, cover_command):
        """Handle button press from Nikobus system for cover"""
        async_dispatcher_send(self._hass, f"nikobus_cover_update_{address}{impacted_group}", {'command': cover_command})

    async def send_command(self, command):
        _LOGGER.debug('----- Nikobus.send_command() enter -----')
        _LOGGER.debug(f'command = {command.encode()}')
        try:
            # Acquire the lock to ensure exclusive access to _nikobus_writer
            async with self._nikobus_writer_lock:
                # Write the encoded command and wait for the writer to drain
                self._nikobus_writer.write(command.encode() + b'\r')
                await self._nikobus_writer.drain()
        except Exception as err:
            # Log an error message if any exception occurs during the process
            _LOGGER.error('Error occurred while sending command: %s', err)

    async def send_command_get_answer(self, command):
        _LOGGER.debug('----- Nikobus.send_command_get_answer() enter -----')
        _LOGGER.debug(f'command = {command}')
        _wait_command_ack = '$05' + command[3:5]
        try:
            async with self._nikobus_writer_lock:
                self._nikobus_writer.write(command.encode() + b'\r')
                await self._nikobus_writer.drain()
            ack_found = False
            while True:
                try:
                    # Wait for a response from the queue
                    message = await asyncio.wait_for(self._response_queue.get(), timeout=10)
                    if _wait_command_ack in message:
                        _LOGGER.debug(f"Found ACK: {_wait_command_ack} in message: {message}")
                        ack_found = True
                    elif ack_found:
                        # This message is the first one received after the ACK
                        _answer = message[9:21]
                        _LOGGER.debug(f"Response message: {message}")
                        _LOGGER.debug(f"_answer: {_answer}")
                        return _answer
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout waiting for command response")
                    return None
        except Exception as e:
            _LOGGER.error(f"Error during command execution: {e}")
            return None

    async def get_output_state(self, address, group):
        _LOGGER.debug('----- NikobusApi.get_output_state() enter -----')
        _LOGGER.debug(f'address = {address}, group = {group}')
        if int(group) == 1:
            cmd = make_pc_link_command(0x12, address)
        elif int(group) == 2:
            cmd = make_pc_link_command(0x17, address)
        else:
            raise ValueError("Invalid group number")
        return await self.send_command_get_answer(cmd)

    async def set_output_state(self, address, group_number, value):
        _LOGGER.debug('----- NikobusApi.setOutputState() enter -----')
        _LOGGER.debug(f'address = {address}, group = {group_number}, value = {value}')
        if int(group_number) == 1:
            cmd = make_pc_link_command(0x15, address, value + 'FF')
        elif int(group_number) == 2:
            cmd = make_pc_link_command(0x16, address, value + 'FF')
        else:
            raise ValueError("Invalid group number")
        _LOGGER.debug('SET OUTPUT STATE command %s',cmd)
        await self.send_command(cmd)

    async def set_value_at_address(self, address, channel):
        channel += 1
        group_number = calculate_group_number(channel)
        group_output_number = calculate_group_output_number(channel)
        values = self.json_state_data[address]
        _LOGGER.debug('JSON %s', self.json_state_data)
        _LOGGER.debug('JSON ADDRESS %s', self.json_state_data[address])
        if group_number == 1:
            new_value = ''.join(values[i] for i in range(6))
        elif group_number == 2:
            new_value = ''.join(values[i] for i in range(6, 12))
        _LOGGER.debug('Setting value %s for %s', new_value, address)
        await self.set_output_state(address, group_number, new_value)

    async def set_value_at_address_shutter(self, address, channel, value):
        group_number = 1
        original_string = '000000000000'
        new_value = original_string[:channel*2] + value + original_string[channel*2:-2]
        _LOGGER.debug('Shutters - Setting value %s for %s', new_value, address)
        await self.set_output_state(address, group_number, new_value)

#### SWITCHES
    def get_switch_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        if _state == "FF":
            return True
        else:
            return False

    async def turn_on_switch(self, address, channel):
        _LOGGER.debug('CHANNEL %s', channel)
        self.json_state_data.setdefault(address, {})[channel] = 'FF'
        await self.set_value_at_address(address, channel)

    async def turn_off_switch(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)
#####

#### DIMMERS
    def get_light_state(self, address, channel):
        _state = self.json_state_data.get(address, {}).get(channel)
        _LOGGER.debug("get_light_state: %s %s %s",address, channel, _state)
        if _state == "00":
            return False
        else:
            return True
    
    def get_light_brightness(self, address, channel):
        return int(self.json_state_data.get(address, {}).get(channel),16)

    async def turn_on_light(self, address, channel, brightness):
        self.json_state_data.setdefault(address, {})[channel] = format(brightness, '02X')
        await self.set_value_at_address(address, channel)

    async def turn_off_light(self, address, channel):
        self.json_state_data.setdefault(address, {})[channel] = '00'
        await self.set_value_at_address(address, channel)
#####

#### COVERS
    async def stop_cover(self, address, channel) -> None:
        """Stop the cover."""
        await self.set_value_at_address_shutter(address, channel, '00')

    async def open_cover(self, address, channel) -> None:
        """Open the cover."""
        await self.set_value_at_address_shutter(address, channel, '01')

    async def close_cover(self, address, channel) -> None:
        """Close the cover."""
        await self.set_value_at_address_shutter(address, channel, '02')
#####

#### BUTTONS
    async def send_button_press(self, address) -> None:
        start_of_transmission = '#N'
        end_of_transmission = "\r#E1";
        await self.send_command(start_of_transmission + address + end_of_transmission)
#### 
