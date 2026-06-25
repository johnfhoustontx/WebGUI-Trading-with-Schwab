# Driver isolated paper account + performance scorecard (design)

**Date:** 2026-06-25
**Branch:** `Using_Highcharts`
**Status:** Approved (brainstorm) — pending implementation plan

## 1. Premise & goal

The autonomous Driver needs its **own isolated paper book** so its trades, P&L, and
performance are tracked **separately from the user's manual paper trades** — the
point is to measure *how well the autonomous module performs on its own*. The user
chose a **full performance scorecard** (not just isolation): win rate, profit
factor, P&L by symbol/strategy, etc.

This also corrects a latent wiring bug found while investigating: the Driver today
**writes** its trades into one paper system but **reads its day-P&L from another.**

## 2. The two paper systems (the problem)

`options-scanner/` has **two completely separate** paper stores:

| | System A — LEDGER | System B — ENGINE ACCOUNT |
|---|---|---|
| Code | `paper_trader.py` → `trades_db.py` | `paper_engine.py` + `paper_account_db.py` + `paper_broker.py` |
| DB | `data/trades.db` | `data/paper_account.db` |
| What | flat trade journal (entry + realized-on-close); **no repricing, no auto-manage, no account, no live unrealized P&L** | real broker sim: account balance/BP, positions w/ **live `unrealized_pnl`/marks**, auto-entry, **auto-manage/auto-exit** (TAKE_PROFIT/CUT/EXPIRED), session P&L, drawdown halt |
| View / page | `cache:options:paper_trades` · `/options/paper` | `cache:options:paper_account` · `/options/portfolio` |
| `source` / account field | **none** | **none** (single account, `CHECK(id=1)`) |

**The Driver's current (broken) wiring:**
- It enqueues `cmd:options` `paper_create` → `compute.create_paper_trade` →
  `paper_trader` → **System A (ledger)**. The `source="driver"` tag it attaches is
  **silently dropped** (the ledger has no such column).
