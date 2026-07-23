"""Tests for compute.compute_market_regime — the service function that fetches
market data, runs the pure Task 1-4 regime modules, threads the temporal
smoothing/commit state off ``prior``, and returns a contract-shaped dict.

Defensive throughout: any failure returns a fully-shaped ``unclear`` dict.
Reuses the ``_bars`` / fake-schwab idiom from ``test_compute.py``.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from shared.contracts.sentiment import RegimeState
from services.sentiment_svc import compute

NOW = 1_800_000_000  # fixed unix seconds so the TTL keys off the passed ``now``

# The subset of the return dict that is the publishable RegimeState (the
# ``_fast``/``_slow``/``_commit``/``_sample_ts`` carry fields are NOT cached).
_PUBLISH = ("ts", "as_of", "memberships", "raw", "confidence", "unclear",
            "label", "committed_label", "transition", "evidence", "version_info")


@pytest.fixture(autouse=True)
def _clear_cache():
    """The SPY 5-min fetch is memoized module-side; reset it between tests so a
    cached bar-set can't leak across cases."""
    compute.reset_spy_5m_cache()
    yield
    compute.reset_spy_5m_cache()


def _bars(n, start, step, vol=1_000_000):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "open": closes,
        "high": [c + abs(step) for c in closes],
        "low": [c - abs(step) for c in closes],
        "close": closes,
        "volume": [vol] * n,
        "datetime": pd.date_range("2026-06-01", periods=n, freq="5min"),
    })


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class _FakeBullSchwab:
    """Rising 5-min + daily bars, benign VIX. Every method returns data."""

    def get_intraday_history(self, symbol, minutes=5, days=1):
        return _bars(120, 500.0, 0.5)

    def get_daily_history(self, symbol, months=12):
        return _bars(60, 400.0, 0.6)

    def get_quote(self, symbol):
        return {"last": 14.0}

    def get_quotes(self, symbols):
        out = {}
        for s in symbols:
            if s == "$VIX1D":
                out[s] = {"last": 13.0}
            elif s == "$VIX3M":
                out[s] = {"last": 16.0}
            else:  # $VIX and anything else
                out[s] = {"last": 14.0}
        return out


class _FakeDeadSchwab:
    """Every fetch raises or returns None (proxy down / no data)."""

    def get_intraday_history(self, *a, **k):
        raise RuntimeError("dead")

    def get_daily_history(self, *a, **k):
        return None

    def get_quote(self, *a, **k):
        raise RuntimeError("dead")

    def get_quotes(self, *a, **k):
        return None


class _CountingSchwab(_FakeBullSchwab):
    """Bull data, but counts intraday fetches (for the TTL test)."""

    def __init__(self):
        self.intraday_calls = 0

    def get_intraday_history(self, symbol, minutes=5, days=1):
        self.intraday_calls += 1
        return super().get_intraday_history(symbol, minutes=minutes, days=days)


def test_returns_valid_contract_shape():
    out = compute.compute_market_regime(_FakeBullSchwab(), now=NOW)
    # The publishable subset validates against the additive contract.
    RegimeState(**{k: out[k] for k in _PUBLISH})
    assert set(out["memberships"]) == set(compute.market_regime.REGIMES)
    assert abs(sum(out["memberships"].values()) - 1.0) < 1e-6
    assert set(out["raw"]) == set(compute.market_regime.REGIMES)
    # carry fields present (held by the handler, not cached).
    assert set(out) >= {"_fast", "_slow", "_commit", "_sample_ts"}
    assert out["_sample_ts"] == int(NOW)
    assert out["version_info"] == {"model": "regime-v1"}


def test_never_raises_on_dead_schwab():
    out = compute.compute_market_regime(_FakeDeadSchwab(), now=NOW)
    assert out["unclear"] is True
    RegimeState(**{k: out[k] for k in _PUBLISH})
    # a fully-shaped unclear shell.
    assert out["label"] == "Unclear"
    assert out["transition"] is None
    assert abs(sum(out["memberships"].values()) - 1.0) < 1e-6


def test_prior_threads_smoothing():
    schwab = _FakeBullSchwab()
    r1 = compute.compute_market_regime(schwab, prior=None, now=NOW)
    r2 = compute.compute_market_regime(schwab, prior=r1, now=NOW + 300)
    # smoothing accumulates toward the (identical) sample -> membership holds/grows.
    assert r2["memberships"]["trending"] >= r1["memberships"]["trending"] - 1e-9
    # the carry sample-ts advanced.
    assert r2["_sample_ts"] == int(NOW + 300)


def test_matrix_row_extracted_and_stale_gated():
    schwab = _FakeBullSchwab()
    row = {"symbol": "SPY", "spot": 500.0, "flip": 495.0, "gex_regime": "above"}
    fresh = {"ts": _iso(NOW), "rows": [row]}
    stale = {"ts": _iso(NOW - 600), "rows": [row]}  # 10 min old

    r_none = compute.compute_market_regime(schwab, matrix=None, now=NOW)
    compute.reset_spy_5m_cache()
    r_fresh = compute.compute_market_regime(schwab, matrix=fresh, now=NOW)
    compute.reset_spy_5m_cache()
    r_stale = compute.compute_market_regime(schwab, matrix=stale, now=NOW)

    # a fresh "above" row feeds above_flip -> the mean_reversion evidence string.
    assert "Above gamma flip" in r_fresh["evidence"]
    # matrix=None and a 10-min-stale matrix are equivalent (row gated out).
    assert "Above gamma flip" not in r_none["evidence"]
    assert "Above gamma flip" not in r_stale["evidence"]
    assert r_stale["raw"] == r_none["raw"]


def test_crisis_attack_forces_crisis_commit():
    # An inverted term structure (vix1d>vix, vix>vix3m) drives raw crisis to 1.0,
    # which is >= CRISIS_ATTACK (0.7) -> fast-attack + forced crisis commit.
    vix = {"vix": 20.0, "vix1d": 40.0, "vix3m": 15.0}
    out = compute.compute_market_regime(_FakeBullSchwab(), vix=vix, now=NOW)
    assert out["committed_label"] == "crisis"
    assert out["label"] == "Crisis"
    assert out["memberships"]["crisis"] >= 0.9
    assert out["raw"]["crisis"] >= 0.7


def test_fetch_ttl_shares_within_window():
    schwab = _CountingSchwab()
    compute.compute_market_regime(schwab, now=NOW)          # fetch #1
    assert schwab.intraday_calls == 1
    compute.compute_market_regime(schwab, now=NOW + 100)    # within 240s -> cached
    assert schwab.intraday_calls == 1
    compute.compute_market_regime(schwab, now=NOW + 300)    # > 240s -> refetch
    assert schwab.intraday_calls == 2


def test_unclear_shell_preserves_prior_carry():
    good = compute.compute_market_regime(_FakeBullSchwab(), now=NOW)
    assert good["_slow"] is not None
    compute.reset_spy_5m_cache()  # so the dead call actually re-fetches (and fails)
    shell = compute.compute_market_regime(_FakeDeadSchwab(), prior=good, now=NOW + 300)
    # a transient fetch failure must NOT reset the smoothing state.
    assert shell["_slow"] == good["_slow"]
    assert shell["_fast"] == good["_fast"]
    assert shell["unclear"] is True
