# Options, paper books and trade selection: the invariants and their evidence

> Moved here VERBATIM from `CLAUDE.md` on 2026-10-04 (audit CQ-09), when
> that file was cut from 327 KB to under 100 KB. `CLAUDE.md` keeps the rules;
> this file keeps the detail behind them: the incident, the measurement and
> the reasoning. It is a living reference. Correct it in place, exactly as
> the rules in `CLAUDE.md` say, and never append a correction under a wrong
> sentence. Its section headings are the ones `CLAUDE.md` used.

## An equity lot is cash CONVERTED, never a buying-power reservation

The manual paper book holds shares as well as options (`equity_lots` in
`paper_account_db`, created by put assignment at expiry). **The rule that makes
that safe is that a lot never touches `buying_power_reserved`, and nothing may
make it.** `reconcile_buying_power` recomputes `buying_power_reserved` as
`Σ OPEN paper_positions.max_loss_total` and hands the difference back to cash, so
**anything that reserves outside that sum is silently zeroed at the next service
start**. A lot is cash already spent on stock; the reservation the short put held
is released by `_close` on the ordinary settlement path, and `debit_cash` then
pays for the shares.

That is the whole reason `equity_lots` is a separate table rather than a `kind`
column on `paper_positions` — every one of that table's many readers stays
correct without learning to filter, and any one of them forgetting would be a
silent miscount. Three corollaries, each of which has a test because each is
invisible when wrong:

* **Do not add a second `release_buying_power` to `_assign_shares`.** `_close`
  already returned it, and for a cash-secured put that reservation IS the strike
  notional — a second release credits it twice and the resulting lot looks
  identical. The one assertion that catches it is
  `reconcile_buying_power(db) == 0.0`.
* **Do not book the purchase through `realize_pnl`.** It would report the
  purchase price as a realized loss and, at a whole strike notional, trip the
  session drawdown halt on a trade that lost nothing. Hence `debit_cash`, which
  touches neither reserved BP nor realized P&L.
* **`session_start_equity` includes `Σ(open lots: shares × cost_basis)`** — at
  cost only, since a lot's basis is committed capital by the same definition that
  puts `buying_power_reserved` there, while its mark is unrealized and stays out.
  `reset_account` therefore clears `equity_lots` too, or the next session opens
  claiming committed capital the account no longer has.

## The paper engine's risk envelope has SIX rungs, not two

`config_paper.py` held only `MAX_RISK_PER_TRADE` (one trade) and
`MAX_SESSION_DRAWDOWN` (the account), so a book could be **entirely one name**
and clear both ends. On 2026-09-08 it was: 14 open positions, all ORCL, $2,829 —
11.6% of a $24,490 account — one direction, one expiry, over a report the next
day. **`paper_concentration.concentration_reject`** is the missing middle,
enforced at `run_entry_cycle` — and since 2026-09-15 a thin adapter over
`shared/book_caps.py` (see the next section): `MAX_POSITIONS_PER_SYMBOL` ·
`MAX_RISK_PER_SYMBOL` · `MAX_POSITIONS_PER_EXPIRY` (**counted across symbols** —
five positions on one Friday is a bet on a date).

⚠ **A concentration breach SKIPS without recording a rejected order**, unlike
`RISK_TOO_HIGH`. The condition is transient — it describes the book at this
instant, not the signal — and an order row would make `has_order_for_signal`
blacklist the signal permanently, so a name that freed up an hour later could
never be entered. **Accepted consequence, for the automatic Account's entry
cycle: it leaves no trace in the UI**, only the journal
(`SKIPPED <sym> <reason> (concentration cap)`); the visible symptom is a good
signal that never opens. Do not "fix" that by recording the row. (A Paper-button
trade into the Ledger is the opposite case: a click is a person waiting, so its
refusal comes back as a toast.)

**The FIFTH rung is the book-wide deployment cap** (`MAX_DEPLOYED_RISK_PCT`,
0.20): total open max loss as a fraction of equity. The four above are per trade,
per symbol, per expiry and per account-drawdown — so three symbols at the $750
symbol cap is $2,250 and clears every one of them. 0.20 is the **tight end** of
the published range (theoptionpremium 20–25%; Option Alpha keeps 40–50% in cash,
i.e. 50–60% deployed). Measured on the live book 2026-09-11: $1,933 committed
against $24,184 equity — 8.0% — so it is a real ceiling with ~11 more $250 spreads
of headroom rather than something that bites on day one.

The denominator is **`session_start_equity`, and this is that field's FIRST
reader** — it was written correctly and consumed by nothing, its own comment
saying so. Right here for two reasons beyond being free: it already includes
`equity_at_cost` (share lots are committed capital), and being fixed for the
session it gives a **stable** ceiling, where a live-equity denominator would
loosen the cap as unrealized P&L ticks up and tighten it on a blip — how much the
book may commit would depend on the minute you asked. Intraday losses are the
drawdown halt's job; live-equity *sizing* is B8.

⚠ **No equity SKIPS the cap rather than treating it as zero**, and a `limits` dict
without the key keeps the pre-cap behaviour — a fraction of an unknown cannot be
enforced, and a zero denominator would refuse every trade forever, which reads as
a broken engine. ⚠ **A hand-opened cash-secured put consumes it fast**: that
structure's `max_loss_total` is the whole strike notional, so one $15k CSP is 62%
of this book and the auto entry cycle then stops opening spreads — correctly (the
cash really is committed) but invisibly, since the entry cycle's concentration
breach leaves no UI trace. Check the journal before assuming the engine is stuck.

The risk sum goes through **`shared.book_caps.open_risk_dollars`** rather
than a local `sum(...)` — a NaN total makes every `>` False and silently
switches the ceiling off, the documented pins-the-bound trap.

**The SIXTH rung is the SECTOR cap** (`MAX_POSITIONS_PER_SECTOR` 5 /
`MAX_RISK_PER_SECTOR` $1,500, grouped by `config/sectors.toml` through
`shared/sectors.py`). Four DIFFERENT semiconductors at the full symbol cap breach
nothing above it, and that is the correlated book the playbook warns about.
Measured before it was built: the (since removed) **driver's** book once held **$21,531 across 15
Information Technology positions — 86% of a $25,000 account in one sector** — and
$15,018 across nine INDEX positions; the manual book peaked at $3,569 across 19
IT positions. A $1,500 cap would have bound on 20 of 46 manual trading days and
35 of 40 driver ones. $1,500 is two symbols at the full symbol cap and ~31% of the
deployment ceiling, so filling the book needs four sectors; it sits strictly
between the rungs either side, which is the test of whether a rung exists at all.

**The premise was measured, not assumed** — two of this audit's rationales have
already failed that way. Across six months of daily returns on the tradeable
watchlist, mean pairwise correlation is **0.250 within** a sector against **0.017
across**, 14 of the 15 most-correlated pairs share one, and the top decile of
correlated pairs is 63% same-sector against a 21% base rate. ⚠ The grouping is
weakest where the book concentrates: **Information Technology is 30 of the 74
tradeable names at only 0.240 internal correlation**, because it holds IBM and TXN
beside IONQ/RGTI (0.939) and CRWV/NBIS (0.838) — so it under-controls the
AI-datacenter cluster, which is an argument for the tight end, not for a different
taxonomy. ⚠ **Indices are a BUCKET, not an exemption**, and that is measured too:
SPY/QQQ/DIA/IWM correlate **0.799** pairwise, the second-tightest group after
Energy, so they share one `INDEX` bucket.

⚠ **`config/sectors.toml` exists because both data sources the assessment
proposed fail.** `config/symbols.toml`'s `sectors` is the SPDR ETF collection
list, not a map; and `sentiment-dashboard/sectors_ref.py`'s workbook covers **48
of 80 watchlist symbols**, missing MU, AMAT, MRVL, INTC, TXN, ALAB, SMCI, DELL and
SPCX — *the semiconductors the cap is for* — so **181 of 273 historical paper
positions (66%) were on symbols it had never heard of**. It also lists 20 symbols
under two sectors, where TOML would silently keep the last. The new map is
hand-maintained (nothing here derives a sector, and Schwab returns a description,
not a classification), so the safety is structural rather than a promise:
**`shared.sectors.group_key` gives an unmapped symbol a bucket of its OWN**
(`"?<SYMBOL>"`), which means a new name is capped exactly as before by the tighter
per-symbol rungs and can neither borrow another sector's allowance nor drag
unrelated names into one — and `paper_engine._log_capped` prints the bucket, so a
`?` in the journal is the map saying it does not know that name. `sector_of` is
injected into `concentration_reject` (defaulting to the real map) so the decision
stays pure; a lookup that RAISES degrades to "no grouping", never to a refusal.

Design: [the B4 doc](../plans/2026-09-12-sector-cap-design.md).

## The Paper Ledger is capped, and there is one cap module

