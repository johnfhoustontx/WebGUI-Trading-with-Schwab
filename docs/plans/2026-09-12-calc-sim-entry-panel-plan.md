# Calculator + Simulator Entry Panel — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Calculator's and Simulator's button-driven, dropdown-heavy
trade entry with one shared panel — symbol bar, expiry strip, a clickable
chain grid (Bid → SELL, Ask → BUY) beside a compact leg table — that prices and
recalculates on every edit.

**Architecture:** Tier 2 widens the thinned chain (OI, volume, all Greeks) and
has `sim_fetch` publish that same thinned chain to `cache:options:sim_chain`
from its existing `/chains` call. Tier 1 gains two PURE modules
(`chain_grid.py` readers + row builders, `entry.py` stepping/pick/debounce), a
widget module (`entry_panel.py`: symbol bar, expiry strip, grid), and a third
`leg_editor` layout, `"table"`. Each page mounts the panel on top and keeps its
own results area below.

**Tech Stack:** NiceGUI (Tailwind-first `.classes()`, no `.style()`), Redis bus
(`bus_client`), pytest. Design: [the design doc](2026-09-12-calc-sim-entry-panel-design.md).

---

## Ground rules for whoever executes this

- **Python:** `PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (the
  worktree has no venv). Run webgui tests as `cd webgui && "$PY" -m pytest …`,
  service tests from the worktree root one service at a time, options-scanner
  tests as `cd options-scanner && "$PY" -m pytest tests -p no:randomly`.
- **Baseline first (Task 0).** Compare the failing **SET** (node IDs), not the
  count, at the end.
- **Never weaken a test to make it pass.** If an existing assertion conflicts
  with the design, stop and flag it; only tests whose *subject* is removed
  (e.g. the LOAD CHAIN button) are rewritten, and the rewrite must assert the
  replacement behaviour.
- **Tailwind-first:** no `.style(`, no `:style=`, no `props("style=…")`. Colours
  from a finite token dict, never a runtime-built hex.
- **UI words:** whole words (Delta, Gamma, Theta, Vega, Volume, Mark); trader
  acronyms stay (IV, OI, DTE).
- Commit after each task: `git add <exact files>` then `git commit` — never
  `git add -A` (other sessions share this repo).

---

### Task 0: Baseline

**Step 1:** Record baselines (save output under the session scratchpad, not the repo):

```bash
cd webgui && "$PY" -m pytest -q -rfs > "$SCRATCH/webgui-before.txt"; cd ..
"$PY" -m pytest services/options_svc -q -rfs > "$SCRATCH/optsvc-before.txt"
cd options-scanner && "$PY" -m pytest tests -p no:randomly -q -rfs > "$SCRATCH/scanner-before.txt"; cd ..
```

**Step 2:** Note the FAILED/SKIPPED node IDs. No commit.

---

## Phase 1 — Tier 2

### Task 1: Widen the thinned calc chain

**Files:**
- Modify: `services/options_svc/compute.py:6800` (`_CALC_CONTRACT_FIELDS`) and
  the `thin_calc_chain` docstring
- Modify: `services/options_svc/tests/test_compute.py:5707`

**Step 1: Update the failing test** — the whitelist is the subject:

```python
def test_thin_calc_chain_keeps_only_the_fields_the_pages_read():
    chain = {"symbol": "$SPX", "status": "SUCCESS", "underlyingPrice": 6400.0,
             "putExpDateMap": {"2026-08-21:1": {"6000.0": [_fat_contract()]}},
             "callExpDateMap": {"2026-08-21:1": {"6400.0": [
                 _fat_contract(putCall="CALL", delta=0.5)]}}}
    out = compute.thin_calc_chain(chain)
    put = out["putExpDateMap"]["2026-08-21:1"]["6000.0"][0]
    assert set(put) == {"bid", "ask", "mark", "volatility", "delta", "gamma",
                        "theta", "vega", "openInterest", "totalVolume"}
    assert put["bid"] == 1.0 and put["delta"] == -0.25
    # the bulk per-contract fields the grid never reads stay OUT
    for dropped in ("description", "exchangeName", "quoteTimeInLong",
                    "intrinsicValue", "rho"):
        assert dropped not in put
    call = out["callExpDateMap"]["2026-08-21:1"]["6400.0"][0]
    assert call["delta"] == 0.5
    assert set(out) == {"putExpDateMap", "callExpDateMap"}
```

Check `_fat_contract()` (line ~5690) carries `gamma`, `theta`, `vega`,
`openInterest`, `totalVolume`, `rho`; add any it lacks to its dict (fixture
completeness, not weakening).

**Step 2:** `"$PY" -m pytest services/options_svc/tests/test_compute.py -k thin_calc_chain -v` → FAIL (set mismatch).

**Step 3:** Implement:

```python
# The chain grid's pickable columns + the leg pricing/delta readers. Widened
# 2026-09-12 from five fields for the entry panel's chain grid (OI, volume and
# the Greeks). Still a whitelist: the unthinned chain was 8.77 MB.
_CALC_CONTRACT_FIELDS = ("bid", "ask", "mark", "volatility", "delta", "gamma",
                         "theta", "vega", "openInterest", "totalVolume")
```

Update the docstring sentence "no page reads any other per-contract field" to
name the grid.

**Step 4:** Re-run → PASS. Also run `-k "calc_load or calc_chain"` → PASS.

**Step 5:** Measure the payload on a realistic chain if one is available in a
fixture; otherwise record "est. ~1.3 MB" as unmeasured in the CHANGELOG task.

**Step 6:** Commit `feat(calc): keep OI, volume and the Greeks in the cached chain`.

---

### Task 2: `fetch_snapshot` hands its raw chain to a callback

**Files:**
- Modify: `options-scanner/options_simulator/data.py:82`
- Test: `options-scanner/tests/test_options_simulator.py`

**Step 1: Failing test** (use the file's existing fake client pattern; read it
first and reuse its helper for a 200 chain response):

```python
def test_fetch_snapshot_hands_the_raw_chain_to_on_chain():
    seen = []
    client = _FakeClient(chain=_CHAIN)          # existing helper in this file
    snap = data.fetch_snapshot(client, "SPY", on_chain=seen.append)
    assert seen == [_CHAIN]                     # the SAME dict, once
    assert snap.symbol == "SPY"


def test_fetch_snapshot_on_chain_is_optional():
    data.fetch_snapshot(_FakeClient(chain=_CHAIN), "SPY")   # no raise
```

If no fake client helper exists, write a minimal one returning an object with
`status_code=200` and `.json()`; the price-history call may return an empty
payload.

**Step 2:** Run → FAIL (`unexpected keyword argument 'on_chain'`).

**Step 3:** Add `on_chain=None` to the signature; immediately after
`chain = chain or {}`:

```python
    if on_chain is not None:
        on_chain(chain)
```

Docstring: "``on_chain(chain)`` receives the raw chain dict — how `sim_fetch`
publishes the grid's chain without a second `/chains` call."

**Step 4:** Run → PASS. **Step 5:** Commit `feat(sim): fetch_snapshot exposes its raw chain`.

---

### Task 3: `sim_fetch` publishes `cache:options:sim_chain`

**Files:**
- Modify: `services/options_svc/compute.py:7135` (`sim_fetch`)
- Modify: `services/options_svc/handlers.py:373` (constants) and `:2571`
- Test: `services/options_svc/tests/test_compute.py:2506`, `services/options_svc/tests/test_handlers.py:1272`

**Step 1: Failing compute test** — extend the fake `fetch_snapshot` in
`_patch_sim` to accept `**kwargs` and call `kwargs["on_chain"](RAW)` when given,
and count calls:

```python
def test_sim_fetch_returns_the_thinned_chain_from_the_one_fetch(monkeypatch):
    snap = _SimSnap("SPY", 450.0, [_SimRow("2026-06-19", "call", 450)])
    raw = {"callExpDateMap": {"2026-06-19:7": {"450.0": [
               {"bid": 1.0, "ask": 1.2, "mark": 1.1, "openInterest": 900,
                "description": "SPY 06/19/2026 450 C"}]}},
           "putExpDateMap": {}}
    calls = _patch_sim(monkeypatch, snap, raw_chain=raw)   # returns a call counter
    compute._SIM_SNAPSHOTS.clear()

    meta = compute.sim_fetch("SPY")

    assert calls["fetch_snapshot"] == 1
    c = meta["chain"]["callExpDateMap"]["2026-06-19:7"]["450.0"][0]
    assert c["openInterest"] == 900 and "description" not in c
```

Update `_patch_sim`'s signature (default `raw_chain=None` → on_chain not called)
so existing tests keep passing unchanged.

**Step 2: Failing handler test:**

```python
def test_sim_fetch_command_publishes_the_chain_before_the_meta(monkeypatch):
    bus = Bus(fake=True)
    chain = {"callExpDateMap": {}, "putExpDateMap": {}}
    meta = {"symbol": "SPY", "spot": 450.0, "n_contracts": 0,
            "expiries": [], "strikes": {}, "chain": chain}
    monkeypatch.setattr(handlers.compute, "sim_fetch", lambda symbol: dict(meta))
    order = []
    real = bus.cache_set
    def _spy(key, payload, **kw):
        order.append(key)
        return real(key, payload, **kw)
    monkeypatch.setattr(bus, "cache_set", _spy)

    handlers.handle_command(bus, Command(type="sim_fetch", args={"symbol": "SPY"}))

    assert order == ["cache:options:sim_chain", "cache:options:sim_meta"]
    assert bus.cache_get("cache:options:sim_chain").payload == {"symbol": "SPY", "chain": chain}
    assert "chain" not in bus.cache_get("cache:options:sim_meta").payload
```

Also update `test_sim_fetch_command_caches_meta` only if it breaks because meta
now lacks `chain` — it should not (its fake meta has no chain key; `pop` with a
default).

**Step 3:** Run both → FAIL.

**Step 4: Implement** in `sim_fetch`:

```python
    raw = {}
    snap = sdata.fetch_snapshot(_proxy.schwab_py_client, symbol,
                                on_chain=lambda c: raw.update(chain=c))
    ...
    return {..., "chain": thin_calc_chain(raw.get("chain"))}
```

Handlers:

```python
CACHE_SIM_CHAIN = "cache:options:sim_chain"
EVENT_SIM_CHAIN = "events:options:sim_chain"
...
    elif command.type == "sim_fetch":
        meta = compute.sim_fetch(command.args.get("symbol", "SPY"))
        # The grid's chain goes FIRST: the page reacts to the meta version and
        # then reads the chain, so chain-already-written is the only skew it
        # can see (the gamma history ordering, same reason).
        chain = meta.pop("chain", None)
        cver = bus.cache_set(CACHE_SIM_CHAIN, {"symbol": meta.get("symbol"), "chain": chain})
        bus.publish(EVENT_SIM_CHAIN, {"version": cver})
        version = bus.cache_set(CACHE_SIM_META, meta)
        bus.publish(EVENT_SIM_META, {"version": version})
```

**Step 5:** Run both → PASS; run all `-k sim_` in both files → PASS.

**Step 6:** Commit `feat(sim): publish the thinned chain as cache:options:sim_chain`.

---

## Phase 2 — Tier 1 pure modules

### Task 4: `chain_grid.py` — move the chain readers

**Files:**
- Create: `webgui/pages/options/chain_grid.py`
- Modify: `webgui/pages/options/calculator.py:135-330, 954-975`
- Test: `webgui/tests/test_chain_grid.py`

**Step 1:** Write `test_chain_grid.py::test_calculator_reexports_the_readers`:

```python
from pages.options import calculator as calc, chain_grid as cg

def test_calculator_reexports_the_readers():
    for name in ("extract_atm_iv", "extract_premium", "extract_delta", "leg_delta",
                 "position_delta", "chain_expiries", "chain_strikes", "_find_contract"):
        assert getattr(calc, name) is getattr(cg, name)
```

**Step 2:** Run → FAIL (no module).

**Step 3:** Move `extract_atm_iv`, `_find_contract`, `_finite`, `extract_premium`,
`_DELTA_LIMIT`, `extract_delta`, `leg_delta`, `position_delta`, `chain_expiries`,
`chain_strikes` verbatim (docstrings and comments included) into
`chain_grid.py` (module docstring: "Pure readers over the thinned option chain,
shared by the Calculator and the Simulator. No nicegui import."). In
`calculator.py` replace them with:

```python
# The chain readers moved to chain_grid (2026-09-12) so the Simulator can read
# the same chain without importing another PAGE. Re-exported by name.
from .chain_grid import (extract_atm_iv, _find_contract, _finite, extract_premium,
                         _DELTA_LIMIT, extract_delta, leg_delta, position_delta,
                         chain_expiries, chain_strikes)
```

Keep `_finite` usable in calculator (other helpers call it).

**Step 4:** `cd webgui && "$PY" -m pytest tests/test_chain_grid.py tests/test_options_calculator.py tests/test_calculator_stock_leg.py -q` → PASS.

**Step 5:** Commit `refactor(calc): chain readers into chain_grid`.

---

### Task 5: `chain_grid.py` — columns, cell text, grid rows, expiry pills

**Files:** Modify `webgui/pages/options/chain_grid.py`; Test `webgui/tests/test_chain_grid.py`

**Step 1: Failing tests:**

```python
import datetime as dt
import math

def _c(**kw):
    base = {"bid": 1.0, "ask": 1.2, "mark": 1.1, "delta": 0.4, "volatility": 20.0,
            "gamma": 0.03, "theta": -0.05, "vega": 0.1, "openInterest": 4120,
            "totalVolume": 88}
    base.update(kw)
    return [base]

CHAIN = {
    "callExpDateMap": {"2026-09-19:7": {f"{k}.0": _c(delta=round(0.9 - k / 1000, 2))
                                         for k in range(560, 581, 5)}},
    "putExpDateMap": {"2026-09-19:7": {f"{k}.0": _c(delta=-0.3)
                                        for k in range(560, 581, 5)}},
}

def test_default_columns_are_bid_ask_delta_oi():
    assert cg.DEFAULT_COLUMNS == ["bid", "ask", "delta", "openInterest"]

def test_parse_columns_keeps_known_in_registry_order_and_falls_back():
    assert cg.parse_columns(["openInterest", "bid"]) == ["bid", "openInterest"]
    assert cg.parse_columns(["nope"]) == cg.DEFAULT_COLUMNS
    assert cg.parse_columns(None) == cg.DEFAULT_COLUMNS
    assert cg.parse_columns("bid") == cg.DEFAULT_COLUMNS

def test_parse_columns_always_keeps_bid_and_ask():
    # Bid and Ask are the click targets; a grid without them cannot add a leg.
    assert cg.parse_columns(["delta"]) == ["bid", "ask", "delta"]

def test_cell_text_formats_by_field_and_dashes_non_readings():
    assert cg.cell_text("bid", 2.05) == "2.05"
    assert cg.cell_text("bid", 0) == "—"          # no market
    assert cg.cell_text("delta", -0.312) == "-0.31"
    assert cg.cell_text("delta", -999.0) == "—"   # Schwab's missing-greek sentinel
    assert cg.cell_text("gamma", 0.0312) == "0.031"
    assert cg.cell_text("volatility", 22.46) == "22.5"
    assert cg.cell_text("openInterest", 4120) == "4.1k"
    assert cg.cell_text("openInterest", 0) == "0"   # zero OI is a real reading
    assert cg.cell_text("totalVolume", 1_250_000) == "1.3M"
    assert cg.cell_text("bid", None) == "—"
    assert cg.cell_text("bid", math.nan) == "—"
    assert cg.cell_text("bid", True) == "—"

def test_grid_rows_window_around_spot_and_flag_itm():
    g = cg.chain_grid_rows(CHAIN, "2026-09-19", spot=571.0, above=1, below=1)
    assert [r["strike"] for r in g["rows"]] == [570.0, 575.0]
    assert g["more_below"] is True and g["more_above"] is True
    r570, r575 = g["rows"]
    assert r570["call_itm"] is True and r570["put_itm"] is False
    assert r575["call_itm"] is False and r575["put_itm"] is True
    assert r570["atm"] is True                    # nearest strike to spot
    assert r570["call"]["openInterest"] == 4120

def test_grid_rows_missing_side_is_an_empty_dict():
    chain = {"callExpDateMap": {"2026-09-19:7": {"570.0": _c()}}, "putExpDateMap": {}}
    g = cg.chain_grid_rows(chain, "2026-09-19", spot=570.0)
    assert g["rows"][0]["put"] == {}

def test_grid_rows_total_on_junk():
    assert cg.chain_grid_rows(None, "2026-09-19", 570.0)["rows"] == []
    assert cg.chain_grid_rows(CHAIN, "2031-01-01", 570.0)["rows"] == []
    assert cg.chain_grid_rows(CHAIN, "2026-09-19", None)["rows"] != []   # no spot: top of ladder, no ITM flags

def test_expiry_pills_label_and_dte():
    pills = cg.expiry_pills(["2026-09-12", "2026-09-19", "junk"], today=dt.date(2026, 9, 12))
    assert pills == [{"value": "2026-09-12", "label": "Sep 12", "dte": 0},
                     {"value": "2026-09-19", "label": "Sep 19", "dte": 7}]
```

**Step 2:** Run → FAIL.

**Step 3: Implement:**

```python
#: Registry order IS the column order on screen (left→right per side).
GRID_COLUMNS = {
    "bid": "Bid", "ask": "Ask", "mark": "Mark", "delta": "Delta",
    "volatility": "IV", "gamma": "Gamma", "theta": "Theta", "vega": "Vega",
    "openInterest": "OI", "totalVolume": "Volume",
}
DEFAULT_COLUMNS = ["bid", "ask", "delta", "openInterest"]
_REQUIRED = ("bid", "ask")        # the click targets
_PRICE = {"bid", "ask", "mark"}
_GREEK_LIMIT = 999.0              # Schwab's missing-greek sentinel is -999.0


def parse_columns(value):
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_COLUMNS)
    picked = {v for v in value if v in GRID_COLUMNS}
    if not picked:
        return list(DEFAULT_COLUMNS)
    picked.update(_REQUIRED)
    return [k for k in GRID_COLUMNS if k in picked]


