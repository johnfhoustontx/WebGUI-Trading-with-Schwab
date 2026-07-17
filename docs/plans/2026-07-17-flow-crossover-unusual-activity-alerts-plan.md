# Options-flow alerts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Alert (in-app toast+chime + Discord/Telegram) when a symbol's cumulative call **premium ($)** crosses over its put premium (sentiment flip), or when there's an unusual per-minute burst of call/put **volume** — across the whole collected universe.

**Architecture:** Server-side detection in `options_svc` rides the existing 1-min GEX poll: after `collect_gex_history`, a new `run_flow_alerts(bus)` reads each symbol's day flow series from `gex_history_db.load_flow_series`, runs pure detectors (`flow_alerts.py`) with a Redis cooldown map, pushes via `push_notify.send_flow_alert`, and appends to `cache:options:flow_alerts`; the webgui's 2-s watcher reads that key and fires a toast+chime.

**Tech Stack:** Python 3.11, options_svc (FastAPI service), NiceGUI webgui, `shared.notify.channels` (Telegram/Discord/SMS), `gex_history_db` (SQLite), pytest + fakeredis.

**Design:** [2026-07-17-flow-crossover-unusual-activity-alerts-design.md](2026-07-17-flow-crossover-unusual-activity-alerts-design.md)

**Test commands** (repo root): `.venv\Scripts\python -m pytest services\options_svc\tests\<file> -q`; webgui: `cd webgui && ..\.venv\Scripts\python -m pytest tests\<file> -q`.

**Key facts:**
- `gex_history_db.load_flow_series(conn, symbol, d=None)` → list of tuples `(ts, spot, call_vol, put_vol, call_prem, put_prem)`, chronological, `ts` = epoch seconds. `d` default = today.
- The collected universe = `gex_collector.collection_symbols()` (index base + `Top 20.xlsx`).
- `shared.notify.channels`: `send_telegram(token, chat_id, text)`, `send_discord(webhook_url, embed)` — both no-op on missing creds, never raise.
- `push_notify.load_config()` → the notifications config dict (`enabled`, `telegram`, `discord`, `sms`, `market_hours_only`). `load_seen(bus, key)` / `save_seen(bus, key, dict)` = read/write a Redis dict.
- Hook point: `handlers.collect_gex_history(bus)` (services/options_svc/handlers.py:632) already calls `publish_flow_skew(bus)` after `compute.collect_gex_snapshots()` — add `run_flow_alerts(bus)` the same way (best-effort, guarded).

---

### Task 1: Config file + loader

**Files:**
- Create: `config/flow_alerts.toml`
- Modify: `repo_paths.py` (add `FLOW_ALERTS_TOML`)
- Create: `services/options_svc/flow_alerts.py` (the `load_thresholds` part)
- Test: `services/options_svc/tests/test_flow_alerts.py` (create)

**Step 1: Write `config/flow_alerts.toml`:**
```toml
# Options-flow alert thresholds. Edit + restart options_svc to tune.
enabled = true          # whole-feature kill switch (server push side)

[crossover]
band = 0.02             # the crossing side must lead by >= 2% of the larger side
cooldown_min = 30       # per-symbol re-fire cooldown

[spike]
k = 4.0                 # fire when this-minute volume >= k x trailing average
window = 20             # trailing-average window (minutes/increments)
floor = 500             # absolute contract floor (ignore thinner bursts)
min_points = 5          # warm-up: need this many increments before firing
cooldown_min = 20       # per-symbol-per-side re-fire cooldown
```

**Step 2: Add to `repo_paths.py`** near the other config consts:
```python
FLOW_ALERTS_TOML = REPO_ROOT / "config" / "flow_alerts.toml"
```

**Step 3: Write the failing test** in `services/options_svc/tests/test_flow_alerts.py`:
```python
from services.options_svc import flow_alerts


def test_load_thresholds_defaults(tmp_path, monkeypatch):
    # Missing file → built-in defaults, never raises.
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", tmp_path / "nope.toml")
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is True
    assert cfg["spike"]["k"] == 4.0 and cfg["crossover"]["band"] == 0.02


def test_load_thresholds_reads_file(tmp_path, monkeypatch):
    p = tmp_path / "flow_alerts.toml"
    p.write_text("enabled = false\n[spike]\nk = 9.0\n", encoding="utf-8")
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", p)
    cfg = flow_alerts.load_thresholds()
    assert cfg["enabled"] is False and cfg["spike"]["k"] == 9.0
    assert cfg["crossover"]["band"] == 0.02   # unspecified keys fall back to defaults
```

