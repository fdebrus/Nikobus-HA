"""Config flow for Nikobus integration."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from nikobus_connect import NikobusConnect
from nikobus_connect.discovery import find_module

from .const import (
    CONF_CONNECTION_STRING,
    CONF_HAS_FEEDBACK_MODULE,
    CONF_PRESS_REPEAT,
    CONF_PRIOR_GEN3,
    CONF_REFRESH_INTERVAL,
    DEFAULT_PRESS_REPEAT,
    DOMAIN,
)
from .coordinator import (
    NKB_IMPORT_CATEGORIES,
    NikobusConfigEntry,
    NikobusDataCoordinator,
)
from .exceptions import NikobusConnectionError

_LOGGER = logging.getLogger(__name__)


class _NkbUploadError(Exception):
    """A `.nkb` upload failed; ``key`` is the translation/error key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# Module hardware capabilities: which HA entity types each module type can back.
#
# Per-module dropdown choices in the "Customize a module" options flow:
#
#   - "default"  — use the module's natural entity type (switch, light, or
#                  cover depending on the module). Stored as absent
#                  ``entity_type`` on the channel; the router's
#                  ``_resolve_entity_type`` falls through to the hardware
#                  default.
#   - a concrete override (``switch`` / ``light`` / ``cover``) — only listed
#     when the hardware can actually back that entity type.
#   - "disabled" — skip the channel entirely (no HA entity created). Checked
#                  in ``router.py`` alongside the legacy ``not_in_use``
#                  description-prefix convention.
_MODULE_ENTITY_TYPES: dict[str, list[str]] = {
    "switch_module": ["default", "light", "disabled"],
    "dimmer_module": ["default", "disabled"],
    "roller_module": ["default", "switch", "light", "disabled"],
}

# The implicit default entity type a channel resolves to when no explicit
# override is stored — declared in router._resolve_entity_type. The
# translations/*.json selector.entity_type_<module_type>.options.default
# labels ("Default (switch)" / …) document the mapping to the user.

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


# User-overridable module classifications shown in the
# Customize-a-module step. Mirrors the buckets the router
# recognises (switch / dimmer / roller). ``other_module`` is
# included so users with a misclassified non-output module
# (PC Logic, Feedback, etc.) can park it there until library
# discovery learns its real type.
_OVERRIDABLE_MODULE_TYPES: tuple[str, ...] = (
    "switch_module",
    "dimmer_module",
    "roller_module",
    "other_module",
)

# Default channel count by module_type — matches the library's
# inventory defaults (mapping.DEVICE_TYPES). Only used when the
# user changes ``module_type`` and the existing ``channels`` list
# is shorter than the new type's expected count: we extend it to
# the right length with ``not_in_use`` placeholders so the router
# can build the full set of entities for the new classification.
# Existing channel entries are preserved verbatim — never
# truncated, never overwritten.
_DEFAULT_CHANNELS_BY_TYPE: dict[str, int] = {
    "switch_module": 12,
    "dimmer_module": 12,
    "roller_module": 6,
    "other_module": 0,
}


# Module types whose "channels" are bus-event sources, not output loads —
# PC-Logic exposes 6 logical inputs, the 05-206 modular interface exposes
# 6 wired inputs. The placeholder description reads "input_N" instead of
# the generic "output_N" used for switch / dimmer / roller. Mirrors the
# library's ``_INPUT_MODULE_TYPES`` in ``fileio.py``.
_INPUT_MODULE_TYPES: frozenset[str] = frozenset({"pc_logic", "interface_module"})


def _make_default_channel(module_type: str, index: int) -> dict[str, Any]:
    """Build a placeholder channel entry for a freshly-padded slot.

    Mirrors ``nikobus_connect.discovery.fileio._default_channel`` so a
    re-discovery merge doesn't see drift between user-padded entries
    and library-padded ones.
    """
    label = "input" if module_type in _INPUT_MODULE_TYPES else "output"
    channel: dict[str, Any] = {"description": f"not_in_use {label}_{index}"}
    if module_type == "roller_module":
        channel["operation_time_up"] = "30"
    return channel


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
        vol.Optional(
            CONF_PRESS_REPEAT,
            default=defaults.get(CONF_PRESS_REPEAT, DEFAULT_PRESS_REPEAT),
        ): vol.All(cv.positive_int, vol.Range(min=1, max=10)),
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


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------

