# Expected Move Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each code task follows superpowers:test-driven-development (red → green → commit).

**Goal:** Add a standalone **Expected Move** page that charts 6 months of daily candlesticks for a symbol plus a forward ATM-IV expected-move cone to an option's expiration, with the leg strikes drawn as horizontal lines and an axis crosshair — reachable via a new-browser-tab handoff button on Scanner, Paper Trades, Captured Signals, and Calculator.

**Architecture:** Tier-3 page (imports only `nicegui` + `bus_client`) enqueues an `expected_move` command on `cmd:options`; `options_svc` computes candles + ATM-IV cone and caches `cache:options:expected_move`; the page version-polls and repaints a single persistent `ui.highchart`. On-demand, latest-result model identical to Calculator/Simulator.

**Tech Stack:** Python 3.11, NiceGUI, `nicegui-highcharts` (`ui.highchart`, `extras=["stock"]` for the candlestick series + crosshair labels), `shared.bus` (Redis/fakeredis), pytest.

---

## Background facts the implementer needs

- **Pages are thin readers.** `webgui/pages/options/*.py` must NOT import any engine or call the proxy. They read cache via `bus_client.read(view)` / `read_version(view)` and enqueue commands via `bus_client.request("options", {...})`. See `webgui/pages/options/calculator.py` and `simulator.py` for the exact pattern (version-poll `ui.timer`, persistent chart, `@guard` on handlers).
- **`@guard`** (`from pages.ui_guard import guard`) wraps every timer/event handler that mutates widgets so a navigated-away tab no-ops cleanly.
- **Highcharts in-place update:** `el.options = fig; el.update()`. A dynamically-added chart needs a chart present at first render — so build the `ui.highchart` once in `render()` and update it, never `clear()`/rebuild.
- **Candlestick needs the stock module:** pass `extras=["stock"]` to `ui.highchart(...)`. The stock build also enables axis **crosshair label** boxes. Verified bundled at `.venv/Lib/site-packages/nicegui_highcharts/dist/stock-*.js`.
- **Service compute** (`services/options_svc/compute.py`) may import engines + use `services._proxy`:
  - `_proxy.schwab_py_client.get_price_history_every_day(api)` → `resp.json()["candles"]` = list of `{datetime(ms), open, high, low, close, volume}` (~1 year daily).
  - `_proxy.schwab_py_client.get_option_chain(api, contract_type="ALL", from_date=…, to_date=…)` → `resp.json()` chain dict (`callExpDateMap`/`putExpDateMap`, exp keys like `"2026-07-18:28"`; each contract has `volatility`).
  - `_proxy.schwab_client.get_quote(api)` → `{"last": float, ...}` or None.
  - SPX maps to `$SPX`: `api = "$SPX" if symbol.upper() == "SPX" else symbol.upper()`.
- **Command dispatch** is the `elif command.type == ...` chain in `services/options_svc/handlers.py:handle_command`. Latest-result views (e.g. `calc_compute`) cache one view + publish one event.
- **Run service tests per folder** from repo root: `.venv\Scripts\python -m pytest services\options_svc -q`. Run webgui tests from inside `webgui`: `cd webgui && ..\.venv\Scripts\python -m pytest -q`.
- **Signal dict field names** (from `calculator.py:_prefill`): `type` (PCS/CCS/IC/LONG_PUT/NAKED_PUT/LONG_CALL/NAKED_CALL), `symbol`, `expiration`, `underlying_price`, `short_strike`, `long_strike` (puts for PCS, calls for CCS), and for IC also `call_short`, `call_long`. (`*_mark`/`short_iv` exist too but are not needed here.)

> Reminder: `git commit` messages in this repo end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer. Commit on the current `Using_Highcharts` branch.

---

## Task 1: ATM-IV-from-chain helper (service compute)

**Files:**
- Modify: `services/options_svc/compute.py` (add `atm_iv_from_chain` near the other chain helpers, ~end of file)
- Test: `services/options_svc/tests/test_expected_move.py` (new)

**Step 1: Write the failing test**

```python
# services/options_svc/tests/test_expected_move.py
from services.options_svc import compute


def _chain(vol_by_strike, exp_key="2026-07-18:28"):
    return {"callExpDateMap": {exp_key: {
        f"{k:.1f}": [{"volatility": v}] for k, v in vol_by_strike.items()}}}


def test_atm_iv_picks_nearest_strike_and_normalizes_percent():
    chain = _chain({100.0: 18.0, 105.0: 22.0})  # Schwab gives vol as a percent
    iv = compute.atm_iv_from_chain(chain, spot=101.0, expiry="2026-07-18")
    assert abs(iv - 0.18) < 1e-9  # nearest strike 100 -> 18% -> 0.18 decimal


def test_atm_iv_none_when_no_contracts():
    assert compute.atm_iv_from_chain({}, spot=100.0, expiry="2026-07-18") is None
```

