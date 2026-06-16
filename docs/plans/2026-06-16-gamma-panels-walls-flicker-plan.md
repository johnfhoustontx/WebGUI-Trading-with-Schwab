# Gamma Page — Proportional Panels, Tight GAMMA Range, No Flicker, Single Walls — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Gamma page's heatmap grow / bars shrink as the day progresses, crop GAMMA's empty strike space, eliminate chart-regeneration flicker, and reduce GAMMA/DELTA walls to one Call + one Put.

**Architecture:** Three changes live in the Tier-3 page (`webgui/pages/options/gamma.py`) — a snapshot-count flex ratio, a significant-strike y-range, and persistent Plotly elements updated in place. The walls fix lives in the options service (`services/options_svc/compute.py`) by reusing the engine's already-tested `get_directional_walls`, so the page renders service-provided walls unchanged.

**Tech Stack:** NiceGUI + Plotly (page), pandas-free pure builders, pytest. Options engine `gamma_tool` (reused, not modified).

**Design doc:** `docs/plans/2026-06-16-gamma-panels-walls-flicker-design.md`

**Test commands:**
- Page: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py -q`
- Service: `..\.venv\Scripts\python -m pytest services/options_svc/tests/test_compute.py -q` (run from repo root: `.venv\Scripts\python -m pytest services/options_svc/tests/test_compute.py -q`)

---

## Task 1: `panel_flex` — snapshot-count-driven panel ratio (page, pure)

**Files:**
- Modify: `webgui/pages/options/gamma.py` (add `panel_flex` near `bar_yrange`)
- Test: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_options_gamma.py`:

```python
def test_panel_flex_endpoints_and_monotonic():
    bar0, heat0 = gamma.panel_flex(0)
    assert heat0 == 0.28 and round(bar0 + heat0, 4) == 1.0      # session start: bars wide
    barf, heatf = gamma.panel_flex(82)
    assert heatf == 0.70 and round(barf + heatf, 4) == 1.0      # full session: heat wide
    # clamps past a full session
    assert gamma.panel_flex(200) == gamma.panel_flex(82)
    # heat fraction is non-decreasing with more snapshots
    heats = [gamma.panel_flex(n)[1] for n in range(0, 90, 10)]
    assert heats == sorted(heats)
    # midpoint is between the endpoints
    _, heat_mid = gamma.panel_flex(41)
    assert 0.28 < heat_mid < 0.70
```

**Step 2: Run to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py::test_panel_flex_endpoints_and_monotonic -q`
Expected: FAIL — `AttributeError: module 'pages.options.gamma' has no attribute 'panel_flex'`.

**Step 3: Implement**

In `webgui/pages/options/gamma.py`, add after `bar_yrange` (~line 163):

```python
def panel_flex(n_cols, full_cols=82, min_heat=0.28, max_heat=0.70):
    """(bar_weight, heat_weight) flex ratio from intraday snapshot count.

    full_cols ≈ five-minute slots in an 08:30–15:20 CT session. The heatmap
    fraction lerps min_heat→max_heat with session progress so the heatmap grows
    and the bars shrink as the day fills in; bars take the remainder."""
    p = 0.0 if full_cols <= 0 else max(0.0, min(1.0, n_cols / full_cols))
    heat = min_heat + (max_heat - min_heat) * p
    return round(1.0 - heat, 4), round(heat, 4)
```

**Step 4: Run to verify it passes**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py::test_panel_flex_endpoints_and_monotonic -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): panel_flex — snapshot-count-driven bar/heatmap width ratio"
```

---

## Task 2: `significant_strikes` — crop GAMMA dead space (page, pure)

**Files:**
- Modify: `webgui/pages/options/gamma.py` (add `significant_strikes` near `bars_from_gex`)
- Test: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing tests**

Append to `webgui/tests/test_options_gamma.py`:

```python
def test_significant_strikes_crops_near_zero_tails():
    bars = {"strikes": [440.0, 448.0, 450.0, 452.0, 460.0],
            "nets": [5.0, 600.0, -900.0, 500.0, 8.0]}   # tails ≈ 0 vs 900 peak
    assert gamma.significant_strikes(bars, frac=0.03) == [448.0, 450.0, 452.0]


def test_significant_strikes_noop_when_all_significant():
    bars = {"strikes": [448.0, 450.0, 452.0], "nets": [600.0, -900.0, 500.0]}
    assert gamma.significant_strikes(bars, frac=0.03) == [448.0, 450.0, 452.0]


def test_significant_strikes_all_zero_returns_all():
    bars = {"strikes": [448.0, 450.0], "nets": [0.0, 0.0]}
    assert gamma.significant_strikes(bars) == [448.0, 450.0]
    assert gamma.significant_strikes({"strikes": [], "nets": []}) == []
```

