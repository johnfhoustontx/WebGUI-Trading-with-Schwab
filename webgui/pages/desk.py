"""The Desk (/desk) — one screen carrying the most decision-relevant element of
every other page, so the morning read is a single glance rather than a tour.

Tier-1 reader: it consumes ``cache:options:matrix``, ``cache:options:paper_account``,
``cache:options:driver_paper_account``, ``cache:options:flow_alerts``,
``cache:options:gex_status`` and ``cache:sentiment:regime`` and renders them. No
engine imports, no Schwab calls, no arithmetic of its own.

**The load-bearing principle: the Desk composes, it never restates.** Every
number here is produced by the same pure function its owning page uses —
``flow.alert_rows`` for the alert feed, ``paper``'s DTE helper for expiries,
``console_regime``'s label derivation for the regime word. This is not tidiness.
The app already carries a documented open bug where ``/sentiment/sectors`` and
``/sentiment/rotation`` print OPPOSITE regime verdicts, because each computed its
own headline from a different quantity on a different scale. A screen that
aggregates ten pages is ten chances to repeat that mistake, and a Desk that
contradicts the page it links to is worse than no Desk.

This module is pure display logic only — module-level functions over plain dicts.
The widgets and wiring land later, and none of it belongs here.
"""
import math

from pages import console_regime as _CR
from pages.options import flow as _flow
from pages.options import paper as _paper

# The four symbols the Desk watches. Deliberately short: the Desk is a glance,
# and the Opportunity Board already exists for the full watchlist.
DESK_SYMBOLS = ("$SPX", "SPY", "QQQ", "$NDX")


def _finite(v):
    """``float(v)`` when it is a real, finite number — otherwise ``None``.

    This is the guard the app's documented NaN trap demands. ``min(hi, nan)``
    returns ``hi`` and ``max(lo, nan)`` returns ``lo`` (every comparison against
    NaN is False, so the running value survives), so an unguarded non-finite
    value does not degrade to "no reading" — it PINS a bound and renders as a
    confident extreme. Filter at the call site; never trust a clamp to notice.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── structure map ────────────────────────────────────────────────────────────
def structure_positions(spot, flip, put_wall, call_wall):
    """Percentage positions along the structure bar, or None if undrawable.

    Returns ``{"put_wall": 0.0, "call_wall": 100.0, "spot": pct, "flip": pct|None}``
    with the walls pinned to the ends, since the bar's whole job is to show where
    price sits BETWEEN them.

    Percentages, not a viewBox: the caller applies them as ``left-[{pct}%]``
    Tailwind arbitrary values. Drawing this as a scaled SVG would need
    ``vector-effect: non-scaling-stroke`` to stop the non-uniform scale smearing
    the strokes, and DOMPurify strips that attribute — leaving strokes thick
    horizontally and hairline vertically while the server-side string stays
    perfectly correct, which is invisible to every test. Never raises.
    """
    lo, hi = _finite(put_wall), _finite(call_wall)
    s = _finite(spot)
    # No walls, no bar — and a non-finite spot is withheld rather than clamped,
    # because the clamp would place it exactly ON a wall (see ``_finite``).
    if lo is None or hi is None or s is None or hi <= lo:
        return None
    span = hi - lo

    def _pct(v):
        f = _finite(v)
        if f is None:
            return None
        return round(min(100.0, max(0.0, (f - lo) / span * 100.0)), 2)

    # The flip is optional decoration on a bar the walls already define, so a
    # missing (or non-finite) flip costs the tick, not the whole bar.
    return {"put_wall": 0.0, "call_wall": 100.0, "spot": _pct(s),
            "flip": _pct(flip)}


# ── dealer positioning rows ──────────────────────────────────────────────────
# ONE regime word, ONE source. ``gex_regime`` (spot vs the flip) is the only
# input; ``net_gex`` is displayed as a magnitude beside it and must never reach
# this map. The two can legitimately disagree — a symbol can sit above its flip
# while net GEX prints negative — and a row that made two conflicting regime
# claims would be the /sentiment/sectors-vs-/sentiment/rotation bug reproduced
# inside a single line of text.
REGIME_WORDS = {"above": "LONG GAMMA · PINS", "below": "SHORT GAMMA · RUNS",
                "na": "—"}
_NO_REGIME = "—"


def regime_word(gex_regime):
    """The dealer-regime headline for a matrix row's ``gex_regime``."""
    return REGIME_WORDS.get(gex_regime, _NO_REGIME)


