# Options Section Expansion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand the webgui Options page into a full section matching the Tk dashboard — expandable nav, two-pane scanner with a shared graphics-rich Trade detail panel, compact header strip, Calculator with a colored P&L heatmap, plus Paper Trades / Captured Signals / Paper Portfolio / Swing Scanner, and Gamma/Simulator stubs.

**Architecture:** Refactor `webgui/pages/options.py` into a `webgui/pages/options/` subpackage. Pure transforms/SVG builders are TDD'd; `render()` functions wire NiceGUI widgets and are smoke-verified. All data comes from the copied options-scanner engines (no reimplementation). Detail panel + header strip are shared modules.

**Tech Stack:** NiceGUI 3.x (`ui.expansion`, `ui.html` for SVG, `ui.table`, `ui.timer`, `nicegui.run.io_bound`), pandas/scipy engines, pytest.

**Reference:** Design doc [`2026-06-14-options-section-expansion-design.md`](2026-06-14-options-section-expansion-design.md). Source UI (read-only): `D:\Trading With Schwab\options-scanner\dashboard.py`.

**Test command (from repo root):** `cd webgui && ..\.venv\Scripts\python -m pytest -q`
**Preview:** restart via Claude Preview `webgui` config (port 8500) and screenshot.

---

## Batch A — Nav restructure, scanner two-pane, shared detail + SVG, header strip, stubs

### Task A1: Subpackage skeleton + move scanner

**Files:**
- Create: `webgui/pages/options/__init__.py`
- Move logic: `webgui/pages/options.py` → `webgui/pages/options/scanner.py` (keep `signal_columns`/`signal_rows`/`_round`; drop the dialog-based `_show_detail`/`_open_detail` — replaced by the shared panel in A3)
- Delete: `webgui/pages/options.py`
- Modify test: `webgui/tests/test_options_page.py` → import `pages.options.scanner as options`
- Modify: `webgui/main.py` home route → `from pages.options import scanner; scanner.render()`

**Steps:**
1. Update `test_options_page.py` imports to `pages.options.scanner`. Run → FAIL (module missing).
2. Create `pages/options/__init__.py` (docstring only). Move `options.py` content to `pages/options/scanner.py`; fix its engine `sys.path` import (still `from scanner_engine import run_full_scan`). Remove dialog code (kept transforms). Delete old `options.py`.
3. Run `test_options_page.py` → PASS (transforms unchanged).
4. Point `main.py` home route at `scanner.render()`. Run full suite → PASS.
5. Commit: `refactor: move options page into pages/options subpackage`.

### Task A2: SVG builders (pure, TDD)

**Files:**
- Create: `webgui/pages/options/svg.py`
- Test: `webgui/tests/test_options_svg.py`

**Step 1 (test):**
```python
from pages.options import svg

def test_speedometer_svg_contains_score_and_svg_tag():
    out = svg.speedometer_svg(72, "A")
    assert out.strip().startswith("<svg")
    assert "</svg>" in out
    assert "72" in out and "A" in out

def test_gradient_bar_clamps_and_renders():
    assert svg.gradient_bar_svg(150).startswith("<svg")   # clamps >100, no error
    assert svg.gradient_bar_svg(-5).startswith("<svg")     # clamps <0
    assert "<rect" in svg.gradient_bar_svg(50)

def test_range_marker_positions_current_between_low_high():
    out = svg.range_marker_svg(10.0, 20.0, 15.0, width=100)
    assert out.startswith("<svg")
    assert "</svg>" in out

def test_range_marker_handles_degenerate_range():
    # low == high must not divide by zero
    out = svg.range_marker_svg(10.0, 10.0, 10.0)
    assert out.startswith("<svg")
```
**Step 2:** Run → FAIL. **Step 3:** Implement:
- `speedometer_svg(score, grade)` — semicircle gauge, colored zones (0–40 `#ef5350`, 40–55 `#ffa726`, 55–75 `#42a5f5`, 75–100 `#66bb6a`), needle at `score` (clamp 0–100), score+grade text. Use a 160×110 viewBox.
- `gradient_bar_svg(value, width=150, height=12)` — clamp [0,100]; fill width = value%; color via `_lerp_color` (0–50 red→amber, 50–100 amber→green). Background track + filled rect.
- `range_marker_svg(low, high, current, width=160, height=14)` — horizontal line, end caps, triangle/marker at `frac=(current-low)/(high-low)` guarded for `high==low` (frac=0.5).
- helper `_lerp_color(c1, c2, t)`.
**Step 4:** Run → PASS. **Step 5:** Commit `feat: SVG builders for trade-detail graphics`.

