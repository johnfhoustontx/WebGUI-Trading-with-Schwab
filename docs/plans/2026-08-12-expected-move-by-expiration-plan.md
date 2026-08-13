# Expected Move — current-day candle + by-expiration selection: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Draw the current-day candle on `/options/expected-move`, and replace its free-text expiry/strike inputs with dropdowns driven by the symbol's real option chain.

**Architecture:** Two independent Tier-2 additions plus a Tier-1 rewire. (a) A pure `today_candle` helper synthesizes the forming daily bar from the raw quote, because Schwab's `period`-based daily history ends at the *previous* trading day; `compute_expected_move` appends it in daily mode. (b) A new `em_chain` command publishes `cache:options:em_chain` — expirations plus a per-expiry strike ladder — which the page reads to populate two `ui.select`s. Strike changes repaint locally; expiry changes re-enqueue the existing `expected_move` command.

**Tech Stack:** Python 3.11, NiceGUI + Highcharts (Tier 1), FastAPI + Redis/Memurai bus (Tier 2), pytest.

**Design doc:** [`docs/plans/2026-08-12-expected-move-by-expiration-design.md`](2026-08-12-expected-move-by-expiration-design.md)

---

## Environment notes (read before Task 1)

This worktree has **no `.venv`**. Use the absolute interpreter, and confine every
`cd` to a **subshell** — a bare `cd` into a subdirectory leaves the shell there
and the relative-path hooks in `.claude/settings.json` then fail on every
subsequent tool call, unrecoverably.

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_expected_move.py -q
```

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_expected_move.py -q)
```

Service tests run from the **worktree root**; webgui tests run from inside
`webgui/`. Never run `pytest services` across all services — that puts several
hyphenated app dirs on `sys.path` at once and re-triggers the documented
`config`/`scoring`/`notifier` module-name collisions.

---

## Task 1: `today_candle` pure helper

**Files:**
- Modify: `services/options_svc/compute.py` (add near `em_cone`, ~line 5436)
- Test: `services/options_svc/tests/test_expected_move.py`

**Step 1: Write the failing tests**

Append to `services/options_svc/tests/test_expected_move.py`:

```python
import datetime as _dt


def _quote(**over):
    q = {"openPrice": 774.71, "highPrice": 774.9, "lowPrice": 771.28,
         "lastPrice": 772.30}
    q.update(over)
    return q


def _ms(y, m, d):
    return int(_dt.datetime(y, m, d).timestamp() * 1000)


def test_today_candle_builds_bar_from_quote():
    # Wed 2026-08-12 10:00 local, history ends Tue the 11th.
    now = _dt.datetime(2026, 8, 12, 10, 0)
    bar = compute.today_candle(_quote(), _ms(2026, 8, 11), now=now)
    assert bar == [_ms(2026, 8, 12), 774.71, 774.9, 771.28, 772.30]


def test_today_candle_drawn_after_the_close():
    # "From the open onward" — still drawn at 19:58 local, not just during RTH.
    now = _dt.datetime(2026, 8, 12, 19, 58)
    assert compute.today_candle(_quote(), _ms(2026, 8, 11), now=now) is not None


def test_today_candle_skipped_premarket():
    # Before 08:30 CT the quote's openPrice is still the PRIOR session's open.
    now = _dt.datetime(2026, 8, 12, 7, 15)
    assert compute.today_candle(_quote(), _ms(2026, 8, 11), now=now) is None


def test_today_candle_skipped_on_weekend_and_holiday():
    sat = _dt.datetime(2026, 8, 15, 10, 0)
    assert compute.today_candle(_quote(), _ms(2026, 8, 14), now=sat) is None
    xmas = _dt.datetime(2026, 12, 25, 10, 0)
    assert compute.today_candle(_quote(), _ms(2026, 12, 24), now=xmas,
                                holidays={_dt.date(2026, 12, 25)}) is None


def test_today_candle_no_op_when_history_already_has_today():
    now = _dt.datetime(2026, 8, 12, 10, 0)
    assert compute.today_candle(_quote(), _ms(2026, 8, 12), now=now) is None


def test_today_candle_degrades_on_missing_or_zero_fields():
    now = _dt.datetime(2026, 8, 12, 10, 0)
    last = _ms(2026, 8, 11)
    assert compute.today_candle(_quote(openPrice=None), last, now=now) is None
    assert compute.today_candle(_quote(lastPrice=0), last, now=now) is None
    assert compute.today_candle({}, last, now=now) is None
    assert compute.today_candle(None, last, now=now) is None
```

