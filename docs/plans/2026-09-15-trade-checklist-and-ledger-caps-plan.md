# Trade Checklist, Capped Paper Button and "Why No Trade?" Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give every trade candidate a Go / No-Go checklist, cap the Paper button with the paper book's risk rules (and preview them before a click), and show why a symbol produced no Market Scanner signal.

**Architecture:** One pure cap module, `shared/book_caps.py`, is shared by the Paper Account's entry cycle, a newly capped Paper Ledger and the page's preview. The options service stamps per-candidate facts onto rows and publishes the Ledger's book; a pure Tier-1 module joins those with live views (Opportunity Board, regime, calibration) into checks. The scanner engine counts its rejections per symbol and publishes them as their own view.

**Tech Stack:** Python 3.11, NiceGUI (Tier 1), FastAPI service + Redis bus (`shared.bus`, fakeredis under pytest), pydantic contracts, pytest.

**Design:** [`2026-09-15-trade-checklist-and-ledger-caps-design.md`](2026-09-15-trade-checklist-and-ledger-caps-design.md) — read it first. Every decision below is recorded there.

---

## Rules for whoever executes this

1. **Never weaken an existing test to make it pass.** Do not narrow, delete or loosen an existing assertion. If an existing test fails after your change, STOP and report which test, the failure, and why you think it fails. The one planned exception is Task 6's `test_create_paper_trade_creates_then_adds`, whose contract this plan deliberately changes; the new assertions are given in full.
2. **Money path.** Tasks 3 and 6 change code that decides whether a paper trade opens. Run the full options-scanner and options_svc suites after each, and compare the **failing set** by node id against the baseline you record in Task 0b — never just the count.
3. **Stage files by name.** `git add <file> <file>`; never `git add -A` or `.` (other sessions share the stash and history).
4. **Commit trailer.** Every commit message ends with a blank line and
   `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
5. **Tier 1 imports.** `webgui/` may import `shared.book_caps` (added to the allow-list in Task 4) but still never `services.*`, `options-scanner` modules, `sqlite3`, `shared.sectors` or `shared.scanner_config`.
6. **Tailwind-first.** No `.style(` and no `:style=` in any new or touched page file.
7. **Where things run.** Work in the worktree root:
   `D:\WebGUI Trading with Schwab\.claude\worktrees\options-calc-simulator-redesign-0d1f87`.
   The venv lives in the main checkout. Every Bash command below starts with
   `PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe";` — shell variables do not persist between Bash calls, so set it in the same command. Per-app suites run from inside their folder in a subshell, e.g. `(cd options-scanner && "$PY" -m pytest tests -q -p no:randomly)`.

Suite commands used throughout (from the worktree root):

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd options-scanner && "$PY" -m pytest tests -q -p no:randomly)
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest services/options_svc -q
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest shared/tests shared/contracts -q
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd webgui && "$PY" -m pytest . -q)
```

---

## Phase 0 — Measure and baseline

### Task 0a: Measure how many candidates the $250 Ledger cap would make unopenable

Read-only, against prod's live caches. Nothing is committed. The result is reported to the operator **before Phase 2** ships.

**Files:**
- Create (scratchpad, not the repo): `<scratchpad>/ledger_cap_exposure.py`

**Step 1: Write the script**

```python
"""How many live candidates risk more than $250 for ONE contract? Read-only."""
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "options-scanner")
from shared.bus import Bus
import paper_trader

bus = Bus()
cap = 250.0


def per_contract(row):
    try:
        return paper_trader.create_paper_trade(row, 1)["max_loss_total"]
    except Exception:
        return None


def tally(name, rows):
    risks = [per_contract(r) for r in rows]
    tradeable = [x for x in risks if x is not None]
    over = [x for x in tradeable if x > cap]
    print(f"{name}: rows={len(rows)} paper-tradeable={len(tradeable)} "
          f"over ${cap:.0f}={len(over)} "
          f"({(100 * len(over) / len(tradeable)) if tradeable else 0:.0f}%)")


scan = bus.cache_get("cache:options:scan")
p = scan.payload if scan else {}
for key in ("signals_0dte", "signals_swing", "signals_directional"):
    tally(key, p.get(key) or [])
swing = bus.cache_get("cache:options:swing")
sp = swing.payload if swing else {}
tally(f"finder ({sp.get('symbol')})", sp.get("signals") or [])
```

**Step 2: Run it on the prod box** (`/home/administrator/dev` is the prod checkout; nothing is written):

```bash
ssh vps2-ts 'cd /home/administrator/dev && .venv/bin/python -' < "<scratchpad>/ledger_cap_exposure.py"
```

Expected: four lines of counts. Paste them into the design doc under a new `## Measured before shipping` section (edit in place) and include them in the Phase 2 hand-off to the operator.

### Task 0b: Record the failing-set baseline

**Step 1:** Run all four suite commands above with `-rf` (it is the pytest default in `pyproject.toml`) and save each "short test summary info" block to `<scratchpad>/baseline-<suite>.txt`.

Expected: no failures (CLAUDE.md records none). If there are any, note the node ids; they are the baseline you compare against after every money-path task.

---

## Phase 1 — `shared/book_caps.py` (no behaviour change)

### Task 1: The rung evaluator

**Files:**
- Create: `shared/book_caps.py`
- Test: `shared/tests/test_book_caps.py`

**Step 1: Write the failing tests**

```python
"""shared.book_caps - the paper books' risk caps in one place."""
import math

import pytest

from shared import book_caps as bc

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.20,
          "max_risk_per_trade": 250.0}


def _row(symbol, expiration="2026-10-17", risk=100.0, sector="Information Technology"):
    return {"symbol": symbol, "expiration": expiration, "max_loss_total": risk,
            "sector": sector}


def _cand(symbol="ORCL", expiration="2026-10-17", sector="Information Technology"):
    return {"symbol": symbol, "expiration": expiration, "sector": sector}


def _by_code(rungs):
    return {r["code"]: r for r in rungs}


def test_every_rung_is_reported_in_display_order():
    rungs = bc.evaluate([], _cand(), 100.0, LIMITS, equity=25000.0)
    assert [r["code"] for r in rungs] == list(bc.DISPLAY_ORDER)


def test_an_empty_book_passes_every_rung():
    rungs = bc.evaluate([], _cand(), 100.0, LIMITS, equity=25000.0)
    assert not any(r["binds"] for r in rungs)
    assert bc.first_breach(rungs, bc.DISPLAY_ORDER) is None


def test_per_trade_binds_above_the_limit_and_not_at_it():
    at = _by_code(bc.evaluate([], _cand(), 250.0, LIMITS, 25000.0))
    over = _by_code(bc.evaluate([], _cand(), 250.01, LIMITS, 25000.0))
    assert at[bc.TRADE_RISK_CAP]["binds"] is False
    assert over[bc.TRADE_RISK_CAP]["binds"] is True


def test_symbol_count_binds_when_the_new_position_would_exceed_the_cap():
    book = [_row("ORCL"), _row("ORCL"), _row("ORCL")]
    r = _by_code(bc.evaluate(book, _cand(), 10.0, LIMITS, 25000.0))[bc.SYMBOL_POSITION_CAP]
    assert (r["used"], r["after"], r["cap"], r["binds"]) == (3, 4, 3, True)


def test_symbol_risk_sums_existing_plus_candidate():
    book = [_row("ORCL", risk=600.0)]
    r = _by_code(bc.evaluate(book, _cand(), 200.0, LIMITS, 25000.0))[bc.SYMBOL_RISK_CAP]
    assert (r["used"], r["after"], r["binds"]) == (600.0, 800.0, True)


def test_symbol_matching_ignores_case_and_whitespace():
    book = [_row(" orcl "), _row("Orcl"), _row("ORCL")]
    r = _by_code(bc.evaluate(book, _cand(), 10.0, LIMITS, 25000.0))[bc.SYMBOL_POSITION_CAP]
    assert r["used"] == 3


def test_deployment_uses_twenty_percent_of_equity():
    book = [_row("MSFT", risk=4900.0, sector="X")]
    r = _by_code(bc.evaluate(book, _cand(), 101.0, LIMITS, 25000.0))[bc.DEPLOYMENT_CAP]
    assert (r["cap"], r["after"], r["binds"]) == (5000.0, 5001.0, True)


@pytest.mark.parametrize("equity", [None, 0.0, -5.0, float("nan"), "junk"])
def test_deployment_is_skipped_without_a_usable_equity(equity):
    r = _by_code(bc.evaluate([], _cand(), 1e9, LIMITS, equity))[bc.DEPLOYMENT_CAP]
    assert r["skipped"] and r["binds"] is False


@pytest.mark.parametrize("key", ["max_risk_per_trade", "max_deployed_risk_pct",
                                 "max_positions_per_sector", "max_risk_per_sector"])
def test_opt_in_rungs_skip_when_their_key_is_zero(key):
    limits = {**LIMITS, key: 0}
    code = {"max_risk_per_trade": bc.TRADE_RISK_CAP,
            "max_deployed_risk_pct": bc.DEPLOYMENT_CAP,
            "max_positions_per_sector": bc.SECTOR_POSITION_CAP,
            "max_risk_per_sector": bc.SECTOR_RISK_CAP}[key]
    r = _by_code(bc.evaluate([], _cand(), 1e9, limits, 25000.0))[code]
    assert r["skipped"] and r["binds"] is False


def test_a_zero_symbol_cap_refuses_everything_exactly_as_today():
    limits = {**LIMITS, "max_positions_per_symbol": 0}
    r = _by_code(bc.evaluate([], _cand(), 1.0, limits, 25000.0))[bc.SYMBOL_POSITION_CAP]
    assert r["binds"] is True


def test_a_missing_required_key_raises():
    limits = {k: v for k, v in LIMITS.items() if k != "max_positions_per_expiry"}
    with pytest.raises(KeyError):
        bc.evaluate([], _cand(), 1.0, limits, 25000.0)


def test_sector_rungs_skip_when_the_candidate_has_no_sector():
    rungs = _by_code(bc.evaluate([], _cand(sector=None), 1.0, LIMITS, 25000.0))
    assert rungs[bc.SECTOR_POSITION_CAP]["skipped"]
    assert rungs[bc.SECTOR_RISK_CAP]["skipped"]


def test_a_row_without_a_sector_never_counts_toward_a_sector():
    book = [_row("A", sector=None)] * 9
    r = _by_code(bc.evaluate(book, _cand(), 1.0, LIMITS, 25000.0))[bc.SECTOR_POSITION_CAP]
    assert r["used"] == 0


def test_expiry_counts_across_symbols():
    book = [_row(s, sector=s) for s in ("A", "B", "C", "D", "E")]
    r = _by_code(bc.evaluate(book, _cand(sector="Z"), 1.0, LIMITS, 25000.0))[bc.EXPIRY_POSITION_CAP]
    assert r["binds"] is True


def test_a_nan_book_row_cannot_switch_a_risk_ceiling_off():
    book = [_row("ORCL", risk=float("nan")), _row("ORCL", risk=700.0)]
    r = _by_code(bc.evaluate(book, _cand(), 100.0, LIMITS, 25000.0))[bc.SYMBOL_RISK_CAP]
    assert r["binds"] is True and math.isfinite(r["after"])


def test_first_breach_follows_the_order_it_is_given():
    book = [_row("ORCL")] * 3                       # symbol count binds
    rungs = bc.evaluate(book, _cand(), 300.0, LIMITS, 25000.0)   # per trade binds too
    assert bc.first_breach(rungs, bc.DISPLAY_ORDER)["code"] == bc.TRADE_RISK_CAP
    assert bc.first_breach(rungs, bc.ACCOUNT_ORDER)["code"] == bc.SYMBOL_POSITION_CAP
```

**Step 2: Run to verify they fail**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest shared/tests/test_book_caps.py -q
```

Expected: collection error, `ImportError: cannot import name 'book_caps'`.

**Step 3: Write the module**

```python
"""shared/book_caps.py - the paper books' risk caps, evaluated in ONE place.

Three callers share this module and must never disagree:

* ``options-scanner/paper_concentration.concentration_reject`` - the Paper
  ACCOUNT's automatic entry cycle (a thin adapter over this since 2026-09-15);
* ``services/options_svc/compute.create_paper_trade`` - the Paper LEDGER, which
  the Paper button opens into and which enforced no cap at all before this;
* ``webgui/pages/options/book_fit`` - the Paper dialog's preview.

Pure: ``math`` and ``shared.driver_policy.open_risk_dollars`` (itself math-only).
Sectors arrive RESOLVED on every row as ``sector``, so this module never reads
config, and Tier 1 may import it.

Every rung is reported, not only the first breach, because the preview shows
headroom. A rung that cannot be evaluated is ``skipped`` - never passed.

The skip rules copy ``concentration_reject`` exactly and differ by rung. Opt-in
rungs (per trade, deployment, both sector rungs) skip when their key is missing
or zero, and deployment also without a usable equity. The three original rungs
(symbol positions, symbol risk, expiry positions) are always evaluated: a zero
cap refuses everything and a missing key raises ``KeyError``. (One deliberate
difference: the old function reached a missing required key only AFTER the
deployment rung, so a binding deployment cap masked the ``KeyError``. Here it
always raises. Every production caller passes complete limits.)
"""
import math

from shared.driver_policy import open_risk_dollars

TRADE_RISK_CAP = "TRADE_RISK_CAP"
DEPLOYMENT_CAP = "DEPLOYMENT_CAP"
SYMBOL_POSITION_CAP = "SYMBOL_POSITION_CAP"
SYMBOL_RISK_CAP = "SYMBOL_RISK_CAP"
SECTOR_POSITION_CAP = "SECTOR_POSITION_CAP"
SECTOR_RISK_CAP = "SECTOR_RISK_CAP"
EXPIRY_POSITION_CAP = "EXPIRY_POSITION_CAP"

# The screen and the Ledger: per trade FIRST, because "lower the quantity" is
# the fix a reader can act on immediately (operator decision, 2026-09-15).
DISPLAY_ORDER = (TRADE_RISK_CAP, DEPLOYMENT_CAP, SYMBOL_POSITION_CAP,
                 SYMBOL_RISK_CAP, SECTOR_POSITION_CAP, SECTOR_RISK_CAP,
                 EXPIRY_POSITION_CAP)
# The Account's order, unchanged from ``concentration_reject``. It has no
# per-trade rung: the entry cycle's sizing refuses that as RISK_TOO_HIGH.
ACCOUNT_ORDER = DISPLAY_ORDER[1:]

# The Paper dialog's own maximum quantity.
QTY_CEILING = 100


