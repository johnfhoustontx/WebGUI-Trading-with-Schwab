# Strategy Finder — the whole chain, every expiry

**Date:** 2026-09-14 · **Status:** approved design · **Route:** `/options/swing`
**Follows:** `2026-09-13-strategy-finder-redesign-design.md`.

## Request

"I need the restriction of 120 days to be removed and the full chain to be considered
for trades."

## What is actually restricted today

The 120 days is only the page's **Any** preset and starting DTE max. The service takes
any `dte_max` and fetches the chain up to it. The more important restriction is inside
the builders, which consider **one expiry** each:

| Structures | Expiries they build on today |
|---|---|
| Credit spreads (`se.screen_spreads`), and iron condors from them | every expiry in the range; condors are the top 3 across the whole scan |
| Long/short calls and puts, debit verticals | the nearest expiry in the range (`_front_exp`) |
| Straddles/strangles, butterflies/condors, covered call/protective put/collar | the nearest expiry at least 7 DTE (`_front_pair`) |
| Calendars and diagonals | that front, plus a back month nearest front + 28 days |

So widening the range alone changes almost nothing: on a synthetic 56-expiry chain the
scan still produced exactly 16 ideas.

## Measurements (prod, 2026-09-14 pre-market)

| Symbol | Expiries (last) | Contracts | One `/chains` call | Groups of 8, 4 at a time |
|---|---|---|---|---|
| NVDA | 25 (2029-01-19) | 3,984 | 3.8 s, 5.1 MB | 2.2 s |
| AAPL | 25 (2029-01-19) | 3,398 | 3.3 s | — |
| SPY | 34 (2029-01-19) | 12,956 | **timed out at the proxy's 30 s** | 6.5 s, 16.6 MB |
| QQQ | 33 (2029-01-19) | 11,280 | — | 5.7 s |
| $SPX | 56 (2031-12-19) | 25,650 | — | 11–12 s, 37.7 MB |

The expiration list (`/expirationchain`) costs 0.2–0.3 s. The builders plus scoring took
0.5 s on a synthetic $SPX-sized chain. **Not yet measured:** the score's expiry mix on a
live chain (pre-market chains carry no quotes); that is part of verification.

## Operator decisions

| Question | Choice |
|---|---|
| What "full chain" means | **Every expiry** — every builder builds on every listed expiry in the range |
| Earnings on long-dated single-stock candidates | **Show and flag** — keep the candidate, tag it with the report date |
| While a scan runs | **The spinner stays until the data lands** |
| A scan with no ideas | **Still shows the spot price**, so an empty answer reads as an answer |
| ~1,000 rows from an index scan (found in review) | **Best 25 of each strategy type**, the rest counted as not shown; the list paged 50 at a time |

## Design

### 1. The range (page)

- **Any → All**, the default: DTE min 0 and DTE max **blank**, which means no upper
  limit. The scan command carries `dte_max: null`.
- Presets: *1–2 wk* (7–14) · *2–6 wk* (14–42) · *1–3 mo* (30–90) · **3–12 mo** (90–365) ·
  **1 yr+** (365–no limit) · **All** (0–no limit).
- Typing numbers still works; a blank DTE max shows the placeholder *no limit*.
- `scan_controls_from` (following a cached scan) treats a `null` DTE max as no limit, and
  its fallback becomes *All*.

### 2. The fetch (options service, every `swing_scan` caller)

`fetch_scan_chain(symbol, dte_max)`:

1. List the expirations (`compute.option_expirations`).
2. Keep those from **today** to today + `dte_max` + 2 (all of them when `dte_max` is
   `None`). From today, not from `dte_min`: `run_iv_analysis` reads the near-dated
   expiries for the IV and expected move, exactly as the single fetch gives it today.
3. Split into runs of **at most 8 consecutive listed expiries**; fetch each run
   (`from = run[0]`, `to = run[-1]`) with at most **4 in parallel**
   (`services._parallel.parallel_map`).
4. Merge the RAW chains: the expiry maps are unioned; top-level fields
   (`underlyingPrice`, `volatility`, …) come from the first successful response.
