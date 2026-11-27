# The paper books on a half day — design

2026-11-27 (the day after Thanksgiving, a half day that closes at 12:00 CT).

## The problem

`[early_close]` in `config/sessions.toml` names the half days, and
`market_calendar.session_close_on(day)` returns 12:00 on one. Until now the
paper books knew nothing about it. Every gate they use is
`market_calendar.is_regular_hours`, which still says the session runs until
15:00 on a half day. That is a deliberate invariant: callers move to
`session_close_on` one at a time. The paper books had not moved yet.

Here is what that meant on 2026-11-27, measured on prod:

* **Idea marks** kept running every 5 minutes after 12:00, on post-close
  quotes. One posted idea was closed at its stop at 12:01, booking −$692.20 from
  a quote nobody could trade against.
* **Tracked structures** kept being recorded and marked, and were closed on
  their exit rules.
* **Captured signals** kept being repriced and closed by their exit rules, and
  the scan kept publishing and capturing new signals until 15:00.
* **The Account's hourly cycle** would have run at 12:00, 13:00 and 14:00 against
  quotes from a closed market. There was no activity today only because nothing
  was due.
* **Expiries** settled at 15:05, three hours after the session that set their
  price had ended. The answer was right, because `settlement_last` reads the
  regular-session last. But until then every view showed the day's expiring
  positions as open, and so did the 12:15 end-of-day report.

## The rule

On every day, a paper book stops acting at the session's actual close, and
expiries settle then:

1. **One predicate**, `market_calendar.is_session_open(now)`. It is
   `is_regular_hours(now)` that ends at `session_close_on(day)` rather than at
   15:00. On a regular day it is exactly `is_regular_hours`, so a regular day
   behaves as it did. `is_regular_hours` and `session_at` are unchanged.
2. **Settlement**: `paper_engine.should_settle` settles an expiry-day position
   at or after `session_close_on(expiry)`, not at `hour >= 15`. On a regular day
   that is still 15:00 CT (16:00 ET, the OCC strike). On a half day it is
   12:00 CT (13:00 ET), which is when OCC strikes a half day.
   `settlement_underlying` is unchanged: on the expiry day it reads the quote's
   `regularMarketLastPrice`, which on a half day is the 12:00 close.
3. **The settle pass** (`[slots.paper_settle]`) is placed relative to the close.
   Each slot is "this long after the regular close", and it moves with the day's
   actual close. On a half day, `close = "15:05"` fires at 12:05 with the same
   grace. This adds no new key: the slot is the same five minutes after the
   close. It is still settle-ONLY.
4. **The Account's hourly cycle** (`paper_cycle_due`) skips any hour at or after
   the day's close. On a half day it runs at 09:00, 10:00 and 11:00, and not at
   12:00, 13:00 or 14:00.
5. **Callers that move to `is_session_open`**:
   * idea marks (`scheduler.idea_marks_due`)
   * the site's Ledger live pass (`scheduler.site_live_due`) and the Ledger's
     streamed marks (`ledger_live.evaluate`)
   * tracked structures' marks and exits (`tracked.manage_cycle`)
   * captured and tracked recording (`signal_recorder`)
   * the scan's signal publish (`scanner_engine._signals_session_open`), so that
     publish and capture still agree
   * the shadow ideas (`handlers.run_idea_shadow`)
6. **Captured signals' manage cycle** (`compute.run_captured_manage_cycle`)
   becomes settle-only from the close. It still runs on its scan-window clock,
   and it still settles. But after the close it no longer reprices or closes a
   signal on its exit rules.

## Decisions

* **A new predicate, not a change to `is_regular_hours`.** About a dozen other
  callers read `is_regular_hours`: the GEX collector, the flow-sides OI read,
  the market console, live-order placement, traded premium, and others. Their
  half-day behaviour is a separate question, so each moves when someone decides
  it should.
* **The settle slot moves with the close; it gets no `half_day_close` key.** A
  separate key would need a rule for which days it fires on. "Five minutes after
  the close" already says what the slot means.
* **The captured cycle's after-close rule also applies on a regular day,
  15:00–15:15.** This is the one change a regular day sees. Until now, a captured
  signal could be closed on its exit rules in the 15 minutes after the 15:00
  close, at an after-close option quote. The invariants already say that is not
  a market (`[slots.paper_settle]` is settle-only for this reason).
  `tracked.manage_cycle` already stopped marking at 15:00. The captured cycle
  was the one book that did not.
* **Pre-open is left alone.** The captured and tracked cycles also run from
  08:00, before the open. That is a separate question and is not changed here.

## What a reader sees

On a half day, every paper book is still from 12:00. The day's expiries have
settled by 12:05, so the 12:15 end-of-day report shows them settled. The board
publishes no new signals after 12:00.
