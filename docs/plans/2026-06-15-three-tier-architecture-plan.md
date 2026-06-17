# Three-Tier Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Re-tier the whole monorepo into three physically separate tiers — a NiceGUI
GUI, per-domain FastAPI processing services, and a Redis (Memurai) storage+communication
backbone — migrated strangler-fig one domain at a time, webgui staying green throughout.

**Architecture:** GUI reads `cache:{domain}:{view}` on page build and subscribes to
`events:{domain}:{view}` for live repaints; user actions `enqueue` onto `cmd:{domain}`
Redis Streams. Each `services/{domain}_svc` FastAPI app imports only its own engines, owns
its scheduler + command consumer, and validates→caches→publishes results. `shared/contracts/`
holds the typed payloads (the API); `shared/bus/` wraps redis-py with a fakeredis test backend.

**Tech Stack:** Python 3.11, NiceGUI ≥2.0, FastAPI + uvicorn, redis-py, fakeredis, Pydantic,
Memurai (Windows Redis), pytest. Design doc: `docs/plans/2026-06-15-three-tier-architecture-design.md`.

**Conventions for every task below:** run tests with the repo venv
(`..\.venv\Scripts\python -m pytest`). Commit after each green step. Each domain service's
tests live in `services/<svc>/tests` and run from inside that folder (same `sys.path` pattern
as existing apps). Never "fix" options-scanner's ~2 known date-relative failures.

---

## PHASE 0 — FOUNDATION (no behavior change; nothing wired to the GUI yet)

### Task 0.1: Install Memurai + add deps

