"""tools/hiro_report.py -- the daily HIRO-model validation report.

The report decides whether the hiro_surge / hiro_flip alerts ever earn a phone
push, so it must replay EXACTLY the live rules (services/options_svc/hiro.py)
and never print a zero it did not measure. Design:
docs/plans/2026-10-01-hiro-alert-design.md section 3.

``tools/`` has no ``__init__.py``; the sys.path insert mirrors
tools/tests/test_flow_delta_instrumentation.py.
"""
import math
import pathlib
import random
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from services.options_svc import hiro  # noqa: E402
from tools import hiro_report as hr  # noqa: E402

DAY = "2026-09-30"          # a Wednesday, an ordinary trading day
T0 = hiro.ct_ts(DAY, "08:30")

CFG = {"enabled": True, "push": False, "public": False,
       "symbols": ["$SPX", "SPY"], "window_min": 15, "k": 3.0, "push_k": 4.0,
       "min_notional": 1, "max_unclassified": 0.5, "cooldown_min": 30,
       "baseline_sessions": 2, "min_minutes": 30, "flip_enabled": True,
       "flip_band": 1.0, "flip_not_before": "09:00", "flip_cooldown_min": 60,
       "keep_sessions": 20}

# A zero percentage that stands alone ("0%", "0.0%"), not the tail of "50%".
_ZERO_PCT = re.compile(r"(?<![\d.])0(?:\.0+)?%")


def _rows(impacts, spots=None, t0=T0, cls=100.0, uncl=10.0):
    spots = spots or [100.0] * len(impacts)
    return [{"ts": t0 + 60 * i, "spot": spots[i], "impact": float(v),
             "classified_vol": cls, "unclassified_vol": uncl}
            for i, v in enumerate(impacts)]


def _quiet(n):
    return [1000.0 if i % 2 else -1000.0 for i in range(n)]


# --- replay_surges ------------------------------------------------------------
def test_load_cfg_tolerates_a_non_dict_hiro_table(monkeypatch):
    """``hiro = 5`` in the TOML replaces the whole table; the report falls
    back to the built-in [hiro] defaults rather than crashing on dict(5)."""
    from services.options_svc import flow_alerts
    monkeypatch.setattr(hr.flow_alerts, "load_thresholds", lambda: {"hiro": 5})
    assert hr.load_cfg() == flow_alerts._DEFAULTS["hiro"]


def test_load_cfg_coerces_numbers_like_the_live_handler(monkeypatch):
    """The SAME coercion live applies (hiro.clean_cfg), so a string cooldown
    replays as the default it runs at live, not as a crash."""
    from services.options_svc import flow_alerts
    d = flow_alerts._DEFAULTS["hiro"]
    monkeypatch.setattr(hr.flow_alerts, "load_thresholds",
                        lambda: {"hiro": {**d, "cooldown_min": "30", "k": math.nan,
                                          "push_k": "4", "symbols": ["SPY"]}})
    c = hr.load_cfg()
    assert c["cooldown_min"] == d["cooldown_min"] and c["k"] == d["k"]
    assert c["push_k"] == d["push_k"] and c["symbols"] == ["SPY"]


def test_a_long_surge_fires_once_per_cooldown_not_once_per_minute():
    imp = _quiet(200)
    for i in range(100, 140):               # a 40-minute surge
        imp[i] = 1e6
    fires = hr.replay_surges(_rows(imp), 1e6, CFG, 3.0)
    assert len(fires) == 2
    assert [f["ts"] for f in fires] == [T0 + 60 * 102, T0 + 60 * 132]
    assert all(f["side"] == "dealers_buying" for f in fires)


def test_the_two_directions_cool_down_independently():
    imp = _quiet(200)
    for i in range(100, 105):
        imp[i] = 1e6
    for i in range(120, 125):               # 20 minutes later, inside 30
        imp[i] = -1e6
    fires = hr.replay_surges(_rows(imp), 1e6, CFG, 3.0)
    assert [f["side"] for f in fires] == ["dealers_buying", "dealers_selling"]


