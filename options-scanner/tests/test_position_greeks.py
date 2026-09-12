"""C4: net Greeks per position, from the chain the repricer already fetched.

Design: docs/plans/2026-09-12-book-greeks-design.md.

``paper_positions`` stored the SHORT leg's delta and nothing else greek — enough
for the delta stop, and not enough for a book read: a −0.20 short against a −0.08
long is +0.12 net, so summing short deltas overstates the book's direction by the
whole long-leg offset.

⚠ The signs are the part a bug would hide. Signs follow the POSITION, not the
option: a short leg contributes MINUS its greek, so a put credit spread must come
out net positive delta, negative gamma, POSITIVE theta and negative vega. A sign
error would render a premium-selling book as long volatility.
"""
import pytest

import signal_repricer as sr


def _chain(short_strike=500.0, long_strike=495.0, right="put", **over):
    """A two-strike chain shaped like the proxy's, with full Greeks."""
    short_ctr = {"bid": 1.00, "ask": 1.10, "delta": -0.20, "gamma": 0.010,
                 "theta": -0.05, "vega": 0.12}
    long_ctr = {"bid": 0.40, "ask": 0.50, "delta": -0.08, "gamma": 0.006,
                "theta": -0.03, "vega": 0.08}
    short_ctr.update(over.pop("short", {}) or {})
    long_ctr.update(over.pop("long", {}) or {})
    leg_map = {"2026-10-16:34": {f"{short_strike:.1f}": [short_ctr],
                                 f"{long_strike:.1f}": [long_ctr]}}
    key = "putExpDateMap" if right == "put" else "callExpDateMap"
    out = {"underlyingPrice": 505.0, "putExpDateMap": {}, "callExpDateMap": {}}
    out[key] = leg_map
    out.update(over)
    return out


def _pcs(**over):
    t = {"symbol": "SPY", "strategy": "PCS", "short_strike": 500.0,
         "long_strike": 495.0, "width": 5.0, "entry_credit": 0.60,
         "expiration": "2026-10-16"}
    t.update(over)
    return t


def _raiser(*a, **k):
    raise RuntimeError("boom")


# ── the signs, which are the whole point ────────────────────────────────────

def test_a_put_credit_spread_is_net_LONG_delta():
    """It profits as the underlying rises. Short −0.20 minus long −0.08 = +0.12."""
    g = sr.position_greeks(_pcs(), _chain())
    assert g["net_delta"] == pytest.approx(0.12)


def test_a_put_credit_spread_is_net_SHORT_gamma():
    g = sr.position_greeks(_pcs(), _chain())
    assert g["net_gamma"] == pytest.approx(-(0.010 - 0.006))
    assert g["net_gamma"] < 0


def test_a_put_credit_spread_is_net_POSITIVE_theta():
    """⚠ The sanity check that catches an inverted sign: a credit book EARNS time.
    Schwab reports theta as a negative number per option, so a SHORT option
    contributes +0.05."""
    g = sr.position_greeks(_pcs(), _chain())
    assert g["net_theta"] == pytest.approx(0.05 - 0.03)
    assert g["net_theta"] > 0


def test_a_put_credit_spread_is_net_SHORT_vega():
    g = sr.position_greeks(_pcs(), _chain())
    assert g["net_vega"] == pytest.approx(-(0.12 - 0.08))
    assert g["net_vega"] < 0


def test_a_call_credit_spread_is_net_SHORT_delta():
    """The mirror: a CCS profits as the underlying FALLS."""
    chain = _chain(right="call",
                   short={"delta": 0.20, "gamma": 0.010, "theta": -0.05,
                          "vega": 0.12},
                   long={"delta": 0.08, "gamma": 0.006, "theta": -0.03,
                         "vega": 0.08})
    g = sr.position_greeks(_pcs(strategy="CCS", call_short=500.0,
                                call_long=495.0), chain)
    assert g["net_delta"] == pytest.approx(-0.12)
    assert g["net_theta"] > 0


def test_a_single_leg_short_put_is_the_bare_leg_negated():
    """One short option: net delta +0.20 (long the underlying), theta +0.05."""
    g = sr.position_greeks(_pcs(strategy="SHORT_PUT", long_strike=None), _chain())
    assert g["net_delta"] == pytest.approx(0.20)
    assert g["net_theta"] == pytest.approx(0.05)
    assert g["net_vega"] == pytest.approx(-0.12)


