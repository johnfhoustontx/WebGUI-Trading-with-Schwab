"""The Desk (/desk) — one screen carrying the most decision-relevant element of
every other page, so the morning read is a single glance rather than a tour.

Tier-1 reader: it consumes ``cache:options:matrix``, ``cache:options:paper_account``,
``cache:options:driver_paper_account``, ``cache:options:captured``,
``cache:options:flow_alerts``, ``cache:options:gex_status``,
``cache:sentiment:regime`` and ``cache:sentiment:bullbear`` and renders them.
No engine imports, no Schwab calls, no arithmetic of its own. Every one of them is polled in the SINGLE batched
``read_versions`` in ``_poll`` — see ``VIEWS``.

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
import json
import logging
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# ``alerts`` for its ONE market-hours predicate. The Settings card promises the
# spoken alerts obey "the existing market-hours gate above", so this page must
# read the SAME ``alert_market_hours_only`` setting through the SAME function
# the scanner chime does — a second, voice-only copy of the idea is how two
# gates end up disagreeing about when the market is open.
import alerts as _alerts
import app_settings
import bus_client
import voice as _voice
from nicegui import run, ui

from pages import bullbear as _bb
from pages import console as _K
from pages import console_cards as _CC
from pages import console_regime as _CR
# The map page, for its ONE sentence. ``headline_line`` is the count line
# /sentiment/bullbear prints, pluralisation and empty-payload rule included —
# imported rather than restated, for the reason at the top of this file.
from pages import sentiment_bullbear as _bbmap
from pages.fmt import num as _finite  # the ONE copy (pages/fmt.py)
from pages.options import flow as _flow
from pages.options import handoff as _handoff
from pages.options import header as _hdr
from pages.options import paper as _paper
from pages.options.matrix import signal_class as _signal_class
from pages.options.theme import (CON_ACCENT, CON_NEG, CON_POS, CON_TXT,
                                 CON_TXT_DIM, CON_TXT_FAINT, CON_TXT_MUTED,
                                 CON_WARN, CONSOLE_CARD, CONSOLE_COLORS,
                                 CONSOLE_DISPLAY, CONSOLE_DIVIDER,
                                 CONSOLE_FONT_HEAD_HTML, CONSOLE_KEYFRAMES_CSS,
                                 CONSOLE_PAGE, CONSOLE_RULE)
# ``_TREND_SHORT`` is private only in the sense that /sentiment owns it. It is
# the vocabulary the console's Trend pill prints, and the Desk shows the SAME
# pill — copying the five words here is exactly the drift this page exists to
# avoid, so it is imported rather than restated.
from pages.sentiment import _TREND_SHORT as _TREND_WORDS
from pages.sentiment import sentiment_arcs as _sentiment_arcs
from pages.sentiment import trend_arcs as _trend_arcs
from pages.ui_guard import guard, guard_async
from shared import market_calendar as _cal

# The four symbols the Desk watches. Deliberately short: the Desk is a glance,
# and the Opportunity Board already exists for the full watchlist.
#
# The ORDER pairs each index with its tracking ETF — $SPX with SPY for the S&P
# complex, $NDX with QQQ for the Nasdaq — because those are the two rows a
# reader actually compares. The index carries the real dealer book (its options
# are where the gamma is) and the ETF carries the tradeable one; reading them
# against each other is how a divergence between the two shows up at all. Split
# across the panel, as they were, that comparison needs the reader to skip a row.
DESK_SYMBOLS = ("$SPX", "SPY", "$NDX", "QQQ")

# The trading clock, not the host's — and it sits up here rather than with the
# rest of the display vocabulary because ``countdown_facts`` is a PURE builder
# that needs it. Every session BOUND still comes from ``market_calendar``; this
# is only the zone a naive datetime is read in, which is that module's rule too.
_CT = ZoneInfo("America/Chicago")


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

# How many symbols the board carries. A CONSTANT rather than a literal at the
# call site, and rather than a WORD in the panel subtitle ("HOTTEST FIVE", as it
# read), because all three have to move together — the subtitle is built from it
# and the tests assert against it.
#
# Six, not five. The rows became ONE line each (see ``_board_row``), which took a
# row from 71px to 50px measured, and the panel had room for one more.
#
# ⚠ The rule that did NOT survive measurement on the Flow panel — "size this to
# the panel it shares a grid row with" — DOES hold here, and the difference is
# worth writing down. Flow's neighbour is Positions, whose length is DATA (open
# trades), so a constant can only match it by accident. This panel's neighbour is
# Dealer Positioning, which is exactly ``len(DESK_SYMBOLS)`` rows — deterministic
# — so the two genuinely can be squared off. Re-measured live at 1920px, on the
# reference-scale type ladder (the panel chrome is common to both, so only the
# head and the rows are counted):
#
#   dealer   24px column head + 4 x 62.5px rows = 274px of content
#   board    24px column head + N x 44px rows
#
#   N = 5 -> 244px (30px SHORT of the dealer, and no extra rows at all)
#   N = 6 -> 288px (14px OVER)
#
# `items-stretch` makes the two cards the same height either way, so this only
# decides which panel carries the void — and six puts the smaller void in the
# better place twice over. It is a third of a row, less than the panel's own
# 16px bottom padding; and the dealer panel grows by a ~19px "walls withheld"
# line whenever the GEX feed goes stale, which is most of the day, so six sits
# BETWEEN the dealer's two heights rather than above both. ⚠ The 14px survived
# the whole ladder being rescaled 0.8x — both panels shrank together — but that
# is luck, not invariance: re-measure it, do not re-derive it.
BOARD_ROWS_N = 6


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


def opportunity_rows(matrix_view, limit=BOARD_ROWS_N):
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
# How many alerts the panel carries. It is a CONSTANT rather than a literal at
# the call site because the tests assert against it: the count and the assertion
# have to move together, and a bare 5 in both places is how they stop doing so.
#
# Nine, not five. The rows became ONE line each (see ``_flow_row``), which took
# a row from 71px to 50px measured — so the same panel now carries nearly twice
# the feed for the height it already had.
#
# ⚠ The obvious rule — "pick the count that squares the Flow panel off against
# the Positions panel it shares a grid row with" — does not survive measurement,
# and it is worth writing down why. Positions is DATA-length (both paper books'
# open trades; three of them the day this was measured, for a 269px body), while
# this is a CONSTANT. At three positions the matching count is five, i.e. no
# increase at all, and at ten it would be fifteen. So the two can only agree by
# accident. The panels themselves are always the same height — `items-stretch`
# guarantees that — and the question is only which one carries the void.
# Nine puts it in Positions, which is the right place for it: a book grows
# through the week, where the alert feed is capped by definition.
FLOW_ROWS_N = 9


def flow_rows(flow_view, limit=FLOW_ROWS_N):
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


# ── open positions (all three books) ─────────────────────────────────────────
# rescue_state → the flag word the card prints. WATCH is deliberately a separate
# word from AT RISK: it means "keep an eye on it", and folding it in would blunt
# the only word on this card meant to make the reader do something.
POSITION_FLAGS = {"ok": "OK", "watch": "WATCH", "tested": "AT RISK",
                  "critical": "RESCUE"}
_DEFAULT_FLAG = "OK"

# The flag for a book the rescue overlay never looks at (see ``BOOKS``). It is
# an em-dash and NOT the "OK" default, and that distinction is the whole point:
# ``_DEFAULT_FLAG`` means "the manage cycle inspected this trade and found
# nothing wrong", which is a real finding. Falling through to it for a book
# nobody inspects would print a clean bill of health that no part of this app
# ever issued — the same class of lie as a NaN clamping to a bound and rendering
# as a confident extreme. ⚠ Do not "tidy" this back into the default.
UNTAGGED_FLAG = "—"

# The rescue states that genuinely mean "this trade is in trouble" — the same
# pair ``paper._AT_RISK_STATES`` highlights.
AT_RISK_STATES = ("tested", "critical")

# The three books the panel merges, and the chip each one's rows wear. Three
# separate ledgers with three separate P&Ls, so a row that did not say which it
# came from would be unactionable.
PAPER_SOURCE, CLAUDE_SOURCE, CAPTURED_SOURCE = "PAPER", "CLAUDE", "CAPTURED"

# What each book's payload looks like, as DATA rather than as three near-copies
# of the same loop. The three differ in more than their chip word, and every one
# of those differences is a place a shared loop would quietly paper over:
#
#   list_key   captured publishes ``signals``, not ``positions``. A hardcoded
#              "positions" would simply find nothing and the book would vanish
#              from the panel with no error anywhere.
#   id_key     ``signal_id`` vs ``position_id``.
#   rescue     whether the manage cycle's rescue overlay tags this book at all.
#              It tags the paper ACCOUNT only (see the comment in
#              ``pages/options/captured.py``), so captured rows carry no
#              ``rescue_state`` and no ``heat`` — see ``UNTAGGED_FLAG``.
#   held       whether these are trades somebody is actually IN. Captured
#              signals are ADVISORY: the scanner found them, nobody bought them.
#              That drives two things — there is no quantity to print (rendering
#              1 would state a size this app does not have, and would look
#              exactly like a real one-contract position), and a held trade
#              outranks an advisory one under the cap (see ``_URGENCY_RANK``).
BOOKS = (
    {"source": PAPER_SOURCE, "list_key": "positions", "id_key": "position_id",
     "rescue": True, "held": True},
    {"source": CLAUDE_SOURCE, "list_key": "positions", "id_key": "position_id",
     "rescue": True, "held": True},
    {"source": CAPTURED_SOURCE, "list_key": "signals", "id_key": "signal_id",
     "rescue": False, "held": False},
)

# Where a row's own page lives. Click-through is the whole reason the Desk may
# stay this terse: every row is one click from the page that can act on it, and
# a book with no route would strand its rows here.
POSITION_ROUTES = {PAPER_SOURCE: "/options/paper", CLAUDE_SOURCE: "/driver",
                   CAPTURED_SOURCE: "/options/captured"}

# The ledger closes a trade as CLOSED or EXPIRED; a row with no status at all is
# treated as open, matching ``paper_adjust``'s own default. The captured-signals
# store uses the same two words, so all three books share one rule.
_CLOSED_STATUSES = ("CLOSED", "EXPIRED")

# How many rows the panel actually draws — see ``POSITION_ROWS_N``. The pure
# builder returns the WHOLE book; slicing is the render layer's business,
# because ``positions_summary`` has to total what is not on screen too.
POSITION_ROWS_N = 8

# The sort, in three keys. All three exist because the panel draws eight rows
# out of a book that ran to thirty-five the day this was measured, so the ORDER
# is what decides whether the cap is safe or is a defect.
#
# 1. URGENCY. At-risk states first, so the cap can never hide a trade in trouble
#    behind a healthy one. An unsorted list would push every RESCUE row off the
#    bottom the moment the captured book grew.
_URGENCY_RANK = {"critical": 0, "tested": 1}
_CALM = 2                       # everything else, including an untagged book

# 2. HELD BEFORE ADVISORY. Measured live before this key existed: 30 captured
#    signals at 2 DTE against 3 paper positions at 9 DTE meant every visible row
#    was a captured signal, and a panel titled POSITIONS showed no positions at
#    all. Money at risk outranks a suggestion nobody acted on. This sits BELOW
#    urgency, never above it — a tested paper spread and a tested driver spread
#    still lead the panel — and it only ever reorders the calm tier, since the
#    advisory book carries no rescue state to be urgent with.
_HELD_FIRST, _ADVISORY_LAST = 0, 1

# 3. NEAREST EXPIRY, not largest absolute unrealized P&L. Size is not urgency: a
#    $500 loser at 45 DTE has weeks to mean-revert, while a spread at 2 DTE has
#    to be decided today — gamma and assignment risk both accelerate into
#    expiry, and expiry is the one clock nobody can stop. Ranking on |unrealized|
#    would also mix winners and losers into one order, so the biggest WINNER on
#    the book would displace a small loser about to expire. A row whose
#    expiration will not parse sorts LAST rather than first: an unreadable date
#    is not evidence of urgency.
_NO_DTE = 10 ** 6


def position_flag(rescue_state, rescue_tagged=True):
    """The flag word for a position's ``rescue_state``.

    ``rescue_tagged=False`` is the explicit "nobody looked" answer for a book
    the manage cycle does not inspect — see ``UNTAGGED_FLAG``. It is a parameter
    rather than a lookup miss precisely so the caller has to SAY which case it
    is: a missing state inside a tagged book really does mean healthy.
    """
    if not rescue_tagged:
        return UNTAGGED_FLAG
    return POSITION_FLAGS.get(rescue_state, _DEFAULT_FLAG)


# ── arrival + change detection ───────────────────────────────────────────────
# Pure, so the whole "what is new on this screen" question is testable without
# a browser. The page-state sets these read against are seeded SILENTLY on the
# first paint — without that, navigating to the Desk announces the entire day's
# alert list and lights every row, which is exactly the trap main.py's watcher
# already documents for the scanner chime.
def new_ids(rows, seen, key="id"):
    """Ids in ``rows`` not already in ``seen``, IN ROW ORDER.

    Row order is load-bearing: the flow feed is newest-first and the newest
    arrival is the one that gets spoken, so the caller reads ``[0]``. A row with
    no id is skipped rather than given a positional key — a synthetic key would
    change identity on the next repaint and re-announce forever.
    """
    out = []
    for r in rows or ():
        rid = r.get(key) if isinstance(r, dict) else None
        if rid is None or rid in seen or rid in out:
            continue
        out.append(rid)
    return out


def id_set(rows, key="id"):
    """The ids present in ``rows`` — what ``seen`` is replaced with each paint.

    REPLACED, not unioned: the flow list is day-scoped and rolling, and a
    position that closes and reopens really is a new position. An ever-growing
    set would also never shrink on a page left open for days.
    """
    ids = (r.get(key) for r in rows or () if isinstance(r, dict))
    return {rid for rid in ids if rid is not None}


def flag_map(rows):
    """``{position_id: flag}`` — the previous-state map ``flag_changes`` reads.

    The id field is hardcoded where ``new_ids``/``id_set`` take a ``key=``, and
    that is the intended asymmetry: those two run over the flow feed as well,
    which keys on ``id``, while a FLAG is a positions-only idea and there is no
    second caller for this pair to generalise for.
    """
    return {r["position_id"]: r.get("flag") for r in rows or ()
            if isinstance(r, dict) and r.get("position_id") is not None}


def flag_changes(rows, prev):
    """Position ids whose ``flag`` moved since ``prev``.

    A FIRST SIGHTING is deliberately not a change — it is an arrival, and
    ``new_ids`` already glows it. Counting it in both places would give a new
    row two overlapping glows.
    """
    out = []
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        rid = r.get("position_id")
        if rid is None or rid not in prev:
            continue
        if r.get("flag") != prev[rid]:
            out.append(rid)
    return out


def _is_open(p):
    return (p.get("status") or "OPEN").upper() not in _CLOSED_STATUSES


def strikes_text(p):
    """'600.0/595.0' — the spread's two strikes, or an em-dash.

    Falls back to the CALL pair when there is no put side, which is what an iron
    condor's payload looks like from the put-first fields. Shared by all three
    books so a strike pair can never render one way on one chip and another way
    on the next."""
    for short_key, long_key in (("short_strike", "long_strike"),
                                ("call_short", "call_long")):
        sk, lk = p.get(short_key), p.get(long_key)
        if sk is not None:
            return f"{sk}/{lk}"
    return "—"


def position_rows(paper_view, driver_view, captured_view=None):
    """Open rows from ALL THREE books, each tagged with its source, most
    actionable first.

    Reads the *account* views for the two paper books, not the paper ledger: the
    ledger carries no live mark, so an unrealized P&L taken from it would be
    entry-time arithmetic wearing a live label. Captured signals are the third
    book — advisory rather than held, which is why they carry neither a size nor
    a rescue verdict (see ``BOOKS``).

    Returns the WHOLE merged book. The panel caps what it draws at
    ``POSITION_ROWS_N``, but the cap must never reach the summary — unrealized
    P&L and the at-risk count are book-level facts, and computing either off a
    visible slice would understate both.
    """
    out = []
    for view, book in zip((paper_view, driver_view, captured_view), BOOKS):
        entries = ((view or {}).get(book["list_key"])
                   if isinstance(view, dict) else None)
        if not isinstance(entries, list):
            continue
        for p in entries:
            if not isinstance(p, dict) or not _is_open(p):
                continue
            out.append({
                "source": book["source"],
                "position_id": p.get(book["id_key"]),
                "symbol": p.get("symbol", ""),
                "strategy": p.get("strategy", ""),
                "short_strike": _finite(p.get("short_strike")),
                "long_strike": _finite(p.get("long_strike")),
                "strikes": strikes_text(p),
                "width": _finite(p.get("width")),
                "expiration": p.get("expiration", ""),
                # The paper page's own helper — one calendar for the whole app.
                # Deliberately NOT captured's stored ``dte_at_entry``, which is
                # the countdown as it stood on the day the signal was found and
                # would print a stale, too-large number for every row here.
                "dte": _paper._dte_from_expiration(p.get("expiration")),
                # An unheld book prints an em-dash, never a 1 — see ``BOOKS``.
                "quantity": (_finite(p.get("quantity")) if book["held"]
                             else None),
                "held": book["held"],
                "entry_credit": _finite(p.get("entry_credit")),
                "current_value": _finite(p.get("current_value")),
                "unrealized_pnl": _finite(p.get("unrealized_pnl")),
                "rescue_state": p.get("rescue_state") if book["rescue"] else None,
                "heat": _finite(p.get("heat")) if book["rescue"] else None,
                "flag": position_flag(p.get("rescue_state"),
                                      rescue_tagged=book["rescue"]),
            })
    # Stable, so rows level on all three keys keep the book order they merged in.
    out.sort(key=lambda r: (_URGENCY_RANK.get(r["rescue_state"], _CALM),
                            _HELD_FIRST if r["held"] else _ADVISORY_LAST,
                            _NO_DTE if r["dte"] is None else r["dte"]))
    return out


def positions_summary(rows):
    """``{"open": n, "unrealized": float, "at_risk": n}`` over ``position_rows``.

    ⚠ Hand this the FULL merged book, never the slice the panel draws. Both
    numbers it produces are book-level facts, and ``at_risk`` in particular is
    the one figure on this panel somebody acts on — computing it off eight
    visible rows out of thirty-six would report zero trades in trouble while
    trades were in trouble.

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


