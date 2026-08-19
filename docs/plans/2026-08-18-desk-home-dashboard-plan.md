# Desk Home Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build `/desk`, a single-screen command centre aggregating the highest glance-value element of each page, and make it the app's landing route.

**Architecture:** A Tier-1 NiceGUI reader page (`webgui/pages/desk.py`) over eight existing `cache:*` views, plus one Tier-2 enrichment that adds six keys to `cache:options:matrix` rows so per-symbol walls, net GEX, ATM IV and the dealer setup tag exist for the first time. All display logic lives in pure module-level functions unit-tested without a browser; `render()` is widgets and wiring only.

**Tech Stack:** Python 3.11, NiceGUI (Tailwind-first, no `.style()`), Redis/Memurai via `shared.bus`, SQLite (`gex_history.db`), pytest.

**Design doc:** [`2026-08-18-desk-home-dashboard-design.md`](2026-08-18-desk-home-dashboard-design.md) — read it first.

---

## Before you start

**Environment.** You are in a git worktree. There is **no `.venv` here** — the venv is at the repo root. Every command below uses the absolute interpreter:

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
```

**Confine `cd` to a subshell** — `(cd webgui && "$PY" -m pytest . -q)` — so the shell's persistent cwd stays at the worktree root.

**Baselines to compare against** (record the failing *set*, never the count — this repo has a documented incident where two real regressions hid behind two tests flipping to skipped while the total held steady):

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q -rf)
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q -rf
```

webgui should be **1826 passed**. options_svc had **0 failures** as of 2026-08-15, but `test_expected_move` carries date-relative tests that fail depending on the run date, and `test_flow_alert_window.py::test_gth_signal_still_fires_at_the_open` is known-flaky. Write down what you actually see before touching anything.

**Commit after every task.** Do not batch.

---

# Phase 1 — Tier-2: enrich `cache:options:matrix` rows

The Desk cannot be built first: its flagship panel needs six row keys that do not exist yet. All six come from data the matrix build already has in hand or can fetch from the same open connection.

---

### Task 1: A `LIMIT 1` grid loader (do NOT reuse `load_date_with_grid`)

`load_date_with_grid` returns and **decodes every grid for the whole session**. Calling it per symbol per minute for ~45 symbols would reintroduce the exact hotspot CLAUDE.md records as *"the service's largest CPU burn"* — `gamma_snapshot` re-decoding the whole session's grids every minute, which was fixed by making it incremental. We need one row: the newest.

**Files:**
- Modify: `options-scanner/gex_history_db.py` (add after `load_date_with_grid`, ~line 465)
- Test: `options-scanner/tests/test_gex_history_db.py`

**Step 1: Write the failing test**

```python
def test_latest_grid_row_returns_only_the_newest_row(tmp_path, monkeypatch):
    conn = _memory_db()                      # reuse this module's existing helper
    gh.insert_snapshot(conn, symbol="SPY", view="gex", ts=1000,
                       spot=100.0, flip=99.0, net_total=5.0,
                       gex_grid={"100": {"call": 1.0, "put": -1.0, "net": 0.0}})
    gh.insert_snapshot(conn, symbol="SPY", view="gex", ts=2000,
                       spot=101.0, flip=99.5, net_total=7.0,
                       gex_grid={"101": {"call": 2.0, "put": -1.0, "net": 1.0}})
    row = gh.latest_grid_row(conn, "SPY", "gex")
    assert row is not None
    ts, spot, net_total, grid = row
    assert ts == 2000 and spot == 101.0 and net_total == 7.0
    assert "101" in grid


def test_latest_grid_row_is_none_when_symbol_has_no_rows():
    conn = _memory_db()
    assert gh.latest_grid_row(conn, "NOPE", "gex") is None
```

Match the existing fixtures in that file — check how other tests build a connection and call `insert_snapshot`, and adapt the kwargs to its real signature (`sed -n '354,398p' options-scanner/gex_history_db.py`).

**Step 2: Run it and watch it fail**

```bash
(cd options-scanner && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_gex_history_db.py -q -p no:randomly -k latest_grid_row)
```
Expected: `AttributeError: module 'gex_history_db' has no attribute 'latest_grid_row'`.

**Step 3: Implement**

```python
def latest_grid_row(
    conn: sqlite3.Connection,
    symbol: str,
    view: str = "gex",
    date=None,
) -> tuple | None:
    """The NEWEST (ts, spot, net_total, grid) for one symbol/view on ``date``.

    Deliberately NOT ``load_date_with_grid(...)[-1]``: that loads and DECODES
    every grid for the session, which is the hotspot the incremental
    ``gamma_snapshot`` memo exists to avoid. The matrix build calls this once per
    symbol per minute for ~45 symbols, so it must decode exactly one grid.

    Returns None when the symbol has no rows for the date. Sargable on the PK via
    the same ``ts >= ? AND ts < ?`` range every other reader here uses.
    """
    start, end = _local_unix_range(date)
    cur = conn.execute(
        """
        SELECT ts, spot, net_total, gex_json
          FROM snapshots
         WHERE symbol = ? AND view = ?
           AND ts >= ? AND ts < ?
         ORDER BY ts DESC
         LIMIT 1
        """,
        (symbol, view, start, end),
    )
    row = cur.fetchone()
    if row is None:
        return None
    ts, spot, net_total, raw = row
    return (ts, spot, net_total, _decode_grid(raw))
```

**Step 4: Verify it passes**

```bash
(cd options-scanner && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_gex_history_db.py -q -p no:randomly)
```
Expected: PASS. (This suite has 11 documented baseline failures — none in this file; confirm your set is unchanged.)

**Step 5: Commit**

