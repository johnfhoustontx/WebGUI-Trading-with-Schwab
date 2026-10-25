"""The Opening Scalp's net-gamma regime study: the gap rule, the cells and the
usable sessions (design 2026-10-25-scalp-gamma-regime-study, section 3.1)."""
import datetime as dt
import importlib.util
import json
import pathlib
import random
import sqlite3
import sys
from datetime import date, timedelta

import pytest

from services.options_svc import scalp_regime_study as rs
from services.options_svc import scalp_store
from shared import scalp_config

_ROOT = pathlib.Path(__file__).resolve().parents[3]


def snap(symbol="$SPX", net_gex=-1e9, state="ok", age=1.0, untrusted=False):
    return {"matrix": {symbol: {"spot": 100.0, "flip": 99.0, "call_wall": 105.0,
                                "put_wall": 95.0, "net_gex": net_gex,
                                "walls_untrusted": untrusted}},
            "sources": {"matrix": {"state": state, "age_min": age}}}


def out(day, slot, symbol="$SPX", role="long_with", result="win", scale=1.0):
    return {"day": day, "slot": slot, "symbol": symbol, "iv_scale": scale,
            "role": role, "block": scalp_config.intraday_block(slot), "result": result}


def test_regime_of_signs():
    assert rs.regime_of(snap(net_gex=-5e8), "$SPX") == rs.NEGATIVE
    assert rs.regime_of(snap(net_gex=5e8), "$SPX") == rs.POSITIVE


def test_regime_of_gaps():
    assert rs.regime_of(snap(net_gex=0.0), "$SPX") is None
    assert rs.regime_of(snap(net_gex=None), "$SPX") is None
    assert rs.regime_of(snap(net_gex=float("nan")), "$SPX") is None
    assert rs.regime_of(snap(untrusted=True), "$SPX") is None
    assert rs.regime_of(snap(state="stale"), "$SPX") is None
    assert rs.regime_of(snap(state="absent"), "$SPX") is None
    assert rs.regime_of(snap(age=6.0), "$SPX") is None          # > max_age_min 5
    assert rs.regime_of(None, "$SPX") is None
    assert rs.regime_of(snap(symbol="SPY"), "$SPX") is None     # no row for it


def test_regime_of_reads_a_backfilled_snapshot_only_when_allowed():
    assert rs.regime_of(snap(state="backfill", age=None), "$SPX", allow_backfill=True) == rs.NEGATIVE


