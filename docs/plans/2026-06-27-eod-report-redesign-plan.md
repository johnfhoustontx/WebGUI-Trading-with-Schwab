# EoD Report Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild the `/eod` report into a navigable, verbose Daily/Weekly/MTD performance summary (per book) plus a detailed report with trade-type breakdowns.

**Architecture:** Pure-webgui (the page reads Redis caches and builds an HTML fragment + CSS; no app-engine imports). All new logic is pure, unit-tested builders in `webgui/pages/eod.py`. One small additive service change: `compute.driver_account_view()` also returns `closed_positions` so the driver book date-buckets symmetrically with the manual ledger.

**Tech Stack:** Python, NiceGUI (`ui.html` + `ui.add_css`), pytest. No JS — collapsible sections use native HTML `<details>`.

**Design:** `docs/plans/2026-06-27-eod-report-redesign-design.md`

**Conventions (this repo):**
- webgui tests: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_eod.py -q`
- options_svc tests: from repo root `.venv\Scripts\python -m pytest services\options_svc\tests\test_driver_account.py -q`
- The page must import only `nicegui` + `shared.bus` + `shared.contracts` (3-tier rule).
- Every builder is defensive: missing/None cache → a "No data" note, never raises.
- Commit per task with conventional prefixes.

---

## Task 1: Service — driver closed positions in `driver_account_view`

**Files:**
- Modify: `services/options_svc/compute.py` (`driver_account_view`, ~line 183-203)
- Test: `services/options_svc/tests/test_driver_account.py`

**Step 1: Write the failing test**

Add to `test_driver_account.py` (uses the existing `_fake_broker`/`_driver_signal` helpers + `tmp_path`/`monkeypatch` DRIVER_PAPER_DB pattern already in that file):

```python
def test_driver_account_view_includes_closed_positions(tmp_path, monkeypatch):
    """The view exposes CLOSED positions (with exit_ts + realized_pnl) so the EoD
    report can date-bucket the driver book like the manual ledger."""
    db = tmp_path / "driver.db"
    monkeypatch.setattr(compute, "DRIVER_PAPER_DB", db)
    compute.ensure_driver_account()
    # open one, then close it
    compute.open_driver_position(_driver_signal(), qty=1, broker=_fake_broker(1.50))
    import paper_account_db
    pos = paper_account_db.fetch_open_positions(db)[0]
    paper_account_db.update_position_mark(
        db, pos["position_id"], status="CLOSED", realized_pnl=42.0,
        exit_ts="2026-06-27T15:00:00")
    view = compute.driver_account_view()
    assert "closed_positions" in view
    closed = view["closed_positions"]
    assert len(closed) == 1 and closed[0]["status"] == "CLOSED"
    assert closed[0]["realized_pnl"] == 42.0
    # open list no longer contains it
    assert all(p["status"] == "OPEN" for p in view["positions"])
```

**Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest services\options_svc\tests\test_driver_account.py::test_driver_account_view_includes_closed_positions -q`
Expected: FAIL (`KeyError`/assert — `closed_positions` not present).

**Step 3: Write minimal implementation**

In `compute.driver_account_view`, after the `orders` fetch, add a `closed_positions` fetch and include it in the returned dict:

```python
    try:
        closed_positions = [p for p in paper_account_db.fetch_all_positions(DRIVER_PAPER_DB)
                            if (p.get("status") or "").upper() != "OPEN"]
    except Exception:
        closed_positions = []
    return {"snapshot": snapshot, "positions": positions, "orders": orders,
            "closed_positions": closed_positions, "has_account": has_driver_account()}
```

**Step 4: Run test to verify it passes**

