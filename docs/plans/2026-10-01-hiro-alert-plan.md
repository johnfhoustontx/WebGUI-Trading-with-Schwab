# HIRO-style Hedging-Flow Alert — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Model SpotGamma-style HIRO (dealer hedging flow) for $SPX/SPY/QQQ/IWM from the
1-minute chain poll, and fire two screen-only flow alerts — **Hedging surge** and
**Hedging reversal** — with a daily report to validate them before any phone push.

**Architecture:** A pure module `services/options_svc/hiro.py` measures each minute's
signed hedge impact from the chain the GEX poll already fetched (no new Schwab calls)
and holds the detection rules. `compute.collect_gex_snapshots` stores one row per symbol
per minute in a new `hiro_minutes` table in `gex_history.db`; `handlers.run_flow_alerts`
reads it back, runs Surge + Flip, publishes `cache:options:hiro`, and appends alerts to
the existing `cache:options:flow_alerts` list the Flow Alerts page already renders.

**Tech Stack:** Python 3.11, SQLite (`gex_history_db`), Redis via `shared.bus`
(fakeredis under pytest), NiceGUI page module (pure helpers only), pytest.

**Design:** [`2026-10-01-hiro-alert-design.md`](2026-10-01-hiro-alert-design.md) — read it
first. Every rule below is justified there.

---

## Ground rules for the engineer

- **Python:** this worktree has no venv. Use the main checkout's:
  `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"` (written `$PY` below).
  In Git Bash: `PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"`.
- **Run service tests from the repo root, one service at a time** — never
  `pytest services` (cross-app module-name collisions; see root CLAUDE.md "Tests").
- **The suite cannot open a live SQLite store.** The root `conftest.py` refuses
  `sqlite3.connect` into `options-scanner/` etc. Every DB test uses `tmp_path`.
- **Compare the failing SET, not the count.** Task 0 records the baseline.
- **Never weaken an existing assertion to make a test pass.** If an existing test
  breaks, stop and report why — do not edit its expectation unless this plan says to.
- **Config:** every tunable lives in `config/flow_alerts.toml` `[hiro]` AND
  `flow_alerts._DEFAULTS`, AND gets a `webgui/config_schema.py` entry (standing rule;
  `webgui/tests/test_config_schema.py` fails otherwise).
- **No `.style()`** in webgui pages (Tailwind-first; `test_no_inline_style.py`).
- Commit after every task with a conventional prefix and this trailer:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

---

### Task 0: Record the test baseline

**Step 1:** Run and save the failing/skipped sets:

```bash
PY="/d/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
"$PY" -m pytest services/options_svc -q -rfs 2>&1 | tail -15 > /tmp/base_options_svc.txt
(cd options-scanner && "$PY" -m pytest tests -q -rfs 2>&1 | tail -15) > /tmp/base_scanner.txt
(cd webgui && "$PY" -m pytest . -q -rfs 2>&1 | tail -15) > /tmp/base_webgui.txt
"$PY" -m pytest shared/tests -q -rfs 2>&1 | tail -15 > /tmp/base_shared.txt
"$PY" -m pytest deploy -q -rfs 2>&1 | tail -15 > /tmp/base_deploy.txt
"$PY" -m pytest tools/tests -q -rfs 2>&1 | tail -15 > /tmp/base_tools.txt
```

(Use the scratchpad directory instead of `/tmp` if running as Claude.) Expected: all
green (see CLAUDE.md "Tests"). Note anything that is not.

---

### Task 1: `[hiro]` config defaults + TOML

**Files:**
- Modify: `services/options_svc/flow_alerts.py:16-37` (`_DEFAULTS`)
- Modify: `config/flow_alerts.toml` (append a `[hiro]` table)
- Test: `services/options_svc/tests/test_flow_alerts.py`

**Step 1: Write the failing test** (append to `test_flow_alerts.py`):

```python
def test_load_thresholds_has_hiro_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(flow_alerts, "_TOML_PATH", tmp_path / "missing.toml")
    flow_alerts.reset_thresholds_cache()
    h = flow_alerts.load_thresholds()["hiro"]
    assert h["enabled"] is True
    assert h["push"] is False                     # screen-only at ship
    assert h["symbols"] == ["$SPX", "SPY", "QQQ", "IWM"]
    assert h["window_min"] == 15 and h["k"] == 3.0 and h["push_k"] == 4.0
    assert h["min_notional"] == 25_000_000
    assert h["max_unclassified"] == 0.5
    assert h["cooldown_min"] == 30
    assert h["baseline_sessions"] == 5 and h["min_minutes"] == 30
    assert h["flip_enabled"] is True and h["flip_band"] == 1.0
    assert h["flip_not_before"] == "09:00" and h["flip_cooldown_min"] == 60
    assert h["keep_sessions"] == 20
    flow_alerts.reset_thresholds_cache()
```

Check how `test_load_thresholds_has_big_delta_defaults` (line ~51) redirects the TOML
path and copy its exact mechanism if it differs from the above.

**Step 2:** `"$PY" -m pytest services/options_svc/tests/test_flow_alerts.py -k hiro_defaults -q`
→ FAIL (`KeyError: 'hiro'`).

**Step 3: Implement.** Add to `_DEFAULTS` after `"big_delta"`:

```python
    # HIRO-style dealer hedging flow (docs/plans/2026-10-01-hiro-alert-design.md).
    # A MODEL of SpotGamma's HIRO from the 1-min chain poll: Schwab has no tape, so
    # each contract gets one buy/sell label per minute. push=false = Flow screen only.
    "hiro": {"enabled": True, "push": False,
             "symbols": ["$SPX", "SPY", "QQQ", "IWM"],
             "window_min": 15, "k": 3.0, "push_k": 4.0,
             "min_notional": 25_000_000, "max_unclassified": 0.5,
             "cooldown_min": 30, "baseline_sessions": 5, "min_minutes": 30,
             "flip_enabled": True, "flip_band": 1.0, "flip_not_before": "09:00",
             "flip_cooldown_min": 60, "keep_sessions": 20},
```

Append to `config/flow_alerts.toml`:

```toml
[hiro]
# HIRO-style dealer hedging flow -- a MODEL of SpotGamma's HIRO built from the 1-min
# chain poll (Schwab publishes no trade tape, so each contract gets ONE buy/sell label
# per minute). Design: docs/plans/2026-10-01-hiro-alert-design.md.
# Every value below is a STARTING GUESS until tools/hiro_report.py has measured it.
enabled = true            # measure + detect
push    = false           # PHONE gate. false = Flow screen only (ship state).
symbols = ["$SPX", "SPY", "QQQ", "IWM"]
window_min = 15           # Surge window: rolling sum of hedge impact over this many minutes
k = 3.0                   # Surge fires at >= k x the symbol's normal 15-min size (RMS)
push_k = 4.0              # ...and is PHONE-pushed only at >= push_k x (needs push = true)
min_notional = 25000000   # ...and the window's |impact| is at least this ($) -- dead-tape guard
max_unclassified = 0.5    # skip when more than this share of the window's volume had no buy/sell label
cooldown_min = 30         # per symbol and direction
baseline_sessions = 5     # "normal size" = prior sessions used; fewer stored -> today's data
min_minutes = 30          # today-fallback needs at least this many minutes of rows
flip_enabled = true       # Hedging reversal: the day's running total changes sign
flip_band = 1.0           # dead zone around zero, in multiples of normal 15-min size
flip_not_before = "09:00" # CT -- the first half hour crosses zero constantly
flip_cooldown_min = 60    # per symbol
keep_sessions = 20        # hiro_minutes retention (the GEX snapshots keep only 5)
```

**Step 4:** re-run → PASS. Run the whole file: `"$PY" -m pytest services/options_svc/tests/test_flow_alerts.py -q` → PASS.

**Step 5:** Commit: `feat(options): [hiro] flow-alert config defaults`

---

### Task 2: `hiro.classify_side`

**Files:**
- Create: `services/options_svc/hiro.py`
- Test: `services/options_svc/tests/test_hiro.py`

**Step 1: Failing test** (`test_hiro.py`):

```python
import math

import pytest

from services.options_svc import hiro


@pytest.mark.parametrize("last,bid,ask,want", [
    (1.10, 1.00, 1.10, 1),     # at the ask -> customer bought
    (1.20, 1.00, 1.10, 1),     # through the ask
    (1.00, 1.00, 1.10, -1),    # at the bid -> customer sold
    (0.95, 1.00, 1.10, -1),
    (1.08, 1.00, 1.10, 1),     # above mid
    (1.02, 1.00, 1.10, -1),    # below mid
    (1.05, 1.00, 1.10, 0),     # exactly mid -> unclassified, never guessed
])
def test_classify_side_quote_rule(last, bid, ask, want):
    assert hiro.classify_side(last, bid, ask) == want


@pytest.mark.parametrize("last,bid,ask", [
    (None, 1.0, 1.1), (1.0, None, 1.1), (1.0, 1.0, None),
    (math.nan, 1.0, 1.1), (1.0, math.inf, 1.1), (True, 1.0, 1.1),
    (0.0, 1.0, 1.1),           # no print
    (1.0, 1.2, 1.1),           # crossed quote
    (1.0, -999.0, 1.1),        # sentinel
])
def test_classify_side_unusable_quote_is_unclassified(last, bid, ask):
    assert hiro.classify_side(last, bid, ask) == 0
```

**Step 2:** `"$PY" -m pytest services/options_svc/tests/test_hiro.py -q` → FAIL (module missing).

**Step 3: Implement** `services/options_svc/hiro.py`:

```python
"""HIRO-style dealer hedging flow — measurement + detection. PURE (stdlib only).

A MODEL of SpotGamma's HIRO, not SpotGamma's number. Schwab publishes no
time-and-sales tape, so each contract gets ONE buy/sell label per minute, read
from where its latest trade price sits against this minute's bid/ask.
Design: docs/plans/2026-10-01-hiro-alert-design.md.

Hedge impact of a trade = side x delta x contracts x 100 x spot, with the
contract's SIGNED delta: a customer buying a call (+1 x +delta) makes the dealer
buy stock (positive); buying a put (+1 x -delta) makes the dealer sell (negative).
"""
import math


def _finite(v):
    """A real finite float, or None (rejects bool, NaN, inf, non-numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def classify_side(last, bid, ask) -> int:
    """+1 customer bought, -1 customer sold, 0 unclassified.

    At/through the ask = bought, at/through the bid = sold, otherwise the side of
    the midpoint. Exactly at the midpoint, or any unusable quote, is 0 — a reading
    with no label must never be guessed into one."""
    last, bid, ask = _finite(last), _finite(bid), _finite(ask)
    if last is None or bid is None or ask is None:
        return 0
    if last <= 0 or bid < 0 or ask <= 0 or ask < bid:
        return 0
    if last >= ask:
        return 1
    if last <= bid:
        return -1
    mid = (bid + ask) / 2.0
    if last > mid:
        return 1
    if last < mid:
        return -1
    return 0
```

**Step 4:** re-run → PASS.

**Step 5:** Commit: `feat(options): hiro.classify_side quote rule`

---

### Task 3: `hiro.measure_chain`

**Files:** Modify `services/options_svc/hiro.py`; Test `services/options_svc/tests/test_hiro.py`

The chain shape is Schwab's real `/chains` shape — `callExpDateMap` /
`putExpDateMap` → `"YYYY-MM-DD:DTE"` → `"strike"` → `[contract]`, contract keys
`symbol` (OSI), `totalVolume`, `delta`, `last`, `bid`, `ask`; root `underlyingPrice`.

**Step 1: Failing tests** (append):

```python
def _c(osi, vol, delta, last, bid=1.00, ask=1.10):
    return {"symbol": osi, "totalVolume": vol, "delta": delta,
            "last": last, "bid": bid, "ask": ask}


def _chain(calls=(), puts=(), spot=500.0):
    def emap(cs):
        return {"2026-10-02:1": {str(100 + i): [c] for i, c in enumerate(cs)}}
    return {"underlyingPrice": spot, "callExpDateMap": emap(calls),
            "putExpDateMap": emap(puts)}


def test_measure_first_sight_seeds_baseline_and_books_nothing():
    chain = _chain(calls=[_c("C1", 1000, 0.5, 1.10)])
    row, prev = hiro.measure_chain(chain, {})
    assert prev == {"C1": 1000.0}
    assert row["impact"] == 0.0 and row["classified_vol"] == 0.0
    assert row["unclassified_vol"] == 0.0 and row["spot"] == 500.0


def test_measure_signs_call_and_put_buys_correctly():
    prev = {"C1": 1000.0, "P1": 50.0}
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10)],      # 10 bought at ask
                   puts=[_c("P1", 54, -0.4, 1.10)])        # 4 bought at ask
    row, new_prev = hiro.measure_chain(chain, prev)
    # call: +1 * 0.5 * 10 * 100 * 500 = +250,000 ; put: +1 * -0.4 * 4 * 100 * 500 = -80,000
    assert row["impact"] == pytest.approx(170_000.0)
    assert row["classified_vol"] == 14.0
    assert new_prev == {"C1": 1010.0, "P1": 54.0}


def test_measure_customer_sell_of_call_is_dealer_selling():
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.00)]), {"C1": 1000.0})
    assert row["impact"] == pytest.approx(-250_000.0)


def test_measure_mid_print_is_unclassified_and_contributes_nothing():
    row, _ = hiro.measure_chain(_chain(calls=[_c("C1", 1010, 0.5, 1.05)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["unclassified_vol"] == 10.0 and row["classified_vol"] == 0.0


@pytest.mark.parametrize("delta", [math.nan, -999.0, 1.5, None, True])
def test_measure_drops_bad_delta(delta):
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 1010, delta, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0
    assert row["classified_vol"] == 0.0 and row["unclassified_vol"] == 0.0
    assert prev["C1"] == 1010.0           # baseline still advances


def test_measure_volume_reset_books_nothing():
    row, prev = hiro.measure_chain(_chain(calls=[_c("C1", 5, 0.5, 1.10)]), {"C1": 1000.0})
    assert row["impact"] == 0.0 and prev["C1"] == 5.0


@pytest.mark.parametrize("spot", [None, 0, -1, math.nan])
def test_measure_unusable_spot_returns_no_row(spot):
    chain = _chain(calls=[_c("C1", 1010, 0.5, 1.10)], spot=spot)
    row, prev = hiro.measure_chain(chain, {"C1": 1000.0})
    assert row is None
    assert prev == {"C1": 1000.0}          # nothing consumed: the next good minute books it


@pytest.mark.parametrize("bad", [None, {}, "x", {"callExpDateMap": "x", "underlyingPrice": 500}])
def test_measure_malformed_chain_is_total(bad):
    row, prev = hiro.measure_chain(bad, {})
    assert prev == {}
```

**Step 2:** run → FAIL.

**Step 3: Implement** (append to `hiro.py`):

```python
def _contracts(chain):
    """Yield every contract dict in a Schwab chain. Total over malformed input."""
    for mapkey in ("callExpDateMap", "putExpDateMap"):
        exp_map = chain.get(mapkey)
        if not isinstance(exp_map, dict):
            continue
        for strike_map in exp_map.values():
            if not isinstance(strike_map, dict):
                continue
            for contracts in strike_map.values():
                if not isinstance(contracts, list):
                    continue
                for c in contracts:
                    if isinstance(c, dict):
                        yield c


def measure_chain(chain, prev_vol):
    """One minute of hedge impact for one symbol.

    ``prev_vol`` is ``{contract symbol: totalVolume}`` from the previous poll.
    Returns ``(row, new_prev)``; ``row`` is ``{"spot", "impact",
    "classified_vol", "unclassified_vol"}`` or None when the chain has no usable
    spot (then ``new_prev`` is ``prev_vol`` unchanged, so the next good minute
    books the volume rather than losing it).

    A contract's FIRST reading only seeds the baseline: after a restart it must
    never book the whole day's volume into one minute."""
    prev_vol = dict(prev_vol or {})
    if not isinstance(chain, dict):
        return None, prev_vol
    spot = _finite(chain.get("underlyingPrice"))
    if spot is None or spot <= 0:
        return None, prev_vol
    new_prev = dict(prev_vol)
    impact = classified = unclassified = 0.0
    for c in _contracts(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None:
            continue
        before = new_prev.get(osi)
        new_prev[osi] = vol
        if before is None:
            continue
        dv = vol - before
        if dv <= 0:
            continue
        delta = _finite(c.get("delta"))
        if delta is None or abs(delta) > 1:      # NaN / Schwab's -999 sentinel
            continue
        side = classify_side(c.get("last"), c.get("bid"), c.get("ask"))
        if side == 0:
            unclassified += dv
            continue
        classified += dv
        impact += side * delta * dv * 100.0 * spot
    return ({"spot": spot, "impact": impact, "classified_vol": classified,
             "unclassified_vol": unclassified}, new_prev)
```

**Step 4:** run → PASS.

**Step 5:** Commit: `feat(options): hiro.measure_chain per-minute hedge impact`

---

### Task 4: `hiro_minutes` table in `gex_history_db`

**Files:**
- Modify: `options-scanner/gex_history_db.py` (schema + four functions)
- Test: `options-scanner/tests/test_gex_history_hiro.py` (new)

**Step 1: Failing test:**

```python
import datetime as dt
import sqlite3

import gex_history_db as gh


def _conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "g.db"))
    gh.init_schema(conn)
    return conn


def _ts(d, hh, mm):
    return int(dt.datetime(d.year, d.month, d.day, hh, mm).timestamp())   # local, as gh does


def test_hiro_insert_and_load_day_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {"spot": 500.0, "impact": 1.5e6,
                       "classified_vol": 10.0, "unclassified_vol": 2.0})
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 1), {"spot": 501.0, "impact": -2.0e6,
                       "classified_vol": 5.0, "unclassified_vol": 0.0})
    rows = gh.load_hiro_day(conn, "SPY", d)
    assert [r["impact"] for r in rows] == [1.5e6, -2.0e6]
    assert rows[0] == {"ts": _ts(d, 9, 0), "spot": 500.0, "impact": 1.5e6,
                       "classified_vol": 10.0, "unclassified_vol": 2.0}
    assert gh.load_hiro_day(conn, "QQQ", d) == []


def test_hiro_insert_same_minute_replaces(tmp_path):
    conn = _conn(tmp_path)
    d = dt.date(2026, 10, 1)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), row)
    gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {**row, "impact": 2.0})
    assert [r["impact"] for r in gh.load_hiro_day(conn, "SPY", d)] == [2.0]


def test_hiro_prior_sessions_newest_first_and_excludes_today(tmp_path):
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    days = [dt.date(2026, 9, 28), dt.date(2026, 9, 29), dt.date(2026, 9, 30), dt.date(2026, 10, 1)]
    for i, d in enumerate(days):
        gh.insert_hiro_row(conn, "SPY", _ts(d, 9, 0), {**row, "impact": float(i)})
    got = gh.load_hiro_prior_sessions(conn, "SPY", 2, before=dt.date(2026, 10, 1))
    assert [[r["impact"] for r in s] for s in got] == [[2.0], [1.0]]


def test_purge_hiro_keeps_last_n_sessions(tmp_path):
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    for day in (28, 29, 30):
        gh.insert_hiro_row(conn, "SPY", _ts(dt.date(2026, 9, day), 9, 0), row)
    assert gh.purge_hiro(conn, keep_sessions=2) == 1
    assert gh.load_hiro_day(conn, "SPY", dt.date(2026, 9, 28)) == []
    assert gh.load_hiro_day(conn, "SPY", dt.date(2026, 9, 29)) != []


def test_purge_keep_sessions_does_not_touch_hiro(tmp_path):
    """The GEX retention (5 sessions) must not cut the hiro baseline."""
    conn = _conn(tmp_path)
    row = {"spot": 1.0, "impact": 1.0, "classified_vol": 1.0, "unclassified_vol": 0.0}
    gh.insert_hiro_row(conn, "SPY", _ts(dt.date(2026, 9, 1), 9, 0), row)
    gh.purge_keep_sessions(conn, keep_sessions=1)
    assert gh.load_hiro_day(conn, "SPY", dt.date(2026, 9, 1)) != []
```

