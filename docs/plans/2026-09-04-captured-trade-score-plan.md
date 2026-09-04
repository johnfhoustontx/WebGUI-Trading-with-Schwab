# Captured-Trade Score Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A Daily / Weekly (WTD) / MTD performance section for **captured
signals** in the EOD report, scored from 2026-09-01, backdated from data already
in `signals.db`.

**Architecture:** `options_svc` publishes normalised outcome ROWS over a bounded
date window to `cache:options:captured_perf`; Tier-1 `eod.py` feeds them to the
`period_buckets()` / `performance_table_html()` pair that already renders the
same table for the Paper Ledger and Claude books. No new aggregation, no new
recording, and no SQLite in Tier 1.

**Tech Stack:** Python 3.11, SQLite (`signals.db`), Redis via `shared.bus`, pytest.

**Design:** [2026-09-04-captured-trade-score-design.md](2026-09-04-captured-trade-score-design.md)

**Baselines (measured 2026-09-04):** `webgui/tests/test_eod.py` **34 passed**;
`services/options_svc` — see §Task 5 (long run; measure before starting).

**Test commands** (the venv lives in the main checkout; a worktree has none):

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/ev-formula-app-application-a55575/webgui" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest tests/test_eod.py -q
```

```bash
cd "D:/WebGUI Trading with Schwab/.claude/worktrees/ev-formula-app-application-a55575" && "D:/WebGUI Trading with Schwab/.venv/Scripts/python.exe" -m pytest services/options_svc -q
```

---

### Task 1: A date-RANGE outcome reader

`get_outcomes_for_date` takes one day and returns no open date, so it cannot feed
a weekly or monthly row. This adds its range sibling, returning the two extra
columns the score needs: `first_seen_ts` (the open date) and `scanner_type`.

**Files:**
- Modify: `options-scanner/signal_db.py` (beside `get_outcomes_for_date`, ~line 384)
- Test: `options-scanner/tests/test_signal_db.py`

**Step 1: Write the failing test**

```python
def test_get_outcomes_in_range_spans_dates_and_carries_the_open_date(tmp_path):
    """The weekly/monthly score needs BOTH dates: outcomes bucket by close date,
    opened counts by the date the signal was first seen."""
    db = tmp_path / "signals.db"
    signal_db.init_schema(db_path=db)
    # two signals, opened 08-31 and 09-02, closed 09-01 and 09-03
    for sid, seen, credit in (("a", "2026-08-31T10:00:00-05:00", 1.00),
                              ("b", "2026-09-02T11:00:00-05:00", 2.00)):
        signal_db.insert_signal({**_MIN_SIGNAL, "signal_id": sid,
                                 "first_seen_ts": seen, "entry_credit": credit,
                                 "scanner_type": "0DTE"}, db_path=db)
    signal_db.close_signal_manually("a", 0.50, "MONEY_STOP", db_path=db,
                                    close_ts=_ct("2026-09-01T14:00:00"))
    signal_db.close_signal_manually("b", 0.50, "TIME_STOP", db_path=db,
                                    close_ts=_ct("2026-09-03T14:00:00"))

    rows = signal_db.get_outcomes_in_range("2026-09-01", "2026-09-03", db_path=db)
    assert [r["signal_id"] for r in rows] == ["a", "b"]          # close order
    assert rows[0]["first_seen_ts"].startswith("2026-08-31")     # BEFORE the window
    assert rows[0]["scanner_type"] == "0DTE"
    assert rows[0]["realized_pnl"] == 50.0                       # (1.00-0.50)*100


def test_get_outcomes_in_range_is_inclusive_at_both_ends(tmp_path):
    """A half-open range would silently drop the newest day - the one the Daily
    row is made of."""
    db = _db_with_close(tmp_path, close_date="2026-09-03")
    assert len(signal_db.get_outcomes_in_range("2026-09-03", "2026-09-03", db_path=db)) == 1
    assert signal_db.get_outcomes_in_range("2026-09-04", "2026-09-05", db_path=db) == []