def _compact(n):
    for size, suffix in ((1_000_000, "M"), (1_000, "k")):
        if abs(n) >= size:
            return f"{n / size:.1f}{suffix}"
    return f"{int(n)}"


def cell_text(field, value):
    v = _finite(value)
    if v is None:
        return "—"
    if field in _PRICE:
        return f"{v:.2f}" if v > 0 else "—"
    if field in ("delta", "gamma", "theta", "vega") and abs(v) >= _GREEK_LIMIT:
        return "—"
    if field == "delta" or field in ("theta", "vega"):
        return f"{v:.2f}"
    if field == "gamma":
        return f"{v:.3f}"
    if field == "volatility":
        return f"{v:.1f}"
    return _compact(v)            # openInterest / totalVolume


def _side_map(chain, map_key, expiry):
    out = {}
    for exp_key, strikes in ((chain or {}).get(map_key) or {}).items():
        if exp_key.split(":")[0] != str(expiry):
            continue
        for sk, contracts in (strikes or {}).items():
            try:
                k = float(sk)
            except (TypeError, ValueError):
                continue
            if isinstance(contracts, list) and contracts and isinstance(contracts[0], dict):
                out[k] = contracts[0]
    return out


def chain_grid_rows(chain, expiry, spot, above=15, below=15):
    """Rows for one expiry, ``below`` strikes at/under spot + ``above`` over it.

    TOTAL: junk chain / unknown expiry → no rows. With no usable spot the
    window starts at the bottom of the ladder and nothing is flagged ITM/ATM."""
    if not isinstance(chain, dict):
        return {"rows": [], "more_above": False, "more_below": False}
    calls = _side_map(chain, "callExpDateMap", expiry)
    puts = _side_map(chain, "putExpDateMap", expiry)
    strikes = sorted(set(calls) | set(puts))
    s = _finite(spot)
    if not strikes:
        return {"rows": [], "more_above": False, "more_below": False}
    if s is None:
        window, lo, hi = strikes[:above + below], 0, min(len(strikes), above + below)
        atm = None
    else:
        under = [k for k in strikes if k <= s]
        lo = max(len(under) - below, 0)
        hi = min(len(under) + above, len(strikes))
        window = strikes[lo:hi]
        atm = min(strikes, key=lambda k: abs(k - s))
    rows = [{"strike": k, "call": dict(calls.get(k) or {}), "put": dict(puts.get(k) or {}),
             "call_itm": s is not None and k < s, "put_itm": s is not None and k > s,
             "atm": k == atm}
            for k in window]
    return {"rows": rows, "more_below": lo > 0, "more_above": hi < len(strikes)}


