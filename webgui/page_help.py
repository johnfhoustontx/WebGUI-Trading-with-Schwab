"""Per-page "idiot's guide" help text.

Shown in a 2-second hover tooltip on the nav drawer items and the top tab strip
(``main._help_tooltip``), keyed by the page's active route. The old header ``?``
button is retired. Each value is Markdown — keep it short, plain,
and focused on (a) what the page is for and (b) how changing the key parameters
changes the outcome. ``help_md(active)`` returns the guide for a route (or a
sensible default).
"""

# The 0-DTE close projection explained. Its own constant because it is BOTH part
# of the Gamma page's hover guide (below) AND rendered on the page itself, in a
# collapsed "How to read the 0-DTE close projection" expander beside the charts —
# the hover tooltip is ``pointer-events: none`` and clips to the space under its
# nav item (measured: ~466px of a ~1400px guide), so long-form detail parked there
# alone is unreachable. One constant, two surfaces, no drift.
PROJECTION_HELP_MD = """
**The 0-DTE close projection** (only on names with options expiring **today** —
$SPX, $NDX, SPY, QQQ, IWM, AMD; absent everywhere else). Options expiring today
lose delta just from the clock running out, even if price never moves. Three
displays show that same drift three ways:

- **"Projected close" outlines** (amber outline on the bars) — *which strikes
  move.* Each outline is that strike's net **after** its own drift. Outline
  **reaching past** the solid bar = hedging demand there **grows** into the
  close; **pulled back inside** = it **fades**. A strike with no outline holds no
  expiring-today interest at all (that's different from "no drift").
- **Proj. flip** (amber dashed line, on all four views) — *where the crossing
  ends up.* **The gap between it and the actual flip is the drift measured in
  price** — that's why both are drawn. A small gap means the clock won't move the
  battlefield today; a wide one means the level dealers defend is migrating.
  It's a **delta** level, so compare it to the **delta** flip — the gamma flip is
  a different curve and can sit ten-plus points away.
- **0-DTE hedge pressure** (the small green/red panel under the heat map) — *how
  big, and which side.* Green = dealers must **buy** the underlying into the
  close to stay hedged; red = **sell**. Plotted in billions of dollars. The
  moment worth watching is the bar flipping sides mid-session.
- **What you're assuming:** spot stays put, open interest stands still, and only
  time passes. It answers *"if nothing else happens, where does hedging demand
  land at 3:00?"* — a **baseline, not a forecast**. Any real move rewrites it,
  which is why it recomputes every minute. Read it as context (knowing dealers
  owe $3B of buying tells you how a drift higher gets funded), not as a signal on
  its own.
"""


