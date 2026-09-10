# Desk Market Summary + Regime popup — Design

**Date:** 2026-09-10
**Status:** Approved (brainstorm complete) — implementation plan:
[`2026-09-10-desk-market-summary-plan.md`](2026-09-10-desk-market-summary-plan.md)

## What is being built

1. **A popup on the Regime word**, the same pattern already on the Trend, Bias and
   Signal words (see [`2026-09-10-market-trend-flight-words-design.md`](2026-09-10-market-trend-flight-words-design.md)).
2. **A MARKET SUMMARY frame at the bottom of the Desk**: one Claude-written
   sentence consolidating six readings — Sentiment, Trend, Bias, Signal, Regime,
   Bull/Bear — that adapts as the market moves, over a row of six live chips.

## The six readings are four

Sentiment, Bias and Signal are **one number** said three ways: Bias and Signal are
bands of the sentiment composite (`live_composite.signal_band`). Trend and Regime
are two independent direction/character reads; Bull/Bear is breadth. So the
summary consolidates four reads — fear/greed, direction, tape character, breadth —
and its job is to say where they agree and where they conflict.

## Decisions (from the brainstorm)

| Question | Decision |
|---|---|
| How is the text produced? | **Claude-written**, combined with an existing call to save money. |
| Which call? | **`market_svc`'s ticker narrative**, re-enabled and **change-driven**. |
| Posture? | **Read + posture** — the sentence closes with a trading posture. |
| Public live screens? | **Shown there too** (same page module; the ACL already reads `cache:*`). |

**Why the ticker narrative, not the driver decider.** Measured on prod
2026-09-10: ~20 Claude calls per weekday (driver checkpoints every 30 min
08:45–14:30 CT, four gamma briefings), and the ticker narrative **off since
2026-08-29**. The decider would add zero calls but runs only 08:45–14:30 CT,
stops whenever the driver halts or stands down, and would put display text inside
the call that decides trades — where added output fields have starved existing
ones before (see memory: forced tool fields). The gamma briefings run four times a
day, too sparse to adapt intraday. The ticker narrative is a market read by
design; the cost is re-enabling it, made cheap by refreshing on change.

## 1 — The Regime popup

On the Desk's MARKET REGIME tile (updated in place — the popup is swapped only
when its sentence changes) and on `/sentiment`'s regime dial card.

| Word | Popup |
|---|---|
| Balanced | Quiet, two-sided tape: price is sitting at its own average, trend strength is low, and dealers are dampening moves. Neutral premium selling — iron condors — fits best. |
| Trending | Price is moving with persistence, but the two direction reads disagree on which way, so no direction is named. Follow the move once it shows; avoid fading it. |
| Rallying | A steep, persistent move higher, confirmed by both the price slope and the Market Trend score. Follow it; don't sell calls into it. |
| Firming | A steady, gentle climb, confirmed by both the price slope and the Market Trend score. Follow the direction; avoid fading it. |
| Retreating | A steep, persistent move lower, confirmed by both the price slope and the Market Trend score. Follow it; don't sell puts into it. |
| Softening | A steady, gentle decline, confirmed by both the price slope and the Market Trend score. Follow the direction; avoid fading it. |
| Breakout | The range is expanding into new ground. Momentum trades fit; credit spreads against the move are dangerous. |
| Breakdown | The range is expanding to the downside. Momentum favors the downside; put credit spreads here are dangerous. |
| Whipsaw | Plenty of movement, no progress: failed breaks and two-sided wicks. The hardest regime — reduce size or stand aside. |
| Stressed | Fear is driving the tape: elevated VIX, an inverted volatility curve, gaps that don't fill. Premium is rich but the risk is real — defined risk only. |
| Unclear | No regime has enough evidence to name. Wait for one to form. |

Lives in `webgui/pages/regime_mix.py` beside `REGIME_LABELS` / `REGIME_NOTES`,
keyed by the **displayed** word (the service publishes it with its direction
already applied, `regime_view["label"]`). A cross-tier test reads
`scoring/market_regime.py` as text — `REGIME_DISPLAY` values, every
`_DIRECTIONAL` word, and "Unclear" — and fails on any word without a popup.

## 2 — The summary generator (`market_svc`)

**Input packet — the six readings in the screen's own words:**

| Reading | Sent as | Source |
|---|---|---|
| Sentiment | composite score 0–10 | `cache:sentiment:composite` live composite |
| Trend | flight word + trend score 0–100 | `derived.trend.state` → flight word; `smoothed_score` |
| Bias / Signal | the two words + position size | `derived.bias` / `signal` / `size` |
| Regime | displayed word + confidence | `cache:sentiment:regime` `label` / `confidence` |
| Bull / Bear | sector count per quadrant + horizon | `cache:sentiment:bullbear` sector rows |

Bull/Bear counts use the map's own rule — absolute trend × excess vs SPY, ties to
the cautious side — on **today's** axes once the regular session has opened and a
benchmark move exists, the **quarter's** otherwise (the Desk strip's rule,
`desk.strip_is_live`). The quadrant rule is four sign tests, duplicated in
`market_svc` with a docstring naming `bullbear.quadrant` (the service cannot import
Tier 1).

**Dropped from the packet:** index moves, volatility quotes, sector movers. A
sentence quoting "SPX +0.4%" is stale between refreshes, and the ticker's live
items already carry prices. The flight words need a **third copy** in
`market_svc`; the existing cross-tier mirror test is extended to all three.