def _finite(value):
    """A usable number, or 0.0. Zero is the right absence value for a candidate's
    risk: an unreadable number must not wave itself past a ceiling."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _key(value):
    return (value or "").strip().upper()


def _count(code, scope, used, cap):
    return {"code": code, "kind": "count", "scope": scope, "used": used,
            "after": used + 1, "cap": cap, "binds": used >= cap, "skipped": None}


def _risk(code, scope, used, added, cap):
    after = used + added
    return {"code": code, "kind": "risk", "scope": scope, "used": used,
            "after": after, "cap": cap, "binds": after > cap, "skipped": None}


def _skip(code, kind, scope, reason):
    return {"code": code, "kind": kind, "scope": scope, "used": None,
            "after": None, "cap": None, "binds": False, "skipped": reason}


def evaluate(book, candidate, added_risk, limits, equity=None):
    """Every rung for opening ``candidate`` into ``book``, in DISPLAY_ORDER.

    ``book`` rows: ``{symbol, expiration, max_loss_total, sector}`` for OPEN
    positions (``max_loss`` × ``quantity`` is the fallback ``open_risk_dollars``
    already understands). ``candidate``: ``{symbol, expiration, sector}``.
    ``added_risk``: the candidate's total max loss in dollars.
    """
    rows = [p for p in book or () if isinstance(p, dict)]
    cand = candidate if isinstance(candidate, dict) else {}
    added = _finite(added_risk)
    out = []

    per_trade = limits.get("max_risk_per_trade")
    if per_trade:
        out.append(_risk(TRADE_RISK_CAP, None, 0.0, added, per_trade))
    else:
        out.append(_skip(TRADE_RISK_CAP, "risk", None, "no per-trade limit"))

    pct = limits.get("max_deployed_risk_pct")
    eq = _finite(equity)
    if not pct:
        out.append(_skip(DEPLOYMENT_CAP, "risk", None, "no deployment limit"))
    elif not eq > 0:
        out.append(_skip(DEPLOYMENT_CAP, "risk", None, "no equity figure"))
    else:
        out.append(_risk(DEPLOYMENT_CAP, None, open_risk_dollars(rows), added,
                         pct * eq))

    sym = _key(cand.get("symbol"))
    same_symbol = [p for p in rows if _key(p.get("symbol")) == sym]
    out.append(_count(SYMBOL_POSITION_CAP, sym, len(same_symbol),
                      limits["max_positions_per_symbol"]))
    out.append(_risk(SYMBOL_RISK_CAP, sym, open_risk_dollars(same_symbol), added,
                     limits["max_risk_per_symbol"]))

    bucket = cand.get("sector")
    same_sector = ([p for p in rows if p.get("sector") == bucket]
                   if bucket is not None else [])
    max_n = limits.get("max_positions_per_sector")
    if not max_n:
        out.append(_skip(SECTOR_POSITION_CAP, "count", bucket, "no sector limit"))
    elif bucket is None:
        out.append(_skip(SECTOR_POSITION_CAP, "count", None, "sector unknown"))
    else:
        out.append(_count(SECTOR_POSITION_CAP, bucket, len(same_sector), max_n))
    max_risk = limits.get("max_risk_per_sector")
    if not max_risk:
        out.append(_skip(SECTOR_RISK_CAP, "risk", bucket, "no sector limit"))
    elif bucket is None:
        out.append(_skip(SECTOR_RISK_CAP, "risk", None, "sector unknown"))
    else:
        out.append(_risk(SECTOR_RISK_CAP, bucket, open_risk_dollars(same_sector),
                         added, max_risk))

    exp = (cand.get("expiration") or "").strip()
    same_expiry = [p for p in rows if (p.get("expiration") or "").strip() == exp]
    out.append(_count(EXPIRY_POSITION_CAP, exp, len(same_expiry),
                      limits["max_positions_per_expiry"]))
    return out


def first_breach(rungs, order):
    """The first binding rung in ``order``, or None."""
    by_code = {r["code"]: r for r in rungs or ()}
    for code in order:
        r = by_code.get(code)
        if r is not None and r["binds"]:
            return r
    return None
```

**Step 4: Run to verify they pass**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest shared/tests/test_book_caps.py -q
```

Expected: all pass.

**Step 5: Commit**

```bash
git add shared/book_caps.py shared/tests/test_book_caps.py
git commit -m "feat(shared): book_caps - every paper risk rung in one pure module" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 2: `max_quantity` and `describe`

> **Revised after code review (commit `09901cf`):** the count sentences read from the trade's side ("Position 3 of 3 in ORCL" / "ORCL already holds 3 of 3 positions"), the deployment line says "across the book", an unmapped bucket reads "IONQ's own group (no sector on file)", and an unknown code never prints a raw constant. `shared/book_caps.py` and its tests are authoritative; the code below is the first cut.

**Files:**
- Modify: `shared/book_caps.py`
- Test: `shared/tests/test_book_caps.py`

**Step 1: Append the failing tests**

```python
def test_max_quantity_is_bounded_by_the_tightest_risk_rung():
    # per trade: floor(250/80)=3; symbol risk: floor((750-600)/80)=1
    book = [_row("ORCL", risk=600.0)]
    assert bc.max_quantity(book, _cand(), 80.0, LIMITS, 25000.0) == 1


def test_max_quantity_is_zero_when_a_count_rung_binds():
    book = [_row("ORCL")] * 3
    assert bc.max_quantity(book, _cand(), 1.0, LIMITS, 25000.0) == 0


def test_max_quantity_is_zero_when_one_contract_is_over_the_trade_cap():
    assert bc.max_quantity([], _cand(), 425.0, LIMITS, 25000.0) == 0


def test_max_quantity_respects_the_dialog_ceiling():
    limits = {**LIMITS, "max_risk_per_trade": 0, "max_risk_per_symbol": 1e12,
              "max_risk_per_sector": 0, "max_deployed_risk_pct": 0}
    assert bc.max_quantity([], _cand(), 1.0, limits, 25000.0) == bc.QTY_CEILING


def test_max_quantity_never_overshoots_on_floating_point():
    q = bc.max_quantity([], _cand(), 83.33, LIMITS, 25000.0)
    assert q == 3 and q * 83.33 <= 250.0


@pytest.mark.parametrize("per", [None, 0, -1, float("nan"), "x"])
def test_max_quantity_is_none_for_an_unusable_contract_risk(per):
    assert bc.max_quantity([], _cand(), per, LIMITS, 25000.0) is None


def test_max_quantity_agrees_with_evaluate():
    book = [_row("ORCL", risk=300.0), _row("MSFT", risk=900.0)]
    per = 70.0
    q = bc.max_quantity(book, _cand(), per, LIMITS, 25000.0)
    assert bc.first_breach(bc.evaluate(book, _cand(), q * per, LIMITS, 25000.0),
                           bc.DISPLAY_ORDER) is None
    assert bc.first_breach(bc.evaluate(book, _cand(), (q + 1) * per, LIMITS, 25000.0),
                           bc.DISPLAY_ORDER) is not None


def test_describe_names_the_cap_in_plain_words():
    book = [_row("ORCL")] * 3
    r = bc.first_breach(bc.evaluate(book, _cand(), 10.0, LIMITS, 25000.0),
                        bc.DISPLAY_ORDER)
    assert bc.describe(r) == "Already 3 of 3 positions in ORCL"


def test_describe_an_unmapped_sector_says_so():
    r = bc._count(bc.SECTOR_POSITION_CAP, "?IONQ", 5, 5)
    assert "IONQ (no sector on file)" in bc.describe(r)


def test_describe_a_trade_risk_breach():
    r = bc._risk(bc.TRADE_RISK_CAP, None, 0.0, 425.0, 250.0)
    assert bc.describe(r) == "Risks $425, over the $250 per-trade limit"


def test_describe_a_skipped_rung():
    r = bc._skip(bc.DEPLOYMENT_CAP, "risk", None, "no equity figure")
    assert bc.describe(r) == "Not checked: no equity figure"
```

**Step 2: Run to verify they fail**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest shared/tests/test_book_caps.py -q
```

Expected: FAIL with `AttributeError: module 'shared.book_caps' has no attribute 'max_quantity'`.

**Step 3: Append the implementation**

```python
def max_quantity(book, candidate, per_contract, limits, equity=None,
                 ceiling=QTY_CEILING):
    """The largest quantity that clears every rung, or None when the per-contract
    risk is unusable. A binding COUNT rung means 0: adding one position breaks it
    whatever the size."""
    try:
        per = float(per_contract)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(per) and per > 0):
        return None
    best = ceiling
    for r in evaluate(book, candidate, 0.0, limits, equity):
        if r["skipped"]:
            continue
        if r["kind"] == "count":
            if r["binds"]:
                return 0
            continue
        room = r["cap"] - r["used"]
        if room < 0:
            return 0
        n = math.floor(room / per + 1e-9)
        while n > 0 and r["used"] + n * per > r["cap"]:
            n -= 1
        best = min(best, n)
    return max(0, best)


def _money(v):
    return f"${v:,.0f}" if abs(v - round(v)) < 0.005 else f"${v:,.2f}"


def scope_label(scope):
    """A sector bucket for a reader: ``?IONQ`` is an unmapped symbol's own bucket."""
    if isinstance(scope, str) and scope.startswith("?"):
        return f"{scope[1:]} (no sector on file)"
    return scope or ""


def describe(rung):
    """One plain sentence for a rung."""
    if rung.get("skipped"):
        return f"Not checked: {rung['skipped']}"
    code, scope = rung["code"], scope_label(rung.get("scope"))
    used, after, cap = rung["used"], rung["after"], rung["cap"]
    if code == TRADE_RISK_CAP:
        word = "over" if rung["binds"] else "within"
        return f"Risks {_money(after)}, {word} the {_money(cap)} per-trade limit"
    if code == DEPLOYMENT_CAP:
        return f"Open risk would reach {_money(after)} of {_money(cap)}"
    if code == SYMBOL_POSITION_CAP:
        return f"Already {used} of {cap} positions in {scope}"
    if code == SYMBOL_RISK_CAP:
        return f"{scope} risk would reach {_money(after)} of {_money(cap)}"
    if code == SECTOR_POSITION_CAP:
        if rung["binds"]:
            return f"{scope} is full ({used} of {cap} positions)"
        return f"{scope}: {used} of {cap} positions"
    if code == SECTOR_RISK_CAP:
        return f"{scope} risk would reach {_money(after)} of {_money(cap)}"
    if code == EXPIRY_POSITION_CAP:
        return f"Already {used} of {cap} positions expiring {scope}"
    return code
```

**Step 4: Run to verify they pass**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest shared/tests/test_book_caps.py -q
```

Expected: all pass.

**Step 5: Commit**

```bash
git add shared/book_caps.py shared/tests/test_book_caps.py
git commit -m "feat(shared): book_caps max_quantity and plain-language rung sentences" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 3: Rewire `concentration_reject` onto `book_caps` (money path, identical decisions)

**Files:**
- Modify: `options-scanner/paper_concentration.py`
- Test: `options-scanner/tests/test_book_caps_equivalence.py`

**Step 1: Write the equivalence test.** It freezes TODAY's function body verbatim so the comparison is against the real old behaviour, not a paraphrase.

```python
"""concentration_reject must decide EXACTLY as it did before book_caps.

The frozen copy below is the function as of 2026-09-15 (commit before this
change), trimmed of comments only. Do not edit it to make a test pass: a
mismatch means the adapter changed a money-path decision.
"""
import math
import random

import pytest

import paper_concentration as pc
from shared import sectors as _sectors
from shared.driver_policy import open_risk_dollars


def _frozen_group_of(symbol, sector_of=None):
    try:
        if sector_of is None:
            return _sectors.group_key(symbol)
        key = (symbol or "").strip().upper()
        if key is None:
            return None
        found = sector_of(key)
        return found if found else _sectors.group_key(key)
    except Exception:  # noqa: BLE001
        return None


def _frozen_finite(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def frozen_concentration_reject(positions, symbol, expiration, added_risk,
                                limits, equity=None, sector_of=None):
    rows = [p for p in positions or () if isinstance(p, dict)]
    pct = limits.get("max_deployed_risk_pct")
    eq = _frozen_finite(equity)
    if pct and eq and eq > 0:
        if open_risk_dollars(rows) + _frozen_finite(added_risk) > pct * eq:
            return "DEPLOYMENT_CAP"
    key = lambda v: (v or "").strip().upper()  # noqa: E731
    sym = key(symbol)
    same_symbol = [p for p in rows if key(p.get("symbol")) == sym]
    if len(same_symbol) >= limits["max_positions_per_symbol"]:
        return "SYMBOL_POSITION_CAP"
    if open_risk_dollars(same_symbol) + _frozen_finite(added_risk) > limits["max_risk_per_symbol"]:
        return "SYMBOL_RISK_CAP"
    max_sector_n = limits.get("max_positions_per_sector")
    max_sector_risk = limits.get("max_risk_per_sector")
    if max_sector_n or max_sector_risk:
        bucket = _frozen_group_of(symbol, sector_of)
        if bucket is not None:
            same_sector = [p for p in rows
                           if _frozen_group_of(p.get("symbol"), sector_of) == bucket]
            if max_sector_n and len(same_sector) >= max_sector_n:
                return "SECTOR_POSITION_CAP"
            if max_sector_risk and (open_risk_dollars(same_sector)
                                    + _frozen_finite(added_risk)) > max_sector_risk:
                return "SECTOR_RISK_CAP"
    exp = (expiration or "").strip()
    same_expiry = [p for p in rows if (p.get("expiration") or "").strip() == exp]
    if len(same_expiry) >= limits["max_positions_per_expiry"]:
        return "EXPIRY_POSITION_CAP"
    return None


SYMBOLS = ["ORCL", "orcl ", "MSFT", "AMD", "MU", "XOM", "SPY", "QQQ",
           "NOTAREALSYM", None, ""]
EXPIRIES = ["2026-10-17", " 2026-10-17", "2026-10-24", None, ""]
RISKS = [0.0, 50.0, 125.5, 250.0, 700.0, float("nan"), None, "junk", -10.0]


def _random_limits(rng):
    return {
        "max_positions_per_symbol": rng.choice([0, 1, 2, 3, 5]),
        "max_risk_per_symbol": rng.choice([0.0, 250.0, 750.0, 5000.0]),
        "max_positions_per_expiry": rng.choice([0, 1, 3, 5]),
        **({"max_positions_per_sector": rng.choice([0, 2, 5])} if rng.random() < .8 else {}),
        **({"max_risk_per_sector": rng.choice([0, 500.0, 1500.0])} if rng.random() < .8 else {}),
        **({"max_deployed_risk_pct": rng.choice([0, 0.05, 0.2])} if rng.random() < .8 else {}),
    }


def _random_book(rng):
    book = []
    for _ in range(rng.randint(0, 12)):
        row = {"symbol": rng.choice(SYMBOLS), "expiration": rng.choice(EXPIRIES)}
        if rng.random() < .8:
            row["max_loss_total"] = rng.choice(RISKS)
        else:
            row["max_loss"] = rng.choice(RISKS)
            row["quantity"] = rng.choice([1, 2, None])
        book.append(row)
    if rng.random() < .1:
        book.append("not a dict")
    return book


@pytest.mark.parametrize("seed", range(3000))
def test_adapter_decides_exactly_as_the_frozen_function(seed):
    rng = random.Random(seed)
    limits = _random_limits(rng)
    book = _random_book(rng)
    symbol, expiration = rng.choice(SYMBOLS), rng.choice(EXPIRIES)
    added = rng.choice(RISKS)
    equity = rng.choice([None, 0.0, 25000.0, 5000.0, float("nan")])
    sector_of = rng.choice([None, lambda s: "Tech" if s in ("ORCL", "MSFT", "AMD", "MU") else None])
    expected = frozen_concentration_reject(book, symbol, expiration, added, limits,
                                           equity=equity, sector_of=sector_of)
    got = pc.concentration_reject(book, symbol, expiration, added, limits=limits,
                                  equity=equity, sector_of=sector_of)
    assert got == expected, (seed, limits, book, symbol, expiration, added, equity)
```

**Step 2: Run it against the UNCHANGED module**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd options-scanner && "$PY" -m pytest tests/test_book_caps_equivalence.py -q -p no:randomly)
```

Expected: all 3000 PASS. This proves the frozen copy is faithful before anything changes. If any fail, the frozen copy is wrong; fix the COPY (compare against `git show HEAD:options-scanner/paper_concentration.py`), never the module.

**Step 3: Replace the body of `concentration_reject`** in `options-scanner/paper_concentration.py`. Keep the module docstring, `default_limits`, `_group_of` and `_finite`. Change the imports and the reason constants, and replace `concentration_reject` with:

```python
from shared.driver_policy import open_risk_dollars  # noqa: E402,F401  (kept: re-exported for callers)
from shared import sectors as _sectors  # noqa: E402
from shared import book_caps as _book_caps  # noqa: E402

# The reason codes live in shared.book_caps since 2026-09-15 and are re-exported
# here so every existing caller and log line keeps its spelling.
SYMBOL_POSITION_CAP = _book_caps.SYMBOL_POSITION_CAP
SYMBOL_RISK_CAP = _book_caps.SYMBOL_RISK_CAP
EXPIRY_POSITION_CAP = _book_caps.EXPIRY_POSITION_CAP
SECTOR_POSITION_CAP = _book_caps.SECTOR_POSITION_CAP
SECTOR_RISK_CAP = _book_caps.SECTOR_RISK_CAP
DEPLOYMENT_CAP = _book_caps.DEPLOYMENT_CAP


def concentration_reject(positions, symbol, expiration, added_risk,
                         limits=None, equity=None, sector_of=None):
    """Return the reason opening this candidate would breach a cap, else None.

    A thin adapter over ``shared.book_caps`` since 2026-09-15: sectors are
    resolved here exactly as before (``_group_of``, including the ``?SYMBOL``
    bucket and raise-means-no-grouping), then every rung is evaluated there and
    the first breach is reported in the Account's historical order - deployment,
    symbol, sector, expiry. ``tests/test_book_caps_equivalence.py`` holds the
    pre-change function frozen and proves the decisions are identical.

    (The rest of the old docstring's reasoning - why the deployment cap reports
    first, why sector precedes expiry, why no equity skips - still holds and is
    recorded on ``shared.book_caps``.)
    """
    limits = limits or default_limits()
    book = [dict(p, sector=_group_of(p.get("symbol"), sector_of))
            for p in positions or () if isinstance(p, dict)]
    candidate = {"symbol": symbol, "expiration": expiration,
                 "sector": _group_of(symbol, sector_of)}
    rungs = _book_caps.evaluate(book, candidate, added_risk, limits, equity)
    breach = _book_caps.first_breach(rungs, _book_caps.ACCOUNT_ORDER)
    return breach["code"] if breach else None
```

Keep the long explanatory comments from the old body by moving them onto `shared/book_caps.evaluate` next to the matching rung (copy, do not paraphrase). Delete the `_key` helper only if nothing else in the file uses it (`grep -n "_key(" options-scanner/paper_concentration.py`).

**Step 4: Run the equivalence test and every existing cap test**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd options-scanner && "$PY" -m pytest tests/test_book_caps_equivalence.py tests/test_paper_concentration.py tests/test_sector_cap.py tests/test_paper_engine_concentration.py tests/test_paper_engine.py -q -p no:randomly)
```

Expected: all pass. Then run the full options-scanner and options_svc suites and compare the failing set with the Task 0b baseline — it must be identical.

**Step 5: Commit**

```bash
git add options-scanner/paper_concentration.py options-scanner/tests/test_book_caps_equivalence.py shared/book_caps.py
git commit -m "refactor(paper): concentration_reject is an adapter over shared.book_caps" -m "Decisions proven identical to the frozen pre-change function over 3000 generated books." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 4: Put `shared.book_caps` on the Tier-1 allow-list, and prove it drags nothing in

