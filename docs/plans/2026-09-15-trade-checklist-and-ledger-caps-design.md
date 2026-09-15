# A trade checklist, a capped Paper button, and "Why no trade?"

*Design, 2026-09-15. Decision-making tools #5 (Go / No-Go checklist), #6 (book-fit
preview) and #2 (why no trade on a symbol), chosen from the ideas list of the
same day.*

## What was asked for

Decision tools, not strategies, so that potential trades can be identified
quickly:

- **#5** — one strip per candidate that states every check a trade has to clear.
- **#6** — before opening a paper trade, whether the book's risk caps would refuse
  it and how much room is left.
- **#2** — for a symbol that produced nothing, which stage of the scan removed it.

## What the code settled first

### 1. The Paper button opens into a book with no caps at all

"Send to Paper trade" (`webgui/pages/options/handoff.send_to_paper`) enqueues
`paper_create`, which `compute.create_paper_trade` hands to
`paper_trader.create_paper_trade` → the **Paper Ledger** (`trades.db`). That path
checks the structure allowlist, a positive debit and command staleness. It does
**not** size, apply `MAX_RISK_PER_TRADE`, check cash, honour a halt, or run any
concentration cap; quantity is whatever is typed (1–100).

The six concentration rungs (`paper_concentration.concentration_reject`) bind
**only** the Paper **Account's** automatic entry cycle
(`paper_engine.run_entry_cycle`). `income_open` and `open_driver_position` do not
call them either.

So "would this be refused?" had no single answer. **Decision:** the Paper button
becomes capped, so the preview predicts exactly what the button will do.

### 2. A refusal never reaches the screen

A `paper_create` that raises is logged and dead-lettered by the consumer. The
page toast says "Sent to the paper ledger", and a refused trade is a button that
did nothing. `income_open` already solved this with a result view carrying `seq`
and a TTL (`handlers._publish_income_open`); that pattern is reused.

### 3. Most checklist inputs are already in Redis; the rules are not

Readable by Tier 1 today, for every watchlist symbol: `cache:options:matrix`
(`spot`, `flip`, `gex_regime`, `call_wall`, `put_wall`, `trend_state`,
`trend_dir`), `cache:sentiment:regime` (`committed_label`, `direction`) and
`cache:options:calibration`.

Not published anywhere: the cap values, the sector map, the volatility floors, and
any equity basis for the Ledger. `shared/sectors.py` and `shared/scanner_config.py`
are import-safe but not on the Tier-1 allow-list, and `paper_concentration` needs
path glue into a hyphenated app folder, which Tier 1 may never do.

### 4. Four defects this work has to handle

- **The Market Scanner's IV-rank floor reads a missing rank as 0**
  (`scanner_engine.py:2001`, `... .get("iv_rank") or 0`), so a symbol with no IV
  history loses every credit spread — the opposite of `shared/vol_gate`'s
  documented absence rule. **Decision: keep refusing**, but as a named rule with
  its own funnel line and a documented exception, not an `or 0` accident. Low IV
  rank is the strongest measured loser in the book, so letting unknowns through
  would loosen exactly where the evidence is worst.
- **Two reject counters never move on the path the live scan uses.**
  `liq_fail_long` and `credit_fail` increment only on the explicit-widths branch;
  on the auto-width branch every rejection inside `select_best_width` collapses
  into `no_width`. A funnel built on them would name the wrong cause.
- **Delta-band rejects and the 0.27 short-delta ceiling have no counter**
  (only `delta_pass` is counted).
- **The detail panel's "Signals like this" line reads the wrong scale on Finder
  rows.** `strategy_scoring.score_signal` overwrites `composite_score` with its
  Fit+Quality score (`strategy_scoring.py:898`) while the row keeps
  `trade_type="SWING"`, so `ev.calibrated_facts` looks up Market Scanner buckets
  built on a different score. A row carrying `fit_score` is on the Finder's scale.

### 5. The scan throws away the context a funnel needs

`cache:options:scan` is projected through `ScanResult`, which keeps only the
signal lists, VIX term structure, timestamp, errors and warnings. `iv_data` and
`symbols` are dropped, so **a symbol with zero signals has no IV rank anywhere in
Redis**.

## Decisions

