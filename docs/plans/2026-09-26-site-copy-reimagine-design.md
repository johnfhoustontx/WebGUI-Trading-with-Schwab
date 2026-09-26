# neuralstrike.co — the wording, reimagined

**Date:** 2026-09-26 · **Status:** proposed, awaiting the owner's approval before any
page is edited.

**Request:** "Look at neuralstrike.co holistically, then reimagine the wording while
keeping the branding the same."

**Scope:** the five hand-owned pages under `deploy/site/` — `index.html`, `live.html`,
`report.html`, `gallery.html` and the head of `glossary.html` — plus the Tools menu
they share and the meta/social tags. **Out of scope:** the glossary body (generated
from `docs/manuals/glossary/glossary.md`, 187 terms, shared with the app), the market
report itself (rendered outside this repo), the social card image, and any copy inside
the app or on the live origin's own headers.

## What stays, because it IS the brand

These are the branding, and the request keeps them. Nothing below touches them:

| Element | Kept verbatim |
|---|---|
| The name and lockup | `Neural` / `Strike`, the Flip mark, the hero caption **"Dealers pinned to the flip"** |
| The headline | **"Most tools show you prices. This one shows you who has to trade next."** — it is also the social card and the Reference Guide's opening |
| The tagline | **"dealer flow, measured."** — promoted from the footer to the hero eyebrow and the page title, but not reworded |
| The thesis line | **"Somebody is always forced to hedge. That flow is measurable."** |
| The stance | **"no signal-selling"** — the one promise the brand doc and a test both make |
| The Rescue tool's name | **"Rescue my Sh\*tty trade"** — the owner's choice (commit `cbad2df`); its register is deliberate and it stays |
| Screen names | The Desk, Gamma Heatmap, Opportunity Board, Net Prem, … — product names, not copy |
| Nocturne | the voice it implies: restrained, declarative, no exclamation marks, no AI signifiers |

## The holistic read: five things the current copy gets wrong

1. **The site sells the product a visitor cannot get, and buries the one they can.**
   The hero, the first stat and both safety cards describe a program that "runs on your
   own computer and talks to your brokerage account". There is no build to download
   (the 2026-09-06 design says so). Meanwhile, since 2026-09-07 the site fronts a public
   origin with **seventeen screens and five interactive tools, no sign-in** — and that
   appears only as a nav button and a sub-page. The copy was written the day before the
   public screens existed and has never been re-centred on them. The hero's primary
   button still opens a folder of screenshots.

2. **Stated facts have drifted.** `live.html` says *"Twenty-four screens … Twenty are
   read-only … The other four are tools"*; the table publishes **17** (12 tiles + 5
   tools; Market News joined 2026-09-26). Its meta description says twenty-four too, and
   its head comment says fourteen. The gallery's meta description still lists "the daily
   briefings", dropped 2026-09-08. The live page's footer promises *"the only real money
   on screen is your read-only brokerage portfolio"* — on the public origin there is no
   portfolio on any screen. The tests pin the tiles against the source table, but no
   test reads the prose numbers, which is exactly why they rotted.

3. **The one instruction the page gives leads nowhere.** The pull-quote says dealer
   gamma is *"explained in plain language under Dealer Positioning"* — a heading in the
   app's manual, not on this site, and not a link. The glossary section that explains it
   is one click away and unmentioned.

4. **The disclaimer is said five ways.** The hero note, a safety card, the index footer,
   the gallery footer and the live footer each phrase "paper only, no orders" differently.
   The app itself keeps shared sentences in one place (`webgui/pages/copy.py`) for exactly
   this reason: two screens wording one condition differently reads as a defect.

5. **Small consistencies of voice.** The four lenses mix verb moods (*Measures / Watch /
   Describe / Turn*); "Evaluate your performance" is a caption nobody would say; "See
   value by price and days ahead" is a description of a chart rather than of what the
   Simulator is for; "App gallery" in the top nav is "Gallery" everywhere else.

## The reimagining, in one paragraph

Keep the brand's voice — mechanics, not magic; declaratives; second person; the Flip as
the one recurring image — and **re-centre every page on the reader who has just arrived:
a trader who can open seventeen live screens and five tools right now, for free, without
signing in, and who can never lose money here because nothing places an order.** The
local workbench becomes what it is on the site: the thing the live screens are a window
onto, mentioned where it earns its place (the read-only portfolio card) and nowhere else.
One paper line and one disclaimer line, reused verbatim on every page.

