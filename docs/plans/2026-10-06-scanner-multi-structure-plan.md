# Market Scanner — Structures Beyond Credit Spreads: Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The Market Scanner's 0-DTE and Swing tabs also build, show and track debit spreads, straddles, strangles, butterflies, condors, calendars, diagonals and ratio backspreads.

**Architecture:** A new pure engine module, `options-scanner/structure_scan.py`, runs the Strategy Finder's existing builders on the chains `run_full_scan` already holds and returns scored candidates in two new lists, `structures_0dte` and `structures_swing`. The credit-spread lists, their score and every reader of them are untouched. Capture reuses `signals.db` with additive columns, two new scanner types, and two guards that keep the paper Account from opening any of it.

**Tech stack:** Python 3.11, pytest, NiceGUI (Tier 1), Redis via `shared.bus`, SQLite (`signals.db`), TOML config through `shared/config_toml.toml_loader`.

**Design:** [2026-10-06-scanner-multi-structure-design.md](2026-10-06-scanner-multi-structure-design.md). Read it first.

---

## Before you start

**Read these, in this order:**

1. Root `CLAUDE.md`: "3-tier architecture", "Options, paper books and trade selection", "Tests".
2. `options-scanner/CLAUDE.md`.
3. `docs/reference/options-engine-invariants.md`.
4. `docs/plans/2026-09-13-strategy-finder-all-structures-design.md`: the measurements the existing builders rest on.

**The Python to use.** The worktree has no venv of its own:

```powershell
$PY = "D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe"
```

**How each suite runs** (never `pytest services` over several folders):

| Code under | Run from | Command |
|---|---|---|
| `options-scanner/` | `options-scanner/` | `& $PY -m pytest tests/<file> -q -rf` |
| `shared/` | repo root | `& $PY -m pytest shared/tests/<file> -q -rf` |
| `services/options_svc/` | repo root | `& $PY -m pytest services/options_svc/tests/<file> -q -rf` |
| `webgui/` | `webgui/` | `& $PY -m pytest tests/<file> -q -rf` |
| `tools/` | repo root | `& $PY -m pytest tools/tests/<file> -q -rf` |

**Rules that have each cost this repo real time:**

- Take a baseline of each suite you will touch before the first edit (`-q -rf`, save the output). Afterwards compare the failing **set** and the skipped **set**, never the count.
- **Never weaken, narrow or delete an existing assertion to make a test pass.** If an existing test fails, stop and report it.
- Write test files with the editor. A heredoc mangles backslash escapes into a test that asserts nothing.
- A test that proves a config value is **read** monkeypatches the accessor and `importlib.reload`s the consumer. Asserting the shipped value proves nothing.
- A new operator-tunable number goes in `config/scanner.toml`, `shared/scanner_config.py` and `webgui/config_schema.py` in the same commit.
- A user-visible change updates `webgui/page_help.py` and the manuals in the same commit.
- No `.style(...)`, no raw `ui.button` / `ui.table` / `ui.dialog` / `ui.notify` on a page. Use `pages/ui_kit.py`.
- New service code does not go in `services/options_svc/compute.py` unless it is wiring; that file's line count has a ceiling test.
- Commit after every task. End each commit message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nothing reaches prod except through `tools/promote.sh`, after the close.

**Vocabulary used below:**

- *Normalized candidate*: the dict `strategy_scanner._assemble` returns. It has a `legs` list, per-contract dollar figures (`net_debit` or `net_credit`, `max_profit`, `max_loss`, `capital`), `breakevens`, `pop_pct`, `rr`, position-signed `net_delta` / `net_theta` / `net_vega`, `type`, `family`, `expiration` (the front one) and `dte`.
- *Window*: the scan's two DTE ranges. "0-DTE" is DTE 0..4; "SWING" is DTE 5..15. The engine's own trade-type strings are `"0-DTE"` and `"SWING"`; the recorded bucket spellings are `0DTE` and `SWING`.

---

# Phase 1 — Measure

The gate bars were measured at 14, 30 and 45 days. This phase finds out what passes at 0 to 15 days. **It ends with a report to the operator and a stop.**

### Task 1: A front-DTE parameter on the two builders that hard-code seven days

**Files:**
- Modify: `options-scanner/strategy_scanner.py` (`_front_pair`, `build_straddles_strangles`, `build_butterflies_condors`)
- Create: `options-scanner/tests/_bs_chain.py`
- Test: `options-scanner/tests/test_strategy_scanner.py`

**Step 1: Add the test chain helper**

`options-scanner/tests/_bs_chain.py`. It mirrors `tools/sweep_strategy_gates.chain`, which the tests cannot import.

```python
"""A Schwab-shaped Black-Scholes chain for builder tests. Expirations are dated
from TODAY, because ``strategy_scanner._dte_for`` reads the real date."""
import datetime as dt

import options_calculator as oc


def bs_chain(spot=100.0, iv=0.28, days=(2,), step=1.0, span=0.30):
    r = oc.RISK_FREE_RATE
    out = {"underlyingPrice": spot, "callExpDateMap": {}, "putExpDateMap": {}}
    n = int(spot * span / step)
    for d in days:
        key = f"{(dt.date.today() + dt.timedelta(days=d)).isoformat()}:{d}"
        T = max(d, 0.5) / 365.0
        for kind, m in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            side = {}
            for i in range(-n, n + 1):
                K = round(spot + i * step, 2)
                mark = round(max(oc.bs_price(spot, K, T, r, iv, kind), 0.01), 2)
                side[f"{K}"] = [{
                    "delta": round(oc.bs_delta(spot, K, T, r, iv, kind), 4),
                    "mark": mark,
                    "bid": round(mark * 0.98, 2), "ask": round(mark * 1.02, 2),
                    "theta": round(float(oc.bs_theta(spot, K, T, r, iv, kind)), 4),
                    "vega": round(float(oc.bs_vega(spot, K, T, r, iv, kind)), 4),
                    "gamma": round(float(oc.bs_gamma(spot, K, T, r, iv, kind)), 5),
                    "volatility": iv * 100,
                    "totalVolume": 500, "openInterest": 2000}]
            out[m][key] = side
    return out
```

**Step 2: Write the failing tests** (append to `test_strategy_scanner.py`)

```python
from tests._bs_chain import bs_chain


def test_the_seven_day_floor_is_still_the_default():
    chain = bs_chain(days=(2, 9))
    out = ss.build_straddles_strangles(chain, "T", 100.0, 0.28, 0, 30)
    assert out and {s["dte"] for s in out} == {9}
    flies = ss.build_butterflies_condors(chain, "T", 100.0, 0.28, 0, 30)
    assert {s["dte"] for s in flies} <= {9}


def test_a_caller_can_lower_the_front_floor():
    chain = bs_chain(days=(2, 9))
    out = ss.build_straddles_strangles(chain, "T", 100.0, 0.28, 0, 30,
                                       min_front_dte=0)
    assert out and {s["dte"] for s in out} == {2}
    flies = ss.build_butterflies_condors(chain, "T", 100.0, 0.28, 0, 30,
                                         min_front_dte=0)
    assert {s["dte"] for s in flies} <= {2}


def test_the_default_floor_output_is_unchanged_by_the_parameter():
    chain = bs_chain(days=(9, 37))
    plain = ss.build_straddles_strangles(chain, "T", 100.0, 0.28, 0, 60)
    explicit = ss.build_straddles_strangles(chain, "T", 100.0, 0.28, 0, 60,
                                            min_front_dte=7)
    strip = lambda rows: [{k: v for k, v in r.items() if k != "timestamp"} for r in rows]
    assert strip(plain) == strip(explicit)
```

**Step 3: Run them and watch them fail**

Run: `& $PY -m pytest tests/test_strategy_scanner.py -q -rf -k "floor"`
Expected: 2 fail with `TypeError: ... unexpected keyword argument 'min_front_dte'`; the default test passes.

**Step 4: Implement**

```python
def _front_pair(chain, dte_min, dte_max, min_front_dte=None):
    # ... docstring: add one sentence - the Market Scanner passes its window's
    # own minimum; the Strategy Finder passes nothing and keeps 7.
    floor = max(dte_min, _MIN_FRONT_DTE if min_front_dte is None else min_front_dte)
```

Add `min_front_dte=None` as the last parameter of `build_straddles_strangles` and `build_butterflies_condors`, and hand it to `_front_pair`. Do not touch `build_calendars` or `build_stock_structures`.

**Step 5: Run the whole file**

Run: `& $PY -m pytest tests/test_strategy_scanner.py tests/test_strategy_scoring.py -q -rf`
Expected: the same failing set as the baseline (none), plus the three new passes.

**Step 6: Commit**

```bash
git add options-scanner/strategy_scanner.py options-scanner/tests/_bs_chain.py options-scanner/tests/test_strategy_scanner.py
git commit -m "feat(scanner): a front-DTE parameter on the straddle and butterfly builders"
```

### Task 2: The sweep reaches short expiries

**Files:**
- Modify: `tools/sweep_strategy_gates.py` (`rows`, `main`)
- Test: `tools/tests/test_sweep_strategy_gates.py`

**Step 1: Failing test**

```python
def test_the_sweep_can_measure_a_two_day_front():
    out = list(sweep.rows(100.0, 0.28, 2, 1.0, min_front_dte=0))
    types = {r["type"] for r in out}
    assert "LONG_STRADDLE" in types and "BUTTERFLY_CALL" in types
    assert {r["dte"] for r in out if r["type"] == "LONG_STRADDLE"} == {2}
```

Match the import name the file already uses for the tool.

**Step 2:** Run it. Expected: `TypeError` on `min_front_dte`.