**Step 2: Run to verify they fail**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_expected_move.py -q -k today_candle
```

Expected: FAIL — `AttributeError: module 'compute' has no attribute 'today_candle'`.

**Step 3: Implement**

Add to `services/options_svc/compute.py`, immediately after `em_cone`:

```python
# The daily-history endpoint (periodType=year&period=1) ends at the PREVIOUS
# trading day — Schwab never returns the forming bar — so the current session is
# missing from the Expected Move chart. The live quote does carry it, so
# synthesize it. Gated at/after the open because premarket ``openPrice`` is still
# the PRIOR session's open and would draw a false bar.
_TODAY_CANDLE_OPEN = (8, 30)  # local CT market open


def today_candle(quote, last_ts_ms, now=None, holidays=None):
    """``[ts_ms, o, h, l, c]`` for today's forming session bar, or ``None``.

    ``quote`` is a RAW Schwab quote dict (``openPrice``/``highPrice``/
    ``lowPrice``/``lastPrice``) — the normalized ``SchwabProxyClient.get_quote``
    drops ``openPrice``. Returns None unless today is a trading day, local time
    is at/after the 08:30 CT open, every OHLC field is numeric and > 0, and the
    history's last candle predates today (so this is a no-op should Schwab ever
    start returning the forming bar). Timestamp is today's local midnight in ms,
    matching the daily candles' own convention. Never raises."""
    import datetime as _dt

    if not isinstance(quote, dict):
        return None
    now = now or _dt.datetime.now()
    holidays = holidays or set()
    today = now.date()
    if now.weekday() >= 5 or today in holidays:
        return None
    if now.time() < _dt.time(*_TODAY_CANDLE_OPEN):
        return None
    try:
        last_date = _dt.datetime.fromtimestamp(int(last_ts_ms) / 1000).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    if last_date >= today:
        return None
    vals = []
    for key in ("openPrice", "highPrice", "lowPrice", "lastPrice"):
        v = quote.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            return None
        vals.append(float(v))
    ts = int(_dt.datetime(today.year, today.month, today.day).timestamp() * 1000)
    return [ts, *vals]
```

**Step 4: Run to verify they pass**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_expected_move.py -q
```

Expected: all pass except the 2 documented date-relative baseline failures in this file — **compare the failing node IDs**, never the count.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_expected_move.py
git commit -m "feat(options): today_candle synthesizes the forming daily bar from the quote"
```

---

## Task 2: Append today's candle in `compute_expected_move`

**Files:**
- Modify: `services/options_svc/compute.py:5509-5577` (`compute_expected_move`)
- Test: `services/options_svc/tests/test_expected_move.py`

The function currently fetches spot via `_proxy.schwab_client.get_quote`, whose
normalization **drops `openPrice`**. Prefer the raw
`_proxy.schwab_py_client.get_quotes([api])` (the unwrap `calc_load_symbol`
already performs) so one call yields both the spot and the candle's open, and
**keep `schwab_client.get_quote` as a fallback**.

⚠ The fallback is load-bearing, not belt-and-braces. Two existing tests in this
file (`test_compute_expected_move_builds_payload`,
`test_compute_expected_move_skips_partial_candle`, lines ~120-177) monkeypatch a
`schwab_py_client` double that has **no `get_quotes` method** and supply spot via
`schwab_client.get_quote`. Without the fallback those fixtures silently lose
their spot. Both are already red for an unrelated date-relative reason (their
hardcoded `2026-07-18` expiry is in the past, so `dte < 0`) — so they are **not**
a usable regression signal here. Do not "fix" their dates; that is out of scope.

**Step 1: Write the failing test**

```python
def test_compute_expected_move_appends_todays_candle(monkeypatch):
    """Daily mode appends the forming bar, and the cone anchors on IT."""
    hist = [[_ms(2026, 8, 10), 772.6, 775.05, 771.62, 773.03],
            [_ms(2026, 8, 11), 774.53, 774.61, 769.2, 770.56]]
    monkeypatch.setattr(compute, "_fetch_em_candles", lambda *a, **k: list(hist))
    monkeypatch.setattr(compute, "atm_iv_from_chain", lambda *a, **k: 0.15)
    monkeypatch.setattr(compute, "today_candle",
                        lambda *a, **k: [_ms(2026, 8, 12), 774.71, 774.9, 771.28, 772.3])

    class _Resp:
        status_code = 200
        def __init__(self, d): self._d = d
        def json(self): return self._d

    monkeypatch.setattr(compute._proxy, "schwab_py_client", type("C", (), {
        "get_quotes": staticmethod(
            lambda syms: _Resp({"SPY": {"quote": {"lastPrice": 772.3,
                                                  "openPrice": 774.71,
                                                  "highPrice": 774.9,
                                                  "lowPrice": 771.28}}})),
        "get_option_chain": staticmethod(lambda *a, **k: _Resp({})),
    })())

    exp = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()
    out = compute.compute_expected_move("SPY", exp, [])
    assert out["candles"][-1][0] == _ms(2026, 8, 12)
    assert out["spot"] == 772.3
    # The cone anchors at the LAST candle — today's, not yesterday's.
    assert out["em_upper"][0][0] == _ms(2026, 8, 12)


