# claude-driver (legacy config only)

> **The morning-agent + browser approval workflow that this folder used to host was
> removed on 2026-07-08.** Its job — autonomously selecting, sizing, and managing
> defined-risk option credit spreads toward a daily target — now lives in the 3-tier
> **`services/driver_svc`** (a Claude decision layer + pure `guardrails.py`, surfaced
> on the NiceGUI `/driver` page over Redis). See the root `CLAUDE.md`
> "Autonomous Driver" sections.

## What's left here

One file that still matters:

| File | Role |
|---|---|
| `config.py` | Parameters incl. `RISK_LIMITS`; still imported by `services/driver_svc` for the daily-loss-halt fallback. **Do not delete.** |

There is no entry point, port, approval UI or test suite here. The ML-server
feature builder and diagnostic scripts that served the morning agent were removed
on 2026-09-11 — nothing in this repo talks to the external ML prediction servers.
