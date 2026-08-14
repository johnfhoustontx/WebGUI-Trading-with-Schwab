# Sentiment + Trend ring graphics — design

**Date:** 2026-08-14
**Status:** approved, pending implementation
**Scope:** `/sentiment` top region only (Market Sentiment + Market Trend panels),
plus one additive `sentiment_svc` compute.

## Problem

The `/sentiment` page mounts **four** copies of the shared Highcharts angular
gauge (`webgui/pages/gauge.py`) — a semicircular painted red→yellow→green face
with a needle:

| Panel | Gauges today |
|---|---|
| Market Sentiment | Today · 30-Day Avg |
| Market Trend | Today · 30-Day |

Each is `170×120px`, sitting side by side inside its panel. Four near-identical
semicircles crowd the top region, and comparing a value to its own average means
reading two separate dials.

The requested replacement is a **neon concentric-ring** graphic that folds
multiple horizons into one dial — and extends the two horizons to **three: Day,
Week, Month**.

## Decisions

Four decisions were taken during brainstorming; each is recorded with the
alternative that was rejected, because the rejected option is the thing a future
reader will be tempted to "fix" back.

### D1 — One combined ring per panel, three arcs (Day / Week / Month)

Rejected: keeping one value per graphic (four rings). The whole point of the
concentric form is that today-vs-average becomes a *visual* comparison instead of
a side-by-side read; four separate rings would keep the crowding and leave the
inner arcs unused.

`/sentiment` goes from **4 gauges → 2 ring graphics**.

### D2 — Inline SVG, not Highcharts, not CSS

**Chosen: a pure SVG-string builder mounted via `ui.html()`.**

| | Highcharts `solidgauge` ×3 | CSS `conic-gradient` | **Inline SVG** |
|---|---|---|---|
| Neon glow | CSS filters onto `.highcharts-series` | `filter` / `box-shadow` | `<feGaussianBlur>` + `feMerge`, exact control |
| Rounded arc caps | supported | **impossible** | `stroke-linecap="round"` |
| 3 concentric arcs | per-series `radius`/`innerRadius`, fiddly | stacked masked divs | trivial |
| Center text (3 stacked values) | `useHTML` dataLabels across 3 series | easy | easy |
| Update in place | `chart.update()` merge semantics | runtime CSS vars | `el.content = svg` — one string swap |
| Unit-testable | options dict | no | pure function |

Three further reasons SVG wins here:

1. It sidesteps the documented `ui.highchart` **ESM-import-map trap** (a chart
   added to a page that had none at first render fails to resolve
   `nicegui-highcharts`) and the `chart.update()` merge hazards.
2. Raw `ui.html()` fragments are an **explicitly documented exemption** from the
   Tailwind-first no-inline-style standard, so this does not fight the house rule
   the way runtime CSS custom properties would.
3. Precedent exists — `webgui/pages/options/svg.py` already builds inline SVG
   fragments as pure, unit-tested functions.

**`webgui/pages/gauge.py` is NOT modified or deleted.** It still serves the
options detail-panel speedometer (`pages/options/detail.py`). Accepted
consequence: the app carries two gauge idioms until someone unifies them.

### D3 — 270° sweep, gap at the bottom

The reference image labels 0 at the bottom, 25 left, 75 right and 100 at the top,
which is not a consistent single sweep — it reads as decorative rather than as a
spec. Rejected both "match the reference literally" (the left and right halves
would be separate scales, making any single value ambiguous) and a full 360°
(0 and 100 indistinguishable, no natural break for tick labels).

**Geometry:** start 225°, sweep 270°, end 135°, measured **clockwise from 12
o'clock**. So 0 sits lower-left, 25 upper-left, 50 at top, 75 upper-right, 100
lower-right, and the bottom gap gives the stacked center numbers room.

### D4 — Color is dynamic per arc, from that arc's own value

Each of the three arcs is colored independently by its own value through the
existing `gauge._ramp_color()` ramp, so `config/theme.toml [gauge]`
(`low`/`mid`/`high`) continues to drive the palette — no new hardcoded hexes, and
the existing Settings → Appearance editor keeps working.

Consequence worth stating: a Day-green / Month-red divergence is immediately
legible, but the arcs no longer carry a fixed identity colour. Ring **position**
(outermost = Day) plus the center legend is what identifies them.

## Component

New pure module **`webgui/pages/rings.py`** — no `nicegui` import, no engine
import.

```python
def ring_svg(arcs, uid, size=280) -> str:
    """arcs = [{"value": 72.0, "caption": "DAY"},    # outermost
               {"value": 61.0, "caption": "WEEK"},
               {"value": 52.0, "caption": "MONTH"}]"""
```

**Dimensions at the default `size=280`** (centre 140,140):

| Arc | Radius | Stroke |
|---|---|---|
| Day (outer) | 112 | 13 |
| Week | 90 | 13 |
| Month (inner) | 68 | 13 |

Outermost edge lands at 118.5; tick labels sit at r=132, inside the 140
half-width. The inner clear diameter is ~123px, enough for a ~44px value, an
~11px caption and two ~15px rows beneath.

280px was chosen over 240px specifically because the Month arc is the innermost
and smallest — at 240 it read as cramped. If it still does, the fix is a larger
`size`, **not** thinner strokes.

**Each arc draws two paths:** a dim full-270° track, then the value arc
(`stroke-linecap="round"`) sweeping `270 × value/100`.

**`uid` is required, not optional.** Two rings on one page means two `<filter>`
and gradient elements; duplicate DOM ids would silently make both rings share one
glow. All ids are suffixed with `uid`.

