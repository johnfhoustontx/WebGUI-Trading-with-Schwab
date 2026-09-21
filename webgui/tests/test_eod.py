"""Tests for the EOD report pure builders (webgui/pages/eod.py)."""
from pages import eod


SAMPLE = {
    "date": "2026-06-18", "generated_at": "2026-06-18 16:05 CT",
    "scan": {"signals_0dte": [{"symbol": "$SPX", "composite_score": 9.0}],
             "signals_swing": []},
    "captured": {"signals": [{"symbol": "AAPL", "score": 8.1, "current_score": 7.4,
                              "score_drift": -0.7, "recommendation": "HOLD",
                              "unrealized_pnl": 42.0}]},
    "paper_trades": {"trades": []},
    "paper_account": {"has_account": False, "snapshot": None},
    "driver_paper_account": {"has_account": False, "snapshot": None,
                             "positions": [], "closed_positions": []},
    "driver_paper_perf": {"total_trades": 0, "realized_pnl": 0.0, "win_rate": None},
}


# --- CSS + document wrapper ---------------------------------------------------
def test_css_is_scoped_nonempty_string():
    assert isinstance(eod.EOD_CSS, str)
    assert ".eod-report" in eod.EOD_CSS


def test_wrap_document_is_standalone_with_css_and_title():
    doc = eod.wrap_document("<p>hi</p>", ".eod-report{color:red}", "My Title")
    assert doc.lstrip().startswith("<!DOCTYPE html>")
    assert "<style>.eod-report{color:red}</style>" in doc
    assert "<title>My Title</title>" in doc
    assert "<p>hi</p>" in doc
    assert doc.rstrip().endswith("</html>")


# --- formatting helpers -------------------------------------------------------
def test_money_formats_sign_and_none():
    assert eod._money(1234.5) == "$1,234.50"
    assert eod._money(-50) == "-$50.00"
    assert eod._money(None) == "—"


def test_cell_escapes_html():
    assert eod._cell("<script>") == "<td>&lt;script&gt;</td>"


def test_pn_class():
    assert eod._pn_class(5) == "pos"
    assert eod._pn_class(-5) == "neg"
    assert eod._pn_class(0) == ""
    assert eod._pn_class(None) == ""


# --- per-section builders -----------------------------------------------------
def test_captured_section_renders_rows_and_handles_empty():
    html = eod.captured_section(SAMPLE["captured"])
    assert "AAPL" in html and "HOLD" in html
    assert "-0.70" in html  # drift to 2dp
    assert 'class="none"' in eod.captured_section({"signals": []})
    assert 'class="none"' in eod.captured_section(None)


def test_paper_section_lists_trades_and_account():
    trades = {"trades": [
        {"symbol": "SPY", "strategy": "IC", "realized_pnl": 120.0,
         "status": "CLOSED", "entry_time": "2026-06-18T09:40:00"},
    ]}
    account = {"has_account": True,
               "snapshot": {"equity": 10120.0, "session_pnl": 120.0,
                            "realized_pnl": 120.0, "open_unrealized": -5.0,
                            "open_count": 1}}
    html = eod.paper_section(trades, account)
    assert "SPY" in html and "IC" in html
    assert "$120.00" in html
    # empty trades + no account → none notes for both summary and table
    empty = eod.paper_section({"trades": []}, {"has_account": False, "snapshot": None})
    assert empty.count('class="none"') == 2


def test_scanner_section_counts_and_lists():
    scan = {"signals_0dte": [{"symbol": "$SPX", "composite_score": 9.0}],
            "signals_swing": [{"symbol": "QQQ", "composite_score": 7.0}]}
    html = eod.scanner_section(scan)
    assert "$SPX" in html and "QQQ" in html
    assert 'class="none"' in eod.scanner_section({"signals_0dte": [], "signals_swing": []})


# --- whole-report fragments ---------------------------------------------------
def test_detail_fragment_includes_all_sections_and_date():
    html = eod.detail_fragment(SAMPLE)
    assert 'class="eod-report"' in html
    assert "2026-06-18" in html
    for heading in ("Captured Signals", "Paper Trades", "Scanner Signals", "Driver"):
        assert heading in html
    assert "AAPL" in html and "$SPX" in html


def test_summary_fragment_tiles_and_detail_link():
    html = eod.summary_fragment(SAMPLE, "/eod/detail")
    assert 'class="eod-report"' in html
    assert 'href="/eod/detail"' in html
    assert "Scanner signals" in html and "Captured signals" in html
    assert "Driver win rate" in html  # driver scorecard tile surfaced


def test_summary_fragment_link_target_is_parameterized():
    assert 'href="detail.html"' in eod.summary_fragment(SAMPLE, "detail.html")


# --- snapshot + archive -------------------------------------------------------
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


def test_write_archive_uses_atomic_replace(tmp_path, monkeypatch):
    """The archive write must go through os.replace (atomic rename), so a
    reader never sees a partial file — and the temp file lives in the SAME dir."""
    seen = []
    real_replace = eod.os.replace

    def _spy_replace(src, dst):
        # A .tmp temp file in the SAME directory as the final target.
        assert str(src).endswith(".tmp")
        assert eod.Path(src).parent == eod.Path(dst).parent
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(eod.os, "replace", _spy_replace)
    paths = eod.write_archive(tmp_path, "2026-06-18", "<sum/>", "<det/>")
    assert len(seen) == 2  # both files went through the atomic rename
    assert paths["summary"].read_text(encoding="utf-8") == "<sum/>"
    assert paths["detail"].read_text(encoding="utf-8") == "<det/>"