**Files:**
- Modify: `requirements.txt` (or the project's dep file)

**Step 1:** Install Memurai Developer edition (native Windows service) from memurai.com;
confirm it listens on 6379: `Test-NetConnection 127.0.0.1 -Port 6379` → `TcpTestSucceeded: True`.

**Step 2:** Add `redis>=5.0`, `fakeredis>=2.20`, `pydantic>=2.0` to the dep file. Install:
`..\.venv\Scripts\python -m pip install redis fakeredis pydantic`.

**Step 3:** Verify: `..\.venv\Scripts\python -c "import redis, fakeredis, pydantic; print('ok')"`
→ prints `ok`.

**Step 4: Commit**
```bash
git add requirements.txt && git commit -m "chore: add redis/fakeredis/pydantic deps for 3-tier backbone"
```

### Task 0.2: Add ports for Memurai + services

**Files:**
- Modify: `config/ports.toml`
- Modify: `repo_paths.py:21-28`

**Step 1: Write the failing test** — `tests/test_repo_paths_ports.py` (create):
```python
def test_new_ports_exposed():
    import repo_paths as rp
    assert rp.MEMURAI_PORT == 6379
    assert rp.MEMURAI_URL == "redis://127.0.0.1:6379/0"
    assert rp.SERVICE_PORTS["sentiment"] == 8210
    assert rp.SERVICE_PORTS["options"] == 8211
    assert rp.SERVICE_PORTS["portfolio"] == 8212
    assert rp.SERVICE_PORTS["trade"] == 8213
    assert rp.SERVICE_PORTS["driver"] == 8214
```

**Step 2: Run, verify fail:** `..\.venv\Scripts\python -m pytest tests/test_repo_paths_ports.py -v`
→ FAIL (`AttributeError: MEMURAI_PORT`).

**Step 3: Implement.** Add to `config/ports.toml`:
```toml
memurai = 6379

[services]
sentiment = 8210
options   = 8211
portfolio = 8212
trade     = 8213
driver    = 8214
```
Add to `repo_paths.py` after the existing port reads:
```python
MEMURAI_PORT  = _ports["memurai"]
MEMURAI_URL   = f"redis://127.0.0.1:{MEMURAI_PORT}/0"
SERVICE_PORTS = dict(_ports["services"])
SERVICE_URLS  = {k: f"http://127.0.0.1:{v}" for k, v in SERVICE_PORTS.items()}
```

**Step 4: Run, verify pass.** Then `..\.venv\Scripts\python -c "import repo_paths"` (no import error).

**Step 5: Commit** — `feat(ports): add memurai + per-service ports`.

### Task 0.3: `shared/contracts/` — typed payloads (the tier API)

**Files:**
- Create: `shared/contracts/__init__.py`
- Create: `shared/contracts/envelope.py`
- Create: `shared/contracts/sentiment.py`
- Test: `shared/contracts/tests/test_contracts.py`

**Step 1: Write the failing test:**
```python
from shared.contracts.envelope import CacheEnvelope, Command
from shared.contracts.sentiment import CompositeSnapshot

def test_envelope_roundtrip():
    env = CacheEnvelope(version=3, ts="2026-06-15T12:00:00Z", payload={"a": 1})
    raw = env.to_json()
    assert CacheEnvelope.from_json(raw) == env

def test_command_roundtrip():
    cmd = Command(type="rescan", args={"force": True})
    assert Command.from_json(cmd.to_json()).type == "rescan"

def test_composite_rejects_malformed():
    import pytest
    with pytest.raises(Exception):
        CompositeSnapshot.from_json('{"total": "not-a-number"}')

def test_composite_roundtrip():
    snap = CompositeSnapshot(total=7.8, bias="Bullish", components={"vix_complex": 5.0})
    assert CompositeSnapshot.from_json(snap.to_json()).total == 7.8
```

**Step 2: Run, verify fail** (`ModuleNotFoundError`).

**Step 3: Implement.** `envelope.py`:
```python
from pydantic import BaseModel

class _Base(BaseModel):
    def to_json(self) -> str: return self.model_dump_json()
    @classmethod
    def from_json(cls, raw: str): return cls.model_validate_json(raw)

class CacheEnvelope(_Base):
    version: int
    ts: str
    payload: dict

class Command(_Base):
    type: str
    args: dict = {}
```
`sentiment.py`:
```python
from .envelope import _Base
class CompositeSnapshot(_Base):
    total: float
    bias: str = ""
    components: dict = {}
```
Add empty `__init__.py` files. (Add `SectorPerf`, `ScanResult` etc. lazily when their
migration step needs them — YAGNI.)

**Step 4: Run, verify pass.**

**Step 5: Commit** — `feat(contracts): cache envelope + command + composite snapshot`.

### Task 0.4: `shared/bus/` — redis-py wrapper with fakeredis test backend

**Files:**
- Create: `shared/bus/__init__.py`
- Create: `shared/bus/client.py`
- Test: `shared/bus/tests/test_bus.py`

**Step 1: Write the failing tests** (all against the in-memory backend):
```python
from shared.bus import client as bus

def test_cache_set_get_bumps_version(monkeypatch):
    b = bus.Bus(fake=True)
    v1 = b.cache_set("cache:test:x", {"n": 1})
    assert v1 == 1
    env = b.cache_get("cache:test:x")
    assert env.version == 1 and env.payload == {"n": 1}
    v2 = b.cache_set("cache:test:x", {"n": 2})
    assert v2 == 2

def test_publish_subscribe_roundtrip():
    b = bus.Bus(fake=True)
    sub = b.subscribe("events:test:x")
    b.publish("events:test:x", {"version": 5})
    msg = sub.get_message(timeout=1.0)
    assert msg["version"] == 5

def test_command_stream_enqueue_consume():
    b = bus.Bus(fake=True)
    b.enqueue_command("cmd:test", {"type": "rescan", "args": {}})
    cmds = b.consume_commands("cmd:test", group="g", consumer="c", block_ms=50)
    assert cmds[0][1].type == "rescan"
```

**Step 2: Run, verify fail.**

**Step 3: Implement `client.py`** — thin wrapper. Key points:
- `Bus(fake=False)`: `fakeredis.FakeStrictRedis()` when `fake` or `PYTEST_CURRENT_TEST` in env,
  else `redis.Redis.from_url(MEMURAI_URL)`.
- `cache_set(key, payload)`: `INCR key+":ver"`, store `CacheEnvelope(version, ts, payload)`
  JSON at `key` (ts via a passed-in clock or `datetime.now(timezone.utc)` — wrapped so tests
  can stub), return version.
- `cache_get(key)`: read + `CacheEnvelope.from_json`, or `None`.
- `publish(channel, dict)`: `redis.publish(channel, json.dumps(dict))`.
- `subscribe(channel)`: return a pubsub object with a `.get_message(timeout)` that parses JSON.
- `enqueue_command(stream, dict)`: `XADD stream * data <Command json>`.
- `consume_commands(stream, group, consumer, block_ms)`: ensure group (`XGROUP CREATE … MKSTREAM`,
  ignore BUSYGROUP), `XREADGROUP`, return `[(id, Command.from_json(...))]`; expose `ack(stream, group, id)`.

**Step 4: Run, verify pass.**

**Step 5: Commit** — `feat(bus): redis-py wrapper with fakeredis backend (cache/pubsub/streams)`.

### Task 0.5: Service scaffold helper (shared by every service)

**Files:**
- Create: `services/__init__.py`
- Create: `services/_scaffold.py`
- Test: `services/tests/test_scaffold.py`

**Step 1: Write failing test** — a `make_app(domain, scheduler_fn, handlers)` returns a FastAPI
app with a `/health` route returning `{"domain": ..., "up": True}`; uses `fastapi.testclient`.

**Step 2–4:** Implement `make_app` (FastAPI app + `/health`; `@app.on_event("startup")` spawns
the scheduler loop + the command-consumer loop as asyncio tasks; both wrapped so they never die).
Verify with `TestClient`.

**Step 5: Commit** — `feat(services): shared FastAPI scaffold (health + scheduler/consumer hooks)`.

---

## PHASE 1 — REFERENCE MIGRATION: SENTIMENT

> This is the pattern every later domain copies. Do it carefully and fully.

### Task 1.1: Sentiment compute module (engine-only, no NiceGUI, no _CACHE)

**Files:**
- Create: `services/sentiment_svc/__init__.py`
- Create: `services/sentiment_svc/compute.py`
- Test: `services/sentiment_svc/tests/test_compute.py`

**Step 1:** Move the engine-call functions out of `webgui/pages/sentiment.py` into `compute.py`:
`_load_live` → `load_live()`, `_load_snapshots` → `load_snapshots()`, `_load_sector_perf`,
`_load_industries`, `build_and_write_bridge`, plus the pure series helpers they call
(`composite_series`, `commit_trend_regime`). Keep the `sys.path.insert(SENTIMENT)` + the eager
`import live_composite` / `from scoring import …` at the top (these are now isolated in the
sentiment process — note in a comment that the `scoring` collision can no longer occur here).

**Step 2: Write a test** with a fake schwab client (monkeypatched `proxy`) asserting
`load_live()` returns a dict and never raises on a thrown client (returns `None`), mirroring the
current defensive behavior.

**Step 3:** Implement (mostly mechanical move).

**Step 4: Run** `cd services/sentiment_svc && ..\..\.venv\Scripts\python -m pytest -v`.

**Step 5: Commit** — `feat(sentiment_svc): compute module (engines moved out of webgui page)`.

### Task 1.2: Sentiment handler — compute→validate→cache→publish (+bridge dual-write)

**Files:**
- Create: `services/sentiment_svc/handlers.py`
- Test: `services/sentiment_svc/tests/test_handlers.py`

**Step 1: Write the failing test** (fakeredis Bus + monkeypatched `compute`):
```python
def test_refresh_publishes_composite(monkeypatch):
    from services.sentiment_svc import handlers
    b = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "load_live", lambda: {"total": 7.8, "components": {...}})
    monkeypatch.setattr(handlers.compute, "load_snapshots", lambda: ([...], [...]))
    handlers.refresh(b, with_sectors=False)
    env = b.cache_get("cache:sentiment:composite")
    assert env.version == 1
    assert CompositeSnapshot.from_json(json.dumps(env.payload)).total == 7.8
    # and an event was published on events:sentiment:composite
```

**Step 2: Run, verify fail.**

**Step 3: Implement `refresh(bus, with_sectors)`:** call `compute.load_snapshots()` +
`compute.load_live()` (+ `load_sector_perf` when `with_sectors`), build a `CompositeSnapshot`
(+ history payload), `bus.cache_set("cache:sentiment:composite", …)`,
`bus.cache_set("cache:sentiment:history", …)`, `bus.publish("events:sentiment:composite",
{"version": v})`, and **also** `compute.build_and_write_bridge(...)` (dual-write the legacy file
so `regime_filter` keeps working). Add a `handle_command(bus, cmd)` that maps `type=="refresh"`
→ `refresh(bus, with_sectors=True)`.

**Step 4: Run, verify pass.**

**Step 5: Commit** — `feat(sentiment_svc): refresh handler caches+publishes+dual-writes bridge`.

### Task 1.3: Sentiment scheduler + app

**Files:**
- Create: `services/sentiment_svc/scheduler.py`
- Create: `services/sentiment_svc/app.py`
- Test: `services/sentiment_svc/tests/test_app.py`

**Step 1: Write test** — `TestClient(app).get("/health")` → 200 `{"domain":"sentiment","up":True}`.

**Step 2–3:** `scheduler.py`: `async def loop(bus)` = `refresh(with_sectors=True)` once then
every 120 s `refresh(with_sectors=False)` (port `_bg_loop` cadence). `app.py`: build via
`_scaffold.make_app("sentiment", scheduler_loop, command_handler)` with a real `Bus()`; expose
`if __name__=="__main__": uvicorn.run(app, port=SERVICE_PORTS["sentiment"])`.

**Step 4: Run, verify pass.**

**Step 5: Commit** — `feat(sentiment_svc): scheduler (120s) + FastAPI app on :8210`.

### Task 1.4: GUI bus client

**Files:**
- Create: `webgui/bus_client.py`
- Test: `webgui/tests/test_bus_client.py`

**Step 1: Write failing test** — `read("sentiment:composite")` returns the cached payload dict
(or `None`); `request("sentiment", {"type":"refresh"})` enqueues a Command; `on_event(channel, cb)`
registers a subscriber that fires `cb(version)` on publish (drive it with a fakeredis Bus).

**Step 2–3:** Implement a thin module holding a process-wide `Bus()`; `read(view)` →
`cache_get(f"cache:{view}")`; `request(domain, cmd)` → `enqueue_command(f"cmd:{domain}", cmd)`;
`on_event(channel, cb)` → background subscriber that calls `cb` with the new version. Single-user,
one Bus per webgui process.

**Step 4: Run, verify pass.**

**Step 5: Commit** — `feat(webgui): bus_client (cache read / command enqueue / event subscribe)`.

### Task 1.5: Cut the Sentiment page over to the bus

**Files:**
- Modify: `webgui/pages/sentiment.py` (remove `_CACHE`, `_refresh_cache_sync`, `refresh_cache`,
  `_bg_loop`, `start_background_refresh`, `_load_*`, `build_and_write_bridge`; keep ALL pure
  transforms + `render()` widget wiring)
- Modify: `webgui/main.py` (drop `sentiment.start_background_refresh` from `@app.on_startup`)
- Test: existing `webgui/tests/test_sentiment*.py` (adjust only imports that moved)

**Step 1:** In `render()`, replace the `_CACHE` reads with `bus_client.read("sentiment:composite")`
/ `read("sentiment:history")` / `read("sentiment:sectors")` for the instant paint; replace the
version-poll `ui.timer` with `bus_client.on_event("events:sentiment:composite", lambda v: repaint())`
(keep a cheap fetch-free `ui.timer` fallback that repaints if the cached version changed); wire
the **Refresh** button to `bus_client.request("sentiment", {"type":"refresh"})`.

**Step 2:** Move the pure-transform tests that still pass as-is; update any test importing a moved
function to import it from `services.sentiment_svc.compute`. Run the full webgui suite:
`cd webgui && ..\.venv\Scripts\python -m pytest -q` → expect green (≈127, minus moved-fn tests now
living under the service).

**Step 3: Verify in browser (Claude Preview).** Start Memurai → proxy → `python
services/sentiment_svc/app.py` → `python webgui/main.py`. Open `/sentiment`; confirm the headline
matches the documented legacy parity (82% green | Cap-wtd +0.70% | Score 7.8/10). Trigger Refresh;
confirm the page repaints via the event with no manual reload. Confirm `shared/sentiment_bridge.json`
still updates and `regime_filter.evaluate_regime()` reads it.

**Step 4: Commit** — `refactor(sentiment): GUI reads Redis + subscribes; engines now in sentiment_svc`.

### Task 1.6: Launcher + run-order docs

**Files:**
- Modify: the `start_all` launcher; `CLAUDE.md` "Running" section
- Modify: `config/launch.json` if a service dev entry is wanted

**Steps:** Add Memurai-check → proxy → `sentiment_svc` → webgui to `start_all`; document the new
run order. Commit — `chore(launchers): start sentiment_svc between proxy and webgui`.

---

## PHASE 2–5 — REMAINING DOMAINS (repeat the Phase-1 reference pattern)

> Each domain repeats Tasks 1.1–1.6 with the same TDD cadence (compute → handler → scheduler/app →
> cut page over → verify → launcher). Only the domain-specific deltas are listed. Write the
> domain's contract additions (`ScanResult`, `SectorPerf`, `Positions`, …) in `shared/contracts/`
> as the first task of each phase, TDD'd exactly like Task 0.3.

### PHASE 2 — Options (`services/options_svc`, :8211) — highest payoff
- **Engines:** `run_full_scan` (scan + swing). **Scheduler:** port `autoscan_due` / `_market_now`
  / `_is_trading_day` / `_is_market_hours` / `_HOLIDAYS` + the 15-min 08:00–15:15 CT window from
  `scanner.py` verbatim into `options_svc/scheduler.py`.
- **DELETE, do not port:** the `options_scoring()` context manager and `pages/options/engines.py`
  collision guard — the separate process makes `import scoring` unambiguous. Note this in the
  commit body so it isn't reflexively reintroduced.
- **Cache views:** `cache:options:scan`, `cache:options:swing`. **Command:** `{"type":"rescan"}`
  from the "Run scan" button.
- **Page cut-over:** `webgui/pages/options/scanner.py` loses `_LAST_RESULTS`, `_autoscan_loop`,
  `start_autoscan`, the engine import + `sys.path.insert(OPTIONS_SCANNER)`; keeps `signal_columns`,
  `signal_rows`, `_scan_meta_strip`, `_populate`, `render`. `main.py` drops `scanner.start_autoscan`.
- **Also re-home** paper/captured/portfolio/calculator/swing/gamma/simulator reads to
  `cache:options:*` as their data needs dictate (most are on-demand → command-driven).
- **Verify:** scan counts match a direct engine run; auto-scan fires once per 15-min slot in-window.

### PHASE 3 — Portfolio (`services/portfolio_svc`, :8212) — builds the stub page natively
- **Engines:** `portfolio-analyzer/src` (sector breakdown, vs-sector perf, **live streaming**).
- **Scheduler:** poll the proxy stream; publish `cache:portfolio:positions` + event on each tick
  (replaces the planned GUI-side `ui.timer` stream polling).
- **Page:** build `/portfolio` (currently a stub) directly on `bus_client` subscribe → repaint.

### PHASE 4 — Trade (`services/trade_svc`, :8213) — stub page, born 3-tier
- **Engines:** `trade-analyzer/src/analysis` (MTF + verdicts + fundamentals). Mostly on-demand:
  symbol entry → `{"type":"analyze","args":{"symbol":"AAPL"}}` command → `cache:trade:{symbol}` +
  event. Watch for the `notifier` module-name clash — isolated by the separate process, but verify.

### PHASE 5 — Driver (`services/driver_svc`, :8214) — stub page, born 3-tier — **DONE (2026-06-16)**
- **Engines:** `claude-driver/approval_server.py` + `morning_agent.py`. The order-approval queue is
  a natural Redis Streams flow: driver publishes pending orders to `cache:driver:approvals` + event;
  GUI approve/reject buttons enqueue `cmd:driver` commands the consumer acts on.
- **Built as:** contracts `ApprovalState`/`PerfReport` (`shared/contracts/driver.py`); `driver_svc/compute`
  ports `morning_agent.run_morning_agent`'s orchestration minus its file-write/HTTP-post side effects
  (`run_morning`/`execute`/`build_perf_report`, all defensive); `driver_svc/handlers` (`run`/`approve`/
  `skip`/`perf` → `cache:driver:approvals` + `cache:driver:performance`); `driver_svc/scheduler`
  (`morning_due` once/day at 09:28 ET — never executes orders); `driver_svc/app`
  (`make_app("driver", scheduler, command_handler)`); page `webgui/pages/driver.py` (approval queue
  w/ APPROVE confirm-dialog / SKIP + Performance view). `order_executor` runs with `config.PAPER_TRADE=True`
  → simulated. Tests: `services/driver_svc/tests` (26) + `webgui/tests/test_driver.py`. Note: run service
  suites **per folder** — importing driver_svc + trade_svc engines in one process re-triggers the `config`
  module-name collision.

---

## PHASE 6 — RETIRE SHIMS

### Task 6.1: `regime_filter` reads Redis
- **Modify:** `options-scanner/regime_filter.py` to read `cache:sentiment:composite` via a tiny
  `shared/bus` read (fallback to the JSON file if Redis is down). TDD against fakeredis.
- **Then** drop the `build_and_write_bridge` dual-write from `sentiment_svc/handlers.py`; retire
  `shared/sentiment_bridge.json` (keep the `.example.json` template).

### Task 6.2: Remove residual webgui engine-glue
- Grep `webgui/` for any remaining `sys.path.insert(... SCANNER/SENTIMENT/...)` and engine imports;
  confirm none remain. `webgui` should import only `nicegui`, `shared.bus`, `shared.contracts`.
- **Commit** — `chore: webgui fully decoupled from app engines (3-tier complete)`.

### Task 6.3: Final docs pass
- Update `CLAUDE.md`: flip "Planned 3-tier architecture" to current; update the Routes table
  (Portfolio/Trade/Driver now built); refresh the test counts and run order. Commit.

---

## Done-when
- `python webgui/main.py` runs with zero app-engine imports; killing a service degrades only that
  page (GUI never crashes on an engine exception).
- Each domain's scheduler/command paths are fakeredis-tested; one `@pytest.mark.live_redis` test per
  service passes against Memurai locally.
- Browser parity verified per domain against live proxy; `sentiment_bridge.json` retired and
  `regime_filter` reads Redis.
