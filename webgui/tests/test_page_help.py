"""Tests for page_help — the per-page hover "idiot's guide" content."""
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
