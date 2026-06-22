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
- **Read the market's mood** through a sentiment composite, market-trend gauges,
  and a sector-rotation map.
- **Review** your live brokerage portfolio and an end-of-day report.
- **Approve** the morning trading agent's proposed orders.

> **This is a single-user, local application.** It runs on your own machine and
> talks to Schwab through a local gateway. There are no accounts to log into in
> the web app itself.

---

# Getting Started

## Starting the application

The app is made of several background services plus the web interface. Start them
all together with one of the launcher scripts in the project root:

| Launcher | What it does |
|----------|--------------|
| `start_all.bat` | Opens the gateway, the five domain services, and the web app in **seven separate console windows**, then opens your browser. |
| `start_all_wt.bat` | Same seven processes, but as **seven tabs in one Windows Terminal window** (less desktop clutter; requires Windows Terminal). |

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
- **Five domain services** — Sentiment, Options, Portfolio, Trade, and Driver.
  Each one powers its matching page(s).
- **Memurai (Redis)** — a local data backbone the services and the web app share.

## The proxy-down banner

If you ever see a **red banner** across the top of every page saying the proxy is
unreachable, the Schwab gateway isn't running or has stopped. Live data won't load
until it's back. Use the **System Status** page (under **More**) to check and
restart components.

## "Waiting for … service" placeholders

Each page is powered by its service. If a page shows a *"Waiting for … service"*
message, that service hasn't started or has stopped. Start the full stack with a
launcher, or restart the specific service from the **System Status** page.

## Stopping everything

