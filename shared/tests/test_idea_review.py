"""The weekly idea review's numbers: ratings, review day, grouped facts."""
from datetime import date, timedelta

import pytest

from shared import idea_review as ir
from shared import market_calendar as mc

CFG = {"clean_mae_pct": 25.0, "min_n": 3}
# Fri 2026-10-16; its ISO week is Mon 10-12 .. Sun 10-18.
TODAY = date(2026, 10, 16)


def row(**kw):
    base = {
        "source": "trade_idea", "status": "CLOSED", "exit_reason": "TARGET",
        "realized_pnl": 100.0, "unrealized_pnl": None, "mae_pnl": -10.0,
        "quantity": 1, "max_loss_per": 5.0, "idea_id": "i1",
        "idea_posted": "2026-10-13T09:15:00-05:00", "idea_grade": "Strong",
        "idea_score": 75, "strategy": "Put Credit Spread",
        "trade_type": "SWING", "symbol": "SPY",
        "entry_time": "2026-10-13T09:16:00",
        "exit_time": "2026-10-14T12:15:00-05:00",
    }
    base.update(kw)
    return base


def by_id(table, fid):
    return next(f for f in table if f["id"] == fid)


# ---- rate ---------------------------------------------------------------

@pytest.mark.parametrize("kw, want", [
    # max loss 500 per contract: -50 is -10%, -200 is -40%
    ({"exit_reason": "TARGET", "mae_pnl": -50.0}, "clean win"),
    ({"exit_reason": "TARGET", "mae_pnl": -200.0}, "win after drawdown"),
    ({"exit_reason": "STOP", "realized_pnl": -300.0}, "stopped"),
    ({"exit_reason": "EXPIRED", "status": "EXPIRED",
      "realized_pnl": 40.0}, "expired win"),
    ({"exit_reason": "EXPIRED", "status": "EXPIRED",
      "realized_pnl": -40.0}, "expired loss"),
    ({"exit_reason": "MANUAL", "realized_pnl": 10.0}, "closed by hand"),
])
def test_rate_each_outcome(kw, want):
    assert ir.rate(row(**kw), 25.0) == want


def test_rate_a_zero_expiry_is_a_loss_and_the_clean_line_is_strict():
    assert ir.rate(row(exit_reason="EXPIRED", realized_pnl=0.0), 25.0) \
        == "expired loss"
    # exactly -25% is not better than -25%
    assert ir.rate(row(mae_pnl=-125.0), 25.0) == "win after drawdown"


def test_a_target_with_no_drawdown_percentage_is_clean_only_if_it_never_fell():
    # no worst point recorded at all: not called clean
    assert ir.rate(row(mae_pnl=None), 25.0) == "win after drawdown"
    assert ir.rate(row(mae_pnl=None, max_loss_per=None), 25.0) \
        == "win after drawdown"
    # it fell, but with no max loss the depth cannot be judged: not clean
    assert ir.rate(row(max_loss_per=0), 25.0) == "win after drawdown"
    assert ir.rate(row(max_loss_per=None), 25.0) == "win after drawdown"


@pytest.mark.parametrize("max_loss", [None, 0, 0.0, 5.0])
def test_a_target_that_never_went_below_zero_is_a_clean_win(max_loss):
    """Marked and never negative (the booked 0.0 worst point kept): no drawdown
    happened, so a missing max loss blocks only the percentage, not the rating."""
    assert ir.rate(row(mae_pnl=0.0, max_loss_per=max_loss), 25.0) == "clean win"


# ---- calendar -----------------------------------------------------------

def test_week_bounds_is_monday_to_sunday():
    for d in (date(2026, 10, 12), date(2026, 10, 14), date(2026, 10, 18)):
        assert ir.week_bounds(d) == (date(2026, 10, 12), date(2026, 10, 18))


def test_is_review_day_friday_and_holiday_friday():
    friday = date(2026, 10, 16)
    assert friday.weekday() == 4 and mc.is_trading_day(friday)
    assert ir.is_review_day(friday)
    assert not ir.is_review_day(friday - timedelta(days=1))
    # a full-day Friday closure, from the calendar's own table
    holiday = next(d for y in (2026, 2027, 2028)
                   for d in sorted(mc.nyse_holidays(y)) if d.weekday() == 4)
    assert not ir.is_review_day(holiday)
    assert ir.is_review_day(holiday - timedelta(days=1))
    # a weekend never is
    assert not ir.is_review_day(friday + timedelta(days=1))
    assert not ir.is_review_day(friday + timedelta(days=2))


# ---- facts --------------------------------------------------------------

