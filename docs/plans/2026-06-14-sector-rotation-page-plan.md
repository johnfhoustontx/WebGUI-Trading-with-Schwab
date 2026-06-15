# Sector Rotation sub-page — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Sector Rotation page at `/sentiment/rotation` as a sub-item under an expandable Sentiment nav group; reuse the copied `sector_rotation_assessment` engine; cache the assessment, manual refresh only; render headline + FROM/INTO + quadrant table + an RRG scatter.

**Architecture:** New `webgui/pages/sentiment_rotation.py` (pure builders + thin `render()` + module-level `_ROTATION_CACHE`) calling `sector_rotation_assessment.build_aligned_frame` + `build_assessment` off-thread. `main.py` gains a Sentiment expansion group + the new route.

**Tech Stack:** NiceGUI (`ui.plotly`, `ui.row`/`ui.column`, `nicegui.run.io_bound`), pytest. Reuses `sector_rotation_assessment` + `sectors_ref`.

**Design:** [`2026-06-14-sector-rotation-page-design.md`](2026-06-14-sector-rotation-page-design.md)

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (venv `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`).

**Engine facts (verified):** `rotation_tool.build_aligned_frame(symbols) -> (frame|None, missing)` (fetches via proxy itself); `rotation_tool.build_assessment(frame, date) -> dict|None`. Constants: `BENCHMARK="SPY"`, `SECTOR_ETFS` (etf→name, 11), `MIN_BARS=129`. Assessment dict: `date`, `headline{regime,text,spread,cyclical_mom_mean,defensive_mom_mean}`, `sectors=[{name,etf,rs_ratio,rs_momentum,quadrant,direction}]` (sorted by rs_momentum desc), `rotating_from/into=[{name,etf,quadrant}]`.

---

## Task 1: Pure transforms + tests

**Files:** Create `webgui/pages/sentiment_rotation.py` (transforms only this task); create `webgui/tests/test_sentiment_rotation.py`.

**Step 1 — failing tests** (`webgui/tests/test_sentiment_rotation.py`):
```python
"""Pure-transform tests for the Sector Rotation page."""
from pages import sentiment_rotation as R


def _assessment():
    return {
        "date": "2026-06-13",
        "headline": {"regime": "Risk-ON", "text": "Cyclicals leading",
                     "spread": 2.1, "cyclical_mom_mean": 101.2, "defensive_mom_mean": 99.1},
        "sectors": [
            {"name": "Technology", "etf": "XLK", "rs_ratio": 101.5, "rs_momentum": 102.0,
             "quadrant": "Leading", "direction": "INTO"},
            {"name": "Utilities", "etf": "XLU", "rs_ratio": 98.0, "rs_momentum": 97.0,
             "quadrant": "Lagging", "direction": "FROM"},
        ],
        "rotating_from": [{"name": "Utilities", "etf": "XLU", "quadrant": "Lagging"}],
        "rotating_into": [{"name": "Technology", "etf": "XLK", "quadrant": "Leading"}],
    }


def test_quadrant_color():
    assert R.quadrant_color("Leading") == R.CLR_GREEN
    assert R.quadrant_color("Improving") == R.CLR_CYAN
    assert R.quadrant_color("Weakening") == R.CLR_YELLOW
    assert R.quadrant_color("Lagging") == R.CLR_RED
    assert R.quadrant_color("???") == R.CLR_FLAT


def test_headline_parts():
    regime, color, text, detail = R.headline_parts(_assessment())
    assert regime == "Risk-ON" and color == R.CLR_GREEN
    assert "Cyclicals leading" in text
    assert "spread" in detail and "+2.1" in detail


def test_side_rows():
    weights = {"XLK": 32.5, "XLU": 2.1}
    rows, total = R.side_rows(_assessment(), "rotating_into", weights)
    assert rows[0]["name"] == "Technology" and rows[0]["quadrant"] == "Leading"
    assert rows[0]["weight"] == 32.5
    assert round(total, 1) == 32.5


def test_rotation_rows_sorted_and_colored():
    rows = R.rotation_rows(_assessment())
    assert [r["etf"] for r in rows] == ["XLK", "XLU"]      # rs_momentum desc
    assert rows[0]["color"] == R.CLR_GREEN                 # Leading
    assert rows[1]["color"] == R.CLR_RED                   # Lagging
    assert rows[0]["rs_ratio"] == 101.5


def test_rrg_scatter_figure_shape():
    fig = R.rrg_scatter_figure(_assessment())
    assert fig["data"][0]["type"] == "scatter"
    assert fig["data"][0]["mode"].startswith("markers")
    assert set(fig["data"][0]["x"]) == {101.5, 98.0}      # rs_ratio
    # crosshair reference lines at 100/100 present as shapes
    assert any(s.get("type") == "line" for s in fig["layout"].get("shapes", []))
```

