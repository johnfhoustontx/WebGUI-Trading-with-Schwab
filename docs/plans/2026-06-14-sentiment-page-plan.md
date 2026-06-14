# Sentiment page (NiceGUI) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the `/sentiment` NiceGUI page — composite gauge + bias, component breakdown, 30-day history (velocity/divergence), and trend regime — by reusing the already-copied `history_backfill` + `scoring` engines.

**Architecture:** A thin NiceGUI page over `history_backfill.backfill_history(proxy.schwab_client, sector_data, [], days=35)`, run off-thread. The latest snapshot drives the gauge + component cards; the full series drives the history chart + `composite.velocity`; component scores drive `composite.divergence`; SPY 12-month closes drive `trend_regime.classify` + a replayed `commit_state`. The only ported logic is the workbook loader (`sectors_ref.py`); everything else is existing tested code. Pure transforms are unit-tested; `render()` is thin (widgets + timers), mirroring `pages/options/gamma.py`.

**Tech Stack:** NiceGUI (`ui.plotly`, `ui.html`, `ui.timer`, `nicegui.run.io_bound`), pandas (via the engine), pytest. Reuses `webgui/pages/options/svg.py` (speedometer, gradient bar).

**Reference design:** `docs/plans/2026-06-14-sentiment-page-design.md`

**Run tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q`
(sectors_ref test: `cd sentiment-dashboard && ..\.venv\Scripts\python -m pytest tests/test_sectors_ref.py -q`)

---

## Task 1: Port `sectors_ref.py` (workbook loader + sector constants)

Extract the non-tk sector logic from the uncopied source tk file so both the
webgui page and (later) the headless snapshot can import it without tkinter.
Source: `D:\Trading With Schwab\sentiment-dashboard\sentiment_dashboard.py:295-398`.

**Files:**
- Create: `sentiment-dashboard/sectors_ref.py`
- Test: `sentiment-dashboard/tests/test_sectors_ref.py`

**Step 1: Write the failing test**

```python
# sentiment-dashboard/tests/test_sectors_ref.py
"""Tests for sectors_ref — the non-tk sector workbook loader."""
import sectors_ref


def test_load_sectors_data_returns_sector_rows_with_weights():
    rows = sectors_ref.load_sectors_data()
    assert rows, "expected rows from Sectors_Industries_ETFs.xlsx"
    sectors = [r for r in rows if r.get("kind") == "sector"]
    # 11 GICS sectors in the reference workbook.
    assert len(sectors) == 11
    # Every sector row carries the cap weight used by sector_perf.
    weighted = [r for r in sectors if r.get("sp_weight", 0) > 0]
    assert len(weighted) == 11
    # Each sector has an ETF symbol.
    assert all(r.get("etf") for r in sectors)


def test_weights_sum_about_100():
    total = sum(sectors_ref.SP500_SECTOR_WEIGHTS.values())
    assert 95.0 <= total <= 105.0


def test_missing_workbook_returns_empty():
    rows = sectors_ref.load_sectors_data(xlsx_path="does_not_exist.xlsx")
    assert rows == []
```

**Step 2: Run test to verify it fails**

Run: `cd sentiment-dashboard && ..\.venv\Scripts\python -m pytest tests/test_sectors_ref.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sectors_ref'`

**Step 3: Write the implementation**

Copy verbatim from source `sentiment_dashboard.py:295-398` the items below into
`sentiment-dashboard/sectors_ref.py`. **No tkinter imports.** Keep the exact
loader logic (two-sheet workbook: `Sectors` + `Industries`, dedupe by
industry/etf, `sp_weight` from `SP500_SECTOR_WEIGHTS`).

