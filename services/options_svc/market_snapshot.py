"""Pure builders for the 30-min Market Snapshot infographic (options_svc).

Reads NOTHING — every function takes plain dicts/lists (the Redis payloads the
handler already fetched) and returns HTML/SVG strings. Deterministic + unit-tested.
The handler renders the doc to PNG via ``briefing_image.render_html_png`` and pushes
it via ``push_notify.send_market_snapshot``.
"""
import html as _html
import math

# semicircle gauge geometry
_R = 80
_CX, _CY = 100, 95
_A0, _A1 = 180.0, 0.0   # left→right sweep (degrees)


def _pt(cx, cy, r, deg):
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy - r * math.sin(rad)


def _arc(cx, cy, r, d0, d1):
    x0, y0 = _pt(cx, cy, r, d0)
    x1, y1 = _pt(cx, cy, r, d1)
    large = 1 if abs(d1 - d0) > 180 else 0
    sweep = 0 if d1 > d0 else 1
    return f"M {x0:.1f} {y0:.1f} A {r} {r} 0 {large} {sweep} {x1:.1f} {y1:.1f}"


def _num(x):
    """``float(x)`` for an int/float OR a numeric string, else None (never raises).

    ``bool`` is excluded — ``True``/``False`` are ints but never a real score.
    (sentiment_svc publishes ``total_score`` as a formatted string, e.g. "7.80".)"""
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def gauge_svg(value, *, vmin, vmax, bands, value_label, caption):
    """Semicircle gauge. ``bands`` = ascending [(upper_value, color), …] over
    [vmin,vmax]; the arc is drawn in colored segments and a needle points at
    ``value`` (clamped to range)."""
    span = (vmax - vmin) or 1.0
    frac = min(1.0, max(0.0, (float(value) - vmin) / span))
    segs = []
    prev = vmin
    for upper, color in bands:
        f0 = (prev - vmin) / span
        f1 = (min(upper, vmax) - vmin) / span
        d0 = _A0 + (_A1 - _A0) * f0
        d1 = _A0 + (_A1 - _A0) * f1
        segs.append(f'<path d="{_arc(_CX, _CY, _R, d0, d1)}" stroke="{color}" '
                    f'stroke-width="14" fill="none" stroke-linecap="butt"/>')
        prev = upper
    ndeg = _A0 + (_A1 - _A0) * frac
    nx, ny = _pt(_CX, _CY, _R - 6, ndeg)
    return (
        f'<svg viewBox="0 0 200 120" width="200" height="120" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(segs)
        + f'<line x1="{_CX}" y1="{_CY}" x2="{nx:.1f}" y2="{ny:.1f}" '
          f'stroke="#f5f5f5" stroke-width="3"/>'
          f'<circle cx="{_CX}" cy="{_CY}" r="4" fill="#f5f5f5"/>'
          f'<text x="{_CX}" y="80" text-anchor="middle" fill="#eaf0fb" '
          f'font-size="22" font-weight="700">{_html.escape(str(value_label))}</text>'
          f'<text x="{_CX}" y="113" text-anchor="middle" fill="#8794b4" '
          f'font-size="11">{_html.escape(str(caption))}</text>'
          f'</svg>'
    )


_REGIME_ORDER = ["mean_reversion", "trending", "breakout", "choppy", "crisis"]
_REGIME_COLORS = {"mean_reversion": "#3fb6c7", "trending": "#3fb36b",
                  "breakout": "#e0c452", "choppy": "#8794b4", "crisis": "#e05252"}
# Display words, mirroring sentiment-dashboard/scoring/market_regime (this
# service cannot import that package — cross-app ``scoring`` collision).
_REGIME_LABELS = {"mean_reversion": "Balanced", "trending": "Trending",
                  "breakout": "Breakout", "choppy": "Whipsaw", "crisis": "Stressed"}
_REGIME_DIRECTIONAL = {
    "trending": {(1, True): "Rallying", (1, False): "Firming",
                 (-1, True): "Retreating", (-1, False): "Softening"},
    "breakout": {(-1, True): "Breakdown", (-1, False): "Breakdown"},
}


