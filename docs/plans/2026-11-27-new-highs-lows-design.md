# New highs and new lows on the Macro Board — design

Date: 2026-11-27 · Status: draft, for the operator's approval

## Why

The operator wants a read on the **broad market's trend** from the top of the
Macro Board. The Advancing / Declining meter there cannot give it: it counts only
the few dozen tiles in the board's four equity frames (Broad Market, Top 10,
Sectors, Industries; `BREADTH_CATEGORIES` in `webgui/pages/market.py`). The
number of stocks making new highs against the number making new lows, over a
long, a middle and a short window, is the classic breadth read for this, and it
must be taken across **every US-listed stock**: the operator ruled the S&P 500
not broad enough.

## Goals

- Counts of stocks at a **new high** and at a **new low** over **52 weeks,
  13 weeks and 4 weeks**, across all US exchange-listed common stocks.
- Shown in the Macro Board's top control bar, beside Advancing / Declining.
- Refreshed by the app on its own through the session, no Claude session needed.

## Non-goals

- No list of WHICH stocks made highs or lows (counts only; a list is a later step
  if wanted).
- No history chart of the counts, and no new-high/new-low index or ratio series.
- No change to the Advancing / Declining meter or what it counts.
- No Schwab or proxy call: this costs nothing from the proxy's 5-per-second budget.

## Anti-goals (a failure even if it works)

- A count that is silently wrong: a partial fetch published as a full one, or a
  failed fetch shown as zero highs and zero lows. Absence must read as absence.
- A refresh that slows the board's 3-second poll.

## Constraints found

- **The TradingView connector cannot feed the app.** It is attached to the
  operator's Claude session, not to the server on vps2. The app calls
  TradingView's screener itself, at `https://scanner.tradingview.com/america/scan`
  (the same service the connector wraps).
- **That address is unofficial.** It is the one TradingView's own screener page
  uses; it has no published contract and can change or start refusing.
- **The Charting Library is not a source** (researched 2026-11-27): it is a
  browser chart that supplies no data, has no screener, and is free only on a
  public site.
- **Tested 2026-11-27, no sign-in:** the screener COUNTS on its own side. A
  request filtered `high >= price_52_week_high` (a field compared with a field)
  with a two-row range returns `totalCount` for the whole market. The six counts
  came back as 121 / 85 (52 wk), 205 / 120 (13 wk), 347 / 158 (4 wk), within a
  few of the connector's full 4,984-row pull (125 / 87, 211 / 123, 354 / 161) a
  few minutes earlier. The $5 row came back as 120 / 12, 203 / 27, 314 / 55, and
  the universe alone as 4,984, exactly the connector's count. Without a sign-in the data may be delayed; for a breadth
  count over a session that is acceptable.
- **The public `/macro` screen renders this same page** (`webgui/live_screens.py`).
  Whether TradingView's terms allow showing their screener's figures on a public
  site was not checked; the operator chose to show them publicly (2026-11-27),
  with a switch to take them off (see Decisions).

## Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| **A. market_svc asks TradingView's screener for the counts** (six small requests a refresh, or twelve with the $5 row) | Tiny, no sign-in, no Claude; but an unofficial address | **Chosen** |
| B. A scheduled Claude run on vps2 calls the TradingView connector | Uses the connector as asked; but each refresh is a Claude run, and the all-stocks pull is ~2.3 MB a refresh, more than a run can read without writing a script each time | Rejected |
| C. Compute from Schwab: quotes carry the 52-week high/low, and a nightly price-history pull per stock gives 13 and 4 weeks | Official data; but ~5,000 history calls a night through the shared proxy budget, and the stock list must be kept by us | Rejected for now; the fallback if TradingView's address closes |

## Definitions

- **Universe:** `type = stock`, `is_primary = true` (each company's main listing
  once), `exchange` in NYSE, NASDAQ, AMEX (NYSE American). OTC and funds out.
  4,984 stocks on 2026-11-27.
- **New high** over a window: today's high is at or above the window's high
  (`egreater`); **new low**: today's low at or below the window's low (`eless`).
  The window's high includes today, so "at or above" means today set it.
