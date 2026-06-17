# RRG Declutter — Sampled Tails, Hover-Isolate, Full-Width Layout — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Sector Rotation RRG readable: shorten tails to 12 every-other-day points, draw each sector as one line + a single head dot, hover a sector to highlight it and dim the rest, and give the RRG the full panel width below a top row holding the quadrant map + ROTATING FROM/INTO.

**Architecture:** The tail data change is in the engine (`sector_rotation_assessment.py`). The figure becomes one trace per sector so `curveNumber == sector index`; hover-isolate uses NiceGUI `plotly_hover`/`plotly_unhover` + `run_plot_method('restyle')` (client-side, no rebuild). The layout move and event wiring are in the page's `render()`.

**Tech Stack:** pandas (engine), NiceGUI 3.13 + Plotly (page), pytest.

**Design doc:** `docs/plans/2026-06-16-rrg-declutter-hover-design.md`

**Test commands:**
- Engine: `cd sentiment-dashboard ; ..\.venv\Scripts\python -m pytest tests/test_sector_rotation_tool.py -q`
- Page: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py -q`

---

## Task 1: Engine — 12-point every-other-day tail

**Files:**
- Modify: `sentiment-dashboard/sector_rotation_assessment.py` (`TAIL_LENGTH`; add `TAIL_STRIDE`; `assess_sector` tail build at ~line 268)
- Test: `sentiment-dashboard/tests/test_sector_rotation_tool.py`

**Step 1: Replace the two existing tail tests**

In `sentiment-dashboard/tests/test_sector_rotation_tool.py`, replace
`test_assess_from_close_series_includes_tail` and `test_tail_length_constant_is_30`
(lines ~66-83) with:

```python
def test_assess_from_close_series_includes_sampled_tail():
    sectors, bench = _synthetic_closes()
    a = rt.assess_from_close_series(sectors, bench, "2026-06-08")
    assert a is not None
    for s in a["sectors"]:
        tail = s.get("tail")
        assert isinstance(tail, list) and tail, f"{s['etf']} missing tail"
        # At most TAIL_LENGTH plotted points.
        assert len(tail) <= rt.TAIL_LENGTH
        # Same keys as the head reading; newest point == current head.
        assert set(tail[0]) == {"rs_ratio", "rs_momentum"}
        assert tail[-1]["rs_ratio"] == s["rs_ratio"]
        assert tail[-1]["rs_momentum"] == s["rs_momentum"]


def test_tail_constants():
    assert rt.TAIL_LENGTH == 12
    assert rt.TAIL_STRIDE == 2


def test_tail_samples_every_other_point():
    # Build assessment and confirm consecutive tail points are TAIL_STRIDE apart in
    # the underlying RS series (i.e. every-other-day sampling), by checking the tail
    # is a strict subsequence whose spacing matches the stride against assess_sector's
    # full series.
    sectors, bench = _synthetic_closes(n=200)
    import pandas as pd
    bench_s = pd.Series([float(c) for c in bench[-200:]])
    etf = next(iter(rt.SECTOR_ETFS))
    sec_s = pd.Series([float(c) for c in sectors[etf][-200:]])
    rs_ratio = rt.compute_rs_ratio(sec_s, bench_s)
    rs_mom = rt.compute_rs_momentum(rs_ratio)
    paired = pd.DataFrame({"ratio": rs_ratio, "mom": rs_mom}).dropna().reset_index(drop=True)
    expected = paired.iloc[::-1].iloc[::rt.TAIL_STRIDE].iloc[:rt.TAIL_LENGTH].iloc[::-1]
    a = rt.assess_from_close_series(sectors, bench, "2026-06-08")
    got = next(s for s in a["sectors"] if s["etf"] == etf)["tail"]
    assert len(got) == len(expected)
    assert got[-1]["rs_ratio"] == round(float(expected["ratio"].iloc[-1]), 2)
    # spacing: the gap between the last two plotted ratios matches the stride-2 source.
    assert got[-2]["rs_ratio"] == round(float(expected["ratio"].iloc[-2]), 2)
```

**Step 2: Run to verify it fails**

Run: `cd sentiment-dashboard ; ..\.venv\Scripts\python -m pytest tests/test_sector_rotation_tool.py -k "tail" -q`
Expected: FAIL — `TAIL_LENGTH == 12` assertion fails (currently 30); `TAIL_STRIDE` missing.

**Step 3: Implement**

In `sentiment-dashboard/sector_rotation_assessment.py`:

Change the constant (line ~75) and add the stride:

```python
# Meteor-tail: how many trailing daily RRG points to PLOT per sector and the
# day stride between them (every other day → spans ~24 trading days with 12 pts).
TAIL_LENGTH = 12
TAIL_STRIDE = 2
```

Replace the tail build in `assess_sector` (lines ~268-271):

```python
    sampled = paired.iloc[::-1].iloc[::TAIL_STRIDE].iloc[:TAIL_LENGTH].iloc[::-1]
    tail = [
        {"rs_ratio": round(float(r), 2), "rs_momentum": round(float(m), 2)}
        for r, m in zip(sampled["ratio"], sampled["mom"])
    ]
