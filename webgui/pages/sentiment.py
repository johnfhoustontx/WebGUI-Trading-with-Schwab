"""Sentiment page — Day/Week/Month rings + components + intraday + trend regime.

Tier-3 reader: this page holds **no engine calls, no refresh loop, and no app
``scoring``/``live_composite`` imports**. All scoring-derived values (component
weights, size/bias/signal band, velocity, divergence, trend regime, cap-weighted
sector pct/score) are computed in ``services/sentiment_svc`` — the only process
where ``import scoring`` resolves to sentiment's package rather than the
options-scanner ``scoring.py`` (the documented cross-app collision). The service
places them in the cache; this page only **formats** them. Cache views read:

* ``sentiment:composite`` → ``{"live", "composite_at", "proxy_up", "derived"}``
  where ``derived`` = ``{"weights", "size", "bias", "signal", "velocity",
  "divergence", "trend"}`` (see ``compute.derive_composite_extras``).
* ``sentiment:history``   → ``{"snaps", "spy"}``
* ``sentiment:sectors``   → ``{"sector", "industries", "sector_at", "summary"}``
  where ``summary`` = ``{"wpct", "score"}`` (see ``compute.derive_sector_summary``).

The pure display transforms (``traffic_color``, ``composite_series`` for the
history figure, table/figure builders, …) are unit-tested. ``render()`` wires
widgets, a Refresh button that enqueues a ``cmd:sentiment`` command, and a
fetch-free version-poll ``ui.timer`` that repaints when the bus cache version
changes.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import bus_client
from pages.rings import ring_svg
from pages.options.theme import BTN_3D, THEME
from pages.ui_guard import guard

_CT = ZoneInfo("America/Chicago")  # trading session clock for the intraday graphs

# The page's 5-color value palette now comes from config/theme.toml [charts]
# (edit + restart the webgui to restyle) — defaults preserve the historical look:
# green #66bb6a / red #ef5350 / yellow #ffd54f / flat #9e9e9e / cyan #3fb6c7.
_CH = THEME["charts"]
CLR_GREEN = _CH["green"]
CLR_RED = _CH["red"]
CLR_YELLOW = _CH["yellow"]
CLR_FLAT = _CH["flat"]
CLR_CYAN = _CH["cyan"]

# LOCAL Tailwind class maps (Phase 5), generated from the same palette. These are
# the page's OWN 5-color vocabulary — the yellow/cyan have no theme TXT_* token and
# the flat differs from the theme neutral, so they are intentionally NOT the shared
# theme tokens. The `CLR_*` hex constants above feed the Highcharts figures +
# non-`.classes()` callers; the `*_class` helpers below return the Tailwind class
# string for `.classes()`.
TXT_G = f"text-[{CLR_GREEN}]"
TXT_R = f"text-[{CLR_RED}]"
TXT_Y = f"text-[{CLR_YELLOW}]"
TXT_FLAT = f"text-[{CLR_FLAT}]"
TXT_CY = f"text-[{CLR_CYAN}]"
BG_G = f"bg-[{CLR_GREEN}]"
BG_R = f"bg-[{CLR_RED}]"
BG_Y = f"bg-[{CLR_YELLOW}]"
# Remove-sets for reactive in-place recolors (these MUST cover every class the
# element can apply, or colors stack across the page's ~2s auto-refresh).
SENT_TEXT_CLASSES = " ".join([TXT_G, TXT_R, TXT_Y, TXT_FLAT, TXT_CY])
TRAFFIC_BG_CLASSES = " ".join([BG_G, BG_R, BG_Y])

# Map a local hex -> its Tailwind text class (for helpers that already return a hex).
_HEX_TO_TXT = {CLR_GREEN: TXT_G, CLR_RED: TXT_R, CLR_YELLOW: TXT_Y,
               CLR_FLAT: TXT_FLAT, CLR_CYAN: TXT_CY}
# Map a local hex -> its Tailwind bg class (only the three traffic bands have a bg).
_HEX_TO_TXT_BG = {CLR_GREEN: BG_G, CLR_RED: BG_R, CLR_YELLOW: BG_Y}
# Per-cell right divider for the sector/industry table rows (last cell omits it).
BORDER_R = "border-r border-white/[0.04]"

# (component_scores key, display name). Weights are NO LONGER baked from app
# ``scoring`` at import — they arrive at render time via the cached
# ``derived["weights"]`` (computed in the service). A component with no weight
# in that dict (or weight 0) is treated as out-of-composite (e.g. credit_pulse).
COMPONENTS = [
    ("vix_complex", "VIX Complex"),
    ("put_call",    "Put/Call (sectors)"),
    ("breadth",     "Market Breadth"),
    ("rotation",    "Rotation"),
    ("sector_perf", "Sector Performance"),
    ("credit_pulse", "Credit Pulse"),
]


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def traffic_color(total):
    """Composite traffic-light band for tile backgrounds.
    >=6.5 green, <=4.5 red, else amber. Mirrors source _update_metric_card_colors."""
    v = _safe_float(total, 5.0)
    if v >= 6.5:
        return CLR_GREEN
    if v <= 4.5:
        return CLR_RED
    return CLR_YELLOW


def traffic_bg_class(total):
    """Composite traffic-light band as a Tailwind bg class (tile backgrounds)."""
    return _HEX_TO_TXT_BG[traffic_color(total)]


def gauge_score(total):
    """0-10 composite -> 0-100 for the speedometer gauge."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# Short state words, one per trend horizon. These used to caption the two trend
# gauge faces; the rings carry only numbers, so the words now live on the Trend
# Detail popup's per-horizon line (``_apply``). That line is the ONLY place the
# Week/Month state word survives — the regime badge below the ring names the Day
# horizon alone. Covers BOTH the five-state (direction x aggression) vocab the
# intraday horizons publish AND the old trend-band vocab the 30-day structural
# read still uses.
_TREND_SHORT = {
    # new five-state vocab (intraday horizons)
    "bullish": "Bull", "lack_of_bullishness": "Weak Bull", "neutral": "Neutral",
    "lack_of_bearishness": "Resilient", "bearish": "Bear",
    # old trend-band vocab (30-day structural read)
    "bull_trend": "BULL", "pullback_in_bull": "PULLBACK",
    "range": "RANGE", "bear_rally": "BEAR RALLY", "bear_trend": "BEAR"}

# Market Trend sub-score display metadata (name + weight). Mirrors the service's
# TREND_WEIGHTS — kept local so the page imports no engine (3-tier rule).
_TREND_SUB_META = [("price", "Price / MTF", "45%"), ("breadth", "Breadth", "25%"),
                   ("sector", "Sector", "20%"), ("vix", "VIX", "10%")]


def trend_gauge_value(trend):
    """0-100 needle = the intraday trend score directly (smoothed if present)."""
    t = trend or {}
    v = t.get("smoothed_score", t.get("score"))
    if v is None:
        return 50.0
    return _clamp(_safe_float(v, 50.0), 0.0, 100.0)


def trend_subscore_rows(trend):
    """Display rows [{name, score, weight, conf}] for the Market Trend sub-score
    popup; skips sub-scores absent from the payload (e.g. the 30-day variant has
    only price+sector)."""
    t = trend or {}
    scores = t.get("sub_scores") or {}
    confs = t.get("sub_confidence") or {}
    rows = []
    for key, name, weight in _TREND_SUB_META:
        if key not in scores:
            continue
        rows.append({"name": name, "score": f"{_safe_float(scores.get(key)):.1f}",
                     "weight": weight, "conf": f"{_safe_float(confs.get(key)):.2f}"})
    return rows


def bias_color(bias):
    b = (bias or "").lower()
    if "bull" in b:
        return CLR_GREEN
    if "bear" in b:
        return CLR_RED
    return CLR_YELLOW


# Tone key -> plain text class. The Signals tiles add their own glow on top;
# this is the un-glowed form, for running text like the headline under the ring.
_TONE_TXT = {"pos": TXT_G, "neg": TXT_R, "warn": TXT_Y, "flat": TXT_FLAT}


def bias_text_class(bias):
    """Text colour for a positioning/strength word.

    Delegates to ``_word_tone`` rather than ``bias_color``: the headline under
    the Sentiment ring shows the SAME word as the BIAS tile, and ``bias_color``
    only substring-matches bull/bear — so "Long" and "Short" fell through to
    amber while the tile beside them read green and red. Same word, same screen,
    two colours. The bull/bear/neutral buckets its own tests pin are unchanged;
    this only adds the positioning vocabulary ``signal_band`` actually emits."""
    return _TONE_TXT[_word_tone(bias)]


