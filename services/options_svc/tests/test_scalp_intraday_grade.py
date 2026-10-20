"""Grading the intraday scalp calls (scalp_intraday_grade) and the scorecard's
``intraday`` section.

No network and no live store. A slot is PUBLISHED the way production does it
(``scalp_intraday_call.run_slot`` on a fake bus), which freezes the call and its
own chain slice in ``intraday_calls`` / ``intraday_chain_slices``; the day's
5-minute path is then seeded by hand, so each trade's result is known before the
grade runs: the ``up`` path jumps +1% in its first bar (a long call and a put
credit spread win, a long put and a call credit spread lose).

What these pin, each from a controller ruling:

* every role of every PUBLISHED slot call is graded from the FROZEN entry (never a
  recomputation), flagged ``highlighted`` / ``avoided``, with the call's
  confirmation word, the role's frozen ``p_base`` and the slot's block beside it;
* grades are write-once per (day, slot, symbol, role);
* the chain result is solved from THAT slot's own saved slice at its own
  ``underlyingPrice``, and is ``None`` without one;
* a path short of bars is not graded yet; a short session is never graded and so
  never counted;
* the scorecard's ``intraday`` section is per symbol per block, split by the
  confirmation word, with ``None`` (never 0) for a rate with nothing under it.
"""
import ast
import sqlite3

import pytest

from services import _degrade
from services.options_svc import (
    scalp_grade, scalp_intraday_call as sic, scalp_intraday_grade as sig, scalp_model,
    scalp_score, scalp_store)
from services.options_svc.scalp_sim import ROLES
from services.options_svc.tests.test_scalp_intraday_call import (
    AT, EVEN, ISO, PRICES, SVC, FakeClient, _seeded, bucket_of, seed_bars, seed_study,
    spot_at, ts)
from shared import scalp_config
from shared.bus import Bus
from shared.bus.client import reset_fake_bus

SLOTS = ("09:06", "09:21")
AT2 = AT.replace(minute=21)
WORDS = ("Confirms", "Mixed", "Contradicts")


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


def seed_path(db, kind="up", skip=None, spx_to=(14, 55)):
    """5-minute bars from the 09:05 boundary to 10:15 for every symbol (the first
    bar jumps +1% for ``up``, nothing moves for ``flat``), and $SPX's regular
    session on to ``spx_to`` so the day is not a short one. ``skip`` drops one bar
    index of the path after 09:05."""
    rows = []
    for sym in PRICES:
        c = spot_at(sym)
        for i in range(15):
            if skip == i:
                continue
            if kind == "up" and i == 0:
                bar = (c, c * 1.01, c, c * 1.01)
            elif kind == "up":
                bar = (c * 1.01,) * 4
            else:
                bar = (c, c, c, c)
            rows.append((sym, 5, ts(9, 5) + 300 * i, *bar, 1000.0))
    t, end = ts(10, 20), ts(*spx_to)
    while t <= end:
        rows.append(("$SPX", 5, t, 5000.0, 5000.0, 5000.0, 5000.0, 0.0))
        t += 300
    scalp_store.upsert_bars(rows, db_path=db)


def publish(db, bus, slots=SLOTS, study=None, edges=None):
    """Seed the study and the opening bars, publish ``slots`` (freezing them)."""
    _seeded(db, study=study, edges=edges)
    client = FakeClient()
    for slot, at in zip(SLOTS, (AT, AT2)):
        if slot in slots:
            assert sic.run_slot(bus, slot, now=at, db_path=db, client=client) is True
    return client


def grades(db, where="1=1"):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM intraday_grades WHERE {where} ORDER BY day, slot, symbol, role")]
    finally:
        conn.close()


def by_role(rows, slot, symbol):
    return {r["role"]: r for r in rows if r["slot"] == slot and r["symbol"] == symbol}


# ── what is graded ───────────────────────────────────────────────────────────

def test_grades_every_role_of_every_slot(db, bus):
    publish(db, bus)
    seed_path(db)
    written = sig.grade_day(ISO, db_path=db)
    assert written == len(SLOTS) * len(PRICES) * len(ROLES)
    rows = grades(db)
    assert len(rows) == written
    saved = scalp_store.intraday_calls_on(ISO, db_path=db)
    for slot in SLOTS:
        for sym in PRICES:
            got = by_role(rows, slot, sym)
            assert set(got) == set(ROLES)
            entry = saved[(slot, sym)]
            for role in ROLES:
                row = got[role]
                assert row["structure"] == entry["roles"][role]["structure"]
                assert row["p_base"] == pytest.approx(entry["roles"][role]["p_base"])
                assert row["confirm_word"] == entry["confirmation"]["word"]
                assert row["block"] == "morning"
                assert row["highlighted"] == int(entry["highlight"] == role)
                assert row["avoided"] == int(role in entry["avoid"])
    # The 09:06 slot's path jumps +1% at its entry: the with-the-move long call
    # and put credit spread win, the against ones lose.
    spy = by_role(rows, "09:06", "SPY")
    assert spy["long_with"]["model_result"] == "win"
    assert spy["credit_with"]["model_result"] == "win"
    assert spy["long_against"]["model_result"] == "loss"
    assert spy["credit_against"]["model_result"] == "loss"
    assert spy["long_with"]["chain_result"] in ("win", "loss", "timeout")