```

(Leave the `"tail": tail` in the returned dict unchanged.)

**Step 4: Run to verify it passes**

Run: `cd sentiment-dashboard ; ..\.venv\Scripts\python -m pytest tests/test_sector_rotation_tool.py -q`
Expected: PASS (all rotation-tool tests).

**Step 5: Commit**

```bash
git add sentiment-dashboard/sector_rotation_assessment.py sentiment-dashboard/tests/test_sector_rotation_tool.py
git commit -m "feat(rotation): 12-point every-other-day RRG tail (was 30 daily)"
```

---

## Task 2: Page — one trace per sector (line + head dot) + `_focus_opacities`

**Files:**
- Modify: `webgui/pages/sentiment_rotation.py` (replace `_tail_trace`/`rrg_scatter_figure`; add `_sector_trace`, `_focus_opacities`)
- Test: `webgui/tests/test_sentiment_rotation.py`

**Step 1: Rewrite the figure tests**

In `webgui/tests/test_sentiment_rotation.py`, replace the block from `_head_trace`
(line ~72) through `test_rrg_scatter_handles_missing_tail` (ends ~122) with:

```python
def _sector_traces(fig):
    return [t for t in fig["data"] if t.get("mode") == "lines+markers+text"]


def test_rrg_scatter_one_trace_per_sector():
    fig = R.rrg_scatter_figure(_assessment())
    traces = _sector_traces(fig)
    assert len(traces) == 2                       # one trace per sector
    # curveNumber/order maps to the sectors order.
    assert traces[0]["x"][-1] == 101.5 and traces[1]["x"][-1] == 98.0
    # crosshair reference lines at 100/100 present as shapes
    assert any(s.get("type") == "line" for s in fig["layout"].get("shapes", []))
    # hovermode closest so the line/head is hoverable
    assert fig["layout"].get("hovermode") == "closest"


def test_rrg_scatter_line_plus_single_head_dot():
    fig = R.rrg_scatter_figure(_assessment())
    xlk = next(t for t in _sector_traces(fig) if t["x"][-1] == 101.5)
    # Trail follows the path, oldest -> newest, ending at the head.
    assert xlk["x"] == [100.2, 100.9, 101.5]
    assert xlk["y"] == [99.5, 100.8, 102.0]
    # Only the LAST marker (head) is visible; trail markers are invisible.
    op = xlk["marker"]["opacity"]
    assert op[-1] == 1.0 and all(o == 0.0 for o in op[:-1])
    sz = xlk["marker"]["size"]
    assert sz[-1] >= 10 and all(s == 0.0 for s in sz[:-1])
    # Label only on the head; quadrant color; faint rgba line.
    assert xlk["text"][-1] == "XLK" and all(t == "" for t in xlk["text"][:-1])
    assert xlk["marker"]["color"] == R.CLR_GREEN
    assert xlk["line"]["color"].startswith("rgba(")
    assert xlk.get("showlegend") is False


def test_rrg_scatter_no_legend_leak():
    fig = R.rrg_scatter_figure(_assessment())
    assert all(t.get("showlegend") is False for t in fig["data"])


def test_rrg_scatter_handles_missing_tail():
    a = _assessment()
    for s in a["sectors"]:
        s.pop("tail", None)
    fig = R.rrg_scatter_figure(a)
    traces = _sector_traces(fig)
    assert len(traces) == 2                       # single-point head trace each
    xlk = next(t for t in traces if t["x"] == [101.5])
    assert xlk["y"] == [102.0]
    assert xlk["marker"]["opacity"][-1] == 1.0


def test_focus_opacities():
    assert R._focus_opacities(3, 1) == [0.12, 1.0, 0.12]
    assert R._focus_opacities(3, 0, dim=0.2) == [1.0, 0.2, 0.2]
    # out-of-range / None -> all visible (restore on unhover)
    assert R._focus_opacities(3, None) == [1.0, 1.0, 1.0]
    assert R._focus_opacities(3, 9) == [1.0, 1.0, 1.0]
```

(Keep `test_hex_to_rgba_helper` as-is below.)

**Step 2: Run to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py -q`
Expected: FAIL — `lines+markers+text` traces / `_focus_opacities` not present yet.

**Step 3: Implement**

In `webgui/pages/sentiment_rotation.py`, replace `_tail_trace` and `rrg_scatter_figure`
(the block from `def _tail_trace` through the end of `rrg_scatter_figure`) with:

```python
def _sector_trace(sec):
    """One RRG trace for a sector: a faded trail line + a single bright head dot
    (the current point), labeled with the ETF. Built from the sampled tail; falls
    back to a single head point when the sector has no tail."""
    tail = sec.get("tail") or []
    color = quadrant_color(sec.get("quadrant"))
    if tail:
        xs = [p["rs_ratio"] for p in tail]
        ys = [p["rs_momentum"] for p in tail]
    else:
        r, m = sec.get("rs_ratio"), sec.get("rs_momentum")
        if r is None or m is None:
            return None
        xs, ys = [r], [m]
    n = len(xs)
    return {
        "type": "scatter", "mode": "lines+markers+text",
        "x": xs, "y": ys,
        "line": {"color": _hex_to_rgba(color, 0.4), "width": 1.6, "shape": "spline"},
        "marker": {"color": color,
                   "size":    [0.0] * (n - 1) + [13],
                   "opacity": [0.0] * (n - 1) + [1.0]},
        "text": [""] * (n - 1) + [sec.get("etf") or ""],
        "textposition": "top center", "textfont": {"size": 10},
        "hovertemplate": (f"{sec.get('name')} ({sec.get('etf')}) — "
                          f"{sec.get('quadrant')}<br>RS-Ratio %{{x:.2f}} · "
                          f"RS-Mom %{{y:.2f}}<extra></extra>"),
        "showlegend": False,
    }


def _focus_opacities(n, focus, dim=0.12):
    """Trace-opacity list: 1.0 for the ``focus`` trace, ``dim`` for the rest.
    Returns all-1.0 when ``focus`` is None / out of range (restore on unhover)."""
    if focus is None or not (0 <= focus < n):
        return [1.0] * n
    return [1.0 if i == focus else dim for i in range(n)]


def rrg_scatter_figure(a):
    """Plotly RRG scatter: one trace per sector (faded trail line + a single head
    dot), 100/100 crosshair lines, hovermode closest so each sector is hoverable.
    Trace order matches the sectors order so ``curveNumber == sector index``."""
    secs = a.get("sectors") or []
    traces = [t for t in (_sector_trace(s) for s in secs) if t is not None]
    line = {"color": "rgba(255,255,255,0.25)", "width": 1}
    return {
        "data": traces,
        "layout": {
            "margin": {"l": 44, "r": 12, "t": 8, "b": 36}, "height": 560,
            "template": "plotly_dark", "hovermode": "closest",
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

(The `_hex_to_rgba` helper above stays. The old `_tail_trace` is fully removed.)

**Step 4: Run to verify it passes**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/sentiment_rotation.py webgui/tests/test_sentiment_rotation.py
git commit -m "feat(rotation): RRG one-trace-per-sector (line + head dot) + focus-opacities helper"
```

---

## Task 3: Page — layout move + hover-isolate wiring

**Files:**
- Modify: `webgui/pages/sentiment_rotation.py` (`render()` static layout + `_render` plot creation)
- Test: `webgui/tests/test_sentiment_rotation.py` (structural guard); browser in Task 4

**Background — current `render()` layout (verify before editing):**
- `cols_box = ui.row(...)` (ROTATING FROM/INTO) is created right after the headline.
- A two-column row holds `table_box` (Full Quadrant Map) and `rrg_box` (RRG), each
  `flex:1`.
- `_render` clears + repopulates `cols_box`, `table_box`, `rrg_box`, and rebuilds the
  RRG via `rrg_box.clear()` + `ui.plotly(rrg_scatter_figure(a))`.

**Step 1: Add a structural guard test**

Append to `webgui/tests/test_sentiment_rotation.py`:

```python
def test_render_wires_hover_and_fullwidth_rrg():
    import inspect
    src = inspect.getsource(R.render)
    # hover-isolate wiring present
    assert "plotly_hover" in src and "plotly_unhover" in src
    assert "run_plot_method" in src and "_focus_opacities" in src
```

**Step 2: Run to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py::test_render_wires_hover_and_fullwidth_rrg -q`
Expected: FAIL (no hover wiring yet).

**Step 3: Implement**

(a) **Move the layout.** In `render()`, delete the standalone
`cols_box = ui.row(...)` line near the headline and the existing two-column
`ui.row(... ) ... table_box ... rrg_box` block. Replace them with a top row
(table + FROM/INTO) and a full-width RRG below:

```python
    # Top: Full Quadrant Map (left) + ROTATING FROM/INTO (right).
    with ui.row().classes("w-full no-wrap gap-6 items-start q-mt-md"):
        with ui.column().style("flex:1.4;min-width:0"):
            ui.label("Full Quadrant Map (sorted by RS-Momentum)").classes("text-subtitle2")
            table_box = ui.column().classes("w-full q-gutter-none")
        with ui.column().style("flex:1;min-width:0"):
            cols_box = ui.column().classes("w-full")
    ui.label("Pairing is ordinal — strongest relative-selling vs strongest "
             "relative-buying pressure, not literal cash flow.").classes("opacity-50 text-xs q-mt-sm")
    # Below: the RRG spans the full width.
    ui.label("RRG").classes("text-subtitle2 q-mt-md")
    rrg_box = ui.column().classes("w-full")
