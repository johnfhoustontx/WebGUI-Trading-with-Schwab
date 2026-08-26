import datetime

import signal_repricer

# A clearly-future expiration so the expiry guard (reprice_swing skips the chain
# fetch for past expirations) never trips on the pricing-math tests below.
_FUTURE_EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


class _FakeResp:
    status_code = 200

    def json(self):
        return {"putExpDateMap": {}, "callExpDateMap": {}, "underlying": {}}


class _FakeContractType:
    ALL = "ALL"


class _FakeOptions:
    ContractType = _FakeContractType


class _FakeClient:
    """Records the kwargs passed to get_option_chain so tests can assert types."""

    Options = _FakeOptions

    def __init__(self):
        self.calls = []

    def get_option_chain(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return _FakeResp()


def test_fetch_chain_passes_date_objects_not_strings():
    """schwab-py validates from_date/to_date as datetime.date; the DB stores
    expiration as an ISO string, so _fetch_chain must convert it."""
    signal_repricer.clear_chain_cache()
    client = _FakeClient()

    signal_repricer._fetch_chain(client, "QQQ", "2026-06-19")

    symbol, kwargs = client.calls[0]
    assert symbol == "QQQ"
    assert isinstance(kwargs["from_date"], datetime.date)
    assert isinstance(kwargs["to_date"], datetime.date)
    assert kwargs["from_date"] == datetime.date(2026, 6, 19)
    assert kwargs["to_date"] == datetime.date(2026, 6, 19)


def test_fetch_chain_accepts_date_object():
    """If a caller already passes a date, it should pass through unchanged."""
    signal_repricer.clear_chain_cache()
    client = _FakeClient()
    exp = datetime.date(2026, 6, 19)

    signal_repricer._fetch_chain(client, "SPY", exp)

    _, kwargs = client.calls[0]
    assert kwargs["from_date"] == exp


def _pcs(short=690, long=688, credit=0.6):
    return {"strategy": "PCS", "short_strike": short, "long_strike": long,
            "entry_credit": credit, "call_short": None, "call_long": None}


def test_intrinsic_pcs_settles_above_short_is_max_profit():
    t = _pcs(short=690, long=688, credit=0.6)
    v, pnl = signal_repricer.intrinsic_value(t, settlement=700.0)
    assert v == 0
    assert pnl == 60.0


def test_intrinsic_pcs_settles_below_long_is_max_loss():
    t = _pcs(short=690, long=688, credit=0.6)
    v, pnl = signal_repricer.intrinsic_value(t, settlement=680.0)
    assert v == 2.0
    assert pnl == (0.6 - 2.0) * 100


def test_intrinsic_pcs_between_strikes():
    t = _pcs(short=690, long=688, credit=0.6)
    v, pnl = signal_repricer.intrinsic_value(t, settlement=689.0)
    assert v == 1.0  # only short is ITM
    assert abs(pnl - (0.6 - 1.0) * 100) < 1e-6


def test_intrinsic_ccs():
    t = {"strategy": "CCS", "short_strike": 700, "long_strike": 702,
         "entry_credit": 0.5, "call_short": None, "call_long": None}
    v, pnl = signal_repricer.intrinsic_value(t, settlement=690.0)
    assert v == 0
    assert pnl == 50.0


# ── DEBIT / legs structures (long options + debit verticals) ─────────────────
def test_position_intrinsic_long_and_spread():
    long_call = [{"kind": "call", "side": "long", "strike": 100, "qty": 1}]
    assert signal_repricer.position_intrinsic(long_call, 108.0) == 8.0
    assert signal_repricer.position_intrinsic(long_call, 95.0) == 0.0
    # debit call spread: long 100 / short 105 → capped at width 5
    spread = long_call + [{"kind": "call", "side": "short", "strike": 105, "qty": 1}]
    assert signal_repricer.position_intrinsic(spread, 110.0) == 5.0
    assert signal_repricer.position_intrinsic(spread, 95.0) == 0.0


def test_legs_intrinsic_value_long_call():
    t = {"legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1}],
         "entry_debit": 250}   # paid $2.50/contract
    v, pnl = signal_repricer.legs_intrinsic_value(t, settlement=108.0)
    assert v == 8.0 and pnl == 550.0            # worth $800 − $250 debit
    v2, pnl2 = signal_repricer.legs_intrinsic_value(t, settlement=95.0)
    assert v2 == 0.0 and pnl2 == -250.0          # OTM → lose the debit


def _chain_client(call_quotes=None, put_quotes=None, last=100.0):
    def _mk(quotes):
        return {f"{_FUTURE_EXP}:30": {f"{float(k):.1f}": [{"bid": b, "ask": a, "delta": 0.5}]
                                      for k, (b, a) in (quotes or {}).items()}}

    class _R:
        status_code = 200

        def json(self):
            return {"callExpDateMap": _mk(call_quotes), "putExpDateMap": _mk(put_quotes),
                    "underlying": {"last": last}}

    class _C:
        Options = _FakeOptions

        def get_option_chain(self, symbol, **kwargs):
            return _R()

    return _C()


def test_reprice_legs_long_call_gain():
    signal_repricer.clear_chain_cache()
    t = {"direction": "DEBIT", "symbol": "SPY", "expiration": _FUTURE_EXP,
         "entry_debit": 250, "quantity": 1,
         "legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1}]}
    client = _chain_client(call_quotes={100: (3.4, 3.6)}, last=103.0)   # mid 3.50 → $350
    rep = signal_repricer.reprice_legs(t, client)
    assert rep["error"] is None
    assert rep["current_value"] == 3.5           # per share net value
    assert rep["unrealized_pnl"] == 100.0         # $350 − $250 per contract


def test_reprice_legs_debit_spread_and_missing_quote():
    signal_repricer.clear_chain_cache()
    t = {"direction": "DEBIT", "symbol": "SPY", "expiration": _FUTURE_EXP,
         "entry_debit": 200, "quantity": 1,
         "legs": [{"kind": "call", "side": "long", "strike": 100, "qty": 1},
                  {"kind": "call", "side": "short", "strike": 105, "qty": 1}]}
    client = _chain_client(call_quotes={100: (4.0, 4.2), 105: (1.9, 2.1)})  # 4.1 − 2.0 = 2.1
    rep = signal_repricer.reprice_legs(t, client)
    assert rep["current_value"] == 2.1 and rep["unrealized_pnl"] == 10.0   # $210 − $200
    # a leg with no quote → graceful failure, not a crash
    client2 = _chain_client(call_quotes={100: (4.0, 4.2)})   # 105 missing
    signal_repricer.clear_chain_cache()
    rep2 = signal_repricer.reprice_legs(t, client2)
    assert rep2["error"] == "repricing failed" and rep2["unrealized_pnl"] is None


def test_intrinsic_ic_both_wings_safe():
    t = {"strategy": "IC", "short_strike": 690, "long_strike": 688,
         "call_short": 710, "call_long": 712, "entry_credit": 1.0}
    v, pnl = signal_repricer.intrinsic_value(t, settlement=700.0)
    assert v == 0
    assert pnl == 100.0


def test_reprice_swing_from_mock_chain(monkeypatch):
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": _FUTURE_EXP, "entry_credit": 1.30,
         "call_short": None, "call_long": None}

    def fake_chain(client, sym, exp):
        return {"putExpDateMap": {"2026-04-24:7": {
            "590.0": [{"bid": 0.45, "ask": 0.55, "delta": -0.12}],
            "585.0": [{"bid": 0.15, "ask": 0.25, "delta": -0.05}],
        }}, "underlying": {"last": 600.0}}
    monkeypatch.setattr(signal_repricer, "_fetch_chain", fake_chain)

    result = signal_repricer.reprice_swing(t, client=object())
    # realistic buy-to-close: net market 0.20x0.40 -> 0.40 - 0.40*0.20 = 0.32
    assert abs(result["current_value"] - 0.32) < 1e-6
    assert abs(result["unrealized_pnl"] - (1.30 - 0.32) * 100) < 1e-6
    # pnl_pct_of_credit is a PERCENT: (1.30 - 0.32) / 1.30 * 100 ≈ 75.38
    assert abs(result["pnl_pct_of_credit"] - (0.98 / 1.30 * 100)) < 1e-6
    assert result["current_underlying"] == 600.0
    assert result["current_short_delta"] == -0.12
    assert result["error"] is None
    # realistic debit sits strictly between mid (0.30) and natural (0.40)
    assert 0.30 < result["current_value"] < 0.40


def test_reprice_swing_ccs_from_mock_chain(monkeypatch):
    t = {"strategy": "CCS", "symbol": "QQQ", "short_strike": 620, "long_strike": 625,
         "expiration": _FUTURE_EXP, "entry_credit": 0.80,
         "call_short": None, "call_long": None}

    def fake_chain(client, sym, exp):
        return {"callExpDateMap": {"2026-04-24:7": {
            "620.0": [{"bid": 0.30, "ask": 0.40, "delta": 0.18}],
            "625.0": [{"bid": 0.05, "ask": 0.15, "delta": 0.05}],
        }}, "underlying": {"last": 600.0}}
    monkeypatch.setattr(signal_repricer, "_fetch_chain", fake_chain)

    result = signal_repricer.reprice_swing(t, client=object())
    # realistic buy-to-close: net market 0.15x0.35 -> 0.35 - 0.40*0.20 = 0.27
    assert abs(result["current_value"] - 0.27) < 1e-6
    assert result["current_short_delta"] == 0.18


def test_reprice_swing_ic_from_mock_chain(monkeypatch):
    t = {"strategy": "IC", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "call_short": 610, "call_long": 615, "expiration": _FUTURE_EXP,
         "entry_credit": 1.50}

    def fake_chain(client, sym, exp):
        return {
            "putExpDateMap": {"2026-04-24:7": {
                "590.0": [{"bid": 0.30, "ask": 0.40, "delta": -0.12}],
                "585.0": [{"bid": 0.10, "ask": 0.20, "delta": -0.05}],
            }},
            "callExpDateMap": {"2026-04-24:7": {
                "610.0": [{"bid": 0.25, "ask": 0.35, "delta": 0.15}],
                "615.0": [{"bid": 0.05, "ask": 0.15, "delta": 0.05}],
            }},
            "underlying": {"last": 600.0},
        }
    monkeypatch.setattr(signal_repricer, "_fetch_chain", fake_chain)

    result = signal_repricer.reprice_swing(t, client=object())
    # realistic buy-to-close per leg: put net 0.10x0.30 -> 0.22;
    # call net 0.10x0.30 -> 0.22; total 0.44
    assert abs(result["current_value"] - 0.44) < 1e-6


def test_reprice_swing_api_failure_returns_error(monkeypatch):
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": _FUTURE_EXP, "entry_credit": 1.30,
         "call_short": None, "call_long": None}

    def boom(*a, **kw):
        raise RuntimeError("api down")
    monkeypatch.setattr(signal_repricer, "_fetch_chain", boom)
    result = signal_repricer.reprice_swing(t, client=object())
    assert result["current_value"] is None
    assert result["error"] == "repricing failed"


