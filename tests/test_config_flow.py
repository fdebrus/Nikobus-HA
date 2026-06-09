"""Config / reconfigure / options flow step tests.

Phase-1 gold matrix: success path, connection errors, the hardware
branch (feedback module present vs polling needed), reconfigure, and
the options menu. The conftest flow stubs return HA-shaped FlowResult
dicts, so each step is exercised as a plain coroutine.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.data_entry_flow import AbortFlow

from custom_components.nikobus.config_flow import (
    NikobusConfigFlow,
    NikobusOptionsFlow,
)
from custom_components.nikobus.const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
)

_TEST_CONN = "custom_components.nikobus.config_flow._test_connection"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _flow() -> NikobusConfigFlow:
    flow = NikobusConfigFlow()
    flow.hass = MagicMock()
    flow._configured_unique_ids = set()
    return flow


class TestConfigFlowUser(unittest.TestCase):
    def test_shows_form_without_input(self):
        result = _run(_flow().async_step_user(None))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["errors"], {})

    def test_success_advances_to_hardware(self):
        flow = _flow()
        with patch(_TEST_CONN, new=AsyncMock()) as conn:
            result = _run(
                flow.async_step_user({CONF_CONNECTION_STRING: " 192.168.2.50:9999 "})
            )
        # Connection string is stripped before testing + storing.
        conn.assert_awaited_once_with(flow.hass, "192.168.2.50:9999")
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "hardware")
        self.assertEqual(flow._data[CONF_CONNECTION_STRING], "192.168.2.50:9999")

    def test_cannot_connect_shows_error(self):
        flow = _flow()
        with patch(_TEST_CONN, new=AsyncMock(side_effect=ValueError("nope"))):
            result = _run(
                flow.async_step_user({CONF_CONNECTION_STRING: "/dev/ttyUSB0"})
            )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})

    def test_unexpected_error_shows_unknown(self):
        flow = _flow()
        with patch(_TEST_CONN, new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = _run(
                flow.async_step_user({CONF_CONNECTION_STRING: "/dev/ttyUSB0"})
            )
        self.assertEqual(result["errors"], {"base": "unknown"})

    def test_duplicate_connection_aborts(self):
        flow = _flow()
        # unique_id is the lower-cased connection string.
        flow._configured_unique_ids = {"192.168.2.50:9999"}
        with patch(_TEST_CONN, new=AsyncMock()):
            with self.assertRaises(AbortFlow) as ctx:
                _run(
                    flow.async_step_user(
                        {CONF_CONNECTION_STRING: "192.168.2.50:9999"}
                    )
                )
        self.assertEqual(ctx.exception.reason, "already_configured")


class TestConfigFlowHardwareBranch(unittest.TestCase):
    def _flow_with_connection(self) -> NikobusConfigFlow:
        flow = _flow()
        flow._data[CONF_CONNECTION_STRING] = "host:1234"
        return flow

    def test_feedback_module_skips_polling(self):
        # With a feedback module the bus pushes state — no polling step,
        # the entry is created immediately with the default interval.
        flow = self._flow_with_connection()
        result = _run(
            flow.async_step_hardware(
                {CONF_HAS_FEEDBACK_MODULE: True, CONF_PRIOR_GEN3: False}
            )
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Nikobus (host:1234)")
        self.assertTrue(result["data"][CONF_HAS_FEEDBACK_MODULE])
        self.assertEqual(result["data"][CONF_REFRESH_INTERVAL], 120)

    def test_no_feedback_module_requires_polling_step(self):
        flow = self._flow_with_connection()
        result = _run(
            flow.async_step_hardware(
                {CONF_HAS_FEEDBACK_MODULE: False, CONF_PRIOR_GEN3: False}
            )
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "polling")

        result = _run(flow.async_step_polling({CONF_REFRESH_INTERVAL: 300}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_REFRESH_INTERVAL], 300)
        self.assertFalse(result["data"][CONF_HAS_FEEDBACK_MODULE])


class TestReconfigureFlow(unittest.TestCase):
    def _flow_with_entry(self) -> tuple[NikobusConfigFlow, MagicMock]:
        flow = _flow()
        entry = MagicMock()
        entry.data = {CONF_CONNECTION_STRING: "old:1234"}
        entry.options = {}
        flow._reconfigure_entry = entry
        return flow, entry

    def test_success_updates_entry_and_aborts(self):
        flow, entry = self._flow_with_entry()
        new_input = {
            CONF_CONNECTION_STRING: "new:5678",
            CONF_HAS_FEEDBACK_MODULE: True,
            CONF_PRIOR_GEN3: False,
            CONF_REFRESH_INTERVAL: 120,
        }
        with patch(_TEST_CONN, new=AsyncMock()):
            result = _run(flow.async_step_reconfigure(new_input))
        self.assertEqual(result["type"], "abort")
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(entry.data, new_input)

    def test_cannot_connect_keeps_form(self):
        flow, entry = self._flow_with_entry()
        with patch(_TEST_CONN, new=AsyncMock(side_effect=ValueError("nope"))):
            result = _run(
                flow.async_step_reconfigure(
                    {CONF_CONNECTION_STRING: "bad:0"}
                )
            )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "reconfigure")
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        # Entry untouched on failure.
        self.assertEqual(entry.data, {CONF_CONNECTION_STRING: "old:1234"})


class TestOptionsFlow(unittest.TestCase):
    def _options_flow(self) -> NikobusOptionsFlow:
        flow = NikobusOptionsFlow()
        entry = MagicMock()
        entry.data = {
            CONF_CONNECTION_STRING: "host:1234",
            CONF_HAS_FEEDBACK_MODULE: False,
            CONF_REFRESH_INTERVAL: 120,
        }
        entry.options = {}
        flow.config_entry = entry
        return flow

    def test_init_shows_menu(self):
        result = _run(self._options_flow().async_step_init(None))
        self.assertEqual(result["type"], "menu")
        self.assertEqual(
            result["menu_options"],
            ["hardware", "configure_modules", "upload_nkb", "import_nkb"],
        )

    def test_hardware_with_feedback_creates_entry(self):
        flow = self._options_flow()
        result = _run(
            flow.async_step_hardware(
                {CONF_HAS_FEEDBACK_MODULE: True, CONF_PRIOR_GEN3: False}
            )
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(result["data"][CONF_HAS_FEEDBACK_MODULE])

    def test_hardware_without_feedback_goes_to_polling(self):
        flow = self._options_flow()
        result = _run(
            flow.async_step_hardware(
                {CONF_HAS_FEEDBACK_MODULE: False, CONF_PRIOR_GEN3: False}
            )
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "polling")
        result = _run(flow.async_step_polling({CONF_REFRESH_INTERVAL: 600}))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][CONF_REFRESH_INTERVAL], 600)
