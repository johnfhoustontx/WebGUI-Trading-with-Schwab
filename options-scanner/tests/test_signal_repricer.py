import datetime

import signal_repricer


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


def test_intrinsic_ic_both_wings_safe():
    t = {"strategy": "IC", "short_strike": 690, "long_strike": 688,
         "call_short": 710, "call_long": 712, "entry_credit": 1.0}
    v, pnl = signal_repricer.intrinsic_value(t, settlement=700.0)
    assert v == 0
    assert pnl == 100.0


def test_reprice_swing_from_mock_chain(monkeypatch):
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": "2026-04-24", "entry_credit": 1.30,
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
         "expiration": "2026-04-24", "entry_credit": 0.80,
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
         "call_short": 610, "call_long": 615, "expiration": "2026-04-24",
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
         "expiration": "2026-04-24", "entry_credit": 1.30,
         "call_short": None, "call_long": None}

    def boom(*a, **kw):
        raise RuntimeError("api down")
    monkeypatch.setattr(signal_repricer, "_fetch_chain", boom)
    result = signal_repricer.reprice_swing(t, client=object())
    assert result["current_value"] is None
    assert result["error"] == "repricing failed"


def test_reprice_swing_missing_leg_quotes_returns_error(monkeypatch):
    t = {"strategy": "PCS", "symbol": "QQQ", "short_strike": 590, "long_strike": 585,
         "expiration": "2026-04-24", "entry_credit": 1.30,
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
