"""The ETF hover card's view: each fund's top holdings, quoted."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from services.market_svc import holdings as H
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.market import MarketHoldings

_CT = ZoneInfo("America/Chicago")
_SESSION = dt.datetime(2026, 11, 12, 10, 0, tzinfo=_CT)     # a Thursday, open
_NIGHT = dt.datetime(2026, 11, 12, 20, 0, tzinfo=_CT)


def _cfg(**over):
    cfg = {"enabled": True, "top_n": 2, "rth_refresh_sec": 15,
           "offhours_refresh_sec": 300, "stale_after_h": 96,
           "as_of": "2026-11-12",
           "funds": {"XLK": ["NVDA", "AAPL", "MSFT"], "SMH": ["NVDA", "TSM"],
                     "USO": []},
           "names": {"NVDA": "NVIDIA", "AAPL": "Apple", "TSM": "TSMC (ADR)"},
           "notes": {"USO": "Holds oil futures, not stocks"}}
    cfg.update(over)
    return cfg


def _q(last, pct, traded=None):
    # A quote trades "now" unless a test says otherwise: one with no trade time
    # at all is a different case (see the no-trade test below).
    traded = _SESSION.timestamp() - 60 if traded is None else traded
    q = {"lastPrice": last, "netChange": 1.0, "netPercentChange": pct,
         "tradeTime": int(traded * 1000)}
    return {"assetMainType": "EQUITY", "quote": q}


@pytest.fixture(autouse=True)
def _empty_bus():
    reset_fake_bus()
    yield


def test_quote_symbols_are_the_shown_holdings_once_each():
    # top_n = 2: MSFT is listed but never shown, so it is never fetched.
    assert H.quote_symbols(_cfg()) == ["NVDA", "AAPL", "TSM"]


def test_build_reads_price_and_move_from_the_quote():
    raw = {"NVDA": _q(186.2, 1.25), "AAPL": _q(230.0, -0.5)}
    view = H.build(raw, _cfg(), ts=1_700_000_000)
    MarketHoldings(**view)
    assert view["as_of"] == "2026-11-12" and view["ts"] == 1_700_000_000
    assert view["funds"]["XLK"]["rows"] == [
        {"symbol": "NVDA", "name": "NVIDIA", "last": 186.2, "change_pct": 1.25,
         "stale": False, "traded": None},
        {"symbol": "AAPL", "name": "Apple", "last": 230.0, "change_pct": -0.5,
         "stale": False, "traded": None},
    ]


def test_a_holding_with_no_quote_is_absent_never_zero():
    view = H.build({"NVDA": _q(186.2, 1.25)}, _cfg(), ts=1)
    tsm = view["funds"]["SMH"]["rows"][1]
    assert tsm["symbol"] == "TSM" and tsm["last"] is None and tsm["change_pct"] is None


def test_a_fund_with_no_stocks_carries_its_note():
    view = H.build({}, _cfg(), ts=1)
    assert view["funds"]["USO"] == {"rows": [], "note": "Holds oil futures, not stocks"}


def test_the_cadence_is_faster_in_the_session():
    assert H.interval(_cfg(), _SESSION) == 15
    assert H.interval(_cfg(), _NIGHT) == 300


def test_refresh_publishes_then_waits_for_the_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(H.cfg_mod, "load", lambda: _cfg())
    monkeypatch.setattr(H.compute, "fetch_raw_quotes",
                        lambda syms: calls.append(syms) or {"NVDA": _q(1.0, 2.0)})
    bus, state = Bus(), {}
    assert H.refresh(bus, state, now=_SESSION, wall=1000.0) is True
    assert bus.cache_get(H.CACHE).payload["funds"]["XLK"]["rows"][0]["last"] == 1.0
    assert H.refresh(bus, state, now=_SESSION, wall=1010.0) is False   # not due
    assert H.refresh(bus, state, now=_SESSION, wall=1015.0) is True
    assert len(calls) == 2


def test_a_failed_fetch_publishes_nothing(monkeypatch):
    monkeypatch.setattr(H.cfg_mod, "load", lambda: _cfg())
    monkeypatch.setattr(H.compute, "fetch_raw_quotes", lambda syms: {})
    bus = Bus()
    assert H.refresh(bus, {}, now=_SESSION, wall=1000.0) is False
    assert bus.cache_get(H.CACHE) is None


def test_switching_off_publishes_one_empty_view(monkeypatch):
    monkeypatch.setattr(H.cfg_mod, "load", lambda: _cfg(enabled=False))
    monkeypatch.setattr(H.compute, "fetch_raw_quotes",
                        lambda syms: pytest.fail("no fetch while switched off"))
    bus, state = Bus(), {}
    H.refresh(bus, state, now=_SESSION, wall=1000.0)
    v1 = bus.cache_version(H.CACHE)
    env = bus.cache_get(H.CACHE)
    assert env.payload["enabled"] is False and env.payload["funds"] == {}
    H.refresh(bus, state, now=_SESSION, wall=2000.0)
    assert v1 and bus.cache_version(H.CACHE) == v1    # published once, not every tick


def test_switching_back_on_fetches_at_once(monkeypatch):
    on = {"v": False}
    monkeypatch.setattr(H.cfg_mod, "load", lambda: _cfg(enabled=on["v"]))
    monkeypatch.setattr(H.compute, "fetch_raw_quotes", lambda syms: {"NVDA": _q(1.0, 1.0)})
    bus, state = Bus(), {}
    H.refresh(bus, state, now=_SESSION, wall=1000.0)
    on["v"] = True
    assert H.refresh(bus, state, now=_SESSION, wall=1001.0) is True


def test_refresh_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("bad config")
    monkeypatch.setattr(H.cfg_mod, "load", boom)
    assert H.refresh(Bus(), {}, now=_SESSION, wall=1.0) is False


def test_a_line_that_has_not_traded_for_days_shows_no_move():
    # Measured 2026-11-12: Samsung's OTC line SSNLF had not traded since April
    # and still reported +10.15% against a close as old.
    now = _SESSION.timestamp()
    raw = {"NVDA": _q(186.2, 1.25, traded=now - 60),
           "AAPL": _q(154.2, 10.15, traded=now - 97 * 3600)}
    rows = H.build(raw, _cfg(), ts=now)["funds"]["XLK"]["rows"]
    assert rows[0]["stale"] is False and rows[0]["change_pct"] == 1.25
    assert rows[1]["stale"] is True and rows[1]["change_pct"] is None
    assert rows[1]["last"] == 154.2
    assert rows[1]["traded"] == "2026-11-08"


def test_a_weekend_is_not_stale():
    # Friday's close read on Monday morning is ~65 hours old.
    now = _SESSION.timestamp()
    rows = H.build({"NVDA": _q(1.0, 2.0, traded=now - 65 * 3600)}, _cfg(), ts=now)
    assert rows["funds"]["XLK"]["rows"][0]["change_pct"] == 2.0


def test_a_line_with_no_trade_on_record_shows_no_price_and_no_move():
    # Measured 2026-11-12: FUISF (Fubon, OTC) carried no trade time, a last of
    # 1.29 against a close of 4.85, and so read -73%.
    raw = {"NVDA": {"assetMainType": "EQUITY",
                    "quote": {"lastPrice": 1.29, "closePrice": 4.85,
                              "netPercentChange": -73.4}}}
    row = H.build(raw, _cfg(), ts=_SESSION.timestamp())["funds"]["XLK"]["rows"][0]
    assert row == {"symbol": "NVDA", "name": "NVIDIA", "last": None,
                   "change_pct": None, "stale": True, "traded": None}


def test_no_quote_at_all_is_not_called_stale():
    row = H.build({}, _cfg(), ts=_SESSION.timestamp())["funds"]["XLK"]["rows"][0]
    assert row["last"] is None and row["stale"] is False
