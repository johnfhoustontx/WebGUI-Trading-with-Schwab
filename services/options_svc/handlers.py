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

CACHE_PAPER = "cache:options:paper_account"
EVENT_PAPER = "events:options:paper_account"

CACHE_PAPER_TRADES = "cache:options:paper_trades"
EVENT_PAPER_TRADES = "events:options:paper_trades"

CACHE_PAPER_ANALYZE = "cache:options:paper_analyze"
EVENT_PAPER_ANALYZE = "events:options:paper_analyze"

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


def refresh_paper_account(bus) -> None:
    """Read the paper account view and publish it to the bus.

    No strict contract: the view is a loosely-shaped read-only dict (snapshot +
    positions + orders + has_account flag) that only the Paper Portfolio page
    consumes, and ``compute.paper_account_view`` is already fully defensive."""
    data = compute.paper_account_view()
    version = bus.cache_set(CACHE_PAPER, data)
    bus.publish(EVENT_PAPER, {"version": version})


def refresh_paper_trades(bus) -> None:
    """Read the paper-trade ledger view and publish it to the bus.

    No strict contract: the view is a loosely-shaped read-only dict
    (``{"trades": [...]}``) that only the Paper Trades page consumes, and
    ``compute.paper_trades_view`` is already fully defensive."""
    data = compute.paper_trades_view()
    version = bus.cache_set(CACHE_PAPER_TRADES, data)
    bus.publish(EVENT_PAPER_TRADES, {"version": version})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:options`` command. ``rescan`` → full rescan;
    ``swing_scan`` → on-demand parameterized swing scan; ``refresh_paper`` →
    re-read the paper account; ``paper_entry``/``paper_manage`` → run the cycle
    (guarded on an existing account) then refresh; ``paper_reset`` → reset the
    account then refresh; ``paper_reload`` → re-read the trade ledger;
    ``paper_close``/``paper_delete``/``paper_delete_closed`` → run the lifecycle
    action then refresh the ledger; ``paper_analyze`` → analyze the selected
    trade, cache the result + publish; else no-op."""
    if command.type == "rescan":
        rescan(bus)
    elif command.type == "swing_scan":
        swing_scan(bus, command.args)
    elif command.type == "refresh_paper":
        refresh_paper_account(bus)
    elif command.type == "paper_entry":
        # No account -> don't run the cycle; refresh so the page shows the
        # no-account state.
        if compute.has_paper_account():
            compute.run_entry_cycle()
        refresh_paper_account(bus)
    elif command.type == "paper_manage":
        if compute.has_paper_account():
            compute.run_manage_cycle()
        refresh_paper_account(bus)
    elif command.type == "paper_reset":
        compute.reset_paper_account(float(command.args.get("starting_balance", 25000.0)))
        refresh_paper_account(bus)
    elif command.type == "paper_reload":
        refresh_paper_trades(bus)
    elif command.type == "paper_close":
        compute.close_paper(command.args.get("trade_id"),
                            command.args.get("debit", 0.0))
        refresh_paper_trades(bus)
    elif command.type == "paper_delete":
        compute.delete_paper(command.args.get("trade_id"))
        refresh_paper_trades(bus)
    elif command.type == "paper_delete_closed":
        compute.delete_closed_paper()
        refresh_paper_trades(bus)
    elif command.type == "paper_analyze":
        res = compute.analyze_paper(command.args.get("trade_id"))
        version = bus.cache_set(CACHE_PAPER_ANALYZE, res)
        bus.publish(EVENT_PAPER_ANALYZE, {"version": version})
