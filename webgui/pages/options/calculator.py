"""Options strategy Calculator page (Tier-3 reader).

A three-step screen: ① STRATEGY and ③ LEGS fill a fixed 424 px input column with
the action grid under them; ② SYMBOL, six metric cards and the P&L matrix
(``calc_spread_pnl``: price × eval-date pairs of $ and %) fill the results
column beside it. The palette is the page-scoped ``[calc]`` language
(``.calc-v3``), not the app-wide dark navy.

Everything the screen shows beyond the service's own payload — the per-leg
delta, the ③ LEGS net/max-loss strip, the status pill, the matrix ``%`` column,
the metric cards — is derived HERE by a pure function over payloads
``options_svc`` already caches. **No Tier-2 change**: ``calc_load`` /
``calc_compute`` / ``calc_iv`` keep their contracts.

This page holds **no engine call**: the symbol quote + option-chain fetch and the
options-calculator math (the summary + the P&L grid) live in
``services/options_svc/compute`` (``calc_load_symbol``/``calc_compute``). The
option chain is a plain JSON dict, so it round-trips through the bus cache; the
page keeps its PURE chain-extractors (``extract_atm_iv``/``extract_premium``/
``chain_expiries``/``chain_strikes``) and runs them LOCALLY on the cached chain.

Interaction model:

* **load** → enqueue ``calc_load``; a version-poll on ``options:calc_chain``
  populates price/range/expiries/strikes from the cached chain dict.
* **IV** / **Fetch premiums** operate LOCALLY on the cached chain (no command).
* **Calculate** → enqueue ``calc_compute`` with the form params; a version-poll on
  ``options:calc_result`` repaints the metric cards + P&L matrix from the cached
  ``{summary, eval_labels, pnl_data}``.

Pure transforms (banding, grid mapping, formatting, chain extractors) are
unit-tested; ``render()`` wires the form + visuals.
"""
import datetime as dt
import math
from types import SimpleNamespace

from .inputs import select_all_on_focus, should_load
# The page-scoped ``[calc]`` language (scope hook ``.calc-v3``) — this screen's
# own near-black palette, deliberately NOT the app-wide dark navy the Simulator
# and Trade share under ``.calc-v2``.
from .theme import (THEME, CALC_CSS, CALC_KEYFRAMES_CSS, CALC_FONT_HEAD_HTML,
                    CALC_MONO, CALC_PAGE, CALC_FRAME, CALC_FRAME_IDLE, CALC_CHIP,
                    CALC_TILE, CALC_BTN, CALC_BTN_PRIMARY, CALC_STRATEGY_BTN,
                    CALC_EYEBROW, CALC_BODY, CALC_MUTED, CALC_DIM,
                    CALC_POS, CALC_NEG, CALC_ACCENT, CALC_WARN, CALC_STATE_TEXT,
                    CALC_EDGE_POS, CALC_EDGE_NEG, CALC_EDGE_ACCENT, CALC_EDGE_WARN)
from . import page_state as _ps

# Persisted (single-user) Calculator input snapshot — survives navigation + browser
# reload, resets on a webgui restart (same as the other persisting pages). The pure
# snapshot/merge/precedence helpers live in page_state.py.
_CALC_KEYS = ("symbol", "strategy", "legs", "iv", "rate", "ivadj", "contracts",
              "price", "num_strikes", "expiry")
_CALC_DEFAULTS = {"symbol": "SPY", "strategy": "PCS", "legs": [], "iv": 20.0,
                  "rate": 4.5, "ivadj": 0.0, "contracts": 1, "price": 100.0,
                  "num_strikes": 24, "expiry": None}
_LAST_CALC: dict = {}

def strategy_options():
    """Flat list of strategy codes for the dropdown, in ``STRATEGY_GROUPS`` order.

    The editable leg-editor (``pages.options.strategies``) is the single source of
    truth for the strategy table, so the Calculator + Simulator never drift. Codes
    span singles / verticals / condors / butterflies / calendars; the analytic
    summary path is auto-selected per code in the Tier-2 ``calc_compute``.
    """
    from . import strategies as S

    return [code for _label, codes in S.STRATEGY_GROUPS for code in codes]


def _summary_strategy(strategy_code, legs, dirty):
    """The strategy code to send to ``calc_compute`` for summary routing.

    The analytic summary (PCS/CCS/IC/singles) is used only when the legs are
    untouched AND still MATCH the selected template (shape + single expiry); an
    edited structure, or one copied in while the dropdown reads a different code,
    falls to the generic numeric summary (``"CUSTOM"``). ``summary_code`` (shared
    with the Simulator) is the single source of truth for the match check.

    NB: imports ``strategies`` itself — the page's only ``strategies`` alias is a
    local inside ``strategy_options``, so referencing it inline in ``do_calc``
    raised ``NameError: name 'S' is not defined`` on Calculate."""
    if dirty:
        return "CUSTOM"
    from . import strategies as S
    return S.summary_code(strategy_code, legs)


def grid_extremes(pnl_data):
    """(global_max, global_min) over every P&L value in the grid."""
    vals = [p for r in (pnl_data or []) for p in (r.get("pnl") or [])
            if isinstance(p, (int, float))]
    if not vals:
        return 0.0, 0.0
    return max(vals), min(vals)


def grid_rows(pnl_data):
    """[{price, cells:[{pnl, pnl_pct}, ...]}, ...] from calc_spread_pnl output."""
    rows = []
    for r in pnl_data or []:
        pnls = r.get("pnl") or []
        pcts = r.get("pnl_pct") or []
        cells = [{"pnl": pnls[i] if i < len(pnls) else None,
                  "pnl_pct": pcts[i] if i < len(pcts) else None}
                 for i in range(len(pnls))]
        rows.append({"price": r.get("price"), "cells": cells})
    return rows


def eval_date_labels(dates):
    return [d.strftime("%m/%d") if hasattr(d, "strftime") else str(d) for d in dates or []]


def fmt_dollar(v):
    return f"{v:+,.0f}" if isinstance(v, (int, float)) else "—"


def fmt_pct(v):
    return f"{v:+.1f}%" if isinstance(v, (int, float)) else "—"


def api_symbol(symbol):
    """Map a user symbol to the Schwab API form ($SPX for SPX index)."""
    s = (symbol or "").strip().upper()
    return "$SPX" if s == "SPX" else s


