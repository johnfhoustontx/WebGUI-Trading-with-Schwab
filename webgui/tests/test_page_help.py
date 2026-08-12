"""Tests for page_help — the per-page hover "idiot's guide" content."""
import inspect

import page_help


def test_every_nav_route_has_a_guide():
    """Each registered page route should have its own help guide (not the default),
    so a new page isn't silently shipped without one."""
    import main

    routes = {p for p, _, _ in main.OPTIONS_CHILDREN}
    routes |= {p for p, _, _ in main.OPTIONS_RAIL}
    routes |= {p for p, _, _ in main.SENTIMENT_CHILDREN}
    routes |= {p for p, _, _ in main.FLAT_NAV}
    routes |= {p for p, _, _ in main.MORE_CHILDREN}
    routes |= {p for p, _, _ in main.SETTINGS_CHILDREN}
    routes |= {p for p, _, _ in main.SYSTEM_RAIL}

    missing = [r for r in routes if r not in page_help.HELP_MD]
    assert not missing, f"routes without an idiot's guide: {sorted(missing)}"


def test_help_md_falls_back_for_unknown_route():
    text = page_help.help_md("/does-not-exist")
    assert text == page_help._DEFAULT
    assert text.strip()


def test_guides_are_nonempty_markdown():
    for route, md in page_help.HELP_MD.items():
        assert md.strip(), f"empty guide for {route}"
        assert "**" in md, f"guide for {route} has no bold title"


def test_gamma_guide_explains_the_0dte_close_projection():
    """The projected-close outline / Proj. flip line / hedge-pressure panel are the
    page's least self-evident overlays, and all three rest on a flat-spot assumption
    a reader must know about — so the guide has to name each and state the caveat."""
    md = page_help.help_md("/options/gamma")
    for term in ("Projected close", "Proj. flip", "hedge pressure"):
        assert term in md, f"gamma guide never explains {term!r}"
    assert "baseline, not a forecast" in md, "gamma guide omits the flat-spot caveat"


def test_projection_help_is_shared_by_the_tooltip_and_the_page():
    """The projection guide is rendered in TWO places — the nav hover tooltip and the
    Gamma page's own collapsed expander. Both must read the one constant: the hover
    tooltip is pointer-events:none and clips to the space under its nav item, so a
    page-side copy is what a reader actually reaches, and a drifting duplicate would
    leave one of the two wrong."""
    from pages.options import gamma

    assert page_help.PROJECTION_HELP_MD.strip()
    assert page_help.PROJECTION_HELP_MD in page_help.help_md("/options/gamma")

    src = inspect.getsource(gamma.render)
    assert "PROJECTION_HELP_MD" in src, (
        "the Gamma page must render the shared constant, not its own copy")


def test_subtab_help_covers_every_sub_tab():
    """Each page's sub-tabs (view/tab pickers) must all have a hover tooltip, so a
    new view can't ship without one. The tab VALUES here mirror what the pages pass
    to ``ui.tab(...)``."""
    expected = {
        "/": {"0-DTE", "Swing", "Directional"},
        "/options/gamma": {"GEX", "Charm", "DEX", "Vanna", "Flow", "Net Prem",
                           "Term", "indices", "sectors", "megacaps"},
        "/options/simulator": {"Replay", "What-if", "IV shock"},
        "/options/rescue": {"At-Risk Board", "Ad-hoc Trade"},
        "/portfolio": {"Holdings", "Sectors", "Performance"},
    }
    for route, keys in expected.items():
        have = set(page_help.SUBTAB_HELP.get(route, {}))
        missing = keys - have
        assert not missing, f"{route} sub-tabs without a tooltip: {sorted(missing)}"


def test_subtab_help_texts_are_plain_nonempty():
    """Sub-tab tooltips render as PLAIN text, so no Markdown markers, and none empty."""
    for route, tabs in page_help.SUBTAB_HELP.items():
        for key, text in tabs.items():
            assert text.strip(), f"empty sub-tab tooltip: {route} / {key}"
            assert "**" not in text, f"markdown in sub-tab tooltip: {route} / {key}"

    # subtab_help() returns "" for an unknown tab (so pages can skip empty tooltips).
    assert page_help.subtab_help("/options/gamma", "nope") == ""
    assert page_help.subtab_help("/nope", "GEX") == ""
