# Sentiment — layout & restyle — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Re-layout `/sentiment` (component table right of the gauge; trend regime moved up; smaller traffic-light tiles; softer history grid; full-width sector table with gridlines+hover; bottom status bar) and two data tweaks (industries show P/C + RRG; component table drops Contrib + shows Score to 2 decimals).

**Architecture:** Presentation changes in `render()` + a small `ui.add_css` block; new pure `traffic_color`; tweaks to `build_history_figure`, `industry_rows`, `component_table_rows`; `_load_industries` extended to fetch P/C + RRG. No composite/sector data-pipeline changes.

**Tech Stack:** NiceGUI (`ui.row`/`ui.column` layout, `ui.add_css`, `ui.timer`), pytest. Reuses `pcr_from_chain`, `scoring_rotation.compute_rrg_quadrants`.

**Design:** [`2026-06-14-sentiment-layout-restyle-design.md`](2026-06-14-sentiment-layout-restyle-design.md)

**Run tests:** from `D:\WebGUI Trading with Schwab\webgui` (PowerShell): `..\.venv\Scripts\python -m pytest -q`. venv: `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`.

**Read first:** `webgui/pages/sentiment.py` — current `render()` builds: header row, `gauge_box`, `bias_lbl`, `sub_lbl`, tiles row (`TILE_DEFS`, `tile_lbls`), `comp_box` (rendered by `_render_components`), history label + `hist_plot` + `roll_lbl`/`vel_lbl`/`flag_lbl`/`div_lbl`, "Market Trend Regime" label + `regime_badge`/`regime_desc`/`regime_detail`, then "Sector & Industry Performance" (`SEC_COLS`, controls row, `summary_lbl`, `rotation_lbl`, `sector_box`). `_render_sector_table()` builds the sector/industry rows. `_apply()` populates composite+tiles+regime; `_apply_sectors()` populates summary/banner/table.

---

## Task 1: Pure-transform changes + tests

**Files:** Modify `webgui/pages/sentiment.py`; modify `webgui/tests/test_sentiment_sectors.py` (+ `webgui/tests/test_sentiment.py` for the history-grid assertion, optional).

### 1a. New `traffic_color(total)`

**Test (append to test_sentiment_sectors.py):**
```python
def test_traffic_color_bands():
    assert S.traffic_color(7.0) == S.CLR_GREEN
    assert S.traffic_color(6.5) == S.CLR_GREEN
    assert S.traffic_color(3.0) == S.CLR_RED
    assert S.traffic_color(4.5) == S.CLR_RED
    assert S.traffic_color(5.5) == S.CLR_YELLOW
    assert S.traffic_color("bad") == S.CLR_YELLOW
```

**Impl (pure-transforms section):**
```python
def traffic_color(total):
    """Composite traffic-light band for tile backgrounds.
    >=6.5 green, <=4.5 red, else amber. Mirrors source _update_metric_card_colors."""
    v = _safe_float(total, 5.0)
    if v >= 6.5:
        return CLR_GREEN
    if v <= 4.5:
        return CLR_RED
    return CLR_YELLOW
```

