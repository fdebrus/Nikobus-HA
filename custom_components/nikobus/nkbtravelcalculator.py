"""Position calculator for time-based Nikobus covers."""

import time

class NikobusTravelCalculator:
    """Calculates cover position based on travel time."""

    def __init__(self, time_up: float, time_down: float) -> None:
        """Initialize the calculator."""
        self.time_up = time_up
        self.time_down = time_down
        self.position: float = 100.0
        self._start_time: float | None = None
        self._start_pos: float | None = None
        self._direction: int | None = None  # 1 for up, -1 for down

    def set_position(self, position: float) -> None:
        """Manually set the current known position."""
        self.position = position

    def start_travel(self, direction: str) -> None:
        """Mark the start of travel and record start position/time."""
        self._start_pos = self.position
        self._start_time = time.monotonic()
        self._direction = 1 if direction == "opening" else -1

    def stop(self) -> None:
        """Stop traveling and lock in the calculated position."""
        self.position = self.current_position()
        self._direction = None

    def current_position(self) -> float:
        """Calculate the exact current position based on elapsed time."""
        if self._direction is None or self._start_time is None or self._start_pos is None:
            return self.position

        elapsed = time.monotonic() - self._start_time
        active_time = self.time_up if self._direction == 1 else self.time_down
        progress = (elapsed / active_time) * 100

        if self._direction == 1:
            return min(100.0, self._start_pos + progress)
        else:
            return max(0.0, self._start_pos - progress)