- Its day-P&L / $500-target / halt logic reads `compute._day_pnl` →
  `cache:options:paper_account.session_pnl` → **System B (the user's manual book).**
- So the Driver's trades are inert ledger rows that **never reprice or auto-close**,
  and its halt/target decisions measure the **user's** account, not its own.
  (`driver_svc/compute.build_packet` even has a dead `source=="driver"` filter that
  can never match.)

## 3. Approach

**Chosen: a dedicated Driver paper account in its OWN DB file (System B), zero
schema change.** Rejected alternatives:
- *Tag + filter the ledger (System A)* — no repricing/auto-manage ⇒ no live P&L, no
  win/loss ⇒ can't measure performance. ✗
- *Multi-account migration in one DB (`account_id`)* — invasive: drop `CHECK(id=1)`,
  re-key `paper_positions`/`paper_orders`, touch ~15 functions. ✗

**Why the dedicated-file approach works with no migration:** every
`paper_account_db.py` and `paper_engine.py` function already takes a `db_path`
(defaulting to the shared DB only when `None`). A second file
`data/paper_account_driver.db` is a fully independent single-account store — its own
`account` row, positions, orders, session counters, halt — and the entire
open/reprice/auto-manage/snapshot machinery already accepts the path to operate on
it. The single-account `CHECK(id=1)` constraint is sidestepped entirely by using a
separate file. Moving the Driver onto this account fixes **both** the isolation goal
**and** the write/read split in one move.

## 4. Architecture

```
Claude decision → guardrails clamp (driver_svc)
   │  enqueue cmd:options  driver_paper_create {signal, qty}
   ▼
options_svc (owns ALL paper_engine imports — driver_svc must NOT import them)
   ├─ compute.open_driver_position(signal, qty)   → opens into paper_account_driver.db
   ├─ scheduler driver-manage tick (5-min)        → run_manage_cycle(db_path=driver_db)
   │        → live reprice + auto-exits + drawdown halt for the driver account
   ├─ publish cache:options:driver_paper_account   (snapshot + positions)
   └─ publish cache:options:driver_paper_perf       (the scorecard)
        ▲ read                                   │ read
driver_svc                                       │
   ├─ run_autonomous_cycle reads day-P&L from the DRIVER account view (not the
   │    shared engine account) → halt/$500 now measures the driver's OWN book
   └─ AutonomousState gains an additive `perf` field
        ▲ version-poll
/driver page → Day-P&L + Open-positions from cache:options:driver_paper_account
            + a NEW Performance scorecard card from cache:options:driver_paper_perf
```

## 5. Components

**Dedicated account.** `DRIVER_PAPER_DB = data/paper_account_driver.db` — added to
`repo_paths.py` (no hard-coded paths). Starting balance **$25,000** (the existing
`paper_reset` default; tunable). Isolated from the manual `paper_account.db`.

**`services/options_svc` (Tier 2 — owns the engine imports):**
- `compute.open_driver_position(signal, qty)` — extract the per-signal open block of
  `paper_engine.run_entry_cycle` (`paper_engine.py:169-228`: `size → paper_broker.
  submit_order (PAPER_MODE simulated fill) → re-size on fill → reserve BP →
  insert_position`) into a single-signal primitive pointed at `DRIVER_PAPER_DB`. The
  Driver supplies its own guardrail-clamped qty (cap by sizing, don't re-derive).
- `compute.run_driver_manage_cycle()` — `paper_engine.run_manage_cycle(client, today,
  db_path=DRIVER_PAPER_DB)` (reuse as-is: reprice via `signal_repricer.reprice_swing`,
  auto-exit, session roll, halt).
- `compute.driver_paper_account_view()` — `account_snapshot(db_path=DRIVER_PAPER_DB)`
  + `fetch_open_positions(db_path=DRIVER_PAPER_DB)` (same shape as
  `paper_account_view`, so the page reuses its position mapping).
- `compute.driver_paper_perf()` — a **PURE scorecard aggregator** over
  `fetch_all_positions(db_path=DRIVER_PAPER_DB)` (open + closed): # trades, open vs
  closed, **win rate**, avg win / avg loss, **profit factor**, Σrealized, Σunrealized,
  total P&L, **P&L grouped by symbol and by strategy**, best/worst trade. Modeled on
  `paper_trader.get_trade_summary` but over `paper_positions` rows.
- `handlers`: a `driver_paper_create` command → `open_driver_position` + publish the
  driver views; publish `cache:options:driver_paper_account` +
  `cache:options:driver_paper_perf`.
- `scheduler`: a driver-account manage tick mirroring `manage_due` (5-min RTH), each
  branch guarded; publishes the driver views.

**`services/driver_svc` (Tier 2 — engine-free re the paper account; only enqueues +
reads cache):**
- `handlers.run_autonomous_cycle`: enqueue `driver_paper_create` (not `paper_create`);
  read day-P&L + open positions from `cache:options:driver_paper_account` (new
  `CACHE_OPT_DRIVER_PAPER` const) instead of `cache:options:paper_account`.
- `compute.build_packet` / `_day_pnl`: source day-P&L from the driver account snapshot
  (the dead `source=="driver"` filter becomes correct-by-construction — the whole
  driver DB is the driver's, so no filtering needed).
- Read `cache:options:driver_paper_perf` and attach it to the published
  `AutonomousState` (additive `perf` field).

**`shared/contracts/driver.py`:** `AutonomousState` gains an optional additive
`perf: dict = {}` (loose, like the existing fields).

**`webgui/pages/driver.py` (Tier 1 — cache reader):**
- The monitor's Day-P&L bar + summary + Open-positions read
  `cache:options:driver_paper_account` (the driver's own book) — replacing the
  current `cache:options:paper_account` read (which showed the manual account).
- A new **Performance scorecard** card (pure builders: win rate, profit factor,
  realized/unrealized, a P&L-by-symbol and P&L-by-strategy table, best/worst) from
  `AutonomousState.perf` (or `cache:options:driver_paper_perf` directly).
- Version-poll the new view(s).

## 6. The performance scorecard (metrics)

Pure `driver_paper_perf` over the driver account's positions returns (all defensive):
- **Headline:** total trades, open / closed, **win rate** (closed wins / closed),
  Σrealized P&L, Σ open unrealized, total P&L (realized + unrealized), session/day P&L.
- **Quality:** avg win $, avg loss $, **profit factor** (Σwins / |Σlosses|), best
  trade, worst trade.
- **Breakdowns:** P&L + count + win-rate **by symbol** and **by strategy** (PCS/CCS/IC).

The page renders the headline + quality as chips and the breakdowns as small tables.

## 7. Cross-process constraint (load-bearing)

`paper_engine` transitively imports `scoring` / `signal_repricer` (options-scanner).
`driver_svc` already imports `claude-driver/config`. Importing `paper_engine` into
`driver_svc` would re-trigger the documented `config`/`scoring` cross-app
module-name collisions. **Therefore all `paper_engine`/`paper_account_db` calls stay
in `options_svc`** (which already owns them); `driver_svc` only enqueues commands and
reads cache views. This is the same isolation the existing `paper_create` path uses.

## 8. Testing

- **`driver_paper_perf`** — exhaustive pure unit tests (empty account; all-open;
  mixed open/closed; all-wins / all-losses → profit-factor div-by-zero guard;
  by-symbol/by-strategy grouping; best/worst).
- **`open_driver_position`** — against a `tmp_path` driver DB: a position lands in the
  driver DB at the clamped qty, with the right symbol/strikes; isolation verified (the
  manual `paper_account.db` is untouched).
- **`run_driver_manage_cycle`** / the scheduler tick — pure due-gate + a smoke that it
  reprices the driver DB only.
- **Cache views + handlers** — `driver_paper_create` lands a position + publishes the
  two driver views; `run_autonomous_cycle` reads day-P&L from the driver account.
- **Redis-driven e2e** — enqueue `driver_paper_create` → position in the driver DB →
  `driver_paper_account` + `driver_paper_perf` reflect it; the `/driver` monitor +
  scorecard render from seeded views.
- Run service suites **per folder** (the `config`/`src` collision rule). webgui from
  its own dir.

## 9. Scope

**v1 (this build):** isolated driver paper account (dedicated DB, $25k); driver
trades + reprices + auto-exits in it; halt/$500 reads it; the `/driver` monitor +
**full performance scorecard** read it.

**Out of scope:** the 14 historical ledger MU trades (left where they are — the driver
starts fresh in its dedicated account); migrating the user's manual trades; a
separate "reset driver account" UI (a command is enough); level-C / live execution.

## 10. Reusable vs must-build

| Capability | Status |
|---|---|
| Second isolated account (separate DB file) | **Reusable** — all fns take `db_path`; zero schema change |
| Simulated fill, risk sizing, repricer | **Reusable as-is** (`paper_broker`, `paper_sizing`, `signal_repricer`) |
| Live reprice + auto-exit + session P&L + halt + session roll | **Reusable as-is** — `run_manage_cycle(db_path=…)`, `account_snapshot(db_path=…)` |
| Closed-trade history for perf | **Reusable read** — `fetch_all_positions(db_path=…)` |
| Single-signal open primitive | **Must build (small)** — extract from `run_entry_cycle:169-228` |
| Driver-account manage tick + cache views + `driver_paper_create` | **Must build** — mirror `manage_due`; new handlers/views |
| Performance scorecard aggregator | **Must build (small pure fn)** |
| `AutonomousState.perf` + page scorecard card | **Must build** (additive contract field + pure page builders) |

Design/exploration grounded in: `options-scanner/paper_engine.py` (`run_entry_cycle`
:151, per-signal block :169-228, `run_manage_cycle` :267, `account_snapshot` :341),
`paper_account_db.py` (all fns `db_path`-parameterized; `CHECK(id=1)` :23; `ALTER`
migration pattern :118-120), `paper_trader.py`/`trades_db.py` (the ledger),
`services/options_svc/compute.py` (paper section ~104-230) + `handlers.py`
(`paper_create` :490), `services/driver_svc/compute.py` (`build_packet` :200,
`_day_pnl` :159) + `handlers.py` (`run_autonomous_cycle` :195) + `scheduler.py`,
`shared/contracts/driver.py` (`AutonomousState`).
