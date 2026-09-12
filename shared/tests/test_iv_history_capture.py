"""Start the IV history accruing — gap assessment C3.

Design: docs/plans/2026-09-12-iv-history-capture-design.md.

The store, the writer and the reader all already existed in
``services/trade_svc/deepdive/iv_history.py`` — with **7 rows, all dated
2026-08-04**, because `record_snapshot` is reached only from
`deepdive/engine.analyze_symbol`, i.e. only when someone opens a Deep Dive
report. So this covers the two things that were actually missing: the module being
reachable from the tier that has the chains, and a **basis** on the
constant-maturity reading so a clamped one can be refused.

⚠ The clamp is the trap worth the tests. ``constant_maturity_iv`` clamps to the
nearest tenor when 30 DTE is outside the available range — right for a one-off
report, and corrupting for a ranked series, because ``_rank_from_series`` takes
``min``/``max`` and one 7-day reading filed as a 30-day one pins the bottom of the
range for a year.
"""
import math

import pytest

from repo_paths import IV_HISTORY_DB
from shared import iv_history as ivh


# ── the module is reachable from shared/, and knows its own store ───────────

def test_the_default_db_path_is_the_repo_constant_not_a_relative_file():
    """⚠ It was ``Path('./iv_history.db')`` — a RELATIVE default, so a caller that
    omitted the path would write a stray database into whatever the process's
    working directory happened to be. Both existing callers pass the constant, so
    nothing leaked; this makes it impossible."""
    assert ivh.DEFAULT_DB_PATH == IV_HISTORY_DB


def test_the_public_surface_survived_the_move():
    for name in ("init_db", "close_db", "constant_maturity_iv", "record_snapshot",
                 "backfill_rv", "iv_rank", "rv_rank", "snapshot_count",
                 "MIN_SAMPLES_FOR_RANK", "TARGET_DTE", "DEFAULT_LOOKBACK_DAYS"):
        assert hasattr(ivh, name), name


def test_the_deepdive_still_reaches_it_through_shared():
    """Three import sites moved with it; a stale one would be an ImportError only
    when a Deep Dive is opened, which is the surface nobody runs daily."""
    import services.trade_svc.deepdive.engine as engine
    assert engine.ivh is ivh


# ── atm_iv_ladder ───────────────────────────────────────────────────────────

def _chain(spot=100.0, expiries=((20, 25.0), (27, 26.0), (34, 27.0))):
    """A chain shaped like the proxy's: ``<date>:<dte>`` keys, strike-keyed
    contract lists, ``volatility`` as a PERCENT."""
    puts, calls = {}, {}
    for dte, iv in expiries:
        key = f"2026-10-01:{dte}"
        puts[key] = {f"{spot:.1f}": [{"volatility": iv, "bid": 1.0, "ask": 1.1}],
                     f"{spot * 0.80:.1f}": [{"volatility": iv + 9, "bid": 0.2,
                                             "ask": 0.3}]}
        calls[key] = {f"{spot:.1f}": [{"volatility": iv, "bid": 1.0, "ask": 1.1}]}
    return {"underlyingPrice": spot, "putExpDateMap": puts, "callExpDateMap": calls}


def test_the_ladder_is_one_entry_per_expiry_sorted_by_dte():
    got = ivh.atm_iv_ladder(_chain())
    assert [e["dte"] for e in got] == [20, 27, 34]


def test_the_ladder_averages_the_ATM_band_across_both_sides():
    got = ivh.atm_iv_ladder(_chain(expiries=((20, 25.0),)))
    assert got[0]["atm_iv"] == pytest.approx(25.0)


def test_strikes_far_from_spot_are_excluded_from_the_ATM_band():
    """The 80%-of-spot wing carries IV+9. Including it would make the ladder a
    skew average rather than an ATM reading."""
    got = ivh.atm_iv_ladder(_chain(expiries=((20, 25.0),)))
    assert got[0]["atm_iv"] == pytest.approx(25.0)


def test_a_chain_with_no_spot_yields_no_ladder():
    chain = _chain()
    chain.pop("underlyingPrice")
    assert ivh.atm_iv_ladder(chain) == []


@pytest.mark.parametrize("bad", [None, {}, {"putExpDateMap": {}}, "nope", 3])
def test_an_unusable_chain_yields_an_empty_ladder_rather_than_raising(bad):
    assert ivh.atm_iv_ladder(bad) == []


def test_schwabs_minus_999_volatility_sentinel_is_refused():
    """⚠ A documented Schwab sentinel — ``flow_skew`` was accepting it as a usable
    IV until it was fixed. A −999 averaged into an ATM band would store a negative
    volatility."""
    chain = _chain(expiries=((20, 25.0),))
    key = "2026-10-01:20"
    chain["putExpDateMap"][key]["100.0"] = [{"volatility": -999.0}]
    got = ivh.atm_iv_ladder(chain)
    assert got and got[0]["atm_iv"] == pytest.approx(25.0)


