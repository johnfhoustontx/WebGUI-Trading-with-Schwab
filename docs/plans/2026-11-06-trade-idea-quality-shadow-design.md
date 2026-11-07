# Quality-triggered trade ideas, in shadow — design

2026-11-06. The operator asked whether trade ideas could be suggested by the
quality of the trade rather than on the hourly clock, possibly more than one an
hour. This design is the first slice they chose: **shadow first**. Nothing is
posted. The rule's picks are booked into the Paper Ledger under their own tag
and measured against the clock posts.

## What was measured before designing

The 75 closed trade-idea trades in the Paper Ledger, 2026-10-21 to 2026-11-05
(12 sessions), from the 2026-11-05 prod backup on E: opened read-only. R is
realized P&L over the trade's max loss.

| Group | n | Win | Mean R |
|---|---|---|---|
| All posted ideas | 75 | 69% | +0.24 |
| Score 75-80 | 72 | 69% | +0.24 |
| Score 80+ | 3 | 67% | +0.23 |
| Grade Strong | 8 | 75% | +0.45 |
| Grade Good | 67 | 69% | +0.22 |

- **The composite score cannot be the trigger.** Every one of the 83 posts scored
  77-81: the best candidate in each scan sits near 79, so a "score above X" rule
  fires on every scan or on none. The August calibration said the same: the score
  is a floor, not a ranking.
- **Grade Strong is the one measured separator**, at about twice the mean R, on
  8 trades. On debit spreads and single options Strong means excellent on every
  quality gate. About 1 to 1.5 Strong rows a day reach `signals.db`.
- A model-derived "expected value" (probability of profit times the payoff) is
  near zero by construction, because the probability comes from the same option
  prices. It is not used.

## The rule (`[idea_shadow]` in `config/paper.toml`)

Evaluated once per published scan (the scheduler's 30-second tick sees
`cache:options:scan`'s version move; a scan already evaluated is skipped by its
timestamp, so a restart re-evaluates nothing). A trade is picked when ALL hold:

1. It is a trade-idea candidate (`trade_idea.candidates`, with the `trade_idea`
   block's `max_age_min`, `min_dte`, `min_score`, `other_structures`) at a grade
   at or above `min_grade` (`Strong`).
2. **New this scan**: its trade key (symbol, structure, expiry, legs) was not a
   candidate in the previous scan. Scan row ids carry a list index and are not
   stable between scans, so they are never used for this.
3. Not already posted or shadow-booked today, and (with `one_per_symbol`) its
   symbol was not.
4. Selling premium: the symbol's Vol Rank is at least `min_vol_rank` (50),
   through `shared/vol_gate.blocks` on the row's own `net_vega` sign. An absent
   reading or vega skips the check (the gate's own rule).
5. The combined feed would stay inside its limits: `max_per_hour` (2, rolling 60
   minutes), `min_gap_min` (20) since the last post, `max_per_day` (10). These
   count the clock posts and the shadow picks together, as the live feed would.

Among several, `trade_idea.pick`'s order chooses one (score, then grade). At most
one per scan. Picks are booked only in the regular session; outside it a scan
only refreshes the previous-scan keys. The first scan of a day seeds the keys
and books nothing, so a start-up never books a burst.

Liquidity (bid-ask width) is not in this slice: the scan's rows do not carry a
uniform bid and ask across the credit, directional and structure shapes.

## Where the picks go

- `idea_ledger.book(idea, now, source="idea_shadow")`. The row is
  self-managed (`SELF_MANAGED_SOURCES` in both tiers), so the same 5-minute
  marks, stop and target close it as a posted idea. `[idea_trades] enabled` does
  not stop shadow bookings; `[idea_shadow] mode = "off"` does.
- It is not an idea (`is_idea` is false): it never reaches the public site, the
  weekly review, the posted-idea banner or X. The Ledger page shows it under All
  with a **Shadow idea** tag; the end-of-day report and the Symbol page count it
  as a measurement, not as the operator's trade.
- `cache:options:idea_shadow` holds the day's state and the last decision with
  its reason, so a quiet day says why.

## The comparison

`tools/idea_shadow_compare.py --db <trades.db>` prints closed trades by source
(`trade_idea`, `idea_shadow`): n, win rate, mean R and P&L. Re-run after 15-20
sessions. The rule goes live only if the shadow picks beat the clock posts'
mean R; otherwise it is turned off and nothing was ever posted.

## Build steps

1. `idea_shadow.py` (pure): trade key, day state, novelty, limits, decision. Tests.
2. `normalize` carries `iv_rank` and `net_vega`; `idea_ledger.book` takes `source`.
3. `handlers.run_idea_shadow` + the scheduler's scan-version branch.
4. The source in both `SELF_MANAGED_SOURCES`, the Ledger tag, the end-of-day and
   Symbol page measured sources.
5. `paper_limits.idea_shadow()`, `[idea_shadow]`, Settings catalogue entries.
6. The comparison tool, the manuals, the CHANGELOG.
