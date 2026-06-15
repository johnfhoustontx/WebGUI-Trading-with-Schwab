# Sentiment background refresher — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refresh the sentiment cache + bridge every 120 s server-side (independent of any open/active tab); the page shows cached data on activation with NO new fetch.

**Architecture:** A client-independent asyncio background loop (mirrors `pages/options/scanner.start_autoscan`) calls a no-UI `refresh_cache()` that updates the module `_CACHE` + publishes the bridge. The page drops its on-activation fetch + per-page 300 s timer and instead repaints from `_CACHE` on a cheap 120 s timer. `main.py` starts the loop at `@app.on_startup`.

**Tech Stack:** NiceGUI, asyncio (`asyncio.create_task`, `loop.run_in_executor`), pytest.

**Design:** [`2026-06-14-sentiment-background-refresher-design.md`](2026-06-14-sentiment-background-refresher-design.md)

**Tests:** `cd webgui && ..\.venv\Scripts\python -m pytest -q` (venv `D:\WebGUI Trading with Schwab\.venv\Scripts\python.exe`).

**Read first:** `webgui/pages/sentiment.py` — module-level loaders `_load_snapshots()`, `_load_live()`, `_load_sector_perf(spy)`, `_proxy_up()`, `is_rth(now)`, `build_bridge_payload`, `commit_trend_regime`, `composite_series`, and `_CACHE` (keys snaps/spy/sector/live/expanded/industry/composite_at/sector_at/proxy_up). In `render()`: the closure `_publish_bridge()` (~656-683), `_apply`/`_apply_sectors`/`_render_status`, `load(with_sectors)` (~894), and the tail timers (~932-941). Reference background-task pattern: `pages/options/scanner.py:85-107`.

---

## Task 1: Shared bridge helper + `refresh_cache` + `start_background_refresh` (module-level, no UI) + tests

**Files:** Modify `webgui/pages/sentiment.py`; add tests to `webgui/tests/test_sentiment_sectors.py`.

### 1a. Extract a module-level bridge helper (shared by page + refresher)
Add near the loaders:
```python
def build_and_write_bridge(snaps, spy, live, sector):
    """Build the bridge payload from cache/state data and write it. Defensive."""
    try:
        import bridge
        from datetime import datetime, timezone
        latest = live or (snaps[-1] if snaps else None)
        if not latest:
            return
        prior = composite_series(snaps or [])[1]
        trend = None
        if spy:
            tr, committed, _d = commit_trend_regime(spy)
            trend = {"state": committed, "label": trend_regime.STATE_LABELS[committed],
                     "description": trend_regime.STATE_DESCRIPTIONS[committed],
                     "raw_state": tr.state, "spy_close": round(tr.spy_close, 4),
                     "sma_50": round(tr.sma_50, 4), "sma_200": round(tr.sma_200, 4),
                     "sma_200_slope_pct": round(tr.sma_200_slope_pct, 4),
                     "drawdown_pct": round(tr.drawdown_pct, 4), "confidence": round(tr.confidence, 3)}
        sec_arg = None
        if sector:
            sec_arg = {"sector_data": sector.get("sector_data"),
                       "quotes": sector.get("quotes"), "dual": sector.get("dual")}
        payload = build_bridge_payload(latest, prior, spy or [],
                                       datetime.now(timezone.utc).isoformat(),
                                       sector=sec_arg, trend=trend)
        bridge.write_bridge(payload)
    except Exception:
        pass
```
Then REPLACE the page's closure `_publish_bridge()` body (in `render()`) to delegate:
```python
    def _publish_bridge():
        build_and_write_bridge(state.get("snaps"), state.get("spy"),
                               state.get("live"), state.get("sector"))
```
(Keep the calls to `_publish_bridge()` at the end of `_apply`/`_apply_sectors` as-is.)

### 1b. `_refresh_cache_sync` + `refresh_cache` + `start_background_refresh`
Add at module level (near `_CACHE` / loaders). Guard state in a module dict:
```python
_BG = {"started": False, "refreshing": False}


def _refresh_cache_sync(with_sectors=False):
    """Synchronous cache update (run in an executor). Updates _CACHE + bridge.
    No NiceGUI/page UI access — safe with zero clients."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    snaps, spy = _load_snapshots()
    _CACHE["snaps"], _CACHE["spy"] = snaps, spy
    live = _load_live() if is_rth(datetime.now(ZoneInfo("America/Chicago"))) else None
    _CACHE["live"] = live
    if with_sectors:
        try:
            _CACHE["sector"] = _load_sector_perf(spy)
        except Exception:
            pass
    _CACHE["composite_at"] = datetime.now()
    _CACHE["proxy_up"] = _proxy_up()
    build_and_write_bridge(snaps, spy, live, _CACHE.get("sector"))


async def refresh_cache(with_sectors=False):
    """Off-thread cache refresh; never raises; non-reentrant."""
    if _BG["refreshing"]:
        return
    _BG["refreshing"] = True
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _refresh_cache_sync, with_sectors)
    except Exception:
        pass
    finally:
        _BG["refreshing"] = False


async def _bg_loop():
    import asyncio
    await refresh_cache(with_sectors=True)   # initial: composite + sectors
    while True:
        await asyncio.sleep(120)
        await refresh_cache(with_sectors=False)  # composite-only


def start_background_refresh():
    """Start the 120s server-side refresher (idempotent). Call once at startup."""
    import asyncio
    if _BG["started"]:
        return
    _BG["started"] = True
    asyncio.create_task(_bg_loop())
```

