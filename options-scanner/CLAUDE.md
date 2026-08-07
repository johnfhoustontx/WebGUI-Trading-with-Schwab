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

Single-user, self-hosted, Windows-first intraday options-trading platform built around the Schwab API. It scans for 0-DTE and swing credit spreads on $SPX / SPY / QQQ, scores them, visualizes dealer gamma/charm/delta exposure with a forward-projection heatmap, manages paper trades, and emits an end-of-day markdown rollup. Ships two interfaces over the same Python core: a Tk desktop app and a FastAPI + React web app.

Python 3.11+. On this machine: `C:\Users\john_\AppData\Local\Programs\Python\Python311\python.exe`.

## Commands

```powershell
# Tk desktop (primary trader interface) — gamma tool launches as a Toplevel from here
python dashboard.py

# Web stack
uvicorn server.main:app --reload --port 8000
cd frontend; npm install; npm run dev          # dev server on :5173
cd frontend; npm run build                     # → frontend/dist/, served by FastAPI StaticFiles

# Standalone scanners / tools
python scanner.py                              # auto-loops 15 min, Mon–Fri 08:00–15:15 CT
python scanner.py --once                       # one-shot (also: scan_once.bat)
python gex_collector.py                        # 1-min GEX snapshots over collection_symbols() (index base + Top 20.xlsx watchlist) — MANUAL FALLBACK ONLY (the options service owns collection)
python eod_report.py                           # today's EOD rollup
python eod_report.py --date YYYY-MM-DD         # backfill any date

# Tests
pytest tests/ -v --tb=no -q                    # full suite — see baseline below
pytest tests/test_scoring.py -v                # single file
pytest tests/test_scoring.py::test_name -v     # single test
```

**Test baseline: 1327 passed, 17 failed, 1 skipped** (re-measured 2026-08-07 after the EM-scoring change; was 1319/15/2 earlier that day with `-p no:randomly`; was 1309/17/1 on 2026-08-06 and 1286/17 on 2026-07-25 — the failing SET is unchanged, only the passing count grew with intervening work; the 15-vs-17 swing is the order-varying `test_dashboard_captured_signals_drift` pair moving between failed and skipped).

> **Compare the failing SET, not the count** — this bit for real on 2026-08-07: a change introduced two genuine `TestScreenSpreadsStrikeValidity` regressions while two order-varying `test_dashboard_*` cases happened to move to skipped, holding the total at 17 and making a real break look like a clean run. The failures are **pre-existing** and unrelated to current features — do not "fix" them as part of unrelated work; flag them only if a change is expected to touch them. They fall in four groups:

- `tests/test_scanner_engine.py::TestEarningsAvoidance` — stale fixtures
- `tests/test_dashboard_*` — **`dashboard.py` was never copied into the webgui monorepo**, so these fail `ModuleNotFoundError: No module named 'dashboard'`. Diagnosed 2026-07-25; previously mis-filed as "intermittent". Whether a given one *fails* or *skips* still varies with ordering (the Tk-root guard), so the count moves even under `-p no:randomly`.
- `tests/test_gex_collector*` — timing-dependent
- `tests/test_key_levels_doc.py` — a missing doc file

**The failure COUNT varies 14–16 run-to-run** — some are order-dependent under `pytest-randomly`. Use `-p no:randomly` for a comparable number, and when in doubt measure your own baseline (`git stash` → run → `git stash pop`) rather than trusting this line. Compare the *set* of failing tests, not just the count.

> This figure was **667 passed / 2 failed** for a long time and was badly stale — it was propagated into a plan doc in 2026-07 and nearly caused a real regression to be waved through as "pre-existing". If you notice it drifting again, fix it here.