def test_write_archive_no_partial_file_on_crash(tmp_path, monkeypatch):
    """If the write crashes mid-generate, the final .html never appears — the
    reader is never handed a half-written document; the temp file is cleaned up."""
    def _boom(src, dst):
        raise RuntimeError("crash mid-generate")

    monkeypatch.setattr(eod.os, "replace", _boom)
    try:
        eod.write_archive(tmp_path, "2026-06-18", "<sum/>", "<det/>")
    except RuntimeError:
        pass
    day = tmp_path / "2026-06-18"
    assert not (day / "summary.html").exists()  # no half-written final file
    # And no stray temp files were left behind.
    assert list(day.glob("*.tmp")) == []


def test_write_archive_same_date_overwrites(tmp_path):
    """Re-generating the same date overwrites in place (atomic, preserved)."""
    eod.write_archive(tmp_path, "2026-06-18", "<sum-v1/>", "<det-v1/>")
    paths = eod.write_archive(tmp_path, "2026-06-18", "<sum-v2/>", "<det-v2/>")
    assert paths["summary"].read_text(encoding="utf-8") == "<sum-v2/>"
    assert paths["detail"].read_text(encoding="utf-8") == "<det-v2/>"


def test_generate_writes_standalone_docs_with_relative_link(tmp_path, monkeypatch):
    monkeypatch.setattr(eod, "read_snapshot", lambda: dict(SAMPLE))
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    out = eod.generate()
    assert out["date"] == "2026-06-18"
    summ = (tmp_path / "2026-06-18" / "summary.html").read_text(encoding="utf-8")
    det = (tmp_path / "2026-06-18" / "detail.html").read_text(encoding="utf-8")
    assert summ.startswith("<!DOCTYPE html>") and det.startswith("<!DOCTYPE html>")
    assert 'href="detail.html"' in summ  # file link is relative, not the route
    assert "AAPL" in det and "$SPX" in det


# --- Task 2: normalize_trades ------------------------------------------------
def test_normalize_trades_ledger_and_driver():
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


# --- Task 3: period_buckets --------------------------------------------------
import datetime as _dt


def _nt(entry, exit_, pnl, status="CLOSED"):
    return {"symbol": "X", "strategy": "PCS", "trade_type": "SWING", "status": status,
            "entry_date": entry, "exit_date": exit_, "realized_pnl": pnl, "credit": 100.0}


def test_period_buckets_daily_weekly_mtd():
    today = _dt.date(2026, 6, 27)          # a Saturday; week-to-date = Mon 06-22..27
    trades = [
        _nt("2026-06-27", "2026-06-27", 50.0),     # opened+closed today
        _nt("2026-06-23", "2026-06-25", -20.0),    # closed this week (not today)
        _nt("2026-06-02", "2026-06-10", 200.0),    # closed this month (not this week)
        _nt("2026-05-30", "2026-05-31", 999.0),    # last month — excluded everywhere
        _nt("2026-06-26", None, None, status="OPEN"),  # open entry this week
    ]
    b = eod.period_buckets(trades, today)
    assert b["daily"]["realized"] == 50.0
    assert b["weekly"]["realized"] == 50.0 + (-20.0)
    assert b["mtd"]["realized"] == 50.0 - 20.0 + 200.0
    assert b["daily"]["closed"] == 1
    assert b["weekly"]["closed"] == 2
    assert b["mtd"]["closed"] == 3
    assert b["weekly"]["wins"] == 1 and b["weekly"]["losses"] == 1
    assert b["weekly"]["win_rate"] == 0.5
    assert b["daily"]["opened"] == 1
    assert b["weekly"]["opened"] == 3      # 06-27, 06-23, 06-26
    assert b["mtd"]["opened"] == 4         # + 06-02


# --- Task 4: breakdown_rows --------------------------------------------------
def test_breakdown_rows_by_strategy():
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
    rows2 = eod.breakdown_rows([{"status": "OPEN"}], "trade_type")
    assert rows2[0]["group"] == "—"


# --- Task 5: nav + formatting helpers ----------------------------------------
def test_toc_and_details_section():
    toc = eod.toc([("perf", "Performance"), ("brk", "Breakdowns")])
    assert 'href="#perf"' in toc and "Performance" in toc
    sec = eod.details_section("perf", "Performance", "<p>body</p>")
    assert 'id="perf"' in sec
    assert "<details" in sec and "<summary>" in sec and "Performance" in sec
    assert "<p>body</p>" in sec


def test_pct_helper():
    assert eod._pct(0.5) == "50%"
    assert eod._pct(None) == "—"


def test_performance_table_html_renders_periods():
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


def test_breakdown_table_html_renders():
    rows = [{"group": "PCS", "trades": 2, "open": 1, "closed": 1,
             "realized": 30.0, "win_rate": 1.0}]
    html = eod.breakdown_table_html(rows)
    assert "PCS" in html and "$30.00" in html