**`shared/book_caps.py` evaluates every paper-book rung in ONE place** — per
trade, deployment, symbol positions, symbol risk, sector positions, sector risk,
expiry positions — over plain rows, and reports **every** rung (`used`, `after`,
`cap`, `binds`, `skipped`) rather than only the first breach, plus `first_breach`,
`max_quantity` and `describe` (the sentence both the Paper toast and the Paper
dialog's preview lines show). It imports
only `math` (`open_risk_dollars` lives in it), which is what puts it on the
Tier-1 allow-list. The Account's `concentration_reject` is an adapter over it, and
`options-scanner/tests/test_book_caps_equivalence.py` holds a frozen copy of the
pre-module function and proves identical decisions over generated books — **edit
the rungs in `book_caps`, never in the adapter.**

**The Ledger — the book the Paper button opens into — enforced no cap at all
until 2026-09-15.** `options_svc.compute.create_paper_trade` now evaluates the
rungs against the Ledger's **own** open trades and **writes nothing** on a
refusal. Three decisions in it are load-bearing:

- **It checks the risk it would BOOK** — the `max_loss_total` of the trade
  `paper_trader` builds — never a figure recomputed from the signal, so the
  per-share / per-contract unit traps cannot separate what was checked from what
  was stored.
- **`book_caps` counts an unusable candidate risk as ZERO** (right for the Account,
  whose entry cycle sized the trade first), so **any non-Account caller must refuse
  a non-positive or non-finite risk BEFORE calling `evaluate`** — or a trade with
  no readable max loss passes every risk rung.
- **Per-trade limits are two keys, one per book, both $750 since 2026-09-22**
  (`config/paper.toml` via `shared/paper_limits.py`, surfaced as module constants
  by `config_paper`). `MAX_RISK_PER_TRADE` is the Account's and also sizes the
  Market Scanner's widths (`DEFAULT_MAX_RISK_DOLLARS`); `LEDGER_MAX_RISK_PER_TRADE`
  is the Ledger's and sizes the Strategy Finder's and Income Window's credit
  spreads, because `swing_scan` passes it to `screen_spreads` at call time — their
  trades book into the Ledger. At $750 one maximum-size trade fills its symbol's
  $750 risk cap in either book. The six concentration caps are the Account's. Ledger equity for the deployment cap is
  `STARTING_BALANCE` + realized P&L of its closed trades — it moves on a close,
  never on a mark.

**Every click the options service consumes is answered** — nothing publishes
while the service is down. `handlers` publishes each outcome — opened, refused,
stale, error — to **`cache:options:paper_create`** (600 s TTL, a per-publish
`seq`), including after `create_paper_trade` raises, before dead-lettering: a
refusal the screen cannot see is a button that does nothing. The Market Scanner
and Strategy Finder watch it through `handoff.watch_paper_results`, and ⚠ that
watch must compare versions with **`!=`**, since the TTL expiring resets the
`:ver` counter to 1. A quantity that is not a whole number ≥ 1, a malformed
signal, or an unreadable max loss is an **error** outcome, never a write.

**The Paper dialog previews the SAME decision before the click**
(`handoff.paper_dialog_view` over the pure `pages/options/book_fit.preview`),
reading **`cache:options:ledger_caps`** once on open and the row's stamps. Two
rules had to become ONE before the two ends could agree:

- **One bucket rule, `book_caps.sector_bucket(table, symbol)`.**
  `shared.sectors.group_key` delegates to it over the loaded table, and the page
  runs it over the table the service publishes — so `ledger_caps` carries the
  **WHOLE** `config/sectors.toml` table, never a map derived from the watchlist
  (which left a Strategy Finder symbol's sector rungs unchecked in the preview).
  `test_sector_bucket_parity.py` freezes `group_key`'s pre-delegation body.
- **One rounding rule, `book_caps.booked_risk(basis, qty)`.** `paper_trader`
  books every `max_loss_total` through it, the stamp `ledger_risk_basis` is the
  UNROUNDED figure it books from, and the preview computes every quantity from
  that basis. ⚠ **Never preview from the cent-rounded `ledger_risk_per_contract`
  × qty**: a $1.87504 per-share spread stamps $187.50, previews four contracts at
  $750.00 inside a $750 limit, and books $750.02 — refused.
  `test_ledger_booking_identical.py` pins the booking byte-identical; a NaN, inf or
  bool max loss now books `None`, which the service refuses with a degrade.

`refresh_ledger_caps` writes under **`_LEDGER_CAPS_LOCK`** — the command consumer
and the manage tick call `refresh_paper_trades` on different executor threads,
and interleaved an OLDER book overwrites a newer one until the next Ledger change
— from a **`finally`** in `refresh_paper_trades` (the book follows every Ledger
change even when that view raises), with `skip_unchanged`, plus once at startup.
⚠ **A dialog that cannot preview never blocks.** No view, no stamp or a malformed
limits map is "can't preview", and Create stays enabled because the service still
checks; only a known breach, a bad quantity or one above the dialog's ceiling of
100 disables it — missing data must never become a refusal on the page. The
quantity box's max is the largest fitting quantity, never below a typed quantity
that fits (`max_quantity` can land one short on sub-cent risk and the box clamps on
blur). The **checklist's Paper book line is the same `book_fit.preview` at
quantity 1**, so a row's chip and the dialog cannot disagree — and it obeys the
same rule: no preview is a GREY line, never `Blocked`. It is why "Only clear"
filters on its own stamp (`stamp_checks`'s `_checks_clear`) rather than on the
verdict: a grey book line leaves the chip Clear while the fit was never tested.
**`services/options_svc/tests/test_preview_agrees_with_ledger.py` is the
guard**: real books through the real publish and `create_paper_trade`, preview
and Ledger compared line for line, sub-cent risk and the suggested-quantity
step-down included. A change to either end that it does not cover is unguarded.

**Every mutation of either paper book runs under ONE lock,
`paper_lock.BOOK_LOCK`** (re-entrant; the `@paper_lock.serialized` decorator).
The scheduler's cycles and the command consumer are different executor threads,
and until 2026-10-03 nothing serialized them — the read-book-then-insert above,
or a rule close racing a Rescue apply. A function that writes `paper_positions`,
the account row, `equity_lots` or the Ledger takes the decorator; an AST test
(`test_paper_book_lock.py`) fails on a `compute` writer without it. ⚠ It covers
ONE process: a second writer process needs a database-side guard. **A close is
one transaction on a row that is still OPEN** (`close_position_and_settle`):
`close_position` returns False for a row already closed and every caller stops
there, where a second close used to overwrite the first's realized P&L.
Accepted limits:
**Delete all closed** on the Paper Ledger removes realized history, so equity —
and the deployment cap — moves with it; an old open row with no usable max loss
counts **$0** toward the risk sums (it still counts toward the position caps); and
because the $750 per-trade limit equals the $750 per-symbol risk cap, one
maximum-size trade fills its symbol. Design:
[the doc](../plans/2026-09-15-trade-checklist-and-ledger-caps-design.md).

## ⚠ The "0-DTE" bucket spans DTE 0..4 — the name is a WINDOW LABEL, not a DTE

`zerodte_max_dte = 4` (a deliberate 2026-05-21 design; the semantics are on
`is_short_strike_in_em_window`). So a `scanner_type=0DTE` row is routinely a
multi-session hold — all sixteen ORCL captures on 2026-09-08 carried
`dte_at_entry=3`. **This misled the code, the manuals and the operator at once:**
the earnings gate exempted the whole bucket because such a position "is flat by
the close", and `page_help.py` plus the Reference Guide both told the reader it
"expires TODAY".

**The gate's decision is `scanner_engine.earnings_gate_applies(trade_type, dte)`,
never a membership test on `EARNINGS_GATED_TRADE_TYPES`.** That tuple is still
the underlying data, but the exemption now needs DTE, so both mirror sites —
`screen_spreads` and `options_svc.compute.swing_scan` — must call the predicate
or they drift; `swing_scan` decides **per signal**, since one scan spans a DTE
range — dropping the row, or in the Strategy Finder's `earnings_mode="flag"`
tagging it, over exactly the same set. An AST guard in `test_earnings_gate_mirror.py` pins it.

⚠ **`run_full_scan` passed NO `earnings_date` until 2026-09-09**, so
`if earnings_date and ...` was always False and the gate was a no-op on every
live scan — while reading exactly like protection. `scan_earnings_dates` now
reads `EARNINGS_CALENDAR_DB` once per scan. Its `None` means **"no date to gate
on", not "no earnings"**: `not_listed` deliberately does not block, because
failing closed would empty the watchlist whenever vendor coverage thins. The
older `data/earnings_cache.json` is dead — all-`null` since 2026-08-29.

## Exit rules are PER STRUCTURE, and the Income Window's two are the reason

**`shared/trade_mgmt.structure_rules(strategy)` is the one accessor** — `[stops]`
overlaid by that structure's `[structures.<canonical name>]` table in
`config/trade_mgmt.toml`. `signal_recommender.recommend` resolves it **per
position**, which is why it is the one thing in that module that is not a
module-level constant: unlike every other accessor there, the answer depends on
the position. A structure with **no** table — every credit spread — and a ctx
with no `strategy` at all get `[stops]` unchanged with every rule on, so the
table is additive by construction and the pre-2026-09-11 callers are untouched.

Two keys exist only per structure, with no `[stops]` counterpart:

- **`loss_rules = false`** skips rules 1, 2 and 4 (the 2× credit money stop, the
  `cut_dte` time stop, the delta stop). Set for `SHORT_PUT` and `COVERED_CALL`,
  because for them each rule is **inverted** rather than merely unproven: a
  covered call losing 2× its credit is the stock rallying — the shares hold that
  gain, and Option Alpha's covered-call study found stops simply produce more
  losers — while a cash-secured put's delta and time stops fire exactly when
  assignment becomes likely, which is the wheel's plan and the reason
  `equity_lots` exists. Putting the money stop back re-breaks the wheel.
- **`manage_dte = 21`** closes a **profitable** position at or below that DTE
  (`MANAGE_DTE`; playbook X5 — gamma rises and the last few percent of premium is
  not worth it). ⚠ The profit condition IS the design: closing an underwater one
  there would be the `cut_dte` stop under another name. **Spreads deliberately
  have none** — X5 is written about premium selling generally, but applying it to
  PCS/CCS/IC would change how every position in the app exits, which is a
  separate change with its own measurement.

**`paper_positions.entry_short_delta` is what makes the delta stop mean what it
says.** `delta_drift` measures adverse movement *relative to entry*, and the
column did not exist until 2026-09-11 — so every paper position fell to
`delta_abs_fallback` (0.35), which is **too tight** for a short sold rich (a
0.30-delta spread opens 0.05 from its own stop) and **far too loose** for one
sold cheap (a 0.10-delta short has to more than triple before the stop notices,
where the drift rule acts at 0.22). It is recorded by both producers that
open a position — the captured-signal entry cycle and `apply_roll`, which takes the CANDIDATE's
`new_short_delta` because a roll is a new entry at a new strike. ⚠ **`None` means
"not recorded" and keeps the fallback — never write `0.0`**, which would make the
drift rule fire at 0.12 on a position that has not moved. And
`run_manage_cycle` puts it in the **base** ctx: it sat in the lifecycle branch
alone, which the book that trades never takes (the manual account's toggle
defaults off), so the column
alone would have changed nothing.

The profit target stays the global **0.50** for these two. TradingBlock's ~90% /
~95% pairs with rolling straight into the next cycle, which this app cannot do
for a single leg, so the higher target alone would just hold ~20 more days for
the last 40 points of a small credit. `tp_frac` is in the table if the evidence
changes — and `paper_engine`'s arm-break-even check reads the same
`signal_recommender.tp_frac_for`, so the two cannot disagree about where the
target is. Design:
[the B1 doc](../plans/2026-09-11-income-exit-rules-design.md).

**The mark.** `signal_repricer.reprice_swing` prices PCS/CCS/IC **and** the two
single-leg income structures — the put map for `SHORT_PUT`/`NAKED_PUT`, the call
map for `COVERED_CALL` (its strike lives in `short_strike`, the same field the
spreads use for their short leg), the side chosen by the taxonomy below. Each is
ONE short option, so the mark comes from `fill_model.realistic_single_fill` —
that leg's own bid/ask, worked `FILL_FRAC` from the natural side — never the
net-spread form with zero quotes for a leg that does not exist. ⚠ Until
2026-09-11 the repricer raised on anything but the three spreads, so
`run_manage_cycle` hit `per_contract is None`, skipped the position and logged an
ERROR every cycle: the Income board could open a position that nothing would ever
mark, manage or close, and the Rescue board could not even offer "Close now"
because that needs a mark.

⚠ **A covered call's mark is the OPTION LEG only.** Nothing here prices a bare
share — which is why `/options/shares` dashes Mark and Unrealized — so its
`unrealized_pnl` is not the position's economics: the shares' gain is invisible to
it. Read it as "what closing the call would cost", never as the trade's P&L. That
is also an independent reason the money stop cannot apply: it would be measuring
half the position.

**`shared/structures.py` is the ONE home for the taxonomy** — which side the risk
is on (`is_put_side`, `short_right`), what shape the position is (`is_single_leg`,
`is_short_put`, `is_covered_call`), how many legs it takes to close
(`option_legs`), and one canonical name per structure (`canonical`, which the rule
table is keyed on so `SHORT_PUT` and `NAKED_PUT` cannot be given different rules).
It is **vocabulary, not policy**; the rules are the TOML above. Both
`options-scanner` and `services/*` import it.

⚠ **It exists because seven copies of those sets had accumulated across tiers
that cannot import each other, and one was WRONG.** `paper_adjust.apply_roll`
tested `strategy in ("PCS", "IC")` to pick the option right, so a short put
resolved to `"CALL"` and its roll would have been priced off the call chain —
unreachable only because single-leg positions had no roll candidate to apply, the
same shape as the `_close_legs` commission defect beside it. The earlier round of
this fixed `rescue.is_put_side` alone, where a `SHORT_PUT` matching neither name
had the at-risk board scoring it with the CALL-side formula. **`test_structures.py`
now fails on a new copy anywhere in `options-scanner` or `services`** — an AST
walk over both the inline `x in (...)` shape and the assigned constant, since five
of the seven were constants with a comment above them asking the next editor to
keep the mirror in step. `webgui/pages/options/shares.py` is the one remaining
copy: Tier 1 takes no `services.*` import and widening its allow-list was out of
scope, so `test_cross_tier_mirrors.py` pins it against `shared.structures`.

**Rescue routes single-leg positions to `single_candidates`.** Every spread roll
builder early-returns for them — a roll needs a long leg to re-price — so a tested
cash-secured put's entire menu was "Close now", while the repairs the playbook
prescribes sat unused one function away in the builders written for the ad-hoc
naked shorts. Those now cover `COVERED_CALL` too (roll **up** and out; no
"define risk" row, since the shares already bound it) and carry a zero-economics
**wheel** row — `accept_assignment` / `let_called_away` — which is the one row a
quote gap can never remove, and the alternative B1's rule table names. ⚠ They are
**advisory**: `paper_adjust.apply_roll` partitions `est_fill_legs` into a closing
PAIR and a reopening PAIR and books a spread reopen, so executing a single-leg
roll is a change to the money path. `_annotate` is shared by both paths so a
delegated menu cannot lose the board's context line.

## The Income board is CAPTURED for calibration, and capturing is not trading

The 30–45 DTE window had produced **no outcome data at all** — nothing recorded
it — so the nightly calibration could never test the playbook's central claim
against this app's own trades. `handlers.record_income_signals` captures each
day's published board under **`scanner_type = "INCOME"`**, which
`shared.calibration.family_key` buckets on its own (an unrecognised family is
passed through rather than folded into 0DTE or SWING).

⚠ **`paper_engine.run_entry_cycle` refuses `_NO_AUTO_ENTRY_TYPES` outright, and
that refusal is what makes the capture safe.** That cycle reads *every* open
captured signal with no type filter, and most income candidates are ordinary
PCS/CCS spreads it would size without complaint — so recording the board would
otherwise have converted a hand-picked screen into an auto-traded feed. "Screen
or feed?" is an open product decision, and the Income page promises the current
answer: *nothing here is traded automatically*. The refusal is by TYPE, not by
shape; an absent `scanner_type` still opens, which is every pre-capture row.

**Two things made the one-line version of this a no-op or worse**, and both are
`compute.income_capture_row`'s reason to exist:

- **Units.** `signals.entry_credit` and `entry_max_loss` are **per share** —
  measured on prod, `entry_max_loss == width − entry_credit` exactly. An income
  board row is per-**contract** dollars (`net_credit` 340.0, `max_loss` 15661.3)
  and carries the per-share `credit` only on an adapted spread, never on a single
  leg. Handing the recorder `net_credit` would make every income outcome 100×
  wrong in the one dataset the feature exists to build, and nothing downstream
  would flag it: an R-multiple is unitless, so it would read as an implausibly
  good strategy rather than a bug. ⚠ `max_loss` keeps the board's
  **commission-inclusive** convention, ~1.7% larger than a scanner row's gross
  figure, which makes income R slightly conservative against 0DTE/SWING —
  accepted because income is measured in its own bucket, and because a gross
  re-derivation needs a branch per structure. A7 is the change that moves every
  column at once.
- **Shape.** An adapted spread carries the flat `short_strike` **and** the
  normalized `legs`; a `SHORT_PUT` carries only `legs`. Strikes come through
  `income_open_strike` and the delta off the short leg — a missing delta would be
  recorded as 0, and B6 made that load-bearing.

**`capture_min_income = 0` is deliberate** (`config/scanner.toml`). The composite
was tuned for the 0-DTE/swing core and this board scores well below it — measured
2026-09-11, all five candidates at **50.2–57.0**, every one graded *Marginal* — so
sharing `capture_min = 58` would record **nothing, every day**. 0 means "whatever
the board offered": `income_scan` already cuts below `swing_min` service-side, so
that is the real filter. `signal_recorder.capture_floor` resolves it per type, and
a new scanner type opts in **by name** rather than inheriting the loosest floor.

**`MANAGE_DTE` is in `_CAPTURED_CLOSE_CODES`**, or a tracked income signal's only
exit would be expiry while the app's own policy closes it at 21 DTE in profit —
the calibration would then measure a hold-to-expiry policy the manage cycle never
executes. `TARGET_HIT` stays out: on the lifecycle path +50% arms break-even and
holds, so that code cannot arise there.

## Selling premium has a volatility FLOOR, and it keys on vega, not on a name

**`shared/vol_gate.blocks(iv_rank, net_vega, floor, ceiling)`** is the one
predicate: a `floor` refuses SHORT premium below it, a `ceiling` refuses LONG
premium above it, and the bounds come from `config/scanner.toml` `[iv_rank]` /
`[iv_rank_ceiling]` through `shared.scanner_config`. Until 2026-09-12 the floor
lived in `run_full_scan` alone, over `signals_0dte` and `signals_swing`, so three
premium-selling surfaces had none: the Market Scanner's **Directional** tab
(naked shorts), the **Strategy Finder**, and the **Income Window** — whose
`trade_type="INCOME"` had no key at all, so even a keyed lookup answered 0.

⚠ **It keys on the candidate's own VEGA SIGN, never on its structure name**,
because every list that needed it is MIXED: Directional carries `SHORT_PUT` /
`SHORT_CALL` beside `LONG_CALL` / `LONG_PUT`, and the Strategy Finder carries
debit verticals beside credit spreads. Cheap volatility is exactly when the
long-premium half is the right trade, so a per-LIST filter would cut hardest
where it must not cut at all. The Market Scanner's two credit-spread lists keep
their existing whole-list filter — those are uniformly short premium, so the
per-signal form would be the same answer at more cost.

⚠ **Keying on vega sign is only as good as the sign, and the
Finder's credit rows carried the wrong one from the day it shipped (fixed on
branch 2026-09-16).** A raw scanner row's
`net_vega` / `net_theta` are **`short − long`, NOT position-signed** — a credit
spread carries POSITIVE vega and NEGATIVE theta — and `build_iron_condors` sums
them the same way (it writes no `net_vega` at all, deliberately: the Market
Scanner's composite reads a missing one as 0). `strategy_scanner._normalize_credit`
copied that convention into the Finder's normalized contract, where every native
family is position-signed, and an adapted IC read 0.0 from its vega-0 legs. So
**every PCS, CCS and iron condor on the Strategy Finder and the Income Window
passed the floor**, and `strategy_scoring.fit_vol` rewarded selling cheap
volatility. Measured on prod's nightly Redis dumps: the **2026-09-14 Income board,
two days after the gate shipped, was two IREN put credit spreads at IV rank 2.3**;
B2's own verification had exercised a single-leg short, and its tests handed
`swing_scan` invented rows (`{"type": "PCS", "net_vega": -0.31}`) no producer
emits. **`shared.structures.position_greek(row, greek)` is now the one converter**:
the explicit `entry_net_<greek>_position` wins (`screen_spreads` writes delta,
theta and vega, `build_iron_condors` delta and vega, and an adapted row stamps all
three), else `−net_<greek>`. **Delta needs the explicit field**: a raw row has no
`net_delta` to negate, and the adapter's reconstructed legs carry 0 on every long
leg, so a leg-derived `net_delta` reads `−short_delta` for a vertical (median 7×
the position's on prod's stored spreads) and exactly 0 for an iron condor — which
`fit_directional` scores beside leg-exact native families. `paper_trader` reads through it too, because its credit branch gets
raw rows AND Finder rows — negating a position-signed row again would book a credit
spread as long vega — and it keeps writing the ledger's `net_theta` in the scanner
convention the Paper page displays. ⚠ **Raw scanner rows keep the old convention**
(`signal_recorder` stores it, the detail panel shows it), so anything new that reads
a sign off a scanner row goes through `position_greek`, and any vega test builds its
rows with the real producers over a chain whose greeks fall away from the money —
a flat-vega chain makes every spread's net vega exactly 0 and hides this entirely
(`services/options_svc/tests/test_vol_gate_on_real_rows.py`).

⚠ **That whole-list filter is a DIFFERENT expression in a different place, and it
disagrees with `vol_gate` about absence on purpose.** It lives in
`run_full_scan` over `signals_0dte` / `signals_swing`, and an **unknown** IV rank
is REFUSED there, not skipped — the 2026-09-15 operator decision: nothing sells
premium against a volatility reading it does not have. Since then it is a NAMED
refusal, `no_iv_history` beside `below_iv_floor` in the scan funnel, because "we
have no history for this name" and "this name is cheap today" are different facts
and only one is about today's market. ⚠ It is written `not (rank >= min_rank)`,
never `rank < min_rank`: a NaN fails every comparison, so the naive spelling
would KEEP a row the old `(rank or 0) >= min_rank` dropped.

**Three absence rules, and each is the actual content of the function.** A
missing `iv_rank` (`iv_analysis` returns `None` when HV history is too short) or
a missing `net_vega` **skips** the gate — a bound cannot be enforced against an
unknown, and an outage must degrade to ungated rather than to refused (the
Market Scanner's own floor above is the deliberate exception). A vega of
**exactly zero is neither side**, so a vega-neutral structure is not handed the
ceiling by a rounding sign (note `-0.0 < 0` is False, so the two spellings of
zero would not even agree). And **`0` means OFF for both bounds, not "a bound at
zero"** — an IV rank of 0.0 is a real reading, so a floor of 0 must not be the
thing that refuses it; that is what lets the ceiling ship as all-zero.

**The levels, and why only one moved.** `INCOME = 30` matches SWING — the rule
the rest of the app already lives under, which is what makes it additive.
⚠ **The existing 35 / 30 are measurably too LOW and were deliberately left
alone.** Over 910 closed captured signals on prod, mean R by entry IV rank runs
**0–39 −0.149 · 40–44 −0.151 · 45–49 +0.015 · 55–69 +0.239 · 85–100 +0.266**,
with the win rate going 24% → 81%; a floor at **45** is the optimum on total R
(cutting 94 trades worth −14.1R while keeping +199.9R) and 55 gives the gain
back. The effect survives a control for `entry_score` — within every score
tercile the low band is far worse — so it is **not the composite in disguise**,
and the composite's own `iv` factor does not capture it. Raising all three is a
~10% cut to every signal the app emits, so it is the operator's call, in the same
class as `MAX_RISK_PER_TRADE`. ⚠ Two honest weaknesses before anyone acts on it:
**ORCL and UAL are 75 of the 139 trades below 50**, and excluding them the rest
of that band is +0.042 rather than negative; and the 5-point grid is not monotone
(50–54 measures +0.484 on n=26).

**The ceiling ships OFF, and that is a decision, not an oversight.** There is no
long-premium outcome data in this app — `signals.db` holds only PCS, CCS and IC —
so a level would be invention. When one is wanted, **65** is the natural value:
not a new number but `strategy_scoring.infer_market_view`'s own "high" boundary,
the mirror of the floor's 35 being its "low" one.

⚠ **The drop count is its OWN field (`vol_filtered`), never folded into
`filtered_out`.** The Strategy Finder renders that one as *"N below the quality
bar"*, and a volatility drop is a statement about the environment, not about the
candidate — folding them prints something untrue on exactly the scan where the
reader most needs the real reason. The Income board sums it across the pass and
says *"N too cheap to sell"*, because a short board and a quiet tape are
different facts that the row count cannot tell apart. ⚠ And
`scanner_config.min_iv_rank()` is **closed over its own `DEFAULTS`**, so a trade
type added to the TOML alone is silently dropped — both halves, always. Design:
[the B2 doc](../plans/2026-09-12-volatility-gate-design.md).

## A short-delta band governs the SHORT legs, aims at the midpoint, caps the top

`swing_scan` takes `put_d_min/put_d_max` + `call_d_min/call_d_max` and — since
2026-09-11 — hands them to **both** builders. It used to pass them to
`screen_spreads` alone, so inside ONE call the Income Window's documented
0.15–0.25 band governed its credit spreads while its cash-secured put was built
at `strategy_scanner._SHORT_DELTA` (0.28) and the band was simply ignored. ⚠ The
harm was not the target but the **realised** delta: measured across four live
chains on 2026-09-11 every short sat at or above the ceiling — XOM **0.328**, SPY
0.281, IREN 0.274, CRWV 0.274 — because `nearest_by_delta` returns the closest
strike on a coarse ladder however far out it lands.

Three rules, and each is a decision rather than an implementation detail:

- **The target is the band's MIDPOINT**, mirroring `compute._COVERED_TARGET_DELTA`
  so a band edit moves every consumer at once. On the real XOM $5 ladder (|delta|
  0.131 / 0.218 / 0.328) that is the whole fix: 0.28 picks 0.328, 0.20 picks 0.218.
- **Only the CEILING is enforced** — a short above `hi` is dropped, one below `lo`
  is kept. Deliberately asymmetric: "richer premium and more assignment than the
  window documents" is the harm, while escaping the band *downward* is a thin
  credit the delta-aware edge floor (`credit/width ≥ |Δ| + EDGE_MARGIN`) and the
  credit floor already refuse. A symmetric drop would be a second gate that can
  only empty the board for a reason something else covers.
- **A band never touches the LONG legs.** It says where you are willing to SELL
  premium and nothing about where to buy it; a 0.20-delta long call is a lottery
  ticket, not the 0.55 directional bet `build_directional` intends.

`_band_abs` normalises sign and order, because `INCOME_PUT_DELTA` is signed
`(-0.25, -0.15)` while the call band is positive and `nearest_by_delta` works on
`abs` — and an unusable band degrades to the legacy fixed target, never to `(0,0)`,
which would aim every short at the far wing. **Consequence worth knowing: the
Strategy Finder's own Δ inputs now bind on its single-leg shorts**, which they
never did — the page's help already claimed they did.

## The width search sizes against the REAL book, and the scan reads the calendar

**`scanner_engine.DEFAULT_MAX_RISK_DOLLARS` is `config_paper.MAX_RISK_PER_TRADE`
($750 since 2026-09-22; $250 when the figures below were measured).** `select_best_width` defaulted to a phantom `account_size=100000,
max_risk_pct=0.05` — a $5,000 per-trade budget — so the E[PnL] race that picks a
width was decided for a book **20× the real one**, and it routinely chose a width
whose single contract the entry cycle then refused. ⚠ Measured on the live book
2026-09-11: **169 of 773 paper orders (21.9%) rejected `RISK_TOO_HIGH`**, over 35
trading dates.

The mechanism, measured per symbol: the chosen width cost **$425** a contract on
MU (75 rejections, $894 underlying, $5 strikes), $645 on MSFT (7.5-wide) and
**$2,044** on ALAB (25-wide). `max_risk_dollars` makes `n_risk` real, and the
existing `contracts <= 0 → continue` guard then drops a width nothing can size —
so ~92% of those rejections stop being emitted at all rather than being emitted
and refused.

⚠ **The honest consequence: a per-trade cap excludes high-priced underlyings
whose narrowest width costs more than it.** At the old $250, MU's $5 width ($425)
was unaffordable and the scan emitted nothing for it; the operator raised the cap
to $750 on 2026-09-22, which readmits that case but not, say, a 25-wide ALAB
($2,044). That is the truth made visible (no signal) instead of invisible (a
rejection buried in the fills log).

**`compute.scan_earnings` is the one earnings lookup every scan uses.**
`swing_scan` has gated per signal since the 0-DTE-bucket fix and `income_scan`
supplied a date, but the Strategy Finder's handler passed **none** — so
`if earnings_date and ...` was always False and the gate was a no-op on that whole
surface while reading exactly like protection, the same shape as the defect A1
fixed on the Market Scanner. Three scan paths; the gate was live on one. The
handler now supplies the date — and since 2026-09-14 passes
**`earnings_mode="flag"`**, so the Finder **keeps and tags** a row open through a
report (`spans_earnings` + `earnings_date`, rendered *Earnings Nov 19*) while the
Market Scanner and the Income Window still **drop** it. Operator decision: a
whole-chain scan out to a year would otherwise end every single stock at its next
report. ⚠ In flag mode `screen_spreads` must receive NO date — it can only drop —
and the paper ledger has no earnings check, so a tagged trade sent to Paper (a
credit spread or condor as much as a debit trade) opens like any other. Every row
is also **stamped** with the coverage it got (`earnings_status`), because a row that
skipped the check must not look like one that passed it, and `not_listed`
deliberately does not block — with no vendor key it is every symbol, so failing
closed would empty the page. ⚠ `scan_earnings` is a thin WRAPPER, not
`scan_earnings = _income_earnings`: an alias binds the function object at import,
so the ~15 tests that monkeypatch `_income_earnings` by name would silently miss
this path.

## The NAKED reward gate is a RATE (per year), not a per-trade return

`strategy_scoring._reward_metric`'s NAKED branch returns
`(max_profit / capital) × (365 / max(dte, MIN_ANNUALISE_DTE))` — **annualised
capital efficiency**, so `GATE_BARS["NAKED"]`'s `capeff` 0.10 / 0.20 mean 10% and
20% **per year**. It was a per-trade return until 2026-09-05, which demanded the
same 10% of a 1-day trade as of a 45-day one and so cut **every** `SHORT_PUT` /
`SHORT_CALL` the scanner has ever emitted (a 35-DTE cash-secured put returns
~1.70%/trade ≈ 17.8%/yr). Two invariants:

* **`MIN_ANNUALISE_DTE = 5` floors the DIVISOR**, capping the short-end
  amplification annualising introduces (unfloored, a 1-DTE short is rescaled
  365×). It is the horizon lever; the bars are the capital lever, and the ~4.4×
  SHORT_CALL/SHORT_PUT gap is a **capital basis** difference (margin proxy vs
  stock-to-zero), not a horizon one — so raising a bar to discourage short-dated
  shorts cuts on the wrong axis.
* **`dte <= 0` returns `None`, and `MIN_ANNUALISE_DTE` must never reach that
  guard.** The floor is for a horizon that exists; the guard asks whether one
  exists at all. `strategy_scanner._dte_for` returns `max(0, …)` **and** folds an
  unparseable expiration into the same bucket, so a data fault and a same-day
  contract are indistinguishable there. **Accepted consequence: the NAKED reward
  gate is unreachable for 0-DTE** — every same-day naked short fails it, however
  rich the credit. Admitting them needs its own per-horizon bar, not a yearly
  rate over a horizon of zero.

Every figure in that block is re-runnable: `python tools/sweep_naked_capeff.py`
(`--rows` / `--floors`) is pure Black-Scholes through the same scorers, no Schwab
call and no DB. **Quote its numbers with their parameters** — they move with the
strike ladder, and this block has shipped stale ones twice.

## What a replayed command may re-do

**Replay is stopped in two places.** Consumer groups are created at id `0`, so a
fresh group re-delivers the whole backlog — the documented incident where a first
launch "burned a day's API budget in one go". (1) The CONSUMER (`_scaffold`)
drops any command older than `[age] replay_max_sec` (900) in
`config/services.toml` unless the service lists it as safe late (`make_app(
late_ok=)`; options: `refresh_paper`, `paper_reload`, `captured_reload`,
`calibration_refresh`). (2) The options DISPATCHER refuses a listed
side-effect command older than `side_effect_max_sec` (180).
`handlers._REPLAY_GUARDED` lists them — the seven that mutate the paper books
(`paper_reset`, `paper_close`, `paper_delete`, `paper_delete_closed`,
`captured_close`, `set_autoclose`, `set_manual_paper_lifecycle`) and `rescue_apply`
(MUTATES the paper book; its own is-it-open + 15%-drift guards pass a fast
replay), `gamma_analyze` (a PAID Claude call), `calc_rate` and `dossier` (Schwab
calls for a page that has moved on), `x_post` / `x_post_report` (a public post) —
and `handle_command` refuses a listed command older than
`STALE_OPEN_MAX_AGE_SEC` BEFORE its handler runs. ⚠ Until 2026-10-04 each branch
had to call `_is_stale_side_effect` itself, and `calc_rate` was listed,
documented as guarded and never checked. `paper_create` keeps its own check
(`_is_stale_open`), because it must answer the page with a `stale` outcome.

**`cmd:options` commands are a table.** One `_cmd_*` function per command,
registered by name in `handlers._COMMANDS`; `handle_command` is the lookup, and
its docstring is the API list, held equal to the table by two tests. Add a
command by registering a function and adding its docstring line — never a branch
in `handle_command`, which a test refuses. The long, self-contained commands
(`handlers.SLOW_LANE`) run on the queue's slow lane (`make_app(slow_commands=)`),
so a paper, reprice or rescue command never waits behind a scan; nothing that
changes a paper book may be put there, and a load whose follow-up reads its
result stays on the fast lane with that follow-up.

⚠ **That is an age gate, not idempotency** — two genuinely FRESH duplicates still
both run. It closes the replay case with machinery the service already trusts; a
dedup store keyed on the stream message id would be the stronger fix and is not
built.

## A STOCK leg is 100-share LOTS, and five sites read "no strike" as "no leg"

⚠ **A share leg is a normalized leg dict like any other** — same six keys, with
`option_type = "stock"` (`strategies.STOCK` / `options_calculator.STOCK_KIND`,
pinned together by `test_stock_leg_mirror.py` because neither tier may import the
other), `strike` and `expiry` `None`, `premium` the price paid **per share**, and
⚠ **`qty` counting 100-share LOTS, not shares.**

**That lot convention is why D4 was a small change.** Every consumer already
multiplies `value × qty × 100`, so one lot of a $100 stock comes out at $10,000
with no new branch — exactly as one $100 option contract would. `qty` in shares
plus a per-leg multiplier would have needed a branch at each of the ~six places
that multiply by 100, and each is a place a units bug hides. So the pricing core
needed only a per-share **value** function, `options_calculator.leg_value`: a
share is worth the underlying at any T and any IV, and `bs_price(price, None, …)`
raises — on a grid rebuilt every keystroke.

**The max-loss scan reaches ZERO when a leg set holds shares.**
`calc_summary_generic` scanned `0.5×spot … 1.5×spot`, which is right for an
option-only structure (no vertical, condor or fly can lose more than its width)
and wrong the moment shares are involved: it reported a covered call on a $100
stock as risking **$4,800** — the loss at $50 — against a real **$9,800**. It is
also what proves a protective put's loss is *bounded*, which is the entire reason
to own one. A set **net short puts** scans from zero for the same reason (a short
100 put read $4,800 against a true $9,800, and a far-OTM one read a max loss of
ZERO), and a set whose upside slope is negative — short more calls than it is
long, a share lot counting as one — returns the `UNLIMITED` sentinel, because the
grid's edge at 1.5× spot is where the scan stopped, not a risk figure (a short
straddle read $4,310). Every other option-only set keeps the old floor.

⚠ **Five places on the Calculator open-coded `l.get("strike") is None`, and one
SILENTLY DROPPED the leg** — so the page would have priced a covered call as a
naked short call with no warning. They go through
`leg_editor.legs_ready` now, which also rejects a **NaN** strike that `is None`
passed straight to `bs_price`. `max_loss_estimate` **declines** a leg set holding
shares rather than mapping the share leg to a *put*, which is what it did; Send
to Expected Move still drops it, and that one is right — a share leg has no
strike **line** to draw.

⚠ **A stale `expiry` on a share leg moves the PRICING HORIZON**, which is not
cosmetic: it joins `calc_summary_generic`'s front-expiry computation, and if it
were earlier than the real option leg's, the option would be priced with time
remaining at the wrong horizon — a payoff diagram that is not a payoff. Closed at
three writers (`build_default_legs` never sets one, `retype_leg` clears both
fields when a leg crosses the stock/option boundary, the leg editor's
`apply_expiry` skips share legs) **and** at the chokepoint both summary paths share:
`_leg_expiry_years` returns `None` for a share leg regardless, since a pasted or
hand-built leg set can still arrive carrying one.

**Only the CALCULATOR offers the three stock structures, and it takes TWO gates.**
Adding a template exposes it on every page that mounts the picker, and
`test_strategies.py` requires `STRATEGY_MENU` to cover every template *exactly*
(a template missing from the menu is unreachable) — so they cannot be hidden in
the data:

| surface | strategy menu | leg TYPE select |
|---|---|---|
| **Calculator** | offers them | `allow_stock=True` |
| **Simulator** | `exclude=STOCK_STRATEGIES` | option-only |
| **Rescue** ad-hoc | `exclude=STOCK_STRATEGIES` | option-only |

Both, because either alone leaves a hole: gating the menu still lets a user flip
a leg's TYPE by hand, and gating the TYPE select still lets them pick the
template. ⚠ And the exclusions are **safety**: the Simulator's Replay/IV-shock
engines price a `ContractRow` off the option chain and have no share concept,
while the Rescue ad-hoc form **books into the paper account**, which cannot hold
shares in `paper_positions` at all — they live in `equity_lots` — so a covered
call submitted there would be stored as a **bare short call**, the shape of
assessment defect 12.

⚠ **`COVERED_CALL` now names two different objects.** The Calculator's template
**and the Strategy Finder's row** (`strategy_scanner.build_stock_structures`,
since 2026-09-13) are the whole position (shares + call), and the template's tags
lead with **DEBIT**, because you pay for the shares; the paper account's and the
Income board's `COVERED_CALL` is the **option leg only** (`paper_positions` holds
no shares, and such a position's `unrealized_pnl` covers the option alone), which
is correctly a credit structure. ⚠ **So a Finder covered call — or protective put
or collar — must never gain a Paper button**: `paper_trader.create_paper_trade`
refuses by name any type outside the credit spreads ∪ `PAPER_DEBIT_TYPES`, so the
button would fail every click. `strategy_table._PAPER_TYPES` is a literal pinned
equal to `shared.structures.LEDGER_DEBIT` ∪ the credit spreads by
`test_cross_tier_mirrors`.

The **`100 SHARES`** chip on all three templates is what
distinguishes them on screen. All three route to the NUMERIC summary — none is in
`_ANALYTIC_CODES`, and a share leg pasted into an analytic strategy falls to
`CUSTOM` because the shape multiset cannot match, so the analytic formulas are
structurally unreachable from a share leg. Design:
[the D4 doc](../plans/2026-09-12-stock-legs-design.md).

**The Strategy Finder has its own payoff math with TWO valuation paths**
(`strategy_scanner.payoff_metrics`), deliberately not the Calculator's
`calc_summary_generic` (which ignores commission, prices every leg at one IV and
uses a different PoP model). The Finder's probability of profit
(`pop_from_payoff`) is LOGNORMAL, centred on the forward, over the time left to
the row's own expiry on the clock; inside two months it sits within a point of
the zero-drift normal it replaced, and it moves long-dated and same-day rows. A
single-expiry, options-only leg set is valued at
intrinsic on that expiry — ⚠ **that path must stay byte-identical**, because it is
every structure the Finder built before 2026-09-13 and their grades and cuts rest on
it; `test_single_expiry_options_never_take_the_front_valuation_path` guards it. A
set with a later-expiring leg or a share leg (calendars, diagonals, covered call,
protective put, collar) is valued at the FRONT expiry: back legs
Black-Scholes-Merton floored at intrinsic, shares at spot, commission per option
contract with shares free. Design:
[the Finder doc](../plans/2026-09-13-strategy-finder-all-structures-design.md).

⚠ **A later leg is priced at the chain's dividend yield and the volatility ITS
OWN MARK implies (`strategy_scanner.later_leg_vols`), not at the chain's
per-contract `volatility`.** The position is entered at the legs' marks, so the
model that values it later has to be worth those marks today. Measured on real
chains 2026-10-03 (weekend marks, seven names): the chain header's
`dividendYield` agrees with put-call parity within 0.4 points, while the contract
`volatility` ran **0.71–1.08** of the mark-implied figure and is one value for
call and put at a strike. A mark that cannot be inverted falls back to the chain
`volatility` at q = 0, and an unusable chain `volatility` still raises.
`bs_price` / `implied_vol` take an optional `q` for this ONE caller; everything
else prices at q = 0. ⚠ Any other code that reads the chain `volatility` as "the
IV that prices this contract" inherits that gap until it is checked in regular
hours (scorecard AC-19).

## Expiry settlement has ONE rule, and three books use it

**`paper_engine.settlement_underlying(client, symbol, expiration, today)`** is
the price an expired option position settles against, for the paper Account, the
Ledger and the captured signals alike:

* **expiry day**, at or after the 15:00 CT close (`should_settle` gates it): the
  regular-session last from a direct quote (`regular.regularMarketLastPrice`
  when Schwab sends it, else `lastPrice`);
* **any later day**: the EXPIRATION DATE's daily close — never a live quote;
* **no usable price**: `None`, and the caller DEFERS. It never falls back from
  the dated close to a live quote, nor (captured signals) to the entry price.

⚠ **Until 2026-10-03 nothing could settle a paper position on its own expiry
day.** The hourly cycle's last run is 14:00 CT and settlement needs 15:00, so a
position settled the next trading morning against THAT morning's quote: a put
credit spread worth +$98.70 at Friday's close booked −$401.30 on Monday. And
captured signals closed as `EXPIRED` at the first cycle of their expiry day (the
gate was `dte <= 0` with no clock), at that morning's option mark. Measured on
prod: **307 of 930 stored outcomes** are such rows. The fix is forward-only;
they can be re-settled from daily closes, and the score calibration, the
entry-volatility-rank study and the ladder replay were computed on them.

**`[slots.paper_settle]` (15:05 CT) runs a settle-ONLY pass**
(`handlers.run_paper_settle` → `paper_engine.run_settle_cycle` +
`expire_ledger_trades`). ⚠ It must never become a manage cycle: after the close
an option quote is a stale or one-sided market, and an exit rule acting on one
closes positions at prices nobody could trade. Keep the slot AFTER 15:00 —
earlier, nothing is due and the slot is spent for the day.

⚠ **`signal_repricer.expiry_value` is the CAPTURED-signal settlement value, and
it is not `intrinsic_value`.** `intrinsic_value` books a single-leg short at
ZERO whatever the close, which is right for the Account only — there an
in-the-money cash-secured put is ASSIGNED and its loss lives in the share lot. A
captured signal has no lot, so `expiry_value` returns the option's own intrinsic,
and `None` (never 0.0, which books the whole credit as a win) for anything it
cannot value.

**Two neighbouring rules from the same audit.** The Account sizes a position off
**`paper_sizing.risk_width(sig)`** — an iron condor's WIDER wing, read off its
four strikes — never off the row's `width`, which the scanner fills with the PUT
wing and Rescue's roll builders read that way. And **`paper_broker.simulate_fill_price`
prices a single-leg short off its own leg** (the branch `reprice_swing` takes to
mark it); before that every income position's rule-close was rejected each cycle.
⚠ The manage-cycle tests that use `_FakeBroker` cannot see a broker that cannot
fill — it fills whatever it is asked.

## A session-scoped scorer gets ONE session, and a timeframe weight is looked up by name

The Day gauge's intraday frames hold **ten sessions** (the EMA alignment needs
them), but its session structure and its volume profile describe one. They read
**`sentiment_svc.compute._session_frame(frames)`** — today's bars only, `None`
with fewer than `SESSION_MIN_BARS` or with no timestamps to tell sessions apart,
and it never falls back to the multi-day frame. Handed all ten, a −3% day after
nine up days scored **+1.00**.

`technical.calculate_ema_alignment` **raises on a timeframe name that is not in
`TIMEFRAME_WEIGHTS`**. It defaulted an unknown key to 1.0, which is how the
sentiment service's daily frame — passed as `"1day"` where the table says
`"daily"` — carried the 5-minute frame's weight instead of 3.0.

## A DEBIT position inverts every credit rule, and the ledger had none of its own

⚠ **A debit trade stores `entry_credit` as the NEGATIVE per-share debit**, so
`signal_recommender.recommend`'s `credit_total` is negative and its rules do not
merely fail to apply — they **invert**. Measured on the real function, a healthy
long call came back **CUT/MONEY_STOP at every P&L from −$199 to +$399** (rule 1
reads `pnl <= -2 × -200`, i.e. `pnl <= +400`) and TAKE_PROFIT above that. So
`recommend` **DISPATCHES** a debit to `_recommend_debit` before any credit rule
runs; nothing may reach them with a negative credit.

**The Paper Ledger already had a manage cycle — what it lacked was an exit
RULE.** `run_manage_and_refresh` has repriced it and settled its expiries
(`expire_ledger_trades`) on the manual account's cycle all along. Nothing closed
a position *before* expiry except the page's Close button, so a long option or
debit vertical sent from the Strategy Finder or the Market Scanner's
**Directional** tab (`strategy_table._PAPER_TYPES` lists all four) rode to expiry
whatever it did in between. `compute.manage_ledger_trades` is that rule pass,
**before** the settlement on the same tick — a position at its target *on* its
expiration day must book the target it reached, and `should_settle` fires from
15:00 CT while the target may have been hit hours earlier.

⚠ **That cycle is HOURLY (`paper_cycle_due`, 09:00–14:00 CT — six times a trading
day), not 5-minute**, and `expire_ledger_trades`' own docstring claimed 5-minute
for months. (Expiry SETTLEMENT also runs on its own 15:05 CT slot — see
"Expiry settlement has ONE rule" above.) Six checks a day is the honest resolution of these rules — a target
reached at 09:15 is acted on at 10:00 — and the pass rides that cadence rather
than adding a seventh scheduler slot, because the rules are day-scale (a +50%
target, a 21-DTE exit) and not intraday.

**Three rules, from `config/trade_mgmt.toml`'s `[structures.*]` — and the debit
structures read `exit_dte` / `debit_stop_frac`, never `loss_rules` / `stop_mult`
/ `cut_dte`, which are all credit-denominated** (*2× a debit* is a loss that
cannot happen):

- **The loss side ships OFF** (`debit_stop_frac` unset). Sourced: the
  practitioners close debit spreads before expiry rather than stopping them out,
  so a level would be invention. One line to opt in.
- ⚠ **The profit target's DENOMINATOR differs by structure, and the two readings
  are genuinely different numbers.** A bounded vertical takes a fraction of its
  **max profit** — the mirror of the credit side, where the credit *is* the max
  profit — and a long option a fraction of the **debit paid**, because it has no
  max profit at all (the ledger stores `unbounded = True` /
  `max_profit_total = None` for exactly that). On one $2.00 debit over a $5
  width those are **+$150 and +$100**. `_debit_target_base` owns the choice; an
  unusable max profit falls back to the debit rather than making the target
  unreachable, or at zero firing it at break-even. ⚠ `max_profit_total` is
  already × quantity while the repricer's P&L is per contract — `_ledger_exit_ctx`
  divides, or a 3-lot's target sits three times too far away.
- ⚠ **The time exit fires only when `dte_at_entry` was GREATER than
  `exit_dte`**, and that guard is what makes 21 shippable. The Directional tab
  builds from two windows — **DTE 0–4** and **DTE 5–15** — so every debit it can
  produce arrives inside 21 days, and an unguarded rule would close **100% of
  them on the tick after they opened**. A rule that fires at entry is worse than
  no rule. Those positions keep their target and the expiry settlement. Unlike
  the credit side's `manage_dte` it is **not** profit-conditional (`TAKE_PROFIT`
  ahead, `CUT` behind, code `TIME_EXIT` either way — distinct from `TIME_STOP`,
  which means DTE ≤ `cut_dte` **and** underwater).

