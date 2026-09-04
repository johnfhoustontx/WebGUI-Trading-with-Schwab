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
from pages.fmt import float_or  # the ONE copy (pages/fmt.py)
from pages.fmt import clamp as _clamp  # the ONE copy (pages/fmt.py)
from pages import busy as _busy
from pages import console_page
from pages.options import theme
from pages.options.theme import BTN_3D, THEME
from pages.ui_guard import guard
from pages import copy as _copy  # the ONE copy (pages/copy.py)

def _safe_float(v, default=0.0):
    return float_or(v, default)


_CT = ZoneInfo("America/Chicago")  # trading session clock for the intraday graphs

# The page's 5-color value palette now comes from config/theme.toml [charts]
# (edit + restart the webgui to restyle) — defaults preserve the historical look:
# green #66bb6a / red #ef5350 / yellow #ffd54f / flat #9e9e9e / cyan #3fb6c7.
_CH = THEME["charts"]
CLR_GREEN = _CH["green"]
CLR_RED = _CH["red"]
CLR_YELLOW = _CH["yellow"]

# LOCAL Tailwind class maps (Phase 5), generated from the same palette. These are
# the page's OWN 5-color vocabulary — the yellow/cyan have no theme TXT_* token and
# the flat differs from the theme neutral, so they are intentionally NOT the shared
# theme tokens. The `CLR_*` hex constants above feed the Highcharts figures +
# non-`.classes()` callers; the `*_class` helpers below return the Tailwind class
# string for `.classes()`.
TXT_G = f"text-[{CLR_GREEN}]"
TXT_R = f"text-[{CLR_RED}]"
TXT_Y = f"text-[{CLR_YELLOW}]"


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


def traffic_color(total):
    """Composite traffic-light band for tile backgrounds.
    >=6.5 green, <=4.5 red, else amber. Mirrors source _update_metric_card_colors."""
    v = _safe_float(total, 5.0)
    if v >= 6.5:
        return CLR_GREEN
    if v <= 4.5:
        return CLR_RED
    return CLR_YELLOW