# --- Task 6: rewired read_snapshot + summary/detail fragments ----------------
def test_read_snapshot_includes_driver_views(monkeypatch):
    monkeypatch.setattr(eod.bus_client, "read", lambda k: {"_k": k})
    snap = eod.read_snapshot()
    assert "driver_paper_account" in snap and "driver_paper_perf" in snap


def test_summary_fragment_has_performance_and_toc():
    import datetime as _dt
    snap = {
        "date": "2026-06-27", "generated_at": "x",
        "paper_trades": {"trades": [
            {"symbol": "AMD", "strategy": "PCS", "trade_type": "SWING", "status": "OPEN",
             "entry_time": "2026-06-27T10:00:00", "exit_time": None,
             "realized_pnl": None, "entry_credit_total": 120.0}]},
        "driver_paper_account": {"has_account": True, "snapshot": {"equity": 25000.0},
                                 "positions": [], "closed_positions": []},
    }
    html = eod.summary_fragment(snap, "/eod/detail", today=_dt.date(2026, 6, 27))
    assert "Performance" in html and "Daily" in html
    assert "eod-toc" in html
    assert "Manual paper" in html and "Driver" in html   # both books labelled


def test_detail_fragment_has_breakdowns():
    import datetime as _dt
    snap = {"date": "2026-06-27", "generated_at": "x",
            "paper_trades": {"trades": [
                {"symbol": "AMD", "strategy": "PCS", "trade_type": "SWING",
                 "status": "OPEN", "entry_time": "2026-06-27T10:00:00",
                 "realized_pnl": None, "entry_credit_total": 120.0}]},
            "driver_paper_account": {"has_account": True, "snapshot": {},
                                     "positions": [], "closed_positions": []}}
    html = eod.detail_fragment(snap, today=_dt.date(2026, 6, 27))
    assert "By strategy" in html and "By 0-DTE / Swing" in html and "By status" in html
    assert "<details" in html


def test_summary_fragment_back_compat_two_args():
    # generate()/render() call it with no `today` — must still work.
    html = eod.summary_fragment({"date": "2026-06-27"}, "detail.html")
    assert "EOD Summary" in html


def test_detail_fragment_back_compat_one_arg():
    html = eod.detail_fragment({"date": "2026-06-27"})
    assert "EOD Detailed Report" in html


# --- captured closed-today section (captured-autoclose) ----------------------
CLOSED_SAMPLE = {"closed": [
    {"signal_id": "s1", "symbol": "AAPL", "strategy": "PCS", "entry_credit": 1.20,
     "exit_value": 0.40, "realized_pnl": 80.0, "exit_reason": "BREAKEVEN_STOP",
     "close_ts": "2026-08-09T14:31:00-05:00"},
    {"signal_id": "s2", "symbol": "MSFT", "strategy": "IC", "entry_credit": 2.00,
     "exit_value": 3.50, "realized_pnl": -150.0, "exit_reason": "MONEY_STOP",
     "close_ts": "2026-08-09T10:05:00-05:00"},
], "total_realized": -70.0}


def test_captured_closed_rows_map_fields():
    rows = eod.captured_closed_rows(CLOSED_SAMPLE)
    assert len(rows) == 2
    joined = "".join(rows)
    assert "AAPL" in joined and "PCS" in joined and "BREAKEVEN_STOP" in joined
    assert "1.20" in joined and "0.40" in joined          # credit / exit @ 2dp
    assert "$80.00" in joined                             # realized as money
    assert "14:31" in joined                              # CT time from close_ts


def test_captured_closed_section_shows_total_and_empty_note():
    html = eod.captured_closed_section(CLOSED_SAMPLE)
    assert "MSFT" in html
    assert "-$70.00" in html                              # day realized total
    empty = eod.captured_closed_section({"closed": [], "total_realized": 0.0})
    assert 'class="none"' in empty                        # graceful empty
    assert 'class="none"' in eod.captured_closed_section(None)


def test_read_snapshot_includes_captured_closed(monkeypatch):
    monkeypatch.setattr(eod.bus_client, "read", lambda k: {"_k": k})
    snap = eod.read_snapshot()
    assert "captured_closed" in snap


def test_captured_closed_section_in_both_fragments():
    snap = dict(SAMPLE)
    snap["captured_closed"] = CLOSED_SAMPLE
    det = eod.detail_fragment(snap)
    summ = eod.summary_fragment(snap, "/eod/detail")
    assert "Captured — closed today" in det
    assert "Captured — closed today" in summ
    assert "AAPL" in det                                  # rows rendered in detail


# ── captured signals as the third performance book ───────────────────────────
import datetime as _dt

_CAP_ROW = {
    "symbol": "SPY", "strategy": "PCS", "trade_type": "0DTE", "status": "CLOSED",
    "first_seen_ts": "2026-09-02T10:00:00-05:00",
    "close_ts": "2026-09-03T14:00:00-05:00",
    "realized_pnl": 25.0, "entry_credit_total": 55.0,
}


def test_normalize_trades_reads_the_captured_shape():
    """Captured rows carry their own timestamp field names, so the kind branch
    exists to point ``_date_of`` at them — the same job it does for the other
    two books, not a second date parser."""
    rows = eod.normalize_trades([_CAP_ROW], kind="captured")
    assert rows[0]["entry_date"] == "2026-09-02"
    assert rows[0]["exit_date"] == "2026-09-03"
    assert rows[0]["realized_pnl"] == 25.0
    assert rows[0]["credit"] == 55.0            # already one-contract dollars
    assert rows[0]["trade_type"] == "0DTE"


