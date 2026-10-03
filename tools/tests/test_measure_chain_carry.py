"""The carry-accuracy report's arithmetic, and the guards around the run.

Nothing here reaches Schwab: the pure parts take plain dicts, and the run is
driven with a stand-in client, an injected clock and a sleep that only moves
that clock.
"""
import datetime as dt
import importlib.util
import pathlib
from zoneinfo import ZoneInfo

import pytest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "measure_chain_carry.py"
_spec = importlib.util.spec_from_file_location("measure_chain_carry", _PATH)
mcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcc)

CT = ZoneInfo("America/Chicago")
RTH = dt.datetime(2026, 10, 5, 10, 0, 0, tzinfo=CT)           # a Monday, mid-session
CLOSED = [dt.datetime(2026, 10, 5, 7, 0, tzinfo=CT),          # before the open
          dt.datetime(2026, 10, 5, 15, 30, tzinfo=CT),        # after the close
          dt.datetime(2026, 10, 4, 10, 0, tzinfo=CT)]         # a Sunday


def summary(net, flip, pos, neg):
    return {"net_total": net, "flip": flip, "top_pos_strike": pos, "top_neg_strike": neg}


#############################################
# THE PLAN'S SPECIFICATION
#############################################

def test_identical_summaries_have_no_error():
    row = mcc.compare(summary(1e9, 100.0, 105.0, 95.0), summary(1e9, 100.0, 105.0, 95.0))
    assert row == {"net_rel_err": 0.0, "flip_abs_err": 0.0, "walls_agree": True}


def test_errors_are_relative_to_the_fresh_reading():
    row = mcc.compare(summary(1.0e9, 100.0, 105.0, 95.0),
                      summary(1.1e9, 100.5, 110.0, 95.0))
    assert round(row["net_rel_err"], 6) == 0.1
    assert row["flip_abs_err"] == 0.5 and row["walls_agree"] is False


def test_a_missing_reading_is_reported_as_missing_not_as_zero_error():
    row = mcc.compare(summary(None, None, 105.0, 95.0), summary(1e9, 100.0, 105.0, 95.0))
    assert row["net_rel_err"] is None and row["flip_abs_err"] is None


def test_report_lines_state_the_worst_case_and_the_count():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True},
            {"net_rel_err": 0.04, "flip_abs_err": None, "walls_agree": False}]
    text = mcc.report(rows)
    assert "2 comparisons" in text and "4.0%" in text and "1 of 2" in text


#############################################
# compare / report — the rest of their edges
#############################################

def test_the_keys_compared_are_the_ones_the_engine_writes():
    """``GammaEngine.snapshot_summary`` is the source of both summaries. If it
    renames a key, every comparison would silently read "missing"."""
    import sys
    for p in (mcc.ROOT, mcc.ROOT / "options-scanner"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import gamma_tool as gt
    made = gt.GammaEngine.snapshot_summary(
        {"spot": 100.0, "gex": {95.0: {"call": 0.0, "put": -2.0, "net": -2.0},
                                105.0: {"call": 3.0, "put": 0.0, "net": 3.0}}})
    assert set(mcc.SUMMARY_KEYS) <= set(made)
    assert mcc.compare(made, made) == {"net_rel_err": 0.0,
                                       "flip_abs_err": 0.0 if made["flip"] is not None
                                       else None, "walls_agree": True}


def test_a_missing_reading_on_the_carried_side_is_missing_too():
    row = mcc.compare(summary(1e9, 100.0, 105.0, 95.0), summary(None, None, 105.0, 95.0))
    assert row["net_rel_err"] is None and row["flip_abs_err"] is None
    assert row["walls_agree"] is True


def test_a_fresh_net_of_zero_has_no_relative_error_to_state():
    row = mcc.compare(summary(0.0, 100.0, 105.0, 95.0), summary(5.0, 100.0, 105.0, 95.0))
    assert row["net_rel_err"] is None


@pytest.mark.parametrize("bad", [True, "1e9", float("nan"), float("inf")])
def test_a_reading_that_is_not_a_real_number_is_missing(bad):
    row = mcc.compare(summary(bad, bad, 105.0, 95.0), summary(1e9, 100.0, 105.0, 95.0))
    assert row["net_rel_err"] is None and row["flip_abs_err"] is None


def test_the_error_is_measured_against_the_size_of_a_negative_net():
    row = mcc.compare(summary(-2.0e9, 100.0, 105.0, 95.0),
                      summary(-1.0e9, 99.0, 105.0, 95.0))
    assert row["net_rel_err"] == 0.5 and row["flip_abs_err"] == 1.0


def test_either_wall_moving_is_a_disagreement():
    base = summary(1e9, 100.0, 105.0, 95.0)
    assert mcc.compare(base, summary(1e9, 100.0, 105.0, 90.0))["walls_agree"] is False
    assert mcc.compare(base, summary(1e9, 100.0, 110.0, 95.0))["walls_agree"] is False


def test_the_report_states_medians_and_worst_cases():
    rows = [{"net_rel_err": e, "flip_abs_err": f, "walls_agree": True}
            for e, f in ((0.01, 0.10), (0.02, 0.30), (0.09, 0.20))]
    text = mcc.report(rows)
    assert "3 comparisons" in text
    assert "median 2.0%" in text and "worst 9.0%" in text
    assert "median 0.20" in text and "worst 0.30" in text
    assert "walls differ in 0 of 3" in text


def test_a_report_with_nothing_to_compare_says_so_without_inventing_numbers():
    text = mcc.report([])
    assert "0 comparisons" in text
    assert "%" not in text and "median" not in text


def test_the_report_states_how_often_the_gamma_cap_bound():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True, "capped": 0},
            {"net_rel_err": 0.02, "flip_abs_err": 0.1, "walls_agree": True, "capped": 3},
            {"net_rel_err": 0.03, "flip_abs_err": 0.1, "walls_agree": True, "capped": 2}]
    text = mcc.report(rows)
    assert "gamma cap bound on 5 contract(s), in 2 of 3 comparisons" in text


