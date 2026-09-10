import datetime as dt
from zoneinfo import ZoneInfo

from services.market_svc import compute

_CT = ZoneInfo("America/Chicago")
_OPEN = dt.datetime(2026, 9, 10, 10, 0, tzinfo=_CT)      # Thursday, session open
_PRE = dt.datetime(2026, 9, 10, 7, 0, tzinfo=_CT)        # before the bell


def _composite(**trend):
    return {"live": {"composite": {"total_score": "3.98", "bias": "Cautious"}},
            "derived": {"size": "0.85x", "bias": "Cautious", "signal": "Bearish",
                        "trend": {"state": "lack_of_bearishness",
                                  "smoothed_score": 38.6, **trend}}}


def _regime(**over):
    return {"label": "Whipsaw", "committed_label": "choppy",
            "confidence": 0.62, **over}


def _row(sym, trend, excess, day_pct=None, day_excess=None):
    return {"symbol": sym, "raw": {"trend": trend, "excess": excess},
            "day_pct": day_pct, "day_excess": day_excess}


def _bullbear(benchmark=0.4):
    return {"benchmark_day_pct": benchmark, "levels": {"sector": [
        _row("XLK", 0.3, 0.2, day_pct=-0.5, day_excess=-0.9),   # quarter RL, today FL
        _row("XLU", -0.2, 0.1, day_pct=0.8, day_excess=0.4),    # quarter FL, today RL
        _row("XLE", -0.1, -0.3, day_pct=1.1, day_excess=0.7),   # quarter FLag, today RL
    ]}}


def _dash():
    return {"categories": [
        {"category": "Volatility", "tiles": [
            {"display": "VIX", "last": 16.9, "change_pct": 4.8, "color_state": "risk_off_strong"},
            {"display": "SKEW", "last": 150.0, "change_pct": 2.8, "color_state": "risk_off_strong"}]},
        {"category": "Cash Index", "tiles": [
            {"display": "SPX", "last": 7482.0, "change_pct": -0.3, "color_state": "risk_off_mild"},
            {"display": "NDX", "last": 29252.0, "change_pct": 0.3, "color_state": "risk_on_mild"}]},
        {"category": "Sector SPDR", "tiles": [
            {"display": "XLK", "last": 181.0, "change_pct": 1.4, "color_state": "risk_on_strong"},
            {"display": "XLB", "last": 50.0, "change_pct": -2.6, "color_state": "risk_off_strong"}]},
    ]}


def _sent():
    return {"live": {"composite": {"total_score": "3.9", "bias": "Cautious"},
                     "sector_pcr": 1.34,
                     "breadth": {"interpretation": "A/D 0.41:1 - weak"}},
            "derived": {"trend": {"score": 42.7, "label": "Neutral"}}}


def test_packet_carries_the_six_readings_in_the_screens_words():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert p["sentiment"] == {"composite": 3.98}
    assert p["trend"] == {"word": "Gliding", "score": 38.6}
    assert (p["bias"], p["signal"], p["size"]) == ("Cautious", "Bearish", "0.85x")
    assert p["regime"] == {"word": "Whipsaw", "confidence": 0.62}


def test_packet_counts_bullbear_on_today_once_the_bell_has_rung():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert p["bullbear"]["horizon"] == "today"
    assert p["bullbear"]["counts"]["rising_leading"] == 2
    assert p["bullbear"]["counts"]["falling_lagging"] == 1


def test_packet_counts_bullbear_on_the_quarter_before_the_bell():
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_PRE)
    assert p["bullbear"]["horizon"] == "quarter"
    assert p["bullbear"]["counts"]["rising_leading"] == 1


def test_packet_counts_the_quarter_when_no_benchmark_move_exists():
    """A dead proxy mid-session leaves benchmark_day_pct None - the Desk strip's
    rule: no benchmark, no 'today'."""
    p = compute.build_summary_packet(_composite(), _regime(),
                                     _bullbear(benchmark=None), now=_OPEN)
    assert p["bullbear"]["horizon"] == "quarter"


def test_packet_withholds_confidence_on_an_unclear_sample():
    p = compute.build_summary_packet(_composite(), _regime(unclear=True), {},
                                     now=_OPEN)
    assert p["regime"]["confidence"] is None
    assert p["regime"]["word"] == "Whipsaw"


def test_packet_quotes_no_prices():
    """Index moves, vol quotes and sector movers were dropped on purpose: a
    sentence quoting them is stale between refreshes."""
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    assert set(p) == {"sentiment", "trend", "bias", "signal", "size",
                      "regime", "bullbear"}


def test_packet_from_cold_caches_is_all_absent_never_neutral():
    p = compute.build_summary_packet({}, {}, {}, now=_OPEN)
    assert p["sentiment"]["composite"] is None and p["trend"]["word"] is None
    assert p["bias"] is None and p["signal"] is None
    assert p["regime"]["word"] == "Unclear"
    assert p["bullbear"] == {"horizon": None, "counts": {}}


def _packet(**over):
    p = compute.build_summary_packet(_composite(), _regime(), _bullbear(), now=_OPEN)
    p.update(over)
    return p


def test_fingerprint_ignores_movement_below_display_resolution():
    a = _packet()
    b = _packet(sentiment={"composite": 4.10})               # 3.98 -> 4.10: both 4.0
    b["trend"] = {"word": "Gliding", "score": 39.9}          # 38.6 -> 39.9: both 40
    assert compute.summary_fingerprint(a) == compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_word_changes():
    a = _packet()
    for key, val in (("bias", "Neutral"), ("signal", "Neutral")):
        assert compute.summary_fingerprint(a) != compute.summary_fingerprint(
            _packet(**{key: val})), key
    b = _packet()
    b["regime"] = {"word": "Balanced", "confidence": 0.62}
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_number_crosses_its_step():
    a = _packet()
    b = _packet(sentiment={"composite": 4.40})               # 4.0 -> 4.5
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_fingerprint_moves_when_a_sector_changes_quadrant():
    a = _packet()
    b = _packet()
    b["bullbear"] = {"horizon": "today",
                     "counts": {**a["bullbear"]["counts"], "rising_leading": 3,
                                "falling_lagging": 0}}
    assert compute.summary_fingerprint(a) != compute.summary_fingerprint(b)


def test_nothing_to_summarize_has_no_fingerprint():
    """Cold caches must not trigger a paid call to describe nothing."""
    assert compute.summary_fingerprint(
        compute.build_summary_packet({}, {}, {}, now=_OPEN)) is None
    assert compute.summary_fingerprint(None) is None


def test_generate_summary_no_client_is_empty_but_safe():
    # The autouse _no_live_claude fixture forces _make_summary_client → None, so the
    # default real-client resolution path returns an empty narrative (no network).
    out = compute.generate_summary(_dash(), _sent(), client=None)
    assert out["narrative"] == ""


def test_generate_summary_with_fake_client_returns_narrative():
    class _Msg:
        def __init__(self, text): self.content = [type("B", (), {"text": text, "type": "text"})()]
    class _FakeClient:
        class messages:
            @staticmethod
            def create(**kw): return _Msg("Cautious, narrow tape — breadth weak.")
    out = compute.generate_summary(_dash(), _sent(), client=_FakeClient())
    assert "Cautious" in out["narrative"]
    assert len(out["narrative"]) <= compute._SUMMARY_MAX_CHARS + 50
