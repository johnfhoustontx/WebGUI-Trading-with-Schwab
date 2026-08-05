# Net Premium Groups View — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a **Net Prem** subtab to Dealer Positioning (`/options/gamma`) that plots intraday net options premium (call$ − put$) as one fixed-color line per symbol, for any combination of symbols drawn from three groups (Indices & Broad, SPDR Sectors, Mega-caps).

**Architecture:** A pure group/series module (`services/options_svc/net_premium.py`) + a DB-only orchestrator (`compute.build_net_premium`) publish all 28 symbols' series to `cache:options:net_premium` on the **existing** 1-min GEX branch — which already reads these exact rows for the Opportunity Board, so marginal cost is ~zero. The webgui filters client-side, so checkbox toggles are instant. The 11 SPDR sectors must be **added to the GEX collection universe** first; they are not collected today.

**Tech Stack:** Python 3.11, pytest, NiceGUI + Highcharts (`ui.highchart`), Redis/Memurai via `shared/bus`, SQLite (`gex_history.db`).

**Design doc:** [`docs/plans/2026-08-05-net-premium-groups-view-design.md`](2026-08-05-net-premium-groups-view-design.md)

---

## Orientation for the implementing engineer

Read this once before Task 1. It will save you an hour.

**Three tiers, strictly separated.** Tier 1 is `webgui/` (NiceGUI pages) and may import ONLY `nicegui`, `bus_client`, `shared.contracts` — **never** an engine or a service module. Tier 2 is `services/options_svc/` (imports engines, does the computing, publishes to Redis). Tier 3 is Redis + the on-disk SQLite DBs. If you find yourself importing `services.*` from `webgui/`, stop — you've taken a wrong turn.

**Where things live:**
- Repo root: `D:\WebGUI Trading with Schwab\.claude\worktrees\eloquent-bun-b7b5ef` (a git worktree).
- Python: `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe` — the worktree has **no** venv of its own. Every command below uses this interpreter.
- The GEX history DB is `options-scanner/gex_history.db` (1.5 GB, live). Never write to it from a test.

**Test commands** (run from the worktree root unless stated):
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```
```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q
```
```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest shared/contracts -q
```
Never run `pytest services` across all services at once — multiple hyphenated app dirs land on `sys.path` together and collide on top-level module names (`config`, `scoring`, `src`). One service folder at a time.

**Baselines before you start** (record these; a pre-existing failure is not yours to fix): options_svc has 2 known date-relative `test_expected_move` failures. options-scanner carries ~17 known failures.

**The live DB is NOT in this worktree.** `gex_history.db` is gitignored; the real 1.5 GB file is in the MAIN repo at `D:\WebGUI Trading with Schwab\options-scanner\gex_history.db`. Unit tests never touch it (they use fakes). Task 10 explains how to verify against it safely.

**Reference numbers from a live prototype** (run 2026-08-05 mid-session, so you can tell "working" from "plausible"): 296 rows per symbol; `$SPX −$244.3M / −10.8%`, `SPY −$375.2M / −46.6%`, `QQQ −$275.1M / −42.1%`, `IWM +$1.8M / +5.2%`, `DIA +$0.1M / +2.5%`, `BIG10 +$431.4M / +27.2%`. BIG10's row count matched its members' exactly — the per-minute timestamps align across symbols.

**House rules that this feature touches:**
- Anything riding the 1-min GEX branch goes in its **own** `try/except` — a failure there must never break GEX collection.
- Tailwind-first: no `.style()` or inline `style=` in `webgui/pages`. Dynamic colors map from a **finite set** to fixed classes. `webgui/tests/test_no_inline_style.py` enforces it.
- Commit after every green test run.

---

## Task 1: Pure group model (`net_premium.GROUPS`)

**Files:**
- Create: `services/options_svc/net_premium.py`
- Create: `services/options_svc/tests/test_net_premium.py`

**Step 1: Write the failing test**

Create `services/options_svc/tests/test_net_premium.py`:

```python
"""Pure group model + series builder for the Dealer Positioning Net Prem view."""
from services.options_svc import net_premium as np_mod


def test_three_groups_in_display_order():
    keys = [g["key"] for g in np_mod.GROUPS]
    assert keys == ["indices", "sectors", "megacaps"]


def test_group_membership_matches_the_spec():
    by_key = {g["key"]: list(g["symbols"]) for g in np_mod.GROUPS}
    assert by_key["indices"] == ["$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA"]
    assert by_key["sectors"] == ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                                 "XLP", "XLRE", "XLU", "XLV", "XLY"]
    assert by_key["megacaps"] == ["NVDA", "AVGO", "AAPL", "META", "MSFT",
                                  "TSLA", "PLTR", "AMZN", "GOOGL", "AMD"]


def test_big10_basket_matches_market_dashboard_membership():
    # Must stay identical to market_svc/symbols.py's BIG10 basket, or "BIG10"
    # would mean two different things on two pages.
    assert set(np_mod.BASKETS["BIG10"]) == {
        "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA",
        "AVGO", "PLTR", "AMD"}


def test_display_symbols_are_every_group_member_deduped_in_order():
    out = np_mod.display_symbols()
    assert out[0] == "$SPX"
    assert "BIG10" in out
    assert len(out) == len(set(out)) == 7 + 11 + 10


def test_source_symbols_drop_baskets_and_add_their_members():
    out = np_mod.source_symbols()
    assert "BIG10" not in out          # not a real ticker — nothing to read
    assert "NVDA" in out               # a BIG10 member, and its own group entry
    assert len(out) == len(set(out))   # deduped
```

**Step 2: Run it to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'services.options_svc.net_premium'`.

**Step 3: Write the implementation**

Create `services/options_svc/net_premium.py`:

```python
"""Group model + intraday net-premium series for the Dealer Positioning
"Net Prem" view.

PURE — stdlib only, no I/O, no engine imports. ``compute.build_net_premium``
does the DB reading and hands the raw rows here.

Net premium = cumulative call premium ($) − cumulative put premium ($) for a
symbol, per intraday snapshot. Because Schwab serves no time-&-sales tape the
premium is UNSIGNED cumulative traded dollars, so this is a money-weighted
put/call read, NOT net buying. The UI says so.
"""
from __future__ import annotations

# The three selectable groups, in display order. This is the single source of
# truth for membership + ordering — the page builds its tabs from it.
GROUPS = (
    {"key": "indices", "label": "Indices & Broad",
     "symbols": ("$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA")},
    {"key": "sectors", "label": "SPDR Sectors",
     "symbols": ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                 "XLP", "XLRE", "XLU", "XLV", "XLY")},
    {"key": "megacaps", "label": "Mega-caps",
     "symbols": ("NVDA", "AVGO", "AAPL", "META", "MSFT",
                 "TSLA", "PLTR", "AMZN", "GOOGL", "AMD")},
)

# Composite pseudo-symbols: summed server-side from real tickers. Membership is
# identical to market_svc/symbols.py's BIG10 basket by design — a test pins it.
BASKETS = {
    "BIG10": ("NVDA", "MSFT", "GOOGL", "AMZN", "META",
              "AAPL", "TSLA", "AVGO", "PLTR", "AMD"),
}


def display_symbols() -> list:
    """Every symbol the view can plot, in group order (includes baskets)."""
    out: list = []
    for group in GROUPS:
        for sym in group["symbols"]:
            if sym not in out:
                out.append(sym)
    return out


def source_symbols() -> list:
    """The REAL tickers to read from gex_history: display symbols minus the
    baskets, plus every basket's members (deduped, order-preserving)."""
    out: list = []
    for sym in display_symbols():
        members = BASKETS.get(sym)
        for real in (members if members else (sym,)):
            if real not in out:
                out.append(real)
    return out
```