def extract_atm_iv(chain, spot, expiry=None):
    """ATM implied vol (as a percentage) from an option-chain payload.

    Picks the contract whose strike is closest to ``spot`` and reads its
    ``volatility`` (Schwab returns it as a percentage or a decimal — normalize).
    When ``expiry`` is given, only contracts under that expiry are considered
    (chain exp keys look like ``"2026-06-19:5"``). Returns None if no usable
    volatility is found.
    """
    if not isinstance(chain, dict) or not isinstance(spot, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    best_diff = float("inf")
    best = None
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key, strikes in (chain.get(map_key) or {}).items():
            if exp_iso and exp_key.split(":")[0] != exp_iso:
                continue
            for strike_str, contracts in (strikes or {}).items():
                try:
                    strike = float(strike_str)
                except (ValueError, TypeError):
                    continue
                if not (isinstance(contracts, list) and contracts):
                    continue
                vol = contracts[0].get("volatility")
                if vol is None:
                    continue
                diff = abs(strike - spot)
                if diff < best_diff:
                    best_diff = diff
                    best = vol if vol < 5.0 else vol / 100.0
    return None if best is None else best * 100.0


def _find_contract(chain, option_type, strike, expiry=None):
    """The first chain contract matching one leg: call/put map, strike within
    0.51, and — when ``expiry`` is given — that expiry only. None if not found.

    The shared walk behind ``extract_premium`` and ``extract_delta``, so the
    premium and the delta on one leg row are always read off the SAME contract
    and can never come from different strikes.
    """
    if not isinstance(chain, dict) or not isinstance(strike, (int, float)):
        return None
    exp_iso = None
    if expiry is not None:
        exp_iso = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    for exp_key, strikes in (chain.get(map_key) or {}).items():
        if exp_iso and exp_key.split(":")[0] != exp_iso:
            continue
        for strike_str, contracts in (strikes or {}).items():
            try:
                sk = float(strike_str)
            except (ValueError, TypeError):
                continue
            if abs(sk - strike) < 0.51 and isinstance(contracts, list) and contracts:
                return contracts[0]
    return None


def extract_premium(chain, option_type, strike, expiry=None):
    """Premium for one leg (mark, else bid/ask mid) from the chain.

    Matches the strike within 0.51 in the call/put map for ``option_type``.
    When ``expiry`` is given, only that expiry is considered. None if not found.
    """
    c = _find_contract(chain, option_type, strike, expiry)
    if c is None:
        return None
    mark = c.get("mark")
    if mark and mark > 0:
        return mark
    bid = c.get("bid", 0) or 0
    ask = c.get("ask", 0) or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


# ── the redesign's page-side readouts ────────────────────────────────────────
# Everything the rebuilt screen shows beyond today's page — the per-leg delta,
# the ③ LEGS strip, the matrix % column, the status pill — is derived HERE, from
# payloads options_svc already caches. No Tier-2 change; no second pricing model.

# Mirror of UNLIMITED (999999) in the scanner's options-calculator module — a
# magic placeholder the service returns VERBATIM for an uncapped max_profit
# (LONG_CALL) or max_loss (NAKED_CALL). It is NOT infinity: divide by it and
# every matrix cell reads +0.0%; format it and the tile reads "$999,999".
# Tier-1 may not import that module, so the value is restated here; keep the
# two in step. (The name is written hyphenated on purpose — the Tier-1 guard
# test bans the module's import token anywhere in this file, comments included.)
UNLIMITED = 999999

# Past this, a "delta" is the chain's missing-greek SENTINEL rather than a
# reading: Schwab sends -999.0 for a greek it does not have, and a real option
# delta never leaves [-1, 1]. ``options_svc.flow_alerts.detect_big_delta`` drops
# the same contracts for the same reason.
_DELTA_LIMIT = 1.0

# Contracts per option — the multiplier on every dollar figure below.
_SHARES_PER_CONTRACT = 100


def is_unlimited(v):
    """Whether a summary figure is the service's uncapped sentinel."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == UNLIMITED


def _finite(v):
    """``v`` as a float when it is a real finite number, else None.

    Bools are rejected: ``True`` is not a premium, and ``isinstance(True, int)``
    would otherwise let one through as 1.0."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _leg_qty(leg):
    """A leg's contract count as a float, or None when it is unusable.

    A missing or falsy qty means one contract (the page's long-standing
    ``int(leg.get("qty", 1) or 1)`` convention); junk means None, so the caller
    degrades to an em-dash rather than pricing an unknown size."""
    return _finite(leg.get("qty", 1) or 1)


def extract_delta(chain, option_type, strike, expiry=None):
    """Per-contract delta for one leg from the cached chain, or ``None``.

    Reads the chain's OWN ``delta`` (the field ``flow_alerts`` uses), so this is
    market delta rather than a second pricing model living in Tier 1.

    ⚠ Returns ``None`` — never ``0.0`` — when the contract carries no usable
    delta. Index option chains read hollow outside regular hours, and a ``0.00``
    on an otherwise live-looking row is a confident wrong number. A genuine
    ``0.0`` (a far out-of-the-money contract) IS kept; what is rejected is the
    missing-greek sentinel, i.e. anything outside ``[-1, 1]``.
    """
    c = _find_contract(chain, option_type, strike, expiry)
    d = _finite(c.get("delta")) if isinstance(c, dict) else None
    if d is None or abs(d) > _DELTA_LIMIT:
        return None
    return d


def leg_delta(chain, leg):
    """One leg's POSITION delta from the cached chain, or ``None``.

    TOTAL by construction — no chain, no strike, an unknown option type or a
    strike the chain does not carry all return ``None``, and the leg card's
    DELTA cell renders an em-dash. It runs inside the leg editor's ``_render``,
    where an exception would propagate out of the page build and leave a blank
    screen, so it must not raise rather than being wrapped in a try/except.

    ⚠ Reads the leg's OWN expiry and does NOT fall back to a cross-expiry
    match. ``extract_delta`` returns ``None`` both for "no such contract" and
    for "the contract carries Schwab's -999.0 sentinel", so a fallback cannot
    tell them apart — and ``_find_contract`` answers with the FIRST expiry in
    dict order carrying that strike. A December leg would silently render
    August's delta. (The fallback bought nothing either: ``leg_editor._render``
    coerces every leg's expiry into ``expiries_for()`` before the body reads it,
    so a leg dated off the loaded ladder cannot reach here.)
    """
    leg = leg or {}
    strike = leg.get("strike")
    otype = leg.get("option_type")
    if not chain or otype not in ("call", "put"):
        return None
    if isinstance(strike, bool) or not isinstance(strike, (int, float)):
        return None
    d = extract_delta(chain, otype, float(strike), leg.get("expiry") or None)
    return position_delta(d, leg.get("side"))


def position_delta(delta, side):
    """Contract delta signed for the POSITION: a short leg inverts it.

    PER CONTRACT — deliberately not multiplied by ``qty``. The leg row shows it
    beside its own QTY cell, and this is the number that compares directly with
    a broker's chain. An aggregate would be ``sum(position_delta(d, side) * qty)``
    and belongs in its own function, not folded in here."""
    if isinstance(delta, bool) or not isinstance(delta, (int, float)):
        return None
    return -delta if side == "short" else delta


def net_premium(legs):
    """Net cash at entry in dollars: positive = credit received, negative = paid.

    ``None`` when any leg is unpriced. Legs arrive with ``premium=None`` until
    Fetch premiums runs, and counting those as zero would print "NET $0" over a
    position whose cash is simply not known yet. No legs is a true ``0.0``."""
    total = 0.0
    for leg in legs or []:
        prem = _finite(leg.get("premium"))
        qty = _leg_qty(leg)
        if prem is None or qty is None or qty < 0:
            return None
        sign = 1 if leg.get("side") == "short" else -1
        total += sign * prem * qty * _SHARES_PER_CONTRACT
    return round(total, 2)          # a cash figure: cents are the meaningful unit


def net_call_quantity(legs):
    """Net LONG call contracts across the position (shorts negative), or ``None``.

    The one number that decides both unbounded ends of an expiration payoff,
    because only calls stay linear above the last strike:

    * **negative** — net short calls, so LOSS grows without bound above the last
      strike (a naked call, a call ratio credit). ``max_loss_estimate`` refuses.
    * **positive** — net long calls, so PROFIT does (a long call, a backspread).
      ``profit_uncapped_above`` says so; see the note there for why the page has
      to work this out for itself.

    ``None`` when any call leg's qty is unusable — the caller then claims
    nothing rather than reading a junk leg as zero."""
    total = 0.0
    for leg in legs or []:
        if leg.get("option_type") != "call":
            continue
        qty = _leg_qty(leg)
        if qty is None or qty < 0:
            return None
        total += -qty if leg.get("side") == "short" else qty
    return total


def profit_uncapped_above(legs):
    """Whether the position's profit grows without bound above the last strike.

    ⚠ This exists because the SUMMARY cannot be trusted to say so. The service
    routes an untouched single-leg template through the analytic summary, which
    returns the ``UNLIMITED`` sentinel for a long call — but the moment any leg
    is edited the strategy becomes ``CUSTOM`` and it routes through the GENERIC
    numeric path, whose ``max_profit`` is ``max(pnl)`` over a price grid that
    stops at 1.5x spot. That figure is the grid's edge, not a cap: measured on
    SPY 668 with a 670 call, the same position reads "Unlimited" untouched and
    "$32,780 at 30d expiry" after a nudge. Printing the second is exactly the
    confidently-stated wrong number this screen is written against.

    The legs decide it, so both routings land on one screen."""
    n = net_call_quantity(legs)
    return n is not None and n > 0


def _short_outlives_long(legs):
    """True when a SHORT leg expires LATER than a long one (a reverse calendar).

    The single-date payoff below would model that short as settled while it is
    still live, and report an uncovered credit as riskless."""
    shorts = [str(l.get("expiry")) for l in legs or []
              if l.get("side") == "short" and l.get("expiry")]
    longs = [str(l.get("expiry")) for l in legs or []
             if l.get("side") != "short" and l.get("expiry")]
    return bool(shorts and longs and max(shorts) > min(longs))


def max_loss_estimate(legs):
    """The ③ LEGS strip's max-loss figure, in dollars, or ``None``.

    The MINIMUM of the position's expiration payoff. That curve is piecewise
    linear with corners only at the strikes, so evaluating ``net_premium`` plus
    intrinsic value at ``{0} ∪ strikes`` finds the true minimum exactly — no
    pricing model, no width heuristic, and no per-structure special cases. It is
    therefore right for everything the templates build and for anything edited by
    hand: an iron condor risks ONE side rather than both, a 1-2-1 butterfly's
    qty-2 middle leg needs no handling of its own, and a lone short put reads its
    real ``strike × 100 − credit`` instead of a width that does not exist.

    ``None`` — an em-dash — rather than a wrong dollar figure when:

    * a leg is unpriced or has no strike (nothing to evaluate);
    * the net call quantity is SHORT, so loss grows without bound above the
      last strike (a naked call, a call ratio credit);
    * a short leg outlives a long one, which this single-date model cannot see.

    ⚠ Every leg is settled at ONE date. For the templates' calendars/diagonals
    (short front, long back) that is the standard convention and gives the right
    answer — the back leg's residual value only helps — but it is an assumption,
    not a valuation. The authoritative MAX RISK tile still comes from the
    service's summary; this is the header strip.
    """
    net = net_premium(legs)
    if net is None:
        return None
    rows = []
    for leg in legs or []:
        strike = _finite(leg.get("strike"))
        qty = _leg_qty(leg)
        if strike is None or qty is None or qty < 0:
            return None
        rows.append(("call" if leg.get("option_type") == "call" else "put",
                     1 if leg.get("side") != "short" else -1, qty, strike))
    if not rows:
        return 0.0
    if _short_outlives_long(legs):
        return None
    net_calls = net_call_quantity(legs)
    if net_calls is None or net_calls < 0:
        return None

    def _payoff(spot):
        v = 0.0
        for kind, sign, qty, strike in rows:
            intrinsic = max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)
            v += sign * qty * _SHARES_PER_CONTRACT * intrinsic
        return net + v

    worst = min(_payoff(s) for s in [0.0] + [r[3] for r in rows])
    return round(-worst, 2) if worst < 0 else 0.0


def usable_denominator(v):
    """``v`` as a strictly positive finite denominator, else ``None``.

    Refuses the uncapped sentinel above all: ``999999`` is a placeholder, not a
    figure, and dividing by it paints ``+0.0%`` down a whole column — a fake
    measurement stated confidently on every row."""
    f = _finite(v)
    return f if (f is not None and f > 0 and not is_unlimited(f)) else None


def matrix_pct_of(pnl, denominator):
    """A matrix cell's P&L as a percentage of ``denominator``.

    ``None`` — an em-dash, never a ``0.0%`` that would read like a measurement —
    when either side is unusable. Which denominator applies is ``matrix_basis``'s
    decision, taken ONCE per render; this function only divides."""
    p = _finite(pnl)
    d = usable_denominator(denominator)
    return None if p is None or d is None else p / d * 100.0


# The three bases, one shape. The heading is PART of the basis rather than a
# separate decision: a percentage whose denominator the reader has to infer is
# worse than no percentage at all. ``matrix_basis`` fills the denominator in for
# the first two; the third has none by definition.
MATRIX_BASIS_MAX = {"kind": "max", "denominator": None, "heading": "% MAX"}
MATRIX_BASIS_COST = {"kind": "cost", "denominator": None, "heading": "% COST"}
MATRIX_BASIS_NONE = {"kind": "none", "denominator": None, "heading": "%"}


def matrix_basis(summary, legs=None):
    """What the matrix's ``%`` column is a percentage OF: ``{kind, denominator,
    heading}``, resolved once per render from the service's summary and the legs
    it was computed from.

    Three cases, in order:

    * **max** — a real capped max return. The column reads directly against the
      MAX RETURN tile above the matrix. For a credit structure this is exactly
      the service's old ``pnl_pct`` (its ``max_profit`` IS the entry credit), so
      those screens are numerically unchanged.
    * **cost** — no capped return (a long call's ``max_profit`` is the uncapped
      sentinel), but the position was BOUGHT, so what it cost is an honest
      denominator and "+125% of cost" is the figure a trader wants. The debit
      comes from ``entry_credit`` — negative for a debit, and the same figure
      the ENTRY DEBIT tile shows, so the column and the tile agree by
      construction. (``net_premium(legs)`` computes the identical quantity
      page-side; the summary's is preferred because it is the tile's own
      number.)
    * **none** — neither. Em-dash cells under a bare ``%``: no basis, no claim.

    ⚠ On the generic NUMERIC path (any structure the analytic summaries do not
    cover — debit verticals, butterflies, calendars) the service's ``max_profit``
    is ``max(pnl)`` over ITS OWN price grid, not a closed-form cap. Correct, but
    surprising: widen that grid and the denominator can move, so the same cell
    can read a different ``% MAX``. The analytic paths (PCS/CCS/IC and the
    singles) return a true cap and do not drift.

    ⚠ Where that grid edge is not merely imprecise but WRONG — a net-long-call
    structure, whose profit has no cap at all — ``legs`` overrides the summary
    and the column falls through to ``% COST`` (see ``profit_uncapped_above``).
    Without it, editing one strike on a long call swaps the whole matrix from
    ``% COST`` to a ``% MAX`` measured against a fabricated denominator, and the
    heading asserts that denominator on every row. ``legs`` defaults to ``None``
    — no legs, no override, and the summary decides as before.
    """
    s = summary or {}
    cap = None if profit_uncapped_above(legs) else usable_denominator(s.get("max_profit"))
    if cap is not None:
        return dict(MATRIX_BASIS_MAX, denominator=cap)
    credit = _finite(s.get("entry_credit"))
    cost = usable_denominator(-credit) if credit is not None else None
    if cost is not None:
        return dict(MATRIX_BASIS_COST, denominator=cost)
    return dict(MATRIX_BASIS_NONE)


# ── the P&L matrix ───────────────────────────────────────────────────────────
# The heat ramp is a DATA-DRIVEN colour map — the one category ``config/theme.toml``
# deliberately keeps out of the palette (see the note in its ``[calc]`` header) —
# so it lives here beside the renderer rather than in ``theme.py``. The two hues
# ARE the ``[calc]`` ``pos``/``neg`` signal colours; the text tones are their
# light ends, and the spot amber is ``[calc]`` ``warn``.
_MATRIX_PROFIT_RGB = "45,212,167"       # #2dd4a7
_MATRIX_LOSS_RGB = "251,95,124"         # #fb5f7c
_MATRIX_PROFIT_FG = "#b8f5e4"
_MATRIX_LOSS_FG = "#ffd0d9"
_MATRIX_EMPTY_FG = "#6f8598"            # [calc] `dim` — a cell with no reading