**Step 2 — run, expect FAIL.**

**Step 3 — implement** `webgui/pages/sentiment_rotation.py` (transforms + constants; `render()`/`_compute` in Task 2):
```python
"""Sector Rotation page — RRG-vs-SPY assessment (under Sentiment).

Thin NiceGUI layer over the copied ``sector_rotation_assessment`` engine.
Pure builders here are unit-tested; ``render()`` (Task 2) wires widgets.
Data is fairly static: cached module-level, manual Refresh only.
"""
import sys

from repo_paths import SENTIMENT

if str(SENTIMENT) not in sys.path:
    sys.path.insert(0, str(SENTIMENT))

import sector_rotation_assessment as rotation_tool  # noqa: E402

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
CLR_CYAN = "#3fb6c7"
CLR_FLAT = "#9e9e9e"

_QUAD_COLOR = {"Leading": CLR_GREEN, "Improving": CLR_CYAN,
               "Weakening": CLR_YELLOW, "Lagging": CLR_RED}


def quadrant_color(q):
    return _QUAD_COLOR.get(q, CLR_FLAT)


def _regime_color(regime):
    return {"Risk-ON": CLR_GREEN, "Risk-OFF": CLR_RED}.get(regime, CLR_YELLOW)


def headline_parts(a):
    """(regime, color, text, detail) from an assessment dict."""
    h = a.get("headline") or {}
    regime = h.get("regime", "—")
    text = h.get("text", "")
    spread = h.get("spread")
    if spread is not None:
        detail = (f"cyclical RS-Mom {h.get('cyclical_mom_mean', 0):.2f} vs "
                  f"defensive {h.get('defensive_mom_mean', 0):.2f} "
                  f"(spread {spread:+.1f}; threshold ±{rotation_tool.RISK_THRESHOLD})")
    else:
        detail = ""
    return regime, _regime_color(regime), text, detail


def side_rows(a, side_key, weights):
    """([{name, etf, quadrant, weight}], total_weight) for rotating_from/into."""
    rows = []
    total = 0.0
    for s in a.get(side_key) or []:
        w = float((weights or {}).get(s.get("etf"), 0.0) or 0.0)
        total += w
        rows.append({"name": s.get("name"), "etf": s.get("etf"),
                     "quadrant": s.get("quadrant"), "weight": w})
    return rows, total


def rotation_rows(a):
    """Quadrant-map rows (already rs_momentum-desc from the engine), + color."""
    out = []
    for s in a.get("sectors") or []:
        out.append({**s, "color": quadrant_color(s.get("quadrant"))})
    return out


def rrg_scatter_figure(a):
    """Plotly RRG scatter: x=RS-Ratio, y=RS-Momentum, dot per sector, 100/100 lines."""
    secs = a.get("sectors") or []
    xs = [s.get("rs_ratio") for s in secs]
    ys = [s.get("rs_momentum") for s in secs]
    colors = [quadrant_color(s.get("quadrant")) for s in secs]
    labels = [s.get("etf") for s in secs]
    line = {"color": "rgba(255,255,255,0.25)", "width": 1}
    return {
        "data": [{
            "type": "scatter", "mode": "markers+text",
            "x": xs, "y": ys, "text": labels, "textposition": "top center",
            "marker": {"size": 12, "color": colors},
            "hovertext": [f"{s.get('name')} — {s.get('quadrant')}" for s in secs],
            "hoverinfo": "text",
        }],
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 8, "b": 36}, "height": 360,
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
            "xaxis": {"title": "RS-Ratio", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "yaxis": {"title": "RS-Momentum", "zeroline": False,
                      "gridcolor": "rgba(255,255,255,0.06)"},
            "shapes": [
                {"type": "line", "xref": "x", "yref": "paper", "x0": 100, "x1": 100,
                 "y0": 0, "y1": 1, "line": line},
                {"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1,
                 "y0": 100, "y1": 100, "line": line},
            ],
        },
    }
```

**Step 4 — run, expect PASS (5).** Full webgui suite stays green.

**Step 5 — commit:**
```bash
git add webgui/pages/sentiment_rotation.py webgui/tests/test_sentiment_rotation.py
git commit -m "feat(rotation): Sector Rotation pure builders (headline/side/rows/RRG scatter)"
```

---

## Task 2: `render()` + cache + off-thread compute

**Files:** Modify `webgui/pages/sentiment_rotation.py` (append `_ROTATION_CACHE`, `_compute`, `render`).

