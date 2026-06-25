# Driver Isolated Paper Account + Performance Scorecard — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give the autonomous Driver its own isolated paper account (a dedicated `paper_account_driver.db`) with live P&L, auto-exits, and a full performance scorecard — so its trades and standalone performance are tracked separately from the user's manual paper trades, and its $500/halt logic measures its own book.

**Architecture:** Reuse the `db_path`-parameterized `paper_engine`/`paper_account_db` (System B — real broker sim with repricing + auto-manage + session P&L) against a **second DB file**, zero schema change. All `paper_engine` calls stay in `options_svc` (which already owns them; `driver_svc` must not import them — cross-app `scoring`/`config` collisions). The Driver enqueues a new `driver_paper_create` command and reads new `cache:options:driver_paper_account` + `cache:options:driver_paper_perf` views.

**Tech Stack:** Python 3.11, `shared.bus` (Redis/Memurai, fakeredis under pytest), `shared.contracts`, NiceGUI, pytest. `options-scanner` engines imported standalone in `options_svc`'s process.

**Design doc:** [2025... see 2026-06-25-driver-isolated-paper-account-design.md](2026-06-25-driver-isolated-paper-account-design.md)

**Relevant skills:** @superpowers:test-driven-development · @superpowers:executing-plans · @superpowers:verification-before-completion

---

## Conventions

- **Run service tests per folder** from the repo root: `.venv\Scripts\python -m pytest services\options_svc` / `services\driver_svc` / `shared\contracts`. NEVER `pytest services` (cross-app `config`/`scoring`/`src` collisions). webgui: `cd webgui && ..\.venv\Scripts\python -m pytest -q`.
- **Branch:** `Using_Highcharts`. **Paper only** (`config_paper.PAPER_MODE=True`) — never live.
- After each task: run the named tests, see them pass, commit. Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **The driver account is a SECOND DB FILE** (`OPTIONS_SCANNER/data/paper_account_driver.db`). Every `paper_account_db`/`paper_engine` function already takes `db_path` — thread `DRIVER_PAPER_DB` through new driver-scoped wrappers; do NOT add `db_path` to the existing options_svc wrappers (would risk the manual account).

### Verified facts (from the engine extraction)
- `paper_engine.run_entry_cycle(client, now_date, signals, broker=None, db_path=None)` — the per-signal open block (`paper_engine.py:169-228`) sizes via `paper_sizing.size_contracts`, submits via `paper_broker.submit_order` (PAPER_MODE simulated fill), **re-sizes off the actual fill**, reserves BP, `insert_position`.
- `paper_engine.run_manage_cycle(client, now_date, broker=None, db_path=None)` — reprices + auto-exits + session-halt. Reusable as-is with `db_path`.
- `paper_engine.account_snapshot(db_path=None)` → `{equity, cash, buying_power_reserved, open_unrealized, session_pnl, realized_pnl, open_count, halted}`. **`session_pnl` = the driver's day-P&L.**
- `paper_account_db`: `ensure_account(db_path, starting_balance=25000.0, session_date=None)`, `reset_account(db_path, starting_balance, session_date)`, `get_account(db_path)` (None if un-seeded), `fetch_open_positions(db_path)`, `fetch_all_positions(db_path)`, `insert_position(db_path, p)`. `connect` auto-creates the schema per new path. **Must `ensure_account(DRIVER_PAPER_DB, ...)` once** or the engine raises on `get_account()["halted"]`.
- `paper_positions` columns (for the scorecard): `symbol, strategy, status` (`OPEN`/`CLOSED`/`EXPIRED`), `quantity, entry_credit, max_loss_per, max_loss_total, realized_pnl, unrealized_pnl, current_value, exit_reason, exit_ts, entry_ts, dte_at_entry`.
- `paper_sizing.size_contracts(credit, width, max_risk=None) -> (qty, max_loss_per)`; `max_risk` default `config_paper.MAX_RISK_PER_TRADE=250.0`.
- `_proxy.schwab_py_client` is the proxy client (`from services import _proxy`).
- options_svc publish pattern: `version = bus.cache_set(KEY, data); bus.publish(EVENT, {"version": version})`.

---

## Phase 0 — Dedicated DB path + account seeding

### Task 0.1: `DRIVER_PAPER_DB` constant

