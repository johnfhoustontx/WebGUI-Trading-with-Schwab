# Driver directional gate + cumulative MTD target — Design

**Date:** 2026-07-09
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorming) → ready for implementation plan
**Motivated by:** the DELTA_STOP forensics (investigation "C") on the driver's real
closed book — 22 closed trades, **−$908 realized / 27% win / PF 0.23**, drawdown to
$22,768 (−8.9%), and a −$1,946 halt day. Root cause = **wrong-side selection**: 10 of 11
DELTA_STOPs were **call credit spreads (CCS)** run over by a rising tape (CCS bucket
−$706 @ 21% win). Stops fire at ~0.35 short delta (sensible, not hair-trigger) with a
median ~1‑day hold (some 5‑day) — i.e. genuine adverse moves, not whipsaw. **Do NOT
loosen the stops; fix the entry side.**

Two features:
- **A — Directional gate** (hard block the wrong-side spread by regime; **backtested
  against the driver's own closed trades before it can gate live**).
- **B — Cumulative MTD target** (carry the $500/day deficit/excess forward month-to-date,
  capped).

## Key forensic finding that shapes the gate signal

The app's own **sentiment read was 3.92 (bearish) while price melted up** — so the app's
*directional opinions* (sentiment, and likely the gamma-briefing `bias`, and the
five-state) were **inverted** during exactly the period that bled. Therefore the gate must
key on **price reality (actual index direction + breadth)**, NOT on any model's directional
opinion, and NOT on the gamma **flip** alone (the flip is a *volatility/gamma* regime
signal — above flip = positive gamma / mean-reverting, below = negative gamma / trending —
not a direction signal). This is the single most important design constraint.

---

## A — Directional gate (hard block)

### Fixed architecture (not up for tuning)
- **Lives in** `services/driver_svc/guardrails.py` — the pure, code-authoritative safety
  core. A new per-trade check inside `apply_guardrails`; the model proposes, the code
  blocks.
- **Block mechanic** (`_side_blocked(signal, posture)`), using the existing
  `signal_structure` (PCS/CCS/IC):
  - posture **`up`** → **block CCS** (short-call spreads — the −$706 bucket)
  - posture **`down`** → **block PCS** (short-put spreads)
  - posture **`neutral`** → block nothing (the ambiguous middle keeps trading — respects
    the Very Aggressive mandate)
  - **IC exempt** (neutral structure, defined both sides; easy to fold in later)
- **Rejection reason** = **`WRONG_SIDE_REGIME`**, surfaced on the `/driver` decision-log
  row exactly like `RISK_TOO_HIGH` (observability — you see *why* a pick was killed).
- **Degrade-safe:** posture `neutral` (its default) means no block, so **no market_read /
  no data → the gate is inert** (byte-identical to today). It can never block blind.
- **Wiring:** `compute.run_cycle` already has `packet["market_read"]` (from the
  market-context block shipped 2026-07-08). It computes `posture =
  _directional_posture(market_read)` and passes `posture=` into `apply_guardrails`
  (new kwarg, default `"neutral"`). **No handler change needed** — the market_read is
  already in the packet.

### Posture signal — price reality, finalized by the backtest
`compute._directional_posture(market_read)` → `up` / `down` / `neutral`. It keys on
**price truth**, deliberately NOT on sentiment/bias:
- **Broad-index direction** — is the broad tape ($SPX/QQQ) actually trending up or down?
  (Live: from `cache:market:dashboard` change_pct — already read for the market_read;
  historical/backtest: reconstructed from the `gex_history_db` spot series.)
- **Breadth** — `$ADVN-$DECN` sign (live, from the dashboard) as the confirmer.
- Decisive `up`/`down` only when the index direction and breadth agree; else `neutral`.

**The exact threshold (how strong a trend, whether breadth must agree, lookback) is an
OUTPUT of the backtest, not pre-committed.** We fix the *mechanism* + the *family of
signal* (price-truth direction) and tune the trigger against the real losses until it
reliably flags the wrong-side CCS without nuking winners. Honest proxy caveat: breadth
history is not stored, so the **backtest validates the index-direction component**
(recorded in `gex_history`); breadth is the live confirmer that only *tightens* the gate.

### Backtest BEFORE it gates live (the discipline)
A manual validation harness (run offline, NEVER in a request path — mirrors
`validate_market_state.py`):
1. Load the driver's 22 (and growing) closed trades from `paper_account_driver.db`
   (`symbol, strategy, entry_ts, exit_reason, realized_pnl`).
2. For each entry, reconstruct the broad-index direction at that time from
   `gex_history_db` (spot trend over a short lookback around `entry_ts`).
3. Apply the candidate posture rule → would this trade have been blocked?
4. **Tally:** $ of the CCS loss bucket blocked (saved) vs $ of winners blocked (forgone),
   plus win-rate of the surviving book.
- **Acceptance gate:** blocks a **majority of the −$706 CCS loss bucket** while sparing
  **most winners** (net-positive impact on the realized book). Only then is the gate
  enabled to block live. If the first signal is too weak/strong, tune it (this is the
  point of backtesting first).
- Small sample (22 trades, one up-trending stretch) → this is *directional evidence*, not
  a statistical proof; stated as such.

---

## B — Cumulative MTD target

Replaces the flat `DAILY_TARGET = 500` banking target with a dynamic month-to-date target
that carries the deficit/excess forward.

### Formula (pure `compute.effective_target(...)`)
```
effective_target = clamp( N*BASE − MTD_realized_before_today , FLOOR , CAP )
   BASE  = 500     (settings.DAILY_TARGET)
   CAP   = 1000    (settings.TARGET_CAP,  2x base — recover over days, never one shot)
   FLOOR = 250     (settings.TARGET_FLOOR — keep a light day even when ahead of pace)
   N     = trading days elapsed this month INCLUDING today (weekdays − _HOLIDAYS)
   MTD_realized_before_today = Σ realized_pnl of driver closed_positions whose exit date
                               is in the current month AND strictly before today
```
- **Behind the $500/day pace →** `N*500 − MTD` is large → target ratchets up to **$1,000**.
- **On pace →** ≈ **$500**.
- **Ahead →** the term goes small/negative → floored at **$250** (carry the excess forward;
  still grinds a light day).
- Resets naturally on the **1st** (MTD). MTD data missing/unreadable → **fall back to $500**.

### What changes and what does NOT
- **Banking halt** (`guardrails.halt_state`) banks the day when `day_pnl ≥ effective_target`
  (was the flat 500). So a behind day presses to +$1,000 before banking; an ahead day banks
  at +$250.
- **The −$1,500 loss halt stays FIXED** (`settings.DAILY_LOSS_HALT`) — the risk cap is
  unchanged.
- **Per-trade + daily-risk caps unchanged** (`PER_TRADE_MAX_RISK`, `DAILY_RISK_BUDGET`,
  `MAX_CONCURRENT`, `MAX_TRADES_PER_CYCLE`). So the ratchet changes **when it banks/stops**,
  NOT how big any single trade can be — bounded by construction (no chasing via oversizing).
- The decider sees the ratcheted `target` + `gap_to_target` in its packet, so it naturally
  presses harder on a behind day and eases on an ahead day. The `_SYSTEM` prompt needs no
  change (it already reasons over target/gap).

### Wiring
- `settings.py`: add `TARGET_CAP=1000.0`, `TARGET_FLOOR=250.0` (BASE stays `DAILY_TARGET`).
  `limits()` gains them so they're visible to the guardrails/tests.
- New pure `compute.effective_target(base, n_trading_days, mtd_before_today, *, cap, floor)`
  + `compute.mtd_realized_before_today(closed_positions, today_ct)` + a trading-day counter
  (reuse `scheduler._HOLIDAYS`). All unit-tested.
- `handlers.run_autonomous_cycle`: compute `N` + `mtd_before_today` (from the
  `driver_paper_account` `closed_positions` it can already read), derive `effective_target`,
  and pass it as `target=` into `compute.run_cycle` (which threads it into `build_packet` +
  `halt_state`). Defensive → base 500 on any failure.
- **Observability:** surface `effective_target` + the MTD pace (±$ vs `N*500`) on the
  published `AutonomousState` (a small additive field / decision-log note) so `/driver`
  shows "Target today $X · MTD ±$Y".

---

## Testing (TDD; no live Claude/proxy)
- **Gate:** `_side_blocked` (CCS blocked on `up`, PCS on `down`, IC exempt, neutral →
  nothing); `_directional_posture` (up/down/neutral from index-direction + breadth;
  missing/partial → neutral); `apply_guardrails` rejects a wrong-side trade with
  `WRONG_SIDE_REGIME` and **passes it through untouched when posture=neutral / absent**
  (back-compat); `run_cycle` derives posture from the market_read and threads it.
- **Cumulative target:** `effective_target` (behind → cap, on-pace → base, ahead → floor,
  clamps); `mtd_realized_before_today` (month filter + excludes today + junk-tolerant);
  the trading-day counter; `halt_state` banks at the dynamic target; handler passes the
  right target; missing MTD → 500 fallback.
- **Backtest harness:** its own unit tests over a synthetic closed-trade set + a real run
  reported in the plan (acceptance = blocks the CCS loss bucket).
- Full `services/driver_svc` + `shared/contracts` suites green.

## Files
- `services/driver_svc/guardrails.py` — `_side_blocked`, `WRONG_SIDE_REGIME`, the
  per-trade gate in `apply_guardrails` (+ `posture` kwarg).
- `services/driver_svc/compute.py` — `_directional_posture`, `effective_target`,
  `mtd_realized_before_today`, trading-day counter; `run_cycle` posture wiring.
- `services/driver_svc/settings.py` — `TARGET_CAP`, `TARGET_FLOOR` (+ `limits()`).
- `services/driver_svc/handlers.py` — compute + thread the effective target; surface it.
- `services/driver_svc/validate_directional_gate.py` — the offline backtest harness (NEW).
- `shared/contracts/driver.py` — additive field for the effective target / pace (docstring;
  rows stay loose).
- `services/driver_svc/tests/…` + docs (CLAUDE.md changelog) + memory.

## Deferred (still not in scope)
- The **gamma-wall guardrail** (reject a spread whose short strike is beyond the protective
  wall) — a *volatility/structure* gate, complementary to this *directional* gate; separate
  unit.
- Scanner/Swing `rr_delta` + breadth ranking tilts; the `regime_filter` bridge→cache
  consistency fix.
- A liquidity/vol name filter for the fast small-cap money-stops (NBIS/IREN) — noted in C
  as a minor secondary; can fold into the gate later.

## Restart note
After build, **restart `driver_svc`** (richest with `options_svc` + `market_svc` +
`sentiment_svc` up so the market_read populates). The gate ships **inert** (posture
neutral) until the backtest passes and it's switched to block. PAPER ONLY.
