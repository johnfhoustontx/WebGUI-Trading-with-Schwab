"""Model Health (``model_health``): how the app's stated chances are scored.

The scalp tests seed a ``tmp_path`` store with calls and outcome rows written
directly (no study rebuild), so every expected number can be worked out by hand.
The signal tests replace the signal loader with plain dicts.
Design: docs/plans/2026-11-14-model-health-design.md.
"""
import datetime as dt
import json

import pytest

from services import _degrade
from shared.bus import Bus
from services.options_svc import model_health as mh
from services.options_svc import scalp_store, scalp_study
from shared import market_calendar as mc
from shared import model_health_config, scalp_config

ROLES = ("long_with", "long_against", "credit_with", "credit_against")


def trading_days(n, start=dt.date(2026, 3, 2)):
    out, d = [], start
    while len(out) < n:
        if mc.is_trading_day(d):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "scalp.db"
    scalp_store.connect(path).close()
    return path


@pytest.fixture
def fake_bus():
    return Bus(fake=True)


@pytest.fixture(autouse=True)
def _no_signals(monkeypatch):
    monkeypatch.setattr(mh, "_load_signal_rows", lambda db_path=None: [])
    from services.options_svc import exit_chance
    monkeypatch.setattr(exit_chance, "evaluate", lambda db_path=None: [])
    model_health_config.reset_cache()


def _scale():
    return scalp_study._scale_key(scalp_config.iv_scale())


def put_morning(db, day, symbol, by_role, results, readings=None):
    """One 08:36 call with ``by_role`` chances, and its 5-minute outcomes."""
    pushes = [{"factor": k, "reading": v} for k, v in (readings or {}).items()]
    body = {"chance": {"by_role": by_role, "pushes": pushes}}
    conn = scalp_store.connect(db)
    with conn:
        conn.execute("INSERT INTO calls (day, symbol, json) VALUES (?, ?, ?)",
                     (day, symbol, json.dumps(body)))
        for role, result in results.items():
            put_outcome(conn, day, symbol, role, result)
    conn.close()


def put_outcome(conn, day, symbol, role, result):
    conn.execute(
        "INSERT INTO outcomes (day, symbol, iv_scale, role, structure, bucket, result, "
        "minutes, entry_value, end_pnl_pct, both_in_bar, interval) "
        "VALUES (?, ?, ?, ?, 'x', 'b', ?, 1, 1.0, 0.0, 0, ?)",
        (day, symbol, _scale(), role, result, scalp_study.VERDICT_INTERVAL))


def put_intraday(db, day, slot, symbol, by_role, results):
    body = {"chance": {"by_role": by_role, "pushes": []}}
    conn = scalp_store.connect(db)
    with conn:
        conn.execute("INSERT INTO intraday_calls (day, slot, symbol, json) "
                     "VALUES (?, ?, ?, ?)", (day, slot, symbol, json.dumps(body)))
        for role, result in results.items():
            conn.execute(
                "INSERT INTO intraday_outcomes (day, slot, symbol, iv_scale, role, "
                "structure, block, bucket, result, minutes, entry_value, both_in_bar) "
                "VALUES (?, ?, ?, ?, ?, 'x', ?, 'b', ?, 1, 1.0, 0)",
                (day, slot, symbol, _scale(), role, scalp_config.intraday_block(slot),
                 result))
    conn.close()


def model(payload, key):
    return next(m for m in payload["models"] if m["key"] == key)


# ── the verdict rule ────────────────────────────────────────────────────────
@pytest.mark.parametrize("days,diff,want", [
    (0, None, "no_data"),
    (19, {"lo": -0.2, "hi": -0.1}, "too_few_days"),
    (20, {"lo": -0.2, "hi": -0.01}, "beats"),
    (20, {"lo": 0.01, "hi": 0.2}, "does_not_beat"),
    (20, {"lo": -0.01, "hi": 0.02}, "matches"),
    (20, {"lo": -0.1, "hi": 0.0}, "matches"),          # an end AT zero matches
    (20, {"lo": 0.0, "hi": 0.1}, "matches"),
    (20, None, "no_data"),
])
def test_verdict_order_and_edges(days, diff, want):
    assert mh.verdict(days, 20, diff) == want


