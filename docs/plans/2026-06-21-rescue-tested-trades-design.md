# Rescue Tested Trades — Advisory + One-Click Apply (Design)

**Date:** 2026-06-21
**Branch:** `Using_Highcharts`
**Status:** Approved design — proceeding to implementation plan.

## Problem

When a defined-risk credit spread (PCS / CCS / IC) is **tested** — the underlying
drops toward/through the short strike — delta and negative gamma expand and the
trader must choose between capital preservation and trade rescue. The system today
has an auto-**close** manage cycle (50% profit target, 2× credit money-stop,
delta-stop, time-stop) but **no roll / convert / narrow / adjust logic anywhere**.
This feature fills that gap: detect tested positions and present a **ranked menu of
viable rescue adjustments**, each with full economics (incl. commissions) and
market-structure context, that the user can **approve to apply**.

## Decisions (from brainstorming)

- **Core behavior:** Advisory **+ one-click apply** (recommend → approve → execute),
  mirroring the Driver approval queue.
- **Trade universe:** Paper positions **+ captured signals** (advisory on both; apply
  only on actual paper positions).
- **Rescue actions:** All of the essay's four **plus** a researched broader set
  (below) — user wanted the real option space, not just the essay subset.
- **Presentation:** **Ranked menu** of all applicable actions (not a single
  opinionated rec), each scored with mechanics + rationale.
- **Architecture:** **Approach C (hybrid)** — cheap detection in the always-running
  5-min manage cycle (alert + heat), expensive candidate ranking on-demand when a
  position's rescue view is opened.
- **Commissions:** Schwab standard rates included in every candidate's economics
  and in ranking.
- **Roll = close + new linked position** (not in-place mutation), with a stale-price
  re-check abort before any apply.

## Action space (researched)

Essay's four: systematic stop/close, convert to Iron Condor, narrow the spread,
roll down & out. Plus: roll out only, roll down only, roll the untested side closer,
convert to Iron Butterfly, convert to Broken-Wing Butterfly/Condor, go inverted,
delta-hedge with ES/NQ/SPX futures (advisory-only), partial close / scale down,
time-based (21-DTE) management trigger.

