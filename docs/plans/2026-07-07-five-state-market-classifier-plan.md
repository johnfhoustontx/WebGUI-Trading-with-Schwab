# Five-State Market Classifier Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the app's one-axis trend state with a two-axis (direction × aggression) five-state market classifier — Bullish / Lack of Bullishness / Neutral / Lack of Bearishness / Bearish — fed by volume-effort, IV skew, options flow, intraday structure, and streamer order flow.

**Architecture:** A new pure grid classifier (`scoring/market_state.py`) crosses the existing 0–100 directional trend score with a new signed net-aggression score, then maps `(direction_band, aggression_sign)` to one of five states. `sentiment_svc` owns the classifier and publishes the new state under the same `trend_regime.state` bridge key; `regime_filter` is rekeyed to the new vocabulary (its AND-of-agreement logic is unchanged). Aggression inputs compute where the data already lives — volume-effort/structure in `sentiment_svc`, 25-delta skew + index call/put volume in `options_svc`'s 2-min GEX poll, and streamer order flow via the proxy's existing shared stream worker. Design doc: `docs/plans/2026-07-07-five-state-market-classifier-design.md`.

**Tech Stack:** Python 3.11, pytest (TDD), Redis/Memurai (`shared.bus`), `shared.contracts`, FastAPI proxy + schwab-py StreamClient, NiceGUI webgui.

**Conventions (read before starting):**
- Run each service's tests from the repo root, one service at a time (never `pytest services` — the `config`/`scoring`/`src` module-name collision). Sentiment-dashboard pure modules run from inside `sentiment-dashboard/`.
- Pure scoring modules: no I/O, no tk, confidence in `[0.0, 1.0]`, missing input → confidence 0. Mirror the `intraday_trend.py` idiom.
- Bridge fields are additive-only EXCEPT the `trend_regime.state` **vocabulary** (the one coordinated non-additive change — `regime_filter` rekeyed in lockstep).
- Commit after every green step with conventional prefixes.
- Branch: `Using_Highcharts` (commit directly; do not open a PR unless asked).

---

## Phase 0 — Shared push helper

Lift the proven Telegram/Discord/Fi-SMS senders + gate + config out of
`services/options_svc/push_notify.py` into `shared/notify/`, so the new state-transition
alerts (Phase 3) reuse one code path and one config.

### Task 0.1: Create the shared notify package skeleton

**Files:**
- Create: `shared/notify/__init__.py`
- Create: `shared/notify/channels.py`
- Test: `shared/notify/tests/test_channels.py`

**Step 1: Write failing tests** — port the existing `push_notify` channel-formatter and
gate tests (Telegram HTML, Discord embed, Fi-SMS email framing, market-hours/holiday
gate, config-presence self-gating → no-op). Copy the assertions from
`services/options_svc/tests/test_push_notify.py`, retargeting the import to
`shared.notify.channels`.

**Step 2: Run** `cd "D:/WebGUI Trading with Schwab" && .venv/Scripts/python -m pytest shared/notify/tests -q` → FAIL (module missing).

**Step 3: Implement** — move the channel senders (`send_telegram`, `send_discord`,
`send_sms`), the market-hours/holiday gate (`_within_market_hours`, `_HOLIDAYS`), and the
`shared/notifications.json` + env-var config resolver from `push_notify.py` into
`shared/notify/channels.py`. Keep signatures identical. Export from `__init__.py`.

**Step 4: Run** the tests → PASS.

**Step 5: Commit** `feat(notify): lift push channels into shared/notify helper`.

### Task 0.2: Reduce push_notify.py to a thin wrapper

**Files:**
- Modify: `services/options_svc/push_notify.py`
- Test: `services/options_svc/tests/test_push_notify.py` (unchanged — must stay green)

**Step 1:** Replace the channel/gate/config bodies in `push_notify.py` with imports from
`shared.notify.channels` (keep `push_notify`'s public functions — the signal-diff/seen-set
logic stays here; only the *channels* are delegated).

