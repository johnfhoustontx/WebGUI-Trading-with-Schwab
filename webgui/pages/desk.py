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
from pages.options.matrix import signal_class as _signal_class
from pages.options.theme import (CON_ACCENT, CON_NEG, CON_POS, CON_TXT,
                                 CON_TXT_DIM, CON_TXT_FAINT, CON_TXT_MUTED,
                                 CON_WARN, CONSOLE_CARD, CONSOLE_COLORS,
                                 CONSOLE_DISPLAY, CONSOLE_FONT_HEAD_HTML,
                                 CONSOLE_KEYFRAMES_CSS, CONSOLE_PAGE,
                                 CONSOLE_RULE)
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


def flip_sub_text(row):
    """'0.49% above' — the qualifier under the flip level, or '' when there is
    none to make.

    The side word is dropped when ``flip_side`` is unknown rather than defaulted:
    "above" is a claim about dealer hedging, and there is no honest default. The
    half-width dealer panel STACKS this under the level instead of printing the
    one long string, which is the only reason it is a function of its own."""
    side = (row or {}).get("flip_side")
    dist = _finite((row or {}).get("flip_distance"))
    if side is None or dist is None:
        return ""
    return f"{dist:.2f}% {side}"


def flip_text(row):
    """'6,680.00 · 0.49% above' — the flip level and where spot sits on it.

    Composed from the two halves the panel renders separately, so the one-line
    and the stacked readings can never say different things."""
    level = fmt_price((row or {}).get("flip"))
    if level == _DASH:
        return _DASH
    sub = flip_sub_text(row)
    return f"{level} · {sub}" if sub else level


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
def _chip(hexv, fill=0.12, weight="", wrap=False, track=".16em",
          pad="px-[7px]"):
    """A chip in the reference design's shape: 8px caps on wide tracking, a 2px
    radius, and a SOLID 1px border in the same hue as the text — an outline, not
    a filled badge.

    ``fill=None`` emits no background class at all, which is what the reference
    specifies for the dealer regime chip (border + colour, nothing else). It is
    spelled as an absent class rather than a zero alpha so the generated string
    can never depend on the JIT accepting ``/[0.0]``.

    ``wrap=True`` lets the text break onto a second line INSIDE the border
    instead of running past it. A chip is normally one short word, so
    ``whitespace-nowrap`` would be the natural default — but the two cases that
    matter here are both genuinely long: the dealer regime reads three words,
    and it is the one word on that panel saying whether dealer hedging dampens
    moves or amplifies them, so a truncated "SHORT GAMMA · RU…" would be worse
    than two lines. ``break-words`` rather than plain wrapping, so even a
    single unbreakable word ("RESCUE" in a 44px track) folds instead of running
    past the border it sits in.

    ``track``/``pad`` exist for the two NARROW tracks the reference gives us —
    BOOK at 52px and FLAG at 44px. A chip is sized by its letter-spacing far
    more than by its font size, so tightening the tracking is what buys the fit;
    shrinking the text below 8px would make it unreadable instead.
    """
    flow = "break-words leading-[1.3]" if wrap else "whitespace-nowrap"
    bg = "" if fill is None else f"bg-[{hexv}]/[{fill}]"
    return (f"{pad} py-[2px] rounded-[2px] text-[8px] tracking-[{track}] {flow} "
            f"border border-[{hexv}] {bg} text-[{hexv}] {weight}").strip()


# The book and flag chips live in the 52px and 44px tracks, so they take the
# compact tracking and padding; they also wrap, because "AT RISK" is two words
# and "RESCUE" is one long one.
_TIGHT = {"track": ".1em", "pad": "px-[5px]", "wrap": True}

CHIP_POS = _chip(_C["positive"], **_TIGHT)
CHIP_NEG = _chip(_C["negative"], **_TIGHT)
CHIP_NEG_STRONG = _chip(_C["negative"], fill=0.28, weight="font-semibold",
                        **_TIGHT)
CHIP_WARN = _chip(_C["warning"], **_TIGHT)
CHIP_ACCENT = _chip(_C["accent"], **_TIGHT)
CHIP_MUTED = _chip(_C["muted"], **_TIGHT)

# regime_word → chip. Only the two real readings are coloured; the em-dash
# ("no side known") stays muted rather than borrowing either verdict's colour.
# Both readings wrap (see ``_chip``) — the words stay whole at any column width.
# The em-dash fallback keeps the shared muted chip because ``regime_chip_class``
# is a total function, but the DEALER ROW never draws it: an unknown regime
# renders the dash on its own, with no chip. An empty outlined box reads as a
# broken widget, not as a missing reading.
_REGIME_CHIP = {"LONG GAMMA · PINS": _chip(_C["positive"], wrap=True, fill=None),
                "SHORT GAMMA · RUNS": _chip(_C["negative"], wrap=True,
                                            fill=None)}

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

