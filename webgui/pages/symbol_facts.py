"""The Symbol Dossier's fact-builders — every number the ``/symbol`` page shows,
assembled from the cache views (and, for what they lack, the on-demand
``cache:options:dossier:<SYMBOL>`` fetch).

PURE: no widgets, no bus reads, no I/O, never raises. The page reads the views
and hands them in; everything here is a function of its arguments.

Two rules run through the whole module:

* **An absent reading is ``None``, never ``0``.** Every number passes through
  ``fmt.num`` (which rejects NaN, ±inf and bool), and every derived figure is
  ``None`` the moment one of its inputs is. A ``0`` would claim a measurement
  that was never taken — this repo's most expensive bug class.
* **Coverage is a property of the FACT, not of the symbol** (see the design,
  ``docs/plans/2026-09-17-symbol-dossier-design.md``): a collected-only symbol
  such as ``$VIX`` has a structure panel but no Vol Rank, so the page asks per
  fact where it came from rather than per symbol whether it is "known".

Tier 1: imports only ``pages.*`` and ``shared.symbols``.
"""
import math

from pages import bullbear as _bb
from pages import desk as _desk
from pages.fmt import num as _num  # the ONE copy (pages/fmt.py)
from pages.options import flow as _flow
from pages.options import scanner as _scanner
from shared.symbols import clean_symbol

# ── coverage ────────────────────────────────────────────────────────────────
SCANNED = "scanned"      # a matrix row AND a scan-funnel account
COLLECTED = "collected"  # a matrix row only ($VIX, the sector ETFs)
UNKNOWN = "unknown"      # neither — anything typed

# ── the fact vocabulary ─────────────────────────────────────────────────────
# The dossier payload's keys (services/options_svc/dossier.py DOSSIER_KEYS, less
# its ``symbol`` / ``error`` / ``fetched_at`` bookkeeping) plus the matrix-only
# facts the fetch never supplies.
NUMERIC_FACTS = ("spot", "day_pct", "flip", "put_wall", "call_wall",
                 "iv_rank", "current_iv", "hv_current", "net_gex", "atm_iv")
TEXT_FACTS = ("earnings_date", "earnings_status",
              "iv_state", "dealer_regime", "gex_regime")
FACT_KEYS = NUMERIC_FACTS + TEXT_FACTS

_MATRIX_NUMERIC = ("spot", "day_pct", "flip", "put_wall", "call_wall",
                   "net_gex", "atm_iv")
_MATRIX_TEXT = ("iv_state", "dealer_regime", "gex_regime")
_FUNNEL_NUMERIC = ("iv_rank", "current_iv", "hv_current")

SOURCE_CACHE = "cache"
SOURCE_FETCH = "fetch"

# ── IV vs HV ────────────────────────────────────────────────────────────────
# Mirrors options-scanner/strategy_scoring.py ``infer_market_view``'s fallback
# (``iv_hv >= 1.2`` -> "high", ``iv_hv <= 0.9`` -> "low", else "mid"). Those are
# BARE LITERALS there and Tier 1 cannot import that module, so the numbers are
# restated here — and shared/tests/test_cross_tier_mirrors.py AST-parses both
# files and fails if either side moves alone. The band WORDS are the scorer's
# too, so the dossier can never describe a symbol in terms the scorer disagrees
# with.
IV_HV_HIGH = 1.2
IV_HV_LOW = 0.9

# ── expected move ───────────────────────────────────────────────────────────
_EM_DAYS = {"day": 1, "week": 7}
_DAYS_PER_YEAR = 365

# ── the three books ──────────────────────────────────────────────────────────
# (tag, view, list key). The ledger is included here where the Desk leaves it
# out: the Desk totals a live P&L and the ledger carries no live mark, but a
# dossier's question is "do I hold anything in this name" — and a Paper-button
# trade lives only in the ledger.
BOOK_VIEWS = (
    ("account", "options:paper_account", "positions"),
    ("ledger", "options:paper_trades", "trades"),
    ("captured", "options:captured", "signals"),
)


def _sym(v):
    """A ticker normalised for comparison, or None — case/whitespace-blind."""
    return clean_symbol(v) if isinstance(v, str) else None


def _matrix_row(symbol, matrix):
    sym = _sym(symbol)
    rows = matrix.get("rows") if isinstance(matrix, dict) else None
    if sym is None or not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and _sym(row.get("symbol")) == sym:
            return row
    return None