**Step 2: Run** `.venv/Scripts/python -m pytest services/options_svc/tests/test_push_notify.py -q` → PASS unchanged (proves behavior identical).

**Step 3: Run** the full options_svc suite → green.

**Step 4: Commit** `refactor(options): push_notify delegates channels to shared/notify`.

---

## Phase 1 — Tier-1 aggression inputs on REST data

Produce the sub-scores that feed the aggression axis. No classifier yet — publish the
pieces and prove them.

### Task 1.1: Volume-effort pure module

**Files:**
- Create: `sentiment-dashboard/scoring/effort.py`
- Test: `sentiment-dashboard/tests/test_effort.py`

**Step 1: Write failing tests.** `score_effort(daily_ohlcv) -> EffortResult` where
`daily_ohlcv` is a list of dicts `{open, high, low, close, volume}` (oldest first).
Returns a signed `score` in `[-1, 1]` (positive = motivated buying) + `confidence` +
`components` dict. Cases:

```python
from scoring.effort import score_effort

def test_up_volume_dominant_is_positive():
    # 20 bars: up-days carry ~2x the volume of down-days -> strongly positive
    bars = _bars(updown_vol_ratio=2.0)
    r = score_effort(bars)
    assert r.score > 0.4 and r.confidence > 0.5

def test_rally_on_shrinking_volume_is_negative():
    # price grinds up but volume contracts on up-days -> "lack of oomph" -> negative
    bars = _bars(up_trend=True, up_vol_shrinking=True)
    assert score_effort(bars).score < 0

def test_downday_volume_dries_up_is_positive():
    # price dips but down-days have no volume -> "no panic" -> positive (resilience)
    bars = _bars(down_drift=True, down_vol_dry=True)
    assert score_effort(bars).score > 0

def test_close_location_value():
    # closes near the HIGH of each bar -> buyers in control -> positive component
    assert score_effort(_bars(clv=+0.9)).components["clv"] > 0

def test_insufficient_bars_zero_confidence():
    assert score_effort(_bars(n=3)).confidence == 0.0
```

**Step 2: Run** `cd sentiment-dashboard && ../.venv/Scripts/python -m pytest tests/test_effort.py -q` → FAIL.

**Step 3: Implement** `score_effort`: (a) up/down-day volume ratio over the last 20 bars
→ signed; (b) volume-on-up-moves vs volume-on-down-moves trend → signed; (c)
close-location-value = mean of `(close-low)/(high-low)*2-1` → signed. Confidence scales
with bar count (0 below ~10). Blend the three signed components (equal weight) → `score`.
Pure, defensive (missing/zero-range bars skipped).

**Step 4: Run** → PASS.

**Step 5: Commit** `feat(sentiment): volume-effort scoring module`.

### Task 1.2: 25-delta risk-reversal skew (pure)

**Files:**
- Create: `options-scanner/flow_skew.py` (pure — no `services/` import; mirrors the `commissions.py` PURE-engine discipline)
- Test: `options-scanner/tests/test_flow_skew.py`

**Step 1: Write failing tests.** `risk_reversal_25d(chain) -> {put_iv, call_iv, rr}` where
`chain` is a normalized option chain (the shape `gex_collector.poll_once` already parses).
`rr = put_iv_25d - call_iv_25d` (positive = downside fear). Also
`index_call_put_volume(chain) -> {call_vol, put_vol, ratio}`. Cases: exact-delta strike,
nearest-delta fallback, missing IV → None, empty chain → None.

**Step 2: Run** `cd options-scanner && ../.venv/Scripts/python -m pytest tests/test_flow_skew.py -q` → FAIL.

**Step 3: Implement** — find the nearest-to-0.25-delta put and 0.25-delta call (abs delta),
read their IVs, subtract. Volume aggregation sums call vs put `totalVolume`. Defensive.

**Step 4: Run** → PASS.

**Step 5: Commit** `feat(options): 25-delta risk-reversal + call/put volume (pure)`.

### Task 1.3: Store skew per GEX snapshot