# ── the Bull / Bear sector strip ─────────────────────────────────────────────
# ALL ELEVEN sectors, not the extremes. The reading this strip gives is the
# DISTRIBUTION — how much of the market is rising, how much is leading, and how
# many sit in the trap quadrant (falling but still beating SPY, the row a
# relative-strength screen calls a buy) — and that reading is exactly what a
# trim destroys: "the two strongest and the two weakest" answers a different
# question while looking like it answered this one, and picking WHICH to keep is
# a ranking judgement the strip has no business making.
#
# The layout does not argue against it either, and that was arithmetic rather
# than optimism: eleven chips on their ``min-w-[124px]`` floor plus ten 8px gaps
# is 1444px, and the strip is a ``flex-wrap`` row — so below that it simply
# wraps to two rows and keeps every sector. It is the one region on this page
# that reflows rather than clips, which is why it needs no width budget of its
# own (contrast the panel grid: see ``PANEL_BUDGET_PX``).
BULLBEAR_ROUTE = "/sentiment/bullbear"

# Its own message, NOT ``WAITING_OPTIONS``: these scores come from a NIGHTLY
# cascade rather than a 30 s poll, so "not published yet" means something a
# reader can act on (wait for tonight) that the generic line does not. Wording
# follows ``sentiment_bullbear.WAITING``.
WAITING_BULLBEAR = ("Waiting for the sentiment service — the Bull / Bear map is "
                    "built by the nightly cascade at 16:20 CT.")


def _bullbear_rows(bullbear_view):
    """The payload's sector rows, ordered strongest-first and null-free.

    Shape-guarded at BOTH levels because ``render()`` seeds every view at build
    time: a half-written key, an older writer or a service caught mid-restart
    can put a non-dict in either position, and ``or {}`` would pass a truthy
    malformed payload straight through to the first ``.get``.

    Ordering is ``bullbear.by_strength`` — the map's own — so the strip and the
    page it links to can never list the same sectors in different orders.
    """
    view = bullbear_view if isinstance(bullbear_view, dict) else {}
    levels = view.get("levels")
    levels = levels if isinstance(levels, dict) else {}
    return _bb.by_strength(levels.get("sector"))


def bullbear_chips(bullbear_view):
    """One chip per scored sector — everything the strip draws, as plain dicts.

    Every DECISION here belongs to ``pages/bullbear.py``: the ordering, the
    quadrant, the breadth width and its thin threshold, and the day-move
    formatting. This function picks the sector level out of the payload and
    names the fields; it computes nothing.

    ``payload["regime"]`` is deliberately never read. ``/sentiment/sectors`` and
    ``/sentiment/rotation`` already print OPPOSITE risk-on/risk-off headlines
    off quantities that are not commensurable (CLAUDE.md, 2026-08-17); the map
    answers that by counting rows and stopping, and a strip pointing at it under
    a verdict word would reopen the same hole one screen earlier.

    ``day_text`` separates "the proxy did not return this symbol" (a dash) from
    "the proxy returned it" — NOT from "unchanged". ``compute.merge_live``
    (services/sentiment_svc/compute.py) leaves ``day_pct`` None only for an
    omitted symbol, and ``SchwabProxyClient._extract_change_pct``
    (schwab-proxy/proxy_client.py) falls through to a literal ``0.0`` when every
    percent field is missing or zero. So "0.00%" is not proof of a flat tape.
    """
    out = []
    for row in _bullbear_rows(bullbear_view):
        share = _bb.row_participation(row)
        out.append({
            "label": str(row.get("label") or row.get("symbol") or ""),
            "symbol": str(row.get("symbol") or ""),
            "quadrant": _bb.quadrant(*_bb.row_axes(row)),
            "day_text": _bb.signed_pct(row.get("day_pct")),
            # None ("no reading at all") and 0 ("nothing confirms") are two
            # different drawings, so this stays the raw None rather than being
            # folded to 0 — see ``_bullbear_breadth``.
            "breadth": _bb.breadth_width(share),
            "thin": _bb.breadth_is_thin(share),
        })
    return out


def bullbear_headline(bullbear_view):
    """The map's own count sentence, over the same rows the chips draw.

    ``sentiment_bullbear.headline_line`` handles the pluralisation and returns
    "" on an empty payload — where "0 of 0 sectors rising and leading" would
    state a maximally bearish tape that nobody measured.
    """
    return _bbmap.headline_line(_bullbear_rows(bullbear_view))


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


# ── the Sentiment / Trend hero pills ─────────────────────────────────────────
# The Desk's two score cards are the Market Regime Console's own cards, so their
# hero pills must read the SAME words off the SAME payload. ``sentiment_arcs`` /
# ``trend_arcs`` already carry the three meter values; these two carry the word
# beside the hero number, which is the only other thing the compact card shows.
def sentiment_pill_text(live, snaps):
    """'CAUTIOUS 4.45' — the composite's bias word and its total score.

    ``live`` wins over the newest backfill snapshot, exactly as ``/sentiment``'s
    own ``_apply`` picks its headline, so the pill can never name a different
    session than the Day meter beside it.

    ONE deviation from that page, deliberate: it formats the total through a
    ``_safe_float`` that defaults to **0.0**, so a composite published without a
    score reads "CAUTIOUS 0.00" — a maximally bearish number nobody measured.
    Here a missing total drops the number and keeps the word. An absent bias
    prints nothing at all rather than a filler.
    """
    if not isinstance(snaps, list):
        snaps = []
    latest = live or (snaps[-1] if snaps else None)
    comp = latest.get("composite") if isinstance(latest, dict) else None
    comp = comp if isinstance(comp, dict) else {}
    bias = str(comp.get("bias") or "").strip().upper()
    if not bias:
        return ""
    total = _finite(comp.get("total_score"))
    return bias if total is None else f"{bias} {total:.2f}"


def trend_pill_text(derived):
    """'RESILIENT' — the Day horizon's short trend-state word.

    Straight off ``pages.sentiment._TREND_SHORT``, which is the map the console's
    own Trend pill uses. An unknown or absent state prints nothing: the five
    words are readings, and there is no sixth one meaning "no reading".
    """
    d = derived if isinstance(derived, dict) else {}
    trend = d.get("trend") if isinstance(d.get("trend"), dict) else {}
    return str(_TREND_WORDS.get(trend.get("state")) or "").upper()


def _arc_value(arcs, i):
    """The i-th arc's 0-100 value, or None.

    ``sentiment_arcs``/``trend_arcs`` always return three entries, so this is a
    guard rather than a branch anyone expects to take — but the hero delta is
    decoration on a card whose numbers are elsewhere, and it must degrade to no
    delta rather than take the strip down with an IndexError."""
    try:
        return arcs[i].get("value")
    except (IndexError, KeyError, AttributeError, TypeError):
        return None


# ── the session countdown ────────────────────────────────────────────────────
# What the clock counts to, and what it calls itself. Two states only: the
# session is open, or it is not — there is no third reading a trader acts on.
COUNTDOWN_LABELS = {"to_close": "TO CLOSE", "to_open": "TO OPEN"}


def countdown_facts(now):
    """``{"label", "text", "state"}`` — time to the close, or to the next open.

    A wall clock says what a trader already knows. What is worth a tile is how
    much session is left: inside regular hours this counts down to the cash
    close, outside them it counts down to the next open.

    **Every session bound comes from ``shared.market_calendar``** — this
    function contains no time literal and no holiday list, which is the whole
    point of that module. ``mins_to_close`` is what decides which branch runs
    (it returns None outside the regular session, so the two states cannot
    disagree with ``is_regular_hours``), and ``next_regular_open`` rolls weekends
    and holidays forward through the shared NYSE calendar.

    ``now`` may be naive; it is then read as CT, the app's trading clock and the
    same rule ``market_calendar`` applies to its own inputs.
    """
    mins = _cal.mins_to_close(now)
    if mins is not None:
        return {"label": COUNTDOWN_LABELS["to_close"],
                "text": _hms(mins * 60.0), "state": "to_close"}
    ct = now.astimezone(_CT) if now.tzinfo else now.replace(tzinfo=_CT)
    return {"label": COUNTDOWN_LABELS["to_open"],
            "text": _hms((_cal.next_regular_open(ct) - ct).total_seconds()),
            "state": "to_open"}


