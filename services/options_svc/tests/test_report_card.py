import datetime as dt
import io

from services.options_svc import report_card

REPORT = {"headline": "Buyers defend the flip; breadth thin",
          "highlights": ["SPX holds 6,610 gamma flip", "Semis lead, staples lag",
                         "VIX term back in contango", "Flow: calls bid in NVDA",
                         "a fifth one is not drawn"],
          "slot": "midday", "slot_label": "Midday Report", "report_date": "2026-09-22",
          "as_of": "11:30 CT", "report_url": "https://neuralstrike.co/report.html"}


def test_renders_a_1200x675_card_at_2x():
    from PIL import Image
    png = report_card.render_report_png(REPORT, now=dt.datetime(2026, 9, 22, 11, 35))
    im = Image.open(io.BytesIO(png))
    assert im.size == (2400, 1350)


def test_never_raises_on_garbage():
    for bad in ({"headline": None, "highlights": 7}, {}, None, {"headline": "x" * 2000}):
        out = report_card.render_report_png(bad)
        assert out is None or isinstance(out, bytes)


def test_draws_at_most_four_string_highlights():
    assert report_card.highlights(REPORT) == REPORT["highlights"][:4]
    assert report_card.highlights({"highlights": ["a", 5, None, "b"]}) == ["a", "b"]


def test_a_missing_headline_is_nothing_to_post():
    assert report_card.render_report_png({**REPORT, "headline": ""}) is None
    assert report_card.render_report_png({**REPORT, "headline": None}) is None


def test_a_render_failure_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("draw failed")
    monkeypatch.setattr(report_card, "_render", boom)
    assert report_card.render_report_png(REPORT, now=dt.datetime(2026, 9, 22, 11, 35)) is None


def test_the_eyebrow_names_the_slot(monkeypatch):
    seen = []
    orig = report_card._header

    def spy(c, now, label="TRADE IDEA"):
        seen.append(label)
        return orig(c, now, label=label)

    monkeypatch.setattr(report_card, "_header", spy)
    now = dt.datetime(2026, 9, 22, 11, 35)
    assert report_card.render_report_png(REPORT, now=now)
    assert report_card.render_report_png({**REPORT, "slot_label": None}, now=now)
    assert seen == ["MIDDAY REPORT", "MARKET REPORT"]
