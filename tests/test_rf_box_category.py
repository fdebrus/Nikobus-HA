"""The library's 05-315 RF box lands with the RF transmitters.

nikobus-connect 0.57.4 catalogues type 0x3C (Nikobus-HA #575). The
router files a button by its type name, matching "interface" before
"RF"; this pins the library's name to the class it is meant for, so a
rename there cannot move the device to another class unnoticed.
"""

from __future__ import annotations

from nikobus_connect.discovery.mapping import DEVICE_TYPES

from custom_components.nikobus.const import CATEGORY_REMOTES
from custom_components.nikobus.router import _category_for_button_type


def test_rf_box_is_filed_with_the_remotes() -> None:
    assert _category_for_button_type(DEVICE_TYPES["3C"]["Name"]) == CATEGORY_REMOTES