**Step 2:** `cd options-scanner && "$PY" -m pytest tests/test_gex_history_hiro.py -q` → FAIL.

**Step 3: Implement** in `gex_history_db.py`. Add the DDL constant beside `TERM_SCHEMA_SQL`:

```python
HIRO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hiro_minutes (
    symbol            TEXT    NOT NULL,
    ts                INTEGER NOT NULL,
    spot              REAL,
    impact            REAL    NOT NULL,
    classified_vol    REAL    NOT NULL,
    unclassified_vol  REAL    NOT NULL,
    PRIMARY KEY (symbol, ts)
);
"""
```

At the end of `init_schema`, after `init_term_schema(conn)`, add
`conn.executescript(HIRO_SCHEMA_SQL); conn.commit()`. Then add:

```python
_HIRO_COLS = ("ts", "spot", "impact", "classified_vol", "unclassified_vol")


def insert_hiro_row(conn, symbol, ts, row) -> None:
    """One minute of HIRO-model hedge impact (services/options_svc/hiro.py).
    INSERT OR REPLACE: a re-run of the same minute overwrites, never doubles."""
    conn.execute(
        "INSERT OR REPLACE INTO hiro_minutes "
        "(symbol, ts, spot, impact, classified_vol, unclassified_vol) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (symbol, int(ts), row.get("spot"), float(row["impact"]),
         float(row["classified_vol"]), float(row["unclassified_vol"])))
    conn.commit()


def load_hiro_day(conn, symbol, d=None) -> list[dict]:
    """Every hiro_minutes row for ``symbol`` on local date ``d`` (default today), ASC."""
    start, end = _local_unix_range(d)
    cur = conn.execute(
        "SELECT ts, spot, impact, classified_vol, unclassified_vol FROM hiro_minutes "
        "WHERE symbol = ? AND ts >= ? AND ts < ? ORDER BY ts", (symbol, start, end))
    return [dict(zip(_HIRO_COLS, r)) for r in cur.fetchall()]


def _hiro_dates(conn, symbol=None) -> list[str]:
    sql = "SELECT DISTINCT DATE(ts, 'unixepoch', 'localtime') AS d FROM hiro_minutes"
    args = ()
    if symbol is not None:
        sql += " WHERE symbol = ?"
        args = (symbol,)
    cur = conn.execute(sql + " ORDER BY d DESC", args)
    return [r[0] for r in cur.fetchall() if r[0] is not None]


def load_hiro_prior_sessions(conn, symbol, n, before=None) -> list[list[dict]]:
    """The last ``n`` stored sessions for ``symbol`` strictly BEFORE local date
    ``before`` (default today), newest first, each a list of rows ASC."""
    before = (before or _dt.date.today()).isoformat()
    out = []
    for ds in _hiro_dates(conn, symbol):
        if ds >= before:
            continue
        y, m, dd = (int(x) for x in ds.split("-"))
        out.append(load_hiro_day(conn, symbol, _dt.date(y, m, dd)))
        if len(out) >= n:
            break
    return out


def purge_hiro(conn, keep_sessions: int = 20) -> int:
    """Keep the last ``keep_sessions`` distinct session-dates of hiro_minutes.
    Separate from purge_keep_sessions on purpose: the GEX grids keep 5 sessions,
    and the HIRO baseline needs 5 sessions BEFORE today."""
    keep_sessions = max(1, int(keep_sessions))
    dates = _hiro_dates(conn)
    if len(dates) <= keep_sessions:
        return 0
    y, m, dd = (int(x) for x in dates[keep_sessions - 1].split("-"))
    cutoff, _ = _local_unix_range(_dt.date(y, m, dd))
    cur = conn.execute("DELETE FROM hiro_minutes WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount or 0
```

(Confirm `_dt` is the module's `datetime` import alias — `purge_keep_sessions` uses
`_dt.date`, so it is.)

**Step 4:** run → PASS, then the whole options-scanner suite:
`cd options-scanner && "$PY" -m pytest tests -q -rfs` → same set as baseline.

**Step 5:** Commit: `feat(gex-history): hiro_minutes table with its own retention`

---

### Task 5: Measure during collection

**Files:**
- Modify: `services/options_svc/compute.py` (near `_BIG_DELTA_STASH` ~L4311; inside
  `collect_gex_snapshots` ~L4826-4892; `_maybe_purge_gex` ~L4654)
- Test: `services/options_svc/tests/test_compute.py`

The `on_chain` callback runs on the collector's calling thread (see
`options-scanner/gex_collector.py:326-341`), so a plain module dict is safe.

**Step 1: Failing tests** (append to `test_compute.py`):

```python
def _hchain(vol, last=1.10, spot=500.0):
    return {"underlyingPrice": spot,
            "callExpDateMap": {"2026-10-02:1": {"100": [
                {"symbol": "C1", "totalVolume": vol, "delta": 0.5,
                 "last": last, "bid": 1.0, "ask": 1.1}]}},
            "putExpDateMap": {}}


def test_hiro_tick_row_seeds_then_books_and_resets_per_date():
    compute.reset_hiro_memo()
    assert compute.hiro_tick_row("SPY", _hchain(1000), "2026-10-01")["impact"] == 0.0
    row = compute.hiro_tick_row("SPY", _hchain(1010), "2026-10-01")
    assert row["impact"] == pytest.approx(250_000.0)
    # a new session date clears the memo: first reading seeds again
    assert compute.hiro_tick_row("SPY", _hchain(20), "2026-10-02")["impact"] == 0.0
    compute.reset_hiro_memo()


def test_hiro_tick_row_never_raises():
    compute.reset_hiro_memo()
    assert compute.hiro_tick_row("SPY", None, "2026-10-01") is None
```

(Add `import pytest` at the top of the test file if it is not already there.)

**Step 2:** run `"$PY" -m pytest services/options_svc/tests/test_compute.py -k hiro -q` → FAIL.

**Step 3: Implement.** Below `take_big_delta_stash` in `compute.py`:

```python
# HIRO-model per-contract volume memo: {"date": "YYYY-MM-DD", "prev": {symbol:
# {contract symbol: totalVolume}}}. Cleared when the session date changes, so the
# first poll of a day seeds rather than books (hiro.measure_chain).
_HIRO_MEMO: dict = {"date": None, "prev": {}}


def reset_hiro_memo():
    _HIRO_MEMO.update(date=None, prev={})


def hiro_tick_row(symbol, chain, session_date):
    """This minute's HIRO-model row for one symbol, advancing the volume memo.
    Best-effort: never raises (a measurement failure must not break collection)."""
    try:
        from services.options_svc import hiro
        if _HIRO_MEMO["date"] != session_date:
            _HIRO_MEMO.update(date=session_date, prev={})
        row, new_prev = hiro.measure_chain(chain, _HIRO_MEMO["prev"].get(symbol, {}))
        _HIRO_MEMO["prev"][symbol] = new_prev
        return row
    except Exception:
        _degrade.degraded("options.hiro_tick_row")
        return None
```

Check `compute.py` already imports `_degrade` (`from services import _degrade`); if
not, use `log.debug(..., exc_info=True)` like the neighbouring stash code.

In `collect_gex_snapshots`, after `_big_delta_on = ...` (~L4857) add:

```python
        # HIRO model (docs/plans/2026-10-01-hiro-alert-design.md): regular session
        # only -- off-hours the chain's underlyingPrice can be stale and index OI
        # reads zero. Rows are written AFTER poll_once, on this write connection.
        from shared import market_calendar as _mc
        import datetime as _dtm
        from zoneinfo import ZoneInfo as _ZI
        _hiro_cfg = _uoa_cfg.get("hiro", {})
        _hiro_now = now or _dtm.datetime.now(_ZI("America/Chicago"))
        _hiro_on = (_hiro_cfg.get("enabled", True) and _mc.is_regular_hours(_hiro_now))
        _hiro_syms = set(_hiro_cfg.get("symbols") or [])
        _hiro_date = _hiro_now.date().isoformat()
        _hiro_rows: dict = {}
```

Inside `on_chain`, after the big_delta block:

```python
            if _hiro_on and sym in _hiro_syms:
                r = hiro_tick_row(sym, chain, _hiro_date)
                if r is not None:
                    _hiro_rows[sym] = r
```

After `gc.poll_once(...)` and before `gc.touch_lock(...)`:

```python
        if _hiro_rows:
            _ts_min = int(_hiro_now.timestamp()) // 60 * 60
            for _s, _r in _hiro_rows.items():
                try:
                    gh.insert_hiro_row(conn, _s, _ts_min, _r)
                except Exception:
                    _degrade.degraded("options.hiro_insert")
```

In `_maybe_purge_gex`, after `gh.purge_keep_sessions(...)`:

```python
        try:
            from services.options_svc import flow_alerts as _fa
            gh.purge_hiro(conn, keep_sessions=_fa.load_thresholds()
                          .get("hiro", {}).get("keep_sessions", 20))
        except Exception:
            _degrade.degraded("options.purge_hiro")
```