5. **Fallback:** no expiration list → today's single fetch (to today + `dte_max` + 2, or
   no `to_date` when `dte_max` is `None`).
6. A run that fails is **counted, not hidden**: `expiries_failed` goes on the result and
   the page adds *"N expirations could not be loaded"* to the count line. Every run
   failing is the no-chain case it is today.

The Income Window rides this path too; its chain content is the same, so its board does
not change.

### 3. Every expiry (Strategy Finder only)

`swing_scan(..., every_expiry=False)`. The Finder's handler passes `True`; the Income
Window keeps `False`.

With `every_expiry=True`, for each listed expiry *e* (DTE *d*) inside the range:

- **Single-expiry builders** (directional, debit verticals, straddles/strangles,
  butterflies/condors, stock structures) run on a chain **sliced to *e*** with
  `dte_min = dte_max = d`. The builders already take the nearest expiry in their window,
  so on a one-expiry chain they build exactly that expiry.
- **Calendars/diagonals** run on a chain sliced to *e* and later expiries, with
  `dte_min = d`, `dte_max` = the scan's upper bound — but only when *d* ≥
  `strategy_scanner._MIN_FRONT_DTE` (7). Below that, the builder would jump to the next
  expiry and build a duplicate.
- **Credit spreads** already cover every expiry: `screen_spreads` runs once on the whole
  chain. **Iron condors** are built per expiry (`build_iron_condors` on each expiry's
  spreads) instead of the top 3 overall.

**No builder changes**, so every builder test and every single-expiry result stays
byte-identical. `every_expiry=False` keeps today's code path exactly.

### 4. Earnings: flag instead of drop (Strategy Finder only)

`swing_scan(..., earnings_mode="drop")`. The Finder's handler passes `"flag"`.

- `"drop"` (default; Income Window): unchanged.
- `"flag"`: `screen_spreads` receives **no** `earnings_date` (it would drop internally),
  and every candidate whose **latest** leg expiry spans the report
  (`se.check_earnings_conflict(earnings_date, _latest_expiration(s))`) keeps its place and
  gains `spans_earnings: True` and `earnings_date`.
- The page shows **`Earnings Nov 19`**: a warning badge on the top-pick card, and a small
  tag after the strategy name in the list.

The Market Scanner, the Income Window and the paper engine keep the gate. The paper
ledger has no earnings check, so a tagged trade sent to Paper — a credit spread or
condor as much as a debit trade — opens like any other.

### 5. The spinner lasts the whole wait (operator addition)

"Show the spinner for the duration of the wait till the data is loaded."

Today the spinner and placeholder cards give up after the shared 30 s backstop
(`busy.BUSY_TIMEOUT_SEC`) and show the "taking longer than expected" card, and a scan
that RAISES in the service publishes nothing, so the page cannot tell a slow scan from
a dead one.

- **The service always answers.** The `swing_scan` handler catches an exception from
  the scan, records it (`_degrade.degraded("options.swing_scan")`) and publishes an
  answer for that request anyway: `signals: []`, `error` = the exception's class name (e.g. `"TypeError"`; readers test it for truthiness), the symbol, the params
  and the spot if it was read. A failed scan ends the wait in seconds.
- **The page waits for that answer.** The spinner and placeholders stay up until a
  payload that answers THIS request lands (`payload_answers_scan`, unchanged), with a
  running count — *Scanning $SPX… 12 s* — through `build_busy`'s existing
  `elapsed_label`, so a long wait never reads as a hang.
- **One safety ceiling, not 30 s:** `SCAN_TIMEOUT_SEC = 180`, over ten times the
  slowest measured scan. It exists only for a service that is down or a command that
  was dropped; reaching it shows today's still card and System Status line.

### 6. The spot price on every answer, including an empty one (operator addition)

"Even if there is no signal produced I need you to display the spot price to show no
results were returned."

- `swing_scan` returns **`spot`** on every result (a number; `return_chain` keeps
  returning the chain itself). The quote is read **before** the chain, so the price
  survives a missing chain. The handler publishes `spot`, plus `chain_missing: True`
  when no chain came back.