def test_a_higher_k_never_fires_more_often_than_a_lower_k():
    rnd = random.Random(7)
    imp = [rnd.gauss(0, 4e5) for _ in range(390)]
    rows = _rows(imp)
    counts = [len(hr.replay_surges(rows, 1e6, CFG, k))
              for k in (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > 0                    # the property was actually exercised


def test_replay_matches_detect_surge_over_every_full_prefix():
    """The replay hands detect_surge only the window's rows; the answer must be
    exactly what the live rule gives over the whole prefix, minute by minute."""
    rnd = random.Random(11)
    rows = _rows([rnd.gauss(0, 5e5) for _ in range(200)])
    for k in (2.0, 3.0):
        want, last = [], {}
        for i, r in enumerate(rows):
            a = hiro.detect_surge("", rows[:i + 1], 1e6, {**CFG, "k": k},
                                  now_ts=r["ts"])
            if a and (a["side"] not in last
                      or r["ts"] - last[a["side"]] >= CFG["cooldown_min"] * 60):
                last[a["side"]] = r["ts"]
                want.append((a["ts"], a["side"], a["impact"]))
        got = [(a["ts"], a["side"], a["impact"])
               for a in hr.replay_surges(rows, 1e6, CFG, k)]
        assert got == want and want


def test_replay_uses_the_live_rule_for_its_floors():
    """The dead-tape floor is detect_surge's, not a re-implementation."""
    imp = _quiet(60)
    for i in range(30, 35):
        imp[i] = 1e6
    cfg = {**CFG, "min_notional": 1e12}
    assert hr.replay_surges(_rows(imp), 1e6, cfg, 3.0) == []


def test_no_sigma_replays_nothing():
    imp = _quiet(60)
    assert hr.replay_surges(_rows(imp), None, CFG, 3.0) == []
    assert hr.replay_surges(_rows(imp), float("nan"), CFG, 3.0) == []


# --- forward_return -----------------------------------------------------------
def _fire(side, i, spot=100.0):
    return {"side": side, "ts": T0 + 60 * i, "spot": spot}


def test_forward_return_is_signed_by_the_hedging_direction():
    spots = [100.0] * 30
    for i in range(10, 30):
        spots[i] = 101.0                    # price went up after minute 5
    rows = _rows(_quiet(30), spots)
    up = hr.forward_return(rows, _fire("dealers_buying", 5), 5)
    down = hr.forward_return(rows, _fire("dealers_selling", 5), 5)
    assert math.isclose(up, 0.01)
    assert math.isclose(down, -0.01)
    assert math.isclose(hr.forward_return(rows, _fire("to_buying", 5), 5), 0.01)
    assert math.isclose(hr.forward_return(rows, _fire("to_selling", 5), 5), -0.01)


def test_forward_return_is_none_past_the_session_end():
    rows = _rows(_quiet(20))
    assert hr.forward_return(rows, _fire("dealers_buying", 10), 15) is None


def test_forward_return_is_none_on_an_unusable_spot():
    for bad in (0.0, float("nan"), float("inf"), None, -5.0):
        spots = [100.0] * 30
        spots[10] = bad
        rows = _rows(_quiet(30), spots)
        assert hr.forward_return(rows, _fire("dealers_buying", 5), 5) is None, bad
        assert hr.forward_return(rows, _fire("dealers_buying", 10, bad), 5) is None, bad


def test_forward_return_is_none_across_a_collection_gap():
    """A restart gap must not stretch a 5-minute horizon into an hour."""
    rows = _rows(_quiet(10)) + _rows(_quiet(10), t0=T0 + 60 * 70)
    assert hr.forward_return(rows, _fire("dealers_buying", 8), 5) is None


# --- replay_flips --------------------------------------------------------------
def _flip_day():
    # cum climbs to +10, falls to -10, climbs to +10 again -- all after 09:00.
    imp = [0.0] * 40 + [1.0] * 10 + [-2.0] * 10 + [2.0] * 10 + [0.0] * 10
    return _rows(imp)


def test_replay_flips_agrees_with_the_live_transitions():
    rows = _flip_day()
    nb = hiro.ct_ts(DAY, "09:00")
    cfg = {**CFG, "flip_cooldown_min": 0, "flip_band": 1.0}
    got = [(f["ts"], f["side"]) for f in hr.replay_flips(rows, 2.0, cfg, nb)]
    want = [(ts, "to_buying" if s == "buying" else "to_selling")
            for ts, s, _cum in hiro.flip_transitions(rows, 2.0, nb)]
    assert got == want
    assert len(got) == 2


def test_replay_flips_honours_the_flip_cooldown():
    rows = _flip_day()
    nb = hiro.ct_ts(DAY, "09:00")
    every = hr.replay_flips(rows, 2.0, {**CFG, "flip_cooldown_min": 0}, nb)
    cooled = hr.replay_flips(rows, 2.0, {**CFG, "flip_cooldown_min": 60}, nb)
    assert len(every) == 2 and len(cooled) == 1
    assert cooled[0]["ts"] == every[0]["ts"]


def test_replay_flips_without_sigma_is_empty():
    assert hr.replay_flips(_flip_day(), None, CFG, hiro.ct_ts(DAY, "09:00")) == []


# --- symbol_summary -------------------------------------------------------------
def _noisy(n, seed):
    rnd = random.Random(seed)
    return _rows([rnd.gauss(0, 1e6) for _ in range(n)])


def test_summary_uses_prior_sessions_when_there_are_enough():
    prior = [_noisy(60, 1), _noisy(60, 2)]
    s = hr.symbol_summary(_noisy(60, 3), prior, CFG, [3.0], [5, 15],
                          hiro.ct_ts(DAY, "09:00"))
    assert s["sigma_source"] == "prior"
    assert math.isclose(s["sigma"], hiro.prior_sigma(prior, CFG))
    assert s["rows"] == 60


def test_summary_falls_back_to_today_like_the_handler():
    today = _noisy(40, 3)
    s = hr.symbol_summary(today, [_noisy(60, 1)], CFG, [3.0], [5, 15],
                          hiro.ct_ts(DAY, "09:00"))
    assert s["sigma_source"] == "today"
    assert math.isclose(s["sigma"], hiro.baseline_sigma([], today, CFG))


def test_summary_with_too_little_data_measures_nothing():
    s = hr.symbol_summary(_noisy(10, 3), [_noisy(60, 1)], CFG, [2.0, 3.0], [5, 15],
                          hiro.ct_ts(DAY, "09:00"))
    assert s["sigma"] is None and s["sigma_source"] is None
    assert all(v is None for v in s["fires_by_k"].values())
    assert s["flips"] is None
    assert all(v == [] for v in s["fwd"][3.0].values())
    assert all(v == [] for v in s["flip_fwd"].values())


def test_summary_unclassified_share_is_of_the_whole_day():
    s = hr.symbol_summary(_rows(_quiet(10), cls=75.0, uncl=25.0), [], CFG, [3.0],
                          [5], hiro.ct_ts(DAY, "09:00"))
    assert math.isclose(s["unclassified"], 0.25)
    empty = hr.symbol_summary(_rows(_quiet(10), cls=0.0, uncl=0.0), [], CFG, [3.0],
                              [5], hiro.ct_ts(DAY, "09:00"))
    assert empty["unclassified"] is None


def test_summary_always_measures_the_live_k_and_push_k():
    s = hr.symbol_summary(_noisy(60, 3), [_noisy(60, 1), _noisy(60, 2)], CFG,
                          [2.0], [5], hiro.ct_ts(DAY, "09:00"))
    assert set(s["fires_by_k"]) == {2.0, 3.0, 4.0}


def test_summary_forward_returns_belong_to_the_live_k_fires():
    imp = _quiet(120)
    for i in range(60, 65):
        imp[i] = 1e7
    spots = [100.0] * 62 + [102.0] * 58
    rows = _rows(imp, spots)
    prior = [_noisy(60, 1), _noisy(60, 2)]
    s = hr.symbol_summary(rows, prior, CFG, [3.0], [5, 15], hiro.ct_ts(DAY, "09:00"))
    assert s["fires_by_k"][3.0] >= 1
    assert len(s["fwd"][3.0][5]) == len(s["surges"])
    assert all(r > 0 for r in s["fwd"][3.0][5])


# --- build_report -----------------------------------------------------------------
def _summaries():
    prior = [_noisy(60, 1), _noisy(60, 2)]
    good = hr.symbol_summary(_noisy(60, 3), prior, CFG, [2.0, 3.0], [5, 15],
                             hiro.ct_ts(DAY, "09:00"))
    starved = hr.symbol_summary(_rows(_quiet(5), cls=0.0, uncl=0.0), [], CFG,
                                [2.0, 3.0], [5, 15], hiro.ct_ts(DAY, "09:00"))
    return {"$SPX": good, "SPY": starved, "QQQ": None}


def test_the_report_says_it_is_a_model_and_marks_the_live_k():
    text = hr.build_report(DAY, _summaries(), CFG)
    assert "model" in text.lower()
    assert "3.0 (live)" in text
    assert "4.0 (push)" in text
    assert "$SPX" in text and "SPY" in text and "QQQ" in text


def test_an_empty_symbol_says_so_rather_than_printing_zeros():
    text = hr.build_report(DAY, _summaries(), CFG)
    qqq = text.split("## QQQ", 1)[1]
    assert "no minutes stored" in qqq
    spy = text.split("## SPY", 1)[1].split("## QQQ", 1)[0]
    assert "—" in spy
    assert not _ZERO_PCT.search(spy), spy


def test_a_symbol_that_failed_is_named_with_its_reason():
    text = hr.build_report(DAY, {"IWM": {"error": "ValueError: bad row"}}, CFG)
    assert "IWM" in text and "ValueError: bad row" in text


# --- default_day ----------------------------------------------------------------------
def _ct(day, hhmm):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(hiro.ct_ts(day, hhmm), hr.CT)


def test_after_the_close_the_default_day_is_today():
    import datetime as _dt
    assert hr.default_day(_ct(DAY, "16:10")) == _dt.date(2026, 9, 30)


def test_a_morning_catch_up_reports_the_session_it_missed():
    """Persistent=true fires a missed run at boot; before today's close the
    newest finished session is the previous trading day, not an empty today."""
    import datetime as _dt
    assert hr.default_day(_ct("2026-10-01", "07:00")) == _dt.date(2026, 9, 30)
    assert hr.default_day(_ct("2026-10-05", "07:00")) == _dt.date(2026, 10, 2)  # Monday


def test_a_non_trading_day_reports_the_last_trading_day():
    """A weekend or holiday catch-up run reports the session it missed."""
    import datetime as _dt
    assert hr.default_day(_ct("2026-10-03", "16:10")) == _dt.date(2026, 10, 2)  # Saturday
    assert hr.default_day(_ct("2026-10-04", "09:00")) == _dt.date(2026, 10, 2)  # Sunday


def test_the_default_day_turns_over_at_the_close():
    import datetime as _dt
    assert hr.default_day(_ct(DAY, "14:59")) == _dt.date(2026, 9, 29)
    assert hr.default_day(_ct(DAY, "15:01")) == _dt.date(2026, 9, 30)


# --- main ---------------------------------------------------------------------------
def _seed_db(path, monkeypatch):
    monkeypatch.setattr(hr.gh, "DB_PATH", path)
    conn = hr.gh.connect()
    hr.gh.init_schema(conn)
    items = []
    for sym in ("$SPX", "SPY"):
        for day, seed in (("2026-09-28", 1), ("2026-09-29", 2), (DAY, 3)):
            t0 = hiro.ct_ts(day, "08:30")
            rnd = random.Random(seed)
            for i in range(120):
                items.append((sym, t0 + 60 * i,
                              {"spot": 100.0 + i * 0.01,
                               "impact": rnd.gauss(0, 1e6),
                               "classified_vol": 100.0, "unclassified_vol": 10.0}))
    hr.gh.insert_hiro_rows(conn, items)
    conn.close()


def test_main_writes_a_report_naming_every_symbol(tmp_path, monkeypatch, capsys):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: {**CFG, "symbols": ["$SPX", "SPY", "QQQ"]})
    out = tmp_path / "reports"
    assert hr.main(["--date", DAY, "--force", "--out", str(out)]) == 0
    report = out / DAY / "report.md"
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    for sym in ("$SPX", "SPY", "QQQ"):
        assert f"## {sym}" in text
    assert "no minutes stored" in text.split("## QQQ", 1)[1]
    assert str(report) in capsys.readouterr().out


def test_main_skips_a_non_trading_day_without_force(tmp_path, monkeypatch):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: CFG)
    out = tmp_path / "reports"
    assert hr.main(["--date", "2026-10-03", "--out", str(out)]) == 0   # a Saturday
    assert not out.exists()