Run: same as Step 2. Expected: PASS.
Then the whole file: `.venv\Scripts\python -m pytest services\options_svc\tests\test_driver_account.py -q` → all green.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_driver_account.py
git commit -m "feat(options_svc): driver_account_view returns closed_positions for EoD bucketing"
```

---

## Task 2: `normalize_trades` — one uniform trade shape for both books

The two books key fields differently (ledger: `entry_time`/`exit_time`/`entry_credit_total`/`trade_type`; driver: `entry_ts`/`exit_ts`/`entry_credit`+`quantity`, no `trade_type`). Normalize both into one shape so the period/breakdown builders are DRY.

**Files:**
- Modify: `webgui/pages/eod.py`
- Test: `webgui/tests/test_eod.py`

**Step 1: Write the failing test**

```python
def test_normalize_trades_ledger_and_driver():
    from pages.options import eod  # noqa
    led = eod.normalize_trades([{
        "symbol": "AMD", "strategy": "PCS", "trade_type": "SWING", "status": "OPEN",
        "entry_time": "2026-06-27T10:00:00+00:00", "exit_time": None,
        "realized_pnl": None, "entry_credit_total": 120.0,
    }], kind="ledger")
    assert led[0] == {
        "symbol": "AMD", "strategy": "PCS", "trade_type": "SWING", "status": "OPEN",
        "entry_date": "2026-06-27", "exit_date": None, "realized_pnl": None,
        "credit": 120.0}
    drv = eod.normalize_trades([{
        "symbol": "SPY", "strategy": "CCS", "status": "CLOSED",
        "entry_ts": "2026-06-26T14:00:00", "exit_ts": "2026-06-27T15:00:00",
        "realized_pnl": 42.0, "entry_credit": 1.5, "quantity": 2,
    }], kind="driver")
    assert drv[0]["entry_date"] == "2026-06-26"
    assert drv[0]["exit_date"] == "2026-06-27"
    assert drv[0]["realized_pnl"] == 42.0
    assert drv[0]["trade_type"] is None            # driver positions carry no horizon
    assert drv[0]["credit"] == 300.0               # 1.5 * qty 2 * 100
```

Note the import: `eod.py` currently lives at `webgui/pages/eod.py` (not under `options/`). The test imports it as `from pages import eod`. Verify the existing test file's import line and match it.

**Step 2: Run to verify it fails** — `AttributeError: normalize_trades`.

**Step 3: Implement**

```python
def _date_of(ts):
    """YYYY-MM-DD from an ISO timestamp string, or None."""
    s = str(ts or "")
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else None


def normalize_trades(raw, *, kind):
    """Map a book's raw trade dicts into one uniform shape:
    {symbol, strategy, trade_type, status, entry_date, exit_date, realized_pnl, credit}.
    ``kind`` = 'ledger' (manual paper_trades) | 'driver' (driver positions)."""
    out = []
    for t in raw or []:
        t = t or {}
        if kind == "driver":
            entry, exit_, credit = "entry_ts", "exit_ts", None
            qty = _num(t.get("quantity"), 1) or 1
            per = _num(t.get("entry_credit"))
            credit = round(per * qty * 100, 2) if per is not None else None
            trade_type = t.get("trade_type")  # normally absent on driver positions
        else:
            entry, exit_ = "entry_time", "exit_time"
            credit = _num(t.get("entry_credit_total"))
            trade_type = t.get("trade_type")
        out.append({
            "symbol": t.get("symbol"),
            "strategy": t.get("strategy"),
            "trade_type": trade_type,
            "status": t.get("status"),
            "entry_date": _date_of(t.get(entry)),
            "exit_date": _date_of(t.get(exit_)),
            "realized_pnl": _num(t.get("realized_pnl")),
            "credit": credit,
        })
    return out
```

**Step 4: Run to verify it passes.**

**Step 5: Commit** — `test(eod): normalize_trades for uniform two-book shape` (add both files).

---

## Task 3: `period_buckets` — Daily/Weekly/MTD aggregation

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Write the failing test**

```python
import datetime as _dt

def _nt(entry, exit_, pnl, status="CLOSED"):
    return {"symbol": "X", "strategy": "PCS", "trade_type": "SWING", "status": status,
            "entry_date": entry, "exit_date": exit_, "realized_pnl": pnl, "credit": 100.0}

