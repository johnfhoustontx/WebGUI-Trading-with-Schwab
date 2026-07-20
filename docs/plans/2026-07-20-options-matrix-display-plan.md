# Options Matrix Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `/options/matrix` tab (main item under Options) showing one row per
watchlist stock — spot, day %, intraday trend, call/put flow acceleration, P/C ratio,
net premium, GEX regime, signal & flow-alert counts, and a Buy/Neutral/Sell composite —
sortable and updating live.

**Architecture:** A new aggregator in `options_svc` publishes `cache:options:matrix`
from data it already owns (`gex_history.db` per-symbol series + stored gamma flip,
`cache:options:scan_day`, `cache:options:flow_alerts`). Pure derivation logic lives in a
new `services/options_svc/matrix.py`; I/O orchestration in `compute.build_matrix`; the
publish rides the existing 1-min GEX branch via `handlers.collect_gex_history`, with an
optional ~30 s live-spot overlay on the `refresh_header` tick. The webgui `/options/matrix`
page is a pure Tier-1 reader that version-polls and rebuilds a sortable `ui.table`.

**Tech Stack:** Python, pydantic contracts, `shared/bus` (Redis/fakeredis), NiceGUI +
Quasar table, pytest. Follow the app's TDD-by-layer + Tailwind-first conventions.

**Conventions to honor:**
- Run service tests **per folder** from the repo root: `.venv\Scripts\python -m pytest services\options_svc`.
- Run webgui tests from inside `webgui`: `cd webgui && ..\.venv\Scripts\python -m pytest -q`.
- Tailwind-first (no `.style()`); dynamic colors map from a **finite set** to fixed classes.
- Every service read is defensive (degrade, never raise). Mirror `flow_skew_view` /
  `publish_flow_skew` exactly.
- Commit after each task.

**Key facts confirmed from the codebase (do not re-derive):**
- `gex_history_db.load_flow_series(conn, symbol, d=None)` → list of
  `(ts, spot, call_vol, put_vol, call_prem, put_prem)`, chronological, `view='gex'`.
- `gex_history_db.load_date_with_grid(conn, symbol, view, date=None, since_ts=None)` → list of
  `(ts, spot, flip, top_pos_strike, top_neg_strike, net_total, grid_dict)`. **`flip` is a
  stored column (index 2)** — take the last row's `flip` for GEX regime; no recompute.
- `gex_history_db.connect(read_only=True)` — caller owns/closes. **No `repo_paths` constant;**
  just call `connect()`.
- Row universe: `handlers._flow_alert_symbols()` = `gex_collector.collection_symbols()` minus
  `$VIX`. Import `gex_collector` **lazily**.
- `cache:options:scan_day` payload = `{date, signals_0dte, signals_swing, signals_directional, [truncated]}`;
  each signal dict has `id` + `symbol`. Gate on `date`.
- `cache:options:flow_alerts` payload = `{date, alerts:[{id, symbol, type, side, text, ...}]}`,
  rolling cap ~50.
- Publish site: `handlers.collect_gex_history(bus)` (handlers.py:655), add a best-effort block
  after `run_flow_alerts(bus)`. No `scheduler.py`/`app.py` change needed.
- `bus.cache_get(key)` → `CacheEnvelope` (`.payload`, `.version`, `.ts`) or `None`.
- `bus.cache_set(key, payload_dict, event=None, skip_unchanged=False)` → int version; publishes when `event=` given.
- Batched quotes: `_proxy.schwab_py_client.get_quotes(list).json()` → dict keyed by symbol.
- `_today_ct()` from `shared.notify.channels` (already imported in handlers).
- `scheduler.active_session_date(now=None)` → the session date to display.
- Webgui bus helpers: `bus_client.read_full(view)`, `read_version(view)`, `read(view)` (view = `"options:matrix"`).
- Nav: append one tuple to `OPTIONS_CHILDREN` (main.py:214); route mirrors `options_rescue_page` (main.py:1153).
- `@guard`/`guard_async` from `pages.ui_guard`; theme tokens from `pages.options.theme`.

---

## Task 1: `MatrixSnapshot` contract