MATRIX_SPOT = "#f5b841"                 # the spot row, and the expiry heading
MATRIX_HEAD_BG = "#080d13"              # the sticky header + price-column ground
MATRIX_HEAD_RULE = "#22303e"            # under the header, and the outer frame
MATRIX_ROW_RULE = "rgba(19,31,43,.7)"   # between price rows
MATRIX_LABEL_FG = "#7189a0"             # a heading that is not the expiry
MATRIX_VOID = "#05070a"                 # behind an untinted (missing) cell
MATRIX_PRICE_FG = "#eaf2f9"             # the price ladder itself

# The alpha ramp: a floor, so the weakest cell still reads as tinted rather than
# as no data; and a ceiling well under 1, so the figure printed ON the tint stays
# legible at full saturation.
_MATRIX_ALPHA_FLOOR = 0.10
_MATRIX_ALPHA_SPAN = 0.42

# Past this a percentage has stopped being a reading and is only blowing the
# column open. It is the LOSS side that gets there: a credit spread risks a
# multiple of its max return, and a naked put ~200x.
_MATRIX_PCT_LIMIT = 999


def _matrix_pct_text(pct):
    """A matrix percentage as text: an em-dash for no reading, clamped at ±999%."""
    if pct is None:
        return "—"
    if pct > _MATRIX_PCT_LIMIT:
        return f">{_MATRIX_PCT_LIMIT}%"
    if pct < -_MATRIX_PCT_LIMIT:
        return f"<-{_MATRIX_PCT_LIMIT}%"
    return f"{pct:+.1f}%"


def matrix_cell_facts(pnl, basis, g_max, g_min):
    """One matrix cell: ``{dollars, pct, bg, fg, alpha}``.

    Tint magnitude is relative to the grid's OWN extremes, and to each SIDE's
    extreme separately, so the best and the worst cell on screen both saturate.
    The two alternatives are worse: one shared scale would wash the profit zone
    of every credit structure out to nothing (the risk is routinely several times
    the reward), and scaling against the summary's max profit / max risk would
    leave a narrow price window — one that never reaches the wings — a uniform
    faint blur, and is not even available when ``max_profit`` is the uncapped
    sentinel. The dollar figure printed in the same cell carries the absolute
    magnitude; the tint carries the shape.

    ``pct`` is a percentage of whatever ``matrix_basis`` resolved — max return,
    or cost for a position with no capped return — and an em-dash, never a
    ``0.0%``, when it resolved neither. The basis dict is passed whole rather
    than as a bare denominator so the cells and the heading above them cannot
    come from different decisions.
    """
    p = _finite(pnl)
    if p is None:
        return {"dollars": "—", "pct": "—", "bg": "transparent",
                "fg": _MATRIX_EMPTY_FG, "alpha": 0.0}
    profit = p >= 0
    scale = _finite(g_max) if profit else _finite(g_min)
    scale = abs(scale) if scale else 0.0
    ratio = min(abs(p) / scale, 1.0) if scale > 0 else 0.0
    alpha = round(_MATRIX_ALPHA_FLOOR + ratio * _MATRIX_ALPHA_SPAN, 3)
    return {
        "dollars": f"{p:+,.0f}",
        "pct": _matrix_pct_text(matrix_pct_of(p, (basis or {}).get("denominator"))),
        "bg": f"rgba({_MATRIX_PROFIT_RGB if profit else _MATRIX_LOSS_RGB},{alpha})",
        "fg": _MATRIX_PROFIT_FG if profit else _MATRIX_LOSS_FG,
        "alpha": alpha,
    }


def matrix_headers(eval_labels, basis=None):
    """Column headings; the LAST column is the expiry and is flagged so the page
    can colour it amber (the design's one emphasised column).

    Each date column heads a ``$`` and a percentage sub-column, and the
    percentage's heading is ``basis["heading"]`` — the column always names what
    it is a percentage of. No basis means no claim: a bare ``%``."""
    labels = list(eval_labels or [])
    last = len(labels) - 1
    pct = (basis or {}).get("heading") or MATRIX_BASIS_NONE["heading"]
    # No special case for the "Now" column: ``.upper()`` already renders it
    # "NOW $", and a branch that cannot change the output is a branch that
    # cannot be tested.
    return [{"label": f"{str(lab).strip()} $".upper(), "pct_label": pct,
             "expiry": i == last}
            for i, lab in enumerate(labels)]


def chain_status_facts(loading, symbol, chain):
    """The title-bar status pill + the ② SYMBOL frame's hint.

    ``{state, label, hint}`` where state is ``idle`` / ``loading`` / ``ready``.
    An EMPTY chain dict is ``idle``, not ``ready`` — a chain that arrived
    carrying nothing is not a loaded chain, and colouring the frame for it would
    announce data the page does not have."""
    if loading:
        return {"state": "loading", "label": "LOADING CHAIN", "hint": "···"}
    if _has_contracts(chain):
        sym = (symbol or "").strip().upper()
        return {"state": "ready",
                "label": f"CHAIN LOADED · {sym}" if sym else "CHAIN LOADED",
                "hint": "LIVE"}
    return {"state": "idle", "label": "AWAITING SYMBOL", "hint": "NOT LOADED"}


def results_panel_facts(status, has_result):
    """The dashed placeholder's copy, or ``None`` once a result is on screen.

    ``{label, hint}``. The screen has TWO waits and they need different actions
    from the reader — a chain that has not been fetched, and a structure that
    has not been priced — so the panel names which one it is. Derived from
    ``chain_status_facts`` rather than written twice, so the placeholder and the
    status pill can never disagree about which phase the page is in.
    """
    if has_result:
        return None
    if (status or {}).get("state") == "ready":
        return {"label": "AWAITING CALCULATION",
                "hint": "press CALCULATE to price the structure across the price "
                        "ladder and every date between now and expiry"}
    return {"label": "AWAITING CHAIN",
            "hint": "pick a strategy, type a ticker, then tab out — the chain loads "
                    "and legs resolve to real strikes before anything can be priced"}


def chain_line(status, symbol, expiry_count, strike_count):
    """The ② SYMBOL frame's one-line footer, under the scan bar.

    Says what the page is doing, or — once loaded — how much of a chain it got,
    which is the number that explains a leg whose strike will not snap where the
    user expects."""
    state = (status or {}).get("state")
    if state == "loading":
        sym = (symbol or "").strip().upper()
        return f"fetching option chain · {sym}" if sym else "fetching option chain"
    if state == "ready":
        return (f"{int(strike_count or 0)} strikes · "
                f"{int(expiry_count or 0)} expiries")
    return "tab out of the symbol field to load the chain"


def tag_tone(tag, first):
    """Tone for one ① STRATEGY tag chip: ``pos`` / ``warn`` / ``muted``.

    Only the FIRST chip is coloured, and only ever by cash-flow direction —
    ``strategy_tags`` guarantees that chip is CREDIT or DEBIT. Every descriptor
    behind it is neutral: colouring "BULLISH" green would read as an opinion the
    page has not formed."""
    if not first:
        return "muted"
    return "pos" if tag == "CREDIT" else "warn"


def matrix_note_text(pnl_data, basis):
    """The P&L MATRIX frame's right-hand note.

    Names the percentage basis in words, because ``% MAX`` fits a column heading
    and does not explain itself anywhere else on the screen."""
    heading = (basis or {}).get("heading") or MATRIX_BASIS_NONE["heading"]
    words = {MATRIX_BASIS_MAX["heading"]: "% of max return",
             MATRIX_BASIS_COST["heading"]: "% of cost"}.get(heading,
                                                            "no percentage basis")
    return f"PRICE × DATE · {len(pnl_data or [])} ROWS · {words.upper()}"


def compact_money(v, signed=False):
    """A dollar figure for the ③ LEGS strip, or an em-dash for no reading.

    K/M suffixes past 100k. The strip carries three readings inside a 424 px
    frame, and ``max_loss_estimate`` returns REAL large figures — a naked put on
    a 660 strike is 65,700, and one on $NDX is millions — so an unabbreviated
    figure pushes the other two readings out of the frame rather than merely
    looking wide."""
    f = _finite(v)
    if f is None:
        return "—"
    sign = "-" if f < 0 else ("+" if signed else "")
    a = abs(f)
    if a >= 1_000_000:
        return f"{sign}${a / 1e6:,.2f}M"
    if a >= 100_000:
        return f"{sign}${a / 1e3:,.0f}K"
    return f"{sign}${a:,.0f}"


def leg_strip_facts(legs):
    """The ③ LEGS frame's header strip: ``{count, net, net_tone, max_loss}``.

    ⚠ Both figures are ``None`` in ordinary use and must read as em-dashes:
    ``net_premium`` while ANY leg is unpriced (which is every fresh template,
    since ``build_default_legs`` sets ``premium: None``), and
    ``max_loss_estimate`` when the loss is unbounded or undecidable. ``NET $0``
    over an unpriced structure would state a figure the page does not have.
    """
    n = len(legs or [])
    net = net_premium(legs)
    tone = "dim" if net is None else ("pos" if net >= 0 else "neg")
    return {"count": f"{n} LEG" if n == 1 else f"{n} LEGS",
            "net": f"NET {compact_money(net, signed=True)}",
            "net_tone": tone,
            "max_loss": f"MAX LOSS {compact_money(max_loss_estimate(legs))}"}


# ── the six metric cards ─────────────────────────────────────────────────────
# The accent vocabulary is a FINITE set the page maps onto CALC_EDGE_* / CALC_*
# classes — the documented alternative to a runtime-built colour. ``dim`` is the
# no-reading tone: a card with nothing in it makes no colour claim either.
METRIC_ACCENTS = ("pos", "neg", "accent", "warn", "dim")

_EM_DASH = "—"


def _dollars(v):
    """A summary dollar figure as text.

    ``"Unlimited"`` for the service's uncapped sentinel — never ``$999,999`` —
    and an em-dash for no reading. The SIGN is kept: on the generic numeric
    path ``max_profit`` is ``max(pnl)`` over the service's own price grid and
    can be negative (a structure that loses everywhere), and rendering that as a
    positive figure would invert the reading."""
    if is_unlimited(v):
        return "Unlimited"
    f = _finite(v)
    if f is None:
        return _EM_DASH
    return f"-${abs(f):,.0f}" if f < 0 else f"${f:,.0f}"


def max_dte_from_legs(legs, today=None):
    """Calendar days from ``today`` to the LAST leg's expiry, or ``None``.

    The result payload carries ``eval_labels`` (MM/DD strings with no year) and
    no horizon of its own, so the metric cards' "at Nd expiry" and their per-day
    return are dated from the legs the compute was enqueued with — which is
    where ``calc_compute`` takes its own horizon from too. A leg with no
    parseable expiry contributes nothing; NO leg with one yields ``None``, so
    the cards render an em-dash rather than a guessed horizon. Floored at 0: an
    expiry already past is today's expiry, not a negative number of days."""
    today = today or dt.date.today()
    days = []
    for leg in legs or []:
        try:
            days.append((dt.date.fromisoformat(str(leg.get("expiry"))) - today).days)
        except (TypeError, ValueError):
            continue
    return max(max(days), 0) if days else None


def _position_note(legs):
    """"N contracts · M legs" for the ENTRY card, or an em-dash for no legs.

    ``N`` is the SMALLEST leg quantity, which is the position size rather than
    the biggest leg's ratio. ``_scale_leg_qty`` takes the page's Contracts count
    onto the legs by multiplying the whole set — a 1-2-1 butterfly at Contracts
    3 becomes 3-6-3 — and every template's smallest leg is 1, so the minimum
    reads the Contracts value back exactly. The maximum reported "6 contracts"
    beside a CONTRACTS field showing 3, and this note is the ONLY place the
    position size appears, so nothing on screen corroborated it.

    Taken from the LEGS and not from the Contracts widget on purpose: the legs
    are what the ENTRY CREDIT above it was priced from, and the widget goes
    stale the moment a leg qty is edited by hand."""
    rows = list(legs or [])
    if not rows:
        return _EM_DASH
    qty = min(int(_leg_qty(l) or 1) for l in rows)
    n = len(rows)
    return (f"{qty} contract{'' if qty == 1 else 's'} · "
            f"{n} leg{'' if n == 1 else 's'}")


