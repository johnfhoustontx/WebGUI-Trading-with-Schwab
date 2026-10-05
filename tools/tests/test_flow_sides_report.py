"""tools/flow_sides_report.py -- do the two bought/sold sources agree?

The poll's tally AT the alert makes the comparison fair: poll-since-the-alert
against stream-since-the-alert, over the same window. The report must never
count a row it cannot compare, and never print a figure it did not measure.
Design: docs/plans/2026-10-04-flow-alert-sides-design.md.

``tools/`` has no ``__init__.py``; the sys.path insert mirrors
tools/tests/test_hiro_report.py.
"""
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import flow_sides_report as fr  # noqa: E402

DAY = "2026-10-06"


def _row(aid, poll, at, stream, **over):
    """``poll`` / ``at`` / ``stream`` are (bought, sold, unlabelled) or None."""
    row = {"session_date": DAY, "alert_id": aid, "symbol": aid.split("|")[0],
           "osi": f"{aid:<21}", "side": "call", "strike": 100.0,
           "expiry": "2026-10-09", "alert_type": "uoa", "fired_ts": 1000,
           "oi_prev": 100.0, "volume": float(sum(poll)),
           "poll_bought": float(poll[0]), "poll_sold": float(poll[1]),
           "poll_unlabelled": float(poll[2])}
    for name, t in (("at", at), ("stream", stream)):
        for key, v in zip(("bought", "sold", "unlabelled"), t or (None,) * 3):
            row[f"{name}_{key}"] = None if v is None else float(v)
    row.update(over)
    return row


def test_since_alert_is_the_running_tally_minus_the_tally_at_the_alert():
    row = _row("A|uoa", poll=(900, 500, 200), at=(300, 100, 100), stream=(580, 410, 110))
    assert fr.since_alert(row) == {"bought": 600.0, "sold": 400.0, "unlabelled": 100.0}


def test_since_alert_is_none_without_an_at_alert_tally():
    assert fr.since_alert(_row("A|uoa", (900, 500, 200), None, (1, 1, 1))) is None


def test_since_alert_never_goes_negative():
    # A restart moves labelled volume nowhere, but a clamp is cheaper than an
    # argument about whether it can.
    row = _row("A|uoa", poll=(100, 50, 10), at=(120, 50, 10), stream=(1, 1, 1))
    assert fr.since_alert(row)["bought"] == 0.0


def test_lean_is_bought_minus_sold_over_the_whole_tally():
    assert fr.lean({"bought": 600.0, "sold": 400.0, "unlabelled": 0.0}) == pytest.approx(0.2)
    assert fr.lean({"bought": 600.0, "sold": 400.0, "unlabelled": 1000.0}) == pytest.approx(0.1)
    assert fr.lean({"bought": 0.0, "sold": 0.0, "unlabelled": 0.0}) is None


def test_compare_counts_only_rows_both_sources_measured():
    rows = [
        _row("A|uoa", (900, 500, 200), (300, 100, 100), (590, 400, 110)),   # comparable
        _row("B|uoa", (900, 500, 200), None, (590, 400, 110)),              # no at-alert tally
        _row("C|uoa", (900, 500, 200), (300, 100, 100), None),              # never streamed
        _row("D|uoa", (310, 105, 100), (300, 100, 100), (9, 5, 1)),         # too little since
    ]
    out = fr.compare(rows, min_since=500)
    assert out["flagged"] == 4
    assert out["compared"] == 1
    assert out["skipped"] == {"no at-alert tally": 1, "not streamed": 1,
                              "under the minimum since the alert": 1}
    (r,) = out["rows"]
    assert r["alert_id"] == "A|uoa"
    assert r["poll_total"] == 1100.0 and r["stream_total"] == 1100.0
    assert r["coverage"] == pytest.approx(1.0)
    assert r["poll_lean"] == pytest.approx(200 / 1100)
    assert r["stream_lean"] == pytest.approx(190 / 1100)


def test_compare_measures_agreement_and_says_how_many_it_rests_on():
    rows = [
        _row("A|uoa", (1700, 1300, 0), (700, 300, 0), (1000, 1000, 0)),   # poll 0, stream 0
        _row("B|uoa", (1600, 400, 0), (0, 0, 0), (1500, 500, 0)),         # both bought
        _row("C|uoa", (400, 1600, 0), (0, 0, 0), (600, 1400, 0)),         # both sold
        _row("D|uoa", (1200, 800, 0), (0, 0, 0), (900, 1100, 0)),         # opposite
    ]
    out = fr.compare(rows, min_since=500)
    assert out["compared"] == 4
    # A has no lean on either source: it cannot agree or disagree.
    assert (out["same_way"], out["opposite"], out["no_lean"]) == (2, 1, 1)
    # |lean difference| per row is 0.0, 0.1, 0.2, 0.3.
    assert out["median_gap"] == pytest.approx(0.15)