Use **More → Terminate** inside the app, or run `stop_all.bat`. This stops the
gateway, the five services, and the web app. (Memurai is intentionally left
running — it's a shared Windows service.)

---

# The Interface

## Layout

Every page shares the same frame:

- A **left navigation drawer** with expandable groups and flat items.
- A **header** at the top with a menu toggle and the current page title.
- The **page content** in the main area.

## Navigation groups

| Group | Pages |
|-------|-------|
| **Options** (expandable) | Scanner · Paper Trades · Captured Signals · Paper Portfolio · Calculator · Swing Scanner · Gamma · Simulator · Expected Move |
| **Sentiment** (expandable) | Sentiment · Sector Rotation |
| **Trade** (flat) | Trade |
| **Portfolio** (flat) | Portfolio |
| **Driver** (flat) | Driver |
| **More** (expandable) | EOD Report · System Status · Settings · Terminate |

The group containing the page you're on opens automatically, and the drawer
remembers which groups you left open as you move around.

## Alert badges and chimes

A small background watcher runs on **every** page. When new qualifying scanner
signals appear it can:

- Play a **chime** (a bundled sound), and optionally
- Fire a **desktop notification**.

It also shows red **count badges** on three nav items:

- **Scanner** — number of brand-new signals.
- **Captured Signals** — new captures.
- **Driver** — a pending order approval.

Opening that page clears its badge.

> **Browser sound is blocked until you interact with the page.** Click any nav
> link, or use **Test sound** on the Settings page, to unlock audio for the
> session.

You control all of this on the **Settings** page (see *Reports & System*).

---

# Options

The Options section is the heart of the app. All Options pages share a common
**signal detail panel** and a few cross-page action buttons.

## Scanner

**Route:** the home page (`/`).

The Scanner continuously looks for credit-spread opportunities and lists them in a
two-pane layout.

**Left pane — the signal list:**

- A **Scan** button (re-runs the scan) and a status line ("N signals").
- Two tabs: **0-DTE** and **Swing**.
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

The list refreshes itself automatically; you rarely need to press **Scan**.

## Swing Scanner

**Route:** `/options/swing`.

A focused, on-demand scan for one symbol over a swing horizon. Enter the
parameters and press **Scan**:

- **Symbol**
- **DTE** min / max (days to expiration)
- **Put Δ** and **Call Δ** min / max (delta bands for strike selection)
- **Min credit %**

Results appear in the same signal table (with the same Score chip, Grade, and the
three per-row action buttons) and detail panel as the Scanner.

## Calculator

**Route:** `/options/calculator`.

An options P&L calculator with a price/time heat map.

**Inputs:**

- **Strategy** — PCS (put credit spread), CCS (call credit spread), IC (iron
  condor), LONG_PUT, NAKED_PUT, LONG_CALL, NAKED_CALL.
- **Symbol** and **spot price**, plus a **Load** button that pulls the live chain,
  current price, a price range, and the list of expirations.
- **Expiry**, **Contracts**, **IV %** (with an **IV** button that reads the
  at-the-money IV from the chain), an **IV Δ %** shock, and a **Rate %**.
- A **leg section** that adapts to the strategy: for each leg, pick a **Strike**
  and enter (or **Fetch**) a **Premium**.
- **Range min / max** and **Range %** controlling the heat map's price span.

**Outputs (after pressing Calculate):**

- **Summary tiles** — entry credit/debit, max risk, max return, return-on-risk %,
  break-even(s), and probability of profit.
- A **P&L heat map** — rows are price points, columns are evaluation dates, each
  cell shows the dollar P&L and % return, shaded green (profit) to red (loss). The
  current spot row is highlighted.

If you arrived here via **Send to Calculator** from a signal table, the form is
pre-filled and the calculation runs automatically.

## Gamma

**Route:** `/options/gamma`.

A dealer-positioning view built from the options chain — gamma exposure (GEX) and
related measures.

**Controls:**

- A **Symbol** dropdown (default `$SPX`; the list is the collected watchlist).
- **Refresh now**, an **Explain** button, an **Analyze** button, and a **Next
  refresh** countdown.
- A **view toggle**: **GEX / Charm / DEX / Vanna / Term**.

**Status row:** a collector status dot, last-scan and next-scan times, and a one-
line summary (spot, strike count, net exposure).

**Panels:**

- **Left** — a horizontal bar chart of net exposure by strike near spot, with the
  spot line, a gamma-flip line, and call/put **wall** lines. (In **Term** view this
  becomes a full-width expiry × strike heat map.)
- **Right** — an **intraday heat map** of strike × time, colored from red
  (negative) through yellow to green (positive), with the spot price overlaid.

**Explain** opens an infographic that interprets the current positioning;
**Analyze** produces a copyable analysis prompt for the index symbols. The view
refreshes automatically every two minutes; switching views is instant and doesn't
re-fetch.

## Simulator

**Route:** `/options/simulator`.

Re-prices a single option contract under different scenarios using Black-Scholes.
Start by entering a **Symbol** and pressing **Fetch snapshot**, then choose a
contract with the **Expiry**, **Kind** (call/put), **Strike**, and **Direction**
(buy/sell) selectors. Three tabs:

- **Replay** (default) — re-prices the contract along the underlying's recent price
  path and shows a **six-panel stack** (price plus Delta, Gamma, Theta, Vega, Rho).
  A **scrub slider** moves a cursor through the trace; a **Look-back** dropdown
  controls how far back the path runs (Auto by DTE, or fixed windows).
- **What-if** — a **ΔS** slider (instant client-side price overlay) and a **Δt**
  slider (days forward) that re-prices the contract; an IV-shock comparison bar
  chart appears below.
- **IV Shock** — an **IV multiplier** slider compares the contract at base IV vs
  shocked IV across Price, Delta, Gamma, Theta, and Vega.

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

## Paper Trades

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

## Captured Signals

**Route:** `/options/captured`.

Signals the system has "captured" to track over time, with live re-pricing.

- **Action buttons:** Reload, **Refresh marks (live)** (re-price all open
  signals), **Close selected** (enter an exit value and reason).
- **Table:** Symbol, Strategy, Mode, Opened, Expiration, DTE, Entry Credit, Entry
  Risk, P&L (green/red), Entry Score, Current Score, **Score Drift**, Grade, and a
  color-coded **Recommendation** (green TAKE_PROFIT / red CUT / amber HOLD).
- Click a row for its detail panel (entry vs current score, recommendation,
  unrealized P&L).
- When a tracked signal hits a stop or target, the page raises a notification.

## Paper Portfolio

**Route:** `/options/portfolio`.

The account view for the automated paper-trading engine.

- **Account cards:** Equity, Cash, Buying-power reserved, Session P&L, Total P&L,
  Open count, and engine status (RUNNING / HALTED).
- **Action buttons:** Reload, **Run entry cycle** (open positions for eligible
  captured signals), **Run manage cycle** (re-price and auto-close), **Reset**
  (set a new starting balance).
- **Open Positions** table and a **Fills log** (last 100 orders).

> The manage cycle also runs automatically every 5 minutes during market hours, so
> the paper portfolio updates on its own — the buttons are manual triggers.

## Rescue

**Route:** `/options/rescue`.

An advisory **and** one-click-apply tool for **tested credit spreads** — put credit
spreads (PCS), call credit spreads (CCS), and iron condors (IC) — that have moved
against you. It tells you whether a position is in trouble and offers a ranked menu
of concrete ways to fix it, with the commission-adjusted cash and risk of each.

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

## Cross-page actions

Three buttons appear on signal rows across the Options section:

| Action | Available on | Effect |
|--------|--------------|--------|
| **Send to Calculator** | Scanner, Swing Scanner | Opens the Calculator pre-filled (strategy, symbol, expiry, strikes, premiums, IV) and runs it. |
| **Send to Paper Trade** | Scanner, Swing Scanner | Asks for a quantity, then creates a paper trade. Stays on the current page. |
| **Expected Move** | Scanner, Swing, Paper Trades, Captured, Calculator | Opens the Expected Move chart in a new browser tab, pre-filled and drawn. |

---

# Sentiment

## Sentiment dashboard

**Route:** `/sentiment`.

A market-mood overview that updates on its own (about every two minutes) whether or
not the page is open.

- **Two Market Sentiment gauges** — **Today** and **30-Day Average** — showing a
  0–10 contrarian composite (higher = more fear/opportunity), with a regime label.
- **Two Market Trend gauges** — **Today** (live intraday) and **30-Day**
  (structural) — a directional 0–100 trend score. A press-and-hold **Trend Detail**
  popup breaks the score into Price / Breadth / Sector / VIX sub-scores.
- A **component breakdown** (press-and-hold popup): each component's value, score,
  weight, and confidence.
- A **2×2 signal matrix** (Bias / Signal / Yesterday / Change) over a traffic-light
  background.
- A collapsible **30-Day History** chart with rolling averages.
- A full-width **Sector & Industry Performance** table — 11 sectors with Day / Week
  / Month %, Put/Call, and an RRG reading; each sector **expands** into its
  industries. A rotation banner and a green/cap-weighted/score summary sit above it.
- A status bar (Updated / Next / Sectors / Proxy).

Press **Refresh** for an immediate update.

## Sector Rotation

**Route:** `/sentiment/rotation`.

A Relative-Rotation-Graph (RRG) view of the 11 sectors against the S&P 500.

- A **headline** — Risk-ON / Risk-OFF, an assessment line, and the cyclical-vs-
  defensive spread.
- **Rotating From / Into** columns showing which sectors are leaving and entering
  leadership, with each sector's S&P weight.
- A **quadrant map** table sorted by momentum.
- A full-width **RRG scatter** with one "meteor-tail" trail per sector (a faded
  history line plus a bright current dot). Hovering a sector dims the others.

This page refreshes **only when you press Refresh**.

---

# Trade

**Route:** `/trade`.

On-demand analysis of a single symbol. Type a **Symbol** and press **Analyze**.

- A **header** with the symbol, price, bias, and volume.
- **Three equal-width cards in one row** — **Position** (1–8 weeks), **Investor**
  (months+), and **Markov Forecast**:
  - **Position** and **Investor** each show a Buy / Hold / Sell verdict (color-coded),
    a score, the top reasons, any hard "gates" that fired, and an expandable factor
    breakdown.
  - **Markov Forecast** projects where the Position score is likely to head. It shows
    the current **regime band** (Strong-Bear … Strong-Bull), a stacked-area chart of the
    **probability of being in each band** at +5 / +10 / +20 trading days, the per-horizon
    **P(BUY) / P(SELL) / expected score** and band **persistence**, and a **drift / tilt**
    line. The tilt is a small, bounded adjustment (±12 points) added to the **Position**
    card's headline score — shown as `base … · Markov …` — so the Position score reflects
    the forecast while the **Buy / Hold / Sell word never changes** (the tilt is advisory).
    The card appears only when there is enough price history; otherwise the row falls back
    to the two verdict cards.
- An **MTF EMA Alignment** card (per-timeframe trend agreement: Daily, 4H, 1H, 15m,
  5m, 1m).
- A **Momentum** strip (RSI, ADX, MACD histogram, VWAP, relative volume).
- A **Sector** card and, when available, a **Fundamentals** card (P/E, PEG, revenue
  & EPS growth, ROE, margin trend).

The analysis persists as you navigate away and back.

---

# Portfolio

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

# Driver

**Route:** `/driver`.

The morning trading agent's **order-approval queue**. Orders are **simulated**
(paper) — nothing is sent to a live brokerage account from here.

- **Run morning agent** grades the day and proposes trades; **Refresh
  performance** rebuilds the performance view.
- When a run is pending you'll see a **grade card**, a **conditions strip** (VIX,
  SPX, VIX1D, P&L today/week), and **proposed-trade cards** (bucket, structure,
  instrument, strikes, contracts, max risk, ML signal/confidence).