**Step 4: Run → FAIL.** `.venv\Scripts\python -m pytest services\options_svc\tests\test_flow_alerts.py -q`

**Step 5: Implement** the loader in `services/options_svc/flow_alerts.py`:
```python
"""Pure options-flow alert detection (crossover + unusual-activity) + config.

Operates on a symbol's day flow series (list of (ts, spot, call_vol, put_vol,
call_prem, put_prem) tuples from gex_history_db.load_flow_series) and a cooldown
map. No I/O, no push — the handler wires those. See the design doc."""
import logging
import tomllib

from repo_paths import FLOW_ALERTS_TOML

log = logging.getLogger(__name__)
_TOML_PATH = FLOW_ALERTS_TOML

_DEFAULTS = {
    "enabled": True,
    "crossover": {"band": 0.02, "cooldown_min": 30},
    "spike": {"k": 4.0, "window": 20, "floor": 500, "min_points": 5,
              "cooldown_min": 20},
}


def _merge(base, over):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_thresholds() -> dict:
    """flow_alerts.toml merged over the built-in defaults. Never raises."""
    try:
        with open(_TOML_PATH, "rb") as fh:
            return _merge(_DEFAULTS, tomllib.load(fh))
    except Exception:
        log.debug("flow_alerts.toml load failed → defaults", exc_info=True)
        return _merge(_DEFAULTS, {})
```

**Step 6: Run → PASS. Commit** `git add config/flow_alerts.toml repo_paths.py services/options_svc/flow_alerts.py services/options_svc/tests/test_flow_alerts.py` → `feat(flow-alerts): config file + threshold loader`.

---

### Task 2: Pure detectors

**Files:** Modify `services/options_svc/flow_alerts.py`; Test `services/options_svc/tests/test_flow_alerts.py`.

**Step 1: Write failing tests** (append):
```python
def _row(ts, cv, pv, cp, pp):
    return (ts, 100.0, cv, pv, cp, pp)   # (ts, spot, call_vol, put_vol, call_prem, put_prem)


def test_crossover_calls_overtake_puts():
    # net = call_prem - put_prem flips - → + decisively.
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    a = flow_alerts.detect_crossover(series, band=0.02)
    assert a and a["side"] == "calls_over" and a["type"] == "crossover"


def test_crossover_none_when_no_flip():
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 150.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02) is None


def test_crossover_band_rejects_graze():
    # Flips sign but only by a hair (< 2% of the larger side) → no alert.
    series = [_row(60, 0, 0, 199.0, 200.0), _row(120, 0, 0, 201.0, 200.0)]
    assert flow_alerts.detect_crossover(series, band=0.02) is None


def test_spike_fires_above_baseline_and_floor():
    # 5 quiet minutes (+100/min) then a +2000 burst → 20x baseline, over floor.
    cum = 0
    series = []
    for i, inc in enumerate([100, 100, 100, 100, 100, 2000]):
        cum += inc
        series.append(_row((i + 1) * 60, cum, 0, 0.0, 0.0))
    a = flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20, min_points=5)
    assert a and a["side"] == "call" and a["type"] == "spike"


def test_spike_respects_floor():
    # A relatively big jump but below the absolute floor → no alert.
    cum = 0
    series = []
    for inc in [10, 10, 10, 10, 10, 100]:   # 100 < floor 500
        cum += inc
        series.append(_row(len(series) * 60, cum, 0, 0.0, 0.0))
    assert flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20, min_points=5) is None


def test_spike_warmup_needs_min_points():
    series = [_row(60, 100, 0, 0.0, 0.0), _row(120, 5000, 0, 0.0, 0.0)]  # 1 increment
    assert flow_alerts.detect_spike(series, "call", k=4.0, floor=500, window=20, min_points=5) is None


def test_detect_flow_alerts_cooldown_suppresses_repeat():
    series = [_row(60, 0, 0, 100.0, 200.0), _row(120, 0, 0, 260.0, 200.0)]
    cfg = flow_alerts.load_thresholds()
    cd = {}
    first = flow_alerts.detect_flow_alerts("$SPX", series, cfg, cd, now_ts=120)
    assert any(a["type"] == "crossover" for a in first)
    # Same tick again within cooldown → nothing new.
    second = flow_alerts.detect_flow_alerts("$SPX", series, cfg, cd, now_ts=180)
    assert second == []
```