def _hms(seconds):
    """'3:07:12' — hours UNBOUNDED, because a Friday-evening countdown to
    Monday's open is 65 hours and wrapping it at 24 would be a lie. Never
    negative: the two callers cannot produce one, and a leading '-' on a
    countdown would read as a clock fault rather than as an edge case."""
    total = max(0, int(_finite(seconds) or 0.0))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


# ── display vocabulary ───────────────────────────────────────────────────────
# Everything below is the RENDER layer: formatters, the finite class maps the
# Tailwind-first standard requires, and ``render()``. The pure builders above
# never reach into it.
_DASH = "—"
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


def flow_kind_text(row):
    """'Unusual activity · Call' — the alert kind and the side it fired on.

    One cell, because the flow rows are one line each now and the side is a
    qualifier on the kind rather than a reading of its own. "Call"/"Put" names
    which side of the book moved and NOT who initiated: Schwab publishes no
    time-and-sales tape to this app. A row with no side prints the kind alone
    rather than a dangling separator."""
    r = row if isinstance(row, dict) else {}
    kind = str(r.get("kind") or "").strip()
    side = str(r.get("side") or "").strip()
    if not kind:
        return side or _DASH
    return f"{kind} · {side}" if side else kind


def strategy_label(s):
    """'put_credit_spread' → 'PUT CREDIT SPREAD'."""
    return str(s or "").replace("_", " ").upper() or _DASH


def dte_text(dte):
    """'0DTE' / '32d' / em-dash. Matches the flow page's own 0DTE shorthand."""
    if dte is None:
        return _DASH
    return "0DTE" if dte == 0 else f"{dte}d"


def expiry_text(row):
    """'2026-08-28 · 10d' — the expiration and how long is left of it.

    ONE cell, not two columns, because these are not two fields: the DTE is
    computed FROM the expiration (see ``position_rows``), so a column each would
    be the row spending width to say the same thing twice. The date is the
    contract's identity and the countdown is its urgency, which is why both are
    worth printing — and why the countdown, being the reading that decides
    anything, is also the panel's sort key.

    A row missing either half prints the half it has rather than a dangling
    separator, in the same shape ``flow_kind_text`` uses."""
    r = row if isinstance(row, dict) else {}
    date = str(r.get("expiration") or "").strip()
    left = dte_text(r.get("dte"))
    if not date:
        return left
    return f"{date} · {left}" if left != _DASH else date


def summary_line(summary, shown=None):
    """The Positions header: 'OPEN 4 · UNREALIZED $90.00 · AT RISK 2'.

    ``shown`` adds a 'SHOWING n' clause when the panel is drawing fewer rows
    than the book holds. A positions panel that silently truncates is dangerous
    — the reader has no way to tell "three open trades" from "three of
    thirty-six" — so the count of what is hidden rides on the one line that is
    always visible. It is omitted when nothing is hidden, because a permanent
    'SHOWING 3' on a three-row book would be noise that trained the eye to skip
    the clause on the day it mattered.
    """
    s = summary or {}
    total = s.get("open", 0)
    parts = [f"OPEN {total}"]
    if shown is not None and shown < total:
        parts.append(f"SHOWING {shown}")
    parts.append(f"UNREALIZED {fmt_money(s.get('unrealized'))}")
    parts.append(f"AT RISK {s.get('at_risk', 0)}")
    return " · ".join(parts)


# --- chips ------------------------------------------------------------------
# A chip is built ONLY from a palette hex, and every call site passes a constant,
# so the generated class strings come from a finite set — the styling standard's
# requirement. Nothing here ever interpolates a value-derived colour.
def _chip(hexv, fill=0.12, weight="", wrap=False, track=".16em",
          pad="px-[7px]"):
    """A chip in the reference design's shape: small caps on wide tracking, a 2px
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
    single unbreakable word ("RESCUE" in the narrow FLAG track) folds instead of running
    past the border it sits in.

    ``track``/``pad`` exist for the two NARROW tracks the reference gives us —
    BOOK and FLAG, the two tightest columns on the Positions panel. A chip is
    sized by its letter-spacing far more than by its font size, so tightening
    the tracking is what buys the fit; shrinking the text back down would make
    it unreadable instead, which is the problem this page was just fixing.
    """
    flow = "break-words leading-[1.3]" if wrap else "whitespace-nowrap"
    bg = "" if fill is None else f"bg-[{hexv}]/[{fill}]"
    return (f"{pad} py-[2px] rounded-[2px] text-[9px] tracking-[{track}] {flow} "
            f"border border-[{hexv}] {bg} text-[{hexv}] {weight}").strip()


# The book and flag chips live in the panel's two tightest tracks, so they take the
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
# The third book's chip, on the palette's dimmest readable step. Deliberately
# the QUIETEST of the three rather than a third loud hue: captured signals are
# advisory, they usually outnumber the two real books several times over, and a
# bright chip repeated eight times down the panel would make the advisory book
# look like the important one. It stays distinct from ``CHIP_MUTED``, which is
# what an UNKNOWN source falls back to — those two must not collide, or a
# malformed row would render as a captured signal.
CHIP_LABEL = _chip(_C["label"], **_TIGHT)

# The board's setup tag. The tight tracking and padding of ``_TIGHT`` — it sits
# in a 96px track — but explicitly NOT its ``wrap``: a board row is one line by
# construction now, and a chip that folded onto a second line would be the only
# thing on the panel breaking that. It fits without folding — "VOL CRUSH", the
# longest of the four tags, measures ~69px against the track's 77px floor.
CHIP_SETUP = _chip(_C["accent"], track=".1em", pad="px-[5px]")

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

# The book a row came from. Three books, three P&Ls — the chip is what makes a
# merged row actionable, and it is also what says whether the row is a HELD
# trade or an advisory signal.
_SOURCE_CHIP = {PAPER_SOURCE: CHIP_ACCENT, CLAUDE_SOURCE: CHIP_WARN,
                CAPTURED_SOURCE: CHIP_LABEL}

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
# poller would be ten Redis round-trips every two seconds for the life of the
# session; ``read_versions`` reads the ten tiny ``{key}:ver`` counters in a
# single pipelined round-trip and only the views that MOVED get deserialized.
# ⚠ A new view belongs HERE, joining the existing batch — never in a poller or
# a timer of its own.
VIEWS = ("options:header", "sentiment:regime", "sentiment:composite",
         "sentiment:history", "options:gex_status", "options:matrix",
         "options:flow_alerts", "options:paper_account",
         "options:driver_paper_account", "options:captured",
         "sentiment:bullbear")

# Which views each region depends on. A repaint touches only the regions whose
# inputs actually changed — without this, one 2 s header bump would rebuild all
# four panels (and re-emit both ring SVGs) every tick.
_REGION_VIEWS = {
    "strip": ("options:header", "sentiment:regime", "sentiment:composite",
              "sentiment:history", "options:gex_status"),
    # The dealer panel reads gex_status too: freshness is what GATES its walls.
    "dealer": ("options:matrix", "options:gex_status"),
    "board": ("options:matrix",),
    # ONE view, and the strip must not repaint on any other: its scores move
    # once a NIGHT and its day-moves at the service's own quote cadence, so a
    # 2 s header bump rebuilding eleven chips is pure churn.
    "bullbear": ("sentiment:bullbear",),
    "flow": ("options:flow_alerts",),
    "positions": ("options:paper_account", "options:driver_paper_account",
                  "options:captured"),
}

POLL_SEC = 2.0
CLOCK_SEC = 1.0

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
# Column labels. The reference's own #3f5265 was drawn for 8px labels sitting
# almost subliminally under the panel rule; at the 12px this page now sets (see
# ``_HEAD``) that hex reads as a rendering fault rather than as restraint. This
# is the reference's PANEL-TITLE colour, one step up the same ladder — bright
# enough to read, still clearly below the data it labels.
REF_HEAD_TXT = "text-[#5b7f8c]"
CALL_HEX = "#2dd4a7"                   # the call wall, and its marker on the map
PUT_HEX = "#fb5f7c"                    # the put wall, and its marker
FLIP_HEX = "#f5b841"                   # the gamma flip tick
SPOT_HEX = "#22d3ee"                   # the spot dot

# ── the 10-second neon glow ──────────────────────────────────────────────────
# ⚠ THE NON-OBVIOUS PART. ``_paint_positions`` calls ``pos_body.clear()`` and
# rebuilds every row, and it runs whenever the paper account re-prices — which
# is constant during market hours. A REBUILT ELEMENT RESTARTS ITS CSS ANIMATION
# FROM ZERO, so the naive implementation glows forever: every repaint resets the
# decay and the row never goes dark.
#
# The fix is a whole-second NEGATIVE ``animation-delay``, which starts an
# animation partway through. The glow's START TIME lives in page state keyed by
# row id; the row wears ``desk-neon-N`` where N is how many seconds have already
# elapsed, so a rebuilt element RESUMES rather than restarts.
#
# Ten fixed classes rather than a computed ``[animation-delay:-3.2s]``: the
# styling standard's finite-set rule. The cost is one second of granularity on
# a ten-second decay, which is invisible.
GLOW_SEC = 10.0
GLOW_STEPS = 10

# The two things worth glowing about, and nothing else. NEW is the cyan the
# structure map already uses for spot; FLAG is the amber it uses for the flip —
# both already mean "look here" on this page.
GLOW_NEW = "new"
GLOW_FLAG = "flag"
GLOW_KINDS = (GLOW_NEW, GLOW_FLAG)


def glow_step(started, now, span=GLOW_SEC, steps=GLOW_STEPS):
    """Which ``desk-neon-N`` class a glow started at ``started`` wears at ``now``.

    ``None`` once it has expired, or if it has not begun. Both are the same
    answer to the caller — do not glow — and collapsing them here keeps the
    check at the call site to one branch.
    """
    if started is None:
        return None
    try:
        elapsed = float(now) - float(started)
    except (TypeError, ValueError):
        return None
    # ⚠ INVERTED ON PURPOSE — do not "clean this up" to
    # ``if elapsed < 0 or elapsed >= span``. Every comparison against a NaN is
    # False, so that spelling is False on BOTH halves and waves a NaN through to
    # ``int(nan)``, which raises ValueError — on the paint path, inside
    # ``prune_glows``, which runs this over every entry in the map: one wedged
    # timestamp takes down a whole panel repaint rather than one row. Written
    # this way round the NaN makes the ``not`` True and lands on the safe answer.
    # (Same family as the ``min(hi, nan) == hi`` trap CLAUDE.md documents, where
    # a missing reading rendered as a maximum one. NaN does not degrade; it has
    # to be caught by name.)
    if not (0 <= elapsed < span):
        return None
    # The ``min`` is DEFENSIVE, not observed. For it to fire, the float division
    # at the very top of the range would have to round up to exactly ``steps``,
    # and it does not — checked over the 500 consecutive doubles below each of
    # eight spans plus 2M random samples, never once. It stays because it is
    # free, because a loosened range check above would make it load-bearing, and
    # because ``desk-neon-10`` has no rule behind it, so the failure it guards
    # is silent (the animation restarts instead of finishing). The floor is
    # applied LAST so a nonsense ``steps`` cannot yield ``desk-neon--1``.
    return max(0, min(steps - 1, int(elapsed / span * steps)))


def glow_classes(entry, now):
    """The class string for a glowing row, or ``''``.

    ``entry`` is the ``(kind, started)`` tuple held in page state, or ``None``.
    """
    if not entry:
        return ""
    kind, started = entry
    # A kind outside the finite set is a WIRING bug, and an unguarded one is
    # invisible: ``desk-neon-<typo>`` leaves ``--neon`` unset, and a
    # ``box-shadow`` naming an undefined custom property is invalid at
    # computed-value time, so BOTH shadow declarations drop. The row gets the
    # background flash and no glow at all. Paint nothing instead.
    if kind not in GLOW_KINDS:
        return ""
    step = glow_step(started, now)
    if step is None:
        return ""
    return f"desk-neon desk-neon-{kind} desk-neon-{step}"


def prune_glows(glow, now):
    """Drop expired entries. Mutates and returns ``glow``.

    Called once per paint. Without it the map grows for the life of the tab —
    small, but it is also the only thing that makes the map's size mean
    something when debugging.
    """
    for rid in [k for k, v in glow.items() if glow_step(v[1], now) is None]:
        glow.pop(rid, None)
    return glow