# ── the reference design's own palette ───────────────────────────────────────
# The supplied design carries a THREE-STEP text ladder — symbol, then spot, then
# the flip level, each a shade softer than the last — and a green/red wall pair.
# The `[console]` vocabulary has neither: `CON_TXT` is a single step, and the
# console's positive/negative sit a shade off these two hues. So the reference
# hexes are written out here as named constants rather than borrowed
# approximations. They are constants, not interpolations, so the class set stays
# finite exactly as the styling standard requires. Everything DATA-driven on this
# page (a sign, a side, a state) still maps through the console tokens, which is
# why those maps are untouched below.
REF_TXT_STRONG = "text-[#eaf2f9]"      # the symbol — the brightest thing in a row
REF_TXT = "text-[#dce7f3]"             # spot
REF_TXT_SOFT = "text-[#cfdae8]"        # the gamma flip level
REF_HEAD_TXT = "text-[#3f5265]"        # column labels
CALL_HEX = "#2dd4a7"                   # the call wall, and its marker on the map
PUT_HEX = "#fb5f7c"                    # the put wall, and its marker
FLIP_HEX = "#f5b841"                   # the gamma flip tick
SPOT_HEX = "#22d3ee"                   # the spot dot
_ROW_RULE = "border-[#0d151e]"         # the rule under a data row
_HEAD_RULE = "border-[#121b26]"        # the (brighter) rule under the labels
_MAP_EDGE = "border-[#14202c]"         # the structure map's two end walls

# Column tracks, shared by each panel's head row and its data rows. They must be
# the SAME string in both places — that identity is the only thing keeping the
# labels over their columns, and it is the first thing to drift if the two are
# written out separately. A column dropped here must be dropped from BOTH the
# head tuple and the row painter, or the labels silently slide one cell across
# and every number on the panel starts reading as the wrong quantity.
#
# The widths are the reference design's, not derived here, and the widest panel
# (Positions: 468px of fixed track + a 180px minimum + eight 10px gaps) is what
# sets the page's two-column breakpoint — see the panel wrapper in ``render``.
# Every qualifier rides as a SECOND LINE inside its owner's cell rather than
# taking a column of its own: the day % under spot, the flip distance under the
# flip, the rationale under the symbol, the expiry under the strikes. That costs
# a line of height instead of a whole column of width, and it keeps each row to
# ONE grid line — which is what puts the structure map beside its symbol instead
# of on a tier of its own. `overflow-x-auto` was deliberately not used as the
# fallback: a dashboard you scroll sideways to read defeats the page's purpose.
_GAP = "gap-x-[10px] gap-y-0"
DEALER_GRID = ("grid grid-cols-[76px_74px_74px_minmax(130px,1fr)_72px_72px_118px]"
               f" {_GAP} w-full")
BOARD_GRID = ("grid grid-cols-[44px_minmax(140px,1fr)_66px_56px_52px_84px] "
              f"{_GAP} w-full")
FLOW_GRID = f"grid grid-cols-[52px_minmax(160px,1fr)_80px] {_GAP} w-full"
POS_GRID = ("grid grid-cols-[52px_minmax(180px,1fr)_40px_48px_48px_128px_32px_"
            f"76px_44px] {_GAP} w-full")

_EYEBROW = f"text-[9.5px] tracking-[.22em] {CON_TXT_DIM}"
_HEAD = f"text-[8px] tracking-[.2em] {REF_HEAD_TXT}"
_ROW = f"items-center px-1 py-[11px] border-b {_ROW_RULE} cursor-pointer"
_VALUE = f"text-[13px] tabular-nums {CON_TXT}"
# The dealer panel's three price columns, one shade apart (see the ladder above).
_V_SPOT = f"text-[14px] tabular-nums {REF_TXT}"
_V_FLIP = f"text-[14px] tabular-nums {REF_TXT_SOFT}"
_SUB = "text-[9px] tabular-nums"           # a cell's second line
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
    """The column labels, on the SAME track list as the rows beneath them.

    ``px-1`` matches the data row's own horizontal padding (``_ROW``), which is
    what actually keeps a label over its column: the padding shrinks the grid's
    content box, and if the two rows disagreed about it every fixed track would
    start 4px out of step with its label."""
    with ui.element("div").classes(f"{grid} px-1 pb-2 border-b {_HEAD_RULE}"):
        for text in labels:
            ui.label(text).classes(_HEAD)


