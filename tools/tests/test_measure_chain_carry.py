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

# The one expiration ``_chain`` lists: four days after the run's own day. Derived
# from RTH and never written out, because the engine reads a key dated its own
# "today" as 0-DTE whatever the ``:4`` after it says.
FOUR_OUT = f"{(RTH.date() + dt.timedelta(days=4)).isoformat()}:4"


def _chain(symbol, spot):
    def side(pc, sign, oi):
        return {FOUR_OUT: {
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
    code, client, _ = _run(["--symbols", "SOFI,UBER", "--minutes", "3",
                            "--interval", "2"])
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
    code, client, _ = _run(["--symbols", "SOFI,UBER", "--minutes", "3",
                            "--interval", "2"], fail={"UBER"})
    captured = capsys.readouterr()
    assert code == 0 and len(client.chain_calls) == 6
    assert "2 comparisons" in captured.out
    rows = [line for line in captured.out.splitlines() if " age " in line]
    assert len(rows) == 2 and all("SOFI" in line for line in rows)
    assert captured.err.count("UBER: no chain") == 3        # said, once a minute
    assert client.quote_calls == [["SOFI", "UBER"]] * 3     # the stated quote calls


def test_a_symbol_with_no_live_quote_is_not_compared(capsys):
    clock = _Clock()
    client = _Client(clock)
    client.get_quotes = lambda symbols, **kw: _Resp(
        {"SOFI": {"quote": {"lastPrice": 0}}})                 # the no-print sentinel
    code = mcc.main(["--symbols", "SOFI", "--minutes", "3", "--interval", "2"],
                    clock=clock, client_factory=lambda: client, sleep=clock.sleep)
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
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "3", "--interval", "2"])
    assert code == 0
    assert seen["carry"] == [(100.5, 101.0, 60.0, RTH + dt.timedelta(seconds=61)),
                             (101.0, 101.5, 60.0, RTH + dt.timedelta(seconds=121))]
    assert seen["cap"] == seen["carry"]
    assert ("gamma cap bound on 14 contract(s), in 2 of 2 comparisons"
            in capsys.readouterr().out)


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
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "3", "--interval", "2"])
    assert code == 0
    fresh = net(_chain("SOFI", 101.0))
    carried = net(chain_carry.carry_chain(_chain("SOFI", 100.5), 101.0, age_sec=60.0,
                                          now=RTH + dt.timedelta(seconds=61)))
    assert fresh != carried
    assert seen[0] == (fresh, carried)
    assert len(seen) == 2 and seen[1][0] == net(_chain("SOFI", 101.5))


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
    code = mcc.main(["--symbols", "SOFI", "--minutes", "3", "--interval", "2"],
                    clock=clock, client_factory=boom, sleep=clock.sleep)
    captured = capsys.readouterr()
    assert code == 2 and clock.slept == []
    assert "regular session" in captured.err and "--force" in captured.err
    assert "comparisons" not in captured.out


@pytest.mark.parametrize("when", CLOSED)
def test_force_runs_anyway(when, capsys):
    code, client, _ = _run(["--symbols", "SOFI", "--minutes", "3", "--interval", "2",
                            "--force"], clock=_Clock(start=when))
    assert code == 0 and len(client.chain_calls) == 3
    assert "2 comparisons" in capsys.readouterr().out


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

    assert mcc.main(["--symbols", "SOFI", "--minutes", "3", "--interval", "2"],
                    clock=clock, client_factory=factory, sleep=clock.sleep) == 0
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
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "3", "--interval", "2",
                       *argv])
    assert code == 0
    return seen


def test_the_gamma_cap_defaults_to_the_configured_one(monkeypatch, capsys):
    from shared import marketdata_config as mdc
    real = mdc.section
    monkeypatch.setattr(mdc, "section", lambda name: dict(real(name), max_gamma_ratio=6.5))
    assert _ratios_used(monkeypatch, []) == {"carry": [6.5, 6.5], "cap": [6.5, 6.5]}
    assert "gamma cap: 6.5 times Schwab's value" in capsys.readouterr().out