def test_an_iron_condor_sums_all_four_legs():
    chain = {
        "underlyingPrice": 505.0,
        "putExpDateMap": {"2026-10-16:34": {
            "500.0": [{"delta": -0.20, "gamma": 0.010, "theta": -0.05,
                       "vega": 0.12}],
            "495.0": [{"delta": -0.08, "gamma": 0.006, "theta": -0.03,
                       "vega": 0.08}]}},
        "callExpDateMap": {"2026-10-16:34": {
            "515.0": [{"delta": 0.20, "gamma": 0.010, "theta": -0.05,
                       "vega": 0.12}],
            "520.0": [{"delta": 0.08, "gamma": 0.006, "theta": -0.03,
                       "vega": 0.08}]}}}
    g = sr.position_greeks(
        _pcs(strategy="IC", short_strike=500.0, long_strike=495.0,
             call_short=515.0, call_long=520.0), chain)
    # A balanced condor is delta-flat and doubly short vol.
    assert g["net_delta"] == pytest.approx(0.0)
    assert g["net_theta"] == pytest.approx(2 * (0.05 - 0.03))
    assert g["net_vega"] == pytest.approx(-2 * (0.12 - 0.08))


# ── absence: None means "not computed", never zero ─────────────────────────

def test_an_unquotable_leg_yields_None_not_zero():
    """⚠ A zero delta is a REAL reading (a balanced condor), so a position whose
    chain was unusable must not join the book's sum as flat."""
    chain = _chain()
    chain["putExpDateMap"]["2026-10-16:34"].pop("495.0")
    g = sr.position_greeks(_pcs(), chain)
    assert g["net_delta"] is None and g["net_theta"] is None


def test_a_leg_missing_ONE_greek_yields_None_for_that_greek_only():
    """Partial data is common off-hours. Delta may be present while vega is not,
    and dropping the whole position would lose a usable direction reading."""
    g = sr.position_greeks(_pcs(), _chain(long={"vega": None}))
    assert g["net_delta"] == pytest.approx(0.12)
    assert g["net_vega"] is None


@pytest.mark.parametrize("bad", [None, {}, "nope", 7, {"putExpDateMap": {}}])
def test_an_unusable_chain_yields_all_None_rather_than_raising(bad):
    g = sr.position_greeks(_pcs(), bad)
    assert set(g) == {"net_delta", "net_gamma", "net_theta", "net_vega"}
    assert all(v is None for v in g.values())


def test_an_unknown_strategy_yields_all_None():
    """It must not guess a leg layout it does not know."""
    g = sr.position_greeks(_pcs(strategy="CALENDAR"), _chain())
    assert all(v is None for v in g.values())


def test_a_non_finite_greek_is_refused():
    """The documented NaN trap: a NaN would propagate into the book's sum and make
    every comparison against it False."""
    g = sr.position_greeks(_pcs(), _chain(short={"delta": float("nan")}))
    assert g["net_delta"] is None


def test_it_never_raises_on_a_broken_trade_dict():
    for bad in (None, {}, {"strategy": "PCS"}):
        g = sr.position_greeks(bad, _chain())
        assert all(v is None for v in g.values())


# ── the repricer hands them back with the mark ──────────────────────────────

def test_reprice_swing_carries_the_greeks(monkeypatch):
    import datetime as dt
    monkeypatch.setattr(sr, "_fetch_chain", lambda *a, **k: _chain())
    got = sr.reprice_swing(_pcs(), client=object(), today=dt.date(2026, 9, 12))
    assert got["error"] is None, got
    assert got["net_delta"] == pytest.approx(0.12)
    assert got["net_theta"] == pytest.approx(0.02)


def test_a_greek_failure_does_not_cost_the_MARK(monkeypatch):
    """The mark is the money path; the Greeks are a display number. A failure in
    the new code must not lose the reprice."""
    import datetime as dt
    monkeypatch.setattr(sr, "_fetch_chain", lambda *a, **k: _chain())
    monkeypatch.setattr(sr, "position_greeks", _raiser)
    got = sr.reprice_swing(_pcs(), client=object(), today=dt.date(2026, 9, 12))
    assert got["error"] is None
    assert got["unrealized_pnl"] is not None
    assert got.get("net_delta") is None


def test_an_expired_trade_reports_no_greeks():
    import datetime as dt
    got = sr.reprice_swing(_pcs(expiration="2020-01-01"), client=object(),
                           today=dt.date(2026, 9, 12))
    assert got["error"] == "expired"
    assert got.get("net_delta") is None
