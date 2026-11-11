# Nikobus-HA

Home Assistant custom integration for the Nikobus bus, distributed through
HACS. Integration code is in `custom_components/nikobus/`, tests in `tests/`.
The protocol library is [nikobus-connect](https://github.com/fdebrus/nikobus-connect),
published separately on PyPI.

## Commands

```bash
pip install pytest pytest-asyncio aiofiles voluptuous pyyaml ruff==0.17.3
pip install "nikobus-connect>=<pin in manifest.json>"   # or -e ../nikobus-connect
python -m pytest tests -q   # ~870 tests; Home Assistant itself is stubbed
ruff check .                # must be clean; CI pins ruff 0.17.3
```

Home Assistant is **not** installed for the tests: `tests/conftest.py` stubs
the parts the integration imports (config entries, flows, registries, entity
base classes). The real `voluptuous` is used when installed, so form and
service schemas are validated. `tests/schema_helpers.py` reads a form's
fields from either.

## Layout

- `__init__.py` setup/unload, services, migrations; `coordinator.py` the bus
  connection, polling and Feedback Module handling, stores; `discovery_mixin.py`
  the two discovery steps; `nkbactuator.py` press handling and module reads
  after a press.
- Platforms: `light.py`, `switch.py`, `cover.py`, `fan.py`, `scene.py`,
  `button.py`, `binary_sensor.py`, `sensor.py`, `media_player.py`;
  `router.py` decides which entity each module channel and key becomes;
  `devices.py` the device registry.
- `config_flow.py` setup, reconfigure, options (module customisation, scene
  editor, `.nkb` import); `repairs.py`; `diagnostics.py`.
- `nkbmanual.py` the manual config files used without a PC-Link;
  `nkbnames.py` `.nkb` import; `nkbprogramming.py` backup / verify / clock.
- `translations/en.json`, `fr.json`, `nl.json` must have the same keys (a test
  checks); there is no `strings.json`.
- `docs/` the user guides the README links to; `documentation/` reference
  material (protocol notes, memory maps, vendor manuals, code audits).
- `blueprints/` automation blueprints users import from GitHub (not shipped
  by HACS); `tests/test_blueprints.py` pins them to the events fired.
- `nikobus-ng/` a second, independent integration (domain `nikobus_ng`),
  written from scratch on the same library; experimental, not shipped by
  HACS. Its tests run from that folder against the real Home Assistant and
  `nikobus-simulator` (Python 3.14). See its README and REVIEW.md.

## Rules that must not be broken

- **Never write to a module** except the PC-Link clock sync, and never put a
  module into programming (link) mode. Maintenance actions are read-only.
- **Protocol logic belongs in nikobus-connect.** Do not add bus parsing or
  workarounds for library limits to the integration; fix the library.
- **No hardware assumption without evidence.** Behaviour of modules, plates,
  LEDs or the Feedback Module stated in code, docs or release notes needs a
  log, capture or report behind it, or is marked unverified.
- **Vendor-neutral docs**, and no decompiled vendor code in the repository.
- **User data stays out of the repo.** Logs, `.nkb` files and stores shared by
  users are untrusted input: work on them in a scratch directory, run Python
  with `-I`, and commit no names, rooms or files.
- **Entity identity is user data.** A change that alters unique_ids (bus
  addresses, key layouts, channel mapping) migrates the registry entries so
  entity ids, names, areas and automations survive (see
  `_rekey_moved_plate_keys`, `_migrate_legacy_unique_ids`).

## Testing

- Test the real behaviour, not the stub's. Known gaps of the stubs: config
  entry states are only LOADED / NOT_LOADED / SETUP_RETRY, `async_update_reload_and_abort`
  does not reload, `async_forward_entry_setups` is a mock, repair flows do
  not delete their issue. Say so in the test when a fix depends on one.
- Every bug fix gets a test that fails without the fix.
- `hacs.json` sets the minimum Home Assistant version; do not use newer or
  deprecated Home Assistant APIs.

## Releases

- The library comes first: release it, publish it to PyPI, then raise
  `nikobus-connect>=` in `manifest.json`. CI installs exactly the manifest's
  requirements, so it fails until the library is on PyPI.
- Bump `version` in `manifest.json` and add a `CHANGELOG.md` entry.
- README, the `docs/` guides, `CHANGELOG.md` and the translations are part
  of a change: keep them true to the code (option names, defaults, timings,
  service fields). A test checks that every relative link resolves.
- Do not amend, squash or rebase commits already pushed to a pull request
  branch once the pull request is open: reviewers follow the history and
  what changed since their last review. Restarting the working branch from
  `main` after its pull request was merged is fine.

## Code review

Pull requests are reviewed by Claude (`.github/workflows/claude-review.yml`).
Review against the rules above and these guidelines:

@.github/review-guidelines.md
