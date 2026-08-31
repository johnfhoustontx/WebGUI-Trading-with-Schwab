"""The Pillow market-snapshot card — the second consumer of card_kit.

Same reason as the briefing card: the 30-minute snapshot shipped as a headless
Chrome screenshot of a 1450px infographic, and there is no browser on the Linux
host. It has been pushing a bare text caption every half hour since the
migration.

The snapshot has MORE structure to work with than the briefing, not less: the
handler already hands the push path the dashboard categories, the trend block,
the composite and the regime memberships as plain dicts. The HTML was only ever
an intermediate.
"""
import io
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from services.options_svc import snapshot_card as sc  # noqa: E402

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


DASHBOARD = {
    "categories": [
        {"category": "Volatility", "tiles": [
            {"display": "VIX", "last": 15.18, "change_pct": 5.19,
             "color_state": "risk_off_strong"},
            {"display": "VIX9D", "last": 13.9, "change_pct": -1.2,
             "color_state": "risk_on_mild"},
        ]},
        {"category": "Broad-Market ETF", "tiles": [
            {"display": "SPY", "last": 766.2, "change_pct": -0.42,
             "color_state": "risk_off_mild"},
            {"display": "QQQ", "last": 715.2, "change_pct": -0.61,
             "color_state": "risk_off_mild"},
            {"display": "DXY", "last": 99.1, "change_pct": 0.0,
             "color_state": "flat"},
            {"display": "???", "last": None, "change_pct": None,
             "color_state": "no_data"},
        ]},
    ],
    "proxy_up": True,
}
TREND = {"smoothed_score": 38.62, "score": 37.31, "label": "Lack of Bearishness",
         "description": "Refuses to drop, puts cheap/undefended - favor PCS.",
         "confidence": 0.674}
SENTIMENT = {"total_score": "3.87", "bias": "Cautious",
             "size_modifier": "0.85x", "aggregate_confidence": 0.9}
REGIME = {"memberships": {"mean_reversion": 0.196, "trending": 0.104,
                          "breakout": 0.0, "choppy": 0.164, "crisis": 0.536}}


def _size(raw):
    with Image.open(io.BytesIO(raw)) as im:
        return im.size


#############################################
# Never raises — it runs in the 30-minute scheduler tick
#############################################

@pytest.mark.parametrize("kw", [
    {},
    {"dashboard": None, "trend": None, "sentiment": None, "regime": None},
    {"dashboard": "nonsense", "trend": [], "sentiment": 3, "regime": "x"},
    {"dashboard": {"categories": "not a list"}},
    {"dashboard": {"categories": [{"category": None, "tiles": None}]}},
    {"sentiment": {"total_score": float("nan")}},
    {"regime": {"memberships": {"crisis": float("inf")}}},
    {"trend": {"smoothed_score": None, "label": None}},
], ids=lambda k: repr(k)[:44])
def test_render_never_raises(kw):
    args = {"dashboard": DASHBOARD, "trend": TREND, "sentiment": SENTIMENT,
            "regime": REGIME}
    args.update(kw)
    out = sc.render_snapshot_png(slot="10:30", **args)
    assert out is None or isinstance(out, bytes)


def test_render_produces_a_real_png():
    raw = sc.render_snapshot_png(DASHBOARD, TREND, SENTIMENT, REGIME, slot="10:30")
    assert isinstance(raw, bytes) and raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = _size(raw)
    assert w == sc.WIDTH * sc.SCALE
    assert h > 300


def test_more_categories_make_a_taller_card():
    """Height is measured from the layout, so the board cannot be clipped."""
    one = {"categories": DASHBOARD["categories"][:1]}
    assert _size(sc.render_snapshot_png(DASHBOARD, TREND, SENTIMENT, REGIME,
                                        slot="x"))[1] > \
           _size(sc.render_snapshot_png(one, TREND, SENTIMENT, REGIME,
                                        slot="x"))[1]


#############################################
# The tile palette must be the SAME one the HTML uses
#############################################

def test_tile_colours_come_from_the_html_renderer_not_a_second_copy():
    """market_snapshot._TILE_BG/_TILE_FG already define this vocabulary.

    A second hand-written copy in RGB is how two images pushed to the same chat
    drift apart -- and this repo has scars from exactly that shape of
    duplication."""
    from services.options_svc import market_snapshot as ms
    for state, hexv in ms._TILE_BG.items():
        assert sc.tile_bg(state) == sc.hex_to_rgb(hexv)
    for state, hexv in ms._TILE_FG.items():
        assert sc.tile_fg(state) == sc.hex_to_rgb(hexv)


def test_an_unknown_tile_state_falls_back_rather_than_raising():
    """The dashboard is built upstream; a new colour_state must not crash a push."""
    assert sc.tile_bg("something_new") == sc.tile_bg("no_data")
    assert sc.tile_fg("something_new") == sc.tile_fg("no_data")
    assert sc.tile_bg(None) == sc.tile_bg("no_data")


#############################################
# The regime words are MIRRORED, never re-spelled
#############################################

def test_regime_label_is_the_existing_mirror():
    """CLAUDE.md pins the five display words across four tiers and a test guards
    a fifth copy. This card must reuse market_console's, not add one."""
    from services.options_svc import market_console as mc
    assert sc.dominant_regime(REGIME)[0] == "crisis"
    assert sc.dominant_regime(REGIME)[1] == mc.REGIME_LABELS["crisis"] == "Stressed"


def test_dominant_regime_survives_an_empty_or_broken_membership_map():
    assert sc.dominant_regime({}) == (None, "-", 0.0)
    assert sc.dominant_regime({"memberships": {}}) == (None, "-", 0.0)
    assert sc.dominant_regime({"memberships": {"crisis": float("nan")}})[0] is None


def test_dominant_regime_reports_the_share_it_drew():
    key, label, share = sc.dominant_regime(REGIME)
    assert key == "crisis" and 0.53 < share < 0.54


#############################################
# Numbers the reader acts on
#############################################

def test_sentiment_score_parses_the_string_the_service_publishes():
    """sentiment_svc publishes total_score as a FORMATTED STRING ("3.87").
    Treating it as a float would print a dash on every card."""
    assert sc.score_of(SENTIMENT) == pytest.approx(3.87)
    assert sc.score_of({"total_score": 4}) == 4.0
    assert sc.score_of({"total_score": "n/a"}) is None
    assert sc.score_of({}) is None
