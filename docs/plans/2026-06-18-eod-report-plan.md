# EOD Report Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an "EOD Report" menu item to the webgui that builds a Summary and a
Detailed end-of-day report (Options activity + Driver trades/performance), viewable
in-app and exportable/archivable as standalone HTML files.

**Architecture:** A pure webgui leaf page (`webgui/pages/eod.py`) reads the existing
Redis caches via `bus_client`, builds each report as an HTML **fragment + shared CSS
string** with pure, unit-tested functions, renders the fragment in-app via
`ui.add_css` + `ui.html`, and on "Generate" wraps the same fragment into a standalone
`<html>` document written to `webgui/data/eod/<date>/`. No new service, no new port —
the webgui keeps importing only `nicegui` + `shared.bus` + `shared.contracts`.

**Tech Stack:** NiceGUI, FastAPI (for the file-serving route, like the existing
`/options/explain`), Python stdlib (`html.escape`, `pathlib`, `datetime`), pytest.

**Design doc:** `docs/plans/2026-06-18-eod-report-design.md`

**Reference patterns to mirror:**
- `webgui/pages/options/gamma.py` — `EXPLAIN_CSS` + fragment + `ui.add_css`/`ui.html`.
- `webgui/main.py:72-81` — `@app.get("/options/explain")` returning `HTMLResponse`.
- `webgui/pages/trade.py` / `driver.py` — pure module-level builders + thin `render()`.
- `webgui/bus_client.py` — `read("<domain>:<view>")` returns the cached dict or None.

**Cache shapes (all read defensively with `.get(...)`):**
- `options:scan` → `{"signals_0dte": [...], "signals_swing": [...], "vix_term_structure": {...}}`
- `options:captured` → `{"signals": [ {symbol, score, current_score, score_drift, recommendation, unrealized_pnl, ...} ]}`
- `options:paper_trades` → `{"trades": [ {symbol, strategy, realized_pnl, status, entry_time, ...} ]}`
- `options:paper_account` → `{"snapshot": {...}|None, "positions": [...], "orders": [...], "has_account": bool}`
- `driver:approvals` → `{date, grade, grade_reasons[], conditions{}, pnl_today, proposed_trades[], status, decision, results[], reasons[]}`
- `driver:performance` → `{"summary": {...}, "trades": [...]}`

**Run tests with:** `cd webgui && ..\.venv\Scripts\python -m pytest -q`

---

## Task 1: Scaffold the pure module skeleton + CSS constant

**Files:**
- Create: `webgui/pages/eod.py`
- Test: `webgui/tests/test_eod.py`

**Step 1: Write the failing test**

```python
# webgui/tests/test_eod.py
"""Tests for the EOD report pure builders (webgui/pages/eod.py)."""
from pages import eod


def test_css_is_scoped_nonempty_string():
    assert isinstance(eod.EOD_CSS, str)
    assert ".eod-report" in eod.EOD_CSS  # rules are scoped to a wrapper class
```

**Step 2: Run it to verify it fails**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_eod.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pages.eod'` (or `AttributeError`).

**Step 3: Write the minimal implementation**

```python
# webgui/pages/eod.py
"""EOD Report — pure builders + thin render().

Reads the already-published Redis caches (options:* and driver:*) and builds a
Summary and a Detailed report as an HTML *fragment* + a shared CSS string (one
source of truth for both the in-app view and the exported standalone .html).
Mirrors the gamma.py Explain pattern: NiceGUI's ``ui.html`` strips ``<style>``,
so CSS goes through ``ui.add_css`` in-app and is inlined into the document on
export.
"""
from __future__ import annotations

from html import escape

# Scoped to .eod-report so the in-app add_css does not leak into the rest of the app.
EOD_CSS = """
.eod-report { font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #e8e8e8; }
.eod-report h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
.eod-report h2 { font-size: 1.05rem; margin: 1.2rem 0 .4rem; opacity: .85;
                 border-bottom: 1px solid rgba(255,255,255,.12); padding-bottom: .2rem; }
.eod-report .meta { opacity: .6; font-size: .8rem; margin-bottom: .6rem; }
.eod-report table { border-collapse: collapse; width: 100%; font-size: .82rem; margin: .3rem 0; }
.eod-report th, .eod-report td { text-align: left; padding: 4px 8px;
                 border-bottom: 1px solid rgba(255,255,255,.08); }
.eod-report th { opacity: .7; font-weight: 600; }
.eod-report .tiles { display: flex; flex-wrap: wrap; gap: .6rem; margin: .4rem 0; }
.eod-report .tile { background: rgba(255,255,255,.05); border-radius: 10px;
                 padding: .5rem .8rem; min-width: 120px; }
.eod-report .tile .k { font-size: .72rem; opacity: .6; }
.eod-report .tile .v { font-size: 1.1rem; font-weight: 700; }
.eod-report .pos { color: #4 caf50; } .eod-report .neg { color: #ef5350; }
.eod-report .none { opacity: .5; font-style: italic; }
.eod-report a { color: #64b5f6; }
"""
```

