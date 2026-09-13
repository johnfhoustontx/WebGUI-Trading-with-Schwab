"""View model for the redesigned Strategy Finder (``/options/swing``) - PURE.

The page shows a summary strip, instant-filter strategy chips, up to four top-pick
cards and a slim ranked list. Everything those widgets need to *say* is computed
here from the ``cache:options:swing`` payload, so it is unit-tested without a
browser (``webgui/tests/test_finder_view.py``); ``swing.py`` only builds widgets
and wires them to these functions.

Deliberately imports no widget code - not even ``strategy_table``, whose import of
``scanner`` drags the UI library in. The few facts shared with that module (the
unbounded-side predicates) are restated here with a pointer back.

Design: ``docs/plans/2026-09-13-strategy-finder-redesign-design.md``.
"""
import datetime as _dt
import math as _math

from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)

from .theme import THEME as _THEME   # config only - theme.py imports no widget code

NO_READING = _fmt.NO_READING

# The seven build groups ``swing_scan`` stamps on each candidate as ``group``, in
# the order the chips render. ``swing._FAMILY_OPTIONS`` aliases this.
GROUPS = [
    ("DIRECTIONAL", "Directional"),
    ("VERTICAL", "Spreads"),
    ("NEUTRAL", "Neutral"),
    ("STRADDLE", "Straddles & strangles"),
    ("BUTTERFLY", "Butterflies & condors"),
    ("CALENDAR", "Calendars"),
    ("STOCK", "Stock + options"),
]
_GROUP_LABEL = dict(GROUPS)

# Month abbreviations spelled out rather than ``strftime("%b")``, which follows
# the process locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _half_up(x):
    """Round to the nearest whole number, halves UP.

    Python's ``round`` is banker's rounding (``round(42.5) == 42``), which would
    make the page round one half down and the next up. Every whole number this
    page shows - a percent, Vol Rank, a bar's 5% step - goes through here.
    """
    return int(_math.floor(x + 0.5))


# --------------------------------------------------------------------------- text

def money(v):
    """Dollars for a card or cell: ``$54,058`` at |v| >= 100, ``$4.50`` below.

    Whole dollars at scale because the page puts a $54,000 collar beside a $196
    butterfly, and cents on the first only add noise. A negative keeps a leading
    minus (``-$300``). No reading (None, NaN, a bool) is the em-dash.
    """
    f = _fmt.num(v)
    if f is None:
        return NO_READING
    a = abs(f)
    # Compare the ROUNDED cents, so 99.996 reads "$100", never "$100.00".
    body = f"{a:,.0f}" if round(a, 2) >= 100 else f"{a:,.2f}"
    # The sign is decided AFTER rounding: -0.001 is "$0.00", never "-$0.00".
    sign = "-" if f < 0 and body.strip("0.,") else ""
    return f"{sign}${body}"


def _share_count(legs):
    """Shares held across the stock legs (``qty`` counts 100-share LOTS; a missing
    or malformed qty reads as one lot, as ``strategy_table.legs_summary`` does)."""
    total = 0
    for leg in legs or []:
        if (leg or {}).get("kind") != "stock":
            continue
        n = _fmt.num(leg.get("qty"))
        total += 100 * (int(n) if n is not None and n >= 1 else 1)
    return total


def cost_text(sig):
    """``"$195 debit"`` / ``"$804 credit"`` / ``"$54,058 debit for 100 shares"``.

    The word carries the direction, so the amount is shown unsigned.
    """
    s = sig or {}
    debit = _fmt.num(s.get("net_debit"))
    credit = _fmt.num(s.get("net_credit"))
    if debit is not None:
        text = f"{money(abs(debit))} debit"
    elif credit is not None:
        text = f"{money(abs(credit))} credit"
    else:
        return NO_READING
    shares = _share_count(s.get("legs"))
    if shares:
        text += f" for {shares} shares"
    return text


def expiry_text(sig):
    """``"Oct 16 · 8d"`` - the front expiry and its days to expiry."""
    s = sig or {}
    try:
        d = _dt.date.fromisoformat(str(s.get("expiration"))[:10])
    except (TypeError, ValueError):
        return NO_READING
    text = f"{_MONTHS[d.month - 1]} {d.day}"
    dte = _fmt.num(s.get("dte"))
    if dte is not None:
        text += f" · {int(dte)}d"
    return text


# ------------------------------------------------------------------------ presets

