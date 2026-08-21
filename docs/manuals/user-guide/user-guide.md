[TOC]

# Introduction

**WebGUI Trading with Schwab** is a single, browser-based control center for a
Charles Schwab options-and-equities trading workflow. It replaces a collection of
older desktop and dashboard tools with one web app you open in your browser at
**http://127.0.0.1:8500**.

From this one interface you can:

- **Scan** for 0-DTE and swing options credit-spread opportunities and see each
  candidate scored 0–100 for quality.
- **Analyze** a single ticker for a position (1–8 week) and investor (months+)
  Buy / Hold / Sell verdict.
- **Model** options trades — a P&L calculator, an expected-move chart, a
  Black-Scholes "what-if" simulator, and a dealer-gamma (GEX) view.
- **Track** paper trades and captured signals, with automatic management.
- **Read the market's mood** through a sentiment composite, Day/Week/Month trend
  rings, a market-regime console, and a sector-rotation map.
- **Review** your live brokerage portfolio and an end-of-day report.
- **Watch** an autonomous paper trader pick and size defined-risk spreads.

> **This is a single-user, local application.** It runs on your own machine and
> talks to Schwab through a local gateway. There are no accounts to log into in
> the web app itself.

---

# Prerequisites

Before the app will run, a few things need to be in place on your machine. This is
the plain-English checklist — the *Technical Reference* has the full detail
(exact package versions, environment variables, etc.).

## The essentials (required)

- **Windows 10 or 11.** The app is Windows-first — the launchers are `.bat` files,
  the alert pop-ups use Windows notifications, and the data backbone (Memurai) is a
  Windows service.
- **Python 3.11 or newer.** Install it from python.org if you don't have it.
- **A one-time setup** in the project folder, which creates the virtual environment
  the launchers expect and installs everything:

  ```powershell
  python -m venv .venv
  .\.venv\Scripts\python -m pip install -r requirements.txt
  ```

- **Memurai running.** Memurai is a Windows version of Redis — the local "backbone"
  the app's parts talk through. It installs as a **Windows service on port 6379**;
  start it from **services.msc** if it isn't already running. **Nothing works
  without it** — every page will just say "Waiting for … service."
- **A modern web browser** to open the app at `http://127.0.0.1:8500`.

## Schwab account (required for live data)

The app reads market data and your positions from Schwab, so you need:

- A **Schwab developer account** with a registered app, which gives you an **App
  Key** and **App Secret** (register the callback URL as `https://127.0.0.1:8182`).
- Those keys saved into **`shared/appsettings.json`** (copy the provided
  `shared/appsettings.example.json` and fill in your keys).
- A **one-time Schwab login**: after starting the app, open **System Status → Schwab
  Authorization → Authorize** (or `http://127.0.0.1:8100/auth`) and sign in. This
  creates your token file.

> **Good to know:** the app refreshes your Schwab login automatically most of the
> time. If live data stops and **System Status** shows the Schwab login expired,
> just click **Authorize** again.

## Nice-to-have (optional)