**Files:**
- Modify: `options-scanner/gex_history_db.py` (additive columns `rr_25d`, `call_vol`, `put_vol` on the snapshot table; idempotent `init_schema` ALTER-if-missing)
- Test: `options-scanner/tests/test_gex_history_db.py`

**Step 1:** Test that `insert_snapshot(..., rr_25d=..., call_vol=..., put_vol=...)`
round-trips and that a pre-existing DB without the columns is migrated on `init_schema`
(ALTER TABLE ADD COLUMN guarded by a PRAGMA table_info check).

**Step 2: Run** → FAIL.

**Step 3: Implement** additive columns (default NULL) + the guarded ALTER.

**Step 4: Run** → PASS.

**Step 5: Commit** `feat(options): persist skew scalars in gex_history_db`.

### Task 1.4: Wire skew into the options_svc GEX poll + publish

**Files:**
- Modify: `services/options_svc/compute.py` (`collect_gex_snapshots` — compute RR + call/put vol per index from the chain already fetched, pass to `insert_snapshot`)
- Modify: `services/options_svc/handlers.py` (publish `cache:options:flow_skew` after the collect)
- Create: `shared/contracts/` — additive `flow_skew` view validator
- Test: `services/options_svc/tests/test_compute.py`, `test_handlers.py`

**Step 1:** Tests — `collect_gex_snapshots` calls `flow_skew.risk_reversal_25d` for
$SPX/SPY/QQQ and stores the scalars; a handler publishes
`cache:options:flow_skew = {symbol: {rr_25d, rr_delta, call_vol, put_vol, ts}}` (rr_delta
vs the prior stored snapshot). Lazy-import `flow_skew` (the cross-app `scoring`-collision
discipline).

**Step 2–4:** Red → implement (reuse the chain already in `poll_once`; no extra fetch) →
green.

**Step 5: Commit** `feat(options): publish cache:options:flow_skew from GEX poll`.

### Task 1.5: Sector-P/C 5-day Δ in sentiment_svc

**Files:**
- Modify: `services/sentiment_svc/compute.py` (retain a short rolling history of the sector cap-weighted P/C; expose `sector_pc_delta`)
- Test: `services/sentiment_svc/tests/test_compute.py`

**Step 1:** Test that the sector P/C level (already computed) is retained across refreshes
and `sector_pc_delta` returns the 5-read Δ (positive Δ = rising put demand). Store in a
small module holder like `_TREND` (lock-guarded), or the existing session cache.

**Step 2–4:** Red → implement → green.

**Step 5: Commit** `feat(sentiment): sector put/call 5-day delta`.

---

## Phase 2 — Classifier core + replace + page + recording (FIRST SHIP)

### Task 2.1: The aggression blend (pure)

**Files:**
- Create: `sentiment-dashboard/scoring/aggression.py`
- Test: `sentiment-dashboard/tests/test_aggression.py`

**Step 1: Write failing tests.** `blend_aggression(components, confs) -> (score, conf)`
where `components = {effort, skew_delta, flow, order_flow?}` each signed in `[-1,1]` and
`confs` the matching confidences. Confidence-weighted (same idiom as
`intraday_trend.blend_trend`): `Σ(w·s·c)/Σ(w·c)`, den==0 → `(0.0, 0.0)`. Sign convention:
positive skew_delta (rising put demand) → **negative** aggression contribution (map at the
caller). Test: all-present blends; missing order_flow drops to REST-only; all-missing →
neutral 0/0.

**Step 2–4:** Red → implement → green.

**Step 5: Commit** `feat(sentiment): confidence-weighted aggression blend`.

### Task 2.2: The grid classifier (pure)

**Files:**
- Create: `sentiment-dashboard/scoring/market_state.py`
- Test: `sentiment-dashboard/tests/test_market_state.py`

**Step 1: Write failing tests.**

