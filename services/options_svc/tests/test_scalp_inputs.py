"""The Opening Scalp's morning inputs (scalp_inputs), on hand-built bars.

Every bar is written to a tmp store, so a number asserted here is arithmetic on
the fixture below and nothing else. The fixture morning is Wednesday 2026-10-14
(prior trading day Tuesday 2026-10-13):

* /ES: prior close 100; first overnight bar opens 100; last bar before 02:00
  closes 101; last bar before 08:30 closes 102; overnight high 103, low 99.5.
* SPY: prior close 500; premarket high 502.5, low 499; last premarket close 501.5.
* XLE 50 -> 50.5, XLK 200 -> 201, XLV 100 -> 100.2; $VIX 20 -> 19.5.
* SPY open bar 501.6 / 503 / 501.4 / 502.8 (an up morning breaking the premarket
  high); the $VIX 08:30 bar closes at 19.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import scalp_inputs as si
from services.options_svc import scalp_store

CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 14)
PREV = dt.date(2026, 10, 13)


def ts(day, h, m=0):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def put(db, sym, day, h, m, o, hi, lo, c, interval=5, vol=1000.0):
    scalp_store.upsert_bars([(sym, interval, ts(day, h, m), o, hi, lo, c, vol)],
                            db_path=db)


def flat(db, sym, day, h, m, price, interval=5):
    put(db, sym, day, h, m, price, price, price, price, interval)


def seed_futures(db, fut="/ES", first_day=PREV, first_h=17):
    put(db, fut, first_day, first_h, 0, 100, 100.5, 99.5, 100)
    put(db, fut, DAY, 1, 55, 100.8, 101.2, 100.6, 101)
    put(db, fut, DAY, 5, 0, 101, 103, 100.9, 102.5)
    put(db, fut, DAY, 8, 25, 102.5, 102.6, 101.9, 102)
    flat(db, fut, PREV, 14, 55, 100)


def seed_spy(db, open_bar=(501.6, 503, 501.4, 502.8), etfs=True, vix_move=19.5):
    flat(db, "SPY", PREV, 14, 55, 500)
    put(db, "SPY", DAY, 5, 0, 500, 502, 499, 501)
    put(db, "SPY", DAY, 8, 25, 501, 502.5, 500.5, 501.5)
    if open_bar is not None:
        put(db, "SPY", DAY, 8, 30, *open_bar)
    if etfs:
        for sym, prior, last in (("XLE", 50, 50.5), ("XLK", 200, 201),
                                 ("XLV", 100, 100.2)):
            flat(db, sym, PREV, 14, 55, prior)
            flat(db, sym, DAY, 8, 25, last)
    flat(db, "$VIX", PREV, 14, 55, 20)
    flat(db, "$VIX", DAY, 8, 25, vix_move)
    flat(db, "$VIX", DAY, 8, 30, 19)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def up(db):
    seed_futures(db)
    seed_spy(db)
    return db


def test_up_morning_all_agree_alignment_one(up):
    m = si.morning("SPY", DAY, db_path=up)
    assert m.direction == 1
    assert m.entry_spot == 502.8
    assert m.entry_ts == ts(DAY, 8, 35)
    assert m.open_bar == scalp_store.Bar(ts(DAY, 8, 30), 501.6, 503, 501.4, 502.8, 1000.0)
    i = m.inputs
    assert i["fut_asia"] == pytest.approx(1.0)
    assert i["fut_europe"] == pytest.approx((102 / 101 - 1) * 100)
    assert i["fut_gap"] == pytest.approx(2.0)
    assert i["fut_range_pos"] == pytest.approx((102 - 99.5) / (103 - 99.5))
    assert i["etf:XLE"] == pytest.approx(1.0)
    assert i["etf:XLK"] == pytest.approx(0.5)
    assert i["etf:XLV"] == pytest.approx(0.2)
    assert i["vol_change"] == pytest.approx(-2.5)
    assert i["open_close_loc"] == pytest.approx((502.8 - 501.4) / (503 - 501.4))
    assert i["open_broke_pm"] == 1.0
    assert i["open_range_ratio"] is None
    assert set(m.agree) == set(i) - {"open_range_ratio"}
    assert all(v == 1 for v in m.agree.values())
    assert si.alignment(m) == 1.0
    assert m.vol_at_entry == pytest.approx(0.19)
    assert m.vol_source == "$VIX"


def test_down_morning_mirrors_agreement(db):
    seed_futures(db)
    seed_spy(db, open_bar=(501.6, 501.8, 500.2, 500.5))
    m = si.morning("SPY", DAY, db_path=db)
    assert m.direction == -1
    # The up-looking premarket now disagrees with a down open; the vol fall
    # (an up-leaning input) disagrees too; the open bar closing low agrees.
    assert m.agree["fut_gap"] == -1
    assert m.agree["vol_change"] == -1
    assert m.agree["open_close_loc"] == 1
    assert m.agree["open_broke_pm"] == 0
    assert si.alignment(m) == pytest.approx(1 / 9)


def test_dead_zone_counts_neither(db):
    seed_futures(db)
    seed_spy(db)
    flat(db, "XLV", DAY, 8, 25, 100.02)             # +0.02% is inside the 0.05% band
    put(db, "/ES", DAY, 8, 25, 102.5, 102.6, 101.9, 101.5)   # pos 0.5714, inside 0.4-0.6
    m = si.morning("SPY", DAY, db_path=db)
    assert m.inputs["etf:XLV"] == pytest.approx(0.02)
    assert m.agree["etf:XLV"] == 0
    assert m.inputs["fut_range_pos"] == pytest.approx((101.5 - 99.5) / 3.5)
    assert m.agree["fut_range_pos"] == 0
    # 8 other inputs all agree; the two dead-zone ones are in neither count.
    assert sum(1 for v in m.agree.values() if v == 1) == 8
    assert si.alignment(m) == 1.0


def test_missing_etf_is_absent_not_disagree(up):
    # XLK has no premarket bar at all.
    conn = scalp_store.connect(up)
    with conn:
        conn.execute("DELETE FROM bars WHERE symbol = 'XLK' AND ts = ?",
                     (ts(DAY, 8, 25),))
    conn.close()
    m = si.morning("SPY", DAY, db_path=up)
    assert m.inputs["etf:XLK"] is None
    assert m.agree["etf:XLK"] is None
    assert "etf:XLK" in m.agree
    assert si.alignment(m) == 1.0                   # 9 of 9 that took a side


def test_vix_rise_disagrees_with_up(db):
    seed_futures(db)
    seed_spy(db, vix_move=20.5)
    m = si.morning("SPY", DAY, db_path=db)
    assert m.inputs["vol_change"] == pytest.approx(2.5)
    assert m.agree["vol_change"] == -1
    assert si.alignment(m) == pytest.approx(9 / 10)


def test_monday_asia_starts_sunday_evening(db):
    monday, friday, sunday = dt.date(2026, 10, 19), dt.date(2026, 10, 16), \
        dt.date(2026, 10, 18)
    assert monday.weekday() == 0
    # Friday's regular-hours bar (before 16:00) must NOT be Asia's start.
    put(db, "/ES", friday, 15, 55, 90, 90, 90, 90)
    flat(db, "/ES", friday, 14, 55, 100)
    put(db, "/ES", sunday, 17, 0, 100, 100.4, 99.6, 100.2)
    put(db, "/ES", monday, 1, 55, 100.2, 101.2, 100.1, 101)
    put(db, "/ES", monday, 8, 25, 101, 101.5, 100.9, 101.3)
    m = si.morning("SPY", monday, db_path=db)
    assert m.inputs["fut_asia"] == pytest.approx((101 / 100 - 1) * 100)
    assert m.inputs["fut_europe"] == pytest.approx((101.3 / 101 - 1) * 100)
    assert m.inputs["fut_gap"] == pytest.approx(1.3)
    assert m.inputs["fut_range_pos"] == pytest.approx((101.3 - 99.6) / (101.5 - 99.6))


def test_missing_open_bar_gives_no_direction(db):
    seed_futures(db)
    seed_spy(db, open_bar=None)
    m = si.morning("SPY", DAY, db_path=db)
    assert m.direction is None
    assert m.open_bar is None
    assert m.entry_spot is None and m.entry_ts is None
    assert m.inputs["fut_asia"] == pytest.approx(1.0)     # still filled
    assert m.inputs["etf:XLE"] == pytest.approx(1.0)
    assert m.inputs["open_close_loc"] is None
    assert m.inputs["open_broke_pm"] is None
    assert all(v is None for v in m.agree.values())
    assert si.alignment(m) is None
    assert m.vol_at_entry == pytest.approx(0.19)


def test_flat_open_bar_is_inside_the_open_dead_zone(db):
    seed_futures(db)
    seed_spy(db, open_bar=(500.0, 500.2, 499.9, 500.1))   # +0.02% < 0.03%
    m = si.morning("SPY", DAY, db_path=db)
    assert m.direction is None
    assert m.entry_spot == 500.1
    assert si.alignment(m) is None


def test_spx_reads_futures_for_premarket(db):
    seed_futures(db)
    flat(db, "$VIX", PREV, 14, 55, 20)
    flat(db, "$VIX", DAY, 8, 25, 19.5)
    flat(db, "$VIX", DAY, 8, 30, 19)
    # $SPX has no premarket bar; its own open bar breaks nothing it could name.
    put(db, "$SPX", DAY, 8, 30, 6000, 6004, 5999, 6003)
    # The futures' 08:30 bar clears the futures' premarket high (103).
    put(db, "/ES", DAY, 8, 30, 102, 103.5, 101.8, 103.2)
    m = si.morning("$SPX", DAY, db_path=db)
    assert m.direction == 1
    assert m.inputs["open_broke_pm"] == 1.0
    assert m.agree["open_broke_pm"] == 1
    # The futures' bar breaking the LOW instead gives -1 (against an up open).
    put(db, "/ES", DAY, 8, 30, 102, 102.4, 99.0, 100.0)
    m = si.morning("$SPX", DAY, db_path=db)
    assert m.inputs["open_broke_pm"] == -1.0
    assert m.agree["open_broke_pm"] == -1


def test_spx_without_a_futures_open_bar_is_absent(db):
    seed_futures(db)
    put(db, "$SPX", DAY, 8, 30, 6000, 6004, 5999, 6003)
    m = si.morning("$SPX", DAY, db_path=db)
    assert m.direction == 1
    assert m.inputs["open_broke_pm"] is None
    assert m.agree["open_broke_pm"] is None


def test_spy_open_broke_uses_its_own_premarket(db):
    seed_futures(db)
    seed_spy(db, open_bar=(500.0, 502.0, 498.5, 501.0))   # low 498.5 < pm low 499
    m = si.morning("SPY", DAY, db_path=db)
    assert m.inputs["open_broke_pm"] == -1.0
    # The bar breaking both sides names neither.
    seed_spy(db, open_bar=(500.0, 503.0, 498.5, 501.0))
    m = si.morning("SPY", DAY, db_path=db)
    assert m.inputs["open_broke_pm"] == 0.0


def test_one_minute_and_five_minute_mornings_agree(up):
    # 08:30-08:34 one-minute bars that aggregate to the 5-minute bar above.
    minutes = [(501.6, 502.0, 501.5, 501.9, 100.0), (501.9, 502.3, 501.4, 502.2, 200.0),
               (502.2, 503.0, 502.0, 502.9, 300.0), (502.9, 502.95, 502.4, 502.5, 400.0),
               (502.5, 502.9, 502.4, 502.8, 500.0)]
    for k, (o, h, lo, c, v) in enumerate(minutes):
        put(up, "SPY", DAY, 8, 30 + k, o, h, lo, c, interval=1, vol=v)
    five = si.morning("SPY", DAY, interval=5, db_path=up)
    one = si.morning("SPY", DAY, interval=1, db_path=up)
    assert one.open_bar == scalp_store.Bar(ts(DAY, 8, 30), 501.6, 503.0, 501.4, 502.8,
                                           1500.0)
    assert one.inputs == five.inputs
    assert one.agree == five.agree
    assert (one.direction, one.entry_spot, one.entry_ts, one.vol_at_entry) == \
        (five.direction, five.entry_spot, five.entry_ts, five.vol_at_entry)
    assert one.entry_ts == ts(DAY, 8, 35)


def test_one_minute_morning_missing_a_minute_has_no_open_bar(up):
    for k in (0, 1, 2, 4):                                 # 08:33 is missing
        put(up, "SPY", DAY, 8, 30 + k, 501.6, 503, 501.4, 502.8, interval=1)
    m = si.morning("SPY", DAY, interval=1, db_path=up)
    assert m.open_bar is None and m.direction is None and m.entry_ts is None


def test_missing_vix_bar_gives_no_vol_at_entry(db):
    seed_futures(db)
    seed_spy(db)
    conn = scalp_store.connect(db)
    with conn:
        conn.execute("DELETE FROM bars WHERE symbol = '$VIX' AND ts = ?",
                     (ts(DAY, 8, 30),))
    conn.close()
    m = si.morning("SPY", DAY, db_path=db)
    assert m.vol_at_entry is None
    assert m.vol_source is None
    assert m.direction == 1                               # the call itself is unharmed


@pytest.mark.parametrize("close", [0.0, -3.0, float("nan")])
def test_unusable_vol_close_is_none(db, close):
    seed_futures(db)
    seed_spy(db)
    flat(db, "$VIX", DAY, 8, 30, close)
    assert si.morning("SPY", DAY, db_path=db).vol_at_entry is None


def test_qqq_uses_vxn_and_falls_back_to_vix(db):
    flat(db, "$VIX", DAY, 8, 30, 19)
    put(db, "QQQ", DAY, 8, 30, 400, 401, 399.5, 400.8)
    m = si.morning("QQQ", DAY, db_path=db)
    assert (m.vol_at_entry, m.vol_source) == (pytest.approx(0.19), "$VIX")
    flat(db, "$VXN", DAY, 8, 30, 24)
    m = si.morning("QQQ", DAY, db_path=db)
    assert (m.vol_at_entry, m.vol_source) == (pytest.approx(0.24), "$VXN")
    assert "etf:IGV" in m.inputs and "etf:EWY" in m.inputs   # QQQ's own wiring


def test_open_range_ratio_is_vs_the_prior_sessions(up):
    d = DAY
    for _ in range(6):
        from shared import market_calendar as mc
        d = mc.prev_trading_day(d)
        put(up, "SPY", d, 8, 30, 500, 501, 500, 500.5)    # a range of 1.0 each
    m = si.morning("SPY", DAY, db_path=up)                # today's range is 1.6
    assert m.inputs["open_range_ratio"] == pytest.approx(1.6)
    assert "open_range_ratio" not in m.agree


def test_open_range_ratio_needs_five_prior_sessions(up):
    d = DAY
    from shared import market_calendar as mc
    for _ in range(4):
        d = mc.prev_trading_day(d)
        put(up, "SPY", d, 8, 30, 500, 501, 500, 500.5)
    assert si.morning("SPY", DAY, db_path=up).inputs["open_range_ratio"] is None


def test_an_empty_store_raises_nothing(db):
    m = si.morning("SPY", DAY, db_path=db)
    assert m.direction is None and m.vol_at_entry is None
    assert all(v is None for v in m.inputs.values())
    assert si.alignment(m) is None


def test_alignment_with_no_side_taken_is_none():
    m = si.Morning(day=DAY, symbol="SPY", direction=1, entry_spot=1.0, entry_ts=1,
                   open_bar=None, inputs={}, agree={"a": 0, "b": None})
    assert si.alignment(m) is None


def test_an_unsupported_interval_is_refused(db):
    with pytest.raises(ValueError):
        si.morning("SPY", DAY, interval=3, db_path=db)