**Files:**
- Modify: `shared/contracts/options.py` (add class near the other view models)
- Test: `shared/contracts/tests/test_options.py` (add a test; create if the file's test dir differs — check existing test layout first)

**Step 1: Write the failing test**

```python
def test_matrix_snapshot_roundtrip_and_defaults():
    from shared.contracts.options import MatrixSnapshot
    m = MatrixSnapshot(date="2026-07-20", session_date="2026-07-20", ts="2026-07-20T09:15:00",
                       rows=[{"symbol": "SPY", "signal": "buy"}])
    assert m.rows[0]["symbol"] == "SPY"
    # tolerant defaults so older Redis payloads still validate
    assert MatrixSnapshot().rows == []
    assert MatrixSnapshot().error is None
    dumped = m.to_json()
    assert MatrixSnapshot.from_json(dumped).rows == m.rows
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest shared\contracts -k matrix_snapshot -v`
Expected: FAIL (ImportError: cannot import name 'MatrixSnapshot').

**Step 3: Write minimal implementation**

Mirror the `RescueAdvisory` pattern (subclass `_Base`, all fields defaulted, loose `list[dict]`):

```python
class MatrixSnapshot(_Base):
    """cache:options:matrix — one row per watchlist symbol for the Matrix Display tab."""
    date: str | None = None            # CT date the row counts are scoped to
    session_date: str | None = None    # gex session date used for series/flip
    ts: str | None = None
    rows: list[dict] = []              # heterogeneous per-symbol row dicts (see matrix.build_rows)
    error: str | None = None
```

**Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python -m pytest shared\contracts -k matrix_snapshot -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add shared/contracts/options.py shared/contracts/tests/test_options.py
git commit -m "feat(contracts): MatrixSnapshot for the options matrix view"
```

---

## Task 2: Pure matrix derivation logic (`services/options_svc/matrix.py`)

This is the load-bearing, fully-testable core. **No I/O** — every function takes plain
data. All thresholds are named constants (tunable).

**Files:**
- Create: `services/options_svc/matrix.py`
- Test: `services/options_svc/tests/test_matrix.py`

**Step 1: Write the failing tests**

```python
import services.options_svc.matrix as m

# ---- intraday_trend ----
def test_intraday_trend_up_when_spot_rising():
    # series: (ts, spot) rising ~0.5% over 15 min
    series = [(0, 100.0), (300, 100.1), (600, 100.3), (900, 100.5)]
    state, direction = m.intraday_trend(series, now_ts=900)
    assert direction > 0
    assert state in ("up", "strong_up")

def test_intraday_trend_flat_on_tiny_move():
    series = [(0, 100.0), (900, 100.02)]
    state, direction = m.intraday_trend(series, now_ts=900)
    assert state == "flat"
    assert abs(direction) < 0.2

def test_intraday_trend_neutral_without_two_points():
    assert m.intraday_trend([], now_ts=0) == ("flat", 0.0)
    assert m.intraday_trend([(0, None)], now_ts=0) == ("flat", 0.0)

# ---- flow_acceleration ----
def test_flow_acceleration_hot_when_recent_slope_exceeds_average():
    # cumulative premium: slow early, fast in the last 15 min
    series = [(0, 0.0), (600, 100.0), (1200, 200.0), (1500, 1000.0), (1800, 2000.0)]
    state, ratio = m.flow_acceleration(series, now_ts=1800, lookback_s=900)
    assert state == "hot"
    assert ratio > m._ACCEL_HOT

def test_flow_acceleration_steady_and_flat_edges():
    steady = [(0, 0.0), (900, 100.0), (1800, 200.0)]
    assert m.flow_acceleration(steady, now_ts=1800)[0] == "steady"
    assert m.flow_acceleration([], now_ts=0) == ("flat", 0.0)
    assert m.flow_acceleration([(0, 0.0), (900, 0.0)], now_ts=900) == ("flat", 0.0)

# ---- composite_signal ----
def test_composite_buy_when_trend_up_and_calls_dominant():
    sig, strength = m.composite_signal(trend_dir=0.8, call_state="hot", put_state="steady",
                                       call_prem=300.0, put_prem=100.0)
    assert sig == "buy"
    assert strength >= 1

def test_composite_sell_when_trend_down_and_puts_dominant():
    sig, _ = m.composite_signal(trend_dir=-0.7, call_state="steady", put_state="hot",
                                call_prem=80.0, put_prem=260.0)
    assert sig == "sell"

def test_composite_neutral_on_conflict():
    sig, _ = m.composite_signal(trend_dir=0.6, call_state="steady", put_state="hot",
                                call_prem=200.0, put_prem=200.0)
    assert sig == "neutral"

def test_composite_neutral_on_no_data():
    sig, strength = m.composite_signal(trend_dir=0.0, call_state="flat", put_state="flat",
                                       call_prem=0.0, put_prem=0.0)
    assert sig == "neutral" and strength == 0

# ---- pc_ratio / net_premium ----
def test_pc_ratio_and_net_premium():
    assert m.pc_ratio(call_prem=200.0, put_prem=100.0) == 0.5
    assert m.pc_ratio(call_prem=0.0, put_prem=100.0) is None   # undefined
    assert m.net_premium_m(call_prem=3_000_000.0, put_prem=1_000_000.0) == 2.0  # $M

# ---- gex_regime ----
def test_gex_regime_above_below_na():
    assert m.gex_regime(spot=105.0, flip=100.0) == "above"
    assert m.gex_regime(spot=95.0, flip=100.0) == "below"
    assert m.gex_regime(spot=None, flip=100.0) == "na"
    assert m.gex_regime(spot=100.0, flip=None) == "na"

# ---- hotness ----
def test_hotness_rewards_signals_alerts_and_conviction():
    hot = m.hotness(n_signals=4, n_alerts=3, signal_strength=3)
    cold = m.hotness(n_signals=0, n_alerts=0, signal_strength=0)
    assert hot > cold

# ---- build_rows (pure assembler) ----
def test_build_rows_assembles_one_row_per_symbol():
    raw = {
        "SPY": {"series": [(0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
                           (900, 100.6, 30, 8, 3_000_000.0, 800_000.0)],
                "flip": 100.0},
    }
    rows = m.build_rows(raw, scan_counts={"SPY": 3}, alert_counts={"SPY": 2}, now_ts=900)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["n_signals"] == 3 and r["n_alerts"] == 2
    assert r["signal"] in ("buy", "neutral", "sell")
    assert r["gex_regime"] == "above"          # spot 100.6 > flip 100.0
    assert isinstance(r["hotness"], (int, float))
    assert r["day_pct"] is not None            # (100.6-100.0)/100.0

def test_build_rows_degrades_symbol_with_no_series():
    rows = m.build_rows({"AAPL": {"series": [], "flip": None}},
                        scan_counts={}, alert_counts={}, now_ts=0)
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["spot"] is None
    assert rows[0]["signal"] == "neutral"
    assert rows[0]["n_signals"] == 0
```

**Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_matrix.py -v`
Expected: FAIL (ModuleNotFoundError: services.options_svc.matrix).

**Step 3: Write the implementation**

```python
"""Pure derivation logic for the Options Matrix Display (cache:options:matrix).

No I/O: every function takes plain data so it is unit-tested in isolation. The
I/O orchestration lives in services/options_svc/compute.build_matrix.
"""
from __future__ import annotations

# ---- tunable thresholds ----
_TREND_LOOKBACK_S = 900        # 15 min recent-move window
_TREND_MILD = 0.0012           # 0.12% move → mild trend
_TREND_STRONG = 0.004          # 0.40% move → strong trend
_ACCEL_LOOKBACK_S = 900        # 15 min recent-slope window
_ACCEL_HOT = 1.5               # recent slope ≥1.5× day-avg → accelerating
_ACCEL_COOL = 0.6              # ≤0.6× → cooling
_SIG_BUY = 0.22                # composite score cut for buy/sell
_SIG_STRONG = 0.55             # |score| for a strong tier
_W_TREND, _W_FLOW, _W_ACCEL = 0.50, 0.35, 0.15


def _spot_points(series):
    """[(ts, spot)] with non-null spots, from a flow series row tuple."""
    return [(row[0], row[1]) for row in series if row[1] is not None]


def intraday_trend(spot_series, now_ts):
    """spot_series = [(ts, spot)]; return (state, direction[-1..1]).

    state ∈ {strong_up, up, flat, down, strong_down}. direction is the % move over the
    trailing window normalized to _TREND_STRONG and clamped to [-1, 1].
    """
    pts = [(t, s) for t, s in spot_series if s is not None]
    if len(pts) < 2:
        return ("flat", 0.0)
    cur_t, cur = pts[-1]
    ref = pts[0][1]
    cutoff = cur_t - _TREND_LOOKBACK_S
    for t, s in pts:
        if t <= cutoff:
            ref = s
    if not ref:
        return ("flat", 0.0)
    pct = (cur - ref) / ref
    direction = max(-1.0, min(1.0, pct / _TREND_STRONG))
    ap = abs(pct)
    if ap < _TREND_MILD:
        state = "flat"
    elif ap < _TREND_STRONG:
        state = "up" if pct > 0 else "down"
    else:
        state = "strong_up" if pct > 0 else "strong_down"
    return (state, direction)


def flow_acceleration(prem_series, now_ts, lookback_s=_ACCEL_LOOKBACK_S):
    """prem_series = [(ts, cumulative_premium)] (monotonic).

    Return (state, ratio) where ratio = recent-slope / day-average-slope.
    state ∈ {hot, cool, steady, flat}. flat = no premium accrued or too few points.
    """
    pts = [(t, p) for t, p in prem_series if p is not None]
    if len(pts) < 2:
        return ("flat", 0.0)
    first_t, first_p = pts[0]
    last_t, last_p = pts[-1]
    total_span = last_t - first_t
    total_added = last_p - first_p
    if total_span <= 0 or total_added <= 0:
        return ("flat", 0.0)
    avg_slope = total_added / total_span
    ref_t, ref_p = first_t, first_p
    cutoff = last_t - lookback_s
    for t, p in pts:
        if t <= cutoff:
            ref_t, ref_p = t, p
    recent_span = last_t - ref_t
    if recent_span <= 0:
        return ("steady", 1.0)
    recent_slope = (last_p - ref_p) / recent_span
    ratio = recent_slope / avg_slope if avg_slope > 0 else 0.0
    if ratio >= _ACCEL_HOT:
        state = "hot"
    elif ratio <= _ACCEL_COOL:
        state = "cool"
    else:
        state = "steady"
    return (state, ratio)


def pc_ratio(call_prem, put_prem):
    """Put/Call premium ratio; None when calls are zero (undefined)."""
    if not call_prem:
        return None
    return round(put_prem / call_prem, 2)


def net_premium_m(call_prem, put_prem):
    """(call - put) premium in $M."""
    return round((call_prem - put_prem) / 1_000_000.0, 2)


def gex_regime(spot, flip):
    if spot is None or flip is None:
        return "na"
    return "above" if spot >= flip else "below"


def composite_signal(trend_dir, call_state, put_state, call_prem, put_prem):
    """Return (signal, strength). signal ∈ {buy, neutral, sell}; strength ∈ {0,1,2}."""
    total = (call_prem or 0.0) + (put_prem or 0.0)
    flow_dir = ((call_prem - put_prem) / total) if total > 0 else 0.0
    accel_dir = 0.0
    if call_state == "hot" and put_state != "hot":
        accel_dir = 1.0
    elif put_state == "hot" and call_state != "hot":
        accel_dir = -1.0
    score = _W_TREND * trend_dir + _W_FLOW * flow_dir + _W_ACCEL * accel_dir
    if total <= 0 and trend_dir == 0.0:
        return ("neutral", 0)
    if score >= _SIG_BUY:
        sig = "buy"
    elif score <= -_SIG_BUY:
        sig = "sell"
    else:
        return ("neutral", 1 if abs(score) > 0.1 else 0)
    strength = 2 if abs(score) >= _SIG_STRONG else 1
    return (sig, strength)


def hotness(n_signals, n_alerts, signal_strength):
    """Sort key so opportunities float to the top (higher = hotter)."""
    return 2 * n_signals + 2 * n_alerts + 3 * signal_strength


def build_rows(raw, scan_counts, alert_counts, now_ts):
    """raw = {symbol: {"series": [flow-row tuples], "flip": float|None}}.

    Returns a list of per-symbol row dicts (order = raw insertion order).
    """
    rows = []
    for symbol, blob in raw.items():
        series = blob.get("series") or []
        flip = blob.get("flip")
        spots = _spot_points(series)
        spot = spots[-1][1] if spots else None
        open_spot = spots[0][1] if spots else None
        day_pct = ((spot - open_spot) / open_spot * 100.0) if (spot and open_spot) else None

        t_state, t_dir = intraday_trend(spots, now_ts)
        call_series = [(r[0], r[4]) for r in series]   # (ts, call_prem)
        put_series = [(r[0], r[5]) for r in series]    # (ts, put_prem)
        c_state, _ = flow_acceleration(call_series, now_ts)
        p_state, _ = flow_acceleration(put_series, now_ts)
        call_prem = series[-1][4] if series else 0.0
        put_prem = series[-1][5] if series else 0.0

        sig, strength = composite_signal(t_dir, c_state, p_state, call_prem, put_prem)
        n_sig = int(scan_counts.get(symbol, 0))
        n_alr = int(alert_counts.get(symbol, 0))

        rows.append({
            "symbol": symbol,
            "spot": round(spot, 2) if spot else None,
            "day_pct": round(day_pct, 2) if day_pct is not None else None,
            "trend_state": t_state,
            "trend_dir": round(t_dir, 3),
            "call_accel": c_state,
            "put_accel": p_state,
            "pc_ratio": pc_ratio(call_prem, put_prem),
            "net_prem_m": net_premium_m(call_prem, put_prem),
            "flip": round(flip, 2) if flip else None,
            "gex_regime": gex_regime(spot, flip),
            "n_signals": n_sig,
            "n_alerts": n_alr,
            "signal": sig,
            "signal_strength": strength,
            "hotness": hotness(n_sig, n_alr, strength),
        })
    return rows
```

**Step 4: Run to verify PASS**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_matrix.py -v`
Expected: PASS (all).

**Step 5: Commit**

```bash
git add services/options_svc/matrix.py services/options_svc/tests/test_matrix.py
git commit -m "feat(options): pure matrix derivation logic (trend/accel/composite/rows)"
```

---

## Task 3: `compute.build_matrix` — I/O orchestration (DB-only)

Loads the per-symbol flow series + latest gamma flip from `gex_history.db`, takes the
signal/alert counts passed in, and calls `matrix.build_rows`. **No proxy** (spot comes
from the series; a live-quote overlay is Task 5). Mirror `flow_skew_view`'s
connect/loop/try-finally shape.

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_compute.py` (add cases)

**Step 1: Write the failing test** (monkeypatch the gex_history loader + symbol list)

```python
def test_build_matrix_assembles_rows(monkeypatch):
    from services.options_svc import compute
    # fake gex_history module surface used by build_matrix
    class FakeGH:
        @staticmethod
        def connect(read_only=False): return object()
        @staticmethod
        def load_flow_series(conn, symbol, d=None):
            return [(0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
                    (900, 100.7, 30, 8, 3_000_000.0, 800_000.0)]
        @staticmethod
        def load_date_with_grid(conn, symbol, view, date=None, since_ts=None):
            return [(900, 100.7, 100.0, 0, 0, 0.0, {})]   # flip=100.0
    monkeypatch.setattr(compute, "_matrix_symbols", lambda: ["SPY"])
    monkeypatch.setattr(compute, "_gh", lambda: FakeGH)      # loader accessor (see impl)

    out = compute.build_matrix(
        scan_day={"date": "2026-07-20", "signals_0dte": [{"id": "a", "symbol": "SPY"}],
                  "signals_swing": [], "signals_directional": []},
        flow_alerts={"date": "2026-07-20", "alerts": [{"id": "x", "symbol": "SPY"}]},
        today="2026-07-20", session_date="2026-07-20", now_ts=900)
    assert out["error"] is None
    assert len(out["rows"]) == 1
    r = out["rows"][0]
    assert r["symbol"] == "SPY" and r["n_signals"] == 1 and r["n_alerts"] == 1

def test_build_matrix_counts_only_todays_scan_day():
    from services.options_svc import compute
    # scan_day date mismatch → counts drop to 0 (still one row via symbol list)
    counts = compute._count_scan_signals({"date": "1999-01-01", "signals_0dte":
                 [{"id": "a", "symbol": "SPY"}], "signals_swing": [], "signals_directional": []},
                 today="2026-07-20")
    assert counts == {}

def test_count_helpers_group_by_symbol():
    from services.options_svc import compute
    sc = compute._count_scan_signals({"date": "2026-07-20",
            "signals_0dte": [{"id": 1, "symbol": "SPY"}, {"id": 2, "symbol": "SPY"}],
            "signals_swing": [{"id": 3, "symbol": "QQQ"}], "signals_directional": []},
            today="2026-07-20")
    assert sc == {"SPY": 2, "QQQ": 1}
    al = compute._count_flow_alerts({"date": "2026-07-20",
            "alerts": [{"symbol": "SPY"}, {"symbol": "SPY"}, {"symbol": "QQQ"}]},
            today="2026-07-20")
    assert al == {"SPY": 2, "QQQ": 1}
```

**Step 2: Run to verify FAIL**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k build_matrix -v`
Expected: FAIL (AttributeError: build_matrix).

**Step 3: Implement** (add to `compute.py`; import `matrix` lazily to avoid module-name issues)

```python
_MATRIX_DAY_LISTS = ("signals_0dte", "signals_swing", "signals_directional")


def _gh():
    """Lazy accessor for the gex_history_db module (OPTIONS_SCANNER already on sys.path)."""
    import gex_history_db as gh
    return gh


def _matrix_symbols():
    """Watchlist row universe: collection_symbols() minus $VIX. Defensive → []."""
    try:
        import gex_collector
        return [s for s in gex_collector.collection_symbols() if s != "$VIX"]
    except Exception:
        log.exception("matrix symbol list degraded")
        return []


def _count_scan_signals(scan_day, today):
    payload = scan_day or {}
    if payload.get("date") != today:
        return {}
    counts = {}
    for key in _MATRIX_DAY_LISTS:
        for sig in payload.get(key) or []:
            sym = sig.get("symbol")
            if sym:
                counts[sym] = counts.get(sym, 0) + 1
    return counts


def _count_flow_alerts(flow_alerts, today):
    payload = flow_alerts or {}
    if payload.get("date") != today:
        return {}
    counts = {}
    for a in payload.get("alerts") or []:
        sym = a.get("symbol")
        if sym:
            counts[sym] = counts.get(sym, 0) + 1
    return counts


def build_matrix(scan_day, flow_alerts, today, session_date, now_ts):
    """Assemble cache:options:matrix payload. Defensive → {"rows": [], "error": ...}."""
    import services.options_svc.matrix as mx
    scan_counts = _count_scan_signals(scan_day, today)
    alert_counts = _count_flow_alerts(flow_alerts, today)
    symbols = _matrix_symbols()
    raw = {}
    conn = None
    try:
        gh = _gh()
        conn = gh.connect(read_only=True)
        for sym in symbols:
            try:
                series = gh.load_flow_series(conn, sym, session_date)
                grid_rows = gh.load_date_with_grid(conn, sym, "gex", session_date)
                flip = grid_rows[-1][2] if grid_rows else None
                raw[sym] = {"series": series, "flip": flip}
            except Exception:
                log.exception("matrix row degraded for %s", sym)
                raw[sym] = {"series": [], "flip": None}
    except Exception:
        log.exception("build_matrix degraded")
        return {"date": today, "session_date": session_date, "ts": _now_iso(),
                "rows": [], "error": "matrix unavailable"}
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass
    rows = mx.build_rows(raw, scan_counts, alert_counts, now_ts)
    rows.sort(key=lambda r: r["hotness"], reverse=True)
    return {"date": today, "session_date": session_date, "ts": _now_iso(),
            "rows": rows, "error": None}
```

> Check for an existing `_now_iso()`/timestamp helper in compute.py; reuse it. If none,
> use `_dt.datetime.now(_TZ).isoformat()` matching the module's timezone import.

**Step 4: Run to verify PASS**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k "build_matrix or count_" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(options): compute.build_matrix orchestration + per-symbol count helpers"
```

---

## Task 4: Publish `cache:options:matrix` from the 1-min GEX branch

**Files:**
- Modify: `services/options_svc/handlers.py`
- Test: `services/options_svc/tests/test_handlers.py`

**Step 1: Write the failing test** (fake bus; verify a matrix payload is cached)

```python
def test_publish_matrix_caches_view(fake_bus):
    from services.options_svc import handlers, compute
    # seed the caches build_matrix reads via the bus
    fake_bus.cache_set(handlers.CACHE_SCAN_DAY, {"date": handlers._today_ct(),
        "signals_0dte": [{"id": "a", "symbol": "SPY"}], "signals_swing": [],
        "signals_directional": []})
    fake_bus.cache_set(handlers.CACHE_FLOW_ALERTS, {"date": handlers._today_ct(),
        "alerts": [{"id": "x", "symbol": "SPY"}]})
    # avoid real DB: stub compute.build_matrix
    def fake_build(scan_day, flow_alerts, today, session_date, now_ts):
        return {"date": today, "session_date": session_date, "ts": "t",
                "rows": [{"symbol": "SPY", "n_signals": 1, "hotness": 5}], "error": None}
    handlers.compute.build_matrix = fake_build          # monkeypatch acceptable in test
    handlers.publish_matrix(fake_bus)
    env = fake_bus.cache_get(handlers.CACHE_MATRIX)
    assert env is not None
    assert env.payload["rows"][0]["symbol"] == "SPY"
```

(Confirm the exact scan-day cache constant name in handlers — it is `CACHE_SCAN_DAY` if
present, else read the key `"cache:options:scan_day"` directly. Adjust the test/impl to the
real constant.)

**Step 2: Run to verify FAIL**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -k publish_matrix -v`
Expected: FAIL (AttributeError: CACHE_MATRIX / publish_matrix).

**Step 3: Implement**

Add constants near `CACHE_FLOW_SKEW` (handlers.py ~line 222):

```python
CACHE_MATRIX = "cache:options:matrix"
EVENT_MATRIX = "events:options:matrix"
```

Add the publisher (mirror `publish_flow_skew`, handlers.py:689):

```python
def publish_matrix(bus) -> None:
    try:
        today = _today_ct()
        session_date = scheduler.active_session_date()
        now_ts = int(_time.time())
        scan_day = _cache_payload(bus, "cache:options:scan_day")
        flow_alerts = _cache_payload(bus, CACHE_FLOW_ALERTS)
        view = compute.build_matrix(scan_day, flow_alerts, today,
                                    session_date, now_ts)
        bus.cache_set(CACHE_MATRIX, view, event=EVENT_MATRIX, skip_unchanged=True)
    except Exception:
        log.exception("publish_matrix degraded")
```

Add a tiny helper if one doesn't already exist (check handlers for an existing
`cache_get(...).payload` unwrap — reuse it if so):

```python
def _cache_payload(bus, key):
    env = bus.cache_get(key)
    return env.payload if env is not None else None
```

`scheduler.active_session_date()` and `import time as _time` — confirm `scheduler` is
imported in handlers (it is used elsewhere); add `import time as _time` at the top if absent.

Wire into `collect_gex_history` (handlers.py:655), a third best-effort block **after**
`run_flow_alerts(bus)`:

```python
        try:
            run_flow_alerts(bus)
        except Exception:
            log.exception("run_flow_alerts after collect degraded")
        try:
            publish_matrix(bus)
        except Exception:
            log.exception("publish_matrix after collect degraded")
```

**Step 4: Run to verify PASS + no regressions**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -k publish_matrix -v`
Then the whole service: `.venv\Scripts\python -m pytest services\options_svc -q`
Expected: PASS (baseline: 629/2 known `test_expected_move` fails — do not touch those).

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(options): publish cache:options:matrix on the 1-min GEX branch"
```

---

## Task 5: ~30 s live-spot / day-% overlay (freshness)

Overlay live quote spot + day % onto the already-published matrix on the `refresh_header`
tick, so Spot/Day% feel live without re-decoding grids. Cheap: one batched `get_quotes`
+ field update + republish (`skip_unchanged`).

**Files:**
- Modify: `services/options_svc/handlers.py` (add `refresh_matrix_spots`, call from the header handler)
- Modify: `services/options_svc/compute.py` (pure `apply_live_spots(view, quotes_raw)`)
- Test: `services/options_svc/tests/test_compute.py` + `test_handlers.py`

**Step 1: Write the failing test** (pure overlay)

```python
def test_apply_live_spots_updates_spot_and_daypct():
    from services.options_svc import compute
    view = {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.5, "signal": "buy",
                      "n_signals": 2, "n_alerts": 1, "signal_strength": 1, "hotness": 9}]}
    quotes = {"SPY": {"quote": {"lastPrice": 101.0, "netPercentChange": 1.2}}}
    out = compute.apply_live_spots(view, quotes)
    r = out["rows"][0]
    assert r["spot"] == 101.0
    assert r["day_pct"] == 1.2
    # untouched fields preserved
    assert r["signal"] == "buy" and r["n_signals"] == 2