**`close_paper_trade` booked a debit's P&L backwards, and that was live.** It
computed `(entry_credit − exit_debit) × qty × 100` with no direction branch, so a
long call bought at $2.00 and sold at $3.00 booked **−$500** where the truth is
**+$100**. `_expire_debit_trade` exists because the same formula is wrong at
expiry; the manual close is the ledger's only pre-expiry exit and never got the
same treatment. The control that holds it: closing at $8.00 by hand and expiring
at an $8.00 intrinsic are identical economics and must book the same number.
`exit_debit` therefore means the debit PAID on a credit row and the credit
RECEIVED on a debit row — and `paper.close_prompt_label` now names the side the
position is actually on, where the dialog used to ask a long call for its "Exit
debit".

**Scope is DEBIT rows only.** The ledger's credit spreads are equally ruleless,
but handing them the credit rules would change how a second book exits with its
own measurement attached — the same reason the credit spreads have no
`manage_dte`. ⚠ And there is **no debit outcome data in this app**: `signals.db`
holds only PCS/CCS/IC and the ledger is empty, so these levels are **sourced,
not fitted**, which is why they are config. Design:
[the D3 doc](../plans/2026-09-12-debit-exit-rules-design.md).

## What a Rescue roll, narrow and convert BOOK

**A roll or a narrow reserves the REMAINING position's own risk**: its width
less the credit it carries. The cash the action cost is realized at once, so a
max loss built from "entry credit plus the action's net cash" reserves the
realized loss a second time ($1,060 against $760 on a 10-wide spread sold for
2.40, closed at 5.40, reopened for 2.40). `apply_roll` books the risk of the row
it writes (`_standalone_max_loss`), never the candidate's figure on trust.

