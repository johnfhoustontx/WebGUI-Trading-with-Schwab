# CLAUDE.md

> **Monorepo note:** This app now lives in the *Trading With Schwab* monorepo.
> Cross-app paths and service ports come from the root `repo_paths.py` (which
> reads `config/ports.toml`) — never hard-code `D:\` paths or ports; import
> them. The proxy is at `PROXY_URL` (`http://127.0.0.1:8100`). See the root
> `CLAUDE.md` for the monorepo overview. Some older absolute paths mentioned
> below (e.g. `D:\AI_Based_Analysis`, `D:\Schwab Test Project`) are historical
> and have been superseded by `repo_paths.py`.

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**In THIS monorepo this folder is ENGINES ONLY.** It is the 0-DTE / swing
credit-spread scanner, the GEX/charm/delta analytics, the options pricing math
and the paper-trade store - imported by `services/options_svc`, which owns all
scheduling and publishing. It has no UI and no entrypoint of its own.

The **Tk desktop app and the FastAPI + React web app described in older revisions
of this file were never copied into this repo**, and neither were their
entrypoints. Verified absent 2026-08-21: `dashboard.py`, `scanner.py`,
`eod_report.py`, `notifier.py`, `ai_prompt_builder.py`, `gamma_window_legacy.py`,
`server/`, `frontend/`. Read them in the source repo `D:\Trading With Schwab` if
you need the reference. The NiceGUI webgui (`webgui/`, :8500) is the only
interface now, and it reaches these engines only through `options_svc` over Redis.

Python 3.11+. Use the repo-root venv: `..\.venv\Scripts\python.exe`.

## Commands

```powershell
# Manual GEX-collection fallback. The options service owns collection; run this
# only when it is down. Honors the data/gex_collector.lock advisory lock.
python gex_collector.py

# Tests (from inside this folder)
..\.venv\Scripts\python -m pytest tests -q     # full suite - see baseline below
..\.venv\Scripts\python -m pytest tests/test_scoring.py -v
```


**Test baseline: 1180 passed, 0 failed, 2 skipped** (re-measured 2026-08-21).

**There are no longer any expected failures.** The 8-11 that sat here for months
were labelled "stale fixtures / timing-dependent / a missing doc file". On
inspection every one was a **stale fixture pinning a constant that had moved**:

| was failing | why |
|---|---|
| `test_next_boundary_*` (4) | pinned a 2-minute cadence after `POLL_INTERVAL_MIN` went 2 -> 1 on 2026-07-11 |
| `test_main_skips_before_market_open` | used 8:20/8:25 as "before the open" after the window moved 8:30 -> 8:00 CT |
| `test_acquire_defers_when_fresh_other_owner` | +200s was inside the old `LOCK_TTL_SEC` of 240, outside the derived 120 |
| `TestEarningsAvoidance` (2) | absolute 2026-05 dates drifted into the past - where three SIBLING tests also began passing for the wrong reason |
| `test_per_leg_expiry_...` | its "far" back-leg expiry of 2026-08-21 arrived |
| `test_key_levels_doc.py` (3) | asserted a doc file this repo never had; deleted with the dead code on 2026-08-20 |

All are now **derived from the constant or relative to today**, so they cannot rot
the same way again - the reasoning `gex_collector.py` already applies to
`LOCK_TTL_SEC`. Nothing was xfail-ed to hide it. The 2 skips are the deterministic
`test_dashboard_*` `importorskip`s described below.

> **Compare the failing SET, not the count.** This bit for real on 2026-08-07: a
> change introduced two genuine `TestScreenSpreadsStrikeValidity` regressions while
> two order-varying `test_dashboard_*` cases moved to skipped, holding the total
> steady and making a real break look like a clean run. Both halves of that trap
> are now closed - the dashboard tests skip at COLLECTION via a module-level
> `pytest.importorskip("dashboard")`, and the baseline is green - but the habit is
> still right: run with `-rf` and diff node IDs. A green baseline makes the
> exit code trustworthy, which is the point.
>
> The 2026-08-21 pass is also the argument against a standing red baseline: once
> "8 failures" is normal, a 9th (`test_per_leg_expiry_...`, which appeared the
> morning of the audit) is invisible.

**`pytest-randomly` is NOT installed in this venv** (verified 2026-08-07), so the
`-p no:randomly` in older commands here has always been a no-op; run order is
pytest's deterministic default.


There is no lint/format step configured. Black-Scholes math, DB schemas, and scoring are TDD; UI changes are verified manually.

## Critical operational rules

- **`services/options_svc` is the only orchestrator.** It calls
  `scanner_engine.run_full_scan` and writes `data/signals.db`. The CLI
  (`scanner.py`) and FastAPI (`server/scanner_task.py`) orchestrators that used to
  compete for that DB are not present in this repo.