def _walls_trustworthy(net_gex, stale):
    """Whether this row's call/put walls may be shown at all.

    Two ways they cannot be. **Stale**: the collector has stopped, so the walls
    describe some earlier tape. **net GEX present-but-exactly-zero**: index
    option open interest reads 0 after hours, which yields an all-zero GEX grid,
    and the wall picked out of an all-zero grid is an artefact of the argmax tie-
    break — an arbitrary strike wearing the authority of a level. Absent net GEX
    is NOT that signature (the symbol simply doesn't publish the figure), so it
    keeps its walls.
    """
    if stale:
        return False
    return not (net_gex is not None and net_gex == 0.0)


def dealer_rows(matrix_view, stale):
    """Dealer-positioning rows for ``DESK_SYMBOLS``, in that order.

    ``matrix_view`` is the ``cache:options:matrix`` payload. Symbols the matrix
    does not carry are simply absent — the Desk never invents a row. Total over a
    missing / malformed view.
    """
    rows = (matrix_view or {}).get("rows") if isinstance(matrix_view, dict) else None
    if not isinstance(rows, list):
        return []
    # First row per symbol wins; the matrix publishes one row per symbol, so a
    # duplicate is a producer bug and taking the later one would hide it.
    by_symbol = {}
    for r in rows:
        if isinstance(r, dict) and r.get("symbol") not in by_symbol:
            by_symbol[r.get("symbol")] = r

    out = []
    for sym in DESK_SYMBOLS:
        r = by_symbol.get(sym)
        if r is None:
            continue
        spot, flip = _finite(r.get("spot")), _finite(r.get("flip"))
        net_gex = _finite(r.get("net_gex"))
        show_walls = _walls_trustworthy(net_gex, stale)
        call_wall = _finite(r.get("call_wall")) if show_walls else None
        put_wall = _finite(r.get("put_wall")) if show_walls else None
        side, dist = _flip_read(spot, flip)
        out.append({
            "symbol": sym,
            "spot": spot,
            "day_pct": _finite(r.get("day_pct")),
            "flip": flip,
            "flip_distance": dist,
            "flip_side": side,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "net_gex": net_gex,
            "regime_word": regime_word(r.get("gex_regime")),
            "structure": structure_positions(spot, flip, put_wall, call_wall),
            "stale": bool(stale),
        })
    return out


def _flip_read(spot, flip):
    """``(side, distance_pct)`` — which side of the flip spot sits on, and how far.

    The distance is a MAGNITUDE in percent of the flip level (so $SPX and SPY are
    comparable at a glance); the side carries the sign. ``(None, None)`` whenever
    either input is missing or non-finite — a flip side is a claim about dealer
    hedging, and there is no honest one to make without both numbers.
    """
    if spot is None or flip is None or flip == 0:
        return None, None
    return ("above" if spot >= flip else "below",
            round(abs(spot - flip) / abs(flip) * 100.0, 4))


# ── opportunity board ────────────────────────────────────────────────────────
# dealer_regime → the short setup tag the board prints beside the score. A
# regime with nothing to say ("neutral"/"na"/unknown) prints NOTHING rather than
# a filler word — an empty cell reads as "no setup", where "NEUTRAL" would read
# as a finding.
SETUP_WORDS = {"gamma_cascade": "CASCADE", "vanna_squeeze": "VOL CRUSH",
               "delta_wall_pin": "PIN", "charm_grind": "GRIND",
               "neutral": "", "na": ""}

