# The daily SPY 0-DTE iron condor - design

2026-10-29. Requested by the user; the decisions marked **(user)** are theirs, those
marked **(my call)** were proposed in chat and accepted without change.

## Intent

A fixed, mechanical 0-DTE iron condor on SPY, opened every trading morning in the
Paper Ledger and left to run, so its results can be measured day by day. It is not
a signal and not an idea: there is no scoring, no filter, and no judgment in it.

## The trade

- **When (user):** every trading day at **08:45 CT** (09:45 ET).
  `[slots.daily_condor]` in `config/sessions.toml`, grace **10 minutes** (my call):
  a service that starts after 08:55 skips the day, never a late entry.
- **What (user):** SPY, today's expiry. Put side: sell the put whose delta is
  nearest **0.15** in absolute value, buy the put **$5** below it. Call side: sell
  the call nearest **0.15**, buy the call **$5** above it. A tie on delta takes the
  strike FARTHER from the money (my call).
- **Size (user):** **1** condor a day, configurable.
- **Caps (user):** bypasses EVERY per-trade and book cap. It never goes through
  `compute.create_paper_trade` or `shared.book_caps`.
- **Entry price (my call):** the realistic fill the paper engine already uses
  (`fill_model.realistic_vertical_fill`, worked 40% into each vertical's net
  market), the two verticals summed, priced from a chain fetched NOW (`max_age=0`,
  the rule for a fill). Not the mid.
- **Commission (my call):** the round trip (`commission.round_trip_commission("IC",
  "SPY", qty)`) is folded into `entry_credit` at booking, as an idea row's entry
  cash is; no closing commission is taken later.

## The exit

- **Stop (user):** "If I pay $1 for the trade, $2 will be 100% and $3 will be
  200%." The stop is a **loss of `stop_mult` (2.0) x the credit**: with $1.00
  taken in, the condor closes when buying it back costs **$3.00**.
  Because `entry_credit` carries the commission, the stop line is set so the
  trigger is that PRICE exactly: with gross credit `c` and round-trip commission
  `k` per share, `stop_pnl = (stop_mult x c + k) x 100` per contract
  (`pnl = (c - k - v) x 100 <= -(2c + k) x 100  <=>  v >= 3c`).
- **No profit target (user).** `target_pnl = None`.
- **Checked every 5 minutes (user)**, on the existing trade-idea mark cycle
  (`idea_ledger.mark_cycle`, `[idea_trades] mark_interval_min`, regular session
  only). A stop closes at THAT mark's value, which can be worse than $3.00 when SPY
  gapped between marks; the Ledger records it as it was.
- **Otherwise held to the close (user):** the 15:05 CT settle pass
  (`mark_cycle(settle_only=True)`) settles it at its legs' intrinsic value on
  `paper_engine.settlement_underlying`.

## Days with no trade (my call)

Weekends, NYSE holidays (`shared.market_calendar`), `enabled = false`, a condor
already booked today, and any day the chain cannot price it: no chain, no expiry
today, no usable delta on a side (Schwab's `-999`, refused by `shared.greeks`), no
strike exactly `width` away, or a leg with no bid/ask. Each skip logs one line with
its reason. Nothing retries later in the day.

## How it fits the Ledger

The trade-idea rows already solve "a Ledger row nobody's caps, manage, reprice or
expiry pass may touch, closed only by its own 5-minute mark". The condor rows join
them rather than copy them:

- New source `"daily_condor"`. `idea_ledger` gains `SELF_MANAGED_SOURCES =
  {"trade_idea", "daily_condor"}` and `is_self_managed(t)`.
- **Book-mechanics sites switch from `is_idea` to `is_self_managed`:** every
  `compute.py` exclusion (reprice, caps book state, manual close, expiry, manage,
  the Account settle filter), `mark_cycle`'s row filter, `show_mark`,
  `handlers._carry_live_pnl`, and the "Delete all closed" keep-and-clear rule
  (`paper_trader` / `trades_db`, which take a SET of sources): a closed condor is
  the measurement and is cleared from view, never deleted.
- **Idea-only sites stay `is_idea`:** the weekly Claude review
  (`shared/idea_review.py`), the trade-idea results on the public site. A condor is
  not Claude's idea and must never be rated as one.
- **"The user's own book" readers exclude it** like an idea: `/eod`'s manual list
  and `symbol_facts`' open positions.
- **Paper Ledger page:** the row carries a tag ("SPY condor"); the All / My trades /
  Trade ideas filter keeps it out of My trades and Trade ideas, and shows it under
  All (my call).
- The row is an ordinary `IC` row: `strategy = "IC"`, `short_strike` /
  `long_strike` the put side, `call_short` / `call_long` the call side,
  `legs` the four legs, `notes = "Daily SPY condor"`, `stop_pnl`, `target_pnl =
  None`, `mae_pnl = 0.0`.

## Configuration (standing rule)

`config/paper.toml [daily_condor]`, read through `shared.paper_limits`, each key in
`webgui/config_schema.py`: `enabled` (true), `symbol` ("SPY"), `short_delta`
(0.15), `width` (5.0), `stop_mult` (2.0), `quantity` (1). The time is
`[slots.daily_condor] at` in `config/sessions.toml`.

## Goals / non-goals

- Goal: one honestly priced condor a day in the Ledger, stopped or settled with no
  hand on it.
- Non-goals: push notifications, a weekly review, a page of its own, a profit
  target, any filter on market conditions.
- Anti-goal: anything that lets the user's own Ledger passes or caps touch it, or
  that books two in a day.

## Left to the builder

Module layout (a new `services/options_svc/daily_condor.py`, importing nothing from
`compute`), test names, and the exact chain call.