Verify `is_regular_hours` accepts a CT-aware datetime (`shared/market_calendar.py:477`,
it calls `_ct_of(now)`), and that `now` in `collect_gex_snapshots` is either None or a
datetime (check its callers in `handlers.py` / `scheduler.py`).

**Step 4:** run the two tests → PASS, then the full service suite:
`"$PY" -m pytest services/options_svc -q -rfs` → baseline set.

**Step 5:** Commit: `feat(options): record HIRO-model minutes during GEX collection`

---

### Task 6: Surge detection

**Files:** Modify `services/options_svc/hiro.py`; Test `test_hiro.py`

**Step 1: Failing tests** (append):

```python
CFG = {"window_min": 15, "k": 3.0, "min_notional": 1_000_000,
       "max_unclassified": 0.5, "baseline_sessions": 2, "min_minutes": 30,
       "flip_band": 1.0}


def _rows(impacts, t0=36000, spot=500.0, uncl=0.0):
    return [{"ts": t0 + 60 * i, "spot": spot, "impact": float(x),
             "classified_vol": 10.0, "unclassified_vol": uncl}
            for i, x in enumerate(impacts)]


def test_window_sum_covers_last_n_minutes_only():
    rows = _rows([1, 2, 3, 4])
    w = hiro.window_sum(rows, rows[-1]["ts"], 120)      # last 2 minutes
    assert w["impact"] == 7.0 and w["n"] == 2


def test_window_sum_unclassified_share():
    rows = _rows([1, 1], uncl=10.0)
    assert hiro.window_sum(rows, rows[-1]["ts"], 900)["unclassified_share"] == 0.5


def test_full_window_sums_skip_partial_windows():
    rows = _rows([1] * 20)
    sums = hiro.full_window_sums(rows, 900)
    assert len(sums) == 6 and all(s == 15.0 for s in sums)


def test_rms_ignores_nonfinite_and_rejects_zero():
    assert hiro.rms([3.0, -4.0]) == pytest.approx(math.sqrt(12.5))
    assert hiro.rms([math.nan, 3.0]) == 3.0
    assert hiro.rms([]) is None and hiro.rms([0.0, 0.0]) is None


def test_baseline_prefers_prior_sessions():
    prior = [_rows([1e6] * 20), _rows([-1e6] * 20)]
    sigma = hiro.baseline_sigma(prior, _rows([9e9] * 40), CFG)
    assert sigma == pytest.approx(15e6)                # today's spike not used


def test_baseline_falls_back_to_today_then_none():
    assert hiro.baseline_sigma([], _rows([1e6] * 30), CFG) == pytest.approx(15e6)
    assert hiro.baseline_sigma([], _rows([1e6] * 29), CFG) is None


def test_surge_fires_dealers_buying_above_k():
    rows = _rows([0] * 15 + [4e6] * 15)                 # last 15m = 60e6
    a = hiro.detect_surge("SPY", rows, 15e6, CFG)
    assert a["type"] == "hiro_surge" and a["side"] == "dealers_buying"
    assert a["impact"] == 60e6 and a["mult"] == pytest.approx(4.0)
    assert a["ts"] == rows[-1]["ts"] and a["spot"] == 500.0
    assert a["unclassified_share"] == 0.0 and a["window_min"] == 15


def test_surge_selling_side():
    a = hiro.detect_surge("SPY", _rows([-4e6] * 15), 15e6, CFG)
    assert a["side"] == "dealers_selling"


def test_surge_silent_below_k_below_floor_unlabelled_or_no_sigma():
    assert hiro.detect_surge("SPY", _rows([2e6] * 15), 15e6, CFG) is None   # 2x
    small = {**CFG, "min_notional": 1e9}
    assert hiro.detect_surge("SPY", _rows([4e6] * 15), 15e6, small) is None
    assert hiro.detect_surge("SPY", _rows([4e6] * 15, uncl=20.0), 15e6, CFG) is None
    assert hiro.detect_surge("SPY", _rows([4e6] * 15), None, CFG) is None
    assert hiro.detect_surge("SPY", [], 15e6, CFG) is None
```

**Step 2:** run → FAIL.

**Step 3: Implement** (append to `hiro.py`):

```python
def window_sum(rows, end_ts, window_sec):
    """Sum of the rows with ``end_ts - window_sec < ts <= end_ts``."""
    w = [r for r in rows if end_ts - window_sec < r["ts"] <= end_ts]
    impact = sum(r["impact"] for r in w)
    cls = sum(r["classified_vol"] for r in w)
    uncl = sum(r["unclassified_vol"] for r in w)
    total = cls + uncl
    return {"impact": impact, "n": len(w),
            "unclassified_share": (uncl / total) if total > 0 else None}


def full_window_sums(rows, window_sec):
    """The window sum ending at every row whose window lies wholly inside the
    session (the first ``window`` minutes give partial sums, which would shrink
    the baseline). Rows are ASC; O(n x window), fine for ~390 rows."""
    if not rows:
        return []
    first = rows[0]["ts"]
    return [window_sum(rows, r["ts"], window_sec)["impact"]
            for r in rows if r["ts"] - first >= window_sec - 60]


def rms(values):
    """Root-mean-square of the finite values, or None (none, or all zero).
    RMS, not a standard deviation: hedging flow's centre is zero, and a
    day-long drift is the signal, not noise to subtract."""
    vals = [v for v in (_finite(x) for x in values) if v is not None]
    if not vals:
        return None
    s = math.sqrt(sum(v * v for v in vals) / len(vals))
    return s if s > 0 else None


def baseline_sigma(prior_sessions, today_rows, cfg):
    """A symbol's normal 15-minute size. Prior sessions (newest first) when at
    least ``baseline_sessions`` exist; else today's full windows once there are
    ``min_minutes`` rows; else None (the rules do not run)."""
    window = int(cfg["window_min"]) * 60
    need = int(cfg["baseline_sessions"])
    if len(prior_sessions) >= need:
        return rms([v for s in prior_sessions[:need] for v in full_window_sums(s, window)])
    if len(today_rows) >= int(cfg["min_minutes"]):
        return rms(full_window_sums(today_rows, window))
    return None


def detect_surge(symbol, today_rows, sigma, cfg):
    """A ``hiro_surge`` alert dict for the window ending at the latest row, or
    None. No cooldown here — the handler owns that, as for every flow detector."""
    if not today_rows or sigma is None or sigma <= 0:
        return None
    last = today_rows[-1]
    w = window_sum(today_rows, last["ts"], int(cfg["window_min"]) * 60)
    share = w["unclassified_share"]
    if share is None or share > cfg["max_unclassified"]:
        return None
    imp = w["impact"]
    if abs(imp) < cfg["min_notional"]:
        return None
    mult = abs(imp) / sigma
    if mult < cfg["k"]:
        return None
    return {"type": "hiro_surge",
            "side": "dealers_buying" if imp > 0 else "dealers_selling",
            "symbol": symbol, "ts": last["ts"], "spot": last["spot"],
            "impact": imp, "mult": mult, "window_min": int(cfg["window_min"]),
            "unclassified_share": share}
```

**Step 4:** run → PASS.

**Step 5:** Commit: `feat(options): HIRO surge detection`

---

### Task 7: Reversal (flip) detection

**Files:** Modify `services/options_svc/hiro.py`; Test `test_hiro.py`

**Step 1: Failing tests** (append):

```python
def test_ct_ts_is_central_wallclock():
    import datetime as _d
    from zoneinfo import ZoneInfo
    want = _d.datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Chicago")).timestamp()
    assert hiro.ct_ts("2026-10-01", "09:00") == int(want)


def test_flip_transitions_hysteresis_and_baseline():
    # cum: 5, 10, 4, -2, -6, -12 ; band 5 -> baseline buying at 10? (5>=5) then
    # selling only once cum <= -5 (at -6)
    rows = _rows([5, 5, -6, -6, -4, -6])
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[0]["ts"])
    assert [(s) for _, s, _ in t] == ["selling"]
    assert t[0][0] == rows[4]["ts"]


def test_flip_transitions_ignore_minutes_before_not_before_but_keep_their_cum():
    rows = _rows([20, -1, -1])                        # cum 20, 19, 18
    t = hiro.flip_transitions(rows, band=5.0, not_before_ts=rows[1]["ts"])
    assert t == []                                    # baseline buying, no change


def test_detect_flip_fresh_newer_than_seen():
    rows = _rows([10, -30])                           # cum 10 -> -20
    a = hiro.detect_flip("SPY", rows, sigma=5.0, cfg=CFG,
                         not_before_ts=rows[0]["ts"], seen_ts=None)
    assert a["type"] == "hiro_flip" and a["side"] == "to_selling"
    assert a["ts"] == rows[1]["ts"] and a["cum"] == -20.0 and a["spot"] == 500.0
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], seen_ts=a["ts"]) is None


def test_detect_flip_ignores_a_stale_transition():
    rows = _rows([10, -30, -1, -1, -1])               # flip at minute 1, now minute 4
    assert hiro.detect_flip("SPY", rows, 5.0, CFG, rows[0]["ts"], None) is None


def test_detect_flip_needs_sigma():
    assert hiro.detect_flip("SPY", _rows([10, -30]), None, CFG, 0, None) is None
```

**Step 2:** run → FAIL.

**Step 3: Implement** (append; add `import datetime as _dt` and
`from zoneinfo import ZoneInfo` at the top of `hiro.py`):