**Step 2: Run it, verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_expected_move.py -q`
Expected: FAIL (`AttributeError: module ... has no attribute 'atm_iv_from_chain'`).

**Step 3: Implement (append to `compute.py`)**

```python
def atm_iv_from_chain(chain, spot, expiry=None):
    """ATM implied vol (DECIMAL, e.g. 0.18) for ``expiry`` from a chain payload.

    Picks the contract whose strike is closest to ``spot`` and reads its
    ``volatility`` (Schwab returns a percent or a decimal — normalize to decimal).
    When ``expiry`` (YYYY-MM-DD) is given, only that expiry is considered. Falls
    back to the nearest listed expiry if the exact one has no usable vol. Returns
    None if no volatility is found. Mirrors webgui calculator.extract_atm_iv but
    returns a decimal (not a percent)."""
    if not isinstance(chain, dict) or not isinstance(spot, (int, float)):
        return None
    exp_iso = str(expiry) if expiry is not None else None

    def _scan(require_exp):
        best_diff, best = float("inf"), None
        for map_key in ("callExpDateMap", "putExpDateMap"):
            for exp_key, strikes in (chain.get(map_key) or {}).items():
                if require_exp and exp_iso and exp_key.split(":")[0] != exp_iso:
                    continue
                for strike_str, contracts in (strikes or {}).items():
                    try:
                        strike = float(strike_str)
                    except (ValueError, TypeError):
                        continue
                    if not (isinstance(contracts, list) and contracts):
                        continue
                    vol = contracts[0].get("volatility")
                    if vol is None:
                        continue
                    diff = abs(strike - spot)
                    if diff < best_diff:
                        best_diff = diff
                        best = vol if vol < 5.0 else vol / 100.0
        return best

    return _scan(True) if (exp_iso and _scan(True) is not None) else _scan(False)
```

**Step 4: Run it, verify it passes**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_expected_move.py -q`
Expected: PASS (2 passed).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_expected_move.py
git commit -m "feat(options_svc): ATM-IV-from-chain helper for expected move"
```

---

## Task 2: Expected-move cone builder (pure, service compute)

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_expected_move.py`

**Step 1: Write the failing test (append)**

```python
import math


def test_em_cone_widens_as_sqrt_time():
    # 5 calendar days, daily points; first point is the anchor (width 0 at t=0).
    cone = compute.em_cone(spot=100.0, atm_iv=0.20, dte=5, start_ts_ms=0)
    upper, lower = cone["upper"], cone["lower"]
    assert len(upper) == 6 and len(lower) == 6           # t = 0..5
    assert upper[0][1] == 100.0 and lower[0][1] == 100.0  # anchor at spot
    # width(t) = spot * iv * sqrt(t/365)
    w3 = 100.0 * 0.20 * math.sqrt(3 / 365)
    assert abs(upper[3][1] - (100.0 + w3)) < 1e-9
    assert abs(lower[3][1] - (100.0 - w3)) < 1e-9
    # x advances one day (86_400_000 ms) per step
    assert upper[1][0] - upper[0][0] == 86_400_000


def test_em_cone_empty_on_bad_inputs():
    assert compute.em_cone(None, 0.2, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, None, 5, 0) == {"upper": [], "lower": []}
    assert compute.em_cone(100.0, 0.2, 0, 0) == {"upper": [], "lower": []}
```

**Step 2: Run it, verify it fails** — `AttributeError: ... em_cone`.

**Step 3: Implement (append to `compute.py`)**

```python
_DAY_MS = 86_400_000


def em_cone(spot, atm_iv, dte, start_ts_ms):
    """Forward expected-move cone points anchored at ``spot`` on ``start_ts_ms``.

    Returns {"upper": [[ts_ms, v], ...], "lower": [...]} with one point per
    calendar day t = 0..dte. width(t) = spot * atm_iv * sqrt(t/365). Empty dict
    values on non-positive dte or missing spot/iv (defensive — never raises)."""
    import math
    if not isinstance(spot, (int, float)) or not isinstance(atm_iv, (int, float)):
        return {"upper": [], "lower": []}
    try:
        dte = int(dte)
    except (TypeError, ValueError):
        return {"upper": [], "lower": []}
    if dte <= 0 or atm_iv < 0:
        return {"upper": [], "lower": []}
    upper, lower = [], []
    for t in range(dte + 1):
        ts = int(start_ts_ms) + t * _DAY_MS
        width = spot * atm_iv * math.sqrt(t / 365.0)
        upper.append([ts, round(spot + width, 4)])
        lower.append([ts, round(spot - width, 4)])
    return {"upper": upper, "lower": lower}
```

**Step 4: Run it, verify it passes.**

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_expected_move.py
git commit -m "feat(options_svc): expected-move cone builder (sqrt-time)"
```

---

## Task 3: `compute_expected_move` orchestrator (service compute)

**Files:**
- Modify: `services/options_svc/compute.py`
- Test: `services/options_svc/tests/test_expected_move.py`

**Step 1: Write the failing test (append)**

```python
class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data


