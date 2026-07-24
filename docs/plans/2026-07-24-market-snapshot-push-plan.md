# Market Snapshot push — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A scheduled Discord/Telegram push, fired every :00 and :30 during the
trading day, delivering one combined PNG — the Market Dashboard tile-grid plus three
gauge+sparkline panels (Daily Market Trend / Sentiment / Regime) with explanations.

**Architecture:** All new code lives in `options_svc`, which already owns the
HTML→PNG renderer (`briefing_image.render_html_png`), the push channels
(`push_notify`), and the scheduled-slot pattern (`scheduler.*_due` + `handlers.run_*`).
A new pure module `market_snapshot.py` builds a self-contained dark HTML doc from
six Redis caches; the handler reads those caches, renders, and pushes. No new deps,
no Claude cost.

**Tech Stack:** Python, headless Chrome (installed), Pillow (in venv), inline
SVG/HTML, pytest. Redis via `shared.bus`. Notify via `shared.notify.channels`.

---

## Reference: exact cache shapes (verified)

- `cache:market:dashboard` (read via `bus.cache_get`) →
  `{"categories": [{"category": str, "tiles": [tile,…]}, …], "proxy_up": bool}`
  where each tile = `{"display", "description", "last", "change", "change_pct",
  "value_only", "color_state", "polarity"}`. `color_state` ∈
  `{risk_on_strong, risk_on_mild, flat, risk_off_mild, risk_off_strong, no_data}`.
- `cache:sentiment:composite` → `payload["derived"]["trend"]` =
  `{"score" 0–100, "label", "description", "evidence": [str], "state", …}`; and
  `payload["live"]["composite"]` = `{"total_score" 0–10, "bias"}`.
- `cache:sentiment:regime` → RegimeState dict directly:
  `{"label", "committed_label", "confidence" 0–1, "memberships": {5 keys→float},
  "transition": {"from","to","progress"}|None, "unclear": bool, "evidence": [str]}`.
- `cache:sentiment:intraday_history` → `{"points": [{"ts", "trend" 0–100,
  "sentiment" 0–10}, …]}`.
- `cache:sentiment:regime_history` → `{"points": [{"ts", "memberships": {…},
  "confidence"}, …]}`.

## Reference: existing code to mirror / reuse

- `services/options_svc/briefing_image.py::render_html_png(html) -> bytes|None`
  (never raises; auto-crops).
- `services/options_svc/push_notify.py::send_gamma_briefing` (the exact gate +
  render + telegram-photo + discord-file + size-guard + text-fallback shape to
  copy) and its imports: `send_telegram`, `send_telegram_photo`, `send_discord`,
  `send_discord_file` from `shared.notify.channels`; `load_config()`.
- `services/options_svc/scheduler.py::action_alert_due` /
  `eod_summary_due` (the `(date, slot)`-in-`ran_slots` grace pattern), `_CT`,
  `_is_trading_day`, and the `loop()` latch-before-blocking-branch pattern
  (`_action_alert_branch`, ~lines 596–612).
- `services/options_svc/compute.py::_analyze_doc(body_html, subtitle) -> str`
  and `_ANALYZE_CSS` (the dark self-contained doc wrapper — reuse for consistent
  styling).
- Config already written: `shared/notifications.json` has a `market_snapshot`
  block `{enabled, start, end}` + `discord.market_snapshot_webhook_url`
  (gitignored; the real webhook). `shared/notifications.example.json` has the
  placeholder. **Do not re-add these.**

---

### Task 1: Pure gauge SVG builder

**Files:**
- Create: `services/options_svc/market_snapshot.py`
- Test: `services/options_svc/tests/test_market_snapshot.py`

**Step 1: Write the failing test**