def expiry_pills(expiries, today):
    out = []
    for e in expiries or []:
        try:
            d = dt.date.fromisoformat(str(e))
        except ValueError:
            continue
        out.append({"value": str(e), "label": d.strftime("%b %d").replace(" 0", " "),
                    "dte": (d - today).days})
    return out
```

(Adjust `_compact` so `1_250_000` → `"1.3M"` and `4120` → `"4.1k"`; note
Python's banker's rounding — if `f"{1.25:.1f}"` yields `1.2`, the test value is
chosen so `1_250_000/1e6 = 1.25` — change the test input to `1_260_000`/`"1.3M"`
rather than altering rounding, and say so in the commit.)

**Step 4:** Run → PASS. **Step 5:** Commit `feat(entry): chain grid rows, columns and expiry pills`.

---

### Task 6: `entry.py` — strike stepping, picks, price refill, debounce

**Files:** Create `webgui/pages/options/entry.py`; Test `webgui/tests/test_entry.py`

**Step 1: Failing tests:**

```python
from pages.options import entry as E

LADDER = [560.0, 565.0, 570.0, 575.0]

def test_step_strike_moves_one_real_strike_and_stops_at_the_ends():
    assert E.step_strike(LADDER, 570.0, +1) == 575.0
    assert E.step_strike(LADDER, 570.0, -1) == 565.0
    assert E.step_strike(LADDER, 575.0, +1) == 575.0
    assert E.step_strike(LADDER, 560.0, -1) == 560.0

