# New highs / lows: sectors on hover, stocks on a click — design note

Date: 2026-11-27 · Follows [made today](2026-11-27-new-highs-made-today-design.md)

## The request (the operator's words)

> When I hover over the 52/13/4 wk high or low, pop up a window giving the
> breakdown by sector from high to low (by count). Clicking one of those sectors
> will then give the list of stocks that have made a new high or low for that
> period.

## What the screener gives (measured live, 2026-11-27)

- Every stock carries a `sector` (TradingView's own set: Finance, Health
  Technology, Technology Services, …, and Miscellaneous). It is not GICS; see
  "The sectors are GICS" below for what replaced it.
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

## The sectors are GICS (same day, after the operator's review)

> It seems that the sectors are actually industries.

They were, nearly: TradingView's 21 "sectors" are its own industry groups
(Finance, Technology Services, Electronic Technology, Distribution Services, …),
not the 11 GICS sectors a trader reads, and the board's Sector SPDR tiles use GICS.
Probed live, the screener has no GICS field (`gics_sector`, `sector.tr` and the
like return nothing). So the operator chose a map:

- The list requests ask for `industry` (127 values on 2026-11-27) in place of
  `sector`. `config/new_highs.toml [gics]` names the GICS sector each industry is
  counted in; the loader keeps a row only when its value is one of the 11
  (`new_highs_config.GICS_SECTORS`, from `shared.sectors` without `INDEX`). An
  industry the map lacks is *Unclassified*, so a new TradingView industry shows up
  rather than vanishing. The map lives in the file alone (the built-in default is
  empty), as `config/sectors.toml`'s does.
- Industries that straddle two sectors go to the sector most of their stocks are
  in, each noted in the file: *Miscellaneous Commercial Services* (Cintas, Rollins,
  but also PayPal and Medpace) → Industrials; *Internet Software/Services* (Alphabet,
  Meta, Netflix, but also VeriSign) → Communication Services; *Data Processing
  Services* (Tyler, Akamai and the bitcoin miners, but also Paychex) → Information
  Technology; *Financial Publishing/Services* (S&P Global, Moody's, but also
  Equifax) and *Investment Trusts/Mutual Funds* (mostly business development
  companies, some royalty trusts) → Financials; *Discount Stores* (Walmart, Costco)
  → Consumer Staples, as GICS has had them since 2023.
- Each listed stock carries its `industry`, and the stock window shows it, so a
  reader can see why a stock sits where it does.
- Settings → Configuration → New highs / lows → *Industry → sector*: one row per
  industry, named verbatim, its value chosen from the 11.

Live on 2026-11-27 no stock was Unclassified, and the 4-week highs read Financials
55, Health Care 52, Industrials 39, Information Technology 38 (before: Finance 69,
with the REITs and developers inside it). The lists grew to 100 KB.

## Not done

No link from a listed stock to its own page, and no sector breakdown in the hover
text of the group as a whole. Both are small follow-ups if wanted.
