"""Alert quality: each alert type's 30-minute range against its own day.

Design: docs/plans/2026-11-14-alert-quality-design.md
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import alert_log
from services.options_svc import alert_quality as aq

CT = ZoneInfo("America/Chicago")
H = 1800        # the 30-minute horizon, in seconds


def _ts(day, hh, mm):
    return int(dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=CT).timestamp())


def _sessions(n, first=dt.date(2026, 6, 1)):
    out, d = [], first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _grid(day, start=(8, 30), end=(15, 0)):
    t, stop = _ts(day, *start), _ts(day, *end)
    return list(range(t, stop + 1, aq.GRID_SEC))


def _alert(sym, ts, kind="uoa", aid=None):
    day = dt.datetime.fromtimestamp(ts, CT).date().isoformat()
    return {"alert_id": aid or f"{sym}|{kind}|{ts}", "session_date": day, "ts": ts,
            "symbol": sym, "type": kind, "side": "call", "source": "live"}


# ── the pieces ──────────────────────────────────────────────────────────────
def test_window_range_needs_a_full_horizon():
    path = [(i * 300, s) for i, s in enumerate([100, 101, 99, 102, 100, 100, 100])]
    assert aq.window_range(path, 0, H) == pytest.approx((102 - 99) / 100)
    assert aq.window_range(path, 5, H) is None


def test_slot_is_the_central_five_minute_of_the_day():
    day = dt.date(2026, 11, 13)
    assert aq.slot(_ts(day, 10, 0)) == 120
    assert aq.slot(_ts(day, 10, 4)) == 120


def test_usual_uses_only_earlier_sessions():
    days = _sessions(12)
    paths = {}
    for k, day in enumerate(days):
        r = 0.5 if k == 11 else 0.01
        t0 = _ts(day, 10, 0)
        paths[("SPY", day.isoformat())] = [(t0 + 300 * i, 100.0) for i in range(6)] + \
            [(t0 + 1800, 100.0 * (1 + r))]
    usual = aq.usual_ranges(paths, H, history=20, min_history=10)
    s = aq.slot(_ts(days[0], 10, 0))
    assert usual[("SPY", days[11].isoformat(), s)] == pytest.approx(0.01)
    assert usual[("SPY", days[10].isoformat(), s)] == pytest.approx(0.01)
    assert ("SPY", days[9].isoformat(), s) not in usual


def test_usual_is_a_median_of_at_most_history_sessions():
    days = _sessions(8)
    paths = {}
    for k, day in enumerate(days):
        t0 = _ts(day, 10, 0)
        r = [0.9, 0.9, 0.9, 0.01, 0.02, 0.03, 0.04, 0.0][k]
        paths[("SPY", day.isoformat())] = [(t0 + 300 * i, 100.0) for i in range(6)] + \
            [(t0 + 1800, 100.0 * (1 + r))]
    usual = aq.usual_ranges(paths, H, history=4, min_history=3)
    s = aq.slot(_ts(days[0], 10, 0))
    # the four sessions before the last: 0.01, 0.02, 0.03, 0.04
    assert usual[("SPY", days[7].isoformat(), s)] == pytest.approx(0.025)


# ── scoring ─────────────────────────────────────────────────────────────────
def _spike_book(n_days=52):
    """Quiet days at 100, one spike to 103 ten minutes after each day's alert,
    the alert's time scattered over 40 slots (a step of 17) so that no slot's
    usual range, a median of 20 sessions, is wide."""
    paths, alerts = {}, []
    for k, day in enumerate(_sessions(n_days)):
        at = _ts(day, 9, 0) + 300 * ((k * 17) % 40)
        path = [(t, 103.0 if t == at + 600 else 100.0) for t in _grid(day)]
        paths[("SPY", day.isoformat())] = path
        alerts.append(_alert("SPY", at + 20))
    return paths, alerts


def test_alert_wider_than_usual_beats_a_quiet_base():
    paths, alerts = _spike_book()
    usual = aq.usual_ranges(paths, H, history=20, min_history=10)
    e = aq.score_type("uoa", alerts, paths, usual, H, n_boot=200)
    assert e["hit_rate"] == pytest.approx(1.0)
    assert e["base_rate"] == pytest.approx(0.0)
    assert e["diff"]["hi"] < 0
    assert e["days"] == 42 and e["alerts"] == 42 and e["logged"] == 52
    assert e["dropped"]["history"] == 10
    assert e["last_day"] == _sessions(52)[-1].isoformat()


def test_windows_overlapping_any_alert_are_not_base():
    """A window starting before the alert holds the alert's move: it is no
    'other time' of the day."""
    paths, alerts = _spike_book(12)
    usual = aq.usual_ranges(paths, H, history=20, min_history=10)
    e = aq.score_type("uoa", alerts, paths, usual, H, n_boot=50)
    assert e["base_rate"] == pytest.approx(0.0)


def test_another_types_alert_also_removes_base_windows():
    paths, alerts = _spike_book(12)
    day = _sessions(12)[-1]
    # A HIRO alert at 14:00 on the last day, with its own spike.
    t = _ts(day, 14, 0)
    paths[("SPY", day.isoformat())] = [(ts, 103.0 if ts == t + 600 else s)
                                      for ts, s in paths[("SPY", day.isoformat())]]
    alerts.append(_alert("SPY", t, kind="hiro_surge"))
    usual = aq.usual_ranges(paths, H, history=20, min_history=10)
    e = aq.score_type("uoa", alerts, paths, usual, H, n_boot=50)
    assert e["base_rate"] == pytest.approx(0.0)


def test_busy_day_lifts_both_sides():
    paths, alerts = {}, []
    for k, day in enumerate(_sessions(14)):
        paths[("SPY", day.isoformat())] = [(t, 100.0 + (i % 2)) for i, t in
                                           enumerate(_grid(day))]
        alerts.append(_alert("SPY", _ts(day, 10, 0)))
    usual = aq.usual_ranges(paths, H, history=20, min_history=10)
    e = aq.score_type("uoa", alerts, paths, usual, H, n_boot=50)
    assert e["alerts"] == 4
    assert e["hit_rate"] == pytest.approx(e["base_rate"])
    assert e["diff"]["lo"] <= 0 <= e["diff"]["hi"]


def test_drops_are_counted_not_scored():
    days = _sessions(12)
    paths = {}
    for day in days:
        paths[("SPY", day.isoformat())] = [(t, 100.0) for t in _grid(day)]
        paths[("IWM", day.isoformat())] = [(t, 100.0) for t in
                                           _grid(day, (10, 0), (10, 30))]
    last, first = days[-1], days[0]
    alerts = [_alert("QQQ", _ts(last, 10, 0)),            # no price that day
              _alert("SPY", _ts(last, 14, 55)),           # not a full half hour left
              _alert("SPY", _ts(first, 10, 0)),           # no earlier sessions
              _alert("IWM", _ts(last, 10, 0))]            # nothing else that day
    usual = aq.usual_ranges(paths, H, history=20, min_history=10)
    e = aq.score_type("uoa", alerts, paths, usual, H, n_boot=50)
    assert e["dropped"] == {"price": 1, "horizon": 1, "history": 1, "base": 1}
    assert e["alerts"] == 0 and e["logged"] == 4
    assert e["diff"] is None and e["days"] == 0


def test_an_alert_before_the_first_price_has_no_price():
    day = _sessions(1)[0]
    paths = {("SPY", day.isoformat()): [(t, 100.0) for t in _grid(day, (10, 0))]}
    e = aq.score_type("uoa", [_alert("SPY", _ts(day, 9, 0))], paths, {}, H, n_boot=10)
    assert e["dropped"]["price"] == 1


# ── the stores ──────────────────────────────────────────────────────────────
def _archive(path, rows):
    import gex_archive
    conn = gex_archive.connect_archive(path)
    conn.executemany("INSERT INTO snapshots (symbol, view, ts, spot, carried_age_sec) "
                     "VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_load_paths_keeps_fetched_gex_rows_only(tmp_path):
    day = dt.date(2026, 11, 13)
    t = _ts(day, 10, 0)
    rows = [("SPY", "gex", t + 300 * i, 100.0, None) for i in range(7)]
    rows += [("SPY", "gex", t + 360, 150.0, 30.0),     # carried: a model, not a price
             ("SPY", "dex", t + 420, 150.0, None),     # another view
             ("SPY", "gex", t + 480, 0.0, None),       # no price
             ("FOP.ES", "gex", t, 6000.0, None)]       # the futures feed
    db = tmp_path / "archive.db"
    _archive(db, rows)
    paths = aq.load_paths(db)
    assert list(paths) == [("SPY", "2026-11-13")]
    assert aq.window_range(paths[("SPY", "2026-11-13")], 0, H) == 0.0
    assert aq.load_paths(tmp_path / "none.db") == {}


def test_evaluate_reads_both_stores(monkeypatch, tmp_path):
    from shared import model_health_config as cfg
    monkeypatch.setattr(cfg, "alerts_types", lambda: ["uoa", "crossover"])
    paths, alerts = _spike_book(12)
    rows = [(sym, "gex", ts, s, None) for (sym, _d), p in paths.items() for ts, s in p]
    archive, log = tmp_path / "archive.db", tmp_path / "log.db"
    _archive(archive, rows)
    alert_log.record([{"id": a["alert_id"], "type": "uoa", "side": "call",
                       "symbol": "SPY", "ts": a["ts"]} for a in alerts], db_path=log)
    uoa, cross = aq.evaluate(log_path=log, archive_path=archive)
    assert uoa["type"] == "uoa" and uoa["alerts"] == 2 and uoa["logged"] == 12
    assert cross == {**cross, "type": "crossover", "alerts": 0, "logged": 0,
                     "days": 0, "diff": None}


def test_evaluate_with_no_stores_is_no_data(tmp_path):
    out = aq.evaluate(log_path=tmp_path / "a.db", archive_path=tmp_path / "b.db")
    assert [e["type"] for e in out] == ["uoa", "big_delta", "hiro_surge", "hiro_flip",
                                       "crossover"]
    assert all(e["diff"] is None and e["logged"] == 0 for e in out)


def test_the_window_starts_after_the_alert_not_before():
    """An alert fired BY a move must not be scored on that move: its window
    starts at the first price at or after it."""
    day = _sessions(1)[0]
    t = _ts(day, 10, 0)
    path = [(ts, 100.0 if ts <= t else 103.0) for ts in _grid(day)]
    i = aq._start(path, t + 240)                 # the alert at 10:04
    assert path[i][0] == t + 300                 # the 10:05 price
    assert aq.window_range(path, i, H) == 0.0
    assert path[aq._start(path, t)][0] == t      # on a grid point: that point
    assert aq._start(path, path[-1][0] + 60) is None