# ── arrival detection ────────────────────────────────────────────────────────
# The whole decision layer of the feature — what lights up, what gets said, and
# how many arrivals one sentence stands for — as module-level functions taking
# their state explicitly, rather than as closures inside ``render()``. That is
# the only reason any of it is testable: a closure is reachable from a browser
# and from nowhere else, and every rule below is one a plain dict can exercise.
# ``render()`` keeps the parts that genuinely need a page: reading the cache and
# painting.
#
# ``now`` is passed IN rather than read here, so ONE ``time.monotonic()`` can
# serve both folds, ``prune_glows`` and every ``glow_classes`` call in a single
# paint. Two clocks in one paint can prune a glow and then be asked to draw it.


def arrival_state():
    """The arrival-tracking half of the page state.

    A builder rather than a dict literal in ``render`` so the tests start from
    the state the page actually starts from. ``first`` is the entire mechanism
    behind the silent, dark first paint — navigating to the Desk must not
    announce the day's alert backlog or light every row — and a hand-rolled copy
    in a test could not catch it being dropped.

    ``seen_flow``/``seen_pos`` are REPLACED each paint (see ``id_set``);
    ``glow`` maps a row id to its ``(kind, started_monotonic)``; ``speak`` is
    the queue ``_paint`` fills and the poll drains.
    """
    return {"seen_flow": set(), "seen_pos": set(), "pos_flags": {},
            "glow": {}, "first": True, "speak": []}


def _utterance(row, phrase, extra):
    """``phrase(row, extra)``, or None when the row carries no ticker.

    ``voice.flow_phrase({})`` is "Flow alert." — a squawk that tells the reader
    something happened and then refuses to say what, which is worse than
    silence. ``spell`` is the right test rather than a truthiness check on the
    raw field, because it is exactly what decides whether a ticker survives into
    the sentence: a symbol of pure punctuation spells to "".
    """
    if not isinstance(row, dict) or not _voice.spell(row.get("symbol")):
        return None
    return phrase(row, extra=extra)


def fold_flow_arrivals(state, rows, now):
    """Fold new flow alerts into the glow map; return the utterance, or None.

    Runs over the FULL alert list, not the nine rows the panel draws. A burst of
    ten would otherwise push arrivals off the bottom unseen, and they would
    announce themselves later, when the list shortened.

    ONE sentence per paint however many arrived: the newest is named and the
    rest are counted ("Plus 2 more"). Six sentences queued back to back is a
    minute of talking over a moving tape.
    """
    ids = new_ids(rows, state["seen_flow"])
    state["seen_flow"] = id_set(rows)
    if state["first"] or not ids:
        return None
    for rid in ids:
        state["glow"][rid] = (GLOW_NEW, now)
    newest = next((r for r in rows
                   if isinstance(r, dict) and r.get("id") == ids[0]), None)
    return _utterance(newest, _voice.flow_phrase, len(ids) - 1)


def fold_position_arrivals(state, rows, now):
    """The same, across the three books — plus the SILENT flag-change glow.

    A flag moving (OK -> AT RISK -> RESCUE) glows amber but never speaks: a
    position already in the book changing state is not something that was absent
    a moment ago, and the panel's own FLAG column already prints the new word.
    ``setdefault`` is what keeps an arrival cyan — ``flag_changes`` declines a
    first sighting, and this is the other half of that.
    """
    ids = new_ids(rows, state["seen_pos"], key="position_id")
    moved = flag_changes(rows, state["pos_flags"])
    state["seen_pos"] = id_set(rows, key="position_id")
    state["pos_flags"] = flag_map(rows)
    if state["first"]:
        return None
    for rid in ids:
        state["glow"][rid] = (GLOW_NEW, now)
    for rid in moved:
        state["glow"].setdefault(rid, (GLOW_FLAG, now))
    if not ids:
        return None
    newest = next((r for r in rows if isinstance(r, dict)
                   and r.get("position_id") == ids[0]), None)
    return _utterance(newest, _voice.position_phrase, len(ids) - 1)


# ── the speak gate ───────────────────────────────────────────────────────────
# ``app_settings``' own default, restated as the fallback for a value that will
# not parse. Not 1.0: a settings file somebody hand-edited into nonsense should
# land on the volume they were last offered, never on the loudest one.
DEFAULT_VOICE_VOLUME = 0.8


def should_speak(settings, now):
    """Whether a queued phrase may be spoken at ``now`` (a tz-aware datetime).

    Deliberately the same shape as ``alerts.should_alert`` and going through the
    same ``in_market_hours``: the Settings card tells the user the spoken alerts
    "use the existing market-hours gate above", so there is ONE gate and one
    setting, not a voice-only second copy that can drift out of step with the
    chime's.
    """
    s = settings or {}
    if not s.get("voice_enabled"):
        return False
    if s.get("alert_market_hours_only") and not _alerts.in_market_hours(now):
        return False
    return True


def speak_volume(settings):
    """``voice_volume`` clamped to 0..1, falling back rather than raising.

    The clamp is ``main.play_alert``'s, character for character. What differs is
    the PARSE in front of it, and it has to: this runs on the 2 s poll path
    inside a timer callback, and ``settings.json`` is hand-editable and never
    validated on read — a bare ``float("loud")`` there is a traceback the user
    never sees on a page that then has no audio. ``fmt.num`` is the house
    coercion and also refuses a NaN, which matters for the documented reason:
    ``max(0.0, min(1.0, nan))`` is **1.0**, so the trap would answer a missing
    reading with FULL volume.
    """
    v = _finite((settings or {}).get("voice_volume"))
    if v is None:
        return DEFAULT_VOICE_VOLUME
    return max(0.0, min(1.0, v))


# ── the browser side ─────────────────────────────────────────────────────────
# The Desk speaks through its OWN audio element, not ``main.py``'s shared
# ``alert-audio``. Sharing one element means whichever source assigns ``src``
# last wins, so a scanner chime — which fires from the app-wide 2 s watcher on
# every page, this one included — would cut an announcement off mid-sentence.
# Two elements cost nothing and cannot collide.
#
# A QUEUE rather than a bare play, because a burst can yield several clips and
# ``play()`` on an element already playing restarts it. ``el.onerror = next`` is
# the line that is easy to leave out and expensive to omit: with only
# ``onended``, one 404 — a clip evicted from the cache, a service restarted
# mid-flight — leaves ``busy`` true forever and the tab never speaks again.
#
# A blocked autoplay CLEARS the queue rather than holding it. Audio unlocks on
# the user's next click, which may be minutes later, and a backlog replayed then
# would announce a market that has moved on.
DESK_VOICE_JS = """
window.__deskVoice = window.__deskVoice || {q: [], busy: false};
window.__deskSpeak = function (urls, vol) {
  const v = window.__deskVoice;
  const el = document.getElementById('desk-voice');
  if (!el) return;
  urls.forEach(u => v.q.push(u));
  if (v.busy) return;
  const next = () => {
    if (!v.q.length) { v.busy = false; return; }
    v.busy = true;
    el.src = v.q.shift();
    el.volume = vol;
    el.play().catch(() => {
      v.q.length = 0; v.busy = false;
      emitEvent('desk_voice_blocked', {});
    });
  };
  el.onended = next;
  el.onerror = next;
  next();
};
"""

# The event the JS above emits when the browser refuses to play. NiceGUI's
# ``emitEvent`` is a plain global (nicegui.js is a classic ``<script defer>``),
# and ``ui.on`` subscribes on the CLIENT's layout — so this is per-tab, not
# process-wide, and a second Desk build cannot pile up handlers.
VOICE_BLOCKED_EVENT = "desk_voice_blocked"

# The phrase the unlock button speaks. It confirms audibly that the unlock
# worked, which a silent button could not.
VOICE_UNLOCK_PHRASE = "Spoken alerts on."

# Once per PROCESS, not once per page build: the clip cache is on disk and
# shared by every tab, so a second prewarm would re-walk a warm cache for
# nothing. Only a run that actually prewarms sets the latch — with the feature
# switched off there is nothing to warm, and turning it on later should still
# get the benefit.
_PREWARMED = {"done": False}


def prewarm_symbols(matrix_view):
    """The symbols to warm the flow-clip cache for, de-duplicated, in order.

    The matrix carries one row per WATCHLIST symbol, which is the same universe
    the flow alerts fire on — so this is the set of first-synthesis pauses the
    prewarm can actually remove. Anything that is not a usable symbol string is
    dropped rather than warmed: the payload is a cache read, and a blank would
    only synthesize the ticker-less sentence nothing is allowed to speak.
    """
    rows = (matrix_view or {}).get("rows") if isinstance(matrix_view, dict) else None
    out = []
    for row in rows or ():
        sym = row.get("symbol") if isinstance(row, dict) else None
        if isinstance(sym, str) and sym.strip() and sym not in out:
            out.append(sym)
    return out


def _prewarm_clips(seed):
    """Warm the flow-phrase clip cache in the background. Never raises.

    Wrapped whole, because this runs during the page BUILD: a cold cache, an
    unreadable data directory or a malformed matrix payload must cost the
    prewarm and not the Desk. ``voice.prewarm`` is itself fire-and-forget on a
    daemon thread and swallows its own synthesis failures, so the guard here is
    for the two lines in front of it.
    """
    if _PREWARMED["done"]:
        return
    try:
        settings = app_settings.load()
        if not settings.get("voice_enabled"):
            return          # nothing to warm, and the latch stays open so
                            # switching it on later still gets the benefit
        _PREWARMED["done"] = True
        _voice.prewarm(prewarm_symbols(seed.get("options:matrix")),
                       settings.get("voice_name"))
    except Exception:  # noqa: BLE001 — a cold cache must never break the build.
        logging.getLogger("webgui").warning(
            "Desk voice prewarm failed", exc_info=True)


# The ONE escape hatch this page is already allowed (it injects
# CONSOLE_KEYFRAMES_CSS beside this). A keyframes animation cannot be a utility
# class, and ``--neon`` is a plain custom property inside a real stylesheet —
# NOT a Tailwind arbitrary value, which is where the documented ``var(...)``
# JIT limitation bites.
#
# ⚠ THE BASE RULE USES LONGHANDS, DELIBERATELY. ``animation:`` is a SHORTHAND,
# so it resets every ``animation-*`` longhand it does not name —
# ``animation-delay`` back to 0s included. ``.desk-neon`` and ``.desk-neon-3``
# are both one class, so specificity cannot break that tie and SOURCE ORDER
# would decide it: with a shorthand the whole resume trick rests on the step
# rules happening to be concatenated last in the f-string below, and its failure
# mode is a glow that never expires — indistinguishable from the feature never
# having been built. Spelled as longhands, ``.desk-neon`` never declares a delay
# at all, so the step rule wins wherever it sits. Pinned by
# ``test_the_base_rule_declares_no_delay_for_a_step_rule_to_out_order``.
#
# No ``animation-fill-mode: forwards`` either, and that is not an oversight:
# animation declarations outrank normal author declarations (CSS Cascade
# §6.6.2), so an element still applying the 100% keyframe
# (``background-color: transparent``) would beat the row's ``hover:bg-…`` for as
# long as the class stayed on it — the rest of the session for an alert arriving
# at 15:59, on a row that is ``cursor-pointer`` and click-navigates. It buys
# nothing anyway: that 100% keyframe IS the row's author default, so the end
# state is identical without it.
_NEON_STEPS_CSS = "\n".join(
    # ``-0s`` is valid and behaves identically, but it reads as a generator
    # artifact in a stylesheet a human will open.
    f".desk-neon-{i} {{ animation-delay: {'0s' if i == 0 else f'-{i}s'}; }}"
    for i in range(GLOW_STEPS))

DESK_NEON_CSS = f"""
@keyframes deskNeon {{
  0%   {{ box-shadow: inset 0 0 0 1px var(--neon), 0 0 18px -2px var(--neon);
          background-color: rgba(255,255,255,.055); }}
  65%  {{ box-shadow: inset 0 0 0 1px var(--neon), 0 0 11px -5px var(--neon);
          background-color: rgba(255,255,255,.022); }}
  100% {{ box-shadow: inset 0 0 0 0 transparent, 0 0 0 0 transparent;
          background-color: transparent; }}
}}
.desk-neon {{ animation-name: deskNeon;
              animation-duration: {GLOW_SEC:g}s;
              animation-timing-function: linear;
              border-radius: 3px; }}
.desk-neon-{GLOW_NEW} {{ --neon: {SPOT_HEX}; }}
.desk-neon-{GLOW_FLAG} {{ --neon: {FLIP_HEX}; }}
{_NEON_STEPS_CSS}
"""