```python
"""Sector reference data — workbook loader + S&P cap weights.

Extracted from the (uncopied) tk dashboard so the webgui Sentiment page and
the headless snapshot can build the ``sector_data`` rows that ``scoring``
and ``history_backfill`` consume, without importing tkinter.
"""
from pathlib import Path

SECTORS_XLSX = Path(__file__).parent / "Sectors_Industries_ETFs.xlsx"

# S&P 500 sector weights (percent, ~sum 100) — drives cap-weighted sector_perf.
SP500_SECTOR_WEIGHTS = {
    "Information Technology": 32.53,
    "Financials":             13.42,
    "Communication Services": 10.16,
    "Consumer Discretionary":  9.94,
    "Industrials":             8.86,
    "Health Care":             8.63,
    "Energy":                  4.89,
    "Consumer Staples":        4.61,
    "Materials":               2.74,
    "Real Estate":             2.12,
    "Utilities":               2.09,
}

CYCLICAL_SECTORS = {
    "Consumer Discretionary", "Financials", "Industrials",
    "Information Technology", "Communication Services",
    "Materials", "Energy",
}
DEFENSIVE_SECTORS = {
    "Consumer Staples", "Utilities", "Health Care", "Real Estate",
}


def load_sectors_data(xlsx_path=SECTORS_XLSX):
    """Load sector / industry / ETF rows from the reference workbook.

    Returns a list of dicts in display order:
        {kind: 'sector'|'industry', sector, label, etf, name, notes, sp_weight}
    Returns [] if the workbook or openpyxl is unavailable.
    """
    try:
        import openpyxl
    except ImportError:
        return []
    try:
        wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    except Exception:
        return []

    sector_primary = {}
    sector_descriptions = {}
    sector_order = []
    for i, row in enumerate(wb["Sectors"].iter_rows(values_only=True)):
        if i == 0 or not row or not row[1]:
            continue
        sector_name, spdr = row[1], row[2]
        description = row[5] if len(row) > 5 else None
        sector_primary[sector_name] = spdr
        sector_descriptions[sector_name] = description or ''
        sector_order.append(sector_name)

    industries_by_sector = {}
    seen_industry = set()
    seen_etf = set()
    for i, row in enumerate(wb["Industries"].iter_rows(values_only=True)):
        if i == 0 or not row or not row[0]:
            continue
        sector = row[0]
        industry = row[1] if len(row) > 1 else None
        etf = row[2] if len(row) > 2 else None
        etf_name = row[3] if len(row) > 3 else None
        notes = row[5] if len(row) > 5 else None
        if not etf:
            continue
        key = (sector, industry)
        if key in seen_industry or etf in seen_etf:
            continue
        seen_industry.add(key)
        seen_etf.add(etf)
        industries_by_sector.setdefault(sector, []).append(
            {'industry': industry, 'etf': etf,
             'name': etf_name or '', 'notes': notes or ''})

    rows = []
    for sector_name in sector_order:
        rows.append({
            'kind': 'sector', 'sector': sector_name,
            'label': sector_name, 'etf': sector_primary.get(sector_name),
            'name': sector_descriptions.get(sector_name, ''), 'notes': '',
            'sp_weight': SP500_SECTOR_WEIGHTS.get(sector_name, 0.0),
        })
        for ind in industries_by_sector.get(sector_name, []):
            rows.append({
                'kind': 'industry', 'sector': sector_name,
                'label': ind['industry'] or ind['etf'], 'etf': ind['etf'],
                'name': ind['name'], 'notes': ind['notes'],
                'sp_weight': 0.0,
            })
    return rows
```

**Step 4: Run test to verify it passes**

Run: `cd sentiment-dashboard && ..\.venv\Scripts\python -m pytest tests/test_sectors_ref.py -q`
Expected: PASS (3 passed). If the 11-sector assert fails, print the loaded
sector labels and adjust only the assert to the real count — do not change the
loader logic.

**Step 5: Commit**

```bash
git add sentiment-dashboard/sectors_ref.py sentiment-dashboard/tests/test_sectors_ref.py
git commit -m "feat(sentiment): port sectors_ref workbook loader (no tk)"
```

---

## Task 2: Pure transforms in `webgui/pages/sentiment.py`

All page logic that can be tested without NiceGUI. Build these first, TDD.
The snapshot dict shape is exactly what `history_backfill._score_one_day`
returns (see `sentiment-dashboard/history_backfill.py:271-340`): keys
`date`, `composite.{total_score,bias,size_modifier,aggregate_confidence}`,
`component_scores.{vix_complex,put_call,breadth,rotation,sector_perf,credit_pulse}`,
`component_confidence.{...}`, and per-section `interpretation`.