### Task A3: Shared Trade detail panel (`detail.py`)

**Files:**
- Create: `webgui/pages/options/detail.py`
- Test: `webgui/tests/test_options_detail.py`

**Step 1 (test):** detail builds a handle with `update(signal)`; pure helpers tested:
```python
from pages.options import detail

SIG = {"symbol":"AAPL","type":"PCS","trade_type":"0-DTE","composite_score":72,
       "grade":"A","credit":0.34,"pop_pct":73.2,"breakeven":449.7,"dte":0,
       "expiration":"2026-06-14","short_strike":450,"long_strike":445,"width":5,
       "max_loss":4.66,"rr_pct":7.3,"short_delta":-0.25,"net_theta":0.04,
       "net_vega":-0.01,"short_iv":18.5,
       "factor_scores":{"rr":80,"pop":62,"theta":60,"iv":94,"iv_hv":46,"vega":75,
                        "em":19,"liq":0,"trend":75,"gex":100,"dex":83}}

def test_pop_color_thresholds():
    assert detail.pop_color(75) == detail.GREEN
    assert detail.pop_color(60) == detail.AMBER
    assert detail.pop_color(40) == detail.RED

def test_factor_rows_returns_11_for_non_ic():
    rows = detail.factor_rows(SIG["factor_scores"], "PCS")
    labels = [r[0] for r in rows]
    assert "R:R" in labels and "DEX" in labels and len(rows) == 11

def test_factor_rows_ic_variant():
    rows = detail.factor_rows({"pcs_leg":70,"ccs_leg":65,"delta_bonus":-2}, "IC")
    labels = [r[0] for r in rows]
    assert "Put leg" in labels and "Call leg" in labels
```
**Step 2:** FAIL. **Step 3:** Implement constants `GREEN/AMBER/RED`, `pop_color`, `factor_rows(factor_scores, trade_type)` (returns `[(label, value), ...]`; IC → pcs_leg/ccs_leg/delta_bonus, else the 11 factors with display labels), and a `render()` returning an object with `.update(signal)` and `.clear()` that (re)builds the panel: placeholder when no signal, else header (title + `svg.speedometer_svg` + 2×2 tiles via `pop_color` etc.) and the 5 cards (`ui.expansion`), Card 3 using `svg.gradient_bar_svg` per `factor_rows`, Card 4/5 using `svg.range_marker_svg`. Robust to missing keys.
**Step 4:** PASS. **Step 5:** Commit `feat: shared Trade detail panel with SVG graphics`.

### Task A4: Scanner two-pane wiring + row select → detail

**Files:**
- Modify: `webgui/pages/options/scanner.py`
- Test: `webgui/tests/test_options_page.py` (add `test_signal_rows_keep_id_for_detail` if not present)

**Steps:**
1. Add/confirm test that rows carry `id` and that a `synth_signal(row, by_id)` lookup returns the raw engine signal. Run → FAIL if new.
2. Rewrite `render()`: two-column layout (`ui.row`): left = header strip (`header.render()` from A6 — until then a placeholder) + Run button/spinner/status + tabs (0-4 DTE / 5-15 DTE tables); right = `detail.render()` panel. Wire `table.on('rowClick')` (and `rowSelect`) → `detail_panel.update(by_id[row['id']])`.
3. Run suite → PASS. 4. Commit `feat: scanner two-pane with shared detail panel`.

### Task A5: Gamma + Simulator stub pages

**Files:**
- Create: `webgui/pages/options/gamma.py`, `webgui/pages/options/simulator.py`
- Test: `webgui/tests/test_options_stubs.py`

**Step 1 (test):**
```python
from pages.options import gamma, simulator
def test_stub_pages_have_render():
    assert callable(gamma.render) and callable(simulator.render)
```
**Step 2:** FAIL. **Step 3:** each `render()` shows a heading + "coming soon" note referencing the engine to port. **Step 4:** PASS. **Step 5:** Commit `feat: gamma/simulator stub pages`.

