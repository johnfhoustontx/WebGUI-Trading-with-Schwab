# Exits for long options and debit spreads (D3)

*Design, 2026-09-12. Gap assessment item **D3**.*

## What the assessment asked for

> **D3. Exits for long options and debit spreads.** Move them into the account,
> or give the ledger a manage cycle. Give them a profit target — tastylive uses
> 50% on debit spreads, TradingBlock about 80% — and a time exit before the final
> weeks. The practitioner sources don't stop out losing debit spreads (tastylive
> closes them before expiry instead), so a percent-of-debit stop is an option,
> not a sourced rule.

The gap is **real and reachable**, so unlike D2 this one is built. But the first
sentence is half wrong, and measuring it turned up two defects that had to be
fixed before any rule could be added safely.

## Finding 1 — the ledger already HAS a manage cycle; it had no exit RULE

Measured against the code, the Paper Ledger (`trades.db`) already gets, on the
same `run_manage_and_refresh` pass as the paper account:

| it has | where |
|---|---|
| a live display mark on every OPEN row | `compute._reprice_open_pnl`, via `reprice_legs` for debits |
| **expiry settlement at intrinsic** | `compute.expire_ledger_trades` → `paper_trader.expire_paper_trade` |
| a manual close | the page's Close button → `paper_close` |

