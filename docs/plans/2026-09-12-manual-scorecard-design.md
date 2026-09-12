# A track record for the book that trades (C5)

*Design, 2026-09-12. Gap assessment item **C5**.*

## What the assessment asked for

> **C5. Trade plan and scorecard.** Snapshot the rules in force at entry and a
> free-text thesis on each position. Give the manual book the win-rate and
> profit-factor scorecard the driver has (`driver_perf.py`).

**Two unrelated changes under one number.** The scorecard ships; the trade-plan
snapshot does not, and the reason is below.

## The scorecard — smaller than it looks, and pointed at a real gap

`driver_perf.build_scorecard(positions, snapshot)` is already **pure** — it takes
a list of rows and a snapshot, and knows nothing about which book it is scoring.
So the manual account needed an accessor, not an implementation:
`compute.manual_account_perf()`, mirroring `driver_account_perf()` on the default
DB (`db_path=None` **is** the manual account throughout this engine, the same
convention `manual_analytics` and `paper_account_view` use).

⚠ **The manual account is the book that auto-trades every captured signal, and it
had no track record on screen at all** — no win rate, no profit factor, no
breakdown. The driver's isolated book has had all three since `driver_perf.py`
was written. That asymmetry is the gap.

⚠ **`manual_analytics()` already existed and nothing consumed it** — a third
"built, never called" found in this audit, after the ratchet ladder (C2) and the
IV history (C3). It returns the equity curve and MAE/MFE analytics rather than the
scorecard, so it is a *separate* unused surface and is left as one; recorded here
so it is not rediscovered as new.

### The one thing that is new rather than moved: `by_exit_reason`

`build_scorecard` broke P&L down by symbol and by strategy. It now also breaks it
down by **how the trade ended** — and that axis exists because of something
measured hours earlier while replaying the profit-lock ladder:

| exit reason | share of the captured book's reported P&L |
|---|---|
| `MANUAL_CLOSE` | **+$50,102** |
| every other reason combined | +$11,664 |

with **130 of 388 `MANUAL_CLOSE` rows booking exactly `entry_credit × 100`** — the
full credit, as though the spread expired worthless — and five of them (all MU,
2026-06-15..17) contradicted by their own last mark. Split by symbol and by
strategy, that is invisible. Split by exit reason, it is the first row you read.

**It is not asserted to be a defect.** A manual close can legitimately differ
from the last mark, and this change does not decide the question — it makes the
question visible on the screen where the book is read.

## What it looks like

**The driver page keeps its full card.** The Paper Account page gets **one line**
above its tables — `7 closed · 71.4% win · +$420.00 realized · 2.00 profit
factor` — because that page already carries the account cards, the open positions
and the fills, and a second multi-table block would bury them.

Two copy rules, both the same shape as the rest of the app's empty states:

- ⚠ **Blank until something has CLOSED.** A fresh or all-open book would otherwise
  read "0.0% win", which says the book *loses* rather than that it has no record.
- ⚠ **An undefined profit factor is omitted, not printed.** `None` means "no
  losses yet" — gross win over gross loss is undefined — and an em-dash
  mid-sentence reads as a rendering fault. The driver page shows "—" because
  there it is a *labelled chip*, where the absence is legible.

**`pages/scorecard.py`** now holds the pure render builders, moved out of
`pages/driver.py`. Two pages drawing the same scorecard means a second page
reaching into the driver PAGE for its vocabulary, which is the wrong shape — the
same reasoning behind `pages/fmt.py` and `pages/copy.py`. `driver.py` imports them
by name, so `driver.scorecard_headline_chips` still resolves for the page body and
the 25 existing assertions in `test_driver_monitor.py`.

⚠ **Two shadowing bugs caught during that move, both silent:**

1. The palette was restated. `driver.py` defined
   `PNL_GREEN, PNL_RED, PNL_NEUTRAL = "#66bb6a", "#ef5350", "#bdbdbd"` *after* the
   new import, so the assignment won — and the first draft of `scorecard.py`
   carried the Simulator's payoff green/red instead. A shared module quietly
   holding different colours is precisely the divergence it exists to prevent; the
   hexes moved verbatim and a test pins them.
2. `portfolio.py` already has its own `_money` (the **unsigned** account-card
   formatter — "Equity $24,184.20"), so importing `money as _money` was shadowed
   by it and the track record printed its realized P&L **without a sign**. The
   page imports the module now, not the name.

## What does NOT ship: the trade-plan snapshot

> *"Snapshot the rules in force at entry and a free-text thesis on each position."*

Two things, and neither is a small addition:

- **The rules in force at entry** is now a much larger object than when the
  assessment was written. A position's exit is governed by `[stops]` overlaid by
  `[structures.*]`, the active `[trail]` ladder, `tp_frac_for(strategy)`,
  `cut_dte`, the delta triad, and `manual_paper_lifecycle_enabled` — and six of
  those moved **today**. Snapshotting them needs a decision about granularity (the
  resolved values? the config file's hash? which keys?) that determines whether
  the column is useful or just large, and that decision wants its own design.
- **A free-text thesis** is a new input surface on a book whose positions are
  opened *automatically* by the entry cycle. There is no moment at which a human
  is present to type one, so the feature implies a workflow change (a review step,
  or an edit-after-the-fact field) rather than a column.

Both are real, and both are bigger than "give the manual book the scorecard".
They stay open.
