"""Position calculator for time-based Nikobus covers.

The model is the library's (``nikobus_connect.travel.TravelCalculator``,
since 0.51.0). This keeps the integration's name for it and binds the
clock late, through this module's ``time``, so a test can still patch
``nkbtravelcalculator.time.monotonic`` to drive a cover entity.
"""

from __future__ import annotations

import time

from nikobus_connect.travel import TravelCalculator


class NikobusTravelCalculator(TravelCalculator):
    """The library's travel model, on the integration's clock."""

    def __init__(self, time_up: float, time_down: float) -> None:
        super().__init__(time_up, time_down, clock=lambda: time.monotonic())


__all__ = ["NikobusTravelCalculator"]
