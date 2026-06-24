# Trade Analyzer — theme + layout + Markov near-term fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Theme the `/trade` page with the shared dark-navy "dashboard" look, compact its dead space, fix the Markov forecast chart so it reflects each symbol's composite score (surface the near-term trajectory), make tab-out trigger Analyze, and persist the last analyzed symbol across navigation.

**Architecture:** The Markov forecast converges to the bull-leaning pooled-prior stationary within ~10 days, so the 10d/20d chart columns are score-independent. Fix = emit an **additive** dense near-term `trajectory` (1/2/3/5/10/20d) from the service (reusing the tested `forecast()`, leaving the tilt/adjusted-score math untouched) and plot it. The rest is page wiring: the documented `.calc-v2` theme pattern, an `items-start` compact layout, a `blur`→analyze handler with double-fire dedupe, and seeding the symbol input from the persisted cache result.

**Tech Stack:** Python, NiceGUI (`ui.highchart`, Quasar), numpy/pandas, Redis (Memurai) via `shared.bus`, pytest. Design doc: [`2026-06-24-trade-analyzer-theme-layout-markov-design.md`](2026-06-24-trade-analyzer-theme-layout-markov-design.md).

**Branch:** `Using_Highcharts` (the long-lived dev branch — work directly on it).

---

### Task 1: Service emits a dense near-term `trajectory` in the Markov block

**Files:**
- Modify: `services/trade_svc/compute.py` (constant near `:436`; body of `build_markov_block` `:443-475`)
- Test: `services/trade_svc/tests/test_markov_analyze.py`

**Step 1: Write the failing test**

Append to `services/trade_svc/tests/test_markov_analyze.py`:

```python
def test_build_markov_block_has_dense_trajectory(monkeypatch):
    # The chart needs denser near-term horizons than the metric cards. The block
    # must carry an additive `trajectory` (1/2/3/5/10/20d) while `horizons`
    # (the cards/tilt source) stays 5/10/20 and the tilt math is unchanged.
    monkeypatch.setattr(compute, "get_prior",
                        lambda: (np.full((5, 5), 0.2), "test"))
    bands = pd.Series([2, 2, 3, 3, 4, 3, 3, 4] * 30, dtype=float)
    block = compute.build_markov_block(bands, composite_daily_now=25.0,
                                       composite_full=38.0)
    assert [h["n"] for h in block["horizons"]] == [5, 10, 20]
    assert [t["n"] for t in block["trajectory"]] == [1, 2, 3, 5, 10, 20]
    assert all(len(t["dist"]) == 5 for t in block["trajectory"])
    assert all(abs(sum(t["dist"]) - 1.0) < 1e-6 for t in block["trajectory"])
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests\test_markov_analyze.py::test_build_markov_block_has_dense_trajectory -v`
Expected: FAIL with `KeyError: 'trajectory'`.

**Step 3: Write minimal implementation**

In `services/trade_svc/compute.py`, add the constant beside `_MK_HORIZONS` (`:436`):

```python
_MK_HORIZONS = [5, 10, 20]
# Denser near-term horizons for the CHART ONLY. The 5/10/20d forecast converges to
# the (bull-leaning) prior stationary, so the chart looked identical for every
# symbol; the near-term transient is the score-specific part. This does NOT feed the
# tilt (still horizon _MK_DRIFT_HORIZON) or the metric cards (still _MK_HORIZONS).
_MK_TRAJECTORY_HORIZONS = [1, 2, 3, 5, 10, 20]
```

In `build_markov_block`, right after `fc = _markov.forecast(P, current, _MK_HORIZONS)` (`:454`):

```python
        fc = _markov.forecast(P, current, _MK_HORIZONS)
        traj = _markov.forecast(P, current, _MK_TRAJECTORY_HORIZONS)["horizons"]
```

And add one key to the returned dict (right after the `"horizons": fc["horizons"],` line `:466`):