**Step 2: Run to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py -k significant_strikes -q`
Expected: FAIL — `AttributeError: ... 'significant_strikes'`.

**Step 3: Implement**

In `webgui/pages/options/gamma.py`, add after `bars_from_gex` (~line 140):

```python
def significant_strikes(bars, frac=0.03):
    """Strikes whose |net| ≥ frac·peak — drops near-zero edge strikes so the
    y-range crops to where the bars are actually visible (fixes GAMMA dead space).

    ``bars`` is a bars_from_gex(...) dict. Returns every strike when the peak is
    zero (nothing to crop)."""
    strikes, nets = bars.get("strikes") or [], bars.get("nets") or []
    peak = max((abs(n) for n in nets), default=0.0)
    if peak <= 0:
        return list(strikes)
    thr = peak * frac
    return [s for s, n in zip(strikes, nets) if abs(n) >= thr]
```

**Step 4: Run to verify it passes**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py -k significant_strikes -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): significant_strikes — crop near-zero edge strikes from y-range"
```

---

## Task 3: Walls → one Call + one Put (service)

**Files:**
- Modify: `services/options_svc/compute.py` (add module-level `gamma_walls`; call it from `_walls`)
- Test: `services/options_svc/tests/test_compute.py`

**Background:** `_walls` is a nested closure in `calc_gamma_snapshot`. Extract the
pick into a module-level `gamma_walls(vname, data, spot)` so it is unit-testable,
and reuse the engine's already-tested `get_directional_walls` (call wall = strike
> spot with largest `call` GEX; put wall = strike < spot with most-negative
`put` GEX). For DEX the per-strike map is keyed `"dex"`, so remap it to `"gex"`
for the picker (DEX cells also carry `call`/`put`/`net`).

**Step 1: Write the failing tests**

Append to `services/options_svc/tests/test_compute.py`:

```python
def test_gamma_walls_one_each_side_for_gex():
    data = {"spot": 450.0, "gex": {
        440.0: {"call": 10.0,  "put": -900.0, "net": -890.0},  # put wall (below)
        448.0: {"call": 50.0,  "put": -100.0, "net": -50.0},
        452.0: {"call": 700.0, "put": -20.0,  "net": 680.0},   # call wall (above)
        460.0: {"call": 120.0, "put": -5.0,   "net": 115.0},
    }}
    walls = compute.gamma_walls("GEX", data, 450.0)
    assert walls == [440.0, 452.0]            # [put_wall (<spot), call_wall (>=spot)]


def test_gamma_walls_dex_uses_dex_key():
    data = {"spot": 100.0, "dex": {
        95.0:  {"call": 1.0,   "put": -500.0, "net": -499.0},  # put wall
        105.0: {"call": 800.0, "put": -1.0,   "net": 799.0},   # call wall
    }}
    assert compute.gamma_walls("DEX", data, 100.0) == [95.0, 105.0]


def test_gamma_walls_single_side_and_empty():
    # Only strikes above spot -> just the call wall.
    above_only = {"spot": 450.0, "gex": {452.0: {"call": 9.0, "put": -1.0, "net": 8.0}}}
    assert compute.gamma_walls("GEX", above_only, 450.0) == [452.0]
    # Charm/Vanna never get walls; empty data -> [].
    assert compute.gamma_walls("Charm", above_only, 450.0) == []
    assert compute.gamma_walls("GEX", {"spot": 450.0, "gex": {}}, 450.0) == []
```

**Step 2: Run to verify it fails**

Run (from repo root): `.venv\Scripts\python -m pytest services/options_svc/tests/test_compute.py -k gamma_walls -q`
Expected: FAIL — `AttributeError: module 'services.options_svc.compute' has no attribute 'gamma_walls'`.

**Step 3: Implement**

In `services/options_svc/compute.py`, add a module-level function in the Gamma
section (just above `calc_gamma_snapshot`, after the `# ── Gamma ...` banner):

```python
def gamma_walls(vname, data, spot):
    """[put_wall, call_wall] strikes for GEX/DEX (one per side), else [].

    Reuses the engine's directional-wall picker (call wall = strike > spot with
    largest call GEX; put wall = strike < spot with most-negative put GEX). The
    DEX per-strike map is keyed 'dex', so it is remapped to 'gex' for the picker.
    Defensive: any failure degrades to []."""
    import gamma_tool as gt
    try:
        if vname == "GEX":
            w = gt.get_directional_walls(data, spot)
        elif vname == "DEX":
            w = gt.get_directional_walls({"gex": (data or {}).get("dex")}, spot)
        else:
            return []
    except Exception:
        return []
    return [s for s in (w.get("put_wall"), w.get("call_wall")) if s is not None]
```

Then replace the nested `_walls` inside `calc_gamma_snapshot` (lines ~529-537)
with a thin delegate:

```python
    def _walls(vname, data):
        return gamma_walls(vname, data, spot)
```

**Step 4: Run to verify it passes**

Run (from repo root): `.venv\Scripts\python -m pytest services/options_svc/tests/test_compute.py -k gamma_walls -q`
Expected: PASS. Then the full file: `.venv\Scripts\python -m pytest services/options_svc/tests/test_compute.py -q` → all pass.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(gamma/svc): one Call + one Put wall via engine get_directional_walls"
```

---

## Task 4: Persistent Plotly elements + proportional flex (page wiring)

**Files:**
- Modify: `webgui/pages/options/gamma.py` (`render()` element creation + `_render_view`)
- Test: `webgui/tests/test_options_gamma.py` (a guard test); visual + DOM-persistence in Task 5

**This is the flicker fix + applying `panel_flex`/`significant_strikes` in the UI.**
No pure-function unit test covers the wiring; add one structural guard, then verify
in the browser (Task 5).

**Step 1: Write the guard test**

Append to `webgui/tests/test_options_gamma.py`:

```python
def test_render_view_updates_in_place_not_clear():
    """Regression: the flicker fix means _render_view must NOT tear down the
    Plotly elements every repaint. It should update figures in place and must not
    call chart_box.clear()/heatmap_box.clear() (which rebuilt the canvas)."""
    src = inspect.getsource(gamma.render)
    assert "update_figure" in src
    assert "chart_box.clear()" not in src
    assert "heatmap_box.clear()" not in src
    assert "panel_flex" in src           # proportional split is wired
    assert "significant_strikes" in src  # tight y-range is wired
```

**Step 2: Run to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py::test_render_view_updates_in_place_not_clear -q`
Expected: FAIL (current code uses `chart_box.clear()`, no `update_figure`/`panel_flex`).

**Step 3: Implement**

In `webgui/pages/options/gamma.py`:

(a) Add a tiny empty-figure helper near the other builders (~after `heatmap_figure`):

```python
def _empty_fig(height=680):
    """Minimal dark-themed empty figure for first paint / hidden state."""
    return {"data": [], "layout": _apply_dark({"height": height, "autosize": True})}
```

(b) In `render()`, replace the panel container block:

```python
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        chart_box = ui.column().classes("flex-grow min-w-0")
        heatmap_box = ui.column().classes("flex-grow min-w-0")
```

with persistent elements created ONCE:

```python
    with ui.row().classes("w-full no-wrap gap-4 items-start") as panel_row:  # noqa: F841
        chart_box = ui.column().classes("min-w-0").style("flex: 0.5 1 0%")
        with chart_box:
            chart_plot = ui.plotly(_empty_fig()).classes("w-full")
            chart_msg = ui.label("Fetch a symbol… (no snapshot yet).") \
                .classes("opacity-60 text-sm")
        heatmap_box = ui.column().classes("min-w-0").style("flex: 0.5 1 0%")
        with heatmap_box:
            heat_plot = ui.plotly(_empty_fig()).classes("w-full")
            heat_msg = ui.label("").classes("opacity-60 text-sm")
```

(c) Add a flex helper inside `render()` (above `_render_view`):

```python
    def _apply_flex(n_cols, term=False):
        if term:
            chart_box.style("flex: 1 1 0%")
            heatmap_box.style("flex: 0 0 0px")
            heatmap_box.set_visibility(False)
            return
        heatmap_box.set_visibility(True)
        bar_w, heat_w = panel_flex(n_cols)
        chart_box.style(f"flex: {bar_w} 1 0%")
        heatmap_box.style(f"flex: {heat_w} 1 0%")
```

(d) Rewrite `_render_view` to update in place instead of clear/rebuild. Replace the
whole function body with:

```python
    def _render_view():
        """Paint the active view from the cached snapshot (no fetch, no teardown)."""
        snap = state["snap"]
        pressure_box.clear()
        if not snap:
            chart_plot.set_visibility(False)
            heat_plot.set_visibility(False)
            heat_msg.set_visibility(False)
            chart_msg.text = "Fetch a symbol… (no snapshot yet)."
            chart_msg.set_visibility(True)
            summary_lbl.text = ""
            return
        chart_msg.set_visibility(False)

        view = view_toggle.value
        spot = snap.get("spot")
        if view == "Term":
            chart_plot.update_figure(term_heatmap(snap.get("term") or {}))
            chart_plot.set_visibility(True)
            heat_plot.set_visibility(False)
            heat_msg.set_visibility(False)
            _apply_flex(0, term=True)
            summary_lbl.text = summary_text({"spot": spot, "strike_count": None}, "Term")
            return

        entry = (snap.get("views") or {}).get(view) or {}
        raw = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        data = {"spot": raw.get("spot"),
                "strike_count": raw.get("strike_count"),
                "gex": _refloat_keys(raw.get("gex"))}
        view_spot = data.get("spot") or spot
        summary = entry.get("summary") or {}
        flip = entry.get("flip")
        walls = entry.get("walls") or []

        # Shared near-spot range, tight to the strikes that actually have bars.
        yr = bar_yrange(significant_strikes(bars_from_gex(data, view_spot)), view_spot)
        chart_plot.update_figure(
            bar_figure(data, view_spot, view=view, walls=walls, flip=flip, yrange=yr))
        chart_plot.set_visibility(True)
        summary_lbl.text = summary_text(
            {**summary, "strike_count": data.get("strike_count")}, _view_label(view))

        rows = []
        for r in (entry.get("history") or []):
            r = list(r)
            if len(r) > 6:
                r[6] = _refloat_keys(r[6])
            rows.append(tuple(r))
        if rows:
            heat_plot.update_figure(heatmap_figure(rows, view, yrange=yr))
            heat_plot.set_visibility(True)
            heat_msg.set_visibility(False)
        else:
            heat_plot.set_visibility(False)
            heat_msg.text = "No intraday snapshots yet (history collector not running)."
            heat_msg.set_visibility(True)
        _apply_flex(len(rows))

        if view == "DEX":
            hedge = entry.get("hedge") or {}
            with pressure_box:
                hp = hedge.get("hedge_pressure")
                if hp is None:
                    ui.label("0-DTE hedge pressure: n/a (nearest expiry is not 0-DTE)") \
                        .classes("opacity-60 text-sm")
                else:
                    def tile(label, val, color="#bdbdbd"):
                        with ui.card().classes("p-2"):
                            ui.label(label).classes("text-xs opacity-60")
                            ui.label(f"{val:,.0f}").classes("text-base font-bold") \
                                .style(f"color:{color}")
                    tile("Net Δ now", hedge.get("net_delta_0dte") or 0)
                    tile("Projected close", hedge.get("projected_net_delta_close") or 0)
                    tile("Hedge pressure", hp, "#66bb6a" if hp >= 0 else "#ef5350")
```

> Note: the `chart_box`/`heatmap_box`/`chart_plot`/`heat_plot`/`chart_msg`/`heat_msg`
> names must be in scope for `_render_view` and `_apply_flex` (they are — both are
> nested functions defined after the element creation in `render()`).

**Step 4: Run the guard test + full page suite**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_options_gamma.py -q`
Expected: PASS (guard test + all existing builder tests).

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "fix(gamma): persistent Plotly elements (no flicker) + proportional panels + tight range"
```

---

## Task 5: Browser verification

**Goal:** Confirm all four behaviors against the live stack and prove the flicker is gone.

**Preconditions:** Memurai (:6379), schwab-proxy (:8100), `options_svc` (:8211), webgui
(:8500) running. The walls change is in the **service**, so `options_svc` must be
**restarted** to load the new `compute.gamma_walls` before walls show as one-each.
The page change needs the webgui restarted (reload=False) — use the Preview tool.

**Steps:**
1. Restart `options_svc` (load new wall logic), then via the Gamma page click **Refresh now** for `$SPX` so the snapshot recomputes with one-each walls.
2. `preview_start` webgui; navigate to `/options/gamma`.
3. **Walls:** screenshot — confirm exactly one "Call wall" and one "Put wall" label, no overlap. Toggle to DELTA — same.
4. **GAMMA dead space:** on GAMMA, confirm the bar y-axis crops to the populated strike band (no large empty space above/below) and the heatmap shares the range.
5. **Flicker:** capture the chart Plotly node id via `preview_eval`
   (`document.querySelector('.js-plotly-plot')?.id` or the element's stable DOM ref),
   click **Refresh now**, wait for the version-poll repaint, capture again —
   assert the **same** node persists (not replaced). No visible flash.
6. **Proportional panels:** confirm the heatmap panel is wider later in the session
   (more snapshot columns) than the bars; on a cold/early cache the bars are wider.
   If the ratio needs tuning, adjust `panel_flex` `min_heat`/`max_heat` and re-verify.
7. `preview_console_logs` — no JS/Plotly errors.

> If services aren't running in this environment, note verification was deferred;
> the unit tests (Tasks 1–4) are the proof for the pure logic, and the flicker fix
> is a structural change covered by the guard test.

---

## Final: full suites green + docs

```bash
cd webgui ; ..\.venv\Scripts\python -m pytest -q
cd .. ; .venv\Scripts\python -m pytest services/options_svc/tests -q
```

Expected: both green. Then update the root `CLAUDE.md` Gamma row / "Options GUI
polish" note to record: proportional bar/heatmap split, significant-strike y-range,
flicker-free in-place Plotly updates, and single Call/Put walls. Commit.