```bash
git add options-scanner/gex_history_db.py options-scanner/tests/test_gex_history_db.py
git commit -m "feat(gex-history): add latest_grid_row, a one-row grid loader"
```

---

### Task 2: `matrix._wall_dist_pct` and `matrix._latest_atm_iv`

Two small pure helpers `dealer_regime` needs. Both fail toward *"can't tell"*, never toward the most extreme reading — the generalisation of this repo's NaN-clamps-to-the-high-bound trap.

**Files:**
- Modify: `services/options_svc/matrix.py`
- Test: `services/options_svc/tests/test_matrix.py`

**Step 1: Write the failing tests**

```python
# ---- wall distance ----
def test_wall_dist_pct_picks_the_NEAREST_wall():
    # call wall 2% up, put wall 5% down -> 2.0
    assert m._wall_dist_pct(100.0, call_wall=102.0, put_wall=95.0) == 2.0

def test_wall_dist_pct_is_none_when_no_wall_is_known():
    # None, NOT 0.0 — 0.0 means "spot is exactly ON the wall", the maximally
    # pin-like value, so a missing wall would fabricate the strongest signal.
    assert m._wall_dist_pct(100.0, None, None) is None

def test_wall_dist_pct_tolerates_one_missing_side():
    assert m._wall_dist_pct(100.0, call_wall=None, put_wall=97.0) == 3.0

def test_wall_dist_pct_is_none_without_a_usable_spot():
    assert m._wall_dist_pct(None, 102.0, 98.0) is None
    assert m._wall_dist_pct(0.0, 102.0, 98.0) is None

# ---- latest ATM IV ----
def test_latest_atm_iv_takes_the_last_non_null_sample():
    assert m._latest_atm_iv([(1, 12.0), (2, 13.5), (3, None)]) == 13.5

def test_latest_atm_iv_is_none_when_every_sample_is_null():
    # atm_iv is forward-only: legacy rows come back as (ts, None).
    assert m._latest_atm_iv([(1, None), (2, None)]) is None
    assert m._latest_atm_iv([]) is None
    assert m._latest_atm_iv(None) is None
```

**Step 2: Run and watch fail**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_matrix.py -q -k "wall_dist or latest_atm_iv"
```
Expected: `AttributeError: module ... has no attribute '_wall_dist_pct'`.

**Step 3: Implement** — add above `dealer_regime` in `services/options_svc/matrix.py`:

```python
def _wall_dist_pct(spot, call_wall, put_wall):
    """|spot - NEAREST wall| / spot * 100, or None when it cannot be known.

    Feeds ``dealer_regime``'s ``delta_wall_pin`` gate. Returns None — never 0.0 —
    when no wall is available: 0.0 reads as "spot is exactly ON the wall", the
    maximally pin-like value, so degrading to it would fabricate the strongest
    possible signal out of missing data. Never raises.
    """
    try:
        if not spot or spot <= 0:
            return None
        dists = [abs(spot - w) for w in (call_wall, put_wall)
                 if w is not None and w > 0]
        if not dists:
            return None
        return round(min(dists) / spot * 100.0, 3)
    except Exception:
        return None


def _latest_atm_iv(iv_series):
    """Last non-null positive ATM-IV level from [(ts, atm_iv)], else None.

    ``atm_iv`` is forward-only, so legacy/early rows come back as (ts, None).
    Never raises.
    """
    try:
        for _ts, iv in reversed(list(iv_series or ())):
            if iv is not None and iv > 0:
                return round(float(iv), 2)
    except Exception:
        pass
    return None
```

**Step 4: Verify**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_matrix.py -q
```
Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/matrix.py services/options_svc/tests/test_matrix.py
git commit -m "feat(matrix): add wall-distance and latest-ATM-IV helpers"
```

---

### Task 3: `build_rows` emits the six new keys

**Files:**
- Modify: `services/options_svc/matrix.py:320-420` (`build_rows`, both the happy path and the degraded branch)
- Test: `services/options_svc/tests/test_matrix.py`

**Step 1: Write the failing tests**

```python
def _blob(**over):
    """A build_rows raw-blob with the new enrichment fields, overridable."""
    blob = {
        "series": [(0, 100.0, 10, 5, 1_000_000.0, 400_000.0),
                   (900, 101.0, 30, 8, 3_000_000.0, 800_000.0)],
        "flip": 100.0,
        "call_wall": 103.0,
        "put_wall": 97.0,
        "net_gex": 1_420_000_000.0,
        "iv_series": [(0, 12.0), (900, 13.5)],
    }
    blob.update(over)
    return blob


def test_build_rows_emits_walls_net_gex_and_iv():
    rows = m.build_rows({"SPY": _blob()}, {}, {}, now_ts=900)
    r = rows[0]
    assert r["call_wall"] == 103.0
    assert r["put_wall"] == 97.0
    assert r["net_gex"] == 1_420_000_000.0
    assert r["atm_iv"] == 13.5
    assert r["iv_state"] in ("spiking", "collapsing", "stable", "na")


def test_build_rows_new_keys_are_none_not_zero_when_absent():
    # The off-hours case depends on this distinction: index OI reads 0 after
    # hours, so an all-zero grid yields ARBITRARY walls. None means "withhold";
    # 0.0 would render as a confident wall at strike zero.
    rows = m.build_rows({"SPY": {"series": [], "flip": None}}, {}, {}, now_ts=0)
    r = rows[0]
    assert r["call_wall"] is None and r["put_wall"] is None
    assert r["net_gex"] is None and r["atm_iv"] is None
    assert r["iv_state"] == "na"
    assert r["dealer_regime"] == "na"


