"""D4: a STOCK leg in the pricing core, which unlocks covered call / protective
put / collar analysis.

**The representation is the whole design.** A stock leg is a normalized leg dict
like any other, with ``option_type = "stock"``, no ``strike`` and no ``expiry``,
and ⚠ its ``qty`` counts **100-share lots, not shares** — so the existing
``price x qty x 100`` arithmetic in every consumer stays byte-identical and only
the per-share VALUE function needs to know about it. A share is worth the
underlying at any T, which is exactly why ``leg_value`` exists: ``bs_price`` with
``strike=None`` would raise.

Every figure below is exact and hand-checkable, which is the point — a covered
call on 100 shares at $100 short the 105 call for $2.00 has a max profit of
``(105-100) x 100 + 200 = $700`` and a break-even at ``100 - 2 = $98``. If any
layer disagrees about per-share vs per-contract, these numbers move by 100x.
"""
import pytest

import options_calculator as oc


def _stock(qty=1, price=100.0, side="long"):
    """100 shares per ``qty``, bought at ``price``/share."""
    return {"option_type": "stock", "side": side, "strike": None,
            "expiry": None, "qty": qty, "premium": price}


def _opt(kind, side, strike, premium, qty=1, expiry=None):
    return {"option_type": kind, "side": side, "strike": strike,
            "expiry": expiry, "qty": qty, "premium": premium}


def _covered_call():
    return [_stock(), _opt("call", "short", 105.0, 2.00)]


def _protective_put():
    return [_stock(), _opt("put", "long", 95.0, 1.50)]


def _collar():
    return [_stock(), _opt("put", "long", 95.0, 1.50),
            _opt("call", "short", 105.0, 2.00)]


# ── leg_value: the one function that has to know ───────────────────────────

def test_a_share_is_worth_the_underlying():
    assert oc.leg_value(137.42, _stock(), 0.0, 0.045, 0.25) == pytest.approx(137.42)


@pytest.mark.parametrize("t", [0.0, 0.01, 0.5, 2.0])
def test_a_share_ignores_TIME_entirely(t):
    """No theta, no decay — the reason a stock leg cannot go through bs_price."""
    assert oc.leg_value(100.0, _stock(), t, 0.045, 0.25) == pytest.approx(100.0)


@pytest.mark.parametrize("iv", [0.01, 0.25, 3.0])
def test_a_share_ignores_VOLATILITY_entirely(iv):
    assert oc.leg_value(100.0, _stock(), 0.5, 0.045, iv) == pytest.approx(100.0)


def test_a_share_is_worth_ZERO_at_a_zero_price():
    """⚠ The endpoint the max-loss scan needs: shares really can go to zero,
    which no option-only structure can do."""
    assert oc.leg_value(0.0, _stock(), 0.0, 0.045, 0.25) == 0.0


def test_an_OPTION_leg_still_routes_to_bs_price():
    leg = _opt("call", "long", 100.0, 5.0)
    assert oc.leg_value(110.0, leg, 0.0, 0.045, 0.25) == pytest.approx(
        oc.bs_price(110.0, 100.0, 0.0, 0.045, 0.25, "call"))


@pytest.mark.parametrize("spelling", ["stock", "STOCK", "Stock", "  stock  "])
def test_the_stock_kind_is_matched_case_and_space_insensitively(spelling):
    """It arrives from a UI select and a JSON round trip."""
    leg = {**_stock(), "option_type": spelling}
    assert oc.leg_value(100.0, leg, 0.0, 0.045, 0.25) == pytest.approx(100.0)
    assert oc.is_stock_leg(leg) is True


@pytest.mark.parametrize("leg", [
    {"option_type": "call"}, {"option_type": None}, {"option_type": 7},
    {}, {"option_type": "stocks"},
])
def test_is_stock_leg_is_false_for_everything_else(leg):
    """⚠ ``"stocks"`` is in here on purpose: a near-miss must read as an OPTION
    and raise on its missing strike, not silently price as a share."""
    assert oc.is_stock_leg(leg) is False


# ── the covered call, end to end through the numeric summary ───────────────

def test_a_covered_call_entry_is_a_net_DEBIT():
    """⚠ Pay $10,000 for the shares, receive $200 for the call: -$9,800. The
    option leg alone is a credit, and the POSITION is not — the distinction the
    rest of the app does not have to make, because its COVERED_CALL is the option
    leg only."""
    s = oc.calc_summary_generic(_covered_call(), spot=100.0, iv=0.25, T=30 / 365)
    assert s["entry_credit"] == pytest.approx(-9800.0)


