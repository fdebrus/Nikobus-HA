"""Tests for the frame-count-anchored press state machine.

Pins:

  * Each received frame counts as one ``FRAME_CADENCE_S`` quantum of
    held time, regardless of when it actually arrived in our process.
  * Timer events (``nikobus_button_timer_1/2/3``) fire when
    ``frame_count`` crosses their threshold — synchronously, so a
    burst-flush triggers them all even though wall-clock barely
    advanced.
  * The release-detection threshold extends in burst mode (last few
    inter-frame gaps below ``BURST_GAP_THRESHOLD_S``), capped at
    ``MAX_EXTENDED_RELEASE_MS``.
  * On release, ``duration_s`` and the bucket classification come
    from ``frame_count * FRAME_CADENCE_S`` — so a 97-frame burst
    that all arrives in 12 ms is correctly classified as a 3.88 s
    long press, not a 12 ms tap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.nikobus.const import (
    BURST_RECENT_GAPS_WINDOW,
    FRAME_CADENCE_S,
    MAX_EXTENDED_RELEASE_MS,
    RELEASE_THRESHOLD_MS,
    SHORT_PRESS,
)
from custom_components.nikobus.nkbactuator import NikobusActuator, PressState


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


def _make_actuator() -> NikobusActuator:
    hass = _FakeHass()
    coordinator = MagicMock()
    coordinator.nikobus_command = MagicMock()
    coordinator.nikobus_command.get_output_state = AsyncMock(return_value=None)
    actuator = NikobusActuator(
        hass=hass,
        coordinator=coordinator,
        dict_button_data={"nikobus_button": {}},
        module_data={"nikobus_module": {}},
    )
    return actuator


def _events_of(actuator: NikobusActuator, event_type: str) -> list[dict]:
    return [
        payload
        for et, payload in actuator._hass.bus.events
        if et == event_type
    ]


# ---------------------------------------------------------------------------
# Frame-count duration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_frame_creates_press_state_with_frame_count_1():
    actuator = _make_actuator()
    await actuator.handle_button_press("C5E952")

    state = actuator._press_states["C5E952"]
    assert state.frame_count == 1
    assert state.current_release_threshold_ms == float(RELEASE_THRESHOLD_MS)


@pytest.mark.asyncio
async def test_subsequent_frames_increment_frame_count():
    actuator = _make_actuator()
    for _ in range(5):
        await actuator.handle_button_press("C5E952")

    state = actuator._press_states["C5E952"]
    assert state.frame_count == 5


# ---------------------------------------------------------------------------
# Timer events fire from frame-count crossings (synchronous, burst-safe)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timer_events_fire_when_frame_count_crosses_thresholds():
    """25 frames = 1.0 s of wire time → timer_1.
    50 frames = 2.0 s → timer_2.
    75 frames = 3.0 s → timer_3.
    All must fire synchronously even when frames arrive in a single
    burst (no wall-clock delay between calls).
    """
    actuator = _make_actuator()

    # Push 80 frames as fast as possible — simulates a buffered burst
    # flushing into the actuator.
    for _ in range(80):
        await actuator.handle_button_press("C5E952")

    timer_1 = _events_of(actuator, "nikobus_button_timer_1")
    timer_2 = _events_of(actuator, "nikobus_button_timer_2")
    timer_3 = _events_of(actuator, "nikobus_button_timer_3")

    assert len(timer_1) == 1
    assert len(timer_2) == 1
    assert len(timer_3) == 1

    # Duration in payload uses frame_count anchor: at the moment
    # timer_1 fires, frame_count just crossed 25, so duration ~= 1.0 s.
    assert timer_1[0]["duration_s"] == pytest.approx(25 * FRAME_CADENCE_S, abs=0.05)
    assert timer_2[0]["duration_s"] == pytest.approx(50 * FRAME_CADENCE_S, abs=0.05)
    assert timer_3[0]["duration_s"] == pytest.approx(75 * FRAME_CADENCE_S, abs=0.05)


@pytest.mark.asyncio
async def test_timer_events_only_fire_once_per_threshold():
    """Continuing frames after a timer threshold has been crossed
    must not refire the same timer event."""
    actuator = _make_actuator()
    for _ in range(40):
        await actuator.handle_button_press("C5E952")

    assert len(_events_of(actuator, "nikobus_button_timer_1")) == 1
    # We haven't reached timer_2 yet (50 frames):
    assert len(_events_of(actuator, "nikobus_button_timer_2")) == 0


@pytest.mark.asyncio
async def test_short_tap_fires_no_timer_events():
    """A 5-frame tap (= 200 ms wire-time) must not trigger any
    long-press timer events."""
    actuator = _make_actuator()
    for _ in range(5):
        await actuator.handle_button_press("C5E952")

    assert _events_of(actuator, "nikobus_button_timer_1") == []
    assert _events_of(actuator, "nikobus_button_timer_2") == []
    assert _events_of(actuator, "nikobus_button_timer_3") == []


# ---------------------------------------------------------------------------
# Burst-aware release threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_threshold_stays_at_baseline_for_normal_cadence():
    """Frames at normal ~40 ms cadence (no burst gaps) keep the
    release threshold at ``RELEASE_THRESHOLD_MS``."""
    actuator = _make_actuator()

    # Simulate normal cadence by manually populating the gap deque
    # — handle_button_press uses time.monotonic() so we'd otherwise
    # have to sleep. Drive the threshold-update logic directly.
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 10
    for _ in range(BURST_RECENT_GAPS_WINDOW):
        state.recent_gaps.append(0.040)

    actuator._update_release_threshold(state)
    assert state.current_release_threshold_ms == float(RELEASE_THRESHOLD_MS)


@pytest.mark.asyncio
async def test_burst_window_extends_release_threshold():
    """When the recent-gap window is full of burst-flush gaps
    (< 5 ms), the release threshold scales to the implied stall
    (frame_count * cadence ms), capped at the maximum."""
    actuator = _make_actuator()
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 50  # implies ~2 s of wire-time hold
    for _ in range(BURST_RECENT_GAPS_WINDOW):
        state.recent_gaps.append(0.0005)  # well under burst threshold

    actuator._update_release_threshold(state)
    # 50 frames * 40 ms = 2000 ms — bigger than the 300 ms baseline,
    # smaller than the 5 s cap, so we expect exactly that.
    assert state.current_release_threshold_ms == 2000.0


@pytest.mark.asyncio
async def test_burst_extended_threshold_caps_at_max():
    """A very long burst (300 frames = 12 s implied) gets clamped to
    ``MAX_EXTENDED_RELEASE_MS`` so release latency stays bounded."""
    actuator = _make_actuator()
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 300
    for _ in range(BURST_RECENT_GAPS_WINDOW):
        state.recent_gaps.append(0.0005)

    actuator._update_release_threshold(state)
    assert state.current_release_threshold_ms == float(MAX_EXTENDED_RELEASE_MS)


@pytest.mark.asyncio
async def test_threshold_does_not_shrink_within_burst_mode():
    """While the recent-gap window still shows burst signal, the
    threshold may only grow — once we've credited X ms of patience,
    a single subsequent burst-marker frame mustn't undo it."""
    actuator = _make_actuator()
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 100  # 4 s implied
    for _ in range(BURST_RECENT_GAPS_WINDOW):
        state.recent_gaps.append(0.0005)
    actuator._update_release_threshold(state)
    assert state.current_release_threshold_ms == 4000.0

    # A subsequent same-burst frame with frame_count still in the
    # burst window mustn't lower the threshold.
    state.frame_count = 50  # hypothetical recount
    actuator._update_release_threshold(state)
    assert state.current_release_threshold_ms >= 4000.0


