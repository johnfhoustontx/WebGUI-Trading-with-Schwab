"""Tests for the directional second pass through screen_spreads."""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scanner_engine import screen_spreads


TZ = ZoneInfo("America/Chicago")


def _make_chain(spot=500.0, exp="2026-06-05", dte=7):
    """Build a minimal swing chain spanning 470-530 in $1 increments.

    Each contract gets crude delta approximation linear in distance from
    spot, plus enough volume/OI to clear the liquidity gate. Marks scale
    with distance so the credit/width ratio works out across reasonable
    widths.
    """
    put_strikes = {}
    call_strikes = {}
    for k in range(470, 531):
        # Put delta: steeper so directional band (within ~8 of spot) hits
        # the -0.55..-0.30 short-delta range. At k=spot, delta ~ -0.5.
        if k <= spot:
            put_delta = max(-0.99, -0.5 + (k - spot) / 32.0)
        else:
            put_delta = max(-0.49, -0.5 + (k - spot) / 32.0)
            put_delta = max(put_delta, -0.99)
            put_delta = min(put_delta, -0.01)
        # Call delta: mirror — at k=spot, delta ~ 0.5
        if k >= spot:
            call_delta = min(0.99, 0.5 - (k - spot) / 32.0)
        else:
            call_delta = min(0.99, 0.5 - (k - spot) / 32.0)
        call_delta = max(0.01, min(0.99, call_delta))
        # Put mark: higher closer to / above spot (ATM puts most expensive,
        # deep OTM cheap). put_mark roughly = |put_delta| * scale + base.
        put_mark = max(0.10, abs(put_delta) * 15.0 + 0.20)
        # Call mark: higher closer to / below spot (ATM calls most expensive).
        call_mark = max(0.10, abs(call_delta) * 15.0 + 0.20)
        # Tight bid-ask (penny markets) so the spread gate passes.
        common = {
            "totalVolume": 500, "openInterest": 1000,
            "volatility": 18.0, "theta": -0.05, "vega": 0.10, "gamma": 0.01,
        }
        put_strikes[str(float(k))] = [{
            "delta": put_delta,
            "mark": put_mark,
            "bid": round(put_mark - 0.02, 4), "ask": round(put_mark + 0.02, 4),
            **common,
        }]
        call_strikes[str(float(k))] = [{
            "delta": call_delta,
            "mark": call_mark,
            "bid": round(call_mark - 0.02, 4), "ask": round(call_mark + 0.02, 4),
            **common,
        }]

    return {
        "underlyingPrice": spot,
        "putExpDateMap": {f"{exp}:{dte}": put_strikes},
        "callExpDateMap": {f"{exp}:{dte}": call_strikes},
    }


@pytest.fixture(autouse=True)
def _force_market_open(monkeypatch):
    """Pin market-open so liquidity gate's market-hours behavior is what's tested."""
    import scanner_engine
    monkeypatch.setattr(scanner_engine, "_is_options_market_open", lambda: True)


def test_directional_mode_tags_signals():
    """When mode='DIRECTIONAL' is passed, every produced signal carries mode='DIRECTIONAL'."""
    chain = _make_chain()
    now = datetime(2026, 5, 28, 10, 0, tzinfo=TZ)
    signals = screen_spreads(
        chain, "SPY", dte_min=7, dte_max=7,
        put_d_min=-0.55, put_d_max=-0.30,
        call_d_min=0.30, call_d_max=0.55,
        min_cr_pct=0.10, trade_type="SWING",
        spot=500.0, daily_expected_move=5.0, now_ct=now,
        mode="DIRECTIONAL",
    )
    assert len(signals) > 0
    assert all(s["mode"] == "DIRECTIONAL" for s in signals)


def test_directional_strikes_in_zero_to_0618_em_band():
    """Directional short strikes sit within 0.0x-0.618x of period EM from spot."""
    chain = _make_chain()
    now = datetime(2026, 5, 28, 10, 0, tzinfo=TZ)
    daily_em = 5.0
    dte = 7
    period_em = daily_em * math.sqrt(dte)
    max_dist = 0.618 * period_em

    signals = screen_spreads(
        chain, "SPY", dte_min=dte, dte_max=dte,
        put_d_min=-0.55, put_d_max=-0.30,
        call_d_min=0.30, call_d_max=0.55,
        min_cr_pct=0.10, trade_type="SWING",
        spot=500.0, daily_expected_move=daily_em, now_ct=now,
        mode="DIRECTIONAL",
    )
    assert len(signals) > 0
    for s in signals:
        dist = abs(s["short_strike"] - 500.0)
        assert dist <= max_dist + 1e-6, (
            f"Short strike {s['short_strike']} too far ({dist:.2f} > {max_dist:.2f})"
        )


