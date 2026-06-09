"""Constants for the Nikobus integration."""

from __future__ import annotations

from typing import Final

# =============================================================================
# General
# =============================================================================
DOMAIN: Final[str] = "nikobus"
BRAND: Final[str] = "Niko"
HUB_IDENTIFIER: Final[str] = "nikobus_hub"

# =============================================================================
# Device-registry category groupings
# =============================================================================
# Intermediate "category" devices inserted between the hub and real devices
# so the integration's device list nests by type instead of dumping everything
# under one Hub node. Each category device:
#   * has no entities of its own
#   * uses ``via_device=hub`` so it appears under the bridge
#   * is itself the ``via_device`` of every real device in that category
#
# Categories with no real children are auto-removed by
# ``_async_cleanup_orphan_entities`` (the "kept when it has children" rule).
CATEGORY_OUTPUT_MODULES: Final[str] = "category_output_modules"
CATEGORY_SYSTEM_MODULES: Final[str] = "category_system_modules"
CATEGORY_WALL_BUTTONS: Final[str] = "category_wall_buttons"
CATEGORY_REMOTES: Final[str] = "category_remotes"
CATEGORY_INTERFACES: Final[str] = "category_interfaces"
CATEGORY_SCENES: Final[str] = "category_scenes"

# Display metadata for each category device (identifier → (name, model)).
# Order is the order they get registered in; HA preserves it in the device
# list (sort is alphabetical by display name though, so order is cosmetic).
CATEGORY_DEVICES: Final[tuple[tuple[str, str, str], ...]] = (
    (CATEGORY_OUTPUT_MODULES, "Output modules",
     "Switch / dimmer / roller modules"),
    (CATEGORY_SYSTEM_MODULES, "System modules",
     "PC-Logic, Feedback, Audio, Modular Interface"),
    (CATEGORY_WALL_BUTTONS, "Wall buttons",
     "Physical bus push buttons"),
    (CATEGORY_REMOTES, "Remotes",
     "RF transmitters"),
    (CATEGORY_INTERFACES, "Interfaces",
     "Push-button / switch / universal interfaces"),
    (CATEGORY_SCENES, "Scenes", "Software scenes"),
)

# =============================================================================
# Events
# =============================================================================
EVENT_BUTTON_OPERATION: Final[str] = "nikobus_button_operation"
EVENT_BUTTON_PRESSED: Final[str] = "nikobus_button_pressed"
# Fired when a discovered CF/light scene is activated on the bus (its
# trigger address is seen) — lets automations react to a scene firing,
# whether triggered physically or from HA.
EVENT_SCENE_ACTIVATED: Final[str] = "nikobus_scene_activated"


def operation_signal(address: str) -> str:
    """Per-address dispatcher signal for a button-operation notification.

    Sent when a press impacts a module so that module's output entities
    can invalidate their optimistic state. Routing by address means only
    the impacted module's entities wake — unlike a listener on the shared
    ``EVENT_BUTTON_OPERATION`` bus event, where every output entity is
    invoked and all but the matching channels filter themselves out.
    Mirrors the coordinator's per-address ``{DOMAIN}_update_{address}``.
    """
    return f"{DOMAIN}_operation_{address.upper()}"


def press_signal(address: str) -> str:
    """Per-address dispatcher signal for a button-press notification.

    Sent when a button frame is seen on the bus, keyed by both the button
    ``address`` and the impacted ``module_address``, so only the entities
    concerned with that address wake — instead of every binary sensor /
    cover / scene running a shared ``EVENT_BUTTON_PRESSED`` listener and
    filtering itself out. The payload mirrors the bus event's data dict.
    """
    return f"{DOMAIN}_press_{address.upper()}"


# =============================================================================
# Discovery
# =============================================================================
# Device-type / channel / key / mode / timer mapping tables live in
# ``nikobus_connect.discovery.mapping`` and are owned by the library —
# ``classify_device_type()`` writes the resolved name + model + channel
# count straight into the discovery output, which the HA-side platform
# code reads via ``phys.get("type")`` etc. Don't shadow them here: a
# stale local copy is what made the integration look like it was doing
# its own classification when it isn't.

SIGNAL_DISCOVERY_STATE: Final[str] = "nikobus_discovery_state"

DISCOVERY_PHASE_IDLE: Final[str] = "idle"
DISCOVERY_PHASE_PC_LINK: Final[str] = "pc_link"
DISCOVERY_PHASE_MODULE_SCAN: Final[str] = "module_scan"
DISCOVERY_PHASE_FINISHED: Final[str] = "finished"
DISCOVERY_PHASE_ERROR: Final[str] = "error"

