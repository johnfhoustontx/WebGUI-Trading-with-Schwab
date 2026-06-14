"""Tests for html_render — styled HTML for Key Levels and the Explain popup."""
import html_render as hr


# ── google_url ──

def test_google_url_quotes_query():
    url = hr.google_url("gamma flip")
    assert url.startswith("https://www.google.com/search?q=")
    assert "gamma+flip" in url


# ── linkify ──

def test_linkify_links_first_occurrence_only():
    html = "<p>The gamma flip matters. Past the gamma flip it changes.</p>"
    out = hr.linkify(html, [("gamma flip", "options gamma flip")])
    assert out.count("<a ") == 1
    assert "google.com/search" in out


def test_linkify_preserves_original_case():
    out = hr.linkify("<p>GEX here</p>", [("gex", "gamma exposure")])
    # The visible anchor text keeps the original casing from the document.
    assert ">GEX</a>" in out


def test_linkify_skips_inside_existing_anchor():
    html = '<p><a href="x">gamma flip</a> and gamma flip</p>'
    out = hr.linkify(html, [("gamma flip", "q")])
    # Only the second (outside-anchor) occurrence gets linked.
    assert out.count("google.com/search") == 1


def test_linkify_does_not_corrupt_injected_href():
    # 'flip' would match inside the previous term's query text if we rescanned
    # the injected anchor. Ensure a later term does not match inside an href.
    html = "<p>gamma flip then flip</p>"
    glossary = [("gamma flip", "gamma flip dealer"), ("flip", "flip standalone")]
    out = hr.linkify(html, glossary)
    # Both terms link once; neither href is broken (each anchor closes cleanly).
    assert out.count("<a ") == 2
    assert "<a <a" not in out


def test_linkify_longer_term_wins_tie():
    out = hr.linkify("<p>call wall ahead</p>",
                     [("call", "c"), ("call wall", "cw")])
    assert ">call wall</a>" in out


# ── wrap_page ──

def test_wrap_page_is_full_document():
    page = hr.wrap_page("My Title", "<p>hello</p>")
    assert page.lstrip().startswith("<!DOCTYPE html>")
    assert "<style>" in page
    assert "My Title" in page
    assert "<p>hello</p>" in page


# ── markdown_to_html ──

def test_markdown_headers_and_tables():
    md = "# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    out = hr.markdown_to_html(md)
    assert "<h1>" in out
    assert "<table>" in out


# ── explain_to_html ──

def test_explain_to_html_sections_bullets_and_hr():
    text = (
        "━━ WHAT YOU'RE LOOKING AT ━━\n"
        "Some intro line.\n"
        "\n"
        "• first bullet\n"
        "• second bullet\n"
        "\n"
        "━━━\n"
        "VIX 14 · Sentiment n/a\n"
    )
    out = hr.explain_to_html(text)
    # The apostrophe in the title is HTML-escaped (correct behaviour).
    assert "LOOKING AT</h2>" in out and "<h2>WHAT YOU" in out
    assert "<li>first bullet</li>" in out
    assert "<hr>" in out
    assert "<p>Some intro line.</p>" in out


def test_explain_to_html_escapes_html():
    out = hr.explain_to_html("A <tag> & more\n")
    assert "&lt;tag&gt;" in out and "&amp;" in out


# ── integration ──

def test_render_key_levels_full_page_with_links():
    md = "# Key Levels\n\nThe gamma flip is the pivot.\n"
    page = hr.render_key_levels_html(md)
    assert "<!DOCTYPE html>" in page
    assert "google.com/search" in page


def test_explain_to_html_subheading():
    out = hr.explain_to_html("── Right now ──\nspot above flip.\n")
    assert "<h3>Right now</h3>" in out


def test_render_explain_full_page_with_links():
    text = "━━ WHAT YOU'RE LOOKING AT ━━\nNet dealer gamma and the gamma flip.\n"
    page = hr.render_explain_html(text, symbol="$SPX")
    assert "<!DOCTYPE html>" in page
    assert "google.com/search" in page
    assert "$SPX" in page  # symbol in title/heading


# ── Dealer Pinch section (folded into the Explain page) ──

def _pinch():
    return {
        "symbol": "$SPX", "armed": True, "confidence": 72.0, "regime": "PIN",
        "conditions": {"c1": True, "c2": True, "c3a": True, "c3b": True},
        "node": {"strike": 5800.0, "dist_pts": 1.0, "dist_pct": 0.0002},
        "node_dominance": 0.41, "secondary_node": 5750.0, "pin_risk": 0.8,
        "iv_pctile": 85.0, "rv_trend": {"value": 11.0, "falling": True},
        "forced_hedge_dir": "down",
        "levels": {"pin_target": 5800.0, "break_trigger": 5781.0,
                   "invalidation": "IV %ile < 60, or spot > 1% from node"},
        "time_to_resolve": {"dte": 2, "hours_to_close": 3.0},
        "playbook": "PIN: fade the edges and sell premium centered on the node.",
        "reason": "All 4 conditions met — pinch armed.",
    }


def test_pinch_section_html_is_fragment():
    frag = hr.pinch_section_html(_pinch())
    assert "<!DOCTYPE html>" not in frag      # fragment, not a full page
    assert "PIN" in frag and "5,800" in frag
    assert "DTE" in frag                       # checklist
    assert "Playbook" in frag


def test_pinch_section_html_none_is_empty():
    assert hr.pinch_section_html(None) == ""


def test_render_explain_shows_pinch_placeholder_when_no_state():
    # Even with no pinch state (e.g. before the first fetch), the page must
    # still carry a Dealer Pinch section explaining why it's empty.
    page = hr.render_explain_html("━━ GAMMA EXPOSURE (GEX) ━━\nx.\n",
                                  pinch_state=None, symbol="$SPX")
    assert "Dealer Pinch" in page
    assert "no pinch data" in page.lower() or "waiting" in page.lower()


def test_render_explain_includes_pinch_at_top():
    text = "━━ GAMMA EXPOSURE (GEX) ━━\nNet dealer gamma.\n"
    page = hr.render_explain_html(text, pinch_state=_pinch(), symbol="$SPX")
    assert "<!DOCTYPE html>" in page
    assert "Dealer Pinch" in page
    assert "PIN" in page and "5,800" in page
    # pinch section precedes the GEX narrative
    assert page.index("Dealer Pinch") < page.index("GAMMA EXPOSURE")
    # narrative still present
    assert "Net dealer gamma" in page
