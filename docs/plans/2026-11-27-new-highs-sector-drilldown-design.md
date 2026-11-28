# New highs / lows: sectors on hover, stocks on a click — design note

Date: 2026-11-27 · Follows [made today](2026-11-27-new-highs-made-today-design.md)

## The request (the operator's words)

> When I hover over the 52/13/4 wk high or low, pop up a window giving the
> breakdown by sector from high to low (by count). Clicking one of those sectors
> will then give the list of stocks that have made a new high or low for that
> period.

## What the screener gives (measured live, 2026-11-27)

- Every stock carries a `sector` (TradingView's own set: Finance, Health
  Technology, Technology Services, …, and Miscellaneous). It is not GICS.
- The request that returns a count can return its rows too: the same filter with
  `columns` and `"range": [0, N]` answers `totalCount` and `data`. The 4-week highs
  (266 stocks) came back in 0.4 s, 37 KB raw. **So the lists need no extra
  request**: the six window requests simply ask for their rows.
- All six lists that day came to 80 KB once trimmed to what the board shows.

## The design

**Tier 2** (`services/market_svc/new_highs.py`). Each window × side request asks
for `name, description, sector, close, change, high, low`, up to `list_max` rows
(config, 5,000: past the whole universe, so nothing is cut), sorted by name. The
service parses the rows, groups them by sector (most stocks first, ties by name)
and sorts each sector's stocks by the day's move (highs: biggest gain first; lows:
biggest fall first). It publishes `cache:market:new_highs_detail`
(`MarketNewHighsDetail`) **before** the counts, in the same all-or-nothing refresh:
a count above 0 that comes back without its rows fails the refresh.

Why a second view: the board's 2-second poll reads the counts; the lists run to tens
or hundreds of KB and are wanted only on a hover.

**Tier 1** (`webgui/pages/market.py`). Each count is a `ui.menu` anchor. Hover (or
tap) reads the lists view (`read_shared`, once per version per process), and only if
its `session` matches the counts' does it fill the popup: the window and side, the
total, and the sectors with their counts. The popup closes 0.35 s after the pointer
leaves the count unless it reached the popup, and when it leaves the popup. Clicking
a sector opens a `kit.window` with a `kit.table`: Symbol, Company, Price, Change %,
Day high (or Day low). The group's own hover text moved onto the title column, so it
never opens over a popup.

**Public board.** It renders the same page, so the popup and the lists show there
too, behind the same `[public] show` switch as the counts.

**Switches.** `list_max = 0` asks for the counts alone and publishes the lists as
`enabled: false`, which turns the hover off; `enabled = false` takes both views off.

## Not done

No link from a listed stock to its own page, and no sector breakdown in the hover
text of the group as a whole. Both are small follow-ups if wanted.
