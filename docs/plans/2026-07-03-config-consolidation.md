# Config Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate three classes of duplicated hard-coded config — the market-holiday calendar (copied in 5 files), the canonical index-symbol sets (copied in ~4 files), and the semantic UI color palette (redefined per-page) — by moving each to a single sanctioned source and having every consumer import it.

**Architecture:** Three independent phases, each shippable on its own. Phase A adds `shared/market_calendar.py` (the `shared/` package is the existing cross-tier library, importable by all 3 services + webgui + engines). Phase B adds `shared/symbols.py` for the small canonical index sets only (the large backtest/sector universes stay put — deliberately tiered). Phase C promotes the shared semantic hexes into a single palette block in `webgui/pages/options/theme.py` and points pages/charts at it; **documented intentional color variants are preserved, not merged.** All three are behavior-preserving refactors: rendered output and scheduling behavior must be identical before/after.

**Tech Stack:** Python 3.11 (`tomllib`, `zoneinfo`, `datetime.date`), pytest, NiceGUI + Tailwind arbitrary-value classes, Highcharts option dicts. Run service suites **per folder** from the repo root (never `pytest services` over all of them — it re-triggers the `config`/`scoring` module-name collisions).

**Ground rules (from the root CLAUDE.md):**
- `shared/` has **no `__init__.py`** — it's a namespace package; `from shared.market_calendar import …` resolves once the repo root is on `sys.path`. The 3 services + webgui already run with repo root on path. The one exception is `options-scanner/scanner.py` (legacy standalone CLI) which needs a 3-line `sys.path` bootstrap (Phase A, Task A6), modeled on `services/options_svc/commission.py:9-12`.
- These are **refactors** — do not change any holiday date, any symbol, or any color value. Identical output is the acceptance bar.
- Small commits, conventional prefixes, run the touched suite green before each commit.

---

## Phase A — Single market calendar (holidays in 1 place, not 5)

**Why:** `_HOLIDAYS` (identical 2026–2027 NYSE set, 20 dates) is copy-pasted into 5 files, each carrying a "keep in sync" comment. Yearly maintenance is 5 edits and silently drifts. The `alerts.py` comment even *claims* the service copies omit 2027/Juneteenth — that claim is **stale/false** (all 5 are byte-identical today); the shared module makes the claim structurally true.

**The 5 consumers:**
- `services/options_svc/scheduler.py:32-41` (`_HOLIDAYS`, used by `_is_trading_day`/`_prev_trading_day`/`active_session_date`/`gamma_cleared`)
- `services/sentiment_svc/scheduler.py:33-42` (`_HOLIDAYS`, used by `_is_rth`)
- `services/portfolio_svc/scheduler.py:48-57` (`_HOLIDAYS`, used by `_is_rth`)
- `webgui/alerts.py:20-29` (`_HOLIDAYS` frozenset, used by `is_market_holiday`/`in_market_hours`)
- `options-scanner/scanner.py:111-122` (`Config.HOLIDAYS`, used by `is_trading_day`)

**What moves vs. what stays:** Only the **holiday set + trading-day/next/prev logic** is shared. Each consumer keeps its own **market-hours window** tuples (`_SCAN_START` 08:00–15:15, `_RTH_START` 08:30–15:00, GEX 08:30–15:20) — those legitimately differ and are not duplication.

### Task A1: Create the shared calendar module

**Files:**
- Create: `shared/market_calendar.py`
- Test: `shared/tests/test_market_calendar.py`

**Step 1: Write the failing test**

```python
# shared/tests/test_market_calendar.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

from shared import market_calendar as mc


def test_holidays_is_frozenset_of_dates():
    assert isinstance(mc.HOLIDAYS, frozenset)
    assert date(2026, 6, 19) in mc.HOLIDAYS      # Juneteenth
    assert date(2027, 12, 24) in mc.HOLIDAYS      # Christmas observed 2027
    assert len(mc.HOLIDAYS) == 20                 # 10 per year, 2026 + 2027


def test_is_holiday():
    assert mc.is_holiday(date(2026, 12, 25)) is True
    assert mc.is_holiday(date(2026, 12, 24)) is False


def test_is_trading_day_weekend_and_holiday():
    assert mc.is_trading_day(date(2026, 7, 6)) is True    # Mon
    assert mc.is_trading_day(date(2026, 7, 4)) is False   # Sat
    assert mc.is_trading_day(date(2026, 7, 3)) is False   # Independence Day (obs)
    # accepts a datetime too
    assert mc.is_trading_day(datetime(2026, 7, 6, 10, 0, tzinfo=mc.CT)) is True


def test_prev_and_next_trading_day_skip_weekend_holiday():
    # 2026-07-03 is a holiday (Fri); prev trading day is Thu 07-02
    assert mc.prev_trading_day(date(2026, 7, 6)) == date(2026, 7, 2)
    # next trading day after Thu 07-02 skips the Fri holiday + weekend -> Mon 07-06
    assert mc.next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


def test_market_now_is_ct_aware():
    now = mc.market_now()
    assert now.tzinfo is not None
```