```python
# services/options_svc/tests/test_market_snapshot.py
from services.options_svc import market_snapshot as ms

def test_gauge_svg_marker_position_scales_with_value():
    lo = ms.gauge_svg(0, vmin=0, vmax=100, bands=[(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")], value_label="0", caption="Bear")
    hi = ms.gauge_svg(100, vmin=0, vmax=100, bands=[(30, "#e05252"), (70, "#e0c452"), (100, "#3fb36b")], value_label="100", caption="Bull")
    assert lo.startswith("<svg") and hi.startswith("<svg")
    assert "0" in lo and "100" in hi and "Bull" in hi
    # needle angle differs across the range (value drives the transform)
    assert lo != hi

def test_gauge_svg_clamps_out_of_range():
    # value below vmin / above vmax must not crash or overshoot the arc
    ms.gauge_svg(-20, vmin=0, vmax=100, bands=[(100, "#3fb36b")], value_label="—", caption="x")
    ms.gauge_svg(999, vmin=0, vmax=100, bands=[(100, "#3fb36b")], value_label="—", caption="x")
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services/options_svc/tests/test_market_snapshot.py -v`
Expected: FAIL (module/function not defined).

**Step 3: Write minimal implementation**

```python
# services/options_svc/market_snapshot.py
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
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest services/options_svc/tests/test_market_snapshot.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/market_snapshot.py services/options_svc/tests/test_market_snapshot.py
git commit -m "feat(notify): gauge SVG for market-snapshot infographic"
```

---

### Task 2: Pure sparkline + regime-mix SVG builders

**Files:**
- Modify: `services/options_svc/market_snapshot.py`
- Test: `services/options_svc/tests/test_market_snapshot.py`

**Step 1: Write the failing test**

```python
def test_sparkline_svg_empty_points_is_placeholder():
    out = ms.sparkline_svg([], key="trend", vmin=0, vmax=100, bands=[(30,"#e05252"),(70,"#e0c452"),(100,"#3fb36b")])
    assert out.startswith("<svg") and "no data" in out.lower()

def test_sparkline_svg_draws_polyline_over_points():
    pts = [{"trend": 40}, {"trend": 55}, {"trend": 62}]
    out = ms.sparkline_svg(pts, key="trend", vmin=0, vmax=100, bands=[(30,"#e05252"),(70,"#e0c452"),(100,"#3fb36b")])
    assert out.count("<polyline") >= 1 or out.count("<path") >= 1

def test_regime_mix_svg_empty_is_placeholder():
    assert "no data" in ms.regime_mix_svg([]).lower()

def test_regime_mix_svg_stacks_membership_bands():
    pts = [{"memberships": {"mean_reversion":0.6,"trending":0.2,"breakout":0.1,"choppy":0.05,"crisis":0.05}}]
    out = ms.regime_mix_svg(pts)
    assert out.startswith("<svg") and "<rect" in out
```

**Step 2: Run** → FAIL (functions not defined).

**Step 3: Implement** (append to `market_snapshot.py`):

```python
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
```

**Step 4: Run** → PASS. **Step 5: Commit** `feat(notify): sparkline + regime-mix SVG`.

---

### Task 3: Dashboard tile-grid HTML

**Files:** Modify `market_snapshot.py`; test in the same test file.

**Step 1: Failing test**

```python
def test_dashboard_grid_html_frames_and_tiles():
    cats = [{"category": "Volatility", "tiles": [
        {"display": "VIX", "description": "CBOE VIX", "last": 14.2, "change_pct": 3.6,
         "color_state": "risk_off_strong", "value_only": False}]}]
    out = ms.dashboard_grid_html(cats)
    assert "Volatility" in out and "VIX" in out and "risk_off_strong" not in out  # class mapped, not raw
    assert "3.6" in out

def test_dashboard_grid_html_empty():
    assert "no data" in ms.dashboard_grid_html([]).lower()
```

**Step 2: Run** → FAIL.

**Step 3: Implement** (append). Map `color_state` → a fixed hex bg; render each
category as a framed block of tiles.

