# Review of the Nikobus integration

A review of `custom_components/nikobus` at 3.31.6 (15 036 lines of Python,
923 tests), written before `nikobus-ng` and as its brief: what to keep,
and what a clean-slate design would do differently.

Method: the code was read and measured, then run **unmodified under the
real Home Assistant 2026.10.4** against `nikobus-simulator`, which its own
test suite cannot do since it stubs Home Assistant.

## What is solid

- **The protocol lives in a library.** `nikobus-connect` owns framing,
  retries, the bus lock, discovery and the decoders. The integration never
  parses a frame. This is the decision everything else rests on.
- **Read-only by rule.** Nothing writes to a module's programming; the one
  write is the PC-Link clock. The rule is stated, tested and kept.
- **Evidence.** Hardware claims cite the install and the capture they come
  from. The two-button plate regression of 0.57.1 is documented where the
  code now lives, so it cannot be repeated by accident.
- **Entity identity is treated as user data.** Unique-id changes come with
  registry migrations.
- **It runs clean on the real core.** Set up under Home Assistant
  2026.10.4 with the simulator, the integration loads, creates its
  entities, switches an output, and unloads, with no deprecation or frame
  warning from the core. Device links already use `via_device_id`.

## Findings

Each finding says what was observed, why it costs something, and what
`nikobus-ng` does instead. None is a bug report: they are structure.

### 1. Output state lives in three places

The bytes a module reports sit in a shared buffer. The coordinator wakes
entities per module. Each entity then keeps its own optimistic copy:
`_is_on`, `_optimistic_brightness`, `_switching`, `_commanding`, 90
references across `light.py`, `switch.py`, `fan.py` and `entity.py`.

Two releases fixed regressions born there. In 3.30.3 a key-driven light
changed state three times per switch, because the answer to a read made
*before* the press overwrote the optimistic value. In 3.31.4 the same
happened to dimmers, for the same reason, in code that had been written
separately.

The copies are not needed. `NikobusCommandHandler.set_output_state` puts
the new value in the buffer when the request is queued, and
`apply_group_state` lays pending writes back on top of any answer that
arrives in between. An entity that reads the buffer therefore already
shows the requested state.

**nikobus-ng:** entities hold no state. A switch is `value != 0`. A write
that is not acknowledged is followed by a read, which restores the truth.

### 2. The coordinator is the integration

`coordinator.py` (2 066 lines) and its mixin `discovery_mixin.py` (1 538)
hold 92 methods and set 60 attributes in `__init__`: the connection, the
poll, the Feedback Module accounting, three stores, the discovery state
machine, repairs, the `.nkb` import, purge, LED-key checks. Tests build it
with `__new__` and set attributes by hand, 15 times, because constructing
it is not practical. A change to discovery touches the object every entity
depends on.

**nikobus-ng:** four objects with one job each. `hub.py` (400 lines) is the
connection and the output state, `discovery.py` (187) one discovery run,
`presses.py` (146) the keys, `inventory.py` (272) the stores.

### 3. Discovery is two buttons in the right order

*1. Load Project Overview*, then *2. Load Existing Installation*, each
with its own progress, guards and reload. The issue template had to tell
users to run both, in order. The second is disabled until the first ran.

**nikobus-ng:** one action runs both passes and reloads once. A first
setup with nothing in the store starts it by itself.

### 4. The tests cannot see Home Assistant

The suite runs against a 550-line stub (`tests/conftest.py`). CLAUDE.md
lists its known gaps: entry states, reload behaviour, forwarded platforms.
What a stub cannot do at all is change when the core does.

`nikobus-ng` met this on its first run under the real core: `DeviceInfo`'s
`via_device` raised a deprecation, and `device_registry.async_get_device`
is now an error in tests. The existing code is already right on the first
point, which is luck and care, not coverage.

**Recommendation, independent of nikobus-ng:** add one CI job that sets
the existing integration up under `pytest-homeassistant-custom-component`
with the simulator. The harness in `nikobus-ng/tests/conftest.py` does it
for the existing integration in about thirty lines; that is how the
"runs clean" statement above was obtained.

### 5. A press is up to seven events, two entities and six classes

One press fires up to seven kinds of bus event (`nikobus_button_pressed`,
`_released`, `_short_…` or `_long_…`, `_pressed_0…3`, `_timer_1…3`,
`nikobus_button_operation`), pulses a binary sensor and may have a button entity, all filtered by six
"press-entity classes" with a snapshot store to bring entities back.

Home Assistant has had an entity for exactly this since 2023: `event`.

**nikobus-ng:** one `event` entity per key with four types (`pressed`,
`hold`, `short_release`, `long_release`), enabled by default, and one bus
event for automations on a bare address. A press button per key exists,
disabled.

### 6. Entity types are chosen in an options flow

A relay becomes a switch or a light through *Customize a module*, backed
by `router.py` (741 lines) and 17 flow steps. Home Assistant already lets a
user show any switch as a light.

**nikobus-ng:** a relay is a switch. *Show as* does the rest.

### 7. A discovered output has no entity until it is named

This one deserves a look on its own. The library describes the outputs of
a newly inventoried module as `not_in_use output_N`
(`nikobus-connect`, `discovery/fileio.py`, `_default_channel`), and the
router skips every channel whose description starts with `not_in_use`
(`router.py`, `build_routing`). Nothing renames them except the `.nkb`
import and the user, in *Customize a module*.

Observed under the real core, with a store shaped as an inventory leaves
it: the integration created 16 press sensors, 22 buttons and 5 sensors,
and **no switch, light or cover**. With the descriptions changed to
anything else, it created 12 switches, 12 lights and 6 covers.

So an installation discovered without a project file shows no output until
each one is named by hand. The prefix was meant for hand-written files,
where the user chose it; here the library chooses it for everything.
A two-line fix is possible on either side: the library can describe a
discovered output as `Output N`, or the router can ignore the prefix on a
module that carries `discovered_info`.

**nikobus-ng:** every output gets an entity. After a full link scan, an
output that no key drives is created disabled, since it is most likely not
wired, and the user can enable it.

### 8. Weight of history

`legacy` or `migrat` appears 143 times. There are three stores, a scene
file, two manual inventory files, a backup folder and a names snapshot.
Each was right when added; together they are most of what a newcomer has
to learn.

**nikobus-ng:** one store per config entry, deleted with it. This is the
luxury of having no users yet, not a criticism.

### 9. Smaller things

- 30 function-level imports, mostly to break cycles through the
  coordinator. With smaller modules there is no cycle.
- 19 `getattr(self, …, None)` on the integration's own objects, guarding
  against half-built coordinators in tests.
- 13 broad `except Exception`, several of them justified at a callback
  boundary.

## What to take back, in order of value

0. **Outputs without a name** (finding 7): the only item here that changes
   what a new user sees today.
1. **A real-Home-Assistant CI job** for the existing integration (finding 4).
   Small, and it protects every later change.
2. **Entities as views of the buffer** (finding 1). It removes a class of
   regressions that has already cost two releases.
3. **One discovery action** (finding 3). Mostly orchestration on top of
   what exists.
4. **`event` entities** (finding 5), alongside the current events at first.

`nikobus-ng` is the proof that 1 to 3 work together; it is not a
replacement. See its README for what it leaves out.
