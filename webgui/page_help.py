"""Per-page "idiot's guide" help text.

Shown in a hover tooltip on the ``?`` button in the header (see ``main._layout``),
keyed by the page's active route. Each value is Markdown — keep it short, plain,
and focused on (a) what the page is for and (b) how changing the key parameters
changes the outcome. ``help_md(active)`` returns the guide for a route (or a
sensible default).
"""

HELP_MD: dict[str, str] = {
    "/": """
**Scanner — the simple version**

Finds option **credit spreads** (you sell risk and collect cash up front) and
scores each one **0–100** for quality.

- **0-DTE / Swing tabs** — trades expiring today vs over several days.
- **Score chip & Grade** — greener/higher means better reward-vs-risk, higher
  probability of profit, and better trend fit. Work from the top down.
- **Click a row** for full details; the row buttons send it to the Calculator,
  paper-trade it, or chart its Expected Move.
- It re-scans on its own — **Scan** just forces a refresh.
""",
    "/options/swing": """
**Swing Scanner — the simple version**

Like the Scanner, but you set the hunt parameters for one symbol over several days.

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
**Gamma — the simple version**

Shows where option **dealers** must buy or sell to stay hedged — which can pin or
accelerate price.

- **Symbol dropdown** — pick the index or stock. The page **remembers** your last
  symbol when you come back, and switching symbols refreshes automatically.
- **GEX / Charm / DEX / Vanna / Term** — different lenses on dealer positioning;
  the **walls** mark likely support/resistance.
- **Bars + heat map** — show ±20 strikes around spot, so the size stays steady as
  the day moves. **Press and hold** the left mouse button on the heat map to read a
  strike's value (it follows the cursor while held); plain hovering shows nothing.
- Positive gamma → price tends to **stick** near the walls; negative gamma →
  moves get **amplified**. Auto-refreshes every 2 minutes.
- After the close the **last session's** chart stays on screen until midnight
  (Friday's holds through the weekend), then clears for the next trading day.
- **Analyze** asks Claude to read the live $SPX / SPY / QQQ dealer positioning and
  opens an **infographic** in a new tab — a regime + bias gauge, a price-level
  ladder + key levels per index, a per-symbol **what-if** (rally / sell-off / chop),
  and a **why is this happening** at the bottom. It also runs on its own at
  **premarket, ~18 min after the open, midday, and the close** — the **Auto
  briefings** buttons open each. **Explain** opens a one-symbol infographic.
""",
    "/options/simulator": """
**Simulator — the simple version**

Re-prices a **multi-leg** option position under different what-ifs (Black-Scholes).

- **Fetch snapshot**, then pick a **Strategy** and adjust the **legs** (kind / side /
  strike / expiry / qty) — singles, spreads, condors, butterflies, calendars.
- **Replay** — how the whole position would have behaved along recent price moves.
- **What-if (ΔS / Δt)** — slide the underlying price or fast-forward **elapsed** days;
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
    "/options/paper": """
**Paper Trades — the simple version**

A practice ledger of option trades — **no real money**.

- **Each row** is a trade with its strikes, credit, max loss, and live P&L.
- **Analyze** re-prices it now and shows current Greeks; **Close** records an exit.
- Use it to test ideas from the Scanner without risk.
""",
    "/options/captured": """
**Captured Signals — the simple version**

Scanner signals you're **tracking over time** to see whether they're working.

- **Entry vs Current Score / Drift** — is the setup getting better or worse since
  you saved it?
- **Recommendation** — green = take profit, red = cut, amber = hold.
- **Refresh marks** re-prices everything live; you're alerted when a stop or target
  is hit.
""",
    "/options/portfolio": """
**Paper Portfolio — the simple version**

The account behind the automated paper-trading engine.

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
- **Components & sectors** — what's driving the score; expand a sector for its
  industries.
- Updates itself every ~2 minutes; **Refresh** forces it.
""",
    "/sentiment/rotation": """
**Sector Rotation — the simple version**

Shows which sectors money is rotating **into** or **out of**, vs the S&P 500.

- **Risk-ON / OFF headline** — are the leaders aggressive (cyclical) or defensive
  sectors?
- **RRG chart** — each sector's trail: top-right = leading, bottom-left = lagging.
- **Rotating From / Into** — sectors crossing between quadrants. **Refresh** only
  (no auto-update).
""",
    "/trade": """
**Trade — the simple version**

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

- **Holdings** — live P&L plus how each name is doing vs its sector.
- **Sectors** — are you over- or under-weight vs the S&P?
- **Performance** — letter grades per position; click one for advice.
- P&L streams live; **Refresh** rebuilds the grades.
""",
    "/driver": """
**Driver — the simple version**

The morning agent proposes trades; you approve or skip. Orders are **simulated**
(paper) — nothing goes to a live account.

- **Run morning agent** — grades the day and lists proposed trades with their risk.
- **APPROVE** (simulated fill) / **SKIP**.
- **Performance** — win rate and P&L of past decisions. It also runs itself at
  09:28 ET each weekday.
""",
    "/market": """
**Market Dashboard — the simple version**

A live grid of macro tickers, grouped into framed panels by category.

- **Colored tiles** — green means risk-on (up), red means risk-off, grey is flat
  or has no data. Fear gauges like VIX flip: up = red.
- **Auto-updates** roughly every 2 seconds during market hours.
""",
    "/eod": """
**EOD Report — the simple version**

An end-of-day summary of the day's options activity and the Driver.

- **Summary tiles** plus per-section tables (scanner, captured, paper, driver).
- **Generate** saves a dated HTML snapshot you can reopen later.
""",
    "/status": """
**System Status — the simple version**

Shows whether each part of the app is alive.

- **Green/red cards** — Memurai, the Schwab gateway, your Schwab login, the five
  services, and the web app.
- **Data freshness** — flags data that's gone stale.
- **Restart** brings an offline piece back; **Authorize** re-logs into Schwab.
""",
    "/settings": """
**Settings — the simple version**

Controls the alert chimes and notifications.

- **Audio alert / sound / volume** — what plays when new signals appear.
- **Market-hours only / minimum score** — when the app is allowed to bother you.
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
**Terminate — the simple version**

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