def _regime_label(key, direction=0, strong=False):
    key = str(key)
    base = _REGIME_LABELS.get(key, key)
    words = _REGIME_DIRECTIONAL.get(key)
    if not words or direction not in (-1, 1):
        return base
    return words.get((direction, bool(strong)), base)


def _empty_svg(w, h, msg="no data"):
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg"><text x="{w//2}" y="{h//2}" '
            f'text-anchor="middle" fill="#7f8db0" font-size="11">{msg}</text></svg>')


def _band_color(v, bands):
    for upper, color in bands:
        if v <= upper:
            return color
    return bands[-1][1]


def sparkline_svg(points, *, key, vmin, vmax, bands, w=220, h=54):
    vals = [p.get(key) for p in (points or [])
            if isinstance(p, dict) and isinstance(p.get(key), (int, float))]
    if not vals:
        return _empty_svg(w, h)
    span = (vmax - vmin) or 1.0
    n = len(vals)

    def xy(i, v):
        x = 4 + (w - 8) * (i / max(1, n - 1))
        y = h - 4 - (h - 8) * min(1.0, max(0.0, (v - vmin) / span))
        return f"{x:.1f},{y:.1f}"
    pts_attr = " ".join(xy(i, v) for i, v in enumerate(vals))
    color = _band_color(vals[-1], bands)   # colorize by latest band
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg">'
            f'<polyline points="{pts_attr}" fill="none" stroke="{color}" '
            f'stroke-width="2"/></svg>')


def regime_mix_svg(points, w=220, h=54):
    pts = [p for p in (points or []) if isinstance(p, dict) and isinstance(p.get("memberships"), dict)]
    if not pts:
        return _empty_svg(w, h)
    n = len(pts)
    col_w = (w - 8) / n
    rects = []
    for i, p in enumerate(pts):
        m = p["memberships"]

        def _mv(key):
            v = m.get(key, 0.0)
            return max(0.0, float(v)) if isinstance(v, (int, float)) else 0.0
        total = sum(_mv(k) for k in _REGIME_ORDER) or 1.0
        y = 4.0
        x = 4 + i * col_w
        for k in _REGIME_ORDER:
            frac = _mv(k) / total
            bh = (h - 8) * frac
            rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{col_w:.1f}" '
                         f'height="{bh:.1f}" fill="{_REGIME_COLORS[k]}"/>')
            y += bh
    return (f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + "".join(rects) + "</svg>")


_TILE_BG = {"risk_on_strong": "#12331f", "risk_on_mild": "#16261c",
            "risk_off_strong": "#331213", "risk_off_mild": "#2a1618",
            "flat": "#141b2c", "no_data": "#10141f"}
_TILE_FG = {"risk_on_strong": "#4ad07f", "risk_on_mild": "#4ad07f",
            "risk_off_strong": "#f07171", "risk_off_mild": "#f07171",
            "flat": "#cdd8ee", "no_data": "#7f8db0"}


def _fmt_change(tile):
    if tile.get("value_only"):
        return ""
    pct = tile.get("change_pct")
    if pct is None:
        return ""
    try:
        return f"{float(pct):+.2f}%"
    except (TypeError, ValueError):
        return ""


# ── concentric Day/Week/Month rings ─────────────────────────────────────────
# MIRRORS webgui/pages/rings.py. It cannot IMPORT it: that module is Tier 1 and
# this is Tier 2, and market_snapshot is documented as reading nothing. Keep the
# two in step — the geometry constants below are the contract.
#
# Angles run clockwise from 12 o'clock. The scale starts at 225 deg (lower-left
# = 0) and sweeps 270 deg to 495 deg (lower-right = 100), leaving a 90 deg gap at
# the bottom for the Week/Month legend.
RING_START_DEG = 225.0
RING_SWEEP_DEG = 270.0
_RING_MIN_SWEEP = 0.5          # below this an arc is under ~1px — draw nothing
RING_VIEWBOX = 280
RING_RADII = (112.0, 90.0, 68.0)   # outer -> inner
RING_STROKE = 13.0
RING_CX = RING_CY = 140.0
RING_TRACK = "#1b2233"


