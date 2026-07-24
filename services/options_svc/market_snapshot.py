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
    vals = [p.get(key) for p in (points or []) if isinstance(p.get(key), (int, float))]
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
    pts = [p for p in (points or []) if isinstance(p.get("memberships"), dict)]
    if not pts:
        return _empty_svg(w, h)
    n = len(pts)
    col_w = (w - 8) / n
    rects = []
    for i, p in enumerate(pts):
        m = p["memberships"]
        total = sum(max(0.0, float(m.get(k, 0.0))) for k in _REGIME_ORDER) or 1.0
        y = 4.0
        x = 4 + i * col_w
        for k in _REGIME_ORDER:
            frac = max(0.0, float(m.get(k, 0.0))) / total
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


def dashboard_grid_html(categories):
    cats = [c for c in (categories or []) if c.get("tiles")]
    if not cats:
        return '<div class="ms-empty">no data</div>'
    frames = []
    for c in cats:
        tiles = []
        for t in c["tiles"]:
            cs = t.get("color_state", "no_data")
            bg = _TILE_BG.get(cs, _TILE_BG["no_data"])
            fg = _TILE_FG.get(cs, _TILE_FG["no_data"])
            last = t.get("last")
            last_s = "" if last is None else (f"{last:g}" if isinstance(last, (int, float)) else str(last))
            tiles.append(
                f'<div class="ms-tile" style="background:{bg}">'
                f'<div class="ms-sym">{_html.escape(str(t.get("display", "")))}</div>'
                f'<div class="ms-last">{_html.escape(last_s)}</div>'
                f'<div class="ms-chg" style="color:{fg}">{_fmt_change(t)}</div></div>')
        frames.append(
            f'<div class="ms-frame"><div class="ms-frame-h">'
            f'{_html.escape(str(c.get("category", "")))}</div>'
            f'<div class="ms-tiles">{"".join(tiles)}</div></div>')
    return f'<div class="ms-grid">{"".join(frames)}</div>'


