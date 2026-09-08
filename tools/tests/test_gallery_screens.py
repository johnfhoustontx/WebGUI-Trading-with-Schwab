"""``tools/gallery_screens.py`` -- the map behind the marketing gallery.

The table is pure data, so the only failures it can have are failures of
AGREEMENT, and every one of them is silent: a shot pointed at the wrong route
publishes a correct-looking screenshot of the wrong page, and a filename that
drifts from ``gallery.html`` writes a .webp nothing references while the tile it
was meant to replace keeps serving the old capture. Neither raises anything.

So what is pinned here is the agreement with the two things the table sits
between -- the gallery HTML on one side, and the app's registered routes on the
other. The route half needs ``import main``, so it lives in
``webgui/tests/test_gallery_routes.py``; see the note there for why it could not
be run from this directory.
"""
import html
import pathlib
import re

import repo_paths
from tools import gallery_screens as g

GALLERY = pathlib.Path(repo_paths.SITE_ROOT) / "gallery.html"

# The control surfaces. A screenshot of one is not a leak the way a live public
# route is, but it is still the wrong thing to put on a marketing page: it shows
# the operator's own machine controls, and the Stop All / Sign out screens say
# nothing about what the app does.
FORBIDDEN = {"/terminate", "/settings", "/status", "/login"}


# --- the shape of the table -------------------------------------------------

def test_there_are_fifteen_screens_and_twentytwo_shots():
    assert len(g.SCREENS) == 15
    assert sum(len(s.shots) for s in g.SCREENS) == 22


def test_daily_briefings_is_gone():
    """Dropped by decision 2026-09-08; its two shots go with it."""
    assert not any("Briefing" in s.title for s in g.SCREENS)


def test_no_screen_captures_a_control_surface():
    paths = {sh.route.split("?")[0] for s in g.SCREENS for sh in s.shots}
    assert paths & FORBIDDEN == set()


def test_every_shot_writes_its_own_file():
    """The filenames ARE the join to the HTML, so a duplicate is a lost shot.

    Two shots naming ``image16`` would capture twice, write once, and leave one
    tile showing a page it never claimed to show -- with the run reporting 22
    captures either way.
    """
    names = [sh.image for s in g.SCREENS for sh in s.shots]
    assert len(names) == len(set(names))
    # Not "exactly image1..image22": the run is incidental, and pinning it would
    # fail a future sixteenth screen that is perfectly correct. What is NOT
    # incidental is that the two dropped files stay dropped.
    assert {"image23", "image24"}.isdisjoint(names)


# --- agreement with gallery.html --------------------------------------------

def _gallery_sections():
    """``[(title, [image stem, ...]), ...]`` read out of the shipped HTML.

    A regex rather than a parser: the file is generated, the two shapes below
    are stable, and adding a BeautifulSoup import for it would make this test
    depend on a package that is in the venv only as one of ``yfinance``'s
    orphans -- see the requirements.lock note in CLAUDE.md.
    """
    text = GALLERY.read_text(encoding="utf-8")
    parts = text.split('<main class="ns-viewer">', 1)
    assert len(parts) == 2, f"{GALLERY} no longer opens the viewer the way this test reads it"
    out = []
    for block in parts[1].split('<section class="ns-screen')[1:]:
        title = re.search(r"<h2>(.*?)</h2>", block, re.S)
        assert title, "a gallery section has no <h2> to join on"
        shots = re.findall(r"assets/shots/(image\d+)\.webp", block)
        out.append((html.unescape(title.group(1)).strip(), shots))
    assert out, f"{GALLERY} parsed to no sections at all"
    return out


def test_the_table_matches_the_gallery_it_feeds():
    """Same titles, same files, SAME ORDER -- the order is the caption join.

    ``gallery.html`` pairs each figure with a tab label by POSITION, so a table
    whose shots are correct but reordered captions every one of them wrongly.
    Screen 12's files really do run image14, 15, 17, 16, 18 in the HTML today;
    that is preserved rather than tidied, because tidying it here would silently
    swap two captions on the site.
    """
    sections = dict(_gallery_sections())
    for s in g.SCREENS:
        assert s.title in sections, f"{s.title!r} has no section in gallery.html"
        assert [sh.image for sh in s.shots] == sections[s.title], \
            f"{s.title}: shot order disagrees with gallery.html"


def test_the_only_section_the_table_drops_is_daily_briefings():
    """Guards the CONVERSE of the test above, which containment cannot see.

    A screen deleted from the table by accident leaves a tile on the site whose
    capture is never refreshed again -- so it keeps the old branding forever,
    which is the exact defect this whole exercise exists to fix.
    """
    in_html = [t for t, _ in _gallery_sections()]
    dropped = [t for t in in_html if t not in {s.title for s in g.SCREENS}]
    assert dropped == ["Daily Briefings"]
