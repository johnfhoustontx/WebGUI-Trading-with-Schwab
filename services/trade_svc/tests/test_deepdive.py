"""Run from the repo root with the repo venv:
    .venv\\Scripts\\python -m pytest services\\trade_svc\\tests\\test_deepdive.py -v
(never `pytest services` over all services — cross-app module-name collisions.)"""

import pytest
import types

from services.trade_svc import compute


_FAKE_RESULT = {
    "symbol": "OKLO", "quote": {"lastPrice": 12.3},
    "technicals": {"last_close": 12.3, "rvol_20d": 55.0},
    "fundamentals": {"pe_ratio": None},
    "options": {"available": False}, "ranks": {}, "takeaways": ["t1"],
}


def test_run_deep_dive_returns_html(monkeypatch):
    # Stub the engine so no proxy/DB is touched: analyze_symbol -> a canned result,
    # render_html -> a sentinel HTML string.
    from services.trade_svc.deepdive import engine
    monkeypatch.setattr(engine, "SchwabClient", lambda *a, **k: object())
    monkeypatch.setattr(engine, "analyze_symbol", lambda *a, **k: dict(_FAKE_RESULT))
    monkeypatch.setattr(engine, "render_html", lambda *a, **k: "<html>DEEP DIVE</html>")
    monkeypatch.setattr(compute, "_open_iv_conn", lambda: None)  # skip real SQLite

    res = compute.run_deep_dive("oklo")
    assert res["symbol"] == "OKLO"
    assert "DEEP DIVE" in res["html"]
    assert res["ts"]


def test_run_deep_dive_bad_symbol_returns_error_html():
    res = compute.run_deep_dive("")
    assert res["symbol"] == "?"
    assert "html" in res and res["html"]  # a friendly error page, never None/raise


def test_build_deep_dive_query_injects_digest(monkeypatch):
    from services.trade_svc.deepdive import engine
    monkeypatch.setattr(engine, "SchwabClient", lambda *a, **k: object())
    monkeypatch.setattr(engine, "analyze_symbol", lambda *a, **k: dict(_FAKE_RESULT))
    monkeypatch.setattr(compute, "_open_iv_conn", lambda: None)

    res = compute.build_deep_dive_query("OKLO")
    md = res["markdown"]
    assert res["symbol"] == "OKLO"
    assert "OKLO" in md                  # {{SYMBOL}} substituted
    assert "{{QUANT_DATA}}" not in md    # placeholder filled
    assert "<!--" not in md              # HOW-TO comment stripped


class _FakeBus:
    def __init__(self):
        self.sets = {}
        self.published = []

    def cache_set(self, key, payload):
        self.sets[key] = payload
        return 1

    def publish(self, event, msg):
        self.published.append((event, msg))


def test_handle_command_deepdive(monkeypatch):
    from services.trade_svc import handlers
    monkeypatch.setattr(compute, "run_deep_dive",
                        lambda s: {"symbol": s.upper(), "html": "<h1>H</h1>", "ts": "t"})
    bus = _FakeBus()
    handlers.handle_command(bus, types.SimpleNamespace(type="deepdive", args={"symbol": "oklo"}))
    assert "OKLO" in bus.sets["cache:trade:deepdive"]["symbol"]
    assert bus.published and bus.published[0][0] == "events:trade:deepdive"


def test_handle_command_deepdive_query(monkeypatch):
    from services.trade_svc import handlers
    monkeypatch.setattr(compute, "build_deep_dive_query",
                        lambda s: {"symbol": s.upper(), "markdown": "PROMPT", "ts": "t"})
    bus = _FakeBus()
    handlers.handle_command(bus, types.SimpleNamespace(type="deepdive_query", args={"symbol": "oklo"}))
    assert bus.sets["cache:trade:deepdive_query"]["markdown"] == "PROMPT"
    assert bus.published and bus.published[0][0] == "events:trade:deepdive_query"


# ── 25-delta skew: ONE sign convention across the app (Phase 3, task 3.1) ────

class TestSkewSignConvention:
    """Two implementations carried OPPOSITE conventions: `flow_skew`
    (options-scanner, feeding the sentiment aggression axis) computes
    ``put_iv - call_iv``, while the deep dive computed ``call_iv - put_iv``.
    Put them on one page and they disagree for reasons that are convention,
    not market — the reader has no way to tell which.

    `flow_skew`'s is the one adopted: positive = downside fear is the standard
    equity-skew reading, and it is the one already wired into a live scoring
    path, so changing IT would move a scorer while changing this one moves only
    a display."""

    @staticmethod
    def _chain_df(call_iv, put_iv):
        import pandas as pd
        return pd.DataFrame([
            {"side": "CALL", "delta": 0.25, "iv": call_iv},
            {"side": "PUT", "delta": -0.25, "iv": put_iv},
        ])

    def test_downside_fear_is_POSITIVE(self):
        from services.trade_svc.deepdive import engine
        rr, c, p = engine.risk_reversal(self._chain_df(call_iv=20.0, put_iv=28.0))
        assert rr == pytest.approx(8.0)          # put - call
        assert (c, p) == (20.0, 28.0)

    def test_upside_being_bid_is_NEGATIVE(self):
        from services.trade_svc.deepdive import engine
        rr, _, _ = engine.risk_reversal(self._chain_df(call_iv=30.0, put_iv=22.0))
        assert rr == pytest.approx(-8.0)

    def test_it_matches_flow_skew_on_the_same_two_legs(self):
        """The cross-tier pin. flow_skew cannot be imported here (it lives in an
        options-scanner module that would drag the documented `scoring`
        collision in), so the convention is asserted against its stated
        contract: put_iv - call_iv."""
        from services.trade_svc.deepdive import engine
        call_iv, put_iv = 21.5, 26.0
        rr, _, _ = engine.risk_reversal(self._chain_df(call_iv, put_iv))
        assert rr == pytest.approx(put_iv - call_iv)


