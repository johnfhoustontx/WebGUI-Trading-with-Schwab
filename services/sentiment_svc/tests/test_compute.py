"""Tests for the NiceGUI-free sentiment compute module.

These call real engine code, so we test the safe/defensive paths by
monkeypatching the proxy accessor so nothing requires a live proxy.
"""
import sys

import pandas as pd

from services import _proxy
from services.sentiment_svc import compute


def test_load_live_returns_none_on_client_error(monkeypatch):
    """load_live swallows engine errors and returns None (defensive).

    We make sectors_ref.load_sectors_data raise so the exception escapes into
    load_live's try/except (compute_live itself tolerates a broken client and
    returns a zeroed snapshot, so patching the client alone won't trip it)."""
    import sectors_ref

    def _raise(*a, **k):
        raise RuntimeError("sectors error")

    monkeypatch.setattr(sectors_ref, "load_sectors_data", _raise)
    assert compute.load_live() is None


def test_proxy_up_false_when_health_raises(monkeypatch):
    """proxy_up returns False when the health check raises (defensive)."""
    def _raise(*a, **k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(_proxy, "health", _raise)
    assert compute.proxy_up() is False


def _snap(date, total, **scores):
    base = {"vix_complex": 5.0, "put_call": 5.0, "breadth": 5.0,
            "rotation": 5.0, "sector_perf": 5.0, "credit_pulse": 5.0}
    base.update(scores)
    return {
        "date": date,
        "composite": {"total_score": f"{total:.2f}", "bias": "Neutral",
                      "size_modifier": "1.00x", "aggregate_confidence": 0.8},
        "component_scores": base,
        "component_confidence": {k: 0.9 for k in base},
    }


def test_derive_composite_extras_shape_and_values():
    """derive_composite_extras returns the full derived payload with real
    scoring-computed weights/band/velocity/divergence/trend."""
    snaps = [_snap(f"2026-05-{d:02d}", 5.0 + (d % 3) * 0.2) for d in range(1, 21)]
    live = _snap("2026-06-01", 8.0, vix_complex=9.0, sector_perf=2.0)
    spy = [100.0 + i * 0.5 for i in range(260)]
    out = compute.derive_composite_extras(live, snaps, spy)

    assert set(out) == {"weights", "size", "bias", "signal",
                        "velocity", "divergence", "trend", "trend_30d_ago"}
    # weights = sentiment v4.3 WEIGHTS (credit_pulse excluded, sums to 1.0).
    assert abs(sum(out["weights"].values()) - 1.0) < 1e-9
    assert "credit_pulse" not in out["weights"]
    assert out["weights"]["sector_perf"] == 0.25
    # band labels populated (signal_band over total 8.0).
    assert out["size"] and out["bias"] and out["signal"]
    # velocity text built from the prior series (full backfill since live).
    assert "3d ROC" in out["velocity"]["text"] and "20d Z" in out["velocity"]["text"]
    # vix 9 vs sector_perf 2 -> >=4 spread -> a divergence label.
    assert "DIVERGENCE" in out["divergence"]
    # trend defaults to the neutral directional dict when none is threaded in.
    tr = out["trend"]
    assert tr is not None
    assert tr["state"] in _TREND_STATES
    assert {"label", "description", "raw_state", "score", "smoothed_score",
            "confidence", "sub_scores"} <= set(tr)


def test_derive_composite_extras_defensive_no_spy():
    """No trend threaded in -> neutral placeholder; minimal snaps ->
    velocity dashes, no crash."""
    out = compute.derive_composite_extras(None, [], [])
    assert out["trend"]["state"] == "neutral"
    assert out["trend_30d_ago"]["state"] == "neutral"
    assert out["divergence"] == ""
    assert out["velocity"]["flag"] == ""
    assert isinstance(out["weights"], dict)


def test_build_trend_dict_none_on_empty():
    assert compute.build_trend_dict([]) is None
    assert compute.build_trend_dict(None) is None


def test_bridge_trend_merges_intraday_over_daily(monkeypatch):
    """``_bridge_trend`` overlays the intraday model's directional state +
    trend_score/sub_scores onto the daily classify dict's structural sma_*."""
    monkeypatch.setattr(compute, "build_trend_dict",
                        lambda spy: {"state": "range", "sma_50": 480.0,
                                     "sma_200": 450.0, "spy_close": 500.0,
                                     "confidence": 0.4})
    intraday = {"state": "bullish", "label": "Bullish", "description": "x",
                "raw_state": "bullish", "confidence": 0.9,
                "smoothed_score": 84.0, "sub_scores": {"price": 88}}
    merged = compute._bridge_trend(intraday, spy=[1.0, 2.0])
    assert merged["state"] == "bullish"
    assert merged["trend_score"] == 84.0
    assert merged["sub_scores"] == {"price": 88}
    assert merged["confidence"] == 0.9
    # structural fields preserved from the daily dict
    assert merged["sma_50"] == 480.0
    assert merged["spy_close"] == 500.0


def test_bridge_trend_falls_back_to_daily_when_no_intraday(monkeypatch):
    """No intraday state (cold start): keep the daily dict's structural
    back-compat fields, but the published ``state`` is OVERRIDDEN to the new-vocab
    NEUTRAL — never the daily dict's OLD-vocab band (which regime_filter, rekeyed
    to the five-state vocabulary, would fail-open on)."""
    daily = {"state": "range", "sma_50": 480.0, "drawdown": -0.03,
             "spy_close": 500.0, "confidence": 0.4}
    monkeypatch.setattr(compute, "build_trend_dict", lambda spy: dict(daily))

    for intraday in (None, {"confidence": 0.9}):  # absent / present-but-no-state
        out = compute._bridge_trend(intraday, spy=[1.0])
        # published state is the RECOGNIZED new-vocab neutral, not old-vocab.
        assert out["state"] == "neutral"
        assert out["state"] not in {"range", "bull_trend", "pullback_in_bull",
                                    "bear_rally", "bear_trend"}
        assert out["label"] == "Neutral"
        assert out["raw_state"] == "neutral"
        assert out["confidence"] == 0.0
        # structural back-compat fields still sourced from the daily dict.
        assert out["sma_50"] == 480.0
        assert out["drawdown"] == -0.03
        assert out["spy_close"] == 500.0

    # no daily dict at all -> None (nothing to publish, no old-vocab leak).
    monkeypatch.setattr(compute, "build_trend_dict", lambda spy: None)
    assert compute._bridge_trend(None, spy=[]) is None


def test_derive_sector_summary_values():
    sd = [
        {"kind": "sector", "sector": "Tech", "etf": "XLK",
         "sp_weight": 30.0, "name": "Tech"},
        {"kind": "sector", "sector": "Energy", "etf": "XLE",
         "sp_weight": 5.0, "name": "Energy"},
    ]
    quotes = {"XLK": {"change_pct": 1.0}, "XLE": {"change_pct": -0.5}}
    out = compute.derive_sector_summary({"sector_data": sd, "quotes": quotes})
    assert set(out) == {"wpct", "score"}
    assert out["wpct"] is not None
    assert isinstance(out["score"], float)


def test_derive_sector_summary_defensive():
    out = compute.derive_sector_summary(None)
    assert out == {"wpct": None, "score": 0.0}


def test_derive_composite_extras_includes_trend_30d_ago():
    snaps = [{"composite": {"total_score": "6.00"}}]
    out = compute.derive_composite_extras(live=None, snaps=snaps, spy=[])
    assert "trend" in out and "trend_30d_ago" in out
    t30 = out["trend_30d_ago"]
    assert t30 is not None and t30.get("state") in _TREND_STATES
    # neutral placeholder shape (directional, not the daily build_trend_dict).
    assert "sub_scores" in t30


def test_derive_composite_extras_trend_30d_passes_through():
    t30 = {"state": "bull_trend", "score": 90.0, "marker": "verbatim30"}
    out = compute.derive_composite_extras(live=None, snaps=[], spy=[],
                                          trend_30d=t30)
    assert out["trend_30d_ago"] == t30


# --- intraday trend (Phase 3) -------------------------------------------------

_TREND_STATES = {"bullish", "lack_of_bullishness", "neutral",
                 "lack_of_bearishness", "bearish"}


def _bars(n, start, step, vol=1_000_000):
    """A synthetic OHLCV DataFrame of ``n`` rising (step>0) / falling bars."""
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "open": closes,
        "high": [c + abs(step) for c in closes],
        "low": [c - abs(step) for c in closes],
        "close": closes,
        "volume": [vol] * n,
        "datetime": pd.date_range("2026-06-01", periods=n, freq="5min"),
    })


