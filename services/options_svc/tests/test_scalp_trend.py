"""The trend factors (scalp_trend): EMA slope and RSI on 5-minute, 15-minute and
hourly bars built from the stored 5-minute bars, on hand-built fixtures.

Every bar is a 5-minute bar stored by its START in a tmp store. The fixture day is
Wednesday 2026-10-14 (a regular 08:30-15:00 CT session).
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from shared import market_calendar as mc
from shared import scalp_config
from services.options_svc import scalp_store
from services.options_svc import scalp_trend as st

CT = ZoneInfo("America/Chicago")
DAY = dt.date(2026, 10, 14)
SYM = "SPY"


def ts(h, m=0, day=DAY):
    return int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=CT).timestamp())


def open_of(day):
    return ts(8, 30, day)


def close_of(day):
    return ts(15, 0, day)


def bar5(start, close):
    return scalp_store.Bar(start, close, close, close, close, 1000.0)


def run(day, first, count, price):
    """``count`` 5-minute bars of ``day`` from ``first`` (an epoch), the i-th
    closing at ``price(i)``."""
    return [bar5(first + i * 300, price(i)) for i in range(count)]


def put(db, bars, sym=SYM):
    scalp_store.upsert_bars(
        [(sym, 5, b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars],
        db_path=db)


def full_session(day, price):
    """The day's 78 regular-session bars, 08:30 .. 14:55."""
    return run(day, open_of(day), 78, price)


def prior_days(n, day=DAY):
    out = []
    for _ in range(n):
        day = mc.prev_trading_day(day)
        out.append(day)
    return out


# ── the arithmetic ───────────────────────────────────────────────────────────
def test_ema_slope_on_a_straight_line():
    closes = [100.0 + i for i in range(40)]
    period, slope = 20, 3
    alpha = 2.0 / (period + 1)
    e = [sum(closes[:period]) / period]          # independent loop, same recipe
    for c in closes[period:]:
        e.append(c * alpha + e[-1] * (1 - alpha))
    expected = (e[-1] / e[-4] - 1.0) * 100.0
    assert st.ema_slope_pct(closes, period, slope) == pytest.approx(expected, abs=1e-9)
    assert expected > 0


def test_ema_slope_needs_period_plus_slope_closes():
    closes = [100.0 + i for i in range(22)]
    assert st.ema_slope_pct(closes, 20, 3) is None
    assert st.ema_slope_pct(closes + [122.0], 20, 3) is not None


def test_rsi_known_values():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
              46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    # The first Wilder value: simple means of the first 14 changes. Counted by hand
    # from these 15 closes the nine gains sum to 3.34 and the four losses to 1.40,
    # so RS = 3.34 / 1.40 and RSI = 100 - 100 / (1 + RS) = 70.46. (The 70.53 the
    # textbook prints comes from unrounded prices, which these closes are not.)
    assert st.rsi(closes, 14) == pytest.approx(100.0 - 100.0 / (1.0 + 3.34 / 1.40),
                                               abs=1e-9)
    assert st.rsi(closes, 14) == pytest.approx(70.46, abs=0.01)
    assert st.rsi([float(i) for i in range(1, 20)], 14) == 100.0
    assert st.rsi([10.0] * 20, 14) == 50.0
    assert st.rsi(closes[:14], 14) is None
    assert st.rsi([float(20 - i) for i in range(20)], 14) == pytest.approx(0.0, abs=1e-9)


# ── resample ─────────────────────────────────────────────────────────────────
def test_resample_drops_the_bar_still_open_at_entry():
    # 5-minute bars starting 08:30 .. 09:40; the entry is 09:50.
    bars = run(DAY, ts(8, 30), 15, lambda i: 100.0 + i)
    assert bars[-1].ts == ts(9, 40)
    m15 = st.resample(bars, 15, open_of, close_of, ts(9, 50))
    assert [s for s, _ in m15] == [ts(8, 30) + 900 * k for k in range(5)]
    assert m15[-1] == (ts(9, 30), 114.0)         # the 09:30-09:45 bar, closing as 09:40's
    m60 = st.resample(bars, 60, open_of, close_of, ts(9, 50))
    assert m60 == [(ts(8, 30), 111.0)]           # 08:30-09:30, closing as 09:25's