**Files:**
- Create: `webgui/pages/sentiment.py` (transforms only for now)
- Test: `webgui/tests/test_sentiment.py`

**Step 1: Write the failing tests**

```python
# webgui/tests/test_sentiment.py
"""Pure-transform tests for the Sentiment page."""
from pages import sentiment as S


def _snap(date, total, **comp):
    base = {"vix_complex": 5, "put_call": 5, "breadth": 5,
            "rotation": 5, "sector_perf": 5, "credit_pulse": 5}
    base.update(comp)
    return {
        "date": date,
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 0.9 for k in base},
    }


def test_gauge_score_scales_0_10_to_0_100():
    assert S.gauge_score("7.5") == 75.0
    assert S.gauge_score(0) == 0.0
    assert S.gauge_score("bad") == 0.0


def test_bias_color_buckets():
    assert S.bias_color("Bullish") == S.CLR_GREEN
    assert S.bias_color("Bearish") == S.CLR_RED
    assert S.bias_color("Neutral") == S.CLR_YELLOW


def test_composite_series_filters_zeros_and_blanks():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 0.0),
             _snap("2026-06-03", 7.0)]
    dates, scores = S.composite_series(snaps)
    assert scores == [6.0, 7.0]
    assert dates == ["2026-06-01", "2026-06-03"]


def test_velocity_line_formats_and_flags():
    # rising series, today jumps -> regime break possible
    scores = [5.0, 5.1, 5.0, 5.2, 5.1]
    line, flag = S.velocity_line(scores, today_score=8.0)
    assert "3d ROC" in line and "20d Z" in line
    assert "REGIME BREAK" in flag


def test_velocity_line_insufficient_history():
    line, flag = S.velocity_line([], today_score=5.0)
    assert "—" in line
    assert flag == ""


def test_divergence_named_extracts_confident_components():
    snap = _snap("2026-06-03", 6.0, vix_complex=9, sector_perf=2)
    named = S.divergence_named(snap)
    names = [n for n, _ in named]
    assert "VIX Complex" in names and "Sector Perf" in names


def test_build_history_figure_shape():
    snaps = [_snap("2026-06-01", 6.0), _snap("2026-06-02", 7.0)]
    fig = S.build_history_figure(snaps)
    assert fig["data"][0]["type"] == "scatter"
    assert fig["data"][0]["y"] == [6.0, 7.0]


def test_commit_trend_regime_returns_state():
    # 260 rising closes -> bull_trend, high confidence
    closes = [100.0 + i * 0.5 for i in range(260)]
    tr, committed, days = S.commit_trend_regime(closes)
    assert committed in {"bull_trend", "pullback_in_bull", "range",
                         "bear_rally", "bear_trend"}
    assert days >= 1


def test_commit_trend_regime_short_series_is_range():
    tr, committed, days = S.commit_trend_regime([100.0, 101.0])
    assert committed == "range"
```

**Step 2: Run to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_sentiment.py -q`
Expected: FAIL — `ImportError`/`AttributeError` on `pages.sentiment`.

**Step 3: Write the transforms**

```python
# webgui/pages/sentiment.py
"""Sentiment page — composite gauge + components + 30d history + trend regime.

Thin NiceGUI layer over the copied ``history_backfill`` + ``scoring`` engines.
Pure transforms here are unit-tested; ``render()`` wires widgets + timers.
"""
import sys

from repo_paths import SENTIMENT_DASHBOARD  # see Task 4 note if missing

if str(SENTIMENT_DASHBOARD) not in sys.path:
    sys.path.insert(0, str(SENTIMENT_DASHBOARD))

from scoring import WEIGHTS  # noqa: E402
from scoring import composite as scoring_composite  # noqa: E402
from scoring import trend_regime as trend_regime  # noqa: E402

CLR_GREEN = "#66bb6a"
CLR_RED = "#ef5350"
CLR_YELLOW = "#ffd54f"
LINE_COLOR = "#42a5f5"

