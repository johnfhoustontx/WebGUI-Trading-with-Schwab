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

Between them sits a small in-memory database (Redis).
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
| **Trade Analyzer** | You want a Buy/Hold/Sell read on one stock, or a ranked shortlist to pick one from. |
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

## Desk

*Menu: pinned at the top of the rail · Route `/desk` — also the app's home page,
so plain `http://127.0.0.1:8500` lands here*

### What it is

One screen that answers, in order down the page, the four questions a trading
session actually opens with:

1. **What is the market doing?** — the top strip
2. **Where is the structure?** — Dealer Positioning
3. **What should I act on?** — the Opportunity Board and Flow Alerts
4. **What am I holding?** — Positions

That sequence is the whole design. It is also the admission rule: a panel earns a
place here only if it answers one of those four *at a glance*. Screens that reward
deliberate study — the RRG, the sector heat grid, the momentum leaderboard, the
Calculator, the Simulator — are deliberately absent, not overlooked.

### When to open it

First thing, and whenever you have lost the thread. Every panel is a summary of a
page that goes deeper, and **clicking any row opens that page already set to the
symbol you clicked**.

It is also the screen to **leave open**, because it is the only one that will
interrupt you: new flow alerts and newly-opened positions are announced out loud and
the arriving row glows for ten seconds (see *Spoken arrivals* below).

### The panels

**Top strip.** The clock; two dials showing **Day / Week / Month** for market
**sentiment** and market **trend**; then three verdict tiles — **Bias**, **Signal**
and the **market regime** word (Rallying, Balanced, Whipsaw, Stressed…).

**Bias** and **Signal** are two readings of one number, the market sentiment
composite. Bias says how to be positioned — **Long**, **Neutral**, **Cautious** or
**Short**. Signal says how strong the reading is — **Strong Bull**, **Bullish**,
**Neutral**, **Bearish** or **Strong Bear**. They are the same two tiles the
Sentiment page's Signals column carries, and each is coloured from its own word, so
the colour can never contradict the text beside it. Before either has been
published they show a dash rather than "Neutral": Neutral is the middle reading,
and no reading at all is not the same thing.

**Market Regime** is a separate and independent read — the tape's own committed
direction, not the composite's — which is why it sits at the far end.

Note the strip shows *no price at all*, and that is deliberate. SPX and QQQ appear
in Dealer Positioning immediately below with more context, and the two panels read
from different caches with independent update counters — so showing both could
display two different prices for one symbol on one screen. VIX used to be the one
exception (it is excluded from the dealer-row universe and cannot appear below); it
was replaced by Bias and Signal on 2026-08-24.

**Dealer Positioning.** One row each for **$SPX, SPY, QQQ, $NDX**: price and day
change, the **gamma flip** with the signed distance to it, the **call and put
walls**, net gamma exposure, and a regime chip. The small bar shows where price
sits *between the two walls*.

The chip reads **LONG GAMMA · PINS** or **SHORT GAMMA · RUNS**, and it is derived
from price-versus-flip and nothing else. Net gamma exposure is shown beside it as a
magnitude only. The two can legitimately disagree, and when they do that is real
information — but the screen will never print two conflicting regime claims in one
row. See *Dealer Positioning* below for what these levels mean.

**Opportunity Board.** The five hottest names, each with a one-line reason, its
at-the-money implied volatility **and whether that is rising or falling**, signal
strength, put/call ratio, net premium, and a setup tag when one is active.

There is no "edge" column. Edge normally means implied volatility minus *realized*
volatility, and this app has no realized-volatility series — so the column would be
decoration. Implied volatility's **direction** is published instead, and it is the
more useful half: it is what separates a volatility-crush setup from a
negative-gamma cascade.

**Live Flow Alerts.** The five newest unusual-options events — crossover, unusual
activity, gamma flip, large delta.

These show **call or put**, never *bought* or *sold*. Schwab publishes no
time-and-sales tape, so no one can honestly say which side initiated. Any product
that tells you "$3.9M of calls were **bought**" is inferring it, usually from the
bid/ask side, and that inference is often wrong.