def test_a_highlight_and_an_avoid_are_stored_with_the_grade(db, bus):
    # SPY: long_with has a measured edge above its base, long_against one below.
    seed_bars(db)
    bucket = bucket_of(db, "SPY")
    other = next(b for b in scalp_model.BUCKETS if b != bucket)
    for sym in PRICES:
        seed_study(db, sym, EVEN, bucket=bucket)
    seed_study(db, "SPY", {"long_with": (24, 30), "long_against": (3, 30)},
               edges=("long_with", "long_against"), bucket=bucket, start=100)
    seed_study(db, "SPY", {"long_with": (6, 30), "long_against": (27, 30)},
               bucket=other, start=200)
    assert sic.run_slot(bus, "09:06", now=AT, db_path=db, client=FakeClient()) is True
    spy_entry = scalp_store.intraday_calls_on(ISO, db_path=db)[("09:06", "SPY")]
    assert spy_entry["highlight"] == "long_with" and spy_entry["avoid"] == ["long_against"]
    seed_path(db)
    sig.grade_day(ISO, db_path=db)
    spy = by_role(grades(db), "09:06", "SPY")
    assert [spy[r]["highlighted"] for r in ROLES] == [1, 0, 0, 0]
    assert [spy[r]["avoided"] for r in ROLES] == [0, 1, 0, 0]


def test_write_once(db, bus):
    publish(db, bus)
    seed_path(db)
    first = sig.grade_day(ISO, db_path=db)
    before = grades(db)
    assert first == len(before)
    # A later night with the opposite path and another volatility scale rewrites
    # nothing: a finished grade is never recomputed.
    seed_path(db, kind="flat")
    assert sig.grade_day(ISO, db_path=db) == 0
    assert grades(db) == before
    # Only a role that is missing is graded, and the rest are left as they were.
    conn = scalp_store.connect(db)
    try:
        with conn:
            conn.execute("DELETE FROM intraday_grades WHERE slot = '09:06' AND "
                         "symbol = 'SPY' AND role = 'long_with'")
    finally:
        conn.close()
    assert sig.pending_days(db_path=db) == [ISO]
    assert sig.grade_day(ISO, db_path=db) == 1
    after = grades(db)
    assert [r for r in after if (r["slot"], r["symbol"], r["role"])
            != ("09:06", "SPY", "long_with")] == [
        r for r in before if (r["slot"], r["symbol"], r["role"])
        != ("09:06", "SPY", "long_with")]
    assert sig.pending_days(db_path=db) == []


def test_the_grade_uses_the_rules_frozen_with_the_call(db, bus, monkeypatch):
    publish(db, bus, slots=("09:06",))
    seed_path(db)
    # A call frozen with a 30-minute time stop needs six bars, though the config
    # (and the 60-minute default) would need twelve: only SIX bars are in.
    key = ("09:06", "SPY")
    entry = scalp_store.intraday_calls_on(ISO, db_path=db)[key]
    entry["rules"] = dict(entry["rules"], time_stop_min=30)
    scalp_store.save_intraday_call(ISO, "09:06", "SPY", entry, db_path=db)
    conn = scalp_store.connect(db)
    try:
        with conn:
            conn.execute("DELETE FROM bars WHERE symbol = 'SPY' AND interval = 5 "
                         "AND ts >= ?", (ts(9, 5) + 6 * 300,))
    finally:
        conn.close()
    monkeypatch.setattr(scalp_config, "time_stop_min", lambda: 120)
    monkeypatch.setattr(scalp_config, "target_pct", lambda: 1.0)
    sig.grade_day(ISO, db_path=db)
    spy = by_role(grades(db), "09:06", "SPY")
    assert set(spy) == set(ROLES)
    assert spy["long_with"]["model_result"] == "win"
    # The other two symbols kept their frozen 60 minutes and 12 bars: graded too.
    assert len(grades(db, "symbol = 'QQQ'")) == len(ROLES)


