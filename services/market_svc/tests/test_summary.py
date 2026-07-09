from services.market_svc import compute


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


def test_build_summary_packet_extracts_compact_facts():
    p = compute.build_summary_packet(_dash(), _sent())
    assert p["sentiment"]["score"] == "3.9" and p["sentiment"]["bias"] == "Cautious"
    assert p["trend"]["label"] == "Neutral" and p["trend"]["score"] == 42.7
    assert p["put_call"] == 1.34
    # a few notable movers are captured
    assert any(m["display"] == "XLK" for m in p["movers"])
    assert "VIX" in {t["display"] for t in p["vol"]}


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