def test_build_rows_degraded_row_carries_the_new_keys():
    # A row that raises mid-construction still needs every key, or the Desk's
    # column read blows up on a KeyError for one bad symbol.
    rows = m.build_rows({"BAD": {"series": "not-a-list", "flip": None}},
                        {}, {}, now_ts=0)
    r = rows[0]
    for k in ("call_wall", "put_wall", "net_gex", "atm_iv",
              "iv_state", "dealer_regime"):
        assert k in r, f"degraded row missing {k}"


def test_build_rows_dealer_regime_fires_gamma_cascade_below_flip_on_spiking_iv():
    # The label that only became reachable once atm_iv turned out to be emitted.
    rows = m.build_rows(
        {"SPY": _blob(flip=110.0,                     # spot 101 -> BELOW flip
                      iv_series=[(0, 10.0), (900, 14.0)])},   # +40% -> spiking
        {}, {}, now_ts=900)
    assert rows[0]["gex_regime"] == "below"
    assert rows[0]["dealer_regime"] == "gamma_cascade"


def test_build_rows_dealer_regime_fires_vanna_squeeze_above_flip_on_collapsing_iv():
    rows = m.build_rows(
        {"SPY": _blob(flip=95.0,                      # spot 101 -> ABOVE flip
                      iv_series=[(0, 20.0), (900, 12.0)])},   # -40% -> collapsing
        {}, {}, now_ts=900)
    assert rows[0]["dealer_regime"] == "vanna_squeeze"
```

Check `_IV_SPIKE` / `_IV_CRUSH` at the top of `matrix.py` and pick series that clear them; adjust the numbers above if the thresholds differ.

**Step 2: Run and watch fail**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_matrix.py -q -k "walls_net_gex or new_keys or degraded_row_carries or gamma_cascade or vanna_squeeze"
```
Expected: `KeyError: 'call_wall'`.

**Step 3: Implement**

Change the signature to thread in the one time-of-day input (keeping the function pure — same reasoning as the existing `eth_symbols` note):

```python
def build_rows(raw, scan_counts, alert_counts, now_ts, eth_symbols=None,
               mins_to_close=None):
```

Extend the docstring's `raw` description:

```
    raw = {symbol: {"series": [...], "flip": float|None,
                    "call_wall": float|None, "put_wall": float|None,
                    "net_gex": float|None, "iv_series": [(ts, atm_iv)]}}
```

In the happy path, after `sig, strength = composite_signal(...)`:

```python
            call_wall = blob.get("call_wall")
            put_wall = blob.get("put_wall")
            net_gex = blob.get("net_gex")
            iv_series = blob.get("iv_series") or []
            iv_state, _iv_rel = iv_regime(iv_series, now_ts)
            atm_iv = _latest_atm_iv(iv_series)
            wall_dist = _wall_dist_pct(spot, call_wall, put_wall)
```

and add to the emitted row dict:

```python
                # Dealer-structure columns (2026-08-18). The Desk's structure map
                # needs walls for EVERY symbol; cache:options:gamma holds only one
                # at a time and is mutated by whichever Gamma page is open, so
                # reading it from a second page is a race.
                "call_wall": round(call_wall, 2) if call_wall is not None else None,
                "put_wall": round(put_wall, 2) if put_wall is not None else None,
                "net_gex": net_gex,
                "atm_iv": atm_iv,
                "iv_state": iv_state,
                "dealer_regime": dealer_regime(spot, flip, iv_state, t_state,
                                               mins_to_close, wall_dist),
```

In the **degraded** branch add:

```python
                "call_wall": None,
                "put_wall": None,
                "net_gex": None,
                "atm_iv": None,
                "iv_state": "na",
                "dealer_regime": "na",
```

**Step 4: Verify**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_matrix.py -q
```
Expected: PASS, no regressions.

**Step 5: Commit**

```bash
git add services/options_svc/matrix.py services/options_svc/tests/test_matrix.py
git commit -m "feat(matrix): emit walls, net GEX, ATM IV and the dealer setup tag"
```

---

### Task 4: `build_matrix` feeds the new fields

**Files:**
- Modify: `services/options_svc/compute.py:2795-2820`
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Write the failing test** — a fake `gh` module asserting the loop wires each loader into the blob:

```python
def test_build_matrix_threads_walls_and_iv_into_the_row_blobs(monkeypatch):
    seen = {}

    class _FakeGH:
        def connect(self, read_only=False):
            class _C:
                def close(self_inner): pass
            return _C()
        def load_flow_series(self, conn, sym, d):
            return [(0, 100.0, 1, 1, 10.0, 5.0)]
        def latest_flip(self, conn, sym, view, d):
            return 99.0
        def load_atm_iv_series(self, conn, sym, d):
            return [(0, 12.0)]
        def latest_grid_row(self, conn, sym, view="gex", date=None):
            return (0, 100.0, 4.2e9, {"101": {"call": 1.0, "put": 0.0, "net": 1.0}})

    monkeypatch.setattr(c, "_matrix_gh", lambda: _FakeGH())
    monkeypatch.setattr(c, "_matrix_symbols", lambda: ["SPY"])

    real_build_rows = c_mx.build_rows
    def _spy(raw, *a, **kw):
        seen.update(raw)
        return real_build_rows(raw, *a, **kw)
    monkeypatch.setattr(c_mx, "build_rows", _spy)

    c.build_matrix(scan_day={}, flow_cooldowns={}, today="2026-08-18",
                   session_date="2026-08-18", now_ts=0)

    blob = seen["SPY"]
    assert blob["net_gex"] == 4.2e9
    assert blob["iv_series"] == [(0, 12.0)]
    assert "call_wall" in blob and "put_wall" in blob