**Step 4: Run the tests to verify they pass**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: 5 passed.

**Step 5: Commit**

```bash
git add services/options_svc/net_premium.py services/options_svc/tests/test_net_premium.py
git commit -m "feat(options): pure group model for the Net Prem view"
```

---

## Task 2: Collect the SPDR sectors

The sectors are in **neither** the index base nor `Top 20.xlsx`, so today they have **zero** premium history. This task fixes that — and pins it with a drift test so a future watchlist edit cannot silently empty a group.

**Why the non-sector additions too:** `Top 20.xlsx` is **gitignored**. Relying on it for a named feature group means a fresh clone (or an edit to the workbook) silently empties Mega-caps. `collection_symbols()` dedupes, so on a machine that already has the watchlist this adds **zero** extra chain fetches for those names. This is the same reasoning the file already documents for `$NDX`.

**Files:**
- Modify: `options-scanner/gex_collector.py:36`
- Modify: `services/options_svc/tests/test_net_premium.py`

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_net_premium.py`:

```python
def test_every_source_symbol_is_actually_collected():
    """Drift guard: a group symbol that isn't in the GEX collection universe has
    no premium history, so its line would be permanently empty. gex_collector is
    reachable because importing compute puts OPTIONS_SCANNER on sys.path."""
    from services.options_svc import compute  # noqa: F401  (sys.path side effect)
    import gex_collector

    collected = set(gex_collector.collection_symbols())
    missing = [s for s in np_mod.source_symbols() if s not in collected]
    assert not missing, f"not collected: {missing}"
```

**Step 2: Run it to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py::test_every_source_symbol_is_actually_collected -q
```
Expected: FAIL listing the 11 sectors, e.g. `AssertionError: not collected: ['XLB', 'XLC', 'XLE', ...]`.

**Step 3: Write the implementation**

In `options-scanner/gex_collector.py`, replace line 36:

```python
SYMBOLS = ["$SPX", "$VIX", "SPY", "QQQ", "$NDX"]
```

with:

```python
# The base universe. Everything the Dealer Positioning "Net Prem" view groups
# lives HERE rather than being left to `Top 20.xlsx` — that workbook is
# GITIGNORED, so a fresh clone (or an edit to it) would silently empty a named
# group in the UI. Same reasoning as the $NDX note above; collection_symbols()
# dedupes, so on a machine that already lists these in the watchlist this costs
# no extra chain fetches.
SYMBOLS = [
    "$SPX", "$VIX", "SPY", "QQQ", "$NDX", "IWM", "DIA",
    # SPDR sectors — NEW 2026-08-05. These were collected by nothing before, so
    # they are the only genuinely additional fetches here (~+11/poll).
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    # BIG10 mega-caps (also the Net Prem "Mega-caps" group).
    "NVDA", "MSFT", "GOOGL", "AMZN", "META", "AAPL", "TSLA", "AVGO", "PLTR", "AMD",
]
```

**Step 4: Run the tests to verify they pass**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: 6 passed.

Then confirm you did not break the collector's own suite:
```bash
cd options-scanner && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_gex_collector.py -q
```
Expected: PASS (or the same count as before your change — check the baseline).

**Step 5: Commit**

```bash
git add options-scanner/gex_collector.py services/options_svc/tests/test_net_premium.py
git commit -m "feat(gex): collect the 11 SPDR sectors + pin the Net Prem universe"
```

