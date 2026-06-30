# Daily Sentiment & Trend Intraday Graphs — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the `/sentiment` page's 30-Day History section with two value-colorized intraday graphs (Daily Market Sentiment 0–10 + Daily Market Trend 0–100), recorded every ~2 min, rolling last 5 trading days, in a collapsible expander.

**Architecture:** Tier-2 `sentiment_svc` records a `(ts, sentiment, trend)` point each 120 s `refresh()` (RTH-gated) into a new SQLite store, prunes to the last 5 distinct trading dates, and publishes `cache:sentiment:intraday_history`. Tier-1 `webgui/pages/sentiment.py` reads that view and renders two Highcharts `stockChart` line series colorized by value via `series.zones`.

**Tech Stack:** Python, SQLite (`sqlite3`), NiceGUI + Highcharts (`ui.highchart`), `shared.bus` Redis, pytest, fakeredis.

**Design doc:** [`2026-06-30-sentiment-daily-intraday-graphs-design.md`](2026-06-30-sentiment-daily-intraday-graphs-design.md)

**Conventions:** TDD (@superpowers:test-driven-development) — write the failing test first, watch it fail, implement minimally, watch it pass, commit. Run service tests from the repo root one service at a time; webgui tests from `webgui/`.

---

## Task 1: SQLite store `intraday_history_db.py`

**Files:**
- Modify: `repo_paths.py` (add `SENTIMENT_INTRADAY_DB`)
- Create: `services/sentiment_svc/intraday_history_db.py`
- Test: `services/sentiment_svc/tests/test_intraday_history_db.py`

**Step 1: Add the path constant.** In `repo_paths.py`, after the `DRIVER_PAPER_DB` block (~line 27):

```python
# Intraday 2-min sentiment + trend series for the /sentiment "Daily Sentiment &
# Trend" graphs. Rolling last 5 trading days; written by sentiment_svc each refresh.
SENTIMENT_INTRADAY_DB = SENTIMENT / "data" / "sentiment_intraday.db"
```

**Step 2: Write the failing test** at `services/sentiment_svc/tests/test_intraday_history_db.py`:

```python
import datetime as dt

from services.sentiment_svc import intraday_history_db as db


def _conn():
    c = db.connect(":memory:")
    return c


def _ts(date, hour=10, minute=0):
    return int(dt.datetime(date.year, date.month, date.day, hour, minute)
               .astimezone().timestamp())


def test_insert_and_load_roundtrip():
    c = _conn()
    today = dt.date(2026, 6, 30)
    db.insert_point(c, _ts(today, 10, 0), 6.2, 72.0)
    db.insert_point(c, _ts(today, 10, 2), 6.4, 70.0)
    rows = db.load_recent(c, n_days=5)
    assert [(r[1], r[2]) for r in rows] == [(6.2, 72.0), (6.4, 70.0)]
    # ascending by ts
    assert rows[0][0] < rows[1][0]


def test_load_recent_keeps_only_last_n_trading_dates():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(8):                       # 8 distinct dates
        d = base + dt.timedelta(days=i)
        db.insert_point(c, _ts(d), 5.0 + i * 0.1, 50.0 + i)
    rows = db.load_recent(c, n_days=5)
    dates = sorted({dt.datetime.fromtimestamp(r[0]).astimezone().date() for r in rows})
    assert len(dates) == 5
    assert dates[0] == base + dt.timedelta(days=3)   # last 5 of 8


def test_prune_deletes_older_than_n_dates():
    c = _conn()
    base = dt.date(2026, 6, 16)
    for i in range(8):
        db.insert_point(c, _ts(base + dt.timedelta(days=i)), 5.0, 50.0)
    db.prune(c, n_days=5)
    remaining = c.execute("SELECT COUNT(*) FROM sentiment_intraday").fetchone()[0]
    assert remaining == 5
```

**Step 3: Run it — expect failure** (`ModuleNotFoundError`):

```
.venv\Scripts\python -m pytest services/sentiment_svc/tests/test_intraday_history_db.py -v
```

**Step 4: Implement** `services/sentiment_svc/intraday_history_db.py`:

```python
"""SQLite persistence for the 2-min intraday sentiment + trend series.

One row per ~2-min sample: (ts unix-seconds, sentiment 0-10, trend 0-100).
Rolling window = the last N distinct LOCAL trading dates present (so weekends /
holidays / gaps are handled by date-presence, not a fixed calendar lookback).
Mirrors the gex_history_db pattern."""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sentiment_intraday (
    ts        INTEGER PRIMARY KEY,
    sentiment REAL,
    trend     REAL
);
CREATE INDEX IF NOT EXISTS idx_si_ts ON sentiment_intraday(ts);
"""


def connect(path=None) -> sqlite3.Connection:
    if path is None:
        from repo_paths import SENTIMENT_INTRADAY_DB
        path = SENTIMENT_INTRADAY_DB
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _local_date(ts: int) -> _dt.date:
    return _dt.datetime.fromtimestamp(ts).astimezone().date()


def insert_point(conn, ts: int, sentiment: float, trend: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sentiment_intraday(ts, sentiment, trend) "
        "VALUES (?, ?, ?)", (int(ts), float(sentiment), float(trend)))
    conn.commit()


def load_recent(conn, n_days: int = 5):
    """[(ts, sentiment, trend)] for the last n_days distinct local dates, asc."""
    rows = conn.execute(
        "SELECT ts, sentiment, trend FROM sentiment_intraday ORDER BY ts ASC"
    ).fetchall()
    if not rows:
        return []
    dates = sorted({_local_date(r[0]) for r in rows})
    keep = set(dates[-n_days:])
    return [r for r in rows if _local_date(r[0]) in keep]


def prune(conn, n_days: int = 5) -> None:
    """Delete rows older than the last n_days distinct local dates."""
    dates = sorted({_local_date(r[0])
                    for r in conn.execute("SELECT ts FROM sentiment_intraday")})
    if len(dates) <= n_days:
        return
    cutoff_date = dates[-n_days]
    cutoff_ts = int(_dt.datetime.combine(cutoff_date, _dt.time.min)
                    .astimezone().timestamp())
    conn.execute("DELETE FROM sentiment_intraday WHERE ts < ?", (cutoff_ts,))
    conn.commit()
```

**Step 5: Run — expect PASS.** Same command as Step 3.

**Step 6: Commit**

```bash
git add repo_paths.py services/sentiment_svc/intraday_history_db.py services/sentiment_svc/tests/test_intraday_history_db.py
git commit -m "feat(sentiment_svc): SQLite store for 2-min intraday sentiment+trend series"
```

---

## Task 2: Additive contract `IntradayHistory`

**Files:**
- Modify: `shared/contracts/sentiment.py`
- Modify: `shared/contracts/__init__.py` (export it, if the module re-exports — check first)
- Test: `shared/contracts/tests/test_sentiment.py` (create if absent)

**Step 1: Write the failing test** at `shared/contracts/tests/test_sentiment.py`:

```python
from shared.contracts.sentiment import IntradayHistory


def test_intraday_history_accepts_points():
    h = IntradayHistory(points=[{"ts": 1, "sentiment": 6.0, "trend": 70.0}])
    assert h.points[0]["sentiment"] == 6.0


def test_intraday_history_defaults_empty():
    assert IntradayHistory().points == []
```

**Step 2: Run — expect ImportError.**

```
.venv\Scripts\python -m pytest shared/contracts/tests/test_sentiment.py -v
```

**Step 3: Implement.** Append to `shared/contracts/sentiment.py`:

```python
class IntradayHistory(_Base):
    points: list = []   # [{"ts": int, "sentiment": float, "trend": float}, ...]
```

Check `shared/contracts/__init__.py`: if it explicitly re-exports names, add `IntradayHistory` to the sentiment import line; if it does `from .sentiment import *`, no change needed.

**Step 4: Run — expect PASS.**

**Step 5: Commit**

```bash
git add shared/contracts/sentiment.py shared/contracts/__init__.py shared/contracts/tests/test_sentiment.py
git commit -m "feat(contracts): add IntradayHistory sentiment view contract"
```

---

## Task 3: Record + publish in `handlers.refresh`

**Files:**
- Modify: `services/sentiment_svc/handlers.py`
- Test: `services/sentiment_svc/tests/test_handlers.py` (add cases)

