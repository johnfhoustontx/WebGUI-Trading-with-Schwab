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

The arithmetic is module-level pure functions over plain dicts, so the whole
screen is testable without a browser; ``render()`` at the foot is widgets and
wiring only.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import bus_client
from nicegui import run, ui

from pages import console as _K
from pages import console_regime as _CR
from pages import rings as _rings
from pages.options import flow as _flow
from pages.options import handoff as _handoff
from pages.options import header as _hdr
from pages.options import paper as _paper
from pages.options import theme as _theme
from pages.options.matrix import signal_class as _signal_class
from pages.options.theme import (CON_ACCENT, CON_NEG, CON_POS, CON_TXT,
                                 CON_TXT_DIM, CON_TXT_FAINT, CON_TXT_MUTED,
                                 CON_WARN, CONSOLE_CARD, CONSOLE_COLORS,
                                 CONSOLE_DISPLAY, CONSOLE_DIVIDER,
                                 CONSOLE_FONT_HEAD_HTML, CONSOLE_KEYFRAMES_CSS,
                                 CONSOLE_PAGE, CONSOLE_RULE)
from pages.sentiment import sentiment_arcs as _sentiment_arcs
from pages.sentiment import trend_arcs as _trend_arcs
from pages.ui_guard import guard, guard_async

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


# ── display vocabulary ───────────────────────────────────────────────────────
# Everything below is the RENDER layer: formatters, the finite class maps the
# Tailwind-first standard requires, and ``render()``. The pure builders above
# never reach into it.
_DASH = "—"
_CT = ZoneInfo("America/Chicago")            # the trading clock, not the host's
_C = CONSOLE_COLORS                          # raw hexes, for chips and markers


def fmt_price(v):
    """'6,712.81' — or an em-dash. Never a 0.00 standing in for 'no quote'."""
    f = _finite(v)
    return _DASH if f is None else f"{f:,.2f}"


def fmt_signed_pct(v):
    """'+0.31%' / '-1.20%' / em-dash. The sign is always explicit."""
    f = _finite(v)
    return _DASH if f is None else f"{f:+.2f}%"


def fmt_gex(v):
    """Net gamma exposure as a signed, scaled magnitude: '+1.42B' / '-540M'."""
    f = _finite(v)
    if f is None:
        return _DASH
    sign, a = ("-" if f < 0 else "+"), abs(f)
    if a >= 1e9:
        return f"{sign}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}{a / 1e6:.0f}M"
    if a >= 1e3:
        return f"{sign}{a / 1e3:.0f}K"
    return f"{sign}{a:.0f}"


def fmt_money(v):
    """'$110.00' / '-$40.00' — the shape a P&L wants."""
    f = _finite(v)
    if f is None:
        return _DASH
    return f"-${abs(f):,.2f}" if f < 0 else f"${f:,.2f}"


def fmt_net_prem(v):
    """``net_prem_m`` is ALREADY in millions of dollars, so it is scaled once
    here and never again. Doubling that scale is the classic way this column
    starts printing a plausible thousand-fold error."""
    f = _finite(v)
    return _DASH if f is None else f"{f:+.1f}M"


def fmt_iv(v):
    f = _finite(v)
    return _DASH if f is None else f"{f:.1f}%"


def fmt_ratio(v):
    f = _finite(v)
    return _DASH if f is None else f"{f:.2f}"


def fmt_hotness(v):
    f = _finite(v)
    return _DASH if f is None else f"{f:.0f}"


def flip_text(row):
    """'6,680.00 · 0.49% above' — the flip level and where spot sits on it.

    The side word is dropped when ``flip_side`` is unknown rather than defaulted:
    "above" is a claim about dealer hedging, and there is no honest default."""
    level = fmt_price((row or {}).get("flip"))
    side, dist = (row or {}).get("flip_side"), _finite((row or {}).get("flip_distance"))
    if level == _DASH:
        return _DASH
    if side is None or dist is None:
        return level
    return f"{level} · {dist:.2f}% {side}"