def test_a_run_where_the_cap_never_bound_says_zero_not_nothing():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True, "capped": 0}]
    assert "gamma cap bound on 0 contract(s), in 0 of 1 comparisons" in mcc.report(rows)


def test_rows_with_no_cap_count_get_no_cap_line():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True}]
    assert "gamma cap" not in mcc.report(rows)


def test_a_row_is_printed_in_words_and_a_missing_reading_as_not_available():
    assert mcc.row_text({"net_rel_err": 0.0338, "flip_abs_err": 0.5,
                         "walls_agree": True, "capped": 2}) == \
        "net 3.4%  flip 0.50  walls agree  capped 2"
    assert mcc.row_text({"net_rel_err": None, "flip_abs_err": None,
                         "walls_agree": False}) == \
        "net n/a  flip n/a  walls DIFFER  capped 0"


#############################################
# THE PLAN: SYMBOLS, MINUTES, CALLS
#############################################

def test_symbols_are_split_stripped_and_deduplicated_in_order():
    assert mcc.parse_symbols(" SOFI, UBER ,,SOFI,$SPX ") == ["SOFI", "UBER", "$SPX"]
    assert mcc.parse_symbols("") == []


def test_the_planned_calls_are_one_chain_per_symbol_per_minute_and_one_quote_a_minute():
    assert mcc.planned_calls(["SOFI", "UBER", "HOOD"], 12) == {
        "symbols": ["SOFI", "UBER", "HOOD"], "minutes": 12,
        "chain_calls": 36, "quote_calls": 12, "total_calls": 48}


#############################################
# THE RUN, WITH A STAND-IN CLIENT
#############################################

def _chain(symbol, spot):
    def side(pc, sign, oi):
        return {"2026-10-09:4": {
            str(k): [{"putCall": pc, "strikePrice": k, "gamma": g,
                      "delta": sign * d, "volatility": 30.0, "openInterest": oi,
                      "totalVolume": 40, "mark": 1.25}]
            for k, g, d in ((95.0, 0.03, 0.7), (100.0, 0.06, 0.5), (105.0, 0.03, 0.3))}}
    return {"symbol": symbol, "underlyingPrice": spot,
            "callExpDateMap": side("CALL", 1, 500), "putExpDateMap": side("PUT", -1, 300)}


class _Resp:
    def __init__(self, data, status=200):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class _Clock:
    """A clock that only ``sleep`` and each chain fetch move."""
    def __init__(self, start=RTH):
        self.now = start
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += dt.timedelta(seconds=seconds)


