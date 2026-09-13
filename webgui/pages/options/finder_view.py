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

from pages import fmt as _fmt    # the ONE numeric vocabulary (pages/fmt.py)

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
    sign = "-" if f < 0 else ""
    a = abs(f)
    # Compare the ROUNDED cents, so 99.996 reads "$100", never "$100.00".
    body = f"{a:,.0f}" if round(a, 2) >= 100 else f"{a:,.2f}"
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


# --------------------------------------------------------------- chips and picks

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


def summary_facts(payload):
    """The summary strip's facts, or None before any scan has published.

    ``{"symbol", "price", "pills", "vol_rank", "counts"}``. Vol Rank lives here
    rather than in the list because a scan is one symbol, so every row carried the
    same value.
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
    vol_rank = None if rank is None else f"Vol Rank {round(rank)}"

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
