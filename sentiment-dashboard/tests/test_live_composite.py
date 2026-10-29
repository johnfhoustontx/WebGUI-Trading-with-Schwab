"""Tests for live_composite pure helpers."""
import json
import live_composite as L


def _snap(total, **comp):
    base = {"vix_complex": 4, "put_call": 8, "breadth": 7, "rotation": 5, "sector_perf": 8}
    base.update(comp)
    return {
        "date": "2026-06-14",
        "composite": {"total_score": f"{total:.2f}"},
        "component_scores": base,
        "component_confidence": {k: 1.0 for k in base},
        "volatility": {"interpretation": "term flat"},
        "options": {"pc_equity": "0.86"},
        "breadth": {"interpretation": "Advancing"},
        "rotation": {"interpretation": "Cyc leads"},
    }


def test_signal_band():
    assert L.signal_band(9.5) == ("1.25x", "Long", "Strong Bull")
    assert L.signal_band(7.0) == ("1.10x", "Long", "Bullish")
    assert L.signal_band(5.0) == ("1.00x", "Neutral", "Neutral")
    assert L.signal_band(3.0) == ("0.85x", "Cautious", "Bearish")
    assert L.signal_band(1.0) == ("0.70x", "Short", "Strong Bear")


def test_build_bridge_payload_core():
    p = L.build_bridge_payload(_snap(6.81), history_scores=[6.0, 6.5, 6.8],
                               spy_closes=[], generated_at="2026-06-14T20:00:00+00:00")
    assert p["composite_score"] == 6.81
    assert p["regime"] == "bullish"
    assert p["bias"] == "neutral"
    assert p["position_size_modifier"] == "1.00x"
    assert p["generated_at"] == "2026-06-14T20:00:00+00:00"
    assert p["date"] == "2026-06-14"
    assert set(["vix_complex", "put_call", "breadth", "rotation", "sector_perf"]) \
        <= set(p["component_scores"])
    assert "aggregate_confidence" in p and "velocity" in p and "weights" in p


def test_cap_weighted_pcr_known():
    import pytest
    # (0.9*2 + 0.6*1)/(2+1) = 2.4/3 = 0.8
    pcr = {"XLK": 0.9, "XLF": 0.6}
    weights = {"XLK": 2.0, "XLF": 1.0}
    assert L.cap_weighted_pcr(pcr, weights) == pytest.approx(0.8)


def test_cap_weighted_pcr_empty_is_none():
    assert L.cap_weighted_pcr({}, {}) is None
    assert L.cap_weighted_pcr({"XLK": 0.9}, {}) is None       # no usable weight
    assert L.cap_weighted_pcr({}, {"XLK": 2.0}) is None       # no pcr


def test_cap_weighted_pcr_partial_weights():
    # only sectors with a non-zero weight AND a pcr contribute
    pcr = {"XLK": 1.0, "XLF": 0.5, "XLE": 0.3}
    weights = {"XLK": 3.0, "XLF": 0.0, "XLE": 1.0}   # XLF weight 0 -> excluded
    # (1.0*3 + 0.3*1)/(3+1) = 3.3/4 = 0.825
    import pytest
    assert L.cap_weighted_pcr(pcr, weights) == pytest.approx(0.825)


def test_cap_weighted_pcr_single_sector():
    assert L.cap_weighted_pcr({"XLK": 0.7}, {"XLK": 2.0}) == 0.7


def test_build_bridge_payload_regime_bands():
    band = lambda t: L.build_bridge_payload(_snap(t), [], [], "x")["regime"]
    assert band(8.5) == "strong_bullish"
    assert band(7.0) == "bullish"
    assert band(5.5) == "neutral"
    assert band(4.0) == "bearish"
    assert band(2.0) == "strong_bearish"


