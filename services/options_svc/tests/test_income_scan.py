"""The INCOME window: 30-45 DTE, two-sided, plus the cash-secured put.

The two-sidedness is the point — see
``docs/plans/2026-09-05-income-window-and-share-inventory-design.md``. A test
that only asserts PCS would pass on a long-only implementation, which is
exactly the regression this file exists to prevent.

⚠ **Two vacuity traps are guarded explicitly here, because this repo has been
bitten by both.** (1) "drops the structures this window does not want" passes
trivially if the builders never produced them, so
``test_the_unwanted_structures_are_really_built_before_they_are_dropped``
asserts the unfiltered call DOES produce them, in the same test that asserts
the filtered one does not. (2) Every assertion over a ``SHORT_PUT`` row raises
rather than asserts if the stubbed chain carries no put near 0.28 delta, so
``test_the_fixture_actually_yields_candidates`` pins the fixture itself.
"""
import datetime as dt

import pytest

import services.options_svc.compute as compute


# The window this fixture sits inside. A REAL forward date, because
# ``strategy_scanner._assemble`` recomputes dte from ``date.today()`` and
# ``check_earnings_conflict`` compares against today — a frozen 2026-07-15 would
# make both meaningless the moment it drifted into the past, which is precisely
# how three tests in this repo came to take the same early-out and assert
# nothing.
_DTE = 35
_EXP = (dt.date.today() + dt.timedelta(days=_DTE)).isoformat()


def _income_chain():
    """A minimal 35-DTE chain carrying the greeks ``extract_options`` needs.

    The 0.28-delta wings matter: ``build_directional`` targets
    ``_SHORT_DELTA = 0.28``, so without them ``SHORT_PUT`` is never built and
    every assertion about the cash-secured put would raise on an empty list
    rather than fail on a wrong value.
    """
    def leg(delta, mark):
        return [{"delta": delta, "mark": mark, "bid": mark - 0.05, "ask": mark + 0.05,
                 "theta": -0.03, "vega": 0.10, "gamma": 0.01, "volatility": 22.0,
                 "totalVolume": 1000, "openInterest": 5000}]
    key = f"{_EXP}:{_DTE}"
    return {
        "underlyingPrice": 540.0,
        "callExpDateMap": {key: {
            "535.0": leg(0.60, 8.0), "545.0": leg(0.40, 4.0),
            "555.0": leg(0.28, 2.0),
        }},
        "putExpDateMap": {key: {
            "545.0": leg(-0.60, 8.0), "535.0": leg(-0.40, 4.0),
            "525.0": leg(-0.28, 2.0),
        }},
    }


@pytest.fixture
def income_seams(monkeypatch):
    """Stub the seven I/O seams ``swing_scan`` owns; return the recorder dict.

    Copied from ``test_compute.py``'s swing-scan setup rather than reinvented —
    its ``_screen`` stub already records the ``kind`` positional, which is the
    argument this window changes.

    The quality cut is dropped (as ``unfiltered_swing`` does for the swing
    tests) because this chain grades its DIRECTIONAL candidates and the adapted
    PCS/CCS Weak: with the production cut in force every assertion below would
    bite an empty list. The cut has its own tests; these are about the window.
    """
    calls = {}
    chain = _income_chain()

    monkeypatch.setattr(compute.se, "fetch_option_chain",
                        lambda client, symbol, from_date=None, to_date=None: (
                            calls.__setitem__("to_date", to_date), chain)[1])
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda symbol: {"last": 540.0})
    monkeypatch.setattr(compute.se, "fetch_price_history",
                        lambda client, symbol: {"hist": True})
    monkeypatch.setattr(compute.se, "calc_technicals",
                        lambda hist: {"trend": "NEUTRAL", "rsi14": 50,
                                      "price": 540.0, "sma20": 540.0})
    monkeypatch.setattr(compute, "run_iv_analysis",
                        lambda client, symbol, price=None, hist=None, chain=None: {
                            "iv_rank": 50.0,
                            "expected_moves": {"daily": {"move_dollars": 5.0}}})

    def _screen(chain, symbol, dte_min, dte_max, put_d_min, put_d_max,
                call_d_min, call_d_max, min_cr, kind, spot=None,
                daily_expected_move=None, earnings_date=None, **kw):
        calls["screen"] = dict(kind=kind, dte_min=dte_min, dte_max=dte_max,
                               put_d=(put_d_min, put_d_max),
                               call_d=(call_d_min, call_d_max),
                               min_cr=min_cr, spot=spot,
                               earnings_date=earnings_date)
        common = {"symbol": symbol, "expiration": _EXP, "underlying_price": 540.0}
        # BOTH sides, so the two-sidedness assertion is real rather than a
        # statement about a one-sided stub.
        return [
            {**common, "type": "PCS", "short_strike": 525.0, "long_strike": 520.0,
             "short_mark": 1.2, "long_mark": 0.6, "credit": 0.6, "max_loss": 4.4},
            {**common, "type": "CCS", "short_strike": 555.0, "long_strike": 560.0,
             "short_mark": 1.2, "long_mark": 0.6, "credit": 0.6, "max_loss": 4.4},
        ]

    monkeypatch.setattr(compute.se, "screen_spreads", _screen)
    monkeypatch.setattr(compute.se, "build_iron_condors", lambda spreads: [])
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 0.0)
    monkeypatch.setattr(compute, "SWING_EXCLUDED_GRADES", ())
    return calls