### The two canonical lines

- **Paper line** (hero note, live and gallery notes):
  *Nothing here places an order. Every position is paper, marked against the live market.*
- **Disclaimer** (every footer):
  *NeuralStrike — dealer flow, measured. Educational and simulation software, not
  investment advice. No orders are transmitted to any broker.*

### Voice rules applied throughout

- Lead with what the reader gets, then how it works, then what it will not do.
- Name the mechanism, never the intelligence ("measured", "forced", "mechanically" — never
  "AI", "smart", "signals").
- One idea per sentence. No exclamation marks. No numbers the tests do not pin.
- Where the copy states a count, it is a word the page can be held to
  (*seventeen*, *five*, *fifteen*) and a test should read it — see "Tests" below.

---

## index.html

### Head

| | Current | Proposed |
|---|---|---|
| `<title>` | NeuralStrike — local options workbench | **NeuralStrike — dealer flow, measured** |
| meta description | NeuralStrike is a local options workbench that measures dealer hedging pressure. Market conditions, trade candidates, pricing models and a paper account on one screen. Paper positions only; no orders are transmitted to any broker. | **NeuralStrike measures the hedging that market makers are forced to do and shows where it will push price next. Seventeen live screens and five tools, open to anyone with nothing to sign into. Paper positions only; no orders are ever sent to a broker.** |
| `og:title` | NeuralStrike — local options workbench | **NeuralStrike — dealer flow, measured** |
| `og:description` | Most tools show you prices. This one shows you who has to trade next. Dealer hedging pressure, measured — on your own machine, paper positions only. | **Most tools show you prices. This one shows you who has to trade next. Dealer hedging pressure, measured — live, in the open, paper positions only.** |

The social card image keeps its text (the claim); only the description beside it moves
off "on your own machine", which a visitor cannot do.

### Nav (all pages)

`App gallery` → **Gallery** (matches the crumb and the other pages). Everything else
keeps its label. Tools menu descriptions, shared by all four pages:

| Tool | Current | Proposed |
|---|---|---|
| Strategy Finder | Rank trades for any symbol | **Rank every trade for a symbol** |
| Rescue my Sh\*tty trade | Repair a trade you hold | **Repair a trade you already hold** |
| Calculator | Price any structure | *(kept)* |
| Simulator | See value by price and days ahead | **Value it across price and time** |
| Market News | Headlines, filings and insider buys, live | *(kept)* |

### Hero

| Piece | Current | Proposed |
|---|---|---|
| eyebrow | Local options workbench | **Dealer flow, measured** |
| h1 | Most tools show you prices. This one shows you who has to trade next. | *(kept — the brand)* |
| lede | NeuralStrike runs on your own computer and talks to your brokerage account through a small gateway. Market conditions, trade candidates, pricing models, a practice account and your real holdings — on one screen. | **Every option you buy, a market maker sells. They do not want your bet, only the fee, so they hedge — and as price moves they have to hedge again. NeuralStrike measures that forced flow across the whole chain and puts the reading, the trade it implies and a paper book to test it on one screen.** |
| primary button | See the screens → gallery | **Open the live screens** → `live.html` |
| ghost button | Learn dealer gamma → #idea | **How the flow works** → `#idea` |
| note | No real orders. Every position it opens is paper, priced against live market data. | **Nothing here places an order. Every position is paper, marked against the live market.** |
| figure caption | Dealers pinned to the flip | *(kept — the brand)* |

### Fact band

| Current | Proposed |
|---|---|
| **Local** / Runs on your machine | **Live** / Open to anyone, no sign-in |
| **One screen** / Conditions to decision | **Measured** / Flow, not opinion |
| **Paper trade** / Evaluate your performance | **Paper** / No orders, ever |

### The idea