def test_apply_live_spots_ignores_missing_symbol():
    from services.options_svc import compute
    view = {"rows": [{"symbol": "SPY", "spot": 100.0, "day_pct": 0.5}]}
    out = compute.apply_live_spots(view, {})     # no quote → unchanged
    assert out["rows"][0]["spot"] == 100.0
```

**Step 2: Run to verify FAIL.**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k apply_live_spots -v`

**Step 3: Implement**

In `compute.py` (reuse the quote-field extractor the header uses — confirm the exact
schwab quote keys for last price + percent change; `refresh_header` already does this via
`quote_last`, so reuse `quote_last(quotes_raw, sym)` and the percent-change field it reads):

```python
def apply_live_spots(view, quotes_raw):
    """Overlay live spot + day% onto a matrix view in place (defensive)."""
    rows = (view or {}).get("rows") or []
    for r in rows:
        q = (quotes_raw or {}).get(r.get("symbol"))
        if not q:
            continue
        quote = q.get("quote") or {}
        last = quote.get("lastPrice")
        pct = quote.get("netPercentChange")
        if last is not None:
            r["spot"] = round(last, 2)
        if pct is not None:
            r["day_pct"] = round(pct, 2)
    return view
```

In `handlers.py`:

```python
def refresh_matrix_spots(bus) -> None:
    try:
        env = bus.cache_get(CACHE_MATRIX)
        if env is None or not env.payload.get("rows"):
            return
        symbols = [r["symbol"] for r in env.payload["rows"]]
        raw = _proxy.schwab_py_client.get_quotes(symbols).json() or {}
        view = compute.apply_live_spots(dict(env.payload), raw)
        bus.cache_set(CACHE_MATRIX, view, event=EVENT_MATRIX, skip_unchanged=True)
    except Exception:
        log.exception("refresh_matrix_spots degraded")
```

