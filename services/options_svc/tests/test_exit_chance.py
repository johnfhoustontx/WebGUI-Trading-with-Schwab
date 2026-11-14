"""The exit chance: rows, labels, the walk-forward and its score."""
import sqlite3
from datetime import datetime, timedelta

import pytest

from services.options_svc import exit_chance as ec

T0 = datetime(2030, 1, 7, 9, 0, tzinfo=ec.CENTRAL)


def _trade(fracs, *, close_frac=None, strategy="PCS", step_min=5, reason="EXPIRED",
           closed=None, spot=505.0, delta=0.15, entry_delta=0.15, day="2030-01-07",
           first_seen=T0, sid="t1", kind="SWING", iv_rank=40.0, short=500.0,
           call_short=None, expiration="2030-01-17"):
    marks = [(first_seen + timedelta(minutes=step_min * i), f, spot, delta)
             for i, f in enumerate(fracs)]
    return ec.Trade(signal_id=sid, kind=kind, strategy=strategy, entry_day=day,
                    first_seen=first_seen,
                    closed=closed or (marks[-1][0] + timedelta(minutes=5)),
                    expiration=expiration, short_strike=short, call_short=call_short,
                    entry_short_delta=entry_delta, entry_iv_rank=iv_rank,
                    exit_reason=reason, close_frac=close_frac, marks=marks)


# ── rows ────────────────────────────────────────────────────────────────────
def test_rows_stop_at_first_target_hit():
    out = ec.rows([_trade([-0.1, 0.2, 0.55, 0.3], close_frac=0.3)],
                  tp_frac=0.5, thin_min=5, min_hold=30)
    assert len(out) == 2
    assert all(r.y == 1 for r in out)


def test_rows_label_zero_when_series_ends_short():
    out = ec.rows([_trade([-0.1, -0.4], close_frac=-1.2)], tp_frac=0.5, thin_min=5,
                  min_hold=30)
    assert [r.y for r in out] == [0, 0]


def test_close_counts_as_reaching():
    out = ec.rows([_trade([0.1, 0.2, 0.3], close_frac=1.0)], tp_frac=0.5, thin_min=5,
                  min_hold=30)
    assert out and all(r.y == 1 for r in out)


def test_thinning_keeps_one_mark_per_thin_min():
    out = ec.rows([_trade([0.0] * 13, close_frac=0.0)], tp_frac=0.5, thin_min=30,
                  min_hold=30)
    assert len(out) == 3
    assert [r.x["pnl_frac"] for r in out] == [0.0, 0.0, 0.0]


def test_early_time_stop_left_out():
    tr = _trade([-0.1, -0.1], close_frac=-0.1, reason="TIME_STOP",
                closed=T0 + timedelta(minutes=10))
    assert ec.rows([tr], tp_frac=0.5, thin_min=5, min_hold=30) == []
    late = _trade([-0.1, -0.1], close_frac=-0.1, reason="TIME_STOP",
                  closed=T0 + timedelta(minutes=45))
    assert len(ec.rows([late], tp_frac=0.5, thin_min=5, min_hold=30)) == 2


def test_first_mark_hit_gives_no_rows():
    assert ec.rows([_trade([0.6, 0.2], close_frac=0.2)], tp_frac=0.5, thin_min=5,
                   min_hold=30) == []


def test_inputs_are_point_in_time():
    out = ec.rows([_trade([-0.3, 0.2, -0.1], close_frac=-1.0)], tp_frac=0.5,
                  thin_min=5, min_hold=30)
    assert [(r.x["peak_frac"], r.x["worst_frac"]) for r in out] == [
        (-0.3, -0.3), (0.2, -0.3), (0.2, -0.3)]
    assert out[0].x["cushion"] == pytest.approx((505 - 500) / 505 * 100)
    assert out[0].x["life_used"] == pytest.approx(0.0)
    assert 0.0 < out[2].x["life_used"] < 0.01