def test_an_expiry_with_no_usable_iv_is_dropped_not_zeroed():
    chain = _chain(expiries=((20, 25.0), (27, 26.0)))
    chain["putExpDateMap"]["2026-10-01:27"] = {"100.0": [{"volatility": None}]}
    chain["callExpDateMap"]["2026-10-01:27"] = {"100.0": [{"volatility": None}]}
    assert [e["dte"] for e in ivh.atm_iv_ladder(chain)] == [20]


def test_a_zero_dte_expiry_is_excluded():
    """``constant_maturity_iv`` already drops ``dte <= 0``; the ladder does too, so
    the two cannot disagree about what a tenor is."""
    assert [e["dte"] for e in ivh.atm_iv_ladder(_chain(expiries=((0, 40.0),
                                                                 (27, 26.0))))] == [27]


# ── cm30_from_chain: the basis is the point ─────────────────────────────────

def test_a_bracketing_ladder_reports_interpolated():
    cm30, basis = ivh.cm30_from_chain(_chain())
    assert basis == "interpolated"
    assert 25.0 < cm30 < 27.0


def test_an_exact_30_dte_expiry_reports_exact():
    cm30, basis = ivh.cm30_from_chain(_chain(expiries=((20, 25.0), (30, 26.5),
                                                       (41, 27.0))))
    assert basis == "exact"
    assert cm30 == pytest.approx(26.5)


def test_a_ladder_entirely_SHORT_of_30_reports_clamped():
    """The `gex_collector`'s today..+7 chain, which is exactly why the snapshot
    does not ride on it."""
    cm30, basis = ivh.cm30_from_chain(_chain(expiries=((2, 40.0), (5, 38.0),
                                                       (7, 36.0))))
    assert basis == "clamped"
    assert cm30 == pytest.approx(36.0)


def test_a_ladder_entirely_BEYOND_30_reports_clamped():
    cm30, basis = ivh.cm30_from_chain(_chain(expiries=((45, 22.0), (70, 23.0))))
    assert basis == "clamped"


def test_a_single_expiry_reports_clamped_not_interpolated():
    """One point cannot bracket a tenor. ``constant_maturity_iv`` returns it
    happily, so the basis is the only thing that can say it is not a 30-day
    reading."""
    cm30, basis = ivh.cm30_from_chain(_chain(expiries=((27, 26.0),)))
    assert basis == "clamped"
    assert cm30 == pytest.approx(26.0)


def test_no_usable_ladder_reports_no_basis_and_no_value():
    assert ivh.cm30_from_chain(None) == (None, None)
    assert ivh.cm30_from_chain({}) == (None, None)


def test_the_value_matches_constant_maturity_iv_on_the_same_ladder():
    """The wrapper must not become a second implementation of the interpolation —
    the total-variance domain is the part that is easy to get wrong."""
    chain = _chain()
    ladder = ivh.atm_iv_ladder(chain)
    assert ivh.cm30_from_chain(chain)[0] == pytest.approx(
        ivh.constant_maturity_iv(ladder))


def test_interpolation_really_happens_in_total_variance_space():
    """A sanity check on the inherited maths, independent of the implementation:
    for a 20/40-DTE pair at 20/30 vol, the 30-day total variance is the midpoint,
    so cm30 = sqrt((0.20^2*20 + 0.30^2*40) / 2 / 30)."""
    cm30, basis = ivh.cm30_from_chain(_chain(expiries=((20, 20.0), (40, 30.0))))
    expected = math.sqrt(((0.20 ** 2 * 20) + (0.30 ** 2 * 40)) / 2.0 / 30.0) * 100
    assert basis == "interpolated"
    assert cm30 == pytest.approx(expected)


# ── the store still works from its new home ─────────────────────────────────

def test_a_snapshot_round_trips_and_the_rank_reports_its_maturity(tmp_path):
    conn = ivh.init_db(tmp_path / "iv.db")
    try:
        for i in range(25):
            ivh.record_snapshot(conn, "TEST", 100.0, {"cm30_iv": 20.0 + i},
                                {}, snapshot_date=f"2026-08-{i + 1:02d}")
        assert ivh.snapshot_count(conn, "TEST") == 25
        got = ivh.iv_rank(conn, "TEST", 32.0)
        assert got["samples"] == 25 and got["sufficient"] is True
        assert got["rank"] == pytest.approx(50.0)
    finally:
        ivh.close_db(conn)


