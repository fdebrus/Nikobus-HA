# Audit — 2026-10-31

Third audit pass over **nikobus-connect 0.57.2** and **Nikobus-HA 3.31.2**, by three read-only reviewers; the findings marked *re-run* or *CONFIRMED* were reproduced with the probe scripts in [`probes/`](probes/). Nothing here has been fixed yet.

Running the probes: a Python 3.11+ venv with `pytest` and the library installed from a checkout (`pip install -e <nikobus-connect>`). The library probes import `nikobus_connect`; the integration probes import `tests/conftest.py`, so run them from the root of this repository. `probes/library/p1_history.sh` replays a store through library 0.57.0 → 0.57.1 → 0.57.2: it expects those two versions extracted next to it as `v0570/` and `v0571/` (`git archive v0.57.0 nikobus_connect | tar -x -C v0570`, same for v0.57.1) and `PY` (your venv's python) and `LIB` (a nikobus-connect checkout at 0.57.2) set in the environment.

## Library (nikobus-connect 0.57.2)

Probes: [`probes/library/`](probes/library/)

1. CONFIRMED (code read + p2_real_decoder_path.py) — `_settle_two_button_layouts` (discovery/fileio.py ~1674) never moves a plate in production. The mapping key's first element is the key's bus frame (discovery.py add_to_command_mapping: physical_push = push_button_address once get_button_channels knows the plate), not the plate's physical address, so `buttons.get(...)` misses. A fresh #184-type install stays at 8/C and its 0/2 records stay unmatched. Fix: identify the plate from each output's `button_address` (physical); take the family from key_raw.
2. CONFIRMED (p1_history.sh, p8_no_repair.py) — `_known_two_button_layout` stamps `key_layout: "0_4"` on generic plates whose 0.57.0 links were carried to 0/4 by a 0.57.1 inventory without a link rescan; with (1) the link scan cannot repair it. Fix: drop the "linked 0/4 keys" inference once settling works; trust only `key_layout` or the link scan.
3. CONFIRMED (p6) — 0.56.3 inventory guard: `_finalize_inventory_phase` checks queued identity reads only in stage `inventory_identity`; the normal flow stays in `inventory_addresses`, so the inventory still finishes with reads queued. Test sets the stage by hand. Fix: check in both inventory stages.
4. CONFIRMED (p4) — command.py ~140: a cancelled GET item is skipped even when a deduplicated second caller still waits on its key; that caller gets no exchange. Fix: skip only when every waiter for the key is done.
5. CONFIRMED logic (p5) — listener.py ~300: after our ack every matching $1C for the module is attributed to our group until the exchange ends, so a Feedback Module answer for the other group in the same chunk is filed under ours. Fix: attribute only the first matching answer after the ack, then clear `_awaited_query`.
6. PLAUSIBLE — layout judged per merge call (per frame batch / per module rebuild), so mixed families across calls flip a plate and registry records decide it. Fix: settle after the authoritative filter; ideally once per run.

Hardware assumptions without a cited capture (reviewer list): 0x10 fixed right pair (one install), 0x05/0x3F per-plate (no key capture), 0x21/0x22/0x1F at 8/C, stale comment mapping.py ~695-702 (says the 05-060 family emits 0/2), motion detector light/dark key indices, 0x44 as 8-key, KEY_MAPPING_MODULE[1], 0x26 channels vs vendor ref, timing constants.

Tests passing only through their fakes: test_two_button_plates `_records` (physical address as mapping key), the 0.57.1-store test, test_inventory_window_waits_while_identity_reads_queued, test_acknowledged_query_answer_keeps_our_group.


## Cross-repo consistency (reviewer report, spot-checked)

Docs stating something false:
- README says `nikobus_scene_activated` fires also when HA sends the scene; it only fires on inbound presses (scene.py listens to press_signal; HA's own send dispatches none). Verified.
- README says "seven services" (lines 784, 1009); there are eight. Verified.
- README says Customize changes survive re-discovery; a module type set there is overwritten by step 1 (library fileio.py ~389), and the type-mismatch repair row tells users to set it there.
- `nikobus_button_operation` is described as post-refresh; it fires when the read is scheduled, and its payload adds impacted_module_address / impacted_module_group.
- Library "Key layouts" table: 05-312 uses its own table, PC-Logic inputs (0/4) missing, `key_layout` is only stored for 0_4.
- quality_scale.yaml claims docs-removal-instructions done; README has no removal section.
- "One config entry per HA instance" not enforced: no `single_config_entry` in manifest. Verified.
- Cover example matches a module address on a button event; `labels` import category undocumented; CHANGELOG 3.30.7 overstates the CI change.
Translations: all three languages consistent; one stale button name in options.step.edit_module.description.
Pins: `pyserial-asyncio-fast` required by the manifest but imported nowhere (library uses pyserial-asyncio). Verified. No upper bound on nikobus-connect.
Contract: integration imports library internals not exported (NON_OUTPUT_MODULE_TYPES, mapping tables compared by identity `is TWO_BUTTON_PLATE_KEYS`, key_layout_for_model, motion helpers missing from __all__), plus getattr-with-default reads that would hide renames.
Tooling: library publish.yml publishes without tests/lint; no library CI; HA CI tests only the newest library, never the minimum pin, and nothing runs when the library releases; no mypy job; hassfest/hacs actions unpinned.


## Integration (Nikobus-HA 3.30.7–3.31.2; probes re-run where marked)

Probes: [`probes/integration/`](probes/integration/). The reviewer checked HA behaviour against the Home Assistant core 2026.10 source.

1. DATA LOSS, re-run — a raw `.storage/nikobus.modules` copy (with its `version`/`key`/`data` envelope) as nikobus_module_config.json replaces the module store with one bogus module `NIKOBUS_MODULE` of type "data" (nkbmanual.py convert_legacy_to_flat; zero-entry guard does not fire). Fix: unwrap `data`; refuse to replace when no entry has a known type.
2. DATA LOSS, re-run — button file has neither guard: `{"nikobus_button": []}` empties the button store; a store-shaped file collapses each plate to one fake 1A key, losing bus addresses and links. Fix: same guard; accept operation_points as-is.
3. BUG (HA source) — a failure after async_forward_entry_setups stops the coordinator and re-raises, leaving platforms loaded; the retry fails with "already been setup". Same path for reconfigure's re-setup. Fix: unload platforms in the except, or first refresh before forwarding.
4. RACE (HA source) — reconfigure on a loaded entry with an update listener reloads twice and logs HA's "should use update listener" warning (breaks 2026.12). Fix: async_update_and_abort when update_listeners exist.
5. PLAUSIBLE — reconfigure in SETUP_RETRY does not cancel the retry timer: retry and test can fight over the exclusive port. Fix: entry.async_cancel_retry_setup().
6. PLAUSIBLE — reconfigure ignores async_unload's result; CancelledError skips the re-setup. Fix: check result; try/finally.
7. BUG, re-run — one bad motion-detector address (`motion_detector_bus_addresses` raises ValueError) aborts the whole binary_sensor platform. Fix: per-detector try/except (and make the library helper return {} on a bad address).
8. PLAUSIBLE — motion sensor ignores coordinator updates, so availability never renders; a restored "on" can stay on.
9. UX (HA source) — no-buttons repair: HA deletes the issue on create_entry even if the inventory is refused. Fix: check discovery_running first and abort.
10. PLAUSIBLE — cover: same-direction report during the end-stop margin restarts motion as "nikobus" until the deferred stop.
11. Low — reconfigure keeps the old title. 12. Low — partial .nkb bootstrap never regenerates the missing button file.
Fragility: identity comparison `is TWO_BUTTON_PLATE_KEYS` (use ==); imports outside library __all__; copy of private fileio._default_channel; nkbsnapshot uses deprecated device_registry.async_get_device (breaks 2027.8).
Stub-only tests: conftest async_update_reload_and_abort, 2-state ConfigEntryState, MagicMock forward setups, repair async_create_entry lambda.

## Suggested order of fixes

1. **Library 0.57.3**: the five confirmed library bugs — the two-button layout settling first (it never runs on a real install), then the wrong `key_layout` stamp, the inventory guard, the GET dedup orphan and the listener attribution.
2. **Nikobus-HA 3.31.3**: both manual-file guards, platform cleanup on a failed setup, the reconfigure reload, the per-detector guard in the binary sensor platform, the repair check.
3. **Docs**: the false statements above, `single_config_entry`, dropping `pyserial-asyncio-fast`.
4. **CI**: tests and lint before the library publishes; an integration CI job pinned to the minimum library version.