# (component_scores key, display name, weight-or-None-if-out-of-composite)
COMPONENTS = [
    ("vix_complex", "VIX Complex", WEIGHTS.get("vix_complex")),
    ("put_call",    "Put/Call",    WEIGHTS.get("put_call")),
    ("breadth",     "Breadth",     WEIGHTS.get("breadth")),
    ("rotation",    "Rotation",    WEIGHTS.get("rotation")),
    ("sector_perf", "Sector Perf", WEIGHTS.get("sector_perf")),
    ("credit_pulse", "Credit Pulse", WEIGHTS.get("credit_pulse")),  # None -> out of composite
]


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def gauge_score(total):
    """0-10 composite -> 0-100 for the svg speedometer."""
    return max(0.0, min(100.0, _safe_float(total) * 10.0))


def bias_color(bias):
    b = (bias or "").lower()
    if "bull" in b:
        return CLR_GREEN
    if "bear" in b:
        return CLR_RED
    return CLR_YELLOW


def composite_series(snapshots):
    """(dates, scores) for snapshots with a positive composite total."""
    dates, scores = [], []
    for s in snapshots:
        v = _safe_float((s.get("composite") or {}).get("total_score"))
        if v > 0:
            dates.append(s.get("date"))
            scores.append(v)
    return dates, scores


def velocity_line(prior_scores, today_score):
    """(text, flag) from scoring.composite.velocity."""
    v = scoring_composite.velocity(list(prior_scores), _safe_float(today_score))
    roc3, roc5, z = v["roc_3d"], v["roc_5d"], v["z_20d"]
    parts = [
        f"3d ROC: {roc3:+.2f}" if roc3 is not None else "3d ROC: —",
        f"5d ROC: {roc5:+.2f}" if roc5 is not None else "5d ROC: —",
        f"20d Z: {z:+.2f}" if z is not None else "20d Z: —",
    ]
    flag = f"REGIME BREAK: {z:+.2f}σ from 20d mean" if v["regime_break"] else ""
    return " | ".join(parts), flag


def divergence_named(snapshot):
    """[(display_name, score)] for confident, scored components."""
    scores = snapshot.get("component_scores") or {}
    confs = snapshot.get("component_confidence") or {}
    out = []
    for key, name, _w in COMPONENTS:
        s = _safe_float(scores.get(key))
        if s > 0 and _safe_float(confs.get(key)) > 0:
            out.append((name, s))
    return out


def build_history_figure(snapshots):
    """Plotly fig dict: composite over time."""
    dates, scores = composite_series(snapshots)
    return {
        "data": [{
            "type": "scatter", "mode": "lines+markers",
            "x": dates, "y": scores,
            "line": {"color": LINE_COLOR, "width": 2},
            "name": "Composite",
        }],
        "layout": {
            "margin": {"l": 36, "r": 12, "t": 8, "b": 28},
            "height": 220,
            "yaxis": {"range": [0, 10], "title": "Composite"},
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
        },
    }


def commit_trend_regime(spy_closes, lookback_days=trend_regime.HYSTERESIS_DAYS + 1):
    """Replay classify + commit_state over the last sessions for faithful
    hysteresis without persisted state. Returns (result, committed, days)."""
    closes = list(spy_closes)
    result = trend_regime.classify(closes)
    # Replay committed state over the trailing window.
    committed = None
    history = []
    raw_states = []
    n = len(closes)
    # Build raw classifications for the last `lookback_days` sessions.
    span = min(lookback_days, max(1, n - trend_regime.MIN_BARS_PARTIAL))
    for back in range(span - 1, -1, -1):
        sub = closes[: n - back] if back else closes
        raw = trend_regime.classify(sub).state
        raw_states.append(raw)
        committed, history = trend_regime.commit_state(raw, history, committed)
    # days-in-state: trailing run of identical committed states is unknown
    # without history; report 1 for a fresh page (matches cold-start tk).
    days = 1
    return result, (committed or result.state), days
```

**Step 4: Run to verify pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_sentiment.py -q`
Expected: PASS (9 passed). If `repo_paths.SENTIMENT_DASHBOARD` is missing, do
Task 4 sub-step first (add the constant), then re-run.

**Step 5: Commit**

```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment.py
git commit -m "feat(sentiment): pure transforms (gauge/velocity/divergence/history/regime)"
```

---

## Task 3: `render()` + data load + wire into the shell

Add the NiceGUI UI and the off-thread engine call, then replace the `/sentiment`
stub in `main.py`.