**Step 2: Run → FAIL.**

**Step 3: Implement** (append to `flow_alerts.py`):
```python
def _norm(series):
    """[(ts, spot, call_vol, put_vol, call_prem, put_prem), …] → list of dicts,
    dropping rows with a non-numeric ts."""
    out = []
    for r in series or []:
        if len(r) < 6 or not isinstance(r[0], (int, float)):
            continue
        out.append({"ts": r[0], "call_vol": r[2] or 0, "put_vol": r[3] or 0,
                    "call_prem": r[4], "put_prem": r[5]})
    return out


def detect_crossover(series, band):
    """Alert dict when net=call_prem-put_prem flips sign vs the prior snapshot and the
    new lead clears `band` × the larger side; else None. side: calls_over | puts_over."""
    rows = [r for r in _norm(series)
            if isinstance(r["call_prem"], (int, float)) and isinstance(r["put_prem"], (int, float))]
    if len(rows) < 2:
        return None
    prev, cur = rows[-2], rows[-1]
    n0 = prev["call_prem"] - prev["put_prem"]
    n1 = cur["call_prem"] - cur["put_prem"]
    crossed = (n0 < 0 < n1) or (n1 < 0 < n0)
    larger = max(cur["call_prem"], cur["put_prem"], 1.0)
    if not crossed or abs(n1) < band * larger:
        return None
    side = "calls_over" if n1 > 0 else "puts_over"
    return {"type": "crossover", "side": side, "ts": cur["ts"],
            "call_prem": cur["call_prem"], "put_prem": cur["put_prem"]}


def _increments(rows, field):
    out = []
    for i in range(1, len(rows)):
        d = (rows[i][field] or 0) - (rows[i - 1][field] or 0)
        out.append(d if d > 0 else 0.0)   # cumulative shouldn't drop; guard
    return out


def detect_spike(series, side, k, floor, window, min_points):
    """Alert dict when this-minute `side` volume increment ≥ k×trailing-avg AND ≥ floor
    (after a warm-up of min_points increments); else None. side: call | put."""
    field = "call_vol" if side == "call" else "put_vol"
    rows = _norm(series)
    incs = _increments(rows, field)
    if len(incs) < min_points:
        return None
    latest = incs[-1]
    base_window = incs[-1 - window:-1] if window > 0 else incs[:-1]
    baseline = (sum(base_window) / len(base_window)) if base_window else 0.0
    if latest < floor:
        return None
    if baseline > 0 and latest < k * baseline:
        return None
    return {"type": "spike", "side": side, "ts": rows[-1]["ts"],
            "increment": latest, "baseline": baseline,
            "mult": (latest / baseline) if baseline > 0 else None}


def _on_cooldown(cooldowns, key, now_ts, cooldown_sec):
    last = cooldowns.get(key)
    return isinstance(last, (int, float)) and (now_ts - last) < cooldown_sec


def detect_flow_alerts(symbol, series, cfg, cooldowns, now_ts):
    """Run both detectors for one symbol, honoring the cooldown map (mutated in place;
    the caller persists it). Returns a list of alert dicts (each with symbol + id)."""
    out = []
    xo = cfg.get("crossover", {})
    sp = cfg.get("spike", {})

    a = detect_crossover(series, band=xo.get("band", 0.02))
    if a:
        key = f"{symbol}|crossover"
        if not _on_cooldown(cooldowns, key, now_ts, xo.get("cooldown_min", 30) * 60):
            cooldowns[key] = now_ts
            out.append({**a, "symbol": symbol})

    for side in ("call", "put"):
        a = detect_spike(series, side, k=sp.get("k", 4.0), floor=sp.get("floor", 500),
                         window=sp.get("window", 20), min_points=sp.get("min_points", 5))
        if a:
            key = f"{symbol}|spike|{side}"
            if not _on_cooldown(cooldowns, key, now_ts, sp.get("cooldown_min", 20) * 60):
                cooldowns[key] = now_ts
                out.append({**a, "symbol": symbol})

    for a in out:
        a["id"] = f"{a['symbol']}|{a['type']}|{a.get('side')}|{int(a['ts'])}"
        a["text"] = alert_text(a)
    return out


def alert_text(a) -> str:
    """One-line human-readable alert (reused by push + popup). No buy/sell claim."""
    s = a["symbol"]
    if a["type"] == "crossover":
        if a["side"] == "calls_over":
            return (f"{s}: call premium overtook puts — "
                    f"${a['call_prem']:,.0f} vs ${a['put_prem']:,.0f} (bullish flip)")
        return (f"{s}: put premium overtook calls — "
                f"${a['put_prem']:,.0f} vs ${a['call_prem']:,.0f} (bearish flip)")
    mult = f"{a['mult']:.1f}× avg" if a.get("mult") else "burst"
    return (f"{s}: unusual {a['side']} activity — {int(a['increment']):,} contracts "
            f"this minute ({mult})")
```