See [USER_GUIDE § Known issues](docs/USER_GUIDE.md#known-issues).

There is no lint/format step configured. Black-Scholes math, DB schemas, and scoring are TDD; UI changes are verified manually.

## Critical operational rules

- **Never run two scanner orchestrators at once.** `scanner.py` (CLI) and `server/scanner_task.py` (the FastAPI background task) both call `scanner_engine.run_full_scan` and both write to `data/signals.db`. Running both simultaneously causes duplicate signals and Schwab token-refresh conflicts. Pick one per machine.
- **Schwab client resolution:** every module that needs Schwab data first checks for SchwabProxy at `http://127.0.0.1:8100` (multi-tool token sharing, lives at `D:/AI_Based_Analysis/SchwabProxy`); falls back to direct `schwab-py` if absent. Don't bypass this — token refresh conflicts otherwise.
- **`data/` and `logs/` are git-ignored and regenerated at runtime.** Never check in `signals.db`, `gex_history.db`, `trades.db`, or anything in `data/reports/`.
- **Settlement-time behavior matters.** EOD report auto-triggers at 15:00 CT inside `server/scanner_task.py` using a sentinel file to prevent double-runs; the GEX collector exits cleanly past STOP_HOUR (~15:20 CT). Don't add work that assumes processes are still polling after that.

## Architecture (big picture)

Five loosely-coupled subsystems share Schwab access + SQLite/JSON stores. No event bus — subsystems talk through shared stores and explicit function calls. Both interfaces (Tk, Web) pull from the same cores independently.

1. **Scanner & signals** — `scanner_engine.py` (core), `scanner.py` / `server/scanner_task.py` (orchestrators), `scoring.py` (9-factor 0–100 composite: R:R, PoP, Theta, IV Rank, IV/HV, Vega, EM Buffer, Liquidity, Trend), `iv_analysis.py`, `signal_db.py` + `signal_recorder.py` (dedup by symbol/type/strikes/expiration), `signal_recommender.py` (HOLD/TAKE/CUT rules), `signal_repricer.py` (intrinsic at 0-DTE settlement; mid-mark for swings with per-`(symbol, expiration)` chain cache).
2. **Gamma analytics** — `gamma_tool.py` is the **headless GEX/Charm/DEX/Vanna engine** (`GammaEngine`, `build_analysis_dict`, `calc_flip_point`, `fetch_symbol_analysis`, the bundled-prompt builders, `draw_term_heatmap`). **The legacy Tk window was split out to `gamma_window_legacy.py` on 2026-07-25** (`GammaWindow(tk.Toplevel)`: side-by-side bars + heatmap, forward-projection band, 0-DTE hedge-pressure panel, Chart Setup popup persisting 24 styleable elements to `data/chart_style.json`) — it is **parked/unused** (its `dashboard.py` entrypoint was never copied into the monorepo). **Do not re-add `tkinter`/`matplotlib` imports at module scope in `gamma_tool.py`**: they were being paid by every headless importer (`services/options_svc` ~10 lazy sites, `gex_collector`, `scanner_engine`) — measured **0.69 s → 0.207 s** and `sys.modules` 478 → 239 once removed, plus `matplotlib.use("TkAgg")` is no longer forced process-wide. `build_chart_style_vars` imports `tkinter` function-locally and `draw_term_heatmap` imports matplotlib function-locally for the same reason; `tests/test_gamma_tool_headless.py` pins this with a subprocess import probe. The per-strike snapshot collector writes every **1 min** (was 2 min; 2026-07-11) via `gex_history_db.py` over `gex_collector.collection_symbols()` (the index base `$SPX`/`$VIX`/`SPY`/`QQQ` ∪ the `Top 20.xlsx` watchlist; `poll_once(..., symbols=None)` defaults to it). **`poll_once` fetches the ~24 per-symbol chains CONCURRENTLY** (a `ThreadPoolExecutor`, `POLL_FETCH_WORKERS=6`; 2026-07-18) — the serial loop consumed 15–35 s of the 60 s slot and (measured) dropped ~37% of 1-min slots; engine compute + SQLite inserts stay on the calling thread (conn affinity + `engine._last_dte`). It also accepts an **`on_chain(symbol, chain)`** callback so the options service can reuse the just-fetched chain for the same tick's gamma snapshot instead of refetching it. **`gex_json` grids are stored zlib-compressed** (`_encode_grid`/`_decode_grid`, ~5× smaller; readers accept both compressed BLOB + legacy JSON TEXT), the redundant `idx_snap_today` index (duplicate of the PK) is dropped, and the **term-structure chain polls every 5 min** (`TERM_POLL_INTERVAL_MIN`, not every 1-min slot). Flow-alert detectors read only the trailing rows via **`gex_history_db.load_flow_tail`** (2026-07-19). In the webgui monorepo the always-on options service owns this; standalone it can still auto-start inside the gamma tool. Only **one collector runs at a time**, enforced by an advisory file lock at `data/gex_collector.lock` (helpers in `gex_collector.py`). `gex_collector.py` remains as a **manual fallback** (run it standalone if the gamma tool isn't open); it stands down if the in-tool collector already owns the lock. The Task Scheduler job is **retired** — disable/delete the `GEX Collector` scheduled task. Math in `options_calculator.py` (`bs_delta`/`bs_charm`/`bs_gamma`, `calc_summary`). Collector health classifier in `gex_status.py`.
3. **Paper trading** — `paper_trader.py` + `trades_db.py` (UUID trade IDs, status transitions in place — rows are never deleted), `trade_analyzer.py` (live Greeks + data-quality flags for the Analyze popup). **Rescue apply primitives** live in `paper_adjust.py`: `apply_close`/`apply_partial_close`/`apply_narrow`/`apply_convert_ic`/`apply_convert_butterfly`/`apply_roll`/`apply_inverted` mutate the paper DB inside the existing cash/buying-power mechanism (reconciling reserved BP to the new max-loss + writing an audit row), and the `apply_adjustment` dispatcher re-prices the candidate legs and **aborts without mutation** if economics drifted past tolerance or the position isn't OPEN (the stale-price guard). `paper_account_db.py` backs this with a `position_adjustments` audit table + a `parent_position_id` column on `paper_positions` (for linked rolls), plus `insert_adjustment`/`list_adjustments`. The advisory engine + on-demand candidate menu that drive these live in the webgui options service (`services/options_svc/rescue.py`); see the root `CLAUDE.md` "Rescue tested trades" section.
4. **Notifications & AI** — `notifier.py` (Windows toast + audio; pluggable for Telegram/Discord via stub slots in `signal_alert`), `ai_prompt_builder.py` (composes markdown prompts for pasting into Claude.ai — **no API calls**).
5. **EOD reporting** — `eod_report.py` writes `data/reports/YYYY-MM-DD-eod-report.md`.