def _cell(text, extra=""):
    return ui.label(text).classes(f"{_VALUE} {extra}".strip())


def _stack():
    """A cell holding a value and its qualifier. ``min-w-0`` so a ``truncate``
    on the second line can actually bite — a grid item's automatic minimum is
    its content, so without it a long sub-label pushes the track wider than the
    panel instead of ellipsing."""
    return ui.column().classes("gap-[3px] min-w-0")


def _structure_map(pos):
    """The put-wall → call-wall span with spot and the gamma flip marked on it.

    This is a CELL of the dealer row (the flexible 4th track), not a tier of its
    own: the whole reading is "where does price sit between the walls", which is
    only legible beside the symbol and the two wall prices it refers to.

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
    # Two hairline uprights instead of a filled track: the map is a SPAN, and
    # its ends are the only part of it that is a fixed fact.
    with ui.element("div").classes(
            f"relative h-[34px] w-full border-l border-r {_MAP_EDGE} px-[2px]"):
        ui.element("div").classes(
            f"absolute top-[6px] bottom-[4px] w-[2px] bg-[{PUT_HEX}] "
            f"shadow-[0_0_7px_rgba(251,95,124,0.7)] "
            f"{_K.left_class(pos['put_wall'])}")
        # The call wall sits at 100%, so without pulling it back by its own
        # width it would hang past the map and widen the whole panel by 2px —
        # enough to make the row report a horizontal overflow.
        ui.element("div").classes(
            f"absolute top-[6px] bottom-[4px] w-[2px] ml-[-2px] "
            f"bg-[{CALL_HEX}] shadow-[0_0_7px_rgba(45,212,167,0.7)] "
            f"{_K.left_class(pos['call_wall'])}")
        if pos.get("flip") is not None:
            ui.element("div").classes(
                f"absolute top-[2px] bottom-0 w-px bg-[{FLIP_HEX}] "
                f"opacity-[.85] {_K.left_class(pos['flip'])}")
            # Named, because a lone amber hairline between two glowing walls is
            # not self-explanatory. `ml-[-6px]` half-centres it on the tick.
            ui.label("FLIP").classes(
                f"absolute bottom-[-3px] ml-[-6px] text-[8px] "
                f"text-[#4b6070] {_K.left_class(pos['flip'])}")
        if pos.get("spot") is not None:
            # A ring, not a dot: it has to read as a position ON the span
            # rather than as a third wall. `ml-[-5px]` centres its 11px on the
            # percentage (a negative margin, so nothing overflows to the right).
            ui.element("div").classes(
                f"absolute top-[10px] w-[11px] h-[11px] ml-[-5px] rounded-full "
                f"border-2 border-[{SPOT_HEX}] bg-[#06121a] "
                f"shadow-[0_0_12px_rgba(34,211,238,0.75)] "
                f"{_K.left_class(pos['spot'])}")


def render():
    """Mount the Desk: a top strip and a 2x2 panel grid over ``VIEWS``.

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

        # The four panels sit in a 2x2 grid, reading left-to-right then down in
        # the order the page argues: structure, then what to act on, then what is
        # already on. `items-stretch` so the two panels of a row square off at
        # the same height instead of leaving a stepped edge between them.
        #
        # The breakpoint is ARITHMETIC, not a Tailwind size name. The Positions
        # row needs 728px of content (468px of fixed track + a 180px minimum for
        # the symbol + eight 10px gaps), and a panel spends 40px of its own on
        # padding; two of those plus the 68px icon rail, the page's own padding
        # and the 20px gutter is ~1700px. Below that the page is ONE column,
        # where a full-width panel carries ~1130px and every grid fits with room
        # to spare — so the narrow layout is the comfortable one, and the two-up
        # layout is the one that has to be earned. `lg:` (1024px) was the old
        # value and it was simply wrong: it promised two columns 675px short of
        # what the widest panel needs.
        with ui.element("div").classes(
                "grid grid-cols-1 min-[1700px]:grid-cols-2 gap-5 w-full "
                "items-stretch"):
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
            # Seven labels for seven tracks. NET GEX and the regime chip share
            # the last one — the chip is the WORD for the number above it, so
            # the label names both.
            _grid_head(DEALER_GRID,
                       ("SYMBOL", "SPOT", "GAMMA FLIP", "STRUCTURE MAP",
                        "CALL WALL", "PUT WALL", "NET GEX / REGIME"))
            for row in rows:
                _dealer_row(row)

    def _dealer_row(row):
        # ONE grid line. The structure map is the flexible 4th cell, so it sits
        # beside the symbol it describes rather than on a tier of its own.
        #
        # The symbol carries no second line: the reference shows a venue under
        # it ("CBOE · INDEX") and this app publishes no venue for a matrix row.
        # The cell stays a single line rather than borrowing something unrelated
        # to fill the space.
        el = ui.element("div").classes(
            f"{DEALER_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            ui.label(row["symbol"]).classes(
                f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
            with _stack():
                ui.label(fmt_price(row["spot"])).classes(_V_SPOT)
                ui.label(fmt_signed_pct(row["day_pct"])).classes(
                    f"{_SUB} {signed_class(row['day_pct'])}")
            with _stack():
                ui.label(fmt_price(row["flip"])).classes(_V_FLIP)
                # Empty string, not an em-dash: the level above it already
                # carries the dash when there is no flip to report.
                ui.label(flip_sub_text(row)).classes(
                    f"{_SUB} {flip_side_class(row['flip_side'])}")
            if row["structure"] is None:
                # No walls (or no placeable spot) is a missing READING, so it
                # gets the same em-dash every other cell uses. An empty framed
                # box would read as a widget that failed to draw.
                _cell(_DASH, CON_TXT_MUTED)
            else:
                _structure_map(row["structure"])
            # Each wall in the same hue as its marker on the map, so the number
            # and the tick standing for it are visibly one thing. The reference
            # puts an open-interest figure under each; this app publishes no
            # per-strike OI on a matrix row, so the second line is left out.
            _cell(fmt_price(row["call_wall"]), f"text-[14px] text-[{CALL_HEX}]")
            _cell(fmt_price(row["put_wall"]), f"text-[14px] text-[{PUT_HEX}]")
            with _stack():
                ui.label(fmt_gex(row["net_gex"])).classes(
                    f"text-[13px] font-medium tabular-nums "
                    f"{signed_class(row['net_gex'])}")
                # `self-start` so the chip shrinks to its words. Without it the
                # chip stretches to the full 118px track and, on a row with no
                # regime, would draw an EMPTY outlined box — which reads as a
                # broken widget rather than as an absent reading. Hence the
                # dash-and-no-chip branch as well.
                if row["regime_word"] == _NO_REGIME:
                    ui.label(_NO_REGIME).classes(f"{_SUB} {CON_TXT_MUTED}")
                else:
                    ui.label(row["regime_word"]).classes(
                        f"self-start {regime_chip_class(row['regime_word'])}")
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
            # Six labels for six tracks. The WHY sentence has none: it rides
            # under the symbol as that cell's second line, which is where it
            # was already being read from. SCORE rather than HOTNESS because
            # the 44px track the reference gives this column cannot hold seven
            # letters of 8px caps on .2em tracking — and the panel's own
            # subtitle already says HOTTEST FIVE, so nothing is lost.
            _grid_head(BOARD_GRID,
                       ("SCORE", "SYMBOL", "ATM IV", "NET PREM", "P/C",
                        "SIGNAL"))
            # The bar is proportional to the HOTTEST row on screen, not to 100:
            # hotness has no published ceiling, so a fixed denominator would be
            # an invented scale.
            top = max((r["hotness"] for r in rows if r["hotness"] is not None),
                      default=None)
            for row in rows:
                _board_row(row, top)

    def _board_row(row, top):
        # One grid line, on the same seven-cell discipline as the dealer rows:
        # every qualifier is the second line of the cell it qualifies — the WHY
        # sentence under the symbol, the IV state under the IV, the setup tag
        # under the signal it explains.
        el = ui.element("div").classes(
            f"{BOARD_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            with _stack():
                _cell(fmt_hotness(row["hotness"]), CON_ACCENT)
                with ui.element("div").classes(
                        f"{_K.track_classes()} h-[4px] w-full"):
                    if row["hotness"] is not None and top:
                        ui.element("div").classes(
                            f"absolute left-0 top-0 bottom-0 "
                            f"bg-[{_C['accent']}] "
                            f"{_K.width_class(row['hotness'] / top * 100.0)}")
            with _stack():
                ui.label(row["symbol"]).classes(
                    f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
                # A rationale is up to three clauses of ordinary words, so it
                # takes whatever the flexible track gives it and ellipses the
                # rest — never a track of its own, where it would be truncated
                # to eight characters.
                ui.label(row["rationale"] or _DASH).classes(
                    f"{_SUB} truncate w-full "
                    + (CON_TXT_MUTED if row["rationale"] else CON_TXT_FAINT))
            with _stack():
                _cell(fmt_iv(row["atm_iv"]))
                ui.label(row["iv_state"]).classes(
                    f"text-[9px] {iv_state_class(row['iv_state'])}")
            _cell(fmt_net_prem(row["net_prem_m"]),
                  signed_class(row["net_prem_m"]))
            _cell(fmt_ratio(row["pc_ratio"]))
            with _stack():
                # `self-start` on both chips, for the reason spelled out on the
                # dealer regime chip: a chip stretched to its track is a box
                # around a word rather than a label on it.
                ui.label(row["signal"].upper()).classes(
                    f"self-start px-[5px] py-[2px] rounded-[2px] text-[8px] "
                    f"tracking-[.1em] {_signal_class(row['signal'])}")
                # An empty setup tag renders NO chip: a blank line reads as "no
                # setup", where a "NEUTRAL" chip would read as a finding.
                if row["setup"]:
                    ui.label(row["setup"]).classes(f"self-start {CHIP_ACCENT}")
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
            # Three labels for three tracks. DETAIL rides under the symbol it
            # describes and the side under the kind it qualifies ("Unusual
            # activity / Call"), which leaves the whole flexible track for the
            # detail line — the only cell here long enough to be truncated. It
            # is still "Call"/"Put", never bought/sold: Schwab publishes no
            # time-and-sales tape to this app, so nobody here knows who
            # initiated. DETAIL carries the premium the alert fired on, in the
            # Flow Alerts page's own wording.
            _grid_head(FLOW_GRID, ("TIME", "SYMBOL", "KIND"))
            for row in rows:
                _flow_row(row)

    def _flow_row(row):
        el = ui.element("div").classes(
            f"{FLOW_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            ui.label(row["time"] or _DASH).classes(
                f"text-[11px] tabular-nums {CON_TXT_MUTED}")
            with _stack():
                ui.label(row["symbol"]).classes(
                    f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
                ui.label(row["detail"] or row["text"] or _DASH).classes(
                    f"{_SUB} truncate w-full {CON_TXT_MUTED}")
            # ``_tone_class`` is stamped by the Flow Alerts page from its own
            # finite (type, side) map — borrowed here rather than re-derived,
            # and shared by the kind and the side it qualifies.
            with _stack():
                ui.label(row["kind"]).classes(
                    f"text-[10px] leading-[1.2] {row['_tone_class']}")
                ui.label(row["side"] or _DASH).classes(
                    f"{_SUB} {row['_tone_class']}")
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
            # Nine labels for nine tracks. STRATEGY sits under the symbol it
            # describes and EXPIRY under the strikes it applies to, which is
            # where each of them was already being read from anyway; ENTRY and
            # MARK are the pair the unrealized figure is the difference of, so
            # the row now shows its own arithmetic rather than only its result.
            # The track ORDER is the reference's own — the widths dictate which
            # reading can sit where, and the strikes need the 128px one.
            _grid_head(POS_GRID,
                       ("BOOK", "SYMBOL", "DTE", "ENTRY", "MARK", "STRIKES",
                        "QTY", "UNREALIZED", "FLAG"))
            for row in rows:
                _position_row(row)

    def _position_row(row):
        el = ui.element("div").classes(
            f"{POS_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            ui.label(row["source"]).classes(
                f"self-start {source_chip_class(row['source'])}")
            with _stack():
                ui.label(row["symbol"]).classes(
                    f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
                ui.label(strategy_label(row["strategy"])).classes(
                    f"{_SUB} truncate w-full {CON_TXT_MUTED}")
            _cell(dte_text(row["dte"]))
            # Both are per-share option prices (0.21 / 0.39), not position
            # dollars — the same numbers the paper ledger quotes.
            _cell(fmt_money(row["entry_credit"]))
            _cell(fmt_money(row["current_value"]))
            with _stack():
                _cell(row["strikes"])
                ui.label(row["expiration"] or _DASH).classes(
                    f"{_SUB} truncate w-full {CON_TXT_MUTED}")
            _cell(_DASH if row["quantity"] is None else f"{row['quantity']:g}")
            _cell(fmt_money(row["unrealized_pnl"]),
                  signed_class(row["unrealized_pnl"]))
            ui.label(row["flag"]).classes(
                f"self-start {flag_chip_class(row['flag'])}")
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