```

Adapt the monkeypatch targets to the real helper names in `compute.py` (`_matrix_gh`, and whatever supplies the symbol list — check `sed -n '2761,2800p' services/options_svc/compute.py`).

**Step 2: Run and watch fail.**

**Step 3: Implement.** Add a module-level helper near `gamma_walls`:

```python
def _matrix_dealer_levels(gh, conn, symbol, session_date):
    """{'call_wall','put_wall','net_gex'} for one symbol from its NEWEST grid.

    Uses ``latest_grid_row`` (one row, one decode) rather than
    ``load_date_with_grid`` — the latter decodes every grid for the session, and
    running that per symbol per minute is the hotspot the incremental
    gamma_snapshot memo exists to avoid.

    Every value degrades to None, never 0.0: index OI reads 0 after hours, so an
    all-zero grid produces ARBITRARY walls, and the Desk withholds a wall it
    cannot trust rather than printing a confident wrong one. Never raises.
    """
    blank = {"call_wall": None, "put_wall": None, "net_gex": None}
    try:
        row = gh.latest_grid_row(conn, symbol, "gex", session_date)
        if not row:
            return blank
        _ts, spot, net_total, grid = row
        walls = gamma_walls("GEX", grid, spot) or []
        put_wall = walls[0] if len(walls) >= 1 else None
        call_wall = walls[1] if len(walls) >= 2 else None   # NB: put is FIRST
        return {"call_wall": call_wall, "put_wall": put_wall,
                "net_gex": net_total}
    except Exception:
        log.debug("matrix dealer levels failed for %s", symbol, exc_info=True)
        return blank
```

Then in the per-symbol loop:

```python
            try:
                series = gh.load_flow_series(conn, sym, session_date)
                flip = gh.latest_flip(conn, sym, "gex", session_date)
                iv_series = gh.load_atm_iv_series(conn, sym, session_date)
                levels = _matrix_dealer_levels(gh, conn, sym, session_date)
                raw[sym] = {"series": series, "flip": flip,
                            "iv_series": iv_series, **levels}
            except Exception:
                log.debug("build_matrix: read failed for %s", sym, exc_info=True)
                raw[sym] = {"series": [], "flip": None, "iv_series": [],
                            "call_wall": None, "put_wall": None, "net_gex": None}
```

And pass the time-of-day input to `build_rows`:

```python
        rows = mx.build_rows(raw, scan_counts, alert_counts, now_ts,
                             eth_symbols=_matrix_eth_symbols(),
                             mins_to_close=_mins_to_cash_close(now_ts))
```

`_mins_to_cash_close` should come from `shared/market_calendar.py` — **do not add a new window constant or holiday literal**; that module is the single source of truth. If no such helper exists, add it there, not here.

**Step 4: Verify**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q -rf
```
Expected: the same failing set you recorded at the start.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(matrix): load dealer levels and ATM IV into the row blobs"
```

---

### Task 5: Correct the stale `iv_regime` docstring

It claims ATM IV is *"the axis the app does NOT yet emit"*. That was true when written; the column landed 2026-08-13 and is now 100% populated. The claim was believed twice during this design — once by a codebase search, once by the first draft — and cost a real column each time.

**Files:** Modify `services/options_svc/matrix.py` (the `iv_regime` docstring).

**Step 1:** No test — this is prose. Replace the paragraph beginning *"This is the axis the app does NOT yet emit"* with:

```
    Fed from the ``atm_iv`` snapshots column, which the poll writes every cycle
    (``gex_collector`` -> ``iv_analysis.extract_atm_iv``) and
    ``gex_history_db.load_atm_iv_series`` reads back. The column is forward-only,
    so rows written before it existed come back as (ts, None) and are skipped.
    Live since 2026-08-13; measured 100% populated across 92 symbols on
    2026-08-18. (This docstring previously said the column did not exist — it was
    written before the column landed, and that staleness twice cost a column that
    was actually available. Verify against the DB, not the prose.)
```

**Step 2: Commit**

```bash
git add services/options_svc/matrix.py
git commit -m "docs(matrix): correct the stale iv_regime docstring — atm_iv is emitted"
```

---

# Phase 2 — Desk pure builders

All of Phase 2 is `webgui/pages/desk.py` module-level functions plus `webgui/tests/test_desk.py`. **No NiceGUI imports are exercised by these tests** — they take dicts and return dicts. This mirrors `pages/market.py`, whose entire display logic is pure and browser-free.

Run throughout:

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_desk.py -q)
```

---

### Task 6: `structure_positions` — the structure-map geometry

**Step 1: Write the failing test**

```python
import pages.desk as d

def test_structure_positions_places_spot_between_the_walls():
    p = d.structure_positions(spot=100.0, flip=99.0,
                              put_wall=95.0, call_wall=105.0)
    assert 0.0 <= p["put_wall"] < p["spot"] < p["call_wall"] <= 100.0
    assert p["spot"] == 50.0          # midway between 95 and 105

def test_structure_positions_is_none_without_both_walls():
    assert d.structure_positions(100.0, 99.0, None, 105.0) is None
    assert d.structure_positions(100.0, 99.0, 95.0, None) is None
    assert d.structure_positions(None, 99.0, 95.0, 105.0) is None

def test_structure_positions_clamps_spot_outside_the_walls():
    # Spot can break a wall; the marker must stay inside the bar.
    p = d.structure_positions(spot=110.0, flip=99.0,
                              put_wall=95.0, call_wall=105.0)
    assert p["spot"] == 100.0

def test_structure_positions_survives_a_degenerate_span():
    # Equal walls would divide by zero.
    assert d.structure_positions(100.0, 100.0, 100.0, 100.0) is None

def test_structure_positions_omits_flip_when_absent_but_keeps_the_bar():
    p = d.structure_positions(100.0, None, 95.0, 105.0)
    assert p is not None and p["flip"] is None
```

