# Captured-trade break-even trailing + recovery-aware auto-management — design

**Date:** 2026-08-09
**Status:** approved (brainstorm complete) — plan/impl to follow
**Scope:** the *captured signals* (scanner-tracked trades in `signals.db`), **paper-only**
(no broker orders). Manual paper account + isolated driver account are OUT of scope
(they already have their own manage cycles).

## Problem / motivation

Captured signals are today **advisory only**: `signal_recommender.recommend()` produces a
HOLD / TAKE_PROFIT / CUT recommendation (shown as the Rec column + a chime/toast flag), but
nothing acts on it — the user closes manually. Three gaps:

1. **Profit is left on the table.** The current rule takes profit the moment +50% of the
   credit is captured. The user wants to *let winners run*: at +50%, raise the stop to
   break-even and keep holding toward full credit.
2. **Good trades get cut on noise.** A delta-drift CUT can fire on a position that has
   ample time and no imminent strike breach and would likely recover.
3. **It's all manual.** The user wants unattended auto-refresh + auto-close, and the day's
   closed trades rolled into the EOD report.

## Confirmed decisions (from brainstorm)

| Decision | Choice |
|---|---|
| Recovery-hold may defer… | **the delta-drift stop only**. Money-stop (2×) + time-stop (DTE≤2) stay HARD floors. |
| Break-even exit level | **break-even + round-trip commissions** (net ≥ $0 after fees, never a loss). |
| Auto-close autonomy | **Settings toggle, default ON**. Market-hours gated, paper-only. |

## Exit logic — the new lifecycle (`signal_recommender.recommend()`)

Today's rule is a stateless ladder (TP@50% → 2× money-stop → delta-stop → time-stop → HOLD).
Rework it into a **lifecycle** driven by one new stateful input, **`be_armed`** (has the
trade ever touched +50% of credit?), plus recovery inputs (spot, short strike, type, DTE):

Precedence (first match wins):

1. **Money-stop (HARD):** `pnl ≤ −STOP_MULT·credit_total` → CUT / `MONEY_STOP`. Always.
2. **Time-stop (HARD):** `DTE ≤ CUT_DTE` **and** underwater → CUT / `TIME_STOP`. Always.
   (An *armed, profitable* trade near expiry is NOT time-stopped — it rides to full credit,
   protected by the break-even stop below.)
3. **Break-even stop (only when `be_armed`):** `pnl ≤ be_level` → CUT / `BREAKEVEN_STOP`,
   where `be_level = round-trip commissions` (a small positive $, so net ≈ $0 after fees).
   This is the "raised stop" — it protects the give-back once +50% was seen.
4. **Delta-stop (SOFT — recovery-deferrable):** the existing delta-drift/hard-ceiling breach →
   CUT / `DELTA_STOP`, **unless** the recovery rule holds, in which case HOLD.
5. **Arming transition:** `pnl ≥ TP_FRAC·credit_total` and not yet armed → **HOLD**
   (recommendation note "break-even armed"); the manage cycle persists `be_armed = 1`.
   The trade is NOT closed here — this replaces the old immediate TAKE_PROFIT.
6. **HOLD** otherwise (with the score-drift note).

**Recovery rule** (defers rule 4 only): HOLD instead of a delta-stop CUT when **all** of:
- `DTE ≥ RECOVERY_DTE_MIN`, and
- the short strike is **not breached** (PCS: `spot > short_put`; CCS: `spot < short_call`;
  IC: neither side breached), and
- spot is at least `RECOVERY_MIN_CUSHION` away from the short strike (per side for IC).

Because rules 1–2 remain hard floors and an actual breach re-enables the delta-stop, a
deferred trade can never exceed a ~2× loss or bleed into expiration week while losing — the
bounded-risk profile the user chose.

`recommend()` stays **pure**: `be_armed`, `spot`, `short_strike`, `call_short` (IC), `type`,
and `dte_remaining` all arrive in `ctx`; the caller (manage cycle / `build_mark`) supplies
them. New `recommendation_code` value `BREAKEVEN_STOP` joins the close-code set.

### Tunable constants (defaults; live in `signal_recommender.py`, the single source)

| Constant | Default | Meaning |
|---|---|---|
| `TP_FRAC` | 0.50 (unchanged) | +50% credit → arm break-even (no longer an immediate close) |
| `STOP_MULT` | 2.0 (unchanged) | 2× credit hard money-stop |
| `RECOVERY_DTE_MIN` | 5 | min DTE to defer a delta-stop |
| `RECOVERY_MIN_CUSHION` | 0.015 (1.5%) | min spot↔short-strike cushion to defer |
| break-even buffer | round-trip commissions | from `config/commissions.toml` via `commission.py` |

## Auto-manage engine (`services/options_svc`)

A new **captured-manage cycle**, mirroring the existing driver/paper managers, on a new
scheduler slot **every 5 min within market hours** (trading days only), gated by the toggle:

1. **Reprice** each OPEN captured signal (fresh mark/delta/spot via `signal_repricer`), and
   **record a mark** (`insert_mark`) so peak-profit / `be_armed` is tracked and the EOD/UI
   see fresh marks.