```

**Step 2: Run it and watch it fail**

Expected: `AttributeError: module 'signal_db' has no attribute 'get_outcomes_in_range'`.

**Step 3: Implement**

```python
def get_outcomes_in_range(lo_iso, hi_iso, db_path=DEFAULT_DB_PATH):
    """Closed-signal outcomes with ``lo_iso <= close_date <= hi_iso``, oldest first.

    The range sibling of ``get_outcomes_for_date``, and it returns two columns
    that one does not: ``first_seen_ts`` (the score's "opened" date, which may
    fall BEFORE the window - a signal opened in August can close in September)
    and ``scanner_type`` (0DTE vs SWING).

    Both ends are INCLUSIVE: a half-open range would drop the newest day, which
    is exactly the day the Daily row is made of.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute("""
            SELECT o.signal_id        AS signal_id,
                   s.symbol           AS symbol,
                   s.strategy         AS strategy,
                   s.scanner_type     AS scanner_type,
                   s.first_seen_ts    AS first_seen_ts,
                   s.entry_credit     AS entry_credit,
                   o.exit_value       AS exit_value,
                   o.realized_pnl     AS realized_pnl,
                   o.exit_reason      AS exit_reason,
                   o.close_date       AS close_date,
                   o.close_ts         AS close_ts
            FROM signal_outcomes o
            JOIN signals s ON s.signal_id = o.signal_id
            WHERE o.close_date >= ? AND o.close_date <= ?
            ORDER BY o.close_ts ASC
        """, (str(lo_iso), str(hi_iso)))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
```

**Step 4: Run the test** — expect PASS. Then run the whole
`options-scanner/tests/test_signal_db.py` file to be sure nothing else moved.

**Step 5: Commit**

```bash
git add options-scanner/signal_db.py options-scanner/tests/test_signal_db.py
git commit -m "feat(signal_db): a date-range outcome reader carrying the open date"
```

---

### Task 2: The window, and the row builder

**Files:**
- Modify: `services/options_svc/compute.py` (beside `captured_closed_today`, ~line 1597)
- Test: `services/options_svc/tests/test_captured_performance.py` (create)

**Step 1: Write the failing tests** — the window logic is the part with a trap in it.

```python
import datetime as dt
from options_svc import compute


def test_the_window_is_the_EARLIER_of_the_week_and_month_start():
    """⚠ The trap. Month-to-date looks like the right bound because MTD is the
    widest ROW - but on 1 October the WTD row starts Monday 28 September, BEFORE
    the month start. A month-to-date window would return no rows for 28-30
    September and the weekly row would silently under-count, on the first days of
    every month, with nothing on screen to say so."""
    # Thu 2026-10-01; its week began Mon 2026-09-28.
    lo, hi = compute.captured_score_window(dt.date(2026, 10, 1))
    assert lo == dt.date(2026, 9, 28)
    assert hi == dt.date(2026, 10, 1)


def test_the_window_is_the_month_start_when_that_is_earlier():
    # Wed 2026-09-16; its week began Mon 09-14, so the month start wins.
    lo, hi = compute.captured_score_window(dt.date(2026, 9, 16))
    assert lo == dt.date(2026, 9, 1)


def test_the_window_never_reaches_before_the_epoch():
    """868 outcomes go back to 2026-06-15 and none of them are part of this
    score. The period windows never reach them, and this is what guarantees it."""
    lo, _hi = compute.captured_score_window(dt.date(2026, 9, 2))
    assert lo >= compute.CAPTURED_SCORE_EPOCH == dt.date(2026, 9, 1)


def test_rows_carry_the_dollars_on_a_ONE_CONTRACT_basis():
    """``close_signal_manually`` computes (entry_credit - exit) * 100, so the
    credit must be on the same basis or the two columns describe different
    position sizes. A captured signal is never sized - one contract is the only
    honest basis either has."""
    raw = [{"symbol": "SPY", "strategy": "PCS", "scanner_type": "0DTE",
            "first_seen_ts": "2026-09-02T10:00:00-05:00",
            "close_ts": "2026-09-03T14:00:00-05:00", "close_date": "2026-09-03",
            "entry_credit": 0.55, "realized_pnl": 25.0}]
    row = compute.captured_perf_rows(raw)[0]
    assert row["entry_credit_total"] == 55.0        # 0.55 * 100, one contract
    assert row["realized_pnl"] == 25.0
    assert row["trade_type"] == "0DTE"


def test_rows_degrade_rather_than_raise_on_a_malformed_outcome():
    """This feeds a nightly report; one bad row must not cost the section."""
    assert compute.captured_perf_rows(None) == []
    assert compute.captured_perf_rows([None, "nonsense", {}]) == [
        r for r in compute.captured_perf_rows([{}])]
```

**Step 2: Run and watch them fail.**

**Step 3: Implement**

```python
# The score's inception. Nothing before this date is part of it: signal_outcomes
# reaches back to 2026-06-15, from a period whose captures predate the
# regular-hours recorder gate. The period windows never reach it anyway - this
# constant is what guarantees they cannot.
CAPTURED_SCORE_EPOCH = _dt.date(2026, 9, 1)


def captured_score_window(today):
    """``(lo, hi)`` inclusive dates the score must read to fill Daily/Weekly/MTD.

    ⚠ The earlier of the WEEK start and the MONTH start, not month-to-date. MTD
    is the widest ROW, which makes month-to-date look like the right bound - but
    on 1 October the WTD row starts Monday 28 September, before the month began.
    Bounding at the month start would drop 28-30 September and under-count the
    weekly row on the first days of every month, silently.

    Floored at ``CAPTURED_SCORE_EPOCH``. The width is what bounds the payload:
    at most ~5 weeks of closes.
    """
    month_start = today.replace(day=1)
    week_start = today - _dt.timedelta(days=today.weekday())   # Monday
    return max(min(month_start, week_start), CAPTURED_SCORE_EPOCH), today


def captured_perf_rows(raw):
    """Outcome rows -> the shape ``eod.normalize_trades(kind="captured")`` reads.

    ``entry_credit_total`` is ``entry_credit * 100`` - ONE contract, matching
    ``close_signal_manually``'s ``(entry_credit - exit_value) * 100``. A captured
    signal is never sized, so one contract is the only basis either number has,
    and the two must share it or the columns describe different positions.

    Total over a malformed row: this feeds a nightly report, and one bad row must
    not cost the whole section.
    """
    out = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        credit = _num(r.get("entry_credit"))
        out.append({
            "symbol": r.get("symbol"),
            "strategy": r.get("strategy"),
            "trade_type": r.get("scanner_type"),
            "status": "CLOSED",
            "first_seen_ts": r.get("first_seen_ts"),
            "close_ts": r.get("close_ts") or r.get("close_date"),
            "realized_pnl": _num(r.get("realized_pnl")),
            "entry_credit_total": (round(credit * 100.0, 2)
                                   if credit is not None else None),
            "exit_reason": r.get("exit_reason"),
        })
    return out


def captured_performance() -> dict:
    """The captured score's rows + its window. Defensive -> empty on any failure."""
    import signal_db

    today = _dt.datetime.now(_PROJ_CT_TZ).date()
    lo, hi = captured_score_window(today)
    try:
        raw = signal_db.get_outcomes_in_range(lo.isoformat(), hi.isoformat())
    except Exception:
        log.exception("captured_performance read degraded -> empty")
        raw = []
    return {"rows": captured_perf_rows(raw),
            "window": {"start": lo.isoformat(), "end": hi.isoformat()}}