**Files:** Modify `repo_paths.py`; Test `shared/bus/tests/test_repo_paths.py` (create if absent — else assert inline in Task 0.2's test).

**Step 1: Add the constant** after the JSON file paths in `repo_paths.py`:
```python
# Dedicated paper-account DB for the autonomous Driver — a SEPARATE file from the
# manual paper_account.db so the driver's book is fully isolated (zero schema change;
# every paper_account_db/paper_engine fn already takes db_path).
DRIVER_PAPER_DB = OPTIONS_SCANNER / "data" / "paper_account_driver.db"
```
**Step 2:** `.venv\Scripts\python -c "from repo_paths import DRIVER_PAPER_DB; print(DRIVER_PAPER_DB)"` → prints `...options-scanner\data\paper_account_driver.db`.
**Step 3: Commit** `feat(driver): DRIVER_PAPER_DB path for the isolated driver paper account`

### Task 0.2: `compute.ensure_driver_account` + `driver_account_view` (options_svc)

**Files:** Modify `services/options_svc/compute.py`; Test `services/options_svc/tests/test_driver_account.py`.

**Step 1: Failing test** (uses a tmp DB so it can't touch the real account):
```python
from services.options_svc import compute

def test_ensure_and_view_driver_account(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account(starting_balance=25000.0)
    v = compute.driver_account_view()
    assert v["has_account"] is True
    assert v["snapshot"]["cash"] == 25000.0
    assert v["snapshot"]["session_pnl"] == 0.0
    assert v["positions"] == [] and v["snapshot"]["open_count"] == 0
```

**Step 2:** Run → FAIL.
`​.venv\Scripts\python -m pytest services\options_svc\tests\test_driver_account.py -v`

**Step 3: Implement** (add to `compute.py`; mirror `paper_account_view` but threading `DRIVER_PAPER_DB`):
```python
from repo_paths import DRIVER_PAPER_DB   # add near the other imports


def ensure_driver_account(starting_balance: float = 25000.0) -> None:
    """Seed the dedicated driver paper account if absent (idempotent). Must run
    before the first open/manage — the engine indexes get_account()['halted']."""
    import datetime as dt
    import paper_account_db
    paper_account_db.ensure_account(DRIVER_PAPER_DB, starting_balance=starting_balance,
                                    session_date=dt.date.today().isoformat())


def has_driver_account() -> bool:
    import paper_account_db
    try:
        return paper_account_db.get_account(DRIVER_PAPER_DB) is not None
    except Exception:
        return False


def driver_account_view() -> dict:
    """Driver account snapshot + open positions (mirrors paper_account_view on the
    DRIVER db). No rescue overlay (that reads the manual account)."""
    import paper_account_db
    import paper_engine
    try:
        snapshot = paper_engine.account_snapshot(DRIVER_PAPER_DB)
    except Exception:
        snapshot = None
    try:
        positions = paper_account_db.fetch_open_positions(DRIVER_PAPER_DB)
    except Exception:
        positions = []
    try:
        orders = paper_account_db.fetch_orders(DRIVER_PAPER_DB, limit=100, status="FILLED")
    except Exception:
        orders = []
    return {"snapshot": snapshot, "positions": positions, "orders": orders,
            "has_account": has_driver_account()}
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): ensure_driver_account + driver_account_view (isolated DB)`

---

## Phase 1 — Performance scorecard (PURE — the headline deliverable)

### Task 1.1: `driver_perf.build_scorecard` (pure aggregator)

**Files:** Create `services/options_svc/driver_perf.py`; Test `services/options_svc/tests/test_driver_perf.py`. (Pure — no engine import, no I/O.)

**Step 1: Failing tests**
```python
from services.options_svc import driver_perf as dp

def _closed(symbol, strategy, pnl):
    return {"symbol": symbol, "strategy": strategy, "status": "CLOSED",
            "realized_pnl": pnl, "unrealized_pnl": None}

def _open(symbol, strategy, upnl):
    return {"symbol": symbol, "strategy": strategy, "status": "OPEN",
            "realized_pnl": None, "unrealized_pnl": upnl}

def _snap(**o):
    base = {"session_pnl": 0.0, "realized_pnl": 0.0, "open_unrealized": 0.0,
            "equity": 25000.0, "open_count": 0, "halted": False}
    base.update(o); return base

def test_empty_account():
    s = dp.build_scorecard([], _snap())
    assert s["total_trades"] == 0 and s["closed"] == 0 and s["win_rate"] == 0.0
    assert s["profit_factor"] is None and s["by_symbol"] == [] and s["best"] is None

def test_win_rate_and_profit_factor():
    pos = [_closed("MU","PCS",120.0), _closed("MU","PCS",-60.0),
           _closed("SPY","CCS",40.0), _open("QQQ","IC",15.0)]
    s = dp.build_scorecard(pos, _snap(open_unrealized=15.0, realized_pnl=100.0,
                                      session_pnl=115.0, open_count=1))
    assert s["total_trades"] == 4 and s["open"] == 1 and s["closed"] == 3
    assert s["wins"] == 2 and s["losses"] == 1
    assert s["win_rate"] == round(2/3, 4)
    assert s["realized_pnl"] == 100.0          # 120 - 60 + 40
    assert s["open_unrealized"] == 15.0 and s["total_pnl"] == 115.0
    assert s["avg_win"] == 80.0 and s["avg_loss"] == -60.0
    assert s["profit_factor"] == round(160.0/60.0, 2)   # (120+40)/|−60|
    assert s["best"]["realized_pnl"] == 120.0 and s["worst"]["realized_pnl"] == -60.0

def test_profit_factor_none_when_no_losses():
    s = dp.build_scorecard([_closed("MU","PCS",50.0)], _snap(realized_pnl=50.0))
    assert s["profit_factor"] is None     # no losses → undefined, render "—"

def test_breakdown_by_symbol_and_strategy():
    pos = [_closed("MU","PCS",100.0), _closed("MU","PCS",-30.0), _closed("SPY","CCS",20.0)]
    s = dp.build_scorecard(pos, _snap())
    bym = {r["symbol"]: r for r in s["by_symbol"]}
    assert bym["MU"]["trades"] == 2 and bym["MU"]["pnl"] == 70.0 and bym["MU"]["win_rate"] == 0.5
    bys = {r["strategy"]: r for r in s["by_strategy"]}
    assert bys["PCS"]["pnl"] == 70.0 and bys["CCS"]["pnl"] == 20.0

def test_tolerates_sparse_rows():
    s = dp.build_scorecard([{"status": "CLOSED"}, None, {}], _snap())
    assert s["closed"] >= 1   # None/empty don't crash
```

**Step 2:** Run → FAIL.

**Step 3: Implement** `services/options_svc/driver_perf.py`:
```python
"""PURE performance scorecard over the driver paper account's positions.

No I/O, no engine import — given the positions list (paper_account_db.fetch_all_positions)
and the account snapshot, it computes the 'how good is the autonomous module' metrics
the /driver page renders. Defensive: sparse/None rows are tolerated, never raise."""


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _is_closed(p):
    return (p or {}).get("status") in ("CLOSED", "EXPIRED")


def build_scorecard(positions, snapshot) -> dict:
    positions = [p for p in (positions or []) if isinstance(p, dict)]
    snap = snapshot or {}
    closed = [p for p in positions if _is_closed(p)]
    open_ = [p for p in positions if not _is_closed(p)]
    realized = [r for r in (_num(p.get("realized_pnl")) for p in closed) if r is not None]
    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    sum_w, sum_l = round(sum(wins), 2), round(sum(losses), 2)
    pf = round(sum_w / abs(sum_l), 2) if sum_l else None     # None = no losses yet
    best = max(closed, key=lambda p: _num(p.get("realized_pnl")) or 0, default=None) if realized else None
    worst = min(closed, key=lambda p: _num(p.get("realized_pnl")) or 0, default=None) if realized else None
    open_unreal = _num(snap.get("open_unrealized")) or 0.0
    return {
        "total_trades": len(positions),
        "open": len(open_), "closed": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
        "realized_pnl": round(sum(realized), 2),
        "open_unrealized": round(open_unreal, 2),
        "total_pnl": round(sum(realized) + open_unreal, 2),
        "session_pnl": _num(snap.get("session_pnl")),
        "avg_win": round(sum_w / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum_l / len(losses), 2) if losses else 0.0,
        "profit_factor": pf,
        "best": best, "worst": worst,
        "by_symbol": _group(closed, "symbol"),
        "by_strategy": _group(closed, "strategy"),
    }


def _group(closed, key):
    buckets = {}
    for p in closed:
        k = p.get(key) or "?"
        b = buckets.setdefault(k, {"trades": 0, "wins": 0, "pnl": 0.0})
        r = _num(p.get("realized_pnl")) or 0.0
        b["trades"] += 1
        b["wins"] += 1 if r > 0 else 0
        b["pnl"] = round(b["pnl"] + r, 2)
    out = [{key: k, "trades": b["trades"], "pnl": b["pnl"],
            "win_rate": round(b["wins"] / b["trades"], 4) if b["trades"] else 0.0}
           for k, b in buckets.items()]
    return sorted(out, key=lambda r: r["pnl"], reverse=True)
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): pure performance scorecard aggregator (driver_perf)`

### Task 1.2: `compute.driver_account_perf` (wire the scorecard to the driver DB)

**Files:** Modify `services/options_svc/compute.py`; Test `services/options_svc/tests/test_driver_account.py`.

**Step 1: Failing test**
```python
def test_driver_account_perf_reads_db(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    perf = compute.driver_account_perf()
    assert perf["total_trades"] == 0 and perf["win_rate"] == 0.0
```

**Step 2:** Run → FAIL. **Step 3: Implement** (add to `compute.py`):
```python
def driver_account_perf() -> dict:
    """Performance scorecard over the driver account (driver_perf.build_scorecard).
    Defensive → an empty scorecard on any failure."""
    import paper_account_db
    import paper_engine
    from services.options_svc import driver_perf
    try:
        positions = paper_account_db.fetch_all_positions(DRIVER_PAPER_DB)
    except Exception:
        positions = []
    try:
        snapshot = paper_engine.account_snapshot(DRIVER_PAPER_DB)
    except Exception:
        snapshot = {}
    return driver_perf.build_scorecard(positions, snapshot)
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): compute.driver_account_perf over the driver DB`

---

## Phase 2 — Open a position in the driver account

### Task 2.1: `compute.open_driver_position(signal, qty)`

**Files:** Modify `services/options_svc/compute.py`; Test `services/options_svc/tests/test_driver_account.py`.

> Extract the per-signal open block of `paper_engine.run_entry_cycle` (`paper_engine.py:169-228`) into a single-signal open against `DRIVER_PAPER_DB`. **Read that block first.** Key reconciliation: the engine **re-sizes off the actual fill** (`size_contracts(fill, width)`); the driver brings a guardrail-clamped `qty`, so open at **`min(clamped_qty, sized_off_fill)`** — the clamp is a CEILING the engine can only size *down* from. Use a `broker` injectable so the test can supply a deterministic fake fill (no proxy).

**Step 1: Failing test** (fake broker → deterministic FILLED; tmp driver DB):
```python
def _fake_broker(fill_price):
    class B:
        PREFIX = "[PAPER-DRV]"
        def submit_order(self, order, client):
            return {"orderId": 1, "status": "FILLED", "price": fill_price,
                    "enteredTime": "2026-06-25T14:00:00", "filledQuantity": order["quantity"]}
    return B()

def _driver_signal(**o):
    base = {"signal_id": "MU_PCS_x", "symbol": "MU", "strategy": "PCS",
            "short_strike": 105.0, "long_strike": 100.0, "call_short": None,
            "call_long": None, "width": 5.0, "expiration": "2026-07-10",
            "dte_at_entry": 15, "entry_credit": 1.50, "source": "driver"}
    base.update(o); return base

def test_open_driver_position_into_driver_db(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    # fill 1.50 on a $5 width → max_loss_per = (5-1.5)*100 = $350; clamped qty=2.
    res = compute.open_driver_position(_driver_signal(), qty=2, broker=_fake_broker(1.50))
    assert res["status"] == "opened"
    pv = compute.driver_account_view()
    assert pv["snapshot"]["open_count"] == 1
    pos = pv["positions"][0]
    assert pos["symbol"] == "MU" and pos["strategy"] == "PCS"
    assert pos["quantity"] == min(2, __import__("paper_sizing").size_contracts(1.5, 5.0)[0])

def test_open_driver_position_rejects_unfilled(tmp_path, monkeypatch):
    db = tmp_path / "driver.db"; monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    class B:
        PREFIX = "x"
        def submit_order(self, order, client): return {"status": "REJECTED", "price": 0}
    res = compute.open_driver_position(_driver_signal(), qty=1, broker=B())
    assert res["status"] != "opened"
    assert compute.driver_account_view()["snapshot"]["open_count"] == 0
```

**Step 2:** Run → FAIL.

**Step 3: Implement** `compute.open_driver_position` — adapt `run_entry_cycle`'s per-signal block (size→submit→re-size-on-fill→guard→reserve_buying_power→insert_position) threading `DRIVER_PAPER_DB`, with the `min(qty, sized)` reconciliation. Skeleton (fill in from `paper_engine.py:169-228`):
```python
def open_driver_position(signal: dict, qty: int, broker=None) -> dict:
    """Open ONE driver position into DRIVER_PAPER_DB at min(clamped qty, fill-sized).
    Returns {"status": "opened"|"rejected"|"error", ...}. Never raises."""
    import paper_account_db, paper_broker, paper_sizing, config_paper
    broker = broker or paper_broker            # module exposes submit_order
    try:
        ensure_driver_account()
        if paper_account_db.get_account(DRIVER_PAPER_DB)["halted"]:
            return {"status": "rejected", "reason": "halted"}
        order = {"signal_id": signal["signal_id"], "symbol": signal["symbol"],
                 "side": "SELL_TO_OPEN", "strategy": signal["strategy"],
                 "short_strike": signal["short_strike"], "long_strike": signal["long_strike"],
                 "call_short": signal.get("call_short"), "call_long": signal.get("call_long"),
                 "expiration": signal["expiration"], "quantity": int(qty),
                 "limit_price": signal["entry_credit"], "legs": []}
        resp = broker.submit_order(order, _proxy.schwab_py_client)
        if resp.get("status") != "FILLED":
            return {"status": "rejected", "reason": resp.get("status")}
        fill = resp["price"]
        if fill < config_paper.MIN_FILL_CREDIT:
            return {"status": "rejected", "reason": "LOW_CREDIT"}
        sized, max_loss_per = paper_sizing.size_contracts(fill, signal["width"])
        open_qty = min(int(qty), sized)        # the guardrail clamp is a CEILING
        if max_loss_per <= 0 or open_qty < 1:
            return {"status": "rejected", "reason": "RISK_TOO_HIGH"}
        max_loss_total = round(max_loss_per * open_qty, 2)
        if max_loss_total > paper_account_db.get_account(DRIVER_PAPER_DB)["cash"]:
            return {"status": "rejected", "reason": "INSUFFICIENT_BUYING_POWER"}
        order["quantity"] = open_qty
        oid = paper_account_db.insert_order(DRIVER_PAPER_DB, {**order, **resp})  # match _record_order shape — CONFIRM against paper_engine._record_order
        paper_account_db.reserve_buying_power(DRIVER_PAPER_DB, max_loss_total)
        paper_account_db.insert_position(DRIVER_PAPER_DB, {
            "signal_id": signal["signal_id"], "symbol": signal["symbol"],
            "strategy": signal["strategy"], "short_strike": signal["short_strike"],
            "long_strike": signal["long_strike"], "call_short": signal.get("call_short"),
            "call_long": signal.get("call_long"), "width": signal["width"],
            "expiration": signal["expiration"], "dte_at_entry": signal.get("dte_at_entry", 0),
            "quantity": open_qty, "entry_credit": fill, "entry_order_id": oid,
            "max_loss_per": max_loss_per, "max_loss_total": max_loss_total,
            "entry_ts": resp["enteredTime"]})
        return {"status": "opened", "symbol": signal["symbol"], "qty": open_qty,
                "entry_credit": fill, "max_loss_total": max_loss_total}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}
```
> NOTE: confirm `paper_account_db.insert_order` arg shape against `paper_engine._record_order` (read it) — mirror exactly so the order row + `entry_order_id` link is correct. If `_record_order` does extra work, expose a small reusable helper in `paper_engine` instead of duplicating, and call it with `DRIVER_PAPER_DB`.

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): open_driver_position into the isolated driver account`

---

## Phase 3 — Manage cycle for the driver account

### Task 3.1: `compute.run_driver_manage_cycle` + `driver_account_perf` already done

**Files:** Modify `services/options_svc/compute.py`; Test `services/options_svc/tests/test_driver_account.py`.

**Step 1: Failing test** (a smoke that it runs against the driver DB without touching the manual one; reprice is proxy-bound, so monkeypatch `paper_engine.run_manage_cycle` to assert the db_path):
```python
def test_run_driver_manage_cycle_targets_driver_db(monkeypatch):
    from services.options_svc import compute
    import paper_engine
    seen = {}
    monkeypatch.setattr(paper_engine, "run_manage_cycle",
                        lambda client, today, db_path=None: seen.update(db_path=db_path))
    compute.run_driver_manage_cycle()
    assert seen["db_path"] == compute.DRIVER_PAPER_DB
```

**Step 2:** Run → FAIL. **Step 3: Implement**:
```python
def run_driver_manage_cycle() -> None:
    """Reprice + auto-close the DRIVER account's open positions (run_manage_cycle on
    DRIVER_PAPER_DB). No-op-safe if the account doesn't exist yet."""
    import datetime as dt
    import paper_engine
    if not has_driver_account():
        return
    paper_engine.run_manage_cycle(_proxy.schwab_py_client, dt.date.today().isoformat(),
                                  db_path=DRIVER_PAPER_DB)
```

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): run_driver_manage_cycle (reprice/auto-exit the driver account)`

---

## Phase 4 — options_svc handlers + cache views

### Task 4.1: cache keys + publish helpers + `driver_paper_create` command

**Files:** Modify `services/options_svc/handlers.py`; Test `services/options_svc/tests/test_handlers_driver_paper.py`.

**Step 1: Failing tests** (`Bus(fake=True)`):
```python
import json
from shared.bus import Bus
from shared.contracts.envelope import Command
from services.options_svc import handlers

def test_refresh_driver_paper_publishes(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "driver_account_view",
                        lambda: {"snapshot": {"session_pnl": 12.0}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf",
                        lambda: {"total_trades": 0, "win_rate": 0.0})
    handlers.refresh_driver_paper(bus)
    acct = bus.cache_get("cache:options:driver_paper_account")
    perf = bus.cache_get("cache:options:driver_paper_perf")
    assert acct.payload["snapshot"]["session_pnl"] == 12.0
    assert perf.payload["total_trades"] == 0

def test_driver_paper_create_opens_and_publishes(monkeypatch):
    bus = Bus(fake=True)
    opened = {}
    monkeypatch.setattr(handlers.compute, "open_driver_position",
                        lambda signal, qty, **k: opened.update(signal=signal, qty=qty) or {"status": "opened"})
    monkeypatch.setattr(handlers.compute, "driver_account_view", lambda: {"snapshot": {}, "positions": [], "has_account": True})
    monkeypatch.setattr(handlers.compute, "driver_account_perf", lambda: {})
    handlers.handle_command(bus, Command(type="driver_paper_create",
                                         args={"signal": {"symbol": "MU"}, "qty": 2}))
    assert opened["signal"]["symbol"] == "MU" and opened["qty"] == 2
    assert bus.cache_get("cache:options:driver_paper_account") is not None
```

**Step 2:** Run → FAIL.

**Step 3: Implement** (add to `handlers.py`):
```python
CACHE_DRIVER_PAPER = "cache:options:driver_paper_account"
EVENT_DRIVER_PAPER = "events:options:driver_paper_account"
CACHE_DRIVER_PERF = "cache:options:driver_paper_perf"
EVENT_DRIVER_PERF = "events:options:driver_paper_perf"


def refresh_driver_paper(bus) -> None:
    """Publish the driver account view + performance scorecard (NO rescue overlay —
    that reads the manual account)."""
    acct = compute.driver_account_view()
    va = bus.cache_set(CACHE_DRIVER_PAPER, acct)
    bus.publish(EVENT_DRIVER_PAPER, {"version": va})
    perf = compute.driver_account_perf()
    vp = bus.cache_set(CACHE_DRIVER_PERF, perf)
    bus.publish(EVENT_DRIVER_PERF, {"version": vp})


def run_driver_manage_and_refresh(bus) -> None:
    """5-min driver-account manage tick: reprice/auto-close then republish."""
    compute.run_driver_manage_cycle()
    refresh_driver_paper(bus)
```
And in `handle_command`, add:
```python
    elif command.type == "driver_paper_create":
        compute.open_driver_position(command.args.get("signal"),
                                     int(command.args.get("qty", 1)))
        refresh_driver_paper(bus)
    elif command.type == "driver_paper_manage":
        run_driver_manage_and_refresh(bus)
    elif command.type == "driver_paper_reset":
        compute.ensure_driver_account(float(command.args.get("starting_balance", 25000.0)))
        refresh_driver_paper(bus)
```
(Keep all existing branches.)

**Step 4:** Run → PASS. **Step 5: Commit** `feat(driver): options_svc driver_paper_create + driver account/perf publish`

---

## Phase 5 — options_svc scheduler: driver-account manage tick

### Task 5.1: ride the existing 5-min manage tick for the driver account

**Files:** Modify `services/options_svc/scheduler.py`; Test `services/options_svc/tests/test_scheduler.py` (add a case).

> The existing 5-min `manage_due` loop branch (`scheduler.py:293-299`) runs `handlers.run_manage_and_refresh` for the MANUAL account. Add a sibling line in the SAME `m_due` block that also runs the driver manage+refresh, so the driver account reprices on the same 5-min cadence. Keep it independently guarded.

**Step 1:** In the `m_due` branch, after the manual `run_manage_and_refresh`, add:
```python
                await loop_.run_in_executor(None, handlers.run_driver_manage_and_refresh, bus)
```
(inside the existing `try` or its own guarded `try` so a driver-side failure can't skip the manual refresh — prefer a separate `try/except: pass`.)

**Step 2:** Add a test asserting `run_driver_manage_and_refresh` is wired (or a pure-gate test if you extract one). Run the folder.
**Step 3: Commit** `feat(driver): scheduler reprices the driver account on the 5-min manage tick`

---

## Phase 6 — driver_svc rewiring (the load-bearing correctness fix)

### Task 6.1: `AutonomousState.perf` contract field

**Files:** Modify `shared/contracts/driver.py`; Test `shared/contracts/tests/test_driver.py`.

**Step 1: Failing test**
```python
def test_autonomous_state_perf_field():
    from shared.contracts.driver import AutonomousState
    s = AutonomousState(perf={"win_rate": 0.5, "total_trades": 4})
    assert s.perf["win_rate"] == 0.5
    assert AutonomousState().perf == {}     # additive default
```
**Step 2:** Run → FAIL. **Step 3:** Add `perf: dict = {}` to `AutonomousState`. **Step 4:** PASS. **Step 5: Commit** `feat(contracts): AutonomousState.perf scorecard field`

### Task 6.2: driver reads its OWN account for day-P&L + enqueues `driver_paper_create`

**Files:** Modify `services/driver_svc/handlers.py` (+ `compute.py` if `_day_pnl`/`build_packet` need the new view); Test `services/driver_svc/tests/test_handlers_autonomous.py` (update) + e2e.

> Two changes in `run_autonomous_cycle`:
> 1. Read day-P&L + open positions from **`cache:options:driver_paper_account`** (new `CACHE_OPT_DRIVER_PAPER = "cache:options:driver_paper_account"`), NOT `cache:options:paper_account`. Pass that view to `compute.run_cycle` (so `_day_pnl` reads the driver account's `session_pnl`).
> 2. Enqueue **`driver_paper_create`** (not `paper_create`) per survivor: `{"type":"driver_paper_create","args":{"signal":{**t["signal"],"source":"driver"},"qty":t["qty"]}}`.
> 3. Attach the perf scorecard: read `cache:options:driver_paper_perf` and pass to `_publish_autonomous` → `AutonomousState.perf`.

**Step 1:** Update the existing `test_cycle_enqueues_paper_create` → assert the command type is now `driver_paper_create`; add a test that the driver reads `cache:options:driver_paper_account` for day-P&L (seed it, assert build_packet/_day_pnl uses it). Add a perf-passthrough test.
**Step 2:** Run → FAIL. **Step 3:** Implement the re-wiring (change `CACHE_OPT_PAPER`→driver key in `_read_payload`; change the enqueue command type; read+attach perf). **Step 4:** Run → PASS (whole `services\driver_svc` folder). **Step 5: Commit** `fix(driver): autonomous cycle trades into + reads P&L from the isolated driver account`

### Task 6.3: ensure the driver account exists before the first cycle

**Files:** `services/driver_svc` — on enable / first cycle, enqueue a `driver_paper_reset`/ensure (or have options_svc `ensure_driver_account` lazily in `open_driver_position`, already done). Confirm the lazy `ensure_driver_account()` inside `open_driver_position` (Task 2.1) is sufficient; if a fresh view is needed before any trade, enqueue `driver_paper_manage` on enable. Add a test if you add the enable hook. Commit.

---

## Phase 7 — /driver page: account view + scorecard card

### Task 7.1: pure scorecard builders

**Files:** Modify `webgui/pages/driver.py`; Test `webgui/tests/test_driver_monitor.py`.

**Step 1: Failing tests** for pure builders, e.g.:
```python
def test_scorecard_rows():
    perf = {"total_trades": 4, "closed": 3, "wins": 2, "win_rate": 0.667,
            "realized_pnl": 100.0, "open_unrealized": 15.0, "total_pnl": 115.0,
            "profit_factor": 2.5, "avg_win": 80.0, "avg_loss": -60.0,
            "by_symbol": [{"symbol":"MU","trades":2,"pnl":70.0,"win_rate":0.5}],
            "by_strategy": [{"strategy":"PCS","trades":2,"pnl":70.0,"win_rate":0.5}]}
    rows = driver.scorecard_metric_rows(perf)
    assert any("Win rate" in r[0] and "66.7%" in r[1] for r in rows)
    assert any("Profit factor" in r[0] and "2.5" in r[1] for r in rows)
    assert driver.scorecard_breakdown_rows(perf["by_symbol"], "symbol")[0]["pnl"] == "+$70.00"

def test_scorecard_empty_safe():
    assert driver.scorecard_metric_rows({}) and driver.scorecard_breakdown_rows(None, "symbol") == []
```
**Step 2:** Run → FAIL. **Step 3:** Implement `scorecard_metric_rows(perf)` (Win rate / Trades / Realized / Unrealized / Total P&L / Profit factor [`"—"` when None] / Avg win / Avg loss) + `scorecard_breakdown_rows(rows, key)` (formatted pnl via `_money`, win-rate %). **Step 4:** PASS. **Step 5: Commit** `feat(webgui): driver scorecard pure builders`

### Task 7.2: render the scorecard + point the monitor at the driver account

**Files:** Modify `webgui/pages/driver.py`; Test `webgui/tests/test_driver_monitor.py` (render smoke).

> 1. Change the monitor's Day-P&L + open-positions read from `options:paper_account` → **`options:driver_paper_account`** (and version-poll it). [This replaces the Phase-"P&L fix" read so the monitor shows the driver's OWN book.]
> 2. Add a **Performance scorecard** card below the decision log: the metric chips (`scorecard_metric_rows`) + a P&L-by-symbol and P&L-by-strategy table (`scorecard_breakdown_rows`), sourced from `AutonomousState.perf` (already on the cached autonomous view) or `cache:options:driver_paper_perf`.
> 3. Version-poll the new view(s).

**Step 1:** Add a render-smoke test seeding `cache:options:driver_paper_account` + `AutonomousState.perf`; assert `render()` doesn't raise and the builders surface the numbers. **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` green. **Step 5: Commit** `feat(webgui): /driver shows the isolated driver account + performance scorecard`

---

## Phase 8 — End-to-end + verification + docs

### Task 8.1: Redis-driven e2e (real pipeline)

**Files:** `services/options_svc/tests/test_driver_paper_e2e.py` (uses a tmp DRIVER_PAPER_DB + fake broker).

**Step 1:** Seed nothing; `monkeypatch` `compute.DRIVER_PAPER_DB`→tmp + a fake broker; enqueue/dispatch `driver_paper_create` with a real-shaped signal + qty=2; assert (a) a position lands in the driver DB at the clamped/sized qty, (b) `cache:options:driver_paper_account` + `cache:options:driver_paper_perf` reflect it (open_count=1, total_trades=1), (c) the manual `paper_account.db` is untouched (isolation). **Step 2:** Run → PASS. **Step 3: Commit** `test(driver): e2e isolated driver-account open + views`

### Task 8.2: Full-suite verification (@superpowers:verification-before-completion)
Run + paste real output:
```
.venv\Scripts\python -m pytest services\options_svc      # green
.venv\Scripts\python -m pytest services\driver_svc       # green
.venv\Scripts\python -m pytest shared\contracts          # green
cd webgui && ..\.venv\Scripts\python -m pytest -q        # green
```
Live smoke (services up): restart options_svc + driver_svc + webgui; enable autonomy; confirm a checkpoint opens a position in the driver account, `/driver` shows it + the scorecard, and `/options/portfolio` (manual account) is unchanged.

### Task 8.3: Update CLAUDE.md
Add an "Isolated driver paper account + scorecard" note to the Driver section (the dedicated `paper_account_driver.db`, `driver_paper_create`/`run_driver_manage_cycle`/`driver_paper_perf`, the new cache views, the page scorecard, and that the driver's $500/halt now reads its own book). Commit.

---

## Out of scope (do NOT build)
The 14 historical ledger MU trades (left in `trades.db`); migrating manual trades; a full "driver account reset" UI (the `driver_paper_reset` command suffices); level-C/live; per-position `source` column (a dedicated DB makes it redundant).
