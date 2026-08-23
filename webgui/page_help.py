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
    "/desk": """
**Desk — the simple version**

The **home page**. One screen answering, top to bottom, the four questions you ask
in order: *what is the market doing · where is the structure · what should I act
on · what am I holding.*

- **Top strip** — the clock, **VIX** and its band, the **market regime** word
  (Rallying / Balanced / Whipsaw / Stressed…), and two dials showing **Day / Week
  / Month** for market **sentiment** and market **trend**. These are the same
  numbers the Sentiment page shows — the Desk never computes its own.
- **Dealer Positioning** — one row each for **$SPX, SPY, QQQ, $NDX**: price, the
  **gamma flip** and how far price sits from it, the **call and put walls**, and
  net gamma exposure. The little bar shows where price sits **between the two
  walls**. *Long gamma · pins* means dealer hedging tends to **hold** price near
  those walls; *short gamma · runs* means it **amplifies** moves instead.
- **Opportunity Board** — the five hottest names right now, with what makes each
  one interesting, its at-the-money implied volatility and whether that is rising
  or falling, and a setup tag when one is active.
- **Live Flow Alerts** — the five newest unusual-options events. Note these show
  **call or put**, never *bought* or *sold*: Schwab publishes no time-and-sales
  tape, so nobody can honestly say which side traded.
- **Positions** — your open paper trades and Claude's, together, with the live
  mark and profit or loss, and a flag: **OK**, **Watch**, **At risk**, **Rescue**.
  The header totals open trades, unrealised profit and loss, and how many need
  attention.

**Click any row** to open the page it came from, already set to that symbol.
Nothing on this page places or changes a trade.

⚠ **After the close the walls are hidden, not zeroed.** Index option open interest
reads 0 overnight, which would otherwise produce confident-looking walls that are
pure noise. A greyed panel with a timestamp means "this is the last good reading",
not "the market is flat".
""",
    "/desk/live": """
**Live Mirror — the simple version**

**The Desk, on a screen you are not sitting at.** Same numbers, same layout, built
as a plain web page instead of an app page — so it keeps updating on a wall
display, a spare monitor or a phone, and picks itself back up after the machine
sleeps or the network blinks. It opens in a **new tab**, so the tab you were
working in stays where it was.

- **Same numbers, always.** Every figure is produced by the Desk's own code, so
  the mirror cannot quietly disagree with the Desk. If the two ever differ, one of
  them has stopped updating — check the dot.
- **The dot, top right.** **Live** means the stream is connected and the screen is
  current. **Reconnecting** means it dropped and is retrying by itself; the numbers
  on screen are the last good ones until it says Live again.
- **The clock keeps ticking even when nothing else changes** — that is deliberate.
  A frozen page and a quiet market look identical, and the second hand is how you
  tell them apart at a glance from across the room.
- **The four panels stay 2x2 at every width**, unlike the Desk, which stacks them
  into one column on a narrow screen. A display you have pinned something to should
  not rearrange itself.

**Click any row** to open the page it came from. **Open the full Desk** (top left)
goes back to the real app.

This screen is **read-only**. It places no trades and changes nothing.
""",
    "/options/scanner": """
**Market Scanner — the simple version**

Finds option trades across the watchlist and scores each one **0–100** for
quality — mostly **credit spreads** (you sell risk and collect cash up front),
plus single-leg directional trades on their own tab.

- **0-DTE / Swing / Directional** — the small tabs at the very top (under the page
  tabs): expiring today, over several days, and single-leg long/short calls and
  puts. Directional only lists trades that clear a quality bar, so an empty tab
  means "nothing qualified today", not a failure.
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
- Only candidates that clear a **quality bar** are listed. The status line says how
  many were cut, which is what tells "everything failed the bar" apart from
  "nothing was found at all".
""",
    "/options/calculator": """
**Calculator — the simple version**

Shows what an options trade makes or loses **before** you place it — any
**multi-leg** structure. Work down the three numbered boxes on the left; the
results appear on the right.

- **① Strategy** — pick a template (single, vertical, **iron condor**,
  **butterfly**, **calendar/diagonal**…). The chips say whether it takes in a
  **credit** or costs a **debit**, how many legs it has, and its lean; the line
  under them is the trade's thesis in one sentence.
- **② Symbol** — type a ticker and tab out (or press **Load chain**). The pill
  top-right says whether a chain is loaded. **Price, IV %, IV Δ, Rate,
  Contracts, Strikes** and **Expiry** live here — Expiry sets *every* leg's
  expiry. Higher IV = pricier options and wider P&L swings. **IV Update** works
  the volatility back out of the traded contract's own price, the way
  ThinkorSwim does.
- **③ Legs** — one card per leg: type, side, expiry, strike, quantity, premium,
  and the leg's **delta** from the chain. The strip on the frame keeps a running
  **leg count, net premium and max loss**. A dash means *not known yet*, not
  zero: premiums are blank until you press **Fetch Premiums**, and delta is
  blank whenever the chain carries no Greeks — normal outside market hours.
- **Calculate** fills the **six cards** — entry credit/debit, max risk, max
  return, return on risk, breakeven(s), probability of profit — and the **P&L
  matrix** under them: one row per real strike, one column per date from **Now**
  (today's mark-to-market) to **Exp** (the payoff at expiration), green = profit,
  red = loss, spot row in amber. Calendars price each leg at its own expiry.
- Each date column shows **dollars and a percentage**, and the heading says what
  the percentage is *of*: **% MAX** (of the most the trade can make) when the
  payoff is capped, **% COST** (of what you paid) when it isn't, and a plain
  **%** with dashes when neither applies.
- **Unlimited** on a card is real, not an error — a long call's upside and a
  naked call's risk have no cap.
- Loading a **different** symbol clears the cards and matrix; reloading the same
  one keeps them. **Copy to Simulator** sends the exact legs across. Widening
  strikes raises the credit you collect but also the max loss.
""",
    "/options/gamma": """
**Dealer Positioning — the simple version**

Shows where option **dealers** must buy or sell to stay hedged — which can pin or
accelerate price.

- **Symbol dropdown** — pick the index or stock. The page **remembers** your last
  symbol when you come back, and switching symbols refreshes automatically.
- **Gamma / Charm / Delta / Vanna / Flow / Net Prem / Term** — the small tabs at
  the very top (under the page tabs): different lenses on dealer positioning; the
  **walls** mark likely support/resistance. The header names the one you're on,
  e.g. "Markets › Dealer Positioning › Gamma".
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
- **Big delta** — one contract carries an outsized share of that symbol's whole
  directional exposure; the **Share** column is how big a share.

None of these can tell a **buy** from a **sell** — Schwab publishes no options
tape — so read every row as "something large happened here", not as a direction.

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

- **Newest first** — the table opens with your most recently captured signal at
  the top. Click any column heading to re-sort it.
- **Rec** — green = take profit, red = cut, amber = hold.
- **Credit vs Cur Price** — what you took in against what it would cost to close
  now; **P&L** is the difference, green in profit and red in loss.
- **Refresh marks (live)** re-prices everything against fresh chains; you're
  alerted when a stop or target is hit.
- Click a row to load it into the **detail panel** on the right, and to pick it
  for **Close selected**.
- **The footer** under the table sums the day: how many signals were captured and
  closed today, the **booked** P&L of today's closes, and the **open** P&L across
  every signal still running. Open P&L shows a dash, not $0.00, until the signals
  have been priced — hover it to see how many of them carry a live mark.
""",
    "/options/portfolio": """
**Paper Account — the simple version**

The account behind the automated paper-trading engine.

This is the **engine's own account** — it opens and closes positions on its own.
Trades you sent by hand live on **Paper Ledger**.

- **Cards** — equity, cash, P&L, open count, engine status.
- **Run entry / manage cycle** — open new positions from captured signals, or
  re-price and auto-close existing ones. This also runs on its own **at the top of
  each hour, 09:00–14:00 CT** (there is no 15:00 run) — so a target hit at 09:15 is
  acted on at 10:00 unless you press **Run manage cycle** yourself.
- **Reset** sets a new starting balance.
""",
    "/sentiment": """
**Sentiment — the simple version**

A **0–10** read on market mood. It's **contrarian**: a high score means lots of
fear, which can mean opportunity.

- **Sentiment ring** — three arcs on one dial: **Day** (right now), **Week** (the
  last 5 sessions' average) and **Month** (the full history's average), so you can
  see today against its own recent normal.
- **Market Trend ring** — the same three horizons for direction, 0–100
  (50 = neutral, 100 = strong bull).
- A horizon with **no usable reading** draws its track and an em-dash rather than
  a number. That is deliberate: a missing input used to render as a confident
  value, and an empty arc is the one thing a needle cannot say.
- **Components** — press and hold to see what's driving the score.
- **Market Regime** — the *character* of the tape: **Balanced** (quiet, price
  pinned near its mean), **Trending**, **Breakout**, **Whipsaw** (plenty of
  movement, no progress) or **Stressed** (fear — high VIX, inverted term
  structure). The panel below **ranks** the five by how much of today's tape each
  one holds — a bar against the leader, a sparkline of its own session, and how
  far it has moved since the open. The footer names the leader's **margin over the
  runner-up**, which is the number that says whether the headline was nearly a
  coin toss. When one regime is taking over you'll see a line like
  "Balanced → Rallying · 60%".
- **Direction on the regime** — Trending and Breakout also say *which way*:
  **Rallying** or **Firming** up, **Retreating** or **Softening** down, and
  **Breakdown** for a break to the downside. That word appears only when the
  tape's own slope and the Market Trend gauge agree — when they don't it stays
  the plain name, so this panel can never contradict the gauge above it.
  Balanced, Whipsaw and Stressed have no direction by nature. Recomputes every
  5 minutes during market hours; outside them it holds the last read. Says
  "Unclear" when the evidence is genuinely weak rather than guessing.
- **Daily Sentiment & Trend** — the two color-coded intraday graphs (last 5 days).
- **Sector & Industry**, **Sector Rotation** and **RRG** now have their own tabs
  along the top.
- Updates itself every ~2 minutes; **Refresh** forces it.
""",
    "/sentiment/sectors": """
**Sector & Industry — the simple version**

How each S&P sector — and the industries inside it — is performing, painted as a
heat band you read down the page rather than a table you read across.

- **Day / Week / Month** — return over each window, filled green up / red down.
  **The colour is the size of the move, not just its direction.**
- **Intensity is judged per column.** Day is compared against the day's own
  spread, Week against the week's, Month against the month's — so a strong day
  looks strong even in a quiet month.
- **Small moves stay dark on purpose.** Under ±0.50% (Day), ±1.00% (Week) or
  ±1.50% (Month) a cell reads flat, so only real moves light up.
- **The rank line** under each sector name restates its position in the pack, on
  whichever column you are sorted by.
- **Click Day, Week or Month** to sort by it; click again to reverse.
- **Click a row** (or **Expand all**) to see that sector's industries.
- **P/C** — put/call volume, plain number, tinted amber above 1.5 (put-heavy).
  It is a ratio, not a return, so it deliberately gets no heat tile.
- **Rotation quadrants** are no longer here — the **RRG** and **Sector Rotation**
  tabs show them properly.
- **Refresh** re-pulls the data (it also refreshes on its own).
""",
    "/sentiment/bullbear": """
**Bull / Bear Map — the simple version**

Where the market is strong and weak, as a tree: eleven sectors, the industries
inside each, and the stocks inside those. Click a row to open the level below it.

- **"Bullish" is two facts, not one**, and this page never merges them.
  **Trend** asks whether price is genuinely rising. **vs SPY** asks whether it is
  beating the index. A name can do one without the other.
- **The quadrant chip** is those two facts combined. **Rising · Leading** is
  unambiguous strength; **Falling · Lagging** unambiguous weakness.
  **Falling · Leading** is the one to watch — it is going DOWN, just less than
  the index, and a screen that ranks only on relative strength calls it a buy.
  **No reading** means the cascade could not score that row; it is not neutral.
- **The headline counts rather than judges.** "5 of 11 sectors rising and
  leading" is arithmetic about the rows on screen. There is deliberately **no
  risk-on / risk-off verdict here** — the **Sector Rotation** tab owns that.
- **Breadth** is the share of a group's members confirming the move. A sector
  rising on a quarter of its constituents is a fragile advance, and the bar turns
  red to say so. **Stocks have no breadth bar** — a stock has no members. A dash
  means no reading at all, which is not the same as 0%.
- **Two clocks, and they mean different things.** Trend, vs SPY and Breadth come
  from **last night's** cascade — momentum needs months of history, so there is
  no intraday version of them. Only **Today** is live. If the quote line says
  quotes are unavailable, only the Today column is affected.
- **Refresh** re-pulls the live quotes and republishes the map.
""",
    "/sentiment/rotation": """
**Sector Rotation — the simple version**

Shows which sectors money is rotating **into** or **out of**, vs the S&P 500.

- **The verdict** — Risk-on or Risk-off, in one word, with a sentence saying what
  that means.
- **The gauge** is the whole argument in one picture. Cyclical sectors' momentum
  versus defensive sectors' momentum, on a −3 to +3 scale. The bar runs from zero
  to wherever the reading landed: **left of centre is risk-off, right is risk-on**,
  and the further out, the stronger. The two ticks at ±1.50 are the triggers — a
  reading has to clear one for a rotation to be called at all.
- **The sentence under the spread** tells you whether it *just* cleared the
  trigger (a fresh signal, easy to reverse) or is well past it (entrenched).
- **The band** answers "how much of the index is actually moving?" Every sector is
  a block, and **the width is its weight in the S&P 500** — so Technology at 32%
  is a third of the bar on its own. Red side is rotating out, green side in, and
  the two totals tell you the split. Thin slivers drop their labels; hover isn't
  needed, they are in the panels below.
- **The four panels** are the RRG quadrants, each showing what share of the index
  sits in it. **Leading** is where money is going, **Lagging** where it is coming
  from; **Improving** is turning up early, **Weakening** is rolling over. Inside
  each, one card per sector with its RS-Momentum and a bar for its index weight —
  **all the bars share one scale**, so a long bar means a heavy sector, always.
- The **RRG** chart itself is on its own tab. **Refresh** only (no auto-update).
""",
    "/sentiment/rrg": """
**RRG — the simple version**

The Relative Rotation Graph: where every sector sits against the S&P 500, and
which way it is heading.

- **The four tinted areas are the answer.** Top-right **Leading** (strong and
  still strengthening), top-left **Improving** (weak but turning up), bottom-left
  **Lagging** (weak and getting weaker), bottom-right **Weakening** (strong but
  rolling over). Sectors tend to travel clockwise around the centre.
- **Left–right is strength** (RS-Ratio, 100 = matching the S&P). **Up–down is
  momentum** (RS-Mom). The crosshair is always at 100/100.
- **Dot size is the sector's weight in the S&P 500** — by area, so Technology is
  visibly the elephant. A big dot drifting into Lagging matters far more than a
  small one doing the same.
- **The trail is its last five readings**, drawn as a smooth curve that is faintest
  and thinnest at the oldest — so the trail points the way it is travelling. It
  bends only between readings; it always passes through the real ones.
- Each marker is labelled with its **sector name**.
- **The strip at the top** is the verdict, with the cyclical-versus-defensive
  numbers behind it.
- **Refresh** only — this changes slowly by design.
""",
    "/sentiment/momentum": """
**Momentum — the simple version**

Which sectors, industries and stocks are actually moving — and whether the
current market pays for chasing them. Recomputed **once a night**, not live.
The page reads as five numbered steps, top to bottom.

- **1 · Is it worth trading today?** All three states are shown side by side and
  the live one is enlarged, so you can see what today *isn't* as well as what it
  is. *Favorable* = momentum's home turf. *Neutral* = chop, so the score leans on
  a shorter lookback. **Suppressed** = momentum-crash risk (a volatile rebound off
  a low, where the biggest losers rip hardest) — stand aside. Each card ends with
  what to actually do about it.
- **Dispersion** underneath is how spread out the field is, as a percentile. Low
  dispersion means everything is moving together, so a relative-strength screen
  has little to separate — the score still computes, it just matters less.
- **2 · Three levels.** How many names in each universe are in their own top
  quartile. The green panel counts the **stocks whose industry and sector both
  confirm** — the highest-conviction rows on the page — and **lists them by
  rank** underneath. Clicking one decomposes it in section 4; because these are
  stocks, it switches the level selector to Stocks to do it. Hover a ticker for
  its sector and industry.
- **3 · Where the names sit.** The four quadrants as counts: **Leading** (strong
  and still accelerating), **Improving** (weak but turning up), **Weakening**
  (strong but fading — late, don't chase), **Lagging** (weak and decelerating).
  The strongest few are named in each; **+N more** opens the rest of that
  quadrant. **Click any name** to decompose it in the section below.
  Same four names as the **RRG** tab, though the axes differ: RRG measures
  strength purely against the S&P, while this score blends five things of which
  relative strength is one.
- **4 · What a score is made of.** Whichever name you clicked, decomposed into its
  five z-scores (trend, relative strength, acceleration, path quality,
  participation) — the current leader until you pick something else, and
  **Top ranked** puts it back. Leaderboard rows are clickable too.
  Bars run either side of a centre line, which is the universe average.
  **Participation** is how many of an industry's constituents are above their own
  50-day average — leadership its own members don't confirm is thin.
- **5 · Rank over recent sessions.** Steady climbers beat one-day pops. A short
  line means that name has fewer stored sessions, not a shorter trend.
- **Full leaderboard** is collapsed at the bottom — open it for the ranked table
  with every component column.
- The **limits** cards spell out what this page cannot tell you, and the footnote
  counts names that were dropped (too illiquid, too little history, or no quote)
  — that is how a renamed or delisted ticker becomes visible instead of silently
  vanishing.
""",
    "/trade": """
**Trade Analyzer — the simple version**

A **Buy / Hold / Sell** read on a single stock, for two horizons.

- **Position (1–8 weeks)** vs **Investor (months+)** verdicts, side by side.
- **Position is now backtested** — instead of a hand-tuned score, it ranks the
  stock on factors that were *tested against real forward returns*, then places it in
  a **calibrated band**. The headline shows what that band has historically meant:
  an **expected return** over the next ~4 weeks — **excess vs the S&P** (how much it
  beat or trailed the index, not the raw move) — and how often it **beat the S&P**
  (e.g. "+1.3% excess / 20d · 52% beat-SPY"). Open **"Why — validated factors"** to see each
  factor's pull and the model's own track record (its out-of-sample accuracy). The
  old hand-tuned verdict is still there under **"Legacy heuristic"**.
- **Investor (months+)** still shows a verdict with a score and its top reasons.
- **Hard gates (⛔)** — deal-breakers that override the score.
- **MTF alignment / Momentum / Fundamentals** — the evidence behind the verdict.
- **Deep Dive** opens a full technical + fundamental + options report in a new tab;
  **AI Query** opens the same digest as a chat prompt you can copy.
- Type a symbol and press **Analyze** (tabbing out of the field does it too).
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
- **Day P&L** progresses toward the day's target. That target is **not fixed**: the
  base is $500, ratcheted against the month-to-date pace — up to $1,000 when behind,
  down to $250 when ahead — so the number in the tile moves day to day. A $1,500
  daily loss halts new entries. The decision log shows what it did and why.
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
- **Ranked frames** — **Broad-Market ETF**, **Top 10**, **Sector SPDR**,
  **Thematic / Industry** and **Countries** re-order themselves by the day's
  move, biggest gainer first, so the leaders and laggards are always at the ends.
  Every other frame keeps its curated order (VIX before its tenors; the cash
  indexes paired with their futures) because that layout is itself information.
- **Advancing / Declining** (top rail) — a breadth count across those same
  ranked **stock** frames only, so a bid VIX or a rallying Treasury doesn't count
  as a decline. The BIG10 tile is skipped, since it's the average of the ten
  mega-caps already counted beside it.
- **Skin toggle** (top right) switches between the two board looks; your choice
  is remembered.
- **Auto-updates** every ~3 seconds during market hours, ~15 outside them —
  futures trade nearly around the clock, so off-hours stays live.
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
- **Data freshness** — flags data that's gone stale. A view is only judged when
  its publisher is actually due to run: the **scanner** only scans during the
  session, so overnight and at weekends its age is left alone rather than
  reported as a fault. Everything that publishes round the clock is still checked
  round the clock, with a longer allowance outside market hours.
- **Restart** brings an offline piece back; **Authorize** re-logs into Schwab.
""",
    "/settings": """
**Settings — the simple version**

Controls the alert chimes, notifications, the ticker, and the app's look.

- **Audio alert / sound / volume** — what plays when new signals appear.
- **Market-hours only / minimum score** — when the app is allowed to bother you.
- **Ticker** — the scrolling market-summary bar at the bottom of every page.
  Switching it off also stops the app paying for its Claude-written verdict, so
  it is a cost control as well as a display one.
- **Appearance** — every color, font, and menu style, editable in-app (tabs of
  clickable color swatches). **Save & restart web GUI** applies the change.
- **API usage** — how many calls the app made to Schwab (counted at the
  gateway) and to Claude (counted at each call site), today / this week /
  this month.
- **Maintenance** — **Vacuum GEX history DB** shrinks the intraday options
  database on disk (run it after hours; it locks the file for minutes).
- **Test sound** also unlocks browser audio (browsers block it until you click
  something). The **User Manuals** are a tab under **More**, not here.
""",
    "/manuals": """
**User Manuals — the simple version**

Links to the full documentation (each opens in a new tab).

- **User Guide** — how to use everything.
- **Reference Guide** — what each tab and sub-tab is *for*, why it matters and when
  to open it. Start with its one-page summary.
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
    "/options/scanner": {  # Market Scanner
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
