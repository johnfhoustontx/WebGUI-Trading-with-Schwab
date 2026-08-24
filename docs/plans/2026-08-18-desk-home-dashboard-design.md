# Desk — the single-screen home dashboard (2026-08-18)

A new page at `/desk` that aggregates the highest glance-value element of each
existing screen into one command centre, and becomes the app's landing page.
Built from a supplied design (`Options Desk.dc.html`, Claude Design project
`54e7865d`), read live in the browser rather than described second-hand.

**It is NOT a pure Tier-1 re-render**, which is the first thing that separates it
from the four 2026-08-17 sentiment rebuilds. Those were re-renders of payloads
that already existed. This one needs four new fields on `cache:options:matrix`
— see [The one Tier-2 change](#the-one-tier-2-change) — because the design's
flagship panel asks for something the cache genuinely does not hold.

## The organising idea

A day-trading loop is four questions in order:

> Is it risk-on or risk-off? → Where is the structure? → What should I act on? →
> What am I managing?

Each panel answers exactly one of them, in that order down the page. That is the
whole layout rationale, and it is also the admission criterion: **a screen earns a
panel only if it answers one of those four at a glance.** RRG, the sector heat
grid, the momentum leaderboard, the expected-move cone, the Calculator and the
Simulator are all deliberately absent — they are deep-dives you open with intent,
and none of them has glance value.

## What the design asked for vs. what the cache holds

Checked *before* building, against the live payloads. Most of it was already
there; five things were not, and pretending otherwise would have shipped five
permanently-empty columns.

| design element | live cache |
|---|---|
| SPX / QQQ / VIX quotes | `cache:options:header` — `prices` + `vix` + `vix_regime` |
| Market regime + committed direction | `cache:sentiment:regime` — `label`, `direction`, `direction_strong` |
| Day / Week / Month rings | `sentiment.sentiment_arcs` + `trend_arcs` → `rings.ring_svg` |
| Per-symbol spot, day %, gamma flip | `cache:options:matrix` rows — for the whole ~45-name universe, 1-min |
| Long/short-gamma regime | `cache:options:matrix` rows — `gex_regime` (`above`/`below`/`na`) |
| Top-5 by hotness | `cache:options:matrix` rows — `hotness` |
| Flow alerts, newest first | `cache:options:flow_alerts` + the existing `flow.alert_rows()` |
| Position mark + unrealized | `cache:options:paper_account` — `current_value`, `unrealized_pnl` |
| OK / TRIM / AT RISK / RESCUE flags | `rescue_state` ∈ `ok`/`watch`/`tested`/`critical` + `heat` 0–100 |
| IV level + IV direction | `snapshots.atm_iv` + `matrix.iv_regime()` — see [the stale-docstring correction](#the-stale-docstring-that-nearly-cost-two-columns) |
| **call wall / put wall per symbol** | **only on `cache:options:gamma`, which is SINGLE-SYMBOL** |
| **net GEX per symbol** | **same** |
| **setup tag** (MOMENTUM / BREAKOUT / …) | `dealer_regime()` is written and tested but **publishes to nothing** |
| **RV, and "edge" (IV − RV, `+7.4v`)** | **does not exist** — there is no realized-vol series anywhere |
| **Vol/OI per symbol** | **does not exist** — only per-contract on `uoa` alerts |
| **BUY / SELL on flow** | **structurally impossible** — Schwab gives no time-&-sales tape |

The last three are dropped, not stubbed. A dash reads as *temporarily missing*;
these were never there. The panels show real columns instead — and for flow, the
call/put side, which is all the data can honestly support. `alert_text`'s own
docstring is explicit: *"No buy/sell claim."*

### The stale-docstring that nearly cost two columns

`matrix.iv_regime()` carries a docstring saying ATM IV is *"the axis the app does
NOT yet emit"*, and describing the `atm_iv` snapshots column as something that
would have to be added. **That is out of date.** The column exists
(`gex_history_db.py:91`), is written on every poll
(`gex_collector.py:397` — `iv_analysis.extract_atm_iv(chain)`), and has its own
loader (`load_atm_iv_series`, `:658`).

Measured against the live prod DB on 2026-08-18: **162,566 of 162,598 `gex` rows
carry a non-null `atm_iv` (100.0%), across 92 symbols, collecting since
2026-08-13** — five days after the docstring was written. Latest samples: `$SPX`
9.25, `QQQ` 15.91, `$NDX` 15.58.

Two consequences, both material:

1. **The Opportunity Board gets a real IV column** — level *and* direction
   (`spiking` / `collapsing` / `stable`), which is more decision-useful than the
   supplied design's static IV/RV pair, because direction is what actually
   distinguishes the setups.
2. **`dealer_regime()` can fire all six labels.** Without an IV state it
   collapses: `below flip` can only ever return `neutral`, and `vanna_squeeze`
   becomes unreachable — so the two most valuable labels, `gamma_cascade` and
   `vanna_squeeze`, are *exactly* the ones that need this axis. Publishing the
   tag without IV would have shipped a mostly-`neutral` column.

**The lesson, which is the reason this is written down:** a docstring is not
evidence about the state of the data. This one was believed twice — once by the
codebase search that reported `iv_regime` as dead, and once by the first draft of
this design, which dropped the IV column on its authority. It took one read-only
`COUNT(*)` against the live DB to settle. Query the data, not the prose. The
`atm_iv` docstring in `iv_regime()` is corrected as part of this work.

## The decisions worth recording

**1. Every number is computed by the same pure function its owning page uses.**
The Desk imports `sentiment.sentiment_arcs` / `trend_arcs`, `rings.ring_svg`,
`matrix.matrix_rows`, `flow.alert_rows`, and `console_regime`'s label logic. It
composes; it never restates arithmetic. This is not tidiness — this app already
has a **documented open bug** where `/sentiment/sectors` and `/sentiment/rotation`
print *opposite* regime verdicts because each computed its own from a different
quantity on a different scale. A dashboard that quietly disagrees with the page it
links to is worse than no dashboard, so a test asserts the Desk's regime word
equals `console_regime`'s for the same payload.

**2. The duplicated spot prices are removed, because they can genuinely disagree.**
The supplied design shows SPX in two places — `6,712.74` in the top strip and
`6,712.81` in the Dealer row. That is not a mockup slip. Those would come from
`cache:options:header` and `cache:options:matrix`, **separate keys with independent
version counters**, so a 2-second window really can render two different prices for
one symbol on one screen. The top strip therefore drops SPX/QQQ; the Dealer band
owns per-symbol prices and shows strictly more (spot + day % + distance to flip).

> ⚠ **Superseded 2026-08-24** — the VIX tile was replaced by BIAS and SIGNAL at
> the user's request, and `cache:options:header` left the page's poll batch with
> it. The paragraph below is the reasoning as written; the split it argues for is
> unaffected, since the strip now carries no per-symbol quote at all.

**VIX stays in the top strip and cannot move**, which is the clean confirmation
that the split is right: `$VIX` is deliberately excluded from the matrix row
universe (`compute.py:2696` — *"the collected universe minus `$VIX`"*), so it can
never be a Dealer row. The division falls out on its own: **top strip = market-wide
state, dealer band = per-symbol structure.**

**3. One regime word, one source.** The `LONG GAMMA · PINS` / `SHORT GAMMA · RUNS`
chip is derived from `gex_regime` (spot vs flip) and nothing else. Net GEX is shown
as a signed magnitude beside it and is **never** allowed to assert a second regime
word. The two can disagree — a symbol can sit above its flip while net GEX is
negative — and that disagreement is real information, but printing two conflicting
regime words in one row is the same failure as decision 1, at row scale.

**4. Both ring sets, in the space the removed quotes freed.** `/sentiment` renders
two ring graphics (`render_sentiment_card` / `render_trend_card`,
`console_page.py:117`) — Sentiment and Trend, each Day/Week/Month; `ring_svg`'s
docstring notes the `uid` argument exists precisely because "two rings share the
/sentiment page". Market **Regime** is a *different* graphic (`console_dial.dial_svg`
plus the five-state membership block), which is why carrying the Regime word as
text next to both rings is three independent reads, not one read shown twice.

**5. Zero Highcharts, deliberately.** The structure map is positioned divs; the
rings are inline SVG strings. Nothing on this page is a time series, so nothing
needs a chart element — and this repo's chart traps (collapse when mounted hidden,
the stock-module `update()` throw that empties a chart, no `ResizeObserver`) are
exactly the class of bug you do not want on a page that is open all day.

