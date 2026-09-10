"""``tools/generate_glossary_page.py`` -- the site half of the one-source glossary.

``deploy/tests/test_site.py`` compares the term SETS of the markdown and the
page. That catches a missing or invented term, but not a changed DEFINITION:
edit a sentence in ``glossary.md``, rebuild only the manual, and the public page
keeps the old wording with every set-comparison green. Comparing the committed
page to the generator's output is what sees that.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools import generate_glossary_page as g  # noqa: E402


def _committed():
    return (g.SOURCE_PATH.read_text(encoding="utf-8"),
            g.OUTPUT_PATH.read_text(encoding="utf-8"))


def test_the_committed_page_is_what_the_generator_emits():
    md, page = _committed()
    assert g.render(md, page) == page, (
        "deploy/site/glossary.html is stale against glossary.md -- run "
        "`python tools/generate_glossary_page.py` (and docs/manuals/build_docs.py "
        "glossary for the app's copy)")


def test_a_changed_definition_is_seen():
    """The case the set comparison cannot see: same term, new words."""
    md, page = _committed()
    edited = md.replace("**Call** — The right to *buy*",
                        "**Call** — The privilege to *buy*", 1)
    assert edited != md, "fixture sentence no longer in the source"
    assert g.render(edited, page) != page
    assert "The privilege to <em>buy</em>" in g.render(edited, page)


def test_the_hand_owned_chrome_survives():
    """Only the TOC..</main> region and the count are generated."""
    md, page = _committed()
    out = g.render(md, page)
    head = page[:page.index("<body>")]
    assert out.startswith(head)
    assert out[out.index('<footer'):] == page[page.index('<footer'):]


def test_the_count_is_the_number_of_definitions():
    md, page = _committed()
    sections = g.parse(md)
    n = g.term_count(sections)
    assert f'<span class="ns-count">{n} terms</span>' in page
    assert page.count("<dt>") == n


def test_escaping_and_emphasis():
    assert g._inline("P&L *today*") == "P&amp;L <em>today</em>"
    assert g._inline("don't") == "don&#x27;t"


def test_slugs_match_the_toc_anchors():
    assert g._slug("Orders, Execution, and Risk") == "orders-execution-and-risk"
    assert g._slug("Pricing Models and Math") == "pricing-models-and-math"
