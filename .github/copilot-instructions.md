<!--
  Adapted from home-assistant/core (.github/copilot-instructions.md and
  .claude/skills/ha-integration-knowledge/), licensed under Apache-2.0:
  https://github.com/home-assistant/core
  Modified for the Nikobus custom integration: paths, testing approach,
  Python version and PR rules changed; core-only rules removed.
-->

# Copilot code review instructions

This repository contains **Nikobus**, a Home Assistant custom integration distributed through HACS. Integration code lives in `custom_components/nikobus/`, tests in `tests/`. The bus protocol library is `nikobus-connect`, published separately on PyPI.

## How to review

- Start review comments with a short, one-sentence summary of the suggested fix.
- Do not comment on code style, formatting or linting issues. Ruff runs in CI.
- Only comment on code added or changed by the pull request. Do not ask for refactors of unchanged code.
- Flag comments that over-explain straightforward code, narrate the obvious, or read like AI commentary (multi-sentence justifications for a single line).
- A pull request that bumps a dependency version (in `manifest.json` `requirements`) should only contain the changes required for that bump. If it includes other changes, request that they are moved to a separate pull request.
- When a pull request changes `manifest.json` `version`, check that `CHANGELOG.md` has a matching entry.

## Python and compatibility

- CI runs the tests on Python 3.12 and 3.13. Flag syntax or standard-library features that need a newer Python than 3.12, for example `except A, B:` without parentheses (PEP 758, Python 3.14) or relying on lazily evaluated annotations (PEP 649) without `from __future__ import annotations`.
- `hacs.json` declares the minimum supported Home Assistant version. Flag use of Home Assistant APIs introduced after that version, and flag use of Home Assistant APIs that are deprecated.

## Integration guidelines

- Prefer integrations with the Platinum or Gold level in the Home Assistant core repository as examples.
- Polling intervals are not user-configurable. Do not add `scan_interval`, update interval or polling frequency options to config flows or config entries.
- Do not let users set the config entry name in the config flow.
- Protocol parsing and bus state machines belong in the `nikobus-connect` library, not in the integration. Flag new protocol logic added to `custom_components/nikobus/`. Do not ask to move existing modules in a pull request that does not otherwise change them.
- Do not add workarounds in the integration for limitations in `nikobus-connect`; the library should be fixed instead.
- In config flows, map known failures to the matching standard error (for example `cannot_connect`) without logging them. Catch unexpected exceptions only at the outer level, log them with `_LOGGER.exception()` and map them to `unknown`. Do not mix translated and untranslated text in an error shown to the user.
- User-facing strings belong in `custom_components/nikobus/translations/en.json`. This custom integration has no `strings.json`. When a pull request adds a translation key, check it exists in `en.json`.
- When validation guarantees a dict key exists, prefer direct key access (`data["key"]`) over `.get("key")`, so contract violations surface instead of being silently masked.
- Do not suggest extra defensive checks for action fields already validated by the action schema or entity selection filters. Suggest guards only when data bypasses those validators or is transformed into a less safe form.
- Keep try-clauses as small as possible. Do not wrap large blocks of code in a try-clause, and do not catch exceptions from calls that are not expected to raise them.
- Asserts that only narrow types for the type checker must be behind an `if TYPE_CHECKING:` guard.
- Service actions that change configuration or have security implications should require an admin user, registered with `async_register_admin_service`.

## Entity platforms

- `async_added_to_hass()` and `async_will_remove_from_hass()` must be symmetrical: anything subscribed or set up in one is unsubscribed or torn down in the other.
- Use `_attr_*` attributes instead of properties for values that never change. Define them as class attributes only when the value is shared by all instances.
- When overriding `available`, combine it with the parent's value (`return super().available and ...`).
- Do not suggest redeclaring attributes, properties or methods the entity base class already provides, and do not add guards against the base class's behaviour changing.
- Changes to an entity's `unique_id` format break existing installations. Flag them unless the pull request also adds a migration.

## Actions

- Actions are registered from `services.yaml`. When a pull request adds or renames an action or a field, check that `services.yaml` and `translations/en.json` are updated to match.
- Do not request converting existing action names or fields to `StrEnum`s.

## Repairs (`repairs.py`)

- Every repair issue must be actionable by the user. Only create issues for problems the user can resolve.
- The issue text must say what the problem is, why it matters, the exact steps to resolve it (a numbered list when there are several), and what to expect afterwards. Avoid vague instructions such as "check your installation".
- Severity: `WARNING` for future breakage, `ERROR` for problems needing immediate attention, `CRITICAL` only for extreme cases.

## Diagnostics (`diagnostics.py`)

- Diagnostics must never expose passwords, tokens, host credentials or other sensitive data. Use `async_redact_data` for anything that could contain them.

## Tests

- Home Assistant is not installed in CI. `tests/conftest.py` stubs the `homeassistant` modules the integration imports. When a pull request imports a Home Assistant module or name that is not yet stubbed, check that the stub is added to `conftest.py`.
- Do not request migrating the test suite to `pytest-homeassistant-custom-component`, `MockConfigEntry` or Syrupy snapshots in a pull request about something else.
- New behaviour and bug fixes should come with tests. A bug fix should include a test that fails without the fix.
- All test function parameters must have type annotations. Prefer concrete types over `Any`.
- Avoid conditions and branching in tests. Split the test or use parametrization instead.
- If several tests share most of their code, merge them with `pytest.mark.parametrize`, using `pytest.param(..., id=...)` to name each case.
- Async tests use `@pytest.mark.asyncio`.
- Patch objects where they are used, not where they are defined.
- Hardcoded `entity_id`s in tests are fine. If the same one is repeated, use a constant.

## Comments in code

- Keep comments concise. Prefer one short line stating a non-obvious constraint, or no comment at all.
- Do not add comments that restate the code that follows. Comments explain why, never what.
- Do not add comments that justify a change by describing what the code looked like before.
- Do not add section or divider comments (for example `# --- Helpers ---`). Flag new ones introduced by the pull request.
- Flag commented-out code.

## Integration Quality Scale

- `manifest.json` declares `"quality_scale": "gold"`, and `custom_components/nikobus/quality_scale.yaml` records each rule as `done`, `exempt` or `todo`.
- Check changed code against the rules marked `done` or `exempt` that it can affect. Rules are documented at https://contributors.home-assistant.io/core/quality-scale/rules/
- If a pull request marks a rule as `done` or `exempt`, verify the integration satisfies it or that the exemption reason is valid.
- Skip rules that do not apply to a custom integration, such as `docs-*` rules that refer to the home-assistant.io documentation repository.
- Only report a quality scale violation when you can cite the exact file and line and the rule documentation supports it.

## AI-assisted contributions

A human must review, understand and be able to explain every change before it is submitted. Do not open issues or pull requests autonomously, and do not post comments on behalf of a user without their review.