- **Windows:** TradingView has no week fields. 52 weeks is
  `price_52_week_high` / `price_52_week_low`; **13 weeks is `High.3M` / `Low.3M`**
  and **4 weeks is `High.1M` / `Low.1M`** (calendar months). The labels say
  52 wk / 13 wk / 4 wk as the operator named them; the help text says what they
  are measured on.
- **The $5 row:** the same counts with `close >= 5`. On 2026-11-27 it moved the
  52-week lows from 87 to 12: nearly every new low was a sub-$5 stock, so the
  all-stocks row alone reads the market weaker than it is.

## Decisions

Made by the operator: all US-listed stocks; 52 / 13 / 4 weeks; in the top frame
beside Advancing / Declining; TradingView as the source; approach A; **shown on
the public `/macro` screen too**, behind a switch (`[public] show = true`, read
through `shell.is_public()`) that takes it off the public screen alone.

My calls, for the operator to check:

1. **Two rows, all stocks and $5 and up**, both shown (not one behind a hover).
2. *(retired: the public screen was the operator's call, above.)*
3. **Cadence:** every 60 s in the regular session, first at the open (a
   pre-market "today's high" is not a session high); one refresh 10 minutes after
   the close (the early close on a half day, through
   `market_calendar.session_close_on`) and then hold; nothing on a closed day. A
   minute is ample for a breadth count and keeps the unofficial address quiet.
4. **No sign-in cookie in the first slice.** An optional TradingView session
   cookie read from a gitignored `shared/tradingview.json` is left as a later step,
   only if the delay turns out to matter.
5. **Shown as numbers, not a bar:** the meter beside it already carries a bar,
   and six pairs of bars would crowd the control bar.

## The design

### Tier 2 — `services/market_svc/new_highs.py`

Follows `holdings.py`: a `refresh(bus, state, now=None, wall=None)` run BESIDE
the 3-second poll in `scheduler.loop` (its own `run_in_executor` task, at most
one at a time), returning at once when not due, never raising.

- `requests_for(cfg)` — PURE: the list of `(key, body)` for each window × side ×
  row (`52w`/`13w`/`4w` × `highs`/`lows` × `all`/`over5`). Each body is the
  universe filter plus one field-to-field comparison, plus `close egreater 5`
  for the $5 row, with `columns: ["name"]` and `range: [0, 1]`: only `totalCount`
  is read.
- `fetch_count(body, cfg)` — one POST through `shared/http_watchdog.py` (the
  deadline `news_svc` and `xfeed_svc` use), returning `totalCount` as an int, or
  `None` for any failure (status, timeout, bad JSON, missing or non-integer
  count). A generic browser `User-Agent`; nothing about the operator in the
  request.
- `build(counts, universe, ts)` — PURE: the view. **If any one count is `None`,
  nothing is built**: a refresh is all twelve or nothing, so no window ever pairs
  a fresh high with a stale low.
- The thirteen requests go one after another (about half a second each), never
  in parallel: the address is unofficial and must not see bursts. The exchange
  list is sent as `{"left": "exchange", "operation": "in_range", "right": [...]}`,
  the form tested on 2026-11-27.
- The universe size comes from one more request (the universe filter alone), so
  the frame can say "of 4,984".
- On a failed refresh: publish nothing (the last view stays and ages by its
  `:ts`), `_degrade.degraded("market.new_highs")`, retry after `retry_sec`.
- Switched off (`enabled = false`): publish once `{"enabled": false}` so the page
  hides the frame, as holdings does.

**The view** `cache:market:new_highs`, event `events:market:new_highs`,
validated on write by a new `MarketNewHighs` model in `shared/contracts/market.py`:

```
{"enabled": true, "source": "TradingView", "universe": 4984,
 "session": "2026-11-27",
 "rows": {"all":   {"52w": {"highs": 121, "lows": 85}, "13w": {...}, "4w": {...}},
          "over5": {"52w": {"highs": 120, "lows": 12}, ...}}}
```

Published with `skip_unchanged=True`, so it carries **no timestamp of its own**
(the `:ts` side key is the age the header's stale stamp reads). `session` is the
trading date the counts belong to, so the page can label a held after-close
reading.

### Configuration — `config/new_highs.toml` + `shared/new_highs_config.py`

Loader on `shared.config_toml.toml_loader` (a `NEW_HIGHS_TOML` path in
`repo_paths`, as `ETF_HOLDINGS_TOML`), defaults for every key, never raises;
read on every refresh (no restart). Every key in `webgui/config_schema.py`
(Settings → Configuration).

```
enabled = true
url = "https://scanner.tradingview.com/america/scan"
exchanges = ["NYSE", "NASDAQ", "AMEX"]
min_price = 5            # the second row; 0 hides it
rth_refresh_sec = 60
after_close_min = 10     # the one refresh after the close
timeout_sec = 10
retry_sec = 120
[public]
show = true              # false takes it off live.'s /macro only
```

The windows and their TradingView field names are code, not config: changing
one needs the labels and help to follow.

### Tier 1 — `webgui/pages/market.py`

A second group in the control bar, after the breadth meter and before the skin
toggle. Pure helpers (`new_highs_rows(payload)` → the strings and direction
classes per cell) so the page test needs no browser. Reads the view with
`bus_client` off the event loop, and repaints IN PLACE on its own version, as the
holdings view does. Colours from the existing `_T['MB_UP']` / `_T['MB_DN']`
tokens; Tailwind classes only.

```
 ADVANCING   DECLINING  │  NEW HIGHS / LOWS        52 WK     13 WK     4 WK
    31          12      │  All stocks            121/85   205/120   347/158
 ████████████▓▓▓▓▓      │  $5 and up             120/12   203/27    314/55
```

- Highs in the up colour, lows in the down colour; a dash, never 0, for a cell
  the view lacks.
- Hover: "Stocks at a new high / low across 4,984 NYSE, NASDAQ and NYSE American
  stocks, from TradingView's screener. 13 wk is 3 months, 4 wk is 1 month."
- No view yet, or `enabled: false`, or the public process with `[public] show`
  off: the group is not drawn.
- Old data: the frame greys and says "as of the close" once `session` is not
  today's session. In the session it greys and says "as of 10:00 AM CT" once the
  view's `:ts` is older than the view's `stale_after_sec` (one refresh plus a failed
  one and its retry, 300 s). Both are re-checked every poll tick. (Settled in the
  final review: the first build checked only on a new version and never by age.)

### Documentation, in the same change

`webgui/page_help.py` (the Macro Board's hover guide), the User Guide and
Reference Guide sections for the Macro Board, the Technical Reference (the
definitions, the source and the cadence), `docs/webgui-routes.md` for `/market`,
and a CHANGELOG entry. CLAUDE.md gains one line only: market_svc's outside
call to TradingView (it was Schwab-only).

### Tests

- `requests_for`: twelve bodies plus the universe body, each with the universe
  filter, the right field pair and operator, and the $5 filter only on its row.
- `build`: a full set builds; any single `None` builds nothing.
- `fetch_count`: non-200, timeout, bad JSON, missing / non-integer `totalCount`
  → `None` (the HTTP layer injected; the suite cannot reach the network).
- `refresh`: not due → no fetch; failure → no publish, degrade counted, retry
  set; disabled → one `enabled: false` publish; after-close single refresh, and
  nothing on a holiday (`shared.market_calendar`).
- The contract rejects a negative or non-integer count.
- Config loader defaults and bad values; schema test covers the new keys.
- Page helpers: dashes for absence; hidden when disabled; on the public process
  drawn when `[public] show` is on and hidden when it is off.
- Verified in `tools/ui_harness.py` on a fake bus before merge, then on prod after
  the promote (a read-only change).

## Left to the builder

The control-bar spacing at narrow widths (the group may wrap under the meter),
and the exact wording of the stale label.

## Later, if wanted

The list of names behind a count; a day-by-day history of the counts (a small
table in a store, one row per session); the optional sign-in cookie; approach C
as a fallback if TradingView's address stops answering.