def test_main_survives_one_symbols_bad_data(tmp_path, monkeypatch):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: CFG)
    real = hr.gh.load_hiro_day

    def flaky(conn, symbol, d=None):
        if symbol == "$SPX":
            raise ValueError("corrupt minute")
        return real(conn, symbol, d)

    monkeypatch.setattr(hr.gh, "load_hiro_day", flaky)
    out = tmp_path / "reports"
    assert hr.main(["--date", DAY, "--force", "--out", str(out)]) == 0
    text = (out / DAY / "report.md").read_text(encoding="utf-8")
    assert "corrupt minute" in text
    assert "## SPY" in text and "no minutes stored" not in text.split("## SPY", 1)[1]


def test_main_fails_when_every_symbol_errored(tmp_path, monkeypatch):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: CFG)

    def broken(conn, symbol, d=None):
        raise ValueError("corrupt minute")

    monkeypatch.setattr(hr.gh, "load_hiro_day", broken)
    out = tmp_path / "reports"
    assert hr.main(["--date", DAY, "--force", "--out", str(out)]) == 1
    assert (out / DAY / "report.md").is_file()


def test_main_fails_loudly_when_a_trading_day_has_no_minutes_at_all(
        tmp_path, monkeypatch, capsys):
    """Every symbol empty on a trading day is a dead collector, not a quiet day."""
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: {**CFG, "symbols": ["QQQ", "IWM"]})
    out = tmp_path / "reports"
    assert hr.main(["--date", DAY, "--out", str(out)]) == 1
    text = (out / DAY / "report.md").read_text(encoding="utf-8")
    assert "collector" in text.lower()
    assert "collector" in capsys.readouterr().err.lower()