```python
from scoring.market_state import classify_market_state, STATE_LABELS

def test_bullish():
    s = classify_market_state(direction_score=75, aggression=0.6)
    assert s.state == "bullish"

def test_lack_of_bullishness_up_but_hollow():
    s = classify_market_state(direction_score=72, aggression=-0.5)
    assert s.state == "lack_of_bullishness"

def test_neutral_balance():
    assert classify_market_state(50, 0.0).state == "neutral"

def test_lack_of_bearishness_down_but_no_followthrough():
    s = classify_market_state(direction_score=35, aggression=+0.4)
    assert s.state == "lack_of_bearishness"

def test_bearish():
    assert classify_market_state(22, -0.6).state == "bearish"

def test_evidence_lines_present():
    s = classify_market_state(75, 0.6, evidence=["up-volume +40% vs 20d"])
    assert s.evidence and s.label == STATE_LABELS["bullish"]

def test_states_cover_five_labels():
    assert set(STATE_LABELS) == {
        "bullish","lack_of_bullishness","neutral","lack_of_bearishness","bearish"}
```

**Step 2: Run** → FAIL.

**Step 3: Implement** the grid per the design table. Direction bands: `>=60` bullish,
`<=40` bearish, else neutral (module constants `DIR_BULL_MIN=60`, `DIR_BEAR_MAX=40`).
Aggression sign: `>=AGG_POS` strong+, `<=AGG_NEG` strong−, else weak (`AGG_POS=0.2`,
`AGG_NEG=-0.2`). Lookup → state. `STATE_LABELS`/`STATE_DESCRIPTIONS`. `MarketState`
frozen dataclass (`state,label,description,evidence`).

**Step 4: Run** → PASS.

**Step 5: Commit** `feat(sentiment): five-state market_state grid classifier`.

### Task 2.3: Retire score_to_state; keep blend_trend as the direction axis

**Files:**
- Modify: `sentiment-dashboard/scoring/intraday_trend.py` (delete `score_to_state`)
- Modify: `sentiment-dashboard/tests/test_intraday_trend.py` (drop the `score_to_state` band tests; keep `blend_trend`/`ema_smooth`)

**Step 1:** Delete `score_to_state` and its tests (the new classifier replaces the
banding; `blend_trend` stays as the direction input).

**Step 2: Run** `cd sentiment-dashboard && ../.venv/Scripts/python -m pytest tests -q` → green (no orphan references).

**Step 3: Commit** `refactor(sentiment): retire score_to_state (replaced by market_state)`.

### Task 2.4: Rekey regime_filter to the new vocabulary

**Files:**
- Modify: `options-scanner/regime_filter.py` (`_TREND_STATE_VOTE`)
- Test: `options-scanner/tests/test_regime_filter.py` (rekey the vote-map assertions; keep AND-of-agreement / per-symbol / hysteresis tests)

**Step 1: Write/adjust failing tests** asserting:

```python
_TREND_STATE_VOTE == {
    "bullish": "bull",
    "lack_of_bullishness": "lean_bear",
    "neutral": None,
    "lack_of_bearishness": "lean_bull",
    "bearish": "bear",
}
```
plus a bullish-regime-blocks-CCS and bearish-regime-blocks-PCS end-to-end through
`evaluate_regime` with the new state strings.

**Step 2: Run** → FAIL.

**Step 3: Implement** — rekey the dict only. `evaluate_regime` logic untouched.

**Step 4: Run** the full options-scanner regime tests → PASS.

**Step 5: Commit** `feat(scanner): rekey regime_filter votes to five-state vocabulary`.

### Task 2.5: Compute the classifier in sentiment_svc + thread the new state

**Files:**
- Modify: `services/sentiment_svc/compute.py` (`compute_intraday_trend` now also builds `market_state`: gather aggression components — effort from SPY daily, skew_delta from `cache:options:flow_skew`, sector_pc_delta — blend, classify; return the state string + evidence alongside the 0–100 score)
- Modify: `services/sentiment_svc/compute.py:_bridge_trend` (publish the `market_state` string as `trend_regime.state`; keep the 0–100 score + additive back-compat `sma_*` fields)
- Modify: `services/sentiment_svc/handlers.py` (thread `market_state`/evidence through `_TREND` + into `cache:sentiment:composite` additive `market_state` block; `commit_state` hysteresis reused unchanged)
- Test: `services/sentiment_svc/tests/test_compute.py`, `test_handlers.py`