def test_compute_expected_move_builds_payload(monkeypatch):
    candles = [{"datetime": 1_700_000_000_000 + i * compute._DAY_MS,
                "open": 100, "high": 101, "low": 99, "close": 100 + i}
               for i in range(200)]
    chain = {"callExpDateMap": {"2026-07-18:28": {"100.0": [{"volatility": 20.0}]}}}

    class _PY:
        def get_price_history_every_day(self, sym):
            return _Resp({"candles": candles})

        def get_option_chain(self, sym, **kw):
            return _Resp(chain)

    class _SC:
        def get_quote(self, sym):
            return {"last": 100.0}

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _PY())
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())

    out = compute.compute_expected_move(
        "SPY", "2026-07-18",
        [{"strike": 100.0, "option_type": "put", "side": "short"}])

    assert out["error"] is None
    assert out["symbol"] == "SPY" and out["spot"] == 100.0
    assert abs(out["atm_iv"] - 0.20) < 1e-9
    assert len(out["candles"]) <= 130           # capped to ~6 months
    assert out["candles"][0][0] < out["candles"][-1][0]  # ascending, [ts,o,h,l,c]
    assert len(out["candles"][0]) == 5
    assert out["em_upper"] and out["em_lower"]
    assert out["legs"] == [{"strike": 100.0, "option_type": "put", "side": "short"}]


def test_compute_expected_move_error_on_no_history(monkeypatch):
    class _PY:
        def get_price_history_every_day(self, sym):
            return _Resp({"candles": []})

        def get_option_chain(self, sym, **kw):
            return _Resp({})

    class _SC:
        def get_quote(self, sym):
            return None

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _PY())
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())
    out = compute.compute_expected_move("SPY", "2026-07-18", [])
    assert out["error"]
```

**Step 2: Run it, verify it fails** — `AttributeError: ... compute_expected_move`.

**Step 3: Implement (append to `compute.py`)**

```python
_EM_HISTORY_BARS = 130  # ~6 months of trading days


def compute_expected_move(symbol, expiry, legs) -> dict:
    """Build the Expected Move payload for a symbol/expiry/legs (defensive).

    Fetches ~6mo daily candles + the option chain, derives ATM IV for ``expiry``
    and the spot, and builds the forward cone. Always returns a JSON-safe dict;
    on any failure ``error`` is set and the data fields are empty."""
    import datetime as dt

    base = {"symbol": symbol, "expiry": expiry, "spot": None, "atm_iv": None,
            "dte": None, "candles": [], "em_upper": [], "em_lower": [],
            "legs": legs or [], "generated_at": _now_iso(), "error": None}
    try:
        api = "$SPX" if (symbol or "").upper() == "SPX" else (symbol or "").upper()
        if not api:
            base["error"] = "No symbol."
            return base

        # 1. Candles (most-recent ~130 daily bars), ascending [ts,o,h,l,c].
        cresp = _proxy.schwab_py_client.get_price_history_every_day(api)
        raw = cresp.json().get("candles", []) if getattr(cresp, "status_code", None) == 200 else []
        candles = [[int(c["datetime"]), c["open"], c["high"], c["low"], c["close"]]
                   for c in raw
                   if c.get("datetime") is not None and c.get("close") is not None]
        candles.sort(key=lambda r: r[0])
        candles = candles[-_EM_HISTORY_BARS:]
        if not candles:
            base["error"] = f"No price history for {api}."
            return base
        base["candles"] = candles

        # 2. Chain for ATM IV (today -> expiry, ALL types).
        try:
            exp_date = dt.date.fromisoformat(str(expiry))
        except Exception:
            base["error"] = f"Bad expiry: {expiry!r}."
            return base
        oresp = _proxy.schwab_py_client.get_option_chain(
            api, contract_type="ALL", from_date=dt.date.today(), to_date=exp_date)
        chain = oresp.json() if getattr(oresp, "status_code", None) == 200 else None

        # 3. Spot: live quote, else last close.
        spot = None
        q = _proxy.schwab_client.get_quote(api) or {}
        if isinstance(q, dict):
            spot = q.get("last")
        if not spot:
            spot = candles[-1][4]
        base["spot"] = spot

        atm_iv = atm_iv_from_chain(chain or {}, spot, expiry=str(expiry))
        base["atm_iv"] = atm_iv

        dte = (exp_date - dt.date.today()).days
        base["dte"] = dte
        if atm_iv is None:
            base["error"] = f"No ATM IV for {api} {expiry}."
            return base  # candles still drawn; cone omitted

        cone = em_cone(spot, atm_iv, dte, candles[-1][0])
        base["em_upper"] = cone["upper"]
        base["em_lower"] = cone["lower"]
        return base
    except Exception as exc:  # defensive: never raise to the handler
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base
```

Add the `_now_iso` helper if one is not already present in `compute.py` (search first; reuse the existing timestamp idiom if there is one):

```python
def _now_iso():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()
```

**Step 4: Run it, verify it passes.** `.venv\Scripts\python -m pytest services\options_svc\tests\test_expected_move.py -q` → 6 passed.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_expected_move.py
git commit -m "feat(options_svc): compute_expected_move orchestrator"
```