def test_chain_result_uses_the_slots_own_slice(db, bus, monkeypatch):
    publish(db, bus)
    seed_path(db)
    # Move the 09:21 slot's slice to another spot: each slot must be solved at ITS
    # slice, never at the other's (or the morning's).
    moved = scalp_store.intraday_slice(ISO, "09:21", "SPY", db_path=db)
    base = moved["underlyingPrice"]
    moved["underlyingPrice"] = base * 1.004
    scalp_store.save_intraday_slice(ISO, "09:21", "SPY", ts(9, 21), moved, db_path=db)
    seen = []
    real = scalp_grade._chain_sigma

    def spy_on(slice_, day, structure, strikes, when):
        seen.append((slice_.get("underlyingPrice") if isinstance(slice_, dict) else None,
                     when))
        return real(slice_, day, structure, strikes, when)

    monkeypatch.setattr(scalp_grade, "_chain_sigma", spy_on)
    sig.grade_day(ISO, db_path=db)
    by_when = {}
    for under, when in seen:
        by_when.setdefault(when, set()).add(under)
    assert base * 1.004 in by_when[scalp_grade.scalp_fetch.ct(ts(9, 20))]
    assert base * 1.004 not in by_when[scalp_grade.scalp_fetch.ct(ts(9, 5))]
    # And it is the saved slice that is read, so no slice means no chain result.
    conn = scalp_store.connect(db)
    try:
        with conn:
            conn.execute("DELETE FROM intraday_grades")
            conn.execute("DELETE FROM intraday_chain_slices WHERE slot = '09:06' "
                         "AND symbol = 'SPY'")
    finally:
        conn.close()
    monkeypatch.setattr(scalp_grade, "_chain_sigma", real)
    sig.grade_day(ISO, db_path=db)
    rows = grades(db)
    for role in ROLES:
        assert by_role(rows, "09:06", "SPY")[role]["chain_result"] is None
        assert by_role(rows, "09:06", "SPY")[role]["model_result"] is not None
        assert by_role(rows, "09:21", "SPY")[role]["chain_result"] in (
            "win", "loss", "timeout", "skip")


def test_the_slot_slice_not_the_morning_one_is_read(db, bus):
    publish(db, bus, slots=("09:06",))
    seed_path(db)
    assert scalp_store.chain_slice(ISO, "SPY", db_path=db) is None
    sig.grade_day(ISO, db_path=db)
    assert by_role(grades(db), "09:06", "SPY")["long_with"]["chain_result"] in (
        "win", "loss", "timeout", "skip")


def test_short_session_calls_not_graded_or_counted(db, bus):
    publish(db, bus)
    seed_path(db, spx_to=(11, 55))          # $SPX stops at 11:55: a short session
    assert sig.grade_day(ISO, db_path=db) == 0
    assert grades(db) == []
    assert sig.grade_pending(db_path=db) == {"days": 1, "rows": 0}
    assert "intraday" not in scalp_score.scorecard(db_path=db)
    # The same bars on a full day are graded and counted.
    seed_path(db)
    assert sig.grade_pending(db_path=db) == {"days": 1,
                                            "rows": len(SLOTS) * len(PRICES) * len(ROLES)}
    card = scalp_score.scorecard(db_path=db)["intraday"]
    assert card["days"] == 1


def test_an_incomplete_path_is_not_graded_yet(db, bus):
    publish(db, bus, slots=("09:06",))
    seed_path(db, skip=5)                   # a hole: 11 of the 12 bars in the window
    assert sig.grade_day(ISO, db_path=db) == 0
    assert grades(db) == []
    assert sig.pending_days(db_path=db) == [ISO]
    seed_path(db)
    assert sig.grade_day(ISO, db_path=db) == len(PRICES) * len(ROLES)
    assert sig.pending_days(db_path=db) == []


def test_pending_days_and_grade_pending(db, bus):
    assert sig.pending_days(db_path=db) == []
    publish(db, bus)
    assert sig.pending_days(db_path=db) == [ISO]
    seed_path(db)
    out = sig.grade_pending(db_path=db)
    assert out == {"days": 1, "rows": len(SLOTS) * len(PRICES) * len(ROLES)}
    assert sig.pending_days(db_path=db) == []
    assert sig.grade_pending(db_path=db) == {"days": 0, "rows": 0}