### Task A6: Compact header strip (`header.py`)

**Files:**
- Create: `webgui/pages/options/header.py`
- Test: `webgui/tests/test_options_header.py`

**Step 1 (test):** pure helpers:
```python
from pages.options import header
def test_sentiment_dot_mapping():
    assert header.sentiment_dot({"active":False})[1] == "No data"
    assert header.sentiment_dot({"active":True,"allow_ccs":False,"allow_pcs":True})[1] == "Bullish"
    assert header.sentiment_dot({"active":True,"allow_ccs":True,"allow_pcs":False})[1] == "Bearish"
    assert header.sentiment_dot({"active":True,"allow_ccs":True,"allow_pcs":True})[1] == "Neutral"

def test_quote_prices_extracts_last():
    raw = {"SPY":{"quote":{"lastPrice":742.36}}}
    assert header.quote_last(raw, "SPY") == 742.36
```
**Step 2:** FAIL. **Step 3:** Implement `sentiment_dot(regime)->(color,label)`, `quote_last(raw,sym)`, and `render()` that builds the strip (SPX/SPY/QQQ/VIX labels + regime badge + sentiment dot) and a `refresh()` using `proxy.schwab_py_client.get_quotes`, `scanner_engine.vix_regime`, `regime_filter.evaluate_regime`; wire `ui.timer(30, refresh)`. Engine `sys.path` glue like scanner. **Step 4:** PASS. **Step 5:** Commit `feat: compact options header strip`. Then wire `header.render()` into `scanner.render()` (replace A4 placeholder); run suite; commit.

### Task A7: Expandable nav group in shell

**Files:**
- Modify: `webgui/main.py` (NAV structure + `_layout` group rendering + new routes)
- Modify: `webgui/tests/test_shell.py`

**Step 1 (test):** assert new routes registered:
```python
for path in ("/", "/options/paper","/options/captured","/options/portfolio",
             "/options/calculator","/options/swing","/options/gamma",
             "/options/simulator","/sentiment","/trade","/portfolio","/driver"):
    assert path in routes
```
**Step 2:** FAIL. **Step 3:** Restructure `NAV` into groups: an Options group with children (Scanner `/`, Paper Trades, Captured, Paper Portfolio, Calculator, Swing, Gamma, Simulator) + flat items (Sentiment/Trade/Portfolio/Driver). `_layout` renders the Options group as `ui.expansion` (expanded when active path starts `/options` or is `/`), children as links with active highlight; flat items as before. Register `@ui.page` routes for each options child calling the matching `render()` (paper/captured/portfolio/calculator/swing exist as stubs until their batch — create minimal `render()` placeholders now in their modules so routes resolve). **Step 4:** PASS. **Step 5:** Commit `feat: expandable Options nav group + child routes`.

**Batch A verification:** restart preview, screenshot: expandable nav, scanner two-pane, run a scan, click a row → detail panel populates with speedometer + factor bars; header strip shows quotes/VIX/sentiment. Report + checkpoint.

---

## Batch B — Calculator + Captured Signals

### Task B1: Calculator pure transforms (banding/grid)

**Files:** Create `webgui/pages/options/calculator.py` (transforms); Test `webgui/tests/test_options_calculator.py`.

**Step 1 (test):**
```python
from pages.options import calculator as calc
def test_pnl_cell_class_profit_loss_neutral():
    assert calc.pnl_cell_class(0, 100, -100) == "neutral"
    assert calc.pnl_cell_class(100, 100, -100) == "p5"
    assert calc.pnl_cell_class(10, 100, -100) == "p1"
    assert calc.pnl_cell_class(-100, 100, -100) == "l5"
def test_grid_rows_shapes_price_and_pairs():
    data=[{"price":450.0,"pnl":[10,-5],"pnl_pct":[2.0,-1.0]}]
    rows=calc.grid_rows(data)
    assert rows[0]["price"]==450.0 and len(rows[0]["cells"])==2
```
**Step 2:** FAIL. **Step 3:** Implement `pnl_cell_class(value,g_max,g_min)` (5-band p1..p5 / l1..l5 / neutral by |frac|), `grid_rows(pnl_data)`, `eval_date_labels(dates)`, `cell_text($/ %)`. **Step 4:** PASS. **Step 5:** Commit.