def test_period_buckets_daily_weekly_mtd():
    from pages import eod
    today = _dt.date(2026, 6, 27)          # a Saturday; week-to-date = Mon 06-22..27
    trades = [
        _nt("2026-06-27", "2026-06-27", 50.0),     # opened+closed today
        _nt("2026-06-23", "2026-06-25", -20.0),    # closed this week (not today)
        _nt("2026-06-02", "2026-06-10", 200.0),    # closed this month (not this week)
        _nt("2026-05-30", "2026-05-31", 999.0),    # last month — excluded everywhere
        _nt("2026-06-26", None, None, status="OPEN"),  # open entry this week
    ]
    b = eod.period_buckets(trades, today)
    # realized is summed by EXIT date in range
    assert b["daily"]["realized"] == 50.0
    assert b["weekly"]["realized"] == 50.0 + (-20.0)
    assert b["mtd"]["realized"] == 50.0 - 20.0 + 200.0
    # closed counts (exit in range)
    assert b["daily"]["closed"] == 1
    assert b["weekly"]["closed"] == 2
    assert b["mtd"]["closed"] == 3
    # wins/losses + win rate (weekly: 1 win, 1 loss)
    assert b["weekly"]["wins"] == 1 and b["weekly"]["losses"] == 1
    assert b["weekly"]["win_rate"] == 0.5
    # opened counts (entry in range): today has the 06-27 trade; week adds 06-23 + 06-26
    assert b["daily"]["opened"] == 1
    assert b["weekly"]["opened"] == 3      # 06-27, 06-23, 06-26
    assert b["mtd"]["opened"] == 4         # + 06-02
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

```python
def _period_ranges(today):
    """(start_date, end_date) inclusive for daily / weekly(WTD) / mtd."""
    return {
        "daily": (today, today),
        "weekly": (today - dt.timedelta(days=today.weekday()), today),  # Monday→today
        "mtd": (today.replace(day=1), today),
    }


def _in_range(date_str, lo, hi):
    d = _parse_date(date_str)
    return d is not None and lo <= d <= hi


def _parse_date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def period_buckets(norm_trades, today):
    """{daily, weekly, mtd} each {realized, closed, wins, losses, win_rate, opened,
    credit}. Realized/closed bucket by EXIT date; opened/credit by ENTRY date."""
    out = {}
    for name, (lo, hi) in _period_ranges(today).items():
        realized = wins = losses = closed = opened = 0.0
        credit = 0.0
        for t in norm_trades or []:
            if _in_range(t.get("exit_date"), lo, hi):
                closed += 1
                pnl = _num(t.get("realized_pnl"))
                if pnl is not None:
                    realized += pnl
                    if pnl > 0:
                        wins += 1
                    elif pnl < 0:
                        losses += 1
            if _in_range(t.get("entry_date"), lo, hi):
                opened += 1
                c = _num(t.get("credit"))
                if c is not None:
                    credit += c
        decided = wins + losses
        out[name] = {
            "realized": round(realized, 2), "closed": int(closed),
            "wins": int(wins), "losses": int(losses),
            "win_rate": (wins / decided) if decided else None,
            "opened": int(opened), "credit": round(credit, 2),
        }
    return out
```

**Step 4: Run to verify it passes.**

**Step 5: Commit** — `feat(eod): period_buckets daily/weekly/mtd aggregation`.

---

## Task 4: `breakdown_rows` — group by strategy / trade_type / status

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Write the failing test**

```python
def test_breakdown_rows_by_strategy():
    from pages import eod
    trades = [
        {"strategy": "PCS", "status": "OPEN", "realized_pnl": None},
        {"strategy": "PCS", "status": "CLOSED", "realized_pnl": 30.0},
        {"strategy": "CCS", "status": "CLOSED", "realized_pnl": -10.0},
    ]
    rows = eod.breakdown_rows(trades, "strategy")
    by = {r["group"]: r for r in rows}
    assert by["PCS"]["trades"] == 2 and by["PCS"]["open"] == 1 and by["PCS"]["closed"] == 1
    assert by["PCS"]["realized"] == 30.0
    assert by["CCS"]["realized"] == -10.0
    # missing key → grouped under "—", not dropped
    rows2 = eod.breakdown_rows([{"status": "OPEN"}], "trade_type")
    assert rows2[0]["group"] == "—"
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

```python
def breakdown_rows(norm_trades, key):
    """Group normalized trades by ``key`` ('strategy'|'trade_type'|'status').
    Each row: {group, trades, open, closed, realized, wins, losses, win_rate}.
    Sorted by trade count desc. A missing/None key value groups under '—'."""
    groups = {}
    for t in norm_trades or []:
        g = t.get(key) or "—"
        b = groups.setdefault(g, {"group": g, "trades": 0, "open": 0, "closed": 0,
                                  "realized": 0.0, "wins": 0, "losses": 0})
        b["trades"] += 1
        st = (t.get("status") or "").upper()
        if st == "OPEN":
            b["open"] += 1
        else:
            b["closed"] += 1
        pnl = _num(t.get("realized_pnl"))
        if pnl is not None:
            b["realized"] += pnl
            if pnl > 0:
                b["wins"] += 1
            elif pnl < 0:
                b["losses"] += 1
    rows = []
    for b in groups.values():
        decided = b["wins"] + b["losses"]
        b["realized"] = round(b["realized"], 2)
        b["win_rate"] = (b["wins"] / decided) if decided else None
        rows.append(b)
    return sorted(rows, key=lambda r: r["trades"], reverse=True)