def gauge_score(total):
    """0-10 composite -> 0-100 for the speedometer gauge."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


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


def market_state_evidence_rows(trend):
    """Evidence strings explaining WHY the new five-state trend was chosen
    (e.g. "direction 75/100", "aggression -0.37"). Defensive: [] when absent."""
    return (trend or {}).get("evidence") or []


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

    # Market Regime Console assets — scoped to THIS page, not the app shell.
    # ``add_head_html`` during a page build is client-scoped, so the condensed
    # display face is requested on /sentiment and nowhere else; every other page
    # keeps the two fonts the shell already loads. "" when [console].font_url is
    # blank, in which case the stack falls back to the app font.
    if theme.CONSOLE_FONT_HEAD_HTML:
        ui.add_head_html(theme.CONSOLE_FONT_HEAD_HTML)
    # The console's ONE escape-hatch rule: a keyframes animation cannot be a
    # utility class (same justification as the market ticker's marquee).
    ui.add_css(theme.CONSOLE_KEYFRAMES_CSS)

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

    # ── Market Regime Console ────────────────────────────────────────────────
    # The redesigned top of the page. Everything below it is unchanged and stays
    # — the intraday graphs, the Components popup, the status bar and Refresh.
    # One container, repainted by ``console_page.apply`` from ``_apply``.
    console_root = console_page.render()
    # Refresh refetches the composite (and, on the hour, ~24 sector calls).
    console_busy = _busy.build_busy(console_root, "Refreshing sentiment…")

    # The two press-and-hold popups the console's cards point at. Everything
    # ELSE that used to sit here — the two Day/Week/Month rings, the 1x4 Signals
    # stack, the velocity/divergence lines and the Market Regime expander — was
    # REMOVED when the console landed, because the console renders all of it
    # (meters for the rings, a 2x2 matrix for the tiles, signed meters for the
    # velocity numbers, and the whole regime block). Keeping both would show
    # every reading on this page twice.
    #
    # These two survive because the console has no room for them: the component
    # breakdown is a 520px table and the trend detail is a per-horizon
    # sub-score dump. They are what its "COMPONENTS →" and "TREND DETAIL →"
    # captions refer to.
    with ui.row().classes("w-full items-center gap-2 q-mt-sm"):
        with ui.button("Components", icon="table_view").props("flat dense") as comp_btn:
            with ui.menu().props("no-parent-event") as comp_menu:
                comp_box = ui.column().classes("q-pa-md min-w-[520px]")
        # Press-and-hold: shown while the mouse button is down, closed on release.
        comp_btn.on("mousedown", lambda: comp_menu.open())
        comp_btn.on("mouseup", lambda: comp_menu.close())
        comp_btn.on("mouseleave", lambda: comp_menu.close())
        with ui.button("Trend Detail", icon="insights").props("flat dense") as trend_btn:
            with ui.menu().props("no-parent-event") as trend_menu:
                trend_detail_box = ui.column().classes("q-pa-md text-sm min-w-[240px]")
        trend_btn.on("mousedown", lambda: trend_menu.open())
        trend_btn.on("mouseup", lambda: trend_menu.close())
        trend_btn.on("mouseleave", lambda: trend_menu.close())

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
        console_busy.hide()
        live = state.get("live")
        snaps = state["snaps"]
        if not live and not snaps:
            # Nothing published yet — the console renders its own waiting state
            # rather than an empty frame.
            console_page.apply(console_root, {})
            return
        latest = live or snaps[-1]
        comp = latest.get("composite") or {}
        total = _safe_float(comp.get("total_score"))
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
        # The console repaints from the SAME derived values the rest of the page
        # uses, so the two halves can never disagree about a number.
        _trend = derived.get("trend") or {}
        console_page.apply(console_root, {
            "sent_arcs": sentiment_arcs(live, snaps),
            "trend_arcs": trend_arcs(derived),
            "bias": comp.get("bias"),
            "total": f"{total:.2f}",
            "confidence": _safe_float(comp.get("aggregate_confidence"), None),
            "trend_short": _TREND_SHORT.get(_trend.get("state"), ""),
            "trend_verdict": _trend.get("label"),
            "trend_guidance": _trend.get("description"),
            "signal_rows": signal_tile_rows(t, prev_total),
            "velocity_values": (derived.get("velocity") or {}).get("values"),
            "divergence_detail": derived.get("divergence_detail"),
            "regime": state.get("regime") or {},
            "regime_points": state.get("regime_points") or [],
            "as_of": state.get("composite_at"),
        })
        rotation_value, sector_value = _comp_context()
        _render_components(latest, rotation_value, sector_value)
        pts = state.get("intraday") or []
        sent_intraday_plot.options = build_sentiment_intraday_figure(pts)
        sent_intraday_plot.update()
        trend_intraday_plot.options = build_trend_intraday_figure(pts)
        trend_intraday_plot.update()
        trend = derived.get("trend")
        if trend:
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
            # REPAINT-only semantics: on a fresh page the popup is already empty,
            # so this matters solely when a repaint follows a paint that HAD a
            # trend. derived.trend comes from a module-level holder in
            # sentiment_svc and can go absent on a restart or a defensive compute
            # failure, which would otherwise strand a stale detail dump on screen
            # indefinitely. (The badge/description labels this used to clear are
            # gone — the console's trend card carries the verdict now, and it is
            # rebuilt wholesale on every repaint, so it cannot go stale.)
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
        console_busy.show()
        ui.notify("Refreshing — the page updates when the new read lands.")

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
        status_lbl.text = ("   ·   ".join(parts) if parts
                           else _copy.WAITING_SENTIMENT)

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
    status_lbl = ui.label(_copy.WAITING_SENTIMENT).classes(
        "opacity-60 text-xs w-full")

    # Initial paint from the bus cache (graceful-empty if the service is cold).
    _apply()
    _refill_component_context()
    _render_status()
    # Fetch-free version-poll repaint: tracks the service's cache writes without
    # any engine call. The page never fetches; the Refresh button enqueues a
    # command for the service to recompute.
    ui.timer(2.0, _maybe_repaint)