def test_a_symbol_that_fails_never_stops_the_others(db, bus, monkeypatch):
    publish(db, bus, slots=("09:06",))
    seed_path(db)
    real = scalp_grade._grade_rows

    def boom(day, symbol, *a, **kw):
        if symbol == "SPY":
            raise RuntimeError("bad")
        return real(day, symbol, *a, **kw)

    monkeypatch.setattr(scalp_grade, "_grade_rows", boom)
    assert sig.grade_day(ISO, db_path=db) == 2 * len(ROLES)
    assert {r["symbol"] for r in grades(db)} == {"$SPX", "QQQ"}
    assert _degrade.counts().get("options.scalp_intraday_grade", 0) >= 1


def test_flat_and_missed_entries_have_nothing_to_grade(db, bus):
    publish(db, bus, slots=("09:06",))
    seed_path(db)
    # An entry with no roles (flat, missed) is not a call: nothing to grade.
    scalp_store.save_intraday_call(ISO, "09:21", "SPY", {"slot": "09:21", "roles": {},
                                                          "note": "FLAT"}, db_path=db)
    assert sig.grade_day(ISO, db_path=db) == len(PRICES) * len(ROLES)
    assert all(r["slot"] == "09:06" for r in grades(db))
    assert sig.pending_days(db_path=db) == []