**Step 1: Write failing tests** — `compute_intraday_trend` returns a `market_state` key
with a valid state string; `_bridge_trend` puts the new state under `trend_regime.state`;
the composite payload carries an additive `market_state` block; a down-skew/negative-effort
fixture yields `lack_of_bullishness`; `options_svc` cache absent → skew confidence 0, still
classifies (graceful degradation). Reuse the existing `_TREND`/`commit_state` plumbing (its
tests already pin hysteresis — keep them green with the new state strings).

**Step 2: Run** the sentiment_svc suite → FAIL on the new assertions.

**Step 3: Implement** — read `cache:options:flow_skew` via the bus (defensive), map
skew_delta sign, call `blend_aggression` + `classify_market_state`, thread the string
through `commit_state` and the bridge/composite. Lazy/standalone imports per the sentiment
`scoring` isolation rule.

**Step 4: Run** → PASS (whole sentiment_svc suite green).

**Step 5: Commit** `feat(sentiment): compute + publish five-state market_state`.

### Task 2.6: Record the daily committed state (item 11 groundwork)

**Files:**
- Create: `services/sentiment_svc/market_state_history_db.py` (SQLite, mirrors `intraday_history_db.py`: one shared connection, `check_same_thread=False`, serialized by a lock)
- Add: `repo_paths.MARKET_STATE_HISTORY_DB`
- Modify: `services/sentiment_svc/handlers.py` (record `{date, committed_state, direction_score, aggression, components}` once per committed flip / daily)
- Test: `services/sentiment_svc/tests/test_market_state_history_db.py`

**Step 1–4:** TDD the store (insert/read/round-trip, one row per date, upsert on
recompute) → green.

**Step 5: Commit** `feat(sentiment): record daily market_state for later validation`.

### Task 2.7: /sentiment state chip → new vocabulary + evidence

**Files:**
- Modify: `webgui/pages/sentiment.py` (the trend state chip reads `derived.market_state`/composite `market_state` block: label + description + evidence lines; the 0–100 gauge is unchanged)
- Test: `webgui/tests/test_sentiment.py` (pure builders for the chip label/evidence rows)

**Step 1: Write failing tests** for a pure `market_state_chip(...)` builder (label + color
class from the 5-state finite set → Tailwind palette map, per the Tailwind-first standard;
evidence rows). No `.style()`.

**Step 2–4:** Red → implement → green (add to `test_no_inline_style.py` if a new file).

**Step 5:** Verify live in the preview (`webgui` server, `/sentiment`): chip shows a real
state + evidence; no console errors.

**Step 6: Commit** `feat(webgui): sentiment market-state chip + evidence`.

**► FIRST USER-VISIBLE SHIP.** The five states are live on REST data, driving the scanner
gate. Validate for a few sessions before trusting Phase 4–5 enrichment. Restart
`sentiment_svc` + `options_svc` to pick it up.

---

## Phase 3 — Tier-2 structure signals (task outlines; expand to a per-phase plan when reached)

- **3.1 Session structure (item 6)** — `sentiment-dashboard/scoring/` new pure
  `session_structure(intraday_bars) -> {pct_above_vwap, or_break}` signed → feeds the
  **direction** component of `score_price`. Tests: held-above-VWAP → positive; pinned-below
  → negative; OR break up/down. Wire into `compute_intraday_trend`.
- **3.2 Rejection/defense (item 7)** — pure candle fns `upper_wick_cluster(bars)` (near
  highs → exhaustion, aggression −) and `support_defense(bars)` (shallow pullback + fast
  recovery → resilience, aggression +). Feed the aggression blend.
- **3.3 State-transition push** — in `handlers.py`, on a *committed* state flip, call
  `shared.notify.channels` with a formatted "Market state: X → Y (evidence)" message,
  gated by market-hours + config presence. Reuses the Phase-0 helper. Test the transition
  detector (only fires on flip, deduped) + the formatter.