def _funnel_account(symbol, funnel):
    sym = _sym(symbol)
    accounts = funnel.get("symbols") if isinstance(funnel, dict) else None
    if sym is None or not isinstance(accounts, dict):
        return None
    for key, acct in accounts.items():
        if _sym(key) == sym and isinstance(acct, dict):
            return acct
    return None


def symbol_coverage(symbol, matrix, funnel):
    """``"scanned"`` | ``"collected"`` | ``"unknown"`` for ``symbol``.

    The universes nest — the scan watchlist is part of the collection list — so
    a funnel account WITHOUT a matrix row is a cold matrix, not a scanned
    symbol, and reads ``unknown``. Cold or malformed caches never raise.
    """
    if _matrix_row(symbol, matrix) is None:
        return UNKNOWN
    return SCANNED if _funnel_account(symbol, funnel) is not None else COLLECTED


def needs_fetch(coverage):
    """Whether the page should enqueue a ``dossier`` command.

    Only a scanned symbol gets every fact from the cache; a collected one lacks
    Vol Rank, IV vs HV and earnings, and an unknown one lacks everything.
    """
    return coverage != SCANNED


def iv_vs_hv(current_iv, hv_current):
    """``{"ratio", "band"}`` — ATM implied vol over 30-day realized vol.

    Both are PERCENTS on the same basis, so the units cancel. Band words and
    inclusive boundaries are the scorer's (see ``IV_HV_HIGH``). A missing leg, a
    non-positive HV (the scorer's own ``hv_current > 0`` guard — never divide by
    zero) or a negative IV (Schwab's ``-999`` sentinel) is ``{"ratio": None,
    "band": "na"}``.
    """
    iv, hv = _num(current_iv), _num(hv_current)
    if iv is None or hv is None or hv <= 0 or iv < 0:
        return {"ratio": None, "band": "na"}
    ratio = iv / hv
    if ratio >= IV_HV_HIGH:
        band = "high"
    elif ratio <= IV_HV_LOW:
        band = "low"
    else:
        band = "mid"
    return {"ratio": ratio, "band": band}


def expected_move(spot, atm_iv_pct):
    """1-sigma dollar moves over one and seven calendar days.

    ⚠ ``atm_iv_pct`` is a PERCENT (the matrix's ``atm_iv``: 48.5 means 48.5%) —
    the decimal trap ``services/options_svc/compute.py`` ``scan_vol_inputs``
    documents. A non-positive spot or IV is no reading, so it yields ``None``
    rather than a zero move.
    """
    s, iv = _num(spot), _num(atm_iv_pct)
    if s is None or iv is None or s <= 0 or iv <= 0:
        return dict.fromkeys(_EM_DAYS)
    return {k: s * (iv / 100.0) * math.sqrt(d / _DAYS_PER_YEAR)
            for k, d in _EM_DAYS.items()}


def _text(v):
    return v if isinstance(v, str) and v else None


def cached_facts(symbol, matrix, funnel):
    """The cache's facts for ``symbol`` in the dossier payload's key vocabulary.

    Always carries every ``FACT_KEYS`` key, ``None`` where the cache has no
    reading. Numbers go through ``num``; the matrix's documented ``"na"`` degrade
    strings stay ``"na"``. Spot is the matrix's one-minute read, falling back to
    the scan funnel's quote. The funnel carries the earnings DATE its gate read
    but never the three-valued status, so ``earnings_status`` is never cached.
    """
    out = dict.fromkeys(FACT_KEYS)
    row = _matrix_row(symbol, matrix) or {}
    acct = _funnel_account(symbol, funnel) or {}
    for k in _MATRIX_NUMERIC:
        out[k] = _num(row.get(k))
    for k in _MATRIX_TEXT:
        out[k] = _text(row.get(k))
    for k in _FUNNEL_NUMERIC:
        out[k] = _num(acct.get(k))
    if out["spot"] is None:
        out["spot"] = _num(acct.get("price"))
    out["earnings_date"] = _text(acct.get("earnings_date"))
    return out


def _reading(key, v):
    return _num(v) if key in NUMERIC_FACTS else _text(v)