```

> Note: `cols_box` was a `ui.row` (FROM and INTO side by side); the FROM/INTO
> builder in `_render` opens its own inner row per side, so a `ui.column` parent
> stacks the two sides vertically in the narrower right pane. Keep it a
> `ui.row().classes("w-full no-wrap gap-8")` instead if you want them side by side:
> use `cols_box = ui.row().classes("no-wrap gap-8 w-full")`. **Use the `ui.row`
> form** so FROM/INTO stay side by side as today.

So the right column is:

```python
        with ui.column().style("flex:1;min-width:0"):
            cols_box = ui.row().classes("no-wrap gap-8 w-full")
```

(b) **Wire hover.** In `_render`, where the RRG is drawn, capture the plotly
element and attach handlers. Replace:

```python
        rrg_box.clear()
        with rrg_box:
            ui.plotly(rrg_scatter_figure(a)).classes("w-full")
```

with:

```python
        rrg_box.clear()
        fig = rrg_scatter_figure(a)
        n = len(fig["data"])
        with rrg_box:
            plot = ui.plotly(fig).classes("w-full")

        def _on_hover(e):
            pts = (getattr(e, "args", None) or {}).get("points") or []
            cn = pts[0].get("curveNumber") if pts else None
            plot.run_plot_method("restyle", {"opacity": _focus_opacities(n, cn)},
                                 list(range(n)))

        def _on_unhover(e):
            plot.run_plot_method("restyle", {"opacity": _focus_opacities(n, None)},
                                 list(range(n)))

        plot.on("plotly_hover", _on_hover)
        plot.on("plotly_unhover", _on_unhover)
```

**Step 4: Run the page suite**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_sentiment_rotation.py -q`
Expected: PASS (guard + all builder tests). Then the full webgui suite:
`cd webgui ; ..\.venv\Scripts\python -m pytest -q` → green.

**Step 5: Commit**

```bash
git add webgui/pages/sentiment_rotation.py webgui/tests/test_sentiment_rotation.py
git commit -m "feat(rotation): full-width RRG below quadrant map + FROM/INTO; hover-isolate sectors"
```

---

## Task 4: Browser verification

**Goal:** Confirm the four behaviors against the live stack.

**Preconditions:** Memurai (:6379), proxy (:8100), `sentiment_svc` (:8210), webgui
(:8500) running. The tail change is in the **engine** → restart `sentiment_svc` and
hit **Refresh** on the page so the cache repopulates with 12-point tails. The page
change needs the webgui restarted (reload=False) — use the Preview tool.

**Steps:**
1. Restart `sentiment_svc` (venv python), then Refresh the rotation page so
   `cache:sentiment:rotation` has the new sampled tails.
2. `preview_start` webgui; navigate to `/sentiment/rotation`.
3. **Layout:** screenshot — quadrant-map table top-left, ROTATING FROM/INTO
   top-right, RRG full-width below and taller.
4. **Tails:** each sector shows a short line (~12 pts, every other day) + one bright
   head dot with its ETF label — far less spaghetti than before.
5. **Hover:** `preview_eval` to dispatch a `plotly_hover` (or hover the XLE dot),
   confirm only that sector stays bright and the rest dim (read back trace opacities
   via the plotly div); move off → all restore. If the trail line isn't hoverable
   (only the head), bump trail-marker opacity to a near-zero nonzero (e.g. 0.001) in
   `_sector_trace` and re-verify.
6. **X-axis size:** confirm the RRG now spans the full panel width (≈2× before).
7. `preview_console_logs` — no JS/Plotly errors.

> If services aren't running, note verification was deferred; the unit tests
> (Tasks 1–3) prove the pure logic, and hover wiring is covered by the guard test.

---

## Final: full suites + docs

```bash
cd sentiment-dashboard ; ..\.venv\Scripts\python -m pytest tests -q
cd webgui ; ..\.venv\Scripts\python -m pytest -q
```

Expected: both green (sentiment-dashboard keeps its ~2 pre-existing
`No module named 'sentiment_dashboard'` failures — unrelated). Then update the root
`CLAUDE.md` Sector Rotation row: 12-point every-other-day tails, line + single head
dot, **hover-isolate** a sector, full-width RRG below the quadrant map / FROM-INTO.
Commit.