```python
            "horizons": fc["horizons"],
            "trajectory": [{"n": h["n"], "dist": h["dist"]} for h in traj],
```

**Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests\test_markov_analyze.py -v`
Expected: PASS (new test + all 4 existing — `markov_adjusted_score`/`tilt` unchanged).

**Step 5: Commit**

```bash
git add services/trade_svc/compute.py services/trade_svc/tests/test_markov_analyze.py
git commit -m "feat(trade): emit dense near-term Markov trajectory for the chart"
```

---

### Task 2: Figure builder plots the dense trajectory + dark-navy theme

**Files:**
- Modify: `webgui/pages/trade.py` (`markov_forecast_figure` `:174-207`; add axis-style constants near `:119`)
- Test: `webgui/tests/test_trade.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_trade.py` (the `_MK` fixture there already has `horizons` 5/10/20 and **no** `trajectory`):

```python
def test_markov_figure_uses_dense_trajectory():
    mk = dict(_MK, trajectory=[
        {"n": 1, "dist": [0.00, 0.00, 0.10, 0.60, 0.30]},
        {"n": 2, "dist": [0.00, 0.05, 0.15, 0.50, 0.30]},
        {"n": 3, "dist": [0.05, 0.05, 0.20, 0.45, 0.25]},
        {"n": 5, "dist": [0.05, 0.10, 0.20, 0.40, 0.25]},
        {"n": 10, "dist": [0.05, 0.10, 0.15, 0.40, 0.30]},
        {"n": 20, "dist": [0.05, 0.10, 0.15, 0.35, 0.35]},
    ])
    fig = trade.markov_forecast_figure(mk)
    assert fig["xAxis"]["categories"] == ["now", "1d", "2d", "3d", "5d", "10d", "20d"]
    assert all(len(s["data"]) == 7 for s in fig["series"])
    assert fig["chart"]["backgroundColor"] == "transparent"  # dark theme


def test_markov_figure_falls_back_to_horizons_without_trajectory():
    # Back-compat: an older block with only `horizons` still renders 5/10/20.
    fig = trade.markov_forecast_figure(_MK)
    assert fig["xAxis"]["categories"] == ["now", "5d", "10d", "20d"]
    assert all(len(s["data"]) == 4 for s in fig["series"])


def test_markov_figure_differs_by_current_band():
    # Regression guard for "looks the same regardless of score": a bullish vs a
    # bearish near-term trajectory must produce different early series data.
    labels = _MK["band_labels"]
    bull = trade.markov_forecast_figure(
        {"current_band": 4, "band_labels": labels,
         "trajectory": [{"n": 1, "dist": [0.0, 0.0, 0.1, 0.3, 0.6]}]})
    bear = trade.markov_forecast_figure(
        {"current_band": 0, "band_labels": labels,
         "trajectory": [{"n": 1, "dist": [0.6, 0.3, 0.1, 0.0, 0.0]}]})
    assert bull["series"][4]["data"][0] == 1.0   # now one-hot at band 4 (Strong-Bull)
    assert bear["series"][0]["data"][0] == 1.0   # now one-hot at band 0 (Strong-Bear)
    assert bull["series"][4]["data"] != bear["series"][4]["data"]
```

**Step 2: Run tests to verify they fail**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py::test_markov_figure_uses_dense_trajectory tests/test_trade.py::test_markov_figure_differs_by_current_band -q`
Expected: FAIL (categories are `["now","5d","10d","20d"]`; no `backgroundColor` key).

**Step 3: Write the implementation**

In `webgui/pages/trade.py`, add near the band-color constants (`:119-121`):

```python
# Dark-navy chart styling (matches the dashboard theme used by the Calculator/Simulator).
_MK_AXIS_STYLE = {"color": "#bdbdbd"}
_MK_GRID_COLOR = "rgba(255,255,255,0.08)"
```

Replace `markov_forecast_figure` (`:174-207`) entirely with:

```python
def markov_forecast_figure(mk):
    """Highcharts stacked-area option dict: band probability over horizon.

    Plots the DENSE near-term trajectory (now/1/2/3/5/10/20d from ``mk['trajectory']``,
    falling back to the sparse ``mk['horizons']`` when absent) so the score-specific
    early path is visible — the 10d/20d tail converges to the prior stationary and is
    near-identical across symbols, which made the chart look the same for every stock.
    Dark-navy themed (transparent bg, light axes) to sit on the dashboard navy.

    Tolerates None (returns an empty-but-valid figure with an explicit height so the
    persistent chart element renders at a stable size before data arrives)."""
    base = {
        "accessibility": {"enabled": False},
        "chart": {"type": "area", "height": 200, "backgroundColor": "transparent"},
        "title": {"text": None},
        "credits": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#cdd8ee"},
                   "itemHoverStyle": {"color": "#ffffff"}},
    }
    if not mk:
        return {**base, "series": []}
    labels = mk.get("band_labels") or ["?"] * 5
    points = mk.get("trajectory") or mk.get("horizons") or []
    cats = ["now"] + [f"{h['n']}d" for h in points]
    now = [0.0] * 5
    cb = mk.get("current_band", 2)
    if 0 <= cb < 5:
        now[cb] = 1.0
    dists = [now] + [h["dist"] for h in points]
    series = [{
        "name": labels[b] if b < len(labels) else "?",
        "color": _MK_AREA_COLORS[b],
        "data": [round(d[b] if b < len(d) else 0.0, 4) for d in dists],
    } for b in range(5)]
    return {
        **base,
        "xAxis": {"categories": cats, "labels": {"style": _MK_AXIS_STYLE},
                  "lineColor": _MK_GRID_COLOR, "tickColor": _MK_GRID_COLOR},
        "yAxis": {"min": 0, "max": 100, "title": {"text": "P(band)", "style": _MK_AXIS_STYLE},
                  "labels": {"format": "{value}%", "style": _MK_AXIS_STYLE},
                  "gridLineColor": _MK_GRID_COLOR},
        "plotOptions": {"area": {"stacking": "percent", "marker": {"enabled": False}}},
        "series": series,
    }
```

**Step 4: Run tests to verify they pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py -q`
Expected: PASS — the new trajectory tests, the back-compat/`_MK` tests (`test_markov_figure_category_data_alignment`, `test_markov_empty_horizons`), and the shape/None tests all pass.

**Step 5: Commit**

```bash
git add webgui/pages/trade.py webgui/tests/test_trade.py
git commit -m "feat(trade): plot dense Markov trajectory + dark-navy chart theme"
```

---

### Task 3: Pure helpers for symbol seeding + analyze dedupe

**Files:**
- Modify: `webgui/pages/trade.py` (module-level, near the other pure helpers ~`:117`)
- Test: `webgui/tests/test_trade.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_trade.py`:

```python
def test_seed_symbol():
    assert trade.seed_symbol({"symbol": "TSLA"}) == "TSLA"
    assert trade.seed_symbol(None) == "AAPL"
    assert trade.seed_symbol({}) == "AAPL"


def test_should_request():
    # changed symbol fires immediately; an identical repeat within the window is
    # deduped (collapses the blur-then-click double fire); a repeat after the
    # window is a deliberate refresh; empty never fires.
    assert trade.should_request("TSLA", "AAPL", 0.1) is True
    assert trade.should_request("tsla", "TSLA", 0.1) is False
    assert trade.should_request("TSLA", "TSLA", 2.0) is True
    assert trade.should_request("   ", "AAPL", 9.0) is False
```

**Step 2: Run tests to verify they fail**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py::test_seed_symbol tests/test_trade.py::test_should_request -q`
Expected: FAIL (`AttributeError: module 'pages.trade' has no attribute 'seed_symbol'`).

**Step 3: Write the implementation**

In `webgui/pages/trade.py`, add after the `alignment_rows` helper (~`:117`):