class NikobusOptionsFlow(config_entries.OptionsFlow):
    """Change hardware/polling settings and customize discovered modules.

    Discovery itself (PC-Link inventory + module-link scan) is no
    longer reachable from this options flow; both actions live on the
    Nikobus Bridge device's button entities (`1. Load Project Overview`
    and `2. Load Existing Installation`). Routing scans through the
    options flow on top of the device buttons gave users two
    different entry points for the same operation, and the flow's
    progress-dialog → terminal-step plumbing was where the
    "Invalid flow specified" failure mode kept resurfacing.
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._edit_module_address: str | None = None
        self._edit_channel_index: int | None = None

    def _current(self) -> dict[str, Any]:
        """Merge entry data + options so defaults reflect the live settings."""
        return {**self.config_entry.data, **self.config_entry.options}

    def _coordinator(self) -> NikobusDataCoordinator | None:
        return self.config_entry.runtime_data

    # --- Step 1: main menu --------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Entry point — let the user pick what to do.

        Discovery actions (PC-Link inventory, full module scan) live
        on the Nikobus Bridge device's button entities, not in this
        menu. Keeping a single entry point avoids the "two ways to do
        the same thing" UX and the progress-flow flakiness that
        accompanied the options-flow path.

        ``configure_modules`` is always offered. For installs running
        on manual-config files (no PC-Link), edits made through this
        UI will be overwritten on the next reload — the files remain
        the declarative source of truth. Users on manual-config should
        edit the JSON files directly to make customisations stick.
        """
        menu_options = ["hardware", "configure_modules", "upload_nkb", "import_nkb"]
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
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

    # --- Upload the .nkb project file --------------------------------------

    async def async_step_upload_nkb(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Upload a Nikobus ``.nkb`` project file into the config dir.

        Prompts for a file (any name), validates it parses as a real
        ``.nkb`` (a ZIP holding the MS Access DB), and saves it as the
        canonical ``nikobus.nkb`` in the HA config directory so the
        **Import Names from .nkb** button can pick it up. Doesn't change
        any options; the file *is* the result.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._save_uploaded_nkb(user_input["file"])
            except _NkbUploadError as err:
                errors["base"] = err.key
            else:
                # No options change — just close the flow with success.
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )

        return self.async_show_form(
            step_id="upload_nkb",
            data_schema=vol.Schema(
                {vol.Required("file"): FileSelector(FileSelectorConfig(accept=".nkb"))}
            ),
            errors=errors,
        )

    async def _save_uploaded_nkb(self, file_id: str) -> None:
        """Validate the uploaded file is a real ``.nkb`` and save it as
        ``nikobus.nkb`` in the config dir. Runs the blocking file work in
        an executor; raises :class:`_NkbUploadError` on a bad file."""
        from homeassistant.components.file_upload import process_uploaded_file

        from .nkbnames import CANONICAL_NKB_FILENAME, parse_nkb

        hass = self.hass
        dest = hass.config.path(CANONICAL_NKB_FILENAME)

        def _validate_and_save() -> None:
            import shutil

            with process_uploaded_file(hass, file_id) as src:
                try:
                    parse_nkb(src)  # parses → it's a usable .nkb
                except Exception as err:  # noqa: BLE001 — surface as flow error
                    raise _NkbUploadError("invalid_nkb") from err
                shutil.copyfile(src, dest)

        try:
            await hass.async_add_executor_job(_validate_and_save)
        except _NkbUploadError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Failed to save uploaded .nkb file")
            raise _NkbUploadError("upload_failed") from err

    # --- Import names / channels / Areas / scenes from the .nkb ------------

    async def async_step_import_nkb(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Apply the `.nkb` selectively, with an optional overwrite.

        Lets the user choose which categories to import (device names,
        per-channel names, Areas, scenes) and whether to overwrite names /
        Areas they've already set. The ``Import Names from .nkb`` bridge
        button remains the quick "everything, non-destructive" path.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            cats = {c for c in NKB_IMPORT_CATEGORIES if user_input.get(c)}
            if not cats:
                errors["base"] = "nkb_nothing_selected"
            else:
                try:
                    await self._coordinator().async_import_nkb_names(
                        categories=cats, overwrite=bool(user_input.get("overwrite"))
                    )
                except HomeAssistantError as err:
                    errors["base"] = getattr(err, "translation_key", None) \
                        or "nkb_parse_failed"
                else:
                    return self.async_create_entry(
                        title="", data=dict(self.config_entry.options)
                    )

        return self.async_show_form(
            step_id="import_nkb",
            data_schema=vol.Schema(
                {
                    vol.Optional("device_names", default=True): bool,
                    vol.Optional("channel_names", default=True): bool,
                    vol.Optional("areas", default=True): bool,
                    vol.Optional("scenes", default=True): bool,
                    vol.Optional("overwrite", default=False): bool,
                }
            ),
            errors=errors,
        )

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
                "label": _module_label(addr, entry),
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

            # Persist a manual module_type override.
            #
            # Discovery normally owns ``module_type`` and re-merges it
            # from the inventory's ``device_type`` byte every time the
            # user runs *1. Load Project Overview*. That round-trip
            # clobbers any UI override done here — by design today (see
            # ``nikobus_connect.discovery.fileio.merge_module_inventory``
            # at the line ``existing["module_type"] = module_type``). A
            # follow-up library change will respect non-`other_module`
            # values written by the integration; until that lands, this
            # override survives until the next inventory run, which is
            # enough to unblock users hitting a misreported device_type
            # byte (e.g. dimmer self-reported as roller).
            new_type = (user_input.get("module_type") or "").strip()
            if new_type and new_type != entry.get("module_type"):
                entry["module_type"] = new_type
                expected = _DEFAULT_CHANNELS_BY_TYPE.get(new_type, 0)
                channels_list = entry.get("channels")
                if not isinstance(channels_list, list):
                    channels_list = []
                # Extend (never truncate) to the new type's default
                # channel count. Preserves user-edited descriptions /
                # entity_type / LED triggers / travel times on every
                # existing slot.
                while len(channels_list) < expected:
                    channels_list.append(
                        _make_default_channel(new_type, len(channels_list) + 1)
                    )
                entry["channels"] = channels_list

            selected = user_input.get("channel")
            await coordinator.async_on_module_save()

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

        current_type = entry.get("module_type") or "other_module"
        if current_type not in _OVERRIDABLE_MODULE_TYPES:
            current_type = "other_module"

        return self.async_show_form(
            step_id="edit_module",
            data_schema=vol.Schema({
                vol.Optional(
                    "description",
                    default=entry.get("description") or "",
                ): TextSelector(TextSelectorConfig()),
                vol.Required(
                    "module_type",
                    default=current_type,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(_OVERRIDABLE_MODULE_TYPES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="module_type",
                    )
                ),
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
                if entity_type and entity_type != "default":
                    # "disabled" and concrete overrides ("switch" / "light" /
                    # "cover") are stored verbatim on the channel.
                    channel["entity_type"] = entity_type
                else:
                    # "default" — drop the override so the router falls
                    # through to the hardware default.
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

                await coordinator.async_on_module_save()
                # Return to the module step so the user can edit another
                # channel or finish.
                self._edit_channel_index = None
                return await self.async_step_edit_module()

        allowed = _MODULE_ENTITY_TYPES.get(module_type, ["default", "light", "disabled"])
        # Absent entity_type on the channel resolves to the default at runtime,
        # so present it that way in the dropdown too. Any pre-existing value
        # that no longer appears in the module's allowed list (e.g. a roller
        # channel stored as "switch" that is no longer offered) falls back to
        # "default" rather than silently sticking on an off-list value.
        current_entity_type = channel.get("entity_type") or "default"
        if current_entity_type not in allowed:
            current_entity_type = "default"

        schema_dict: dict[Any, Any] = {
            vol.Required(
                "entity_type",
                default=current_entity_type,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(allowed),
                    mode=SelectSelectorMode.DROPDOWN,
                    # Labels come from translations/*.json under
                    # selector.entity_type_<module_type>.options.<value>.
                    # One key per module_type so the "Default (…)" suffix
                    # can name the module's hardware-default entity type.
                    translation_key=f"entity_type_{module_type}",
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