def test_resample_needs_the_bar_to_have_ended_by_the_entry():
    bars = run(DAY, ts(8, 30), 15, lambda i: 100.0 + i)
    assert st.resample(bars, 15, open_of, close_of, ts(9, 44))[-1][0] == ts(9, 15)
    assert st.resample(bars, 15, open_of, close_of, ts(9, 45))[-1][0] == ts(9, 30)


def test_resample_skips_a_bar_with_a_missing_part():
    bars = [b for b in run(DAY, ts(8, 30), 6, lambda i: 100.0 + i) if b.ts != ts(8, 40)]
    m15 = st.resample(bars, 15, open_of, close_of, ts(9, 30))
    assert [s for s, _ in m15] == [ts(8, 45)]    # 08:30 lost its 08:40 part


def test_resample_skips_a_bar_with_an_unusable_close():
    bars = run(DAY, ts(8, 30), 6, lambda i: 100.0 + i)
    bars[1] = bar5(bars[1].ts, float("nan"))
    assert [s for s, _ in st.resample(bars, 15, open_of, close_of, ts(9, 0))] == [ts(8, 45)]


def test_resample_short_last_bar_counts_once_the_session_has_closed():
    # A session that closes at 09:45: its last hourly bar is 09:30-09:45.
    bars = run(DAY, ts(8, 30), 15, lambda i: 100.0 + i)
    early = lambda day: ts(9, 45, day)  # noqa: E731
    got = st.resample(bars, 60, open_of, early, ts(9, 45))
    assert got == [(ts(8, 30), 111.0), (ts(9, 30), 114.0)]
    assert st.resample(bars, 60, open_of, early, ts(9, 44)) == [(ts(8, 30), 111.0)]


def test_resample_regular_session_only():
    pre = run(DAY, ts(7, 0), 6, lambda i: 50.0)          # 07:00-07:25
    post = run(DAY, ts(15, 0), 6, lambda i: 50.0)        # 15:00 on
    reg = run(DAY, ts(8, 30), 6, lambda i: 100.0 + i)
    got = st.resample(pre + reg + post, 15, open_of, close_of, ts(23, 0))
    assert [s for s, _ in got] == [ts(8, 30), ts(8, 45)]


def test_resample_hourly_ends_with_the_short_bar_of_a_full_day():
    bars = full_session(DAY, lambda i: 100.0 + i)
    got = st.resample(bars, 60, open_of, close_of, ts(15, 0))
    assert [s for s, _ in got][-1] == ts(14, 30)
    assert len(got) == 7
    assert got[-1][1] == 100.0 + 77


def test_resample_keeps_each_days_own_alignment():
    d1, d2 = prior_days(1)[0], DAY
    bars = (run(d1, open_of(d1), 6, lambda i: 100.0 + i)
            + run(d2, open_of(d2), 6, lambda i: 200.0 + i))
    got = st.resample(bars, 15, open_of, close_of, ts(23, 0, d2))
    assert [s for s, _ in got] == [ts(8, 30, d1), ts(8, 45, d1), ts(8, 30, d2), ts(8, 45, d2)]


# ── trend_factors ────────────────────────────────────────────────────────────
def wave(i):
    return 100.0 + (i % 17) * 0.3 + i * 0.01


def build_store(db, nprior=10, extra_today=()):
    for n, d in enumerate(prior_days(nprior)):
        put(db, full_session(d, lambda i, n=n: wave(i + 7 * n)))
    put(db, run(DAY, ts(8, 30), 4, lambda i: wave(i)))          # 08:30 .. 08:45
    put(db, list(extra_today))


def test_trend_factors_keys_and_shape(tmp_path):
    db = str(tmp_path / "s.db")
    build_store(db)
    got = st.trend_factors(SYM, DAY, ts(8, 50), db_path=db)
    assert set(got) == {"slope_5", "slope_15", "slope_60", "rsi_5", "rsi_15", "rsi_60"}
    assert all(v is not None for v in got.values())
    assert all(0.0 <= got[f"rsi_{m}"] <= 100.0 for m in (5, 15, 60))