def strategy_label(s):
    """'put_credit_spread' → 'PUT CREDIT SPREAD'."""
    return str(s or "").replace("_", " ").upper() or _DASH


def dte_text(dte):
    """'0DTE' / '32d' / em-dash. Matches the flow page's own 0DTE shorthand."""
    if dte is None:
        return _DASH
    return "0DTE" if dte == 0 else f"{dte}d"


def summary_line(summary):
    """The Positions header: 'OPEN 4 · UNREALIZED $90.00 · AT RISK 2'."""
    s = summary or {}
    return (f"OPEN {s.get('open', 0)} · "
            f"UNREALIZED {fmt_money(s.get('unrealized'))} · "
            f"AT RISK {s.get('at_risk', 0)}")


# --- chips ------------------------------------------------------------------
# A chip is built ONLY from a palette hex, and every call site passes a constant,
# so the generated class strings come from a finite set — the styling standard's
# requirement. Nothing here ever interpolates a value-derived colour.
def _chip(hexv, fill=0.12, weight=""):
    return (f"px-2 py-[2px] text-[10px] tracking-[.14em] whitespace-nowrap "
            f"border border-[{hexv}]/[0.35] bg-[{hexv}]/[{fill}] "
            f"text-[{hexv}] {weight}").strip()


CHIP_POS = _chip(_C["positive"])
CHIP_NEG = _chip(_C["negative"])
CHIP_NEG_STRONG = _chip(_C["negative"], fill=0.28, weight="font-semibold")
CHIP_WARN = _chip(_C["warning"])
CHIP_ACCENT = _chip(_C["accent"])
CHIP_MUTED = _chip(_C["muted"])

# regime_word → chip. Only the two real readings are coloured; the em-dash
# ("no side known") stays muted rather than borrowing either verdict's colour.
_REGIME_CHIP = {"LONG GAMMA · PINS": CHIP_POS, "SHORT GAMMA · RUNS": CHIP_NEG}

# The four position flags. RESCUE shares the negative hue with AT RISK but fills
# harder: they are the same KIND of trouble at two depths, and giving RESCUE a
# fifth hue would imply a different axis.
_FLAG_CHIP = {"OK": CHIP_POS, "WATCH": CHIP_WARN, "AT RISK": CHIP_NEG,
              "RESCUE": CHIP_NEG_STRONG}

# The paper book a position came from. Two books, two P&Ls — the chip is what
# makes a merged row actionable.
_SOURCE_CHIP = {PAPER_SOURCE: CHIP_ACCENT, CLAUDE_SOURCE: CHIP_WARN}

# iv_state ∈ {spiking, collapsing, stable, na} (services/options_svc/matrix.py).
# Deliberately NOT green/red: rising IV is neither good nor bad on its own — it
# is good for a buyer and bad for a seller, and this page knows neither.
_IV_STATE_CLASS = {"spiking": CON_WARN, "collapsing": CON_ACCENT,
                   "stable": CON_TXT_MUTED, "na": CON_TXT_FAINT}
_SIDE_CLASS = {"above": CON_POS, "below": CON_NEG}


def regime_chip_class(word):
    return _REGIME_CHIP.get(word, CHIP_MUTED)


def flag_chip_class(flag):
    return _FLAG_CHIP.get(flag, CHIP_MUTED)


def source_chip_class(source):
    return _SOURCE_CHIP.get(source, CHIP_MUTED)


def iv_state_class(state):
    return _IV_STATE_CLASS.get(state, CON_TXT_FAINT)


def flip_side_class(side):
    return _SIDE_CLASS.get(side, CON_TXT_MUTED)


def signed_class(v):
    """Positive / negative / no-reading text colour for a signed number.

    A missing value gets the MUTED class, not the zero colour — an absent P&L
    must not read as a flat one."""
    f = _finite(v)
    if f is None:
        return CON_TXT_MUTED
    if f > 0:
        return CON_POS
    return CON_NEG if f < 0 else CON_TXT_MUTED


