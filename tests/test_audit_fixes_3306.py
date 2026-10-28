"""Fixes from the 3.30.5 audit (3.30.6)."""

from __future__ import annotations

import asyncio
import json
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.nikobus import async_setup_entry, async_unload_entry
from custom_components.nikobus.const import DOMAIN
from custom_components.nikobus.entity import write_module_channels

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "nikobus"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestSetupStopsTheCoordinatorOnAnyFailure(unittest.TestCase):
    """A failure after the port is opened used to orphan the connection,
    and the retry then found the port locked."""

    def _setup(self, failing_step: str):
        coord = MagicMock()
        coord.connect = AsyncMock()
        coord.stop = AsyncMock()
        coord.async_config_entry_first_refresh = AsyncMock()
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        if failing_step == "platforms":
            hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError("platform boom")
        else:
            coord.async_config_entry_first_refresh.side_effect = RuntimeError("refresh boom")
        entry = MagicMock()
        with (
            patch("custom_components.nikobus.NikobusDataCoordinator", return_value=coord),
            patch("custom_components.nikobus._register_hub_device"),
            patch("custom_components.nikobus._async_restore_button_customisations", AsyncMock()),
            patch("custom_components.nikobus._async_cleanup_orphan_entities", AsyncMock()),
            patch("custom_components.nikobus.async_call_later"),
            patch("custom_components.nikobus.async_track_time_interval"),
            self.assertRaises(RuntimeError),
        ):
            _run(async_setup_entry(hass, entry))
        return coord

    def test_platform_setup_failure_stops_the_coordinator(self):
        self._setup("platforms").stop.assert_awaited_once()

    def test_first_refresh_failure_stops_the_coordinator(self):
        self._setup("refresh").stop.assert_awaited_once()


class TestUnloadDropsDerivedState(unittest.TestCase):
    def test_routing_cache_does_not_outlive_the_entry(self):
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.data = {DOMAIN: {"E1": {"routing": {"light": []}}}}
        entry = MagicMock(entry_id="E1")
        entry.runtime_data.stop = AsyncMock()
        self.assertTrue(_run(async_unload_entry(hass, entry)))
        self.assertNotIn("E1", hass.data[DOMAIN])


class TestWriteModuleChannels(unittest.TestCase):
    def _coord(self, buffer):
        coord = MagicMock()
        coord.get_module_channel_count = MagicMock(return_value=12)
        coord.get_bytearray_state = MagicMock(side_effect=lambda a, ch: buffer[ch - 1])
        coord.nikobus_command.set_output_state = AsyncMock()
        return coord

    def test_named_channels_are_written_whatever_the_buffer_says(self):
        coord = self._coord(bytearray([0xFF] + [0] * 11))
        state = bytearray([0xFF] + [0] * 11)
        _run(write_module_channels(coord, "CF58", state, {1, 5}, None))
        writes = [c.args for c in coord.nikobus_command.set_output_state.await_args_list]
        self.assertEqual(writes, [("CF58", 1, 0xFF), ("CF58", 5, 0)])

    def test_without_names_only_differences_are_written(self):
        coord = self._coord(bytearray([0xFF] + [0] * 11))
        state = bytearray([0, 0, 0xFF] + [0] * 9)
        _run(write_module_channels(coord, "CF58", state, None, None))
        writes = [c.args for c in coord.nikobus_command.set_output_state.await_args_list]
        self.assertEqual(writes, [("CF58", 1, 0), ("CF58", 3, 0xFF)])

    def test_the_failure_handler_rides_along_and_no_handler_means_no_write(self):
        coord = self._coord(bytearray(12))
        handler = MagicMock()
        _run(write_module_channels(coord, "CF58", bytearray([1] * 12), {2}, handler))
        self.assertIs(coord.nikobus_command.set_output_state.await_args.kwargs["failure_handler"], handler)
        coord.nikobus_command = None
        _run(write_module_channels(coord, "CF58", bytearray([1] * 12), {2}, handler))  # no error


class TestCFSceneIgnoresPolls(unittest.TestCase):
    def test_coordinator_update_writes_nothing(self):
        from custom_components.nikobus.scene import NikobusCFSceneEntity

        coord = MagicMock()
        e = NikobusCFSceneEntity(coord, "384100", {"pattern": "switch_pair", "outputs": []})
        e.async_write_ha_state = MagicMock()
        e._handle_coordinator_update()
        e.async_write_ha_state.assert_not_called()


class TestTranslationsAreComplete(unittest.TestCase):
    """Every language carries every key, and every translation key the
    code raises or registers exists."""

    @staticmethod
    def _flat(d, prefix=""):
        out = {}
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(TestTranslationsAreComplete._flat(v, key))
            else:
                out[key] = v
        return out

    def test_fr_and_nl_match_en(self):
        base = PKG / "translations"
        en = self._flat(json.loads((base / "en.json").read_text()))
        for lang in ("fr", "nl"):
            other = self._flat(json.loads((base / f"{lang}.json").read_text()))
            self.assertEqual(set(en) - set(other), set(), f"missing in {lang}")
            self.assertEqual(set(other) - set(en), set(), f"extra in {lang}")

    def test_every_raised_translation_key_exists(self):
        en = json.loads((PKG / "translations" / "en.json").read_text())
        known = set(en.get("exceptions", {})) | set(en.get("issues", {}))
        pattern = re.compile(r'translation_domain=DOMAIN,\s*(?:is_fixable=\w+,\s*severity=[^,]+,\s*)?translation_key=f?"([a-z_]+)')
        missing = set()
        for path in PKG.glob("*.py"):
            if path.name in ("config_flow.py", "repairs.py"):
                continue
            for key in pattern.findall(path.read_text()):
                if key not in known:
                    missing.add(f"{path.name}:{key}")
        self.assertEqual(missing, set())

    def test_every_service_has_a_translation_and_an_icon(self):
        import custom_components.nikobus as pkg
        from custom_components.nikobus import __init__ as init_module  # noqa: F401
        services = {v for k, v in vars(pkg).items() if k.startswith("SERVICE_")}
        en = json.loads((PKG / "translations" / "en.json").read_text())["services"]
        icons = json.loads((PKG / "icons.json").read_text())["services"]
        self.assertEqual(services - set(en), set())
        self.assertEqual(services - set(icons), set())
