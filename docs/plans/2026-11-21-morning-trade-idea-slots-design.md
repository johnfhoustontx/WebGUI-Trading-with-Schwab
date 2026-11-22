# More morning trade ideas, and a larger Paper Account risk cap — design

**Date:** 2026-11-21 · **Status:** built, not promoted · **Operator decisions:** both changes below.

Two operator requests from one session, shipped together because both are config
moves with no new mechanism.

## 1. The trade idea posts every 30 minutes in the morning

### What changes

`config/sessions.toml [slots.trade_idea]` goes from 7 slots to 10:

| | Before | After |
|---|---|---|
| Morning | 08:40, 09:40, 10:40, 11:40 | 08:40, **09:10**, 09:40, **10:10**, 10:40, **11:10**, 11:40 |
| Afternoon | 12:40, 13:40, 14:40 | unchanged |

Nothing in the code changes: `scheduler.trade_idea_due` walks whatever slots the file
lists, and `trade_idea.pick` already refuses a trade posted earlier the same day and
prefers a symbol not yet posted. Every new slot is 10 minutes past a quarter hour,
the same place the :40 slots sit relative to their scan, so
`test_every_slot_reads_a_finished_scan` holds. A new test pins that `grace_min` (10)
stays under the shortest gap between slots (now 30 minutes).

### What was measured, and why the afternoon was kept

The operator first asked for more morning posts and fewer afternoon ones. Measured on
the prod backup of 2026-11-20 20:00 (read-only, `immutable=1`):

1. **Posted ideas** (`trades.db`, `source = 'trade_idea'`, settled, 2026-10-21 to
   11-19, 22 sessions): morning (posted before 12:00 CT) 66 of 86 won, 76.7%, mean
   +0.30 of max loss; afternoon 40 of 65, 61.5%, +0.12. Fisher p = 0.0495 on the win
   rate; Mann-Whitney p = 0.10 on the return.
2. **But the gap is one week.** 82% of those ideas are bear put debit spreads. In
   three of the five weeks the afternoon matched or beat the morning; the gap comes
   from 2026-11-02 to 11-06, when the 13:40 and 14:40 posts lost about 0.42 of max
   loss each. Outside that week 14:40 was the best slot of the day (82%, +0.40).
   Day by day the morning was better on 13 of 22 days (sign test p = 0.52).
3. **The larger sample shows no time-of-day edge.** Captured scanner signals
   (`signals.db`, 2026-06-15 to 11-20) filtered the way the idea filters (Strong or
   Good, at least 1 DTE, bounded loss, captured in the regular session), with the
   TIME_STOPs that fire within 15 minutes of capture removed (the known first-mark
   artifact): 779 trades, morning 62.7% won / mean +0.12, afternoon 60.3% / +0.13;
   Fisher p = 0.57, Mann-Whitney p = 0.68; the morning better on 29 of 51 days with
   both halves (p = 0.40). Captured bear puts alone: morning 79% (48), afternoon 88% (8).
4. **The paper Account** (`paper_account.db`, 287 session entries): morning 44.9%
   won, afternoon 29.7% (p = 0.013), but both halves near break-even or below on
   return (mean 0.00 and −0.05 of max loss). Its exits may share the first-mark time
   stop; that is not yet checked.
5. **The shadow ideas** (`source = 'idea_shadow'`, picks the posted feed did not
   take, 2026-11-06 onward): morning 74 to 95% won in each week; afternoon weaker.
   They are the best evidence that a second and third pick from the same morning
   scans still holds up, which is what the new slots take.

No sample shows the morning worse, so adding morning posts is supported. No
afternoon hour is weak in every sample (12:00 is the weakest for the posted ideas
and average for the captured ones; 14:00 the reverse), so the operator chose to keep
all three afternoon posts.

**Caveats.** Captured signals are mostly credit spreads, timed at their FIRST
appearance (which leans early: 233 of 779 in the 08:00 hour), and closed by the
scanner's exit rules rather than the idea's own stop and target. Ideas on one day
move with the market, so 22 sessions is a smaller sample than 151 trades sounds.

### Effects

- **Paper Ledger:** about 3 more idea rows a day (they book with no cap and are
  marked every 5 minutes; the load is negligible). The weekly review and the site's
  results fill faster.
- **The shadow feed** never takes the posted idea's trade, so its morning pool
  shrinks, and `tools/idea_shadow_compare.py` should be read from 2026-11-21 on as a
  new period.
- **Wording:** the feature was "the hourly trade idea" in Settings, the manuals,
  `page_help.py` and the public site. User-facing text now says "the trade idea" and
  states the schedule; internal docstrings and test names keep "hourly" as a name.

### How to judge it

After about 20 sessions, compare the three new :10 slots with the :40 morning slots
on the same measure (win rate and return per dollar of max loss), and the afternoon
before and after.

## 2. The Paper Account's risk caps

The operator asked to raise the Account's per-trade risk from $750 to $1,500. Alone
that does nothing above $750: `book_caps` sums a new trade into its symbol, and the
symbol cap was $750, so every trade between the two would be refused. The operator
chose (from three options) to keep today's proportions:

| Account cap | Before | After |
|---|---|---|
| Per trade (`max_risk_per_trade`) | $750 | **$1,500** |
| Per symbol (`max_risk_per_symbol`) | $750 | **$1,500** — one full trade per name, as before |
| Per sector (`max_risk_per_sector`) | $1,500 | **$3,000** — two names per sector, as before |

The position counts, the 20% deployment cap and the $2,500 session drawdown are unchanged.

**The Ledger keeps its caps.** Both books used to share the symbol and sector caps
(`compute.ledger_book_state` took `paper_concentration.default_limits()` and replaced
only the per-trade cap). Raising them would have loosened the Paper button's book,
which was not asked for, so each book now has its own pair:
`ledger_max_risk_per_symbol` ($750) and `ledger_max_risk_per_sector` ($1,500), applied
by the new `paper_concentration.ledger_limits()`.

**Configurable now.** The symbol and sector caps were literals in `config_paper.py`;
under the standing rule they moved to `config/paper.toml [risk]`, read through
`shared/paper_limits.py`, with Settings → Configuration fields.

**A side effect to watch.** `max_risk_per_trade` is also the budget the Market
Scanner's width search sizes against (`scanner_engine.DEFAULT_MAX_RISK_DOLLARS`). At
$1,500 the scanner emits spreads up to twice as wide on high-priced names, so the
scanner's rows (and with them the trade ideas and the captured signals) can change.
The Strategy Finder and the Income Window size against the Ledger's cap and do not
move.