# ── the page ─────────────────────────────────────────────────────────────────
# Every cache view the Desk reads, in ONE tuple, because they are polled as one
# batch. This page is the landing page and stays open all day, so a per-view
# poller would be nine Redis round-trips every two seconds for the life of the
# session; ``read_versions`` reads the nine tiny ``{key}:ver`` counters in a
# single pipelined round-trip and only the views that MOVED get deserialized.
VIEWS = ("options:header", "sentiment:regime", "sentiment:composite",
         "sentiment:history", "options:gex_status", "options:matrix",
         "options:flow_alerts", "options:paper_account",
         "options:driver_paper_account")

# Which views each region depends on. A repaint touches only the regions whose
# inputs actually changed — without this, one 2 s header bump would rebuild all
# four panels (and re-emit both ring SVGs) every tick.
_REGION_VIEWS = {
    "strip": ("options:header", "sentiment:regime", "sentiment:composite",
              "sentiment:history", "options:gex_status"),
    # The dealer panel reads gex_status too: freshness is what GATES its walls.
    "dealer": ("options:matrix", "options:gex_status"),
    "board": ("options:matrix",),
    "flow": ("options:flow_alerts",),
    "positions": ("options:paper_account", "options:driver_paper_account"),
}

POLL_SEC = 2.0
CLOCK_SEC = 1.0
RING_PX = 150

# Column tracks, shared by each panel's head row and its data rows. They must be
# the SAME string in both places — that identity is the only thing keeping the
# labels over their columns, and it is the first thing to drift if the two are
# written out separately.
DEALER_GRID = ("grid grid-cols-[76px_120px_170px_minmax(140px,1fr)_100px_100px_"
               "104px_150px] gap-4 items-center w-full")
BOARD_GRID = ("grid grid-cols-[104px_74px_minmax(150px,1fr)_112px_78px_66px_"
              "78px_88px] gap-4 items-center w-full")
FLOW_GRID = ("grid grid-cols-[76px_128px_70px_minmax(160px,1fr)_86px] gap-4 "
             "items-center w-full")
POS_GRID = ("grid grid-cols-[78px_70px_minmax(130px,1fr)_108px_66px_54px_100px_"
            "92px] gap-4 items-center w-full")

_EYEBROW = f"text-[9.5px] tracking-[.22em] {CON_TXT_DIM}"
_HEAD = f"text-[9.5px] tracking-[.2em] {CON_TXT_DIM}"
_ROW = f"py-[9px] border-b {CONSOLE_DIVIDER} cursor-pointer"
_VALUE = f"text-[13px] tabular-nums {CON_TXT}"
_PLACEHOLDER = f"text-[11.5px] {CON_TXT_MUTED} py-4"

# The service is cold vs the service is fine and has nothing to say. Rendering
# the same words for both would make a dead service indistinguishable from a
# quiet market — which is the whole reason this page must never print a zero it
# did not read.
WAITING_OPTIONS = "Waiting for the options service…"

# The VIX band badge is repainted in place, so its previous background has to be
# removed explicitly or the classes stack and the first band painted wins
# forever. Derived from ``header.regime_badge_class`` over its finite label set
# rather than reaching for that module's private set, so this follows a palette
# change there instead of drifting from it.
_VIX_BANDS = ("Low vol", "Normal", "Elevated", "High vol", "")
_ALL_VIX_BG = " ".join(sorted({_hdr.regime_badge_class(b) for b in _VIX_BANDS}))
# Same problem for the reactive text colours in the strip.
_ALL_STATE_TEXT = " ".join(sorted({CON_POS, CON_NEG, CON_WARN, CON_TXT,
                                   CON_TXT_MUTED}))
_ALL_DOT_BG = f"bg-[{_C['warning']}] bg-[{_C['positive']}] con-pulse"