def _ring_point(cx, cy, r, deg):
    rad = math.radians(deg - 90.0)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _ring_arc(cx, cy, r, d0, d1):
    sweep = d1 - d0
    if sweep < _RING_MIN_SWEEP:
        return ""
    x0, y0 = _ring_point(cx, cy, r, d0)
    x1, y1 = _ring_point(cx, cy, r, d1)
    large = 1 if sweep > 180.0 else 0
    return (f"M {x0:.2f} {y0:.2f} "
            f"A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}")


def _ring_value(v):
    """0-100 clamped, or None. None is the 'no usable reading' signal and must
    NOT collapse to 0 — a 0 paints as a genuine maximally-bearish value."""
    n = _num(v)
    if n is None:
        return None
    return max(0.0, min(100.0, n))


def _ring_fill(v, bands):
    return "#7f8db0" if v is None else _band_color(v, bands)


def ring_svg(arcs, bands, *, centre_suffix="", size=200):
    """Concentric Day/Week/Month dial, outermost-first.

    ``arcs`` is ``[{"value": 0-100 or None, "caption": str}, ...]``. A horizon
    with ``value=None`` draws its TRACK ONLY and reads an em-dash — the one thing
    a needle structurally cannot say, and the reason this replaced the old
    semicircular gauge.
    """
    arcs = [a if isinstance(a, dict) else {} for a in (arcs or [])][:len(RING_RADII)]
    vals = [_ring_value(a.get("value")) for a in arcs]
    caps = [_html.escape(str(a.get("caption") or "")) for a in arcs]

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" '
         f'viewBox="0 0 {RING_VIEWBOX} {RING_VIEWBOX}" width="{size}" height="{size}">']
    for r in RING_RADII[:len(vals)]:
        d = _ring_arc(RING_CX, RING_CY, r, RING_START_DEG, RING_START_DEG + RING_SWEEP_DEG)
        p.append(f'<path d="{d}" fill="none" stroke="{RING_TRACK}" '
                 f'stroke-width="{RING_STROKE}" stroke-linecap="round"/>')
    for i, v in enumerate(vals):
        if v is None:
            continue
        r = RING_RADII[i]
        end = RING_START_DEG + RING_SWEEP_DEG * (v / 100.0)
        d = _ring_arc(RING_CX, RING_CY, r, RING_START_DEG, end)
        if not d:
            continue
        col = _ring_fill(v, bands)
        # Halo = a wide translucent copy under a normal-width bright one (not an
        # SVG <filter>, to stay portable through the HTML->PNG renderer).
        p.append(f'<path d="{d}" fill="none" stroke="{col}" '
                 f'stroke-width="{RING_STROKE + 9:.0f}" stroke-linecap="round" opacity="0.22"/>')
        p.append(f'<path d="{d}" fill="none" stroke="{col}" '
                 f'stroke-width="{RING_STROKE}" stroke-linecap="round"/>')
    # centre = the OUTER horizon only (no room for three rows inside r=68)
    outer = vals[0] if vals else None
    head = "—" if outer is None else (centre_suffix or "").format(v=outer) or f"{outer:.0f}"
    p.append(f'<text x="{RING_CX}" y="{RING_CY - 4}" text-anchor="middle" '
             f'dy="0.35em" fill="#eaf0fb" font-size="40" font-weight="700">{_html.escape(head)}</text>')
    if caps:
        p.append(f'<text x="{RING_CX}" y="{RING_CY + 34}" text-anchor="middle" dy="0.35em" '
                 f'fill="#8794b4" font-size="13" letter-spacing="2">{caps[0]}</text>')
    # Week / Month sit in the 90 deg gap at the bottom.
    legend = []
    for i in (1, 2):
        if i < len(vals):
            txt = "—" if vals[i] is None else f"{vals[i]:.0f}"
            legend.append((caps[i], txt, _ring_fill(vals[i], bands)))
    for j, (cap, txt, col) in enumerate(legend):
        x = RING_CX + (-52 if j == 0 else 52)
        p.append(f'<text x="{x}" y="{RING_CY + 96}" text-anchor="middle" dy="0.35em" '
                 f'fill="#7f8db0" font-size="11" letter-spacing="1.5">{cap}</text>')
        p.append(f'<text x="{x}" y="{RING_CY + 114}" text-anchor="middle" dy="0.35em" '
                 f'fill="{col}" font-size="17" font-weight="700">{_html.escape(txt)}</text>')
    p.append("</svg>")
    return "".join(p)