def test_a_backfilled_snapshot_is_a_gap_for_the_study():
    """Design 3.1: the regime is the LIVE record only."""
    assert rs.regime_of(snap(state="backfill", age=None), "$SPX") is None
    readings = {("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(state="backfill", age=None)}}
    assert rs.regimes(readings) == {("2026-10-21", "12:36", "$SPX"): None}


def test_the_gap_rule_on_the_matrix_age():
    assert rs.regime_of(snap(state="ok", age=None), "$SPX") is None     # no age is no proof
    assert rs.regime_of(snap(age=5.0), "$SPX") == rs.NEGATIVE           # exactly the limit is fresh
    assert rs.regime_of(snap(age=5.01), "$SPX") is None


def test_regime_of_matches_symbol_without_dollar():
    assert rs.regime_of(snap(symbol="SPX"), "$SPX") == rs.NEGATIVE


def test_regimes_maps_each_reading_by_its_symbol():
    readings = {("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(net_gex=-5e8)},
                ("2026-10-21", "13:06", "$SPX"): {"snapshot": snap(net_gex=5e8)},
                ("2026-10-21", "12:36", "SPY"): {"snapshot": snap(net_gex=5e8)},
                ("2026-10-21", "13:36", "$SPX"): {}}
    assert rs.regimes(readings) == {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE,
                                    ("2026-10-21", "13:06", "$SPX"): rs.POSITIVE,
                                    ("2026-10-21", "12:36", "SPY"): None,
                                    ("2026-10-21", "13:36", "$SPX"): None}


def test_cells_count_labelled_long_rows_of_the_block():
    rows = [out("2026-10-21", "12:36", result="win"),
            out("2026-10-21", "12:36", role="long_against", result="timeout"),
            out("2026-10-21", "12:36", role="credit_with", result="win"),
            out("2026-10-21", "12:36", result="skip"),
            out("2026-10-21", "12:36", result="win", scale=1.25),
            out("2026-10-21", "10:06", result="win")]
    got = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    assert got == {("2026-10-21", "$SPX", "12:36"): [1, 2, 1]}


def test_usable_sessions_start_on_the_declared_day():
    regime = {("2026-10-20", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    rows = [out("2026-10-20", "12:36"), out("2026-10-21", "12:36")]
    assert rs.usable_sessions(regime, rows) == ["2026-10-21"]


def test_usable_sessions_skip_early_close_and_gaps():
    regime = {("2026-11-27", "12:36", "$SPX"): rs.NEGATIVE,
              ("2026-10-22", "12:36", "$SPX"): None}
    rows = [out("2026-11-27", "12:36"), out("2026-10-22", "12:36")]
    assert rs.usable_sessions(regime, rows) == []


def test_usable_sessions_ignore_the_0836_record():
    regime = {("2026-10-21", "08:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "08:36")]) == []


def test_usable_session_needs_regime_and_outcome_on_the_same_call():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    apart = [out("2026-10-21", "13:06", "$SPX")]
    assert rs.usable_sessions(regime, apart) == []
    other_symbol = [out("2026-10-21", "12:36", "SPY")]
    assert rs.usable_sessions(regime, other_symbol) == []
    together = apart + [out("2026-10-21", "12:36", "$SPX")]
    assert rs.usable_sessions(regime, together) == ["2026-10-21"]


def test_day_without_afternoon_outcomes_is_not_usable():
    regime = {("2026-10-21", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.usable_sessions(regime, [out("2026-10-21", "10:06")]) == []


# ── the effect, the permutation and the declared verdict ─────────────────────

_WORLD_SYMBOLS = ("$SPX", "SPY", "QQQ")
_WORLD_SLOTS = ("12:36", "13:06")
_AGREE = {"share": 1.0, "sessions": 5}


def world(n_days, neg_win, pos_win):
    """``(regime_map, outcomes)`` for ``n_days`` sessions from 2026-10-21: three
    symbols, two afternoon slots, regimes alternating by day and symbol, and a
    long outcome for both long roles at every multiplier."""
    rng = random.Random(1)
    regime, rows = {}, []
    day, i = date(2026, 10, 21), 0
    while i < n_days:
        if day.weekday() < 5 and day not in (date(2026, 11, 26), date(2026, 11, 27)):
            for j, sym in enumerate(_WORLD_SYMBOLS):
                reg = rs.NEGATIVE if (i + j) % 2 == 0 else rs.POSITIVE
                win = neg_win if reg == rs.NEGATIVE else pos_win
                for slot in _WORLD_SLOTS:
                    regime[(day.isoformat(), slot, sym)] = reg
                    for scale in scalp_config.iv_scales():
                        for role in rs.LONG_ROLES:
                            result = "win" if rng.random() < win else "timeout"
                            rows.append(out(day.isoformat(), slot, sym, role, result, scale))
            i += 1
        day += timedelta(days=1)
    return regime, rows


@pytest.fixture(autouse=True)
def _few_permutations(monkeypatch):
    monkeypatch.setattr(scalp_config, "regime_permutations", lambda: 300)


def test_effect_is_negative_share_minus_positive_share():
    regime = {("d1", "12:36", "$SPX"): rs.NEGATIVE, ("d1", "13:06", "$SPX"): rs.POSITIVE,
              ("d2", "12:36", "$SPX"): rs.NEGATIVE}
    cell_map = {("d1", "$SPX", "12:36"): [2, 2, 0], ("d1", "$SPX", "13:06"): [0, 2, 2],
                ("d2", "$SPX", "12:36"): [1, 2, 1]}
    assert rs.effect(cell_map, regime) == pytest.approx(75.0)   # mean(1.0, 0.5) - 0.0


def test_effect_undefined_when_one_side_is_empty():
    regime = {("d1", "12:36", "$SPX"): rs.NEGATIVE}
    assert rs.effect({("d1", "$SPX", "12:36"): [1, 2, 0]}, regime) is None


def test_permutation_is_deterministic_and_bounded():
    regime, rows = world(12, 0.7, 0.3)
    sessions = sorted({d for d, _, _ in regime})
    cm = rs.cells(rows, 1.0, "afternoon", rs.LONG_ROLES)
    obs = rs.effect(cm, regime)
    a = rs.permutation_p(cm, regime, sessions, obs, 200, 5)
    assert a == rs.permutation_p(cm, regime, sessions, obs, 200, 5)
    assert 0.0 <= a <= 1.0


def test_collecting_shows_nothing_but_the_count():
    regime, rows = world(39, 0.9, 0.1)
    got = rs.evaluate(regime, rows, _AGREE)
    assert got == {"status": "collecting", "sessions": 39, "needed": 40,
                   "first": "2026-10-21", "start_day": "2026-10-21"}


def test_a_strong_afternoon_effect_passes_and_midday_is_tested_next():
    regime, rows = world(40, 0.8, 0.2)
    got = rs.evaluate(regime, rows, _AGREE)
    first, second = got["blocks"][0], got["blocks"][1]
    assert first["block"] == "afternoon" and first["passed"] and first["reasons"] == []
    assert second["block"] == "midday" and second["tested"]
    assert second["reasons"] == ["effect undefined"]       # the world has no midday rows
    assert got["blocks"][2]["reasons"] == ["not tested: midday did not pass"]


def test_no_effect_fails_and_stops_the_order():
    regime, rows = world(40, 0.0, 0.0)          # every long times out: effect 0, p 1.0
    blocks = rs.evaluate(regime, rows, _AGREE)["blocks"]
    assert blocks[0]["reasons"] == ["p above alpha", "effect below the floor",
                                   "not above 0 at every multiplier"]
    assert blocks[1]["reasons"] == ["not tested: afternoon did not pass"]


def test_small_effect_fails_the_floor(monkeypatch):
    monkeypatch.setattr(scalp_config, "regime_min_effect_pp", lambda: 99.0)
    regime, rows = world(40, 0.8, 0.2)
    assert "effect below the floor" in rs.evaluate(regime, rows, _AGREE)["blocks"][0]["reasons"]


def test_precondition_missing_or_failed_blocks_the_test():
    """Design 3.2: the precondition runs BEFORE the test; with it unpassed no
    block, effect or p is computed."""
    regime, rows = world(40, 0.8, 0.2)
    missing = rs.evaluate(regime, rows, None)
    assert missing["status"] == "blocked" and "blocks" not in missing
    assert missing["precondition"] == {"checked": False, "passed": False,
                                       "share": None, "sessions": None}
    assert missing["sessions"] == rs.usable_sessions(regime, rows)[:40]
    low = rs.evaluate(regime, rows, {"share": 0.9, "sessions": 5})
    assert low["status"] == "blocked"
    assert low["precondition"]["checked"] and not low["precondition"]["passed"]
    few = rs.evaluate(regime, rows, {"share": 1.0, "sessions": 4})
    assert few["status"] == "blocked"


def test_a_blocked_evaluation_runs_no_permutation(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the test must not run")
    monkeypatch.setattr(rs, "permutation_p", boom)
    monkeypatch.setattr(rs, "effect", boom)
    regime, rows = world(40, 0.8, 0.2)
    assert rs.evaluate(regime, rows, None)["status"] == "blocked"


def test_a_multiplier_at_or_below_zero_fails():
    regime, rows = world(40, 0.8, 0.2)
    rows = [dict(r, result="timeout") if r["iv_scale"] == 1.5 else r for r in rows]
    assert "not above 0 at every multiplier" in rs.evaluate(regime, rows, _AGREE)["blocks"][0]["reasons"]


def test_sessions_after_the_minimum_are_ignored():
    regime, rows = world(40, 0.8, 0.2)
    base = rs.evaluate(regime, rows, _AGREE)
    more_regime, more_rows = world(45, 0.8, 0.2)
    later = {k: v for k, v in more_regime.items() if k[0] > base["sessions"][-1]}
    flipped = [dict(r, result="win") for r in more_rows if r["day"] > base["sessions"][-1]]
    again = rs.evaluate({**regime, **later}, rows + flipped, _AGREE)
    assert again == base


# ── the loaders and the read-only report ─────────────────────────────────────

def _regime_report():
    spec = importlib.util.spec_from_file_location(
        "scalp_regime_report", _ROOT / "tools" / "scalp_regime_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed_store(tmp_path, n_days, neg_win, pos_win):
    """A real-schema store holding ``world``'s regimes as recorded readings and its
    outcomes as ``intraday_outcomes`` rows."""
    regime, rows = world(n_days, neg_win, pos_win)
    db = tmp_path / "scalp.db"
    conn = scalp_store.connect(db)
    conn.close()
    for (day, slot, sym), reg in regime.items():
        gex = -1e9 if reg == rs.NEGATIVE else 1e9
        scalp_store.save_reading(day, slot, sym, {"snapshot": snap(symbol=sym, net_gex=gex)},
                                 db_path=db)
    conn = scalp_store.connect(db)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO intraday_outcomes (day, slot, symbol, iv_scale, role, "
                "structure, block, bucket, result, minutes, entry_value, both_in_bar) "
                "VALUES (?, ?, ?, ?, ?, 'long_call', ?, NULL, ?, NULL, NULL, 0)",
                [(r["day"], r["slot"], r["symbol"], r["iv_scale"], r["role"],
                  r["block"], r["result"]) for r in rows])
    finally:
        conn.close()
    return db


def readings_keys(db):
    return list(scalp_store.readings_all(db_path=db))


def opposite_snapshot(db, key):
    """The snapshot with the other sign of net GEX than the one stored for ``key``."""
    day, slot, sym = key
    stored = scalp_store.readings_all(db_path=db)[key]["snapshot"]
    net = stored["matrix"][sym]["net_gex"]
    return snap(symbol=sym, net_gex=1e9 if net < 0 else -1e9)


def test_loaders_round_trip(tmp_path):
    db = seed_store(tmp_path, 3, 0.8, 0.2)
    conn = sqlite3.connect(db)
    readings, rows = rs.load_readings(conn), rs.load_outcomes(conn)
    conn.close()
    assert len({k[0] for k in readings}) == 3
    assert set(rows[0]) == {"day", "slot", "symbol", "iv_scale", "role", "block", "result"}
    assert readings == scalp_store.readings_all(db_path=db)


def test_report_while_collecting_prints_only_the_count(tmp_path):
    db = seed_store(tmp_path, 12, 0.9, 0.1)
    text = _regime_report().render(db, today=dt.date(2026, 11, 6))
    assert "# Gamma regime study - 2026-11-06" in text
    assert "Collecting: 12 of 40 usable sessions since 2026-10-21." in text
    assert "First usable session: 2026-10-21." in text
    assert "Nothing else is shown until usable session 40 is graded (design §3.4)." in text
    assert " pp" not in text and "PASS" not in text and "FAIL" not in text
    assert "p-value" not in text and "Precondition" not in text
    # (the autouse fixture's 300 permutations are a drift from the declared 10000)
    assert "Settings" in text and "Early-close days left out:" in text


def test_report_ready_prints_verdicts_in_order(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    agree = tmp_path / "agree.json"
    agree.write_text(json.dumps({"share": 1.0, "sessions": 5}))
    text = _regime_report().render(db, agreement_path=agree)
    assert text.index("**afternoon: PASS**") < text.index("**midday:") < text.index("**morning:")
    assert "Precondition: passed (100.00% of slots agree over 5 sessions)." in text
    assert "Sessions: 2026-10-21 to " in text and " (40)." in text
    assert " pp" in text
    assert f"(Python {sys.version.split()[0]})" in text.splitlines()[0]


def _assert_nothing_decided(text):
    assert " pp" not in text and "PASS" not in text and "FAIL" not in text
    assert "p-value:" not in text and "Pairs" not in text and "Reported, never decided" not in text


def test_report_without_agreement_shows_no_effect_at_all(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    text = _regime_report().render(db)
    assert "Sessions: 2026-10-21 to " in text and " (40)." in text
    assert "Precondition not checked: the test does not run." in text
    assert ("No effect, p-value or verdict is shown until the precondition passes "
            "(design §3.2).") in text
    _assert_nothing_decided(text)
    assert "A fail does not show" not in text and "(Python" not in text


def test_report_with_a_failed_agreement_shows_no_effect_either(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    agree = tmp_path / "agree.json"
    agree.write_text(json.dumps({"share": 0.9, "sessions": 6}))
    text = _regime_report().render(db, agreement_path=agree)
    assert "Precondition failed (90.00% of slots agree): the test does not run." in text
    _assert_nothing_decided(text)


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]"])
def test_an_unusable_agreement_file_is_no_file(tmp_path, content):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    path = tmp_path / "agree.json"
    if content is not None:
        path.write_text(content)
    report = _regime_report()
    assert report.render(db, agreement_path=path) == report.render(db)
    assert "Precondition not checked: the test does not run." in report.render(db, agreement_path=path)


def test_report_ignores_prob_sources(tmp_path):
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    before = _regime_report().render(db, today=dt.date(2026, 12, 20))
    flipped = [("intraday", day, slot, sym, opposite_snapshot(db, (day, slot, sym)))
               for (day, slot, sym) in readings_keys(db)]
    scalp_store.save_prob_sources(flipped, db_path=db)
    assert _regime_report().render(db, today=dt.date(2026, 12, 20)) == before


def test_report_opens_the_store_read_only(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        _regime_report().render(missing)
    assert not missing.exists()


def _canned(p, reasons=(), passed=False, tested=True):
    return {"block": "afternoon", "tested": tested, "passed": passed,
            "reasons": list(reasons),
            "effect_pp": {"1": 7.5, "1.25": None}, "p": p,
            "negative_pairs": 30, "positive_pairs": 28,
            "reported": {"per_symbol": {"SPY": 3.0, "QQQ": None}, "credit_pp": -1.5,
                         "long_with_pp": 4.0, "long_against_pp": None,
                         "timeout_share": {"negative": 0.25, "positive": None}}}


def test_report_prints_the_p_value_and_the_pass_and_fail_words(tmp_path, monkeypatch):
    db = seed_store(tmp_path, 1, 0.8, 0.2)
    ready = {"status": "ready", "sessions": ["2026-10-21", "2026-10-22"],
             "precondition": {"checked": True, "passed": True, "share": 0.99, "sessions": 6},
             "blocks": [_canned(0.0, ["p above alpha", "effect below the floor"]),
                        dict(_canned(None, ["not tested: afternoon did not pass"], tested=False),
                             block="midday")]}
    monkeypatch.setattr(rs, "evaluate", lambda *a: ready)
    text = _regime_report().render(db)
    assert "Sessions: 2026-10-21 to 2026-10-22 (2)." in text
    assert "Precondition: passed (99.00% of slots agree over 6 sessions)." in text
    assert "**afternoon: FAIL** (p above alpha; effect below the floor)" in text
    assert "< 0.0033" in text                 # 1 / 300 permutations, four decimals
    assert "+7.50 pp" in text and "-1.50 pp" in text
    assert "**midday: not tested** (afternoon did not pass)" in text
    ready["blocks"][0]["p"] = 0.0321
    assert "0.0321" in _regime_report().render(db)


def test_main_prints_the_report_for_a_path(tmp_path, capsys):
    db = seed_store(tmp_path, 12, 0.9, 0.1)
    agree = tmp_path / "agree.json"
    agree.write_text(json.dumps({"share": 1.0, "sessions": 5}))
    assert _regime_report().main([str(db), "--agreement", str(agree)]) == 0
    assert "Collecting: 12 of 40 usable sessions" in capsys.readouterr().out


# ── the declared settings must not drift silently ────────────────────────────

def test_declared_matches_the_shipped_config(monkeypatch):
    monkeypatch.undo()                     # the autouse fixture's 300 permutations
    assert rs.drift() == []
    assert set(rs.settings_in_use()) == set(rs.DECLARED)
    assert rs.DECLARED["regime.min_sessions"] == 40 and rs.DECLARED["regime.seed"] == 20261025


def test_declared_is_frozen():
    with pytest.raises(TypeError):
        rs.DECLARED["regime.min_sessions"] = 30


def test_drift_names_each_changed_setting(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(scalp_config, "regime_min_sessions", lambda: 30)
    monkeypatch.setattr(scalp_config, "confirm_max_age_min", lambda: 9)
    assert rs.drift() == ["regime.min_sessions", "confirm.max_age_min"]


def test_drift_sees_a_moved_block(monkeypatch):
    monkeypatch.undo()
    blocks = scalp_config._blocks()
    blocks["afternoon"] = (blocks["afternoon"][0], blocks["afternoon"][1] - 5)
    monkeypatch.setattr(scalp_config, "_blocks", lambda: blocks)
    assert rs.drift() == ["intraday.blocks.afternoon"]


def test_report_says_the_settings_are_as_declared(tmp_path, monkeypatch):
    monkeypatch.undo()
    db = seed_store(tmp_path, 12, 0.9, 0.1)
    text = _regime_report().render(db, today=dt.date(2026, 11, 6))
    assert "Settings: as declared." in text and "DIFFER" not in text
    assert "Early-close days left out: " in text and "2026-11-27" in text


def test_report_names_a_drifted_setting_while_still_collecting(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(scalp_config, "regime_min_sessions", lambda: 30)
    db = seed_store(tmp_path, 12, 0.9, 0.1)
    text = _regime_report().render(db, today=dt.date(2026, 11, 6))
    assert ("Settings DIFFER from the declaration: regime.min_sessions. "
            "This is not the declared study.") in text.splitlines()
    assert "Settings: as declared." not in text
    assert "Collecting: 12 of 30 usable sessions" in text


def test_report_prints_the_settings_line_in_every_status(tmp_path, monkeypatch):
    monkeypatch.undo()
    db = seed_store(tmp_path, 40, 0.8, 0.2)
    report = _regime_report()
    blocked = report.render(db)
    assert "Settings: as declared." in blocked
    ready = {"status": "ready", "sessions": ["2026-10-21", "2026-10-22"],
             "precondition": {"checked": True, "passed": True, "share": 1.0, "sessions": 6},
             "blocks": [_canned(0.01, passed=True)]}
    monkeypatch.setattr(rs, "evaluate", lambda *a: ready)
    assert "Settings: as declared." in report.render(db)
    monkeypatch.setattr(scalp_config, "regime_alpha", lambda: 0.1)
    assert ("Settings DIFFER from the declaration: regime.alpha. "
            "This is not the declared study.") in report.render(db)


# ── the permutation, pinned directly ─────────────────────────────────────────

def test_permutation_of_two_sessions_is_a_coin_flip():
    """Day A is negative and won, day B positive and lost: the observed effect is
    100, and swapping the two days gives -100, so about half the relabelings (the
    identity) reach it."""
    regime = {("A", "12:36", "$SPX"): rs.NEGATIVE, ("B", "12:36", "$SPX"): rs.POSITIVE}
    cell_map = {("A", "$SPX", "12:36"): [1, 1, 0], ("B", "$SPX", "12:36"): [0, 1, 0]}
    observed = rs.effect(cell_map, regime)
    assert observed == pytest.approx(100.0)
    for seed in (1, 2, 20261025):
        p = rs.permutation_p(cell_map, regime, ["A", "B"], observed, 2000, seed)
        assert 0.4 <= p <= 0.6, (seed, p)


def test_a_moved_day_keeps_only_the_slots_both_days_read():
    regime = {("A", "12:36", "$SPX"): rs.NEGATIVE, ("A", "13:06", "$SPX"): rs.NEGATIVE,
              ("B", "12:36", "$SPX"): rs.POSITIVE}                  # B has no 13:06 reading
    swapped = rs._moved(regime, ["A", "B"], {"A": "B", "B": "A"})
    assert swapped[("A", "12:36", "$SPX")] == rs.POSITIVE
    assert swapped[("B", "12:36", "$SPX")] == rs.NEGATIVE
    assert swapped[("A", "13:06", "$SPX")] is None                  # a gap, not A's own sign
    assert ("B", "13:06", "$SPX") not in swapped
    same = rs._moved(regime, ["A", "B"], {"A": "A", "B": "B"})
    assert same == regime


def test_a_gap_on_either_day_is_a_gap_after_the_move():
    regime = {("A", "12:36", "$SPX"): None, ("B", "12:36", "$SPX"): rs.POSITIVE,
              ("A", "13:06", "$SPX"): rs.NEGATIVE, ("B", "13:06", "$SPX"): None}
    swapped = rs._moved(regime, ["A", "B"], {"A": "B", "B": "A"})
    assert set(swapped.values()) == {None}


def test_the_gap_decides_the_permuted_effect():
    """A has two negative slots (both won), B one positive slot (lost). Swapped, A
    keeps only the slot B has - positive - and B takes A's negative: the effect is
    -100 from the shared slot alone. Were A's second slot to keep its own sign the
    effect would read -50 instead."""
    regime = {("A", "12:36", "$SPX"): rs.NEGATIVE, ("A", "13:06", "$SPX"): rs.NEGATIVE,
              ("B", "12:36", "$SPX"): rs.POSITIVE}
    cell_map = {("A", "$SPX", "12:36"): [1, 1, 0], ("A", "$SPX", "13:06"): [1, 1, 0],
                ("B", "$SPX", "12:36"): [0, 1, 0]}
    swapped = rs._moved(regime, ["A", "B"], {"A": "B", "B": "A"})
    assert rs.effect(cell_map, swapped) == pytest.approx(-100.0)


def test_permutation_moves_a_whole_day_across_symbols(monkeypatch):
    """Three symbols that always share a day's sign still share it under every
    relabeling the test draws: the day moves with all its symbols together."""
    signs = [rs.NEGATIVE, rs.POSITIVE, rs.NEGATIVE, rs.POSITIVE, rs.POSITIVE]
    days = [f"d{i}" for i in range(len(signs))]
    regime = {(d, "12:36", s): sign for d, sign in zip(days, signs) for s in _WORLD_SYMBOLS}
    cell_map = {(d, s, "12:36"): [1, 2, 0] for d in days for s in _WORLD_SYMBOLS}
    drawn = []
    real = rs._moved

    def spy(*args, **kwargs):
        got = real(*args, **kwargs)
        drawn.append(got)
        return got

    monkeypatch.setattr(rs, "_moved", spy)
    rs.permutation_p(cell_map, regime, days, 0.0, 200, 11)
    assert len(drawn) == 200
    for fake in drawn:
        assert len(fake) == len(regime)
        for d in days:
            assert len({fake[(d, "12:36", s)] for s in _WORLD_SYMBOLS}) == 1
    assert len({tuple(fake[(d, "12:36", "$SPX")] for d in days) for fake in drawn}) > 1