# Fine-grained sub-phases exposed via ``discovery_sub_phase``. The high-level
# ``discovery_phase`` stays on the legacy enum so existing automations keep
# working: ``inventory`` + ``identity`` map back to ``pc_link``;
# ``register_scan`` + ``finalizing`` map back to ``module_scan``.
DISCOVERY_SUB_PHASE_IDLE: Final[str] = "idle"
DISCOVERY_SUB_PHASE_INVENTORY: Final[str] = "inventory"
DISCOVERY_SUB_PHASE_IDENTITY: Final[str] = "identity"
DISCOVERY_SUB_PHASE_REGISTER_SCAN: Final[str] = "register_scan"
DISCOVERY_SUB_PHASE_FINALIZING: Final[str] = "finalizing"
# Post-discovery residue probe + eviction (HA-side, fires from
# ``_reconcile_post_discovery``). The library's discovery itself is
# done by this point, but the integration still has 5-15 s of work
# (bus probe + retries + eviction); a distinct sub-phase keeps the
# diagnostic status meaningful rather than freezing on the last
# inventory frame.
DISCOVERY_SUB_PHASE_PROBING: Final[str] = "probing"
DISCOVERY_SUB_PHASE_FINISHED: Final[str] = "finished"
DISCOVERY_SUB_PHASE_ERROR: Final[str] = "error"

# Weighting for the 0-100 progress sensor. Must sum to 100.
DISCOVERY_WEIGHT_INVENTORY: Final[int] = 10
DISCOVERY_WEIGHT_IDENTITY: Final[int] = 20
DISCOVERY_WEIGHT_REGISTER_SCAN: Final[int] = 65
DISCOVERY_WEIGHT_FINALIZING: Final[int] = 5

#: The selectable ``.nkb`` import categories (all applied by default).
NKB_IMPORT_CATEGORIES: Final[tuple[str, ...]] = (
    "device_names",
    "channel_names",
    "areas",
    "scenes",
)

# =============================================================================
# Repair issues
# =============================================================================
ISSUE_NO_BUTTONS_CONFIGURED: Final[str] = "no_buttons_configured"
ISSUE_STALE_INVENTORY_PRESENT: Final[str] = "stale_inventory_present"
# Surfaced after Stage-2 scan-all when one or more buttons still have
# no decoded ``linked_modules``. We can't programmatically distinguish
# "intentionally unwired (HA automation trigger)" from "residue from a
# previous owner" — both look identical from the bus signal. Push the
# decision to the user via a Repairs flow.
ISSUE_LEGACY_UNDECODED_BUTTONS: Final[str] = "legacy_undecoded_buttons"

# Physical button types that are INPUT-ONLY by design — they generate
# bus press telegrams when their contacts change state but they don't
# write into output-module link tables (their inputs feed PC-Logic
# conditions instead). Discovery's per-module register scan therefore
# never finds link records pointing back at them, and they'd otherwise
# be tagged ``legacy_undecoded`` and trigger a false-positive Repairs
# alert.
#
# Tagged ``input_only`` instead so the Repairs flow and per-entity
# ``wall_button_status`` treat them like ``synthesized_input`` (already
# an exclusion for the same reason: PC-Logic Logical Inputs also have
# no link table by design).
#
# Match is by the human-readable ``type`` string discovery writes into
# each button entry, which already reflects device_type 0x43 vs 0x44
# for the two 05-058 modes.
INPUT_ONLY_BUTTON_TYPES: Final[frozenset[str]] = frozenset({
    "Universal interface, switch mode",        # Niko 05-058, dtype 0x44 (8-ch)
    "Universal interface, push-button mode",   # Niko 05-058, dtype 0x43 (4-ch)
})

# =============================================================================
# Configuration Keys
# =============================================================================
CONF_CONNECTION_STRING: Final[str] = "connection_string"
CONF_REFRESH_INTERVAL: Final[str] = "refresh_interval"
CONF_HAS_FEEDBACK_MODULE: Final[str] = "has_feedbackmodule"
CONF_PRIOR_GEN3: Final[str] = "prior_gen3"
CONF_PRESS_REPEAT: Final[str] = "press_repeat"

# Filenames used by the manual-config import — the step-1 inventory
# source for installs without a PC-Link. Both are read on every
# coordinator setup when present. Canonical filenames only; the old
# ``.migrated`` fallback was dropped in 2.11.4.
MANUAL_MODULE_CONFIG_FILENAME: Final[str] = "nikobus_module_config.json"
MANUAL_BUTTON_CONFIG_FILENAME: Final[str] = "nikobus_button_config.json"