```python
_CT = ZoneInfo("America/Chicago")
FLIP_MAX_AGE_SEC = 120     # a transition older than this is history, not news


def ct_ts(day, hhmm) -> int:
    """Unix seconds of ``hhmm`` Central on ``day`` ('YYYY-MM-DD' or a date)."""
    if isinstance(day, str):
        day = _dt.date.fromisoformat(day)
    h, m = (int(x) for x in str(hhmm).split(":"))
    return int(_dt.datetime(day.year, day.month, day.day, h, m, tzinfo=_CT).timestamp())


def _state(cum, prev, band):
    if prev == "buying":
        return "selling" if cum <= -band else "buying"
    if prev == "selling":
        return "buying" if cum >= band else "selling"
    if cum >= band:
        return "buying"
    if cum <= -band:
        return "selling"
    return None


def flip_transitions(rows, band, not_before_ts):
    """``[(ts, new_state, cum)]`` for every change of the day's hedging direction.

    The running total counts from the session's FIRST row; only the evaluation
    waits for ``not_before_ts``. The first state reached is the baseline and is
    not a transition. Hysteresis: a state changes only when the total clears zero
    by ``band`` on the other side."""
    out, state, cum = [], None, 0.0
    for r in rows:
        cum += r["impact"]
        if r["ts"] < not_before_ts:
            continue
        new = _state(cum, state, band)
        if new is None:
            continue
        if state is not None and new != state:
            out.append((r["ts"], new, cum))
        state = new
    return out


def detect_flip(symbol, rows, sigma, cfg, not_before_ts, seen_ts):
    """A ``hiro_flip`` alert for the latest transition, or None.

    Stateless: replays today's rows every tick. Fires only for a transition
    newer than ``seen_ts`` AND at most FLIP_MAX_AGE_SEC old, so a restart cannot
    fire an old flip and an intraday-moving sigma cannot surface one."""
    if not rows or sigma is None or sigma <= 0:
        return None
    t = flip_transitions(rows, float(cfg["flip_band"]) * sigma, not_before_ts)
    if not t:
        return None
    ts, state, cum = t[-1]
    if seen_ts is not None and ts <= seen_ts:
        return None
    if rows[-1]["ts"] - ts > FLIP_MAX_AGE_SEC:
        return None
    spot = next((r["spot"] for r in rows if r["ts"] == ts), None)
    return {"type": "hiro_flip", "side": "to_buying" if state == "buying" else "to_selling",
            "symbol": symbol, "ts": ts, "spot": spot, "cum": cum}
```

⚠ The comment in the first flip test claims a cum sequence; recompute it by hand when
running it (`5,10,4,-2,-6,-12` for impacts `5,5,-6,-6,-4,-6`) and fix the COMMENT if
the arithmetic in it is wrong — never the assertion.

**Step 4:** run → PASS.

**Step 5:** Commit: `feat(options): HIRO reversal (flip) detection`

---

### Task 8: Alert text, push gate, and the view row

**Files:** Modify `services/options_svc/flow_alerts.py` and `services/options_svc/hiro.py`;
Test `test_flow_alerts.py`, `test_hiro.py`

**Step 1: Failing tests.** In `test_flow_alerts.py`:

```python
_HS = {"type": "hiro_surge", "side": "dealers_buying", "symbol": "$SPX", "ts": 1,
       "spot": 5712.5, "impact": 2.4e9, "mult": 3.6, "window_min": 15,
       "unclassified_share": 0.18}
_HF = {"type": "hiro_flip", "side": "to_selling", "symbol": "SPY", "ts": 1,
       "spot": 571.2, "cum": -3.1e8}


def test_alert_text_hiro_surge():
    t = flow_alerts.alert_text(_HS)
    assert t.startswith("$SPX — hedging surge: dealers BUYING about $2.40B of stock")
    assert "15 min" in t and "3.6× normal" in t and "18% unlabelled" in t
    assert "model" in t


def test_alert_text_hiro_flip():
    t = flow_alerts.alert_text(_HF)
    assert t.startswith("SPY — dealer hedging turned to net SELLING for the day")
    assert "-$310.00M" in t


def test_hiro_should_push_gates():
    on = {"hiro": {"push": True, "push_k": 4.0}}
    off = {"hiro": {"push": False, "push_k": 4.0}}
    assert not flow_alerts.hiro_should_push({**_HS, "mult": 9.0}, off)
    assert not flow_alerts.hiro_should_push(_HS, on)                  # 3.6 < 4
    assert flow_alerts.hiro_should_push({**_HS, "mult": 4.0}, on)
    assert flow_alerts.hiro_should_push(_HF, on)                      # flips: push gate only
    assert not flow_alerts.hiro_should_push({"type": "uoa"}, on)
    assert not flow_alerts.hiro_should_push({**_HS, "mult": float("nan")}, on)
```

In `test_hiro.py`:

```python
def test_symbol_view_summarises_latest_minute():
    rows = _rows([1e6] * 15)
    v = hiro.symbol_view(rows, 5e6, CFG)
    assert v == {"ts": rows[-1]["ts"], "spot": 500.0, "impact": 1e6, "cum": 15e6,
                 "window_impact": 15e6, "sigma": 5e6, "mult": pytest.approx(3.0),
                 "unclassified_share": 0.0}
    assert hiro.symbol_view(rows, None, CFG)["mult"] is None
```

**Step 2:** run → FAIL.

**Step 3: Implement.** In `flow_alerts.py`:

```python
HIRO_TYPES = ("hiro_surge", "hiro_flip")


def _hiro_money(v):
    """$2.40B / $310.00M / $25k -- billions matter here ($SPX hedging runs to $B)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "$0"
    if abs(v) >= 999_500_000:
        return f"{'-' if v < 0 else ''}${abs(v)/1e9:.2f}B"
    return ("-" if v < 0 else "") + _human_money(abs(v))


def hiro_should_push(alert, cfg) -> bool:
    """Phone gate for the HIRO alerts, separate from firing (the big_delta
    pattern): every fire reaches the Flow screen; a surge is pushed only at
    >= push_k x normal, a reversal whenever push is on. Never raises."""
    h = (cfg or {}).get("hiro", {}) if isinstance(cfg, dict) else {}
    if not h.get("push", False) or not isinstance(alert, dict):
        return False
    t = alert.get("type")
    if t == "hiro_flip":
        return True
    if t != "hiro_surge":
        return False
    m = alert.get("mult")
    return (isinstance(m, (int, float)) and not isinstance(m, bool)
            and m == m and float(m) >= h.get("push_k", 4.0))
```

In `alert_text`, before the final `return`:

```python
    if a["type"] == "hiro_surge":
        word = "BUYING" if a["side"] == "dealers_buying" else "SELLING"
        return (f"{s} — hedging surge: dealers {word} about "
                f"{_hiro_money(abs(a.get('impact') or 0))} of stock in "
                f"{a.get('window_min', 15)} min ({a.get('mult', 0):.1f}× normal) · "
                f"spot {a.get('spot', 0):g} · model, "
                f"{(a.get('unclassified_share') or 0):.0%} unlabelled")
    if a["type"] == "hiro_flip":
        word = "BUYING" if a["side"] == "to_buying" else "SELLING"
        return (f"{s} — dealer hedging turned to net {word} for the day "
                f"(running total {_hiro_money(a.get('cum'))}) · spot {a.get('spot', 0):g}")
```

In `hiro.py`:

```python
def symbol_view(rows, sigma, cfg):
    """The small per-symbol summary published to cache:options:hiro."""
    last = rows[-1]
    w = window_sum(rows, last["ts"], int(cfg["window_min"]) * 60)
    return {"ts": last["ts"], "spot": last["spot"], "impact": last["impact"],
            "cum": sum(r["impact"] for r in rows), "window_impact": w["impact"],
            "sigma": sigma,
            "mult": (abs(w["impact"]) / sigma) if sigma else None,
            "unclassified_share": w["unclassified_share"]}
```

**Step 4:** run both files → PASS.

**Step 5:** Commit: `feat(options): HIRO alert text, push gate and view row`

---

### Task 9: Wire HIRO into `run_flow_alerts`

**Files:** Modify `services/options_svc/handlers.py` (constants ~L544; new helpers beside
`_run_gamma_flip` ~L2068; `run_flow_alerts` ~L2164 and ~L2212); Test `test_handlers.py`

**Step 1: Failing tests** (append to `test_handlers.py`; model the setup on
`test_run_flow_alerts_gamma_flip_baseline_then_transition` at ~L2241):

