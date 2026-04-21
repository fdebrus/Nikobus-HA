"""Config flow for Nikobus integration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DOMAIN,
)
from .coordinator import NikobusConfigEntry
from .exceptions import NikobusConnectionError
from nikobus_connect import NikobusConnect
from nikobus_connect.discovery import find_module

_LOGGER = logging.getLogger(__name__)

# Module hardware capabilities: which HA entity types each module type can back.
_MODULE_ENTITY_TYPES: dict[str, list[str]] = {
    "switch_module": ["switch", "light", "none"],
    "dimmer_module": ["light", "none"],
    "roller_module": ["cover", "switch", "light", "none"],
}

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def _validate_optional_hex6(value: Any) -> str:
    """Accept an empty string or a 6-char hex bus address."""
    if value is None:
        return ""
    text = str(value).strip().upper()
    if text == "":
        return ""
    if not _HEX_RE.match(text):
        raise vol.Invalid("invalid_hex_address")
    return text


_MODULE_TYPE_ORDER: dict[str, int] = {
    "switch_module": 0,
    "dimmer_module": 1,
    "roller_module": 2,
}


def _module_type_order(module_type: str | None) -> int:
    return _MODULE_TYPE_ORDER.get(module_type or "", 99)


def _module_label(address: str, entry: dict[str, Any]) -> str:
    """Render a user-facing label for the module picker."""
    desc = entry.get("description") or f"Module {address}"
    module_type = entry.get("module_type") or "module"
    pretty_type = module_type.replace("_", " ").title()
    return f"{pretty_type} — {address} — {desc}"


def _set_or_drop(mapping: dict[str, Any], key: str, value: str) -> None:
    """Store ``value`` when non-empty; otherwise remove the key entirely."""
    if value:
        mapping[key] = value
    else:
        mapping.pop(key, None)


def _set_time_or_drop(mapping: dict[str, Any], key: str, value: Any) -> None:
    """Store an operation-time value (as a string, to match legacy shape)."""
    if value in (None, ""):
        mapping.pop(key, None)
        return
    try:
        as_int = int(float(value))
    except (TypeError, ValueError):
        mapping.pop(key, None)
        return
    if as_int <= 0:
        mapping.pop(key, None)
        return
    mapping[key] = str(as_int)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

# ---------------------------------------------------------------------------
# Reusable schema fragments
# ---------------------------------------------------------------------------

def _hardware_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(
            CONF_HAS_FEEDBACK_MODULE,
            default=defaults.get(CONF_HAS_FEEDBACK_MODULE, False),
        ): bool,
        vol.Optional(
            CONF_PRIOR_GEN3,
            default=defaults.get(CONF_PRIOR_GEN3, False),
        ): bool,
    })


def _polling_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(
            CONF_REFRESH_INTERVAL,
            default=defaults.get(CONF_REFRESH_INTERVAL, 120),
        ): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
    })


def _needs_polling(user_input: dict[str, Any]) -> bool:
    """Return True when the selected hardware requires a polling interval."""
    return not (
        user_input.get(CONF_HAS_FEEDBACK_MODULE) or user_input.get(CONF_PRIOR_GEN3)
    )


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

async def _test_connection(hass: HomeAssistant, connection_string: str) -> None:
    """Open a connection, complete the PC-Link handshake, then close it."""
    conn = NikobusConnect(connection_string)
    try:
        await conn.connect()
    except NikobusConnectionError as err:
        raise ValueError("cannot_connect") from err
    finally:
        await conn.disconnect()


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class NikobusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow: connection → hardware type → (optional) polling."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    # --- Options flow hook --------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: NikobusConfigEntry,
    ) -> NikobusOptionsFlow:
        return NikobusOptionsFlow()

    # --- Step 1: connection string ------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect and validate the connection string."""
        errors: dict[str, str] = {}

        if user_input is not None:
            conn_str = user_input[CONF_CONNECTION_STRING].strip()
            await self.async_set_unique_id(conn_str.lower())
            self._abort_if_unique_id_configured()

            try:
                await _test_connection(self.hass, conn_str)
            except ValueError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Nikobus connection test")
                errors["base"] = "unknown"
            else:
                self._data[CONF_CONNECTION_STRING] = conn_str
                return await self.async_step_hardware()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_CONNECTION_STRING,
                    default=self._data.get(CONF_CONNECTION_STRING, ""),
                ): str,
            }),
            errors=errors,
        )

    # --- Step 2: hardware capabilities -------------------------------------

    async def async_step_hardware(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Determine whether a Feedback Module or legacy PC-Link is present."""
        if user_input is not None:
            self._data.update(user_input)
            if _needs_polling(user_input):
                return await self.async_step_polling()
            self._data.setdefault(CONF_REFRESH_INTERVAL, 120)
            return self._finish()

        return self.async_show_form(
            step_id="hardware",
            data_schema=_hardware_schema(self._data),
        )

    # --- Step 3: polling interval (only without feedback module) -----------

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Choose how often to poll the bus when no Feedback Module is present."""
        if user_input is not None:
            self._data.update(user_input)
            return self._finish()

        return self.async_show_form(
            step_id="polling",
            data_schema=_polling_schema(self._data),
        )

    def _finish(self) -> config_entries.FlowResult:
        return self.async_create_entry(
            title=f"Nikobus ({self._data[CONF_CONNECTION_STRING]})",
            data=self._data,
        )

    # --- Reconfigure (single step, all fields) -----------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Re-test the connection and update all settings in one step."""
        entry = self._get_reconfigure_entry()
        defaults = {**entry.data, **entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _test_connection(
                    self.hass, user_input[CONF_CONNECTION_STRING].strip()
                )
            except ValueError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Nikobus reconfigure test")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_CONNECTION_STRING,
                    default=defaults.get(CONF_CONNECTION_STRING, ""),
                ): str,
                vol.Optional(
                    CONF_HAS_FEEDBACK_MODULE,
                    default=defaults.get(CONF_HAS_FEEDBACK_MODULE, False),
                ): bool,
                vol.Optional(
                    CONF_PRIOR_GEN3,
                    default=defaults.get(CONF_PRIOR_GEN3, False),
                ): bool,
                vol.Optional(
                    CONF_REFRESH_INTERVAL,
                    default=defaults.get(CONF_REFRESH_INTERVAL, 120),
                ): vol.All(cv.positive_int, vol.Range(min=60, max=3600)),
            }),
            errors=errors,
        )

    async def async_step_import(
        self, import_config: dict[str, Any]
    ) -> config_entries.FlowResult:
        return await self.async_step_user(import_config)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class NikobusOptionsFlow(config_entries.OptionsFlow):
    """Change hardware/polling settings and trigger discovery with live progress."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._discovery_task: asyncio.Task[None] | None = None
        self._discovery_kind: str | None = None  # "pc_link" or "module_scan"
        self._edit_module_address: str | None = None
        self._edit_channel_index: int | None = None

    def _current(self) -> dict[str, Any]:
        """Merge entry data + options so defaults reflect the live settings."""
        return {**self.config_entry.data, **self.config_entry.options}

    def _coordinator(self):
        return self.config_entry.runtime_data

    # --- Step 1: main menu --------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Entry point — let the user pick what to do."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "hardware",
                "configure_modules",
                "discovery_pc_link",
                "discovery_modules",
            ],
        )

    # --- Hardware settings (existing flow) ---------------------------------

    async def async_step_hardware(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            self._options.update(user_input)
            if _needs_polling(user_input):
                return await self.async_step_polling()
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="hardware",
            data_schema=_hardware_schema(self._current()),
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(data=self._options)

        return self.async_show_form(
            step_id="polling",
            data_schema=_polling_schema(self._current()),
        )

    # --- Discovery: PC Link inventory --------------------------------------

    async def async_step_discovery_pc_link(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Kick off a PC Link inventory scan and show live progress."""
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        if self._discovery_task is None:
            self._discovery_kind = "pc_link"
            self._discovery_task = self.hass.async_create_task(
                coordinator.start_pc_link_inventory(auto_reload=False)
            )

        return await self._progress_step("discovery_pc_link")

    # --- Discovery: full module scan ---------------------------------------

    async def async_step_discovery_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Kick off a full module-scan discovery and show live progress."""
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        if self._discovery_task is None:
            self._discovery_kind = "module_scan"
            self._discovery_task = self.hass.async_create_task(
                coordinator.start_module_scan(auto_reload=False)
            )

        return await self._progress_step("discovery_modules")

    async def _progress_step(
        self, step_id: str
    ) -> config_entries.FlowResult:
        """Poll the background discovery task and show a progress spinner.

        HA only re-runs the flow step when the ``progress_task`` completes.
        To refresh the displayed progress during a long-running discovery, we
        pass a short "poll" task (sleep 1s) as the ``progress_task``. HA
        re-runs the step when that poll task completes, which lets us read
        the latest coordinator state and return a new spinner with updated
        description placeholders. Meanwhile the real discovery task runs
        independently; once it finishes, the next invocation transitions to
        the done/error step.
        """
        coordinator = self._coordinator()
        task = self._discovery_task

        if task is not None and task.done():
            self._discovery_task = None
            try:
                task.result()
            except Exception as err:
                _LOGGER.error("Discovery failed: %s", err)
                return self.async_show_progress_done(next_step_id="discovery_error")
            return self.async_show_progress_done(next_step_id="discovery_done")

        raw_message = (
            coordinator.discovery_status_message
            if coordinator
            else "Discovery in progress…"
        )
        percent = coordinator.discovery_progress_percent if coordinator else 0
        display = raw_message or "Starting…"

        # Short poll task so HA re-runs this step every 1s to refresh the UI.
        poll_task = self.hass.async_create_task(asyncio.sleep(1))

        kwargs: dict[str, Any] = {
            "step_id": step_id,
            "progress_action": "discovery",
            "description_placeholders": {
                "message": display,
                "percent": str(percent),
            },
        }
        try:
            return self.async_show_progress(progress_task=poll_task, **kwargs)
        except TypeError:
            # Older HA without progress_task support — fall back to plain call.
            return self.async_show_progress(**kwargs)

    # --- Module customization ----------------------------------------------

    async def async_step_configure_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Let the user pick a module to customize."""
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        modules = coordinator.module_storage.data.get("nikobus_module") or {}
        if not modules:
            return self.async_abort(reason="no_modules")

        if user_input is not None:
            self._edit_module_address = str(user_input["module"]).upper()
            return await self.async_step_edit_module()

        options = [
            {
                "value": addr,
                "label": self._module_label(addr, entry),
            }
            for addr, entry in sorted(
                modules.items(),
                key=lambda kv: (
                    _module_type_order(kv[1].get("module_type")),
                    kv[0],
                ),
            )
        ]

        return self.async_show_form(
            step_id="configure_modules",
            data_schema=vol.Schema({
                vol.Required("module"): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
            }),
        )

    async def async_step_edit_module(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit module-level description; pick a channel or finish."""
        coordinator = self._coordinator()
        if coordinator is None or self._edit_module_address is None:
            return self.async_abort(reason="not_loaded")

        module_data = coordinator.module_storage.data
        hit = find_module(module_data, self._edit_module_address)
        if hit is None:
            return self.async_abort(reason="module_not_found")
        address, entry = hit

        channels = entry.get("channels", [])

        if user_input is not None:
            # Persist the module-level description verbatim.
            new_desc = (user_input.get("description") or "").strip()
            if new_desc:
                entry["description"] = new_desc

            selected = user_input.get("channel")
            await coordinator._async_on_module_save()

            if selected and selected != "done":
                try:
                    self._edit_channel_index = int(selected)
                except ValueError:
                    return await self.async_step_edit_module()
                return await self.async_step_edit_channel()

            # Done — close the flow. The entry's update listener reloads.
            merged = {**self.config_entry.options, **self._options}
            return self.async_create_entry(title="", data=merged)

        channel_options = [
            {
                "value": str(idx),
                "label": (
                    f"Channel {idx}: "
                    f"{ch.get('description') or '(no description)'}"
                ),
            }
            for idx, ch in enumerate(channels, start=1)
            if isinstance(ch, dict)
        ]
        channel_options.append({"value": "done", "label": "Finish editing"})

        return self.async_show_form(
            step_id="edit_module",
            data_schema=vol.Schema({
                vol.Optional(
                    "description",
                    default=entry.get("description") or "",
                ): TextSelector(TextSelectorConfig()),
                vol.Required("channel", default="done"): SelectSelector(
                    SelectSelectorConfig(
                        options=channel_options,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }),
            description_placeholders={
                "address": address,
                "module_type": entry.get("module_type", "unknown"),
                "model": entry.get("model") or "unknown",
            },
        )

    async def async_step_edit_channel(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Edit a single channel's user-owned fields."""
        coordinator = self._coordinator()
        if (
            coordinator is None
            or self._edit_module_address is None
            or self._edit_channel_index is None
        ):
            return self.async_abort(reason="not_loaded")

        hit = find_module(coordinator.module_storage.data, self._edit_module_address)
        if hit is None:
            return self.async_abort(reason="module_not_found")
        address, entry = hit
        module_type = entry.get("module_type", "switch_module")
        idx = self._edit_channel_index
        channels = entry.get("channels", [])
        if not (1 <= idx <= len(channels)) or not isinstance(channels[idx - 1], dict):
            return self.async_abort(reason="channel_not_found")
        channel = channels[idx - 1]

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                led_on = _validate_optional_hex6(user_input.get("led_on", ""))
                led_off = _validate_optional_hex6(user_input.get("led_off", ""))
            except vol.Invalid:
                errors["base"] = "invalid_hex_address"
            else:
                entity_type = user_input.get("entity_type")
                if entity_type and entity_type != "none":
                    channel["entity_type"] = entity_type
                else:
                    channel.pop("entity_type", None)

                channel["description"] = (
                    user_input.get("description") or channel.get("description", "")
                )
                _set_or_drop(channel, "led_on", led_on)
                _set_or_drop(channel, "led_off", led_off)

                if module_type == "roller_module":
                    up = user_input.get("operation_time_up")
                    down = user_input.get("operation_time_down")
                    _set_time_or_drop(channel, "operation_time_up", up)
                    _set_time_or_drop(channel, "operation_time_down", down)

                await coordinator._async_on_module_save()
                # Return to the module step so the user can edit another
                # channel or finish.
                self._edit_channel_index = None
                return await self.async_step_edit_module()

        allowed = _MODULE_ENTITY_TYPES.get(module_type, ["switch", "light", "none"])
        current_entity_type = channel.get("entity_type") or "none"
        if current_entity_type not in allowed:
            current_entity_type = allowed[0]

        schema_dict: dict[Any, Any] = {
            vol.Required(
                "entity_type",
                default=current_entity_type,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": v, "label": v.capitalize()} for v in allowed],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                "description",
                default=channel.get("description") or "",
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                "led_on",
                default=channel.get("led_on") or "",
            ): TextSelector(TextSelectorConfig()),
            vol.Optional(
                "led_off",
                default=channel.get("led_off") or "",
            ): TextSelector(TextSelectorConfig()),
        }

        if module_type == "roller_module":
            schema_dict[vol.Optional(
                "operation_time_up",
                default=_coerce_int(channel.get("operation_time_up"), 30),
            )] = NumberSelector(
                NumberSelectorConfig(
                    min=1, max=600, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            )
            schema_dict[vol.Optional(
                "operation_time_down",
                default=_coerce_int(channel.get("operation_time_down"), 30),
            )] = NumberSelector(
                NumberSelectorConfig(
                    min=1, max=600, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s"
                )
            )

        return self.async_show_form(
            step_id="edit_channel",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "address": address,
                "channel": str(idx),
                "module_type": module_type,
            },
            errors=errors,
        )

    async def async_step_discovery_done(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Finalize the flow after a successful discovery.

        Use ``async_create_entry`` with empty options so HA closes the
        flow cleanly via its standard success dialog. The coordinator's
        auto-reload (triggered by the options update listener) picks up
        the newly-discovered entities. This avoids the "Invalid flow
        specified" error that async_abort + manual reload can cause.
        """
        # Merge back the existing options so the update listener fires
        # even if self._options is empty.
        merged = {**self.config_entry.options, **self._options}
        return self.async_create_entry(title="", data=merged)

    async def async_step_discovery_error(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Close the flow after a failed discovery, surfacing the error."""
        coordinator = self._coordinator()
        err = (
            coordinator.discovery_last_error
            if coordinator
            else "Unknown error"
        ) or "Unknown error"
        return self.async_abort(
            reason="discovery_error",
            description_placeholders={"error": err},
        )