| Question | Decision |
|---|---|
| Which book does book-fit describe? | Cap the Paper button: the Ledger enforces the caps against its own open trades |
| Per-trade limit on the Ledger | **$250**, the Account's `MAX_RISK_PER_TRADE` — one rule for both books |
| Ledger equity for the deployment cap | **$25,000 + the Ledger's realized P&L** from closed trades (moves on a close, never on a mark) |
| Rung order on screen | **Per trade first**, then deployment, symbol, sector, expiry — "lower the quantity" is the fix you can act on immediately |
| Architecture | Hybrid (C): the service stamps what is fixed per candidate, publishes the book, and one pure cap module is shared by all three callers; the page joins live context |
| Checks column refresh | On scan, Ledger-book or regime change, else every **5 minutes**; the detail panel and Paper dialog read live |
| Income Window | **Later.** Its button opens into the Account via `income_open`, which is also uncapped — the same decision as the Paper button, not taken silently |
| No IV history on the Scanner's floor | **Keep refusing**, named and documented |

## Part 1 — Ledger caps and enforcement

### `shared/book_caps.py` — the one cap module

Pure. Imports `math` and `shared.driver_policy.open_risk_dollars` (itself
math-only), nothing else. It works on plain data:

```python
position  = {"symbol", "expiration", "max_loss_total", "sector"}  # open book row
candidate = {"symbol", "expiration", "sector"}                     # + added_risk $
```

Sectors are resolved **before** the call, so the module never reads
`config/sectors.toml`. A `sector` of `None` skips the sector rungs for the
candidate and leaves that row out of every sector count, which is exactly today's
`_group_of → None` behaviour.

`evaluate(book, candidate, added_risk, limits, equity) -> list[Rung]`, every rung
in display order. Book rows keep `max_loss_total` (with the `max_loss × quantity`
fallback) so `open_risk_dollars` sums them unchanged:

| Rung | Code | Compares |
|---|---|---|
| Per trade | `TRADE_RISK_CAP` | candidate risk > `max_risk_per_trade` |
| Deployment | `DEPLOYMENT_CAP` | open risk + candidate > `max_deployed_risk_pct × equity` |
| Symbol positions | `SYMBOL_POSITION_CAP` | same-symbol count ≥ cap |
| Symbol risk | `SYMBOL_RISK_CAP` | same-symbol risk + candidate > cap |
| Sector positions | `SECTOR_POSITION_CAP` | same-sector count ≥ cap |
| Sector risk | `SECTOR_RISK_CAP` | same-sector risk + candidate > cap |
| Expiry positions | `EXPIRY_POSITION_CAP` | same-expiry count ≥ cap |

Each `Rung` carries `code`, `used`, `cap`, `after`, `binds`, and `skipped` (a
reason string, or `None`). **Skipped is never passed.** The skip rules copy
`concentration_reject` exactly, and they differ by rung:

- **Opt-in rungs** — per trade, deployment, both sector rungs — are skipped when
  their key is missing or zero, and deployment also when equity is missing,
  non-finite or not positive.
- **The three original rungs** — symbol positions, symbol risk, expiry positions
  — are always evaluated. A zero cap refuses everything, and a missing key is an
  error, both exactly as today.

Every risk sum goes through `open_risk_dollars`, so a NaN row cannot switch a
ceiling off.

`first_breach(rungs, order=...)` returns the first binding code. The Account keeps
its existing log order (deployment, symbol, sector, expiry, and no per-trade rung,
since its sizing handles that); the Ledger and the screen use display order.

`max_quantity(book, candidate, per_contract, limits, equity)` is the largest
quantity whose risk clears every **risk** rung. Count rungs do not depend on
quantity, so a binding count rung means 0.

### `concentration_reject` becomes a thin adapter

It resolves sectors exactly as today (`_group_of`, including the `?SYMBOL`
bucket and the raise-means-no-grouping rule), maps positions to the data shape
(`max_loss_total`, else `max_loss × quantity`), calls `book_caps.evaluate`, and
returns the first breach in its existing order. The reason constants move to
`book_caps` and are re-exported. This rewires the Account's automatic entry cycle,
so it is proven identical by test (see Testing).

### Enforcement in `compute.create_paper_trade`

