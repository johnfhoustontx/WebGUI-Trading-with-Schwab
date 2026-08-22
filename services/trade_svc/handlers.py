"""Trade service analyze handler (Tier-2 → Tier-3 write path).

The service-side analog of the legacy desktop ``analyze()`` button: on an
``analyze`` command it runs ``compute.analyze(symbol)`` (a JSON-safe dict),
projects it onto the ``TradeAnalysis`` contract as a validation gate (so gross
shape drift raises loudly BEFORE caching), writes the validated payload to the
Redis bus under ONE cache view, and publishes a change event for the GUI.

Single cache view (``cache:trade:analysis``): the page analyzes one symbol at a
time, so a single latest-result view is the right shape (mirroring the
simulator/calculator on-demand result views) — the GUI reads the latest analysis
and the payload's own ``symbol`` field identifies it.

On-demand only: there is no scheduler. The GUI Trade page enqueues an
``analyze`` command with the symbol; we never run unprompted.

Kept synchronous: the scaffold's consumer loop handles sync handlers.
"""
from services.trade_svc import compute
from shared.contracts.trade import TradeAnalysis

CACHE_ANALYSIS = "cache:trade:analysis"
EVENT_ANALYSIS = "events:trade:analysis"

# EquityDeepDive on-demand views (loose {html|markdown, symbol, ts} dicts — NOT
# projected onto TradeAnalysis; the webgui serves them raw / in a copyable page).
CACHE_DEEPDIVE = "cache:trade:deepdive"
EVENT_DEEPDIVE = "events:trade:deepdive"
CACHE_DEEPDIVE_QUERY = "cache:trade:deepdive_query"
EVENT_DEEPDIVE_QUERY = "events:trade:deepdive_query"

# The TradeAnalysis fields we project the compute dict onto (dropping extras the
# GUI ignores). ``.get`` with the field default keeps a partial/error result
# (which omits most keys) from crashing, while construction validates the types
# of whatever is present.
_FIELDS = {
    "symbol": "",
    "description": "",
    "price": None,
    "volume": None,
    "bias": "",
    "ema_alignment": {},
    "momentum": {},
    "volume_profile": {},
    "sector": {},
    "position_verdict": {},
    "investor_verdict": {},
    "markov": None,
    "swing_model": None,
    "fundamentals": {},
    "fundamentals_available": False,
    "timestamp": None,
    "errors": [],
    # Two-sided reads. Additive and optional; a compute result that predates
    # them (or a degraded one that omits them) projects to None and the page's
    # builders no-op.
    "direction_clearance": None,
    "dealer_context": None,
    "peers": None,
    "earnings_coverage": None,
}


def analyze(bus, args) -> None:
    """Analyze the requested symbol, validate, cache the result, publish an event."""
    symbol = (args or {}).get("symbol", "")
    result = compute.analyze(symbol)
    if not result:
        # Empty/invalid symbol → cache a graceful error payload so the page
        # shows a "couldn't analyze" state instead of staling on a prior symbol.
        result = {"symbol": (symbol or "").strip().upper() or "?",
                  "errors": ["No symbol provided"]}

    # Validation gate: project onto TradeAnalysis fields and construct the model
    # so a gross shape drift (e.g. momentum not a dict) raises BEFORE caching.
    ta = TradeAnalysis(**{k: result.get(k, default)
                          for k, default in _FIELDS.items()})

    version = bus.cache_set(CACHE_ANALYSIS, ta.model_dump())
    bus.publish(EVENT_ANALYSIS, {"version": version})


def deepdive(bus, args) -> None:
    """Run the EquityDeepDive quant report for the symbol; cache the HTML + publish."""
    res = compute.run_deep_dive((args or {}).get("symbol", ""))
    version = bus.cache_set(CACHE_DEEPDIVE, res)
    bus.publish(EVENT_DEEPDIVE, {"version": version})


def deepdive_query(bus, args) -> None:
    """Build the chat-prompt query for the symbol; cache the markdown + publish."""
    res = compute.build_deep_dive_query((args or {}).get("symbol", ""))
    version = bus.cache_set(CACHE_DEEPDIVE_QUERY, res)
    bus.publish(EVENT_DEEPDIVE_QUERY, {"version": version})


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:trade`` command. ``analyze`` (args ``symbol``) → run the
    single-symbol analysis; ``deepdive`` / ``deepdive_query`` → the EquityDeepDive
    report / chat-prompt query; else no-op. All cache + publish."""
    if command.type == "analyze":
        analyze(bus, command.args)
    elif command.type == "deepdive":
        deepdive(bus, command.args)
    elif command.type == "deepdive_query":
        deepdive_query(bus, command.args)
