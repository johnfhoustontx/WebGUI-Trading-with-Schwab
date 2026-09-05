# Two-sided 30–45 DTE income window + share inventory — design

**Date:** 2026-09-05
**Status:** design agreed, not built
**Plan:** [`2026-09-05-income-window-and-share-inventory-plan.md`](2026-09-05-income-window-and-share-inventory-plan.md)

## What this is

Two things, sequenced so the first ships and is verifiable before the second starts:

**A. An `INCOME` scan window at 30–45 DTE**, two-sided, screening put *and* call
credit spreads plus cash-secured puts across the watchlist, on its own morning
schedule.

**B. Share inventory in the manual paper account** — an `equity_lots` table, put
assignment at expiry, and covered-call candidates struck at or above cost basis.

Together they are the wheel, minus the part of the wheel that is a directional bet
nobody stated.

## Why, and what it is NOT

This came from assessing an 8-agent "Grok options desk" prompt kit. Six of its
eight roles already exist here as deterministic Python — IV rank, the earnings
gate, the roll desk, the risk envelope, the morning brief — several with more
rigour than a prompt can carry. What genuinely did not exist was a book that holds
**shares**, at horizons of **30–45 days**.

⚠ **This is deliberately not a "wheel desk".** The thread's Put Seller bot is
long-only: cash-secured puts, covered calls and LEAPS are all bullish-to-neutral,
and a wheel has no bearish expression at all. Every other scan in this repo is
two-sided by construction —

| gate | bull side | bear side |
|---|---|---|
| `screen_spreads` | PCS over `putExpDateMap` | CCS over `callExpDateMap` |
| `_apply_momentum_veto` | drops PCS when down-extended | drops CCS when up-extended |
| `regime_filter` | bearish regime → `allow_pcs = False` | bullish regime → `allow_ccs = False` |
| `passes_wall` | PCS short below the put wall | CCS short above the call wall |
| directional mode | bullish → PCS | bearish → CCS |

— so a long-only window would be the one asymmetric thing in the stack. Making
this window two-sided is also **free**: `screen_spreads` already loops both
expiry maps out of the **same chain object**, so the second side costs no extra
Schwab call.

**LEAPS is out of scope.** It shares no machinery with either half — different
horizon, different scoring, its own chain-thinning problem — so it earns its own
design.

## Decisions

### 1. A separate morning pass, not a third autoscan window

The autoscan runs every 15 minutes over ~23 symbols and already pulls two chains
per symbol (`chain_0`, `chain_swing`). A 30–45 DTE range is a third `fromDate`/
`toDate`, so it is a genuine third fetch — **~690 chain calls/day** at autoscan
cadence, for candidates that do not meaningfully re-rank inside fifteen minutes.

Run once in the morning instead: **~23 chain calls/day**, roughly 0.03% of the
audited ~68–76k/day, and it stays out of the 15-minute slot's wall-clock budget.
Schedule lives in `config/sessions.toml` `[slots.income]`, so the cadence is an
operator control rather than a code constant — the same treatment `[slots.analyze]`
gets because each of *those* firings costs money.

### 2. `equity_lots` is a new table, and it never holds reserved buying power

This is the load-bearing decision. `paper_account_db.reconcile_buying_power`
recomputes `buying_power_reserved` as `Σ OPEN paper_positions.max_loss_total` and
corrects drift against `cash`. Anything that reserves buying power outside that
sum gets **silently zeroed** at the next service start.

So an equity lot holds **cash converted into shares**, never a reservation.
Assignment is three moves, all of which the account already knows how to make:

1. the short put closes — `status='EXPIRED'`, `exit_reason='ASSIGNED'`, realized
   P&L is the credit kept (the existing settlement path);
2. `release_buying_power(strike_notional)` returns the reservation to cash (the
   existing close path already does this);
3. cash is debited `strike × 100 × qty` and an `equity_lots` row is inserted at
   `cost_basis = strike`.

Afterwards `reserved` is again exactly the open-position sum. **`reconcile_buying_power`
needs no change at all** — which is the entire reason to prefer a separate table
over a `kind` column on `paper_positions`, where every one of its many readers
would have to learn to filter and any one of them forgetting is a silent miscount.

**What does change is equity at cost.** `roll_session_if_needed` computes
`equity = cash + buying_power_reserved`, deliberately excluding open unrealized
P&L. Shares held at cost basis are committed capital by exactly that definition,
so the consistent extension is:

```
equity = cash + buying_power_reserved + Σ(open lots: shares × cost_basis)
```

Share *unrealized* stays excluded, matching how options are treated. Omitting the
term entirely would understate session-start equity for any session that opens
holding stock.