```

⚠ Use the module's existing `_num` helper; do not add another. If `compute.py`
has no module-level `_num`, use `float_or`-style coercion already present there.

**Step 4: Run the new test file** — expect PASS.

**Step 5: Commit**

```bash
git add services/options_svc/compute.py services/options_svc/tests/test_captured_performance.py
git commit -m "feat(options_svc): the captured score's window and row builder"
```

---

### Task 3: Publish it

**Files:**
- Modify: `services/options_svc/handlers.py` — constants (~line 182), a publisher
  beside `publish_captured_closed` (~line 773), and the shared manage path (~line 837)
- Modify: `services/options_svc/scheduler.py:550` (startup publish)
- Test: `services/options_svc/tests/test_captured_performance.py`

**Step 1: Write the failing test**

```python
def test_the_manage_cycle_publishes_the_score_beside_the_closed_view():
    """It rides the path that already republishes the captured views, so a close
    updates the score in the same breath it updates the ledger. A score published
    on its own schedule could disagree with the closed-today view beside it."""
    import inspect
    from options_svc import handlers
    src = inspect.getsource(handlers.run_captured_manage)
    assert "publish_captured_closed(bus)" in src
    assert "publish_captured_performance(bus)" in src


def test_the_published_view_carries_rows_and_its_window(monkeypatch):
    from shared.bus import Bus
    from options_svc import handlers
    monkeypatch.setattr(handlers.compute, "captured_performance",
                        lambda: {"rows": [{"symbol": "SPY"}],
                                 "window": {"start": "2026-09-01", "end": "2026-09-04"}})
    bus = Bus(fake=True)
    handlers.publish_captured_performance(bus)
    payload = bus.cache_get(handlers.CACHE_CAPTURED_PERF).payload
    assert payload["rows"] == [{"symbol": "SPY"}]
    assert payload["window"]["start"] == "2026-09-01"