# The rationale's vocabulary. Each map covers only the states worth a phrase;
# everything else contributes nothing, so the line stays short and every word in
# it is carrying a real reading.
_SETUP_PHRASE = {"gamma_cascade": "cascade risk", "vanna_squeeze": "vol crush",
                 "delta_wall_pin": "pinned at wall", "charm_grind": "charm grind"}
_FLIP_PHRASE = {"above": "above flip", "below": "below flip"}
_TREND_PHRASE = {"strong_up": "strong uptrend", "up": "uptrend",
                 "down": "downtrend", "strong_down": "strong downtrend"}
_ACCEL_PHRASE = {"hot": "hot", "cool": "cooling"}

OPPORTUNITY_LIMIT = 5


def setup_word(dealer_regime):
    """The board's short setup tag for a row's ``dealer_regime``."""
    return SETUP_WORDS.get(dealer_regime, "")


def rationale(row):
    """One short line saying WHY this symbol is near the top, in plain words.

    Built only from state the matrix already publishes — the dealer setup, which
    side of the flip price sits on, the trend, and whichever side of the option
    flow is actually moving. Nothing here is computed; the Desk's job is to read
    the row aloud, not to form a second opinion about it. A row that knows
    nothing gets an empty string, never a hedge sentence.
    """
    r = row if isinstance(row, dict) else {}
    parts = []
    for key, table in ((r.get("dealer_regime"), _SETUP_PHRASE),
                       (r.get("gex_regime"), _FLIP_PHRASE),
                       (r.get("trend_state"), _TREND_PHRASE)):
        phrase = table.get(key)
        if phrase:
            parts.append(phrase)
    # Flow: name the side that is moving. "steady"/"flat" is the resting state
    # and says nothing worth a clause.
    for side, key in (("call", r.get("call_accel")), ("put", r.get("put_accel"))):
        word = _ACCEL_PHRASE.get(key)
        if word:
            parts.append(f"{side} flow {word}")
    return " · ".join(parts[:3])


# Sorts a hotness-less row below every scored one without inventing a score for
# it (0 would be a claim; this is a sort position).
_UNSCORED = float("-inf")


def opportunity_rows(matrix_view, limit=OPPORTUNITY_LIMIT):
    """The hottest ``limit`` symbols from ``cache:options:matrix``, hottest first.

    Deliberately carries NO ``rv``/``edge`` field: realized volatility is not
    collected or published anywhere in this app, so an IV-vs-RV edge cannot be
    computed and a column pretending otherwise would look exactly like one that
    works. ``atm_iv`` + ``iv_state`` are the honest version of that read.
    """
    rows = (matrix_view or {}).get("rows") if isinstance(matrix_view, dict) else None
    if not isinstance(rows, list):
        return []
    scored = [r for r in rows if isinstance(r, dict)]

    def _rank(r):
        # `or _UNSCORED` would be wrong here: a genuine hotness of 0.0 is falsy
        # and would be demoted as if it had no score at all.
        h = _finite(r.get("hotness"))
        return -(_UNSCORED if h is None else h)

    # Stable sort, so equal hotness keeps the matrix's own (hotness-ranked) order.
    scored.sort(key=_rank)
    out = []
    for r in scored[:max(0, int(limit))]:
        out.append({
            "symbol": r.get("symbol", ""),
            "hotness": _finite(r.get("hotness")),
            "rationale": rationale(r),
            "setup": setup_word(r.get("dealer_regime")),
            "atm_iv": _finite(r.get("atm_iv")),
            "iv_state": r.get("iv_state") or "na",
            "signal": r.get("signal") or "neutral",
            "signal_strength": _finite(r.get("signal_strength")),
            "pc_ratio": _finite(r.get("pc_ratio")),
            "net_prem_m": _finite(r.get("net_prem_m")),
        })
    return out


