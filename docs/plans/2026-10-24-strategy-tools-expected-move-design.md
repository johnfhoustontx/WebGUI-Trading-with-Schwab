# Expected Move as a Strategy Tool — design (2026-10-24)

## The ask

"Move the Options/Expected move to the subtab under the strategy tools. The
properties from the calculator and simulator will also propagate to the expected
move and vice versa." Then: "this will show in the website as Strategy tools
replacing the Calculator and Simulator menuitems." Asked whether the website's
Strategy Tools should include a public Expected Move, which needs a new anonymous
request kind, the user chose yes.

## 1. The private app

**Menu.** `/options/expected-move` moves from `OPTIONS_CHILDREN` to
`STRATEGY_TOOLS_CHILDREN`, after the Simulator. The route does not change, so
the Scanner, Paper, Captured, Calculator and Symbol hand-offs and every bookmark
still land.

**What is shared.** The Calculator and Simulator already share one position
(`shared_position`: symbol, strategy, legs, selected expiry; whichever page was
edited last). The Expected Move edits less of a position than they do: a symbol,
an expiry, at most one strike, a put/call toggle. Writing what it shows through
`publish` would replace the Calculator's position with a one-leg sketch on every
draw, losing quantities, typed prices, share legs and a calendar's far leg. So it
writes through `merge_view(prior, symbol, expiry, legs)`:

| Edit on the Expected Move | The shared position becomes |
|---|---|
| Lines differ from the prior legs (a picked strike, a hand-off) | Those legs, unpriced, qty 1; the strategy kept if the prior one still fits, else `template_for` |
| A different symbol | That symbol, the strategy kept, no legs (each page lays its template) |
| Same symbol, new expiry | The legs on the old expiry moved to it with their prices dropped; others untouched |
| Anything else (a reload, a redraw) | Unchanged |

`$SPX` and `SPX` are one symbol (the page strips the `$`). The strike the page
pre-selects nearest spot is a convenience and is never shared, and the look-back
stays the page's own.

**Reading it.** With no hand-off waiting, the page opens on the shared position
exactly as if it had been handed off: symbol, expiry, and `view_legs` (the
option legs' strikes) as the lines. A hand-off still wins.

One behaviour change rode along: a Draw on a new expiry keeps drawing handed
lines (`handed_legs`) until a strike is picked or another symbol's chain lands;
it used to replace a handed spread's lines with an empty selection.

## 2. The public site

**Request.** A sixth tools kind, `em`, on the existing `cmd:tools_public` stream
through the existing `bus_client.request_public_tool`, so the live ACL user
needs no new write selector and the writer set `test_bus_client.py` pins is
unchanged. Its args are symbol, expiration and look-back (one of
`EM_LOOKBACKS`, mirrored across the page, the validator and the engine by
`test_cross_tier_mirrors.py`). No legs: the strike lines are drawn page-side, so
they cost nothing and two visitors on one symbol share one result.

**Worker** (`tools_public._tools_em`). Refusals in order: `expired`, `cached`,
`load_first` (no held chain, or no strikes held for that expiration), then
`not_listed` and the shared duplicate / closed / budget gate. The cone's IV is
`atm_iv_from_chain` over the HELD chain cut to the one expiration
(`_one_expiry`), because that function otherwise falls back to the nearest
expiration with a volatility. One Schwab call, the price history. With no quote
there is no forming bar; once the session is open the cone anchors at today
midnight CT (`em_anchor_ms`) rather than at yesterday's candle, which would
reach the expiry a day early. Published: the candles, the cone, the ATM IV and
the spot; no contract's quote (the public Calculator publishes an IV on the same
terms).

**Page** (`em_live`). The private chart construction and figure builders. A page
load sends nothing; a Calculator position handed across in the same tab is an
action and loads its symbol and draws its strikes. An expiration whose strikes
are not loaded is fetched first. A `load_first` answer triggers one chain reload
and one retry, never a loop. The hourly limit is the Calculator's `TOOLS`
counter.

**Navigation.** The public header carries no navigation by design, so each of
the three public tools draws `strategy_strip` at the top of its page: tabs to
the tools this origin serves, through `shell.can_navigate` / `navigate_to`. The
site's Tools menu replaces Calculator and Simulator with one item, Strategy
Tools, linking `/calculator`.

## Not done

- The public tab hand-off stays one-way, Calculator to Simulator and Expected
  Move: the public Calculator always opens fresh by design.
- Nothing has run against a live chain yet.