# ── the fixture itself, first ───────────────────────────────────────────────

def test_the_fixture_actually_yields_candidates(income_seams):
    """VACUITY GUARD. Every assertion in this file indexes into ``signals``; if
    the chain stopped producing a 0.28-delta put the list would be empty and
    those tests would raise IndexError rather than fail — or, worse, pass while
    asserting over nothing."""
    sigs = compute.income_scan("AAPL")["signals"]
    assert sigs, "the stubbed chain must produce candidates or nothing below bites"
    assert {s["type"] for s in sigs} == {"PCS", "CCS", "SHORT_PUT"}


# ── the trade type reaches the engine ───────────────────────────────────────

def test_income_scan_screens_as_INCOME_not_SWING(income_seams):
    """The one thread that makes the earnings gate, the liquidity floor and the
    calibration bucket all key off INCOME."""
    compute.income_scan("AAPL")
    assert income_seams["screen"]["kind"] == "INCOME"


def test_income_scan_screens_the_30_45_window(income_seams):
    compute.income_scan("AAPL")
    assert (income_seams["screen"]["dte_min"],
            income_seams["screen"]["dte_max"]) == (30, 45)


def test_income_scan_screens_a_delta_band_below_the_premium_ceiling(income_seams):
    """``screen_spreads`` in PREMIUM mode drops any short with
    ``abs(delta) > MAX_ENTRY_SHORT_DELTA``. The config's directional bands sit
    entirely ABOVE that ceiling, and the drop increments no reject counter — so
    using them here would have emptied the window silently, forever."""
    import scanner_engine as se
    compute.income_scan("AAPL")
    put_d = income_seams["screen"]["put_d"]
    call_d = income_seams["screen"]["call_d"]
    assert max(abs(d) for d in put_d + call_d) < se.MAX_ENTRY_SHORT_DELTA
    assert put_d == compute.INCOME_PUT_DELTA
    assert call_d == compute.INCOME_CALL_DELTA


def test_income_scan_fetches_only_the_one_chain_it_needs(income_seams):
    """The chain fetch is bounded ``today … dte_max + 2``, so the window costs
    one chain per symbol — the call budget the design costed."""
    compute.income_scan("AAPL")
    assert income_seams["to_date"] == dt.date.today() + dt.timedelta(days=47)


# ── the structures ──────────────────────────────────────────────────────────

def test_income_scan_emits_both_spread_sides(income_seams):
    kinds = {c["type"] for c in compute.income_scan("AAPL")["signals"]}
    assert "PCS" in kinds
    assert "CCS" in kinds


def test_income_scan_emits_the_cash_secured_put(income_seams):
    csps = [c for c in compute.income_scan("AAPL")["signals"]
            if c["type"] == "SHORT_PUT"]
    assert csps, "the cash-secured put is the whole point of a 30-45 DTE window"
    csp = csps[0]
    assert len(csp["legs"]) == 1
    assert csp["legs"][0]["side"] == "short"
    assert csp["legs"][0]["kind"] == "put"
    assert csp["capital"] > 0


def test_the_unwanted_structures_are_really_built_before_they_are_dropped(income_seams):
    """VACUITY GUARD + the assertion itself, deliberately in ONE test so they
    cannot drift apart. A long call at 35 DTE is a different thesis and
    SHORT_CALL is undefined risk — neither belongs ranked against the premium
    core — but "they are absent" only means something once "the builders emit
    them" is shown on the same fixture."""
    unwanted = {"LONG_CALL", "LONG_PUT", "SHORT_CALL", "BULL_CALL", "BEAR_PUT"}

    unfiltered = compute.swing_scan(
        "AAPL", compute.INCOME_DTE_MIN, compute.INCOME_DTE_MAX,
        *compute.INCOME_PUT_DELTA, *compute.INCOME_CALL_DELTA, 0.12,
        families=("VERTICAL", "DIRECTIONAL"))
    built = {s["type"] for s in unfiltered["signals"]}
    assert unwanted <= built, (
        "the builders must emit these for the filter assertion to mean anything; "
        f"got {sorted(built)}")

    kept = {s["type"] for s in compute.income_scan("AAPL")["signals"]}
    assert not (kept & unwanted)


def test_the_structures_filter_runs_before_the_quality_cut(income_seams, monkeypatch):
    """``filtered_out`` is rendered as "the quality bar removed N". A structure
    this window never wanted must not be counted there, or an empty table would
    explain itself with the wrong reason."""
    # Put the production cut back so filtered_out is non-trivial, and pin that
    # the count never exceeds what a scan of only the wanted structures could
    # possibly drop.
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 100.0)
    out = compute.income_scan("AAPL")
    assert out["signals"] == []
    assert out["filtered_out"] == 3, "PCS + CCS + SHORT_PUT scored, all cut"