**Step 2: Run it to verify it fails**

Run (from repo root): `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -q`
Expected: FAIL — `ModuleNotFoundError: shared.market_calendar`.

**Step 3: Write the module**

```python
# shared/market_calendar.py
"""Single source of truth for the US-equity (NYSE) market calendar used across
the stack: the full-closure holiday set + trading-day / prev / next helpers.

This REPLACES the per-file ``_HOLIDAYS`` copies previously duplicated in
services/{options,sentiment,portfolio}_svc/scheduler.py, webgui/alerts.py, and
options-scanner/scanner.py. Each consumer keeps its own MARKET-HOURS WINDOW
(scan 08:00-15:15, RTH 08:30-15:00, GEX 08:30-15:20) — only the holiday set and
trading-day logic are shared here.

Observed dates follow NYSE rules (Sat->prior Fri, Sun->following Mon), include
Juneteenth. **Update yearly**: add the next year's dates to ``HOLIDAYS`` — this
ONE edit propagates to every consumer.
"""
from __future__ import annotations

import datetime as _dt
from datetime import date as _date
from zoneinfo import ZoneInfo

# Central Time — the stack's market clock (schedulers gate in CT).
CT = ZoneInfo("America/Chicago")

# NYSE full-closure holidays. frozenset so it is safe to share (immutable).
HOLIDAYS: frozenset[_date] = frozenset({
    # 2026
    _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16), _date(2026, 4, 3),
    _date(2026, 5, 25), _date(2026, 6, 19), _date(2026, 7, 3), _date(2026, 9, 7),
    _date(2026, 11, 26), _date(2026, 12, 25),
    # 2027
    _date(2027, 1, 1), _date(2027, 1, 18), _date(2027, 2, 15), _date(2027, 3, 26),
    _date(2027, 5, 31), _date(2027, 6, 18), _date(2027, 7, 5), _date(2027, 9, 6),
    _date(2027, 11, 25), _date(2027, 12, 24),
})


def _as_date(d) -> _date:
    return d.date() if hasattr(d, "date") else d


def is_holiday(day) -> bool:
    """True if ``day`` (date or datetime) is an NYSE full-closure holiday."""
    return _as_date(day) in HOLIDAYS


def is_trading_day(day) -> bool:
    """True if ``day`` (date or datetime) is a weekday and not a holiday."""
    d = _as_date(day)
    return d.weekday() < 5 and d not in HOLIDAYS


def prev_trading_day(day) -> _date:
    """Most recent trading day strictly BEFORE ``day``."""
    d = _as_date(day) - _dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= _dt.timedelta(days=1)
    return d


def next_trading_day(day) -> _date:
    """Earliest trading day strictly AFTER ``day``."""
    d = _as_date(day) + _dt.timedelta(days=1)
    while not is_trading_day(d):
        d += _dt.timedelta(days=1)
    return d


def market_now() -> _dt.datetime:
    """Current CT-aware datetime (the stack's market clock)."""
    return _dt.datetime.now(CT)
```

**Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\python -m pytest shared\tests\test_market_calendar.py -q`
Expected: PASS (5 tests).

**Step 5: Commit**

```bash
git add shared/market_calendar.py shared/tests/test_market_calendar.py
git commit -m "feat(calendar): add shared/market_calendar single-source holiday set"
```

> Note: if `shared/tests/` does not exist, create it. Confirm it's collected — `shared/contracts` and `shared/bus` already ship tests, so pytest discovery from the repo root works.

### Task A2: Point options_svc scheduler at the shared calendar

**Files:**
- Modify: `services/options_svc/scheduler.py:18-45`

**Step 1:** Add the import near the top (after line 19's `from zoneinfo import ZoneInfo`):

```python
from shared.market_calendar import CT as _CT, HOLIDAYS as _HOLIDAYS, is_trading_day as _cal_is_trading_day
```

**Step 2:** DELETE the local `_HOLIDAYS = {...}` block (lines 32-41) and the `_CT = ZoneInfo("America/Chicago")` assignment on line 26 (now imported). Keep `_SCAN_START`/`_SCAN_END`.

**Step 3:** Replace the body of `_is_trading_day` (lines 44-45) to delegate:

```python
def _is_trading_day(now):
    return _cal_is_trading_day(now)
```

Leave `_prev_trading_day`, `active_session_date`, `gamma_cleared` **unchanged** — they already reference `_HOLIDAYS`, which is now the imported alias (identical membership), so their behavior is preserved. `_market_now()` still returns `datetime.now(_CT)` with the imported `_CT`.

**Step 4:** Run the suite: `.venv\Scripts\python -m pytest services\options_svc -q`
Expected: PASS (was 334 green). Pay attention to `test_scheduler.py` (holiday + `active_session_date`/`gamma_cleared` cases).

**Step 5: Commit**

```bash
git add services/options_svc/scheduler.py
git commit -m "refactor(options_svc): source holidays from shared.market_calendar"
```

### Task A3: Point sentiment_svc scheduler at the shared calendar

**Files:** Modify `services/sentiment_svc/scheduler.py:11,26,33-42,45-50`

- Add import: `from shared.market_calendar import CT as _CT, HOLIDAYS as _HOLIDAYS`
- Delete the local `_CT = ZoneInfo(...)` (line 26) and `_HOLIDAYS = {...}` (lines 33-42). Keep `_RTH_START`/`_RTH_END`/`_OFFHOURS_INTERVAL_MIN`.
- `_is_rth` (lines 45-50) is unchanged — it references `_HOLIDAYS` (now the alias). `_market_now` uses `_CT`.
- Run: `.venv\Scripts\python -m pytest services\sentiment_svc -q` (was 61). Commit `refactor(sentiment_svc): source holidays from shared.market_calendar`.

### Task A4: Point portfolio_svc scheduler at the shared calendar

**Files:** Modify `services/portfolio_svc/scheduler.py:22,42,48-57,60-64`

- Add import: `from shared.market_calendar import CT as _CT, HOLIDAYS as _HOLIDAYS`
- Delete local `_CT` (line 42) and `_HOLIDAYS` (lines 48-57). Keep `_RTH_START`/`_RTH_END`.
- `_is_rth` (lines 60-64) unchanged (references `_HOLIDAYS` alias).
- Run: `.venv\Scripts\python -m pytest services\portfolio_svc -q` (was 32). Commit `refactor(portfolio_svc): source holidays from shared.market_calendar`.

### Task A5: Point webgui/alerts.py at the shared calendar

**Files:** Modify `webgui/alerts.py:7,11,20-34`

- Add import: `from shared.market_calendar import CT as _CAL_CT, HOLIDAYS as _HOLIDAYS, is_holiday`
- Delete the local `_HOLIDAYS = frozenset({...})` (lines 20-29) **and its stale comment** (lines 14-19 claim the service copies omit 2027/Juneteenth — false; replace with a one-line pointer to `shared.market_calendar`).
- `CT` on line 11 (`ZoneInfo("America/Chicago")`) — keep the name `CT` (used by `in_market_hours`), but set it from the shared module: `CT = _CAL_CT`. Keep `_OPEN`/`_CLOSE`.
- Replace `is_market_holiday` (lines 32-34) body with `return is_holiday(day)`. `in_market_hours` (line 77) references `_HOLIDAYS` (alias) — unchanged.
- **Import-path note:** `webgui/conftest.py` puts the repo root on `sys.path`; `main.py` runs webgui with repo root on path. Verify `from shared.market_calendar import …` resolves in the webgui test context (Task A5 test run will confirm).
- Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_alerts.py -q` then the full `..\.venv\Scripts\python -m pytest -q` (was ~676). Commit `refactor(webgui): source holidays from shared.market_calendar; drop stale sync comment`.

### Task A6: Point the legacy scanner.py CLI at the shared calendar

**Files:** Modify `options-scanner/scanner.py:32-34,111-122`