def metric_cards(summary, legs, spot, max_dte):
    """The six results cards as ``{label, value, sub, accent}`` dicts.

    Order is the design's: ENTRY CREDIT/DEBIT · MAX RISK · MAX RETURN · RETURN
    ON RISK · BREAKEVEN(S) · PROB OF PROFIT. Always six, so the grid never
    reflows between renders.

    Every value degrades to an em-dash rather than to a zero. ``{}`` is "not
    calculated yet", and a screen of ``$0`` / ``0.0%`` reads as a measured
    result — the failure mode this whole redesign is written against.

    Three arguments beyond the summary, each load-bearing: ``legs`` sizes the
    ENTRY card's sub-line (contracts × legs — the only place the position size
    is stated), ``spot`` is what the breakeven distance is measured FROM, and
    ``max_dte`` dates the horizon (see ``max_dte_from_legs`` — the payload
    carries none).
    """
    s = summary or {}
    mp, ml = s.get("max_profit"), s.get("max_loss")

    # 1 — ENTRY CREDIT / DEBIT. The sign picks the label, so the figure is
    # unsigned; with no reading the label stays CREDIT but claims nothing.
    credit = _finite(s.get("entry_credit"))
    if credit is None:
        entry = {"label": "ENTRY CREDIT", "value": _EM_DASH, "sub": _EM_DASH,
                 "accent": "dim"}
    else:
        entry = {"label": "ENTRY CREDIT" if credit >= 0 else "ENTRY DEBIT",
                 "value": _dollars(abs(credit)), "sub": _position_note(legs),
                 "accent": "pos" if credit >= 0 else "accent"}

    # 2 — MAX RISK. A ZERO is refused as hard as a missing figure: on the
    # generic numeric path max_loss is |min(pnl)| over a grid spanning only
    # [0.5x, 1.5x] spot, floored at 0 — so 0.0 means "the grid never reached the
    # loss", not "there is no risk". Live: a far-OTM short put returns
    # max_loss 0.0 while the ③ LEGS strip, which solves the payoff exactly,
    # reads $29,965 for the same legs. "$0" in red under "worst case at expiry"
    # is the most dangerous number this page could print.
    zero_risk = _finite(ml) == 0.0
    risk_val = _EM_DASH if zero_risk else _dollars(ml)
    no_risk_reading = risk_val == _EM_DASH
    risk = {"label": "MAX RISK", "value": risk_val,
            "sub": (_EM_DASH if no_risk_reading else
                    ("unbounded above the short strike" if is_unlimited(ml)
                     else "worst case at expiry")),
            "accent": "dim" if no_risk_reading else "neg"}

    # 3 — MAX RETURN. ``profit_uncapped_above`` overrides the summary for a
    # net-long-call structure the generic path capped at its own grid edge.
    uncapped_up = is_unlimited(mp) or profit_uncapped_above(legs)
    ret_val = "Unlimited" if uncapped_up else _dollars(mp)
    if uncapped_up:
        ret_sub = "no upside cap"
    elif _finite(max_dte) is None:
        ret_sub = "at expiry"
    else:
        ret_sub = f"at {int(max_dte)}d expiry"
    ret = {"label": "MAX RETURN", "value": ret_val, "sub": ret_sub,
           "accent": "dim" if ret_val == _EM_DASH else "pos"}

    # 4 — RETURN ON RISK. There is no ratio when either side is uncapped, and
    # the service already sends 0.0 in that case — which would read as a
    # measured zero return rather than as "not defined". A ZERO max_loss is the
    # third such case (``calc_summary``'s own guard is ``max_loss in (0,
    # UNLIMITED) or max_profit == UNLIMITED``, and the generic path's
    # ``if max_loss > 0 else 0.0`` does the same) — the card was printing that
    # 0.0 as "0.0% / 0.00% per day". An uncapped upside kills it too: the
    # 7804.8% a grid edge over a debit produces has no numerator.
    ror = _finite(s.get("return_on_risk"))
    if ror is None or uncapped_up or zero_risk or is_unlimited(ml):
        ror_card = {"label": "RETURN ON RISK", "value": _EM_DASH, "sub": _EM_DASH,
                    "accent": "dim"}
    else:
        dte = _finite(max_dte)
        ror_card = {
            "label": "RETURN ON RISK", "value": f"{ror:.1f}%",
            # max(dte, 1): a 0-DTE structure earns its whole return today, so
            # the per-day figure IS the return — not a division by zero.
            "sub": (f"{ror / max(dte, 1.0):.2f}% per day" if dte is not None
                    else _EM_DASH),
            "accent": "warn"}

    # 5 — BREAKEVEN(S), with the first crossing's distance from spot.
    bes = [_finite(b) for b in (s.get("breakevens") or [])]
    bes = [b for b in bes if b is not None]
    sp = _finite(spot)
    if not bes:
        be = {"label": "BREAKEVEN(S)", "value": _EM_DASH,
              "sub": "no crossing in range", "accent": "dim"}
    else:
        be = {"label": "BREAKEVEN(S)",
              "value": " / ".join(f"{b:,.2f}" for b in bes),
              "sub": (f"{(bes[0] / sp - 1) * 100:+.2f}% from spot"
                      if sp else _EM_DASH),
              "accent": "accent"}

    # 6 — PROB OF PROFIT. Risk-neutral lognormal with drift r (the service's
    # ``_estimate_pop``); naming the model is what stops it reading as a forecast.
    pop = _finite(s.get("pop"))
    if pop is None:
        pop_card = {"label": "PROB OF PROFIT", "value": _EM_DASH, "sub": _EM_DASH,
                    "accent": "dim"}
    else:
        pop_card = {"label": "PROB OF PROFIT", "value": f"{pop:.1f}%",
                    "sub": "lognormal · risk-neutral drift",
                    "accent": "pos" if pop >= 60 else ("warn" if pop >= 45 else "neg")}

    return [entry, risk, ret, ror_card, be, pop_card]


def chain_expiries(chain):
    """Sorted unique expiry strings (YYYY-MM-DD) from an option-chain payload."""
    out = set()
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key in (chain or {}).get(map_key) or {}:
            out.add(exp_key.split(":")[0])
    return sorted(out)


def chain_strikes(chain, expiry, option_type):
    """Sorted strikes for one expiry + option_type (call/put)."""
    map_key = "callExpDateMap" if option_type == "call" else "putExpDateMap"
    out = set()
    for exp_key, strikes in ((chain or {}).get(map_key) or {}).items():
        if exp_key.split(":")[0] != str(expiry):
            continue
        for s in strikes or {}:
            try:
                out.add(float(s))
            except (ValueError, TypeError):
                continue
    return sorted(out)


def _has_contracts(chain):
    return bool(chain and (chain.get("callExpDateMap") or chain.get("putExpDateMap")))


# Scroll the P&L grid so the current-spot row sits in the MIDDLE of the scroll
# viewport (the grid is spot-centered but opens scrolled to the top, hiding spot).
# Scrolls only the grid container, never the page.
_CENTER_SPOT_JS = """
(() => {
  const sc = document.getElementById('calc-grid-scroll');
  const row = document.getElementById('calc-spot-row');
  if (!sc || !row) return;
  const sr = sc.getBoundingClientRect(), rr = row.getBoundingClientRect();
  sc.scrollTop += (rr.top - sr.top) - (sc.clientHeight / 2) + (rr.height / 2);
})()
"""


# ── the page's own class vocabulary ──────────────────────────────────────────
# The [calc] token set covers the surfaces this screen shares with itself; the
# handful below are one-off geometries of THIS page (a rule under the title, a
# 2 px scan track, a dashed placeholder, the frames' label chip) that no other
# screen has and that would only bloat the shared vocabulary. They are built
# from the SAME ``config/theme.toml`` [calc] colours, so the page still follows
# the palette knob-for-knob.
_C = THEME["calc"]

_TITLE_TEXT = f"text-[{_C['bright']}]"
_TITLE_RULE = f"border-b border-b-[{_C['edge_idle']}]"
_STRIP_GROUND = f"bg-[{_C['chip_bg']}] px-1.5"          # a chip row over the border
_SCAN_TRACK = f"bg-[{_C['tile_a']}]"
_EMPTY_PANEL = (f"border border-dashed border-[{_C['edge_idle']}] "
                f"bg-[{_C['frame_b']}] rounded-[3px]")

# The numbered frames' label chip. Active is the design's muted cyan — a lighter
# step of the [calc] `accent` family that the shared vocabulary has no knob for;
# idle is the configured `label`. Two states, two STATIC classes.
_CHIP_ON = "text-[#8fc6d6]"
_CHIP_TEXT = {"on": _CHIP_ON, "off": f"text-[{_C['label']}]"}
_CHIP_SWAP = " ".join(_CHIP_TEXT.values())

# The title-bar status pill. It carries ONE colour class and paints its dot and
# its border from it via `bg-current` / `border-current`, so the three parts can
# never drift apart.
_PILL_TEXT = {"ready": CALC_POS, "loading": CALC_WARN, "idle": CALC_MUTED}
_PILL_SWAP = " ".join(dict.fromkeys(_PILL_TEXT.values()))

# tone -> class, for the ③ LEGS strip, the SPOT readout and the ① STRATEGY tag
# chips. ``_TONE_SWAP`` is the whole set as one string, for the documented
# ``.classes(remove=…, add=…)`` swap that stops repeated repaints stacking
# conflicting text-[…] classes. It extends the theme's own ``CALC_STATE_TEXT``
# rather than restating it, so it follows the [calc] config for free — ``muted``
# is the one tone this page needs that the shared state set does not carry.
_TONE_TEXT = {"pos": CALC_POS, "neg": CALC_NEG, "accent": CALC_ACCENT,
              "warn": CALC_WARN, "dim": CALC_DIM, "muted": CALC_MUTED}
_TONE_SWAP = " ".join(dict.fromkeys(CALC_STATE_TEXT.split() + [CALC_MUTED]))

# metric-card accent -> its left edge + its value colour. Every key of
# ``METRIC_ACCENTS`` has an entry: a missing one would raise mid-render.
_METRIC_EDGE = {"pos": CALC_EDGE_POS, "neg": CALC_EDGE_NEG,
                "accent": CALC_EDGE_ACCENT, "warn": CALC_EDGE_WARN,
                "dim": f"border-l-2 border-l-[{_C['dim']}]"}

# The shared leg editor's card palette, repainted in [calc]. The GEOMETRY is the
# editor's; only the colours enter from here, which is how the Simulator keeps
# the app-wide navy while mounting the same card.
_LEG_TOKENS = {
    "frame": f"border border-[{_C['edge_idle']}] rounded-[2px] bg-[{_C['frame_b']}]",
    "eyebrow": (f"text-[8px] tracking-[.14em] text-[{_C['label']}] "
                f"whitespace-nowrap truncate"),
    # Side -> accent, the design's cyan long / green short.
    "accent_long": f"border-l-2 border-l-[{_C['accent']}]",
    "accent_short": f"border-l-2 border-l-[{_C['pos']}]",
    "num": f"text-[10px] text-[{_C['label']}]",
    "delta": f"text-[11px] text-[{_C['txt']}] whitespace-nowrap",
    "remove": (f"text-[10px] text-[{_C['btn_txt']}] border "
               f"border-[{_C['btn_edge']}] rounded-[2px]"),
    "remove_off": (f"text-[10px] text-[{_C['off_txt']}] border "
                   f"border-[{_C['off_edge']}] rounded-[2px] cursor-not-allowed"),
    "add": (f"text-[9px] tracking-[.18em] text-[{_C['accent_txt']}] border "
            f"border-dashed border-[{_C['btn_edge']}] rounded-[2px]"),
    "reset": (f"text-[9px] tracking-[.18em] text-[{_C['muted']}] border "
              f"border-[{_C['off_edge']}] rounded-[2px]"),
}