def test_premium_mode_unchanged_by_default():
    """Default screen_spreads call (no mode) still produces PREMIUM signals.

    Uses a delta band whose strikes sit in the PREMIUM EM window
    (~[11.25, 24.49] from spot at DTE=7, daily_em=5). Given the chain's
    linear delta-distance mapping, puts in (-0.95, -0.85) and calls in
    (0.05, 0.15) land in that band.
    """
    chain = _make_chain()
    now = datetime(2026, 5, 28, 10, 0, tzinfo=TZ)
    signals = screen_spreads(
        chain, "SPY", dte_min=7, dte_max=7,
        put_d_min=-0.95, put_d_max=-0.85,
        call_d_min=0.05, call_d_max=0.15,
        min_cr_pct=0.10, trade_type="SWING",
        spot=500.0, daily_expected_move=5.0, now_ct=now,
    )
    assert len(signals) > 0
    assert all(s["mode"] == "PREMIUM" for s in signals)


#############################################
# RUN_FULL_SCAN DIRECTIONAL PASS INTEGRATION
#############################################

class _StubResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def json(self):
        return self._payload
    def raise_for_status(self):
        pass


def _make_chain_symmetric(spot=500.0, exp="2026-06-05", dte=7):
    """Build a chain with realistic OTM-side delta+mark conventions on BOTH
    puts and calls, so PCS and CCS each produce positive credit spreads.

    Unlike `_make_chain` (used by the unit tests, which intentionally inverts
    put deltas to exercise edge cases), here:
      - OTM puts (k < spot) have small |delta|, low marks
      - OTM calls (k > spot) have small delta, low marks
      - ATM strikes have |delta| ~ 0.5 and the highest marks
    """
    put_strikes = {}
    call_strikes = {}
    for k in range(470, 531):
        # Distance below spot for puts; OTM = k < spot. Delta magnitude grows
        # as we move ITM (k > spot for puts). Use a smooth ramp.
        # Put delta ~ -0.5 at ATM, ~0 deep OTM (k<<spot), ~-1 deep ITM (k>>spot).
        # Convention: |delta| grows with k (above-spot puts are ITM).
        put_delta = -0.5 - (k - spot) / 32.0
        put_delta = max(-0.99, min(-0.01, put_delta))
        # Call delta ~ 0.5 at ATM, ~0 deep OTM (k>>spot), ~1 deep ITM (k<<spot).
        call_delta = 0.5 - (k - spot) / 32.0
        call_delta = max(0.01, min(0.99, call_delta))
        # Marks: higher when |delta| is closer to 0.5+ (ATM/ITM region).
        # For puts, mark scales with |put_delta| (ITM puts cost more).
        put_mark = max(0.10, abs(put_delta) * 15.0 + 0.20)
        # For calls, mark scales with call_delta (ITM calls cost more).
        call_mark = max(0.10, call_delta * 15.0 + 0.20)
        common = {
            "totalVolume": 500, "openInterest": 1000,
            "volatility": 18.0, "theta": -0.05, "vega": 0.10, "gamma": 0.01,
        }
        put_strikes[str(float(k))] = [{
            "delta": put_delta,
            "mark": put_mark,
            "bid": round(put_mark - 0.02, 4), "ask": round(put_mark + 0.02, 4),
            **common,
        }]
        call_strikes[str(float(k))] = [{
            "delta": call_delta,
            "mark": call_mark,
            "bid": round(call_mark - 0.02, 4), "ask": round(call_mark + 0.02, 4),
            **common,
        }]
    return {
        "underlyingPrice": spot,
        "putExpDateMap": {f"{exp}:{dte}": put_strikes},
        "callExpDateMap": {f"{exp}:{dte}": call_strikes},
    }


def _stub_client(price=500.0):
    """Minimal Schwab client stub returning a fixed price + chain + history.

    Returns the same DTE=7 swing chain for every option-chain call. Buckets
    that don't match (0-DTE asking for 0-4 DTE) see no matching expirations
    and return [] from screen_spreads — exactly what we want, since the
    directional pass tests focus on the SWING bucket here.
    """
    swing_chain = _make_chain_symmetric(spot=price, dte=7)
    # Use status=FAILED so run_full_scan's `chain_0.get("status") != "FAILED"`
    # guard skips it cleanly — avoids tripping a pre-existing unbound-local
    # in screen_spreads' "no matches" log path on empty chains.
    empty_chain = {
        "underlyingPrice": price,
        "putExpDateMap": {},
        "callExpDateMap": {},
        "status": "FAILED",
    }

    class _Stub:
        class Options:
            class ContractType:
                ALL = "ALL"

        def get_quotes(self, symbols):
            quotes = {}
            for s in symbols:
                if s in ("$VIX", "$VIX9D", "$VIX3M"):
                    quotes[s] = {"quote": {"lastPrice": 18.0}}
                else:
                    quotes[s] = {"quote": {"lastPrice": price}}
            return _StubResponse(quotes)

        def get_option_chain(self, symbol, **kwargs):
            # Return the DTE=7 swing chain only when from_date lies in the
            # 5-15-DTE swing window; return an empty chain otherwise.
            # This keeps the 0-DTE bucket from running screen_spreads against
            # a chain whose only expiration is DTE=7 (which would trip a
            # pre-existing unbound-local in the "no matches" log path).
            from_date = kwargs.get("from_date")
            from datetime import date, timedelta
            today = date.today()
            swing_lo = today + timedelta(days=5)
            swing_hi = today + timedelta(days=15)
            if from_date is not None and swing_lo <= from_date <= swing_hi:
                return _StubResponse(swing_chain)
            return _StubResponse(empty_chain)

        def get_price_history_every_day(self, symbol):
            candles = []
            base = price - 30
            for i in range(60):
                c = base + i * 0.5
                candles.append({
                    "open": c - 0.5, "high": c + 1.0, "low": c - 1.0,
                    "close": c, "datetime": i,
                })
            return _StubResponse({"candles": candles})

    return _Stub()