> Note: fix the `#4 caf50` typo to `#4caf50` when typing (kept here only to flag
> it). Real value: `.eod-report .pos { color: #4caf50; }`.

**Step 4: Run the test to verify it passes**

Run: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_eod.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add webgui/pages/eod.py webgui/tests/test_eod.py
git commit -m "feat(webgui): EOD report module skeleton + scoped CSS"
```

---

## Task 2: `wrap_document` — fragment → standalone HTML

**Files:**
- Modify: `webgui/pages/eod.py`
- Test: `webgui/tests/test_eod.py`

**Step 1: Write the failing test**

```python
def test_wrap_document_is_standalone_with_css_and_title():
    doc = eod.wrap_document("<p>hi</p>", ".eod-report{color:red}", "My Title")
    assert doc.lstrip().startswith("<!DOCTYPE html>")
    assert "<style>.eod-report{color:red}</style>" in doc
    assert "<title>My Title</title>" in doc
    assert "<p>hi</p>" in doc
    assert doc.rstrip().endswith("</html>")
```

**Step 2: Run it — Expected:** FAIL (`AttributeError: wrap_document`).

**Step 3: Implement**

```python
def wrap_document(fragment: str, css: str, title: str) -> str:
    """Wrap a report fragment + CSS into a self-contained HTML document for export."""
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{css}</style></head>"
        f"<body style=\"background:#1e1e1e;margin:0;padding:1.2rem\">{fragment}</body></html>"
    )
```

**Step 4: Run — Expected:** PASS.

**Step 5: Commit**

```bash
git add webgui/pages/eod.py webgui/tests/test_eod.py
git commit -m "feat(webgui): EOD wrap_document standalone HTML wrapper"
```

---

## Task 3: Small formatting helpers (`_money`, `_num`, `_cell`)

**Files:**
- Modify: `webgui/pages/eod.py`
- Test: `webgui/tests/test_eod.py`

**Step 1: Write the failing tests**

```python
def test_money_formats_sign_and_none():
    assert eod._money(1234.5) == "$1,234.50"
    assert eod._money(-50) == "-$50.00"
    assert eod._money(None) == "—"

def test_cell_escapes_html():
    assert eod._cell("<script>") == "<td>&lt;script&gt;</td>"
```

**Step 2: Run — Expected:** FAIL.

**Step 3: Implement**

```python
def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _money(v) -> str:
    n = _num(v)
    if n is None:
        return "—"  # em dash
    sign = "-" if n < 0 else ""
    return f"{sign}${abs(n):,.2f}"

def _cell(v) -> str:
    return f"<td>{escape('' if v is None else str(v))}</td>"

def _pn_class(v) -> str:
    """CSS class for a signed number ('pos'/'neg'/'')."""
    n = _num(v)
    return "" if n is None or n == 0 else ("pos" if n > 0 else "neg")
