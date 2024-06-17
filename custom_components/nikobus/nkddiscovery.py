"""Discovery for Nikobus"""

###
### PLACE HOLDER / NOT USED FOR NOW ###
###

import logging

COMMAND_GET_CONTROLLER = '#A'

RECEIVED_INVENTORY_ITEM = "$0510"

SWITCH_MODULE  = '000001000000'
COMPACT_SWITCH_MODULE = '000031000000'
SHUTTER_MODULE = '000002000000'
DIMMER_MODULE  = '000003000000'

PC_LOGIC_MODULE = '000008000000'
PC_LINK_MODULE  = '00000A000000'
FEEDBACK_MODULE = '000042000000'

class NikobusDiscovery:

    def __init__(self, nikobus_command_handler):
        self._nikobus_command = nikobus_command_handler

    async def get_controller_address(self) -> str:
        controller_address = await self.send_discovery_get_answer(command, address)
        _LOGGER.debug(f'Controller Addrress: {controller_address}')

