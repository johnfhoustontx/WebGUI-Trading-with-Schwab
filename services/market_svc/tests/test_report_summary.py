"""The Desk summary is the published report's own highlights.

The fixture reproduces the markup ``render.build`` (the report tooling, outside
this repo) emits: a style block full of selector names, a page head, the slot
chip, an ``h1`` with coloured number spans and entities, and one ``h2`` per
section followed by a lede.
"""
from services.market_svc import report_summary as rs

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>NeuralStrike Market close Assessment 2026-09-14</title>
<style>h1 { font-size: 20pt } h2 { color: red } .slotchip { x: y }</style></head><body>
<div class="pagehead"><div class="meta">Market Assessment &middot; Market close</div></div>
<div class="slotchip">Market close &middot; 16:20 CT</div><a class="pdfbtn" href="x.pdf" download>Download PDF</a>
<h1>A rotation, not a rout: software <span class="mono" style="color:#00E5A0">+5.21%</span>,
 chips <span class="mono" style="color:#FF4D6D">&minus;4.32%</span></h1>
<p class="dek">SPX closed lower.</p>
{sections}
</body></html>"""


def _section(title):
    return (f'<section class="sec"><div class="avoid"><div class="sechead">'
            f'<p class="eyebrow">01 &middot; Macro</p><h2>{title}</h2>'
            f'<p class="lede">Lede text.</p></div><p>Body</p></div></section>')


def _page(*titles):
    return _PAGE.replace("{sections}", "".join(_section(t) for t in titles))


def test_headline_highlights_and_provenance():
    p = rs.parse_report(_page("Chips broke", "Software &amp; staples ripped"),
                        "2026-09-14 5 close 16:20CT\n")
    assert p["headline"] == "A rotation, not a rout: software +5.21%, chips −4.32%"
    assert p["highlights"] == ["Chips broke", "Software & staples ripped"]
    assert (p["slot"], p["slot_label"], p["report_date"], p["as_of"]) == (
        "close", "Market close", "2026-09-14", "16:20 CT")
    assert p["report_url"].endswith("/report.html")


def test_at_most_five_highlights_in_report_order():
    titles = [f"Point {i}" for i in range(1, 7)]
    assert rs.parse_report(_page(*titles))["highlights"] == titles[:5]
    assert rs.MAX_HIGHLIGHTS == 5


def test_a_report_without_sections_falls_back_to_its_headline():
    p = rs.parse_report(_page())
    assert p["highlights"] == [p["headline"]]


def test_inline_markup_inside_a_headline_does_not_end_it_early():
    p = rs.parse_report(_page("Walls <b>stepped</b> down<br>under price"))
    assert p["highlights"] == ["Walls stepped down under price"]


def test_not_a_report_is_none():
    assert rs.parse_report("") is None
    assert rs.parse_report("<html><body><p>502 Bad Gateway</p></body></html>") is None


def test_read_report_off_disk(tmp_path):
    assert rs.read_report(tmp_path) is None
    assert rs.report_stamp(tmp_path) is None
    (tmp_path / "latest.html").write_text(_page("Chips broke"), encoding="utf-8")
    p = rs.read_report(tmp_path)
    assert p["highlights"] == ["Chips broke"] and p["slot"] == ""
    assert rs.report_stamp(tmp_path) is not None


def test_the_report_lives_under_the_served_site_root():
    from repo_paths import SITE_ROOT
    assert rs.REPORTS_DIR == SITE_ROOT / "reports"