```

**Step 4: Run — Expected:** PASS.

**Step 5: Commit**

```bash
git add webgui/pages/eod.py webgui/tests/test_eod.py
git commit -m "feat(webgui): EOD formatting helpers (_money/_num/_cell)"
```

---

## Task 4: Per-section table builders (detail rows)

Each builder takes the cache dict and returns an HTML `<table>` (or a `<p class="none">No data</p>`
when empty). One sub-task per section; identical TDD shape.

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

### 4a — `captured_section(cache)`

**Step 1: Failing test**

```python
def test_captured_section_renders_rows_and_handles_empty():
    cache = {"signals": [
        {"symbol": "AAPL", "score": 8.1, "current_score": 7.4,
         "score_drift": -0.7, "recommendation": "HOLD", "unrealized_pnl": 42.0},
    ]}
    html = eod.captured_section(cache)
    assert "AAPL" in html and "HOLD" in html
    assert "-0.70" in html  # drift formatted to 2dp
    assert 'class="none"' in eod.captured_section({"signals": []})
    assert 'class="none"' in eod.captured_section(None)
```

**Step 3: Implement**

```python
def _table(headers, rows_html, empty="No data"):
    if not rows_html:
        return f'<p class="none">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"

def captured_section(cache) -> str:
    sigs = (cache or {}).get("signals", []) if isinstance(cache, dict) else []
    rows = []
    for s in sigs:
        drift = _num(s.get("score_drift"))
        drift_txt = f"{drift:.2f}" if drift is not None else "—"
        rows.append(
            "<tr>"
            + _cell(s.get("symbol"))
            + _cell(s.get("score"))
            + _cell(s.get("current_score"))
            + f'<td class="{_pn_class(drift)}">{drift_txt}</td>'
            + _cell(s.get("recommendation"))
            + f'<td class="{_pn_class(s.get("unrealized_pnl"))}">{_money(s.get("unrealized_pnl"))}</td>'
            + "</tr>"
        )
    return _table(
        ["Symbol", "Entry score", "Current", "Drift", "Rec", "Unrealized P&L"], rows,
        empty="No captured signals.")
```

**Steps 2/4:** run the test (FAIL then PASS). **Step 5:** commit
`feat(webgui): EOD captured-signals section builder`.

### 4b — `paper_section(trades_cache, account_cache)`

**Failing test**

```python
def test_paper_section_lists_trades_and_account_pnl():
    trades = {"trades": [
        {"symbol": "SPY", "strategy": "IC", "realized_pnl": 120.0,
         "status": "CLOSED", "entry_time": "2026-06-18T09:40:00"},
    ]}
    account = {"snapshot": {"realized_pnl_today": 120.0, "open_pnl": -5.0,
                            "account_value": 10120.0}, "has_account": True}
    html = eod.paper_section(trades, account)
    assert "SPY" in html and "IC" in html
    assert "$120.00" in html
    assert 'class="none"' in eod.paper_section({"trades": []},
                                               {"has_account": False, "snapshot": None})
```

**Implement** — render an account summary line (pull `realized_pnl_today` /
`open_pnl` / `account_value` from `snapshot` with `.get`, each via `_money`; if
`has_account` is False or `snapshot` is None, show a `none` note for the summary)
followed by a trades `_table` (`Symbol`, `Strategy`, `Status`, `Entry`,
`Realized P&L`). Use `entry_time` sliced `[:19]` like `paper.py`. Empty trades →
`none` note. Commit `feat(webgui): EOD paper-trades section builder`.

### 4c — `scanner_section(scan_cache)`

**Failing test**

```python
def test_scanner_section_counts_and_lists():
    scan = {"signals_0dte": [{"symbol": "$SPX", "composite_score": 9.0}],
            "signals_swing": [{"symbol": "QQQ", "composite_score": 7.0}]}
    html = eod.scanner_section(scan)
    assert "$SPX" in html and "QQQ" in html
    assert 'class="none"' in eod.scanner_section({"signals_0dte": [], "signals_swing": []})
```

**Implement** — two `_table`s (0-DTE + Swing) with `Symbol` + `Score` (read
`composite_score`), each empty→`none`. Wrap with `<h2>` subheads inside the
section. Commit `feat(webgui): EOD scanner section builder`.

### 4d — `driver_section(approvals_cache, perf_cache)`

**Failing test**

```python
def test_driver_section_shows_grade_status_and_perf():
    appr = {"date": "2026-06-18", "grade": "B+", "status": "approved",
            "decision": "approved", "pnl_today": 210.0,
            "proposed_trades": [{"symbol": "MES", "bucket": "A", "side": "long"}],
            "reasons": []}
    perf = {"summary": {"win_rate": 0.62, "realized_pnl": 1500.0, "trades": 21},
            "trades": []}
    html = eod.driver_section(appr, perf)
    assert "B+" in html and "approved" in html
    assert "MES" in html
    assert "62" in html  # win rate rendered as percent
    assert 'class="none"' in eod.driver_section(None, None)
