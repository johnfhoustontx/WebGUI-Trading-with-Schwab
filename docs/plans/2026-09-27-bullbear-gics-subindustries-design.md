# Bull / Bear Map + Momentum on GICS sub-industries — design

**Date:** 2026-09-27 · **Status:** built

## Ask

Replace the Bull / Bear Map's industry level with GICS **sub-industries**, and use
the symbols listed on the **GICS Map** tab of `GICS_Classification_Symbols.xlsx`
(163 sub-industries, 725 unique symbols, 14 of them OTC ADRs).

## Decisions (operator, 2026-09-27)

1. **A sub-industry is scored as an equal-weight basket** of its listed stocks.
   The old industry level scored each industry's ETF price series; no
   sub-industry has an ETF. The rejected alternative, the median of the
   members' readings, is not a price series and could not be scored the way
   every other row is.
2. **Both pages.** The cascade feeds the Bull / Bear Map *and* the Momentum
   page, and both switch. There is still one cascade and one set of levels.

## Shape

- `sectors_ref.load_gics_map()` / `gics_symbols()` read the tab (copied into
  `sentiment-dashboard/`, beside the older Sectors workbook, which still drives
  the sector heat grid's industry ETFs).
- `compute._momentum_universe()` now builds from the GICS map. The **sector**
  level is unchanged: the SPDR ETF, joined to the GICS sector by name (a test
  pins that all 11 names match).
- `compute._momentum_basket(members, grid)` is pure: the mean of the members'
  daily returns, compounded from 100 on **SPY's date grid**, so
  `scoring.momentum` aligns it with the benchmark exactly as it aligned an ETF.
  A member joins from its second bar, and a missing day carries the level. If
  SPY fails to load, the members' own dates are the grid.
- A basket row keeps the level key `industry` (the `MomentumSnapshot` contract,
  the `momentum_scores` table and the public `/momentum?level=industry` pin all
  use it). `symbol` is the 8-digit GICS code, and the row carries
  `basket: true` and its `members`. Only the display words changed.

## Rules worth keeping

- **A GICS code is never sent to `/quotes`.** `bullbear_symbols` asks for a
  basket's members instead, and `merge_live` gives the basket the mean of its
  QUOTED members' day moves. An omitted member is left out, never counted as 0.
- **Two liquidity floors, the same split as before.** A basket member only
  measures its sub-industry, so it clears the $250k instrument floor. A stock
  row has to support a position, so it clears $5M. A thin name is therefore in
  its basket but not ranked as a stock.
- **`min_members` (2)** usable members, or the sub-industry goes to `excluded`
  (`too_few_members`) and its stocks show under "Not in a scored sub-industry".
  At 1, a basket is one stock under another name. Today that excludes the three
  single-symbol sub-industries (Highways & Railtracks and Marine Ports &
  Services, both OTC-only, and Specialized Finance). Drug Retail lists no
  symbol at all and is reported as `no_members`.
- **Rank history is limited to rows scored this session.** Stored sessions
  still name the old industry ETFs and the 311-stock universe, and an old
  rank 1 would chart as a leader.
- **Quotes go out in batches** of `[bullbear] quote_batch` (375, the largest
  size measured to return in one call): two calls per poll for ~737 symbols.

## Costs

- **Nightly history:** about 500 symbols are new to `momentum.db`, so the first
  cascade after deploy backfills a year each (~100 s at the proxy's 5 req/s).
  After that it is back to one bar per symbol per night. The ~70 industry ETFs
  are no longer fetched by the cascade.
- **Live quotes:** two `/quotes` calls per Bull / Bear poll where there was
  one — roughly +780 calls a trading day.
- **Payload:** `cache:sentiment:momentum` grows from about 304 KB to about
  400 KB (measured with synthetic bars over the real universe).

## Known first-night artifact

`rank_prev` compares against the previous stored session. For stocks in both
universes, the first night's Δ compares a rank among 311 with a rank among
~700, so that one day's movement column is not meaningful. Sub-industries have
no previous rank, so they show no Δ. Both settle after one session.

## Config

`config/momentum.toml` (new), catalogued in Settings → Configuration →
**Momentum & Bull / Bear Map**. Changes take effect after a `sentiment_svc`
restart.
