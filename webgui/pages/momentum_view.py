"""Pure display language for the Momentum page (/sentiment/momentum).

The redesign turns the page into a numbered argument rather than a dashboard:

1. *Is momentum worth trading today?* — all three regimes shown side by side
   with the live one enlarged, then the dispersion reading behind it.
2. *Three levels, and where they agree* — how much of each universe is in its
   own top quartile, and how many stocks have industry **and** sector behind
   them.
3. *Where the names sit* — the four quadrants as counts, not a scatter.
4. *What a score is made of* — one worked example, decomposed.
5. *Rank over recent sessions* — who is actually climbing.

Every section's arithmetic lives here so it can be pinned by
``tests/test_momentum_view.py``; ``pages/sentiment_momentum.py`` holds widgets.

**Showing all three regime states at once is the point of section 1.** The old
banner named the live state and left the reader to remember what the other two
would have meant — but the whole reason this page exists is that momentum is
only tradeable in some conditions, and that is a comparison.

Palette and the warm-neutral ladder are imported from ``pages/rotation_view.py``
— this is the fourth screen in that design family.
"""
import math

from pages.oklch import oklch_hex
from pages.rotation_view import (  # noqa: F401 — the shared palette
    NB, NE, NT, QUAD_CHROMA, QUAD_HUE,
)

DASH = "—"
MINUS = "−"      # U+2212, matching the mono face's figures


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── 1 · the regime cards ─────────────────────────────────────────────────────
# Order is the argument, not the alphabet: best conditions → worst.
REGIME_ORDER = ("favorable", "neutral", "suppressed")
FALLBACK_STATE = "neutral"

# label, what it means, what to do about it. The instruction line is the part
# the old banner never carried, and it is the only line that tells a reader
# what to actually change.
REGIME_COPY = {
    "favorable": ("Favorable",
                  "Trending conditions — momentum's home turf.",
                  "Pursue the leaderboard"),
    "neutral": ("Neutral",
                "Chop. The score leans on the shorter lookback.",
                "Take the leaderboard with a pinch of salt"),
    "suppressed": ("Suppressed",
                   "Momentum-crash risk — a volatile rebound off a low.",
                   "Board dims · stand aside"),
}
REGIME_HUE = {"favorable": 158.0, "neutral": 80.0, "suppressed": 22.0}
REGIME_CHROMA = {"favorable": 0.11, "neutral": 0.13, "suppressed": 0.14}


def regime_state(regime):
    """The live state, coerced to one this page can render."""
    s = (regime or {}).get("state")
    return s if s in REGIME_COPY else FALLBACK_STATE


def regime_cards(regime):
    """All three states, with the live one flagged ``active``.

    The inactive two stay on screen deliberately: the reader needs to know what
    the *other* conditions would have meant to judge how much today's reading
    is worth."""
    live = regime_state(regime)
    lookback = (regime or {}).get("lookback")
    out = []
    for state in REGIME_ORDER:
        label, blurb, action = REGIME_COPY[state]
        active = state == live
        if active and state == "neutral" and lookback:
            blurb = f"Chop. The score leans on the shorter {lookback} lookback."
        out.append({
            "state": state, "label": label,
            "title": f"{label} · now" if active else label,
            "blurb": blurb, "action": action, "active": active,
        })
    return out