# (label, DTE min, DTE max). "Any" is the DTE inputs' own full range.
EXPIRY_PRESETS = [
    ("1–2 wk", 7, 14),
    ("2–6 wk", 14, 42),
    ("1–3 mo", 30, 90),
    ("Any", 0, 120),
]


def expiry_range_for(label):
    """``(DTE min, DTE max)`` for a preset label; None for anything else (a cleared
    toggle, a hand-edited range)."""
    for p_label, p_lo, p_hi in EXPIRY_PRESETS:
        if label == p_label:
            return p_lo, p_hi
    return None


def expiry_preset_for(lo, hi):
    """The preset label a DTE range matches, or None when it is hand-edited."""
    a, b = _fmt.num(lo), _fmt.num(hi)
    for label, p_lo, p_hi in EXPIRY_PRESETS:
        if a == p_lo and b == p_hi:
            return label
    return None


# Short-leg |delta| bands, applied to both sides. Balanced is TODAY's default scan
# (put -0.20..-0.10, call 0.10..0.20) - the design's first draft would have
# relabelled it Custom and silently moved the default.
RISK_STYLES = {
    "Conservative": (0.05, 0.10),
    "Balanced": (0.10, 0.20),
    "Aggressive": (0.20, 0.30),
}
RISK_DEFAULT = "Balanced"
RISK_CUSTOM = "Custom"
_BAND_TOL = 1e-9


def risk_bands(name):
    """The four scan keyword values for a risk style (put side negative)."""
    lo, hi = RISK_STYLES[name]
    return {"put_d_min": -hi, "put_d_max": -lo, "call_d_min": lo, "call_d_max": hi}


def risk_style_for(put_d_min, put_d_max, call_d_min, call_d_max):
    """The style whose bands these four values are, else ``"Custom"``."""
    got = [_fmt.num(v) for v in (put_d_min, put_d_max, call_d_min, call_d_max)]
    if any(v is None for v in got):
        return RISK_CUSTOM
    for name in RISK_STYLES:
        want = risk_bands(name)
        exp = [want["put_d_min"], want["put_d_max"], want["call_d_min"], want["call_d_max"]]
        if all(abs(g - e) <= _BAND_TOL for g, e in zip(got, exp)):
            return name
    return RISK_CUSTOM


def bands_for_choice(choice):
    """The bands a risk-style TOGGLE value selects, or None.

    The page's handler goes through this rather than :func:`risk_bands`, which
    raises on ``"Custom"``: Custom is a read-only state (the fields match no
    style), never a choice that writes four fields.
    """
    return risk_bands(choice) if choice in RISK_STYLES else None


def risk_toggle_value(put_d_min, put_d_max, call_d_min, call_d_max):
    """The toggle's value for four band fields: a style name, or None for Custom.

    The toggle offers only the three styles, so a hand-edited band shows no
    selection (plus the page's read-only Custom marker).
    """
    style = risk_style_for(put_d_min, put_d_max, call_d_min, call_d_max)
    return style if style in RISK_STYLES else None


# --------------------------------------------------------------- chips and picks

ALL_CHIP = "ALL"


def toggle_chip(active, code):
    """The active chip set after clicking ``code``. ``None`` means All.

    All resets; a chip toggles its membership; un-choosing the last chip goes
    back to All rather than to an empty page. Never mutates ``active``.
    """
    if code == ALL_CHIP:
        return None
    if active is None:
        return {code}
    out = set(active)
    if code in out:
        out.discard(code)
    else:
        out.add(code)
    return out or None


def chip_is_active(active, code):
    """Whether chip ``code`` renders highlighted for the active set."""
    if code == ALL_CHIP:
        return active is None
    return active is not None and code in active


def carry_chips(active, prev_symbol, symbol, signals):
    """Chip state across a repaint: kept for the same symbol, All for a new one.

    Groups the new payload did not produce are dropped, and if nothing chosen
    survives the page goes back to All.
    """
    if active is None or prev_symbol != symbol:
        return None
    present = {code for code, _label, _n in chip_counts(signals)}
    kept = set(active) & present
    return kept or None

def chip_counts(signals):
    """``[(code, label, n), ...]`` in :data:`GROUPS` order, only groups with rows."""
    counts = {}
    for s in signals or []:
        g = (s or {}).get("group")
        if g in _GROUP_LABEL:
            counts[g] = counts.get(g, 0) + 1
    return [(code, label, counts[code]) for code, label in GROUPS if counts.get(code)]