- **Schwab client resolution:** everything goes through schwab-proxy at
  `repo_paths.PROXY_URL` (:8100). There is no direct-`schwab-py` fallback here -
  the refresh token is a single rotating credential and two holders invalidate
  each other's session.

- **`data/` and `logs/` are git-ignored and regenerated at runtime.** Never check in `signals.db`, `gex_history.db`, `trades.db`, or anything in `data/reports/`.
- **Settlement-time behavior matters.** The EOD rollup is built by the webgui `/eod` page and `services/options_svc`; the GEX collector exits cleanly past STOP_HOUR (~15:20 CT). Don't add work that assumes processes are still polling after that.

## Architecture (big picture)

Loosely-coupled engine subsystems share Schwab access (via the proxy) + SQLite/JSON stores. No event bus inside this folder - subsystems talk through shared stores and explicit function calls; `services/options_svc` drives them and publishes to Redis.

1. **Scanner & signals** — `scanner_engine.py` (core; orchestrated by `services/options_svc`), `scoring.py` (9-factor 0–100 composite: R:R, PoP, Theta, IV Rank, IV/HV, Vega, EM Buffer, Liquidity, Trend), `iv_analysis.py`, `signal_db.py` + `signal_recorder.py` (dedup by symbol/type/strikes/expiration), `signal_recommender.py` (HOLD/TAKE/CUT rules), `signal_repricer.py` (intrinsic at 0-DTE settlement; mid-mark for swings with per-`(symbol, expiration)` chain cache).
2. **Gamma analytics** — `gamma_tool.py` is the **headless GEX/Charm/DEX/Vanna engine** (`GammaEngine`, `build_analysis_dict`, `calc_flip_point`, `fetch_symbol_analysis`, the bundled-prompt builders, `draw_term_heatmap`). **The legacy Tk window (`gamma_window_legacy.py`) was DELETED on 2026-08-20** along with the rest of the dead UI code; the webgui `/options/gamma` page is its replacement. **Do not re-add `tkinter`/`matplotlib` imports at module scope in `gamma_tool.py`**: they were being paid by every headless importer (`services/options_svc` ~10 lazy sites, `gex_collector`, `scanner_engine`) — measured **0.69 s → 0.207 s** and `sys.modules` 478 → 239 once removed, plus `matplotlib.use("TkAgg")` is no longer forced process-wide. `build_chart_style_vars` imports `tkinter` function-locally and `draw_term_heatmap` imports matplotlib function-locally for the same reason; `tests/test_gamma_tool_headless.py` pins this with a subprocess import probe. The per-strike snapshot collector writes every **1 min** (was 2 min; 2026-07-11) via `gex_history_db.py` over `gex_collector.collection_symbols()` (the index base `$SPX`/`$VIX`/`SPY`/`QQQ` ∪ the `Top 20.xlsx` watchlist; `poll_once(..., symbols=None)` defaults to it). **`poll_once` fetches the ~24 per-symbol chains CONCURRENTLY** (a `ThreadPoolExecutor`, `POLL_FETCH_WORKERS=6`; 2026-07-18) — the serial loop consumed 15–35 s of the 60 s slot and (measured) dropped ~37% of 1-min slots; engine compute + SQLite inserts stay on the calling thread (conn affinity + `engine._last_dte`). It also accepts an **`on_chain(symbol, chain)`** callback so the options service can reuse the just-fetched chain for the same tick's gamma snapshot instead of refetching it. **`gex_json` grids are stored as a COLUMNAR float32 blob** (2026-08-08; `_pack_columnar`/`_unpack_columnar` behind `_encode_grid`/`_decode_grid`): `b"G1"` + zlib(`<I` count + n float32 sorted strikes + n×3 float32 call/put/net). Measured **2.5× faster decode / 2.9× faster encode / 1.38× smaller** than the previous JSON-in-zlib — `json.loads` had been **68% of the whole read path** while SQLite itself was only 4% (which is also why a **PostgreSQL migration was evaluated and rejected — measured SLOWER** for this workload). **Shape-gated:** anything but three plain numbers per cell (nested dicts / strings / None / `bool` / a missing field) falls back to the JSON path, preserving the flexible-cell contract pinned by `test_gex_history_efficiency`; 100% of real cells are `{call, put, net}` floats. **float32 is safe** (values are already rounded to `_GRID_SIG_FIGS`=6 sig figs; max rel err 5.96e-08 over 471,657 real cells) **except that sub-1.18e-38 denormals flush to 0.0** — verified display-neutral across 600 real snapshots (0 wall disagreements, 0 display-significant cells lost; flip/walls/net_total live in their own columns computed from the full chain). Decoded values are plain Python floats, never numpy scalars (the grid is JSON-serialized into `cache:options:gamma`). **Forward-only** — readers accept all three formats (columnar BLOB + legacy compressed BLOB + legacy JSON TEXT). `connect()` sets **`PRAGMA mmap_size=1 GiB`** (`_MMAP_BYTES`) because one (symbol, view, session) read touches ~437 scattered pages (rows of one key land 360 rowids apart). **`WITHOUT ROWID` was measured and REJECTED** (59% larger, 4× slower writes — 1.3 KB blobs are its documented anti-pattern). The redundant `idx_snap_today` index (duplicate of the PK) is dropped, and the **term-structure chain polls every 5 min** (`TERM_POLL_INTERVAL_MIN`, not every 1-min slot). Flow-alert detectors read only the trailing rows via **`gex_history_db.load_flow_tail`** (2026-07-19). The always-on options service owns collection. Only **one collector runs at a time**, enforced by an advisory file lock at `data/gex_collector.lock` (helpers in `gex_collector.py`); `gex_collector.py` remains as a **manual fallback** and stands down if the service already owns the lock. The Task Scheduler job is **retired** — disable/delete the `GEX Collector` scheduled task. Math in `options_calculator.py` (`bs_delta`/`bs_charm`/`bs_gamma`, `calc_summary`). Collector health classifier in `gex_status.py`.
3. **Paper trading** — `paper_trader.py` + `trades_db.py` (UUID trade IDs, status transitions in place — rows are never deleted), `trade_analyzer.py` (live Greeks + data-quality flags for the Analyze popup). **Rescue apply primitives** live in `paper_adjust.py`: `apply_close`/`apply_partial_close`/`apply_narrow`/`apply_convert_ic`/`apply_convert_butterfly`/`apply_roll`/`apply_inverted` mutate the paper DB inside the existing cash/buying-power mechanism (reconciling reserved BP to the new max-loss + writing an audit row), and the `apply_adjustment` dispatcher re-prices the candidate legs and **aborts without mutation** if economics drifted past tolerance or the position isn't OPEN (the stale-price guard). `paper_account_db.py` backs this with a `position_adjustments` audit table + a `parent_position_id` column on `paper_positions` (for linked rolls), plus `insert_adjustment`/`list_adjustments`. The advisory engine + on-demand candidate menu that drive these live in the webgui options service (`services/options_svc/rescue.py`); see the root `CLAUDE.md` "Rescue tested trades" section.
4. **Notifications & AI** — not in this folder. `notifier.py` and `ai_prompt_builder.py` were deleted 2026-08-20; push notifications live in `shared/notify/` + `services/options_svc/push_notify.py`, and Claude calls are made by the services (see the root `CLAUDE.md`).
5. **EOD reporting** — not in this folder. `eod_report.py` was deleted 2026-08-20; the rollup is the webgui `/eod` page, archiving to `webgui/data/eod/<date>/`.

