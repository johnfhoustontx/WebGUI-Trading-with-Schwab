# RRG meteor tails + tighter FROM/INTO spacing — design

**Date:** 2026-06-16
**Page:** Sector Rotation (`/sentiment/rotation`)
**Status:** approved

## Goal

Two changes to the Sector Rotation page:

1. **Tighten the gap** between the `ROTATING FROM` and `ROTATING INTO` columns
   (currently ~half the container width) by ~75–80%.
2. **Add "meteor tails"** to the RRG scatter — each sector's prior **30 trading
   days** of `(RS-Ratio, RS-Momentum)` movement, drawn as a fading trail behind
   its current dot.

Decisions (confirmed with the user):
- Tail length = **30 trading days** (the last 30 valid daily RRG points).
- Tail color = **match the sector's current quadrant color** (green / cyan /
  yellow / red), fading toward the oldest point.

## Where the data already is

`sector_rotation_assessment.assess_sector()` computes the **full** RS-Ratio and
RS-Momentum *series* per sector, then discards everything except `.iloc[-1]`
(the current point). The 30-day trail is therefore already computed — we just
retain the tail instead of throwing it away.

The assessment dict flows verbatim through `build_assessment()` →
`services/sentiment_svc` cache (`cache:sentiment:rotation`, plain JSON, **no
typed contract that strips unknown fields**) → the page. So adding a `tail` key
to each sector dict needs **no service-code change** — it rides the existing
passthrough.

## Change 1 — spacing

`webgui/pages/sentiment_rotation.py`, `_render()`: each FROM/INTO column is built
with `.style("flex:1")` inside a `w-full ... gap-6` row. `flex:1` stretches each
column to half the row, pushing the two text blocks to opposite halves — that is
the gap.

Fix: drop `flex:1` so each column hugs its content, and set a tight fixed gap
between the two columns. The exact gap is tuned visually to land ~75–80% below
the original whitespace.

## Change 2 — meteor tails

### Engine (`sentiment-dashboard/sector_rotation_assessment.py`)

- Add `TAIL_LENGTH = 30`.
- In `assess_sector()`, after building the dropna'd `paired` frame
  (`ratio` + `mom`), attach the last `TAIL_LENGTH` rows as
  `"tail": [{"rs_ratio": float, "rs_momentum": float}, ...]`, ordered
  **oldest → newest**; the final entry equals the current head point. Values
  rounded to 2dp like the head. Fewer than 30 valid rows → a shorter tail (no
  error).

### Page (`webgui/pages/sentiment_rotation.py`, `rrg_scatter_figure`)

For each sector with a `tail`, prepend one tail trace:

```
{
  "type": "scatter", "mode": "lines+markers",
  "x": [ratio...], "y": [mom...],
  "line":   {"color": "rgba(<quadrant rgb>, 0.28)", "width": 1.5},
  "marker": {"color": "<quadrant color>",
             "size":    [3 .. 6  ramp],
             "opacity": [0.12 .. 0.85 ramp]},   # meteor fade, head brightest
  "hoverinfo": "skip", "showlegend": False,
}
```

Trace order becomes `[...tail traces..., head trace]` so the bright labeled head
dots (the existing `markers+text` trace) render on top. A sector with no `tail`
(cold / pre-upgrade cache) simply shows its head dot — no tail, no error.

A small helper converts a quadrant hex color → `rgba(r,g,b,a)` for the faint
line, and builds the size/opacity ramp arrays for a tail of length *n*.

## Testing

- **Engine** (`sentiment-dashboard/tests`): `assess_sector` / `build_assessment`
  return a `tail` list of ≤30 oldest→newest `(rs_ratio, rs_momentum)` pairs whose
  last element equals the head `(rs_ratio, rs_momentum)`.
- **Page** (`webgui/tests/test_sentiment_rotation.py`):
  - `rrg_scatter_figure` emits one tail trace per sector that has a `tail`, with
    a monotonically non-decreasing marker-opacity array and the sector's quadrant
    color.
  - The head trace is located by `mode == "markers+text"` (the existing
    `data[0]`-index assertion is updated, since tails now precede the head).
  - A sector dict without `tail` still produces a valid figure (head dot only).

## Out of scope (YAGNI)

- No per-tail-point hover text.
- No UI control for tail length.
- No recoloring of the head dots (they stay quadrant-colored).
