# Dealer Positioning — plasma palette + background wash (design)

**Date:** 2026-08-15
**Scope:** `webgui/pages/options/gamma.py` (Tier-1 only — no service, contract, or cache change)
**Source:** the attached `Application dashboard design.zip` → `gamma_heatmap_export/`
(a self-contained "Gamma Exposure Map" reference render + its component source).

## What changes

The five heatmap views on `/options/gamma` — **GEX, Charm, DEX, Vanna** (intraday
strike × time) and **Term** (expiry × strike) — move from the red↔transparent↔green
`HEAT_STOPS` scale to the reference design's **plasma** palette: cyan/ice for
call-heavy (positive net), magenta/pink for put-heavy (negative net), over a
blue→magenta vertical background wash.

Scope was chosen explicitly as **palette + wash**, not a full visual rebuild. The
design's bloom/blur passes are CSS filters over a grid of DOM cells; our heatmap is
a Highcharts-rasterized `<image>`, so they do not port and are out of scope.

## 1. `HEAT_STOPS`

`_coloraxis()` is shared by `heatmap_figure` (all four intraday views) and
`term_heatmap`, so one stops list restyles all five. The axis is **diverging**
(0.0 = most negative … 0.50 = zero … 1.0 = most positive) while the design's ramps
are one-sided intensity ramps, so each ramp is mirrored outward from the centre:

| stop | colour | design source |
|---|---|---|
| 0.00 | `rgba(255,186,220,.98)` | put ramp, brightest (`#FFBADC`) |
| 0.12 | `rgba(255,77,141,.92)` | put "hot" (`#FF4D8D`) |
| 0.28 | `rgba(150,36,122,.62)` | `#96247A` |
| 0.48 | `rgba(52,10,44,0)` | put ramp base (`#340A2C`), faded out |
| 0.50 | `rgba(0,0,0,0)` | zero — the wash shows through |
| 0.52 | `rgba(8,44,78,0)` | call ramp base (`#082C4E`), faded out |
| 0.72 | `rgba(42,118,224,.62)` | `#2A76E0` |
| 0.88 | `rgba(53,200,255,.92)` | call "hot" (`#35C8FF`) |
| 1.00 | `rgba(190,248,255,.98)` | call ramp, brightest (`#BEF8FF`) |

Two properties are carried over deliberately:

- **The alpha ramp stays.** Net ≈ 0 fading to transparent is what lets the heatmap
  blend into the page instead of sitting in a box, and the design does the same
  (`0.10 + a^1.05 × 0.90`).
- **The bright stops are pushed out** to 0.88 / 0.12 rather than spread evenly, so
  most of the field sits in the deep blue / aubergine and only the cores reach
  cyan and ice. This reproduces the design's `pow(a, 1.7)` colour-position
  shaping without needing a non-linear axis.

## 2. Background wash

`chart.plotBackgroundColor` becomes a vertical linear gradient using the design's
exact stops — `#0a1626 → #0b1420 (46%) → #140b16 (62%) → #170a12` — blue at the top
fading to magenta at the bottom, so quiet strikes read as dim colour rather than
empty space. A 1px `plotBorderColor: rgba(120,140,160,0.16)` frames it as a panel,
matching the design's bordered box.

### Reversing a prior decision (read this before "fixing" it back)

Commit `e6ef342` **removed** `plotBackgroundColor` and left two tests asserting
`"plotBackgroundColor" not in fig["chart"]`. That is not a ban on the key — at the
time it held a **flat grey** (`HEATMAP_SEP = "#4d4d4d"`) which showed through the
gaps between individually-bordered cells and read as a separator mesh. The same
commit turned on `interpolation: True`, which renders the heatmap as ONE continuous
image with no cell gaps at all, so nothing can show *between* cells any more. A
gradient here is a wash painted *behind* that image, not a mesh.

Those two assertions are therefore **rewritten, not deleted** — they now pin the
new intent (a gradient wash, and specifically *not* the flat `HEATMAP_SEP` grey),
so the mesh regression they were written to catch is still caught.

`HEATMAP_SEP` itself is deleted; nothing references it once the wash lands.

## 3. Colours that follow the palette

| constant | was | now | why |
|---|---|---|---|
| `POS_COLOR` / `NEG_COLOR` | `#66bb6a` / `#ef5350` | `#35c8ff` / `#ff4d8d` | the by-strike bars are the design's net-γ rail; they must read as the same instrument as the field beside them |
| `WALL_COLOR` | `#b39ddb` (both walls) | split → `CALL_WALL_COLOR` `#35c8ff`, `PUT_WALL_COLOR` `#ff4d8d` | the design colour-codes each wall to its side; `wall_plot_lines` / `line_annotations` already compute which side a wall is on |
| `FLIP_COLOR` | `#42a5f5` | `#b39ddb` | `#42a5f5` would collide with the call ramp's `#2a76e0`; the lavender the walls just vacated is free and stays distinct |
| Spot line | no halo | `shadow` — black, width 5 | the design draws a black polyline under the white one; a series `shadow` is one config key, so the load-bearing fixed series count is untouched |

`PROJ_FLIP_COLOR` (amber `#ffb74d`) is unchanged. The three level colours stay
mutually distinct: sided walls (cyan/magenta), gamma flip (lavender), projected
flip (amber).

## 4. Deliberately unchanged

- **EM cone** (`#7fd1a3` / `#e79a9a`) and the candle / hedge-panel
  `UP_COLOR` / `DOWN_COLOR`. These encode **price direction** and **hedge side**,
  not gamma sign, and the candles share the panel with the cone — recolouring one
  and not the other would read worse than leaving both.
- `PRICE_LINE` stays `#f5f5f5` (the design's `#f2f7fb` is indistinguishable).
- Flow and Net Prem are line charts with no `colorAxis`; untouched by construction.
- No bloom / blur / glow pass — see the scope note above.

## 5. Verification

Pure-builder unit tests for: the stops' diverging shape and endpoint hues, the
transparent zero, the wash gradient's presence and orientation, the per-side wall
colours, and the spot shadow. Then the real check — dev stack on `:9500`, every
view (GEX / Charm / Delta / Vanna / Term, plus Flow to confirm nothing leaked
into the non-colorAxis charts) rendered and screenshotted.
