from services.market_svc import classify as C


def test_normalize_equity_uses_net_percent_change():
    q = {"assetMainType": "EQUITY",
         "quote": {"lastPrice": 100.0, "netChange": 1.0, "netPercentChange": 1.0,
                   "closePrice": 99.0}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 100.0 and chg == 1.0 and round(pct, 3) == 1.0


def test_normalize_future_uses_future_percent_change():
    q = {"assetMainType": "FUTURE",
         "quote": {"lastPrice": 7560.75, "netChange": 9.5,
                   "futurePercentChange": 0.1258, "closePrice": 7551.25}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 7560.75 and chg == 9.5 and round(pct, 3) == 0.126


def test_normalize_internal_value_only_no_change():
    q = {"assetMainType": "EQUITY",
         "quote": {"lastPrice": 38.0, "netChange": 0.0, "netPercentChange": 0.0,
                   "closePrice": 0.0}}
    last, chg, pct = C.normalize_quote(q)
    assert last == 38.0 and chg == 0.0 and pct == 0.0


def test_spread_diff_last():
    assert C.spread_value("diff_last", (1160.0, 0.0, 0.0), (1625.0, 0.0, 0.0)) == \
        (-465.0, -465.0, 0.0)  # (last, change, pct) — value = a.last - b.last


def test_spread_diff_pct():
    # HYG +0.5% vs LQD +0.2% → HY outperforms by +0.3
    last, chg, pct = C.spread_value("diff_pct", (81.0, 0.0, 0.5), (110.0, 0.0, 0.2))
    assert round(pct, 3) == 0.3 and round(last, 3) == 0.3


def test_color_state_normal_up_is_risk_on():
    assert C.color_state(2.0, polarity="normal") == "risk_on_strong"
    assert C.color_state(0.4, polarity="normal") == "risk_on_mild"


def test_color_state_inverted_up_is_risk_off():
    # VIX +5% → inverted → risk-off
    assert C.color_state(5.0, polarity="inverted") == "risk_off_strong"


def test_color_state_flat_and_no_data():
    assert C.color_state(0.02, polarity="normal") == "flat"
    assert C.color_state(None, polarity="normal") == "no_data"


def test_color_state_boundaries_pin_flat_and_strong_cutoffs():
    # Exactly _FLAT_PCT (0.1): mag < _FLAT_PCT is a strict `<`, so 0.1 is NOT
    # flat — it's the first mild bucket.
    assert C.color_state(0.1, polarity="normal") == "risk_on_mild"
    # Exactly _STRONG_PCT (1.0): mag >= _STRONG_PCT, so 1.0 IS strong.
    assert C.color_state(1.0, polarity="normal") == "risk_on_strong"


def test_color_state_value_only_uses_sign_one_intensity():
    # a value-only internal (e.g. $TICK = +300) → mild risk-on, not "strong"
    assert C.color_state(300.0, polarity="normal", value_only=True) == "risk_on_mild"
    assert C.color_state(-300.0, polarity="normal", value_only=True) == "risk_off_mild"


# ── an absent price is no data, not a price of zero (2026-08-20) ────────────

def test_normalize_quote_returns_none_when_there_is_no_last_price():
    """`_num` coerced anything unparseable to 0.0, so a symbol that arrived
    WITHOUT a lastPrice rendered as a real tile reading "0.00" coloured flat --
    indistinguishable from a genuinely unchanged market. The `no_data` path only
    fired when the whole symbol was missing from the quote map.

    Returning None routes it into that existing degrade path (compute._leg
    already returns None -> the tile paints no_data), so no new branch is needed.
    """
    assert C.normalize_quote({"quote": {"netChange": 0.0}}) is None


def test_normalize_quote_returns_none_for_a_nan_last_price():
    assert C.normalize_quote({"quote": {"lastPrice": float("nan")}}) is None
    assert C.normalize_quote({"quote": {"lastPrice": "n/a"}}) is None


def test_normalize_quote_keeps_a_genuine_zero_change():
    """The distinction that matters: a flat tape is data. Only the PRICE being
    absent is an absence -- change/pct of exactly 0.0 stay 0.0."""
    out = C.normalize_quote({"quote": {"lastPrice": 100.0, "netChange": 0.0,
                                       "netPercentChange": 0.0, "closePrice": 100.0}})
    assert out == (100.0, 0.0, 0.0)


def test_normalize_quote_accepts_a_legitimately_zero_price():
    """A zero price is unusual but not absent (some spreads/internals print 0)."""
    out = C.normalize_quote({"quote": {"lastPrice": 0.0, "netChange": 0.0}})
    assert out is not None and out[0] == 0.0