### Interface

There is exactly one: the **NiceGUI webgui** (`webgui/`, :8500), which never
imports this folder. It reads Redis cache views published by
`services/options_svc`, which is the only thing that imports these engines. The
Tk `dashboard.py` hub and the React/FastAPI stack described in older revisions do
not exist here.


### Storage

```
data/signals.db           signals, marks, outcomes
data/gex_history.db       per-strike intraday snapshots (full grids + summary fields keyed by symbol/view/ts)
data/trades.db            paper-trade lifecycle (trades + trade_events)
data/reports/*.md         EOD rollups
data/chart_style.json     per-element gamma-chart styling
logs/gex_collector.log    GEX collector stderr
logs/scanner_YYYY-MM-DD.log
```

Both `.db` files use idempotent `init_schema` migrations — safe to call on every startup.

## Five-state classifier engine inputs (2026-07-07)

Two PURE engine modules feed the webgui's five-state market classifier (full design in the root
`CLAUDE.md`): **`flow_skew.py`** — `risk_reversal_25d(chain)` (25-δ put−call IV, shared front
expiration) + `index_call_put_volume(chain)`, computed in the options-service **2-min GEX poll** from
the already-fetched $SPX/SPY/QQQ chains (no extra fetch), persisted per snapshot in `gex_history_db`
(additive `rr_25d`/`call_vol`/`put_vol` columns) → published as `cache:options:flow_skew`.