def test_facts_groups_and_thin_flag():
    trades = [
        row(idea_id="a", idea_grade="Strong", realized_pnl=100.0,
            mae_pnl=-50.0),
        row(idea_id="b", idea_grade="Strong", exit_reason="STOP",
            realized_pnl=-300.0, mae_pnl=-500.0),
        row(idea_id="c", idea_grade="Strong", exit_reason="MANUAL",
            realized_pnl=50.0, mae_pnl=-100.0),
        row(idea_id="d", idea_grade="Fair", realized_pnl=20.0,
            idea_score=None, trade_type="0-DTE"),
    ]
    f = ir.facts(trades, TODAY, CFG)
    strong = by_id(f["week"]["grade"], "week.grade.Strong")
    assert strong["n"] == 3
    assert (strong["targets"], strong["stops"], strong["expired"],
            strong["manual"]) == (1, 1, 0, 1)
    assert strong["total_pnl"] == pytest.approx(-150.0)
    assert strong["avg_pnl"] == pytest.approx(-50.0)
    assert strong["win_rate"] == pytest.approx(200 / 3)
    # -10%, -100%, -20% of a 500 max loss
    assert strong["avg_mae_pct"] == pytest.approx((-10 - 100 - 20) / 3)
    assert strong["avg_held_h"] == pytest.approx(27.0)
    assert strong["thin"] is False
    fair = by_id(f["week"]["grade"], "week.grade.Fair")
    assert fair["n"] == 1 and fair["thin"] is True
    # score bands, including the unscored row
    ids = {x["id"] for x in f["week"]["score_band"]}
    assert ids == {"week.score_band.70-79", "week.score_band.no score"}
    assert by_id(f["week"]["trade_type"], "week.trade_type.0-DTE")["n"] == 1
    assert by_id(f["week"]["symbol"], "week.symbol.SPY")["n"] == 4
    assert by_id(f["week"]["strategy"],
                 "week.strategy.Put Credit Spread")["n"] == 4
    assert by_id(f["week"]["post_hour"], "week.post_hour.9")["n"] == 4
    assert f["week"]["total"]["id"] == "week.total"
    assert f["week"]["total"]["n"] == 4
    assert f["all_time"]["total"]["id"] == "all_time.total"
    assert by_id(f["all_time"]["grade"], "all_time.grade.Strong")["n"] == 3


def test_structures_carry_their_display_names():
    """Groups stay keyed by the structure key (the fact ids Claude cites); each
    line carries the idea card's name and ``structure_names`` maps key to name,
    a key with no stored name keeping its own text."""
    f = ir.facts([row(idea_id="a", strategy="BULL_CALL", idea_label="Bull Call Spread"),
                  row(idea_id="b", strategy="BULL_CALL", idea_label=None),
                  row(idea_id="c", strategy="PCS"),
                  row(idea_id="o", strategy="LONG_CALL", idea_label="Long Call",
                      status="OPEN", exit_reason=None, exit_time=None)],
                 TODAY, CFG)
    assert "week.strategy.BULL_CALL" in ir.fact_ids(f)
    assert f["structure_names"] == {"BULL_CALL": "Bull Call Spread", "PCS": "PCS",
                                    "LONG_CALL": "Long Call"}
    assert [x["structure"] for x in f["closed_week"]] == [
        "Bull Call Spread", "BULL_CALL", "PCS"]
    assert [x["structure"] for x in f["open"]] == ["Long Call"]


@pytest.mark.parametrize("score, band", [
    (59, "<60"), (60, "60-69"), (69.9, "60-69"), (70, "70-79"),
    (79, "70-79"), (80, "80+"), (95, "80+"), (None, "no score"),
])
def test_score_bands(score, band):
    f = ir.facts([row(idea_score=score)], TODAY, CFG)
    assert [x["id"] for x in f["all_time"]["score_band"]] == [
        f"all_time.score_band.{band}"]


def test_post_hour_is_central_whatever_the_stored_offset():
    # 14:30 UTC in October is 09:30 Central; a naive stamp is Central already
    f = ir.facts([row(idea_posted="2026-10-13T14:30:00+00:00"),
                  row(idea_posted="2026-10-13T10:05:00")], TODAY, CFG)
    hours = {x["id"]: x["n"] for x in f["week"]["post_hour"]}
    assert hours == {"week.post_hour.9": 1, "week.post_hour.10": 1}


def test_week_membership_by_exit_date():
    inside = row(idea_id="in", exit_time="2026-10-12T08:31:00-05:00")
    # Sunday 23:30 Central is already Monday in UTC: still last week
    before = row(idea_id="out1", exit_time="2026-10-12T04:30:00+00:00")
    after = row(idea_id="out2", exit_time="2026-10-19T09:00:00")
    f = ir.facts([inside, before, after], TODAY, CFG)
    assert f["week"]["total"]["n"] == 1
    assert [t["idea_id"] for t in f["closed_week"]] == ["in"]
    assert f["all_time"]["total"]["n"] == 3


def test_closed_week_trades_carry_their_rating():
    f = ir.facts([row(mae_pnl=-50.0)], TODAY, CFG)
    (t,) = f["closed_week"]
    assert t["rating"] == "clean win"
    assert t["mae_pct"] == pytest.approx(-10.0)
    assert t["realized_pnl"] == 100.0


def test_closed_week_trades_carry_entry_and_exit_dollars():
    """The weekly report's closed-trade table prints them, so they are facts
    the verifier knows too."""
    f = ir.facts([row(entry_credit_total=-212.5, exit_debit_total=310.0)],
                 TODAY, CFG)
    (t,) = f["closed_week"]
    assert (t["entry"], t["exit"]) == (-212.5, 310.0)
    assert {"-212.50", "212.50", "310.00"} <= ir.fact_numbers(f)
    (bare,) = ir.facts([row()], TODAY, CFG)["closed_week"]
    assert bare["entry"] is None and bare["exit"] is None


