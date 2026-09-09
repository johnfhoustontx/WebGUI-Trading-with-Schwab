# Concentration caps and a working earnings gate — design

**Date:** 2026-09-09
**Trigger:** the paper book held fourteen open positions and every one was ORCL.

## What was measured

Read from prod on 2026-09-09:

| | |
|---|---|
| Open paper positions | **14 of 14 ORCL** |
| Max loss reserved | **$2,829, all of it ORCL** |
| Account equity | $24,490 (from a $25,000 start) |
| Share of the account in one name | **11.6%** |
| Structure | every one a PCS — short puts, one direction |
| Expiry | every one **2026-09-11** |
| ORCL earnings | **2026-09-10**, the day before |

Fourteen tickets read as a diversified book. They were one bet sliced fourteen
ways: one name, one direction, one expiry, over one event.

The correlated-loss mode had already fired. On **2026-09-01 ORCL fell
149.12 → 141.32** (low 139.95) in one session and eight ORCL spreads hit
DELTA_STOP / MONEY_STOP the same day. **The stops worked correctly** — that is
the point. Nothing was wrong with the exits; the book was simply undiversified,
so one name's one bad day emptied a third of it at once.

The captured-signals ledger says the same thing more bluntly: ORCL has **31
closed signals, 1 winner, −$683**, and every exit was a stop. Not one reached
`EXPIRED` or `MANUAL_CLOSE`, the two outcomes that carry all of the ledger's
tracked profit (691 of 883 closes). September's captures were **19 ORCL of 25**.

## Three gaps, and why each existed

### 1. The engine had no rung between one trade and the whole account

`config_paper.py` held `MAX_RISK_PER_TRADE = 250` and
`MAX_SESSION_DRAWDOWN = 2500` and nothing in between, so a book could be
entirely one name and clear both ends. Per-symbol caps existed elsewhere and
reached nothing here: `config/scanner.toml`'s `max_per_symbol` bounds a *single
scan's output*, and `driver_svc/guardrails.py`'s one-per-symbol rule governs the
driver book, which is empty.

### 2. The earnings gate never fired on the live scan

`screen_spreads` has had an earnings gate for months, reading
`if earnings_date and trade_type in EARNINGS_GATED_TRADE_TYPES`. **`run_full_scan`
never passed an `earnings_date`**, so the first conjunct was always False. The
`options-scanner/data/earnings_cache.json` it was nominally fed from holds
`"date": null` for all seventeen symbols in it and was last written 2026-08-29.

Meanwhile `services/trade_svc/data/earnings_calendar.db` — filled nightly and
already read by the income window through `shared/earnings.py` — held
`('ORCL', '2026-09-10')` from 2026-09-07, two sessions before the captures.

### 3. The gate's exemption rested on a false premise

The comment justifying the "0-DTE" exemption said such a position "is flat by
the close" and so cannot be held through a report. **The bucket spans DTE 0..4**
(`zerodte_max_dte = 4`; the bucket semantics are documented on
`is_short_strike_in_em_window`). All sixteen ORCL captures on 2026-09-08 were
`scanner_type=0DTE` with `dte_at_entry=3`. The premise is true at DTE 0 and
false at every other DTE in the bucket.

⚠ **This was originally scoped as "the 0-DTE scanner emits 3-DTE trades, probably
because ORCL lacks daily expirations — add a DTE ceiling."** That was wrong.
The 0..4 span is a deliberate 2026-05-21 design and 4 already *is* the ceiling.
The real defect was the exemption, not the range.

## What was built

### Caps — `options-scanner/paper_concentration.py`

A pure decision over a book and a candidate, unit-testable without a broker,
client or database:

```
concentration_reject(positions, symbol, expiration, added_risk, limits=None)
    -> "SYMBOL_POSITION_CAP" | "SYMBOL_RISK_CAP" | "EXPIRY_POSITION_CAP" | None
```