**Center:** the outermost arc's value as the large number, its caption beneath,
then the remaining two values stacked in smaller type, each tinted to match its
own arc.

### Sanitizer risk — verify first

`ui.html()` strips `<style>` and `<iframe>`. It is **not yet confirmed** that
`<defs>` / `<filter>` / `<feGaussianBlur>` survive. This is implementation step 1,
not an assumption.

**Fallback if filters are stripped:** layer a wide translucent arc beneath a
narrow bright one. That reads as glow with no filter element at all, and needs no
change to the rest of the design.

## Data flow

### Market Sentiment — no backend change

Generalize the existing `sentiment_30d_avg(snaps)` to `sentiment_avg(snaps, n=None)`:

| Arc | Source |
|---|---|
| Day | live composite `total_score` ×10 |
| Week | mean of the last **5** snaps ×10 |
| Month | mean of all snaps ×10 (today's behaviour) |

### Market Trend — one additive Tier-2 change

| Arc | Source |
|---|---|
| Day | `derived.trend.smoothed_score` (exists) |
| Week | `derived.trend_7d` — **NEW** |
| Month | `derived.trend_30d_ago` (exists, relabeled) |

**`compute_30d_trend` is misnamed and the UI inherits the error.** It is not the
trend *30 days ago* and it is not an average: it is a **monthly-horizon
structural read** (12-month SPY daily bars + sector **month** % moves, price and
sector only, no VWAP/breadth/VIX, no smoothing or hysteresis). Relabeling that arc
"Month" is therefore *more* accurate than the "30-Day" on screen today. The
function is not renamed — that would churn the service, its tests and the cached
payload key for no user-visible gain.

`compute_7d_trend` mirrors it exactly: same price+sector structure, scored on
sector **`week_pct`** and a shorter SPY lookback, with its own `_CYC_DEF_SCALE_7D`
and TTL cache. Computed on the existing 15-min `trend_due` gate and held in
`_TREND` alongside the other two.

**API cost is ~zero.** `week_month_from_closes` already returns `week_pct`
alongside `month_pct` from the same fetch. Refactor `_fetch_sector_month_pcts`
into a single `_fetch_sector_pcts()` returning both horizons rather than adding a
second independent fetch — this repo's Schwab call volume is already audited at
~68–76k/day and a duplicate sector fetch would be pure waste.

**Contract touchpoints:**

- `derive_composite_extras(...)` gains `trend_7d` in its returned dict.
- `services/sentiment_svc/tests/test_compute.py` asserts
  `set(out) == {...}` exactly, so it **will fail loudly** — that is the intended
  signal, and the assertion is updated as part of the change.

### Semantic caveat (inherent, not introduced)

Sentiment's Week/Month arcs are **averages**; Trend's are **horizon-scoped
structural reads**. Both are honestly "Day/Week/Month", but they are not the same
kind of number. The current four-gauge UI already has this property; the ring
does not make it worse, but it does place them closer together.

## Page wiring

`webgui/pages/sentiment.py`:

- Four `ui.highchart(gauge_figure(...))` → two `ui.html(ring_svg(..., uid=...))`.
- The `Today` / `30-Day Avg` / `30-Day` sub-caption labels fold into the ring
  centre and are removed.
- `_apply()` sets `el.content = ring_svg(...)` instead of
  `el.options = ...; el.update()`.
- `bias_lbl`, `sub_lbl`, `regime_badge`, `regime_desc` and both press-and-hold
  popups (Components, Trend Detail) are **untouched**.
- The page keeps its intraday + regime Highcharts, so removing these four does
  **not** disturb the ESM import map. (On a page with no other chart it would.)

## Error handling

Every value passes through `_safe_float` + clamp to [0,100]. A missing arc value
renders track-only with `—` in its centre row. `ring_svg` cannot raise — this
matches the existing contract of `gauge_figure`, which the page relies on today.

A missing `derived.trend_7d` (i.e. before `sentiment_svc` is restarted) degrades
the Week arc to track-only rather than breaking the panel.

## Testing

| Suite | Covers |
|---|---|
| `webgui/tests/test_rings.py` (new) | arc-path math at 0/50/100; per-arc colour ramp; **unique filter ids across two rings on one page**; clamping of `None`/negative/>100/non-numeric; centre-text formatting; no `<style>` emitted |
| `webgui/tests/test_sentiment.py` | `sentiment_avg(snaps, 5)`; three-arc assembly for both panels; missing-trend and missing-`trend_7d` fallbacks |
| `services/sentiment_svc/tests/test_compute.py` | `compute_7d_trend` shape + neutral degradation + TTL cache; update the exact-set assertion on `derive_composite_extras` |
| `webgui/tests/test_no_inline_style.py` | confirm the guard still passes (`ui.html` fragments are documented out-of-scope) |

## Rollout

1. Verify the `ui.html` sanitizer preserves `<filter>` **before** building on it.
2. Implement + test bottom-up: `rings.py` → `sentiment.py` → `compute_7d_trend`.
3. Visual check in **dev** at `:9500` via the preview tools — not tests alone;
   this is a purely visual change and the suite cannot see it.
4. Restart `sentiment_svc` so `trend_7d` is published.
5. Promote with `tools\promote.bat` (never `git pull` in the prod checkout).

## Out of scope

- The options detail-panel speedometer (`pages/options/detail.py`) keeps the
  existing needle gauge. Unifying the two idioms is a separate decision.
- `webgui/pages/gauge.py` is unchanged.
- No new nav, route, cache key, command or contract *file* — `trend_7d` is an
  additive field on an existing payload.