> **Cost note to carry into the CLAUDE.md entry:** ~+4,300 Schwab chain fetches/day (~+14% on GEX collection, the stack's #1 caller). The sectors also start appearing on the Opportunity Board and in the Dealer Positioning symbol dropdown — both additive and expected.

---

## Task 3: `build_series` — raw DB rows → per-symbol series

**Files:**
- Modify: `services/options_svc/net_premium.py`
- Modify: `services/options_svc/tests/test_net_premium.py`

Input is the raw tuple shape `gex_history_db.load_flow_series` returns:
`(ts, spot, call_vol, put_vol, call_prem, put_prem)` — premium at indices **4 and 5**, and **forward-only** (`None` on rows written before the columns existed).

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_net_premium.py`:

```python
def _row(ts, call, put):
    """A gex_history flow row: (ts, spot, call_vol, put_vol, call_prem, put_prem)."""
    return (ts, 100.0, 0, 0, call, put)


def test_build_series_projects_ts_call_put():
    out = np_mod.build_series({"SPY": [_row(1, 10.0, 4.0), _row(2, 20.0, 5.0)]})
    assert out["SPY"] == [[1, 10.0, 4.0], [2, 20.0, 5.0]]


def test_build_series_skips_rows_with_no_premium_at_all():
    out = np_mod.build_series({"SPY": [_row(1, None, None), _row(2, 20.0, 5.0)]})
    assert out["SPY"] == [[2, 20.0, 5.0]]


def test_build_series_treats_one_missing_side_as_zero():
    out = np_mod.build_series({"SPY": [_row(1, 10.0, None)]})
    assert out["SPY"] == [[1, 10.0, 0.0]]


def test_build_series_sums_basket_members_by_timestamp():
    out = np_mod.build_series({
        "NVDA": [_row(1, 10.0, 1.0), _row(2, 30.0, 2.0)],
        "MSFT": [_row(1, 5.0, 3.0), _row(2, 7.0, 4.0)],
    })
    assert out["BIG10"] == [[1, 15.0, 4.0], [2, 37.0, 6.0]]


def test_basket_tolerates_partially_reported_timestamps():
    # A member missing a snapshot contributes nothing at that ts rather than
    # dropping the whole column.
    out = np_mod.build_series({
        "NVDA": [_row(1, 10.0, 1.0), _row(2, 30.0, 2.0)],
        "MSFT": [_row(1, 5.0, 3.0)],
    })
    assert out["BIG10"] == [[1, 15.0, 4.0], [2, 30.0, 2.0]]


def test_basket_absent_when_no_member_has_data():
    out = np_mod.build_series({"NVDA": []})
    assert "BIG10" not in out


def test_symbols_with_no_rows_are_omitted():
    out = np_mod.build_series({"XLK": [], "SPY": [_row(1, 1.0, 1.0)]})
    assert "XLK" not in out and "SPY" in out


def test_build_series_never_raises_on_junk():
    out = np_mod.build_series({"SPY": [("x",), None, _row(1, 1.0, 1.0)]})
    assert out["SPY"] == [[1, 1.0, 1.0]]
```

**Step 2: Run to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: FAIL — `AttributeError: module ... has no attribute 'build_series'`.

**Step 3: Write the implementation**

Append to `services/options_svc/net_premium.py`:

```python
def _num(value):
    """A finite number, or None. Rejects bools (``True`` is an int in Python)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _project(rows):
    """``[(ts, spot, cvol, pvol, cprem, pprem), …]`` → ``[[ts, call, put], …]``.

    Rows carrying NO premium on either side are skipped (premium is forward-only
    — legacy rows predate the columns), so a series simply starts where premium
    began collecting. One missing side counts as 0.0. Junk rows are skipped, not
    raised on."""
    out = []
    for row in rows or []:
        try:
            ts, call, put = _num(row[0]), _num(row[4]), _num(row[5])
        except (TypeError, IndexError):
            continue
        if ts is None or (call is None and put is None):
            continue
        out.append([ts, call or 0.0, put or 0.0])
    out.sort(key=lambda r: r[0])
    return out


def build_series(flow_by_symbol) -> dict:
    """``{symbol: [flow rows]}`` → ``{symbol: [[ts, call_prem, put_prem], …]}``.

    Symbols with no usable rows are OMITTED (the page names them as
    "no data yet" rather than drawing an empty line). Baskets are summed from
    their members aligned on ``ts``; a member missing a snapshot contributes
    nothing at that timestamp rather than dropping the column. Dollar sum is the
    only sensible aggregation for money, so a basket's skew is
    ``Σnet ÷ Σ(call+put)`` — the same dollar-weighted convention
    ``market_svc.symbol_premium_skew`` uses."""
    flow_by_symbol = flow_by_symbol or {}
    out = {}
    for sym, rows in flow_by_symbol.items():
        projected = _project(rows)
        if projected:
            out[sym] = projected

    for basket, members in BASKETS.items():
        totals: dict = {}
        for member in members:
            for ts, call, put in out.get(member, ()):
                acc = totals.setdefault(ts, [0.0, 0.0])
                acc[0] += call
                acc[1] += put
        if totals:
            out[basket] = [[ts, c, p] for ts, (c, p) in sorted(totals.items())]
    return out
```

**Step 4: Run the tests to verify they pass**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: 14 passed.

**Step 5: Commit**

```bash
git add services/options_svc/net_premium.py services/options_svc/tests/test_net_premium.py
git commit -m "feat(options): build_series for net premium incl. the BIG10 basket"
```

---

## Task 4: `NetPremiumSnapshot` contract

**Files:**
- Modify: `shared/contracts/options.py` (append after `MatrixSnapshot`, ~line 113)
- Modify: `shared/contracts/tests/test_options.py`

**Step 1: Write the failing test**

Append to `shared/contracts/tests/test_options.py` (match the import style already used at the top of that file):

```python
def test_net_premium_snapshot_validates_envelope():
    from shared.contracts.options import NetPremiumSnapshot

    snap = NetPremiumSnapshot(
        session_date="2026-08-05",
        series={"SPY": [[1, 10.0, 4.0]]},
    )
    assert snap.series["SPY"][0][2] == 4.0
    assert snap.error is None


def test_net_premium_snapshot_defaults_every_field():
    """A payload cached before a field existed must still validate — Redis keeps
    cache:options:net_premium across a service restart."""
    from shared.contracts.options import NetPremiumSnapshot

    snap = NetPremiumSnapshot()
    assert snap.series == {} and snap.session_date is None
```

**Step 2: Run to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest shared/contracts -q
```
Expected: FAIL — `ImportError: cannot import name 'NetPremiumSnapshot'`.

**Step 3: Write the implementation**

Append to `shared/contracts/options.py`:

```python
class NetPremiumSnapshot(_Base):
    """cache:options:net_premium — intraday net-premium series per symbol for the
    Dealer Positioning "Net Prem" view.

    ``series`` maps a display symbol (including the composite ``BIG10``) to
    ``[[ts, call_prem, put_prem], …]``, RTH-cropped, for ``session_date``. Like
    the other view models this validates only the envelope shape. Every field
    carries a default so a payload cached before a field existed still validates.
    """
    session_date: str | None = None    # gex session date the series cover
    ts: str | None = None
    series: dict = {}                  # {symbol: [[ts, call_prem, put_prem], …]}
    error: str | None = None
```

**Step 4: Run to verify it passes**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest shared/contracts -q
```
Expected: all pass (2 more than the baseline).

**Step 5: Commit**

```bash
git add shared/contracts/options.py shared/contracts/tests/test_options.py
git commit -m "feat(contracts): NetPremiumSnapshot for cache:options:net_premium"
```

---

## Task 5: `compute.build_net_premium`

DB-only orchestration. Mirrors `build_matrix` (`services/options_svc/compute.py:2232`) — one reused read-only connection, ALWAYS closed, per-symbol failures degrade to an empty blob.

**Files:**
- Modify: `services/options_svc/compute.py` (add after `build_matrix`, ~line 2296)
- Modify: `services/options_svc/tests/test_net_premium.py`

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_net_premium.py`:

```python
import datetime

import pytest

from services.options_svc import compute


class _FakeGH:
    """Stands in for gex_history_db: records the symbols asked for."""

    def __init__(self, data, fail_for=()):
        self.data = data
        self.fail_for = set(fail_for)
        self.asked = []
        self.closed = False

    def connect(self, read_only=False):
        gh = self

        class _Conn:
            def close(self):
                gh.closed = True

        return _Conn()

    def load_flow_series(self, conn, symbol, d=None):
        self.asked.append(symbol)
        if symbol in self.fail_for:
            raise RuntimeError("boom")
        return self.data.get(symbol, [])


@pytest.fixture
def _pin_session(monkeypatch):
    """Pin the display session date so RTH bounds are deterministic."""
    monkeypatch.setattr(compute, "_display_session_date",
                        lambda now, sd: datetime.date(2026, 8, 5))
    return datetime.date(2026, 8, 5)


def _rth_ts(hour, minute):
    """Unix ts for a CT wall-clock time on the pinned session date."""
    return datetime.datetime(2026, 8, 5, hour, minute,
                             tzinfo=compute._PROJ_CT_TZ).timestamp()


def test_build_net_premium_reads_every_source_symbol(monkeypatch, _pin_session):
    gh = _FakeGH({"SPY": [(_rth_ts(10, 0), 1.0, 0, 0, 10.0, 4.0)]})
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(_pin_session)

    assert set(gh.asked) == set(np_mod.source_symbols())
    assert out["series"]["SPY"] == [[_rth_ts(10, 0), 10.0, 4.0]]
    assert out["session_date"] == "2026-08-05"
    assert out["error"] is None
    assert gh.closed is True


def test_build_net_premium_crops_to_rth(monkeypatch, _pin_session):
    gh = _FakeGH({"SPY": [
        (_rth_ts(8, 5), 1.0, 0, 0, 1.0, 1.0),    # pre-open — dropped
        (_rth_ts(10, 0), 1.0, 0, 0, 10.0, 4.0),  # RTH — kept
        (_rth_ts(15, 10), 1.0, 0, 0, 9.0, 9.0),  # post-close — dropped
    ]})
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(_pin_session)
    assert [r[0] for r in out["series"]["SPY"]] == [_rth_ts(10, 0)]


def test_one_symbol_read_failure_does_not_sink_the_build(monkeypatch, _pin_session):
    gh = _FakeGH({"SPY": [(_rth_ts(10, 0), 1.0, 0, 0, 10.0, 4.0)]},
                 fail_for=["XLK"])
    monkeypatch.setattr(compute, "_matrix_gh", lambda: gh)

    out = compute.build_net_premium(_pin_session)
    assert "SPY" in out["series"] and "XLK" not in out["series"]
    assert out["error"] is None


def test_db_unavailable_degrades_to_empty_series(monkeypatch, _pin_session):
    def _boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(compute, "_matrix_gh", _boom)

    out = compute.build_net_premium(_pin_session)
    assert out["series"] == {} and out["error"]
```

**Step 2: Run to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: FAIL — `AttributeError: module 'services.options_svc.compute' has no attribute 'build_net_premium'`.

**Step 3: Write the implementation**

In `services/options_svc/compute.py`, immediately after `build_matrix` ends (before `def build_gamma_read`):

```python
def build_net_premium(session_date, now=None):
    """Assemble the ``cache:options:net_premium`` payload.

    Intraday net-premium series for every symbol the Dealer Positioning "Net Prem"
    view can plot (``net_premium.source_symbols()``), RTH-cropped to the displayed
    session — the same window the heatmap and Flow views use, so the three time
    axes agree. One reused read-only connection, ALWAYS closed. No proxy calls.

    Rides the 1-min GEX branch, which has ALREADY read these same rows for
    ``build_matrix``; this is a second cheap indexed read over the same open DB,
    not new data collection.

    Fully defensive: a DB-connect failure degrades to an empty-series payload with
    ``error`` set; a per-symbol read failure yields no series for that symbol
    (the page names it as "no data yet")."""
    from services.options_svc import net_premium as np_mod

    if now is None:
        import datetime as _dtmod
        now = _dtmod.datetime.now(_PROJ_CT_TZ)
    # The time-axis charts show RTH only, so before the 08:30 open fall back to
    # the prior session — today has collected rows but none displayable.
    display_date = _display_session_date(now, session_date)
    bounds = _rth_bounds(display_date)
    date_key = (display_date.isoformat()
                if hasattr(display_date, "isoformat") else display_date)

    try:
        gh = _matrix_gh()
        conn = gh.connect(read_only=True)
    except Exception:
        log.debug("build_net_premium: DB unavailable", exc_info=True)
        return {"session_date": date_key, "ts": _now_iso(), "series": {},
                "error": "net premium unavailable"}

    flow: dict = {}
    try:
        for sym in np_mod.source_symbols():
            try:
                flow[sym] = _rth_only(
                    gh.load_flow_series(conn, sym, display_date), bounds)
            except Exception:
                log.debug("build_net_premium: read failed for %s", sym,
                          exc_info=True)
                flow[sym] = []
    finally:
        try:
            conn.close()
        except Exception:
            log.debug("build_net_premium conn close failed", exc_info=True)

    try:
        series = np_mod.build_series(flow)
    except Exception:
        log.debug("build_net_premium: build failed", exc_info=True)
        return {"session_date": date_key, "ts": _now_iso(), "series": {},
                "error": "net premium build failed"}
    return {"session_date": date_key, "ts": _now_iso(), "series": series,
            "error": None}
```

**Step 4: Run to verify it passes**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_net_premium.py -q
```
Expected: 18 passed.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_net_premium.py
git commit -m "feat(options): build_net_premium — RTH-cropped series for all groups"
```

---

## Task 6: Publish on the 1-min GEX branch

**Files:**
- Modify: `services/options_svc/handlers.py` (keys ~line 232; `collect_gex_history` ~line 731; new fn after `publish_matrix` ~line 785)
- Modify: `services/options_svc/tests/test_handlers.py`

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_handlers.py`. That file uses a **real fakeredis bus** (`Bus(fake=True)`, already imported at the top) rather than a hand-rolled fake — keep to that:

```python
def test_publish_net_premium_caches_the_view(monkeypatch):
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "build_net_premium",
                        lambda session_date, **kw: {"series": {"SPY": [[1, 2.0, 1.0]]},
                                                    "session_date": "2026-08-05",
                                                    "ts": "t", "error": None})

    handlers.publish_net_premium(bus)

    env = bus.cache_get("cache:options:net_premium")
    assert env is not None
    assert env.payload["series"]["SPY"] == [[1, 2.0, 1.0]]


def test_publish_net_premium_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(handlers.compute, "build_net_premium", _boom)
    handlers.publish_net_premium(Bus(fake=True))      # must not raise


def test_collect_gex_history_publishes_net_premium_guarded(monkeypatch):
    """A net-premium failure must not break GEX collection or the other publishes."""
    calls = []
    monkeypatch.setattr(handlers.compute, "collect_gex_snapshots",
                        lambda **kw: calls.append("collect"))
    monkeypatch.setattr(handlers, "publish_flow_skew", lambda bus: None)
    monkeypatch.setattr(handlers, "run_flow_alerts", lambda bus: None)
    monkeypatch.setattr(handlers, "publish_matrix", lambda bus: calls.append("matrix"))
    monkeypatch.setattr(handlers, "_current_gamma_symbol", lambda bus: "$SPX")

    def _boom(bus):
        calls.append("netprem")
        raise RuntimeError("boom")

    monkeypatch.setattr(handlers, "publish_net_premium", _boom)

    handlers.collect_gex_history(Bus(fake=True))      # must not raise

    assert calls == ["collect", "matrix", "netprem"]
```

**Step 2: Run to verify it fails**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc/tests/test_handlers.py -q -k net_premium
```
Expected: FAIL — `AttributeError: ... has no attribute 'publish_net_premium'`.

**Step 3: Write the implementation**

(a) Add the cache keys next to `CACHE_MATRIX` (`services/options_svc/handlers.py:231`):

```python
CACHE_NET_PREMIUM = "cache:options:net_premium"
EVENT_NET_PREMIUM = "events:options:net_premium"
```

(b) Add the publisher immediately after `publish_matrix`:

```python
def publish_net_premium(bus) -> None:
    """Assemble the Net Prem view and publish it to the bus.

    Rides the 1-min GEX tick right after publish_matrix, over the rows
    ``collect_gex_snapshots`` just wrote. ``compute.build_net_premium`` is fully
    defensive (a DB failure degrades to empty series). ``skip_unchanged`` so an
    unchanged payload doesn't wake GUI version-pollers. Guarded so a net-premium
    failure never escapes into the caller — the 1-min GEX collection and the
    other publishes must be unaffected."""
    try:
        # scheduler imports handlers at its module top — import lazily to avoid
        # a module-top import cycle (same as publish_matrix).
        from services.options_svc import scheduler
        view = compute.build_net_premium(scheduler.active_session_date())
        bus.cache_set(CACHE_NET_PREMIUM, view, event=EVENT_NET_PREMIUM,
                      skip_unchanged=True)
    except Exception:
        log.exception("publish_net_premium degraded")
```

(c) Wire it into `collect_gex_history` — append after the `publish_matrix` block (~line 734):

```python
        try:
            publish_net_premium(bus)
        except Exception:
            log.exception("publish_net_premium after collect degraded")
```

**Step 4: Run to verify it passes**

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```
Expected: all pass except the 2 known `test_expected_move` date-relative failures.

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_handlers.py
git commit -m "feat(options): publish cache:options:net_premium on the 1-min GEX branch"
```

---

## Task 7: Webgui pure builders (colors, figure, summary)

Tier 1. Everything here is a **pure function** operating on the cached payload — no NiceGUI widgets, so it is fully unit-testable.

**Files:**
- Modify: `webgui/pages/options/gamma.py` (add after `flow_summary_text`, ~line 895)
- Modify: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing test**

Append to `webgui/tests/test_options_gamma.py`:

```python
# --- Net Prem view ----------------------------------------------------------

def _np_series():
    return {
        "$SPX": [[1, 100.0, 1000.0], [2, 200.0, 2000.0]],
        "SPY":  [[1, 50.0, 10.0],    [2, 60.0, 20.0]],
    }


def test_net_prem_colors_cover_every_group_symbol():
    """A symbol without a color would fall back to grey and be unidentifiable."""
    groups = gamma.NET_PREM_GROUPS
    for group in groups:
        for sym in group["symbols"]:
            assert gamma.NET_PREM_COLORS.get(sym), f"no color for {sym}"


def test_net_prem_colors_are_distinct():
    used = [gamma.NET_PREM_COLORS[s] for g in gamma.NET_PREM_GROUPS
            for s in g["symbols"]]
    assert len(used) == len(set(used))


def test_symbol_color_is_stable_across_selections():
    """The whole point of the feature: a symbol keeps its color no matter what
    else is plotted."""
    one = gamma.net_prem_figure(_np_series(), ["SPY"])
    both = gamma.net_prem_figure(_np_series(), ["$SPX", "SPY"])
    spy_one = [s for s in one["series"] if s["name"] == "SPY"][0]
    spy_both = [s for s in both["series"] if s["name"] == "SPY"][0]
    assert spy_one["color"] == spy_both["color"] == gamma.NET_PREM_COLORS["SPY"]


def test_dollars_mode_is_net_in_millions():
    fig = gamma.net_prem_figure(_np_series(), ["SPY"], mode="dollars")
    series = fig["series"][0]
    assert series["data"] == [[0, (50.0 - 10.0) / 1e6], [1, (60.0 - 20.0) / 1e6]]
    assert "$M" in fig["yAxis"]["title"]["text"]


def test_skew_mode_is_signed_percent():
    fig = gamma.net_prem_figure(_np_series(), ["$SPX"], mode="skew")
    # (100 - 1000) / 1100 * 100 = -81.81...
    assert fig["series"][0]["data"][0][1] == pytest.approx(-81.8181, abs=1e-3)
    assert "%" in fig["yAxis"]["title"]["text"]


def test_x_axis_is_the_union_of_timestamps_across_selected_symbols():
    series = {"A": [[1, 1.0, 0.0]], "B": [[2, 1.0, 0.0]]}
    fig = gamma.net_prem_figure(series, ["A", "B"])
    assert len(fig["xAxis"]["categories"]) == 2
    # Each symbol's point sits at its own timestamp's column, not at index 0.
    by_name = {s["name"]: s["data"] for s in fig["series"]}
    assert by_name["A"][0][0] == 0 and by_name["B"][0][0] == 1


def test_selected_symbol_with_no_data_yields_no_series():
    fig = gamma.net_prem_figure(_np_series(), ["SPY", "XLK"])
    assert [s["name"] for s in fig["series"]] == ["SPY"]


def test_net_prem_missing_names_the_empty_symbols():
    assert gamma.net_prem_missing(_np_series(), ["SPY", "XLK", "XLU"]) == ["XLK", "XLU"]


def test_net_prem_figure_with_no_selection_is_empty_not_broken():
    fig = gamma.net_prem_figure(_np_series(), [])
    assert fig["series"] == [] and fig["xAxis"]["categories"] == []


def test_net_prem_summary_reports_the_extremes():
    text = gamma.net_prem_summary_text(_np_series(), ["$SPX", "SPY"], "dollars")
    assert "2 symbols" in text
    assert "SPY" in text and "$SPX" in text


def test_net_prem_summary_prompts_when_nothing_selected():
    assert "Select" in gamma.net_prem_summary_text(_np_series(), [], "dollars")


def test_net_prem_summary_handles_no_data_at_all():
    assert gamma.net_prem_summary_text({}, ["SPY"], "dollars")
```

Add `import pytest` at the top of the test file if it is not already imported.

**Step 2: Run to verify it fails**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_gamma.py -q -k net_prem
```
Expected: FAIL — `AttributeError: module 'pages.options.gamma' has no attribute 'NET_PREM_COLORS'`.

**Step 3: Write the implementation**

Append to `webgui/pages/options/gamma.py` after `flow_summary_text`:

```python
# --- Net Prem view ----------------------------------------------------------
# Intraday net premium (call$ − put$) for a SELECTION of symbols, grouped. The
# groups mirror services/options_svc/net_premium.GROUPS — Tier 1 may not import a
# service module, so the labels are duplicated here and pinned by a test in the
# service suite (test_every_source_symbol_is_actually_collected) plus the shape
# test below. Order matters: it is the tab order.
NET_PREM_GROUPS = (
    {"key": "indices", "label": "Indices & Broad",
     "symbols": ("$SPX", "$NDX", "BIG10", "SPY", "QQQ", "IWM", "DIA")},
    {"key": "sectors", "label": "SPDR Sectors",
     "symbols": ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                 "XLP", "XLRE", "XLU", "XLV", "XLY")},
    {"key": "megacaps", "label": "Mega-caps",
     "symbols": ("NVDA", "AVGO", "AAPL", "META", "MSFT",
                 "TSLA", "PLTR", "AMZN", "GOOGL", "AMD")},
)

# A FIXED color per symbol: a symbol keeps its identity no matter which others
# are selected. Hues are spread WITHIN each group so an all-sectors or
# all-mega-caps selection stays distinguishable; all are chosen to read on the
# dark navy background.
NET_PREM_COLORS = {
    # Indices & Broad
    "$SPX": "#f5f5f5", "$NDX": "#8ab4ff", "BIG10": "#ffd166", "SPY": "#4dd0e1",
    "QQQ": "#b388ff", "IWM": "#ff8a65", "DIA": "#9ccc65",
    # SPDR sectors
    "XLB": "#a1887f", "XLC": "#4fc3f7", "XLE": "#ffb74d", "XLF": "#66bb6a",
    "XLI": "#90a4ae", "XLK": "#7986cb", "XLP": "#f06292", "XLRE": "#26a69a",
    "XLU": "#dce775", "XLV": "#ce93d8", "XLY": "#ff8a80",
    # Mega-caps
    "NVDA": "#76ff03", "AVGO": "#ff5252", "AAPL": "#eeeeee", "META": "#448aff",
    "MSFT": "#00e5ff", "TSLA": "#ff4081", "PLTR": "#ffab40", "AMZN": "#ffd54f",
    "GOOGL": "#69f0ae", "AMD": "#e57373",
}
NET_PREM_FALLBACK = "#9e9e9e"
NET_PREM_MODES = {"dollars": "Dollars ($M)", "skew": "Skew %"}


def net_prem_symbols():
    """Every plottable symbol, in group order."""
    return [s for g in NET_PREM_GROUPS for s in g["symbols"]]


def net_prem_value(row, mode):
    """Net premium for one ``[ts, call, put]`` row.

    ``dollars`` → (call − put) in $M. ``skew`` → (call − put) / (call + put) as a
    signed percent in [-100, 100], which puts every symbol on one comparable axis
    regardless of size. None when the row has nothing to report."""
    call = row[1] if isinstance(row[1], (int, float)) else 0.0
    put = row[2] if isinstance(row[2], (int, float)) else 0.0
    if mode == "skew":
        total = call + put
        return None if total <= 0 else (call - put) / total * 100.0
    return (call - put) / 1e6


def net_prem_missing(series, symbols):
    """Selected symbols with no rows — named in the UI rather than silently
    dropped (every sector reads this way until collection starts)."""
    series = series or {}
    return [s for s in symbols or [] if not series.get(s)]


def net_prem_figure(series, symbols, mode="dollars", height=680):
    """Intraday net-premium chart: one colored line per selected symbol.

    ``series`` = the ``cache:options:net_premium`` payload's ``series`` map
    ({symbol: [[ts, call_prem, put_prem], …]}). The x-axis is the SORTED UNION of
    timestamps across the selection, as categories — the same gap-packing trick
    ``flow_figure`` uses, so a symbol that started collecting late still lines up
    on the shared axis. Tooltip is deliberately NOT shared: with 20+ series a
    shared tooltip is an unreadable wall."""
    series = series or {}
    symbols = [s for s in (symbols or []) if series.get(s)]

    stamps = sorted({row[0] for s in symbols for row in series[s]})
    index = {ts: i for i, ts in enumerate(stamps)}

    plots = []
    for sym in symbols:
        points = []
        for row in series[sym]:
            value = net_prem_value(row, mode)
            if value is not None:
                points.append([index[row[0]], value])
        plots.append({
            "type": "line", "name": sym, "data": points,
            "color": NET_PREM_COLORS.get(sym, NET_PREM_FALLBACK),
            "lineWidth": 2, "marker": {"enabled": False},
        })

    unit = "Net premium (%)" if mode == "skew" else "Net premium ($M)"
    fig = _base_chart("line", height)
    fig["chart"]["marginBottom"] = 64
    fig["legend"] = {"enabled": True, "itemStyle": {"color": FONT},
                     "itemHoverStyle": {"color": "#ffffff"}}
    fig.update({
        "title": {"text": "Intraday net premium (call − put)",
                  "style": {"color": FONT}},
        "xAxis": {**_dark_axis("Time"),
                  "categories": [_fmt_ts(ts) for ts in stamps],
                  "labels": {"rotation": -45, "style": {"color": FONT}}},
        "yAxis": {**_dark_axis(unit),
                  "plotLines": [{"value": 0, "color": "#777777", "width": 1,
                                 "zIndex": 3}]},
        "tooltip": {"shared": False, "backgroundColor": "#222222",
                    "borderColor": "#444444",
                    "style": {"color": FONT, "fontSize": "11px"},
                    "valueDecimals": 2,
                    "valueSuffix": "%" if mode == "skew" else "M"},
        "series": plots,
    })
    return fig


def net_prem_summary_text(series, symbols, mode="dollars"):
    """One-line status: how many symbols, and the day's extremes."""
    series = series or {}
    symbols = list(symbols or [])
    if not symbols:
        return "Select one or more symbols to plot net premium."
    live = [s for s in symbols if series.get(s)]
    if not live:
        return ("No net-premium data yet for the selected symbols "
                "(collected going forward).")

    lasts = []
    for sym in live:
        value = net_prem_value(series[sym][-1], mode)
        if value is not None:
            lasts.append((sym, value))
    if not lasts:
        return f"{len(live)} symbols selected · no premium accrued yet."

    lasts.sort(key=lambda pair: pair[1], reverse=True)
    top, bottom = lasts[0], lasts[-1]

    def _fmt(pair):
        sym, value = pair
        return (f"{sym} {value:+,.1f}%" if mode == "skew"
                else f"{sym} ${value:+,.1f}M")

    parts = [f"{len(live)} symbols",
             f"most call-led: {_fmt(top)}",
             f"most put-led: {_fmt(bottom)}"]
    missing = net_prem_missing(series, symbols)
    if missing:
        parts.append(f"no data yet: {', '.join(missing)}")
    return " · ".join(parts)
```

**Step 4: Run to verify it passes**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_gamma.py -q
```
Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): pure builders for the Net Prem view"
```

---

## Task 8: Persist the view's settings

**Files:**
- Modify: `webgui/app_settings.py:22` (after `gamma_spot_interval`)
- Modify: `webgui/tests/test_app_settings.py`

**Step 1: Write the failing test**

Append to `webgui/tests/test_app_settings.py`:

```python
def test_net_prem_defaults_exist():
    import app_settings

    assert app_settings.DEFAULTS["gamma_netprem_group"] == "indices"
    assert app_settings.DEFAULTS["gamma_netprem_mode"] == "dollars"
    # The three whose magnitudes are actually comparable in Dollars mode.
    assert app_settings.DEFAULTS["gamma_netprem_symbols"] == ["$SPX", "SPY", "QQQ"]