**A convert's credit joins `entry_credit`** and is realized at the close, like
the credit the position was opened for; only the commission is booked at the
convert. Realized at once with `entry_credit` left alone, every later mark (which
prices all four legs against `entry_credit`) read worse than the position was by
the whole convert credit, and the money stop sat that much nearer.

## A CCS keeps its strikes in `short_strike`, and two bugs turned on forgetting it

⚠ **Only an IC uses `call_short` / `call_long`.** A standalone **CCS** keeps its
strikes in `short_strike` / `long_strike`, read off the **call** map —
`reprice_swing`'s own branches are the authority, and both bugs below came from
assuming otherwise.

**1. `_apply_convert` destroyed a CCS's call legs (live, money path, fixed
2026-09-12).** Converting a position to an IC or an iron butterfly adds the
opposite side and relabels the row `IC`. The PCS branch was always right — its put
strikes stay put and the calls are added beside them. The **CCS** branch wrote the
new **put** strikes straight over `short_strike`/`long_strike` and never moved the
calls anywhere, so the row came out with two NULLs. The consequence is worse than
a wrong number: the IC pricing branch needs all four strikes, so the position
became **unmarkable** — no mark, no exit rule, no P&L, until it expired. One real
occurrence: manual position **403** (SPY), a `convert_butterfly` on 2026-06-29,
left as `puts 747.0/746.0, calls NULL/NULL`, status `EXPIRED`.

