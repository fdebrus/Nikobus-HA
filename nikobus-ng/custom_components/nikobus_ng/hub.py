"""The connection to the bus and the state of every output.

One hub per config entry. It owns the library's objects, keeps the
output state the modules report, tells the entities of a module when
that state changed, and is the only place a frame is written from.

Entities hold no state of their own. A set-output puts the new value in
the buffer when it is queued, so an entity that reads the buffer shows
the requested state at once; a write the module does not acknowledge is
followed by a read, which puts the truth back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from nikobus_connect import (
    NikobusAPI,
    NikobusCommandHandler,
    NikobusConnect,
    NikobusConnectionError,
    NikobusError,
    NikobusEventListener,
)
from nikobus_connect.const import DEVICE_ADDRESS_INVENTORY, DEVICE_INVENTORY_ANSWER
from nikobus_connect.discovery import InventoryQueryType, NikobusDiscovery

from .const import (
    CLOCK_READ_INTERVAL,
    CONF_CONNECTION,
    CONF_FAN_OUTPUTS,
    CONF_FEEDBACK_MODULE,
    CONF_LED_KEYS,
    CONF_LIGHT_OUTPUTS,
    CONF_MODULE_TYPES,
    CONF_OFF_KEY,
    CONF_OLD_PC_LINK,
    CONF_ON_KEY,
    CONF_PRESS_REPEAT,
    CONF_SHUTTERS,
    COVER_OPENING,
    COVER_STOPPED,
    DEFAULT_PRESS_REPEAT,
    DIMMER_MODULE,
    DOMAIN,
    FEEDBACK_PORT_ANSWERS,
    FEEDBACK_SAFETY_POLL,
    ISSUE_FEEDBACK_PORT,
    ISSUE_NO_DEVICE,
    ISSUE_NOT_ANSWERING,
    POLL_INTERVAL,
    RECONNECT_DELAY_INITIAL,
    RECONNECT_DELAY_MAX,
    RELEASE_READ_DELAY,
    REPROGRAMMED_CHECK_DELAY,
    REPROGRAMMED_CHECK_INTERVAL,
    SILENT_READS,
    SWITCH_MODULE,
)
from .discovery import DiscoveryRunner
from .inventory import Inventory
from .maintenance import Maintenance
from .presses import PressHandler

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class NikobusHub:
    """The bus, as the rest of the integration sees it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.inventory = Inventory(hass, entry.entry_id, entry.options.get(CONF_MODULE_TYPES))
        self.presses = PressHandler(self)
        self.discovery = DiscoveryRunner(self)
        self.maintenance = Maintenance(self)
        self.feedback_module: bool = entry.options.get(
            CONF_FEEDBACK_MODULE, entry.data.get(CONF_FEEDBACK_MODULE, False)
        )
        #: A PC-Link older than generation 3, which answers no state read.
        self.old_pc_link: bool = entry.options.get(CONF_OLD_PC_LINK, False)
        self.light_outputs: frozenset[str] = frozenset(entry.options.get(CONF_LIGHT_OUTPUTS, ()))
        self.fan_outputs: frozenset[str] = frozenset(entry.options.get(CONF_FAN_OUTPUTS, ()))
        self.shutters: dict[str, dict[str, Any]] = dict(entry.options.get(CONF_SHUTTERS, {}))
        self.led_keys: dict[str, dict[str, str]] = dict(entry.options.get(CONF_LED_KEYS, {}))
        self.connected = False
        #: The gateway's device, which every other device hangs under.
        self.gateway_device_id: str = ""

        self.connection: NikobusConnect
        self.listener: NikobusEventListener
        self.command: NikobusCommandHandler
        self.api: NikobusAPI
        self.engine: NikobusDiscovery

        self._states: dict[str, bytearray] = {}
        self._heard: dict[tuple[str, int], float] = {}
        self._silent: dict[str, int] = defaultdict(int)
        self._feedback_port_reported = False
        self._module_listeners: dict[str, list[CALLBACK_TYPE]] = defaultdict(list)
        self._status_listeners: list[CALLBACK_TYPE] = []
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopping = False

        # What the library's discovery reads and writes on its host
        # (nikobus_connect.CoordinatorProtocol).
        self.discovery_running = False
        self.discovery_module: Any = False
        self.discovery_module_address: str | None = None
        self.inventory_query_type: InventoryQueryType | None = None
        self.nikobus_command: NikobusCommandHandler | None = None

    # --- the library's host protocol -------------------------------------------

    @property
    def dict_module_data(self) -> dict[str, Any]:
        """The module store by type, for the scan planner."""
        return self.inventory.grouped

    def get_module_type(self, module_id: str) -> str | None:
        """The type of a module address."""
        return self.inventory.module_type(module_id)

    def get_module_channel_count(self, module_id: str) -> int:
        """The number of outputs of a module address."""
        return self.inventory.channel_count(module_id)

    def get_button_channels(self, button_address: str) -> int | None:
        """The number of keys of a plate address."""
        return self.inventory.plate_channels(button_address)

    # --- lifecycle ---------------------------------------------------------------

    async def async_start(self) -> None:
        """Connect and start listening. Raises ``ConfigEntryNotReady``."""
        await self.inventory.async_load()
        self.connection = NikobusConnect(self.entry.data[CONF_CONNECTION])
        try:
            await self.connection.connect()
        except NikobusConnectionError as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

        self.engine = NikobusDiscovery(
            self,
            config_dir=self.hass.config.config_dir,
            create_task=self.hass.async_create_task,
            button_data=self.inventory.buttons,
            on_button_save=self.inventory.async_save,
            module_data=self.inventory.modules,
            on_module_save=self.inventory.async_save,
            on_progress=self.discovery.on_progress,
        )
        self.engine.on_discovery_finished = self.discovery.async_on_finished
        self.listener = NikobusEventListener(
            self.connection,
            self._async_on_frame,
            feedback_callback=self._async_on_feedback,
            has_feedback_module=self.feedback_module,
        )
        self.listener.on_connection_lost = self._async_connection_lost
        self.command = NikobusCommandHandler(
            self.connection, self.listener, module_states=self._states
        )
        self.nikobus_command = self.command
        self.api = NikobusAPI(self.command, self._led_key_table())
        self.api.press_repeat = int(self.entry.options.get(CONF_PRESS_REPEAT, DEFAULT_PRESS_REPEAT))
        self.allocate_states()
        self.apply_write_guard()
        try:
            await self.command.start()
            await self.listener.start()
        except NikobusError as err:
            await self.connection.disconnect()
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        self.connected = True
        self.connection.on_device_answered = self._note_probe
        self._note_probe()

        if self.old_pc_link:
            self._restore_states()
        else:
            interval = FEEDBACK_SAFETY_POLL if self.feedback_module else POLL_INTERVAL
            self.entry.async_on_unload(
                async_track_time_interval(self.hass, self._async_poll, interval)
            )
        self.entry.async_create_background_task(
            self.hass, self._async_first_reads(), name=f"{DOMAIN}_first_read"
        )
        maintenance = self.maintenance
        for unsubscribe in (
            async_track_time_interval(self.hass, maintenance.async_scheduled_clock, CLOCK_READ_INTERVAL),
            async_call_later(self.hass, REPROGRAMMED_CHECK_DELAY, maintenance.async_scheduled_check),
            async_track_time_interval(
                self.hass, maintenance.async_scheduled_check, REPROGRAMMED_CHECK_INTERVAL
            ),
        ):
            self.entry.async_on_unload(unsubscribe)

    async def _async_first_reads(self) -> None:
        """Every output group once, then the PC-Link's clock."""
        if not self.old_pc_link:
            await self._async_poll()
        await self.maintenance.async_scheduled_clock()

    def _restore_states(self) -> None:
        """Take back what was last written, where the gateway answers no state read.

        A set-output carries all six outputs of a group. With nothing to
        read them from, the other five are what Home Assistant last wrote,
        kept across restarts so that the first command does not switch them off.
        """
        for address, image in self.inventory.states.items():
            buffer = self._states.get(address)
            try:
                values = bytes.fromhex(image)
            except ValueError:
                continue
            if buffer is None or len(values) != len(buffer):
                continue
            buffer[:] = values
            for group in (1, 2)[: len(buffer) // 6]:
                self._heard[(address, group)] = time.monotonic()

    async def async_stop(self) -> None:
        """Stop listening and close the connection."""
        self._stopping = True
        self.connected = False
        if self.old_pc_link:
            # What was written is the only record of the outputs: do not leave it to a delayed save.
            await self.inventory.async_save()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
        self.presses.stop()
        for stop in (self.listener.stop, self.command.stop, self.connection.disconnect):
            try:
                await stop()
            except NikobusError as err:
                _LOGGER.debug("While stopping: %s", err)

    # --- output state ------------------------------------------------------------

    def allocate_states(self) -> None:
        """Give every output module a buffer of its own size."""
        for module in self.inventory.output_modules():
            size = 12 if module.channels > 6 else 6
            if len(self._states.get(module.address, b"")) != size:
                self._states[module.address] = bytearray(size)
        for address, _ in self.inventory.rgb_modules():
            if len(self._states.get(address, b"")) != 6:
                self._states[address] = bytearray(6)

    def value(self, address: str, channel: int) -> int | None:
        """What an output was last known to be; ``None`` before its group was heard."""
        if (address, 1 if channel <= 6 else 2) not in self._heard:
            return None
        buffer = self._states.get(address)
        return buffer[channel - 1] if buffer is not None and channel <= len(buffer) else None

    def image(self, address: str, group: int) -> bytes | None:
        """The six bytes a group last reported; ``None`` before it was heard."""
        if (address, group) not in self._heard:
            return None
        start = 0 if group == 1 else 6
        return bytes(self._states[address][start : start + 6])

    @callback
    def subscribe(self, address: str, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener`` whenever a module's state or availability changed."""
        self._module_listeners[address].append(listener)
        return lambda: self._module_listeners[address].remove(listener)

    @callback
    def subscribe_status(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener`` when the connection or the discovery status changed."""
        self._status_listeners.append(listener)
        return lambda: self._status_listeners.remove(listener)

    @callback
    def notify(self, address: str | None = None) -> None:
        """Wake the entities of one module, or everything."""
        if address is not None:
            for listener in list(self._module_listeners[address]):
                listener()
            return
        for listeners in list(self._module_listeners.values()):
            for listener in list(listeners):
                listener()
        for listener in list(self._status_listeners):
            listener()

    def _apply(self, address: str, group: int, state_hex: str) -> None:
        """Store a group's reported state and wake its module's entities."""
        if self.command.apply_group_state(address, group, state_hex):
            self._heard[(address, group)] = time.monotonic()
            self.notify(address)

    async def async_read_group(self, address: str, group: int) -> bool:
        """Read one output group from its module. ``False`` when it did not answer."""
        try:
            state = await self.command.get_output_state(address, group)
        except (NikobusError, TimeoutError) as err:
            _LOGGER.debug("Module %s group %d did not answer: %s", address, group, err)
            return False
        if not state:
            return False
        self._apply(address, group, state)
        return True

    def _led_key_table(self) -> dict[str, dict[str, Any]]:
        """The LED keys of the options, in the shape the library looks them up in."""
        table: dict[str, dict[str, Any]] = {}
        for module in self.inventory.output_modules():
            channels: list[dict[str, str]] = []
            for channel in range(1, module.channels + 1):
                keys = self.led_keys.get(f"{module.address}:{channel}", {})
                channels.append(
                    {
                        name: key
                        for name, key in (("led_on", keys.get(CONF_ON_KEY)), ("led_off", keys.get(CONF_OFF_KEY)))
                        if key
                    }
                )
            table.setdefault(module.module_type, {})[module.address] = {"channels": channels}
        return table

    async def _async_set_by_key(self, address: str, channel: int, value: int) -> bool:
        """Make the change by pressing the output's LED key. ``False`` when it has none for it.

        A plate's LED follows key telegrams, not a set-output: an output
        with an LED key is therefore switched by that key. The library
        reads the module first and leaves the key alone when the output
        is already there, since a press that was not needed inverts the LED.
        """
        keys = self.led_keys.get(f"{address}:{channel}", {})
        module_type = self.inventory.module_type(address)
        before = self.value(address, channel) or 0
        # A shutter's keys are its open key and its close key; a stop is
        # the key of the direction it runs in.
        opening = (value or before) == COVER_OPENING
        towards_on = bool(value) if module_type in (SWITCH_MODULE, DIMMER_MODULE) else opening
        key = keys.get(CONF_ON_KEY if towards_on else CONF_OFF_KEY)
        if not key:
            return False
        api = self.api
        self.note_press(key)
        try:
            if module_type == SWITCH_MODULE:
                await (api.turn_on_switch if value else api.turn_off_switch)(address, channel)
            elif module_type == DIMMER_MODULE and value:
                await api.turn_on_light(address, channel, value, before)
            elif module_type == DIMMER_MODULE:
                await api.turn_off_light(address, channel, before)
            elif value == COVER_STOPPED:
                await api.stop_cover(address, channel, "opening" if opening else "closing")
            else:
                await (api.open_cover if opening else api.close_cover)(address, channel)
        except (NikobusError, TimeoutError) as err:
            self.hass.async_create_task(self.async_read_group(address, 1 if channel <= 6 else 2))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"address": address, "error": str(err)},
            ) from err
        # The key may drive more than this output: read what it reached.
        self.note_press(key)
        self.presses.schedule_reads(key, released=True)
        self.presses.schedule_read(address, 1 if channel <= 6 else 2, RELEASE_READ_DELAY)
        self.notify(address)
        return True

    async def async_set_output(self, address: str, channel: int, value: int) -> None:
        """Write one output and wait for the module's acknowledgement."""
        if not self.connected:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_connected")
        if self.led_keys and await self._async_set_by_key(address, channel, value):
            return
        group = 1 if channel <= 6 else 2
        try:
            written = await self.command.set_output_state(address, channel, value)
            # The buffer holds the new value from here on.
            self.notify(address)
            await written
        except (NikobusError, TimeoutError) as err:
            # The buffer may now hold a value the module never took.
            self.hass.async_create_task(self.async_read_group(address, group))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"address": address, "error": str(err)},
            ) from err
        # The module holds exactly the frame it acknowledged, and the library
        # may have read the group first: the other outputs can have moved.
        self._heard[(address, group)] = time.monotonic()
        if self.old_pc_link:
            self.inventory.states[address] = bytes(self._states[address]).hex()
            self.inventory.schedule_save()
        self.notify(address)

    # --- presses -----------------------------------------------------------------

    @callback
    def note_press(self, bus_address: str) -> None:
        """Forget the state of what a press may have changed.

        The next write to those groups then reads them first: the groups
        the key's links drive, or every group for a key with no known link.
        """
        groups = self.inventory.impacted_groups(bus_address)
        if not groups:
            self.command.invalidate_group_state()
            return
        for address, group in groups:
            self.command.invalidate_group_state(address, group)

    async def async_press(self, bus_address: str) -> None:
        """Put a key press on the bus, as a held key does."""
        if not self.connected:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_connected")
        bus_address = bus_address.upper()
        self.note_press(bus_address)

        async def _on_the_wire() -> None:
            # The gateway does not relay the host's own press: read what it reached.
            self.note_press(bus_address)
            self.presses.schedule_reads(bus_address, released=True)

        try:
            await self.api.press_button(bus_address, completion_handler=_on_the_wire)
        except (NikobusError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"address": bus_address, "error": str(err)},
            ) from err

    # --- frames ------------------------------------------------------------------

    async def _async_on_frame(self, message: str) -> None:
        """Route what is neither an acknowledgement nor an output state."""
        if message.startswith("#N") and len(message) >= 8:
            self.presses.frame(message[2:8])
        elif message.startswith(DEVICE_ADDRESS_INVENTORY):
            self.discovery.on_inventory_frame(message)
        elif message.startswith(DEVICE_INVENTORY_ANSWER):
            await self.discovery.async_on_answer_frame(message)

    @callback
    def _note_probe(self) -> None:
        """Say so when the port opened and nothing on it answered.

        A wrong port, a PC-Link without power, a port another program left
        dead: the connection stands, and every command would time out
        without a word. The library takes the verdict back on the first
        frame it receives, and calls this again.
        """
        issue_id = f"{ISSUE_NO_DEVICE}_{self.entry.entry_id}"
        if self.connection.device_answered is False:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_NO_DEVICE,
                translation_placeholders={"connection": self.entry.data[CONF_CONNECTION]},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def _note_feedback_port(self) -> None:
        """Say so when states are pushed with none of the queries behind them.

        A PC-Link relays the Feedback Module's queries and the answers;
        the Feedback Module's own port relays the answers alone. Which
        group an answer is for is then a guess, and the two halves of a
        twelve-output module get swapped.
        """
        issue_id = f"{ISSUE_FEEDBACK_PORT}_{self.entry.entry_id}"
        if self.listener.feedback_queries_seen:
            if self._feedback_port_reported:
                self._feedback_port_reported = False
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        elif self.listener.feedback_answers_seen >= FEEDBACK_PORT_ANSWERS and not self._feedback_port_reported:
            self._feedback_port_reported = True
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_FEEDBACK_PORT,
                data={"entry_id": self.entry.entry_id},
            )

    async def _async_on_feedback(self, group: int, message: str) -> None:
        """An output state nobody here asked for: a Feedback Module's read."""
        self._note_feedback_port()
        if len(message) < 21:
            return
        address = (message[5:7] + message[3:5]).upper()
        state = message[9:21]
        self._apply(address, group, state)
        self.command.resolve_pending_get(address, group, state)

    # --- keeping up --------------------------------------------------------------

    @property
    def busy(self) -> bool:
        """Whether a discovery or a maintenance run has the bus."""
        return self.discovery.running or self.discovery_running or self.maintenance.running

    def apply_write_guard(self) -> None:
        """Tell the library which groups need no read before a write.

        The groups nothing but a key telegram changes, for two refresh
        periods; none while a discovery rewrites the links they are
        judged by. ``note_press`` is the other half of the bargain.
        """
        if self.old_pc_link:
            # It answers no state read: reading first would only be a timeout before every command.
            self.command.read_before_write_s = None
            return
        groups = set() if self.discovery.running else self.inventory.steady_groups()
        period = FEEDBACK_SAFETY_POLL if self.feedback_module else POLL_INTERVAL
        self.command.set_steady_groups(groups, max_age=2 * period.total_seconds())

    async def _async_poll(self, _now: datetime | None = None) -> None:
        """Read the groups nothing has reported for a whole period."""
        if not self.connected or self.busy:
            return
        horizon = time.monotonic() - FEEDBACK_SAFETY_POLL.total_seconds()
        due = [
            key
            for key in self.inventory.output_groups()
            if not self.feedback_module or self._heard.get(key, float("-inf")) < horizon
        ]
        answered: set[str] = set()
        for address, group in due:
            if not self.connected or self.busy:
                return
            if await self.async_read_group(address, group):
                answered.add(address)
        if due and not answered and any(key in self._heard for key in due):
            _LOGGER.warning("No module answered a state read: reconnecting")
            await self._async_connection_lost()
        elif answered:
            self._note_silent({address for address, _ in due}, answered)

    def _note_silent(self, asked: set[str], answered: set[str]) -> None:
        """Report a module that stays silent while the others answer."""
        for address in asked:
            issue_id = f"{ISSUE_NOT_ANSWERING}_{address.lower()}"
            if address in answered:
                if self._silent.pop(address, 0) >= SILENT_READS:
                    ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue
            self._silent[address] += 1
            if self._silent[address] == SILENT_READS:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=ISSUE_NOT_ANSWERING,
                    translation_placeholders={"module": self.inventory.module_label(address)},
                    data={"entry_id": self.entry.entry_id, "address": address},
                )

    async def async_forget(self, address: str) -> None:
        """Take a module or a plate out of the inventory; a discovery may bring it back."""
        address = address.upper()
        if self.inventory.remove(address):
            await self.inventory.async_save()
        self._states.pop(address, None)
        for key in [key for key in self._heard if key[0] == address]:
            del self._heard[key]
        self._silent.pop(address, None)
        ir.async_delete_issue(self.hass, DOMAIN, f"{ISSUE_NOT_ANSWERING}_{address.lower()}")
        self.apply_write_guard()

    async def _async_connection_lost(self) -> None:
        """The line dropped, or went silent: reconnect in the background."""
        if self._stopping or self._reconnect_task is not None:
            return
        self._reconnect_task = self.entry.async_create_background_task(
            self.hass, self._async_reconnect(), name=f"{DOMAIN}_reconnect"
        )

    async def _async_reconnect(self) -> None:
        _LOGGER.warning("Connection to the Nikobus gateway lost: reconnecting")
        self.connected = False
        self.notify()
        try:
            while True:
                # The listener is stopped first: its pending read on the old
                # line would otherwise fail inside the new handshake.
                await self.command.stop()
                await self.listener.stop()
                await self.connection.reconnect_with_backoff(
                    initial_delay=RECONNECT_DELAY_INITIAL, max_delay=RECONNECT_DELAY_MAX
                )
                try:
                    self.command.reset()
                    self.listener.reset()
                    await self.command.start()
                    await self.listener.start()
                except NikobusError as err:
                    _LOGGER.warning("Could not restart after reconnecting: %s", err)
                    await self.connection.disconnect()
                    continue
                break
        finally:
            self._reconnect_task = None
        self._heard.clear()
        self.connected = True
        self._note_probe()
        self.notify()
        _LOGGER.info("Reconnected to the Nikobus gateway")
        await self._async_poll()
