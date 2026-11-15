# The profit trail: protecting a winner in every paper book

2026-11-15. Settings: `config/trade_mgmt.toml [protect]` (Settings → Configuration →
Profit trail). Measurement: `tools/replay_profit_trail.py`.

## Why

The operator asked for "a trailing strategy to protect profits in Captured Signals,
Paper Account and Paper Ledger". What the books did before this change:

| Book | What protected a winner |
|---|---|
| Captured signals | Nothing short of the +50% target, which only ARMED a break-even stop and rode on (the manual lifecycle path, always on for captures) |
| Paper Account | The target closed at +50% (switch off), or armed break-even and rode the profit-lock ladder (switch on, as prod runs) |
| Paper Ledger, the operator's credit trades | No automatic exit at all |
| Paper Ledger, debit trades | The debit stop and target only |
| Paper Ledger, self-managed rows (ideas, condor, shadow) | Their own stop and target lines only |

A trade that went +40% and came back to a loss was left to do so everywhere.

## What the data said (prod backup of 2026-11-13, 388 closed captures)

Closed PCS/CCS/IC captures, hand closes and first-mark time stops left out, 10% of
the credit lost on every replayed exit plus commissions, intervals bootstrapped by
capture day:

| Rule | $ a trade | vs the real outcome (95%) |
|---|---|---|
| The real outcome (arm at +50%, ride) | −6.30 | — |
| Take the profit at +50% | +7.60 | +2.64 … +31.21 |
| +50% target **and** trail 25% / 10% (shipped) | +8.32 | +1.85 … +33.49 |
| +50% target and trail 20% / 10% | +8.91 | +2.05 … +34.05 |
| Trail 25% / 10%, no target | −1.08 | −1.58 … +14.43 |

- **The take-profit carries almost all of the gain** (about $14 a trade over riding).
  A trail used INSTEAD of a target loses to it at every setting tried.
- **The trail on top of the target adds about $1 a trade** — inside the noise. At 25%
  slippage it adds nothing (+0.31 vs +0.55 for the target alone). At the hourly marks
  the Account and Ledger see, +11.03 vs +10.00. On 0-DTE alone (77 trades) every rule
  still loses money; the trail narrows it from −13.74 to −12.43.
- **Why riding lost:** 27% of the losers had been +50% up. Their break-even stops fired
  on single marks priced off the wide opening and closing quotes (08:30–08:45 and
  14:45–15:00 Central), then the trade came back, or the stop booked at a far worse mark
  than break-even.

So the trail is a modest protection for the trades short of the target, not an edge; the
quiet windows and the captured take-profit are what the measurement supports. This is
a measurement of the app's paper book, not advice.

⚠ The replay can only exit EARLIER than the real close (the marks end there), so a rule
that would hold longer than the app did cannot be measured with it.

## The rule

Once a trade's best mark reaches `trail_start` (25%) of its base, it is closed when its
P&L falls `trail_giveback` (10%) of that base below the best — and never below
break-even plus commissions (`be_level`).

- **Base:** the credit × 100 for a credit trade; for a debit trade the same base its
  target uses (`_debit_target_base`: max profit when defined, else the debit paid).
