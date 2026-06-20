# DTE-Aware Look-Back (Replay + Expected Move) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the **Replay** look-back (the path that gets re-priced) and the
**Expected Move** trailing history (context behind the forward cone) adapt to the
selected option's **DTE**, with an optional manual override dropdown on both tabs.

**Locked design (from user):**
- **EM trailing history ≈ 3× DTE** trading days (short DTE → intraday candles).
- **Controls:** automatic by DTE **+ a manual override dropdown** on both tabs.
- **Replay tiers:** 0-DTE → 1-min/1d · ≤5 → 5-min/3d · ≤15 → 5-min/5d · >15 →
  daily/~½×DTE.

**Architecture:** Pure spec functions in Tier-2 compute map `(dte, override) →
{resolution, window}`; defensive fetch helpers turn a spec into a price Series
(Replay) / OHLC candles (EM) via `_proxy.schwab_client.get_intraday_history` /
`get_daily_history` (flexible minute/days + months params, return DataFrames).
The `sim_replay` and `expected_move` commands gain an optional `lookback` arg;
the pages add a "Look-back" dropdown that re-enqueues on change. **Replay no
longer reuses the snapshot's fixed 2-day history** — it fetches a DTE-appropriate
window itself (the expiry/DTE is known at replay time, not at `sim_fetch`).

**Tech Stack:** Python 3.11, pandas, NiceGUI, `shared.bus`, pytest. Spec
functions + fetch wiring are unit-tested.

---

## Look-back tables

**Replay** (`replay_lookback_spec(dte, override)`):

| Auto DTE | freq | window | key (override) |
|----------|------|--------|----------------|
| 0 | 1-min | 1 day | `1m_1d` |
| ≤5 | 5-min | 3 days | `5m_3d` |
| ≤15 | 5-min | 5 days | `5m_5d` |
| >15 | daily | ceil(DTE/2) trading days | `1d_20d` (fixed 20) |
| — | 15-min | 10 days | `15m_10d` |

Override menu: `auto, 1m_1d, 5m_3d, 5m_5d, 15m_10d, 1d_20d`.

**Expected Move** (`em_lookback_spec(dte, override)`), trailing = ~3× DTE:

| Auto DTE | mode | window |
|----------|------|--------|
| ≤2 | intraday 30-min | 3 days |
| >2 | daily | clamp(3×DTE, 20, 252) bars |

Override menu: `auto, 1mo (21), 3mo (63), 6mo (130), 1y (252)`.

---

## Task 1: `replay_lookback_spec` pure function (Tier 2)

**Files:** Modify `services/options_svc/compute.py`; Test
`services/options_svc/tests/test_compute.py`.

**Step 1 — failing test:**

```python
def test_replay_lookback_spec_auto_tiers():
    assert compute.replay_lookback_spec(0)["minutes"] == 1
    assert compute.replay_lookback_spec(0)["days"] == 1
    assert compute.replay_lookback_spec(3)["minutes"] == 5
    assert compute.replay_lookback_spec(3)["days"] == 3
    assert compute.replay_lookback_spec(15)["days"] == 5
    big = compute.replay_lookback_spec(30)
    assert big["freq_type"] == "daily" and big["bars"] == 15

def test_replay_lookback_spec_override_keys():
    assert compute.replay_lookback_spec(99, "1m_1d")["minutes"] == 1
    assert compute.replay_lookback_spec(0, "15m_10d")["minutes"] == 15
    assert compute.replay_lookback_spec(0, "1d_20d")["freq_type"] == "daily"
    # Unknown override falls back to auto.
    assert compute.replay_lookback_spec(0, "bogus") == compute.replay_lookback_spec(0)
```

**Step 2 — run, expect FAIL** (`AttributeError`).

**Step 3 — implement** in `compute.py` (near `sim_replay`):

```python
import math as _math

_REPLAY_OVERRIDES = {
    "1m_1d":  {"freq_type": "minute", "minutes": 1,  "days": 1,  "label": "1-min · 1d"},
    "5m_3d":  {"freq_type": "minute", "minutes": 5,  "days": 3,  "label": "5-min · 3d"},
    "5m_5d":  {"freq_type": "minute", "minutes": 5,  "days": 5,  "label": "5-min · 5d"},
    "15m_10d":{"freq_type": "minute", "minutes": 15, "days": 10, "label": "15-min · 10d"},
    "1d_20d": {"freq_type": "daily",  "months": 1,   "bars": 20, "label": "daily · 20d"},
}

def replay_lookback_spec(dte, override="auto") -> dict:
    """Map (dte, override) -> a price-history fetch spec for the Replay path.
    ``override`` 'auto' (or unknown) uses the DTE tiers; any other key selects a
    fixed window. Always returns a dict with ``freq_type`` ('minute'|'daily')
    plus the params that fetch needs."""
    if override and override != "auto" and override in _REPLAY_OVERRIDES:
        return dict(_REPLAY_OVERRIDES[override])
    try:
        dte = int(dte)
    except (TypeError, ValueError):
        dte = 15
    if dte <= 0:
        return {"freq_type": "minute", "minutes": 1, "days": 1, "label": "1-min · 1d"}
    if dte <= 5:
        return {"freq_type": "minute", "minutes": 5, "days": 3, "label": "5-min · 3d"}
    if dte <= 15:
        return {"freq_type": "minute", "minutes": 5, "days": 5, "label": "5-min · 5d"}
    bars = _math.ceil(dte / 2)
    months = max(1, _math.ceil(bars / 21))
    return {"freq_type": "daily", "months": months, "bars": bars,
            "label": f"daily · {bars}d"}
```