# ── the dispersion strip ─────────────────────────────────────────────────────
def ordinal(n):
    """``12`` → ``12th``. The teens are all -th regardless of last digit."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }".replace(" ", "")


def dispersion(regime):
    """The dispersion percentile, its bar position and the service's reason.

    ``None`` when the service published no reading — better a missing strip
    than a confident bar pinned at zero."""
    r = regime or {}
    pct = _num(r.get("dispersion_pct"))
    if pct is None:
        return None
    pct = max(0.0, min(1.0, pct)) * 100.0
    reasons = [str(x) for x in (r.get("reasons") or []) if str(x).strip()]
    return {"pct": pct, "ordinal": ordinal(round(pct)),
            "sentence": reasons[0] if reasons else ""}


# ── 2 · the three levels ─────────────────────────────────────────────────────
LEVEL_ORDER = ("sector", "industry", "stock")
LEVEL_NAMES = {"sector": "Sectors", "industry": "Industries", "stock": "Stocks"}
TOP_QUARTILE = 75.0


def level_bars(levels):
    """One row per level: universe size, top-quartile count, and bar geometry.

    Track width scales with **√size**, not size. Linear would make the stock
    track 27× the sector track and squash the smaller two into slivers; the
    square root keeps all three legible while still ranking them honestly."""
    levels = levels or {}
    totals = {k: len(levels.get(k) or []) for k in LEVEL_ORDER}
    biggest = max(totals.values()) if any(totals.values()) else 0
    out = []
    for key in LEVEL_ORDER:
        rows = levels.get(key) or []
        total = len(rows)
        top = sum(1 for r in rows if (_num(r.get("percentile")) or 0) >= TOP_QUARTILE)
        track = math.sqrt(total / biggest) * 100.0 if biggest and total else 0.0
        out.append({
            "key": key, "name": LEVEL_NAMES[key], "total": total, "top": top,
            "track_pct": track,
            "fill_pct": (track * top / total) if total else 0.0,
        })
    return out


def alignment_count(levels):
    """Stocks whose industry **and** sector both confirm — all three true.

    The highest-conviction rows on the page, and the only number here that says
    the three levels are telling one story rather than three."""
    return sum(1 for r in ((levels or {}).get("stock") or [])
               if list(r.get("alignment") or []) == [True, True, True])


# ── 3 · the quadrant panels ──────────────────────────────────────────────────
QUAD_ORDER = ("Leading", "Improving", "Weakening", "Lagging")
QUAD_BLURB = {
    "Leading": "Strong and still accelerating.",
    "Improving": "Weak, but acceleration has turned up.",
    "Weakening": "Still strong, acceleration fading.",
    "Lagging": "Weak and decelerating.",
}


def quadrant_of(row):
    """Which corner a row sits in — score against acceleration."""
    score = _num(row.get("score"))
    accel = _num((row.get("components") or {}).get("accel"))
    if score is None or accel is None:
        return ""
    if score >= 0:
        return "Leading" if accel >= 0 else "Weakening"
    return "Improving" if accel >= 0 else "Lagging"


def quadrant_panels(rows, top_names=3):
    """Count, share, bar and the strongest names for each of the four corners.

    All four render even when empty: a quadrant nobody is in is information,
    and dropping the panel would reflow the others as though the grid were
    simply smaller."""
    rows = list(rows or [])
    total = len(rows)
    buckets = {q: [] for q in QUAD_ORDER}
    for r in rows:
        q = quadrant_of(r)
        if q in buckets:
            buckets[q].append(r)
    fullest = max((len(v) for v in buckets.values()), default=0)
    out = []
    for q in QUAD_ORDER:
        got = sorted(buckets[q], key=lambda r: -(_num(r.get("score")) or -9e9))
        # The FULL membership, not just the chips: "which names are Leading?" is
        # the question this section exists to answer, and a three-name teaser
        # cannot answer it. The page shows the head inline and the tail behind
        # an expander, but both come from one list so they cannot disagree.
        members = [{
            "symbol": r.get("symbol"),
            "label": str(r.get("label") or r.get("symbol") or ""),
            "score": _num(r.get("score")),
            "rank": _num(r.get("rank")),
        } for r in got]
        out.append({
            "name": q, "count": len(got), "blurb": QUAD_BLURB[q],
            "share": f"{round(len(got) / total * 100) if total else 0}%",
            "bar_pct": (len(got) / fullest * 100.0) if fullest else 0.0,
            "members": members,
            "names": members[:top_names],
            "more": max(0, len(members) - len(members[:top_names])),
        })
    return out


# ── 4 · what a score is made of ──────────────────────────────────────────────
COMPONENT_ORDER = ("trend", "rs", "accel", "path", "participation")
COMPONENT_LABEL = {"trend": "Trend", "rs": "RS", "accel": "Accel",
                   "path": "Path", "participation": "Partic."}
COMPONENT_MEANING = {
    "trend": "How strong the price trend is",
    "rs": "Strength against the benchmark",
    "accel": "Still building, or fading",
    "path": "How smooth the advance was",
    "participation": "Constituents above their 50-day",
}
# The service caps z-scores here, so it is also where a bar reaches full width.
Z_CAP = 3.0


def example_row(rows, symbol=None):
    """One row decomposed — the page's worked example.

    ``symbol`` selects a specific row (a quadrant chip or a leaderboard click);
    with no selection it is the **top-ranked** row, which doubles as "the current
    leader, explained" and keeps the card in step with the board's first line.

    A selection that is not on this level — switching Industries → Stocks
    strands the previous pick — falls back to the leader rather than blanking
    the card, and says so via ``is_default``."""
    rows = [r for r in (rows or []) if r]
    if not rows:
        return None
    best = None
    if symbol:
        best = next((r for r in rows if r.get("symbol") == symbol), None)
    picked = best is not None
    if best is None:
        best = min(rows, key=lambda r: (_num(r.get("rank")) is None,
                                        _num(r.get("rank")) or 9e9))
    score, pct = _num(best.get("score")), _num(best.get("percentile"))
    rank, prev = _num(best.get("rank")), _num(best.get("rank_prev"))
    delta = None if rank is None or prev is None else int(prev - rank)
    align = [bool(b) for b in (best.get("alignment") or [])]
    return {
        "symbol": best.get("symbol"),
        "is_default": not picked,
        "label": best.get("label") or best.get("symbol") or "",
        "sector": best.get("sector") or "",
        "score": DASH if score is None else f"{score:.2f}",
        "percentile": DASH if pct is None else f"{pct:.0f}",
        "delta": DASH if delta is None else
                 ("0" if delta == 0 else
                  (f"+{delta}" if delta > 0 else f"{MINUS}{abs(delta)}")),
        "delta_positive": bool(delta and delta > 0),
        "quadrant": quadrant_of(best),
        "align_blocks": align,
        "align_text": (f"{sum(align)} of {len(align)} align" if align else ""),
        "components": best.get("components") or {},
    }


def component_bars(components):
    """One diverging bar per component, centred on the universe average.

    Centred rather than left-anchored because these are z-scores: the sign is
    the reading, and a bar growing from the left would render −2 and +2 as
    "small" and "large" instead of "opposite"."""
    comp = components or {}
    out = []
    for key in COMPONENT_ORDER:
        v = _num(comp.get(key))
        t = 0.0 if v is None else min(1.0, abs(v) / Z_CAP)
        positive = bool(v is not None and v >= 0)
        out.append({
            "key": key, "label": COMPONENT_LABEL[key],
            "meaning": COMPONENT_MEANING[key],
            "value": v, "positive": positive,
            "text": DASH if v is None else
                    (f"+{v:.2f}" if v >= 0 else f"{MINUS}{abs(v):.2f}"),
            "left_pct": 50.0 if positive else 50.0 - t * 50.0,
            "width_pct": t * 50.0,
        })
    return out


# ── 5 · the rank chart ───────────────────────────────────────────────────────
RANK_SERIES = 5
LINE_W = (1.4, 2.6)          # ordinary series, highlighted series
RANK_TICK_TARGET = 5


def _points(history):
    """``[(date, rank)]`` for one symbol, dropping anything unreadable."""
    out = []
    for p in history or []:
        try:
            date, rank = p[0], _num(p[1])
        except (TypeError, IndexError):
            continue
        if date and rank is not None:
            out.append((str(date), rank))
    return out


def rank_chart(rank_history, n=RANK_SERIES):
    """Series, shared date axis and rank domain for the rank-over-time chart.

    **Every series is placed on ONE shared date axis.** The live histories are
    ragged — symbols carry 15, 10, 7 or 5 sessions — and indexing each series
    by its own position would stretch a five-session symbol across the full
    width and draw it as a full-length trend.

    **The rank domain is computed**, not capped: live ranks run past 60, and the
    name climbing from deep is usually the most interesting thing on the page.
    """
    hist = {k: _points(v) for k, v in (rank_history or {}).items()}
    hist = {k: v for k, v in hist.items() if len(v) >= 2}
    if not hist:
        return {"series": [], "dates": [], "rank_lo": 1, "rank_hi": 1}

    dates = sorted({d for pts in hist.values() for d, _r in pts})
    idx = {d: i for i, d in enumerate(dates)}
    span = max(1, len(dates) - 1)

    climbs = {k: v[0][1] - v[-1][1] for k, v in hist.items()}
    climber = max(climbs, key=lambda k: climbs[k])
    if climbs[climber] <= 0:
        climber = None
    leaders = sorted(hist, key=lambda k: hist[k][-1][1])
    chosen = ([climber] if climber else []) + [k for k in leaders if k != climber]
    chosen = chosen[:max(1, n)]

    ranks = [r for k in chosen for _d, r in hist[k]]
    lo, hi = 1, max(ranks)
    rspan = max(1e-9, hi - lo)

    series = []
    for sym in chosen:
        pts = [(idx[d] / span * 100.0, (r - lo) / rspan * 100.0)
               for d, r in hist[sym]]
        series.append({
            "symbol": sym, "points": pts,
            "highlight": sym == climber,
            "width": LINE_W[1] if sym == climber else LINE_W[0],
            "first_rank": hist[sym][0][1], "last_rank": hist[sym][-1][1],
        })
    return {"series": series, "dates": dates, "rank_lo": lo, "rank_hi": hi}


def rank_ticks(chart):
    """Rank gridline labels across the computed domain, 1 always included."""
    lo, hi = chart.get("rank_lo", 1), chart.get("rank_hi", 1)
    if hi <= lo:
        return [{"rank": lo, "y_pct": 0.0}]
    step = max(1, round((hi - lo) / max(1, RANK_TICK_TARGET - 1)))
    out, r = [], lo
    while r <= hi:
        out.append({"rank": int(r), "y_pct": (r - lo) / (hi - lo) * 100.0})
        r += step
    # Always label the bottom of the axis. Without it a 1..272 domain can stop
    # labelling at 205, and a reader takes the chart's floor to be 205.
    if out[-1]["rank"] < hi:
        out.append({"rank": int(hi), "y_pct": 100.0})
    return out


def rank_svg(chart):
    """The rank lines as one SVG string for ``ui.html``.

    Percentage-addressed ``<line>``s rather than the reference's ``<polyline>``
    in a scaled viewBox: ``points`` cannot take percentages, and the attribute
    that rescues stroke width under a non-uniform viewBox
    (``vector-effect``) is stripped by DOMPurify — verified against the shipped
    copy — so the lines would render stretched."""
    parts = []
    for s in chart.get("series") or []:
        stroke = (oklch_hex(0.72, 0.13, 232) if s["highlight"]
                  else oklch_hex(0.42, 0.01, 90))
        for (x1, y1), (x2, y2) in zip(s["points"], s["points"][1:]):
            parts.append(
                f'<line x1="{x1:.2f}%" y1="{y1:.2f}%" x2="{x2:.2f}%" '
                f'y2="{y2:.2f}%" stroke="{stroke}" '
                f'stroke-width="{s["width"]:.2f}" stroke-linecap="round"/>')
    if not parts:
        return ""
    return '<svg width="100%" height="100%">' + "".join(parts) + "</svg>"


def rank_story(chart):
    """One sentence on the clearest move, or "" when nothing has moved.

    Silence is the honest output on a flat board — a manufactured "highlight"
    on a day nobody moved is worse than no sentence at all."""
    for s in chart.get("series") or []:
        if not s["highlight"]:
            continue
        gain = s["first_rank"] - s["last_rank"]
        if gain <= 0:
            return ""
        return (f"{s['symbol']} has climbed from {ordinal(int(s['first_rank']))} "
                f"to {ordinal(int(s['last_rank']))} over the sessions shown — "
                "the clearest move on this chart.")
    return ""


# ── the limits cards ─────────────────────────────────────────────────────────
# Stated on the page rather than buried in a manual, because every one of them
# is a way this screen can be confidently wrong.
LIMITS = [
    ("Nightly",
     "Recomputed once at 16:20 CT on daily bars. It cannot react to today's news."),
    ("Coarse sample",
     "Participation is measured on a handful of constituents per industry."),
    ("Blind spots",
     "Says nothing about valuation or event risk — an earnings date overrides "
     "everything here."),
    ("Weekends",
     "Shows the last computed session, so Monday morning still reads Friday."),
]


# ── the quadrant palette ─────────────────────────────────────────────────────
# Same four hues as the rotation screens, so a quadrant means one colour across
# the whole nav group.
QUAD_CLASSES = {
    q: {
        "panel": f"bg-[{oklch_hex(0.135, QUAD_CHROMA[q] * 0.16, QUAD_HUE[q])}]",
        "edge": f"border-[{oklch_hex(0.26, QUAD_CHROMA[q] * 0.4, QUAD_HUE[q])}]",
        "dot": f"bg-[{oklch_hex(0.70, QUAD_CHROMA[q], QUAD_HUE[q])}]",
        "title": f"text-[{oklch_hex(0.84, QUAD_CHROMA[q], QUAD_HUE[q])}]",
        "bar": f"bg-[{oklch_hex(0.62, QUAD_CHROMA[q], QUAD_HUE[q])}]",
        "chip": f"bg-[{oklch_hex(0.22, QUAD_CHROMA[q] * 0.35, QUAD_HUE[q])}]",
        "chip_txt": f"text-[{oklch_hex(0.86, QUAD_CHROMA[q] * 0.5, QUAD_HUE[q])}]",
    }
    for q in QUAD_ORDER
}

REGIME_CLASSES = {
    s: {
        "panel": f"bg-[{oklch_hex(0.20, REGIME_CHROMA[s] * 0.35, REGIME_HUE[s])}]",
        "edge": f"border-[{oklch_hex(0.62, REGIME_CHROMA[s] * 0.85, REGIME_HUE[s])}]",
        "dot": f"bg-[{oklch_hex(0.80, REGIME_CHROMA[s], REGIME_HUE[s])}]",
        "title": f"text-[{oklch_hex(0.86, REGIME_CHROMA[s] * 0.92, REGIME_HUE[s])}]",
        "action": f"text-[{oklch_hex(0.80, REGIME_CHROMA[s] * 0.77, REGIME_HUE[s])}]",
        "dim_dot": f"bg-[{oklch_hex(0.34, REGIME_CHROMA[s] * 0.42, REGIME_HUE[s])}]",
        "dim_title": f"text-[{oklch_hex(0.50, REGIME_CHROMA[s] * 0.28, REGIME_HUE[s])}]",
    }
    for s in REGIME_ORDER
}

POS_TXT = f"text-[{oklch_hex(0.84, 0.09, 158)}]"
NEG_TXT = f"text-[{oklch_hex(0.80, 0.11, 22)}]"
POS_BAR = f"bg-[{oklch_hex(0.70, 0.12, 158)}]"
NEG_BAR = f"bg-[{oklch_hex(0.66, 0.15, 22)}]"
ALIGN_ON = f"bg-[{oklch_hex(0.72, 0.13, 158)}]"
ALIGN_OFF = f"bg-[{oklch_hex(0.24, 0.01, 90)}]"
HILITE_TXT = f"text-[{oklch_hex(0.84, 0.11, 232)}]"
LEVEL_FILL = f"bg-[{oklch_hex(0.68, 0.11, 232)}]"
LEVEL_TRACK = f"bg-[{oklch_hex(0.26, 0.006, 90)}]"
LEVEL_GROOVE = f"bg-[{oklch_hex(0.165, 0.006, 90)}]"
