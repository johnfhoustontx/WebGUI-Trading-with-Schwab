"""``compute.covered_call_candidates`` — calls written against shares actually held.

The 30-45 DTE income window's third product. Unlike the spreads and the
cash-secured put it is not a screen over a universe: it is a screen over the
paper account's OPEN EQUITY LOTS, so the builder takes the lots, the chains and
the spots as arguments and touches neither the proxy nor a database. That is
what makes every rule below testable without a live pass.

⚠ **The hard floor is the design decision** (design doc, Decision 3): a call
struck BELOW cost basis books a guaranteed loss on the shares if it is called
away, and the premium rarely covers it. The builder refuses to emit one — not a
warning, not a score penalty.

⚠ **Vacuity.** "never offers a strike below basis" passes trivially if the
builder returns ``[]`` for everything, and this repo has a documented incident
where three tests passed only because every fixture took the same early-out. So
``test_a_lot_below_spot_actually_produces_candidates`` runs FIRST and asserts the
fixture is productive; the floor tests then have something to be negative about.

⚠ **Units.** Every dollar figure on this board is PER CONTRACT, because that is
what ``webgui/pages/options/income.py`` renders (``net_credit``, never the
per-share ``credit`` an adapted spread also carries). Both ratios below are
therefore built from ``net_credit`` and a ``cost_basis * 100`` denominator.
"""
import datetime as _dt
import math

import pytest

from services.options_svc import compute


# ── the fixture chain ───────────────────────────────────────────────────────
# Expirations are RELATIVE TO TODAY, not literals. ``extract_options`` filters on
# the DTE embedded in the expiry key and the earnings gate compares real dates,
# so a hardcoded 2026-10-16 would drift out of the window and quietly turn every
# assertion below into a test of the empty case — the failure mode this repo
# fixed across options-scanner by deriving fixtures from the constant.
_TODAY = _dt.date.today()
_DTE_NEAR, _DTE_FAR = 35, 42
_EXP_NEAR = (_TODAY + _dt.timedelta(days=_DTE_NEAR)).isoformat()
_EXP_FAR = (_TODAY + _dt.timedelta(days=_DTE_FAR)).isoformat()

# strike -> (delta, mark). Spot is 100. The income convention is a ~0.20 short
# delta (the midpoint of compute.INCOME_CALL_DELTA), which lands on the 110
# strike here — deliberately, so a floor at 115 has to MOVE the pick rather than
# merely agree with it. That is what makes the mutation check meaningful.
_NEAR_CALLS = {
    90.0: (0.75, 11.50),
    95.0: (0.62, 7.80),
    100.0: (0.50, 4.90),
    105.0: (0.36, 2.90),
    110.0: (0.24, 1.60),
    115.0: (0.15, 0.85),
    120.0: (0.09, 0.42),
}
# The far expiry carries fatter marks (more time) so a row from one expiry can
# never be mistaken for a row from the other.
_FAR_CALLS = {k: (d, round(m * 1.25, 2)) for k, (d, m) in _NEAR_CALLS.items()}

_SPOT = 100.0


def _contract(strike, delta, mark):
    """One Schwab chain contract, with the fields ``extract_options`` reads."""
    return {
        "strikePrice": strike, "delta": delta, "mark": mark,
        "bid": round(mark - 0.05, 2), "ask": round(mark + 0.05, 2),
        "theta": -0.03, "vega": 0.11, "gamma": 0.01,
        # Schwab reports ``volatility`` as a PERCENT (28.5, not 0.285) — the
        # documented percent/decimal trap. The fixture carries it in Schwab's
        # units so the builder's own conversion is exercised, not bypassed.
        "volatility": 28.0,
        "totalVolume": 400, "openInterest": 2500,
    }


def _exp_map(calls, exp, dte):
    return {f"{exp}:{dte}": {str(k): [_contract(k, d, m)]
                             for k, (d, m) in calls.items()}}