```python
def seed_symbol(result):
    """The symbol to pre-fill the input on (re)build: the last analyzed symbol from
    the persisted cache result, else the AAPL default (so revisiting the page shows
    the symbol that matches the displayed analysis)."""
    sym = (result or {}).get("symbol")
    return sym if sym else "AAPL"


def should_request(symbol, last_requested, since_seconds):
    """True when an analyze request should fire for ``symbol``.

    Non-empty, AND (the symbol changed since the last request OR enough time has
    passed). The time guard collapses the blur-then-click double fire — clicking
    Analyze blurs the field first, so blur + click would otherwise enqueue twice —
    while still allowing a deliberate same-symbol refresh seconds later."""
    s = (symbol or "").strip().upper()
    if not s:
        return False
    return s != (last_requested or "") or since_seconds >= 1.0
```

**Step 4: Run tests to verify they pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py::test_seed_symbol tests/test_trade.py::test_should_request -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/trade.py webgui/tests/test_trade.py
git commit -m "feat(trade): pure helpers for symbol seeding + analyze dedupe"
```

---

### Task 4: Apply the dark-navy theme + compact-in-place layout

**Files:**
- Modify: `webgui/pages/trade.py` (`render` `:218-250` top section + the card builder funcs)
- Test: `webgui/tests/test_trade.py::test_render_is_callable` (existing smoke) + browser screenshot

**Step 1: Add the theme import**

At the top of `webgui/pages/trade.py`, beside the existing `from .options.inputs import select_all_on_focus` (`:26`):

```python
from .options.inputs import select_all_on_focus
from .options.theme import DASHBOARD_CSS
```

**Step 2: Wrap the render body in `.calc-v2` + theme the controls (compact layout)**

Replace the top of `render()` from `ui.label("Trade Analyzer")` (`:220`) through the `results_bottom = ...` line (`:250`) with (note `items-stretch` → `items-start`, `calc-card` on the cards, the navy title, the themed button, and a `.calc-v2` wrapper):

```python
    ui.add_css(DASHBOARD_CSS)

    # Page state (local closure, not module globals — built per request).
    state = {"result": None, "ver": None, "last_requested": None, "last_ts": 0.0}

    with ui.column().classes("calc-v2 w-full gap-3"):
        ui.label("Trade Analyzer").classes("text-h6").style("color:#eaf0fb")

        with ui.row().classes("items-center gap-3 flex-wrap"):
            symbol_in = select_all_on_focus(ui.input("Symbol", value="AAPL").classes("w-32"))
            analyze_btn = ui.button("Analyze", icon="analytics", color=None) \
                .props("no-caps").classes("cv2-btn-primary")
            status = ui.label("Enter a symbol and click Analyze.").classes("calc-eyebrow")

        # Layout: a top area (error banner + header), then a single verdict ROW of
        # three EQUAL-width cards (Position · Investor · Markov Forecast), then a
        # bottom area (MTF/momentum/sector). results_top/bottom are cleared+rebuilt on
        # repaint; the verdict row and its three cards are PERSISTENT — the Markov card
        # holds a Highcharts element that must exist at first render and must not be
        # destroyed by a clear(), so the verdict cards are refilled in place.
        # items-start (not items-stretch): the short Position/Investor cards no longer
        # stretch to the tall Markov card, removing the dead space.
        results_top = ui.column().classes("w-full gap-2")
        verdict_row = ui.row().classes("w-full gap-3 items-start flex-wrap")
        with verdict_row:
            position_card = ui.card().classes("calc-card flex-1 min-w-[280px]")
            investor_card = ui.card().classes("calc-card flex-1 min-w-[280px]")
            markov_card = ui.card().classes("calc-card flex-1 min-w-[280px]")
            with markov_card:
                ui.label("Markov Forecast · composite-score regime").classes("calc-eyebrow")
                markov_head = ui.row().classes("items-center gap-3 flex-wrap")
                markov_metrics = ui.row().classes("gap-4 flex-wrap")
                markov_chart = ui.highchart(markov_forecast_figure(None)).classes("w-full")
        verdict_row.set_visibility(False)
        markov_card.set_visibility(False)
        results_bottom = ui.column().classes("w-full gap-2")