HELP_MD: dict[str, str] = {
    "/": """
**Market Scanner — the simple version**

Finds option **credit spreads** (you sell risk and collect cash up front) and
scores each one **0–100** for quality.

- **0-DTE / Swing** — the small tabs at the very top (under the page tabs):
  trades expiring today vs over several days.
- **Score chip & Grade** — greener/higher means better reward-vs-risk, higher
  probability of profit, and better trend fit. Work from the top down.
- **Click a row** for full details; the row buttons send it to the Calculator,
  paper-trade it, or chart its Expected Move.
- It re-scans on its own — **Run scan** (at the right edge of the table) just
  forces a refresh.
""",
    "/options/swing": """
**Strategy Finder — the simple version**

Like the Market Scanner, but you pick one symbol and it ranks every strategy
family for it — directional, spreads, and neutral.

- **DTE min/max** — how many days to expiration to allow. Wider = more candidates.
- **Put/Call Δ (delta)** — how far out-of-the-money the strikes sit. A smaller
  |delta| is safer but pays less credit.
- **Min credit %** — throw out trades that don't pay enough premium for the risk.
- Raising deltas/credit → fewer but richer trades; lowering → more but lower quality.
""",
    "/options/calculator": """
**Calculator — the simple version**

Shows the profit/loss of an options trade **before** you place it — any
**multi-leg** structure.

- **Strategy** — pick a template (single, vertical, **iron condor**, **butterfly**,
  **calendar/diagonal**…) and the **leg editor** fills in. Add, remove, or edit legs
  freely — each has its own strike, **expiry**, and quantity. **Load**/**Fetch** pull
  live prices.
- **IV %, IV Δ, Rate** — volatility and interest assumptions. Higher IV = pricier
  options and wider P&L swings.
- **Calculate** builds the **summary tiles** (max risk/return, breakeven,
  probability) and a **heat map** of P&L by price (rows) and date (columns) —
  green = profit, red = loss. Calendars price each leg at its own expiry.
- **Copy to Simulator** sends the exact legs across. Widening strikes raises the
  credit you collect but also the max loss.
""",
    "/options/gamma": """
**Dealer Positioning — the simple version**

Shows where option **dealers** must buy or sell to stay hedged — which can pin or
accelerate price.

- **Symbol dropdown** — pick the index or stock. The page **remembers** your last
  symbol when you come back, and switching symbols refreshes automatically.
- **GEX / Charm / DEX / Vanna / Flow / Net Prem / Term** — the small tabs at the
  very top (under the page tabs): different lenses on dealer positioning; the
  **walls** mark likely support/resistance.
- **Bars + heat map** — show ±20 strikes around spot, so the size stays steady as
  the day moves. **Press and hold** the left mouse button on the heat map to read a
  strike's value (it follows the cursor while held); plain hovering shows nothing.
- Positive gamma → price tends to **stick** near the walls; negative gamma →
  moves get **amplified**. Auto-refreshes every 2 minutes.
- After the close the **last session's** chart stays on screen until midnight
  (Friday's holds through the weekend), then clears for the next trading day.
- **Net Prem** — the one tab that shows **many symbols at once**: the day's net
  options premium (call dollars **minus** put dollars) as one line per name,
  from a menu of 28. The group tabs (**Indices & Broad**, **SPDR Sectors**,
  **Mega-caps**) only filter the tick-boxes — your picks **stay selected** when
  you switch groups, so you can plot `$SPX` next to `XLK`. Each symbol keeps the
  same colour whatever else is on the chart.
- **Dollars ($M) / Skew %** — the sizes are wildly apart (SPY can run hundreds of
  millions on a day DIA barely reaches one), so **Dollars** shows the real money
  and **Skew %** rescales each line to net as a share of that symbol's own
  premium — use it to compare a big name and a small one side by side. Premium is
  **unsigned cumulative traded dollars** (Schwab serves no time-&-sales tape), so
  this is a **money-weighted put/call read, NOT net buying**. Group, ticks and
  scale are remembered. Sector history starts the day this shipped, so those
  lines fill in from here on.
- **Analyze** asks Claude to read the live $SPX / SPY / QQQ dealer positioning and
  opens an **infographic** in a new tab — a regime + bias gauge, a price-level
  ladder + key levels per index, a per-symbol **what-if** (rally / sell-off / chop),
  and a **why is this happening** at the bottom. It also runs on its own at
  **premarket, ~18 min after the open, midday, and the close** — the
  **Briefings** dropdown (top right, next to Explain and Analyze) opens each.
  **Explain** opens a one-symbol infographic.
""" + PROJECTION_HELP_MD,
    "/options/simulator": """
**Simulator — the simple version**

Re-prices a **multi-leg** option position under different what-ifs (Black-Scholes).

- **Fetch snapshot**, then pick a **Strategy** and adjust the **legs** (kind / side /
  strike / expiry / qty) — singles, spreads, condors, butterflies, calendars.
  The controls and strategy sit side-by-side in one panel.
- **Replay / What-if / IV shock** — the small tabs at the very top (under the
  page tabs) switch the three views below.
- **Replay** — how the whole position would have behaved along recent price moves.
- **What-if** — the **Price change** slider moves the underlying up or down, and the
  **Days passed** slider fast-forwards time;
  each leg decays on its own clock, so calendars behave correctly. Watch the price
  and Greeks change.
- **IV Shock** — multiply volatility to see vega risk. More time or volatility =
  more option value.
- **Copy to Calculator** sends the exact legs across for the P&L tiles + heat map.
""",
    "/options/expected-move": """
**Expected Move — the simple version**

Draws how far the market **expects** a symbol to move by an option's expiration —
the ±1 standard-deviation cone.

- **Symbol + Expiry** — the chart shows recent candles plus the dashed
  expected-move bands.
- **Strike lines** — your short/long strikes overlaid. Strikes **outside** the cone
  are ones the market thinks are unlikely to be reached (good for sellers).
- **Look-back** — how much history to show. A wider cone means higher implied
  volatility.
""",
    "/options/rescue": """
**Rescue — the simple version**

Flags credit spreads that are **in trouble** and offers ways to fix them.

- **At-risk board** — paper positions scored **tested** or **critical**, ranked by
  a 0–100 **heat** (green = calm, red = danger).
- **Click a row** to see ranked **rescue options** — roll, widen, or close — each
  with its cash cost/credit and the new risk numbers.
- **Apply** dispatches a (simulated) paper adjustment. The board refreshes itself
  as positions are re-priced.
""",
    "/options/matrix": """
**Opportunity Board — the simple version**

One **at-a-glance grid** of every tracked symbol, so you can scan the whole board
without opening each page.

- **Each row** is a ticker: spot, **Day %**, a **trend** arrow, call/put
  **acceleration**, put/call ratio, net premium ($M), the **GEX** regime
  (above/below the flip), how many live **signals** and **flow** alerts it has, an
  overall **Signal** (buy / neutral / sell), and a **hotness** score.
- **Click any column header** to sort — e.g. hottest names or biggest movers first.
- Green leans bullish, red leans bearish. Auto-refreshes as the data updates.
""",
    "/options/flow": """
**Flow Alerts — the simple version**

Everything the options service flagged **today**, newest first — the same alerts
that chime and hit your phone, kept somewhere you can actually read them.

- **Crossover** — call premium overtook put premium on a symbol, or the reverse.
- **Unusual activity** — one contract traded far more than its open interest.
- **Gamma flip** — spot crossed the dealer gamma flip, so dealer hedging starts
  damping the move instead of amplifying it (or the reverse).

**Age** tells you whether this just happened or is this morning's news. Filter by
type or symbol, and **click any row** to open Dealer Positioning for that symbol.

Covers today only; the list resets overnight.
""",
    "/options/paper": """
**Paper Ledger — the simple version**

A practice ledger of option trades — **no real money**.

This is the **hand-kept ledger** — trades you sent here yourself. The automated
engine's positions live on **Paper Account**.

- **Each row** is a trade with its strikes, credit, max loss, and live P&L.
- **Analyze** re-prices it now and shows current Greeks; **Close** records an exit.
- Use it to test ideas from the Market Scanner without risk.
""",
    "/options/captured": """
**Captured Signals — the simple version**

Market Scanner signals you're **tracking over time** to see whether they're working.

- **Entry vs Current Score / Drift** — is the setup getting better or worse since
  you saved it?
- **Recommendation** — green = take profit, red = cut, amber = hold.
- **Refresh marks** re-prices everything live; you're alerted when a stop or target
  is hit.
""",
    "/options/portfolio": """
**Paper Account — the simple version**

The account behind the automated paper-trading engine.

This is the **engine's own account** — it opens and closes positions on its own.
Trades you sent by hand live on **Paper Ledger**.

- **Cards** — equity, cash, P&L, open count, engine status.
- **Run entry / manage cycle** — open new positions from captured signals, or
  re-price and auto-close existing ones (this also runs every 5 minutes on its own).
- **Reset** sets a new starting balance.
""",
    "/sentiment": """
**Sentiment — the simple version**

A **0–10** read on market mood. It's **contrarian**: a high score means lots of
fear, which can mean opportunity.

- **Sentiment gauges** — today vs the 30-day average.
- **Market Trend gauges** — a 0–100 direction (50 = neutral, 100 = strong bull).
- **Components** — press and hold to see what's driving the score.
- **Market Regime** — *how* the market is moving, not which way: Mean Reversion,
  Trending, Breakout, Choppy or Crisis. The stacked chart shows how much of each
  is in today's tape, so a change of character shows up as the bands shifting
  gradually. When one regime is taking over you'll see a line like
  "Mean Reversion → Trending · 60%". Recomputes every 5 minutes during market
  hours; outside them it holds the last read. Says "Unclear" when the evidence
  is genuinely weak rather than guessing.
- **Daily Sentiment & Trend** — the two color-coded intraday graphs (last 5 days).
- **Sector & Industry**, **Sector Rotation** and **RRG** now have their own tabs
  along the top.
- Updates itself every ~2 minutes; **Refresh** forces it.
""",
    "/sentiment/sectors": """
**Sector & Industry — the simple version**

How each S&P sector — and the industries inside it — is performing.

- **Day / Week / Month %** — return over each window, green up / red down.
- **P/C** — put/call volume (call-heavy green, put-heavy red); **RRG** — the sector's
  rotation quadrant.
- **Click a row** (or **Expand All**) to see that sector's industries.
- **Summary line** — % of sectors green, cap-weighted move, and a 0–10 score.
- **Refresh** re-pulls the data (also refreshes on its own).
""",
    "/sentiment/rotation": """
**Sector Rotation — the simple version**

Shows which sectors money is rotating **into** or **out of**, vs the S&P 500.

- **Risk-ON / OFF headline** — are the leaders aggressive (cyclical) or defensive
  sectors?
- **Quadrant map** — every sector ranked by rotation momentum.
- **Rotating From / Into** — sectors crossing between quadrants, with their S&P
  weights. The **RRG** chart is on its own tab. **Refresh** only (no auto-update).
""",
    "/sentiment/rrg": """
**RRG — the simple version**

The Relative Rotation Graph: each sector's momentum vs the S&P 500, over time.

- **The plot** — top-right is **Leading**, bottom-right **Weakening**, bottom-left
  **Lagging**, top-left **Improving**.
- **Each sector** draws a trail; the **bright dot** is now, the faded line is where
  it came from. **Hover** one to dim the rest.
- **Risk-ON / OFF headline** gives the one-line summary. **Refresh** only.
""",
    "/sentiment/momentum": """
**Momentum — the simple version**

Which sectors, industries and stocks are actually moving — and whether the
current market pays for chasing them. Recomputed **once a night**, not live.

- **The banner is the first thing to read.** *Favorable* = momentum's home turf.
  *Neutral* = chop, so the score leans on a shorter lookback. **Suppressed** =
  momentum-crash risk (a volatile rebound off a low, where the biggest losers rip
  hardest) — the leaderboard below dims, and that is deliberate.
- **The scatter** — right means strong, up means still accelerating. Top-right is
  **Leading**, top-left **Improving** (turning up early), bottom-right **Weakening**
  (strong but fading — late, don't chase), bottom-left **Lagging**. Same four names
  as the **RRG** tab, though the axes differ: RRG measures strength purely against
  the S&P, while this score blends five things of which relative strength is one.
  Switch between industries and stocks with the dropdown.
- **The ribbon** shows rank over recent sessions — steady climbers beat one-day pops.
- **The tables** show every component behind the score (trend, relative strength,
  acceleration, path quality, participation) so you can see *why* something ranks.
  **Align** shows three blocks — sector, industry, stock — filled when each is in
  its top quartile. Three filled blocks is the highest-conviction row.
- **Participation** is how many of an industry's five constituents are above their
  own 50-day average. Leadership that its own members don't confirm is thin.
- **Excluded count** at the bottom lists names that were dropped (too illiquid, too
  little history, or no quote at all) — that is how a renamed or delisted ticker
  becomes visible instead of silently vanishing.
""",
    "/trade": """
**Trade Analyzer — the simple version**

A **Buy / Hold / Sell** read on a single stock, for two horizons.

- **Position (1–8 weeks)** vs **Investor (months+)** verdicts. These sit
  side-by-side with the **Markov Forecast** as three equal cards.
- **Position is now backtested** — instead of a hand-tuned score, it ranks the
  stock on factors that were *tested against real forward returns*, then places it in
  a **calibrated band**. The headline shows what that band has historically meant:
  an **expected return** over the next ~4 weeks — **excess vs the S&P** (how much it
  beat or trailed the index, not the raw move) — and how often it **beat the S&P**
  (e.g. "+1.3% excess / 20d · 52% beat-SPY"). Open **"Why — validated factors"** to see each
  factor's pull and the model's own track record (its out-of-sample accuracy). The
  old hand-tuned verdict is still there under **"Legacy heuristic"**.
- **Investor (months+)** still shows a verdict with a score and its top reasons.
- **Markov Forecast** — looks at where the Position score has historically *travelled*
  and forecasts where it's likely to head: the colored bars show the odds of drifting
  toward **BUY** or **SELL** over the next 5 / 10 / 20 days, and a small **tilt** nudges
  the Position score up or down (the Buy/Hold/Sell **word** itself never changes — the
  tilt is advisory).
- **Hard gates (⛔)** — deal-breakers that override the score.
- **MTF alignment / Momentum / Fundamentals** — the evidence behind the verdict.
- Type a symbol and press **Analyze**.
""",
    "/portfolio": """
**Portfolio — the simple version**

Your **real** Schwab holdings, with context.

- **Holdings / Sectors / Performance** — the tabs at the very top of the page.
- **Holdings** — live P&L plus how each name is doing vs its sector.
- **Sectors** — are you over- or under-weight vs the S&P?
- **Performance** — letter grades per position; click one for advice.
- P&L streams live; **Refresh** rebuilds the grades.
""",
    "/driver": """
**Claude Trades — the simple version**

An autonomous **paper** options trader: Claude picks and sizes defined-risk credit
spreads, code-enforced guardrails cap the risk. Nothing goes to a live account.

- **Enable / Disable** — turn the autonomous trader on or off.
- **STOP** — halt new trades for the rest of the day (open positions keep managing).
- **Run now** — fire one decision checkpoint immediately.
- **Day P&L** progresses toward the $500 target; the decision log shows what it did.
- **Performance** — the driver's closed trades and realized P&L (win rate, profit
  factor, P&L by symbol/strategy). **Refresh** reprices now.
""",
    "/market": """
**Market Dashboard — the simple version**

A live grid of macro tickers, grouped into framed panels by category.

- **Colored tiles** — green means risk-on (up), red means risk-off, grey is flat
  or has no data. Fear gauges like VIX flip: up = red.
- **Options Sentiment** — **Put/Call** (cap-weighted sector ratio) and **Net Prem**,
  the dollar-weighted call-vs-put **premium** across the ~45 collected symbols
  ("Call 46%" = more money through calls, green). It's a money-weighted Put/Call,
  not net buying (Schwab has no tape).
- **Premium sublines** — the index (SPX/NDX), broad-ETF (SPY/DIA/QQQ/IWM) and
  mega-cap tiles show a small **call/put premium** line ("Call 37%"/"Put 11%")
  for that name; the **BIG10** tile (in the Top 10 frame) shows the net of its 10
  members (the Mag-7 plus AVGO/PLTR/AMD). A dash means that name isn't in the
  collected universe.
- **Auto-updates** roughly every 2 seconds during market hours.
""",
    "/eod": """
**EOD Report — the simple version**

An end-of-day summary of the day's options activity and Claude Trades (the autonomous driver).

- **Summary tiles** plus per-section tables (scanner, captured, paper, driver).
- **Generate** saves a dated HTML snapshot you can reopen later.
""",
    "/status": """
**System Status — the simple version**

Shows whether each part of the app is alive.

- **Green/red cards** — Memurai, the Schwab gateway, your Schwab login, the six
  services, and the web app.
- **Data freshness** — flags data that's gone stale.
- **Restart** brings an offline piece back; **Authorize** re-logs into Schwab.
""",
    "/settings": """
**Settings — the simple version**

Controls the alert chimes, notifications, the ticker, and the app's look.

- **Audio alert / sound / volume** — what plays when new signals appear.
- **Market-hours only / minimum score** — when the app is allowed to bother you.
- **Ticker** — the scrolling market-summary bar at the bottom of every page.
- **Appearance** — every color, font, and menu style, editable in-app (tabs of
  clickable color swatches). **Save & restart web GUI** applies the change.
- **API usage** — how many calls the app made to Schwab (counted at the
  gateway) and to Claude (counted at each call site), today / this week /
  this month.
- **Maintenance** — **Vacuum GEX history DB** shrinks the intraday options
  database on disk (run it after hours; it locks the file for minutes).
- **Test sound** also unlocks browser audio. The **User Manuals** live here too.
""",
    "/manuals": """
**User Manuals — the simple version**

Links to the full documentation (each opens in a new tab).

- **User Guide** — how to use everything.
- **Technical Reference** — the math behind every number.
- **API / Developer Reference** — for developers extending the app.
""",
    "/terminate": """
**Stop All Services — the simple version**

A big red stop button for the whole local stack.

- Confirming stops the gateway, the services, and this web app — the page then goes
  unresponsive (that's expected).
- Re-launch with `start_all.bat`. Memurai keeps running.
""",
}