**Step 4: Run → PASS.**

**Step 5: Commit** `feat(flow-alerts): pure crossover + volume-spike detectors`.

---

### Task 3: Push formatter + sender

**Files:** Modify `services/options_svc/push_notify.py`; Test `services/options_svc/tests/test_push_notify.py` (append).

**Step 1: Write failing tests:**
```python
def test_flow_alert_telegram_and_discord_shape():
    from services.options_svc import push_notify as pn
    a = {"type": "crossover", "side": "calls_over", "symbol": "$SPX",
         "text": "$SPX: call premium overtook puts — $260 vs $200 (bullish flip)"}
    assert "$SPX" in pn.flow_alert_telegram_text(a)
    e = pn.flow_alert_discord_embed(a)
    assert e["color"] == 0x2ECC71 and "$SPX" in e["title"] + str(e.get("description", ""))
    a2 = {"type": "spike", "side": "put", "symbol": "MU", "text": "MU: unusual put activity"}
    assert pn.flow_alert_discord_embed(a2)["color"] == 0xE74C3C   # put/bearish → red


def test_send_flow_alert_noop_when_disabled(monkeypatch):
    from services.options_svc import push_notify as pn
    calls = []
    monkeypatch.setattr(pn, "send_telegram", lambda *a, **k: calls.append("tg"))
    monkeypatch.setattr(pn, "send_discord", lambda *a, **k: calls.append("dc"))
    a = {"type": "spike", "side": "call", "symbol": "SPY", "text": "x"}
    assert pn.send_flow_alert(a, config={"enabled": False}) is False
    assert calls == []
    pn.send_flow_alert(a, config={"enabled": True, "telegram": {}, "discord": {}})
    assert set(calls) == {"tg", "dc"}
```
(`send_telegram`/`send_discord` are imported into `push_notify` from `shared.notify.channels` — confirm the import names; if they're referenced as `channels.send_telegram`, monkeypatch that path instead.)

**Step 2: Run → FAIL.**

**Step 3: Implement** in `push_notify.py` (mirror `send_action_digest`; green = bullish/calls, red = bearish/puts):
```python
_FLOW_GREEN = 0x2ECC71
_FLOW_RED = 0xE74C3C


def _flow_is_bullish(a) -> bool:
    return (a.get("type") == "crossover" and a.get("side") == "calls_over") or \
           (a.get("type") == "spike" and a.get("side") == "call")


def flow_alert_telegram_text(a) -> str:
    icon = "🟢" if _flow_is_bullish(a) else "🔴"
    return f"{icon} <b>Flow alert</b> — {a.get('text', '')}"


def flow_alert_discord_embed(a) -> dict:
    return {"title": "Options-flow alert",
            "description": a.get("text", ""),
            "color": _FLOW_GREEN if _flow_is_bullish(a) else _FLOW_RED}


def send_flow_alert(a, *, config: dict | None = None) -> bool:
    """Push one flow alert to Telegram + Discord. Best-effort, never raises.
    Returns True if a send was attempted (config enabled)."""
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return False
    tg = cfg.get("telegram", {})
    dc = cfg.get("discord", {})
    send_telegram(tg.get("bot_token"), tg.get("chat_id"), flow_alert_telegram_text(a))
    send_discord(dc.get("webhook_url"), flow_alert_discord_embed(a))
    return True
```
(If `send_telegram`/`send_discord` aren't already imported at module top, add `from shared.notify.channels import send_telegram, send_discord` — check how `send_action_digest` calls them and match.)

**Step 4: Run → PASS. Commit** `feat(flow-alerts): Telegram/Discord push for flow alerts`.

---

### Task 4: Handler — detect, cooldown, push, publish

**Files:** Modify `services/options_svc/handlers.py`; Modify `services/options_svc/compute.py` (a thin `flow_alert_series(symbol)` reader if convenient, else read in the handler); Test `services/options_svc/tests/test_handlers.py` (append).

**Step 1: Write the failing test** — with a fakeredis `Bus`, monkeypatch the universe + `load_flow_series` to a seeded crossover series, monkeypatch `push_notify.send_flow_alert` to record calls, call `handlers.run_flow_alerts(bus)`, and assert: (a) `cache:options:flow_alerts` payload contains the crossover alert; (b) `send_flow_alert` was called once; (c) the cooldown state persisted; (d) a second call with the same series is silent (cooldown) + no new push.

Sketch:
```python
def test_run_flow_alerts_detects_pushes_publishes(monkeypatch):
    from shared.bus import Bus
    from services.options_svc import handlers, flow_alerts
    bus = Bus(fake=True)
    series = [(60, 100.0, 0, 0, 100.0, 200.0), (120, 100.0, 0, 0, 260.0, 200.0)]
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["$SPX"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda sym: series)
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: 120)
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert", lambda a, **k: sent.append(a))
    handlers.run_flow_alerts(bus)
    env = bus.cache_get("cache:options:flow_alerts")
    assert env and any(x["type"] == "crossover" for x in env.payload["alerts"])
    assert len(sent) == 1
    handlers.run_flow_alerts(bus)   # cooldown → silent
    assert len(sent) == 1
```

**Step 2: Run → FAIL.**

**Step 3: Implement** `run_flow_alerts(bus)` in `handlers.py`. Add module consts `CACHE_FLOW_ALERTS = "cache:options:flow_alerts"`, `EVENT_FLOW_ALERTS = "events:options:flow_alerts"`, `_FLOW_COOLDOWN_KEY = "cache:options:flow_alert_cooldowns"`, `_FLOW_ALERTS_MAX = 50`. Helpers `_flow_alert_symbols()` (returns `gex_collector.collection_symbols()` via the lazy engine import, defensive → `[]`), `_load_flow_series_for(symbol)` (opens a read-only `gex_history_db` conn, `load_flow_series(conn, symbol)`, closes; `[]` on failure), `_flow_now_ts()` (`int(time.time())`). Body:
```python
def run_flow_alerts(bus) -> None:
    """Detect + push + publish options-flow alerts (crossover + unusual activity) for
    the whole collected universe. Rides the 1-min GEX poll; best-effort, never raises."""
    try:
        cfg = flow_alerts.load_thresholds()
        if not cfg.get("enabled", True):
            return
        cd_env = bus.cache_get(_FLOW_COOLDOWN_KEY)
        today = _today_ct()
        cd_payload = cd_env.payload if (cd_env and isinstance(cd_env.payload, dict)) else {}
        # Date-scope the cooldown map (fresh each trading day).
        cooldowns = cd_payload.get("map", {}) if cd_payload.get("date") == today else {}
        now_ts = _flow_now_ts()
        push_cfg = push_notify.load_config()

        fresh = []
        for sym in _flow_alert_symbols():
            series = _load_flow_series_for(sym)
            for a in flow_alerts.detect_flow_alerts(sym, series, cfg, cooldowns, now_ts):
                fresh.append(a)

        if fresh:
            for a in fresh:
                try:
                    push_notify.send_flow_alert(a, config=push_cfg)
                except Exception:
                    log.exception("send_flow_alert degraded")
            # Append to the rolling published view (capped, date-scoped).
            env = bus.cache_get(CACHE_FLOW_ALERTS)
            prior = env.payload.get("alerts", []) if (env and isinstance(env.payload, dict)
                                                      and env.payload.get("date") == today) else []
            alerts = (prior + fresh)[-_FLOW_ALERTS_MAX:]
            bus.cache_set(CACHE_FLOW_ALERTS, {"date": today, "alerts": alerts},
                          event=EVENT_FLOW_ALERTS)
        # Persist the cooldown map every tick (even with no fresh alerts is fine).
        bus.cache_set(_FLOW_COOLDOWN_KEY, {"date": today, "map": cooldowns})
    except Exception:
        log.exception("run_flow_alerts degraded")
```
Wire it into `collect_gex_history(bus)` right after `publish_flow_skew(bus)`:
```python
        try:
            run_flow_alerts(bus)
        except Exception:
            log.exception("run_flow_alerts after collect degraded")
```
Import `flow_alerts` + `push_notify` at the top of `handlers.py` if not already (`push_notify` is; add `from services.options_svc import flow_alerts`).

**Step 4: Run → PASS** (`.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -q -k flow`).

**Step 5: Commit** `feat(flow-alerts): run_flow_alerts handler (detect → push → publish) on the 1-min tick`.

---

### Task 5: Webgui popup + Settings toggle

**Files:** Modify `webgui/main.py` (the `_tick`/`_guarded_compute` watcher), `webgui/alerts.py` (pure new-id diff), `webgui/app_settings.py` (`flow_alerts_enabled` default), `webgui/pages/settings.py` (the toggle). Test `webgui/tests/test_alerts.py` (append).

**Step 1: Write the failing pure test** for a new-id diff helper in `alerts.py`:
```python
def test_new_flow_alert_ids():
    from webgui import alerts
    view = {"alerts": [{"id": "A"}, {"id": "B"}]}
    new, acked = alerts.new_flow_alerts(view, set())
    assert [a["id"] for a in new] == ["A", "B"] and acked == {"A", "B"}
    # Already-acked → nothing new.
    new2, acked2 = alerts.new_flow_alerts(view, {"A", "B"})
    assert new2 == [] and acked2 == {"A", "B"}
```

**Step 2: Run → FAIL.**

**Step 3: Implement** `alerts.new_flow_alerts(view, acked)` (pure): read `view["alerts"]`, return `([alert dicts whose id not in acked], acked | all_ids)`. Then wire the watcher:
- In `main.py` `_guarded_compute` (the off-loop bus-read fn), read `bus_client.read("options:flow_alerts")`, call `alerts.new_flow_alerts(view, _FLOW_ACKED)` with a module-level `_FLOW_ACKED` set (single-user, like `_NAV_OPEN`), store back the acked set, and include the new alerts in the returned `decision` dict under `"flow"` — GATED on `app_settings` `flow_alerts_enabled`.
- In `_tick` (UI thread, after the await), if `decision.get("flow")`: for each new alert `play_alert(sound, volume)` (once) + `ui.notify(a["text"], color="green"/"red" by side, icon="insights")` + optional `notify_desktop`. Reuse the same sound/volume/desktop settings the scanner branch uses.
- Seed `_FLOW_ACKED` with the current view's ids on first build (like the scanner seed) so a page load doesn't replay the day's backlog.

**Step 4:** `app_settings.py` `DEFAULTS` — add `"flow_alerts_enabled": True`. `settings.py` — add a "Flow alerts" `ui.switch` bound to it in the alerts section.

**Step 5: Run** the pure test → PASS; `cd webgui && ..\.venv\Scripts\python -m pytest -q` → no new failures (render smoke tests exercise `_layout`/`_tick` wiring).

**Step 6: Commit** `feat(flow-alerts): in-app toast+chime popup + Settings toggle`.

---

### Task 6: Docs + verification

**Step 1:** Restart `options_svc` + the webgui. During RTH, verify end-to-end via a Redis-driven check: enqueue nothing — instead read `cache:options:flow_alerts` after a few ticks (or seed a synthetic crossover series into `gex_history.db` for a test symbol and confirm an alert publishes + a Discord/Telegram message arrives if creds are set). Off-hours, confirm `run_flow_alerts` no-ops cleanly (poll window closed).

**Step 2:** Update the root `CLAUDE.md` — a dated "Last updated" entry + a note under the push-notifications / Gamma sections describing the two alert types, the config file, and the delivery channels. Add `config/flow_alerts.toml` to the "config source of truth" list.

**Step 3: Commit** `docs(flow-alerts): record crossover + unusual-activity alerts`.

**Step 4:** Full suites green: `.venv\Scripts\python -m pytest services\options_svc -q` and `cd webgui && ..\.venv\Scripts\python -m pytest -q`.

---

## Notes for the implementer
- **DRY:** reuse `shared.notify.channels` senders (via `push_notify`), `load_flow_series`, `gex_collector.collection_symbols()`, the `play_alert`/`ui.notify`/`notify_desktop` webgui infra, and the `load_seen`/`save_seen`-style Redis dict pattern for the cooldown map.
- **YAGNI:** no premium-based spike, no historical baseline, no modal, no per-symbol config.
- **Defensive:** every server path is best-effort/guarded — a flow-alert failure must NEVER affect the GEX collection it rides on. The engine (`options-scanner`) stays push-free.
- **Tier rule:** the webgui imports only `nicegui` + `shared.bus` + `shared.contracts`; all detection/push stays in options_svc.
- **Cooldown map is date-scoped** so it resets each trading day (no cross-day suppression) and never grows unbounded.