**Prompt:** the composite is contrarian (high = fear, read as opportunity);
Sentiment/Bias/Signal are one number, say it once; name where the reads agree or
conflict; ≤ 2 sentences, ≤ 350 chars; use the given words verbatim; no prices;
close with a posture.

**Refresh — on change, not on a clock.** Each `market_svc` poll builds the packet
and a **fingerprint** of it at display resolution: words exact, composite to 0.5,
trend score to 5, regime confidence to 10 %, Bull/Bear counts exact + horizon. A
new sentence is written only when:

- the fingerprint differs from the one the last sentence was written from, **and**
- at least **10 min** have passed since the last call, **and**
- fewer than **30** calls have been made today (resets at the CT date change).

The first poll after a restart always writes one. Overnight and at weekends
nothing moves, so nothing is called. Estimate: 8–15 calls on a trading day.

**The ticker toggle goes back to hiding the marquee only.** The Desk needs the
sentence regardless. Removed: `settings.apply_ticker_enabled`'s command,
`main.sync_ticker_setting`, `handlers.summary_enabled` / `set_summary_enabled`,
`cache:market:summary_enabled`, and the `enable_summary` / `disable_summary`
commands. (Removing `sync_ticker_setting` also retires the `live_main.py` warning
that the public process must never call it.)

**Published:** `cache:market:summary` keeps `narrative` and gains **`inputs`** —
the packet the sentence was written from (additive; `MarketSummary.inputs`
defaults to `{}`). The envelope timestamp is the "as of".

**Failure:** no key / dev → empty narrative, never a fabricated line. An API
error or timeout instead publishes **nothing** — the last good sentence stays on
the Desk — and the fingerprint it was launched against is forgotten, so the same
readings are retried once `SUMMARY_MIN_GAP_SEC` has passed rather than a
transient failure freezing a stale sentence until the market moves. The failed
attempt still counts toward the gap and the daily cap — a wedged Claude endpoint
cannot spin faster than the ceiling either.
`stop_reason == "max_tokens"` is logged; `max_tokens` rises 220 → 300 (a cap, not
a spend) with a tripwire test on the floor.

## 3 — The Desk frame

Full width, below the four panels, console style, titled **MARKET SUMMARY**:

```
MARKET SUMMARY                                        as of 10:42 CT
<the sentence>
SENTIMENT 3.98 · TREND Gliding · BIAS Cautious · SIGNAL Bearish ·
REGIME Whipsaw · BULL/BEAR 4 of 11 rising & leading today
[Readings have changed since this was written.]
```

- **Six live chips**, from the views the strip already reads, so they are current
  even while the sentence lags. Trend / Bias / Signal / Regime carry their existing
  popups. New popups: **Sentiment** — "The sentiment composite, 0–10. Contrarian:
  a higher score means more fear, which this model reads as opportunity."
  **Bull/Bear** — the full four-quadrant distribution and its horizon.
- **"Moved since"** — shown when a chip's word or count differs from `inputs`.
  Worded as a fact, not a promise of refresh (the gap or the ceiling may delay one).
- **Empty states** — no sentence: "No summary yet — one is written when the
  readings next change." An unpublished reading shows a dash, never "Neutral".
- **Repaint** — a new `summary` region on the Desk's existing batched poll;
  `market:summary` joins `VIEWS`. Chips and sentence update **in place**; a popup is
  swapped only when its sentence changes (Bull/Bear repaints often, and a rebuild
  would close a popup under the cursor).
- **One wall clock per paint.** The Bull/Bear strip and the summary frame both
  decide the today-vs-quarter horizon from `strip_is_live`, so a paint straddling
  the opening bell must not let one region see "before the bell" and the other
  "after" — `summary_facts` takes the SAME `now` `_paint` hands the strip, not a
  fresh `datetime.now()` of its own. `bullbear_distribution` renders the
  counts/live pair its caller already derived rather than taking its own clock,
  so it cannot mint a second one either.
- The public live Desk renders the frame too.

## 4 — Testing, rollout, docs

**Tests (TDD, per piece):** Regime popup coverage + cross-tier word pin + Desk
tile / dial render; packet (six readings, horizon, no prices); fingerprint
resolution; trigger (first run, change + 10 min, not within 10 min, daily ceiling,
date reset, unchanged → no call); call (`narrative` + `inputs`, cut-off logging,
`max_tokens` floor, empty on dev/no key); toggle no longer gates; contract default;
three-way flight-word mirror; Desk `summary_facts` (chips, tones, popups, moved
since, empty states) + render + region wiring.

**Rollout:** worktree → full suites → push → promote at a chosen time. The first
poll after the restart writes one sentence (one call). Verify on prod:
`cache:market:summary` has `narrative` + `inputs`, both served Desk pages show the
frame, and the daily Claude count moves as expected; re-check the count later in
the day against the 8–15 estimate.

**Docs in the same change:** this design + the plan; CHANGELOG; `webgui-routes.md`
`/desk`; User Guide (frame, Regime popup, ticker line 1513); Reference Guide
(frame, Regime popup, ticker paragraph 3052 — it says turning the ticker off stops
the calls); **Technical Reference** market_svc cadence row (line 1539, "every 40
min" → on change); **API Reference** (`MarketSummary` gains `inputs`;
`SUMMARY_*` constants, `enable_summary` / `disable_summary`,
`cache:market:summary_enabled` removed); `page_help.py` Desk help; the
ticker-toggle memory note.
