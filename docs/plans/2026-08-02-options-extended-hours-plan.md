# Options Extended Trading Hours Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the app aware of Cboe's extended options sessions — **GTH** 06:30–08:25 CT and **Curb** 15:00–15:15 CT — for ETH-eligible symbols, effective **2026-08-17** (confirmed in Cboe notice [C2026061202](https://www.cboe.com/notices/content/?id=60500)) and inert before that date, while the execution layer stays observe-only.

**Terminology:** use Cboe's names — **GTH** (morning) and **Curb** (afternoon) — not "pre-market"/"after-hours". Cboe distinguishes the option GTH session from the equity pre-market, and the codebase should not blur them.

**Architecture:** Add `shared/market_calendar.py` as the single source of truth for holidays and session windows, backed by a new `config/sessions.toml`. Migrate nine duplicated holiday sets and fourteen hardcoded window constants to it as a **behavior-preserving refactor first** (Phases A–B), then layer ETH behavior on top (Phases C–E). Per-symbol eligibility is harvested from the `ethOptionEligible` field already present on every chain response — no new API calls, no hardcoded symbol list.

**Tech Stack:** Python 3.11 (`tomllib`, `zoneinfo`, `datetime`, `enum`), pytest, Redis/Memurai via `shared.bus`, NiceGUI + Tailwind (webgui display only).

**Design doc:** [2026-08-02-options-extended-hours-design.md](2026-08-02-options-extended-hours-design.md)

---

## Ground rules

- `shared/` has **no `__init__.py`** — it is a namespace package. `from shared.market_calendar import ...` resolves once the repo root is on `sys.path`, which all five services and the webgui already arrange. The one exception is `options-scanner/scanner.py` (legacy standalone CLI), which needs a three-line bootstrap modeled on `services/options_svc/commission.py:9-12`.
- Run service suites **per folder** from the repo root. **Never** `pytest services` across all of them — it puts multiple hyphenated app dirs on `sys.path` at once and re-triggers the documented `config`/`scoring`/`notifier` module-name collisions.
- Phases A and B are **refactors**. Do not change any holiday date, any window time, or any scheduling behavior. **Identical output is the acceptance bar.**
- Small commits, conventional prefixes, touched suite green before each commit.
- **The venv is NOT in the worktree.** It lives in the main repo:
  `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe` (Python 3.11.9). Work
  from the worktree root and invoke it by absolute path. Verified working:
  `"D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest shared/tests -q`
  Every `.venv\Scripts\python` in this document means that absolute path.

## Task A1 result — GATE PASSED (2026-08-02)

Ran ahead of implementation. **All eight in-scope holiday sets are byte-identical
at 20 dates**: `options_svc`, `sentiment_svc`, `portfolio_svc`, `market_svc`,
`driver_svc`, `shared/notify/channels.py`, `webgui/alerts.py`,
`options-scanner/scanner.py`.

Two findings:

1. **The `webgui/alerts.py:14-19` comment is false.** It claims the service copies
   omit 2027 and Juneteenth. They do not — all eight carry both. Delete that
   comment during Task A11, as the plan already specifies.
2. **`claude-driver/config.py` `MARKET_HOLIDAYS` stores ISO strings**
   (`"2026-01-01"`), not `date()` objects — which is why the extractor reported it
   MISSING rather than divergent. Spot-checking confirms the same dates. It remains
   **deliberately exempt** from Phase A (legacy module; its morning-agent consumers
   were deleted 2026-07-08, only `RISK_LIMITS` is still read).

Consolidating the eight is therefore safe. Task A1 needs no re-run.

## Two calendar bugs found during Phase A — FIX AFTER PHASE B (decided 2026-08-02)

The consolidation surfaced two genuine correctness bugs. **Neither is a regression
from this work** — both predate it. Both are scheduled to be fixed **after the
Phase B migration lands**, each in its own clearly-labelled behavior-change commit,
so the migration itself keeps its "identical output" guarantee and the fixes are
visible as deliberate deltas rather than smuggled in.

**Bug 1 — `options-scanner/scanner_engine.py:91` `HOLIDAYS_2026`.** A **tenth**
holiday site, missed by the A1 gate because its name did not match the pattern
searched. It holds **9 dates, 2026 only** — missing **Juneteenth 2026-06-19** and
**all ten 2027 holidays**. It is load-bearing:

```
paper_engine.in_trading_window  ->  scanner_engine.is_market_hours
                                ->  scanner_engine.is_trading_day  ->  HOLIDAYS_2026
```

So from **2027-01-01 the paper engine treats every market holiday as a trading
day** and will run entry/manage cycles against a closed market. Juneteenth 2026
already fired. Fix by repointing to `shared.market_calendar`.

**Bug 2 — year-boundary holiday spill.** When 1 January falls on a Saturday the
NYSE observes the holiday on the **prior** 31 December, so `nyse_holidays(2028)`
contains `2027-12-31`. Because `is_holiday` looks up only `nyse_holidays(d.year)`:

```
Jan 1 2028 falls on: Saturday
is_holiday(date(2027, 12, 31))     -> False     # NYSE is CLOSED that day
is_trading_day(date(2027, 12, 31)) -> True      # wrong
```

The hardcoded sets being replaced have the **identical** gap, which is why fixing
it is a behavior change rather than a refactor. First occurrence is **2027-12-31**,
inside the calendar's live window. Fix:

```python
return d in nyse_holidays(d.year) or d in nyse_holidays(d.year + 1)
```

with a regression test pinning `2027-12-31`.

**Also noted, unrelated and not absorbed:** `services/sentiment_svc` currently
reports **1 failed, 188 passed** (`test_daily_history_wins_over_session_latch`, a
VIX1D session-latch test), verified failing at the pre-work base commit. The root
CLAUDE.md documents a 189-green baseline, so either that figure is stale or
something regressed before this work began. Out of scope here.

---

# Phase A — Single market calendar (holidays in 1 place, not 9)

## Task A1: Prove the nine holiday sets are identical before consolidating

Do not skip this. The whole refactor rests on the claim that these sets are interchangeable, and `webgui/alerts.py:14-19` carries a comment asserting the service copies *omit* 2027 and Juneteenth. That claim needs to be verified or disproved before anything is deleted.

**Files:**
- Create: `docs/plans/scratch/verify_holidays.py` (throwaway, deleted in Step 3)

**Step 1: Write the comparison script**

```python
# docs/plans/scratch/verify_holidays.py
"""One-shot: prove every duplicated holiday set is identical. Deleted after use."""
import ast, pathlib, sys

SITES = [
    ("options_svc",   "services/options_svc/scheduler.py",   "_HOLIDAYS"),
    ("sentiment_svc", "services/sentiment_svc/scheduler.py", "_HOLIDAYS"),
    ("portfolio_svc", "services/portfolio_svc/scheduler.py", "_HOLIDAYS"),
    ("market_svc",    "services/market_svc/scheduler.py",    "_HOLIDAYS"),
    ("driver_svc",    "services/driver_svc/scheduler.py",    "_HOLIDAYS"),
    ("notify",        "shared/notify/channels.py",           "_HOLIDAYS"),
    ("webgui",        "webgui/alerts.py",                    "_HOLIDAYS"),
    ("scanner",       "options-scanner/scanner.py",          "HOLIDAYS"),
    ("claude-driver", "claude-driver/config.py",             "MARKET_HOLIDAYS"),
]


def extract(path, name):
    """Pull the literal date set out of the source without importing the module."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == name:
                out = set()
                # frozenset({...}) wrapper or a bare {...}
                v = node.value
                if isinstance(v, ast.Call):
                    v = v.args[0]
                for elt in v.elts:
                    args = [a.value for a in elt.args]
                    out.add(tuple(args))
                return out
    return None


sets = {}
for label, path, name in SITES:
    s = extract(path, name)
    sets[label] = s
    print(f"{label:15} {'MISSING' if s is None else len(s)} dates")

base_label, base = "options_svc", sets["options_svc"]
ok = True
for label, s in sets.items():
    if s is None:
        print(f"!! {label}: could not extract")
        ok = False
        continue
    if s != base:
        ok = False
        print(f"!! {label} DIFFERS from {base_label}")
        print(f"   only in {label}: {sorted(s - base)}")
        print(f"   only in {base_label}: {sorted(base - s)}")
print("\nALL IDENTICAL" if ok else "\nDIVERGENCE FOUND")
sys.exit(0 if ok else 1)
```