def merge_facts(cached, fetched):
    """Cache wins; the fetch only fills gaps.

    A fetch is a point-in-time snapshot while the matrix is one-minute fresh, so
    preferring the fetch would make a scanned symbol WORSE after a Refresh.
    ``0.0`` is a real reading and is kept; ``None`` and NaN are not.

    Returns ``{"facts": {key: value}, "source": {key: "cache"|"fetch"|None},
    "error": ..., "fetched_at": ...}`` — ``source`` lets the page say "Fetched
    14:32" only beside the facts that really were.
    """
    cached = cached if isinstance(cached, dict) else {}
    fetched = fetched if isinstance(fetched, dict) else {}
    facts, source = {}, {}
    for k in FACT_KEYS:
        c, f = _reading(k, cached.get(k)), _reading(k, fetched.get(k))
        if c is not None:
            facts[k], source[k] = c, SOURCE_CACHE
        elif f is not None:
            facts[k], source[k] = f, SOURCE_FETCH
        else:
            facts[k], source[k] = None, None
    return {"facts": facts, "source": source,
            "error": fetched.get("error"),
            "fetched_at": fetched.get("fetched_at")}


def position_rows(symbol, books):
    """Open rows in ``symbol`` from all three books, each tagged with ``book``.

    ``books`` maps a view name (``BOOK_VIEWS``) to its payload. Open means the
    Desk's rule (``desk._is_open``: not CLOSED / EXPIRED, a missing status is
    open) so the two pages cannot disagree about what is held. Rows are copies;
    the payload is never mutated. Empty or missing books give ``[]``.
    """
    sym = _sym(symbol)
    if sym is None or not isinstance(books, dict):
        return []
    out = []
    for tag, view, list_key in BOOK_VIEWS:
        payload = books.get(view)
        entries = payload.get(list_key) if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            continue
        for p in entries:
            if (isinstance(p, dict) and _sym(p.get("symbol")) == sym
                    and _desk._is_open(p)):
                out.append(dict(p, book=tag))
    return out


def signals_for(symbol, day_env, today=None):
    """Today's signals in ``symbol`` across the day union's three lists.

    Each row is a copy tagged with ``list`` (its ``scanner.DAY_LISTS`` key).
    Goes through ``scanner.day_signals``, whose date gate is load-bearing: a
    failed merge leaves YESTERDAY's envelope in place, ``live=True`` rows and
    all, and that must yield nothing rather than day-old "live" signals.
    """
    sym = _sym(symbol)
    if sym is None or not isinstance(day_env, dict):
        return []
    out = []
    for key in _scanner.DAY_LISTS:
        for row in _scanner.day_signals(day_env, key, today):
            if isinstance(row, dict) and _sym(row.get("symbol")) == sym:
                out.append(dict(row, list=key))
    return out


def alerts_for(symbol, flow_env):
    """Today's flow alerts in ``symbol``, NEWEST first, as ``flow.alert_rows``
    builds them — the Flow Alerts page's own display rows."""
    sym = _sym(symbol)
    if sym is None:
        return []
    return [r for r in _flow.alert_rows(flow_env)
            if _sym(r.get("symbol")) == sym]


def _bullbear_row(symbol, bullbear_env):
    sym = _sym(symbol)
    levels = bullbear_env.get("levels") if isinstance(bullbear_env, dict) else None
    stocks = levels.get("stock") if isinstance(levels, dict) else None
    if sym is None or not isinstance(stocks, list):
        return None
    for row in stocks:
        if isinstance(row, dict) and _sym(row.get("symbol")) == sym:
            return row
    return None


def context_facts(symbol, regime_env, bullbear_env):
    """The market regime and ``symbol``'s place on the Bull / Bear map.

    ``regime`` is ``desk.regime_display`` — the SAME word the Desk and the
    Market Regime Console print — or ``None`` for a cold view (never a
    fabricated "Unclear" when nothing was read). The quadrant is
    ``bullbear.quadrant`` over ``bullbear.row_axes``, the map's own rule; a
    symbol absent from the cascade has quadrant ``None``, while a present row
    with a missing axis reads the map's own ``"unknown"``.
    """
    regime = (_desk.regime_display(regime_env)
              if isinstance(regime_env, dict) and regime_env else None)
    row = _bullbear_row(symbol, bullbear_env)
    quad = _bb.quadrant(*_bb.row_axes(row)) if row is not None else None
    return {"regime": regime, "bullbear": row, "quadrant": quad,
            "quadrant_label": _bb.quadrant_label(quad) if quad else None}