def _render_metrics(box, summary, legs, spot, max_dte):
    """Paint the six metric cards into ``box`` (a CSS grid)."""
    from nicegui import ui

    box.clear()
    with box:
        for card in metric_cards(summary, legs, spot, max_dte):
            accent = card["accent"]
            with ui.column().classes(f"{CALC_TILE} {_METRIC_EDGE[accent]} "
                                     f"min-w-0 gap-1.5 px-3 pt-2.5 pb-3"):
                ui.label(card["label"]).classes(CALC_EYEBROW)
                ui.label(card["value"]).classes(
                    f"text-[17px] font-bold truncate {_TONE_TEXT[accent]}")
                ui.label(card["sub"]).classes(
                    f"{CALC_DIM} text-[9px] leading-snug break-words")


def matrix_html(eval_labels, pnl_data, spot, summary, legs=None):
    """The P&L matrix as ONE raw HTML fragment ("" when there is nothing to draw).

    Raw HTML rather than NiceGUI components on purpose — a few hundred cells
    built with ``.classes()`` would be a few hundred Vue elements — which is the
    repo's documented out-of-scope case for the Tailwind-first rule, so the
    inline ``style=`` attributes below are the intended form here.

    ``summary`` is the service's summary payload and ``legs`` the position it
    was computed from; the ``%`` column's basis is resolved from the pair ONCE
    here, so every cell and the heading above them share one meaning.
    """
    basis = matrix_basis(summary, legs)
    rows = grid_rows(pnl_data)
    # ``eval_labels`` arrive pre-formatted (MM/DD strings) from the service;
    # ``eval_date_labels`` is harmless here (it str()'s strings) and keeps the
    # page robust if date objects are ever passed.
    headers = matrix_headers(eval_date_labels(eval_labels), basis)
    if not rows or not headers:
        return ""
    g_max, g_min = grid_extremes(pnl_data)
    # No spot, no marked row. ``spot`` degrades to 0.0 when the chain carries no
    # underlying price (index chains read hollow off-hours), and the nearest row
    # to zero is the LOWEST price on the ladder — which the amber rule would
    # then present as today's price.
    sp = _finite(spot)
    spot_idx = (None if sp is None or sp <= 0 else
                min(range(len(rows)), key=lambda i: abs((rows[i]["price"] or 0) - sp)))

    head_style = "text-align:right;padding:6px 10px;font-size:9px;letter-spacing:.18em;"
    ths = [f'<th style="position:sticky;left:0;top:0;z-index:3;'
           f'background:{MATRIX_HEAD_BG};border-bottom:1px solid {MATRIX_HEAD_RULE};'
           f'color:{MATRIX_LABEL_FG};{head_style}">PRICE</th>']
    for h in headers:
        fg = MATRIX_SPOT if h["expiry"] else MATRIX_LABEL_FG
        cell = (f'position:sticky;top:0;z-index:2;background:{MATRIX_HEAD_BG};'
                f'border-bottom:1px solid {MATRIX_HEAD_RULE};color:{fg};{head_style}')
        ths.append(f'<th style="{cell}">{h["label"]}</th>'
                   f'<th style="{cell}">{h["pct_label"]}</th>')

    trs = []
    for i, r in enumerate(rows):
        at_spot = i == spot_idx
        rule = MATRIX_SPOT if at_spot else MATRIX_ROW_RULE
        row_style = (f'border-top:1px solid {rule};text-align:right;padding:3px 10px;'
                     + (f'border-bottom:1px solid {rule};' if at_spot else ''))
        price = r["price"] if isinstance(r["price"], (int, float)) else 0.0
        tds = [f'<td style="position:sticky;left:0;z-index:1;'
               f'background:{MATRIX_SPOT if at_spot else MATRIX_HEAD_BG};'
               f'color:{MATRIX_VOID if at_spot else MATRIX_PRICE_FG};'
               f'font-weight:600;'
               f'{row_style}">{price:,.2f}</td>']
        for c in r["cells"]:
            fact = matrix_cell_facts(c["pnl"], basis, g_max, g_min)
            cell = f'background:{fact["bg"]};color:{fact["fg"]};{row_style}'
            tds.append(f'<td style="{cell}">{fact["dollars"]}</td>'
                       f'<td style="{cell}">{fact["pct"]}</td>')
        tr = '<tr id="calc-spot-row">' if at_spot else "<tr>"
        trs.append(tr + "".join(tds) + "</tr>")

    return (f'<div id="calc-grid-scroll" style="max-height:480px;overflow:auto;'
            f'border:1px solid {MATRIX_HEAD_RULE};border-radius:3px;'
            f'background:{MATRIX_VOID};">'
            f'<table style="border-collapse:collapse;font-size:11px;'
            f"font-family:'JetBrains Mono',ui-monospace,monospace;"
            f'font-variant-numeric:tabular-nums;">'
            f'<thead><tr>{"".join(ths)}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def _render_grid(box, eval_labels, pnl_data, spot, summary=None, legs=None):
    from nicegui import ui

    box.clear()
    html = matrix_html(eval_labels, pnl_data, spot, summary, legs)
    with box:
        if not html:
            ui.label("No P&L data.").classes("opacity-60")
            return
        ui.html(html).classes("w-full")
        # Centre the spot row in the viewport once the DOM has painted.
        ui.timer(0.12, lambda: ui.run_javascript(_CENTER_SPOT_JS), once=True)


def strikes_window(strikes, spot, n):
    """The P&L grid's price rows: the ``n`` strikes ≤ spot plus the ``n`` strikes
    > spot (strictly ±n around spot — far-OTM legs beyond it fall off the grid).

    Junk / non-numeric strikes are ignored and duplicates collapsed."""
    xs = sorted({float(s) for s in (strikes or []) if isinstance(s, (int, float))})
    if not xs or spot is None:
        return []
    below = [s for s in xs if s <= spot][-n:]
    above = [s for s in xs if s > spot][:n]
    return below + above


def render():
    """Build the Calculator page: the three numbered steps + the results column.

    ① STRATEGY · ③ LEGS and the action grid fill a fixed 424 px input column;
    ② SYMBOL, the six metric cards and the P&L matrix fill the results column
    beside it.

    No engine call here — ``load`` enqueues ``calc_load`` and ``Calculate``
    enqueues ``calc_compute``; version-polls on the two cache views paint the
    chain selectors and the metrics/matrix. IV + Fetch premiums run the pure
    chain-extractors LOCALLY on the cached chain dict."""
    from nicegui import ui, run

    import bus_client

    from pages.ui_guard import guard, guard_async

    from . import handoff
    from . import leg_editor
    from . import strategies as S
    from . import strategy_menu
    from . import overlay as _overlay

    # This page's own language (.calc-v3), never the app-wide navy scope the
    # Simulator and Trade share (see the module header for which that is).
    # ``add_head_html`` during a page build is client-scoped, so the mono face is
    # requested here and on no other route.
    if CALC_FONT_HEAD_HTML:
        ui.add_head_html(CALC_FONT_HEAD_HTML)
    ui.add_css(CALC_CSS + CALC_KEYFRAMES_CSS)

    # Full-screen wait overlay shown while a user-initiated Load is in flight.
    wait = _overlay.build_loading_overlay()

    # Page state (local closure, not module globals — built per request).
    state = {
        "chain": None,        # last calc_load chain dict (pure-extracted locally)
        "result": None,       # last calc_result payload (summary/labels/grid)
        "chain_ver": None,    # last-seen calc_chain cache version
        "result_ver": None,   # last-seen calc_result cache version
        "iv_ver": None,       # last-seen calc_iv cache version
        "calc_spot": None,    # spot used for the last enqueued compute (grid marker)
        "calc_dte": None,     # horizon of the last enqueued compute (metric cards)
        "calc_symbol": None,  # symbol the on-screen result belongs to (stale check)
        "spot": None,         # last loaded chain price (the ② SYMBOL readout)
        "pending_legs": None,  # legs copied in from the Simulator, applied on chain load
        "contracts": 1,       # last-applied Contracts count (drives per-leg qty scaling)
        "restoring": False,   # True while restoring a persisted snapshot (suppress enqueues)
        "last_loaded": None,   # last symbol a Load was triggered for (tab/Enter dedup)
        "loading": False,      # True while a user-initiated load is in flight (overlay up)
        "applying": False,     # True while _apply_chain/_prefill set Expiry programmatically
        "chain_fetching": False,  # in-flight guard for the off-loop big-chain read
    }

    # ── the numbered-frame helper ────────────────────────────────────────────
    def _frame(chip_text, *, note=False, gap="gap-3"):
        """A numbered frame: the label chip sits ON the border line.

        Pure Tailwind — a ``relative`` frame plus an ``absolute -top-1.5`` chip
        painted in the page's own ground, which is what interrupts the border
        rather than a notch. The chip row is absolutely positioned, so it is not
        a flex item and the frame's gap never applies to it."""
        box = ui.column().classes(f"{CALC_FRAME} w-full min-w-0 {gap} px-3 pt-5 pb-3")
        with box:
            row = ui.row().classes("absolute -top-1.5 left-3 right-3 items-center "
                                   "justify-between gap-2.5 min-w-0")
            with row:
                chip = ui.label(chip_text).classes(f"{CALC_CHIP} {_CHIP_ON} shrink-0")
                note_lbl = None
                if note:
                    note_lbl = ui.label("").classes(
                        f"{_STRIP_GROUND} {CALC_MUTED} text-[8px] tracking-[.16em] "
                        f"whitespace-nowrap truncate min-w-0")
        return SimpleNamespace(box=box, row=row, chip=chip, note=note_lbl)

    def _cell(caption, basis):
        """A captioned ② SYMBOL cell — the eyebrow over its own control."""
        col = ui.column().classes(f"gap-1 min-w-0 {basis}")
        with col:
            ui.label(caption).classes(f"{CALC_EYEBROW} truncate")
        return col

    # ── the three-step layout (page-scoped, .calc-v3) ────────────────────────
    # LEFT  = the 424 px input column: ① STRATEGY, ③ LEGS, the action grid.
    # RIGHT = ② SYMBOL over the six metric cards over the P&L matrix; min-w-0 so
    #         the matrix h-scrolls inside its frame instead of pushing the inputs.
    with ui.column().classes(f"calc-v3 {CALC_PAGE} {CALC_MONO} w-full gap-[15px]"):
        # TITLE BAR — the name, and the live chain-status pill.
        with ui.row().classes(f"w-full items-center justify-between gap-2.5 "
                              f"pb-2.5 {_TITLE_RULE}"):
            ui.label("STRATEGY CALCULATOR").classes(
                f"text-[14px] font-bold tracking-[.13em] {_TITLE_TEXT} whitespace-nowrap")
            # ONE colour class on the pill; the dot and the border take it from
            # `currentColor`, so the three parts cannot drift apart.
            status_pill = ui.row().classes(
                f"items-center gap-2 px-[11px] py-1 border border-current "
                f"rounded-[2px] shrink-0 {_PILL_TEXT['idle']}")
            with status_pill:
                ui.element("div").classes(
                    "w-1.5 h-1.5 rounded-full bg-current shrink-0 "
                    "animate-[blip_1.6s_ease-in-out_infinite]")
                status_lbl = ui.label("AWAITING SYMBOL").classes(
                    "text-[9px] tracking-[.2em] whitespace-nowrap")

        with ui.row().classes("w-full items-start gap-[18px] no-wrap"):
            # ── LEFT: the 424 px input column ────────────────────────────────
            with ui.column().classes("shrink-0 grow-0 w-[424px] min-w-[380px] gap-[15px]"):
                # ① STRATEGY
                strat_frame = _frame("① STRATEGY", gap="gap-3")
                with strat_frame.box:
                    strategy_sel = strategy_menu.build_strategy_menu(
                        value="PCS", classes="w-full", boxed=True, caption=False,
                        btn_class=CALC_STRATEGY_BTN, menu_class="strat-menu-calc")
                    tags_box = ui.row().classes("flex-wrap gap-2 w-full")
                    blurb_lbl = ui.label("").classes(
                        f"{CALC_BODY} text-[11px] leading-relaxed w-full")

                # ③ LEGS — the chip row carries the live count / net / max-loss strip.
                legs_frame = _frame("③ LEGS", gap="gap-2")
                with legs_frame.row:
                    with ui.row().classes("items-center gap-2 min-w-0 shrink "
                                          f"overflow-hidden {_STRIP_GROUND}"):
                        legcount_lbl = ui.label("").classes(
                            f"{CALC_MUTED} text-[8px] tracking-[.14em] whitespace-nowrap")
                        net_lbl = ui.label("").classes(
                            f"{CALC_DIM} text-[8px] tracking-[.14em] whitespace-nowrap")
                        maxloss_lbl = ui.label("").classes(
                            f"{CALC_WARN} text-[8px] tracking-[.14em] truncate min-w-0")
                with legs_frame.box:
                    leg_box = ui.column().classes("gap-2 w-full min-w-0")

                # ACTIONS — two columns, with the status line spanning both.
                with ui.element("div").classes("grid grid-cols-2 gap-2 w-full"):
                    ui.button("FETCH PREMIUMS", color=None,
                              on_click=lambda: fetch_premiums()) \
                        .props("no-caps unelevated").classes(f"{CALC_BTN} w-full h-[34px]") \
                        .tooltip("Fill leg premiums from the chain")
                    ui.button("CALCULATE", color=None, on_click=lambda: do_calc()) \
                        .props("no-caps unelevated") \
                        .classes(f"{CALC_BTN_PRIMARY} w-full h-[34px]")
                    ui.button("EXPECTED MOVE", color=None, on_click=lambda: send_to_em()) \
                        .props("no-caps unelevated").classes(f"{CALC_BTN} w-full h-[34px]") \
                        .tooltip("Chart the expected move for these legs")
                    ui.button("COPY TO SIMULATOR", color=None,
                              on_click=lambda: handoff.send_to_simulator(
                                  leg_editor.legs_to_payload(
                                      (symbol_in.value or "").replace("$", "").upper(),
                                      editor.get_legs(), keep_premium=False))) \
                        .props("no-caps unelevated").classes(f"{CALC_BTN} w-full h-[34px]") \
                        .tooltip("Open these legs in the Simulator")
                    action_lbl = ui.label("").classes(
                        f"col-span-2 {CALC_MUTED} text-[9px] tracking-[.14em]")

            # ── RIGHT: the results column ────────────────────────────────────
            with ui.column().classes("flex-1 min-w-0 gap-[13px]"):
                # ② SYMBOL — the market readout. Three rows rather than one
                # wrapping row: nine cells plus two buttons cannot fit one line
                # at any realistic width, and an explicit break lands the wrap
                # where the design draws it instead of wherever the browser does.
                sym_frame = _frame("② SYMBOL", note=True, gap="gap-2.5")
                with sym_frame.box:
                    with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                        with _cell("TICKER", "shrink-0 grow-0 basis-[118px]"):
                            symbol_in = select_all_on_focus(
                                ui.input(value="SPY").classes("w-full")
                                .props('spellcheck=false input-class="!text-[16px] '
                                       '!font-bold !tracking-[.12em] uppercase"'))
                        with _cell("SPOT", "shrink-0 grow-0 basis-[120px]"):
                            spot_lbl = ui.label("———").classes(
                                f"text-[16px] font-medium truncate {CALC_DIM}")
                        with _cell("PRICE", "flex-[1_1_88px] max-w-[132px]"):
                            price_in = ui.number(value=100.0, format="%.2f").classes("w-full")
                        with _cell("IV %", "flex-[1_1_88px] max-w-[132px]"):
                            iv_in = ui.number(value=20.0, format="%.1f").classes("w-full")
                        with _cell("RATE %", "flex-[1_1_88px] max-w-[132px]"):
                            rate_in = ui.number(value=4.5, format="%.2f").classes("w-full")
                        with _cell("IV Δ %", "flex-[1_1_88px] max-w-[132px]"):
                            ivchg_in = ui.number(value=0.0, format="%.1f").classes("w-full")
                    with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                        with _cell("CONTRACTS", "flex-[1_1_88px] max-w-[132px]"):
                            contracts_in = ui.number(value=1, min=1, max=100,
                                                     format="%.0f").classes("w-full")
                        with _cell("STRIKES", "flex-[1_1_88px] max-w-[132px]"):
                            # The P&L grid spans ±N real chain strikes around spot.
                            nstrikes_in = ui.number(value=24, min=1, max=200,
                                                    format="%.0f").classes("w-full") \
                                .tooltip("Strikes shown either side of spot in the P&L grid")
                        with _cell("EXPIRY", "flex-[1_1_150px] max-w-[200px]"):
                            # Kept against the mock, which has per-leg expiry only:
                            # this drives calc_compute's ``expiry`` argument and the
                            # apply_expiry propagation onto every leg.
                            expiry_sel = ui.select([]).classes("w-full")
                    with ui.row().classes("w-full items-center gap-2.5 flex-wrap"):
                        ui.button("LOAD CHAIN", color=None,
                                  on_click=lambda: load_symbol(show_wait=True)) \
                            .props("no-caps unelevated").classes(f"{CALC_BTN} px-3") \
                            .tooltip("Load price + expiries/strikes")
                        ui.button("IV UPDATE", color=None, on_click=lambda: fetch_iv()) \
                            .props("no-caps unelevated").classes(f"{CALC_BTN} px-3") \
                            .tooltip("Fetch / imply IV for the expiry")
                        with ui.element("div").classes(
                                f"relative shrink-0 w-[84px] h-0.5 overflow-hidden "
                                f"{_SCAN_TRACK}"):
                            scan_bar = ui.element("div").classes(
                                f"absolute inset-y-0 left-0 w-[34%] {CALC_ACCENT} "
                                f"bg-gradient-to-r from-transparent via-current "
                                f"to-transparent animate-[scan_1.1s_linear_infinite]")
                        chain_lbl = ui.label("").classes(
                            f"{CALC_MUTED} text-[9px] tracking-[.12em] truncate "
                            f"min-w-0 flex-1")

                # The dashed placeholder, and the two panels it stands in for.
                empty_panel = ui.column().classes(
                    f"w-full items-center justify-center gap-2.5 min-h-[420px] "
                    f"{_EMPTY_PANEL}")
                with empty_panel:
                    empty_lbl = ui.label("").classes(
                        f"{CALC_BODY} text-[12px] tracking-[.22em] whitespace-nowrap")
                    empty_hint = ui.label("").classes(
                        f"{CALC_DIM} text-[10px] leading-relaxed text-center max-w-[420px]")

                metrics_box = ui.element("div").classes(
                    "grid grid-cols-[repeat(auto-fit,minmax(148px,1fr))] gap-2.5 w-full")
                matrix_frame = _frame("P&L MATRIX", note=True, gap="gap-0")
                with matrix_frame.box:
                    grid_box = ui.column().classes("w-full min-w-0")

    # ── editable multi-leg editor (shared with the Simulator) ────────────────
    # Strike/expiry options come from the cached chain; the editor owns the legs
    # (add/remove/edit) and tracks a ``dirty`` flag (any manual edit ⇒ the summary
    # routes through the generic numeric path with strategy="CUSTOM").
    def _strikes_for(expiry, otype):
        chain = state.get("chain") or {}
        if expiry:
            return chain_strikes(chain, expiry, otype)
        # Union across expiries — used by apply_template before a per-leg expiry
        # is set, and by the editor's pre-load empty state.
        out = set()
        for e in chain_expiries(chain):
            out.update(chain_strikes(chain, e, otype))
        return sorted(out)

    def _expiries_for():
        return chain_expiries(state.get("chain") or {})

    def _delta_for(leg):
        """The leg card's DELTA cell, against the currently cached chain.

        A one-line binding of the pure ``leg_delta`` — which is where the rules
        (and the reason there is no cross-expiry fallback) are documented, and
        where they can be tested without building a page."""
        return leg_delta(state.get("chain"), leg)

    editor = leg_editor.build_leg_editor(
        leg_box, strikes_for=_strikes_for, expiries_for=_expiries_for,
        show_premium=True, on_change=lambda: (_capture(), _sync_legs()),
        spot_getter=lambda: float(price_in.value or 0),
        layout="card", tokens=_LEG_TOKENS, delta_for=_delta_for,
        # A floor of ONE, not the mock's two. The mock locks at two because its
        # own buildLegs PADS a single-leg spec with a synthetic opposite leg;
        # this app does not pad, and ships four genuine single-leg templates
        # (LONG_CALL / LONG_PUT / NAKED_CALL / NAKED_PUT), so a two-leg floor
        # would make those unreachable by hand. One still holds: a zero-leg
        # calculator has nothing to price.
        min_legs=1, on_reset=lambda: _seed_template())

    def _scale_leg_qty(factor):
        """Multiply every leg's qty by ``factor`` (RATIO-preserving) and re-render —
        how the page-level Contracts count flows onto the legs (a 1-2-1 butterfly
        scales to 10-20-10, not flattened)."""
        if factor == 1:
            return
        legs = editor.get_legs()
        if not legs:
            return
        for leg in legs:
            leg["qty"] = max(1, round(int(leg.get("qty", 1) or 1) * factor))
        editor.set_legs(legs)
        _sync_legs()          # set_legs does not fire on_change

    def _seed_template():
        """Apply the selected template (legs = its ratios) then scale by the current
        Contracts so the legs reflect the position size from the start."""
        if state.get("restoring"):
            return
        editor.apply_template(strategy_sel.value)
        _scale_leg_qty(max(1, int(contracts_in.value or 1)))
        _sync_legs()

    # ── persist + restore full UI state across navigation (single-user) ───────
    def _capture():
        """Snapshot the current inputs into the module-level _LAST_CALC (cheap dict
        write; wired to every input change). No-op while restoring."""
        if state.get("restoring"):
            return
        _LAST_CALC.clear()
        _LAST_CALC.update(_ps.snapshot({
            "symbol": (symbol_in.value or "").strip().upper(),
            "strategy": strategy_sel.value, "legs": editor.get_legs(),
            "iv": iv_in.value, "rate": rate_in.value, "ivadj": ivchg_in.value,
            "contracts": int(contracts_in.value or 1), "price": price_in.value,
            "num_strikes": int(nstrikes_in.value or 24), "expiry": expiry_sel.value,
        }, _CALC_KEYS))

    def _restore(snap):
        """Apply a persisted snapshot to the widgets under the restoring guard (so
        wiring fires no stray commands). Legs ride ``pending_legs`` so the post-load
        ``_apply_chain`` applies them (keeping their premiums) and recomputes."""
        s = _ps.merge_restore(snap, _CALC_DEFAULTS)
        state["restoring"] = True
        try:
            symbol_in.value = s["symbol"]
            strategy_sel.value = s["strategy"]
            iv_in.value = s["iv"]
            rate_in.value = s["rate"]
            ivchg_in.value = s["ivadj"]
            contracts_in.value = s["contracts"]
            state["contracts"] = s["contracts"]
            price_in.value = s["price"]
            nstrikes_in.value = s["num_strikes"]
            if s["expiry"]:
                expiry_sel.options = [s["expiry"]]
                expiry_sel.value = s["expiry"]
                expiry_sel.update()
            state["pending_legs"] = s["legs"] or None
        finally:
            state["restoring"] = False

    # ── display sync (pure facts in, widget text/classes out) ────────────────
    # Four repaints, each fed by one of the pure functions above so the screen
    # cannot state something the helpers did not compute. All are idempotent, so
    # calling one after any leg/chain change is always safe.
    @guard
    def _sync_strategy():
        """① STRATEGY — the tag chips and the one-line thesis for the picked code."""
        code = strategy_sel.value
        tags_box.clear()
        with tags_box:
            for i, tag in enumerate(S.strategy_tags(code)):
                ui.label(tag).classes(
                    f"px-2 py-0.5 rounded-[2px] border border-current text-[9px] "
                    f"tracking-[.14em] whitespace-nowrap "
                    f"{_TONE_TEXT[tag_tone(tag, first=i == 0)]}")
        blurb_lbl.text = S.strategy_blurb(code)

    @guard
    def _sync_legs():
        """③ LEGS — the count / net / max-loss strip, and the action-grid note."""
        legs = editor.get_legs()
        facts = leg_strip_facts(legs)
        legcount_lbl.text = facts["count"]
        net_lbl.text = facts["net"]
        net_lbl.classes(remove=_TONE_SWAP, add=_TONE_TEXT[facts["net_tone"]])
        maxloss_lbl.text = facts["max_loss"]
        ready = _has_contracts(state.get("chain"))
        if not ready:
            action_lbl.text = "load a chain before pricing"
        elif any(l.get("strike") is None for l in legs) or not legs:
            action_lbl.text = "pick every leg strike to calculate"
        else:
            action_lbl.text = "ready to calculate"

    @guard
    def _sync_results():
        """The results column — the panels, or the dashed placeholder naming which
        wait it is. ONE decision point, so the copy cannot be written twice."""
        status = chain_status_facts(state.get("loading"), symbol_in.value,
                                    state.get("chain"))
        facts = results_panel_facts(status, bool(state.get("result")))
        empty_panel.set_visibility(facts is not None)
        metrics_box.set_visibility(facts is None)
        matrix_frame.box.set_visibility(facts is None)
        if facts is not None:
            empty_lbl.text = facts["label"]
            empty_hint.text = facts["hint"]

    @guard
    def _sync_status():
        """The title-bar pill, the ②/③ frame accents, the scan bar and the hint."""
        status = chain_status_facts(state.get("loading"), symbol_in.value,
                                    state.get("chain"))
        phase = status["state"]
        status_pill.classes(remove=_PILL_SWAP, add=_PILL_TEXT[phase])
        status_lbl.text = status["label"]
        sym_frame.note.text = status["hint"]
        scan_bar.set_visibility(phase == "loading")
        spot = state.get("spot")
        spot_lbl.text = f"{spot:,.2f}" if isinstance(spot, (int, float)) else "———"
        spot_lbl.classes(remove=_TONE_SWAP,
                         add=_TONE_TEXT["pos" if spot else "dim"])
        exps = _expiries_for()
        strikes = set(_strikes_for(expiry_sel.value, "call")) \
            | set(_strikes_for(expiry_sel.value, "put"))
        chain_lbl.text = chain_line(status, symbol_in.value, len(exps), len(strikes))
        # An idle/loading page is not a loaded chain: the two frames that depend
        # on one drop to the muted edge until it lands.
        live = phase == "ready"
        for frame in (sym_frame, legs_frame):
            frame.box.classes(remove=CALC_FRAME_IDLE if live else CALC_FRAME,
                              add=CALC_FRAME if live else CALC_FRAME_IDLE)
            frame.chip.classes(remove=_CHIP_SWAP,
                               add=_CHIP_TEXT["on" if live else "off"])
        _sync_results()

    # Seed the default template (PCS). Tolerates empty strikes/expiries pre-load.
    _seed_template()
    strategy_sel.on_value_change(
        lambda e: None if state.get("restoring")
        else (_seed_template(), _capture(), _sync_strategy()))

    @guard
    def _on_contracts_change():
        """Contracts is the position-size multiplier: scale all legs by new/old so
        changing it from 1 → 10 takes every leg's qty up 10× (ratios preserved)."""
        if state.get("restoring"):
            return
        new = max(1, int(contracts_in.value or 1))
        old = state.get("contracts") or 1
        if new != old:
            _scale_leg_qty(new / old)
        state["contracts"] = new

    contracts_in.on_value_change(lambda e: (_on_contracts_change(), _capture()))
    # Persist the remaining inputs on change (cheap dict write; no command).
    for _w in (iv_in, rate_in, ivchg_in, price_in, nstrikes_in):
        _w.on_value_change(lambda e: _capture())
    # Symbol: tab-out / Enter simulate Load (deduped); value-change still persists.
    symbol_in.on_value_change(lambda e: _capture())
    symbol_in.on("keydown.enter", lambda e: _symbol_submit())
    symbol_in.on("focusout", lambda e: _symbol_submit())

    @guard
    def _on_expiry_change():
        """Top-level Expiry → propagate to ALL legs (re-syncs each leg's strikes).
        Suppressed while restoring or while _apply_chain/_prefill set it programmatically."""
        if state.get("restoring") or state.get("applying"):
            return
        editor.apply_expiry(expiry_sel.value)
        _capture()

    expiry_sel.on_value_change(lambda e: _on_expiry_change())

    @guard
    def fetch_premiums():
        """Fill leg premiums from the CACHED chain (pure ``extract_premium``).

        Each leg is priced at its OWN expiry (falling back to the primary Expiry
        select, then to a no-expiry strike match). The filled legs are written back
        via ``editor.set_legs`` so the premium fields repaint."""
        chain = state.get("chain")
        if chain is None:
            ui.notify("Load symbol first.", type="warning")
            return
        legs = editor.get_legs()
        if any(l.get("strike") is None for l in legs):
            ui.notify("Pick all leg strikes first.", type="warning")
            return
        filled, missing = 0, []
        for leg in legs:
            strike = float(leg["strike"])
            leg_exp = leg.get("expiry") or expiry_sel.value
            prem = extract_premium(chain, leg["option_type"], strike, expiry=leg_exp)
            if prem is None:
                prem = extract_premium(chain, leg["option_type"], strike)
            if prem is not None:
                leg["premium"] = round(prem, 2)
                filled += 1
            else:
                missing.append(f"{leg['option_type']} @ {strike:g}")
        editor.set_legs(legs)
        _sync_legs()
        if filled:
            ui.notify(f"Filled {filled} premium(s).", type="positive")
        if missing:
            ui.notify("No premium for: " + ", ".join(missing), type="warning")

    @guard
    def load_symbol(show_wait=False):
        """Enqueue a ``calc_load`` for the symbol; the version-poll applies it.

        ``show_wait`` (user-initiated: Load button / symbol tab-out / Enter) shows the
        centered wait overlay until the chain arrives (or a safety timeout).
        Mount-time auto-loads (restore / handoff) pass show_wait=False."""
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            ui.notify("Enter a symbol first.", type="warning")
            return
        if show_wait and state.get("loading"):
            # Collapses the focusout-then-button-click double fire while a load is in
            # flight. (The Load button still force-reloads once loading clears — it
            # bypasses should_load, unlike the Trade page's button-through-dedup.)
            return
        # A result belongs to the symbol it was computed for. Loading a
        # DIFFERENT one would leave that symbol's cards and matrix on screen
        # under a pill announcing this one — the page stating two symbols at
        # once. Reloading the SAME symbol keeps them: that is a refresh, which
        # is also what the restore-on-navigation path does.
        if state.get("result") is not None and sym != state.get("calc_symbol"):
            state["result"] = None
        state["last_loaded"] = sym
        if show_wait:
            state["loading"] = True
            wait.show(f"Loading {sym}…")
            ui.timer(_overlay.LOAD_TIMEOUT_SEC, _load_timeout, once=True)
        bus_client.request("options", {"type": "calc_load", "args": {"symbol": sym}})
        _sync_status()
        ui.notify(f"Loading {sym}…", type="info")

    @guard
    def _load_timeout():
        """Safety net: if the chain never arrived, drop the overlay + reset the dedup
        so a retry re-triggers (e.g. the service is down)."""
        if state.get("loading"):
            state["loading"] = False
            wait.hide()
            state["last_loaded"] = None
            _sync_status()

    @guard
    def _symbol_submit():
        """Tab-out / Enter on the symbol field → Load (deduped: only when changed)."""
        if not should_load((symbol_in.value or "").strip().upper(), state.get("last_loaded")):
            return
        load_symbol(show_wait=True)

    @guard
    def fetch_iv():
        """Imply IV (ThinkorSwim-style) from the traded contract's live mark at the
        intraday time-to-expiry — the service solves Black-Scholes for sigma (async;
        the ``calc_iv`` poll fills the field). Falls back to the cached chain's ATM
        ``volatility`` when no leg strike/mark is available yet (pre-selection)."""
        sym = (symbol_in.value or "").strip().upper()
        if not sym or not expiry_sel.value or not price_in.value:
            ui.notify("Load symbol + pick Expiry (Price required).", type="warning")
            return
        chain = state.get("chain")
        if chain is None:
            ui.notify("Load symbol first.", type="warning")
            return
        try:
            expiry = dt.date.fromisoformat(str(expiry_sel.value))
            spot = float(price_in.value)
        except Exception as exc:
            ui.notify(f"Bad expiry/price: {exc}", type="negative")
            return

        # Prefer implying IV from the traded contract's mark (matches ToS). Pick the
        # leg whose strike is nearest spot (the most liquid mark) among legs that
        # have a chosen strike + a mark in the cached chain.
        primary = None  # (strike, option_type, mark)
        best = float("inf")
        for leg in editor.get_legs():
            sv = leg.get("strike")
            if not sv:
                continue
            strike = float(sv)
            mark = (extract_premium(chain, leg["option_type"], strike, expiry=expiry)
                    or extract_premium(chain, leg["option_type"], strike))
            if mark is None:
                continue
            d = abs(strike - spot)
            if d < best:
                best, primary = d, (strike, leg["option_type"], mark)
        if primary is not None:
            strike, otype, mark = primary
            bus_client.request("options", {"type": "calc_iv", "args": {
                "spot": spot, "strike": strike, "option_type": otype, "mark": mark,
                "expiry": str(expiry), "rate": float(rate_in.value or 4.5) / 100.0}})
            ui.notify(f"Implying IV from {otype} {strike:g} mark {mark:.2f}…",
                      type="info")
            return

        # Fallback: ATM volatility straight from the cached chain (pre-strike pick).
        iv = extract_atm_iv(chain, spot, expiry=expiry)
        approx = False
        if iv is None:
            iv = extract_atm_iv(chain, spot)  # nearest listed expiry
            approx = iv is not None
        if iv is None:
            ui.notify(f"No ATM IV for {sym} {expiry}.", type="warning")
            return
        iv_in.value = round(iv, 1)
        suffix = " (nearest listed expiry)" if approx else ""
        ui.notify(f"ATM IV {iv:.1f}%{suffix}", type="positive")

    @guard
    def do_calc():
        """Build the params dict and enqueue ``calc_compute``; the version-poll
        paints the summary tiles + P&L grid from the cached result.

        Each leg carries its OWN expiry + qty (so calendars/diagonals price each
        leg correctly and ``calc_compute`` derives the grid horizon from the front
        leg). When the user has edited the legs (``editor.is_dirty()``) the summary
        routes through the generic numeric path (``strategy="CUSTOM"``); otherwise
        the selected strategy code drives the analytic path where supported."""
        legs = editor.get_legs()
        if not legs:
            ui.notify("Add at least one leg first.", type="warning")
            return
        if any(l.get("strike") is None for l in legs):
            ui.notify("Pick all leg strikes first.", type="warning")
            return
        try:
            spot = float(price_in.value)
            page_qty = int(contracts_in.value or 1)
            page_exp = str(expiry_sel.value)
            # Route analytic vs generic summary (see _summary_strategy): a copied
            # or edited structure falls to the generic numeric summary.
            strat = _summary_strategy(strategy_sel.value, legs, editor.is_dirty())
            params = {
                "strategy": strat,
                "spot": spot,
                "iv": float(iv_in.value) / 100.0,
                "rate": float(rate_in.value) / 100.0,
                "ivadj": float(ivchg_in.value) / 100.0,
                "qty": page_qty,
                "expiry": page_exp,
                "legs": [{"strike": float(l["strike"]),
                          "premium": float(l["premium"] or 0),
                          "option_type": l["option_type"],
                          "side": l["side"],
                          "qty": int(l.get("qty", 1) or 1),
                          "expiry": l.get("expiry") or page_exp}
                         for l in legs],
                "num_strikes": int(nstrikes_in.value or 24),
                # Grid rows = the ±N real chain strikes around spot (front-expiry
                # ladder, union of calls+puts). None when no chain yet → engine
                # falls back to its even-step ±N heuristic.
                "price_rows": strikes_window(
                    sorted(set(_strikes_for(expiry_sel.value, "call"))
                           | set(_strikes_for(expiry_sel.value, "put"))),
                    spot, int(nstrikes_in.value or 24)) or None,
            }
            dt.date.fromisoformat(params["expiry"])  # validate before enqueue
        except Exception as exc:
            ui.notify(f"Calc failed: {exc}", type="negative")
            return
        state["calc_spot"] = spot
        # The result payload carries no horizon of its own — see max_dte_from_legs.
        state["calc_dte"] = max_dte_from_legs(params["legs"])
        state["calc_symbol"] = (symbol_in.value or "").strip().upper()
        bus_client.request("options", {"type": "calc_compute", "args": params})
        ui.notify("Calculating…", type="info")

    @guard
    def send_to_em():
        """Chart the expected move for the current legs (opens a new tab)."""
        legs = [{"strike": float(l["strike"]), "option_type": l["option_type"],
                 "side": l["side"]}
                for l in editor.get_legs() if l.get("strike") is not None]
        handoff.send_to_expected_move({
            "symbol": (symbol_in.value or "").replace("$", "").upper(),
            "expiry": str(expiry_sel.value or ""), "legs": legs})

    # ── version-poll repaint (fetch-free) ────────────────────────────────────
    def _apply_chain(cc):
        cc = cc or {}
        state["loading"] = False
        wait.hide()
        state["chain"] = cc.get("chain")
        if cc.get("price"):
            state["spot"] = round(cc["price"], 2)
            price_in.value = round(cc["price"], 2)
        exps = chain_expiries(state["chain"] or {})
        state["applying"] = True
        try:
            expiry_sel.options = exps
            if exps and expiry_sel.value not in exps:
                expiry_sel.value = exps[0]
            expiry_sel.update()
        finally:
            state["applying"] = False
        # Repopulate the per-leg expiry/strike selects from the freshly-loaded
        # chain. Pending legs copied in from the Simulator win; otherwise, when the
        # user hasn't touched the legs, re-seed the template so strikes snap to the
        # real ladder (preserves the old Load → strikes-ready behavior).
        pending = state.pop("pending_legs", None)
        if pending:
            editor.set_legs(pending)
            # Copy-from-Simulator legs carry no premium → fetch from the chain; a
            # restored snapshot keeps the user's own premiums (don't clobber them).
            if any(l.get("premium") in (None, 0) for l in pending):
                fetch_premiums()
            do_calc()
        elif not editor.is_dirty():
            _seed_template()   # re-seed (template ratios × Contracts), snap strikes
        else:
            editor.refresh_options()
        _sync_legs()
        _sync_status()
        if cc.get("symbol") is not None:
            price = cc.get("price")
            msg = f"{cc['symbol']}: {len(exps)} expiries" + (f", {price:.2f}" if price else "")
            ui.notify(msg, type="positive" if exps else "warning")

    def _apply_result(result):
        state["result"] = result or None
        if not result:
            _sync_results()
            return
        spot = state.get("calc_spot")
        if spot is None:
            spot = float(price_in.value or 0)
        summary = result.get("summary") or {}
        pnl_data = result.get("pnl_data") or []
        legs = editor.get_legs()
        _render_metrics(metrics_box, summary, legs, spot, state.get("calc_dte"))
        _render_grid(grid_box, result.get("eval_labels") or [], pnl_data, spot,
                     summary, legs)
        matrix_frame.note.text = matrix_note_text(pnl_data, matrix_basis(summary, legs))
        _sync_results()

    def _apply_iv(res):
        """Fill the IV field from a ``calc_iv`` result (implied from the mark)."""
        res = res or {}
        iv = res.get("iv")
        if iv is not None:
            iv_in.value = round(iv, 1)
            sk = res.get("strike")
            sk_txt = f"{sk:g}" if isinstance(sk, (int, float)) else sk
            ui.notify(f"Implied IV {iv:.1f}% ({res.get('option_type')} {sk_txt})",
                      type="positive")
        elif res.get("error"):
            ui.notify(f"Couldn't imply IV ({res['error']}). Enter it manually.",
                      type="warning")

    @guard_async
    async def _poll_chain():
        # The :ver probe stays ON the event loop (cheap). The chain payload
        # (cache:options:calc_chain — thinned server-side to the five fields this
        # page reads, ~0.7 MB since 2026-08-20; it was the raw ~8.8 MB chain — a
        # blocking GET + JSON parse) is read OFF the loop via run.io_bound so it
        # never blocks other clients. The version-gate means the big read only
        # happens when a new chain was published (not every 1 s tick), and the
        # in-flight guard stops a slow read from stacking across ticks.
        version = bus_client.read_version("options:calc_chain")
        if version == state["chain_ver"] or state.get("chain_fetching"):
            return
        state["chain_ver"] = version
        state["chain_fetching"] = True
        try:
            chain = await run.io_bound(bus_client.read, "options:calc_chain")
        finally:
            state["chain_fetching"] = False
        _apply_chain(chain)

    @guard
    def _poll_result():
        version = bus_client.read_version("options:calc_result")
        if version == state["result_ver"]:
            return
        state["result_ver"] = version
        _apply_result(bus_client.read("options:calc_result"))

    @guard
    def _poll_iv():
        version = bus_client.read_version("options:calc_iv")
        if version == state["iv_ver"]:
            return
        state["iv_ver"] = version
        _apply_iv(bus_client.read("options:calc_iv"))

    # Initial paint (graceful-empty when the service is cold). Track the current
    # versions WITHOUT applying stale cached chain/result so a fresh page doesn't
    # adopt a previous symbol's chain or grid; the user drives load/Calculate.
    state["chain_ver"] = bus_client.read_version("options:calc_chain")
    state["result_ver"] = bus_client.read_version("options:calc_result")
    state["iv_ver"] = bus_client.read_version("options:calc_iv")

    ui.timer(1.0, _poll_chain)
    ui.timer(1.0, _poll_result)
    ui.timer(1.0, _poll_iv)

    # Per signal-type: leg specs as (option_type, side, strike_field, mark_field).
    # Mirrors the legacy ``setleg`` wiring (PCS/CCS/IC); the strikes/marks come
    # straight off the scanner signal dict.
    _PREFILL_LEGS = {
        "PCS": [("put", "short", "short_strike", "short_mark"),
                ("put", "long", "long_strike", "long_mark")],
        "CCS": [("call", "short", "short_strike", "short_mark"),
                ("call", "long", "long_strike", "long_mark")],
        "IC": [("put", "short", "short_strike", "short_mark"),
               ("put", "long", "long_strike", "long_mark"),
               ("call", "short", "call_short", "call_short_mark"),
               ("call", "long", "call_long", "call_long_mark")],
    }

    def _prefill(sig):
        """Populate inputs from a scanner/swing signal (Send to Calculator).

        Builds the legs from the signal's strike/mark fields and stashes them as
        ``pending_legs``, then **loads the symbol's chain** — the legs are applied
        (and the grid computed) once the chain arrives, in ``_apply_chain``. This
        mirrors the Copy-from-Simulator path: applying legs BEFORE the chain loads
        wiped every strike, because the leg-editor coerces each strike against the
        cached chain's strike ladder (empty pre-load → strike cleared to None) —
        which is exactly the "legs don't transfer" bug.

        Note: ``oc.generate_price_range`` is gone from the page, so the Range
        min/max are NOT pre-filled here; ``calc_compute`` derives the grid rows
        from the loaded chain's strike ladder."""
        t = sig.get("type")
        if t in strategy_options():
            strategy_sel.value = t        # also re-seeds the template via on_change
        sym = (sig.get("symbol") or "").replace("$", "")
        if sym:
            symbol_in.value = sym
        price = sig.get("underlying_price")
        if price:
            price_in.value = round(price, 2)
        exp = sig.get("expiration")
        if exp:
            state["applying"] = True
            try:
                expiry_sel.options = [exp]
                expiry_sel.value = exp
                expiry_sel.update()
            finally:
                state["applying"] = False
        iv = sig.get("short_iv")
        if iv:
            iv_in.value = round(iv, 1)

        legs = []
        for otype, side, strike_field, mark_field in _PREFILL_LEGS.get(t, []):
            strike = sig.get(strike_field)
            if strike in (None, 0, ""):
                continue
            mark = sig.get(mark_field)
            legs.append({"option_type": otype, "side": side,
                         "strike": float(strike), "expiry": exp, "qty": 1,
                         "premium": round(mark, 2) if mark else None})
        # Apply once the chain lands (valid strikes); load_symbol enqueues calc_load.
        state["pending_legs"] = legs or None
        load_symbol()
        ui.notify(f"Loaded {sym} {t} from scanner — loading chain…", type="positive")

    _pending = handoff.take_pending_calculator()
    if _pending:
        _prefill(_pending)

    # Legs copied in from the Simulator: stash them and load the symbol; the legs
    # are applied once the chain arrives (see ``_apply_chain``'s pending path).
    _legs_in = handoff.take_pending_calculator_legs()
    if _legs_in:
        symbol_in.value = _legs_in.get("symbol") or symbol_in.value
        state["pending_legs"] = _legs_in.get("legs") or []
        load_symbol()   # enqueue calc_load; legs applied when the chain arrives

    # Restore the persisted snapshot — but only when no handoff consumed the seed
    # (an explicit Copy/Send-to-Calculator wins). Auto-refresh: reload the chain
    # (fresh price) → _apply_chain applies the restored legs + recomputes.
    if not _pending and not _legs_in and _LAST_CALC:
        _restore(_LAST_CALC)
        load_symbol()

    # First paint of the derived readouts. LAST, so it reflects whatever the
    # handoff / restore paths above settled on — ``_restore`` in particular
    # writes the widgets under the restoring guard, which suppresses every
    # change handler that would otherwise have painted them.
    _sync_strategy()
    _sync_legs()
    _sync_status()