def test_captured_is_the_third_book():
    snap = {"captured_perf": {"rows": [_CAP_ROW]}}
    assert [label for label, _n, _s in eod._books(snap)] == [
        "Manual paper", "Driver", "Captured signals"]


def test_the_captured_book_reuses_the_shared_period_buckets():
    """No new aggregation. All three books go through ``period_buckets``, so
    they cannot disagree about what "this week" means."""
    snap = {"captured_perf": {"rows": [_CAP_ROW]}}
    norm = [n for label, n, _s in eod._books(snap) if label == "Captured signals"][0]
    b = eod.period_buckets(norm, _dt.date(2026, 9, 3))
    assert b["daily"]["realized"] == 25.0 and b["daily"]["closed"] == 1
    assert b["daily"]["wins"] == 1 and b["daily"]["losses"] == 0
    assert b["mtd"]["opened"] == 1            # opened 09-02, still inside MTD
    assert b["mtd"]["credit"] == 55.0


def test_the_captured_book_has_no_account_line():
    """It is a tracking book with no account behind it, so the point-in-time
    line the other two print must stay empty rather than invent an equity."""
    snap = {"captured_perf": {"rows": [_CAP_ROW]}}
    now_snap = [s for label, _n, s in eod._books(snap)
                if label == "Captured signals"][0]
    assert now_snap is None
    assert eod._book_now_line(now_snap) == ""


def test_a_cold_captured_view_still_builds_the_section():
    """A missing view must cost the numbers, never the report."""
    for snap in ({}, {"captured_perf": {}}, {"captured_perf": {"rows": []}}):
        norm = [n for label, n, _s in eod._books(snap)
                if label == "Captured signals"][0]
        assert norm == []


def test_the_captured_section_says_the_dollars_are_one_contract():
    """Without it, a -$1,251 month reads as an account loss. These signals were
    tracked to see whether the scanner was right; they were never sized and
    never traded."""
    _toc, html = eod._performance_block({"captured_perf": {"rows": [_CAP_ROW]}},
                                        _dt.date(2026, 9, 4))
    low = html.lower()
    assert "one contract" in low
    assert "not" in low and ("traded" in low or "taken" in low)


# --- has_data: the gate the scheduled run needs and the button does not ------
def test_has_data_is_false_for_a_snapshot_that_read_nothing():
    """The whole point of the gate. Every builder here degrades to a "No data"
    note, so an empty snapshot renders a complete-looking report -- and the
    archive is keyed by date and overwrites in place, so an unattended run
    committing that would destroy the day's real report."""
    empty = {"date": "2026-09-17", "generated_at": "2026-09-17 15:15 CT"}
    empty.update({k: {} for k in eod._CACHE_VIEWS})
    assert eod.has_data(empty) is False


def test_has_data_is_true_when_any_single_view_carried_a_payload():
    """ANY view, not all of them: a day with no captured signals and a live
    paper book is an ordinary day, and refusing it would skip real reports."""
    for key in eod._CACHE_VIEWS:
        snap = {k: {} for k in eod._CACHE_VIEWS}
        snap[key] = {"rows": [1]}
        assert eod.has_data(snap) is True, key


def test_has_data_checks_the_views_read_snapshot_actually_reads(monkeypatch):
    """One list, so the gate cannot drift into checking a different set of
    views than the snapshot fills -- which would make it either vacuous (always
    true) or a permanent refusal."""
    monkeypatch.setattr(eod.bus_client, "read", lambda k: {"_k": k})
    snap = eod.read_snapshot()
    for key, view in eod._CACHE_VIEWS.items():
        assert snap[key] == {"_k": view}
    assert eod.has_data(snap) is True


def test_a_missing_key_is_absence_not_an_error():
    """A snapshot built by an older caller, or a partially built one, must read
    as "no data" rather than raise inside a scheduled run."""
    assert eod.has_data({}) is False


# --- generate() reuses a snapshot the caller already read -------------------
def test_generate_writes_the_snapshot_it_was_given(tmp_path, monkeypatch):
    """The scheduled run inspects a snapshot, then writes THAT one. A second
    read inside generate would mean the bytes checked were not the bytes
    archived -- and at 15:15 the caches are still moving."""
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    def _boom():
        raise AssertionError("generate re-read the caches instead of using the "
                             "snapshot it was handed")
    monkeypatch.setattr(eod, "read_snapshot", _boom)
    snap = dict(SAMPLE)
    snap["date"] = "2026-09-17"
    out = eod.generate(snap)
    assert out["date"] == "2026-09-17"
    assert (tmp_path / "2026-09-17" / "summary.html").is_file()


def test_generate_still_reads_for_itself_when_given_nothing(tmp_path, monkeypatch):
    """The button's path is unchanged -- it passes no snapshot."""
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(eod, "read_snapshot", lambda: dict(SAMPLE))
    out = eod.generate()
    assert (tmp_path / out["date"] / "detail.html").is_file()


