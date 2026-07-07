# Five-State Market Classifier (Direction × Aggression) — Design

**Date:** 2026-07-07
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorm complete) — implementation plan to follow.

## Motivation

The app already has a five-state market read
(`sentiment-dashboard/scoring/intraday_trend.py:score_to_state` →
`bull_trend / pullback_in_bull / range / bear_rally / bear`), and that state string
drives `options-scanner/regime_filter.py`, which gates the live scanner's PCS/CCS
side selection. But that model is **one directional axis banded into five** — it can
say *which way* the market leans, not *why*.

The trader framework we are implementing is a **two-axis model: direction ×
aggression**. Its five states —

1. **Bullish** — aggressive, motivated buying absorbing supply.
2. **Lack of Bullishness** — exhaustion: price at highs but no oomph behind it.
3. **Neutral** — balance; a truce between buyers and sellers.
4. **Lack of Bearishness** — resilience: refuses to drop, sellers absent, puts undefended.
5. **Bearish** — aggressive, urgent selling / liquidation.

— are defined by **effort-vs-result asymmetry**: what volume, wicks, skew, options
flow, and order flow are doing *relative to* price. The two middle states cannot be
expressed on a single directional axis: "Lack of Bullishness" (up but hollow) and
"Neutral" (balanced) collapse together under a one-axis banding. The app computes
almost none of that second (aggression) axis today.

## Scope

All eight items across Tiers 1–2 of the roadmap, built in phases:

**Tier 1 (aggression axis, on data already flowing):**
1. Volume-effort module.
2. Five-state classifier (direction × aggression grid).
3. Real IV skew (25-delta risk-reversal) from our own chains.
4. Options-flow direction (sector P/C Δ + index call/put volume trend).

**Tier 2 (intraday structure + measured order flow):**
5. Streamer order flow — **equity aggressor + option aggressor** (full streaming, both).
6. Session-structure score (% of session above VWAP + opening-range hold/break).
7. Rejection/defense detection + **state-transition push alerts**.
8. Volume-profile shape as a Neutral detector.

**Out of scope (Tier 3, separate design):** state → strategy-family bias in the Swing
Scanner (item 9), feeding the state into the Driver (item 10), and the formal
IC/backtest validation pass (item 11). We **record** the daily state from day one so
item 11 can run later, but we do not build the validation harness here.

## Key design decisions (from brainstorming)

1. **Scope:** everything in Tiers 1–2 (all 8 items), phased.
2. **Classifier fit:** **replace** the existing trend state — retire `score_to_state`'s
   banding and rekey `regime_filter` to the new vocabulary. Clean single source of
   truth. Deserves validation (hence record-from-day-one).
3. **Streamer depth:** level-one aggressor **+ full streaming option flow** (no order
   books).
4. **Push channels:** lift `push_notify.py`'s Telegram/Discord/Fi-SMS senders + the
   `shared/notifications.json` config into a thin `shared/` helper, used by both the
   existing options signal-push and the new state-transition alerts.

## Approach chosen (classifier)

**Two-axis grid (direction score × aggression score → rule-table lookup).** Compute a
*direction* score (reuse the existing 0–100 intraday trend score, unchanged) and a
separate signed *net-aggression* score, then map the `(direction_band,
aggression_sign)` pair to one of the five states via a small, tunable rule table.
Interpretable, each axis independently inspectable as evidence, and it maps cleanly to
the regime votes.

Rejected: per-state weighted-evidence argmax (harder to reason about, harder to map to
votes, overkill for five states); single conviction-adjusted scalar → banding (collapses
the two-axis distinction — the whole point).

## Architecture

### Section A — Classifier core

- **New pure module** `sentiment-dashboard/scoring/market_state.py` (no I/O, no tk —
  same discipline as `intraday_trend.py`). Exposes
  `classify_market_state(direction_score, aggression) -> MarketState`, where
  `MarketState` carries `state`, `label`, `description`, and `evidence` (per-dimension
  lines, e.g. "price at highs · up-volume −38% vs 20d · call IV draining").