def test_a_covered_calls_max_profit_is_the_called_away_gain_plus_the_premium():
    """(105 - 100) x 100 + 200 = $700, and it cannot earn more however far the
    stock runs — that is what being called away means."""
    s = oc.calc_summary_generic(_covered_call(), spot=100.0, iv=0.25, T=30 / 365)
    assert s["max_profit"] == pytest.approx(700.0, abs=2.0)


def test_a_covered_calls_break_even_is_the_share_cost_less_the_premium():
    """$100 paid - $2.00 received = $98."""
    s = oc.calc_summary_generic(_covered_call(), spot=100.0, iv=0.25, T=30 / 365)
    assert any(abs(b - 98.0) < 0.5 for b in s["breakevens"]), s["breakevens"]


def test_a_covered_calls_max_loss_is_the_stock_going_to_ZERO():
    """⚠ The grid change. ``calc_summary_generic`` scanned 0.5x..1.5x spot, which
    is right for an option-only structure and WRONG here: it would have reported
    -$4,800 (the loss at $50) as the max loss of a position that can lose $9,800.
    A leg set holding shares scans from zero."""
    s = oc.calc_summary_generic(_covered_call(), spot=100.0, iv=0.25, T=30 / 365)
    assert s["max_loss"] == pytest.approx(9800.0, abs=25.0)


def test_an_OPTION_ONLY_structure_keeps_the_old_grid():
    """The control: a put credit spread's max loss is width - credit = $140,
    unchanged. Extending the grid must not move an existing answer."""
    pcs = [_opt("put", "short", 100.0, 0.60), _opt("put", "long", 98.0, 0.00)]
    s = oc.calc_summary_generic(pcs, spot=100.0, iv=0.25, T=30 / 365)
    assert s["max_loss"] == pytest.approx(140.0, abs=5.0)


# ── the protective put ─────────────────────────────────────────────────────

def test_a_protective_puts_max_loss_is_BOUNDED_by_the_put():
    """(100 - 95) x 100 + 150 paid = $650, and no worse — that is the whole
    point of owning the put, and the grid reaching zero is what proves it."""
    s = oc.calc_summary_generic(_protective_put(), spot=100.0, iv=0.25,
                                T=30 / 365)
    assert s["max_loss"] == pytest.approx(650.0, abs=25.0)


def test_a_protective_put_has_UNCAPPED_upside():
    """Nothing sold, so max profit is grid-bounded rather than structural: at
    1.5x spot the shares are worth $15,000 for $10,150 committed."""
    s = oc.calc_summary_generic(_protective_put(), spot=100.0, iv=0.25,
                                T=30 / 365)
    assert s["max_profit"] > 4000.0


def test_a_protective_puts_break_even_is_the_cost_of_the_insurance():
    """$100 + $1.50 = $101.50."""
    s = oc.calc_summary_generic(_protective_put(), spot=100.0, iv=0.25,
                                T=30 / 365)
    assert any(abs(b - 101.5) < 0.5 for b in s["breakevens"]), s["breakevens"]


# ── the collar: both ends bounded ──────────────────────────────────────────

def test_a_collar_is_bounded_on_BOTH_sides():
    """Net credit of $0.50 on the options. Up: (105-100)x100 + 50 = $550.
    Down: -(100-95)x100 + 50 = -$450."""
    s = oc.calc_summary_generic(_collar(), spot=100.0, iv=0.25, T=30 / 365)
    assert s["max_profit"] == pytest.approx(550.0, abs=25.0)
    assert s["max_loss"] == pytest.approx(450.0, abs=25.0)


def test_a_zero_cost_collar_breaks_even_at_the_share_price():
    """Put and call priced identically, so the options net zero and the
    break-even is what the shares cost."""
    legs = [_stock(), _opt("put", "long", 95.0, 2.00),
            _opt("call", "short", 105.0, 2.00)]
    s = oc.calc_summary_generic(legs, spot=100.0, iv=0.25, T=30 / 365)
    assert any(abs(b - 100.0) < 0.5 for b in s["breakevens"]), s["breakevens"]


# ── quantity: lots, not shares ─────────────────────────────────────────────

def test_a_TWO_LOT_covered_call_doubles_every_dollar_figure():
    """⚠ ``qty`` is 100-share LOTS. Two lots against two short calls is 200
    shares: entry -$19,600, max profit $1,400."""
    legs = [_stock(qty=2), _opt("call", "short", 105.0, 2.00, qty=2)]
    s = oc.calc_summary_generic(legs, spot=100.0, iv=0.25, T=30 / 365)
    assert s["entry_credit"] == pytest.approx(-19600.0)
    assert s["max_profit"] == pytest.approx(1400.0, abs=4.0)


