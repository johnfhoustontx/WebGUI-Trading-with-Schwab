# Start the IV history accruing (C3)

*Design, 2026-09-12. Gap assessment item **C3**.*

## What the assessment asked for

> **C3. Store one ATM IV per symbol per day.** The collector already fetches the
> chains, so this is small. A true IV rank then exists in a year; until then,
> label the field "Vol rank".

## What measurement found: the store, the writer AND the reader already exist

`services/trade_svc/deepdive/iv_history.py` is a complete, well-built module —
`init_db`, `record_snapshot`, `constant_maturity_iv` (interpolating in
total-variance space, which is the correct linear domain), `backfill_rv`,
`iv_rank`, `rv_rank`, `snapshot_count`, and a `MIN_SAMPLES_FOR_RANK` guard. The
SQLite store has an idempotent upsert on `(symbol, snapshot_date)`.

It holds **7 rows across just three days** — three on 2026-08-04, two on 08-23,
two on 08-25. (⚠ Corrected after the promote: an earlier note in this repo said
"all dated 2026-08-04", which came from sampling the first two rows. The scatter
makes the point more clearly, not less — seven readings on three unrelated days
across three weeks is exactly what "it fills only when someone opens a Deep Dive"
looks like.)

The reason is that `record_snapshot` is called from exactly one place —
`deepdive/engine.analyze_symbol` — so the store fills **only when someone opens a
Deep Dive report**. Seven were run, on three scattered days in August. Nothing
schedules it, and nothing on the scanner path has ever touched it. This is the
"built, tested, never called" class (assessment defect 8), one layer worse: it is
built, tested, *called*, and called by a surface nobody uses daily.

**So C3 is not "write the thing". It is "run the thing that exists."**

## Where the chain comes from: nowhere new

Three candidate hosts, and only one works:

| host | chain window | verdict |
|---|---|---|
| `gex_collector.poll_once` | today → **+7 days** | ✗ no expiry near 30 DTE — `constant_maturity_iv` would **clamp to the ~7-day tenor and store it as a 30-day reading** |
| a new scheduled slot | its own fetch | ✗ ~80 extra calls/day for nothing the scan does not already have |
| **`scanner_engine.run_full_scan`** | **today +20 → +45** | ✓ already fetched per symbol, already handed to `run_iv_analysis` |

Measured against live chains, 2026-09-12 — the `+20..+45` window brackets 30 DTE
for **every** symbol tried, and produces the **identical** CM30 to a much wider
`today..+60` fetch:

| symbol | expiries in +20..+45 | CM30 | how |
|---|---|---|---|
| SPY | 20, 27, 34, 41 | 12.69 | interpolated |
| $SPX | 20, 23, 24, 25, 26, 27, **30**, 31… | 12.41 | exact |
| AAPL | 20, 27, 34, 41 | 24.19 | interpolated |
| MU | 20, 27, 34, 41 | 59.34 | interpolated |
| XOM / IREN / SPCX / PG / IBKR | 20, 27, 34, 41 | 29.29 / 79.94 / 49.83 / 18.05 / 37.10 | interpolated |
| CMG | 20, 27, 41 | 33.69 | interpolated |

Not one clamped. ⚠ And the wide window is actively worse: `$SPX` on
`today..+60` **timed out at the proxy** (30 s read timeout) while the narrow
window returned in time — a wider fetch is both unnecessary and less reliable.

**So the snapshot costs zero Schwab calls.** It is computed from a chain object
`run_full_scan` already holds.

## ⚠ The clamp is a silent quality trap, and it is refused rather than stored

`constant_maturity_iv` documents *"Outside the available range: clamp to the
nearest tenor"* — sensible for a one-off report, and **wrong for a ranked
series**. A file mixing 7-day and 30-day readings under one column is not
rankable, and the corruption is invisible: the number looks like an IV.

So `cm30_from_chain` returns **`(value, basis)`** with `basis` in
`{"exact", "interpolated", "clamped", None}`, and the scanner path **records only
`exact` or `interpolated`**. A clamped reading is dropped, because a gap in the
series costs one sample while a wrong sample corrupts the range the rank is
measured against — `_rank_from_series` takes `min` and `max` of the series, so a
single contaminated low pins the bottom of the range for a year.

## What the recorded field actually is, and what this does NOT claim

⚠ **The existing `iv_rank` is not an IV rank, and the module has said so since
2026-04-19.** `iv_analysis.calc_iv_rank_percentile` places *current ATM IV within
the 52-week distribution of realized volatility (HV-30)*. That is a **variance
risk premium** reading — "am I being paid more than recent movement justifies" —
and the module already exposes the honest `hv_rank` / `hv_percentile` aliases
beside the legacy `iv_*` keys.

This matters for how the B2 measurement is read: the field that predicted
outcomes so strongly there (mean R −0.15 below 45, +0.25 above 55) is the **VRP
proxy**, not a pure IV rank. So this change deliberately makes **no claim that a
true IV rank is better** — that is exactly the question a year of data will
answer, and answering it is the C tier's stated purpose. What ships is the
recording, not a new input to selection.

Two things make the recording more useful than the item implies: the snapshot row
already carries `front_iv` / `front_dte` / `vrp` beside `cm30_iv`, and
`backfill_rv` derives the realized-vol series from the price history
`run_full_scan` **also already fetches** — so `rv_rank` works *immediately*, and
VRP becomes derivable from the two tables rather than only from a Deep Dive.

## The shape

**`shared/iv_history.py`** — the module moves there from
`services/trade_svc/deepdive/`. It must: `options-scanner/scanner_engine.py`
cannot import `services.*`, and duplicating a store's write path is how two
writers come to disagree about a schema. The precedent is exact —
**`shared/earnings.py`** is the cross-tier path to `EARNINGS_CALENDAR_DB`, a store
that also lives under `services/trade_svc/data/`. The module is a leaf (sqlite3,
math, logging, datetime, plus pandas/numpy in `backfill_rv` alone), which is
`shared/`'s contract. Three import sites in `trade_svc` follow it.

⚠ **`DEFAULT_DB_PATH` was `Path('./iv_history.db')`** — a *relative* default, so
any caller that omitted the path would have written a stray database into
whatever the process's working directory happened to be. Both current callers
pass `repo_paths.IV_HISTORY_DB` explicitly, so nothing has leaked; the default
becomes that constant so nothing can.

**Two new pure functions**, both testable without a proxy:

- `atm_iv_ladder(chain)` → `[{"dte", "atm_iv"}]`, ATM being the mean IV of
  strikes within 3% of spot, merged across the put and call maps. A light dict
  walk rather than the deepdive's DataFrame flatten, because this one runs inside
  a scan.
- `cm30_from_chain(chain)` → `(cm30, basis)` per the clamp rule above.

**One call site:** `run_full_scan`'s per-symbol block, wholly guarded — a
volatility snapshot must never be able to break a scan. It writes on **every**
scan; the upsert's last-write-wins therefore stores the day's **final** reading,
which is a consistent late-session basis and is also what an intraday reader
wants. One tiny upsert per symbol per scan is noise against the 24 chain fetches
and full candidate scoring the same pass already does.

**The relabel:** the UI still says **"IV Rank"** in three places — the Market
Scanner table, the Strategy Finder table, and the Trade detail panel — five months
after the engine module renamed it internally. They become **"Vol Rank"**, which
is what the number is.