**Step 2: Run it**

Run: `.venv\Scripts\python docs\plans\scratch\verify_holidays.py`

Expected: each site reports **20 dates** and the script prints `ALL IDENTICAL`.

**If it prints `DIVERGENCE FOUND`: STOP.** Do not proceed. Record the differing dates in the plan and ask which set is authoritative. A silent holiday change would alter scheduling behavior, which this phase forbids.

`claude-driver/config.py` is legacy (its morning-agent consumers were removed 2026-07-08) and may legitimately differ or be unused. If **only** that site differs, note it, leave `claude-driver/config.py` untouched for the whole of Phase A, and continue.

**Step 3: Delete the scratch script and commit nothing**

```bash
rm -r docs/plans/scratch
```

This task produces no commit — it is a gate.

---

## Task A2: Add `SESSIONS_TOML` to repo_paths

**Files:**
- Modify: `repo_paths.py:33` (after `FLOW_ALERTS_TOML`)

**Step 1: Write the failing test**

```python
# shared/tests/test_market_calendar.py
import repo_paths


def test_sessions_toml_path_declared():
    assert repo_paths.SESSIONS_TOML.name == "sessions.toml"
    assert repo_paths.SESSIONS_TOML.parent.name == "config"
```

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: FAIL — `AttributeError: module 'repo_paths' has no attribute 'SESSIONS_TOML'`

**Step 3: Add the constant**

In `repo_paths.py`, directly below the `FLOW_ALERTS_TOML` line:

```python
SESSIONS_TOML = REPO_ROOT / "config" / "sessions.toml"
```

**Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add repo_paths.py shared/tests/test_market_calendar.py
git commit -m "feat(config): declare SESSIONS_TOML path"
```

---

## Task A3: Create `config/sessions.toml`

**Files:**
- Create: `config/sessions.toml`

**Step 1: Write the file**

```toml
# Market session windows + the extended-hours activation date.
#
# All times are America/Chicago (CT). ET and CT observe DST together, so these
# CT values are stable year-round and need no seasonal adjustment.
#
# Edit + restart the affected service to change a window. See
# docs/plans/2026-08-02-options-extended-hours-design.md.

[activation]
# Cboe extended trading hours for select multi-listed single-stock options.
# Every ETH branch in the codebase is INERT before this date: session_at()
# reports "closed" for the ETH windows and all behavior is byte-identical to
# the pre-ETH app. Cboe's published launch is 2026-07-13 "subject to SEC
# approval of a related rule filing"; this project targets 2026-08-17.
extended_hours_from = "2026-08-17"

# ── Sessions ───────────────────────────────────────────────────────────────
[sessions.gth]
start = "06:30"   # 07:30 ET
end   = "08:25"   # 09:25 ET

[sessions.regular]
start = "08:30"   # 09:30 ET
end   = "15:00"   # 16:00 ET

[sessions.curb]
start = "15:00"   # 16:00 ET
end   = "15:15"   # 16:15 ET

# ── Named operating windows (NOT sessions — service cadence windows) ───────
# These legitimately differ from each other and are kept distinct, not merged.
[windows.scan]
# options_svc auto-scan operating window (includes premarket by design).
start = "08:00"
end   = "15:15"

[windows.collection]
# GEX history collection. `start` is the full-universe start; `eth_start`
# applies ONLY to ethOptionEligible symbols on/after the activation date.
start     = "08:00"
eth_start = "06:30"
stop      = "15:20"

[windows.session_flip]
# When the Gamma display flips from the prior session to today. Held SEPARATE
# from collection.start on purpose: widening premarket collection must not
# silently move the display flip. See hazard H2 in the design doc.
at = "08:00"

[windows.market_snapshot]
start = "08:30"
end   = "15:00"

[windows.driver_entry]
# Autonomous driver entry window, in ET (not CT) — matches the existing
# constant it replaces. Skips the open's first ~15 min and takes no new
# entries in the last 30 min before the close.
tz    = "America/New_York"
start = "09:45"
end   = "15:30"

[alerts]
# Fire phone pushes / in-app toasts during extended sessions. Off by default:
# a 06:30 CT push on a handful of thin premarket prints is noise, and the
# execution posture is observe-only. Data still accrues either way.
fire_in_extended_hours = false
```

**Step 2: Commit**

```bash
git add config/sessions.toml
git commit -m "feat(config): add sessions.toml with windows + ETH activation date"
```

---

## Task A4: Create `shared/market_calendar.py` — holidays and trading days only

Sessions come in Phase B. This task ships the holiday half so it can be migrated and verified independently.

**Files:**
- Create: `shared/market_calendar.py`
- Test: `shared/tests/test_market_calendar.py` (append)

**Step 1: Write the failing tests**

Append to `shared/tests/test_market_calendar.py`:

```python
from datetime import date

from shared import market_calendar as mc


def test_holidays_is_frozenset_of_20_dates():
    assert isinstance(mc.HOLIDAYS, frozenset)
    assert len(mc.HOLIDAYS) == 20            # 10 per year, 2026 + 2027
    assert date(2026, 6, 19) in mc.HOLIDAYS  # Juneteenth
    assert date(2027, 12, 24) in mc.HOLIDAYS  # Christmas observed 2027


def test_is_holiday():
    assert mc.is_holiday(date(2026, 12, 25)) is True
    assert mc.is_holiday(date(2026, 12, 24)) is False


def test_is_trading_day_excludes_weekends_and_holidays():
    assert mc.is_trading_day(date(2026, 7, 6)) is True    # Monday
    assert mc.is_trading_day(date(2026, 7, 4)) is False   # Saturday
    assert mc.is_trading_day(date(2026, 7, 5)) is False   # Sunday
    assert mc.is_trading_day(date(2026, 7, 3)) is False   # holiday (observed)


def test_prev_trading_day_skips_weekend():
    # Monday 2026-07-06 -> Thursday 2026-07-02 (Fri 7/3 is the observed holiday)
    assert mc.prev_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)


def test_next_trading_day_skips_weekend():
    assert mc.next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


def test_prev_trading_day_is_strictly_before():
    d = date(2026, 7, 7)                      # a plain Tuesday
    assert mc.prev_trading_day(d) < d
```

**Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.market_calendar'`

**Step 3: Write the module**

