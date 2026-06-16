# Gamma charts refresh — design (2026-06-16)

Visual refinements to the Gamma page charts. All changes live in the pure figure
builders + view toggle of `webgui/pages/options/gamma.py` — no service changes.

## Decisions (with user)
- 3D bars = **beveled/outlined** (per-bar darker border).
- Term = keep **full strike range**, dark + **stronger contrast**, **faint cell
  separators**, **less-intrusive hover**.
- Reference lines labeled: **Spot**, **Gamma flip**, **Call wall** (≥ spot) /
  **Put wall** (< spot).

## Changes

1. **Relabel views** — `_VIEW_LABELS = {"GEX": "GAMMA", "DEX": "DELTA"}`. The
   `ui.toggle` shows the friendly labels (GAMMA/DELTA, others unchanged) while
   `view_toggle.value` stays `"GEX"`/`"DEX"` — engine/cache/`if view=="DEX"`
   logic untouched. Chart titles use the friendly label via a `_view_label(view)`
   helper.

2. **Dark theme (all 3 charts)** — shared `_dark_layout(extra)` helper merging:
   `paper_bgcolor`/`plot_bgcolor` `#1b1b1b`, `font.color` `#e6e6e6`,
   axis `gridcolor` `#333`, `zerolinecolor` `#555`. Applied in `bar_figure`,
   `heatmap_figure`, `term_heatmap`.

3. **Beveled bars** — `bar_figure` adds `marker.line` with a per-bar darker shade
   of the fill (`_darker(color)`), width 1. (`bars_from_gex` already returns the
   per-bar fill colors.)

4. **Reference-line labels** — `bar_figure` builds, alongside each `_hline`, a
   right-edge annotation (`xref=paper`, `x=1`, `yref=y`, `showarrow=false`,
   small color-matched font): `Spot {k}`, `Gamma flip {k}`, and per wall
   `Call wall {k}` (k ≥ spot) / `Put wall {k}` (k < spot). Pure helper
   `line_annotations(spot, flip, walls)` → list of annotation dicts (unit-tested).

5. **Axis alignment** — `_render_view` computes one `bar_yrange(...)` near-spot
   range and passes it to BOTH `bar_figure` and `heatmap_figure` (new `yrange`
   param) so strikes align across the two panels.

6. **Heatmap readability** — `heatmap_figure` + `term_heatmap`:
   - `xgap=1`, `ygap=1` → faint cell separators (dark bg shows through).
   - concise `hovertemplate` + `hoverlabel` (small, dark) — less intrusive.
   - Term: keep full strike range; boost contrast via symmetric `zmin/zmax`
     clamped to a robust max of |net| (pure `_robust_zmax(z)`), so mid-values
     aren't washed out by a few extremes.

## Testing
Extend `webgui/tests/test_options_gamma.py`: `_view_label`, `_dark_layout` keys,
`bar_figure` marker.line present, `line_annotations` (spot/flip/call/put wall),
heatmap `xgap`/`ygap` + dark, `_robust_zmax`. Then visual verify in the preview.