```

**Step 2: Run to verify it fails**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_app_settings.py -q
```
Expected: FAIL — `KeyError: 'gamma_netprem_group'`.

**Step 3: Write the implementation**

In `webgui/app_settings.py`, add to `DEFAULTS` after `gamma_spot_interval`:

```python
    "gamma_netprem_group": "indices",          # Net Prem picker: which group tab
    "gamma_netprem_mode": "dollars",           # Net Prem y-axis: dollars | skew
    "gamma_netprem_symbols": ["$SPX", "SPY", "QQQ"],   # plotted symbols
```

**Step 4: Run to verify it passes**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_app_settings.py -q
```
Expected: all pass.

**Step 5: Commit**

```bash
git add webgui/app_settings.py webgui/tests/test_app_settings.py
git commit -m "feat(webgui): persist the Net Prem group/mode/selection"
```

---

## Task 9: Wire the view into the page

This is the only task touching NiceGUI widgets. Work inside `render()` in `webgui/pages/options/gamma.py`.

**Files:**
- Modify: `webgui/pages/options/gamma.py` (`render()`, lines ~1000–1630)
- Modify: `webgui/tests/test_options_gamma.py`

**Step 1: Write the failing test**

The widget wiring itself is verified live (Task 10); what a unit test CAN pin is that the view is registered and rendered before the snapshot guard. Append to `webgui/tests/test_options_gamma.py`:

```python
def test_net_prem_is_a_registered_view():
    import inspect

    source = inspect.getsource(gamma.render)
    assert '"Net Prem"' in source


