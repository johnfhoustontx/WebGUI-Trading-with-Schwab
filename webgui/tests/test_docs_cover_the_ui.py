"""Every menu item, tab, sub-tab and alternate screen must appear in the manuals.

The manuals have no failing test when they go stale, which is exactly why they did:
a 2026-08-16 audit found the User Guide still documenting a queue removed in July,
six shipped pages with no entry at all, and three of four cadences wrong. This is the
mechanical guard.

**The rail and tab entries are derived from ``main.py``**, not restated here, so
adding a page to the nav fails this test until it is documented. The sub-tabs and
alternate screens are listed explicitly because they live inside page modules as
ad-hoc ``ui.tab`` / ``ui.dialog`` / ``new_tab=True`` calls with no shared registry to
read; keep them in step when you add one.
"""
import pathlib
import re

import pytest

import main

_REPO = pathlib.Path(main.__file__).resolve().parents[1]
_MANUALS = _REPO / "docs" / "manuals"

USER_GUIDE = _MANUALS / "user-guide" / "user-guide.md"
REFERENCE_GUIDE = _MANUALS / "reference-guide" / "reference-guide.md"


def _read(p):
    assert p.is_file(), f"manual missing: {p}"
    return p.read_text(encoding="utf-8")


# Both are END-USER manuals: the User Guide says how to operate a thing, the
# Reference Guide says what it is for. A user-facing surface belongs in both.
def _both():
    return {"user-guide": _read(USER_GUIDE), "reference-guide": _read(REFERENCE_GUIDE)}


def _nav_labels():
    """Every rail item + tab-strip child label, straight from main.py."""
    labels = set()
    for _label, _icon, children in main._NAV_GROUPS:
        labels.add(_label)
        labels.update(lbl for _p, lbl, _i in children)
    for _p, lbl, _i in main.OPTIONS_RAIL + main.FLAT_NAV + main.SYSTEM_RAIL:
        labels.add(lbl)
    return labels


@pytest.mark.parametrize("label", sorted(_nav_labels()))
def test_every_nav_label_is_documented(label):
    """A rail item or tab a user can click must be named in BOTH end-user manuals."""
    for name, text in _both().items():
        assert label in text, (
            f"nav item {label!r} is not mentioned anywhere in the {name}. "
            "Add it, or the page ships undocumented."
        )


# Sub-tabs, and the buttons that open a SEPARATE screen (a new browser tab or a
# dialog). These are the surfaces a reader cannot discover from the nav alone.
# (label, regex that counts as covering it)
SUBTABS_AND_SCREENS = [
    ("Scanner 0-DTE", r"0-DTE"),
    ("Scanner Directional", r"Directional"),
    ("Gamma Charm", r"Charm"),
    ("Gamma Vanna", r"Vanna"),
    ("Gamma Flow", r"\bFlow\b"),
    ("Gamma Net Prem", r"Net Prem"),
    ("Gamma Term", r"\bTerm\b"),
    ("Gamma Explain button", r"\bExplain\b"),
    ("Gamma Analyze button", r"\bAnalyze\b"),
    ("Gamma Briefings button", r"Briefings"),
    ("Simulator Replay", r"\bReplay\b"),
    ("Simulator What-if", r"What-if"),
    ("Simulator IV shock", r"IV[- ]?shock"),
    ("Portfolio Holdings", r"\bHoldings\b"),
    ("Portfolio Sectors", r"\bSectors\b"),
    ("Portfolio Performance", r"\bPerformance\b"),
    ("Rescue At-Risk Board", r"At-Risk Board"),
    ("Rescue Ad-hoc Trade", r"Ad-hoc Trade"),
    ("EOD Detailed view", r"Detailed"),
    ("Trade Deep Dive button", r"Deep Dive"),
    ("Trade AI Query button", r"AI Query"),
    ("Status Re-authorize button", r"Re-?authoriz"),
    ("Settings Appearance", r"Appearance"),
    ("Settings Vacuum action", r"Vacuum"),
    ("Driver STOP control", r"\bSTOP\b"),
]


@pytest.mark.parametrize("label,pattern", SUBTABS_AND_SCREENS,
                         ids=[lbl for lbl, _ in SUBTABS_AND_SCREENS])
def test_every_subtab_and_alternate_screen_is_documented(label, pattern):
    rx = re.compile(pattern, re.I)
    for name, text in _both().items():
        assert rx.search(text), (
            f"{label!r} is not covered in the {name}. Sub-tabs and buttons that open "
            "their own screen are invisible from the nav, so an undocumented one is "
            "effectively hidden."
        )


def test_manuals_catalog_matches_the_built_files():
    """Every manual offered in-app must exist on disk, or the link 404s."""
    from pages import manuals

    for slug, entry in manuals.MANUALS.items():
        built = _MANUALS / entry["file"]
        assert built.is_file(), (
            f"manual {slug!r} points at {entry['file']!r}, which is not built. "
            "Run docs/manuals/build_docs.py."
        )


def test_reference_guide_has_a_section_for_every_nav_page():
    """The Reference Guide's promise is per-page depth, so each page needs a heading
    of its own -- being mentioned in passing elsewhere is not coverage."""
    text = _read(REFERENCE_GUIDE)
    headings = set(re.findall(r"^##\s+(.+?)\s*$", text, re.M))
    # Group labels are chapters/sections, not pages; pages are what need headings.
    pages = set()
    for _label, _icon, children in main._NAV_GROUPS:
        pages.update(lbl for _p, lbl, _i in children)
    for _p, lbl, _i in main.OPTIONS_RAIL + main.FLAT_NAV + main.SYSTEM_RAIL:
        pages.add(lbl)

    missing = sorted(p for p in pages if p not in headings)
    assert not missing, (
        f"Reference Guide has no '## <page>' section for: {missing}. "
        "Every nav page gets its own section under the standard template."
    )