def _chain(near=True, far=True):
    call_map = {}
    if near:
        call_map.update(_exp_map(_NEAR_CALLS, _EXP_NEAR, _DTE_NEAR))
    if far:
        call_map.update(_exp_map(_FAR_CALLS, _EXP_FAR, _DTE_FAR))
    return {"symbol": "AAPL", "underlyingPrice": _SPOT,
            "callExpDateMap": call_map, "putExpDateMap": {}}


@pytest.fixture
def chain():
    return _chain()


@pytest.fixture
def near_only():
    """One expiry, so a per-lot assertion reads one row rather than two."""
    return _chain(far=False)


def _lot(shares=100, basis=95.0, symbol="AAPL", lot_id=1):
    return {"lot_id": lot_id, "symbol": symbol, "shares": shares,
            "cost_basis": basis, "status": "OPEN", "source": "assignment"}


def _run(lots, chain, spot=_SPOT, earnings=None):
    return compute.covered_call_candidates(
        lots, chains={"AAPL": chain}, spots={"AAPL": spot}, earnings=earnings)


# ── the vacuity guard: the fixture must be productive ───────────────────────

def test_a_lot_below_spot_actually_produces_candidates(near_only):
    """Runs first ON PURPOSE. Every floor test below is a negative assertion,
    and a negative assertion over an always-empty builder proves nothing."""
    out = _run([_lot(basis=95.0)], near_only)

    assert out, "the fixture must produce a candidate or the floor tests are vacuous"
    assert out[0]["symbol"] == "AAPL"
    assert out[0]["covered"] is True
    # The unconstrained income pick: |0.24 - 0.20| beats |0.15 - 0.20|.
    assert out[0]["short_strike"] == 110.0


def test_one_candidate_per_expiry_in_the_window(chain):
    """The horizon is the reader's choice; the strike is not (the ~0.20 delta
    convention pins it). Bounded at one row per expiry rather than one per
    eligible strike, which would swamp a board picked from by hand."""
    out = _run([_lot(basis=95.0)], chain)

    assert sorted(c["expiration"] for c in out) == sorted([_EXP_NEAR, _EXP_FAR])


# ── the hard floor ──────────────────────────────────────────────────────────

def test_never_offers_a_strike_below_cost_basis(near_only):
    """A basis ABOVE spot is the interesting case: the lot is underwater, and a
    delta-only pick would sell the 110 — booking a $500 loss on the shares. The
    eligible ladder still has strikes above 115 further out."""
    out = _run([_lot(basis=115.0)], near_only)

    assert out, "an underwater lot still has strikes at or above its basis"
    assert all(c["short_strike"] >= 115.0 for c in out)
    # And it MOVED: the unconstrained pick is 110 (see the test above).
    assert out[0]["short_strike"] == 115.0


def test_a_basis_above_every_strike_yields_nothing(near_only):
    """No eligible strike is an empty answer, never a below-basis fallback."""
    assert _run([_lot(basis=500.0)], near_only) == []


# ── the two numbers that decide a covered call ──────────────────────────────

def test_reports_yield_on_cost_and_total_return_if_called(near_only):
    """Hand figures, not a restatement of the implementation.

    basis 95, strike 110, mark 1.60 -> net_credit 160.00 per contract.
      yield_on_cost          = 160 / 9500                = 0.01684210...
      total_return_if_called = (1500 + 160) / 9500       = 0.17473684...
    """
    c = _run([_lot(basis=95.0)], near_only)[0]

    assert c["net_credit"] == pytest.approx(160.0)
    assert c["yield_on_cost"] == pytest.approx(160.0 / 9500.0)
    assert c["total_return_if_called"] == pytest.approx(1660.0 / 9500.0)


def test_the_ratios_are_per_contract_not_per_share(near_only):
    """The trap the income page was rebuilt around: ``credit`` on an adapted
    spread is per-SHARE (1.60) while ``net_credit`` is per-CONTRACT (160.00).
    A yield built on the per-share number is 100x too small."""
    c = _run([_lot(basis=95.0)], near_only)[0]

    assert c["yield_on_cost"] == pytest.approx(c["net_credit"] / (95.0 * 100))
    assert c["yield_on_cost"] > 0.01, "a per-share credit would give ~0.00017"


