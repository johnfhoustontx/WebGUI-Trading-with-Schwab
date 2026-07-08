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