def test_some_empty_symbols_are_not_a_failure(tmp_path, monkeypatch):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: {**CFG, "symbols": ["SPY", "QQQ"]})
    assert hr.main(["--date", DAY, "--out", str(tmp_path / "r")]) == 0


def test_a_malformed_flip_not_before_skips_reversals_but_reports_surges(
        tmp_path, monkeypatch):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: {**CFG, "flip_not_before": "nine"})
    out = tmp_path / "reports"
    assert hr.main(["--date", DAY, "--out", str(out)]) == 0
    text = (out / DAY / "report.md").read_text(encoding="utf-8")
    assert "flip_not_before" in text
    assert "3.0 (live)" in text


def test_k_and_horizons_parse_as_lists():
    a = hr.parse_args(["--k", "2,3.5", "--horizons", "5,30"])
    assert a.k == [2.0, 3.5] and a.horizons == [5, 30]
    b = hr.parse_args([])
    assert b.k is None and b.horizons is None


# --- base rate, direction, ties -----------------------------------------------------
def test_the_base_rate_counts_every_minute_with_the_same_slack_rule():
    spots = [100.0, 101.0, 101.0, 100.0, 99.0, 99.0, 99.0]
    rows = _rows(_quiet(7), spots)
    b = hr.base_counts(rows, [1])
    # moves after each minute: +, 0, -, -, 0, 0, (none: session over)
    assert b[1] == {"up": 1, "down": 2, "flat": 3}
    gap = _rows(_quiet(3), [100.0, 101.0, 102.0]) + _rows(
        _quiet(2), [90.0, 80.0], t0=T0 + 60 * 60)
    assert sum(hr.base_counts(gap, [1])[1].values()) == 3    # no move across the gap