**Positions.** Your paper trades and Claude's, merged, open only: source, strikes,
days to expiration, size, entry, live mark, unrealized profit or loss, and a flag —
**OK**, **Watch**, **At risk**, **Rescue**. The header totals open trades,
unrealized P&L, and how many need attention. *At risk* and *Rescue* are the two
that count toward that total; *Watch* does not.

### Spoken arrivals

A **new flow alert** or a **newly-opened position** is announced in a synthetic
human voice and the row glows cyan for ten seconds. Tickers are always spelled
letter by letter rather than read as words, which is squawk-box convention and also
the only rule that survives an unbounded symbol list: "SPY" read aloud as *spy* is
actively misleading.

**Two of the four flow detectors name a contract, and the announcement says it.**
Unusual activity and large delta are about one option, so the sentence carries its
expiry, strike and side: *"N D X. Unusual activity, 0-D T E 7 15 Put."* A crossover
is a fact about a symbol's whole premium tape and a gamma flip about the whole
dealer book — neither has a contract to name, so both keep the short form: *"S P Y.
Crossover alert, calls over."* A new position speaks its strikes, expiry and entry
price, and says **credit or debit** explicitly, because the paper book stores a
debit as a negative credit and a debit that sounded like a credit would be the most
expensive sentence this feature could say.

Numbers are spoken the way a trader hears them, not the way a computer reads them:
leading digits one at a time and the last two as a pair, so 715 is *"seven
fifteen"*, 21500 is *"two one five hundred"* and 207.5 is *"two oh seven point
five"*. This was settled by listening, not by argument. If any part of a contract
is missing or unreadable, the announcement falls back to the short form rather than
speaking a sentence with a hole in it — **shorter, never half**.

Three more rules are worth knowing, because each is a decision rather than an
accident:

- **A flag change glows amber and says nothing.** A position moving OK → At risk →
  Rescue is not an *arrival*; it was already on the screen, and the flag column
  already prints the new word. Only genuinely new rows speak.
- **All four flow detectors speak, large-delta included** — even though the scanner
  chime deliberately ignores that one. The reason the chime ignores it is that a
  *chime* carrying no information is noise at that detector's frequency. An
  announcement that names the ticker and the contract is not: if it does not concern
  you, ignoring it costs nothing.
- **A burst names the newest and counts the rest** — "…plus 5 more" — rather than
  reading a list. Six sentences back to back is a minute of talking over a moving
  tape.

Everything about it is switchable under *Settings → Spoken alerts (Desk)*, and it
obeys the **same** market-hours restriction as the scanner chime — there is
deliberately no second switch to fall out of step with the first.

### What the numbers are, and are not

- **The Desk computes nothing of its own.** Every figure is produced by the same
  function that produces it on the page it came from. This is deliberate: a summary
  screen that quietly disagrees with the page it links to is worse than no summary
  screen.
- **After the close, the walls disappear rather than going to zero.** Index option
  open interest reads 0 overnight, which produces an all-zero exposure grid and
  therefore *arbitrary* walls. A greyed panel with a timestamp means "this is the
  last reading I trust", not "the market is flat".
- **The freshness indicator reports the real collection state**, including
  "unknown". It is not a decorative "live" light.
- **Silence is more often a blocked browser than a broken feature.** Browsers refuse
  to play audio until the page has been interacted with, and the refusal produces no
  error anywhere. When it happens an **Enable spoken alerts** button appears at the
  top of the Desk; one click unlocks sound for the session, and it speaks a line back
  to confirm.
- **Nothing on this page can place, change, or close a trade.** It reads and links.

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
| **Broad-Market ETF** | SPY, DIA, QQQ, IWM, RSP, QQEW | Ranked by the day's move. RSP and QQEW are *equal-weighted* — compare where they land against SPY and QQQ to see if the move is broad or driven by a few giants. |
| **Top 10** | A **BIG10** composite plus its ten mega-cap members | BIG10 shows the equal-weighted average move and a breadth subline ("5/10 up"). |
| **Sector SPDR** | The eleven S&P sectors | Ranked by the day's move. |
| **Thematic / Industry ETF** | Semis, biotech, software, retail, oil… | Ranked. |
| **Factor / Momentum** | MTUM, SPMO | Whether momentum as a style is working. |
| **Fixed Income / Credit** | TLT, HYG, LQD | HYG weak while equities rally is a classic warning. |
| **Crypto / Alternatives**, **Countries** | | Countries is ranked. |

