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
    "driver_approvals": {"grade": "B", "status": "no_trade"},
    "driver_performance": {"summary": {}, "trades": []},
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


def test_driver_section_shows_grade_status_and_perf():
    appr = {"date": "2026-06-18", "grade": "B+", "status": "approved",
            "decision": "approved", "pnl_today": 210.0,
            "proposed_trades": [{"symbol": "MES", "bucket": "A", "side": "long"}]}
    perf = {"summary": {"win_rate": 0.62, "realized_pnl": 1500.0, "trades": 21},
            "trades": []}
    html = eod.driver_section(appr, perf)
    assert "B+" in html and "approved" in html
    assert "MES" in html
    assert "62%" in html
    assert 'class="none"' in eod.driver_section(None, None)


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
    assert "no_trade" in html  # driver status surfaced


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