**Step 2: Run, watch fail** (`ModuleNotFoundError: pages.desk`).

**Step 3: Implement** — create `webgui/pages/desk.py` with a module docstring and:

```python
def structure_positions(spot, flip, put_wall, call_wall):
    """Percentage positions along the structure bar, or None if undrawable.

    Returns {"put_wall": 0.0, "call_wall": 100.0, "spot": pct, "flip": pct|None}
    with the walls pinned to the ends, since the bar's whole job is to show where
    price sits BETWEEN them.

    Percentages, not a viewBox: the caller applies them as ``left-[{pct}%]``
    Tailwind arbitrary values. Drawing this as a scaled SVG would need
    ``vector-effect: non-scaling-stroke`` to stop the non-uniform scale smearing
    the strokes, and DOMPurify strips that attribute — leaving strokes thick
    horizontally and hairline vertically while the server-side string stays
    perfectly correct, which is invisible to every test. Never raises.
    """
    try:
        if spot is None or put_wall is None or call_wall is None:
            return None
        lo, hi = float(put_wall), float(call_wall)
        if hi <= lo:
            return None
        span = hi - lo

        def _pct(v):
            if v is None:
                return None
            return round(min(100.0, max(0.0, (float(v) - lo) / span * 100.0)), 2)

        return {"put_wall": 0.0, "call_wall": 100.0,
                "spot": _pct(spot), "flip": _pct(flip)}
    except Exception:
        return None
```

**Step 4: Verify PASS. Step 5: Commit**

```bash
git add webgui/pages/desk.py webgui/tests/test_desk.py
git commit -m "feat(desk): add structure-map geometry"
```

---

### Task 7: `dealer_rows` — including off-hours wall suppression

The single most important behaviour in the page. Index OI reads 0 after hours, so `$SPX`/`$NDX` produce all-zero grids and arbitrary walls.

**Step 1: Write the failing test**

```python
DESK_SYMBOLS = ("$SPX", "SPY", "QQQ", "$NDX")

def _mrow(sym, **over):
    r = {"symbol": sym, "spot": 100.0, "day_pct": 0.5, "flip": 99.0,
         "call_wall": 105.0, "put_wall": 95.0, "net_gex": 1.42e9,
         "gex_regime": "above", "atm_iv": 13.4, "iv_state": "stable",
         "dealer_regime": "charm_grind", "hotness": 40}
    r.update(over)
    return r

def test_dealer_rows_selects_and_orders_the_desk_symbols():
    view = {"rows": [_mrow("QQQ"), _mrow("AAPL"), _mrow("$SPX")]}
    rows = d.dealer_rows(view, stale=False)
    assert [r["symbol"] for r in rows] == ["$SPX", "QQQ"]   # DESK order, AAPL dropped

def test_dealer_rows_regime_word_comes_only_from_gex_regime():
    above = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="above",
                                          net_gex=-5.0)]}, stale=False)[0]
    # net_gex disagrees with the regime; the WORD must still follow gex_regime
    # alone. Two sources for one label is how /sentiment/sectors and
    # /sentiment/rotation came to print opposite verdicts.
    assert above["regime_word"] == "LONG GAMMA · PINS"
    below = d.dealer_rows({"rows": [_mrow("$SPX", gex_regime="below")]},
                          stale=False)[0]
    assert below["regime_word"] == "SHORT GAMMA · RUNS"

def test_dealer_rows_withhold_walls_when_stale():
    rows = d.dealer_rows({"rows": [_mrow("$SPX")]}, stale=True)
    assert rows[0]["call_wall"] is None and rows[0]["put_wall"] is None
    assert rows[0]["structure"] is None
    assert rows[0]["stale"] is True

def test_dealer_rows_withhold_walls_on_the_all_zero_grid_signature():
    # After hours index OI reads 0 -> an all-zero grid -> arbitrary walls.
    rows = d.dealer_rows({"rows": [_mrow("$SPX", net_gex=0.0)]}, stale=False)
    assert rows[0]["call_wall"] is None and rows[0]["structure"] is None

def test_dealer_rows_is_empty_for_a_missing_view():
    assert d.dealer_rows(None, stale=False) == []
    assert d.dealer_rows({}, stale=False) == []

def test_dealer_rows_tolerates_a_row_missing_every_new_key():
    rows = d.dealer_rows({"rows": [{"symbol": "$SPX"}]}, stale=False)
    assert rows[0]["symbol"] == "$SPX"
    assert rows[0]["structure"] is None
```

**Step 3: Implement** with `DESK_SYMBOLS`, a `_REGIME_WORD = {"above": "LONG GAMMA · PINS", "below": "SHORT GAMMA · RUNS", "na": "—"}` map, and a `flip_distance` field (`spot - flip`, plus an `above`/`below` word). Walls are set to `None` when `stale` or when `net_gex` is falsy-but-present. Comment the `net_gex == 0` suppression with the OI reason.

**Step 5: Commit** `feat(desk): add dealer rows with off-hours wall suppression`

---

### Task 8: `opportunity_rows`

**Step 1: Write the failing test**