```python
# shared/market_calendar.py
"""Single source of truth for the market calendar and session windows.

Replaces nine duplicated holiday sets and fourteen hardcoded window constants
across the five Tier-2 services, the webgui and the options-scanner engines.

Everything here is a PURE function of its arguments plus ``config/sessions.toml``
(mtime-cached, mirroring ``services/options_svc/flow_alerts.load_thresholds``).
No network, no database, and no clock unless you omit an optional ``now``.

``shared/`` is a namespace package (no ``__init__.py``), so
``from shared.market_calendar import ...`` resolves once the repo root is on
``sys.path`` — which the services and webgui already arrange. The one caller
needing a bootstrap is ``options-scanner/scanner.py`` (legacy standalone CLI);
use the three-line pattern from ``services/options_svc/commission.py``.

**Update HOLIDAYS yearly** — add the next year, drop the oldest.
"""
import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

# NYSE full-closure holidays, 2026–2027. Observed dates follow NYSE rules
# (Saturday -> prior Friday, Sunday -> following Monday). Includes Juneteenth
# and Good Friday.
HOLIDAYS = frozenset({
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3),                                        # Good Friday
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15),
    date(2027, 3, 26),                                       # Good Friday
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

_MAX_SPAN_DAYS = 10  # longest plausible market closure; bounds the search loops


def is_holiday(d) -> bool:
    """True if ``d`` (a ``date``) is an NYSE full-closure holiday."""
    return d in HOLIDAYS


def is_trading_day(d) -> bool:
    """True if ``d`` is a weekday that is not an NYSE full-closure holiday."""
    return d.weekday() < 5 and d not in HOLIDAYS


def prev_trading_day(d):
    """Most recent trading day STRICTLY before ``d``."""
    for _ in range(_MAX_SPAN_DAYS):
        d = d - timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days before {d}")


def next_trading_day(d):
    """Next trading day STRICTLY after ``d``."""
    for _ in range(_MAX_SPAN_DAYS):
        d = d + timedelta(days=1)
        if is_trading_day(d):
            return d
    raise ValueError(f"no trading day within {_MAX_SPAN_DAYS} days after {d}")
```

**Step 4: Run to verify they pass**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add shared/market_calendar.py shared/tests/test_market_calendar.py
git commit -m "feat(shared): add market_calendar with the single holiday set"
```

---

## Tasks A5–A12: Migrate each holiday consumer

These eight tasks are mechanically identical. **Do them one at a time, each with its own test run and commit.** Each is ~3 minutes.

For each site: delete the local set, import the shared one, keep the local name as an alias so no call site changes, run that folder's suite, commit.

### The pattern

**Before** (e.g. `services/options_svc/scheduler.py:29-45`):

```python
# US market holidays 2026–2027 (keep in sync with the other service schedulers...)
_HOLIDAYS = {
    _date(2026, 1, 1), ...
}


def _is_trading_day(now):
    return now.weekday() < 5 and now.date() not in _HOLIDAYS
```

**After:**

```python
from shared.market_calendar import HOLIDAYS as _HOLIDAYS, is_trading_day as _cal_is_trading_day


def _is_trading_day(now):
    return _cal_is_trading_day(now.date())
```

Keep `_HOLIDAYS` bound (as the import alias) — several modules reference it directly beyond `_is_trading_day`, and rebinding it to the shared frozenset is behavior-preserving. Delete the now-obsolete "keep in sync" comments.

### Task list

| Task | File | Local name | Suite to run |
|---|---|---|---|
| A5 | `services/options_svc/scheduler.py:32` | `_HOLIDAYS` | `pytest services\options_svc` |
| A6 | `services/sentiment_svc/scheduler.py:41` | `_HOLIDAYS` | `pytest services\sentiment_svc` |
| A7 | `services/portfolio_svc/scheduler.py:48` | `_HOLIDAYS` | `pytest services\portfolio_svc` |
| A8 | `services/market_svc/scheduler.py:28` | `_HOLIDAYS` | `pytest services\market_svc` |
| A9 | `services/driver_svc/scheduler.py:41` | `_HOLIDAYS` | `pytest services\driver_svc` |
| A10 | `shared/notify/channels.py:364` | `_HOLIDAYS` | `pytest shared\notify` |
| A11 | `webgui/alerts.py:20` | `_HOLIDAYS` | `cd webgui && pytest .` |
| A12 | `options-scanner/scanner.py:111` | `Config.HOLIDAYS` | `cd options-scanner && pytest tests` |

**Notes on the awkward ones:**

**A11 (`webgui/alerts.py`)** — also delete the stale comment at lines 14-19 claiming the service copies omit 2027 and Juneteenth. Task A1 proved that false. Replace with a one-liner pointing at the shared module.

**A12 (`options-scanner/scanner.py`)** — `Config.HOLIDAYS` is a class attribute and the module is a standalone CLI, so it needs the `sys.path` bootstrap. Add at the top of the file, modeled on `services/options_svc/commission.py:9-12`:

```python
import sys as _sys, pathlib as _pathlib
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))
from shared.market_calendar import HOLIDAYS as _SHARED_HOLIDAYS
```

then inside `Config`:

```python
    HOLIDAYS = _SHARED_HOLIDAYS
```

**`claude-driver/config.py:118` (`MARKET_HOLIDAYS`) is deliberately NOT migrated.** That module is legacy — its morning-agent consumers were deleted 2026-07-08 and only `RISK_LIMITS` is still read. Leave it alone; touching it risks the documented `config` module-name collision for no benefit.

**Per-task steps (repeat for each):**

1. Apply the edit.
2. Run that row's suite. Expected: **same pass count as before the edit**, zero new failures.
3. Commit: `git commit -m "refactor(<area>): read holidays from shared.market_calendar"`

**Step: verify the consolidation is complete**

After A12, run:

```bash
grep -rn "date(2026, 6, 19)" --include=*.py . | grep -v "\.venv" | grep -v market_calendar.py | grep -v claude-driver
```

Expected: **no output** (every copy but the shared one and the exempt legacy file is gone).

Commit nothing for the grep — it is a verification gate.

---

# Phase B — Session windows in the calendar

## Task B1: Add the `Session` enum, config loader, and `session_at`

**Files:**
- Modify: `shared/market_calendar.py`
- Test: `shared/tests/test_market_calendar.py` (append)

**Step 1: Write the failing tests**

```python
import datetime as dt
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


def _ct(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CT)


# ── activation gate ────────────────────────────────────────────────────────
def test_extended_hours_inactive_before_activation():
    assert mc.extended_hours_active(dt.date(2026, 8, 14)) is False   # Friday
    assert mc.extended_hours_active(dt.date(2026, 8, 16)) is False   # Sunday


def test_extended_hours_active_on_and_after_activation():
    assert mc.extended_hours_active(dt.date(2026, 8, 17)) is True    # Monday
    assert mc.extended_hours_active(dt.date(2026, 9, 1)) is True


# ── sessions BEFORE activation: ETH windows must read CLOSED ───────────────
def test_premarket_is_closed_before_activation():
    assert mc.session_at(_ct(2026, 8, 14, 7, 0)) is mc.Session.CLOSED


def test_curb_is_closed_before_activation():
    assert mc.session_at(_ct(2026, 8, 14, 15, 5)) is mc.Session.CLOSED


def test_regular_session_unaffected_by_activation():
    assert mc.session_at(_ct(2026, 8, 14, 10, 0)) is mc.Session.REGULAR


# ── sessions ON/AFTER activation ───────────────────────────────────────────
def test_premarket_session_after_activation():
    assert mc.session_at(_ct(2026, 8, 17, 7, 0)) is mc.Session.GTH


def test_curb_session_after_activation():
    assert mc.session_at(_ct(2026, 8, 17, 15, 5)) is mc.Session.CURB


# ── boundaries ─────────────────────────────────────────────────────────────
def test_session_boundaries_after_activation():
    d = (2026, 8, 17)
    assert mc.session_at(_ct(*d, 6, 29)) is mc.Session.CLOSED
    assert mc.session_at(_ct(*d, 6, 30)) is mc.Session.GTH
    assert mc.session_at(_ct(*d, 8, 25)) is mc.Session.GTH
    assert mc.session_at(_ct(*d, 8, 26)) is mc.Session.CLOSED   # 5-min gap
    assert mc.session_at(_ct(*d, 8, 30)) is mc.Session.REGULAR
    assert mc.session_at(_ct(*d, 15, 0)) is mc.Session.REGULAR  # regular wins the overlap
    assert mc.session_at(_ct(*d, 15, 1)) is mc.Session.CURB
    assert mc.session_at(_ct(*d, 15, 15)) is mc.Session.CURB
    assert mc.session_at(_ct(*d, 15, 16)) is mc.Session.CLOSED