**6. The structure map is divs, not SVG.** Bar geometry is a pure
`structure_positions(spot, flip, put_wall, call_wall)` returning percentages,
applied as runtime `left-[{pct}%]` arbitrary-value classes — the documented
continuous-value exception to the fixed-palette rule. This also sidesteps the
DOMPurify `vector-effect` trap, which has cost this repo twice: a scaled `viewBox`
plus `preserveAspectRatio="none"` is the natural way to draw a fluid-width bar, and
the stroke-correction attribute it requires is silently stripped, leaving strokes
thick horizontally and hairline vertically while the server-side string stays
perfectly correct.

## Layout

Top strip full width, then a 2×2 grid (`grid-cols-1 xl:grid-cols-2`, so it stacks
rather than crushes on narrow viewports).

| | |
|---|---|
| **① Top strip** — clock · VIX + regime band · Market Regime label + committed direction · Sentiment ring · Trend ring · freshness | full width |
| **② Dealer Positioning** — `$SPX` `SPY` `QQQ` `$NDX`: spot + day %, gamma flip + signed distance, structure map, call wall, put wall, net GEX, regime chip | top-left |
| **③ Opportunity Board** — top 5 by hotness: score + mini bar, symbol, composed rationale, IV level + direction, signal strength, P/C, net premium, setup tag | top-right |
| **④ Live Flow Alerts** — newest 5: time, kind chip, symbol + detail, premium | bottom-left |
| **⑤ Positions** — paper + driver merged, open only: source chip, strikes, DTE, qty, entry, mark, unrealized, flag | bottom-right |

