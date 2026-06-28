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


def handle_command(bus, command) -> None:
    """Dispatch a ``cmd:trade`` command. ``analyze`` (args ``symbol``) → run the
    single-symbol analysis, cache + publish; else no-op."""
    if command.type == "analyze":
        analyze(bus, command.args)
