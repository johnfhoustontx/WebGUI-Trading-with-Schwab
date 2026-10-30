"""The public site's menu on the live screens (``webgui/site_nav.py``).

Two promises: the menu IS the site's menu (taken from blog.html, styled by the
site's own declarations), and every address in it is a page on the public site
-- never this origin's, never the app's.
"""
import html
import pathlib
import re

import pytest

import repo_paths
import site_nav

_SITE = pathlib.Path(repo_paths.SITE_ROOT)
_HOST = repo_paths.SITE_HOST


def _hrefs(markup):
    return re.findall(r'href="([^"]*)"', markup)


def test_the_menu_is_built_from_the_sites_own_page():
    nav = site_nav.site_nav_html("The Desk")
    assert nav, "the site's menu could not be read -- every other test is vacuous"
    assert 'class="ns-lockup"' in nav and 'class="ns-mark"' in nav
    assert 'class="ns-brand"' in nav


def test_every_address_is_a_page_on_the_public_site():
    hrefs = _hrefs(site_nav.site_nav_html("The Desk"))
    assert len(hrefs) >= 5, hrefs
    for href in hrefs:
        assert href.startswith(f"https://{_HOST}/"), href
        assert repo_paths.LIVE_HOST not in href, href
        assert not href.startswith(f"https://app."), href


def test_the_logo_goes_to_the_home_page():
    nav = site_nav.site_nav_html("The Desk")
    lockup = re.search(r'<a class="ns-lockup" href="([^"]*)"', nav)
    assert lockup and lockup.group(1) == f"https://{_HOST}/"


def test_every_address_resolves_to_a_page_the_site_ships():
    for href in _hrefs(site_nav.site_nav_html("The Desk")):
        page = href[len(f"https://{_HOST}/"):].partition("#")[0] or "index.html"
        assert (_SITE / page).is_file(), href


def test_the_live_desk_is_the_page_you_are_on_and_nothing_else_is():
    nav = site_nav.site_nav_html("The Desk")
    assert nav.count('aria-current="page"') == 1
    assert f'href="https://{_HOST}/live.html" aria-current="page"' in nav


def test_the_menu_is_not_pinned_over_the_screen():
    assert "ns-nav-sticky" not in site_nav.site_nav_html("The Desk")


def test_the_screen_is_named_in_the_sites_breadcrumb_escaped():
    nav = site_nav.site_nav_html("Sector & Industry")
    assert f'<span class="ns-crumb">{html.escape("Sector & Industry")}</span>' in nav
    assert nav.index("ns-crumb") < nav.index("ns-spacer"), "the name must follow the logo"
    assert "ns-crumb" not in site_nav.site_nav_html("")


@pytest.mark.parametrize("host", ["", "localhost", "evil.com/x", "a b.co", "x.co:8100"])
def test_a_bad_host_draws_no_menu(host):
    assert site_nav.site_nav_html("The Desk", site_host=host) == ""


def _page_with(tmp_path, nav):
    page = tmp_path / "blog.html"
    page.write_text(f"<html><body>{nav}</body></html>", encoding="utf-8")
    return page


_NAV = ('<nav class="ns-nav" aria-label="Site">'
        '<a class="ns-lockup" href="{first}">x</a>'
        '<span class="ns-spacer"></span>'
        '<a class="btn" href="live.html">Live Desk</a></nav>')


@pytest.mark.parametrize("href", [
    "https://app.neuralstrike.co/settings", "//evil.com/x.html", "../index.html",
    "javascript:alert(1)", "/index.html", "#top", "blog/x/index.html"])
def test_a_menu_holding_anything_but_a_plain_site_page_is_refused(tmp_path, href):
    page = _page_with(tmp_path, _NAV.replace("{first}", href))
    assert site_nav.site_nav_html("The Desk", page=page) == ""


def test_a_plain_menu_in_a_scratch_page_is_accepted(tmp_path):
    """The partner of the refusal above: without it the refusal could pass on a
    builder that refuses everything."""
    page = _page_with(tmp_path, _NAV.replace("{first}", "index.html"))
    assert site_nav.site_nav_html("The Desk", page=page)


def test_two_menus_or_none_draws_no_menu(tmp_path):
    one = _NAV.replace("{first}", "index.html")
    assert site_nav.site_nav_html("x", page=_page_with(tmp_path, one + one)) == ""
    assert site_nav.site_nav_html("x", page=_page_with(tmp_path, "<p>no menu</p>")) == ""
    assert site_nav.site_nav_html("x", page=tmp_path / "missing.html") == ""


# --- the styling is the site's own -------------------------------------------

def _rules(css):
    """``{selector: {property: value}}`` for each selector's FIRST rule."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        sel = " ".join(sel.split())
        decls = {}
        for part in body.split(";"):
            prop, sep, value = part.partition(":")
            if sep:
                decls[prop.strip()] = " ".join(value.split())
        out.setdefault(sel, decls)
    return out


_SITE_CSS = _rules((_SITE / "assets" / "site.css").read_text(encoding="utf-8"))
_NOCTURNE = _rules((_SITE / "assets" / "nocturne.css").read_text(encoding="utf-8"))
_OURS = _rules(site_nav.SITE_NAV_CSS)

_FROM_SITE = (".ns-nav", ".ns-spacer", ".ns-brand", ".ns-brand .ns-brand-b",
              ".ns-lockup", ".ns-mark", ".ns-mark-rule", ".ns-mark-fig", ".ns-crumb",
              ".ns-navlink", ".ns-navlink:hover", '.ns-navlink[aria-current="page"]',
              "a", "a:hover")
_FROM_NOCTURNE = (".btn", ".btn-primary", ".btn-primary:hover", ".btn-primary:active")


@pytest.mark.parametrize("selector", _FROM_SITE)
def test_each_menu_rule_matches_site_css(selector):
    assert _OURS[f".ns-site-bar {selector}"] == _SITE_CSS[selector], selector


@pytest.mark.parametrize("selector", _FROM_NOCTURNE)
def test_each_button_rule_matches_nocturne(selector):
    assert _OURS[f".ns-site-bar {selector}"] == _NOCTURNE[selector], selector


def test_the_tokens_are_the_sites_tokens():
    ours = {k: v for k, v in _OURS[".ns-site-bar"].items() if k.startswith("--")}
    root = _NOCTURNE[":root"]
    assert ours, "no tokens were read -- this would be vacuous"
    for name, value in ours.items():
        assert root.get(name) == value, name


def test_every_token_the_menu_uses_is_defined():
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", site_nav.SITE_NAV_CSS))
    defined = {k for k in _OURS[".ns-site-bar"] if k.startswith("--")}
    assert used <= defined, sorted(used - defined)


def test_the_menu_wraps_where_the_site_menu_wraps():
    site = (_SITE / "assets" / "site.css").read_text(encoding="utf-8")
    assert "@media (max-width: 960px)" in site
    assert "@media (max-width: 960px)" in site_nav.SITE_NAV_CSS