**Files:**
- Test: `webgui/tests/test_book_caps_tier1.py`
- Modify: `CLAUDE.md` (the "Tier-1 import allow-list" paragraph near line 100)

**Step 1: Write the test**

```python
"""Tier 1 may import shared.book_caps because it is pure math.

Run in a FRESH interpreter so a transitive import cannot hide behind a module
some earlier test already loaded.
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

PROBE = r"""
import sys
sys.path.insert(0, r"%s")
before = set(sys.modules)
import shared.book_caps
new = set(sys.modules) - before
bad = sorted(m for m in new if m.split(".")[0] in {
    "sqlite3", "redis", "fakeredis", "requests", "pandas", "numpy", "nicegui",
    "services", "paper_trader", "paper_concentration", "config_paper",
    "scanner_engine", "tomllib"} or m in {"shared.sectors", "shared.scanner_config",
                                         "shared.config_toml", "repo_paths"})
print("BAD:" + ",".join(bad))
""" % REPO


def test_book_caps_imports_nothing_tier1_forbids():
    out = subprocess.run([sys.executable, "-c", PROBE], capture_output=True,
                         text=True, check=True).stdout
    assert out.strip() == "BAD:", out
```

**Step 2: Run it**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd webgui && "$PY" -m pytest tests/test_book_caps_tier1.py -q)
```

Expected: PASS (book_caps imports only `math` and `shared.driver_policy`). If it FAILS, `shared.driver_policy` has grown an import; report it rather than widening the probe.

**Step 3: Edit CLAUDE.md in place.** In the allow-list paragraph, after the `shared.calibration` sentence, add:

> `shared.book_caps` (since 2026-09-15; pure — `math` plus `shared.driver_policy.open_risk_dollars`, itself math-only) — the Paper dialog's preview must evaluate the SAME rungs the service enforces, so it imports the one cap module rather than a Tier-1 copy; `webgui/tests/test_book_caps_tier1.py` pins that it drags nothing in.

**Step 4: Commit**

```bash
git add webgui/tests/test_book_caps_tier1.py CLAUDE.md
git commit -m "docs(tier1): shared.book_caps joins the import allow-list, pinned by test" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Phase 2 — The Ledger enforces the caps (behaviour change)

> Before starting: hand the Task 0a numbers to the operator. This phase makes the Paper button refuse trades.

### Task 5: `compute.ledger_book_state`

> **Operator decision, 2026-09-15 (after the Task 0a measurement):** the Ledger's per-trade limit is **$750**, its own constant `config_paper.LEDGER_MAX_RISK_PER_TRADE`; the Account keeps `MAX_RISK_PER_TRADE = $250`. At $250, 62% of Directional long options were unopenable; at $750, 21-23%.

**Files:**
- Modify: `options-scanner/config_paper.py` - add, directly after `MAX_RISK_PER_TRADE`:
  ```python
  # The Paper LEDGER's own per-trade limit (the book the webgui Paper button opens
  # into). Separate from MAX_RISK_PER_TRADE, which sizes the automatic Account and
  # the scanner's width search: at $250 about two thirds of Directional long
  # options could not be opened by hand (measured on prod 2026-09-15); at $750
  # about a fifth. Operator decision, 2026-09-15.
  LEDGER_MAX_RISK_PER_TRADE = 750.0
  ```
- Modify: `services/options_svc/compute.py` (add beside `paper_trades_view`, ~line 2626)
- Test: `services/options_svc/tests/test_ledger_caps.py`

**Step 1: Write the failing tests**

```python
"""The Paper Ledger's book, limits and equity basis for the risk caps."""
import pytest

from services.options_svc import compute


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A real trades.db under tmp_path (same shape as test_ledger_manage_end_to_end)."""
    import paper_trader
    import trade_tracker_client
    import trades_db

    monkeypatch.setattr(trades_db, "DEFAULT_DB_PATH", tmp_path / "trades.db")
    monkeypatch.setattr(trades_db, "_initialised", set())
    monkeypatch.setattr(trade_tracker_client, "track", lambda t: True)
    monkeypatch.setattr(trade_tracker_client, "untrack", lambda tid: True)
    return paper_trader


def _pcs(symbol="ORCL", expiration="2026-10-17", credit=0.60, width=2.5):
    return {"symbol": symbol, "type": "PCS", "trade_type": "SWING",
            "expiration": expiration, "dte": 30, "short_strike": 100.0,
            "long_strike": 100.0 - width, "width": width, "credit": credit,
            "max_loss": round(width - credit, 2)}


def test_open_trades_carry_symbol_expiry_risk_and_sector(ledger):
    ledger.add_trade(ledger.create_paper_trade(_pcs(), 1))
    state = compute.ledger_book_state()
    assert len(state["open"]) == 1
    row = state["open"][0]
    assert row["symbol"] == "ORCL" and row["expiration"] == "2026-10-17"
    assert row["max_loss_total"] == pytest.approx(190.0)
    assert row["sector"] == "Information Technology"


def test_equity_is_starting_balance_plus_closed_realized_pnl(ledger):
    t = ledger.create_paper_trade(_pcs(), 1)
    ledger.add_trade(t)
    closed = ledger.close_paper_trade(t, 0.10)         # +$50
    ledger.update_trade(t["trade_id"], closed)
    state = compute.ledger_book_state()
    assert state["open"] == []
    assert state["realized_pnl"] == pytest.approx(50.0)
    assert state["equity"] == pytest.approx(25050.0)


def test_limits_are_the_accounts_plus_the_per_trade_cap(ledger):
    import config_paper
    import paper_concentration
    limits = compute.ledger_book_state()["limits"]
    assert limits == {**paper_concentration.default_limits(),
                      "max_risk_per_trade": config_paper.LEDGER_MAX_RISK_PER_TRADE}


def test_a_non_finite_realized_pnl_is_dropped_not_summed(ledger, monkeypatch):
    rows = [{"status": "CLOSED", "realized_pnl": float("nan")},
            {"status": "EXPIRED", "realized_pnl": 20.0}]
    state = compute.ledger_book_state(trades=rows)
    assert state["realized_pnl"] == pytest.approx(20.0)
```

**Step 2: Run to verify they fail**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest services/options_svc/tests/test_ledger_caps.py -q
```

Expected: FAIL, `AttributeError: module ... has no attribute 'ledger_book_state'`.

**Step 3: Implement** (add after `paper_trades_view`):

```python
def ledger_book_state(trades=None) -> dict:
    """The Paper LEDGER as the risk caps see it (design 2026-09-15, Part 1).

    ``open``: OPEN trades as ``shared.book_caps`` rows, sectors resolved with
    ``shared.sectors.group_key`` (an unmapped name is its own ``?SYMBOL``
    bucket). ``equity``: ``STARTING_BALANCE`` plus the realized P&L of every
    closed or expired Ledger trade - it moves when a trade closes, never on a
    mark (operator decision). ``limits``: the Account's six caps plus the
    Ledger's own ``LEDGER_MAX_RISK_PER_TRADE`` ($750; the Account's per-trade
    limit stays $250), read at call time so a config edit plus a restart moves
    them.
    """
    import config_paper
    import paper_concentration
    import paper_trader
    from shared import sectors as _sectors

    if trades is None:
        trades = paper_trader.get_all_trades()
    open_rows, realized = [], 0.0
    for t in trades or ():
        if not isinstance(t, dict):
            continue
        if t.get("status") == "OPEN":
            open_rows.append({"symbol": t.get("symbol"),
                              "expiration": t.get("expiration"),
                              "max_loss_total": t.get("max_loss_total"),
                              "sector": _sectors.group_key(t.get("symbol"))})
            continue
        try:
            pnl = float(t.get("realized_pnl"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(pnl):
            realized += pnl
    start = float(config_paper.STARTING_BALANCE)
    limits = dict(paper_concentration.default_limits(),
                  max_risk_per_trade=config_paper.LEDGER_MAX_RISK_PER_TRADE)
    return {"open": open_rows, "starting_balance": start,
            "realized_pnl": round(realized, 2),
            "equity": round(start + realized, 2), "limits": limits}
```

Confirm `math` is imported at the top of `compute.py` (`grep -n "^import math" services/options_svc/compute.py`); add it if not.

**Step 4: Run to verify they pass** (same command). Expected: PASS.

**Step 5: Commit**

```bash
git add options-scanner/config_paper.py services/options_svc/compute.py services/options_svc/tests/test_ledger_caps.py
git commit -m "feat(options-svc): ledger_book_state - the Paper Ledger as the caps see it" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 6: `compute.create_paper_trade` refuses a breach and returns an outcome

**Files:**
- Modify: `services/options_svc/compute.py:2656-2667`
- Modify: `services/options_svc/tests/test_compute.py:915-938` (planned contract change, below)
- Test: `services/options_svc/tests/test_ledger_caps.py`

**Step 1: Write the failing tests** (append to `test_ledger_caps.py`)

```python
def test_a_trade_within_every_cap_opens_and_reports_it(ledger):
    out = compute.create_paper_trade(_pcs(), 1)
    assert out["status"] == "opened"
    assert out["trade_id"] and out["trade"]["trade_id"] == out["trade_id"]
    assert [t["trade_id"] for t in ledger.get_open_trades()] == [out["trade_id"]]


def test_over_the_per_trade_cap_is_refused_and_nothing_is_written(ledger):
    out = compute.create_paper_trade(_pcs(credit=1.00, width=10.0), 1)  # $900
    assert out["status"] == "refused"
    assert out["code"] == "TRADE_RISK_CAP"
    assert out["max_quantity"] == 0
    assert "over the $750 per-trade limit" in out["message"]
    assert ledger.get_open_trades() == []


def test_quantity_counts_toward_the_per_trade_cap(ledger):
    out = compute.create_paper_trade(_pcs(), 4)          # 4 x $190 = $760
    assert out["status"] == "refused" and out["code"] == "TRADE_RISK_CAP"
    assert out["max_quantity"] == 3


def test_the_account_limit_does_not_bind_the_ledger(ledger):
    """$400 is over the Account's $250 but inside the Ledger's own $750."""
    out = compute.create_paper_trade(_pcs(credit=1.00, width=5.0), 1)
    assert out["status"] == "opened"


def test_a_fourth_position_in_one_symbol_is_refused(ledger):
    for _ in range(3):
        assert compute.create_paper_trade(_pcs(), 1)["status"] == "opened"
    out = compute.create_paper_trade(_pcs(expiration="2026-10-24"), 1)
    assert out["status"] == "refused" and out["code"] == "SYMBOL_POSITION_CAP"
    assert len(ledger.get_open_trades()) == 3


def test_the_limit_really_binds_when_config_moves(ledger, monkeypatch):
    """Discriminating: the cap is read at call time, not copied from a literal."""
    import config_paper
    monkeypatch.setattr(config_paper, "LEDGER_MAX_RISK_PER_TRADE", 100.0)
    out = compute.create_paper_trade(_pcs(), 1)          # $190
    assert out["status"] == "refused" and out["code"] == "TRADE_RISK_CAP"


def test_every_outcome_carries_the_rungs_in_display_order(ledger):
    from shared import book_caps
    out = compute.create_paper_trade(_pcs(), 1)
    assert [r["code"] for r in out["rungs"]] == list(book_caps.DISPLAY_ORDER)


def test_an_untradeable_signal_is_an_error_outcome_not_a_raise(ledger):
    out = compute.create_paper_trade({"symbol": "SPY", "type": "LONG_STRADDLE"}, 1)
    assert out["status"] == "error" and "not paper-tradeable" in out["message"]


def test_a_trade_whose_max_loss_cannot_be_read_is_refused_not_opened(ledger):
    """book_caps counts an unusable risk as ZERO (the Account sized its trades
    first). The Ledger did not, so it must refuse here - otherwise a debit signal
    with no max_loss books max_loss_total 0.0 and clears every risk rung, while
    the preview (which needs a positive risk) says it cannot check."""
    sig = {"symbol": "SPY", "type": "LONG_CALL", "expiration": "2026-10-17",
           "net_debit": 300.0, "legs": [{"kind": "call", "side": "long", "strike": 500}]}
    out = compute.create_paper_trade(sig, 1)
    assert out["status"] == "error"
    assert out["message"] == "The trade's max loss could not be read, so the risk caps cannot be checked."
    assert ledger.get_open_trades() == []
```

**Step 2: Run to verify they fail.** Expected: FAIL (the current function returns the trade dict and never refuses).

**Step 3: Replace `create_paper_trade`**

```python
def create_paper_trade(signal: dict, qty: int) -> dict:
    """Create a Paper LEDGER trade if it clears every risk cap; return the outcome.

    Since 2026-09-15 the Ledger enforces the Account's six caps plus its own
    $750 per-trade limit (``LEDGER_MAX_RISK_PER_TRADE``), against its OWN open trades (design
    2026-09-15-trade-checklist-and-ledger-caps). The checked risk is the
    ``max_loss_total`` of the trade ``paper_trader`` builds - the number it
    books - so the per-share/per-contract unit traps cannot separate them.

    Returns ``{"status": "opened"|"refused"|"error", "symbol", "type",
    "expiration", "qty", "rungs", ...}``: ``opened`` adds ``trade_id`` and
    ``trade``; ``refused`` adds ``code``, ``message`` and ``max_quantity``;
    ``error`` adds ``message``. Never raises for a bad signal - a refusal the
    screen cannot see is a button that does nothing.
    """
    import paper_trader
    from shared import book_caps
    from shared import sectors as _sectors

    sig = signal or {}
    base = {"symbol": sig.get("symbol"), "type": sig.get("type"),
            "expiration": sig.get("expiration"), "qty": int(qty), "rungs": []}
    try:
        trade = paper_trader.create_paper_trade(sig, int(qty))
    except (ValueError, KeyError, TypeError) as exc:
        return {**base, "status": "error", "message": str(exc)}
    # shared.book_caps counts an unusable candidate risk as ZERO, which is right
    # for the Account (its entry cycle sized the trade first) and wrong here.
    risk = trade.get("max_loss_total")
    if not (isinstance(risk, (int, float)) and not isinstance(risk, bool)
            and math.isfinite(risk) and risk > 0):
        return {**base, "status": "error",
                "message": "The trade's max loss could not be read, so the risk "
                           "caps cannot be checked."}

    book = ledger_book_state()
    candidate = {"symbol": trade["symbol"], "expiration": trade["expiration"],
                 "sector": _sectors.group_key(trade["symbol"])}
    rungs = book_caps.evaluate(book["open"], candidate, trade["max_loss_total"],
                               book["limits"], book["equity"])
    base["rungs"] = rungs
    breach = book_caps.first_breach(rungs, book_caps.DISPLAY_ORDER)
    if breach is not None:
        per = (trade["max_loss_total"] / int(qty)) if int(qty) else None
        return {**base, "status": "refused", "code": breach["code"],
                "message": book_caps.describe(breach),
                "max_quantity": book_caps.max_quantity(
                    book["open"], candidate, per, book["limits"], book["equity"])}
    paper_trader.add_trade(trade)
    return {**base, "status": "opened", "trade_id": trade["trade_id"],
            "trade": trade}
```

**Step 4: Update the existing unit test whose contract this deliberately changes.** Replace the body of `test_create_paper_trade_creates_then_adds` in `services/options_svc/tests/test_compute.py` so it keeps its original intent (build, then add, in that order, with signal and qty) and asserts the new return shape:

```python
def test_create_paper_trade_creates_then_adds(monkeypatch):
    """create_paper_trade builds the trade via paper_trader.create_paper_trade,
    persists it via add_trade (in that order) when it clears the caps, and
    returns an ``opened`` outcome carrying the created trade (the return was the
    bare trade dict until the Ledger became capped on 2026-09-15)."""
    import sys as _sys
    import types as _types

    order = []
    trade = {"trade_id": "T9", "symbol": "SPY", "expiration": "2026-10-17",
             "max_loss_total": 100.0}
    signal = {"symbol": "SPY", "type": "PCS"}

    def _create(sig, qty):
        order.append(("create", sig, qty))
        return trade

    def _add(t):
        order.append(("add", t))

    monkeypatch.setitem(_sys.modules, "paper_trader",
                        _types.SimpleNamespace(create_paper_trade=_create, add_trade=_add,
                                               get_all_trades=lambda: []))

    out = compute.create_paper_trade(signal, 2)
    assert out["status"] == "opened" and out["trade"] is trade
    # create runs before add, with the signal + qty; add gets the created trade.
    assert order == [("create", signal, 2), ("add", trade)]
```

**Step 5: Run the new tests, the changed test, and both full suites**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest services/options_svc/tests/test_ledger_caps.py services/options_svc/tests/test_compute.py -q
```

Expected: PASS. Then run the full options_svc suite and compare the failing set against Task 0b.

**Step 6: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_ledger_caps.py services/options_svc/tests/test_compute.py
git commit -m "feat(paper-ledger): the Paper button enforces the book's risk caps" -m "Per-trade \$750 (the Ledger's own limit) plus the Account's six concentration rungs, against the Ledger's own open trades and \$25k + realized P&L. A refusal writes nothing." -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 7: Publish every `paper_create` outcome to `cache:options:paper_create`

**Files:**
- Modify: `services/options_svc/handlers.py` (constants near line 190; `_publish_income_open` near 2338; the `paper_create` branch at 2545)
- Test: `services/options_svc/tests/test_paper_create_result.py`

**Step 1: Write the failing tests**

```python
"""Every paper_create outcome reaches the screen."""
import datetime as _dt

from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from services.options_svc import handlers


def _stub(monkeypatch, outcome):
    monkeypatch.setattr(handlers.compute, "create_paper_trade", lambda s, q: outcome)
    monkeypatch.setattr(handlers.compute, "paper_trades_view", lambda reprice=True: {"trades": []})


def test_a_refusal_is_published_with_its_reason(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "refused", "code": "TRADE_RISK_CAP",
                        "message": "Risks $400, over the $250 per-trade limit",
                        "max_quantity": 0, "symbol": "MU", "rungs": []})
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "MU"}, "qty": 1}))
    env = bus.cache_get(handlers.CACHE_PAPER_CREATE)
    assert env is not None
    assert env.payload["status"] == "refused"
    assert env.payload["code"] == "TRADE_RISK_CAP"
    assert env.payload["seq"] >= 1 and env.payload["ts"]