```

**Implement** — a grade/status/decision/`pnl_today` line, a proposed-trades
`_table` (loose dicts: render `symbol`/`bucket`/`side`/`quantity` via `.get`), and
a perf summary line (`win_rate`→percent, `realized_pnl`→`_money`, `trades` count).
If both caches are falsy → single `none` note. Commit
`feat(webgui): EOD driver section builder`.

---

## Task 5: `detail_fragment(snapshot)` — assemble the detailed report

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

`snapshot` is a dict of the raw caches: `{"date", "generated_at", "scan",
"captured", "paper_trades", "paper_account", "driver_approvals",
"driver_performance"}`.

**Step 1: Failing test**

```python
SAMPLE = {
    "date": "2026-06-18", "generated_at": "2026-06-18 16:05 CT",
    "scan": {"signals_0dte": [{"symbol": "$SPX", "composite_score": 9.0}],
             "signals_swing": []},
    "captured": {"signals": [{"symbol": "AAPL", "recommendation": "HOLD"}]},
    "paper_trades": {"trades": []}, "paper_account": {"has_account": False, "snapshot": None},
    "driver_approvals": {"grade": "B", "status": "no_trade"},
    "driver_performance": {"summary": {}, "trades": []},
}

def test_detail_fragment_includes_all_sections_and_date():
    html = eod.detail_fragment(SAMPLE)
    assert 'class="eod-report"' in html
    assert "2026-06-18" in html
    for heading in ("Captured Signals", "Paper Trades", "Scanner Signals", "Driver"):
        assert heading in html
    assert "AAPL" in html and "$SPX" in html
```

**Step 3: Implement**

```python
def detail_fragment(snap: dict) -> str:
    snap = snap or {}
    parts = [
        '<div class="eod-report">',
        f"<h1>EOD Detailed Report — {escape(str(snap.get('date', '')))}</h1>",
        f'<div class="meta">Generated {escape(str(snap.get("generated_at", "")))}</div>',
        "<h2>Captured Signals</h2>", captured_section(snap.get("captured")),
        "<h2>Paper Trades</h2>", paper_section(snap.get("paper_trades"), snap.get("paper_account")),
        "<h2>Scanner Signals</h2>", scanner_section(snap.get("scan")),
        "<h2>Driver — Trades & Performance</h2>",
        driver_section(snap.get("driver_approvals"), snap.get("driver_performance")),
        "</div>",
    ]
    return "".join(parts)
```

**Steps 2/4:** FAIL → PASS. **Step 5:** commit `feat(webgui): EOD detail_fragment`.

---

## Task 6: `summary_fragment(snapshot, detail_href)` — the rollup

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Failing test**

```python
def test_summary_fragment_tiles_and_detail_link():
    html = eod.summary_fragment(SAMPLE, "/eod/detail")
    assert 'class="eod-report"' in html
    assert 'href="/eod/detail"' in html
    # rollup counts present
    assert "1" in html  # 1 scanner signal, 1 captured signal
    assert "no_trade" in html or "B" in html  # driver grade/status surfaced

def test_summary_fragment_link_target_is_parameterized():
    assert 'href="detail.html"' in eod.summary_fragment(SAMPLE, "detail.html")
