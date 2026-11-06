"""The blueprints shipped in ``blueprints/`` match what the integration fires.

Home Assistant is not installed for the tests, so the blueprint is not run;
these checks read its YAML and pin it to the integration's events, its
inputs to its body, and its ``source_url`` to its place in the repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINTS = sorted((ROOT / "blueprints").rglob("*.yaml"))
EVENT_SOURCES = [ROOT / "custom_components" / "nikobus" / name for name in ("const.py", "nkbactuator.py")]


class _InputRef(str):
    """The name an ``!input`` tag refers to."""


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!input", lambda loader, node: _InputRef(loader.construct_scalar(node)))


def _load(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def _declared_inputs(inputs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for key, spec in inputs.items():
        if isinstance(spec, dict) and "input" in spec:
            declared.update(_declared_inputs(spec["input"]))
        else:
            declared[key] = spec or {}
    return declared


def _input_refs(node: Any) -> set[str]:
    if isinstance(node, _InputRef):
        return {str(node)}
    if isinstance(node, dict):
        return set().union(*(_input_refs(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(_input_refs(v) for v in node)) if node else set()
    return set()


def _fired_events() -> set[str]:
    text = "\n".join(path.read_text(encoding="utf-8") for path in EVENT_SOURCES)
    return set(re.findall(r'"(nikobus_[a-z_]+)"', text))


def test_there_is_a_blueprint() -> None:
    assert BLUEPRINTS


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.name)
def test_every_input_is_declared_and_used(path: Path) -> None:
    data = _load(path)
    declared = _declared_inputs(data["blueprint"]["input"])
    body = {key: value for key, value in data.items() if key != "blueprint"}
    assert _input_refs(body) == set(declared)


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.name)
def test_source_url_points_at_the_file(path: Path) -> None:
    url = _load(path)["blueprint"]["source_url"]
    assert url == f"https://github.com/fdebrus/Nikobus-HA/blob/main/{path.relative_to(ROOT).as_posix()}"


def test_key_blueprint_offers_only_events_the_integration_fires() -> None:
    data = _load(ROOT / "blueprints/automation/fdebrus/nikobus_key_controls_entity.yaml")
    press = data["blueprint"]["input"]["press"]
    offered = {option["value"] for option in press["selector"]["select"]["options"]}
    assert offered <= _fired_events()
    assert press["default"] in offered


def test_key_blueprint_actions_exist_for_every_target_domain() -> None:
    data = _load(ROOT / "blueprints/automation/fdebrus/nikobus_key_controls_entity.yaml")
    behaviours = {o["value"] for o in data["blueprint"]["input"]["behaviour"]["selector"]["select"]["options"]}
    # homeassistant.toggle / turn_on / turn_off dispatch to each target's
    # own domain, and every domain offered has all three.
    assert behaviours == {"toggle", "turn_on", "turn_off"}
    assert data["actions"] == [{"action": "homeassistant.{{ behaviour }}", "target": "target_entities"}]


def test_key_blueprint_matches_the_address_as_the_event_carries_it() -> None:
    data = _load(ROOT / "blueprints/automation/fdebrus/nikobus_key_controls_entity.yaml")
    # The event's address is upper case (the press tracker keys on it so);
    # a typed address is trimmed and upper-cased before it is compared.
    (trigger,) = data["triggers"]
    assert trigger["event_data"]["address"] == "{{ key_address | string | trim | upper }}"
    assert trigger["event_type"] == "{{ press_event }}"
