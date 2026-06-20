# Simulator Replay Tab Migration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate the third Simulator tab — **Replay** — from the legacy Tk
`options_simulator` window into the 3-tier NiceGUI webgui, so the page shows
Replay / What-if / IV-shock like the source desktop app.

**Architecture:** Follow the existing simulator 3-tier split. The in-process
`ChainSnapshot` (already fetched + stashed by `sim_fetch` in
`services/options_svc/compute.py`, and which **already carries
`price_history`**) is re-priced along the underlying's recent path by the
existing pure `ReplayEngine`. A new Tier-2 `sim_replay` compute path turns that
trace + the gap-compression/session layout (ported from the Tk window) into a
JSON-safe dict cached at `cache:options:sim_replay`; a new `sim_replay` command
publishes it. The Tier-1 page gains a third **Replay** tab that version-polls
that view, renders a single stacked multi-yAxis Highcharts chart (price + 5
Greeks), and drives a client-side scrub-cursor plotLine from a slider.

**Decisions (locked):**
- **Separate command + cache view** (`sim_replay` / `cache:options:sim_replay`),
  NOT folded into `sim_run` — replay depends only on the contract selector, not
  the dt/mult sliders, so it must not recompute on every debounced slider drag.
- **Faithful 6-panel stack** (price + Delta/Gamma/Theta/Vega/Rho) as ONE
  Highcharts element with stacked yAxes sharing an integer x-axis.
- **Integer (gap-compressed) x-axis**, dates in axis labels/tooltip — sidesteps
  the documented datetime-crosshair epoch-ms gotcha.

**Tech Stack:** Python 3.11, NiceGUI + `nicegui-highcharts` (`ui.highchart`),
pandas/numpy, `shared.bus` Redis backbone, pytest. Pure builders are unit-tested;
engine calls stay in Tier 2.

---

## Reference: what the source does

- Tabs built at `D:\Trading With Schwab\options-scanner\options_simulator\window.py:131-136`
  (`Replay`, `What-if`, `IV shock`).