**Step 4 — run, expect PASS. Step 5 — commit** (`feat(options_svc): replay_lookback_spec DTE tiers`).

---

## Task 2: `_fetch_replay_history` + DTE-aware `sim_replay` (Tier 2)

**Files:** Modify `compute.py` (`sim_replay`); Test `test_compute.py`.

**Step 1 — failing test** (monkeypatch `_proxy.schwab_client` with a fake that
returns a DataFrame; assert `sim_replay` uses the DTE-fetched history, not the
snapshot's):

```python
def test_sim_replay_fetches_dte_history(monkeypatch):
    import pandas as pd, datetime as dt
    snap = _real_replay_snapshot("SPY")        # expiry 2026-06-26
    compute._SIM_SNAPSHOTS.clear(); compute._SIM_SNAPSHOTS["SPY"] = snap
    calls = {}
    class _SC:
        def get_intraday_history(self, symbol, minutes, days):
            calls["intraday"] = (symbol, minutes, days)
            idx = pd.date_range("2026-06-18 09:30", periods=4, freq="5min")
            return pd.DataFrame({"datetime": idx, "open":[1,2,3,4], "high":[1,2,3,4],
                                 "low":[1,2,3,4], "close":[450.0,451,452,453]})
        def get_daily_history(self, symbol, months): return None
    monkeypatch.setattr(compute._proxy, "schwab_client", _SC())
    out = compute.sim_replay("SPY", "2026-06-26", "call", 450.0, "buy")
    # 2026-06-26 is >15 DTE from 2026-06-18? It's 8 days -> 5-min/5d tier.
    assert calls["intraday"][1] == 5            # minutes
    assert out["prices"] == [450.0, 451.0, 452.0, 453.0]
    assert out["lookback"]["label"]             # spec label surfaced
```

**Step 2 — run, expect FAIL.**

**Step 3 — implement:** add a fetch helper + thread a `lookback` param through
`sim_replay`. Compute DTE from `expiry` vs today; call `replay_lookback_spec`;
fetch via `_proxy.schwab_client`; build the Series; pass it where `hist` was. Add
`"lookback": {"label": ..., "key": override or "auto"}` to the returned dict. Add
a module cache `{(symbol, label): Series}` so strike/dir changes (same DTE) don't
refetch.

```python
def _fetch_replay_history(symbol, spec):
    import pandas as pd
    sc = _proxy.schwab_client
    try:
        if spec["freq_type"] == "minute":
            df = sc.get_intraday_history(symbol, minutes=spec["minutes"], days=spec["days"])
        else:
            df = sc.get_daily_history(symbol, months=spec["months"])
        if df is None or len(df) == 0:
            return pd.Series(dtype=float)
        s = pd.Series(df["close"].values, index=pd.to_datetime(df["datetime"]))
        if spec.get("bars"):
            s = s.iloc[-spec["bars"]:]
        return s
    except Exception:
        return pd.Series(dtype=float)
```

Signature becomes `sim_replay(symbol, expiry, kind, strike, direction, lookback="auto")`.
Replace `hist = snap.price_history` with the DTE-fetched series; if empty →
`{"error": "Replay unavailable - no price history"}`.

**Step 4 — run, expect PASS** (plus the existing `sim_replay` tests still green —
update `_real_replay_snapshot` users to monkeypatch `schwab_client`, or have the
helper inject the fake). **Step 5 — commit** (`feat(options_svc): DTE-aware Replay history fetch`).

---

## Task 3: `sim_replay` command passes `lookback` (Tier 2 handler)

**Files:** Modify `handlers.py` (`sim_replay` branch); Test `test_handlers.py`.

Add `a.get("lookback", "auto")` to the `compute.sim_replay(...)` call. Test:
enqueue with `args={..., "lookback": "5m_3d"}` and assert the monkeypatched
compute received it. **Commit** (`feat(options_svc): sim_replay lookback arg`).

---

## Task 4: Replay look-back dropdown (Tier 1 page)

**Files:** Modify `webgui/pages/options/simulator.py`; Test `test_options_simulator.py`.

- Add a module list `REPLAY_LOOKBACKS = [("auto","Auto"), ("1m_1d","1-min · 1d"),
  ("5m_3d","5-min · 3d"), ("5m_5d","5-min · 5d"), ("15m_10d","15-min · 10d"),
  ("1d_20d","Daily · 20d")]` and a pure helper `lookback_options()` returning the
  `{key: label}` dict (unit-tested).
- In the Replay tab row add `lookback_sel = ui.select(lookback_options(),
  value="auto", label="Look-back")`.
- `_enqueue_replay()` includes `"lookback": lookback_sel.value`.
- `lookback_sel.on_value_change(lambda e: _enqueue_replay())`.
- `_render_replay()` shows the active spec label from `state["replay"]["lookback"]`
  in the cursor/readout line.
- The import-guard test (`test_simulator_module_imports_no_engine_or_proxy`) stays
  green. **Commit** (`feat(webgui): Replay look-back dropdown`).

---

## Task 5: `em_lookback_spec` + DTE-aware `compute_expected_move` (Tier 2)

**Files:** Modify `compute.py`; Test `test_compute.py` / `test_expected_move.py`.

**Step 1 — failing test** for the pure spec:

```python
def test_em_lookback_spec_auto():
    assert compute.em_lookback_spec(1)["mode"] == "intraday"
    d = compute.em_lookback_spec(15)
    assert d["mode"] == "daily" and d["bars"] == 45          # 3×15
    assert compute.em_lookback_spec(2)["mode"] == "intraday"
    assert compute.em_lookback_spec(3)["bars"] == 20          # clamp floor
    assert compute.em_lookback_spec(200)["bars"] == 252       # clamp cap

def test_em_lookback_spec_override():
    assert compute.em_lookback_spec(15, "6mo")["bars"] == 130
    assert compute.em_lookback_spec(15, "bogus") == compute.em_lookback_spec(15)
```

**Step 3 — implement** `em_lookback_spec(dte, override="auto")` (overrides:
`1mo`=21, `3mo`=63, `6mo`=130, `1y`=252 — all daily; auto ≤2 → intraday 30-min/3d,
else daily clamp(3×dte,20,252)). Then thread it into `compute_expected_move`:
add a `lookback="auto"` param; replace the fixed `get_price_history_every_day` +
`[-_EM_HISTORY_BARS:]` slice with a spec-driven fetch (intraday for short DTE via
`get_intraday_history`, else daily via `get_daily_history(months)` sliced to
`bars`); surface `base["lookback"] = {"label":…, "key":…}`. DTE is already
computed (`dte = (exp_date - today).days`) — compute the spec **after** parsing
the expiry, fetch candles per spec. Keep all defensive guards. **Commit**
(`feat(options_svc): DTE-aware Expected Move history`).

---

## Task 6: `expected_move` command + page dropdown (Tier 2 handler + Tier 1)

**Files:** Modify `handlers.py` (`expected_move` branch) + `webgui/pages/options/expected_move.py`;
Tests `test_expected_move.py` + `webgui/tests/test_expected_move.py`.

- Handler passes `a.get("lookback", "auto")` to `compute_expected_move`.
- Page adds an `EM_LOOKBACKS` list + `ui.select` "Look-back" (Auto / 1mo / 3mo /
  6mo / 1y); the command enqueue includes `lookback`; re-enqueue on change; show
  the active spec label. Keep handoff (Scanner/Paper/etc.) defaulting to `auto`.
- **Commit** (`feat(webgui): Expected Move look-back dropdown`).

---

## Task 7: Live verification

Restart `options_svc` (loads new compute/handlers), start the `webgui` preview.
- Replay: pick a 15-DTE-ish contract → confirm it fetches 5-min/5d (verify via the
  `cache:options:sim_replay` `lookback.label` + bar count/resolution). Switch the
  dropdown to "Daily · 20d" → confirm the trace changes. Read the cache from Redis
  for proof.
- Expected Move: open for a ~15-DTE expiry → confirm ~45 daily candles (not 130);
  switch to "6mo" → ~130. Read `cache:options:expected_move` `lookback` + candle
  count.
Screenshots optional (the heavy multi-panel page may time out the raster tool —
DOM/Redis proof is acceptable, as established).

---

## Task 8: Docs

Update CLAUDE.md: the Simulator + Expected Move route rows and the dedicated
notes to mention DTE-aware look-back + the override dropdown. Update test counts.
**Commit** (`docs(CLAUDE): DTE-aware look-back for Replay + Expected Move`).

---

## Gotchas

- **Run service suites per folder** (never `pytest services`).
- Replay now does a **proxy fetch per replay compute** — cache by `(symbol, spec
  label)` so same-DTE strike/dir changes don't refetch.
- `get_intraday_history` minute/period must be Schwab-valid (minute ∈ {1,5,10,15,30},
  periodType=day period ≤ 10) — the tiers above stay inside those bounds.
- EM cone math (`em_cone`) is unchanged; only the candle window/resolution changes.
- Existing `sim_replay`/`expected_move` tests must be updated to monkeypatch
  `schwab_client` (Replay) since history is now fetched, not taken from the snapshot.