```python
def _hiro_setup(monkeypatch, today_rows, prior, cfg_over=None):
    from shared.bus import Bus
    from services.options_svc import handlers, flow_alerts
    import gex_history_db as gh
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers, "_flow_alert_symbols", lambda: ["SPY"])
    monkeypatch.setattr(handlers, "_load_flow_series_for", lambda conn, sym, limit: [])
    monkeypatch.setattr(handlers.compute, "take_uoa_stash", lambda: {})
    monkeypatch.setattr(handlers.compute, "take_big_delta_stash", lambda: {})
    monkeypatch.setattr(handlers, "_load_spot_flip_for", lambda conn, sym: None)
    monkeypatch.setattr(handlers, "_today_ct", lambda: "2026-10-01")

    class _FakeConn:
        def close(self): pass
    monkeypatch.setattr(gh, "connect", lambda **k: _FakeConn())
    monkeypatch.setattr(handlers, "_load_hiro_today", lambda conn, sym: today_rows)
    monkeypatch.setattr(handlers, "_load_hiro_prior", lambda conn, sym, n: prior)
    real = flow_alerts.load_thresholds()
    hcfg = {**real["hiro"], "symbols": ["SPY"], "min_notional": 1.0,
            "flip_not_before": "00:00", **(cfg_over or {})}
    monkeypatch.setattr(handlers.flow_alerts, "load_thresholds",
                        lambda: {**real, "hiro": hcfg})
    sent = []
    monkeypatch.setattr(handlers.push_notify, "send_flow_alert",
                        lambda a, **k: sent.append(a))
    return bus, handlers, sent


def _hrows(impacts, t0=1_790_863_200):   # 2026-10-01 09:00 CT (verified)
    return [{"ts": t0 + 60 * i, "spot": 500.0, "impact": float(x),
             "classified_vol": 10.0, "unclassified_vol": 0.0}
            for i, x in enumerate(impacts)]


def test_run_flow_alerts_hiro_surge_screen_only_and_view(monkeypatch):
    prior = [_hrows([1e6] * 30)] * 5                     # normal 15-min = 15e6
    today = _hrows([0] * 15 + [5e6] * 15)                # last 15 min = 75e6 = 5x
    bus, handlers, sent = _hiro_setup(monkeypatch, today, prior)
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: today[-1]["ts"])
    handlers.run_flow_alerts(bus)
    alerts = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    hs = [a for a in alerts if a["type"] == "hiro_surge"]
    assert len(hs) == 1 and hs[0]["side"] == "dealers_buying"
    assert hs[0]["id"] == f"SPY|hiro_surge|dealers_buying|{today[-1]['ts']}"
    assert not [a for a in sent if a["type"] == "hiro_surge"]     # push=false
    view = bus.cache_get("cache:options:hiro").payload
    assert view["date"] == "2026-10-01" and view["symbols"]["SPY"]["sigma"] > 0
    # next minute, still surging: cooldown holds it
    handlers.run_flow_alerts(bus)
    alerts = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    assert len([a for a in alerts if a["type"] == "hiro_surge"]) == 1


def test_run_flow_alerts_hiro_pushes_above_push_k_when_push_on(monkeypatch):
    prior = [_hrows([1e6] * 30)] * 5
    today = _hrows([0] * 15 + [5e6] * 15)
    bus, handlers, sent = _hiro_setup(monkeypatch, today, prior,
                                      {"push": True, "push_k": 4.0})
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: today[-1]["ts"])
    handlers.run_flow_alerts(bus)
    assert [a["type"] for a in sent] == ["hiro_surge"]


def test_run_flow_alerts_hiro_flip_once(monkeypatch):
    prior = [_hrows([1e6] * 30)] * 5                     # sigma 15e6, band 15e6
    today = _hrows([20e6, -40e6])                        # cum 20e6 -> -20e6
    bus, handlers, sent = _hiro_setup(monkeypatch, today, prior)
    monkeypatch.setattr(handlers, "_flow_now_ts", lambda: today[-1]["ts"])
    handlers.run_flow_alerts(bus)
    handlers.run_flow_alerts(bus)
    alerts = bus.cache_get("cache:options:flow_alerts").payload["alerts"]
    hf = [a for a in alerts if a["type"] == "hiro_flip"]
    assert len(hf) == 1 and hf[0]["side"] == "to_selling"


def test_run_flow_alerts_hiro_disabled_does_nothing(monkeypatch):
    prior = [_hrows([1e6] * 30)] * 5
    today = _hrows([0] * 15 + [5e6] * 15)
    bus, handlers, sent = _hiro_setup(monkeypatch, today, prior, {"enabled": False})
    handlers.run_flow_alerts(bus)
    env = bus.cache_get("cache:options:flow_alerts")
    assert env is None or not any(a["type"].startswith("hiro") for a in env.payload["alerts"])
    assert bus.cache_get("cache:options:hiro") is None
```

⚠ Check the `t0` constant: compute
`int(datetime(2026,10,1,9,0,tzinfo=ZoneInfo("America/Chicago")).timestamp())` and
correct the literal if it differs. Also check whether `run_flow_alerts`'s
`_flow_window_open()` gate is patched open by `services/options_svc/tests/conftest.py`;
if not, add `monkeypatch.setattr(handlers, "_flow_window_open", lambda *a, **k: True)`
to `_hiro_setup`.

**Step 2:** run `"$PY" -m pytest services/options_svc/tests/test_handlers.py -k hiro -q` → FAIL.

**Step 3: Implement.** Constants beside `CACHE_FLOW_ALERTS`:

```python
CACHE_HIRO = "cache:options:hiro"
EVENT_HIRO = "events:options:hiro"
```

Helpers beside `_run_gamma_flip`:

```python
def _load_hiro_today(conn, symbol):
    try:
        import gex_history_db as gh
        return gh.load_hiro_day(conn, symbol)
    except Exception:
        log.debug("load_hiro_day degraded for %s", symbol, exc_info=True)
        return []


def _load_hiro_prior(conn, symbol, n):
    try:
        import gex_history_db as gh
        return gh.load_hiro_prior_sessions(conn, symbol, n)
    except Exception:
        log.debug("load_hiro_prior_sessions degraded for %s", symbol, exc_info=True)
        return []


def _run_hiro(conn, cfg, bus, today, cooldowns, now_ts):
    """HIRO-model Surge + Reversal for the [hiro] symbols; publishes the small
    cache:options:hiro view. Best-effort -> []. Design:
    docs/plans/2026-10-01-hiro-alert-design.md."""
    from services.options_svc import hiro
    h = cfg.get("hiro", {})
    if not h.get("enabled", True) or conn is None:
        return []
    out, view = [], {}
    not_before = hiro.ct_ts(today, h.get("flip_not_before", "09:00"))
    for sym in h.get("symbols") or []:
        rows = _load_hiro_today(conn, sym)
        if not rows:
            continue
        sigma = hiro.baseline_sigma(_load_hiro_prior(conn, sym, h["baseline_sessions"]),
                                    rows, h)
        a = hiro.detect_surge(sym, rows, sigma, h)
        if a:
            key = f"{sym}|hiro_surge|{a['side']}"
            if not flow_alerts._on_cooldown(cooldowns, key, now_ts, h["cooldown_min"] * 60):
                cooldowns[key] = now_ts
                a["id"] = f"{key}|{int(a['ts'])}"
                a["text"] = flow_alerts.alert_text(a)
                out.append(a)
        if h.get("flip_enabled", True):
            seen_key = f"{sym}|hiro_flip_seen"
            f = hiro.detect_flip(sym, rows, sigma, h, not_before, cooldowns.get(seen_key))
            if f:
                cooldowns[seen_key] = f["ts"]       # seen once, fired or not
                key = f"{sym}|hiro_flip"
                if not flow_alerts._on_cooldown(cooldowns, key, now_ts,
                                                h["flip_cooldown_min"] * 60):
                    cooldowns[key] = now_ts
                    f["id"] = f"{sym}|hiro_flip|{f['side']}|{int(f['ts'])}"
                    f["text"] = flow_alerts.alert_text(f)
                    out.append(f)
        view[sym] = hiro.symbol_view(rows, sigma, h)
    bus.cache_set(CACHE_HIRO, {"date": today, "symbols": view},
                  event=EVENT_HIRO, skip_unchanged=True)
    return out
```

In `run_flow_alerts`, after the `_run_gamma_flip` line (~L2164):

```python
            # HIRO-model hedging flow (Surge + Reversal) - same open read-only conn.
            fresh.extend(_run_hiro(conn, cfg, bus, today, cooldowns, now_ts))
```

And replace the push-gate loop body's first check (~L2213) with:

```python
                if a.get("type") == "big_delta" and not flow_alerts.big_delta_should_push(a, cfg):
                    continue
                if (a.get("type") in flow_alerts.HIRO_TYPES
                        and not flow_alerts.hiro_should_push(a, cfg)):
                    continue
```

**Step 4:** run the hiro tests → PASS; then the whole service suite → baseline set.
⚠ If an existing `run_flow_alerts` test now fails, it is almost certainly because the
real `[hiro]` config is enabled and the test's conn is real-but-refused (conftest guard)
→ `_run_hiro` gets `conn is None` and returns `[]`. If a failure is anything else,
stop and investigate — do not edit the old test.

**Step 5:** Commit: `feat(options): HIRO surge + reversal in run_flow_alerts`

---

### Task 10: Push routing category `flow_hiro`

**Files:**
- Modify: `services/options_svc/push_notify.py:259-278`
- Modify: `shared/notify/channels.py:198-202` (`ROUTE_CATEGORIES`)
- Modify: `config/notify.toml` (after `[channels.flow_gamma_flip]`)
- Modify: `webgui/config_schema.py:~1214` (category label map)
- Test: `services/options_svc/tests/test_push_notify.py` (find the existing
  `flow_category` tests and add beside them)

**Step 1: Failing test:**

```python
def test_flow_category_routes_both_hiro_types_to_flow_hiro():
    assert push_notify.flow_category({"type": "hiro_surge"}) == "flow_hiro"
    assert push_notify.flow_category({"type": "hiro_flip"}) == "flow_hiro"


def test_hiro_bullishness_follows_dealer_direction():
    assert push_notify._flow_is_bullish({"type": "hiro_surge", "side": "dealers_buying"})
    assert not push_notify._flow_is_bullish({"type": "hiro_surge", "side": "dealers_selling"})
    assert push_notify._flow_is_bullish({"type": "hiro_flip", "side": "to_buying"})
```

**Step 2:** run → FAIL.

**Step 3: Implement.** `_FLOW_CATEGORIES` gains `"hiro_surge": "flow_hiro",
"hiro_flip": "flow_hiro"`. `_flow_is_bullish` gains:

```python
           (a.get("type") == "hiro_surge" and a.get("side") == "dealers_buying") or \
           (a.get("type") == "hiro_flip" and a.get("side") == "to_buying")
```

`ROUTE_CATEGORIES` gains `"flow_hiro"` after `"flow_gamma_flip"`. `config/notify.toml`:

```toml
[channels.flow_hiro]           # HIRO-model hedging surge / reversal
discord = true
telegram = true
```

`config_schema.py`'s category label map gains `"flow_hiro": "Hedging flow"`.

**Step 4:** run push_notify tests, `"$PY" -m pytest shared/tests -q -rfs`, and
`(cd webgui && "$PY" -m pytest tests/test_config_schema.py tests/test_config_editor.py -q)`.
A test enumerating `ROUTE_CATEGORIES` against `notify.toml` or the label map may need
the new entry — that is an ADDITION to its expected set, which is allowed; changing any
other expectation is not.