# ── the earnings gate ───────────────────────────────────────────────────────

def test_a_symbol_the_calendar_cannot_speak_for_is_flagged_not_dropped(income_seams):
    """There is no Alpha Vantage key in most checkouts, so ``not_listed`` is the
    COMMON case. Dropping on it would empty the scan; emitting silently would
    imply a check that never ran."""
    sigs = compute.income_scan("NOSUCH")["signals"]
    assert sigs, "not_listed must not empty the scan"
    assert all(s["earnings_status"] == "not_listed" for s in sigs)
    assert income_seams["screen"]["earnings_date"] is None


def test_a_known_clear_symbol_is_stamped_none_scheduled(income_seams, monkeypatch):
    monkeypatch.setattr(compute, "_income_earnings",
                        lambda symbol: ("none_scheduled", None))
    sigs = compute.income_scan("AAPL")["signals"]
    assert sigs
    assert all(s["earnings_status"] == "none_scheduled" for s in sigs)


def test_a_dated_report_reaches_screen_spreads(income_seams, monkeypatch):
    """``screen_spreads`` owns the spread-side gate (it drops the conflicting
    expiration). It can only do that if the date arrives."""
    report = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(compute, "_income_earnings",
                        lambda symbol: ("upcoming", report))
    out = compute.income_scan("AAPL")
    assert income_seams["screen"]["earnings_date"] == report
    assert all(s["earnings_status"] == "upcoming" for s in out["signals"])


def test_a_dated_report_also_removes_the_cash_secured_put(income_seams, monkeypatch):
    """``build_directional`` does NOT consult the earnings calendar — only
    ``screen_spreads`` does. Without an explicit gate a 35-DTE cash-secured put
    sails straight over the report the spreads were just protected from, which
    is the easiest half of this task to leave undone."""
    report = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(compute, "_income_earnings",
                        lambda symbol: ("upcoming", report))
    kinds = {s["type"] for s in compute.income_scan("AAPL")["signals"]}
    assert "SHORT_PUT" not in kinds


def test_a_report_after_the_expiration_leaves_the_cash_secured_put_alone(
        income_seams, monkeypatch):
    """VACUITY GUARD for the test above: prove the single survives a
    non-conflicting date, or "SHORT_PUT is absent" would be satisfied by a gate
    that drops it unconditionally."""
    report = (dt.date.today() + dt.timedelta(days=_DTE + 20)).isoformat()
    monkeypatch.setattr(compute, "_income_earnings",
                        lambda symbol: ("upcoming", report))
    kinds = {s["type"] for s in compute.income_scan("AAPL")["signals"]}
    assert "SHORT_PUT" in kinds


# ── the shared read helper ──────────────────────────────────────────────────

def test_income_earnings_reads_the_three_states(tmp_path):
    """The helper consumes ``shared.earnings.coverage``'s vocabulary directly
    rather than reducing it to a boolean — ``none_scheduled`` and ``not_listed``
    both leave the date None, and conflating them is what makes the gate fail
    open silently."""
    from shared import earnings as _earn
    db = tmp_path / "earnings.db"
    conn = _earn.init_db(db)
    today = dt.date.today()
    conn.execute("INSERT INTO earnings (symbol, report_date) VALUES (?, ?)",
                 ("AAPL", (today + dt.timedelta(days=12)).isoformat()))
    conn.execute("INSERT INTO earnings (symbol, report_date) VALUES (?, ?)",
                 ("OLD", (today - dt.timedelta(days=30)).isoformat()))
    conn.commit()
    _earn.close_db(conn)

    assert compute._income_earnings("AAPL", db_path=db) == (
        "upcoming", (today + dt.timedelta(days=12)).isoformat())
    assert compute._income_earnings("OLD", db_path=db) == ("none_scheduled", None)
    assert compute._income_earnings("NOSUCH", db_path=db) == ("not_listed", None)


def test_income_earnings_never_raises(tmp_path):
    """A gate that raises costs the user the whole scan. An unreadable store is
    ``not_listed`` — "we do not know" — which is the honest answer and the one
    the row is stamped with."""
    bad = tmp_path / "nope" / "cannot" / "exist.db"
    bad.parent.mkdir(parents=True)
    bad.write_text("not a database")
    assert compute._income_earnings("AAPL", db_path=bad) == ("not_listed", None)


# ── the nine existing swing_scan call sites ─────────────────────────────────

def test_swing_scan_defaults_are_unchanged(income_seams):
    """Nine existing call sites depend on these defaults. A default-args call
    must still screen as SWING, filter no structure and gate no earnings."""
    out = compute.swing_scan("SPY", 30, 45, -0.20, -0.10, 0.10, 0.20, 0.10)
    assert income_seams["screen"]["kind"] == "SWING"
    assert income_seams["screen"]["earnings_date"] is None
    # Nothing was filtered: the unwanted structures are all still present.
    assert {"LONG_CALL", "SHORT_CALL", "BULL_CALL"} <= {
        s["type"] for s in out["signals"]}
    # And no earnings_status is invented on a swing row.
    assert all("earnings_status" not in s for s in out["signals"])