def test_reprice_swing_missing_leg_quotes_returns_error(monkeypatch):
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": _FUTURE_EXP, "entry_credit": 1.30,
         "call_short": None, "call_long": None}

    def fake_chain(client, sym, exp):
        return {"putExpDateMap": {"2026-04-24:7": {
            "590.0": [{"bid": 0, "ask": 0, "delta": -0.12}],  # no quotes
            "585.0": [{"bid": 0.15, "ask": 0.25, "delta": -0.05}],
        }}, "underlying": {"last": 600.0}}
    monkeypatch.setattr(signal_repricer, "_fetch_chain", fake_chain)
    result = signal_repricer.reprice_swing(t, client=object())
    assert result["current_value"] is None
    assert result["error"] == "repricing failed"


# ── Expired-trade guard: no live chain exists for a past expiration, so skip
#    the Schwab chain fetch entirely (Schwab 400s on it) instead of burning an
#    API call every reprice cycle. Mirrors the proxy's /track guard.

def test_is_expired_past_present_future():
    today = datetime.date(2026, 6, 20)
    assert signal_repricer._is_expired("2026-06-19", today=today) is True
    assert signal_repricer._is_expired("2026-06-20", today=today) is False  # 0-DTE still live
    assert signal_repricer._is_expired("2026-06-21", today=today) is False