**Step 5:** Commit: `feat(notify): flow_hiro push category`

---

### Task 11: Flow Alerts screen, chime exclusion, spoken phrases

**Files:**
- Modify: `webgui/pages/options/flow.py:75-111` (labels, tones) and `alert_detail` (~L168)
- Modify: `webgui/alerts.py:234-254` (quiet types)
- Modify: `webgui/voice.py:538-541` (`_ALL_CAUSES`)
- Test: `webgui/tests/test_flow_page.py`, `webgui/tests/test_alerts.py`

**Step 1: Failing tests.** `test_flow_page.py`:

```python
_HS = {"type": "hiro_surge", "side": "dealers_buying", "symbol": "$SPX", "ts": 1,
       "spot": 5712.5, "impact": 2.4e9, "mult": 3.6, "window_min": 15,
       "unclassified_share": 0.18, "id": "x1", "text": "t"}
_HF = {"type": "hiro_flip", "side": "to_selling", "symbol": "SPY", "ts": 1,
       "spot": 571.2, "cum": -3.1e8, "id": "x2", "text": "t"}


def test_hiro_labels_and_tones():
    assert flow.alert_kind_label(_HS) == "Hedging surge"
    assert flow.alert_kind_label(_HF) == "Hedging reversal"
    assert flow.side_label(_HS) == "Dealers buying"
    assert flow.side_label(_HF) == "Now selling"
    assert flow.tone_class(_HS) == "text-emerald-400"
    assert flow.tone_class(_HF) == "text-rose-400"


def test_hiro_detail_cells():
    assert flow.alert_detail(_HS) == "$2.40B in 15 min · 3.6× normal · 18% unlabelled"
    assert flow.alert_detail(_HF) == "running total -$310.00M · spot 571.2"
    assert flow.alert_detail({"type": "hiro_surge"}) == ""
```

Update the existing set-equality test at ~L315 by ADDING the two new keys to its
expected set (`"hiro_surge", "hiro_flip"`) — that is the one sanctioned edit to an
existing assertion in this task, because the set is meant to equal `_KIND_LABEL`.

`test_alerts.py`:

```python
def test_new_flow_alerts_excludes_hiro_from_chime():
    view = {"alerts": [{"id": "a", "type": "uoa"}, {"id": "b", "type": "hiro_surge"},
                       {"id": "c", "type": "hiro_flip"}]}
    new, acked = alerts.new_flow_alerts(view, set())
    assert [a["id"] for a in new] == ["a"] and acked == {"a", "b", "c"}
```

**Step 2:** run → FAIL.

**Step 3: Implement.** `flow.py`:
- `_KIND_LABEL` gains `"hiro_surge": "Hedging surge", "hiro_flip": "Hedging reversal"`
  (noun phrases — see the comment above `_KIND_LABEL`).
- `_SIDE_LABEL` gains `"dealers_buying": "Dealers buying", "dealers_selling":
  "Dealers selling", "to_buying": "Now buying", "to_selling": "Now selling"`.
- `_TONE` gains `("hiro_surge","dealers_buying"): _TONE_POS, ("hiro_surge",
  "dealers_selling"): _TONE_NEG, ("hiro_flip","to_buying"): _TONE_POS,
  ("hiro_flip","to_selling"): _TONE_NEG` — dealers buying stock is upward pressure.
