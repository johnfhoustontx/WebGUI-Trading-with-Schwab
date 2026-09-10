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


class _Msg:
    def __init__(self, text, stop="end_turn"):
        self.content = [type("B", (), {"text": text, "type": "text"})()]
        self.stop_reason = stop


def _client(text, stop="end_turn", seen=None):
    class _C:
        class messages:
            @staticmethod
            def create(**kw):
                if seen is not None:
                    seen.update(kw)
                return _Msg(text, stop)
    return _C()


def test_generate_summary_returns_the_sentence_its_inputs_and_when():
    p = _packet()
    out = compute.generate_summary(p, client=_client("Fear builds; lean defensive."))
    assert out["narrative"] == "Fear builds; lean defensive."
    assert out["inputs"] == p
    assert out["as_of"].endswith("+00:00")


def test_generate_summary_sends_only_the_packet():
    import json
    seen = {}
    p = _packet()
    compute.generate_summary(p, client=_client("x", seen=seen))
    assert json.loads(seen["messages"][0]["content"]) == p
    assert seen["model"] == compute._SUMMARY_MODEL


def test_the_prompt_asks_for_the_consolidation_and_a_posture():
    s = compute._SUMMARY_SYSTEM.lower()
    for phrase in ("contrarian", "one reading", "agree or conflict", "posture",
                   "verbatim", "no prices"):
        assert phrase in s, phrase


def test_max_tokens_keeps_headroom():
    """A cap, not a spend: billing is on generated tokens. A trim below 300 risks
    a sentence cut mid-word that still renders as if complete."""
    assert compute._SUMMARY_MAX_TOKENS >= 300


def test_a_cut_off_reply_is_logged(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="market_svc.compute"):
        compute.generate_summary(_packet(), client=_client("Fear bu", stop="max_tokens"))
    assert any("max_tokens" in r.getMessage() for r in caplog.records)


def test_no_client_is_an_empty_sentence_never_a_made_up_one():
    out = compute.generate_summary(_packet(), client=None)
    assert out["narrative"] == "" and out["inputs"] == _packet()


def test_the_packet_reader_rebuilds_only_when_a_view_moves(monkeypatch):
    from shared.bus import Bus
    bus = Bus()
    compute.reset_packet_memo()
    bus.cache_set("cache:sentiment:composite", _composite())
    bus.cache_set("cache:sentiment:regime", _regime())
    bus.cache_set("cache:sentiment:bullbear", _bullbear())
    builds = []
    real = compute.build_summary_packet
    monkeypatch.setattr(compute, "build_summary_packet",
                        lambda *a, **k: builds.append(1) or real(*a, **k))
    first = compute.read_summary_packet(bus, now=_OPEN)
    again = compute.read_summary_packet(bus, now=_OPEN)
    assert first == again and len(builds) == 1          # no version moved
    bus.cache_set("cache:sentiment:regime", _regime(label="Balanced"))
    assert compute.read_summary_packet(bus, now=_OPEN)["regime"]["word"] == "Balanced"
    assert len(builds) == 2