- The summary strip takes its price from the payload's `spot` (falling back to the
  first row's `underlying_price` for a payload written before this change) and shows
  for every answer: *SPY  $764.48 · 0 ideas · 18 below the quality bar*. With no price
  at all it says **Price unavailable** — never a zero.
- The empty list names the symbol, the price and the reason:
  - *No strategies cleared the quality bar for SPY at $764.48.*
  - *No strategies for SPY at $764.48 — premium is too cheap to sell.*
  - *No option chain came back for SPY at $764.48.* (`chain_missing`)
  - *The scan for SPY failed. Check System Status and scan again.* (`error`)
  - otherwise *No strategies could be built for SPY at $764.48 in this expiry range.*

### 7. The best 25 of each strategy type (operator decision, during the build)

Measured in the Task 2 review on a synthetic $SPX-sized chain (56 expiries, 25.6k
contracts): every expiry left **1,061 rows after the quality cut** — a 2.27 MB cache
payload, and ~3 MB more of per-row payoff SVG pushed to the browser on every paint and
every chip click. Off-hours it is worse: the liquidity gate relaxes its volume and spread
checks when the market is closed, so long-dated strikes are not cut.

- The whole chain is still scanned, scored and cut. After the quality cut the service
  keeps the **best `FINDER_PER_TYPE_LIMIT = 25` rows of each `type`** (e.g. the best 25
  bull call spreads across all expiries), ranked by `composite_score`, and counts the
  rest as **`not_shown`**. `swing_scan(..., per_type_limit=None)`; the Finder's handler
  passes 25. Ids and payoff curves are built after the limit, so dropped rows cost
  nothing further.
- The page's count line adds *"1,040 lower-scoring ideas not shown"*.
- The ranked list is **paged, 50 rows at a time** (Quasar `rows-per-page`), so the
  browser never draws hundreds of payoff shapes at once.

### 8. Consequences, stated

- **Many more ideas.** SPY could produce several hundred before the quality cut. The
  top-pick cards, the chips and the sortable Expiry column do the narrowing; illiquid
  long-dated strikes fall to the existing liquidity checks.
- **Scan time** — estimated at about 20 s for $SPX from a synthetic chain (the 11–12 s
  fetch plus a 6.55 s every-expiry build, calendars about half); measured live after the
  promote at **40.1 s for $SPX**, 25.7–27.4 s for SPY and 13.5 s for NVDA (range *All*).
  Still well inside the page's 180 s ceiling (`SCAN_TIMEOUT_SEC`, §5). Commands on
  `cmd:options` run one at a time, so a Calculator load or Gamma refresh clicked during a
  scan waits behind it.
- **Payload size** grows with the rows (each carries a 25-point payoff curve). Measured
  at verification.

## Testing

- **Fetch:** runs of ≤ 8 consecutive expiries; ≤ 4 in parallel; raw merge keeps
  top-level fields; a failed run is counted; no expiration list falls back to the single
  fetch; `dte_max=None` selects every expiry.
- **Every expiry:** equals the builders called by hand on each sliced expiry; calendars
  skip fronts under 7 DTE; iron condors per expiry; `every_expiry=False` output identical
  to before; ids unique.
- **Earnings:** flag keeps and stamps the row (calendar judged on its back month);
  `screen_spreads` gets no date in flag mode; drop mode unchanged; the handler passes
  flag and `every_expiry=True`; the Income Window passes neither.
- **Always answers:** a scan that raises still publishes `error` for its request; the
  result carries `spot` on the no-chain path and on an empty scan.
- **Page:** *All* sends `dte_max: null`; blank DTE max; the new presets and their
  detection; the earnings badge and tag; the failed-expirations line; following a cached
  scan with no DTE max; the spinner survives past 30 s and ends on the answer, including
  an `error` answer; the elapsed count; the summary strip and empty-list line carry the
  price on a zero-idea answer, and *Price unavailable* without one.
- **Before calling it done:** the local page harness with a multi-expiry synthetic chain,
  then live Redis-driven scans on prod after the open (SPY, NVDA, $SPX): time, row count,
  payload size and the expiry mix of the top picks.

## Out of scope

Changing any builder; changing scoring to balance horizons (measured first, decided
after); the Market Scanner and Income Window ranges; a per-expiry filter on the page.