- A page-local money helper (Tier 1 cannot import the service's):

```python
def _hiro_money(v):
    """Signed dollars with a B form ($SPX hedging runs to billions). '' if unusable."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    sign = "-" if v < 0 else ""
    if abs(v) >= 999_500_000:
        return f"{sign}${abs(v)/1e9:.2f}B"
    return sign + _money(abs(v))
```

- In `alert_detail`, before the `except`:

```python
        if t == "hiro_surge":
            if d.get("impact") is None or d.get("mult") is None:
                return ""
            return (f"{_hiro_money(abs(float(d['impact'])))} in "
                    f"{int(d.get('window_min') or 15)} min · {float(d['mult']):.1f}× normal · "
                    f"{float(d.get('unclassified_share') or 0):.0%} unlabelled")
        if t == "hiro_flip":
            if d.get("cum") is None or d.get("spot") is None:
                return ""
            return f"running total {_hiro_money(d['cum'])} · spot {float(d['spot']):g}"
```

`alerts.py`: replace the `big_delta` literal with a module constant
`_QUIET_FLOW_TYPES = ("big_delta", "hiro_surge", "hiro_flip")` and
`if a.get("type") in _QUIET_FLOW_TYPES:`; extend the docstring ("HIRO is quiet-live
until its daily report justifies a push").

`voice.py`: `_ALL_CAUSES` gains `("Hedging surge", "Dealers buying"), ("Hedging surge",
"Dealers selling"), ("Hedging reversal", "Now buying"), ("Hedging reversal", "Now
selling")`. Update its comment's "eight" / "four types" wording to match.

**Step 4:** `(cd webgui && "$PY" -m pytest . -q -rfs)` → baseline set. Pay attention to
`test_voice.py` (the `_ALL_CAUSES` ↔ `flow._TONE` cross-check) and `test_desk.py`.

**Step 5:** Commit: `feat(webgui): HIRO alerts on the Flow Alerts screen`

---

### Task 12: Settings → Configuration entries

**Files:** Modify `webgui/config_schema.py` (inside `_FLOW`, after the "Big delta"
Section ~L375); Test: `webgui/tests/test_config_schema.py` (already fails on a key
missing from the catalogue — that is the failing test)

**Step 1:** `(cd webgui && "$PY" -m pytest tests/test_config_schema.py -q)` → FAIL
naming the `hiro.*` keys (proves the guard sees them).

**Step 2: Implement:**

```python
        Section("Hedging flow (HIRO model)",
                "A model of dealer hedging built from the 1-minute chain poll. "
                "Schwab has no trade tape, so each contract gets one buy/sell label "
                "per minute.", (
            Field("hiro.enabled", "Hedging-flow alerts on", "", kind="bool"),
            Field("hiro.push", "Send hedging-flow pushes to the phone",
                  "Off = Flow screen only.", kind="bool"),
            Field("hiro.symbols", "Symbols watched", "", kind="symbols"),
            Field("hiro.window_min", "Surge window", "", kind="int", unit="min",
                  min=5, max=60, step=1),
            Field("hiro.k", "Surge at", "Multiples of the symbol's normal window size.",
                  kind="float", unit="× normal", min=1, max=20, step=0.5),
            Field("hiro.push_k", "Push surges at", "A separate, higher bar for the phone.",
                  kind="float", unit="× normal", min=1, max=20, step=0.5),
            Field("hiro.min_notional", "…and at least", "Ignores a dead tape.",
                  kind="money", min=0, max=1e11, step=1000000),
            _pct("hiro.max_unclassified", "Skip when unlabelled volume exceeds",
                 "Trades at the exact midpoint get no buy/sell label.", hi=100, step=5),
            Field("hiro.cooldown_min", "Surge quiet period", "Per symbol and direction.",
                  kind="int", unit="min", min=0, max=1440, step=5),
            Field("hiro.baseline_sessions", "Normal size from the last", "",
                  kind="int", unit="sessions", min=1, max=20, step=1),
            Field("hiro.min_minutes", "Until then, wait for", "Minutes of today's data.",
                  kind="int", unit="min", min=15, max=390, step=5),
            Field("hiro.flip_enabled", "Reversal alerts on", "", kind="bool"),
            Field("hiro.flip_band", "Reversal dead zone", "Multiples of normal window size.",
                  kind="float", unit="× normal", min=0, max=10, step=0.25),
            Field("hiro.flip_not_before", "No reversals before", "Central time.",
                  kind="time"),
            Field("hiro.flip_cooldown_min", "Reversal quiet period", "",
                  kind="int", unit="min", min=0, max=1440, step=5),
            Field("hiro.keep_sessions", "Keep minute history for", "",
                  kind="int", unit="sessions", min=6, max=120, step=1),
        )),
```

Check `_pct`'s scaling convention against `big_delta.rel_threshold` (stored 0.25, shown
25%) — `max_unclassified` is stored as 0.5, so it must use `_pct` the same way.

**Step 3:** re-run → PASS; whole webgui suite → baseline.

**Step 4:** Commit: `feat(settings): HIRO configuration entries`

---

### Task 13: Daily validation report + timer

**Files:**
- Create: `tools/hiro_report.py`
- Test: `tools/tests/test_hiro_report.py`
- Modify: `shared/market_calendar.py` slot defaults (~L290, beside `"flow_delta"`)
- Modify: `config/sessions.toml` (add `[slots.hiro_report]` after `[slots.flow_delta]`)
- Modify: `deploy/systemd/generate_units.py` (`_hiro_report_units`, registered ~L995)
- Modify: `webgui/config_schema.py` if the sessions catalogue lists slots individually
  (`test_config_schema.py` will say)

The report recomputes everything from `hiro_minutes` (no Redis, no Schwab), so a missed
day can be re-run later — unlike the flow-delta report.

**Step 1: Failing tests** (`tools/tests/test_hiro_report.py`):

```python
from tools import hiro_report as hr


def _rows(impacts, spots, t0=0):
    return [{"ts": t0 + 60 * i, "spot": s, "impact": float(x),
             "classified_vol": 10.0, "unclassified_vol": 0.0}
            for i, (x, s) in enumerate(zip(impacts, spots))]


CFG = {"window_min": 15, "k": 3.0, "min_notional": 1.0, "max_unclassified": 0.5,
       "cooldown_min": 30}


def test_replay_surges_honours_cooldown_and_k():
    rows = _rows([0] * 15 + [5e6] * 40, [100.0] * 55)
    fires = hr.replay_surges(rows, sigma=15e6, cfg=CFG, k=3.0)
    assert len(fires) == 2                    # cooldown 30 min across 40 surging minutes
    assert all(f["side"] == "dealers_buying" for f in fires)


def test_forward_return_signed_by_direction():
    rows = _rows([0] * 20, [100.0] * 5 + [101.0] * 15)
    fire = {"ts": rows[4]["ts"], "spot": 100.0, "side": "dealers_selling"}
    assert hr.forward_return(rows, fire, minutes=5) == pytest.approx(-0.01)
    assert hr.forward_return(rows, {**fire, "ts": rows[-1]["ts"]}, 5) is None


def test_build_report_lists_k_table_and_hit_rate():
    md = hr.build_report("2026-10-01", {"SPY": {"fires_by_k": {3.0: 4, 4.0: 1},
                         "unclassified": 0.2, "fwd5": [0.001, -0.002], "fwd15": [0.003]}})
    assert "| SPY |" in md and "3.0" in md and "Hit rate" in md
```

(add `import pytest`.)

**Step 2:** run `"$PY" -m pytest tools/tests/test_hiro_report.py -q` → FAIL.

**Step 3: Implement** `tools/hiro_report.py` — reuse `hiro.window_sum`,
`hiro.baseline_sigma`; never re-implement a rule:

```python
"""Daily HIRO-model validation report (docs/plans/2026-10-01-hiro-alert-design.md).

Recomputes from gex_history.db's hiro_minutes only -- no Redis, no Schwab -- so a
missed day can be re-run. Answers: how many surges per symbol at each k, how much
volume went unlabelled, and whether a surge was followed by price moving its way
over 5 and 15 minutes. A push stays OFF until this shows an edge.

    .venv/bin/python -X utf8 tools/hiro_report.py [--date YYYY-MM-DD] [--force]
"""
import argparse
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "options-scanner"))

from services.options_svc import flow_alerts, hiro  # noqa: E402
from shared.market_calendar import is_trading_day  # noqa: E402

K_GRID = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)
OUT_ROOT = ROOT / "options-scanner" / "data" / "hiro_report"


def replay_surges(rows, sigma, cfg, k):
    """Every surge the live rule would have fired at bar ``k``, with its cooldown."""
    fires, last = [], {}
    c = {**cfg, "k": k}
    for i in range(len(rows)):
        a = hiro.detect_surge("_", rows[: i + 1], sigma, c)
        if not a:
            continue
        prev = last.get(a["side"])
        if prev is not None and a["ts"] - prev < cfg["cooldown_min"] * 60:
            continue
        last[a["side"]] = a["ts"]
        fires.append(a)
    return fires


def forward_return(rows, fire, minutes):
    """Spot return ``minutes`` after a fire, signed so + means price moved the
    way the dealers' hedging pushed. None when the session ends first."""
    target = fire["ts"] + minutes * 60
    later = [r for r in rows if r["ts"] >= target and r.get("spot")]
    if not later or not fire.get("spot"):
        return None
    ret = later[0]["spot"] / fire["spot"] - 1.0
    return ret if fire["side"] == "dealers_buying" else -ret


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def build_report(day, per_symbol):
    lines = [f"# HIRO-model report — {day}", "",
             "A model of dealer hedging; see the design doc for what it cannot see.", "",
             "| Symbol | " + " | ".join(f"k {k}" for k in K_GRID) +
             " | Unlabelled | Hit rate 5m | Mean 5m | Hit rate 15m | Mean 15m |",
             "|---" * (len(K_GRID) + 6) + "|"]
    for sym, s in per_symbol.items():
        def hit(xs):
            return f"{sum(1 for x in xs if x > 0) / len(xs):.0%} (n={len(xs)})" if xs else "—"
        def mean(xs):
            m = _mean(xs)
            return f"{m:+.3%}" if m is not None else "—"
        ks = " | ".join(str(s["fires_by_k"].get(k, 0)) for k in K_GRID)
        unl = s.get("unclassified")
        lines.append(f"| {sym} | {ks} | {unl:.0%} | {hit(s['fwd5'])} | {mean(s['fwd5'])} | "
                     f"{hit(s['fwd15'])} | {mean(s['fwd15'])} |"
                     if unl is not None else
                     f"| {sym} | {ks} | — | {hit(s['fwd5'])} | {mean(s['fwd5'])} | "
                     f"{hit(s['fwd15'])} | {mean(s['fwd15'])} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--date")
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    if not a.force and not is_trading_day(day):
        print(f"{day} is not a trading day -- nothing to measure")
        return 0
    import gex_history_db as gh
    cfg = flow_alerts.load_thresholds()["hiro"]
    conn = gh.connect(read_only=True)
    per = {}
    try:
        for sym in cfg["symbols"]:
            rows = gh.load_hiro_day(conn, sym, day)
            if not rows:
                continue
            prior = gh.load_hiro_prior_sessions(conn, sym, cfg["baseline_sessions"], before=day)
            sigma = hiro.baseline_sigma(prior, rows, cfg)
            fires = replay_surges(rows, sigma, cfg, cfg["k"]) if sigma else []
            cls = sum(r["classified_vol"] for r in rows)
            unl = sum(r["unclassified_vol"] for r in rows)
            per[sym] = {
                "fires_by_k": {k: len(replay_surges(rows, sigma, cfg, k)) if sigma else 0
                               for k in K_GRID},
                "unclassified": (unl / (cls + unl)) if (cls + unl) else None,
                "fwd5": [x for x in (forward_return(rows, f, 5) for f in fires) if x is not None],
                "fwd15": [x for x in (forward_return(rows, f, 15) for f in fires) if x is not None],
            }
    finally:
        conn.close()
    out = OUT_ROOT / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(build_report(day.isoformat(), per), encoding="utf-8")
    print(f"wrote {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

⚠ `replay_surges` is O(n²) in rows; 390 rows × 6 k × 4 symbols is a few seconds —
measure it once on a full day's rows and say so in the commit if it is slower.

Slot + timer: add `"hiro_report": {"at": "16:10"}` to `market_calendar`'s slot defaults
and `[slots.hiro_report]` with `at = "16:10"` and a comment to `config/sessions.toml`.
In `generate_units.py`, copy `_flow_delta_units` into `_hiro_report_units` with
`ExecStart={_python()} -X utf8 tools/hiro_report.py`, `slot_times("hiro_report")`,
description "HIRO-model report", and **no** `EnvironmentFile` dependency comment (it
reads SQLite only). Register it in the `render_all` dict. Check
`deploy/systemd/tests` for a test listing every timer and ADD the new one to its
expected set.

**Step 4:** run `tools/tests`, `deploy`, `shared/tests`, and the webgui config tests →
baseline sets.

**Step 5:** Commit: `feat(tools): daily HIRO-model validation report + timer`

---

### Task 14: Documentation

**Files:**
- `webgui/page_help.py` — the Flow Alerts page's guide: two new alert kinds, what
  "Dealers buying" means, that it is a model, screen-only.
- `docs/manuals/user-guide/*.md` — Flow Alerts section.
- `docs/manuals/technical-reference/*.md` — the formula, quote rule, σ as RMS, Surge and
  Reversal rules, every `[hiro]` key, the 7-day/one-label-per-minute limits.
- `docs/manuals/options-glossary/*.md` — "HIRO (hedging impact)" entry.
- `docs/webgui-routes.md` — `/options/flow` gains the two kinds; `cache:options:hiro`.
- `docs/CHANGELOG.md` — dated entry: what shipped, quiet-live, report, commit SHAs.
- Rebuild the manuals: `"$PY" docs/manuals/build_docs.py` (check the README for the
  exact command).

Locate each section with Grep (`big_delta`, `Outsized bet`, `Flow Alerts`) and follow
the existing wording style: whole words, plain English, no "enqueued"-style mechanism
talk. Run `(cd webgui && "$PY" -m pytest . -q -rfs)` afterwards — `page_help` and the
manuals both have tests.

Commit: `docs: HIRO hedging-flow alerts`

---

### Task 15: Full verification, then ship

**Step 1:** Re-run every command in Task 0 and diff each against its baseline file.
The failing and skipped SETS must match. Also run the narrow type check:
`"$PY" -m pyright` → clean.

**Step 2:** Request a code review (superpowers:requesting-code-review) over the branch.

**Step 3:** Merge to `main` and push (see memory "Branch + dev/prod workflow"). Promote
**after 15:25 CT** on a trading day unless told otherwise (promote restarts the whole
target and loses GEX minutes):

```bash
ssh vps2 'cd /home/administrator/dev && tools/promote.sh'
```

**Step 4: Verify on prod during the next regular session** (no dev stack exists — this
is additive and screen-only, which is why prod verification is acceptable):

```bash
ssh vps2-ts 'cd /home/administrator/dev && .venv/bin/python -c "
import sys; sys.path[:0]=[\".\", \"options-scanner\"]
import gex_history_db as gh
c = gh.connect(read_only=True)
for s in (\"\$SPX\",\"SPY\",\"QQQ\",\"IWM\"):
    r = gh.load_hiro_day(c, s); print(s, len(r), r[-1] if r else None)"'
```

Expect a growing row count per symbol and an `unclassified_vol` share well under half.
Then read `cache:options:hiro` through `shared.bus.Bus().cache_get(...)`.payload and
confirm each symbol has `sigma` (from the 2nd session onward, or after 30 minutes on the
first). Check the Flow Alerts screen's Type filter lists "Hedging surge" and "Hedging
reversal". Check `journalctl --user -u trading-prod-options_svc` for
`options.hiro_*` degrade warnings.

**Step 5:** After the first full session, run
`tools/hiro_report.py --date <that day>` on prod and read the report. Note in the
CHANGELOG what it showed. Pushes stay off until several sessions show surges leading
price.