⚠ **Corrected 2026-09-05, measured rather than assumed.** An earlier draft of
this paragraph said omitting it would "quietly loosen the drawdown guard". That
is **not true today**: `session_start_equity` is written in three places in
`paper_account_db.py` and **read nowhere** in `services/`, `webgui/` or
`options-scanner/`. The live guard is `should_halt`, which compares
`session_realized_pnl + open_unrealized` against the absolute-dollar
`config_paper.MAX_SESSION_DRAWDOWN` and never consults it. The term is still
worth adding — a stored value should be right for its first reader — but the
justification is correctness, not an active safety hole.

### 3. Covered calls are struck at or above basis, and that is a hard floor

A call struck below cost basis books a guaranteed loss on the shares if called
away, and the premium rarely covers it. The candidate builder refuses to emit
one — not a warning, not a score penalty.

Each covered-call candidate carries `yield_on_cost` and `total_return_if_called`
alongside the usual fields, because those are the two numbers that actually decide
a covered call and the repo computes neither today.

### 4. The earnings gate must be extended, and this is where it matters most

`scanner_engine.screen_spreads` applies `check_earnings_conflict` only when
`trade_type == "SWING"`. At 5–15 DTE a straddled report is occasional; at
**30–45 DTE it is close to certain** — most names report inside any 35-day
window. The gate must cover `INCOME`, and it costs **zero** extra API:
`services/trade_svc/earnings_calendar.py` makes one bulk Alpha Vantage call a
night against a 25/day free tier, and the lookup is a local SQLite read.

### 5. Menu placement — tabs, not a new rail group

An earlier draft proposed a "Wheel" rail group. Once the window is two-sided that
is wrong: it is the same **find → analyze → track → repair** workflow the Options
group already encodes, at a longer horizon. Both pages become Options tabs:

```
Market Scanner · Strategy Finder · Income · Expected Move · Captured Signals
  · Paper Ledger · Paper Account · Shares · Rescue
```

`Income` sits in the *find* phase beside the other two scanners; `Shares` sits in
*track*, next to the two book views. The rail stays at 14 items and `NAV_SECTIONS`
is untouched.

⚠ Nine compact tabs is the most this strip has carried. If it wraps at a narrow
width, the fallback is to move `Shares` under ACCOUNT beside `/portfolio` — it is
the more separable of the two. Check this in a browser before calling the nav
done; it is not something a test will tell you.

## Cost

| Piece | Cadence | Chain calls/day |
|---|---|---|
| `INCOME` scan, both sides + CSP | 1×/day | **~23** |
| Covered-call candidates | same pass, held names only, mostly already in the watchlist | **0–10** |
| Earnings gate | local DB read | **0** |

Against ~68–76k/day. The second *side* of the spread scan costs nothing, as above.

**The cost that is not API** is payload. `cache:options:calc_chain` was once
8.77 MB — 53% of all prod Redis string bytes — before `thin_calc_chain` cut it
92%. A 30–45 DTE chain is wider than a 0-DTE one. Publish only the scored
candidates in `cache:options:income`; never the chain.

## Deliberately not built

- **LEAPS.** Own design, later.
- **Naked calls as a "short wheel".** The literal inverse of a wheel is selling
  undefined-risk calls, which `driver_policy`'s structure allowlist refuses on
  principle. The bearish expression at this horizon is the CCS the window already
  screens.
- **Autonomous trading of these signals.** The driver's envelope
  (`config/driver.toml`) is unchanged and `INCOME` is not added to its structure
  allowlist. This is a screen a human acts on.
- **Multi-lot cost-basis accounting (FIFO/LIFO/specific-ID).** One lot per
  assignment, closed whole. Partial disposal is a real feature and is not this one.

## Open risks

1. ~~**`bucket_key` and calibration.**~~ **Settled 2026-09-05 — no change needed.**
   `shared/calibration.family_key` passes an unrecognised family through
   **upper-cased rather than guessed**, and says why in its own docstring: *"a new
   scanner type should show up as its own bucket, not be folded into an existing
   one."* `_FAMILY_ALIASES` exists only to reconcile `scanner_type` `'0DTE'` with
   `trade_type` `'0-DTE'`; `INCOME` has no hyphenated variant, so it needs no
   alias and `test_cross_tier_mirrors.py` needs no edit. `INCOME` buckets appear
   on their own as outcomes accrue.
   ⚠ The one thing to hold to: the recorder must write **`INCOME`** as the
   `scanner_type` too. Two spellings would silently produce two buckets, which is
   the exact failure `_FAMILY_ALIASES` was added to fix for 0-DTE.
2. **Assignment is detected at settlement, from the underlying quote.** The
   settlement branch already defers a cycle when no quote is available. An
   assignment that defers past 15:00 CT on expiry day settles on the next cycle —
   correct, but it means a lot can appear a cycle late. Do not add a second
   detection path; one deferral mechanism is better than two that can disagree.
3. **Nine tabs.** See above — a browser check, not a test.
