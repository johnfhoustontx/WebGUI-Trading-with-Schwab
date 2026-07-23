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


def _daily_varied(n=60, base=400.0):
    """Rising daily closes with VARIED true ranges (cycling 1..5), today's range
    the smallest — so ``atr_pctile`` reads LOW and doesn't spuriously fire the
    crisis regime (a constant-range frame pins the percentile at 1.0)."""
    closes = [base + i * 0.6 for i in range(n)]
    ranges = [1.0 + (i % 5) for i in range(n)]
    ranges[-1] = 1.0  # today's range small -> low atr percentile
    return pd.DataFrame({
        "open": closes,
        "high": [c + r / 2.0 for c, r in zip(closes, ranges)],
        "low": [c - r / 2.0 for c, r in zip(closes, ranges)],
        "close": closes,
        "volume": [1_000_000] * n,
        "datetime": pd.date_range("2026-04-01", periods=n, freq="1D"),
    })


class _FakeBullSchwab:
    """Rising 5-min + daily bars, benign VIX. Every method returns data."""

    def get_intraday_history(self, symbol, minutes=5, days=1):
        return _bars(120, 500.0, 0.5)

    def get_daily_history(self, symbol, months=12):
        return _daily_varied()

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


class _FakeChopSchwab(_FakeBullSchwab):
    """Oscillating 5-min bars (whipsaws, no trend) -> LOW trending membership."""

    def get_intraday_history(self, symbol, minutes=5, days=1):
        n = 120
        closes = [500.0 + (2.0 if i % 2 == 0 else -2.0) for i in range(n)]
        return pd.DataFrame({
            "open": closes,
            "high": [c + 2.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "datetime": pd.date_range("2026-06-01", periods=n, freq="5min"),
        })


class _CountingSchwab(_FakeBullSchwab):
    """Bull data, but counts intraday fetches (for the TTL test)."""

    def __init__(self):
        self.intraday_calls = 0

    def get_intraday_history(self, symbol, minutes=5, days=1):
        self.intraday_calls += 1
        return super().get_intraday_history(symbol, minutes=minutes, days=days)


def _session_rows(date_str, n, base, amp):
    """One session of ``n`` alternating 5-min bars whose Bollinger width scales
    with ``amp`` (population std of the alternating closes ~= amp)."""
    closes = [base + (amp if i % 2 == 0 else -amp) for i in range(n)]
    return {
        "open": closes,
        "high": [c + abs(amp) for c in closes],
        "low": [c - abs(amp) for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
        "datetime": list(pd.date_range(f"{date_str} 13:30", periods=n, freq="5min")),
    }


def _multi_session_frame(amps, n=24, base=500.0):
    """A multi-session 5-min frame — one distinct calendar date per amp — with
    per-session band width set by ``amps`` (the last is today's session)."""
    dates = pd.bdate_range("2026-06-01", periods=len(amps))
    cols = {k: [] for k in ("open", "high", "low", "close", "volume", "datetime")}
    for d, amp in zip(dates, amps):
        s = _session_rows(d.strftime("%Y-%m-%d"), n, base, amp)
        for k in cols:
            cols[k].extend(s[k])
    return pd.DataFrame(cols)


def test_today_session_is_single_session():
    frame = _multi_session_frame([1.0, 1.5, 2.0, 2.5])  # 4 sessions x 24 bars
    session = compute._today_session(frame)
    assert len(session) == 24
    # exactly ONE local date -> the session-scoped helpers see today only.
    assert session["datetime"].dt.date.nunique() == 1
    assert session["datetime"].dt.date.iloc[0] == frame["datetime"].dt.date.max()


def test_bb_width_pctile_uses_5min_timescale_not_pinned():
    """The regression the daily-basis bug would fail: today's intraday width is
    ranked against trailing 5-min sessions, so a normal-wide today reads HIGH
    (not ~0), and a squeeze reads LOW — both genuinely inside (0, 1)."""
    # Progressively wider prior sessions; today's band is high-but-not-max.
    wide = _multi_session_frame([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 2.8])
    session_w = compute._today_session(wide)
    pw_w = compute._prior_widths(wide, exclude_last=len(session_w))
    ev_w = compute.regime_evidence.evidence_from_bars(
        session_w, None, None, None, pw_w)
    pct_w = ev_w["bb_width_pctile"]

    # Squeeze: same wide history, today narrow.
    sq = _multi_session_frame([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 0.3])
    session_s = compute._today_session(sq)
    pw_s = compute._prior_widths(sq, exclude_last=len(session_s))
    ev_s = compute.regime_evidence.evidence_from_bars(
        session_s, None, None, None, pw_s)
    pct_s = ev_s["bb_width_pctile"]

    assert pw_w is not None and pct_w is not None
    # a normal-wide today reads HIGH (the buggy daily basis pinned it at ~0),
    # and genuinely inside (0, 1) — not pinned at either rail.
    assert 0.5 < pct_w < 1.0
    # the squeeze ranks materially lower than the wide session.
    assert pct_s < pct_w
    assert pct_s < 0.3


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
    # Two DISTINCT samples: a choppy (low-trending) first sample, then a strongly
    # trending one. The smoothed trending membership must MOVE toward the new
    # sample, not merely hold.
    r1 = compute.compute_market_regime(_FakeChopSchwab(), prior=None, now=NOW)
    compute.reset_spy_5m_cache()   # the second call must fetch the NEW (bull) fake
    r2 = compute.compute_market_regime(_FakeBullSchwab(), prior=r1, now=NOW + 300)
    # trending membership advanced toward the trending sample (strict move).
    assert r2["memberships"]["trending"] > r1["memberships"]["trending"]
    # ... but the EMA still lags a pure trending read (didn't snap to it).
    compute.reset_spy_5m_cache()
    pure = compute.compute_market_regime(_FakeBullSchwab(), prior=None, now=NOW)
    assert r2["memberships"]["trending"] < pure["memberships"]["trending"]
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