```python
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
                f'<div class="ms-sym">{_html.escape(str(t.get("display","")))}</div>'
                f'<div class="ms-last">{_html.escape(last_s)}</div>'
                f'<div class="ms-chg" style="color:{fg}">{_fmt_change(t)}</div></div>')
        frames.append(
            f'<div class="ms-frame"><div class="ms-frame-h">'
            f'{_html.escape(str(c.get("category","")))}</div>'
            f'<div class="ms-tiles">{"".join(tiles)}</div></div>')
    return f'<div class="ms-grid">{"".join(frames)}</div>'
```

**Step 4: Run** → PASS. **Step 5: Commit** `feat(notify): dashboard tile-grid HTML`.

---

### Task 4: The three panels + full doc

**Files:** Modify `market_snapshot.py`; test in the same file.

**Step 1: Failing test**

```python
def test_trend_panel_shows_label_explainer_and_live_read():
    out = ms.trend_panel_html({"score": 64, "label": "Bull", "description": "Trending up",
                               "evidence": ["ADX 64 rising"]}, [{"trend": 64}])
    assert "Bull" in out and "ADX 64 rising" in out and "direction" in out.lower()

def test_sentiment_panel_handles_missing():
    out = ms.sentiment_panel_html({}, [])
    assert out.startswith("<div") and "Sentiment" in out

def test_regime_panel_shows_transition_when_present():
    out = ms.regime_panel_html(
        {"label": "Trending", "committed_label": "trending", "confidence": 0.6,
         "memberships": {"mean_reversion":0.2,"trending":0.6,"breakout":0.1,"choppy":0.05,"crisis":0.05},
         "transition": {"from": "mean_reversion", "to": "trending", "progress": 0.6}}, [])
    assert "Trending" in out and ("→" in out or "-&gt;" in out or "to" in out.lower())

def test_market_snapshot_doc_is_self_contained():
    doc = ms.market_snapshot_doc({"categories": []}, {}, {}, {}, {"points": []}, {"points": []}, subtitle="09:00 CT")
    assert doc.lstrip().lower().startswith("<!doctype") or "<html" in doc.lower()
    assert "Market Read" in doc and "09:00 CT" in doc
```

**Step 2: Run** → FAIL.

**Step 3: Implement** (append). Panels compose Task 1–3 builders; the doc reuses
`compute._analyze_doc` for the dark wrapper.

```python
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
                 f'{_html.escape(str(tr.get("from","")))} &rarr; '
                 f'{_html.escape(str(tr.get("to","")))}{prog_s}</span>')
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
```

> **Executor note:** confirm `compute._analyze_doc`'s signature accepts
> `subtitle=` (it does — `_analyze_doc(body_html, subtitle=None)`). If its
> wrapper class conflicts with `.ms-*`, the `_MS_CSS` is namespaced so it won't.
> The doc must be **self-contained** (inline CSS/SVG only) — no external refs —
> so headless Chrome renders it offline.

**Step 4: Run** → PASS. **Step 5: Commit** `feat(notify): market-snapshot panels + doc`.

---

### Task 5: `push_notify.send_market_snapshot`

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py`

**Step 1: Failing test** (mirror the gamma-briefing gate tests; monkeypatch the
render + senders so no browser/network is touched):

```python
def test_send_market_snapshot_disabled_master(monkeypatch):
    calls = []
    monkeypatch.setattr(push_notify.briefing_image, "render_html_png", lambda h: b"PNG")
    monkeypatch.setattr(push_notify, "send_discord_file", lambda *a, **k: calls.append("d"))
    monkeypatch.setattr(push_notify, "send_telegram_photo", lambda *a, **k: calls.append("t"))
    assert push_notify.send_market_snapshot({}, {}, {}, {}, {}, {}, slot="09:00",
        config={"enabled": False, "market_snapshot": {"enabled": True}}) is False
    assert calls == []

def test_send_market_snapshot_block_disabled(monkeypatch):
    monkeypatch.setattr(push_notify.briefing_image, "render_html_png", lambda h: b"PNG")
    assert push_notify.send_market_snapshot({}, {}, {}, {}, {}, {}, slot="09:00",
        config={"enabled": True, "market_snapshot": {"enabled": False}}) is False