def test_step_strike_snaps_an_off_ladder_value_first():
    assert E.step_strike(LADDER, 571.0, 0) == 570.0
    assert E.step_strike(LADDER, 572.6, +1) == 575.0     # snapped 575, capped

def test_step_strike_total():
    assert E.step_strike([], 570.0, +1) is None
    assert E.step_strike(LADDER, None, +1) == 560.0

def test_parse_strike_text():
    assert E.parse_strike_text("571", LADDER) == 570.0
    assert E.parse_strike_text(" 565.00 ", LADDER) == 565.0
    assert E.parse_strike_text("abc", LADDER) is None
    assert E.parse_strike_text("", LADDER) is None

def test_leg_from_pick_bid_sells_ask_buys_at_the_given_price():
    sell = E.leg_from_pick("bid", "put", 565.0, "2026-09-19", price=2.07)
    assert sell == {"option_type": "put", "side": "short", "strike": 565.0,
                    "expiry": "2026-09-19", "qty": 1, "premium": 2.07}
    assert E.leg_from_pick("ask", "call", 575.0, "2026-09-19", price=None)["side"] == "long"

def test_leg_from_pick_rejects_an_unknown_column():
    import pytest
    with pytest.raises(ValueError):
        E.leg_from_pick("mark", "put", 565.0, "2026-09-19", price=1.0)

