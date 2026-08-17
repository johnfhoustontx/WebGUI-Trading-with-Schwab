[TOC]

# The app in one page

Read this section first. Everything after it is detail.

## What this app is

This is a **local options-trading workbench**. It runs on your own computer, talks to
your Charles Schwab account through a small gateway program, and puts everything you
need to make an options decision on one screen — market conditions, trade candidates,
pricing models, a practice account, and your real holdings.

It does not place real orders. Every trade the app opens is **paper** — a simulated
position priced against real market data. The only real money on screen is your Schwab
portfolio, which is read-only.

## The one idea behind it

Most retail options tools show you *prices*. This app is built around a different
question: **who has to trade next, and why?**

When you buy an option, a professional market maker (a "dealer") usually takes the
other side. Dealers do not want a directional bet — they want the fee. So they hedge,
buying or selling the underlying stock to stay neutral. As price moves, their required
hedge changes, and they must trade again. That forced, mechanical flow is large,
predictable, and visible in the options chain if you measure it.

Almost every screen in this app is a different lens on that idea:

- **Dealer Positioning** measures the hedging pressure directly.
- **Flow Alerts** and **Net Prem** watch where the option money is actually going.
- **Sentiment** and **Market Regime** describe the environment that pressure acts in.
- **Market Scanner**, **Strategy Finder** and **Rescue** turn it into specific trades.