⚠ **And the cadence is HOURLY, not 5-minute** — `paper_cycle_due`, 09:00–14:00
CT, six times a trading day, plus the "Run manage cycle" button.
`expire_ledger_trades`' own docstring said "the 5-min manage tick" and had for
months (the 1-minute `manage_due` slot is the isolated DRIVER account's), and the
first draft of this feature's help text repeated it — the manuals-rot trap doing
its work in real time. Corrected in both places. Six checks a day is the honest
resolution of these rules — a target reached at 09:15 is acted on at 10:00 — and
the pass rides that cadence rather than adding a seventh scheduler slot, because
the rules are day-scale (a +50% target, a 21-DTE exit) and not intraday.

What it did **not** have is anything that closes a position *before* expiry. So
the change is an exit-rule evaluation on a cycle that already runs — not a new
cycle, and not a migration into the account.

**And the inventory is reachable.** `strategy_table._PAPER_TYPES` lists
`LONG_CALL`, `LONG_PUT`, `BULL_CALL`, `BEAR_PUT` alongside the credit spreads, so
the Paper button is live for them on both the Strategy Finder and the Market
Scanner's **Directional** tab; `paper_trader.PAPER_DEBIT_TYPES` routes exactly
those four to `_create_debit_trade`. A long call sent there rode to expiry
whatever it did in between — up 300% or down to nothing.

⚠ Prod's ledger currently holds **0 trades**, so nothing has actually been
mismanaged yet. That is why this is a latent gap rather than an incident, and why
the two defects below cost nothing to fix now.

## Finding 2 — the credit rules are INVERTED for a debit, not merely absent

`signal_recommender.recommend` computes `credit_total = entry_credit × 100`, and
a debit row stores `entry_credit` as the **negative** per-share debit. For a
$2.00 debit (`credit_total = -200`):

```
rule 1   pnl <= -stop_mult × credit_total   ->   pnl <= +400    CUT/MONEY_STOP
rule 5   pnl >= tp_frac  × credit_total     ->   pnl >= -100    TAKE_PROFIT
```

Measured by running the real function:

| position P&L | action | code |
|---|---|---|
| $0 | **CUT** | MONEY_STOP |
| −$99 | **CUT** | MONEY_STOP |
| +$100 | **CUT** | MONEY_STOP |
| +$399 | **CUT** | MONEY_STOP |
| +$401 | TAKE_PROFIT | TARGET_HIT |

A healthy long call comes back **CUT/MONEY_STOP at every P&L from −$199 to
+$399** — it would be closed on its first manage tick. Nothing routes a debit
through `recommend` today, so it is latent; **giving the ledger exits without
fixing this first would have been the bug.** `recommend` now DISPATCHES on
direction, so no path can reach the credit rules with a negative credit.

## Finding 3 — a live defect: the manual close books a debit's P&L backwards

`close_paper_trade` computed `realized_pnl = (entry_credit − exit_debit) × qty ×
100` with no direction branch. A long call bought at $2.00 and sold at $3.00
booked `(−2.00 − 3.00) × 100 = −$500` where the truth is **+$100** — a winner
recorded as a five-times-larger loss.

`_expire_debit_trade` exists precisely because the same formula is wrong at
expiry. The manual close is the ledger's **only** pre-expiry exit and never got
the same treatment. Fixed, with a control test asserting that closing at $8.00 by
hand and expiring at an $8.00 intrinsic — identical economics — book the same
number. They did not.

The dialog's label was wrong in the same place: it asked a long call for its
"Exit debit" when closing one pays you a credit. `paper.close_prompt_label` now
names the side the position is actually on. Same defect as the copy pass's
*a `Credit` column is wrong wherever the book holds debits*, one layer up.

## The rules

`signal_recommender._recommend_debit`, three rules, first match wins, every
threshold resolved through the existing `shared.trade_mgmt.structure_rules` so
the levels live in `config/trade_mgmt.toml` beside the credit ones.

### 1. The loss side ships OFF

`debit_stop_frac = None`. That is the **sourced** default: the practitioner
sources close debit spreads before expiry rather than stopping them out, so a
shipped level would be invention. It is a one-line opt-in
(`debit_stop_frac = 0.60` cuts once 60% of the debit is gone), and it is the
debit path's only loss-side knob — `loss_rules` and `stop_mult` are
credit-denominated and are not read here, since *2× a debit* is a loss that
cannot happen.

### 2. The profit target, on the denominator the structure actually has

⚠ **This is a decision, not an implementation detail, because the two readings
are genuinely different numbers.** The source says 50%; it does not say 50% *of
what*.

| structure | denominator | why |
|---|---|---|
| a bounded vertical (`BULL_CALL` / `BEAR_PUT`) | **max profit** | this is what `tp_frac` already means on the credit side, where the credit *is* the max profit |
| a long option (`LONG_CALL` / `LONG_PUT`) | **the debit paid** | it has no max profit at all — the ledger stores `unbounded = True` / `max_profit_total = None` for exactly that |

On one $2.00 debit over a $5 width, 50% of max profit is **+$150** and 50% of the
debit paid is **+$100**. An unusable max profit (absent, zero, negative, NaN, a
string, a bool) falls back to the debit paid rather than making the target
unreachable — or, at zero, firing it at break-even.

⚠ The row stores `max_profit_total` **already multiplied by quantity** while the
repricer's P&L is per contract, so `_ledger_exit_ctx` divides. Handed through
raw, a 3-lot's target would be three times too far away.

`tp_frac` stays the app-wide **0.50**, which is both the lower of the two sourced
numbers and the value every other structure here uses. TradingBlock's ~80% is one
edit away, exactly as B1 left TradingBlock's 90/95% for the income structures.

### 3. A time exit that is profit-blind — and guarded against firing at entry

Unlike the credit side's `manage_dte`, `exit_dte = 21` fires whether the trade is
up or down (the sourced rule is to be out before the final weeks either way):
`TAKE_PROFIT` when ahead, `CUT` when behind, code `TIME_EXIT` either way — a
distinct code from `TIME_STOP`, which means "DTE ≤ `cut_dte` **and** underwater".

⚠ **It fires only when `dte_at_entry` was greater than the threshold, and that
guard is what makes the rule shippable.** Measured: the Market Scanner's
Directional tab builds from two windows — the 0-DTE window at **DTE 0–4** and the
swing window at **DTE 5–15** — so *every* debit it can produce arrives inside a
21-day threshold. An unguarded rule would close **100% of them on the tick after
they opened**, and a rule that fires at entry is worse than no rule. An unknown
`dte_at_entry` declines the exit too: absence must not read as a long horizon.

The Strategy Finder's DTE inputs default to **0–120**, so it is the surface that
actually produces positions this rule applies to.

⚠ **Accepted consequence: the short-dated inventory gets no time exit.** That is
correct rather than a hole — a 0-DTE long call's entire life *is* the final
hours, there is no "before the final weeks" to be out by, and its target plus the
ledger's expiry settlement already bound it. The alternative, a threshold
expressed as a *fraction* of `dte_at_entry`, would manage those positions but is
un-sourced invention; it is recorded here and not built.

## Scope: DEBIT rows only

`manage_ledger_trades` skips credit rows. The ledger's credit spreads are equally
ruleless, but handing them the credit rules would change how a second book exits,
with its own measurement attached — the same reason the credit spreads have no
`manage_dte`. D3 asked for long options and debit spreads.

## Ordering and absence

**The exit pass runs BEFORE the expiry settlement**, and that is load-bearing: a
position at its target *on* its expiration day should book the target it reached,
not an intrinsic settlement — `should_settle` fires from 15:00 CT while the target
may have been hit hours earlier.

Three absences are skips rather than actions: **no mark** (a zero would satisfy a
zero-threshold rule and record a fabricated close price), a P&L with **no value**
to write, and an **expired** row (`expire_ledger_trades` owns expiry and settles
at intrinsic). One bad row never aborts the pass, and an exit-pass exception
cannot cost the ledger its republish.

⚠ **There is no market-hours gate, deliberately — it matches the account.**
`paper_engine.run_manage_cycle` has none either, so pressing **Run manage cycle**
off-hours reprices and can act on both books against a stale chain. Gating only
the ledger half would make one button behave two ways on the same press. The
scheduled path cannot hit it: `paper_cycle_due` fires only on a trading day
between 09:00 and 14:00 CT. (`_reprice_open_pnl`, the *display* reprice, does
gate — it runs on every page publish rather than on an explicit request.)

## What is NOT built

- **The `account` option.** D3 offered "move them into the account, or give the
  ledger a manage cycle". The account reserves buying power against
  `max_loss_total` and holds share lots; a debit position's risk is simply the
  cash paid, and `reconcile_buying_power` recomputes reservations as
  `Σ OPEN max_loss_total`, so admitting debits there is a change to the cash
  mechanism, not a routing change. The ledger already had the cycle.
- **A percent-of-debit stop level** — off, by the source.
- **A fraction-of-horizon time exit** — the only rule that would manage the
  short-dated inventory, and un-sourced.
- **Outcome data.** There is none: `signals.db` holds only PCS/CCS/IC and the
  ledger is empty, so these levels are *sourced*, not fitted. When the ledger has
  closed debit trades, that is the first thing to measure — and it is exactly why
  the levels are config rather than code.