def test_net_prem_renders_before_the_no_snapshot_guard():
    """The view is symbol-independent — it must paint even when no gamma
    snapshot has been cached yet, so its branch has to come BEFORE the
    `if not snap:` early-return in _render_view."""
    import inspect

    source = inspect.getsource(gamma.render)
    branch = source.index('view_toggle.value == "Net Prem"')
    guard = source.index("if not snap:")
    assert branch < guard


def test_net_prem_cache_view_is_polled():
    import inspect

    assert "options:net_premium" in inspect.getsource(gamma.render)
```

**Step 2: Run to verify it fails**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_options_gamma.py -q -k net_prem
```
Expected: 3 FAIL on the substring assertions.

**Step 3: Write the implementation**

**(a) Register the tab.** In `_build_view_tabs` (~line 1028) change:

```python
            for v in list(_VIEWS) + ["Flow", "Term"]:
```
to:
```python
            # Net Prem sits beside Flow — they are the two options-flow views.
            for v in list(_VIEWS) + ["Flow", "Net Prem", "Term"]:
```

**(b) Extend `state` and `seen`** (~line 1010):

```python
    state: dict = {"snap": None, "countdown": 120, "fetching": False,
                   "netprem": None,
                   "netprem_sel": list(app_settings.get("gamma_netprem_symbols") or [])}
    seen = {"gamma": None, "explain": None, "analyze": None, "status": None,
            "netprem": None}
```