_TREND_BANDS = [(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")]
_SENT_BANDS = [(4.5, "#e05252"), (6.5, "#e0c452"), (10, "#3fb36b")]

_TREND_EXPLAIN = "Market Trend (0–100): direction × conviction of today's tape."
_SENT_EXPLAIN = "Market Sentiment (0–10): the app's cap-weighted composite read."
_REGIME_EXPLAIN = "Market Regime: the structural character of the tape (how it moves)."


def _panel(title, gauge, spark, explain, live):
    return (f'<div class="ms-panel"><div class="ms-panel-h">{_html.escape(title)}</div>'
            f'<div class="ms-panel-row">{gauge}{spark}</div>'
            f'<div class="ms-explain">{_html.escape(explain)}</div>'
            f'<div class="ms-live">{live}</div></div>')


def trend_panel_html(trend, intraday_points):
    t = trend or {}
    score = t.get("score")
    val = round(score) if isinstance(score, (int, float)) else "—"
    label = t.get("label") or "—"
    gauge = gauge_svg(score if isinstance(score, (int, float)) else 50,
                      vmin=0, vmax=100, bands=_TREND_BANDS, value_label=val, caption=label)
    spark = sparkline_svg(intraday_points, key="trend", vmin=0, vmax=100, bands=_TREND_BANDS)
    ev = t.get("evidence") or []
    live = _html.escape(t.get("description") or "")
    if ev:
        live += ' · <span class="ms-ev">' + _html.escape(" / ".join(map(str, ev[:3]))) + "</span>"
    return _panel("Daily Market Trend", gauge, spark, _TREND_EXPLAIN, live or "&mdash;")


def sentiment_panel_html(sentiment, intraday_points):
    s = sentiment or {}
    raw = s.get("total_score")
    val = f"{float(raw):.1f}" if isinstance(raw, (int, float)) else "—"
    bias = s.get("bias") or "—"
    gauge = gauge_svg(float(raw) if isinstance(raw, (int, float)) else 5.0,
                      vmin=0, vmax=10, bands=_SENT_BANDS, value_label=val, caption=bias)
    spark = sparkline_svg(intraday_points, key="sentiment", vmin=0, vmax=10, bands=_SENT_BANDS)
    live = f"Bias: {_html.escape(str(bias))}"
    return _panel("Daily Market Sentiment", gauge, spark, _SENT_EXPLAIN, live)


def regime_panel_html(regime, regime_points):
    r = regime or {}
    label = r.get("label") or "—"
    conf = r.get("confidence")
    conf_pct = f"{float(conf)*100:.0f}%" if isinstance(conf, (int, float)) else "—"
    unclear = bool(r.get("unclear"))
    cap = "Unclear" if unclear else label
    gauge = gauge_svg(float(conf)*100 if isinstance(conf, (int, float)) else 0,
                      vmin=0, vmax=100,
                      bands=[(40, "#8794b4"), (70, "#e0c452"), (100, "#3fb36b")],
                      value_label=conf_pct, caption=cap)
    spark = regime_mix_svg(regime_points)
    live = f"Committed: <b>{_html.escape(label)}</b> · confidence {conf_pct}"
    tr = r.get("transition")
    if isinstance(tr, dict) and tr.get("to"):
        prog = tr.get("progress")
        prog_s = f" · {float(prog)*100:.0f}%" if isinstance(prog, (int, float)) else ""
        live += (f'<br><span class="ms-ev">'
                 f'{_html.escape(str(tr.get("from", "")))} → '
                 f'{_html.escape(str(tr.get("to", "")))}{prog_s}</span>')
    return _panel("Daily Market Regime", gauge, spark, _REGIME_EXPLAIN, live)


_MS_CSS = """
.ms-grid{display:flex;flex-wrap:wrap;gap:10px}
.ms-frame{background:#0d1424;border:1px solid #1d2942;border-radius:8px;padding:8px;flex:1 1 260px}
.ms-frame-h{color:#8794b4;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.ms-tiles{display:flex;flex-wrap:wrap;gap:5px}
.ms-tile{min-width:74px;min-height:56px;border-radius:6px;padding:5px 7px}
.ms-sym{color:#eaf0fb;font-size:12px;font-weight:700}
.ms-last{color:#cdd8ee;font-size:12px}
.ms-chg{font-size:12px;font-weight:600}
.ms-read{display:flex;flex-wrap:wrap;gap:12px;margin-top:14px}
.ms-panel{background:#0d1424;border:1px solid #1d2942;border-radius:8px;padding:10px;flex:1 1 300px}
.ms-panel-h{color:#eaf0fb;font-size:13px;font-weight:700;margin-bottom:4px}
.ms-panel-row{display:flex;align-items:center;gap:8px}
.ms-explain{color:#8794b4;font-size:11px;margin-top:6px}
.ms-live{color:#cdd8ee;font-size:12px;margin-top:4px}
.ms-ev{color:#8794b4;font-size:11px}
.ms-empty{color:#7f8db0;font-size:12px}
"""


def market_snapshot_doc(dashboard, trend, sentiment, regime, intraday, regime_hist, *, subtitle=""):
    from services.options_svc import compute  # lazy: reuse the dark doc wrapper
    ipts = (intraday or {}).get("points") or []
    rpts = (regime_hist or {}).get("points") or []
    body = (
        f'<style>{_MS_CSS}</style>'
        f'<h1 style="color:#eaf0fb;font-size:18px;margin:0 0 10px">Market Snapshot</h1>'
        + dashboard_grid_html((dashboard or {}).get("categories") or [])
        + '<h2 style="color:#eaf0fb;font-size:15px;margin:16px 0 6px">Market Read</h2>'
        + '<div class="ms-read">'
        + trend_panel_html(trend, ipts)
        + sentiment_panel_html(sentiment, ipts)
        + regime_panel_html(regime, rpts)
        + '</div>'
    )
    return compute._analyze_doc(body, subtitle=subtitle)
