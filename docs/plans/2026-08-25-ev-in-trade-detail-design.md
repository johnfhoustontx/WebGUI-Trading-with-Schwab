# EV in the Trade detail panel — design (2026-08-25)

**The ask:** show `EV = p·b − (1−p)` in the shared Trade detail panel as a
recommendation / informational display.

**The finding that shaped it:** the obvious implementation is not merely
unhelpful, it is actively harmful. Every number needed to compute EV from a
signal is derived from that signal's own price, so an honest EV is ~0 — and the
cases where it is large are the cases where the inputs are broken. This document
records what was measured, what we display, and what we deliberately refuse.

---

## 1. What already exists

`scanner_engine.calc_expected_pnl` stamps `expected_pnl_10` / `expected_pnl_target`
on 0-DTE signals, and `detail.py` renders one row for it — inside the **collapsed**
"Score factors" expander. That placement contradicts the panel's own stated rule
(*"Everything above the expansions answers 'reject or not'; nothing that answers
it is behind a click"*), and it reaches **22 of 465 live signals (4.7%)**.

EV is also already load-bearing where it is *correct* to be: `select_best_width`
rejects any width with `e_pnl <= 0` and picks the max, and the
`credit/w < |delta| + EDGE_MARGIN` floor above it is the same inequality in
closed form. **That is the right place for the priced EV — a gate, not a display.**

## 2. Measured evidence (prod, 2026-08-25)

**(a) The priced EV is ~0 in the median, and junk in the tail.** Across the 75
live signals carrying usable fields, median priced EV is **+0.038 R**. The top of
the distribution is entirely bid-ask width:

| symbol | EV(R) | credit/width | \|Δ\| | relative bid-ask |
|---|---|---|---|---|
| UAL | +2.832 | 0.780 | 0.157 | 225% |
| IBKR | +0.751 | 0.515 | 0.151 | 239% |
| AMGN | +0.412 | 0.412 | 0.170 | 395% |

A 78%-of-width credit against a 16-delta short cannot exist in an efficient
market. **Ranking on priced EV ranks the least trustworthy marks first.**

**(b) `p` and `b` must name the SAME event.** The formula assumes a binary: win
→ gain `b`, lose → lose 1. That approximately holds for a credit spread carried
to expiry. It does not hold for the Strategy Finder shape, where `max_profit` is
a tail outcome and `pop_pct` is P(any profit). Computing `b = max_profit/max_loss`
there yields:

```
   +2137.1 R   Long Put    unbounded=False
   +1776.6 R   Long Put    unbounded=False
   median across 225 bounded signals: +16.63 R
```

A long put's `max_profit` assumes the underlying reaches **zero**. ⚠ Note
`unbounded=False` on every one — the existing flag does **not** protect against
this, because a put's profit genuinely is bounded. The defect is the mismatch
between the two events, not unboundedness.

**(c) Three incompatible signal shapes.** 0-DTE and swing carry
`credit`/`max_loss`/`short_delta`; directional (390 of 465, **84%**) carries
`max_profit`/`max_loss`/`net_credit`/`commission` and neither `credit` nor
`short_delta`. Any EV surface needs a per-family branch that **refuses** rather
than guesses.

## 3. What we display

Two numbers, and they answer different questions.

### 3a. Breakeven win rate — structural, informational

`p* = max_loss / (credit + max_loss)` — equivalently `1 − credit/width`. It states
**what this trade requires**, is derived from the two figures the panel already
shows, and cannot be distorted by the delta-vs-mark mismatch that ruins the
priced EV. It goes in the **ECONOMICS block** directly beneath the existing
`Probability` row, so the reader sees `95.7%` against `needs 73.0%` and reads the
margin without arithmetic.

It is deliberately **not** called EV and carries no verdict colour beyond the
margin's sign.

### 3b. Calibrated EV — the recommendation

`EV(R)` for the bucket this signal falls into — family × score bin — computed from
**realized outcomes** in `signals.db`, never from the option's price. Rendered as
a sentence with its own sample size:

> Signals like this returned **+0.52R** per trade · n=108 over 36 days

This is the only figure in the feature that earns the word *recommendation*,
because it is the only one whose `p` is independent of what the market charged.
It carries `n` and `days` inline because a bucket with three trades must not read
like a bucket with three hundred, and it is **withheld entirely** when the
bucket's day-clustered `tDay` is inside ±2 — an EV we cannot distinguish from
noise is not a recommendation.

## 4. What we refuse

**Decision: show nothing at all** (not a dash, not a flagged number) when EV
cannot be computed honestly. The panel already omits rows for missing keys, so
this is consistent, and the repo's own history is the argument against the
alternative: *a confident wrong number outlives its caveat*.

| family | breakeven win rate | calibrated EV |
|---|---|---|
| 0-DTE credit spread | yes | yes |
| Swing credit spread | yes | yes |
| Iron condor | yes | yes — but never a priced `p` (see below) |
| Directional / Strategy Finder | **no** — no `credit` | yes, if the bucket qualifies |
| Anything with `max_profit` as a tail | **no** | — |

⚠ **Iron condors never get a delta-derived `p`.** `signal_db` stores one
`entry_short_delta` and `scanner_engine` writes the SUM of both shorts into it,
which for a symmetric condor is ~0 — so `1−|Δ|` prices it at a confident
**0.8995**. Already guarded in `shared/calibration.priced_win_rate`; the panel
must not reintroduce it.

## 5. Architecture

Tier 1 holds no `sqlite3` (verified: zero imports in `webgui/`), so the realized
data must arrive over the bus.

```
signals.db  ──►  options_svc (already imports signal_db lazily)
                     │  nightly slot, config/sessions.toml [slots.calibration]
                     ▼
              cache:options:calibration     ← small: ~10 buckets, no per-trade rows
                     │
                     ▼
              webgui/pages/options/ev.py    ← PURE builders, unit-tested
                     │
                     ▼
              detail.py  ECONOMICS block
```

**The pure statistics move to `shared/calibration.py`**, imported by both
`tools/signal_calibration.py` (CLI + SQL + rendering) and `options_svc`. A second
copy would be the `clamp`-times-nine trap in a package that has already been bitten
by it; and a service importing a `tools/` CLI inverts the dependency direction.

**Cadence: nightly**, mirroring `sentiment_svc`'s momentum cascade
(`[slots.momentum]` at 16:20 CT → `momentum_due` → `handlers.refresh_momentum`).
The bucket table moves on closed trades, which arrive a handful per day; a
per-tick recompute would re-read the whole database for a number that changes
daily.

## 6. Rejected alternatives

- **Display the priced EV prominently.** §2a. It is ~0 when honest and
  spectacular when the mark is broken.
- **Rank or sort the board by EV.** Same reason, amplified: the sort puts the
  worst marks on top.
- **Fix it with a liquidity filter on the priced EV.** Treats the symptom. Even
  with perfect marks the number is ~0 by no-arbitrage, so there is nothing to
  display.
- **Use `unbounded` to gate the Strategy Finder family.** §2b — it reads False
  for every one of the worst offenders.
- **Let Tier 1 read `signals.db` directly.** Breaks the tier rule for a number
  that changes once a day.
- **Show a dash with an explanatory tooltip.** Considered and declined by the
  operator; silence is consistent with the panel's existing treatment of missing
  keys.

## 7. Open

- The calibration buckets are 44–49 days of one regime. The `tDay` gate is what
  keeps a thin bucket from speaking; the re-run due **2026-09-23** is what
  revisits it.
- The R-multiples do not net commissions. Directional signals already carry a
  `commission` field; credit-spread families do not, so folding it in is a
  follow-up rather than part of this change.