**2. `signal_repricer._LEG_LAYOUT["CCS"]` read the wrong fields** for the few
hours between shipping the C4 Greeks and finding this, so **every CCS position
returned all-`None` Greeks**. ⚠ It passed review because the test fixture was
written to match the wrong assumption — the documented "a unit test over an
invented fixture passes while the live column is entirely blank" trap, the same
shape as the `get_quotes` envelope bug. Live impact was nil (all 11 open positions
are PCS; CCS is 15 of 273 historical rows) and `positions_priced` would have
disclosed the gap.

**The guard is an AST cross-check**, not a third copy of the field names:
`test_position_greeks` reads the strike fields out of `reprice_swing`'s own PCS /
CCS / IC branches and asserts `_LEG_LAYOUT` matches. ⚠ Walk `parent.body`, not the
`If` node — the node's `orelse` is the whole `elif` chain, so a whole-node walk
lets PCS "read" the IC branch's fields and the comparison passes on anything.
Assessment: [the D2 doc](../plans/2026-09-12-iron-butterfly-assessment.md).

## A quote or a Greek that is not usable is ABSENT, and one function says so

**`shared/greeks.py`** (`usable`, `delta`, `gamma`) is the one test for a Greek
read off a chain: Schwab sends **`-999`** as a placeholder, which is finite and
so passed every NaN guard, pinned the delta stop and poisoned net gamma. The
repricer, the recommender, the gamma engine and the big-delta detector all read
through it. **A leg with no bid is still a market when its offer is at or under
`[marks] zero_bid_max_ask`** (`config/paper.toml`): that is the long leg of a
spread that has won, and without a mark the position gets no target and no
profit lock. A zero bid under a larger offer stays unmarked.