| Piece | Current | Proposed |
|---|---|---|
| eyebrow | The one idea behind it | *(kept)* |
| h2 | Somebody is always forced to hedge. That flow is measurable. | *(kept — the brand)* |
| body | When you buy an option, a professional market maker — a dealer — usually takes the other side. Dealers do not want a directional bet. They want the fee. So they hedge. | **When you buy an option, a market maker — a dealer — is usually on the other side. Dealers do not want a directional bet. They want the fee. So they hedge, and the hedge is where the story starts.** |
| 01 | You buy the option / A dealer takes the other side of your trade, whether they like the direction or not. | **You buy the option** / A dealer takes the other side, whether or not they like the direction. |
| 02 | They hedge to stay neutral / They buy or sell the underlying stock so the position carries no view — only the fee. | **The dealer hedges flat** / They buy or sell the stock until the position carries no view. Just the fee. |
| 03 | Price moves, the hedge breaks / The required hedge changes with price. Staying neutral means trading again — mechanically. | **Price moves. The hedge is wrong again.** / The right hedge changes with every tick, so staying flat means trading again — mechanically, in a direction the math dictates. |
| 04 | That flow is visible / Large, predictable and sitting in the options chain — if you measure it. That is the whole app. | **That trading is visible** / It is large, it is predictable, and it is sitting in the options chain for anyone who measures it. Measuring it is the whole app. |
| pull-quote | If you only ever learn one concept here, make it **dealer gamma** — explained in plain language under Dealer Positioning. | If you learn one thing here, make it **dealer gamma**. [The glossary](glossary.html#dealer-positioning-and-flow) explains it without the jargon, and the [Gamma screen](https://live.neuralstrike.co/gamma) shows it live. |

The pull-quote gains the two links it was always describing.

### The four lenses

| Piece | Current | Proposed |
|---|---|---|
| eyebrow / h2 | Every screen, one idea / Four lenses on the same forced flow | *(kept)* |
| link | See all 15 screens → | *(kept — pinned by test against the gallery count)* |
| Dealer Positioning | Measures the hedging pressure directly — where dealers are pinned, where they amplify a move, and where they dampen it. | **Measures the pressure directly: where dealers are pinned, where they will amplify a move and where they will lean against it.** |
| Flow Alerts & Net Prem | Watch where the option money is actually going, not where commentary says it went. | **Watches where the option money is going right now, rather than where the commentary says it went.** |
| Sentiment & Market Regime | Describe the environment that pressure acts in — the same reading means different things in different regimes. | **Names the environment the pressure acts in. The same reading means different things on a trending day and a whipsaw one.** |
| Scanner, Strategy Finder & Rescue | Turn the reading into specific trades — candidates, structures priced against models, and repairs for positions that moved against you. | **Turns the reading into a trade: ranked candidates, structures priced against the models, and repairs for a position that has gone against you.** |

One mood throughout: each lens *does* something.

### Safety cards

| Piece | Current | Proposed |
|---|---|---|
| card 1 | **Paper by design** / It does not place real orders / Every trade the app opens is simulated — a paper position priced against real market data, so the practice account behaves like the real one without the consequences. | **Paper by design** / **It never places an order** / Every trade the app opens is simulated: a paper position marked against the real market, so the book behaves like a live one and costs you nothing to be wrong in. |
| card 2 | **Real money, read-only** / Your Schwab portfolio, untouched / The only real money on screen is your own holdings, pulled through a small local gateway and displayed read-only. Nothing on the page can move it. | **Real money, read-only** / **Your own holdings, untouched** / Run it on your own machine and the one real thing on screen is your Schwab portfolio, pulled through a small local gateway and shown read-only. Nothing on any screen can move it. |

Card 2 is the one place the local workbench is still described, and it is now framed as
the condition it is ("run it on your own machine"), not as what the visitor has.

### Close

| Piece | Current | Proposed |
|---|---|---|
| h2 | Start with the question, not the price. | *(kept)* |
| body | Every screen in the gallery is a real capture from the running app — the dealer-positioning read first, then what it implies and what to do about it. Start at Dealer Positioning and read down. | **The live screens run against the market all session, with nothing to sign into. Read Dealer Positioning first, then what the rest of the desk makes of it. The gallery holds a capture of every screen, including the ones that stay private.** |
| primary | Open the gallery | **Open the live screens** → `live.html` |
| ghost | Ask in Discord | **Browse the gallery** → `gallery.html` |

Discord loses its button here only because the Community block with its own Discord
button is the very next thing on the page.

### Community

| Piece | Current | Proposed |
|---|---|---|
| h3 | Read the flow with other traders | *(kept)* |
| body | Gamma reads, flow alerts and paper-trade post-mortems, posted as they happen. Questions welcome — no signal-selling. | **Gamma reads, flow alerts and paper-trade post-mortems, posted as they happen. Questions welcome. Nobody here is selling signals.** |
| buttons | Join the Discord / Telegram alerts bot | *(kept — the bot label is a documented decision)* |

### Footer

Current: *NeuralStrike — dealer flow, measured. Educational and simulation software. Not
investment advice; no orders are transmitted to any broker.*
Proposed: the canonical disclaimer (one comma moved, one sentence break — so it can be
byte-identical on every page).

---

## live.html

| Piece | Current | Proposed |
|---|---|---|
| meta description | Twenty-four NeuralStrike screens, running live against the market: dealer positioning, options flow, market regime, sector rotation and the desk itself, plus four tools that rank, repair, price and simulate a trade. No sign-in. | **Seventeen NeuralStrike screens running against the market with nothing to sign into: dealer positioning, options flow, market regime and sector rotation, plus five tools that rank, repair, price and simulate a trade and follow the news behind it.** |
| h1 | Running against the market, right now. | *(kept)* |
| lede | Twenty-four screens from the workbench, open to anyone with nothing to sign into. Twenty are read-only views of what the desk runs on. The other four are tools you use: the Strategy Finder ranks trades for a symbol, Rescue repairs a trade you hold, the Calculator prices any structure, and the Simulator shows what it is worth as the price moves and the days pass. Pick one to open it; the thumbnails are refreshed through the session. | **Seventeen screens from the desk, open to anyone. Twelve are the readings the desk runs on, from dealer positioning to sector rotation. Five are tools you drive: the Strategy Finder ranks every trade for a symbol, Rescue repairs one you already hold, the Calculator prices any structure, the Simulator shows what it is worth as price and time move, and Market News follows the headlines and filings behind it. Pick a screen to open it; the thumbnails refresh through the session.** |
| note | Quiet outside market hours: the screens show the last session until the next one opens. Want the whole app, including the parts that are not published? The gallery has a capture of every screen. | **Quiet outside market hours: each screen holds the last session until the next one opens. The gallery has a capture of every screen, including the ones that stay private.** |
| footer | Paper positions only. The only real money on screen is your read-only brokerage portfolio. | the canonical disclaimer |

The head comment's "fourteen" becomes "the screens" so it cannot go stale a third time.

---

## report.html

| Piece | Current | Proposed |
|---|---|---|
| meta description | The latest NeuralStrike market assessment: the macro view down to the ten largest names, updated five times through the trading day. | **The latest NeuralStrike read on the market: the macro picture down to the ten largest names, with calls and the confidence behind them, rewritten five times each trading day.** |
| h1 | The latest read on the market. | *(kept)* |
| lede | From the macro view down to the ten largest names, with calls and the confidence behind them. Rewritten five times each trading day — pre-market, the open, after the first hour, at lunch and at the close — and each report grades the ones before it. | **From the macro picture down to the ten largest names, with the calls and the confidence behind each. Rewritten five times a trading day — pre-market, at the open, after the first hour, at lunch and at the close — and every report grades the ones before it.** |
| link | Open the report on its own page | *(kept)* |

---

## gallery.html

| Piece | Current | Proposed |
|---|---|---|
| `<title>` | NeuralStrike — App Gallery | **NeuralStrike — Gallery** |
| meta description | Every screen in NeuralStrike, captured from the running app: dealer positioning, options flow, market regime, sector rotation, the strategy calculator and the daily briefings. | **Fifteen NeuralStrike screens captured from the running app: dealer positioning, options flow, market regime, sector rotation, the strategy calculator and a stock taken apart.** |
| rail eyebrow | App Gallery | **Gallery** |
| footer | Paper positions only — the numbers are simulated against live market data. Not investment advice; no orders are transmitted to any broker. | the canonical disclaimer |

Captions (screen names unchanged):

| Screen | Current | Proposed |
|---|---|---|
| The Desk | The landing surface: everything the app knows about right now, condensed into one working view. | **Everything the app knows right now, condensed into one working view. The screen the day starts on.** |
| Gamma Heatmap | Dealer gamma by strike and expiry — where hedging pressure pins price, and where it amplifies a move. | **Dealer gamma by strike and time: where hedging pins price and where it will amplify a move.** |
| Premium Divergence | Where option premium disagrees with price: the early tell that positioning is shifting. | *(kept)* |
| Net Options Premium | Call premium minus put premium — the dollar-weighted read on where the option money is going. | **Call premium minus put premium, in dollars: where the option money is going, weighted by size.** |
| Opportunity Board | Ranked candidates pulled from the current positioning and flow readings. | **One row per watchlist symbol, ranked by how hot its positioning and flow readings are.** |
| Flow Alerts | Unusual option activity as it prints, filtered to size that can actually move a hedge. | **Unusual option activity as it prints, filtered to size that can move a hedge.** |
| Macro Board | The live tape: volatility, breadth, internals, sectors and cross-asset context on one lattice. | **The tape in one lattice: volatility, breadth, internals, sectors and cross-asset context.** |
| Market Regime Control | Sentiment, trend and regime share — the environment that hedging pressure acts inside. | **Sentiment, trend and regime share: the environment the hedging pressure acts inside.** |
| Where the Market Stands | A plain-language summary of the current read, with the components behind it. | *(kept)* |
| Sector & Industry Performance | Which parts of the market are carrying the move, and which are quietly leaking. | *(kept)* |
| Sector Rotation | Relative strength rotation — leadership changing hands before the index shows it. | **Relative strength changing hands, before the index shows it.** |
| Momentum | What is moving, what has stalled, and how persistent the move has been. | **What is moving, what has stalled, and how long the move has held.** |
| Strategy Calculator | Build a structure leg by leg and price it against models — then stress it. | **Build a structure leg by leg, price it against the models, then stress it.** |
| Strategy Finder | Find trade structures that fit the current reading, ranked by what the setup rewards. | **Every structure the chain supports for one symbol, ranked on fit and quality.** |
| Stock Evaluation | Take a single name apart: the setup, the case for and against, and the plan to trade it. | **One name taken apart: the setup, the case for and against, and the plan to trade it.** |

---

## glossary.html (head only)

| Piece | Current | Proposed |
|---|---|---|
| h1 | The words, in plain English. | *(kept)* |
| lede | Every term the app puts on screen, defined without jargon — from what a contract actually is to what dealer gamma means and why it moves price. Read it through, or jump to what you need. | **Every term the app puts on screen, defined without the jargon: from what a contract is, to what dealer gamma means and why it moves price. Read it through, or jump to the section you need.** |

The body is generated and unchanged.

---

## Tests

Existing guards the rewrite must keep green (all in `deploy/tests/test_site.py`):

- `test_live_screens_is_the_primary_call_to_action` — only the **nav** button is pinned;
  the hero and close CTAs are free to move, and this proposal moves both to `live.html`.
- `test_the_pages_that_COUNT_the_screens_say_how_many_there_are` — the "See all 15
  screens" link and the gallery's "15 screens" badge; both kept as-is.
- `test_the_retired_tagline_is_gone`, `test_no_page_names_the_app_host`,
  `test_no_page_reaches_an_external_origin` — the two new pull-quote links go to
  `glossary.html` (internal) and the live origin, which the test's link allow-list already carries (it is distinct from the empty `ALLOWED_ORIGINS`, which governs what the page fetches).
- `test_every_anchor_target_exists` — `#dealer-positioning-and-flow` is a real id in the
  generated glossary.

One guard to **add** with the change, because finding 2 is what happens without it:
`live.html`'s lede and meta must state the published count in words, and a test should
derive *seventeen* / *twelve* / *five* from `live_screens.SCREENS` (`tile`, and
`not tile and not parent`) and read them out of the prose — the same shape as the
existing count test for the gallery.

## Not proposed, and why

- **Reordering the sections.** The page order (hero → facts → idea → lenses → safety →
  close → community) is the design's and still reads well; the re-centring is done by
  the words and the two buttons, not by moving blocks.
- **Dropping "Schwab".** The workbench is Schwab-specific and card 2 is accurate; it is
  merely reframed.
- **A new tagline or a softer Rescue name.** Both are branding; the request keeps them.
- **Touching the glossary definitions or the report.** Different sources, different
  owners, and the glossary is shared with the app's manuals.