def test_open_trades_listed_not_graded():
    o = row(idea_id="o1", status="OPEN", exit_reason=None, exit_time=None,
            realized_pnl=None, unrealized_pnl=-40.0, mae_pnl=-100.0,
            idea_posted="2026-10-12T10:00:00")
    f = ir.facts([o], TODAY, CFG)
    assert f["week"]["total"]["n"] == 0 and f["all_time"]["total"]["n"] == 0
    assert f["week"]["grade"] == [] and f["closed_week"] == []
    (op,) = f["open"]
    assert op["idea_id"] == "o1" and op["symbol"] == "SPY"
    assert op["unrealized_pnl"] == -40.0
    assert op["mae_pct"] == pytest.approx(-20.0)
    assert op["days_open"] == 4
    assert "rating" not in op


def test_other_sources_are_ignored():
    f = ir.facts([row(source="scanner"), row(source=None),
                  row(source="trade_idea", idea_id="x")], TODAY, CFG)
    assert f["all_time"]["total"]["n"] == 1


def test_repeats_counted():
    trades = [row(idea_id="r"), row(idea_id="r"), row(idea_id="r"),
              row(idea_id="s"), row(idea_id="s", status="OPEN",
                                    exit_time=None, exit_reason=None),
              row(idea_id="solo")]
    f = ir.facts(trades, TODAY, CFG)
    assert f["repeats"] == [{"idea_id": "r", "count": 3},
                            {"idea_id": "s", "count": 2}]


def test_repeats_only_count_this_weeks_bookings():
    old = row(idea_id="r", idea_posted="2026-09-01T09:15:00",
              exit_time="2026-09-02T09:15:00")
    f = ir.facts([old, row(idea_id="r")], TODAY, CFG)
    assert f["repeats"] == []


def test_empty_book_gives_empty_facts_not_error():
    f = ir.facts([], TODAY, CFG)
    assert f["open"] == [] and f["closed_week"] == [] and f["repeats"] == []
    for scope in ("week", "all_time"):
        assert f[scope]["total"]["n"] == 0
        assert f[scope]["total"]["win_rate"] is None
        assert f[scope]["total"]["thin"] is True
        for dim in ("grade", "strategy", "trade_type", "score_band",
                    "post_hour", "symbol"):
            assert f[scope][dim] == []
    assert ir.fact_ids(f) == {"week.total", "all_time.total"}
    assert ir.fact_numbers(f) >= {"0"}


# ---- ids and numbers ----------------------------------------------------

def test_fact_ids_are_unique_and_stable():
    trades = [row(idea_id="a"), row(idea_id="b", idea_grade="Fair"),
              row(idea_id="c", exit_time="2026-09-01T10:00:00")]
    f = ir.facts(trades, TODAY, CFG)
    every = [x["id"] for scope in ("week", "all_time")
             for x in [f[scope]["total"]] + [
                 g for dim in ("grade", "strategy", "trade_type",
                               "score_band", "post_hour", "symbol")
                 for g in f[scope][dim]]]
    assert len(every) == len(set(every))
    assert set(every) == ir.fact_ids(f)
    assert {"week.grade.Strong", "all_time.grade.Strong",
            "week.grade.Fair", "week.total",
            "all_time.score_band.70-79"} <= ir.fact_ids(f)
    assert ir.fact_ids(ir.facts(trades, TODAY, CFG)) == ir.fact_ids(f)


def test_fact_numbers_render_the_way_the_report_prints():
    trades = [
        row(idea_id="a", realized_pnl=125.0, mae_pnl=-50.0),
        row(idea_id="b", exit_reason="STOP", realized_pnl=-37.5,
            mae_pnl=-400.0),
        row(idea_id="c", realized_pnl=0.0),
        row(idea_id="d", realized_pnl=0.0),
    ]
    f = ir.facts(trades, TODAY, CFG)
    nums = ir.fact_numbers(f)
    # counts as integers
    assert {"4", "3", "1", "0"} <= nums
    # money and percentages, two decimals, signed and absolute
    assert {"125.00", "-37.50", "37.50", "87.50", "21.88", "-23.50", "23.50"} <= nums
    # a stored total of 87.5 is not printed as 87.5
    assert "87.5" not in nums
    # win rate: one of four closed with a profit
    assert "25.00" in nums
    # no signed zero
    assert "-0.00" not in nums


def test_fact_numbers_cover_open_closed_and_repeats():
    o = row(idea_id="o1", status="OPEN", exit_reason=None, exit_time=None,
            realized_pnl=None, unrealized_pnl=-41.25, mae_pnl=-100.0)
    f = ir.facts([o, row(idea_id="z"), row(idea_id="z")], TODAY, CFG)
    nums = ir.fact_numbers(f)
    assert {"-41.25", "41.25", "-20.00", "20.00", "2"} <= nums
    assert "100.00" in nums  # a closed trade's realized P&L