Confirm `_proxy` is imported in handlers (it is used elsewhere / via compute). If handlers
lacks a direct `_proxy`, move the quote fetch into a small `compute.matrix_quotes(symbols)`
and call that instead. Wire into `refresh_header` (handlers.py:334):

```python
def refresh_header(bus) -> None:
    data = compute.refresh_header()
    bus.cache_set(CACHE_HEADER, data, event=EVENT_HEADER, skip_unchanged=True)
    try:
        refresh_matrix_spots(bus)
    except Exception:
        log.exception("refresh_matrix_spots after header degraded")
```

**Step 4: Run to verify PASS** (+ full service suite green vs baseline).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/handlers.py services/options_svc/tests/
git commit -m "feat(options): ~30s live spot/day% overlay on the matrix view"
```

---

## Task 6: Webgui page pure builders (`pages/options/matrix.py`)

Pure, NiceGUI-free functions first (columns, display rows with stamped class fields, color
maps). TDD them without a server.

**Files:**
- Create: `webgui/pages/options/matrix.py` (pure builders only in this task)
- Test: `webgui/tests/test_options_matrix.py`

**Step 1: Write the failing tests**

```python
from pages.options import matrix

def test_matrix_columns_are_sortable():
    cols = matrix.matrix_columns()
    fields = {c["field"] for c in cols}
    for f in ("symbol", "spot", "day_pct", "n_signals", "n_alerts", "signal", "hotness"):
        assert f in fields
    assert all(c["sortable"] for c in cols)