### Interfaces

- **Tk desktop:** `dashboard.py` (`OptionsScannerApp(tk.Tk)`) is the trader hub; gamma tool is a separate `Toplevel` so both can be visible. A **Captured Signals** tab gives a live read of `signals.db` open signals (re-mark via `signal_repricer`, manual close via `signal_db.close_signal_manually`) — see [docs/plans/2026-05-26-captured-signals-tab-design.md](docs/plans/2026-05-26-captured-signals-tab-design.md).
- **Web backend:** `server/main.py` with `server/routes/{quotes,scanner,signals,trades,calculator,auth}.py` + `server/websockets.py`. Scanner results are served from in-memory `scanner_state.latest_results` (not DB) for speed; DB is authoritative for history. Paper trades + gamma snapshots are always DB-backed.
- **Frontend:** React + TypeScript + Vite in `frontend/`, talks REST + WebSockets to FastAPI.

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
- **New REST endpoint:** drop a module in `server/routes/`, register in `server/main.py`, consume from React via `fetch`.
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

Small commits with conventional prefixes: `feat:`, `fix:`, `refactor:`, `chore:`, `test:`, `docs:`. Run `pytest tests/ -v --tb=no -q` before pushing — anything beyond the 246/7 baseline is a regression.

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — subsystem details, per-module role tables, data flow, extension points
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — trader-facing walkthrough; also the canonical list of known issues
- [docs/INSTALL.md](docs/INSTALL.md) — clean-machine install, Schwab OAuth, Task Scheduler setup
- [docs/SIGNAL_TRACKING.md](docs/SIGNAL_TRACKING.md) — `signals.db` internals (predates ARCHITECTURE.md but still authoritative for the signal schema)