Policy in `config_paper.py`, read at **call** time so an edit plus a restart
moves the engine: `MAX_POSITIONS_PER_SYMBOL = 3`, `MAX_RISK_PER_SYMBOL = 750.0`
(~3% of the account), `MAX_POSITIONS_PER_EXPIRY = 5`.

Three decisions worth recording:

* **The expiry cap is book-wide, not per name.** Five positions expiring the
  same Friday is date concentration even when no single name is over its own
  cap.
* **A refusal SKIPS without recording a rejected order.** Unlike
  `RISK_TOO_HIGH`, the condition is transient — it describes the book at this
  instant, not the signal. An order row would make `has_order_for_signal`
  blacklist the signal permanently, so a name that freed up an hour later could
  never be entered. A test drives exactly that: cap at 1, open, close, re-run,
  the second signal gets in.
* **The risk sum goes through `shared.driver_policy.open_risk_dollars`.** It
  drops non-finite rows instead of poisoning the total, and a NaN total makes
  every `>` comparison False — silently switching the ceiling off while the code
  still reads like a guard. That is the repo's documented pins-the-bound trap;
  reusing the one hardened summation beats a tenth copy of the guard.

Checked twice in `run_entry_cycle`: before the broker call with `added_risk=0`
(so a name already at its position or expiry limit costs no round-trip), and
again on the real fill where the dollar risk is known.

### A working gate — `scanner_engine.scan_earnings_dates`

One calendar read per scan over `EARNINGS_CALENDAR_DB`, threaded into all four
`screen_spreads` call sites in `run_full_scan`.

⚠ **A `None` from it means "no date to gate on", not "no earnings".**
`shared.earnings.coverage` draws a three-valued distinction and `"not_listed"`
is genuinely unknown. This resolver deliberately does not surface it: the scan's
gate can only act on a date, and failing closed on unknown would empty the
watchlist whenever vendor coverage thins — measured at 1,814 symbols in the near
month and 11 by March. The income window, which *stamps* rows rather than
dropping them, is where that distinction is carried. This was an explicit
choice, not an oversight.

### A correct exemption — `scanner_engine.earnings_gate_applies`

```
earnings_gate_applies(trade_type, dte) -> bool
```

`SWING`/`INCOME` always gated; `0-DTE` gated at every DTE except 0; unknown
trade types ungated, matching the deliberate fail-open default the liquidity
gate documents. An **unreadable** `dte` does not earn the exemption — the
carve-out rests on knowing the expiry is same-day. That fails closed on the
*exemption*, not on the gate: nothing is dropped unless a report actually
straddles.

⚠ **The predicate replaces the bare tuple test at BOTH mirror sites.**
`screen_spreads` gates the adapted credit spreads; `options_svc.compute.swing_scan`
gates the builder families the engine never sees. `EARNINGS_GATED_TRADE_TYPES`
was exported precisely so the two could not drift — and keying off the tuple was
no longer enough once the decision depended on DTE. `swing_scan`'s filter is now
**per signal** rather than per scan, because one scan spans a DTE range: within
a 0..4 request the same-day candidates keep the exemption while the overnight
ones do not. An AST guard in
`services/options_svc/tests/test_earnings_gate_mirror.py` fails if that site ever
restates the membership test again.

## What was deliberately not done

* **No DTE ceiling on the 0-DTE bucket.** 0..4 is the design; 4 is the ceiling.
* **Nothing was closed.** The fourteen open ORCL positions are the operator's
  decision, and the caps apply at open time only.
* **`not_listed` does not block.** See above.
* **No sector or correlation cap.** Three tech names in one basket is a real
  exposure the per-symbol cap does not see. It needs a sector map the paper
  engine does not have, and guessing one would be worse than the gap.

## Testing note

The `swing_scan` mirror harness produced an empty signal list on its first run
because a monkeypatched double had the wrong signature — which made every "was
dropped" assertion pass for the wrong reason. `test_the_harness_itself_produces_a_row`
now guards every other assertion in that file.