## Book Greeks are NET per position, and delta is a direction not a hedge ratio

**`signal_repricer.position_greeks(trade, chain)`** returns
`{net_delta, net_gamma, net_theta, net_vega}` per contract, signed by side, off
the chain `reprice_swing` **already fetched** — so it costs no API call, the same
argument that made C3's IV snapshot free. Four additive nullable columns on
`paper_positions` store it on the manage tick, and
`compute.book_greeks(positions)` sums `greek × quantity` over the open book, which
`cache:options:paper_account` publishes.

⚠ **Summing `current_short_delta` would NOT have been net delta.** A put credit
spread at a −0.20 short and a −0.08 long is **+0.12** net, so a book total built
from short legs overstates its direction by the whole long-leg offset. That column
stays what it is (the delta stop is about the short strike's moneyness); the
Greeks read both legs.

**Signs follow the POSITION, not the option** — a short leg contributes MINUS its
greek. So a credit spread must come out net **positive** delta, negative gamma,
**positive** theta (Schwab reports per-option theta as negative) and negative
vega. A sign error would render a premium-selling book as *long volatility*, which
is why the tests assert each sign rather than a magnitude. `_LEG_LAYOUT` is keyed
on `shared.structures.canonical`, so `NAKED_PUT` and `SHORT_PUT` cannot be given
different leg layouts.

⚠ **`None` means "not computed", never zero — at every level.** A zero delta is a
real and meaningful reading (a balanced iron condor), so an unquotable leg refuses
the whole POSITION rather than contributing flat, and `book_greeks` reports `None`
when no position had a reading. It also reports `positions_priced` /
`positions_total`, because a partial book is the normal case on the first cycle
after a restart and a total that silently omits three positions is worse than one
that says so. A leg missing ONE greek yields `None` for that greek only — partial
data is normal off-hours, and dropping the position would lose a usable direction.

⚠ **Theta is dollars-per-day and additive; a cross-symbol DELTA total is not a
hedge ratio.** Without a beta there is no sense in which a $970 MU delta and a
$145 PG delta add up, so the page names theta in dollars and delta as a
direction ("long 1.44 delta"), and never implies a share count to hedge with.
**The beta weighting the assessment calls "optional" is NOT shipped**: nothing in
this repo stores or computes a beta and Schwab does not serve one, so producing
one would be inventing the input to the only quantity the reader cares about. It
is derivable — `run_full_scan` already fetches a year of daily bars per symbol —
which is the same shape as C3, and it is recorded as a follow-up rather than
guessed at. Design:
[the C4 doc](../plans/2026-09-12-book-greeks-design.md).

## The manual book's scorecard, and the P&L-by-exit-reason axis

**`services/options_svc/book_perf.build_scorecard`** is pure over `(positions, snapshot)`
(it was `driver_perf` until the Claude Trades page it was written for was removed,
2026-09-22). **`webgui/pages/scorecard.py`** holds the two formatters the Paper
Account page's one-line track record uses, `money` and `percent` — the chips, tables
and best/worst line went with that page.

⚠ **When moving a formatter into a shared module, grep the destination for the name
you are importing.** `portfolio.py` already has its own **unsigned** `_money` (the
account-card formatter — "Equity $24,184.20"), so `money as _money` was shadowed
by it and the track record printed realized P&L **without a sign**. The page
imports the module, not the name.

**`compute.manual_account_perf()`** is the manual book's scorecard —
`build_scorecard` was already pure over `(positions, snapshot)`, so this is an
accessor, not a second implementation. It rides the existing
`cache:options:paper_account` view rather than a new one, for the same reason
`lots` does: one database must not get two publish cadences, or the cards and the
scorecard could disagree about the same account. ⚠ It reads the FULL history
itself — the view's `positions` is the OPEN set, and a scorecard over open rows
alone would report a win rate of zero forever.

⚠ **`build_scorecard` has `by_exit_reason`, and that axis is the point** — though
since 2026-09-22 no page draws it (the breakdown tables were on Claude Trades).
Measured while replaying the ladder: `MANUAL_CLOSE` accounts for **+$50,102** of
the captured book's reported P&L against **+$11,664** for every other reason
combined, with **130 of 388** of its rows booking exactly `entry_credit × 100` —
the full credit, as if the spread expired worthless — and five contradicted by
their own last mark. Split by symbol and strategy that is invisible; split by exit
reason it is the first row. It is **not** asserted to be a defect (a manual close
can legitimately differ from the last mark); the change makes the question visible
where the book is read.

**Two copy rules on the Paper Account page's one-line track record**, both the
same shape as the app's other empty states: it is **blank until something has
CLOSED** (a fresh book reading "0.0% win" says it *loses*, not that it has no
record), and an **undefined profit factor is omitted, not printed** — `None` means
"no losses yet", and an em-dash mid-sentence reads as a rendering fault.

**`manual_analytics()` IS consumed** — `handlers` publishes it to `cache:options:paper_analytics`, and the Paper Account page's equity curve and excursion (MAE/MFE) panel read it. This file said otherwise until 2026-09-19. And C5's **trade-plan
snapshot did not ship**: "the rules in force at entry" is now a much larger object
than when it was written (six of those rules moved today) and needs a granularity
decision of its own, while a free-text thesis implies a workflow change on a book
whose positions open automatically. Design:
[the C5 doc](../plans/2026-09-12-manual-scorecard-design.md).

## The profit-lock ladder is live, and it can only ever raise a stop

**`[trail].active`** names the ladder in force — `"ratchet"` as shipped
2026-09-12, read through `shared.trade_mgmt.active_trail_ladder()`. ⚠ An unknown
or malformed name falls back to the **inert** `default_ladder` (a single
break-even rung), never to the richer one: a typo in a risk config must not
switch on an untried policy.

⚠ **`_locked_profit_level` has taken `ctx["trail_ladder"]` and
`ctx["peak_pnl_frac"]` since it was written, and NOTHING ever passed them** — so
the ratchet was inert whatever the TOML said. Two call sites now supply both:
`paper_engine.run_manage_cycle`'s **lifecycle branch** and
`compute.run_captured_manage_cycle`.

**The peak must be THIS cycle's `mfe`, not `pos["mfe"]`.** The row is fetched
before the mark, so its stored value lags one cycle — and on the very cycle where
a trade peaks and then collapses, that lag is the difference between locking 50%
of the credit and locking nothing. On the captured side the peak is
`signal_db.peak_unrealized`, one indexed `MAX(unrealized_pnl)` over the marks the
cycle already writes. ⚠ **Both return `None`, never 0.0, when there is no peak**:
a 0 clears the first rung (whose lock is 0.0), and `_locked_profit_level` reads a
missing peak as "no lock" — which is exactly today's plain break-even behaviour,
and therefore what every position opened before this change keeps.

**It cannot increase loss exposure on any path.** Rule 3 computes
`stop_level = max(be_level, _locked_profit_level(...))`, so a lock only ever
raises a stop that is **already above break-even**. Its cost is exiting a
recovered winner early.

**Measured before switching it on** (replaying all 281 closed captured signals
against their own mark series, 10%-of-credit slippage on every ladder exit): close
outright at +50% **+$4,788** · break-even stop **+$8,173** · **ratchet
+$8,556**. Better on 43, worse on 18, identical on 220, and ahead in every month
and both scanner types. ⚠ It **reverses at a 50% haircut** (+$6,526 vs +$6,924) —
a higher floor triggers more often, so it pays slippage more often. The edge is
real and small.

⚠ **`manual_paper_lifecycle_enabled` is still OFF, deliberately.** Closing at +50%
against holding-and-ratcheting is a $3,768 gap on the same sample — far bigger
than the ladder's own contribution — but it is the claim that does *not* survive
slicing: **September reversed it** (+$74 vs −$125, better on 0 of 9) and **0-DTE
gets nothing** (−$456 vs −$369), since a same-week trade has no room to ratchet.
Flipping it changes how every position in the manual book exits, so it is an
operator decision with those numbers. Design:
[the C2 doc](../plans/2026-09-12-profit-lock-ladder-design.md).

## "Vol Rank" is a variance risk premium, and the TRUE IV rank starts here

⚠ **`iv_analysis.calc_iv_rank_percentile` is not an IV rank, and the module has
said so since 2026-04-19.** It places current ATM IV inside the 52-week
distribution of **realized** volatility (HV-30) — a *variance risk premium*
reading, "am I being paid more than recent movement justifies" — and exposes
honest `hv_rank` / `hv_percentile` aliases beside the legacy `iv_*` keys. The
screens said **"IV Rank"** for five more months; they say **"Vol Rank"** from
2026-09-12, in the Market Scanner table, the Strategy Finder table and the Trade
detail panel. The **field stays `iv_rank`** — renaming a payload key that three
tiers read would be a contract change for no gain.

This matters for reading the B2 measurement: the field that predicts outcomes
there (mean R −0.15 below 45, +0.25 above 55) is the **VRP proxy**, not an IV
rank. Nothing in the app claims a true IV rank would do better — that is the
question a year of data will answer.

**`shared/iv_history.py` is the store that makes it answerable.** It was
`services/trade_svc/deepdive/iv_history.py` and held **7 rows, all dated
three days** (08-04, 08-23, 08-25): `record_snapshot` was reached only from
`deepdive/engine.analyze_symbol`, so it filled only when somebody opened a Deep
Dive report. Built, tested, *called* — by a surface nobody runs daily. It moved to
`shared/` because `options-scanner/scanner_engine.py` cannot import `services.*`
and duplicating a store's write path is how two writers come to disagree about a
schema; **`shared/earnings.py` is the exact precedent** for a cross-tier path to a
store living under `services/trade_svc/data/`.

**`run_full_scan` now records one `cm30_iv` per symbol per scan, at ZERO Schwab
cost.** It rides on the **+20..+45 DTE** chain the scan already fetches for
`run_iv_analysis`, plus the year of daily bars it already fetches for technicals.
Measured 2026-09-12 across ten symbols, that window brackets 30 DTE every time
(expiries at 20 / 27 / 34 / 41) and gives the *identical* CM30 as a `today..+60`
fetch — while a wider fetch is worse, since `$SPX` on `today..+60` **timed out at
the proxy**. ⚠ The GEX collector's chain stops at **+7 days**, which is why the
snapshot does not ride on it.

⚠ **A CLAMPED reading is refused, and this is the load-bearing rule.**
`constant_maturity_iv` documents clamping to the nearest tenor when the target is
outside the ladder — right for a one-off report, **corrupting for a ranked
series**: a column mixing 7-day and 30-day readings is not rankable, the number
still looks like an IV, and `_rank_from_series` takes `min`/`max`, so **one**
contaminated sample pins the bottom of the range for a year. `cm30_from_chain`
therefore returns `(value, basis)` with `basis` in
`{exact, interpolated, clamped, None}`, and only the first two are stored. A
missing sample costs a day; a wrong one costs the range.

⚠ **`backfill_rv` accepts TWO price-history shapes now, and only one used to
work.** The Deep Dive holds a DataFrame; `scanner_engine.fetch_price_history`
returns the **raw Schwab payload** (`{"candles": [...]}`, epoch-**ms** stamps).
The DataFrame-only code raised `AttributeError` on `.empty`, which the caller's
guard swallowed — a backfill that read like a feature and wrote nothing.
`_as_candle_frame` normalises both, so realized vol stays one computation and
`rv_rank` works **immediately** (RV needs no waiting; only IV does).

⚠ **`DEFAULT_DB_PATH` was `Path('./iv_history.db')`** — a relative default that
would have written a stray database into the process's working directory. Both
callers passed `repo_paths.IV_HISTORY_DB` explicitly, so nothing leaked; it is now
that constant. **The snapshot does not feed selection** and must not until the
series matures: `iv_rank()` reports `samples` and `sufficient` against
`MIN_SAMPLES_FOR_RANK` (20), and acting on 7 samples would be exactly the
unmeasured change this audit keeps catching. Design:
[the C3 doc](../plans/2026-09-12-iv-history-capture-design.md).

## An open position's Rescue read knows about earnings and about a pin

Two **modifiers** on `rescue.assess_position_risk`, beside the GEX and regime ones
its docstring already calls *"never standalone triggers"* — and the weight is the
finding, not an implementation detail.

**Earnings (B7) is a modifier because the measurement did not reproduce the
claim.** `earnings_date` is INJECTED (the caller owns the SQLite read, as
`sector_of` is injected into the concentration caps) and adds the same +6 as the
regime tilt. ⚠ Over all 910 closed captured signals the split looked decisive —
*no report* +0.254 mean R at 75.6% win against *spanned a report* −0.032 at
**16.2%** — but the earnings calendar's rows only begin **2026-08-24**, so
everything earlier was filed as "no report" and the comparison was really
June–July against August–September. Restricted to the 132 signals whose whole life
sits inside coverage, it **inverts**: spanned **−0.059 at 14.9%** against
**−0.241 at 12.3%**. So the factor may reorder a ranked list and must never
escalate `state`. **No push ships** — a phone alert is a standing stream with a
certain cost and an absent measured case. And the *entry* half is already covered:
all 67 spanning positions would be refused today by A1/A5's gate.

**The expiry-day pin (B5) is a flag, not a close.** `_pinned_at_expiry` needs
expiration **day** (not merely near it — ordinary proximity is rule 1's job and
already escalates), the `proximity_tested_pct` threshold reused rather than a
second number, and a **physically settled** name. ⚠ B5's *"close it by a set CT
time"* half is deliberately **not built**: `run_manage_cycle` settles at intrinsic
against the **15:00 CT** close and models no after-hours leg, so the 17:30 ET
exercise notice it defends against **cannot occur in this simulation**. Nor can it
be measured — `signal_outcomes.settlement_underlying` was NULL in every row until
2026-10-03 (an expiry now records the price it settled against) and
both books hold **zero `equity_lots`**, so no assignment has ever occurred here.

⚠ **`strategic_context`'s `assignment_risk` was unconditionally `True` for every
equity short** (assessment defect 13) — a flag always on carries no information,
while the futures branch beside it had gated on moneyness all along. It is now ITM
or nothing, with **no spot meaning keep the flag** (unknown moneyness must not
CLEAR a risk). And the note **stopped claiming an ex-dividend check**: there is no
ex-dividend *date* anywhere in this repo, only `dividendYield`, so it described a
test that does not exist. Design:
[the B5/B7/B8 doc](../plans/2026-09-12-position-awareness-design.md).

## The scheduled briefings bill the SUBSCRIPTION, and the API counter counts only the key

**`services/options_svc/claude_cli.py`** runs the four `[slots.analyze]` briefings
through `claude -p` with `CLAUDE_CODE_OAUTH_TOKEN` rather than the API key, falling back
per call to the key. It implements `client.messages.create`, so a briefing's prompts,
parsing and rendering are identical on either path. Everything else that calls Claude
(the Analyze button) is still on the key. Design:
[the doc](../plans/2026-09-14-briefings-on-claude-subscription-design.md).

⚠ **Claude Code prefers `ANTHROPIC_API_KEY` whenever it is set**, so a CLI call from a
process that carries the key bills the key while every log line says "subscription".
The child env strips it AND the client refuses any run whose init event reports an
`apiKeySource` other than `"none"` — keep both if you touch this or add another CLI
caller. ⚠ **`_count_anthropic_call(client)` skips a client with
`bills_api_per_call = False`**, and `FallbackClient` counts its own fallback, so
Settings → API usage is a count of BILLED calls; a new call site must pass its client
or it will count subscription calls as spend. ⚠ The options_svc conftest makes
`claude_cli._default_run` raise — the subscription is real money too, and the CLI and
token ARE installed on the prod box.