# The reference design's BODY face. It is a monospace, which is the whole point
# on a screen that is nine columns of numbers: JetBrains Mono's figures are
# fixed-width by construction, so a price column stays a column without
# `tabular-nums` having to rescue it, and a digit changing on the 2 s poll does
# not shuffle the cell beside it.
#
# Loaded and applied HERE, page-scoped, rather than through `config/theme.toml`:
# `[console].font_url` is shared with /sentiment and the `[typography]` block is
# app-wide, so moving either would repaint pages nobody asked to change. The
# panel TITLES keep `CONSOLE_DISPLAY` (Rajdhani) — a display face over a data
# face is the reference's own pairing, and a child's own font class beats the
# wrapper's inherited one.
DESK_FONT = "font-['JetBrains_Mono',ui-monospace,monospace]"
DESK_FONT_HEAD_HTML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=JetBrains+Mono:wght@400;500;600&display=swap">'
)

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
# The widest panel (Positions) is what sets the page's MINIMUM SUPPORTED WIDTH
# — see the panel wrapper in ``render``, which does that arithmetic from the
# minmax floors below, and ``PANEL_BUDGET_PX`` for the budget they spend.
# Every qualifier rides as a SECOND LINE inside its owner's cell rather than
# taking a column of its own: the day % under spot, the flip distance under the
# flip, the rationale under the symbol, the expiry under the strikes. That costs
# a line of height instead of a whole column of width, and it keeps each row to
# ONE grid line — which is what puts the structure map beside its symbol instead
# of on a tier of its own. `overflow-x-auto` was deliberately not used as the
# fallback: a dashboard you scroll sideways to read defeats the page's purpose.
_GAP = "gap-x-[8px] gap-y-0"

# ── the width budget every track floor below is spent against ────────────────
# The page is read at a 1920px window, and the four panels are a FIXED 2x2 (see
# the note in ``render``), so each panel gets exactly half of what is left after
# the app chrome and the grid gutter. That is the number every floor here has to
# add up under — not a target, an upper bound: a CSS grid will not shrink a
# track below its ``minmax()`` floor, so a panel whose floors oversubscribe this
# does not reflow, it CLIPS.
#
# ``DESK_CHROME_PX`` is the MEASURED non-panel width — the icon rail's laid-out
# 68px plus the page's own ``p-4`` and the drawer/page padding around it —
# confirmed live at 1920 (a 1905px document less a 1741px panel grid). It is
# written down rather than computed because there is nothing to compute it from.
#
# ⚠ The SCROLLBAR is subtracted, and that is not fussiness: this page is taller
# than any window it is read in, so the classic scrollbar is ALWAYS there, and a
# budget taken off ``innerWidth`` reads 868px where the panel really gets 860px.
# Eight pixels is a third of the slack the tightest panel has.
DESK_WINDOW_PX = 1920
DESK_CHROME_PX = 164
DESK_SCROLLBAR_PX = 15            # the classic Windows scrollbar, always shown
PANEL_GUTTER_PX = 20              # the 2x2's ``gap-5``, between the two columns
PANEL_BUDGET_PX = (DESK_WINDOW_PX - DESK_SCROLLBAR_PX - DESK_CHROME_PX
                   - PANEL_GUTTER_PX) // 2

# What a panel spends before its first track: the card's 1px border both sides,
# the panel's ``px-4`` both sides (``_panel``) and the row's own ``px-1`` both
# sides (``_ROW``/``_grid_head``). The gaps are ``len(tracks) - 1`` x 8px on top.
PANEL_PAD_PX = 2 + 32 + 8
COL_GAP_PX = 8

# Column widths are the reference design's, but every flexible track is
# ``minmax(<reference px>, <weight>fr)`` rather than a bare pixel width with ONE
# 1fr track soaking up the remainder.
#
# Why: with a single 1fr track the structure map rendered **502px wide** — it
# needs ~200 — which left a void between the map and the wall columns and made
# the markers read as scattered rather than as a scale. The map was CORRECT (put
# 0%, spot 26%, flip 46%, call 100%); it was just stretched across half the
# panel. Weighting every track instead spreads the slack proportionally, so the
# row fills at any width and no single cell balloons.
#
# **The floors are the reference's own pixels, at the reference's own scale.**
# They were briefly carried at ~1.3x it, to stand under type scaled ~1.35x for a
# 2381px screen (see the size ladder below) — and that pairing is right, a floor
# is only meaningful relative to the text standing in it. But the page is read
# at 1920px, which is the width the reference was authored for in the first
# place, and at 1920 the scaled-up floors oversubscribed the budget above by
# 174px on Positions, 100px on the Board and 44px on Dealer Positioning: three
# of four panels clipped their rows. So the whole scaling was unwound — type and
# floors together, at 0.8x, which lands both back within a rounding step of the
# reference. The `fr` WEIGHTS are untouched throughout: the proportions were
# always right, only the absolute floor moved.
#
# ⚠ Three floors do NOT follow that 0.8x, and none of the three is bound by the
# value under it. "GAMMA FLIP" (82px) and the BOARD's SCORE track (52px) are
# bound by their column LABEL: a label on .2em tracking does not shrink as fast
# as the data under it, and a label that clips turns a column of numbers into an
# unlabelled column of numbers. "NET GEX / REGIME" (144px) is bound by neither —
# it is the dealer REGIME CHIP, which measures 139px ("SHORT GAMMA · RUNS" on
# .16em tracking) against a label needing 128px and a value needing 47px. That
# one was found by MEASUREMENT, not arithmetic: at the 130px this track was
# first given, the fr weights handed it 138.5px and the chip wrapped to two
# lines by half a pixel — leaving two of the four dealer rows 13px taller than
# the other two, which reads as a rendering fault rather than as a long word.
# Chip ``wrap=True`` stays the genuine narrow-case backstop; it should not be
# what happens at the width the page is read at.
DEALER_GRID = ("grid grid-cols-[78px_minmax(77px,1fr)_minmax(82px,1fr)_"
               "minmax(136px,2fr)_minmax(75px,1fr)_minmax(75px,1fr)_"
               f"minmax(144px,1.5fr)] {_GAP} w-full")
# Eight tracks now, not six: the board rows went flat (one line per symbol), so
# WHY and SETUP take columns of their own instead of riding under the symbol and
# the signal. The IV state rides INSIDE the ATM IV cell, on the same line — it
# is two words wide and belongs beside the number it qualifies, not in a column
# that would need its own label.
#
# WHY carries 5fr, far more than any other track, and that weight is what stops
# it ellipsing. The longest rationale this page can build is three clauses —
# "pinned at wall · below flip · strong downtrend", 46 characters — which is
# 276px of JetBrains Mono at 10px (0.6em advance). Its floor is 200px, and the
# weights hand it well past that at the width the page is read at, so the worst
# case never reaches the `truncate`.
#
# ATM IV's 116px is likewise a WORST CASE, not the common one: it holds the
# value AND its state word on one line, and "100.0% collapsing" measures ~113px
# at the current ladder. The obvious ~106px (which is what the common "57.6%
# collapsing" wants) fits every symbol on the board today and truncates the
# first three-digit IV that appears — the kind of column that looks correct
# until the one row that matters arrives.
#
# SCORE's 52px is the exception noted above: it holds a two-digit number, but
# its LABEL is five caps on .2em tracking (~40px), and that is what binds it.
BOARD_GRID = ("grid grid-cols-[52px_minmax(77px,1fr)_minmax(200px,5fr)_"
              "minmax(116px,1.2fr)_minmax(66px,1fr)_minmax(37px,0.8fr)_"
              f"minmax(60px,1fr)_minmax(77px,1fr)] {_GAP} w-full")
# Four tracks now, not three: the flow rows went flat (one line per alert), so
# DETAIL takes a column of its own instead of riding under the symbol. The 3fr
# weight is still on DETAIL because it is the only cell here that can be long —
# the other three are a clock time, a ticker, and a two-word kind.
FLOW_GRID = ("grid grid-cols-[54px_minmax(70px,1fr)_minmax(192px,4fr)_"
             f"minmax(126px,2fr)] {_GAP} w-full")
# Ten tracks now, not nine: the position rows went flat like the board's and the
# flow's, so STRATEGY takes a column of its own instead of riding under the
# symbol, and the expiry rides WITH the DTE it is the source of rather than
# under the strikes (see ``expiry_text``). Net, the panel spends one more track
# and gets back a whole line of height per row — which is what pays for a third
# book on the same panel.
#
# Every floor is a worst-case string against the RENDERED font, not an assumed
# advance. That distinction earned its keep: a first pass sized three of these
# tracks from arithmetic, they came out 7-17px short, and the shortfall was
# invisible because none of the strings that overflow them was on screen the day
# it was measured. The widths below are quoted at the current ladder (14px
# symbol / 13px value / 11px secondary / 10px label / 9px chip); JetBrains Mono
# advances 0.6em, so a size change moves every one of them proportionally.
#
#   BOOK   64px  "CAPTURED" is the widest chip on the page. The chips
#                ``break-words`` as a backstop, and this floor is what stops
#                that backstop ever firing: a folded chip would be the one
#                two-line cell in a panel of one-line rows.
#   SYMBOL  53px "GOOGL" at 14px bold on .08em tracking — 47.6px.
#   STRAT   42px the head label "STRAT" binds, not the value: the services emit
#                two- and three-letter codes (PCS / CCS / IC).
#   EXPIRY 144px ⚠ "2026-08-28 · 0DTE" is 132.6px, NOT the shorter "· 10d"
#                shape that happened to be on screen. Every credit spread
#                reaches 0DTE on its expiration day, and "· 365d" is the same
#                width — so the common case is narrower than the case that
#                matters. Sizing on what was visible would have clipped this
#                column on exactly the morning it was being read hardest.
#   ENTRY,
#   MARK    53px "$12.45" at 13px, 46.8px.
#   STRIKES 126px "24000.0/23950.0" — an index strike pair, 117px. An equity
#                pair ("1200.0/1195.0") is not the worst case.
#   QTY     36px "125" at 13px is 23.4px; the label is the binding half.
#   UNREAL  94px "-$12,345.00" is 85.8px. The driver's own max_loss_total runs
#                to four figures, so five-figure P&L is a real row, not a
#                hypothetical one.
#   FLAG    60px "AT RISK" at chip metrics — the same no-fold rule as BOOK.
#
# The fr WEIGHTS are set so no track sits ON its floor at the width this page is
# read at, which is what stops a single character of drift becoming a clipped
# cell. This is the panel that spends the budget hardest: its ten floors plus
# nine gaps plus ``PANEL_PAD_PX`` are what decide the page's minimum supported
# window, so a track widened here is a track that has to come from somewhere.
POS_GRID = ("grid grid-cols-[64px_minmax(53px,0.8fr)_minmax(42px,0.6fr)_"
            "minmax(144px,2fr)_minmax(53px,0.8fr)_minmax(53px,0.8fr)_"
            "minmax(126px,1.8fr)_minmax(36px,0.5fr)_minmax(94px,1.3fr)_"
            f"minmax(60px,0.8fr)] {_GAP} w-full")

# The type ladder. Every size is 0.8x what this page briefly carried, which was
# the reference design's own multiplied by ~1.35 for a 2381px screen. The page
# is read at 1920px — the width the reference was authored for — so that scaling
# was unwound rather than trimmed: squeezing columns until the text ellipsed
# would have cost the reading, where returning to the scale the design was drawn
# at costs nothing. The ladder now runs 14 / 13 / 12 / 11 / 10 / 9.
#
# The RATIOS between the steps are the reference's and must stay that way — the
# three-tier hierarchy (value / qualifier / label) is what makes a nine-column
# row scannable, and flattening it by scaling one step and not another would
# cost more than the small type does. Scale the whole ladder or none of it.
# (The ladder's top step, a 13px eyebrow, went with the strip that used it — the
# strip tiles carry their own; see ``_STRIP_EYEBROW``.)
#
# Column labels still get a step MORE than the ladder gives (9px -> 10px) and a
# brighter hex (see ``REF_HEAD_TXT``): at 8px on .2em tracking they were the one
# thing on the page that could not be read at all, and a label nobody can read
# turns nine columns of numbers into nine unlabelled columns of numbers. The
# wide tracking stays — it is what separates a LABEL from the data under it now
# that the size difference between them is smaller — and it is also why three
# track floors are label-bound rather than value-bound (see ``DEALER_GRID``).
_HEAD = f"text-[10px] tracking-[.2em] {REF_HEAD_TXT}"
_ROW = f"items-center px-1 py-[11px] border-b {_ROW_RULE} cursor-pointer"
_VALUE = f"text-[13px] tabular-nums {CON_TXT}"
# The dealer panel's three price columns, one shade apart (see the ladder above).
_V_SPOT = f"text-[14px] tabular-nums {REF_TXT}"
_V_FLIP = f"text-[14px] tabular-nums {REF_TXT_SOFT}"
_SUB = "text-[10px] tabular-nums"          # a cell's second line
_PLACEHOLDER = f"text-[12px] {CON_TXT_MUTED} py-4"

# The service is cold vs the service is fine and has nothing to say. Rendering
# the same words for both would make a dead service indistinguishable from a
# quiet market — which is the whole reason this page must never print a zero it
# did not read.
WAITING_OPTIONS = "Waiting for the options service…"

# ── the Bull / Bear chip ─────────────────────────────────────────────────────
# The chip's frame. Its COLOUR is not here: ``bullbear.quadrant_class`` supplies
# text, background and border from its own five-literal palette, and the chip's
# inner labels inherit that text colour rather than setting one — so the strip
# and the map cannot colour the same quadrant differently.
_BB_CHIP = ("flex-1 min-w-[124px] border rounded-[2px] px-[8px] py-[6px] "
            "gap-[5px] cursor-pointer")