def test_matrix_rows_formats_and_stamps_classes():
    payload = {"rows": [{
        "symbol": "SPY", "spot": 101.0, "day_pct": 1.2, "trend_state": "up",
        "trend_dir": 0.6, "call_accel": "hot", "put_accel": "cool", "pc_ratio": 0.5,
        "net_prem_m": 2.0, "flip": 100.0, "gex_regime": "above", "n_signals": 3,
        "n_alerts": 2, "signal": "buy", "signal_strength": 2, "hotness": 12}]}
    rows = matrix.matrix_rows(payload)
    r = rows[0]
    assert r["symbol"] == "SPY"
    assert r["_signal_class"]                      # buy → a green class
    assert r["_daypct_class"]
    assert r["_regime_class"]
    assert r["signal_label"] == "Buy"

def test_signal_class_maps_all_states():
    for s in ("buy", "neutral", "sell", "bogus"):
        assert isinstance(matrix.signal_class(s), str) and matrix.signal_class(s)

def test_daypct_class_sign():
    assert matrix.daypct_class(1.0) != matrix.daypct_class(-1.0)
    assert matrix.daypct_class(None) == matrix.daypct_class(0.0)
```

**Step 2: Run to verify FAIL**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_options_matrix.py -v`
Expected: FAIL (ModuleNotFoundError).

