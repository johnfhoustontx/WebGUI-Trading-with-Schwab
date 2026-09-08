"""The fifteen screens the marketing gallery shows, and the route behind each shot.

PURE DATA -- no imports beyond ``dataclasses`` -- so the capture tool, the
gallery HTML and the tests all read one source and cannot disagree about which
page a tile is showing.

⚠ THIS IS THE PRIVATE APP, NOT THE PUBLIC ONE. ``webgui/live_screens.py`` is the
sibling table for ``live.neuralstrike.co``; it looks similar and answers a
different question. Reusing its captures for the gallery was considered and
rejected: the gallery's whole subject is the APPLICATION, and the public shell
renders no rail, no header and no breadcrumb by design, so a gallery rebuilt
from those shots would carry no brand mark anywhere. These routes are served on
``127.0.0.1:8500`` behind the login, captured with a locally-minted session
cookie, and nothing here is ever published as a route.

Adding an entry means a tile on ``neuralstrike.co/gallery.html``, so it is a
marketing decision as much as a technical one -- and the failure mode is quiet.
A shot pointed at the wrong route publishes a real, correct-looking screenshot
of the wrong page; a filename that drifts from the HTML writes a .webp nothing
references while the stale tile keeps serving. Neither raises. Both are pinned:
``tools/tests/test_gallery_screens.py`` holds the table against the HTML, and
``webgui/tests/test_gallery_routes.py`` holds it against the app's registered
routes (from there, because it needs ``import main``).

TWO WAYS A SHOT NAMES A VIEW WITHIN A PAGE, because the app really does have two:

* a QUERY STRING, for a page whose ``@_page`` function takes the pin as a
  parameter. ``/sentiment/momentum?level=stock`` is the shipped precedent; the
  three ``/options/gamma?view=`` routes below are the same shape and are
  INTENDED rather than built -- ``gamma.render()`` already accepts ``view``, and
  wiring the route parameter is a later task. The route test strips the query
  before checking registration, so recording it costs nothing today.
* a ``subtab`` label, for a page where the view lives in page state and the
  capture has to click. ``simulator.render()`` takes no arguments at all, so its
  three shots are one route told apart by nothing else. Deliberately NOT written
  as an invented ``?tab=``: that would assert a URL contract nobody has agreed
  to build, where the label only states what the screenshot shows.

ORDER IS LOAD-BEARING TWICE. ``SCREENS`` order is the gallery's rail order, and
a screen's ``shots`` order pairs each figure with its tab caption BY POSITION --
so re-sorting shots silently re-captions them. Screen 12's files run 14, 15, 17,
16, 18; that is what the HTML has, and it is preserved rather than tidied.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Shot:
    """One image in the gallery.

    ``image`` is the output filename stem under ``deploy/site/assets/shots``.
    The existing ``imageN`` names are kept -- they carry no meaning, and
    renaming them would rewrite every ``<img src>`` in ``gallery.html`` for a
    recapture that is not supposed to change the page at all.
    """

    image: str            # filename stem: assets/shots/<image>.webp
    route: str            # the PRIVATE app route, query string included
    subtab: str = ""      # a view the route cannot name; the capture clicks it


@dataclass(frozen=True)
class Screen:
    """One gallery section: a title and the shots filed under it.

    ⚠ ``title`` is the join to ``gallery.html``'s ``<h2>`` and is matched
    exactly, so it is copy rather than an identifier -- "Strategy Calculator"
    below covers three different pages (Calculator, Expected Move, Simulator)
    because that is how the site groups them for a reader, not because they
    share a route.
    """

    title: str
    shots: tuple[Shot, ...]


SCREENS = (
    Screen("The Desk", (
        Shot("image1", "/desk"),
    )),
    # The same page three times, told apart by its view picker. ``?view=GEX`` is
    # spelled out rather than left to the default so all three read alike and
    # none depends on what the page happened to be showing last.
    #
    # ⚠ NO SYMBOL IS PINNED, and the shipped captures are not all one symbol --
    # image2's picker reads SPY, image3's Premium Divergence header $SPX (Net
    # Prem is a symbol GROUP and has no single one). A recapture will use
    # whatever the page defaults to, which is a difference in the PICTURE and
    # not in which page it is. If the gallery wants a specific symbol, that is a
    # pin here plus the same @_page work the ``view`` pins are waiting on.
    Screen("Gamma Heatmap", (
        Shot("image2", "/options/gamma?view=GEX"),
    )),
    Screen("Premium Divergence", (
        Shot("image3", "/options/gamma?view=Flow"),
    )),
    Screen("Net Options Premium", (
        Shot("image4", "/options/gamma?view=Net+Prem"),
    )),
    Screen("Opportunity Board", (
        Shot("image5", "/options/matrix"),
    )),
    Screen("Flow Alerts", (
        Shot("image6", "/options/flow"),
    )),
    Screen("Macro Board", (
        Shot("image7", "/market"),
    )),
    Screen("Market Regime Control", (
        Shot("image8", "/sentiment"),
    )),
    # ⚠ NOT a /sentiment variant. Read off the shipped capture's breadcrumb --
    # "Markets > Trend & Sentiment > Bull / Bear Map" -- after the design doc
    # had assumed otherwise. The assumption would have published the Sentiment
    # console under a Bull/Bear caption and looked entirely fine.
    Screen("Where the Market Stands", (
        Shot("image9", "/sentiment/bullbear"),
    )),
    Screen("Sector & Industry Performance", (
        Shot("image10", "/sentiment/sectors"),
    )),
    Screen("Sector Rotation", (
        Shot("image11", "/sentiment/rotation"),
        Shot("image12", "/sentiment/rrg"),
    )),
    Screen("Momentum", (
        Shot("image13", "/sentiment/momentum"),
    )),
    # ⚠ ONE CAPTION, THREE PAGES, and the order is the HTML's (17 before 16).
    # Mapped from the breadcrumbs in the shipped captures rather than from the
    # tab labels above them, and one of the five does not survive that: the
    # figure captioned "What-if: over time" is the Simulator's REPLAY tab -- the
    # six-panel Price/Delta/Gamma/Theta/Vega/Rho stack, titled "Replay" in the
    # shot itself. Its caption would have sent a capture to the What-if tab.
    Screen("Strategy Calculator", (
        Shot("image14", "/options/calculator"),
        Shot("image15", "/options/expected-move"),
        Shot("image17", "/options/simulator", subtab="IV shock"),
        Shot("image16", "/options/simulator", subtab="What-if"),
        Shot("image18", "/options/simulator", subtab="Replay"),
    )),
    Screen("Strategy Finder", (
        Shot("image19", "/options/swing"),
    )),
    # The Trade Analyzer's fourth tab, Rank Board (/trade/board), is not in the
    # gallery and is not an omission to correct here -- adding a shot means
    # adding a figure and a tab caption to gallery.html in the same change.
    Screen("Stock Evaluation", (
        Shot("image20", "/trade"),
        Shot("image21", "/trade/evidence"),
        Shot("image22", "/trade/plan"),
    )),
)

# Daily Briefings (image23, image24) was the sixteenth section and was DROPPED by
# decision on 2026-09-08. Recorded here rather than left as an absence, because
# "the table is missing a screen the site still shows" and "the site still shows
# a screen the table deliberately dropped" look identical from the table alone.