- **3.4 Volume-profile shape (item 8)** — pure `profile_shape(intraday_bars)` reusing
  `technical.volume_profile` → `balance | trend | double` + a balance-strength scalar;
  feed a Neutral-confidence boost into the classifier (strong balance + falling IV rank →
  crisp Neutral). Tests: single-HVN bell → balance; two-distribution day → not balance.
- **Ship 3:** structure signals sharpen the middle/Neutral cells. Commit per task; restart
  `sentiment_svc`.

## Phase 4 — Streamer equity aggressor flow (task outlines)

- **4.1 Widen the proxy equity normalizer** — `schwab_proxy.py`:
  `_L1_EQUITY_FIELDS` += `bid`(1), `ask`(2), `bid_size`(4), `ask_size`(5), `last_size`(9),
  `total_volume`(8); `_normalize_level1_equity` emits them. Additive — unit-test the
  normalizer; portfolio_svc consumers unaffected. **Live-verify field population** against a
  real RTH `LEVELONE_EQUITIES` sample (the existing `TODO(live)` caveat).
- **4.2 Aggressor classifier (pure)** — new pure `quote_rule(last, bid, ask, prev_last)` →
  `+size/-size/0` (Lee-Ready: `last>=ask`→buy, `last<=bid`→sell, between→tick-test vs
  prev). Unit-test each branch + the tick-test fallback.
- **4.3 sentiment_svc SSE consumer** — background task on `/stream/quotes?symbols=SPY,QQQ`
  (the portfolio_svc pattern), rolling 1–5 min windows → aggressor ratio + CVD + slope;
  publish `cache:sentiment:order_flow` (new `OrderFlow` contract). Feed `order_flow` into
  `blend_aggression` with its own confidence. Defensive: stream down → confidence 0.
- **Ship 4:** equity CVD enriches the aggression axis. Honest caveat in the code/docs:
  sampled (level-one conflates), SPY proxies $SPX.

## Phase 5 — Streamer option aggressor flow (task outlines; expand + live-verify when reached)

- **5.1 Widen the proxy option normalizer** — capture `last` + `last_size` in
  `_on_option_message`'s per-OSI cache (it already has bid/ask/iv). Additive.
- **5.2 Option SSE fan-out** — mirror the equity fan-out: an option-subscriber registry +
  refcounted OSI union (subscription union = tracked-trade legs ∪ flow OSIs) +
  `/stream/options?symbols=<OSI,…>` endpoint + `_on_option_message` fans raw ticks to
  subscribers. Reuse the equity `_update_equity_refcount`/reconcile pattern. Unit-test the
  refcount + fan-out; live-verify.
- **5.3 sentiment_svc option-flow consumer** — pick near-ATM SPY/QQQ OSIs from chains
  (refresh as spot moves), subscribe via `/stream/options`, classify each tick at-bid/at-ask
  (quote rule), tag put/call from the OSI, aggregate put/call aggressor pressure into
  `cache:sentiment:order_flow`. Feed into `blend_aggression`.
- **Ship 5:** measured option protection-flow. This is the heaviest phase and touches the
  proxy's stream worker — do it last, with live verification at each step.

---

## Acceptance (whole feature)

- `regime_filter` votes on the five new states; scanner gating verified end-to-end
  (Redis-driven: publish a `bullish` composite → CCS blocked; `bearish` → PCS blocked).
- `/sentiment` shows the state + evidence; the 0–100 gauge is unchanged.
- Aggression axis degrades gracefully (options_svc down / stream down → confidence drops,
  classifier still runs on what remains).
- Daily committed state recorded to `market_state_history.db` for later IC/backtest
  validation (item 11, separate design).
- All touched suites green, run per-service from the repo root.

## Deferred (Tier 3 — separate design later)

Item 9 (state → Swing-Scanner strategy-family bias), item 10 (state into the Driver
packet + guardrail modifier), item 11 (formal IC/backtest validation of the five states).
