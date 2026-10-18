"""Grading the Opening Scalp's published calls (scalp_grade) and the scorecard.

No network and no live store. A call is PUBLISHED the way production does it
(``scalp_call.run`` on a fake bus, a hand-built 0DTE chain), which freezes it in
``calls``; the day's 5-minute path is then seeded by hand, so each trade's result
is known before the grade runs: an ``up`` path jumps +1% in the first bar (a long
call and a put credit spread win, a long put and a call credit spread lose), a
``flat`` path never moves (every role times out).

What these pin, each from a controller ruling:

* only a PUBLISHED role is graded, from the FROZEN entry (``calls``), never from
  the view or a recomputation; every role is graded, the highlighted one flagged;
* the chain result prices from the saved slice (the short leg of a spread) and is
  ``None`` without one; a path with a bar missing is not graded yet;
* the calibration (``atm_iv`` over the morning's volatility reading) lives in a
  table of its own, which a study rebuild leaves alone;
* the scorecard counts highlighted days for the edge, avoided roles for the
  caution, and every role for the table.
"""
import datetime as dt
import json
import sqlite3

import pytest

from services import _degrade
from services.options_svc import scalp_call, scalp_grade, scalp_score, scalp_sim, scalp_store, scalp_study
from services.options_svc.scalp_sim import ROLES
from services.options_svc.tests.test_scalp_call import (
    DAY, EVEN, NOW, PRICES, FakeClient, entry_spot, make_chain, seed_morning, seed_study, ts)
from shared import scalp_config
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.options import ScalpView

ISO = DAY.isoformat()


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def bus():
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture(autouse=True)
def _fresh_degrades():
    _degrade.reset()
    yield
    _degrade.reset()


def seed_path(db, day=DAY, kind="up", symbols=tuple(PRICES), skip=None):
    """The twelve 5-minute bars after the 08:35 entry, and $SPX's regular session
    to 14:55 (so the day is not a short one). ``skip`` drops one bar index."""
    rows = []
    for sym in symbols:
        c = PRICES[sym] * 1.0015            # the 08:30 bar's close = the entry spot
        for i in range(12):
            if skip == i:
                continue
            if kind == "up" and i == 0:
                bar = (c, c * 1.01, c, c * 1.01)
            elif kind == "up":
                bar = (c * 1.01,) * 4
            else:
                bar = (c, c, c, c)
            rows.append((sym, 5, ts(day, 8, 35) + 300 * i, *bar, 1000.0))
    t, end = ts(day, 9, 35), ts(day, 14, 55)
    while t <= end:
        rows.append(("$SPX", 5, t, 5000.0, 5000.0, 5000.0, 5000.0, 0.0))
        t += 300
    scalp_store.upsert_bars(rows, db_path=db)


def publish(db, bus, client=None, **study):
    """Seed the morning and the study, publish the 08:36 call (freezing it)."""
    seed_morning(db)
    for sym in PRICES:
        seed_study(db, sym, study.get(sym, EVEN), edges=study.get(sym + ":edges", ()))
    scalp_call.run(bus, now=NOW, db_path=db, client=client or FakeClient())


def grades(db, where="1=1"):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM grades WHERE {where} ORDER BY day, symbol, role")]
    finally:
        conn.close()


def by_role(rows, symbol):
    return {r["role"]: r for r in rows if r["symbol"] == symbol}


# SPY's up-morning study that gives long_with a measured edge ABOVE its base rate
# and long_against one BELOW it (so one is highlighted and one avoided).
HIGHLIGHT = {"SPY": {"long_with": (24, 30), "long_against": (3, 30),
                     "credit_with": (15, 30), "credit_against": (15, 30)}}


def publish_with_calls(db, bus):
    seed_morning(db)
    for sym in PRICES:
        seed_study(db, sym, EVEN)
    seed_study(db, "SPY", HIGHLIGHT["SPY"], edges=("long_with", "long_against"))
    seed_study(db, "SPY", {"long_with": (6, 30), "long_against": (27, 30),
                           "credit_with": (15, 30), "credit_against": (15, 30)},
               bucket="low", start=200)
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient())
    spy = scalp_store.calls_on(ISO, db_path=db)["SPY"]
    assert spy["highlight"] == "long_with" and spy["avoid"] == ["long_against"]


# ── what is graded ───────────────────────────────────────────────────────────