# --- Task 3: Generate confirms, and refuses a cold cache --------------------
# ⚠ Driven through the real ``render()``, never by calling ``generate()``. The
# gate these pin is a consumer-side guard, and this repo's own lesson is that a
# consumer-side guard proves nothing until a test drives it from the PRODUCER:
# ``has_data`` was written, documented and tested from both sides above while
# the button went straight past it for months.
COLD_SNAP = {"date": "2026-09-18", "generated_at": "2026-09-18 16:05 CT"}
COLD_SNAP.update({k: {} for k in eod._CACHE_VIEWS})


def _render_eod(monkeypatch, tmp_path, snap):
    """Render the EOD summary page over a pinned snapshot and a tmp archive."""
    from nicegui import ui
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(eod, "read_snapshot", lambda: dict(snap))
    with ui.card() as host:
        eod.render()
    return host


def _said(monkeypatch):
    """Everything the page reports, in order: ``(kind, text)``.

    Both spellings, because Task 3 adds the kit's toast while the two outcome
    notifies are still ``ui.notify`` - and the kit is patched on its OWN module,
    so this works whichever name ``eod`` reaches it by."""
    from pages import ui_kit
    seen = []
    monkeypatch.setattr(eod.ui, "notify",
                        lambda msg="", **kw: seen.append((kw.get("type"), msg)))
    monkeypatch.setattr(ui_kit, "toast", lambda kind, text: seen.append((kind, text)))
    return seen


def _fire(el, kind, args=None):
    """Fire an element's OWN registered listener - what the browser would send."""
    from nicegui.events import GenericEventArguments
    fired = [li.handler(GenericEventArguments(sender=el, client=el.client, args=args))
             for li in list(el._event_listeners.values())
             if li.type.split(".")[0] == kind and li.handler is not None]
    assert fired, f"no {kind} listener to fire"


def _click(host, text):
    from nicegui import ui
    btn = [b for b in host.descendants()
           if isinstance(b, ui.button) and b.text == text]
    assert btn, f"no {text!r} button on the page"
    _fire(btn[-1], "click")


def _dialog_with(confirm_text):
    """The most recently built dialog whose confirm button says ``confirm_text``.

    ⚠ Scoped by RECENCY rather than by the page: a dialog lives on the client
    LAYOUT (NiceGUI 3.x), which the whole test module shares, so ``[-1]`` is
    what keeps a test reading its own render instead of an earlier one's."""
    from nicegui import context, ui
    found = [d for d in context.client.layout.descendants()
             if isinstance(d, ui.dialog)
             and any(isinstance(e, ui.button) and e.text == confirm_text
                     for e in d.descendants())]
    assert found, f"no confirm dialog offering {confirm_text!r}"
    return found[-1]


def _confirm(dlg):
    """Run a confirm dialog's action (the test_appearance.py recipe verbatim):
    its ``run`` is a COROUTINE function that nicegui would only DEFER here, so
    drive the dialog's own keydown.enter listener inside a slot context."""
    import asyncio
    (run_,) = [li.handler for li in dlg._event_listeners.values()
               if li.type == "keydown.enter"]

    async def _drive(slot):
        with slot:
            await run_(None)

    asyncio.run(_drive(dlg.parent_slot))


def test_generate_asks_before_it_replaces_the_days_saved_files(monkeypatch, tmp_path):
    """The archive holds ONE copy per date and ``write_archive`` overwrites it
    in place, so Generate is destructive and confirms like every other
    destructive action in the app."""
    said = _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, SAMPLE)
    _click(host, "Generate")
    assert list(tmp_path.iterdir()) == [], "Generate wrote before anyone confirmed"
    assert said == [], "and it reported an outcome nobody asked for"
    _confirm(_dialog_with("Generate"))
    assert (tmp_path / SAMPLE["date"] / "summary.html").is_file()
    assert (tmp_path / SAMPLE["date"] / "detail.html").is_file()


def test_a_confirmed_generate_over_a_cold_cache_writes_nothing(monkeypatch, tmp_path):
    """The defect. Every builder here is defensive, so an all-empty snapshot
    renders a complete-looking report of "No data" notes - and the archive is
    keyed by date and overwrites in place, so one click replaced the day's real
    report with that."""
    said = _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, COLD_SNAP)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert list(tmp_path.iterdir()) == [], "a cold cache was archived anyway"
    assert said, "the refusal said nothing at all"
    assert said[-1][0] == "warn"
    assert "empty" in said[-1][1].lower()


def test_a_cold_generate_leaves_the_days_REAL_report_on_disk(monkeypatch, tmp_path):
    """What the refusal actually protects: the file already there."""
    day = tmp_path / COLD_SNAP["date"]
    day.mkdir()
    (day / "summary.html").write_text("<the real one/>", encoding="utf-8")
    (day / "detail.html").write_text("<the real detail/>", encoding="utf-8")
    _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, COLD_SNAP)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert (day / "summary.html").read_text(encoding="utf-8") == "<the real one/>"
    assert (day / "detail.html").read_text(encoding="utf-8") == "<the real detail/>"


def test_the_button_archives_the_snapshot_it_CHECKED(monkeypatch, tmp_path):
    """One read feeds both the gate and the write. A second read inside
    ``generate`` would mean the bytes examined were not the bytes archived -
    exactly why ``generate`` takes a snapshot at all."""
    _said(monkeypatch)
    got = []
    real_generate = eod.generate

    def _spy(snap=None):
        got.append(snap)
        return real_generate(snap)

    host = _render_eod(monkeypatch, tmp_path, SAMPLE)
    monkeypatch.setattr(eod, "generate", _spy)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert got, "Generate never reached generate()"
    assert got[0] is not None, \
        "the button handed generate() nothing, so it re-read the caches for itself"
    assert eod.has_data(got[0]) is True


