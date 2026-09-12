# Wire the profit-lock ladder (C2)

*Design, 2026-09-12. Gap assessment item **C2**.*

## What the assessment asked for

> **C2. Wire the profit-lock ladder and trial the lifecycle on the manual book.**
> `[trail].ratchet_ladder` is built and tested with no caller, and
> `manual_paper_lifecycle_enabled` exists and defaults off. Running one book each
> way gives a direct comparison of "close at 50%" with "arm break-even and
> ratchet".

**Two separate changes**, and the measurement below says to ship one and put the
other to the operator.

## The replay

Every closed captured signal carries a mark series (`signal_marks`, 58,895 rows
with `unrealized_pnl`), so both policies can be replayed against the **same real
price path** rather than argued about. Restricted to the 281 closed signals with
at least 10 marks — a 1-mark series cannot express a stop at all — with a
slippage haircut applied to every ladder exit, because a real stop fills *through*
its level and a 5-minute mark series cannot see a gap:

| policy | total | vs break-even |
|---|---|---|
| close outright at +50% | **+$4,788** | −$3,385 |
| **break-even stop (today's lifecycle)** | **+$8,173** | — |
| **ratchet 65/80 (the opt-in ladder)** | **+$8,556** | **+$383** |

*(slip = 10% of credit; `n` = 281)*

### Finding 1 — the ratchet beats the break-even stop it replaces, modestly and consistently

+$383 (+4.7%). Better on 43 trades, worse on 18, identical on 220. And it wins in
**every month and both scanner types**:

| slice | break-even | ratchet |
|---|---|---|
| 2026-07 (n=24) | +$1,857 | +$1,857 |
| 2026-08 (n=225) | +$6,525 | **+$6,824** |
| 2026-09 (n=32) | −$209 | **−$125** |
| 0DTE (n=60) | −$493 | **−$369** |
| SWING (n=221) | +$8,666 | **+$8,925** |

⚠ **It reverses at extreme slippage.** A higher floor triggers more often, so it
pays the haircut more often: at 0% slip the ratchet leads by $579, at 25% by $89,
and at **50% slip it LOSES** (+$6,526 vs +$6,924). 50% of credit is an
implausible fill on a $0.40 spread, but the direction of the sensitivity is worth
knowing — this edge is real and small, not free.

**The structural argument matters as much as the number:** rule 3 computes
`stop_level = max(be_level, _locked_profit_level(...))`, so the ladder can only
ever raise a stop that is **already above break-even**. It cannot increase loss
exposure on any path. Its cost is exiting a recovered winner early — which is
what the 18 "worse" trades are — and its benefit is banking 25–50% of credit
instead of 0% on a collapse.

**So the ladder ships on**, as `[trail].active = "ratchet"`.

### Finding 2 — the lifecycle trial is the bigger, period-dependent change, and is NOT flipped

Closing outright at +50% is worth **+$4,788** against the lifecycle's **+$8,556** —
a $3,768 gap, far larger than the ladder's own contribution. The mechanism is
explicit: of the 205 trades that reached +50%, **118 ran on to ≥95% of credit**
(expiring worthless) while **61 fell back below +50%**, so holding past the target
won about 2:1 in this sample.

⚠ But that is the claim that does *not* survive slicing:

- **September reversed it** — close +$74 against ratchet −$125, and the ratchet was
  better on 0 of 9 trades that differed.
- **0-DTE gets nothing from it** — −$456 against −$369, on 60 trades. A same-week
  trade has no room to ratchet.
- The sample is three months in which this book's realized P&L was positive, and
  "hold longer" is exactly the policy that flatters such a period.

Flipping `manual_paper_lifecycle_enabled` changes how **every** position in the
manual book exits. That is the operator's call, with these numbers, not a
drive-by — the same treatment the IV-rank floors and the driver's sizing appetite
got.

## ⚠ A data defect found while replaying, and a check on an earlier claim

`realized_pnl` equals the last mark exactly on most rows, but **5 `MANUAL_CLOSE`
rows (all MU, 2026-06-15..17) book exactly `entry_credit × 100`** — the full
credit, as if the spread expired worthless — while their last mark shows them
**underwater**. Worth $3,010 of phantom profit. More broadly, 130 of 388
`MANUAL_CLOSE` rows book exactly the full credit, and `MANUAL_CLOSE` accounts for
**+$50,102** of the book's reported P&L against +$11,664 for every other reason
combined. That is recorded as a finding to investigate rather than a proven
defect: a manual close *can* legitimately differ from the last mark.

**It does not move the B2 result**, which was checked rather than assumed. Mean R
by entry IV rank, three ways:

| filter | 0–44 | 45–54 | 55+ |
|---|---|---|---|
| all rows (as published) | −0.150 | +0.187 | +0.251 |
| excluding the 5 suspect rows | −0.150 | +0.187 | +0.250 |
| excluding **every** `MANUAL_CLOSE` row | −0.150 | +0.197 | +0.252 |

## The shape

**`[trail].active`** names the ladder in force (`"default"` or `"ratchet"`), read
through `shared.trade_mgmt.active_trail_ladder()`. A name rather than a fourth
array, so the two ladders stay side by side in the file and switching back is one
word.

**Two call sites supply what the mechanism has always needed** — `trail_ladder`
and `peak_pnl_frac`, which `_locked_profit_level` has taken since it was written
and nothing ever passed:

- `paper_engine.run_manage_cycle`'s **lifecycle branch**, where the peak is
  `mfe / credit_total`. ⚠ The **freshly computed** `mfe` from this cycle's
  excursion update, not `pos["mfe"]`, which is the row as fetched and therefore
  one cycle stale — on the cycle where a trade peaks and collapses, the stale
  value is the difference between locking 50% and locking nothing.
- `compute.run_captured_manage_cycle`, where the peak comes from a new
  `signal_db.peak_unrealized(signal_id)` — one `MAX(unrealized_pnl)` over the
  marks already being written. `build_mark` has accepted `trail_ladder` and
  `peak_pnl_frac` as keyword arguments since it was written; they were simply
  never passed.

**No peak means no lock.** `_locked_profit_level` already returns 0.0 for a
missing peak, so a position with no excursion history keeps exactly today's
break-even behaviour — which is every position opened before this change.