def test_grades_every_role_not_only_highlighted(db, bus):
    publish_with_calls(db, bus)
    seed_path(db)
    written = scalp_grade.grade_day(ISO, db_path=db)
    assert written == 3 * len(ROLES)
    spy = by_role(grades(db), "SPY")
    assert set(spy) == set(ROLES)
    assert [spy[r]["highlighted"] for r in ROLES] == [1, 0, 0, 0]
    assert [spy[r]["avoided"] for r in ROLES] == [0, 1, 0, 0]
    # The path jumps +1% at once: the with-the-open long call and put spread win.
    assert spy["long_with"]["model_result"] == "win"
    assert spy["credit_with"]["model_result"] == "win"
    assert spy["long_against"]["model_result"] == "loss"
    assert spy["credit_against"]["model_result"] == "loss"
    assert spy["long_with"]["structure"] == "long_call"
    # The frozen base rate is kept for the scorecard to average.
    assert spy["long_with"]["p_base"] == pytest.approx(0.5)


def test_chain_result_none_without_slice(db, bus):
    chains = {s: make_chain(s) for s in PRICES}
    chains["SPY"] = RuntimeError("proxy down")
    publish(db, bus, client=FakeClient(chains))
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    rows = grades(db)
    spy, qqq = by_role(rows, "SPY"), by_role(rows, "QQQ")
    for role in ROLES:
        assert spy[role]["chain_result"] is None
        assert spy[role]["model_result"] in ("win", "loss", "timeout")   # the model still grades
        assert qqq[role]["chain_result"] in ("win", "loss", "timeout")
    assert qqq["long_with"]["chain_result"] == "win"


def test_chain_result_for_a_spread_uses_the_short_leg(db, bus, monkeypatch):
    publish(db, bus)
    seed_path(db)
    oc = scalp_sim._calc()
    seen = []
    real = oc.implied_vol

    def spy(price, S, K, T, r, option_type, *a, **k):
        seen.append((price, S, K, option_type))
        return real(price, S, K, T, r, option_type, *a, **k)

    monkeypatch.setattr(oc, "implied_vol", spy)
    scalp_grade.grade_day(ISO, db_path=db)
    s = entry_spot("SPY")
    # SPY: long call 501; put credit spread short 500 / long 499; call credit
    # spread short 501 / long 502; long put 500. The vol is read at ONE strike each.
    seen_spy = [(round(K, 6), t) for (_p, S, K, t) in seen if abs(S - s) < 1e-9]
    assert (500.0, "put") in seen_spy          # the put credit spread: its SHORT put
    assert (499.0, "put") not in seen_spy      # never the long leg
    assert (501.0, "call") in seen_spy         # the long call; the call spread's short
    assert (502.0, "call") not in seen_spy


def test_grading_never_reads_a_role_that_was_not_in_calls(db, bus):
    # The study holds a full set of outcomes and mornings for the day, but only
    # $SPX and QQQ were ever PUBLISHED: SPY has no grade.
    seed_morning(db, symbols=("$SPX", "QQQ"))
    for sym in PRICES:
        seed_study(db, sym, EVEN)
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient())
    assert set(scalp_store.calls_on(ISO, db_path=db)) == {"$SPX", "QQQ"}
    seed_morning(db, symbols=("SPY",))          # SPY's bar arrives after the window
    seed_path(db)
    scalp_study.rebuild(db_path=db)             # the study now holds SPY's outcome too
    written = scalp_grade.grade_day(ISO, db_path=db)
    assert written == 2 * len(ROLES)
    assert {r["symbol"] for r in grades(db)} == {"$SPX", "QQQ"}
    # A day with no call at all has nothing to grade.
    assert scalp_grade.grade_day("2026-10-20", db_path=db) == 0


def test_a_symbol_with_no_roles_that_day_has_no_grades(db, bus):
    seed_morning(db, flat=("QQQ",))
    for sym in PRICES:
        seed_study(db, sym, EVEN)
    scalp_call.run(bus, now=NOW, db_path=db, client=FakeClient())
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    assert {r["symbol"] for r in grades(db)} == {"$SPX", "SPY"}


