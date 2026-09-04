# Captured-trade score — daily / weekly / monthly performance (design)

**Date:** 2026-09-04
**Scope:** a Daily / Weekly (WTD) / MTD performance section for **captured
signals** in the EOD report, scoring from **2026-09-01**.

## What this is, and what it is not

`/options/captured` tracks Market Scanner signals over time to see whether they
would have worked. It already prints a **today-only** footer (opened / closed /
booked P&L / open P&L). What it has never had is a record over longer periods.

The EOD report already renders exactly that table — Realized P&L, Closed (W-L),
Win %, Opened, Credit across Daily / Weekly (WTD) / MTD — for **two** books:
`normalize_trades(kind="ledger")` (the manual Paper Ledger) and
`kind="driver"` (Claude's). Captured signals are simply not one of them. This
adds the third.

⚠ **This scores the scanner's picks under the auto-manage rules. It is not an
account P&L**, and the section must say so — see §4.

## 1. Nothing new is recorded

`signal_outcomes` already carries `close_date` (indexed), `realized_pnl` and
`exit_reason`, joined to `signals.first_seen_date` for the open date. Measured on
prod: **868 outcomes spanning 2026-06-15 → 2026-09-04**.

So "starting 1 Sep" is a **floor on what the report counts**, not the point where
recording begins — which is why the MTD row can be backdated to 1 September the
moment this ships, rather than starting to accumulate from the build date.

The 2026-06-15 → 2026-08-31 history stays excluded. The period windows never
reach it anyway; the floor is what guarantees that.

## 2. The service publishes rows, Tier-1 buckets them

`compute.captured_closed_today()` is **today-only** (`get_outcomes_for_date`), so
it cannot feed a weekly or monthly row. `options_svc` gains
`captured_performance()`, publishing **`cache:options:captured_perf`** beside the
existing captured views.

It publishes **normalised rows, not aggregates.** Aggregating service-side would
duplicate `period_buckets()`, which already exists in Tier-1, is already tested,
and already produces the exact table shape the other two books use. Rows keep the
new code to a reader and a normaliser.

Row shape is what `normalize_trades` needs: `symbol`, `strategy`, `trade_type`
(the `scanner_type`), `status`, `entry_date`, `exit_date`, `realized_pnl`,
`credit`.

⚠ **`/eod` is Tier-1 and may not open SQLite** — the allow-list is explicit about
zero `sqlite3`. The rows reach it through Redis or not at all.

## 3. ⚠ The window is `min(month_start, week_start)`, not month-to-date

The obvious bound is month-to-date, since MTD is the widest row. It is wrong.

On **1 October**, the WTD row starts Monday **28 September** — before the month
start. A month-to-date window would return no rows for 28–30 September and the
weekly row would silently under-count, on the first days of every month, with
nothing on screen to say it had. The window is therefore the earlier of the two
period starts, floored at 2026-09-01.

This also bounds the payload: at most ~5 weeks of closes, which is the documented
lesson from `driver_account_view` publishing every closed trade ever.

## 4. Two things the section must state

**The dollars are per ONE contract.** `close_signal_manually` computes
`realized_pnl = (entry_credit − exit_value) × 100`, and a captured signal is
never sized — `/desk` already refuses to print a quantity for one, on the grounds
that a printed number would be the page inventing a position. So `Credit` is
`entry_credit × 100` on the same one-contract basis, and the section says the
figures assume one contract per signal. Without that line, −$1,251 reads as an
account loss.

**It scores picks, not trades taken.** These signals were tracked, not traded.

## 5. What it will show tonight

Measured on prod, 2026-09-01 → 09-04:

| period | realized | closed (W-L) | win % |
|---|---|---|---|
| Daily (09-04) | **+$329** | 4 (4-0) | 100% |
| Weekly (WTD) | **−$1,251** | 45 (5-40) | 11% |
| MTD | **−$1,251** | 45 (5-40) | 11% |

By exit reason: `DELTA_STOP` 17 (−$975) · `BREAKEVEN_STOP` 13 (−$154) ·
`TIME_STOP` 8 (−$115) · `EXPIRED` 4 (**+$329**) · `MONEY_STOP` 3 (−$336).
By type: 0DTE −$636 / Swing −$615.

**The only four winners expired; every managed stop lost money.** That is the
finding, and the report exists to keep showing it rather than to flatter it.

## 6. Out of scope

- **No inception ("Since 1 Sep") row**, by decision. Consequence, recorded so it
  is a choice rather than a surprise: in October the MTD row resets to 1 October
  and September's score stops being visible anywhere. One extra row if wanted.
- **`/options/captured` is unchanged** — it keeps its today-only footer.
- **No regular-hours filter.** `calibration.load_rows` filters to
  `is_regular_hours` because 223 of 819 historical rows were captured off-hours
  and priced off the prior close. That filter is moot here: `signal_recorder`
  now gates capture on regular hours, and every September close measured was
  captured between 10:17 and 15:02 CT. Worth knowing, not worth re-implementing.

## 7. Verification

Not browser-verified — a worktree resolves to prod and would bind `:8500`. The
`webgui` suite plus `options_svc`'s own are the gate. The rendered numbers are
checkable against the prod query in §5.
