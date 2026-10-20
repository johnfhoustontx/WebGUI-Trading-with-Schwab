"""The intraday calls' price reading (scalp_intraday_inputs), on hand-built bars.

Every bar is written to a tmp store, so a number asserted here is arithmetic on
the fixture below. The slot under test is 08:51 on Wednesday 2026-10-14: its
entry is the 08:50 boundary, so the reading sees the bars starting 08:30, 08:35,
08:40 and 08:45 (the last 15 minutes are the 08:35, 08:40 and 08:45 bars, and
``entry_spot`` is the close of the 08:45 bar).

``path(db, sym, [p0 .. p4])`` writes four 5-minute bars from 08:30 whose open is
the previous price and whose close is the next: ``p0`` is the session open, ``p4``
the spot, and the last-15-minute move is ``p4 / p1 - 1``.

The fixture day is an up tape: SPY 500 -> 503 (+0.6% since the open, +0.6% over
the last 15 minutes), /ES and the sector funds up, $VIX down, and a big ten of
which seven names are up.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from shared import symbols
from services.options_svc import scalp_inputs
from services.options_svc import scalp_intraday_inputs as sii
from services.options_svc import scalp_store

CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 14)
SLOT = "08:51"
MEMBERS = tuple(f"M{i}" for i in range(1, 11))
UP = [500, 500, 501, 502, 503]
DOWN = [500, 500, 499, 498, 497]


def ts(h, m=0, day=DAY):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def put(db, sym, h, m, o, hi, lo, c, vol=1000.0):
    scalp_store.upsert_bars([(sym, 5, ts(h, m), o, hi, lo, c, vol)], db_path=db)


def path(db, sym, prices, vol=1000.0, first_minute=30):
    """Bars from 08:30 (or ``first_minute``) opening at one price, closing at the
    next."""
    for i in range(len(prices) - 1):
        o, c = prices[i], prices[i + 1]
        minute = first_minute + 5 * i
        put(db, sym, 8 + minute // 60, minute % 60, o, max(o, c), min(o, c), c, vol)


def seed(db, spy=UP, es=(100, 100, 100.5, 101, 101.5), vix=(20, 20, 19.8, 19.6, 19.4),
         members_up=7):
    path(db, "SPY", spy)
    path(db, "/ES", es)
    path(db, "$VIX", vix)
    for sym in ("XLE", "XLK", "XLV"):
        path(db, sym, (50, 50, 50.2, 50.4, 50.6))
    for i, sym in enumerate(MEMBERS):
        path(db, sym, (100, 100, 100.5, 101, 101.5) if i < members_up
             else (100, 100, 99.5, 99, 98.5))


@pytest.fixture(autouse=True)
def _big_ten(monkeypatch):
    monkeypatch.setattr(symbols, "big10", lambda: MEMBERS)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "scalp.db"


@pytest.fixture
def up(db):
    seed(db)
    return db


def test_direction_from_last_15_minutes(up):
    r = sii.reading("SPY", DAY, SLOT, db_path=up)
    assert r.direction == 1
    assert r.entry_spot == 503
    assert r.entry_ts == ts(8, 50)
    assert r.symbol == "SPY" and r.slot == SLOT and r.day == DAY
    assert r.inputs["since_open"] == pytest.approx((503 / 500 - 1) * 100)
    assert r.inputs["fut_15m"] == pytest.approx((101.5 / 100 - 1) * 100)
    assert r.agree["since_open"] == 1
    assert r.agree["fut_15m"] == 1
    assert r.agree["etf:XLE"] == 1
    assert sii.alignment(r) == 1.0


def test_direction_is_the_last_15_minutes_not_since_open(db):
    # Up 1.4% since the open, but down over the last three bars: the call is down.
    seed(db, spy=[500, 510, 509, 508, 507])
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["since_open"] == pytest.approx((507 / 500 - 1) * 100)
    assert r.direction == -1
    assert r.agree["since_open"] == -1  # up since the open disagrees with down


def test_down_direction_flips_the_agreement(db):
    seed(db, spy=DOWN)
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction == -1
    assert r.agree["since_open"] == 1      # down since the open agrees with down
    assert r.agree["fut_15m"] == -1        # futures are up
    assert r.agree["vol_change"] == -1     # $VIX falling disagrees with a down call


def test_dead_zone_no_direction(db):
    # 0.01% over the last 15 minutes is inside the 0.03% dead zone.
    seed(db, spy=[500, 500, 500.01, 500.02, 500.05])
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction is None
    assert r.entry_spot == 500.05
    assert all(v is None for v in r.agree.values())
    assert r.inputs["since_open"] == pytest.approx(0.01)  # still filled where possible
    assert sii.alignment(r) is None


def test_a_move_outside_the_dead_zone_is_a_direction(db):
    seed(db, spy=[500, 500, 500.1, 500.2, 500.4])  # +0.08% > 0.03%
    assert sii.reading("SPY", DAY, SLOT, db_path=db).direction == 1


def test_vix_rise_disagrees_with_up(db):
    seed(db, vix=(20, 20, 20.3, 20.6, 21))
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction == 1
    assert r.inputs["vol_change"] == pytest.approx((21 / 20 - 1) * 100)
    assert r.agree["vol_change"] == -1
    # ... and a falling $VIX agrees.
    seed(db, vix=(20, 20, 19.8, 19.6, 19.4))
    assert sii.reading("SPY", DAY, SLOT, db_path=db).agree["vol_change"] == 1


def test_vwap_side_for_a_symbol_with_volume(db):
    # Heavy volume early at 500, a light run up to 503: VWAP sits near 500.
    seed(db)
    put(db, "SPY", 8, 30, 500, 500, 500, 500, vol=9000.0)
    put(db, "SPY", 8, 35, 500, 501, 500, 501, vol=100.0)
    put(db, "SPY", 8, 40, 501, 502, 501, 502, vol=100.0)
    put(db, "SPY", 8, 45, 502, 503, 502, 503, vol=100.0)
    typical = [500.0, 500.6666666667, 501.6666666667, 502.6666666667]
    vols = [9000.0, 100.0, 100.0, 100.0]
    vwap = sum(t * v for t, v in zip(typical, vols)) / sum(vols)
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["vwap_side"] == pytest.approx((503 / vwap - 1) * 100, rel=1e-6)
    assert r.agree["vwap_side"] == 1


def test_spx_uses_spy_vwap(db):
    # $SPX prints no volume and rises; SPY's tape is what the VWAP side reads.
    seed(db)
    path(db, "$SPX", [5000, 5000, 5010, 5020, 5030], vol=0.0)
    # SPY: a spike on huge volume at the open, then a slide; the spot is below VWAP.
    put(db, "SPY", 8, 30, 500, 510, 500, 509, vol=50000.0)
    put(db, "SPY", 8, 35, 509, 509, 504, 505, vol=100.0)
    put(db, "SPY", 8, 40, 505, 505, 502, 503, vol=100.0)
    put(db, "SPY", 8, 45, 503, 503, 501, 502, vol=100.0)
    spx = sii.reading("$SPX", DAY, SLOT, db_path=db)
    spy = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert spx.direction == 1
    assert spx.entry_spot == 5030          # its own price ...
    assert spx.inputs["vwap_side"] == spy.inputs["vwap_side"]   # ... SPY's VWAP side
    assert spx.inputs["vwap_side"] < 0
    assert spx.agree["vwap_side"] == -1    # SPY below its VWAP disagrees with up


def test_vwap_is_absent_with_no_volume(db):
    seed(db)
    path(db, "SPY", UP, vol=0.0)
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["vwap_side"] is None
    assert r.agree["vwap_side"] is None
    assert r.direction == 1  # the rest of the reading stands


def test_breadth_counts_big10_members(db):
    seed(db, members_up=7)
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["breadth"] == pytest.approx(0.7)
    assert r.agree["breadth"] == 1
    # Five of ten is not a lean either way.
    seed(db, members_up=5)
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["breadth"] == pytest.approx(0.5)
    assert r.agree["breadth"] == 0
    # Three of ten up disagrees with an up call ...
    seed(db, members_up=3)
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.agree["breadth"] == -1
    # ... and exactly breadth_agree (six of ten) agrees.
    seed(db, members_up=6)
    assert sii.reading("SPY", DAY, SLOT, db_path=db).agree["breadth"] == 1


def test_breadth_for_a_down_call_counts_the_down_names(db):
    seed(db, spy=DOWN, members_up=3)   # seven names down
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction == -1
    assert r.agree["breadth"] == 1


def test_breadth_dead_zone_member_does_not_agree(db):
    seed(db, members_up=6)
    path(db, "M1", (100, 100, 100.001, 100.002, 100.003))   # flat: inside the dead zone
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["breadth"] == pytest.approx(0.5)        # 5 up of 10 usable
    assert r.agree["breadth"] == 0


def test_breadth_needs_enough_members(db):
    seed(db)
    conn = scalp_store.connect(db)
    with conn:   # six members lose their bars: four usable is not a breadth
        conn.execute("DELETE FROM bars WHERE symbol IN (%s)" %
                     ",".join("?" * len(MEMBERS[4:])), MEMBERS[4:])
    conn.close()
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["breadth"] is None
    assert r.agree["breadth"] is None
    assert r.direction == 1


def test_missing_bar_is_absent(db):
    seed(db)
    conn = scalp_store.connect(db)
    with conn:   # the futures lose the 08:40 bar: their 15-minute move is unknown
        conn.execute("DELETE FROM bars WHERE symbol = '/ES' AND ts = ?", (ts(8, 40),))
    conn.close()
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.inputs["fut_15m"] is None
    assert r.agree["fut_15m"] is None
    assert r.direction == 1 and r.agree["since_open"] == 1


def test_missing_symbol_bar_leaves_no_direction(db):
    seed(db)
    conn = scalp_store.connect(db)
    with conn:
        conn.execute("DELETE FROM bars WHERE symbol = 'SPY' AND ts = ?", (ts(8, 40),))
    conn.close()
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction is None
    assert all(v is None for v in r.agree.values())


def test_no_open_bar_no_direction(db):
    seed(db)
    conn = scalp_store.connect(db)
    with conn:
        conn.execute("DELETE FROM bars WHERE symbol = 'SPY' AND ts = ?", (ts(8, 30),))
    conn.close()
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction is None
    assert r.inputs["since_open"] is None


def test_an_empty_store_does_not_raise(db):
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.direction is None and r.entry_spot is None
    assert r.vol_at_entry is None and r.vol_source is None
    assert all(v is None for v in r.agree.values())


def test_slot_before_the_first_fifteen_minutes_has_no_direction(db):
    seed(db)
    # Premarket bars exist, but the 08:36 slot's last 15 minutes would start 08:20.
    path(db, "SPY", [490, 490, 491, 492], first_minute=15)   # 08:15, 08:20, 08:25
    r = sii.reading("SPY", DAY, "08:36", db_path=db)
    assert r.direction is None
    assert all(v is None for v in r.agree.values())


def test_vol_at_entry_is_the_entry_bar_close(up):
    r = sii.reading("SPY", DAY, SLOT, db_path=up)
    assert r.vol_at_entry == pytest.approx(0.194)       # the 08:45 bar's close / 100
    assert r.vol_source == "$VIX"


def test_qqq_vol_falls_back_to_vix(db):
    seed(db)
    path(db, "QQQ", UP)
    path(db, "/NQ", (100, 100, 100.5, 101, 101.5))
    r = sii.reading("QQQ", DAY, SLOT, db_path=db)
    assert r.vol_source == "$VIX"
    assert r.vol_at_entry == pytest.approx(0.194)
    path(db, "$VXN", (25, 25, 24.8, 24.6, 24.4))
    r = sii.reading("QQQ", DAY, SLOT, db_path=db)
    assert r.vol_source == "$VXN"
    assert r.vol_at_entry == pytest.approx(0.244)


def test_non_positive_vol_is_absent(db):
    seed(db)
    path(db, "$VIX", (20, 20, 19, 18, 0))
    r = sii.reading("SPY", DAY, SLOT, db_path=db)
    assert r.vol_at_entry is None


def test_cached_equals_uncached(db):
    seed(db)
    path(db, "$SPX", [5000, 5000, 5010, 5020, 5030], vol=0.0)
    for later in ("08:55", "09:00", "09:05", "09:10"):   # more bars for later slots
        h, m = int(later[:2]), int(later[3:])
        for sym in ("SPY", "/ES", "$VIX", "XLE", "XLK", "XLV", "$SPX") + MEMBERS:
            put(db, sym, h, m, 100, 101, 99, 100.5)
    cache = {}
    for symbol in ("$SPX", "SPY"):
        for slot in ("08:51", "09:06", "09:21", "08:36"):
            plain = sii.reading(symbol, DAY, slot, db_path=db)
            cached = sii.reading(symbol, DAY, slot, db_path=db, cache=cache)
            assert cached == plain
    assert cache   # the cache was used
    # A second pass over a warm cache still agrees.
    again = sii.reading("SPY", DAY, "08:51", db_path=db, cache=cache)
    assert again == sii.reading("SPY", DAY, "08:51", db_path=db)


@pytest.mark.parametrize("use_cache", [False, True])
def test_entry_never_reads_past_entry_ts(db, use_cache):
    seed(db)
    path(db, "$SPX", [5000, 5000, 5010, 5020, 5030], vol=0.0)
    cache = {} if use_cache else None
    before = sii.reading("SPY", DAY, SLOT, db_path=db)
    before_spx = sii.reading("$SPX", DAY, SLOT, db_path=db)
    # Bars AT and after the entry boundary, wildly different, for every symbol.
    for minute in (50, 55, 60, 65, 120):
        for sym in ("SPY", "$SPX", "/ES", "$VIX", "XLE", "XLK", "XLV") + MEMBERS:
            put(db, sym, 8 + minute // 60, minute % 60, 900, 999, 1, 7, vol=1e9)
    after = sii.reading("SPY", DAY, SLOT, db_path=db, cache=cache)
    after_spx = sii.reading("$SPX", DAY, SLOT, db_path=db, cache=cache)
    assert after == before
    assert after_spx == before_spx


def test_alignment_reuses_the_morning_semantics(up):
    r = sii.reading("SPY", DAY, SLOT, db_path=up)
    assert sii.alignment(r) == scalp_inputs.alignment(r)
    r.agree = {"a": 1, "b": -1, "c": 0, "d": None, "e": 1}
    assert sii.alignment(r) == pytest.approx(2 / 3)    # the dead zone and absent are ignored
    r.direction = None
    assert sii.alignment(r) is None
