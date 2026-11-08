"""The blog card: its size, where its words land, and what it does with awkward text."""
import io

from PIL import Image

from services.blog_svc import card
from shared import card_draw as D
from shared import social_cards

ENTRY = {"title": "Why the gamma flip moved forty points in a day",
         "summary": "What dealer positioning did on Thursday, and what it meant for the 0-DTE condor.",
         "tags": ["Gamma", "0-DTE"], "published_at": "2026-11-08T15:00:00+00:00"}
LONG = ("An unusually long title that goes on and on about dealer positioning, the gamma flip, "
        "charm into the close and the condor stop rule")
CFG = social_cards.config()["blog"]


def _drawn(c, *texts):
    return [d for d in c.drawn if any(t in d[0] for t in texts)]


def _words(c):
    """Everything drawn in the middle column: the title lines and the summary."""
    skip = ("NEURALSTRIKE", "neuralstrike.co/blog", "Gamma", "0-DTE")
    return [d for d in c.drawn if d[0] not in skip and not d[0].startswith("BLOG")]


def test_png_is_2400_by_1260():
    png = card.render_blog_card(ENTRY)
    assert png and Image.open(io.BytesIO(png)).size == (2400, 1260)


def test_title_and_summary_inside_the_strip():
    c = card.layout(ENTRY)
    mid = _words(c)
    assert len(mid) >= 4                                    # title segments and summary lines
    for text, x0, _y0, x1, _y1 in mid + _drawn(c, "NEURALSTRIKE", "BLOG"):
        assert D.in_strip(x0, x1), (text, x0, x1)


def test_long_title_shrinks_then_ellipsis():
    title = (LONG + " " + LONG)[:160]
    assert len(title) == 160
    c = card.layout({**ENTRY, "title": title})
    assert c.title_size == CFG["title_min_px"]
    assert 1 < len(c.title_lines) <= CFG["title_max_lines"]
    assert c.title_lines[-1].endswith("…")
    for text, x0, _y0, x1, _y1 in _words(c):
        assert D.in_strip(x0, x1), (text, x0, x1)


def test_short_title_keeps_the_largest_size():
    assert card.layout(ENTRY).title_size == CFG["title_max_px"]


def test_unbroken_long_word_is_cut_not_spilled():
    word = "x" * 40
    c = card.layout({**ENTRY, "title": word})
    assert c.title_lines and c.title_lines[0].endswith("…")
    assert all(D.in_strip(x0, x1) for _t, x0, _y0, x1, _y1 in _words(c))


def test_no_summary_no_tags():
    c = card.layout({"title": "Charm", "published_at": ENTRY["published_at"]})
    texts = [d[0] for d in c.drawn]
    assert "Charm" in texts
    assert not any("Thursday" in t for t in texts)
    assert "Gamma" not in texts
    assert card.render_blog_card({"title": "Charm"})        # no date either: still drawn


def test_tags_are_drawn_in_the_right_wing():
    c = card.layout(ENTRY)
    tags = [d for d in c.drawn if d[0] in ("Gamma", "0-DTE")]
    assert len(tags) == 2 and all(d[3] <= 1140 and d[1] >= 856 for d in tags)


def test_split_title():
    assert card.split_title("Why the gamma flip moved forty points in a day") == (
        "Why the gamma flip moved", "forty points in a day")
    assert card.split_title("Charm") == ("Charm", "")


def test_digest_moves_with_each_field_and_version(monkeypatch):
    base = card.digest(ENTRY)
    assert base == card.digest(dict(ENTRY))
    for field, value in (("title", "Other"), ("summary", "Other"), ("tags", ["Other"]),
                         ("published_at", "2026-11-09T15:00:00+00:00")):
        assert card.digest({**ENTRY, field: value}) != base, field
    monkeypatch.setattr(card, "RENDER_VERSION", card.RENDER_VERSION + 1)
    assert card.digest(ENTRY) != base


def test_render_never_raises(monkeypatch):
    out = card.render_blog_card({})
    assert out is None or isinstance(out, bytes)
    monkeypatch.setattr(card, "layout", lambda e: 1 / 0)
    assert card.render_blog_card(ENTRY) is None