def test_the_rank_says_it_is_NOT_sufficient_below_the_sample_floor(tmp_path):
    """The honest state for the next year: a number exists but must not be acted
    on. 7 rows is where the real store stands today."""
    conn = ivh.init_db(tmp_path / "iv.db")
    try:
        for i in range(7):
            ivh.record_snapshot(conn, "TEST", 100.0, {"cm30_iv": 20.0 + i},
                                {}, snapshot_date=f"2026-08-{i + 1:02d}")
        got = ivh.iv_rank(conn, "TEST", 22.0)
        assert got["samples"] == 7
        assert got["sufficient"] is False
    finally:
        ivh.close_db(conn)


def test_recording_twice_on_one_date_upserts_rather_than_duplicating(tmp_path):
    """The scan writes on every pass, so the day's LAST reading is what is kept."""
    conn = ivh.init_db(tmp_path / "iv.db")
    try:
        ivh.record_snapshot(conn, "TEST", 100.0, {"cm30_iv": 20.0}, {},
                            snapshot_date="2026-08-01")
        ivh.record_snapshot(conn, "TEST", 101.0, {"cm30_iv": 23.0}, {},
                            snapshot_date="2026-08-01")
        assert ivh.snapshot_count(conn, "TEST") == 1
        row = conn.execute("SELECT cm30_iv, spot FROM iv_snapshots").fetchone()
        assert row["cm30_iv"] == pytest.approx(23.0) and row["spot"] == pytest.approx(101.0)
    finally:
        ivh.close_db(conn)


# ── the realized-vol backfill takes BOTH real price-history shapes ──────────

def _candles(n=60, start=100.0):
    """The RAW Schwab ``/pricehistory`` payload — epoch-MS stamps, one bar a day.

    This is what ``scanner_engine.fetch_price_history`` returns, and it is the
    shape `backfill_rv` used to reject silently.
    """
    day_ms = 86_400_000
    return {"candles": [
        {"open": start + i, "high": start + i + 1, "low": start + i - 1,
         "close": start + i * (1.0 if i % 2 else -0.5),
         "datetime": (i + 1) * day_ms}
        for i in range(n)]}


def test_the_backfill_accepts_the_raw_schwab_payload(tmp_path):
    """⚠ The bug this closes: the Deep Dive holds a DataFrame while the SCANNER
    holds the raw payload, so the DataFrame-only code raised ``AttributeError`` on
    ``.empty`` and the caller's guard swallowed it — a backfill that read like a
    feature and wrote nothing at all."""
    conn = ivh.init_db(tmp_path / "iv.db")
    try:
        n = ivh.backfill_rv(conn, "TEST", _candles())
        assert n > 0
        rows = conn.execute("SELECT bar_date, close, rvol_20d FROM rv_history "
                            "ORDER BY bar_date").fetchall()
        assert len(rows) == n
        assert all(r["rvol_20d"] is not None for r in rows)
    finally:
        ivh.close_db(conn)


def test_the_backfill_accepts_a_bare_candle_list_too():
    """Callers differ on whether they unwrap the envelope; both are real."""
    frame = ivh._as_candle_frame(_candles()["candles"])
    assert frame is not None and len(frame) == 60


def test_the_backfill_still_accepts_a_DataFrame(tmp_path):
    """The Deep Dive's shape must keep working — it is the caller that has
    populated this store for its whole life."""
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    frame = pd.DataFrame({"close": [100.0 + (i % 7) for i in range(60)]}, index=idx)
    conn = ivh.init_db(tmp_path / "iv.db")
    try:
        assert ivh.backfill_rv(conn, "TEST", frame) > 0
    finally:
        ivh.close_db(conn)


def test_the_bar_date_is_the_session_date_not_the_day_before(tmp_path):
    """Daily Schwab bars are stamped midnight CENTRAL (05:00/06:00 UTC), so the
    naive-UTC conversion keeps the session date. Converting the other way would
    shift every bar a day and silently mis-align RV against IV."""
    frame = ivh._as_candle_frame(
        {"candles": [{"close": 100.0, "datetime": 1_767_243_600_000}]})
    assert frame is not None
    assert frame.index[0].strftime("%Y-%m-%d") == "2026-01-01"


@pytest.mark.parametrize("bad", [None, {}, {"candles": []}, [], "nope", 7,
                                 {"candles": [{"close": None, "datetime": 1}]},
                                 {"candles": [{"close": 1.0}]},
                                 {"candles": [{"close": float("nan"),
                                               "datetime": 1}]}])
def test_an_unusable_history_yields_no_frame_rather_than_raising(bad):
    assert ivh._as_candle_frame(bad) is None


def test_a_non_positive_close_is_dropped():
    frame = ivh._as_candle_frame({"candles": [
        {"close": 0.0, "datetime": 86_400_000},
        {"close": 100.0, "datetime": 172_800_000}]})
    assert frame is not None and len(frame) == 1