# /eod/detail shares the EOD guide.
HELP_MD["/eod/detail"] = HELP_MD["/eod"]

_DEFAULT = (
    "**Quick help**\n\n"
    "Use the left nav to move between pages. Hover this **?** on any page for a "
    "plain-language guide to that page and how its settings change the results."
)


def help_md(active: str) -> str:
    """Return the idiot's-guide Markdown for a route (falls back to a default)."""
    return HELP_MD.get(active, _DEFAULT)


# ---------------------------------------------------------------------------
# Per-sub-tab hover help.
#
# The pages that carry a second row of "view" tabs under the main strip
# (Scanner, Dealer Positioning/Gamma, Simulator, Rescue, Portfolio) attach an
# individual hover tooltip to each sub-tab so a reader can learn what GEX / Charm
# / Replay / etc. mean without leaving the page. Keyed by route -> {tab value:
# plain-text tooltip}. The tab *value* is what the page passes to ``ui.tab(...)``
# (NOT the display label) — for Gamma that's the internal view key ("GEX",
# "Charm", …) and the Net-Prem group keys ("indices", …).
#
# Keep these to ONE or TWO plain sentences (tooltips render as plain text, so no
# Markdown), focused on what that view/tab shows and when to reach for it.
# ---------------------------------------------------------------------------