def test_a_refresh_or_a_changed_view_cannot_change_a_grade(db, bus):
    publish(db, bus)
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    first = grades(db)
    # After the close the study learns the day, the chains move, the view is
    # republished on a refresh and even overwritten: the frozen call is the source.
    seed_study(db, "SPY", {r: (30, 30) for r in ROLES}, edges=ROLES, start=100)
    later = {s: make_chain(s, iv=35.0, spot=entry_spot(s) * 1.02) for s in PRICES}
    scalp_call.run(bus, now=NOW.replace(hour=17), db_path=db, client=FakeClient(later))
    bus.cache_set(scalp_call.VIEW, {"day": ISO, "state": "call", "symbols": {
        "SPY": {"direction": -1, "roles": {}}}}, event=scalp_call.EVENT)
    assert scalp_grade.grade_day(ISO, db_path=db) == len(first)
    assert grades(db) == first


def test_grading_is_idempotent(db, bus):
    publish(db, bus)
    seed_path(db)
    a = scalp_grade.grade_day(ISO, db_path=db)
    rows = grades(db)
    assert scalp_grade.grade_day(ISO, db_path=db) == a
    assert grades(db) == rows and len(rows) == a


def test_an_incomplete_path_is_not_graded_yet(db, bus):
    publish(db, bus)
    seed_path(db, skip=5)                      # a hole: 11 of the 12 bars
    assert scalp_grade.grade_day(ISO, db_path=db) == 0
    assert grades(db) == []
    assert scalp_grade.pending_days(db_path=db) == [ISO]
    seed_path(db)                              # the bar arrives
    assert scalp_grade.grade_day(ISO, db_path=db) == 3 * len(ROLES)
    assert scalp_grade.pending_days(db_path=db) == []


def test_a_short_session_is_not_graded(db, bus):
    publish(db, bus)
    seed_path(db)
    conn = scalp_store.connect(db)
    try:
        with conn:      # $SPX stops at 11:55: the model's settlement would be wrong
            conn.execute("DELETE FROM bars WHERE symbol = '$SPX' AND interval = 5 "
                         "AND ts > ?", (ts(DAY, 11, 55),))
    finally:
        conn.close()
    assert scalp_grade.grade_day(ISO, db_path=db) == 0


def test_pending_days_and_grade_pending(db, bus):
    publish(db, bus)
    assert scalp_grade.pending_days(db_path=db) == [ISO]
    seed_path(db)
    out = scalp_grade.grade_pending(db_path=db)
    assert out == {"days": 1, "rows": 3 * len(ROLES)}
    assert scalp_grade.pending_days(db_path=db) == []
    assert scalp_grade.grade_pending(db_path=db) == {"days": 0, "rows": 0}


def test_a_day_that_raises_does_not_stop_the_others(db, bus, monkeypatch):
    publish(db, bus)
    seed_path(db)
    monkeypatch.setattr(scalp_grade, "grade_day",
                        lambda day, db_path=None: (_ for _ in ()).throw(RuntimeError("x")))
    assert scalp_grade.grade_pending(db_path=db) == {"days": 1, "rows": 0}
    assert _degrade.total() >= 1


# ── the volatility calibration ───────────────────────────────────────────────

def test_calibration_is_stored_per_day_and_symbol_and_survives_a_rebuild(db, bus):
    publish(db, bus)
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)

    def rows():
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT day, symbol, atm_iv, vol_reading, ratio FROM "
                                "iv_calibration ORDER BY symbol").fetchall()
        finally:
            conn.close()

    got = rows()
    assert [r[1] for r in got] == sorted(PRICES)
    for day, sym, atm, vol, ratio in got:
        assert day == ISO and atm == pytest.approx(0.18) and vol == pytest.approx(0.20)
        assert ratio == pytest.approx(0.18 / 0.20)
    scalp_study.rebuild(db_path=db)
    assert rows() == got                        # rebuild deletes mornings, not this
    scalp_grade.grade_day(ISO, db_path=db)
    assert rows() == got


def test_no_chain_no_calibration_row(db, bus):
    chains = {s: RuntimeError("down") for s in PRICES}
    publish(db, bus, client=FakeClient(chains))
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM iv_calibration").fetchone()[0] == 0
    finally:
        conn.close()


# ── the scorecard ────────────────────────────────────────────────────────────

def put_grade(db, day, symbol, role, model, chain=None, highlighted=0, avoided=0,
              p_base=0.5, structure="long_call"):
    conn = scalp_store.connect(db)
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO grades (day, symbol, role, structure, "
                         "model_result, chain_result, highlighted, avoided, p_base) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (day, symbol, role, structure, model, chain, highlighted,
                          avoided, p_base))
    finally:
        conn.close()


