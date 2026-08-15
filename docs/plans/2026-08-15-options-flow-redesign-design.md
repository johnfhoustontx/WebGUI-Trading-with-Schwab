# Options Flow redesign — Premium Divergence + Flow Field (design)

**Date:** 2026-08-15 · **Scope:** the **Flow** and **Net Prem** views of
`/options/gamma` (Dealer Positioning) · **Source:** the `Modern dashboard
redesign.zip` handoff (`Options Flow Design/`), whose `README.md` is the
authoritative spec and whose `options-flow.html` carries the reference markup.

## What changes

Two views are replaced, in place, by self-contained dark-console panels built to
the **same visual system the Gamma heatmap already uses** (plasma cyan/magenta,
no border radius, glow-only shadows, Rajdhani display + IBM Plex Mono numerals):

| View | Was | Becomes |
|---|---|---|
| **Flow** | `flow_figure` — Highcharts, 3 lines + a net area panel, floating tooltip, bottom legend | **Premium Divergence** — one canvas: call/put two-tone ribbon + white spot line, status chips, a **strike ladder**, and a right readout rail |
| **Net Prem** | `net_prem_figure` — Highcharts, up to 28 lines + a wrapping legend | **Flow Field** — shared-scale lines with **terminus labels**, symbol status chips, and a live **leaderboard** rail |

Nothing about the *data* the views describe changes, and no other view moves.
The palette is already the app's (`POS_COLOR` `#35c8ff` / `NEG_COLOR` `#ff4d8d`
are byte-identical to the spec's call/put colours), so this is continuous with
the 2026-08-15 plasma work rather than a second visual language.

## The three things the spec removed, and why they were right

1. **The floating tooltip** sat on top of the data it described. Replaced by a
   fixed right rail that never occludes the plot.
2. **The bottom legend** duplicated information and, on Net Prem, wrapped into
   several rows that collided with the rotated time labels (a live bug —
   `net_prem_figure` carries a comment about exactly this). Replaced by status
   chips above the plot and terminus labels at each line's right end.
3. **The yellow price line** (`FLOW_PRICE = #e8d44d`) read as a *third premium
   series* beside the green/pink pair. Now white (`#eaf6ff`), on its own scale.

## Two decisions taken with the user

### 1. Per-strike premium gets its own stored history

The strike ladder needs call/put premium **by strike, at the cursor's
timestamp**. Nothing in the stack held that: `flow_skew.index_call_put_premium`
collapses the whole chain to two scalars per snapshot, and `gex_history.db`
stores per-strike **GEX**, not premium.

The user chose stored history (over a live "now-only" ladder) so the ladder
recomputes as you scrub, as the spec shows.

**It costs no schema change.** `snapshots` is keyed `(symbol, view, ts)` with a
free-form `view` string and a `gex_json` grid whose cells are
`{call, put, net}` floats — exactly the shape per-strike premium takes. So this
lands as a **fifth view string, `"prem"`**, written by the same
`insert_snapshot` and read by the same `load_date_with_grid`. The columnar
float32 packer accepts it unchanged (three plain numbers per cell is precisely
its shape gate).

- **Cost:** one extra row per symbol per minute — **+25% on `gex_history.db`**
  (four views become five). Vacuum via `tools/vacuum_gex.py` as usual.
- **Forward-only.** The ladder is empty until data accrues, and says so rather
  than rendering an empty frame that reads as breakage.
- **float32 is safe here for the same reason it is for GEX** (values are rounded
  to 6 sig figs upstream), and premiums are dollars in the 1e2–1e8 range, far
  above the 1.18e-38 denormal floor that flushes to zero.

### 2. Full client-side scrub

Mousemove over either chart updates the chips, the rail, the ladder and the
leaderboard **with no server round-trip**. The page ships the already-cropped
series to the browser as one JSON blob and a small page-scoped script rewrites
text nodes and bar widths — the same class of escape hatch as the existing
`_CROSSHAIR_JS` and `_HEAT_PRESS_TOOLTIP_JS`.