def test_compute_expected_move_spot_falls_back_to_normalized_quote(monkeypatch):
    """A schwab_py_client without get_quotes must still yield a spot.

    Guards the existing fixtures in this file, whose doubles predate the raw-quote
    switch. Those fixtures are already red for a date-relative reason, so this is
    the only live check of the fallback.
    """
    monkeypatch.setattr(compute, "_fetch_em_candles",
                        lambda *a, **k: [[_ms(2026, 8, 11), 1, 1, 1, 99.0]])
    monkeypatch.setattr(compute, "atm_iv_from_chain", lambda *a, **k: 0.15)
    monkeypatch.setattr(compute, "today_candle", lambda *a, **k: None)

    class _PY:  # no get_quotes — exactly like the older fixtures
        def get_option_chain(self, *a, **k):
            class _R:
                status_code = 200
                @staticmethod
                def json(): return {}
            return _R()

    class _SC:
        def get_quote(self, sym): return {"last": 123.0}

    monkeypatch.setattr(compute._proxy, "schwab_py_client", _PY())
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())
    exp = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()
    out = compute.compute_expected_move("SPY", exp, [])
    assert out["spot"] == 123.0          # normalized fallback, not the candle close


def test_compute_expected_move_skips_today_in_intraday_mode(monkeypatch):
    """dte<=2 uses intraday candles, which already reach today."""
    called = []
    monkeypatch.setattr(compute, "today_candle",
                        lambda *a, **k: called.append(1) or [0, 1, 1, 1, 1])
    monkeypatch.setattr(compute, "_fetch_em_candles",
                        lambda *a, **k: [[_ms(2026, 8, 11), 1, 1, 1, 1]])
    monkeypatch.setattr(compute, "atm_iv_from_chain", lambda *a, **k: None)
    exp = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
    compute.compute_expected_move("SPY", exp, [])
    assert called == []
```

**Step 2: Run to verify they fail**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_expected_move.py -q -k "appends_todays or skips_today"
```

Expected: FAIL — the last candle is still `2026-08-11`.

**Step 3: Implement**

Replace the spot block in `compute_expected_move` (currently lines ~5549-5555):

```python
        # Prefer the RAW quote: the normalized schwab_client.get_quote drops
        # openPrice, which today's synthetic candle needs. Fall back to the
        # normalized client when the raw one yields nothing, so the spot path
        # degrades exactly as it did before.
        raw_q = {}
        try:
            qresp = _proxy.schwab_py_client.get_quotes([api])
            if getattr(qresp, "status_code", None) == 200:
                info = (qresp.json() or {}).get(api) or {}
                raw_q = info.get("quote", info.get("reference", info)) or {}
        except Exception:
            raw_q = {}
        spot = raw_q.get("lastPrice") if isinstance(raw_q, dict) else None
        if not spot:
            q = _proxy.schwab_client.get_quote(api) or {}
            if isinstance(q, dict):
                spot = q.get("last")
        if not spot:
            spot = candles[-1][4]
        base["spot"] = spot

        # Schwab's daily history stops at the previous trading day; append the
        # forming bar so the chart shows today AND the cone anchors on it (it is
        # sized from today's spot, so anchoring at yesterday overshot the expiry
        # by a day).
        if spec.get("mode") != "intraday":
            try:
                from services.options_svc.scheduler import _HOLIDAYS as _mkt_hols
            except Exception:
                _mkt_hols = set()
            bar = today_candle(raw_q, candles[-1][0], holidays=_mkt_hols)
            if bar:
                candles = candles + [bar]
                base["candles"] = candles
```