def put_day(db, day, symbol="SPY", highlight=None, avoid=(), results=None):
    """One graded day: all four roles, ``results`` role -> (model, chain)."""
    results = results or {}
    for role in ROLES:
        model, chain = results.get(role, ("loss", None))
        put_grade(db, day, symbol, role, model, chain,
                  highlighted=int(role == highlight), avoided=int(role in avoid))


def test_scorecard_headline_counts_highlighted_days_only(db):
    # Five graded days; only two had a highlight: one won, one lost.
    put_day(db, "2026-10-12", highlight="long_with",
            results={"long_with": ("win", None)})
    put_day(db, "2026-10-13", highlight="credit_with",
            results={"credit_with": ("loss", None), "long_with": ("win", None)})
    for d in ("2026-10-14", "2026-10-15", "2026-10-16"):
        put_day(db, d, results={"long_with": ("win", None)})
    spy = scalp_score.scorecard(db_path=db)["symbols"]["SPY"]
    assert spy["days"] == 5
    assert spy["edge"]["days"] == 2 and spy["edge"]["wins"] == 1
    assert spy["edge"]["rate"] == pytest.approx(0.5)
    assert spy["edge"]["p_base"] == pytest.approx(0.5)


def test_scorecard_has_no_edge_line_without_highlighted_days(db):
    put_day(db, "2026-10-12")
    spy = scalp_score.scorecard(db_path=db)["symbols"]["SPY"]
    assert spy["edge"] == {"days": 0, "wins": 0, "rate": None, "p_base": None}


def test_an_avoided_role_gets_its_caution_line(db):
    # The role was cautioned on three days and won once; its frozen base rate was 0.5.
    put_day(db, "2026-10-12", avoid=("long_against",),
            results={"long_against": ("loss", None)})
    put_day(db, "2026-10-13", avoid=("long_against",),
            results={"long_against": ("win", None)})
    put_day(db, "2026-10-14", avoid=("long_against",),
            results={"long_against": ("timeout", None)})
    put_day(db, "2026-10-15", results={"long_against": ("win", None)})   # not avoided
    spy = scalp_score.scorecard(db_path=db)["symbols"]["SPY"]
    assert spy["caution"]["days"] == 3 and spy["caution"]["wins"] == 1
    assert spy["caution"]["rate"] == pytest.approx(1 / 3)
    assert spy["caution"]["p_base"] == pytest.approx(0.5)


def test_roles_table_counts_every_role_and_both_grades(db):
    put_day(db, "2026-10-12", results={"long_with": ("win", "win"),
                                       "credit_with": ("timeout", "loss")})
    put_day(db, "2026-10-13", results={"long_with": ("loss", "win"),
                                       "credit_with": ("win", None)})
    roles = scalp_score.scorecard(db_path=db)["symbols"]["SPY"]["roles"]
    assert set(roles) == set(ROLES)              # nothing missing in place of a role
    lw = roles["long_with"]
    assert (lw["n"], lw["win"], lw["loss"], lw["timeout"]) == (2, 1, 1, 0)
    assert lw["win_rate"] == pytest.approx(0.5)
    assert lw["chain_n"] == 2 and lw["chain_win_rate"] == pytest.approx(1.0)
    cw = roles["credit_with"]
    assert (cw["n"], cw["win"], cw["loss"], cw["timeout"]) == (2, 1, 0, 1)
    assert cw["chain_n"] == 1 and cw["chain_win_rate"] == 0.0
    assert roles["long_against"]["chain_win_rate"] is None     # no chain grades


def test_the_scorecard_looks_back_scorecard_days_graded_days_per_symbol(db, monkeypatch):
    monkeypatch.setattr(scalp_config, "scorecard_days", lambda: 3)
    days = [f"2026-10-{d:02d}" for d in (5, 6, 7, 8, 9)]
    for d in days:
        put_day(db, d, results={"long_with": ("win", None)})
    put_day(db, "2026-10-12", symbol="QQQ")
    card = scalp_score.scorecard(db_path=db)
    assert card["symbols"]["SPY"]["days"] == 3
    assert card["symbols"]["SPY"]["roles"]["long_with"]["n"] == 3
    assert card["symbols"]["QQQ"]["days"] == 1
    assert "$SPX" not in card["symbols"]