def test_send_market_snapshot_pushes_png(monkeypatch):
    got = {}
    monkeypatch.setattr(push_notify, "market_snapshot", __import__("services.options_svc.market_snapshot", fromlist=["x"]))
    monkeypatch.setattr(push_notify.market_snapshot, "market_snapshot_doc", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(push_notify.briefing_image, "render_html_png", lambda h: b"PNGDATA")
    monkeypatch.setattr(push_notify, "send_telegram_photo", lambda tok, cid, fn, png, cap: got.setdefault("t", (fn, png)))
    monkeypatch.setattr(push_notify, "send_discord_file", lambda wh, fn, png, cap, content_type=None: got.setdefault("d", (wh, fn)))
    cfg = {"enabled": True, "market_snapshot": {"enabled": True},
           "telegram": {"bot_token": "b", "chat_id": 1},
           "discord": {"webhook_url": "GEN", "market_snapshot_webhook_url": "MS"}}
    assert push_notify.send_market_snapshot({"categories": []}, {}, {}, {}, {}, {}, slot="09:00", config=cfg) is True
    assert got["t"][1] == b"PNGDATA"
    assert got["d"][0] == "MS"                       # per-channel webhook wins
    assert got["d"][1].startswith("market-snapshot-")
```

**Step 2: Run** → FAIL.

**Step 3: Implement.** Add near `send_gamma_briefing`. Import the new module at
top: `from services.options_svc import market_snapshot`.

```python
_MS_MAX_BYTES = _BRIEFING_MAX_BYTES   # reuse the same size ceiling


def _ms_webhook(dc: dict) -> str:
    dc = dc or {}
    return dc.get("market_snapshot_webhook_url") or dc.get("webhook_url", "")


def market_snapshot_caption(trend, sentiment, regime) -> str:
    t = (trend or {}).get("label") or "—"
    s = (sentiment or {}).get("total_score")
    s = f"{float(s):.1f}" if isinstance(s, (int, float)) else "—"
    r = (regime or {}).get("label") or "—"
    return f"📊 Market Snapshot — Trend: {t} · Sentiment: {s}/10 · Regime: {r}"


def send_market_snapshot(dashboard, trend, sentiment, regime, intraday, regime_hist,
                         *, slot: str, config: dict | None = None) -> bool:
    """Push the 30-min market snapshot PNG to Telegram + Discord. Never raises.

    Two gates: the master ``enabled`` and the ``market_snapshot.enabled`` block.
    On render failure, falls back to a TEXT caption (never goes silent). No SMS."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    block = cfg.get("market_snapshot") or {}
    if not block.get("enabled", True):
        return False
    caption = market_snapshot_caption(trend, sentiment, regime)
    tg = cfg.get("telegram", {})
    webhook = _ms_webhook(cfg.get("discord", {}))
    doc = market_snapshot.market_snapshot_doc(dashboard, trend, sentiment, regime,
                                              intraday, regime_hist, subtitle=f"{slot} CT")
    png = briefing_image.render_html_png(doc)
    if not png:
        log.warning("market snapshot %s: render failed — pushing text only", slot)
        send_telegram(tg.get("bot_token"), tg.get("chat_id"), _html.escape(caption))
        send_discord(webhook, {"description": caption})
        return True
    if len(png) > _MS_MAX_BYTES:
        log.warning("market snapshot %s too large (%d bytes)", slot, len(png))
        return False
    filename = f"market-snapshot-{slot.replace(':','')}.png"
    send_telegram_photo(tg.get("bot_token"), tg.get("chat_id"), filename, png, caption)
    send_discord_file(webhook, filename, png, caption, content_type="image/png")
    return True
```

> **Executor note:** `slot` here is `"HH:MM"` (from the scheduler). The filename
> strips the colon → `market-snapshot-0900.png`. Prepend the date in the handler
> if a per-day sortable name is wanted (optional).

**Step 4: Run** → PASS (`pytest services/options_svc/tests/test_push_notify.py -k market_snapshot -v`).
**Step 5: Commit** `feat(notify): send_market_snapshot push`.

---

### Task 6: `scheduler.market_snapshot_due`

**Files:**
- Modify: `services/options_svc/scheduler.py`
- Test: `services/options_svc/tests/test_scheduler.py`

**Step 1: Failing test**

```python
import datetime as dt
from services.options_svc import scheduler as sch
_CT = sch._CT

def _at(h, m):  # a trading day (Wed 2026-07-22)
    return dt.datetime(2026, 7, 22, h, m, tzinfo=_CT)

def test_market_snapshot_due_fires_on_the_half_hour():
    assert sch.market_snapshot_due(_at(9, 0), set()) == "09:00"
    assert sch.market_snapshot_due(_at(9, 30), set()) == "09:30"

def test_market_snapshot_due_once_per_slot():
    ran = set()
    slot = sch.market_snapshot_due(_at(9, 0), ran)
    ran.add(("2026-07-22", slot))
    assert sch.market_snapshot_due(_at(9, 2), ran) is None   # within grace, already ran

def test_market_snapshot_due_outside_window_none():
    assert sch.market_snapshot_due(_at(7, 0), set()) is None     # before start
    assert sch.market_snapshot_due(_at(15, 30), set()) is None   # after end
    assert sch.market_snapshot_due(_at(9, 15), set()) is None    # not a :00/:30 slot

def test_market_snapshot_due_skips_weekend():
    sat = dt.datetime(2026, 7, 25, 9, 0, tzinfo=_CT)
    assert sch.market_snapshot_due(sat, set()) is None
```

**Step 2: Run** → FAIL.

**Step 3: Implement** (add near `action_alert_due`):

```python
# ── 30-min Market Snapshot cadence (:00 & :30, 08:30–15:00 CT) ───────────────
_MKT_SNAP_START = (8, 30)
_MKT_SNAP_END = (15, 0)          # last slot fires at 15:00
_MKT_SNAP_GRACE_MIN = 10         # < 30 so a slot can't bleed into the next


def _market_snapshot_slots():
    """The (h, m) :00/:30 slot targets within [start, end], inclusive."""
    import datetime as _dt
    out = []
    cur = _dt.time(*_MKT_SNAP_START)
    end = _dt.time(*_MKT_SNAP_END)
    h, m = cur.hour, cur.minute
    while (h, m) <= (end.hour, end.minute):
        out.append((h, m))
        m += 30
        if m >= 60:
            m -= 60
            h += 1
    return out


def market_snapshot_due(now, ran_slots):
    """The "HH:MM" market-snapshot slot due now, or None. Once per slot per trading
    day within a 10-min grace (mirrors ``action_alert_due``). The caller records the
    returned ``(date, "HH:MM")`` in ``ran_slots`` so it won't refire."""
    if not _is_trading_day(now):
        return None
    import datetime as _dt
    day = now.date().isoformat()
    for h, m in _market_snapshot_slots():
        name = f"{h:02d}:{m:02d}"
        if (day, name) in ran_slots:
            continue
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now < target + _dt.timedelta(minutes=_MKT_SNAP_GRACE_MIN):
            return name
    return None
```

**Step 4: Run** → PASS. **Step 5: Commit** `feat(notify): market_snapshot_due scheduler gate`.

---

### Task 7: `handlers.run_market_snapshot`

**Files:**
- Modify: `services/options_svc/handlers.py`
- Test: `services/options_svc/tests/test_handlers.py`

**Step 1: Failing test** (fake bus; monkeypatch the push so no render/network):

```python
class _FakeBus:
    def __init__(self, data): self._d = data; self.sets = {}
    def cache_get(self, k): return self._d.get(k)
    def cache_set(self, k, v, **kw): self.sets[k] = v; return 1

def test_run_market_snapshot_reads_caches_and_pushes(monkeypatch):
    seen = {}
    monkeypatch.setattr(handlers.push_notify, "send_market_snapshot",
                        lambda *a, **k: seen.setdefault("args", (a, k)) or True)
    bus = _FakeBus({
        "cache:market:dashboard": {"categories": []},
        "cache:sentiment:composite": {"derived": {"trend": {"label": "Bull", "score": 64}},
                                      "live": {"composite": {"total_score": 7.1, "bias": "Bullish"}}},
        "cache:sentiment:regime": {"label": "Trending", "confidence": 0.6, "memberships": {}},
        "cache:sentiment:intraday_history": {"points": [{"trend": 64, "sentiment": 7.1}]},
        "cache:sentiment:regime_history": {"points": []},
    })
    handlers.run_market_snapshot(bus, "09:00")
    a, k = seen["args"]
    assert k["slot"] == "09:00"
    assert a[1]["label"] == "Bull"                 # trend passed
    assert a[2]["total_score"] == 7.1              # sentiment passed
    assert "cache:options:market_snapshot" in bus.sets

def test_run_market_snapshot_never_raises_on_bad_bus(monkeypatch):
    monkeypatch.setattr(handlers.push_notify, "send_market_snapshot", lambda *a, **k: True)
    class _Boom:
        def cache_get(self, k): raise RuntimeError("down")
        def cache_set(self, *a, **k): pass
    handlers.run_market_snapshot(_Boom(), "09:00")   # must not raise
```

**Step 2: Run** → FAIL.

**Step 3: Implement.** Add a cache constant + the handler. (Ensure `push_notify`
is imported in handlers — it already is for the signal/action pushes.)

```python
CACHE_MARKET_SNAPSHOT = "cache:options:market_snapshot"


def run_market_snapshot(bus, slot):
    """Build + push the 30-min Market Snapshot PNG (Telegram + Discord).

    Reads six caches (market dashboard + sentiment composite/regime + the two
    intraday histories), pushes via ``push_notify.send_market_snapshot``, and caches
    the inputs at ``cache:options:market_snapshot`` for inspection. Best-effort —
    ANY failure logs and returns, never raises into the scheduler."""
    try:
        dashboard = bus.cache_get("cache:market:dashboard") or {}
        comp = bus.cache_get("cache:sentiment:composite") or {}
        trend = (comp.get("derived") or {}).get("trend") or {}
        sentiment = (comp.get("live") or {}).get("composite") or {}
        regime = bus.cache_get("cache:sentiment:regime") or {}
        intraday = bus.cache_get("cache:sentiment:intraday_history") or {}
        regime_hist = bus.cache_get("cache:sentiment:regime_history") or {}
    except Exception:  # noqa: BLE001 — a down bus must not break the tick.
        log.exception("market snapshot %s: cache read failed", slot)
        return
    try:
        push_notify.send_market_snapshot(dashboard, trend, sentiment, regime,
                                         intraday, regime_hist, slot=slot)
    except Exception:  # noqa: BLE001 — the push primitives shouldn't raise, belt+braces.
        log.exception("market snapshot %s: push failed", slot)
    try:
        bus.cache_set(CACHE_MARKET_SNAPSHOT,
                      {"slot": slot, "trend": trend, "sentiment": sentiment,
                       "regime": regime})
    except Exception:  # noqa: BLE001
        log.exception("market snapshot %s: cache_set failed", slot)
```

**Step 4: Run** → PASS. **Step 5: Commit** `feat(notify): run_market_snapshot handler`.

---

### Task 8: Wire into `scheduler.loop`

**Files:**
- Modify: `services/options_svc/scheduler.py` (the `loop()` body — mirror the
  `action_alert` block ~lines 596–612)
- Test: `services/options_svc/tests/test_scheduler.py` (source-inspection, like the
  existing loop tests)

**Step 1: Failing test**

```python
import inspect
def test_loop_wires_market_snapshot():
    src = inspect.getsource(sch.loop)
    assert "market_snapshot_due" in src
    assert "run_market_snapshot" in src
    assert "market_snapshot_ran" in src
```

**Step 2: Run** → FAIL.

**Step 3: Implement.** In `loop()`: add `market_snapshot_ran = set()` beside the
other `*_ran` sets, and a gate+branch mirroring `_action_alert_branch` — latch the
slot in `market_snapshot_ran` BEFORE the blocking push so a slow render can't
double-fire:

```python
    market_snapshot_ran = set()  # (date, "HH:MM") of fired market-snapshot pushes
    ...
        try:
            ms_slot = market_snapshot_due(now, market_snapshot_ran)
            if ms_slot:
                market_snapshot_ran.add((now.date().isoformat(), ms_slot))
        except Exception:
            log.exception("market_snapshot_due gate degraded")
            ms_slot = None

        async def _market_snapshot_branch(slot_name):
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, handlers.run_market_snapshot, bus, slot_name)
            except Exception:
                log.exception("market snapshot branch failed")

        # launch alongside the other due branches (non-blocking, isolated)
        if ms_slot:
            launch(_market_snapshot_branch(ms_slot))   # match how sibling branches are launched