def test_build_bridge_payload_roundtrips_through_bridge(tmp_path):
    import bridge
    p = L.build_bridge_payload(_snap(6.0), [6.0], [], "2026-06-14T20:00:00+00:00")
    out = tmp_path / "b.json"
    bridge.write_bridge(p, path=out)
    reread = json.loads(out.read_text())
    assert reread["composite_score"] == 6.0
    assert reread["schema_version"]


def test_bridge_trend_regime_additive_fields():
    """The trend block carries the new additive trend_score/sub_scores while
    keeping the daily structural sma_* fields (additive-only contract)."""
    trend = {
        "state": "bull_trend", "label": "Bull Trend", "description": "x",
        "raw_state": "bull_trend", "spy_close": 500.0, "sma_50": 480.0,
        "sma_200": 450.0, "sma_200_slope_pct": 0.3, "drawdown_pct": -1.0,
        "confidence": 0.9, "trend_score": 84.0, "sub_scores": {"price": 88},
    }
    p = L.build_bridge_payload(_snap(6.81), [6.0], [], "x", trend=trend)
    tr = p["trend_regime"]
    assert tr["state"] == "bull_trend"
    assert tr["trend_score"] == 84.0
    assert tr["sub_scores"] == {"price": 88}
    # back-compat structural field still present
    assert tr["sma_50"] == 480.0
    assert tr["confidence"] == 0.9


def test_bridge_trend_regime_without_new_fields_is_valid():
    """A daily-only trend dict (no trend_score/sub_scores) still yields a valid
    trend_regime block — the new keys degrade to None, no KeyError."""
    trend = {
        "state": "range", "label": "Range", "description": "x",
        "raw_state": "range", "spy_close": 500.0, "sma_50": 480.0,
        "sma_200": 450.0, "sma_200_slope_pct": 0.1, "drawdown_pct": -2.0,
        "confidence": 0.5,
    }
    p = L.build_bridge_payload(_snap(5.0), [5.0], [], "x", trend=trend)
    tr = p["trend_regime"]
    assert tr["state"] == "range"
    assert tr["trend_score"] is None
    assert tr["sub_scores"] is None
    assert tr["sma_50"] == 480.0


class _FakeDF:
    def __init__(self, closes): self._c = closes
    def __getitem__(self, k): return self
    def tolist(self): return self._c

class _FakeClient:
    def get_quote(self, s): return {"last": 18.0} if s == "$VIX" else {"last": 1.5}
    def get_quotes(self, syms): return {s: {"last": 17.0, "change_pct": 0.5} for s in syms}
    def get_daily_history(self, s, months=12):
        return _FakeDF([100.0 + i for i in range(80)])
    def _request(self, ep, params=None): return None

def test_compute_live_smoke():
    import sectors_ref
    sd = sectors_ref.load_sectors_data()
    L.reset_pcr_cache()
    snap = L.compute_live(_FakeClient(), sd)
    assert 0 <= float(snap["composite"]["total_score"]) <= 10
    assert set(["vix_complex","put_call","breadth","rotation","sector_perf"]) \
        <= set(snap["component_scores"])


_CHAIN = {  # minimal Schwab /chains shape with real volume -> pcr 0.5
    "putExpDateMap": {"e": {"100.0": [{"totalVolume": 50}]}},
    "callExpDateMap": {"e": {"100.0": [{"totalVolume": 100}]}},
}


class _CountingClient(_FakeClient):
    def __init__(self):
        self.chain_calls = 0

    def _request(self, ep, params=None):
        if ep == "/chains":
            self.chain_calls += 1
            return dict(_CHAIN)
        return None


def test_sector_pcr_cached_across_refreshes():
    """The per-sector P/C chain fan-out (11 NTM chains) must NOT refetch on every
    120s composite refresh — the ratio moves slowly, so it is TTL-cached."""
    import sectors_ref
    sd = sectors_ref.load_sectors_data()
    L.reset_pcr_cache()
    c = _CountingClient()
    L.compute_live(c, sd)
    first = c.chain_calls
    assert first >= 11  # cold: fetched every sector chain
    snap2 = L.compute_live(c, sd)
    assert c.chain_calls == first  # within TTL: zero additional chain calls
    assert snap2["sector_pcr"] == 0.5  # cached pcr still feeds the composite


