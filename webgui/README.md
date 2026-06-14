# webgui — NiceGUI front-end

Single multi-page NiceGUI app for the Schwab trading stack. Replaces the old
per-app UIs (Dash / React / Tk) with one Python web app served on **:8500**.

## Layout

| File            | Role                                                              |
|-----------------|------------------------------------------------------------------|
| `main.py`       | NiceGUI server + nav shell (header, left-nav, proxy-down banner). |
| `proxy.py`      | Thin wrapper over `schwab-proxy/proxy_client.py` + `health()`.    |
| `pages/`        | One module per feature page (added in Phase 3).                   |
| `conftest.py`   | Pytest `sys.path` glue (repo root + this folder).                 |
| `tests/`        | Smoke tests (pages import + register without a live proxy).       |

Pages: Options (`/`), Sentiment (`/sentiment`), Trade (`/trade`),
Portfolio (`/portfolio`), Driver (`/driver`).

## Running

The **schwab-proxy must be started first** — every page resolves its Schwab
client + market data through `http://127.0.0.1:8100`. If it is down, the app
still loads and shows a banner on every page.

```powershell
# one-shot: starts proxy (new window), waits for :8100, then the GUI
.\start_all.bat

# or manually
.\.venv\Scripts\Activate.ps1
python schwab-proxy\schwab_proxy.py     # terminal 1
python webgui\main.py                    # terminal 2  -> http://127.0.0.1:8500
```

Port comes from `config/ports.toml` (`nicegui = 8500`) via
`repo_paths.NICEGUI_PORT` — never hard-coded.

## Tests

```powershell
cd webgui
..\.venv\Scripts\python -m pytest -q
```