# ── flow feed ────────────────────────────────────────────────────────────────
FLOW_LIMIT = 5


def flow_rows(flow_view, limit=FLOW_LIMIT):
    """The newest ``limit`` flow alerts, newest first.

    Delegates wholesale to ``pages.options.flow.alert_rows`` — it already
    reverses the service's oldest-first list, formats the clock time and the
    per-type detail line, and picks the tone class. Re-deriving any of that here
    would give the Desk a second, drifting copy of the Flow Alerts page.

    Note what the rows deliberately do NOT say: which side INITIATED. Schwab
    exposes no time-and-sales tape to this app, so "call side, 4.4x OI" is the
    whole of what is known — ``flow_alerts.alert_text`` carries the same
    restraint ("No buy/sell claim"), and the Desk must not add one by paraphrase.
    """
    return _flow.alert_rows(flow_view)[:max(0, int(limit))]


# ── open positions (both paper books) ────────────────────────────────────────
# rescue_state → the flag word the card prints. WATCH is deliberately a separate
# word from AT RISK: it means "keep an eye on it", and folding it in would blunt
# the only word on this card meant to make the reader do something.
POSITION_FLAGS = {"ok": "OK", "watch": "WATCH", "tested": "AT RISK",
                  "critical": "RESCUE"}
_DEFAULT_FLAG = "OK"

# The rescue states that genuinely mean "this trade is in trouble" — the same
# pair ``paper._AT_RISK_STATES`` highlights.
AT_RISK_STATES = ("tested", "critical")

# Account views the Desk merges, and the chip each one's rows wear. Two separate
# paper books with two separate P&Ls, so a row that did not say which it came
# from would be unactionable.
PAPER_SOURCE, CLAUDE_SOURCE = "PAPER", "CLAUDE"

# The ledger closes a trade as CLOSED or EXPIRED; a row with no status at all is
# treated as open, matching ``paper_adjust``'s own default.
_CLOSED_STATUSES = ("CLOSED", "EXPIRED")


def position_flag(rescue_state):
    """The flag word for a position's ``rescue_state`` (healthy when unknown)."""
    return POSITION_FLAGS.get(rescue_state, _DEFAULT_FLAG)


def _is_open(p):
    return (p.get("status") or "OPEN").upper() not in _CLOSED_STATUSES


def position_rows(paper_view, driver_view):
    """Open positions from BOTH paper accounts, each tagged with its source.

    Reads the *account* views, not the paper ledger: the ledger carries no live
    mark, so an unrealized P&L taken from it would be entry-time arithmetic
    wearing a live label.
    """
    out = []
    for view, source in ((paper_view, PAPER_SOURCE), (driver_view, CLAUDE_SOURCE)):
        positions = (view or {}).get("positions") if isinstance(view, dict) else None
        if not isinstance(positions, list):
            continue
        for p in positions:
            if not isinstance(p, dict) or not _is_open(p):
                continue
            sk, lk = p.get("short_strike"), p.get("long_strike")
            out.append({
                "source": source,
                "position_id": p.get("position_id"),
                "symbol": p.get("symbol", ""),
                "strategy": p.get("strategy", ""),
                "short_strike": _finite(sk),
                "long_strike": _finite(lk),
                "strikes": f"{sk}/{lk}" if sk is not None else "—",
                "width": _finite(p.get("width")),
                "expiration": p.get("expiration", ""),
                # The paper page's own helper — one calendar for the whole app.
                "dte": _paper._dte_from_expiration(p.get("expiration")),
                "quantity": _finite(p.get("quantity")),
                "entry_credit": _finite(p.get("entry_credit")),
                "current_value": _finite(p.get("current_value")),
                "unrealized_pnl": _finite(p.get("unrealized_pnl")),
                "rescue_state": p.get("rescue_state"),
                "heat": _finite(p.get("heat")),
                "flag": position_flag(p.get("rescue_state")),
            })
    return out


