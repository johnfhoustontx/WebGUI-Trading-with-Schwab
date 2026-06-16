# Gamma page — proportional panels, tight GAMMA range, no flicker, single walls — design

**Date:** 2026-06-16
**Page:** Gamma (`/options/gamma`)
**Status:** approved

## Goals

Four fixes/improvements to the Gamma page, from a live screenshot review:

1. **Proportional panels** — as the trading day progresses, the intraday heatmap
   (right) should expand and the exposure bars (left) should shrink.
2. **GAMMA dead space** — the GEX/GAMMA view shows empty strike space above
   ~7620 and below ~7440; the bars + heatmap should crop to where data actually
   is. Charm/DEX/Vanna already look fine.
3. **Flicker** — the charts flash when they regenerate (e.g. the 120 s
   auto-refresh).
4. **Multiple walls** — GAMMA shows 3 Call walls + 2 Put walls with overlapping
   labels. Reduce to one Call + one Put wall.

Decisions (confirmed with the user):
- Walls: **one Call + one Put** wall.
- Resize driver: **snapshot count** (data-driven, no timezone logic on the page).

## Current shape (what we found)

- `webgui/pages/options/gamma.py` is a Tier-3 reader: it reads `cache:options:gamma`
  and renders. Pure builders (`bars_from_gex`, `bar_yrange`, `bar_figure`,
  `heatmap_figure`, `line_annotations`) are unit-tested.
- Layout: `chart_box` (bars) and `heatmap_box` (heatmap) are both
  `ui.column().classes("flex-grow min-w-0")` → fixed 50/50.
- `bars_from_gex` includes **every** strike within ±2 % of spot, even near-zero
  ones at the edges. The shared `yr = bar_yrange(...)` (used by both panels)
  stretches to those, producing GAMMA's dead space. GAMMA's chain tapers to
  near-zero gamma at the window edges; the other views don't.
- `_render_view` calls `chart_box.clear()` / `heatmap_box.clear()` and builds a
  **new** `ui.plotly(...)` on every repaint → Plotly canvas teardown = flicker.
- Walls come from the service: `services/options_svc/compute.py` `_walls()` →
  `gt.get_gex_walls(data, top_n=5)` (and `get_dex_walls`), i.e. the 5
  largest-`|net|` strikes regardless of side. The page labels each Call/Put by
  position vs spot → multiple same-side walls + overlapping labels.

## Changes

### 1. Proportional panels (page, `gamma.py`)

New pure helper:

```python
def panel_flex(n_cols, full_cols=82, min_heat=0.28, max_heat=0.70):
    """(bar_weight, heat_weight) flex ratio from intraday snapshot count.

    full_cols = five-minute slots in an 08:30–15:20 CT session (~82). The heatmap
    fraction lerps min_heat→max_heat with session progress; bars get the rest."""
    p = 0.0 if full_cols <= 0 else max(0.0, min(1.0, n_cols / full_cols))
    heat = min_heat + (max_heat - min_heat) * p
    return round(1.0 - heat, 4), round(heat, 4)
```

`_render_view` applies it from `len(rows)`:
`chart_box.style(f"flex: {bar_w} 1 0%")`, `heatmap_box.style(f"flex: {heat_w} 1 0%")`.
Term view → chart full width, heatmap panel hidden.

### 2. GAMMA dead space (page, `gamma.py`)

New pure helper:

```python
def significant_strikes(bars, frac=0.03):
    """Strikes whose |net| ≥ frac·peak (drops near-zero edge strikes).

    bars is the bars_from_gex(...) dict. Returns all strikes when peak is 0."""
    strikes, nets = bars.get("strikes") or [], bars.get("nets") or []
    peak = max((abs(n) for n in nets), default=0.0)
    if peak <= 0:
        return list(strikes)
    thr = peak * frac
    return [s for s, n in zip(strikes, nets) if abs(n) >= thr]
```

`_render_view` computes `yr` from significant strikes:
`yr = bar_yrange(significant_strikes(bars_from_gex(data, view_spot)), view_spot)`.
Both panels keep sharing `yr`, so the bar chart and heatmap crop together.

### 3. Flicker (page, `gamma.py`)

Create the two Plotly elements **once** in `render()` and update in place:

- In `chart_box`: `chart_plot = ui.plotly(_empty_fig())` + a persistent
  `chart_msg` label. In `heatmap_box`: `heat_plot` + `heat_msg`.
- `_render_view` calls `chart_plot.update_figure(fig)` / `heat_plot.update_figure(fig)`
  (NiceGUI → Plotly.react diff, no teardown) and toggles plot/message visibility
  with `set_visibility(...)`.
- The DEX pressure-tile row keeps its cheap `pressure_box.clear()` rebuild (small
  labels, not a canvas — no flicker).

`_empty_fig()` is a minimal dark-themed empty figure for first paint.

### 4. Walls → one Call + one Put (service, `compute.py`)

Fix where walls are produced so the page stays a pure renderer and the shared
`gt.get_gex_walls` engine (also used by the Tk desktop app) is untouched. Replace
`_walls()` for GEX & DEX with a one-per-side pick:

```python
def _one_each_walls(data, key):
    """[put_wall, call_wall] strikes: call = strike ≥ spot with max net
    (resistance), put = strike < spot with min net (support). Missing side
    omitted; empty when no data."""
    spot = (data or {}).get("spot")
    per = (data or {}).get(key) or {}
    if spot is None or not per:
        return []
    above = {s: (c or {}).get("net", 0.0) for s, c in per.items() if s >= spot}
    below = {s: (c or {}).get("net", 0.0) for s, c in per.items() if s < spot}
    out = []
    if below:
        out.append(min(below, key=below.get))   # most negative below spot
    if above:
        out.append(max(above, key=above.get))   # most positive above spot
    return out
```

`_walls("GEX", data)` → `_one_each_walls(data, "gex")`; `_walls("DEX", data)` →
`_one_each_walls(data, "dex")`. Charm/Vanna keep returning `[]`. The page renders
`entry["walls"]` unchanged; `line_annotations` already labels by side.

## Testing

- **Page** (`webgui/tests/test_gamma.py`):
  - `panel_flex` — `n=0` → bars-wide, `n≥full_cols` → heat = `max_heat`,
    monotonic non-decreasing heat, clamps past full.
  - `significant_strikes` — crops near-zero tails; no-op when all significant;
    all-zero → returns all.
- **Service** (`services/options_svc/tests/test_compute.py`):
  - `_one_each_walls` — returns exactly one strike per side (put < spot, call ≥
    spot); only-one-side data → single wall; empty/missing → `[]`.
- **Flicker** — verified in-browser: the Plotly DOM node **persists** (same
  element id) across a refresh instead of being replaced.
- **Visual** — y-range crop, single walls, and the proportional split verified
  against the live `$SPX` snapshot.

## Out of scope (YAGNI)

- No per-element chart-style configuration.
- No UI toggle for wall count or panel ratio.
- No change to the heatmap's zero-strike-dropping logic.