# ── the scalp adapters ──────────────────────────────────────────────────────
def test_morning_live_rows_join_each_trade_chance_to_its_outcome(db):
    days = trading_days(3)
    for day in days:
        put_morning(db, day, "SPY", {r: 0.6 for r in ROLES},
                    {"long_with": "win", "long_against": "loss",
                     "credit_with": "timeout", "credit_against": "skip"})
    rows, _inputs = mh.scalp_rows("morning", db)
    # Day one has no earlier day, so no usual rate: left out. A skip is no label.
    assert {r.day for r in rows} == set(days[1:])
    assert len(rows) == 2 * 3
    by_role = {(r.day, r.group[1]): r for r in rows}
    assert by_role[(days[1], "long_with")].y == 1
    assert by_role[(days[1], "long_against")].y == 0
    assert by_role[(days[1], "credit_with")].y == 0
    assert all(r.p == 0.6 for r in rows)
    # Usual rate of long_with on day two: day one's one win of one.
    assert by_role[(days[1], "long_with")].usual == pytest.approx(1.0)
    assert by_role[(days[1], "long_against")].usual == pytest.approx(0.0)


def test_usual_rate_reads_the_full_outcome_history_not_only_live_days(db):
    # Two days with outcomes and no call, then one live day: the usual rate on
    # the live day comes from the two earlier outcome days.
    days = trading_days(3)
    conn = scalp_store.connect(db)
    with conn:
        put_outcome(conn, days[0], "SPY", "long_with", "win")
        put_outcome(conn, days[1], "SPY", "long_with", "loss")
    conn.close()
    put_morning(db, days[2], "SPY", {"long_with": 0.7}, {"long_with": "win"})
    rows, _ = mh.scalp_rows("morning", db)
    assert len(rows) == 1
    assert rows[0].usual == pytest.approx(0.5)


def test_a_call_with_no_chance_is_left_out(db):
    days = trading_days(2)
    put_morning(db, days[0], "SPY", {"long_with": 0.5}, {"long_with": "win"})
    conn = scalp_store.connect(db)
    with conn:
        conn.execute("INSERT INTO calls (day, symbol, json) VALUES (?, 'SPY', ?)",
                     (days[1], json.dumps({"chance": None})))
        put_outcome(conn, days[1], "SPY", "long_with", "win")
    conn.close()
    rows, _ = mh.scalp_rows("morning", db)
    assert rows == []


def test_intraday_usual_rate_is_per_block(db):
    days = trading_days(2)
    for day in days:
        put_intraday(db, day, "08:51", "SPY", {"long_with": 0.5}, {"long_with": "win"})
        put_intraday(db, day, "13:51", "SPY", {"long_with": 0.5}, {"long_with": "loss"})
    rows, _ = mh.scalp_rows("intraday", db)
    usual = {r.slot: r.usual for r in rows if r.day == days[1]}
    assert usual == {"08:51": pytest.approx(1.0), "13:51": pytest.approx(0.0)}


def test_each_day_symbol_and_trade_weighs_one(db):
    days = trading_days(2)
    for day in days:
        for slot in ("08:51", "09:06", "09:21"):
            put_intraday(db, day, slot, "SPY", {"long_with": 0.5}, {"long_with": "win"})
    rows, _ = mh.scalp_rows("intraday", db)
    live = [r for r in rows if r.day == days[1]]
    assert len(live) == 3
    assert sum(r.weight for r in live) == pytest.approx(1.0)


# ── scoring a model ─────────────────────────────────────────────────────────
def test_a_model_on_too_few_days_never_gets_a_verdict(db):
    days = trading_days(6)
    for day in days:
        put_morning(db, day, "SPY", {"long_with": 0.9}, {"long_with": "win"})
    m = model(mh.build(scalp_db=db), "scalp_morning")
    assert m["verdict"] == "too_few_days"
    assert m["days"] == 5 and m["min_days"] == 20
    assert m["brier"] is not None and m["brier_usual"] is not None