@pytest.mark.parametrize("basis", [0.0, -5.0, float("nan"), float("inf"), None, "x"])
def test_a_basis_that_is_not_a_reading_yields_none_never_a_number(basis):
    """``fmt``-style strictness on BOTH operands. A NaN reaching a ranking
    comparison is this repo's most-documented bug class, and a plausible 0.0 is
    worse than a None because it sorts."""
    assert compute.yield_on_cost(160.0, basis) is None
    assert compute.total_return_if_called(160.0, 110.0, basis) is None


@pytest.mark.parametrize("credit", [float("nan"), float("inf"), None, "x"])
def test_a_credit_that_is_not_a_reading_yields_none(credit):
    assert compute.yield_on_cost(credit, 95.0) is None
    assert compute.total_return_if_called(credit, 110.0, 95.0) is None


def test_a_lot_with_an_unusable_basis_produces_no_candidate(near_only):
    """There is no floor to apply against a basis you cannot read, so the lot is
    skipped whole rather than emitted with two None ratios."""
    assert _run([_lot(basis=0.0)], near_only) == []
    assert _run([_lot(basis=float("nan"))], near_only) == []


# ── sizing ──────────────────────────────────────────────────────────────────

def test_sizes_to_whole_lots_only(near_only):
    """149 shares covers one contract, not 1.49."""
    out = _run([_lot(shares=149)], near_only)

    assert out
    assert all(c["quantity"] == 1 for c in out)


def test_sizes_to_the_number_of_whole_hundreds(near_only):
    assert _run([_lot(shares=350)], near_only)[0]["quantity"] == 3


def test_fewer_than_a_hundred_shares_is_not_a_covered_call(near_only):
    """99 shares cannot cover a contract, so there is no candidate at all —
    not a candidate with quantity 0."""
    assert _run([_lot(shares=99)], near_only) == []


def test_the_economics_stay_per_contract_whatever_the_lot_size(near_only):
    """``quantity`` says how many contracts the lot supports; the dollars stay
    per contract so the row compares against the spreads on one scale."""
    one = _run([_lot(shares=100)], near_only)[0]
    three = _run([_lot(shares=300)], near_only)[0]

    assert three["quantity"] == 3
    assert three["net_credit"] == one["net_credit"]
    assert three["capital"] == one["capital"]


# ── the empty cases ─────────────────────────────────────────────────────────

def test_no_lots_means_no_candidates():
    assert compute.covered_call_candidates([], chains={}, spots={}) == []
    assert compute.covered_call_candidates(None, chains=None, spots=None) == []


def test_a_lot_with_no_chain_is_skipped_not_raised(near_only):
    out = compute.covered_call_candidates(
        [_lot(symbol="MSFT")], chains={"AAPL": near_only}, spots={"AAPL": _SPOT})
    assert out == []


# ── the earnings gate ───────────────────────────────────────────────────────

def test_an_expiry_straddling_a_report_is_dropped(near_only):
    """The same gate the rest of the window uses. 30-45 DTE is where it matters
    most: most names report inside any 35-day window."""
    report = (_TODAY + _dt.timedelta(days=20)).isoformat()
    out = _run([_lot()], near_only, earnings={"AAPL": ("upcoming", report)})

    assert out == []


def test_a_report_after_expiry_is_kept_and_stamped(near_only):
    report = (_TODAY + _dt.timedelta(days=60)).isoformat()
    out = _run([_lot()], near_only, earnings={"AAPL": ("upcoming", report)})

    assert out
    assert out[0]["earnings_status"] == "upcoming"