def filter_groups(signals, active):
    """The signals whose group is in ``active``; ``None`` means every group.

    An EMPTY set is a real selection (nothing chosen) and yields nothing.
    """
    rows = list(signals or [])
    if active is None:
        return rows
    return [s for s in rows if (s or {}).get("group") in active]


def _rank_key(sig):
    score = _fmt.num(sig.get("composite_score"))
    # Unscored rows last; ties by id so the cards do not reshuffle between paints.
    return (score is None, -(score or 0.0), str(sig.get("id") or ""))


def ranked(signals):
    """Signals best score first (unscored last, ties by id)."""
    return sorted((s for s in signals or [] if s), key=_rank_key)


def top_picks(signals, k=4):
    """Up to ``k`` cards: the best-scoring signal of each DIFFERENT group.

    Four spreads in a row would be one idea shown four times; the cards exist to
    compare structures.
    """
    if k <= 0:
        return []
    picks, taken = [], set()
    for s in ranked(signals):
        g = s.get("group")
        if g in taken:
            continue
        taken.add(g)
        picks.append(s)
        if len(picks) >= k:
            break
    return picks


# ------------------------------------------------------------------ summary strip

def _conviction_word(c):
    if c < 0.34:
        return "low"
    if c < 0.67:
        return "medium"
    return "high"


def payload_answers_scan(scan_symbol, payload):
    """Whether a newly published payload is the answer to the scan in progress.

    ``cache:options:swing`` is ONE slot: scan AAPL then quickly MSFT and AAPL's
    result still lands, as does any other tab's scan. While a scan waits
    (``scan_symbol`` set), only a payload for that symbol may replace the
    placeholders. ``None`` means nothing is waiting, so anything paints; a blank
    request names no symbol, so any answer is its answer.
    """
    if scan_symbol is None or not str(scan_symbol).strip():
        return True
    got = (payload or {}).get("symbol")
    return bool(got) and str(got).strip().upper() == str(scan_symbol).strip().upper()


def no_data_label(payload):
    """What the empty list says after a scan that returned no rows - the reason,
    in the page's voice, rather than Quasar's "No data available"."""
    p = payload or {}
    if _fmt.num(p.get("filtered_out")):
        return "No strategies cleared the quality bar for this symbol."
    if _fmt.num(p.get("vol_filtered")):
        return "No strategies to show — premium is too cheap to sell for this symbol."
    return "No strategies could be built for this symbol in this expiry range."


def summary_facts(payload):
    """The summary strip's facts, or None before any scan has published.

    ``{"symbol", "price", "pills", "vol_rank", "counts"}``. Vol Rank lives here
    rather than in the list because a scan is one symbol, so every row carried the
    same value.

    ⚠ **Two drops, two sentences, never merged** (moved here from the old
    ``swing.status_text``). The service's quality cut and the volatility gate (gap
    assessment B2) both remove candidates, but the gate refuses to SELL premium
    when IV rank sits below the floor - a statement about today's environment, not
    the candidate - so borrowing "below the quality bar" for it would print
    something untrue on exactly the scan where the reader most needs the reason.
    Both read with a falsy default, so a payload written before ``vol_filtered``
    existed (Redis keeps this view across a restart) renders as it always did.
    """
    p = payload or {}
    symbol = p.get("symbol")
    if not symbol:
        return None
    signals = [s for s in (p.get("signals") or []) if s]
    first = signals[0] if signals else {}

    spot = _fmt.num(first.get("underlying_price"))
    price = None if spot is None else f"${spot:,.2f}"

    view = p.get("view") or {}
    pills = []
    if view.get("direction"):
        pills.append(str(view["direction"]).title())
    conviction = _fmt.num(view.get("conviction"))
    if conviction is not None:
        pills.append(f"Conviction {_conviction_word(conviction)}")
    if view.get("vol_regime"):
        pills.append(f"Volatility {view['vol_regime']}")

    rank = _fmt.num(first.get("iv_rank"))
    vol_rank = None if rank is None else f"Vol Rank {_half_up(rank)}"

    n = len(signals)
    parts = [f"{n} idea" if n == 1 else f"{n} ideas"]
    below = _fmt.num(p.get("filtered_out"))
    if below:
        parts.append(f"{int(below)} below the quality bar")
    cheap = _fmt.num(p.get("vol_filtered"))
    if cheap:
        parts.append(f"{int(cheap)} where premium is too cheap to sell")

    return {"symbol": symbol, "price": price, "pills": pills,
            "vol_rank": vol_rank, "counts": " · ".join(parts)}