_BB_NAME = "text-[13px] font-semibold leading-none min-w-0 truncate"
_BB_QUAD = "text-[10px] leading-none tracking-[.1em] opacity-80 truncate"
# The day move is deliberately NOT coloured by its sign: ``signed_pct`` prints
# the sign already, and a second green/red inside a chip that is itself green or
# red would be two colour languages on one 124px box — on two different clocks
# (last night's cascade vs this morning's quote), which can legitimately
# disagree and would then read as a rendering fault.
_BB_DAY = f"text-[12px] leading-none tabular-nums shrink-0 {CON_TXT_MUTED}"

# The breadth groove. Two static classes: a move confirmed by too few of a
# sector's constituents takes the negative hue, which is the whole qualifier the
# bar draws — ``sentiment_bullbear._BREADTH_FILL`` makes the same call in that
# page's palette, and a strip that painted a thin move like a broad one would be
# the softer of the two screens on exactly the point they share.
_BB_FILL = {True: f"bg-[{_C['negative']}]", False: f"bg-[{_C['positive']}]"}
_BB_TRACK = f"h-[3px] w-full rounded-full overflow-hidden bg-[{_C['line']}]/[0.35]"

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


# ── the compact Sentiment / Trend cards ──────────────────────────────────────
# These are the Market Regime Console's own two cards at roughly 62% of its type
# scale, with the footer (model-confidence meter, verdict block, the two
# "→" links) dropped: the Desk is a glance surface and the whole card already
# click-throughs to /sentiment, where all three live at full size.
#
# They are built HERE rather than by calling ``console_cards.render_*`` because
# every size in that module is baked into its class strings — a 76px hero, an
# 18px meter track, a 20px card padding — and this strip has to fit inside ~90px
# in total. Parameterising them would put a size argument on a page nobody asked
# to change. What IS shared is everything that decides a NUMBER or a COLOUR:
# ``console.meter_row`` (band, fill gradient, marker tint, the no-read hatch),
# ``console_cards.hero_parts`` and ``console_cards.delta_parts``. So the two
# renderings can differ in size and in nothing else — which is the only kind of
# divergence this page can afford.
#
# The size ladder, console -> Desk: title 19->12, meta 10->8, hero 76->46,
# kicker 10->8, pill 12->9, meter label 10->8, meter track 18->11, meter value
# 17->11, ruler mark 9.5->8. Every one of those is ~60-65% EXCEPT the four
# already at 9.5-10px, which stop at 8: below that a wide-tracked cap label is
# not small, it is unreadable, and the point of keeping the console's anatomy is
# that the reader can still read it.
#
# ``leading-none`` is on every text class here, not decoration: a Tailwind
# arbitrary ``text-[Npx]`` sets the font SIZE only and leaves line-height to the
# app-wide `[typography]` rule, so without it each of these lines would carry
# ~50% of inherited leading and the strip would overshoot its height budget by
# more than the type scale saved.
_CARD_TITLE = (f"{CONSOLE_DISPLAY} text-[12px] leading-none font-bold "
               f"tracking-[.16em] {CON_TXT}")
_CARD_META = f"text-[8px] leading-none tracking-[.18em] {CON_TXT_DIM}"
_CARD_HERO = "text-[46px] font-semibold leading-[.85] tracking-[-.02em]"
_CARD_KICKER = f"text-[8px] leading-none tracking-[.24em] {CON_TXT_MUTED}"
_CARD_DELTA = "text-[9px] leading-none whitespace-nowrap"
_METER_LABEL = (f"w-[34px] shrink-0 text-[8px] leading-none tracking-[.18em] "
                f"{CON_TXT_MUTED}")
_METER_VALUE = "w-[24px] shrink-0 text-right text-[11px] leading-none font-medium"
_RULER_MARK = f"text-[8px] leading-none {CON_TXT_FAINT}"

# The strip's own vocabulary. Its eyebrows sit at 10px rather than the 13px the
# panels' type ladder gives: a strip tile is one reading, so its label has no
# column of numbers to compete with and does not need the panels' weight.
_STRIP_EYEBROW = f"text-[10px] leading-none tracking-[.22em] {CON_TXT_DIM}"
_STRIP_VALUE = f"text-[28px] leading-none tabular-nums {CON_TXT}"
# Each strip tile is its own console card. The strip used to be ONE card holding
# everything, which meant a card inside a card once the score cards arrived —
# and, more practically, its own padding on top of theirs, which is height this
# strip does not have.
#
# ⚠ NO `h-full` here, and that was measured rather than reasoned: `height:100%`
# against the row's INDEFINITE height is not `auto`, and `align-items: stretch`
# only stretches an item whose cross size IS auto — so the one class that looks
# like it squares the tiles off is exactly what stops the strip doing it (81 /
# 92 / 92 / 84 / 81 with it, 92 across without). The tiles stretch because the
# ROW says `items-stretch`; each score card then fills its own holder with
# `flex-1`, which works because a holder in a COLUMN flex container has a
# definite main axis. `mt-auto` on a tile's last row depends on this too.
_TILE = f"{CONSOLE_CARD} rounded-none px-[10px] py-[9px] gap-[6px]"


def _compact_pill(hexv):
    """The hero pill, in ``console_cards.pill_classes``' shape at Desk scale.

    Same border/fill/text relationship, smaller box: an outlined word, not a
    filled badge. The alpha is written as Tailwind's ``/[0.16]`` opacity form
    rather than the console's 8-digit hex — the two render identically and this
    one needs no extra import."""
    return (f"border border-[{hexv}]/[0.45] bg-[{hexv}]/[0.16] "
            f"px-[6px] py-[1px] text-[9px] leading-none tracking-[.14em] "
            f"whitespace-nowrap text-[{hexv}]")


def _mount_meter(row):
    """One Day/Week/Month meter: label · track · value.

    Every decision in ``row`` — the band, the fill gradient, the marker tint,
    whether it is a NO READ hatch — was already made by ``console.meter_row``.
    This only sizes it."""
    with ui.row().classes("items-center gap-2 w-full flex-nowrap"):
        ui.label(row["label"]).classes(_METER_LABEL)
        with ui.element("div").classes(f"flex-1 h-[11px] {_K.track_classes()}"):
            if row["no_read"]:
                # A hatch, never an empty track: "the instrument is absent" and
                # "the value is zero" must not look the same on a 0-100 scale.
                ui.element("div").classes(f"absolute inset-0 {_K.NO_READ_HATCH}")
            else:
                ui.element("div").classes(
                    f"absolute left-0 top-0 bottom-0 "
                    f"{_K.width_class(row['pct'])} {row['fill']} {row['glow']}")
                ui.element("div").classes(
                    f"absolute w-[2px] -top-[2px] -bottom-[2px] "
                    f"{_K.left_class(row['pct'])} {row['marker']}")
        ui.label(row["text"]).classes(f"{_METER_VALUE} text-[{row['hex']}]")


def _mount_ruler():
    """The 0/25/50/75/100 rule under the meter stack. The two spacers match the
    meter row's label and value columns, so the ticks line up with the TRACK
    rather than with the row."""
    with ui.row().classes("items-center gap-2 w-full flex-nowrap"):
        ui.element("div").classes("w-[34px] shrink-0")
        with ui.row().classes(
                f"flex-1 justify-between border-t {CONSOLE_DIVIDER} pt-[3px]"):
            for mark in _K.RULER_MARKS:
                ui.label(str(mark)).classes(_RULER_MARK)
        ui.element("div").classes("w-[24px] shrink-0")


def _compact_card(title, arcs, pill_text, delta):
    """One compact score card: head · hero · three meters, in a console frame.

    The hero and the meters sit SIDE BY SIDE, where the console stacks them.
    That is the whole height saving: stacked, the two blocks are ~39px and ~54px
    and the card cannot fit the strip's budget; side by side the card is as tall
    as the taller of them. Nothing is dropped to buy it.
    """
    arcs = list(arcs or [])
    text, hexv = _CC.hero_parts(arcs[0].get("value") if arcs else None)
    with ui.column().classes(f"{_TILE} w-full flex-1 cursor-pointer") as card:
        with ui.row().classes("items-baseline justify-between w-full gap-3"):
            ui.label(title).classes(_CARD_TITLE)
            ui.label("SCALE 0—100").classes(_CARD_META)
        with ui.row().classes("items-end gap-4 w-full flex-nowrap"):
            with ui.row().classes("items-end gap-2 shrink-0"):
                ui.label(text).classes(f"{_CARD_HERO} text-[{hexv}]")
                with ui.column().classes("gap-[5px]"):
                    ui.label("DAY READ").classes(_CARD_KICKER)
                    with ui.row().classes("items-center gap-[6px]"):
                        if pill_text:
                            ui.label(pill_text).classes(_compact_pill(hexv))
                        if delta:
                            arrow, dtext, dhex = delta
                            ui.label(f"{arrow} {dtext}").classes(
                                f"{_CARD_DELTA} text-[{dhex}]")
            # `min-w-0` so the meter column can actually shrink: a grid/flex
            # item's automatic minimum is its content, and without it the ruler's
            # five marks would hold the card wider than its track.
            with ui.column().classes("flex-1 min-w-0 gap-[3px]"):
                for arc in arcs:
                    _mount_meter(_K.meter_row(arc.get("caption", ""),
                                              arc.get("value")))
                _mount_ruler()
    card.on("click", lambda _e: ui.navigate.to("/sentiment"))