- **Direction axis:** the existing 0–100 `blend_trend` score, untouched. Banded
  bullish / neutral / bearish (≈ ≥60 / 40–60 / ≤40 — tunable module constants).
- **Aggression axis:** a new signed score in ~[−1, +1] from the Tier-1 inputs. Positive
  = motivated buying absorbing supply; negative = urgent selling / protection-buying.
- **Grid → five states:**

  | direction \ aggression | strong + | weak/absent | strong − |
  |---|---|---|---|
  | **bullish** | Bullish | Lack of Bullishness | Lack of Bullishness |
  | **neutral** | Lack of Bearishness | Neutral | Lack of Bullishness |
  | **bearish** | Lack of Bearishness | Lack of Bearishness | Bearish |

- **State vocabulary (new):** `bullish`, `lack_of_bullishness`, `neutral`,
  `lack_of_bearishness`, `bearish`. `STATE_LABELS` / `STATE_DESCRIPTIONS` in
  `market_state.py`.

### Replace mechanics

The bridge already keys the regime on a **string** under `trend_regime.state`, and
`regime_filter._TREND_STATE_VOTE` maps that string to a vote. So "replace" is:

1. `sentiment_svc` publishes the new state string under the **same**
   `trend_regime.state` bridge key.
2. Rekey `regime_filter._TREND_STATE_VOTE` to the new vocabulary:

   | state | vote | rationale |
   |---|---|---|
   | `bullish` | `bull` (hard) | block CCS |
   | `lack_of_bullishness` | `lean_bear` | exhaustion at highs → favor CCS |
   | `neutral` | `None` | both sides allowed |
   | `lack_of_bearishness` | `lean_bull` | resilient, puts cheap/undefended → favor PCS |
   | `bearish` | `bear` (hard) | block PCS |

3. The two middle states land exactly on the soft-lean slots the filter already models
   (`pullback_in_bull` / `bear_rally`), so `evaluate_regime`'s AND-of-agreement logic
   needs **zero structural change** — only the dict keys and `STATE_LABELS` /
   `STATE_DESCRIPTIONS`.
4. **Hysteresis:** reuse `commit_state` (2-read flip) unchanged, so a single noisy
   aggression read can't whipsaw the scanner gate.

`score_to_state` (the old banding) is retired; the 0–100 `blend_trend` direction score
is **kept** as the direction axis input.

### Section B — Tier-1 aggression inputs (compute where the data lives)

- **Item 1 — Volume-effort → `sentiment_svc`** (new pure `scoring/effort.py`, over the
  SPY daily OHLCV the trend model already fetches). Signed sub-signals: up-day vs
  down-day volume ratio (20-day), volume-on-rallies vs volume-on-pullbacks, and
  close-location-value (where closes sit in their bar range — the honest no-tape proxy
  for "buying at the ask"). Defining evidence for Lack-of-Bullishness (no oomph) vs
  Lack-of-Bearishness (no panic).
- **Item 3 — 25-delta skew → `options_svc`** (in the existing 2-min GEX poll — the
  collector already holds the $SPX/SPY/QQQ chains). Compute the 25-delta risk-reversal
  (put IV − call IV) and store it as an additive scalar per snapshot in
  `gex_history_db` (skew-*change* needs history, which the GEX DB already provides).
  Publish `cache:options:flow_skew`. Powers call-skew-rising (Bullish),
  put-skew-flattening (Lack-of-Bearishness), put-IV-spiking (Bearish); also finally
  feeds `put_call.score`'s dormant `options_skew` param with a computed value.
- **Item 4 — Flow direction → split:** sector-P/C 5-day Δ in `sentiment_svc` (retain a
  short history of the P/C level it already computes); index call-vs-put **volume**
  trend rides with skew in `options_svc` (same chains, into `cache:options:flow_skew`).