- `ReplayEngine.full_trace(contract)` —
  `D:\Trading With Schwab\options-scanner\options_simulator\engine.py:117-144` —
  re-prices the contract at each `price_history` bar (`bs_greeks` per bar, `T`
  from each bar's timestamp to expiry 15:00). Returns a DataFrame indexed by the
  history timestamps with `theo_price` + 5 Greeks.
- Render + gap-compression + session boundaries + scrub cursor —
  `window.py:368-560`.
- The migrated copy of the engine in THIS repo is
  `options-scanner/options_simulator/engine.py` (same `ReplayEngine`,
  `aggregate_position`, `Position`, `ChainSnapshot`). `sim_fetch` already stashes
  the full snapshot (with `price_history`) in `compute._SIM_SNAPSHOTS`.

---

## Task 1: `compute.sim_replay` — JSON-safe replay trace (Tier 2)

**Files:**
- Modify: `services/options_svc/compute.py` (add `sim_replay` after `sim_run`, ~line 1138)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Write the failing test**

Add to `test_compute.py`. Build a tiny in-process snapshot using the real engine
classes and stash it like `sim_fetch` would, then call `sim_replay`.

```python
def test_sim_replay_builds_jsonsafe_trace(monkeypatch):
    import sys, pathlib, datetime as dt
    from repo_paths import OPTIONS_SCANNER
    sys.path.insert(0, str(OPTIONS_SCANNER))
    from options_simulator import engine as seng
    import pandas as pd

    # 3 bars across one session, plus a contract.
    idx = pd.to_datetime(["2026-06-18 09:30", "2026-06-18 09:31", "2026-06-18 09:32"])
    hist = pd.Series([450.0, 451.0, 452.0], index=idx)
    contract = seng.ContractRow(strike=450.0, kind="call", bid=1.0, ask=1.2,
                                mid=1.1, iv=0.20, expiry=dt.date(2026, 6, 26))
    snap = seng.ChainSnapshot(spot=452.0, as_of=dt.datetime(2026, 6, 18, 9, 32),
                              r=0.04, symbol="SPY", contracts=[contract],
                              price_history=hist)
    compute._SIM_SNAPSHOTS["SPY"] = snap

    out = compute.sim_replay("SPY", "2026-06-26", "call", 450.0, "buy")
    assert out["spot"] == 452.0
    assert out["timestamps"] == ["2026-06-18T09:30:00", "2026-06-18T09:31:00",
                                 "2026-06-18T09:32:00"]
    assert out["prices"] == [450.0, 451.0, 452.0]
    assert set(out["greeks"]) == {"delta", "gamma", "theta", "vega", "rho"}
    assert len(out["greeks"]["delta"]) == 3
    assert out["x"] == [0, 1, 2]
    assert out["gaps"] == []                       # single session, no overnight gap
    assert len(out["sessions"]) == 1
    assert out["sessions"][0]["date"] == "2026-06-18"
    # JSON-serializable end to end
    import json; json.dumps(out)


def test_sim_replay_missing_snapshot_returns_empty():
    compute._SIM_SNAPSHOTS.pop("NOPE", None)
    assert compute.sim_replay("NOPE", "2026-06-26", "call", 1.0, "buy") == {}


def test_sim_replay_zero_iv_degrades(monkeypatch):
    import sys, datetime as dt
    from repo_paths import OPTIONS_SCANNER
    sys.path.insert(0, str(OPTIONS_SCANNER))
    from options_simulator import engine as seng
    import pandas as pd
    contract = seng.ContractRow(strike=450.0, kind="call", bid=1, ask=1, mid=1,
                                iv=0.0, expiry=dt.date(2026, 6, 26))
    snap = seng.ChainSnapshot(spot=452.0, as_of=dt.datetime(2026, 6, 18, 9, 32),
                              r=0.04, symbol="ZIV", contracts=[contract],
                              price_history=pd.Series([450.0], index=pd.to_datetime(["2026-06-18 09:30"])))
    compute._SIM_SNAPSHOTS["ZIV"] = snap
    out = compute.sim_replay("ZIV", "2026-06-26", "call", 450.0, "buy")
    assert out.get("error")
```

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k sim_replay -v`
Expected: FAIL with `AttributeError: module 'compute' has no attribute 'sim_replay'`.

**Step 3: Write the implementation**

Add after `sim_run` (`compute.py:1138`). Port the gap/session logic from
`window.py:402-462` but keep it JSON-safe (integer x, ISO timestamps, lists).

```python
def sim_replay(symbol, expiry, kind, strike, direction) -> dict:
    """Re-price the selected contract along the underlying's recent price path.

    Ports the Tk Replay tab: ``ReplayEngine.full_trace`` over the snapshot's
    ``price_history``, plus the gap-compression / session-boundary layout the
    page needs to draw a clean integer x-axis (overnight/weekend gaps collapsed).
    Returns a JSON-safe dict; ``{}`` if the snapshot/contract is missing (page
    prompts a re-fetch/selection), or ``{"error": ...}`` if IV is unavailable
    (mirrors the Tk "IV unavailable - cannot simulate" guard). Replay depends
    only on the contract selector — NOT the dt/mult sliders — so it is its own
    command/cache view, separate from ``sim_run``."""
    from options_simulator import engine as seng
    import numpy as np

    snap = _SIM_SNAPSHOTS.get(symbol)
    if snap is None:
        return {}
    contract = find_contract(snap, expiry, kind, strike)
    if contract is None:
        return {}
    if contract.iv <= 0:
        return {"error": "IV unavailable - cannot simulate"}

    pos = seng.Position.single(contract, direction, snap.symbol)
    trace = seng.aggregate_position(pos, lambda c: seng.ReplayEngine(snap).full_trace(c))
    hist = snap.price_history
    if trace is None or trace.empty or hist.empty:
        return {"error": "Replay unavailable - no price history"}

    # Gap-compress overnight/weekend breaks onto an integer x-axis (window.py).
    if len(hist) >= 2:
        deltas = (hist.index[1:] - hist.index[:-1]).total_seconds()
        median_delta_s = float(np.median(deltas))
        gap_threshold_s = max(median_delta_s * 3, 60 * 60)
        gap_indices = [i + 1 for i, d in enumerate(deltas) if d > gap_threshold_s]
    else:
        median_delta_s = 0.0
        gap_indices = []

    sessions = []
    starts = [0] + gap_indices
    ends = gap_indices + [len(hist)]
    for s, e in zip(starts, ends):
        if e > s:
            sessions.append({"start": s, "end": e,
                             "date": hist.index[s].strftime("%Y-%m-%d")})

    sessions_n = len(gap_indices) + 1 if len(hist) else 0
    if len(hist) >= 2:
        if median_delta_s < 120:
            resolution = f"{len(hist)} bars, 1-min × {sessions_n} sessions"
        elif median_delta_s < 3600:
            resolution = (f"{len(hist)} bars, {int(round(median_delta_s/60))}-min "
                          f"× {sessions_n} sessions")
        else:
            span_days = (hist.index[-1] - hist.index[0]).days or 1
            resolution = f"{len(hist)} bars, ~{span_days}d daily"
    else:
        resolution = f"{len(hist)} bar"

    # Up to 8 HH:MM ticks across the integer axis.
    if len(hist) >= 4:
        tick_pos = np.linspace(0, len(hist) - 1, min(8, len(hist))).astype(int)
        ticks = {"pos": [int(i) for i in tick_pos],
                 "labels": [hist.index[i].strftime("%H:%M") for i in tick_pos]}
    else:
        ticks = {"pos": list(range(len(hist))),
                 "labels": [hist.index[i].strftime("%H:%M") for i in range(len(hist))]}

    def _f(seq):
        return [float(v) for v in seq]

    return {
        "spot": snap.spot,
        "timestamps": [ts.isoformat() for ts in hist.index],
        "x": list(range(len(hist))),
        "prices": _f(hist.values),
        "greeks": {g: _f(trace[g].values) for g in ("delta", "gamma", "theta", "vega", "rho")},
        "gaps": [int(i) for i in gap_indices],
        "sessions": sessions,
        "ticks": ticks,
        "resolution": resolution,
    }
```

**Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k sim_replay -v`
Expected: 3 PASS.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(options_svc): sim_replay compute path for Simulator Replay tab"
```

---

## Task 2: `sim_replay` command + cache view (Tier 2 handler)

**Files:**
- Modify: `services/options_svc/handlers.py:55-71` (constants) and the dispatch
  chain near `:345` (after the `sim_run` branch)
- Test: `services/options_svc/tests/test_handlers.py` (mirror an existing
  `sim_run`/`sim_fetch` handler test; if none, add a focused one)

**Step 1: Write the failing test**

Find the existing sim handler test pattern first
(`grep -n "sim_run\|sim_fetch" services/options_svc/tests/test_handlers.py`) and
mirror it. Shape:

```python
def test_handle_sim_replay_caches_and_publishes(monkeypatch):
    monkeypatch.setattr(compute, "sim_replay", lambda *a: {"spot": 1.0, "x": [0]})
    bus = _FakeBus()   # reuse the suite's fake/raw bus fixture
    cmd = Command(type="sim_replay",
                  args={"symbol": "SPY", "expiry": "2026-06-26",
                        "kind": "call", "strike": 450.0, "direction": "buy"})
    handlers.handle_command(cmd, bus)
    assert bus.get(handlers.CACHE_SIM_REPLAY)["spot"] == 1.0
    assert bus.published(handlers.EVENT_SIM_REPLAY)
```

(Adapt to the suite's actual bus test helper — match the `sim_run` test.)

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -k sim_replay -v`
Expected: FAIL (`AttributeError: ... CACHE_SIM_REPLAY` / unknown command no-op).

**Step 3: Implement**

In `handlers.py`, beside the sim_meta/sim_result constants (`:55-59`):

```python
CACHE_SIM_REPLAY = "cache:options:sim_replay"
EVENT_SIM_REPLAY = "events:options:sim_replay"
```

In the dispatch chain, after the `sim_run` branch (`:345`):

```python
    elif command.type == "sim_replay":
        a = command.args or {}
        res = compute.sim_replay(
            a.get("symbol"), a.get("expiry"), a.get("kind"),
            a.get("strike"), a.get("direction"))
        version = bus.cache_set(CACHE_SIM_REPLAY, res)
        bus.publish(EVENT_SIM_REPLAY, {"version": version})
```

**Step 4: Run to verify pass**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -k sim_replay -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(options_svc): sim_replay command + cache:options:sim_replay view"
```

---

## Task 3: `replay_figure` pure builder (Tier 1 page)

**Files:**
- Modify: `webgui/pages/options/simulator.py` (add builder near `ivshock_figure`, ~line 106)
- Test: `webgui/tests/test_options_simulator.py`

**Step 1: Write the failing test**

```python
def test_replay_figure_stacks_price_and_greeks():
    trace = {
        "spot": 452.0,
        "timestamps": ["2026-06-18T09:30:00", "2026-06-18T09:31:00"],
        "x": [0, 1],
        "prices": [450.0, 451.0],
        "greeks": {"delta": [0.5, 0.55], "gamma": [0.01, 0.01],
                   "theta": [-0.1, -0.1], "vega": [0.2, 0.2], "rho": [0.05, 0.05]},
        "gaps": [], "sessions": [{"start": 0, "end": 2, "date": "2026-06-18"}],
        "ticks": {"pos": [0, 1], "labels": ["09:30", "09:31"]},
        "resolution": "2 bars, 1-min × 1 sessions",
    }
    fig = sim.replay_figure(trace, cursor=1)
    # 6 stacked series: price + 5 greeks
    assert len(fig["series"]) == 6
    # 6 stacked yAxes
    assert len(fig["yAxis"]) == 6
    # cursor plotLine present on the (integer) xAxis
    assert any(pl["value"] == 1 for pl in fig["xAxis"]["plotLines"])


def test_replay_figure_empty_trace_is_safe():
    fig = sim.replay_figure({}, cursor=0)
    assert fig["series"] == [] or all(s["data"] == [] for s in fig["series"])
```

**Step 2: Run to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_options_simulator.py -k replay -v`
Expected: FAIL (`AttributeError: ... replay_figure`).

**Step 3: Implement**

In `simulator.py`, add a builder. Single chart, 6 stacked yAxes sharing the
integer x-axis; session boundaries as xAxis plotLines; the scrub cursor as one
more xAxis plotLine. Put the date in the tooltip (the `sessions`/`ticks` give
labels) — do NOT use a datetime axis.

```python
_GREEK_PANELS = ["delta", "gamma", "theta", "vega", "rho"]
CURSOR_COLOR = "#ef5350"

def replay_figure(trace, cursor=None):
    """Stacked price + 5-Greek replay chart over an integer (gap-compressed) x.

    ``cursor`` (int x-index) draws a client-side vertical plotLine across the
    panels. Returns an empty-but-valid chart when ``trace`` is missing."""
    x = (trace or {}).get("x") or []
    prices = (trace or {}).get("prices") or []
    greeks = (trace or {}).get("greeks") or {}
    sessions = (trace or {}).get("sessions") or []
    ticks = (trace or {}).get("ticks") or {"pos": [], "labels": []}

    panels = ["price"] + _GREEK_PANELS
    n = len(panels)
    gap = 2                       # % gap between panels
    h = (100 - gap * (n - 1)) / n
    yaxes, series = [], []
    titles = ["Price", "Delta", "Gamma", "Theta", "Vega", "Rho"]
    for i, (panel, title) in enumerate(zip(panels, titles)):
        top = i * (h + gap)
        yaxes.append({**_DARK_AXIS, "title": {"text": title, "style": {"color": "#bdbdbd"}},
                      "top": f"{top}%", "height": f"{h}%", "offset": 0,
                      "lineWidth": 1})
        data = list(zip(x, prices)) if panel == "price" else list(zip(x, greeks.get(panel) or []))
        series.append({"name": title, "type": "line", "yAxis": i, "data": data,
                       "marker": {"enabled": False},
                       "color": "#66bb6a" if panel == "price" else "#42a5f5"})

    # Session boundaries (dashed) + the scrub cursor.
    xplotlines = [_plotline(s["start"] - 0.5, "#777777", dash="Dot", width=1)
                  for s in sessions[1:]]
    if cursor is not None:
        xplotlines.append(_plotline(cursor, CURSOR_COLOR, width=1))

    return {
        "chart": {"height": 560, "backgroundColor": "transparent"},
        "title": {"text": f"Replay — {(trace or {}).get('resolution', '')}",
                  "style": {"color": "#e6e6e6"}},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {**_DARK_AXIS, "tickPositions": ticks["pos"],
                  "labels": {"style": {"color": "#bdbdbd"},
                             "formatter": None},
                  "plotLines": xplotlines},
        "yAxis": yaxes,
        "tooltip": {"shared": True},
        "series": series,
    }
```

> Note: the integer xAxis tick LABELS should show HH:MM from `ticks["labels"]`.
> Highcharts maps `tickPositions`→default numeric labels; to show HH:MM, pass a
> `categories`-free formatter via NiceGUI's `:`-prefixed dynamic property at the
> element level in `render()` (Task 4), OR accept numeric x-labels in v1 and put
> time in the tooltip. Keep the pure builder numeric; wire the formatter (if
> wanted) on the element. Tests assert structure only.

**Step 4: Run to verify pass**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_options_simulator.py -k replay -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/simulator.py webgui/tests/test_options_simulator.py
git commit -m "feat(webgui): replay_figure stacked price+Greeks builder"
```

---

## Task 4: Wire the Replay tab into `render()` (Tier 1 page)

**Files:**
- Modify: `webgui/pages/options/simulator.py` `render()` (`:109-303`)
- Test: existing `webgui/tests/test_options_simulator.py` import/empty-cache
  guards (`test_simulator_module_imports_no_engine_or_proxy`, `test_render_*`)
  must still pass.

**Step 1 (state + tab):** Add to `state` (`:114`): `"replay": None`,
`"replay_ver": None`. Make Replay the first tab (source order) and default:

```python
with ui.tabs() as tabs:
    tab_replay = ui.tab("Replay")
    tab_whatif = ui.tab("What-if")
    tab_ivshock = ui.tab("IV shock")
with ui.tab_panels(tabs, value=tab_replay).classes("w-full"):
    with ui.tab_panel(tab_replay):
        with ui.row().classes("items-center gap-4 w-full"):
            scrub_lbl = ui.label("Cursor —")
            scrub_slider = ui.slider(min=0, max=1, value=0).classes("w-96")
        replay_empty = ui.label("Select a contract to run the replay.").classes("opacity-70")
        replay_chart = ui.highchart(replay_figure({}, None)).classes("w-full")
    with ui.tab_panel(tab_whatif):
        ...                       # unchanged
```

**Step 2 (render fn):** Add `_render_replay()` — paints from `state["replay"]`,
positions the scrub cursor (client-side, like ΔS), updates the readout label:

```python
def _render_replay():
    tr = state["replay"]
    if not tr or tr.get("error") or not tr.get("x"):
        replay_empty.text = (tr or {}).get("error") or "Select a contract to run the replay."
        replay_empty.set_visibility(True)
        replay_chart.set_visibility(False)
        return
    replay_empty.set_visibility(False)
    replay_chart.set_visibility(True)
    n = len(tr["x"])
    scrub_slider.max = max(n - 1, 1)
    cur = int(min(max(scrub_slider.value, 0), n - 1))
    ts = tr["timestamps"][cur] if cur < len(tr["timestamps"]) else ""
    scrub_lbl.text = f"Cursor {ts}"
    replay_chart.options = replay_figure(tr, cursor=cur)
    replay_chart.update()
```

**Step 3 (enqueue + scrub wiring):** Replay enqueues only on selector changes
(not dt/mult). Add:

```python
@guard
def _enqueue_replay():
    if not state["meta"] or strike_sel.value is None:
        return
    bus_client.request("options", {"type": "sim_replay", "args": {
        "symbol": (state["meta"].get("symbol") or symbol_in.value or "").upper(),
        "expiry": expiry_sel.value, "kind": kind_tog.value,
        "strike": strike_sel.value, "direction": dir_tog.value}})
```

Call `_enqueue_replay()` from `_on_selector()`, `strike_sel`/`dir_tog`
on-change, and the `_apply_meta` first-sweep kickoff (alongside `_enqueue_run`).
The scrub slider is client-side only:

```python
scrub_slider.on_value_change(lambda e: _render_replay())
```

**Step 4 (poll + initial paint):** Mirror `_poll_result`:

```python
@guard
def _poll_replay():
    version = bus_client.read_version("options:sim_replay")
    if version == state["replay_ver"]:
        return
    state["replay_ver"] = version
    state["replay"] = bus_client.read("options:sim_replay") or None
    _render_replay()
```

Add initial cold reads (mirror `:296-298`) and `ui.timer(2.0, _poll_replay)`.

**Step 5: Run the page guard tests + full webgui suite**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_options_simulator.py -v`
then `..\.venv\Scripts\python -m pytest -q`
Expected: all green (322 + the new replay tests).

**Step 6: Commit**

```bash
git add webgui/pages/options/simulator.py
git commit -m "feat(webgui): wire Replay tab into Simulator page (poll + scrub)"
```

---

## Task 5: Live verification

**Steps:**
1. Ensure Memurai + proxy + `options_svc` + webgui are up (or start the
   `webgui` preview on :8500 and `options_svc`).
2. Open `/options/simulator`, Fetch snapshot for `SPY`, select a contract.
3. Confirm the **Replay** tab shows the price + 5 Greek panels, session
   boundary lines, and that the **scrub slider** moves the red cursor and updates
   the timestamp readout.
4. Screenshot via the Claude Preview tool and share as proof.
5. Off-hours caveat: if `price_history` is sparse (weekend), the trace may be a
   single session or daily fallback — that's expected (per CLAUDE.md), not a bug.

**No commit** (verification only).

---

## Task 6: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the `/options/simulator` Routes row + the "remaining
  follow-ups" line that says "Replay TODO")

Change the Simulator route description from "What-if + IV-shock; Replay TODO" to
note all three tabs are built; drop the "Simulator **Replay** tab" follow-up.

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): Simulator Replay tab migrated (all three tabs built)"
```

---

## Notes / gotchas (carry into execution)

- **Run service suites per folder** (never `pytest services`) — multi-app
  `sys.path` collides `config`/`scoring`/`src` (CLAUDE.md).
- **ESM import-map gotcha:** the replay chart MUST be built once at render (it
  is, in Task 4 Step 1) — a dynamically-added `ui.highchart` on a page with no
  chart at first render fails to resolve `nicegui-highcharts`.
- **No datetime x-axis** for replay — integer/gap-compressed axis with dates in
  tooltip/labels avoids the epoch-ms crosshair-label bug.
- **`extras`:** line series need none. Do NOT pass `extras=["highcharts-more"]`.
- Replay is **independent of dt/mult** — keep it off the debounced `sim_run`
  path so slider drags stay cheap.