def test_sector_pcr_refetches_after_ttl():
    import sectors_ref
    sd = sectors_ref.load_sectors_data()
    L.reset_pcr_cache()
    c = _CountingClient()
    L.compute_live(c, sd)
    first = c.chain_calls
    L._PCR_CACHE["ts"] -= L.PCR_TTL_SEC + 1  # expire the TTL
    L.compute_live(c, sd)
    assert c.chain_calls == 2 * first


def test_sector_pcr_empty_result_not_cached():
    """Off-hours a chain has no volume -> pcr {}. That must NOT be cached, so the
    first refresh after the open picks up real volume immediately."""
    import sectors_ref
    sd = sectors_ref.load_sectors_data()
    L.reset_pcr_cache()
    c = _CountingClient()

    dead = _FakeClient()  # _request returns None -> pcr {}
    L.compute_live(dead, sd)
    L.compute_live(c, sd)  # next refresh must fetch, not reuse the empty {}
    assert c.chain_calls >= 11


# ── breadth: a MISSING symbol is not "no decliners" (2026-08-20) ────────────

def _bq(**kw):
    """A batched-quote map shaped like the one _breadth_inputs reads."""
    return {k: [{"close": v}] for k, v in kw.items() if v is not None}


def test_missing_decn_does_not_fabricate_max_bullish_breadth():
    """`(advn/decn) if (advn and decn) else (inf if advn else None)` could not
    tell `decn == 0` (a real 10/10 tape) from `decn is None` (the symbol simply
    absent from the batch, which a per-symbol quote failure produces). The
    absent case handed `inf` to breadth.score, which maps inf -> score 10:
    MAXIMUM bullish breadth conjured from a missing quote."""
    from live_composite import _breadth_ratio
    assert _breadth_ratio(1500.0, None) is None      # absent -> no reading
    assert _breadth_ratio(1500.0, 0.0) == float("inf")   # genuinely no decliners


def test_breadth_ratio_normal_and_bearish_cases():
    from live_composite import _breadth_ratio
    assert _breadth_ratio(1500.0, 500.0) == 3.0
    # advn == 0 with decliners is an EXTREME bearish tape, not "no reading"
    assert _breadth_ratio(0.0, 900.0) == 0.0


def test_breadth_ratio_none_when_advancers_absent():
    from live_composite import _breadth_ratio
    assert _breadth_ratio(None, 900.0) is None
    assert _breadth_ratio(None, None) is None


def test_inf_still_scores_max_when_it_is_real():
    """Guard the other direction: the inf path must survive for a true 10/10."""
    from scoring import breadth as _breadth
    r = _breadth.score(pct_above_50="", nyse_ad="Neutral", new_highs=0.0,
                       new_lows=0.0, breadth_ratio=float("inf"),
                       breadth_numeric=1500.0)
    assert r.score == 10


# ── A dead data feed is ABSENCE, not the bottom of the scale (audit AC-40) ───
# With every fetch failing, ``scoring_composite.blend`` has nothing to weigh and
# returns 0.0 at aggregate confidence 0.0. ``signal_band`` and the bridge's
# regime ladder are both TOTAL over the reals, so that zero fell out of their
# last branch as the most bearish reading in the vocabulary: "Strong Bear /
# Short / 0.70x" on the screens and the phone, ``strong_bearish`` in the bridge
# - where the scanner counted it as a bear vote and could block put credit
# spreads. These tests drive the PRODUCER with a client that fails every call;
# a guard that is only tested from the consumer side can never fire.

class _DeadClient:
    """Every Schwab call raises - the proxy is down."""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise ConnectionError("proxy down")
        return _boom