---

## Options Simulator — Overview

Replays 2 days of live Schwab option chain data through a Black-Scholes simulation engine
to visualize price decay, time decay, and all five Greeks interactively.

**Three simulation modes:**
- **Replay mode** — scrub through actual 2-day history bar by bar; all Greeks update live
- **What-if mode** — freeze time, sweep underlying price ±20%, show Delta/Gamma interaction
- **IV shock mode** — spike or crush implied volatility; shows Vega dominance on IV crush

> **Multi-leg (2026-06-23):** `options_simulator/engine.py` prices arbitrary
> multi-leg positions — `Position(legs=[Leg(contract, sign, ratio), ...])` +
> `aggregate_position` scale each leg's Greeks by `sign * ratio` (the `ratio`
> field, default 1, lets a **butterfly body** trade at 2×; build via
> `Position.from_legs([(contract, sign, ratio), ...], label)`). The webgui
> Simulator + Calculator drive this with a shared strategy/leg editor (verticals,
> condors, butterflies, **calendars/diagonals** with per-leg expiry). The
> per-leg time treatment lives in the webgui options service
> (`services/options_svc/compute.sim_run` — per-leg **elapsed** What-if decay —
> and `calc_spread_pnl(per_leg_expiry=True)` / `calc_summary_generic`); see the
> root `CLAUDE.md` "Multi-leg Simulator + Calculator" entry.

---

## File Structure

```
options_simulator/
├── data/
│   ├── fetcher.py          # Schwab API pulls, snapshot scheduling
│   └── cache.py            # Parquet/SQLite storage, keyed on (symbol, strike, expiry, ts)
├── engine/
│   ├── black_scholes.py    # Vectorized B-S pricing + analytical Greeks
│   ├── simulator.py        # Replay runner, what-if + IV shock scenario logic
│   └── pnl.py              # P&L decomposition: δΔS + ½γΔS² + θΔt + νΔσ
├── viz/
│   ├── surface.py          # 3D PnL surface (go.Surface: price × time → value)
│   ├── greek_decay.py      # 5-panel subplot, one Greek per panel, shared time axis
│   ├── chain_heatmap.py    # go.Heatmap: strikes × time, colored by Greek intensity
│   └── dashboard.py        # Dash app, layout, dcc.Slider replay control
├── tests/
│   └── test_black_scholes.py
├── main.py                 # Entry point
└── requirements.txt
```

---

## Tech Stack

| Layer | Library | Notes |
|-------|---------|-------|
| Data | `schwab-py` | Auth already configured in this project |
| Greeks | `py_vollib_vectorized` | Numpy-native, fast batch recalculation |
| Fallback Greeks | `mibian` | Simpler, good for single-contract checks |
| Visualization | `plotly` + `dash` | Interactive, embeds cleanly in tkinter via thread |
| Data storage | `pandas` + `pyarrow` | Parquet for option snapshots |
| Scheduling | `schedule` or `apscheduler` | Snapshot every 1–5 min during market hours |

**Python version:** `py -3.11` (matches rest of project)

---

## Data Schema

Option snapshot table — stored per (symbol, strike, expiry, timestamp):

