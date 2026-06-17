# Dual speedometers (today + 30d) for Sentiment & Trend — design

**Date:** 2026-06-15
**Status:** approved
**Page:** `/sentiment` (3-tier: webgui renders; `services/sentiment_svc` computes)

## Goal

Two speedometers for **Market Sentiment** (Today + Past-30-Days) and two for
**Market Trend** (Today + ~30-sessions-ago), to compare the current read against
its recent baseline at a glance.

## Decisions (confirmed)

- Sentiment "past 30 days" gauge = **30-day average composite** (mean of the
  `sentiment:history` snaps).
- Market Trend → **0–10 trend score** map: `bear_trend 1, bear_rally 3, range 5,
  pullback_in_bull 6.5, bull_trend 9`. "Today" = current regime; "30 days" = the
  committed regime ~30 sessions back.

## Architecture (3-tier — important)

The webgui Sentiment page is a **thin renderer**: it reads
`cache:sentiment:composite` (incl. a `derived` block), `cache:sentiment:history`
(`snaps`, `spy`), `cache:sentiment:sectors` off the bus and only formats them. It
**cannot import the scoring engine**. All scoring lives in
`services/sentiment_svc/compute.py`.

Therefore the four gauge values are computed in **Tier-2 (the service)** and the
page just renders them:

| Gauge | Value source (in `derived`) | Computed where |
|---|---|---|
| Sentiment · Today | existing `live` composite total | already published |
| Sentiment · 30-Day Avg | `derived["composite_30d_avg"]` | service (mean of snaps) |
| Trend · Today | `derived["trend"]["score"]` (+ label/state) | service (new `score`) |
| Trend · ~30d Ago | `derived["trend_30d_ago"]` ({state,label,score}) | service (`build_trend_dict(spy[:-30])`) |

The page can't classify SPY 30 bars back (no scoring engine) — that's why the
30d-ago regime must come from the service.

## Tier-2 changes — `services/sentiment_svc/compute.py`

- New pure `trend_score(state)` → the 0–10 map above (default 5.0 for unknown/None).
- `build_trend_dict(spy)`: add `"score": trend_score(committed)` to its return.
- `derive_composite_extras(live, snaps, spy)`: add
  - `"composite_30d_avg": round(mean(composite_series(snaps)[1]), 2)` (0.0 if empty),
  - `"trend_30d_ago": build_trend_dict(spy[:-30])` when `len(spy) > 30 + MIN_BARS_PARTIAL`,
    else `build_trend_dict(spy)` (degrade to current); already carries `score` via
    the `build_trend_dict` change.
- All defensive (the function already guards sub-failures → safe defaults).
- `derived` is a loose dict (not a strict contract field) — additive, no contract
  bump. (Verify `shared/contracts` doesn't pin `derived`'s shape; it doesn't today.)

## Tier-1 changes — `webgui/pages/sentiment.py`

In `_apply` (left column), replace the single gauge + inline trend block with a
**2×2 grid of `speedometer_svg`** gauges:

```
[ Sentiment · Today ]    [ Sentiment · 30-Day Avg ]
[ Market Trend · Today ] [ Market Trend · ~30d Ago ]
```
- Today sentiment: `gauge_score(total)` (unchanged value).
- 30d-avg sentiment: `gauge_score(derived["composite_30d_avg"])`.
- Trend today/30d: `derived["trend"]["score"]*10` / `derived["trend_30d_ago"]["score"]*10`;
  grade = the regime label, colored by regime (green bull / cyan improving /
  yellow range / red bear).
- Keep the "{total} · {bias}" + "size/conf" line under the sentiment pair and the
  trend description/detail under the trend pair (today's regime).
- Pure rendering only — reads `state["derived"]`; no scoring import. A small page
  helper `_gauge_band_label(score)` may format the sentiment grade (or reuse the
  existing bias from `derived`).

## Data flow

No new fetches. `snaps`/`spy` already flow to the service's `derive_composite_extras`
inputs; the page reads everything from the bus on the existing change-version
repaint. Works with the existing scheduler/refresh.

## Testing

- **Tier-2** (`services/sentiment_svc/tests`): unit-test `trend_score` (the 5 states
  + default); assert `derive_composite_extras` includes `composite_30d_avg`,
  `trend.score`, and `trend_30d_ago` (with a short-`spy` degrade case).
- **Tier-1**: the page render is import/smoke-verified; if a tiny pure formatter is
  added, unit-test it. Browser screenshot of the 2×2 layout when the port frees.

## Out of scope
- Changing the composite/regime computation itself (presentation + two derived
  scalars only).
- A 30-day trend *history* chart (only the two gauges are requested).