@pytest.mark.asyncio
async def test_normal_cadence_window_relaxes_threshold():
    """After the recent-gap window fills with normal-cadence gaps,
    the threshold relaxes back to baseline so a real release on a
    healthy bridge isn't held up by stale burst state."""
    actuator = _make_actuator()
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 100
    state.current_release_threshold_ms = 4000.0  # was extended
    for _ in range(BURST_RECENT_GAPS_WINDOW):
        state.recent_gaps.append(0.040)  # all normal

    actuator._update_release_threshold(state)
    assert state.current_release_threshold_ms == float(RELEASE_THRESHOLD_MS)


# ---------------------------------------------------------------------------
# End-to-end: 97-frame burst classified correctly via frame count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_release_uses_frame_count_for_duration_and_bucket():
    """The release event payload must report ``duration_s`` as
    ``frame_count * FRAME_CADENCE_S``, and the bucket must reflect
    that. A 97-frame burst (= 3.88 s wire-time) is bucket 3 / long
    press, not bucket 0 / short tap — exactly the misclassification
    we're fixing.
    """
    actuator = _make_actuator()
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 97

    duration = state.frame_count * FRAME_CADENCE_S
    await actuator._handle_release(state, duration)

    released = _events_of(actuator, "nikobus_button_released")
    long_press = _events_of(actuator, "nikobus_long_button_pressed")
    bucket_3 = _events_of(actuator, "nikobus_button_pressed_3")
    short_press = _events_of(actuator, "nikobus_short_button_pressed")

    assert len(released) == 1
    assert released[0]["duration_s"] == pytest.approx(97 * FRAME_CADENCE_S)
    assert released[0]["bucket"] == 3
    assert len(long_press) == 1
    assert len(bucket_3) == 1
    # Most importantly: the broken-bridge case must NOT fire the
    # short-press event that today's wall-clock logic produces.
    assert short_press == []


@pytest.mark.asyncio
async def test_short_tap_still_classified_as_short_press():
    """A genuine 5-frame tap (= 200 ms wire-time) keeps producing
    a short-press event — no regression on the working case."""
    actuator = _make_actuator()
    actuator._press_states["C5E952"] = PressState(
        address="C5E952",
        press_start=0.0,
        last_press_time=0.0,
        press_id="pid",
        module_address=None,
        channel=None,
    )
    state = actuator._press_states["C5E952"]
    state.frame_count = 5

    duration = state.frame_count * FRAME_CADENCE_S
    assert duration < SHORT_PRESS

    await actuator._handle_release(state, duration)

    assert len(_events_of(actuator, "nikobus_short_button_pressed")) == 1
    assert _events_of(actuator, "nikobus_long_button_pressed") == []
    assert _events_of(actuator, "nikobus_button_pressed_0")[0]["bucket"] == 0
