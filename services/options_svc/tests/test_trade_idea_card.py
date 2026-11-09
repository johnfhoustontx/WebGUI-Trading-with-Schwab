"""The trade idea card at 1200x630: what sits in the Instagram grid strip, and what
moves to the wings.

The strip (x 363.75-836.25 of 1200) is all a profile-grid thumbnail shows, so the
ticker, the strategy line and the grade must land inside it, and the figures that
do not fit there must still all be printed -- in the wings.
"""
import io

import pytest

from services.options_svc import trade_idea as T
from services.options_svc import trade_idea_card as C
from services.options_svc.tests.test_trade_idea import NOW, long_call, long_put, pcs
from shared import card_draw as D


def ic_idea():
    return T.normalize(pcs(type="IC", call_short=775.0, call_long=780.0))


def _texts(canvas):
    return [d[0] for d in canvas.drawn]


def _entries(canvas, text):
    return [d for d in canvas.drawn if d[0] == text]


def test_png_is_2400_by_1260():
    Image = pytest.importorskip("PIL.Image")
    png = C.render_trade_idea_png(ic_idea(), now=NOW)
    assert Image.open(io.BytesIO(png)).size == (2400, 1260)
    assert (C.WIDTH, C.HEIGHT) == (1200, 630)


def test_essentials_inside_the_strip():
    pytest.importorskip("PIL.Image")
    idea = ic_idea()
    c = C.layout(idea, NOW)
    wanted = [idea["symbol"], idea["label"], f"${idea['spot']:,.2f}", idea["grade"],
              f"{idea['score']:.0f}"]
    for text in wanted:
        hits = _entries(c, text)
        assert hits, f"{text!r} was not drawn"
        for _s, x0, _y0, x1, _y1 in hits:
            assert D.in_strip(x0, x1), (text, x0, x1)


def test_wings_hold_the_rest():
    pytest.importorskip("PIL.Image")
    c = C.layout(ic_idea(), NOW)
    for text in ("$378", "$122", "78%", "738.78 / 776.22"):
        hits = _entries(c, text)
        assert hits, f"{text!r} was not drawn"
        for _s, x0, _y0, x1, _y1 in hits:
            assert x0 >= 856 or x1 <= 344, (text, x0, x1)


def test_every_figure_today_is_still_printed():
    pytest.importorskip("PIL.Image")
    idea = ic_idea()
    printed = set(_texts(C.layout(idea, NOW)))
    for lg in idea["legs"]:
        assert T.strike_text(lg["strike"]) in printed
    for text in ("$378", "$122", "78%", "738.78 / 776.22", "Good", "70"):
        assert text in printed, text


@pytest.mark.parametrize("row", [pcs(), pcs(type="IC", call_short=775.0, call_long=780.0),
                                 long_put(), long_call()],
                         ids=["2 legs", "4 legs", "1 leg put", "1 leg debit call"])
def test_leg_counts_fit(row):
    pytest.importorskip("PIL.Image")
    idea = T.normalize(row)
    c = C.layout(idea, NOW)
    rows_bottom = 132 + 46 * len(idea["legs"]) - 8
    expiry = T.expiry_text(idea["expiration"])
    notes = [d for d in c.drawn if d[0].startswith(expiry)]
    assert notes, "no expiry line"
    for _s, x0, y0, x1, y1 in notes:
        assert y0 >= rows_bottom - 1 and y1 < 440 and x1 <= 344
    strikes = [d for d in c.drawn if d[0] in {T.strike_text(lg["strike"]) for lg in idea["legs"]}
               and d[1] < 344]
    assert strikes and max(d[4] for d in strikes) <= rows_bottom


def test_card_facts_render_identically():
    pytest.importorskip("PIL.Image")
    idea = ic_idea()
    facts = C.card_facts(idea)
    assert set(facts) == set(C.CARD_FIELDS)
    assert C.render_trade_idea_png(idea, now=NOW) == C.render_trade_idea_png(facts, now=NOW)
