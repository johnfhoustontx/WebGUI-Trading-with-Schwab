# WebGUI Trading with Schwab

A self-contained **NiceGUI web app** for a personal Schwab options-trading stack:
GEX/options scanning, dealer-gamma analytics, a multi-strategy calculator/simulator,
market-sentiment scoring, a trade analyzer, portfolio analytics, and an autonomous
(paper) trade "driver" backed by Claude. Single-user, localhost, Windows-first.

> **Working with the code?** Read [`CLAUDE.md`](CLAUDE.md) first — it is the living
> architecture/decision record (per-feature deep-dives, gotchas, and the running
> change log). This README is the short onboarding/run guide.

---

## Architecture (3-tier)

```
TIER 1  webgui/ (NiceGUI, :8500)            render-only; reads Redis, enqueues commands
   ▲ cache read / subscribe   │ commands
TIER 3  Memurai/Redis (:6379)               cache:{domain}:{view} + events pub/sub + cmd:{domain} streams
   ▲ publish                  │ consume     shared/contracts (typed payloads) + shared/bus (redis wrapper)
TIER 2  services/{domain}_svc (:8210–8214)  FastAPI; own scheduler + command consumer; call the proxy
   │
schwab-proxy (:8100)                        owns Schwab auth/tokens + market data — START FIRST
```

- **Tier 1** imports only `nicegui` + `shared.bus` + `shared.contracts` (enforced by tests).
- **Tier 2** services each import only their own engines (`options-scanner/`,
  `sentiment-dashboard/`, `trade-analyzer/`, `portfolio-analyzer/`, `claude-driver/`),
  in separate OS processes so top-level module-name collisions can't occur.
- **The proxy must run first** — everything reads market data through `http://127.0.0.1:8100`.

Ports are single-sourced in [`config/ports.toml`](config/ports.toml) (via `repo_paths.py`);
never hard-code ports/paths.

## Requirements

- **Python 3.11+** (Windows-first: uses `winotify`, tkinter, and a local **Memurai** Redis service).
- **Memurai** (Redis for Windows) running on `:6379`.
- A Schwab developer app + OAuth tokens (see `shared/*.example.*` templates).
- An `ANTHROPIC_API_KEY` (env or `shared/anthropic_key.txt`) for the driver + Gamma Analyze.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock   # exact, tested versions
# (or requirements.txt for the loose direct-deps; requirements-dev.txt for tooling)
```

Copy the secret templates and fill in real values (all gitignored):
`shared/appsettings.example.json` → `appsettings.json`, `tokens.example.json` → `tokens.json`.

## Running

Easiest — launch the whole stack (Memurai check → proxy → 5 services → web GUI):

```powershell
start_all_wt.bat     # 7 tabs in one Windows Terminal window (live logs)
# or start_all.bat   # 7 separate console windows
```

Then open http://127.0.0.1:8500. Stop with `stop_all.bat` (or the in-app **More → Terminate**
page). Memurai is left running (it's a shared Windows service).

Manual order: Memurai → `schwab-proxy\schwab_proxy.py` → `services\*_svc\app.py` (×5) → `webgui\main.py`.

## Testing

Tests run **per app folder** (running the whole tree at once re-triggers cross-app
module-name collisions):

```powershell
.venv\Scripts\python -m pytest services\options_svc     # etc. — one folder at a time
cd webgui        ; ..\.venv\Scripts\python -m pytest
cd options-scanner ; ..\.venv\Scripts\python -m pytest tests
```

CI (`.github/workflows/ci.yml`) runs the per-folder matrix + `ruff` + `pip-audit` on every push.
Lint locally: `uvx ruff check .` (config in `ruff.toml`); install hooks with `pre-commit install`.

## Repo layout

| Path | Role |
|---|---|
| `webgui/` | Tier-1 NiceGUI app (:8500) |
| `services/` | Tier-2 domain services + shared `_scaffold.py` |
| `shared/` | `bus/` (Redis wrapper), `contracts/` (Pydantic payloads), `analysis_lib/` |
| `schwab-proxy/` | Schwab API gateway / token manager (:8100) |
| `options-scanner/`, `sentiment-dashboard/`, `trade-analyzer/`, `portfolio-analyzer/`, `claude-driver/` | copied engines (Tier-2 imports only) |
| `config/`, `repo_paths.py` | single-source ports/paths/commission rates |
| `docs/plans/`, `docs/audits/` | design docs (de-facto ADRs) + audit reports |

## Security & license

Single-user localhost tool — see [`SECURITY.md`](SECURITY.md) for the threat model and
secret handling. **Proprietary / all rights reserved** ([`LICENSE`](LICENSE)); bundles
Highcharts under a personal, **non-commercial** license — do not deploy commercially.
