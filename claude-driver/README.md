# claude-driver (legacy support library)

> **The morning-agent + browser approval workflow that this folder used to host was
> removed on 2026-07-08.** Its job — autonomously selecting, sizing, and managing
> defined-risk option credit spreads toward a daily target — now lives in the 3-tier
> **`services/driver_svc`** (a Claude decision layer + pure `guardrails.py`, surfaced
> on the NiceGUI `/driver` page over Redis). See the root `CLAUDE.md`
> "Autonomous Driver" sections.

## What's left here

This folder is no longer a runnable agent — it's a small support library:

| File | Role |
|---|---|
| `config.py` | Parameters incl. `RISK_LIMITS`; still imported by `services/driver_svc` for the daily-loss-halt fallback. **Do not delete.** |
| `feature_engineer.py` | Feature builder for the external ML prediction servers (unrelated to the removed agent). |
| ML / diagnostic scripts + tests | Standalone utilities for the external ML servers. |

There is no entry point, port, `start_all.bat`, or approval UI here anymore.

## Dependencies (for the ML scripts)

- Schwab proxy on port 8100 (this repo's `schwab-proxy/`, via `repo_paths.PROXY_URL`).
- External ML prediction servers — MES 8000 / MNQ 8001 / ES 8004 / NQ 8005
  (`ML_SERVER_URLS`); separate processes, not started by this repo.

## Tests

```powershell
cd claude-driver && python -m pytest .
```