def test_an_unchecked_symbol_is_stamped_not_listed_not_cleared(near_only):
    """``not_listed`` is the COMMON case (no Alpha Vantage key). It must not
    read as a row that passed the check."""
    out = _run([_lot()], near_only, earnings={"AAPL": ("not_listed", None)})

    assert out[0]["earnings_status"] == "not_listed"


# ── the rest of the normalized shape ────────────────────────────────────────

def test_carries_the_normalized_single_leg_shape(near_only):
    c = _run([_lot(basis=95.0)], near_only)[0]

    assert [l["side"] for l in c["legs"]] == ["short"]
    assert c["legs"][0]["kind"] == "call"
    assert c["legs"][0]["strike"] == 110.0
    for key in ("capital", "max_profit", "max_loss", "breakevens", "rr",
                "pop_pct", "dte", "expiration", "type", "id"):
        assert key in c, key
    assert c["type"] == "COVERED_CALL"
    assert c["dte"] == _DTE_NEAR


def test_capital_is_the_cost_of_the_shares_not_a_margin_proxy(near_only):
    """A lone short call is undefined risk and ``payoff_metrics`` would price it
    off a spot*0.20 margin proxy. Covered, the capital committed is the stock."""
    c = _run([_lot(basis=95.0)], near_only)[0]

    assert c["capital"] == pytest.approx(9500.0)
    assert c["max_loss"] < c["capital"], "the credit offsets part of the stock risk"


def test_breakeven_is_the_basis_less_the_premium(near_only):
    c = _run([_lot(basis=95.0)], near_only)[0]

    assert c["breakevens"] == [pytest.approx(95.0 - 1.60)]


def test_max_profit_is_net_of_the_opening_commission(near_only):
    """Assignment costs no closing commission (``commission_for``'s own
    contract), so the called-away path pays for the OPEN leg only."""
    c = _run([_lot(basis=95.0)], near_only)[0]

    assert c["commission"] > 0
    assert c["max_profit"] == pytest.approx(1500.0 + 160.0 - c["commission"])


def test_pop_is_a_percent_read_off_the_breakeven(near_only):
    """P(S_T > breakeven) under the same normal model ``pop_from_payoff`` uses,
    with the leg IV converted out of Schwab's percent units."""
    c = _run([_lot(basis=95.0)], near_only)[0]

    sigma = _SPOT * 0.28 * math.sqrt(_DTE_NEAR / 365.0)
    z = ((95.0 - 1.60) - _SPOT) / sigma
    expected = (1.0 - 0.5 * (1 + math.erf(z / math.sqrt(2)))) * 100.0
    assert c["pop_pct"] == pytest.approx(expected, abs=0.1)


def test_no_spot_means_no_pop_rather_than_a_confident_number(near_only):
    out = compute.covered_call_candidates(
        [_lot(basis=95.0)], chains={"AAPL": near_only}, spots={})

    assert out, "a missing spot must not lose the candidate — only its PoP"
    assert out[0]["pop_pct"] is None


# ── the wiring: covered calls reach the published board ─────────────────────
# ``handlers.publish_income`` owns the impure half — reading the lots, deciding
# which chains it already has, and fetching only the ones it does not. These
# tests pin the API-cost rule, because a naive implementation doubles the pass
# and nothing about the output would say so.

def _bus():
    from shared.bus import Bus
    return Bus(fake=True)


def _spread_row(symbol, score=50.0):
    return {"type": "PCS", "symbol": symbol, "composite_score": score,
            "short_strike": 100.0, "credit": 1.2}


def test_income_scan_hands_back_the_chain_only_when_asked(monkeypatch):
    """The chain rides back IN MEMORY so the covered-call screen can reuse it.
    Off by default: nine call sites want the signals and nothing else, and a
    chain retained for no reason is the payload problem this window is careful
    about."""
    seen = {}

    def _swing(symbol, *a, **kw):
        seen["return_chain"] = kw.get("return_chain")
        out = {"signals": [], "view": {}, "filtered_out": 0}
        if kw.get("return_chain"):
            out["chain"], out["spot"] = {"callExpDateMap": {}}, 123.0
        return out

    monkeypatch.setattr(compute, "swing_scan", _swing)

    plain = compute.income_scan("AAPL")
    assert seen["return_chain"] is False
    assert "chain" not in plain

    asked = compute.income_scan("AAPL", return_chain=True)
    assert asked["chain"] == {"callExpDateMap": {}}
    assert asked["spot"] == 123.0