- **The blend:** `sentiment_svc` owns the classifier and combines these into the signed
  net-aggression scalar via the same confidence-weighted idiom as `blend_trend`. A
  missing dimension (e.g. `options_svc` momentarily down → no skew) drops that
  dimension's confidence and the classifier still runs on volume-effort + direction.
  **Soft coupling, graceful degradation** — consistent with the house defensive style;
  `sentiment_svc` reads `options_svc`'s published caches the same way the webgui and
  driver already read cross-service caches.

### Section C — Tier-2 structure signals (existing REST data; sharpen specific cells)

- **Item 6 — Session structure → `sentiment_svc`** (15-min trend refresh, over the SPY
  intraday 5/15-min bars already fetched). % of session held above VWAP +
  opening-range hold/break. Feeds the **direction axis** — `score_price` only *samples*
  VWAP at a point, but the framework's tell is *holding* above VWAP/OR (Bullish) vs
  *pinned below* (Bearish). Enters as a session-structure sub-signal in the price
  component.
- **Item 7 — Rejection/defense detection → `sentiment_svc`** (pure candle functions).
  Upper-wick clusters near range highs → exhaustion (aggression negative →
  Lack-of-Bullishness); shallow pullback depth + fast recovery at support → resilience
  (aggression positive → Lack-of-Bearishness). Feeds the **aggression axis**.
  - **State-transition push alerts:** the classifier + hysteresis live in
    `sentiment_svc`, so a *committed* state flip ("Bullish → Lack of Bullishness")
    fires the push from there, through the new shared push helper (below).