def test_the_08_36_tables_are_untouched(db, bus):
    publish(db, bus)
    seed_path(db)
    conn = scalp_store.connect(db)
    try:
        before = conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0]
    finally:
        conn.close()
    sig.grade_pending(db_path=db)
    conn = scalp_store.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == before == 0
        assert conn.execute("SELECT COUNT(*) FROM iv_calibration").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_module_does_not_import_compute():
    tree = ast.parse((SVC / "scalp_intraday_grade.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = [mod] + [f"{mod}.{a.name}" for a in node.names]
        else:
            continue
        assert not any(n == "compute" or n.endswith(".compute") for n in names)


# ── the scorecard's intraday section ─────────────────────────────────────────

def put(db, day, slot, symbol, role, model, *, word="Mixed", highlighted=0,
        avoided=0, p_base=0.5, block="morning", chain=None):
    conn = scalp_store.connect(db)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO intraday_grades (day, slot, symbol, role, "
                "structure, model_result, chain_result, highlighted, avoided, "
                "confirm_word, p_base, block) VALUES (?, ?, ?, ?, 'x', ?, ?, ?, ?, ?, ?, ?)",
                (day, slot, symbol, role, model, chain, highlighted, avoided, word,
                 p_base, block))
    finally:
        conn.close()


def test_scorecard_splits_by_confirmation_word(db):
    # SPY / morning: the long-with role over six slots, by what the call said.
    plan = [("2026-10-12", "09:06", "Confirms", "win"),
            ("2026-10-12", "09:21", "Confirms", "win"),
            ("2026-10-13", "09:06", "Confirms", "loss"),
            ("2026-10-13", "09:21", "Mixed", "win"),
            ("2026-10-14", "09:06", "Mixed", "loss"),
            ("2026-10-14", "09:21", "Contradicts", "timeout")]
    for day, slot, word, result in plan:
        put(db, day, slot, "SPY", "long_with", result, word=word)
        # Other roles carry a word too but never enter the split.
        put(db, day, slot, "SPY", "credit_with", "win", word=word)
    card = scalp_score.scorecard(db_path=db)["intraday"]
    spy = card["symbols"]["SPY"]
    morning = spy["blocks"]["morning"]
    by_word = morning["by_word"]
    assert set(by_word) == set(WORDS)
    assert by_word["Confirms"] == {"rows": 3, "wins": 2, "days": 2,
                                   "win_rate": pytest.approx(2 / 3)}
    assert by_word["Mixed"] == {"rows": 2, "wins": 1, "days": 2,
                                "win_rate": pytest.approx(0.5)}
    assert by_word["Contradicts"] == {"rows": 1, "wins": 0, "days": 1, "win_rate": 0.0}
    assert morning["days"] == 3 and spy["days"] == 3 and card["days"] == 3


def test_scorecard_has_none_not_zero_where_there_are_no_rows(db):
    put(db, "2026-10-12", "09:06", "SPY", "long_with", "win", word="Confirms")
    card = scalp_score.scorecard(db_path=db)["intraday"]["symbols"]["SPY"]["blocks"]["morning"]
    assert card["by_word"]["Mixed"] == {"rows": 0, "wins": 0, "days": 0, "win_rate": None}
    assert card["edge"] == {"days": 0, "rows": 0, "wins": 0, "rate": None, "p_base": None}
    assert card["caution"] == {"days": 0, "rows": 0, "wins": 0, "rate": None, "p_base": None}
    # A block with no rows is not shown; an unknown symbol is not either.
    assert set(scalp_score.scorecard(db_path=db)["intraday"]["symbols"]["SPY"]["blocks"]) \
        == {"morning"}
    assert "QQQ" not in scalp_score.scorecard(db_path=db)["intraday"]["symbols"]


def test_scorecard_edge_and_caution_per_block(db):
    # Morning: two highlighted rows (one win, one loss) on two days, one avoided
    # row that lost; midday: a highlighted win only. Blocks never mix.
    put(db, "2026-10-12", "09:06", "SPY", "long_with", "win", highlighted=1, p_base=0.4)
    put(db, "2026-10-13", "09:21", "SPY", "credit_with", "loss", highlighted=1, p_base=0.6)
    put(db, "2026-10-13", "09:06", "SPY", "long_against", "loss", avoided=1, p_base=0.45)
    put(db, "2026-10-13", "09:06", "SPY", "credit_against", "win")
    put(db, "2026-10-13", "10:21", "SPY", "long_with", "win", highlighted=1, p_base=0.5,
        block="midday")
    spy = scalp_score.scorecard(db_path=db)["intraday"]["symbols"]["SPY"]["blocks"]
    m, mid = spy["morning"], spy["midday"]
    assert m["edge"] == {"days": 2, "rows": 2, "wins": 1, "rate": pytest.approx(0.5),
                         "p_base": pytest.approx(0.5)}
    assert m["caution"] == {"days": 1, "rows": 1, "wins": 0, "rate": 0.0,
                            "p_base": pytest.approx(0.45)}
    assert mid["edge"] == {"days": 1, "rows": 1, "wins": 1, "rate": 1.0,
                           "p_base": pytest.approx(0.5)}
    assert mid["caution"]["rows"] == 0 and mid["caution"]["rate"] is None
    assert m["days"] == 2 and mid["days"] == 1


def test_scorecard_looks_back_scorecard_days_graded_days_per_symbol_per_block(
        db, monkeypatch):
    monkeypatch.setattr(scalp_config, "scorecard_days", lambda: 2)
    for i, day in enumerate(("2026-10-12", "2026-10-13", "2026-10-14")):
        put(db, day, "09:06", "SPY", "long_with", "win" if i else "loss", word="Confirms",
            highlighted=1)
    put(db, "2026-10-12", "10:21", "SPY", "long_with", "loss", block="midday",
        word="Mixed")
    spy = scalp_score.scorecard(db_path=db)["intraday"]["symbols"]["SPY"]["blocks"]
    assert spy["morning"]["days"] == 2 and spy["morning"]["by_word"]["Confirms"]["rows"] == 2
    assert spy["morning"]["by_word"]["Confirms"]["wins"] == 2     # the loss aged out
    assert spy["midday"]["days"] == 1                              # its own window


def test_an_ungraded_row_is_not_a_result(db):
    put(db, "2026-10-12", "09:06", "SPY", "long_with", None, word="Confirms",
        highlighted=1)
    put(db, "2026-10-12", "09:06", "SPY", "credit_with", "skip", word="Confirms")
    assert scalp_score.scorecard(db_path=db) == {}


def test_the_0836_sections_are_unchanged_by_an_intraday_section(db):
    conn = scalp_store.connect(db)
    try:
        with conn:
            for role in ROLES:
                conn.execute(
                    "INSERT INTO grades (day, symbol, role, structure, model_result, "
                    "chain_result, highlighted, avoided, p_base) "
                    "VALUES ('2026-10-12', 'SPY', ?, 'x', 'win', NULL, 0, 0, 0.5)", (role,))
    finally:
        conn.close()
    plain = scalp_score.scorecard(db_path=db)
    assert "intraday" not in plain
    put(db, "2026-10-12", "09:06", "SPY", "long_with", "win", word="Confirms")
    both = scalp_score.scorecard(db_path=db)
    assert {k: v for k, v in both.items() if k != "intraday"} == plain
    assert "intraday" in both


def test_an_intraday_only_scorecard_is_not_empty(db):
    put(db, "2026-10-12", "09:06", "SPY", "long_with", "win", word="Confirms")
    card = scalp_score.scorecard(db_path=db)
    assert set(card) == {"intraday"}
    assert card["intraday"]["symbols"]["SPY"]["blocks"]["morning"]["by_word"][
        "Confirms"]["rows"] == 1


def test_scorecard_values_are_plain_floats(db, bus):
    publish(db, bus)
    seed_path(db)
    sig.grade_pending(db_path=db)
    card = scalp_score.scorecard(db_path=db)["intraday"]

    def plain(node):
        if isinstance(node, dict):
            return all(plain(v) for v in node.values())
        return not isinstance(node, float) or type(node) is float

    assert plain(card)
    assert set(card["symbols"]) == set(PRICES)