### 1b. `component_table_rows` — float score (drop int truncation)
Change `"score": int(s),` → `"score": s,`. Leave `contrib` in the dict (still computed; just won't be displayed). The existing `test_component_table_rows_contrib` asserts `vix["score"] == 4` which still holds (`4.0 == 4`). Add one assertion to that test confirming the float is preserved for a decimal score:
```python
    # score is the raw float (rendered to 2 decimals in the UI), not truncated
    rows2 = S.component_table_rows(_full_snap(6.81, sector_perf=7.6), sector_value="+0.70%")
    assert next(r for r in rows2 if r["name"] == "Sector Perf")["score"] == 7.6
```

### 1c. `industry_rows` — accept P/C + RRG
Change signature to `industry_rows(sector_data, sector_name, ind_quotes, ind_trends, ind_pcr=None, ind_quadrants=None)`. Replace the hardcoded `"pcr": None, "rrg": None` with:
```python
            "pcr": (ind_pcr or {}).get(etf),
            "rrg": (ind_quadrants or {}).get(etf),
```
Update tests:
```python
def test_industry_rows_with_pcr_rrg():
    quotes = {"SMH": {"change_pct": 2.5}}
    trends = {"SMH": {"week_pct": 4.0, "month_pct": 9.0}}
    pcr = {"SMH": 0.92}
    quads = {"SMH": "Leading"}
    rows = S.industry_rows(_sector_data(), "Information Technology", quotes, trends, pcr, quads)
    assert rows[0]["pcr"] == 0.92 and rows[0]["rrg"] == "Leading"


def test_industry_rows_blank_when_no_pcr_rrg():
    rows = S.industry_rows(_sector_data(), "Information Technology", {}, {})
    assert rows[0]["pcr"] is None and rows[0]["rrg"] is None
```
(Keep the existing `test_industry_rows_built`/`test_industry_rows_missing_data_blank`; the latter still passes since pcr/quadrants default None → None.)

### 1d. `build_history_figure` — softer grid
In the returned `layout`, add subtle grid styling to the axes. Replace the `xaxis`/`yaxis`/template block so the layout includes:
```python
        "layout": {
            "margin": {"l": 36, "r": 12, "t": 8, "b": 28},
            "height": 220,
            "template": "plotly_dark",
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "xaxis": {"gridcolor": "rgba(255,255,255,0.06)", "zeroline": False,
                      "linecolor": "rgba(255,255,255,0.15)", "nticks": 6},
            "yaxis": {"range": [0, 10], "title": "Composite",
                      "gridcolor": "rgba(255,255,255,0.06)", "zeroline": False,
                      "linecolor": "rgba(255,255,255,0.15)"},
        },
```
The existing `test_build_history_figure_shape` only checks `data[0].type`/`y` — keep it green. (Optional: add `assert fig["layout"]["yaxis"]["gridcolor"].startswith("rgba")`.)

**Steps:** write/adjust the failing tests → run (fail) → implement → run (pass) → full suite.

**Commit:**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment_sectors.py
git commit -m "feat(sentiment): traffic_color, float scores, industry P/C+RRG params, softer history grid"
```

---

## Task 2: `_load_industries` fetches P/C + RRG

**Files:** Modify `webgui/pages/sentiment.py`.

Change `_load_industries(etfs)` → `_load_industries(etfs, spy_closes)`. After building `quotes`/`trends`, also collect per-ETF closes, fetch `/chains` for P/C, and compute RRG quadrants:

```python
def _load_industries(etfs, spy_closes):
    """Off-thread: quotes + week/month trends + P/C + RRG for industry ETFs."""
    import proxy
    from datetime import date, timedelta
    try:
        quotes = proxy.schwab_client.get_quotes(list(etfs)) or {}
    except Exception:
        quotes = {}
    trends, closes = {}, {}
    for etf in etfs:
        try:
            df = proxy.schwab_client.get_daily_history(etf, months=3)
        except Exception:
            df = None
        if df is None:
            continue
        cl = [float(c) for c in df["close"].tolist()]
        closes[etf] = cl
        d3, wk, mo = week_month_from_closes(cl)
        trends[etf] = {"day3_pct": d3, "week_pct": wk, "month_pct": mo}
    pcr = {}
    today_iso = date.today().isoformat()
    to_iso = (date.today() + timedelta(days=30)).isoformat()
    for etf in etfs:
        try:
            chain = proxy.schwab_client._request("/chains", params={
                "symbol": etf, "contractType": "ALL", "range": "NTM",
                "strikeCount": 50, "fromDate": today_iso, "toDate": to_iso})
        except Exception:
            chain = None
        v = pcr_from_chain(chain)
        if v is not None:
            pcr[etf] = v
    quads = scoring_rotation.compute_rrg_quadrants(closes, spy_closes or [],
                                                   rs_window=50, mom_window=20)
    return {"quotes": quotes, "trends": trends, "pcr": pcr, "quadrants": quads}
```

In `_ensure_industry`, pass SPY: `await ng_run.io_bound(_load_industries, etfs, state["spy"])`. The empty-etfs fallback becomes `{"quotes": {}, "trends": {}, "pcr": {}, "quadrants": {}}`.

In `_render_sector_table`, change the industry render to pass pcr/quadrants and fill the last two cells:
- call `industry_rows(sd, sector_name, ind["quotes"], ind["trends"], ind.get("pcr"), ind.get("quadrants"))`
- replace the two trailing blank cells with P/C and RRG cells styled like the sector rows:
  ```python
                                pv = ir["pcr"]
                                ui.label(f"{pv:.2f}" if pv is not None else "").style(
                                    f"width:56px;color:{pcr_color(pv)}")
                                rv = ir["rrg"]
                                ui.label(str(rv or "")).style(f"width:90px;color:{rrg_color(rv)}")
  ```

**Verify:** full suite green + `import main` ok (no dev server). **Commit:**
```bash
git add webgui/pages/sentiment.py
git commit -m "feat(sentiment): industries fetch P/C + RRG on expand"
```

---

## Task 3: `render()` re-layout + CSS + tiles + status bar + component display

**Files:** Modify `webgui/pages/sentiment.py`.

### 3a. Scoped CSS (call `ui.add_css(...)` once at the top of `render()`)
```python
    ui.add_css('''
    .sent-sectors .secrow { border-bottom: 1px solid rgba(255,255,255,0.05); }
    .sent-sectors .secrow:hover { background: rgba(255,255,255,0.04); }
    .sent-sectors .secrow > div { border-right: 1px solid rgba(255,255,255,0.04); }
    .sent-sectors .secrow > div:last-child { border-right: none; }
    .sent-sectors .indrow { background: rgba(255,255,255,0.02); }
    ''')
```

### 3b. Top two-column region (gauge+regime left, table right)
Replace the current sequence (gauge_box, bias_lbl, sub_lbl … and later the standalone Market Trend Regime block) with:
```python
    with ui.row().classes("w-full no-wrap items-start gap-6"):
        with ui.column().classes("items-start").style("min-width:280px"):
            gauge_box = ui.html("").classes("q-mt-sm")
            bias_lbl = ui.label("").classes("text-h6")
            sub_lbl = ui.label("").classes("opacity-80")
            ui.separator().classes("q-my-sm")
            ui.label("Market Trend").classes("opacity-60 text-xs")
            regime_badge = ui.badge("").classes("text-subtitle2 q-pa-sm")
            regime_desc = ui.label("").classes("opacity-80 text-sm")
            regime_detail = ui.label("").classes("opacity-60 text-xs")
        comp_box = ui.column().classes("q-gutter-xs").style("flex:1")
```
Remove the old standalone "Market Trend Regime" label/separator/badge/desc/detail block lower down (those widgets now live in the left column). `_apply()` keeps setting the same `regime_badge`/`regime_desc`/`regime_detail` refs — no logic change.

### 3c. Tiles: ~25% smaller + traffic-light background
In the tiles loop, shrink and store the card refs so `_apply` can recolor:
```python
    tile_lbls, tile_cards = {}, {}
    with ui.row().classes("w-full no-wrap gap-2 q-mt-sm"):
        for tkey, tlabel in TILE_DEFS:
            c = ui.card().classes("q-pa-xs items-center").style("min-width:72px;flex:1")
            with c:
                ui.label(tlabel).classes("text-xs").style("color:#111")
                tile_lbls[tkey] = ui.label("—").classes("text-bold").style("color:#111")
            tile_cards[tkey] = c
```
In `_apply()`, after computing `total`, set the band color on every tile card:
```python
        band = traffic_color(total)
        for tkey, _tlabel in TILE_DEFS:
            tile_lbls[tkey].text = t[tkey]
            tile_cards[tkey].style(f"background-color:{band}")
```
(The title+value labels already use dark text via the style above.)

### 3d. Component table: drop Contrib column, Score 2 decimals
In `_render_components`: remove the "Contrib" header label and the trailing contrib cell; change the score cell to `ui.label(f"{sc:.2f}")` (sc = `r["score"]`, now a float). Keep the score color logic (`sc >= 7` green / `< 4` red / else yellow).

### 3e. Sector table: full width + CSS classes
Wrap `sector_box` content in the `.sent-sectors` class: add `sector_box = ui.column().classes("w-full q-gutter-none sent-sectors")` (or add the class to the existing `sector_box`). In `_render_sector_table`, add `.classes("secrow")` to each sector `ui.row` and `.classes("secrow indrow")` to each industry `ui.row`, and make the Description cell flex instead of fixed: change its `.style("width:200px;…")` to `.style("flex:1;min-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")`. Update the header row's Description cell to match (`flex:1;min-width:160px`). Keep numeric columns fixed-width.

### 3f. Bottom status bar
Add as the LAST element in `render()` (after the sector section):
```python
    ui.separator().classes("q-my-sm")
    status_lbl = ui.label("Loading…").classes("opacity-60 text-xs w-full")
```
Add a helper + timers. Use Python `datetime` (normal app code — fine here):
```python
    from datetime import datetime, timedelta
    def _render_status():
        parts = []
        ca = state.get("composite_at")
        if ca:
            parts.append(f"Updated {ca.strftime('%H:%M:%S')}")
            parts.append(f"Next ~{(ca + timedelta(seconds=300)).strftime('%H:%M')}")
        sa = state.get("sector_at")
        if sa:
            parts.append(f"Sectors {sa.strftime('%H:%M:%S')}")
        try:
            up = proxy.health().get("up")
        except Exception:
            up = None
        parts.append(f"Proxy: {'connected' if up else 'down'}")
        status_lbl.text = "   ·   ".join(parts) if parts else "Loading…"
```
`import proxy` at the top of render() (module is already importable; or reference via `from … import proxy`-style already used in loaders — simplest: `import proxy` inside `_render_status`). In `load()` set `state["composite_at"] = datetime.now()` (and mirror `_CACHE["composite_at"]`) right after `_apply()`; in `load_sectors()` set `state["sector_at"]`/`_CACHE`. Seed `state["composite_at"]/["sector_at"]` from `_CACHE` in the `state` init. Call `_render_status()` at end of `_apply`, end of `_apply_sectors`, and on `ui.timer(15.0, _render_status)` so "Next/Proxy" stay fresh. Add `"composite_at": None, "sector_at": None` to `_CACHE`.

**Verify:** full suite green + `import main` ok. (Browser verify is Task 4.) **Commit:**
```bash
git add webgui/pages/sentiment.py
git commit -m "feat(sentiment): two-column top layout, regime up top, traffic-light tiles, softer grid, full-width sector table, status bar"
```

---

## Task 4: Verify + docs

1. **Browser:** restart `webgui` preview, navigate `/sentiment`. After load, `preview_screenshot` (and a11y `preview_snapshot`) to confirm: two-column top (gauge+regime left, component table right), colored smaller tiles, softer history grid, full-width sector table with hover/gridlines, status bar text, and (expand a sector) industry rows now showing P/C + RRG. If the proxy is slow, script-verify industry P/C+RRG via a temp script driving `_load_industries(etfs, spy_closes)` and delete it.
2. **Docs:** update root `CLAUDE.md` `/sentiment` row + dev-note to mention the layout (two-column, regime up top, status bar) + industries now show P/C+RRG + component table (no Contrib, 2-dp score). Bump test count.
3. **Commit** the docs.

---

## Gotchas
- `ui.html` strips `<style>` → use `ui.add_css` (already the pattern; see gamma Explain).
- No lazy `from scoring import` — `compute_rrg_quadrants` is the top-level `scoring_rotation` binding.
- `datetime.now()` is fine in page code (this is NOT a workflow script).
- Tile dark text (`#111`) is for contrast on the colored background — keep it on both the title and value labels.
- Industry P/C uses `/chains` per industry ETF — thin-volume industries often return no chain → `pcr` absent → blank cell (graceful, expected).
- Keep `render()` readable; persistence/expansion/data logic unchanged.
- `options-scanner` has ~2 known unrelated date-relative test failures — ignore.