---

## Task 4: `expected_move` command + cache view (service handlers)

**Files:**
- Modify: `services/options_svc/handlers.py` (add cache/event constants + dispatch branch)
- Test: `services/options_svc/tests/test_expected_move.py`

**Step 1: Write the failing test (append)**

```python
from shared.bus import Bus
from services.options_svc import handlers


class _Cmd:
    def __init__(self, type, args):
        self.type = type
        self.args = args


def test_expected_move_command_caches_view(monkeypatch):
    monkeypatch.setattr(compute, "compute_expected_move",
                        lambda s, e, legs: {"symbol": s, "expiry": e, "legs": legs,
                                            "error": None, "candles": [[1, 1, 1, 1, 1]]})
    bus = Bus()  # fakeredis under pytest
    handlers.handle_command(bus, _Cmd("expected_move",
                            {"symbol": "SPY", "expiry": "2026-07-18", "legs": []}))
    env = bus.cache_get(handlers.CACHE_EXPECTED_MOVE)
    assert env.payload["symbol"] == "SPY"
```

**Step 2: Run it, verify it fails** — `AttributeError: ... CACHE_EXPECTED_MOVE` (and no dispatch).

**Step 3: Implement**

Add constants near the other view constants in `handlers.py` (after `CALC_RESULT`):

```python
CACHE_EXPECTED_MOVE = "cache:options:expected_move"
EVENT_EXPECTED_MOVE = "events:options:expected_move"
```

Add a dispatch branch in `handle_command` (before the trailing `else`/end), mirroring `calc_compute`:

```python
    elif command.type == "expected_move":
        a = command.args or {}
        res = compute.compute_expected_move(
            a.get("symbol"), a.get("expiry"), a.get("legs") or [])
        version = bus.cache_set(CACHE_EXPECTED_MOVE, res)
        bus.publish(EVENT_EXPECTED_MOVE, {"version": version})
```

Also add `expected_move` to the `handle_command` docstring command list (one clause, matching style).

**Step 4: Run it, verify it passes.** Then run the full service suite to confirm no regressions:

Run: `.venv\Scripts\python -m pytest services\options_svc -q`
Expected: all pass (previous count + the new expected-move tests).

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_expected_move.py
git commit -m "feat(options_svc): expected_move command + cache view"
```

---

## Task 5: Handoff payload builders + trigger helper (webgui)

**Files:**
- Modify: `webgui/pages/options/handoff.py`
- Test: `webgui/tests/test_expected_move.py` (new)

**Step 1: Write the failing test**

```python
# webgui/tests/test_expected_move.py
from pages.options import handoff


def test_signal_to_em_payload_pcs():
    sig = {"type": "PCS", "symbol": "SPY", "expiration": "2026-07-18",
           "short_strike": 540, "long_strike": 535}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPY" and out["expiry"] == "2026-07-18"
    assert out["legs"] == [
        {"strike": 540.0, "option_type": "put", "side": "short"},
        {"strike": 535.0, "option_type": "put", "side": "long"},
    ]


def test_signal_to_em_payload_iron_condor():
    sig = {"type": "IC", "symbol": "QQQ", "expiration": "2026-07-18",
           "short_strike": 470, "long_strike": 465,
           "call_short": 490, "call_long": 495}
    legs = handoff.signal_to_em_payload(sig)["legs"]
    assert {"strike": 470.0, "option_type": "put", "side": "short"} in legs
    assert {"strike": 495.0, "option_type": "call", "side": "long"} in legs
    assert len(legs) == 4


def test_signal_to_em_payload_strips_dollar_symbol():
    sig = {"type": "LONG_CALL", "symbol": "$SPX", "expiration": "2026-07-18",
           "long_strike": 5400}
    out = handoff.signal_to_em_payload(sig)
    assert out["symbol"] == "SPX"
    assert out["legs"] == [{"strike": 5400.0, "option_type": "call", "side": "long"}]
```

**Step 2: Run it, verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_expected_move.py -q`
Expected: FAIL (`AttributeError: ... signal_to_em_payload`).

**Step 3: Implement (add to `handoff.py`)**