The Desk draws **no wordmark of its own** — the supplied design is a standalone
screen, but here the shell already provides the brand lockup, breadcrumb and rail.

**Click-through, no actions.** Rows navigate to the owning page: Dealer →
`/options/gamma` for that symbol (reusing the existing `handoff.send_to_gamma`
one-shot stash), Opportunity → `/options/matrix`, Flow → `/options/flow`,
Position → `/options/paper` or `/driver`. Nothing on the Desk enqueues a command,
so it stays a pure reader with no busy overlay and no stale-price guard — and the
home page cannot mutate state on a mis-click.

## Data flow

Eight Tier-1 views, **one batched `read_versions()` per 2 s tick** (cheap `:ver`
probes, one pipelined round-trip), dispatching only changed views to their panel
painter. Payloads deserialize only when a version moved. Everything through
`run.io_bound`; every callback `@guard` / `@guard_async`. This is the coalesced-poll
pattern `gamma.py` already uses, and it matters more here because the page is open
all day.

| panel | views |
|---|---|
| ① | `options:header`, `sentiment:regime`, `sentiment:composite`, `sentiment:history`, `options:gex_status` |
| ② ③ | `options:matrix` — **one read, two panels** |
| ④ | `options:flow_alerts` |
| ⑤ | `options:paper_account`, `options:driver_paper_account` |

## Degradation

Each panel degrades **independently**; one dead service never blanks the page.

- **Missing view** → that panel alone shows "Waiting for the *X* service…". Never
  a fabricated zero.
- **Off-hours walls are suppressed, not printed.** Index OI reads 0 after hours, so
  `$SPX`/`$NDX` yield all-zero grids and *arbitrary* walls. When `gex_status`
  reports the session closed or the snapshot stale, the Dealer band renders dimmed
  with an explicit "as of HH:MM" stamp; on the all-zero signature the walls are
  withheld. A confident wall that is really a zero-grid artifact is the exact
  failure this panel must not have.
- **Non-finite inputs are filtered at the call site.** A NaN clamps to the *high*
  bound in this codebase, so a data outage renders as a maximal reading — the trap
  behind two separate `sentiment_svc` bugs. No guard goes into a shared primitive;
  each Desk computation filters its own inputs.
- **Empty is stated, not blank** — "no open positions", "no alerts today".

## The one Tier-2 change

`build_matrix` (`compute.py:2795-2815`) already opens a read-only `gex_history`
connection and, per symbol, calls `load_flow_series` + `latest_flip` into
`raw[sym] = {"series": …, "flip": …}`, which the pure `matrix.build_rows` turns
into rows. The change extends that same loop and that same pure function to emit
six more keys per row:

| key | source |
|---|---|
| `call_wall` / `put_wall` | the stored GEX grid, via the existing `gamma_walls()` |
| `net_gex` | `net_total`, **already in the loaded snapshot row tuple** |
| `atm_iv` | the last non-null sample from `load_atm_iv_series` |
| `iv_state` | `iv_regime()` over that series — `spiking` / `collapsing` / `stable` / `na` |
| `dealer_regime` | the existing, already-tested `dealer_regime()` that today publishes nowhere — now with the IV axis it needs, plus `wall_dist_pct` from the new walls, so all six labels are reachable |

Rows are `list[dict]` inside `MatrixSnapshot`, so **no contract change**. Every new
key degrades to `None` (or `"na"`) when its source is absent, **never `0`** — the
distinction the off-hours case depends on, and the reason the degraded-row branch
of `build_rows` must be extended too, not just the happy path.

Note the walls and the IV axis are mutually reinforcing rather than independent:
`delta_wall_pin` needs `wall_dist_pct`, which needs the walls; `gamma_cascade` and
`vanna_squeeze` need `iv_state`. Shipping either half alone would leave
`dealer_regime` mostly returning `neutral`.

This is the documented exception to webgui-only work: *"only when no clean state
exists, refactor the Tier-2 source to emit one."* The alternative designs were
considered and rejected — reading `cache:options:gamma` is a **race** (it holds one
symbol at a time and is mutated by whichever Gamma page is open), the 4×/day Claude
briefing covers only three symbols and is gated off in dev, and a bespoke
`cache:options:desk` aggregate would duplicate five existing views and couple the
service to a GUI layout, so every Desk redesign would become a service change.

## Shell integration

- `/` redirects to `/desk` instead of `/market` (one line; its rationale comment is
  rewritten at the same time). Note `/` was **already** just a redirect — the Market
  Scanner moved to `/options/scanner` on 2026-08-16 — so nothing has to be relocated.
- `/desk` joins `FLAT_NAV`, and `NAV_SECTIONS` gains a **leading caption-less block**
  `(None, [_sec_page("/desk")])`; `_nav_section_header` is skipped when the caption is
  `None`. Desk therefore sits alone above the `MARKETS` caption with a separator under
  it — mirroring how `SYSTEM_RAIL` is pinned to the foot, and marking it as home.
- `breadcrumb_trail` returns just `["Desk"]` for a caption-less section.
- Icon `space_dashboard` (distinct from all 13 already in use; `dashboard` is taken by
  Market Dashboard).

## Styling

Reuse the existing **`[console]` theme tokens** (`CONSOLE_CARD`, `CON_TXT_*`,
`CON_POS/NEG/WARN`, `CONSOLE_HAIRLINE`) — the supplied design's aesthetic *is* the
Market Regime Console's language, already config-driven in `config/theme.toml`.
**No new `[desk]` section** (YAGNI); add one only if the Desk later needs to restyle
independently of the Console. Tailwind-first throughout.

## Tests

New `webgui/tests/test_desk.py` over the pure builders — `structure_positions`,
`dealer_rows`, `opportunity_rows`, `flow_rows`, `position_rows`, at-risk counting,
freshness, `None`-view degradation, off-hours wall suppression — plus the
**anti-contradiction test** from decision 1.

Updated: `test_shell.py` (route set; drawer 13→14 and still mutually distinct;
`NAV_SECTIONS` captions `[None, "MARKETS", "STRATEGY", "ACCOUNT"]` and counts
`[1, 4, 4, 2]`; breadcrumb for a caption-less section; `_group_children("/desk") is
None`), `test_no_inline_style.py`, `test_page_help.py`, and `test_docs_cover_the_ui.py`
— which demands a Desk section in **both** the user guide and the reference guide, in
rail order. Tier-2: `services/options_svc` tests for the four new row keys and their
`None` degradation.

## Out of scope

No inline actions. **No RV, and therefore no "edge" column** — IV level and
direction ship, but IV−RV needs a realized-vol series that does not exist and is
not worth building for one column. No new theme section. The dealer symbol set is
a module constant `("$SPX", "SPY", "QQQ", "$NDX")` — making it a user setting is a
later increment, not v1.
