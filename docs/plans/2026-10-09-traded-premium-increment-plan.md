# Traded Premium, Booked As It Trades: Implementation Plan (Phase A)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Store, per symbol and strike, a running total of traded premium in which
each minute's new volume is priced once, so the figure can never fall when marks
fall. Nothing reads it in this phase.

**Architecture:** A new sibling module, `services/options_svc/traded_premium.py`,
keeps each contract's highest volume in memory and books only the new volume, at
the mark of the reading it arrived in. It rides the collector's existing per-chain
hook and writes one row per symbol per poll, after `poll_once`, as a sixth view
string (`tprem`) in `gex_history.db`. No schema change, no new Schwab call. It
ships switched off.

**Tech stack:** Python 3.11, SQLite through `options-scanner/gex_history_db.py`,
`shared/marketdata_config.py` (read per call, so no restart), pytest.

**Design:** [2026-10-09-traded-premium-increment-design.md](2026-10-09-traded-premium-increment-design.md).
Read it first: the eight rules there are what the tests below pin.

---

## Before you start

**Decisions this plan takes as proposed in the design. None was confirmed by the
user; say so in the hand-off.**

1. New volume is priced at the **mark** (`flow_skew._contract_mark`). The day's
   check also totals it at `last`, so one week of logs settles the choice.
2. Contract baselines are **not** persisted. A restart leaves the volume traded
   during it out of the total.
3. A **carried** minute writes no row.

**One thing the design did not say and the code needs:** the watch begins at the
regular open (`market_calendar.regular_session_has_opened`), the rule
`flow_sides_tick.on_chain` already takes. The design doc has been corrected.