```python
columns = [
    "timestamp",        # datetime, UTC
    "symbol",           # underlying ticker e.g. "SPY"
    "strike",           # float
    "expiry",           # date
    "option_type",      # "call" | "put"
    "underlying_price", # float — S at snapshot time
    "bid", "ask", "mid",# floats
    "iv",               # implied volatility (decimal, e.g. 0.22)
    "delta",            # from Schwab or recalculated
    "gamma",
    "theta",            # per-day convention (negative for long)
    "vega",             # per 1-vol-point
    "rho",
    "theo_price",       # Black-Scholes theoretical mid
    "days_to_expiry",   # float (intraday granularity)
]
```

---

## Simulation Engine — Key Logic

### Black-Scholes Greeks (analytical)

```python
# engine/black_scholes.py
"""
Options Simulator - Black-Scholes Engine
Version: 1.0.0
Last Updated: 2025-05-23

Version 1.0.0 Changes:
- Initial implementation
"""

import numpy as np
from scipy.stats import norm

#############################################
# BLACK-SCHOLES ANALYTICAL GREEKS
#############################################

def bs_greeks(S, K, T, r, sigma, option_type="call"):
    """
    Compute full Greek set analytically.

    Args:
        S: underlying price
        K: strike price
        T: time to expiry in years (e.g. 2/252 for 2 trading days)
        r: risk-free rate (decimal)
        sigma: implied volatility (decimal)
        option_type: "call" or "put"

    Returns:
        dict with keys: price, delta, gamma, theta, vega, rho
    """
    if T <= 0:
        return {"price": max(0, S - K) if option_type == "call" else max(0, K - S),
                "delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    N  = norm.cdf
    n  = norm.pdf

    if option_type == "call":
        price = S * N(d1)  - K * np.exp(-r * T) * N(d2)
        delta = N(d1)
        rho   = K * T * np.exp(-r * T) * N(d2) / 100
    else:
        price = K * np.exp(-r * T) * N(-d2) - S * N(-d1)
        delta = N(d1) - 1
        rho   = -K * T * np.exp(-r * T) * N(-d2) / 100

    gamma = n(d1) / (S * sigma * np.sqrt(T))
    theta = (-(S * n(d1) * sigma) / (2 * np.sqrt(T))
             - r * K * np.exp(-r * T) * (N(d2) if option_type == "call" else N(-d2))) / 365
    vega  = S * np.sqrt(T) * n(d1) / 100   # per 1 vol point

    return {"price": price, "delta": delta, "gamma": gamma,
            "theta": theta, "vega": vega, "rho": rho}
```

### P&L Decomposition

For each time step, attribute P&L by Greek contribution:

```python
# pnl.py  — second-order Taylor expansion
def decompose_pnl(greeks_t0, delta_S, delta_t_years, delta_sigma):
    delta_pnl = greeks_t0["delta"] * delta_S
    gamma_pnl = 0.5 * greeks_t0["gamma"] * delta_S ** 2
    theta_pnl = greeks_t0["theta"] * delta_t_years * 365  # convert back to per-step
    vega_pnl  = greeks_t0["vega"]  * delta_sigma * 100    # vega is per 1 vol point
    rho_pnl   = greeks_t0["rho"]   * 0                    # rate rarely changes intraday
    return {
        "delta": delta_pnl,
        "gamma": gamma_pnl,
        "theta": theta_pnl,
        "vega":  vega_pnl,
        "rho":   rho_pnl,
        "total": delta_pnl + gamma_pnl + theta_pnl + vega_pnl,
    }
```

---

## Visualization Panels

### 1. PnL Surface (`viz/surface.py`)
- `go.Surface(x=price_range, y=time_axis, z=option_values_matrix)`
- X axis: underlying price ± 10% from current
- Y axis: timestamp (2-day window, 1-min bars)
- Z axis: theoretical option price (Black-Scholes recalculated at each grid point)
- Add scatter overlay showing the actual path taken

### 2. Greek Decay Panel (`viz/greek_decay.py`)
- `make_subplots(rows=5, shared_xaxes=True)`
- Row 1: Delta — shows directional drift as S moves
- Row 2: Gamma — spikes near ATM near expiry
- Row 3: Theta — nonlinear decay, steeper final day
- Row 4: Vega — drops as expiry approaches
- Row 5: Rho — mostly flat for short-dated options
- Vertical cursor line driven by replay slider