# ------------------------------------------------------------------------- bars

# Width classes snap to 5% steps: a FIXED, finite class set (the Tailwind
# finite-palette rule), never a runtime ``w-[43.7%]``.
_WIDTH = {p: ("w-0" if p == 0 else "w-full" if p == 100 else f"w-[{p}%]")
          for p in range(0, 101, 5)}


def _snap(pct):
    """``pct`` to the nearest 5 in [0, 100]. A positive value never snaps to 0 -
    a real $1,960 profit beside a $53,817 loss must still show a sliver."""
    f = _fmt.num(pct)
    if f is None or f <= 0:
        return 0
    return max(5, min(100, _half_up(f / 5.0) * 5))


def _width(pct):
    return _WIDTH[_snap(pct)]


# Which side of a payoff is unbounded. Restated from strategy_table's
# ``_profit_is_unbounded`` / ``_loss_is_unbounded`` (that module cannot be imported
# here, see the docstring): the explicit engine flags first, then the legacy
# ``unbounded`` partitioned by ``max_profit`` for a row cached before them.
def _profit_unbounded(sig):
    if sig.get("unbounded_profit"):
        return True
    return bool(sig.get("unbounded")) and sig.get("max_profit") is None


def _loss_unbounded(sig):
    if sig.get("unbounded_loss"):
        return True
    return bool(sig.get("unbounded")) and sig.get("max_profit") is not None


_INFINITY = "∞"


def risk_reward_bar(sig):
    """One split bar: red max loss left, green max profit right.

    Both halves scale to the LARGER of the two, so the bar shows the shape of the
    bet (a butterfly mostly green, a covered call mostly red). A single scale
    across rows would let one covered call flatten every other bar. An unbounded
    side draws full and is labelled ``∞`` - for a naked short, whose ``max_loss``
    is a margin proxy, drawing that number to scale would read as a cap.

    ``{"loss_class", "profit_class", "loss_label", "profit_label"}``, or None when
    neither side has a usable number.
    """
    s = sig or {}
    p_inf, l_inf = _profit_unbounded(s), _loss_unbounded(s)
    profit = None if p_inf else _fmt.num(s.get("max_profit"))
    loss = None if l_inf else _fmt.num(s.get("max_loss"))
    profit = None if profit is None else abs(profit)
    loss = None if loss is None else abs(loss)
    if not (p_inf or l_inf) and profit is None and loss is None:
        return None

    def _half(value, inf, other_inf):
        if inf:
            return "w-full", _INFINITY
        if value is None:
            return "w-0", NO_READING
        if other_inf:
            return _width(0 if value == 0 else 1), money(value)   # a sliver
        top = max(v for v in (profit, loss) if v is not None)
        return _width(100.0 * value / top if top > 0 else 0), money(value)

    loss_class, loss_label = _half(loss, l_inf, p_inf)
    profit_class, profit_label = _half(profit, p_inf, l_inf)
    return {"loss_class": loss_class, "profit_class": profit_class,
            "loss_label": loss_label, "profit_label": profit_label}


def pop_bar(pop):
    """Probability-of-profit bar, 0-100%: amber below 40, green above 60.

    ``pop`` is a PERCENT (the engine's ``pop_pct``), not a fraction.
    ``{"class", "tone": "warn" | "neutral" | "pos", "label"}``, or None.
    """
    f = _fmt.num(pop)
    if f is None:
        return None
    # The band is decided on the ROUNDED percent, so the colour always agrees
    # with the label beside it (39.6 reads "40%" and is not amber).
    whole = _half_up(f)
    tone = "warn" if whole < 40 else "pos" if whole > 60 else "neutral"
    return {"class": _width(f), "tone": tone, "label": f"{whole}%"}


# The odds bar's fill per tone - a fixed class set, bound through a table slot's
# ``:class``: amber below 40, the theme's accent blue between (grey read as "no
# data"), the app's profit green above 60.
POP_FILL = {"warn": "bg-[#fbbf24]",
            "neutral": f"bg-[{_THEME['palette']['primary']}]",
            "pos": "bg-[#34d399]"}