> **Intraday premium-flow data (2026-07-09, Phase 1 of the flow-chart feature).** The same 2-min poll
> now also computes **`flow_skew.index_call_put_premium(chain)`** — daily-cumulative call vs put
> **premium ($)** = `Σ mark × totalVolume × 100` (mark = mid; Schwab has no tape, so this is an
> UNSIGNED cumulative estimate, not a buy/sell split) — for **every** collected symbol (index base +
> `Top 20.xlsx`), stored in `gex_history_db` as additive `call_prem`/`put_prem` REAL columns (idempotent
> ALTER migration). Read via **`gex_history_db.load_flow_series(conn, symbol, d=None)`** →
> `(ts, spot, call_vol, put_vol, call_prem, put_prem)` per snapshot, chronological (feeds the coming
> per-symbol intraday price + call/put premium/volume chart). Forward-only (no backfill of premium on
> pre-existing rows). Phase 2 (the webgui **Flow** tab, inserted before Term) shipped.
> **Forward gamma projection (2026-07-11):** the webgui GEX heatmap also re-prices this
> standing OI at future 15-min marks to the 4pm-ET close (`services/options_svc/compute.project_gex_grid`,
> flat-spot BS gamma time-decay ratio) → a forward projection band + expected-move cone; GEX-only,
> hidden off-hours. See the root `CLAUDE.md` "Last updated" entry. And
**`strategy_scoring.py`** gained a LOW-weight market-state family tilt (`state_family_tilt`,
`STATE_TILT_MAX=6`) folded into `score_strategy`'s composite **after** the hard-gate grade (a ranking
nudge that can never flip a gated grade), fed the live state by the options-service `swing` handler.

## Where to extend

- **New Greek view** (e.g. Vanna): extend `GammaEngine` in `gamma_tool.py` mirroring `calc_charm_from_chain`, add the view string to `_set_view`/`_redraw`, add `bs_vanna` in `options_calculator.py`. The history-DB schema is view-string keyed — no migration needed.
- **New scoring factor:** weight + math in `scoring.calc_composite_score`, thread through `scanner_engine.run_full_scan` so it shows in the dashboard detail panel, regression tests in `tests/test_scoring.py`.
- **New GUI-reachable data:** publish a cache view from `services/options_svc/handlers.py` and read it in a `webgui/pages/` module. Tier 1 never imports these engines.
- **New scanner spread type** (e.g. iron condor): extend `scanner_engine`'s spread filter, add a `type` tag in the signal dict, add a table in the dashboard and a key in `signal_db`.
- **New alert channel:** extend `notifier.Notifier`; `signal_alert` already has dispatcher stubs.

## Design-doc convention (load-bearing)

Every substantive feature has paired docs in `docs/plans/`:

```
docs/plans/YYYY-MM-DD-<topic>-design.md    # what and why
docs/plans/YYYY-MM-DD-<topic>-plan.md      # how (TDD task list, exact files, tests)
```

**Before extending a subsystem, `ls docs/plans/` and skim the relevant design doc** — it explains *why* the shape is what it is, which the code doesn't capture. New features are expected to follow the same pattern: design doc first, then plan, then code.

## Commit conventions

Small commits with conventional prefixes: `feat:`, `fix:`, `refactor:`, `chore:`, `test:`, `docs:`. Run `pytest tests/ -q -p no:randomly` before pushing and compare against the **1311 passed / 17 failed** baseline at the top of this file — **comparing the failing SET, not the count**, since the `test_dashboard_*` tests wander between fail and skip. (This line previously cited a "246/7 baseline" that had not been true for a very long time.)

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — subsystem details, per-module role tables, data flow, extension points
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — trader-facing walkthrough; also the canonical list of known issues
- [docs/INSTALL.md](docs/INSTALL.md) — clean-machine install, Schwab OAuth, Task Scheduler setup
- [docs/SIGNAL_TRACKING.md](docs/SIGNAL_TRACKING.md) — `signals.db` internals (predates ARCHITECTURE.md but still authoritative for the signal schema)

---

## Options Simulator

`options_simulator/` is **three flat modules**, not the Dash application older
revisions of this file described (there is no `viz/`, `engine/` or `data/`
package here, and no plotly/dash/py_vollib/mibian dependency - the webgui charts
with Highcharts):

| module | role |
|---|---|
| `engine.py` | Black-Scholes pricing + analytical Greeks; `Leg` / `Position` / `aggregate_position` for arbitrary multi-leg positions (each leg scaled by `sign * ratio`, so a butterfly body can trade 2x) |
| `data.py` | option-chain history load for Replay. Builds its index with `.tz_convert("America/Chicago").tz_localize(None)` - **a tz-naive datetime in this project means CENTRAL time** |
| `pnl.py` | P&L decomposition into delta / gamma / theta / vega contributions |

The three simulation modes (Replay, What-if, IV shock), the per-leg time
treatment, and the shared strategy/leg editor all live in
`services/options_svc/compute.py` (`sim_run`, `sim_replay`) and
`webgui/pages/options/simulator.py`. **Never compute a time-to-expiry inline** -
there is one settlement instant (16:00 ET) and one helper per tier
(`options_calculator.expiry_time_to_years`,
`services/options_svc/compute.time_to_expiry_years`). See the root `CLAUDE.md`
"Multi-leg Simulator + Calculator" and time-basis entries.