```

> **Executor note:** match the EXACT launch mechanism the sibling due-branches use
> in this `loop()` (whether they're `await`ed, gathered, or fire-and-forget via a
> helper). Read the surrounding ~40 lines first and copy that pattern precisely —
> do not invent a `launch()` if the file uses `asyncio.create_task` or a
> `launch_branches` helper.

**Step 4: Run** → PASS (`pytest services/options_svc/tests/test_scheduler.py -v`).
**Step 5: Commit** `feat(notify): wire market snapshot into the scheduler loop`.

---

### Task 9: Full suite + ruff

**Step 1:** `.venv\Scripts\python -m pytest services/options_svc -q`
Expected: green (prior baseline + the new tests). Note the documented ~2
pre-existing `test_expected_move` date-relative fails are NOT introduced by this work.

**Step 2:** `.venv\Scripts\ruff check services/options_svc/market_snapshot.py services/options_svc/push_notify.py services/options_svc/scheduler.py services/options_svc/handlers.py`
Expected: clean.

**Step 3: Commit** any lint fixups.

---

### Task 10: Live end-to-end verification (manual)

**Not a unit test — run against the live stack.**

1. Ensure Memurai + proxy + `market_svc` + `sentiment_svc` + `options_svc` are up
   (so all six caches are populated). During RTH is ideal; off-hours the caches
   still hold the last session (dashboard may be stale but present).
2. Temporarily set the config window / or drive the handler directly to avoid
   waiting for a real slot. From the repo root:
   ```
   .venv\Scripts\python -c "from shared.bus import Bus; from services.options_svc import handlers; handlers.run_market_snapshot(Bus(), '09:00')"
   ```
3. Confirm: (a) a PNG was pushed to the configured Discord channel
   (`market_snapshot_webhook_url`) + the Telegram chat; (b)
   `Bus().cache_get('cache:options:market_snapshot')` is populated; (c) the image
   shows the dashboard grid + three panels with gauges, sparklines, and the live
   read text; (d) no traceback in `services/options_svc/logs/options.log`.
4. If the render returns text-only, check `find_browser()` (Chrome/Edge path) —
   same dependency as the gamma briefing.
5. **Restart `options_svc`** so the scheduler picks up `market_snapshot_due`; watch
   the next :00/:30 slot fire on its own.

---

## Update CLAUDE.md

After Task 10 passes, add a "Last updated" entry summarizing the feature (host =
options_svc, six caches read, server-composed PNG, :00/:30 RTH cadence, config block
+ per-channel webhook, no Claude cost) and note **restart `options_svc`**. Follow the
existing changelog style at the top of `CLAUDE.md`.