# =============================================================================
# Buttons
# =============================================================================
REFRESH_DELAY: Final[float] = 0.5  # Delay before retrieving status after button press
DIMMER_DELAY: Final[int] = 1  # Delay before retrieving dimmer status

# Simulated-button-press repetition. A real Nikobus button emits its
# telegram repeatedly for as long as it's held, and modules only act on
# a command seen at least twice (the bus protocol's noise/collision
# guard). A single #N frame is therefore unreliable under bus
# contention, so HA-originated presses (button, scene, CF, latch switch)
# are sent as a short, spaced burst. ``DEFAULT_PRESS_REPEAT`` mirrors
# the reference firmware's "2 to register, 3 to be sure"; the per-repeat
# gap keeps the burst short enough to read as a tap, not a hold.
DEFAULT_PRESS_REPEAT: Final[int] = 3
PRESS_REPEAT_DELAY: Final[float] = 0.05  # seconds between repeated #N frames
SHORT_PRESS: Final[float] = 1.0  # Short press duration in seconds
BUTTON_TIMER_THRESHOLDS: Final[tuple[int, int, int]] = (1, 2, 3)

# Wire cadence of the Nikobus "button held" signal. The bus emits one
# press frame every ~40 ms while a button is held. We use this as the
# physical invariant for duration measurement: frame_count * cadence
# tells us how long the wire was carrying the held signal, regardless
# of when the bytes actually reached our process. See nkbactuator.py.
FRAME_CADENCE_S: Final[float] = 0.040

# How long (in ms) of inter-frame silence before we consider a button
# released. Bumped from the historical 150 ms to absorb typical
# bridge hiccups under 300 ms. Bursts can adaptively extend this
# further (see BURST_* constants below).
RELEASE_THRESHOLD_MS: Final[int] = 300

# Burst-flush detection. When the transport (TCP-to-serial bridge,
# OS scheduler, asyncio loop) stalls, frames pile up upstream and
# drain in microsecond-spaced bursts that are physically impossible
# on a 40 ms-cadence wire. We treat any gap below this as a marker
# that the current frame came from a buffer, not the wire.
BURST_GAP_THRESHOLD_S: Final[float] = 0.005

# Sliding window of inter-frame gaps used to decide whether we're
# currently inside a burst-flush. If ``BURST_DETECT_GAP_COUNT`` of
# the last ``BURST_RECENT_GAPS_WINDOW`` gaps were burst-shaped, we
# extend the release threshold to absorb the implied bridge stall.
BURST_RECENT_GAPS_WINDOW: Final[int] = 4
BURST_DETECT_GAP_COUNT: Final[int] = 3

# Maximum value the release threshold can grow to under burst-mode
# extension. Bigger = more correct on multi-burst stalls but slower
# release detection on a real release while in burst mode. Cap
# keeps worst-case latency bounded.
MAX_EXTENDED_RELEASE_MS: Final[int] = 5000

# =============================================================================
# Covers
# =============================================================================
DEFAULT_COVER_MOVEMENT_BUFFER: Final[float] = 3.0
DEFAULT_COVER_DEBOUNCE_DELAY: Final[float] = 0.3
DEFAULT_COVER_OPERATION_TIME: Final[float] = 30.0

# =============================================================================
# Listener
# =============================================================================
DEVICE_ADDRESS_INVENTORY: Final[str] = "$18"
DEVICE_INVENTORY_ANSWER: Final[tuple[str, str]] = ("$2E", "$1E")

# =============================================================================
# Reconnect
# =============================================================================
RECONNECT_DELAY_INITIAL: Final[int] = 5   # First retry delay in seconds
RECONNECT_DELAY_MAX: Final[int] = 60      # Cap on exponential-backoff delay

# =============================================================================
# Command Execution
# =============================================================================
COMMAND_EXECUTION_DELAY: Final[float] = 0.15  # Delay between command executions (OH1 uses 50 ms; 150 ms gives the bus a safe clearing window)
COMMAND_ACK_WAIT_TIMEOUT: Final[int] = 15   # Outer deadline for the whole ACK+ANSWER wait
COMMAND_ANSWER_WAIT_TIMEOUT: Final[int] = 5  # Pre-ACK: how long to wait for the ACK itself
COMMAND_POST_ACK_ANSWER_TIMEOUT: Final[float] = 1.5  # Post-ACK: data should follow ACK quickly
MAX_ATTEMPTS: Final[int] = 3  # Maximum retry attempts