def test_is_expired_accepts_date_object():
    today = datetime.date(2026, 6, 20)
    assert signal_repricer._is_expired(datetime.date(2026, 6, 1), today=today) is True


def test_is_expired_malformed_returns_false():
    # Unparseable / missing -> not expired, so the normal live path still runs.
    assert signal_repricer._is_expired("not-a-date") is False
    assert signal_repricer._is_expired(None) is False


def test_reprice_swing_expired_skips_chain_fetch(monkeypatch):
    """An expired trade must NOT call the chain API; it returns an unpriceable
    'expired' mark (downstream build_mark treats any truthy error the same)."""
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": "2026-04-24", "entry_credit": 1.30,
         "call_short": None, "call_long": None}

    calls = []
    monkeypatch.setattr(signal_repricer, "_fetch_chain",
                        lambda *a, **kw: calls.append(a) or {})

    result = signal_repricer.reprice_swing(t, client=object(),
                                           today=datetime.date(2026, 6, 20))

    assert calls == []                          # the chain fetch was skipped
    assert result["current_value"] is None
    assert result["unrealized_pnl"] is None
    assert result["current_underlying"] is None
    assert result["error"] == "expired"


def test_reprice_swing_today_expiration_still_fetches(monkeypatch):
    """0-DTE (expiration == today) is NOT expired — the chain is still fetched."""
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": "2026-06-20", "entry_credit": 1.30,
         "call_short": None, "call_long": None}

    calls = []

    def fake_chain(client, sym, exp):
        calls.append((sym, exp))
        return {"putExpDateMap": {"2026-06-20:0": {
            "590.0": [{"bid": 0.45, "ask": 0.55, "delta": -0.12}],
            "585.0": [{"bid": 0.15, "ask": 0.25, "delta": -0.05}],
        }}, "underlying": {"last": 600.0}}
    monkeypatch.setattr(signal_repricer, "_fetch_chain", fake_chain)

    result = signal_repricer.reprice_swing(t, client=object(),
                                           today=datetime.date(2026, 6, 20))

    assert calls == [("QQQ", "2026-06-20")]     # fetched
    assert result["error"] is None


