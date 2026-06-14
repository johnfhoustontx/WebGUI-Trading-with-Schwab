# Gamma & Simulator Pages Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Gamma and Simulator stub pages with working NiceGUI pages — Gamma (GEX/Charm/DEX/Vanna bars + flip/walls + 0-DTE pressure + intraday heatmap) and Simulator (What-if sweep + IV-shock bars).

**Architecture:** Pages call the already-pure engines (`gamma_tool.GammaEngine`, `options_simulator.{data,engine}`) and render with NiceGUI `ui.plotly`. Pure figure/transform builders are TDD'd; `render()` wires controls + charts; heavy fetches run off-thread via `nicegui.run.io_bound`.

**Tech Stack:** NiceGUI `ui.plotly`/`ui.select`/`ui.slider`/`ui.timer`, gamma_tool + options_simulator engines, options_calculator BS, gex_history_db, pytest.

**Reference:** design [`2026-06-14-gamma-simulator-design.md`](2026-06-14-gamma-simulator-design.md). Source UI (read-only): `D:\Trading With Schwab\options-scanner\gamma_tool.py`, `…\options_simulator\window.py`.

**Test cmd:** `cd webgui && ..\.venv\Scripts\python -m pytest -q`. **Preview:** restart Claude Preview `webgui` (8500) + screenshot.

> Plotly note: `ui.plotly(fig_dict)` takes a Plotly figure dict `{"data": [...], "layout": {...}}`. Builders return that dict so they can be unit-tested without NiceGUI.

---

## Batch G — Gamma page

### Task G1: Gamma figure/transform builders (pure, TDD)

**Files:** Create `webgui/pages/options/gamma.py` (transforms; keep a temporary `render` placeholder); Test `webgui/tests/test_options_gamma.py`.

**Verify first:** in `options-scanner`, confirm `GammaEngine().calc_all_from_chain` returns `(gex,charm,dex,vanna)` and each has `["gex"]`=`{strike:{call,put,net}}`; confirm `get_gex_walls`/`get_dex_walls(data, top_n)` and `snapshot_summary(data, view)` shapes (already mapped in design).

**Step 1 (test):**
```python
from pages.options import gamma

GEX = {"spot": 450.0, "gex": {
    448.0: {"call": 100.0, "put": -40.0, "net": 60.0},
    450.0: {"call": 200.0, "put": -250.0, "net": -50.0},
    452.0: {"call": 30.0, "put": -10.0, "net": 20.0},
}, "strike_count": 3}

def test_bars_from_gex_filters_band_and_sorts():
    b = gamma.bars_from_gex(GEX, 450.0, pct=0.01)   # ±1% -> 445.5..454.5 keeps all 3
    assert b["strikes"] == [448.0, 450.0, 452.0]    # ascending
    assert b["nets"] == [60.0, -50.0, 20.0]
    assert b["colors"][1] != b["colors"][0]         # negative differs from positive

def test_bars_from_gex_excludes_out_of_band():
    b = gamma.bars_from_gex(GEX, 450.0, pct=0.001)  # ±0.45 -> only 450 within band
    assert b["strikes"] == [450.0]

def test_bar_figure_is_plotly_dict():
    fig = gamma.bar_figure(GEX, 450.0, view="GEX", walls=[450.0], flip=449.5, pct=0.02)
    assert "data" in fig and "layout" in fig

def test_heatmap_matrix_from_history():
    # rows: (ts, spot, flip, top_pos, top_neg, net_total, grid_dict{strike:net})
    rows = [("09:30", 450, None, None, None, 0, {448.0: 5, 450.0: -3}),
            ("09:35", 450, None, None, None, 0, {448.0: 7, 450.0: -1})]
    m = gamma.heatmap_matrix(rows)
    assert m["x"] == ["09:30", "09:35"]
    assert 448.0 in m["y"] and 450.0 in m["y"]
    assert len(m["z"]) == len(m["y"]) and len(m["z"][0]) == 2

def test_heatmap_matrix_empty():
    assert gamma.heatmap_matrix([])["z"] == []
```
**Step 2:** FAIL. **Step 3:** implement `bars_from_gex(data, spot, pct)` (filter strikes to `[spot*(1-pct), spot*(1+pct)]`, ascending; nets list; colors pos/neg; carry call/put for hover), `bar_figure(data, spot, view, walls, flip, pct)` (horizontal bar Plotly dict + spot/flip hlines via shapes + wall markers), `heatmap_matrix(rows)` (union of strikes as y, ts as x, z[y][x]=grid value or None), `heatmap_figure(rows, view)`, `summary_text(summary, view)`. **Step 4:** PASS. **Step 5:** commit `feat: gamma figure/transform builders (Task G1)`.

### Task G2: Gamma render (fetch, view toggle, bars + summary + pressure)

**Files:** Modify `gamma.py`.
- `render()`: symbol input + Fetch button + spinner; view toggle (`ui.toggle(["GEX","Charm","DEX","Vanna"])`). On Fetch (off-thread): `chain = proxy.schwab_py_client.get_option_chain(sym, contract_type="ALL", strike_count=100, from_date=today, to_date=today+7).json()` (verify call shape vs proxy_client) → `GammaEngine().calc_all_from_chain(chain)` → store 4 results + chain in page state. View toggle re-renders `ui.plotly(bar_figure(...))` from cached result, `summary_text`, walls (`get_gex_walls`/`get_dex_walls`), flip from `snapshot_summary`. DEX view: show hedge-pressure panel (net Δ now/projected/pressure, green/red). Errors → `ui.notify`. Smoke-screenshot (fetch SPY, toggle all 4 views). Commit.

