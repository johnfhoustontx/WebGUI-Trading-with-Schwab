# GEX Watchlist Collection + Gamma Symbol Dropdown Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand intraday GEX history collection to the full scan watchlist (Top 20.xlsx) on a 2-minute cadence, and turn the Gamma page's Symbol field into a dropdown (default `$SPX`, excluding `$VIX`) populated from the collected universe so Explain covers every tracked symbol.

**Architecture:** The collector (`options-scanner/gex_collector.py`) gains a dynamic `collection_symbols()` (index base ∪ `watchlist.get_scan_symbols()`) that `poll_once` iterates; the options service (Tier 2) already reuses `poll_once` verbatim and publishes the dropdown universe over Redis (`cache:options:gamma_symbols`). The Gamma page (Tier 3) reads that cache to build a `ui.select`. The poll interval drops 5→2 min everywhere it's referenced, with the staleness threshold kept at 2× the interval.

**Tech Stack:** Python 3.11, pytest, NiceGUI, Redis (shared.bus / bus_client), openpyxl (watchlist), Plotly.

---

## Background / key facts for the implementer

- Run service tests **per folder** from the repo root (never `pytest services` over
  all of them — cross-app module-name collisions). Commands used below:
  - `cd options-scanner ; python -m pytest tests/test_<x>.py -q`
  - From repo root: `.venv\Scripts\python -m pytest services\options_svc -q`
  - `cd webgui ; ..\.venv\Scripts\python -m pytest -q`
- `options-scanner` has ~2 pre-existing date-relative test failures — not ours; do
  not "fix" them.
- `watchlist.get_scan_symbols()` returns e.g.
  `['$SPX','SPY','QQQ','NVDA',...]` (21 symbols), order-preserving, mtime-cached,
  fallback to `['$SPX','SPY','QQQ']` on read failure.
- `gex_collector.SYMBOLS = ["$SPX","$VIX","SPY","QQQ"]` — keep as the index base
  (supplies `$VIX`, absent from the watchlist).
- The options service's `compute.collect_gex_snapshots()` calls
  `gc.poll_once(...)` with **no** `symbols` arg, so the dynamic default flows
  through automatically.
- Tier-3 rule: `webgui/pages/options/gamma.py` may import only `nicegui` +
  `bus_client`/`shared.*` — NOT `watchlist`. The dropdown list must come from the
  bus.

---

## Task 1: `collection_symbols()` in the collector

**Files:**
- Modify: `options-scanner/gex_collector.py` (near `SYMBOLS`, ~line 26)
- Test: `options-scanner/tests/test_gex_collector_symbols.py` (create)

**Step 1: Write the failing test**

```python
"""Tests for gex_collector.collection_symbols (dynamic watchlist union)."""
import gex_collector as gc


def test_collection_symbols_unions_base_and_watchlist(monkeypatch):
    monkeypatch.setattr(gc, "SYMBOLS", ["$SPX", "$VIX", "SPY", "QQQ"])
    # Patch the watchlist accessor the function imports.
    import watchlist
    monkeypatch.setattr(watchlist, "get_scan_symbols",
                        lambda: ["$SPX", "SPY", "QQQ", "NVDA", "TSLA"])
    out = gc.collection_symbols()
    # Base first (so $VIX is retained), watchlist extras appended, deduped.
    assert out == ["$SPX", "$VIX", "SPY", "QQQ", "NVDA", "TSLA"]


def test_collection_symbols_falls_back_to_base_on_error(monkeypatch):
    monkeypatch.setattr(gc, "SYMBOLS", ["$SPX", "$VIX", "SPY", "QQQ"])
    import watchlist

    def _boom():
        raise RuntimeError("watchlist unreadable")

    monkeypatch.setattr(watchlist, "get_scan_symbols", _boom)
    assert gc.collection_symbols() == ["$SPX", "$VIX", "SPY", "QQQ"]
```

**Step 2: Run to verify it fails**

Run: `cd options-scanner ; python -m pytest tests/test_gex_collector_symbols.py -q`
Expected: FAIL — `AttributeError: module 'gex_collector' has no attribute 'collection_symbols'`.

**Step 3: Implement**

In `options-scanner/gex_collector.py`, just below the `SYMBOLS` constant:

```python
def collection_symbols():
    """Dynamic collection universe: the index base (SYMBOLS) unioned with the
    scan watchlist (BASE ∪ Top 20.xlsx), deduped + order-preserving.

    SYMBOLS is listed FIRST so ``$VIX`` (absent from the watchlist) is always
    retained. Defensive: any watchlist import/read failure falls back to the
    static SYMBOLS so a poll never crashes over the watchlist."""
    try:
        import watchlist
        extra = watchlist.get_scan_symbols()
    except Exception:
        log.warning("watchlist unavailable; collecting index base only",
                    exc_info=True)
        return list(SYMBOLS)
    out, seen = [], set()
    for s in list(SYMBOLS) + list(extra or []):
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
```

**Step 4: Run to verify it passes**

Run: `cd options-scanner ; python -m pytest tests/test_gex_collector_symbols.py -q`
Expected: PASS (2 passed).

**Step 5: Commit**

```bash
git add options-scanner/gex_collector.py options-scanner/tests/test_gex_collector_symbols.py
git commit -m "feat(options-scanner): dynamic GEX collection_symbols (index base + watchlist)"
```

---

## Task 2: `poll_once` iterates the dynamic universe

**Files:**
- Modify: `options-scanner/gex_collector.py` `poll_once` (~line 146-156)
- Test: `options-scanner/tests/test_gex_collector_symbols.py` (append)

**Step 1: Write the failing test**

```python
def test_poll_once_defaults_to_collection_symbols(monkeypatch):
    seen = []

    class _Client:
        class Options:
            class ContractType:
                ALL = "ALL"

        def get_option_chain(self, symbol, **kw):
            seen.append(symbol)
            class _R:
                status_code = 500   # non-200 → skipped after recording the call
                def json(self):
                    return None
            return _R()

    monkeypatch.setattr(gc, "collection_symbols",
                        lambda: ["$SPX", "NVDA", "TSLA"])
    # Term poll is SPX-only and also hits the client; stub it out to isolate the loop.
    monkeypatch.setattr(gc, "poll_term_once", lambda *a, **k: None)

    class _Conn:
        def commit(self):
            pass

    gc.poll_once(_Client(), object(), _Conn())
    assert seen == ["$SPX", "NVDA", "TSLA"]


def test_poll_once_honors_explicit_symbols(monkeypatch):
    seen = []

    class _Client:
        class Options:
            class ContractType:
                ALL = "ALL"

        def get_option_chain(self, symbol, **kw):
            seen.append(symbol)
            class _R:
                status_code = 500
                def json(self):
                    return None
            return _R()

    monkeypatch.setattr(gc, "poll_term_once", lambda *a, **k: None)
    # collection_symbols must NOT be consulted when symbols is passed explicitly.
    monkeypatch.setattr(gc, "collection_symbols",
                        lambda: (_ for _ in ()).throw(AssertionError("should not be called")))

    class _Conn:
        def commit(self):
            pass

    gc.poll_once(_Client(), object(), _Conn(), symbols=["SPY"])
    assert seen == ["SPY"]
```

**Step 2: Run to verify it fails**

Run: `cd options-scanner ; python -m pytest tests/test_gex_collector_symbols.py -q`
Expected: FAIL — `poll_once()` got an unexpected keyword `symbols` (or iterates the old `SYMBOLS`).

**Step 3: Implement**

Change the `poll_once` signature and the loop source:

```python
def poll_once(client, engine, conn, lock=None, symbols=None) -> None:
    """Fetch + store one snapshot per symbol. Per-symbol exceptions are logged,
    not propagated, so one bad symbol doesn't kill the whole poll.

    ``symbols`` defaults to ``collection_symbols()`` (the index base unioned with
    the scan watchlist) when None; pass an explicit list to override (tests)."""
    if symbols is None:
        symbols = collection_symbols()
    now = datetime.now(TZ)
    ...
    for symbol in symbols:        # was: for symbol in SYMBOLS
        ...
```

(Leave the term-structure poll at the end unchanged — it is SPX-only.)

**Step 4: Run to verify it passes**

Run: `cd options-scanner ; python -m pytest tests/test_gex_collector_symbols.py -q`
Expected: PASS (4 passed).

**Step 5: Run the existing collector suite for regressions**