def test_cushion_by_structure():
    assert ec._cushion("CCS", 505.0, 510.0, None) == pytest.approx((510 - 505) / 505 * 100)
    ic = ec._cushion("IC", 505.0, 500.0, 507.0)
    assert ic == pytest.approx((507 - 505) / 505 * 100)
    assert ec._cushion("PCS", None, 500.0, None) is None


def test_missing_inputs_stay_none():
    out = ec.rows([_trade([0.0, 0.1], close_frac=0.0, spot=None, delta=None,
                          iv_rank=None)], tp_frac=0.5, thin_min=5, min_hold=30)
    assert out[0].x["cushion"] is None
    assert out[0].x["short_delta"] is None
    assert out[0].x["delta_change"] is None
    assert out[0].x["entry_iv_rank"] is None


def test_structure_indicators():
    out = ec.rows([_trade([0.0, 0.1], close_frac=0.0, strategy="IC", call_short=510.0)],
                  tp_frac=0.5, thin_min=5, min_hold=30)
    assert (out[0].x["is_ic"], out[0].x["is_ccs"]) == (1.0, 0.0)


# ── load ────────────────────────────────────────────────────────────────────
def _db(tmp_path):
    import signal_db
    path = tmp_path / "signals.db"
    signal_db.init_db(path)
    return path


def _insert(conn, sid, *, strategy="PCS", scanner_type="INCOME", credit=1.0,
            reason="EXPIRED", realized=1.0, legs=None, marks=()):
    conn.execute(
        "INSERT INTO signals(signal_id, scanner_type, symbol, strategy, short_strike, "
        "long_strike, expiration, entry_credit, entry_short_delta, entry_iv_rank, "
        "first_seen_ts, first_seen_date, dedup_key, status, legs_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, scanner_type, "SPY", strategy, 500, 495, "2030-01-17", credit, -0.15, 40,
         T0.isoformat(), "2030-01-07", sid, "CLOSED", legs))
    conn.execute("INSERT INTO signal_outcomes(signal_id, close_ts, close_date, "
                 "realized_pnl, exit_reason) VALUES (?,?,?,?,?)",
                 (sid, (T0 + timedelta(days=2)).isoformat(), "2030-01-09", realized, reason))
    for i, (pnl, pct) in enumerate(marks):
        conn.execute("INSERT INTO signal_marks(signal_id, mark_ts, mark_date, "
                     "unrealized_pnl, pnl_pct_of_credit, current_underlying, "
                     "current_short_delta) VALUES (?,?,?,?,?,?,?)",
                     (sid, (T0 + timedelta(minutes=5 * i)).isoformat(), "2030-01-07",
                      pnl, pct, 505.0, -0.15))


def test_load_reads_per_share_trade(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    # Per share: a $1.00 credit, marks at +$0.20 (20%) and +$0.30 (30%), closed +$0.80.
    _insert(conn, "share", realized=0.80, marks=[(0.20, 20.0), (0.30, 30.0)])
    _insert(conn, "manual", reason="MANUAL_CLOSE", marks=[(10, 10.0), (20, 20.0)])
    _insert(conn, "debit", legs='[{"k": 1}]', marks=[(10, 10.0), (20, 20.0)])
    _insert(conn, "tracked", scanner_type="SWING_STRUCT", marks=[(10, 10.0), (20, 20.0)])
    _insert(conn, "callspread", strategy="LONG_CALL", marks=[(10, 10.0), (20, 20.0)])
    _insert(conn, "onemark", marks=[(10, 10.0)])
    conn.commit()
    conn.close()
    trades = ec.load(path)
    assert [t.signal_id for t in trades] == ["share"]
    (t,) = trades
    assert t.close_frac == pytest.approx(0.80)
    assert [m[1] for m in t.marks] == pytest.approx([0.20, 0.30])
    assert t.kind == "INCOME" and t.entry_day == "2030-01-07"