```

**Step 4: Run to verify it passes.**

**Step 5: Commit** — `feat(eod): breakdown_rows by strategy/trade_type/status`.

---

## Task 5: Navigation + formatting helpers (`toc`, `details_section`, `_pct`, period/breakdown HTML)

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Write the failing test**

```python
def test_toc_and_details_section():
    from pages import eod
    toc = eod.toc([("perf", "Performance"), ("brk", "Breakdowns")])
    assert 'href="#perf"' in toc and "Performance" in toc
    sec = eod.details_section("perf", "Performance", "<p>body</p>")
    assert 'id="perf"' in sec
    assert "<details" in sec and "<summary>" in sec and "Performance" in sec
    assert "<p>body</p>" in sec

def test_pct_helper():
    from pages import eod
    assert eod._pct(0.5) == "50%"
    assert eod._pct(None) == "—"

def test_performance_table_html_renders_periods():
    from pages import eod
    buckets = {
        "daily": {"realized": 50.0, "closed": 1, "wins": 1, "losses": 0,
                  "win_rate": 1.0, "opened": 1, "credit": 100.0},
        "weekly": {"realized": 30.0, "closed": 2, "wins": 1, "losses": 1,
                   "win_rate": 0.5, "opened": 3, "credit": 300.0},
        "mtd": {"realized": 230.0, "closed": 3, "wins": 2, "losses": 1,
                "win_rate": 0.667, "opened": 4, "credit": 400.0},
    }
    html = eod.performance_table_html(buckets)
    assert "Daily" in html and "Weekly" in html and "MTD" in html
    assert "$50.00" in html and "$230.00" in html
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

```python
def _pct(frac):
    n = _num(frac)
    return "—" if n is None else f"{n * 100:.0f}%"


def toc(sections):
    """An anchor-link table of contents: sections = [(id, title), ...]."""
    links = " · ".join(f'<a href="#{escape(i)}">{escape(t)}</a>' for i, t in sections)
    return f'<nav class="eod-toc">{links}</nav>'


def details_section(anchor, title, body, *, open=True):
    """A collapsible section (native <details>, no JS — works in-app + exported)."""
    op = " open" if open else ""
    return (f'<details id="{escape(anchor)}" class="eod-sec"{op}>'
            f'<summary>{escape(title)}</summary>{body}</details>')


_PERIOD_LABELS = [("daily", "Daily"), ("weekly", "Weekly (WTD)"), ("mtd", "MTD")]


def performance_table_html(buckets):
    """Daily/Weekly/MTD rows: Realized | Closed (W-L) | Win% | Opened | Credit."""
    buckets = buckets or {}
    rows = []
    for key, label in _PERIOD_LABELS:
        b = buckets.get(key) or {}
        wl = f'{int(b.get("wins", 0))}-{int(b.get("losses", 0))}'
        rows.append(
            "<tr>"
            f"<td><b>{escape(label)}</b></td>"
            f'<td class="{_pn_class(b.get("realized"))}">{_money(b.get("realized"))}</td>'
            f'<td>{int(b.get("closed", 0))} ({wl})</td>'
            f'<td>{_pct(b.get("win_rate"))}</td>'
            f'<td>{int(b.get("opened", 0))}</td>'
            f'<td>{_money(b.get("credit"))}</td>'
            "</tr>")
    return _table(["Period", "Realized P&L", "Closed (W-L)", "Win %", "Opened",
                   "Credit collected"], rows, empty="No activity.")


def breakdown_table_html(rows):
    """A breakdown table: Group | Trades | Open | Closed | Realized | Win%."""
    body = []
    for r in rows or []:
        body.append(
            "<tr>"
            f"<td>{escape(str(r.get('group', '—')))}</td>"
            f"<td>{int(r.get('trades', 0))}</td>"
            f"<td>{int(r.get('open', 0))}</td>"
            f"<td>{int(r.get('closed', 0))}</td>"
            f'<td class="{_pn_class(r.get("realized"))}">{_money(r.get("realized"))}</td>'
            f'<td>{_pct(r.get("win_rate"))}</td>'
            "</tr>")
    return _table(["Group", "Trades", "Open", "Closed", "Realized P&L", "Win %"],
                  body, empty="No trades.")
```

