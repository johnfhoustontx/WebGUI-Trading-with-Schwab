"""The manuals' architecture diagrams (docs/manuals/diagrams/).

A manual places a diagram with a paragraph of its own,
``{{diagram: <name> | Caption}}``. The HTML build inlines the SVG (the in-app
manuals are single self-contained files, and the serving route refuses any
other path); the Word build embeds the PNG. Each diagram is drawn once, by
``make_diagrams.py``. Nothing fails when a diagram goes stale, so these tests
pin at least that every placed diagram exists in both formats, that every drawn
diagram is placed, and that both builds expand the marker."""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANUALS = ROOT / "docs" / "manuals"
DIAGRAMS = MANUALS / "diagrams"
sys.path.insert(0, str(MANUALS))
sys.path.insert(0, str(DIAGRAMS))

import build_docs  # noqa: E402
import make_diagrams  # noqa: E402

MARKER = re.compile(r"^\{\{diagram:\s*([a-z0-9-]+)\s*\|\s*(.+?)\}\}\s*$", re.M)


def _placed():
    out = {}
    for md in MANUALS.glob("*/*.md"):
        for name, _caption in MARKER.findall(md.read_text(encoding="utf-8")):
            out.setdefault(name, []).append(md.parent.name)
    return out


def test_every_placed_diagram_exists_in_both_formats():
    placed = _placed()
    assert placed, "no manual places a diagram"
    for name in placed:
        assert (DIAGRAMS / f"{name}.svg").is_file(), name
        assert (DIAGRAMS / f"{name}.png").is_file(), name


def test_every_drawn_diagram_is_placed_somewhere():
    assert set(make_diagrams.DIAGRAMS) <= set(_placed())


def test_the_html_build_inlines_the_svg_with_its_caption(tmp_path):
    md = "# Title\n\nBefore.\n\n{{diagram: request-flow | One request, end to end}}\n\nAfter.\n"
    body = build_docs.build_html(md, "T", "S", "M", tmp_path / "out.html")
    assert "{{diagram" not in body
    fig = re.search(r'<figure class="diagram" data-diagram="request-flow">(.*?)</figure>',
                    body, re.S)
    assert fig, body[:400]
    assert "<svg" in fig.group(1) and "<img" not in fig.group(1)
    assert "<figcaption>One request, end to end</figcaption>" in fig.group(1)
    # The diagram scales with the page: no fixed point size left on the root.
    root = re.search(r"<svg[^>]*>", fig.group(1)).group(0)
    assert 'width="' not in root and 'height="' not in root
    assert "<?xml" not in body and "<!DOCTYPE svg" not in body


def test_an_inlined_svg_cannot_restyle_the_page_or_clash_with_another(tmp_path):
    # matplotlib writes <style>*{...}</style>: inlined as is, it would restyle
    # every element of the manual. And two diagrams on one page both carry
    # id="figure_1": every id is made the diagram's own.
    md = ("{{diagram: request-flow | A}}\n\n{{diagram: deploy-units | B}}\n")
    body = build_docs.build_html(md, "T", "S", "M", tmp_path / "out.html")
    assert "<style" not in body and "<metadata" not in body
    ids = re.findall(r'\bid="([^"]+)"', body)
    assert ids and len(ids) == len(set(ids))
    assert all(i.startswith(("request-flow-", "deploy-units-")) for i in ids)
    for ref in re.findall(r'url\(#([^)]+)\)|href="#([^"]+)"', body):
        target = ref[0] or ref[1]
        assert target in ids, target


def test_the_word_build_embeds_the_png(tmp_path):
    import docx
    md = "# Title\n\n{{diagram: request-flow | One request, end to end}}\n"
    body = build_docs.build_html(md, "T", "S", "M", tmp_path / "out.html")
    out = tmp_path / "out.docx"
    build_docs.build_docx(body, "T", "S", "M", out)
    doc = docx.Document(str(out))
    assert len(doc.inline_shapes) == 1
    assert any(p.text == "One request, end to end" for p in doc.paragraphs)


def test_an_unknown_diagram_fails_the_build(tmp_path):
    with pytest.raises(ValueError, match="no-such-diagram"):
        build_docs.build_html("{{diagram: no-such-diagram | X}}\n", "T", "S", "M",
                              tmp_path / "out.html")