def test_leg_bid_ask_reads_two_sided_quote():
    leg_map = {"2026-06-03:0": {"500.0": [{"bid": 1.10, "ask": 1.20, "delta": -0.30}]}}
    bid, ask, delta = signal_repricer._leg_bid_ask(leg_map, 500)
    assert (bid, ask) == (1.10, 1.20)
    assert delta == -0.30


def test_leg_bid_ask_missing_returns_none():
    assert signal_repricer._leg_bid_ask({}, 500) == (None, None, None)


def test_leg_bid_ask_one_sided_returns_none():
    leg_map = {"e": {"500.0": [{"bid": 0, "ask": 1.20, "delta": -0.3}]}}
    assert signal_repricer._leg_bid_ask(leg_map, 500) == (None, None, None)


# ── ATM IV for the captured Expected Move (2026-08-25) ───────────────────────
# The Trade detail panel's Expected Move expansion needs a price AND an IV.
# Captured signals carried a price but NO IV at all, so the expansion never
# rendered on that page. The reprice cycle ALREADY fetches the chain, so the IV
# comes off data in hand — no extra Schwab call.

class TestAtmIv:
    @staticmethod
    def _chain(*pairs, underlying=100.0):
        """A putExpDateMap-shaped chain: (strike, volatility) pairs."""
        return {"underlying": {"last": underlying},
                "putExpDateMap": {"2026-09-04:10": {
                    f"{float(k):.1f}": [{"bid": 1.0, "ask": 1.1, "volatility": v}]
                    for k, v in pairs}}}

    def test_it_takes_the_strike_nearest_spot(self):
        """ATM, not the short leg. The short leg is OTM, so its IV carries skew
        and would overstate the move — for a put spread, systematically."""
        chain = self._chain((90, 40.0), (100, 25.0), (110, 30.0), underlying=101.0)
        assert signal_repricer.atm_iv(chain) == 25.0

    def test_it_rejects_schwabs_minus_999_sentinel(self):
        """Schwab returns volatility = -999 for a contract it cannot price. It is
        a sentinel, not a reading — `flow_skew._as_float` accepted it as a usable
        IV until 2026-08-20 and this repo paid for that."""
        chain = self._chain((100, -999.0), (110, 30.0), underlying=100.0)
        assert signal_repricer.atm_iv(chain) == 30.0

    def test_a_chain_with_no_usable_iv_yields_None(self):
        chain = self._chain((100, -999.0), (110, None), underlying=100.0)
        assert signal_repricer.atm_iv(chain) is None

    def test_a_non_finite_iv_is_refused(self):
        chain = self._chain((100, float("nan")), (110, 28.0), underlying=100.0)
        assert signal_repricer.atm_iv(chain) == 28.0

    def test_a_zero_or_negative_iv_is_not_a_reading(self):
        chain = self._chain((100, 0.0), (110, 28.0), underlying=100.0)
        assert signal_repricer.atm_iv(chain) == 28.0

    def test_junk_inputs_yield_None_rather_than_raising(self):
        for junk in (None, {}, {"underlying": {}}, "nope", {"putExpDateMap": {}}):
            assert signal_repricer.atm_iv(junk) is None

    def test_it_reads_the_call_map_when_there_are_no_puts(self):
        chain = {"underlying": {"last": 100.0},
                 "callExpDateMap": {"2026-09-04:10": {
                     "100.0": [{"bid": 1.0, "ask": 1.1, "volatility": 22.0}]}}}
        assert signal_repricer.atm_iv(chain) == 22.0