```python
_pending["expected_move"] = None  # extend the existing module-level stash


# Per signal-type: list of (field_name, option_type, side) for the strike legs.
_EM_LEG_FIELDS = {
    "PCS":        [("short_strike", "put",  "short"), ("long_strike", "put",  "long")],
    "CCS":        [("short_strike", "call", "short"), ("long_strike", "call", "long")],
    "IC":         [("short_strike", "put",  "short"), ("long_strike", "put",  "long"),
                   ("call_short",   "call", "short"), ("call_long",   "call", "long")],
    "LONG_PUT":   [("long_strike",  "put",  "long")],
    "NAKED_PUT":  [("short_strike", "put",  "short")],
    "LONG_CALL":  [("long_strike",  "call", "long")],
    "NAKED_CALL": [("short_strike", "call", "short")],
}


def _legs_from_fields(sig, specs):
    legs = []
    for field, otype, side in specs:
        v = sig.get(field)
        if v in (None, 0, ""):
            continue
        try:
            legs.append({"strike": float(v), "option_type": otype, "side": side})
        except (TypeError, ValueError):
            continue
    return legs


def signal_to_em_payload(signal):
    """Normalize a scanner/captured/paper signal dict to {symbol, expiry, legs}."""
    sig = signal or {}
    symbol = (sig.get("symbol") or "").replace("$", "").upper()
    expiry = sig.get("expiration") or sig.get("expiry")
    specs = _EM_LEG_FIELDS.get(sig.get("type"), [])
    return {"symbol": symbol, "expiry": expiry, "legs": _legs_from_fields(sig, specs)}


def set_pending_expected_move(payload):
    _pending["expected_move"] = payload


def take_pending_expected_move():
    p = _pending.get("expected_move")
    _pending["expected_move"] = None
    return p


def send_to_expected_move(payload):
    """Stash the payload and open the Expected Move page in a NEW browser tab."""
    if not payload or not payload.get("symbol"):
        ui.notify("No symbol for expected move.", type="warning")
        return
    set_pending_expected_move(payload)
    ui.navigate.to("/options/expected-move", new_tab=True)
```

**Step 4: Run it, verify it passes.**

**Step 5: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/tests/test_expected_move.py
git commit -m "feat(webgui): expected-move handoff payload builders"
```

---

## Task 6: Pure figure builders (webgui page)

**Files:**
- Create: `webgui/pages/options/expected_move.py`
- Test: `webgui/tests/test_expected_move.py`

**Step 1: Write the failing test (append)**

```python
from pages.options import expected_move as em


def _payload():
    return {
        "symbol": "SPY", "expiry": "2026-07-18", "spot": 100.0, "atm_iv": 0.2, "dte": 5,
        "candles": [[1, 100, 101, 99, 100], [2, 100, 102, 99, 101]],
        "em_upper": [[2, 100.0], [3, 101.0]],
        "em_lower": [[2, 100.0], [3, 99.0]],
        "legs": [{"strike": 540.0, "option_type": "put", "side": "short"},
                 {"strike": 535.0, "option_type": "put", "side": "long"}],
        "error": None,
    }


def test_leg_lines_short_solid_long_dashed():
    lines = em.leg_lines(_payload()["legs"])
    assert lines[0]["value"] == 540.0 and "dashStyle" not in lines[0]      # short solid
    assert lines[1]["value"] == 535.0 and lines[1]["dashStyle"] == "Dash"  # long dashed


def test_expected_move_figure_series_and_crosshair():
    fig = em.expected_move_figure(_payload())
    types = {s["type"] for s in fig["series"]}
    assert "candlestick" in types
    assert fig["series"][0]["data"][0] == [1, 100, 101, 99, 100]
    assert fig["xAxis"]["type"] == "datetime"
    assert fig["xAxis"]["crosshair"]["label"]["enabled"] is True
    assert fig["yAxis"]["crosshair"]["label"]["enabled"] is True
    assert any(s.get("dashStyle") == "Dash" for s in fig["series"] if s["type"] == "line")
    assert len(fig["yAxis"]["plotLines"]) == 2  # two leg lines


def test_expected_move_figure_handles_empty_payload():
    fig = em.expected_move_figure({})
    assert fig["series"] == [] or all(not s.get("data") for s in fig["series"])
```

**Step 2: Run it, verify it fails** — module does not exist.

**Step 3: Implement `webgui/pages/options/expected_move.py`** (builders + a render stub so the import resolves; render is fleshed out in Task 7):

```python
"""Expected Move page (Tier-3 reader) — candlestick history + ATM-IV cone.

Engine-free: render() enqueues an ``expected_move`` command on ``cmd:options``
and version-polls ``options:expected_move``; the cone + candles + ATM IV are all
computed in ``services/options_svc``. Pure figure builders are unit-tested.

Reached via a new-browser-tab handoff (handoff.send_to_expected_move) from the
Scanner / Paper / Captured / Calculator pages, or standalone from the nav.
Chart is Highcharts candlestick (extras=["stock"], which also provides the axis
crosshair label boxes)."""

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"
EM_UP_COLOR = "#66bb6a"
EM_DOWN_COLOR = "#ef5350"
PUT_COLOR = "#ef9a9a"
CALL_COLOR = "#90caf9"