**Step 3: Implement.** `rows(spot, iv, front_days, step, rich=1.0, min_front_dte=None)`. Add `build_debit_verticals` to `BUILDERS`. Pass `min_front_dte` only to `build_straddles_strangles` and `build_butterflies_condors`:

```python
FLOORED = ("build_straddles_strangles", "build_butterflies_condors")
...
        if name in FLOORED and min_front_dte is not None:
            kw["min_front_dte"] = min_front_dte
```

In `main`: `ap.add_argument("--min-front-dte", type=int, default=None)` and thread it through. Update the module docstring's builder count if a test pins it.

**Step 4:** Run `tools/tests/test_sweep_strategy_gates.py`. Expected: all pass.

**Step 5: Commit** — `feat(tools): sweep the strategy gates at short expiries`.

### Task 3: Run the measurement and report it

**No code.** Run the sweep on the grid below and paste the output tables into the design doc under a new heading, `## Measured, 2026-10-xx`, each table with its parameters.

```powershell
foreach ($iv in 0.20, 0.28, 0.45) { foreach ($step in 1, 2.5, 5) {
  & $PY tools/sweep_strategy_gates.py --days 0,1,2,4,7,10,15 --min-front-dte 0 --iv $iv --step $step
} }
```

Then write, in the same section, one line per structure and window: passes, fails (and on which bar), or depends on the ladder. Do not move a bar.

**STOP. Report the table to the operator.** Phase 2 proceeds unchanged unless the operator says otherwise. If a whole family never passes in a window, say so plainly; the funnel will show it as "below the quality bar".

Commit: `docs(scanner): measured gate results at 0 to 15 days`.

---

# Phase 2 — Show

> **Changed by Phase 1's measurement (2026-10-06).** The cap is per FAMILY, not
> one number per window: the config key is `max_per_family = 2` in place of
> `max_per_symbol_window = 6`, and `structure_scan.select` keeps the best
> `max_per_family` rows of each `group` (its `capped` counter and the partition
> identity are unchanged). Tasks 4, 6 and 7 below still show the single cap;
> apply this change when executing them. The sweep also gained a `--scanner`
> mode with a pinned clock in place of the `--min-front-dte` flag Task 2
> describes; Task 17's backspread measurement uses `--scanner`.

### Task 4: Configuration

**Files:**
- Modify: `config/scanner.toml`, `shared/scanner_config.py`, `webgui/config_schema.py`
- Test: `shared/tests/test_scanner_config.py`, `webgui/tests/test_config_schema.py`