- **APPROVE** (behind a confirmation dialog) executes the proposed trades as
  simulated orders; **SKIP** declines them.
- A **Performance** section shows win rate, realized P&L, and a per-trade table.

The morning run also fires automatically once each weekday at **09:28 ET** — you
just approve or skip the result.

---

# Reports & System

## EOD Report

**Route:** `/eod` (with a `/eod/detail` drill-down).

An end-of-day rollup of the day's Options activity and the Driver.

- A **Summary** with per-domain one-liners (scanner counts, captured signals, paper
  session P&L, Driver grade/status).
- **Sections** for Scanner, Captured, Paper, and Driver with their tables.
- A **Generate** button snapshots the current data into standalone HTML files
  archived by date under `webgui/data/eod/<date>/`. Past dates are listed for
  reopening, and archived files can be served raw.

## System Status

**Route:** `/status` (under **More**).

A health board for the whole stack.

- An **overall banner** — green (all up), red (naming what's down), or grey
  (checking).
- A **component grid** — Memurai, the Schwab gateway, **Schwab Authorization**, the
  five services, and the web app itself, each with Online/Offline and its tier.
- A **Schwab Authorization** card with an **Authorize** button that opens the
  gateway's OAuth login in a new tab (use this if your Schwab token has expired).
- A **data-freshness** table showing each domain's latest update age, flagging
  scheduled data that has gone stale.
- **Offline** components show a **Restart** button. The board re-checks every few
  seconds and on demand.

## Settings

**Route:** `/settings` (under **More**).

Preferences for the alert system (saved on your machine, no login):

- **Scanner Alerts** — enable the audio alert, pick the sound (chime / bell /
  ping), a **Test sound** button, a **Volume** slider, an **only during market
  hours** toggle, and a **minimum score to alert**.
- **Desktop Notifications** — enable desktop notifications and grant the browser
  permission.

> Clicking **Test sound** also unlocks browser audio for the session.

## Terminate

**Route:** `/terminate` (under **More**).

A guarded "stop the whole local stack" page. The red **Stop all services** button
(behind a confirmation) stops the gateway, the five services, and the web app.

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

**Scans return nothing, or Gamma shows "no data."**
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