_DARK_AXIS = {"labels": {"style": {"color": "#bdbdbd"}},
              "gridLineColor": "rgba(255,255,255,0.06)",
              "lineColor": "rgba(255,255,255,0.15)"}


def leg_lines(legs):
    """yAxis plotLines for each leg: short solid / long dashed, put/call colored."""
    lines = []
    for leg in legs or []:
        strike = leg.get("strike")
        if not isinstance(strike, (int, float)):
            continue
        otype = leg.get("option_type", "")
        side = leg.get("side", "")
        color = CALL_COLOR if otype == "call" else PUT_COLOR
        pl = {"value": float(strike), "color": color, "width": 1.5, "zIndex": 4,
              "label": {"text": f"{side} {otype} {strike:g}",
                        "style": {"color": color, "fontSize": "10px"}}}
        if side == "long":
            pl["dashStyle"] = "Dash"
        lines.append(pl)
    return lines


def expected_move_figure(payload, timeframe="daily"):
    """Highcharts options for the candlestick + EM cone + leg lines.

    ``timeframe`` is accepted for future intraday support (daily only for now)."""
    p = payload or {}
    candles = p.get("candles") or []
    em_upper = p.get("em_upper") or []
    em_lower = p.get("em_lower") or []
    title = p.get("symbol") or "Expected Move"
    if p.get("expiry"):
        title = f"{title} — Expected Move to {p['expiry']}"

    series = [{
        "type": "candlestick", "name": p.get("symbol") or "Price", "data": candles,
        "color": DOWN_COLOR, "upColor": UP_COLOR,
        "lineColor": DOWN_COLOR, "upLineColor": UP_COLOR,
    }]
    if em_upper:
        series.append({"type": "line", "name": "Upper EM", "data": em_upper,
                       "color": EM_UP_COLOR, "dashStyle": "Dash",
                       "marker": {"enabled": False}})
    if em_lower:
        series.append({"type": "line", "name": "Lower EM", "data": em_lower,
                       "color": EM_DOWN_COLOR, "dashStyle": "Dash",
                       "marker": {"enabled": False}})

    return {
        "chart": {"backgroundColor": "transparent", "height": 540},
        "title": {"text": title, "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": True, "itemStyle": {"color": "#bdbdbd"}},
        "rangeSelector": {"enabled": False},
        "navigator": {"enabled": False},
        "scrollbar": {"enabled": False},
        "xAxis": {**_DARK_AXIS, "type": "datetime",
                  "crosshair": {"label": {"enabled": True}, "snap": False}},
        "yAxis": {**_DARK_AXIS, "title": {"text": "Price"}, "opposite": False,
                  "crosshair": {"label": {"enabled": True}, "snap": False},
                  "plotLines": leg_lines(p.get("legs"))},
        "tooltip": {"shared": True},
        "series": series,
    }


def render():  # fleshed out in Task 7
    pass
```

**Step 4: Run it, verify it passes.** `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_expected_move.py -q`.

**Step 5: Commit**

```bash
git add webgui/pages/options/expected_move.py webgui/tests/test_expected_move.py
git commit -m "feat(webgui): expected-move figure builders (candlestick + cone)"
```

---

## Task 7: Page `render()` (webgui)

**Files:**
- Modify: `webgui/pages/options/expected_move.py` (replace the `render()` stub)
- Test: `webgui/tests/test_expected_move.py` (render-smoke)

**Step 1: Write the failing smoke test (append)**

```python
def test_render_smoke(monkeypatch):
    # render() must build without a live backend (graceful-empty cache).
    import bus_client
    monkeypatch.setattr(bus_client, "read_version", lambda v: None)
    monkeypatch.setattr(bus_client, "read", lambda v: None)
    monkeypatch.setattr(bus_client, "request", lambda d, c: "id")
    from nicegui import ui
    with ui.card():            # any slot context
        em.render()            # should not raise
