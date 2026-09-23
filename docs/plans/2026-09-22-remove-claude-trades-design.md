# Remove Claude Trades (the autonomous driver)

2026-09-22. Operator request: remove the Claude AI section from the app
completely. Scope chosen: **everything** - page, service, paper-book machinery,
config, and every reference.

## State before removal

On prod the driver was **armed** (`cache:driver:control` enabled, not halted), with
0 open positions and $12,794 equity in its isolated book. It was **disarmed first**
(a `disable` command on `cmd:driver`), before any code changed, so nothing could
trade while the removal was in flight.

## What was removed

| Layer | Removed |
|---|---|
| Service | `services/driver_svc/` (decider, guardrails, scheduler, handlers, settings), its port 8214 in `config/ports.toml`, its CI job, and so its generated systemd unit |
| Shared | `shared/driver_limits.py`, `shared/driver_policy.py` (its one shared function, `open_risk_dollars`, moved into `shared/book_caps.py`), `shared/contracts/driver.py` |
| Config | `config/driver.toml`; `[windows.driver_entry]` in `config/sessions.toml` (the `tz` / `end_exclusive` window mechanism stays, tested on a synthetic window); the `autonomous_trading` environment flag |
| options_svc | the driver paper book: `open_driver_position`, `run_driver_manage_cycle`, the account/perf/analytics views (`cache:options:driver_paper_*`), the `driver_paper_create/manage/reset` commands, the 1-minute manage tick, the driver's EOD-push book and buying-power reconcile; `perf_analytics.posture_postmortem` (it only ever read driver trades). `driver_perf.py` is now `book_perf.py` - the manual book's scorecard uses it |
| webgui | the `/driver` page and its rail item + AI pill; the CLAUDE book on the Desk; the driver book on the Symbol page, the EOD report and System Status; the "Autonomous driver" and "Driver entry window" sections in Settings -> Configuration; the scorecard builders only that page drew |
| Tools | the dev snapshot's control-key disarm and its copy of the driver DB |

## Kept deliberately

- `options-scanner/data/paper_account_driver.db` - the driver's trade history. Not
  read by anything; still swept by the nightly backup (`*.db`).
- The `entry_context` column on `paper_positions` - a schema column, only the
  driver wrote it; dropping it would be a migration for no benefit.
- `anthropic` in requirements - options_svc still calls Claude (Gamma Analyze).
- Stale Redis keys (`cache:driver:*`, `cache:options:driver_paper_*`) - nothing
  reads them.
- `/driver` stays in the public origin's FORBIDDEN route lists in tests - a guard
  that the route can never appear there.

## Prod steps beyond promote.sh

`generate_units --install` writes units but never deletes one, so the old
`trading-prod-driver_svc.service` file is left behind. The regenerated target no
longer names it, so it does not start, but the stale file is removed by hand
(`rm` + `daemon-reload`) after the promote.
