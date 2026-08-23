"""Tests for compute.compute_market_regime — the service function that fetches
market data, runs the pure Task 1-4 regime modules, threads the temporal
smoothing/commit state off ``prior``, and returns a contract-shaped dict.

Defensive throughout: any failure returns a fully-shaped ``unclear`` dict.
Reuses the ``_bars`` / fake-schwab idiom from ``test_compute.py``.
"""
from datetime import date as _date, datetime, timedelta as _timedelta, timezone

import pandas as pd
import pytest

from shared.contracts.sentiment import RegimeState
from services.sentiment_svc import compute

NOW = 1_800_000_000  # fixed unix seconds so the TTL keys off the passed ``now``

# The subset of the return dict that is the publishable RegimeState (the
# ``_fast``/``_slow``/``_commit``/``_sample_ts`` carry fields are NOT cached).
_PUBLISH = ("ts", "as_of", "memberships", "raw", "confidence", "unclear",
            "label", "committed_label", "transition", "evidence",
            "evidence_detail", "version_info", "direction", "direction_strong")


@pytest.fixture(autouse=True)
def _clear_cache():
    """The SPY 5-min fetch and the $VIX1D prior close are memoized module-side;
    reset both between tests so a cached bar-set / prior close can't leak."""
    compute.reset_spy_5m_cache()
    compute.reset_vix1d_prev_cache()
    yield
    compute.reset_spy_5m_cache()
    compute.reset_vix1d_prev_cache()


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
    assert out["label"] == "Stressed"   # internal key "crisis" -> displayed "Stressed"
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


# --- VIX1D day-over-day spike (the most direct crisis tell) -------------------
# ``_fetch_vix`` threads the PRIOR session's $VIX1D close as ``vix1d_prev`` so
# ``regime_evidence._vix1d_spike_pct`` can fire. The daily history is memoized
# per local date (the prior close only changes once a session) so the 120 s
# crisis check never re-fetches it.


def _vix1d_daily(closes, include_today=False):
    """A $VIX1D daily frame ending YESTERDAY (or today when ``include_today``),
    so the "prior session" row is unambiguous regardless of the run date."""
    end = _date.today() - _timedelta(days=0 if include_today else 1)
    return pd.DataFrame({
        "open": list(closes),
        "high": list(closes),
        "low": list(closes),
        "close": list(closes),
        "volume": [0] * len(closes),
        "datetime": pd.date_range(end=pd.Timestamp(end), periods=len(closes),
                                  freq="1D"),
    })


class _FakeVix1dSchwab(_FakeBullSchwab):
    """Bull bars, but $VIX1D quotes ``vix1d_now`` against a prior close of
    ``vix1d_prev``. The term structure is deliberately in contango (no
    inversion), so the ONLY crisis tell available is the day-over-day spike."""

    def __init__(self, vix1d_prev=12.0, vix1d_now=18.0, daily_fails=False):
        self.vix1d_prev = vix1d_prev
        self.vix1d_now = vix1d_now
        self.daily_fails = daily_fails
        self.vix1d_daily_calls = 0

    def get_daily_history(self, symbol, months=12):
        if symbol == "$VIX1D":
            self.vix1d_daily_calls += 1
            if self.daily_fails:
                raise RuntimeError("no vix1d history")
            return _vix1d_daily([self.vix1d_prev] * 5)
        return super().get_daily_history(symbol, months=months)

    def get_quotes(self, symbols):
        out = {}
        for s in symbols:
            if s == "$VIX1D":
                out[s] = {"last": self.vix1d_now}
            elif s == "$VIX3M":
                out[s] = {"last": 22.0}   # contango: vix3m > vix -> no inversion
            else:
                out[s] = {"last": 20.0}
        return out