def test_refill_on_only_for_identity_fields_and_never_a_manual_price():
    assert E.should_refill("strike", manual=False) is True
    assert E.should_refill("expiry", manual=False) is True
    assert E.should_refill("option_type", manual=False) is True
    assert E.should_refill("strike", manual=True) is False
    assert E.should_refill("qty", manual=False) is False
    assert E.should_refill("side", manual=False) is False

def test_debounce_fires_once_after_the_last_poke():
    d = E.Debounce(0.3)
    assert d.ready(0.0) is False
    d.poke(0.0); d.poke(0.2)
    assert d.ready(0.4) is False     # 0.2 + 0.3 = 0.5
    assert d.ready(0.5) is True
    assert d.ready(0.9) is False     # consumed
```

**Step 2:** Run → FAIL.

**Step 3: Implement:**

```python
"""Pure trade-entry rules shared by the Calculator and the Simulator (no nicegui)."""
from .leg_editor import coerce_strike

_PICK_SIDE = {"bid": "short", "ask": "long"}
_REFILL_FIELDS = ("strike", "expiry", "option_type")


def step_strike(strikes, current, step):
    xs = sorted({float(s) for s in (strikes or []) if isinstance(s, (int, float))})
    if not xs:
        return None
    if current is None:
        return xs[0]
    snapped = coerce_strike(float(current), xs)
    i = xs.index(snapped) + int(step)
    return xs[max(0, min(i, len(xs) - 1))]


def parse_strike_text(text, strikes):
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return step_strike(strikes, v, 0)


def leg_from_pick(column, option_type, strike, expiry, price):
    """A grid click → a leg. ⚠ ``price`` is the MARK whichever side was clicked;
    the column decides only long/short (see the design doc)."""
    if column not in _PICK_SIDE:
        raise ValueError(f"not a pick column: {column!r}")
    return {"option_type": option_type, "side": _PICK_SIDE[column],
            "strike": float(strike), "expiry": expiry, "qty": 1, "premium": price}


def should_refill(field, manual):
    return field in _REFILL_FIELDS and not manual


class Debounce:
    def __init__(self, delay):
        self.delay = float(delay)
        self._due = None

    def poke(self, now):
        self._due = float(now) + self.delay

    def ready(self, now):
        if self._due is not None and float(now) >= self._due:
            self._due = None
            return True
        return False
```

`coerce_strike` lives in `leg_editor.py` (which imports nicegui); `entry.py`
has no reason to avoid that import.

**Step 4:** Run → PASS. **Step 5:** Commit `feat(entry): strike stepping, grid picks, refill rule, debounce`.

---

## Phase 3 — Tier 1 widgets

### Task 7: `leg_editor` `layout="table"`

**Files:** Modify `webgui/pages/options/leg_editor.py`; Test `webgui/tests/test_leg_editor_table.py`

Row, left→right: `#` · **B/S** button (text `BUY`/`SELL`, click flips side) ·
**Qty** `ui.number` (min 1, max 100) · **Expiry** `ui.select` (dense) ·
**Strike** `‹` `ui.input` `›` · **C/P** button (text `CALL`/`PUT`; with
`allow_stock`, cycles call→put→stock) · **Price** (`show_premium` only) number +
a reset `↺` visible only when manual · **Delta** label (`delta_for` only) · ✕.

New `build_leg_editor` args: `price_for=None` (leg → price or None; used for the
refill rule) — inert outside table mode. The manual flag is a private leg key
`_manual_premium`, stripped by `normalize_legs` like `_strike_widget`.

**Step 1: Failing tests** (build inside `with ui.card() as root:` like
`test_leg_editor.py:200`; reuse its `_walk`/element-finding helpers by copying
them, and find controls by class hooks `leg-side`, `leg-type`, `leg-strike`,
`leg-strike-dn`, `leg-strike-up`, `leg-price`, `leg-price-reset`, `leg-remove`):

```python
def test_table_layout_is_accepted(): ...                      # no ValueError
def test_table_side_button_flips_long_short(): ...            # click → get_legs()[0]["side"]
def test_table_type_button_cycles_call_put_and_stock_only_when_allowed(): ...
def test_table_strike_buttons_step_one_real_strike(): ...     # 570 → › → 575
def test_table_strike_input_snaps_typed_text_on_enter(): ...  # "572" + enter → 570
def test_table_strike_arrow_keys_step(): ...                  # keydown.up → 575
def test_table_strike_change_refills_price_from_price_for(): ...
def test_table_typed_price_survives_a_strike_change(): ...
def test_table_price_reset_clears_manual_and_refills(): ...
def test_table_share_leg_disables_strike_and_expiry(): ...
def test_table_remove_respects_min_legs(): ...
def test_table_omits_price_and_delta_columns_when_not_supplied(): ...
def test_normalize_strips_the_manual_flag(): ...
```

Write each body concretely: build the editor with `strikes_for=lambda e, t: [560.0, 565.0, 570.0, 575.0]`,
`expiries_for=lambda: ["2026-09-19"]`, `price_for=lambda leg: {565.0: 2.0, 570.0: 2.5, 575.0: 3.1}.get(leg["strike"])`,
`set_legs([{... "strike": 570.0 ...}])`, fire the handler (button: call the
click listener like `test_options_calculator_apply._click`; input: set
`.value` then invoke the `keydown.enter` listener), assert on `get_legs()`.

**Step 2:** Run → FAIL.

