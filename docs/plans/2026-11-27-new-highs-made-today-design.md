# New highs / new lows: two lines, made today — design note

Date: 2026-11-27 · Follows [the first build](2026-11-27-new-highs-lows-design.md)

## The request (the operator's words)

> Modify the New highs and lows into 2 lines. The All stocks line can be
> eliminated and only consider stocks that are $5 or more; that title can be
> renamed to All Stocks. The top line will be new highs and the next line will be
> new lows. A new high is a new high formed for that trading day; a new low is a
> new low formed for that trading day.

## What changes

- **One universe.** The no-floor counts go. Only stocks at or above `min_price`
  ($5, `config/new_highs.toml`) are counted, and the group is titled **All Stocks**.
  `min_price = 0` now counts every price instead of dropping a row.
- **Two lines.** NEW HIGHS on top (green), NEW LOWS under it (red), one count per
  window under 52 WK / 13 WK / 4 WK. It replaces the highs / lows pairs per row.
- **Made today.** A stock counts when its latest daily bar is today's and today's
  high is strictly above the window's high as it stood before today. A new low is
  the same with the low strictly below.

## What the screener's fields mean (measured live, 2026-11-27, after the 12:00 close)

| Question | Test | Answer |
|---|---|---|
| Do `price_52_week_high`, `High.3M`, `High.1M` include today? | count `high > field` | **0** for all three (and for the lows): they include today |
| Is "at or above the field" the same as "made today"? | `high >= High.1M` vs. the rule below | No: 353 vs 266 at $5 and up |
| Why the gap? (a) not traded today | `time` (the daily bar's open) for NASDAQ:MTAK | Wed 2026-11-25 08:30 CT: its `high` was Wednesday's |
| (b) ties | `high == price_52_week_high[1]` | 14 stocks, mostly SPACs pinned at a price (WRB tied yesterday's high) |
| What is `X[1]`? | MTAK `High.1M[1]` = 9.89, its high a bar earlier | the field one daily bar back: the window before today |
| Is "1M" a calendar month? | VZ's `Low.1M` = 43.61, the Oct 29 low; the Nov 2–27 low is 44.03 | No: a **trailing** month; `High.3M[1]` = VZ's Sep 15 high, a trailing 3 months |
| Does the screener filter on `[1]` fields and `time`? | the counts below | Yes, no error |

Counts at $5 and up (3,426 stocks, 3,252 traded today), the old rule against the new:

| Window | old highs (`>=` field) | **new highs** | old lows | **new lows** |
|---|---|---|---|---|
| 52 wk | 132 | **90** | 15 | **10** |
| 13 wk | 227 | **168** | 32 | **24** |
| 4 wk | 353 | **266** | 63 | **49** |

## The rule, as the requests send it

Each window × side request is the universe (type, primary listing, exchanges,
`close >= min_price`) plus:

- `{"left": "time", "operation": "egreater", "right": <the session date's midnight CT, unix>}`
- highs: `{"left": "high", "operation": "greater", "right": "High.1M[1]"}`
  (`price_52_week_high[1]`, `High.3M[1]`); lows: `low` `less` `Low.1M[1]` …

That is seven requests a refresh (the universe, then 3 windows × 2 sides), down from
13. The windows stay those the panel offered. The labels say weeks and the fields
measure months, as before; the hover says so.

## The view

`cache:market:new_highs` carries `counts: {window: {highs, lows}}` in place of
`rows`. `MarketNewHighs` refuses a payload with `rows`, and the service does not
put a stored `rows` view back after a restart (its numbers meant "at a high").
The page does not draw one either. A promote in the usual window (after the close)
restarts the service while the settling refresh is still due, so the new shape is
published within seconds. A promote on a closed day leaves the group hidden until
the next open.

## Left as it was

The cadence, the all-or-nothing refresh, the stale greying, the public switch, the
exchanges, and the source.