**Step 4: Run to verify they pass**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_expected_move.py -q
```

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_expected_move.py
git commit -m "fix(options): draw today's candle on Expected Move; anchor the cone on it"
```

---

## Task 3: `em_chain_meta` — expirations + strike ladders

**Files:**
- Modify: `services/options_svc/compute.py` (add after `atm_iv_from_chain`, ~line 5397)
- Test: `services/options_svc/tests/test_expected_move.py`

The pure extraction is duplicated from `webgui/pages/options/calculator.py:206-227`
**on purpose**: a Tier-2 service must not import a Tier-1 page, and publishing the
raw chain instead (the Calculator's approach) would put megabytes on the bus for
data the page reduces to two short lists.

**Step 1: Write the failing tests**

```python
_CHAIN = {
    "callExpDateMap": {"2026-08-14:2": {"770.0": [{}], "775.0": [{}]},
                       "2026-09-18:37": {"800.0": [{}]}},
    "putExpDateMap": {"2026-08-14:2": {"765.0": [{}], "770.0": [{}]}},
}


def test_chain_expiries_and_strikes():
    assert compute.chain_expiries(_CHAIN) == ["2026-08-14", "2026-09-18"]
    # Deduped ACROSS call+put — one ladder per expiry.
    assert compute.chain_strikes(_CHAIN, "2026-08-14") == [765.0, 770.0, 775.0]
    assert compute.chain_strikes(_CHAIN, "2026-09-18") == [800.0]
    assert compute.chain_strikes(_CHAIN, "2026-01-01") == []


def test_chain_helpers_defensive_on_junk():
    assert compute.chain_expiries(None) == []
    assert compute.chain_expiries({}) == []
    assert compute.chain_strikes({"callExpDateMap": {"x:1": {"nope": [{}]}}}, "x") == []


def test_em_chain_meta_builds_payload(monkeypatch):
    class _Resp:
        status_code = 200
        def __init__(self, d): self._d = d
        def json(self): return self._d

    monkeypatch.setattr(compute._proxy, "schwab_py_client", type("C", (), {
        "get_quotes": staticmethod(
            lambda syms: _Resp({"SPY": {"quote": {"lastPrice": 772.3}}})),
        "get_option_chain": staticmethod(lambda *a, **k: _Resp(_CHAIN)),
    })())
    out = compute.em_chain_meta("SPY")
    assert out["symbol"] == "SPY" and out["api"] == "SPY"
    assert out["spot"] == 772.3
    assert out["expirations"] == ["2026-08-14", "2026-09-18"]
    assert out["strikes"]["2026-08-14"] == [765.0, 770.0, 775.0]
    assert out["error"] is None


def test_em_chain_meta_maps_spx_and_degrades(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("proxy down")
    monkeypatch.setattr(compute._proxy, "schwab_py_client",
                        type("C", (), {"get_quotes": staticmethod(_boom),
                                       "get_option_chain": staticmethod(_boom)})())
    out = compute.em_chain_meta("SPX")
    assert out["api"] == "$SPX"
    assert out["expirations"] == [] and out["strikes"] == {}
    assert out["error"]
```

**Step 2: Run to verify they fail**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_expected_move.py -q -k "chain_expiries or chain_helpers or em_chain_meta"
```

**Step 3: Implement**

```python
# Chain-ladder extraction for the Expected Move page's expiry/strike dropdowns.
# Duplicated from webgui calculator.chain_expiries/chain_strikes ON PURPOSE: a
# Tier-2 service must not import a Tier-1 page, and publishing the RAW chain (the
# Calculator's pattern) would put megabytes on the bus for two short lists.
def chain_expiries(chain):
    """Sorted unique expiry strings (YYYY-MM-DD) from an option-chain payload."""
    out = set()
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key in ((chain or {}).get(map_key) or {}):
            out.add(str(exp_key).split(":")[0])
    return sorted(out)


def chain_strikes(chain, expiry):
    """Sorted strikes for one expiry, deduped across BOTH call and put maps.

    Put-vs-call is the page toggle's job (it colors the line); the ladder itself
    is one list so the dropdown does not change under the toggle."""
    out = set()
    for map_key in ("callExpDateMap", "putExpDateMap"):
        for exp_key, strikes in ((chain or {}).get(map_key) or {}).items():
            if str(exp_key).split(":")[0] != str(expiry):
                continue
            for s in (strikes or {}):
                try:
                    out.add(float(s))
                except (ValueError, TypeError):
                    continue
    return sorted(out)


_EM_CHAIN_DAYS = 90  # today→+90d: monthlies further out than the Calculator's 60d


def em_chain_meta(symbol) -> dict:
    """Expirations + per-expiry strike ladders for the Expected Move dropdowns.

    Returns ``{"symbol", "api", "spot", "expirations", "strikes", "error"}`` —
    JSON-safe and fully defensive (a failed fetch leaves the ladders empty and
    the page's manual path working). Never raises."""
    import datetime as dt

    api = "$SPX" if (symbol or "").upper() == "SPX" else (symbol or "").upper()
    base = {"symbol": symbol, "api": api, "spot": None,
            "expirations": [], "strikes": {}, "error": None}
    if not api:
        base["error"] = "No symbol."
        return base
    try:
        today = dt.date.today()
        cresp = _proxy.schwab_py_client.get_option_chain(
            api, contract_type="ALL", from_date=today,
            to_date=today + dt.timedelta(days=_EM_CHAIN_DAYS))
        chain = cresp.json() if getattr(cresp, "status_code", None) == 200 else None
        if not chain:
            base["error"] = f"No option chain for {api}."
            return base
        exps = chain_expiries(chain)
        base["expirations"] = exps
        base["strikes"] = {e: chain_strikes(chain, e) for e in exps}
        try:
            qresp = _proxy.schwab_py_client.get_quotes([api])
            if getattr(qresp, "status_code", None) == 200:
                info = (qresp.json() or {}).get(api) or {}
                q = info.get("quote", info.get("reference", info)) or {}
                base["spot"] = q.get("lastPrice")
        except Exception:
            pass
        return base
    except Exception as exc:
        base["error"] = f"{type(exc).__name__}: {exc}"
        return base
```

**Step 4: Run to verify they pass**, then **Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_expected_move.py
git commit -m "feat(options): em_chain_meta publishes expirations + strike ladders"
```

---

## Task 4: `em_chain` command handler

**Files:**
- Modify: `services/options_svc/handlers.py` (keys near line 272; dispatch near line 1748; docstring at 1554-1560)
- Test: `services/options_svc/tests/test_handlers.py`

**Step 1: Write the failing test** (mirror `test_calc_load_command_caches_chain`, line 1264)

```python
def test_em_chain_command_caches_ladders(monkeypatch):
    bus = _FakeBus()
    seen = {}

    def _rec(symbol):
        seen["symbol"] = symbol
        return {"symbol": symbol, "api": symbol, "spot": 772.3,
                "expirations": ["2026-08-14"], "strikes": {"2026-08-14": [770.0]},
                "error": None}

    monkeypatch.setattr(handlers.compute, "em_chain_meta", _rec)
    handlers.handle_command(bus, Command(type="em_chain", args={"symbol": "SPY"}))
    assert seen["symbol"] == "SPY"
    cached = bus.cache[handlers.CACHE_EM_CHAIN]
    assert cached["expirations"] == ["2026-08-14"]
```

Match the `_FakeBus` / cache-inspection idiom already used in that file — read
`test_calc_load_command_caches_chain` first and copy its shape exactly.

**Step 2: Run to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_handlers.py -q -k em_chain
```

Expected: FAIL — `AttributeError: … has no attribute 'CACHE_EM_CHAIN'`.

**Step 3: Implement**

Beside `CACHE_EXPECTED_MOVE` (line 272):

```python
CACHE_EM_CHAIN = "cache:options:em_chain"
EVENT_EM_CHAIN = "events:options:em_chain"
```

In `handle_command`, immediately after the `expected_move` branch:

```python
    elif command.type == "em_chain":
        res = compute.em_chain_meta((command.args or {}).get("symbol", "SPY"))
        version = bus.cache_set(CACHE_EM_CHAIN, res)
        bus.publish(EVENT_EM_CHAIN, {"version": version})
```

Add to `handle_command`'s docstring, after the `expected_move` line:
`` `em_chain` (args symbol) → expirations + strike ladders for the Expected Move dropdowns. ``

**Step 4: Run to verify it passes**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_handlers.py -q
```

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(options): em_chain command publishes cache:options:em_chain"
```

---

## Task 5: `expected_move_figure` legs override

**Files:**
- Modify: `webgui/pages/options/expected_move.py:68-124`
- Test: `webgui/tests/test_expected_move.py`

Lets a strike change repaint the plotLine **locally**, with no service round-trip.

**Step 1: Write the failing tests**

```python
def test_expected_move_figure_legs_override_wins():
    p = _payload()
    p["legs"] = [{"strike": 100.0, "option_type": "put", "side": "short"}]
    fig = em.expected_move_figure(
        p, legs=[{"strike": 105.0, "option_type": "call", "side": "long"}])
    lines = fig["yAxis"]["plotLines"]
    assert [ln["value"] for ln in lines] == [105.0]
    assert lines[0]["color"] == em.CALL_COLOR


def test_expected_move_figure_legs_none_falls_back_to_payload():
    p = _payload()
    p["legs"] = [{"strike": 100.0, "option_type": "put", "side": "short"}]
    fig = em.expected_move_figure(p)
    assert [ln["value"] for ln in fig["yAxis"]["plotLines"]] == [100.0]


def test_expected_move_figure_legs_empty_list_clears_lines():
    p = _payload()
    p["legs"] = [{"strike": 100.0, "option_type": "put", "side": "short"}]
    assert em.expected_move_figure(p, legs=[])["yAxis"]["plotLines"] == []
```

**Step 2: Run to verify they fail**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_expected_move.py -q -k legs)
```

**Step 3: Implement**

Change the signature and the `plotLines` line only:

```python
def expected_move_figure(payload, timeframe="daily", legs=None):
    """Highcharts options for the candlestick + EM cone + leg lines.

    ``legs`` overrides the payload's own legs when given (INCLUDING an empty
    list, which clears the lines) — the page passes its current strike/type
    selection so a strike change repaints locally instead of re-running the
    service. ``timeframe`` is accepted for future intraday support.
    """
```

and:

```python
                  "plotLines": leg_lines(p.get("legs") if legs is None else legs)},
```

**Step 4: Run to verify they pass** (whole file), **Step 5: Commit**

```bash
git add webgui/pages/options/expected_move.py webgui/tests/test_expected_move.py
git commit -m "feat(webgui): expected_move_figure accepts a legs override"
```

---

## Task 6: Page dropdowns + wiring

**Files:**
- Modify: `webgui/pages/options/expected_move.py:127-223` (`render`)
- Modify: `webgui/pages/options/inputs.py` (`bind_symbol_load` docstring)
- Test: `webgui/tests/test_expected_move.py`

**Step 1: Write the failing tests** for the two new pure helpers

```python
def test_strike_options_labels_are_trimmed():
    assert em.strike_options([765.0, 770.5]) == {765.0: "765", 770.5: "770.5"}
    assert em.strike_options([]) == {}


def test_expiry_options_labels_carry_dte():
    import datetime as dt
    today = dt.date(2026, 8, 12)
    opts = em.expiry_options(["2026-08-14", "2026-09-18"], today=today)
    assert opts["2026-08-14"] == "2026-08-14  (2d)"
    assert opts["2026-09-18"] == "2026-09-18  (37d)"
    assert em.expiry_options([], today=today) == {}


def test_expiry_options_tolerates_junk():
    import datetime as dt
    opts = em.expiry_options(["nope"], today=dt.date(2026, 8, 12))
    assert opts["nope"] == "nope"
```

**Step 2: Run to verify they fail**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_expected_move.py -q -k "strike_options or expiry_options")
```

**Step 3: Implement the pure helpers** (module level, beside `em_lookback_options`)

```python
def strike_options(strikes):
    """{strike_float: label} for the strike ui.select — trailing .0 trimmed."""
    return {float(s): f"{float(s):g}" for s in (strikes or [])}


