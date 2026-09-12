# What an OPEN position should know about itself (B5, B7, B8)

*Design, 2026-09-12. Gap assessment items **B5**, **B7**, **B8**.*

Three items that look unrelated and turn out to share one shape: the app gates
hard at ENTRY and then stops asking questions. Each is measured below, and **one
of the three rationales did not survive the measurement** — which is now the
fourth time that has happened in this audit, so the result is reported rather
than papered over.

---

## B7 — earnings awareness on open positions

> **B7.** Raise Rescue heat and send a push when a position's expiration spans a
> report. Nothing in `rescue.py`, `signal_recommender.py` or
> `services/driver_svc` reads the calendar today. Option Alpha's 10-year study of
> 1,546 reports found misses averaged 34–38% beyond the expected move.

### The first measurement looked decisive, and was a time confound

Over all 910 closed captured signals, joined to
`services/trade_svc/data/earnings_calendar.db` (2,979 symbols, 3,332 dates):

| cell | n | mean R | win |
|---|---|---|---|
| no report in the position's life | 579 | +0.254 | **75.6%** |
| no calendar coverage | 263 | +0.155 | 65.8% |
| **spanned a report** | 68 | −0.032 | **16.2%** |

A 16% win rate against 76% is the largest split anywhere in this audit. It is
also **not real**: the calendar's rows only start at **2026-08-24** (its own
`recorded_at`), so a position that closed in June or July cannot have a report
inside its window by construction and lands in *"no report"*. The comparison is
therefore mostly "late August and September" against "June and July".

### Restricted to the window the calendar can see, the effect INVERTS

Taking only the 132 closed signals whose entire life sits inside the coverage
window on a symbol the calendar carries:

| cell | n | mean R | median R | win |
|---|---|---|---|---|
| **spanned a report** | 67 | **−0.059** | −0.027 | 14.9% |
| no report | 65 | **−0.241** | −0.114 | 12.3% |

The positions that spanned a report did **better**, on both mean and median. Both
cells are poor because that month was poor for the whole book. So on this app's
own data there is **no measurable earnings penalty** — the entire apparent effect
was calendar coverage standing in for the calendar month.

And a second finding makes the *entry* half of B7 moot anyway: all 67 of those
spanning positions would be **refused at entry today** by the gate A1 and A5
shipped (`earnings_gate_applies` says "applies" for 42 `0-DTE` and 25 `SWING`).
Every one was opened *before* its report, which is the case the entry gate now
covers. The population B7 actually targets — a position **already open** when a
report appears in the calendar — is not represented in the sample at all, and
there are **0 such positions open right now** in either book.

### So: the flag ships, the push does not, and no rule changes

- **A heat MODIFIER and a note**, in `assess_position_risk` and
  `strategic_context`, beside the existing GEX and regime modifiers — which that
  function's own docstring calls *"heat modifiers, never standalone triggers"*.
  That is precisely the right weight for a factor the sourced literature supports
  and this book's data does not reproduce: it can raise a position up a ranked
  list, and it can never on its own call a position tested.
- **No push.** A phone alert is a new standing alert stream, and the measured
  case for it is absent while the cost (noise on a device already receiving flow
  alerts) is certain. It is one `shared/notify` call away if the evidence changes.
- **No exit rule, no refusal, nothing automatic.** The entry gate already covers
  entry.

⚠ Worth keeping in view: Option Alpha's figure is about the **size** of earnings
moves, which is a real phenomenon, and 67 trades over one month is a small sample
in a bad month. "Not reproduced here" is not "not real" — it is a reason to make
this advisory rather than a reason to ignore it.

---

## B5 — the expiry-day rule, and the flag that already existed and said nothing

> **B5.** On expiration day, close (or at least flag) any short within about 1% of
> its strike by a set CT time; cash-settled index options are exempt. OIC notes
> exercise notices are accepted until about 5:30 pm ET, so an after-hours move can
> assign a short that closed out of the money.

### Nothing can be measured, and that is itself a finding

Three separate reasons:

1. **`signal_outcomes.settlement_underlying` is NULL in all 910 rows** — the
   column exists, is in the schema, and is written by nothing.
2. **`signal_marks.current_underlying` was `0.0` in all 58,895 rows.** Not
   missing — *zero*. See the defect below; it is fixed in this change, so the data
   starts accruing now.
3. **Both paper books hold zero `equity_lots`**, so no assignment has ever
   actually occurred in either.

### And the rule's "close it" half would defend a risk the book does not model

`paper_engine.run_manage_cycle` settles an expiring position **at intrinsic
against the underlying at or after the 15:00 CT close**. There is no after-hours
leg: a short that finishes out of the money at 15:00 CT and moves through its
strike afterwards is settled at the 15:00 print regardless. So the exposure B5
describes — an exercise notice at 17:30 ET — **cannot occur in this simulation**,
and a rule that closed positions to avoid it would be protecting against
something the paper book cannot express. That is worth building only when real
money is involved, which this app explicitly is not.

### What DOES ship: making the existing assignment flag mean something

`strategic_context` already returns an `assignment_risk` boolean, and for every
equity or ETF short it is set **unconditionally `True`** with the note *"early
assignment possible near ex-dividend or when deep ITM"* — which is defect 13 in
the assessment's own list, and a flag that is always on carries no information.
Futures shorts in the same function already gate it on moneyness. So:

- **Equity/ETF `assignment_risk` becomes moneyness-aware**, mirroring the futures
  branch that was already right: true when the short is ITM.
- **Expiry day amplifies it**, which is B5's contribution: a short pinned within
  the proximity threshold on its expiration date gets a heat modifier and a note
  naming the pin. The *escalation* is already handled — rule 1 (proximity) and
  rule 4 (`dte_urgent`) between them already read `critical` there — so this adds
  information, not a second path to the same state.
- **Index options stay exempt**, which that function already gets right (European,
  cash-settled), and the note says so.
- ⚠ **The ex-dividend half is NOT built, and the note stops claiming it.** There
  is no ex-dividend *date* anywhere in this repo — the chain carries
  `dividendYield` and nothing else — so a note mentioning ex-dividend was
  describing a check that does not exist. Naming a risk the code has not tested is
  the failure mode this audit keeps finding; the note now says what was actually
  measured.

---

## B8 — size as a percent of current equity

> **B8.** Size as a percent of current equity, so the cap shrinks in a drawdown
> instead of staying $250 / $3,000.

### The manual book is fine. The DRIVER's caps now say the opposite of what their comments claim

Measured on the live books, 2026-09-12:

| book | equity | cap | comment says | actually is |
|---|---|---|---|---|
| manual | **$24,184** | `MAX_RISK_PER_TRADE` $250 | ~1% | **1.03%** |
| manual | $24,184 | `MAX_SESSION_DRAWDOWN` $2,500 | 10% | **10.3%** |
| **driver** | **$13,347** | `per_trade_max_risk` $3,000 | *"~12% of the book"* | **22.5%** |
| **driver** | $13,347 | `daily_risk_budget` $12,000 | *"~half the book"* | **89.9%** |

The manual book's fixed dollars have barely drifted, and B3 already made its
book-level ceiling a percentage of `session_start_equity`. **The driver is the
whole of B8**: it is down 46.6% from $25,000, so a cap written as "12% of the
book" is now nearly a quarter of it, and "half the book" is nine tenths. Nothing
was mis-set — the numbers simply stopped meaning what they were chosen to mean,
which is exactly the failure B8 names.

### What ships

`config/driver.toml` gains `per_trade_max_risk_pct` and `daily_risk_budget_pct`,
enforced as **`min(dollar cap, pct × equity)`** so the change can only ever
**tighten** relative to today, never loosen — the safe direction, and the one that
needs no decision about appetite. The percentages are **the comments' own stated
intent** (12% and 48%), so this restores what the config already claimed rather
than imposing a new policy. On today's equity the effective caps become **$1,602
per trade** and **$6,406 of open risk**.

`MAX_SESSION_DRAWDOWN_PCT` does the same for the manual book's halt: inert today
(10.3% vs 10%) and load-bearing the moment that book draws down.

⚠ **`MAX_RISK_PER_TRADE` is deliberately left as a flat dollar figure.** Making it
float would desync it from `scanner_engine.DEFAULT_MAX_RISK_DOLLARS`, which A6
wired *to that exact constant* so the width search sizes against the same cap the
entry cycle enforces — and the scan does not have the paper book in hand. Floating
one and not the other would re-open the 21.9%-of-orders `RISK_TOO_HIGH` problem A6
just closed, from the other end. Handing the scan the live book is a wiring change
with its own design; the flat $250 is 1.03% today and is not the thing that hurt.

⚠ And the driver's sizing **appetite** is still the operator's open question
(assessment decision 3). This change does not answer it. It only makes the config
honest: whatever percentage is chosen, it now stays that percentage.

---

## The defect found while measuring all three

**`signal_repricer` read the spot from a key Schwab does not send**, in two
places:

```python
underlying = (chain.get("underlying") or {}).get("last", 0)
```

`underlying` is populated only with `includeUnderlyingQuote=true`, which this app
never requests — measured live, a SPY chain returned `underlying: None` and
`underlyingPrice: 764.29`. So the expression hit its **default on every call**,
and the default was `0` rather than `None`. Prod's
`signal_marks.current_underlying` is `0.0` across all **58,895** rows while every
sibling column on the same row is populated.

**It disabled a rule, not just a display field.**
`signal_recommender._recoverable` early-returns on `spot <= 0`, so the
`RECOVERY_MIN_CUSHION` deferral — the rule that holds a stop while the short still
has room — has been **permanently off** on the captured-signal path. It degraded
to "the stop fires", the conservative direction, which is exactly why nothing
looked wrong. Two UI paths read `rep.get("current_underlying") or
row.get("entry_underlying")` and so rendered the **entry** price under a *current*
label, and the expiry settlement paid an extra quote call falling through the same
`or`.

⚠ **`atm_iv`, in the same module, had already found and fixed this on 2026-08-25**
— and its comment records the part worth keeping: the nested `underlying.last` is
the **live** quote and is preferred, while `underlyingPrice` is **pinned to the
prior close outside RTH**. That fix was applied to one of three call sites. All
three now share `_chain_spot`, which carries that precedence and returns `None`
for any non-positive reading, and an **AST guard** fails if any function other
than `_chain_spot` reads a spot off a chain again.
