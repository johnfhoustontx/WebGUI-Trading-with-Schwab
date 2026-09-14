"""Every Strategy Finder scan request gets an answer, and the answer carries the
spot price (design docs/plans/2026-09-14-strategy-finder-whole-chain-design.md
sections 5 and 6).

Two things the page could not tell apart before: a scan that raised published
nothing, so it waited out its ceiling as if the scan were slow; and an empty
result carried no price, so "no results" could not say at what price. The
quote is now read BEFORE the chain so it survives a missing chain.

"None in range" and "the fetch failed" are separate flags: ``fetch_scan_chain``
returns ``(None, 0)`` when the expiration list loaded but no listed expiry falls
in the window, ``(None, N>0)`` when runs failed, and ``(chain_or_None, None)``
from the single-fetch fallback, whose count was never taken.
"""
import math

import pytest

from services.options_svc import compute, handlers
from shared.bus import Bus

BANDS = (-0.2, -0.1, 0.1, 0.2, 0.1)


def _no_chain(monkeypatch, failed, quote):
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda symbol, dte_max: (None, failed))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: quote)


def test_no_chain_still_returns_the_spot(monkeypatch):
    _no_chain(monkeypatch, 3, {"last": 540.0})
    out = compute.swing_scan("SPY", 0, None, *BANDS)
    assert out["spot"] == 540.0 and out["chain_missing"] is True and out["signals"] == []
    assert out["no_expiries_in_range"] is False and out["expiries_failed"] == 3


def test_no_listed_expiry_in_range_is_not_a_missing_chain(monkeypatch):
    _no_chain(monkeypatch, 0, {"last": 540.0})
    out = compute.swing_scan("SPY", 0, 3, *BANDS)
    assert out["no_expiries_in_range"] is True and out["chain_missing"] is False
    assert out["spot"] == 540.0 and out["expiries_failed"] == 0


def test_a_fallback_fetch_that_returned_nothing_is_a_missing_chain(monkeypatch):
    """``(None, None)``: no expiration list AND the single fetch came back empty.
    The count was never taken, so it stays None - and the chain is missing."""
    _no_chain(monkeypatch, None, {"last": 540.0})
    out = compute.swing_scan("SPY", 0, None, *BANDS)
    assert out["chain_missing"] is True and out["no_expiries_in_range"] is False
    assert out["expiries_failed"] is None


def test_the_quote_is_read_before_the_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote",
                        lambda s: calls.append("quote") or {"last": 540.0})
    monkeypatch.setattr(compute, "fetch_scan_chain",
                        lambda symbol, dte_max: calls.append("chain") or (None, 2))
    compute.swing_scan("SPY", 0, None, *BANDS)
    assert calls == ["quote", "chain"]


def test_an_empty_scan_carries_the_spot(scan_env, monkeypatch):
    monkeypatch.setattr(compute, "SWING_MIN_SCORE", 101.0)        # cut everything
    out = compute.swing_scan("SPY", 0, None, *BANDS, payoff=False)
    assert out["signals"] == [] and out["spot"] == 540.0
    assert out["chain_missing"] is False and out["no_expiries_in_range"] is False


def test_no_quote_and_no_chain_spot_is_None_not_zero(monkeypatch):
    _no_chain(monkeypatch, 0, {})
    out = compute.swing_scan("SPY", 0, None, *BANDS)
    assert out["spot"] is None


@pytest.mark.parametrize("bad", [0, 0.0, -1.0, math.nan, math.inf, None, "540", True])
def test_an_unusable_quote_falls_back_to_the_chain(bad, scan_env, monkeypatch):
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: {"last": bad})
    out = compute.swing_scan("SPY", 0, None, *BANDS, payoff=False)
    assert out["spot"] == scan_env.chain["underlyingPrice"]


@pytest.mark.parametrize("bad", [0, -1.0, math.nan, None])
def test_no_usable_price_anywhere_is_None_and_nothing_is_built(bad, monkeypatch):
    """The spot guard keeps its meaning (the builders price off spot) - the
    result now says so with ``spot: None``, never a zero."""
    monkeypatch.setattr(compute, "fetch_scan_chain", lambda symbol, dte_max: (
        {"underlyingPrice": bad, "callExpDateMap": {"x": {}}, "putExpDateMap": {}}, 0))
    monkeypatch.setattr(compute._proxy.schwab_client, "get_quote", lambda s: {"last": bad})
    out = compute.swing_scan("SPY", 0, None, *BANDS)
    assert out["signals"] == [] and out["spot"] is None
    assert out["chain_missing"] is False and out["no_expiries_in_range"] is False


def test_the_handler_publishes_spot_and_the_failed_expiry_count(monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan", lambda **k: {
        "signals": [], "view": {}, "filtered_out": 3, "vol_filtered": 0,
        "not_shown": 4, "spot": 764.48, "chain_missing": False,
        "no_expiries_in_range": False, "expiries_failed": 2})
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "SPY", "dte_max": None})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["spot"] == 764.48 and p["expiries_failed"] == 2
    assert p["chain_missing"] is False and p["no_expiries_in_range"] is False
    assert p["not_shown"] == 4 and p["params"]["dte_max"] is None
    assert "error" not in p


def test_the_handler_publishes_both_flags_and_an_uncounted_fetch_as_None(monkeypatch):
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan", lambda **k: {
        "signals": [], "view": {}, "spot": None, "chain_missing": True,
        "no_expiries_in_range": False, "expiries_failed": None})
    bus = Bus(fake=True)
    handlers.swing_scan(bus, {"symbol": "SPY"})
    p = bus.cache_get(handlers.CACHE_SWING).payload
    assert p["chain_missing"] is True and p["no_expiries_in_range"] is False
    assert p["expiries_failed"] is None and p["spot"] is None


def test_a_scan_that_raises_still_answers_its_request(monkeypatch):
    """Otherwise nothing is published and the page waits out its ceiling."""
    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))

    def _boom(**k):
        raise RuntimeError("builder bug")

    monkeypatch.setattr(compute, "swing_scan", _boom)
    bus = Bus(fake=True)
    sub = bus.subscribe(handlers.EVENT_SWING)
    handlers.swing_scan(bus, {"symbol": "SPY", "dte_min": 7})
    env = bus.cache_get(handlers.CACHE_SWING)
    p = env.payload
    assert p["error"] is True and p["signals"] == [] and p["symbol"] == "SPY"
    assert p["params"] == {"symbol": "SPY", "dte_min": 7}
    assert p["view"] == {} and p["spot"] is None and p["expiries_failed"] is None
    assert p["filtered_out"] == 0 and p["vol_filtered"] == 0 and p["not_shown"] == 0
    # The same publish path as a normal answer, so the page's version poll sees it.
    msg = sub.get_message(timeout=1.0)
    sub.close()
    assert msg is not None and msg["version"] == env.version


def test_a_raising_scan_is_recorded_as_a_degrade(monkeypatch):
    from services import _degrade

    monkeypatch.setattr(compute, "scan_earnings", lambda s: ("not_listed", None))
    monkeypatch.setattr(compute, "swing_scan",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    before = _degrade.counts().get("options.swing_scan", 0)
    handlers.swing_scan(Bus(fake=True), {"symbol": "SPY"})
    assert _degrade.counts().get("options.swing_scan", 0) == before + 1