```

**Step 2: Run and watch it fail.**

**Step 3: Implement** — mirror `publish_captured_closed` exactly:

```python
CACHE_CAPTURED_PERF = "cache:options:captured_perf"
EVENT_CAPTURED_PERF = "events:options:captured_perf"


def publish_captured_performance(bus) -> None:
    """Publish the captured score's rows (``cache:options:captured_perf``).

    Read-only and published regardless of the auto-close toggle, for the same
    reason ``publish_captured_closed`` is: MANUAL closes land in
    ``signal_outcomes`` too. ``compute.captured_performance`` is fully defensive.
    """
    data = compute.captured_performance()
    version = bus.cache_set(CACHE_CAPTURED_PERF, data)
    bus.publish(EVENT_CAPTURED_PERF, {"version": version})
```

Add `publish_captured_performance(bus)` directly after the
`publish_captured_closed(bus)` line in `run_captured_manage`, and after the
startup publish in `scheduler.py:550` (same `run_in_executor` + `try/except`
shape as its neighbour).

**Step 4: Run `services/options_svc` in full** — compare the failing SET.

**Step 5: Commit**

```bash
git add services/options_svc/handlers.py services/options_svc/scheduler.py services/options_svc/tests/test_captured_performance.py
git commit -m "feat(options_svc): publish the captured score beside the closed view"
```

---

### Task 4: The third book in the EOD report

**Files:**
- Modify: `webgui/pages/eod.py` — `normalize_trades` (~line 249), `_books` (~line 426), the snapshot reader (~line 595)
- Test: `webgui/tests/test_eod.py`

**Step 1: Write the failing tests**

```python
def test_normalize_trades_reads_the_captured_shape():
    rows = eod.normalize_trades([{
        "symbol": "SPY", "strategy": "PCS", "trade_type": "0DTE",
        "status": "CLOSED", "first_seen_ts": "2026-09-02T10:00:00-05:00",
        "close_ts": "2026-09-03T14:00:00-05:00",
        "realized_pnl": 25.0, "entry_credit_total": 55.0}], kind="captured")
    assert rows[0]["entry_date"] == "2026-09-02"
    assert rows[0]["exit_date"] == "2026-09-03"
    assert rows[0]["realized_pnl"] == 25.0
    assert rows[0]["credit"] == 55.0