def test_non_trading_day_is_always_closed():
    assert mc.session_at(_ct(2026, 8, 22, 10, 0)) is mc.Session.CLOSED  # Saturday
    assert mc.session_at(_ct(2026, 9, 7, 10, 0)) is mc.Session.CLOSED   # Labor Day


def test_is_regular_hours_matches_session_at():
    assert mc.is_regular_hours(_ct(2026, 8, 17, 10, 0)) is True
    assert mc.is_regular_hours(_ct(2026, 8, 17, 7, 0)) is False
```

Note the deliberate boundary decision asserted above: **regular wins the 15:00 overlap** with the curb window, so nothing that gates on `REGULAR` loses its final minute.

**Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: FAIL — `AttributeError: module 'shared.market_calendar' has no attribute 'Session'`

**Step 3: Implement**

Append to `shared/market_calendar.py`:

```python
import datetime as _dt
import os
import tomllib
from enum import Enum
from zoneinfo import ZoneInfo

from repo_paths import SESSIONS_TOML

CT = ZoneInfo("America/Chicago")


class Session(Enum):
    """Which market session a moment falls in. ETH members are only ever
    returned on/after the configured activation date."""
    CLOSED = "closed"
    GTH = "gth"
    REGULAR = "regular"
    CURB = "curb"


_DEFAULTS = {
    "activation": {"extended_hours_from": "2026-08-17"},
    "sessions": {
        "gth": {"start": "06:30", "end": "08:25"},
        "regular":       {"start": "08:30", "end": "15:00"},
        "curb":      {"start": "15:00", "end": "15:15"},
    },
    "windows": {
        "scan":            {"start": "08:00", "end": "15:15"},
        "collection":      {"start": "08:00", "eth_start": "06:30", "stop": "15:20"},
        "session_flip":    {"at": "08:00"},
        "market_snapshot": {"start": "08:30", "end": "15:00"},
        "driver_entry":    {"tz": "America/New_York", "start": "09:45", "end": "15:30"},
    },
    "alerts": {"fire_in_extended_hours": False},
}

_CACHE = {"mtime": None, "cfg": None}


def reset_config_cache():
    """Drop the mtime-cached config (test helper)."""
    _CACHE.update(mtime=None, cfg=None)