def expiry_options(expirations, today=None):
    """{expiry: "YYYY-MM-DD  (Nd)"} for the expiry ui.select.

    The DTE suffix is what makes the list scannable — a bare date column of 50
    weeklies does not tell you which one is the 0-DTE. Unparseable entries fall
    back to the raw string rather than being dropped."""
    import datetime as dt

    today = today or dt.date.today()
    out = {}
    for e in expirations or []:
        try:
            out[e] = f"{e}  ({(dt.date.fromisoformat(str(e)) - today).days}d)"
        except (ValueError, TypeError):
            out[e] = str(e)
    return out
```

**Step 4: Rewire `render()`**

Replace the input row (lines 148-156) with:

```python
    state = {"ver": None, "chain_ver": None, "last": None,
             "payload": None, "chain": {}}

    with ui.row().classes("items-end gap-3 flex-wrap"):
        symbol_in = select_all_on_focus(ui.input("Symbol", value="SPY").classes("w-28"))
        expiry_sel = ui.select({}, label="Expiry", with_input=True).classes("w-56")
        strike_sel = ui.select({}, label="Strike (optional)",
                               with_input=True, clearable=True).classes("w-40")
        type_tog = ui.toggle(["put", "call"], value="put")
        lookback_sel = ui.select(em_lookback_options(), value="auto",
                                 label="Look-back").classes("w-40")
        draw_btn = ui.button("Draw", icon="show_chart", color=None).props("no-caps").classes(BTN_3D)
        status = ui.label("").classes("opacity-70 text-sm")