class _FakeBullSchwab:
    """Canned bullish market: rising bars, advn>decn, low/falling VIX, green
    cyclical sectors. Every method returns data (no failures)."""

    def get_intraday_history(self, symbol, minutes=15, days=1):
        if symbol == "SPY":
            return _bars(120, 500.0, 0.5)
        return _bars(120, 100.0, 0.2)

    def get_daily_history(self, symbol, months=12):
        if symbol == "$VIX":
            # last close lower than prior -> falling VIX
            return _bars(60, 20.0, -0.05)
        return _bars(260, 400.0, 0.6)

    def get_quote(self, symbol):
        if symbol == "$VIX":
            return {"last": 14.0}
        return {"last": 1.0}

    def get_quotes(self, symbols):
        out = {}
        for s in symbols:
            if s in ("$ADVN",):
                out[s] = {"last": 2400.0}
            elif s in ("$DECN",):
                out[s] = {"last": 600.0}
            elif s in ("$NYHGH", "$NYHGH.X", "$NEWH"):
                out[s] = {"last": 300.0}
            elif s in ("$NYLOW", "$NYLOW.X", "$NEWL"):
                out[s] = {"last": 40.0}
            elif s in ("$SPXA50R", "$NYA50R", "$MMFI"):
                out[s] = {"last": 72.0}
            elif s in ("$VIX1D",):
                out[s] = {"last": 13.0}
            elif s in ("$VIX9D",):
                out[s] = {"last": 15.0}
            else:
                # sector / industry ETFs -> green day move
                out[s] = {"last": 100.0, "change_pct": 1.2}
        return out


