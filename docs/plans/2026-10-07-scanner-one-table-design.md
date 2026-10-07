# Market Scanner: one table per tab

**Date:** 2026-10-07 · **Status:** built the same day, on the owner's two answers.

## What changed

The 0-DTE and Swing tabs each held two tables behind a two-way switch: *Credit
spreads* and *Other structures* (debit spreads, straddles and strangles,
butterflies and condors, calendars, ratio backspreads). Each tab now holds ONE
table with both, and the switch is gone. The public Option Signals page is this
same page, so it changed with it.

## Owner decisions

| Question | Answer |
|---|---|
| Layout | One table. Credit spreads are rows in the other structures' columns, and *Credit spreads* is one more family tick box |
| Default order | One ranking by score across both kinds |

## The thing this gives up, and what stands in for it

The two tables were kept apart on purpose (design of 2026-10-06): a credit
spread carries the premium composite, every other structure the Fit + Quality
score, and the two are on different scales. A single ranking compares them. The
owner chose it knowing that, so the page says it in two places rather than
hiding it:

- a note under the tick boxes (`scanner_structures.SCALE_NOTE`);
- a hover on every score naming the scale it is on (`_score_tip`, drawn by
  `scanner._SCORE_SLOT`).

Unticking *Credit spreads*, or ticking only it, ranks one kind alone, which is
what the switch used to do.

## How a credit spread becomes a row of the shared columns

The scanner publishes a credit spread per SHARE with its strikes in fields; the
other rows are per CONTRACT with a `legs` list. `scanner_structures.credit_rows`
draws one in the shared columns:

| Shared column | From |
|---|---|
| Strategy, Bias | the engine's own names (Put Credit Spread, Call Credit Spread, Iron Condor) and bullish / bearish / neutral |
| Legs | the strike fields, the short leg of each wing first |
| Debit/Credit, Max P, Max L | `credit` and `max_loss` times 100 |
| R:R | `credit / max_loss`, a ratio (the old column was a percentage) |
| BE | `breakeven`: a number, or an iron condor's `"put/call"` string |

A figure the signal lacks is a dash, never a zero.

## What did not change

- **What the service publishes.** The day envelope still has five lists. The
  paper Account enters from `signals_0dte` / `signals_swing` alone, and the
  hourly trade idea reads the lists, not the page.
- **The build's per-list rows.** `_build_populate` stamps each list as before
  (stale, the public Paper gate, persistence, checks) and then merges each
  tab's lists (`scanner_structures.merged`). `built["tables"]` holds the SAME
  row dicts as `built["rows"]`, so every stamp shows.
- **The Trade detail panel.** One click handler serves both row shapes and
  reads which from the signal: a normalized candidate has `legs`, a scanner
  credit spread does not.
- **The public rules.** The shared build carries the merged tables, and the
  Paper gate, the quotes switch and the read-only rows work as before.

## Left behind

`scanner.signal_columns` has no caller on a page any more (the Symbol page
still uses `signal_rows`, with columns of its own). It is kept, with its tests,
rather than removed in this change.

## Tests

`webgui/tests/test_scanner_combined.py`. The tests that pinned the switch and
five tables now pin three tables, one slot loop and one click handler; each
changed assertion says what it was before.