**Step 3: Implement** `_table_body(i, leg, exps, e_val, s_opts, s_val, _lab)`:
- Add `"table"` to `_LAYOUTS`; in `_render` pick `_table_body` and `_table_footer`
  (ADD LEG, optional RESET TO TEMPLATE — same as the card footer's buttons, so
  reuse `_card_footer` if its classes read from `tk`).
- Grid track strings are a finite dict keyed `(show_premium, show_delta)`
  exactly like `_CARD_ROW2_GRIDS`, e.g.
  `"grid grid-cols-[20px_52px_56px_minmax(0,1fr)_minmax(0,1.1fr)_52px_64px_52px_24px] gap-x-1.5 items-center w-full"`.
- `_set_field` for table mode: after writing the field, if
  `entry.should_refill(field, leg.get("_manual_premium"))` and `price_for`,
  set `leg["premium"] = price_for(normalized leg)` (keep the old value when it
  returns None — never zero it); a typed price sets `_manual_premium=True`.
  For `option_type`/`expiry` in table mode call `_render()` (the strike widget is
  an input, not a select, so `_sync_row_strikes` does not apply) — branch on
  `card`/`layout` explicitly.
- Strike input: `ui.input(value=f"{s_val:g}" if s_val is not None else "")`
  `.props("dense input-class=text-right").classes("leg-strike w-full")`,
  `.on("keydown.enter", …parse_strike_text…)`, `.on("blur", …)`,
  `.on("keydown.up", …step +1…)`, `.on("keydown.down", …step -1…)`. Return it
  (the `_strike_widget` registration contract).
- Everything via `.classes()` + `tk` tokens; add token keys `side_long`,
  `side_short`, `toggle`, `manual` to `DEFAULT_CARD_TOKENS` (the test
  `test_default_tokens_cover_every_key_the_card_renders` must keep passing).

**Step 4:** Run the new file + `tests/test_leg_editor.py tests/test_leg_editor_stock.py` → PASS.

**Step 5:** Commit `feat(legs): compact table layout with strike stepping and price refill`.

---

### Task 8: `entry_panel.py` — symbol bar, expiry strip, chain grid widget

**Files:** Create `webgui/pages/options/entry_panel.py`; Modify
`webgui/app_settings.py` (DEFAULTS `"chain_grid_columns": ["bid", "ask", "delta", "openInterest"]`);
Test `webgui/tests/test_entry_panel.py`; Modify `webgui/tests/test_no_inline_style.py:17`
(add `entry_panel.py`, `chain_grid.py`, `entry.py` to the helpers list).

API:

```python
def build_entry_panel(*, tokens=None, strategy_value="PCS", strategy_exclude=(),
                      strategy_classes="w-56", boxed=True):
    """Mount the shared entry panel at the current slot. Returns a handle:

    symbol_in, spot_lbl, refresh_btn, status_lbl, strategy_sel,
    bar_extra (a row slot pages add their own controls to),
    legs_box (the leg editor's container), legs_footer (a row slot),
    set_chain(chain, spot), selected_expiry(), on_expiry(cb), on_pick(cb)
    where cb(column, option_type, strike, expiry).
    """
```

Layout (Tailwind only): a bar row (ticker `ui.input` with class hook
`entry-ticker`, spot label, strategy menu via
`strategy_menu.build_strategy_menu`, `bar_extra` row, `⟳` button, status
label) → the expiry strip (a scrollable `flex no-wrap overflow-x-auto` row of
pill buttons `entry-expiry`, active pill via a finite `{on, off}` class swap,
plus a `⚙` button opening a `ui.menu` of `ui.checkbox` per `GRID_COLUMNS`
entry, Bid/Ask disabled) → `ui.row().classes("w-full items-start gap-3 no-wrap")`
with the grid column `flex-[1.4_1_0%] min-w-0` and the legs column
`flex-1 min-w-[420px]`.

Grid painting (`_paint_grid`): `chain_grid_rows(chain, expiry, spot, above=n, below=n)`
with `n` in panel state (default 12). Header row: call columns (reversed so Bid
sits nearest the strike? — NO: keep thinkorswim order, calls read left→right
`[…cols]`, puts `[…cols]`), strike centre. Each row is a CSS grid with a
finite track string keyed by `len(columns)` (1..10 → build the dict of ten
static strings at import from `GRID_COLUMNS`; the count set is finite).
Bid/Ask cells are `ui.label(...).classes("entry-pick cursor-pointer …")` with
`.on("click", lambda e, …: _pick(col, otype, strike))`; ITM rows get the `itm`
token wash on that side only; the ATM strike gets the `atm` token. "▲ more" /
"▼ more" buttons grow `n` by 10 and repaint. The ATM row is scrolled into view
after paint with
`ui.run_javascript(f"getElement({row.id})?.$el?.scrollIntoView({{block:'center'}})")`.

Column picker writes `app_settings.set("chain_grid_columns", cols)` and
repaints; reads `chain_grid.parse_columns(app_settings.get("chain_grid_columns"))`.

**Step 1: Failing tests** (build in `ui.card()`; drive handlers directly):

```python
def test_panel_paints_one_row_per_windowed_strike(): ...
def test_clicking_a_put_bid_calls_on_pick_with_bid_put_strike_expiry(): ...
def test_clicking_an_expiry_pill_repaints_and_calls_on_expiry(): ...
def test_column_picker_persists_and_repaints(monkeypatch): ...   # patch app_settings.set/get
def test_more_below_extends_the_window(): ...
def test_set_chain_with_no_chain_shows_the_empty_line_and_no_rows(): ...
def test_panel_has_exactly_one_ticker_input(): ...
```

**Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run + the
no-inline-style test → PASS.

**Step 5:** Commit `feat(entry): shared entry panel with chain grid and expiry strip`.

---

## Phase 4 — Pages

### Task 9: Calculator mounts the panel