class TestSkewTakeawayReadsTheNewSignCorrectly:
    """The interpretation has to flip WITH the number. A sign change that
    leaves the prose alone is worse than the inconsistency it fixed: the words
    would confidently say the opposite of the truth."""

    @staticmethod
    def _opts(rr):
        return {"available": True, "front": {"risk_reversal_25d": rr,
                                             "expiration": "2026-09-19", "dte": 28}}

    def test_a_positive_reading_is_described_as_PUT_skew(self):
        from services.trade_svc.deepdive import engine
        notes = engine.build_takeaways({}, {}, self._opts(6.0))
        line = next(n for n in notes if "risk reversal" in n)
        assert "put skew" in line.lower()
        assert "downside" in line.lower()

    def test_a_negative_reading_is_described_as_CALL_skew(self):
        from services.trade_svc.deepdive import engine
        notes = engine.build_takeaways({}, {}, self._opts(-6.0))
        line = next(n for n in notes if "risk reversal" in n)
        assert "call skew" in line.lower()
        assert "upside" in line.lower()

    def test_a_flat_skew_says_nothing(self):
        from services.trade_svc.deepdive import engine
        notes = engine.build_takeaways({}, {}, self._opts(0.5))
        assert not any("risk reversal" in n for n in notes)


# ── gamma flip: ONE algorithm across the app (Phase 3, task 3.1) ────────────

class TestGammaFlipMatchesTheCollector:
    """Three flip algorithms coexisted under one name. The deep dive found
    where CUMULATIVE GEX crosses zero; `gamma_tool.snapshot_summary` — whose
    result is what the collector STORES and every dealer surface displays —
    finds where PER-STRIKE net GEX changes sign, restricted to ±3% of spot,
    requiring the sign to persist 2 live strikes either side, then interpolated
    and resolved to the candidate nearest spot.

    Those are different quantities, not different precisions. Showing both on
    one page makes them disagree for reasons that are arithmetic, not market.
    """

    # Per-strike net flips sign between 99 and 100. Cumulative GEX does not
    # reach zero until 105, because the deep negative wing has to be paid back
    # first — so the two algorithms differ by five strikes on the same grid.
    GRID = {
        95.0: -100.0, 96.0: -100.0, 97.0: -100.0, 98.0: -50.0, 99.0: -10.0,
        100.0: 10.0, 101.0: 50.0, 102.0: 100.0, 103.0: 100.0, 104.0: 100.0,
        105.0: 100.0,
    }

    def test_it_finds_the_PER_STRIKE_sign_change_not_the_cumulative_one(self):
        from services.trade_svc.deepdive import engine
        flip = engine.flip_point(self.GRID, spot=100.0)
        # Interpolated between 99 (-10) and 100 (+10) -> 99.5, NOT 105.
        assert flip == pytest.approx(99.5)

    def test_it_agrees_with_gamma_tool_on_the_same_grid(self):
        """The cross-tier pin. If either implementation drifts, this fails."""
        import sys
        from repo_paths import REPO_ROOT
        scanner = str(REPO_ROOT / "options-scanner")
        if scanner not in sys.path:
            sys.path.insert(0, scanner)
        import gamma_tool as gt
        from services.trade_svc.deepdive import engine

        gex = {"spot": 100.0,
               "gex": {k: {"net": v, "call": max(v, 0.0), "put": min(v, 0.0)}
                       for k, v in self.GRID.items()}}
        theirs = gt.GammaEngine().snapshot_summary(gex, "gex").get("flip")
        ours = engine.flip_point(self.GRID, spot=100.0)
        assert ours == pytest.approx(theirs)

    def test_a_crossing_outside_the_3pct_band_is_ignored(self):
        """An extreme-wing sign change is not a dealer level near spot."""
        from services.trade_svc.deepdive import engine
        far = {80.0: -100.0, 81.0: 100.0, 99.0: 50.0, 100.0: 50.0, 101.0: 50.0}
        assert engine.flip_point(far, spot=100.0) is None

    def test_a_zero_net_strike_is_absence_of_data_not_a_level(self):
        """Strict crossing: a strike nobody traded must not create a flip."""
        from services.trade_svc.deepdive import engine
        grid = {98.0: 50.0, 99.0: 0.0, 100.0: 50.0, 101.0: 50.0, 102.0: 50.0}
        assert engine.flip_point(grid, spot=100.0) is None

    def test_an_isolated_one_strike_blip_does_not_qualify(self):
        """Persistence: the sign must hold 2 live strikes either side, or a
        single noisy strike is promoted to a level."""
        from services.trade_svc.deepdive import engine
        grid = {97.0: 50.0, 98.0: 50.0, 99.0: -5.0, 100.0: 50.0, 101.0: 50.0,
                102.0: 50.0}
        assert engine.flip_point(grid, spot=100.0) is None

    def test_an_empty_or_unusable_grid_is_none(self):
        from services.trade_svc.deepdive import engine
        assert engine.flip_point({}, spot=100.0) is None
        assert engine.flip_point(self.GRID, spot=None) is None
        assert engine.flip_point(self.GRID, spot=0) is None