def test_hits_are_per_direction_and_ties_are_counted_apart():
    fires = [{"side": "dealers_buying", "fwd": {5: 0.01}},
             {"side": "dealers_buying", "fwd": {5: 0.0}},
             {"side": "dealers_buying", "fwd": {5: -0.02}},
             {"side": "dealers_selling", "fwd": {5: 0.03}},
             {"side": "dealers_buying", "fwd": {5: None}}]
    t = hr.tally(fires, "dealers_buying", 5)
    assert (t["hits"], t["n"], t["flat"]) == (1, 2, 1)
    s = hr.tally(fires, "dealers_selling", 5)
    assert (s["hits"], s["n"], s["flat"]) == (1, 1, 0)


def test_hits_read_k_of_n_and_a_percentage_only_from_ten():
    assert hr.k_of_n(3, 7) == "3 of 7"
    assert hr.k_of_n(6, 10) == "6 of 10 (60%)"
    assert hr.k_of_n(0, 0) == "—"


def test_the_report_names_hit_rate_and_base_rate():
    text = hr.build_report(DAY, _summaries(), CFG)
    assert "Hit rate" in text and "base rate" in text
    assert "flat" in text


def test_a_sigma_less_symbol_shows_dashes_for_its_fires():
    text = hr.build_report(DAY, _summaries(), CFG)
    spy = text.split("## SPY", 1)[1].split("## QQQ", 1)[0]
    fires = [ln for ln in spy.splitlines() if ln.startswith("| 3.0 (live)")]
    assert fires and fires[0].rstrip().endswith("| — |")