def _stub_regime_evaluate(monkeypatch, active, allow_ccs, allow_pcs, bias):
    """Monkeypatch regime_filter.evaluate_regime so both early and late
    call sites in run_full_scan return the same regime dict, and bypass the
    post-scan IV Rank floor so the stub iv_data (which lacks iv_rank) doesn't
    filter our signals away.

    Note: run_full_scan still calls signal_recorder.record_signals; the
    production DB is protected by the autouse `_isolate_production_signal_db`
    fixture in conftest.py, which redirects default-path writes to a temp DB."""
    import regime_filter
    import scanner_engine
    fake = lambda **kw: {
        "active": active, "allow_ccs": allow_ccs, "allow_pcs": allow_pcs,
        "per_symbol": {}, "bias": bias,
        "trend_state": None, "trend_confidence": None,
        "aggregate_confidence": None, "divergence_flag": None,
        "composite_score": 5.0,
        "reason": f"test {bias}",
    }
    monkeypatch.setattr(regime_filter, "evaluate_regime", fake)
    # Bypass IV Rank floor — stub iv_data has iv_rank=None which would drop everything.
    monkeypatch.setattr(scanner_engine, "MIN_IV_RANK", {"0-DTE": 0, "SWING": 0})


def test_run_full_scan_skips_directional_when_regime_inactive(monkeypatch):
    """When regime.active is False, no directional signals are produced."""
    _stub_regime_evaluate(monkeypatch, active=False, allow_ccs=True, allow_pcs=True, bias="neutral")
    from scanner_engine import run_full_scan
    results = run_full_scan(client=_stub_client(), symbols=["SPY"])
    all_signals = results["signals_0dte"] + results["signals_swing"]
    assert not any(s.get("mode") == "DIRECTIONAL" for s in all_signals)


def test_run_full_scan_bullish_produces_directional_pcs(monkeypatch):
    """Regime BULLISH (allow_ccs=False) -> directional PCS produced; no directional CCS."""
    _stub_regime_evaluate(monkeypatch, active=True, allow_ccs=False, allow_pcs=True, bias="bullish")
    from scanner_engine import run_full_scan
    results = run_full_scan(client=_stub_client(), symbols=["SPY"])
    directional = [s for s in (results["signals_0dte"] + results["signals_swing"])
                   if s.get("mode") == "DIRECTIONAL"]
    assert len(directional) > 0
    assert all(s["mode"] == "DIRECTIONAL" for s in directional)
    assert any(s["type"] == "PCS" for s in directional), \
        f"Expected directional PCS, got: {[(s['type'], s.get('mode')) for s in directional]}"
    assert not any(s["type"] == "CCS" for s in directional)


def test_run_full_scan_bearish_produces_directional_ccs(monkeypatch):
    """Regime BEARISH (allow_pcs=False) -> directional CCS produced; no directional PCS."""
    _stub_regime_evaluate(monkeypatch, active=True, allow_ccs=True, allow_pcs=False, bias="bearish")
    from scanner_engine import run_full_scan
    results = run_full_scan(client=_stub_client(), symbols=["SPY"])
    directional = [s for s in (results["signals_0dte"] + results["signals_swing"])
                   if s.get("mode") == "DIRECTIONAL"]
    assert len(directional) > 0
    assert all(s["mode"] == "DIRECTIONAL" for s in directional)
    assert any(s["type"] == "CCS" for s in directional)
    assert not any(s["type"] == "PCS" for s in directional)


def test_directional_signals_capped_at_two_per_symbol_per_bucket(monkeypatch):
    """No more than 2 directional signals per symbol per bucket."""
    _stub_regime_evaluate(monkeypatch, active=True, allow_ccs=False, allow_pcs=True, bias="bullish")
    from scanner_engine import run_full_scan
    results = run_full_scan(client=_stub_client(), symbols=["SPY"])
    for bucket_key in ("signals_0dte", "signals_swing"):
        dir_sigs = [s for s in results[bucket_key]
                   if s.get("mode") == "DIRECTIONAL" and s["symbol"] == "SPY"]
        assert len(dir_sigs) <= 2