**Five frames re-rank themselves** by the day's move — Broad-Market ETF, Top 10,
Sector SPDR, Thematic, and Countries — so leaders and laggards are always at the ends.
**Every other frame keeps its curated order on purpose**: VIX before its tenors, the
cash indices paired with their futures. That layout is itself information.

**The top rail** carries a live clock, a session indicator, an advancing/declining
**breadth meter**, and an **A/B skin toggle** (Instrument or Heat Lattice). Tiles
**flash** when their value changes.

**What the breadth meter counts.** The four *stock* frames only — Broad-Market ETF,
Top 10, Sector SPDR and Thematic / Industry — so a bid VIX, a stronger dollar or a
rallying Treasury does not register as a decline, and the meter reads the equity tape
rather than the whole risk complex. The BIG10 composite is skipped, since it is the
average of the ten mega-caps already counted beside it.

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

## Bull / Bear Map

*Menu: MARKETS → Trend & Sentiment → Bull / Bear Map · Route `/sentiment/bullbear`*

### What it is

A three-level tree — sector, industry, stock — answering one question directly:
where is the market bullish, and where is it bearish. It exists because "bullish" is
**two facts, not one**, and every other rotation screen in this app collapses them.

### Where the data comes from

| | |
|---|---|
| Service | `sentiment_svc` (:8210), `cache:sentiment:bullbear` |
| Scores | The nightly momentum cascade, 16:20 CT — 11 sectors, ~69 industries, ~296 stocks |
| Day moves | One batched `/quotes` call covering every symbol at once, republished about every 30 seconds while the tape is open — **any** open session, extended hours and curb included — and throttled to once every 5 minutes once it is genuinely closed |
| Refresh | Re-pulls quotes and republishes; the scores are untouched until the next cascade |

### Reading the screen

**Trend** is `momentum.trend_strength`: the annualised exponential-regression slope of
log(close), scaled by R². It is **signed and absolute** — positive means price is
genuinely rising, with no benchmark involved. **vs SPY** is `momentum.relative_strength`:
excess return against the index, **signed and relative**. The page shows both, side by
side, and produces no combined score at any point.

| Quadrant | Reading |
|---|---|
| **Rising · Leading** | Unambiguous strength. The real bullish bucket. |
| **Rising · Lagging** | Going up, but the index is going up faster. |
| **Falling · Leading** | The trap. Down, but down less than the index — the row a relative-strength-only screen calls a buy. |
| **Falling · Lagging** | Unambiguous weakness. |
| **No reading** | The cascade could not score it. An absence, not a neutral. |

Ties go to the cautious side: a dead-flat trend is not "rising", and a zero excess is
not "leading".

**Breadth** is participation — the share of a group's constituents confirming its move.
It is a third and independent dimension, drawn beside the quadrant rather than folded
into it, and it separates two rows that look identical on trend alone. Sector and
industry rows carry it; **stock rows do not**, because a stock has no constituents, and
a dash there is the absence of a reading rather than 0%.