def test_two_identical_refusals_are_two_distinct_publishes(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "refused", "code": "X", "message": "m", "rungs": []})
    cmd = Command(type="paper_create", args={"signal": {"symbol": "MU"}, "qty": 1})
    handlers.handle_command(bus, cmd)
    first = bus.cache_get(handlers.CACHE_PAPER_CREATE)
    handlers.handle_command(bus, cmd)
    second = bus.cache_get(handlers.CACHE_PAPER_CREATE)
    assert second.payload["seq"] == first.payload["seq"] + 1
    assert second.version > first.version


def test_an_opened_trade_is_published_without_the_full_trade_dict(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "opened", "trade_id": "ab12", "symbol": "SPY",
                        "trade": {"trade_id": "ab12"}, "rungs": []})
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "SPY"}, "qty": 1}))
    payload = bus.cache_get(handlers.CACHE_PAPER_CREATE).payload
    assert payload["status"] == "opened" and payload["trade_id"] == "ab12"
    assert "trade" not in payload


def test_a_stale_command_is_published_too(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, {"status": "opened"})
    old = (_dt.datetime.now(_dt.timezone.utc)
           - _dt.timedelta(seconds=handlers.STALE_OPEN_MAX_AGE_SEC + 60)).isoformat()
    handlers.handle_command(bus, Command(type="paper_create", ts=old,
                                         args={"signal": {"symbol": "SPY"}, "qty": 1}))
    payload = bus.cache_get(handlers.CACHE_PAPER_CREATE).payload
    assert payload["status"] == "stale" and payload["symbol"] == "SPY"


def test_a_stub_returning_nothing_publishes_an_error_not_a_crash(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    _stub(monkeypatch, None)
    handlers.handle_command(bus, Command(type="paper_create",
                                         args={"signal": {"symbol": "SPY"}, "qty": 1}))
    assert bus.cache_get(handlers.CACHE_PAPER_CREATE).payload["status"] == "error"
```

**Step 2: Run to verify they fail.** Expected: `AttributeError: ... CACHE_PAPER_CREATE`.

**Step 3: Implement.** Constants beside `CACHE_INCOME_OPEN`:

```python
# The OUTCOME of one ``paper_create`` - opened, refused by a risk cap, stale, or
# an error - which the Scanner and Strategy Finder turn into a toast. Same shape
# and reasoning as CACHE_INCOME_OPEN: written on EVERY outcome, a per-publish
# ``seq`` so two identical refusals are two visible answers, and a TTL so an old
# answer is not read as fresh after a restart. Until 2026-09-15 a refused
# paper_create raised and was dead-lettered: a button that did nothing.
CACHE_PAPER_CREATE = "cache:options:paper_create"
EVENT_PAPER_CREATE = "events:options:paper_create"
PAPER_CREATE_TTL_SEC = 600
```

Publisher beside `_publish_income_open`:

```python
_PAPER_CREATE_SEQ = 0


def _publish_paper_create(bus, result: dict) -> None:
    """Cache one ``paper_create`` outcome with a ``seq`` and ``ts`` (see
    CACHE_PAPER_CREATE). The booked trade dict is dropped: the page needs the
    answer, not the row, and the Ledger view carries the row."""
    global _PAPER_CREATE_SEQ
    _PAPER_CREATE_SEQ += 1
    payload = {k: v for k, v in (result or {}).items() if k != "trade"}
    payload.setdefault("status", "error")
    payload["seq"] = _PAPER_CREATE_SEQ
    payload.setdefault("ts", _dt.datetime.now(mc.CT).isoformat())
    version = bus.cache_set(CACHE_PAPER_CREATE, payload, ttl=PAPER_CREATE_TTL_SEC)
    bus.publish(EVENT_PAPER_CREATE, {"version": version})
```

The `paper_create` branch becomes:

```python
    elif command.type == "paper_create":
        sig = command.args.get("signal") or {}
        if _is_stale_open(command):
            age = _command_age_seconds(command)
            log.warning(
                "REJECTED stale paper_create for %s: age %.0fs > %ds (enqueue ts=%s)",
                sig.get("symbol"), age or -1, STALE_OPEN_MAX_AGE_SEC,
                getattr(command, "ts", None))
            _record_open_result({"status": "rejected", "reason": "stale_command",
                                 "symbol": sig.get("symbol"),
                                 "age_sec": round(age or 0, 1), "source": "manual"})
            _publish_paper_create(bus, {"status": "stale", "symbol": sig.get("symbol"),
                                        "type": sig.get("type"),
                                        "message": "The request was too old to act on "
                                                   "(the service was restarting)."})
        else:
            outcome = compute.create_paper_trade(sig, command.args.get("qty", 1)) or {}
            if outcome.get("status") == "refused":
                log.info("REFUSED paper_create %s %s: %s", sig.get("symbol"),
                         outcome.get("code"), outcome.get("message"))
            _publish_paper_create(bus, outcome)
        refresh_paper_trades(bus)
```

Note `test_paper_create_command` / `test_paper_create_defaults_qty` stub `create_paper_trade` with a lambda that returns `None`; `or {}` handles that and they keep passing unchanged. `test_r5_stale_manual_paper_create_is_rejected` must still pass unchanged.

**Step 4: Run**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; "$PY" -m pytest services/options_svc/tests/test_paper_create_result.py services/options_svc/tests/test_handlers.py services/options_svc/tests/test_handlers_driver_paper.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_paper_create_result.py
git commit -m "feat(options-svc): publish every paper_create outcome, refusals included" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 8: The Scanner and Strategy Finder toast the outcome

**Files:**
- Modify: `webgui/pages/options/handoff.py` (`send_to_paper` toast text; new `paper_result_toast`, `watch_paper_results`)
- Modify: `webgui/pages/options/scanner.py` (`render`, after the tables are built)
- Modify: `webgui/pages/options/swing.py` (`render`, beside `ui.timer(2.0, _maybe_repaint)`)
- Test: `webgui/tests/test_paper_create_toast.py`

**Step 1: Write the failing tests**

```python
"""The Paper button's answer becomes a toast. PURE part only."""
from pages.options import handoff


def test_opened():
    assert handoff.paper_result_toast({"status": "opened", "symbol": "SPY", "qty": 2,
                                       "type": "PCS"}) == (
        "Opened 2 × SPY PCS in the paper ledger.", "positive")


def test_refused_names_the_cap_and_the_quantity_that_fits():
    text, kind = handoff.paper_result_toast({
        "status": "refused", "symbol": "ORCL", "code": "SYMBOL_POSITION_CAP",
        "message": "ORCL already holds 3 of 3 positions", "max_quantity": 0})
    assert kind == "warning"
    assert text == "Not opened — ORCL already holds 3 of 3 positions."


def test_refused_with_room_for_a_smaller_quantity_says_so():
    text, _ = handoff.paper_result_toast({
        "status": "refused", "symbol": "SPY", "code": "TRADE_RISK_CAP",
        "message": "Risks $900, over the $750 per-trade limit", "max_quantity": 1})
    assert text.endswith("Up to 1 contract fits.")


def test_stale_and_error():
    assert handoff.paper_result_toast({"status": "stale", "message": "m"})[1] == "warning"
    assert handoff.paper_result_toast({"status": "error", "message": "boom"}) == (
        "Not opened — boom.", "negative")


def test_nothing_to_say_for_an_empty_payload():
    assert handoff.paper_result_toast(None) is None
```

**Step 2: Run to verify they fail**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd webgui && "$PY" -m pytest tests/test_paper_create_toast.py -q)
```

Expected: `AttributeError: ... paper_result_toast`.

**Step 3: Implement in `handoff.py`**

```python
PAPER_CREATE_VIEW = "options:paper_create"


def paper_result_toast(payload):
    """``(text, notify type)`` for one ``paper_create`` outcome, or None. PURE."""
    if not isinstance(payload, dict) or not payload.get("status"):
        return None
    status = payload["status"]
    if status == "opened":
        qty = payload.get("qty") or 1
        return (f"Opened {qty} × {payload.get('symbol', '')} {payload.get('type', '')} "
                f"in the paper ledger.", "positive")
    message = (payload.get("message") or "the service gave no reason").rstrip(".")
    if status == "refused":
        text = f"Not opened — {message}."
        fits = payload.get("max_quantity")
        if isinstance(fits, int) and fits > 0:
            text += f" Up to {fits} contract{'' if fits == 1 else 's'} fits."
        return text, "warning"
    if status == "stale":
        return f"Not opened — {message}.", "warning"
    return f"Not opened — {message}.", "negative"


def watch_paper_results():
    """Toast every ``paper_create`` answer on this page. Seeds the version, so an
    answer published before the page opened is not replayed."""
    from ..view_watch import watch_view

    def _on_change():
        toast = paper_result_toast(bus_client.read(PAPER_CREATE_VIEW))
        if toast:
            ui.notify(toast[0], type=toast[1])

    return watch_view(PAPER_CREATE_VIEW, _on_change)
```

In `send_to_paper.confirm`, change the toast so it promises only what happened:

```python
            ui.notify("Sent — the paper ledger answers in a moment.", type="info")
```

In `scanner.render`, after `handoff.add_strategy_row_actions(table_dir, ...)`, add `handoff.watch_paper_results()`. In `swing.render`, beside `ui.timer(2.0, _maybe_repaint)`, add `handoff.watch_paper_results()`.

**Step 4: Run the new test and the existing handoff/page tests**

```bash
PY="D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe"; (cd webgui && "$PY" -m pytest tests/test_paper_create_toast.py tests/test_options_handoff.py tests/test_options_page.py tests/test_options_swing.py tests/test_no_inline_style.py -q)
```

Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/pages/options/scanner.py webgui/pages/options/swing.py webgui/tests/test_paper_create_toast.py
git commit -m "feat(webgui): the Paper button's answer - opened or refused and why - is a toast" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 9: Phase 2 docs

**Files:**
- Modify: `CLAUDE.md` — add a section after "The paper engine's risk envelope has SIX rungs, not two": **"The Paper Ledger is capped, and there is one cap module"** (5–8 sentences: the Ledger enforces its own per-trade `LEDGER_MAX_RISK_PER_TRADE` ($750; the Account stays $250) + the six rungs against its own open trades; equity = $25k + realized; `shared.book_caps` is shared by the Account, the Ledger and the preview; `concentration_reject` is an adapter proven identical by a frozen copy; every outcome is published to `cache:options:paper_create`). Also correct in place the sentence in the rungs section that says concentration refusals leave no UI trace — true for the Account's auto cycle, no longer for the Paper button.
- Modify: `docs/manuals/` User Guide — the Paper trade section: the button can now refuse, and the toast says why.
- Modify: `webgui/page_help.py` — `/options/scanner` and `/options/swing` entries: one sentence each about the Paper button's caps.
- Modify: `docs/CHANGELOG.md` — dated entry for Phases 1–2.

**Step 1:** Make the edits. Correct wrong sentences in place; do not append corrections.

**Step 2:** Run `(cd webgui && "$PY" -m pytest tests -q -k "help or manual")` and the docs build if the User Guide has one (`grep -n MANUALS build_docs.py`). Expected: PASS.

**Step 3: Commit**

```bash
git add CLAUDE.md docs/CHANGELOG.md webgui/page_help.py docs/manuals/<the edited User Guide file>
git commit -m "docs: the Paper Ledger is capped; one cap module" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Phase 3 — Row stamps, the published Ledger book, and the Paper dialog preview

### Task 10: `compute.stamp_candidate`

**Files:**
- Modify: `services/options_svc/compute.py` (new section after `ledger_book_state`)
- Modify: `services/options_svc/compute.py` `swing_scan`, the `for s in signals: s["iv_rank"] = iv_rank` loop (~line 960): also set `s["daily_em"] = dem`
- Test: `services/options_svc/tests/test_candidate_stamps.py`

**Step 1: Write the failing tests.** Rows must come from the REAL producers.

```python
"""Per-candidate stamps the checklist reads. Rows are built by real producers."""
import math

import pytest

from services.options_svc import compute


def _raw_pcs():
    """A screen_spreads-shaped PCS - the exact keys the scanner emits."""
    return {"id": "ORCL_PCS_2026-10-17_100.0_97.5", "symbol": "ORCL", "type": "PCS",
            "trade_type": "SWING", "expiration": "2026-10-17", "dte": 12,
            "short_strike": 100.0, "long_strike": 97.5, "width": 2.5,
            "short_mark": 0.95, "long_mark": 0.35, "credit": 0.60, "max_loss": 1.90,
            "spread_bid": 0.55, "spread_ask": 0.65, "net_vega": 0.02,
            "underlying_price": 110.0,
            "expected_moves": {"daily": {"move_dollars": 2.0}}}


def test_ledger_risk_equals_what_the_ledger_books_for_one_contract():
    import paper_trader
    row = _raw_pcs()
    stamped = compute.stamp_candidate(dict(row), trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] == pytest.approx(
        paper_trader.create_paper_trade(row, 1)["max_loss_total"])
    assert stamped["ledger_risk_per_contract"] == pytest.approx(190.0)


def test_ledger_risk_for_a_normalized_spread_is_not_100x():
    import strategy_scanner
    norm = strategy_scanner.adapt_credit_spread(_raw_pcs())
    stamped = compute.stamp_candidate(dict(norm), trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] == pytest.approx(190.0, abs=1.0)


def test_ledger_risk_is_none_for_a_structure_the_ledger_refuses():
    stamped = compute.stamp_candidate({"symbol": "SPY", "type": "LONG_STRADDLE"},
                                      trade_type="SWING")
    assert stamped["ledger_risk_per_contract"] is None


def test_friction_is_the_spread_width_over_the_credit():
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING")
    assert stamped["friction_pct"] == pytest.approx(0.10 / 0.60 * 100)


def test_friction_sums_leg_widths_when_there_are_no_spread_quotes():
    row = {"symbol": "X", "type": "LONG_CALL", "net_debit": 300.0,
           "legs": [{"side": "long", "kind": "call", "bid": 2.90, "ask": 3.10}]}
    stamped = compute.stamp_candidate(row, trade_type="SWING")
    assert stamped["friction_pct"] == pytest.approx(0.20 / 3.00 * 100)


def test_friction_is_none_when_a_leg_has_no_quote():
    row = {"symbol": "X", "type": "LONG_CALL", "net_debit": 300.0,
           "legs": [{"side": "long", "kind": "call"}]}
    assert compute.stamp_candidate(row, trade_type="SWING")["friction_pct"] is None


def test_em_to_expiry_uses_the_daily_move_times_root_dte():
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING")
    assert stamped["em_to_expiry"] == pytest.approx(2.0 * math.sqrt(12))


def test_em_to_expiry_reads_a_finder_rows_daily_em():
    row = {"symbol": "X", "type": "PCS", "dte": 0, "daily_em": 1.5}
    assert compute.stamp_candidate(row, trade_type="SWING")["em_to_expiry"] == pytest.approx(1.5)


def test_vol_floor_is_the_trade_types_floor():
    from shared import scanner_config
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="0-DTE")
    assert stamped["vol_floor"] == scanner_config.min_iv_rank().get("0-DTE")