def test_negative_money_and_the_reversal_size_are_unambiguous():
    assert hr._money(-250.6e6) == "-$250.6M"
    rows = _rows(_quiet(30))
    flip = {"type": "hiro_flip", "side": "to_selling", "ts": T0, "spot": 100.0,
            "cum": -250.6e6}
    assert hr.event_row(flip, rows, [5])["size"] == "day net -$250.6M"
    surge = {"type": "hiro_surge", "side": "dealers_buying", "ts": T0,
             "spot": 100.0, "mult": 3.26}
    assert hr.event_row(surge, rows, [5])["size"] == "3.3× normal"


def test_reversals_are_labelled_off_live_when_flips_are_disabled():
    cfg = {**CFG, "flip_enabled": False}
    prior = [_noisy(60, 1), _noisy(60, 2)]
    s = hr.symbol_summary(_noisy(60, 3), prior, cfg, [3.0], [5], hiro.ct_ts(DAY, "09:00"))
    text = hr.build_report(DAY, {"SPY": s}, cfg)
    assert "Reversals (off live)" in text


# --- today's sigma, as live had it ----------------------------------------------------
def test_live_sigmas_equal_baseline_sigma_on_every_prefix():
    rows = _noisy(60, 5)
    got = hr.live_sigmas(rows, CFG)
    for i in range(len(rows)):
        want = hiro.baseline_sigma([], rows[:i + 1], CFG)
        if want is None:
            assert got[i] is None, i
        else:
            assert math.isclose(got[i], want), i


def test_a_today_sourced_day_cannot_fire_before_min_minutes():
    """No look-ahead: the end-of-day size would let an early surge through,
    but live had no size at all before min_minutes rows."""
    imp = _quiet(390)
    for i in range(16, 21):
        imp[i] = 3e6
    rows = _rows(imp)
    s = hr.symbol_summary(rows, [], CFG, [3.0], [5], hiro.ct_ts(DAY, "09:00"))
    assert s["sigma_source"] == "today"
    assert all(f["ts"] >= rows[CFG["min_minutes"] - 1]["ts"] for f in s["surges"])
    final = hr.replay_surges(rows, s["sigma"], CFG, 3.0)
    assert any(f["ts"] < rows[CFG["min_minutes"] - 1]["ts"] for f in final)


# --- the pooled sessions --------------------------------------------------------------
def test_each_prior_day_takes_the_sigma_live_had_that_day():
    hist = [_noisy(60, 11), _noisy(60, 12), _noisy(60, 13)]     # newest first
    cfg = {**CFG, "keep_sessions": 3}
    days = hr.session_summaries(_noisy(60, 10), hist, cfg, [3.0], [5],
                                lambda rows: rows[0]["ts"])
    assert len(days) == 3
    assert days[0]["sigma_source"] == "prior"
    assert math.isclose(days[0]["sigma"], hiro.prior_sigma(hist[:2], cfg))
    assert math.isclose(days[1]["sigma"], hiro.prior_sigma(hist[1:3], cfg))
    assert days[2]["sigma_source"] == "today"            # only one older session
    # ...and live had a prior sigma that day (the older sessions were purged
    # since), so the replay is not faithful and the day stays out of the pool.
    assert days[2]["excluded"] and not days[1]["excluded"] and not days[0]["excluded"]
    p = hr.pool(days)
    assert p["sessions"] == 2 and p["excluded"] == 1


def test_a_young_history_keeps_its_today_sigma_days_in_the_pool():
    """In the first weeks live ALSO used today's minutes, so those days are
    faithful replays and stay in."""
    hist = [_noisy(60, 11), _noisy(60, 12), _noisy(60, 13)]
    cfg = {**CFG, "keep_sessions": 20}
    days = hr.session_summaries(_noisy(60, 10), hist, cfg, [3.0], [5],
                                lambda rows: rows[0]["ts"])
    assert len(days) == 4
    assert [d["sigma_source"] for d in days] == ["prior", "prior", "today", "today"]
    assert not any(d["excluded"] for d in days)
    p = hr.pool(days)
    assert p["sessions"] == 4 and p["excluded"] == 0
    assert p["sources"] == {"prior": 2, "today": 2, None: 0}