```

> If the existing suite has a NiceGUI client fixture (check `webgui/conftest.py` / other `test_*` render-smoke tests), follow that pattern instead of a bare `ui.card()` context.

**Step 2: Run it, verify it fails** (render is a no-op `pass` → assertion/None, or fails once you assert widgets). Make the test meaningful by asserting it runs after implementation; initially it passes trivially, so first extend render to actually build, then ensure the smoke test exercises it. (Acceptable: this task is render wiring; the guard is the manual preview in Task 10.)

**Step 3: Implement `render()`**

```python
def render():
    """Build the Expected Move page: input row + persistent candlestick chart.

    Handoff flow: a stashed payload (from Scanner/Paper/Captured/Calculator) is
    consumed once on load and its command enqueued immediately. Standalone flow:
    the user types a symbol + expiry (+ optional strike) and clicks Draw."""
    import datetime as dt

    from nicegui import ui

    import bus_client

    from pages.ui_guard import guard

    from . import handoff
    from .inputs import select_all_on_focus

    ui.label("Expected Move").classes("text-h5")

    state = {"ver": None}

    with ui.row().classes("items-end gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-28"))
        expiry_in = ui.input("Expiry (YYYY-MM-DD)").classes("w-44")
        strike_in = ui.number("Strike (optional)", format="%.2f").classes("w-36")
        type_tog = ui.toggle(["put", "call"], value="put")
        draw_btn = ui.button("Draw", icon="show_chart")
        status = ui.label("").classes("opacity-70 text-sm")

    # Persistent chart (must exist at first render for the ESM import map).
    chart = ui.highchart(expected_move_figure({}), extras=["stock"]).classes("w-full")

    def _repaint(payload):
        status.text = (payload or {}).get("error") or ""
        chart.options = expected_move_figure(payload or {})
        chart.update()

    @guard
    def _enqueue(payload):
        if not payload or not payload.get("symbol") or not payload.get("expiry"):
            ui.notify("Symbol + expiry required.", type="warning")
            return
        bus_client.request("options", {"type": "expected_move", "args": payload})
        status.text = f"Computing expected move for {payload['symbol']}…"

    @guard
    def _draw():
        legs = []
        if strike_in.value:
            legs = [{"strike": float(strike_in.value),
                     "option_type": type_tog.value, "side": "short"}]
        _enqueue({"symbol": (symbol_in.value or "").replace("$", "").upper(),
                  "expiry": (expiry_in.value or "").strip(), "legs": legs})

    draw_btn.on_click(_draw)

    @guard
    def _poll():
        version = bus_client.read_version("options:expected_move")
        if version == state["ver"]:
            return
        state["ver"] = version
        _repaint(bus_client.read("options:expected_move"))

    # Consume a stashed handoff payload (new browser tab = fresh request).
    pending = handoff.take_pending_expected_move()
    if pending:
        symbol_in.value = pending.get("symbol") or symbol_in.value
        if pending.get("expiry"):
            expiry_in.value = pending["expiry"]
        # Track current version WITHOUT painting a stale prior result, then enqueue.
        state["ver"] = bus_client.read_version("options:expected_move")
        _enqueue(pending)
    else:
        # Standalone open: default expiry to ~30d out for convenience, no auto-fetch.
        if not expiry_in.value:
            expiry_in.value = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        state["ver"] = bus_client.read_version("options:expected_move")

    ui.timer(1.0, _poll)
```

**Step 4: Run it, verify it passes** (smoke). `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_expected_move.py -q`.

**Step 5: Commit**

```bash
git add webgui/pages/options/expected_move.py webgui/tests/test_expected_move.py
git commit -m "feat(webgui): expected-move page render + version-poll"
```

---

## Task 8: Route + nav registration (webgui)

**Files:**
- Modify: `webgui/main.py` (add `OPTIONS_CHILDREN` item + `@ui.page` route)
- Modify: `webgui/tests/test_shell.py` (add the route to the expected set)

**Step 1: Write the failing test** — add `/options/expected-move` to the expected-routes set in `test_shell.py` (find the existing set literal; add the entry).

**Step 2: Run it, verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_shell.py -q`
Expected: FAIL (route missing from the registered pages).

**Step 3: Implement**

In `main.py`, add to `OPTIONS_CHILDREN` (after the Simulator line ~117):

```python
    ("/options/expected-move", "Expected Move", "candlestick_chart"),
```

Add the page route (mirror the simulator route ~394):

```python
@ui.page("/options/expected-move")
def options_expected_move_page() -> None:
    with _layout("/options/expected-move", "Options · Expected Move"):
        from pages.options import expected_move
        expected_move.render()
```

**Step 4: Run it, verify it passes** — `pytest tests\test_shell.py -q`.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(webgui): register /options/expected-move route + nav item"
```

---

## Task 9: Trigger buttons on the four source pages

Add an **Expected Move** action that calls `handoff.send_to_expected_move(...)`. For the three signal tables, extend the existing per-row action slot; for the Calculator, add a top-level button.

**Files:**
- Modify: `webgui/pages/options/handoff.py` (extend `_ACTIONS_SLOT` + `add_row_actions` with an EM button)
- Modify: `webgui/pages/options/calculator.py` (add a button)
- Test: `webgui/tests/test_expected_move.py`

**Step 1: Write the failing test (append)** — assert the slot exposes an EM emit and that `add_row_actions` wires it:

```python
def test_actions_slot_has_expected_move_button():
    assert "to_em" in handoff._ACTIONS_SLOT
    assert "show_chart" in handoff._ACTIONS_SLOT