**Files:** Modify `webgui/pages/options/calculator.py` (`render`, module
docstring); Modify `webgui/tests/test_options_calculator_apply.py`,
`webgui/tests/test_options_calculator.py:1173`.

Changes inside `render()`:
1. Replace the ① STRATEGY / ② SYMBOL / ③ LEGS / ACTIONS columns with
   `panel = entry_panel.build_entry_panel(tokens=_GRID_TOKENS, …)` at the top
   (keep the title bar + status pill). `symbol_in = panel.symbol_in`,
   `strategy_sel = panel.strategy_sel`. Strategy tags go in `panel.bar_extra`;
   the blurb becomes the strategy button's tooltip.
2. **Pricing assumptions**: a `ui.expansion("Pricing assumptions")` (closed)
   under the panel holding PRICE, IV %, RATE %, IV Δ %, CONTRACTS, STRIKES —
   the same widgets and variable names, so `_capture`/`_restore` stay intact.
   `expiry_sel` is removed; its role is `panel.selected_expiry()` (persist key
   `expiry` keeps its name).
3. Legs: `build_leg_editor(panel.legs_box, layout="table", tokens=_LEG_TOKENS,
   show_premium=True, delta_for=_delta_for, allow_stock=True, min_legs=1,
   on_reset=_seed_template, price_for=_price_for, on_change=_on_legs_changed, …)`
   where `_price_for(leg)` = `extract_premium(chain, type, strike, expiry)` with
   the no-expiry fallback (share legs → None).
4. `panel.legs_footer`: **Expected move** and **Copy to Simulator** buttons (same
   handlers), plus the net / max-loss strip labels.
5. **Remove** the LOAD CHAIN, IV UPDATE, FETCH PREMIUMS, CALCULATE buttons.
   `fetch_premiums` becomes `_fill_all_prices()` (silent; used after chain load
   and template seed). `do_calc` loses its `ui.notify("Calculating…")` and its
   warning toasts return silently (the status line already says why).
6. **Auto-recalc:** `recalc = entry.Debounce(0.3)`; every edit path
   (`_on_legs_changed`, pricing-assumption changes, expiry pick, IV applied)
   calls `recalc.poke(time.monotonic())`; `ui.timer(0.1, _recalc_tick)` runs
   `do_calc()` when `recalc.ready(time.monotonic())` and `legs_ready` and a chain
   is loaded. Name the tick `_recalc_tick` (the harness collects timers by name).
7. **Auto IV:** after `_apply_chain` fills prices, call `fetch_iv()` with its
   toasts removed (rename `_auto_iv`); `_apply_iv` pokes `recalc`.
8. **Grid → leg:** `panel.on_pick(lambda col, t, k, e: _add_pick(...))` appends
   `entry.leg_from_pick(col, t, k, e, price=_price_for(...))` via
   `editor.set_legs(editor.get_legs() + [leg])`, marks dirty (add an
   `editor.mark_dirty()` to the handle in Task 7 if absent — prefer adding it
   there with its own test), `_capture()`, poke recalc.
9. **Expiry pill:** `panel.on_expiry(lambda e: (editor.apply_expiry(e), _capture(), poke))`.
10. `_apply_chain`: `panel.set_chain(state["chain"], state["spot"])`; expiry =
    the restored/pending expiry if the chain lists it, else the nearest;
    then pending legs / template seed as today, then `_fill_all_prices()` for
    legs without a manual price, `_auto_iv()`, poke recalc.
11. `_sync_status` loses `expiry_sel`/`chain_lbl` references; the chain line
    moves to `panel.status_lbl`.

**Step 1: Rewrite the harness tests whose subject is removed** —
`_click(root, "CALCULATE")` and `_click(root, "LOAD CHAIN")` sites. New helpers:

```python
def _recalc(root):
    """Run the debounce tick past its delay."""
    tick = _timer_named(root, "_recalc_tick")
    import time as _t
    orig = _t.monotonic
    _t.monotonic = lambda: orig() + 10
    try:
        with root:
            tick.callback()
    finally:
        _t.monotonic = orig

def _submit_symbol(root, sym):
    ticker = [el for el in _walk(root) if "entry-ticker" in el._classes][0]
    ticker.value = sym
    _fire(ticker, "keydown.enter")
```

`_calculate_spy` becomes: publish chain → `_drive` → `_recalc(root)` (asserts a
`calc_compute` was enqueued by reading the fake bus stream or by patching
`bus_client.request` to record) → publish result → `_drive` → assert "ENTRY
CREDIT". `_symbol_input` finds `entry-ticker`. The two LOAD CHAIN tests use
`_submit_symbol(root, "QQQ")` / `_submit_symbol(root, "SPY")` — note the SPY
reload goes through `should_load` dedup, so that test must use the ⟳ refresh
button (`panel.refresh_btn`) instead; assert the same texts as before. Update
`test_the_page_names_the_strategy_step_once` to the new chip copy only if the
chip is gone — the invariant ("Strategy" printed once) stays asserted.

**Add new tests:**

```python
def test_a_landed_chain_prices_every_leg_and_enqueues_one_compute(page, monkeypatch): ...
def test_clicking_a_bid_adds_a_short_leg_priced_at_the_mark(page): ...
def test_three_quick_edits_enqueue_one_compute(page, monkeypatch): ...
def test_no_load_calculate_fetch_or_iv_buttons_remain(page): ...
```