class TestAtmIvOnTheRealChainShape:
    """Measured against a live Schwab chain 2026-08-25: `underlying` came back
    **null** while the top-level `underlyingPrice` carried 98.19 and the contracts
    carried real volatilities. The fixtures above used the nested shape because
    that is what `reprice_swing` reads, so they could not have caught this — the
    IV was there and `atm_iv` refused it for want of a spot."""

    def test_it_falls_back_to_the_top_level_underlying_price(self):
        chain = {"underlying": None, "underlyingPrice": 98.19,
                 "putExpDateMap": {"2026-09-04:10": {
                     "98.0": [{"volatility": 46.3}],
                     "120.0": [{"volatility": 55.0}]}}}
        assert signal_repricer.atm_iv(chain) == 46.3

    def test_the_nested_shape_still_wins_when_both_are_present(self):
        chain = {"underlying": {"last": 120.0}, "underlyingPrice": 98.0,
                 "putExpDateMap": {"2026-09-04:10": {
                     "98.0": [{"volatility": 46.3}],
                     "120.0": [{"volatility": 55.0}]}}}
        assert signal_repricer.atm_iv(chain) == 55.0

    def test_neither_price_present_still_yields_None(self):
        chain = {"underlying": None,
                 "putExpDateMap": {"2026-09-04:10": {"98.0": [{"volatility": 46.3}]}}}
        assert signal_repricer.atm_iv(chain) is None