def test_a_model_that_is_closer_than_the_usual_rate_beats_it(db, monkeypatch):
    # Wins and losses alternate by symbol; the chance says 0.9 on the winners
    # and 0.1 on the losers, while the usual rate sits near one half.
    monkeypatch.setattr(model_health_config, "min_days", lambda: 10)
    days = trading_days(30)
    for i, day in enumerate(days):
        win = i % 2 == 0
        put_morning(db, day, "SPY", {"long_with": 0.9 if win else 0.1},
                    {"long_with": "win" if win else "loss"})
    m = model(mh.build(scalp_db=db), "scalp_morning")
    assert m["verdict"] == "beats"
    assert m["diff"]["hi"] < 0
    assert len(m["reliability"]) == 10


def test_a_model_further_off_than_the_usual_rate_does_not_beat_it(db, monkeypatch):
    monkeypatch.setattr(model_health_config, "min_days", lambda: 10)
    days = trading_days(30)
    for i, day in enumerate(days):
        win = i % 2 == 0
        put_morning(db, day, "SPY", {"long_with": 0.1 if win else 0.9},
                    {"long_with": "win" if win else "loss"})
    m = model(mh.build(scalp_db=db), "scalp_morning")
    assert m["verdict"] == "does_not_beat"
    assert m["diff"]["lo"] > 0


def test_an_empty_store_reads_no_data(db):
    payload = mh.build(scalp_db=db)
    assert payload["data_date"] is None
    assert {m["key"]: m["verdict"] for m in payload["models"]} == {
        "scalp_morning": "no_data", "scalp_intraday": "no_data"}


# ── the refit history row ───────────────────────────────────────────────────
def _wf(brier, usual, beats, weeks=30):
    return {"weeks": weeks, "brier": brier, "brier_usual": usual, "beats_usual": beats,
            "calibration": []}


def test_history_pools_the_four_trades_by_rows():
    bodies = {("morning", r): {"rows": 100, "n_days": 190,
                               "walk_forward": _wf(0.29, 0.26, False)} for r in ROLES}
    bodies[("morning", "long_with")]["rows"] = 300
    bodies[("morning", "long_with")]["walk_forward"] = _wf(0.25, 0.24, False)
    h = mh.history_row(bodies, "morning")
    assert h["brier"] == pytest.approx((300 * 0.25 + 3 * 100 * 0.29) / 600)
    assert h["brier_usual"] == pytest.approx((300 * 0.24 + 3 * 100 * 0.26) / 600)
    assert h["verdict"] == "does_not_beat"
    assert h["beat_trades"] == 0 and h["weeks"] == 30 and h["days"] == 190


def test_history_beats_only_when_every_trade_beats():
    bodies = {("intraday", r): {"rows": 10, "n_days": 199,
                                "walk_forward": _wf(0.20, 0.25, True)} for r in ROLES}
    assert mh.history_row(bodies, "intraday")["verdict"] == "beats"
    bodies[("intraday", "credit_with")]["walk_forward"] = _wf(0.20, 0.25, False)
    h = mh.history_row(bodies, "intraday")
    assert h["verdict"] == "matches" and h["beat_trades"] == 3


def test_history_with_nothing_scored_is_none():
    bodies = {("morning", r): {"rows": 0, "n_days": 0,
                               "walk_forward": _wf(None, None, False, weeks=0)}
              for r in ROLES}
    assert mh.history_row(bodies, "morning") is None
    assert mh.history_row({}, "morning") is None


def test_build_attaches_the_latest_refit_history(db):
    scalp_store.save_prob_models(1, "2026-11-07T08:00:00-06:00", {
        ("morning", r): {"rows": 50, "n_days": 191, "walk_forward": _wf(0.29, 0.26, False)}
        for r in ROLES}, db_path=db)
    m = model(mh.build(scalp_db=db), "scalp_morning")
    assert m["history"]["verdict"] == "does_not_beat"
    assert model(mh.build(scalp_db=db), "scalp_intraday")["history"] is None