1. Build the trade with `paper_trader.create_paper_trade(signal, qty)` as now.
2. Risk = that trade's `max_loss_total` — the checked number is the booked number,
   so the per-share/per-contract unit traps cannot separate them.
3. Book = the Ledger's own OPEN trades, sectors resolved with
   `shared.sectors.group_key`.
4. Equity = `config_paper.STARTING_BALANCE` + Σ `realized_pnl` of closed Ledger
   trades (a non-finite row is dropped, not summed).
5. Limits = `paper_concentration.default_limits()` plus
   `max_risk_per_trade = config_paper.MAX_RISK_PER_TRADE`.
6. Any rung binds → nothing is written; return a refusal. Otherwise persist.

The Ledger gets no drawdown halt: nothing asked for one, and it would need a
session concept the Ledger does not have.

### Every outcome reaches the screen — `cache:options:paper_create`

Written on **every** outcome, with a per-publish `seq` and a 600 s TTL, copying
`_publish_income_open`:

```json
{"seq": 12, "ts": "...", "status": "opened|refused|stale",
 "symbol": "ORCL", "type": "PCS", "expiration": "2026-10-17", "qty": 2,
 "trade_id": "ab12cd34",
 "code": "SECTOR_POSITION_CAP", "message": "Information Technology is full (5 of 5)",
 "max_quantity": 0, "rungs": [...]}
```

The stale-command branch publishes here too (it currently only appends to the R1
results list). The Scanner and Strategy Finder watch this view's version and toast
the outcome; the dialog's own toast stops claiming anything beyond "sent".

### The book, published for the preview — `cache:options:ledger_caps`

```json
{"limits": {...}, "starting_balance": 25000.0, "realized_pnl": -120.5,
 "equity": 24879.5,
 "open": [{"symbol", "expiration", "risk", "sector"}],
 "sector_of": {"ORCL": "Information Technology", "...": "..."}}
```

`sector_of` covers the scan universe plus open trades. Republished wherever
`refresh_paper_trades` runs (open, close, delete, expiry) and at startup.

### The Paper dialog is the full preview

Each row carries a service stamp `ledger_risk_per_contract` (Part 2). The dialog
runs `book_caps.evaluate` over `ledger_caps` and renders one line per rung:

```
ORCL put credit spread · 2026-10-17 · risk $182 per contract
Per trade     $182 of $250               ✓   (max 1 contract)
Deployment    $1,240 → $1,422 of $4,976  ✓
Symbol ORCL   2 → 3 of 3 positions  ✓    $410 → $592 of $750 ✓
Sector IT     5 of 5 positions           ✗   Information Technology is full
Expiry 10-17  3 → 4 of 5                 ✓
[Create disabled: sector full]
```

The quantity box is capped at `max_quantity` and the lines recompute as it
changes. A row with no stamp, or a missing `ledger_caps` view, says the preview is
unavailable and that the service will still check — never a false green. The
service re-checks on every click, because the book can change between opening the
dialog and pressing Create.

## Part 2 — The checklist

### Checks

Green, amber or grey. A check that does not apply to a structure is **omitted**,
not shown as passing. Only book fit can be red: every other hard gate already
removed its failures before a row reached the page, and the rest are judgment.

| Check | Green | Amber | Source |
|---|---|---|---|
| Book fit | clears every rung | red: blocked, with the reason | `ledger_caps` + `ledger_risk_per_contract` |
| Earnings | no report before expiry | a report lands before expiry (date shown) | stamp `earnings_status` / `earnings_date` |
| Vol rank | ≥ floor + 10 | within 10 points above the floor | stamp `vol_floor` + `iv_rank`; short-premium (negative `net_vega`) only |
| Cost to trade | bid-ask round trip ≤ 10% of credit/debit | > 10%, worded "very wide" above 25% (amber, not red: red means blocked) | stamp `friction_pct`, every leg |
| Strike vs expected move | short strike ≥ 1 expected move from price | inside 1 | stamp `em_to_expiry` ($) + live `spot` from the matrix |
| Strike vs wall | short strike beyond the put wall (PCS) / call wall (CCS); iron condor both | inside the wall | matrix `put_wall` / `call_wall`, live |
| Dealer gamma | price above the flip | below the flip | matrix `gex_regime`, live; short premium only |
| Direction | structure agrees | structure opposes | 0-DTE: the symbol's intraday `trend_dir`; longer holds: market `direction`; labelled with which; grey when there is no direction |
| Track record | calibrated EV > 0 | ≤ 0 | calibration view; rows **without** `fit_score` only |