def test_prior_daily_close_skips_todays_forming_bar():
    """During RTH the last daily row is today's UNFINISHED bar â€” the prior
    SESSION's close is the one before it."""
    today = _date.today().isoformat()
    frame = _vix1d_daily([10.0, 11.0, 12.0, 99.0], include_today=True)
    assert compute._prior_daily_close(frame, today) == 12.0
    # Off-hours the frame may end yesterday â€” then its last row IS the prior close.
    frame2 = _vix1d_daily([10.0, 11.0, 12.0])
    assert compute._prior_daily_close(frame2, today) == 12.0


def test_fetch_vix_threads_vix1d_prev():
    schwab = _FakeVix1dSchwab(vix1d_prev=12.0, vix1d_now=18.0)
    vix = compute._fetch_vix(schwab)
    assert vix["vix1d_prev"] == 12.0
    # ... and the pure evidence builder turns it into the spike percentage.
    ev = compute.regime_evidence.evidence_from_bars(None, None, vix, None, None)
    assert ev["vix1d_spike_pct"] == pytest.approx(50.0)


def test_vix1d_spike_fires_crisis():
    """12.0 -> 18.0 is a +50% VIX1D spike (>= the 35% full-ramp) -> raw crisis
    1.0, which clears CRISIS_ATTACK and force-commits crisis."""
    out = compute.compute_market_regime(
        _FakeVix1dSchwab(vix1d_prev=12.0, vix1d_now=18.0), now=NOW)
    assert out["raw"]["crisis"] >= 0.7
    assert out["committed_label"] == "crisis"
    assert out["label"] == "Stressed"   # internal key "crisis" -> displayed "Stressed"
    assert any("VIX1D" in e for e in out["evidence"])


def test_vix1d_flat_no_crisis():
    out = compute.compute_market_regime(
        _FakeVix1dSchwab(vix1d_prev=18.0, vix1d_now=18.0), now=NOW)
    assert out["raw"]["crisis"] < 0.7
    assert out["committed_label"] != "crisis"


def test_vix1d_prev_absent_when_daily_fetch_fails():
    """A failing daily history degrades to NO key â€” the spike tell reads None
    (exactly as before this was threaded), never a fabricated value, never a raise."""
    schwab = _FakeVix1dSchwab(daily_fails=True)
    vix = compute._fetch_vix(schwab)
    assert vix is not None
    assert "vix1d_prev" not in vix
    ev = compute.regime_evidence.evidence_from_bars(None, None, vix, None, None)
    assert ev["vix1d_spike_pct"] is None
    out = compute.compute_market_regime(schwab, now=NOW)  # must not raise
    assert out["raw"]["crisis"] < 0.7


def test_vix1d_prev_memoized_per_date():
    """The prior close changes once a session â€” the 120 s crisis check must NOT
    re-fetch a daily history every cycle."""
    schwab = _FakeVix1dSchwab()
    for _ in range(3):
        compute._fetch_vix(schwab)
    assert schwab.vix1d_daily_calls == 1
    # a new session day re-fetches.
    compute.reset_vix1d_prev_cache()
    compute._fetch_vix(schwab)
    assert schwab.vix1d_daily_calls == 2