This is the one consumer without repo-root on `sys.path`. Add the bootstrap (model on `services/options_svc/commission.py:9-12`) near the top imports (after line 34):

```python
import pathlib as _pathlib
sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))  # repo root
from shared.market_calendar import HOLIDAYS as _SHARED_HOLIDAYS  # noqa: E402
```

Then replace the `Config.HOLIDAYS = {...}` literal (lines 111-122) with:

```python
    # Sourced from shared/market_calendar.py (single source; update yearly there).
    HOLIDAYS = _SHARED_HOLIDAYS
```

`is_trading_day`/`is_market_hours` reference `Config.HOLIDAYS` — unchanged.

Run: `cd options-scanner && python -m pytest tests -q --tb=no` (baseline: 667 passed / 2 known-fail — do not fix the 2 `TestEarningsAvoidance`). Commit `refactor(scanner): source holidays from shared.market_calendar (+repo-root bootstrap)`.

### Task A7: Cross-consumer drift guard

**Files:** Modify `shared/tests/test_market_calendar.py`

Add a test that pins the canonical set and asserts every live consumer now shares the SAME object (so drift is structurally impossible):

```python
def test_all_consumers_share_the_same_holiday_set():
    from services.options_svc import scheduler as opt
    from services.sentiment_svc import scheduler as sen
    from services.portfolio_svc import scheduler as pf
    from webgui import alerts
    assert opt._HOLIDAYS is mc.HOLIDAYS
    assert sen._HOLIDAYS is mc.HOLIDAYS
    assert pf._HOLIDAYS is mc.HOLIDAYS
    assert alerts._HOLIDAYS is mc.HOLIDAYS
```

> **Caveat:** importing all four service schedulers in ONE test process can re-trigger the documented `config`/`scoring` cross-app collisions. If this test fails to import cleanly, split it into per-consumer identity checks placed in each service's own suite instead (e.g. `services/options_svc/tests/test_scheduler.py::test_holidays_are_shared`), and keep only the `len(HOLIDAYS)==20` canonical pin in `shared/tests`. Prefer the split if there's any import friction — it matches the repo's per-folder test discipline.

Run the relevant suite(s); commit `test(calendar): pin canonical holiday set + consumer identity`.

**Phase A done when:** all 5 consumers import `shared.market_calendar`, no `date(2026,...)` holiday literal remains outside it (grep: `grep -rn "date(2026, 1, 1)" services webgui options-scanner` → only test fixtures), and every touched suite is green.

---

## Phase B — Tiered canonical symbols (index sets in 1 place)

**Why:** The canonical index symbols drift across files. Scope is **tiered** (per the decision): centralize only the small overlapping index sets + index-root classification. The live scan universe (`Top 20.xlsx` ∪ base) is already file-sourced — leave it. The 78-name backtest `UNIVERSE_SECTOR` and 140-symbol sector maps are domain-specific — leave them.

**Consumers to converge:**
- `options-scanner/gex_collector.py:26` — `SYMBOLS = ["$SPX", "$VIX", "SPY", "QQQ"]` (collection base)
- `options-scanner/watchlist.py:24` — `BASE_SYMBOLS = ["$SPX", "SPY", "QQQ"]` (scan base; no `$VIX`)
- `options-scanner/scanner_engine.py:276` — `INDEX_SYMBOLS = frozenset({"$SPX", "SPY", "QQQ"})` (dedup gate)
- `options-scanner/scanner.py:64-65` — `Config.SYMBOLS = ["$SPX", "SPY", "QQQ"]`, `VIX_SYMBOL = "$VIX"`
- `options-scanner/commissions.py:32` **and** `services/options_svc/commission.py:31` — duplicated `_INDEX_ROOTS = {"SPX","VIX","OEX","NDX","RUT","XSP","DJX"}` + `is_index_symbol`

### Task B1: Create the shared symbols module

**Files:**
- Create: `shared/symbols.py`
- Test: `shared/tests/test_symbols.py`

**Step 1: Write the failing test**