class _FakeDeadSchwab:
    """Every method raises or returns None (proxy down / no data)."""

    def get_intraday_history(self, *a, **k):
        raise RuntimeError("dead")

    def get_daily_history(self, *a, **k):
        return None

    def get_quote(self, *a, **k):
        raise RuntimeError("dead")

    def get_quotes(self, *a, **k):
        return None


def test_compute_intraday_trend_shape_and_bull():
    # Bullish DIRECTION (rising tape) + POSITIVE aggression (falling put fear /
    # falling put demand) -> the ``bullish`` state.
    out = compute.compute_intraday_trend(
        _FakeBullSchwab(),
        flow_skew={"SPY": {"rr_delta": -3.0}},
        sector_pc_delta=-0.2)
    for k in ("score", "smoothed_score", "state", "label", "description",
              "sub_scores", "sub_confidence", "confidence",
              "aggression", "aggression_confidence", "evidence"):
        assert k in out, f"missing key {k}"
    assert 0.0 <= out["score"] <= 100.0
    assert out["score"] > 60.0
    assert out["aggression"] > 0.0          # positive aggression from the inputs
    assert out["state"] == "bullish"
    assert set(out["sub_scores"]) == {"price", "breadth", "sector", "vix"}
    assert set(out["sub_confidence"]) == {"price", "breadth", "sector", "vix"}
    # The 45% price input must actually contribute — a DataFrame in a boolean
    # `or` used to raise "truth value ambiguous", silently zeroing this block.
    assert out["sub_confidence"]["price"] > 0.0
    assert out["sub_scores"]["price"] > 50.0   # rising tape -> bullish price score
    # evidence carries the direction + aggression readouts.
    assert any("direction" in e for e in out["evidence"])
    assert any("aggression" in e for e in out["evidence"])


def test_compute_intraday_trend_bull_direction_negative_aggression():
    """A bullish DIRECTION with NEGATIVE aggression (rising put fear via a
    positive rr_delta + rising sector put demand) flips to lack_of_bullishness —
    the aggression axis, not direction alone, decides the state."""
    out = compute.compute_intraday_trend(
        _FakeBullSchwab(),
        flow_skew={"SPY": {"rr_delta": 3.0}},
        sector_pc_delta=0.2)
    assert out["score"] > 60.0              # direction still bullish
    assert out["aggression"] < 0.0          # but aggression is negative
    assert out["state"] == "lack_of_bullishness"
    # both put-skew + sector P/C evidence lines present.
    assert any("put-skew" in e for e in out["evidence"])
    assert any("sector P/C" in e for e in out["evidence"])


def test_compute_intraday_trend_graceful_without_flow_inputs():
    """Missing flow_skew + None sector_pc_delta still classifies (effort-only /
    neutral aggression) without crashing — a valid new-vocab state."""
    out = compute.compute_intraday_trend(_FakeBullSchwab())  # no flow inputs
    assert out["state"] in _TREND_STATES
    assert {"aggression", "aggression_confidence", "evidence"} <= set(out)
    # no put-skew / sector-P&C evidence when those inputs are absent.
    assert not any("put-skew" in e for e in out["evidence"])
    assert not any("sector P/C" in e for e in out["evidence"])


def test_compute_intraday_trend_defensive_no_data():
    out = compute.compute_intraday_trend(_FakeDeadSchwab())
    assert out["score"] == 50.0
    assert out["confidence"] == 0.0
    assert out["state"] == "neutral"
    assert out["aggression"] == 0.0
    assert set(out["sub_scores"]) == {"price", "breadth", "sector", "vix"}


def test_compute_intraday_trend_hysteresis_threads_state():
    """A single divergent read does NOT flip a prior committed new-vocab state
    (2-day hysteresis): commit_state needs HYSTERESIS_DAYS matching raws to flip."""
    out = compute.compute_intraday_trend(
        _FakeBullSchwab(), prior_history=["neutral"], prior_committed="neutral",
        flow_skew={"SPY": {"rr_delta": -3.0}}, sector_pc_delta=-0.2)
    # committed state stays 'neutral' on the first divergent read.
    assert out["state"] == "neutral"
    # raw_state reflects the fresh (bullish) classification.
    assert out["raw_state"] == "bullish"
    # the rolling history was advanced by commit_state.
    assert out["state_history"] and out["state_history"][-1] == out["raw_state"]


def test_compute_intraday_trend_migration_guard_old_vocab_prior():
    """A stale OLD-vocab prior_committed ('range') is treated as a cold start —
    the new-vocab state is adopted immediately (no 2-read transient)."""
    out = compute.compute_intraday_trend(
        _FakeBullSchwab(), prior_history=["range"], prior_committed="range",
        flow_skew={"SPY": {"rr_delta": -3.0}}, sector_pc_delta=-0.2)
    assert out["state"] in _TREND_STATES
    assert out["state"] != "range"
    # cold start -> committed == raw (adopted immediately, not held at 'range').
    assert out["state"] == out["raw_state"] == "bullish"