def test_as_shipped_the_gamma_cap_is_ten(monkeypatch, capsys):
    assert _ratios_used(monkeypatch, []) == {"carry": [10.0, 10.0],
                                             "cap": [10.0, 10.0]}


def test_the_gamma_cap_argument_wins_over_the_setting(monkeypatch, capsys):
    assert _ratios_used(monkeypatch, ["--max-gamma-ratio", "3"]) == {
        "carry": [3.0, 3.0], "cap": [3.0, 3.0]}
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
    """The same chain, but its only expiration is the run's own day: 0-DTE."""
    chain = _chain(symbol, spot)
    for m in ("callExpDateMap", "putExpDateMap"):
        chain[m] = {f"{RTH.date().isoformat()}:0": next(iter(chain[m].values()))}
    return chain


def _engine_on(monkeypatch, clock):
    """Pin the engine's wall clock to the run's own.

    ``GammaEngine`` takes no clock: its "today" is ``datetime.now``, and the
    tool hands its own clock to the carry only. In a real run the two are one
    clock. Left alone here they are not, and the engine would judge a chain's
    dates against the day the suite happens to run."""
    gt = mcc._engine_modules()[1]

    class _Pinned(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock() if tz is None else clock().astimezone(tz)

    monkeypatch.setattr(gt, "datetime", _Pinned)


def _rows_of(monkeypatch, chain_of):
    rows = []
    real = mcc.report
    monkeypatch.setattr(mcc, "report",
                        lambda r, **kw: (rows.extend(r), real(r, **kw))[1])
    clock = _Clock()
    _engine_on(monkeypatch, clock)
    client = _Client(clock)
    calls = []

    def get_chain(symbol, **kw):
        calls.append(symbol)
        clock.now += dt.timedelta(seconds=1)
        return _Resp(chain_of(symbol, 100.0 + 0.5 * len(calls)))

    client.get_option_chain = get_chain
    assert mcc.main(["--symbols", "SOFI", "--minutes", "3", "--interval", "2"],
                    clock=clock, client_factory=lambda: client, sleep=clock.sleep) == 0
    return rows


def test_a_comparison_is_marked_by_what_the_engine_read(monkeypatch, capsys):
    assert [r["expiration_day"] for r in _rows_of(monkeypatch, _expiring)] == [True, True]
    assert "on expiration day: 2 comparisons" in capsys.readouterr().out


def test_a_chain_four_days_out_is_not_an_expiration_day(monkeypatch, capsys):
    assert [r["expiration_day"] for r in _rows_of(monkeypatch, _chain)] == [False, False]
    assert "on expiration day: 0 comparisons" in capsys.readouterr().out


#############################################
# THE INTERVAL BEING CONSIDERED
#############################################
# At an interval of N the collector carries a chain for up to N - 1 minutes.
# Holding two chains could only ever speak for an interval of 3, while the
# settings say "use 3 or 5".

def _config(monkeypatch, **collection):
    from shared import marketdata_config as mdc
    real = mdc.section
    monkeypatch.setattr(mdc, "section", lambda name: (
        dict(real(name), **collection) if name == "collection" else real(name)))


def _dry(argv, capsys):
    code = mcc.main([*argv, "--dry-run"], clock=_Clock(),
                    client_factory=lambda: 1 / 0, sleep=lambda s: None)
    assert code == 0
    return capsys.readouterr().out


def test_the_oldest_chain_compared_is_one_minute_short_of_the_interval_plus_the_slack():
    assert mcc.max_carry_age(3, 30) == 150
    assert mcc.max_carry_age(5, 30) == 270
    assert mcc.max_carry_age(2, 0) == 60
    assert mcc.max_carry_age(4, 12.5) == 192.5


@pytest.mark.parametrize("configured, used", [(1, 3), (0, 3), (2, 2), (3, 3), (4, 4),
                                              (5, 5), (7, 5)])
def test_the_interval_defaults_to_the_configured_one_when_it_is_above_one(
        monkeypatch, capsys, configured, used):
    """Above 5 the collector reads 5, so the tool does too."""
    _config(monkeypatch, tail_interval_min=configured)
    assert mcc.configured_interval() == used
    assert f"interval: {used} minutes" in _dry([], capsys)


def test_as_shipped_the_interval_is_three(capsys):
    out = _dry([], capsys)
    assert "interval: 3 minutes (a carried chain is up to 2 minutes old, " \
           "at most 150 seconds)" in out


def test_the_interval_argument_wins_over_the_setting(monkeypatch, capsys):
    _config(monkeypatch, tail_interval_min=3)
    out = _dry(["--interval", "5"], capsys)
    assert "interval: 5 minutes (a carried chain is up to 4 minutes old, " \
           "at most 270 seconds)" in out


def test_the_age_limit_uses_the_configured_slack(monkeypatch, capsys):
    _config(monkeypatch, carry_slack_sec=10)
    assert "at most 130 seconds" in _dry(["--interval", "3"], capsys)


@pytest.mark.parametrize("bad", ["1", "0", "6", "10", "-3", "x", "2.5"])
def test_an_interval_outside_two_to_five_is_refused_before_any_call(bad, capsys):
    with pytest.raises(SystemExit) as stop:
        mcc.main(["--interval", bad, "--minutes", "12"], clock=_Clock(),
                 client_factory=lambda: 1 / 0, sleep=lambda s: None)
    assert stop.value.code == 2


@pytest.mark.parametrize("interval", ["2", "3", "4", "5"])
def test_every_interval_from_two_to_five_is_accepted(interval, capsys):
    assert f"interval: {interval} minutes" in _dry(["--interval", interval], capsys)


@pytest.mark.parametrize("interval, minutes", [("2", "2"), ("3", "3"), ("3", "2"),
                                               ("5", "5"), ("5", "4")])
def test_a_run_no_longer_than_the_interval_is_refused_before_any_call(
        interval, minutes, capsys):
    """It would make one comparison at the oldest age, or none."""
    with pytest.raises(SystemExit) as stop:
        mcc.main(["--interval", interval, "--minutes", minutes], clock=_Clock(),
                 client_factory=lambda: 1 / 0, sleep=lambda s: None)
    assert stop.value.code == 2
    assert "--minutes" in capsys.readouterr().err


@pytest.mark.parametrize("interval, minutes", [("2", "3"), ("3", "4"), ("5", "6")])
def test_a_run_one_minute_longer_than_the_interval_is_accepted(interval, minutes, capsys):
    _dry(["--interval", interval, "--minutes", minutes], capsys)


def test_the_dry_run_call_count_does_not_depend_on_the_interval(capsys):
    """One chain per symbol per minute and one quote a minute, whatever is held."""
    for interval in ("2", "5"):
        out = _dry(["--symbols", "SOFI,UBER,HOOD", "--minutes", "12",
                    "--interval", interval], capsys)
        assert "Schwab calls: 36 chain + 12 quote = 48" in out
        assert f"interval: {interval} minutes" in out


def _ages(out):
    return sorted(int(float(line.split("age")[1].split("s")[0]))
                  for line in out.splitlines() if " age " in line)


def test_an_interval_of_five_compares_chains_up_to_four_minutes_old(capsys):
    code, client, _ = _run(["--symbols", "SOFI", "--minutes", "7", "--interval", "5"])
    out = capsys.readouterr().out
    assert code == 0 and len(client.chain_calls) == 7
    # Minute 2 has one chain to carry, minute 3 two, ... from minute 5 on, four.
    assert _ages(out) == [60] * 6 + [120] * 5 + [180] * 4 + [240] * 3
    assert "18 comparisons" in out


def test_an_interval_of_two_compares_only_the_last_minutes_chain(capsys):
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "4", "--interval", "2"])
    out = capsys.readouterr().out
    assert code == 0 and _ages(out) == [60, 60, 60]