**Where things are.** Work in the worktree, on its branch. The venv is in the main
checkout, so every command uses its absolute path:

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"
```

Run every command from the worktree root unless it says otherwise. Each suite
runs on its own (never `pytest services` over several).

**Rules that bite here.**

- `services/options_svc/compute.py` is 10,514 lines against a ceiling of 10,515
  that may only be lowered. Task 1 makes the room. New code goes in sibling
  modules that import nothing from `compute`.
- Write test files with the editor, never with a shell heredoc.
- The commit hook reports unused imports (F401) and does not fix them. Each task's
  import block is exactly what that task's code uses.
- Do not edit a module while its suite is running.
- Line numbers below are as of commit `8112ca2`. Task 1 shifts everything after
  line 58 of `compute.py`: **find code by its text, not its line number.**
- Compare the failing SET of a suite before and after, never the count.

**Baseline before Task 1** (record the failing set, if any):

```bash
"$PY" -m pytest services/options_svc -q -rf 2>&1 | tail -5
"$PY" -m pytest shared -q -rf 2>&1 | tail -5
"$PY" -m pytest tools -q -rf 2>&1 | tail -5
```

Expected: all green, except that `tools` may show
`test_measure_chain_carry.py::test_a_chain_four_days_out_is_not_an_expiration_day`
(a fixture that hard-codes 2026-10-09; it fails on `main` too and has its own
task).

---

## Task 1: Move the hedging-flow row writer out of `compute`

Makes room under the ceiling. Behaviour does not change; the function keeps its
name on `compute`, which is the rule `test_compute_module_shape.py` states.

**Files:**
- Create: `services/options_svc/hiro_store.py`
- Modify: `services/options_svc/compute.py` (the `_write_hiro_rows` definition, and one import near line 58)
- Modify: `services/options_svc/tests/test_compute_module_shape.py`

**Step 1: Write the failing test**

Append to `services/options_svc/tests/test_compute_module_shape.py`:

```python
def test_the_hiro_row_writer_lives_in_its_own_module():
    from services.options_svc import hiro_store
    assert compute._write_hiro_rows is hiro_store.write_rows
    import ast
    tree = ast.parse(pathlib.Path(hiro_store.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(str(n).endswith("compute") for n in imported), imported
```

**Step 2: Run it to make sure it fails**

```bash
"$PY" -m pytest services/options_svc/tests/test_compute_module_shape.py -q
```

Expected: FAIL, `cannot import name 'hiro_store'`.

**Step 3: Create the module**

`services/options_svc/hiro_store.py`. The docstring and body of `write_rows` are
`compute._write_hiro_rows` moved verbatim: copy them from `compute.py`, do not
retype them.

```python
"""Writing one minute's hedging-flow rows.

Moved out of ``compute`` whole (``compute._write_hiro_rows`` is this function).
It imports nothing from ``compute``.
"""
import logging

from services import _degrade

log = logging.getLogger(__name__)


def write_rows(gh, conn, ts_min, rows) -> None:
    """Persist one minute's HIRO rows ``{symbol: row}``. Never raises.

    ONE batch (one commit) normally. ``insert_hiro_rows`` validates every row
    BEFORE writing any, so one bad row fails the whole batch — then fall back to
    one insert per symbol, so that row alone is lost, not the minute for every
    symbol.

    A DB-level failure part-way through ``executemany`` leaves the batch's
    earlier rows in an OPEN transaction, and the inserts ACCUMULATE: without a
    rollback the fallback would add those rows a second time and commit a
    double count. So the failed batch is rolled back before the fallback."""
    if not rows:
        return
    try:
        gh.insert_hiro_rows(conn, [(s, ts_min, r) for s, r in rows.items()])
        return
    except Exception:
        log.debug("hiro batch insert failed; falling back per symbol", exc_info=True)
    try:
        conn.rollback()
    except Exception:
        log.debug("hiro batch rollback failed", exc_info=True)
    for s, r in rows.items():
        try:
            gh.insert_hiro_row(conn, s, ts_min, r)
        except Exception:
            _degrade.degraded("options.hiro_insert", detail=s)
```

**Step 4: Take it out of `compute.py`**

1. Delete the whole `def _write_hiro_rows(gh, conn, ts_min, rows) -> None:`
   function and the two blank lines after it (30 lines; it sits between
   `hiro_tick_row` and `_rth_bounds`). Leave exactly two blank lines between
   those two functions.
2. Directly under the line
   `from services.options_svc import gamma_window as _gw  # noqa: E402  (the gamma display window + scale lock)`
   add:

```python
from services.options_svc.hiro_store import write_rows as _write_hiro_rows  # noqa: E402  (one minute's hedging-flow rows)
```

The call site, `_write_hiro_rows(gh, conn, _hiro_ts, _hiro_rows)   # never raises`,
does not change.

**Step 5: Lower the ceiling**

```bash
wc -l services/options_svc/compute.py
```

Expected: 10,485. In `test_compute_module_shape.py` set `COMPUTE_MAX_LINES` to
**that count plus 2** (expected 10,487). The two lines are the ones Task 5 adds;
leaving them now means the ceiling is lowered once and never raised.

**Step 6: Run the tests**

```bash
"$PY" -m pytest services/options_svc/tests/test_compute_module_shape.py -q
"$PY" -m pytest services/options_svc/tests/test_compute.py -q -k "hiro"
```

Expected: PASS. The five `test_write_hiro_rows_*` tests call
`compute._write_hiro_rows` and patch `compute._degrade.degraded`; `_degrade` is one
module object, so they pass untouched. **If one fails, the move changed behaviour:
fix the move, do not edit the test.**

**Step 7: Commit**

```bash
git add services/options_svc/hiro_store.py services/options_svc/compute.py services/options_svc/tests/test_compute_module_shape.py
git commit -m "refactor(options_svc): the hedging-flow row writer in a module of its own"
```

---

## Task 2: Two settings

**Files:**
- Modify: `shared/marketdata_config.py` (`DEFAULTS["collection"]`)
- Modify: `config/marketdata.toml` (`[collection]`)
- Modify: `webgui/config_schema.py` (the `Section("Collector", ...)` of the marketdata file)
- Test: `shared/tests/test_marketdata_config.py`

**Step 1: Write the failing tests**

Append to `shared/tests/test_marketdata_config.py`:

```python
# ---- traded premium -----------------------------------------------------------

def test_traded_premium_ships_off_and_only_a_literal_true_switches_it_on(monkeypatch):
    assert mc.section("collection")["traded_premium"] is False
    _with(monkeypatch, "collection", "traded_premium", True)
    assert mc.section("collection")["traded_premium"] is True
    for bad in ("true", 1, None, _MISSING):
        _with(monkeypatch, "collection", "traded_premium", bad)
        assert mc.section("collection")["traded_premium"] is False


def test_the_late_step_is_a_setting(monkeypatch):
    assert mc.section("collection")["traded_premium_late_sec"] == 90
    _with(monkeypatch, "collection", "traded_premium_late_sec", 150)
    assert mc.section("collection")["traded_premium_late_sec"] == 150
    for bad in ("90", True, float("nan"), -1, _MISSING):
        _with(monkeypatch, "collection", "traded_premium_late_sec", bad)
        assert mc.section("collection")["traded_premium_late_sec"] == 90
```

**Step 2: Run them to make sure they fail**

```bash
"$PY" -m pytest shared/tests/test_marketdata_config.py -q -k "traded_premium or late_step"
```

Expected: FAIL with `KeyError: 'traded_premium'`.

**Step 3: Add the defaults**

In `shared/marketdata_config.py`, `DEFAULTS["collection"]` becomes:

```python
    "collection": {"tail_interval_min": 1, "fresh_max_age_sec": 20,
                   "max_gamma_ratio": 10.0, "carry_slack_sec": 30,
                   "cap_refetch_max": 8, "empty_retry_min": 60,
                   "traded_premium": False, "traded_premium_late_sec": 90},
```

`section()` already checks each value against its default's type: a bool default
accepts only a bool, a number default only a finite non-negative number.

**Step 4: Add the keys to the shipped file**

`test_shipped_file_matches_the_built_in_defaults` compares the file with
`DEFAULTS`, so both must move together. At the end of `[collection]` in
`config/marketdata.toml` (after `empty_retry_min = 60`):

```toml
# Record traded premium as it trades: per strike, a running total in which each
# minute's NEW volume is priced once, at that minute's mark. Stored as a sixth
# view ("tprem") beside the five the collector writes; unlike "prem" (the day's
# volume at the CURRENT mark) it cannot fall when prices fall. Nothing reads it
# yet. Adds about an eighth to gex_history.db. Switching it off and on again
# mid-session leaves the volume traded in between out of the total.
traded_premium = false
# Only for the day's check line in the options service's log: new volume first
# seen more than this many seconds after the symbol's previous reading is
# counted as priced late. 90 = more than one poll minute.
traded_premium_late_sec = 90
```

**Step 5: Add them to Settings → Configuration**

In `webgui/config_schema.py`, inside `Section("Collector", "", (` of the
marketdata file, after the `collection.carry_slack_sec` field:

```python
            Field("collection.traded_premium",
                  "Record traded premium as it trades",
                  "Stores, per strike, a running total in which each minute's "
                  "new volume is priced once, at that minute's mark. Unlike "
                  "the premium the Flow view shows, it cannot fall when prices "
                  "fall. Nothing reads it yet. It adds about an eighth to the "
                  "gamma history database. Turning it off and on again during "
                  "a session leaves the volume traded in between out of the "
                  "total.", kind="bool"),
            Field("collection.traded_premium_late_sec",
                  "Seconds after which new volume counts as priced late",
                  "Used only by the daily check line in the options service's "
                  "log. New volume first seen more than this long after the "
                  "symbol's previous reading is counted as priced late.",
                  **{**_SEC, "min": 30}, max=600),
```

**Step 6: Run the tests**

```bash
"$PY" -m pytest shared/tests/test_marketdata_config.py -q
cd webgui && "$PY" -m pytest tests/test_config_schema.py -q; cd ..
```

Expected: PASS. `test_config_schema.py` fails on a file key the catalogue lacks, so
it is the proof the two fields are there.

**Step 7: Commit**

```bash
git add shared/marketdata_config.py config/marketdata.toml webgui/config_schema.py shared/tests/test_marketdata_config.py
git commit -m "feat(config): a switch for recording traded premium, and its late step"
```

---

## Task 3: The arithmetic (`advance`, `grid`, `_resume`)

Pure functions over one symbol's state. No clock, no config, no store.

**Files:**
- Create: `services/options_svc/traded_premium.py`
- Create: `services/options_svc/tests/test_traded_premium.py`

**Step 1: Write the failing tests**

Create `services/options_svc/tests/test_traded_premium.py`:

```python
"""traded_premium: new volume priced once, kept as a running total per strike.

Design: docs/plans/2026-10-09-traded-premium-increment-design.md.
"""
import ast
import pathlib

import numpy as np
import pytest

from services.options_svc import traded_premium as tp


def _c(osi, vol, mark=1.00, **more):
    c = {"symbol": osi, "totalVolume": vol, **more}
    if mark is not None:
        c["mark"] = mark
    return c


def _chain(calls=None, puts=None, spot=500.0):
    """``calls`` / ``puts``: {strike text: [contract, ...]} in one expiration."""
    return {"underlyingPrice": spot,
            "callExpDateMap": {"2026-10-16:11": calls or {}},
            "putExpDateMap": {"2026-10-16:11": puts or {}}}


def _advance(state, chain, ts, late_sec=90):
    return tp.advance(state, chain, ts, late_sec=late_sec)


# ---- the arithmetic -----------------------------------------------------------

def test_the_first_reading_sets_baselines_and_books_nothing():
    s = tp.new_state()
    assert _advance(s, _chain({"100.0": [_c("C1", 1000, 5.0)]}), 60) == 0.0
    assert s["total"] == {} and s["hw"] == {"C1": 1000.0}
    assert s["seeded"] is True and s["since_ts"] == 60 and s["first_vol"] == 1000.0


def test_new_volume_is_priced_once_at_the_mark_it_arrived_at():
    """The design's example: 1,000 contracts at $5.00, then the mark decays."""
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0, 5.0)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 1000, 5.0)]}), 60) == 500_000.0
    for ts, mark in ((120, 2.0), (180, 0.2)):
        assert _advance(s, _chain({"100.0": [_c("C1", 1000, mark)]}), ts) == 0.0
    assert tp.grid(s) == {100.0: {"call": 500_000.0, "put": 0.0, "net": 500_000.0}}


def test_a_contract_first_seen_later_counts_from_zero():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 10)]}), 0)
    booked = _advance(s, _chain({"100.0": [_c("C1", 10)],
                                 "105.0": [_c("C2", 7, 2.0)]}), 60)
    assert booked == 1400.0
    assert tp.grid(s)[105.0]["call"] == 1400.0


def test_a_glitch_read_of_zero_books_nothing_and_cannot_rebook_the_day():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 1000)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 1010)]}), 60)        # 10 x $1 x 100
    assert _advance(s, _chain({"100.0": [_c("C1", 0)]}), 120) == 0.0
    assert _advance(s, _chain({"100.0": [_c("C1", 1010)]}), 180) == 0.0
    assert s["booked"] == 1000.0


def test_volume_with_no_usable_mark_waits_for_one():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 100)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 130, mark=None)]}), 60) == 0.0
    assert s["hw"]["C1"] == 100.0                  # the baseline did not move
    assert _advance(s, _chain({"100.0": [_c("C1", 150, 2.0)]}), 120) == 10_000.0


def test_the_mark_falls_back_to_the_middle_of_the_quote():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    chain = _chain({"100.0": [_c("C1", 10, mark=None, bid=1.0, ask=1.2)]})
    assert _advance(s, chain, 60) == pytest.approx(1100.0)


@pytest.mark.parametrize("mark", [float("inf"), float("nan"), 0, -1.0])
def test_an_unusable_mark_is_never_priced(mark):
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    assert _advance(s, _chain({"100.0": [_c("C1", 10, mark=mark)]}), 60) == 0.0
    assert s["total"] == {}


def test_strikes_add_up_across_expirations_and_the_sides_stay_apart():
    def chain(a, b, p):
        return {"underlyingPrice": 500.0,
                "callExpDateMap": {"2026-10-09:4": {"100.0": [a]},
                                   "2026-10-16:11": {"100.0": [b]}},
                "putExpDateMap": {"2026-10-09:4": {"100.0": [p]}}}
    s = tp.new_state()
    _advance(s, chain(_c("A", 0), _c("B", 0), _c("P", 0)), 0)
    assert _advance(s, chain(_c("A", 1, 1.0), _c("B", 2, 3.0), _c("P", 5, 0.5)),
                    60) == 950.0
    assert tp.grid(s) == {100.0: {"call": 700.0, "put": 250.0, "net": 450.0}}


@pytest.mark.parametrize("chain", [None, "x", {}, {"error": "Bad Request"},
                                   {"callExpDateMap": {}, "putExpDateMap": {}},
                                   {"callExpDateMap": 5}])
def test_a_chain_with_no_contracts_changes_nothing(chain):
    """An error body must not count as the first reading: the next real chain
    would then read the whole day's volume as new."""
    s = tp.new_state()
    assert _advance(s, chain, 60) is None
    assert s == tp.new_state()


def test_volume_first_seen_after_a_long_step_is_counted_as_priced_late():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 10)]}), 60)           # one minute on
    _advance(s, _chain({"100.0": [_c("C1", 15)]}), 260)          # 200 s on
    assert (s["booked_vol"], s["late_vol"]) == (15.0, 5.0)


def test_the_same_volume_is_also_priced_at_last_where_there_is_one():
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 0), _c("C2", 0)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 10, 2.0, last=2.5),
                                  _c("C2", 4, 1.0)]}), 60)
    assert s["booked"] == 2400.0                        # everything, at the mark
    assert (s["last_vol"], s["at_last"], s["at_mark_same"]) == (10.0, 2500.0, 2000.0)


def test_the_chains_own_volume_is_read_apart_from_the_booking():
    """What the day's check sets the booking against."""
    s = tp.new_state()
    _advance(s, _chain({"100.0": [_c("C1", 100)]}), 0)
    _advance(s, _chain({"100.0": [_c("C1", 130, mark=None)]}), 60)
    assert (s["first_vol"], s["chain_vol"], s["booked_vol"]) == (100.0, 130.0, 0.0)


# ---- resuming a stored total --------------------------------------------------

def test_a_resumed_total_lands_in_the_cell_new_volume_uses():
    """The store keeps strikes as float32: 17.63 reads back as 17.6299991607666.
    Two cells for one strike pack to ONE stored key, and one would be lost."""
    stored_key = float(np.float32(17.63))
    assert stored_key != 17.63
    s = tp.new_state()
    tp._resume(s, (0, 500.0, 0.0,
                   {stored_key: {"call": 1000.0, "put": 0.0, "net": 1000.0}}))
    _advance(s, _chain({"17.63": [_c("X", 0)]}), 0)
    _advance(s, _chain({"17.63": [_c("X", 5)]}), 60)
    assert tp.grid(s) == {17.63: {"call": 1500.0, "put": 0.0, "net": 1500.0}}


def test_resume_takes_what_is_usable_and_nothing_else():
    s = tp.new_state()
    for prior in (None, (0, 1.0, 0.0, None), (0, 1.0, 0.0, "x")):
        tp._resume(s, prior)
    assert s["total"] == {}
    tp._resume(s, (0, 1.0, 0.0, {100.0: {"call": 5.0, "put": float("nan")},
                                 "bad": {"call": 1.0}, 105.0: "x"}))
    assert tp.grid(s) == {100.0: {"call": 5.0, "put": 0.0, "net": 5.0}}


# ---- the module ---------------------------------------------------------------

def test_the_module_imports_nothing_from_compute():
    tree = ast.parse(pathlib.Path(tp.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    assert not any(n.endswith("compute") for n in names), names
```

**Step 2: Run them to make sure they fail**

```bash
"$PY" -m pytest services/options_svc/tests/test_traded_premium.py -q
```

Expected: an import error, `cannot import name 'traded_premium'`.

**Step 3: Write the module's pure half**

Create `services/options_svc/traded_premium.py`:

```python
"""Traded premium, booked as it trades: per strike and side, a running total of
new volume priced ONCE, at the mark of the reading it first appeared in.

The stored ``prem`` view is the day's volume at the CURRENT mark, so it falls
when marks fall. This figure cannot: a sixth view string, ``tprem``, in
``gex_history.db``, with the same ``{call, put, net}`` cell.

``advance``     pure: books one chain's new volume into a symbol's state.
``on_chain``    every fetched chain, on the collector's thread. In memory only
                -- it never opens the database, so it cannot slow or break a
                poll.
``write_rows``  once a poll, after ``poll_once``, on its write connection:
                resumes a symbol's total from the store the first time it is
                written after a start, writes this poll's rows in one commit,
                and logs the day's check once after the regular close.

Off unless ``config/marketdata.toml [collection] traded_premium`` is true, and
nothing reads the view yet. An ESTIMATE: unsigned, priced at the mid of the
minute the volume was first seen. It imports nothing from ``compute``.
Design: docs/plans/2026-10-09-traded-premium-increment-design.md.
"""
import sys

from repo_paths import OPTIONS_SCANNER
from shared.numeric import finite as _finite
from shared.numeric import parsed_finite as _parsed

# ``flow_skew`` lives under options-scanner; ``compute`` puts it on the path
# too, repeated here so this module imports on its own (a test, a tool).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import flow_skew  # noqa: E402  (the ONE definition of a contract's mark)

VIEW = "tprem"
CALL, PUT = 0, 1

_MULTIPLIER = 100           # one option contract = 100 shares
_SIDES = (("callExpDateMap", CALL), ("putExpDateMap", PUT))
# A strike is keyed at three decimals. The store keeps strikes as float32, so
# an odd strike (17.63) comes back as 17.6299991607666; unrounded, a total
# resumed after a restart and the same strike's new volume would sit in two
# cells that pack to ONE float32 key, and one of them would be lost.
_STRIKE_DECIMALS = 3


def new_state() -> dict:
    """One symbol's session state."""
    return {
        "seeded": False,        # one usable reading taken this session
        "since_ts": None,       # when the watch began: the first usable reading
        "last_ts": None,        # the latest usable reading
        "hw": {},               # {contract symbol: highest volume already booked}
        "total": {},            # {strike: [call dollars, put dollars]}
        # The day's check. The chain's own volume, first and latest, is summed
        # straight off the chain so it does not depend on the booking below.
        "first_vol": 0.0, "chain_vol": 0.0,
        "booked_vol": 0.0, "late_vol": 0.0, "booked": 0.0,
        # The same new volume priced at each contract's ``last``, where it has
        # one, beside the mark dollars for exactly that volume.
        "last_vol": 0.0, "at_last": 0.0, "at_mark_same": 0.0,
    }


def _walk(chain):
    """Yield ``(side, strike, contract)`` for every contract dict in a chain.

    The strike is the MAP KEY, as ``flow_skew._accumulate_by_strike`` reads it:
    the key is always present, while the contract's own field can be absent."""
    for mapkey, side in _SIDES:
        exp_map = chain.get(mapkey)
        if not isinstance(exp_map, dict):
            continue
        for strike_map in exp_map.values():
            if not isinstance(strike_map, dict):
                continue
            for raw_strike, contracts in strike_map.items():
                strike = _parsed(raw_strike)
                if strike is None or not isinstance(contracts, (list, tuple)):
                    continue
                strike = round(strike, _STRIKE_DECIMALS)
                for c in contracts:
                    if isinstance(c, dict):
                        yield side, strike, c


def advance(state, chain, now_ts, *, late_sec):
    """Book one fetched chain's new volume into ``state``, IN PLACE. Returns
    the dollars booked by this reading, or None for a chain with no contracts
    (an error body), which changes nothing.

    The symbol's FIRST usable reading only sets each contract's baseline: after
    a restart the day's volume must not land in one minute. On a later reading
    a contract with no baseline stood at zero volume before, so all of its
    volume is new.

    The baseline is a HIGH-WATER mark: volume never falls within a session, so
    a glitch read of 0 books nothing and cannot re-book the day. A contract
    with new volume and no usable mark books nothing and KEEPS its baseline, so
    that volume is booked when a mark returns."""
    if not isinstance(chain, dict):
        return None
    seeded = state["seeded"]
    last_ts = state["last_ts"]
    late = last_ts is not None and now_ts - last_ts > late_sec
    hw, total = state["hw"], state["total"]
    contracts, chain_vol, dollars_now = 0, 0.0, 0.0
    for side, strike, c in _walk(chain):
        osi = c.get("symbol")
        vol = _finite(c.get("totalVolume"))
        if not osi or vol is None or vol < 0:
            continue
        contracts += 1
        chain_vol += vol
        before = hw.get(osi)
        if before is None:
            if not seeded:
                if vol > 0:
                    hw[osi] = vol
                continue
            before = 0.0
        dv = vol - before
        if dv <= 0:
            continue
        mark = _finite(flow_skew._contract_mark(c))
        if mark is None:
            continue
        hw[osi] = vol
        dollars = dv * mark * _MULTIPLIER
        cell = total.get(strike)
        if cell is None:
            cell = total[strike] = [0.0, 0.0]
        cell[side] += dollars
        dollars_now += dollars
        state["booked_vol"] += dv
        if late:
            state["late_vol"] += dv
        last = _finite(c.get("last"))
        if last is not None and last > 0:
            state["last_vol"] += dv
            state["at_last"] += dv * last * _MULTIPLIER
            state["at_mark_same"] += dollars
    if not contracts:
        return None
    if not seeded:
        state["seeded"], state["since_ts"] = True, now_ts
        state["first_vol"] = chain_vol
    state["chain_vol"] = chain_vol
    state["booked"] += dollars_now
    state["last_ts"] = now_ts
    return dollars_now


def grid(state) -> dict:
    """The running totals as the store's cell shape, strikes ascending. Exactly
    ``{call, put, net}`` floats: what the columnar packer gates on."""
    return {strike: {"call": cell[CALL], "put": cell[PUT],
                     "net": cell[CALL] - cell[PUT]}
            for strike, cell in sorted(state["total"].items())}


def _resume(state, prior) -> None:
    """Add the session's last stored totals to ``state``: what a restart must
    not lose. ``prior`` is ``gex_history_db.latest_grid_row``'s answer.

    Everything is read BEFORE anything is added, so a bad cell cannot leave
    half a total behind for the retry to add a second time."""
    if not prior or not isinstance(prior[3], dict):
        return
    adds = []
    for raw_strike, cell in prior[3].items():
        strike = _parsed(raw_strike)
        if strike is None or not isinstance(cell, dict):
            continue
        adds.append((round(strike, _STRIKE_DECIMALS),
                     max(_finite(cell.get("call")) or 0.0, 0.0),
                     max(_finite(cell.get("put")) or 0.0, 0.0)))
    total = state["total"]
    for strike, call, put in adds:
        cell = total.get(strike)
        if cell is None:
            cell = total[strike] = [0.0, 0.0]
        cell[CALL] += call
        cell[PUT] += put
```

**Step 4: Run the tests**

```bash
"$PY" -m pytest services/options_svc/tests/test_traded_premium.py -q
```

Expected: PASS, all of them.

**Step 5: Commit**

```bash
git add services/options_svc/traded_premium.py services/options_svc/tests/test_traded_premium.py
git commit -m "feat(options_svc): book new option volume once, at the mark it arrived at"
```

---

## Task 4: The session state and the store rows (`on_chain`, `write_rows`, the day's check)

**Files:**
- Modify: `services/options_svc/traded_premium.py`
- Modify: `services/options_svc/tests/test_traded_premium.py`

**Step 1: Write the failing tests**

In `test_traded_premium.py`, replace the import block at the top with:

```python
import ast
import datetime as dt
import logging
import pathlib
import sqlite3
import time
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from services import _degrade
from services.options_svc import traded_premium as tp

import gex_history_db as gh

CT = ZoneInfo("America/Chicago")
MON = "2026-10-05"                  # a trading day
```

(`gex_history_db` is importable because `traded_premium` puts `options-scanner`
on the path; keep that import after it.)

Then append:

```python
# ---- the session state and the store -----------------------------------------
# Readings are taken mid-morning so the stored rows fall on the same calendar
# date whatever zone the machine running the suite is in.

@pytest.fixture(autouse=True)
def _clean():
    tp.reset()
    yield
    tp.reset()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    gh.init_schema(c)
    yield c
    c.close()


def _switch(monkeypatch, on=True, late_sec=90):
    """The ACCESSOR is patched, so a test proves the value is read."""
    real = tp._mdc.section
    monkeypatch.setattr(tp._mdc, "section", lambda name: dict(
        real(name), traded_premium=on, traded_premium_late_sec=late_sec))


def _at(hh, mm, day=MON):
    y, m, d = (int(x) for x in day.split("-"))
    return dt.datetime(y, m, d, hh, mm, tzinfo=CT)


def _poll(conn, now, chain, symbol="SPY", store=gh):
    tp.on_chain(symbol, chain, now)
    tp.write_rows(store, conn, int(now.timestamp()) // 60 * 60)


def _stored(conn, symbol="SPY"):
    return [(ts, gh._decode_grid(raw), calls, puts)
            for ts, raw, calls, puts in conn.execute(
                "SELECT ts, gex_json, call_prem, put_prem FROM snapshots "
                "WHERE symbol = ? AND view = 'tprem' ORDER BY ts", (symbol,))]


def _call(vol, mark=1.0):
    return _chain({"100.0": [_c("C1", vol, mark)]})


def _degrades(monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **kw: seen.append(area))
    return seen


class _Store:
    """The real store, with one named call made to fail once."""

    def __init__(self, fail):
        self.fail = fail

    def _call(self, name, *a, **k):
        if self.fail == name:
            self.fail = None
            raise sqlite3.OperationalError("database is locked")
        return getattr(gh, name)(*a, **k)

    def latest_grid_row(self, *a, **k):
        return self._call("latest_grid_row", *a, **k)

    def insert_snapshot(self, *a, **k):
        return self._call("insert_snapshot", *a, **k)


def test_it_ships_off_and_then_does_nothing(conn):
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))
    assert _stored(conn) == [] and tp._S["symbols"] == {}


def test_nothing_is_booked_before_the_regular_open(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(8, 0), _call(50))
    assert _stored(conn) == [] and tp._S["symbols"] == {}


def test_each_poll_stores_the_running_total_and_no_side_ever_falls(monkeypatch, conn):
    _switch(monkeypatch)

    def chain(call_vol, call_mark, put_vol, put_mark):
        return _chain({"100.0": [_c("C1", call_vol, call_mark)]},
                      {"100.0": [_c("P1", put_vol, put_mark)]})

    _poll(conn, _at(10, 0), chain(1000, 5.0, 200, 1.0))
    _poll(conn, _at(10, 1), chain(1100, 5.0, 200, 1.0))    # 100 calls at $5
    _poll(conn, _at(10, 2), chain(1100, 2.0, 250, 1.2))    # the call mark falls
    rows = _stored(conn)
    assert [ts for ts, *_ in rows] == [int(_at(10, m).timestamp()) for m in (0, 1, 2)]
    assert [g for _ts, g, _calls, _puts in rows] == [
        {},                                                # the watch began here
        {100.0: {"call": 50_000.0, "put": 0.0, "net": 50_000.0}},
        {100.0: {"call": 50_000.0, "put": 6_000.0, "net": 44_000.0}}]
    assert rows[-1][2:] == (50_000.0, 6_000.0)             # the symbol's totals


def test_the_row_carries_the_chains_spot_and_the_polls_minute(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _chain({"100.0": [_c("C1", 1)]}, spot=512.5))
    assert conn.execute("SELECT ts, spot, dte FROM snapshots WHERE view = 'tprem'"
                        ).fetchall() == [(int(_at(10, 0).timestamp()), 512.5, None)]


def test_without_the_polls_minute_the_row_takes_the_clocks(monkeypatch, conn):
    _switch(monkeypatch)
    tp.on_chain("SPY", _call(1), _at(10, 0))
    tp.write_rows(gh, conn, None)
    (ts, *_rest), = _stored(conn)
    assert ts % 60 == 0 and abs(ts - time.time()) < 120


def test_a_restart_continues_the_total_and_leaves_the_gap_unbooked(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000, 5.0))
    _poll(conn, _at(10, 1), _call(1100, 5.0))              # $50,000
    tp.reset()                                             # the process restarted
    _poll(conn, _at(10, 5), _call(1500, 2.0))              # 400 traded unseen
    _poll(conn, _at(10, 6), _call(1510, 2.0))              # 10 x $2 x 100
    assert [g[100.0]["call"] for _ts, g, _calls, _puts in _stored(conn)[1:]] == [
        50_000.0, 50_000.0, 52_000.0]


def test_a_new_session_starts_from_zero(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))
    _poll(conn, _at(10, 0, day="2026-10-06"), _call(30))
    _poll(conn, _at(10, 1, day="2026-10-06"), _call(40))
    assert _stored(conn)[-1][1] == {100.0: {"call": 1000.0, "put": 0.0, "net": 1000.0}}


def test_switching_it_off_drops_the_state_and_on_again_resumes_from_the_store(
        monkeypatch, conn):
    """Hours of unwatched volume must not be booked at one minute's mark."""
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))                   # $10,000
    _switch(monkeypatch, on=False)
    _poll(conn, _at(10, 2), _call(5000))
    assert tp._S["date"] is None and len(_stored(conn)) == 2
    _switch(monkeypatch)
    _poll(conn, _at(10, 3), _call(5000))                   # sets baselines again
    _poll(conn, _at(10, 4), _call(5005))                   # $500
    assert _stored(conn)[-1][1][100.0]["call"] == 10_500.0


def test_a_failed_write_never_raises_and_the_next_row_carries_it(monkeypatch, conn):
    _switch(monkeypatch)
    seen = _degrades(monkeypatch)
    store = _Store("insert_snapshot")
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100), store=store)      # this write fails
    _poll(conn, _at(10, 2), _call(1100), store=store)
    assert seen == ["options.traded_premium.write"]
    assert [g for _ts, g, _calls, _puts in _stored(conn)] == [
        {}, {100.0: {"call": 10_000.0, "put": 0.0, "net": 10_000.0}}]


def test_a_failed_resume_is_retried_and_never_counted_twice(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(1000))
    _poll(conn, _at(10, 1), _call(1100))                   # $10,000 stored
    tp.reset()
    seen = _degrades(monkeypatch)
    store = _Store("latest_grid_row")
    _poll(conn, _at(10, 2), _call(1100), store=store)      # the read fails
    _poll(conn, _at(10, 3), _call(1105), store=store)      # $500 more
    _poll(conn, _at(10, 4), _call(1105), store=store)
    assert seen == ["options.traded_premium.write"]
    assert [g[100.0]["call"] for _ts, g, _calls, _puts in _stored(conn)[2:]] == [
        10_500.0, 10_500.0]


@pytest.mark.parametrize("chain", [None, "x", {}, {"callExpDateMap": 5}])
def test_a_bad_chain_never_raises_and_writes_no_row(monkeypatch, conn, chain):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), chain)
    assert _stored(conn) == []


def test_on_chain_counts_a_failure_and_carries_on(monkeypatch):
    _switch(monkeypatch)
    seen = _degrades(monkeypatch)
    monkeypatch.setattr(tp, "advance", lambda *a, **k: 1 / 0)
    tp.on_chain("SPY", _call(1), _at(10, 0))               # must not raise
    assert seen == ["options.traded_premium.on_chain"]


# ---- the day's check ----------------------------------------------------------

def test_day_check_sets_what_the_chain_showed_beside_what_was_booked(monkeypatch, conn):
    _switch(monkeypatch)
    _poll(conn, _at(10, 0), _call(100))
    _poll(conn, _at(10, 1), _chain({"100.0": [_c("C1", 130, mark=None)]}))
    check = tp.day_check()
    assert check["date"] == MON
    assert check["symbols"]["SPY"]["seen_vol"] == 30.0
    assert check["symbols"]["SPY"]["booked_vol"] == 0.0
    assert check["pass"]["polls"] == 2 and check["write"]["polls"] == 2


def test_the_days_check_is_logged_once_after_the_regular_close(monkeypatch, conn, caplog):
    _switch(monkeypatch)
    caplog.set_level(logging.INFO, logger=tp.log.name)

    def lines():
        return [r.getMessage() for r in caplog.records if "day_check" in r.getMessage()]

    _poll(conn, _at(14, 58), _call(1000, 2.0))
    _poll(conn, _at(14, 59), _chain({"100.0": [_c("C1", 1010, 2.0, last=2.5)]}))
    assert lines() == []
    _poll(conn, _at(15, 1), _call(1010, 2.0))
    _poll(conn, _at(15, 2), _call(1010, 2.0))
    got = lines()
    assert len(got) == 2                                   # everything, then SPY
    assert got[0].startswith(f"traded_premium day_check {MON} all: 1 symbols; "
                             "booked 10 of 10 contracts seen (100.0%)")
    assert got[1].startswith(f"traded_premium day_check {MON} SPY: since 14:58; ")
    assert "at last $2,500 against $2,000 at the mark" in got[1]
```

**Step 2: Run them to make sure they fail**

```bash
"$PY" -m pytest services/options_svc/tests/test_traded_premium.py -q
```

Expected: the Task 3 tests still PASS; the new ones FAIL with
`AttributeError: module ... has no attribute 'reset'` (from the fixture).

**Step 3: Add the state half to the module**

In `traded_premium.py`, replace the import block (everything from `import sys`
down to and including the `import flow_skew` line) with:

```python
import datetime as _dt
import logging
import statistics
import sys
import time
from zoneinfo import ZoneInfo

from repo_paths import OPTIONS_SCANNER
from shared import market_calendar as _mc
from shared import marketdata_config as _mdc
from shared.numeric import finite as _finite
from shared.numeric import parsed_finite as _parsed

from services import _degrade

# ``flow_skew`` lives under options-scanner; ``compute`` puts it on the path
# too, repeated here so this module imports on its own (a test, a tool).
if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import flow_skew  # noqa: E402  (the ONE definition of a contract's mark)

log = logging.getLogger(__name__)
```

Directly under `CALL, PUT = 0, 1` add:

```python

_CT = ZoneInfo("America/Chicago")
```

Then append to the end of the file:

```python


# ── the session state ────────────────────────────────────────────────────────
def _fresh(date=None) -> dict:
    return {
        "date": date,           # CT session date the state belongs to
        "symbols": {},          # {symbol: new_state()}
        "rows": {},             # {symbol: spot}: read this poll, not yet written
        "restored": set(),      # symbols whose stored total has been added back
        "now": None,            # the latest reading's clock
        "poll_sec": 0.0,        # this poll's time in on_chain, so far
        "passes": [],           # each poll's time in on_chain, seconds
        "writes": [],           # each poll's write, seconds
        "checked": False,       # the day's check was logged
    }


# ``on_chain`` and ``write_rows`` run on ONE thread (poll_once loops over its
# results on the calling thread, then the write follows), so no lock.
_S: dict = _fresh()


def reset() -> None:
    """Drop all state (a test helper; also what switching it off does)."""
    _S.clear()
    _S.update(_fresh())


def _roll(date) -> None:
    if _S["date"] != date:
        _S.clear()
        _S.update(_fresh(date))


def _now(now):
    """``now`` as an aware Central datetime. None = the scheduler's clock, the
    one ``compute`` reads; a naive value is Central."""
    if now is None:
        from services.options_svc import scheduler as _sched   # lazy: import cycle
        now = _sched._market_now()
    if now.tzinfo is None:
        return now.replace(tzinfo=_CT)
    return now.astimezone(_CT)


def on_chain(symbol, chain, now=None) -> None:
    """Book one fetched chain's new volume. In memory only; never raises."""
    try:
        cfg = _mdc.section("collection")
        if cfg.get("traded_premium") is not True:
            # Switched off: drop the state, so switching it back on resumes
            # from the store like a restart. Hours of unwatched volume must
            # not be booked at one minute's mark.
            if _S["date"] is not None:
                reset()
            return
        now = _now(now)
        # Nothing before the regular open, the rule the bought/sold tally and
        # the hedging-flow model take: until then a chain can still carry
        # yesterday's volume for a contract that has not traded yet, and its
        # marks are frozen. After the 15:00 close it keeps booking: ETF options
        # trade to 15:15.
        if not _mc.regular_session_has_opened(now):
            return
        started = time.perf_counter()
        _roll(now.date().isoformat())
        state = _S["symbols"].get(symbol)
        if state is None:
            state = _S["symbols"][symbol] = new_state()
        booked = advance(state, chain, int(now.timestamp()),
                         late_sec=cfg["traded_premium_late_sec"])
        if booked is not None:
            _S["rows"][symbol] = _finite(chain.get("underlyingPrice"))
            _S["now"] = now
        _S["poll_sec"] += time.perf_counter() - started
    except Exception:
        _degrade.degraded("options.traded_premium.on_chain", detail=symbol)


def write_rows(gh, conn, ts_min=None) -> None:
    """Write this poll's rows in ONE commit. Never raises.

    The stored row is the RUNNING TOTAL, so a row this fails to write loses
    nothing: the next one carries it. A symbol written for the first time
    since this process started (or since the switch came on) first gets the
    session's last stored total added back.

    ``ts_min`` is the poll's minute, the one the hedging-flow rows use. A
    symbol's first row may hold an empty grid: its time is when the watch
    began."""
    rows, _S["rows"] = _S["rows"], {}
    if not rows:
        return
    try:
        _S["passes"].append(_S["poll_sec"])
        _S["poll_sec"] = 0.0
        started = time.perf_counter()
        day = _dt.date.fromisoformat(_S["date"])
        ts = int(ts_min) if ts_min is not None else int(time.time()) // 60 * 60
        for symbol, spot in rows.items():
            state = _S["symbols"][symbol]
            if symbol not in _S["restored"]:
                _resume(state, gh.latest_grid_row(conn, symbol, VIEW, date=day))
                _S["restored"].add(symbol)
            cells = grid(state)
            calls = sum(c["call"] for c in cells.values())
            puts = sum(c["put"] for c in cells.values())
            gh.insert_snapshot(
                conn, symbol, VIEW,
                {"ts": ts, "spot": spot, "net_total": calls - puts,
                 "call_prem": calls, "put_prem": puts},
                cells, None)
        conn.commit()
        _S["writes"].append(time.perf_counter() - started)
    except Exception:
        _degrade.degraded("options.traded_premium.write")
        try:
            conn.rollback()
        except Exception:
            log.debug("traded premium rollback failed", exc_info=True)
        return
    _log_day_check_once()


# ── the day's check ──────────────────────────────────────────────────────────
_SUMS = ("booked_vol", "seen_vol", "late_vol", "booked", "last_vol", "at_last",
         "at_mark_same")


def _timing(samples):
    if not samples:
        return None
    return {"polls": len(samples),
            "median_ms": round(statistics.median(samples) * 1000.0, 1),
            "worst_ms": round(max(samples) * 1000.0, 1)}


def day_check() -> dict:
    """The figures Phase A is judged on, per symbol and for the timing.

    ``seen_vol`` is the chain's own volume now less at the first reading: what
    the booking should have found. ``booked_vol`` under it is volume with no
    usable mark, or a contract that left the chain."""
    symbols = {}
    for symbol, s in _S["symbols"].items():
        if not s["seeded"]:
            continue
        symbols[symbol] = {
            "since_ts": s["since_ts"], "booked_vol": s["booked_vol"],
            "seen_vol": s["chain_vol"] - s["first_vol"],
            "late_vol": s["late_vol"], "booked": s["booked"],
            "last_vol": s["last_vol"], "at_last": s["at_last"],
            "at_mark_same": s["at_mark_same"]}
    return {"date": _S["date"], "symbols": symbols,
            "pass": _timing(_S["passes"]), "write": _timing(_S["writes"])}


def _pct(part, whole) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole > 0 else "n/a"


def _check_line(s) -> str:
    return (f"booked {s['booked_vol']:,.0f} of {s['seen_vol']:,.0f} contracts "
            f"seen ({_pct(s['booked_vol'], s['seen_vol'])}); "
            f"{_pct(s['late_vol'], s['booked_vol'])} of them priced late; "
            f"${s['booked']:,.0f} at the mark; at last ${s['at_last']:,.0f} "
            f"against ${s['at_mark_same']:,.0f} at the mark for the "
            f"{_pct(s['last_vol'], s['booked_vol'])} of volume with a last price")


def _log_day_check_once() -> None:
    """One line for everything and one a symbol, at the first poll after the
    regular close. Never raises."""
    try:
        now = _S["now"]
        if _S["checked"] or now is None or _mc.is_regular_hours(now):
            return
        _S["checked"] = True
        check = day_check()
        every = {k: sum(s[k] for s in check["symbols"].values()) for k in _SUMS}
        log.info("traded_premium day_check %s all: %d symbols; %s; pass %s; "
                 "write %s", check["date"], len(check["symbols"]),
                 _check_line(every), check["pass"], check["write"])
        for symbol, s in sorted(check["symbols"].items()):
            since = _dt.datetime.fromtimestamp(s["since_ts"], _CT).strftime("%H:%M")
            log.info("traded_premium day_check %s %s: since %s; %s",
                     check["date"], symbol, since, _check_line(s))
    except Exception:
        _degrade.degraded("options.traded_premium.day_check")
```

This code was run once, outside the repo, against a real in-memory store before
the plan was written (seed, three polls, a restart, an odd strike across a
restart, the close). The tests above are the proof; that run only means the code
is not being seen for the first time.

**Step 4: Run the tests**

```bash
"$PY" -m pytest services/options_svc/tests/test_traded_premium.py -q
"$PY" -m pytest services/tests/test_no_silent_degrades.py -q
```

Expected: PASS. If a test fails, the module is wrong, not the test: the tests are
the design's rules. The one exception is a float that differs in its last digit;
then use `pytest.approx` on that one figure and say so in the commit.

**Step 5: Commit**

```bash
git add services/options_svc/traded_premium.py services/options_svc/tests/test_traded_premium.py
git commit -m "feat(options_svc): traded premium as a running total in the gamma history store"
```

---

## Task 5: Wire it into the collection poll

Two lines and one widened import in `compute.collect_gex_snapshots`.

**Files:**
- Modify: `services/options_svc/compute.py` (`collect_gex_snapshots`)
- Test: `services/options_svc/tests/test_compute.py`

**Step 1: Write the failing test**

In `test_compute.py`, directly after
`test_collect_gex_snapshots_records_no_hiro_rows_outside_regular_hours`
(it uses that section's `_hiro_ct`, `_hchain` and `_fake_gex_modules`):

```python
def test_collect_gex_snapshots_hands_each_chain_to_traded_premium_then_writes(
        monkeypatch):
    """Every fetched chain, then ONE write after the poll, on the poll's own
    connection and minute. What the module does with them is its own suite."""
    from services.options_svc import traded_premium
    now = _hiro_ct(10, 0)
    chains = {"SPY": _hchain(1000), "NVDA": _hchain(7)}
    calls = _fake_gex_modules(monkeypatch, lock_ok=True, now=now, chains=chains)
    monkeypatch.setattr(compute, "_publish_eth_eligibility", lambda seen: None)
    seen = []
    monkeypatch.setattr(
        traded_premium, "on_chain",
        lambda sym, chain, at=None: seen.append(("chain", sym, chain is chains[sym], at)))
    monkeypatch.setattr(
        traded_premium, "write_rows",
        lambda gh, conn, ts_min=None: seen.append(("write", conn is calls["conn"], ts_min)))

    compute.collect_gex_snapshots(now=now)

    assert seen == [("chain", "SPY", True, now), ("chain", "NVDA", True, now),
                    ("write", True, int(now.timestamp()) // 60 * 60)]
```

**Step 2: Run it to make sure it fails**

```bash
"$PY" -m pytest services/options_svc/tests/test_compute.py -q -k "traded_premium"
```

Expected: FAIL, `assert [] == [...]`.

**Step 3: Wire it**

In `collect_gex_snapshots`:

1. Change
   `from services.options_svc import eth, flow_sides_tick`
   to
   `from services.options_svc import eth, flow_sides_tick, traded_premium`
2. In the nested `on_chain`, directly under
   `flow_sides_tick.on_chain(sym, chain, now)  # bought/sold tally; never raises`
   add:

```python
            traded_premium.on_chain(sym, chain, now)   # traded premium; never raises
```

3. Directly under
   `_write_hiro_rows(gh, conn, _hiro_ts, _hiro_rows)   # never raises`
   add:

```python
        traded_premium.write_rows(gh, conn, _hiro_ts)      # never raises
```

`_hiro_ts` is the poll's minute, computed once before `poll_once`. It is `None`
only when the hedging-flow setup above it degraded; `write_rows` then takes the
clock's minute.

**Step 4: Run the tests and check the ceiling**

```bash
"$PY" -m pytest services/options_svc/tests/test_compute.py -q -k "collect_gex or hiro or traded_premium"
"$PY" -m pytest services/options_svc/tests/test_compute_module_shape.py -q
wc -l services/options_svc/compute.py
```

Expected: PASS, and the count equals `COMPUTE_MAX_LINES` (expected 10,487). If
the count is over, a line was added that this plan does not list: find it. Never
raise the ceiling.

The other `collect_gex_snapshots` tests run with the switch off (the shipped
value; the override layer is ignored under pytest), so `on_chain` returns at
once and `write_rows` has no rows: they must pass untouched.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(options_svc): the collection poll feeds traded premium and writes its rows"
```

---

## Task 6: The check tool

Check 1 (no cell ever falls) and the storage half of check 3, for every symbol
with stored rows on a day. Read-only.

**Files:**
- Create: `tools/check_traded_premium.py`
- Create: `tools/tests/test_check_traded_premium.py`

**Step 1: Write the failing tests**

Create `tools/tests/test_check_traded_premium.py`:

```python
"""``tools/check_traded_premium.py`` -- does a stored traded-premium total ever
fall, and what does the view add to the day's stored bytes.

Synthetic rows and an in-memory database; no store is opened here.
"""
import datetime as dt
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import check_traded_premium as tool  # noqa: E402

gh = tool.gh
DAY = dt.date(2026, 10, 5)
CLEAN = {"symbols": 1, "rows": 2, "steps": 2, "fell": 0, "dollars": 0.0, "worst": []}


def _cell(call, put):
    return {"call": call, "put": put, "net": call - put}


def _row(i, grid):
    return (1000 + 60 * i, 100.0, None, None, None, 0, grid)


def _db():
    conn = sqlite3.connect(":memory:")
    gh.init_schema(conn)
    return conn


def _put(conn, symbol, view, minute, grid):
    ts = int(dt.datetime(2026, 10, 5, 10, minute).timestamp())
    gh.insert_snapshot(conn, symbol, view, {"ts": ts, "spot": 100.0}, grid, None)


def test_a_total_that_only_rises_has_no_falls():
    rows = [_row(0, {}), _row(1, {100.0: _cell(5.0, 0.0)}),
            _row(2, {100.0: _cell(5.0, 2.0)})]
    assert tool.falls(rows) == (2, 0, 0.0)


def test_a_fall_is_counted_even_across_missing_minutes():
    """A running total must not fall across a gap either."""
    rows = [_row(0, {100.0: _cell(9.0, 1.0)}), _row(5, {100.0: _cell(7.0, 1.0)})]
    assert tool.falls(rows) == (2, 1, 2.0)


def test_check_reads_every_symbol_with_rows_that_day():
    conn = _db()
    _put(conn, "SPY", "tprem", 0, {100.0: _cell(5.0, 1.0)})
    _put(conn, "SPY", "tprem", 1, {100.0: _cell(6.0, 1.0)})
    _put(conn, "QQQ", "tprem", 0, {50.0: _cell(9.0, 0.0)})
    _put(conn, "QQQ", "tprem", 1, {50.0: _cell(4.0, 0.0)})
    _put(conn, "SPY", "gex", 0, {100.0: _cell(1.0, 1.0)})       # another view
    result = tool.check(conn, DAY)
    assert (result["symbols"], result["rows"], result["steps"]) == (2, 4, 4)
    assert (result["fell"], result["dollars"]) == (1, 5.0)
    assert result["worst"] == [(5.0, "QQQ", 1)]


def test_sizes_are_counted_by_view():
    conn = _db()
    _put(conn, "SPY", "tprem", 0, {100.0: _cell(5.0, 1.0)})
    _put(conn, "SPY", "tprem", 1, {100.0: _cell(6.0, 1.0)})
    _put(conn, "SPY", "gex", 0, {100.0: _cell(1.0, 1.0)})
    by_view = tool.sizes(conn, DAY)
    assert set(by_view) == {"gex", "tprem"}
    assert by_view["tprem"][0] == 2 and by_view["tprem"][1] > 0


def test_the_report_calls_a_fall_a_bug_and_says_what_the_view_adds():
    result = dict(CLEAN, fell=1, dollars=5.0, worst=[(5.0, "QQQ", 1)])
    text = "\n".join(tool.report(DAY, result, {"gex": (2, 800), "tprem": (2, 100)}))
    assert "must never fall" in text and "QQQ: 1 falls, $5" in text
    assert "tprem adds 12.5% to the day's grid bytes" in text


def test_a_clean_day_is_reported_clean():
    text = "\n".join(tool.report(DAY, CLEAN, {"gex": (2, 800), "tprem": (2, 100)}))
    assert "no side of any strike ever fell" in text


def test_the_report_says_when_nothing_is_stored():
    result = dict(CLEAN, symbols=0, rows=0, steps=0)
    assert "nothing stored" in "\n".join(tool.report(DAY, result, {"gex": (2, 800)}))
```

**Step 2: Run them to make sure they fail**

```bash
"$PY" -m pytest tools/tests/test_check_traded_premium.py -q
```

Expected: an import error, `cannot import name 'check_traded_premium'`.

**Step 3: Write the tool**

Create `tools/check_traded_premium.py`:

```python
"""Does the stored traded premium ever fall, and what does it cost to store.

``services/options_svc/traded_premium.py`` stores, per strike per poll, a
RUNNING TOTAL of traded premium (the ``tprem`` view). A running total can never
fall, so on each side (calls, puts) of each strike every fall is a bug. This
reads every symbol's stored rows for one day, counts the falls, and prints each
view's rows and bytes so what ``tprem`` adds can be set against the estimate
(about an eighth). Design:
docs/plans/2026-10-09-traded-premium-increment-design.md, "How Phase A proves
itself".

Read-only. Usage, from the repo root::

    .venv/bin/python tools/check_traded_premium.py
    .venv/bin/python tools/check_traded_premium.py --date 2026-10-12
    .venv/bin/python tools/check_traded_premium.py --date 2026-10-12 --db /path/to/gex_history.db

``--db`` reads a COPY of the database (a backup) immutably, which is only safe
for a file nothing is writing. Exit status 1 when any total fell.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sqlite3
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from repo_paths import OPTIONS_SCANNER  # noqa: E402

if str(OPTIONS_SCANNER) not in sys.path:
    sys.path.insert(0, str(OPTIONS_SCANNER))

import gex_history_db as gh  # noqa: E402

from tools import measure_prem_remark as remark  # noqa: E402

VIEW = "tprem"


def falls(rows):
    """``(steps, fell, dollars)`` over each side of each strike, every reading
    against the one before it whatever the time between them: a running total
    must not fall across a gap either."""
    steps = fell = 0
    dollars = 0.0
    for series in remark._side_series(rows).values():
        for (_t0, before), (_t1, after) in zip(series, series[1:]):
            steps += 1
            if after < before:
                fell += 1
                dollars += before - after
    return steps, fell, dollars


def symbols_with(conn, view, day):
    start, end = gh._local_unix_range(day)
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM snapshots "
        "WHERE view = ? AND ts >= ? AND ts < ? ORDER BY symbol",
        (view, start, end))]


def sizes(conn, day):
    """``{view: (rows, grid bytes)}`` for the day."""
    start, end = gh._local_unix_range(day)
    return {view: (n, size) for view, n, size in conn.execute(
        "SELECT view, COUNT(*), COALESCE(SUM(LENGTH(gex_json)), 0) FROM snapshots "
        "WHERE ts >= ? AND ts < ? GROUP BY view", (start, end))}


def check(conn, day, view=VIEW):
    out = {"symbols": 0, "rows": 0, "steps": 0, "fell": 0, "dollars": 0.0,
           "worst": []}
    for symbol in symbols_with(conn, view, day):
        rows = gh.load_date_with_grid(conn, symbol, view, date=day)
        steps, fell, dollars = falls(rows)
        out["symbols"] += 1
        out["rows"] += len(rows)
        out["steps"] += steps
        out["fell"] += fell
        out["dollars"] += dollars
        if fell:
            out["worst"].append((dollars, symbol, fell))
    out["worst"].sort(reverse=True)
    return out


def report(day, result, by_view, view=VIEW):
    lines = [f"{view} {day}: {result['symbols']} symbols, {result['rows']:,} rows, "
             f"{result['steps']:,} side-steps compared"]
    if not result["symbols"]:
        lines.append("  nothing stored: is [collection] traded_premium on?")
    elif result["fell"]:
        lines.append(f"  FELL {result['fell']:,} times, ${result['dollars']:,.0f} in "
                     "all. A traded total must never fall: this is a bug.")
        lines += [f"    {symbol}: {fell:,} falls, ${dollars:,.0f}"
                  for dollars, symbol, fell in result["worst"][:10]]
    else:
        lines.append("  no side of any strike ever fell")
    for name in sorted(by_view):
        n, size = by_view[name]
        lines.append(f"  {name:6} {n:8,} rows {size / 1e6:8.1f} MB")
    others = sum(size for name, (_n, size) in by_view.items() if name != view)
    if view in by_view and others > 0:
        lines.append(f"  {view} adds {100.0 * by_view[view][1] / others:.1f}% "
                     "to the day's grid bytes")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", help="session date, YYYY-MM-DD (default: today)")
    ap.add_argument("--db", help="read this COPY of gex_history.db, immutably")
    ap.add_argument("--view", default=VIEW)
    args = ap.parse_args(argv)
    day = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    try:
        conn = remark._connect(args.db)
    except sqlite3.Error as e:
        print(f"Cannot open the history database read-only: {e}", file=sys.stderr)
        return 2
    try:
        result = check(conn, day, args.view)
        print("\n".join(report(day, result, sizes(conn, day), args.view)))
    finally:
        conn.close()
    return 1 if result["fell"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 4: Run the tests**

```bash
"$PY" -m pytest tools/tests/test_check_traded_premium.py -q
```

Expected: PASS.

**Step 5: Run it once against the backup** (proves it opens a real store; the
backup predates the view, so "nothing stored" is the right answer):

```bash
"$PY" tools/check_traded_premium.py --date 2026-09-24 --db "E:/TradingBackups/prod_2026-09-25_1737/options-scanner/gex_history.db"
```

Expected: `tprem 2026-09-24: 0 symbols`, `nothing stored`, and five view lines
whose megabytes match the design's table (gex 28.0, prem 21.8, …). Read nothing
else in that folder: it also holds `.env` files.

**Step 6: Commit**

```bash
git add tools/check_traded_premium.py tools/tests/test_check_traded_premium.py
git commit -m "feat(tools): check that a stored traded-premium total never falls"
```

---

## Task 7: Documentation

Nothing a user sees on a page changes, so `page_help.py` and the User Guide are
not touched. The switch appears in Settings → Configuration and a sixth view
appears in the store, and those are documented.

**Files:**
- Modify: `CLAUDE.md` (two sentences, corrected in place)
- Modify: `options-scanner/CLAUDE.md` (one sentence)
- Modify: `docs/reference/config-files.md` (the `config/marketdata.toml` row)
- Modify: `docs/manuals/technical-reference/technical-reference.md` (+ rebuilt `.html` / `.docx`)
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/plans/2026-10-09-traded-premium-increment-design.md` (status)
- Modify: this plan ("As built")

**Step 1: `CLAUDE.md`**

1. In the folder map's `options-scanner/` row, after the sentence that ends
   "…and costs ~**+25%** on that DB." add:

   > A SIXTH, **`tprem`**, is written by `options_svc/traded_premium.py` (not by the collector) only while `config/marketdata.toml` `[collection] traded_premium` is on.

2. Under "Options, paper books and trade selection" → "Other standing facts",
   add one bullet:

   > - The stored premium (`prem`, `call_prem` / `put_prem`) is the day's volume at the CURRENT mark and falls when marks fall: never difference it or read a fall as money leaving. `tprem` is the running total that cannot fall (`options_svc/traded_premium.py`); a fall in it is a bug (`tools/check_traded_premium.py`).

Then:

```bash
"$PY" -m pytest tests/test_claude_md_size.py -q
```

Expected: PASS. If it fails, the file is at its ceiling: shorten the two
additions, do not raise the ceiling.

**Step 2: `options-scanner/CLAUDE.md`**

In "Gamma analytics", after "A carried chain is written to all five views and is
NOT passed to `on_chain`." add:

> The options service can add a sixth view, `tprem` (traded premium booked as it trades), from that same hook: see `services/options_svc/traded_premium.py`.

**Step 3: `docs/reference/config-files.md`**

In the `config/marketdata.toml` row, change "the collector's tail interval and
carry limits (`[collection]`)" to "the collector's tail interval and carry
limits, and the switch for recording traded premium (`[collection]`)", and add
`options_svc/traded_premium.py` to the row's list of readers.

**Step 4: The Technical Reference**

In `docs/manuals/technical-reference/technical-reference.md`, find the paragraph
that begins "A carried minute is still written to all five views". After that
paragraph add:

```markdown
**Traded premium, booked as it trades (optional, off as shipped).** The premium
the collector stores is each contract's volume for the day times its current
mark, so it is re-priced every minute and falls when marks fall. With
`collection.traded_premium` on (Settings → Configuration → Local market data →
Collector), the options service also keeps a running total in which each
minute's new volume is priced once, at that minute's mark, and stores it per
strike as a sixth view. That total cannot fall. It is an estimate: it is not
split into bought and sold, a minute's volume did not all trade at that minute's
mid, and it leaves out what traded before the regular open and during a restart.
No page reads it yet. It is not written for a carried minute, because a carried
chain holds no new volume. Once a session, after the 15:00 close, the options
service logs how much volume it booked beside how much the chain showed.
```

Rebuild the manuals:

```bash
"$PY" docs/manuals/build_docs.py
```

Expected: the Technical Reference `.html` and `.docx` change; `git status` shows
no other manual changed beyond its build stamp.

**Step 5: `docs/CHANGELOG.md`**

A new entry at the top, in the file's existing form (demote the current "Last
updated" to "Prior —" the way earlier entries were). Content:

- **What it is.** The options service can now store traded premium as a running
  total per strike: each minute's new volume priced once, at that minute's mark.
  Sixth view `tprem` in `gex_history.db`. Off as shipped; nothing reads it.
- **Why.** The stored premium is the day's volume at the current mark. Measured on
  stored sessions it fell in 33% to 41% of minutes, and `$SPX` on 2026-09-24
  peaked at $2.82B and closed at $1.98B. The heatmap's Premium value failed its
  gate on that.
- **How it is judged before anything reads it.** No cell ever falls
  (`tools/check_traded_premium.py`); the day's log line sets booked volume beside
  what the chain showed, with the share priced late and the same volume priced at
  `last`; pass and write times against the poll's minute; bytes against the
  one-eighth estimate.
- **Limits.** Unsigned. Priced at the minute's mid. Starts at the regular open.
  A restart, or switching it off and on, leaves the volume in between out.
  Nothing rebuilds past sessions.
- **Also.** The hedging-flow row writer moved to `options_svc/hiro_store.py` to
  make room under `compute.py`'s ceiling (lowered).
- **Configuration.** `config/marketdata.toml [collection] traded_premium`
  (false), `traded_premium_late_sec` (90). No restart.
- **Verified.** By the module's suite against a real in-memory store. There is no
  dev environment and no stored per-contract volume to replay, so it has not run
  against live chains; the first session with the switch on is that check.
- **Not confirmed by the user.** The price (mark), no stored baselines across a
  restart, no row on a carried minute.

No test counts, no commit SHAs.

**Step 6: The design and this plan**

In the design doc, change the status line to say Phase A is built and switched
off, and that the three proposals were built as written. Append an "As built"
section to this plan: anything that differed from the tasks, with the reason.

**Step 7: Commit**

```bash
git add CLAUDE.md options-scanner/CLAUDE.md docs
git commit -m "docs: traded premium booked as it trades, and how it is checked"
```

---

## Task 8: Whole suites, lint, and the hand-off

**Step 1: Run every suite this touched, each on its own**

```bash
"$PY" -m pytest services/options_svc -q -rf 2>&1 | tail -5
"$PY" -m pytest services/tests -q -rf 2>&1 | tail -5
"$PY" -m pytest shared -q -rf 2>&1 | tail -5
"$PY" -m pytest tools -q -rf 2>&1 | tail -5
"$PY" -m pytest tests -q -rf 2>&1 | tail -5
cd webgui && "$PY" -m pytest . -q -rf 2>&1 | tail -5; cd ..
```

(`options-scanner` has no code change here, only a sentence in its `CLAUDE.md`,
so its suite is not in the list.)

Expected: the failing SET equals the baseline's. Touch no file while a suite
runs.

**Step 2: Lint with the gate's rules**

```bash
"$PY" -m ruff check --select E9,F63,F7,F82,F401,F811 services/options_svc/traded_premium.py services/options_svc/hiro_store.py services/options_svc/compute.py tools/check_traded_premium.py shared/marketdata_config.py webgui/config_schema.py
```

Expected: `All checks passed!`

**Step 3: Report, and stop**

Report what was built, the suite results, and the three unconfirmed decisions.
Do **not** merge, push or promote: that is the user's call, and this branch also
carries the heatmap work that has not shipped.

---

## After a promote (the user's call; not a task to run unasked)

The feature ships off, so a promote changes nothing until the switch is turned.

1. **Turn it on.** Settings → Configuration → Local market data → Collector →
   "Record traded premium as it trades". It writes `config/local/marketdata.toml`
   and needs no restart. Turn it on before the open, or accept that the first day
   starts mid-session.
2. **During the first session,** watch the options service's degrade count on
   System Status. `options.traded_premium.*` should not appear.
3. **After the 15:00 close,** read the day's check:

```bash
ssh vps2 'journalctl --user --since today | grep traded_premium.day_check'
```

4. **Then check the store:**

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python tools/check_traded_premium.py'
```

**What good looks like.**

| Check | Pass |
|---|---|
| No cell ever falls | `no side of any strike ever fell`; exit status 0 |
| Volume reconciles | booked within a few percent of seen on the index symbols; the shortfall is volume with no usable mark |
| Priced late | small on one-minute symbols; near 100% on watchlist-only symbols while the tail interval is 3 (they are read every third minute, which is what "late" means there) |
| Cost | `pass` and `write` worst-case times a small part of the poll's minute; `tprem adds` near 13% |

**Turn it off** in the same place if a degrade appears, the write time is large,
or polls start being missed. Off drops the in-memory state; the stored rows stay
and age out with the other views after five sessions.

**After about a week of sessions,** decide from the logs: mark or `last` as the
price, whether restarts cost enough to store baselines, and which Phase B reader
to build first (the heatmap's Premium value is the one the design was written
for). Each reader is its own design decision and its own plan.

---

## As built: Tasks 1 to 4 (2026-10-09)

Built as written, with these differences.

- **One test more than the plan (Task 4).** Each rule was broken in turn to see
  that a test fails for it. Fourteen were caught. One was not: removing the
  three-decimal rounding where a chain's strike is READ. The plan's test proved
  only that the resume side rounds. `test_a_strike_is_keyed_the_same_way_read_or_resumed`
  now pins both to one key. No test was changed or loosened.
- **The module was written from the prototype** that had been run before the
  plan, not retyped from the plan's text; the state tests were copied out of the
  plan file. So the plan and the code agree by construction.
- **`compute.py`** is 10,485 lines and its ceiling 10,487, as expected. The two
  spare lines are Task 5's.

**Where this leaves the branch.** The switch is in Settings → Configuration and
the module is complete, but nothing calls it: until Task 5, turning the switch on
does nothing. Tasks 5 to 8 remain (the poll wiring, the check tool, the
documents, the whole suites). Do not promote between Task 4 and Task 5 without
saying so: a switch that does nothing is a trap.

**Verified.** The module's suite (41 tests) against a real in-memory store, and
the options service, service-guard and shared suites whole, all green. It has not
run against a live chain.