class _Client:
    class Options:
        class ContractType:
            ALL = "ALL"

    def __init__(self, clock, fail=()):
        self.clock, self.fail = clock, set(fail)
        self.chain_calls, self.quote_calls = [], []

    def get_option_chain(self, symbol, **kw):
        self.chain_calls.append((symbol, kw))
        self.clock.now += dt.timedelta(seconds=1)              # a fetch takes time
        if symbol in self.fail:
            return _Resp(None, status=502)
        minute = len([s for s, _ in self.chain_calls if s == symbol])
        return _Resp(_chain(symbol, 100.0 + 0.5 * minute))     # the price drifts

    def get_quotes(self, symbols, **kw):
        self.quote_calls.append(list(symbols))
        minute = len(self.quote_calls)
        return _Resp({s: {"quote": {"lastPrice": 100.0 + 0.5 * minute}}
                      for s in symbols})


def _run(argv, clock=None, fail=()):
    clock = clock or _Clock()
    made = []

    def factory():
        made.append(_Client(clock, fail=fail))
        return made[-1]

    code = mcc.main(argv, clock=clock, client_factory=factory, sleep=clock.sleep)
    return code, (made[0] if made else None), clock


def test_every_chain_fetch_asks_for_a_real_one_over_the_collectors_window(capsys):
    code, client, _ = _run(["--symbols", "SOFI,UBER", "--minutes", "3"])
    assert code == 0
    assert len(client.chain_calls) == 6
    for symbol, kw in client.chain_calls:
        assert kw["max_age"] == 0                              # never a stored answer
        assert kw["contract_type"] == "ALL"
        assert kw["from_date"] == RTH.date()
        assert kw["to_date"] == RTH.date() + dt.timedelta(days=7)


def test_the_run_makes_exactly_the_calls_the_dry_run_states(capsys):
    plan = mcc.planned_calls(["SOFI", "UBER", "HOOD"], 4)
    code, client, _ = _run(["--symbols", "SOFI,UBER,HOOD", "--minutes", "4"])
    assert code == 0
    assert len(client.chain_calls) == plan["chain_calls"]
    assert len(client.quote_calls) == plan["quote_calls"]
    assert all(q == ["SOFI", "UBER", "HOOD"] for q in client.quote_calls)


def test_each_minute_compares_the_fresh_chain_with_the_one_and_two_minute_old_ones(capsys):
    code, client, clock = _run(["--symbols", "SOFI", "--minutes", "4"])
    out = capsys.readouterr().out
    # Minute 1 has nothing to carry; then 1, 2 and 2 held chains.
    assert "5 comparisons" in out
    assert "gamma cap bound on 0 contract(s), in 0 of 5 comparisons" in out
    ages = sorted(int(float(line.split("age")[1].split("s")[0]))
                  for line in out.splitlines() if " age " in line)
    assert ages == [60, 60, 60, 120, 120]
    assert len(clock.slept) == 3                               # not after the last


def test_a_chain_fetch_that_fails_is_skipped_and_the_run_goes_on(capsys):
    code, client, _ = _run(["--symbols", "SOFI,UBER", "--minutes", "3"], fail={"UBER"})
    captured = capsys.readouterr()
    assert code == 0 and len(client.chain_calls) == 6
    assert "3 comparisons" in captured.out
    rows = [line for line in captured.out.splitlines() if " age " in line]
    assert len(rows) == 3 and all("SOFI" in line for line in rows)
    assert captured.err.count("UBER: no chain") == 3        # said, once a minute
    assert client.quote_calls == [["SOFI", "UBER"]] * 3     # the stated quote calls


def test_a_symbol_with_no_live_quote_is_not_compared(capsys):
    clock = _Clock()
    client = _Client(clock)
    client.get_quotes = lambda symbols, **kw: _Resp(
        {"SOFI": {"quote": {"lastPrice": 0}}})                 # the no-print sentinel
    code = mcc.main(["--symbols", "SOFI", "--minutes", "3"], clock=clock,
                    client_factory=lambda: client, sleep=clock.sleep)
    assert code == 0 and "0 comparisons" in capsys.readouterr().out