2. **Arm break-even** the first time `pnl ≥ 50%·credit` (persist `be_armed = 1`).
3. **Recommend** via the new `recommend()`; if it returns a close action
   (`BREAKEVEN_STOP` / `MONEY_STOP` / `TIME_STOP` / non-deferred `DELTA_STOP`),
   **auto-close** via `signal_db.close_signal_manually(signal_id, exit_val=current_mark,
   reason=code)` — writes the outcome + realized P&L; **no broker order**.
4. **Expiry settlement:** when a signal reaches expiration (`DTE ≤ 0`), settle at the
   repriced intrinsic — OTM → exit `0` = **full credit**; breached → intrinsic (partial/max
   loss). (`signal_repricer` already returns intrinsic at 0-DTE.)
5. **Publish** the refreshed open-signals cache + a new **closed-today** view, and fire the
   existing chime/toast/phone-push on each close.

**Safety guards** (mirror the driver's): skip any signal whose reprice failed or is stale
(never close on bad data); market-hours + trading-day + toggle gated; each auto-close wrapped
so one failure can't abort the loop; paper-only (writes outcomes, never orders).

**Cadence note:** 5 min ≈ 45–90 chain fetches/cycle across the ~45 collected symbols
(repricer caches chains per `(symbol, expiration)` within a cycle). Tunable
(`_CAPTURED_MANAGE_INTERVAL_MIN`) if tighter/looser is wanted; documented as a Schwab-API
cost lever alongside the GEX/market pollers.

## State — `be_armed`

Persist a **`be_armed INTEGER DEFAULT 0`** column on the `signals` table (idempotent `ALTER`
migration in `signal_db.init_schema`), set once when +50% is first reached. Survives
restarts, cheap to read, explicit.
*Alternative considered:* derive the peak from `MAX(pnl_pct_of_credit)` over `signal_marks`
each cycle — no schema change but a query per signal per cycle and depends on mark history
being complete. **Chosen: the stored flag** for simplicity and testability.

`get_open_signals_with_latest_mark` gains `s.be_armed` (additive, like the recent
`current_value` addition) so the GUI/manage cycle read it with the signal.

## Closed-today view + EOD report

The webgui `/eod` report is **Tier-1** (reads caches only), so closed captured trades must
reach it via a cache:

- New service view **`cache:options:captured_closed`** = today's closed captured outcomes
  (`{symbol, strategy, entry_credit, exit_value, realized_pnl, reason, close_ts}` + a day
  total), built by a new `compute.captured_closed_today()` reading `signal_outcomes` for the
  CT date, published by the manage cycle (and on any manual close).
- New EOD section **"Captured — closed today"** (`webgui/pages/eod.py`): a table + realized
  P&L total, in both the summary and detail reports (pure builders, unit-tested, in the
  existing `<details>`/TOC structure). Reads `$0`/`—` until trades close (by design).

## Settings toggle

New `app_settings` key **`captured_autoclose_enabled`** (default `True`) + a Settings-page
switch. Written through to the service (the service is a separate process) via the **ticker-
toggle pattern**: the toggle enqueues a `cmd:options` command → the handler writes
**`cache:options:autoclose_enabled`** → the manage cycle reads it each cycle
(`enabled` defaults True on a missing key / unreadable bus, so only an explicit OFF disables
it). Re-asserted from settings.json at webgui startup (mirrors `sync_ticker_setting`).

## Data flow

```
options_svc scheduler (5-min captured-manage slot, market hours, toggle ON)
  └─ compute.run_captured_manage_cycle()
       reprice → insert_mark → arm be_armed → recommend() → auto-close (close_signal_manually)
       → expiry settle → publish cache:options:captured (+ :captured_closed)
GUI (Tier-1): Captured page reads :captured (live), /eod reads :captured_closed
Settings toggle → cmd:options → cache:options:autoclose_enabled → read each cycle
```

## Testing (TDD)

- **`recommend()` lifecycle** (options-scanner tests): arming at +50% (no close), break-even
  +commissions exit after arming, recovery *defers* delta-stop when DTE/cushion pass but NOT
  when breached or short-dated, money/time hard floors always fire, expiry full-credit vs
  intrinsic.
- **`signal_db`**: `be_armed` migration + round-trip; closed-today outcome query.
- **manage cycle** (options_svc): arm→retrace→auto-close path; stale-reprice skip; toggle
  gate; publishes both views. A **Redis-driven e2e**: seed an open signal, drive it to +50%,
  retrace, assert it auto-closes at ~break-even and lands in `:captured_closed`.
- **EOD builders** (webgui): closed-captured section rows + totals; graceful-empty.
- Guards: no-inline-style / Tier-1 import rules stay green.

## Out of scope / future

- Real broker execution (stays paper/advisory-record only).
- Further trailing beyond break-even (e.g., ratchet to lock 25%/50%) — the user chose
  break-even+commissions; a laddered trail is a later option.
- Applying the same lifecycle to the manual paper / driver accounts (they have their own
  managers; could be unified later).
- The legacy `options-scanner/eod_report.py` markdown rollup (target is the webgui `/eod`).