```

> NOTE: the closure defs (`_header`, `_fill_verdict_card`, …) and the wiring/initial-paint
> stay at the function-body indent **after** this `with` block — they only reference the
> captured element vars and re-enter `with results_top:`/`with markov_card:` when called,
> so they must NOT be indented into the `with` above.

**Step 3: Add `calc-card` to the builder-function cards**

- `_header` (`:254`): `with ui.card().classes("w-full"):` → `with ui.card().classes("calc-card w-full"):`
- `_alignment_card` (`:305`): `ui.card().classes("flex-1 min-w-[220px]")` → `ui.card().classes("calc-card flex-1 min-w-[220px]")`
- `_momentum_card` (`:317`): `ui.card().classes("flex-1 min-w-[200px]")` → `ui.card().classes("calc-card flex-1 min-w-[200px]")`
- `_fundamentals_card` (`:325`): `ui.card().classes("flex-1 min-w-[200px]")` → `ui.card().classes("calc-card flex-1 min-w-[200px]")`
- `_sector_card` (`:335`): `ui.card().classes("flex-1 min-w-[200px]")` → `ui.card().classes("calc-card flex-1 min-w-[200px]")`

Also change the eyebrow labels inside these cards from `.classes("text-subtitle2 opacity-70")` to `.classes("calc-eyebrow")` for theme consistency (the `ui.label("MTF EMA alignment")`, `"Momentum"`, `"Fundamentals"`, `"Sector"`, and the `_fill_verdict_card` title label `:280`, and the `"Markov Forecast …"` label — already changed in Step 2).

**Step 4: Verify render still constructs + smoke test**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py -q`
Expected: PASS (incl. `test_render_is_callable`).

**Step 5: Commit**

```bash
git add webgui/pages/trade.py
git commit -m "feat(trade): apply dark-navy dashboard theme + compact layout"
```

---

### Task 5: Tab-out triggers Analyze + persist last analyzed symbol

**Files:**
- Modify: `webgui/pages/trade.py` (`import time`; `render` — symbol seeding, `_request_analyze` `:434-441`, event wiring `:443-444`, initial paint `:458-464`)

**Step 1: Import time**

At the top of `webgui/pages/trade.py`:

```python
import time

import bus_client
```

**Step 2: Read the cache up-front + seed the input**

The input value must seed from the persisted result, which requires reading the cache **before** the input is created. In `render()`, replace the `state = {...}` line added in Task 4 and seed the input. Just after `ui.add_css(DASHBOARD_CSS)`:

```python
    ui.add_css(DASHBOARD_CSS)

    # Page state (local closure). Read any prior cached analysis up-front so the
    # symbol field seeds to the LAST analyzed symbol (the result itself already
    # persists across navigation via the trade:analysis cache).
    state = {"result": None, "ver": None, "last_requested": None, "last_ts": 0.0}
    state["ver"] = bus_client.read_version("trade:analysis")
    state["result"] = bus_client.read("trade:analysis") or None
    seed = seed_symbol(state["result"])
    state["last_requested"] = seed
```

And change the input creation (in the Task-4 block) from `value="AAPL"` to `value=seed`:

```python
            symbol_in = select_all_on_focus(ui.input("Symbol", value=seed).classes("w-32"))
```

**Step 3: Dedupe `_request_analyze` + wire the blur event**

Replace `_request_analyze` (`:434-441`) with:

```python
    @guard
    def _request_analyze():
        sym = (symbol_in.value or "").strip().upper()
        if not sym:
            return
        if not should_request(sym, state["last_requested"], time.monotonic() - state["last_ts"]):
            return
        state["last_requested"] = sym
        state["last_ts"] = time.monotonic()
        bus_client.request("trade", {"type": "analyze", "args": {"symbol": sym}})
        status.text = f"Analyzing {sym}…"
```