```python
def test_opportunity_rows_takes_the_top_five_by_hotness():
    view = {"rows": [_mrow(f"S{i}", hotness=i) for i in range(10)]}
    rows = d.opportunity_rows(view)
    assert len(rows) == 5
    assert [r["hotness"] for r in rows] == [9, 8, 7, 6, 5]

def test_opportunity_rows_carries_iv_level_and_direction():
    rows = d.opportunity_rows({"rows": [_mrow("NVDA", atm_iv=41.2,
                                              iv_state="spiking")]})
    assert rows[0]["atm_iv"] == 41.2
    assert rows[0]["iv_state"] == "spiking"

def test_opportunity_rows_composes_a_rationale_from_real_state():
    rows = d.opportunity_rows({"rows": [_mrow("NVDA", dealer_regime="vanna_squeeze",
                                              trend_state="strong_up",
                                              call_accel="hot")]})
    assert rows[0]["rationale"]                       # non-empty
    assert "vanna" in rows[0]["rationale"].lower() or \
           "squeeze" in rows[0]["rationale"].lower()

def test_opportunity_rows_has_no_iv_rv_edge_columns():
    # RV does not exist anywhere, so neither does edge. Asserted so a future
    # change cannot quietly reintroduce a column with nothing behind it.
    r = d.opportunity_rows({"rows": [_mrow("NVDA")]})[0]
    assert "rv" not in r and "edge" not in r

def test_opportunity_rows_is_empty_for_a_missing_view():
    assert d.opportunity_rows(None) == []
```

Include a `SETUP_LABEL` map turning `dealer_regime` keys into display words (`gamma_cascade` → `CASCADE`, `vanna_squeeze` → `VOL CRUSH`, `delta_wall_pin` → `PIN`, `charm_grind` → `GRIND`, `neutral`/`na` → `""`).

**Step 5: Commit** `feat(desk): add the opportunity board rows`

---

### Task 9: `flow_rows`

Thin — it delegates to the existing `flow.alert_rows`, which already reverses to newest-first. **Delegate, do not reimplement**; that is the design's load-bearing principle.

```python
def test_flow_rows_delegates_and_takes_the_newest_five():
    view = {"date": "2026-08-18",
            "alerts": [{"type": "uoa", "side": "call", "symbol": f"S{i}",
                        "ts": 1000 + i, "id": f"i{i}", "text": "x",
                        "premium": 1e6} for i in range(9)]}
    rows = d.flow_rows(view)
    assert len(rows) == 5
    assert rows[0]["symbol"] == "S8"          # newest first

def test_flow_rows_never_claims_a_buy_or_sell_side():
    # Schwab gives no time-and-sales tape; alert_text's docstring is explicit.
    rows = d.flow_rows({"alerts": [{"type": "uoa", "side": "call",
                                    "symbol": "SPY", "ts": 1, "id": "a",
                                    "text": "x"}]})
    blob = " ".join(str(v) for v in rows[0].values()).lower()
    assert "buy" not in blob and "sell" not in blob

def test_flow_rows_is_empty_for_a_missing_view():
    assert d.flow_rows(None) == []
```

**Commit** `feat(desk): add the flow alert rows`

---

### Task 10: `position_rows` and `positions_summary`

Merges paper + driver **account** views (the ledger has no mark; the account does).

```python
def _pos(**over):
    p = {"position_id": 1, "symbol": "SPY", "strategy": "PCS",
         "short_strike": 670.0, "long_strike": 665.0, "expiration": "2026-08-21",
         "quantity": 12, "entry_credit": 3.15, "current_value": 4.02,
         "unrealized_pnl": 1046.0, "status": "OPEN",
         "rescue_state": "ok", "heat": 12.0}
    p.update(over); return p

def test_position_rows_merges_paper_and_driver_with_a_source_chip():
    rows = d.position_rows({"positions": [_pos(symbol="SPY")]},
                           {"positions": [_pos(symbol="NVDA")]})
    assert {r["source"] for r in rows} == {"PAPER", "CLAUDE"}

def test_position_rows_maps_rescue_state_to_a_flag():
    states = ["ok", "watch", "tested", "critical"]
    rows = d.position_rows({"positions": [_pos(position_id=i, rescue_state=s)
                                          for i, s in enumerate(states)]}, None)
    assert [r["flag"] for r in rows] == ["OK", "WATCH", "AT RISK", "RESCUE"]

def test_position_rows_excludes_closed_positions():
    rows = d.position_rows({"positions": [_pos(status="CLOSED")]}, None)
    assert rows == []

def test_positions_summary_counts_open_unrealized_and_at_risk():
    rows = d.position_rows(
        {"positions": [_pos(position_id=1, unrealized_pnl=1046.0),
                       _pos(position_id=2, unrealized_pnl=-697.0,
                            rescue_state="tested"),
                       _pos(position_id=3, unrealized_pnl=0.0,
                            rescue_state="critical")]}, None)
    s = d.positions_summary(rows)
    assert s["open"] == 3
    assert s["unrealized"] == 349.0
    assert s["at_risk"] == 2            # tested + critical, NOT watch

def test_positions_summary_of_nothing_is_zeroed_not_none():
    assert d.positions_summary([])["open"] == 0
```

Also emit a derived `dte` from `expiration` (reuse `paper._dte_from_expiration` rather than rewriting it).

**Commit** `feat(desk): add merged position rows and their summary`

---

### Task 11: `freshness_facts` — the honest "streaming" indicator

The mockup's green STREAMING dot always says live. Ours reports the truth, and it is what gates Task 7's `stale` flag.

```python
def test_freshness_facts_reports_live_from_gex_status():
    f = d.freshness_facts({"status_label": "Collecting", "status_color": "#0f0",
                           "age_seconds": 45, "session": "regular"})
    assert f["stale"] is False and f["label"]

def test_freshness_facts_is_stale_when_the_snapshot_is_old():
    f = d.freshness_facts({"status_label": "Idle", "age_seconds": 3600,
                           "session": "closed"})
    assert f["stale"] is True

def test_freshness_facts_with_no_probe_is_unknown_not_live():
    # Never a confident "live" on absent data — the same rule the drawer's
    # status card follows.
    f = d.freshness_facts(None)
    assert f["stale"] is True
    assert "unknown" in f["label"].lower()
```