Amber and green thresholds for vol-rank margin, friction and expected-move
distance are module constants in `checks.py` — they have a single consumer, so a
TOML would relocate them rather than deduplicate them.

### Row stamps (Tier 2)

One function, `compute.stamp_candidate(row, context)`, applied at the scan,
Strategy Finder and Income publish points (Income is stamped now so its later
checklist has the data; it gets no column yet):

| Stamp | How |
|---|---|
| `ledger_risk_per_contract` | `paper_trader.create_paper_trade(row, 1)["max_loss_total"]` in a try/except → `None`; the same code the Ledger books with |
| `earnings_status`, `earnings_date` | `compute.scan_earnings`; Scanner and Directional rows lack it today |
| `vol_floor` | `shared.scanner_config.min_iv_rank()` for the row's trade type |
| `friction_pct` | Σ over legs of (ask − bid) ÷ \|credit or debit\| per share; `None` if any leg lacks a quote |
| `em_to_expiry` | daily expected move × √max(DTE, 1) — the convention `strategy_scoring.score_all(daily_move=…)` already uses, so the check and the score measure distance the same way. Daily move: `expected_moves.daily.move_dollars` on Scanner rows; `compute.swing_scan` stamps its own `dem` as `daily_em` |

Scanner rows need no new quote fields: `spread_bid` / `spread_ask` are built
from BOTH legs (`short.bid − long.ask`, `short.ask − long.bid`), so their
difference already is the round-trip width, and an iron condor sums its two
sides'. Normalized rows without them sum `ask − bid` over their `legs`.
`scan_day` rows written before the stamps exist read as unchecked for those lines.

### `webgui/pages/options/checks.py` (Tier 1, pure)

`build_checks(row, matrix_row, regime, calibration, ledger_caps, qty=1) -> list[Check]`
and `summary(checks) -> Chip`. No nicegui import. Rows fed to its tests come from
the real producers' output passed through `stamp_candidate`, never from invented
dicts.

### Where it shows