**(c) Add the controls row.** Immediately after the existing header `ui.row()` block closes (after the `analyze_btn` / briefings row, ~line 1075), add:

```python
    # Net Prem controls — their own row, shown ONLY on that view (the other views
    # would be cluttered by 28 checkboxes). The group tab FILTERS the checkbox
    # list; the selection persists across tabs, so any cross-group combination
    # works.
    with ui.column().classes("w-full gap-1") as netprem_row:
        with ui.row().classes("items-center gap-3 flex-wrap w-full"):
            netprem_tabs = ui.tabs(
                value=app_settings.get("gamma_netprem_group") or "indices"
            ).classes("compact-subtabs").props("dense no-caps inline-label align=left")
            with netprem_tabs:
                for _g in NET_PREM_GROUPS:
                    ui.tab(_g["key"], label=_g["label"])
            netprem_mode = ui.select(
                dict(NET_PREM_MODES),
                value=app_settings.get("gamma_netprem_mode") or "dollars",
                label="Scale").props("dense options-dense").classes("w-36")
            netprem_mode.tooltip(
                "Dollars = net premium in $M (honest magnitude; big names "
                "dominate). Skew % = net ÷ (call+put), so every symbol is "
                "comparable regardless of size.")
            ui.space()
            netprem_count = ui.label("").classes(f"text-xs {MUTED}")
            netprem_clear = ui.button("Clear all", color=None).props(
                "no-caps dense flat").classes(BTN)
        netprem_boxes = ui.row().classes("items-center gap-3 flex-wrap w-full")
```

