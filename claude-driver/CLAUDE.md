# claude-driver — CLAUDE.md

> Cross-app paths and service ports come from the root `repo_paths.py`
> (which reads `config/ports.toml`). Never hard-code `D:\` paths or ports —
> import them. See the root `CLAUDE.md` for the monorepo overview.

## Purpose

Orchestration agent for the morning/intraday trading workflow. It grades the
day, selects trades, surfaces them for human approval, and (on approval) routes
orders. It pulls market data and analytics from the proxy and external services
and presents a browser-based approval UI.

## Entry points & ports

- `morning_agent.py` — day grader + trade selector (scheduled ~9:28am ET).
- `approval_server.py` — browser APPROVE/SKIP approval UI on
  `http://127.0.0.1:8300` (`APPROVAL_PORT` from `repo_paths.py`).
- `intraday_monitor.py` — intraday monitoring loop.
- `start_all.bat` — this folder's own launcher (note: the **repo-root**
  `start_all.bat` is the orchestrator that starts the proxy first, then this).

## Key files

| File                  | Role                                              |
|-----------------------|---------------------------------------------------|
| `morning_agent.py`    | Morning grader + trade selector.                  |
| `approval_server.py`  | Approval UI (port 8300).                           |
| `intraday_monitor.py` | Intraday monitoring.                              |
| `trade_selector.py`   | Trade selection logic.                            |
| `order_executor.py`   | Order routing.                                    |
| `feature_engineer.py` | Builds features for the ML servers.               |
| `config.py`           | App config.                                       |

## Services it talks to

- **schwab-proxy** on :8100 (`PROXY_URL`) — market data. Must be running first.
- **Options analytics** on :8200 (`ANALYTICS_URL`) — GEX/DEX/Charm snapshots.
- **ML prediction servers** — MES 8000 / MNQ 8001 / ES 8004 / NQ 8005
  (`ML_SERVER_URLS`).

The analytics service (8200) and the ML servers are **external/separate
processes** — they are not part of this repo and are not started by it.

## Dependency on the proxy

claude-driver fetches all Schwab market data through the proxy at :8100, so the
proxy must be up before running the morning agent or monitors.

## Tests

```powershell
cd claude-driver && python -m pytest .
```