- **Code:** `TRAIL_STOP` (`TRAIL` on the Ledger's self-managed rows). TAKE_PROFIT when
  the exit is at a profit, CUT otherwise.
- **Where it sits:** credit trades in rule 3, beside the break-even/ladder stop — the
  higher of the two levels governs, and the money stop, time stop and delta stop come
  first and are never deferred. Debit trades: rule 1b, after the debit stop.
- **Quiet windows** (`quiet_windows`, Central, start inclusive, end exclusive): the
  PROTECTIVE stops — the trail and break-even — wait them out. A mark with no time is
  never quiet, so a caller that does not say when gets the stops it always had.
- **A missing P&L is never 0 to the trail.** `recommend` reads a missing P&L as 0 for
  its other rules; 0 sits under every armed trail level.
- **No best is "not armed",** never "armed at 0".

## Per book

| Book | Switch | Best it reads | Change |
|---|---|---|---|
| Captured signals | `captured` | `signal_marks` peak, with the mark being built taken in | Also: `captured_at_target = "close"` closes at +50% (`TARGET_HIT` joins `_CAPTURED_CLOSE_CODES`) instead of arming break-even and riding; `"ride"` keeps the old path |
| Paper Account | `account` | `mfe` ÷ quantity (position-level → per contract) | Both lifecycle modes. The lifecycle switch is the operator's and is not changed |
| Paper Ledger, own trades | `ledger`, `ledger_take_profit` | new `trades.mfe_pnl` / `mfe_at`, raised only (`trades_db.raise_peak`) | Credit trades get the trail and, since the operator's follow-up the same day, the +50% target (`protect_only(take_profit=)`). No loss stop: cutting a loser on the operator's own trades stays theirs. Debit trades: their rules plus the trail |
| Paper Ledger, self-managed | `ledger_self_managed` | the row's `mfe_pnl` (`idea_ledger._raise_mfe`) | Checked after the row's own STOP and TARGET lines |

A book that is off passes no inputs (`protect_inputs` returns `{}`), so its behaviour is
exactly what it was.

## Approaches considered

1. **A trail instead of the target.** Measured worse at every setting; rejected.
2. **Extend the existing profit-lock ladder below the target.** The ladder is stepwise
   and only arms at the target; a continuous give-back is simpler to read and to set.
3. **Target plus a trail below it (chosen).** Keeps the measured gain of the target,
   adds the protection asked for, and leaves the ladder in charge above the target.

For the opening and closing quotes: **confirming a stop on a second mark** was weighed
against **deferring it through fixed windows**. Deferral was chosen — the windows are
where the bad marks were measured, a confirmation costs every stop a mark (five minutes
for captures, an hour for the Account), and the windows are a setting.

## Follow-up the same day (the operator's requests)

- **Close at +50%.** The Ledger's own credit trades take the profit at the
  structure's `tp_frac` (`[protect] ledger_take_profit`, on). The paper Account's
  close-or-ride choice stays the operator's Settings switch ("Manual paper:
  break-even lifecycle"); prod runs it ON, so the operator turns it off there.
  Code does not override a switch the operator owns.
- **Stop and Target columns** on the Ledger, Captured Signals and the Account.
  The levels come from `signal_recommender.exit_levels`, which reads the SAME ctx
  each manage cycle builds, and a test walks a P&L grid to check `recommend`
  agrees with them; `services/options_svc/exit_levels.py` writes them on each
  OPEN row as position dollars where the three views are published. One Stop
  column shows the stop in force now (it rises to break-even or the trail), with a
  Stop rule column naming it. Time, delta and manage-at-DTE exits are not P&L
  levels and are not shown.
- **Live order take-profit 70%.** `config/tos_order.toml [debit] target_pct`
  100 -> 70, for the ThinkorSwim copy and Send Live Order alike (one setting).

## Effects to know

- Captured-signal outcomes now close at +50%: calibration and score studies over
  `signal_outcomes` see a change of exit rule from 2026-11-15. Compare across it with care.
- The weekly idea review rates a trail exit `trailed` and counts it apart from hand
  closes; the site's idea record names it ("Closed on its trailing stop").
- The settings are validated: a bad value is the SHIPPED one, never a looser one, and a
  give-back at or above the start (which would close on the first mark) reverts both.

## Files

`shared/trade_mgmt.py` (`protect`, `in_quiet_window`) · `options-scanner/signal_recommender.py`
(the rule, `protect_inputs`, `protect_only`) · `options-scanner/paper_engine.py` ·
`options-scanner/trades_db.py` · `services/options_svc/profit_trail.py` (the captured and
Ledger wiring, kept out of `compute.py`) · `services/options_svc/idea_ledger.py` · the exit-word
readers (`site_ideas.py`, `ideas.js`, `eod.py`, `idea_review.py`, `weekly_idea_review.py`).
