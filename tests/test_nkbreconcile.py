"""The reconcile shim: the library's store helpers re-exported, plus the
one rule that is Home Assistant's — which central functions become
scene entities. The helpers themselves are tested in the library."""

from __future__ import annotations

from custom_components.nikobus.nkbreconcile import (
    INPUT_ONLY_BUTTON_TYPES,
    build_controlled_by_index,
    classify_button_status,
    is_button_backed_cf,
    is_surfaced_cf_scene,
)


def test_the_shim_hands_out_the_library_helpers():
    from nikobus_connect.discovery import store

    assert build_controlled_by_index is store.build_controlled_by_index
    assert classify_button_status is store.classify_button_status
    assert INPUT_ONLY_BUTTON_TYPES is store.INPUT_ONLY_BUTTON_TYPES


# ---------------------------------------------------------------------------
# is_button_backed_cf / is_surfaced_cf_scene — which CFs become scenes.
#
# A button-backed light-scene (fired by a real wall button / IR input) is
# only surfaced as a scene once the .nkb import has named it; unnamed ones
# are ordinary buttons and must not appear as phantom scenes. The bare 38xx
# central functions always surface.
# ---------------------------------------------------------------------------


def _lscene(name=None, pattern="light_scene"):
    cf = {"pattern": pattern,
          "outputs": [{"module_address": "9105", "channel": 3,
                       "mode": "M04 (Light scene on)"}]}
    if name is not None:
        cf["name"] = name
    return cf


def test_is_button_backed_cf():
    assert is_button_backed_cf(_lscene()) is True
    assert is_button_backed_cf(_lscene(pattern="nkb_scene")) is True
    assert is_button_backed_cf({"pattern": "switch_pair"}) is False
    assert is_button_backed_cf({"pattern": "roller_pair"}) is False
    assert is_button_backed_cf({"pattern": "cf_other"}) is False
    assert is_button_backed_cf({}) is False


def test_unnamed_button_backed_light_scene_is_not_surfaced():
    assert is_surfaced_cf_scene(_lscene()) is False
    assert is_surfaced_cf_scene(_lscene(name="")) is False
    assert is_surfaced_cf_scene(_lscene(name="   ")) is False
    assert is_surfaced_cf_scene(_lscene(pattern="nkb_scene")) is False


def test_named_button_backed_light_scene_is_surfaced():
    assert is_surfaced_cf_scene(_lscene(name="Scene - TV")) is True
    assert is_surfaced_cf_scene(_lscene(name="ShuttersUp", pattern="nkb_scene")) is True


def test_bare_38xx_cf_always_surfaces():
    # switch_pair / roller_pair / cf_other are genuine central functions
    # (no button behind them) — surfaced whether named or not.
    for pat in ("switch_pair", "roller_pair", "cf_other", "unknown"):
        cf = {"pattern": pat, "outputs": [
            {"module_address": "8110", "channel": 1, "mode": "M02 (on)"}]}
        assert is_surfaced_cf_scene(cf) is True, pat
        cf["name"] = "Named"
        assert is_surfaced_cf_scene(cf) is True, pat
    # missing pattern is treated as not-button-backed → surfaced
    assert is_surfaced_cf_scene({"outputs": []}) is True