# --- micro-structure refinements: session / rejection / profile ---------------

def _wick_bars(n, start, step, wick, tail, vol=1_000_000):
    """A rising DAILY OHLCV frame with big UPPER wicks + tiny lower tails —
    exhaustion/rejection near the highs (bearish for aggression)."""
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "open": closes,
        "high": [c + wick for c in closes],   # long upper wick
        "low": [c - tail for c in closes],     # tiny lower tail
        "close": closes,
        "volume": [vol] * n,
        "datetime": pd.date_range("2026-06-01", periods=n, freq="D"),
    })


class _FakeExhaustedRallySchwab(_FakeBullSchwab):
    """Rising intraday tape (bullish DIRECTION) but the DAILY SPY frame prints
    big upper wicks near the highs -> rejection/exhaustion (negative aggression)."""

    def get_daily_history(self, symbol, months=12):
        if symbol == "$VIX":
            return _bars(60, 20.0, -0.05)
        return _wick_bars(60, 400.0, 0.6, wick=3.0, tail=0.2)


def test_session_structure_nudges_price_direction_up(monkeypatch):
    """A strongly-bullish session structure (above VWAP + OR break) blends INTO
    the price sub-score, lifting the direction vs the same read with no session."""
    ss = compute.session_structure_mod
    # control: session forced to zero confidence -> no blend, price untouched.
    monkeypatch.setattr(ss, "score_session_structure",
                        lambda *a, **k: ss.SessionStructure(0.0, 0.0))
    base = compute.compute_intraday_trend(_FakeBullSchwab())
    # treatment: a max-bullish structure blends in and raises the price sub-score.
    monkeypatch.setattr(ss, "score_session_structure",
                        lambda *a, **k: ss.SessionStructure(1.0, 1.0))
    boosted = compute.compute_intraday_trend(_FakeBullSchwab())
    assert boosted["sub_scores"]["price"] > base["sub_scores"]["price"]
    # the session readout appears only when it carries confidence.
    assert any("session" in e for e in boosted["evidence"])
    assert not any("session" in e for e in base["evidence"])


def test_rejection_pushes_aggression_more_negative(monkeypatch):
    """Big daily upper wicks (rejection) drag the aggression axis MORE negative;
    a bullish direction + negative aggression -> lack_of_bullishness."""
    sch = _FakeExhaustedRallySchwab()
    real = compute.compute_intraday_trend(sch)          # real rejection reading
    rd = compute.rejection_mod
    monkeypatch.setattr(rd, "score_rejection_defense",
                        lambda *a, **k: rd.RejectionDefense(0.0, 0.0))
    base = compute.compute_intraday_trend(sch)          # rejection dropped out
    assert real["score"] > 60.0                         # direction still bullish
    assert real["aggression"] < base["aggression"]      # rejection dragged it down
    assert real["aggression"] < 0.0
    assert real["state"] == "lack_of_bullishness"
    assert any("rejection/defense" in e for e in real["evidence"])
    assert not any("rejection/defense" in e for e in base["evidence"])


def test_balanced_profile_dampens_aggression(monkeypatch):
    """A strongly-balanced single-HVN profile shrinks |aggression| toward 0
    (rotational balance -> more likely Neutral)."""
    ps = compute.profile_mod
    sch = _FakeBullSchwab()
    kwargs = dict(flow_skew={"SPY": {"rr_delta": -1.0}})   # mild positive aggression
    # control: no balance (strength 0) -> no damping.
    monkeypatch.setattr(ps, "classify_profile_shape",
                        lambda *a, **k: ps.ProfileShape("trend", 0.0, 0.0))
    base = compute.compute_intraday_trend(sch, **kwargs)
    # treatment: a max-balanced session -> heavy damping (factor 1-0.5*1=0.5).
    monkeypatch.setattr(ps, "classify_profile_shape",
                        lambda *a, **k: ps.ProfileShape("balance", 1.0, 1.0))
    damped = compute.compute_intraday_trend(sch, **kwargs)
    assert abs(damped["aggression"]) < abs(base["aggression"])
    assert any("profile balance" in e for e in damped["evidence"])
    assert any("profile trend" in e for e in base["evidence"])   # shape still logged


def test_order_flow_makes_aggression_more_positive():
    """Streamed positive SPY aggressor ratio lifts the aggression axis (NO sign
    flip — net buying is aligned) and logs an order-flow evidence line; a mirror
    negative ratio drags it down. Same schwab fixture, only order_flow differs."""
    sch = _FakeBullSchwab()
    base = compute.compute_intraday_trend(sch)  # no order_flow
    hi = compute.compute_intraday_trend(
        sch, order_flow={"SPY": {"aggressor_ratio": 0.8, "n": 50}})
    lo = compute.compute_intraday_trend(
        sch, order_flow={"SPY": {"aggressor_ratio": -0.8, "n": 50}})
    assert hi["aggression"] > base["aggression"]   # positive flow lifts aggression
    assert hi["aggression"] > lo["aggression"]     # monotonic in the ratio
    assert any("order-flow" in e for e in hi["evidence"])
    assert not any("order-flow" in e for e in base["evidence"])