```python
# shared/tests/test_symbols.py
from shared import symbols as sym


def test_canonical_sets():
    assert sym.SPX == "$SPX"
    assert sym.VIX == "$VIX"
    assert sym.SCAN_BASE == ("$SPX", "SPY", "QQQ")           # order-preserving, no $VIX
    assert sym.COLLECTION_BASE == ("$SPX", "$VIX", "SPY", "QQQ")  # incl $VIX, $VIX 2nd
    assert sym.INDEX_SYMBOLS == frozenset({"$SPX", "SPY", "QQQ"})


def test_index_roots_and_classification():
    assert "NDX" in sym.INDEX_ROOTS
    assert sym.is_index_symbol("$SPX") is True
    assert sym.is_index_symbol("spx") is True     # case + $ insensitive
    assert sym.is_index_symbol("AAPL") is False
    assert sym.is_index_symbol("") is False
    assert sym.is_index_symbol(None) is False
```

**Step 2: Run to verify fail.** `.venv\Scripts\python -m pytest shared\tests\test_symbols.py -q` → `ModuleNotFoundError`.

**Step 3: Write the module**

```python
# shared/symbols.py
"""Single source of truth for the small CANONICAL index-symbol sets used across
apps (the parts that were drifting). Deliberately TIERED: this holds only the
benchmark-index sets + index-root classification. The LIVE scan universe stays
file-sourced (options-scanner/data/Top 20.xlsx via watchlist.py), and the large
backtest / sector universes stay in their own modules — they are different
concerns, not duplication.
"""
from __future__ import annotations

# Benchmark index tickers (Schwab uses the leading '$' for cash indices).
SPX = "$SPX"
VIX = "$VIX"
SPY = "SPY"
QQQ = "QQQ"

# Scan base — the three benchmark indices the scanner always covers (no $VIX,
# which is a context quote, not a scan target). Order-preserving tuple.
SCAN_BASE: tuple[str, ...] = (SPX, SPY, QQQ)

# GEX collection base — the scan base plus $VIX (listed 2nd so it's always present
# even when absent from the watchlist). Order matters for collection dedup.
COLLECTION_BASE: tuple[str, ...] = (SPX, VIX, SPY, QQQ)

# Underlyings treated as "index" for the directional-signal dedup gate.
INDEX_SYMBOLS: frozenset[str] = frozenset({SPX, SPY, QQQ})

# Roots that carry the index OPTION commission rate (+ exchange passthrough).
INDEX_ROOTS: frozenset[str] = frozenset({"SPX", "VIX", "OEX", "NDX", "RUT", "XSP", "DJX"})


def is_index_symbol(symbol: str | None) -> bool:
    """True if ``symbol`` (with or without a leading '$', any case) is an index
    root — used for commission routing."""
    if not symbol:
        return False
    return str(symbol).lstrip("$").upper() in INDEX_ROOTS
```

**Step 4: Run → PASS. Step 5: Commit** `feat(symbols): add shared/symbols canonical index sets`.

### Task B2: Converge the commission index-root duplication

Two files define `_INDEX_ROOTS` + `is_index_symbol` identically. Point both at `shared.symbols`.

**Files:**
- Modify `services/options_svc/commission.py:30-37` — it already bootstraps repo root (lines 9-12). Replace the local `_INDEX_ROOTS` + `is_index_symbol` with:
  ```python
  from shared.symbols import INDEX_ROOTS as _INDEX_ROOTS, is_index_symbol  # noqa: E402
  ```
  (Keep the name `is_index_symbol` exported — `_option_rate` calls it locally. `INDEX_ROOTS` re-exported as `_INDEX_ROOTS` for any external reference.)
- Modify `options-scanner/commissions.py:31-49` — add a repo-root bootstrap if not already present (check top of file; commissions.py already imports `repo_paths` per the audit, so repo root is on path), then replace its `_INDEX_ROOTS`/`is_index_symbol` the same way. **Preserve** the existing `_DEFAULT_*` rate fallbacks — those are unrelated.

Run BOTH `.venv\Scripts\python -m pytest services\options_svc -q` and `cd options-scanner && python -m pytest tests\test_commission* tests\test_commissions* -q` (adjust to actual test filenames). Commit `refactor(commission): source index roots from shared.symbols (de-dup)`.

### Task B3: Converge the scanner/collector base-symbol sets