Run: `cd options-scanner ; python -m pytest tests/test_gex_collector.py -q`
(if it exists; otherwise skip). Expected: no NEW failures.

**Step 6: Commit**

```bash
git add options-scanner/gex_collector.py options-scanner/tests/test_gex_collector_symbols.py
git commit -m "feat(options-scanner): poll_once iterates dynamic collection_symbols"
```

---

## Task 3: Drop the poll interval 5→2 min + staleness 600→240

**Files:**
- Modify: `options-scanner/gex_collector.py:27` (`POLL_INTERVAL_MIN`)
- Modify: `options-scanner/gex_status.py:10` (`STALE_AFTER_SEC`)
- Modify: `services/options_svc/scheduler.py:70` (`_GEX_INTERVAL_MIN`)

**Step 1: Edit the constants**

- `gex_collector.py`: `POLL_INTERVAL_MIN = 2` (update the `# == 600 at
  POLL_INTERVAL_MIN=5` comment on `LOCK_TTL_SEC` to `# == 240 at
  POLL_INTERVAL_MIN=2`).
- `gex_status.py`: `STALE_AFTER_SEC = 240  # 2 x poll interval (2 min)`.
- `scheduler.py`: `_GEX_INTERVAL_MIN = 2    # gex_collector.POLL_INTERVAL_MIN`.

**Step 2: Run the affected suites**

Run: `cd options-scanner ; python -m pytest tests/ -q --tb=short`
Expected: existing tests that hardcoded 5-min boundaries or `STALE_AFTER_SEC=600`
will FAIL — note them; they are fixed in Task 4. No other regressions.

**Step 3: Commit** (after Task 4 green — defer; or commit constants + fixes together in Task 4)

---

## Task 4: Recalibrate boundary/staleness tests to 2-min cadence

**Files:**
- Modify: `services/options_svc/tests/test_compute.py` (`test_gex_status_view_in_window`,
  `test_gex_next_scan_boundaries`, and any 5-min boundary asserts ~line 1132-1175+)
- Possibly modify: `options-scanner/tests/test_gex_status.py` (if it asserts
  `STALE_AFTER_SEC == 600` or a 600-based boundary) — grep first.
- Possibly modify: `options-scanner/tests/` next_boundary tests — grep first.

**Step 1: Find every test that encodes the old cadence**

Run from repo root:
```
grep -rn "10:05\|STALE_AFTER_SEC\|600\|minute //\|next_boundary\|POLL_INTERVAL_MIN" \
  options-scanner/tests services/options_svc/tests
```
Inspect each hit; update the expected values for a 2-min interval. Examples:
- `test_gex_status_view_in_window`: next boundary strictly after 10:02 → **10:04**
  (was 10:05). Update `assert out["next_scan"] == "10:04 AM"`.
- `test_gex_next_scan_boundaries`: recompute each expected boundary on 2-min steps.
- Any `STALE_AFTER_SEC == 600` → `== 240`; any age threshold using 600 → 240.

**Step 2: Run the suites to verify green**

Run: `.venv\Scripts\python -m pytest services\options_svc -q`
Run: `cd options-scanner ; python -m pytest tests/ -q --tb=short`
Expected: green except the ~2 known pre-existing options-scanner failures.

**Step 3: Commit**

```bash
git add options-scanner/gex_collector.py options-scanner/gex_status.py \
  services/options_svc/scheduler.py options-scanner/tests services/options_svc/tests
git commit -m "feat: drop GEX poll interval 5->2min; staleness 600->240s; recalibrate tests"
```

---

## Task 5: `collect_gex_snapshots` returns the dynamic count

**Files:**
- Modify: `services/options_svc/compute.py:640-678` (`collect_gex_snapshots`)
- Modify: `services/options_svc/tests/test_compute.py:1064-1110`
  (`_fake_gex_modules`, `test_collect_gex_snapshots_polls_with_proxy_client`)

**Step 1: Update the failing test first (TDD)**

In `_fake_gex_modules`, add `collection_symbols` to the fake namespace:

```python
    fake_gc = _types.SimpleNamespace(
        LOCK_PATH="LOCK", SYMBOLS=["$SPX", "SPY"],
        collection_symbols=lambda: ["$SPX", "SPY", "NVDA"],
        acquire_collector_lock=lambda path, **kw: lock_ok,
        ...
    )
```