SUBTAB_HELP: dict[str, dict[str, str]] = {
    "/": {  # Market Scanner
        "0-DTE": "Credit spreads that expire TODAY — fastest decay, highest risk "
                 "(zero days to expiration).",
        "Swing": "Credit spreads days-to-weeks out — slower decay, more room to be "
                 "right.",
        "Directional": "Single-leg long or short calls/puts — a plain bullish or "
                       "bearish bet, scored on fit + quality (not the premium model).",
    },
    "/options/gamma": {  # Dealer Positioning — the analytics lenses
        "GEX": "Gamma exposure by strike — where dealers must hedge. Big positive "
               "walls tend to pin price; negative gamma amplifies moves.",
        "Charm": "Charm — how dealer delta drifts purely from time passing. Shows "
                 "the hedging pull that builds into the close.",
        "DEX": "Delta exposure by strike — the net directional hedge dealers carry. "
               "Its zero-crossing is the gamma 'flip'.",
        "Vanna": "Vanna — how dealer delta shifts when volatility (IV) changes. "
                 "Matters most on big IV moves.",
        "Flow": "Intraday options flow for this symbol — price plus cumulative call "
                "vs put premium, and the net (call minus put).",
        "Net Prem": "Net options premium (call $ minus put $) for many symbols at "
                    "once — pick from 28 across indices, sectors and mega-caps.",
        "Term": "Term structure — the same exposure across the next several "
                "expirations, as a strike x expiry heat map.",
        # Net Prem group sub-tabs (they filter the symbol picker):
        "indices": "Filter the symbol list to indices & broad ETFs ($SPX, $NDX, "
                   "BIG10, SPY, QQQ, IWM, DIA). Your ticked symbols stay selected.",
        "sectors": "Filter the list to the 11 SPDR sector ETFs (XLB…XLY). Your "
                   "ticked symbols stay selected across groups.",
        "megacaps": "Filter the list to the ten mega-caps (NVDA, AAPL, MSFT…). "
                    "Your ticked symbols stay selected across groups.",
    },
    "/options/simulator": {
        "Replay": "Replay — how the whole position would have behaved along the "
                  "underlying's recent price path, bar by bar.",
        "What-if": "What-if — slide the price up/down and fast-forward days to watch "
                   "the value and the Greeks change.",
        "IV shock": "IV shock — multiply implied volatility to see vega risk (how "
                    "much a volatility move helps or hurts the position).",
    },
    "/options/rescue": {
        "At-Risk Board": "Your paper credit spreads that are tested or critical, "
                         "ranked by heat, each with ranked fix-it options.",
        "Ad-hoc Trade": "Paste in any credit spread (not from the paper book) to see "
                        "advisory rescue ideas for it.",
    },
    "/portfolio": {
        "Holdings": "Each position's live P&L and how it's doing versus its sector.",
        "Sectors": "Your sector weights versus the S&P benchmark (over/under-weight).",
        "Performance": "Per-position letter grades and a composite; click a row for "
                       "advice.",
    },
}


def subtab_help(route: str, tab_value: str) -> str:
    """Return the plain-text hover tooltip for a page's sub-tab (or "" if none)."""
    return SUBTAB_HELP.get(route, {}).get(tab_value, "")