**Files:**
- `options-scanner/watchlist.py:24` → `from shared.symbols import SCAN_BASE; BASE_SYMBOLS = list(SCAN_BASE)` (needs repo-root bootstrap at top — watchlist.py currently imports only `logging`/`pathlib`, so add the 2-line `sys.path.insert` + import). Keep `BASE_SYMBOLS` a `list` (it's returned via `list(BASE_SYMBOLS)` and mutated-by-copy — identical behavior).
- `options-scanner/gex_collector.py:26` → `SYMBOLS = list(COLLECTION_BASE)` with the shared import (verify gex_collector already reaches repo root; if not, add the bootstrap).
- `options-scanner/scanner_engine.py:276` → `INDEX_SYMBOLS = INDEX_SYMBOLS` from shared (import as-is; it's already a frozenset with identical members).
- `options-scanner/scanner.py:64-65` → `Config.SYMBOLS = list(SCAN_BASE)`, `VIX_SYMBOL = VIX` (reuse the Phase-A bootstrap added in Task A6 — repo root already on path).

**Behavior check:** `SCAN_BASE`/`COLLECTION_BASE` are tuples with the **exact same order** as the current literals, so `collection_symbols()` dedup order and the watchlist union are unchanged.

Run `cd options-scanner && python -m pytest tests -q --tb=no` (667/2-known-fail baseline) + `.venv\Scripts\python -m pytest services\options_svc -q`. Commit `refactor(scanner): source base/index symbols from shared.symbols`.

**Phase B done when:** the index-root set and the base/index symbol lists exist once (in `shared/symbols.py`); grep `grep -rn '"\$SPX", "SPY", "QQQ"' options-scanner services` returns only `shared/symbols.py` + tests; suites green. The `Top 20.xlsx` watchlist mechanism and the 78-name/140-symbol universes are intentionally untouched.

---

## Phase C — Single semantic UI palette (Level A: edit-one-file theming)

**Why:** `theme.py` has a clean token vocabulary, but ~10 pages redefine the same semantic hexes (`#66bb6a` green, `#ef5350` red, `#ffa726` amber, `#ffd54f` yellow, `#42a5f5` blue, `#3fb6c7` cyan, `#9e9e9e`/`#bdbdbd`/`#888888` neutrals). "Re-theme the app" today means editing many files. Level A promotes these to named constants in one place; pages import them. (Level B — runtime CSS-variable theming — is explicitly out of scope.)

**Hard constraint — preserve documented intentional variants (do NOT merge these):**
- `trade.py:42-44` verdict `#2e7d32`/`#f9a825`/`#c62828` — CLAUDE.md says "deliberately DARKER, not shared."
- `simulator.py:61-62` `PNL_GREEN=#34d399`/`PNL_RED=#f87171` — chart-specific shades.
- `expected_move.py:14` `UP_COLOR=#26a69a` — candlestick teal.
- `portfolio.py:24-25` `#2e9e6b`/`#e24b4a` — status-bar-specific.
- `driver.py` grade colors `#1D9E75`/`#185FA5`/`#BA7517`/`#E24B4A`.
- `gamma.py:31-33` chart chrome `#1b1b1b`/`#333333`/`#e6e6e6`, `PRICE_LINE=#f5f5f5`, `HEATMAP_SEP=#4d4d4d`.

These stay as page-local named constants (they're genuinely distinct roles). Level A centralizes only the **shared** semantic hexes.

### Task C1: Add the canonical semantic palette to theme.py

**Files:**
- Modify: `webgui/pages/options/theme.py` (add a `SEMANTIC PALETTE` block near the `TXT_*` section, ~line 98)
- Test: `webgui/tests/test_theme.py` (add cases)

**Step 1: Write the failing test**

```python
# add to webgui/tests/test_theme.py
from pages.options import theme


def test_semantic_palette_raw_hexes():
    p = theme.PALETTE
    assert p["green"] == "#66bb6a"
    assert p["red"] == "#ef5350"
    assert p["amber"] == "#ffa726"
    assert p["yellow"] == "#ffd54f"
    assert p["blue"] == "#42a5f5"
    assert p["cyan"] == "#3fb6c7"
    assert p["flat"] == "#9e9e9e"
    assert p["neutral"] == "#bdbdbd"
    assert p["muted"] == "#888888"


def test_existing_txt_tokens_reference_palette():
    # TXT_POS/NEG/WARN must still equal their historical arbitrary-value classes
    assert theme.TXT_POS == "text-[#66bb6a]"
    assert theme.TXT_NEG == "text-[#ef5350]"
    assert theme.TXT_WARN == "text-[#ffa726]"
```

**Step 2: Run → FAIL** (`AttributeError: PALETTE`).
Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_theme.py -q`

**Step 3: Add the palette block** to `theme.py` (above the `TXT_POS` definitions on line ~103):

```python
# ---------------------------------------------------------------------------
# SEMANTIC PALETTE — the single source for the app's shared semantic colors.
# Pages/charts that previously redefined these hexes now import from here, so a
# re-theme is a ONE-FILE edit. Raw hexes (for Highcharts option dicts + ui.html
# fragments) + helper builders for the two other forms colors are consumed in:
# Tailwind arbitrary-value classes and (r,g,b) tuples (svg.py / gauge.py).
# NOTE: intentional page-local variants (trade verdict darks, simulator chart
# shades, gamma chart chrome, driver grades, portfolio status) are DELIBERATELY
# NOT in here — see the Phase C plan.
# ---------------------------------------------------------------------------
PALETTE = {
    "green":   "#66bb6a",   # positive / bullish
    "red":     "#ef5350",   # negative / bearish
    "amber":   "#ffa726",   # caution / warning
    "yellow":  "#ffd54f",   # spot / neutral-highlight
    "blue":    "#42a5f5",   # informational / flip
    "cyan":    "#3fb6c7",   # sentiment cyan
    "flat":    "#9e9e9e",   # flat / no-change
    "neutral": "#bdbdbd",   # neutral text
    "muted":   "#888888",   # muted / disabled
}


def hex_of(name: str) -> str:
    """Raw hex for a palette color (use in Highcharts dicts / ui.html)."""
    return PALETTE[name]


def txt(name: str) -> str:
    """Tailwind text-color arbitrary class for a palette color."""
    return f"text-[{PALETTE[name]}]"


def bg(name: str) -> str:
    """Tailwind bg-color arbitrary class for a palette color."""
    return f"bg-[{PALETTE[name]}]"


def rgb(name: str) -> tuple[int, int, int]:
    """(r, g, b) tuple for a palette color (use in svg.py / gauge.py)."""
    h = PALETTE[name].lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
```

Then rewrite the existing `TXT_*` lines to derive from the palette (values byte-identical):

```python
TXT_POS = txt("green")       # text-[#66bb6a]
TXT_WARN = txt("amber")      # text-[#ffa726]
TXT_NEG = txt("red")         # text-[#ef5350]
TXT_NEUTRAL = txt("neutral") # text-[#bdbdbd]
STATE_TEXT_CLASSES = " ".join([TXT_POS, TXT_WARN, TXT_NEG, TXT_NEUTRAL])
```

**Step 4: Run → PASS.** **Step 5: Commit** `feat(theme): add canonical SEMANTIC PALETTE + txt/bg/rgb/hex helpers`.

### Task C2: Migrate the sentiment + rotation pages to the palette

**Files:** `webgui/pages/sentiment.py:29-48`, `webgui/pages/sentiment_rotation.py:23-37`

Both define `CLR_GREEN/RED/YELLOW/FLAT/CYAN` + `TXT_*`/`BG_*` classes with the exact palette hexes. Replace the literal blocks with palette-derived definitions (import `from pages.options import theme`):

```python
CLR_GREEN, CLR_RED = theme.hex_of("green"), theme.hex_of("red")
CLR_YELLOW, CLR_FLAT, CLR_CYAN = theme.hex_of("yellow"), theme.hex_of("flat"), theme.hex_of("cyan")
TXT_G, TXT_R = theme.txt("green"), theme.txt("red")
TXT_Y, TXT_FLAT, TXT_CY = theme.txt("yellow"), theme.txt("flat"), theme.txt("cyan")
BG_G, BG_R, BG_Y = theme.bg("green"), theme.bg("red"), theme.bg("yellow")   # sentiment.py only
```

**Behavior check:** every resulting string is byte-identical to today's literal (the tests below pin this). The reactive `remove/add` recolor sets that reference these names keep working unchanged.

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests\test_sentiment.py tests\test_sentiment_rotation.py -q`. Commit `refactor(sentiment): derive semantic colors from theme.PALETTE`.

### Task C3: Migrate the remaining shared-hex pages

Apply the same substitution (literal hex → `theme.hex_of(...)`, and `text-[#..]`/`bg-[#..]` → `theme.txt(...)`/`theme.bg(...)`, and `(r,g,b)` tuples → `theme.rgb(...)`) **only for the shared semantic hexes** in each file. Leave documented variants alone.

Per-file map (shared hexes to convert → leave):
- `options/gamma.py:22-24` — `POS_COLOR`→`hex_of("green")`, `NEG_COLOR`→`hex_of("red")`, `SPOT_COLOR`→`hex_of("yellow")`, `FLIP_COLOR`→`hex_of("blue")` (line 27). **Leave** `PRICE_LINE`/`HEATMAP_SEP`/`WALL_COLOR`/`DARK_BG`/`GRID`/`FONT` (chart chrome).
- `options/rescue.py:20-28` — `HEAT_GREEN`→green, `HEAT_RED`/`CASH_RED`→red, `HEAT_AMBER`→amber, `CASH_GREEN`→green, `CASH_NEUTRAL`→flat. **Leave** `HEAT_ORANGE=#ff7043` (unique 4th heat zone).
- `options/svg.py:8-11` — `RED`→`rgb("red")`, `AMBER`→`rgb("amber")`, `BLUE`→`rgb("blue")`, `GREEN`→`rgb("green")`.
- `pages/gauge.py:11-13` — `_RED`→`rgb("red")`, `_YELLOW`→`rgb("yellow")`, `_GREEN`→`rgb("green")`.
- `options/simulator.py:52-54,195-196` — `SPOT_COLOR`→yellow, `TARGET_COLOR`/`BASE_COLOR`/`GREEK_COLOR`→blue, `SHOCK_COLOR`→amber, `CURSOR_COLOR`→red, `PRICE_COLOR`→green. **Leave** `PNL_GREEN=#34d399`/`PNL_RED=#f87171` (chart-specific shades).
- `options/expected_move.py:15-19` — `DOWN_COLOR`/`EM_DOWN_COLOR`→red, `EM_UP_COLOR`→green. **Leave** `UP_COLOR=#26a69a` (candlestick teal), `PUT_COLOR=#ef9a9a`/`CALL_COLOR=#90caf9` (lighter leg tints — unique).

Do this as **one file per commit** (six small commits), running that page's test file each time (`test_gamma.py`, `test_rescue.py`, `test_svg.py`/`test_gauge.py`, `test_simulator.py`, `test_expected_move.py`). Commit message pattern: `refactor(<page>): derive semantic colors from theme.PALETTE`.

> `driver.py`, `portfolio.py`, `trade.py` are **NOT** migrated — their colors are all documented intentional variants. Leave them as-is (optionally add a one-line comment noting the deliberate divergence).

### Task C4: Full webgui regression + visual smoke

- Run the whole suite: `cd webgui && ..\.venv\Scripts\python -m pytest -q` (was ~676; the `test_no_inline_style.py` guard must stay green — we added no `.style()`).
- Browser smoke via the preview tool: start `webgui`, screenshot Sentiment, Gamma, Simulator, Rescue, Expected-Move; confirm colors are visually unchanged (this is a no-op refactor, so any color shift is a bug — likely a fat-fingered palette key).
- Commit any fixes; final commit `test(theme): full webgui green after palette migration`.

**Phase C done when:** every shared semantic hex is defined once in `theme.PALETTE`; grep `grep -rn "#66bb6a\|#ef5350\|#ffa726" webgui/pages` returns only `theme.py` + the documented-variant files + tests; the app renders identically.

---

## Sequencing & risk

- **Phases are independent** — ship A, B, C in any order or in parallel branches. A is the highest-value / lowest-risk (fixes real yearly-maintenance drift). C is the largest surface but purely cosmetic-refactor.
- **Biggest risk:** an import that can't reach `shared` (Phase A/B on the options-scanner side) — mitigated by the `sys.path` bootstrap pattern already proven in `commission.py`. Run the options-scanner suite after each of A6/B2/B3.
- **Second risk:** the cross-consumer identity test in A7 tripping the multi-service import collision — mitigated by the split-into-per-folder fallback noted there.
- **Do not** batch-edit with sed across colors — the intentional-variant exclusions in Phase C require per-line judgment.

## After each phase
Update the root `CLAUDE.md`: Phase A → note the 5-file holiday duplication is retired (single source `shared/market_calendar.py`); Phase B → note `shared/symbols.py`; Phase C → update the "App theme" section to point at `theme.PALETTE` as the semantic-color source.