def test_a_generous_slack_does_not_let_in_a_chain_a_whole_interval_old(
        monkeypatch, capsys):
    """Only ``interval - 1`` chains are held. With a slack of 60 the age limit
    alone would admit the two-minute-old chain at an interval of 2."""
    _config(monkeypatch, carry_slack_sec=60)
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "4", "--interval", "2"])
    assert code == 0 and _ages(capsys.readouterr().out) == [60, 60, 60]


def _one_fetch_then_failures(minutes, interval, capsys, monkeypatch=None, slack=None):
    """Minute 1 fetches, every minute up to the last fails, the last fetches."""
    if slack is not None:
        _config(monkeypatch, carry_slack_sec=slack)
    clock = _Clock()
    client = _Client(clock)
    real = client.get_option_chain
    client.get_option_chain = lambda symbol, **kw: (
        real(symbol, **kw) if len(client.chain_calls) in (0, minutes - 1)
        else (client.chain_calls.append((symbol, kw)), _Resp(None, 502))[1])
    code = mcc.main(["--symbols", "SOFI", "--minutes", str(minutes),
                     "--interval", str(interval)], clock=clock,
                    client_factory=lambda: client, sleep=clock.sleep)
    assert code == 0
    return _ages(capsys.readouterr().out)


def test_a_chain_held_past_the_intervals_age_limit_is_not_compared(capsys, monkeypatch):
    # Interval 5: at most 4 x 60 + 30 = 270 s. The only held chain is 300 s old.
    assert _one_fetch_then_failures(6, 5, capsys) == []
    # A slack of 60 makes the limit 300 s: that same chain is now compared.
    assert _one_fetch_then_failures(6, 5, capsys, monkeypatch, slack=60) == [300]