```

Then, after the chart element:

```python
    def _current_legs():
        """The leg list for the CURRENT strike/type selection (may be empty)."""
        if strike_sel.value is None:
            return []
        return [{"strike": float(strike_sel.value),
                 "option_type": type_tog.value, "side": "short"}]

    def _repaint(payload=None):
        if payload is not None:
            state["payload"] = payload
            err = (payload or {}).get("error")
            spec = ((payload or {}).get("lookback") or {}).get("label") or ""
            status.text = err or (f"Look-back: {spec}" if spec else "")
        chart.options = expected_move_figure(state["payload"] or {},
                                             legs=_current_legs())
        chart.update()
```

`_enqueue` keeps its body but sends `_current_legs()`; `_draw` becomes:

```python
    @guard
    def _draw():
        _enqueue({"symbol": (symbol_in.value or "").replace("$", "").upper(),
                  "expiry": expiry_sel.value or "", "legs": _current_legs()})
```

Add the chain load, the strike-local repaint, and the chain poll:

```python
    @guard
    def _load_chain():
        sym = (symbol_in.value or "").replace("$", "").upper()
        if not sym:
            return
        bus_client.request("options", {"type": "em_chain", "args": {"symbol": sym}})
        status.text = f"Loading {sym} expirations…"

    @guard
    def _strike_changed():
        # Local-only: the strike is a plotLine, and the candles/cone do not
        # depend on it. No command, no chain refetch.
        _repaint()

    @guard
    def _expiry_changed():
        _fill_strikes()
        if expiry_sel.value:
            _draw()

    def _fill_strikes():
        ladder = (state["chain"].get("strikes") or {}).get(expiry_sel.value) or []
        keep = strike_sel.value
        strike_sel.options = strike_options(ladder)
        strike_sel.value = keep if keep in strike_sel.options else None
        strike_sel.update()

    @guard
    def _poll_chain():
        version = bus_client.read_version("options:em_chain")
        if version == state["chain_ver"]:
            return
        state["chain_ver"] = version
        meta = bus_client.read("options:em_chain") or {}
        state["chain"] = meta
        if meta.get("error"):
            status.text = meta["error"]
            return
        keep = expiry_sel.value
        expiry_sel.options = expiry_options(meta.get("expirations") or [])
        expiry_sel.value = keep if keep in expiry_sel.options else None
        expiry_sel.update()
        _fill_strikes()
