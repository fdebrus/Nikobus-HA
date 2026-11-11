"""Tools a language model can use to understand and operate the installation.

Home Assistant's own tools let a model see entities and call actions.
These add what only this integration knows: the wiring. They are
offered as an LLM API of their own, which a conversation agent is given
next to Assist, and which Home Assistant's MCP server passes on.

Every tool reads the inventory, except the one that presses a key.
None reads or writes a module's programming.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .const import DOMAIN
from .describe import Describer

if TYPE_CHECKING:
    from .hub import NikobusHub

API_PROMPT = (
    "Tools for a Nikobus installation, a wired home-automation bus. Output modules hold the "
    "outputs: a switch module's are relays, a dimmer's are dimmed, a roller module's move "
    "shutters. Wall plates, remotes and inputs have keys; pressing a key puts a telegram, its "
    "six-digit hexadecimal address, on the bus. A link, stored in a module, says what an output "
    "does when it sees a key's telegram: its mode (on/off, impulse, delayed off, light scene, "
    "open-stop-close and so on) and its times. A key can drive several outputs and an output can "
    "be driven by several keys. To explain why an output behaves as it does, look at its links. "
    "Start with nikobus_overview to find entity ids. To switch an output, use Home Assistant's "
    "own tools on its entity; press a key only when the point is the key itself."
)

_READ = llm.ToolAnnotations(read_only=True, destructive=False, idempotent=True, open_world=False)
_PRESS = llm.ToolAnnotations(read_only=False, destructive=False, idempotent=False, open_world=False)
_NONE = probatio.Schema({})


@dataclass(frozen=True, slots=True)
class _Spec:
    name: str
    title: str
    description: str
    parameters: probatio.Schema
    annotations: llm.ToolAnnotations
    call: Callable[[NikobusHub, dict[str, Any]], Any]


def _output(hub: NikobusHub, args: dict[str, Any]) -> dict[str, Any]:
    if (found := Describer(hub).find_output(args["entity_id"])) is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="llm_not_an_output",
            translation_placeholders={"entity_id": args["entity_id"]},
        )
    return found


def _key(hub: NikobusHub, args: dict[str, Any]) -> dict[str, Any]:
    if (found := Describer(hub).find_key(args["key"])) is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="llm_not_a_key",
            translation_placeholders={"key": args["key"]},
        )
    return found


async def _press(hub: NikobusHub, args: dict[str, Any]) -> dict[str, Any]:
    await hub.async_press(args["address"])
    return {"pressed": args["address"].upper()}


_SPECS = (
    _Spec(
        "nikobus_overview",
        "Nikobus overview",
        "List every Nikobus output (entity id, name, area, kind, value, number of keys that "
        "drive it), every scene and every plate. Use it first, to find what to ask about.",
        _NONE,
        _READ,
        lambda hub, _args: Describer(hub).overview(),
    ),
    _Spec(
        "nikobus_output",
        "Nikobus output",
        "Explain one output: which keys drive it, in which mode and with which times, which "
        "scenes include it, its module and its current value.",
        probatio.Schema(
            {
                probatio.Required(
                    "entity_id", description="The entity id of the light, switch, cover or fan."
                ): str
            }
        ),
        _READ,
        _output,
    ),
    _Spec(
        "nikobus_key",
        "Nikobus key",
        "Explain one key: its plate, and every output it drives with the mode and the times of "
        "each link.",
        probatio.Schema(
            {
                probatio.Required(
                    "key",
                    description="The key's event entity id, or its six-digit hexadecimal address.",
                ): str
            }
        ),
        _READ,
        _key,
    ),
    _Spec(
        "nikobus_scenes",
        "Nikobus scenes",
        "List the central functions and light scenes: the keys that activate each and the "
        "outputs each sets.",
        _NONE,
        _READ,
        lambda hub, _args: {"scenes": Describer(hub).scenes()},
    ),
    _Spec(
        "nikobus_health",
        "Nikobus health",
        "Report whether the bus is connected, whether a discovery runs, the PC-Link's clock "
        "drift, the result of the last programming check and the open repairs.",
        _NONE,
        _READ,
        lambda hub, _args: Describer(hub).health(),
    ),
    _Spec(
        "nikobus_press_key",
        "Press a Nikobus key",
        "Put a key's telegram on the bus, as a press of that key does. Every output linked to "
        "the key reacts in its own mode, so check the key first. To switch one output, use its "
        "entity instead.",
        probatio.Schema(
            {
                probatio.Required(
                    "address", description="The key's six-digit hexadecimal address."
                ): probatio.All(str, probatio.Match(r"^[0-9A-Fa-f]{6}$"))
            }
        ),
        _PRESS,
        _press,
    ),
)


class NikobusTool(llm.Tool):
    """One tool, bound to the hub of the entry."""

    def __init__(self, hub: NikobusHub, spec: _Spec) -> None:
        self.name = spec.name
        self.title = spec.title
        self.description = spec.description
        self.parameters = spec.parameters
        self.annotations = spec.annotations
        self.integration = DOMAIN
        self._hub = hub
        self._call = spec.call

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> llm.ToolResult:
        """Validate the arguments and answer from the inventory."""
        result = self._call(self._hub, self.parameters(tool_input.tool_args))
        if hasattr(result, "__await__"):
            result = await result
        return llm.ToolResult(data=result)


@dataclass(slots=True, kw_only=True)
class NikobusLLMAPI(llm.API):
    """The tools of one installation."""

    hub: NikobusHub
    _tools: list[llm.Tool] = field(init=False)

    def __post_init__(self) -> None:
        self._tools = [NikobusTool(self.hub, spec) for spec in _SPECS]

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """The tools; nothing about them depends on who asks."""
        return llm.APIInstance(api=self, api_prompt=API_PROMPT, llm_context=llm_context, tools=self._tools)


@callback
def async_register_llm_api(hass: HomeAssistant, hub: NikobusHub) -> CALLBACK_TYPE:
    """Offer the tools while the entry is loaded."""
    return llm.async_register_api(hass, NikobusLLMAPI(hass=hass, id=DOMAIN, name="Nikobus NG", hub=hub))