In `test_collect_gex_snapshots_polls_with_proxy_client`, change the count assert:

```python
    assert n == 3                                         # len(collection_symbols())
```

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py::test_collect_gex_snapshots_polls_with_proxy_client -q`
Expected: FAIL — returns 2 (`len(gc.SYMBOLS)`), expected 3.

**Step 3: Implement**

In `compute.collect_gex_snapshots`, change the final line and docstring ref:

```python
    return len(gc.collection_symbols())
```
(Update the docstring "Returns ``len(gex_collector.SYMBOLS)``" →
"Returns ``len(gex_collector.collection_symbols())``".)

**Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k collect_gex -q`
Expected: PASS (both collect_gex tests).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(options_svc): collect_gex_snapshots counts dynamic collection_symbols"
```

---

## Task 6: `compute.gamma_symbol_options()` (dropdown universe, no $VIX)

**Files:**
- Modify: `services/options_svc/compute.py` (add near the Gamma section, ~line 815)
- Test: `services/options_svc/tests/test_compute.py` (append)

**Step 1: Write the failing test**

```python
def test_gamma_symbol_options_excludes_vix_spx_first(monkeypatch):
    import sys as _sys
    import types as _types
    fake_gc = _types.SimpleNamespace(
        collection_symbols=lambda: ["$SPX", "$VIX", "SPY", "QQQ", "NVDA"])
    monkeypatch.setitem(_sys.modules, "gex_collector", fake_gc)
    out = compute.gamma_symbol_options()
    assert out[0] == "$SPX"
    assert "$VIX" not in out
    assert out == ["$SPX", "SPY", "QQQ", "NVDA"]