def test_median_gap_is_the_median_absolute_lean_difference():
    rows = [
        _row("A|uoa", (1000, 1000, 0), (0, 0, 0), (1000, 1000, 0)),   # gap 0.00
        _row("B|uoa", (1600, 400, 0), (0, 0, 0), (1500, 500, 0)),     # .60 vs .50 -> 0.10
        _row("C|uoa", (1200, 800, 0), (0, 0, 0), (900, 1100, 0)),     # .20 vs -.10 -> 0.30
    ]
    assert fr.compare(rows, min_since=500)["median_gap"] == pytest.approx(0.10)


def test_compare_of_nothing_reports_nothing_rather_than_zeros():
    out = fr.compare([], min_since=500)
    assert out["compared"] == 0
    assert out["median_gap"] is None and out["median_coverage"] is None
    assert out["correlation"] is None


def test_correlation_needs_three_rows_and_some_spread():
    two = [_row("A|uoa", (1600, 400, 0), (0, 0, 0), (1500, 500, 0)),
           _row("B|uoa", (400, 1600, 0), (0, 0, 0), (600, 1400, 0))]
    assert fr.compare(two, min_since=500)["correlation"] is None
    three = two + [_row("C|uoa", (1000, 1000, 0), (0, 0, 0), (1050, 950, 0))]
    assert fr.compare(three, min_since=500)["correlation"] == pytest.approx(1.0, abs=0.02)


def _db(tmp_path, rows):
    import gex_history_db as gh
    path = tmp_path / "g.db"
    conn = sqlite3.connect(str(path))
    gh.init_schema(conn)
    gh.upsert_flow_contract_days(conn, rows)
    conn.close()
    return path


def test_the_report_prints_what_it_measured(tmp_path, capsys):
    path = _db(tmp_path, [
        _row("SPY|uoa|call|770", (1600, 400, 0), (0, 0, 0), (1500, 500, 0)),
        _row("QQQ|uoa|call|750", (900, 500, 200), None, (590, 400, 110))])
    assert fr.main(["--date", DAY, "--db", str(path), "--min", "500"]) == 0
    text = capsys.readouterr().out
    assert DAY in text
    assert "compared 1 of 2" in text
    assert "no at-alert tally: 1" in text
    assert "SPY|uoa|call|770" in text and "QQQ|uoa|call|750" not in text


def test_the_report_says_so_when_there_is_nothing_to_compare(tmp_path, capsys):
    path = _db(tmp_path, [_row("QQQ|uoa", (900, 500, 200), None, None)])
    assert fr.main(["--date", DAY, "--db", str(path)]) == 0
    text = capsys.readouterr().out
    assert "compared 0 of 1" in text
    assert "nothing to compare" in text.lower()
    assert "%" not in text.split("nothing to compare")[-1]     # no invented figure after it


def test_the_report_defaults_to_the_newest_stored_session(tmp_path, capsys):
    path = _db(tmp_path, [
        _row("A|uoa", (1600, 400, 0), (0, 0, 0), (1500, 500, 0), session_date="2026-10-05"),
        _row("B|uoa", (1600, 400, 0), (0, 0, 0), (1500, 500, 0), session_date=DAY)])
    assert fr.main(["--db", str(path)]) == 0
    assert DAY in capsys.readouterr().out


def test_a_store_not_yet_migrated_is_reported_not_raised(tmp_path, capsys):
    # The table as prod first created it, before the at-alert columns. The
    # report is read-only and cannot migrate it.
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE flow_contract_days (session_date TEXT, alert_id TEXT, "
                 "fired_ts INTEGER)")
    conn.execute("INSERT INTO flow_contract_days VALUES (?, 'a', 1)", (DAY,))
    conn.commit()
    conn.close()
    assert fr.main(["--db", str(path)]) == 0
    assert "not ready" in capsys.readouterr().out


def test_the_report_opens_the_store_read_only():
    import inspect
    assert "mode=ro" in inspect.getsource(fr._connect)