Add the blur handler beside the existing click/enter wiring (`:443-444`):

```python
    analyze_btn.on_click(_request_analyze)
    symbol_in.on("keydown.enter", lambda e: _request_analyze())
    symbol_in.on("blur", lambda e: _request_analyze())  # tab-out = Analyze
```

**Step 4: Drop the duplicate cache read at the bottom**

The initial paint block (`:458-464`) now re-reads the cache that Step 2 already read. Remove the two read lines, keep the paint + timer:

```python
    # Initial paint (graceful-empty when the service is cold / no prior analysis).
    _render_results()
    _update_markov(state["result"])
    status.text = _status_for(state["result"])
    ui.timer(2.0, _poll)
```

**Step 5: Verify + commit**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_trade.py -q`
Expected: PASS.

```bash
git add webgui/pages/trade.py
git commit -m "feat(trade): tab-out triggers Analyze + persist last symbol"
```

---

### Task 6: Full test sweep + live verification

**Files:** none (verification only)

**Step 1: Run both affected suites**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: all green (was 430).

Run: `.venv\Scripts\python -m pytest services\trade_svc\tests -v`
Expected: all green.

**Step 2: Restart `trade_svc` to pick up the compute change**

The running service is stale after editing `compute.py`. Restart it (free port 8213, relaunch):

Run (background): `tools\restart_one.bat 8213 8100 services\trade_svc\app.py`
(or stop the existing `services\trade_svc\app.py` console and relaunch `.venv\Scripts\python services\trade_svc\app.py`.)

**Step 3: Live-verify the trajectory differs by symbol (Redis-driven)**

```python
.venv\Scripts\python -c "
import time
from shared.bus import Bus
b = Bus()
for s in ['NVDA', 'INTC']:
    b.enqueue_command('cmd:trade', {'type':'analyze','args':{'symbol':s}})
    time.sleep(8)
    mk = (b.cache_get('cache:trade:analysis').payload or {}).get('markov') or {}
    grn = lambda d: round(d[3]+d[4], 2)
    print(s, 'band', mk.get('current_band'),
          'traj green:', [grn(t['dist']) for t in mk.get('trajectory', [])])
"
```
Expected: the two symbols print **different** near-term green fractions (the early columns diverge), confirming the chart will differ by score.

**Step 4: Screenshot the themed page**

Start the preview (`webgui`, :8500), navigate to `/trade`, analyze a symbol, screenshot. Confirm: navy gradient + bordered cards, no large dead space below Position/Investor, the Markov chart on transparent navy with a dense now→20d x-axis. Then navigate away and back → the symbol field holds the last symbol and the analysis is still shown. Tab out of the field after changing the symbol → it analyzes.

**Step 5: Update CLAUDE.md + commit**

Update the root `CLAUDE.md` "Last updated" block + the `/trade` route row to note the theme + Markov near-term trajectory. Commit:

```bash
git add CLAUDE.md
git commit -m "docs: note Trade Analyzer theme + Markov near-term trajectory"
```

---

## Notes for the executor
- **DRY/YAGNI/TDD**: pure transforms are TDD'd; `render()` wiring is verified by the smoke test + screenshot (NiceGUI widget wiring isn't unit-tested per the repo convention).
- **Do not** touch the tilt / `markov_adjusted_score` / `horizons` math — the `trajectory` is purely additive and chart-only (the verdict label + adjusted score must stay identical).
- The `.calc-v2` theme auto-restyles inputs/selects; **buttons need `color=None` + a `cv2-*` class**. The factor-breakdown `ui.table` is not restyled by the theme — if it clashes on navy, that's a follow-up, not part of this plan.
- Service-test isolation: run `services\trade_svc\tests` **alone**, never `pytest services` (cross-app module-name collisions).