**Step 3: Implement pure builders**

```python
"""Options Matrix Display — Tier-1 reader of cache:options:matrix.

Pure builders (columns/rows/color maps) are module-level and NiceGUI-free for testing.
render() (Task 7) mounts the sortable table and version-polls.
"""
from __future__ import annotations

VIEW = "options:matrix"

_SIGNAL_CLASS = {
    "buy": "bg-emerald-600/80 text-white",
    "neutral": "bg-slate-600/40 text-slate-200",
    "sell": "bg-rose-600/80 text-white",
}
_ALL_SIGNAL = " ".join(dict.fromkeys(" ".join(_SIGNAL_CLASS.values()).split()))

_REGIME_CLASS = {
    "above": "text-emerald-400",
    "below": "text-rose-400",
    "na": "text-slate-500",
}
_TREND_CLASS = {
    "strong_up": "text-emerald-400", "up": "text-emerald-300",
    "flat": "text-slate-400",
    "down": "text-rose-300", "strong_down": "text-rose-400",
}
_TREND_ARROW = {
    "strong_up": "▲▲", "up": "▲", "flat": "▬", "down": "▼", "strong_down": "▼▼",
}
_ACCEL_CLASS = {"hot": "text-emerald-400", "cool": "text-rose-400",
                "steady": "text-slate-400", "flat": "text-slate-500"}
_ACCEL_ARROW = {"hot": "▲", "cool": "▼", "steady": "▬", "flat": "·"}
_SIGNAL_LABEL = {"buy": "Buy", "neutral": "Neutral", "sell": "Sell"}


def signal_class(s):
    return _SIGNAL_CLASS.get(s, _SIGNAL_CLASS["neutral"])


def daypct_class(v):
    if not v:
        return "text-slate-400"
    return "text-emerald-400" if v > 0 else "text-rose-400"


def matrix_columns():
    spec = [
        ("symbol", "Ticker"), ("spot", "Spot"), ("day_pct", "Day %"),
        ("trend", "Trend"), ("call_accel_disp", "Call"), ("put_accel_disp", "Put"),
        ("pc_ratio", "P/C"), ("net_prem_m", "Net $M"), ("gex_regime", "GEX"),
        ("n_signals", "Sig"), ("n_alerts", "Flow"), ("signal_label", "Signal"),
        ("hotness", "Hot"),
    ]
    return [{"name": f, "label": l, "field": f, "sortable": True, "align": "left"}
            for f, l in spec]


def matrix_rows(payload):
    rows = []
    for r in (payload or {}).get("rows") or []:
        t_state = r.get("trend_state", "flat")
        rows.append({
            "symbol": r.get("symbol", ""),
            "spot": r.get("spot"),
            "day_pct": r.get("day_pct"),
            "_daypct_class": daypct_class(r.get("day_pct")),
            "trend": _TREND_ARROW.get(t_state, "▬"),
            "_trend_class": _TREND_CLASS.get(t_state, "text-slate-400"),
            "call_accel_disp": _ACCEL_ARROW.get(r.get("call_accel"), "·"),
            "_call_class": _ACCEL_CLASS.get(r.get("call_accel"), "text-slate-500"),
            "put_accel_disp": _ACCEL_ARROW.get(r.get("put_accel"), "·"),
            "_put_class": _ACCEL_CLASS.get(r.get("put_accel"), "text-slate-500"),
            "pc_ratio": r.get("pc_ratio"),
            "net_prem_m": r.get("net_prem_m"),
            "gex_regime": r.get("gex_regime", "na"),
            "_regime_class": _REGIME_CLASS.get(r.get("gex_regime"), "text-slate-500"),
            "n_signals": r.get("n_signals", 0),
            "n_alerts": r.get("n_alerts", 0),
            "signal_label": _SIGNAL_LABEL.get(r.get("signal"), "Neutral"),
            "_signal_class": signal_class(r.get("signal")),
            "hotness": r.get("hotness", 0),
        })
    return rows
```