# State -> text-color class, covering BOTH the new five-state vocab (Today gauge)
# and the old trend-band vocab (30-day structural gauge). Unlisted -> amber.
_TREND_STATE_CLASS = {
    # new five-state vocab
    "bullish": TXT_G, "lack_of_bearishness": TXT_G,
    "bearish": TXT_R,
    "lack_of_bullishness": TXT_Y, "neutral": TXT_Y,
    # old trend-band vocab
    "bull_trend": TXT_G, "pullback_in_bull": TXT_G,
    "bear_rally": TXT_R, "bear_trend": TXT_R,
    "range": TXT_Y}


def trend_text_class(committed):
    """Tailwind text class for a committed trend state (both vocabularies)."""
    return _TREND_STATE_CLASS.get(committed, TXT_Y)


def market_state_evidence_rows(trend):
    """Evidence strings explaining WHY the new five-state trend was chosen
    (e.g. "direction 75/100", "aggression -0.37"). Defensive: [] when absent."""
    return (trend or {}).get("evidence") or []


def rotation_text_class(color):
    """Map a rotation_banner() hex color -> Tailwind text class."""
    return _HEX_TO_TXT.get(color, TXT_FLAT)


def sc_text_class(score):
    """Component score color: >=7 green, <4 red, else amber (mirrors _render_components)."""
    sc = _safe_float(score)
    if sc >= 7:
        return TXT_G
    if sc < 4:
        return TXT_R
    return TXT_Y


def composite_series(snapshots):
    """(dates, scores) for snapshots with a positive composite total."""
    dates, scores = [], []
    for s in snapshots:
        v = _safe_float((s.get("composite") or {}).get("total_score"))
        if v > 0:
            dates.append(s.get("date"))
            scores.append(v)
    return dates, scores


# Trading days averaged into the concentric ring's "Week" arc. The backfill
# history is one snapshot per COMPLETED session, so a week is 5 rows, not 7.
WEEK_SNAPS = 5


def sentiment_avg_or_none(snaps, n=None):
    """Mean composite over the last ``n`` snapshots (all of them when ``n`` is
    None), or None when the history holds no scored session at all. Pure.

    None is the ring's "no data" reading — distinct from a real 0.0, which the
    ring would draw as a genuine (maximally bearish) value.

    Non-finite scores are DROPPED rather than averaged. ``composite_series``
    already loses NaN to its ``v > 0`` filter, but inf survives it — and inf
    reaches a caller as a *clamped* 100.0, a confident full arc indistinguishable
    from a real maximum. These payloads cross Redis as JSON and Python's ``json``
    both emits and accepts ``Infinity``/``NaN``, so a service-side division by
    zero round-trips intact."""
    scores = [s for s in composite_series(snaps or [])[1] if math.isfinite(s)]
    if n is not None:
        scores = scores[-n:]
    return round(sum(scores) / len(scores), 2) if scores else None


def sentiment_avg(snaps, n=None):
    """``sentiment_avg_or_none`` with a 0.0 floor. Pure.

    Currently called only by its own tests: its last production caller was the
    30-Day-Avg speedometer, and the rings deliberately take the ``_or_none``
    variant so a horizon with no scored session draws a bare track instead of a
    0 they would paint as a genuine maximally-bearish reading. Kept anyway —
    it is a distinct, tested contract (floored, not nullable) that the ring
    horizons or a later tile may well want, and keeping one small function
    costs less than deleting and restoring it."""
    v = sentiment_avg_or_none(snaps, n)
    return 0.0 if v is None else v


def _composite_arc_value(snapshot):
    """A composite snapshot's 0-100 arc value, or None when its score is
    non-finite. Pure.

    The Day arc needs the same non-finite guard ``sentiment_avg_or_none`` gives
    Week and Month, and for the same reason: ``gauge_score`` ends in
    ``min(100.0, x)``, and ``min(100.0, nan)`` is **100.0** — so a NaN or inf
    total_score painted a confident FULL arc, indistinguishable from a real
    maximum. inf reached it too (``inf * 10`` clamps to 100.0).

    Reachable, not hypothetical: these payloads cross Redis as JSON, and
    Python's ``json`` both emits and accepts ``Infinity``/``NaN``, so a
    service-side division by zero round-trips intact into either the ``live``
    key or a history snapshot. Without this, one poisoned snapshot made a single
    call contradict itself — DAY 100.0 beside WEEK/MONTH None, off the same row.

    An UNPARSEABLE score reads None for the same reason. ``_safe_float``'s 0.0
    default would paint junk as a genuine maximally-BEARISH full arc — the mirror
    of the NaN case above, and the same self-contradiction one input to the left
    ('n/a' gave DAY 0.0 beside WEEK/MONTH None). This keeps Day symmetric with
    ``_trend_arc_value``, which already passes ``None`` as its default."""
    if snapshot is None:
        return None
    v = _safe_float((snapshot.get("composite") or {}).get("total_score"), None)
    return gauge_score(v) if v is not None and math.isfinite(v) else None


def sentiment_arcs(live, snaps):
    """Day/Week/Month arcs for the Market Sentiment ring (composite 0-10 -> 0-100).

    The Day reading picks live-over-backfill exactly as ``_apply`` does, so the
    ring's outer arc always agrees with the headline. A horizon with no scored
    session reads None — the ring then draws that arc's track only, rather than
    a 0 it would paint as a genuine maximally-bearish value."""
    latest = live or (snaps[-1] if snaps else None)
    day = _composite_arc_value(latest)
    week = sentiment_avg_or_none(snaps, WEEK_SNAPS)
    month = sentiment_avg_or_none(snaps)
    return [
        {"value": day, "caption": "DAY"},
        {"value": None if week is None else gauge_score(week), "caption": "WEEK"},
        {"value": None if month is None else gauge_score(month), "caption": "MONTH"},
    ]


def _trend_arc_value(trend):
    """A trend horizon's 0-100 arc value, or None when it carries no score.

    The invariant: this returns None in precisely the cases where
    ``trend_gauge_value`` would invent a value — an absent horizon, an empty
    payload, one published without a score, or one whose score is unparseable
    (both of that function's 50.0 fallbacks) or non-finite (which ``_clamp``
    silently turns into 100.0, since ``min(100.0, nan)`` is 100.0). A gauge
    needs a needle position and so must invent one; a ring can say nothing.

    ZERO CONFIDENCE is the fourth case, and the one that actually fires in
    production. The service's failure path does NOT omit the horizon — both
    ``_neutral_trend`` and ``_neutral_structural_trend`` return a fully shaped
    dict carrying **score 50.0, confidence 0.0**, and ``compute_7d_trend`` /
    ``compute_30d_trend`` swallow their own exceptions to return exactly that.
    So on any proxy blip a good reading is replaced by a confident-looking 50,
    and the absent-key guards above never see it. Keying on confidence is what
    catches it.

    Confidence is a SOUND discriminator here, verified rather than assumed:
    ``blend_trend`` weights each sub-score by its own confidence, so the
    aggregate rounds to 0.0 only when there was no usable evidence at all — a
    genuinely neutral but well-evidenced read (50/50 at full confidence) scores
    agg 0.65 and passes straight through. Note it is "rounds to zero", not "all
    sub-confidences are exactly zero": a lone 0.001 sub-confidence also lands on
    0.0, which is the right call — a reading that confident is not one to paint."""
    t = trend or {}
    if _safe_float(t.get("confidence"), None) == 0.0:
        return None
    v = _safe_float(t.get("smoothed_score", t.get("score")), None)
    return None if v is None or not math.isfinite(v) else _clamp(v, 0.0, 100.0)


def trend_arcs(derived):
    """Day/Week/Month arcs for the Market Trend ring (already 0-100).

    A horizon with no usable reading draws its track only, rather than a
    fabricated neutral 50. That covers an absent key (``trend_7d`` before a
    sentiment_svc restart carrying it) AND — the case that actually recurs — a
    horizon the service published as a zero-confidence neutral after a fetch
    failure. See ``_trend_arc_value``."""
    d = derived or {}
    return [{"value": _trend_arc_value(d.get("trend")), "caption": "DAY"},
            {"value": _trend_arc_value(d.get("trend_7d")), "caption": "WEEK"},
            {"value": _trend_arc_value(d.get("trend_30d_ago")), "caption": "MONTH"}]


# Break the intraday line where consecutive RTH points are more than this far
# apart (ms) — i.e. across the overnight gap from the prior day's ~15:00 CT close
# to the next day's ~08:30 CT open. 4h is safely above any intra-session recording
# gap and well below the ~17.5h overnight, so each trading day renders as its own
# segment with a small gap between days.
_INTRADAY_GAP_MS = 4 * 60 * 60 * 1000