def test_earnings_are_stamped_from_the_lookup_given(monkeypatch):
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING",
                                      earnings=("upcoming", "2026-10-09"))
    assert (stamped["earnings_status"], stamped["earnings_date"]) == ("upcoming", "2026-10-09")


def test_iv_rank_known_is_stamped_when_given():
    stamped = compute.stamp_candidate(_raw_pcs(), trade_type="SWING", iv_rank_known=False)
    assert stamped["iv_rank_known"] is False
```

**Step 2: Run to verify they fail.** Expected: `AttributeError: ... stamp_candidate`.

**Step 3: Implement**

```python
def _num_or_none(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def ledger_risk_per_contract(row):
    """The max loss the Paper LEDGER would book for ONE contract of ``row``, or
    None if the Ledger refuses the structure. Uses ``paper_trader`` itself, so
    the preview and the enforcement can never compute different units."""
    import paper_trader
    try:
        total = paper_trader.create_paper_trade(row, 1).get("max_loss_total")
    except Exception:  # noqa: BLE001 - an untradeable row simply has no figure
        return None
    v = _num_or_none(total)
    return v if v is not None and v > 0 else None


def _friction_pct(row):
    """Round-trip bid-ask width as a percent of the per-share credit or debit.

    ``spread_bid``/``spread_ask`` are built from BOTH legs (short.bid - long.ask,
    short.ask - long.bid), so their difference is the whole round trip; an iron
    condor sums its two sides'. Normalized rows without them sum ask - bid over
    their legs. None when any figure is missing - never a guessed zero."""
    credit = _num_or_none(row.get("credit"))
    if credit is None:
        nc, nd = _num_or_none(row.get("net_credit")), _num_or_none(row.get("net_debit"))
        credit = (nc / 100.0) if nc else ((nd / 100.0) if nd else None)
    if not credit:
        return None
    sb, sa = _num_or_none(row.get("spread_bid")), _num_or_none(row.get("spread_ask"))
    if sb is not None and sa is not None and sa >= sb:
        width = sa - sb
    else:
        legs = [l for l in row.get("legs") or () if isinstance(l, dict)
                and str(l.get("kind", "")).lower() in ("call", "put")]
        if not legs:
            return None
        width = 0.0
        for leg in legs:
            b, a = _num_or_none(leg.get("bid")), _num_or_none(leg.get("ask"))
            if b is None or a is None or a < b:
                return None
            width += (a - b) * (_num_or_none(leg.get("qty")) or 1.0)
    return round(width / abs(credit) * 100.0, 1)


def _em_to_expiry(row):
    """Daily expected move x sqrt(max(DTE, 1)) - the convention
    ``strategy_scoring.score_all(daily_move=...)`` uses, so the check and the
    score measure distance the same way."""
    daily = _num_or_none(row.get("daily_em"))
    if daily is None:
        em = (row.get("expected_moves") or {}).get("daily")
        daily = _num_or_none(em.get("move_dollars")) if isinstance(em, dict) else _num_or_none(em)
    dte = _num_or_none(row.get("dte"))
    if not daily or dte is None:
        return None
    return round(daily * math.sqrt(max(dte, 1.0)), 4)


def stamp_candidate(row, *, trade_type, earnings=None, iv_rank_known=None):
    """Stamp the facts the checklist needs onto one candidate row (in place;
    returns it). Design 2026-09-15, Part 2. Every stamp is None when unknown."""
    row["ledger_risk_per_contract"] = ledger_risk_per_contract(row)
    row["friction_pct"] = _friction_pct(row)
    row["em_to_expiry"] = _em_to_expiry(row)
    row["vol_floor"] = _scanner_config.min_iv_rank().get(trade_type)
    if earnings is not None:
        row["earnings_status"], row["earnings_date"] = earnings
    if iv_rank_known is not None:
        row["iv_rank_known"] = bool(iv_rank_known)
    return row
```

In `swing_scan` add `s["daily_em"] = dem` inside the existing `for s in signals: s["iv_rank"] = iv_rank` loop (confirm `dem` is in scope there with `grep -n "dem = " services/options_svc/compute.py`).

**Step 4: Run.** Expected: PASS. Then the full options_svc suite (failing set vs Task 0b).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_candidate_stamps.py
git commit -m "feat(options-svc): stamp_candidate - ledger risk, friction, expected move, vol floor" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 11: Apply the stamps at the three publish points

**Files:**
- Modify: `services/options_svc/handlers.py` — `rescan` (before the `ScanResult` projection), `swing_scan` (the existing `for sig in signals: sig["earnings_status"] = status` loop), `publish_income` (before `IncomeScan(`)
- Test: `services/options_svc/tests/test_candidate_stamps_published.py`

**Step 1: Write the failing tests**

```python
"""The published views carry the stamps."""
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.options_svc import handlers


def _scan_result():
    row = {"id": "a", "symbol": "ORCL", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0,
           "long_strike": 97.5, "width": 2.5, "credit": 0.60, "max_loss": 1.90,
           "spread_bid": 0.55, "spread_ask": 0.65}
    return {"signals_0dte": [], "signals_swing": [row],
            "signals_directional": [{"id": "d", "symbol": "ORCL", "type": "LONG_CALL",
                                     "dte": 2, "net_debit": 300.0, "legs": []}],
            "iv_data": {"ORCL": {"iv_rank": None}}, "timestamp": "t", "errors": [],
            "warnings": []}


def test_rescan_stamps_every_list_before_publishing(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", _scan_result)
    monkeypatch.setattr(handlers.compute, "scan_earnings", lambda s: ("upcoming", "2026-10-09"))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    p = bus.cache_get(handlers.CACHE_SCAN).payload
    swing = p["signals_swing"][0]
    assert swing["ledger_risk_per_contract"] == 190.0
    assert swing["earnings_date"] == "2026-10-09"
    assert swing["iv_rank_known"] is False
    directional = p["signals_directional"][0]
    assert "vol_floor" in directional          # windowed by DTE: 0-DTE floor


def test_a_stamp_failure_never_costs_the_scan(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "run_scan", _scan_result)
    monkeypatch.setattr(handlers.compute, "stamp_candidate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(handlers.push_notify, "notify_signals", lambda *a, **k: None)
    handlers.rescan(bus)
    assert bus.cache_get(handlers.CACHE_SCAN).payload["signals_swing"]
```

**Step 2: Run to verify they fail.**

**Step 3: Implement.** In `handlers.py` add:

```python
def _stamp_scan(result) -> None:
    """Stamp every candidate list of a raw scan result (design 2026-09-15).

    Before the ScanResult projection, which drops ``iv_data``. Earnings are read
    ONCE per symbol. A Directional row's floor is its WINDOW's (DTE 0-4 is the
    0-DTE bucket, the scanner's own rule). Best-effort: a stamp failure is a
    degrade, never a lost scan."""
    earnings = {}
    iv_data = result.get("iv_data") or {}
    try:
        for key, trade_type in (("signals_0dte", "0-DTE"), ("signals_swing", "SWING"),
                                ("signals_directional", None)):
            for row in result.get(key) or []:
                sym = row.get("symbol")
                if sym not in earnings:
                    try:
                        earnings[sym] = compute.scan_earnings(sym)
                    except Exception:  # noqa: BLE001
                        earnings[sym] = ("not_listed", None)
                tt = trade_type or ("0-DTE" if (row.get("dte") or 0) <= 4 else "SWING")
                rank = (iv_data.get(sym) or {}).get("iv_rank")
                compute.stamp_candidate(row, trade_type=tt, earnings=earnings[sym],
                                        iv_rank_known=rank is not None)
    except Exception:  # noqa: BLE001
        _degrade.degraded("options.stamp_scan")
```

Call `_stamp_scan(result)` in `rescan` right after `result = compute.run_scan()`. In `swing_scan`, inside the existing loop: `compute.stamp_candidate(sig, trade_type="SWING", earnings=(status, earnings_date), iv_rank_known=sig.get("iv_rank") is not None)` wrapped in one try/`_degrade.degraded("options.stamp_swing")` around the whole loop. In `publish_income`, loop the `candidates` the same way with `trade_type="INCOME"` (and the row's existing `earnings_status` left untouched — pass `earnings=None`).

Confirm `_degrade` and `push_notify` are the names imported in `handlers.py` (`grep -n "^from\|^import" services/options_svc/handlers.py`).

**Step 4: Run** the new test plus `test_handlers.py`, `test_income_capture.py` and the swing handler tests (`grep -ln "swing_scan" services/options_svc/tests/*.py`). Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/tests/test_candidate_stamps_published.py
git commit -m "feat(options-svc): stamp scan, Finder and Income candidates at publish" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 12: Publish `cache:options:ledger_caps`

> **Revised after code review:** the view publishes the WHOLE sector table (`sectors`, from `config/sectors.toml`) and `unmapped_prefix`, not a watchlist-derived `sector_of` map, and the bucket rule lives once in `shared.book_caps.sector_bucket(table, symbol)`, which `shared.sectors.group_key` delegates to. A Strategy Finder symbol outside the watchlist would otherwise have shown its sector rungs as unchecked while the service enforced them. A lock serialises the read-and-write, and the refresh runs in a `finally`. The committed code and tests are authoritative.

**Files:**
- Modify: `services/options_svc/handlers.py` (new `refresh_ledger_caps`; call it at the end of `refresh_paper_trades`)
- Modify: `services/options_svc/app.py` — find the startup warm-up block (`grep -n "refresh_paper" services/options_svc/app.py services/options_svc/scheduler.py`) and call `handlers.refresh_ledger_caps(bus)` there once
- Test: `services/options_svc/tests/test_ledger_caps_view.py`

**Step 1: Write the failing test**

```python
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from services.options_svc import handlers


def test_the_view_carries_limits_equity_open_trades_and_a_sector_map(monkeypatch):
    reset_fake_bus()
    bus = Bus(fake=True)
    monkeypatch.setattr(handlers.compute, "ledger_book_state", lambda: {
        "open": [{"symbol": "ORCL", "expiration": "2026-10-17",
                  "max_loss_total": 190.0, "sector": "Information Technology"}],
        "starting_balance": 25000.0, "realized_pnl": 10.0, "equity": 25010.0,
        "limits": {"max_risk_per_trade": 250.0}})
    monkeypatch.setattr(handlers, "_scan_universe", lambda: ["ORCL", "XOM"])
    handlers.refresh_ledger_caps(bus)
    p = bus.cache_get(handlers.CACHE_LEDGER_CAPS).payload
    assert p["equity"] == 25010.0 and p["limits"]["max_risk_per_trade"] == 250.0
    assert p["open"][0]["sector"] == "Information Technology"
    assert p["sector_of"]["XOM"] == "Energy"
    assert p["sector_of"]["ORCL"] == "Information Technology"


def test_a_failure_is_a_degrade_not_a_raise(monkeypatch):
    reset_fake_bus()
    monkeypatch.setattr(handlers.compute, "ledger_book_state",
                        lambda: (_ for _ in ()).throw(RuntimeError("db")))
    handlers.refresh_ledger_caps(Bus(fake=True))     # must not raise
```


**Step 2: Run to verify it fails.**

**Step 3: Implement**

```python
CACHE_LEDGER_CAPS = "cache:options:ledger_caps"
EVENT_LEDGER_CAPS = "events:options:ledger_caps"


def _scan_universe():
    """Every symbol a candidate can carry: the scanner watchlist plus the index
    base the scans add. Read defensively."""
    try:
        import watchlist
        return list(watchlist.get_scan_symbols())
    except Exception:  # noqa: BLE001
        return []


def refresh_ledger_caps(bus) -> None:
    """Publish the Paper Ledger's book for the Paper dialog preview (design
    2026-09-15, Part 1). Republished whenever the Ledger refreshes. A symbol
    missing from ``sector_of`` makes the page's sector rungs read 'unknown';
    the service still enforces on the click."""
    try:
        from shared import sectors as _sectors
        state = compute.ledger_book_state()
        symbols = set(_scan_universe()) | {r.get("symbol") for r in state["open"]}
        state["sector_of"] = {s: _sectors.group_key(s) for s in symbols if s}
        bus.cache_set(CACHE_LEDGER_CAPS, state, skip_unchanged=True)
        bus.publish(EVENT_LEDGER_CAPS, {"version": bus.cache_version(CACHE_LEDGER_CAPS)})
    except Exception:  # noqa: BLE001
        _degrade.degraded("options.ledger_caps")
```

Confirm `watchlist.get_scan_symbols` is the scanner's symbol source (`grep -n "def get_scan_symbols" options-scanner/watchlist.py`) and `Bus.cache_version` exists (`grep -n "def cache_version" shared/bus/client.py`); if `cache_set` returns the version even when skipping, use its return value instead.

Add `refresh_ledger_caps(bus)` as the last line of `refresh_paper_trades`. ⚠ Many handler tests call `refresh_paper_trades` with `paper_trades_view` stubbed; the real `ledger_book_state` will then hit the repo-root live-DB guard and raise inside the `try` — that is a degrade, not a failure, and those tests must keep passing unchanged.

**Step 4: Run** the new test and `test_handlers.py`. Expected: PASS.

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/app.py services/options_svc/tests/test_ledger_caps_view.py
git commit -m "feat(options-svc): publish the Ledger's book for the Paper dialog preview" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 13: Tier-1 `book_fit` — the preview's pure logic

> **Revised after review (commits 0089bf6, a3fa0ae):** the preview reads the stamp `ledger_risk_basis` (`{"per_share": x}` or `{"per_contract": y}`), not the cent-rounded `ledger_risk_per_contract`, and computes each quantity's risk with `shared.book_caps.booked_risk(basis, qty)` — the SAME rounding `paper_trader` now books `max_loss_total` through (proven byte-identical over 80,000 bookings). A cent-rounded per-contract figure had let a sub-cent risk preview green at four contracts while the Ledger refused $750.02. It mirrors the service's quantity rule and its suggested-quantity step-down, and `services/options_svc/tests/test_preview_agrees_with_ledger.py` proves preview and Ledger agree line for line over real books, stamps and enforcement. The committed code is authoritative.

**Files:**
- Create: `webgui/pages/options/book_fit.py`
- Test: `webgui/tests/test_book_fit.py`

**Step 1: Write the failing tests**

```python
from pages.options import book_fit

CAPS = {"limits": {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
                   "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
                   "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
                   "max_risk_per_trade": 250.0},
        "equity": 25000.0,
        "open": [{"symbol": "ORCL", "expiration": "2026-10-17",
                  "max_loss_total": 410.0, "sector": "Information Technology"}] ,
        "sectors": {"ORCL": "Information Technology"}, "unmapped_prefix": "?"}

SIG = {"symbol": "ORCL", "type": "PCS", "expiration": "2026-10-17",
       "ledger_risk_per_contract": 182.0}


def test_preview_for_one_contract():
    p = book_fit.preview(SIG, CAPS, qty=1)
    assert p["available"] and p["max_quantity"] == 1 and p["breach"] is None
    assert p["lines"][0]["label"] == "Per trade"
    assert p["lines"][0]["tone"] == "pos"


def test_preview_blocks_at_two_contracts_and_names_why():
    p = book_fit.preview(SIG, CAPS, qty=2)
    assert p["breach"]["code"] == "TRADE_RISK_CAP"
    assert p["block_text"] == "Risks $364, over the $250 per-trade limit"
    assert p["lines"][0]["tone"] == "neg"


def test_no_stamp_means_no_preview_never_a_green():
    p = book_fit.preview({**SIG, "ledger_risk_per_contract": None}, CAPS, qty=1)
    assert p["available"] is False
    assert p["unavailable_text"] == ("Can't preview this trade here — the paper "
                                     "ledger still checks every cap when you create it.")


def test_no_caps_view_means_no_preview():
    assert book_fit.preview(SIG, None, qty=1)["available"] is False


def test_a_symbol_outside_the_table_is_capped_in_its_own_group():
    """Exactly the service's rule (book_caps.sector_bucket): an unmapped name is
    its own bucket, so its sector rungs are evaluated, never skipped."""
    p = book_fit.preview({**SIG, "symbol": "ZZZZ"}, CAPS, qty=1)
    sector = [l for l in p["lines"] if l["code"] == "SECTOR_POSITION_CAP"][0]
    assert sector["tone"] == "pos"
    assert sector["text"] == "Position 1 of 5 in ZZZZ's own group (no sector on file)"


def test_no_sector_table_means_no_preview():
    """Without the table the page cannot compute the bucket the service will."""
    caps = {k: v for k, v in CAPS.items() if k != "sectors"}
    assert book_fit.preview(SIG, caps, qty=1)["available"] is False


def test_short_reason_for_the_checklist_chip():
    assert book_fit.short_reason({"code": "SECTOR_POSITION_CAP"}) == "sector full"
    assert book_fit.short_reason({"code": "TRADE_RISK_CAP", "cap": 750.0}) == "over $750 per trade"
    assert book_fit.short_reason({"code": "TRADE_RISK_CAP", "cap": 250.0}) == "over $250 per trade"
```

**Step 2: Run to verify they fail.**

**Step 3: Implement**

```python
"""The Paper dialog's book-fit preview - PURE (no nicegui, no bus).

Evaluates the SAME rungs the service enforces, through ``shared.book_caps``
(on the Tier-1 allow-list since 2026-09-15). The service re-checks on every
click, because the book can change between opening the dialog and pressing
Create, so this is a preview and never the decision.
"""
from shared import book_caps

from ..fmt import num

UNAVAILABLE = ("Can't preview this trade here — the paper ledger still checks "
               "every cap when you create it.")

_SHORT = {book_caps.TRADE_RISK_CAP: "over the per-trade limit",
          book_caps.DEPLOYMENT_CAP: "book fully deployed",
          book_caps.SYMBOL_POSITION_CAP: "symbol full",
          book_caps.SYMBOL_RISK_CAP: "symbol risk full",
          book_caps.SECTOR_POSITION_CAP: "sector full",
          book_caps.SECTOR_RISK_CAP: "sector risk full",
          book_caps.EXPIRY_POSITION_CAP: "expiry full"}

_LABELS = {book_caps.TRADE_RISK_CAP: "Per trade",
           book_caps.DEPLOYMENT_CAP: "Deployment",
           book_caps.SYMBOL_POSITION_CAP: "Symbol",
           book_caps.SYMBOL_RISK_CAP: "Symbol risk",
           book_caps.SECTOR_POSITION_CAP: "Sector",
           book_caps.SECTOR_RISK_CAP: "Sector risk",
           book_caps.EXPIRY_POSITION_CAP: "Expiry"}


def short_reason(rung):
    """A few words for the checklist chip. The per-trade wording names the
    rung's own cap, never a hard-coded figure: the Ledger's limit is config."""
    rung = rung or {}
    cap = num(rung.get("cap"))
    if rung.get("code") == book_caps.TRADE_RISK_CAP and cap is not None:
        return f"over ${cap:,.0f} per trade"
    return _SHORT.get(rung.get("code"), "blocked")


def preview(signal, caps, qty=1):
    """``{available, lines, breach, block_text, max_quantity, unavailable_text}``."""
    sig = signal or {}
    per = num(sig.get("ledger_risk_per_contract"))
    if (not isinstance(caps, dict) or not caps.get("limits")
            or not isinstance(caps.get("sectors"), dict) or per is None or per <= 0):
        return {"available": False, "lines": [], "breach": None, "block_text": "",
                "max_quantity": None, "unavailable_text": UNAVAILABLE}
    symbol = (sig.get("symbol") or "").strip().upper()
    candidate = {"symbol": symbol, "expiration": sig.get("expiration"),
                 "sector": book_caps.sector_bucket(caps.get("sectors"), symbol)}
    book, limits, equity = caps.get("open") or [], caps["limits"], caps.get("equity")
    q = max(1, int(qty or 1))
    rungs = book_caps.evaluate(book, candidate, per * q, limits, equity)
    breach = book_caps.first_breach(rungs, book_caps.DISPLAY_ORDER)
    lines = [{"code": r["code"], "label": _LABELS[r["code"]],
              "text": book_caps.describe(r),
              "tone": "muted" if r["skipped"] else ("neg" if r["binds"] else "pos")}
             for r in rungs]
    return {"available": True, "lines": lines, "breach": breach,
            "block_text": book_caps.describe(breach) if breach else "",
            "max_quantity": book_caps.max_quantity(book, candidate, per, limits, equity),
            "unavailable_text": ""}
```

Confirm `pages/fmt.num` is importable as `from ..fmt import num` from `pages/options/` (ev.py does exactly this).

**Step 4: Run.** Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/book_fit.py webgui/tests/test_book_fit.py
git commit -m "feat(webgui): book_fit - the Paper preview over shared.book_caps" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 14: The Paper dialog shows the preview

**Files:**
- Modify: `webgui/pages/options/handoff.py` (`send_to_paper`)
- Test: `webgui/tests/test_options_handoff.py` (append a source-level guard) and a render smoke

**Step 1: Write the failing tests** (append to `test_options_handoff.py`)

```python
def test_the_paper_dialog_reads_the_ledger_caps_view_and_the_preview():
    import inspect
    from pages.options import handoff
    src = inspect.getsource(handoff.send_to_paper)
    assert "options:ledger_caps" in src
    assert "book_fit.preview" in src
    assert "max_quantity" in src
```

**Step 2: Run to verify it fails.**

**Step 3: Implement.** Replace `send_to_paper` with:

```python
def send_to_paper(signal):
    if not signal:
        ui.notify("Select a signal first.", type="warning")
        return
    from . import book_fit
    from .theme import MUTED, TXT_NEG, TXT_POS

    caps = bus_client.read("options:ledger_caps")
    tone_class = {"pos": TXT_POS, "neg": TXT_NEG, "muted": MUTED}

    with ui.dialog() as dlg, ui.card().classes("min-w-[340px]"):
        ui.label(f"Paper trade {signal.get('symbol')} {signal.get('type')} "
                 f"{signal.get('expiration', '')}").classes("text-subtitle1")
        risk = book_fit.preview(signal, caps, 1)
        qty = ui.number("Quantity", value=1, min=1,
                        max=max(1, risk["max_quantity"] or 1) if risk["available"] else 100)
        lines_box = ui.column().classes("w-full gap-0")
        block = ui.label("").classes(f"text-sm {TXT_NEG}")

        def _paint():
            p = book_fit.preview(signal, caps, int(qty.value or 1))
            lines_box.clear()
            with lines_box:
                if not p["available"]:
                    ui.label(p["unavailable_text"]).classes(f"text-xs {MUTED}")
                for line in p["lines"]:
                    with ui.row().classes("w-full justify-between gap-3 no-wrap"):
                        ui.label(line["label"]).classes(f"text-xs {MUTED}")
                        ui.label(line["text"]).classes(
                            f"text-xs text-right {tone_class[line['tone']]}")
            block.text = p["block_text"]
            block.set_visibility(bool(p["breach"]))
            if p["breach"]:
                create.disable()
            else:
                create.enable()

        def confirm():
            bus_client.request("options", {
                "type": "paper_create",
                "args": {"signal": signal, "qty": int(qty.value or 1)},
            })
            ui.notify("Sent — the paper ledger answers in a moment.", type="info")
            dlg.close()

        with ui.row():
            create = ui.button("Create", color=None, on_click=confirm) \
                .props("no-caps").classes(BTN_3D)
            ui.button("Cancel", on_click=dlg.close).props("flat")
        qty.on_value_change(lambda _e: _paint())
        _paint()
    dlg.open()
```

**Step 4: Run** `test_options_handoff.py`, `test_no_inline_style.py`, `test_book_fit.py`. Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/handoff.py webgui/tests/test_options_handoff.py
git commit -m "feat(webgui): the Paper dialog previews every cap and caps the quantity" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 15: Verify Phases 2–3 in the local page harness

No repo files. Follow memory note *local-page-harness-replaces-missing-dev*: a scratchpad NiceGUI app with `bus_client._bus = Bus(fake=True)`, the REAL `services.options_svc.handlers`, a daemon thread consuming `cmd:options`, and a temporary Ledger DB (`trades_db.DEFAULT_DB_PATH` → scratchpad, `trade_tracker_client.track`/`untrack` stubbed).

**Steps:**
1. Seed `cache:options:scan_day` and `cache:options:scan` with two stamped rows (one $190 PCS, one $900 PCS) by calling `handlers._stamp_scan` on a hand-built result, then `bus.cache_set`. Call `handlers.refresh_ledger_caps(bus)`.
2. Render `pages.options.scanner.render()` at `/`; open it in the Browser pane.
3. Click Paper on the $190 row → dialog shows seven lines, Create enabled, quantity max 3. Create → toast "Paper ledger: opened 1 × ORCL Credit spread — put."
4. Click Paper on the $900 row → Per trade line red, Create disabled, text "Risks $900, over the $750 per-trade limit".
5. Open two more $190 trades on the same symbol, then a fourth → toast "Paper ledger: not opened — ORCL already holds 3 of 3 positions."
6. Screenshot the refused dialog and the toast; report both.

Expected: all of the above. Any mismatch is a bug to fix before Phase 4.

### Task 16: Phase 3 docs

- `docs/webgui-routes.md`: `/options/scanner` and `/options/swing` — the Paper dialog preview; new views `options:paper_create`, `options:ledger_caps`.
- `docs/manuals/` User Guide: the preview lines and the capped quantity box.
- `docs/CHANGELOG.md`: entry.
- Commit: `docs: the Paper dialog's book-fit preview` (trailer as always).

---

## Phase 4 — The checklist

### Task 17: `checks.py` — every check as a pure function

**Files:**
- Create: `webgui/pages/options/checks.py`
- Test: `webgui/tests/test_checks.py`

**Step 1: Write the failing tests**

```python
"""The Go / No-Go checklist - PURE."""
import pytest

from pages.options import checks

LIMITS = {"max_positions_per_symbol": 3, "max_risk_per_symbol": 750.0,
          "max_positions_per_expiry": 5, "max_positions_per_sector": 5,
          "max_risk_per_sector": 1500.0, "max_deployed_risk_pct": 0.2,
          "max_risk_per_trade": 250.0}
CAPS = {"limits": LIMITS, "equity": 25000.0, "open": [], "sectors": {"ORCL": "IT"},
        "unmapped_prefix": "?"}


def _pcs(**over):
    row = {"id": "a", "symbol": "ORCL", "type": "PCS", "trade_type": "SWING",
           "expiration": "2026-10-17", "dte": 12, "short_strike": 100.0,
           "long_strike": 97.5, "credit": 0.60, "iv_rank": 55.0, "iv_rank_known": True,
           "vol_floor": 30, "friction_pct": 8.0, "em_to_expiry": 6.0,
           "earnings_status": "none_scheduled", "earnings_date": None,
           "ledger_risk_per_contract": 190.0, "_allow_paper": True,
           "underlying_price": 110.0}
    row.update(over)
    return row


MATRIX = {"spot": 110.0, "put_wall": 102.0, "call_wall": 120.0, "gex_regime": "above",
          "trend_dir": 0.4, "trend_state": "up"}
REGIME = {"direction": 1}


def _tones(row, matrix=MATRIX, regime=REGIME, calibration=None, caps=CAPS):
    return {c["key"]: c["tone"] for c in
            checks.build_checks(row, matrix, regime, calibration or {}, caps)}


def test_a_clean_short_put_spread_is_all_green():
    tones = _tones(_pcs())
    # With no calibration published the track record is honestly grey.
    assert tones.pop("record") == "muted"
    assert set(tones.values()) == {"pos"}
    assert set(tones) >= {"book", "earnings", "vol", "cost", "em", "wall", "gamma", "direction"}


def test_book_fit_is_the_only_red():
    tones = _tones(_pcs(ledger_risk_per_contract=425.0))
    assert tones["book"] == "neg"
    assert all(t != "neg" for k, t in tones.items() if k != "book")


def test_earnings_before_expiry_is_amber_with_the_date():
    cs = checks.build_checks(_pcs(earnings_status="upcoming", earnings_date="2026-10-09"),
                             MATRIX, REGIME, {}, CAPS)
    e = [c for c in cs if c["key"] == "earnings"][0]
    assert e["tone"] == "warn" and "Oct 9" in e["text"]


def test_earnings_after_expiry_is_green():
    assert _tones(_pcs(earnings_status="upcoming", earnings_date="2026-11-20"))["earnings"] == "pos"


def test_no_earnings_coverage_is_grey():
    assert _tones(_pcs(earnings_status="not_listed"))["earnings"] == "muted"


@pytest.mark.parametrize("rank,tone", [(40.0, "pos"), (39.9, "warn"), (30.0, "warn")])
def test_vol_rank_margin_over_the_floor(rank, tone):
    assert _tones(_pcs(iv_rank=rank))["vol"] == tone


def test_vol_rank_is_omitted_for_long_premium():
    row = _pcs(type="LONG_CALL", legs=[{"side": "long", "kind": "call", "strike": 110}],
               net_vega=0.5)
    assert "vol" not in _tones(row)


def test_unknown_iv_rank_is_grey_not_a_zero():
    assert _tones(_pcs(iv_rank=0, iv_rank_known=False))["vol"] == "muted"


@pytest.mark.parametrize("friction,tone,word", [(10.0, "pos", None), (10.1, "warn", None),
                                                (26.0, "warn", "very wide")])
def test_cost_to_trade(friction, tone, word):
    c = [c for c in checks.build_checks(_pcs(friction_pct=friction), MATRIX, REGIME, {}, CAPS)
         if c["key"] == "cost"][0]
    assert c["tone"] == tone
    if word:
        assert word in c["text"]


def test_short_strike_inside_one_expected_move_is_amber():
    # spot 110, short put 105, em 6 -> 0.83 EM
    assert _tones(_pcs(short_strike=105.0))["em"] == "warn"


def test_expected_move_uses_the_live_price_from_the_board():
    # the scan's price says 1.67 EM; the live board price says 0.5 EM
    assert _tones(_pcs(), matrix={**MATRIX, "spot": 103.0})["em"] == "warn"


def test_put_spread_inside_the_put_wall_is_amber():
    assert _tones(_pcs(short_strike=104.0))["wall"] == "warn"


def test_iron_condor_checks_both_walls():
    row = _pcs(type="IC", short_strike=101.0, call_short=121.0, long_strike=99.0,
               call_long=123.0)
    assert _tones(row)["wall"] == "pos"
    assert _tones({**row, "call_short": 118.0})["wall"] == "warn"


def test_below_the_flip_is_amber():
    assert _tones(_pcs(), matrix={**MATRIX, "gex_regime": "below"})["gamma"] == "warn"


def test_direction_opposing_the_market_is_amber_for_a_swing_hold():
    assert _tones(_pcs(), regime={"direction": -1})["direction"] == "warn"


def test_zero_dte_direction_reads_the_symbols_intraday_trend():
    row = _pcs(trade_type="0-DTE", dte=0)
    assert _tones(row, matrix={**MATRIX, "trend_dir": -0.5, "trend_state": "down"},
                  regime={"direction": 1})["direction"] == "warn"


def test_no_direction_is_grey():
    assert _tones(_pcs(), regime={"direction": 0})["direction"] == "muted"


def test_track_record_is_omitted_on_a_finder_row():
    assert "record" not in _tones(_pcs(fit_score=70.0, composite_score=72.0))


def test_a_row_the_ledger_cannot_open_has_no_book_line():
    assert "book" not in _tones(_pcs(_allow_paper=False))


def test_summary_chip():
    clean = checks.build_checks(_pcs(), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(clean)["text"].startswith("Clear · ")
    cautioned = checks.build_checks(_pcs(short_strike=104.0), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(cautioned)["state"] == "warn"
    blocked = checks.build_checks(_pcs(ledger_risk_per_contract=425.0), MATRIX, REGIME, {}, CAPS)
    assert checks.summary(blocked)["text"] == "Blocked · over $250 per trade"


def test_summary_with_no_context_is_unchecked():
    assert checks.summary(checks.build_checks(_pcs(), None, None, None, None))["state"] in ("warn", "pos", "muted")
    assert checks.summary([])["text"] == "unchecked"
```

**Step 2: Run to verify they fail.**

**Step 3: Implement `checks.py`.** Required public surface and rules (write the code so the tests above pass; keep each check a small private function returning a dict or None):

```python
"""The Go / No-Go checklist for one candidate - PURE (no nicegui, no bus).

Design 2026-09-15, Part 2. Each check is ``{"key", "label", "tone", "text"}``
with tone ``pos`` / ``warn`` / ``neg`` / ``muted``. A check that does not apply
is OMITTED (never shown as passing); only ``book`` can be ``neg``, because every
other hard gate already removed its failures upstream.
"""
import datetime as _dt

from ..fmt import num
from . import book_fit, ev

VOL_MARGIN = 10.0          # points above the floor before Vol rank reads green
FRICTION_OK_PCT = 10.0     # round-trip bid-ask as % of credit/debit
FRICTION_WIDE_PCT = 25.0   # above this the words say "very wide" (still amber)
EM_OK = 1.0                # short strike at least this many expected moves away
ZERO_DTE_MAX = 4           # the 0-DTE bucket spans DTE 0..4

TONE_CLASS = {"pos": "text-emerald-400", "warn": "text-amber-400",
              "neg": "text-rose-400", "muted": "text-[#7f8db0]"}
```

Rules to implement (each matches a test):
- `_short_legs(row)` → list of `(right, strike)`: rows without `legs` use `type` (PCS → put `short_strike`; CCS → call `short_strike`; IC → put `short_strike` + call `call_short`); normalized rows use legs with `side == "short"` and `kind in ("put","call")`.
- `_is_short_premium(row)` → `not row.get("legs")` (the scanner's credit lists) or `num(row.get("net_vega")) < 0`. Omit `vol`, `em`, `wall`, `gamma` when False.
- `_bias(row)` → `row["bias"]` if present, else PCS bullish / CCS bearish / IC neutral. Omit `direction` for neutral.
- `book`: omitted when `_allow_paper` is falsy; `muted` when `book_fit.preview(...)["available"]` is False; `neg` with `book_caps.describe(breach)` on a breach; else `pos`, "Fits the paper book — up to N contracts".
- `earnings`: `upcoming` with a date on or before `expiration` → `warn`, "Earnings Oct 9, before expiry"; after → `pos`; `none_scheduled` → `pos`, "No report scheduled"; anything else → `muted`, "No earnings coverage".
- `vol`: floor missing/0 → omitted; `iv_rank_known is False` → `muted`, "No IV history"; rank ≥ floor + VOL_MARGIN → `pos`; else `warn`. Text: "Vol rank 38 · floor 30".
- `cost`: `friction_pct` None → `muted`; ≤ 10 → `pos`; else `warn`, with "very wide" in the text above 25.
- `em`: spot = matrix `spot` else row `underlying_price` (text notes "scan price" then); distance per short leg = (spot − strike)/em for a put, (strike − spot)/em for a call; the minimum decides: ≥ 1 → `pos`; else `warn`. Missing em or spot → `muted`.
- `wall`: short put strike < `put_wall` and short call strike > `call_wall` → `pos`; any short inside its wall → `warn`; a wall missing → `muted`.
- `gamma`: `gex_regime` "above" → `pos`, "below" → `warn`, else `muted`.
- `direction`: if `trade_type == "0-DTE"` or `dte <= 4`, use the sign of matrix `trend_dir` (0 when `trend_state == "flat"`), labelled "today's move"; otherwise regime `direction`, labelled "market direction". Sign 0 → `muted`; agrees with bias → `pos`; opposes → `warn`.
- `record`: omitted when the row has `fit_score`; else `ev.calibrated_facts(row, calibration)` → None → `muted`, "Not enough history for this score"; `ev_r > 0` → `pos`; else `warn`; text is its `text`.
- `summary(checks)` → `{"state", "text", "class"}`: empty list → `muted`/"unchecked"; any `neg` → "Blocked · " + `book_fit.short_reason(breach)` (carry the breach rung on the book check as `check["breach"]`); any `warn` → "N caution(s)"; else "Clear · K checked" where K counts non-muted checks, and when some are muted, "Clear · K of N checked".
- `build_checks(row, matrix_row, regime, calibration, caps, qty=1)` returns the checks in this order: book, earnings, vol, cost, em, wall, gamma, direction, record. Every input may be None.

**Step 4: Run.** Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/options/checks.py webgui/tests/test_checks.py
git commit -m "feat(webgui): checks - the Go / No-Go checklist as pure functions" -m "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

### Task 18: Guard the calibration lookup against the Finder's score scale

**Files:**
- Modify: `webgui/pages/options/ev.py` (`calibrated_facts`)
- Test: `webgui/tests/test_ev.py` (append)

**Step 1: Failing test**

```python
def test_a_finder_row_never_reads_a_scanner_calibration_bucket():
    """strategy_scoring overwrites composite_score with Fit+Quality while the row
    keeps trade_type SWING; a bucket built on the scanner's score must not answer."""
    from pages.options import ev
    cal = {"buckets": {"SWING|70-75": {"speaks": True, "ev_r": 0.8, "n": 30, "days": 12}}}
    row = {"trade_type": "SWING", "composite_score": 72.0, "fit_score": 70.0}
    assert ev.calibrated_facts(row, cal) is None
    assert ev.calibrated_facts({k: v for k, v in row.items() if k != "fit_score"}, cal)
```

(`shared.calibration.bucket_key` spells a bucket `"SWING|70-75"`.)

**Step 2: Run to verify the first assert fails.**

**Step 3:** At the top of `calibrated_facts`, after the argument type check:

```python
    # A row scored by strategy_scoring carries fit_score, and its composite_score
    # is that model's Fit+Quality, not the scanner composite the calibration
    # buckets are built on (strategy_scoring.py writes it). Same trade_type,
    # different scale: answering would print another model's history.
    if s.get("fit_score") is not None:
        return None
```

**Step 4: Run** `test_ev.py`. Expected: PASS.

**Step 5: Commit** — `fix(webgui): Finder rows no longer read the scanner's calibration buckets`.

### Task 19: `checks_feed` — read the live context once

**Files:**
- Create: `webgui/pages/options/checks_feed.py`
- Test: `webgui/tests/test_checks_feed.py`

**Step 1: Failing test**

```python
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

import bus_client
from pages.options import checks_feed


def _fresh(monkeypatch):
    """Module-level read_gated memos outlive a test; a fresh bus needs fresh memos."""
    reset_fake_bus()
    for memo in checks_feed._memos.values():
        memo.clear()


def test_context_indexes_the_board_by_symbol_and_reads_the_other_views(monkeypatch):
    _fresh(monkeypatch)
    bus = Bus(fake=True)
    monkeypatch.setattr(bus_client, "_bus", bus)
    bus.cache_set("cache:options:matrix", {"rows": [{"symbol": "ORCL", "spot": 110.0}]})
    bus.cache_set("cache:sentiment:regime", {"direction": 1})
    bus.cache_set("cache:options:ledger_caps", {"limits": {}})
    ctx = checks_feed.read_context()
    assert ctx["matrix"]["ORCL"]["spot"] == 110.0
    assert ctx["regime"]["direction"] == 1
    assert ctx["caps"] == {"limits": {}}
    assert ctx["calibration"] == {}


def test_a_cold_bus_yields_empty_context_not_a_raise(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(bus_client, "_bus", Bus(fake=True))
    ctx = checks_feed.read_context()
    assert ctx == {"matrix": {}, "regime": None, "calibration": {}, "caps": None}


def test_versions_lists_the_views_that_should_refresh_the_column():
    assert set(checks_feed.REFRESH_VIEWS) == {"options:ledger_caps", "sentiment:regime"}
```

(`bus_client._bus` is the module-level client `bus()` returns; patching it is how the harness injects a fake bus too.)

**Step 3: Implement**

```python
"""Live context for the checklist, read with version-gated memos.

Separate from ``checks`` so that module stays pure. The Opportunity Board view
updates every minute; the tables re-stamp on the REFRESH_VIEWS changing or a
5-minute timer (operator decision), while the detail panel and the Paper dialog
call ``read_context`` on every open.
"""
import bus_client

MATRIX_VIEW = "options:matrix"
REGIME_VIEW = "sentiment:regime"
CALIBRATION_VIEW = "options:calibration"
CAPS_VIEW = "options:ledger_caps"
REFRESH_VIEWS = (CAPS_VIEW, REGIME_VIEW)
TABLE_REFRESH_SEC = 300.0

_memos = {MATRIX_VIEW: {}, REGIME_VIEW: {}, CALIBRATION_VIEW: {}, CAPS_VIEW: {}}


def _gated(view):
    try:
        payload, _changed = bus_client.read_gated(view, _memos[view])
        return payload
    except Exception:  # noqa: BLE001 - a missing view costs its checks, not the page
        return None


def read_context():
    board = _gated(MATRIX_VIEW) or {}
    by_symbol = {(r.get("symbol") or "").upper(): r
                 for r in board.get("rows") or [] if isinstance(r, dict)}
    return {"matrix": by_symbol, "regime": _gated(REGIME_VIEW),
            "calibration": _gated(CALIBRATION_VIEW) or {}, "caps": _gated(CAPS_VIEW)}


def checks_for(row, ctx):
    from . import checks
    ctx = ctx or {}
    sym = (row.get("symbol") or "").upper()
    return checks.build_checks(row, (ctx.get("matrix") or {}).get(sym),
                               ctx.get("regime"), ctx.get("calibration"), ctx.get("caps"))
```

⚠ `read_gated` memos are module-level and shared by every open tab — that is intentional (the calibration memo in `detail.py` does the same).

**Step 4/5:** Run, then commit `feat(webgui): checks_feed - the checklist's live context`.

### Task 20: Checks column and "Only clear" on the Market Scanner

**Files:**
- Modify: `webgui/pages/options/scanner.py`
- Test: `webgui/tests/test_options_page.py` (append)

**Step 1: Failing tests**

```python
def test_signal_columns_carry_a_checks_column_before_actions():
    names = [c["name"] for c in options.signal_columns()]
    assert names.index("checks") == names.index("actions") - 1


def test_stamp_checks_joins_by_id_and_filters_only_clear():
    rows = [{"id": "a"}, {"id": "b"}]
    sigs = [{"id": "a", "symbol": "X"}, {"id": "b", "symbol": "Y"}]
    fake = {"a": [{"key": "book", "tone": "pos"}],
            "b": [{"key": "wall", "tone": "warn"}]}
    options.stamp_checks(rows, sigs, ctx={}, build=lambda s, ctx: fake[s["id"]])
    assert rows[0]["checks"].startswith("Clear") and rows[1]["_checks_state"] == "warn"
    assert [r["id"] for r in options.only_clear(rows)] == ["a"]
```

**Step 3: Implement.**
- `signal_columns()`: insert `("checks", "Checks")` before `_DROPPED_COL`; `directional_columns()`: insert `_col("checks", "Checks")` before the dropped column.
- Add, next to `stamp_stale`:

```python
def stamp_checks(rows, signals, ctx, build=None):
    """Stamp the checklist summary onto display rows, joined by id (the builders
    re-sort). ``build`` is injectable for tests; production uses checks_feed."""
    from . import checks, checks_feed
    build = build or checks_feed.checks_for
    by_id = {s.get("id"): s for s in (signals or []) if s.get("id")}
    for r in rows:
        sig = by_id.get(r.get("id"))
        chip = checks.summary(build(sig, ctx) if sig else [])
        r["checks"], r["_checks_state"], r["_checks_class"] = chip["text"], chip["state"], chip["class"]
    return rows


def only_clear(rows):
    return [r for r in rows if r.get("_checks_state") == "pos"]
```

- `_read_all` also returns `checks_feed.read_context()`; `_build_populate(day_env, live, ctx=None)` calls `stamp_checks(rows[key], sigs[key], ctx)` for all three lists (off the event loop). Keep the full rows in `state["rows"]` and assign `table.rows = only_clear(rows) if clear_toggle.value else rows` in `_apply_populate`.
- A `ui.switch("Only clear", value=False)` in the Run-scan row (left of the button); on change, repaint from `state["rows"]` without re-reading.
- `_maybe_repaint` probes `read_versions((_DAY_VIEW, _LIVE_VIEW) + checks_feed.REFRESH_VIEWS)`; add `ui.timer(checks_feed.TABLE_REFRESH_SEC, _force_repaint)` where `_force_repaint` clears `seen` and calls `_maybe_repaint`.
- Slot for the chip (Tailwind classes via the stamped `_checks_class`, never `:style`):

```python
_CHECKS_SLOT = r'''
  <q-td :props="props">
    <span :class="props.row._checks_class + ' text-xs whitespace-nowrap'">{{ props.value || '—' }}</span>
  </q-td>
'''
```

Add it with `add_slot('body-cell-checks', _CHECKS_SLOT)` to all three tables.

**Step 4: Run** `test_options_page.py`, `test_no_inline_style.py`. Expected: PASS.

**Step 5: Commit** — `feat(scanner): Checks column and an Only-clear filter`.

### Task 21: Checks column and "Only clear" on the Strategy Finder

**Files:** `webgui/pages/options/finder_view.py` (`finder_columns`), `webgui/pages/options/swing.py` (`_set_rows`, `_show_page`, `_maybe_repaint`), tests in `webgui/tests/test_finder_view.py` / `test_options_swing.py`.

Same pattern as Task 20:
- Add `("checks", "Checks", "checks", False)` to `finder_columns` before Grade.
- In `swing._set_rows`, stamp each row from its signal with `scanner.stamp_checks(rows, sigs, checks_feed.read_context())`; keep `state["all_rows"]`; `state["rows"] = scanner.only_clear(all_rows) if clear.value else all_rows`.
- Re-stamp (not re-scan) on `checks_feed.REFRESH_VIEWS` version change and on the 300 s timer, then `_show_page(table.pagination)` so the reader's page and sort survive.
- Tests: the column exists; a restamp keeps the current page number. `finder_view` must still not import nicegui (`test_finder_view.py:19`).

Commit — `feat(finder): Checks column and an Only-clear filter`.

### Task 22: The full checklist in the Trade detail panel

**Files:** `webgui/pages/options/detail.py` (`_build_cards`), test in `webgui/tests/test_options_detail*.py` (find with `ls webgui/tests | grep detail`).

- At the top of `_build_cards(s)`, before the contract card:

```python
    # 0 — THE CHECKLIST. What this trade has to clear, live (design 2026-09-15).
    from . import checks, checks_feed
    items = checks_feed.checks_for(s, checks_feed.read_context())
    if items:
        chip = checks.summary(items)
        with ui.column().classes(f"w-full gap-1 {CARD}"):
            ui.label(chip["text"]).classes(f"text-sm font-bold {chip['class']}")
            for c in items:
                with ui.row().classes("w-full no-wrap gap-2 items-start"):
                    ui.label(c["label"]).classes(f"text-xs w-24 shrink-0 {MUTED}")
                    ui.label(c["text"]).classes(f"text-xs {checks.TONE_CLASS[c['tone']]}")
```

- ⚠ The Finder and Directional paths pass `strategy_table.detail_signal(sig)`, a shallow copy — confirm it keeps `fit_score`, `ledger_risk_per_contract` and the other stamps (`dict(signal)` does).
- Test: a pure test that `checks_feed.checks_for` on a stamped row returns the nine keys in order, and a render smoke that `detail.render()` + `update(row)` does not raise with a cold bus.

Commit — `feat(detail): the full checklist at the top of the Trade detail panel`.

### Task 23: Verify Phase 4 in the harness, then docs

1. Extend the Task 15 harness: seed `cache:options:matrix` (rows for the seeded symbols with spot, walls, `gex_regime`, `trend_dir`), `cache:sentiment:regime`, and `cache:options:calibration`.
2. Check on screen: the Checks column chip on each tab; "Only clear" hides amber and red rows; clicking a row shows the nine-line list; changing `ledger_caps` (open a trade) re-stamps within one poll; a Finder row shows no track-record line.
3. Screenshot the Scanner table and the detail panel.
4. Docs: `docs/webgui-routes.md` (Checks column, Only clear, detail checklist), Reference Guide pages for Market Scanner and Strategy Finder, `webgui/page_help.py`, `docs/CHANGELOG.md`; add `checks.py`, `book_fit.py`, `checks_feed.py` to `test_no_inline_style.py`'s helper list. Commit `docs: the Go / No-Go checklist`.

---

## Phase 5 — "Why no trade?"

### Task 24: `select_best_width` reports why a strike found no width

**Files:**
- Modify: `options-scanner/scanner_engine.py:1347-1453`
- Test: `options-scanner/tests/test_scan_funnel.py`

**Step 1: Failing tests**

```python
"""Truthful rejection counts. Output of the width search must NOT change."""
from collections import Counter

import scanner_engine as se
from test_scanner_engine import _opts_from   # the existing helper


def _short_and_opts(marks=None):
    """``_opts_from(short_strike, {strike: mark})`` returns the opts DICT
    screen_spreads builds; the short leg is the entry at the short strike."""
    opts = _opts_from(100.0, marks or {100.0: 1.00, 99.0: 0.30, 98.0: 0.15, 97.0: 0.08})
    return opts[100.0], opts


def test_the_result_is_identical_with_and_without_the_counter():
    short, opts = _short_and_opts()
    plain = se.select_best_width(short, opts, "PCS", 1.0, "SWING", 0.10,
                                 max_risk_dollars=250.0)
    counted = se.select_best_width(short, opts, "PCS", 1.0, "SWING", 0.10,
                                   max_risk_dollars=250.0, reasons=Counter())
    assert plain == counted


def test_a_width_over_the_trade_cap_is_named():
    short, opts = _short_and_opts()
    reasons = Counter()
    assert se.select_best_width(short, opts, "PCS", 1.0, "SWING", 0.10,
                                max_risk_dollars=1.0, reasons=reasons) is None
    assert reasons == Counter({"over_trade_cap": 1})


def test_no_long_strike_in_the_chain_is_named():
    short, _ = _short_and_opts()
    reasons = Counter()
    assert se.select_best_width(short, {100.0: short}, "PCS", 1.0, "SWING", 0.10,
                                reasons=reasons) is None
    assert reasons == Counter({"long_leg_missing": 1})


def test_a_found_width_records_nothing():
    short, opts = _short_and_opts()
    reasons = Counter()
    assert se.select_best_width(short, opts, "PCS", 1.0, "SWING", 0.0,
                                max_risk_dollars=1e6, reasons=reasons) is not None
    assert reasons == Counter()
```

⚠ If `_opts_from`'s real signature differs, adapt `_short_and_opts` — do not change `_opts_from`. If the "found width" fixture yields `None` (the chain's edge floor rejects it), pick marks that pass and say which in a comment; never delete the test.

**Step 3: Implement**

```python
# The stages a width passes in select_best_width, in order. A strike that finds
# no width is attributed to the FURTHEST stage its best width reached - "every
# width cleared the credit floor but one contract costs more than the cap" is
# the sentence a reader can act on; "no width" is not (design 2026-09-15).
WIDTH_STAGES = ("long_leg_missing", "long_leg_unpriced", "long_leg_illiquid",
                "no_credit", "sanity_cap", "credit_floor", "edge_floor",
                "over_trade_cap", "no_contracts", "no_positive_ev")
```

In the loop keep `furthest = -1`; before each `continue` set `furthest = max(furthest, <index of that stage>)` (the `ml <= 0` guard counts as `no_credit`). After the loop:

```python
    if not candidates:
        if reasons is not None:
            reasons[WIDTH_STAGES[max(furthest, 0)]] += 1
        return None
```

Add `reasons=None` to the signature and the docstring.

**Step 4: Run** the new file and `tests/test_scanner_engine.py`. Expected: PASS, and the full options-scanner failing set unchanged.

**Step 5: Commit** — `feat(scanner): the width search names why a strike found no width`.

### Task 25: `screen_spreads` fills a funnel counter

**Files:** `options-scanner/scanner_engine.py:957-1190`; tests appended to `tests/test_scan_funnel.py`.

- Add `funnel=None` to `screen_spreads`. When given (a dict), increment: `expirations_in_window`, `expirations_skipped_earnings`, `delta_reject`, `delta_pass`, `mark_fail`, `delta_ceiling`, `em_fail`, `liq_fail_short`, `width_found`, and pass `funnel.setdefault("width_reasons", Counter())` as `reasons=` to `select_best_width`.
- Leave the explicit-widths branch's own counters alone.
- Change the NO SPREADS log line so the auto-width path prints the width reasons instead of the always-zero `liq_long`/`credit`.
- Tests: over the existing `_chain_at(spot, exp, dte)` fixture, the counts are internally consistent — `delta_pass == mark_fail + delta_ceiling + em_fail + liq_fail_short + width_found + sum(width_reasons.values())` — and `screen_spreads(...)` returns an identical list with and without `funnel=`.

Commit — `feat(scanner): screen_spreads counts every rejection per strike`.

### Task 26: `run_full_scan` collects the per-symbol funnel, and the IV floor names "no IV history"

**Files:** `options-scanner/scanner_engine.py:1530-2080`; tests in `tests/test_scanner_engine.py` (new class `TestScanFunnel`, reusing the `fake_client` fixture).

Implement `results["funnel"] = {}` with, per symbol:

```python
{"price": float|None, "iv_rank": float|None, "earnings_date": str|None,
 "stop": None | "no_quote" | "no_data",
 "buckets": {
   "0DTE":  {"chain": bool, "strikes": {...screen_spreads funnel...},
             "spreads": {"built": n, "momentum_veto": n, "kept_after_cap": n,
                         "regime_pass_added": n, "regime_filter": n,
                         "below_iv_floor": n, "no_iv_history": n,
                         "gamma_gate": n, "emitted": n}},
   "SWING": {...same...},
   "DIRECTIONAL": {"windows_without_candidates": n, "built": n,
                   "vol_gate": n, "score_cut": n, "emitted": n}}}
```

- Removed counts for `regime_filter`, the IV floor and `gamma_gate` are per-symbol diffs of `Counter(s["symbol"] for s in list)` before and after each step.
- Replace the IV floor's `... or 0) >= min_rank` with an explicit two-way split that keeps the SAME set of rows: rank `None` → dropped and counted `no_iv_history`; rank `< min_rank` → dropped and counted `below_iv_floor`. Add a comment naming the operator decision (2026-09-15: keep refusing unknowns).
- Do NOT change the later `s["iv_rank"] = iv_for_sym.get("iv_rank") or 0` line (its comment says other code depends on it).

Tests:
- Signals are byte-identical with the funnel code in place: run `run_full_scan(fake_client, symbols=...)` and compare `json.dumps` of the three lists, with volatile `timestamp` fields removed, against a run with the collection monkeypatched out. If that toggle is awkward, pin a golden count and id list captured BEFORE the change.
- A symbol whose IV analysis yields `iv_rank=None` shows its spreads under `no_iv_history` and emits none.
- Per bucket `emitted` equals the symbol's count in the final lists.
- An unquotable symbol reads `stop == "no_quote"`.

Commit — `feat(scanner): a per-symbol funnel; no IV history is a named refusal`.

### Task 27: `ScanFunnel` contract and `cache:options:scan_funnel`

**Files:** `shared/contracts/options.py`, `shared/contracts/tests/test_options.py`, `services/options_svc/handlers.py` (`rescan`), `services/options_svc/tests/test_scan_funnel_publish.py`.

```python
class ScanFunnel(_Base):
    """cache:options:scan_funnel - why each symbol did or did not produce a
    Market Scanner signal (design 2026-09-15, Part 3). Its OWN view: the
    ScanResult projection would drop it, and every scan reader would pay for
    bytes it never shows. Every field defaults, because Redis persists the view
    across restarts."""
    timestamp: str | None = None
    symbols: dict[str, dict] = {}
```

In `rescan`, after the day-union block, best-effort:

```python
    try:
        funnel = ScanFunnel(timestamp=result.get("timestamp"),
                            symbols=result.get("funnel") or {})
        fver = bus.cache_set(CACHE_SCAN_FUNNEL, funnel.model_dump(), skip_unchanged=True)
        bus.publish(EVENT_SCAN_FUNNEL, {"version": fver})
    except Exception:  # noqa: BLE001
        _degrade.degraded("options.scan_funnel")
```

Tests: round-trip and defaults for the contract; `rescan` with a stubbed `run_scan` publishes the funnel and a malformed `funnel` (a list) degrades without losing the scan. Measure the payload size from a real `run_full_scan(fake_client)` result and write the number into the design doc (replace "Estimated ~50 KB").

Commit — `feat(options-svc): publish the per-symbol scan funnel`.

### Task 28: `funnel_view` — headline and stage lines (Tier 1, pure)

**Files:** `webgui/pages/options/funnel_view.py`, `webgui/tests/test_funnel_view.py`.

Public surface:
- `empty_symbols(payload, bucket)` → symbols whose bucket emitted 0, sorted.
- `bucket_card(entry, bucket)` → `{"headline", "stages": [{"label", "remaining", "binding"}], "note"}`.
  - Strike section remaining counts in order:
    - in delta band (`delta_pass`)
    - priced (minus `mark_fail`)
    - under the delta ceiling (minus `delta_ceiling`)
    - inside the expected-move window (minus `em_fail`)
    - short leg liquid (minus `liq_fail_short`)
    - width found (`width_found`)
  - Spread section: built → after momentum veto → after per-symbol cap → + regime pass → after regime filter → after IV floor → after gamma gate → emitted.
  - `binding` marks the first stage whose remaining count is 0.
  - Headline sentences, one per binding stage, e.g. width found = 0 with top reason `over_trade_cap` → *"MU · Swing: 38 short strikes priced, and every width that cleared the credit and edge floors cost more than the $250 per-trade cap."*
  - Write a sentence for every `WIDTH_STAGES` reason and every spread stage; a test iterates them all so none falls back to a code.
- `stop` values → one sentence each: `no_quote` "Schwab returned no quote for this symbol."; `no_data` "The scan could not load this symbol's price history or chains."
- A funnel whose `timestamp` differs from the live scan's → `note` "From an earlier scan." Never print zeros for a symbol absent from the payload: "This symbol was not in the last scan."

Tests cover each sentence, the binding marker, and the never-zeros rule. Commit — `feat(webgui): funnel_view - why no trade, in words`.

### Task 29: The "Why no trade?" panel on the Market Scanner

**Files:** `webgui/pages/options/scanner.py`; smoke test in `test_options_page.py`.

- A flat button **Why no trade?** left of Run scan opens a `ui.dialog` (`w-[720px] max-w-full`).
- Inside:
  1. read `options:scan_funnel` and `options:scan` (small, on open);
  2. chips per bucket from `funnel_view.empty_symbols`;
  3. a `ui.select` of every symbol in the payload;
  4. three cards (0-DTE, Swing, Directional) from `funnel_view.bucket_card`, stage rows with the binding stage in `TXT_WARN`.
- Tailwind classes only.
- Smoke test: the render with a cold bus does not raise, and the button exists (`find` by text in a `User` fixture if the suite has one; otherwise a source-level assertion that `funnel_view.bucket_card` is called in `scanner.py`).

Commit — `feat(scanner): the Why no trade? panel`.

### Task 30: Verify Phase 5 in the harness, then docs

1. Harness: run the REAL `run_full_scan` with the scanner test suite's `fake_client`-style stub (copy it into the scratchpad), `_stamp_scan`, publish through `handlers.rescan` with `compute.run_scan` monkeypatched to return that result.
2. Open Why no trade?; confirm a symbol with no signals names a binding stage in words; screenshot.
3. Docs:
   - `CLAUDE.md`: correct in place the IV-floor description (unknown IV rank is a named refusal by operator decision, an exception to `vol_gate`'s absence rule) and the reject-counter note;
   - `docs/webgui-routes.md`;
   - the Reference Guide page for the Market Scanner;
   - `webgui/page_help.py`;
   - `docs/CHANGELOG.md`.
4. Add `funnel_view.py` to the inline-style guard.
5. Commit `docs: why no trade`.

---

## Finish

After Task 30: run all four suites and compare failing sets with Task 0b. Then use **superpowers:finishing-a-development-branch**. Promotion to prod is the operator's call (`tools/promote.sh`, suggested window 15:25–16:15 CT). The first live signal for each phase:
- **Phase 2:** a refused Paper click toasts a reason.
- **Phase 4:** the Checks column populates within one scan.
- **Phase 5:** `redis-cli GET cache:options:scan_funnel` is non-empty after the first scan.