**Step 4: Run to verify PASS**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_options_matrix.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/matrix.py webgui/tests/test_options_matrix.py
git commit -m "feat(webgui): matrix page pure builders (columns/rows/color maps)"
```

---

## Task 7: Webgui `render()` — sortable table + in-place version-poll

**Files:**
- Modify: `webgui/pages/options/matrix.py` (add `render()`)

**Step 1: Implement** (no new unit test — render smoke is covered by the shell test in Task 8;
follow the market.py poll pattern + scanner.py slot pattern)

```python
import bus_client
from nicegui import run, ui
from pages.ui_guard import guard_async
from pages.options.theme import QUASAR_INTERNAL_CSS, PAGE, CARD, EYEBROW, LABEL

_SIGNAL_SLOT = r'''
  <q-td :props="props">
    <q-badge :class="props.row._signal_class + ' px-2 py-1'" :label="props.value"/>
  </q-td>
'''
_DAYPCT_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._daypct_class">{{ props.value == null ? '—' : props.value + '%' }}</span>
  </q-td>
'''
_TREND_SLOT = r'''
  <q-td :props="props"><span :class="props.row._trend_class">{{ props.value }}</span></q-td>
'''
_CALL_SLOT = r'''
  <q-td :props="props"><span :class="props.row._call_class">{{ props.value }}</span></q-td>
'''
_PUT_SLOT = r'''
  <q-td :props="props"><span :class="props.row._put_class">{{ props.value }}</span></q-td>
'''
_REGIME_SLOT = r'''
  <q-td :props="props"><span :class="props.row._regime_class">{{ props.value }}</span></q-td>
