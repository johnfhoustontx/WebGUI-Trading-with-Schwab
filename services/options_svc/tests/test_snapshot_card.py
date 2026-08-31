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
# The regime must agree with the rest of the product
#############################################

def test_committed_regime_mirrors_the_html_renderer():
    """market_console.dial_card_html: label -> REGIME_LABELS[committed_label]
    -> "Unclear". The five display words are already mirrored across four tiers
    with a test guarding a fifth copy; this card reuses them."""
    from services.options_svc import market_console as mc
    r = {"label": "Balanced", "committed_label": "mean_reversion",
         "memberships": {"mean_reversion": 0.31, "crisis": 0.26}}
    key, label, share = sc.committed_regime(r)
    assert (key, label) == ("mean_reversion", "Balanced")
    assert share == pytest.approx(0.31), "the share must describe the NAMED regime"
    assert mc.REGIME_LABELS["mean_reversion"] == "Balanced"


def test_committed_regime_is_not_the_argmax_of_memberships():
    """THE regression this replaced, and it was live for one commit.

    The committed label carries hysteresis; the raw mix moves every tick.
    Measured on the real payload: memberships peaked at `crisis` while the
    committed label was `mean_reversion`, so the card drew "Stressed" while the
    caption in the SAME push said "Balanced"."""
    r = {"label": "Balanced", "committed_label": "mean_reversion",
         "memberships": {"mean_reversion": 0.31, "crisis": 0.54}}
    assert sc.committed_regime(r)[1] == "Balanced", "argmax would say Stressed"


def test_committed_regime_falls_back_through_the_documented_order():
    from services.options_svc import market_console as mc
    assert sc.committed_regime({"committed_label": "crisis"})[1] ==         mc.REGIME_LABELS["crisis"] == "Stressed"
    assert sc.committed_regime({})[1] == "Unclear"
    assert sc.committed_regime(None)[1] == "Unclear"
    assert sc.committed_regime({"memberships": {"crisis": 0.9}})[1] == "Unclear",         "memberships alone must NOT name a regime"


def test_committed_regime_share_is_zero_when_unknown():
    assert sc.committed_regime({"label": "Balanced"})[2] == 0.0
    assert sc.committed_regime({"label": "X", "committed_label": "k",
                                "memberships": {"k": float("nan")}})[2] == 0.0


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


def test_value_only_tiles_show_no_change_just_like_the_html():
    """market_snapshot._fmt_change returns "" for a value_only tile -- $TICK,
    $ADVN, Put/Call have a level but no meaningful day change. Printing a dash
    there says "we could not get it", which is a different and wrong claim."""
    assert sc.chip_change({"value_only": True, "change_pct": 1.2}) == ""
    assert sc.chip_change({"value_only": True}) == ""
    assert sc.chip_change({"change_pct": 1.2}) == "+1.20%"
    assert sc.chip_change({"change_pct": None}) == "-", \
        "a genuinely missing change is still a dash"
