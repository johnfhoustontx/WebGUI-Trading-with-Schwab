"""Per-action rescue candidate builder economics — pins each builder's numbers."""
from services.options_svc import rescue


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _pos(**kw):
    p = {
        "symbol": "AAPL",
        "strategy": "PCS",
        "short_strike": 100.0,
        "long_strike": 95.0,
        "width": 5.0,
        "expiration": "2026-07-17",
        "entry_credit": 1.50,        # per-contract credit
        "quantity": 1,
        "max_loss_total": 350.0,
    }
    p.update(kw)
    return p


def _mark(**kw):
    m = {
        "current_underlying": 101.0,
        "current_value": 2.50,       # per-contract mid debit to close the spread
        "unrealized_pnl": -100.0,
        "current_short_delta": -0.40,
        "dte": 14,
    }
    m.update(kw)
    return m


def _flat_pricer(*a, **k):
    return 1.00


def _dead_pricer(*a, **k):
    return None


# --------------------------------------------------------------------------- #
# build_close
# --------------------------------------------------------------------------- #
def test_close_candidate_uses_current_value_and_2_legs_commission():
    c = rescue.build_close(_pos(quantity=2), _mark(current_value=2.50), _flat_pricer, ctx={})
    assert c["action"] == "close"
    assert c["apply_kind"] == "execute"
    assert c["gross_cash"] == -2.50 * 100 * 2
    assert c["commission"] == 2 * 2 * 0.65
    assert c["net_cash"] == round(c["gross_cash"] - c["commission"], 2)
    assert c["new_max_loss"] == 0.0
    assert c["new_short_delta"] == 0.0
    assert c["est_fill_legs"] == []