def test_a_failed_generate_still_reports_and_does_not_crash_the_page(monkeypatch,
                                                                     tmp_path):
    """The defensive branch survives the gate: a write that raises is an error
    toast, never a traceback on the page."""
    said = _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, SAMPLE)

    def _boom(_root, _date, _s, _d):
        raise OSError("the archive directory is read-only")

    monkeypatch.setattr(eod, "write_archive", _boom)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert said and "read-only" in said[-1][1]


# --- Task 4: both frames on the kit -----------------------------------------
# ⚠ ``render_detail`` is a SECOND render() in this module, so every frame
# assertion here is made twice.
def _src(fn):
    import inspect
    return inspect.getsource(fn)


def _timer(host, name):
    """The page's ``ui.timer`` whose callback is ``name`` (``functools.wraps``
    keeps the name through ``guard_async``)."""
    from nicegui import ui
    found = [e for e in host.descendants()
             if isinstance(e, ui.timer)
             and getattr(e.callback, "__name__", "") == name]
    assert found, f"no ui.timer registered for {name}()"
    return found[-1]


def _run_timer(host, name):
    """Drive a page's once-timer the way the browser's first tick would."""
    import asyncio
    import inspect
    t = _timer(host, name)

    async def _drive():
        with t.parent_slot:
            result = t.callback()
            if inspect.isawaitable(result):
                await result

    asyncio.run(_drive())


def _scrim(host):
    """``kit.region``'s spinner scrim - the spinner's own parent element."""
    from nicegui import ui
    spinners = [e for e in host.descendants() if isinstance(e, ui.spinner)]
    assert spinners, "the page mounted no region spinner"
    return spinners[-1].parent_slot.parent


def _htmls(host):
    from nicegui import ui
    return [e for e in host.descendants() if isinstance(e, ui.html)]


def test_both_frames_wear_the_kit_page_and_name_themselves():
    """Neither frame had a title at all - ``main.py`` supplied one as the
    browser title and the breadcrumb, and the page itself opened on a button
    row. The standard gives every page one header line."""
    for fn, title in ((eod.render, "EOD Report"),
                      (eod.render_detail, "EOD Report — Detail")):
        src = _src(fn)
        assert "kit.page()" in src, fn.__name__
        assert f'kit.header("{title}")' in src, fn.__name__


def test_neither_frame_claims_a_freshness_stamp_over_eight_views(monkeypatch,
                                                                 tmp_path):
    """The page reads EIGHT cache views, so a stamp on one would name the age
    of a key that is only part of what is on screen. The fragment's own
    "Generated ... CT" meta line is this page's real freshness, and it stays.

    ⚠ Asked of the RENDERED page, not of the source: a source grep for
    ``view=`` fails on the docstring that EXPLAINS why there is no view, which
    is the manuals.py lesson - the page's own prose is free to describe the
    page. What is checked is the kit's stamp label: hidden, and never carrying
    a time."""
    from nicegui import ui

    from pages import ui_kit
    _said(monkeypatch)
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(eod, "read_snapshot", lambda: dict(SAMPLE))
    for build in (eod.render, eod.render_detail):
        with ui.card() as host:
            build()
        texts = [str(getattr(e, "text", "")) for e in host.descendants()]
        assert ui_kit.WAITING_TEXT not in texts, build.__name__
        assert not [t for t in texts if t.startswith("Updated ")], build.__name__
        hidden = [e for e in host.descendants()
                  if isinstance(e, ui.label) and e.text == "" and not e.visible]
        assert hidden, f"{build.__name__}: kit.header's stamp is not hidden"
    assert len(eod._CACHE_VIEWS) == 8, "the eight views this reasoning rests on"


def test_the_module_builds_no_button_dialog_notify_or_table_of_its_own():
    """The page-level half of deleting ``eod.py``'s guard entry."""
    import inspect
    src = inspect.getsource(eod)
    for call in ("ui.button(", "ui.dialog(", "ui.notify(", "ui.table("):
        assert call not in src, f"{call} should go through pages/ui_kit.py"


def test_the_summary_frame_paints_its_fragment_off_the_event_loop(monkeypatch,
                                                                  tmp_path):
    """``read_snapshot`` is eight sequential bus reads and ran on the loop at
    page build. It now crosses ``run.io_bound``, so this asserts the THREAD it
    ran on, not the spelling."""
    import threading
    _said(monkeypatch)
    where = []
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(
        eod, "read_snapshot",
        lambda: where.append(threading.current_thread()) or dict(SAMPLE))
    from nicegui import ui
    with ui.card() as host:
        eod.render()
    assert not _htmls(host), "the fragment was built on the event loop"
    _run_timer(host, "_repaint")
    assert where and where[0] is not threading.main_thread(), \
        "read_snapshot still ran on the event loop"
    drawn = "".join(h.content for h in _htmls(host))
    assert 'class="meta"' in drawn and "Generated" in drawn, "the meta line went"
    assert "EOD Summary" in drawn