**Commit** `feat(desk): add the freshness indicator`

---

### Task 12: The anti-contradiction test

The design's first decision, pinned. This is the test that stops the Desk drifting from the page it links to.

```python
def test_desk_regime_word_matches_console_regime_for_the_same_payload():
    import pages.console_regime as cr
    payload = {"label": "Rallying", "committed_label": "trending",
               "direction": 1, "direction_strong": True, "unclear": False,
               "memberships": {"mean_reversion": .1, "trending": .6,
                               "breakout": .1, "choppy": .1, "crisis": .1},
               "raw": {"mean_reversion": .1, "trending": .6, "breakout": .1,
                       "choppy": .1, "crisis": .1},
               "confidence": 0.8, "evidence": [], "evidence_detail": [],
               "transition": None, "ts": "", "as_of": "", "version_info": {}}
    assert d.regime_display(payload) == cr.regime_display(payload)

def test_desk_regime_word_is_unclear_when_the_sample_is_unclear():
    assert "unclear" in d.regime_display({"unclear": True,
                                          "label": "Rallying"}).lower()
```

Find `console_regime`'s real label accessor first (`grep -n "def .*regime\|REGIME_LABELS" webgui/pages/console_regime.py`) and have `desk.regime_display` **call it** rather than duplicate it. If it isn't exposed as a pure function, extract one — that refactor is the point of the task.

**Commit** `feat(desk): derive the regime word from console_regime, with a drift guard`

---

# Phase 3 — the page

### Task 13: `render()` skeleton, top strip, and the batched poll

**Files:** Modify `webgui/pages/desk.py`.

Follow `pages/market.py` exactly for the shape: `state = {"versions": {}, ...}`, a `_build`/`_paint` split, `read_full` for the first paint, then a 2 s `@guard_async` poll.

```python
VIEWS = ("options:header", "sentiment:regime", "sentiment:composite",
         "sentiment:history", "options:gex_status", "options:matrix",
         "options:flow_alerts", "options:paper_account",
         "options:driver_paper_account")

    @guard_async
    async def _poll():
        vers = await run.io_bound(bus_client.read_versions, list(VIEWS))
        changed = [v for v in VIEWS if vers.get(v) not in (None, state["versions"].get(v))]
        if not changed:
            return
        payloads = {}
        for v in changed:
            payloads[v] = await run.io_bound(bus_client.read, v)
            state["versions"][v] = vers.get(v)
        _paint(payloads)
```

One `read_versions` per tick — **not one per view.** Cheap `:ver` probes in a single pipelined round-trip; payloads deserialize only for views that moved. This page is open all day, so this is the difference between negligible and constant load.

Top strip: clock · VIX + `vix_regime` · regime word from `regime_display` · both rings via `rings.ring_svg(sentiment_arcs(live, snaps), uid="desk-sent")` and `ring_svg(trend_arcs(derived), uid="desk-trend")` — **distinct `uid`s are mandatory**, they namespace the SVG root id and two rings on one page will collide otherwise. **No SPX/QQQ quotes** (design decision 2).

Styling: `from pages.options.theme import CONSOLE_CARD, CON_TXT, ...`. Tailwind only — no `.style()`.

**Verify:** `(cd webgui && "$PY" -m pytest tests/test_desk.py -q)` still passes; nothing here is unit-tested beyond the builders.

**Commit** `feat(desk): render the page shell, top strip and batched poll`

---

### Task 14-17: the four panels

One task each, same shape: a `_build_<panel>(container, rows)` that clears and rebuilds, called from `_paint`. Each renders its own placeholder when its view is `None` — "Waiting for the options service…", "no open positions", "no alerts today". **Never a blank box, never a fabricated zero.**

Task 14 (Dealer) additionally renders the structure bar: a relative track div with absolutely-positioned children at `left-[{pct}%]`, one per `structure_positions` key, and renders nothing when `structure` is `None`. Task 17 (Positions) renders the summary line from `positions_summary`.

Commit after each: `feat(desk): render the dealer positioning panel`, `… opportunity board`, `… flow alerts`, `… positions`.

---

### Task 18: Click-through

Each row gets `.on("click", ...)` → `ui.navigate.to(...)`. The Dealer row first stashes its symbol via the **existing** `handoff.send_to_gamma` (one-shot by design — a symbol left in the stash would silently hijack the Gamma dropdown on its next build), then navigates to `/options/gamma`.

**Commit** `feat(desk): wire row click-through to the owning pages`

---

# Phase 4 — shell integration

### Task 19: The caption-less leading rail block

**Files:** Modify `webgui/main.py`; test `webgui/tests/test_shell.py`.

**Step 1: Write the failing tests**

```python
def test_desk_is_the_first_rail_entry_above_every_caption():
    caption, entries = main.NAV_SECTIONS[0]
    assert caption is None
    assert entries == [main._sec_page("/desk")]

def test_desk_breadcrumb_is_just_its_own_name():
    assert main.breadcrumb_trail("/desk") == ["Desk"]

def test_desk_is_a_rail_page_with_no_tab_strip():
    assert main._group_children("/desk") is None
```

Then update the three tests that will now fail:
- `test_nav_section_captions_and_their_derived_counts` → `[None, "MARKETS", "STRATEGY", "ACCOUNT"]` and `[1, 4, 4, 2]`
- `test_drawer_icons_are_present_and_distinct` → `14`, and `space_dashboard` must not collide
- `test_breadcrumb_trail_starts_at_a_section_for_every_page` → allow the caption-less home page