def test_a_held_watchlist_symbol_costs_no_extra_chain_fetch(monkeypatch, near_only):
    """The API-cost rule. AAPL is scanned anyway, so its chain is REUSED —
    ``income_chain`` (a second round-trip) must not be called for it."""
    from services.options_svc import handlers

    fetched = []
    monkeypatch.setattr(handlers, "_income_lots", lambda: [_lot()])
    monkeypatch.setattr(handlers.compute, "income_chain",
                        lambda sym: fetched.append(sym) or (None, None))
    monkeypatch.setattr(handlers.compute, "income_earnings_map",
                        lambda syms: {s: ("not_listed", None) for s in syms})

    def _scan(sym, **kw):
        out = {"signals": [_spread_row(sym)], "view": {}, "filtered_out": 0}
        if kw.get("return_chain"):
            out["chain"], out["spot"] = near_only, _SPOT
        return out

    monkeypatch.setattr(handlers.compute, "income_scan", _scan)

    bus = _bus()
    handlers.publish_income(bus, symbols=["AAPL", "MSFT"])

    assert fetched == [], "a scanned symbol's chain must be reused, not refetched"
    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert any(r.get("type") == compute.COVERED_CALL_TYPE for r in rows)
    assert any(r.get("type") == "PCS" for r in rows), "the spreads still publish"


def test_a_held_symbol_outside_the_watchlist_is_fetched_once(monkeypatch, near_only):
    """The 0-10 extra calls the design costed. One chain fetch, and it adds ONLY
    covered calls — running the full scan there would silently widen the board's
    universe past the watchlist it is documented to mirror."""
    from services.options_svc import handlers

    fetched = []
    monkeypatch.setattr(handlers, "_income_lots", lambda: [_lot()])
    monkeypatch.setattr(handlers.compute, "income_earnings_map",
                        lambda syms: {s: ("not_listed", None) for s in syms})

    def _chain_for(sym):
        fetched.append(sym)
        return (near_only, _SPOT)

    monkeypatch.setattr(handlers.compute, "income_chain", _chain_for)
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [_spread_row(sym)],
                                           "view": {}, "filtered_out": 0})

    bus = _bus()
    handlers.publish_income(bus, symbols=["MSFT"])

    assert fetched == ["AAPL"]
    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    covered = [r for r in rows if r.get("type") == compute.COVERED_CALL_TYPE]
    assert covered and covered[0]["symbol"] == "AAPL"
    # The universe is unchanged: no AAPL SPREAD row appeared.
    assert [r["symbol"] for r in rows if r.get("type") == "PCS"] == ["MSFT"]


def test_covered_calls_sort_below_a_scored_spread(monkeypatch, near_only):
    """They carry no ``composite_score`` on purpose, and ``_income_rank`` sends
    an absent reading to -inf. Pinning it so a later "fix" that fabricates a
    score has to argue with a test."""
    from services.options_svc import handlers

    monkeypatch.setattr(handlers, "_income_lots", lambda: [_lot()])
    monkeypatch.setattr(handlers.compute, "income_earnings_map",
                        lambda syms: {s: ("not_listed", None) for s in syms})
    monkeypatch.setattr(handlers.compute, "income_chain", lambda sym: (None, None))
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [_spread_row(sym, 61.0)],
                                           "view": {}, "filtered_out": 0,
                                           "chain": near_only, "spot": _SPOT})

    bus = _bus()
    handlers.publish_income(bus, symbols=["AAPL"])

    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert rows[0]["type"] == "PCS"
    assert rows[-1]["type"] == compute.COVERED_CALL_TYPE


