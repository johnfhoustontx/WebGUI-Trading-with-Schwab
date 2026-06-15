"""Options service rescan handler (Tier-2 → Tier-3 write path).

The service-side analog of ``webgui/pages/options/scanner.py``'s scan call.
Instead of holding the result in an in-process page state, it computes via
``compute.run_scan`` (a dict), projects it onto the ``ScanResult`` contract as a
validation gate (so gross shape drift raises loudly BEFORE caching), writes the
full validated payload into the Redis bus (Tier 3) under ONE cache key, and
publishes a change event for the GUI to react to.

Single cache view (``cache:options:scan``): a single scan produces BOTH the
0-DTE and swing signal lists, so one cache view holds the whole result. The
GUI's two tabs (0-DTE / Swing) both read from it — there is no scan/swing split.

Kept synchronous: the scaffold's consumer loop handles sync handlers.
"""
from services.options_svc import compute
from shared.contracts.options import ScanResult

CACHE_SCAN = "cache:options:scan"
EVENT_SCAN = "events:options:scan"

CACHE_HEADER = "cache:options:header"
EVENT_HEADER = "events:options:header"

CACHE_SWING = "cache:options:swing"
EVENT_SWING = "events:options:swing"

# Defaults mirror the page's input defaults (symbol SPY, 5-30 DTE, the put/call
# delta gates, min credit 10% -> 0.10 fraction). The page sends the fraction.
_SWING_DEFAULTS = {
    "symbol": "SPY",
    "dte_min": 5,
    "dte_max": 30,
    "put_d_min": -0.20,
    "put_d_max": -0.10,
    "call_d_min": 0.10,
    "call_d_max": 0.20,
    "min_cr_fraction": 0.10,
}

# The six fields ScanResult validates — we project the engine dict onto exactly
# these (dropping the extra keys the GUI ignores). ``.get`` with a default of
# the field's container type keeps missing optional keys from crashing, while
# the ScanResult construction validates the *types* of whatever is present.
_SCAN_DEFAULTS = {
    "signals_0dte": [],
    "signals_swing": [],
    "vix_term_structure": {},
    "timestamp": None,
    "errors": [],
    "warnings": [],
}


def rescan(bus) -> None:
    """Run a scan, validate its shape, cache the full result, publish an event."""
    result = compute.run_scan()

    # Validation gate: project onto ScanResult fields and construct the model so
    # a gross shape drift (e.g. signals_0dte not a list) raises BEFORE caching.
    scan = ScanResult(**{k: result.get(k, default)
                         for k, default in _SCAN_DEFAULTS.items()})

    # One cache view holds the whole result (both signal lists + metadata).
    version = bus.cache_set(CACHE_SCAN, scan.model_dump())
    bus.publish(EVENT_SCAN, {"version": version})


def refresh_header(bus) -> None:
    """Compute the compact header view and publish it to the bus.

    No strict contract: the header view is a small, loosely-shaped read-only dict
    (prices + vix + regime + sentiment dot) that only the header strip consumes,
    so a Pydantic gate would be ceremony with no payoff (YAGNI). ``refresh_header``
    is already fully defensive — it never returns a malformed shape — which is the
    invariant the ScanResult gate exists to enforce for the heavier scan path."""
    data = compute.refresh_header()
    version = bus.cache_set(CACHE_HEADER, data)
    bus.publish(EVENT_HEADER, {"version": version})


def swing_scan(bus, args: dict) -> None:
    """Run a user-parameterized swing scan, cache the result, publish an event.

    On-demand only (not scheduled): the GUI Swing page enqueues a ``swing_scan``
    command with the user's inputs in ``args``; we extract them (falling back to
    the page-default for any missing key), call ``compute.swing_scan`` with the
    raw client objects, and cache the signal list (plus the symbol + original
    args, for the page to display/debug) under ``cache:options:swing``.

    No ScanResult gate: this is a flat signal list (not the dual-list scan
    contract), and the page reads ``payload["signals"]`` directly."""
    args = args or {}
    params = {k: args.get(k, default) for k, default in _SWING_DEFAULTS.items()}
    signals = compute.swing_scan(**params)
    payload = {"signals": signals, "symbol": params["symbol"], "params": args}
    version = bus.cache_set(CACHE_SWING, payload)
    bus.publish(EVENT_SWING, {"version": version})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:options`` command. ``rescan`` → full rescan;
    ``swing_scan`` → on-demand parameterized swing scan; else no-op."""
    if command.type == "rescan":
        rescan(bus)
    elif command.type == "swing_scan":
        swing_scan(bus, command.args)
