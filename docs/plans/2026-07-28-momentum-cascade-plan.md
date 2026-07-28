# Momentum Cascade — implementation plan (Phase 1)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this
> plan task-by-task. Read the companion design first:
> [2026-07-28-momentum-cascade-design.md](2026-07-28-momentum-cascade-design.md)

**Goal:** Ship a regime-conditioned momentum score across three levels (11 sectors,
74 industry ETFs, 311 stocks) sourced from the new **Stocks** tab in
`Sectors_Industries_ETFs.xlsx` — pure scoring modules, a nightly sentiment_svc slot,
SQLite bar + score storage, a `cache:sentiment:momentum` publish, and a webgui page.

**Explicitly NOT in scope:** `scoring/__init__.py:WEIGHTS` is untouched. No new
sentiment component, no bridge `component_scores` change, no driver gate, no sizing
change. Momentum is context published on its own key.

**Architecture:** PURE math in `sentiment-dashboard/scoring/momentum.py` +
`momentum_regime.py` (TDD'd, no I/O, no tk), orchestration in
`services/sentiment_svc` (own nightly slot, NOT the 120 s tick), additive
`MomentumSnapshot` contract, Tier-1 webgui reader.

**Tech stack:** Python 3.11, pytest, fakeredis (via `shared/bus` under pytest),
NiceGUI + Highcharts (plain chart — NOT stockChart), SQLite.

**House rules that bind every task:** TDD (failing test first); tests run per-folder
(`cd sentiment-dashboard; ..\.venv\Scripts\python -m pytest tests` — services from the
repo root: `.venv\Scripts\python -m pytest services\sentiment_svc`); ruff clean before
each commit; no hard-coded `D:\` paths or ports — import from `repo_paths`; webgui
pages are Tailwind-first (no `.style()`); every compute is defensive (degrade, never
raise); no long "what this does" docstrings — comments explain *why*; commit after
each green task.

---

## Task 1: `sectors_ref.load_stocks_data()` — read the Stocks tab

**Files:**
- Edit: `sentiment-dashboard/sectors_ref.py`
- Test: `sentiment-dashboard/tests/test_sectors_ref_stocks.py`

Mirror `load_sectors_data` exactly: same mtime-cache pattern, separate single-entry
cache dict, `reset_stocks_cache()` test helper, `[]` on missing workbook/openpyxl.

Returns a list of dicts in workbook order:
`{sector, industry, rank, symbol, company, etfs}` where `etfs` is the split list from
column F. Skip the header row, skip rows with no symbol, and stop at the trailing
merged note block (row 372+ — guard on `symbol` being falsy, do not hard-code a row
number).

Also expose `stock_symbols()` → deduped list of the 311 symbols, and
`constituents_by_industry()` → `{(sector, industry): [symbols]}`, since both callers
downstream want those shapes and neither should re-derive them.

**Tests:** row count 370; 74 distinct `(sector, industry)` keys; every key has exactly
5 symbols; `stock_symbols()` is deduped (311, not 370 — names legitimately repeat
across industries); missing-file returns `[]`; cache returns the same object until
mtime changes.

---

## Task 2: `scoring/momentum.py` — the five components (pure)

**Files:**
- Create: `sentiment-dashboard/scoring/momentum.py`
- Test: `sentiment-dashboard/tests/test_momentum.py`

No I/O, no tk, no proxy. Every function takes plain lists/arrays and returns
`float | None` — `None` on insufficient bars, never a raise, never a silent 0.

```
trend_strength(closes, n=90)        -> annualized exp-regression slope * R²
relative_strength(closes, bench, n=63) -> (excess return, RS-line slope)
acceleration(closes)                -> r21 - r63/3
path_quality(closes, n=63)          -> blend(pct up days, return/realized vol)
participation(constituent_closes)   -> fraction above own 50 DMA
zscore_within_level(values)         -> clipped ±3, None-safe
blend(components, weights)          -> renormalizes over present components
percentile_rank(values)             -> 0-100 within level
```

Weights live in a module-level `MOMENTUM_WEIGHTS` dict (trend .30, rs .25, accel .20,
path .15, participation .10) with an import-time assert that it sums to 1.0 — same
invariant style as `scoring/__init__.py:WEIGHTS`.

`blend` renormalizing over *present* components is what makes stock-level scoring
correct: participation is undefined there, and substituting a neutral 0 would bias
every stock down against its own peer group.

**Tests, one per breakpoint:**
- `trend_strength` on a clean exponential series → recovers the known annualized rate,
  R² ≈ 1.0; on the same series with noise injected → same sign, materially lower value.
- `trend_strength` with `len(closes) < n` → `None`.
- `relative_strength` where symbol == benchmark → excess 0.0, slope ≈ 0.
- `acceleration` on a series whose last 21d is flat after a strong 63d → negative
  (the decelerating case the page exists to catch).
- `path_quality`: monotonic riser scores above a same-return sawtooth.
- `participation`: 3 of 5 above their 50 DMA → 0.6; empty input → `None`.
- `zscore_within_level` with a `None` in the list → that slot stays `None`, others
  unaffected; all-identical input → all 0.0 (not a divide-by-zero).
- `blend` with participation missing → weights renormalize to 1.0, result matches the
  hand-computed four-component value.

---

## Task 3: `scoring/momentum_regime.py` — the gate (pure)

**Files:**
- Create: `sentiment-dashboard/scoring/momentum_regime.py`
- Test: `sentiment-dashboard/tests/test_momentum_regime.py`

```
dispersion(returns_by_symbol)                    -> cross-sectional stdev of 5d returns
dispersion_percentile(current, history)          -> 0..1, None if history < 60
classify(spy_closes, vix_term, dispersion_pct)   -> RegimeVerdict
```

`RegimeVerdict` is a frozen dataclass: `state` (`favorable` | `neutral` |
`suppressed`), `lookback` (`"63/126"` | `"21/63"`), `crash_risk: bool`, and
`reasons: tuple[str, ...]` — the human-readable clauses the page banner renders.

Rule order matters; check `suppressed` first:
- **suppressed** — SPY < 200 DMA AND 21d return > 0 AND realized-vol percentile > 80.
- **favorable** — SPY > 200 DMA AND VIX term in contango AND dispersion_pct > 0.40.
- **neutral** — everything else.

Module-level tunables at the top (`CRASH_VOL_PCT = 0.80`, `DISPERSION_FLOOR = 0.40`,
`SMA_LONG = 200`), same convention as `scoring/trend_regime.py`.

**Tests:** one case per branch, plus the boundary on each constant; missing/short SPY
history → `neutral` with a `reasons` entry naming the gap (degrade, never raise);
`dispersion` with a single symbol → `None`.

---

## Task 4: `services/sentiment_svc/momentum_db.py` — SQLite store

**Files:**
- Create: `services/sentiment_svc/momentum_db.py`
- Test: `services/sentiment_svc/tests/test_momentum_db.py`

Follow `intraday_history_db.py` for connection handling, path resolution via
`repo_paths`, and pragma setup.

Two tables:
- `daily_bars(symbol, date, open, high, low, close, volume)` — PK `(symbol, date)`,
  index on `symbol`.
- `momentum_scores(session_date, level, symbol, score, percentile, rank, components_json,
  participation)` — PK `(session_date, level, symbol)`.

API: `upsert_bars(rows)`, `max_date(symbol)`, `bars(symbol, limit=252)`,
`write_scores(session_date, level, rows)`, `scores(session_date, level)`,
`rank_history(level, days=60)` (feeds the ribbon), `prune(keep_days=400)`.

`max_date` is what makes the nightly run cheap — it drives the delta fetch. Test it
returns `None` for an unknown symbol rather than raising, since that is the
first-backfill path.

---

## Task 5: `compute.compute_momentum()` — orchestration

**Files:**
- Edit: `services/sentiment_svc/compute.py`
- Test: `services/sentiment_svc/tests/test_compute_momentum.py`

Sequence: load the Stocks tab (Task 1) → for each of the 385 symbols read
`momentum_db.max_date` → fetch only missing bars through the **existing** proxy
price-history helper (reuse what `history_backfill.py` uses; do not add a new HTTP
client) → `upsert_bars` → pull 252d per symbol → apply the $5M 20d-dollar-volume
filter → score each level via `scoring.momentum` → z-score and percentile *within
level* → compute dispersion and the regime verdict → assemble the payload.

Fan the delta fetch through `services/_parallel.py`. Any symbol that fails a fetch or
has fewer than 90 bars lands in `excluded` with its reason and is dropped from the
z-score population — it must not become a zero in the distribution.

Stock-level `alignment` is the three-block flag: its sector top-quartile, its industry
top-quartile, its own top-quartile.

**Tests** use a fake proxy + temp DB: 385 symbols requested; a symbol already current
triggers no fetch; a failing symbol appears in `excluded` and is absent from `levels`;
a thin-volume symbol is excluded with `reason: "liquidity"`; the payload validates
against `MomentumSnapshot`.

---

## Task 6: contract + handler + scheduler slot

**Files:**
- Edit: `shared/contracts/sentiment.py` (add `MomentumSnapshot`)
- Edit: `services/sentiment_svc/handlers.py`, `scheduler.py`
- Test: `services/sentiment_svc/tests/test_momentum_publish.py`

Handler writes `cache:sentiment:momentum` and publishes the change event, exactly as
the composite/sectors views do. Nothing existing changes — this is a fourth cache view
alongside composite / history / sectors.

Scheduler gains **one nightly slot** at ~16:20 CT on weekdays (reuse the existing
`_HOLIDAYS` set and market-hours helpers — do not duplicate the calendar), plus a
`handle_command` entry point for a manual refresh from the page. Guard with a lock and
a `last_session_date` sentinel so a manual refresh racing the scheduled one cannot
double-run the backfill.

**Tests:** the slot does not fire during RTH; it does not fire twice for the same
session date; a manual command forces a run; publish payload round-trips through the
contract.

---

## Task 7: `webgui/pages/sentiment_momentum.py`

**Files:**
- Create: `webgui/pages/sentiment_momentum.py`
- Edit: nav registration (follow `sentiment_rrg.py` / `sentiment_sectors.py`)

Tier-1 reader: reads `cache:sentiment:momentum` only. No proxy calls, no compute.

Three panels, in this order:

1. **Regime banner** — the `state`, the active lookback, and the `reasons` clauses.
   In `suppressed`, the banner is the loud element on the page and the leaderboard
   renders muted beneath it.
2. **Quadrant scatter** (Highcharts `scatter`, plain chart) — score on x, acceleration
   on y, bubble sized by dollar volume, colored by sector. Quadrant labels:
   bottom-right *Extended*, top-left *Emerging*, top-right *Leading*, bottom-left
   *Lagging*. Toggle the series between industry and stock level. This chart replaces
   about six tables — build it first.
3. **Rank ribbon** (bump chart from `rank_history`) — 74 industries over 60 days, and
   a **leaderboard** table beneath: top and bottom 15, expandable to constituents,
   with the component columns visible. A score that cannot be decomposed is a score
   nobody trusts at 9:31.

Footer shows `excluded` count with a hover listing symbols and reasons — that is how a
delisted or renamed ticker on the static Stocks tab becomes visible instead of
silently vanishing.

---

## Task 8: docs

- `sentiment-dashboard/CLAUDE.md` — new section: momentum is a separate subsystem on
  its own cache key, **not** a composite component; where the pure modules live; the
  nightly cadence and why it is not on the 120 s tick.
- Root `CLAUDE.md` — one line in the service map for the nightly slot and the new
  cache view.
- `CHANGELOG.md` — entry.

---

## Verification before calling Phase 1 done

- `cd sentiment-dashboard; ..\.venv\Scripts\python -m pytest tests -q` green (existing
  105 tests plus the new ones).
- `.venv\Scripts\python -m pytest services\sentiment_svc -q` green.
- ruff clean.
- `sum(WEIGHTS.values()) == 1.0` still asserts at import and the bridge snapshot
  regression fixture (`tests/fixtures/bridge_v39_snapshot.json`) is unchanged — proof
  the composite was not touched.
- One real nightly run against the live proxy: confirm ≤ 385 fetches on the first
  backfill and a small delta on the second, and eyeball the top 15 industries against
  the RRG page for agreement.
