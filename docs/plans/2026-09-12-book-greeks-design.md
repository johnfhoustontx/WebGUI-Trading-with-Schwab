# Book-level Greeks (C4)

*Design, 2026-09-12. Gap assessment item **C4**.*

## What the assessment asked for

> **C4. Book-level Greeks:** net delta, gamma, theta and vega per book, optionally
> beta-weighted to SPY.

## What the book can already answer, and what it cannot

`paper_positions` stores `current_short_delta` — the **short leg's** delta — and
nothing else greek. That is enough for the delta stop (which is about the short
strike's moneyness) and **not** enough for a book-level read: a put credit spread
at a −0.20 short and a −0.08 long is +0.12 net, not +0.20, and summing short
deltas would overstate the book's direction by the whole long-leg offset.

⚠ Calling that sum "net delta" would be precisely the mislabelling this audit
keeps correcting, so the Greeks are computed per POSITION from both legs.

**The chain is already in hand.** `signal_repricer.reprice_swing` fetches a
chain per `(symbol, expiration)` every manage cycle and reads each leg out of it
to build the mark; Schwab's contracts carry `delta`, `gamma`, `theta` and `vega`
beside the quotes. So the Greeks cost **no extra API call** — the same argument
that made C3's IV snapshot free.

## The shape

**`signal_repricer.position_greeks(trade, chain)`** — a new PURE function, not a
restructuring of the four existing structure branches. Those branches are the
money path (they produce every mark, every cycle), and threading a fifth return
value through each of them to get a display number is the wrong trade. The new
function walks the same legs independently and returns
`{net_delta, net_gamma, net_theta, net_vega}` **per contract**, signed by side.

Signs follow the position, not the option: a short leg contributes **minus** its
greek. So a put credit spread is net **positive** delta (it profits as the
underlying rises), net negative gamma, net **positive** theta and net negative
vega — which is the sanity check the tests assert, because a sign error here would
render a premium-selling book as long volatility.

**Storage:** four additive nullable columns on `paper_positions`
(`net_delta` / `net_gamma` / `net_theta` / `net_vega`), written by
`update_position_mark` on the manage tick. ⚠ **`NULL` means "not computed", never
zero** — a zero delta is a real and meaningful reading (a balanced iron condor),
so a position whose chain was unquotable must not join the book's sum as flat.
Every position opened before this change reads NULL until its next mark.

**Aggregation:** `compute.book_greeks(positions)` sums `greek × quantity` across
positions with a reading, and reports `positions_priced` / `positions_total` so
the page can say what the sum covers. A partial book is the normal case for the
first cycle after a restart, and a total that silently omits three positions is
worse than one that says so.

## ⚠ What does NOT ship: the beta weighting

> *"optionally beta-weighted to SPY"*

**There is no beta anywhere in this repo.** Nothing stores one, nothing computes
one, and Schwab does not serve one. A beta-weighted delta is a *different* number
from a raw one — it answers "how many SPY-equivalent shares am I long" — and
producing it from an assumed or hard-coded beta would be inventing the input to
the only quantity the reader cares about.

It is derivable: `run_full_scan` already fetches a year of daily bars per symbol,
and a rolling regression against SPY is the standard construction — which is the
same shape as C3's finding, where the data was in hand and nothing recorded it. It
is recorded as a follow-up rather than guessed at.

## What the numbers mean, for the page

Delta and theta are the two a premium seller reads daily, and their units differ:

- **net delta** is in *shares-equivalent per contract × contracts* — a book at
  +1.5 net delta moves like 150 shares of the underlying, per underlying, so it
  is only summable across symbols as a rough direction.
- **net theta** is *dollars per day* and genuinely additive: it is what the book
  earns for a day passing, and for a credit book it should be positive.

⚠ So the card names theta as dollars and delta as a direction, and does **not**
present a cross-symbol delta total as though it were a hedge ratio. That is the
same honesty the beta-weighting paragraph is about, one level down: without beta,
adding a $970 MU delta to a $145 PG delta is arithmetic rather than risk.