def test_no_lots_still_publishes_the_spreads(monkeypatch):
    """A fresh clone has no paper database. The scan must not be lost with it."""
    from services.options_svc import handlers

    monkeypatch.setattr(handlers, "_income_lots", lambda: [])
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [_spread_row(sym)],
                                           "view": {}, "filtered_out": 0})

    bus = _bus()
    handlers.publish_income(bus, symbols=["AAPL"])

    rows = bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert [r["type"] for r in rows] == ["PCS"]


def test_a_failing_lots_read_degrades_audibly_and_still_publishes(monkeypatch,
                                                                 tmp_path):
    """A swallowed failure with no trace is this repo's costliest bug class:
    ``errors`` tells the page, ``_degrade`` tells /health."""
    from services import _degrade
    from services.options_svc import handlers

    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [_spread_row(sym)],
                                           "view": {}, "filtered_out": 0})
    _degrade.reset()

    # A path that is not a database at all -> the read raises inside the guard.
    bad = tmp_path / "not-a-db.sqlite"
    bad.write_text("this is not sqlite")
    assert handlers._income_lots(db_path=str(bad)) == []
    assert _degrade.counts().get("options.income_lots") == 1

    # Bind the real function BEFORE patching the name, or the lambda recurses
    # into its own replacement.
    real = handlers._income_lots
    monkeypatch.setattr(handlers, "_income_lots", lambda: real(db_path=str(bad)))
    bus = _bus()
    handlers.publish_income(bus, symbols=["AAPL"])

    assert bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]


def test_covered_calls_survive_a_builder_failure(monkeypatch, near_only):
    """The spreads are the bulk of the board; a covered-call bug must not cost
    them. One guard, and it speaks."""
    from services import _degrade
    from services.options_svc import handlers

    monkeypatch.setattr(handlers, "_income_lots", lambda: [_lot()])
    monkeypatch.setattr(handlers.compute, "income_earnings_map", lambda syms: {})
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [_spread_row(sym)],
                                           "view": {}, "filtered_out": 0,
                                           "chain": near_only, "spot": _SPOT})

    def _boom(*a, **kw):
        raise RuntimeError("bad lot")

    monkeypatch.setattr(handlers.compute, "covered_call_candidates", _boom)
    _degrade.reset()

    bus = _bus()
    handlers.publish_income(bus, symbols=["AAPL"])

    assert bus.cache_get(handlers.CACHE_INCOME).payload["candidates"]
    assert _degrade.counts().get("options.covered_calls") == 1


def test_the_lots_read_does_not_touch_the_live_store_under_pytest():
    """``db_path=None`` under pytest returns [] rather than connecting: the
    repo-root conftest refuses that connect, and ``fetch_open_lots`` would open
    the real paper account otherwise. Same shape as ``_income_earnings``."""
    from services.options_svc import handlers

    assert handlers._income_lots() == []


def test_a_scanned_symbol_with_no_chain_is_not_refetched(monkeypatch):
    """Off-hours ``income_scan`` returns no chain at all (its own two guards).
    That symbol was already tried; asking again spends a call to be told the
    same thing, and off-hours is exactly when every held name would do it."""
    from services.options_svc import handlers

    fetched = []
    monkeypatch.setattr(handlers, "_income_lots", lambda: [_lot()])
    monkeypatch.setattr(handlers.compute, "income_earnings_map", lambda syms: {})
    monkeypatch.setattr(handlers.compute, "income_chain",
                        lambda sym: fetched.append(sym) or (None, None))
    # The no-chain degrade path: signals empty, and no ``chain`` key at all.
    monkeypatch.setattr(handlers.compute, "income_scan",
                        lambda sym, **kw: {"signals": [], "view": {},
                                           "filtered_out": 0})

    bus = _bus()
    handlers.publish_income(bus, symbols=["AAPL"])

    assert fetched == []
    assert bus.cache_get(handlers.CACHE_INCOME).payload["candidates"] == []