def test_vix1d_daily_probe_is_once_per_date_even_when_it_fails(monkeypatch):
    """Schwab serves NO $VIX1D history, so the daily source fails EVERY time.
    It must be probed at most ONCE per local date, not retried on a timer â€”
    a retry cadence over a known-dead endpoint burns ~100+ failing proxy calls
    a day for a source the session latch already covers. The clock is advanced
    past any plausible retry window so this pins the behavior, not the runtime."""
    schwab = _FakeVix1dSchwab(daily_fails=True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(compute.time, "monotonic", lambda: clock["t"])
    for _ in range(25):                      # a full session of crisis checks
        compute._fetch_vix(schwab)
        clock["t"] += 1800.0                 # +30 min between reads
    assert schwab.vix1d_daily_calls == 1      # one probe, then it stops asking


# Schwab serves NO $VIX1D price history (live-verified 2026-07-23: /pricehistory
# returns {"empty": true} for it while $VIX/$VIX3M return candles), so the daily
# source above is usually unavailable and the in-process session latch is what
# actually keeps the spike tell alive.


def test_vix1d_prev_falls_back_to_prior_session_observation(monkeypatch):
    schwab = _FakeVix1dSchwab(vix1d_now=12.0, daily_fails=True)
    day = {"iso": "2026-07-22"}
    monkeypatch.setattr(compute, "_local_date_iso", lambda: day["iso"])

    v1 = compute._fetch_vix(schwab)
    assert "vix1d_prev" not in v1        # nothing observed before today yet

    day["iso"] = "2026-07-23"            # session rollover
    schwab.vix1d_now = 18.0
    v2 = compute._fetch_vix(schwab)

    assert v2["vix1d_prev"] == 12.0      # yesterday's LAST observed VIX1D
    ev = compute.regime_evidence.evidence_from_bars(None, None, v2, None, None)
    assert ev["vix1d_spike_pct"] == pytest.approx(50.0)


@pytest.mark.xfail(
    strict=True,
    reason="OPEN BUG (first seen 2026-08-08): the $VIX1D session latch beats the "
           "daily close, so vix1d_prev reads 18.0 where the real prior close is "
           "10.0 - inflating vix1d_spike_pct. Fix the precedence in _fetch_vix, "
           "then DELETE this marker (strict=True fails the run if it starts "
           "passing while still marked).",
)
def test_daily_history_wins_over_session_latch(monkeypatch):
    """When Schwab DOES serve the history, the real prior close is authoritative."""
    schwab = _FakeVix1dSchwab(vix1d_prev=10.0, vix1d_now=18.0)
    day = {"iso": "2026-07-22"}
    monkeypatch.setattr(compute, "_local_date_iso", lambda: day["iso"])
    compute._fetch_vix(schwab)           # latches 18.0 for 2026-07-22
    day["iso"] = "2026-07-23"
    out = compute._fetch_vix(schwab)
    assert out["vix1d_prev"] == 10.0     # the daily close, not the 18.0 latch


def test_session_latch_expires_after_a_multi_day_gap(monkeypatch):
    """A stale observation must NOT be served as 'yesterday's close'. If the
    service was down for days, the last thing it saw is not a prior session â€”
    treating it as one would manufacture a bogus day-over-day spike."""
    schwab = _FakeVix1dSchwab(vix1d_now=12.0, daily_fails=True)
    day = {"iso": "2026-07-13"}
    monkeypatch.setattr(compute, "_local_date_iso", lambda: day["iso"])
    compute._fetch_vix(schwab)               # latch 12.0 on 2026-07-13

    day["iso"] = "2026-07-14"                # next day: the latch is valid
    schwab.vix1d_now = 18.0
    assert compute._fetch_vix(schwab)["vix1d_prev"] == 12.0

    compute.reset_vix1d_prev_cache()
    day["iso"] = "2026-07-13"
    schwab.vix1d_now = 12.0
    compute._fetch_vix(schwab)               # latch 12.0 again on 2026-07-13
    day["iso"] = "2026-07-23"                # ...then a 10-day service gap
    schwab.vix1d_now = 18.0
    assert "vix1d_prev" not in compute._fetch_vix(schwab)


def test_session_latch_spans_a_long_weekend(monkeypatch):
    """The bound must tolerate a normal Friday->Tuesday gap (weekend + holiday)."""
    schwab = _FakeVix1dSchwab(vix1d_now=12.0, daily_fails=True)
    day = {"iso": "2026-07-17"}              # Friday
    monkeypatch.setattr(compute, "_local_date_iso", lambda: day["iso"])
    compute._fetch_vix(schwab)
    day["iso"] = "2026-07-21"                # Tuesday (4 days later)
    schwab.vix1d_now = 18.0
    assert compute._fetch_vix(schwab)["vix1d_prev"] == 12.0


def test_session_latch_ignores_same_day_observations(monkeypatch):
    """Repeated intraday reads must never become their own 'prior close' â€” that
    would turn ordinary intraday drift into a fabricated day-over-day spike."""
    schwab = _FakeVix1dSchwab(vix1d_now=12.0, daily_fails=True)
    monkeypatch.setattr(compute, "_local_date_iso", lambda: "2026-07-23")
    compute._fetch_vix(schwab)
    schwab.vix1d_now = 30.0
    out = compute._fetch_vix(schwab)
    assert "vix1d_prev" not in out


def test_spy_5m_lookback_is_an_allowed_schwab_period():
    """Schwab rejects periodType=day with any period outside [1,2,3,4,5,10] (400),
    and _safe_intraday swallows it -> bars None -> the classifier is permanently
    'Unclear'. A fake client can't see that, so pin the constant itself."""
    assert compute._SPY_5M_SESSIONS in compute._SPY_PERIOD_DAYS_ALLOWED


def test_fetch_spy_5m_requests_an_allowed_period():
    """...and pin the value actually passed to the client, not just the constant."""
    seen = {}

    class _Rec:
        def get_intraday_history(self, symbol, minutes=5, days=1):
            seen["days"] = days
            return None

        def get_daily_history(self, symbol, months=1):
            return None

    compute.reset_spy_5m_cache()
    compute._fetch_spy_5m(_Rec(), 1_000.0)
    assert seen["days"] in compute._SPY_PERIOD_DAYS_ALLOWED


# ------------------------------------------------- direction (display adornment)


def _commit_direction(schwab, trend_score, reads=2, now=NOW):
    """Run the regime enough times to clear the direction hysteresis, threading
    the carry the way the handler does."""
    prior = None
    for i in range(reads):
        compute.reset_spy_5m_cache()
        prior = compute.compute_market_regime(
            schwab, prior=prior, trend_score=trend_score, now=now + i * 300)
    return prior


def test_public_key_list_matches_the_handler_allowlist():
    # The handler filters the cache payload through its OWN tuple; this test's
    # _PUBLISH duplicates it, so pin them together or a new field silently never
    # reaches the cache.
    from services.sentiment_svc import handlers
    assert set(_PUBLISH) == set(handlers._REGIME_PUBLIC_KEYS)


def test_agreeing_reads_commit_a_direction_and_reword_the_label():
    out = _commit_direction(_FakeBullSchwab(), trend_score=65.0)
    assert out["direction"] == 1
    assert out["label"] in ("Rallying", "Firming")
    RegimeState(**{k: out[k] for k in _PUBLISH})


def test_disagreeing_reads_leave_the_label_neutral():
    # SPY structure is rising but the composite direction says down -> no word.
    out = _commit_direction(_FakeBullSchwab(), trend_score=35.0)
    assert out["direction"] == 0
    assert out["label"] == compute.market_regime.REGIME_DISPLAY[out["committed_label"]]


def test_absent_trend_score_is_neutral_not_a_guess():
    out = _commit_direction(_FakeBullSchwab(), trend_score=None)
    assert out["direction"] == 0
    assert out["direction_strong"] is False


def test_direction_needs_two_reads():
    compute.reset_spy_5m_cache()
    first = compute.compute_market_regime(
        _FakeBullSchwab(), prior=None, trend_score=65.0, now=NOW)
    assert first["direction"] == 0     # claimed only on the second agreeing read


def test_unclear_shell_carries_a_neutral_direction():
    good = _commit_direction(_FakeBullSchwab(), trend_score=65.0)
    compute.reset_spy_5m_cache()
    shell = compute.compute_market_regime(
        _FakeDeadSchwab(), prior=good, trend_score=65.0, now=NOW + 900)
    assert shell["direction"] == 0
    assert shell["label"] == "Unclear"
    RegimeState(**{k: shell[k] for k in _PUBLISH})


def test_direction_carry_survives_the_unclear_shell():
    # A transient miss must not reset the direction streak, same rule the
    # smoothing carry already follows.
    good = _commit_direction(_FakeBullSchwab(), trend_score=65.0)
    compute.reset_spy_5m_cache()
    shell = compute.compute_market_regime(
        _FakeDeadSchwab(), prior=good, trend_score=65.0, now=NOW + 900)
    assert shell["_dir"] == good["_dir"]
