[TOC]

# Introduction

**NeuralStrike** is a single, browser-based control center for a Charles Schwab
options-and-equities trading workflow. It replaces a collection of older desktop
and dashboard tools with one web app you open in your browser at
**https://app.neuralstrike.co**, behind a password and an authenticator code.

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

> **This is a single-user application.** It runs on your own machine and talks
> to Schwab through a local gateway. Reaching it from anywhere else goes through
> a sign-in — your password and an authenticator code — and the app itself still
> listens only on the host.

---

# Prerequisites

Before the app will run, a few things need to be in place on your machine. This is
the plain-English checklist — the *Technical Reference* has the full detail
(exact package versions, environment variables, etc.).

## The essentials (required)

- **A Linux host.** The stack runs on Ubuntu Server 24.04 LTS as `systemd` user
  services. You do not need a desktop on it — you reach the app from your own
  computer over an SSH tunnel (see *Opening the app*, below).
- **Python 3.11 or newer.** Install it from python.org if you don't have it.
- **A one-time setup** in the project folder, which creates the virtual
  environment the units expect and installs everything:

  ```bash
  uv venv --python 3.11 .venv && uv pip install -r requirements.lock
  ```

  Use the **lock**, not `requirements.txt` — the lock is what the host installs,
  and a package present in one but not the other ships missing.

- **Redis running.** The local "backbone" the app's parts talk through, on port
  6379. `sudo systemctl enable --now redis-server`. **Nothing works without it** —
  every page shows a "Waiting for … service" placeholder.
- **A modern web browser** to open the app at **https://app.neuralstrike.co**
  (or `http://127.0.0.1:8500` if you are sitting at the machine itself).

## Schwab account (required for live data)

The app reads market data and your positions from Schwab, so you need:

- A **Schwab developer account** with a registered app, which gives you an **App
  Key** and **App Secret** (register the callback URL as `https://127.0.0.1:8182`).
- Those keys saved into **`shared/appsettings.json`** (copy the provided
  `shared/appsettings.example.json` and fill in your keys).
- A **one-time Schwab login**: after starting the app, open **System Status → Schwab
  Authorization → Authorize** and sign in. This creates your token file. The
  button opens the proxy's login page, which is on your private Tailscale network
  rather than the public site — so the device you click it from must be signed in
  to Tailscale.

> **Good to know:** the app refreshes your Schwab login automatically most of the
> time. If live data stops and **System Status** shows the Schwab login expired,
> just click **Authorize** again.

## Nice-to-have (optional)

