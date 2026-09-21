"""Tests for the User Manuals page (webgui/pages/manuals.py).

There was no test module for this page before the Phase 6 kit migration: the
only coverage anywhere was ``test_docs_cover_the_ui.py``'s catalog-to-built-file
check and ``shared/tests/test_cross_tier_mirrors.py``'s dual-registration pin.
Both are about the CATALOG; nothing looked at the page.

⚠ ``MANUALS`` is also ``main.py``'s serving whitelist, so the catalog tests here
are must-not-change guards rather than migration tests - they are green on both
sides of the migration deliberately, and are labelled where that is true.
"""
import inspect

import page_help
from pages import manuals
from pages.options import theme


# ── the catalog: a must-not-change guard, green before and after ────────────
def test_the_catalog_is_the_serving_whitelist_and_keeps_its_shape():
    """``main.manual_file`` looks a query parameter up in this dict and refuses
    anything else, so a restructure here is a routing change. Every entry needs
    all four keys, and the icons must stay distinct - the cards are told apart
    by their icon."""
    assert list(manuals.MANUALS) == ["user-guide", "reference-guide",
                                     "technical-reference", "api-reference",
                                     "glossary"]
    for slug, m in manuals.MANUALS.items():
        assert set(m) == {"title", "desc", "icon", "file"}, slug
        assert m["file"].endswith(".html"), slug
    icons = [m["icon"] for m in manuals.MANUALS.values()]
    assert len(set(icons)) == len(icons), "two manuals share an icon"


# ── the frame ───────────────────────────────────────────────────────────────
def test_the_frame_is_the_kit_and_carries_no_surface_of_its_own():
    src = inspect.getsource(manuals.render)
    assert 'kit.page(width="form")' in src
    assert 'kit.header("User Manuals")' in src
    # The page's own headline, width cap and opacity dimming are the kit's now.
    for token in ("text-h5", "max-w-2xl", "opacity-", "BTN_3D"):
        assert token not in src, f"{token} is the page styling itself"


def test_the_page_builds_no_button_of_its_own():
    """The guard's ``manuals.py`` entry is deleted in the same commit, so this
    is the page-level half of it."""
    assert "ui.button(" not in inspect.getsource(manuals)


# ── the rendered page ───────────────────────────────────────────────────────
def _render():
    from nicegui import ui
    with ui.card() as host:
        manuals.render()
    return host


def _labels(host):
    from nicegui import ui
    return [e for e in host.descendants() if isinstance(e, ui.label)]


def _buttons(host):
    from nicegui import ui
    return [e for e in host.descendants() if isinstance(e, ui.button)]


def test_every_manual_gets_a_card_with_its_title_and_description():
    host = _render()
    texts = {lbl.text for lbl in _labels(host)}
    for m in manuals.MANUALS.values():
        assert m["title"] in texts, m["title"]
        assert m["desc"] in texts, m["title"]
    cards = [e for e in host.descendants()
             if theme.CARD.split()[0] in " ".join(e.classes)]
    assert len(cards) == len(manuals.MANUALS), \
        "one app card per manual, and nothing else wearing the card token"


def test_every_open_button_is_the_kits_secondary():
    host = _render()
    opens = [b for b in _buttons(host) if b.text == "Open"]
    assert len(opens) == len(manuals.MANUALS)
    want = theme.BTN.split()
    for b in opens:
        assert set(want) <= set(b.classes), "an Open button is not kit secondary"
        assert b._props.get("no-caps") is True, "the kit's props did not apply"
        assert b._props.get("icon") == "open_in_new"


def test_each_open_button_opens_its_OWN_manual():
    """The per-row default argument is what binds the slug. Without it every
    card would open the last manual in the dict, which reads as a broken page
    rather than as a styling change."""
    from nicegui import ui
    from nicegui.events import GenericEventArguments
    host = _render()
    went = []
    real = ui.navigate.to
    ui.navigate.to = lambda target, *a, **kw: went.append(target)
    try:
        for b in [b for b in _buttons(host) if b.text == "Open"]:
            for li in list(b._event_listeners.values()):
                if li.type.split(".")[0] == "click" and li.handler is not None:
                    li.handler(GenericEventArguments(sender=b, client=b.client,
                                                     args=None))
    finally:
        ui.navigate.to = real
    assert went == [f"/manuals/file?name={slug}" for slug in manuals.MANUALS]


# ── the hover help ──────────────────────────────────────────────────────────
def test_the_hover_help_names_every_manual_the_page_offers():
    """``page_help`` listed FOUR manuals while ``MANUALS`` has carried five
    since the Options Glossary was added - so the one manual a reader is most
    likely to need explained was the one the help did not mention."""
    text = page_help.HELP_MD["/manuals"]
    for m in manuals.MANUALS.values():
        assert m["title"] in text, f"{m['title']} is on the page but not in the help"


def test_the_description_line_moved_from_the_page_into_the_hover_help():
    """The header line carries no description sentence (the standard); the
    .docx note is real information, so it lands in the help rather than going.

    Asked of the RENDERED labels, not the source: the page's own docstring is
    free to explain the page, and a source grep would only forbid that."""
    host = _render()
    drawn = " ".join(lbl.text or "" for lbl in _labels(host))
    assert "docx" not in drawn.lower()
    assert "new browser tab" not in drawn
    text = page_help.HELP_MD["/manuals"]
    assert ".docx" in text
    assert "docs/manuals/" in text