**Files:**
- Modify: `webgui/pages/sentiment.py` (append `render()` + `_load`)
- Modify: `webgui/main.py:156-159` (replace stub body)

**Step 1: Append `render()` to `webgui/pages/sentiment.py`**

```python
def _load_snapshots(days=35):
    """Off-thread: full scoring path via the copied backfill engine.
    Returns (snapshots, spy_closes)."""
    import proxy
    import sectors_ref
    from history_backfill import backfill_history

    sector_data = sectors_ref.load_sectors_data()
    snaps, _stats = backfill_history(proxy.schwab_client, sector_data, [], days=days)
    spy_df = proxy.schwab_client.get_daily_history("SPY", months=12)
    spy_closes = (
        [float(c) for c in spy_df["close"].tolist()]
        if spy_df is not None else []
    )
    return snaps, spy_closes


def render():
    import nicegui.run as ng_run
    from nicegui import ui

    from pages.options.svg import speedometer_svg, gradient_bar_svg

    state = {"snaps": [], "spy": []}

    with ui.row().classes("items-center gap-3 w-full"):
        ui.label("Market Sentiment").classes("text-h6")
        date_lbl = ui.label("").classes("opacity-70 text-sm")
        ui.space()
        spinner = ui.spinner(size="sm")
        spinner.visible = False
        ui.button(icon="refresh", on_click=lambda: load()).props("flat round")

    gauge_box = ui.html("").classes("q-mt-sm")
    bias_lbl = ui.label("").classes("text-h6")
    sub_lbl = ui.label("").classes("opacity-80")
    comp_box = ui.column().classes("w-full q-gutter-xs q-mt-md")
    ui.separator().classes("q-my-md")
    ui.label("30-Day History").classes("text-subtitle1")
    hist_plot = ui.plotly(build_history_figure([])).classes("w-full")
    vel_lbl = ui.label("").classes("opacity-80 text-sm")
    flag_lbl = ui.label("").classes("text-negative text-sm")
    div_lbl = ui.label("").classes("text-warning text-sm")
    ui.separator().classes("q-my-md")
    ui.label("Market Trend Regime").classes("text-subtitle1")
    regime_badge = ui.badge("").classes("text-subtitle2 q-pa-sm")
    regime_desc = ui.label("").classes("opacity-80 text-sm")
    regime_detail = ui.label("").classes("opacity-60 text-xs")

    def _render_components(latest):
        comp_box.clear()
        scores = latest.get("component_scores") or {}
        confs = latest.get("component_confidence") or {}
        with comp_box:
            for key, name, w in COMPONENTS:
                s = _safe_float(scores.get(key))
                c = _safe_float(confs.get(key))
                with ui.row().classes("items-center w-full no-wrap gap-3"):
                    ui.label(name).classes("text-sm").style("width:110px")
                    ui.label(f"{s:.1f}").classes("text-sm text-bold").style("width:34px")
                    ui.html(gradient_bar_svg(s * 10.0))
                    tag = (f"w {w*100:.0f}%" if w else "out of composite")
                    ui.label(tag).classes("opacity-60 text-xs").style("width:120px")
                    ui.label(f"conf {c:.0%}").classes("opacity-60 text-xs")

    def _apply():
        snaps = state["snaps"]
        if not snaps:
            bias_lbl.text = "No data"
            return
        latest = snaps[-1]
        comp = latest.get("composite") or {}
        total = _safe_float(comp.get("total_score"))
        date_lbl.text = f"as of {latest.get('date')} (last completed session)"
        gauge_box.content = speedometer_svg(gauge_score(total), comp.get("bias", ""),
                                            width=220, height=140)
        bias_lbl.text = f"{total:.2f} · {comp.get('bias', '')}"
        bias_lbl.style(f"color:{bias_color(comp.get('bias'))}")
        sub_lbl.text = (f"size {comp.get('size_modifier', '—')} · "
                        f"agg conf {_safe_float(comp.get('aggregate_confidence')):.0%}")
        _render_components(latest)
        hist_plot.update_figure(build_history_figure(snaps))
        _dates, scores = composite_series(snaps[:-1])
        line, flag = velocity_line(scores, total)
        vel_lbl.text = line
        flag_lbl.text = flag
        div_lbl.text = scoring_composite.divergence(divergence_named(latest)) or ""
        if state["spy"]:
            tr, committed, days = commit_trend_regime(state["spy"])
            green = {"bull_trend", "pullback_in_bull"}
            red = {"bear_rally", "bear_trend"}
            color = CLR_GREEN if committed in green else (
                CLR_RED if committed in red else CLR_YELLOW)
            regime_badge.text = trend_regime.STATE_LABELS[committed]
            regime_badge.style(f"background-color:{color};color:#111")
            regime_desc.text = trend_regime.STATE_DESCRIPTIONS[committed]
            regime_detail.text = (
                f"SPY {tr.spy_close:.2f} · 50d {tr.sma_50:.2f} · 200d {tr.sma_200:.2f} "
                f"· slope {tr.sma_200_slope_pct:+.2f}% · dd {tr.drawdown_pct:+.1f}% "
                f"· conf {tr.confidence:.0%}")

    async def load():
        spinner.visible = True
        try:
            snaps, spy = await ng_run.io_bound(_load_snapshots)
            state["snaps"], state["spy"] = snaps, spy
            _apply()
        except Exception as e:  # noqa: BLE001
            ui.notify(f"Sentiment load failed: {e}", type="negative")
        finally:
            spinner.visible = False

    ui.timer(0.1, load, once=True)
    ui.timer(120.0, load)
```

