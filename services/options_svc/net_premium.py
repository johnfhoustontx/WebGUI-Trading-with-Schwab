"""Group model + intraday net-premium series for the Dealer Positioning
"Net Prem" view.

PURE — stdlib only, no I/O, no engine imports. ``compute.build_net_premium``
does the DB reading and hands the raw rows here.

Net premium = cumulative call premium ($) − cumulative put premium ($) for a
symbol, per intraday snapshot. Because Schwab serves no time-&-sales tape the
premium is UNSIGNED cumulative traded dollars, so this is a money-weighted
put/call read, NOT net buying. The UI must say so.
"""
from __future__ import annotations

import math

from shared import symbols as _shared_symbols

# The selectable groups + the composite baskets both come from
# config/symbols.toml via shared.symbols. They used to be literals here AND a
# byte-copy in webgui/pages/options/gamma.py (Tier 1 may not import services.*)
# AND, for BIG10, a third copy in services/market_svc/symbols.py - three modules
# that cannot import each other, kept in step only by tests. One file now feeds
# all three; the Tier-1 rule bans importing services.*, not reading a config file.
GROUPS = tuple(_shared_symbols.netprem_groups())

# Composite pseudo-symbols: summed server-side from real tickers. BIG10 holds the
# same members as the Mega-caps group above in a DIFFERENT order - that one is
# display order, this one is basket composition.
BASKETS = {"BIG10": _shared_symbols.big10()}


def display_symbols() -> list:
    """Every symbol the view can plot, in group order (includes baskets)."""
    out: list = []
    for group in GROUPS:
        for sym in group["symbols"]:
            if sym not in out:
                out.append(sym)
    return out


def source_symbols() -> list:
    """The REAL tickers to read from gex_history: display symbols minus the
    baskets, plus every basket's members (deduped, order-preserving)."""
    out: list = []
    for sym in display_symbols():
        members = BASKETS.get(sym)
        for real in (members if members else (sym,)):
            if real not in out:
                out.append(real)
    return out


# The row layout this module parses, from ``gex_history_db.load_flow_series()``:
# (ts, spot, call_vol, put_vol, call_prem, put_prem). Named so the positional
# coupling to that SELECT is greppable from BOTH sides. It matters because a
# reordered SELECT would land call_vol/put_vol at 4/5 — contract counts in the
# thousands where dollars in the millions belong — and both pass the numeric
# guard below, so the chart would render a plausible-looking wrong answer and
# skew mode a plausible-looking percentage. Task 5 pins the layout against a
# real DB; that test needs I/O, so it cannot live here (this module is PURE).
_IDX_TS, _IDX_CALL_PREM, _IDX_PUT_PREM = 0, 4, 5


def _num(value):
    """A finite number, or None.

    Rejects bools (``True`` is an ``int`` in Python, so an unguarded flag would
    silently read as ``ts=1``) and nan/inf: a nan propagates through the basket
    sum and would blank the WHOLE BIG10 column rather than one point, and
    ``json.dumps`` emits a bare ``NaN`` literal — invalid JSON on the way into
    Redis."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _project(rows):
    """``[(ts, spot, cvol, pvol, cprem, pprem), …]`` → ``[[ts, call, put], …]``.

    A row with premium on NEITHER side is skipped: premium is forward-only, so
    ``None`` on both means the columns did not exist yet, and a series simply
    starts where premium began collecting. A collected ``0.0`` is the OPPOSITE —
    a real observation that no premium traded that side — so it is kept, and one
    absent side beside a real one counts as 0.0. That distinction is load-bearing
    downstream: skew mode treats a genuine (0, 0) as unreportable while dollars
    mode plots it as zero.

    Unreadable rows are skipped, not raised on. Reading positionally and
    catching is deliberate rather than an ``isinstance`` gate: it accepts
    anything that indexes like the query's row — ``tuple``, ``list`` and
    ``sqlite3.Row`` alike — while still skipping ``None``, truncated rows, and
    the mappings a ``dict``-style ``row_factory`` would hand back. A type gate
    would drop ``sqlite3.Row`` (the most ordinary factory there is) silently, and
    an empty series is indistinguishable from a symbol nobody collects yet."""
    out = []
    for row in rows or []:
        try:
            ts = _num(row[_IDX_TS])
            call = _num(row[_IDX_CALL_PREM])
            put = _num(row[_IDX_PUT_PREM])
        except (TypeError, IndexError, KeyError):   # None / truncated / mapping
            continue
        if ts is None or (call is None and put is None):
            continue
        out.append([ts,
                    0.0 if call is None else float(call),
                    0.0 if put is None else float(put)])
    out.sort(key=lambda r: r[0])   # Highcharts line series REQUIRE x-ascending
    return out


def build_series(flow_by_symbol) -> dict:
    """``{symbol: [flow rows]}`` → ``{symbol: [[ts, call_prem, put_prem], …]}``.

    Symbols with no usable rows are OMITTED (the page names them as
    "no data yet" rather than drawing an empty line). Baskets are summed from
    their members aligned on ``ts``; a member missing a snapshot contributes
    nothing at that timestamp rather than dropping the column. Dollar sum is the
    only sensible aggregation for money, so a basket's skew is
    ``Σnet ÷ Σ(call+put)`` — the same dollar-weighted convention
    ``market_svc.symbol_premium_skew`` uses.

    The stored values are ALREADY daily-cumulative, so nothing here accumulates
    over the session: every timestamp is summed independently of its neighbours.
    That invariant is what keeps plain float summation safe (~10 values per
    timestamp, worst-case relative error ~1e-15), and it is exactly what a future
    "optimization" into a running total would break.

    A basket key arriving in the INPUT is ignored — baskets are always derived
    here, never passed through — so the output cannot depend on whether the
    caller happened to include one. ``source_symbols()`` never emits one."""
    flow_by_symbol = flow_by_symbol or {}
    out = {}
    for sym, rows in flow_by_symbol.items():
        if sym in BASKETS:          # derived below, never passed through
            continue
        projected = _project(rows)
        if projected:
            out[sym] = projected

    for basket, members in BASKETS.items():
        totals: dict = {}
        for member in members:
            for ts, call, put in out.get(member, ()):
                acc = totals.setdefault(ts, [0.0, 0.0])
                acc[0] += call
                acc[1] += put
        if totals:
            out[basket] = [[ts, c, p] for ts, (c, p) in sorted(totals.items())]
    return out