**Step 1 — append:**
```python
import datetime as _dt

# Static-ish data: cache the assessment; recompute only on manual Refresh.
_ROTATION_CACHE = {"assessment": None, "at": None}


def _compute():
    """Off-thread: fetch aligned frame via the engine + build the assessment.
    Returns (assessment|None, error_str|None)."""
    symbols = [rotation_tool.BENCHMARK] + list(rotation_tool.SECTOR_ETFS)
    frame, missing = rotation_tool.build_aligned_frame(symbols)
    if frame is None:
        return None, "No data from proxy (is schwab-proxy running?)"
    a = rotation_tool.build_assessment(frame, _dt.date.today().isoformat())
    if a is None or not a.get("sectors"):
        return None, (f"Insufficient daily history (need {rotation_tool.MIN_BARS} "
                      f"aligned bars).")
    return a, None


def _sector_weights():
    import sectors_ref
    return {r["etf"]: r.get("sp_weight", 0.0)
            for r in sectors_ref.load_sectors_data()
            if r.get("kind") == "sector" and r.get("etf")}


def render():
    import nicegui.run as ng_run
    from nicegui import ui

    weights = _sector_weights()

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Sector Rotation").classes("text-h6")
        ui.label("RRG vs SPY").classes("opacity-60 text-sm")
        as_of = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        spinner = ui.spinner(size="sm"); spinner.visible = False
        ui.button("Refresh", icon="refresh", on_click=lambda: load(force=True)).props("flat dense")

    headline_lbl = ui.label("").classes("text-subtitle1 text-bold")
    detail_lbl = ui.label("").classes("opacity-70 text-sm")
    msg_lbl = ui.label("").classes("text-warning text-sm")
    cols_box = ui.row().classes("w-full no-wrap gap-6 q-mt-sm")
    ui.label("Full Quadrant Map (sorted by RS-Momentum)").classes("text-subtitle2 q-mt-md")
    table_box = ui.column().classes("w-full q-gutter-none")
    ui.label("RRG").classes("text-subtitle2 q-mt-md")
    rrg_box = ui.column().classes("w-full")
    ui.label("Pairing is ordinal — strongest relative-selling vs strongest "
             "relative-buying pressure, not literal cash flow.").classes("opacity-50 text-xs q-mt-sm")

    QCOLS = [("name", "Sector", 150), ("etf", "ETF", 55), ("rs_ratio", "RS-Ratio", 90),
             ("rs_momentum", "RS-Mom", 90), ("quadrant", "Quadrant", 110),
             ("direction", "Dir", 60)]

    def _render(a):
        regime, color, text, detail = headline_parts(a)
        as_of.text = f"as of {a.get('date')}"
        headline_lbl.text = f"{regime} — {text}"
        headline_lbl.style(f"color:{color}")
        detail_lbl.text = detail
        msg_lbl.text = ""
        cols_box.clear()
        with cols_box:
            for side, title, tcolor in (("rotating_from", "ROTATING FROM", CLR_RED),
                                        ("rotating_into", "ROTATING INTO", CLR_GREEN)):
                rows, total = side_rows(a, side, weights)
                with ui.column().classes("items-start").style("flex:1"):
                    ui.label(f"{title}  ·  {total:.0f}% of S&P").style(f"color:{tcolor}").classes("text-bold text-sm")
                    for r in rows:
                        with ui.row().classes("items-center w-full no-wrap gap-2 text-sm"):
                            ui.label(f"{r['name']} ({r['quadrant']})").style("flex:1")
                            ui.label(f"{r['weight']:.1f}%" if r['weight'] else "").classes("opacity-70")
        table_box.clear()
        with table_box:
            with ui.row().classes("items-center w-full no-wrap gap-2 opacity-60 text-xs"):
                for _f, hdr, w in QCOLS:
                    ui.label(hdr).style(f"width:{w}px")
            for r in rotation_rows(a):
                with ui.row().classes("items-center w-full no-wrap gap-2 text-sm").style(f"color:{r['color']}"):
                    ui.label(str(r.get("name") or "")).style("width:150px")
                    ui.label(str(r.get("etf") or "")).style("width:55px")
                    ui.label(f"{r.get('rs_ratio'):.2f}").style("width:90px")
                    ui.label(f"{r.get('rs_momentum'):.2f}").style("width:90px")
                    ui.label(str(r.get("quadrant") or "")).style("width:110px")
                    ui.label(str(r.get("direction") or "")).style("width:60px")
        rrg_box.clear()
        with rrg_box:
            ui.plotly(rrg_scatter_figure(a)).classes("w-full")

    def _paint_cached():
        a = _ROTATION_CACHE["assessment"]
        if a:
            _render(a)
            return True
        return False

    async def load(force=False):
        if not force and _paint_cached():
            return
        spinner.visible = True
        try:
            a, err = await ng_run.io_bound(_compute)
            if a:
                _ROTATION_CACHE["assessment"] = a
                _ROTATION_CACHE["at"] = _dt.datetime.now()
                _render(a)
            else:
                msg_lbl.text = err or "No rotation data."
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Rotation load failed: {e}", type="negative")
        finally:
            spinner.visible = False

    # Paint cache instantly if present; otherwise compute once (no auto-refresh).
    if not _paint_cached():
        ui.timer(0.1, lambda: load(force=True), once=True)
```