def _pop_fill(bar):
    return POP_FILL.get((bar or {}).get("tone"), "")


# ------------------------------------------------------------------- payoff SVG

# The app's existing P/L colours (simulator.whatif_figure). A hand-drawn chart's
# stroke colours are chart config, outside the Tailwind class rule.
PROFIT_STROKE = "#34d399"
LOSS_STROKE = "#f87171"
ZERO_STROKE = "#3a4a6b"
SPOT_STROKE = "#8794b4"
_PAD = 2
_SPOT_TICK = 4          # half-height of today's-price tick, px


def _n(v):
    """A coordinate as short text: one decimal, no trailing ``.0``."""
    t = f"{v:.1f}"
    return t[:-2] if t.endswith(".0") else t


def _line(x1, y1, x2, y2, stroke, extra=""):
    return (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" '
            f'stroke="{stroke}"{extra}/>')


def _sign_stroke(pnl):
    return PROFIT_STROKE if pnl > 0 else LOSS_STROKE if pnl < 0 else ZERO_STROKE


def payoff_svg(curve, spot, width=120, height=32):
    """A small payoff shape: profit green, loss red, a dashed zero line, a tick at
    today's price.

    ``curve`` is the service's ``payoff_curve`` - ``[[price, pnl], ...]``. The SVG
    is a FIXED pixel size with a matching ``viewBox`` and no
    ``preserveAspectRatio``: stretching a viewBox needs ``vector-effect`` to keep
    strokes even, and DOMPurify strips that attribute (CLAUDE.md). One ``<line>``
    per segment, coloured by its sign - a polyline cannot change colour part-way
    - and a segment that CROSSES zero is split at the interpolated break-even so
    the colour changes exactly there. Returns ``""`` when there is nothing to draw
    (fewer than two usable points, or no price range).
    """
    pts = []
    for p in curve or []:
        try:
            x, y = _fmt.num(p[0]), _fmt.num(p[1])
        except (TypeError, IndexError, KeyError):
            continue
        if x is not None and y is not None:
            pts.append((x, y))
    if len(pts) < 2:
        return ""
    pts.sort(key=lambda q: q[0])
    x_lo, x_hi = pts[0][0], pts[-1][0]
    if x_hi <= x_lo:
        return ""
    y_lo = min(0.0, min(q[1] for q in pts))
    y_hi = max(0.0, max(q[1] for q in pts))
    w, h = int(width), int(height)
    inner_w, inner_h = w - 2 * _PAD, h - 2 * _PAD

    def sx(x):
        return _PAD + (x - x_lo) / (x_hi - x_lo) * inner_w

    def sy(y):
        if y_hi <= y_lo:                      # a flat-zero payoff: centre it
            return h / 2.0
        return _PAD + (y_hi - y) / (y_hi - y_lo) * inner_h

    zero_y = sy(0.0)
    parts = [_line(_PAD, zero_y, w - _PAD, zero_y, ZERO_STROKE,
                   ' stroke-width="1" stroke-dasharray="2 2"')]
    seg = ' stroke-width="1.5" stroke-linecap="round"'
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if y1 * y2 < 0:
            # Crosses break-even: split at the interpolated zero so red and green
            # meet exactly there, instead of one colour spilling past it.
            xc = x1 + (0.0 - y1) / (y2 - y1) * (x2 - x1)
            parts.append(_line(sx(x1), sy(y1), sx(xc), zero_y, _sign_stroke(y1), seg))
            parts.append(_line(sx(xc), zero_y, sx(x2), sy(y2), _sign_stroke(y2), seg))
        else:
            # Same sign, or touching zero at one end: the non-zero end decides.
            parts.append(_line(sx(x1), sy(y1), sx(x2), sy(y2),
                               _sign_stroke(y1 if y1 != 0 else y2), seg))
    s = _fmt.num(spot)
    if s is not None and x_lo <= s <= x_hi:
        x = sx(s)
        parts.append(_line(x, max(0.0, zero_y - _SPOT_TICK), x,
                           min(float(h), zero_y + _SPOT_TICK), SPOT_STROKE,
                           ' stroke-width="1"'))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">' + "".join(parts) + "</svg>")


# ------------------------------------------------------------ list and cards

# An unbounded side sorts past every real figure. float("inf") cannot cross the
# JSON wire to the table, so a large finite stand-in.
_UNBOUNDED_SORT = 1e12