def test_the_detail_frame_paints_its_fragment_off_the_event_loop(monkeypatch,
                                                                 tmp_path):
    import threading
    where = []
    monkeypatch.setattr(eod, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(
        eod, "read_snapshot",
        lambda: where.append(threading.current_thread()) or dict(SAMPLE))
    from nicegui import ui
    with ui.card() as host:
        eod.render_detail()
    assert not _htmls(host)
    _run_timer(host, "_repaint_detail")
    assert where and where[0] is not threading.main_thread()
    drawn = "".join(h.content for h in _htmls(host))
    assert "EOD Detailed Report" in drawn
    assert 'class="meta"' in drawn


def test_generate_runs_off_the_loop_behind_the_REGION_spinner(monkeypatch,
                                                              tmp_path):
    """The wait goes on the REGION, never on the Generate button. Read from
    INSIDE generate(), so it measures what the reader sees while the two
    documents are being built and written."""
    import threading
    _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, SAMPLE)
    scrim = _scrim(host)
    from nicegui import ui
    msg = [e for e in scrim.descendants() if isinstance(e, ui.label)][0]
    seen = {}
    real_generate = eod.generate

    def _spy(snap=None):
        seen["visible"] = scrim.visible
        seen["message"] = msg.text
        seen["thread"] = threading.current_thread()
        return real_generate(snap)

    monkeypatch.setattr(eod, "generate", _spy)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert seen["visible"] is True, "the report was built with no wait on screen"
    assert "Generating" in seen["message"]
    assert seen["thread"] is not threading.main_thread()
    assert scrim.visible is False, "the spinner was left running"


def test_the_spinner_is_mounted_where_a_repaint_cannot_delete_it(monkeypatch,
                                                                 tmp_path):
    """``kit.region`` keeps the scrim on ``outer`` and clears only ``content``,
    so it is still there after the paint that replaces the fragment."""
    from nicegui import ui
    _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, SAMPLE)
    _run_timer(host, "_repaint")
    assert any(isinstance(e, ui.spinner) for e in host.descendants()), \
        "the region's spinner was deleted by the repaint"


def test_every_outcome_is_a_kit_toast_with_the_right_kind(monkeypatch, tmp_path):
    """Three outcomes, three kinds: written / refused / failed."""
    said = _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, SAMPLE)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert said[-1][0] == "ok" and SAMPLE["date"] in said[-1][1]

    host = _render_eod(monkeypatch, tmp_path / "cold", COLD_SNAP)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert said[-1][0] == "warn"

    host = _render_eod(monkeypatch, tmp_path / "boom", SAMPLE)

    def _boom(_root, _date, _s, _d):
        raise OSError("the archive directory is read-only")

    monkeypatch.setattr(eod, "write_archive", _boom)
    _click(host, "Generate")
    _confirm(_dialog_with("Generate"))
    assert said[-1][0] == "error" and "read-only" in said[-1][1]


def test_the_open_file_buttons_disclose_that_generate_writes_the_file(monkeypatch,
                                                                     tmp_path):
    """They link to TODAY's archived file, which does not exist until Generate
    has run - the route already answers "click Generate first" in the tab it
    opens. The dependency is disclosed in a tooltip rather than by disabling
    the button: nothing is lost by clicking, and a disabled button would need a
    per-paint ``is_file()`` check that could disagree with the route's own."""
    from nicegui import ui
    _said(monkeypatch)
    host = _render_eod(monkeypatch, tmp_path, SAMPLE)
    for text in ("Open summary file", "Open detail file"):
        btn = [b for b in host.descendants()
               if isinstance(b, ui.button) and b.text == text][-1]
        tips = [e for e in btn.descendants() if isinstance(e, ui.tooltip)]
        assert tips, f"{text} has no tooltip"
        assert "Generate" in tips[0].text, f"{text} does not say what writes it"


# --- Task 5: the report's CSS takes the app's colours -----------------------
# ⚠ EOD_CSS has TWO destinations: ``ui.add_css`` in both frames (this page's one
# documented escape hatch) and ``wrap_document`` into the standalone
# summary.html / detail.html that ``/eod/file`` serves - raw documents with no
# NiceGUI, no Tailwind and no app stylesheet. So it is RECOLOURED, not deleted
# and not routed through the kit, exactly as the design says.
import re as _re

_HEX = _re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _sentinel_theme():
    """A theme whose every colour is a unique sentinel, so a value that did NOT
    come from the theme is visible by inspection."""
    import copy

    from pages.options import theme
    t = copy.deepcopy(theme._DEFAULTS)
    n = [0]

    def _next():
        n[0] += 1
        return f"#{n[0]:02x}00ff"

    for section in ("palette", "semantic"):
        for key in t[section]:
            t[section][key] = _next()
    t["typography"]["family"] = "'Sentinel Sans', sans-serif"
    return t


def test_every_colour_in_the_report_css_comes_from_the_theme():
    """The point of the recolour. Built over a theme whose every colour is a
    sentinel, the stylesheet may contain NO other hex - so a green, a grey or a
    link blue left behind in the string shows up here rather than on screen."""
    t = _sentinel_theme()
    css = eod.build_eod_css(t)
    allowed = set(t["palette"].values()) | set(t["semantic"].values())
    strays = {h for h in _HEX.findall(css) if h not in allowed}
    assert not strays, f"colours that are not the theme's: {sorted(strays)}"
    assert "'Sentinel Sans', sans-serif" in css, "the font is not the app's"