# Synthetic on purpose: the sector workbook is gitignored machine state.
_SD = [{"kind": "sector", "etf": e, "sector": e, "sp_weight": 9.0}
       for e in ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU",
                 "XLRE", "XLC")]


def _dead_snapshot():
    L.reset_pcr_cache()
    return L.compute_live(_DeadClient(), _SD)


def test_composite_reading_is_the_total_when_there_is_one():
    assert L.composite_reading({"total_score": "6.81", "aggregate_confidence": 0.9}) == 6.81
    assert L.composite_reading({"total_score": 1.0, "aggregate_confidence": 1.0}) == 1.0


def test_composite_reading_without_a_confidence_field_trusts_the_total():
    # A backfill snapshot carries no aggregate confidence at all.
    assert L.composite_reading({"total_score": "4.20"}) == 4.2


def test_composite_reading_is_none_for_a_total_no_scorer_can_produce():
    # Components score 1-10, so a total at or below zero is not a low reading.
    for total in ("0.00", 0, 0.0, -1.5, None, "", "n/a", float("nan"), float("inf")):
        assert L.composite_reading({"total_score": total}) is None, total


def test_composite_reading_is_none_at_zero_aggregate_confidence():
    assert L.composite_reading({"total_score": "4.20", "aggregate_confidence": 0.0}) is None


def test_composite_reading_tolerates_a_missing_composite():
    assert L.composite_reading(None) is None
    assert L.composite_reading({}) is None


def test_a_dead_feed_publishes_no_band_in_the_snapshot():
    comp = _dead_snapshot()["composite"]
    assert comp["aggregate_confidence"] == 0.0
    assert comp["bias"] is None
    assert comp["size_modifier"] is None


def test_a_dead_feed_writes_unknown_to_the_bridge_not_strong_bearish():
    p = L.build_bridge_payload(_dead_snapshot(), history_scores=[6.0, 6.2],
                               spy_closes=[], generated_at="t")
    assert p["regime"] == "unknown"
    assert p["bias"] == "unknown"
    assert p["composite_score"] is None
    assert p["position_size_modifier"] is None
    assert p["contrarian_signal"] is None
    assert p["aggregate_confidence"] == 0.0


def test_a_dead_feed_bridge_still_roundtrips_as_json(tmp_path):
    import bridge
    p = L.build_bridge_payload(_dead_snapshot(), history_scores=[],
                               spy_closes=[], generated_at="t")
    out = bridge.write_bridge(p, path=tmp_path / "b.json")
    assert json.loads(out.read_text())["regime"] == "unknown"


def test_a_real_bottom_of_scale_reading_is_still_strong_bearish():
    """The guard rejects ABSENCE, not bad news: a measured 1.2 keeps every word."""
    p = L.build_bridge_payload(_snap(1.2), history_scores=[2.0, 1.5],
                               spy_closes=[], generated_at="t")
    assert p["regime"] == "strong_bearish"
    assert p["bias"] == "short"
    assert p["composite_score"] == 1.2
    assert p["position_size_modifier"] == "0.70x"
    assert p["contrarian_signal"] == "Strong Bear"


# --- AC-49: the two consumers of the sector score tell absence from a crash ----

def test_the_history_backfill_drops_a_day_only_when_the_sector_score_is_absent():
    """It dropped ``sec_score <= 0``, which was every real crash day."""
    import ast
    import inspect
    import history_backfill
    src = inspect.getsource(history_backfill._score_one_day)
    tree = ast.parse(src)
    bad = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)
           and getattr(n.left, "id", "") == "sec_score"
           and any(isinstance(op, (ast.LtE, ast.Lt)) for op in n.ops)]
    assert bad == [], "the backfill still compares sec_score against zero"
    assert "sec_score is None" in src


def test_the_live_composite_carries_an_absent_sector_score_at_no_confidence():
    import inspect
    import live_composite
    src = inspect.getsource(live_composite.compute_live)
    assert "sec_score is None" in src or "sec_score is not None" in src