def _trend_arc_value(t):
    """A trend horizon's 0-100 arc value, or None when it carries no reading.

    Mirrors ``webgui/pages/sentiment._trend_arc_value``: keys on CONFIDENCE, not
    on key presence, because the service publishes a zero-confidence neutral 50
    after a fetch failure and that must read as "no data", not as "neutral"."""
    if not isinstance(t, dict):
        return None
    conf = _num(t.get("confidence"))
    if conf is not None and conf <= 0:
        return None
    return _num(t.get("score"))


def trend_arcs(derived):
    d = derived if isinstance(derived, dict) else {}
    return [{"value": _trend_arc_value(d.get("trend")), "caption": "DAY"},
            {"value": _trend_arc_value(d.get("trend_7d")), "caption": "WEEK"},
            {"value": _trend_arc_value(d.get("trend_30d_ago")), "caption": "MONTH"}]


WEEK_SNAPS = 5


def _snap_composite(s):
    if not isinstance(s, dict):
        return None
    return _num((s.get("composite") or {}).get("total_score"))


def _avg_or_none(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def sentiment_arcs(sentiment, snaps):
    """Day/Week/Month arcs for sentiment (0-10 composite -> 0-100 arc).

    Week is the last 5 scored sessions, Month the full stored history — the same
    two horizons the /sentiment ring shows, and the reason the handler now also
    reads ``cache:sentiment:history``."""
    snaps = [s for s in (snaps or []) if isinstance(s, dict)]
    day = _num((sentiment or {}).get("total_score"))
    if day is None and snaps:
        day = _snap_composite(snaps[-1])
    week = _avg_or_none([_snap_composite(s) for s in snaps[-WEEK_SNAPS:]])
    month = _avg_or_none([_snap_composite(s) for s in snaps])
    to100 = lambda v: None if v is None else max(0.0, min(100.0, v * 10.0))
    return [{"value": to100(day), "caption": "DAY"},
            {"value": to100(week), "caption": "WEEK"},
            {"value": to100(month), "caption": "MONTH"}]


_TREND_BANDS = [(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")]
_SENT_BANDS = [(4.5, "#e05252"), (6.5, "#e0c452"), (10, "#3fb36b")]

_TREND_EXPLAIN = "Market Trend (0–100): direction × conviction of today's tape."
_SENT_EXPLAIN = "Market Sentiment (0–10): the app's cap-weighted composite read."
_REGIME_EXPLAIN = "Market Regime: the structural character of the tape (how it moves)."


def _panel(title, ring, spark, explain, live, *, state=""):
    chip = f'<span class="ms-hstate">{state}</span>' if state else ""
    return (f'<div class="ms-panel"><div class="ms-panel-h">{_html.escape(title)}'
            f'{chip}</div>'
            f'<div class="ms-panel-row">{ring}<div class="ms-spark">{spark}</div></div>'
            f'<div class="ms-explain">{_html.escape(explain)}</div>'
            f'<div class="ms-live">{live}</div></div>')


def trend_panel_html(trend, derived, intraday_points):
    """Market Trend as concentric Day/Week/Month rings (was one semicircular
    gauge showing Day only)."""
    t = trend or {}
    arcs = trend_arcs(derived)
    ring = ring_svg(arcs, _TREND_BANDS, centre_suffix="{v:.0f}")
    spark = sparkline_svg(intraday_points, key="trend", vmin=0, vmax=100, bands=_TREND_BANDS)
    ev = t.get("evidence") or []
    live = _html.escape(t.get("description") or "")
    if ev:
        live += ' · <span class="ms-ev">' + _html.escape(" / ".join(map(str, ev[:3]))) + "</span>"
    cap = _html.escape(str(t.get("label") or "—"))
    return _panel("Daily Market Trend", ring, spark, _TREND_EXPLAIN,
                  live or "&mdash;", state=cap)


def sentiment_panel_html(sentiment, snaps, intraday_points):
    """Market Sentiment as concentric Day/Week/Month rings. Week is the last 5
    scored sessions, Month the full stored history."""
    s = sentiment or {}
    arcs = sentiment_arcs(s, snaps)
    sval = _num(s.get("total_score"))
    centre = f"{sval:.1f}" if sval is not None else ""
    ring = ring_svg(arcs, [(45, "#e05252"), (65, "#e0c452"), (100, "#3fb36b")],
                    centre_suffix=centre or "{v:.0f}")
    spark = sparkline_svg(intraday_points, key="sentiment", vmin=0, vmax=10, bands=_SENT_BANDS)
    bias = s.get("bias") or "—"
    return _panel("Daily Market Sentiment", ring, spark, _SENT_EXPLAIN,
                  f"Bias: {_html.escape(str(bias))}", state=_html.escape(str(bias)))


# ── ranked regime share panel ───────────────────────────────────────────────
# MIRRORS webgui/pages/regime_mix.py. The percent-stacked band chart this
# replaces was the wrong encoding and the live numbers proved it: measured over
# one session the widest swing all day was 9pp, one regime sat at exactly 0.000
# while still holding a fifth of the legend, and percent-stacking then GUARANTEES
# the bands fill the height — so the day's two real events (the lead changing
# hands out of a 0.2pp gap, and a regime rising from zero) were both sub-pixel.
#
# Ranking gives up the old fixed reading position on purpose: with five rows a
# lead change is rare and is the most interesting thing that happens, so the
# ORDER is signal. Ties break on _REGIME_ORDER so identical data cannot jitter.
_REGIME_ORDER = ("mean_reversion", "trending", "breakout", "choppy", "crisis")
_REGIME_SUB = {
    "mean_reversion": "TWO-SIDED FLOW",
    "trending": "DIRECTIONAL PERSIST",
    "breakout": "RANGE EXPANSION",
    "choppy": "CHOP · NO EDGE",
    "crisis": "VOL EXPANSION",
}
_REGIME_COLOR = {
    "mean_reversion": "#4f9cf0", "trending": "#3fb36b", "breakout": "#35e0ff",
    "choppy": "#e0c452", "crisis": "#e05252",
}


def _memberships(point):
    m = (point or {}).get("memberships")
    return m if isinstance(m, dict) else {}


def regime_rows(points):
    """[(key, share_now, change_since_open)] sorted by share, leader first."""
    pts = [p for p in (points or []) if isinstance(p, dict)]
    if not pts:
        return []
    now, first = _memberships(pts[-1]), _memberships(pts[0])
    rows = []
    for k in _REGIME_ORDER:
        v = _num(now.get(k))
        if v is None:
            continue
        o = _num(first.get(k))
        rows.append((k, v, None if o is None else v - o))
    rows.sort(key=lambda r: (-r[1], _REGIME_ORDER.index(r[0])))
    return rows


def lead_margin(points):
    """(margin_now, margin_min) between the top two, as shares.

    ``margin_min`` is the tightest the top two got today — the number that says
    whether the headline was very nearly a coin toss. ``unclear`` measures
    evidence strength, not how close the top two are, so nothing else says this.
    """
    pts = [p for p in (points or []) if isinstance(p, dict)]
    if not pts:
        return (None, None)
    def _gap(p):
        vs = sorted((v for v in (_num(x) for x in _memberships(p).values())
                     if v is not None), reverse=True)
        return None if len(vs) < 2 else vs[0] - vs[1]
    now = _gap(pts[-1])
    gaps = [g for g in (_gap(p) for p in pts) if g is not None]
    return (now, min(gaps) if gaps else None)


def regime_panel_html(regime, regime_points):
    r = regime or {}
    label = r.get("label") or "—"
    conf = _num(r.get("confidence"))
    conf_pct = f"{conf * 100:.0f}%" if conf is not None else "—"
    head = "Unclear" if r.get("unclear") else label
    rows = regime_rows(regime_points)
    lead = rows[0][1] if rows else None

    body = []
    for k, share, chg in rows:
        pct = share * 100.0
        width = 0.0 if not lead else max(0.0, min(100.0, share / lead * 100.0))
        col = _REGIME_COLOR.get(k, "#8794b4")
        disp = _html.escape(_regime_label(k))
        if chg is None:
            ch_txt, ch_col = "—", "#7f8db0"
        else:
            ch_txt = f"{chg * 100:+.1f}pp"
            ch_col = "#3fb36b" if chg > 0 else ("#e05252" if chg < 0 else "#7f8db0")
        body.append(
            f'<div class="ms-rrow">'
            f'<div class="ms-rname">{disp}<span class="ms-rsub">'
            f'{_html.escape(_REGIME_SUB.get(k, ""))}</span></div>'
            f'<div class="ms-rbar"><i style="width:{width:.1f}%;background:{col}"></i></div>'
            f'<div class="ms-rpct">{pct:.1f}%</div>'
            f'<div class="ms-rchg" style="color:{ch_col}">{ch_txt}</div></div>')
    if not body:
        body.append('<div class="ms-empty">no regime history yet</div>')

    margin, margin_min = lead_margin(regime_points)
    foot = f"Committed: <b>{_html.escape(label)}</b> · confidence {conf_pct}"
    if margin is not None:
        foot += f' · leads by <b>{margin * 100:.1f} pp</b>'
        if margin_min is not None:
            foot += f' · tightest today {margin_min * 100:.1f} pp'
    # The transition must render DISPLAY labels, never the raw contract keys —
    # a "mean_reversion → trending" leaking to a phone is the bug the regime
    # rename exists to prevent, and it is what this used to say.
    tr = r.get("transition")
    if isinstance(tr, dict) and tr.get("to"):
        prog = _num(tr.get("progress"))
        prog_s = f" · {prog * 100:.0f}%" if prog is not None else ""
        d = r.get("direction")
        d = d if d in (-1, 0, 1) and not isinstance(d, bool) else 0
        strong = r.get("direction_strong") is True
        foot += (f'<br><span class="ms-ev">'
                 f'{_html.escape(_regime_label(tr.get("from", ""), d, strong))} → '
                 f'{_html.escape(_regime_label(tr.get("to", ""), d, strong))}{prog_s}</span>')
    return (f'<div class="ms-panel ms-wide"><div class="ms-panel-h">Market Regime '
            f'<span class="ms-hstate">{_html.escape(head)}</span></div>'
            f'<div class="ms-rows">{"".join(body)}</div>'
            f'<div class="ms-explain">{_REGIME_EXPLAIN}</div>'
            f'<div class="ms-live">{foot}</div></div>')


# ── Macro Board dashboard grid ──────────────────────────────────────────────
def _wash(tile):
    """Magnitude-scaled background wash, matching the board's colour language:
    intensity tracks |%change| while the HUE tracks the polarity-aware
    ``color_state`` — so VIX up is red even though the number rose."""
    cs = tile.get("color_state", "no_data")
    base = {"up": "63,179,107", "down": "224,82,82"}.get(cs)
    if not base:
        return "#111a2c"
    pct = _num(tile.get("change_pct")) or 0.0
    a = min(0.30, abs(pct) / 3.0 * 0.30 + 0.05)
    return f"rgba({base},{a:.3f})"


def _skew_word(pct):
    """Signed skew % -> 'Call 31%' / 'Put 22%' / 'Even' / '—'. Mirrors
    ``webgui/pages/market._skew_word``."""
    p = _num(pct)
    if p is None:
        return "—"
    if abs(p) < 1:
        return "Even"
    return f"{'Call' if p > 0 else 'Put'} {abs(p):.0f}%"


def _tile_lines(t):
    """(value, change, subline) for one tile — mirrors the board's ``tile_text``
    + ``descriptor_line`` so the pushed image says the same thing the screen
    does. The special tiles matter: Net Prem and BIG10 carry NO ``last`` at all,
    so a naive read renders them blank (which is what the old snapshot did)."""
    if t.get("net_prem"):
        pct = t.get("skew_pct")
        net = _num(t.get("net_m"))
        chg = "" if net is None else f"{'+' if net >= 0 else '-'}${abs(net) / 1000.0:.2f}B"
        return (_skew_word(pct), chg, "DOLLAR-WEIGHTED CALL/PUT")
    if t.get("basket"):
        avg = _num(t.get("avg_pct"))
        head = "—" if avg is None else f"{avg:+.2f}%"
        return (head, str(t.get("breadth_text") or ""), _skew_word(t.get("prem_skew_pct")))
    last = t.get("last")
    if last is None:
        return ("—", "", _descriptor(t))
    last_s = f"{last:g}" if isinstance(last, (int, float)) else str(last)
    if t.get("value_only"):
        return (last_s, "", _descriptor(t))
    return (last_s, _fmt_change(t), _descriptor(t))


_DESC_MAX = 26


def _descriptor(t):
    if "prem_skew_pct" in t:
        return _skew_word(t.get("prem_skew_pct"))
    d = (t.get("description") or "").strip().upper()
    return d if len(d) <= _DESC_MAX else d[:_DESC_MAX - 1] + "…"


def dashboard_grid_html(categories):
    cats = [c for c in (categories or []) if isinstance(c, dict) and c.get("tiles")]
    if not cats:
        return '<div class="ms-empty">no data</div>'
    frames = []
    for c in cats:
        name = str(c.get("category", ""))
        tiles = []
        for t in c["tiles"]:
            if not isinstance(t, dict):
                continue
            fg = _TILE_FG.get(t.get("color_state", "no_data"), _TILE_FG["no_data"])
            last_s, chg_s, sub = _tile_lines(t)
            tiles.append(
                f'<div class="ms-tile" style="background:{_wash(t)}">'
                f'<div class="ms-sym">{_html.escape(str(t.get("display", "")))}</div>'
                f'<div class="ms-last">{_html.escape(last_s)}</div>'
                f'<div class="ms-chg" style="color:{fg}">{_html.escape(chg_s)}</div>'
                f'<div class="ms-sub">{_html.escape(str(sub))}</div></div>')
        # Width the frame to a whole number of tiles (max 4 across) instead of
        # letting it stretch. A stretched frame left dead space to the right of
        # its tiles and forced one frame per row, which nearly doubled the
        # image height — a real cost on a phone push.
        cols = max(1, min(len(tiles), _MS_MAX_COLS))
        w = cols * _MS_TILE_W + (cols - 1) * _MS_TILE_GAP + _MS_FRAME_PAD
        frames.append(
            f'<div class="ms-frame" style="--acc:{_accent(name)};width:{w}px">'
            f'<div class="ms-frame-h">{_html.escape(name)}</div>'
            f'<div class="ms-tiles">{"".join(tiles)}</div></div>')
    return f'<div class="ms-grid">{"".join(frames)}</div>'


_MS_TILE_W = 152      # must match .ms-tile width in _MS_CSS
_MS_TILE_GAP = 7      # .ms-tiles gap
_MS_FRAME_PAD = 24    # .ms-frame left+right padding + borders
_MS_MAX_COLS = 4      # frames wrap internally past this, so several fit per row


_ACCENTS = ("#35e0ff", "#00e5a0", "#e0c452", "#b98cff", "#ff7ad9", "#4f9cf0")


def _accent(name):
    """Stable per-category accent — deterministic on the NAME, so a category
    keeps its colour between snapshots even if the board's order changes."""
    return _ACCENTS[sum(map(ord, str(name))) % len(_ACCENTS)]


# The doc reuses the gamma-briefing CSS, whose `.ga` is capped at max-width:860px
# — sized for that report's single column. The board needs room for two 4-tile
# frames side by side, so the snapshot widens its own wrapper. _MS_CSS is emitted
# in the BODY, after the head stylesheet, so it wins on source order.
DOC_WIDTH = 1450          # CSS px handed to briefing_image.render_html_png
_MS_CSS = """
.ga{max-width:1380px}
.ms-grid{display:flex;flex-wrap:wrap;gap:11px;align-items:flex-start}
.ms-frame{position:relative;flex:0 0 auto;background:#101a30;border:1px solid #213152;
  padding:10px 11px 11px;
  clip-path:polygon(14px 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%,0 14px)}
.ms-frame::before{content:"";position:absolute;left:0;top:0;width:2px;height:100%;
  background:linear-gradient(180deg,var(--acc,#35e0ff),transparent 78%);opacity:.9}
.ms-frame-h{color:#8794b4;font-size:9.5px;text-transform:uppercase;
  letter-spacing:.24em;margin:0 0 9px 9px}
.ms-tiles{display:flex;flex-wrap:wrap;gap:7px}
/* every tile identical, matching the board's fixed 152x94 */
.ms-tile{position:relative;overflow:hidden;width:152px;height:94px;flex:0 0 152px;
  background:#111a2c;border:1px solid #1d2942;padding:9px 11px 8px;
  clip-path:polygon(0 0,100% 0,100% calc(100% - 8px),calc(100% - 8px) 100%,0 100%)}
.ms-sym{color:#8794b4;font-size:11px;font-weight:700;letter-spacing:.13em}
.ms-last{color:#e7edf8;font-size:17px;font-weight:500;margin-top:3px}
.ms-chg{font-size:10.5px;margin-top:2px}
.ms-sub{color:#7f8db0;font-size:9px;letter-spacing:.05em;margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ms-read{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}
.ms-panel{background:#101a30;border:1px solid #213152;border-radius:8px;
  padding:12px;flex:1 1 340px}
.ms-panel.ms-wide{flex:1 1 100%}
.ms-panel-h{color:#eaf0fb;font-size:13px;font-weight:700;margin-bottom:6px}
.ms-hstate{color:#8794b4;font-size:10px;letter-spacing:.18em;margin-left:8px;
  text-transform:uppercase}
.ms-panel-row{display:flex;align-items:center;gap:10px}
.ms-spark{flex:1 1 auto}
.ms-explain{color:#8794b4;font-size:11px;margin-top:8px}
.ms-live{color:#cdd8ee;font-size:12px;margin-top:4px}
.ms-ev{color:#8794b4;font-size:11px}
.ms-empty{color:#7f8db0;font-size:12px}
/* ranked regime share rows */
.ms-rows{display:flex;flex-direction:column;gap:6px;margin-top:2px}
.ms-rrow{display:flex;align-items:center;gap:10px}
.ms-rname{width:190px;color:#e7edf8;font-size:12px;font-weight:700}
.ms-rsub{display:block;color:#7f8db0;font-size:8.5px;letter-spacing:.14em;
  font-weight:400;margin-top:1px}
.ms-rbar{flex:1 1 auto;height:9px;background:#1b2233;border-radius:2px;overflow:hidden}
.ms-rbar i{display:block;height:100%;border-radius:2px}
.ms-rpct{width:56px;text-align:right;color:#e7edf8;font-size:12px;
  font-variant-numeric:tabular-nums}
.ms-rchg{width:64px;text-align:right;font-size:11px;font-variant-numeric:tabular-nums}
"""


def market_snapshot_doc(dashboard, trend, sentiment, regime, intraday, regime_hist,
                        *, subtitle="", derived=None, snaps=None):
    # Reuse the gamma-briefing dark aesthetic (_ANALYZE_CSS + .ga* structure) but
    # wrap it locally so the doc is titled "Market Snapshot", NOT "Gamma Analysis"
    # (compute._analyze_doc hardcodes the gamma title). Zero blast radius on gamma.
    from services.options_svc import compute  # lazy: reuse the dark doc CSS
    ipts = (intraday or {}).get("points") or []
    rpts = (regime_hist or {}).get("points") or []
    body = (
        f'<style>{_MS_CSS}</style>'
        + dashboard_grid_html((dashboard or {}).get("categories") or [])
        + '<h2 style="color:#eaf0fb;font-size:15px;margin:16px 0 6px">Market Read</h2>'
        + '<div class="ms-read">'
        + trend_panel_html(trend, derived if derived is not None else {"trend": trend}, ipts)
        + sentiment_panel_html(sentiment, snaps, ipts)
        + regime_panel_html(regime, rpts)
        + '</div>'
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Market Snapshot</title>'
        f'<style>{compute._ANALYZE_CSS}</style></head><body><div class="ga">'
        '<div class="ga-title">Market Snapshot</div>'
        f'<div class="ga-sub">{_html.escape(subtitle)}</div>'
        f'<div class="ga-body">{body}</div></div></body></html>'
    )