Leaving the plot returns to the session default, as the spec specifies.

## Rendering: hand-rolled SVG, not Highcharts

These two panels leave Highcharts. Three reasons, in order of weight:

1. **The spec is SVG.** Its reference implementation is hairline paths, a
   two-tone ribbon built from per-segment fills, and decluttered terminus
   labels. Every one of those is a fight against Highcharts and a
   straight-ahead string build in Python.
2. **The repo already has this idiom, twice.** `pages/rings.py` and
   `pages/regime_mix.py` are pure SVG-string builders mounted with `ui.html` and
   updated via `el.content`. Pure string in, no chart instance, trivially
   testable.
3. **It sidesteps two documented Highcharts hazards outright** — the
   ESM-import-map trap (a chart added to a page that had none at first render
   fails to resolve `nicegui-highcharts`) and the `chart.update()` merge/type-
   switch leakage that `_set_chart` exists to work around. The Flow ↔ Net Prem
   switch is currently a chart **recreation** for exactly that reason; as SVG it
   is a string swap.

### The DOMPurify constraint is load-bearing

`ui.html` sanitises through NiceGUI's bundled DOMPurify (it monkeypatches
`Element.prototype.setHTML`), and its allow-list is **not** the native one. In
particular `dominant-baseline` is stripped — the obvious way to centre SVG
`<text>` — which silently mis-positioned every label on the sentiment rings
while the server-side string stayed correct and the suite stayed green.

So: **no `dominant-baseline`, no `foreignObject`, no SVG `<filter>`.** Vertical
centring uses the allow-listed `dy="0.35em"` idiom (`rings._BASELINE_DY`), and
the glow is a layered halo (a wide translucent copy of the path under a normal-
width bright one) exactly as `rings.py` does it — which is also what the spec
itself specifies, for the same reason: no blur filters, so the panels stay cheap
to render.

A test extracts the allow-list out of the shipped `dompurify.mjs` and asserts
every tag and attribute these builders emit survives it, mirroring
`test_rings.py::test_ring_svg_emits_nothing_dompurify_would_strip`.

## Palette: a page-scoped `[flow]` section

Following the `[console]` (sentiment) and `[macro]` (market) precedent exactly:
a new `[flow]` section in `config/theme.toml`, a `build_flow_*` family in
`theme.py`, and **one** `ui.add_css` block. Not surfaced in Settings →
Appearance — that editor's sections are single-kind and this one mixes colours
with font text.

Rajdhani and IBM Plex Mono are **already loaded app-wide** (`[menu].font_url`
and `[typography].font_url` respectively), so this section carries no font URL
of its own.

## What is deliberately kept

- **The 28-symbol Net Prem selector**, its group tabs, and the persisted
  selection. The spec's seven symbols are its sample data, not a reduction in
  scope.
- **Dollars / Skew %.** The spec's `DOLLARS · PERCENTILE` toggle maps onto the
  existing two modes; only the chrome changes. Skew % also answers the spec's
  own "known trade-off" — that a dominant name compresses the small ones on a
  shared dollar scale.
- **`net_prem_status_text`**, the publisher-health line. It reports a failure
  mode no other element can see (a stale publish *inside* the collection
  window), and it is clock-driven rather than repaint-driven for that reason.

## Known limits

- **The ladder is forward-only** and empty until `options_svc` has been
  restarted and has collected. This is stated in the panel, not left to be
  inferred from a blank frame.
- **Premium is unsigned and mark-based.** Schwab publishes no tape, so
  `Σ mark × totalVolume × 100` is a daily-cumulative *estimate* and not a
  buy/sell split — unchanged from the existing Flow view, and the reason the
  rail says "premium", never "buying".
- The ladder's strikes come from the **collected expiration window** (today →
  +7d), matching what the collector already fetches.