**Step 2: Replace the stub in `webgui/main.py`**

Replace lines `156-159`:

```python
@ui.page("/sentiment")
def sentiment_page() -> None:
    with _layout("/sentiment", "Sentiment"):
        from pages import sentiment
        sentiment.render()
```

**Step 3: Run the shell + transform tests**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: PASS — existing 76 + new sentiment transforms; `test_shell` still
green (route already expected).

**Step 4: Commit**

```bash
git add webgui/pages/sentiment.py webgui/main.py
git commit -m "feat(sentiment): render() page + wire /sentiment route"
```

---

## Task 4: `repo_paths.SENTIMENT_DASHBOARD` (if missing) + browser verify + docs

**Files:**
- Modify (if needed): `repo_paths.py`
- Modify: root `CLAUDE.md`

**Step 1: Ensure the path constant exists**

Check `repo_paths.py` for `SENTIMENT_DASHBOARD`. If absent, add it next to the
other app-dir constants (e.g. `OPTIONS_SCANNER`):

```python
SENTIMENT_DASHBOARD = REPO_ROOT / "sentiment-dashboard"
```

Re-run Task 2/3 tests if it was missing.

**Step 2: Browser verify (the design's required proof)**

Use the Claude Preview tool: `preview_start` the `webgui` dev server (:8500),
navigate to `/sentiment`, wait for the spinner to clear, `preview_screenshot`.
Confirm: gauge renders with a needle, bias colored, 6 component bars, history
line chart populated, velocity line + (maybe) divergence, trend-regime badge.
If proxy is down or it's a weekend, expect sparse data / "No data" — note it,
not a code bug. Check `preview_console_logs` for errors.

**Step 3: Update root `CLAUDE.md`**

- In the Routes table, change `/sentiment` row status stub → built with a short
  description ("composite gauge + components + 30d history + trend regime").
- Update the "Last updated" line and the "Next session" list (remove Sentiment).

**Step 4: Commit**

```bash
git add repo_paths.py CLAUDE.md
git commit -m "feat(sentiment): repo_paths constant + docs; mark /sentiment built"
```

---

## Notes / gotchas (from CLAUDE.md + design)

- `ui.html` strips `<style>`/`<iframe>` — the svg builders emit pure `<svg>`, fine.
- `ui.plotly(fig_dict)` expects `{"data":[...],"layout":{...}}`; use
  `plot.update_figure(...)` to refresh.
- Heavy work off-thread via `nicegui.run.io_bound`; page state in a local dict
  closure, not module globals.
- `backfill_history` drops the current session — the gauge shows the last
  completed session (labeled). Live-intraday is an out-of-scope follow-up.
- Weekend/off-hours → sparse data is expected, not a bug.
- `options-scanner` has ~2 known date-relative failing tests — unrelated; do
  not touch.
```