Import `MUTED` alongside the existing `BTN` / `BTN_PRIMARY` import from `pages.options.theme` at the top of the file if it is not already imported.

**(d) Build the checkboxes + selection helpers.** Add these functions inside `render()`, before `_render_view`:

```python
    def _netprem_group_symbols():
        key = netprem_tabs.value
        for group in NET_PREM_GROUPS:
            if group["key"] == key:
                return group["symbols"]
        return NET_PREM_GROUPS[0]["symbols"]

    def _sync_netprem_count():
        netprem_count.text = f"Selected: {len(state['netprem_sel'])}"

    @guard
    def _on_netprem_toggle(sym, checked):
        sel = [s for s in state["netprem_sel"] if s != sym]
        if checked:
            # Keep group order so the legend order is stable.
            order = net_prem_symbols()
            sel = sorted(sel + [sym], key=order.index)
        state["netprem_sel"] = sel
        app_settings.set("gamma_netprem_symbols", sel)
        _sync_netprem_count()
        _render_view()

    def _build_netprem_boxes():
        """Rebuild the checkbox row for the active group tab. Each label is tinted
        in that symbol's line color, so the picker doubles as the legend."""
        netprem_boxes.clear()
        with netprem_boxes:
            for sym in _netprem_group_symbols():
                box = ui.checkbox(sym, value=sym in state["netprem_sel"],
                                  on_change=lambda e, s=sym: _on_netprem_toggle(s, e.value))
                box.props("dense")
                box.classes(f"text-xs text-[{NET_PREM_COLORS.get(sym, NET_PREM_FALLBACK)}]")
        _sync_netprem_count()
```

> The `text-[#hex]` arbitrary class is built from `NET_PREM_COLORS`, a **fixed
> 28-entry map** — this is the documented "map dynamic values to a finite
> palette" pattern, not a runtime-computed color. No `.style()`.

**(e) Render the view.** In `_render_view`, insert this as the **first** statement of the function body (before `snap = state["snap"]` and before the `if not snap:` guard):

```python
        if view_toggle.value == "Net Prem":
            # Symbol-independent: this view reads its OWN cache key, so it must
            # paint even when no gamma snapshot has been cached yet.
            chart_msg.set_visibility(False)
            payload = state["netprem"] or {}
            series = payload.get("series") or {}
            sel = state["netprem_sel"]
            mode = netprem_mode.value or "dollars"
            _set_chart(net_prem_figure(series, sel, mode=mode))
            state["chart_el"].set_visibility(True)
            heat_plot.set_visibility(False)
            heat_msg.set_visibility(False)
            _apply_flex(0, term=True)
            _set_summary(net_prem_summary_text(series, sel, mode))
            return
```

**(f) Show/hide the controls row.** Extend the existing `_sync_spot_controls` (~line 1580) to also govern the Net Prem row, and call it on every view change:

```python
    def _sync_spot_controls():
        # Bar size is meaningless for a line — hide it rather than leave a control
        # that silently does nothing. Likewise the Net Prem picker belongs only to
        # its own view.
        spot_int_sel.set_visibility(spot_style_sel.value != "line")
        netprem_row.set_visibility(view_toggle.value == "Net Prem")
```

and change the tab handler:

```python
    view_toggle.on_value_change(lambda e: (_sync_spot_controls(), _render_view()))
```

**(g) Wire the remaining controls.** Next to the other `on_value_change` handlers:

```python
    @guard
    def _on_netprem_group(e):
        app_settings.set("gamma_netprem_group", e.value)
        _build_netprem_boxes()

    @guard
    def _on_netprem_mode(e):
        app_settings.set("gamma_netprem_mode", e.value)
        _render_view()

    @guard
    def _on_netprem_clear():
        state["netprem_sel"] = []
        app_settings.set("gamma_netprem_symbols", [])
        _build_netprem_boxes()
        _render_view()

    netprem_tabs.on_value_change(_on_netprem_group)
    netprem_mode.on_value_change(_on_netprem_mode)
    netprem_clear.on_click(_on_netprem_clear)
    _build_netprem_boxes()
```

**(h) Poll the cache view.** In `_poll`, add the key to the existing pipelined read and dispatch:

```python
        v = bus_client.read_versions([
            "options:gamma", "options:gex_status",
            "options:gamma_explain", "options:gamma_analyze",
            "options:gamma_briefings", "options:gamma_history",
            "options:net_premium",
            *_SCHED_VIEWS.values()])
```

and after `_watch_history(v["options:gamma_history"])`:

```python
        if v["options:net_premium"] != seen.get("netprem"):
            seen["netprem"] = v["options:net_premium"]
            # ~500 KB — read OFF the event loop, like the gamma snapshot.
            state["netprem"] = await run.io_bound(
                bus_client.read, "options:net_premium")
            if view_toggle.value == "Net Prem":
                _render_view()
```

**(i) Seed it on first paint.** Next to the other `seen[...] = bus_client.read_version(...)` lines (~line 1590):

```python
    seen["netprem"] = bus_client.read_version("options:net_premium")
```

and inside the existing `_initial_load` off-loop worker, alongside the gamma snapshot read:

```python
        state["netprem"] = await run.io_bound(bus_client.read, "options:net_premium")
```

**Step 4: Run the tests**

```bash
cd webgui && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest -q
```
Expected: all pass, including `test_no_inline_style.py` (it will fail loudly if you reached for `.style()` anywhere).

**Step 5: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_options_gamma.py
git commit -m "feat(gamma): Net Prem subtab with grouped multi-symbol selection"
```

---

## Task 10: Page help, live verification, docs

**Files:**
- Modify: `webgui/page_help.py` (the `/options/gamma` entry)
- Modify: `CLAUDE.md`

**Step 1: Update the page guide**

Find the `/options/gamma` entry in `webgui/page_help.py` and extend its view list to mention the new subtab, e.g.:

> *"**Net Prem** compares intraday net premium (call$ − put$) across a selection of symbols — indices and broad ETFs, the SPDR sectors, or the mega-caps. Pick a group tab to filter the checkbox list; your selection persists across tabs, so you can plot $SPX beside XLK and NVDA. Dollars shows true magnitude, Skew % puts every symbol on a comparable axis. Premium is unsigned cumulative traded dollars (Schwab serves no tape), so this is a money-weighted put/call read, not net buying."*

**Step 2a: Verify `build_net_premium` against the LIVE database**

> **Read this before you try to "just restart the service."** `repo_paths.REPO_ROOT`
> is `Path(__file__).parent`, and `gex_history_db.DB_PATH` is
> `<repo>/options-scanner/gex_history.db`. **This worktree has no such file** — the
> DB is gitignored and the real 1.5 GB one lives in the MAIN repo. A service
> launched from the worktree would create an empty DB and collect into it, and the
> worktree has no `.venv` either (`tools\restart_one.bat` resolves `%~dp0..\.venv`).
> So: verify the compute path against the real DB by pointing `DB_PATH` at it, and
> leave the end-to-end restart until the branch is merged into the main working
> tree.

Run from the worktree root:

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -c "
import sys, pathlib, datetime
sys.path.insert(0, '.')
sys.path.insert(0, 'options-scanner')
import gex_history_db as gh
# Point the reader at the MAIN repo's live DB (this worktree has none).
gh.DB_PATH = pathlib.Path(r'D:\WebGUI Trading with Schwab\options-scanner\gex_history.db')
from services.options_svc import compute
out = compute.build_net_premium(datetime.date.today())
print('session', out['session_date'], 'error', out['error'], 'symbols', len(out['series']))
for s in ('\$SPX','SPY','QQQ','BIG10','NVDA','XLK'):
    rows = out['series'].get(s) or []
    if rows:
        c, p = rows[-1][1], rows[-1][2]
        print(f'{s:6} rows={len(rows):4} net=\${(c-p)/1e6:+9,.1f}M skew={(c-p)/(c+p)*100:+6.1f}%')
    else:
        print(f'{s:6} NO DATA')
"
```

**Expected** (a prototype of exactly this read was run on 2026-08-05 against the live DB, so these are real reference numbers, not guesses):
- `error` is None; ~28 symbols; ~296 rows each mid-session.
- `$SPX`, `SPY`, `QQQ`, `NVDA` have rows immediately — they are already collected.
- `BIG10` has the SAME row count as its members (their timestamps align), e.g. `+$431.4M / +27.2%` while SPY read `−$375.2M / −46.6%`.
- **`XLK` reads NO DATA** until a restarted collector has run at least one poll inside the 08:00–15:20 CT window. Expected, not a bug.
- Sanity check the scale decision while you are here: DIA came in at `+$0.1M` against SPY's `−$375.2M` — that four-orders-of-magnitude spread is exactly why the Skew % mode exists.

**Step 2b: End-to-end after merging**

Once the branch is merged into the main working tree, restart the options service there so the sectors start collecting and the new key is published. Easiest is the **System Status** page (`/status`) → the `options` card's **Restart** button (windowless via `tools\restart_one.bat`, logs to `logs\`). From a shell in the MAIN repo:

```bash
cmd /c "tools\restart_one.bat 8211 8100 options services\options_svc\app.py"
```

Then confirm the published key, during market hours:

```bash
"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -c "
import sys; sys.path.insert(0, '.')
from shared.bus import Bus
p = Bus().cache_get('cache:options:net_premium').payload
print('session', p['session_date'], 'error', p['error'], 'symbols', len(p['series']))
print('sectors with data:', [s for s in ('XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY') if p['series'].get(s)])
"
```

Expected: `error` None, and the sector list filling in after the first poll or two.

**Step 3: Verify in the browser**

Start the preview (`.claude/launch.json` defines `webgui` on :8500), open `/options/gamma`, click the **Net Prem** subtab and confirm:
- The default $SPX / SPY / QQQ lines draw in their fixed colors.
- Switching the group tab to SPDR Sectors keeps those three plotted (selection persists) and shows the sector checkboxes tinted.
- Checking a sector adds a line — or names it in the "no data yet" note if collection has not filled yet.
- The `Skew %` mode rescales to ±100% and the axis title changes.
- `Clear all` empties the chart and shows the "Select one or more symbols" prompt.
- No browser console errors.

**Step 4: Update CLAUDE.md**

Add a **Last updated** entry (bump the date, demote the current entry to "Prior") covering: the new Net Prem subtab and its three groups; the selection model (tab filters, selection persists); the Dollars/Skew % toggle and *why* both exist (four orders of magnitude between $SPX and DIA); the fixed per-symbol color map; the data path (published on the existing 1-min GEX branch, reusing the reads `build_matrix` already does, filtered client-side so toggles are instant); **the collection change and its cost** (11 SPDR sectors added → ~+4,300 Schwab calls/day, and they now also appear on the Opportunity Board + the gamma symbol dropdown); that sector history starts the day it ships; and the restart requirement (`options_svc` + webgui). Also add the `/options/gamma` route-table row mention.

**Step 5: Commit**

```bash
git add webgui/page_help.py CLAUDE.md
git commit -m "docs: Net Prem view guide + CLAUDE.md record"
```

---

## Definition of done

- [ ] `services/options_svc` green (bar the 2 known `test_expected_move` failures).
- [ ] `webgui` green, including `test_no_inline_style.py`.
- [ ] `shared/contracts` green.
- [ ] `options-scanner` collector tests at their baseline.
- [ ] `cache:options:net_premium` verified live with real data, `error: None`.
- [ ] All three groups render in the browser; cross-group selection works; both scales work; no console errors.
- [ ] Sectors confirmed collecting after a restart inside the collection window.
- [ ] CLAUDE.md + page_help updated.