- **An Anthropic (Claude) API key** — only needed for the AI features: the Gamma
  **Analyze**/**Explain** infographics, the autonomous **Claude Trades** driver, and
  the market-summary ticker. Set it as the `ANTHROPIC_API_KEY` environment variable
  (or in a `shared/anthropic_key.txt` file). Without it, those features simply stay
  quiet — nothing else is affected, and the auto-trader safely stands down.
- **Push notifications** (Telegram / Discord / text message) — configured in
  `shared/notifications.json` if you want alerts on your phone. Skip it and the app
  is silent on those channels.
- **The watchlist workbook** `options-scanner/data/Top 20.xlsx` — sets which stocks
  the scanner watches. Without it, the app falls back to the core index symbols.

## Ports the app uses

The app runs entirely on your own machine and needs these local ports free:
**6379** (Memurai), **8100** (Schwab gateway), **8210–8215** (the six services),
and **8500** (the web app). If another program is already using one of them, the
matching piece won't start.

---

# Getting Started

## Starting the application

The app is made of several background services plus the web interface. Start them
all together with one of the launcher scripts in the project root:

| Launcher | What it does |
|----------|--------------|
| `start_all.bat` | Opens the gateway, the six domain services, and the web app in **eight separate console windows**, then opens your browser. |
| `start_all_wt.bat` | Same eight processes, but as **eight tabs in one Windows Terminal window** (less desktop clutter; requires Windows Terminal). |

After the windows finish loading, the app is available at:

```
http://127.0.0.1:8500
```

The browser usually opens automatically. If it does not, open that address
yourself.

## What runs behind the scenes

You don't interact with these directly, but it helps to know they exist:

- **Schwab gateway (proxy)** — handles the Schwab connection and market data.
  Everything else depends on it. **It must be running first** (the launcher
  handles ordering for you).
- **Six domain services** — Sentiment, Options, Portfolio, Trade, Driver, and
  Market. Each one powers its matching page(s).
- **Memurai (Redis)** — a local data backbone the services and the web app share.

## The proxy-down banner

If you ever see a **red banner** across the top of every page saying the proxy is
unreachable, the Schwab gateway isn't running or has stopped. Live data won't load
until it's back. Use the **System Status** page (at the foot of the rail) to check and
restart components.

## "Waiting for … service" placeholders

Each page is powered by its service. If a page shows a *"Waiting for … service"*
message, that service hasn't started or has stopped. Start the full stack with a
launcher, or restart the specific service from the **System Status** page.

## Stopping everything

Use **Stop All Services** at the foot of the rail, or run `stop_all.bat`. This stops the
gateway, the six services, and the web app. (Memurai is intentionally left
running — it's a shared Windows service.)

---

# The Interface

## Layout

Every page shares the same frame:

- A **left icon rail** that widens on hover, with groups and standalone pages.
- A **header** at the top with a menu toggle and the current page title.
- **Hover help** on every menu item and tab — rest the mouse on one for two
  seconds and a plain-language "idiot's guide" to that page pops up (see
  *Getting help on any page* below).
- The **page content** in the main area.

## Navigation groups

The left edge is a narrow **icon rail** that widens when you hover it. Clicking a
**group** opens its first page and shows that group's pages as a **tab strip**
across the top; the other rail entries are **standalone pages** with no tab strip.

The rail is organised into **three captioned sections**, each answering one
question, plus a block of machine controls pinned to the bottom.

**MARKETS — what is the market doing?**

| Rail item | Pages |
|-----------|-------|
| **Dealer Positioning** (standalone) | — |
| **Opportunity Board** (standalone) | — |
| **Flow Alerts** (standalone) | — |
| **Trend & Sentiment** (group) | Market Dashboard · Sentiment · Sector & Industry · Sector Rotation · RRG · Momentum |

**STRATEGY — what should I trade?**

| Rail item | Pages |
|-----------|-------|
| **Strategy Tools** (group) | Calculator · Simulator |
| **Options** (group) | Market Scanner · Strategy Finder · Expected Move · Captured Signals · Paper Ledger · Paper Account · Rescue |
| **Trade Analyzer** (standalone) | — |
| **Claude Trades** (standalone) | — |

**ACCOUNT — what do I own?**

| Rail item | Pages |
|-----------|-------|
| **Portfolio** (standalone) | — |
| **More** (group) | EOD Report · User Manuals |

**System controls** sit at the foot of the rail, below a separator: **System
Status**, **Settings**, and a red-outlined **Stop All Services** button. They are
kept apart because none of them is a step in a trading workflow, and the
destructive one is placed last so overshooting Settings cannot land on it.

Two groupings are worth explaining because they are deliberate:

- **Dealer Positioning, Opportunity Board and Flow Alerts sit under MARKETS, not
  under Options.** They are market-*wide* reads. The Options group is the
  per-signal workflow — find a trade, analyze it, track it, repair it — and its
  tabs run in that order.
- **Calculator and Simulator are their own group.** They model legs *you* bring,
  whereas the Options tabs work on signals the app *finds*. They share a leg
  editor and copy trades to each other, so they belong side by side.

The **hamburger** at the top left pins the rail open so it stops collapsing.

## Alert badges and chimes

A small background watcher runs on **every** page. When new qualifying scanner
signals appear it can:

- Play a **chime** (a bundled sound), and optionally
- Fire a **desktop notification**.

It also shows red **count badges** on three nav items:

- **Scanner** — number of brand-new signals.
- **Captured Signals** — new captures.
- **Claude Trades** — new autonomous-driver activity.

Opening that page clears its badge. A **group's** rail badge is the sum of its
children's badges, so a count on the collapsed rail still tells you which section
to open.

> The scanner chime and badge count **credit spreads only**. The Directional tab
> is scored on a different, non-comparable scale, so it is deliberately left out
> of the alert threshold.

> **Browser sound is blocked until you interact with the page.** Click any nav
> link, or use **Test sound** on the Settings page, to unlock audio for the
> session.

You control all of this on the **Settings** page (see *Reports & System*).

## Getting help on any page

Three built-in help features are always within reach:

- **Page hover tooltips** — rest the mouse for two seconds on any rail item or top
  tab. A short "idiot's guide" pops up explaining, in plain language, what that
  page is for and how changing its settings changes the result.
- **Sub-tab hover tooltips** — several pages have a second row of small **view
  tabs** beneath the main strip (for example Dealer Positioning's **GEX / Charm /
  DEX / Vanna / Flow / Net Prem / Term**, the Simulator's **Replay / What-if / IV
  shock**, and the Scanner's **0-DTE / Swing / Directional**). Hover an individual
  sub-tab and a one-line tip explains what that specific view shows — so you can
  learn what "Charm" or "Vanna" means without leaving the page.
- **User Manuals** — a tab in the **More** group. It opens this User Guide, the
  **Reference Guide**, and the Technical and API references in your browser.

> **If you want to understand *why* a page exists rather than how to operate it,
> read the Reference Guide.** This User Guide is task-oriented — it tells you what
> to click. The Reference Guide covers every tab and sub-tab in depth: what its
> data is, how to read each panel and column, what edge it gives (and where it is
> weak), and when in the day to reach for it. It opens with a one-page summary of
> the whole app.

---

## Desk

**Route:** `/desk` — and the app's **home page**, so plain
`http://127.0.0.1:8500` lands here. In the rail it is pinned on its own above the
section captions.

One screen aggregating the most useful element of every other page, laid out as
the four questions you ask in order: *what is the market doing · where is the
structure · what should I act on · what am I holding.*

**The panels, top to bottom:**

| Panel | What it gives you |
|-------|-------------------|
| **Top strip** | Clock, **VIX** and its band, the **market regime** word, and two dials showing **Day / Week / Month** for sentiment and trend |
| **Dealer Positioning** | One row each for **$SPX, SPY, QQQ, $NDX** — price, gamma flip and distance to it, call and put walls, net gamma, and a pins-or-runs chip |
| **Opportunity Board** | The five hottest names, with implied volatility and whether it is rising or falling, and a setup tag |
| **Live Flow Alerts** | The five newest unusual-options events |
| **Positions** | Your paper trades and Claude's together, with live marks and an **OK / Watch / At risk / Rescue** flag |

**Clicking any row** opens the page it came from, already set to that symbol — a
dealer row opens Dealer Positioning on that symbol, a position opens the Paper
Ledger or Claude Trades.

**Nothing on this page can place or change a trade.** It reads and links only.

**It announces arrivals out loud.** When a new flow alert or a newly-opened position
appears, the Desk speaks the ticker and the cause — *"S P Y. Crossover alert, calls
over."* — and the row itself **glows for ten seconds**, so your eye lands where the
voice pointed. Tickers are spelled letter by letter, squawk-box style. If several
arrive together it names the newest and counts the rest ("plus 5 more") rather than
reading out a list.

A position that only changes **flag** — OK to At risk to Rescue — glows amber and
stays **silent**. It was already in the book, and the flag column has already told
you.

Switch it off, change the voice or set its volume under **Settings → Spoken alerts
(Desk)**. It obeys the same *only during market hours* setting as the scanner chime.

**Four things that will look like faults and are not:**

- **After the close the walls vanish and the panel greys out with a timestamp.**
  Index option open interest reads 0 overnight, which would otherwise produce
  confident-looking walls that are pure noise. The panel is telling you it is
  showing the last reading it trusts.
- **Flow alerts say "call" or "put", never "bought" or "sold".** Schwab publishes
  no time-and-sales tape, so nobody — including this app — can honestly tell you
  which side initiated.
- **The top strip shows VIX but not SPX or QQQ.** Those sit in the panel directly
  below with more context; showing them twice from two separately-updating sources
  could briefly display two different prices for the same symbol.
- **The Desk goes silent on a fresh tab, and an *Enable spoken alerts* button
  appears.** Browsers refuse to play audio until you have interacted with the page,
  and they refuse silently — nothing is logged and no error is shown. That button is
  the app telling you it was blocked. One click unlocks sound for the session; any
  other click on the page unlocks it too, the button just says so.

---

# MARKETS — what is the market doing?

The four rail entries in this section establish the conditions a trade would be
taken in. Nothing here proposes a trade.

## Dealer Positioning

**Route:** `/options/gamma`.

A dealer-positioning view built from the options chain — gamma exposure (GEX) and
related measures.

**Controls:**

- A **Symbol** dropdown (default `$SPX`; the list is the collected watchlist).
- **Refresh now**, an **Explain** button, an **Analyze** button, and a **Next
  refresh** countdown.
- A **view toggle** — seven sub-tabs, described below.

**The seven views.** The first four share the same bars-plus-heat-map layout; the
last three are different screens with their own controls.

| View | What it shows | When to use it |
|------|---------------|----------------|
| **Gamma** | How hard dealers must trade per point of price movement. The default, and the main read. | Always. |
| **Charm** | How dealer hedges decay as **time** passes, price unchanged. | Fridays and expiration days. |
| **Delta** | The dealers' **directional** exposure in dollars. | Judging which way the aggregate hedge leans. |
| **Vanna** | How hedges change when **volatility** changes. | CPI, Fed and earnings days. |
| **Flow** | Today's call premium vs put premium for this symbol, as a ribbon. | Confirming a directional bias intraday. |
| **Net Prem** | Net premium (call dollars − put dollars) for **many symbols at once**. | Comparing where money is going across names. |
| **Term** | The same exposure across the **next five expirations**. | Choosing which expiry to trade. |

**Flow** — the **crossover** is the read: the moment call dollars overtake put
dollars, or the reverse. That is the same event the **Flow Alerts** page logs, shown
here in context so you can tell a decisive break from a wobble. A strike ladder shows
where in the chain the premium sits.

**Net Prem** — the only view showing more than one symbol, with a menu of **28** in
three group tabs (**Indices & Broad**, **SPDR Sectors**, **Mega-caps**). Two things
are easy to get wrong:

- The group tabs filter the **tick-boxes, not the chart**, and your selection
  **persists across them** — so you can plot `$SPX` next to `XLK`.
- **Dollars ($M) / Skew %** in the panel header. The sizes differ enormously (SPY can
  run hundreds of millions on a day DIA barely reaches one), so **Dollars** shows the
  real money and **Skew %** rescales each line to that symbol's own premium, which is
  what lets you compare a big name with a small one.

Each symbol keeps the same colour, and your group, ticks and scale are remembered.

**Term** — its vertical axis is a **list of expirations, not a time scale**, so the
gap between columns says nothing about how far apart the expiries are. The hairlines
between columns matter: the smooth shading *across* a boundary is just drawing, not a
measurement. Read the columns, not the gradient between them.

> **Premium is unsigned.** Schwab publishes no options tape, so Flow and Net Prem
> show **traded dollars through calls versus puts** — a money-weighted put/call read,
> **not** net buying. A big call number is equally consistent with someone buying
> calls and someone selling covered calls.

**Status row:** a collector status dot, last-scan and next-scan times, and a one-
line summary (spot, strike count, net exposure).

**Panels:**

- **Left** — a horizontal bar chart of net exposure by strike near spot, with the
  spot line, a gamma-flip line, and call/put **wall** lines. (In **Term** view this
  becomes a full-width expiry × strike heat map.)
- **Right** — an **intraday heat map** of strike × time. Call-heavy (positive)
  strikes run deep blue → cyan; put-heavy (negative) run aubergine → magenta; near
  zero fades to transparent so quiet strikes read as empty. The spot price is
  overlaid. **Press and hold** the left mouse button to read a cell — plain hovering
  shows nothing.

**The buttons at the top right open separate screens, each in a new browser tab:**

| Button | Opens |
|--------|-------|
| **Explain** | A plain-language infographic interpreting the **current symbol's** positioning. |
| **Analyze** | Asks Claude to read the live `$SPX` / `SPY` / `QQQ` positioning and opens a report — a regime and bias gauge, a price-level ladder per index, a per-symbol what-if (rally / sell-off / chop), and a "why is this happening" section. |
| **Briefings** | The four **automatic** Analyze runs — premarket, ~18 minutes after the open, midday, and the close. |
| **History** | A date and slot picker that regenerates any earlier day's briefing. |

Below the chart, a collapsed **"How to read the 0-DTE close projection"** panel
explains the projected-close overlay and the projected-flip line.

The view refreshes automatically every two minutes; switching views is instant and
doesn't re-fetch.

> **Analyze and the automatic briefings call the Claude API**, so they cost money per
> run. The running count is on the **Settings** page under *API usage*.

## Opportunity Board

**Route:** `/options/matrix`. In the rail under **MARKETS**, not in the Options tab
strip — it is a market-wide read rather than a step in the per-signal workflow.

Every watchlist symbol as one sortable row, so you can triage the whole board
without opening a page per ticker.

Columns: **Ticker · Spot · Day % · Trend** (an arrow) **· Call / Put** (whether
premium is accelerating) **· P/C · Net $M · GEX** (whether spot is above or below
the dealer gamma flip) **· Sig** (live scanner signals) **· Flow** (flow alerts
today) **· Signal** (buy/neutral/sell) **· Hot**.

**Hot** is the default sort. Click any column header to re-sort. Three tiles at the
top count how many symbols are currently Buy, Neutral and Sell.

> **Hotness measures activity, not quality.** Use it to decide *where to look*,
> then open Dealer Positioning or Strategy Finder for that symbol. It is not a
> trade signal.

## Flow Alerts

**Route:** `/options/flow`. Also a rail item under **MARKETS**.

Every unusual options event the app detected **today**, newest first — the same
alerts that chime and push to your phone, kept somewhere you can read them.

Four detector types: **Crossover** (call premium overtook put premium, or the
reverse), **Unusual activity** (a contract traded far above its open interest),
**Gamma flip** (spot crossed the dealer gamma flip) and **Big delta** (one contract
holds an outsized share of the symbol's directional exposure).

Columns: **Time · Age · Symbol · Type · Side · Detail · Share · Alert**. Filter by
type or symbol — filtering is instant. **Click any row** to open Dealer Positioning
for that symbol.

> The list covers **today only** and resets overnight. There is no history.

> **No alert can tell a buy from a sell** — Schwab publishes no options tape. Read
> every row as "something large happened here", then use price and gamma to decide
> direction.

## Market Dashboard

**Route:** `/market`. It is the **Trend & Sentiment** group's first tab, so clicking
that rail item lands here. (The app's landing page is the **Desk** — opening
`http://127.0.0.1:8500` redirects there.)

A live wall of about 48 macro instruments in framed panels — volatility, options
sentiment, breadth internals, currency, cash indices, futures, broad ETFs, the top
ten mega-caps, sectors, thematic ETFs, factors, credit, crypto and countries.

- **Tile colour means risk-on (green) / risk-off (red) / no data (grey)**, not
  simply up and down. Fear gauges are flipped: VIX, SKEW, put/call, TLT and UUP
  shade **red when they rise**.
- Five frames — **Broad-Market ETF**, **Top 10**, **Sector SPDR**, **Thematic** and
  **Countries** — re-order themselves by the day's move. Every other frame keeps its
  curated order on purpose.
- The **top rail** carries a clock, an advancing/declining breadth meter, and an
  **A/B skin toggle** that is remembered. The meter counts the four **stock** frames
  only (broad ETFs, top ten, sectors, thematic), so a rising VIX or a bid Treasury is
  not counted as a decline.
- Tiles **flash** when their value changes.

Updates about every 3 seconds during market hours, 15 seconds outside them, and 60
seconds at weekends.

## Sentiment

**Route:** `/sentiment`.

The **Market Regime Console** — market mood and market character on one screen. It
updates on its own about every two minutes whether or not the page is open; press
**Refresh** to force it.

- **Market Sentiment ring** — a 0–10 **contrarian** composite (higher = more fear,
  historically the better environment to sell premium), drawn as three arcs on one
  dial: **Day**, **Week** (the last 5 sessions' average) and **Month** (the full
  history's average). A **Model confidence** figure sits beneath it.
- **Market Trend ring** — the same three horizons for direction, 0–100, where 50 is
  neutral. The Day reading carries a five-state label — **Bull / Weak Bull /
  Neutral / Resilient / Bear** — and a plain-English suggestion.
- **Signals** — four tiles (Bias / Signal / Yesterday / Change) with rate-of-change
  readings and a divergence line beneath.
- **Regime block** — which of five regimes the tape is in (**Balanced**,
  **Trending**, **Breakout**, **Whipsaw**, **Stressed**), a confidence figure,
  diagnostic tags, and a table ranking all five by share with their change since
  the open. Trending and Breakout also carry a direction word (*Rallying*,
  *Retreating*, *Breakdown* and so on).
- **Components** and **Trend Detail** — press and hold either for a full breakdown.
- **Daily Sentiment & Trend** — two intraday graphs over the last five trading days.

> **An arc drawn as a plain track with an em-dash means "no usable reading", not
> zero.** Likewise a regime of **Unclear** means the evidence is genuinely weak.
> The app prefers to say nothing over stating a confident wrong number.

> **Read the LEAD figure in the regime footer.** It is the leader's margin over the
> runner-up. A 10-point lead is a real reading; a 0.2-point lead means the headline
> was very nearly a coin toss.

## Bull / Bear Map

**Route:** `/sentiment/bullbear`.

Where the market is strong and weak, as a tree you open one level at a time:
eleven sectors, the industries inside each, then the stocks inside those.

- **The page keeps two questions apart.** **Trend** is whether price is genuinely
  rising — the annualised slope of a regression through months of closes, scaled
  by how well the line fits. **vs SPY** is whether it is beating the index. A name
  can do either without the other, and no number here blends them.
- **The quadrant chip** names both at once. **Rising · Leading** is unambiguous
  strength, **Falling · Lagging** unambiguous weakness, **Rising · Lagging** is
  going up more slowly than the index — and **Falling · Leading** is the one
  worth learning. It is falling, just less than the index, which is exactly the
  row a relative-strength-only screen paints as a buy.
- **No reading** means the cascade could not score that row (too short or too
  thin a price series). It is an absence, not a neutral verdict.
- **The headline is a count, not a verdict.** "5 of 11 sectors rising and
  leading" describes the rows on screen. There is deliberately no risk-on /
  risk-off call here — **Sector Rotation** owns that read.
- **The chips under the headline** are the full distribution. All four quadrants
  stay on screen even at zero, because an empty trap bucket is itself worth
  knowing. A fifth **No reading** chip appears only when something is unscored.
- **Breadth** is the share of a group's members confirming its move. A sector
  rising on a quarter of its constituents is a fragile advance and the bar turns
  red to say so. **Stock rows have no bar** — a stock has no members — and a dash
  means no reading at all, which is not the same as 0%.
- **Two clocks, and they date different things.** Trend, vs SPY and Breadth come
  from **last night's** cascade; momentum needs months of history, so there is no
  intraday version of them. Only **Today** is live, refreshed every ~30 seconds
  from one batched quote call **whenever the market is open — including the
  extended-hours and curb sessions, not just the regular one**. Once the tape is
  genuinely closed it throttles to about once every 5 minutes, which costs
  nothing, because closed quotes do not move. If the quote line reports quotes
  unavailable, only the Today column is affected — the scores below it are fine.
- **Click a sector** to build its industries, **an industry** to build its
  stocks. Nothing below the sector level is loaded until you ask for it, and
  more than one can be open at a time.
- **Refresh** re-pulls the quotes and republishes the map.

## Sector & Industry

**Route:** `/sentiment/sectors`.

A heat grid for the eleven S&P sectors, each expandable into its industries.

- **Day / Week / Month** are filled tiles, not plain numbers. **The colour is the
  size of the move as well as its direction**, so the shape of the day is visible
  before you read a single figure.
- **Each column is judged against itself.** Day is compared to the day's own
  spread, Week to the week's, Month to the month's — so a strong day still looks
  strong inside a quiet month.
- **Small moves deliberately stay dark.** Under ±0.50% (Day), ±1.00% (Week) or
  ±1.50% (Month) a tile reads flat, so only moves worth noticing light up.
- **Click Day, Week or Month to sort by it**; click again to reverse. The
  **RANK n OF 11** line under each sector name follows whichever column you sorted
  by.
- **Click a row**, or use **Expand all** / **Collapse**, to see industries. They
  render as the same tiles on a shorter row.
- **P/C** (put/call) stays a plain number, tinted amber above 1.5. It is a ratio
  rather than a return, so it gets no tile.
- A line above the grid gives the **regime word** with the
  **cyclical-versus-defensive** spread behind it, and — because the grid itself is
  unweighted — the percentage of sectors green, the cap-weighted move and a 0–10
  score.
- **Rotation quadrants live on the RRG and Sector Rotation tabs**, not here.

## Sector Rotation

**Route:** `/sentiment/rotation`.

Which sectors money is rotating into and out of, measured against SPY.

- A **verdict strip**: the regime in one word (Risk-on / Risk-off), a plain sentence
  saying what it means, and a **diverging gauge** putting the cyclical-versus-defensive
  spread on a −3 to +3 scale. The bar runs from zero out to the reading — left of
  centre is risk-off, right is risk-on — with both **±1.50 triggers** ticked, so you
  can see at a glance whether the signal has cleared its threshold or is sitting on it.
- Beside it the spread itself, and a line telling you whether it **just** cleared the
  trigger (a fresh signal) or is **well past** it (entrenched).
- A **flow band** showing where the index's weight is moving. Each sector is a block
  whose **width is its S&P 500 weight**, so the picture answers "how much of the market
  is actually rotating?" rather than "how many sectors are". The red side is rotating
  out, the green side in, with both totals and sector counts underneath.
- **Four quadrant panels** — Improving, Leading, Lagging, Weakening — each showing its
  share of the index and one card per sector with **RS-Mom** (momentum) and a weight
  bar. All bars share one scale, so a long bar always means a heavy sector.

The quadrant map table and the Rotating From / Into lists were replaced by the band and
the panels, which carry the same sectors plus the weight the table never showed. The
**RRG** tab has the RS-Ratio detail. This page refreshes **only when you press Refresh**.

## RRG

**Route:** `/sentiment/rrg`.

The same rotation data drawn as a Relative Rotation Graph: every sector plotted on
strength (left–right, RS-Ratio) against momentum (up–down, RS-Mom), with the crosshair
at 100/100 — the S&P itself.

- The four quadrants are **tinted**, so a sector's position tells you its state without
  a colour key: top-right **Leading**, bottom-right **Weakening**, bottom-left
  **Lagging**, top-left **Improving**. Sectors tend to rotate clockwise.
- **Dot size is the sector's S&P 500 weight, by area.** This is the part worth having:
  a heavyweight sliding into Lagging is a market event, a 2% sector doing the same is
  not, and on this plot you can see which is which.
- **Each trail is that sector's last five readings**, drawn as a smooth curve that thins
  and fades toward the oldest, so it points the direction of travel.
- Markers are labelled with the **sector name**, not the ETF ticker.
- A **tinted strip** above the plot repeats the Risk-on / Risk-off verdict and the
  numbers behind it.

Refresh-only, like Sector Rotation.

## Momentum

**Route:** `/sentiment/momentum`.

A momentum screen across three levels — sectors, about 70 industry ETFs, and 311
stocks. **Recomputed once nightly at 16:20 CT**, not live, because it is built on
daily bars.

The page reads as **five numbered steps**, top to bottom.

1. **Is momentum worth trading today?** All three states are shown side by side with the
   live one enlarged, so you can see what today *isn't* as well as what it is.
   *Favorable* means trending conditions; *Neutral* means chop; **Suppressed** means
   momentum-crash risk. Each card ends with what to do about it. Underneath,
   **dispersion** as a percentile — low dispersion means everything is moving together,
   so a relative-strength screen has little to separate.
2. **Three levels, and where they agree.** How many names in each universe are in their
   own top quartile, plus the **stocks whose industry and sector both confirm** — the
   highest-conviction rows on the page. The green panel gives the count *and lists every
   one of them by rank*. Click a ticker to decompose it in step 4; because these are
   stocks, that also switches the dropdown to Stocks. Hover a ticker for its sector and
   industry.
3. **Where the names sit** — the four quadrants as counts, with the strongest names in
   each and **+N more** opening the full membership of that quadrant. This is where you
   get the list of what is Leading, Improving, Weakening or Lagging.
4. **What a score is made of** — **click any name** in section 3, or any leaderboard row,
   and it is decomposed here into its five z-scores, as bars either side of a centre line
   (the universe average). With nothing selected it shows the current leader; **Top
   ranked** returns to it.
5. **Rank over recent sessions** — steady climbers beat one-day pops. A short line means
   that name has fewer stored sessions, not a shorter trend.

Then **limits** cards spelling out what this page cannot tell you, and a footer counting
**excluded** symbols, so a delisted or renamed ticker becomes visible instead of
silently vanishing.

**Full leaderboard** at the bottom is collapsed — open it for the ranked top/bottom-15
table with every component column. Use the dropdown to switch between industries and
stocks.

---

# STRATEGY — what should I trade?

**Strategy Tools** model legs you bring; the **Options** group works through
signals the app finds; **Trade Analyzer** judges a single stock; **Claude Trades**
watches the autonomous trader.

## Calculator

**Route:** `/options/calculator`.

An options P&L calculator for **any multi-leg structure**, with a price/time matrix.
The screen is three numbered steps down a fixed left-hand column — **① Strategy**,
**② Symbol**, **③ Legs** — with the results (six metric cards over the P&L matrix)
in the column beside them. It wears its own near-black palette rather than the
app-wide navy.

**Inputs:**

- **① Strategy** — a cascading menu of templates: **Single** (long/short call/put),
  **Credit spread** and **Debit spread** (call/put), **Condor** (iron, all-call,
  all-put), **Butterfly** (call, put, iron), **Calendar** and **Diagonal** (call/put).
  Picking one fills the leg editor with sensible at-the-money strikes. Underneath,
  tag chips say whether the structure takes in a **credit** or costs a **debit**, how
  many legs it has, and its lean (bullish, range, pin, defined risk…), followed by a
  one-line description of what the trade is betting on. Only the credit/debit chip is
  coloured — the rest are descriptions, not recommendations.
- **② Symbol** — type a ticker and press Enter or tab out (or press **Load chain**);
  a full-screen wait overlay shows while the chain loads. The pill in the title bar
  reads **AWAITING SYMBOL** → **LOADING CHAIN** → **CHAIN LOADED · SYM**, and the
  line under the buttons reports how many strikes and expiries arrived. The same
  frame holds **Spot**, **Price**, **IV %**, **Rate %**, **IV Δ %**, **Contracts**,
  **Strikes** (how many real chain strikes either side of spot the matrix spans,
  default 24) and **Expiry**. The top-level **Expiry** propagates to *every* leg.
  **IV Update** implies the volatility from the traded contract's own mark, the way
  ThinkorSwim does, falling back to the chain's at-the-money volatility before you
  have picked a strike.
- **③ Legs** — an editable **card per leg**: **Type** (call/put), **Side**
  (long/short), **Expiry**, **Strike**, **Qty**, **Premium**, and the leg's **Delta**
  read straight from the option chain. **Add leg**, **Reset to template**, and a
  remove ✕ that locks at the last leg. Each leg carries its **own expiry** (so
  **calendars/diagonals** price each leg on its own clock) and its own quantity (so a
  1-2-1 butterfly body trades at 2×). **Fetch Premiums** fills each leg's premium
  from the chain.
- The frame's header strip keeps a running **leg count**, **net premium** and **max
  loss**. All three update as you edit. A **dash** there means *not known yet* rather
  than zero — net premium is blank until every leg is priced, and max loss is blank
  when the loss has no bound (a naked call) or cannot be settled on one date.
  **Delta** shows a dash the same way whenever the chain carries no Greeks, which is
  normal outside regular trading hours.

**Outputs (after pressing Calculate):**

- **Six metric cards**, always in this order: **Entry credit/debit** (with the
  position size beneath it), **Max risk**, **Max return**, **Return on risk** (with a
  per-day figure), **Breakeven(s)** (with the first crossing's distance from spot),
  and **Probability of profit**. The credit-spread/iron-condor and single-leg metrics
  use the exact closed-form formulas; **butterflies, calendars, and other structures**
  are measured numerically off the value-at-expiration curve (max profit/loss + every
  break-even crossing). A card reads **Unlimited** where there genuinely is no cap —
  a long call's upside, a naked call's risk — and a **dash** where there is no
  reading at all, never a misleading `$0`.
- A **P&L matrix** — rows are real chain strikes around spot, columns are evaluation
  dates running from **Now** (the trade's current mark-to-market) to **Exp** (the
  payoff at expiration). Each cell shows the dollar P&L and a percentage, shaded
  green (profit) to red (loss). Your spot row is picked out in amber and scrolled
  into view.
- **What the percentage is a percentage of is written in the column heading.**
  **% MAX** means a share of the most the structure can make; **% COST** means a
  share of what you paid, and appears when the payoff has no cap to measure against;
  a plain **%** over dashes means neither applies. (It used to be a share of the
  premium received — for a credit spread that is the same number, because the credit
  *is* the maximum return.)

If you arrived here via **Send to Calculator** from a signal table, the form is
pre-filled and the calculation runs automatically. **Copy to Simulator** sends the
current legs straight to the Simulator (and the Simulator's **Copy to Calculator**
brings them back), so you can move a structure between the P&L cards and the
scenario/Greeks views without re-entering it. Loading a **different** symbol clears
the cards and matrix — they belonged to the old symbol; reloading the **same** one
keeps them.

## Simulator

**Route:** `/options/simulator`.

Re-prices a **multi-leg** option position under different scenarios using
Black-Scholes. Start by entering a **Symbol** and pressing **Fetch snapshot**, then
pick a **Strategy** (the same template menu as the Calculator — singles, verticals,
condors, butterflies, calendars/diagonals) and adjust the **legs** in the editor —
one **card** per leg with **Type** (call/put), **Side** (long/short), **Expiry**,
**Strike** and **Qty**, plus **Add leg** and a remove ✕ that locks at the last leg
(a position with no legs has nothing to simulate). The Simulator's snapshot carries
no Greeks, so its leg cards show no Delta column — the Calculator's do. Every tab
below operates on the **netted** position (all legs summed). Three tabs:

- **Replay** (default) — re-prices the position along the underlying's recent price
  path and shows a **six-panel stack** (price plus Delta, Gamma, Theta, Vega, Rho).
  A **scrub slider** moves a cursor through the trace; a **Look-back** dropdown
  controls how far back the path runs (Auto by DTE, or fixed windows).
- **What-if** — a **ΔS** slider (instant client-side price overlay) and a **Δt**
  slider that fast-forwards **elapsed** days from now; each leg decays on its **own**
  clock, so a **calendar's** back leg correctly keeps its time value while the front
  leg expires. An IV-shock comparison bar chart appears below.
- **IV Shock** — an **IV multiplier** slider compares the position at base IV vs
  shocked IV across Price, Delta, Gamma, Theta, and Vega.

**Copy to Calculator** sends the current legs to the Calculator for the metric cards
+ P&L matrix (the Calculator's **Copy to Simulator** brings them back).

## Market Scanner

**Route:** `/options/scanner`.

The Market Scanner continuously looks for credit-spread opportunities and lists them in a
two-pane layout.

**Left pane — the signal list:**

- A **Run scan** button (forces a refresh) and a status line ("N live signals").
- Three tabs: **0-DTE**, **Swing** and **Directional**. Directional lists
  single-leg long and short calls and puts, scored on a *different* scale from the
  credit-spread tabs — do not compare their numbers.
- A table of candidate signals. Columns include Symbol, Type, Expiration, DTE,
  Short/Long strikes, Credit, Max Loss, Risk/Reward %, Probability of Profit %,
  a color-coded **Score** chip, and a letter **Grade**.
- A plain-English **VIX term** label (for example, *"VIX term: Contango (near-term
  calm) · as of 1:32 PM"*).
- Brand-new signals get a **NEW** badge.

**Right pane — the detail panel:** click any row to see its full breakdown
(credit, max loss, DTE, delta, theta, IV rank, and more).

**Per-row action buttons** (also see *Cross-page actions* below):

- **Send to Calculator** — open the P&L Calculator pre-filled with this trade.
- **Send to Paper Trade** — create a paper trade from this signal.
- **Expected Move** — open the Expected Move chart for this trade in a new tab.

The list re-scans itself every 15 minutes between 08:00 and 15:15 CT on trading
days; you rarely need to press **Run scan**.

> **The table shows the whole day's signals, not just the current scan.** A signal
> that has stopped qualifying stays visible but is **dimmed and frozen**, stamped
> with the time it dropped out, and its **Paper** button is removed — its price is
> stale, so a paper entry from it would be fictional. The status line's "N live
> signals" counts only those still qualifying.

> **An empty Directional tab is normal.** The engine only emits candidates scoring
> 50 or better, so empty means nothing cleared the bar rather than something
> failed. Index names (`$SPX`, `SPY`, `QQQ`) are also frequently absent, because
> their implied volatility is usually too low to clear the credit floor.

## Strategy Finder

**Route:** `/options/swing`.

A focused, on-demand scan for one symbol over a swing horizon. Enter the
parameters and press **Scan**:

- **Symbol**
- **DTE** min / max (days to expiration)
- **Put Δ** and **Call Δ** min / max (delta bands for strike selection)
- **Min credit %**

Results appear in the same signal table (with the same Score chip, Grade, and the
three per-row action buttons) and detail panel as the Market Scanner.

## Expected Move

**Route:** `/options/expected-move`.

Charts a symbol's recent price action with a forward **expected-move cone** out to
an option's expiration.

**Inputs:** **Symbol**, **Expiry** (YYYY-MM-DD), an optional **Strike** with a
put/call toggle, and a **Look-back** dropdown (Auto ≈ 3× DTE, or 1mo / 3mo / 6mo /
1y). Press **Draw**.

**The chart** is a daily candlestick with:

- Dashed **upper/lower expected-move** bands fanning out to expiration.
- **Leg strike lines** if you provided strikes (short = solid, long = dashed;
  put = red, call = blue), each labeled.
- A price crosshair and a date-aware tooltip. Non-trading days are collapsed so
  there are no blank weekend gaps.

You usually reach this page through the **Expected Move** button on a signal row
(it opens in a new browser tab, pre-filled and drawn).

## Captured Signals

**Route:** `/options/captured`.

Signals the system has "captured" to track over time, with live re-pricing.

- **Action buttons:** Reload, **Refresh marks (live)** (re-price all open
  signals), **Close selected** (enter an exit value and reason).
- **Table:** a color-coded **Rec** (green TAKE_PROFIT / red CUT / amber HOLD),
  Symbol, Strat, Mode, Opened, Exp, DTE, Credit, **Cur Price** (what the spread
  costs to close now), Risk, P&L (green/red) and Grade. It opens **newest capture
  first**; click any column heading to re-sort it.
- Click a row for its detail panel; the clicked row is also the one **Close
  selected** acts on.
- **Footer:** opened today, closed today, P&L today (booked) and P&L today (open).
  The open figure covers every signal still running, and shows a dash rather than
  $0.00 until you have priced them with **Refresh marks (live)**.
- When a tracked signal hits a stop or target, the page raises a notification.

## Paper Ledger

**Route:** `/options/paper`.

A manual paper-trading ledger.

- **Action buttons:** Reload, **Close selected** (enter an exit debit), **Analyze
  selected** (live Greeks + P&L overlay), **Delete selected**, **Delete all
  closed**.
- **Table:** Trade ID, Symbol, Strategy, Strikes, Expiration, Qty, Entry Credit,
  Max Loss, P&L, Status, Entry Time.
- Click a row to load its **detail panel**; the app automatically runs a live
  analysis and overlays current Greeks and P&L.
- Each row also has an **Expected Move** button.

## Paper Account

**Route:** `/options/portfolio`.

The account view for the automated paper-trading engine.

- **Account cards:** Equity, Cash, Buying-power reserved, Session P&L, Total P&L,
  Open count, and engine status (RUNNING / HALTED).
- **Action buttons:** Reload, **Run entry cycle** (open positions for eligible
  captured signals), **Run manage cycle** (re-price and auto-close), **Reset**
  (set a new starting balance).
- **Open Positions** table and a **Fills log** (last 100 orders).

> The entry and manage cycles also run automatically **at the top of each hour,
> 09:00–14:00 CT** on trading days — there is no 15:00 run. So a target hit at 09:15
> is acted on at 10:00 unless you press **Run manage cycle** yourself. (The
> autonomous driver's separate account re-prices every minute; this one does not.)

## Rescue

**Route:** `/options/rescue`.

An advisory **and** one-click-apply tool for **tested credit spreads** — put credit
spreads (PCS), call credit spreads (CCS), and iron condors (IC) — that have moved
against you. It tells you whether a position is in trouble and offers a ranked menu
of concrete ways to fix it, with the commission-adjusted cash and risk of each.

**Two sub-tabs** sit under the page tabs:

| Sub-tab | What it does |
|---------|--------------|
| **At-Risk Board** | The default. Scans your paper and captured positions and lists the ones in trouble. |
| **Ad-hoc Trade** | Enter a position **by hand** and get the same ranked repair menu for it — use this to evaluate a spread the app is not tracking. |

**The at-risk table** (top of the page) lists every paper position and captured
signal the system has flagged as **tested** or **critical**, heat-colored and sorted
by **heat** (a 0–100 danger score — higher is more urgent). A position earns its
heat from how close the underlying is to the short strike, the short-leg delta, P&L
versus the credit taken in, days to expiration, and dealer-gamma / market-regime
context. Detection rides the paper engine's 5-minute manage cycle, so the table
stays current on its own.

**A red badge** on the **Rescue** nav item shows how many positions are currently
tested or critical. Opening the page clears it.

**Select a row** to load its **rescue candidate menu** — a ranked set of cards, each
a different way to adjust the position:

- **Close** or **partial-close** the spread.
- **Narrow** it (roll the long leg in toward the short).
- **Convert** a one-sided spread to an **Iron Condor** or **Iron Butterfly**.
- **Roll** the spread **down**, **out** (later expiry), or **down-and-out**.
- Advisory-only ideas: **broken-wing**, **inverted**, and a **futures hedge**.

**Reading a candidate card:**

- The **action label** and a **score** (higher = the engine likes it more).
- **Gross / commission / net** cash — what the adjustment brings in or costs before
  fees, the Schwab commission, and the net after fees. Commissions are real Schwab
  rates and are built into the ranking, so an action that only works by paying a
  debit is penalized.
- New **max-loss**, **breakeven**, **short-delta**, **width**, and **expiry** for the
  position after the adjustment.
- The **option legs** the adjustment would trade.
- A **rationale**, **strategic-context** notes (the dealer-gamma read — e.g. rolling
  below the gamma flip is risky, resting on a put wall favors a bounce — how it fits
  the current regime, and settlement mechanics: index spreads are European,
  cash-settled with no early assignment; equity/futures spreads are American and
  carry assignment risk when in-the-money), and any **warnings**.

**Applying an adjustment.** Cards that the engine can execute show an **Apply**
button (behind a confirmation). Advisory cards (broken-wing, inverted, futures hedge)
instead say **"manual — place it yourself."** When you Apply, the app re-prices the
candidate's legs live and **only proceeds if the economics still hold**; if prices
have moved past tolerance, or the position is no longer open, it aborts without
changing anything and tells you **"prices moved — re-review."** Rolls close the old
position and open a new, linked one. Every applied adjustment is recorded in an audit
log.

> **Captured signals are advisory-only.** A captured signal that turns at-risk — for
> example one showing a **CUT** recommendation (a money/delta/time stop) — appears in the
> at-risk table and gets a full candidate menu, but it has **no Apply button** (there's no
> paper position to mutate). Use the menu as guidance and place the adjustment yourself.
> Captured signals do **not** add to the Rescue nav badge (that counts paper positions).

## Trade Analyzer

**Route:** `/trade`.

On-demand analysis of a single symbol. Type a **Symbol** and press **Analyze**
(tabbing out of the field does it too).

**Two more buttons sit beside Analyze, and each opens a separate screen in a new
browser tab:**

| Button | Opens |
|--------|-------|
| **Deep Dive** | A full standalone report for the symbol — technicals, fundamentals and short interest, plus options analytics (at-the-money IV, implied move, max pain, 25-delta skew, IV term structure, 30-day constant-maturity IV, net GEX and flip, open-interest walls) and an IV/RV rank. |
| **AI Query** | The same digest formatted as a **copyable chat prompt** you can paste into an AI assistant. It makes **no** API call itself, so it costs nothing. |

> IV rank reads "building" until enough daily snapshots have accumulated for that
> symbol. That is expected on a name you have just started analyzing.

- A **header** with the symbol, price, bias, and volume.
- **Two verdict cards side by side** — **Position** (1–8 weeks) and **Investor**
  (months+):
  - **Position (1–8 weeks)** is **backtested**. Rather than a hand-tuned score, it
    ranks the stock on a set of price/volume factors that were *validated against real
    forward returns* (which factors matter, and by how much, was learned from history —
    not guessed), then places it in a **calibrated band**. The headline shows the
    Buy / Hold / Sell verdict for that band plus what the band has *historically*
    delivered: a band **percentile**, an **expected return** over the model's horizon
    (≈ 4 weeks), and how often names in that band **beat the S&P 500** — e.g.
    *"90th pctile · +1.3% / 20d · 52% beat-SPY"*. Open **"Why — validated factors"** to
    see each factor's contribution (momentum, trend quality, relative strength,
    volatility, turnover…) and the **model's own track record** (its version date and
    out-of-sample accuracy). The previous hand-tuned verdict is still available under a
    collapsed **"Legacy heuristic"** section for comparison. *Honest note:* the edge is
    real but **small and regime-dependent** — treat it as one weighted input, not a
    guarantee. The **Investor (months+)** validation is deferred (it needs fundamentals
    history), so that card is unchanged.
  - **Investor (months+)** shows a Buy / Hold / Sell verdict (color-coded), a score,
    the top reasons, any hard "gates" that fired, and an expandable factor breakdown.
> **The Markov Forecast card was removed in June 2026** and no longer appears on
> this page. It projected the *legacy* technical-momentum score, which contradicted
> the validated Position read sitting beside it. The underlying forecast is still
> computed and still reaches the data feed, but nothing on this screen renders it.

- An **MTF EMA Alignment** card (per-timeframe trend agreement: Daily, 4H, 1H, 15m,
  5m, 1m).
- A **Momentum** strip (RSI, ADX, MACD histogram, VWAP, relative volume).
- A **Sector** card and, when available, a **Fundamentals** card (P/E, PEG, revenue
  & EPS growth, ROE, margin trend).

The analysis persists as you navigate away and back.

---

## Claude Trades

**Route:** `/driver`.

An **autonomous paper options trader**. Claude selects and sizes defined-risk credit
spreads from the scanner's output, and code-enforced guardrails cap the risk.
Everything is **paper** — nothing is ever sent to a live brokerage account.

> The old **order-approval queue** — where a morning agent proposed trades for you
> to APPROVE or SKIP — was removed in July 2026. This page is now purely a monitor
> with a stop button.

**Controls:**

- **Autonomous** — enable or disable the trader.
- **Run now** — fire one decision checkpoint immediately.
- **STOP** (confirm-gated) — halt new entries for the rest of the day. Open
  positions continue to be managed and closed.

**What you see:**

- **Tiles** — Day P&L against the day's target, Session P&L, Realized, Open P&L,
  Equity, and the open-position count.
- **Open positions** and a **decision log** (newest first, in CT) recording each
  checkpoint's reasoning and a one-line market-context summary.
- A **Performance scorecard** — trades, win rate, realized and total P&L, average
  win and loss, **profit factor**, best and worst trade, plus P&L broken down by
  symbol and by strategy.
- A **Performance** view listing closed trades with their exit reason (*Target hit*,
  *Delta stop*, *Time stop*, *Money stop*).

**When it runs:** a 09:28 ET morning checkpoint, then every 30 minutes within an
entry window of **09:45–15:30 ET**. The first quarter-hour after the open is skipped
so the structure is readable, and no *new* entries are taken in the last half hour.
Open positions are re-priced every minute during market hours regardless.

> **The daily target is not fixed.** The base is $500, ratcheted against the
> month-to-date pace — as high as $1,000 when behind, as low as $250 when ahead. A
> $1,500 daily loss halts new entries outright.

> **"Executed" in the decision log means the order was enqueued, not filled.** A
> trade can be logged as executed and then rejected by the risk guardrails; the real
> outcome is in the account view.

> **Enabling autonomy spends Claude API credit** on every checkpoint. The running
> count is on the Settings page.

---

## Cross-page actions

Three buttons appear on signal rows across the Options section:

| Action | Available on | Effect |
|--------|--------------|--------|
| **Send to Calculator** | Market Scanner, Strategy Finder | Opens the Calculator pre-filled (strategy, symbol, expiry, strikes, premiums, IV) and runs it. |
| **Send to Paper Trade** | Market Scanner, Strategy Finder | Asks for a quantity, then creates a paper trade. Stays on the current page. |
| **Expected Move** | Market Scanner, Strategy Finder, Paper Ledger, Captured, Calculator | Opens the Expected Move chart in a new browser tab, pre-filled and drawn. |

---

# ACCOUNT — what do I own?

Your real holdings, and the day's results.

## Portfolio

**Route:** `/portfolio`.

Your live Schwab portfolio, with sector context and a performance scorecard.

- A **status bar** — Refresh button, proxy up/down, live-stream on/off, holding
  count.
- **Holdings** tab — Symbol, Sector, Qty, Market Value, Day P/L, Total P/L,
  vs-Sector relative strength, and since-purchase excess. P&L cells are color-coded
  and update live.
- **Sectors** tab — your sector weights vs the benchmark, plus a tailwind reading.
- **Performance** tab — per-position letter grades (Return / Capital / Risk /
  Entry), a composite, annualized return, vs-sector, and drawdown. Click a row to
  see its **advisory suggestions** below the table.

P&L streams live tick-by-tick; press **Refresh** to rebuild sectors and grades.

---

## EOD Report

**Route:** `/eod` (with a `/eod/detail` drill-down).

An end-of-day rollup of the day's Options activity and Claude Trades.

**Two views**, reached by the link at the foot of the summary:

| View | Route | Contents |
|------|-------|----------|
| **Summary** | `/eod` | Headline tiles plus a Daily / Weekly (WTD) / MTD performance block **per book** — manual paper, the driver, and captured-closed. |
| **Detailed** | `/eod/detail` | The same performance plus breakdowns by **strategy** (PCS / CCS / IC), by **0-DTE vs swing**, and by **status** (open / closed / expired), then the full trade, scanner, captured and driver tables. |

Both views use a jump-link table of contents, and every section is collapsible —
which keeps working in the exported file as well as in the app.

**The buttons:**

- **Generate** snapshots the current data into standalone `summary.html` and
  `detail.html` archived by date under `webgui/data/eod/<date>/`.
- **Open summary file** / **Open detail file** open those archived files in a new
  browser tab.
- The **Archive** list reopens any past date.

> Realized P&L is bucketed by **exit** date, while opened trades and credit
> collected are bucketed by **entry** date. They answer different questions and will
> not reconcile to each other — that is correct, not a bug.

## User Manuals

**Route:** `/manuals` — a tab in the **More** group, alongside EOD Report. (It used
to be nested under Settings; it is now a peer tab.)

A simple index that links the four manuals — each opens in a new browser tab:

- **User Guide** — how to use the app (this document).
- **Reference Guide** — what each tab and sub-tab is for, why it matters and when to
  open it, starting from a one-page summary of the whole app.
- **Technical Reference** — the math behind every number.
- **API / Developer Reference** — the integration surface for developers.

The Word (`.docx`) copies live alongside the HTML under `docs/manuals/`.

---

# SYSTEM

The machine-level controls pinned to the foot of the rail. None of them is a step
in a trading workflow, which is why they sit apart.

## System Status

**Route:** `/status` — a standalone item at the **foot of the rail**.

A health board for the whole stack.

- An **overall banner** — green (all up), red (naming what's down), or grey
  (checking).
- A **component grid** — Memurai, the Schwab gateway, the six services, and the web
  app itself, each with Online/Offline and its tier. The gateway's card also shows
  the **Schwab auth** state.
- A **Re-authorize** button on the gateway card opens Schwab's OAuth login in a new
  browser tab. Use it when the auth line says the login has expired.
- A **data-freshness** table showing each domain's latest cache write and its age.
  This is the more informative half: a service can be *online* and still not be
  publishing, and only this table shows that.
- Every component has a **Restart** button, which relaunches it windowless.
- **Refresh** re-checks on demand; the board also re-checks on its own.

> **`token_expired: true` on the gateway is routine** — it refreshes itself. Only a
> missing or expired *refresh* token needs **Re-authorize**.

> **Restart order matters:** Memurai first, then the gateway, then services, then the
> web app. A service restarted while the gateway is down will start and then fail to
> fetch anything.

## Settings

**Route:** `/settings` — a standalone item at the **foot of the rail**, with System
Status and Stop All Services.

Preferences, all saved on your machine (there is no login):

- **Scanner alerts** — enable the audio alert, pick the sound (chime / bell /
  ping), a **Test sound** button, a **Volume** slider, an **only during market
  hours** toggle, and a **minimum score to alert**.
- **Spoken alerts (Desk)** — the Desk announcing new flow alerts and newly-opened
  positions out loud. An **on/off** switch, a **Voice** picker (six neural voices;
  Aria is the default), a **Volume** slider, and a **Test voice** button. There is
  deliberately no second market-hours toggle here: spoken alerts obey the *only
  during market hours* switch in **Scanner alerts** above.
- **Desktop notifications** — enable them and grant the browser permission.
- **Flow alerts** — whether put/call premium crossovers and unusual activity alert
  you.
- **Captured trade auto-management** — whether captured signals are actively
  managed (break-even stop after +50%, deferred delta cuts on recoverable trades,
  auto-close on the exit rules). Off leaves them advisory.
- **Manual paper: break-even lifecycle (experimental)** — opts the manual paper
  account into that same lifecycle instead of taking profit at +50% immediately.
  The autonomous driver's account is never affected by this toggle.
- **Market summary ticker** — the scrolling bar at the bottom of every page, with a
  speed setting.
- **Appearance** — every colour, font and menu style, in seven tabs. **Save &
  restart web GUI** applies the change; **Reset to defaults** is confirm-gated.
- **API usage** — how many calls the app has made to **Schwab** (counted at the
  gateway) and to **Claude** (counted at each call site), for today, the last 7 days
  and the last 30.
- **Maintenance** — **Vacuum GEX history DB** compacts the intraday options
  database and reports the before-and-after size.

> Clicking **Test sound** — or **Test voice** — also unlocks browser audio for the
> session. Browsers block sound until you interact with the page, and they do it
> silently, so a Desk that never speaks is usually blocked rather than broken. The
> Desk shows an **Enable spoken alerts** button when that happens.

> **The first time a given phrase is spoken it takes a second or two to generate**,
> because the clip is made on demand; after that it plays from a cache on your own
> machine and is instant. The app pre-generates the common flow phrases in the
> background at startup, so in practice you rarely hear the delay.

> **Turning the ticker off also stops the Claude calls behind it**, so it is a cost
> control as well as a display setting.

> **Run Vacuum after hours.** It locks the database for minutes, and the tool
> refuses to run while the collector is active.

**User Manuals** is no longer nested under Settings — it is a tab in the **More**
group, next to EOD Report.

## Stop All Services

**Route:** `/terminate` — the red button at the foot of the rail.

A guarded "stop the whole local stack" page. The red **Stop all services** button
(behind a confirmation) stops the gateway, the six services, and the web app.

> **This also stops the page you're on** — it will become unresponsive right after
> you confirm, by design. Memurai is left running. Re-launch with `start_all.bat`
> or `start_all_wt.bat`.

---

# FAQ & Troubleshooting

**A red "proxy unreachable" banner is on every page.**
The Schwab gateway isn't running. Open **More → System Status**, check the
schwab-proxy card, and use its **Restart** button — or relaunch the whole stack.

**A page says "Waiting for … service."**
That page's service isn't running. Restart it from **System Status**, or relaunch
the stack.

**Scans return nothing, or Dealer Positioning shows "no data."**
On weekends and outside market hours, options data is sparse and 0-DTE scans
legitimately return few or no signals. This is expected, not a failure.

**No alert sound plays.**
Browsers block audio until you interact with the page. Click a nav link or press
**Test sound** on the Settings page. Also confirm the alert toggle is on, the
volume is up, and (if enabled) that you're within market hours and above the
minimum score.

**The Status page flags data as STALE.**
A scheduled view hasn't updated recently — usually because its service stopped.
Restart that service. On-demand views (Trade, Driver) are never flagged stale.

**Schwab Authorization shows as not authorized / expired.**
Open **System Status → Schwab Authorization → Authorize** to re-run the OAuth
login in a new tab.

**My live portfolio P&L isn't moving.**
Check the Portfolio status bar: if the live-stream indicator is off or the proxy is
down, ticks aren't arriving. Press **Refresh**, and verify the gateway on the
Status page.