def test_order_flow_missing_or_malformed_drops_out():
    """None / no-SPY / ratio-None order_flow all drop the component out; the
    classifier still runs and produces a valid state (graceful degradation)."""
    sch = _FakeBullSchwab()
    for of in (None, {}, {"QQQ": {"aggressor_ratio": 0.9, "n": 40}},
               {"SPY": {"aggressor_ratio": None, "n": 0}},
               {"SPY": "not-a-dict"}):
        out = compute.compute_intraday_trend(sch, order_flow=of)
        assert out["state"] in _TREND_STATES
        assert not any("order-flow" in e for e in out["evidence"])


def test_option_flow_pushes_aggression_negative():
    """A put-buying OPTION-flow signal (signal<0) drags the aggression axis MORE
    NEGATIVE; against a bullish direction it flips toward lack_of_bullishness, and
    logs an option-flow evidence line. Same schwab fixture, only order_flow differs."""
    sch = _FakeBullSchwab()
    base = compute.compute_intraday_trend(sch)  # no option flow
    bear = compute.compute_intraday_trend(
        sch, order_flow={"options": {"signal": -0.7, "n": 40}})
    assert bear["aggression"] < base["aggression"]       # put-buying → more negative
    assert bear["state"] == "lack_of_bullishness"        # bullish dir, negative aggression
    assert any("option-flow" in e for e in bear["evidence"])
    assert not any("option-flow" in e for e in base["evidence"])


def test_option_flow_call_buying_more_positive():
    """A call-buying OPTION-flow signal (signal>0) lifts the aggression axis (NO flip)."""
    sch = _FakeBullSchwab()
    base = compute.compute_intraday_trend(sch)
    bull = compute.compute_intraday_trend(
        sch, order_flow={"options": {"signal": 0.7, "n": 40}})
    assert bull["aggression"] > base["aggression"]


def test_option_flow_missing_drops_out():
    """Missing / malformed ``options`` drops the option-flow component out; the
    classifier still runs and produces a valid state."""
    sch = _FakeBullSchwab()
    for of in (None, {}, {"SPY": {"aggressor_ratio": 0.5, "n": 10}},
               {"options": {"signal": None, "n": 0}},
               {"options": "not-a-dict"}):
        out = compute.compute_intraday_trend(sch, order_flow=of)
        assert out["state"] in _TREND_STATES
        assert not any("option-flow" in e for e in out["evidence"])


def test_micro_signals_drop_out_on_no_data():
    """No frames (proxy down) -> session/rejection/profile all drop out; the
    classifier still returns a valid neutral state (graceful)."""
    out = compute.compute_intraday_trend(_FakeDeadSchwab())
    assert out["state"] == "neutral"
    assert not any("session" in e for e in out["evidence"])
    assert not any("rejection/defense" in e for e in out["evidence"])
    assert not any("profile" in e for e in out["evidence"])


# --- 30-day structural trend (Phase 3) ----------------------------------------

def test_compute_30d_trend_from_daily():
    spy = _bars(260, 400.0, 0.6)  # steadily rising daily closes
    months = {"XLK": 4.0, "XLF": 3.0, "XLY": 2.5, "XLP": -0.5, "XLU": -1.0}
    out = compute.compute_30d_trend(spy, months)
    for k in ("score", "state", "label", "description", "confidence",
              "sub_scores"):
        assert k in out
    assert out["score"] > 60.0
    assert out["state"] in {"bull_trend", "pullback_in_bull", "range"}
    assert set(out["sub_scores"]) == {"price", "sector"}


def test_compute_30d_trend_insufficient_data():
    out = compute.compute_30d_trend(None, {})
    assert out["score"] == 50.0
    assert out["state"] == "range"


def test_compute_30d_trend_self_fetch_cached_hourly(monkeypatch):
    """The self-fetching (no-args) path — the one the 15-min trend recompute
    calls — must NOT refetch ~12 histories every 15 min for a ~daily-changing
    structural gauge: the result is TTL-cached ~1 hour."""
    calls = {"spy": 0, "sectors": 0}

    def fake_daily(schwab, symbol, months):
        calls["spy"] += 1
        return _bars(260, 400.0, 0.6)

    def fake_sectors():
        calls["sectors"] += 1
        return {"XLK": 4.0, "XLF": 3.0, "XLP": -0.5}

    monkeypatch.setattr(compute, "_safe_daily", fake_daily)
    monkeypatch.setattr(compute, "_fetch_sector_month_pcts", fake_sectors)
    compute.reset_trend_30d_cache()

    first = compute.compute_30d_trend()
    assert calls == {"spy": 1, "sectors": 1}   # cold: fetched
    second = compute.compute_30d_trend()
    assert calls == {"spy": 1, "sectors": 1}   # within TTL: no refetch
    assert second == first                      # same cached result

    compute._TREND_30D_CACHE["ts"] -= compute.TREND_30D_TTL_SEC + 1
    compute.compute_30d_trend()
    assert calls == {"spy": 2, "sectors": 2}   # TTL expired: refetched