def finder_columns():
    """``ui.table`` columns for the slim ranked list.

    Legs, breakevens, bias and Vol Rank are gone from the list (the detail panel
    and the summary strip carry them). The money, odds and expiry columns sort on
    a NUMERIC twin field - their shown text ("$1,234", "Oct 16 · 30d") would sort
    as a string - and the page's slots render the text.
    """
    spec = [
        ("strategy", "Strategy", "strategy", True),
        ("composite_score", "Score", "composite_score", True),
        ("expiry", "Expiry", "_dte", True),
        ("cost", "Cost", "cost", False),
        ("max_profit", "Max profit", "_max_profit_n", True),
        ("max_loss", "Max loss", "_max_loss_n", True),
        ("pop", "Probability of profit", "_pop_n", True),
        ("grade", "Grade", "grade", False),
    ]
    cols = [{"name": name, "label": label, "field": field, "sortable": sortable,
             "align": "left"} for name, label, field, sortable in spec]
    cols.append({"name": "actions", "label": "", "field": "actions",
                 "sortable": False, "align": "center"})
    return cols


def _max_profit_cell(sig):
    if _profit_unbounded(sig):
        return _INFINITY, _UNBOUNDED_SORT
    v = _fmt.num(sig.get("max_profit"))
    return (NO_READING, None) if v is None else (money(abs(v)), abs(v))


def _max_loss_cell(sig):
    if _loss_unbounded(sig):
        return _INFINITY, _UNBOUNDED_SORT
    v = _fmt.num(sig.get("max_loss"))
    return (NO_READING, None) if v is None else (money(abs(v)), abs(v))


def finder_rows(signals, *, score_class, grade_class, paper_types):
    """Rows for the ranked list, best score first.

    The three hooks are INJECTED because their homes (``scanner.score_zone_class``,
    ``strategy_table.grade_class`` and ``strategy_table._PAPER_TYPES``) import the
    widget library; ``swing.finder_rows`` passes the real ones, so the paper gate
    stays one set rather than a copy here.
    """
    rows = []
    for s in ranked(signals):
        profit_text, profit_n = _max_profit_cell(s)
        loss_text, loss_n = _max_loss_cell(s)
        pop = pop_bar(s.get("pop_pct"))
        dte = _fmt.num(s.get("dte"))
        score = s.get("composite_score")
        rows.append({
            "id": s.get("id"),
            "strategy": s.get("strategy_label") or "",
            "composite_score": score,
            "expiry": expiry_text(s),
            "cost": cost_text(s),
            "max_profit": profit_text,
            "max_loss": loss_text,
            "pop": pop["label"] if pop else NO_READING,
            "grade": s.get("grade") or "",
            "grade_reason": s.get("grade_reason") or "",
            "_dte": None if dte is None else int(dte),
            "_max_profit_n": profit_n,
            "_max_loss_n": loss_n,
            "_pop_n": _fmt.num(s.get("pop_pct")),
            "_score_class": score_class(score),
            "_grade_class": grade_class(s.get("grade")),
            "_payoff_svg": payoff_svg(s.get("payoff_curve"), s.get("underlying_price"),
                                      width=72, height=20),
            "_rr": risk_reward_bar(s),
            "_pop": pop,
            "_pop_fill": _pop_fill(pop),
            "_allow_paper": s.get("type") in paper_types,
            "_undefined_risk": _loss_unbounded(s),
        })
    return rows


CARD_SHAPE_W, CARD_SHAPE_H = 280, 56


def card_facts(sig):
    """What a top-pick card says. The legs line and the paper gate are added by
    the page (their helpers live in widget-importing modules)."""
    s = sig or {}
    score = _fmt.num(s.get("composite_score"))
    pop = pop_bar(s.get("pop_pct"))
    return {
        "title": s.get("strategy_label") or NO_READING,
        "score": score,
        "score_text": NO_READING if score is None else str(_half_up(score)),
        "grade": s.get("grade") or "",
        "expiry": expiry_text(s),
        "cost": cost_text(s),
        # A card is ~300px wide; the list keeps its 72x20 shape.
        "payoff_svg": payoff_svg(s.get("payoff_curve"), s.get("underlying_price"),
                                 width=CARD_SHAPE_W, height=CARD_SHAPE_H),
        "rr": risk_reward_bar(s),
        "pop": pop,
        "pop_fill": _pop_fill(pop),
    }