# ── drift ───────────────────────────────────────────────────────────────────
def test_drift_flags_a_reading_that_went_missing(monkeypatch):
    monkeypatch.setattr(model_health_config, "recent_sessions", lambda: 3)
    monkeypatch.setattr(model_health_config, "min_reference_sessions", lambda: 5)
    days = trading_days(10)
    inputs = []
    for i, day in enumerate(days):
        recent = i >= 7
        inputs.append((day, {"sentiment": None if recent else 0.1 * i,
                             "slope_5": 0.1 * (i % 3)}))
    status, flagged = mh.drift(inputs)
    assert status == "shifted"
    assert [f["input"] for f in flagged] == ["sentiment"]
    assert flagged[0]["missing_before"] == 0.0 and flagged[0]["missing_after"] == 1.0
    assert flagged[0]["band"] == "shifted"
    assert 0 < flagged[0]["p"] < 0.05


def test_drift_does_not_flag_sampling_noise(monkeypatch):
    """Every session drawn from the same spread: a few recent sessions can read
    a high index by chance, and the session shuffle keeps that from a flag."""
    import random
    monkeypatch.setattr(model_health_config, "recent_sessions", lambda: 5)
    monkeypatch.setattr(model_health_config, "min_reference_sessions", lambda: 8)
    rng = random.Random(7)
    days = trading_days(40)
    inputs = [(d, {f"x{j}": rng.gauss(0, 1) for j in range(6)})
              for d in days for _k in range(3)]
    status, flagged = mh.drift(inputs)
    assert status == "steady" and flagged == []


def test_drift_with_too_little_history(monkeypatch):
    days = trading_days(6)
    status, flagged = mh.drift([(d, {"x": 1.0}) for d in days])
    assert status == "too_little_history" and flagged == []


def test_drift_ignores_an_input_the_reference_never_had(monkeypatch):
    monkeypatch.setattr(model_health_config, "recent_sessions", lambda: 2)
    monkeypatch.setattr(model_health_config, "min_reference_sessions", lambda: 3)
    days = trading_days(6)
    inputs = [(d, {"x": 1.0} if i < 4 else {"x": 1.0, "new": 5.0})
              for i, d in enumerate(days)]
    status, flagged = mh.drift(inputs)
    assert status == "steady" and flagged == []


def test_drift_lists_worst_first(monkeypatch):
    import random
    monkeypatch.setattr(model_health_config, "recent_sessions", lambda: 3)
    monkeypatch.setattr(model_health_config, "min_reference_sessions", lambda: 5)
    rng = random.Random(3)
    days = trading_days(25)
    inputs = []
    for i, day in enumerate(days):
        recent = i >= 22
        for _k in range(5):
            inputs.append((day, {"a": rng.gauss(4.0 if recent else 0.0, 1.0),
                                 "b": rng.gauss(1.5 if recent else 0.0, 1.0)}))
    _status, flagged = mh.drift(inputs)
    psis = [f["psi"] for f in flagged]
    assert [f["input"] for f in flagged] == ["a", "b"]
    assert psis == sorted(psis, reverse=True)


# ── signals ─────────────────────────────────────────────────────────────────
def signal(day, kind="0DTE", delta=-0.2, pnl=50.0, strategy="PCS", **kw):
    return {"first_seen_date": day, "scanner_type": kind, "strategy": strategy,
            "entry_short_delta": delta, "entry_max_loss": 400.0, "realized_pnl": pnl,
            "entry_iv_rank": 60.0, "entry_score": 62, "dte_at_entry": 1, **kw}


def test_signals_are_one_model_per_scanner_type(db, monkeypatch):
    days = trading_days(4)
    rows = [signal(d, "0DTE") for d in days] + [signal(d, "SWING", pnl=-10.0)
                                                for d in days]
    monkeypatch.setattr(mh, "_load_signal_rows", lambda db_path=None: rows)
    payload = mh.build(scalp_db=db)
    keys = [m["key"] for m in payload["models"]]
    assert keys == ["scalp_morning", "scalp_intraday", "signals_0DTE", "signals_SWING"]
    zero = model(payload, "signals_0DTE")
    assert zero["rows"] == 3 and zero["days"] == 3         # day one has no usual rate
    assert zero["brier"] == pytest.approx((0.8 - 1.0) ** 2)
    assert zero["note"]


