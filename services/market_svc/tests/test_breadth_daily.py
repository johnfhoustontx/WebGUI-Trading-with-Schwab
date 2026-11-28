"""The daily breadth row: built from Schwab's history bars and the closing quotes.

The facts the rules answer were measured on prod on 2026-11-27: a daily bar is
stamped midnight Central of its session; the breadth series carry weekend-dated
junk bars and $TRIN zeros; the live $UVOL quote was clipped at the 64-bit
ceiling. See docs/plans/2026-11-27-market-cycle-breadth-store-plan.md."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.market_svc import breadth_daily as bd

CT = ZoneInfo("America/Chicago")


def bar(day, close, **extra):
    """A Schwab daily candle: stamped midnight Central of its session."""
    y, m, d = (int(x) for x in day.split("-"))
    ms = int(dt.datetime(y, m, d, tzinfo=CT).timestamp() * 1000)
    return {"datetime": ms, "close": close, "high": extra.get("high", close),
            "low": extra.get("low", close), "volume": extra.get("volume", 0)}


def by_series(rows):
    return {(s, d): v for s, d, v, _ in rows}


# ── the pure builders ───────────────────────────────────────────────────────
def test_session_day_is_the_central_date_in_winter_and_summer():
    assert bd.session_day({"datetime": 1795586400000}) == "2026-11-25"   # 06:00 UTC
    assert bd.session_day(bar("2026-07-01", 1.0)) == "2026-07-01"        # 05:00 UTC
    assert bd.session_day({"datetime": "x"}) is None
    assert bd.session_day({}) is None


def test_up_share_matches_the_measured_days():
    assert bd.up_share(64300268.0, 13841219.0) == pytest.approx(0.8229, abs=1e-4)
    assert bd.up_share(753700.0, 467618.0) == pytest.approx(0.6171, abs=1e-4)
    assert bd.up_share(953043.0, 265676.0) == pytest.approx(0.7820, abs=1e-4)


def test_up_share_refuses_a_clipped_or_missing_volume():
    assert bd.up_share(92233720368.54776, 265676.0) is None
    assert bd.up_share(None, 265676.0) is None
    assert bd.up_share(0.0, 0.0) is None


def test_up_share_from_trin():
    # 2083 advancers, 608 decliners, TRIN 0.38510207 -> up/down volume 8.896
    assert bd.up_share_from_trin(2083, 608, 0.38510207) == pytest.approx(0.8990, abs=1e-3)
    assert bd.up_share_from_trin(2083, 0, 0.4) is None
    assert bd.up_share_from_trin(2083, 608, 0) is None


def test_history_rows_keep_only_spx_sessions():
    candles = {"$SPX": [bar("2006-11-27", 1381.9, high=1400.0, low=1380.0)],
               "$ADVN": [bar("2006-11-25", 1688.0), bar("2006-11-26", 1688.0),
                         bar("2006-11-27", 615.0)]}
    got = by_series(bd.history_rows(candles))
    assert got[("nyse_adv", "2006-11-27")] == 615.0
    assert got[("spx_high", "2006-11-27")] == 1400.0 and got[("spx_low", "2006-11-27")] == 1380.0
    assert not any(day in ("2006-11-25", "2006-11-26") for _, day in got)
    assert {src for *_, src in bd.history_rows(candles)} == {"schwab_history"}


def test_history_rows_without_spx_are_empty():
    assert bd.history_rows({"$ADVN": [bar("2026-11-25", 1886.0)]}) == []
    assert bd.history_rows({"$SPX": None, "$ADVN": [bar("2026-11-25", 1886.0)]}) == []


def test_unusable_values_are_absent():
    day = "2026-11-25"
    candles = {"$SPX": [bar(day, 7182.78)], "$TRIN": [bar(day, 0.0)],
               "$UVOL": [bar(day, 92233720368.54776)], "$DVOL": [bar(day, 265676.0)],
               "$VIX": [bar(day, float("nan"))]}
    got = by_series(bd.history_rows(candles))
    assert ("nyse_trin", day) not in got
    assert ("nyse_up_share", day) not in got       # a clipped volume gives no share
    assert ("vix_close", day) not in got


def test_history_up_share_is_stored_and_raw_volume_is_not():
    day = "2026-11-25"
    rows = bd.history_rows({"$SPX": [bar(day, 7182.78)], "$UVOL": [bar(day, 953043.0)],
                            "$DVOL": [bar(day, 265676.0)]})
    got = by_series(rows)
    assert got[("nyse_up_share", day)] == pytest.approx(0.7820, abs=1e-4)
    assert not any("vol" in s and s != "spy_volume" for s, _ in got)


def test_spy_carries_volume_and_rsp_does_not_store_it():
    day = "2026-11-25"
    got = by_series(bd.history_rows({"$SPX": [bar(day, 7182.78)],
                                     "SPY": [bar(day, 716.7, volume=73461190)],
                                     "RSP": [bar(day, 215.74, volume=13723055)]}))
    assert got[("spy_volume", day)] == 73461190 and got[("rsp_close", day)] == 215.74
    assert not any(s == "rsp_volume" for s, _ in got)


def q(last, high=0.0, low=0.0):
    return {"quote": {"lastPrice": last, "highPrice": high, "lowPrice": low, "closePrice": 1.0}}


QUOTES = {"$SPX": q(7297.49, 7301.0, 7190.0), "$ADVN": q(2083.0), "$DECN": q(608.0),
          "$TRIN": q(0.38510207), "$VIX": q(23.81), "$VIX3M": q(23.82)}
VIEW = {"enabled": True, "session": "2026-11-27", "universe": 3426,
        "counts": {"52w": {"highs": 90, "lows": 12}, "13w": {"highs": 200, "lows": 0},
                   "4w": {"highs": 310, "lows": 40}}}


def test_close_rows():
    rows = bd.close_rows("2026-11-27", QUOTES, VIEW, 1000)
    got = by_series(rows)
    assert got[("spx_close", "2026-11-27")] == 7297.49     # lastPrice, never closePrice
    assert got[("spx_low", "2026-11-27")] == 7190.0
    assert got[("vix3m_close", "2026-11-27")] == 23.82
    assert got[("nyse_up_share", "2026-11-27")] == pytest.approx(0.8990, abs=1e-3)
    assert got[("us_universe", "2026-11-27")] == 3426
    assert got[("us_13w_lows", "2026-11-27")] == 0          # zero is a real count
    sources = {s: src for s, _, _, src in rows}
    assert sources["nyse_adv"] == "schwab_close_quote" and sources["us_52w_highs"] == "own_count"
    assert not any(s.startswith(("spy_", "rsp_")) for s in sources)


def test_close_rows_leave_a_missing_quote_out():
    quotes = {k: v for k, v in QUOTES.items() if k != "$VIX3M"}
    quotes["$SPX"] = q(7297.49)                      # no high / low in the quote
    got = by_series(bd.close_rows("2026-11-27", quotes, None, 1000))
    assert ("vix3m_close", "2026-11-27") not in got
    assert ("spx_high", "2026-11-27") not in got and ("spx_close", "2026-11-27") in got


@pytest.mark.parametrize("view", [None, {}, {**VIEW, "session": "2026-11-25"},
                                  {**VIEW, "enabled": False}])
def test_stale_new_highs_view_leaves_own_counts_out(view):
    got = by_series(bd.close_rows("2026-11-27", QUOTES, view, 1000))
    assert ("nyse_adv", "2026-11-27") in got
    assert not any(s.startswith("us_") for s, _ in got)


def test_a_thin_feed_writes_no_nyse_rows():
    thin = {**QUOTES, "$ADVN": q(40.0), "$DECN": q(12.0)}
    got = by_series(bd.close_rows("2026-11-27", thin, VIEW, 1000))
    assert not any(s.startswith("nyse_") for s, _ in got)
    assert ("spx_close", "2026-11-27") in got


def test_differences_report_only_moves_past_the_tolerance():
    stored = {"nyse_adv": (2083.0, "schwab_close_quote"), "nyse_dec": (608.0, "schwab_close_quote"),
              "spx_close": (7297.49, "schwab_history")}
    history = [("nyse_adv", "2026-11-27", 2200.0, "schwab_history"),
               ("nyse_dec", "2026-11-27", 610.0, "schwab_history"),
               ("spx_close", "2026-11-27", 7000.0, "schwab_history")]
    got = bd.differences("2026-11-27", history, stored, 2.0)
    assert [d["series"] for d in got] == ["nyse_adv"]       # 5.6%; dec moved 0.3%; spx was not a quote
    assert got[0]["was"] == 2083.0 and got[0]["now"] == 2200.0
    assert got[0]["pct"] == pytest.approx(5.617, abs=1e-3)


# ── the service: when, and the two writes ───────────────────────────────────
from shared import breadth_history as bh  # noqa: E402
from shared import market_calendar as mc  # noqa: E402

CFG = {"enabled": True, "settle_min": 20, "correct_at": "07:00", "correct_until": "12:00",
       "retry_min": 30, "tolerance_pct": 2.0, "min_issues": 1000, "history_years": 20,
       "timeout_sec": 20}


def at(day, hhmm):
    y, m, d = (int(x) for x in day.split("-"))
    h, mi = (int(x) for x in hhmm.split(":"))
    return dt.datetime(y, m, d, h, mi, tzinfo=CT)


def test_close_row_is_due_after_the_settle():
    assert bd.due_close(at("2026-11-25", "15:19"), False, {}, CFG) is False
    assert bd.due_close(at("2026-11-25", "15:20"), False, {}, CFG) is True


def test_close_row_follows_the_early_close(monkeypatch):
    early = at("2026-11-27", "12:00")
    monkeypatch.setattr(mc, "session_close_on", lambda d: early)
    assert bd.due_close(at("2026-11-27", "12:19"), False, {}, CFG) is False
    assert bd.due_close(at("2026-11-27", "12:20"), False, {}, CFG) is True


def test_close_row_is_not_due_when_the_day_is_stored():
    assert bd.due_close(at("2026-11-25", "22:00"), True, {}, CFG) is False


def test_close_row_is_not_due_on_a_closed_day_or_when_switched_off():
    assert bd.due_close(at("2026-11-26", "16:00"), False, {}, CFG) is False      # Thanksgiving
    assert bd.due_close(at("2026-11-25", "16:00"), False, {}, {**CFG, "enabled": False}) is False


def test_close_row_waits_out_a_failure():
    state = {"close_next_at": at("2026-11-25", "16:00").timestamp()}
    assert bd.due_close(at("2026-11-25", "15:50"), False, state, CFG) is False
    assert bd.due_close(at("2026-11-25", "16:00"), False, state, CFG) is True


def test_correction_names_the_previous_session():
    assert bd.due_correction(at("2026-11-27", "07:00"), {}, CFG) == "2026-11-25"
    assert bd.due_correction(at("2026-11-27", "06:59"), {}, CFG) is None
    assert bd.due_correction(at("2026-11-27", "12:00"), {}, CFG) is None
    assert bd.due_correction(at("2026-11-27", "07:05"), {"corrected": "2026-11-25"}, CFG) is None
    assert bd.due_correction(at("2026-11-28", "07:05"), {}, CFG) is None          # a Saturday


# 2026-11-27 is a half day: the close is 12:00 CT.
SETTLED = "2026-11-27T18:15:00+00:00"      # 12:15 CT: the after-close refresh
INTRADAY = "2026-11-27T17:59:00+00:00"     # 11:59 CT: the last in-session refresh


class _Bus:
    def __init__(self, view, ts=SETTLED): self.view, self.ts = view, ts
    def cache_get(self, key):
        assert key == "cache:market:new_highs"
        return type("Env", (), {"payload": self.view})() if self.view is not None else None
    def cache_metas(self, keys):
        assert list(keys) == ["cache:market:new_highs"]
        return {"cache:market:new_highs": (1, self.ts) if self.view is not None else (None, None)}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    from shared import breadth_config
    monkeypatch.setattr(breadth_config, "load", lambda: dict(CFG))
    return tmp_path / "breadth.db"


def _row(path, day):
    conn = bh.connect(path)
    try:
        return bh.day_row(conn, day)
    finally:
        conn.close()


def test_refresh_writes_the_close_row_once(monkeypatch, wired):
    calls = []
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: calls.append(syms) or QUOTES)
    now = at("2026-11-27", "15:30")
    assert bd.refresh(_Bus(VIEW), {}, now, db_path=wired) is True
    assert bd.refresh(_Bus(VIEW), {}, now, db_path=wired) is False     # a restart: the day is stored
    assert len(calls) == 1 and tuple(calls[0]) == bd.CLOSE_SYMBOLS
    row = _row(wired, "2026-11-27")
    assert row["us_universe"] == (3426.0, "own_count")
    assert row["nyse_adv"] == (2083.0, "schwab_close_quote")


def test_refresh_backs_off_when_the_quotes_fail(monkeypatch, wired):
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: {})
    state, now = {}, at("2026-11-27", "15:30")
    assert bd.refresh(_Bus(None), state, now, db_path=wired) is False
    assert state["close_next_at"] == now.timestamp() + 30 * 60
    assert _row(wired, "2026-11-27") == {}


def test_correction_waits_for_schwab_to_post_the_bar(monkeypatch, wired):
    old = {"$SPX": [bar("2026-11-24", 7201.09)], "$ADVN": [bar("2026-11-24", 1415.0)]}
    monkeypatch.setattr(bd.compute, "fetch_daily", lambda sym, **k: old.get(sym, []))
    state, now = {}, at("2026-11-27", "07:10")
    assert bd.refresh(_Bus(None), state, now, db_path=wired) is True     # 11-24 filled
    assert "corrected" not in state and state["correct_next_at"] == now.timestamp() + 30 * 60
    assert _row(wired, "2026-11-25") == {}
    assert _row(wired, "2026-11-24")["spx_close"] == (7201.09, "schwab_history")   # filled


def test_correction_replaces_the_close_quote(monkeypatch, wired):
    conn = bh.connect(wired)
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1880.0, "schwab_close_quote")], dt.date(2026, 11, 25))
    conn.close()
    posted = {"$SPX": [bar("2026-11-24", 7201.09), bar("2026-11-25", 7182.78)],
              "$ADVN": [bar("2026-11-25", 1886.0)],
              "SPY": [bar("2026-11-25", 716.7, volume=73461190)]}
    asked = []
    monkeypatch.setattr(bd.compute, "fetch_daily",
                        lambda sym, **k: asked.append((sym, k)) or posted.get(sym, []))
    state = {}
    assert bd.refresh(_Bus(None), state, at("2026-11-27", "07:10"), db_path=wired) is True
    row = _row(wired, "2026-11-25")
    assert row["nyse_adv"] == (1886.0, "schwab_history") and row["spy_volume"][0] == 73461190
    assert state["corrected"] == "2026-11-25"
    assert _row(wired, "2026-11-24") == {"spx_close": (7201.09, "schwab_history"),
                                          "spx_high": (7201.09, "schwab_history"),
                                          "spx_low": (7201.09, "schwab_history")}
    assert [s for s, _ in asked] == list(bd.HISTORY_SYMBOLS)
    assert asked[0][1] == {"period_type": "month", "period": 1, "timeout": 20}


def test_a_large_correction_is_counted(monkeypatch, wired):
    from services import _degrade
    conn = bh.connect(wired)
    bh.upsert(conn, [("nyse_adv", "2026-11-25", 1500.0, "schwab_close_quote")], dt.date(2026, 11, 25))
    conn.close()
    posted = {"$SPX": [bar("2026-11-25", 7182.78)], "$ADVN": [bar("2026-11-25", 1886.0)]}
    monkeypatch.setattr(bd.compute, "fetch_daily", lambda sym, **k: posted.get(sym, []))
    _degrade.reset()
    assert bd.refresh(_Bus(None), {}, at("2026-11-27", "07:10"), db_path=wired) is True
    assert _degrade.counts().get("market.breadth") == 1


def test_switched_off_does_nothing(monkeypatch, wired):
    from shared import breadth_config
    monkeypatch.setattr(breadth_config, "load", lambda: {**CFG, "enabled": False})
    def boom(*a, **k): raise AssertionError("no fetch when switched off")
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", boom)
    monkeypatch.setattr(bd.compute, "fetch_daily", boom)
    assert bd.refresh(_Bus(VIEW), {}, at("2026-11-27", "15:30"), db_path=wired) is False
    assert bd.refresh(_Bus(VIEW), {}, at("2026-11-27", "07:10"), db_path=wired) is False


def test_refresh_never_raises(monkeypatch, wired):
    def boom(*a, **k): raise RuntimeError("proxy down")
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", boom)
    assert bd.refresh(_Bus(VIEW), {}, at("2026-11-27", "15:30"), db_path=wired) is False


def test_the_scheduler_runs_it_beside_the_poll():
    import inspect
    from services.market_svc import scheduler
    assert "breadth_daily.refresh" in inspect.getsource(scheduler.loop)


# -- the final review's fixes ------------------------------------------------
def _store(path, rows, today):
    conn = bh.connect(path)
    try:
        bh.upsert(conn, rows, today)
    finally:
        conn.close()


def test_a_partial_correction_is_not_marked_done(monkeypatch, wired):
    _store(wired, [("nyse_adv", "2026-11-25", 1500.0, "schwab_close_quote")], dt.date(2026, 11, 25))
    posted = {"$SPX": [bar("2026-11-25", 7182.78)]}          # $ADVN's fetch failed
    monkeypatch.setattr(bd.compute, "fetch_daily", lambda sym, **k: posted.get(sym))
    state, now = {}, at("2026-11-27", "07:10")
    bd.refresh(_Bus(None), state, now, db_path=wired)
    assert "corrected" not in state and state["correct_next_at"] == now.timestamp() + 30 * 60
    assert _row(wired, "2026-11-25")["nyse_adv"] == (1500.0, "schwab_close_quote")
    posted["$ADVN"] = [bar("2026-11-25", 1886.0)]              # the next try finds it
    assert bd.refresh(_Bus(None), state, at("2026-11-27", "07:40"), db_path=wired) is True
    assert state["corrected"] == "2026-11-25"
    assert _row(wired, "2026-11-25")["nyse_adv"] == (1886.0, "schwab_history")


def test_a_missed_morning_is_caught_up_while_the_row_is_unlocked(monkeypatch, wired):
    # 11-24's correction was missed (11-25's morning); on 11-27 it is still unlocked.
    _store(wired, [("nyse_adv", "2026-11-24", 1400.0, "schwab_close_quote")], dt.date(2026, 11, 24))
    posted = {"$SPX": [bar("2026-11-24", 7201.09), bar("2026-11-25", 7182.78)],
              "$ADVN": [bar("2026-11-24", 1415.0), bar("2026-11-25", 1886.0)],
              "SPY": [bar("2026-11-24", 712.0, volume=60000000)]}
    monkeypatch.setattr(bd.compute, "fetch_daily", lambda sym, **k: posted.get(sym, []))
    assert bd.refresh(_Bus(None), {}, at("2026-11-27", "07:10"), db_path=wired) is True
    row = _row(wired, "2026-11-24")
    assert row["nyse_adv"] == (1415.0, "schwab_history")
    assert row["spy_close"] == (712.0, "schwab_history")


def test_a_correction_still_owed_when_the_window_closes_is_reported_once(monkeypatch, wired):
    from services import _degrade
    _store(wired, [("nyse_adv", "2026-11-25", 1500.0, "schwab_close_quote")], dt.date(2026, 11, 25))
    def boom(*a, **k): raise AssertionError("no fetch after the window")
    monkeypatch.setattr(bd.compute, "fetch_daily", boom)
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", boom)
    _degrade.reset()
    state = {}
    assert bd.refresh(_Bus(None), state, at("2026-11-27", "12:00"), db_path=wired) is False
    assert bd.refresh(_Bus(None), state, at("2026-11-27", "12:01"), db_path=wired) is False
    assert _degrade.counts().get("market.breadth") == 1


def test_a_corrected_day_is_not_reported_after_the_window(monkeypatch, wired):
    from services import _degrade
    _store(wired, [("nyse_adv", "2026-11-25", 1886.0, "schwab_history"),
                   ("spx_close", "2026-11-25", 7182.78, "schwab_history")], dt.date(2026, 11, 26))
    _degrade.reset()
    assert bd.refresh(_Bus(None), {}, at("2026-11-27", "12:00"), db_path=wired) is False
    assert _degrade.counts().get("market.breadth") is None


def test_intraday_new_highs_counts_wait_for_the_settled_view(monkeypatch, wired):
    calls = []
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: calls.append(syms) or QUOTES)
    state = {}
    assert bd.refresh(_Bus(VIEW, INTRADAY), state, at("2026-11-27", "15:30"), db_path=wired) is True
    row = _row(wired, "2026-11-27")
    assert row["nyse_adv"] == (2083.0, "schwab_close_quote") and "us_universe" not in row
    later = at("2026-11-27", "16:00")
    assert bd.refresh(_Bus(VIEW, SETTLED), state, later, db_path=wired) is True
    assert _row(wired, "2026-11-27")["us_universe"] == (3426.0, "own_count")
    assert len(calls) == 1                       # the own counts need no second quote fetch


def test_own_counts_back_off_while_the_view_is_unsettled(monkeypatch, wired):
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: QUOTES)
    state, now = {}, at("2026-11-27", "15:30")
    bd.refresh(_Bus(VIEW, INTRADAY), state, now, db_path=wired)
    assert state["own_next_at"] == now.timestamp() + 30 * 60
    assert bd.refresh(_Bus(VIEW, SETTLED), state, at("2026-11-27", "15:45"), db_path=wired) is False
    assert "us_universe" not in _row(wired, "2026-11-27")


def test_close_quotes_without_the_index_or_the_issues_back_off(monkeypatch, wired):
    calls = []
    only_vix = {"$VIX": QUOTES["$VIX"]}
    monkeypatch.setattr(bd.compute, "fetch_raw_quotes", lambda syms, **k: calls.append(1) or only_vix)
    state, now = {}, at("2026-11-27", "15:30")
    bd.refresh(_Bus(None), state, now, db_path=wired)
    assert state["close_next_at"] == now.timestamp() + 30 * 60
    bd.refresh(_Bus(None), state, at("2026-11-27", "15:31"), db_path=wired)
    assert len(calls) == 1