def test_compute_30d_trend_explicit_args_bypass_cache(monkeypatch):
    """Passing explicit data (the offline/test path) neither reads nor pollutes
    the self-fetch cache."""
    monkeypatch.setattr(
        compute, "_safe_daily",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    compute.reset_trend_30d_cache()
    spy = _bars(260, 400.0, 0.6)
    out = compute.compute_30d_trend(spy, {"XLK": 4.0})
    assert out["score"] > 50.0
    assert compute._TREND_30D_CACHE["result"] is None  # cache untouched


# --- 7-day structural trend (the Week arc) ------------------------------------

def test_compute_7d_trend_shape():
    spy = _bars(260, 400.0, 0.6)
    weeks = {"XLK": 2.0, "XLF": 1.5, "XLY": 1.2, "XLP": -0.3, "XLU": -0.5}
    out = compute.compute_7d_trend(spy, weeks)
    for k in ("score", "state", "label", "description", "confidence",
              "sub_scores"):
        assert k in out
    assert out["score"] > 60.0
    assert set(out["sub_scores"]) == {"price", "sector"}


def test_compute_7d_trend_neutral_without_data():
    out = compute.compute_7d_trend(None, {})
    assert out["score"] == 50.0
    assert out["state"] == "range"


def test_compute_7d_trend_tracks_sector_breadth():
    """Broad green sectors must score above broad red ones, SPY held fixed."""
    spy = _bars(260, 400.0, 0.6)
    green = {"XLK": 2.0, "XLF": 1.5, "XLY": 1.2, "XLI": 0.8, "XLP": 0.3}
    red = {"XLK": -2.0, "XLF": -1.5, "XLY": -1.2, "XLI": -0.8, "XLP": -0.3}
    up = compute.compute_7d_trend(spy, green)
    down = compute.compute_7d_trend(spy, red)
    assert up["sub_scores"]["sector"] > 50.0 > down["sub_scores"]["sector"]
    assert up["score"] > down["score"]


def test_compute_7d_trend_never_raises_on_junk():
    spy = _bars(260, 400.0, 0.6)
    for pcts in ({"XLK": "wat"}, {"XLK": None}, {None: 1.0}, "not-a-dict", []):
        out = compute.compute_7d_trend(spy, pcts)
        assert out["score"] == out["score"]          # not NaN
        assert 0.0 <= out["score"] <= 100.0
    for frame in (None, "not-a-frame", 42):
        out = compute.compute_7d_trend(frame, {"XLK": 1.0})
        assert 0.0 <= out["score"] <= 100.0


def test_compute_7d_trend_self_fetch_cached(monkeypatch):
    """The self-fetching path (what the 15-min recompute calls) is TTL-cached."""
    calls = {"spy": 0, "sectors": 0}

    def fake_daily(schwab, symbol, months):
        calls["spy"] += 1
        return _bars(260, 400.0, 0.6)

    def fake_weeks():
        calls["sectors"] += 1
        return {"XLK": 2.0, "XLF": 1.5, "XLP": -0.3}

    monkeypatch.setattr(compute, "_safe_daily", fake_daily)
    monkeypatch.setattr(compute, "_fetch_sector_week_pcts", fake_weeks)
    compute.reset_trend_7d_cache()

    first = compute.compute_7d_trend()
    assert calls == {"spy": 1, "sectors": 1}
    second = compute.compute_7d_trend()
    assert calls == {"spy": 1, "sectors": 1}          # within TTL: no refetch
    assert second == first

    compute._TREND_7D_CACHE["ts"] -= compute.TREND_7D_TTL_SEC + 1
    compute.compute_7d_trend()
    assert calls == {"spy": 2, "sectors": 2}          # TTL expired: refetched


def test_compute_7d_trend_explicit_args_bypass_cache(monkeypatch):
    monkeypatch.setattr(
        compute, "_safe_daily",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    compute.reset_trend_7d_cache()
    out = compute.compute_7d_trend(_bars(260, 400.0, 0.6), {"XLK": 2.0})
    assert out["score"] > 50.0
    assert compute._TREND_7D_CACHE["result"] is None


def test_week_and_month_gauges_read_their_own_horizon(monkeypatch):
    """The two structural gauges must read DIFFERENT columns of the shared sector
    fetch. Feed a tape that is green over the week and red over the month: the
    Week arc has to come out bullish and the Month arc bearish. Swapping the two
    horizons in either function flips both and fails here."""
    monkeypatch.setattr(compute, "_safe_daily",
                        lambda *a, **k: _bars(260, 400.0, 0.6))

    import sectors_ref
    monkeypatch.setattr(sectors_ref, "load_sectors_data",
                        lambda *a, **k: [{"kind": "sector", "etf": e}
                                         for e in ("XLK", "XLF", "XLY", "XLP")])
    monkeypatch.setattr(
        compute, "_fetch_closes",
        lambda etfs, months: ({}, {e: {"day3_pct": 0.0, "week_pct": 2.0,
                                       "month_pct": -2.0} for e in etfs}))
    compute.reset_sector_pcts_cache()
    compute.reset_trend_7d_cache()
    compute.reset_trend_30d_cache()

    week = compute.compute_7d_trend()
    month = compute.compute_30d_trend()
    assert week["sub_scores"]["sector"] > 50.0
    assert month["sub_scores"]["sector"] < 50.0


def test_week_horizon_uses_a_tighter_cyc_def_scale():
    """A week's cyclical-vs-defensive spread is smaller than a month's, so the
    same spread must read as STRONGER leadership at the weekly horizon."""
    spy = _bars(260, 400.0, 0.6)
    pcts = {"XLK": 1.0, "XLY": 1.0, "XLP": 0.0, "XLU": 0.0}
    week = compute.compute_7d_trend(spy, pcts)
    month = compute.compute_30d_trend(spy, pcts)
    assert week["sub_scores"]["sector"] > month["sub_scores"]["sector"]


def test_non_finite_sector_pct_is_treated_as_missing():
    """A NaN %-move must NOT read as a real value. ``intraday_trend._clamp`` is
    ``max(lo, min(hi, v))``, which returns the HIGH bound for NaN — so an
    unguarded NaN renders as MAXIMUM cyclical leadership at full confidence.
    Dropping it (lowering n_total, and so the sub-score's confidence) is what a
    missing sector should do. Applies to both structural horizons."""
    nan = float("nan")
    spy = _bars(260, 400.0, 0.6)
    with_nan = {"XLK": nan, "XLF": 1.5, "XLY": 1.2, "XLP": -0.3}
    without = {"XLF": 1.5, "XLY": 1.2, "XLP": -0.3}
    assert compute.compute_7d_trend(spy, with_nan) == \
        compute.compute_7d_trend(spy, without)
    assert compute.compute_30d_trend(spy, with_nan) == \
        compute.compute_30d_trend(spy, without)


# --- shared sector %-move fetch (one fan-out serves both horizons) ------------

def _stub_sector_fetch(monkeypatch, calls):
    """Stub the workbook + the per-ETF history fan-out (no proxy), recording each
    fan-out in ``calls`` so a test can prove how many actually happened."""
    import sectors_ref

    monkeypatch.setattr(sectors_ref, "load_sectors_data",
                        lambda *a, **k: [{"kind": "sector", "etf": "XLK"},
                                         {"kind": "sector", "etf": "XLP"},
                                         {"kind": "industry", "etf": "SMH"}])

    def fake_closes(etfs, months):
        etfs = list(etfs)
        calls.append(etfs)
        return {}, {e: {"day3_pct": 0.1, "week_pct": 1.0 + i,
                        "month_pct": 10.0 + i}
                    for i, e in enumerate(etfs)}

    monkeypatch.setattr(compute, "_fetch_closes", fake_closes)


def test_fetch_sector_pcts_both_horizons_from_one_fetch(monkeypatch):
    """``_fetch_closes`` already derives week AND month %-moves from the same
    frames, so the weekly gauge must cost no extra Schwab calls."""
    calls = []
    _stub_sector_fetch(monkeypatch, calls)
    compute.reset_sector_pcts_cache()

    out = compute._fetch_sector_pcts()

    assert len(calls) == 1                       # ONE fan-out, both horizons
    assert calls[0] == ["XLK", "XLP"]            # sector rows only
    assert out["week"] == {"XLK": 1.0, "XLP": 2.0}
    assert out["month"] == {"XLK": 10.0, "XLP": 11.0}


def test_fetch_sector_pcts_cached(monkeypatch):
    calls = []
    _stub_sector_fetch(monkeypatch, calls)
    compute.reset_sector_pcts_cache()

    compute._fetch_sector_pcts()
    compute._fetch_sector_pcts()
    assert len(calls) == 1                       # within TTL: no refetch

    compute._SECTOR_PCTS_CACHE["ts"] -= compute.SECTOR_PCTS_TTL_SEC + 1
    compute._fetch_sector_pcts()
    assert len(calls) == 2                       # TTL expired: refetched


def test_both_horizon_delegates_share_one_fetch(monkeypatch):
    """The whole point of the refactor: the week and month gauges read the same
    cached fan-out instead of pulling ~11 histories each."""
    calls = []
    _stub_sector_fetch(monkeypatch, calls)
    compute.reset_sector_pcts_cache()

    assert compute._fetch_sector_month_pcts() == {"XLK": 10.0, "XLP": 11.0}
    assert compute._fetch_sector_week_pcts() == {"XLK": 1.0, "XLP": 2.0}
    assert len(calls) == 1


def test_fetch_sector_pcts_degrades_on_failure(monkeypatch):
    import sectors_ref

    def _raise(*a, **k):
        raise RuntimeError("workbook gone")

    monkeypatch.setattr(sectors_ref, "load_sectors_data", _raise)
    compute.reset_sector_pcts_cache()

    assert compute._fetch_sector_pcts() == {"week": {}, "month": {}}
    assert compute._fetch_sector_month_pcts() == {}
    assert compute._fetch_sector_week_pcts() == {}


def test_fetch_sector_pcts_empty_result_not_cached(monkeypatch):
    """A proxy blip returning nothing must not poison the TTL window for an hour
    (same rule as live_composite's P/C cache and ``_fetch_spy_5m``)."""
    calls = []
    _stub_sector_fetch(monkeypatch, calls)

    def empty_closes(etfs, months):
        calls.append(list(etfs))
        return {}, {}

    monkeypatch.setattr(compute, "_fetch_closes", empty_closes)
    compute.reset_sector_pcts_cache()

    assert compute._fetch_sector_pcts() == {"week": {}, "month": {}}
    compute._fetch_sector_pcts()
    assert len(calls) == 2                       # retried, not served from cache


def test_fetch_sector_pcts_returns_copies(monkeypatch):
    """Callers get their own dicts — mutating a result can't corrupt the cache.

    Both paths are covered deliberately: the MISS path builds the dicts, but the
    HIT path is what a caller gets 59 minutes out of 60, and a mutation there
    would poison every remaining read in the TTL window."""
    calls = []
    _stub_sector_fetch(monkeypatch, calls)
    compute.reset_sector_pcts_cache()

    first = compute._fetch_sector_pcts()          # miss path
    first["week"]["XLK"] = 999.0
    first["month"].clear()

    second = compute._fetch_sector_pcts()         # hit path
    assert second["week"]["XLK"] == 1.0
    assert second["month"] == {"XLK": 10.0, "XLP": 11.0}
    second["week"]["XLK"] = -999.0
    second["month"].clear()

    third = compute._fetch_sector_pcts()
    assert third["week"]["XLK"] == 1.0
    assert third["month"] == {"XLK": 10.0, "XLP": 11.0}
    assert len(calls) == 1                        # all three served by one fetch


def test_sector_and_trend_ttls_are_pinned():
    """The cadences are the spec, not an accident: the sector fan-out is hourly
    (daily bars change ~daily) and the week gauge recomputes twice as often as
    the month because a weekly horizon moves faster."""
    assert compute.SECTOR_PCTS_TTL_SEC == 3600
    assert compute.TREND_7D_TTL_SEC == 1800
    assert compute.TREND_30D_TTL_SEC == 3600


def test_derive_composite_extras_passes_through_trend():
    trend = {"state": "bull_trend", "score": 88.0, "marker": "verbatim"}
    out = compute.derive_composite_extras(None, [], [], trend=trend)
    assert out["trend"] == trend


def test_sector_pc_delta_reads_store(monkeypatch):
    """compute.sector_pc_delta opens the store and returns the 5-day delta."""
    import datetime as dt
    from services.sentiment_svc import sector_pcr_history_db as db
    conn = db.connect(":memory:")
    base = dt.date(2026, 6, 16)
    for i in range(6):
        db.record(conn, (base + dt.timedelta(days=i)).isoformat(), 0.50 + i * 0.02)
    monkeypatch.setattr(db, "connect", lambda *a, **k: conn)
    assert abs(compute.sector_pc_delta() - 0.10) < 1e-9   # 0.60 - 0.50


def test_sector_pc_delta_none_on_empty_store(monkeypatch):
    from services.sentiment_svc import sector_pcr_history_db as db
    conn = db.connect(":memory:")
    monkeypatch.setattr(db, "connect", lambda *a, **k: conn)
    assert compute.sector_pc_delta() is None


def test_sector_pc_delta_closes_its_connection(monkeypatch):
    """sector_pc_delta opens a fresh connection every 15-min trend recompute — it
    must CLOSE it, or the service leaks ~26 SQLite handles/day."""
    from services.sentiment_svc import sector_pcr_history_db as db

    class _SpyConn:
        closed = False

        def close(self):
            _SpyConn.closed = True

    monkeypatch.setattr(db, "connect", lambda *a, **k: _SpyConn())
    monkeypatch.setattr(db, "sector_pc_delta", lambda conn: 0.1)
    assert compute.sector_pc_delta() == 0.1
    assert _SpyConn.closed is True


def test_sector_pc_delta_defensive_on_error(monkeypatch):
    """A store-open failure degrades to None, never raises."""
    from services.sentiment_svc import sector_pcr_history_db as db

    def _boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(db, "connect", _boom)
    assert compute.sector_pc_delta() is None


def test_compute_imports_clean():
    """compute imports without pulling in nicegui or the webgui UI tier."""
    import services.sentiment_svc.compute as c  # noqa: F401
    # The module must not expose a NiceGUI `ui` handle (the page's render does).
    assert not hasattr(compute, "ui")
    # And it must not import nicegui at module scope.
    assert "nicegui" not in compute.__dict__