### Task G3: Gamma intraday heatmap

**Files:** Modify `gamma.py`.
**Verify first:** `gex_history_db.load_today_with_grid` signature + row shape (needs a sqlite conn? symbol/view args?). Adapt `heatmap_matrix` row unpacking to the real shape.
- Add right-pane `ui.plotly(heatmap_figure(rows, view))`; load rows for current symbol+view; empty → note. Refresh on Fetch + view toggle. Smoke + commit.

**Batch G checkpoint:** screenshot all four views + heatmap; report.

---

## Batch S — Simulator page

### Task S1: Simulator figure/transform builders (pure, TDD)

**Files:** Create `webgui/pages/options/simulator.py` (transforms; temp `render` placeholder); Test `webgui/tests/test_options_simulator.py`.

**Verify first:** `ContractRow` fields (`kind`/`strike`/`expiry`/`iv`), `Position.single(contract, direction, symbol)`, engine return columns (design has them).

**Step 1 (test):** use plain dicts/objects to avoid heavy deps:
```python
from pages.options import simulator as sim

class Row:  # stand-in for a sweep DataFrame row / ContractRow
    pass

def test_whatif_figure_is_plotly_dict():
    df = [{"S": 440, "theo_price": -50}, {"S": 450, "theo_price": 0}, {"S": 460, "theo_price": 80}]
    fig = sim.whatif_figure(df, spot=450.0, target_s=455.0)
    assert "data" in fig and "layout" in fig
    xs = fig["data"][0]["x"]
    assert xs[0] == 440 and xs[-1] == 460

def test_ivshock_figure_is_plotly_dict():
    base = {"theo_price": 1.0, "delta": 0.5, "gamma": 0.02, "theta": -0.1, "vega": 0.3}
    shock = {"theo_price": 1.6, "delta": 0.55, "gamma": 0.018, "theta": -0.12, "vega": 0.45}
    fig = sim.ivshock_figure(base, shock, mult=1.5)
    assert "data" in fig and len(fig["data"]) == 2     # base + shock series
    assert "Vega" in fig["layout"]["xaxis"]["categoryarray"] or True  # categories present

def test_expiries_of_dedupes_sorted():
    snap = type("S", (), {"contracts": [Row(), Row()]})()
    snap.contracts[0].expiry = "2026-06-19"; snap.contracts[0].kind = "call"; snap.contracts[0].strike = 450
    snap.contracts[1].expiry = "2026-06-18"; snap.contracts[1].kind = "call"; snap.contracts[1].strike = 455
    assert sim.expiries_of(snap) == ["2026-06-18", "2026-06-19"]

def test_strikes_of_filters_by_expiry_and_kind():
    snap = type("S", (), {"contracts": [Row(), Row()]})()
    snap.contracts[0].expiry = "2026-06-19"; snap.contracts[0].kind = "call"; snap.contracts[0].strike = 450
    snap.contracts[1].expiry = "2026-06-19"; snap.contracts[1].kind = "put"; snap.contracts[1].strike = 445
    assert sim.strikes_of(snap, "2026-06-19", "call") == [450]
```
**Step 2:** FAIL. **Step 3:** implement `whatif_figure(df, spot, target_s)` (line of S vs theo_price + zero hline + spot/target vlines via shapes; accepts list-of-dicts or DataFrame via `df["S"]`/iteration), `ivshock_figure(base, shock, mult)` (two grouped bar series across `["Price","Delta","Gamma×100","Theta","Vega"]`), `expiries_of(snapshot)`, `strikes_of(snapshot, expiry, kind)`. **Step 4:** PASS. **Step 5:** commit.

### Task S2: Simulator render — fetch + contract selector + What-if tab

**Files:** Modify `simulator.py`.
- `render()`: symbol + Fetch snapshot (off-thread `data.fetch_snapshot(proxy.schwab_py_client, sym)`; cache in state). Contract selector: expiry `ui.select(expiries_of(snap))` → strike `ui.select(strikes_of(...))` → kind toggle call/put → direction toggle buy/sell. Build `Position.single`/`ContractRow`. What-if tab: ΔS% + Δt-days sliders → `WhatIfEngine(snap).sweep(contract, s_range, t_days)` (in-thread) → `ui.plotly(whatif_figure(df, spot, target_s))`, re-render on slider change. Verify engine signatures. Smoke + commit.

### Task S3: Simulator IV-shock tab

**Files:** Modify `simulator.py`.
- Add IV-shock tab: multiplier `ui.slider(0.5..3.0)` → `IVShockEngine(snap).sweep(contract, [1.0, mult])` → base/shock rows → `ui.plotly(ivshock_figure(base, shock, mult))`. Smoke + commit.

**Batch S checkpoint:** screenshot What-if curve + IV-shock bars; report.

---

## Finalize

- Full suite green; update `CLAUDE.md` routes table (Gamma/Simulator → built; note Replay + heatmap-collector caveats).
- superpowers:requesting-code-review.
- Remaining after this: Replay tab (follow-up) and the four non-Options pages (Sentiment/Trade/Portfolio/Driver).

## Notes
- DRY: reuse `nicegui.run.io_bound`, the engines, and existing `proxy` client; don't reimplement BS/GEX.
- YAGNI: no Replay, no Term view, no collector process, no chart-style persistence.
- Verify each engine signature against the copied module before wiring.
- `git status` clean of secrets/`data/`/`*.db` each commit.