**Step 1: Failing tests** (`test_scanner_config.py`; follow the file's existing pattern for pointing the loader at a temp TOML)

```python
def test_structures_defaults():
    s = scanner_config.structures()
    assert s == {"enabled": True,
                 "families": ["VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR"],
                 "min_score": 50.0, "excluded_grades": ["Weak"],
                 "max_per_symbol_window": 6, "short_delta_min": 0.15,
                 "earnings_long_premium": "flag"}


def test_structures_rejects_what_it_cannot_use(tmp_toml):
    tmp_toml('[structures]\nfamilies = ["VERTICAL", "TYPO"]\n'
             'earnings_long_premium = "maybe"\nmax_per_symbol_window = -1\n'
             'min_score = "high"\n')
    s = scanner_config.structures()
    assert s["families"] == ["VERTICAL"]            # an unknown family is dropped
    assert s["earnings_long_premium"] == "flag"     # an unknown mode is the default
    assert s["max_per_symbol_window"] == 6
    assert s["min_score"] == 50.0
```

**Step 2:** Run. Expected: `AttributeError: ... has no attribute 'structures'`.

**Step 3: Implement** in `shared/scanner_config.py`:

```python
    # Structures other than credit spreads on the Market Scanner's two tabs.
    # See config/scanner.toml [structures].
    "structures": {
        "enabled": True,
        "families": ["VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR"],
        "min_score": 50.0,
        "excluded_grades": ["Weak"],
        "max_per_symbol_window": 6,
        "short_delta_min": 0.15,
        "earnings_long_premium": "flag",
    },
```

```python
STRUCTURE_FAMILIES = ("VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR", "RATIO")
EARNINGS_MODES = ("flag", "drop")


def structures() -> dict:
    """The Market Scanner's non-credit structures pass. Closed over the defaults,
    like :func:`selection`: an unknown key is dropped and an unusable value is the
    shipped one, so a typo can never read as a gate that is on and never gates."""
    d = DEFAULTS["structures"]
    sec = load().get("structures")
    sec = sec if isinstance(sec, dict) else {}
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in d.items()}
    if isinstance(sec.get("enabled"), bool):
        out["enabled"] = sec["enabled"]
    fams = sec.get("families")
    if isinstance(fams, list):
        out["families"] = [f for f in (str(x).strip().upper() for x in fams)
                           if f in STRUCTURE_FAMILIES]
    grades = sec.get("excluded_grades")
    if isinstance(grades, list):
        out["excluded_grades"] = [str(g).strip().capitalize() for g in grades if str(g).strip()]
    for key in ("min_score", "short_delta_min"):
        v = sec.get(key)
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v >= 0):
            out[key] = float(v)
    v = sec.get("max_per_symbol_window")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        out["max_per_symbol_window"] = v
    if sec.get("earnings_long_premium") in EARNINGS_MODES:
        out["earnings_long_premium"] = sec["earnings_long_premium"]
    return out
```

`config/scanner.toml`, after `[single_leg]`:

```toml
[structures]
# Structures OTHER than credit spreads on the Market Scanner's 0-DTE and Swing
# tabs: debit spreads, straddles and strangles, butterflies and condors,
# calendars and diagonals. Scored on the Strategy Finder's Fit + Quality model,
# in their own lists; nothing here changes a credit spread. Edit + restart
# options_svc.
enabled = true
# Which families the scan builds. CALENDAR is built on the Swing tab only.
families = ["VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR"]
min_score = 50.0
excluded_grades = ["Weak"]
# Most rows one symbol may show in one tab's "Other structures" table.
max_per_symbol_window = 6
# A short strangle sells between this delta and [selection]
# max_entry_short_delta, aiming at the midpoint.
short_delta_min = 0.15
# A trade that would be held through an earnings report: "flag" keeps LONG
# premium and marks the row; "drop" removes it. SHORT premium is always dropped.
earnings_long_premium = "flag"
```

`webgui/config_schema.py`: a `Section("Other structures (Market Scanner)", ...)` beside the single-leg one, one `Field` per key. `families` and `excluded_grades` are `kind="symbols"`; `earnings_long_premium` is `kind="choice", choices=("flag", "drop")` (copy the form of `trail.active`); `excluded_grades` needs the same capitalising branch `single_leg.excluded_grades` has near line 2164.

**Step 4:** Run both test files. `test_config_schema.py` fails on any key the catalogue lacks; it must pass.

**Step 5: Commit** — `feat(config): [structures] for the Market Scanner's non-credit pass`.

### Task 5: `structure_scan.py` — building one window

**Files:**
- Create: `options-scanner/structure_scan.py`
- Modify: `options-scanner/strategy_scanner.py` (add `latest_expiration`), `services/options_svc/compute.py` (`_latest_expiration` delegates)
- Test: `options-scanner/tests/test_structure_scan.py`

**Step 1: Failing tests**

```python
import structure_scan as sx
from tests._bs_chain import bs_chain

ALL = ("VERTICAL", "STRADDLE", "BUTTERFLY", "CALENDAR")


def _types(rows):
    return {r["type"] for r in rows}


def test_merge_chains_holds_both_chains_expirations():
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    merged = sx.merge_chains(front, back)
    assert len(merged["callExpDateMap"]) == 2 and len(merged["putExpDateMap"]) == 2
    assert merged["underlyingPrice"] == front["underlyingPrice"]
    assert len(front["callExpDateMap"]) == 1          # inputs are not mutated


def test_merge_chains_survives_a_missing_or_failed_back_chain():
    front = bs_chain(days=(9,))
    assert sx.merge_chains(front, None) is front
    assert sx.merge_chains(front, {"status": "FAILED"}) is front


def test_the_short_window_builds_two_day_structures():
    rows = sx.build_window(bs_chain(days=(2,)), "T", 100.0, 0.28, 0, 4,
                           families=ALL, short_band=(0.15, 0.27))
    assert {"BULL_CALL", "BEAR_PUT", "LONG_STRADDLE", "LONG_STRANGLE"} <= _types(rows)
    assert all(r["dte"] == 2 for r in rows)
    assert not any(t.startswith(("CALENDAR", "DIAGONAL")) for t in _types(rows))


def test_calendars_need_the_back_chain():
    front, back = bs_chain(days=(9,)), bs_chain(days=(37,))
    without = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                              short_band=(0.15, 0.27))
    with_back = sx.build_window(front, "T", 100.0, 0.28, 5, 15, families=ALL,
                                short_band=(0.15, 0.27), back_chain=back,
                                back_dte_max=45)
    assert "CALENDAR_CALL" not in _types(without)
    assert "CALENDAR_CALL" in _types(with_back)


def test_every_row_carries_its_build_group():
    rows = sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                           families=ALL, short_band=(0.15, 0.27))
    assert rows and all(r["group"] in ALL for r in rows)


def test_a_family_left_out_is_not_built():
    rows = sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                           families=("VERTICAL",), short_band=(0.15, 0.27))
    assert _types(rows) <= {"BULL_CALL", "BEAR_PUT"}


def test_the_short_strangle_respects_the_band_ceiling():
    rows = sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                           families=("STRADDLE",), short_band=(0.15, 0.27))
    for r in rows:
        if r["type"] == "SHORT_STRANGLE":
            assert all(abs(l["delta"]) <= 0.27 for l in r["legs"])
```

**Step 2:** Run. Expected: `ModuleNotFoundError: structure_scan`.

**Step 3: Implement**

`strategy_scanner.py`, beside `_front_expiration`:

```python
def latest_expiration(sig):
    """The LAST expiration a candidate is exposed to. A calendar's back month can
    span a report its front leg expires ahead of, so an earnings check reads this,
    not ``sig["expiration"]`` (the front)."""
    exps = [l.get("expiration") for l in (sig or {}).get("legs") or []
            if l.get("expiration")]
    return max(exps) if exps else (sig or {}).get("expiration")
```

In `compute.py`, replace the body of `_latest_expiration` with a call to it (keep the name; other code calls it).

`options-scanner/structure_scan.py`:

```python
"""Structures other than credit spreads for the Market Scanner's two windows.

PURE. Chains, a price and a market view in; scored candidates out. No Schwab
call, no store. ``scanner_engine.run_full_scan`` calls this in a guarded block,
the way it calls the single-leg pass, and puts the result in its OWN lists
(``structures_0dte`` / ``structures_swing``): ten readers of the credit lists
assume the credit shape.

The builders are the Strategy Finder's (``strategy_scanner``), so a candidate is
the same row on both surfaces. Design: docs/plans/2026-10-06-scanner-multi-
structure-design.md.
"""
import logging
import math
import pathlib as _pathlib
import sys as _sys

import strategy_scanner as _ssn
import strategy_scoring as _ssc

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared import vol_gate as _vol_gate  # noqa: E402

log = logging.getLogger("structure_scan")


def merge_chains(front, back):
    """One chain holding both chains' expirations, for a calendar's two months.
    The front chain's own fields (the underlying price among them) win. Neither
    input is mutated; an unusable ``back`` returns ``front`` itself."""
    if not back or back.get("status") == "FAILED":
        return front
    out = dict(front)
    for key in ("callExpDateMap", "putExpDateMap"):
        merged = dict(back.get(key) or {})
        merged.update(front.get(key) or {})
        out[key] = merged
    return out


def _tag(batch, group):
    batch = list(batch)
    for s in batch:
        s["group"] = group
    return batch


def build_window(chain, symbol, spot, atm_iv, dte_min, dte_max, *, families,
                 short_band, back_chain=None, back_dte_max=None):
    """Every candidate one window offers, unscored.

    ``short_band`` is ``(lo, hi)`` absolute deltas for a short strangle's legs.
    ``back_chain`` / ``back_dte_max`` supply a calendar's later month; without
    them no calendar or diagonal is built.
    """
    fams = set(families or ())
    lo, hi = short_band
    out = []
    if "VERTICAL" in fams:
        out += _tag(_ssn.build_debit_verticals(chain, symbol, spot, atm_iv,
                                               dte_min, dte_max), "VERTICAL")
    if "STRADDLE" in fams:
        out += _tag(_ssn.build_straddles_strangles(
            chain, symbol, spot, atm_iv, dte_min, dte_max,
            put_band=(-hi, -lo), call_band=(lo, hi),
            min_front_dte=dte_min), "STRADDLE")
    if "BUTTERFLY" in fams:
        out += _tag(_ssn.build_butterflies_condors(
            chain, symbol, spot, atm_iv, dte_min, dte_max,
            min_front_dte=dte_min), "BUTTERFLY")
    if "CALENDAR" in fams and back_chain is not None and back_dte_max:
        out += _tag(_ssn.build_calendars(merge_chains(chain, back_chain), symbol,
                                         spot, atm_iv, dte_min, back_dte_max),
                    "CALENDAR")
    return out
```

**Step 4:** Run `tests/test_structure_scan.py` and, from the repo root, `services/options_svc/tests/` files that cover the Finder's earnings flag (`-k earnings`). Expected: all pass.

**Step 5: Commit** — `feat(scanner): structure_scan builds a window from the Finder's builders`.

### Task 6: `structure_scan.select` — the gates, in one place

**Files:**
- Modify: `options-scanner/structure_scan.py`
- Test: `options-scanner/tests/test_structure_scan.py`

The order is fixed and each stage counts what it removed, so the funnel bucket partitions: `built == vol_gate + earnings + score_cut + capped + (what is returned)`.

**Step 1: Failing tests**

```python
NEUTRAL = {"direction": "neutral", "conviction": 0.1, "vol_regime": "mid"}


def _row(t, score, vega, dte=9, grade="Good"):
    # A pre-scored row: select() is tested with scoring stubbed out.
    return {"type": t, "id": f"T_{t}", "symbol": "T", "dte": dte,
            "composite_score": score, "grade": grade, "net_vega": vega,
            "expiration": "2099-01-01", "legs": []}


def _select(rows, monkeypatch, **over):
    monkeypatch.setattr(sx._ssc, "score_all", lambda sigs, *a, **k: list(sigs))
    kw = dict(view=NEUTRAL, atm_iv=0.28, daily_em=1.5, dte_min=5, iv_rank=50,
              floor=30, ceiling=0, spans_earnings=lambda s: False,
              earnings_date=None, keep_long_through_earnings=True,
              min_score=50.0, excluded_grades=("Weak",), max_per_window=6)
    kw.update(over)
    bucket = {}
    return sx.select(rows, bucket=bucket, **kw), bucket


def test_short_premium_under_the_floor_is_gated_and_long_is_not(monkeypatch):
    rows = [_row("SHORT_STRANGLE", 70, -0.2), _row("LONG_STRADDLE", 60, 0.3)]
    kept, b = _select(rows, monkeypatch, iv_rank=10)
    assert [r["type"] for r in kept] == ["LONG_STRADDLE"]
    assert b["built"] == 2 and b["vol_gate"] == 1


def test_long_premium_through_a_report_is_kept_and_flagged(monkeypatch):
    rows = [_row("LONG_STRADDLE", 60, 0.3), _row("SHORT_STRANGLE", 70, -0.2)]
    kept, b = _select(rows, monkeypatch, spans_earnings=lambda s: True,
                      earnings_date="2099-01-02")
    assert [r["type"] for r in kept] == ["LONG_STRADDLE"]
    assert kept[0]["spans_earnings"] is True
    assert kept[0]["earnings_date"] == "2099-01-02"
    assert b["earnings"] == 1


def test_drop_mode_removes_long_premium_too(monkeypatch):
    kept, b = _select([_row("LONG_STRADDLE", 60, 0.3)], monkeypatch,
                      spans_earnings=lambda s: True,
                      keep_long_through_earnings=False)
    assert kept == [] and b["earnings"] == 1


def test_a_row_with_no_readable_vega_is_dropped_through_a_report(monkeypatch):
    kept, _ = _select([_row("BUTTERFLY_CALL", 60, None)], monkeypatch,
                      spans_earnings=lambda s: True)
    assert kept == []


def test_no_report_means_no_flag(monkeypatch):
    kept, b = _select([_row("LONG_STRADDLE", 60, 0.3)], monkeypatch)
    assert "spans_earnings" not in kept[0] and b["earnings"] == 0


def test_the_quality_cut_and_the_cap(monkeypatch):
    rows = [_row("A", 49, 0.1), _row("B", 80, 0.1, grade="Weak"),
            _row("C", 55, 0.1), _row("D", 75, 0.1), _row("E", 65, 0.1)]
    kept, b = _select(rows, monkeypatch, max_per_window=2)
    assert [r["type"] for r in kept] == ["D", "E"]       # best first
    assert b["score_cut"] == 2 and b["capped"] == 1


def test_the_bucket_partitions(monkeypatch):
    rows = [_row("SHORT_STRANGLE", 70, -0.2), _row("A", 40, 0.1),
            _row("B", 60, 0.1), _row("C", 61, 0.1), _row("D", 62, 0.1)]
    kept, b = _select(rows, monkeypatch, iv_rank=10, max_per_window=2)
    assert b["built"] == (b["vol_gate"] + b["earnings"] + b["score_cut"]
                          + b["capped"] + len(kept))


def test_no_bucket_changes_nothing(monkeypatch):
    rows = [_row("B", 60, 0.1), _row("C", 61, 0.1)]
    with_bucket, _ = _select([dict(r) for r in rows], monkeypatch)
    monkeypatch.setattr(sx._ssc, "score_all", lambda sigs, *a, **k: list(sigs))
    without = sx.select([dict(r) for r in rows], view=NEUTRAL, atm_iv=0.28,
                        daily_em=1.5, dte_min=5, iv_rank=50, floor=30, ceiling=0,
                        spans_earnings=lambda s: False, earnings_date=None,
                        keep_long_through_earnings=True, min_score=50.0,
                        excluded_grades=("Weak",), max_per_window=6)
    assert with_bucket == without


def test_real_builders_and_real_scoring_produce_scored_rows():
    from tests._bs_chain import bs_chain
    cands = sx.build_window(bs_chain(days=(9,)), "T", 100.0, 0.28, 5, 15,
                            families=ALL, short_band=(0.15, 0.27))
    kept = sx.select(cands, view=NEUTRAL, atm_iv=0.28,
                     daily_em=100 * 0.28 * (1 / 365) ** 0.5, dte_min=5, iv_rank=50,
                     floor=30, ceiling=0, spans_earnings=lambda s: False,
                     earnings_date=None, keep_long_through_earnings=True,
                     min_score=0.0, excluded_grades=(), max_per_window=99)
    assert kept and all("composite_score" in r and "grade" in r for r in kept)
    scores = [r["composite_score"] for r in kept]
    assert scores == sorted(scores, reverse=True)
```

**Step 2:** Run. Expected: `AttributeError: ... 'select'`.

**Step 3: Implement**

```python
def _add(bucket, key, n):
    if bucket is not None and n:
        bucket[key] = bucket.get(key, 0) + n


def select(candidates, *, view, atm_iv, daily_em, dte_min, iv_rank, floor,
           ceiling, spans_earnings, earnings_date, keep_long_through_earnings,
           min_score, excluded_grades, max_per_window, bucket=None):
    """Score one window's candidates and apply the Scanner's gates, in order:
    volatility, earnings, quality, cap. Returns the survivors best first.

    ``bucket`` (optional) receives ``built`` and one counter per stage, so that
    ``built == vol_gate + earnings + score_cut + capped + len(result)``. Counting
    never moves a decision: with ``bucket=None`` the result is identical.

    Earnings is decided on the candidate's own VEGA SIGN, the key the volatility
    gate uses: long premium is kept and flagged when
    ``keep_long_through_earnings``; short premium, and a row whose vega cannot be
    read, is dropped.
    """
    # The same scoring call the single-leg pass and the Strategy Finder make, so
    # one candidate scores the same on every surface. ``em_1sd`` is only the
    # fallback when the daily move is unusable.
    em_1sd = (daily_em or 0.0) * math.sqrt(max(dte_min, 1))
    scored = _ssc.score_all(candidates, view, atm_iv, em_1sd, daily_move=daily_em)
    _add(bucket, "built", len(scored))
    if bucket is not None:
        bucket.setdefault("built", 0)

    kept = [s for s in scored
            if not _vol_gate.signal_blocks(s, floor=floor, ceiling=ceiling,
                                           iv_rank=iv_rank)]
    _add(bucket, "vol_gate", len(scored) - len(kept))

    through = []
    for s in kept:
        if spans_earnings(s):
            long_premium = (_vol_gate.premium_side(s.get("net_vega"))
                            is _vol_gate.LONG_PREMIUM)
            if not (keep_long_through_earnings and long_premium):
                continue
            s["spans_earnings"], s["earnings_date"] = True, earnings_date
        through.append(s)
    _add(bucket, "earnings", len(kept) - len(through))

    passing = [s for s in through
               if (s.get("composite_score") or 0) >= min_score
               and s.get("grade") not in excluded_grades]
    _add(bucket, "score_cut", len(through) - len(passing))

    passing.sort(key=lambda s: (s.get("composite_score") or 0), reverse=True)
    out = passing[:max_per_window] if max_per_window else passing
    _add(bucket, "capped", len(passing) - len(out))
    return out
```

Check `shared/vol_gate.py` exports `premium_side` and `LONG_PREMIUM` under those names before relying on them.

**Step 4:** Run the file. Expected: all pass.

**Step 5: Commit** — `feat(scanner): structure_scan.select applies the volatility, earnings, quality and cap gates`.

### Task 7: Wire the pass into `run_full_scan`

**Files:**
- Modify: `options-scanner/scanner_engine.py` (`run_full_scan`, module constants)
- Test: `options-scanner/tests/test_scan_funnel.py`, `options-scanner/tests/test_scanner_engine.py`

Read `TestScanFunnel` in `test_scan_funnel.py` first and reuse its fake client and `run_full_scan` fixture; do not build a second one.

**Step 1: Failing tests** (names; write them against the existing fixture)

- `test_the_scan_result_carries_the_two_structure_lists`: both keys exist and are lists.
- `test_the_credit_lists_do_not_change_when_the_pass_is_off`: run once with `scanner_engine.STRUCTURES_CFG` patched to `enabled=False` and once on; `signals_0dte`, `signals_swing` and `signals_directional` are equal (strip `timestamp`).
- `test_a_crash_in_the_pass_leaves_every_other_list_alone`: monkeypatch `structure_scan.build_window` to raise; the three existing lists equal the pass-off run and the symbol's `STRUCT_0DTE` and `STRUCT_SWING` buckets carry `build_failed is True`.
- `test_the_structure_buckets_partition`: for every symbol and both buckets, `built == vol_gate + earnings + score_cut + capped + outside_rth + emitted`.
- `test_counting_never_moves_a_structure_decision`: `collect_funnel=True` and `False` give equal structure lists (extend the existing equivalence test rather than copying it).
- `test_outside_regular_hours_holds_the_structure_lists`: with the signal clock outside the session both lists are empty and `outside_rth` carries the count.
- `test_structure_rows_carry_the_symbol_iv_block`: each row has `iv_rank`, `current_iv`, `expected_moves`.

**Step 2:** Run. Expected: `KeyError: 'structures_0dte'`.

**Step 3: Implement**

Module constants, beside `SINGLE_LEG_*`:

```python
# The non-credit structures pass (structure_scan). config/scanner.toml [structures].
STRUCTURES_CFG = _scfg.structures()
STRUCT_FUNNEL_KEYS = ("built", "vol_gate", "earnings", "score_cut", "capped",
                      "outside_rth", "emitted")
# Funnel buckets whose counters sit flat on the bucket (no ``spreads`` table).
FLAT_FUNNEL_BUCKETS = ("DIRECTIONAL", "STRUCT_0DTE", "STRUCT_SWING")
```

Extract the ATM-volatility arithmetic the single-leg block computes inline into a module function, and call it from that block (a pure refactor; its tests must not move):

```python
def decimal_atm_iv(daily_em, price, iv_data):
    """ATM IV as a DECIMAL fraction, from the engine's dollar daily expected move
    (``dem = spot * iv * sqrt(1/365)``). ``run_iv_analysis``'s ``current_iv`` is a
    PERCENT - the documented trap - and is only the fallback."""
    if daily_em and price and price > 0:
        return (daily_em * math.sqrt(365.0)) / price
    civ = (iv_data or {}).get("current_iv")
    return (civ / 100.0) if (civ and civ > 1.5) else (civ or 0.20)
```

In `results`: `"structures_0dte": [], "structures_swing": []`.

In the funnel seed, two more buckets per symbol:

```python
"STRUCT_0DTE": dict.fromkeys(STRUCT_FUNNEL_KEYS, 0) | {"build_failed": False},
"STRUCT_SWING": dict.fromkeys(STRUCT_FUNNEL_KEYS, 0) | {"build_failed": False},
```

In the per-symbol loop, after the single-leg block and outside its `try`:

```python
        # --- Structures other than credit spreads (own lists, own scorer) ---
        # Additive, like the single-leg pass: it must never break the credit
        # scan. Own try per window, so a crash in one leaves the other standing.
        if STRUCTURES_CFG["enabled"]:
            _earn = earnings_by_symbol.get(symbol)
            _band = (STRUCTURES_CFG["short_delta_min"], MAX_ENTRY_SHORT_DELTA)
            for _fkey, _rkey, _chain, _back, _lo, _hi, _back_hi, _wtype in (
                ("STRUCT_0DTE", "structures_0dte", data.get("chain_0"), None,
                 zerodte_min_dte, zerodte_max_dte, None, "0-DTE"),
                ("STRUCT_SWING", "structures_swing", data.get("chain_s"),
                 data.get("chain_iv"), swing_min_dte, swing_max_dte,
                 (iv_to - today).days, "SWING"),
            ):
                bucket_x = _bucket(symbol, _fkey)
                if not _chain or _chain.get("status") == "FAILED":
                    continue
                try:
                    import structure_scan as _sx
                    import strategy_scanner as _ssn
                    import strategy_scoring as _ssc

                    _atm = decimal_atm_iv(daily_em, price, iv_data)
                    _view = _ssc.infer_market_view(tech or {}, iv_data or {})

                    def _spans(sig, _t=_wtype):
                        return bool(_earn) and earnings_gate_applies(
                            _t, sig.get("dte")) and check_earnings_conflict(
                            _earn, _ssn.latest_expiration(sig))

                    _cands = _sx.build_window(
                        _chain, symbol, price, _atm, _lo, _hi,
                        families=STRUCTURES_CFG["families"], short_band=_band,
                        back_chain=_back, back_dte_max=_back_hi)
                    results[_rkey].extend(_sx.select(
                        _cands, view=_view, atm_iv=_atm, daily_em=daily_em,
                        dte_min=_lo, iv_rank=iv_data.get("iv_rank"),
                        floor=MIN_IV_RANK.get(_wtype),
                        ceiling=MAX_IV_RANK.get(_wtype),
                        spans_earnings=_spans, earnings_date=_earn,
                        keep_long_through_earnings=(
                            STRUCTURES_CFG["earnings_long_premium"] == "flag"),
                        min_score=STRUCTURES_CFG["min_score"],
                        excluded_grades=tuple(STRUCTURES_CFG["excluded_grades"]),
                        max_per_window=STRUCTURES_CFG["max_per_symbol_window"],
                        bucket=bucket_x))
                except Exception:  # noqa: BLE001
                    log.exception(f"  structures pass ({_wtype}) for {symbol} failed")
                    if bucket_x is not None:
                        bucket_x["build_failed"] = True
```

Use the module's real name for the entry short-delta ceiling (`_scfg.selection()["max_entry_short_delta"]` if no constant exists). `iv_to` is a `date` (see `scan_windows`).

Then, further down the function:

- **Regular-hours gate:** beside the `signals_directional` handling, for each of the two new lists add `n` per symbol to that bucket's `outside_rth`, add to `held`, and empty the list.
- **Sort:** both lists by `composite_score`, descending.
- **Terminal `emitted`:** add the two `(list key, bucket name)` pairs to the loop and change the target line to `target = b if bucket_name in FLAT_FUNNEL_BUCKETS else b["spreads"]`.
- **IV stamping loop:** add the two keys to the tuple.
- **Not** the position-sizing loop, the expected-P&L loop, the regime filter, the IV floor, the gamma gate or `signal_recorder`. Capture is Phase 4.

Extend `run_full_scan`'s docstring: the two bucket names, the identity, and that a calendar reads the `+20..+45` chain.

**Step 4:** Run both test files in full, then the whole `options-scanner` suite. Expected: the baseline's failing set (none).

**Step 5: Commit** — `feat(scanner): run_full_scan builds non-credit structures into their own lists`.

### Task 8: Measure what the pass costs

**Files:**
- Create: `tools/measure_structure_scan.py`

A script, not a test. It builds synthetic chains for N symbols with `tools/sweep_strategy_gates.chain` (0..4, 5..15 and 20..45 days, realistic strike counts: `--step 1`, `SPAN` 0.40), times `structure_scan.build_window` + `select` per symbol for both windows, and prints the mean, the worst and the total for N = 45.

Run it. Record the figures in the design doc's "Measured" section. **Budget: 1 second per symbol.** If it is over, stop and report; the lever is `max_per_symbol_window` and the family list, not a code shortcut.

Commit: `feat(tools): measure the structures pass per symbol`.

### Task 9: The contract and the publish

**Files:**
- Modify: `shared/contracts/options.py` (`ScanResult`), `services/options_svc/handlers.py` (`_SCAN_DEFAULTS`, `_stamp_scan`)
- Test: `shared/tests/` (the file that covers `ScanResult`), `services/options_svc/tests/test_handlers.py`

**Step 1: Failing tests**

- A `ScanResult` built from a payload **without** the two keys validates and dumps them as `[]` (Redis keeps the view across a restart).
- A `ScanResult` built with them round-trips them through `model_dump()`.
- `rescan` on a fake bus with an engine result carrying one structure row publishes it under `cache:options:scan`, stamped by `_stamp_scan` (assert a key `compute.stamp_candidate` writes; read that function for the name).
- The push call still receives only `signals_0dte + signals_swing`.

**Step 2:** Run. Expected: the round-trip test fails (the key is dropped by the projection).

**Step 3: Implement.**

```python
    # Structures other than credit spreads (structure_scan). The normalized
    # shape, like signals_directional, and their OWN lists for the same two
    # reasons that list has one. Additive with a default.
    structures_0dte: list[dict] = []
    structures_swing: list[dict] = []
```

Add both to `_SCAN_DEFAULTS` and to `_stamp_scan`'s tuple as `("structures_0dte", "0-DTE"), ("structures_swing", "SWING")`. Extend the `ScanResult` docstring.

**Step 4:** Run both files. **Step 5: Commit** — `feat(options_svc): publish the structure lists on the scan view`.

### Task 10: The day union

**Files:**
- Modify: `services/options_svc/compute.py` (`_DAY_LISTS` and the comment above `_DAY_MAX_PER_LIST`)
- Test: `services/options_svc/tests/test_day_setups.py` (or the file that covers `merge_day_signals`)

`setup_key` is already `SYMBOL|TYPE|EXPIRATION` and `_assemble` rows carry all three, so nothing else changes.

**Step 1: Failing tests**

- A structure row present in scan 1 and absent in scan 2 is carried with `live False` and a `stale_since`.
- Its `setup_key` is `T|LONG_STRADDLE|<expiry>`.
- `compute._count_scan_signals` returns the same counts with and without the two new lists in the envelope (the Opportunity Board does not count them in this phase).

**Step 2–5:** Run, add the two keys to `_DAY_LISTS`, run, commit — `feat(options_svc): the day union carries the structure lists`.

### Task 11: "Why no trade?" for the two new buckets

**Files:**
- Modify: `webgui/pages/options/funnel_view.py`, `webgui/pages/options/scanner.py` (`FUNNEL_BUCKETS`, the `funnel_cards` docstring)
- Test: `webgui/tests/test_funnel_view.py`, `webgui/tests/test_options_scanner.py`

**Step 1: Failing tests**

```python
def _struct(**over):
    b = {"built": 6, "vol_gate": 1, "earnings": 1, "score_cut": 2, "capped": 0,
         "outside_rth": 0, "emitted": 2, "build_failed": False}
    b.update(over)
    return {"price": 100.0, "stop": None, "buckets": {"STRUCT_SWING": b}}


def test_a_structure_bucket_reads_as_seven_stages():
    card = funnel_view.bucket_card(_struct(), "STRUCT_SWING", symbol="T")
    assert [s["remaining"] for s in card["stages"]] == [6, 5, 4, 2, 2, 2, 2]
    assert card["stages"][2]["label"] == funnel_view.LABELS["struct_earnings"]


def test_a_failed_structure_build_says_so_and_prints_no_zeroes():
    card = funnel_view.bucket_card(_struct(build_failed=True), "STRUCT_SWING",
                                   symbol="T")
    assert card["stages"] == [] and "failed" in card["headline"]


def test_empty_symbols_reads_a_flat_structure_bucket():
    payload = {"symbols": {"T": _struct(emitted=0)}}
    assert funnel_view.empty_symbols(payload, "STRUCT_SWING") == ["T"]
```

And in `test_options_scanner.py`: `funnel_cards` returns five cards and `funnel_chips` five chips.

**Step 2:** Run. Expected: failures on the missing label and bucket.

**Step 3: Implement.**

```python
STRUCTURE_COUNTERS = ("built", "vol_gate", "earnings", "score_cut", "capped",
                      "outside_rth", "emitted", "build_failed")
# Buckets whose counters sit flat on the bucket, not under ``spreads``.
FLAT_BUCKETS = ("DIRECTIONAL", "STRUCT_0DTE", "STRUCT_SWING")

BUCKET_LABELS = {"0DTE": "0-DTE", "SWING": "Swing", "DIRECTIONAL": "Directional",
                 "STRUCT_0DTE": "0-DTE, other structures",
                 "STRUCT_SWING": "Swing, other structures"}
```

`LABELS["struct_earnings"] = "Past the earnings gate"`, with a `STAGE_SENTENCES` entry in the same voice as its neighbours ("every candidate left would have been held through an earnings report").

`_structure_stages(d)` is `_directional_stages` with the earnings stage between the volatility gate and the quality bar. Write it as a new function; do not parameterise the old one.

In `bucket_card`: `if bucket == "DIRECTIONAL"` stays; add a branch for the two structure buckets with its own failed-build sentence ("the other-structures build failed for this symbol; the scan logged the error."). In `empty_symbols`: `target = b if bucket in FLAT_BUCKETS else b.get("spreads")`.

`scanner.FUNNEL_BUCKETS = ("0DTE", "STRUCT_0DTE", "SWING", "STRUCT_SWING", "DIRECTIONAL")`.

**Step 4:** Run both files. **Step 5: Commit** — `feat(webgui): the funnel explains the structure buckets`.

### Task 12: Page builders for the "Other structures" table

**Files:**
- Create: `webgui/pages/options/scanner_structures.py`
- Test: `webgui/tests/test_scanner_structures.py`

Pure functions only; no `ui.` call in this module.

**Step 1: Failing tests**

```python
from pages.options import scanner_structures as ssx


def _sig(t, group, **over):
    s = {"id": f"T_{t}", "symbol": "T", "type": t, "group": group,
         "family": "NEUTRAL", "strategy_label": t.title(), "bias": "neutral",
         "legs": [{"kind": "call", "side": "long", "strike": 100.0,
                   "expiration": "2099-01-01", "qty": 1}],
         "expiration": "2099-01-01", "dte": 9, "net_debit": 120.0,
         "net_credit": None, "max_profit": 380.0, "max_loss": 121.3,
         "breakevens": [101.2], "rr": 3.1, "pop_pct": 34.0,
         "composite_score": 61.0, "grade": "Good"}
    s.update(over)
    return s


def test_rows_carry_the_group_and_its_label():
    rows = ssx.structure_rows([_sig("BUTTERFLY_CALL", "BUTTERFLY")])
    assert rows[0]["_group"] == "BUTTERFLY"
    assert rows[0]["group_label"] == "Butterflies and condors"
    assert rows[0]["symbol"] == "T"


def test_a_row_held_through_a_report_names_the_date():
    rows = ssx.structure_rows([_sig("LONG_STRADDLE", "STRADDLE",
                                    spans_earnings=True, earnings_date="2099-01-05")])
    assert rows[0]["_earnings"] == "Earnings 01/05"


def test_a_row_with_no_report_has_no_earnings_text():
    assert ssx.structure_rows([_sig("BULL_CALL", "VERTICAL")])[0]["_earnings"] == ""


def test_filter_keeps_only_the_chosen_groups():
    rows = ssx.structure_rows([_sig("BULL_CALL", "VERTICAL"),
                               _sig("LONG_STRADDLE", "STRADDLE")])
    assert [r["type_code"] for r in ssx.filter_groups(rows, {"STRADDLE"})] == ["LONG_STRADDLE"]
    assert ssx.filter_groups(rows, set()) == []


def test_chips_list_only_groups_that_have_rows_in_order():
    rows = ssx.structure_rows([_sig("LONG_STRADDLE", "STRADDLE"),
                               _sig("BULL_CALL", "VERTICAL")])
    assert [c["group"] for c in ssx.chips(rows)] == ["VERTICAL", "STRADDLE"]
    assert ssx.chips(rows)[0]["text"] == "Debit spreads · 1"


def test_paper_is_offered_only_where_the_ledger_books_it():
    rows = ssx.structure_rows([_sig("BUTTERFLY_CALL", "BUTTERFLY"),
                               _sig("LONG_STRADDLE", "STRADDLE"),
                               _sig("CALENDAR_CALL", "CALENDAR")])
    by = {r["type_code"]: r["_allow_paper"] for r in rows}
    assert by == {"BUTTERFLY_CALL": True, "LONG_STRADDLE": False,
                  "CALENDAR_CALL": False}
```

Check what `strategy_table.strategy_rows` calls the raw type field on a row and use that name in place of `type_code`.

**Step 2:** Run. Expected: `ModuleNotFoundError`.

**Step 3: Implement**

```python
"""Display builders for the Market Scanner's "Other structures" tables (PURE).

The rows are the Directional tab's (``scanner.directional_rows`` over the
normalized candidate shape) plus the build group, for the family chips, and the
earnings note. No ``ui.`` call lives here.
"""
from . import scanner

# Build group -> the reader's words, in chip order. The keys are the service's
# ``group`` values (structure_scan / compute._SWING_FAMILIES).
GROUPS = (("VERTICAL", "Debit spreads"),
          ("STRADDLE", "Straddles and strangles"),
          ("BUTTERFLY", "Butterflies and condors"),
          ("CALENDAR", "Calendars"),
          ("RATIO", "Ratio spreads"))
_LABEL = dict(GROUPS)

# Day-list key -> the tab it sits on.
LIST_TAB = {"structures_0dte": "0-DTE", "structures_swing": "Swing"}


def _earnings_text(sig):
    if not sig.get("spans_earnings"):
        return ""
    return f"Earnings {scanner._short_exp(sig.get('earnings_date'))}"


def structure_rows(signals):
    rows = scanner.directional_rows(signals)
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        sig = by_id.get(r.get("id")) or {}
        group = str(sig.get("group") or "").upper()
        r["_group"] = group
        r["group_label"] = _LABEL.get(group, "")
        r["_earnings"] = _earnings_text(sig)
    return rows


def filter_groups(rows, on):
    return [r for r in rows if r.get("_group") in on]


def chips(rows):
    counts = {}
    for r in rows:
        counts[r.get("_group")] = counts.get(r.get("_group"), 0) + 1
    return [{"group": g, "text": f"{label} · {counts[g]}"}
            for g, label in GROUPS if counts.get(g)]
```

**Step 4:** Run. **Step 5: Commit** — `feat(webgui): row, chip and filter builders for the Scanner's other structures`.

### Task 13: The page

**Files:**
- Modify: `webgui/pages/options/scanner.py`, `webgui/pages/symbol_facts.py` (and `symbol.py` if it branches on the list key)
- Test: `webgui/tests/test_options_scanner.py`, the Symbol page's tests

Keep new logic out of `render`: it has a size ceiling that can only be lowered. Put helpers at module level or in `scanner_structures.py`.

**Step 1: Failing tests** (pure parts)

- `scanner.DAY_LISTS` ends with `"structures_0dte", "structures_swing"`.
- `_build_populate` given a day envelope with one row in `structures_swing` returns `rows["structures_swing"]` with one row carrying `_group`.
- `status_line` counts the new lists (it sums over `DAY_LISTS`).
- A new pure `tab_totals(painted, shown)` returns `{"0-DTE": (full, shown), "Swing": (...), "Directional": (...)}` where the first two sum the credit list and the structures list on that tab.
- `symbol_facts`: a structure row is tagged with its list key and rendered through the same branch a Directional row takes.

**Step 2:** Run. Expected: failures.

**Step 3: Implement**

1. `DAY_LISTS = ("signals_0dte", "signals_swing", "signals_directional", "structures_0dte", "structures_swing")`.
2. In `_build_populate`: `rows[key] = scanner_structures.structure_rows(sigs[key])` for the two new keys (lazy import, as `directional_rows` does).
3. In `render`, each of the 0-DTE and Swing tab panels becomes:

   ```python
   with ui.tab_panel(tab_0dte):
       view_0dte = ui.toggle({"credit": "Credit spreads", "other": "Other structures"},
                             value="credit").props("dense no-caps")
       table_0dte = _table(signal_columns())
       with ui.column().classes("w-full gap-2") as other_0dte:
           chips_0dte = ui.row().classes("gap-2 items-center flex-wrap")
           table_x0 = _table(directional_columns())
   ```

   `other_0dte` is hidden while the toggle reads `credit` and the credit table while it reads `other` (`set_visibility` in the toggle's guarded handler; `ui.toggle` is not on the kit guard's list).
4. Factor the slots `table_dir` receives (lines ~999–1024) into a module-level `_wire_normalized_table(table, on_click)` and call it for `table_dir` and the two new tables. Add one slot to the two new tables only, on the strategy cell, appending the earnings note in the warn class when `props.row._earnings` is non-empty.
5. The new tables use `_select_dir` (the normalized adapter and the legs-aware Calculator path).
6. `_paint_tables`: loop the five keys. For the two new keys, apply `scanner_structures.filter_groups(rows, on[key])` before the "Only clear" filter and repaint that tab's chip row from `scanner_structures.chips(painted[key])`. Chips are `kit.button(kind="quiet")` toggles, or the kit's chip if it has one; each handler flips membership in `on[key]` and calls `_paint_tables()`. Compute tab labels once per tab from `tab_totals`.
7. `_wire_paging` for both new tables.
8. `symbol_facts.py`: wherever it branches on `signals_directional`, the two new keys take the same branch.

**Step 4: Verify in a browser.** There is no dev environment.

```powershell
& $PY tools/ui_harness.py --help      # confirm the flags, and that the port is free
```

Render `/options/scanner` on the fake bus with a seeded `options:scan_day` holding rows in all five lists. Confirm: the toggle swaps tables; chips filter; counts in the tab header add both tables; a butterfly shows Paper, a straddle does not; a row with `spans_earnings` shows its date; Send to Calculator opens a calendar with two expiries. Screenshot each. Stop the harness.

Then run `webgui/tests` in full; `test_no_inline_style.py` and `test_ui_kit_guard.py` must pass.

**Step 5: Commit** — `feat(webgui): the Scanner's 0-DTE and Swing tabs show other structures`.

### Task 14: Words

**Files:**
- Modify: `webgui/page_help.py` (the Scanner guide and the 0-DTE and Swing subtab help), `docs/manuals/` (User Guide, Reference Guide, Technical Reference: Scanner sections), `docs/webgui-routes.md`, `docs/CHANGELOG.md`, `options-scanner/CLAUDE.md` (the scanner paragraph), root `CLAUDE.md` only if an invariant changed
- Test: the manuals' and `page_help` tests

Say, in the reader's words: what the switch does, that the two tables are scored on different scales and never ranked together, what the chips are, what the earnings note means, and which rows can be sent to Paper. Rebuild the manuals with `build_docs.py`.

The root `CLAUDE.md` gets at most these lines, under "Selection": the structures pass has its own lists and never enters the credit lists; long premium is flagged through a report and short premium dropped. Check `tests/test_claude_md_size.py` still passes.

Commit — `docs(scanner): the other-structures tables in the help and the manuals`.

**Phase 2 ends here. Run every touched suite, compare failing sets with the baselines, and report.**

---

# Phase 3 — The ratio backspread

### Task 15: Names

**Files:**
- Modify: `shared/structures.py`
- Test: `shared/tests/test_structures.py`, `shared/tests/test_cross_tier_mirrors.py`

Add `BACKSPREAD = ("CALL_BACKSPREAD", "PUT_BACKSPREAD")` with a comment (short one nearer the money, long two further out; three contracts to close), and `_LEGS` entries of `3` for both. They join neither `LEDGER_DEBIT` nor `LEDGER_CREDIT`: no Paper button.

Tests: `option_legs("call_backspread") == 3`; neither name is in a ledger tuple; the duplicate-set guard still passes.

Commit — `feat(structures): name the ratio backspreads`.

### Task 16: The builder

**Files:**
- Modify: `options-scanner/strategy_scanner.py`
- Test: `options-scanner/tests/test_strategy_scanner.py`

**Step 1: Failing tests**

```python
def _bs():
    return bs_chain(spot=100.0, iv=0.28, days=(9,), step=1.0)


def test_a_call_backspread_sells_one_and_buys_two_further_out():
    out = {s["type"]: s for s in ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15)}
    legs = out["CALL_BACKSPREAD"]["legs"]
    short = next(l for l in legs if l["side"] == "short")
    long_ = next(l for l in legs if l["side"] == "long")
    assert (short["qty"], long_["qty"]) == (1, 2)
    assert short["kind"] == long_["kind"] == "call"
    assert long_["strike"] > short["strike"]
    assert abs(abs(short["delta"]) - 0.50) < 0.10
    assert abs(abs(long_["delta"]) - 0.30) < 0.10


def test_a_put_backspread_mirrors_it():
    out = {s["type"]: s for s in ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15)}
    legs = out["PUT_BACKSPREAD"]["legs"]
    short = next(l for l in legs if l["side"] == "short")
    long_ = next(l for l in legs if l["side"] == "long")
    assert long_["strike"] < short["strike"] and long_["qty"] == 2


def test_the_worst_case_is_at_the_long_strike():
    sig = next(s for s in ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15)
               if s["type"] == "CALL_BACKSPREAD")
    short = next(l for l in sig["legs"] if l["side"] == "short")
    long_ = next(l for l in sig["legs"] if l["side"] == "long")
    width = (long_["strike"] - short["strike"]) * 100
    net = sig["net_debit"] if sig["net_debit"] is not None else -sig["net_credit"]
    assert abs(sig["max_loss"] - (width + net + sig["commission"])) < 0.02


def test_the_call_version_has_unbounded_profit_and_the_put_version_does_not():
    out = {s["type"]: s for s in ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15)}
    assert out["CALL_BACKSPREAD"]["unbounded_profit"] is True
    assert out["CALL_BACKSPREAD"]["unbounded_loss"] is False
    assert out["PUT_BACKSPREAD"]["unbounded_loss"] is False


def test_capital_is_the_worst_case_not_a_margin_proxy():
    # payoff_metrics sets capital to a margin proxy on any row it flags
    # unbounded, which a call backspread is (on the PROFIT side).
    for sig in ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15):
        assert sig["capital"] == sig["max_loss"]


def test_the_far_breakeven_is_named():
    sig = next(s for s in ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15)
               if s["type"] == "CALL_BACKSPREAD")
    long_ = next(l for l in sig["legs"] if l["side"] == "long")
    assert sig["target_breakeven"] > long_["strike"]
    assert sig["target_breakeven"] == max(sig["breakevens"])


def test_a_backspread_that_costs_too_much_is_not_built():
    out = ss.build_backspreads(_bs(), "T", 100.0, 0.28, 5, 15, max_debit_frac=0.0)
    assert all(s["net_debit"] is None for s in out)


def test_a_credit_at_or_over_the_strike_distance_is_a_bad_mark_not_a_trade():
    chain = _bs()
    # Mark the at-the-money call absurdly rich: the "credit" exceeds the width.
    exp = next(iter(chain["callExpDateMap"]))
    chain["callExpDateMap"][exp]["100.0"][0]["mark"] = 40.0
    out = ss.build_backspreads(chain, "T", 100.0, 0.28, 5, 15)
    assert "CALL_BACKSPREAD" not in {s["type"] for s in out}
```

**Step 2:** Run. Expected: `AttributeError: build_backspreads`.

**Step 3: Implement**

```python
# A backspread sells one option near the money and buys two further out, same
# expiry: long volatility with a direction. Deltas are the practitioner shape
# (docs/plans/2026-09-11-options-strategy-playbook.md, "Backspread").
_BACK_SHORT_DELTA, _BACK_LONG_DELTA = 0.50, 0.30


def _backspread_priced(sig, width, max_debit_frac):
    """A 1x2 backspread's net must be a credit under the strike distance, or a
    debit no more than ``max_debit_frac`` of it. A credit at or over the distance
    cannot happen on real quotes (the short option is worth at most the distance
    more than one long), so it is a bad mark, not a good trade."""
    cap = width * _CONTRACT_MULT
    credit, debit = sig.get("net_credit"), sig.get("net_debit")
    if credit is not None:
        return math.isfinite(credit) and 0 < credit < cap
    if debit is not None:
        return math.isfinite(debit) and debit <= max_debit_frac * cap
    return True       # even money


def build_backspreads(chain, symbol, spot, atm_iv, dte_min, dte_max,
                      max_debit_frac=0.25):
    """Call and put ratio backspread on the nearest expiry in the window."""
    out = []
    for stype, kind, bias, label in (
            ("CALL_BACKSPREAD", "call", "bullish", "Call Backspread"),
            ("PUT_BACKSPREAD", "put", "bearish", "Put Backspread")):
        fe = _front_exp(extract_options(chain, kind, dte_min, dte_max))
        if not fe:
            continue
        exp, data = fe
        short = nearest_by_delta(data["strikes"], _BACK_SHORT_DELTA)
        if not short:
            continue
        beyond = {k: v for k, v in data["strikes"].items()
                  if (k > short["strike"] if kind == "call" else k < short["strike"])}
        long_ = nearest_by_delta(beyond, _BACK_LONG_DELTA)
        if not long_:
            continue
        width = abs(long_["strike"] - short["strike"])
        bought = _leg_from(long_, kind, "long", exp)
        bought["qty"] = 2
        legs = [_leg_from(short, kind, "short", exp), bought]
        sig = _assemble(stype, "VOLATILITY", label, bias, legs, symbol, spot, atm_iv)
        if not _backspread_priced(sig, width, max_debit_frac):
            continue
        # Its loss is bounded on both sides, so the capital at risk is the worst
        # case. payoff_metrics would hand a call backspread a margin proxy,
        # because it flags the row unbounded for its PROFIT.
        sig["capital"] = sig["max_loss"]
        # The breakeven the trade is FOR: the far one, past the long strike. For
        # a credit there is a second, near the short strike, and that one is
        # where the LOSS begins - see q_breakeven_vs_em.
        bes = sig.get("breakevens") or []
        if bes:
            sig["target_breakeven"] = max(bes) if kind == "call" else min(bes)
        out.append(sig)
    return out
```

If the worst-case test fails by more than the tolerance, read `payoff_metrics`' grid: the loss is read off a price grid and the long strike may sit between two grid points. Report the gap; do not loosen the tolerance past $1 without saying so.

**Step 4:** Run. **Step 5: Commit** — `feat(scanner): build call and put ratio backspreads`.

### Task 17: Score a backspread on the breakeven it is for

**Files:**
- Modify: `options-scanner/strategy_scoring.py` (`q_breakeven_vs_em`, `_TYPE_PROFILE`)
- Test: `options-scanner/tests/test_strategy_scoring.py`

`q_breakeven_vs_em` scores a non-neutral row on its **nearest** breakeven. A backspread taken for a credit has a near breakeven beside the short strike that marks the start of its loss zone, so the factor would reward the trade for sitting next to its own loss.

**Step 1: Failing tests**

```python
def test_a_named_target_breakeven_is_the_one_scored():
    sig = {"family": "VOLATILITY", "underlying_price": 100.0,
           "breakevens": [100.4, 106.0], "target_breakeven": 106.0}
    near_only = dict(sig, target_breakeven=None)
    assert sc.q_breakeven_vs_em(sig, 4.0) < sc.q_breakeven_vs_em(near_only, 4.0)
    assert sc.q_breakeven_vs_em(sig, 4.0) == 0.0          # 6 away, a 4 move


def test_a_row_without_the_field_scores_as_before():
    sig = {"family": "VOLATILITY", "underlying_price": 100.0, "breakevens": [102.0]}
    assert sc.q_breakeven_vs_em(sig, 4.0) == 50.0


def test_an_unusable_target_falls_back_to_the_nearest():
    sig = {"family": "VOLATILITY", "underlying_price": 100.0,
           "breakevens": [102.0], "target_breakeven": float("nan")}
    assert sc.q_breakeven_vs_em(sig, 4.0) == 50.0
```

**Step 2:** Run. **Step 3: Implement**, in the directional branch only:

```python
    # A structure may name the breakeven it is FOR (a backspread's far one); its
    # nearest can be the edge of its own loss zone. Absent or unusable: nearest.
    target = _real(signal.get("target_breakeven"))
    dist = (abs(target - spot) if target is not None
            else min(abs(be - spot) for be in bes))
```

**Step 4: Choose the gate profile by measurement.** Add `build_backspreads` to the sweep's `BUILDERS`, run the Task 3 grid, and read each backspread's PoP and reward under `LONG` and under `DEBIT`. Put the measured table in the design doc. Add both names to `_TYPE_PROFILE` under the profile the measurement supports, with a comment quoting the figures and their parameters, and pin it with a test. If neither profile fits honestly, stop and report; do not invent a bar.

**Step 5:** Run the scoring suite and `tools/tests/`. **Commit** — `feat(scoring): judge a backspread on its far breakeven; measured gate profile`.

### Task 18: Ratio spreads on the Scanner and the Finder

**Files:**
- Modify: `options-scanner/structure_scan.py`, `config/scanner.toml`, `shared/scanner_config.py`, `webgui/config_schema.py`, `services/options_svc/compute.py` (`_SWING_FAMILIES` and the two build paths in `swing_scan`), the Finder page's family checkboxes (`webgui/pages/options/swing.py` / `finder_view.py`)
- Test: `options-scanner/tests/test_structure_scan.py`, `shared/tests/test_scanner_config.py`, `services/options_svc/tests/` (the swing scan tests), `webgui/tests/`

1. `[structures] families` gains `"RATIO"`; a new key `backspread_max_debit_frac = 0.25` with accessor validation (finite, 0..1) and a catalogue `Field`.
2. `build_window` gains `max_debit_frac` and builds `RATIO` through `_ssn.build_backspreads`.
3. `compute._SWING_FAMILIES` gains `"RATIO"`; `swing_scan` builds it in both the front-expiry path and `_build_every_expiry`; the Finder page gains a "Ratio spreads" checkbox, on by default.
4. Tests: the family builds on both surfaces; left out, it does not; the Finder's `filtered_out` count covers it.

Commit — `feat(scanner): ratio spreads on the Market Scanner and the Strategy Finder`.

### Task 19: Calculator templates

**Files:**
- Modify: `webgui/pages/options/strategies.py` (`STRATEGY_TEMPLATES`, the menu table near line 126, the direction table near line 183)
- Test: `webgui/tests/test_strategies.py`, the hand-off tests

Add `CALL_BACKSPREAD` and `PUT_BACKSPREAD` templates in the file's existing spec form (one short, two long further out), a menu entry each under the group the file uses for volatility structures, a label, and a direction. `test_strategies.py` checks every template code is covered by the menu and the label table; it must pass. Add a hand-off test: a Scanner backspread row sent to the Calculator matches its template (`sim_view.matches_template`).

Verify in the harness: Send to Calculator from a backspread row opens "Call Backspread" with a 1 and a 2 quantity.

Commit — `feat(webgui): Calculator templates for the ratio backspreads`.

### Task 20: Words

`page_help.py`, the manuals (add "Backspread" to the Options Glossary), `docs/webgui-routes.md`, CHANGELOG, and the playbook table's "Not supported" cell. Rebuild the manuals. Commit.

**Phase 3 ends here. Run every touched suite and report.**

---

# Phase 4 — Capture and tracking

**This phase is written at task level.** Expand each task to steps, with code, once Phase 2 is live and its first session's funnel has been read: Phase 1's measurement and that session decide which structures produce rows worth tracking. Task 21 is the exception; it is complete, and **it ships first, on its own**.

### Task 21: The paper Account can never open a tracked structure (complete; do first)

**Files:**
- Modify: `shared/structures.py`, `options-scanner/paper_engine.py`
- Test: `shared/tests/test_structures.py`, `options-scanner/tests/test_paper_engine.py`

**Step 1: Failing tests**

```python
def test_the_account_opens_only_the_three_credit_structures():
    assert structures.ACCOUNT_AUTO_ENTRY == ("PCS", "CCS", "IC")
    assert structures.is_account_auto_entry("pcs ")
    assert not structures.is_account_auto_entry("LONG_STRADDLE")
    assert not structures.is_account_auto_entry(None)
```

In `test_paper_engine.py`, using the file's existing entry-cycle fixture and fake broker, three tests, each asserting the broker's `submit_order` was **never called** and no order row was written:

- a signal with `scanner_type="SWING_STRUCT"` and `strategy="PCS"` (the type guard alone);
- a signal with `scanner_type="SWING"` and `strategy="LONG_STRADDLE"` (the structure guard alone);
- a signal with `scanner_type="0DTE_STRUCT"` and `strategy="BUTTERFLY_CALL"`.

And one that a plain `SWING` / `PCS` signal still opens, so the guards are not passing on a broken fixture.

**Step 2:** Run. Expected: the structure test fails with a `KeyError` or an opened position.

**Step 3: Implement**

`shared/structures.py`:

```python
# The structures the paper ACCOUNT's entry cycle may open on its own. An
# allow-list, so a structure added anywhere else is refused here by omission.
ACCOUNT_AUTO_ENTRY = ("PCS", "CCS", "IC")


def is_account_auto_entry(strategy) -> bool:
    return normalise(strategy) in ACCOUNT_AUTO_ENTRY
```

`paper_engine.py`:

```python
_NO_AUTO_ENTRY_TYPES = ("INCOME", "0DTE_STRUCT", "SWING_STRUCT")
```

and in `run_entry_cycle`, directly after the scanner-type refusal:

```python
        # Second guard, by STRUCTURE: the sizer and the order below are written
        # for a credit spread or an iron condor and nothing else. Tracked
        # structures are refused by type above; this refuses one that arrives
        # under any other type.
        if not _structures.is_account_auto_entry(sig.get("strategy")):
            continue
```

Import `shared.structures` the way the module already imports from `shared`.

**Step 4:** Run `test_paper_engine*.py` and `shared/tests/test_structures.py` in full. **Step 5: Commit** — `fix(paper): the Account's entry cycle opens credit spreads and iron condors only`.

### Task 22: Schema

`options-scanner/signal_db.py`: additive, idempotent columns through `_migrate_signals_table` — `legs_json TEXT`, `family TEXT`, `entry_max_profit REAL`, `entry_capital REAL`, `unbounded INTEGER DEFAULT 0`, `entry_spans_earnings INTEGER DEFAULT 0`. `insert_signal` writes them when present. `count_open_by_symbol(scanner_types=None, exclude_types=None)` so the two cap pools can be counted apart.

Tests: a fresh DB and a legacy DB both end with the columns; an old-shape row inserts unchanged; the two counts partition the total.

### Task 23: Configuration for capture

`[capture] max_open_per_symbol_structures = 2` and `[scores] capture_min_structures = 0` (the display cut is the real filter, as for the Income board), with accessors, catalogue entries, and tests that each is **read** (monkeypatch + reload).

### Task 24: The recorder

`signal_recorder.record_structures(signals, scanner_type, db_path, now)`:

- the same regular-hours gate and the same single `now` for gate and stamp;
- `_to_structure_row`: per-contract dollars to per-share (`/ 100`); a debit stored as a **negative** `entry_credit`; `entry_max_loss` per share from `max_loss`, or from `capital` with `unbounded = 1` when `unbounded_loss`; `legs_json` holding kind, side, strike, expiration and qty per leg; `expiration` the front one; `entry_spans_earnings`;
- dedup key from the legs: `SYMBOL|TYPE|` + each leg as `side kind strike expiration xqty`, sorted, `|` + scanner type;
- the cap counted against `0DTE_STRUCT` + `SWING_STRUCT` only, and `record_signals`' own count changed to **exclude** those two, under the same `_CAP_LOCK`;
- fails closed on an unreadable count, like `record_signals`.

Tests: units; the sign of a debit and of a credit; the unbounded flag; two scans of the same structure insert once; a full structures pool does not block a credit capture, and a full credit pool does not block a structure; nothing is written outside the session.

### Task 25: Record from the scan

`run_full_scan` calls `record_structures` for `structures_0dte` (`0DTE_STRUCT`) and `structures_swing` (`SWING_STRUCT`), and for `signals_directional` split by window (`dte <= 4` is the 0-DTE window, the rule `handlers._directional_window` mirrors). Inside the existing recorder `try`.

Tests: rows land under the right type; a recorder failure leaves the scan result intact.

### Task 26: Marks

`signal_repricer.reprice_position(row, client, today=None)`: a generalisation of `reprice_legs` that fetches each leg's own expiration (the per-`(symbol, expiration)` cache already exists), sums signed mids times quantity, and computes P&L against the signed entry. Same return shape as `reprice_swing`. A leg with no usable quote returns an error and no value; a NaN never becomes a number.

Tests: a debit vertical, a short strangle (credit), a butterfly (a two-contract leg) and a calendar (two chains), each against hand-computed values; a missing leg quote; an expired front leg.

### Task 27: Exit rules

`config/trade_mgmt.toml [structures.*]` tables for each new structure, sourced from the playbook and cited in a comment; `shared/trade_mgmt.structure_rules` returns them. `signal_recommender` gains one new code, `FRONT_EXPIRY`, for a two-expiry row on its front leg's expiry day.

Per the design: debit structures and backspreads through `_recommend_debit` unchanged; butterflies and condors with a target and no time exit; short straddle and strangle on the credit rules with `loss_rules` as the playbook gives them; calendars and diagonals with a target and `FRONT_EXPIRY`.

Tests: one per structure per rule, including that an unmarked position (`unrealized_pnl is None`) holds.

### Task 28: The manage cycle

`compute.reprice_captured` and `compute.run_captured_manage_cycle` dispatch on `legs_json`: a leg row is marked with `reprice_position`, settled at expiry with `legs_intrinsic_value` against `paper_engine.settlement_underlying`, and never handed to `_attach_rescue_assessment`. A two-expiry row is closed on its mark by `FRONT_EXPIRY` and, if it reaches settlement unmarked, closed with reason `UNMARKABLE` and no P&L. New code goes in a sibling module (`services/options_svc/captured_structures.py`) that imports nothing from `compute`; `compute` gains only the dispatch.

Tests: an end-to-end test in the style of `test_captured_autoclose_e2e.py` for a debit, a credit and a calendar row; a credit-spread row's path is byte-identical with the new module present.

### Task 29: Every reader of the store

For each of the four `get_open_signals_with_latest_mark` call sites in `compute.py` (lines near 2449, 3309, 3375, 3566 at the time of writing) and for `captured_performance`, `captured_day_summary`, `captured_closed_today`: a test that feeds it a leg row and asserts it neither raises nor reads the row as a credit spread. Fix what fails.

### Task 30: The Captured Signals page

`webgui/pages/options/captured.py`: a Legs cell for leg rows (reuse `strategy_table.legs_summary`), the family, the earnings note, "Debit" or "Credit" worded per row (a `Credit` column is wrong where the book holds debits), and the undefined-risk badge on an `unbounded` row. Verify in the harness.

### Task 31: Calibration

The nightly calibration buckets by scanner type; the two new types are their own buckets and the credit baseline does not move. `unbounded` rows are reported apart and never averaged with defined-risk rows. `entry_spans_earnings` rows are reported apart. Check `shared/calibration.bucket_key` and `services/options_svc/calibration.py` and add the splits there; pin with `test_cross_tier_mirrors.py` if a name is mirrored.

### Task 32: Words, and the invariants

`page_help.py`, the manuals, `docs/webgui-routes.md`, CHANGELOG, `docs/reference/options-engine-invariants.md` (the two Account guards, the two cap pools, the calendar's close), and these lines in root `CLAUDE.md`: the Account's entry cycle is an allow-list by structure; tracked structures have their own capture pool; a two-expiry capture is never settled at intrinsic.

### Task 33: After the promote

Promote after the close. On the next session, read on prod: the funnel's structure buckets for five symbols; the count of `0DTE_STRUCT` / `SWING_STRUCT` rows; that the Account's order table holds no order for any of their `signal_id`s; one mark per structure type checked by hand against the chain. Report what was found, including what did not appear.

---

## Not in this plan

- Automatic entry of any new structure into the paper Account.
- Paper buttons for structures the Ledger cannot book today.
- Push alerts, trade-idea posts, X posts and site ideas from the new lists.
- Counting the new lists on the Opportunity Board.
- Any change to a credit-spread rule, score or floor.
- New gate bars. They follow from the outcome data Phase 4 collects.