```

Wiring (replacing the old Enter handlers):

```python
    draw_btn.on_click(_draw)
    bind_symbol_load(symbol_in, _load_chain)          # Enter OR tab-out
    expiry_sel.on_value_change(lambda e: _expiry_changed())
    strike_sel.on_value_change(lambda e: _strike_changed())
    type_tog.on_value_change(lambda e: _strike_changed())
    lookback_sel.on_value_change(lambda e: _lookback_changed())
```

Import `bind_symbol_load` alongside `select_all_on_focus`. The handoff block
keeps its behavior and additionally seeds the dropdown and loads the chain:

```python
    pending = handoff.take_pending_expected_move()
    if pending:
        symbol_in.value = pending.get("symbol") or symbol_in.value
        if pending.get("expiry"):
            # Seed the select with the handed expiry so it shows BEFORE the
            # chain lands; _poll_chain keeps it if the real list contains it.
            expiry_sel.options = expiry_options([pending["expiry"]])
            expiry_sel.value = pending["expiry"]
        state["ver"] = bus_client.read_version("options:expected_move")
        state["chain_ver"] = bus_client.read_version("options:em_chain")
        _enqueue(pending)
        _load_chain()
    else:
        state["ver"] = bus_client.read_version("options:expected_move")
        state["chain_ver"] = bus_client.read_version("options:em_chain")
        _load_chain()

    ui.timer(1.0, _poll)
    ui.timer(1.0, _poll_chain)
