# How each trade idea did, on neuralstrike.co — design

**Date:** 2026-09-29 · **Status:** approved · Follows
[2026-09-29-site-trade-ideas-design.md](2026-09-29-site-trade-ideas-design.md).

## Decisions

| Question | Answer | Why |
|---|---|---|
| Measured how? | The **underlying's price only** — never an option quote | A live option mark on a public page lets anyone read the option price back (entry + P&L). That is the open Schwab redistribution question (D2) the public Strategy Finder already defers with `show_leg_quotes = false`. Stock prices are already public on the live screens. |
| Open idea | Stock move since the post (`$186.40 → $191.20, +2.6%`) and **"At this price at expiry: +$89"** — the payoff at expiration if the stock settled here | Computable from the legs and the stock price alone. It is NOT a mark: a long option also carries time value, and the label says so. |
| Expired idea | Settled at intrinsic against the stock's **expiry-day close**: `Expired Sep 26 at $184.10 · −$411 (−100% of risk)`. Final, never recomputed | The trade's actual result held to expiration — the only exit a posted idea states. |
| % | Result ÷ the idea's max loss ("of risk") | Every posted idea has a bounded loss (the selector refuses unbounded). |
| Day header | `Today, Sep 29 · 7 ideas · 3 ahead, 4 behind` | Counts open and settled together; a bad day reads as one. |
| Backfilled cards | Rebuilt from the caption (legs, expiry; entry from Risk/Profit to the dollar) plus the stock at the post minute from Schwab's 1-minute history; marked `approx` | Operator choice; ~45 calls once. |

## Data

Each manifest idea gains the entry facts (all already printed on the card):
`legs`, `expiration`, `entry_cash` (per contract, commission in), `max_loss`,
`spot` (stock at the post), `approx`. And a `result`:

```
{"status": "open",    "spot": 191.2, "move_pct": 2.58, "pnl": 89.0, "pnl_pct": 21.7, "as_of": iso}
{"status": "expired", "spot": 184.1, "move_pct": -1.2, "pnl": -411.0, "pnl_pct": -100.0,
 "as_of": iso, "settled": "2026-09-26"}
```

## Refresh

- `[site] refresh_min = 15` (`config/notify.toml`). A new scheduler branch runs
  `handlers.refresh_site_idea_results` once per slot inside the scan window
  (08:00–15:15 CT, trading days).
- **One batched `/quotes` call** for every open idea's symbol (fewer than 40) —
  about 26 calls a day.
- **Settlement** reads the expiry date's **daily close** (`/pricehistory`, daily,
  one call per expiring symbol, once): for an idea whose expiration is before today,
  or is today once it is 15:05 CT or later. No candle yet → try again next slot.
- `site_ideas` holds a lock around every manifest read-modify-write, so a refresh and
  a post in the same minute cannot drop each other's change.
- A symbol with no quote keeps its last result and its old `as_of` — never a 0.
  Anything that fails is `_degrade`d; posting never waits on it.

## Front end

`ideas.js` adds one result line per card (green/red by sign, "approx. entry" noted
on rebuilt ones) and the day header's ahead/behind count. Cards without entry facts
show no line.

## Not done

A live option mark (D2). Early exits (targets/stops) — a posted idea states no exit
but expiry.