# ── Before the open: two inputs that read as live and are not (2026-10-29) ────
# Measured on prod at 05:50 CT: $ADVN 4 / $DECN 0 made breadth an infinite A/D
# ratio, score 10. $VIX1D and $VIX9D last printed at 15:15 CT the day before
# while $VIX traded on, so the VIX1D/VIX ratio paired two different days and
# scored 10 "calm". Together they lifted a risk-off morning to 6.73.

_T_NOW = 1761734400000            # $VIX's print, epoch ms
_T_YESTERDAY = _T_NOW - 14 * 3600 * 1000 - 35 * 60 * 1000


class _PreOpenClient(_FakeClient):
    """Quotes shaped like the flattened proxy mapping, per symbol."""

    def __init__(self, advn=4.0, decn=0.0, v1d_time=_T_YESTERDAY,
                 v9d_time=_T_YESTERDAY):
        self.q = {
            "$VIX": {"last": 17.03, "trade_time": _T_NOW},
            "$VIX1D": {"last": 13.15, "trade_time": v1d_time},
            "$VIX9D": {"last": 15.6, "trade_time": v9d_time},
            "$ADVN": {"last": advn, "trade_time": _T_NOW},
            "$DECN": {"last": decn, "trade_time": _T_NOW},
        }

    def get_quote(self, s):
        return self.q.get(s, {"last": 1.5})

    def get_quotes(self, syms):
        # An index this test does not name is absent, as $NYHGH was on prod.
        return {s: self.q.get(s, {"last": 17.0, "change_pct": 0.5})
                for s in syms if s in self.q or not s.startswith("$")}


def _live(client):
    L.reset_pcr_cache()
    return L.compute_live(client, _SD)


def test_pre_open_advance_decline_counters_are_no_breadth_reading():
    snap = _live(_PreOpenClient(advn=4.0, decn=0.0))
    assert snap["component_confidence"]["breadth"] == 0.0
    assert snap["component_scores"]["breadth"] == 0.0


def test_a_live_advance_decline_count_is_still_scored():
    snap = _live(_PreOpenClient(advn=1800.0, decn=1100.0))
    assert snap["component_confidence"]["breadth"] > 0.0
    assert snap["component_scores"]["breadth"] == 7.0      # ratio 1.64


def test_the_breadth_floor_is_read_from_config(monkeypatch):
    import composite_config
    monkeypatch.setattr(composite_config, "min_breadth_issues", lambda: 5000)
    snap = _live(_PreOpenClient(advn=1800.0, decn=1100.0))
    assert snap["component_confidence"]["breadth"] == 0.0


def test_a_vix1d_and_vix9d_from_yesterday_leave_the_vix_complex():
    snap = _live(_PreOpenClient())
    # Term alone is left: its confidence weight is 0.50 of the complex.
    assert snap["component_confidence"]["vix_complex"] == 0.5
    assert snap["volatility"]["interpretation"].endswith("-1D0-S0")
    assert snap["_vix1d"] == 0.0


def test_a_vix1d_and_vix9d_printed_with_vix_are_still_used():
    snap = _live(_PreOpenClient(v1d_time=_T_NOW - 60_000, v9d_time=_T_NOW))
    assert snap["component_confidence"]["vix_complex"] == 1.0
    assert "-1D0" not in snap["volatility"]["interpretation"]


def test_a_quote_with_no_trade_time_is_not_refused():
    """Absence skips the gate: a client that sends no time keeps the reading."""
    snap = _live(_PreOpenClient(v1d_time=None, v9d_time=None))
    assert snap["component_confidence"]["vix_complex"] == 1.0


def test_the_vix_age_limit_is_read_from_config(monkeypatch):
    import composite_config
    monkeypatch.setattr(composite_config, "vix_max_skew_min", lambda: 24 * 60)
    snap = _live(_PreOpenClient())
    assert snap["component_confidence"]["vix_complex"] == 1.0