```

**Step 2: Run it, verify it fails.**

**Step 3: Implement**

In `handoff.py`, add a third button to `_ACTIONS_SLOT`:

```html
  <q-btn dense flat round size="sm" icon="show_chart" color="accent"
         @click.stop="() => $parent.$emit('to_em', props.row)">
    <q-tooltip>Expected Move</q-tooltip>
  </q-btn>
```

And wire it in `add_row_actions`:

```python
    table.on("to_em", lambda e: send_to_expected_move(signal_to_em_payload(get_signal(e.args))))
```

Because `add_row_actions` is already used by Scanner, Captured, Paper, and Swing (`handoff.add_row_actions(_t, ...)`), this single change lights up the button on **all** signal tables. Verify each of `scanner.py`, `captured.py`, `paper.py` calls `add_row_actions` (grep) — they do via the shared detail/table wiring; no per-page change needed beyond confirming.

> If Paper Trades rows don't carry the signal-style fields (`type`/`short_strike`/...), add a small mapper in `paper.py` where it builds its `get_signal` so the row maps to the `{type, symbol, expiration, *_strike}` shape `signal_to_em_payload` expects. Confirm by reading `paper.py`'s `add_row_actions`/`get_signal` usage before assuming.

For the **Calculator** (`calculator.py`), add a button next to "Calculate" that builds the payload from the form and hands off:

```python
    ui.button("Expected Move", icon="show_chart", on_click=lambda: send_to_em()) \
        .props("flat dense size=sm").tooltip("Chart the expected move for these legs")
```

with, inside `render()` (after `do_calc` is defined; import `send_to_expected_move` from `.handoff`):

```python
    @guard
    def send_to_em():
        legs = [{"strike": float(info["strike"].value), "option_type": info["option_type"],
                 "side": info["side"]}
                for info in leg_inputs.values() if info["strike"].value]
        handoff.send_to_expected_move({
            "symbol": (symbol_in.value or "").replace("$", "").upper(),
            "expiry": str(expiry_sel.value or ""), "legs": legs})
```

(`handoff` is already imported in `calculator.py`.)

**Step 4: Run it, verify it passes** — `pytest tests\test_expected_move.py -q`, then the full webgui suite: `cd webgui && ..\.venv\Scripts\python -m pytest -q`.

**Step 5: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/pages/options/calculator.py webgui/pages/options/paper.py webgui/tests/test_expected_move.py
git commit -m "feat(webgui): Expected Move trigger buttons (scanner/captured/paper/calculator)"
```

---

## Task 10: Full verification (tests + live preview)

**Step 1: Run every affected suite — all green**

```bash
.venv\Scripts\python -m pytest services\options_svc -q
cd webgui && ..\.venv\Scripts\python -m pytest -q && cd ..
```

Expected: options_svc suite passes (prior count + new EM tests); webgui suite passes (prior 322 + new EM tests). Record the actual numbers — do not claim a count you didn't see.

**Step 2: Live preview (REQUIRED — see CLAUDE.md "Verify in the browser")**

Per the preview workflow: start the `webgui` dev server (`:8500`), and ensure Memurai + proxy + `options_svc` are running (a cold service ⇒ the page shows "Computing…" forever). Then:

1. Open the Scanner, click a signal row's **Expected Move** (show_chart) action → a new browser tab opens at `/options/expected-move`.
2. `preview_screenshot` the new tab: confirm candlesticks render, the green/red dashed EM cone fans out to expiry, and the leg strike line(s) are drawn.
3. Hover the chart → confirm the **crosshair** shows the Date on the X axis and Price on the Y axis (crosshair label boxes), plus the OHLC tooltip.
4. `preview_console_logs` → no `Failed to resolve module specifier` / Highcharts errors (the `extras=["stock"]` + persistent-chart guard should prevent the ESM error).
5. Repeat the trigger from the **Calculator** (build legs → Expected Move button) and one of Paper/Captured to confirm all four entry points hand off correctly.

**Step 3: Update CLAUDE.md**

Per the standing maintenance requirement, add an "Expected Move page (`/options/expected-move`) — DONE" subsection (mirroring the other page write-ups) and a row in the Routes table; note the `extras=["stock"]` candlestick + crosshair gotcha in the NiceGUI gotchas section. Use the claude-md-management:revise-claude-md skill if available.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): document the Expected Move page"
```

---

## Done criteria

- [ ] `compute_expected_move` + `em_cone` + `atm_iv_from_chain` unit-tested and green.
- [ ] `expected_move` command caches `cache:options:expected_move` + publishes.
- [ ] Page renders candlesticks + ATM-IV cone + leg lines + working axis crosshair.
- [ ] Buttons on Scanner, Paper Trades, Captured Signals, and Calculator open the page in a new browser tab with the right symbol/expiry/legs.
- [ ] Nav item + route registered; `test_shell.py` updated.
- [ ] All service + webgui suites green (numbers recorded from real runs).
- [ ] Live preview screenshot confirms the chart, cone, leg lines, and crosshair.
- [ ] CLAUDE.md updated.
```