**Step 4: Run to verify it passes.**

**Step 5: Commit** — `feat(eod): toc + details-section + performance/breakdown table html`.

---

## Task 6: Rewire `read_snapshot` + `summary_fragment` + `detail_fragment`

Assemble the new structure. Keep the existing section builders (captured/scanner/driver) reusable inside `<details>`.

**Files:** Modify `webgui/pages/eod.py`; Test `webgui/tests/test_eod.py`.

**Step 1: Write the failing tests**

```python
def test_read_snapshot_includes_driver_views(monkeypatch):
    from pages import eod
    monkeypatch.setattr(eod.bus_client, "read", lambda k: {"_k": k})
    snap = eod.read_snapshot()
    assert "driver_paper_account" in snap and "driver_paper_perf" in snap

def test_summary_fragment_has_performance_and_toc():
    from pages import eod
    snap = {
        "date": "2026-06-27", "generated_at": "x",
        "paper_trades": {"trades": [
            {"symbol": "AMD", "strategy": "PCS", "trade_type": "SWING", "status": "OPEN",
             "entry_time": "2026-06-27T10:00:00", "exit_time": None,
             "realized_pnl": None, "entry_credit_total": 120.0}]},
        "driver_paper_account": {"has_account": True, "snapshot": {"equity": 25000.0},
                                 "positions": [], "closed_positions": []},
    }
    html = eod.summary_fragment(snap, "/eod/detail", today=__import__("datetime").date(2026, 6, 27))
    assert "Performance" in html and "Daily" in html
    assert "eod-toc" in html
    assert "Manual paper" in html and "Driver" in html   # both books labelled

def test_detail_fragment_has_breakdowns():
    from pages import eod
    snap = {"date": "2026-06-27", "generated_at": "x",
            "paper_trades": {"trades": [
                {"symbol": "AMD", "strategy": "PCS", "trade_type": "SWING",
                 "status": "OPEN", "entry_time": "2026-06-27T10:00:00",
                 "realized_pnl": None, "entry_credit_total": 120.0}]},
            "driver_paper_account": {"has_account": True, "snapshot": {},
                                     "positions": [], "closed_positions": []}}
    html = eod.detail_fragment(snap, today=__import__("datetime").date(2026, 6, 27))
    assert "By strategy" in html and "By 0-DTE / Swing" in html and "By status" in html
    assert "<details" in html
```

**Step 2: Run to verify it fails.**

**Step 3: Implement**

- `read_snapshot()` — add:
  ```python
      "driver_paper_account": bus_client.read("options:driver_paper_account") or {},
      "driver_paper_perf": bus_client.read("options:driver_paper_perf") or {},
  ```
- Add a private assembler that builds both books' normalized trades + buckets:
  ```python
  def _books(snap):
      """[(label, kind, norm_trades, snapshot)] for each book."""
      led = normalize_trades((snap.get("paper_trades") or {}).get("trades"), kind="ledger")
      dacc = snap.get("driver_paper_account") or {}
      drv_raw = list(dacc.get("positions") or []) + list(dacc.get("closed_positions") or [])
      drv = normalize_trades(drv_raw, kind="driver")
      return [
          ("Manual paper", "ledger", led, (snap.get("paper_account") or {}).get("snapshot")),
          ("Driver", "driver", drv, dacc.get("snapshot")),
      ]
  ```
- `summary_fragment(snap, detail_href, today=None)` — `today = today or dt.datetime.now(_CT).date()`. Build: header → `toc([...])` → for each book a `details_section` containing `performance_table_html(period_buckets(norm, today))` + a now-line from the snapshot → activity tiles (scanner/captured counts) → detail link. Label each book section with its name ("Manual paper", "Driver").
- `detail_fragment(snap, today=None)` — header → TOC → per-book performance (as summary) → per-book **breakdowns**: `details_section("brk-strategy", "By strategy", breakdown_table_html(breakdown_rows(norm, "strategy")))`, same for `"By 0-DTE / Swing"` (`trade_type`) and `"By status"` (`status`) → full trade tables (reuse existing `paper_section`/`captured_section`/`scanner_section`/`driver_section` inside `<details>`).
- Keep `summary_fragment`/`detail_fragment` BACK-COMPATIBLE for `generate()` (which calls them) — `today` defaults to now.