```

Delete the `import datetime as dt` default-expiry block — the dropdown replaces it.

⚠ Assigning `expiry_sel.value` fires `on_value_change`. Guard the handoff seed
against a spurious `_draw()` with a `state["seeding"]` flag checked at the top of
`_expiry_changed`, or set `.value` **before** wiring `on_value_change` (the idiom
`gamma.render()` already uses for its symbol dropdown — prefer that).

**Step 5: Update the `bind_symbol_load` docstring** in `inputs.py` — it cites
Expected Move as the `tab=False` example ("Expected Move needs an expiry too, so
tabbing OUT of the symbol must not submit"). That is now false: tab-out loads the
CHAIN, not the draw. Replace that clause with a note that Expected Move now uses
the default `tab=True` because its tab-out triggers the chain load.

**Step 6: Run the full webgui suite**

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q)
```

Expected: 1190+ green (the new tests raise the count). Baseline is all-green here
— any failure is yours.

**Step 7: Commit**

```bash
git add webgui/pages/options/expected_move.py webgui/pages/options/inputs.py webgui/tests/test_expected_move.py
git commit -m "feat(webgui): Expected Move selects expiry + strike from the live chain"
```

---

## Task 7: Live verification

Tests do not prove a NiceGUI page renders. Per the repo's development rule, this
must be **seen working** before it is promoted.

**Step 1: Restart `options_svc`** so the new `em_chain` command exists. Either
`/status` → the options card's Restart button, or `tools\restart_one.bat`.
A running service is stale code — the command will be silently ignored otherwise.

**Step 2: Redis-driven end-to-end check** (bypasses the browser, the most
reliable 3-tier probe):

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -c "from shared.bus import Bus; b=Bus(); b.enqueue_command('cmd:options', {'type':'em_chain','args':{'symbol':'SPY'}}); import time; time.sleep(3); m=b.cache_get('cache:options:em_chain').payload; print(len(m['expirations']), m['expirations'][:3]); print(m['strikes'][m['expirations'][0]][:5])"
```

⚠ `cache_get` returns a **CacheEnvelope** — read `.payload`, not `.get()`.

**Step 3: Browser check.** `preview_start` the `webgui` dev server, open
`/options/expected-move`, and confirm: the expiry dropdown populates after
typing a symbol and tabbing out; picking an expiry draws; the **last candle is
today's date**; changing the strike moves the line with no visible reload.
Screenshot it.

**Step 4: Update `CLAUDE.md`** — the `/options/expected-move` route-table row
still describes "standalone w/ symbol+expiry input". Rewrite it to name the
expiry/strike dropdowns, the `cache:options:em_chain` key, and the synthetic
current-day candle (including the premarket gate and the post-market close
approximation, so the next reader does not "fix" them).

**Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): Expected Move by-expiration selection + today's candle"
```