- **Item 8 — Volume-profile shape → `sentiment_svc`** (over intraday bars, reusing
  `technical.volume_profile`'s POC + contiguous value area). Classify the session as
  bell-curve balance (single dominant HVN, price reverting to POC/VWAP) vs
  trend/double-distribution. **Sharpens the Neutral cell**: strong balance + falling IV
  rank = the highest-conviction "sell premium / iron condor" regime.

### Section C.1 — Shared push helper

Lift `services/options_svc/push_notify.py`'s Telegram/Discord/Fi-SMS channel senders +
the market-hours/holiday gate + `shared/notifications.json` config resolution into a
thin `shared/notify/` helper. Both the existing options signal-push **and** the new
market-state-transition push use one code path and one config (Fi-SMS available for
state alerts too). `push_notify.py` becomes a thin wrapper over the shared senders. This
is the only structural change outside the two services.

### Section D — Tier-2 streamer (item 5), both equity and option flow

**Finding:** the existing equity SSE normalizer (`_normalize_level1_equity`) forwards
only `last` + `net_change` — it drops bid/ask/size, so aggressor classification is
impossible without a proxy change. The option stream has the same gap plus a bigger
one: option subscriptions are per-tracked-trade only and `_on_option_message` caches
bid/ask/iv but not last/size.

**Equity aggressor flow (the solid core):**
- **Proxy (additive):** widen `_L1_EQUITY_FIELDS` / `_normalize_level1_equity` to also
  carry `bid`(1), `ask`(2), `bid_size`(4), `ask_size`(5), `last_size`(9),
  `total_volume`(8). Existing SSE consumers (portfolio_svc) ignore the extra keys — no
  regression. Rides the existing shared stream worker (no second Schwab session).
- **`sentiment_svc` consumer:** background SSE consumer on
  `/stream/quotes?symbols=SPY,QQQ` (the portfolio_svc pattern). Per tick, classify
  `last` vs prevailing bid/ask (quote rule: `last≥ask`→buy, `last≤bid`→sell,
  between→tick-test), accumulate rolling 1–5 min windows → aggressor ratio + cumulative
  volume delta (CVD) + CVD slope. Publish `cache:sentiment:order_flow`; feed the
  aggression axis with a labeled confidence.
- **Honest caveat (in-design):** level-one conflates rapid ticks, so this is a
  *sampled* read — reliable over minute windows, not footprint-exact. `$SPX` has no
  tape → SPY stands in.

**Option aggressor flow (mirrors the equity path):**
- **Proxy:** widen the option normalizer to capture `last` + `last_size` (it already
  has bid/ask/iv); add an **option-SSE fan-out** with a refcounted OSI union, mirroring
  the equity fan-out (`/stream/options?symbols=<OSI,…>`). The subscription union becomes
  tracked-trade legs ∪ flow OSIs (refcounted, like equities).
- **`sentiment_svc` consumer:** pick near-ATM SPY/QQQ OSIs from the chains already in
  hand (refreshed as spot moves), subscribe via `/stream/options`, classify each option
  tick at-bid/at-ask, tag put vs call from the OSI, and aggregate put/call aggressor
  pressure into `cache:sentiment:order_flow`.
- Heaviest phase → sequenced last.

### Order flow the streamer genuinely closes vs. what stays open

- **Closes (measured, not proxied):** aggressive buying-at-ask / selling-at-bid
  direction (equity CVD); option protection-buying (puts lifted at ask) vs undefended
  puts (traded at bid) vs calls drying up.
- **Stays open:** tick-perfect footprint/CVD (level-one conflates), $SPX tape/book
  (SPY proxy), futures depth, consolidated depth, and market-profile TPO tail detail
  (approximated from minute bars). No order books in this scope.

## Data flow

```
options_svc GEX 2-min poll ── 25Δ skew + index call/put vol ──▶ gex_history_db
                                                              └─▶ cache:options:flow_skew
proxy shared stream worker ── L1 equity (widened) ───▶ /stream/quotes SSE
                          └── L1 option  (widened) ───▶ /stream/options SSE (new)
                                    │                         │
sentiment_svc ◀── SPY daily/intraday (REST) ── effort, session-structure,
              │                                  rejection/defense, profile shape
              ◀── /stream/quotes  ── equity aggressor CVD ─┐
              ◀── /stream/options ── option put/call flow ─┤
              ◀── cache:options:flow_skew ─────────────────┤
                                                            ▼
                          net-aggression score (confidence-weighted blend)
                                    +
                          direction score (existing blend_trend, unchanged)
                                    ▼
                          classify_market_state → MarketState
                                    ▼
                          commit_state (2-read hysteresis)
                                    ├─▶ bridge trend_regime.state (same key) ─▶ regime_filter (rekeyed votes)
                                    ├─▶ cache:sentiment:composite (additive market_state fields) ─▶ /sentiment chip
                                    ├─▶ state-transition push (shared notify helper)
                                    └─▶ market_state_history.db (record for item 11)
```

## Contracts (additive)

- `cache:sentiment:composite` — additive `market_state` fields (state / label /
  evidence), same pattern as the trend-redesign additive fields.
- `cache:sentiment:order_flow` — new `OrderFlow` contract (equity aggressor
  ratio/CVD/slope + option put/call aggressor pressure, per symbol, with confidence).
- `cache:options:flow_skew` — new additive view (25-delta risk-reversal + index
  call/put volume trend per index).
- Bridge `trend_regime.state` — **same key**, new state vocabulary (the only
  non-additive change; `regime_filter` rekeyed in lockstep).

## Recording for validation (item 11 groundwork)

Persist the daily *committed* state + the aggression sub-scores into a small SQLite
store (`market_state_history.db`, mirroring `intraday_history_db`), so the existing
IC/backtest harness can validate whether the five states stratify forward returns —
without waiting. Because "replace" changes live-scanner gating, this is cheap insurance,
not optional.

## Error handling / defensiveness

- Every scoring module is pure and defensive: missing input → that dimension's
  confidence drops to 0; the classifier still runs on whatever remains (worst case:
  direction axis only, aggression neutral).
- `sentiment_svc` reading `options_svc`'s caches is soft coupling — `options_svc` down
  → skew/flow confidence 0, classifier degrades, no crash.
- Proxy stream changes are additive; SSE consumers tolerate reconnect (existing
  pattern). Streamer down → order-flow confidence 0, REST signals carry the aggression
  axis.
- `regime_filter` keeps its AND-of-agreement + per-symbol re-enable safety; only the
  vote dict keys change.

## Testing

- Pure modules TDD'd per unit: `effort`, `market_state` grid, aggressor classifier
  (quote rule), session-structure, rejection/defense, profile-shape, skew/RR.
- `regime_filter` vote-map test **rekeyed** to the new vocabulary; AND-of-agreement /
  per-symbol / hysteresis tests preserved.
- Proxy: normalizer-widen + option SSE fan-out unit-tested; live-verified against a
  running stream sample (the `TODO(live)` field-population caveat applies — confirm on
  a real payload).
- End-to-end: Redis-driven check that a published `market_state` reaches the scanner
  gate with the correct side blocked.

## Phasing (each phase ships green and is independently validatable)

0. **Shared push helper** — lift channels from `push_notify.py` into `shared/notify/`.
1. **Tier-1 aggression inputs on REST data** — effort (`sentiment_svc`), skew +
   index-vol (`options_svc` GEX poll → `cache:options:flow_skew`), sector-P/C Δ. Publish
   sub-scores; no classifier yet.
2. **Classifier core + replace + page + recording** — `market_state.py`, aggression
   blend, rekey `regime_filter`, `/sentiment` state chip, `market_state_history.db`.
   **← first user-visible ship: the five states go live on REST data.**
3. **Structure signals** — session structure, rejection/defense + transition push,
   profile shape.
4. **Streamer equity aggressor flow** — proxy normalizer widen + `sentiment_svc` SSE
   consumer + CVD into the aggression axis.
5. **Streamer option aggressor flow** — option normalizer widen + option SSE fan-out +
   near-ATM subscription + put/call classifier.

Phase 2 puts a working five-state classifier in front of the user before the heavy
streamer work; Phases 4–5 enrich the aggression axis rather than gating the feature.

## Files (new / changed)

**New:**
- `sentiment-dashboard/scoring/market_state.py` — the grid classifier (pure).
- `sentiment-dashboard/scoring/effort.py` — volume-effort (pure).
- `shared/notify/` — lifted push channels + config + gate.
- `services/sentiment_svc/order_flow.py` (or similar) — SSE consumers + aggressor
  classifier + CVD/put-call aggregation.
- `services/sentiment_svc/market_state_history_db.py` — recording store.
- `shared/contracts/` — `OrderFlow`; additive `market_state` on the composite;
  `flow_skew` view.

**Changed:**
- `options-scanner/regime_filter.py` — rekey `_TREND_STATE_VOTE` + `STATE_LABELS`.
- `sentiment-dashboard/scoring/intraday_trend.py` — retire `score_to_state`; keep
  `blend_trend` as the direction axis.
- `services/sentiment_svc/compute.py` / `handlers.py` — aggression blend, classifier
  wiring, session-structure/rejection/profile, order-flow consumers, transition push,
  recording.
- `services/options_svc/` (GEX poll path) — 25Δ skew + index call/put volume;
  `gex_history_db` additive scalar; `cache:options:flow_skew` publish.
- `services/options_svc/push_notify.py` — thin wrapper over `shared/notify/`.
- `schwab-proxy/schwab_proxy.py` — widen equity normalizer; option normalizer for
  last+size; option SSE fan-out + refcounted OSI union + `/stream/options`.
- `webgui/pages/sentiment.py` — state chip vocabulary + evidence + order-flow readout.

## Related memories / cross-refs

- Trend-redesign additive-contract discipline (`docs/plans/2026-06-19-intraday-market-trend-redesign-*`).
- Push notifications (`docs/plans/2026-07-05-signal-push-notifications-*`) — the channels being lifted to shared.
- Validated-swing-model house standard: label honestly, record, validate OOS, state caveats.