def test_the_carry_and_the_cap_count_are_the_collectors_own(capsys, monkeypatch):
    """The tool measures ``chain_carry`` itself, not a copy of it: the OLD chain,
    the live quote, the age between the two fetches, and the fresh fetch's
    clock. The cap count is asked about the very same carry."""
    chain_carry = mcc._engine_modules()[0]
    seen = {"carry": [], "cap": []}
    real_carry, real_cap = chain_carry.carry_chain, chain_carry.capped_gammas

    def carry(chain, spot, *, age_sec, now, max_ratio):
        seen["carry"].append((chain["underlyingPrice"], spot, age_sec, now))
        return real_carry(chain, spot, age_sec=age_sec, now=now, max_ratio=max_ratio)

    def cap(chain, spot, *, age_sec, now, max_ratio):
        seen["cap"].append((chain["underlyingPrice"], spot, age_sec, now))
        return real_cap(chain, spot, age_sec=age_sec, now=now, max_ratio=max_ratio) + 7

    monkeypatch.setattr(chain_carry, "carry_chain", carry)
    monkeypatch.setattr(chain_carry, "capped_gammas", cap)
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "2"])
    assert code == 0
    assert seen["carry"] == [(100.5, 101.0, 60.0, RTH + dt.timedelta(seconds=61))]
    assert seen["cap"] == seen["carry"]
    assert "gamma cap bound on 7 contract(s), in 1 of 1 comparisons" in capsys.readouterr().out


def test_the_error_is_measured_against_the_chain_fetched_that_minute(capsys, monkeypatch):
    """Fresh first, carried second: the fresh reading is the truth the carry is
    held against."""
    chain_carry, gt, _gc, _mc = mcc._engine_modules()

    def net(chain):
        gex, *_ = gt.GammaEngine().calc_all_from_chain(chain, use_volume=False)
        return gt.GammaEngine.snapshot_summary(gex)["net_total"]

    seen = []
    real = mcc.compare
    monkeypatch.setattr(mcc, "compare", lambda fresh, carried: (
        seen.append((fresh["net_total"], carried["net_total"])), real(fresh, carried))[1])
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "2"])
    assert code == 0
    fresh = net(_chain("SOFI", 101.0))
    carried = net(chain_carry.carry_chain(_chain("SOFI", 100.5), 101.0, age_sec=60.0,
                                          now=RTH + dt.timedelta(seconds=61)))
    assert fresh != carried
    assert seen == [(fresh, carried)]


def test_a_chain_older_than_the_collector_would_carry_is_not_compared(capsys):
    """A fetch that failed for a few minutes leaves only an old chain held. The
    collector would never carry one that old, so measuring it says nothing."""
    clock = _Clock()
    client = _Client(clock)
    real = client.get_option_chain
    client.get_option_chain = lambda symbol, **kw: (
        real(symbol, **kw) if len(client.chain_calls) not in (1, 2, 3)
        else (client.chain_calls.append((symbol, kw)), _Resp(None, 502))[1])
    code = mcc.main(["--symbols", "SOFI", "--minutes", "5"], clock=clock,
                    client_factory=lambda: client, sleep=clock.sleep)
    assert code == 0 and len(client.chain_calls) == 5
    # Minute 1 fetched, 2-4 failed, minute 5 is 240 s after the only held chain.
    assert "0 comparisons" in capsys.readouterr().out


#############################################
# --dry-run
#############################################

def test_a_dry_run_prints_the_plan_and_calls_nothing(capsys):
    def boom():
        raise AssertionError("a dry run must not build a client")

    clock = _Clock(start=CLOSED[2])                            # even on a Sunday
    code = mcc.main(["--symbols", "SOFI,UBER,HOOD", "--minutes", "12", "--dry-run"],
                    clock=clock, client_factory=boom, sleep=clock.sleep)
    out = capsys.readouterr().out
    assert code == 0 and clock.slept == []
    assert "SOFI, UBER, HOOD" in out and "12 minutes" in out
    assert "36 chain" in out and "12 quote" in out and "48" in out
    assert "nothing was called" in out


def test_a_dry_run_does_not_claim_a_name(monkeypatch, capsys):
    monkeypatch.delenv("TRADING_CALLER", raising=False)
    mcc.main(["--dry-run"], clock=_Clock(), client_factory=lambda: 1 / 0,
             sleep=lambda s: None)
    import os
    assert "TRADING_CALLER" not in os.environ


#############################################
# THE SESSION GUARD
#############################################

@pytest.mark.parametrize("when", CLOSED)
def test_outside_the_regular_session_the_tool_refuses_and_calls_nothing(when, capsys):
    def boom():
        raise AssertionError("refused runs must not build a client")

    clock = _Clock(start=when)
    code = mcc.main(["--symbols", "SOFI", "--minutes", "3"], clock=clock,
                    client_factory=boom, sleep=clock.sleep)
    captured = capsys.readouterr()
    assert code == 2 and clock.slept == []
    assert "regular session" in captured.err and "--force" in captured.err
    assert "comparisons" not in captured.out