def test_a_day_with_no_modelled_result_is_not_a_graded_day(db):
    put_day(db, "2026-10-12", results={r: (None, None) for r in ROLES})
    assert scalp_score.scorecard(db_path=db) == {}


def test_empty_store_is_an_empty_scorecard(db):
    assert scalp_score.scorecard(db_path=db) == {}


# ── the scorecard reaches every published payload ───────────────────────────

def test_every_published_state_carries_the_scorecard(db, bus):
    publish(db, bus)
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    for when in (NOW, NOW.replace(hour=7),                       # call, waiting
                 dt.datetime(2026, 10, 18, 12, 0, tzinfo=NOW.tzinfo)):   # closed (Sunday)
        scalp_call.run(bus, now=when, db_path=db, client=FakeClient())
        view = bus.cache_get(scalp_call.VIEW).payload
        assert view["scorecard"]["symbols"]["SPY"]["days"] == 1
        ScalpView(**view)
        json.dumps(view, allow_nan=False)


def test_a_scorecard_failure_publishes_an_empty_one_and_counts_a_degrade(db, bus, monkeypatch):
    publish(db, bus)

    def boom(db_path=None):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(scalp_score, "scorecard", boom)
    scalp_call.run(bus, now=NOW.replace(hour=7), db_path=db, client=FakeClient())
    assert bus.cache_get(scalp_call.VIEW).payload["scorecard"] == {}
    assert _degrade.total() >= 1


def test_the_real_graded_card_is_plain_numbers(db, bus):
    publish_with_calls(db, bus)
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    card = scalp_score.scorecard(db_path=db)
    assert card["days"] == 1
    spy = card["symbols"]["SPY"]
    assert spy["edge"]["days"] == 1 and spy["edge"]["wins"] == 1
    assert spy["edge"]["p_base"] == pytest.approx(0.5)
    assert spy["caution"]["days"] == 1 and spy["caution"]["wins"] == 0

    def plain(node):
        if isinstance(node, dict):
            return all(plain(v) for v in node.values())
        return not isinstance(node, float) or type(node) is float
    assert plain(card)
    json.dumps(card, allow_nan=False)


# ── the study report's calibration section ──────────────────────────────────

def _report():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "scalp_study_report", root / "tools" / "scalp_study_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _calibration_text(db):
    section = _report().render(db).split("## Volatility calibration", 1)
    assert len(section) == 2
    return section[1]


def test_report_calibration_says_so_when_there_are_no_chain_captures(db, bus):
    publish(db, bus, client=FakeClient({s: RuntimeError("down") for s in PRICES}))
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    scalp_study.rebuild(db_path=db)
    assert "No chain captures yet." in _calibration_text(db)


def test_report_calibration_gives_n_days_and_the_median_ratio_per_symbol(db, bus):
    publish(db, bus)
    seed_path(db)
    scalp_grade.grade_day(ISO, db_path=db)
    conn = scalp_store.connect(db)
    try:
        with conn:      # two more days for SPY, so the median is not the only value
            conn.executemany("INSERT INTO iv_calibration VALUES (?, 'SPY', ?, 0.2, ?)",
                             [("2026-10-14", 0.30, 1.5), ("2026-10-15", 0.28, 1.4)])
    finally:
        conn.close()
    scalp_study.rebuild(db_path=db)
    text = _calibration_text(db)
    assert "| SPY | 3 | 1.400 |" in text           # median of 0.9, 1.4, 1.5
    assert "| QQQ | 1 | 0.900 |" in text
    assert "| $SPX | 1 | 0.900 |" in text


def test_report_calibration_tolerates_a_store_older_than_the_grading(tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript("CREATE TABLE outcomes (day, symbol, iv_scale, role, structure, "
                       "bucket, result, minutes, entry_value, end_pnl_pct, both_in_bar, "
                       "interval);"
                       "CREATE TABLE mornings (day, symbol, json);"
                       "CREATE TABLE verdicts (symbol, role, bucket, json);")
    conn.execute("INSERT INTO mornings VALUES ('2026-10-12', 'SPY', "
                 "'{\"day\": \"2026-10-12\", \"symbol\": \"SPY\", \"skip\": \"no_vol\"}')")
    conn.commit()
    conn.close()
    assert "No chain captures yet." in _calibration_text(old)