**Step 4: Run to verify it passes** — and the whole file: `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_eod.py -q`.

**Step 5: Commit** — `feat(eod): verbose perf summary + trade-type breakdowns + TOC nav` (add eod.py + test_eod.py).

---

## Task 7: CSS — TOC, `<details>`, period tiles

**Files:** Modify `webgui/pages/eod.py` (`EOD_CSS`).

**Step 1:** No new test (visual). Append rules to `EOD_CSS`:

```css
.eod-report .eod-toc { margin: .4rem 0 .8rem; font-size: .85rem; opacity: .85; }
.eod-report .eod-toc a { margin-right: .2rem; }
.eod-report details.eod-sec { margin: .5rem 0; border: 1px solid rgba(255,255,255,.10);
    border-radius: 10px; padding: .2rem .7rem; background: rgba(255,255,255,.03); }
.eod-report details.eod-sec > summary { cursor: pointer; font-size: 1.0rem;
    font-weight: 600; padding: .35rem 0; opacity: .9; }
.eod-report details.eod-sec[open] > summary { border-bottom: 1px solid rgba(255,255,255,.08);
    margin-bottom: .3rem; }
.eod-report .book-now { opacity: .8; font-size: .82rem; margin: .25rem 0 .4rem; }
```

**Step 2:** Run the eod test file to confirm nothing broke (CSS is a string constant): `cd webgui && ..\.venv\Scripts\python -m pytest tests/test_eod.py -q` → green.

**Step 3: Commit** — `style(eod): TOC + collapsible section + book-now styling`.

---

## Task 8: Full suite + live verification

**Step 1:** Run the whole webgui suite: `cd webgui && ..\.venv\Scripts\python -m pytest -q`. Expected: all green (≥ prior 527 + new eod tests).

**Step 2:** Run options_svc driver tests: from repo root `.venv\Scripts\python -m pytest services\options_svc\tests\test_driver_account.py -q` → green.

**Step 3: Restart options_svc** (to load the `closed_positions` change) — the running service is stale:
- Kill the PID listening on :8211, relaunch `python services\options_svc\app.py` (background), wait for :8211.

**Step 4: Verify live** via the preview tool:
- Restart the webgui preview (`preview_start webgui` after stopping the stale one).
- Navigate to `/eod`; assert via `preview_eval`: the summary contains "Performance", "Daily/Weekly/MTD", a TOC (`.eod-toc`), and both "Manual paper" + "Driver" book sections.
- Navigate to `/eod/detail`; assert it contains "By strategy"/"By 0-DTE / Swing"/"By status" and `<details>` sections; toggle a `<summary>` and confirm collapse.
- Click **Generate**; open `/eod/file?date=<today>&which=summary` and `&which=detail` in a tab and confirm the standalone files render the new layout (TOC links jump; `<details>` collapse — proving the no-JS nav works in the exported HTML).

**Step 5:** Update `CLAUDE.md` route table entry for `/eod` + `/eod/detail` to describe the new Daily/Weekly/MTD performance + breakdowns + TOC/collapsible nav. Update the EoD section.

**Step 6: Commit** — `docs(claude): EoD report redesign — perf periods + breakdowns + nav`.

---

## Notes / gotchas

- **`ui.html` strips `<style>`** — CSS goes through `ui.add_css(EOD_CSS)` in-app and is inlined by `wrap_document` for export (already the pattern). `<details>`/`<summary>` are NOT stripped (only `<style>`/`<iframe>` are).
- **Driver positions lack `trade_type`** — the "By 0-DTE / Swing" breakdown groups them under "—". That's expected; note it in the section (the driver only trades scanner spreads). Manual ledger trades have real `trade_type`.
- **0 closed trades today** — realized columns read `$0.00`/`—` and `win_rate` is None → renders "—". This is correct, not a bug; the periods fill in as trades close.
- **Two manual books** — the manual *ledger* (`paper_trades`, the user's trades) drives the manual performance/breakdowns; the manual *engine account* snapshot (`paper_account`) is only used for the optional now-line equity/session figures. Don't conflate them.
- **Defensive** — every builder tolerates None/empty; `read_snapshot` already `or {}`-guards each read.