@pytest.mark.parametrize("when", CLOSED)
def test_force_runs_anyway(when, capsys):
    code, client, _ = _run(["--symbols", "SOFI", "--minutes", "2", "--force"],
                           clock=_Clock(start=when))
    assert code == 0 and len(client.chain_calls) == 2
    assert "1 comparisons" in capsys.readouterr().out


def test_a_run_stops_when_the_session_closes_under_it(capsys):
    """Started at 14:58:30 for five minutes: the rounds at 14:58 and 14:59 are
    inside the session, the one at 15:00:30 is not."""
    clock = _Clock(start=dt.datetime(2026, 10, 5, 14, 58, 30, tzinfo=CT))
    code, client, _ = _run(["--symbols", "SOFI", "--minutes", "5"], clock=clock)
    out = capsys.readouterr().out
    assert code == 0 and len(client.chain_calls) == 2
    assert "session closed" in out and "1 comparisons" in out


def test_a_forced_run_does_not_stop_at_the_close(capsys):
    clock = _Clock(start=dt.datetime(2026, 10, 5, 14, 58, 30, tzinfo=CT))
    code, client, _ = _run(["--symbols", "SOFI", "--minutes", "5", "--force"],
                           clock=clock)
    assert code == 0 and len(client.chain_calls) == 5


#############################################
# WHO IS CALLING
#############################################

def _caller_seen(monkeypatch, preset):
    import os
    if preset is None:
        monkeypatch.delenv("TRADING_CALLER", raising=False)
    else:
        monkeypatch.setenv("TRADING_CALLER", preset)
    clock, seen = _Clock(), []

    def factory():
        seen.append(os.environ.get("TRADING_CALLER"))          # as the client is built
        return _Client(clock)

    assert mcc.main(["--symbols", "SOFI", "--minutes", "2"], clock=clock,
                    client_factory=factory, sleep=clock.sleep) == 0
    return seen


@pytest.mark.parametrize("preset", [None, "", "   "])
def test_the_tool_names_itself_when_nobody_has(monkeypatch, capsys, preset):
    assert _caller_seen(monkeypatch, preset) == ["measure_chain_carry"]


def test_a_name_already_set_is_kept(monkeypatch, capsys):
    assert _caller_seen(monkeypatch, "operator_check") == ["operator_check"]


#############################################
# ARGUMENTS THAT WOULD SPEND CALLS FOR NOTHING
#############################################

@pytest.mark.parametrize("argv", [["--minutes", "1"], ["--minutes", "0"],
                                  ["--symbols", " , "]])
def test_a_run_that_could_compare_nothing_is_refused_before_any_call(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        mcc.main(argv, clock=_Clock(), client_factory=lambda: 1 / 0,
                 sleep=lambda s: None)
    assert stop.value.code == 2


#############################################
# THE GAMMA CAP IS A SETTING, AND AN ARGUMENT
#############################################

def _ratios_used(monkeypatch, argv):
    chain_carry = mcc._engine_modules()[0]
    seen = {"carry": [], "cap": []}
    real_carry, real_cap = chain_carry.carry_chain, chain_carry.capped_gammas

    def carry(chain, spot, **kw):
        seen["carry"].append(kw["max_ratio"])
        return real_carry(chain, spot, **kw)

    def cap(chain, spot, **kw):
        seen["cap"].append(kw["max_ratio"])
        return real_cap(chain, spot, **kw)

    monkeypatch.setattr(chain_carry, "carry_chain", carry)
    monkeypatch.setattr(chain_carry, "capped_gammas", cap)
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "2", *argv])
    assert code == 0
    return seen


def test_the_gamma_cap_defaults_to_the_configured_one(monkeypatch, capsys):
    from shared import marketdata_config as mdc
    real = mdc.section
    monkeypatch.setattr(mdc, "section", lambda name: dict(real(name), max_gamma_ratio=6.5))
    assert _ratios_used(monkeypatch, []) == {"carry": [6.5], "cap": [6.5]}
    assert "gamma cap: 6.5 times Schwab's value" in capsys.readouterr().out


def test_as_shipped_the_gamma_cap_is_ten(monkeypatch, capsys):
    assert _ratios_used(monkeypatch, []) == {"carry": [10.0], "cap": [10.0]}


