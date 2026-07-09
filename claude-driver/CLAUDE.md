# claude-driver — CLAUDE.md

> Cross-app paths and service ports come from the root `repo_paths.py`
> (which reads `config/ports.toml`). Never hard-code `D:\` paths or ports —
> import them. See the root `CLAUDE.md` for the monorepo overview.

## Status — morning-agent / approval subsystem REMOVED (2026-07-08)

The legacy **morning-agent + browser order-approval queue** that used to live here
was **deleted** (`morning_agent.py`, `approval_server.py`, `intraday_monitor.py`,
`trade_selector.py`, `order_executor.py`, `order_preview.py`, `perf_report.py`,
this folder's `start_all.bat`, and their tests). Its role — autonomously selecting +
sizing defined-risk option credit spreads toward a daily target — was superseded by
the **3-tier autonomous Driver** in `services/driver_svc` (a Claude decision layer +
pure `guardrails.py`, publishing to the `/driver` page over Redis). See the root
`CLAUDE.md` "Autonomous Driver" sections.

## What remains here

This folder is now a small support library, **not** a runnable agent:

| File                  | Role                                              |
|-----------------------|---------------------------------------------------|
| `config.py`           | App config incl. `RISK_LIMITS` — still imported by `services/driver_svc/compute.py` for the daily-loss-halt fallback. **Do not delete.** |
| `feature_engineer.py` | Builds features for the external ML prediction servers — used by the ML/diagnostic scripts, unrelated to the removed morning agent. |
| ML / diagnostic scripts + tests | Standalone utilities for the external ML servers (MES/MNQ/ES/NQ). |

There is **no entry point / port** in this folder anymore — nothing here is started
by `start_all.bat`. The proxy (:8100) and the external ML servers
(MES 8000 / MNQ 8001 / ES 8004 / NQ 8005, `ML_SERVER_URLS`) are separate processes
not started by this repo.

## Tests

```powershell
cd claude-driver && python -m pytest .
```