def positions_summary(rows):
    """``{"open": n, "unrealized": float, "at_risk": n}`` over ``position_rows``.

    ``at_risk`` counts TESTED + CRITICAL only. A non-finite P&L is skipped rather
    than summed — ``float('nan') + x`` is NaN, so one bad mark would erase the
    whole book's total and print it as a dash.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    total = 0.0
    for r in rows:
        pnl = _finite(r.get("unrealized_pnl"))
        if pnl is not None:
            total += pnl
    return {
        "open": len(rows),
        "unrealized": round(total, 2),
        "at_risk": sum(1 for r in rows if r.get("rescue_state") in AT_RISK_STATES),
    }


# ── freshness ────────────────────────────────────────────────────────────────
# How old the last GEX snapshot may be before the Desk stops calling the feed
# live. The collector runs on a ONE-MINUTE cadence, and a slot legitimately runs
# late — its chain fan-out eats 15-35 s of the 60 s budget — so a single slot's
# jitter must not raise a warning. 150 s is one whole missed slot plus the
# in-flight one: late is normal, twice late is a problem.
STALE_AFTER_SEC = 150

_UNKNOWN_LABEL = "Data age unknown"


def freshness_facts(gex_status_view):
    """Is the GEX feed current? — the Desk's honest replacement for a green dot.

    The mockup this page came from painted a permanent "STREAMING" pill, which
    is a claim the app cannot back: the collector stops outside its window, and
    it can die mid-session. This reads the age the collector itself publishes.

    **No probe data reads "unknown", never "live"** — the same rule the drawer's
    service-status card follows, and it matters more here, because ``stale``
    gates ``dealer_rows``: a wrong "live" promotes off-hours walls (drawn from an
    all-zero, OI-less grid) back to trustworthy. Note that a closed market reads
    stale, which is correct rather than pessimistic — nothing is collecting, so
    nothing on the screen is a current read of dealer positioning.
    """
    st = gex_status_view if isinstance(gex_status_view, dict) else {}
    age = _finite(st.get("age_seconds"))
    facts = {
        "age_seconds": age,
        "last_scan": st.get("last_scan"),
        "next_scan": st.get("next_scan"),
        "session": st.get("session") or "",
        "status_label": st.get("status_label") or "",
    }
    if age is None:
        return {**facts, "stale": True, "label": _UNKNOWN_LABEL}
    if age <= STALE_AFTER_SEC:
        return {**facts, "stale": False, "label": f"Live · {_age_text(age)}"}
    return {**facts, "stale": True, "label": f"Stale · {_age_text(age)}"}


def _age_text(age):
    """'41s ago' / '4m ago' — matching the collector strip's own phrasing."""
    secs = int(age)
    return f"{secs}s ago" if secs < 60 else f"{secs // 60}m ago"


# ── market regime ────────────────────────────────────────────────────────────
def regime_display(regime_view):
    """The Market Regime word plus the reads that qualify it.

    The word itself comes from ``console_regime.regime_name`` — the SAME
    derivation the Market Regime Console prints, called rather than copied. The
    Desk links to that page; a Desk that named a different regime than the page
    one click away would be worse than showing nothing.

    ``confidence`` is filtered through ``_finite``: a non-finite confidence must
    read as absent, not as a maximal one. That is the documented app-wide trap
    in its most expensive form — an all-NaN price read once scored 92.50 at
    confidence 1.0, a data outage rendering as a confident buy signal.
    """
    r = regime_view if isinstance(regime_view, dict) else {}
    return {
        "word": _CR.regime_name(r),
        "committed_label": r.get("committed_label") or "",
        "confidence": _finite(r.get("confidence")),
        "direction": r.get("direction", 0),
        "direction_strong": bool(r.get("direction_strong")),
        "unclear": bool(r.get("unclear")),
    }
