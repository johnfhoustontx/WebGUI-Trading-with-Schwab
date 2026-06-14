import fill_model as fm


def test_sell_to_open_is_short_bid_minus_long_ask():
    # sell short @ bid 1.10, buy long @ ask 0.70 -> credit 0.40 (caller rounds)
    assert round(fm.vertical_fill(1.10, 1.20, 0.60, 0.70, "SELL_TO_OPEN"), 2) == 0.40


def test_buy_to_close_is_short_ask_minus_long_bid():
    # buy short @ ask 1.20, sell long @ bid 0.60 -> debit 0.60
    assert round(fm.vertical_fill(1.10, 1.20, 0.60, 0.70, "BUY_TO_CLOSE"), 2) == 0.60


def test_natural_costs_the_full_spread_round_trip():
    sell = fm.vertical_fill(1.10, 1.20, 0.60, 0.70, "SELL_TO_OPEN")   # 0.40
    buy = fm.vertical_fill(1.10, 1.20, 0.60, 0.70, "BUY_TO_CLOSE")    # 0.60
    # entering and immediately exiting at natural costs the bid/ask spread
    assert round(buy - sell, 2) == 0.20


# Realistic limit fill. Quotes: short 1.10x1.20, long 0.60x0.70
# -> net_bid = 1.10-0.70 = 0.40, net_ask = 1.20-0.60 = 0.60, market width 0.20


def test_realistic_sell_is_40pct_into_market_from_natural():
    # 0.40 + 0.40*0.20 = 0.48
    assert round(fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70,
                                            "SELL_TO_OPEN"), 2) == 0.48


def test_realistic_buy_is_40pct_into_market_from_natural():
    # 0.60 - 0.40*0.20 = 0.52
    assert round(fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70,
                                            "BUY_TO_CLOSE"), 2) == 0.52


def test_realistic_round_trip_costs_20pct_of_market_width():
    sell = fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70, "SELL_TO_OPEN")
    buy = fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70, "BUY_TO_CLOSE")
    assert round(buy - sell, 4) == 0.04  # vs 0.20 for natural round trip


def test_realistic_frac_zero_is_natural():
    assert fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70,
                                      "SELL_TO_OPEN", frac=0.0) == \
        fm.vertical_fill(1.10, 1.20, 0.60, 0.70, "SELL_TO_OPEN")


def test_realistic_frac_half_is_mid_both_sides():
    sell = fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70,
                                      "SELL_TO_OPEN", frac=0.5)
    buy = fm.realistic_vertical_fill(1.10, 1.20, 0.60, 0.70,
                                     "BUY_TO_CLOSE", frac=0.5)
    assert round(sell, 4) == round(buy, 4) == 0.50


# Tradeability gate


def test_tradeable_normal_market():
    assert fm.is_tradeable(1.10, 1.20, 0.60, 0.70, width=5.0) is True


def test_untradeable_when_natural_credit_nonpositive():
    # net_bid = 0.60 - 0.70 = -0.10
    assert fm.is_tradeable(0.60, 1.20, 0.55, 0.70, width=5.0) is False


def test_untradeable_when_market_wider_than_30pct_of_width():
    # net market = (2.50-0.60) - (1.10-0.70) = 1.50 > 0.30 * 1.0
    assert fm.is_tradeable(1.10, 2.50, 0.60, 0.70, width=1.0) is False


def test_market_width_exactly_at_cap_is_tradeable():
    # net_bid 0.40, net_ask 0.70 -> market 0.30 == 0.30 * 1.0
    assert fm.is_tradeable(1.10, 1.30, 0.60, 0.70, width=1.0) is True