- **An Anthropic (Claude) API key** — only needed for the AI features: the Gamma
  **Analyze**/**Explain** infographics.
  Set it as the `ANTHROPIC_API_KEY` environment variable
  (or in a `shared/anthropic_key.txt` file). Without it, those features simply stay
  quiet — nothing else is affected, and the auto-trader safely stands down.
- **Push notifications** (Telegram / Discord / text message) — configured in
  `shared/notifications.json` if you want alerts on your phone. Skip it and the app
  is silent on those channels. Once they are set, the app also posts one **trade idea**
  an hour during the regular session (08:35–14:35 CT): the best Strong or Good trade
  on the Market Scanner, drawn as an image with its legs, grade, risk, profit,
  probability of profit and payoff chart. It skips an hour rather than post a weak,
  stale or same-day trade. Turn it off with `"trade_idea": {"enabled": false}`.
- **The watchlist workbook** `options-scanner/data/Top 20.xlsx` — sets which stocks
  the scanner watches. Without it, the app falls back to the core index symbols.

## Ports the app uses

The app runs entirely on your own machine and needs these local ports free:
**6379** (Redis), **8100** (Schwab gateway), **8210–8213**, **8215** and **8216** (the six services),
**8500** (the web app) and **8501** (the public live screens). If another program
is already using one of them, the matching piece won't start.

---

# Getting Started

## Starting the application

The app is made of several background services plus the web interface. Start them
all together with one of the launcher scripts in the project root:

| Command | What it does |
|----------|--------------|
| `systemctl --user start trading-prod.target` | Starts the gateway, the six domain services, the web app and the public live screens. |
| `systemctl --user list-units 'trading-prod*'` | Shows what is running. |
| `journalctl --user -u trading-prod-options_svc -f` | Follows one service's log. |

They also start **automatically when the machine boots** — you do not normally
run anything by hand.

**Opening the app.** From any browser, go to:

```
https://app.neuralstrike.co
```

The app itself still listens **only on the host**, on `127.0.0.1:8500`. What
makes that address reachable is a small web server on the same machine which
terminates the certificate and passes the request through — the app is never
exposed to the network directly.

**You will be asked to sign in**: your password, then the current 6-digit code
from your authenticator app. Tick **Remember this device** and that browser will
not ask again for a while; a new browser, or a private window, always will.

Two things worth knowing when it refuses you:

- The message is deliberately the same for a wrong password, a wrong code and
  too many attempts. It will not tell you which one you got wrong.
- A code can only be used **once**. If you have just signed in and immediately
  hit something that asks for a code again — stopping the stack does — wait for
  your authenticator to roll to the next one.

**Sitting at the machine itself?** `http://127.0.0.1:8500` still works there and
skips the sign-in, which is what the wall display uses.

## The public live screens

Seventeen of the app's screens are also published **without any sign-in** on
a second address, `https://live.neuralstrike.co` — the Desk,
Opportunity Board, Flow Alerts, Macro Board, Sentiment, Bull / Bear Map, Sector &
Industry, Sector Rotation, RRG, Momentum, Net Prem, **Gamma**, the **Strategy
Finder**, the Rescue ad-hoc form (published as **Rescue my Sh\*tty trade**), the
**Calculator**, the **Simulator** and **Market News**.

The public **Gamma** page is Dealer Positioning with its symbol dropdown: a
visitor picks any symbol your collector gathers (the `symbols.toml` lists plus
your Top 20 list) and switches between Gamma, Charm, Delta, Vanna and Flow.
There is no Term tab and no Net Prem tab (Net Prem is its own screen). $SPX, SPY
and QQQ are always live; any other symbol goes live when a visitor picks it and
stays live while someone watches it, up to 8 at once (Settings → Configuration
→ Public Gamma page). Picking a symbol costs no Schwab call: it reuses the chain
the collector already downloads every minute. The addresses of the old pinned
Gamma screens (`/charm`, `/gamma/spy`, `/premium-divergence/spy` and so on) now
open this page.
Each one shows the same scrolling market-summary ticker along the bottom that
your own app does.

The public Strategy Finder is one of four screens a visitor can act on: they type a
symbol and press Scan, and it ranks strategies for that symbol with one fixed
set of filters (expirations up to 90 days, short legs between 10 and 20 delta,
a credit of at least 10% of a spread's width). They cannot change the filters,
open a paper trade or use the Calculator. Scans run from 08:40 to 15:00 CT on
trading days; a repeat request within 15 minutes shows the earlier result
instead of scanning again. There is a limit of 200 public scans a day for all
visitors together and 10 an hour for each visitor. A result shows the best 5
ideas of each strategy type. SPY and QQQ are scanned automatically at 09:08 CT
so the page opens on a fresh result. Every limit is in
**Settings → Configuration → Public Strategy Finder**, and **Settings →
General → API usage** shows how many public scans have been used today.

The public Rescue form is the second screen a visitor can act on. It is the
**Ad-hoc Trade** tab of your own Rescue page and nothing else: your at-risk
board is never shown there. A visitor loads a symbol, lays out a trade they
hold elsewhere, types the price they received or paid for each leg, and
presses Compute to get the same ranked repair menu you would see. Every card
is advisory; there is no Apply. The expiration and strike lists carry no
prices, and the cards show each leg without its fill price unless **Show
per-leg bid and ask** is on in the Public Strategy Finder settings. It runs
08:40–15:00 CT on trading days, within one daily budget of 600 Schwab-spending
requests shared by all visitors of the Rescue form, the Calculator and the
Simulator together, and 20 rescues an hour for each visitor. The same trade
asked for again within 5 minutes shows the earlier answer, and the same
strikes can be computed only 3 times in those 5 minutes whatever prices are
typed. No list of visitors' requests is kept, but each answer is held for 30
minutes. Every limit is in **Settings → Configuration → Public Rescue form**,
the shared daily budget under its **Budget** heading.

The public **Calculator** is the third. A visitor loads a symbol, picks a
strategy and an expiration, builds the legs on that symbol's real strikes and
types the price of each leg; the page then prices the position with the same
six metric cards and profit-and-loss matrix your own Calculator draws, and
estimates the implied volatility. **Rate my trade** grades the legs with the
Strategy Finder's scorer and checklist, without the paper-book line, since the
visitor has no book here. While **Show per-leg bid and ask** is off (the
default) the page shows no chain grid, no price source (Bid / Mark / Ask), no
delta, and the checklist's cost-to-trade line stays grey, because each of those
would show a quote; the visitor's own typed prices drive everything, and a
rating with an unpriced option leg is refused until they type one. **Open in
Simulator** carries the position across to the public Simulator in the same
browser tab.

The public **Simulator** is the fourth, and shows the **Price & Time** view
only: the what-if chart of how the position's value moves with the underlying
price and the days ahead, and four position tiles — Delta and Theta are left
out, since they would publish the position's greeks. It opens on the position
the Calculator handed over, or a visitor can load a symbol and build one.
Nothing is kept between visits: the hand-off lives in that browser tab only,
for up to an hour.

Both run their chain loads, extra expirations, ratings and Simulator snapshots
08:40–15:00 CT on trading days, inside the shared daily budget above, and each
visitor may make 60 of those an hour. Pricing a loaded position spends no
Schwab call, runs at any time and allows 600 an hour. Their limits are in
**Settings → Configuration → Public Calculator and Simulator**; the daily
budget is under **Public Rescue form → Budget**, because it is one allowance
behind all three.

`https://neuralstrike.co/live.html` is a thumbnail menu of them, and the site's
**Tools** menu links the four screens a visitor can act on, and Market News.

The public **Market News** page is your Market News page — headlines, the SEC
panel and the calendar — with every control that belongs to you taken out: no
Refresh, no Watchlist only, and a ticker is a filter chip rather than a link to a
Symbol page (the public site has none). It shows only the feeds marked **public**
(Settings → Configuration → Market news → Feed switches; every shipped feed is
public), and switching one off removes its stories from the public page at the next
poll. Its impact letters are worked out again from what the public page shows, and
its dividends cover only the gamma collection list, so a ticker you added to
`[tickers] extras` never appears there. A link such as
`live.neuralstrike.co/news?symbol=NVDA` opens it filtered to one ticker. It
writes nothing: a visitor cannot make the service fetch anything. The public
**Desk**'s headlines strip reads the same public-only list.

Three things to know:

- **They are not redacted.** The Desk shows your open paper positions and captured signals,
  the Opportunity Board ranks signals, and Flow Alerts carries live alerts.
  Anyone with the address can read them. That is a deliberate choice — the book
  is paper only — but it is worth knowing before you show someone the link.
- **They are a separate program.** Nothing anyone does there can reach your own
  app: it holds no login and can write only three kinds of thing — a request
  for a public Strategy Finder scan, a request from the public Rescue form, and
  a request from the public Calculator or Simulator — which the options service
  answers separately from your own work. Its
  health has its own card on **System Status**; a red one means the public site
  is down and your own screens are unaffected.
- **To stop publishing**, use **Stop All Services** — it stops both web apps —
  or, if you want to keep working, `systemctl --user stop trading-prod-webgui_live`
  on the machine, which leaves the rest of the stack alone.

The thumbnails on the menu page are refreshed on a **15-minute timer**, on trading
days between 15:25 and 15:50 Central, so each tile shows the end of the session
rather than the moment you open the menu. The screen behind it is always live.

## What runs behind the scenes

You don't interact with these directly, but it helps to know they exist:

- **Schwab gateway (proxy)** — handles the Schwab connection and market data.
  Everything else depends on it. **It must be running first** (the launcher
  handles ordering for you).
- **Six domain services** — Sentiment, Options, Portfolio, Trade, Market and
  News. Each one powers its matching page(s).
- **The public live screens** — a second, read-only copy of the web app serving
  `live.neuralstrike.co`. See *The public live screens* above.
- **Redis** — a local data backbone the services and the web app share.

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

Use **Stop All Services** at the foot of the rail — it asks for your
authenticator code before it will do anything — or run
`systemctl --user stop trading-prod.target`. This stops the
gateway, the six services, the web app **and the public live screens** — so the
public site goes dark until you start the stack again. (Redis is intentionally
left running — it is a *system* service the app's own units cannot reach.)

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

### What every data screen looks like

The screens that show market data share one layout, so you can read a page you
have never opened before:

- **One title line.** The page name on the left; on the right, when the page was
  last known to be current, and then its buttons. The main action — Run scan,
  Load, Find trades — is always the rightmost one.
- **The "Updated" time is not a clock.** It is the moment the service behind the
  page last confirmed that reading was current, always in **Central time**. A
  page that has published nothing says *Waiting for data* rather than inventing a
  time, and the pages that are meant to update on a schedule turn the stamp
  **amber** when they fall behind. A page that only updates when you ask it to
  never claims to be stale, because its age says nothing.
- **A line of counts** under the title — how many rows, which session, what the
  headline reading is.
- **One spinner.** When you press Refresh the area that is about to change dims
  and shows what it is doing. It stays until the new reading lands.
- **Anything destructive asks first**, in a dialog where Cancel comes before the
  red button.
- **Messages tell you the outcome**, not that a request was sent — the spinner
  already says that.

Charts, heat maps, gauges and every colour that carries a *reading* are
unchanged by this: green still means what it meant, and a hot tile is still hot.

## Navigation groups

The left edge is a narrow **icon rail** that widens when you hover it. Clicking a
**group** opens its first page and shows that group's pages as a **tab strip**
across the top; the other rail entries are **standalone pages** with no tab strip.

The rail is organised into **three captioned sections**, each answering one
question, plus a block of machine controls pinned to the bottom. Above the
captions sit the two entry points: **Desk** (the home page — what is happening)
and **Symbol** (one screen about one ticker).

**MARKETS — what is the market doing?**

| Rail item | Pages |
|-----------|-------|
| **Dealer Positioning** (standalone) | — |
| **Opportunity Board** (standalone) | — |
| **Flow Alerts** (standalone) | — |
| **Market News** (standalone) | — |
| **Trend & Sentiment** (group) | Market Dashboard · Sentiment · Sector & Industry · Sector Rotation · RRG · Momentum |

**STRATEGY — what should I trade?**

| Rail item | Pages |
|-----------|-------|
| **Strategy Tools** (group) | Calculator · Simulator |
| **Options** (group) | Market Scanner · Income · Expected Move · Captured Signals · Paper Ledger · Paper Account · Shares · Rescue |
| **Strategy Finder** (standalone) | — |
| **Trade Analyzer** (group) | Analyze · Rank Board |

The autonomous Claude paper trader (*Claude Trades*) was removed on 2026-09-22.

**ACCOUNT — what do I own?**

| Rail item | Pages |
|-----------|-------|
| **Portfolio** (standalone) | — |
| **More** (group) | EOD Report · User Manuals |

**System controls** sit at the foot of the rail, below a separator: **System
Status**, **Settings**, a red-outlined **Stop All Services** button, and **Sign
out**. They are kept apart because none of them is a step in a trading workflow.
Sign out is last on purpose: on a phone the bottom edge is the easiest thing to
hit, so the slot goes to the control that costs nothing if you hit it by mistake,
and the destructive one sits above it.

Two groupings are worth explaining because they are deliberate:

- **Dealer Positioning, Opportunity Board, Flow Alerts and Market News sit under
  MARKETS, not under Options.** They are market-*wide* reads. The Options group is the
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

It also shows red **count badges** on two nav items:

- **Scanner** — number of brand-new signals.
- **Captured Signals** — new captures.

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
  DEX / Vanna / Flow / Net Prem / Term**, the Simulator's **Price & Time / Volatility /
  History**, and the Scanner's **0-DTE / Swing / Directional**). Hover an individual
  sub-tab and a one-line tip explains what that specific view shows — so you can
  learn what "Charm" or "Vanna" means without leaving the page.
- **User Manuals** — a tab in the **More** group. It opens this User Guide, the
  **Reference Guide**, the Technical and API references, and the **Options
  Glossary** in your browser.

> **If you want to understand *why* a page exists rather than how to operate it,
> read the Reference Guide.** This User Guide is task-oriented — it tells you what
> to click. The Reference Guide covers every tab and sub-tab in depth: what its
> data is, how to read each panel and column, what edge it gives (and where it is
> weak), and when in the day to reach for it. It opens with a one-page summary of
> the whole app.

---

## Desk

**Route:** `/desk` — and the app's **home page**, so plain
`http://127.0.0.1:8500` lands here. In the rail it is pinned above the section
captions, with **Symbol** directly beneath it.

One screen aggregating the most useful element of every other page, laid out as
the four questions you ask in order: *what is the market doing · where is the
structure · what should I act on · what am I holding.*

**The panels, top to bottom:**

| Panel | What it gives you |
|-------|-------------------|
| **Top strip** | Clock, two dials showing **Day / Week / Month** for sentiment and trend, then **Bias** (Long / Neutral / Cautious / Short), **Signal** (Strong Bull … Strong Bear) and the **market regime** word |
| **Dealer Positioning** | One row each for **$SPX, SPY, QQQ, $NDX** — price, gamma flip and distance to it, call and put walls, net gamma, and a pins-or-runs chip |
| **Opportunity Board** | The five hottest names, with implied volatility and whether it is rising or falling, and a setup tag |
| **Live Flow Alerts** | The five newest unusual-options events |
| **Positions** | Your paper trades and captured signals together, with live marks and an **OK / Watch / At risk / Rescue** flag |
| **Headlines** | The five newest stories from Market News, one line each — time (Central), feed, and the headline, which opens the article in a new tab. **All headlines →** opens Market News. Full width |
| **Market Summary** | Up to five highlights from the latest published market report, with which report they came from and a link to the full report, over six live chips (Sentiment, Trend, Bias, Signal, Regime, Bull/Bear) — full width, at the bottom |

**Hover Bias, Signal or the market regime word** and a sentence explains what it
means and, for Bias, what position size it implies.

**Clicking any row** opens the page it came from, already set to that symbol — a
dealer row opens Dealer Positioning on that symbol, a position opens the Paper
Ledger or Captured Signals. (A headline row is the exception: its headline opens
the article itself.)

**Nothing on this page can place or change a trade.** It reads and links only.

**It announces arrivals out loud.** When a new flow alert or a newly-opened position
appears, the Desk speaks it and the row itself **glows for ten seconds**, so your eye
lands where the voice pointed. Tickers are spelled letter by letter, squawk-box style.
If several arrive together it names the newest and counts the rest ("plus 5 more")
rather than reading out a list.

**It names the contract when there is one.** An unusual-activity or big-delta alert is
about a specific option, so the announcement says which — *"N D X. Unusual activity,
0-D T E 7 15 Put."* Numbers are spoken the way they are said at a desk rather than the
way a computer reads them: 715 is "seven fifteen", 4500 is "forty-five hundred", 207.5
is "two oh seven point five". A crossover or a gamma flip is a fact about the whole
book with no contract to name, so it stays short: *"S P Y. Crossover alert, calls
over."* A new position adds its strikes, expiry and entry price, and says **credit or
debit** out loud — *"S P Y. New position, put credit spread. 2 07. point 5, 2 05,
8 - 31, entry 56 cent credit."*

A position that only changes **flag** — OK to At risk to Rescue — glows amber and
stays **silent**. It was already in the book, and the flag column has already told
you.

A symbol that **joins the Opportunity Board** is announced as well — *"N V D A.
Joins the Opportunity Board, buy signal."* One that dropped off in the last half
hour glows when it comes back but stays silent, so two names swapping places at
the bottom of the board do not talk on every refresh.

Switch it off, change the voice or set its volume under **Settings → Spoken alerts
(Desk)**. Each section — **Opportunity Board**, **Live Flow Alerts**, **Positions**
— also has its own switch there, so you can keep one talking and silence another; a
silenced section still glows. It obeys the same *only during market hours* setting
as the scanner chime.

**Four things that will look like faults and are not:**

- **After the close the walls vanish and the panel greys out with a timestamp.**
  Index option open interest reads 0 overnight, which would otherwise produce
  confident-looking walls that are pure noise. The panel is telling you it is
  showing the last reading it trusts.
- **Flow alerts say "call" or "put", never "bought" or "sold".** Schwab publishes
  no time-and-sales tape, so nobody — including this app — can honestly tell you
  which side initiated.
- **The top strip shows no prices at all.** SPX and QQQ sit in the panel directly
  below with more context; showing them twice from two separately-updating sources
  could briefly display two different prices for the same symbol. (VIX used to be
  the one exception; Bias and Signal took its tile on 2026-08-24.)
- **Bias and Signal can look like the same word said twice.** They are two reads of
  one number: Bias is how to be *positioned*, Signal is how *strong* the reading is.
  A dash in either means the sentiment service has not published — not "Neutral".
- **The Desk goes silent on a fresh tab, and an *Enable spoken alerts* button
  appears.** Browsers refuse to play audio until you have interacted with the page,
  and they refuse silently — nothing is logged and no error is shown. That button is
  the app telling you it was blocked. One click unlocks sound for the session; any
  other click on the page unlocks it too, the button just says so.

**Market Summary** is the frame across the bottom. It shows the highlights of the
latest published **NeuralStrike market report** — the report the website publishes
five times a trading day — as up to five bullet points, each one of the report's
own section headlines in report order. Above them a line names the report, for
example "Market close report · 14 Sep · 16:20 CT", and **Read the full report**
opens the whole report on the website in a new tab. The highlights are quoted from
the report as written; no AI rewrites them. Three things about the frame are easy
to misread:

- **It changes when a new report is published, not on a schedule.** Between
  reports the bullets stay put, so check the report line for their age. The six
  chips underneath are **live** regardless: SENTIMENT, TREND, BIAS, SIGNAL, REGIME
  and BULL/BEAR update on every poll even while the report above them is hours
  old, and hovering any of them explains that word — the same hover as the top
  strip.
- **If a new report can't be read, the previous highlights stay up** rather than
  the frame going blank.
- **Before any report has been published** the frame reads "No market report
  published yet." instead of a blank space.

---

## Symbol

**Route:** `/symbol` — or `/symbol?symbol=MU` to open one name directly, which
makes a dossier linkable and bookmarkable. In the rail it sits under the Desk,
above the section captions: the Desk answers *what is happening*, Symbol answers
*tell me about this one name*.

**How to use it:** type a ticker in the box at the top left and press **Enter**
(or tab out of the box). Letters, digits, `$` and `.` are accepted — `MU`,
`BRK.B`, `$SPX` all work. The page then fills six bands, each ending in a link
to the page that owns those facts:

| Band | What it gives you | Link |
|------|-------------------|------|
| **Structure** | A bar from the put wall to the call wall with spot and the gamma flip marked on it, which side of the flip price sits on and by how much, net gamma and the pins-or-runs word | Dealer Positioning, already set to this symbol — shown only for a name the app already collects |
| **Volatility** | **Vol Rank** as a bar, **IV vs HV** with its word (*high* at 1.2× or more, *low* at 0.9× or less, otherwise *mid*), ATM implied vol and whether it is rising or falling, and the one-standard-deviation **expected move** for a day and a week | Expected Move |
| **Context** | The market regime word, the name's sector and industry, its quadrant on the Bull / Bear map and its rank there (with last session's rank), and the next **earnings** date with how many days away it is | Bull / Bear Map |
| **Today** | Two columns. **Signals**: this name's rows from today's Market Scanner, each with the time it was first seen and how many scans it has survived, its score trend, and a small line of the score across the day — plus one line per setup, such as *Live since 09:15 · 1 gap*. **Flow alerts**: this name's alerts, newest first | Market Scanner · Flow Alerts |
| **In the news** | The eight newest Market News items tagged with this ticker — time, feed and headline, which opens the article in a new tab. A story that never writes the ticker is not tagged, so an empty band is not proof of no news | Market News |
| **Your position** | Anything open in this name in the paper account, the paper ledger or captured signals, with the rescue flag where the book carries one | Paper Ledger · Rescue |

**The chip beside the price says where the numbers came from:**

- **SCANNED 09:30** — the app scans this name all day, so every band is filled
  from what it already has, and nothing is fetched. The time is the last scan's,
  so after the close you can see how old the Vol Rank and IV vs HV are.
- **COLLECTED** — the app tracks this name's dealer structure (the `$VIX` and
  sector-ETF kind) but does not scan it, so Vol Rank, IV vs HV and earnings are
  looked up on demand. After a look-up the chip reads **COLLECTED · FETCHED
  14:32**.
- **FETCHED 14:32** — the app did not know this name at all, so it looked
  everything up on demand at that time.
- **NOT FOUND** — Schwab answered and has no quote for that ticker. Check the
  spelling.
- **FETCH FAILED** — Schwab could not be reached. The ticker may be fine; press
  **Refresh** in a minute.
- **QUEUED** — the look-up has not answered after 30 seconds, usually because the
  options service is busy with other work (a whole-chain Strategy Finder scan can
  take 30–40 seconds). The request is still in line; the bands fill in when it
  answers. Pressing Refresh here would only queue a second paid look-up behind the
  first.

**Finding trades on this name.** The **Find trades** button beside Refresh opens the
**Strategy Finder** with this symbol filled in and starts its scan at once, so you go
from reading about a ticker to ranked, paper-tradeable candidates in one click. It
appears only once the page has a real quote — scanned, collected, or a look-up that
came back with a price — and stays hidden while a look-up is pending, for **NOT
FOUND**, and for **FETCH FAILED**, since the scan could not work on any of those.
The scan is the Finder's, with the Finder's cost: a large chain can take half a
minute, and a chain listing more than 30 expirations asks which to load first.

⚠ **An on-demand look-up costs 4–5 Schwab calls** from the same allowance the
live dealer charts depend on. So the page looks a name up **only** when you open
a symbol it does not already cover, or when you press **Refresh** — never on its
own timer. A second visit within 15 minutes reuses the previous look-up and costs
nothing. Pressing **Refresh** within a minute of the last look-up fetches nothing
either — the page says the reading is already current — unless that look-up failed,
which is always retried. For a **scanned** symbol Refresh just re-reads
what the app already has.

**Walls after the close.** For a name the app collects, the walls disappear once the
dealer collector stops for the day, and the band says why. Walls from an on-demand
look-up stay, marked *walls fetched 14:32*. If net gamma reads exactly zero — what an
empty after-hours chain looks like — the walls are withheld either way.

**Three kinds of empty are worded differently on purpose.** *No data yet — the
options feed hasn't published this session* means the service has not produced
anything; *No signals for MU today* means everything is working and there is
simply nothing to show; *No quote for XYZQ — check the symbol* means the ticker
itself is the problem.

⚠ **Earnings "not covered" is not "none scheduled".** The first means the
earnings calendar has no data for this name, so a report could still be coming;
the second means the name is covered and nothing is on the calendar.

**Nothing on this page can place or change a trade.** It reads, looks up and
links.

---

# MARKETS — what is the market doing?

The five rail entries in this section establish the conditions a trade would be
taken in. Nothing here proposes a trade.

## Dealer Positioning

**Route:** `/options/gamma`.

A dealer-positioning view built from the options chain — gamma exposure (GEX) and
related measures.

**Controls:**

- A **Symbol** dropdown (default `$SPX`; the list is the collected watchlist).
- **Refresh**, an **Explain** button, an **Analyze** button and a **Briefings**
  menu, all on the page's header line, plus a **Next refresh** countdown in the
  strip under the charts. Explain, Analyze and Briefings' items each open their
  result in a new browser tab; while one is building, its button spins and stays
  disabled, so a second click cannot start the work twice.
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

> **Analyze calls the Claude API**, so it costs money per run. **The four automatic
> briefings run on your Claude subscription** through Claude Code on the server, and
> fall back to the API only when that fails. The running count of billed API calls is
> on the **Settings** page under *API usage*.

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

**Click a symbol** (it carries a dotted underline) to open its [Symbol](#symbol)
dossier at `/symbol?symbol=<ticker>`. The public copy of this board on
live.neuralstrike.co has no dossier, so there the symbol is plain text.

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

## Market News

**Route:** `/news`. A rail item under **MARKETS**, directly below Flow Alerts.

Three panels: the **headlines** on the left, the **SEC / EDGAR** panel (filings and
insider buys) top right, and the **economic calendar** below it. On a phone they
stack in that order. Every time is Central.

- **Each headline is one line**: when it was published (an older item shows its
  date), an **impact** letter, up to two tickers (a **+N** chip holds the rest —
  hover it for the list), the headline, and a small badge naming each feed that ran
  it. On a narrow screen the feed badges are dropped so the headline keeps its room.
- **Impact (H / M / L)** is a rules-based rank the service gives every item: keyword
  tiers (*FOMC*, *CPI*, *merger*, *bankruptcy* … count most; *downgrade*,
  *earnings*, *tariff* … less; *outlook*, *analyst* … least — each tier counts
  once), points for the feed (the Federal Reserve most), a bonus when two or more
  feeds ran the story or when it is tagged with a ticker you follow, the size of an
  insider buy (more for an officer or director), and the type of an offering filing.
  A score of **6** or more is High, **3** or more Med, anything lower Low. A High
  older than **24 hours** shows as **Med**. Hover the letter to read the rules that
  scored it. Every threshold, keyword and point value is in **Settings →
  Configuration → Market news → Impact** (and the *Impact keywords*, *by feed*,
  *insider buys* and *SEC filings* sections below it).
- **Sources** are public news feeds (MarketWatch, CNBC, Yahoo Finance, the
  press-release wires and others). A story several feeds carried shows each feed's
  badge.
- **A ticker is tagged only when the headline names it explicitly** — a cashtag
  like `$NVDA` or a bracket like `(NASDAQ: NVDA)` — or when it came from Yahoo
  Finance's page for that ticker, and only for tickers the app follows (the
  Watchlist only set). A company name alone never tags, so a ticker filter can miss
  a story about that company.
- **Click a ticker** to open its Symbol page. **Click a headline** to read it on the
  publisher's site, in a new tab.
- **Trending** chips count the tickers named in headlines from the general feeds
  over the last 6 hours (`[trending] window_h` in `config/news.toml`). Yahoo
  Finance's per-ticker stories are left out, because they carry the ticker they
  were fetched for rather than one the headline named. Click one to show only its
  news; click it again to clear.
- Filter by **Sources**, **Ticker**, **Watchlist only** (the symbols the app
  collects gamma for, plus `[tickers] extras`) or **Impact** (All · High · High +
  Med); choosing no source means every source. The filters apply to the headlines
  only. **Show more** pages further down the list.
- **The SEC / EDGAR panel** lists insider **purchases** filed on Form 4 and new share
  offerings (S-1, S-3, 424B5, S-3ASR) in three columns: **Date/Time**, **Symbol**
  and **Headline/Details** — the impact letter, the title, then for an insider buy
  how many purchases, their total and the date, or for a filing its form. A filing
  carries the filer's own ticker whether or not the app follows it. Titles open the
  filing on **sec.gov** and nowhere else.
- **The calendar** is three groups of tiles:
  - **Economic news/Calendar** — FOMC meetings, the Beige Book, Board speeches and
    testimony from the Federal Reserve's own calendar, plus a few other scheduled
    releases (JOLTS, the Employment Cost Index).
  - **Dividend / IPO** — the ex-dividend date, amount and pay date for the tickers
    you follow (the next 30 days, and the last 3), and IPOs of $100 million or more
    from Nasdaq's calendar — upcoming with their price range, priced ones for a week
    with their price.
  - **Economic data (CPI, PPI etc)** — one tile per report: CPI, PPI, Jobs (payrolls
    and unemployment), PCE, GDP, Retail sales and Jobless claims. Each line shows
    **Actual** and **Prior**, and the tile shows when the **Next** release is due.
    For a day after a release the tile is either **released** — the new number has
    arrived and is the Actual — or reads **Awaiting the release** (Actual —) while
    the service checks for it every 2 minutes for up to an hour. A tile with no
    future date reads *Next date not yet published*.
  A muted note under a group means its sources could not be reached; it is showing
  the last good reading.
- **Refresh** checks every feed now instead of waiting for the next scheduled poll
  (every 5 minutes in market hours, 15 off-hours, 60 at weekends and holidays), and
  re-checks the calendar — though each calendar source still keeps its own schedule.
- The **Desk** shows the five newest headlines and each **Symbol** page an
  *In the news* band (headlines and SEC items); the public site has its own copy of
  this page (see *The public live screens*). Every feed, its on/off switch, whether
  the public site may show it, the poll intervals, the impact rules and the
  calendar's sources are in **Settings → Configuration → Market news**.

> A headline is not a verified fact, the impact letter is a rule count and not a
> judgement, and nothing here is a trade recommendation.

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

- **Market Sentiment ring** — a 0–10 composite of market conditions (higher =
  calmer and more supportive: quieter volatility, more call buying, broader gains;
  lower = stress). It is **not** a fear gauge. Drawn as three arcs on one
  dial: **Day**, **Week** (the last 5 sessions' average) and **Month** (the full
  history's average). A **Model confidence** figure sits beneath it.
- **Market Trend ring** — the same three horizons for direction, 0–100, where 50 is
  neutral. The Day reading carries a five-state label — **Climbing / Stalling /
  Circling / Gliding / Diving** — and a plain-English suggestion. Hover the word
  to see what it means.
- **Signals** — four tiles (Bias / Signal / Yesterday / Change) with rate-of-change
  readings and a divergence line beneath. Hover the Bias or Signal word to see which
  band of the sentiment composite it covers. A high composite means calm, supportive
  conditions, so *Bullish* describes a supportive backdrop — not fear, and not a
  promise that price will rise.
- **Regime block** — which of five regimes the tape is in (**Balanced**,
  **Trending**, **Breakout**, **Whipsaw**, **Stressed**), a confidence figure,
  diagnostic tags, and a table ranking all five by share with their change since
  the open. Trending and Breakout also carry a direction word (*Rallying*,
  *Retreating*, *Breakdown* and so on). **Hover the regime word on the dial** and
  a sentence explains what it means and what tends to work in it — the same
  hover the Desk's regime tile uses.
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
signals the app finds; and **Trade Analyzer** judges a single stock.

## Calculator

**Route:** `/options/calculator`.

An options P&L calculator for **any multi-leg structure**, with a price/time matrix.
The shared **entry panel** runs across the top — symbol, strategy, expiry strip, and
the option chain beside the legs — with the results (six metric cards over the P&L
matrix) below it. It wears its own near-black palette rather than the app-wide navy.

**The entry panel (shared with the Simulator).** Across the top of the page:

- **Symbol** — type a ticker and press **Enter** or tab out. A full-screen wait
  overlay shows while the chain loads; the pill in the title bar reads
  **AWAITING SYMBOL** → **LOADING CHAIN** → **CHAIN LOADED · SYM**, and the status
  line reports how many strikes and expiries arrived. **Refresh** re-pulls the
  same symbol for fresh quotes. There is no Load, Fetch Premiums, IV Update or
  Calculate button: once the chain lands the strategy's legs are laid on real
  strikes, every leg is priced from the chain, the volatility is implied from
  those prices (the way ThinkorSwim does), and the cards and matrix follow.
- **Strategy** — a cascading menu of templates: **Single** (long/short call/put),
  **Credit spread** and **Debit spread** (call/put), **Condor** (iron, all-call,
  all-put), **Butterfly** (call, put, iron), **Calendar** and **Diagonal** (call/put),
  and **Stock + options** (**covered call**, **protective put**, **collar** — the
  three that need shares; see the note below). Tag chips beside it say whether the
  structure takes in a **credit** or costs a **debit**, how many legs it has, and
  its lean; only the credit/debit chip is coloured. A one-line description of what
  the trade is betting on sits under the panel.
- **Expiry strip** — one button for **every** expiration the symbol lists (TSLA
  runs out past two years), with its days to go. Strikes for the nearest two load
  with the symbol; clicking any other shows **Loading strikes for …** while that one
  expiration is fetched (about a second), then moves **every option leg** to that
  date (a **stock** leg is skipped — shares do not expire) and points the chain at
  it. The status line says how many expirations have strikes loaded so far.
- **The chain** (left half) — the **complete** chain for the selected expiration:
  every strike, calls on the left, strikes in the middle, puts on the right, in a
  box that scrolls and opens **centred on spot** (it re-centres on every load and
  expiry change). The at-the-money strike is gold and in-the-money cells are
  shaded. The call side reads **Delta · OI · Volume · Bid · Ask** towards the
  strike and the put side mirrors it, so Bid and Ask always sit beside the strike.
  **Click a Bid to SELL, an Ask to BUY.** The click **moves** the leg you already
  have on that side and type — a put Bid moves your short put to that strike and
  expiration, keeping its quantity — and adds a new leg only when there is none. It
  is priced at the contract's **mark** whichever you click — the side only decides buy or sell,
  so the page does not look worse by the full bid/ask spread. **Columns** adds
  Mark, IV, Gamma, Theta or Vega (Bid and Ask cannot be removed — they are what you
  click) and remembers the choice.
- **Legs** (right half) — one row per leg: **BUY/SELL** and **CALL/PUT** (and
  **STOCK**) flip with one click; **Qty**; **Expiry**; **Strike**, a dropdown of the
  real strikes that opens on the current one, stepped one strike with **‹ ›**;
  **Price**, with a **Bid / Mark / Ask** dropdown beside it choosing which side of
  the quote the leg is priced at (Mark to start);
  and the leg's **Delta** from the chain. Changing a leg's strike, expiry or type
  re-prices it from the chain — **unless you typed its price**, which then stays
  until you press the **↺** beside it. Each leg keeps its **own expiry** (so
  calendars and diagonals price each leg on its own clock) and its own quantity (so
  a 1-2-1 butterfly body trades at 2×). **Add leg**, **Reset to template**, and a
  remove ✕ that locks at the last leg.
- **Rate my trade** (beside **Expected Move**, available once a chain is loaded and
  every leg has a strike) grades the legs with the **Strategy Finder's own scorer
  and Go/No-Go checklist** and opens a window:
  - **One word.** **BUY** when the grade is Strong or Good and the checklist is
    clear; **CAUTION** when a Strong or Good trade has cautions (or checks that
    could not run), or a Marginal trade has a clear checklist; **PASS** for
    anything Weak, anything **blocked**, or a Marginal trade with cautions.
  - **The grade and score** (Strong / Good / Marginal / Weak, 0–100) and the
    **reasons**: failed quality gates, the checklist's blocks and cautions, a note
    when premium is cheaper than the scanners allow selling, and a note when the
    legs match no template ("a custom structure — judged against the debit bars").
  - **The Trade detail panel** the scanner shows — contract, credit or debit, max
    loss, breakeven, probability, the checklist, Greeks, implied volatility and the
    score factors.
  The trade is rated at **the prices on your legs** and one structure's worth of
  contracts (a 10-lot rates the same as a 1-lot). It costs a few Schwab calls per
  click (a year of price history, the IV analysis, the earnings lookup) and nothing
  otherwise. If no answer comes within 30 seconds the window says so. ⚠ The word is
  a rule over the grade and the checklist, **not fitted to past outcomes**.
- Under the legs, a strip keeps a running **leg count**, **net premium** and **max
  loss**. A **dash** there means *not known yet* rather than zero — max loss is blank
  when the loss has no bound (a naked call), cannot be settled on one date, or the
  position holds **shares** (the cards give the real figure). **Delta** shows a dash
  whenever the chain carries no Greeks, which is normal outside regular hours.

**Pricing assumptions** (a collapsed row under the panel): **Price**, **IV %**,
**Rate %**, **IV Δ %**, **Contracts** and **Strikes** (how many real chain strikes
either side of spot the matrix spans, default 24). Editing any of them re-prices.
IV is re-implied from the chain on every load, so a typed IV lasts until the next
one.

**Stock legs (covered call, protective put, collar).** Set a leg's Type to
**stock** and it becomes shares rather than a contract:

- **Qty** counts **lots** of 100 shares — one lot is 100 shares, which is what one
  option contract covers. ⚠ Typing 100 builds a 10,000-share position and
  multiplies every figure by a hundred.
- **Price** is what you **paid** per share. Blank means "today's price" and the page
  fills it; a value you typed is never overwritten, which is the point when you are
  pricing a call against shares you already hold.
- **Strike** and **Expiry** show a dash and are disabled — shares have neither.
- ⚠ **Max risk is the stock going to zero**, and the cards now scan that far, which
  is also what shows a **protective put** or **collar** capping it.
- ⚠ **Analysis only.** The Simulator and Rescue's ad-hoc form do not offer these:
  the Simulator prices from the option chain and cannot value a share, and Rescue
  books trades into a paper account that tracks shares separately. Nothing on the
  Calculator places a trade regardless.
- ⚠ Elsewhere in the app "covered call" means the **option leg alone** (the paper
  book tracks the shares separately). Here it is the whole position — the
  **100 SHARES** chip is what tells you which.

**Outputs (a moment after you stop editing):**

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
pre-filled and priced once the chain loads. The Calculator and the Simulator work on **one shared position** — the same symbol, strategy, legs and selected expiration. Whichever page you edited last is what the other one opens with, so there is nothing to copy between them.
Open the Simulator after building a trade here and it is already loaded; change it
there and the Calculator shows the change. Your IV, rate and contracts stay on this
page. Loading a **different** symbol clears
the cards and matrix — they belonged to the old symbol; **Refresh** on the **same**
one keeps them.

## Simulator

**Route:** `/options/simulator`.

Re-prices a **multi-leg** option position under different scenarios using
Black-Scholes. The top of the page is the same **entry panel** as the Calculator:
type a **Symbol** and press Enter or tab out (**Refresh** re-pulls it), pick a
**Strategy** (the same template menu — singles, verticals, condors, butterflies,
calendars/diagonals; the three stock structures are not offered here), and build the
legs. **Click a Bid in the chain to SELL, an Ask to BUY** (the click moves the leg
already on that side, or adds one), and
use the **expiry strip** to move every leg to one date. Each leg is a row with
**BUY/SELL** and **CALL/PUT** toggles, **Qty**, **Expiry**, a **Strike** dropdown
(**‹ ›** step it) and its **Delta** from the chain — there is no price
box, because the Simulator prices every leg from volatility itself. **Add leg**, and a
remove ✕ that locks at the last leg (a position with no legs has nothing to simulate).
Every tab below operates on the **netted** position (all legs summed).

**Position tiles.** Under the panel, six tiles state the position without any
hovering: **Entry credit** (or **Entry debit**), **Max profit**, **Max loss**,
**Breakeven(s)**, **Delta** (as shares — "moves like 145 shares long") and **Theta
per day**. The entry is the model price at today's spot, not a market fill. Max
profit, max loss and breakevens are the **expiration** payoff; when the legs expire
on different dates they read "—" with that reason, because a single-date payoff
would get a calendar's back leg wrong. A tile that has nothing to show says why
("pick a strike for every leg", "waiting for a price") rather than showing a zero.

**Warnings.** An **Edited** chip appears when the legs no longer match the strategy
you picked (resizing every leg together does not count). A warning line appears when
a short leg expires after a long one ("From Sep 18 it is no longer covered") or when
more calls are sold than bought (losses unlimited if the price rises).

Three tabs, in this order:

- **Price & Time** (opens first) — a **Price change** slider (an instant price
  overlay) and a **Time passed** slider that fast-forwards from now. Time passed runs
  only as far as your position's last expiry (in quarter days when it is three days or
  less away), and **Now / Halfway / Expiry** buttons jump straight there. A line under
  the sliders reads the result, e.g. *"At 386.00 on Sep 28: profit $8,240"*. Each leg
  decays on its **own** clock, so a **calendar's** back leg correctly keeps its time
  value while the front leg expires.
- **Volatility** — a **Volatility multiplier** slider and a table comparing the
  position at today's volatility and at the multiplied volatility: position value,
  delta, gamma, theta per day and vega per volatility point, with the change. A line
  above states the result, e.g. *"If volatility rises 50%, this position loses
  $1,050."*
- **History** — re-prices the position along the underlying's recent real price
  path and shows six stacked panels: **Price**, the position's own **Profit / loss**
  (green above zero, red below — measured from the first bar, as if opened then),
  then **Delta, Gamma, Theta per day, Vega**. The axis shows real dates, and times
  are **Central** — a session's first bar reads 08:30. Drag the **scrub slider** to
  step through the bars; the line beside it states that bar's time, price, profit or
  loss and delta. It starts on the latest bar. A **Look-back** dropdown controls how
  far back the path runs (Auto by DTE, or fixed windows).

All Simulator figures are for the **whole position** (every contract, times 100),
the same basis a broker shows.

**One position with the Calculator.** The Simulator opens with the Calculator's
symbol, strategy and legs, and any change you make here is what the Calculator shows
next — there are no copy buttons. A position that holds **shares** (a covered call,
protective put or collar) is shown with its option legs only: a note says the shares
are not simulated, and they stay in the position when you edit it here. The sliders
and the open tab stay with this page.

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
- A **Checks** column — the go / no-go checklist's verdict for that row, in one
  chip (see below).
- An **Only clear** switch beside **Run scan**.
- A **Why no trade?** button left of **Run scan**, which explains where each
  symbol stopped rather than changing what the table shows (see below).
- A plain-English **VIX term** label (for example, *"VIX term: Contango (near-term
  calm) · as of 1:32 PM"*).
- Brand-new signals get a **NEW** badge.

**Right pane — the detail panel:** click any row to see its full breakdown
(credit, max loss, DTE, delta, theta, IV rank, and more) — with the full
checklist at the top.

### Using the checklist to pick a trade

Every candidate row carries a **Checks** chip: the nine things worth knowing about
that trade, boiled down to a few words.

| Chip | What it means | What to do |
| --- | --- | --- |
| **Clear · 7 of 9** | Nothing on the list wants your attention. The count is how many checks actually ran — two of the nine did not apply to this structure | Read on |
| **2 cautions** | Two checks want you to look | Click the row and read which |
| **Blocked** | The Paper Ledger would refuse this trade, and the chip says why in short (*Blocked · symbol full*) | Lower the quantity, or pick another name or expiration |
| **Partly checked** | Something the checks read has not loaded, so the verdict is incomplete | Wait a moment, or check **System Status** |
| **—** | The checks have not run on this page yet | Wait a moment |

A caution outranks an incomplete reading: a row showing *2 cautions* may still have
a line the checks could not finish, and clicking the row shows which.

**Hover a chip** and it tells you what it could not fit in the cell: *Blocked*,
*Clear* and *Partly checked* give their full wording, and a **caution chip names
the cautions themselves** — *Short 200 call is 0.5 expected moves from the price ·
Price below the gamma flip — moves can accelerate* — up to three of them, then *…*
The column does not sort: its words would sort alphabetically, which says nothing
about how clear a trade is.

**Click the row** and the detail panel lists all nine, each with a line of its own
— *Earnings Nov 19, before expiry*, *Bid-ask round trip 18% of the credit*, *Short
145 put is 0.7 expected moves from the price*. Two rules are worth knowing:

- **A check that does not apply is left out, not shown as passing.** A long call
  has no short strike, so it gets no expected-move or wall line at all. That is
  why the count says *7 of 9* rather than claiming nine passes.
- **Only the paper-book line can be red.** Everything the app treats as a hard
  rule was applied before the row reached the table; the rest are judgment, which
  is why they are amber and never a refusal.

**Only clear** hides every row that is not fully clear — blocks, cautions, and
anything the checks could not finish, including a row whose paper-book fit was
never checked. The tab counts follow it (*Swing (3 of 40)*), and when it empties a
table the table says which happened: *No row is fully clear — 40 hidden by Only
clear.*, or, when a feed has not loaded, *Every row is only partly checked — a
feed the checks read hasn't loaded. Turn off Only clear to see all 40.* Switching
it on and off re-filters what is already on screen; nothing is re-scanned.

The checks refresh themselves: whenever the paper ledger, the market regime or the
overnight calibration changes, and otherwise every five minutes against the
Opportunity Board. **A refresh never re-runs the scan** — the prices on the row are
the scan's. While a refresh is in flight the panel says *Checking…* rather than
showing a stale verdict, and if the row you have open drops out of the day's scan
the checklist says *This signal is no longer in today's scan — the details below are
as it last read* and stops judging it. Click another row to start again.

**Per-row action buttons** (also see *Cross-page actions* below):

- **Send to Calculator** — open the P&L Calculator pre-filled with this trade.
- **Send to Paper trade** — create a paper trade from this signal, if it fits the
  Paper Ledger's risk limits. The box shows each limit in green or red for the
  quantity you type before you send, and a message a moment later says whether it
  opened (see *Risk limits on new trades* under **Paper Ledger**).
- **Expected Move** — open the Expected Move chart for this trade in a new tab.

The list re-scans itself every 15 minutes between 08:00 and 15:15 CT on trading
days; you rarely need to press **Run scan**.

> **The table shows the whole day's signals, not just the current scan.** A signal
> that has stopped qualifying stays visible but is **dimmed and frozen**, stamped
> with the time it dropped out, and its **Paper** button is removed — its price is
> stale, so a paper entry from it would be fictional. The status line's "N live
> signals" counts only those still qualifying.

**How long has it been there, and is it getting better?** Two columns beside
**Dropped at** answer that, on all three tabs:

- **Seen since** — when the setup first appeared today and how many scans it has
  been live in, e.g. *09:15 · 14x*. A *setup* is the symbol, strategy and
  expiration: when the exact strikes shift a notch as the price moves, the age
  carries on rather than starting again. A dash means the app cannot vouch for the
  start time — not that the signal is new.
- **Score trend** — how the setup's best score has moved over the last hour:
  *▲ +4.2* rising, *▼ −6.1* fading, *▬ +1.0* steady (within two points), or *new*
  until it has four scans behind it.

A setup that has held all morning with a steady or rising score is a different thing
from one that appeared in a single scan. Neither column filters or sorts anything.

> **An empty Directional tab is normal.** The engine only emits candidates scoring
> 50 or better, so empty means nothing cleared the bar rather than something
> failed. Index names (`$SPX`, `SPY`, `QQQ`) are also frequently absent, because
> their implied volatility is usually too low to clear the credit floor.

### "Why was there no trade on X today?"

The tables answer *what qualified*. **Why no trade?** — the small button just left
of **Run scan** — answers the other half: where each symbol stopped in the last
scan.

1. Click **Why no trade?**. The box reads *Reading the last scan…* for a moment,
   then fills.
2. The row of chips at the top says how many symbols each window left empty —
   *0-DTE · 1 of 4 produced nothing*, *Swing · 0 of 4 produced nothing*. That is
   the shape of the day before you look at any one name.
3. Pick your symbol from the **Symbol** box (you can type into it).
4. Read the three cards — one for **0-DTE**, one for **Swing**, one for
   **Directional**.

Each card opens with one sentence, and that sentence is the answer:

> *SPY · Swing: 38 short strikes were priced, and every one sat past the
> short-delta ceiling.*

Under it is the list of steps that window ran, with how many candidates were still
alive after each — *In the delta band 38 · Priced 38 · Under the delta ceiling 0*.
**The highlighted row is where it stopped**; everything below it is zero for that
reason and nothing else. When the symbol *did* produce trades the headline simply
says so — *SPY · Swing: 8 signals reached the board* — and the list shows how the
count came down to them.

Some cards have no list at all, and that is deliberate — **the app will not print a
column of zeros for something it never measured.** Instead the card says what
happened:

| What the card says | What it means |
| --- | --- |
| *Schwab returned no quote for this symbol.* | The scan stopped before it asked for a chain. Nothing about this symbol's options was looked at |
| *The scan could not read an options chain for this window.* | No chain came back for those expirations |
| *The chain for this window carried no underlying price, so nothing could be measured against it.* | The expirations were listed, but the chain quoted no price for the stock itself — common off-hours. Every distance and delta test needs that price |
| *This symbol was not in the last scan.* | The watchlist moved, or the scan has not reached it |
| *The scan recorded no account of this window for this symbol.* | Nothing usable was recorded for that window |
| *the single-leg build failed for this symbol; the scan logged the error* | The Directional pass crashed on this name. It is a fault, not a market condition — check the log |

**Reading a spread card.** The first six steps are about the short strike (is it in
the delta band, does it have a price, is it under the delta ceiling, is it inside
the expected move, is it liquid, and did a width get built around it); the rest are
about the spreads that came out of those (the momentum veto, the per-symbol cap,
the regime filter, the volatility floor, the dealer-gamma gate) and finally how
many reached the board. Two of those counts can go **up**, which is not an error:
iron condors are *built from* the survivors rather than being survivors, and a
later pass can add trades of its own.

**A card carrying the line *From an earlier scan.*** is telling you the account
you are reading was written by an older scan than the signals in the table —
reopen the box after the next scan. The box reads the funnel when you **open**
it, so picking a different symbol is instant but refreshes nothing; close and
reopen to re-read.

> **Two absences that look alike.** *No signals* and *nothing was measured* are
> different answers, and this box is the only place that distinguishes them. A
> symbol with a full stage list genuinely lost its candidates to a market
> condition; a symbol with a sentence and no list never got that far.

## Income

**Route:** `/options/income`.

A board of premium worth **selling** 30 to 45 days out, ranked across the whole
watchlist. The scan runs once each morning on its own schedule — there is no
Refresh — and the status line tells you how many symbols it covered, when it ran,
and whether any of them failed.

Three structures share the board:

- **Put spread** — a put credit spread, for a symbol you do not expect to fall much.
- **Call spread** — a call credit spread, for one you do not expect to rise much.
- **Cash-secured put** — a single short put, for a symbol you would be content to own
  at that strike.
- **Covered call** — a call written against stock the paper account already holds
  (see **Shares**), never struck below what the shares cost.

**The columns:** Symbol · Side · Strikes · Expiry · DTE · **Credit $** · **Capital $**
· **Return on capital** · **Yield on cost** · **Total return if called** · PoP % ·
Breakeven · **Earnings** · Score. Click any column to re-sort.

**Yield on cost** and **Total return if called** apply to covered calls only, and the
other three structures show a dash — they own no shares, so there is no cost to
measure against. Yield on cost is the premium alone as a percentage of what the shares
cost you. Total return if called adds the gain up to the strike, which is what you
actually collect if the stock is called away — and it is the number that decides
between a fat premium at a strike barely above your basis and a thin one well above
it. (It reads almost the same as Return on capital on these rows, but not quite:
Return on capital is after the commission, and it is the only one of the two the
spreads and the cash-secured put have at all.)

**Credit and Capital are both per contract, in dollars.** Capital is the cash the
trade actually commits — for a spread that is its width less the credit; for a
cash-secured put it is the strike all the way down to zero, which is far larger.
**Return on capital** is the credit measured against that, and it is the only column
that makes the two comparable: a $60 credit and a $640 credit say nothing until you
know that one risks $441 and the other $39,361.

**The Earnings column** reports what the earnings calendar knows about the symbol:

- **None scheduled** — checked, and nothing is coming.
- **After expiry** — a report is scheduled, but it lands after this expiration.
  Anything reporting *before* expiration was already removed from the scan.
- **Not checked** — the calendar has no entry for that symbol, so the check could not
  run. This means *unknown*, not *clear*. Without an Alpha Vantage API key configured
  it is what every row will say.

An empty board is a normal outcome, not a fault — the status line says how many
symbols were scanned so you can tell "nothing qualified today" from "the scan never
ran".

### Opening one in the paper account

A **cash-secured put** and a **covered call** carry a wallet button at the end of
their row. It opens that trade in the **paper account** — the book with cash and
share lots behind it, which is the one an assignment can turn into stock. The two
credit spreads do not have the button: their route is **Send to Paper trade** on
the Market Scanner, which writes the paper *ledger*, a separate book that tracks
marks rather than cash.

Press it, confirm the number of contracts, and the account answers in a moment —
either a confirmation, or a refusal saying exactly what stopped it. It will refuse
when:

- the account cannot secure the put (the message names the collateral needed and
  the cash you have);
- there is no share lot behind a covered call, or the lot was already called away
  since this morning's scan;
- a covered call would not cover the lot **whole** — 300 shares is three contracts,
  not one, because the book delivers a lot in one piece (the message names the
  number that works);
- a covered call is already open on that symbol — the book records coverage per
  symbol, so it cannot tell a second one apart from the first;
- the price has moved more than 15% from what the board shows, which after a
  morning scan is common enough to be worth checking rather than filling;
- there is no live quote for the contract at all, or the account is halted for the
  session.

**You are filled at the live price, not the board's.** The board was scanned this
morning; the number you see is what ranked the row, and the number you get is what
the contract is worth when you press the button.

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

A manual paper-trading ledger. You open every row yourself; since 2026-09-12 the
**long options and debit spreads** in it also close themselves (see below), and
since 2026-09-13 so do butterflies and condors.

- **Action buttons:** Reload, **Close selected**, **Analyze selected** (live
  Greeks + P&L overlay), **Delete selected**, **Delete all closed**.
- ⚠ **Close selected asks for a different number depending on the trade.** For a
  credit spread it is the **debit you pay** to close; for a long option or debit
  spread it is the **credit you receive**. The dialog's label says which. Both are
  **per spread** (i.e. per share — type `2.00`, not `200`).
- **Table:** Trade ID, Symbol, Strategy, Strikes, Expiration, Qty, Entry Credit,
  Max Loss, P&L, Status, Entry Time.
- Click a row to load its **detail panel**; the app automatically runs a live
  analysis and overlays current Greeks and P&L.
- Each row also has an **Expected Move** button.

**Risk limits on new trades.** A trade sent with **Send to Paper trade** (Market
Scanner) or **Paper** (Strategy Finder) is checked against this ledger's own open
trades before it is written. If it would break a limit, nothing is written and a
message says why. The limits:

| Limit | Level |
| --- | --- |
| Per trade | at most **$750** of max loss |
| Per symbol | **3** open positions and **$750** of max loss |
| Per sector | **5** open positions and **$1,500** of max loss (the index products — $SPX, SPY, QQQ, $NDX, DIA, IWM — share one group) |
| Per expiration | **5** open positions expiring the same day, across every symbol |
| Whole ledger | open max loss at most **20%** of equity — $25,000 plus the realized P&L of your closed trades |

**The Paper trade box shows the limits before you send.** It opens with a heading
such as *Paper trade ORCL Credit spread — put · 2026-10-16*, the trade's risk (*Risk
$190 per contract*), and one line for each limit, worked out for the quantity you
type:

- **green** — this trade fits that limit: *Position 2 of 3 in ORCL*, *ORCL risk
  would reach $380 of $750*;
- **red** — this quantity breaks it: *Risks $950, over the $750 per-trade limit*,
  *ORCL already holds 3 of 3 positions*;
- **grey** — *Not checked*, with the reason.

When a line is red, **Create** is greyed out and the box says *Up to N contracts
fit.*, or *No quantity fits the paper ledger's limits right now.* When you leave the
**Quantity** box, a number above the largest quantity that fits drops back to it (to
1 when nothing fits). The box also stops at 100 contracts, the most one trade can
open, and **Create** is greyed out when the quantity is not a whole number of at
least 1.

The lines are a preview. The book can change between opening the box and pressing
**Create**, so the paper ledger checks every limit again when the trade arrives —
the message below is the final answer. If the box says *Can't preview this trade
here — the paper ledger still checks every cap when you create it.*, **Create**
still works and the ledger decides. Pressing **Create** shows *Sent 2 contracts — the
paper ledger answers in a moment.*; if instead it says *Could not reach the options
service — the trade was not sent.*, nothing was sent and you can press **Create**
again.

A second or so after you press **Create** in the quantity box, a message answers the click:

- *Paper ledger: opened 2 × SPY Credit spread — put.*
- *Paper ledger: not opened — risks $900, over the $750 per-trade limit. Up to 1
  contract fits.* — **Up to N contracts fit** is the largest quantity of the same
  trade that would clear every limit right now; send again with that number. It is
  left off when not even one contract fits.
- *Paper ledger: not opened — ORCL already holds 3 of 3 positions.* — close a
  position in that symbol, sector or expiration first.

⚠ Equity counts your closed trades' realized P&L, so **Delete all closed** also
changes the 20% limit. A $750 trade by itself uses the whole $750 allowance for its
symbol. The limits apply to this ledger only — the Paper Account's automatic
engine has its own, also with a $750 per-trade limit.

**Automatic exits (long options, debit spreads, butterflies and condors only).** Checked **hourly,
09:00–14:00 CT** on the same run as the paper account — so a target reached at
09:15 is acted on at 10:00, or immediately if you press **Run manage cycle** on
Paper Account.

- **Profit target, +50%** — of the trade's **maximum profit** for a debit spread,
  of **what you paid** for a single long option, which has no maximum profit to
  take a share of. On a $2.00 spread over $5 strikes those are +$150 and +$100.
- **Time exit at 21 days to expiry**, up or down. ⚠ A position that already had
  21 days or fewer when you opened it is **not** time-exited — that is everything
  from the Market Scanner's **Directional** tab, which scans 0–15 days out. Those
  ride on their target and on expiry.
- **Butterflies and condors** (from the Strategy Finder) take the same +50% target,
  measured against their maximum profit, but have **no time exit** at all. They
  gain most of their value in the last two weeks, so a 21-day exit would close
  them near break-even. A butterfly's middle strike shows as `S 2×100C`.
- **No automatic loss stop.** The research this follows closes debit spreads out
  before expiry rather than stopping them; your risk is capped at the premium paid
  regardless. Close any row by hand whenever you like.
- **Credit spreads here are tracked, not managed** — only expiry settles them.
  The engine's own book on **Paper Account** is the one that takes profit and cuts.

> ⚠ These levels come from published practitioner guidance, **not** from this
> book's own results: no long option or debit spread has ever been recorded closed
> in this app. They live in a config file so they can be changed once there is
> something to measure.

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
> is acted on at 10:00 unless you press **Run manage cycle** yourself.

## Shares

**Route:** `/options/shares`.

The **stock** the paper account holds. Options normally expire worthless or are closed;
a **cash-secured put** that finishes below its strike does neither — it is exercised
against you and becomes 100 shares per contract, bought at the strike. Every such lot
appears here.

**The columns:** Symbol · Shares · **Cost basis $/share** · **Cost $** ·
**Mark (not tracked)** · Unrealized $ · **How acquired** · Held since ·
**Covering call**. Click any column to re-sort.

**How acquired** says where the lot came from. *Assigned* means a short put was
exercised against you — which is how nearly every lot arrives — and *Bought* means it
was entered by hand. The two are not interchangeable: an assigned lot's cost basis is
the strike you sold, which may be well above what the shares were worth when they
landed.

**Mark and Unrealized are deliberately blank.** Nothing in this app re-prices a bare
share, so there is no current value to report, and printing the cost basis in the Mark
column would look like a live quote. To see what a holding is worth right now, look the
symbol up on **Market Dashboard** or in your broker.

**Covering call** shows the call already written against that symbol, as strike and
expiry — for example `210c 10/16`, with `×2` if more than one contract. A blank cell
means the shares are uncovered: all the upside is yours and no premium is being
collected. A call *spread* on the same symbol is not a covering call and is not shown
here. The match is by **symbol, not by lot** — the book keeps no record of which
shares a call was written against — so if you hold two lots of one name and have
written one call, that call appears on both rows.

There is nothing to press. Lots appear when the engine settles an in-the-money short
put, and disappear when a covered call written against them finishes **above** its
strike — the shares are called away at that strike, and the cash comes back with the
gain booked as realised P&L. (A call that finishes at or below its strike expires
worthless: you keep the premium and the shares stay.) There is no way to sell a lot
by hand; being called away is the only exit the book has.

What lots are *for* is the **Income** tab, which screens covered calls against them
and never offers a strike below their cost basis.

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
| **Ad-hoc Trade** | Enter a position **by hand** and get the same ranked repair menu for it — use this to evaluate a spread the app is not tracking. The Expiry list holds **every expiration the symbol has**, so you can price a roll well out in time; strikes for an expiry past the first two load when you pick it. |

**The at-risk table** (top of the page) lists every paper position and captured
signal the system has flagged as **tested** or **critical**, heat-colored and sorted
by **heat** (a 0–100 danger score — higher is more urgent). A position earns its
heat from how close the underlying is to the short strike, the short-leg delta, P&L
versus the credit taken in, days to expiration, and dealer-gamma / market-regime
context. Detection rides the paper account's hourly manage cycle (09:00–14:00 CT, or
whenever you press **Run manage cycle**), so the table stays current on its own.

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

## Strategy Finder

**Route:** `/options/swing`. Its own row on the main menu, directly under the
Options group — it has no tab strip.

A focused, on-demand scan for one symbol. It builds every strategy it can for that
symbol — seven groups, always all of them — on **every expiration** in your range,
across the whole option chain, and ranks them on one score. The page reads top to
bottom: the scan bar, a summary of the scan, strategy chips, up to four
top picks, and the full ranked list.

**The scan bar**

- **Symbol** — type a ticker and press **Scan**. Enter, or tabbing out of the box,
  scans too.
- **Expiry** — six presets set the **DTE min / max** boxes (days to expiration)
  beside them:

  | Preset | DTE min – max |
  | --- | --- |
  | **1–2 wk** | 7 – 14 |
  | **2–6 wk** | 14 – 42 |
  | **1–3 mo** | 30 – 90 |
  | **3–12 mo** | 90 – 365 |
  | **1 yr+** | 365 – no limit |
  | **All** | 0 – no limit (the default) |

  A blank **DTE max** box reads *no limit* and means it: every listed expiration is
  scanned, however far out. A blank **DTE min** counts from today (0). Type in
  either box and the preset lets go, so a range you set by hand is never shown
  under a preset's name. A wider range finds more candidates.
- **Risk style** — how far out of the money the options the Finder **sells** sit,
  on both the put and the call side. A smaller delta is safer but pays less.

  | Style | Short-leg delta |
  | --- | --- |
  | **Conservative** | 0.05 – 0.10 |
  | **Balanced** | 0.10 – 0.20 (the default) |
  | **Aggressive** | 0.20 – 0.30 |

  If you edit the delta fields under **Advanced** by hand, the picker clears and
  shows **Custom**.
- **Advanced — delta bands and credit floor** (collapsed) holds the exact numbers:
  - **Put Δ** and **Call Δ** min / max. The bands place every option the Finder
    sells out of the money: the credit spreads, the short put and short call, the
    short strangle, and the call in a covered call or collar. They do not move a
    straddle's or butterfly's shorts, which sit at the money by definition.
  - **Min credit %** — it applies to the credit spreads only.

Changing the preset, the risk style or the Advanced fields does **not** rescan;
press **Scan** when you are ready.

**While it scans**

The top-pick cards turn into grey placeholders reading *Scanning SPY…* — always the
symbol you asked for, never the previous one — the old list is cleared, and a
spinner counts the wait in seconds: *Scanning SPY… 12 s*. Reading a whole chain
takes time. Measured during market hours with the **All** range:

| Symbol | Expirations | About |
| --- | --- | --- |
| NVDA | 25 | 14 s |
| SPY | 34 | 26 s |
| $SPX | 56 | 40 s |

The service handles one request at a time, so a Calculator or Simulator load you
start during a scan waits behind it.

The spinner stays up until the answer lands — including the answer for a scan that
failed, which arrives as soon as it fails (that can be after the chain has been
fetched, about 12 seconds on $SPX). Only if nothing at all has come back after
**3 minutes** (the service is down, say) do the cards become one still card and the
page says *The scan is taking longer than expected. It will appear here if it
finishes; if nothing arrives, check System Status and scan again.* A late result
still appears. Whatever lands belongs to the scan you asked for — same symbol, same
settings.

**When the chain is large, it asks first**

If your range holds **more than 30 expirations** — $SPX lists 56 with **All** — the
Finder asks what to load before it fetches anything. The top picks give way to one
card, *$SPX lists 56 expirations in this range. Choose what to scan:*, with four
buttons. Each shows how many expirations it keeps and a rough wait, worked out at
about 0.75 seconds an expiration:

| Button | Keeps | $SPX, All |
| --- | --- | --- |
| **Next 30 days** | expirations up to 30 days out | *Next 30 days · 23 · ~17 s* |
| **Next 90 days** | expirations up to 90 days out | *Next 90 days · 35 · ~26 s* |
| **Monthlies only** | Schwab's standard monthly expirations — not the weeklies, quarterlies or month-end ones | *Monthlies only · 19 · ~14 s* |
| **Everything** | every expiration in the range | *Everything · 56 · ~42 s* |

Every choice counts only expirations inside your DTE min / max, and a choice that holds
none is greyed out. While the card is up, the summary's count line reads *56
expirations — choose what to scan* and the list reads *Choose which expirations to scan
for $SPX.*

- **Click a button** and the scan runs as usual, spinner and all, for that symbol.
- **The pick is remembered for that symbol** while the page is open (it is not saved
  when you leave): scan $SPX again and it uses the same choice without asking. Another
  symbol has its own pick, or none.
- **After a scan with a choice**, the count line ends *Scanned 19 of 56 expirations ·
  Monthlies only*, with a **Change** link beside it. Change brings the card back;
  nothing rescans until you pick.
- **A range of 30 or fewer expirations never asks**, and ignores a remembered pick:
  everything in it is scanned, and the *Scanned … of …* part is not shown.
- **A pick can hold nothing** in the range you ask for next — *Next 30 days* on $SPX
  with DTE min at 31, say, where 33 expirations are in range and none is within 30 days.
  The count line ends *Scanned 0 of 33 expirations · Next 30 days*, and the list says
  *Next 30 days holds no expirations in this range for $SPX — use Change to pick
  another.*
- **A pick builds only its expirations**, so calendars and diagonals pair only within
  them: *Next 30 days* keeps the later month within 30 days, and *Monthlies only* pairs
  a monthly with a later monthly.
- **The volatility read does not change with the pick.** The Vol Rank, implied
  volatility and expected move every idea is scored against are those of the whole
  chain; the expiration they come from is loaded for that alone.
- The **Income Window** never asks.

**The summary strip**

After every scan — an empty one included — one strip names:

- the **symbol** and its **price**. The price shows even when no ideas came back,
  so an empty answer still reads as an answer about that symbol; when no price
  could be read it says **Price unavailable**, never $0.00;
- the **market view** the scan inferred, as pills — the direction, how strong the
  conviction is, and the volatility regime;
- the **Vol Rank** (one value per scan, so it lives here rather than on every row);
- a **count line**, for example *16 ideas · 6 below the quality bar · 3 where premium
  is too cheap to sell · 40 lower-scoring ideas not shown*. The cut counts are
  different reasons, explained below. The idea count always shows, even at *0
  ideas*; the cut and not-shown counts are left out when they are zero. When part
  of the chain could not be fetched the line adds *2 expirations could not be
  loaded* — the ideas shown come from the rest of the chain. When the scan itself
  failed, the whole line reads **Scan failed**;
- when the list holds **no credit spreads**, a line under the count saying why —
  for example *Credit spreads: none of 209 short strikes in the delta band made a spread — 82 credit below the minimum, 63 credit too small for the short strike's delta, 49 outside the expected-move window.* Those spreads are turned down before scoring, so
  the counts above cannot show them.

**Strategy chips**

One chip per strategy group, each with how many ideas it holds, plus **All**.

- Click a chip to show **only** that group; click more chips to add them.
- Click a chosen chip again to remove it. Removing the last one — or clicking
  **All** — shows everything again.
- Chips **filter instantly**: every scan already built all seven groups, so nothing
  is rescanned. The cards and the list both follow the chips.
- A new symbol starts back at **All**.

**Only clear**

The switch beside the chips hides every idea that is not fully clear on the
checklist — blocks, cautions, and anything the checks could not finish (see *Using
the checklist to pick a trade* under **Market Scanner**; the checks are the same
nine). Two things to know here:

- It filters the **ranked list only**. The top-pick cards go on showing the best
  idea from each group, so you can still see what was rejected and why.
- The count line says what it hid — *3 of 40 shown · 37 hidden by Only clear* —
  and, while a strategy chip is on, counts against that chip rather than the whole
  scan: *3 of the 20 in the chosen strategies shown*.

Nothing is re-scanned when you flip it, and the checks themselves refresh on their
own without re-scanning either.

**Top picks**

Up to four cards: the best-scoring idea from each **different** group among what
the chips are showing comes first — so four near-identical spreads never crowd out
the comparison. When fewer than four groups are showing (you clicked one chip, say),
the spare cards go to the next-best ideas by score, so a single group still fills
all four. Each card shows:

- the strategy, its **score** badge and **grade**;
- the expiration and days to go (`Oct 16 · 8d`), and an **Earnings** badge —
  *Earnings Nov 19* — when the company reports before the trade expires (see
  **Earnings** below);
- the legs, written like `L 100 shares / S 105C` (`S 2×100C` is two contracts, a
  butterfly's middle; a leg on a later expiration carries its date — a calendar
  reads `S 100C / L 100C 11/13`);
- a **payoff shape** — a small picture of what the trade makes or loses across a
  range of prices at its (nearest) expiration: **green** where it profits, **red**
  where it loses, a **dashed line** at zero and a small **tick at today's price**.
  It is a shape, not a chart to read values off;
- a **split bar** — **max loss** in red growing left from the middle, **max profit**
  in green growing right, both drawn against the larger of the two, with the dollar
  figures underneath. It shows the shape of the bet at a glance: a butterfly is
  mostly green, a covered call mostly red. A side with no limit fills its half and
  reads **∞**;
- a **probability-of-profit bar**, 0–100%: **amber** under 40%, **blue** from 40%
  to 60%, **green** over 60%;
- the **cost** — `$195 debit`, `$804 credit`, or `$54,058 debit for 100 shares`
  when the trade holds stock;
- **Calculator**, and **Paper** where the Paper Ledger can record the trade (see
  below).

Click anywhere else on a card to open it in the detail panel.

**The ranked list**

Every idea the chips show, best score first:

| Column | What it shows |
| --- | --- |
| **Strategy** | the name, with a small payoff shape beside it, and an *Earnings Nov 19* tag when the trade is open through a report |
| **Score** | the 0–100 score, coloured by zone |
| **Strikes** | each leg in short form, e.g. `L 765P / S 761P` (long 765 put, short 761 put); a leg on a later expiration adds its date, and a share leg reads `L 100 shares` |
| **Expiry** | the expiration and days to go |
| **Cost** | debit or credit in dollars, with *for 100 shares* when stock is held |
| **Max profit** | in dollars, or **∞** when there is no limit |
| **Max loss** | in dollars, or **∞**; a naked short also carries an *undefined risk* tag |
| **Probability of profit** | a small bar plus the percent, in the same colours as the cards |
| **Checks** | the go / no-go checklist's verdict for that idea, in one chip — *Clear · 7 of 9*, *2 cautions*, *Blocked*, *Partly checked*, or a dash until the checks have run. Hover for the whole sentence — or, on a caution chip, for the cautions themselves; click the row to read all nine lines in the detail panel. This column does not sort, and neither does the Market Scanner's |
| **Grade** | quality grade; hover it for the reason |

The last column holds the three action buttons (hover for their names): **Send to
Calculator**, **Send to Paper trade** (only where allowed) and **Expected Move**.
Click the Score, Expiry, Max profit, Max loss or Probability of profit header to
sort by it. The list shows **50 rows a page**, with page controls underneath; a sort
covers the whole list, not just the page you are on, so page 2 carries on where
page 1 stopped. A new scan, or a chip click, starts back on page 1 and keeps the
sort you chose. The list grows with the page rather than scrolling in a short box.

**When the list is empty**, it says why. It names the symbol, and the price when one
was read (with no price the words *at $…* are left out); a failed scan names the
symbol only:

| Message | Meaning |
| --- | --- |
| *No strategies cleared the quality bar for SPY at $764.48.* | ideas were built, and every one failed the bar |
| *No strategies for SPY at $764.48 — premium is too cheap to sell.* | every idea left would have sold premium, and this symbol's premium is historically cheap |
| *No option chain came back for SPY at $764.48.* | the option chain did not arrive — a mistyped symbol reads this way too |
| *SPY at $764.48 has no expirations in this expiry range.* | nothing is listed from today up to your DTE max (plus two days). DTE min is not considered here — if expirations exist but all fall below your DTE min, you get the *could not be built* message instead; lower DTE min or raise DTE max |
| *No strategies could be built for SPY at $764.48 in this expiry range.* | none of the above: nothing could be built |
| *The scan for SPY failed. Check System Status and scan again.* | the scan itself broke |

**The detail panel**

The Trade detail panel beside the list starts **closed**, so the list keeps its
width, and opens whenever you click a card or a row. A new scan clears it. It opens
with the **checklist** — the nine checks behind the row's chip, one line each — and
says *Checking…* for the moment it takes to read them. If the idea you are reading
drops out of the results, the checklist says *This trade is no longer in the scan's
results — the details below are as it last read*, so the panel is never a verdict on
a row that has gone. The legs,
breakevens and bias live here rather than in the list, and they read the way the
position is held: a share leg is **Buy 100 shares**, a calendar or diagonal lists
each leg with **its own expiration date** (and drops the single "Exp" line, which
would name only the near month), **every** breakeven is shown — a straddle or
butterfly reads `$95.20 / $104.80` — a butterfly's middle reads `Sell 2× 100 C`, and
a position holding shares states its dollars **per position** rather than per
contract.

**The seven strategy groups**

| Group | Builds | How the strikes are picked |
| --- | --- | --- |
| **Directional** | long call, long put, short call, short put | long legs near 0.55 delta; shorts at the middle of your delta band. The short put is the cash-secured put |
| **Spreads** | bull call, bear put, put credit, call credit | debit spreads buy ~0.60 delta and sell ~0.30; credit spreads use your delta band and **Min credit %** |
| **Neutral** | iron condor | built from the credit spreads |
| **Straddles & strangles** | long and short straddle, long and short strangle | straddle at the money; long strangle buys both sides near 0.30 delta; short strangle sells at the middle of your delta band |
| **Butterflies & condors** | call butterfly, put butterfly, iron butterfly, call condor, put condor | body at the money; wings the same distance either side, the listed distance nearest half the expected move. A condor's shorts sit one wing out, its longs two |
| **Calendars** | call and put calendar, call and put diagonal | calendar: same at-the-money strike, near month short, later month long. Diagonal: short the near month near 0.30 delta out of the money, long the later month near 0.70 delta in the money |
| **Stock + options** | covered call, protective put, collar | one 100-share lot at today's price; the sold call at the middle of your call delta band, the bought put near 0.25 delta |

Things worth knowing before you read the results:

- **Calendars and diagonals need two expirations inside your DTE range** — the
  near one at least **7 days** out, the later one the expiration nearest **four
  weeks** after it, and at least a week after it. A narrow range, such as the
  **1–2 wk** preset, often holds no such pair and builds no calendar; pick a wider
  preset or raise **DTE max**. No extra data is fetched for them.
- **Every structure is built on every expiration in your range** (in your pick, when
  a large chain asked), not only the nearest one — a calendar or diagonal takes each
  expiration in turn as its near month.
- **Only the best 25 of each strategy are listed.** A whole chain can produce
  hundreds of ideas, so after the quality bar the Finder keeps the 25
  highest-scoring of each strategy — for example the best 25 bull call spreads
  across every expiration — and counts the rest in the summary as *lower-scoring
  ideas not shown*. Narrow the expiry range to see a different 25.
- **Straddles, strangles, butterflies, the iron butterfly, condors and the share
  structures skip expirations less than 7 days out.** With **DTE min** at 0,
  none of them is built on a 0–6 day expiration; the floor applies by itself, so
  there is no need to raise DTE min for it. Directional trades, the debit and
  credit spreads and the iron condors are built on those near expirations too.
- **Nothing is built off-centre.** When the at-the-money strike is missing from
  the chain, the straddles, butterflies, condors and calendar are skipped rather
  than moved to the next strike.
- **Some candidates are skipped on purpose:** a short strangle or covered call
  whose sold option would sit above your delta band's ceiling; a protective put or
  collar whose put is under 0.10 delta (a collar also needs its call at 0.05 or
  more); a diagonal whose short is outside 0.15–0.45 delta or whose cost reaches
  the width between its strikes; a long butterfly or condor priced for a credit or
  costing its whole wing, and an iron butterfly priced for a debit or taking in its
  whole wing. Wide quotes can put the mid prices there, and those prices are wrong
  rather than the trade good.
- **Earnings:** a trade still open when the company reports is **kept and
  tagged**, not dropped — *Earnings Nov 19* on its card and beside its name in the
  list, with the year added when the report falls in another year. A report can
  move the stock sharply either way, so read the tag before you trade it. A
  calendar or diagonal is checked against its **later** expiration, because the
  back month is still open when a report lands after the near month expires. The
  Market Scanner and the Income Window drop these trades instead of tagging them.
- **Below the quality bar** counts candidates that were built and scored but did
  not clear the bar. That count is what tells "everything failed" apart from
  "nothing was found".
- **Too cheap to sell** counts trades that would **sell** premium, dropped because
  this symbol's option premium is historically cheap; trades that **buy** premium
  are kept. A low-premium symbol correctly shows long calls, long puts and debit
  spreads instead of credit spreads.
- ⚠ **On fairly priced options, the short straddle and the covered call are built
  but nearly always cut.** Both win too rarely for the bar that premium-selling
  trades must clear, so they are counted in the summary's **below the quality bar**
  figure rather than listed. When options are priced **rich** — well above the
  volatility the odds are worked out from — a short straddle's bigger credit can
  carry it over the bar, and it is listed; the covered call stayed cut in every case
  tested. The Calculator still builds both. Long straddles and strangles usually
  pass only as **Marginal**.

**Paper** (on a card) and **Send to Paper trade** (in the list) appear only for
trades the Paper Ledger records correctly: credit spreads, iron condors, long calls
and puts, debit spreads, and the call and put **butterflies and condors**. A debit
trade that arrives with no debit to pay is refused rather than booked as free.
Straddles and strangles are for study only and have no button; neither do the iron
butterfly, calendars, diagonals or the share structures. A trade the button takes
is still checked against the Paper Ledger's risk limits — the Paper trade box shows
each limit before you send — and a message says if it was not opened and why (see
*Risk limits on new trades* under **Paper Ledger**).
The Paper Ledger has no earnings check, so a trade tagged *Earnings* opens there
like any other.
**Calculator** carries every
idea, calendars (both expirations) and share legs included — but clicking an
expiration pill on the Calculator afterwards moves **every** option leg to that date,
which collapses a calendar.

## Overview

**Route:** `/trade`. The first tab of the Trade Analyzer group, and the screen the
**Signal desk** command bar sits above on all four tabs.

The command bar is persistent: the model stamp, a **Symbol** box, the last price and
its change, and the multi-timeframe bias. Type a ticker and press **Enter** (or tab
out) to commit it - the box outline turns indigo while your typing differs from the
committed symbol, so a half-typed ticker never looks like the thing on screen.
Clearing the box and leaving it reverts to the last good symbol rather than blanking
the desk.

Two report buttons sit in the command bar beside the bias, and each opens a separate
screen in a new browser tab once its report is ready:

| Button | Opens |
| --- | --- |
| **Deep Dive** | The EquityDeepDive quantitative report for the committed symbol |
| **AI Query** | A copyable chat prompt describing the symbol, for pasting into Claude |

They act on the **committed** symbol, so commit first if you have just typed a new
ticker.

The screen itself reads top to bottom:

- **Market state** with a chip per side - *long cleared*, *short relative only*, and
  so on. These are the same gates the rest of the app applies.
- **Short Term (1-8 weeks)** - a **recommendation** first: an action (Buy, Buy
  paired, Sell short, Pair short, Stand aside or No trade), a confidence chip, and
  one line saying what to do. The action combines the model's ranking with what the
  market permits, so a bottom-ranked name can read "Pair short" rather than "Sell
  short" - the model predicts it will lag the S&P, not that it will fall. Beneath
  the rule the **ranking** remains as information: the band on its rail and what
  that band has historically been worth. Two cards below say what each side is
  permitted and why.
- **Long Term (months+)** - the verdict with its six factor scores on centred bars.
  A bar to the right of the middle line is a positive contribution, to the left
  negative, and a factor with no data reads **n/a** with no bar at all.
- **Dealer positioning & volatility** - put wall, flip, spot and call wall on one
  ladder, over the volatility stats. The ladder is **absent entirely** when the
  data is not collected or is stale, because a drawn ladder is a much stronger
  claim than a missing one.
- **Where it sits among its peers** - the same model run across its sector, with
  this symbol highlighted.

---

## Evidence

**Route:** `/trade/evidence`.

The reasoning behind the Position verdict. Each validated factor appears with its
**z-score** against today's cross-section, its **weight**, a zero-centred
**contribution bar**, and its **IC** - the historical information coefficient. The
weighted composite is footed at the bottom, and it is the sum of the contribution
column.

Two cards sit alongside, and they answer different questions:

- **Model track record** - the artifact, its out-of-sample IC, which weight set
  scored this symbol, and the live tracking line. The amber callout carries the
  loudest thing the model has to say about itself: how much of its weight sits on
  volatility factors.
- **This name's history** - the last five reads of *this symbol* and what followed.
  A read whose 20 days have not elapsed shows **pending** rather than a number.
  Five reads can never support a correlation, which is why this is a list of
  outcomes and not a statistic.

---

## Rank Board

**Route:** `/trade/board`. The second tab of the Trade Analyzer group.

Where **Analyze** judges one stock you already have in mind, the **Rank Board**
runs the same model across every name in its universe and sorts them — so you can
start a session by seeing what is best and worst today, then analyze the ones worth
a closer look.

**Deciles** are today's ordering: decile 10 is the strongest of the current
cross-section, decile 1 the weakest. **Long candidates** and **Short candidates** are
those two ends.

Each pool carries a line explaining its own state, and it is worth reading before
acting on the list:

- **"Express these RELATIVE…"** means the market filter has not cleared the short
  side. The model predicts return *versus the S&P 500*, so a bottom-decile name in a
  rising market is expected to **lag the index**, not to fall. Pair it against the
  index rather than shorting it outright.
- **"Too few names in today's cross-section…"** means the universe is too small to
  form deciles — a data limit, not a verdict on the market.

**Gated rows stay on the board** with their reasons shown, because "the top-ranked
name reports earnings in two days" is exactly what you want to see. The line under the
header tells you which gates were checked here; it is fewer than the Analyze card
tests, so an unmarked row has not passed everything.

⚠ **Read the amber line before you read the ranking.** It states how much of the
model's weight sits on volatility, and that is currently about half — meaning **the top
of this board is the highest-beta end of the universe**. That ordering has historically
worked while the market rose and inverted when it fell. Use the board to pick what to
research, not as a ranked buy list.

**Rebuild** forces a fresh scoring run; otherwise the board follows the daily
universe snapshot.

### The model paper book

Below the board sits an isolated **paper book** that follows the board's own
pools, so the model builds a track record without you placing anything. It opens
the ungated names from each pool, applies the Trade Plan's stop, target and
20-trading-day time stop, and reports **long and short separately** — a model
carried entirely by its longs is a different thing from one that works on both
sides.

Two details worth knowing before reading its P&L:

- It trades the **underlying stock**, not the options structure the Trade Plan
  suggests. A spread's time decay and volatility swings would swamp the actual
  question, which is whether the ranking works.
- A **relative** short is held as a pair against SPY. The model predicts return
  versus the index, so that is what the book measures; holding it outright would
  be recording the market's direction instead.

It is paper only, and separate from the other paper books.

---

## Trade Plan

**Route:** `/trade/plan`.

Two cards. The left one is the plan when a side is cleared: structure, legs, entry
zone, stop, target, **time stop** and events. The time stop is highlighted in indigo
because it is the model's own 20-trading-day horizon and nothing else in the app
enforces it - past that date the read is unmodelled.

The right card is the other half, and it is always shown. It states plainly why
there is no trade (or why one side is refused), lists **what would change it**, and
tells you **how to express the view anyway** if you want the exposure - usually as a
pair against SPY, since relative return is what the model actually predicts.

---

## Cross-page actions

Three buttons appear on signal rows across the Options section:

| Action | Available on | Effect |
|--------|--------------|--------|
| **Send to Calculator** | Market Scanner, Strategy Finder | Opens the Calculator pre-filled (strategy, symbol, expiry, strikes, premiums, IV) and runs it. |
| **Send to Paper trade** | Market Scanner, Strategy Finder | Asks for a quantity, showing each of the ledger's risk limits in green (fits) or red (breaks) for that quantity, then creates a Paper Ledger trade if it fits. A message a moment later says it opened, or why not. Stays on the current page. |
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
- **Sectors** tab — your sector weights vs the benchmark.
- **Performance** tab — per-position letter grades (Return / Capital / Risk /
  Entry), a composite, annualized return, vs-sector, and drawdown. Click a row to
  see its **advisory suggestions** below the table.

P&L streams live tick-by-tick; press **Refresh** to rebuild sectors and grades.

---

## EOD Report

**Route:** `/eod` (with a `/eod/detail` drill-down).

An end-of-day rollup of the day's Options activity.

**Two views**, reached by the link at the foot of the summary:

| View | Route | Contents |
|------|-------|----------|
| **Summary** | `/eod` | Headline tiles plus a Daily / Weekly (WTD) / MTD performance block **per book** — manual paper and captured signals (counted at one contract per signal). |
| **Detailed** | `/eod/detail` | The same performance plus breakdowns by **strategy** (PCS / CCS / IC), by **0-DTE vs swing**, and by **status** (open / closed / expired), then the full trade, scanner and captured tables. |

Both views use a jump-link table of contents, and every section is collapsible —
which keeps working in the exported file as well as in the app.

**It runs itself at 3:15 pm CT.** Every trading day, a quarter of an hour after the
cash close, the app archives the day's report for you — the same two files the
**Generate** button writes. You only need the button to take an extra snapshot, or to
recover a day the machine was off for. Weekends and market holidays are skipped.

**The buttons:**

- **Generate** snapshots the current data into standalone `summary.html` and
  `detail.html` archived by date under `webgui/data/eod/<date>/`. Pressing it after
  the automatic run replaces that day's files with a fresh snapshot, so **it asks
  first** and the question names the date it is about to replace.
- **It refuses to overwrite a real report with an empty one.** If the stack is
  stopped when you press Generate, every cache reads back empty and the report
  would be a complete-looking document of "No captured signals." notes. Nothing is
  written, and an amber message says so. Start the stack and press it again.
- **Open summary file** / **Open detail file** open those archived files in a new
  browser tab.
- The **Archive** list reopens any past date.

> Realized P&L is bucketed by **exit** date, while opened trades and credit
> collected are bucketed by **entry** date. They answer different questions and will
> not reconcile to each other — that is correct, not a bug.

## Post to X

**Route:** `/x`.

Write a one-off post for the app's X account, and see every post the app has
made there.

- **Post** is the text of the post. **Link** and **Hashtags** go on the lines
  under it, the way every post from the app is laid out.
- The **counter** shows the length the way X counts it: a link always counts
  as 23 characters and an emoji as 2. It turns **red** when the post is too long,
  and the service will then drop hashtags from the end first and only then cut
  the text. The **Preview** underneath is exactly what will be sent — unless
  `x.max_tags` is set to something other than 4, since the page previews four
  hashtags at most.
- **Image** attaches one PNG or JPEG of up to 5 MB; **Remove image** takes it off.
- **Post** asks you to confirm, then hands the post to the options service, which
  sends it. The result appears in the log a moment later.
- **Recent posts** lists every post the app makes to X — the market reports, the
  hourly trade ideas and posts from this page — with a link to each one that went
  out, or the reason it did not (refused, failed, over the daily cap).

> **X is off, and dry, until you set it up.** Nothing reaches X until the `x`
> block in `shared/notifications.json` has its keys filled in and `enabled` is on.
> While `x.dry_run` is on, every post is logged as *Dry run* and nothing is sent.
> The default hashtags for the market reports and the trade ideas live in
> `x.hashtags`; this page's own defaults (`#options #trading` and the
> neuralstrike.co link) are built into the page, and it does not read
> `x.hashtags.marketing`. `max_tags` caps how many hashtags any post carries, and
> `daily_cap` limits how many posts go out in a day.

## User Manuals

**Route:** `/manuals` — a tab in the **More** group, alongside EOD Report. (It used
to be nested under Settings; it is now a peer tab.)

A simple index that links the five manuals — each opens in a new browser tab:

- **User Guide** — how to use the app (this document).
- **Reference Guide** — what each tab and sub-tab is for, why it matters and when to
  open it, starting from a one-page summary of the whole app.
- **Technical Reference** — the math behind every number.
- **API / Developer Reference** — the integration surface for developers.
- **Options Glossary** — plain-English definitions of every term the app puts on
  screen, from what a contract is to what dealer gamma means.

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
- A **component grid** — Redis, the Schwab gateway, the six services, the web app
  itself, and the public live screens beside it, each with Online/Offline and its
  tier. The gateway's card also shows the **Schwab auth** state.
- A **Re-authorize** button on the gateway card opens Schwab's OAuth login in a new
  browser tab. Use it when the auth line says the login has expired.
- A **data-freshness** table showing each domain's latest cache write and its age.
  This is the more informative half: a service can be *online* and still not be
  publishing, and only this table shows that.
- Every component has a **Restart** button, which stops that component and starts
  it again — back within about fifteen seconds. **It asks first**, and the question
  says what *that* restart costs: restarting **this web app** disconnects the page
  you are looking at and you will need to reload it, and restarting the **Schwab
  gateway** while the market is open takes market data away from every service in
  the stack for those seconds. Redis has no Restart button — it is a system service
  this app does not own.
- **Refresh** re-checks on demand; the board also re-checks on its own.

> **`token_expired: true` on the gateway is routine** — it refreshes itself. Only a
> missing or expired *refresh* token needs **Re-authorize**.

> **Restart order matters:** Redis first, then the gateway, then services, then the
> web app. A service restarted while the gateway is down will start and then fail to
> fetch anything.

## Settings

**Route:** `/settings` — a standalone item at the **foot of the rail**, with System
Status and Stop All Services. It has three tabs: **General** (your app preferences),
**Appearance** (every colour and font) and **Configuration** (the trading settings).

### Configuration

Every setting the trading services use — the thresholds that used to mean editing
a file on the server — in one place, grouped by what they do:

| Category | What it controls |
|---|---|
| **Trade selection** | Volatility (IV rank) floors and ceilings, minimum credit, directional and single-option rules, the score a signal needs to be recorded |
| **Exits & trade management** | Take profit, stop loss, time and delta stops, the profit-lock ladder, per-structure rules, Rescue board warnings |
| **Paper books** | The largest loss one trade may carry in each paper book |
| **Flow alerts** | Each detector's thresholds, and which alerts reach your phone |
| **Market hours & schedules** | Session times, operating windows, and the time of every scheduled job (briefings, digests, reports) |
| **Symbols & watchlists** | What the gamma collector polls, the BIG10 basket, the Net Prem groups |
| **Sector map** | Which sector each symbol counts toward for the sector cap |
| **Commissions** | Schwab's per-contract rates |
| **Ports / Environments** | Shown for reference only |

How to use it:

1. Pick a category on the left, or type in **Search** (for example *take profit*,
   *VIX*, *delta*) to find a setting anywhere.
2. Change a value. Each row explains the setting in plain words. Percentages are
   typed as percents (type `50` for 50%). A value outside its allowed range is
   refused on the spot, with a sentence saying why.
3. A changed row shows **Unsaved**; a row that differs from the shipped value shows
   **Shipped: …** and a reset button that puts it back.
4. Press **Save changes** at the bottom. The app then lists the services that must
   restart to use the new values and offers **Restart now**. During market hours it
   warns you first, because restarting the options service loses a minute or two
   of gamma collection. You can choose **Later**; a yellow banner reminds you until
   you restart.

> **Your changes are kept separately from the shipped settings.** They are saved as
> overrides, so app updates never overwrite them, **Reset** always takes you back,
> and **Recent changes** at the bottom lists what was changed and when.

> Some settings cost money or API budget when you loosen them — each category
> with that risk shows a yellow note (for example, every scheduled Claude briefing
> is a paid call, and each symbol added to collection costs about 440 Schwab calls a
> day).

### General

Preferences, all saved on your machine:

- **Scanner alerts** — enable the audio alert, pick the sound (chime / bell /
  ping), a **Test sound** button, a **Volume** slider, an **only during market
  hours** toggle, and a **minimum score to alert**.
- **Spoken alerts (Desk)** — the Desk announcing new flow alerts, newly-opened
  positions and symbols joining the Opportunity Board out loud. An **on/off**
  switch, one switch **per section** under it (greyed out while the main switch is
  off), a **Voice** picker (six neural voices;
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
- **Show the ticker** — the scrolling bar at the bottom of every page, with a
  speed setting. It only shows or hides the marquee — see the note below.
- **Appearance** is its own tab now (**Settings → Appearance**): every colour and
  font, grouped as surfaces, text, fields, buttons, status colours, charts, type
  and menu, with a live preview. **Save changes**, then **Restart now**, applies
  it to every screen; **Reset to shipped values** (confirm-gated) goes back.
- **API usage** — how many calls the app has made to **Schwab** (counted at the
  gateway) and to **Claude** (counted at each call site), for today, the last 7 days
  and the last 30.
- **Maintenance** — **Vacuum GEX history DB** compacts the intraday options
  database and reports the before-and-after size. It asks first, and **the question
  tells you whether the purge switch above it is on** — with it on, every saved
  session but the last five is deleted before the compaction. The button shows a
  spinner while it runs, and the tool's own output lands underneath it.

> Clicking **Test sound** — or **Test voice** — also unlocks browser audio for the
> session. Browsers block sound until you interact with the page, and they do it
> silently, so a Desk that never speaks is usually blocked rather than broken. The
> Desk shows an **Enable spoken alerts** button when that happens.

> **The first time a given phrase is spoken it takes a second or two to generate**,
> because the clip is made on demand; after that it plays from a cache on your own
> machine and is instant. The app pre-generates the common flow phrases in the
> background at startup, so in practice you rarely hear the delay.

> **Turning the ticker off only hides the scrolling bar.** The bar leads with the
> headline of the latest published market report, and that same report also feeds
> the Desk's **Market Summary** frame, so it keeps being picked up — whenever a new
> report is published — whether or not the marquee is showing.

> **Run Vacuum after hours.** It locks the database for minutes, and the tool
> refuses to run while the collector is active.

**User Manuals** is no longer nested under Settings — it is a tab in the **More**
group, next to EOD Report.

## Stop All Services

**Route:** `/terminate` — the red-outlined button at the foot of the rail.

A guarded "stop the whole local stack" page. The **Stop all services** button — a
red outline, matching the rail — opens a confirmation; the solid red button is the
**Stop everything** inside it, which is where the decision is actually made.
Confirming stops the gateway, the six services, the web app **and the public live
screens** — the public site goes dark with it.

**The confirmation asks for your authenticator code.** Type the current 6-digit
code from your authenticator app into the dialog and press **Stop everything** — or
just press **Enter** in the code box. A wrong, missing, or already-used code refuses
the stop and says so, **leaving the dialog open** so you can wait for the next code
and try again — nothing is stopped. A code you spend here
cannot then be used to sign in (and vice versa), so if you have just signed in,
wait for the next one.

> **This also stops the page you're on** — it will become unresponsive right after
> you confirm, by design. Redis is left running. Re-launch with
> `systemctl --user start trading-prod.target`.

---

## Sign out

**Route:** `/logout` — the last item in the rail, below Stop All Services.

Ends this browser's session and returns you to the sign-in page. It also forgets a
**trusted device**, so the next sign-in asks for your authenticator code again —
which is the point on a borrowed or shared machine.

- It signs out **this browser only**. Other devices you are signed in on stay
  signed in.
- Nothing running is affected. The services, the collectors and the scheduled scans
  all carry on. Signing out is not stopping anything —
  that is the page above it.

It sits at the very bottom of the rail deliberately: on a phone the bottom edge is
the easiest thing to hit, so the harmless control gets that slot and the
stop-everything button sits above it.

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
Restart that service. On-demand views (Trade) are never flagged stale.

**Schwab Authorization shows as not authorized / expired.**
Open **System Status → Schwab Authorization → Authorize** to re-run the OAuth
login in a new tab.

**My live portfolio P&L isn't moving.**
Check the Portfolio status bar: if the live-stream indicator is off or the proxy is
down, ticks aren't arriving. Press **Refresh**, and verify the gateway on the
Status page.