`test_options_calculator.py:1173` asserts `'layout="card"' in src` → change to
`'layout="table"'` (the subject changed; the invariant "the Calculator mounts a
declared layout" stays).

**Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:**
`cd webgui && "$PY" -m pytest tests/test_options_calculator_apply.py tests/test_options_calculator.py tests/test_calculator_stock_leg.py tests/test_options_handoff.py tests/test_handoff_legs.py tests/test_no_inline_style.py tests/test_page_state.py -q` → PASS.

**Step 5:** Commit `feat(calc): shared entry panel, auto-pricing and auto-recalculate`.

---

### Task 10: Simulator mounts the panel

**Files:** Modify `webgui/pages/options/simulator.py`; Modify
`webgui/tests/test_options_simulator.py`; Test additions there.

Changes:
1. Replace the Symbol/Load/strategy/legs columns with
   `panel = entry_panel.build_entry_panel(strategy_exclude=strategies.STOCK_STRATEGIES)`
   (navy default tokens). The **Position** tiles move under the panel in their
   own `CARD` row. Remove `fetch_btn` and `expiry_all` ("Set all legs to" is the
   expiry strip now); keep **Copy to Calculator** in `panel.legs_footer`.
2. Leg editor: `layout="table"`, `show_premium=False`,
   `delta_for=lambda leg: leg_delta(state.get("chain"), leg)` (the Simulator now
   HAS greeks — update the stale comment at line ~480 saying it has none).
3. New state `chain`, `chain_ver`; `_poll_chain` (async, `run.io_bound` read of
   `options:sim_chain`, version-gated, in-flight guard — copy the Calculator's
   `_poll_chain` shape) → `state["chain"] = payload["chain"]` only when
   `payload["symbol"]` matches `_sym()`; then `panel.set_chain(chain, spot)`.
4. `panel.on_pick` → append `leg_from_pick(..., price=None)` then
   `_on_legs_changed(); _capture(); _legs_ui()`.
5. `panel.on_expiry` → `editor.apply_expiry(e)` (fires on_change as today).
6. Symbol Enter → `_symbol_submit` unchanged; ⟳ → `_request_fetch(show_wait=True)`.
7. Strike/expiry selects no longer read `meta["strikes"]` only — keep
   `_strikes_for` on meta (the engine can only price contracts in its snapshot,
   and meta is built from that snapshot; the grid's chain is display).

**Step 1: Failing tests** in `test_options_simulator.py` (follow its existing
render harness; if none drives timers, add one modelled on
`test_options_calculator_apply.py`):

```python
def test_simulator_mounts_the_table_layout_and_the_entry_panel(): ...   # source asserts
def test_a_landed_sim_chain_paints_the_grid_for_the_current_symbol(): ...
def test_a_sim_chain_for_another_symbol_is_ignored(): ...
def test_clicking_an_ask_adds_a_long_leg_and_enqueues_a_run(monkeypatch): ...
```

Update the existing `layout="card"` assertion near line 246 the same way as the
Calculator's.

**Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:**
`cd webgui && "$PY" -m pytest tests/test_options_simulator.py tests/test_sim_view.py tests/test_handoff_legs.py tests/test_no_inline_style.py tests/test_strategy_menu_gate.py -q` → PASS.

**Step 5:** Commit `feat(sim): shared entry panel with the chain grid`.

---

### Task 11: Remove the dead card layout

**Precondition:** `grep -rn 'layout="card"' webgui/pages` returns nothing.
Rescue uses `layout="row"` — confirm before deleting.

**Files:** `webgui/pages/options/leg_editor.py` (`_card_body`,
`_CARD_ROW*`, `_CARD_MAX_W`, `"card"` in `_LAYOUTS`); `webgui/tests/test_leg_editor.py`
(the `test_card_*` tests — deleted with their subject; token tests stay if
the table layout still reads those tokens).

Keep `card_tokens`/`DEFAULT_CARD_TOKENS` (the table layout uses them) — rename
is out of scope. Run the full webgui suite. Commit
`refactor(legs): remove the unused card layout`.

---

## Phase 5 — Verify, docs

### Task 12: Runtime verification (local page harness)

No dev stack exists. Render both pages locally with a fake bus + real
`options_svc.handlers` + a synthetic chain (see memory
`local-page-harness-replaces-missing-dev`), in the Browser pane:

1. Enter `SPY` → grid paints around spot; legs priced; results appear with no
   button press.
2. Click a put Bid → a SELL leg appears priced at the mark; strategy reads Custom.
3. Strike `›` and ↑ step one strike; price refills; a typed price survives a
   strike step; ↺ restores the mark.
4. Column picker: add Gamma → column appears; reload → persists.
5. Expiry pill → all legs move; grid repaints.
6. Simulator: same flow; What-if/IV-shock repaint after a grid click.
7. Screenshots of both pages (or DOM reads if screenshots time out).

Inject `* { transition:none!important }` before measuring anything.

### Task 13: Docs

- `webgui/page_help.py` — Calculator and Simulator "simple version" sections:
  drop LOAD CHAIN / FETCH PREMIUMS / CALCULATE / IV UPDATE; describe Enter,
  grid clicks (Bid sells, Ask buys, priced at mark), strike stepping, Pricing
  assumptions, auto IV (overwritten on each load). Run `tests/test_page_help.py`.
- `docs/manuals/user-guide/user-guide.md` §Calculator (line ~792) and
  §Simulator (~892); `docs/manuals/reference-guide/reference-guide.md` matching
  sections. Rebuild: `"$PY" docs/manuals/build_docs.py` (check its usage first).
- `docs/webgui-routes.md` — `/options/calculator` and `/options/simulator`
  entries; add `cache:options:sim_chain`.
- `docs/CHANGELOG.md` — dated entry: what shipped, measured chain size, the
  removed buttons, test result SET comparison.
- `CLAUDE.md` — route-table rows for the two pages, the `leg_editor` paragraph
  (layouts are now `row` + `table`), and the `thin_calc_chain` "five fields"
  claim in the Performance section. Correct in place.

Commit `docs: calculator and simulator entry panel`.

### Task 14: Final comparison

Re-run the three Task 0 commands to `*-after.txt`; diff the FAILED and SKIPPED
node-ID sets. Any new failure is a stop. Report results to the operator; **do
not promote** without their go-ahead (whole-stack restart; trading-day window
15:25–16:15 CT).