def test_signals_without_a_stated_chance_or_an_r_are_left_out(db, monkeypatch):
    days = trading_days(3)
    rows = [signal(days[0]), signal(days[1]),
            signal(days[2], strategy="IC"),              # no stated chance
            signal(days[2], delta=None),                 # no stated chance
            signal(days[2], pnl=None),                   # no R
            signal(days[2])]
    monkeypatch.setattr(mh, "_load_signal_rows", lambda db_path=None: rows)
    m = model(mh.build(scalp_db=db), "signals_0DTE")
    assert m["rows"] == 2


def test_a_zero_pnl_signal_is_not_a_win(db, monkeypatch):
    days = trading_days(2)
    rows = [signal(days[0]), signal(days[1], pnl=0.0)]
    monkeypatch.setattr(mh, "_load_signal_rows", lambda db_path=None: rows)
    m = model(mh.build(scalp_db=db), "signals_0DTE")
    assert m["brier"] == pytest.approx(0.8 ** 2)


# ── the payload ─────────────────────────────────────────────────────────────
def test_data_date_is_the_latest_scored_day_and_payload_has_no_clock(db):
    days = trading_days(3)
    for day in days:
        put_morning(db, day, "SPY", {"long_with": 0.5}, {"long_with": "win"})
    payload = mh.build(scalp_db=db)
    assert payload["data_date"] == days[-1]
    assert mh.build(scalp_db=db) == payload           # nothing time-dependent
    json.dumps(payload)                                # JSON-able


def test_run_publishes_the_view_and_writes_the_report(db, tmp_path, fake_bus):
    days = trading_days(3)
    for day in days:
        put_morning(db, day, "SPY", {"long_with": 0.5}, {"long_with": "win"})
    out = mh.run(fake_bus, scalp_db=db, out_dir=tmp_path / "reports")
    env = fake_bus.cache_get(mh.CACHE_KEY)
    assert env.payload == out
    report = (tmp_path / "reports" / days[-1] / "report.md").read_text(encoding="utf-8")
    assert "08:36 scalp chance" in report and "Too few days" in report