If you only ever learn one concept from this guide, make it dealer gamma — it is
explained in plain language under [Dealer Positioning](#dealer-positioning).

## How the app is put together

Three layers, running as separate programs on your machine:

| Layer | What it is | Why you care |
|---|---|---|
| **The gateway** | `schwab-proxy` on port 8100. Holds your Schwab login and fetches all market data. | If this is down, nothing has fresh data. Start it first. |
| **The services** | Six background programs (ports 8210–8215), one per subject area: sentiment, options, portfolio, trade, driver, market. | They do the work — scanning, scoring, collecting — whether or not a browser is open. |
| **The web app** | What you look at, on port 8500. | It **only displays**. It never calculates anything itself. |

Between them sits a small in-memory database (Memurai, the Windows build of Redis).
Services write their results into it; the web app reads them and repaints.

The practical consequence: **a page showing stale or missing data is almost always a
service problem, not a page problem.** [System Status](#system-status) tells you which
one. Refreshing your browser will not help; restarting the service will.

## The three questions the menu answers

The left menu is grouped into three captioned sections. Each answers one question.

### MARKETS — *what is the market doing?*

| Page | Reach for it when… |
|---|---|
| **Dealer Positioning** | You want to know where price is likely to stick or accelerate today. |
| **Opportunity Board** | You want the whole watchlist ranked on one screen. |
| **Flow Alerts** | You want to see the unusual option activity the app flagged today. |
| **Trend & Sentiment** ▸ Market Dashboard | You want the macro tape — volatility, breadth, sectors, futures — at a glance. |
| ▸ Sentiment | You want one number for market mood, plus what *character* the tape has. |
| ▸ Sector & Industry | You want to know which sectors and industries are working. |
| ▸ Sector Rotation | You want to know whether money is moving to offense or defense. |
| ▸ RRG | You want the same rotation picture as a map with trails. |
| ▸ Momentum | You want to know what has been persistently strong, and whether chasing pays. |

### STRATEGY — *what should I trade?*

| Page | Reach for it when… |
|---|---|
| **Strategy Tools** ▸ Calculator | You have a specific trade in mind and want its risk, reward and breakeven. |
| ▸ Simulator | You want to know how that trade behaves as price, time and volatility change. |
| **Options** ▸ Market Scanner | You want today's ranked credit-spread and directional candidates. |
| ▸ Strategy Finder | You have one symbol and want the best structure for it. |
| ▸ Expected Move | You want to see whether your strikes sit outside the market's expected range. |
| ▸ Captured Signals | You want to follow signals you bookmarked and see if they worked. |
| ▸ Paper Ledger | You want your own hand-kept practice trades. |
| ▸ Paper Account | You want the automated practice engine's account. |
| ▸ Rescue | You have a credit spread going wrong and want ranked repair options. |
| **Trade Analyzer** | You want a Buy/Hold/Sell read on one stock. |
| **Claude Trades** | You want to watch (or stop) the autonomous paper trader. |

### ACCOUNT — *what do I own, and how did I do?*

| Page | Reach for it when… |
|---|---|
| **Portfolio** | You want your real Schwab holdings with live P&L and sector context. |
| **More** ▸ EOD Report | You want the day's results across every book. |
| ▸ User Manuals | You want this guide and the other three. |

At the very bottom of the menu sit three machine-level controls — **System Status**,
**Settings**, and a red **Stop All Services** button. They are separated deliberately:
none of them is a step in a trading workflow.

## A trading day, page by page

This is the intended path through the app. You will not use every page every day.

**Before the open (07:00–08:30 CT).**
Start at **Market Dashboard** for the overnight tape — futures, volatility, currencies.
Then **Sentiment** for the mood reading and the day's regime. Then **Dealer
Positioning** on `$SPX` or `SPY`: note the gamma flip level and the call/put walls,
because those are the levels price will interact with all day. The app has already run
a **premarket Claude briefing** you can open from the **Briefings** dropdown.

**At the open (08:30–09:00 CT).**
The first fifteen minutes are deliberately skipped by the automated trader, and you
should treat them the same way — opening prints distort every measure on these screens.
Watch **Flow Alerts** for early unusual activity.

**Finding a trade (09:00 CT onward).**
**Market Scanner** auto-scans every 15 minutes and ranks candidates 0–100. Or start
from **Opportunity Board**, pick the hottest symbol, and run **Strategy Finder** on it
to compare every structure. Either way, send the candidate to **Calculator** to see its
risk and breakeven, **Simulator** to see how it behaves, and **Expected Move** to check
your short strike sits outside the expected range.

**Committing.**
Send it to **Paper Ledger** (your own book) or let the engine work in **Paper Account**.
**Captured Signals** tracks anything you bookmark without committing to it.

**Managing.**
**Rescue** flags credit spreads that have gone against you and ranks the repairs.
**Claude Trades** shows what the autonomous trader did, with a STOP button.

**After the close.**
**EOD Report** aggregates every book. **Portfolio** shows the real account.

## What the numbers are, and are not

Three things are worth knowing before you trust a screen:

1. **Almost everything is derived, not quoted.** Schwab does not publish a
   time-and-sales tape for options, so "premium flow" in this app is *unsigned traded
   dollars* — money that changed hands through calls versus puts. It is a
   money-weighted put/call read, **not** net buying. Nothing on these screens can tell
   you whether a trade was opened or closed, or who initiated it.

2. **Some numbers deliberately disagree with your broker.** The
   [Expected Move](#expected-move) page's implied volatility and move are defined
   differently from ThinkorSwim's, for documented reasons. Do not "fix" them to match.

3. **Where a signal is weak, the app says so.** An empty gauge arc means "no usable
   reading", not zero. A regime labelled *Unclear* means the evidence genuinely is.
   That is a design choice — an earlier version rendered missing inputs as confident
   numbers, which is worse than showing nothing.

## Conventions used throughout this guide

- **All times are US Central (CT)** unless stated. The regular session is
  08:30–15:00 CT (09:30–16:00 ET).
- **DTE** means days to expiration. **0-DTE** means expiring today.
- **PCS** = put credit spread (mildly bullish). **CCS** = call credit spread (mildly
  bearish). **IC** = iron condor (both, neutral).
- Scores are **0–100 with higher meaning better**, except the sentiment composite,
  which is **0–10 and contrarian** — high means fearful, which historically has been
  the better time to sell premium.

---

# MARKETS

The four entries in this section answer *what is the market doing right now*. Nothing
here proposes a trade; it establishes the conditions a trade would be taken in.

## Dealer Positioning

*Menu: MARKETS → Dealer Positioning · Route `/options/gamma`*

### What it is

The most important — and least familiar — screen in the app. It measures how much
stock option dealers are forced to buy or sell as price moves, and at which strikes
that pressure concentrates.

**The concept, in plain language.** When you buy a call, a dealer sells it to you. To
avoid taking your directional bet, the dealer buys some of the underlying stock as a
hedge. How much they need depends on the option's *delta* — its sensitivity to price.
But delta itself changes as price moves, and the rate of that change is called
**gamma**. So gamma tells you how *quickly* the dealer's hedge goes stale, which is the
same as saying how often they must trade.

Two situations follow, and they are opposites:

- **Positive gamma** (dealers are net long options). Price rises → the dealer's hedge
  needs *less* stock → they **sell into strength**. Price falls → they **buy weakness**.
  This dampens moves. Price tends to grind and pin.
- **Negative gamma** (dealers are net short options). Every hedge goes the *same*
  direction as the move — they sell as it falls and buy as it rises. This amplifies
  moves. Trends run and reversals are violent.

The price at which the sign flips is the **gamma flip**. It is the single most useful
level on this page. Above it, expect mean-reversion; below it, expect momentum.

> **Background reading.** The metric was popularized by SqueezeMetrics' *Gamma Exposure
> (GEX)* white paper (2017) and their later *The Implied Order Book* (2020). A readable
> vendor explainer is [SpotGamma's Gamma Exposure page](https://spotgamma.com/gamma-exposure-gex/).
> Note that every provider computes GEX slightly differently — the numbers on this page
> will not match another service's, and the *shape* is what you should read.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211) |
| Cache keys | `cache:options:gamma`, `:gamma_symbols`, `:net_premium`, `:gamma_analyze*`, `:gamma_briefings` |
| Collection | One snapshot **every minute**, 08:00–15:20 CT on trading days, across ~45 watchlist symbols |
| Page refresh | Every 2 minutes; the symbol dropdown refreshes on change |
| Storage | `gex_history.db` — five series per symbol per minute (gamma, charm, delta, vanna, premium) |
| Off-hours | The **last available session** stays on screen 24/7. Friday's chart holds all weekend. |

### Reading the screen

**Subtabs (the small row under the page tabs).** Seven lenses, all on the same data:

| Subtab | What it measures | When it matters |
|---|---|---|
| **Gamma** | Hedging *intensity* — how hard dealers must trade per point of movement. | Always. This is the default and the main read. |
| **Charm** | How dealer hedges decay as **time** passes, with price unchanged. | Fridays and expiration days, when decay is fastest. |
| **Delta** | The dealers' *directional* exposure in dollars. | Judging which way the aggregate hedge leans. |
| **Vanna** | How hedges change when **volatility** changes. | Volatility-event days — CPI, Fed, earnings. |
| **Flow** | Call versus put premium through the day, as a ribbon. The **crossover** is the read. | Confirming a directional bias intraday. |
| **Net Prem** | Net premium (call dollars − put dollars) for up to 28 symbols at once. | Comparing where money is going across names. |
| **Term** | The same exposure across the **next five expirations**. | Deciding which expiry to trade. |

The first four share one layout and are described together below. **Flow**, **Net
Prem** and **Term** are different screens with their own controls, and each has its
own subsection further down.

**Bars (top chart).** Net exposure at each strike, ±20 strikes around the current
price. The window is fixed so bar width stays constant all day. Blue/cyan is
call-heavy, magenta is put-heavy. A tall bar is a strike where a lot of hedging
pressure is concentrated.

**Heat map (below).** The same thing over time — strike on the vertical axis, clock on
the horizontal. Colour intensity is the size of the exposure; near-zero fades to
transparent so quiet strikes read as empty rather than as a colour. This is where you
see pressure *building* at a level during the session.

> **Press and hold** the left mouse button on the heat map to read a cell's value. It
> follows the cursor while held. Plain hovering shows nothing — this is deliberate, so
> the tooltip does not flicker as you move across the chart.

**The lines across the heat map.**

- **Spot** — the current price, white with a dark halo.
- **Call wall / Put wall** — the strikes with the largest call and put exposure. These
  behave as resistance and support respectively, because dealer hedging there is
  strongest.
- **Flip** — the gamma flip level described above.
- **Proj. flip** (dashed amber) — where the flip would sit once today's expiring
  options have decayed. The **gap between it and the actual flip is the drift** price
  is being pulled through as the day passes. Only drawn when something expires today.

**0-DTE hedge pressure (the small panel under the heat map).** A signed column chart in
billions of dollars. Cyan means dealers must **buy** into the close if price holds;
magenta means they must **sell**. This is the most actionable single number on the
page in the last hour of trading.

**Level movement** (switch, off by default) overlays how the flip and walls have moved
during the session, as step lines. Useful when levels are migrating; noisy otherwise.

**Spot picker.** Draw the price overlay as a **Line**, **Candles**, or **OHLC**, with a
1/5/15-minute bar size. The candles are built from the app's own one-minute samples, so
the **wicks understate the true intra-minute range** — do not read them as exact highs
and lows.

**Explain / Analyze / Briefings (top right).**

- **Explain** opens a plain-language infographic for the one symbol you are viewing.
- **Analyze** asks Claude to read the live `$SPX` / `SPY` / `QQQ` positioning and opens
  a report — regime and bias gauge, a price-level ladder per index, a what-if for
  rally / sell-off / chop, and a "why is this happening" section at the bottom.
- **Briefings** opens the four automatic runs — premarket, ~18 minutes after the open,
  midday, and the close — plus a **History** picker for previous days.

Everything above describes the four **exposure** views — Gamma, Charm, Delta and
Vanna — which share the same bars-plus-heat-map layout. The remaining three subtabs
are different screens with their own controls, and are covered next.

### The Flow subtab — premium divergence

Where the exposure views ask *where are dealers positioned*, Flow asks *where is
today's money actually going*, for the one symbol you are viewing.

**What it draws.** Call premium and put premium through the session as a two-tone
ribbon, with the spot price as a white line on its own scale. **The crossover is the
read** — the moment call dollars overtake put dollars, or the reverse. That is the
same event the [Flow Alerts](#flow-alerts) page logs as a *Crossover*, shown here in
context so you can see whether it was a decisive break or a wobble around the line.

**The other elements:** status chips summarising the current state, a **strike
ladder** showing where in the chain the premium is concentrated, and a readout rail
with the numbers.

> **Premium here is mid-based, unsigned and forward-only.** *Unsigned* is the
> important word: Schwab publishes no time-and-sales tape for options, so this is
> **traded dollars through calls versus puts** — a money-weighted put/call read, and
> **not** net buying. A large call figure is equally consistent with someone buying
> calls and someone selling covered calls. *Forward-only* means the series begins
> when collection starts each morning; there is no overnight carry.

### The Net Prem subtab — many symbols at once

The only view on this page that shows **more than one symbol**. It plots **net
premium** — call dollars minus put dollars — as one line per name, from a menu of
**28**.

**The symbol picker** is grouped into three tabs — **Indices & Broad**,
**SPDR Sectors**, and **Mega-caps**. Two behaviours are worth knowing because they
are not obvious:

- **The group tab only filters the tick-boxes, it does not filter the chart.** Your
  selection **persists across tabs**, so you can plot `$SPX` next to `XLK` by
  ticking one in each group.
- **Each symbol keeps a fixed colour** whatever else is on the chart, so a line
  means the same thing between sessions.

Instead of a legend, lines carry **terminus labels** at their right-hand end, and a
live **leaderboard** rail ranks the current selection.

**DOLLARS / SKEW % — the toggle that makes the view usable.** In the panel header.
The magnitudes span roughly four orders: measured live, SPY at −$375M sat beside DIA
at +$0.1M on the same axis, which renders DIA as a flat line on zero.

| Setting | Shows | Use it to |
|---|---|---|
| **Dollars ($M)** | The real money | Judge whether a flow is big enough to matter |
| **Skew %** | Net as a share of **that symbol's own** premium | Compare a large name and a small one side by side — the same two symbols read −46.6% and +2.5% |

Group, ticks and scale are all remembered. **Explain** works per selected symbol.

> Sector history begins the day the feature shipped, so those lines fill in going
> forward rather than showing a long back-history.

> The status line distinguishes **"not collected yet"** from **"the publisher is
> failing"** — an empty chart otherwise looks identical in both cases.

### The Term subtab — exposure across expirations

The same exposure measure, but spread across the **next five expirations** rather
than across strikes within one. It answers *which expiry carries the positioning* —
which is how you choose a DTE rather than a strike.

It is drawn as a heat map with a **1-pixel hairline between each expiry column**, so
you can see where one expiration ends and the next begins.

> **Its vertical axis is categorical, not proportional.** Rows are addressed by
> index (expiry 1, 2, 3…), not by time, so the spacing between columns tells you
> nothing about how far apart the expirations actually are — a weekly and a monthly
> sit one column apart either way.

> **Read the blending with care.** The heat map interpolates between samples exactly
> as the intraday view does, but the intraday view's x-axis is *time*, where
> interpolating between two adjacent minutes is reasonable. Here the axis is
> *expirations*, and nothing varies continuously between one expiry and the next —
> so the smooth gradient across a column boundary is a drawing artifact, not a
> measurement. The hairlines exist to keep that visible. Read the columns, not the
> gradient between them.

**A side effect worth knowing:** because Term's rows are indexed rather than priced,
it is immune to the uneven-strike-spacing problem that affects the intraday heat map
on `$NDX` (see *Caveats* below).

### Why it matters

This is the app's edge, such as it is. Support and resistance drawn from chart patterns
are subjective; gamma walls are computed from open interest that anyone can verify, and
they exert real mechanical force because dealers are contractually obliged to hedge.

Four concrete uses, one per family of view:

1. **Strike selection** (exposure views). Selling a put credit spread below the put
   wall is materially safer than selling one above it, because dealer buying supports
   that level.
2. **Expectation setting** (exposure views). Above the flip, an intraday breakout is
   more likely to fail than follow through. Below it, the opposite. This alone should
   change position size.
3. **Confirming a bias** (**Flow**). Positioning tells you where dealers *are*; the
   premium ribbon tells you which way today's money is leaning, and its crossover
   dates that change to a minute.
4. **Choosing an expiry, and choosing a name** (**Term**, **Net Prem**). Term shows
   which expiration carries the positioning, so a DTE choice stops being arbitrary.
   Net Prem is the only view that compares symbols, which is how you notice that the
   flow you are looking at is ordinary for that name — or is not.

**Where it is weak.** Open interest is a snapshot, not a live tape — it updates once a
day. The dealer-is-short-gamma assumption is a convention, not a fact; it is usually
right for index options and less reliable for single stocks. And the exposure numbers
scale arbitrarily between providers, so only the *shape and levels* are meaningful.

### When to use it

Premarket, to set the day's levels. Again in the last hour, for the 0-DTE hedge
pressure panel. And any time you are choosing strikes.

### Caveats and gotchas

- **Index open interest reads zero after hours.** `$SPX` and `$NDX` report zero open
  interest overnight, which produces all-zero grids and meaningless wall levels. Read
  index gamma during the session only.
- **`$NDX` quotes mixed strike spacing** (5-wide near the money among 10-wide). The app
  resamples onto an even ladder to stop the heat map from banding; real strikes pass
  through untouched.
- Off-hours the page shows the **last session**, not live data. The status line says so.
- **Flow, Net Prem and Term each carry their own caveats** — see their subsections
  above. The two that catch people out: Net Prem's group tabs filter the *tick-boxes*
  and not the chart, and Term's smooth gradient across a column boundary is a drawing
  artifact rather than a measurement.

### Related pages

[Flow Alerts](#flow-alerts) (row click jumps here for that symbol) ·
[Opportunity Board](#opportunity-board) (the GEX regime column is this page's flip,
per symbol) · [Expected Move](#expected-move) (whether your strike is reachable at all).

---

## Opportunity Board

*Menu: MARKETS → Opportunity Board · Route `/options/matrix`*

### What it is

Every symbol the app tracks — around 45 to 90 names — as one sortable row each. It is
the triage screen: instead of opening Dealer Positioning for twenty tickers, you sort
this board and open the two that stand out.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), aggregator over `cache:options:matrix` |
| Built from | The 1-minute gamma collection, plus today's scanner signals and flow alerts |
| Refresh | The board republishes on the 1-minute collection; the page polls every ~2 s and repaints only on change |
| Spot / Day % | Overlaid live on the ~30-second header tick |

### Reading the screen

| Column | Meaning | How to read it |
|---|---|---|
| **Ticker** | The symbol. | |
| **Spot** | Last price. | |
| **Day %** | Change on the session. | |
| **Trend** | Intraday direction arrow. | ▲ up, ▬ flat, ▼ down. |
| **Call** / **Put** | Whether call and put premium are *accelerating*. | Both arrows the same way is a stronger signal than either alone. |
| **P/C** | Put/call ratio. | Below ~0.7 is call-heavy (bullish tilt); above ~1.0 is put-heavy. Extremes are often contrarian. |
| **Net $M** | Net premium in millions — call dollars minus put dollars. | Large positive = money through calls. Size matters more than sign on small names. |
| **GEX** | Whether spot sits **above** or **below** the dealer gamma flip. | *above* = expect pinning; *below* = expect momentum; *na* = not computable. |
| **Sig** | How many live scanner signals this symbol has. | |
| **Flow** | How many flow alerts fired today. | |
| **Signal** | An overall Buy / Neutral / Sell from the flow composite. | |
| **Hot** | **Hotness**, the default sort. A blend of the above. | Highest first. This is a measure of *activity*, not of *quality*. |

The three tiles at the top count how many symbols are currently Buy, Neutral and Sell.

### Why it matters

Options activity clusters. On most days, three or four names carry the unusual flow and
the rest are noise. This board finds them in one glance, and the **Hot** sort is
specifically tuned to surface where something is happening rather than where a signal
scored well.

**Where it is weak.** Hotness rewards *activity*, and activity is not edge. A name can
be hot because of a single large hedge that means nothing directional. Use this board
to decide **where to look**, never as a trade signal on its own.

### When to use it

Mid-morning, once the open has settled, as your first pass. And any time the Market
Scanner returns nothing interesting — the board often shows activity the scanner's
credit-spread filters exclude.

### Caveats and gotchas

- Counts are gated on the session date, so on a non-trading day you are looking at the
  last session's numbers. The header states the session it is showing.
- **P/C here is volume-based**, while the Market Dashboard's Put/Call tile is
  cap-weighted across sectors. They are different measures and will disagree.
- `na` in the GEX column is normal for names with thin option chains.

### Related pages

[Dealer Positioning](#dealer-positioning) · [Flow Alerts](#flow-alerts) ·
[Strategy Finder](#strategy-finder) (the natural next step once a symbol stands out).

---

## Flow Alerts

*Menu: MARKETS → Flow Alerts · Route `/options/flow`*

### What it is

A readable log of every unusual options event the app detected today. These are the
same alerts that chime, toast, and push to your phone — kept somewhere you can actually
review them, because a toast you miss is gone.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `cache:options:flow_alerts` |
| Detection | On the 1-minute gamma collection |
| Thresholds | `config/flow_alerts.toml` — edit and restart `options_svc` to tune |
| Retention | **Today only.** The list resets overnight. There is no history. |
| Capacity | Up to 300 alerts per day |

### Reading the screen

**The four detector types.**

| Type | What triggered it | What it may mean |
|---|---|---|
| **Crossover** | Call premium overtook put premium for a symbol, or the reverse. | A shift in where the day's option money is going. Bullish flip on calls-over, bearish on puts-over. |
| **Unusual activity** | One contract traded far more than its open interest (e.g. 21×). | Someone opened a large new position — the volume cannot be existing holders closing, because there were not that many to close. |
| **Gamma flip** | Spot crossed the dealer gamma flip level. | The market just switched between move-damping and move-amplifying behaviour. See [Dealer Positioning](#dealer-positioning). |
| **Big delta** | A single contract carries an outsized share of the symbol's total directional exposure. | A concentrated bet or hedge large enough to matter to that symbol's hedging flow. |

**Columns.** Time (CT) · **Age** · Symbol · Type · **Side** (call or put) · **Detail** ·
**Share** (for big-delta, the percentage of the symbol's gross exposure) · **Alert**
(the full sentence, as it was pushed).

The **Age** column recomputes live against the rows already on screen, so it stays
current without the table churning.

**Filters.** Type and symbol filters run instantly, on rows already loaded.

**Click any row** to open [Dealer Positioning](#dealer-positioning) for that symbol.

### Why it matters

Unusual-activity detection is the closest thing retail has to seeing institutional
positioning. A contract trading at 20× its open interest is, arithmetically, mostly new
risk being put on. Combined with the **Side** column and the symbol's gamma regime, it
gives you a directional hypothesis you can test on other pages.

**Where it is weak, and this matters.** The app cannot tell a **buy** from a **sell**.
Schwab publishes no time-and-sales tape for options, so a large call print may be
someone opening a bullish bet — or an institution selling covered calls, which is
mildly bearish. Treat every alert as *"something large happened here"*, then use price
action and gamma to decide direction. Do not read the Side column as a direction.

### When to use it

Scan it after the open and again mid-afternoon. Use it reactively: an alert on a symbol
you already follow is worth more than a strong alert on a name you have never traded.

### Caveats and gotchas

- **Unusual-activity rows may show a blank time** if they were published before an
  `options_svc` restart added timestamps. Crossover and gamma-flip rows always have one.
- `$VIX` is deliberately excluded from crossover detection — its premium crossovers are
  noise.
- Alerts fire on 0-DTE contracts heavily. A 0-DTE alert decays in relevance within
  minutes; check the Age column before acting.

### Related pages

[Dealer Positioning](#dealer-positioning) · [Opportunity Board](#opportunity-board)
(its Flow column counts these) · [Settings](#settings) (chime and push toggles).

---

## Market Dashboard

*Menu: MARKETS → Trend & Sentiment → Market Dashboard · Route `/market` — and the
app's **landing page**: opening `http://127.0.0.1:8500` redirects here*

### What it is

A live wall of roughly 48 macro instruments, grouped into framed panels. It is the
app's widest lens — the tape everything else happens inside.

### Where the data comes from

| | |
|---|---|
| Service | `market_svc` (:8215), `cache:market:dashboard` |
| Refresh | **~3 seconds** during regular hours, **15 seconds** off-hours, **60 seconds** at weekends (futures are closed) |
| Source | The proxy's raw quotes endpoint, normalized across indices, equities and futures |

### Reading the screen

**Tile colour is semantic, not directional.** Green means **risk-on**, red means
**risk-off**, grey means no data. For most instruments that matches up/down — but fear
gauges are **polarity-flipped**: VIX, SKEW, put/call, TLT and UUP shade **red when they
rise**, because a rising VIX is risk-off even though the number went up. Colour
intensity scales with the size of the move.

**The frames, in reading order:**

| Frame | Contents | What it tells you |
|---|---|---|
| **Volatility** | VIX, VIX1D, VIX3M, SKEW | The market's expected volatility. VIX1D above VIX means near-term stress. |
| **Options Sentiment** | Put/Call, Net Prem, MGTN | The app's own cap-weighted put/call and dollar-weighted premium skew. |
| **Market Internals / Breadth** | $ADVN, $DECN, the net spread, $TICK | How *broad* a move is. A rally on negative breadth is narrow and fragile. |
| **Currency** | $DXY (via UUP) | Dollar strength — generally a headwind for equities and commodities. |
| **Cash Index** | SPX, NDX | The indices themselves, each with a call/put premium subline. |
| **Equity Index Futures** | /ES, /NQ | The overnight tape, when cash is closed. |
| **Broad-Market ETF** | SPY, DIA, QQQ, IWM, RSP, QQEW | RSP and QQEW are *equal-weighted* — compare them to SPY and QQQ to see if the move is broad or driven by a few giants. |
| **Top 10** | A **BIG10** composite plus its ten mega-cap members | BIG10 shows the equal-weighted average move and a breadth subline ("5/10 up"). |
| **Sector SPDR** | The eleven S&P sectors | Ranked by the day's move. |
| **Thematic / Industry ETF** | Semis, biotech, software, retail, oil… | Ranked. |
| **Factor / Momentum** | MTUM, SPMO | Whether momentum as a style is working. |
| **Fixed Income / Credit** | TLT, HYG, LQD | HYG weak while equities rally is a classic warning. |
| **Crypto / Alternatives**, **Countries** | | Countries is ranked. |

**Four frames re-rank themselves** by the day's move — Top 10, Sector SPDR, Thematic,
and Countries — so leaders and laggards are always at the ends. **Every other frame
keeps its curated order on purpose**: SPY/DIA/QQQ/IWM in that sequence, VIX before its
tenors. That layout is itself information.

**The top rail** carries a live clock, a session indicator, an advancing/declining
**breadth meter**, and an **A/B skin toggle** (Instrument or Heat Lattice). Tiles
**flash** when their value changes.

**Premium sublines.** Index, broad-ETF and mega-cap tiles carry a small "Call 37%" /
"Put 11%" line — that name's dollar-weighted call-versus-put premium. A dash means the
symbol is not in the collected universe.

### Why it matters

Context changes the correct trade. Selling premium into a VIX of 14 with positive
breadth is a different proposition from selling into a VIX of 30 with the term structure
inverted, even if the scanner scores both candidates identically. This board is how you
notice the difference in five seconds.

The equal-weight comparisons (RSP vs SPY, QQEW vs QQQ) and the breadth internals are
the two most under-used things here. Both answer "is this move real or is it five
stocks?" — which is the question that most often explains why a sensible-looking trade
went wrong.

> **On VIX.** It is not "the fear index" in any literal sense — it is the market's
> expected 30-day volatility of the S&P 500, derived from a strip of SPX option prices.
> Cboe publish the full method:
> [VIX methodology](https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf)
> and the [VIX product page](https://www.cboe.com/tradable-products/vix/).

### When to use it

First thing in the morning and any time you feel the market "changed" but cannot say
how. It is also the fastest way to confirm the data pipeline is alive — if these tiles
are ticking, the gateway and `market_svc` are both healthy.

### Caveats and gotchas

- **Breadth internals read 0 outside regular hours.** `$ADVN`/`$DECN`/`$TICK` are
  exchange-computed and only publish during the cash session.
- `$DXY` is quoted **via UUP** because Schwab cannot quote the index directly, so its
  absolute level is not the dollar index value — only its direction is meaningful.
- Futures tiles keep moving when cash is closed. That is correct, not a bug.

### Related pages

[Sentiment](#sentiment) (turns much of this into one score) ·
[Sector & Industry](#sector-industry) · [Settings](#settings) (the bottom ticker).

---

## Sentiment

*Menu: MARKETS → Trend & Sentiment → Sentiment · Route `/sentiment`*

### What it is

The app's mood reading, presented as the **Market Regime Console** — a single screen
that answers two separate questions: *how does the market feel* (sentiment) and *how is
it behaving* (regime).

**The most important thing to understand: the sentiment score is contrarian.** It runs
0–10, and **high means fearful**. A reading of 8 does not mean the market is happy; it
means participants are defensive — buying puts, bidding volatility, rotating to
defensives. Historically that has been the better environment to *sell* premium, which
is what this app is mostly built to do. A low reading means complacency, which is when
selling premium pays least and hurts most.

Alongside it sits **Market Trend**, which is directional and runs 0–100 on the ordinary
convention: 50 is neutral, 100 is strongly bullish.

### Where the data comes from

| | |
|---|---|
| Service | `sentiment_svc` (:8210) |
| Cache keys | `cache:sentiment:composite`, `:regime`, `:regime_history`, `:intraday_history`, `:sectors` |
| Composite refresh | **Every 2 minutes** during market hours (every 15 minutes off-hours) |
| Trend refresh | **Every 15 minutes** |
| Regime refresh | **Every 5 minutes** during market hours; holds the last read outside them |
| Intraday graphs | Recorded every 2 minutes, rolling last 5 trading days |

### Reading the screen

**The two rings.** Each ring carries three arcs on one dial:

| | Sentiment ring | Trend ring (0–100) |
|---|---|---|
| **Day** | The live composite right now | Today's directional read |
| **Week** | The last **5 sessions'** average | The 7-day trend |
| **Month** | The **full history's** average | The 30-day-ago trend |

Week and Month exist so you can see today against its own recent normal. A reading of
75 means something different when the month sits at 53 than when it sits at 72.

> **An arc drawn as a track with an em-dash means "no usable reading"** — not zero.
> This is deliberate, and it is one of the app's better design decisions. A missing
> input used to render as a confident number, which is the single most dangerous thing
> a dashboard can do.

**Model confidence** (under the sentiment ring) tells you how much of the model's input
set actually reported. Low confidence with a strong-looking score is a reason to
discount it.

**Day read labels.** The sentiment ring shows a word (e.g. *LONG*) and the trend ring
shows a five-state label from the direction-and-aggression vocabulary:

| Label | Meaning | Suggested tilt |
|---|---|---|
| **Bull** | Direction up, buyers aggressive | Long / put credit spreads |
| **Weak Bull** | Up, but buyers are not pressing | Trim longs, favour call credit spreads |
| **Neutral** | No directional edge | Neutral structures, or stand aside |
| **Resilient** | Refuses to fall despite pressure; puts cheap and undefended | Put credit spreads |
| **Bear** | Direction down, sellers aggressive | Call credit spreads / defensive |

The panel also prints a plain-English gloss — for example *"Lack of Bullishness —
Buyers exhausted at highs — favor CCS, trim longs."*

**The Signals column** is four tiles: **BIAS** (market direction), **SIGNAL** (strength
and momentum), **YESTERDAY** (previous close) and **CHANGE**. Below them sit
rate-of-change readings (3-day, 5-day, 20-day z-score) and a **divergence** line that
names which two components disagree — a divergence is a low-conviction warning, not a
signal.

**The regime block.** This is the second half of the screen and it answers a different
question: not *which way*, but *what kind of market is this*.

| Regime | What it looks like | How to trade it |
|---|---|---|
| **Balanced** | Quiet, price pinned near its own mean. Low ADX, flat moving averages. | Neutral premium selling. Iron condors. |
| **Trending** | Directional persistence. | Follow the direction; avoid fading. |
| **Breakout** | Range expansion, new ground. | Momentum; credit spreads against the move are dangerous. |
| **Whipsaw** | Plenty of movement, no progress. Failed breaks, two-sided wicks. | The hardest regime. Reduce size or stand aside. |
| **Stressed** | Fear — elevated VIX, inverted term structure, gaps that do not fill. | Premium is rich but risk is real. Defined risk only. |

**Trending and Breakout also carry a direction word** — *Rallying* or *Firming* up,
*Retreating* or *Softening* down, and *Breakdown* for a downside break. That word
appears **only when the tape's own slope and the Market Trend gauge agree**. When they
disagree, the plain regime name shows instead, so this panel can never contradict the
gauge above it. Balanced, Whipsaw and Stressed have no direction by nature.

**Confidence and the share table.** The console shows the leading regime with a
confidence percentage, then ranks all five by **share** — how much of today's tape each
one holds — with a bar against the leader, a change-since-open column, and a footer
naming:

- **LEAD** — the leader's margin over the runner-up. **This is the number that tells
  you whether the headline was nearly a coin toss.** A 10-point lead is a real read; a
  0.2-point lead is a tie dressed up as a conclusion.
- **TIGHTEST TODAY** — the smallest that margin got during the session.
- **BIGGEST MOVE** and **EMERGING** — which regime is rotating out, and which is rising
  from zero.

**Diagnostic tags** list the specific evidence — *EMA FLAT*, *BALANCED PROFILE*,
*ADX 36 RISING*, *BAND-HUG*, *FAILED OR BREAKS*, *EMA WHIPSAWS* — so you can see what
the classification is actually resting on.

**Components** and **Trend Detail** (press and hold) open popups showing every input
and, for Trend, a *Why* section listing direction, effort, skew, flow, session,
rejection, profile, order-flow and aggression evidence.

**Daily Sentiment & Trend** (expanded by default) shows two colour-coded intraday
graphs over the last five trading days, with overnight gaps collapsed.

### Why it matters

This page decides *what kind of trade is appropriate today*, which is upstream of every
candidate the scanner produces. An iron condor is a good trade in Balanced and a bad one
in Breakout, and the scanner's 0–100 score does not know the difference.

The **LEAD** number deserves particular attention. Regime classifiers of this kind
produce a confident-sounding label every time they run, whether or not the underlying
evidence separates the candidates. Publishing the margin is the app admitting when it
does not really know — and a 0.2-point lead should change how much you weight
everything else on the page.

**Where it is weak.** The composite blends inputs that can each be individually stale,
and its confidence figure is the honest guard against that. There is also a documented
open issue: an all-missing read of the structural price inputs scores near-maximum
bullish rather than neutral. Watch the confidence number, and cross-check against
[Market Dashboard](#market-dashboard) when a reading looks surprising.

### When to use it

Premarket, to set expectations. Again after any large intraday move, because the regime
can genuinely change within a session — the share table shows that happening.

### Caveats and gotchas

- The regime **holds its last reading outside market hours**. An evening visit shows
  the close, not a live read.
- The regime's internal names differ from the displayed ones (the code still calls
  Balanced *mean_reversion* and Stressed *crisis*). You will see the old names only in
  logs or exported data.
- Sentiment and Trend can point opposite ways. That is informative, not a fault — high
  fear during a rally is exactly the divergence the panel is designed to surface.

### Related pages

[Market Dashboard](#market-dashboard) · [Sector Rotation](#sector-rotation) ·
[Claude Trades](#claude-trades) (the autonomous trader reads this page's output).

---

## Sector & Industry

*Menu: MARKETS → Trend & Sentiment → Sector & Industry · Route `/sentiment/sectors`*

### What it is

A performance **heat grid** for the eleven S&P 500 sectors, each expandable into the
industries inside it. It is the "where is it working" screen, and since the 2026-08-17
rebuild it answers that by colour before you read any number.

### Where the data comes from

| | |
|---|---|
| Service | `sentiment_svc` (:8210), `cache:sentiment:sectors` |
| Refresh | On the sentiment service's sector cadence; **Refresh** forces it |
| Universe | The eleven Select Sector SPDR ETFs, plus ~70 industry ETFs |

### Reading the screen

**The header line** names the regime and shows the **cyclical versus defensive**
spread behind it, then — set apart on the right — the percentage of sectors green, the
cap-weighted move (what the index actually did), and a 0–10 score.

That cyclical/defensive spread is the useful part. Cyclical sectors — Technology,
Discretionary, Financials, Industrials, Energy, Materials — do well when growth is
expected. Defensive sectors — Staples, Utilities, Health Care, Real Estate — do well
when it is not. Which group leads tells you what the market believes about growth,
independently of whether the index went up.

The three stats to its right are there **because the grid is unweighted**. Eight green
micro-sectors against three red mega-caps paints an overwhelmingly green grid on a day
the index fell, and the cap-weighted number is the only thing on the page that says so.

| Column | Meaning |
|---|---|
| **Sector** / **ETF** | Name and its tradeable proxy, with a **rank line** beneath giving its position in the pack on whichever column you are sorted by. |
| **Composition** | What is actually in it. |
| **P/C** | Put/call volume for that sector's options. Amber above 1.5 means put-heavy. It is a ratio, not a return, so it deliberately gets no colour tile. |
| **Day / Week / Month** | Return over each window, as a filled tile. |

**Reading the tiles.** This is the part that changed, and it is the point of the screen.
Each figure sits inside a filled block, and the three blocks are flush against each
other, so the eleven rows form one continuous colour band you can read top to bottom
without reading a number.

- **Colour carries magnitude, not just direction.** A deep green is a big move; a faint
  one is a small move in the same direction.
- **Each column is normalised against itself** — Day against the day's own spread, Week
  against the week's, Month against the month's — because the three live on different
  natural scales. Sharing one scale would leave Day permanently pale or Month
  permanently saturated.
- **The scale spans sectors and industries together**, and does so whether or not the
  industries are expanded. Opening a sector therefore never repaints the rows above it.
- **A flat band keeps small moves dark**: under ±0.50% (Day), ±1.00% (Week) or ±1.50%
  (Month) a tile drops to neutral. The bands widen with the horizon because a month is
  *expected* to have travelled further than a day — a single band would paint an
  unremarkable month as a move.
- **Outliers saturate rather than setting the scale.** Industry ETFs occasionally print
  something like a +27% month; normalising on that would flatten every sector to the same
  near-black green. The scale sits at the column's 90th percentile instead, and the
  handful above it simply max out.

**Click Day, Week or Month** to sort by it; click again to reverse. Default is Day
descending. **Click any row** (or **Expand all**) to see the industries within that
sector, which render as the same tiles on a shorter row.

**Rotation quadrants are not on this screen.** They were, as an "RRG" column, but a
one-word quadrant sitting beside a colour band invited reading it as a fourth timeframe.
[RRG](#rrg) and [Sector Rotation](#sector-rotation) show that read properly.

### Why it matters

Index-level readings hide dispersion. A flat S&P can be a violent rotation from
Technology into Staples, and that rotation is often more tradeable than the index move.
The **Month %** column is where you see structural shifts that the day's tape hides.

Practically: if you are selling put spreads, doing it in a sector that is leading on all
three windows is a materially better bet than doing it in the index.

**Where it is weak.** Sector ETF returns are cap-weighted, so a single mega-cap can
carry a sector's number. Cross-check with the equal-weight comparisons on
[Market Dashboard](#market-dashboard).

### When to use it

Once a day, and whenever [Sector Rotation](#sector-rotation) flags a change — this page
is where you find the specific names behind it.

### Related pages

[Sector Rotation](#sector-rotation) · [RRG](#rrg) · [Momentum](#momentum).

---

## Sector Rotation

*Menu: MARKETS → Trend & Sentiment → Sector Rotation · Route `/sentiment/rotation`*

### What it is

A single verdict — **Risk-ON** or **Risk-OFF** — plus the evidence for it, based on
where money is rotating between sectors.

### Where the data comes from

| | |
|---|---|
| Service | `sentiment_svc` (:8210), `cache:sentiment:rotation` |
| Refresh | **Manual only.** Click Refresh. It is cached otherwise. |
| Benchmark | Everything is measured *relative to SPY*. |

### Reading the screen

**The verdict strip** states the regime, the arithmetic behind it, and how strong the
signal is — three panels, left to right.

The middle panel is the argument in one picture. The average rotation momentum of
cyclical sectors sits on the left, defensives on the right, and the **diverging gauge**
below plots the spread between them on a −3 to +3 scale. The bar runs from the centre
out to the reading, so **left of centre is risk-off and right is risk-on**, and distance
from centre is conviction. Both **±1.50 triggers** are ticked on the track.

Showing the triggers is what makes this honest — a spread of −1.51 against a ±1.50
threshold is a verdict that only just qualified — and the right-hand panel says so in
words: *just past the trigger* means a fresh signal that could reverse next session,
*well past* means an entrenched rotation.

**The flow band** answers the question the old table could not: *how much of the index
is actually moving?* Every rotating sector is a block whose **width is its S&P 500
weight**, split into the side rotating out (red) and the side rotating in (green), with
each side's total and sector count beneath. "Money rotating out of Technology" means
much more when Technology is 32.5% of the index than when a 2% sector moves — and here
that is the size of the block rather than a number you have to look up. Very thin
slices drop their labels; they are all named in the panels below.

**The four quadrant panels** carry every sector, with the share of the index sitting in
each quadrant:

- **Leading** — strong and still strengthening. Where the rotation is going.
- **Improving** — still weak against SPY, but momentum has turned up. Early.
- **Weakening** — still strong, but momentum has rolled over. Money is leaving.
- **Lagging** — weak and getting weaker. Where the rotation is coming from.

Each sector card shows **RS-Mom** — relative *momentum*, the rate of change of its
strength against SPY, where above 100 means its outperformance is still improving — and
a bar for its index weight. **All bars share one scale** (the heaviest sector on the
page), so a long bar always means a heavy sector, whichever panel it is in.

The other axis, **RS-Ratio** — relative *strength* against SPY, where above 100 means
outperforming — is named on the rails around the panels and plotted properly on the
[RRG](#rrg) tab.

> The footnote is important: *pairing is ordinal*. The app is ranking relative buying
> and selling pressure. It is **not** measuring literal cash flow between sectors — no
> retail data source can.

### Why it matters

Rotation usually leads price. Defensives taking leadership while the index still makes
highs is one of the more reliable warnings available to a retail trader, and it shows up
here before it shows up in the index.

For an options seller specifically: Risk-OFF with the index flat is the setup where call
credit spreads work and put credit spreads quietly bleed.

**Where it is weak.** The verdict is a two-group average, so a single extreme sector can
tip it. Always read the spread against the triggers on the gauge, and look at the
quadrant panels rather than trusting the headline word alone.

### When to use it

Once a day. It changes slowly by design — refreshing it every ten minutes tells you
nothing new, which is why it is manual-refresh only.

### Related pages

[RRG](#rrg) (the same data as a map with trails) ·
[Sector & Industry](#sector-industry) · [Momentum](#momentum).

---

## RRG

*Menu: MARKETS → Trend & Sentiment → RRG · Route `/sentiment/rrg`*

### What it is

A **Relative Rotation Graph** — the same rotation data as the previous page, drawn as a
map where each sector leaves a trail showing where it has come from.

The technique was created by Julius de Kempenaer and is standard on institutional
terminals. Background:
[relativerotationgraphs.com](https://relativerotationgraphs.com/) and
[StockCharts' RRG documentation](https://help.stockcharts.com/charts-and-tools/other-charting-tools/rrg-charts).

### Where the data comes from

Same service and cache as [Sector Rotation](#sector-rotation). **Manual refresh only.**

### Reading the screen

Two axes, both centred on 100 (which means "exactly matching SPY"):

- **Horizontal: RS-Ratio** — relative strength. Right of centre = outperforming.
- **Vertical: RS-Momentum** — the rate of change of that strength. Above centre = the
  outperformance is accelerating.

That gives four quadrants, and sectors tend to rotate **clockwise** through them:

| Quadrant | Position | Meaning | Typical next move |
|---|---|---|---|
| **Improving** | top-left | Weak, but turning up | → Leading |
| **Leading** | top-right | Strong and accelerating | → Weakening |
| **Weakening** | bottom-right | Still strong, losing steam | → Lagging |
| **Lagging** | bottom-left | Weak and still falling | → Improving |

The four quadrants are **tinted**, so a sector's state is readable from where it sits
rather than from a colour key.

**Each sector draws a trail** of its **last five readings**, drawn as a smooth curve that
thins and fades toward the oldest — so the trail points the direction of travel, and the
bright dot at its end is now. The curve passes through every actual reading; the
smoothing only decides the path between them.

Markers are labelled with the **sector name**, not the ETF ticker.

**Dot size is the sector's weight in the S&P 500, encoded by area.** This is the piece
that changes how the chart is used. A heavyweight sliding out of Leading is a market
event; a 2% sector doing the same is a curiosity. The old version drew both the same
size, so the chart could not distinguish them and you had to carry the weights in your
head.

The clockwise rotation is the whole point of the chart. A dot in Leading that is curving
toward the right-hand edge and starting to drop is an early warning that leadership is
about to change — visible here well before it shows in a returns table.

### Why it matters

Trail *shape* carries information a table cannot. A long straight trail into Leading is
a durable trend; a short jittery cluster around the centre is noise regardless of which
quadrant it currently sits in. Distance from the centre is conviction — a sector at 104
is meaningfully strong; one at 100.2 is essentially flat against SPY. And with dot size
carrying index weight, the single most useful read on the page is a **large** dot a long
way from the centre.

Note that the axes **rescale to fit the data**, so the plot always fills its space. Read
the tick numbers, not the distance in pixels, when comparing one session to another.

**Where it is weak.** RRG is a *relative* measure. In a market where everything falls,
some sector still plots in Leading. It tells you what to prefer, never whether to be in
the market at all.

### When to use it

Weekly, more than daily. The trails are built to show multi-week rotation.

### Related pages

[Sector Rotation](#sector-rotation) · [Momentum](#momentum) (a similar four-quadrant
picture, but different axes — see that page for the distinction).

---

## Momentum

*Menu: MARKETS → Trend & Sentiment → Momentum · Route `/sentiment/momentum`*

### What it is

A three-level momentum screen — sectors, ~70 industry ETFs, and 311 stocks — that also
tells you whether momentum is currently *worth trading at all*.

### Where the data comes from

| | |
|---|---|
| Service | `sentiment_svc` (:8210), `cache:sentiment:momentum` |
| Refresh | **Once nightly at 16:20 CT.** Not live, and deliberately so. |
| Why | It is built on daily bars, which change once a day. Recomputing ~390 regressions every two minutes would be pure waste. |

### Reading the screen

The page is laid out as **five numbered steps**. Work down them in order — each one
qualifies the next.

**1 · Is momentum worth trading today?** All three states render side by side with the
live one enlarged. Showing the other two is the point: the premise of this page is that
momentum only pays in some conditions, and that is a comparison rather than a label.

| Banner | Meaning | What to do |
|---|---|---|
| **Favorable** | Trending conditions — momentum's home turf. | Momentum names are worth pursuing. |
| **Neutral** | Chop. The score leans on a shorter lookback. | Take the leaderboard with a pinch of salt. |
| **Suppressed** | **Momentum-crash risk** — a volatile rebound off a low, the condition where yesterday's biggest losers rip hardest and momentum strategies suffer their worst drawdowns. | The leaderboard dims. That is the app telling you not to chase. |

> The *suppressed* state is not an invention of this app. Momentum's tendency to crash
> during volatile rebounds is a well-documented effect — see Daniel & Moskowitz,
> *Momentum Crashes*, Journal of Financial Economics (2016).

Beneath the cards, the **dispersion percentile** on a 0–100 bar. Low dispersion means
everything is moving together, so a relative-strength screen has little to separate — the
score still computes, it just matters less.

**2 · Three levels, and where they agree.** One track per level showing how many of its
names are in their own top quartile. Track width scales with the *square root* of
universe size, so all three stay legible instead of the 296-stock bar dwarfing the
11-sector one. Beside it, the count of **stocks whose industry and sector both confirm** —
the highest-conviction rows on the page.

**3 · Where the names sit.** The four quadrants as counts and shares, with the strongest
few named in each. Same four quadrant names as [RRG](#rrg) — Leading, Improving,
Weakening, Lagging — but **the axes are different**: RRG measures strength purely against
the S&P, whereas this score blends five components of which relative strength is only
one. The two screens can legitimately disagree about the same sector.

**4 · What a score is made of.** The current top-ranked name, decomposed. Bars run either
side of a centre line which is the universe average, so the *sign* is the reading. They
clamp at ±3, which is where the service caps the z-scores.

**5 · Rank over recent sessions.** A name that has climbed steadily for two weeks is a
better candidate than one that jumped yesterday. Note that **not every symbol has the
same history** — a line that starts partway across has fewer stored sessions, not a
shorter trend — and the rank axis **scales to the data**, so it routinely runs far deeper
than the top twenty.

**The full leaderboard** is collapsed at the foot of the page. Open it for the ranked top
15 and bottom 15, exposing every component:

| Column | What it measures |
|---|---|
| **SCORE** | The blended momentum score (a z-score, so 0 is average). |
| **PCTL** | Percentile within the universe. |
| **TREND** | Price trend strength. |
| **RS** | Relative strength versus the benchmark. |
| **ACCEL** | Whether the momentum is still building or fading. |
| **PATH** | Path quality — how smooth the advance was. A smooth climb is more durable than a spike. |
| **PARTIC.** | **Participation** — for an industry, how many of its five constituents are above their own 50-day average. Leadership its own members do not confirm is thin. |
| **QUADRANT** | Where it sits on the scatter. |
| **Δ** | Rank change since the previous session. |

**Align** shows three blocks — sector, industry, stock — filled when each is in its top
quartile. **Three filled blocks is the highest-conviction row on the page**: the stock
is strong, its industry is strong, and its sector is strong.

**The excluded count** in the footer lists names dropped for illiquidity, insufficient
history, or no quote. This exists so a renamed or delisted ticker becomes *visible*
rather than silently vanishing from the universe.

### Why it matters

Momentum is one of the most robustly documented return factors in finance, and also one
of the most dangerous — its drawdowns are sudden and severe. This page is unusual in
telling you which of those two states you are in *before* showing you the list.

Practically, the highest-value output is the **Align** column. A three-block row is a
name where the whole hierarchy agrees, and those are the candidates worth taking to
[Trade Analyzer](#trade-analyzer) or [Strategy Finder](#strategy-finder).

**Where it is weak.** It is nightly, so it cannot react to today's news. Participation is
measured on only five constituents per industry, which is a coarse sample. And a
momentum score says nothing about valuation or event risk — an earnings date overrides
everything on this page.

### When to use it

Weekly, for building a watchlist. Check the banner daily during volatile periods,
because *suppressed* is a genuine stand-aside signal.

### Caveats and gotchas

- The page shows the **last computed session**, which after a weekend means Friday.
- It is explicitly **not** a component of the sentiment composite — it feeds nothing
  else in the app.
- Switch between industries and stocks with the dropdown; the leaderboards change with
  it.

### Related pages

[RRG](#rrg) · [Sector & Industry](#sector-industry) ·
[Trade Analyzer](#trade-analyzer).

---

# STRATEGY

This section turns market conditions into specific trades. It has a deliberate shape:
**Strategy Tools** model legs *you* bring, the **Options** group works through signals
the app *finds*, **Trade Analyzer** judges a single stock, and **Claude Trades** watches
the automated trader.

## Calculator

*Menu: STRATEGY → Strategy Tools → Calculator · Route `/options/calculator`*

### What it is

A profit-and-loss calculator for any multi-leg options trade, priced against the real
option chain. This is where you find out what a trade actually risks before you take it.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211) — `calc_load`, `calc_compute`, `calc_iv` commands |
| Cache keys | `cache:options:calc_chain`, `:calc_result`, `:calc_iv` |
| Chain | Fetched on demand when you Load a symbol |
| State | The page **remembers everything** — symbol, strategy, legs, fields — across navigation |

### Reading the screen

**The workflow is: symbol → strategy → legs → Calculate.**

1. **Symbol** — type it and press Enter, or just tab out of the field. A full-screen
   wait overlay shows while the chain loads (30-second timeout).
2. **Strategy** — a cascading menu of templates: singles, verticals (credit and debit),
   iron condors, butterflies (long and iron), calendars and diagonals. Picking one fills
   the leg editor.
3. **Legs** — the editable table below. Each leg has its own **type** (put/call),
   **side** (long/short), **expiry**, **strike**, **quantity** and **premium**. Add or
   remove legs freely. Per-leg expiry is what makes **calendars price correctly** —
   each leg is valued at its own time to expiration.
4. **Fetch Premiums** pulls live marks for the legs you have built.
5. **Calculate** produces the summary tiles and the heat map.

**The other inputs:**

| Field | What it does |
|---|---|
| **Expiry** (top level) | Propagates to **all** legs and re-syncs their strike ladders. |
| **Contracts** | Position size. Everything scales by it. |
| **IV %** | The volatility assumption. Higher IV = pricier options and wider swings. |
| **IV Δ %** | A shock applied on top, for stress-testing. |
| **Price** | Override the underlying price. |
| **Rate %** | The risk-free rate. Barely matters at short expiries. |
| **Number of strikes** | How many real chain strikes the heat map spans (default 24, centred on spot). |

**IV Update** implies the volatility **from the traded contract's mark**, the way
ThinkorSwim does — it solves backwards from the actual price rather than using the
chain's published figure. Before you have picked a strike it falls back to at-the-money
chain volatility.

**The summary tiles** give max risk, max return, breakeven and probability of profit.

**The heat map** is the centrepiece: rows are underlying prices (real chain strikes
around spot), columns are dates. Green is profit, red is loss.

> **The first column is "Now" and the last is "Exp".** "Now" is the trade's current
> mark-to-market value; "Exp" is the payoff at expiration. This distinction matters
> enormously for 0-DTE, where an earlier version showed only the expiration payoff
> everywhere and so hid the entire intraday behaviour of the trade.

**Copy to Simulator** sends the exact legs across. **Expected Move** charts them.

### Why it matters

Two numbers decide whether a credit spread is worth taking: the credit you collect and
the width you risk. Everything else — probability, breakeven, Greeks — follows from
those. This page shows all of it against real strikes rather than round numbers, which
is the difference between a trade you can actually fill and one you cannot.

The heat map's second value is *time*. A credit spread that is profitable at expiration
can be deeply underwater three days in, and that intermediate red is what forces people
out of trades that would have worked. Seeing it in advance changes your stop placement.

**Where it is weak.** It is a Black-Scholes model, so it assumes constant volatility and
no early assignment. American-style options on individual stocks can be assigned early,
particularly around dividends, and this page will not warn you.

### When to use it

Every time, before committing to a structure. It is the last stop between a candidate
and a position.

### Caveats and gotchas

- **When a trade is sent here from the Scanner or Strategy Finder**, the chain loads
  first and *then* the legs apply. If you set legs before a chain exists, the strike
  ladder is empty and the strikes get wiped — the app handles this for you, but it is
  why there is a brief pause on hand-off.
- Widening the strikes raises the credit **and** the max loss. The tiles show both;
  check the ratio, not just the credit.

### Related pages

[Simulator](#simulator) (behaviour over time and volatility) ·
[Expected Move](#expected-move) (is the short strike even reachable) ·
[Market Scanner](#market-scanner) and [Strategy Finder](#strategy-finder) (sources).

---

## Simulator

*Menu: STRATEGY → Strategy Tools → Simulator · Route `/options/simulator`*

### What it is

The Calculator's companion. Where the Calculator answers *what does this trade pay*, the
Simulator answers *how does it behave* — as price moves, as days pass, and as volatility
changes.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211) — `sim_fetch`, `sim_run`, `sim_replay` |
| Cache keys | `cache:options:sim_meta`, `:sim_result`, `:sim_replay` |
| Pricing | Black-Scholes, per leg, each on its own clock |
| State | Persists across navigation, like the Calculator |

### Reading the screen

Fetch a snapshot for a symbol, pick a **Strategy**, adjust the **legs** — the controls
and the strategy sit side by side in one panel. Then use the three subtabs:

**Replay.** Re-prices the whole netted position along the underlying's recent actual
price path. Stacked panels show price plus the five Greeks, with a scrub cursor.

This is the most under-used view in the app. It answers "would I have been stopped out
of this?" using real historical movement rather than a hypothetical slider.

**What-if.** A dollar profit-and-loss payoff measured **from your entry**. Two sliders:

- **Price change** moves the underlying up or down.
- **Days passed** fast-forwards time. Each leg decays on its own clock, so calendars
  behave correctly and **theta becomes visible** as you slide.

Profit fills green above breakeven, loss fills red below. For a credit spread the
profit caps at the net credit and the loss floors at width minus credit — matching the
Calculator exactly.

**IV Shock.** Multiplies volatility to expose **vega** risk. This is the view that
explains losses people find inexplicable: a position can be correct on direction and
still lose money because implied volatility collapsed after an event, or gained value
purely because it rose. Selling premium into an IV spike and buying it back after the
crush is the whole basis of event trading.

**Copy to Calculator** sends the legs back the other way.

### Why it matters

Most losing options trades are not wrong about direction — they are wrong about *path*
or *timing*. The three views map exactly onto the three ways a trade goes wrong: the
underlying moved (What-if), time ran out (Days passed), or volatility changed (IV
Shock). Testing all three before entering is the single highest-value habit this app
supports.

**Where it is weak.** Replay uses the underlying's real path but re-prices the option
with a model, so it cannot reproduce actual bid/ask spreads or fills. Treat it as
"how the position would have valued", not "what I would have got".

### When to use it

After the Calculator says the numbers work, before you commit. And any time an existing
position is behaving in a way you do not understand.

### Related pages

[Calculator](#calculator) · [Expected Move](#expected-move) · [Rescue](#rescue).

---

## Market Scanner

*Menu: STRATEGY → Options → Market Scanner · Route `/options/scanner`*

### What it is

The app's front door. It scans the watchlist continuously and ranks option trade
candidates 0–100, split across three tabs by trade type.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211) |
| Cache key | **`cache:options:scan_day`** — the *day's union*, not just the latest scan |
| Schedule | **Auto-scans every 15 minutes**, 08:00–15:15 CT on trading days |
| Manual | **Run scan** forces a refresh |

**Why the day union matters.** The table shows every signal that qualified *at any point
today*, not only those qualifying right now. A signal that has dropped out renders
**dimmed and frozen**, with a "Dropped HH:MM" timestamp and **no Paper button**. This is
deliberate: a dropped signal's frozen price and stale credit would make any paper entry
fictional. The status bar separately reports the count of *live* signals so the two can
never be confused.

### Reading the screen

**Three subtabs:**

| Tab | Contents |
|---|---|
| **0-DTE** | Same-day expirations. Fast, high-decay, unforgiving. |
| **Swing** | Multi-day credit spreads, typically 5–15 DTE. |
| **Directional** | Single-leg long and short calls and puts. |

**The columns:**

| Column | Meaning | How to read it |
|---|---|---|
| **SYMBOL** | The underlying. | |
| **TYPE** | PCS (put credit spread), CCS (call credit spread), IC (iron condor). | PCS is mildly bullish, CCS mildly bearish, IC neutral. |
| **EXP** / **DTE** | Expiration and days to it. | |
| **STRIKES** | Short and long strike. | The gap between them is the width, and the width is your risk. |
| **CREDIT** | Premium collected per spread. | |
| **MAX LOSS** | Width minus credit. **This is what you actually risk.** | Always compare it to the credit, not to the account. |
| **R/R %** | Reward-to-risk. | Higher is better, but high R/R usually means low probability. |
| **POP %** | Probability of profit. | Model-derived. Treat it as a ranking aid, not a forecast. |
| **IV RANK** | Where current implied volatility sits in its own recent range. | High IV rank is the good environment for *selling* premium. |
| **SCORE** | The 0–100 composite. | The default ranking. |
| **GRADE** | A quality letter. | Quality-gated separately from the score. |
| **DROPPED** | When a signal stopped qualifying. | Blank means still live. |

**New signals** are badged. "New" means *unseen since you last viewed this page* — it is
acknowledged on first paint, keyed on the signal's unique id. Restarting the web app
re-marks everything as new; that is page-side state, and deliberate.

**Row actions** send a signal to the [Calculator](#calculator), open its
[Expected Move](#expected-move), or paper-trade it.

**Click a row** to open the Trade detail panel on the right, with a probability
speedometer and full contract detail.

### Why it matters

This is the app's highest-throughput screen: it evaluates thousands of possible spreads
across the watchlist every fifteen minutes and surfaces the few dozen worth a look. No
manual process competes with that.

The scoring is a *premium composite* — it rewards credit relative to risk, probability,
IV rank and trend fit. Work from the top down and stop when the numbers stop being
attractive.

**Where it is weak, and it is worth understanding.** The score ranks candidates against
each other, not against an absolute standard. In a dull market the top-scoring signal is
simply the best of a poor set. Always sanity-check the credit against the max loss
yourself — a 0.20 credit on a 5.00-wide spread is a bad trade whatever it scored.

Note also that the **Directional** tab uses a *different* score (Fit + Quality) that is
**not commensurable** with the credit-spread composite. Do not compare a 70 on
Directional with a 70 on Swing.

### When to use it

Throughout the session. It is the default landing page for a reason.

### Caveats and gotchas

- **An empty Directional tab is normal.** The engine only emits candidates scoring ≥ 50
  and excludes Weak grades, so empty means "nothing cleared the bar". Long *calls*
  largely vanish because of a documented scoring artifact around unbounded profit.
- **Index signals are rare by design.** `$SPX`, `SPY` and `QQQ` usually have implied
  volatility too low to clear the credit floor. Their absence is not a fault.
- **Naked short options** show `Max L = ∞` with an undefined-risk badge and no Paper
  button.
- Weekends and off-hours produce sparse or empty results.
- The alert chime and nav badge count **credit spreads only**.

### Related pages

[Strategy Finder](#strategy-finder) · [Captured Signals](#captured-signals) ·
[Calculator](#calculator) · [Settings](#settings) (alert thresholds).

---

## Strategy Finder

*Menu: STRATEGY → Options → Strategy Finder · Route `/options/swing`*

### What it is

The Market Scanner inverted. Instead of scanning many symbols for one kind of trade, you
give it **one symbol** and it ranks **every strategy family** for that symbol on a single
comparable score.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `swing_scan` command → `cache:options:swing` |
| Trigger | On demand — press **Scan** |

### Reading the screen

**Inputs:** symbol, **DTE min/max** (wider allows more candidates), and a **Strategies**
multiselect across three families:

- **Directional** — long and naked short calls and puts.
- **Spreads** — debit (bull call, bear put) and credit (PCS, CCS).
- **Neutral** — iron condors.

An **Advanced** panel exposes the credit-spread filters: put/call **delta** bounds (how
far out-of-the-money the strikes sit — a smaller absolute delta is safer but pays less)
and a **minimum credit** percentage.

**The view banner.** Before ranking anything, the scanner *infers a market view* for the
symbol from its technicals and implied volatility — a direction, a conviction level, and
a volatility regime. It then scores each candidate on two things:

1. **Fit** — how well the structure matches that inferred view.
2. **Structural quality** — whether the trade is well-built regardless of view.

That is what makes a long call and a put credit spread comparable on one 0–100 number.

**The columns:** Strategy · Bias · Legs · Exp · DTE · Debit/Credit · Max P · Max L ·
R:R · PoP · BE (breakeven) · IV Rank · Score · **Grade**.

**The Grade is quality-gated, not fit-gated** — it is driven by structural quality and
per-family hard gates, and carries a tooltip explaining the reason. A high score with a
poor grade means "fits your view, but badly constructed".

**The status line** reports how many candidates were **cut below the quality bar**. That
count is what distinguishes *"the scan found things and rejected them all"* from *"the
scan found nothing"* — two very different situations that would otherwise look identical.

**Row actions** send to [Calculator](#calculator) or [Expected Move](#expected-move) for
all types, and to paper trading for credit structures (PCS, CCS, IC) and defined-risk
debit structures (long call/put, bull call, bear put). Naked shorts are excluded from
paper trading because their risk is undefined.

### Why it matters

Most traders default to one structure and force every market view through it. This page
inverts that: it starts from what the symbol is actually doing and asks which structure
best expresses it. Frequently the answer is not the one you had in mind.

The Fit-plus-Quality score is the app's most genuinely useful piece of scoring, because
it makes structures comparable that normally cannot be compared at all.

**Where it is weak.** The inferred view is a model output, not a fact. If you disagree
with the banner, the ranking beneath it is ranking against the wrong hypothesis — read
the banner first and discard the scan if it is wrong.

### When to use it

After [Opportunity Board](#opportunity-board) or [Momentum](#momentum) surfaces a
symbol, and any time you have a directional opinion and want the best way to express it.

### Related pages

[Market Scanner](#market-scanner) · [Calculator](#calculator) ·
[Trade Analyzer](#trade-analyzer) (for the directional opinion itself).

---

## Expected Move

*Menu: STRATEGY → Options → Expected Move · Route `/options/expected-move`*

### What it is

A price chart with a forward cone showing how far the market **expects** the symbol to
move by a chosen expiration — with your strikes drawn on it.

**The concept.** Option prices encode a forecast. If you know the implied volatility and
the time to expiration, you can compute the one-standard-deviation range the market is
pricing: roughly `price × volatility × √(time/365)`. Statistically, about 68% of
outcomes fall inside one standard deviation.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `em_chain` command → `cache:options:em_chain` |
| History | 6 months of daily candles |
| Today's bar | Synthesized from the live quote, because Schwab's daily history ends at the previous session |

### Reading the screen

**The candles** are six months of daily price history. **The cone** fans forward from
today to expiration, dashed green above and red below, widening with the square root of
time.

**Strike lines** are overlaid — short strikes solid, long strikes dashed, coloured by
put or call.

**Expiry and strike are dropdowns** driven by the real chain. The expiry list carries a
DTE suffix (`2026-08-12 (0d)`) so weeklies stay readable. Strikes are deduplicated
across calls and puts.

**Look-back** controls how much history shows. **A wider cone means higher implied
volatility.**

### Why it matters

This is the single best sanity check for a premium seller. If your short strike sits
**outside** the cone, the market is pricing that level as unlikely to be reached — which
is exactly what you want when selling. If it sits **inside**, you are selling something
the market thinks is a coin flip, and the credit should be much larger to justify it.

It is also the fastest way to see whether a credit is fair. A wide cone with a small
credit means you are underpaid for the risk.

### Caveats and gotchas

> **This page's implied volatility and expected move deliberately do NOT match
> ThinkorSwim, and the difference has been measured rather than guessed. Do not "fix"
> either number to match ToS without deciding which definition you want.**
>
> There are **two independent differences**, and they push in opposite directions:
>
> 1. **Different IV source.** This app reads the single strike nearest spot. On an
>    equity volatility smile that strike is the *minimum* — measured on one example:
>    46.08% at K=165, **45.59% at K=170 ≈ spot**, 49.03% at K=175. ThinkorSwim publishes
>    a per-series IV aggregated across strikes, which necessarily sits above the
>    at-the-money trough (52.11% in the same example). Schwab reports the *same*
>    volatility for the at-the-money call and put, so put/call skew is not the cause.
> 2. **Different move definition.** This app draws **one standard deviation**, a 68%
>    containment band, which is the correct basis for a *cone*. ThinkorSwim's chain
>    header shows the expected **absolute** move, smaller by exactly √(2/π) ≈ 0.798 —
>    which is what an at-the-money straddle prices.
>
> **The trap:** on that example the two differences nearly cancelled (32.90 versus
> 30.43, about 8% apart). That is luck, not calibration. On a symbol with a flatter
> smile this app's move would read roughly 25% *larger*.

Other caveats:

- After 15:00 CT today's synthesized candle uses the last price, which can include
  post-market prints. Sub-tick on a six-month chart, documented rather than fixed.
- Opened in a new browser tab when handed off from another page.

### Related pages

[Calculator](#calculator) · [Market Scanner](#market-scanner) ·
[Dealer Positioning](#dealer-positioning) (walls versus the cone is a strong combination).

---

## Captured Signals

*Menu: STRATEGY → Options → Captured Signals · Route `/options/captured`*

### What it is

A watchlist for trade ideas. Bookmark a scanner signal here and the app tracks it over
time, re-pricing it and telling you whether it worked — without committing to a position.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `cache:options:captured` |
| Auto-management | Every **5 minutes** during market hours, if enabled in Settings |
| Storage | `signals.db` |

### Reading the screen

| Column | Meaning |
|---|---|
| **REC** | The recommendation — **HOLD**, take profit, or cut. Green take-profit, red cut, amber hold. |
| **SYMBOL** / **STRAT** | Underlying and structure. |
| **MODE** | What the signal was captured as (e.g. PREMIUM). |
| **OPENED** / **EXP** / **DTE** | When captured, when it expires, days left. |
| **CREDIT** | The credit at capture. |
| **CUR PRICE** | What the spread costs to close **now**. |
| **RISK** | Remaining risk. |
| **P&L** | The paper result if you had taken it. |
| **GRADE** | Quality at capture — **Good** or **Marginal**. |

**Refresh marks (live)** re-prices everything against current chains. **Close selected**
records an exit.

**Auto-management** (Settings toggle, on by default) raises the stop to break-even after
+50%, defers delta-drift cuts on recoverable trades, and auto-closes on the exit rules or
at expiry. Turned off, the recommendations are advisory only and you close by hand.

### Why it matters

This is the app's learning loop, and it is the most valuable page for a trader who is
still calibrating. Capturing every signal you *considered* — not just the ones you took
— builds an honest record of whether the scoring actually predicts outcomes, free of the
selection bias in your own trade history.

Compare the **GRADE** column against realised P&L over a few weeks. If "Good" signals do
not outperform "Marginal" ones, the grading is not adding value for your style, and you
should weight it less.

**Where it is weak.** Captured signals are priced at the mark, with no slippage or
commission. Real fills are worse. The P&L column is an upper bound.

### When to use it

Capture liberally during the session; review weekly.

### Related pages

[Market Scanner](#market-scanner) · [Paper Ledger](#paper-ledger) ·
[Rescue](#rescue) (which reads these positions) · [Settings](#settings).

---

## Paper Ledger

*Menu: STRATEGY → Options → Paper Ledger · Route `/options/paper`*

### What it is

Your **hand-kept** practice book. Trades you sent here yourself, with live unrealized
P&L. No real money.

**This is one of three separate paper books**, and confusing them is the most common
source of "why does this number not match" in the app:

| Book | Page | Who trades it |
|---|---|---|
| **Paper Ledger** | this page | **You**, by hand |
| **Paper Account** | [Paper Account](#paper-account) | The automated engine |
| **Driver account** | [Claude Trades](#claude-trades) | The autonomous Claude trader |

They are fully isolated. Nothing crosses between them.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `cache:options:paper_trades` |
| Re-pricing | Open trades are re-priced on page load and on the management cycle, during market hours only |

### Reading the screen

Columns: **SYMBOL · STRAT · STRIKES · EXP · QTY · CREDIT · RISK · P&L · STATUS ·
ENTRY**, newest first. P&L shows realized for closed trades and live unrealized for open
ones, coloured green or red.

**Buttons:** **Reload** · **Close** (records an exit) · **Analyze** · **Delete** ·
**Delete all closed**.

**Analyze** opens a dialog with a verdict, a written rationale, and the metrics behind
it — unrealized P&L and percentage, current price, DTE, target and breakeven. Clicking a
row updates the detail panel silently instead.

> In the detail panel, the speedometer falls back to **probability of profit** for paper
> trades, because a hand-entered trade has no stored composite score.

### Why it matters

The gap between "this looks like a good trade" and "I would have held this through a 40%
drawdown" is where most options traders actually lose money. A ledger you maintain by
hand closes that gap at no cost.

### When to use it

Whenever you would have taken a trade but did not.

### Related pages

[Paper Account](#paper-account) · [Captured Signals](#captured-signals) ·
[Rescue](#rescue) · [EOD Report](#eod-report).

---

## Paper Account

*Menu: STRATEGY → Options → Paper Account · Route `/options/portfolio`*

### What it is

The account behind the **automated** paper-trading engine — the one that opens and
closes positions on its own from captured signals. Distinct from both the hand-kept
[Paper Ledger](#paper-ledger) and the [Claude Trades](#claude-trades) driver book.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `cache:options:paper_account` |
| Automatic cycle | **Hourly at the top of the hour, 09:00–14:00 CT**, trading days only. There is no 15:00 run. |
| Manual | **Run entry cycle** and **Run manage cycle** fire immediately. |

### Reading the screen

**Cards:** Equity · Cash · **BP Reserved** (buying power held against open positions) ·
Session P&L · Total P&L · Open count · Engine status.

**Open positions:** ID · Symbol · Strat · Strikes · Exp · Qty · **Credit** (collected at
entry) · **CurVal** (cost to close now) · **P&L$** · Status.

> Read Credit against CurVal. A credit spread is profitable when CurVal is *below* the
> credit collected — you sold high and can buy back lower.

**Fills log (last 100):** every simulated fill — order id, time, side
(`SELL_TO_OPEN` / `BUY_TO_CLOSE`), symbol, qty, type (`NET_CREDIT` / `NET_DEBIT`), fill
price, status and reason.

**Reset** sets a new starting balance.

### Why it matters

This is the app's mechanical baseline. The engine applies the same rules every time
without hesitation, fear or improvisation — so comparing it against your hand-kept
ledger tells you something specific: whether your discretion **adds** value over the
rules, or subtracts it. That is an uncomfortable comparison and a genuinely useful one.

The fills log is also the best available audit trail when a position behaves unexpectedly.

### Caveats and gotchas

- The automatic cycle is **hourly**, not continuous — a target hit at 09:15 is acted on
  at the 10:00 run unless you press **Run manage cycle**.
- **Reset wipes the book.** There is no undo.

### Related pages

[Paper Ledger](#paper-ledger) · [Captured Signals](#captured-signals) (the entry source)
· [Claude Trades](#claude-trades) · [EOD Report](#eod-report).

---

## Rescue

*Menu: STRATEGY → Options → Rescue · Route `/options/rescue`*

### What it is

Damage control. It finds credit spreads that have gone against you and offers a ranked
menu of ways to fix them, each costed including commissions.

### Where the data comes from

| | |
|---|---|
| Service | `options_svc` (:8211), `cache:options:rescue`, `:rescue_summary` |
| Positions | Read from the captured/paper books |
| Commissions | `config/commissions.toml` — the real Schwab rates, folded into every candidate |

### Reading the screen

**At-Risk Board:**

| Column | Meaning |
|---|---|
| **SYMBOL** / **STRAT** / **STRIKES** / **STRIKE DATE** | The position. |
| **Δ SHORT** | The short strike's delta — the closest thing to a live probability that it finishes in the money. Rising delta is the warning. |
| **P&L** | Current unrealized result. |
| **HEAT** | A 0–100 danger score. Green is calm, red is danger. |
| **STATE** | **tested** (price has reached the short strike) or **critical**. |

**Click a row** for ranked repair candidates — **roll** (out in time, or down/up in
strike), **widen**, or **close** — each showing its cash cost or credit and the resulting
risk profile.

**Apply** dispatches a simulated adjustment, behind a **stale-price guard**: if the
market has moved since the candidate was priced, the app refuses rather than applying at
a price that no longer exists.

**Ad-hoc Trade** lets you evaluate a position you enter manually rather than one from
the books.

### Why it matters

Repairing a losing spread is where most of the damage in premium selling happens.
Rolling for a *debit* to avoid realising a loss converts a small defined loss into a
larger one, and it is the single most common way credit sellers blow up an account.

The reason this page is worth using is that **it costs every candidate including
commissions and shows the new risk**. A roll that looks like a rescue frequently is not,
and seeing the number is what stops you taking it. Sometimes "close" ranks highest, and
that is the page working correctly.

**Where it is weak.** Candidates are priced at the mark. Adjustments are multi-leg and
real fills on multi-leg orders are meaningfully worse than the mark, so the true cost is
higher than shown.

> **Δ short is the number to watch.** As a rough guide, a short strike delta above about
> 0.30 on a spread you sold at 0.15 means the trade has roughly doubled its risk of
> finishing in the money. That is usually the moment to act, not when the P&L column
> turns red.

### When to use it

Check it once a day while you hold credit spreads, and immediately after any large move
in a symbol you are short premium in.

### Related pages

[Captured Signals](#captured-signals) · [Paper Ledger](#paper-ledger) ·
[Simulator](#simulator) (to model a repair before applying it).

---

## Trade Analyzer

*Menu: STRATEGY → Trade Analyzer · Route `/trade`*

### What it is

A Buy / Hold / Sell verdict on a single stock, over two horizons, with all the evidence
exposed. This is the app's only genuinely *stock-level* (rather than options-level) view.

### Where the data comes from

| | |
|---|---|
| Service | `trade_svc` (:8213), `cache:trade:analysis` |
| Trigger | On demand — type a symbol and press **Analyze**, or just tab out of the field |
| State | Remembers the last analyzed symbol across navigation |

### Reading the screen

**Two verdict cards, side by side.**

**Position (1–8 weeks)** is the more rigorous of the two. It is a **backtested,
IC-weighted cross-sectional factor model** — meaning each factor's weight was set by how
well it actually predicted forward returns historically (its *information coefficient*),
not by hand-tuning. The verdict comes from a **calibration band**, and the headline
reports what that band has historically delivered:

- an **expected return** over roughly the next four weeks, stated as **excess versus the
  S&P 500** — how much it beat or trailed the index, not the raw move;
- a **beat-SPY hit rate** (e.g. *"+1.3% excess / 20d · 52% beat-SPY"*).

**"Why — validated factors"** expands to each factor's z-score, weight, contribution and
information coefficient, plus the model's version and its **out-of-sample** IC. The
older hand-tuned score is preserved under **"Legacy heuristic"** for comparison.

**Investor (months+)** remains a heuristic score with its top reasons listed.

**Hard gates (⛔)** override the score outright — for example *"Below 200EMA: cannot be
BUY"*. A gate is a veto, not a deduction.

**The evidence panels below:**

| Panel | What it shows |
|---|---|
| **MTF EMA alignment** | Whether the trend agrees across 1-minute, 5, 15, 60 and daily. A percentage plus per-timeframe labels. All five aligned is a strong, rare condition. |
| **Momentum** | RSI, ADX, MACD histogram, VWAP, relative volume. |
| **Sector** | Sector strength, as a signed adjustment. |
| **Fundamentals** | P/E, PEG, revenue and EPS growth, ROE, margin direction. |

**Deep Dive** opens a full standalone report in a new tab — technicals, fundamentals and
short interest, plus options analytics (at-the-money IV, implied move, max pain, 25-delta
skew, IV term structure, 30-day constant-maturity IV, net GEX and flip, open-interest
walls) and an IV/RV rank. **AI Query** opens the same digest formatted as a chat prompt
you can copy into an AI assistant — it makes no API call itself.

### Why it matters

The honesty of the Position verdict is what sets it apart. Most retail scoring systems
are hand-tuned and never tested; this one publishes its own out-of-sample accuracy, so
you can see how much to trust it. An expected excess return of +1.3% with a 52% hit rate
is a **small** edge, and the page says so plainly rather than dressing it up.

The **excess-versus-S&P** framing is the right one and is frequently missed elsewhere: a
stock returning +3% in a month the index returned +5% has lost you money in
opportunity terms.

**Where it is weak.** Fundamentals come from the broker's instrument data and are
sometimes stale or odd — negative P/E on unprofitable companies, extreme PEG values.
Read the fundamentals card as indicative. The model is also cross-sectional, so it ranks
*relative* to the universe; in a falling market the top-ranked stock still falls.

### When to use it

Before taking a directional options position, and when [Momentum](#momentum) surfaces a
name you do not know.

### Related pages

[Momentum](#momentum) · [Strategy Finder](#strategy-finder) (how to express the view) ·
[Expected Move](#expected-move).

---

## Claude Trades

*Menu: STRATEGY → Claude Trades · Route `/driver`*

### What it is

An **autonomous paper options trader**. Claude selects and sizes defined-risk credit
spreads from the scanner's output; code-enforced guardrails cap the risk. This page
monitors it and can stop it.

**Nothing is ever sent to Schwab.** It trades its own isolated paper book.

### Where the data comes from

| | |
|---|---|
| Service | `driver_svc` (:8214) decides; `options_svc` (:8211) executes into the isolated book |
| Cache keys | `cache:driver:autonomous`, `:control`, `cache:options:driver_paper_account`, `:driver_paper_perf` |
| Checkpoints | 09:28 ET morning run, then every 30 minutes within the entry window **09:45–15:30 ET** |
| Re-pricing | Open positions re-priced **every minute** during market hours |

**Why the entry window is shaped that way.** The first ~15 minutes after the open are
skipped so the post-open structure is readable, and no *new* entries are taken in the
last 30 minutes before the close. Management and exits are unaffected.

### Reading the screen

**Status row:** whether autonomy is ACTIVE, the last cycle time, and three controls —
**Autonomous** (enable/disable), **Run now** (one immediate checkpoint) and a
confirm-gated **STOP** (halts new trades for the day; open positions keep managing).

**Tiles:** Day P&L against the day's target · Session P&L · Realized · Open P&L ·
Equity · Open count.

> **The daily target is dynamic.** The base is **$500**, but it ratchets against the
> month-to-date pace — up to a **$1,000** cap when behind, down to a **$250** floor when
> ahead. So the number in the tile changes day to day. A **−$1,500** daily loss halts new
> entries outright.

**Open positions** and a **decision log** (newest first, in CT) showing each checkpoint's
reasoning, including a one-line market-context summary.

**Performance scorecard:** trades, open, closed, **win rate**, realized, open P&L, total
P&L, **average win**, **average loss**, **profit factor**, best and worst trade, plus
breakdowns of **P&L by symbol** and **by strategy**.

**Performance view:** the closed-trade list with exit reasons — *Target hit*, *Delta
stop*, *Time stop*, *Money stop*.

### How the guardrails work

This is the part worth understanding, because it is what makes the design defensible:
**the model never sizes its own risk.** The cycle is

1. `build_packet` — assemble market context, candidates and account state;
2. `decider.decide` — Claude picks a candidate and proposes a size;
3. `guardrails.apply_guardrails` — **pure code** clamps the size and halts on the banked
   target, the loss cap, or a VIX threshold;
4. the clamped order is enqueued to the paper book.

Step 3 cannot be argued with by the model. Per-trade risk is evaluated in **per-contract
dollars**, and the driver's own book carries a higher per-trade cap ($1,500) than the
manual account ($250).

### Why it matters

As a research instrument this is the most interesting thing in the app: an unbiased,
fully-logged record of what a rules-plus-model system does with the app's own signals.
The decision log tells you *why* each trade was taken, which no human trading journal
manages consistently.

**Be clear-eyed about the results.** At the time of writing this book's record is
**negative**: 151 closed trades, a **45% win rate**, a **profit factor of 0.57**, an
average win of **+$165** against an average loss of **−$236**, and realized P&L of
about **−$8,300**. Put credit spreads account for essentially all of the loss while call
credit spreads are roughly flat.

That shape — winning less than half the time while losing more per loss than you make
per win — is the classic failure mode for premium selling: the wins are capped at the
credit while the losses run to the width. It is *the* thing this page exists to make
visible, and it is a strong argument for treating the driver as an experiment rather
than a strategy.

### Caveats and gotchas

- **"Executed" in the decision log means the order was enqueued, not filled.** The true
  outcome is in the account view's open results. A trade can be logged as executed and
  then rejected for risk.
- Analytics are **forward-only** — the equity curve is complete, but posture and
  MAE/MFE statistics only accrue on trades opened after those were added.
- Enabling autonomy costs Claude API calls per checkpoint. [Settings](#settings) shows
  the running count.

### Related pages

[Market Scanner](#market-scanner) (its candidate source) · [Sentiment](#sentiment) ·
[Paper Account](#paper-account) · [EOD Report](#eod-report).

---

# ACCOUNT

Two entries: your real holdings, and the day's results.

## Portfolio

*Menu: ACCOUNT → Portfolio · Route `/portfolio`*

### What it is

Your **real** Schwab holdings — the only place in the app where real money appears. It
is strictly read-only; no action on this page can affect your account.

### Where the data comes from

| | |
|---|---|
| Service | `portfolio_svc` (:8212), `cache:portfolio:positions` |
| Live P&L | Streamed from Schwab and republished at most every **2 seconds** |
| Full rebuild | Every **10 minutes** during market hours (hourly off-hours), or on **Refresh** |

### Reading the screen

**Three subtabs: Holdings · Sectors · Performance.**

**Holdings** — one row per position:

| Column | Meaning |
|---|---|
| **SYMBOL** | Stock, ETF, or an option contract in OCC format (e.g. `IWM 270617C00400000` = IWM, expiring 2027-06-17, Call, strike 400). |
| **SECTOR** | Its sector, or *Unknown* for ETFs and instruments outside the classification. |
| **QTY** / **MARKET VALUE** | Size and current value. |
| **DAY P/L** | Today's change. Streams live. |
| **TOTAL P/L** | Unrealized profit or loss since purchase. |
| **VS SECTOR (RS)** | **The most useful column here.** Relative strength against the position's own sector over 1 day, 1 week and 1 month, indexed to 100. Above 100 = beating its sector. |
| **SINCE PURCHASE** | Return since you bought it. |

**Sectors** shows your allocation against the S&P — where you are over- and
under-weight. **Performance** grades each position and offers per-position commentary.

**Status indicators** at the top show whether the proxy is up and whether the price
stream is live.

### Why it matters

**VS SECTOR is the column to read.** A position up 8% in a sector up 15% is a *losing*
position in the only sense that matters — you would have done better owning the sector
ETF. Absolute P&L cannot show you that; this can, over three horizons at once.

The Sectors tab answers a different question: concentration. Most self-directed
portfolios are far more concentrated than their owner believes, usually in technology,
and seeing the weight against the index is the fastest correction to that.

**Where it is weak.** Expired or worthless option positions show a market value of
$0.00 while retaining their historical loss, which can make the totals read oddly. The
sector classification is missing for many ETFs and crypto-linked products, so those rows
show *Unknown* and no relative strength.

### When to use it

Daily for the P&L; weekly for the Sectors and Performance tabs, which is where the
useful decisions are.

### Related pages

[Sector & Industry](#sector-industry) (context for the RS column) ·
[Trade Analyzer](#trade-analyzer) (a verdict on any holding).

---

## EOD Report

*Menu: ACCOUNT → More → EOD Report · Routes `/eod` and `/eod/detail`*

### What it is

The day's scoreboard, aggregating every book in the app into one document — and archiving
it as a standalone file you can reopen later.

### Where the data comes from

Purely a reader. It aggregates the `options:*` and `driver:*` caches; it computes nothing
of its own and calls no external service.

### Reading the screen

**Summary tiles:** paper session P&L · scanner signals · captured signals · paper trades
· driver realized P&L · driver win rate · driver trades.

**Performance blocks, one per book** — *Manual paper*, *Driver*, and *Captured closed* —
each with equity, session P&L, open unrealized and open count, then a table:

| Period | What it covers |
|---|---|
| **Daily** | Today. |
| **Weekly (WTD)** | Week to date. |
| **MTD** | Month to date. |

Columns are realized P&L, closed count with a win-loss split, win %, opened count, and
credit collected.

> **Realized P&L is bucketed by *exit* date; opened trades and credit collected are
> bucketed by *entry* date.** They are answering different questions and will not
> reconcile to each other. That is correct.

**Detailed** (`/eod/detail`) adds breakdowns by **strategy** (PCS / CCS / IC), by
**0-DTE versus swing**, and by **status** (open / closed / expired), plus the full trade,
scanner, captured and driver tables. Both views use a jump-link table of contents and
collapsible sections that work in the exported file as well as in the app.

**Generate** snapshots the current caches into standalone `summary.html` and
`detail.html` under `webgui/data/eod/<date>/`. The **Archive** list reopens any past day.

### Why it matters

The per-period tables are where a strategy's real shape appears. A book can show a
healthy daily number for weeks and still be losing month to date, because the losses
cluster. Weekly and MTD side by side make that visible immediately.

Splitting **by strategy** is the single most valuable breakdown here — it is how the
driver's PCS-versus-CCS asymmetry became apparent, and the same analysis on your own
book will usually show one structure carrying all the damage.

**Where it is weak.** It aggregates paper books priced at the mark. Real fills, slippage
and commissions are not in these numbers, so treat every figure as optimistic.

### Caveats and gotchas

- Realized P&L reads `$0` or `—` until trades actually close. That is by design, not a
  failure.
- **Generate** captures the caches *at the moment you press it*. Generating mid-session
  archives a partial day.

### Related pages

[Paper Ledger](#paper-ledger) · [Paper Account](#paper-account) ·
[Claude Trades](#claude-trades).

---

## User Manuals

*Menu: ACCOUNT → More → User Manuals · Route `/manuals`*

Links to the four manuals, each opening in a new tab:

| Manual | For |
|---|---|
| **User Guide** | Operating the app, task by task. |
| **Reference Guide** | This document — what each tab does and why it matters. |
| **Technical Reference** | Every formula, weight, threshold and cadence. |
| **API / Developer Reference** | Contracts, the Redis bus, service commands, proxy endpoints. |

Word (`.docx`) copies sit alongside the HTML under `docs/manuals/`.

---

# SYSTEM

Three machine-level controls, pinned to the bottom of the menu. None of them is part of
a trading workflow, which is exactly why they are separated.

## System Status

*Menu: bottom of the rail · Route `/status`*

### What it is

A live health board for every part of the stack, plus a check that each service is not
merely running but actually *publishing*.

### Reading the screen

**Component cards** — one per process:

| Component | Tier | Port |
|---|---|---|
| Memurai (Redis backbone) | 3 | 6379 |
| schwab-proxy (market data / auth) | 1 | 8100 |
| sentiment_svc | 2 | 8210 |
| options_svc | 2 | 8211 |
| portfolio_svc | 2 | 8212 |
| trade_svc | 2 | 8213 |
| driver_svc | 2 | 8214 |
| market_svc | 2 | 8215 |
| webgui (this app) | 1 | 8500 |

Each card shows online/offline, a health message, and a **Restart** button that
relaunches the component windowless. The proxy card additionally shows **Schwab auth**
status with a **Re-authorize** button.

**Published data freshness** is the more informative half. It lists each domain's latest
cache write with a version number and an age. **A service can be "online" and still not
be publishing** — this table is what catches that.

> Freshness is judged only when a publisher is actually **due** to run. The scanner only
> scans during the session, so overnight and at weekends its age is left alone rather
> than reported as a fault. Views that publish around the clock are checked around the
> clock, with a longer allowance outside market hours.

### Why it matters

Almost every "this page is broken" symptom in this app is a service that has stopped
publishing. Reloading the browser cannot fix that. This page turns a five-minute
investigation into a five-second one: find the stale row, restart that service.

**The order matters when restarting.** Memurai first, then the proxy, then services, then
the web app. A service restarted while the proxy is down will start and then fail to
fetch anything.

### Caveats and gotchas

- The Schwab **refresh** token is the fatal one. `token_expired: true` on the proxy is
  routine — it auto-refreshes. A missing or expired *refresh* token needs
  **Re-authorize**.
- The page header text says "the five domain services" while six are listed; there are
  six.

### Related pages

[Settings](#settings) (API call counts) · [Stop All Services](#stop-all-services).

---

## Settings

*Menu: bottom of the rail · Route `/settings`*

### What it is

Every preference in the app, plus two things that are genuinely useful rather than
cosmetic: **API usage** and **Maintenance**.

### Reading the screen

**Scanner alerts.** Enable the chime, pick the sound and volume, restrict alerts to
market hours, and set a **minimum score to alert** — the most useful knob here, because
it is what stops the app interrupting you for mediocre signals.

> Browsers block audio until you interact with the page. **Test sound** unlocks it.

**Desktop notifications.** A toggle plus a permission grant.

**Flow alerts.** Whether put/call premium crossovers and unusual activity alert you.

**Captured trade auto-management.** Whether captured signals are actively managed —
break-even stop after +50%, deferred delta cuts on recoverable trades, auto-close on the
exit rules. Off leaves them advisory.

**Manual paper: break-even lifecycle (experimental).** Opts the manual paper account into
the same lifecycle: arm break-even at +50% of credit instead of taking profit
immediately, then ride toward full credit protected by a break-even stop. Off (the
default) keeps the plain take-profit at +50%. The driver's isolated account is never
affected by this toggle.

**Market summary ticker.** The scrolling marquee at the bottom of every page. **Turning
it off also stops the Claude calls behind it**, so this is a cost control as well as a
display one.

**Appearance.** Every colour, font and menu style, in seven tabs — surfaces, state
colours, 3D buttons, gauges, charts, text, menu. **Save & restart web GUI** applies the
change; **Reset to defaults** is confirm-gated. Changes are written to
`config/theme.toml`.

**API usage.** Outbound **Schwab** calls counted at the gateway per actual HTTP request
(including retries), and **Claude (Anthropic)** calls counted at each of the three call
sites — the driver's decision maker, Gamma Analyze, and the ticker summary — for today,
the last 7 days and the last 30 days.

**Maintenance.** **Vacuum GEX history DB** compacts the intraday options database, with
an optional purge-first switch, and reports the before-and-after size.

### Why it matters

**API usage is the section to actually look at.** The app makes on the order of tens of
thousands of Schwab calls per day, dominated by the 1-minute gamma collection and the
market dashboard's ~3-second polling. Schwab rate-limits, and a stack that trips the
limit degrades in ways that look like unrelated bugs. This page tells you the real
number before that happens. The Claude counter does the same for money.

**Minimum score to alert** is the difference between an app that helps and one you mute.

### Caveats and gotchas

- Counters are **forward-only** and start when the relevant process restarts — they are
  not historical.
- **Vacuum locks the database for minutes.** Run it after hours; the tool refuses while
  the collector is active.
- Appearance changes need the web GUI to restart *and* a hard browser refresh.

### Related pages

[System Status](#system-status) · [Flow Alerts](#flow-alerts) ·
[Captured Signals](#captured-signals).

---

## Stop All Services

*Menu: bottom of the rail, the red button · Route `/terminate`*

A confirm-gated stop of the entire local stack — the gateway, all six services, and the
web app itself. **Memurai is deliberately left running**, because it is a shared Windows
service this app does not own.

After confirming, this page stops responding. That is expected: it has just stopped the
program serving it.

Restart with `start_all.bat` (or `start_all_hidden.bat` to run windowless).

It is rendered as a danger-outlined button and sits **last** in the menu so that
overshooting Settings cannot land on it.

---

# Appendix A — Subtab index

Pages carrying their own subtab row, and what each subtab does.

| Page | Subtabs |
|---|---|
| **Market Scanner** | **0-DTE** (expiring today) · **Swing** (multi-day credit spreads) · **Directional** (single-leg longs and shorts, scored on a *different*, non-comparable scale) |
| **Dealer Positioning** | **Gamma** (hedging intensity) · **Charm** (time decay of hedges) · **Delta** (directional exposure) · **Vanna** (volatility sensitivity) · **Flow** (call vs put premium ribbon) · **Net Prem** (net premium, up to 28 symbols) · **Term** (next five expirations) |
| **Simulator** | **Replay** (real historical path) · **What-if** (price and time sliders) · **IV Shock** (volatility multiplier) |
| **Portfolio** | **Holdings** · **Sectors** (weights vs S&P) · **Performance** (graded positions) |
| **Rescue** | **At-Risk Board** · **Ad-hoc Trade** |
| **Claude Trades** | Monitor (default) · **Performance** (closed trades and realized P&L) |
| **EOD Report** | **Summary** · **Detailed** (`/eod/detail`) |
| **Settings → Appearance** | Surfaces · State colors · 3D buttons · Gauges · Charts · Text · Menu |

# Appendix B — Refresh cadences

What updates when. All times US Central.

| What | Cadence | Window | Notes |
|---|---|---|---|
| Market Dashboard | **3 s** | Regular hours | 15 s off-hours; 60 s at weekends |
| Portfolio P&L | **2 s** | Streaming | Full rebuild every 10 min (hourly off-hours) |
| Gamma collection | **1 min** | 08:00–15:20 | ~45 symbols; five series each |
| Driver paper re-pricing | **1 min** | 08:00–15:15 | Keeps stops reacting within the minute |
| Flow-alert detection | **1 min** | With the gamma collection | |
| Opportunity Board | **1 min** | With the gamma collection | Spot/Day % overlaid on the ~30 s header tick |
| Sentiment composite | **2 min** | Market hours | 15 min off-hours |
| Dealer Positioning page | **2 min** | | The page; the data collects every minute |
| Market Scanner auto-scan | **15 min** | 08:00–15:15 | |
| Market Trend | **15 min** | | |
| Market Regime | **5 min** | Market hours | Holds the last read outside them |
| Captured-signal management | **5 min** | Market hours | If enabled in Settings |
| Term structure (gamma) | **5 min** | | The widest chain in the system |
| Manual Paper Account cycle | **hourly** | 09:00–14:00 | No 15:00 run |
| Driver checkpoints | **30 min** | 09:45–15:30 ET | Plus a 09:28 ET morning run |
| Gamma Analyze briefings | **4× daily** | Premarket · ~18 min after open · midday · close | |
| Momentum cascade | **nightly** | 16:20 | Daily bars change once a day |
| Sector Rotation / RRG | **manual** | | Cached; press Refresh |
| Trade Analyzer | **on demand** | | |
| Alert/badge watcher | **2 s** | | In the browser, on every page |

# Appendix C — Moving between pages

The app has explicit hand-off buttons. Knowing them turns separate screens into one
workflow.

| From | Action | To | What carries across |
|---|---|---|---|
| Market Scanner · Strategy Finder | **Send to Calculator** | Calculator | Symbol and all legs (the chain loads first, then the legs apply) |
| Market Scanner · Strategy Finder | **Send to Paper trade** | Paper Ledger | The trade, as a paper entry |
| Market Scanner · Strategy Finder · Paper · Captured · Calculator | **Expected Move** | Expected Move (new tab) | Symbol, expiry and strikes |
| Calculator | **Copy to Simulator** | Simulator | The exact legs |
| Simulator | **Copy to Calculator** | Calculator | The exact legs |
| Flow Alerts | **click a row** | Dealer Positioning | That row's symbol |
| Opportunity Board | read the row, then open | Dealer Positioning · Strategy Finder | (manual) |

Calculator and Simulator both **persist their full state** across navigation — symbol,
strategy, legs, slider positions and active tab — so moving away and back does not lose
your work.

# Appendix D — Glossary

Terms the app uses without defining them on screen.

**0-DTE** — expiring today. Maximum time decay, maximum gamma risk.

**ADX** — Average Directional Index. Trend *strength* regardless of direction. Above
~25 is generally read as trending.

**Breadth** — how many stocks participate in a move. Advancing minus declining issues.
A rally on weak breadth is narrow and historically less durable.

**CCS** — call credit spread. Sell a call, buy a further one for protection. Profits if
price stays below the short strike. Mildly bearish.

**Charm** — how an option's delta changes as time passes with price unchanged. Drives
dealer hedging near expiration.

**Credit spread** — sell one option, buy a cheaper further-out one. You collect a credit
up front; the maximum loss is the strike width minus the credit.

**Delta** — how much an option's price moves per $1 of underlying move. Also a rough
proxy for the probability of finishing in the money.

**DEX** — dealer delta exposure. The dealers' aggregate directional position in dollars.

**DTE** — days to expiration.

**Gamma** — the rate at which delta changes. High gamma means hedges go stale quickly.

**GEX** — gamma exposure. See [Dealer Positioning](#dealer-positioning).

**Gamma flip** — the price where aggregate dealer gamma changes sign. Above it, hedging
damps moves; below it, hedging amplifies them.

**IC** — iron condor. A call credit spread and a put credit spread together. Profits if
price stays in a range.

**IC (in the Trade Analyzer)** — *information coefficient*, a measure of how well a
factor predicts forward returns. Unrelated to iron condors; context distinguishes them.

**Implied volatility (IV)** — the volatility the option's market price implies. Higher
IV means richer premium and wider expected ranges.

**IV rank** — where current IV sits within its own recent range, 0–100. High IV rank is
the environment for selling premium.

**Max pain** — the strike at which the largest total value of options expires worthless.

**MTF** — multi-timeframe.

**Open interest (OI)** — contracts currently outstanding. Updated once a day.

**PCS** — put credit spread. Sell a put, buy a further one for protection. Profits if
price stays above the short strike. Mildly bullish.

**PoP** — probability of profit, model-derived.

**Profit factor** — gross profit divided by gross loss. Above 1.0 is profitable; below
1.0 is not.

**Put/call ratio** — put volume divided by call volume. Often read contrarian at
extremes.

**RS-Ratio / RS-Momentum** — relative strength against a benchmark, and its rate of
change. The two RRG axes.

**Term structure** — how implied volatility varies across expirations. Inverted
(near-term above longer-term) signals stress.

**Theta** — the daily value lost to time decay. Positive for premium sellers.

**Vanna** — how delta changes when volatility changes.

**Vega** — how much an option's price moves per 1-point change in implied volatility.

**VIX** — the market's expected 30-day S&P 500 volatility, from SPX option prices. See
Cboe's [methodology](https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf).

**Wall (call / put)** — the strike carrying the largest option exposure. Acts as
resistance or support because dealer hedging concentrates there.

# Appendix E — Further reading

External sources for the concepts this app assumes.

| Topic | Source |
|---|---|
| Dealer gamma exposure | SqueezeMetrics, *Gamma Exposure (GEX)* (2017) and *The Implied Order Book* (2020); vendor explainer at [SpotGamma](https://spotgamma.com/gamma-exposure-gex/) |
| VIX and volatility indices | [Cboe VIX products](https://www.cboe.com/tradable-products/vix/) · [VIX methodology (PDF)](https://cdn.cboe.com/api/global/us_indices/governance/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf) |
| Relative Rotation Graphs | [relativerotationgraphs.com](https://relativerotationgraphs.com/) · [StockCharts RRG documentation](https://help.stockcharts.com/charts-and-tools/other-charting-tools/rrg-charts) |
| Momentum and its crashes | Daniel & Moskowitz, *Momentum Crashes*, Journal of Financial Economics (2016) |
| Options mechanics, exercise and assignment | [The Options Industry Council](https://www.optionseducation.org/) |
| Black-Scholes and the Greeks | Hull, *Options, Futures, and Other Derivatives* — the standard reference |

---

*This guide describes what the app does and how to read it. For the formulas behind the
numbers see the **Technical Reference**; for operating instructions see the **User
Guide**; for the integration surface see the **API / Developer Reference**.*

*Nothing in this document is financial advice.*