def _panel(title, subtitle=""):
    """A console card with a titled head; returns the BODY container.

    The head is built ONCE and the body is what each painter clears, so a
    repaint can neither duplicate the title nor strand a handle to it."""
    with ui.column().classes(f"{CONSOLE_CARD} w-full px-4 pt-4 pb-4 gap-2"):
        with ui.row().classes(
                f"items-baseline justify-between w-full gap-4 border-b "
                f"{CONSOLE_RULE} pb-2"):
            ui.label(title).classes(
                f"{CONSOLE_DISPLAY} text-[19px] font-bold tracking-[.16em] "
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
    with ui.element("div").classes(
            f"relative h-[34px] w-full border-l border-r {_MAP_EDGE} px-[2px]"):
        # ── the track, and it is NOT decoration ──────────────────────────────
        # These two elements are the reference design's own, and they were lost
        # because the reference markup was transcribed from a TRUNCATED extract
        # that ended before them. Without them the map is four floating markers
        # over empty space: there is nothing connecting the put wall to the call
        # wall, so the row reads as scattered ticks rather than as one span with
        # price somewhere along it — which is the entire reading this cell
        # exists to give. They are the FIRST two children because painting order
        # is what puts them BEHIND the markers; move them and the band covers
        # the spot ring.
        #
        # 1. The hairline: the axis itself, running the full width so the span
        #    is continuous even where the band's rounded ends fall short.
        ui.element("div").classes(
            "absolute left-0 right-0 top-[21px] h-px bg-[#1a2836]")
        # 2. The band: rose at the put end fading to green at the call end, so
        #    the DIRECTION of the span is legible before any number is read —
        #    the spot ring's position on it then says which half price is in.
        #    Both stops carry the wall hexes at .16 alpha; written as `rgba()`
        #    with NO SPACES, because a Tailwind arbitrary value cannot contain
        #    one (underscore is the escape, commas are fine).
        ui.element("div").classes(
            "absolute top-[14px] h-[8px] rounded-[1px] left-[2px] right-[2px] "
            "bg-gradient-to-r from-[rgba(251,95,124,0.16)] "
            "to-[rgba(45,212,167,0.16)]")
        # The markers, painted over that track. Two hairline uprights for the
        # walls: they are the span's ends, the only part of it that is a fixed
        # fact.
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
                f"absolute bottom-[-4px] ml-[-7px] text-[9px] "
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
    # The reference's BODY face, loaded alongside the console's display face
    # rather than instead of it — the panel titles still want Rajdhani.
    ui.add_head_html(DESK_FONT_HEAD_HTML)
    # TWO ``ui.add_css`` calls, deliberately. The Tailwind-first standard allows
    # "a single documented block PER THEME", and these are two vocabularies, not
    # one split in half: ``CONSOLE_KEYFRAMES_CSS`` is the shared console
    # language (``/sentiment`` injects the same constant), while
    # ``DESK_NEON_CSS`` is this page's own arrival glow. Both qualify for the
    # hatch for the same reason — a keyframes animation cannot be a utility
    # class — and folding them into one call would only hide which is which.
    ui.add_css(CONSOLE_KEYFRAMES_CSS)
    ui.add_css(DESK_NEON_CSS)
    # The player and its queue. ``ui.html`` is main.py's own idiom for the
    # shared chime element, so the ``<audio preload>`` pair is already known to
    # survive NiceGUI's DOMPurify pass.
    ui.add_head_html(f"<script>{DESK_VOICE_JS}</script>")
    ui.html('<audio id="desk-voice" preload="auto"></audio>')

    # ``data`` holds the LAST payload seen for every view, because the poll hands
    # over only the ones that moved and most regions read more than one view.
    # ``glow_now`` is the ONE clock a paint runs on — set by ``_paint`` before it
    # calls a painter, so detection, pruning and drawing cannot disagree.
    state = {"versions": {}, "data": {}, "glow_now": 0.0, **arrival_state()}

    # ``DESK_FONT`` where the console pages carry ``CONSOLE_DISPLAY``: this page
    # is nine columns of numbers, so the body face is the monospace and the
    # display face is kept for the panel titles (``_panel``), which set it on
    # themselves and so beat this inherited one. Both must NOT sit on this one
    # element — two ``font-[…]`` utilities of equal specificity would leave the
    # winner up to stylesheet order.
    with ui.column().classes(
            f"{CONSOLE_PAGE} {DESK_FONT} w-full gap-4 p-4"):
        # ── the autoplay unlock ──────────────────────────────────────────────
        # Browsers refuse audio until the document has been interacted with, and
        # the refusal is COMPLETELY SILENT — ``play()`` rejects and nothing
        # appears in any log. With no affordance the feature simply looks broken
        # on every fresh tab, which is the worst of the three possible states
        # (working / off / apparently-broken).
        #
        # Hidden until the JS reports a block, so a tab that was never going to
        # need it never shows it. ``set_visibility(False)`` is ``display:none``,
        # which a flex ``gap`` skips entirely — the hidden row costs no height.
        # The click that dismisses it IS the gesture that unblocks audio.
        unlock_btn = ui.button("ENABLE SPOKEN ALERTS", icon="volume_up",
                               color=None).props("no-caps dense").classes(
            f"self-start text-[11px] tracking-[.14em] px-3 "
            f"bg-[{_C['line']}]/[0.18] {CON_ACCENT}")
        unlock_btn.set_visibility(False)

        # ── top strip ────────────────────────────────────────────────────────
        # Deliberately carries NO SPX/QQQ quote. The Dealer Positioning panel
        # below shows those same symbols with far more context, and the two
        # would come from different cache keys with independent version
        # counters — so a 2-second window could genuinely show two different
        # prices for one symbol on one screen. $VIX is excluded from the matrix
        # universe by design and so can never appear as a dealer row, which is
        # why it is the one quote that belongs up here.
        # FIVE tiles, and the ORDER is the argument the strip makes. The
        # countdown owns the left edge because it is the temporal anchor —
        # everything else on the page is a reading taken AT some point in the
        # session, and how much session is left is what says whether a reading
        # is still actionable. The two score cards take the flexible middle
        # because they are the only tiles whose content scales with width. VIX
        # and MARKET REGIME are grouped at the right end, regime outermost:
        # both answer "what is the tape doing", they qualify each other (a
        # regime word means something different at VIX 15 than at VIX 30), and
        # the regime is the coarsest read on the strip, so it terminates it.
        #
        # The strip is a plain row now, not a card of its own: five console
        # cards inside a sixth would be a frame around frames, and its padding
        # would be height this strip cannot spare.
        with ui.row().classes("w-full items-stretch gap-4 flex-wrap"):
            with ui.column().classes(f"{_TILE} w-[168px] shrink-0"):
                # The caption is part of the READING here, not a static label:
                # "TO CLOSE" and "TO OPEN" count to different things, so it is a
                # handle the clock tick rewrites rather than a fixed word.
                clock_cap = ui.label(COUNTDOWN_LABELS["to_open"]).classes(
                    _STRIP_EYEBROW)
                clock_lbl = ui.label(_DASH).classes(_STRIP_VALUE)
                # Feed freshness lives with the clock: both answer "is what I am
                # looking at current", and the pair reads as one status block.
                with ui.row().classes("items-center gap-2 mt-auto"):
                    fresh_dot = ui.element("div").classes(
                        "w-[8px] h-[8px] rounded-full shrink-0")
                    fresh_lbl = ui.label(_DASH).classes(
                        f"text-[11px] leading-none tracking-[.1em] "
                        f"{CON_TXT_MUTED}")
            # Both score cards are REBUILT on repaint rather than updated cell
            # by cell — the console's own choice, and for the same reason: the
            # card carries no interactive state, and threading a dozen element
            # handles through the painter buys nothing.
            sent_box = ui.column().classes("flex-1 min-w-[440px] gap-0")
            trend_box = ui.column().classes("flex-1 min-w-[440px] gap-0")
            with ui.column().classes(f"{_TILE} w-[140px] shrink-0"):
                ui.label("VIX").classes(_STRIP_EYEBROW)
                vix_lbl = ui.label(_DASH).classes(_STRIP_VALUE)
                # color=None drops Quasar's bg-primary so the mapped
                # bg-[...] class is what actually paints.
                vix_badge = ui.badge("", color=None).classes(
                    "self-start text-[10px] leading-none tracking-[.14em] "
                    "mt-auto")
            with ui.column().classes(f"{_TILE} w-[236px] shrink-0"):
                ui.label("MARKET REGIME").classes(_STRIP_EYEBROW)
                regime_lbl = ui.label(_DASH).classes(
                    f"text-[28px] leading-none font-semibold {CON_TXT}")
                regime_sub = ui.label("").classes(
                    f"text-[11px] leading-none mt-auto {CON_TXT_MUTED}")

        # ── the Bull / Bear sector strip ─────────────────────────────────────
        # It sits between the TOP STRIP and the panels, and that position is
        # the argument: the tiles above read the market as one thing (clock,
        # two composite scores, VIX, regime), the panels below are per-symbol,
        # and this is the one band saying which PARTS the composite above is
        # made of. Full width and one tile tall — eleven chips is a row, not a
        # panel.
        with ui.column().classes(f"{_TILE} w-full gap-[8px]"):
            with ui.row().classes("items-baseline w-full gap-4 flex-wrap"):
                ui.label("BULL / BEAR MAP").classes(_STRIP_EYEBROW)
                # The count line, not a verdict — and it is the MAP's sentence,
                # so the two screens cannot report different counts.
                bb_headline = ui.label("").classes(
                    f"text-[13px] leading-none {CON_TXT_MUTED}")
            bb_box = ui.row().classes("w-full items-stretch gap-2 flex-wrap")

        # The four panels sit in a 2x2 grid, reading left-to-right then down in
        # the order the page argues: structure, then what to act on, then what is
        # already on. `items-stretch` so the two panels of a row square off at
        # the same height instead of leaving a stepped edge between them.
        #
        # TWO COLUMNS AT EVERY WIDTH (2026-08-20, by request). It was
        # `grid-cols-1 min-[2300px]:grid-cols-2`; with the breakpoint gone, that
        # breakpoint's arithmetic became the page's MINIMUM SUPPORTED WIDTH.
        # Positions is the panel that sets it:
        #
        #   725px  the TEN ``POS_GRID`` minmax floors summed
        #   + 72   nine 8px column gaps (``_GAP``)
        #   +  8   the row's own px-1, both sides (``_ROW``)
        #   + 32   the panel's px-4, both sides (``_panel``)
        #   +  2   the card's 1px border, both sides
        #   = 839px minimum for one panel      (the last three are PANEL_PAD_PX)
        #   x2 + 20px gutter = 1698px of panel content
        #   + 164 of measured chrome           = 1862px of LAYOUT width
        #   + 15 for the classic scrollbar     = 1877px of innerWidth
        #
        # At the 1920px window this page is read at, ``PANEL_BUDGET_PX`` hands
        # each panel 860px (measured: 861), so Positions clears its floor by
        # 21px and the other three by more (Board 783px, Dealer 757px, Flow
        # 508px). Measured live at 1920 and at 2560: no panel, and no cell in
        # one, reports a horizontal overflow — and at exactly 1877 the panels
        # measure 839px, so the sum above is the boundary rather than an
        # estimate of it. **Below it the page clips**: a CSS grid will not shrink
        # a track under its minmax() floor, so the rows overflow their card
        # rather than reflowing. Measured at 1600 (701px panels): Positions over
        # by 134px, the Board by 78, Dealer Positioning by 52; only Flow, with
        # four tracks, still fits. Keep this sum current when a floor moves; it
        # is the number the next track is sized against.
        #
        # `overflow-x-auto` is deliberately NOT the fallback — see the note above
        # ``_GAP``: a dashboard you scroll sideways to read defeats the page's
        # purpose. If the narrow case ever has to work, narrow the tracks — and
        # the type standing in them, together (see the ladder above).
        with ui.element("div").classes(
                "grid grid-cols-2 gap-5 w-full items-stretch"):
            dealer_body = _panel("DEALER POSITIONING", " · ".join(DESK_SYMBOLS))
            # Both subtitles are DERIVED from their panel's row cap, because
            # each used to be a word and a number written down separately — and
            # both numbers have now moved.
            board_body = _panel("OPPORTUNITY BOARD", f"HOTTEST {BOARD_ROWS_N}")
            flow_body = _panel("LIVE FLOW ALERTS", f"NEWEST {FLOW_ROWS_N}")
            pos_body = _panel("POSITIONS", " · ".join(
                b["source"] for b in BOOKS))

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
        # Both arc builders are the /sentiment page's own, and both index into
        # what they are handed, so the shape is checked HERE rather than there —
        # a string ``snaps`` would otherwise be iterated one character at a time.
        snaps = hist.get("snaps")
        snaps = snaps if isinstance(snaps, list) else []
        derived = comp.get("derived")
        derived = derived if isinstance(derived, dict) else {}
        live = comp.get("live")

        sent_arcs = _sentiment_arcs(live, snaps)
        sent_box.clear()
        with sent_box:
            # Day vs WEEK on Sentiment, Day vs MONTH on Trend — the console's own
            # pairing, kept because each names the horizon that horizon's card
            # is actually judged against.
            _compact_card("MARKET SENTIMENT", sent_arcs,
                          sentiment_pill_text(live, snaps),
                          _CC.delta_parts(_arc_value(sent_arcs, 0),
                                          _arc_value(sent_arcs, 1), "WEEK"))
        t_arcs = _trend_arcs(derived)
        trend_box.clear()
        with trend_box:
            _compact_card("MARKET TREND", t_arcs, trend_pill_text(derived),
                          _CC.delta_parts(_arc_value(t_arcs, 0),
                                          _arc_value(t_arcs, 2), "MONTH"))

    def _paint_bullbear():
        view = _view("sentiment:bullbear")
        bb_headline.text = bullbear_headline(view)
        chips = bullbear_chips(view)
        bb_box.clear()
        with bb_box:
            if not chips:
                ui.label(WAITING_BULLBEAR).classes(_PLACEHOLDER)
                return
            for chip in chips:
                _bullbear_chip(chip)

    def _bullbear_chip(chip):
        el = ui.column().classes(
            f"{_BB_CHIP} {_bb.quadrant_class(chip['quadrant'])}")
        with el:
            with ui.row().classes(
                    "items-baseline justify-between w-full gap-2 flex-nowrap"):
                ui.label(chip["label"]).classes(_BB_NAME)
                ui.label(chip["day_text"]).classes(_BB_DAY)
            # Both axes named, always — "Falling · Leading" is the whole reason
            # the map exists, and one word for it would be the ambiguity back.
            ui.label(_bb.quadrant_label(chip["quadrant"])).classes(_BB_QUAD)
            _bullbear_breadth(chip)
        el.on("click", lambda _e: _open_map())

    def _bullbear_breadth(chip):
        """The participation groove — how much of the sector confirms the move.

        A bar rather than only the thin/not-thin flag: the flag cannot separate
        34% (just over the line) from 96%, and participation is an INDEPENDENT
        third dimension rather than a tiebreak on the quadrant.
        """
        width = chip["breadth"]
        if width is None:
            # NO groove at all, which is a different drawing from an empty one:
            # a sector whose members were all unusable has no reading, where an
            # empty groove would state that nothing confirms. The spacer keeps
            # the chip the same height as the ones beside it.
            ui.element("div").classes("h-[3px] w-full")
            return
        with ui.element("div").classes(_BB_TRACK):
            # The documented continuous-value exception — 0..100 is 101 classes,
            # so this one arbitrary value is built at runtime. Set once per
            # repaint on a freshly cleared box, so there is nothing to remove.
            ui.element("div").classes(
                f"h-full rounded-full w-[{width}%] {_BB_FILL[chip['thin']]}")

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
            # Eight labels for eight tracks, ONE line per symbol. WHY and SETUP
            # were the two qualifiers riding under the symbol and the signal;
            # flat, each is a column of its own, which is what buys the extra
            # rows (see ``BOARD_ROWS_N``). The IV state is the one qualifier
            # that did NOT get a column: it is two words that only mean
            # anything against the number they follow, so it rides inside the
            # ATM IV cell on the same line.
            #
            # SCORE rather than HOTNESS because the first track cannot hold
            # seven letters of 10px caps on .2em tracking — and the panel's own
            # subtitle already says HOTTEST N, so nothing is lost.
            _grid_head(BOARD_GRID,
                       ("SCORE", "SYMBOL", "WHY", "ATM IV", "NET PREM", "P/C",
                        "SIGNAL", "SETUP"))
            for row in rows:
                _board_row(row)

    def _board_row(row):
        # One grid line, and every cell on it is a single line — the same
        # discipline the flow rows took. The proportional hotness bar that used
        # to sit under the score went with the second line: a bar IS a second
        # line by construction, and the scores are ranked and adjacent, so the
        # ordering already carries the comparison the bar was drawing.
        el = ui.element("div").classes(
            f"{BOARD_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06]")
        with el:
            _cell(fmt_hotness(row["hotness"]), CON_ACCENT)
            ui.label(row["symbol"]).classes(
                f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
            # A rationale is up to three clauses of ordinary words. Its track
            # carries by far the largest weight (see ``BOARD_GRID``) precisely
            # so this never ellipses at the width the page is read at; the
            # `truncate` is the graceful floor for the narrow two-column case,
            # not the expected behaviour.
            ui.label(row["rationale"] or _DASH).classes(
                f"{_SUB} min-w-0 truncate "
                + (CON_TXT_MUTED if row["rationale"] else CON_TXT_FAINT))
            # `items-baseline` so the 10px state word sits on the 13px value's
            # baseline rather than floating mid-cap.
            with ui.row().classes(
                    "items-baseline gap-[6px] flex-nowrap min-w-0"):
                _cell(fmt_iv(row["atm_iv"]))
                ui.label(row["iv_state"]).classes(
                    f"text-[10px] truncate {iv_state_class(row['iv_state'])}")
            _cell(fmt_net_prem(row["net_prem_m"]),
                  signed_class(row["net_prem_m"]))
            _cell(fmt_ratio(row["pc_ratio"]))
            # `self-start` on both chips, for the reason spelled out on the
            # dealer regime chip: a chip stretched to its track is a box around
            # a word rather than a label on it.
            ui.label(row["signal"].upper()).classes(
                f"self-start px-[5px] py-[2px] rounded-[2px] text-[9px] "
                f"tracking-[.1em] whitespace-nowrap {_signal_class(row['signal'])}")
            # An empty setup tag renders NO chip: an empty cell reads as "no
            # setup", where a "NEUTRAL" chip would read as a finding.
            if row["setup"]:
                ui.label(row["setup"]).classes(f"self-start {CHIP_SETUP}")
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
            # Four labels for four tracks, ONE line per alert. Every other panel
            # here stacks a qualifier under its value because it is short of
            # WIDTH; this panel is not — three of its five fields are a clock
            # time, a ticker and a two-word kind — so stacking bought nothing
            # and cost a line of height per alert. Flat, the same panel carries
            # nearly twice as many alerts (see ``FLOW_ROWS_N``).
            #
            # SIDE rides with KIND in the last track ("Unusual activity ·
            # Call"), which is where it was already being read from. It is
            # still "Call"/"Put", never bought/sold: Schwab publishes no
            # time-and-sales tape to this app, so nobody here knows who
            # initiated. DETAIL carries the premium the alert fired on, in the
            # Flow Alerts page's own wording.
            _grid_head(FLOW_GRID, ("TIME", "SYMBOL", "DETAIL", "KIND"))
            for row in rows:
                _flow_row(row)

    def _flow_row(row):
        # The glow class is applied at BUILD time and never touched again on a
        # live element. Changing ``animation-delay`` on a running animation
        # re-anchors its start, and the glow visibly jumps; both painters
        # ``.clear()`` and rebuild, which is the path the resume trick is
        # designed for (see the ``GLOW_SEC`` notes). ``state["glow_now"]`` is the
        # paint's single clock — not a fresh ``monotonic()`` per row.
        el = ui.element("div").classes(
            f"{FLOW_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06] "
            + glow_classes(state["glow"].get(row.get("id")),
                           state["glow_now"]))
        with el:
            ui.label(row["time"] or _DASH).classes(
                f"text-[11px] tabular-nums {CON_TXT_MUTED}")
            ui.label(row["symbol"]).classes(
                f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
            # `min-w-0` is what lets `truncate` bite: a grid item's automatic
            # minimum is its content, so without it a long detail line widens
            # the track past the panel instead of ellipsing inside it.
            ui.label(row["detail"] or row["text"] or _DASH).classes(
                f"text-[11px] min-w-0 truncate {CON_TXT_MUTED}")
            # ``_tone_class`` is stamped by the Flow Alerts page from its own
            # finite (type, side) map — borrowed here rather than re-derived,
            # and shared by the kind and the side it qualifies.
            ui.label(flow_kind_text(row)).classes(
                f"text-[10px] min-w-0 truncate {row['_tone_class']}")
        el.on("click", lambda _e: ui.navigate.to("/options/flow"))

    def _paint_positions():
        pos_body.clear()
        paper_view = _view("options:paper_account")
        driver_view = _view("options:driver_paper_account")
        captured_view = _view("options:captured")
        with pos_body:
            if paper_view is None and driver_view is None \
                    and captured_view is None:
                ui.label(WAITING_OPTIONS).classes(_PLACEHOLDER)
                return
            rows = position_rows(paper_view, driver_view, captured_view)
            # ⚠ The summary reads the FULL book; only the DRAW is capped. Moving
            # this line below the slice would make the panel report the P&L and
            # the at-risk count of whatever happened to fit on screen.
            summary = positions_summary(rows)
            shown = rows[:POSITION_ROWS_N]
            ui.label(summary_line(summary, len(shown))).classes(
                f"text-[11px] tracking-[.16em] pb-2 "
                + (CON_WARN if summary["at_risk"] else CON_TXT_MUTED))
            if not rows:
                ui.label("No open positions.").classes(_PLACEHOLDER)
                return
            # Ten labels for ten tracks, ONE line per row — the same move the
            # board and the flow feed just made, and here it is what pays for
            # the third book: a stacked row was 71px, a flat one 50px.
            # STRATEGY has its own column instead of riding under the symbol,
            # and EXPIRY carries its own countdown (see ``expiry_text``) instead
            # of riding under the strikes. ENTRY and MARK are the pair the
            # unrealized figure is the difference of, so the row shows its own
            # arithmetic rather than only its result.
            _grid_head(POS_GRID,
                       ("BOOK", "SYMBOL", "STRAT", "EXPIRY", "ENTRY", "MARK",
                        "STRIKES", "QTY", "UNREALIZED", "FLAG"))
            for row in shown:
                _position_row(row)

    def _position_row(row):
        # Rebuild-time only, same as the flow row above — never updated in place.
        el = ui.element("div").classes(
            f"{POS_GRID} {_ROW} hover:bg-[{_C['line']}]/[0.06] "
            + glow_classes(state["glow"].get(row.get("position_id")),
                           state["glow_now"]))
        with el:
            ui.label(row["source"]).classes(
                f"self-start {source_chip_class(row['source'])}")
            ui.label(row["symbol"]).classes(
                f"text-[14px] font-bold tracking-[.08em] {REF_TXT_STRONG}")
            ui.label(strategy_label(row["strategy"])).classes(
                f"text-[11px] min-w-0 truncate {CON_TXT_MUTED}")
            _cell(expiry_text(row))
            # Both are per-share option prices (0.21 / 0.39), not position
            # dollars — the same numbers the paper ledger quotes.
            _cell(fmt_money(row["entry_credit"]))
            _cell(fmt_money(row["current_value"]))
            _cell(row["strikes"])
            # An em-dash, never a 1: a captured signal was never sized, and a
            # printed quantity would be this page inventing a position.
            _cell(_DASH if row["quantity"] is None else f"{row['quantity']:g}")
            _cell(fmt_money(row["unrealized_pnl"]),
                  signed_class(row["unrealized_pnl"]))
            # An untagged book gets the dash BARE, with no chip around it — the
            # same rule the dealer row follows for an unknown regime. An
            # outlined box holding an em-dash reads as a broken widget, and a
            # box in the FLAG column reads as a verdict whatever is inside it.
            if row["flag"] == UNTAGGED_FLAG:
                ui.label(UNTAGGED_FLAG).classes(
                    f"self-start text-[11px] {CON_TXT_FAINT}")
            else:
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
    def _open_map():
        """The strip is a pointer, not a second map: every industry and stock
        inside a sector lives one click away, and none of them is on this
        page."""
        ui.navigate.to(BULLBEAR_ROUTE)

    @guard
    def _open_position(row):
        """Each book has its own page; the source chip is what decides which."""
        ui.navigate.to(POSITION_ROUTES.get(row.get("source"), "/options/paper"))

    painters = {"strip": _paint_strip, "bullbear": _paint_bullbear,
                "dealer": _paint_dealer, "board": _paint_board,
                "flow": _paint_flow, "positions": _paint_positions}

    # ── arrival detection ────────────────────────────────────────────────────
    # Thin: read the cache, build the rows, hand them to the module-level fold.
    # Everything that DECIDES anything lives up there, where a test can reach it.
    def _detect_flow(now):
        # ``_flow.alert_rows``, not ``flow_rows`` — the FULL list, not the nine
        # the panel draws. A burst of ten would otherwise push arrivals off the
        # bottom unseen and announce them later, when the list shortened.
        rows = _flow.alert_rows(_view("options:flow_alerts"))
        return fold_flow_arrivals(state, rows, now)

    def _detect_positions(now):
        rows = position_rows(_view("options:paper_account"),
                             _view("options:driver_paper_account"),
                             _view("options:captured"))
        return fold_position_arrivals(state, rows, now)

    def _paint(payloads):
        """Merge the changed views in, then repaint only what depends on them."""
        state["data"].update(payloads)
        changed = set(payloads)
        state["speak"] = []
        # ONE clock for the whole paint. Two would let ``prune_glows`` drop an
        # entry that ``glow_classes`` is then asked to draw, or the reverse.
        now = time.monotonic()
        state["glow_now"] = now
        # Detection FIRST: the painters read ``state["glow"]``, so a row has to
        # be marked before the paint that is supposed to draw it lit.
        for region, detect in (("flow", _detect_flow),
                               ("positions", _detect_positions)):
            if changed.intersection(_REGION_VIEWS[region]):
                said = detect(now)
                if said:
                    state["speak"].append(said)
        prune_glows(state["glow"], now)
        for region, deps in _REGION_VIEWS.items():
            if changed.intersection(deps):
                painters[region]()

    @guard_async
    async def _speak_pending():
        """Turn the phrases ``_paint`` queued into clips and play them.

        Synthesis is BLOCKING (~850 ms on a cache miss), so it goes through
        ``run.io_bound`` — never on the event loop, which every page shares.
        The queue is taken and cleared FIRST: an ``await`` below can outlive
        this tick, and a phrase left in the queue would be spoken twice.
        """
        phrases, state["speak"] = state["speak"], []
        if not phrases:
            return
        settings = app_settings.load()
        if not should_speak(settings, datetime.now(_CT)):
            return
        urls = []
        for text in phrases:
            url = await run.io_bound(_voice.ensure, text,
                                     settings.get("voice_name"))
            # ``ensure`` never raises; None is "no clip", and the row has
            # already glowed, so a dead synthesis endpoint costs the sentence
            # and nothing else.
            if url:
                urls.append(url)
        if not urls:
            return
        # ``json.dumps`` rather than an f-string join: the URLs are
        # ``voice.clip_url``'s "/voice/<sha1>.mp3" and could not carry a quote,
        # but the escaping should not depend on knowing that.
        ui.run_javascript(
            f"window.__deskSpeak({json.dumps(urls)}, {speak_volume(settings)})")

    @guard
    def _voice_blocked(_e=None):
        """The browser refused to play. Offer the gesture that fixes it."""
        unlock_btn.set_visibility(True)

    @guard_async
    async def _unlock_voice(_e=None):
        """Hide the prompt and say one line, so the unlock is audibly confirmed.

        The click itself is what unblocks audio — user activation is sticky for
        the document, so the ``await`` below does not cost it. Any other click
        on the page unlocks it too; this button exists because nothing TELLS the
        user that.
        """
        unlock_btn.set_visibility(False)
        settings = app_settings.load()
        url = await run.io_bound(_voice.ensure, VOICE_UNLOCK_PHRASE,
                                 settings.get("voice_name"))
        if url:
            ui.run_javascript(
                f"window.__deskSpeak({json.dumps([url])}, "
                f"{speak_volume(settings)})")

    ui.on(VOICE_BLOCKED_EVENT, _voice_blocked)
    unlock_btn.on_click(_unlock_voice)

    @guard
    def _tick_clock():
        """How much session is left — not what time it is.

        Both the caption and the value move, because outside regular hours this
        counts to the next OPEN and inside them to the CLOSE, and a value
        without its caption would be ambiguous by exactly the amount that
        matters. No reactive colour: the caption already carries the state, and
        a second signal on a 1 s tick is churn."""
        facts = countdown_facts(datetime.now(_CT))
        clock_cap.text = facts["label"]
        clock_lbl.text = facts["text"]

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
        # After the paint, never before: the row must already be lit when the
        # sentence starts, and synthesis can take a second.
        await _speak_pending()

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
    # Everything on screen at first paint is history, not an arrival. Clearing
    # the flag AFTER the seed paint is what makes it so — the folds have just
    # recorded the whole backlog into ``seen_*`` without glowing or speaking a
    # line of it, so the next poll can only report genuine movement.
    state["first"] = False
    _prewarm_clips(seed)
    ui.timer(CLOCK_SEC, _tick_clock)
    ui.timer(POLL_SEC, _poll)
