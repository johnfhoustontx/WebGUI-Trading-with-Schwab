# Stock legs in the leg model (D4)

*Design, 2026-09-12. Gap assessment item **D4**.*

## What the assessment asked for

> **D4. Stock legs in the leg model.** This unlocks covered call, protective put
> and collar analysis. It could then extend to a "protect a holding" view on the
> real Portfolio page, as analysis only.

**The premise holds exactly**, which makes this the first D-tier item measured
true rather than false or half-wrong:

| claim | measured |
|---|---|
| the leg model has no stock leg | ✅ `STRATEGY_TEMPLATES` held 24 templates, every leg `call` or `put` |
| nothing prices one | ✅ `options_simulator/engine.py` has no share concept; `options_calculator` routes every leg through `bs_price` |
| covered call / protective put / collar are absent | ✅ none of the three was a template, and they are precisely the three that need shares |

## The representation is the whole design

A stock leg is a **normalized leg dict like any other** — same six keys — with:

| field | value |
|---|---|
| `option_type` | `"stock"` (`strategies.STOCK` / `options_calculator.STOCK_KIND`) |
| `strike` | `None` |
| `expiry` | `None` |
| `qty` | ⚠ **100-share LOTS, not shares** |
| `premium` | the price **paid per share** |

⚠ **The lot convention is what makes this a small change.** Every consumer
already multiplies `value × qty × 100`, so one lot of a $100 stock comes out at
$10,000 with no new branch — exactly as one $100 option contract would. The
alternative (`qty` in shares plus a per-leg multiplier) would have needed a
branch at each of the ~six places that multiply by 100, and each of those is a
place a units bug hides.

So the only thing the pricing core needed is a per-share **value** function:

```python
def leg_value(price, leg, T, r, sigma):
    if is_stock_leg(leg):
        return float(price)      # a share is worth the underlying, at any T, any IV
    return bs_price(price, leg["strike"], T, r, sigma, leg["option_type"].lower())
```

`bs_price(price, None, …)` raises, so this is not a nicety — and the grid it
feeds is rebuilt on every keystroke.

## What the measurement changed

### The max-loss grid had to reach zero

`calc_summary_generic` scanned `0.5×spot … 1.5×spot`. That is right for an
option-only structure — no vertical, condor or fly can lose more than its width —
and **wrong** the moment shares are involved: it reported a covered call on a
$100 stock as risking **$4,800** (the loss at $50) when the position risks
**$9,800**. A leg set holding shares now scans from **zero**. It is also what
proves a protective put's loss is *bounded*, which is the entire reason to own
one. Option-only leg sets keep the old floor, and a control test pins a put
credit spread's max loss at an unmoved $140.

### Five places treated "no strike" as "not a leg"

The Calculator page open-coded `l.get("strike") is None` five times. Four blocked
or asked the user to pick a strike that does not exist; **one silently dropped
the leg**, which is the worst of the five — the page would then have priced a
covered call as a naked short call with no warning at all.

| site | now |
|---|---|
| the action line | goes through `leg_editor.legs_ready` |
| Calculate | same |
| Fill premiums | fills the option legs from the chain, the share leg from spot |
| `max_loss_estimate` | **declines** — it maps every leg to call/put and reasons from strikes, so a share leg was booked as a *put* |
| Send to Expected Move | still drops it, and that is right: a share leg has no strike **line** to draw |

`leg_ready` also rejects a **NaN** strike, which `is None` passed straight
through to `bs_price`.

### A stale expiry on a share leg would move the horizon

Three separate paths could write one, and the consequence is not cosmetic: a
share leg's `expiry` joins `calc_summary_generic`'s front-expiry computation, and
if it were **earlier** than the real option leg's, the option would be priced
with time remaining at the wrong horizon — a payoff diagram that is not a payoff.

Closed at all three, plus the layer that decides:

1. `build_default_legs` never sets one on a share leg;
2. `retype_leg` clears strike and expiry when a leg crosses the stock/option
   boundary;
3. `set_legs_expiry` (the Calculator's "set all legs to this expiry") skips share
   legs;
4. ⚠ and `options_calculator._leg_expiry_years` returns `None` for a share leg
   regardless — belt-and-braces at the chokepoint both summary paths share, since
   a pasted or hand-built leg set can still arrive carrying one.

## The gate: only the Calculator offers them

⚠ **Adding a template exposes it on every page that mounts the picker**, and
`test_strategies.py` requires `STRATEGY_MENU` to cover every template *exactly* —
a template missing from the menu is unreachable, which is what that guard is for.
So the three cannot be hidden in the data. They are gated at each **mount**, on
two independent controls:

| surface | strategy menu | leg TYPE select |
|---|---|---|
| **Calculator** | offers them | `allow_stock=True` |
| **Simulator** | `exclude=STOCK_STRATEGIES` | option-only |
| **Rescue** ad-hoc | `exclude=STOCK_STRATEGIES` | option-only (row layout) |

Both controls, because either alone leaves a hole: gating only the menu still
lets a user flip a leg's TYPE to stock by hand, and gating only the TYPE select
still lets them pick the template.

**And the exclusions are safety, not taste:**

- the **Simulator**'s Replay and IV-shock engines price a `ContractRow` pulled
  from the option chain and have **no share concept**, so a covered call selected
  there would draw an option-only curve under a frame that said "covered call";
- the **Rescue ad-hoc form BOOKS into the paper account**, and that account
  cannot hold shares inside `paper_positions` at all — they live in
  `equity_lots`, which is the whole reason that table exists — so a covered call
  submitted there would be stored as a **bare short call**. That is the shape of
  assessment **defect 12**, where the same form lists an iron butterfly and
  relabels it an iron condor on the way out.

## Two naming decisions worth stating

**The tags lead with `DEBIT` on all three.** A covered call's *option leg* is a
credit; the *position* is a debit, because you pay for 100 shares. The tag names
the cash flow at entry, which is what the frame colours, so DEBIT is the honest
word — and every one of the three carries **`100 SHARES`** as its second chip so
the reader cannot mistake what they are looking at.

⚠ **The rest of the app calls `COVERED_CALL` a credit structure, and is right
to.** *Its* covered call is the **option leg only** — `paper_positions` holds no
shares, and CLAUDE.md already documents that such a position's `unrealized_pnl`
covers the option leg alone. These are two different objects with one name, and
the `100 SHARES` chip is what distinguishes them on screen.

**The summary stays NUMERIC.** None of the three is in `_ANALYTIC_CODES`, so
`summary_code` returns `CUSTOM` and `calc_summary_generic` prices them on a grid
and reads the figures off the curve — exact for a payoff diagram. Writing
analytic formulas for them would be the unmeasured change this audit keeps
refusing. A share leg pasted into an analytic strategy (the dropdown still reads
`PCS`) also falls to `CUSTOM`, because the shape multiset cannot match — so the
analytic PCS formula, which would price a share leg as an option, is structurally
unreachable.

## What is NOT built

- **The Portfolio "protect a holding" view.** The item itself says "could *then*
  extend", and it is a different piece of work: it needs the real holdings feed,
  a per-holding tenor choice, and a decision about what it does with a lot whose
  size is not a multiple of 100.
- **Stock legs in the Simulator.** Its Replay engine re-prices a contract along
  the underlying's own path, which for a share is trivial — but the plumbing goes
  through `ChainSnapshot.contracts`, built from the option chain, so admitting
  shares there is a change to that model rather than a call-site change.
- **Booking any of the three.** Analysis only, per the item, and the paper
  account's share handling (`equity_lots`) is reached by put assignment, not by
  opening a position.
- **Analytic formulas** for the three — numeric is exact here and sourced
  formulas are not needed.