**The headline counts, it does not judge.** "5 of 11 sectors rising and leading" is an
arithmetic fact about the rows on screen. There is **deliberately no risk-on / risk-off
verdict on this page**: [Sector & Industry](#sector-industry) and
[Sector Rotation](#sector-rotation) already print such verdicts from quantities that are
not commensurable, and can therefore contradict each other. A reader may disagree with
what a count implies; they cannot disagree with the count.

**The two clocks are not decoration.** *Scores as of* dates the cascade, *Quotes* dates
the day-move column. When the quote call fails the page says so and still renders the
tree — the cost is one column, not the page.

**Expansion is lazy.** The default screen is eleven sector rows; industries build when
you open a sector and stocks when you open an industry, so several hundred rows are never
all on screen at once. More than one branch can be open at a time.

### Why it matters

The market-wide picture differs by level, and a single blended score averages that away.
Measured on 2026-08-19: 5 of 11 sectors were constructive while 105 of 296 stocks were in
outright decline — and 19 stocks plus one industry sat in the falling-but-leading bucket
that a relative-only screen would have painted bullish.

**Where it is weak.** The scores are nightly and cannot be otherwise — trend and relative
strength need months of history, so there is no such thing as an intraday reading of them.
The live layer answers only the narrower question of whether today is confirming last
night's map. Breadth is unweighted, so a sector's bar counts a micro-cap the same as a
mega-cap.

### When to use it

Before picking where to put a position: a defined-risk credit spread in a Rising · Leading
sector with broad participation is a materially different bet from the same structure in a
Falling · Leading one. Also whenever [Momentum](#momentum) or [RRG](#rrg) ranks something
highly and you want to know whether it is actually going up.

### Related pages

[Momentum](#momentum) · [RRG](#rrg) · [Sector Rotation](#sector-rotation) ·
[Sector & Industry](#sector-industry).

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
the highest-conviction rows on the page — **and the names themselves, listed by rank**.
Click one to decompose it in section 4; since these are stocks, that switches the level
selector to Stocks. Hovering a ticker shows its sector and industry.

**3 · Where the names sit.** The four quadrants as counts and shares, with the strongest
few named in each and **+N more** opening that quadrant's full membership — this is the
answer to "which names are Leading right now?". Every name is clickable. Same four quadrant names as [RRG](#rrg) — Leading, Improving,
Weakening, Lagging — but **the axes are different**: RRG measures strength purely against
the S&P, whereas this score blends five components of which relative strength is only
one. The two screens can legitimately disagree about the same sector.

**4 · What a score is made of.** Whichever name you selected — from a quadrant chip or a
leaderboard row — decomposed. Bars run either side of a centre line which is the universe
average, so the *sign* is the reading. They clamp at ±3, which is where the service caps
the z-scores. With nothing selected the card shows the current top-ranked name, so the
anatomy and the leaderboard's first row agree; **Top ranked** returns to that. Switching
level clears the selection, since a pick from one level does not exist on another.

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

Practically, the highest-value output is **alignment** — a name where the whole hierarchy
agrees. The green panel in section 2 lists every one of them, ranked, and those are the
candidates worth taking to [Overview](#overview) or
[Strategy Finder](#strategy-finder). The leaderboard's **Align** column shows the same
thing per row, as three blocks, but only for the names inside its top/bottom slice.

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
[Overview](#overview).

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

**The screen is three numbered steps.** ① STRATEGY and ③ LEGS run down a fixed
left-hand column; ② SYMBOL, the six metric cards and the P&L matrix fill the column
beside them. The page wears its own near-black palette rather than the app-wide navy —
that is deliberate, not a theming accident.

1. **① Strategy** — a cascading menu of templates: singles, verticals (credit and
   debit), iron condors, butterflies (long and iron), calendars and diagonals. Picking
   one fills the leg editor. Tag chips below name the cash-flow direction (**credit**
   or **debit**), the leg count and the lean; only the credit/debit chip is coloured,
   because the rest are descriptions rather than opinions. A one-line thesis says what
   the structure is betting on.
2. **② Symbol** — type it and press Enter, or just tab out of the field. A full-screen
   wait overlay shows while the chain loads (30-second timeout), and the pill in the
   title bar reads AWAITING SYMBOL → LOADING CHAIN → CHAIN LOADED · SYM. All the
   scalar inputs live in this frame, and the line under them reports how many strikes
   and expiries the chain actually carried — which is what explains a leg whose strike
   will not snap where you expect.
3. **③ Legs** — one editable **card** per leg. Each has its own **type** (put/call),
   **side** (long/short), **expiry**, **strike**, **quantity**, **premium**, and its
   **delta** read from the chain. Add or remove legs freely; the last one is locked,
   because a calculator with no legs has nothing to price. Per-leg expiry is what
   makes **calendars price correctly** — each leg is valued at its own time to
   expiration. The strip on the frame keeps a running **leg count, net premium and max
   loss** as you edit.
4. **Fetch Premiums** pulls live marks for the legs you have built.
5. **Calculate** produces the six metric cards and the P&L matrix.

**The other inputs:**

| Field | What it does |
|---|---|
| **Expiry** (top level) | Propagates to **all** legs and re-syncs their strike ladders. |
| **Contracts** | Position size. Everything scales by it. |
| **IV %** | The volatility assumption. Higher IV = pricier options and wider swings. |
| **IV Δ %** | A shock applied on top, for stress-testing. |
| **Price** | Override the underlying price. |
| **Rate %** | The risk-free rate, defaulting to the app-wide **4.5%**. A fixed assumption, not a live Treasury yield. Barely matters at short expiries — half a point of rate moves a 0DTE option by about 0.2% — but it is the least accurate input on multi-week structures. |
| **Strikes** | How many real chain strikes the matrix spans (default 24, centred on spot). |

**IV Update** implies the volatility **from the traded contract's mark**, the way
ThinkorSwim does — it solves backwards from the actual price rather than using the
chain's published figure. Before you have picked a strike it falls back to at-the-money
chain volatility.

**The six metric cards** are, in order: entry credit/debit (with the position size
under it), max risk, max return, return on risk (with a per-day figure), breakeven(s)
(with the first crossing's distance from spot), and probability of profit.

> **A dash is a real reading, and so is "Unlimited".** Nothing on this screen prints a
> `$0` it has not measured. A card reads **Unlimited** where the payoff genuinely has
> no cap — a long call's upside, a naked call's risk — and a **dash** where there is no
> number to give: no calculation yet, or a ratio that is not defined because one side
> is uncapped. The same rule governs the ③ LEGS strip (net premium is blank until every
> leg is priced; max loss is blank when the loss is unbounded) and the per-leg
> **delta**, which is blank whenever the chain carries no Greeks — routine outside
> regular hours, and the usual state of index chains overnight. A confident `0.00`
> there would be a wrong number rather than a missing one.

**The matrix** is the centrepiece: rows are underlying prices (real chain strikes
around spot), columns are dates. Green is profit, red is loss, and your spot row is
picked out in amber and scrolled into view.

> **The first column is "Now" and the last is "Exp".** "Now" is the trade's current
> mark-to-market value; "Exp" is the payoff at expiration. This distinction matters
> enormously for 0-DTE, where an earlier version showed only the expiration payoff
> everywhere and so hid the entire intraday behaviour of the trade.

Each date column carries dollars **and a percentage**, and **the heading says what the
percentage is a percentage of.** **% MAX** is a share of the most the structure can
make. **% COST** is a share of what you paid, and appears when the payoff has no cap
to measure against — a long call, for instance, where "+125% of cost" is the figure
you actually want. A plain **%** over dashes means neither basis exists. Before
2026-08-19 the column was a share of the *premium received*; for a credit spread that
is the identical number, because the credit **is** the maximum return.

**Copy to Simulator** sends the exact legs across. **Expected Move** charts them.

### Why it matters

Two numbers decide whether a credit spread is worth taking: the credit you collect and
the width you risk. Everything else — probability, breakeven, Greeks — follows from
those. This page shows all of it against real strikes rather than round numbers, which
is the difference between a trade you can actually fill and one you cannot.

The matrix's second axis is *time*. A credit spread that is profitable at expiration
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
- Widening the strikes raises the credit **and** the max loss. The cards show both;
  check the ratio, not just the credit.
- **Loading a different symbol clears the cards and the matrix.** They belonged to the
  symbol they were calculated for, and leaving them on screen under a status pill
  naming a new one would state two symbols at once. Reloading the *same* symbol is a
  refresh and keeps them — which is also what happens every time you navigate back to
  the page, since it restores and re-loads on its own.

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

Fetch a snapshot for a symbol, pick a **Strategy**, adjust the **legs** — one card per
leg, the same widget the Calculator uses, in this page's navy rather than the
Calculator's near-black; the controls and the strategy sit side by side in one panel.
The snapshot carries no Greeks, so the cards here show no per-leg delta, and the last
leg cannot be removed (a position with no legs leaves the charts frozen on the previous
sweep). Then use the three subtabs:

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
[Overview](#overview) (for the directional opinion itself).

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

The table opens **newest capture first**; click any column heading to re-sort it.

**The footer** sums the session in four figures: signals **opened today** and
**closed today** (counts of captures and of closes, so a signal taken and closed in
the same session appears in both), **P&L today (booked)** — the realized total of
today's closes — and **P&L today (open)**, the unrealized total across every signal
still running, whenever it was captured. Both counts and the booked figure are dated
in **CT**, matching how the database stamps them.

⚠ **Open P&L reads an em dash, not $0.00, until the signals are priced.** The stored
view carries no marks until a reprice runs, so on a cold page every unrealized figure
is unknown rather than zero — reporting that as $0.00 would show a flat book where
there is really no reading at all. An empty book still shows a true $0.00. Hover the
figure to see how many open signals currently carry a live mark; **Refresh marks
(live)** prices them all.

**Refresh marks (live)** re-prices everything against current chains. **Close selected**
records an exit — click a row first to pick it, which also loads it into the detail
panel on the right.

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

## Overview

*Menu: STRATEGY -> Trade Analyzer -> Overview · Route `/trade`*

### What it is

The Signal desk's landing screen: market state, both verdicts, dealer positioning and
peer placement for one symbol, under a command bar shared by all four tabs.

### Where the data comes from

| | |
|---|---|
| Service | `trade_svc` (:8213), `cache:trade:analysis` |
| Trigger | Commit a symbol in the command bar - Enter, Tab, or blur |
| State | The committed symbol persists across all four screens and across navigation |

### Reading the screen

**The command bar** carries a draft/committed distinction: the symbol box outlines
indigo while your typing differs from what is on screen, and an emptied box reverts
rather than clearing. The company and sector line, and every panel below, follow the
committed symbol.

**Deep Dive** and **AI Query** sit in the command bar. Each enqueues its report for the
committed symbol and opens the result in a new tab when it lands; the cache version is
baselined at click time, so a stale report from an earlier run never opens a tab.

**Hover anything you do not recognise.** Every tile on these four screens
carries its own plain-English explanation on hover: the clearance chips and side
cards, each factor name, every table column on the Rank board and the Evidence
screen, the trade-plan rows, the dealer levels, the panel titles and the two
report buttons. The explanations say what the number is and where it misleads,
not just what it is called - so `LONG CLEARED` explains that it is permission
rather than a recommendation, and `SHORT RELATIVE ONLY` explains that the model
predicts a LAG against the index rather than a fall, which is why a plain short
on a correct read can still lose money.

**Short Term** leads with a RECOMMENDATION - an action, a confidence chip and one
line saying what to do - and keeps the ranking underneath as information.

**The action is not the model's verdict.** It combines the verdict with what the
broad market permits, which is why a name the model ranks at the very bottom can
read "Pair short" rather than "Sell short": the model predicts the stock will LAG
the index, and in a rising market a stock can lag and still go up. The six actions
are Buy, Buy paired, Sell short, Pair short, Stand aside and No trade.

**Read the confidence chip with the action, not after it.** It is the band's own
historical hit rate expressed as DISTANCE from a coin flip - so a band that beat
the index only 44% of the time reads Moderate on the short side, because lagging
is what that side predicted. Moderate means at least 5 percentage points from
50/50, Low at least 2, Very low is within noise. Even Moderate is a small edge; it
shows up across many trades rather than in any one, so it argues about sizing more
than about picking.

The amber line beneath states what share of the model's weight sits on volatility
factors. It used to appear only on the Evidence screen - a card that merely ranked
could afford that, one that says "Buy" cannot.

The ranking still appears below the rule, on the same rail: the band, the
calibrated expectation against the S&P over 20 trading days, and the hit rate. **The band is not a percentile of today's names.** The
score's inputs are today's cross-section - each factor measured against the same 78
symbols the model was fitted on - but the five bands are fixed score thresholds cut
from five years of the model's own output. So 90th means "top band", not "top 10% of
the board", and the bands fill unevenly: on 2026-08-22 the 78 names landed 20 / 20 /
12 / 14 / 12 from the weakest band to the strongest, where a true percentile would put
about 15 or 16 in each. A defensive market can leave the top band nearly empty. Hover
the rail for that explanation in the app. The two side cards state what the tape
permits per side.

**Long Term** puts its six factors on the same centred bar language used on the
Evidence screen - right of centre is a positive contribution. A factor the engine did
not return reads `n/a` with no bar, never a zero-length one.

**Earnings trajectory scores the last four quarters of earnings surprises.** Four
clear beats in a row is the strongest reading; a miss in the most recent quarter is
the weakest, whatever came before it. That history comes from **Alpha Vantage**, not
Schwab - Schwab's fundamentals payload carries 56 fields and no surprises at all,
which is why this row contributed exactly 0 for every stock until that source was
wired in. Forward guidance is still unavailable from any source here, so the
component scores on the surprise record alone rather than averaging in a permanent
zero, which used to halve it.

A zero on that row now means what a zero means anywhere else - a genuinely mixed
record - or that the vendor holds no history for the symbol, which the row cannot
tell apart. Free cash flow is still absent, so the check that would cap a stock at
HOLD on negative cash flow can never fire.

**Dealer positioning** is withheld in full when uncollected or stale. That is the
off-hours case, and an absent ladder is the honest rendering of it.

### When to open it

First, for any symbol. It is the orientation screen.

### Related pages

[Evidence](#evidence) · [Rank Board](#rank-board) · [Trade Plan](#trade-plan).

---

## Evidence

*Menu: STRATEGY -> Trade Analyzer -> Evidence · Route `/trade/evidence`*

### What it is

Why the Position verdict is what it is - every weighted factor, its z-score against
the cross-section, its contribution, and its historical IC - plus how the model has
been doing and what this name has done before.

### Where the data comes from

| | |
|---|---|
| Service | `trade_svc`, `cache:trade:analysis` (`swing_model.contributions`) |
| Track record | The artifact's own OOS IC, plus the live monitor over `rec_journal.db` |
| History | This symbol's journalled reads, labelled once their 20 days elapse |

### Reading the screen

The **contribution bar** is the same centred language as the Investor factors: right
of the axis is a positive contribution to the composite, left negative, and the
foot of the table is their weighted sum.

⚠ **The two right-hand cards are not the same question.** Track record is about the
MODEL across every symbol; history is about THIS symbol. Five reads of one name
cannot support a correlation, so the history is a list of outcomes with `pending`
where the horizon has not elapsed - never a statistic.

The amber callout states what share of the model's weight sits on volatility
factors. Read it before reading the table.

### When to open it

Before acting on a Position read you intend to size, and any time the verdict
surprises you.

### Related pages

[Overview](#overview) · [Trade Plan](#trade-plan).

---

## Rank Board

*Menu: STRATEGY → Trade Analyzer → Rank Board · Route `/trade/board`*

### What it is

The same swing model as [Overview](#overview), run over **every name in the
model's universe at once** and sorted. Analyze answers "what about this stock?"; the
Rank Board answers "of everything the model can see, what is best and worst today?" —
the shortlist you start from rather than the verdict you end on.

### Where the data comes from

| | |
|---|---|
| Service | `trade_svc` (:8213), `cache:trade:rank_board` |
| Trigger | Rebuilt from the daily universe snapshot; **Rebuild** forces it |
| Scoring | The *same* scorer the Analyze card uses — one code path, so the two can never disagree about a symbol |

### Reading the screen

**Deciles are today's ranking, not a historical grade.** A name in decile 10 is
the best of *this* cross-section right now. That is a different question from the
calibration band shown on the Analyze card, which asks where the score sat against
five years — a universe where every name is mid-band still has a best and a worst.

**Long candidates / Short candidates** are the top and bottom deciles. Each carries a
note explaining its own state, because a thin short list has three quite different
causes:

- *"Express these RELATIVE…"* — the tape has not cleared the short side. The model
  predicts **excess return versus SPY**, so a bottom-decile name in an uptrend is
  predicted to **lag**, not to fall. Pair it against the index rather than shorting it
  outright.
- *"Too few names in today's cross-section…"* — a sample-size limit, not a reading of
  the market. With fewer than ten names there is no bottom decile to speak of.
- *"Directional expression is cleared"* — the tape permits the trade as stated.

**Gates mark rows; they never remove them.** A gated row stays visible with its reasons,
because "the top-ranked name reports earnings in two days" is exactly what you opened
the board to find out. The line beneath the header names **which** gates were checked
here — the board evaluates a *subset* of the Analyze card's, so a row with no gates has
not cleared everything the card would test.

### ⚠ What the ranking is actually sorted by

An amber line states what share of the model's weight sits on **volatility factors** —
currently about half. That matters more here than anywhere else in the app: it means
**the top of this board is the high-beta end of the universe**. Measured over five
years, this ordering works when the market rises and inverts when it falls. Treat the
board as a starting shortlist to research, never as a ranked buy list.

### The model paper book

Beneath the board, an isolated paper book follows the board's own pools so the
model accrues a track record nobody has to place. It takes the ungated names
from each pool, applies the Trade Plan's stop, target and 20-trading-day time
stop, and splits its reporting by side.

| | |
|---|---|
| Service | `trade_svc`, `cache:trade:model_book`, store `model_book.db` |
| Trigger | The board's **Rebuild** advances it; otherwise it follows the board |
| Scope | **Paper only**, and isolated from the Claude Trades book |

⚠ Two things about what it measures. It trades the **underlying**, not the
options structure the plan suggests — a spread's theta and vega would swamp the
question of whether the ranking works, so a book that lost money on correct
calls would look identical to one whose calls were wrong. And a **relative**
short is held as a pair against SPY, because return-versus-the-index is what the
model actually predicts.

### When to open it

At the start of a research session, to pick what to analyze. Not as a signal in itself.

### Related pages

[Overview](#overview) (the per-symbol verdict) · [Momentum](#momentum) ·
[Strategy Finder](#strategy-finder).

---

## Trade Plan

*Menu: STRATEGY -> Trade Analyzer -> Trade Plan · Route `/trade/plan`*

### What it is

The verdict rendered as something you could be wrong about: structure, legs, entry
zone, stop, target, time stop and events - beside a card stating what would change
the call.

### Where the data comes from

| | |
|---|---|
| Service | `trade_svc`, `cache:trade:analysis` (`trade_plan`) |
| Structure | A pure lookup over side x IV state x dealer levels |
| Stop | ATR or the nearer wall, whichever is TIGHTER; absent if neither exists |

### Reading the screen

**The time stop is highlighted deliberately.** It is the model's own 20-trading-day
horizon, and it is the only field nothing else in the app enforces - a position
opened on a 20-day edge and held three months is no longer being held for the
reason it was opened.

**The no-trade card is always present**, even when a plan is cleared. A refused side
with its reasons is a research finding; hiding it would make the screen look like
the model had nothing to say.

⚠ Where the tape has not cleared a directional short, the alternative offered is a
**pair against SPY**. The model predicts excess return versus the index, so that is
the expression the prediction actually supports.

### When to open it

After Overview and Evidence, when you have decided the read is worth acting on.

### Related pages

[Overview](#overview) · [Evidence](#evidence) · [Rank Board](#rank-board).

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
[Overview](#overview) (a verdict on any holding).

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
| Redis (bus backbone) | 3 | 6379 |
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

**The order matters when restarting.** Redis first, then the proxy, then services, then
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

> Browsers block audio until you interact with the page. **Test sound** — or **Test
> voice** — unlocks it.

**Spoken alerts (Desk).** Whether the Desk announces new flow alerts and
newly-opened positions out loud, which of six neural voices does it, and how loud.
**Test voice** speaks a sample and doubles as the audio unlock. Note what is *not*
here: a market-hours toggle. Spoken alerts reuse the one in **Scanner alerts**, so
the two can never disagree about when the app is allowed to make noise.

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
web app itself. **Redis is deliberately left running**, because it is a *system*
service this app does not own.

After confirming, this page stops responding. That is expected: it has just stopped the
program serving it.

Restart with `systemctl --user start trading-prod.target`.

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