'''


def render() -> None:
    ui.add_css(QUASAR_INTERNAL_CSS)
    state = {"version": None}
    with ui.column().classes(f"calc-v2 {PAGE} w-full gap-3"):
        ui.label("Matrix").classes(f"text-h6 {LABEL}")
        ui.label("Every watchlist stock at a glance — sorted by hotness").classes(EYEBROW)
        with ui.column().classes(f"{CARD} w-full gap-2"):
            status = ui.label("Waiting for the options service…").classes(EYEBROW)
            table = ui.table(columns=matrix_columns(), rows=[], row_key="symbol",
                             pagination={"rowsPerPage": 0}) \
                .classes("w-full matrix-table") \
                .props("dense :sort-by=\"'hotness'\" :descending=\"true\"")
            table.add_slot("body-cell-signal_label", _SIGNAL_SLOT)
            table.add_slot("body-cell-day_pct", _DAYPCT_SLOT)
            table.add_slot("body-cell-trend", _TREND_SLOT)
            table.add_slot("body-cell-call_accel_disp", _CALL_SLOT)
            table.add_slot("body-cell-put_accel_disp", _PUT_SLOT)
            table.add_slot("body-cell-gex_regime", _REGIME_SLOT)

    def _paint(payload):
        table.rows = matrix_rows(payload)
        table.update()
        n = len(payload.get("rows") or [])
        err = payload.get("error")
        status.text = (f"{n} symbols · session {payload.get('session_date', '—')}"
                       + (f" · {err}" if err else ""))

    @guard_async
    async def _poll():
        v = await run.io_bound(bus_client.read_version, VIEW)
        if v is None or v == state["version"]:
            return
        payload = await run.io_bound(bus_client.read, VIEW)
        if payload:
            state["version"] = v
            _paint(payload)

    payload, version = bus_client.read_full(VIEW)
    if payload:
        state["version"] = version
        _paint(payload)
    ui.timer(2.0, _poll)
```

> If the Quasar `:sort-by`/`:descending` props don't take via `.props()` string, sort the
> rows server-side (they already are, by hotness) and drop the props — the table still
> renders hotness-desc from the payload order, and column headers stay click-sortable.

**Step 2: Verify import + build**

Run: `cd webgui && ..\.venv\Scripts\python -c "from pages.options import matrix; matrix.matrix_columns()"`
Expected: no error.

**Step 3: Commit**

```bash
git add webgui/pages/options/matrix.py
git commit -m "feat(webgui): matrix render() — sortable table + version-poll"
```

---

## Task 8: Nav registration + route + shell test

**Files:**
- Modify: `webgui/main.py` (add to `OPTIONS_CHILDREN`; add the `@ui.page` route; optional `_TAB_COLOR`)
- Modify: `webgui/tests/test_shell.py` (add `/options/matrix` to expected routes)

**Step 1: Add `/options/matrix` to the shell test's expected set (failing first)**

In `test_shell.py`'s `expected` tuple add `"/options/matrix"`.

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_shell.py -v`
Expected: FAIL (route not registered yet).

**Step 2: Register the nav item + route in `main.py`**

Append to `OPTIONS_CHILDREN` (main.py:214):

```python
    ("/options/matrix", "Matrix", "grid_on"),
```

Add the route beside `options_rescue_page` (main.py:1153):

```python
@ui.page("/options/matrix")
def options_matrix_page() -> None:
    with _layout("/options/matrix", "Options · Matrix"):
        from pages.options import matrix
        matrix.render()
```

Optional — add a favicon/tab color in `_TAB_COLOR` (main.py:328):

```python
    "/options/matrix": "#4dd0e1",
```

**Step 3: Run to verify PASS**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_shell.py -v`
Then the full webgui suite: `cd webgui && ..\.venv\Scripts\python -m pytest -q`
Expected: PASS (all green).

Also confirm the inline-style guard passes (Tailwind-first): the guard test
`test_no_inline_style.py` may auto-scan every page — if it enumerates files, `matrix.py`
is covered automatically; if it has an explicit list, add `matrix.py` to it.

**Step 4: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(webgui): register /options/matrix tab under Options"
```

---

## Task 9: Live verification + docs

**Step 1: Redis-driven end-to-end check** (services + Memurai must be running; restart
`options_svc` to load the new code)

```powershell
# from repo root, venv active
.venv\Scripts\python -c "from shared.bus import Bus; import json; e=Bus().cache_get('cache:options:matrix'); print(json.dumps(e.payload if e else None, indent=2)[:2000])"
```
Expected: a payload with `rows` (one per watchlist symbol), each carrying spot/day_pct/
trend_state/call_accel/put_accel/pc_ratio/net_prem_m/gex_regime/n_signals/n_alerts/signal/
hotness, sorted hotness-desc. Off-hours: spots from the last session, counts possibly 0 —
not an error. If `rows` is empty, check `options_svc` logs and that the GEX collector has
run today (the DB needs today/most-recent-session rows).

**Step 2: Browser check** — start the `webgui` preview, open `/options/matrix`.
- Verify the table renders one row per symbol, colored Buy/Neutral/Sell badges, colored
  Day%/trend/accel/GEX cells, and click-to-sort on columns.
- `read_console_messages` → no errors. Screenshot for proof (the page is a single light
  table, so the screenshot tool should not time out).

**Step 3: Update `CLAUDE.md`** — add a "Last updated" entry and a `/options/matrix` row to
the route table (mirror the style of the existing rows; note: new aggregator in options_svc
publishing `cache:options:matrix`, 1-min build + ~30s spot overlay, pure `matrix.py` logic,
Tailwind-first reader page). Bump the webgui + options_svc test counts.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the Options Matrix Display tab in CLAUDE.md"
```

---

## Notes / gotchas for the implementer

- **Do not** edit `scheduler.py`/`app.py` — the publish rides `collect_gex_history`.
- Confirm the exact **scan_day cache constant** in handlers (`CACHE_SCAN_DAY` vs the literal
  `"cache:options:scan_day"`); use whatever the file already defines.
- Confirm the exact **schwab quote field names** for last price + percent change in the
  existing `quote_last`/`refresh_header` code and reuse them in `apply_live_spots` (Task 5)
  rather than guessing `lastPrice`/`netPercentChange`.
- The `flow_alerts` ~50-item rolling cap means late-day `n_alerts` can undercount on very
  active days — acceptable (it's a "what's hot" cue, not an audit). Note it in the page's
  eyebrow/help if desired.
- Keep every service-side read defensive; a single bad symbol must not sink the whole matrix
  (per-symbol try/except already in `build_matrix`).
- Tailwind-first: all dynamic colors come from the finite maps in `matrix.py`; no `.style()`.
```