**Step 2 — run** full webgui suite (transforms still green; render is import-only at test time). Import smoke `from pages import sentiment_rotation`.

**Step 3 — commit:**
```bash
git add webgui/pages/sentiment_rotation.py
git commit -m "feat(rotation): render() + module cache + off-thread compute (manual refresh)"
```

---

## Task 3: Nav group + route + test_shell

**Files:** Modify `webgui/main.py`, `webgui/tests/test_shell.py`.

**Step 1 — `main.py`:** Add the Sentiment group + route.
- Add constant:
```python
SENTIMENT_CHILDREN = [
    ("/sentiment", "Sentiment", "insights"),
    ("/sentiment/rotation", "Sector Rotation", "donut_large"),
]
```
- Remove the `("/sentiment", "Sentiment", "insights")` row from `FLAT_NAV` (leave Trade/Portfolio/Driver).
- In `_layout`, after the Options expansion, add a Sentiment expansion:
```python
        sentiment_active = active.startswith("/sentiment")
        with ui.expansion("Sentiment", icon="insights", value=sentiment_active).classes("w-full"):
            for path, label, icon in SENTIMENT_CHILDREN:
                _nav_link(path, label, icon, active)
```
(Place it before the `for … in FLAT_NAV` loop.)
- Add the route (near the `/sentiment` page def):
```python
@ui.page("/sentiment/rotation")
def sentiment_rotation_page() -> None:
    with _layout("/sentiment/rotation", "Sector Rotation"):
        from pages import sentiment_rotation
        sentiment_rotation.render()
```

**Step 2 — `test_shell.py`:** add `"/sentiment/rotation"` to the `expected` tuple.

**Step 3 — run** `..\.venv\Scripts\python -m pytest -q` (test_shell + all green) + import smoke `import main`.

**Step 4 — commit:**
```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(rotation): Sentiment nav group + /sentiment/rotation route"
```

---

## Task 4: Verify + docs

1. **Script-verify the engine** (temp `webgui/_chk_rot.py`, deleted after): insert SENTIMENT+repo root on path, `import sector_rotation_assessment as rt`, `frame,miss = rt.build_aligned_frame([rt.BENCHMARK]+list(rt.SECTOR_ETFS)); a = rt.build_assessment(frame, "2026-06-13")`; print `a["headline"]` + a couple `a["sectors"]` rows. Run with `$env:PYTHONIOENCODING="utf-8"` (allow ~120s; ~12 proxy calls). Confirm sane RRG (ratios/momenta near 100, quadrants assigned, regime set).
2. **Browser** (best-effort): restart preview if a slot is free; nav shows the Sentiment group with Sector Rotation; open `/sentiment/rotation`, confirm headline + FROM/INTO + quadrant table + RRG scatter render (or the friendly message off-hours/insufficient data). a11y snapshot fallback if the renderer is slow.
3. **Docs:** root `CLAUDE.md` — add `/sentiment/rotation` to the Routes table; note Sentiment is now an expandable nav group; mention the cached, manual-refresh Sector Rotation page reusing `sector_rotation_assessment`. Bump webgui test count.
4. **Commit** docs.

---

## Gotchas
- `build_aligned_frame` returns a TUPLE `(frame, missing)` — unpack it; `frame` is None when all fetches fail.
- The engine fetches via the proxy itself (`requests` to `PROXY_URL`) — no client to pass; just call it off-thread.
- `MIN_BARS=129` (~6 months+). Off-hours/weekends still have a year of daily history, so it should compute; a fresh-but-empty proxy → friendly message, not a crash.
- Cap weights: map by **ETF** (`sectors_ref` sector rows' `etf`→`sp_weight`), NOT by name — the engine's short names ("Technology") differ from `SP500_SECTOR_WEIGHTS` keys ("Information Technology").
- No `from scoring import` involved here; `sector_rotation_assessment` has no scoring-collision concern, but keep its import at module top.
- No auto-refresh/timers on this page (manual only) — except the one-shot initial compute when the cache is cold.
- Register `/sentiment/rotation` in `test_shell` or the shell test fails.