### 3. Chain Heatmap (`viz/chain_heatmap.py`)
- `go.Heatmap(x=timestamps, y=strikes, z=greek_matrix)`
- Dropdown to select which Greek to color by
- ATM strike highlighted as a horizontal line
- Color scale: RdYlGn for Delta, Reds for Theta burn

### 4. Risk Dashboard (`viz/dashboard.py`)
- Summary cards: net delta exposure, gamma ($ per 1% move), daily theta burn, vega sensitivity
- Replay `dcc.Slider` with `dcc.Interval` auto-advance
- Scenario toggle: Replay / What-if / IV Shock

---

## Schwab API — Data Fetcher Pattern

```python
# data/fetcher.py
"""
Options Simulator - Schwab Data Fetcher
Version: 1.0.0
Last Updated: 2025-05-23
"""

#############################################
# OPTION CHAIN SNAPSHOT
#############################################

def fetch_option_chain(client, symbol: str, strikes_near_atm: int = 10) -> pd.DataFrame:
    """
    Pull option chain snapshot from Schwab API.
    Returns tidy DataFrame with one row per contract.
    """
    resp = client.get_option_chain(
        symbol,
        contractType="ALL",
        strikeCount=strikes_near_atm,
        includeUnderlyingQuote=True,
        strategy="SINGLE",
    )
    resp.raise_for_status()
    data = resp.json()
    return _parse_chain(data)
```

---

## Coding Conventions

Follows `ninja-python-dev` standards:

- **File headers:** Docstring with module name, version, date, changelog
- **Section separators:** `#############################################`
- **Classes:** `PascalCase` — `BlackScholesEngine`, `OptionSimulator`
- **Methods/functions:** `snake_case` — `fetch_option_chain`, `run_replay`
- **Constants:** `UPPER_SNAKE` — `TRADING_DAYS_PER_YEAR = 252`
- **Private:** `_prefix` — `_parse_chain`, `_build_surface_matrix`
- **Logging:** `logging.getLogger(__name__)` with file handler
- **GUI threading:** data fetches always in background threads, never on main thread

---

## Common Tasks for Claude Code

**"Add a new Greek to the decay panel"**
→ Edit `engine/black_scholes.py` `bs_greeks()` return dict, add row in `viz/greek_decay.py`

**"Add a new simulation mode"**
→ Add mode class in `engine/simulator.py`, wire toggle in `viz/dashboard.py`

**"Fetch and cache a full 2-day snapshot"**
→ See `data/fetcher.py` `fetch_option_chain()` → `data/cache.py` `save_snapshot()`

**"Change the heatmap Greek"**
→ Edit dropdown options in `viz/chain_heatmap.py`, update `z=` matrix source

**"Debug wrong Greek values"**
→ Cross-check `engine/black_scholes.py` `bs_greeks()` against known values:
  SPY ATM call, S=500, K=500, T=0.01 (≈2.5 days), σ=0.20, r=0.05
  Expected: delta≈0.52, gamma≈0.056, theta≈-0.18/day, vega≈0.28

---

## Critical Rules

1. **Always recalculate Greeks analytically** — don't rely solely on Schwab-provided Greeks for simulation (they are snapshots, not continuous)
2. **Time convention** — `T` in years; `1 trading day = 1/252`; theta displayed as per-calendar-day
3. **Vega convention** — per 1 volatility point (i.e., divide raw `∂V/∂σ` by 100)
4. **Theta sign** — theta is **negative** for long options; store as negative, display as negative
5. **IV from Schwab** — use `mark` price (mid of bid/ask) when solving for IV, not last price
6. **Python version** — `py -3.11`
7. **No blocking calls on Dash callbacks** — run fetches in `threading.Thread`, update via `dcc.Store`

