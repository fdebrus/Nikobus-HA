"""Tests for the actuator around the library's press tracker.

What a press *is* — wire-time duration, hold milestones, burst patience
of the release detector — lives in ``nikobus_connect.press`` and is
tested there. These pin what the integration adds: the events fired,
the per-address signals, the release watcher, the refreshes of the
modules a key drives, and teardown.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from nikobus_connect.press import (
    FRAME_CADENCE_S,
    MAX_EXTENDED_RELEASE_MS,
    RELEASE_THRESHOLD_MS,
    PressState,
    PressTracker,
)

from custom_components.nikobus.const import SHORT_PRESS
from custom_components.nikobus.nkbactuator import NikobusActuator


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()

    def async_create_task(self, coro):
        # Don't actually schedule — these tests drive the state
        # machine directly via handle_button_press and inspect the
        # events fired. The release task and discovery task would
        # otherwise hit asyncio.get_event_loop() and the test
        # bus-side mocks aren't fleshed out enough to satisfy them.
        # Closing the coroutine is enough to suppress
        # "coroutine was never awaited" warnings.
        coro.close()

        class _DummyTask:
            def cancel(self) -> None:
                pass

        return _DummyTask()


def _make_actuator(clock: _Clock | None = None) -> NikobusActuator:
    hass = _FakeHass()
    coordinator = MagicMock()
    coordinator.nikobus_command = MagicMock()
    coordinator.nikobus_command.get_output_state = AsyncMock(return_value=None)
    actuator = NikobusActuator(
        hass=hass,
        coordinator=coordinator,
        dict_button_data={"nikobus_button": {}},
        module_data={"nikobus_module": {}},
        tracker=PressTracker(clock=clock or _Clock()),
    )
    return actuator


def _state(address: str = "C5E952", frame_count: int = 1) -> PressState:
    state = PressState(address=address, press_id="pid", started_at=0.0, last_frame_at=0.0)
    state.frame_count = frame_count
    return state


def _events_of(actuator: NikobusActuator, event_type: str) -> list[dict]:
    return [
        payload
        for et, payload in actuator._hass.bus.events
        if et == event_type
    ]


def _make_actuator_with_link() -> NikobusActuator:
    actuator = _make_actuator()
    actuator._dict_button_data["nikobus_button"]["105AA5"] = {
        "operation_points": {
            "1C": {
                "bus_address": "295682",
                "linked_modules": [
                    {"module_address": "81F6", "outputs": [{"channel": 7}, {"channel": 9}]}
                ],
            }
        }
    }
    return actuator


def test_stop_cancels_inflight_tasks():
    """stop() cancels in-flight release + refresh tasks and clears the maps."""

    actuator = _make_actuator()
    asyncio.run(actuator.handle_button_press("AA0000"))
    release = MagicMock()
    release.done.return_value = False
    actuator._contexts["AA0000"].release_task = release
    refresh = MagicMock()
    refresh.done.return_value = False
    actuator._module_refresh_tasks["BB00_1"] = refresh

    actuator.stop()

    release.cancel.assert_called_once()
    refresh.cancel.assert_called_once()
    assert actuator._contexts == {}
    assert "AA0000" not in actuator.tracker
    assert actuator._module_refresh_tasks == {}


# ---------------------------------------------------------------------------
# Frames feed the tracker; the first one fires the press event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_frame_starts_the_press_and_fires_pressed():
    actuator = _make_actuator()
    await actuator.handle_button_press("c5e952")

    state = actuator.tracker.active("C5E952")
    assert state is not None and state.frame_count == 1
    assert state.release_threshold_ms == float(RELEASE_THRESHOLD_MS)
    pressed = _events_of(actuator, "nikobus_button_pressed")
    assert len(pressed) == 1
    assert pressed[0]["address"] == "C5E952"
    assert pressed[0]["state"] == "pressed" and pressed[0]["duration_s"] is None
    assert pressed[0]["press_id"] == state.press_id


@pytest.mark.asyncio
async def test_subsequent_frames_extend_the_press_without_a_second_pressed_event():
    actuator = _make_actuator()
    for _ in range(5):
        await actuator.handle_button_press("C5E952")

    assert actuator.tracker.active("C5E952").frame_count == 5
    assert len(_events_of(actuator, "nikobus_button_pressed")) == 1


@pytest.mark.asyncio
async def test_the_pressed_event_names_the_module_the_key_drives():
    actuator = _make_actuator_with_link()
    await actuator.handle_button_press("295682")
    pressed = _events_of(actuator, "nikobus_button_pressed")[0]
    assert (pressed["module_address"], pressed["channel"]) == ("81F6", 7)


# ---------------------------------------------------------------------------
# Timer events fire from wire-time crossings (synchronous, burst-safe)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timer_events_fire_when_frame_count_crosses_thresholds():
    """25 frames = 1.0 s of wire time → timer_1, 50 → timer_2, 75 →
    timer_3, all synchronously even when the frames arrive as a burst."""
    actuator = _make_actuator()
    for _ in range(80):
        await actuator.handle_button_press("C5E952")

    timer_1 = _events_of(actuator, "nikobus_button_timer_1")
    timer_2 = _events_of(actuator, "nikobus_button_timer_2")
    timer_3 = _events_of(actuator, "nikobus_button_timer_3")
    assert (len(timer_1), len(timer_2), len(timer_3)) == (1, 1, 1)
    assert timer_1[0]["duration_s"] == pytest.approx(25 * FRAME_CADENCE_S, abs=0.05)
    assert timer_2[0]["duration_s"] == pytest.approx(50 * FRAME_CADENCE_S, abs=0.05)
    assert timer_3[0]["duration_s"] == pytest.approx(75 * FRAME_CADENCE_S, abs=0.05)
    assert timer_1[0]["threshold_s"] == 1 and timer_1[0]["state"] == "timer"


@pytest.mark.asyncio
async def test_timer_events_only_fire_once_per_threshold():
    actuator = _make_actuator()
    for _ in range(40):
        await actuator.handle_button_press("C5E952")
    assert len(_events_of(actuator, "nikobus_button_timer_1")) == 1
    assert _events_of(actuator, "nikobus_button_timer_2") == []


@pytest.mark.asyncio
async def test_short_tap_fires_no_timer_events():
    actuator = _make_actuator()
    for _ in range(5):
        await actuator.handle_button_press("C5E952")
    for threshold in (1, 2, 3):
        assert _events_of(actuator, f"nikobus_button_timer_{threshold}") == []


# ---------------------------------------------------------------------------
# Release: the watcher asks the tracker, then fires the classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_waits_out_the_patience_then_classifies():
    clock = _Clock()
    actuator = _make_actuator(clock)
    for _ in range(5):
        await actuator.handle_button_press("C5E952")
    state = actuator.tracker.active("C5E952")

    clock.tick(0.2)
    assert await actuator._release_if_due(state) is False
    assert _events_of(actuator, "nikobus_button_released") == []

    clock.tick(0.15)
    assert await actuator._release_if_due(state) is True
    released = _events_of(actuator, "nikobus_button_released")
    assert len(released) == 1
    assert released[0]["duration_s"] == pytest.approx(5 * FRAME_CADENCE_S)
    assert released[0]["bucket"] == 0
    assert len(_events_of(actuator, "nikobus_short_button_pressed")) == 1
    assert _events_of(actuator, "nikobus_long_button_pressed") == []
    assert len(_events_of(actuator, "nikobus_button_pressed_0")) == 1
    assert "C5E952" not in actuator.tracker and "C5E952" not in actuator._contexts


@pytest.mark.asyncio
async def test_a_burst_extends_the_patience_before_release():
    """97 frames flushed at once: the tracker waits for the implied stall
    (3.88 s, under the cap) before the release, and then files the
    press as a 3.88 s long press in bucket 3 — not a 12 ms tap."""
    clock = _Clock()
    actuator = _make_actuator(clock)
    for _ in range(97):
        await actuator.handle_button_press("C5E952")
        clock.tick(0.0001)
    state = actuator.tracker.active("C5E952")
    assert state.release_threshold_ms == pytest.approx(97 * FRAME_CADENCE_S * 1000)

    clock.tick(RELEASE_THRESHOLD_MS / 1000 + 0.1)
    assert await actuator._release_if_due(state) is False
    clock.tick(MAX_EXTENDED_RELEASE_MS / 1000)
    assert await actuator._release_if_due(state) is True

    released = _events_of(actuator, "nikobus_button_released")
    assert released[0]["duration_s"] == pytest.approx(97 * FRAME_CADENCE_S)
    assert released[0]["bucket"] == 3
    assert len(_events_of(actuator, "nikobus_long_button_pressed")) == 1
    assert len(_events_of(actuator, "nikobus_button_pressed_3")) == 1
    assert _events_of(actuator, "nikobus_short_button_pressed") == []


@pytest.mark.asyncio
async def test_handle_release_uses_wire_time_for_duration_and_bucket():
    actuator = _make_actuator()
    await actuator._handle_release(_state(frame_count=97))
    released = _events_of(actuator, "nikobus_button_released")
    assert released[0]["duration_s"] == pytest.approx(97 * FRAME_CADENCE_S)
    assert released[0]["bucket"] == 3
    assert _events_of(actuator, "nikobus_short_button_pressed") == []


@pytest.mark.asyncio
async def test_short_tap_still_classified_as_short_press():
    actuator = _make_actuator()
    state = _state(frame_count=5)
    assert state.duration_s < SHORT_PRESS
    await actuator._handle_release(state)
    assert len(_events_of(actuator, "nikobus_short_button_pressed")) == 1
    assert _events_of(actuator, "nikobus_long_button_pressed") == []
    assert _events_of(actuator, "nikobus_button_pressed_0")[0]["bucket"] == 0


@pytest.mark.asyncio
async def test_the_watcher_stops_when_its_press_is_gone():
    """A release watcher outlives nothing: once the tracker no longer
    holds its press, it returns without touching a successor."""
    clock = _Clock()
    actuator = _make_actuator(clock)
    await actuator.handle_button_press("C5E952")
    first = actuator.tracker.active("C5E952")
    actuator.tracker.end("C5E952")
    clock.tick(0.5)
    await actuator.handle_button_press("C5E952")
    clock.tick(5.0)
    assert await actuator._release_if_due(first) is False
    assert _events_of(actuator, "nikobus_button_released") == []


# ---------------------------------------------------------------------------
# A press sent by Home Assistant refreshes what it impacts, silently
# ---------------------------------------------------------------------------

def test_host_press_schedules_the_refresh_without_an_event():
    """The PC-Link never relays the host's own #N: the refresh must be
    scheduled explicitly, and no nikobus_button_operation event fired —
    nobody pressed a key."""

    actuator = _make_actuator_with_link()
    asyncio.run(actuator.refresh_after_host_press("295682"))
    assert "81F6_2" in actuator._module_refresh_tasks
    assert _events_of(actuator, "nikobus_button_operation") == []


def test_inbound_press_still_fires_the_event():

    actuator = _make_actuator_with_link()
    asyncio.run(actuator.button_discovery("295682", press_context={"press_id": "p1", "duration_s": 0.3}))
    assert "81F6_2" in actuator._module_refresh_tasks
    assert len(_events_of(actuator, "nikobus_button_operation")) == 1


def test_host_press_of_an_unknown_key_is_a_noop():

    actuator = _make_actuator()
    asyncio.run(actuator.refresh_after_host_press("000000"))
    assert actuator._module_refresh_tasks == {}


def _make_actuator_linked_to(module_address: str, module_type: str | None) -> NikobusActuator:
    """One key whose only link is channel 1 of ``module_address``."""
    actuator = _make_actuator()
    actuator._dict_button_data["nikobus_button"] = {
        "0D1C80": {
            "address": "0D1C80",
            "operation_points": {
                "AUD": {
                    "bus_address": "8083CF",
                    "linked_modules": [
                        {"module_address": module_address, "outputs": [{"channel": 1}]}
                    ],
                }
            },
        }
    }
    if module_type is not None:
        actuator._module_data["nikobus_module"] = {
            module_address: {"address": module_address, "module_type": module_type}
        }
    return actuator


def test_a_press_on_an_audio_trigger_does_not_read_the_audio_module():
    """The 05-205 never answers $1012: reading it after every press was
    three timeouts and an error (Nikobus-HA #310)."""

    actuator = _make_actuator_linked_to("8334", "audio_module")
    asyncio.run(actuator.refresh_after_host_press("8083CF"))
    assert actuator._module_refresh_tasks == {}
    asyncio.run(actuator.button_discovery("8083CF", press_context={"press_id": "p1", "duration_s": 0.3}))
    assert actuator._module_refresh_tasks == {}


def test_a_press_on_a_key_of_a_switch_module_still_reads_it():

    actuator = _make_actuator_linked_to("4707", "switch_module")
    asyncio.run(actuator.refresh_after_host_press("8083CF"))
    assert "4707_1" in actuator._module_refresh_tasks


def test_a_module_the_store_does_not_know_is_still_read():
    """Unclassified is not the same as known-silent."""

    actuator = _make_actuator_linked_to("4707", None)
    asyncio.run(actuator.refresh_after_host_press("8083CF"))
    assert "4707_1" in actuator._module_refresh_tasks


def test_a_press_on_a_key_of_the_rgb_controller_reads_it():
    """The controller answers the state query, and its light consumes the
    answer — so a press on its key refreshes it like a switch module."""

    actuator = _make_actuator_linked_to("801D", "rgb_module")
    asyncio.run(actuator.refresh_after_host_press("8083CF"))
    assert "801D_1" in actuator._module_refresh_tasks