def _panel(title, subtitle=""):
    """A console card with a titled head; returns the BODY container.

    The head is built ONCE and the body is what each painter clears, so a
    repaint can neither duplicate the title nor strand a handle to it."""
    with ui.column().classes(f"{CONSOLE_CARD} w-full px-5 pt-4 pb-4 gap-2"):
        with ui.row().classes(
                f"items-baseline justify-between w-full gap-4 border-b "
                f"{CONSOLE_RULE} pb-2"):
            ui.label(title).classes(
                f"{CONSOLE_DISPLAY} text-[18px] font-bold tracking-[.16em] "
                f"{CON_TXT}")
            if subtitle:
                ui.label(subtitle).classes(
                    f"text-[10px] tracking-[.2em] whitespace-nowrap "
                    f"{CON_TXT_DIM}")
        body = ui.column().classes("w-full gap-0")
    return body


def _grid_head(grid, labels):
    with ui.element("div").classes(f"{grid} pb-1"):
        for text in labels:
            ui.label(text).classes(_HEAD)


def _cell(text, extra=""):
    return ui.label(text).classes(f"{_VALUE} {extra}".strip())


def _structure_bar(pos):
    """The put-wall → call-wall track with spot and the gamma flip marked on it.

    Positioned divs at ``left-[{pct}%]``, NOT a scaled ``viewBox`` SVG: that
    would need ``vector-effect: non-scaling-stroke`` to stop the non-uniform
    scale smearing the strokes, and NiceGUI's bundled DOMPurify strips that
    attribute — the strokes render thick horizontally and hairline vertically
    while the server-side string stays perfectly correct, so no test can see it.
    This repo has been bitten by that twice.

    The percentages are a genuinely continuous computed position, which is the
    documented exception to the map-to-a-finite-palette rule; every COLOUR below
    is a fixed palette constant.
    """
    with ui.element("div").classes(f"{_K.track_classes()} h-[10px] w-full"):
        # The walls ARE the ends of the scale, so they are drawn as the bar's
        # own end caps — the right one pulled back by its own width so it does
        # not hang outside the track.
        ui.element("div").classes(
            f"absolute top-0 bottom-0 w-[2px] bg-[{_C['positive']}] "
            f"{_K.left_class(pos['put_wall'])}")
        ui.element("div").classes(
            f"absolute top-0 bottom-0 w-[2px] -translate-x-full "
            f"bg-[{_C['negative']}] {_K.left_class(pos['call_wall'])}")
        if pos.get("flip") is not None:
            ui.element("div").classes(
                f"absolute top-0 bottom-0 w-[2px] -translate-x-1/2 "
                f"bg-[{_C['warning']}] {_K.left_class(pos['flip'])}")
        if pos.get("spot") is not None:
            ui.element("div").classes(
                f"absolute top-1/2 w-[9px] h-[9px] rounded-full "
                f"-translate-x-1/2 -translate-y-1/2 bg-[{_C['text']}] "
                f"{_K.left_class(pos['spot'])} "
                f"{_theme.console_glow(_C['text'], px=8, alpha=0.55)}")