- **Checks column** on the Market Scanner 0-DTE, Swing and Directional tables and
  the Strategy Finder table: `Blocked · sector full` (red), `2 cautions` (amber),
  `Clear · 7 checked` (green — the count so "clear" is not read as "passed
  everything"), `unchecked` (grey, a required view missing).
- **"Only clear" toggle** on both tables — hides blocked and cautioned rows.
- **Trade detail panel**: the full list at the top, one line and reason per check
  (*"Short 145 put is 0.7 expected moves below price; put wall at 142"*). The
  existing "Signals like this" line gains the `fit_score` guard.
- **Paper dialog**: the book rungs (Part 1) plus a one-line summary of the rest.

The Checks column recomputes on a scan, `ledger_caps` or regime version change,
and otherwise every 5 minutes; the detail panel and dialog read the views live.

## Part 3 — "Why no trade?"

### Truthful counts in `scanner_engine`

- `select_best_width` gains an optional `reasons` counter. Each short strike's
  failure is attributed to the furthest stage its **best** width reached, in
  order: long leg missing · long leg mark · long leg liquidity · no credit ·
  sanity cap · credit floor · edge floor · over the per-trade cap · no contracts ·
  no positive expected P&L. The return value and the single caller are unchanged.
- New counters for delta-band rejects and the 0.27 short-delta ceiling.
- The IV-rank floor becomes an explicit rule: `iv_rank is None` → removed as
  `NO_IV_HISTORY`, counted separately from a rank below the floor. Behaviour is
  unchanged; the `or 0` goes.

### The per-symbol funnel

Collected in `run_full_scan` per symbol and per bucket (`0DTE`, `SWING`,
`DIRECTIONAL`), in two sections because the units differ:

- **Short strikes** — in the DTE window · in the delta band · priced · under the
  delta ceiling · inside the expected-move window · short leg liquid · width
  found (with the reason breakdown).
- **Spreads** — built · removed by the momentum veto · removed by the regime side
  filter · removed by the IV-rank floor (below floor / no IV history) · removed by
  the index dealer-gamma gate · kept after the per-symbol cap.
- **Whole-symbol stops** — no quote · no chain · IV analysis failed · expirations
  skipped for earnings (with the date).
- **Context** — price, IV rank, earnings date.
- **Directional** — chain empty · removed by the volatility gate · below the score
  or grade cut.

### `cache:options:scan_funnel`

Its own view, validated on write by a new `ScanFunnel` contract in
`shared/contracts/options.py`, written with `skip_unchanged`. Not folded into
`cache:options:scan`: the `ScanResult` projection would drop it, and every scan
reader would pay for bytes it never shows. Estimated ~50 KB for 80 symbols (to be
measured).

### The panel

A **Why no trade?** panel on the Market Scanner. The symbol picker lists symbols
that produced nothing in a bucket first, as chips. One card per bucket: a
plain-language headline built from the binding stage (*"MU · Swing: 38 short
strikes priced, and every width that cleared the credit and edge floors cost more
than the $250 per-trade cap"*), then the stage list with counts remaining and the
binding stage highlighted. A symbol the scan never reached, or a funnel older than
the last scan, is said in words — never shown as zeros. The Strategy Finder
already shows its own drop counts and is out of scope.

## Part 4 — Testing and rollout

Five steps, each shipped and verified on its own:

| # | Step | Changes behaviour? |
|---|---|---|
| 0 | Measure, on prod's live boards, the share of Scanner, Finder and Directional candidates whose single contract risks more than $250 | no |
| 1 | `shared/book_caps.py`; `concentration_reject` rewired | **no** — proven by equivalence |
| 2 | Ledger enforcement + `cache:options:paper_create` + toasts | **yes** — the Paper button can refuse |
| 3 | Row stamps + `cache:options:ledger_caps` + the Paper dialog preview | no (display) |
| 4 | `checks.py`, Checks column, Only clear, detail-panel list, `fit_score` calibration guard | display only; the track-record line disappears on Finder rows |
| 5 | Funnel counters, `NO_IV_HISTORY`, `cache:options:scan_funnel`, the panel | no (display; signals identical) |

Tests that carry the weight:

- **Step 1.** A frozen copy of today's `concentration_reject` in the test file,
  compared with the adapter over seeded random books (mapped and unmapped
  symbols, shared sectors and expiries, NaN risks, missing equity, missing keys).
  Rung arithmetic, skipped ≠ passed, `max_quantity`.
- **Step 2.** Against a temporary `trades.db`: refused → nothing persisted and a
  refusal published; opened → `trade_id` published; two identical refusals → two
  `seq`; stale → published. The discriminating test monkeypatches the limit and
  proves it binds.
- **Step 3.** `ledger_risk_per_contract` equals what the Ledger books at quantity
  1 for a raw PCS, a normalized PCS, an iron condor and a debit vertical, each
  built by the real producers — the check that catches a 100× error.
- **Step 4.** Each check's tone table from stamped producer output; a Finder row
  never gets a scanner calibration bucket; new page code in
  `test_no_inline_style.py`; a fresh-interpreter import proves `shared.book_caps`
  pulls in no engine.
- **Step 5.** A fixture chain emits identical signals before and after the counter
  change; stage counts never increase down the funnel; the contract validates.

Verification per step: tests, then the local page harness (fake Bus + real
handlers + synthetic chain) for the page on Windows, then a Redis-driven check
against the running service. Promotion is the operator's call; the suggested
window is 15:25–16:15 CT, since a promote stops the whole target.

Docs move with each step: CLAUDE.md (allow-list gains `shared.book_caps` and
`shared.driver_policy`; invariant "the Ledger is capped, and there is one cap
module"), `docs/webgui-routes.md`, the User Guide (Paper dialog), the Reference
Guide (Checks, Why no trade?), `webgui/page_help.py`, and the CHANGELOG.

## Out of scope, recorded

- **Income Window** checklist and caps on `income_open`.
- **The driver's book** — `open_driver_position` still does not run the
  concentration rungs (open decision 7 of the gap assessment).
- A Ledger drawdown halt.
