# Market Trend flight words — Design

**Date:** 2026-09-10
**Status:** Shipped (display only)

## Problem

The Market Trend pill (the `/sentiment` console and the Desk) named the Day
horizon's five-state reading with Bull / Weak Bull / Neutral / Resilient / Bear.
"Resilient" did not reflect the trend. `lack_of_bearishness` comes out of three
cells of `market_state._GRID`:

| Trend score | Aggression | State |
|---|---|---|
| ≤ 40 (down) | weak / absent | `lack_of_bearishness` |
| ≤ 40 (down) | buyers aggressive | `lack_of_bearishness` |
| 40–60 (flat) | buyers aggressive | `lack_of_bearishness` |

Two of the three sit in the bearish band, so the pill could read RESILIENT beside
a trend ring at 35. The word named the model's conclusion (lean long), not the
tape. It also broke symmetry with its mirror, "Weak Bull", which names a direction
plus a qualifier.

## Constraints

- **Reflect the trend.** The word must agree with the direction the ring shows.
- **Two axes.** The classifier crosses direction (result) with aggression
  (effort). A good word carries both.
- **Width.** The Desk pill is uppercase in a compact card; RESILIENT (9) was the
  widest word. All five new words are ≤ 8.
- **No collisions** with words already on the same screens: the regime words
  (Balanced, Rallying, Firming, Retreating, Softening, Breakdown, Whipsaw,
  Stressed), Bias (Long / Neutral / Cautious / Short) and Signal (Strong Bull …
  Strong Bear).
- **Display only.** The keys are a contract (`regime_filter`, the bridge,
  `market_state_history_db`, the driver packet). Same approach as the 2026-08-14
  regime rename.
- **Colour is not the word's job.** Both pills take their colour from the trend
  score (`console_cards.hero_parts`), so no word-keyed colouring had to move.

## Options considered

| Set | For | Against |
|---|---|---|
| Weak Bear (mirror of Weak Bull) | Minimal; symmetric | Names direction only, not the effort mismatch |
| Sea: Surging / Cresting / Slack / Ebbing / Undertow | Cresting and Slack are excellent | "Ebbing" reads as the market draining away; it loses "no follow-through" |
| Battlefield: Advancing / Overreached / Truce / Dug In / Routed | "Truce" is the design doc's own word for Neutral | "Dug In" names defence, not direction — the flaw Resilient had |
| **Flight: Climbing / Stalling / Circling / Gliding / Diving** | Each word carries both axes: up / level / down × engine on / off | "Gliding" fits its flat-with-buyers cell loosely |

**Chosen: flight.** Climbing vs Stalling and Diving vs Gliding are the same
contrast, engine on or off, which is exactly the effort-vs-result distinction the
classifier exists to draw.

## The hover

Each word carries its picture as a tooltip (`sentiment.TREND_PICTURE`,
`trend_picture(state)`):

| State | Word | Hover |
|---|---|---|
| `bullish` | Climbing | Engine on, gaining height: buyers are pushing and price is rising. |
| `lack_of_bullishness` | Stalling | Nose still up but losing lift: price is high and the buying has run out. A stall comes before a drop — favor call credit spreads, trim longs. |
| `neutral` | Circling | Holding pattern, waiting for clearance: buyers and sellers are balanced. |
| `lack_of_bearishness` | Gliding | Coming down with the engine off: lower, but nobody is pushing it. A glide ends on a runway — a floor is forming, favor put credit spreads. |
| `bearish` | Diving | Nose down under power: urgent selling. |

One helper, `console_cards.pill_tooltip`, hangs it on both the console pill and
the Desk's compact pill, so the two screens cannot word one pill differently. The
30-day structural words (BULL / PULLBACK / RANGE / BEAR RALLY / BEAR) are a
different vocabulary and carry no picture.

## Where the words live

- `webgui/pages/sentiment.py` — `_TREND_SHORT` (source), `TREND_PICTURE`,
  `trend_picture`. The Desk imports both rather than restating them.
- `services/options_svc/market_snapshot.py` — `_TREND_SHORT` mirror for the phone
  snapshot (an image, so no hover). Pinned by
  `shared/tests/test_cross_tier_mirrors.py`.

## Known limits

- **Flat-market cells.** Gliding also shows on a flat tape with buyers stepping in,
  and Stalling on a flat tape with sellers pressing. Making the word agree with the
  ring in every cell would mean deriving it from (band × aggression) rather than
  from the committed state, and the state is hysteresis-committed, so it can lag
  the band by a read anyway.
- **The verdict line keeps the framework name.** The console's verdict block
  prints `market_state.STATE_LABELS` (e.g. "Lack of Bearishness") under the flight
  word, deliberately: the pill gives the picture, the verdict gives the term.
- **The repaint may close an open hover.** The console rebuilds its cards every
  120 s; the tooltip is a plain hover, and the next hover reopens it.