**Step 1: Write the failing tests.** Add to `services/sentiment_svc/tests/test_handlers.py` (mirror the file's existing fakeredis/Bus + monkeypatch idiom — inspect the top of that file for the existing `Bus`/`compute` patching helpers and reuse them):

```python
import datetime as dt
from services.sentiment_svc import handlers


def test_record_point_pure_extracts_values():
    live = {"composite": {"total_score": "6.30"}}
    trend = {"score": 71.5}
    assert handlers._intraday_values(live, trend) == (6.30, 71.5)


def test_record_point_none_when_no_live():
    assert handlers._intraday_values(None, {"score": 70}) is None


def test_refresh_publishes_intraday_history_during_rth(monkeypatch, bus):
    # Force RTH + stub compute so refresh records one point.
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: True)
    monkeypatch.setattr(handlers.compute, "load_live",
                        lambda: {"composite": {"total_score": "6.0", "bias": "Neutral"}})
    monkeypatch.setattr(handlers.compute, "load_snapshots", lambda: ([], []))
    monkeypatch.setattr(handlers.compute, "proxy_up", lambda: True)
    monkeypatch.setattr(handlers.compute, "derive_composite_extras",
                        lambda *a, **k: {})
    monkeypatch.setattr(handlers.compute, "build_and_write_bridge",
                        lambda *a, **k: None)
    handlers._TREND["trend"] = {"score": 70.0}
    # use an in-memory db so the test doesn't touch the real file
    monkeypatch.setattr(handlers, "_intraday_conn",
                        handlers.intraday_history_db.connect(":memory:"))

    handlers.refresh(bus, with_sectors=False)

    view = bus.cache_get("cache:sentiment:intraday_history")
    assert view and view["points"]
    p = view["points"][-1]
    assert p["sentiment"] == 6.0 and p["trend"] == 70.0


def test_refresh_skips_recording_off_hours(monkeypatch, bus):
    monkeypatch.setattr(handlers, "_is_rth_now", lambda: False)
    monkeypatch.setattr(handlers.compute, "load_live", lambda: None)
    monkeypatch.setattr(handlers.compute, "load_snapshots", lambda: ([], []))
    monkeypatch.setattr(handlers.compute, "proxy_up", lambda: True)
    monkeypatch.setattr(handlers.compute, "derive_composite_extras", lambda *a, **k: {})
    monkeypatch.setattr(handlers.compute, "build_and_write_bridge", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "_intraday_conn",
                        handlers.intraday_history_db.connect(":memory:"))
    handlers.refresh(bus, with_sectors=False)
    # No publish off-hours (view absent or empty).
    view = bus.cache_get("cache:sentiment:intraday_history")
    assert not (view and view.get("points"))
```

> If `test_handlers.py` has no shared `bus` fixture, add one in `services/sentiment_svc/tests/conftest.py` using the same fakeredis-backed `Bus` other service test suites use (copy from `services/options_svc/tests/conftest.py`).

**Step 2: Run — expect failure** (`AttributeError: _intraday_values`).

```
.venv\Scripts\python -m pytest services/sentiment_svc/tests/test_handlers.py -v
```

**Step 3: Implement** in `services/sentiment_svc/handlers.py`:

- Add near the other imports:
  ```python
  import datetime as _dt
  from zoneinfo import ZoneInfo as _ZI
  from services.sentiment_svc import intraday_history_db
  ```
- Add cache/event constants beside `CACHE_HISTORY`:
  ```python
  CACHE_INTRADAY = "cache:sentiment:intraday_history"
  EVENT_INTRADAY = "events:sentiment:intraday_history"
  ```
- Add a module-level lazily-opened connection + helpers:
  ```python
  _intraday_conn = None


  def _get_intraday_conn():
      global _intraday_conn
      if _intraday_conn is None:
          _intraday_conn = intraday_history_db.connect()
      return _intraday_conn


  def _is_rth_now() -> bool:
      """Mon–Fri 08:30–15:00 CT (mirrors the page's is_rth)."""
      now = _dt.datetime.now(_ZI("America/Chicago"))
      if now.weekday() >= 5:
          return False
      return (8, 30) <= (now.hour, now.minute) < (15, 0)


  def _intraday_values(live, trend):
      """(sentiment 0-10, trend 0-100) from the live snapshot + trend dict, or
      None when there is no live composite to record."""
      if not live:
          return None
      try:
          sentiment = float((live.get("composite") or {})["total_score"])
          tscore = float((trend or {}).get("score"))
      except (KeyError, TypeError, ValueError):
          return None
      return sentiment, tscore


  def _record_intraday(bus, live, trend):
      """Record one 2-min point (RTH-only), prune to 5 trading days, publish the
      view. Defensive — never aborts the core refresh."""
      try:
          if not _is_rth_now():
              return
          vals = _intraday_values(live, trend)
          if vals is None:
              return
          conn = _get_intraday_conn()
          ts = int(_dt.datetime.now().timestamp())
          intraday_history_db.insert_point(conn, ts, vals[0], vals[1])
          intraday_history_db.prune(conn, n_days=5)
          rows = intraday_history_db.load_recent(conn, n_days=5)
          points = [{"ts": r[0], "sentiment": r[1], "trend": r[2]} for r in rows]
          version = bus.cache_set(CACHE_INTRADAY, {"points": points})
          bus.publish(EVENT_INTRADAY, {"version": version})
      except Exception:  # noqa: BLE001
          log.exception("intraday history record failed")
  ```
- Call it inside `refresh()` after `bus.cache_set(CACHE_HISTORY, …)` (line ~176):
  ```python
  _record_intraday(bus, live, _TREND["trend"])
  ```

> The test monkeypatches `handlers._intraday_conn` directly; `_get_intraday_conn()` returns it when already set, so no real file is touched in tests.

**Step 4: Run — expect PASS.**

```
.venv\Scripts\python -m pytest services/sentiment_svc/tests/test_handlers.py -v
```

**Step 5: Commit**

```bash
git add services/sentiment_svc/handlers.py services/sentiment_svc/tests/
git commit -m "feat(sentiment_svc): record 2-min intraday sentiment+trend, publish cache view"
```

---

## Task 4: Page figure builders (pure)

**Files:**
- Modify: `webgui/pages/sentiment.py` (add two builders near `build_history_figure` ~line 198)
- Test: `webgui/tests/test_sentiment.py` (add cases)

**Step 1: Write the failing tests.** Add to `webgui/tests/test_sentiment.py`:

```python
from pages.sentiment import (build_sentiment_intraday_figure,
                             build_trend_intraday_figure)

_PTS = [{"ts": 1000, "sentiment": 3.0, "trend": 20.0},
        {"ts": 1120, "sentiment": 6.0, "trend": 55.0},
        {"ts": 1240, "sentiment": 8.0, "trend": 85.0}]


def test_sentiment_intraday_figure_maps_points_to_ms():
    fig = build_sentiment_intraday_figure(_PTS)
    data = fig["series"][0]["data"]
    assert data[0] == [1000 * 1000, 3.0]
    assert len(data) == 3


def test_sentiment_intraday_figure_has_value_zones():
    fig = build_sentiment_intraday_figure(_PTS)
    zones = fig["series"][0]["zones"]
    # red <=4.5, yellow <=6.5, green above
    assert zones[0]["value"] == 4.5 and zones[1]["value"] == 6.5
    assert "color" in zones[-1]


def test_trend_intraday_figure_value_zones_and_axis():
    fig = build_trend_intraday_figure(_PTS)
    data = fig["series"][0]["data"]
    assert data[2] == [1240 * 1000, 85.0]
    zones = fig["series"][0]["zones"]
    assert zones[0]["value"] == 30 and zones[1]["value"] == 70
    assert fig["yAxis"]["min"] == 0 and fig["yAxis"]["max"] == 100


def test_intraday_figures_empty_points_are_valid():
    assert build_sentiment_intraday_figure([])["series"][0]["data"] == []
    assert build_trend_intraday_figure(None)["series"][0]["data"] == []
```

**Step 2: Run — expect ImportError.**

```
cd webgui && ..\.venv\Scripts\python -m pytest tests/test_sentiment.py -v
```

**Step 3: Implement** in `webgui/pages/sentiment.py` (after `build_history_figure`). Add a shared helper + two builders:

```python
def _intraday_figure(points, *, value_key, y_max, y_title, zones):
    """Shared Highcharts stockChart for a 2-min intraday value series, colorized
    by value via series.zones. Ordinal x-axis collapses overnight session gaps;
    the date lives in the tooltip header (datetime-crosshair-label epoch-ms gotcha)."""
    pts = points or []
    data = [[int(p["ts"]) * 1000, _safe_float(p.get(value_key))] for p in pts]
    axis_label = {"style": {"color": "#bdbdbd"}}
    return {
        "chart": {"type": "line", "backgroundColor": "transparent",
                  "height": 200, "spacing": [8, 12, 8, 0]},
        "title": {"text": None},
        "credits": {"enabled": False},
        "accessibility": {"enabled": False},
        "legend": {"enabled": False},
        "xAxis": {"type": "datetime", "ordinal": True,
                  "lineColor": "rgba(255,255,255,0.15)",
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label,
                  "crosshair": {"label": {"enabled": False}}},
        "yAxis": {"min": 0, "max": y_max,
                  "title": {"text": y_title, "style": {"color": "#bdbdbd"}},
                  "gridLineColor": "rgba(255,255,255,0.06)", "labels": axis_label},
        "tooltip": {"xDateFormat": "%b %e, %H:%M",
                    "pointFormat": y_title + ": <b>{point.y:.2f}</b>"},
        "series": [{
            "name": y_title, "type": "line", "data": data,
            "lineWidth": 2, "zoneAxis": "y", "zones": zones,
            "marker": {"enabled": False},
        }],
    }


def build_sentiment_intraday_figure(points):
    """Daily Market Sentiment (0-10), colorized by traffic bands."""
    zones = [{"value": 4.5, "color": CLR_RED},
             {"value": 6.5, "color": CLR_YELLOW},
             {"color": CLR_GREEN}]
    return _intraday_figure(points, value_key="sentiment", y_max=10,
                            y_title="Sentiment", zones=zones)


def build_trend_intraday_figure(points):
    """Daily Market Trend (0-100), colorized by the 30/70 range boundaries."""
    zones = [{"value": 30, "color": CLR_RED},
             {"value": 70, "color": CLR_YELLOW},
             {"color": CLR_GREEN}]
    return _intraday_figure(points, value_key="trend", y_max=100,
                            y_title="Trend", zones=zones)
```

**Step 4: Run — expect PASS.**

**Step 5: Commit**

```bash
git add webgui/pages/sentiment.py webgui/tests/test_sentiment.py
git commit -m "feat(webgui): pure builders for colorized intraday sentiment+trend figures"
```

---

## Task 5: Page wiring — read cache, swap the section, reflow-on-expand

**Files:**
- Modify: `webgui/pages/sentiment.py` (`_read_cache`, render section ~622–628, `_apply` ~729–736)

**Step 1: Read the new view.** In `_read_cache`, add:

```python
intraday = bus_client.read("sentiment:intraday_history") or {}
state["intraday"] = intraday.get("points") or []
```

Add `"intraday": []` to the initial `state = {...}` dict.

**Step 2: Replace the section.** Swap lines ~621–628 (the `ui.separator` + `30-Day History` expander + roll/vel/flag/div labels) with:

```python
ui.separator().classes("q-my-md")
# Daily Sentiment & Trend — two value-colorized 2-min intraday series
# (rolling last 5 trading days), collapsed by default.
with ui.expansion("Daily Sentiment & Trend", icon="show_chart",
                  value=False).classes("w-full") as daily_exp:
    ui.label("Daily Market Sentiment").classes("text-subtitle2 q-mt-sm")
    sent_intraday_plot = ui.highchart(
        build_sentiment_intraday_figure([])).classes("w-full")
    ui.label("Daily Market Trend").classes("text-subtitle2 q-mt-md")
    trend_intraday_plot = ui.highchart(
        build_trend_intraday_figure([])).classes("w-full")

# Reflow both charts when the expander opens (a chart built inside a collapsed
# expander measures 0x0 — same fix as the Simulator's hidden tab panels).
def _reflow_daily(e):
    if e.value:
        for el in (sent_intraday_plot, trend_intraday_plot):
            ui.timer(0.05,
                     lambda el=el: ui.run_javascript(
                         f"getElement({el.id})?.chart?.reflow()"), once=True)
daily_exp.on_value_change(_reflow_daily)
```

> Remove the now-unused `roll_lbl`, `vel_lbl`, `flag_lbl`, `div_lbl`, and `hist_plot` widgets. Keep `build_history_figure`, `composite_series`, `rolling_averages`, `sentiment_30d_avg` as functions (still used by the 30-Day-Avg gauge / tests) — only their use in this section is removed.

**Step 3: Update `_apply`.** Replace the old block (~729–736):

```python
hist_plot.options = build_history_figure(snaps)
hist_plot.update()
a5, a20, label = rolling_averages(prior_scores)
roll_lbl.text = ...
vel = derived.get("velocity") or {}
vel_lbl.text = ...
flag_lbl.text = ...
div_lbl.text = ...
```

with:

```python
pts = state.get("intraday") or []
sent_intraday_plot.options = build_sentiment_intraday_figure(pts)
sent_intraday_plot.update()
trend_intraday_plot.options = build_trend_intraday_figure(pts)
trend_intraday_plot.update()
```

> `prior_scores` may still be used above this point (gauge/tiles); if it becomes unused after removing `rolling_averages`, leave it — it's cheap — or delete if the linter flags it. Verify `prior_scores` isn't referenced elsewhere before deleting.

**Step 4: Run the page test suite + the inline-style guard.**

```
cd webgui && ..\.venv\Scripts\python -m pytest tests/test_sentiment.py tests/test_no_inline_style.py tests/test_shell.py -v
```

Expected: PASS (no `.style(`/`:style=` introduced — the figures are Highcharts dicts, the labels use Tailwind classes).

**Step 5: Commit**

```bash
git add webgui/pages/sentiment.py
git commit -m "feat(webgui): swap 30-Day History for Daily Sentiment & Trend intraday graphs"
```

---

## Task 6: Full verification + live check + docs

**Step 1: Run all touched suites.**

```
.venv\Scripts\python -m pytest services/sentiment_svc -q
.venv\Scripts\python -m pytest shared/contracts -q
cd webgui && ..\.venv\Scripts\python -m pytest -q
```

Expected: all green (webgui 610+ green; sentiment_svc green incl. new tests).

**Step 2: Live-verify** (per @superpowers:verification-before-completion). Restart `sentiment_svc` (the running one is stale) so it picks up the new handler, then start the webgui preview and open `/sentiment`:
- Expand "Daily Sentiment & Trend"; both charts render (not collapsed at 0px — confirms the reflow fix). During RTH they fill with points; off-hours they may be empty until the next session records data (expected — recorded going forward).
- To verify end-to-end without waiting for RTH, seed a few rows directly and confirm the page paints: enqueue is not applicable (write path is the service), so instead temporarily insert via a Python one-liner against `SENTIMENT_INTRADAY_DB`, set the cache view with `Bus().cache_set("cache:sentiment:intraday_history", {"points":[...]})`, and confirm the charts colorize (red/yellow/green by value). Remove the seed rows after.
- Use `preview_eval` to read `.highcharts-series path` if the screenshot tool times out on the multi-chart page (known caveat).

**Step 3: Update `CLAUDE.md`.** In the root `CLAUDE.md`, update the `/sentiment` route-table row and the Sentiment section: replace the "30-Day History (collapsed)" description with the new **"Daily Sentiment & Trend"** expander (two value-colorized 2-min intraday series, rolling 5 trading days, recorded going forward by `sentiment_svc` into `SENTIMENT_INTRADAY_DB` → `cache:sentiment:intraday_history`). Note the new `repo_paths.SENTIMENT_INTRADAY_DB`, the `intraday_history_db` module, and the `IntradayHistory` contract. Bump the test counts.

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(sentiment): document Daily Sentiment & Trend intraday graphs"
```

---

## Notes / gotchas (carry into execution)

- **Recorded going forward, no backfill** — graphs are empty until the service records during RTH. This is intended; do not add a backfill.
- **stockChart ordinal axis** collapses session gaps automatically because every recorded point is RTH-only. `type="stockChart"` is set via `ui.highchart(options, type="stockChart")` if needed — but a plain `chart.type:"line"` with `xAxis.type:"datetime"` + `ordinal:True` also collapses gaps; the test only checks the options dict, so confirm in the live check that gaps actually collapse, and switch the `ui.highchart(..., type="stockChart")` constructor if not.
- **Collapsed-expander 0×0 reflow** — the `daily_exp.on_value_change` reflow is load-bearing; without it the charts render collapsed on first expand.
- **`series.zones` colorizes the line by y-value** — green/yellow/red segments, matching the gauge/traffic semantics. No per-point color loop needed.
- **3-tier rule** — the page imports only `nicegui` + `shared.bus` + `shared.contracts`; all recording/SQLite lives in `sentiment_svc`.
- Run service suites **per folder** from the repo root (never `pytest services`), per the documented module-name-collision rule.