def render():
    """Mount the Desk: a top strip and four stacked panels over ``VIEWS``.

    No Highcharts anywhere on this page, deliberately: nothing here is a time
    series, and this app's chart element collapses when it mounts hidden, has no
    ResizeObserver, and loses in-place updates the moment the stock module is
    loaded. Positioned divs and one shared SVG ring builder carry the graphics.
    """
    if CONSOLE_FONT_HEAD_HTML:
        ui.add_head_html(CONSOLE_FONT_HEAD_HTML)
    # The console vocabulary's ONE escape hatch: a keyframes animation cannot be
    # expressed as a utility class.
    ui.add_css(CONSOLE_KEYFRAMES_CSS)

    # ``data`` holds the LAST payload seen for every view, because the poll hands
    # over only the ones that moved and most regions read more than one view.
    state = {"versions": {}, "data": {}}

    with ui.column().classes(
            f"{CONSOLE_PAGE} {CONSOLE_DISPLAY} w-full gap-4 p-4"):
        # ── top strip ────────────────────────────────────────────────────────
        # Deliberately carries NO SPX/QQQ quote. The Dealer Positioning panel
        # below shows those same symbols with far more context, and the two
        # would come from different cache keys with independent version
        # counters — so a 2-second window could genuinely show two different
        # prices for one symbol on one screen. $VIX is excluded from the matrix
        # universe by design and so can never appear as a dealer row, which is
        # why it is the one quote that belongs up here.
        with ui.row().classes(
                f"{CONSOLE_CARD} w-full items-center gap-8 px-5 py-4 "
                f"flex-wrap"):
            with ui.column().classes("gap-1"):
                ui.label("SESSION").classes(_EYEBROW)
                clock_lbl = ui.label(_DASH).classes(
                    f"text-[22px] leading-none tabular-nums {CON_TXT}")
            with ui.column().classes("gap-1"):
                ui.label("VIX").classes(_EYEBROW)
                with ui.row().classes("items-center gap-2"):
                    vix_lbl = ui.label(_DASH).classes(
                        f"text-[22px] leading-none tabular-nums {CON_TXT}")
                    # color=None drops Quasar's bg-primary so the mapped
                    # bg-[...] class is what actually paints.
                    vix_badge = ui.badge("", color=None).classes(
                        "text-[9.5px] tracking-[.14em]")
            with ui.column().classes("gap-1 min-w-[190px]"):
                ui.label("MARKET REGIME").classes(_EYEBROW)
                regime_lbl = ui.label(_DASH).classes(
                    f"text-[22px] leading-none font-semibold {CON_TXT}")
                regime_sub = ui.label("").classes(
                    f"text-[10px] {CON_TXT_MUTED}")
            ui.space()
            with ui.row().classes("items-center gap-2"):
                fresh_dot = ui.element("div").classes(
                    "w-[8px] h-[8px] rounded-full shrink-0")
                fresh_lbl = ui.label(_DASH).classes(
                    f"text-[11px] tracking-[.14em] {CON_TXT_MUTED}")
            with ui.row().classes("items-start gap-6"):
                with ui.column().classes("items-center gap-1"):
                    # Distinct uids: ``ring_svg`` namespaces the SVG root DOM id
                    # with them, and these two rings share a page.
                    sent_ring = ui.html(
                        _rings.ring_svg([], uid="desk-sent", size=RING_PX))
                    ui.label("SENTIMENT").classes(_EYEBROW)
                with ui.column().classes("items-center gap-1"):
                    trend_ring = ui.html(
                        _rings.ring_svg([], uid="desk-trend", size=RING_PX))
                    ui.label("TREND").classes(_EYEBROW)

        dealer_body = _panel("DEALER POSITIONING", " · ".join(DESK_SYMBOLS))
        board_body = _panel("OPPORTUNITY BOARD", "HOTTEST FIVE")
        flow_body = _panel("LIVE FLOW ALERTS", "NEWEST FIVE")
        pos_body = _panel("POSITIONS", "PAPER · CLAUDE")

    # ── painters ─────────────────────────────────────────────────────────────
    def _view(name):
        return state["data"].get(name)

    def _mapping(name):
        """``_view`` narrowed to a dict — ``{}`` for anything else.

        ``_view(...) or {}`` is NOT enough: a malformed payload (a half-written
        key, an older writer, a service caught mid-restart) is truthy, so the
        ``or`` passes it straight through and the first ``.get`` takes the whole
        page down. The row builders above all carry their own isinstance guard;
        this is that guard for the payloads read directly here."""
        v = state["data"].get(name)
        return v if isinstance(v, dict) else {}

    def _paint_strip():
        hdr = _mapping("options:header")
        vix_lbl.text = fmt_price(hdr.get("vix"))
        band = (hdr.get("vix_regime") or {}).get("label", "")
        vix_badge.text = band
        vix_badge.classes(remove=_ALL_VIX_BG,
                          add=_hdr.regime_badge_class(band))

        reg = regime_display(_view("sentiment:regime"))
        regime_lbl.text = reg["word"]
        # Colour follows the direction the service committed, and ONLY when it
        # committed one — a fixed green would paint "Retreating" as bullish.
        tone = CON_TXT_MUTED if reg["unclear"] else CON_TXT
        if not reg["unclear"] and reg["direction"]:
            tone = CON_POS if reg["direction"] > 0 else CON_NEG
        regime_lbl.classes(remove=_ALL_STATE_TEXT, add=tone)
        conf = reg["confidence"]
        # A withheld confidence prints NOTHING. It must never print 0% — that
        # is a reading, and "absent" is not one.
        regime_sub.text = "" if conf is None else f"confidence {conf * 100:.0f}%"

        fresh = freshness_facts(_view("options:gex_status"))
        fresh_lbl.text = fresh["label"]
        fresh_lbl.classes(remove=_ALL_STATE_TEXT,
                          add=CON_WARN if fresh["stale"] else CON_POS)
        fresh_dot.classes(
            remove=_ALL_DOT_BG,
            add=(f"bg-[{_C['warning']}]" if fresh["stale"]
                 else f"bg-[{_C['positive']}] con-pulse"))

        comp = _mapping("sentiment:composite")
        hist = _mapping("sentiment:history")
        # Both ring builders are the /sentiment page's own, and both index into
        # what they are handed, so the shape is checked HERE rather than there —
        # a string ``snaps`` would otherwise be iterated one character at a time.
        snaps = hist.get("snaps")
        derived = comp.get("derived")
        sent_ring.content = _rings.ring_svg(
            _sentiment_arcs(comp.get("live"),
                            snaps if isinstance(snaps, list) else []),
            uid="desk-sent", size=RING_PX)
        trend_ring.content = _rings.ring_svg(
            _trend_arcs(derived if isinstance(derived, dict) else {}),
            uid="desk-trend", size=RING_PX)

    def _paint_dealer():
        dealer_body.clear()
        matrix = _view("options:matrix")
        fresh = freshness_facts(_view("options:gex_status"))
        with dealer_body:
            if matrix is None:
                ui.label(WAITING_OPTIONS).classes(_PLACEHOLDER)
                return
            rows = dealer_rows(matrix, fresh["stale"])
            if not rows:
                ui.label("No dealer positioning published for these symbols "
                         "yet.").classes(_PLACEHOLDER)
                return
            if fresh["stale"]:
                # Say WHY the walls vanished. A silently wall-less row reads as
                # a broken page; this reads as a stopped feed, which is true.
                ui.label(
                    f"Walls withheld — GEX feed {fresh['label'].lower()}"
                ).classes(f"text-[10px] {CON_WARN} pb-1")
            _grid_head(DEALER_GRID,
                       ("SYMBOL", "SPOT", "GAMMA FLIP", "PUT ↔ CALL WALL",
                        "CALL WALL", "PUT WALL", "NET GEX", "DEALER REGIME"))
            for row in rows:
                _dealer_row(row)

    def _dealer_row(row):
        el = ui.element("div").classes(
            f"{DEALER_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            ui.label(row["symbol"]).classes(
                f"text-[15px] font-semibold {CON_TXT}")
            with ui.column().classes("gap-0"):
                _cell(fmt_price(row["spot"]))
                ui.label(fmt_signed_pct(row["day_pct"])).classes(
                    f"text-[10px] tabular-nums {signed_class(row['day_pct'])}")
            _cell(flip_text(row), flip_side_class(row["flip_side"]))
            if row["structure"] is None:
                # No bar at all rather than an empty track: an empty track
                # invites the eye to read a position out of it.
                ui.label(_DASH).classes(f"text-[13px] {CON_TXT_FAINT}")
            else:
                _structure_bar(row["structure"])
            _cell(fmt_price(row["call_wall"]))
            _cell(fmt_price(row["put_wall"]))
            _cell(fmt_gex(row["net_gex"]))
            ui.label(row["regime_word"]).classes(
                regime_chip_class(row["regime_word"]))
        el.on("click", lambda _e, s=row["symbol"]: _open_gamma(s))

    def _paint_board():
        board_body.clear()
        matrix = _view("options:matrix")
        with board_body:
            if matrix is None:
                ui.label(WAITING_OPTIONS).classes(_PLACEHOLDER)
                return
            rows = opportunity_rows(matrix)
            if not rows:
                ui.label("No ranked symbols yet.").classes(_PLACEHOLDER)
                return
            _grid_head(BOARD_GRID,
                       ("HOTNESS", "SYMBOL", "WHY", "ATM IV", "SIGNAL", "P/C",
                        "NET PREM", "SETUP"))
            # The bar is proportional to the HOTTEST row on screen, not to 100:
            # hotness has no published ceiling, so a fixed denominator would be
            # an invented scale.
            top = max((r["hotness"] for r in rows if r["hotness"] is not None),
                      default=None)
            for row in rows:
                _board_row(row, top)

    def _board_row(row, top):
        el = ui.element("div").classes(
            f"{BOARD_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            with ui.column().classes("gap-1 w-full"):
                _cell(fmt_hotness(row["hotness"]), CON_ACCENT)
                with ui.element("div").classes(
                        f"{_K.track_classes()} h-[4px] w-full"):
                    if row["hotness"] is not None and top:
                        ui.element("div").classes(
                            f"absolute left-0 top-0 bottom-0 "
                            f"bg-[{_C['accent']}] "
                            f"{_K.width_class(row['hotness'] / top * 100.0)}")
            ui.label(row["symbol"]).classes(
                f"text-[15px] font-semibold {CON_TXT}")
            ui.label(row["rationale"] or _DASH).classes(
                f"text-[11.5px] truncate "
                + (CON_TXT_MUTED if row["rationale"] else CON_TXT_FAINT))
            with ui.column().classes("gap-0"):
                _cell(fmt_iv(row["atm_iv"]))
                ui.label(row["iv_state"]).classes(
                    f"text-[10px] {iv_state_class(row['iv_state'])}")
            ui.label(row["signal"].upper()).classes(
                f"px-2 py-[2px] text-[10px] tracking-[.12em] text-center "
                f"{_signal_class(row['signal'])}")
            _cell(fmt_ratio(row["pc_ratio"]))
            _cell(fmt_net_prem(row["net_prem_m"]),
                  signed_class(row["net_prem_m"]))
            # An empty setup tag renders NO chip: a blank cell reads as "no
            # setup", where a "NEUTRAL" chip would read as a finding.
            if row["setup"]:
                ui.label(row["setup"]).classes(CHIP_ACCENT)
            else:
                ui.label("")
        el.on("click", lambda _e: ui.navigate.to("/options/matrix"))

    def _paint_flow():
        flow_body.clear()
        view = _view("options:flow_alerts")
        with flow_body:
            if view is None:
                ui.label(WAITING_OPTIONS).classes(_PLACEHOLDER)
                return
            rows = flow_rows(view)
            if not rows:
                ui.label("No alerts today.").classes(_PLACEHOLDER)
                return
            # SIDE is "Call"/"Put", never bought/sold: Schwab publishes no
            # time-and-sales tape to this app, so nobody here knows who
            # initiated. DETAIL carries the premium the alert fired on, in the
            # Flow Alerts page's own wording.
            _grid_head(FLOW_GRID, ("TIME", "KIND", "SYMBOL", "DETAIL", "SIDE"))
            for row in rows:
                _flow_row(row)

    def _flow_row(row):
        el = ui.element("div").classes(
            f"{FLOW_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            _cell(row["time"] or _DASH, CON_TXT_MUTED)
            # ``_tone_class`` is stamped by the Flow Alerts page from its own
            # finite (type, side) map — borrowed here rather than re-derived.
            ui.label(row["kind"]).classes(
                f"text-[11.5px] {row['_tone_class']}")
            ui.label(row["symbol"]).classes(
                f"text-[13px] font-semibold {CON_TXT}")
            ui.label(row["detail"] or row["text"] or _DASH).classes(
                f"text-[11.5px] truncate {CON_TXT_MUTED}")
            ui.label(row["side"] or _DASH).classes(
                f"text-[11.5px] {row['_tone_class']}")
        el.on("click", lambda _e: ui.navigate.to("/options/flow"))

    def _paint_positions():
        pos_body.clear()
        paper_view = _view("options:paper_account")
        driver_view = _view("options:driver_paper_account")
        with pos_body:
            if paper_view is None and driver_view is None:
                ui.label(WAITING_OPTIONS).classes(_PLACEHOLDER)
                return
            rows = position_rows(paper_view, driver_view)
            summary = positions_summary(rows)
            ui.label(summary_line(summary)).classes(
                f"text-[11px] tracking-[.16em] pb-2 "
                + (CON_WARN if summary["at_risk"] else CON_TXT_MUTED))
            if not rows:
                ui.label("No open positions.").classes(_PLACEHOLDER)
                return
            _grid_head(POS_GRID,
                       ("BOOK", "SYMBOL", "STRATEGY", "STRIKES", "EXPIRY",
                        "QTY", "UNREALIZED", "FLAG"))
            for row in rows:
                _position_row(row)

    def _position_row(row):
        el = ui.element("div").classes(
            f"{POS_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            ui.label(row["source"]).classes(source_chip_class(row["source"]))
            ui.label(row["symbol"]).classes(
                f"text-[13px] font-semibold {CON_TXT}")
            ui.label(strategy_label(row["strategy"])).classes(
                f"text-[11px] truncate {CON_TXT_MUTED}")
            _cell(row["strikes"])
            _cell(dte_text(row["dte"]), CON_TXT_MUTED)
            _cell(_DASH if row["quantity"] is None else f"{row['quantity']:g}")
            _cell(fmt_money(row["unrealized_pnl"]),
                  signed_class(row["unrealized_pnl"]))
            ui.label(row["flag"]).classes(flag_chip_class(row["flag"]))
        el.on("click", lambda _e, r=row: _open_position(r))

    # ── click-through ────────────────────────────────────────────────────────
    @guard
    def _open_gamma(symbol):
        """Open Dealer Positioning on this symbol.

        ``handoff.send_to_gamma`` both stashes the symbol — one-shot, so it
        cannot silently re-hijack the gamma dropdown on a later build — and
        navigates. This must not add a second stash or a second navigate."""
        _handoff.send_to_gamma(symbol)

    @guard
    def _open_position(row):
        """Each book has its own page; the source chip is what decides which."""
        ui.navigate.to("/driver" if row.get("source") == CLAUDE_SOURCE
                       else "/options/paper")

    painters = {"strip": _paint_strip, "dealer": _paint_dealer,
                "board": _paint_board, "flow": _paint_flow,
                "positions": _paint_positions}

    def _paint(payloads):
        """Merge the changed views in, then repaint only what depends on them."""
        state["data"].update(payloads)
        changed = set(payloads)
        for region, deps in _REGION_VIEWS.items():
            if changed.intersection(deps):
                painters[region]()

    @guard
    def _tick_clock():
        clock_lbl.text = datetime.now(_CT).strftime("%H:%M:%S")

    @guard_async
    async def _poll():
        """ONE batched version probe per tick, not one per view.

        ``read_versions`` reads the nine tiny ``{key}:ver`` counters in a single
        pipelined round-trip; a full payload is deserialized only for a view
        that actually moved."""
        vers = await run.io_bound(bus_client.read_versions, list(VIEWS))
        changed = [v for v in VIEWS
                   if vers.get(v) is not None
                   and vers.get(v) != state["versions"].get(v)]
        if not changed:
            return
        payloads = {}
        for v in changed:
            payloads[v] = await run.io_bound(bus_client.read, v)
            state["versions"][v] = vers.get(v)
        _paint(payloads)

    # First paint: seed every version so the first poll reports only genuine
    # movement, and paint every region once — including the ones whose view is
    # cold, so each shows its OWN placeholder rather than an empty card. One
    # dead service must not blank the page.
    seed = {}
    for view in VIEWS:
        payload, version = bus_client.read_full(view)
        seed[view] = payload
        state["versions"][view] = version
    _tick_clock()
    _paint(seed)
    ui.timer(CLOCK_SEC, _tick_clock)
    ui.timer(POLL_SEC, _poll)
