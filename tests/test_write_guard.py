"""Which groups are written without a read first, and what a press forgets.

A set-output frame carries a group's six outputs from the state buffer.
Since nikobus-connect 0.56.1 the group was read first whenever its state
was more than three seconds old, about half a second before every relay
click. With 0.57.5 the integration names the groups nothing but a key
telegram changes, and forgets a group's state on every press that may
have reached it, so the read is only made where the buffer may be behind.

The command handler is the library's real one; only the bus is absent.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from nikobus_connect import NikobusCommandHandler

from custom_components.nikobus.const import FEEDBACK_SAFETY_POLL_S
from custom_components.nikobus.coordinator import NikobusDataCoordinator
from custom_components.nikobus.nkbactuator import NikobusActuator

SCANNED = "2026-11-09T10:00:00+00:00"

MODULES: dict[str, Any] = {
    "nikobus_module": {
        "4707": {"module_type": "switch_module", "channels": [{}] * 12, "links_read_at": SCANNED},
        "C9A5": {"module_type": "switch_module", "channels": [{}] * 12, "links_read_at": SCANNED},
        "0E6C": {"module_type": "dimmer_module", "channels": [{}] * 12, "links_read_at": SCANNED},
        "9105": {"module_type": "roller_module", "channels": [{}] * 6, "links_read_at": SCANNED},
    }
}


def _link(module: str, channel: int, mode: str, t1: str | None = None) -> dict[str, Any]:
    return {"module_address": module, "outputs": [{"channel": channel, "mode": mode, "t1": t1}]}


BUTTONS: dict[str, Any] = {
    "nikobus_button": {
        "1843B4": {
            "channels": 4,
            "operation_points": {
                # On / off on 4707 ch 1, and the off key of a two-key control on ch 8.
                "1A": {"bus_address": "8B7086", "linked_modules": [_link("4707", 1, "M01 (On / off)")]},
                "1B": {"bus_address": "CB7086", "linked_modules": [_link("4707", 8, "M03 (Off + Operating time)", "0s")]},
                # A staircase timer on C9A5 ch 2: group 1 of C9A5 changes by itself.
                "1C": {"bus_address": "0B7086", "linked_modules": [_link("C9A5", 2, "M06 (Delayed off (up to 2h))", "3m")]},
                # A key linked to nothing.
                "1D": {"bus_address": "4B7086", "linked_modules": []},
            },
        }
    }
}


def _handler() -> NikobusCommandHandler:
    return NikobusCommandHandler(connection=MagicMock(), listener=MagicMock(), module_states={})


def _coordinator(*, feedback: bool = True, prior_gen3: bool = False, interval: int = 120) -> NikobusDataCoordinator:
    coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    coord.nikobus_command = _handler()
    coord._prior_gen3 = prior_gen3
    coord._has_feedback_module = feedback
    coord._refresh_interval = interval
    coord._discovery_running = False
    coord.dict_button_data = BUTTONS
    coord.module_storage = MagicMock()
    coord.module_storage.data = MODULES
    return coord


def test_only_untimed_switch_groups_are_written_without_a_read() -> None:
    coord = _coordinator()
    coord._apply_write_guard()
    assert coord.steady_group_labels() == ["4707 g1", "4707 g2", "C9A5 g2"]


@pytest.mark.parametrize(
    ("feedback", "interval", "max_age"),
    [
        pytest.param(True, 120, 2 * FEEDBACK_SAFETY_POLL_S, id="feedback-module-safety-poll"),
        pytest.param(False, 120, 240, id="polling-default"),
        pytest.param(False, 600, 1200, id="polling-slow"),
    ],
)
def test_the_buffer_is_trusted_for_two_refresh_periods(feedback: bool, interval: int, max_age: float) -> None:
    coord = _coordinator(feedback=feedback, interval=interval)
    coord._apply_write_guard()
    assert set(coord.nikobus_command._steady_groups.values()) == {float(max_age)}


def test_no_group_is_steady_while_a_scan_rewrites_the_links() -> None:
    coord = _coordinator()
    coord.discovery_running = True
    assert coord.steady_group_labels() == []
    coord.discovery_running = False
    assert coord.steady_group_labels() == ["4707 g1", "4707 g2", "C9A5 g2"]


def test_an_interface_that_answers_no_state_query_is_never_read_first() -> None:
    coord = _coordinator(prior_gen3=True)
    coord._apply_write_guard()
    assert coord.nikobus_command.read_before_write_s is None


def _actuator(command: NikobusCommandHandler) -> NikobusActuator:
    coordinator = MagicMock()
    coordinator.nikobus_command = command
    return NikobusActuator(MagicMock(), coordinator, BUTTONS, MODULES)


def _known(command: NikobusCommandHandler) -> set[tuple[str, int]]:
    return {key for key in (("4707", 1), ("4707", 2), ("C9A5", 1), ("C9A5", 2)) if command.group_state_age(*key) is not None}


def _all_known() -> NikobusCommandHandler:
    command = _handler()
    for address, group in (("4707", 1), ("4707", 2), ("C9A5", 1), ("C9A5", 2)):
        command.apply_group_state(address, group, "00" * 6)
    return command


def test_a_press_forgets_the_groups_its_links_drive() -> None:
    command = _all_known()
    _actuator(command).note_press("cb7086")  # 1B: 4707 channel 8
    assert _known(command) == {("4707", 1), ("C9A5", 1), ("C9A5", 2)}


@pytest.mark.parametrize(
    "address",
    [pytest.param("4B7086", id="key-without-links"), pytest.param("123456", id="unknown-key")],
)
def test_a_press_that_cannot_be_attributed_forgets_every_group(address: str) -> None:
    command = _all_known()
    _actuator(command).note_press(address)
    assert _known(command) == set()


def test_the_first_frame_of_a_wall_press_forgets_before_anything_else() -> None:
    command = _all_known()
    actuator = _actuator(command)
    actuator._hass.async_create_task = lambda coro: coro.close()
    actuator._fire_event = MagicMock()
    asyncio.new_event_loop().run_until_complete(actuator.handle_button_press("8B7086"))
    assert _known(command) == {("4707", 2), ("C9A5", 1), ("C9A5", 2)}


def test_a_press_home_assistant_sends_forgets_too() -> None:
    from unittest.mock import AsyncMock

    from nikobus_connect import NikobusAPI

    coord = NikobusDataCoordinator.__new__(NikobusDataCoordinator)
    coord._press_repeat = 3
    coord.nikobus_command = AsyncMock()
    coord.api = NikobusAPI(coord.nikobus_command, {})
    coord.nikobus_actuator = MagicMock()
    asyncio.new_event_loop().run_until_complete(coord.async_send_button_press("8b7086"))
    # Before the burst is queued, so a command queued behind it reads first.
    coord.nikobus_actuator.note_press.assert_called_once_with("8B7086")