### 1c. Tests (append to `webgui/tests/test_sentiment_sectors.py`)
```python
def test_refresh_cache_sync_populates_cache(monkeypatch):
    from pages import sentiment as S
    monkeypatch.setattr(S, "_load_snapshots", lambda *a, **k: ([{"date": "2026-06-12",
        "composite": {"total_score": "6.00", "bias": "Neutral", "size_modifier": "1.00x",
        "aggregate_confidence": 0.8}, "component_scores": {"vix_complex": 5},
        "component_confidence": {"vix_complex": 1.0}}], [100.0] * 60))
    monkeypatch.setattr(S, "_load_live", lambda: None)
    monkeypatch.setattr(S, "_proxy_up", lambda: True)
    monkeypatch.setattr(S, "build_and_write_bridge", lambda *a, **k: None)  # no file write
    S._CACHE["snaps"] = None
    S._refresh_cache_sync(with_sectors=False)
    assert S._CACHE["snaps"] and S._CACHE["proxy_up"] is True
    assert S._CACHE["composite_at"] is not None


def test_start_background_refresh_idempotent(monkeypatch):
    from pages import sentiment as S
    created = {"n": 0}
    import asyncio
    monkeypatch.setattr(asyncio, "create_task", lambda *a, **k: created.__setitem__("n", created["n"] + 1))
    S._BG["started"] = False
    S.start_background_refresh()
    S.start_background_refresh()
    assert created["n"] == 1
    S._BG["started"] = False  # reset for other tests
```
(`asyncio.get_event_loop()` inside `refresh_cache` is only hit in the async path, not these sync/monkeypatched tests.)

**Run** `..\.venv\Scripts\python -m pytest -q` (report count). **Commit:**
```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment_sectors.py
git commit -m "feat(sentiment): module-level 120s cache refresher + shared bridge helper"
```

---

## Task 2: Page timer rewire + `main.py` startup hook

**Files:** Modify `webgui/pages/sentiment.py` (`render()` tail); modify `webgui/main.py`.

### 2a. `render()` — drop on-activation fetch + 300 s timer; add repaint timers
Add a `_repaint_from_cache()` closure (re-seeds the DATA keys from `_CACHE`, re-applies; NO fetch; leaves `expanded`/`industry` untouched so user expansions persist):
```python
    def _repaint_from_cache():
        for k in ("snaps", "spy", "live", "sector", "composite_at", "proxy_up"):
            state[k] = _CACHE.get(k) if k not in ("snaps", "spy") else (_CACHE.get(k) or [])
        _apply()
        _apply_sectors()
        _render_status()
```
Replace the tail (current lines ~938-941):
```python
    if not state["snaps"]:
        ui.timer(0.1, lambda: load(with_sectors=True), once=True)
    ui.timer(300.0, load)
    ui.timer(15.0, _render_status)
```
with:
```python
    # No fetch on activation: paint from the module cache (refreshed every 120s
    # by the server-side background task). A quick one-shot catches the startup
    # refresh if the page is opened during the cold-start window.
    ui.timer(5.0, _repaint_from_cache, once=True)
    ui.timer(120.0, _repaint_from_cache)
    ui.timer(15.0, _render_status)
```
(Keep the synchronous `if state["snaps"]: _apply()` / `if state["sector"]: _apply_sectors()` / `_render_status()` block just above — instant paint from cache on render. Keep the manual Refresh button wired to `load(with_sectors=True)`.)

### 2b. `main.py` — start the refresher at startup
After the existing `_start_options_autoscan` on_startup hook, add:
```python
@app.on_startup
def _start_sentiment_refresh() -> None:
    """Start the server-side 120s sentiment cache+bridge refresher."""
    from pages import sentiment
    sentiment.start_background_refresh()
```

**Run** `..\.venv\Scripts\python -m pytest -q` (full webgui; report count). **Import smoke:** `..\.venv\Scripts\python -c "import sys; sys.path.insert(0,'.'); sys.path.insert(0,'..'); import main; print('ok')"`.
Confirm via code inspection that the on-activation fetch timer (`ui.timer(0.1, … load …, once=True)`) is gone and `render()` registers no `load` timer (only repaint + status).

**Commit:**
```bash
git add webgui/pages/sentiment.py webgui/main.py
git commit -m "feat(sentiment): page repaints from cache (no activation fetch); start 120s refresher at startup"
```

---

## Task 3: Verify + docs

1. **Runtime check (best-effort):** restart the `webgui` preview if a server slot is free; otherwise a script check: `import main` then confirm `sentiment._BG` exists and `start_background_refresh` is idempotent. If the dev server runs, tail `preview_logs` to confirm the refresher ticks (and that opening `/sentiment` does NOT trigger a fetch beyond the startup one). Off-hours the composite is the backfill snapshot; that's expected.
2. **Docs:** root `CLAUDE.md` — update the `/sentiment` note: "auto-refreshes every 120 s server-side (module-level background task, independent of any open tab); page paints from cache on activation (no fetch); manual Refresh still does a full composite+sector fetch." Bump test count. Note the bridge is now also published every 120 s by the webgui server (in addition to the GEX collector's 5-min publish).
3. **Commit** docs.

---

## Gotchas
- The background loop must touch NO page widgets/`ui.notify` (it runs with zero clients) — `refresh_cache`/`_refresh_cache_sync` only update `_CACHE` + write the bridge.
- Mirror `scanner.start_autoscan` exactly: idempotent `started` guard, `try/except` so the loop never dies, heavy work via `run_in_executor`.
- No lazy `from scoring import` — all scoring/live_composite imports stay at module top.
- `_repaint_from_cache` must NOT reset `expanded`/`industry` (preserve user expansions).
- Importing `sentiment` at startup (via the on_startup hook) binds sentiment's `scoring/` package first — the good order per the root CLAUDE.md collision note.
- options-scanner has ~12 known unrelated test failures; webgui suite is the one to keep green.