**Step 3: Implement.**

```python
FLAT_NAV = [
    ("/desk", "Desk", "space_dashboard"),
    ("/trade", "Trade Analyzer", "query_stats"),
    ...
]

NAV_SECTIONS = [
    # The landing page, alone above every caption — the rail's mirror of the
    # bottom-pinned SYSTEM_RAIL block, and the reason a caption of None means
    # "render no header at all" rather than "render an empty one".
    (None, [_sec_page("/desk")]),
    ("MARKETS", [...]),
    ...
]
```

In the drawer loop:

```python
            for _i, (caption, entries) in enumerate(NAV_SECTIONS):
                if caption is not None:
                    _nav_section_header(caption, len(entries), first=(_i == 0))
```

⚠ `first=(_i == 0)` now never fires, so MARKETS gains an `mt-4` it did not have. Decide deliberately: either pass `first=(_i == 1)` or add a separator after the Desk row. Check it in the browser at Task 22 — a rail whose first caption jumped down 16px is exactly the kind of regression tests do not catch.

In `breadcrumb_trail`, guard both caption uses:

```python
    for caption, entries in NAV_SECTIONS:
        for entry in entries:
            if entry[0] == "group":
                _kind, label, _icon, children = entry
                if any(path == active for path, _l, _i in children):
                    return [c for c in (caption.title() if caption else "", label,
                                        _NAV_LABEL.get(active, "")) if c]
            elif entry[1] == active:
                return [c for c in (caption.title() if caption else "",
                                    entry[2]) if c]
```

Add `_TAB_COLOR["/desk"]` with a hue not already used.

**Step 4:** `(cd webgui && "$PY" -m pytest tests/test_shell.py -q)`

**Commit** `feat(nav): pin Desk above the rail captions as the landing page`

---

### Task 20: Register the route and move the home redirect

```python
@ui.page("/desk")
def desk_page() -> None:
    with _layout("/desk", "Desk"):
        from pages import desk
        desk.render()
```

Change `_root_to_market_dashboard` to redirect to `/desk`, rename it, and **rewrite its comment block** — the existing one explains at length why `/` points at the Market Dashboard, and leaving that in place while changing the target is precisely the "correct in place, never append" rule CLAUDE.md sets.

Add `"/desk"` to `test_shell.py`'s expected route set and `"desk.py"` to `test_no_inline_style.py`'s `PHASE_8_FILES`.

**Commit** `feat(desk): register /desk and make it the landing page`

---

### Task 21: `page_help` entry

`test_page_help.py::test_every_nav_route_has_a_guide` fails until `HELP_MD["/desk"]` exists, and `test_guides_are_nonempty_markdown` requires it to contain `**`. Write it as real guidance — this file is the most-read prose in the app and the first thing to rot.

**Commit** `docs(help): add the Desk page guide`

---

# Phase 5 — docs and live verification

### Task 22: Verify it running in dev — **not optional**

"Tests pass" is not "verified in dev". The DEV chip, the Status-page restart gating and the launcher guards were all green in tests and wrong in practice.

```bash
git checkout Using_Highcharts && git merge --ff-only claude/dashboard-key-elements-8faf10
```

Then restart `options_svc` (the running one is stale and will not emit the new row keys) and the dev webgui. **Confirm the port is actually free after killing** and read the launcher log — a failed bind is silent, the old server keeps serving, and you will verify stale code while everything looks healthy. The tell is `[Errno 10048] error while attempting to bind`. Use `Get-NetTCPConnection -LocalPort 9500 -State Listen`, not a `netstat` one-liner.

Do **not** preview from the worktree — it has no `env.local.toml`, so it resolves to PROD and binds `:8500` where the live stack already is.

Check, in the browser at `http://127.0.0.1:9500/desk`:
1. All five panels paint; none shows a placeholder while its service is up.
2. Walls appear for `$SPX`/`SPY`/`QQQ`/`$NDX` during RTH — and are **withheld**, not zeroed, after the close.
3. The setup tag is not uniformly `neutral` (if it is, `mins_to_close`/`iv_state` are not reaching `dealer_regime`).
4. Both rings render distinctly — a collision means duplicate `uid`s.
5. The rail shows Desk pinned at top and MARKETS has not shifted.
6. `/` lands on the Desk.

Fastest non-browser check of the Tier-2 half:

```python
from shared.bus import Bus
rows = Bus().cache_get("cache:options:matrix").payload["rows"]
print({k: rows[0].get(k) for k in
       ("symbol","call_wall","put_wall","net_gex","atm_iv","iv_state","dealer_regime")})
```

### Task 23: Documentation

Test-enforced (three parametrized tests in `test_docs_cover_the_ui.py` fail without these): a **Desk section in both** `docs/manuals/user-guide/user-guide.md` and `docs/manuals/reference-guide/reference-guide.md`, in rail order — Desk is now first.

Also update, none of which is test-enforced and all of which rot silently:
- `docs/webgui-routes.md` — a `/desk` section, **and fix its stale `## /` section**, which still describes `/` as the Market Scanner.
- `CLAUDE.md` — the rail's "13 items" → 14, the route table, and the new redirect target. Correct in place; do not append.
- `docs/CHANGELOG.md` — the shipping entry.

**Commit** `docs: document the Desk across the manuals, routes and CLAUDE.md`

### Task 24: Full suite, compared as a set

```bash
(cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest . -q -rf)
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q -rf
(cd options-scanner && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests -q -rf -p no:randomly)
```

Diff the failing node IDs against your Task-0 baseline **name by name**. A matching total is not evidence of a clean run.