def test_a_SHORT_stock_leg_inverts_the_position():
    """Shorting shares at $100 takes in $10,000 and loses as price rises."""
    s = oc.calc_summary_generic([_stock(side="short")], spot=100.0, iv=0.25,
                                T=30 / 365)
    assert s["entry_credit"] == pytest.approx(10000.0)
    assert s["max_profit"] == pytest.approx(10000.0, abs=25.0)   # to zero


# ── the P&L grid ──────────────────────────────────────────────────────────

def test_the_PnL_grid_prices_a_covered_call_at_expiry():
    """The grid is what the page renders. At $110 the position is +$700, at
    $100 +$200, at $90 -$800 — the payoff, cell by cell."""
    rows = oc.calc_spread_pnl(_covered_call(), spot=100.0, iv=0.25, r=0.045,
                              eval_dates=[], price_range=(0.0, 1e12),
                              expiry_date=None, eval_times=[0.0],
                              price_rows=[90.0, 100.0, 110.0])
    by_price = {r["price"]: r["pnl"][0] for r in rows}
    assert by_price[110.0] == pytest.approx(700.0, abs=1.0)
    assert by_price[100.0] == pytest.approx(200.0, abs=1.0)
    assert by_price[90.0] == pytest.approx(-800.0, abs=1.0)


def test_the_PnL_grid_carries_a_stock_leg_at_a_LIVE_T_too():
    """⚠ Not just the payoff column: with time left the option leg keeps its
    extrinsic while the share leg does not, so the cell is NOT the payoff. At
    spot with 30 days left the short call still has value, so the position is
    worth less than the +$200 it books at expiry."""
    rows = oc.calc_spread_pnl(_covered_call(), spot=100.0, iv=0.25, r=0.045,
                              eval_dates=[], price_range=(0.0, 1e12),
                              expiry_date=None, eval_times=[30 / 365],
                              price_rows=[100.0])
    live = rows[0]["pnl"][0]
    assert live < 200.0
    assert live > -200.0


def test_the_grid_does_not_RAISE_on_a_strikeless_leg():
    """The regression that motivated ``leg_value``: ``bs_price(price, None, ...)``
    raises, and the grid is built on every keystroke."""
    rows = oc.calc_spread_pnl([_stock()], spot=100.0, iv=0.25, r=0.045,
                              eval_dates=[], price_range=(0.0, 1e12),
                              expiry_date=None, eval_times=[0.0],
                              price_rows=[95.0, 105.0])
    by_price = {r["price"]: r["pnl"][0] for r in rows}
    assert by_price[105.0] == pytest.approx(500.0)     # bought at 100
    assert by_price[95.0] == pytest.approx(-500.0)


# ── a share leg must not join the front-EXPIRY horizon ────────────────────

def test_a_stock_leg_contributes_NO_expiry_to_the_horizon():
    """⚠ Belt-and-braces at the layer that decides. A share leg carrying a
    stale ``expiry`` (the UI clears it on retype, but a pasted or hand-built leg
    set can still arrive with one) would join ``calc_summary_generic``'s
    front-expiry computation. If it were EARLIER than the real option leg's, the
    option would be priced with time remaining at the wrong horizon — a payoff
    diagram that is not a payoff."""
    stale = {**_stock(), "expiry": "2020-01-01"}
    assert oc._leg_expiry_years(stale) is None


def test_a_stale_share_expiry_does_not_move_the_covered_call_payoff():
    """The observable consequence: identical figures with and without it."""
    clean = oc.calc_summary_generic(_covered_call(), spot=100.0, iv=0.25,
                                    T=30 / 365)
    stale_legs = [{**_stock(), "expiry": "2026-01-01"},
                  _opt("call", "short", 105.0, 2.00, expiry="2026-12-18")]
    stale = oc.calc_summary_generic(stale_legs, spot=100.0, iv=0.25, T=30 / 365)
    assert stale["max_profit"] == pytest.approx(clean["max_profit"], abs=2.0)
    assert stale["entry_credit"] == pytest.approx(clean["entry_credit"])


def test_an_OPTION_legs_expiry_is_still_read():
    """The control — the calendar machinery must keep working."""
    leg = _opt("call", "long", 100.0, 5.0, expiry="2099-12-18")
    assert oc._leg_expiry_years(leg) is not None