Sources: [Adjust an Iron Condor — 3 ways](https://advancedautotrades.com/how-to-adjust-or-manage-an-iron-condor-position/),
[Broken Wing Put Butterfly](https://datadrivenoptions.com/strategies-for-option-trading/favorite-strategies/broken-wing-put-butterfly/),
[7-DTE rolling](https://datadrivenoptions.com/7-dte-rolling-options/),
[21-DTE rule](https://medium.com/@build.business.side.hustle/why-ill-never-ignore-the-tastylive-21-dte-options-rule-again-cafe84c8f903).

---

## Section 1 — At-risk detection (cheap layer)

Runs inside the existing 5-min `run_manage_cycle`, which already reprices every open
position (`current_value`, `unrealized_pnl`, `current_short_delta`,
`current_underlying`). No new chain fetches — classify what's already computed, plus
one cheap `gamma_snapshot(symbol)` per distinct symbol (already collected by Gamma).

Pure `assess_position_risk(position, mark, gex, regime)` → a `state`
(`ok → watch → tested → critical`) and a **0–100 heat score**:

| Signal | Source | Contribution |
|---|---|---|
| Strike proximity | `current_underlying` vs `short_strike` | within X% / at / through short strike |
| Short delta | `current_short_delta` | past entry-Δ + 0.12, or abs ≥ ~0.30 (warn) / 0.45 (critical) — reuses existing delta-stop |
| P&L vs credit | `unrealized_pnl` / `entry_credit` | crossing 1× / 2× / 3× credit |
| Time | DTE from `expiration` | ≤ 21 (manage), ≤ 2 (urgent) + tested |
| GEX context | `gamma_snapshot` flip / put-wall | short below flip = danger; on a wall = bounce-likely — **modifier** |
| Regime | bridge `trend_state` | strategy fighting the tape — **modifier** |

Thresholds in one tunable, unit-tested `RESCUE_THRESHOLDS` dict that mirrors the
existing stop constants so detection and auto-close stay consistent. GEX/regime are
heat **modifiers**, never standalone triggers.

Surfaces: red count badge on a new **Rescue** nav item (single-user, like
`_NAV_BADGES`) + heat-colored rows on Paper Trades and Captured Signals.

## Section 2 — Candidate generation & ranking (on-demand layer)

Opening a flagged position fires a `rescue` command → Tier-2
`rescue_candidates(position, mark, gex, regime, lookback)` builds every **applicable**
action, prices each by fetching the relevant chain(s) once, returns a ranked list.
Cached to `cache:options:rescue:<position_id>` + version-polled (like
`sim_run`/`calc_result`).

Applicability gating by `strategy` + state:

| Action | Applies to | Priced by |
|---|---|---|
| Close now (systematic stop) | all | current mark (no fetch) |
| Partial close / scale down | qty > 1 | current mark |
| Narrow (roll long leg in) | PCS/CCS | reprice new long strike |
| Convert to Iron Condor | tested PCS/CCS | price opposite-side credit spread |
| Convert to Iron Butterfly | PCS/CCS/IC | price untested short ATM |
| Convert to Broken-Wing Butterfly/Condor | PCS/CCS | price added long+short legs |
| Roll down only (same expiry) | PCS/CCS | reprice lower strikes |
| Roll out only (same strikes, later expiry) | all | fetch later-expiry chain |
| Roll down & out | all | fetch later-expiry chain, lower strikes |
| Go inverted | IC / strangle | price one side past the other |
| Delta hedge w/ ES/NQ/SPX futures | index symbols | sizing only (advisory) |

Uniform candidate record:
`{action, label, applies, apply_kind(execute|advisory), gross_cash, commission,
net_cash, new_max_loss, new_breakeven, new_short_delta, new_width, new_expiry,
dte_after, est_fill_legs[], rationale[], context[], score, warnings[]}`.

Ranking `score_candidate(...)` priority: (1) max-loss reduction per **net** dollar
spent, (2) delta flattening, (3) credit vs debit (debit actions penalized + flagged —
*never roll for a debit just to save it*), (4) GEX/regime fit (roll-down-and-out
penalized in negative-gamma/expanding-vol; favored into a put wall), (5) breakeven
improvement. Defensive: an unpriceable leg drops that one candidate with a noted
reason; engine returns `{error}` only on total failure.

### Section 2b — Commission model

Single source of truth `config/commissions.toml` (mirrors `config/ports.toml` rule —
no hard-coded rates). Schwab standard (2026 Pricing Guide):

```toml
[options]        # per contract, per leg, open AND close
equity = 0.65
index  = 0.65
index_exchange_fee = 0.00   # Cboe SPX/VIX passthrough — set per product when known

[futures]        # per contract, PER SIDE
standard = 2.25
exchange_fee = 0.00         # exchange/regulatory passthrough
```

`commission_for(action_legs, symbol, qty)`:
- per leg = `qty × rate` (index symbols → `index` + `index_exchange_fee`; else `equity`)
- close 2-leg spread = 2 legs; convert-to-IC = 2 new legs; narrow = 2 legs; roll
  down & out = 4 legs; partial = legs × closed contracts
- **let-expire / assignment legs = $0** (so close-now vs let-ride reflects real fee diff)
- futures hedge = `futures_qty × standard × 2 (round turn) + exchange_fee`

Candidate gains `commission` + `gross_cash`; **`net_cash = gross_cash − commission`**
is the headline. Ranking metric (1) counts commission in the denominator; debit-penalty
applies to the **net-of-commission** figure. Cards show gross / commission / net.

Sources: [Schwab Pricing Guide](https://www.schwab.com/legal/schwab-pricing-guide-for-individual-investors),
[All Trading Fees](https://www.schwab.com/node/15241), [Futures FAQs](https://www.schwab.com/futures/faqs).

## Section 3 — Strategic-context layer

Pure `strategic_context(position, gex, regime)` → attached to the rescue payload,
feeds heat (§1) and ranking (§2). Annotation + ranking modifier, **never a hard gate**.

1. **Dealer gamma** (`gamma_snapshot` flip / put wall / net gamma): short **below
   flip** → negative-gamma / expanding vol → flag "rolling down here is risky"
   (penalize rolls, favor close/narrow). Short **at/near put wall / high-OI** →
   bounce likely → favor rolls, soften urgency. Distance-to-flip / -to-wall as chips.
2. **Regime** (bridge `trend_state` / `trend_confidence`): strategy fighting the tape
   raises heat and penalizes double-down rolls; `pullback_in_bull` softens.
3. **Settlement mechanics** (symbol + small instrument map): SPX/index
   (European cash-settled) → no early-assignment, holding to expiry structurally safe;
   ES/NQ futures options (American) → assignment / futures-delivery risk on deep-ITM
   shorts (bump urgency); equity/ETF (American) → ex-div / deep-ITM assignment note.

Each card carries a short `context[]` string explaining the read.

## Section 4 — Apply mechanics

Pick a candidate → page enqueues `rescue_apply` on `cmd:options`
`{position_id, action, candidate}`. Tier-2 handler executes against the **paper
engine** (paper positions only; captured signals advisory). Guarded.

**Idempotency/safety:** candidate carries the `priced_from_version`; on apply the
handler re-prices live and **aborts (`stale`)** if `net_cash` drifted beyond
tolerance or the position changed/closed → GUI "prices moved, re-review." Atomic per
position; audit line to `paper_engine.log`.

New paper-engine adjustment primitives (`paper_adjust.py` + `paper_engine.py`):

| Action | Effect |
|---|---|
| Close / partial close | existing close path; partial reduces `quantity`, realizes slice P&L, releases that BP |
| Narrow | close old long + open new long; update `long_strike`/`width`/`max_loss` |
| Convert to IC / Iron Butterfly | add legs; `strategy` PCS→IC; recompute `max_loss`/BP (width-based) |
| Convert to broken-wing | add extra long+short legs; recompute |
| Roll down / out / down & out | close current (realize P&L) + open new **linked** position (`parent_position_id`) |
| Go inverted | reprice/move one side past the other |
| Futures delta-hedge | **advisory-only — not executed**; shows sizing |

New `position_adjustments` table (`position_id`, `action`, `legs[]`, `gross_cash`,
`commission`, `net_cash`, `ts`, `reason`) — auditable rescue history, feeds EOD later.
Commission realized into cash on every apply. **Roll = close + new linked position**
(keeps realized P&L + history clean).

## Section 5 — Data model, contracts & service wiring

New contracts (`shared/contracts/options.py`):

```
RescueAdvisory:   # cache:options:rescue:<position_id>
  position_id, symbol, strategy, state, heat,
  mark{underlying,current_value,unrealized_pnl,short_delta,dte},
  context[], candidates:[RescueCandidate], priced_from_version, ts, error?

RescueCandidate:
  action, label, applies, apply_kind, gross_cash, commission, net_cash,
  new_max_loss, new_breakeven, new_short_delta, new_width, new_expiry, dte_after,
  est_fill_legs:[{side,right,strike,expiry,qty,price}], rationale[], context[],
  score, warnings[]
```

Cheap detection rides existing views: `cache:options:paper_account` rows gain
`rescue_state` + `heat` (written by manage cycle); small
`cache:options:rescue_summary` `{n_tested, n_critical, position_ids[]}` for the badge.

Service wiring (`services/options_svc`):
- `compute.py`: `assess_position_risk`, `rescue_candidates`, `strategic_context`,
  `score_candidate`; `commission.py`; primitives in
  `options-scanner/paper_engine.py` + new `paper_adjust.py`.
- `handlers.py`: extend `run_manage_and_refresh` → write risk overlay +
  rescue_summary (cheap); `rescue` command → `cache:options:rescue:<id>`;
  `rescue_apply` command → adjust → re-cache + publish.
- `scheduler.py`: no new cadence — piggybacks `manage_due`.
- `shared/contracts/options.py`: two new models. `config/commissions.toml`: rates.

Tier-1 rule preserved: webgui imports only `nicegui` + `shared.bus` + `shared.contracts`.

## Section 6 — GUI & testing

New page `/options/rescue` (`webgui/pages/options/rescue.py`), engine-free reader:
- **At-risk list** — all `tested`/`critical` positions (paper + captured),
  heat-colored, sorted by heat; reads overlay + captured view; version-polls; no fetch.
- **Selected position → ranked menu** — enqueues `rescue`, polls
  `cache:options:rescue:<id>`; one card per candidate (best first): action/label, the
  **gross / commission / net** line, new max-loss · breakeven · short-Δ · width ·
  expiry, `est_fill_legs` table, `rationale[]` + `context[]` chips, `warnings[]`
  badges. `execute` cards → **Apply** → confirm → `rescue_apply`; `advisory` (futures)
  → sizing only.
- **Apply result** — toast + stale-price re-review path; per-position adjustment-history strip.
- **Nav + alerts** — new **Rescue** item (Options group) with red badge from
  `rescue_summary` (wired into `_NAV_BADGES`/watcher + optional chime); row highlights
  on Paper Trades + Captured.
- Optional persistent `ui.highchart` payoff diagram (current vs proposed) — built at
  render (ESM gotcha); `@guard` on handlers.

Testing (TDD, pure-first):
- Tier-2 unit: `assess_position_risk` ladder/heat; `commission_for` (equity/index/
  futures, leg counts); `rescue_candidates` gating + record completeness + defensive
  drops; `score_candidate` ordering (debit-penalty, GEX modifiers); `strategic_context`
  three reads; apply primitives + stale-price abort (fakeredis + fake chain/broker).
- Contracts: `RescueAdvisory`/`RescueCandidate` validation.
- Tier-1 unit: pure builders (`heat_color`, card rows, summary) in `test_rescue.py`;
  `/options/rescue` in `test_shell.py`.
- End-to-end: Redis-driven — enqueue `rescue`/`rescue_apply`, read the cache.
