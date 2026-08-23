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
from shared.contracts.trade import TradeAnalysis, RankBoard, ModelBook

CACHE_ANALYSIS = "cache:trade:analysis"
EVENT_ANALYSIS = "events:trade:analysis"

# The ranked cross-section (Phase 5). Rebuilt on demand and once the universe
# snapshot has been refreshed for the day; the snapshot is the expensive part
# and the board is pure scoring on top of it.
CACHE_RANK_BOARD = "cache:trade:rank_board"
EVENT_RANK_BOARD = "events:trade:rank_board"

# The model's own paper book (Phase 6): what following the board would have
# done. Isolated from the driver's book, and paper only.
CACHE_MODEL_BOOK = "cache:trade:model_book"
EVENT_MODEL_BOOK = "events:trade:model_book"

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
    "change": None,
    "change_pct": None,
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
    "trade_plan": None,
    "live_ic": None,
    "symbol_history": [],
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


def rank_board(bus, args=None) -> None:
    """Rebuild and publish the ranked cross-section.

    ``skip_unchanged`` because the board only moves when the daily universe
    snapshot does: a page polling its version must not repaint on every rebuild
    of an identical board. ``event`` makes ``cache_set`` pipeline the SET and
    the PUBLISH into one round trip — and skip BOTH when nothing changed, which
    a separate ``bus.publish`` call would defeat."""
    board = compute.build_rank_board()
    rb = RankBoard(**{k: board.get(k, d) for k, d in _BOARD_FIELDS.items()})
    bus.cache_set(CACHE_RANK_BOARD, rb.model_dump(),
                  event=EVENT_RANK_BOARD, skip_unchanged=True)


_BOARD_FIELDS = {
    "status": "ok", "as_of": None, "model_version": None, "regime_key": None,
    "risk_share": None, "horizon_days": 20, "n": 0,
    "thin_cross_section": True, "rows": [], "long_pool": [], "short_pool": [],
    "market_filter": {}, "short_expression": "relative", "gates_evaluated": [],
}


def model_book(bus, args=None) -> None:
    """Tick the model paper book and publish it."""
    book = compute.run_model_book()
    mbk = ModelBook(**{k: book.get(k, d) for k, d in _MODEL_BOOK_FIELDS.items()})
    bus.cache_set(CACHE_MODEL_BOOK, mbk.model_dump(),
                  event=EVENT_MODEL_BOOK, skip_unchanged=True)


_MODEL_BOOK_FIELDS = {"as_of": None, "positions": [], "summary": {}}


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
    elif command.type == "rank_board":
        rank_board(bus, command.args)
    elif command.type == "model_book":
        model_book(bus, command.args)