def test_trend_factors_never_reads_at_or_after_entry(tmp_path):
    clean, tainted = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    build_store(clean)
    # A bar AT the entry (and one after) with an absurd close.
    build_store(tainted, extra_today=[bar5(ts(8, 50), 1000.0), bar5(ts(8, 55), 1000.0)])
    entry = ts(8, 50)
    assert (st.trend_factors(SYM, DAY, entry, db_path=tainted)
            == st.trend_factors(SYM, DAY, entry, db_path=clean))
    # Entry between boundaries: the 08:50 bar is still open at 08:51.
    assert (st.trend_factors(SYM, DAY, ts(8, 51), db_path=tainted)
            == st.trend_factors(SYM, DAY, ts(8, 51), db_path=clean))


def test_trend_factors_the_cache_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "_today", lambda: DAY)   # DAY is the session still printing
    db = str(tmp_path / "s.db")
    build_store(db)
    cache: dict = {}
    first = st.trend_factors(SYM, DAY, ts(8, 50), db_path=db, cache=cache)
    assert first == st.trend_factors(SYM, DAY, ts(8, 50), db_path=db)
    assert cache                                   # the prior sessions were kept
    assert all(k[2] < DAY for k in cache)          # and never the day still printing
    # A later slot on the same cache equals an uncached read of that slot.
    put(db, run(DAY, ts(8, 50), 3, lambda i: wave(i + 4)))
    assert (st.trend_factors(SYM, DAY, ts(9, 5), db_path=db, cache=cache)
            == st.trend_factors(SYM, DAY, ts(9, 5), db_path=db))


def test_hourly_runs_on_from_previous_sessions(tmp_path):
    db = str(tmp_path / "s.db")
    build_store(db, nprior=10)
    got = st.trend_factors(SYM, DAY, ts(8, 51), db_path=db)
    assert got["slope_60"] is not None and got["rsi_60"] is not None

    empty = str(tmp_path / "e.db")
    put(empty, run(DAY, ts(8, 30), 4, lambda i: wave(i)))
    bare = st.trend_factors(SYM, DAY, ts(8, 51), db_path=empty)
    assert bare["slope_60"] is None and bare["rsi_60"] is None
    assert bare["slope_15"] is None and bare["slope_5"] is None


def test_trend_factors_with_no_bars_at_all_are_all_none(tmp_path):
    got = st.trend_factors(SYM, DAY, ts(8, 50), db_path=str(tmp_path / "n.db"))
    assert got == {k: None for k in
                   ("slope_5", "slope_15", "slope_60", "rsi_5", "rsi_15", "rsi_60")}


def test_half_day_drops_after_close_prints(tmp_path, monkeypatch):
    """The store keeps extended-hours bars. On a configured half day (close 12:00)
    the prints from 12:00 on are not the session: the next session's factors equal
    those of a store that never had them."""
    half = prior_days(1)[0]
    monkeypatch.setattr(scalp_config, "early_close_days", lambda: {half})
    assert scalp_config.early_close_on(half).hour == 12

    def store(db, after_close):
        for n, d in enumerate(prior_days(10)):
            count = 42 if d == half else 78                  # 08:30 .. 11:55 on the half day
            put(db, run(d, open_of(d), count, lambda i, n=n: wave(i + 7 * n)))
        if after_close:                                      # 12:00 .. 14:55, absurd closes
            put(db, run(half, ts(12, 0, half), 36, lambda i: 1000.0 + i))
        put(db, run(DAY, ts(8, 30), 4, lambda i: wave(i)))

    clean, tainted = str(tmp_path / "c.db"), str(tmp_path / "t.db")
    store(clean, False)
    store(tainted, True)
    got = st.trend_factors(SYM, DAY, ts(8, 50), db_path=tainted)
    assert got == st.trend_factors(SYM, DAY, ts(8, 50), db_path=clean)
    assert all(v is not None for v in got.values())
    # The test has teeth: were the day a full one, those prints would move the numbers.
    monkeypatch.setattr(scalp_config, "early_close_days", lambda: set())
    assert st.trend_factors(SYM, DAY, ts(8, 50), db_path=tainted) != got