def test_the_pooled_base_rate_counts_only_days_where_the_rules_ran():
    ran = {"surges": [], "reversals": [], "sigma": 1.0, "sigma_source": "prior",
           "base": {5: {"up": 3, "down": 1, "flat": 0}}}
    idle = {"surges": [], "reversals": [], "sigma": None, "sigma_source": None,
            "base": {5: {"up": 100, "down": 0, "flat": 0}}}
    assert hr.pool([ran, idle])["base"][5] == {"up": 3, "down": 1, "flat": 0}


def test_chance_by_luck_is_the_exact_one_sided_binomial():
    assert math.isclose(hr.chance_by_luck(5, 5, 0.5), 1 / 32)
    assert math.isclose(hr.chance_by_luck(4, 5, 0.5), 6 / 32)
    # 3 of 6 at 0.4: P(X>=3) = 1 - (0.6^6 + 6*0.4*0.6^5 + 15*0.16*0.6^4)
    want = 1 - (0.6 ** 6 + 6 * 0.4 * 0.6 ** 5 + 15 * 0.16 * 0.6 ** 4)
    assert math.isclose(hr.chance_by_luck(3, 6, 0.4), want)
    assert hr.chance_by_luck(4, 4, 0.5) is None          # fewer than 5 tries
    assert hr.chance_by_luck(5, 5, None) is None


def test_the_report_explains_signed_moves_and_luck():
    text = hr.build_report(DAY, _summaries(), CFG)
    assert "signed" in text and "Chance by luck" in text
    assert "Mean move (signed)" in text and "Mean return" not in text


def test_a_single_session_has_no_pooled_section(tmp_path, monkeypatch):
    monkeypatch.setattr(hr.gh, "DB_PATH", tmp_path / "g.db")
    conn = hr.gh.connect()
    hr.gh.init_schema(conn)
    rnd = random.Random(1)
    hr.gh.insert_hiro_rows(conn, [("SPY", T0 + 60 * i, {
        "spot": 100.0, "impact": rnd.gauss(0, 1e6), "classified_vol": 100.0,
        "unclassified_vol": 10.0}) for i in range(60)])
    conn.close()
    monkeypatch.setattr(hr, "load_cfg", lambda: {**CFG, "symbols": ["SPY"]})
    out = tmp_path / "r"
    assert hr.main(["--date", DAY, "--out", str(out)]) == 0
    assert "### Last" not in (out / DAY / "report.md").read_text(encoding="utf-8")


def test_a_partial_failure_is_summarised_on_stderr_and_still_exits_zero(
        tmp_path, monkeypatch, capsys):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: CFG)
    real = hr.gh.load_hiro_day

    def flaky(conn, symbol, d=None):
        if symbol == "$SPX":
            raise ValueError("corrupt minute")
        return real(conn, symbol, d)

    monkeypatch.setattr(hr.gh, "load_hiro_day", flaky)
    assert hr.main(["--date", DAY, "--out", str(tmp_path / "r")]) == 0
    err = capsys.readouterr().err
    assert "1 of 2 symbols failed" in err


def test_pooling_keeps_surges_and_reversals_apart_and_sums_the_base_rate():
    a = {"surges": [{"side": "dealers_buying", "fwd": {5: 0.01}}],
         "reversals": [{"side": "to_selling", "fwd": {5: -0.01}}],
         "base": {5: {"up": 3, "down": 1, "flat": 0}}, "sigma": 1.0}
    b = {"surges": [{"side": "dealers_buying", "fwd": {5: -0.01}}], "reversals": [],
         "base": {5: {"up": 1, "down": 3, "flat": 2}}, "sigma": 1.0}
    p = hr.pool([a, b])
    assert len(p["surges"]) == 2 and len(p["reversals"]) == 1
    assert p["base"][5] == {"up": 4, "down": 4, "flat": 2}
    assert p["sessions"] == 2


def test_the_report_carries_the_pooled_section(tmp_path, monkeypatch):
    _seed_db(tmp_path / "gex_history.db", monkeypatch)
    monkeypatch.setattr(hr, "load_cfg", lambda: CFG)
    out = tmp_path / "reports"
    assert hr.main(["--date", DAY, "--out", str(out)]) == 0
    text = (out / DAY / "report.md").read_text(encoding="utf-8")
    assert "### Last 3 sessions" in text
    assert "correlated" in text
