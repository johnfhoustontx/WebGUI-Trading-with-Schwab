# RRG declutter — sampled short tails, hover-isolate, full-width layout — design

**Date:** 2026-06-16
**Page:** Sector Rotation (`/sentiment/rotation`)
**Status:** approved

## Problem

With 11 sectors each carrying a 30-point meteor tail, the RRG scatter is an
unreadable spaghetti. Three changes make it legible:

1. **Shorter, sparser tails** — fewer plotted points per sector.
2. **Hover-isolate** — hovering a sector highlights its dot + tail and dims the
   rest.
3. **Bigger chart** — give the RRG the full panel width (and more height).

Decisions (confirmed with the user):
- Tail = **12 points sampled every other day** (newest, newest−2, …) — spans
  ~24 trading days, half the plotted points.
- Tail style = **fading line + a single bright head dot** (no per-point trail dots).
- Layout = **Full Quadrant Map table top-left, ROTATING FROM/INTO top-right, RRG
  full-width below** (taller).
- Hover = **NiceGUI `plotly_hover`/`plotly_unhover` + `run_plot_method('restyle')`**
  (client-side restyle, no figure rebuild; ~ms latency on localhost).

## Current shape

- Engine `assess_sector` retains `tail = paired.tail(TAIL_LENGTH=30)`.
- `rrg_scatter_figure` builds **11 tail traces** (`lines+markers`, `hoverinfo:skip`,
  opacity ramp) **+ 1 combined head trace** (`markers+text`, all 11 dots).
- RRG sits in a 50/50 row with the quadrant-map table; figure height 360.
- The page is a static `ui.plotly` with no interactivity wiring; `_render` rebuilds
  the figure on each data-version repaint.

## Changes

### 1. Tail data (engine — `sentiment-dashboard/sector_rotation_assessment.py`)

```python
TAIL_LENGTH = 12   # plotted points per sector
TAIL_STRIDE = 2    # sample every other day
```

In `assess_sector`, after building the dropna'd `paired` (oldest→newest) frame,
sample newest-first every `TAIL_STRIDE`, keep `TAIL_LENGTH`, re-reverse:

```python
sampled = paired.iloc[::-1].iloc[::TAIL_STRIDE].iloc[:TAIL_LENGTH].iloc[::-1]
tail = [{"rs_ratio": round(float(r), 2), "rs_momentum": round(float(m), 2)}
        for r, m in zip(sampled["ratio"], sampled["mom"])]
```

The last element is the newest valid point (= the head reading). Fewer than
`(TAIL_LENGTH-1)*TAIL_STRIDE + 1` valid rows → a shorter sampled tail (no error).

### 2. One trace per sector + line-and-head style (page — `rrg_scatter_figure`)

Replace the 11-tails-plus-combined-head structure with **one trace per sector**
via a `_sector_trace(sec)` builder:

```python
def _sector_trace(sec):
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
                   "size":    [0.0] * (n - 1) + [13],     # only the head dot shows
                   "opacity": [0.0] * (n - 1) + [1.0]},
        "text": [""] * (n - 1) + [sec.get("etf") or ""],
        "textposition": "top center", "textfont": {"size": 10},
        "hovertemplate": (f"{sec.get('name')} ({sec.get('etf')}) — "
                          f"{sec.get('quadrant')}<br>RS-Ratio %{{x:.2f}} · "
                          f"RS-Mom %{{y:.2f}}<extra></extra>"),
        "showlegend": False,
    }
```

`rrg_scatter_figure` returns `data = [sector traces…]` (one per sector, order =
`sectors` order ⇒ `curveNumber == sector index`), keeps the 100/100 crosshair
shapes, sets `hovermode:"closest"`, and bumps `height` to ~560.

> Hover hit-test risk: opacity-0 trail markers should still register as `closest`
> targets. If not, bump trail-marker opacity to a near-zero nonzero value
> (visually negligible, definitely hoverable). Verify + tune in-browser.

### 3. Hover-isolate wiring (page — `render()`)

Pure helper (unit-tested):

```python
def _focus_opacities(n, focus, dim=0.12):
    """Trace-opacity list: 1.0 for `focus`, `dim` for the rest. All 1.0 when
    focus is out of range (restore / unhover)."""
    if focus is None or not (0 <= focus < n):
        return [1.0] * n
    return [1.0 if i == focus else dim for i in range(n)]
```

In `_render`, after creating the plot element:

```python
n = len(fig["data"])
def _on_hover(e):
    pts = (getattr(e, "args", None) or {}).get("points") or []
    cn = pts[0].get("curveNumber") if pts else None
    plot.run_plot_method("restyle", {"opacity": _focus_opacities(n, cn)}, list(range(n)))
def _on_unhover(e):
    plot.run_plot_method("restyle", {"opacity": _focus_opacities(n, None)}, list(range(n)))
plot.on("plotly_hover", _on_hover)
plot.on("plotly_unhover", _on_unhover)
```

(Handlers are re-attached whenever `_render` rebuilds the figure on a data repaint;
highlight state resets on repaint — acceptable, repaints are infrequent.)

### 4. Layout (page — `render()`)

Restructure the static layout:

- **Top row** (`items-start`): left column = "Full Quadrant Map" label + `table_box`;
  right column = ROTATING FROM/INTO (`cols_box`).
- The "Pairing is ordinal…" note stays under the top row.
- **Below, full width**: "RRG" label + `rrg_box` (taller figure).

`_render` keeps populating the same `table_box` / `cols_box` / `rrg_box` — only
their parent containers move.

## Testing

- **Engine** (`sentiment-dashboard/tests`): tail length ≤ `TAIL_LENGTH`; consecutive
  tail points are `TAIL_STRIDE` apart in the source series; `tail[-1]` == the head
  `(rs_ratio, rs_momentum)`.
- **Page** (`webgui/tests/test_sentiment_rotation.py`):
  - `rrg_scatter_figure` emits **one trace per sector** (mode `lines+markers+text`),
    each with exactly one visible head marker (last `marker.opacity` == 1.0, rest 0)
    and `showlegend:false`; `curveNumber`/order maps to sectors.
  - Missing-`tail` sector → single-point head trace (no crash).
  - `_focus_opacities` — focused index = 1.0 and others = dim; out-of-range/None →
    all 1.0.
- **Browser**: hover a sector → its dot+tail stay bright, others dim; unhover
  restores; the full-width taller RRG renders with the new top-row layout;
  confirm the line/head is hoverable. Verified against the live `$SPX`… cache.

## Out of scope (YAGNI)

- No click-to-pin a sector.
- No per-point hover values along the trail (head only).
- No UI control for tail length / stride.
