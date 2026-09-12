# A sector cap for the paper book (B4)

*Design, 2026-09-12. Gap assessment item **B4**.*

## What the assessment asked for

> **B4. A sector cap.** Limit both the count and the max loss per sector, with the
> sector map taken from `config/symbols.toml` or the sentiment service's sector
> reference. The scan universe's tilt to semiconductors is exactly the correlated
> book the playbook warns about.

## Three things measurement settled first

### 1. Both proposed data sources fail — one is not a map at all, the other has a hole exactly where the cap is needed

`config/symbols.toml`'s `sectors` key is the list of eleven **SPDR sector ETFs**
to collect. There is no symbol→sector map in it.

The sentiment service's reference (`sentiment-dashboard/sectors_ref.py`, over a
tracked 30 KB workbook) does carry one — 370 rows, 311 distinct symbols, five
constituents per (sector, industry). Measured against the real universe:

- it covers **48 of the 80 watchlist symbols**;
- the 32 it misses include **MU, AMAT, MRVL, INTC, TXN, ALAB, SMCI, DELL** — i.e.
  **the semiconductors this recommendation's own rationale names**;
- and **181 of 273 historical paper positions (66%) are on symbols it has never
  heard of**, led by **SPCX at 121 positions** — the single most-traded name in
  the book.

So the cap as specified would have policed a third of the trades while reading as
protection for all of them: the failure mode this audit keeps finding. It also is
not single-valued — the workbook lists **20 symbols under two different sectors**
(NVDA as Communication Services *and* Information Technology; TSLA as Utilities
*and* Consumer Discretionary), and TOML would silently keep whichever came last.

**So the map is the deliverable, not a detail of it.** `config/sectors.toml`
carries 343 symbols: the workbook's 311 seeded in, an explicit tie-break for the
20 double-listed names, and the 32 missing ones classified from the proxy's own
`/instruments` descriptions (`SPCX` → *"SPACE EX TECH SPACEX A"*, an aerospace
equity → Industrials; `SKHY` → *"SK HYNIX INC ADR"* → Information Technology).
Coverage of the current watchlist is **80/80**.

⚠ **It is hand-maintained, and that is the honest cost.** Nothing in this repo
derives a sector, and Schwab's instrument lookup returns a description rather than
a classification. The mitigation is not a promise to remember: `shared.sectors`
gives an unmapped symbol **a bucket of its own** (`"?<SYMBOL>"`), so a new
watchlist name is capped exactly as it was before — by the tighter per-symbol
rungs — and the engine's journal line prints the bucket, so a `?` in it is the map
saying out loud that it does not know that name.

### 2. The premise holds — sector really is a correlation proxy here

Two of this audit's rationales have already failed on measurement (A7's was false,
A4's understated), so this one was tested rather than assumed. Over six months of
daily returns across the 73 tradeable watchlist names:

| | pairs | mean correlation |
|---|---|---|
| **within** a sector | 552 | **0.250** |
| **across** sectors | 2,076 | **0.017** |

14 of the 15 most-correlated pairs in the universe share a sector, and the top
decile of correlated pairs is **63% same-sector against a 21% base rate** — a 3×
enrichment. Per sector:

| sector | names | internal correlation |
|---|---|---|
| Energy | 3 | **0.846** |
| Consumer Staples | 3 | 0.564 |
| Health Care | 7 | 0.454 |
| Communication Services | 6 | 0.282 |
| Financials | 6 | 0.267 |
| **Information Technology** | **30** | **0.240** |
| Consumer Discretionary | 10 | 0.190 |
| Industrials | 6 | 0.189 |

⚠ **The grouping is weakest exactly where the book concentrates.** Information
Technology is 30 of the 74 tradeable names and its internal correlation is only
average, because it holds IBM and TXN beside IONQ/RGTI (**0.939**) and CRWV/NBIS
(**0.838**). A tighter *theme* grouping would control those sub-clusters better;
it is not shipped because it would be invented rather than sourced. The
consequence is that the cap under-controls the AI-datacenter cluster, which argues
for the tight end of the range rather than for a different taxonomy.

### 3. Indices are a bucket, not an exemption — also measured

The obvious reading is that `$SPX`/`SPY`/`QQQ`/`$NDX`/`DIA`/`IWM` are the
*diversified* case and belong outside a correlation cap. Measured over the same
window, their mean pairwise correlation is **0.799** — the second-tightest group
in the whole universe, behind only Energy. Nine simultaneous index positions is
one large market bet, and the **driver book held exactly nine, for $15,018**. So
they share one `INDEX` bucket and are capped like anything else.

## What the cap would have refused

| book | worst simultaneous sector exposure | days a $1,500 cap binds |
|---|---|---|
| **manual** | Industrials **$20,312 / 107 positions** (SPCX); Information Technology **$3,569 / 19** | 20 of 46 |
| **driver** | Information Technology **$21,531 / 15 positions** — 86% of a $25,000 account in one sector; INDEX $15,018 / 9 | 35 of 40 |

The Industrials figure is single-symbol stacking, which
`MAX_POSITIONS_PER_SYMBOL = 3` now prevents on its own. The Information
Technology figures are genuinely multi-symbol and are what this rung is for.

## What ships

**`MAX_POSITIONS_PER_SECTOR = 5` and `MAX_RISK_PER_SECTOR = 1_500.0`** — the sixth
rung, enforced in `paper_concentration.concentration_reject`.

$1,500 is **two symbols at the full `MAX_RISK_PER_SYMBOL`** and ~31% of the
$4,837 deployment ceiling on the live book, so filling the book takes at least
four sectors. It sits strictly between the rungs either side, which is the test of
whether a rung exists at all: looser than the book cap and it never binds, tighter
than the symbol cap and the symbol cap is dead. 5 positions matches
`MAX_POSITIONS_PER_EXPIRY` for the reason that number was chosen — five positions
on one thing is a bet on that thing.

**Reported after the symbol rungs and before expiry.** When a symbol cap and the
sector cap both bind, *"you already hold three MU"* is the actionable sentence —
the operator can pick another name — while *"tech is full"* is the answer only
once the symbol has room. A shared expiry is the weaker coincidence, so it stays
last. The deployment cap still outranks everything: if the book is full, which
symbol was asked for is irrelevant.

**`sector_of` is injected**, defaulting to `shared.sectors.group_key`, so the
decision stays pure and testable over a stated map while production needs no
wiring at the call site and cannot forget it. A lookup that **raises** degrades to
"no grouping" rather than to a refusal — the map is a config read, and refusing
every trade because a TOML went missing would be a worse failure than not applying
one of six rungs.

## What does NOT ship, and why it is worth reading

**The driver's book is not covered, and its numbers are the worse ones.** The
driver opens through `compute.open_driver_position`, which re-checks structure,
defined risk, `max_concurrent` and `daily_risk_budget` — not
`concentration_reject`, which lives in a module `driver_svc` policy does not
reach. Its envelope is `config/driver.toml`. Extending the cap there is a change
to a second service's risk policy with its own measurement, and it lands on a
driver that is **already armed and down 46.6%** with a directional gate awaiting a
decision. It is recorded as a finding rather than bundled in here.