def test_gamma_symbol_options_defensive(monkeypatch):
    import sys as _sys
    import types as _types
    fake_gc = _types.SimpleNamespace(
        collection_symbols=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setitem(_sys.modules, "gex_collector", fake_gc)
    # Falls back to the index trio (never raises).
    assert compute.gamma_symbol_options() == ["$SPX", "SPY", "QQQ"]
```

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k gamma_symbol_options -q`
Expected: FAIL — `AttributeError: ... has no attribute 'gamma_symbol_options'`.

**Step 3: Implement**

Add to `services/options_svc/compute.py` (Gamma section):

```python
def gamma_symbol_options() -> list:
    """Dropdown universe for the Gamma page: the collected symbols minus ``$VIX``
    ($SPX first). $VIX is still collected (sentiment bridge) but isn't a useful
    Gamma selection. Defensive: any failure → the index trio so the page always
    gets a usable list. ``gex_collector`` is imported lazily (see LAZY IMPORTS)."""
    try:
        import gex_collector as gc
        return [s for s in gc.collection_symbols() if s != "$VIX"]
    except Exception:
        return ["$SPX", "SPY", "QQQ"]
```

**Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_compute.py -k gamma_symbol_options -q`
Expected: PASS (2 passed).

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_compute.py
git commit -m "feat(options_svc): gamma_symbol_options (collected universe minus VIX)"
```

---

## Task 7: `publish_gamma_symbols` handler + scheduler startup publish

**Files:**
- Modify: `services/options_svc/handlers.py` (add cache-key constants ~line 50,
  add `publish_gamma_symbols`)
- Modify: `services/options_svc/scheduler.py` `loop` (one-shot startup block,
  ~line 159-162, alongside `refresh_gamma`)
- Test: `services/options_svc/tests/test_handlers.py` (append)

**Step 1: Write the failing test**

Use the existing fake bus pattern in `test_handlers.py` (grep for how other
`publish_*` handlers are tested — a fake bus capturing `cache_set`/`publish`).
Model this on that:

```python
def test_publish_gamma_symbols_caches_and_publishes(monkeypatch):
    import services.options_svc.compute as compute
    monkeypatch.setattr(compute, "gamma_symbol_options",
                        lambda: ["$SPX", "SPY", "NVDA"])
    bus = FakeBus()        # the suite's existing fake bus
    handlers.publish_gamma_symbols(bus)
    assert bus.cache["cache:options:gamma_symbols"] == {"symbols": ["$SPX", "SPY", "NVDA"]}
    assert any(ch == "events:options:gamma_symbols" for ch, _ in bus.published)
```

> Adapt `FakeBus`/`bus.cache`/`bus.published` to whatever the file's existing
> fake bus exposes (grep `def test_publish_gex_status` or `refresh_gamma` in
> `test_handlers.py` for the exact shape, and reuse it).

**Step 2: Run to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -k gamma_symbols -q`
Expected: FAIL — `AttributeError: ... has no attribute 'publish_gamma_symbols'`.

**Step 3: Implement**

In `handlers.py`, add the constants (near the other Gamma keys):

```python
CACHE_GAMMA_SYMBOLS = "cache:options:gamma_symbols"
EVENT_GAMMA_SYMBOLS = "events:options:gamma_symbols"
```

and the handler:

```python
def publish_gamma_symbols(bus) -> None:
    """Compute the Gamma dropdown universe (collected symbols minus $VIX) and
    publish it to the bus so the Tier-3 Gamma page can populate its symbol
    dropdown without importing any engine. No strict contract: a small read-only
    ``{"symbols":[...]}`` dict; ``compute.gamma_symbol_options`` is defensive."""
    data = {"symbols": compute.gamma_symbol_options()}
    version = bus.cache_set(CACHE_GAMMA_SYMBOLS, data)
    bus.publish(EVENT_GAMMA_SYMBOLS, {"version": version})
```

In `scheduler.py` `loop`, add a one-shot startup publish next to the gamma one
(after the `refresh_gamma` block, ~line 162):

```python
    # One-shot startup publish of the Gamma dropdown symbol universe (collected
    # symbols minus $VIX) so the Gamma page's dropdown is populated on first load.
    # The watchlist rarely changes mid-session; a service restart republishes.
    try:
        await loop_.run_in_executor(None, handlers.publish_gamma_symbols, bus)
    except Exception:
        pass
```

**Step 4: Run to verify it passes**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_handlers.py -k gamma_symbols -q`
Expected: PASS.

**Step 5: Run the whole options_svc suite**

Run: `.venv\Scripts\python -m pytest services\options_svc -q`
Expected: green.

**Step 6: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/scheduler.py \
  services/options_svc/tests/test_handlers.py
git commit -m "feat(options_svc): publish gamma_symbols universe at startup"
```

---

## Task 8: Gamma page `symbol_options` helper + dropdown

**Files:**
- Modify: `webgui/pages/options/gamma.py` (add `symbol_options`; swap the input
  for a select in `render`, ~line 410; remove the `select_all_on_focus` import use
  there; `panel_flex` default `full_cols`)
- Test: `webgui/tests/test_gamma.py` (append; create if absent — grep first)

**Step 1: Write the failing test**

```python
from pages.options import gamma


def test_symbol_options_default_present_and_first():
    out = gamma.symbol_options({"symbols": ["SPY", "$SPX", "NVDA"]})
    assert out[0] == "$SPX"          # $SPX always first
    assert out == ["$SPX", "SPY", "NVDA"]


def test_symbol_options_cold_fallback():
    assert gamma.symbol_options(None) == ["$SPX", "SPY", "QQQ"]
    assert gamma.symbol_options({}) == ["$SPX", "SPY", "QQQ"]


def test_symbol_options_injects_missing_default():
    out = gamma.symbol_options({"symbols": ["SPY", "QQQ"]})
    assert out[0] == "$SPX"
    assert "SPY" in out and "QQQ" in out
```

**Step 2: Run to verify it fails**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_gamma.py -k symbol_options -q`
Expected: FAIL — `AttributeError` / no `symbol_options`.

**Step 3: Implement the helper**

In `webgui/pages/options/gamma.py` (module level, near `_VIEWS`):

```python
_DEFAULT_SYMBOL = "$SPX"
_FALLBACK_SYMBOLS = ["$SPX", "SPY", "QQQ"]


def symbol_options(cached):
    """Dropdown option list from the cached gamma_symbols view.

    ``cached`` is ``{"symbols":[...]}`` (or None when the bus is cold). Returns a
    list with ``$SPX`` guaranteed present and FIRST (so it's the default), order
    otherwise preserved. Cold/empty → the index-trio fallback."""
    syms = (cached or {}).get("symbols") if isinstance(cached, dict) else None
    if not syms:
        return list(_FALLBACK_SYMBOLS)
    ordered = [_DEFAULT_SYMBOL] + [s for s in syms if s != _DEFAULT_SYMBOL]
    # dedupe, order-preserving (in case $SPX already led the list)
    out, seen = [], set()
    for s in ordered:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
```

**Step 4: Run to verify it passes**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest tests/test_gamma.py -k symbol_options -q`
Expected: PASS (3 passed).

**Step 5: Wire the dropdown into `render()`**

In `render()`, replace:

```python
        symbol_in = select_all_on_focus(ui.input("Symbol", value="$SPX").classes("w-28"))
```
with:

```python
        _sym_opts = symbol_options(bus_client.read("options:gamma_symbols"))
        symbol_in = ui.select(_sym_opts, value=_DEFAULT_SYMBOL,
                              with_input=True, label="Symbol").classes("w-40")
```

Notes:
- `bus_client` is already imported at the top of `render()`.
- Leave `_current_symbol()` unchanged (`(symbol_in.value or "").strip().upper()`).
- If `select_all_on_focus` is now unused in the file, remove its import
  (`from .inputs import select_all_on_focus`); if other inputs still use it, leave
  the import.
- `panel_flex` default: change `full_cols=82` → `full_cols=205` (08:30–15:20 CT at
  2-min slots) in its signature; update the docstring line "≈ five-minute slots"
  → "≈ two-minute slots".

**Step 6: Run the webgui suite**

Run: `cd webgui ; ..\.venv\Scripts\python -m pytest -q`
Expected: green (the existing gamma `panel_flex` tests pass any `n_cols`; if one
hardcodes the 82 default behavior, recalibrate it).

**Step 7: Commit**

```bash
git add webgui/pages/options/gamma.py webgui/tests/test_gamma.py
git commit -m "feat(webgui): Gamma symbol dropdown from collected universe"
```

---

## Task 9: Browser verification

**Files:** none (verification only).

**Step 1:** Ensure Memurai + proxy + `services/options_svc` + webgui are running
(or start the webgui dev server via the Claude Preview tool on :8500).

**Step 2:** Open `/options/gamma`. Verify:
- The Symbol field is now a **dropdown** defaulting to `$SPX`, listing the
  watchlist symbols (NVDA, TSLA, AAPL, …) and **not** `$VIX`.
- Selecting a watchlist symbol (e.g. `NVDA`) + clicking **Refresh now** repaints
  the bars; **Explain** opens an infographic tab for that symbol.
- The collector status bar shows a "Next scan" ~2 min out during market hours
  (off-hours it shows idle — expected).

**Step 3:** Screenshot for the user (dropdown open + a watchlist symbol rendered).

> If off-hours, intraday data may be sparse/zero — not a bug (documented).

---

## Task 10: Documentation

**Files:**
- Modify: root `CLAUDE.md` (Gamma route description + the "Gamma intraday-heatmap
  collection" section: note 2-min cadence and watchlist universe)
- Modify: `options-scanner/CLAUDE.md` (collector cadence/symbols line)

**Step 1:** Update the relevant lines:
- Gamma route row: mention the **symbol dropdown** (default `$SPX`, watchlist
  universe, Explain per-symbol).
- "Gamma intraday-heatmap collection" section: cadence **every 2 min** within
  08:30–15:20 CT; symbols = index base ($SPX/$VIX/SPY/QQQ) ∪ `watchlist`
  (Top 20.xlsx); `gamma_symbols` published for the dropdown.
- `options-scanner/CLAUDE.md`: collector now polls `collection_symbols()` every
  2 min (was 4 symbols / 5 min).

**Step 2: Commit**

```bash
git add CLAUDE.md options-scanner/CLAUDE.md
git commit -m "docs: GEX 2-min watchlist collection + Gamma symbol dropdown"
```

---

## Final verification checklist

- [ ] `cd options-scanner ; python -m pytest tests/ -q` — only the ~2 known
      pre-existing failures.
- [ ] `.venv\Scripts\python -m pytest services\options_svc -q` — green.
- [ ] `cd webgui ; ..\.venv\Scripts\python -m pytest -q` — green.
- [ ] Browser: dropdown defaults `$SPX`, excludes `$VIX`, lists watchlist;
      Explain works for a watchlist symbol.
- [ ] Docs updated.