def test_run_that_fails_publishes_nothing_and_counts_a_degrade(db, tmp_path, fake_bus,
                                                               monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("store unreadable")
    monkeypatch.setattr(mh, "build", boom)
    before = _degrade.counts().get("options.model_health", 0)
    assert mh.run(fake_bus, scalp_db=db, out_dir=tmp_path / "reports") is None
    assert fake_bus.cache_get(mh.CACHE_KEY) is None
    assert _degrade.counts().get("options.model_health", 0) == before + 1


# ── wiring ──────────────────────────────────────────────────────────────────
def test_nightly_runs_only_when_enabled(monkeypatch, fake_bus):
    calls = []
    monkeypatch.setattr(mh, "run", lambda bus: calls.append(bus) or {"models": []})
    monkeypatch.setattr(model_health_config, "enabled", lambda: False)
    assert mh.nightly(fake_bus) is None and calls == []
    monkeypatch.setattr(model_health_config, "enabled", lambda: True)
    mh.nightly(fake_bus)
    assert calls == [fake_bus]


def test_the_recompute_command_is_registered_on_the_slow_lane(monkeypatch, fake_bus):
    from services.options_svc import handlers
    assert "model_health_rebuild" in handlers._COMMANDS
    assert "model_health_rebuild" in handlers.SLOW_LANE
    seen = []
    monkeypatch.setattr(mh, "run", lambda bus: seen.append(bus))
    handlers._COMMANDS["model_health_rebuild"](fake_bus, None)
    assert seen == [fake_bus]


def test_the_nightly_step_runs_after_the_intraday_study_rebuild():
    import inspect
    from services.options_svc import scheduler
    src = inspect.getsource(scheduler)
    rebuild = src.index("scalp_intraday_study.rebuild")
    step = src.index("model_health.nightly")
    assert rebuild < step


# ── the range forecast ──────────────────────────────────────────────────────
def _range_entry(symbol="SPY", horizon=30, days=50, lo=-0.02, hi=-0.01):
    diff = {"mean": (lo + hi) / 2, "lo": lo, "hi": hi, "days": days}
    return {"symbol": symbol, "horizon": horizon, "vol_index": "$VIX", "days": days,
            "rows": days * 20, "loss": 0.10, "loss_yardstick": 0.11, "loss_implied": 0.12,
            "diff_yardstick": diff, "diff_implied": dict(diff, lo=-0.01, hi=0.01),
            "coverage": 0.79, "last_day": "2026-11-13",
            "inputs": [(d, {"rv_30": 1.0}) for d in trading_days(3)]}


def test_payload_carries_the_range_verdicts(db, monkeypatch):
    from services.options_svc import range_forecast
    monkeypatch.setattr(range_forecast, "evaluate",
                        lambda db_path=None: [_range_entry(), _range_entry(horizon=0, days=10)])
    payload = mh.build(scalp_db=db)
    first, short = payload["range"]
    assert first["verdict_yardstick"] == "beats" and first["verdict_implied"] == "matches"
    assert short["verdict_yardstick"] == "too_few_days"
    assert "inputs" not in first and "last_day" not in first
    assert first["drift_status"] == "too_little_history"
    assert payload["data_date"] == "2026-11-13"
    json.dumps(payload)


def test_a_failing_range_forecast_leaves_the_rest_published(db, monkeypatch):
    from services.options_svc import range_forecast

    def boom(db_path=None):
        raise RuntimeError("bars unreadable")
    monkeypatch.setattr(range_forecast, "evaluate", boom)
    before = _degrade.counts().get("options.range_forecast", 0)
    payload = mh.build(scalp_db=db)
    assert payload["range"] is None and len(payload["models"]) == 2
    assert _degrade.counts().get("options.range_forecast", 0) == before + 1


def test_range_switched_off(db, monkeypatch):
    monkeypatch.setattr(model_health_config, "range_enabled", lambda: False)
    assert mh.build(scalp_db=db)["range"] is None


# ── the exit chance ─────────────────────────────────────────────────────────
def _exit_entry(kind="SWING", days=50, lo=-0.02, hi=-0.01, last="2026-11-14"):
    diff = {"mean": (lo + hi) / 2, "lo": lo, "hi": hi, "days": days}
    return {"type": kind, "days": days, "rows": days * 30, "trades": days * 4,
            "brier": 0.20, "brier_distance": 0.22, "diff": diff,
            "reliability": [[0.0, 0.1, None, None, 0]] * 10, "last_day": last,
            "inputs": [(d, {"cushion": 1.0}) for d in trading_days(3)]}


def test_build_carries_exit(db, monkeypatch):
    from services.options_svc import exit_chance
    monkeypatch.setattr(exit_chance, "evaluate", lambda db_path=None: [
        _exit_entry(), _exit_entry("0DTE", days=4, lo=-0.1, hi=0.1)])
    payload = mh.build(scalp_db=db)
    swing, zero = payload["exit"]
    assert swing["verdict"] == "beats" and zero["verdict"] == "too_few_days"
    assert swing["min_days"] == model_health_config.exit_min_days()
    assert "inputs" not in swing and "last_day" not in swing
    assert swing["drift_status"] == "too_little_history"
    assert payload["data_date"] == "2026-11-14"
    json.dumps(payload)


def test_exit_off_is_none(db, monkeypatch):
    monkeypatch.setattr(model_health_config, "exit_enabled", lambda: False)
    assert mh.build(scalp_db=db)["exit"] is None


def test_exit_failure_degrades(db, monkeypatch):
    from services.options_svc import exit_chance

    def boom(db_path=None):
        raise RuntimeError("signals unreadable")
    monkeypatch.setattr(exit_chance, "evaluate", boom)
    before = _degrade.counts().get("options.exit_chance", 0)
    payload = mh.build(scalp_db=db)
    assert payload["exit"] is None and len(payload["models"]) == 2
    assert _degrade.counts().get("options.exit_chance", 0) == before + 1


def test_report_has_exit_section(db, monkeypatch):
    from services.options_svc import exit_chance
    monkeypatch.setattr(exit_chance, "evaluate",
                        lambda db_path=None: [_exit_entry(days=12, lo=-0.02, hi=0.16)])
    report = mh.report_markdown(mh.build(scalp_db=db))
    assert "## Exit chance (shadow)" in report
    assert "| SWING | Too few days (12 of 40)" in report
    assert "rules running at the time" in report