def test_close_none_when_no_current_value():
    assert rescue.build_close(_pos(), _mark(current_value=None), _flat_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_partial_close
# --------------------------------------------------------------------------- #
def test_partial_close_none_when_qty_one():
    assert rescue.build_partial_close(_pos(quantity=1), _mark(), _flat_pricer, ctx={}) is None


def test_partial_close_sheds_half():
    c = rescue.build_partial_close(_pos(quantity=4, max_loss_total=1400.0),
                                   _mark(current_value=2.50), _flat_pricer, ctx={})
    assert c["action"] == "partial_close"
    # close 2 of 4
    assert c["gross_cash"] == -2.50 * 100 * 2
    assert c["commission"] == 2 * 2 * 0.65
    assert c["new_max_loss"] == round(1400.0 * 2 / 4, 2)   # 700.0
    assert c["new_short_delta"] == -0.40


def test_partial_close_none_when_no_current_value():
    assert rescue.build_partial_close(_pos(quantity=2), _mark(current_value=None),
                                      _flat_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_narrow
# --------------------------------------------------------------------------- #
def test_narrow_pcs_reduces_width():
    c = rescue.build_narrow(_pos(), _mark(), _flat_pricer, ctx={})
    assert c["action"] == "narrow"
    # PCS: new_long = short(100) - round(5/2)=2 -> 98 ; new_width = |100-98| = 2
    assert c["new_width"] == 2.0
    assert c["new_width"] < 5.0
    # flat pricer => p_old==p_new => gross 0
    assert c["gross_cash"] == 0.0
    assert c["commission"] == 2 * 1 * 0.65


def test_narrow_none_when_width_one():
    assert rescue.build_narrow(_pos(width=1.0, long_strike=99.0), _mark(),
                               _flat_pricer, ctx={}) is None


def test_narrow_none_for_ic():
    assert rescue.build_narrow(_pos(strategy="IC"), _mark(), _flat_pricer, ctx={}) is None


def test_narrow_none_when_unpriceable():
    assert rescue.build_narrow(_pos(), _mark(), _dead_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_convert_ic
# --------------------------------------------------------------------------- #
def test_convert_ic_collects_credit_and_cuts_max_loss():
    c = rescue.build_convert_ic(_pos(max_loss_total=350.0), _mark(), _flat_pricer, ctx={})
    assert c["action"] == "convert_ic"
    # flat pricer: ps==pl==1.0 => credit_pc=0 => gross 0 ... use a spread pricer instead:
    assert c["gross_cash"] >= 0.0


def test_convert_ic_with_credit_pricer():
    def pricer(sym, exp, right, strike):
        # lower (short) strike richer than higher (long) strike -> credit
        return 2.0 if strike < 105 else 1.0
    c = rescue.build_convert_ic(_pos(max_loss_total=350.0), _mark(current_underlying=101.0),
                                pricer, ctx={})
    assert c["gross_cash"] > 0
    assert c["new_max_loss"] < 350.0
    assert c["warnings"]


def test_convert_ic_none_for_ic():
    assert rescue.build_convert_ic(_pos(strategy="IC"), _mark(), _flat_pricer, ctx={}) is None


def test_convert_ic_none_when_unpriceable():
    assert rescue.build_convert_ic(_pos(), _mark(), _dead_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_convert_butterfly
# --------------------------------------------------------------------------- #
def test_convert_butterfly_credit_cuts_max_loss():
    def pricer(sym, exp, right, strike):
        return 3.0 if strike <= 100 else 1.0
    c = rescue.build_convert_butterfly(_pos(max_loss_total=350.0), _mark(), pricer, ctx={})
    assert c["action"] == "convert_butterfly"
    assert c["gross_cash"] > 0
    assert c["new_max_loss"] < 350.0
    assert c["warnings"]


def test_convert_butterfly_none_for_ic():
    assert rescue.build_convert_butterfly(_pos(strategy="IC"), _mark(),
                                          _flat_pricer, ctx={}) is None


def test_convert_butterfly_none_when_unpriceable():
    assert rescue.build_convert_butterfly(_pos(), _mark(), _dead_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_roll_down
# --------------------------------------------------------------------------- #
def test_roll_down_pcs():
    c = rescue.build_roll_down(_pos(), _mark(current_value=2.50), _flat_pricer, ctx={})
    assert c["action"] == "roll_down"
    # 4 legs commission
    assert c["commission"] == 4 * 1 * 0.65
    # PCS new_short = 100 - 5 = 95 ; new_long = 90
    # gross = (-cv + credit_new) * 100 * qty ; flat pricer credit_new = 0
    assert c["gross_cash"] == round((-2.50 + 0.0) * 100 * 1, 2)
    assert c["new_short_delta"] is None


def test_roll_down_none_when_no_current_value():
    assert rescue.build_roll_down(_pos(), _mark(current_value=None),
                                  _flat_pricer, ctx={}) is None


def test_roll_down_none_when_unpriceable():
    assert rescue.build_roll_down(_pos(), _mark(), _dead_pricer, ctx={}) is None


def test_roll_down_old_leg_unpriceable_still_valid_candidate():
    # pricer returns None for the OLD short (illiquid) but prices everything else.
    # build_roll_down prices the CLOSE legs (old short/long) inline; an illiquid
    # one-sided strike -> None must NOT land a None price in est_fill_legs.
    # (new_short = 500 - round(10) = 490, new_long = 480 -> distinct from olds.)
    def pricer(sym, expiry, right, strike):
        return None if strike == 500.0 else 1.00
    c = rescue.build_roll_down(_pos(short_strike=500.0, long_strike=490.0, width=10.0),
                               _mark(current_value=2.50), pricer, ctx={})
    assert c is not None
    # no est_fill_leg carries a None price -> the contract can be constructed
    from shared.contracts.options import RescueCandidate
    RescueCandidate(**c)   # must NOT raise
    assert all(l.get("price") is not None for l in c["est_fill_legs"])


# --------------------------------------------------------------------------- #
# build_roll_out
# --------------------------------------------------------------------------- #
def test_roll_out_adds_30_dte():
    c = rescue.build_roll_out(_pos(), _mark(current_value=2.50, dte=14), _flat_pricer, ctx={})
    assert c["action"] == "roll_out"
    assert c["new_expiry"] == "2026-08-16"   # 2026-07-17 + 30
    assert c["dte_after"] == 44
    assert c["commission"] == 4 * 1 * 0.65


def test_roll_out_none_when_no_current_value():
    assert rescue.build_roll_out(_pos(), _mark(current_value=None),
                                 _flat_pricer, ctx={}) is None


def test_roll_out_none_when_unpriceable():
    assert rescue.build_roll_out(_pos(), _mark(), _dead_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_roll_down_out
# --------------------------------------------------------------------------- #
def test_roll_down_out():
    c = rescue.build_roll_down_out(_pos(), _mark(current_value=2.50, dte=14),
                                   _flat_pricer, ctx={})
    assert c["action"] == "roll_down_out"
    assert c["new_expiry"] == "2026-08-16"
    assert c["dte_after"] == 44
    assert c["commission"] == 4 * 1 * 0.65
    assert c["new_short_delta"] is None


def test_roll_down_out_none_when_unpriceable():
    assert rescue.build_roll_down_out(_pos(), _mark(current_value=2.50),
                                      _dead_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_inverted (advisory)
# --------------------------------------------------------------------------- #
def test_inverted_none_for_pcs():
    assert rescue.build_inverted(_pos(strategy="PCS"), _mark(), _flat_pricer, ctx={}) is None


def test_inverted_advisory_for_ic():
    c = rescue.build_inverted(_pos(strategy="IC", call_short=110.0, call_long=115.0),
                              _mark(), _flat_pricer, ctx={})
    assert c["action"] == "inverted"
    assert c["apply_kind"] == "advisory"
    assert c["gross_cash"] == 0.0
    assert c["commission"] == 0.0
    assert c["net_cash"] == 0.0
    assert c["new_max_loss"] is None
    assert c["est_fill_legs"] == []


# --------------------------------------------------------------------------- #
# build_broken_wing (advisory)
# --------------------------------------------------------------------------- #
def test_broken_wing_advisory_for_pcs():
    c = rescue.build_broken_wing(_pos(), _mark(), _flat_pricer, ctx={})
    assert c["action"] == "broken_wing"
    assert c["apply_kind"] == "advisory"
    assert c["gross_cash"] == 0.0
    assert c["est_fill_legs"] == []


def test_broken_wing_none_for_ic():
    assert rescue.build_broken_wing(_pos(strategy="IC"), _mark(), _flat_pricer, ctx={}) is None


# --------------------------------------------------------------------------- #
# build_futures_hedge (advisory)
# --------------------------------------------------------------------------- #
def test_futures_hedge_none_for_equity_ctx():
    assert rescue.build_futures_hedge(_pos(), _mark(), _flat_pricer,
                                      ctx={"kind": "equity"}) is None


def test_futures_hedge_advisory_for_index():
    c = rescue.build_futures_hedge(_pos(quantity=2), _mark(current_short_delta=-0.40),
                                   _flat_pricer, ctx={"kind": "index"})
    assert c["action"] == "futures_hedge"
    assert c["apply_kind"] == "advisory"
    # net_delta_shares = round(-0.40 * 100 * 2) = -80 ; n = max(1, round(80/5)) = 16
    assert c["commission"] == rescue.futures_commission(16)
    assert c["net_cash"] == round(-c["commission"], 2)
    assert c["gross_cash"] == 0.0


# --------------------------------------------------------------------------- #
# roll_out / roll_down_out carry the CLOSE pair too (2026-08-20)
#
# These two used to emit only the 2 reopening legs while net_cash included the
# close debit (-cv). paper_adjust reprices est_fill_legs uniformly, so even
# UNCHANGED quotes read as drift equal to the whole close cost (measured 187%
# vs the 15% tolerance) -> every live apply refused with "prices moved". And
# when repricing was unavailable the apply fell back to closing the old spread
# at scratch, overstating equity by (cv - entry_credit) x 100 x qty.
# --------------------------------------------------------------------------- #

def _keyed_pricer(prices):
    """price_leg keyed by (expiry, strike)."""
    def _pl(sym, expiry, right, strike):
        return prices.get((str(expiry), float(strike)))
    return _pl


def test_roll_out_emits_the_close_pair_first_at_the_old_expiry():
    pl = _keyed_pricer({("2026-07-17", 100.0): 3.00, ("2026-07-17", 95.0): 0.50,
                        ("2026-08-16", 100.0): 4.20, ("2026-08-16", 95.0): 1.70})
    c = rescue.build_roll_out(_pos(), _mark(current_value=2.50), pl, ctx={})
    legs = c["est_fill_legs"]
    assert [(g["side"], g["strike"], g["expiry"]) for g in legs] == [
        ("BUY", 100.0, "2026-07-17"),   # close: buy back the old short
        ("SELL", 95.0, "2026-07-17"),   # close: sell the old long
        ("SELL", 100.0, "2026-08-16"),  # reopen
        ("BUY", 95.0, "2026-08-16"),
    ]
    assert legs[0]["price"] == 3.00 and legs[1]["price"] == 0.50


def test_roll_out_leg_reprice_matches_its_own_net_cash():
    """The stale guard's basis: repricing est_fill_legs with the SAME pricer that
    built the candidate must land on net_cash (leg-based close ~ cv). Pre-fix the
    two differed by the entire close debit."""
    prices = {("2026-07-17", 100.0): 3.00, ("2026-07-17", 95.0): 0.50,
              ("2026-08-16", 100.0): 4.20, ("2026-08-16", 95.0): 1.70}
    pl = _keyed_pricer(prices)
    c = rescue.build_roll_out(_pos(), _mark(current_value=2.50), pl, ctx={})
    gross = 0.0
    for g in c["est_fill_legs"]:
        sign = 1.0 if g["side"] == "SELL" else -1.0
        gross += sign * g["price"] * 100 * g["qty"]
    assert round(gross - c["commission"], 2) == c["net_cash"]


def test_roll_down_out_emits_the_close_pair_too():
    prices = {("2026-07-17", 100.0): 3.00, ("2026-07-17", 95.0): 0.50,
              ("2026-08-16", 95.0): 2.10, ("2026-08-16", 90.0): 0.90}
    c = rescue.build_roll_down_out(_pos(), _mark(current_value=2.50),
                                   _keyed_pricer(prices), ctx={})
    legs = c["est_fill_legs"]
    assert len(legs) == 4
    assert legs[0]["side"] == "BUY" and legs[0]["expiry"] == "2026-07-17"
    gross = sum((1.0 if g["side"] == "SELL" else -1.0) * g["price"] * 100
                for g in legs)
    assert round(gross - c["commission"], 2) == c["net_cash"]


def test_roll_out_close_pair_falls_back_to_the_marks_cv_split():
    """Old legs unpriceable (illiquid/off-hours): the close pair carries the
    mark's cv on the BUY-back and 0.0 on the sell, so the pair debit IS cv —
    the exact number net_cash used — never a fabricated 0.0-0.0 scratch."""
    pl = _keyed_pricer({("2026-08-16", 100.0): 4.20, ("2026-08-16", 95.0): 1.70})
    c = rescue.build_roll_out(_pos(), _mark(current_value=2.50), pl, ctx={})
    legs = c["est_fill_legs"]
    assert legs[0]["price"] == 2.50 and legs[1]["price"] == 0.0


def test_roll_down_close_pair_uses_the_cv_split_when_unpriceable():
    """The 4-leg roll_down had the same hole one layer down: _leg coalesced an
    unpriceable close leg to 0.0, and paper_adjust's _pair_debit read that as a
    REAL price — booking a credit-to-close when only the long was priceable."""
    prices = {("2026-07-17", 95.0): 2.10, ("2026-07-17", 90.0): 0.90}
    # old short (100) unpriceable; new strikes 95/90 priceable
    c = rescue.build_roll_down(_pos(), _mark(current_value=2.50),
                               _keyed_pricer(prices), ctx={})
    legs = c["est_fill_legs"]
    assert legs[0]["price"] == 2.50 and legs[1]["price"] == 0.0