def test_the_gamma_cap_argument_wins_over_the_setting(monkeypatch, capsys):
    assert _ratios_used(monkeypatch, ["--max-gamma-ratio", "3"]) == {
        "carry": [3.0], "cap": [3.0]}
    assert "gamma cap: 3 times Schwab's value" in capsys.readouterr().out


def test_a_dry_run_states_the_gamma_cap_it_would_use(capsys):
    mcc.main(["--dry-run", "--max-gamma-ratio", "4"], clock=_Clock(),
             client_factory=lambda: 1 / 0, sleep=lambda s: None)
    assert "gamma cap: 4 times Schwab's value" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["0.5", "0", "-2", "nan", "inf"])
def test_a_gamma_cap_under_one_is_refused_before_any_call(bad, capsys):
    with pytest.raises(SystemExit) as stop:
        mcc.main(["--max-gamma-ratio", bad], clock=_Clock(),
                 client_factory=lambda: 1 / 0, sleep=lambda s: None)
    assert stop.value.code == 2


#############################################
# EXPIRATION DAY IS REPORTED ON ITS OWN
#############################################
# On the day the nearest expiration settles a fast move understates carried
# gamma (measured in review: a 1.5% move with two hours left gave 0.041 carried
# against 0.867 true at the new at-the-money strike). Mixed into one median,
# those comparisons would hide behind the quiet ones.

def _row(net, expiration_day, **kw):
    return {"net_rel_err": net, "flip_abs_err": 0.1, "walls_agree": True,
            "expiration_day": expiration_day, **kw}


def test_the_report_states_expiration_day_comparisons_apart_from_the_rest():
    rows = [_row(0.30, True), _row(0.10, True), _row(0.50, True),
            _row(0.01, False), _row(0.03, False)]
    text = mcc.report(rows)
    assert ("net gamma error on expiration day: 3 comparisons, median 30.0%, "
            "worst 50.0%; other days: 2 comparisons, median 3.0%, worst 3.0%") in text
    assert text.count("expiration day") == 1                 # one extra line


def test_a_group_with_nothing_in_it_says_so():
    text = mcc.report([_row(0.02, False), _row(0.04, False)])
    assert ("net gamma error on expiration day: 0 comparisons; "
            "other days: 2 comparisons, median 4.0%, worst 4.0%") in text
    text = mcc.report([_row(0.2, True)])
    assert ("net gamma error on expiration day: 1 comparisons, median 20.0%, "
            "worst 20.0%; other days: 0 comparisons") in text


def test_a_group_whose_comparisons_have_no_reading_is_counted_without_numbers():
    text = mcc.report([_row(None, True), _row(0.02, False)])
    assert ("net gamma error on expiration day: 1 comparisons, no reading; "
            "other days: 1 comparisons, median 2.0%, worst 2.0%") in text


def test_rows_that_do_not_say_get_no_expiration_day_line():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True}]
    assert "expiration day" not in mcc.report(rows)


def _expiring(symbol, spot):
    """The same chain, but its only expiration is the engine's 0-DTE one."""
    chain = _chain(symbol, spot)
    for m in ("callExpDateMap", "putExpDateMap"):
        chain[m] = {f"{RTH.date().isoformat()}:0": next(iter(chain[m].values()))}
    return chain


def _rows_of(monkeypatch, chain_of):
    rows = []
    real = mcc.report
    monkeypatch.setattr(mcc, "report", lambda r: (rows.extend(r), real(r))[1])
    clock = _Clock()
    client = _Client(clock)
    calls = []

    def get_chain(symbol, **kw):
        calls.append(symbol)
        clock.now += dt.timedelta(seconds=1)
        return _Resp(chain_of(symbol, 100.0 + 0.5 * len(calls)))

    client.get_option_chain = get_chain
    assert mcc.main(["--symbols", "SOFI", "--minutes", "2"], clock=clock,
                    client_factory=lambda: client, sleep=clock.sleep) == 0
    return rows


def test_a_comparison_is_marked_by_what_the_engine_read(monkeypatch, capsys):
    assert [r["expiration_day"] for r in _rows_of(monkeypatch, _expiring)] == [True]
    assert "on expiration day: 1 comparisons" in capsys.readouterr().out


def test_a_chain_four_days_out_is_not_an_expiration_day(monkeypatch, capsys):
    assert [r["expiration_day"] for r in _rows_of(monkeypatch, _chain)] == [False]
    assert "on expiration day: 0 comparisons" in capsys.readouterr().out
