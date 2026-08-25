# tools/ — cross-app operational utilities

Standalone CLIs for the *Trading With Schwab* monorepo. Each imports paths and
ports from the root `repo_paths.py` — no hard-coded `D:\` paths or port numbers.

## `check_env.py` — is everything up?

```powershell
python tools\check_env.py
```

Hybrid validator. Exit code `0` only if every *required* app is healthy.

| App | How it's checked | Required |
|-----|------------------|----------|
| schwab-proxy | HTTP `GET {PROXY_URL}/health` (reports token state) | yes |
| options-scanner | process scan for `dashboard.py` | yes |
| sentiment-dashboard | process scan + market-aware bridge freshness | yes |
| trade-analyzer | process scan for `trade_analyzer.py` | no (on-demand) |

States: `UP` / `IDLE` / `DOWN` / `STALE` / `NO TOKEN`. `IDLE` and `UP` are
healthy (exit 0).

**Market awareness:** the sentiment dashboard only republishes its bridge on
NYSE trading days within market hours (08:00–16:00 CT, mirroring the dashboard's
`AUTO_FETCH_START/END_HOUR` and reusing `shared.market_calendar.is_trading_day`).
Outside that window — weekends, holidays, overnight — a stale bridge is expected,
so the check reports `IDLE` (healthy) rather than `STALE`. It only flags genuine
`STALE` when the bridge is old *during* the hours it should be publishing.

## `db_admin.py` — option-trade DB maintenance

Targets `options-scanner/data/`: `trades.db`, `trade_performance.db`, `signals.db`.

**Interactive menu** — run with no subcommand:

```powershell
python tools\db_admin.py            # or: python tools\db_admin.py menu
```

```
=== Option-Trade DB Admin ===
  scope: all (trades.db, trade_performance.db, signals.db)
  1. Status      - row counts, sizes, date ranges (read-only)
  2. Integrity   - PRAGMA integrity_check per DB
  3. Backup      - timestamped copy of each DB
  4. Vacuum      - reclaim space after deletes
  5. Reset       - FULL WIPE + reinit (auto-backup, confirm)
  d. Change DB scope
  q. Quit
```

**Direct subcommands** (scriptable):

```powershell
python tools\db_admin.py status              # row counts, sizes, date ranges
python tools\db_admin.py reset               # full wipe + reinit (auto-backup, confirm)
python tools\db_admin.py reset --db trades   # just one DB
python tools\db_admin.py backup              # timestamped copy of each DB
python tools\db_admin.py vacuum              # reclaim space
python tools\db_admin.py integrity           # PRAGMA integrity_check
```

**Reset for a new month** (e.g. starting June clean): `python tools\db_admin.py reset`.
It wipes each DB and recreates an empty schema using each app's *own* init
function (schema never duplicated here). Safety rails:

1. Auto-backup to `data/backups/<db>.<UTC-stamp>.db` first (skip with `--no-backup`).
2. Refuses to run while the scanner/proxy are alive — they hold WAL locks and
   would re-create rows (override with `--force`).
3. Typed `reset` confirmation prompt (skip with `--yes`).

Restore = copy a backup file back by hand.

## `signal_calibration.py` — does the composite score predict?

Read-only (`mode=ro`) join of `signals.entry_grade` / `entry_score` to
`signal_outcomes.realized_pnl`, reported in **R** (P&L over dollars risked) so
spreads of different widths are comparable.

It exists because every probability the app displays is extracted from the
option's own price, which makes `EV = p·b − (1−p)` ≈ 0 by construction. Realized
outcomes are the only independent source of `p` this repo holds.

```powershell
.venv\Scripts\python tools\signal_calibration.py --by entry_score --min-n 15
.venv\Scripts\python tools\signal_calibration.py --by entry_score --split scanner_type --min-n 15
.venv\Scripts\python tools\signal_calibration.py --by exit_reason --exclude-reason MANUAL_CLOSE
```

`--by` takes `entry_grade` · `entry_score` (binned) · `scanner_type` ·
`strategy` · `symbol` · `exit_reason` · `dte_at_entry`. `--since YYYY-MM-DD` and
`--exclude-reason` narrow the population; `--db` points at another checkout.

**`--split KEY` runs the whole `--by` breakdown separately within each value of
KEY, and `--min-n` then applies within a split rather than across it.** This is
not cosmetic: measured 2026-08-25, the pooled report showed no edge below score
60, and splitting on `scanner_type` showed that was entirely a swing effect —
0-DTE's `55-60` bucket reads `+0.257R` at `tDay 2.37` while swing's reads
`+0.059R` at `tDay -0.89`. Pooling two populations reports the average of a gate
that only one of them has.

⚠ **Read `tDay`, not `t`.** One scan emits a dozen correlated signals onto one
tape, so the naive t-stat counts them as a dozen independent bets and overstates
significance by roughly √(rows/days) — measured on prod, 793 rows span 49 entry
days. The bottom score bucket reads `t +2.45` and `tDay −0.88`: the naive column
alone would have reported edge where there is none.

## Tests

```powershell
cd tools && python -m pytest tests
```