def test_the_pos_and_neg_classes_are_the_apps_SEMANTIC_pair():
    """They were #4caf50 / #ef5350 - a FOURTH green/red pair beside the three
    the design already counted. ``_pn_class`` returning "" for zero already
    matches "muted for zero" and is untouched."""
    t = _sentinel_theme()
    css = eod.build_eod_css(t)
    assert t["semantic"]["positive"] in css
    assert t["semantic"]["negative"] in css
    for retired in ("#4caf50", "#ef5350", "#64b5f6", "#e8e8e8"):
        assert retired not in css, f"{retired} survived the recolour"
    assert eod._pn_class(0) == "", "zero must stay uncoloured"


def test_the_report_css_dims_with_colours_not_with_opacity():
    """Six ``opacity:`` dims and four ``rgba(255,255,255,...)`` washes carried
    the text ladder and every hairline. They are palette values now, which is
    what lets the whole document follow Settings -> Appearance."""
    css = eod.build_eod_css(_sentinel_theme())
    assert "opacity:" not in css
    assert "rgba(255,255,255" not in css
    assert "Segoe UI" not in css


def test_the_tile_lost_the_retired_3d_button_look():
    """Its three-layer inset/drop ``box-shadow`` WAS the 3D button treatment the
    standard retires. A tile is a card: the card ground and a hairline."""
    t = _sentinel_theme()
    css = eod.build_eod_css(t)
    assert "box-shadow" not in css
    tile = [ln for ln in css.splitlines() if ".tile {" in ln or ".tile{" in ln]
    assert tile, "the .tile rule went missing"
    block = css.split(".eod-report .tile {", 1)[1].split("}", 1)[0]
    assert t["palette"]["card_bg"] in block
    assert t["palette"]["card_border"] in block


def test_the_css_is_still_scoped_to_the_report():
    """⚠ A must-not-change guard. ``ui.add_css`` injects this APP-WIDE in both
    frames, so a rule that lost its ``.eod-report`` prefix would restyle every
    other page. Green on both sides of the recolour, deliberately."""
    css = eod.EOD_CSS
    rules = [ln.strip() for ln in css.splitlines()
             if ln.strip() and not ln.startswith((" ", "\t")) and "{" in ln]
    assert rules, "no rules found to check"
    for rule in rules:
        assert rule.startswith(".eod-report"), f"unscoped rule: {rule}"


def test_the_exported_document_stands_on_the_apps_ground():
    """``wrap_document``'s ``<body style="background:#1e1e1e">`` was a flat grey
    under a navy app. It takes the same three-stop radial the shell paints."""
    from pages.options import theme
    doc = eod.wrap_document("<p>hi</p>", ".x{}", "T")
    assert "#1e1e1e" not in doc
    for key in ("page_bg1", "page_bg2", "page_bg3"):
        assert theme.THEME["palette"][key] in doc, key


def test_the_exported_document_carries_the_app_font_link():
    """It is served by ``/eod/file`` with no app stylesheet, so without this the
    ``font-family`` names a face the document never loads and it falls back to
    the same system face it used to hard-code. ``theme.FONT_HEAD_HTML`` is ""
    when no web font is configured, so this follows the config rather than
    pinning a URL."""
    from pages.options import theme
    doc = eod.wrap_document("<p>hi</p>", ".x{}", "T")
    assert theme.FONT_HEAD_HTML
    assert theme.FONT_HEAD_HTML in doc
    assert doc.index(theme.FONT_HEAD_HTML) < doc.index("<style>")


def test_the_module_constant_is_built_from_the_running_theme():
    from pages.options import theme
    assert eod.EOD_CSS == eod.build_eod_css(theme.THEME)


def test_a_signed_tile_value_keeps_its_profit_or_loss_colour():
    """⚠ The specificity trap, caught by MEASURING rather than by looking.

    A first draft of the recolour put ``color`` on ``.eod-report .tile .v`` -
    THREE classes, which out-specifies the two-class ``.eod-report .neg`` - so
    the two P&L tiles rendered title white while the screenshot still read as
    red to the eye (measured live: ``getComputedStyle(".v.neg").color`` was
    ``rgb(238,241,246)``). The colour sits on ``.tile`` instead: an unsigned
    value INHERITS it, and a signed one is claimed by the directly-matching
    .pos/.neg rule, because a rule that matches an element always beats one it
    merely inherits."""
    t = _sentinel_theme()
    css = eod.build_eod_css(t)
    value_rule = css.split(".eod-report .tile .v {", 1)[1].split("}", 1)[0]
    assert "color" not in value_rule, (
        ".tile .v is three classes and would out-specify .eod-report .neg")
    tile_rule = css.split(".eod-report .tile {", 1)[1].split("}", 1)[0]
    assert t["palette"]["title"] in tile_rule, "the tile lost its text colour"
    # And the fragment really does put both classes on the same element, which
    # is what makes the collision reachable at all.
    snap = dict(SAMPLE)
    snap["paper_account"] = {"has_account": True,
                             "snapshot": {"session_pnl": -70.0}}
    html = eod.summary_fragment(snap, "/eod/detail")
    assert 'class="v neg"' in html, "the tile no longer carries both classes"