### Task B2: Calculator render (tiles + heatmap grid)

**Files:** Modify `calculator.py`.
- `render()`: inputs form; on Calculate → call `options_calculator.calc_summary`, `generate_eval_dates`, `generate_price_range`, `calc_spread_pnl` (off-thread if slow); render summary tiles (colored by sign/type) and the colored grid (fixed header, scroll, current-price highlight via `grid_rows` + `pnl_cell_class`, CSS classes for the 10 shades). Optional fetch price/IV buttons (client). Verify engine signatures first. Smoke-screenshot. Commit.

### Task B3: Captured Signals

**Files:** Create `webgui/pages/options/captured.py`; Test `webgui/tests/test_options_captured.py`.
- Pure transforms TDD: `captured_columns()`, `captured_rows(signals)` (sorted, robust), `synth_from_captured(row)` (→ detail-panel signal dict). Tests with a sample signal+mark dict.
- `render()`: two-pane (table left + shared `detail.render()` right). Read `signal_db.get_open_signals_with_latest_mark()`. Buttons: "Refresh marks" (off-thread: `signal_repricer.reprice_swing` + `signal_recommender.build_mark`/`plan_auto_closes` + `signal_db.close_signal_manually`), "Close selected" (manual exit dialog). Row select → `detail.update(synth_from_captured(row))`. Verify signatures. Smoke + commit.

**Batch B checkpoint.**

---

## Batch C — Paper Trades + Paper Portfolio

### Task C1: Paper Trades

**Files:** Create `webgui/pages/options/paper.py`; Test `webgui/tests/test_options_paper.py`.
- Pure transforms TDD: `paper_columns()`, `paper_rows(trades)`, `synth_from_trade(trade)`.
- `render()`: table (+ shared detail panel). Read `paper_trader.get_all_trades()`. Buttons: close (debit dialog → `close_paper_trade`+`update_trade`), delete (`delete_trade`), delete-all-closed (`delete_closed_trades`), per-row analyze (`analyze_trade`, off-thread). Verify signatures. Smoke + commit.

### Task C2: Paper Portfolio

**Files:** Create `webgui/pages/options/portfolio.py`; Test `webgui/tests/test_options_paper_portfolio.py`.
- Pure transforms TDD: `position_columns()`, `position_rows(positions)`, `order_columns()`, `order_rows(orders)`, `account_cards(snapshot)`.
- `render()`: account cards (`paper_engine.account_snapshot()`), positions table (`paper_account_db.fetch_open_positions()`), orders log (`fetch_orders(limit=100,status="FILLED")`). Buttons: reset (`reset_account`), run entry/manage cycle (off-thread, client). Verify signatures. Smoke + commit.

**Batch C checkpoint.**

---

## Batch D — Swing Scanner

### Task D1: Swing Scanner

**Files:** Create `webgui/pages/options/swing.py`; Test `webgui/tests/test_options_swing.py`.
- Reuse `scanner.signal_columns`/`signal_rows` for results; pure transform tests for any swing-specific mapping.
- `render()`: inputs form (symbol, DTE min/max, put/call delta ranges, min credit %); on Scan (off-thread): `fetch_option_chain` + `run_iv_analysis` + `screen_spreads` + `build_iron_condors` + `fetch_price_history`/`calc_technicals` + `score_all_signals`; results table + shared detail panel; optional fetch quote (client). Verify signatures. Smoke + commit.

**Batch D checkpoint.**

---

## Finalize

- Run `cd webgui && ..\.venv\Scripts\python -m pytest -q` → all green.
- Update root `CLAUDE.md` (Options section page list, routes, new subpackage) and the design's "Folder map".
- superpowers:requesting-code-review before declaring the Options section done.
- Then resume the original webgui plan: Sentiment / Trade / Portfolio / Driver pages.

## Notes
- DRY: detail panel + header strip + SVG are shared; reuse `scanner.signal_rows` where applicable.
- YAGNI: no auto-polling timers (manual buttons); Gamma/Simulator stubbed.
- Verify each engine function's real signature in the copied module before wiring (the explorations read source == copy, but confirm).
- Secrets/DBs: `git status` clean of `data/`, tokens, `*.db` each commit.