def _merge(base, over):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """sessions.toml merged over the built-in defaults. Never raises.

    mtime-cached: session predicates are called on every scheduler tick but the
    file effectively never changes, so re-parse only when its mtime moves."""
    try:
        mtime = os.stat(SESSIONS_TOML).st_mtime
    except OSError:
        mtime = None
    if _CACHE["cfg"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["cfg"]
    try:
        with open(SESSIONS_TOML, "rb") as fh:
            cfg = _merge(_DEFAULTS, tomllib.load(fh))
    except Exception:
        log.debug("sessions.toml load failed -> defaults", exc_info=True)
        cfg = _merge(_DEFAULTS, {})
    _CACHE.update(mtime=mtime, cfg=cfg)
    return cfg


def _parse_time(s, fallback):
    """'HH:MM' -> datetime.time; anything malformed falls back rather than raising."""
    try:
        hh, mm = str(s).split(":")
        return _dt.time(int(hh), int(mm))
    except (ValueError, AttributeError):
        log.warning("bad time %r in sessions.toml -> using %s", s, fallback)
        return fallback


def _session_bounds(name):
    cfg = load_config()["sessions"][name]
    dflt = _DEFAULTS["sessions"][name]
    return (_parse_time(cfg.get("start"), _parse_time(dflt["start"], _dt.time(0, 0))),
            _parse_time(cfg.get("end"), _parse_time(dflt["end"], _dt.time(0, 0))))


def activation_date():
    """The date on/after which extended-hours behavior switches on."""
    raw = load_config()["activation"]["extended_hours_from"]
    try:
        return _dt.date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        log.warning("bad activation date %r -> using default", raw)
        return _dt.date.fromisoformat(_DEFAULTS["activation"]["extended_hours_from"])


def extended_hours_active(d) -> bool:
    """True when ``d`` (a ``date``) is on/after the ETH activation date.

    Before it, every ETH branch in the app must behave exactly as it did
    pre-ETH — this is the single gate that guarantees that."""
    return d >= activation_date()


def _ct_of(now):
    return now.astimezone(CT) if now.tzinfo else now.replace(tzinfo=CT)


def session_at(now) -> Session:
    """Which session ``now`` (a datetime; naive is treated as CT) falls in.

    Returns CLOSED on weekends, holidays, and — before the activation date —
    for both extended-hours windows. REGULAR wins the 15:00 overlap with the
    curb window so RTH-gated work does not lose its final minute."""
    ct = _ct_of(now)
    if not is_trading_day(ct.date()):
        return Session.CLOSED
    t = ct.time()

    r_start, r_end = _session_bounds("regular")
    if r_start <= t <= r_end:
        return Session.REGULAR

    if not extended_hours_active(ct.date()):
        return Session.CLOSED

    p_start, p_end = _session_bounds("gth")
    if p_start <= t <= p_end:
        return Session.GTH

    c_start, c_end = _session_bounds("curb")
    if c_start <= t <= c_end:
        return Session.CURB

    return Session.CLOSED


def is_regular_hours(now) -> bool:
    """True during the regular cash session only (08:30-15:00 CT, trading days)."""
    return session_at(now) is Session.REGULAR


def is_extended_hours(now) -> bool:
    """True during either extended session. Always False before activation."""
    return session_at(now) in (Session.GTH, Session.CURB)


def alerts_fire_in_extended_hours() -> bool:
    """Whether alert emission is permitted during extended sessions."""
    return bool(load_config()["alerts"]["fire_in_extended_hours"])
```

**Step 4: Run to verify they pass**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add shared/market_calendar.py shared/tests/test_market_calendar.py
git commit -m "feat(shared): add Session enum, sessions.toml loader and activation gate"
```

---

## Task B2: Add named operating windows

**Files:**
- Modify: `shared/market_calendar.py`
- Test: `shared/tests/test_market_calendar.py` (append)

**Step 1: Write the failing tests**

```python
def test_in_window_scan():
    assert mc.in_window("scan", _ct(2026, 8, 17, 8, 0)) is True
    assert mc.in_window("scan", _ct(2026, 8, 17, 7, 59)) is False
    assert mc.in_window("scan", _ct(2026, 8, 17, 15, 15)) is True
    assert mc.in_window("scan", _ct(2026, 8, 17, 15, 16)) is False


def test_in_window_false_on_non_trading_day():
    assert mc.in_window("scan", _ct(2026, 8, 22, 10, 0)) is False   # Saturday


def test_collection_window_ineligible_symbol_starts_at_0800():
    # A non-ETH symbol never collects premarket, activation or not.
    assert mc.in_collection_window(_ct(2026, 8, 17, 7, 0), eth_eligible=False) is False
    assert mc.in_collection_window(_ct(2026, 8, 17, 8, 0), eth_eligible=False) is True


def test_collection_window_eligible_symbol_starts_at_0630_after_activation():
    assert mc.in_collection_window(_ct(2026, 8, 17, 6, 30), eth_eligible=True) is True
    assert mc.in_collection_window(_ct(2026, 8, 17, 6, 29), eth_eligible=True) is False


def test_collection_window_eligible_symbol_is_inert_before_activation():
    # THE critical regression guard: pre-activation, an eligible symbol must
    # behave exactly like today (08:00 start, no premarket).
    assert mc.in_collection_window(_ct(2026, 8, 14, 7, 0), eth_eligible=True) is False
    assert mc.in_collection_window(_ct(2026, 8, 14, 8, 0), eth_eligible=True) is True


def test_collection_window_stop_is_exclusive():
    assert mc.in_collection_window(_ct(2026, 8, 17, 15, 19)) is True
    assert mc.in_collection_window(_ct(2026, 8, 17, 15, 20)) is False


def test_session_flip_time_is_independent_of_collection_start():
    # Hazard H2: widening premarket collection must NOT move the display flip.
    assert mc.session_flip_time() == dt.time(8, 0)
```

**Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'in_window'`

**Step 3: Implement**

Append to `shared/market_calendar.py`:

```python
def _window(name):
    return _merge(_DEFAULTS["windows"][name], load_config()["windows"].get(name, {}))


def window_bounds(name):
    """(start, end) ``time`` objects for a named operating window."""
    w = _window(name)
    dflt = _DEFAULTS["windows"][name]
    start = _parse_time(w.get("start"), _parse_time(dflt["start"], _dt.time(0, 0)))
    end_raw = w.get("end", w.get("stop"))
    end_dflt = dflt.get("end", dflt.get("stop"))
    return start, _parse_time(end_raw, _parse_time(end_dflt, _dt.time(0, 0)))


def in_window(name, now) -> bool:
    """True on a trading day within the named window (inclusive both ends)."""
    ct = _ct_of(now)
    if not is_trading_day(ct.date()):
        return False
    start, end = window_bounds(name)
    return start <= ct.time() <= end


def session_flip_time():
    """When the Gamma display flips from the prior session to today.

    Deliberately independent of the collection start: widening premarket
    collection must not silently move the display flip (hazard H2)."""
    at = _window("session_flip").get("at")
    return _parse_time(at, _dt.time(8, 0))


def in_collection_window(now, *, eth_eligible=False) -> bool:
    """True when GEX history collection should run for a symbol right now.

    ``eth_eligible`` symbols start at ``collection.eth_start`` (06:30) on and
    after the activation date; everything else — and everything before
    activation — starts at ``collection.start`` (08:00). The stop is EXCLUSIVE,
    matching the ``_in_gex_window`` semantics this replaces."""
    ct = _ct_of(now)
    if not is_trading_day(ct.date()):
        return False
    w = _window("collection")
    start = _parse_time(w.get("start"), _dt.time(8, 0))
    if eth_eligible and extended_hours_active(ct.date()):
        start = _parse_time(w.get("eth_start"), _dt.time(6, 30))
    stop = _parse_time(w.get("stop"), _dt.time(15, 20))
    return start <= ct.time() < stop
```

**Step 4: Run to verify they pass**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add shared/market_calendar.py shared/tests/test_market_calendar.py
git commit -m "feat(shared): add named operating windows + ETH-aware collection window"
```

---

## Task B3: Equivalence-test the migration before doing it

This is the acceptance gate for Phase B. It proves the calendar reproduces every existing predicate exactly, minute by minute, **before** any consumer is changed.

**Files:**
- Test: `shared/tests/test_market_calendar_equivalence.py`

**Step 1: Write the test**

```python
# shared/tests/test_market_calendar_equivalence.py
"""Phase B acceptance gate: the shared calendar must reproduce every legacy
window predicate EXACTLY, minute by minute, across a trading day, a weekend
day and a holiday. If any of these fail, the migration is not safe."""
import datetime as dt
from zoneinfo import ZoneInfo

from shared import market_calendar as mc

CT = ZoneInfo("America/Chicago")

DAYS = [
    dt.date(2026, 8, 14),   # Friday, BEFORE activation
    dt.date(2026, 8, 17),   # Monday, activation day
    dt.date(2026, 8, 22),   # Saturday
    dt.date(2026, 9, 7),    # Labor Day
]


def _minutes(day):
    for hh in range(24):
        for mm in range(60):
            yield dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=CT)


def _legacy_is_trading_day(now):
    """Verbatim copy of the predicate every scheduler used pre-migration."""
    holidays = {
        dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16),
        dt.date(2026, 4, 3), dt.date(2026, 5, 25), dt.date(2026, 6, 19),
        dt.date(2026, 7, 3), dt.date(2026, 9, 7), dt.date(2026, 11, 26),
        dt.date(2026, 12, 25),
        dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15),
        dt.date(2027, 3, 26), dt.date(2027, 5, 31), dt.date(2027, 6, 18),
        dt.date(2027, 7, 5), dt.date(2027, 9, 6), dt.date(2027, 11, 25),
        dt.date(2027, 12, 24),
    }
    return now.weekday() < 5 and now.date() not in holidays


def test_trading_day_predicate_is_unchanged():
    for day in DAYS:
        now = dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=CT)
        assert mc.is_trading_day(day) == _legacy_is_trading_day(now), day


def test_scan_window_matches_legacy_0800_1515():
    for day in DAYS:
        for now in _minutes(day):
            legacy = (_legacy_is_trading_day(now)
                      and dt.time(8, 0) <= now.time() <= dt.time(15, 15))
            assert mc.in_window("scan", now) == legacy, now


def test_rth_window_matches_legacy_0830_1500():
    """The window shared by sentiment_svc, portfolio_svc, market_svc and
    paper_engine. NOTE: market_svc/portfolio_svc use <= on the end and
    sentiment_svc uses < — asserted separately in their own suites."""
    for day in DAYS:
        for now in _minutes(day):
            legacy = (_legacy_is_trading_day(now)
                      and dt.time(8, 30) <= now.time() <= dt.time(15, 0))
            assert mc.is_regular_hours(now) == legacy, now


def test_collection_window_matches_legacy_for_ineligible_symbols():
    for day in DAYS:
        for now in _minutes(day):
            legacy = (_legacy_is_trading_day(now)
                      and (8, 0) <= (now.hour, now.minute) < (15, 20))
            assert mc.in_collection_window(now, eth_eligible=False) == legacy, now


def test_collection_window_matches_legacy_for_eligible_symbols_pre_activation():
    """The inertness guarantee, exhaustively: before 2026-08-17 an ETH-eligible
    symbol collects on exactly the legacy schedule."""
    day = dt.date(2026, 8, 14)
    for now in _minutes(day):
        legacy = (_legacy_is_trading_day(now)
                  and (8, 0) <= (now.hour, now.minute) < (15, 20))
        assert mc.in_collection_window(now, eth_eligible=True) == legacy, now
```

**Step 2: Run it**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar_equivalence.py -v`
Expected: PASS. **If any test fails, fix `market_calendar.py` — not the test.** The legacy behavior is the specification.

**Step 3: Commit**

```bash
git add shared/tests/test_market_calendar_equivalence.py
git commit -m "test(shared): pin calendar equivalence with the legacy window predicates"
```

---

## Tasks B4–B9: Migrate each window consumer

One task each, same discipline as A5–A12: edit, run that folder's suite, confirm the **pass count is unchanged**, commit.

| Task | File | Replace | With |
|---|---|---|---|
| B4 | `services/options_svc/scheduler.py:27-28,48-50` | `_SCAN_START/_SCAN_END` + `_is_market_hours` | `mc.in_window("scan", now)` |
| B5 | `services/options_svc/scheduler.py:78-84` | `_GEX_START/_GEX_STOP` + `_in_gex_window` | `mc.in_collection_window(now)` |
| B6 | `services/options_svc/scheduler.py:104-118` | `active_session_date`'s `>= _GEX_START` test | `>= mc.session_flip_time()` |
| B7 | `services/sentiment_svc/scheduler.py:35-58` | `_RTH_START/_RTH_END` | `mc.is_regular_hours(now)` |
| B8 | `services/portfolio_svc/scheduler.py:43-64` + `services/market_svc/scheduler.py:25-41` | `_RTH_START/_RTH_END` | `mc.is_regular_hours(now)` |
| B9 | `options-scanner/gex_status.py:8-9` | `MARKET_OPEN = 08:30` | collection window bounds |

**B6 is the hazard-H2 task and deserves care.** `active_session_date` currently pivots on `_GEX_START`:

```python
    if is_td and (now.hour, now.minute) >= _GEX_START:
```

becomes:

```python
    flip = mc.session_flip_time()
    if is_td and (now.hour, now.minute) >= (flip.hour, flip.minute):
```

Add a regression test in `services/options_svc/tests/test_scheduler.py` asserting the flip stays at 08:00 even once premarket collection starts at 06:30:

```python
def test_session_flip_unaffected_by_premarket_collection():
    """Widening collection to 06:30 must NOT move the Gamma display flip."""
    from services.options_svc import scheduler as s
    ct = ZoneInfo("America/Chicago")
    # 07:00 CT on activation day: collecting, but display still shows yesterday.
    now = datetime(2026, 8, 17, 7, 0, tzinfo=ct)
    assert s.active_session_date(now) == date(2026, 8, 14)   # prior Friday
    # 08:00 CT: display flips to today.
    assert s.active_session_date(datetime(2026, 8, 17, 8, 0, tzinfo=ct)) == date(2026, 8, 17)
```

**B9 fixes a live bug.** `gex_status.MARKET_OPEN = dtime(8, 30)` and the string `"Collector: starts 8:30"` at line 44 have been wrong since collection moved to 08:00 on 2026-07-11. Correct both, sourcing from `mc.window_bounds("collection")`, and format the message from the actual configured start rather than a literal.

**Note on `<=` vs `<`:** `sentiment_svc` uses `t < _RTH_END` (exclusive) while `portfolio_svc` and `market_svc` use `<=`. `mc.is_regular_hours` is **inclusive**. For `sentiment_svc` this widens the window by exactly one minute (15:00:00–15:00:59). That is a real, if trivial, behavior change. **Preserve the existing behavior**: in B7, use `mc.session_at(now) is mc.Session.REGULAR and now.time() < dt.time(15, 0)`, or add an explicit `end_exclusive=True` parameter. Do not silently widen it.

---

## Task B10: Pin the execution layer as deliberately unchanged

The design specifies `paper_engine` and the driver stay inert in ETH. Nothing changes — but that needs a test, or a future session will "helpfully" widen them.

**Files:**
- Test: `options-scanner/tests/test_paper_engine.py` (append)
- Test: `services/driver_svc/tests/test_scheduler_checkpoint.py` (append)

**Step 1: Write the guard tests**

```python
# options-scanner/tests/test_paper_engine.py
def test_paper_trading_window_excludes_extended_hours():
    """DELIBERATE: paper fills must never use ETH quotes. The 08:30-15:00 CT
    window is the observe-only posture from the 2026-08-02 ETH design doc.
    If this fails because someone widened the window, that is a REGRESSION,
    not an improvement — see docs/plans/2026-08-02-options-extended-hours-design.md §7."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    # Activation day, premarket ETH session:
    assert paper_engine.in_trading_window(dt.datetime(2026, 8, 17, 7, 0, tzinfo=ct)) is False
    # Activation day, curb ETH session:
    assert paper_engine.in_trading_window(dt.datetime(2026, 8, 17, 15, 5, tzinfo=ct)) is False
    # Regular session still works:
    assert paper_engine.in_trading_window(dt.datetime(2026, 8, 17, 10, 0, tzinfo=ct)) is True


def test_settlement_stays_at_1600_et_despite_curb_trading():
    """DELIBERATE, and verified against Cboe's Equity Options ETH FAQ:

        "Expiring equity single stock options will trade until 4:00 p.m. ET as
         part of RTH and 4:15 p.m. ET in the Curb session on expiration day...
         In all cases, OCC marks closing and/or settlement prices based on the
         4:00 p.m. ET National Best Bid and Offer (NBBO). OCC also bases
         in/out-of-the-money determination based on the 4:00 p.m. ET closing
         price of the underlying equity security."

    Curb trading on expiration day exists so holders can CLOSE rather than take
    delivery — it does NOT move settlement. Do not 'extend' this to 15:15 CT.
    See docs/plans/2026-08-02-options-extended-hours-design.md hazard H4."""
    assert paper_engine.SETTLE_HOUR_CT == 15          # 15:00 CT == 16:00 ET
    import options_calculator
    assert options_calculator.EXPIRY_CLOSE_HOUR_ET == 16

    import datetime as dt
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    exp = "2026-08-21"
    # 14:59 CT on expiry day: not yet settled.
    assert paper_engine.should_settle(
        exp, exp, dt.datetime(2026, 8, 21, 14, 59, tzinfo=ct)) is False
    # 15:00 CT (== 16:00 ET NBBO strike): settles, curb session notwithstanding.
    assert paper_engine.should_settle(
        exp, exp, dt.datetime(2026, 8, 21, 15, 0, tzinfo=ct)) is True
```

```python
# services/driver_svc/tests/test_scheduler_checkpoint.py
def test_driver_takes_no_entries_in_extended_hours():
    """DELIBERATE: the driver's 09:45-15:30 ET entry window excludes both ETH
    sessions. Observe-only posture — see the 2026-08-02 ETH design doc §7."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    # 07:45 ET = premarket ETH
    due, _ = scheduler.checkpoint_due(dt.datetime(2026, 8, 17, 7, 45, tzinfo=et), None)
    assert due is False
    # 16:05 ET = curb ETH
    due, _ = scheduler.checkpoint_due(dt.datetime(2026, 8, 17, 16, 5, tzinfo=et), None)
    assert due is False
```

**Step 2: Run**

Run: `cd options-scanner && ..\.venv\Scripts\python -m pytest tests\test_paper_engine.py -v`
Run: `.venv\Scripts\python -m pytest services\driver_svc -v`
Expected: PASS with **no production code changes** — these pin existing behavior.

**Step 3: Commit**

```bash
git commit -am "test: pin paper engine + driver as inert during extended hours"
```

---

# Phase C — Harvest `ethOptionEligible` (additive, inert)

## Task C1: Pure extraction helper

**Files:**
- Modify: `services/options_svc/matrix.py` (or create `services/options_svc/eth.py` if `matrix.py` exceeds ~400 lines)
- Test: `services/options_svc/tests/test_eth.py`

**Step 1: Write the failing tests**

```python
# services/options_svc/tests/test_eth.py
from services.options_svc import eth


def test_extracts_true():
    assert eth.chain_eth_eligible({"ethOptionEligible": True}) is True


def test_extracts_false():
    assert eth.chain_eth_eligible({"ethOptionEligible": False}) is False


def test_missing_field_degrades_to_false():
    """A chain without the field must NOT be treated as eligible — that would
    widen collection for the whole universe."""
    assert eth.chain_eth_eligible({"symbol": "SPY"}) is False


def test_none_chain_degrades_to_false():
    assert eth.chain_eth_eligible(None) is False


def test_non_bool_truthy_value_is_coerced():
    assert eth.chain_eth_eligible({"ethOptionEligible": "true"}) is True
```

**Step 2: Run to verify they fail**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_eth.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement**

```python
# services/options_svc/eth.py
"""Extended-trading-hours eligibility, harvested from the option chain.

Schwab publishes a root-level ``ethOptionEligible`` boolean on every chain
response, so the ETH-eligible symbol list is READ, never hardcoded — which
matters because Cboe refreshes that list semi-annually. See
docs/plans/2026-08-02-options-extended-hours-design.md §2.

PURE: no I/O. The handler wires the cache."""

CACHE_KEY = "cache:options:eth_eligible"


def chain_eth_eligible(chain) -> bool:
    """Whether this chain's symbol trades in extended hours.

    Degrades to False on a missing field or a malformed chain — never True,
    since a false positive would widen premarket collection for a symbol that
    does not quote."""
    if not isinstance(chain, dict):
        return False
    return bool(chain.get("ethOptionEligible", False))


def merge_eligibility(prior, symbol, eligible, *, date_iso):
    """Fold one symbol's eligibility into the cached {date, symbols} envelope.

    A new session date resets the map — eligibility is re-harvested daily so a
    semi-annual Cboe list change is picked up within one session."""
    prior = prior if isinstance(prior, dict) else {}
    symbols = dict(prior.get("symbols") or {}) if prior.get("date") == date_iso else {}
    symbols[symbol] = bool(eligible)
    return {"date": date_iso, "symbols": symbols}


def eligible_symbols(payload, *, date_iso=None) -> set:
    """The eligible symbol set from a cached envelope. Empty on a stale date."""
    payload = payload if isinstance(payload, dict) else {}
    if date_iso is not None and payload.get("date") != date_iso:
        return set()
    return {s for s, ok in (payload.get("symbols") or {}).items() if ok}
```

Add tests for `merge_eligibility` and `eligible_symbols` covering: date rollover clears the map, a stale date yields an empty set, and repeated merges accumulate.

**Step 4: Run to verify they pass**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_eth.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add services/options_svc/eth.py services/options_svc/tests/test_eth.py
git commit -m "feat(options): pure ETH-eligibility extraction from the chain"
```

---

## Task C2: Harvest eligibility on the existing `on_chain` hook

Zero new API calls — this rides the chain the 1-minute GEX poll already fetches.

**Files:**
- Modify: `services/options_svc/compute.py:1882` (the existing `on_chain` callback inside `collect_gex_snapshots`)
- Test: `services/options_svc/tests/test_compute.py` (append)

**Step 1: Write the failing test**

```python
def test_collect_gex_snapshots_harvests_eth_eligibility(monkeypatch):
    """The on_chain hook must record ethOptionEligible for every fetched chain
    without making any additional request."""
    from services.options_svc import compute, eth
    seen = {}
    monkeypatch.setattr(compute, "_publish_eth_eligibility",
                        lambda m: seen.update(m))
    # ... drive a fake poll_once that invokes on_chain with two chains:
    #     ("NVDA", {"ethOptionEligible": True}) and ("SPY", {"ethOptionEligible": False})
    assert seen == {"NVDA": True, "SPY": False}
```

Follow the existing fake-`poll_once` pattern already used in `test_compute.py` for the UOA stash tests.

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k eth_eligib -v`
Expected: FAIL

**Step 3: Implement**

Inside `collect_gex_snapshots`, extend the existing `on_chain` closure (line ~1882) — do **not** add a second hook:

```python
        _eth_seen = {}

        def on_chain(sym, chain):  # noqa: F811 — the callback poll_once calls
            if sym in wanted:
                _stash_tick_chain(sym, chain)
            # ETH eligibility rides this same already-fetched chain (no re-fetch).
            _eth_seen[sym] = eth.chain_eth_eligible(chain)
            ...
```

and after the poll completes, publish once:

```python
        _publish_eth_eligibility(_eth_seen)
```

`_publish_eth_eligibility` reads the current envelope, folds in each symbol via `eth.merge_eligibility`, and writes `cache:options:eth_eligible` with `skip_unchanged=True` (eligibility changes ~twice a year, so this should virtually never bump the version). Wrap it in `try/except` with `log.exception` — a harvest failure must never break GEX collection, consistent with how `run_flow_alerts` is guarded.

**Step 4: Run**

Expected: PASS. Then run the full `pytest services\options_svc` and confirm the pass count only increased.

**Step 5: Commit**

```bash
git commit -am "feat(options): harvest ETH eligibility on the existing chain hook"
```

---

# Phase D — Widened pre-market collection (first live behavior change)

## Task D1: Restrict the pre-market poll to eligible symbols

Polling all ~45 names pre-market would cost ~4,050 calls/day for ~38 symbols that are not quoting. Restricting to the eligible subset makes this **≈ +630 calls/day (+2%)**.

**Files:**
- Modify: `services/options_svc/scheduler.py` (`gex_due`)
- Modify: `services/options_svc/compute.py` (`collect_gex_snapshots` — pass `symbols=` to `poll_once`)
- Test: `services/options_svc/tests/test_scheduler.py`, `tests/test_compute.py`

**Step 1: Write the failing tests**

```python
def test_gex_due_premarket_only_on_activation_day():
    ct = ZoneInfo("America/Chicago")
    # 07:00 CT, day BEFORE activation -> not due (inert)
    due, _ = scheduler.gex_due(datetime(2026, 8, 14, 7, 0, tzinfo=ct), None)
    assert due is False
    # 07:00 CT, activation day -> due (eligible-symbol premarket poll)
    due, _ = scheduler.gex_due(datetime(2026, 8, 17, 7, 0, tzinfo=ct), None)
    assert due is True


def test_premarket_poll_uses_only_eligible_symbols(monkeypatch):
    """At 07:00 CT the poll must request the eligible subset, not the universe."""
    captured = {}
    # fake poll_once capturing its `symbols` kwarg; eligibility cache = {NVDA, TSLA}
    compute.collect_gex_snapshots(now=datetime(2026, 8, 17, 7, 0, tzinfo=ct))
    assert captured["symbols"] == ["NVDA", "TSLA"]


def test_regular_hours_poll_uses_full_universe(monkeypatch):
    compute.collect_gex_snapshots(now=datetime(2026, 8, 17, 10, 0, tzinfo=ct))
    assert captured["symbols"] is None      # poll_once defaults to collection_symbols()


def test_premarket_poll_skipped_when_eligibility_unknown(monkeypatch):
    """Cold start with no cached eligibility: do NOT guess, do NOT poll."""
    # eligibility cache empty
    assert compute.collect_gex_snapshots(now=datetime(2026, 8, 17, 7, 0, tzinfo=ct)) == 0
```

**Step 2: Run to verify they fail**

**Step 3: Implement**

`gex_due` gates on `mc.in_collection_window(now, eth_eligible=True)` — the widest possible window — and `collect_gex_snapshots` decides the symbol list:

```python
    # Pre-market: poll ONLY the ETH-eligible subset. Polling the full universe
    # would cost ~4,050 calls/day for ~38 symbols that are not quoting.
    if not mc.in_collection_window(now, eth_eligible=False):
        eligible = eth.eligible_symbols(_read_eth_cache(), date_iso=today.isoformat())
        if not eligible:
            # Cold start / unknown eligibility: never guess.
            gc.log.info("Premarket poll skipped — no cached ETH eligibility")
            return 0
        symbols = [s for s in gc.collection_symbols() if s in eligible]
    else:
        symbols = None      # poll_once defaults to the full universe
```

then pass `symbols=symbols` to `poll_once`.

**Step 4: Run** — full `pytest services\options_svc` green.

**Step 5: Commit**

```bash
git commit -am "feat(options): collect premarket GEX for ETH-eligible symbols only"
```

---

## Task D2: End-to-end inertness test

The single most important test in the plan. It proves nothing changes before 2026-08-17.

**Files:**
- Test: `services/options_svc/tests/test_eth_activation.py`

```python
"""The activation guarantee: on 2026-08-14 the app must behave EXACTLY as it
did before this feature existed. If any of these fail, the feature is leaking
across its date gate."""


def test_no_premarket_collection_before_activation():
    for hh, mm in [(6, 30), (7, 0), (7, 59)]:
        due, _ = scheduler.gex_due(datetime(2026, 8, 14, hh, mm, tzinfo=CT), None)
        assert due is False, f"{hh}:{mm}"


def test_collection_starts_at_0800_before_activation():
    due, _ = scheduler.gex_due(datetime(2026, 8, 14, 8, 0, tzinfo=CT), None)
    assert due is True


def test_all_sessions_report_closed_or_regular_before_activation():
    for hh in range(24):
        s = mc.session_at(datetime(2026, 8, 14, hh, 0, tzinfo=CT))
        assert s in (mc.Session.CLOSED, mc.Session.REGULAR), (hh, s)
```

Run, confirm PASS, commit.

---

# Phase E — Display and alert gating

## Task E1: Gate alert emission outside regular hours (hazard H1)

**Files:**
- Modify: `services/options_svc/handlers.py` (`run_flow_alerts`)
- Test: `services/options_svc/tests/test_handlers.py`

Data still accrues; only the **push** is suppressed. The cooldown map is date-scoped, so a signal that crosses in pre-market fires once at the open rather than being lost.

```python
def test_flow_alerts_suppressed_in_premarket_by_default():
    """fire_in_extended_hours defaults to false — no 06:30 CT phone push."""


def test_flow_alerts_fire_in_regular_hours():


def test_flow_alerts_fire_in_premarket_when_config_enables_it(monkeypatch):
```

Implement with `mc.session_at(now) is mc.Session.REGULAR or mc.alerts_fire_in_extended_hours()`.

Also apply the same gate in `webgui/alerts.py:in_market_hours` — replace `_OPEN, _CLOSE` with the calendar call so in-app chimes agree with the push gate.

Commit: `feat(alerts): suppress flow-alert pushes outside regular hours by default`

---

## Task E2: Surface eligibility on the Opportunity Board (hazard H5)

**Files:**
- Modify: `services/options_svc/matrix.py` (`build_rows` — add an `eth_eligible` field)
- Modify: `webgui/pages/options/matrix.py` (badge + column)
- Test: both suites

At 07:00 CT most of the board is legitimately frozen. It must read as **"not eligible"**, never as "stale". Add a small `ETH` badge on eligible rows, Tailwind-first with a fixed class from a finite map (no `.style()` — see the Tailwind-first standard in the root CLAUDE.md).

Commit: `feat(matrix): badge ETH-eligible symbols on the Opportunity Board`

---

## Task E3: Report the current session in the status strips

**Files:**
- Modify: `services/options_svc/compute.py` (`gex_status_view`)
- Modify: `options-scanner/gex_status.py` (`classify_collector_status`)
- Modify: `webgui/pages/options/gamma.py` (`status_strip_text`)

Replace the bare "Collector: starts 8:30" with the named session, using Cboe's vocabulary: `GTH` / `Regular` / `Curb` / `Closed`. Reactive label recolors must use `.classes(remove=…, add=…)` against a finite class set so repeated repaints do not stack conflicting classes.

**Hazard H7 — do not report "stale" for a symbol that has not opened yet.** A class begins its GTH opening rotation only "upon receipt of the first round-lot print in the underlying… and observation of a two-sided bid/ask", so an eligible symbol can legitimately have **no quotes for part or all of GTH**. During GTH, absent data must render as *awaiting open*, never as a collector fault. Add a test:

```python
def test_gth_session_absent_data_is_not_reported_stale():
    """H7: a class opens only once its underlying prints. No quotes at 06:35 CT
    is normal, not a collector failure."""
```

Commit: `feat(gamma): report the named market session in the collector status strip`

---

# Final verification

```bash
# Per folder — never `pytest services` across all of them.
.venv\Scripts\python -m pytest shared\tests -q
.venv\Scripts\python -m pytest shared\contracts -q
.venv\Scripts\python -m pytest shared\notify -q
.venv\Scripts\python -m pytest services\options_svc -q
.venv\Scripts\python -m pytest services\sentiment_svc -q
.venv\Scripts\python -m pytest services\portfolio_svc -q
.venv\Scripts\python -m pytest services\market_svc -q
.venv\Scripts\python -m pytest services\driver_svc -q
cd webgui && ..\.venv\Scripts\python -m pytest . -q && cd ..
cd options-scanner && ..\.venv\Scripts\python -m pytest tests -q && cd ..
.venv\Scripts\python -m ruff check .
```

**Baselines to compare against** (from the root CLAUDE.md; confirm before starting so a pre-existing failure is not mistaken for a regression):
- `options_svc` — 888 green, **plus 2 documented date-relative `test_expected_move` failures**
- `webgui` — 888 green
- `options-scanner` — ~1286 passed / 17 failed (pre-existing baseline)
- `sentiment_svc` 189 · `driver_svc` 218 · `market_svc` 61 · `portfolio_svc` 32

**Restart required:** `options_svc` (collection window + eligibility harvest), `sentiment_svc`, `portfolio_svc`, `market_svc` (RTH predicate), and the webgui (alerts + display).

**Live verification on the first activated trading day — Monday 2026-08-17, ~07:00 CT.** This resolves both remaining design-doc open questions in one pass.

```bash
# 1. Is the underlying quoting in GTH?
curl -s "http://127.0.0.1:8100/quotes?symbols=NVDA" | python -c "import json,sys; q=json.load(sys.stdin)['NVDA']['quote']; print('status:', q['securityStatus'], 'quoteTime:', q['quoteTime'])"

# 2. Are option marks fresh, and does totalVolume accrue GTH prints?
curl -s "http://127.0.0.1:8100/chains?symbol=NVDA&contractType=CALL&strikeCount=2" | python -c "
import json,sys,datetime
d=json.load(sys.stdin)
print('ethOptionEligible:', d.get('ethOptionEligible'))
for exp,strikes in list((d.get('callExpDateMap') or {}).items())[:1]:
    for k,arr in list(strikes.items())[:1]:
        c=arr[0]
        qt=c['quoteTimeInLong']/1000
        print('strike',k,'mark',c['mark'],'last',c['last'],'totalVolume',c['totalVolume'])
        print('quoteTime ->',datetime.datetime.fromtimestamp(qt))
"
```

**Interpretation:**

| Observation | Meaning | Action |
|---|---|---|
| `quoteTimeInLong` is this morning | Schwab serves live GTH marks | Phase D is sound — proceed |
| `quoteTimeInLong` is the prior close | Schwab does **not** serve GTH option quotes | Set `extended_hours_from` to a future date; the whole feature reverts to inert with that one edit |
| `totalVolume` > prior session's 15:20 CT value | GTH prints accrue into cumulative volume (open question #1 → yes) | UOA / crossover detectors will see GTH activity; keep the H1 alert gate on |
| `totalVolume` unchanged from the prior close | GTH volume is excluded, consistent with the not-last-sale-eligible `"v"` condition | Flow detectors are effectively RTH-only in ETH; document and move on — no code change, since H1 already suppresses ETH alerts |

Record the prior session's 15:20 CT `totalVolume` for the same contract **before** 2026-08-17 so the comparison is possible. `last` is expected to be stale in GTH either way — Cboe marks these trades not-last-sale-eligible (hazard H6), which is why every engine path here computes from `mark`, not `last`.

---

# Deliberately out of scope

Recorded so a future session does not "fix" them:

- **Overnight GTH for `$SPX`/`$VIX`** — excluded by decision (API cost), not oversight.
- **`paper_engine` 08:30–15:00 window and the driver's 09:45–15:30 ET entry window** — already the observe-only posture; pinned by the Task B10 guard tests.
- **`guardrails.py`** — no new gate.
- **`SETTLE_HOUR_CT = 15` / `EXPIRY_CLOSE_HOUR_ET = 16`** — **verified correct**, not merely unexamined. Eligible names *do* trade the curb on expiration day, but OCC strikes settlement and the ITM determination on the **16:00 ET NBBO**. Pinned by the Task B10 guard test, which quotes the Cboe FAQ answer.
- **Overnight index GTH for `$SPX`/`$VIX`** (20:15–09:25 ET) — a different session from the equity GTH window this plan covers. Excluded by decision (API cost).
- **`gamma_tool` `hours_left` clamp** — pre-existing post-close behavior, already overridden by `compute._session_expected_move`. Documented in the design doc as H3; changing it would be a regression.
- **`claude-driver/config.py` `MARKET_HOLIDAYS`** — legacy module, consumers deleted 2026-07-08.
