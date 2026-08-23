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


_SEP = re.compile(r"^[\s/·,;:|_*`—–\-]+$")


def _prose_words(s):
    return [w for w in re.split(r"\s+", s.strip()) if w and not _SEP.match(w)]


def _logical_lines(text):
    """Markdown here is HARD-WRAPPED, so a term can sit near a line end with its
    explanation continuing on the next. Join each block (paragraph / list item /
    table row) into one logical line before looking at it."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        for chunk in re.split(r"\n(?=\s*(?:[-*]\s|\|))", block):
            out.append(" ".join(chunk.split()))
    return out


def documents(text, term):
    """True when `term` is EXPLAINED, not merely present.

    ⚠ The obvious check -- ``term in text`` -- is what this replaces, and it is not
    good enough. It passed the User Guide on Dealer Positioning's **Flow** sub-tab
    because the token appeared in the unrelated page name "Flow Alerts", and it
    passed **Net Prem** because the view was listed in a bare toggle line
    ``Gamma / Charm / Delta / Vanna / Flow / Net Prem / Term`` that explained none
    of them. Both shipped undocumented behind a green test.

    The discriminator is whether the term is followed by a SENTENCE or sits inside a
    run of peer names.
    """
    t = re.escape(term)
    if re.search(rf"^#{{1,6}}\s.*\b{t}\b", text, re.M | re.I):          # own heading
        return True
    if re.search(rf"^\|\s*\**{t}\**\s*\|", text, re.M | re.I):          # table row label
        return True
    if re.search(rf"^\s*[-*]?\s*\**{t}\**\s*:?\**\s*$", text, re.M | re.I):
        return True                                                     # label line
    for line in _logical_lines(text):
        m = re.search(rf"\b{t}\b", line, re.I)
        if m and (len(_prose_words(line[m.end():])) >= 5
                  or len(_prose_words(line[:m.start()])) >= 8):
            return True
    return False


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
        assert documents(text, label), (
            f"nav item {label!r} is not EXPLAINED in the {name} (a bare mention in a "
            "list of names does not count). Add it, or the page ships undocumented."
        )


def _section(text, page):
    """The body of the ``## <page>`` section, up to the next same-or-higher heading.

    Sub-tab coverage MUST be scoped to its own page. Searching the whole document
    cannot tell "Net Prem is explained as a Dealer Positioning view" apart from "the
    words Net Prem appear in the Market Dashboard tile list", and "Flow" matches the
    unrelated Flow Alerts page on every line. Both of those false passes let real
    gaps ship.
    """
    m = re.search(rf"^##\s+{re.escape(page)}\s*$", text, re.M)
    if not m:
        return None
    nxt = re.search(r"^#{1,2}\s+\S", text[m.end():], re.M)
    return text[m.end():m.end() + nxt.start()] if nxt else text[m.end():]


# (owning page section, label for the test id, the term that section must EXPLAIN)
SUBTABS_AND_SCREENS = [
    ("Market Scanner", "Scanner 0-DTE", "0-DTE"),
    ("Market Scanner", "Scanner Swing", "Swing"),
    ("Market Scanner", "Scanner Directional", "Directional"),
    ("Dealer Positioning", "Gamma view", "Gamma"),
    ("Dealer Positioning", "Charm view", "Charm"),
    ("Dealer Positioning", "Delta view", "Delta"),
    ("Dealer Positioning", "Vanna view", "Vanna"),
    ("Dealer Positioning", "Flow view", "Flow"),
    ("Dealer Positioning", "Net Prem view", "Net Prem"),
    ("Dealer Positioning", "Term view", "Term"),
    ("Dealer Positioning", "Explain button", "Explain"),
    ("Dealer Positioning", "Analyze button", "Analyze"),
    ("Dealer Positioning", "Briefings button", "Briefings"),
    ("Simulator", "Replay", "Replay"),
    ("Simulator", "What-if", "What-if"),
    ("Simulator", "IV shock", "IV shock"),
    ("Portfolio", "Holdings", "Holdings"),
    ("Portfolio", "Sectors", "Sectors"),
    ("Portfolio", "Performance", "Performance"),
    ("Rescue", "At-Risk Board", "At-Risk Board"),
    ("Rescue", "Ad-hoc Trade", "Ad-hoc Trade"),
    ("Rescue", "Apply", "Apply"),
    ("EOD Report", "Detailed view", "Detailed"),
    ("EOD Report", "Generate button", "Generate"),
    # Both report buttons moved into the Signal Desk COMMAND BAR, which is
    # shared by all four screens; Overview is where the manuals describe it.
    ("Overview", "Deep Dive button", "Deep Dive"),
    ("Overview", "AI Query button", "AI Query"),
    ("System Status", "Re-authorize button", "Re-authorize"),
    ("System Status", "Restart button", "Restart"),
    ("Settings", "Appearance editor", "Appearance"),
    ("Settings", "Vacuum action", "Vacuum"),
    ("Claude Trades", "STOP control", "STOP"),
    ("Claude Trades", "Run now control", "Run now"),
    ("Paper Account", "Reset dialog", "Reset"),
    ("Captured Signals", "Refresh marks", "Refresh marks"),
]


@pytest.mark.parametrize(
    "page,label,term", SUBTABS_AND_SCREENS,
    ids=[f"{pg}:{lbl}" for pg, lbl, _t in SUBTABS_AND_SCREENS])
def test_every_subtab_and_alternate_screen_is_documented(page, label, term):
    """Each sub-tab / alternate screen is explained INSIDE its own page's section."""
    for name, text in _both().items():
        body = _section(text, page)
        assert body is not None, f"{name} has no '## {page}' section"
        assert documents(body, term), (
            f"{label!r} is not explained in the {name}'s '{page}' section. "
            "A mention elsewhere in the manual does not count -- a reader on that "
            "page needs it there."
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


def _rail_page_order():
    """Page labels in the order the rail presents them (NAV_SECTIONS, then SYSTEM)."""
    order = []
    for _caption, entries in main.NAV_SECTIONS:
        for entry in entries:
            if entry[0] == "group":
                order.extend(lbl for _p, lbl, _i in entry[3])
            else:
                order.append(entry[2])
    order.extend(lbl for _p, lbl, _i in main.SYSTEM_RAIL)
    return order


@pytest.mark.parametrize("manual", ["user-guide", "reference-guide"])
def test_end_user_manuals_follow_the_rail_order(manual):
    """Both end-user manuals present pages in the order the MENU presents them.

    A manual whose chapters disagree with the rail makes a reader translate on every
    lookup. The rail was reorganised into captioned sections on 2026-08-16 and the
    User Guide kept its old Options/Trade/Reports chapters for a while, which is the
    drift this pins.
    """
    path = USER_GUIDE if manual == "user-guide" else REFERENCE_GUIDE
    headings = re.findall(r"^##\s+(.+?)\s*$", _read(path), re.M)
    rail = _rail_page_order()
    # Where a page has its own section, its position must not regress relative to
    # the other pages. Compare the two sequences restricted to shared members.
    seen, doc_order = set(), []
    for h in headings:
        name = "Trade Analyzer" if h == "Trade" else h
        if name in rail and name not in seen:
            seen.add(name)
            doc_order.append(name)
    expected = [p for p in rail if p in seen]
    assert doc_order == expected, (
        f"{manual} presents pages in a different order than the rail.\n"
        f"  manual: {doc_order}\n  rail:   {expected}"
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
