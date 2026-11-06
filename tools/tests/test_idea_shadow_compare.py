"""tools/idea_shadow_compare.py: the shadow-versus-hourly comparison's arithmetic."""
import pytest

from tools import idea_shadow_compare as C


def _row(source, pnl, loss=100.0, status="CLOSED", grade="Good", day="2026-11-10"):
    return {"source": source, "realized_pnl": pnl, "max_loss_total": loss,
            "status": status, "idea_grade": grade, "idea_posted": f"{day}T09:40:00"}


def test_r_is_pnl_over_max_loss():
    assert C.r_multiple(_row("trade_idea", 50.0, 200.0)) == 0.25
    assert C.r_multiple(_row("trade_idea", None)) is None
    assert C.r_multiple(_row("trade_idea", 10.0, 0.0)) is None


def test_open_rows_and_rows_without_an_r_are_left_out():
    rows = [_row("idea_shadow", 50.0), _row("idea_shadow", -70.0, day="2026-11-11"),
            _row("idea_shadow", 999.0, status="OPEN"), _row("idea_shadow", None)]
    s = C.summarize(rows)
    assert s["n"] == 2 and s["win"] == 0.5 and s["days"] == 2
    assert s["mean_r"] == pytest.approx(-0.10) and s["pnl"] == -20.0


def test_no_closed_rows_is_none():
    assert C.summarize([_row("idea_shadow", 5.0, status="OPEN")]) is None
    assert C.line("x", None).endswith("no closed trades")


def test_the_report_splits_by_source_then_grade():
    rows = [_row("trade_idea", 20.0), _row("trade_idea", 40.0, grade="Strong"),
            _row("idea_shadow", 60.0, grade="Strong"), _row("daily_condor", 1.0)]
    text = C.report(rows)
    assert text[0].startswith("Hourly posts") and "n=   2" in text[0]
    assert [t.split()[1] for t in text[1:3]] == ["Good", "Strong"]
    assert text[3].startswith("Shadow picks") and "n=   1" in text[3]
    assert len(text) == 5          # the condor row is in neither


def test_load_reads_only_the_two_sources(tmp_path):
    import sqlite3
    db = tmp_path / "trades.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE trades (source TEXT, realized_pnl REAL)")
    conn.executemany("INSERT INTO trades VALUES (?, ?)",
                     [("trade_idea", 1.0), ("idea_shadow", 2.0), ("daily_condor", 3.0)])
    conn.commit()
    conn.close()
    rows = C.load(db.as_posix())
    assert sorted(r["source"] for r in rows) == ["idea_shadow", "trade_idea"]