```

**Step 3: Implement** — compute counts defensively and render `tiles`:

```python
def _count(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return 0
        cur = cur.get(k)
    return len(cur) if isinstance(cur, list) else 0

def _tile(k, v, cls="") -> str:
    return f'<div class="tile"><div class="k">{escape(k)}</div>' \
           f'<div class="v {cls}">{v}</div></div>'

def summary_fragment(snap: dict, detail_href: str) -> str:
    snap = snap or {}
    scan = snap.get("scan") or {}
    n_scan = _count(scan, "signals_0dte") + _count(scan, "signals_swing")
    n_cap = _count(snap.get("captured") or {}, "signals")
    n_paper = _count(snap.get("paper_trades") or {}, "trades")
    appr = snap.get("driver_approvals") or {}
    perf = (snap.get("driver_performance") or {}).get("summary") or {}
    acct = (snap.get("paper_account") or {}).get("snapshot") or {}
    pnl_today = acct.get("realized_pnl_today")
    win = perf.get("win_rate")
    win_txt = f"{float(win) * 100:.0f}%" if _num(win) is not None else "—"
    tiles = "".join([
        _tile("Paper P&L (today)", _money(pnl_today), _pn_class(pnl_today)),
        _tile("Scanner signals", n_scan),
        _tile("Captured signals", n_cap),
        _tile("Paper trades", n_paper),
        _tile("Driver grade", escape(str(appr.get("grade") or "—"))),
        _tile("Driver status", escape(str(appr.get("status") or "—"))),
        _tile("Driver win rate", win_txt),
    ])
    return (
        '<div class="eod-report">'
        f"<h1>EOD Summary — {escape(str(snap.get('date', '')))}</h1>"
        f'<div class="meta">Generated {escape(str(snap.get("generated_at", "")))}</div>'
        f'<div class="tiles">{tiles}</div>'
        f'<p><a href="{escape(detail_href)}">View detailed report →</a></p>'
        "</div>"
    )
```

**Steps 2/4:** FAIL → PASS. **Step 5:** commit `feat(webgui): EOD summary_fragment rollup`.

---

## Task 7: Snapshot reader + archive helpers

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Failing tests**

```python
def test_archive_dates_sorts_newest_first(tmp_path):
    for d in ("2026-06-16", "2026-06-18", "2026-06-17"):
        (tmp_path / d).mkdir()
    (tmp_path / "not-a-date").mkdir()
    assert eod.archive_dates(tmp_path) == ["2026-06-18", "2026-06-17", "2026-06-16"]

def test_archive_dates_missing_dir_returns_empty(tmp_path):
    assert eod.archive_dates(tmp_path / "nope") == []

def test_write_archive_creates_both_files(tmp_path):
    paths = eod.write_archive(tmp_path, "2026-06-18", "<sum/>", "<det/>")
    assert paths["summary"].read_text(encoding="utf-8") == "<sum/>"
    assert paths["detail"].read_text(encoding="utf-8") == "<det/>"
    assert paths["summary"].parent.name == "2026-06-18"
```

**Step 3: Implement**

```python
import re
from pathlib import Path

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def archive_dates(root) -> list[str]:
    root = Path(root)
    if not root.is_dir():
        return []
    dates = [p.name for p in root.iterdir() if p.is_dir() and _DATE_RE.match(p.name)]
    return sorted(dates, reverse=True)

def write_archive(root, date: str, summary_doc: str, detail_doc: str) -> dict:
    day = Path(root) / date
    day.mkdir(parents=True, exist_ok=True)
    summ, det = day / "summary.html", day / "detail.html"
    summ.write_text(summary_doc, encoding="utf-8")
    det.write_text(detail_doc, encoding="utf-8")
    return {"summary": summ, "detail": det}
```

Also add the snapshot reader (reads live caches; tested via monkeypatch in Task 8):

```python
import bus_client

ARCHIVE_ROOT = Path(__file__).resolve().parents[1] / "data" / "eod"

def _ct_today() -> str:
    """Current CT trading date as YYYY-MM-DD (matches the rest of the app's CT clock)."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")

def read_snapshot() -> dict:
    """Snapshot the current caches into one dict for the builders."""
    import datetime as dt
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("America/Chicago"))
    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M CT"),
        "scan": bus_client.read("options:scan") or {},
        "captured": bus_client.read("options:captured") or {},
        "paper_trades": bus_client.read("options:paper_trades") or {},
        "paper_account": bus_client.read("options:paper_account") or {},
        "driver_approvals": bus_client.read("driver:approvals") or {},
        "driver_performance": bus_client.read("driver:performance") or {},
    }
```

> `import bus_client` at module top works because `webgui/conftest.py` and the
> server both put `webgui/` on `sys.path` (same as the other pages). If a bare
> `import bus_client` at module load breaks test collection, move it inside
> `read_snapshot()` — but the existing pages import it at top level, so top should
> be fine.

**Steps 2/4:** FAIL → PASS. **Step 5:** commit `feat(webgui): EOD snapshot reader + archive helpers`.

---

## Task 8: `generate()` — build + write one dated archive

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Failing test** (monkeypatch the snapshot + archive root)

```python
def test_generate_writes_standalone_docs_with_relative_link(tmp_path, monkeypatch):
    monkeypatch.setattr(eod, "read_snapshot", lambda: dict(SAMPLE))
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    out = eod.generate()
    assert out["date"] == "2026-06-18"
    summ = (tmp_path / "2026-06-18" / "summary.html").read_text(encoding="utf-8")
    det = (tmp_path / "2026-06-18" / "detail.html").read_text(encoding="utf-8")
    assert summ.startswith("<!DOCTYPE html>") and det.startswith("<!DOCTYPE html>")
    assert 'href="detail.html"' in summ          # file link is relative, not the route
    assert "AAPL" in det and "$SPX" in det
```

**Step 3: Implement**

```python
def generate() -> dict:
    """Snapshot caches, build both standalone docs, write the dated archive."""
    snap = read_snapshot()
    date = snap["date"]
    summary_doc = wrap_document(
        summary_fragment(snap, "detail.html"), EOD_CSS, f"EOD Summary {date}")
    detail_doc = wrap_document(
        detail_fragment(snap), EOD_CSS, f"EOD Detail {date}")
    write_archive(ARCHIVE_ROOT, date, summary_doc, detail_doc)
    return {"date": date}
```

**Steps 2/4:** FAIL → PASS. **Step 5:** commit `feat(webgui): EOD generate() writes dated archive`.

---

## Task 9: Thin `render()` + `render_detail()` page functions

**Files:** Modify `webgui/pages/eod.py`. (No new unit tests — smoke-verified by
screenshot; keep these thin so the logic stays in the tested builders.)

**Step 1–3: Implement** at the bottom of `eod.py`:

```python
from nicegui import ui  # import at top of file with the others

def render() -> None:
    """Summary page: action bar + archive list + in-app summary fragment."""
    ui.add_css(EOD_CSS)
    container = ui.column().classes("w-full gap-2")

    def _repaint():
        container.clear()
        with container:
            with ui.row().classes("items-center gap-2"):
                ui.button("Generate", icon="play_arrow", on_click=_on_generate)
                ui.button("Open summary file", icon="open_in_new",
                          on_click=lambda: ui.navigate.to(
                              f"/eod/file?date={_ct_today()}&which=summary", new_tab=True))
                ui.button("Open detail file", icon="open_in_new",
                          on_click=lambda: ui.navigate.to(
                              f"/eod/file?date={_ct_today()}&which=detail", new_tab=True))
            # Archive list
            dates = archive_dates(ARCHIVE_ROOT)
            if dates:
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label("Archive:").classes("opacity-60")
                    for d in dates:
                        ui.link(d, f"/eod/file?date={d}&which=summary").props("target=_blank")
            # In-app summary fragment (live caches)
            ui.html(summary_fragment(read_snapshot(), "/eod/detail"))

    def _on_generate():
        try:
            out = generate()
            ui.notify(f"EOD report generated for {out['date']}", type="positive")
        except Exception as e:                       # defensive: never crash the page
            ui.notify(f"Generate failed: {e}", type="negative")
        _repaint()

    _repaint()

def render_detail() -> None:
    """Detailed page: in-app detail fragment from live caches."""
    ui.add_css(EOD_CSS)
    ui.html(detail_fragment(read_snapshot()))
```

**Step 4:** Run the whole webgui suite to confirm nothing imports-broke:
`cd webgui && ..\.venv\Scripts\python -m pytest -q` → all green.

**Step 5:** commit `feat(webgui): EOD render() + render_detail() page functions`.

---

## Task 10: Wire routes, nav item, and the file-serving endpoint in `main.py`

**Files:**
- Modify: `webgui/main.py` (FLAT_NAV, two `@ui.page` routes, one `@app.get` file route)
- Modify: `webgui/tests/test_shell.py` (expected route set)

**Step 1: Write the failing shell test** — add `/eod` and `/eod/detail` to the
`expected` tuple in `test_shell.py:14-19`:

```python
    expected = (
        "/", "/options/paper", "/options/captured", "/options/portfolio",
        "/options/calculator", "/options/swing", "/options/gamma",
        "/options/simulator", "/sentiment", "/sentiment/rotation",
        "/trade", "/portfolio", "/driver", "/settings",
        "/eod", "/eod/detail",
    )
```

**Step 2: Run — Expected:** FAIL (`missing page route /eod`).

**Step 3: Implement in `main.py`:**

1. Add to `FLAT_NAV` (after Driver, before Settings) at `main.py:108-113`:
   ```python
       ("/eod", "EOD Report", "summarize"),
   ```

2. Add the two pages alongside the other `@ui.page` blocks (near `main.py:362`):
   ```python
   @ui.page("/eod")
   def _eod():
       with _layout("/eod", "EOD Report"):
           from pages import eod
           eod.render()

   @ui.page("/eod/detail")
   def _eod_detail():
       with _layout("/eod", "EOD Report — Detail"):
           from pages import eod
           eod.render_detail()
   ```
   (Use active="/eod" for both so the nav item highlights on the detail page too.)

3. Add the file-serving route next to `_serve_explain` (`main.py:72`), mirroring it
   (`HTMLResponse` is already imported there):
   ```python
   @app.get("/eod/file")
   def _serve_eod_file(date: str, which: str = "summary"):
       """Serve an archived EOD report file (summary.html / detail.html) raw,
       so its own <style> applies (NiceGUI's ui.html would strip it)."""
       import re
       from pathlib import Path
       from pages import eod
       if which not in ("summary", "detail") or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
           return HTMLResponse("<h1>Not found</h1>", status_code=404)
       path = Path(eod.ARCHIVE_ROOT) / date / f"{which}.html"
       if not path.is_file():
           return HTMLResponse(
               "<h1>No report for that date — click Generate first.</h1>",
               status_code=404)
       return HTMLResponse(path.read_text(encoding="utf-8"))
   ```

**Step 4: Run — Expected:** PASS.

Run: `cd webgui && ..\.venv\Scripts\python -m pytest -q` → full suite green.

**Step 5: Commit**

```bash
git add webgui/main.py webgui/tests/test_shell.py
git commit -m "feat(webgui): wire EOD Report nav item, routes, and file endpoint"
```

---

## Task 11: Browser smoke-verification

**Files:** none (verification only).

1. Ensure Memurai + proxy + the options/driver services + webgui are running
   (`start_all.bat`, or at minimum the webgui — the page degrades to "No data"
   sections when services are down, which is itself worth confirming).
2. Use the Claude Preview tool: start `webgui` (:8500), navigate to `/eod`.
3. Confirm: the **EOD Report** nav item appears (icon `summarize`); the summary
   tiles render; clicking **Generate** shows the success notify and the archive
   list gains today's date; **View detailed report** link reaches `/eod/detail`;
   **Open summary file** opens the standalone `/eod/file?...` in a new tab with its
   own styling intact. Screenshot each for the user.
4. If anything is off, use superpowers:systematic-debugging before patching.

(No commit — verification step.)

---

## Task 12: Update CLAUDE.md

**Files:** Modify `D:\WebGUI Trading with Schwab\CLAUDE.md`.

Add a `/eod` row to the **Routes** table and a short subsection documenting the EOD
report (pure-webgui aggregator, summary + detail fragments + `wrap_document`,
manual Generate → dated archive under `webgui/data/eod/`, `/eod/file` serving
route). Update the "Last updated" banner. Commit
`docs: document EOD Report page in CLAUDE.md`.

---

## Notes for the executor

- **DRY/YAGNI:** the fragment builders are the single source of truth — do not
  duplicate report layout in NiceGUI widgets. In-app and file both consume the
  same `summary_fragment`/`detail_fragment`; only the link target differs.
- **Defensive everywhere:** every builder tolerates `None`/missing keys and renders
  a "No data" note, never raises. `generate()` and the page wrap in try/except.
- **Run the webgui suite from inside `webgui/`** (`..\.venv\Scripts\python -m pytest -q`)
  — `conftest.py` sets `sys.path`. Target ~250 tests green (239 existing + the new
  `test_eod.py`).
- **Do not** add engine imports to the webgui — this page reads Redis only.