def _intraday_figure(points, *, value_key, y_max, y_title, zones, scale=1.0):
    """Shared Highcharts options for a 2-min intraday value series, colorized by
    value via series.zones. RTH-only data (the service records only 08:30–15:00 CT).
    ``scale`` rescales the value (e.g. 0.1 shows the 0-100 trend on a 0-10 axis).

    Renders on a **synthetic integer-index (category) x-axis**, NOT a datetime axis:
    each RTH point gets the next sequential slot, so trading days pack CONTIGUOUSLY
    with only a 1-slot gap between them — no overnight/weekend dead space. A date
    label sits at each day boundary (``tickPositions`` + a category per slot), and the
    real Central-Time date+time lives in each point's ``name`` (shown in the tooltip).
    A NULL slot between days breaks the line into per-day segments.

    Why not the obvious approaches: a Highstock stockChart's ordinal axis collapses
    the dead space natively but its ``chart.update()`` throws in the stock module,
    FREEZING in-place updates (an open page never draws the current day); broken-axis
    (``xAxis.breaks``) collapses the gap but renders zero ticks (no labels). A plain
    category axis updates reliably AND has no dead space."""
    pts = points or []
    # Build the series + the per-slot category labels together. Every slot (real point
    # OR the null gap between days) advances the index by 1, so days are contiguous.
    data, categories, tick_positions = [], [], []
    idx = 0
    prev_ms = None
    prev_date = None
    for p in pts:
        ms = int(_safe_float(p.get("ts"))) * 1000
        ct = datetime.fromtimestamp(ms / 1000, _CT)
        day = ct.date()
        if prev_ms is not None and (ms - prev_ms) > _INTRADAY_GAP_MS:
            # A null slot breaks the line + leaves a small gap between day segments.
            data.append({"x": idx, "y": None})
            categories.append("")
            idx += 1
        if day != prev_date:                       # first slot of a new day → labeled tick
            tick_positions.append(idx)
            categories.append(f"{ct:%b} {ct.day}")   # e.g. "Jul 6"
            prev_date = day
        else:
            categories.append("")
        data.append({"x": idx, "y": _safe_float(p.get(value_key)) * scale,
                     "name": f"{ct:%b} {ct.day}, {ct:%H:%M}"})   # tooltip date+time (CT)
        idx += 1
        prev_ms = ms
    axis_label = {"style": {"color": "#bdbdbd"}}
    return {
        "chart": {"type": "line", "backgroundColor": "transparent",
                  "height": 200, "spacing": [8, 12, 8, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        # Category axis: contiguous slots (no dead space); ticks/labels only at the
        # day boundaries. Date+time per point is carried in point.name (tooltip).
        "xAxis": {"categories": categories, "tickPositions": tick_positions,
                  "lineColor": "rgba(255,255,255,0.15)",
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label,
                  "crosshair": {"label": {"enabled": False}}},
        "yAxis": {"min": 0, "max": y_max,
                  "title": {"text": y_title, "style": {"color": "#bdbdbd"}},
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label},
        "tooltip": {"headerFormat": "",
                    "pointFormat": "{point.name}<br>" + y_title + ": <b>{point.y:.2f}</b>"},
        "series": [{
            "name": y_title, "type": "line", "data": data,
            "lineWidth": 2, "zoneAxis": "y", "zones": zones,
            "marker": {"enabled": False},
        }],
    }


def build_sentiment_intraday_figure(points):
    """Daily Market Sentiment (0-10), colorized by traffic bands."""
    zones = [{"value": 4.5, "color": CLR_RED},
             {"value": 6.5, "color": CLR_YELLOW},
             {"color": CLR_GREEN}]
    return _intraday_figure(points, value_key="sentiment", y_max=10,
                            y_title="Sentiment", zones=zones)


def build_trend_intraday_figure(points):
    """Daily Market Trend on a 0-10 scale (same as sentiment) — the stored 0-100
    trend is rescaled ×0.1; colorized by the 3/7 range boundaries (the 30/70
    trend-state cuts on the 0-10 scale)."""
    zones = [{"value": 3, "color": CLR_RED},
             {"value": 7, "color": CLR_YELLOW},
             {"color": CLR_GREEN}]
    return _intraday_figure(points, value_key="trend", y_max=10,
                            y_title="Trend", zones=zones, scale=0.1)


# --- Market Regime (blended structural regime, cache:sentiment:regime) -------
# Display order + labels + colors for the five structural regimes. The order is
# fixed so the stacked band keeps a stable reading position across repaints.
REGIME_ORDER = ("mean_reversion", "trending", "breakout", "choppy", "crisis")
# The BASE names — deliberately the chart's series names too, because the fixed
# order + stable names ARE the band's reading position. Direction adorns the
# HEADLINE only (a legend that renames itself mid-session defeats the point).
# Mirrors ``sentiment-dashboard/scoring/market_regime.REGIME_DISPLAY``, which the
# webgui cannot import (Tier-1 takes no engine imports).
REGIME_LABELS = {"mean_reversion": "Balanced", "trending": "Trending",
                 "breakout": "Breakout", "choppy": "Whipsaw", "crisis": "Stressed"}
_REGIME_DIRECTIONAL = {
    "trending": {(1, True): "Rallying", (1, False): "Firming",
                 (-1, True): "Retreating", (-1, False): "Softening"},
    "breakout": {(-1, True): "Breakdown", (-1, False): "Breakdown"},
}
REGIME_COLORS = {"mean_reversion": CLR_CYAN, "trending": CLR_GREEN,
                 "breakout": CLR_YELLOW, "choppy": CLR_FLAT, "crisis": CLR_RED}
# Headline text color by committed regime — a finite map (Tailwind-first rule).
_REGIME_TEXT = {"trending": "text-[#66bb6a]", "breakout": "text-[#ffd54f]",
                "mean_reversion": "text-[#3fb6c7]", "choppy": "text-[#9e9e9e]",
                "crisis": "text-[#ef5350]"}
# ... but a DIRECTIONAL regime takes its colour from the direction instead: the
# fixed green would paint "Retreating" as though it were bullish.
_DIRECTION_TEXT = {1: "text-[#66bb6a]", -1: "text-[#ef5350]"}


def regime_direction(regime):
    """(direction, strong) off a regime payload. Junk, or a payload predating the
    field, reads neutral — the base label then renders exactly as before."""
    r = regime if isinstance(regime, dict) else {}
    d = r.get("direction")
    d = d if d in (-1, 0, 1) and not isinstance(d, bool) else 0
    return d, r.get("direction_strong") is True


def regime_label(key, direction=0, strong=False):
    """Display name for a regime key, adorned with the direction where one
    applies (Balanced / Whipsaw / Stressed are directionless by construction)."""
    base = REGIME_LABELS.get(key)
    if base is None:
        return ""
    words = _REGIME_DIRECTIONAL.get(key)
    if not words or direction not in (-1, 1):
        return base
    return words.get((direction, bool(strong)), base)
# Space-separated STRING (not a list) — that is what NiceGUI's
# ``.classes(remove=...)`` splits on; a list raises AttributeError at render.
REGIME_TEXT_CLASSES = " ".join(dict.fromkeys(list(_REGIME_TEXT.values())
                                             + ["text-[#9e9e9e]"]))


def regime_headline_parts(regime):
    """(label, confidence_text, text_class) for the Market Regime headline.

    An absent payload reads as a waiting placeholder; an ``unclear`` sample keeps
    its "Unclear" label (the service never fabricates a regime it can't see).

    The label is re-derived here from ``(committed_label, direction)`` rather than
    echoing the payload's ``label``, so a held/stale sample can never outlive a
    rename; the payload's own word is the fallback for an unknown key."""
    r = regime if isinstance(regime, dict) else {}
    key = "" if r.get("unclear") else (r.get("committed_label") or "")
    direction, strong = regime_direction(r)
    label = (regime_label(key, direction, strong) or r.get("label")
             or ("Waiting for regime…" if not r else "Unclear"))
    conf = r.get("confidence")
    conf_txt = f"{float(conf) * 100:.0f}% confidence" if isinstance(
        conf, (int, float)) and not isinstance(conf, bool) else ""
    if r.get("unclear"):
        cls = "text-[#9e9e9e]"
    elif key in _REGIME_DIRECTIONAL and direction in _DIRECTION_TEXT:
        cls = _DIRECTION_TEXT[direction]
    else:
        cls = _REGIME_TEXT.get(key, "text-[#9e9e9e]")
    return label, conf_txt, cls


def regime_transition_text(regime):
    """'Balanced → Rallying · 60%' while a regime is handing over, else ''.

    Blank when stable, so the row simply hides — the whole point of the blended
    model is that this reads gradually instead of flipping."""
    r = regime if isinstance(regime, dict) else {}
    tr = r.get("transition")
    if not isinstance(tr, dict) or not tr.get("from") or not tr.get("to"):
        return ""
    direction, strong = regime_direction(r)
    frm = regime_label(tr["from"], direction, strong) or str(tr["from"])
    to = regime_label(tr["to"], direction, strong) or str(tr["to"])
    prog = tr.get("progress")
    pct = (f" · {float(prog) * 100:.0f}%"
           if isinstance(prog, (int, float)) and not isinstance(prog, bool) else "")
    return f"{frm} → {to}{pct}"


def regime_evidence_rows(regime):
    """The 'why' lines the classifier attached to this sample (may be empty)."""
    r = regime if isinstance(regime, dict) else {}
    return [str(e) for e in (r.get("evidence") or [])]


def build_regime_mix_figure(points):
    """Stacked-area membership mix over today's recorded regime samples.

    Reads as "how much of each regime is in this tape, and which way is it
    moving" — the blended model's whole premise. Percent-stacked so the bands
    always fill the height and the eye tracks PROPORTION, not absolute scale.

    Same synthetic contiguous category axis as ``_intraday_figure`` (and the same
    reason: a stockChart's ``chart.update()`` throws, freezing an open page), with
    a null slot breaking the bands between sessions."""
    pts = points or []
    series_data = {k: [] for k in REGIME_ORDER}
    categories, tick_positions = [], []
    idx, prev_ms, prev_date = 0, None, None
    for p in pts:
        ms = int(_safe_float(p.get("ts"))) * 1000
        ct = datetime.fromtimestamp(ms / 1000, _CT)
        day = ct.date()
        if prev_ms is not None and (ms - prev_ms) > _INTRADAY_GAP_MS:
            for k in REGIME_ORDER:                 # null slot → break every band
                series_data[k].append({"x": idx, "y": None})
            categories.append("")
            idx += 1
        if day != prev_date:
            tick_positions.append(idx)
            categories.append(f"{ct:%b} {ct.day}")
            prev_date = day
        else:
            categories.append("")
        mem = p.get("memberships") if isinstance(p.get("memberships"), dict) else {}
        for k in REGIME_ORDER:
            series_data[k].append({"x": idx, "y": _safe_float(mem.get(k)),
                                   "name": f"{ct:%b} {ct.day}, {ct:%H:%M}"})
        idx += 1
        prev_ms = ms
    axis_label = {"style": {"color": "#bdbdbd"}}
    return {
        "chart": {"type": "area", "backgroundColor": "transparent",
                  "height": 220, "spacing": [8, 12, 8, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#bdbdbd",
                                                  "fontWeight": "normal"}},
        "xAxis": {"categories": categories, "tickPositions": tick_positions,
                  "lineColor": "rgba(255,255,255,0.15)",
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label,
                  "crosshair": {"label": {"enabled": False}}},
        "yAxis": {"min": 0, "max": 100, "title": {"text": "Regime mix",
                                                  "style": {"color": "#bdbdbd"}},
                  "gridLineColor": "rgba(255,255,255,0.06)",
                  "labels": {**axis_label, "format": "{value}%"}},
        "tooltip": {"shared": True, "valueDecimals": 0,
                    "pointFormat": "{series.name}: <b>{point.percentage:.0f}%</b><br>"},
        "plotOptions": {"area": {"stacking": "percent", "lineWidth": 1,
                                 "marker": {"enabled": False},
                                 "fillOpacity": 0.55}},
        "series": [{"name": REGIME_LABELS[k], "type": "area",
                    "color": REGIME_COLORS[k], "data": series_data[k]}
                   for k in REGIME_ORDER],
    }


def pct_color(pct):
    """Green up / red down / gray flat (|pct| < 0.05)."""
    if pct is None or abs(float(pct)) < 0.05:
        return CLR_FLAT
    return CLR_GREEN if float(pct) > 0 else CLR_RED


def pct_text_class(pct):
    return _HEX_TO_TXT[pct_color(pct)]


def pcr_color(pcr):
    """<0.95 call-dominated green, >1.05 put-dominated red, else flat."""
    if pcr is None or float(pcr) <= 0:
        return CLR_FLAT
    if float(pcr) < 0.95:
        return CLR_GREEN
    if float(pcr) > 1.05:
        return CLR_RED
    return CLR_FLAT


def pcr_text_class(pcr):
    return _HEX_TO_TXT[pcr_color(pcr)]


def rrg_color(quadrant):
    return {
        "Leading": CLR_GREEN, "Improving": CLR_CYAN,
        "Weakening": CLR_YELLOW, "Lagging": CLR_RED,
    }.get(quadrant, CLR_FLAT)


def rrg_text_class(quadrant):
    return _HEX_TO_TXT[rrg_color(quadrant)]


def pcr_from_chain(chain):
    """Sum put vs call totalVolume from a Schwab /chains payload -> ratio.
    Returns None when no chain or zero call volume. Pure transform retained
    for display/test parity (chain fetching itself lives in the service)."""
    if not chain:
        return None
    pv = cv = 0
    for strikes in (chain.get("putExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    pv += v
    for strikes in (chain.get("callExpDateMap") or {}).values():
        for contracts in strikes.values():
            for c in contracts:
                v = c.get("totalVolume", 0) or 0
                if v > 0:
                    cv += v
    return round(pv / cv, 3) if cv > 0 else None


def _pct_change_n(closes, n):
    """%-change from n sessions ago to last close, or None."""
    if not closes or len(closes) < n + 1:
        return None
    prev = float(closes[-(n + 1)])
    last = float(closes[-1])
    if prev == 0:
        return None
    return (last - prev) / prev * 100.0


def week_month_from_closes(closes):
    """(day3_pct, week_pct, month_pct) from a daily-close list (n=3/5/21)."""
    return (_pct_change_n(closes, 3),
            _pct_change_n(closes, 5),
            _pct_change_n(closes, 21))


def sector_table_rows(sector_data, quotes, trends, pcr, quadrants):
    """Build display rows for the sectors, sorted by Day % desc (None last)."""
    rows = []
    for r in sector_data:
        if r.get("kind") != "sector":
            continue
        etf = r.get("etf")
        q = (quotes or {}).get(etf) or {}
        t = (trends or {}).get(etf) or {}
        rows.append({
            "sector": r.get("sector") or r.get("label"),
            "etf": etf,
            "desc": r.get("name") or "",
            "day": q.get("change_pct"),
            "week": t.get("week_pct"),
            "month": t.get("month_pct"),
            "pcr": (pcr or {}).get(etf),
            "rrg": (quadrants or {}).get(etf),
        })
    rows.sort(key=lambda r: (r["day"] is None, -(r["day"] or 0.0)))
    return rows


def sector_summary(sector_data, quotes, summary=None):
    """'{pct_up}% green | Cap-wtd {wpct} | Score {score}/10' (mirrors source).

    ``pct_up`` is computed here from quotes (pure). The cap-weighted pct and
    sector score come from the service-computed ``summary`` dict
    (``{"wpct", "score"}``); when absent (cold cache) they render as '—'/0.0."""
    pcts = []
    for r in sector_data:
        if r.get("kind") != "sector":
            continue
        q = (quotes or {}).get(r.get("etf")) or {}
        p = q.get("change_pct")
        if p is not None:
            pcts.append(p)
    if not pcts:
        return "No sector data returned"
    pct_up = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    summary = summary or {}
    wpct = summary.get("wpct")
    wpct_str = f"{wpct:+.2f}%" if wpct is not None else "—"
    score = _safe_float(summary.get("score"))
    return f"{pct_up:.0f}% green | Cap-wtd {wpct_str} | Score {score:.1f}/10"


def rotation_banner(rot):
    """(regime, color, detail) from a compute_rotation() dict (or None).
    Mirrors source _update_rotation_banner: day -> 3d -> week fallback."""
    if not rot:
        return "—", CLR_FLAT, "Refresh sector data to compute rotation"
    if rot.get("day_spread") is not None:
        tf, spread = "day", rot["day_spread"]
    elif rot.get("3d_spread") is not None:
        tf, spread = "3d", rot["3d_spread"]
    elif rot.get("week_spread") is not None:
        tf, spread = "week", rot["week_spread"]
    else:
        return "—", CLR_FLAT, "Refresh sector data to compute rotation"
    if spread >= 1.0:
        regime, color = "STRONG RISK-ON", CLR_GREEN
    elif spread >= 0.3:
        regime, color = "RISK-ON", CLR_GREEN
    elif spread <= -1.0:
        regime, color = "STRONG RISK-OFF", CLR_RED
    elif spread <= -0.3:
        regime, color = "RISK-OFF", CLR_RED
    else:
        regime, color = "MIXED", CLR_YELLOW
    cyc, dfn = rot.get(f"{tf}_cyc"), rot.get(f"{tf}_def")
    top = rot.get(f"{tf}_top3") or []
    bot = rot.get(f"{tf}_bot3") or []
    cyc_s = f"{cyc:+.2f}%" if cyc is not None else "—"
    def_s = f"{dfn:+.2f}%" if dfn is not None else "—"
    detail = (f"{tf.upper()}: Cyc {cyc_s} vs Def {def_s} (spread {spread:+.2f}%)"
              f"  ▲ {', '.join(top[:2]) or '—'}  ▼ {', '.join(bot[-2:]) or '—'}")
    return regime, color, detail


def industry_rows(sector_data, sector_name, ind_quotes, ind_trends, ind_pcr=None, ind_quadrants=None):
    """Indented rows for a sector's industries: day/week/month % + pcr/rrg."""
    rows = []
    for r in sector_data:
        if r.get("kind") != "industry" or r.get("sector") != sector_name:
            continue
        etf = r.get("etf")
        if not (etf and etf != "n/a" and len(str(etf)) <= 6):
            continue
        q = (ind_quotes or {}).get(etf) or {}
        t = (ind_trends or {}).get(etf) or {}
        rows.append({
            "label": r.get("label") or etf,
            "etf": etf,
            "desc": r.get("name") or "",
            "day": q.get("change_pct"),
            "week": t.get("week_pct"),
            "month": t.get("month_pct"),
            "pcr": (ind_pcr or {}).get(etf),
            "rrg": (ind_quadrants or {}).get(etf),
            "is_industry": True,
        })
    return rows


def is_rth(now):
    """True if `now` (a tz-aware America/Chicago datetime) is within regular
    trading hours (Mon–Fri 08:30–15:00 CT)."""
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return (8, 30) <= hm < (15, 0)


def component_table_rows(snapshot, weights=None, rotation_value=None, sector_value=None):
    """Rows for the in-composite components: name/value/score/weight/conf/contrib.
    Scores/confs come from the snapshot so Contrib reconciles to the composite.

    ``weights`` is the service-computed ``derived["weights"]`` dict (component
    key -> weight). A component absent from it (or weight 0/None) is treated as
    out-of-composite and skipped (e.g. credit_pulse). When ``weights`` is None
    (cold cache) no rows are produced."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
    weights = weights or {}
    # The Value column MUST read the SAME source as each component's score/conf
    # (the composite snapshot), so a displayed value can never contradict its
    # score. Two components had value/score-source mismatches (reported bug):
    #  - Put/Call read the legacy `pc_equity` ($CPCE) field, RETIRED in v4.3 and
    #    always blank — showing "—" beside a real score. Read the cap-weighted
    #    sector-P/C interp (falling back to the sector_pcr ratio) instead.
    #  - Rotation read the SEPARATE sectors-cache dual run (`rotation_value`),
    #    which can say "no sector returns available" while the composite's OWN
    #    dual run (which feeds the score) had data. Prefer the snapshot's interp.
    opts = snapshot.get("options") or {}
    pcr = _safe_float(snapshot.get("sector_pcr"))
    pc_val = opts.get("interpretation") or (f"P/C {pcr:.2f}" if pcr else "")
    rot_val = (snapshot.get("rotation") or {}).get("interpretation") or rotation_value
    value_src = {
        "vix_complex": (snapshot.get("volatility") or {}).get("interpretation"),
        "put_call": pc_val,
        "breadth": (snapshot.get("breadth") or {}).get("interpretation"),
        "rotation": rot_val or "—",
        "sector_perf": sector_value,
    }
    rows = []
    for key, name in COMPONENTS:
        w = weights.get(key)
        if not w:                      # skip out-of-composite (credit_pulse)
            continue
        s = _safe_float(scores.get(key))
        c = _safe_float(confs.get(key))
        rows.append({
            "key": key, "name": name,
            "value": value_src.get(key) or "—",
            "score": s,
            "weight": f"{int(w * 100)}%",
            "conf": f"{int(c * 100)}%",
            "contrib": w * s * c,
        })
    return rows


def tiles(latest, prev_total, band=None):
    """Signal tiles. ``band`` = service-computed ``(size, bias, signal)`` from
    ``derived`` (size_modifier/bias/signal); when absent, those three show '—'."""
    comp = latest.get("composite") or {}
    total = _safe_float(comp.get("total_score"))
    if band:
        size, bias, signal = band
    else:
        size, bias, signal = "—", "—", "—"
    if prev_total is None:
        yest, change = "—", "—"
    else:
        yest = f"{_safe_float(prev_total):.2f}"
        change = f"{total - _safe_float(prev_total):+.2f}"
    return {"modifier": size, "bias": bias, "signal": signal,
            "yesterday": yest, "change": change}


# ---------------------------------------------------------------------------
# Signals column — tile anatomy, tone palette, and the two recovered lines.
#
# LAYOUT DEVIATION from the supplied design reference (which shows a 2x2): the
# tiles are a 1x4 VERTICAL STACK. Two reasons, both measured:
#   * the column's problem is HEIGHT. Its neighbours (the Sentiment and Trend
#     Day/Week/Month rings) measure ~460px; a 2x2 of even generously-sized tiles
#     tops out near ~300px, leaving the same void the flat 2x2 left. Four
#     stacked ~95px tiles + the column header + the velocity/divergence block
#     lands near the rings' height, so the row stops reading as truncated.
#   * the column is ~300px wide. A 2x2 gives ~145px tiles — too narrow for the
#     reference's footer descriptors ("STRENGTH & MOMENTUM" would wrap badly),
#     while a full-width stacked tile fits the complete reference anatomy
#     (icon+label / big glowing value / hairline+dot / circled-icon+descriptor).
#
# The tiles are also INVERTED relative to the old flat ones: the old design put
# dark text on a light traffic-light fill, the reference puts a glowing coloured
# value on a near-black tile. TRAFFIC_BG_CLASSES is therefore NOT the reactive
# remove-set here — each element type gets its own remove-set below.
# ---------------------------------------------------------------------------

def _hex_rgb(value):
    h = str(value).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgba(value, alpha):
    """'#66bb6a' -> 'rgba(102,187,106,0.5)'. No spaces: a Tailwind arbitrary
    value cannot contain them (underscores are the escape, and rgba needs none)."""
    r, g, b = _hex_rgb(value)
    return f"rgba({r},{g},{b},{alpha})"


def _mix(value, other, t):
    """Blend two hexes; ``t`` = weight of ``other``. Used to derive each tile's
    top gradient stop as its own colour barely lifted off the near-black base."""
    a, b = _hex_rgb(value), _hex_rgb(other)
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# Near-black tile floor (the reference's bottom gradient stop).
_TILE_FLOOR = "#0a0f14"

# The four TONE keys are the finite set every data-driven tile colour maps into
# — nothing here is ever built from a runtime value. Each tone carries the FOUR
# class strings its tile needs (value text + glow, tile shell, hairline rule,
# end dot); the CLR_* hexes come from config/theme.toml, resolved once at import.
_TONE_HEX = {"pos": CLR_GREEN, "neg": CLR_RED, "warn": CLR_YELLOW,
             "flat": CLR_FLAT}


def _tone_classes(hexv):
    top = _mix(_TILE_FLOOR, hexv, 0.10)
    return {
        # The neon glow. `[text-shadow:...]` JIT-generates (verified live).
        "text": f"text-[{hexv}] [text-shadow:0_0_12px_{hexv}]",
        # Subtle vertical gradient + colour-tinted hairline border + soft outer
        # glow. box-shadow arbitraries need the rgba() form, not a hex.
        "tile": (f"bg-gradient-to-b from-[{top}] to-[{_TILE_FLOOR}] "
                 f"border border-[{hexv}]/40 "
                 f"shadow-[0_0_18px_-6px_{_rgba(hexv, 0.55)}]"),
        "rule": f"bg-[{hexv}]/30",
        "dot": f"bg-[{hexv}]",
    }


TONE_CLASSES = {k: _tone_classes(v) for k, v in _TONE_HEX.items()}
# Reactive remove-sets — one per element type. These MUST cover every class the
# element can apply or the classes stack across the page's version-poll repaint.
TONE_TEXT_CLASSES = " ".join(t["text"] for t in TONE_CLASSES.values())
TONE_TILE_CLASSES = " ".join(t["tile"] for t in TONE_CLASSES.values())
TONE_RULE_CLASSES = " ".join(t["rule"] for t in TONE_CLASSES.values())
TONE_DOT_CLASSES = " ".join(t["dot"] for t in TONE_CLASSES.values())

# Per-tile static chrome: header icon + label, footer circled icon + descriptor.
SIGNAL_TILE_DEFS = [
    {"key": "bias", "label": "BIAS", "icon": "explore",
     "foot_icon": "adjust", "descriptor": "MARKET DIRECTION"},
    {"key": "signal", "label": "SIGNAL", "icon": "bolt",
     "foot_icon": "radio_button_checked", "descriptor": "STRENGTH & MOMENTUM"},
    {"key": "yesterday", "label": "YESTERDAY", "icon": "history",
     "foot_icon": "schedule", "descriptor": "PREVIOUS CLOSE"},
    {"key": "change", "label": "CHANGE", "icon": "swap_vert",
     "foot_icon": "compare_arrows", "descriptor": "VS YESTERDAY"},
]


def _band_tone(total):
    """Composite traffic band -> tone key (same >=6.5 / <=4.5 cuts as traffic_color)."""
    return {CLR_GREEN: "pos", CLR_RED: "neg"}.get(traffic_color(total), "warn")


# The BIAS and SIGNAL tiles carry ``live_composite.signal_band``'s OWN two
# vocabularies — positioning (Long / Neutral / Cautious / Short) and strength
# (Strong Bull / Bullish / Neutral / Bearish / Strong Bear). Neither is the
# composite's ``bias`` field, so ``bias_color``'s bull/bear substring test does
# not cover them ("Long" would read amber forever). Colour each tile from its
# OWN word, so the colour can never contradict the text beside it.
_WORD_TONE = {
    "long": "pos", "bullish": "pos", "strong bull": "pos",
    "neutral": "warn", "cautious": "warn",
    "short": "neg", "bearish": "neg", "strong bear": "neg",
}


def _word_tone(word):
    """A BIAS/SIGNAL band word -> tone key. '—' (cold cache) -> flat."""
    w = str(word or "").strip().lower()
    if not w or w == "—":
        return "flat"
    if w in _WORD_TONE:
        return _WORD_TONE[w]
    # Unknown wording (the vocabulary is service-side and could grow): fall back
    # to the same substring read the rest of the page uses.
    if "bull" in w or "long" in w:
        return "pos"
    if "bear" in w or "short" in w:
        return "neg"
    return "warn"


def _change_tone(change):
    """Signed change string -> tone. '—' / unparseable / exactly flat -> 'flat'."""
    try:
        v = float(str(change).replace("+", ""))
    except (TypeError, ValueError):
        return "flat"
    if v > 0:
        return "pos"
    if v < 0:
        return "neg"
    return "flat"


def signal_tile_rows(t, prev_total):
    """Display rows for the Signals column: the static chrome from
    ``SIGNAL_TILE_DEFS`` joined to each tile's value and its TONE key (one of
    pos/neg/warn/flat — the finite set ``TONE_CLASSES`` is keyed by).

    ``t`` is a ``tiles()`` result. YESTERDAY reads flat when there is no prior
    session rather than inventing a band for a missing number."""
    tones = {
        "bias": _word_tone(t.get("bias")),
        "signal": _word_tone(t.get("signal")),
        "yesterday": "flat" if prev_total is None else _band_tone(prev_total),
        "change": _change_tone(t.get("change")),
    }
    return [dict(d, value=t.get(d["key"], "—"), tone=tones[d["key"]])
            for d in SIGNAL_TILE_DEFS]


def velocity_lines(derived):
    """``derived`` -> ``{"text", "flag", "divergence"}`` for the foot of the
    Signals column.

    These three come from ``compute.derive_composite_extras`` (``derived
    ["velocity"] = {"text", "flag"}`` and ``derived["divergence"]``, a string).
    They are published on EVERY refresh but lost their on-screen home when the
    intraday graphs replaced the old rolling/velocity/divergence text block —
    an accident of that layout change, not a decision. Defensive: a missing or
    wrongly-shaped payload yields empty strings, and the caller hides an empty
    line (a blank flag means "no regime break", not "unknown")."""
    d = derived or {}
    vel = d.get("velocity")
    if not isinstance(vel, dict):
        vel = {}
    div = d.get("divergence")
    return {"text": str(vel.get("text") or ""),
            "flag": str(vel.get("flag") or ""),
            "divergence": str(div or "")}


def _parse_iso(value):
    """Parse an ISO timestamp string -> datetime, or None. Tolerant of a
    trailing 'Z' and of already-datetime inputs (returns them unchanged)."""
    if value is None:
        return None
    from datetime import datetime
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _fmt_time(value):
    """ISO timestamp (or datetime) -> local 'HH:MM:SS', or '' on failure."""
    dt = _parse_iso(value)
    if dt is None:
        return ""
    try:
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt.strftime("%H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


def render():
    from nicegui import ui

    def _read_cache():
        """Pull the three sentiment cache views off the bus into ``state``.
        Graceful-empty: any missing view yields empty data (page renders a
        waiting placeholder rather than crashing)."""
        composite = bus_client.read("sentiment:composite") or {}
        history = bus_client.read("sentiment:history") or {}
        sectors = bus_client.read("sentiment:sectors") or {}
        state["live"] = composite.get("live")
        state["composite_at"] = composite.get("composite_at")
        state["proxy_up"] = composite.get("proxy_up")
        # Scoring-derived values (weights/size/bias/signal/velocity/divergence/
        # trend) computed in the service; the page only formats them.
        state["derived"] = composite.get("derived") or {}
        state["snaps"] = history.get("snaps") or []
        state["spy"] = history.get("spy") or []
        # rides the composite version bump — published in the same service refresh
        # cycle, so _maybe_repaint's comp_ver poll already triggers a repaint.
        intraday = bus_client.read("sentiment:intraday_history") or {}
        state["intraday"] = intraday.get("points") or []
        # Blended structural regime + today's recorded membership mix. Published
        # on its own 5-min cadence, so _maybe_repaint polls its version too.
        state["regime"] = bus_client.read("sentiment:regime") or {}
        state["regime_points"] = (
            (bus_client.read("sentiment:regime_history") or {}).get("points") or [])
        state["sector"] = sectors.get("sector")
        state["industries"] = sectors.get("industries") or {}
        state["sector_at"] = sectors.get("sector_at")
        # Cap-weighted sector pct/score, computed in the service.
        state["sector_summary"] = sectors.get("summary") or {}

    # Page-local UI state (per render closure; expanded set is page-local, not
    # module-global, per webgui conventions).
    state = {
        "snaps": [], "spy": [], "intraday": [], "sector": None, "live": None,
        "industries": {}, "expanded": set(), "derived": {}, "sector_summary": {},
        "composite_at": None, "sector_at": None, "proxy_up": None,
        "regime": {}, "regime_points": [],
        # last-seen bus cache versions for the fetch-free repaint timer
        "comp_ver": None, "sec_ver": None, "regime_ver": None,
    }
    _read_cache()
    state["comp_ver"] = bus_client.read_version("sentiment:composite")
    state["sec_ver"] = bus_client.read_version("sentiment:sectors")
    state["regime_ver"] = bus_client.read_version("sentiment:regime")

    # Top bar: "as of …" date + a small 3D refresh button, right-aligned. The
    # section titles now live per-column (all the same h6 size) below.
    with ui.row().classes("items-center w-full"):
        ui.space()
        ui.button("Refresh", icon="refresh", color=None,
                  on_click=lambda: _request_refresh()).props("no-caps").classes(BTN_3D)

    # Per-tile reactive element handles (value label, card shell, hairline rule,
    # end dot) — everything the tone recolor has to swap in place.
    tile_lbls, tile_cards, tile_rules, tile_dots = {}, {}, {}, {}
    # Three evenly-distributed, top-aligned columns with matching h6 headers.
    with ui.row().classes("w-full items-start justify-around gap-6 flex-wrap"):
        # ① Market Sentiment — Day/Week/Month ring + press-and-hold Components popup
        with ui.column().classes("items-center min-w-[300px]"):
            ui.label("Market Sentiment").classes("text-h6")
            # Built once with the empty-arc dial (all three tracks, em-dashes) and
            # updated in place via ``.content`` — never rebuilt. The captions live
            # inside the SVG, so there are no sibling caption labels any more.
            # Sanitizing (the ui.html default) is fine: ring_svg emits only
            # DOMPurify-allowlisted tags/attributes, which test_rings pins.
            sent_ring = ui.html(
                ring_svg(sentiment_arcs(None, []), uid="sent")
            ).classes("w-[280px] h-[280px]")
            bias_lbl = ui.label("").classes("text-h6")
            sub_lbl = ui.label("").classes("opacity-80 text-sm")
            with ui.button("Components", icon="table_view").props("flat dense") as comp_btn:
                with ui.menu().props("no-parent-event") as comp_menu:
                    comp_box = ui.column().classes("q-pa-md min-w-[520px]")
            # Press-and-hold: shown while the mouse button is down, closed on release.
            comp_btn.on("mousedown", lambda: comp_menu.open())
            comp_btn.on("mouseup", lambda: comp_menu.close())
            comp_btn.on("mouseleave", lambda: comp_menu.close())
        # ② Market Trend — Day/Week/Month ring + label/desc + detail popup
        with ui.column().classes("items-center min-w-[300px]"):
            ui.label("Market Trend").classes("text-h6")
            # ``uid`` MUST differ from the Sentiment ring's: both SVGs live on this
            # page and a shared id makes their DOM roots collide.
            trend_ring = ui.html(
                ring_svg(trend_arcs({}), uid="trend")
            ).classes("w-[280px] h-[280px]")
            regime_badge = ui.label("").classes("text-subtitle1 text-bold")
            regime_desc = ui.label("").classes("opacity-80 text-sm text-center")
            with ui.button("Trend Detail", icon="insights").props("flat dense") as trend_btn:
                with ui.menu().props("no-parent-event") as trend_menu:
                    trend_detail_box = ui.column().classes("q-pa-md text-sm min-w-[240px]")
            trend_btn.on("mousedown", lambda: trend_menu.open())
            trend_btn.on("mouseup", lambda: trend_menu.close())
            trend_btn.on("mouseleave", lambda: trend_menu.close())
        # ③ Signals — 1x4 vertical stack of glowing tiles (see the layout note
        # above SIGNAL_TILE_DEFS for why this deviates from the 2x2 reference),
        # with the recovered velocity / divergence lines at its foot.
        with ui.column().classes("items-center min-w-[300px] gap-2"):
            ui.label("Signals").classes("text-h6")
            with ui.column().classes("w-full gap-2 q-mt-sm"):
                for d in SIGNAL_TILE_DEFS:
                    tkey = d["key"]
                    # Built with the neutral tone; _apply swaps the tone classes
                    # in place (remove= the full finite set, so nothing stacks).
                    tone = TONE_CLASSES["flat"]
                    # p-2 (not q-pa-sm): nicegui-card's own p-4 wins over the
                    # Quasar padding class, which made each tile 111px and
                    # overshot the rings. bg-[#0a0f14] is the opaque floor under
                    # the gradient — q-card's base background is WHITE.
                    c = ui.card().classes(
                        "p-2 w-full min-h-[88px] justify-between gap-1 "
                        f"rounded-[12px] bg-[{_TILE_FLOOR}] {tone['tile']}")
                    with c:
                        with ui.row().classes("items-center gap-1 no-wrap"):
                            ui.icon(d["icon"], size="14px").classes("opacity-50")
                            ui.label(d["label"]).classes(
                                "text-[10px] tracking-[0.18em] opacity-60")
                        tile_lbls[tkey] = ui.label("—").classes(
                            f"text-2xl text-bold leading-none {tone['text']}")
                        with ui.row().classes("items-center gap-1 no-wrap w-full"):
                            tile_rules[tkey] = ui.element("div").classes(
                                f"flex-1 h-px {tone['rule']}")
                            tile_dots[tkey] = ui.element("div").classes(
                                f"w-[6px] h-[6px] rounded-full {tone['dot']}")
                        with ui.row().classes("items-center gap-1 no-wrap"):
                            ui.icon(d["foot_icon"], size="12px").classes("opacity-40")
                            ui.label(d["descriptor"]).classes(
                                "text-[9px] tracking-[0.12em] opacity-50")
                    tile_cards[tkey] = c
            # Velocity / regime-break flag / divergence. Published on every
            # refresh by the service but rendered nowhere until now; the flag and
            # the divergence note are hidden when empty (empty = "none", not
            # "unknown"), so a quiet tape shows the ROC line alone.
            vel_lbl = ui.label("").classes(
                "text-[11px] opacity-70 w-full text-center leading-snug")
            vel_flag_lbl = ui.label("").classes(
                f"text-[11px] text-bold w-full text-center leading-snug {TXT_Y}")
            div_lbl = ui.label("").classes(
                "text-[11px] opacity-70 w-full text-center leading-snug")
            for _el in (vel_flag_lbl, div_lbl):
                _el.set_visibility(False)

    ui.separator().classes("q-my-md")
    # Market Regime — the blended STRUCTURAL read (how the tape is moving),
    # complementary to the direction × aggression five-state above. Memberships
    # are continuous, so a handover shows as a gradual band shift, not a flip.
    with ui.expansion("Market Regime", icon="stacked_line_chart",
                      value=True).classes("w-full") as regime_exp:
        with ui.row().classes("items-baseline gap-3 w-full q-mt-sm"):
            regime_lbl = ui.label("").classes("text-subtitle1 text-bold")
            regime_conf_lbl = ui.label("").classes("opacity-60 text-sm")
            regime_trans_lbl = ui.label("").classes("opacity-80 text-sm")
        regime_why = ui.row().classes("items-center gap-2 flex-wrap")
        # Plain chart (NOT a stockChart) — see _intraday_figure for why.
        regime_plot = ui.highchart(build_regime_mix_figure([])).classes("w-full")

    @guard
    def _reflow_regime_chart():
        ui.run_javascript(f"getElement({regime_plot.id})?.chart?.reflow()")

    regime_exp.on_value_change(
        lambda e: ui.timer(0.05, _reflow_regime_chart, once=True) if e.value else None)

    ui.separator().classes("q-my-md")
    # Daily Sentiment & Trend — two value-colorized 2-min intraday series
    # (rolling last 5 trading days), expanded by default. Replaces the old
    # 30-Day History composite chart + rolling/velocity/divergence text.
    with ui.expansion("Daily Sentiment & Trend", icon="show_chart",
                      value=True).classes("w-full") as daily_exp:
        ui.label("Daily Market Sentiment").classes("text-subtitle2 q-mt-sm")
        # Plain chart (NOT a stockChart): a stockChart's chart.update() throws in the
        # stock module on every in-place update, freezing an open page on the data it
        # first rendered (the current day never appears) — see _intraday_figure.
        sent_intraday_plot = ui.highchart(
            build_sentiment_intraday_figure([])).classes("w-full")
        ui.label("Daily Market Trend").classes("text-subtitle2 q-mt-md")
        trend_intraday_plot = ui.highchart(
            build_trend_intraday_figure([])).classes("w-full")

    # Reflow both charts when the expander opens (a chart built inside a collapsed
    # expander measures 0x0 — same fix as the Simulator's hidden tab panels). The
    # worker is @guard-ed so the post-timer JS round-trip no-ops on a navigate-away/
    # disconnect race; the timer stays outside the guarded body.
    @guard
    def _reflow_daily_charts():
        for el in (sent_intraday_plot, trend_intraday_plot):
            ui.run_javascript(f"getElement({el.id})?.chart?.reflow()")

    daily_exp.on_value_change(
        lambda e: ui.timer(0.05, _reflow_daily_charts, once=True) if e.value else None)

    # NOTE: the Sector & Industry Performance table moved to its own tab
    # (``/sentiment/sectors``, ``pages.sentiment_sectors``). This page still reads
    # ``sentiment:sectors`` in ``_read_cache`` because the Components popup fills
    # its Rotation / Sector Value cells from that view (see ``_comp_context``).

    def _render_components(latest, rotation_value=None, sector_value=None):
        comp_box.clear()
        weights = (state.get("derived") or {}).get("weights")
        rows = component_table_rows(latest, weights, rotation_value, sector_value)
        with comp_box:
            with ui.row().classes("items-center w-full no-wrap gap-3 opacity-60 text-xs"):
                ui.label("Component").classes("w-[110px]")
                ui.label("Value").classes("w-[140px]")
                ui.label("Score").classes("w-[50px]")
                ui.label("Weight").classes("w-[60px]")
                ui.label("Conf").classes("w-[50px]")
            for r in rows:
                sc = r["score"]
                with ui.row().classes("items-center w-full no-wrap gap-3"):
                    ui.label(r["name"]).classes("text-sm w-[110px]")
                    ui.label(str(r["value"])).classes(
                        "text-sm w-[140px] overflow-hidden text-ellipsis whitespace-nowrap")
                    ui.label(f"{sc:.2f}").classes(
                        "text-sm text-bold w-[50px] " + sc_text_class(sc))
                    ui.label(r["weight"]).classes("text-sm w-[60px]")
                    ui.label(r["conf"]).classes("text-sm w-[50px]")

    def _comp_context():
        """(rotation_value, sector_value) from loaded sector data, or (None, None).
        ``sector_value`` (cap-weighted pct) comes from the service-computed
        sector summary; ``rotation_value`` is the dual-momentum interp string."""
        sec = state["sector"]
        if not sec:
            return None, None
        rotation_value = (sec.get("dual") or {}).get("interp")
        wpct = (state.get("sector_summary") or {}).get("wpct")
        sector_value = f"{wpct:+.2f}%" if wpct is not None else None
        return rotation_value, sector_value

    def _apply():
        live = state.get("live")
        snaps = state["snaps"]
        if not live and not snaps:
            bias_lbl.text = "Waiting for sentiment service…"
            return
        latest = live or snaps[-1]
        comp = latest.get("composite") or {}
        total = _safe_float(comp.get("total_score"))
        # Rings repaint by reassigning ``.content`` (a NiceGUI BindableProperty
        # whose on_change pushes the new innerHTML) — no element rebuild.
        sent_ring.content = ring_svg(sentiment_arcs(live, snaps), uid="sent")
        bias_lbl.text = f"{total:.2f} · {comp.get('bias', '')}"
        bias_lbl.classes(remove=SENT_TEXT_CLASSES, add=bias_text_class(comp.get('bias')))
        sub_lbl.text = f"Confidence {_safe_float(comp.get('aggregate_confidence')):.0%}"
        # Prior series: when showing live, today=live and the prior series is
        # the full backfill (all completed sessions); when showing backfill,
        # exclude the last (it's "today").
        prior_scores = (composite_series(snaps)[1] if live
                        else composite_series(snaps[:-1])[1])
        prev_total = prior_scores[-1] if prior_scores else None
        derived = state.get("derived") or {}
        band_labels = None
        if derived.get("size") is not None:
            band_labels = (derived.get("size", "—"), derived.get("bias", "—"),
                           derived.get("signal", "—"))
        t = tiles(latest, prev_total, band_labels)
        # Per-tile tone swap. Each element type carries its OWN remove-set (the
        # full finite tone vocabulary) so repeated repaints can never stack two
        # conflicting colors on one element.
        for row in signal_tile_rows(t, prev_total):
            tkey, tone = row["key"], TONE_CLASSES[row["tone"]]
            tile_lbls[tkey].text = row["value"]
            tile_lbls[tkey].classes(remove=TONE_TEXT_CLASSES, add=tone["text"])
            tile_cards[tkey].classes(remove=TONE_TILE_CLASSES, add=tone["tile"])
            tile_rules[tkey].classes(remove=TONE_RULE_CLASSES, add=tone["rule"])
            tile_dots[tkey].classes(remove=TONE_DOT_CLASSES, add=tone["dot"])
        vel = velocity_lines(derived)
        vel_lbl.text = vel["text"]
        vel_lbl.set_visibility(bool(vel["text"]))
        vel_flag_lbl.text = vel["flag"]
        vel_flag_lbl.set_visibility(bool(vel["flag"]))
        div_lbl.text = vel["divergence"]
        div_lbl.set_visibility(bool(vel["divergence"]))
        rotation_value, sector_value = _comp_context()
        _render_components(latest, rotation_value, sector_value)
        pts = state.get("intraday") or []
        sent_intraday_plot.options = build_sentiment_intraday_figure(pts)
        sent_intraday_plot.update()
        trend_intraday_plot.options = build_trend_intraday_figure(pts)
        trend_intraday_plot.update()
        # Market Regime — headline + transition + the stacked membership mix.
        # Reactive labels swap classes via remove/add so repeated repaints can't
        # stack conflicting text colors (the Tailwind-first house rule).
        reg = state.get("regime") or {}
        r_label, r_conf, r_cls = regime_headline_parts(reg)
        regime_lbl.text = r_label
        regime_lbl.classes(remove=REGIME_TEXT_CLASSES, add=r_cls)
        regime_conf_lbl.text = r_conf
        regime_trans_lbl.text = regime_transition_text(reg)
        regime_why.clear()
        with regime_why:
            for line in regime_evidence_rows(reg):
                ui.label(line).classes(
                    "text-xs opacity-70 rounded px-2 py-[2px] bg-[#1b2233]")
        regime_plot.options = build_regime_mix_figure(state.get("regime_points") or [])
        regime_plot.update()
        trend = derived.get("trend")
        trend_ring.content = ring_svg(trend_arcs(derived), uid="trend")
        if trend:
            committed = trend.get("state")
            regime_badge.text = trend.get("label", "")
            regime_badge.classes(remove=SENT_TEXT_CLASSES, add=trend_text_class(committed))
            regime_desc.text = trend.get("description", "")
            trend_detail_box.clear()
            with trend_detail_box:
                ui.label(
                    f"Trend score {trend_gauge_value(trend):.0f} · "
                    f"conf {_safe_float(trend.get('confidence')):.0%}"
                ).classes("text-bold")
                # Per-horizon state WORDS. The ring shows each horizon's number
                # but has no room for its label, and the regime badge below names
                # the Day horizon only — so this line is the only place the Week
                # and Month state words appear at all.
                horizons = [("Day", trend), ("Week", derived.get("trend_7d")),
                            ("Month", derived.get("trend_30d_ago"))]
                ui.label(" · ".join(
                    f"{name} {_TREND_SHORT.get((h or {}).get('state'), '—')}"
                    for name, h in horizons)).classes("text-sm opacity-80")
                for r in trend_subscore_rows(trend):
                    ui.label(f"{r['name']} ({r['weight']}): {r['score']}  "
                             f"conf {r['conf']}").classes("text-sm")
                evidence = market_state_evidence_rows(trend)
                if evidence:
                    ui.separator().classes("q-my-xs")
                    ui.label("Why").classes("text-bold text-sm")
                    for line in evidence:
                        ui.label(str(line)).classes("text-sm")
        else:
            # These two clears are REPAINT-only semantics: on a fresh page the
            # labels are already empty, so they matter solely when a repaint
            # follows a paint that HAD a trend. derived.trend comes from a
            # module-level holder in sentiment_svc and can go absent on a restart
            # or a defensive compute failure, which would otherwise strand a
            # stale trend label on screen indefinitely.
            regime_badge.text = ""
            regime_desc.text = ""
            trend_detail_box.clear()
            with trend_detail_box:
                ui.label("—").classes("text-sm")

    def _refill_component_context():
        """Refill the Components popup's Rotation / Sector-Value cells once the
        sector cache loads (it version-bumps independently of the composite). The
        sector TABLE itself now lives on the /sentiment/sectors tab."""
        if not (state["snaps"] or state.get("live")):
            return
        rotation_value, sector_value = _comp_context()
        latest = state.get("live") or state["snaps"][-1]
        _render_components(latest, rotation_value, sector_value)

    @guard
    def _request_refresh():
        bus_client.request("sentiment", {"type": "refresh"})
        ui.notify("Refresh requested")

    from datetime import timedelta
    @guard
    def _render_status():
        parts = []
        ca = _parse_iso(state.get("composite_at"))
        if ca:
            parts.append(f"Updated {_fmt_time(ca)}")
            parts.append(f"Next ~{_fmt_time(ca + timedelta(seconds=120))[:5]}")
        sa = state.get("sector_at")
        sa_str = _fmt_time(sa)
        if sa_str:
            parts.append(f"Sectors {sa_str}")
        up = state.get("proxy_up")
        parts.append(f"Proxy: {'connected' if up else ('—' if up is None else 'down')}")
        status_lbl.text = "   ·   ".join(parts) if parts else "Waiting for sentiment service…"

    @guard
    def _maybe_repaint():
        # Fetch-free: compare the bus cache versions to the last-painted ones and
        # only re-read + repaint on change. Mirrors the previous version-poll
        # pattern but tracks the Redis bus version instead of an in-process cache.
        comp_ver = bus_client.read_version("sentiment:composite")
        sec_ver = bus_client.read_version("sentiment:sectors")
        # The regime publishes on its OWN 5-min cadence (and can republish early
        # on a crisis attack), so it needs its own version probe.
        regime_ver = bus_client.read_version("sentiment:regime")
        if (comp_ver == state["comp_ver"] and sec_ver == state["sec_ver"]
                and regime_ver == state["regime_ver"]):
            return
        state["comp_ver"] = comp_ver
        state["sec_ver"] = sec_ver
        state["regime_ver"] = regime_ver
        _read_cache()
        _apply()
        _refill_component_context()
        _render_status()

    ui.separator().classes("q-my-sm")
    status_lbl = ui.label("Waiting for sentiment service…").classes("opacity-60 text-xs w-full")

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    _apply()
    _refill_component_context()
    _render_status()
    # Fetch-free version-poll repaint: tracks the service's cache writes without
    # any engine call. The page never fetches; the Refresh button enqueues a
    # command for the service to recompute.
    ui.timer(2.0, _maybe_repaint)