def test_the_run_states_the_interval_in_its_plan_and_its_report(capsys):
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "5", "--interval", "4"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("interval: 4 minutes") == 1                 # the plan
    report = out[out.index("interval considered"):]
    assert report.splitlines()[0] == "interval considered: 4 minutes"
    assert "comparisons" in report.splitlines()[1]               # the report follows


#############################################
# ERRORS BY HOW OLD THE CARRIED CHAIN WAS
#############################################

def _aged(net, age_min):
    return {"net_rel_err": net, "flip_abs_err": 0.1, "walls_agree": True,
            "age_min": age_min}


def test_the_report_groups_errors_by_carried_age_in_whole_minutes():
    rows = [_aged(0.01, 1), _aged(0.03, 1), _aged(0.02, 1),
            _aged(0.05, 2), _aged(0.09, 2), _aged(0.20, 4)]
    lines = mcc.report(rows, interval=5).splitlines()
    assert "carried 1 minute old: 3 comparisons, median 2.0%, worst 3.0%" in lines
    assert "carried 2 minutes old: 2 comparisons, median 9.0%, worst 9.0%" in lines
    assert "carried 4 minutes old: 1 comparisons, median 20.0%, worst 20.0%" in lines
    assert not [x for x in lines if x.startswith("carried 3 minute")]
    ages = [x for x in lines if x.startswith("carried ")]
    assert [x.split()[1] for x in ages] == ["1", "2", "4"]       # youngest first


def test_an_age_group_with_no_reading_is_counted_without_numbers():
    lines = mcc.report([_aged(None, 2), _aged(0.04, 1)]).splitlines()
    assert "carried 2 minutes old: 1 comparisons, no reading" in lines


def test_rows_with_no_age_get_no_age_lines():
    rows = [{"net_rel_err": 0.01, "flip_abs_err": 0.1, "walls_agree": True}]
    assert "carried" not in mcc.report(rows)


def test_the_report_states_the_interval_when_it_is_given_one():
    rows = [_aged(0.01, 1)]
    assert mcc.report(rows, interval=3).splitlines()[0] == "interval considered: 3 minutes"
    assert "interval considered" not in mcc.report(rows)


def test_a_runs_rows_carry_their_age_in_whole_minutes(monkeypatch, capsys):
    rows = []
    real = mcc.report
    monkeypatch.setattr(mcc, "report", lambda r, **kw: (rows.extend(r), real(r, **kw))[1])
    code, _, _ = _run(["--symbols", "SOFI", "--minutes", "4", "--interval", "3"])
    assert code == 0
    assert sorted(r["age_min"] for r in rows) == [1, 1, 1, 2, 2]
    out = capsys.readouterr().out
    assert "carried 1 minute old: 3 comparisons" in out
    assert "carried 2 minutes old: 2 comparisons" in out
    assert "interval considered: 3 minutes" in out