def test_captured_is_the_third_book_and_reuses_the_shared_table():
    """No new aggregation: the section is period_buckets + the same table the
    other two books render, so all three cannot disagree about what a period is."""
    snap = {"captured_perf": {"rows": [{
        "symbol": "SPY", "strategy": "PCS", "trade_type": "0DTE",
        "status": "CLOSED", "first_seen_ts": "2026-09-02T10:00:00-05:00",
        "close_ts": "2026-09-03T14:00:00-05:00",
        "realized_pnl": 25.0, "entry_credit_total": 55.0}]}}
    labels = [label for label, _norm, _now in eod._books(snap)]
    assert labels == ["Manual paper", "Driver", "Captured signals"]
    norm = dict(zip(labels, [n for _l, n, _s in eod._books(snap)]))["Captured signals"]
    buckets = eod.period_buckets(norm, dt.date(2026, 9, 3))
    assert buckets["daily"]["realized"] == 25.0
    assert buckets["mtd"]["closed"] == 1


def test_the_captured_section_says_the_dollars_are_one_contract():
    """Without it, -$1,251 reads as an account loss. These signals were tracked,
    never sized, and never traded."""
    _toc, html = eod.performance_sections({"captured_perf": {"rows": []}},
                                          dt.date(2026, 9, 4))
    low = html.lower()
    assert "one contract" in low
    assert "not" in low and ("traded" in low or "taken" in low)
```

**Step 2: Run and watch them fail.**

**Step 3: Implement**

In `normalize_trades`, add the branch:

```python
        elif kind == "captured":
            # Dates come from the signal's own timestamps, so ``_date_of`` does
            # the same job it does for the other two books. The credit is
            # already ONE-CONTRACT dollars (see compute.captured_perf_rows).
            entry, exit_ = "first_seen_ts", "close_ts"
            credit = _num(t.get("entry_credit_total"))
            trade_type = t.get("trade_type")
```

In `_books`, append the third entry (note the `None` snapshot — a captured book
has no account, and `_book_now_line` already returns `""` for that):

```python
    cap = normalize_trades((snap.get("captured_perf") or {}).get("rows"),
                           kind="captured")
    return [
        ("Manual paper", led, (snap.get("paper_account") or {}).get("snapshot")),
        ("Driver", drv, dacc.get("snapshot")),
        ("Captured signals", cap, None),
    ]
```

Add to the snapshot reader beside its siblings:

```python
        "captured_perf": bus_client.read("options:captured_perf") or {},
```

And in `performance_sections`, append the caveat under the captured table only:

```python
CAPTURED_BASIS_NOTE = (
    "Figures assume ONE contract per signal — a captured signal is never sized. "
    "These are the scanner's picks scored under the auto-manage rules, not "
    "trades that were taken."
)
```

**Step 4: Run `tests/test_eod.py`** — expect 34 + 3 passing, then the full
`webgui` suite.

**Step 5: Commit**

```bash
git add webgui/pages/eod.py webgui/tests/test_eod.py
git commit -m "feat(eod): captured signals become the third performance book"
```

---

### Task 5: Verify against prod's real numbers

The section is only worth having if it agrees with the database. This is the step
that proves it, and it costs one read-only query.

**Step 1:** Run the reference query on prod:

```bash
ssh vps2 'cd /home/administrator/dev && .venv/bin/python -c "
import sqlite3
c = sqlite3.connect(\"file:options-scanner/data/signals.db?mode=ro\", uri=True)
for r in c.execute(\"SELECT close_date, COUNT(*), ROUND(SUM(realized_pnl),2) FROM signal_outcomes WHERE close_date >= 0x27 2026-09-01 0x27 GROUP BY close_date\"):
    print(r)"'
```

**Step 2:** Compare against the design's §5 table — MTD −$1,251, 45 closed,
5-40. A mismatch means the window or the join is wrong, not that the data moved.

**Step 3:** Run the full `webgui` suite and `services/options_svc`. Compare the
failing SET, never the count.

---

## Out of scope

No inception ("Since 1 Sep") row; `/options/captured` unchanged; no
regular-hours filter (the recorder already gates capture, and every September
close was captured 10:17–15:02 CT). All three are argued in the design doc.

## Verification limit

Not browser-verified — a worktree resolves to prod and would bind `:8500`. The
EOD section renders as HTML, so the suite plus the prod query in Task 5 are the
gate.
