"""Widget + wiring tests for the Bull / Bear Map page (/sentiment/bullbear).

The display arithmetic is pinned next door in ``test_bullbear.py``; what is left
here is what only a page can get wrong — the nav touchpoints, the unit the two
score axes render in, which element repaints when, and the cold-cache state that
``bullbear.headline`` deliberately refuses to invent.
"""
import ast
import inspect
import pathlib
import re

from nicegui import ui
from nicegui.elements.expansion import Expansion

from pages import bullbear as B
from pages import sentiment_bullbear as P
from pages import ui_kit as kit
from pages.options import theme
from pages.rotation_view import TONE

ROUTE = "/sentiment/bullbear"

# The faint end of the app's text ladder — the colour ``kit.EYEBROW`` wears, and
# what replaced the page-scoped ``NT["ghost"]`` when the neutral ladder went.
FAINT = f"text-[{theme.THEME['palette']['icon']}]"


# ── fixtures ─────────────────────────────────────────────────────────────────
def _row(symbol, label, trend, excess, day=None, part=None, **extra):
    return {"symbol": symbol, "label": label, "day_pct": day,
            "participation": part, "raw": {"trend": trend, "excess": excess},
            **extra}


def _payload(**over):
    """A three-level payload shaped like ``compute.bullbear_view``'s output.

    Deliberately carries the two structural oddities the live payload has: an
    industry with no admitted member stock (3 of 69 on 2026-08-19) and a stock
    naming an industry that was never scored (an orphan, 10 of 296 that day).
    """
    out = {
        "session_date": "2026-08-19",
        "computed_at": "2026-08-19T16:21:04-05:00",
        "quoted_at": "2026-08-20T10:15:32-05:00",
        "regime": {"state": "favorable", "label": "Risk-on regime"},
        "levels": {
            "sector": [
                _row("XLE", "Energy", 0.004, 0.0122, day=0.41, part=0.96),
                _row("XLRE", "Real Estate", 0.044, -0.0189, day=-0.22, part=0.23),
                _row("XLU", "Utilities", None, None, day=None, part=None)],
            "industry": [
                _row("XOP", "Oil & Gas E&P", 0.02, 0.01, day=1.0, part=0.5,
                     sector="Energy"),
                _row("OIH", "Oil Services", -0.02, 0.01, day=None, part=0.0,
                     sector="Energy")],
            "stock": [
                _row("XOM", "XOM", 0.03, 0.02, day=0.5, sector="Energy",
                     industry="Oil & Gas E&P"),
                _row("SLB", "SLB", -0.01, -0.02, day=-0.3, sector="Energy",
                     industry="Never scored")],
        },
    }
    out.update(over)
    return out


def _render(monkeypatch, payload):
    """Build the page against the auto-index client; return the new elements."""
    import bus_client
    monkeypatch.setattr(bus_client, "read_full",
                        lambda _v: (payload, 1 if payload else None))
    monkeypatch.setattr(bus_client, "read_version",
                        lambda _v: 1 if payload else None)
    before = set(ui.context.client.elements)
    with ui.card():
        P.render()
    return [e for k, e in ui.context.client.elements.items() if k not in before]


def _texts(elements):
    return [t for t in (getattr(e, "text", None) for e in elements) if t]


def _panels(elements):
    """Expansions keyed by their label prop — the row's identity for a test."""
    return {e._props.get("label"): e for e in elements
            if isinstance(e, Expansion)}


def _open(elements, label):
    """Open one panel and return everything it built."""
    before = set(ui.context.client.elements)
    _panels(elements)[label].value = True
    return [e for k, e in ui.context.client.elements.items() if k not in before]


# ── nav touchpoints ──────────────────────────────────────────────────────────
def test_bullbear_is_the_third_trend_and_sentiment_tab():
    """Third: the "where" that follows the what. Index, not membership — the tab
    strip renders in list order, so a correct-but-appended tab is still wrong."""
    import main
    routes = [r for r, _label, _icon in main.SENTIMENT_CHILDREN]
    assert routes.index(ROUTE) == 2


def test_bullbear_has_a_favicon_colour_no_other_page_uses():
    """``_TAB_COLOR.get(active, "#42a5f5")`` defaults to the Market Scanner's own
    blue, so an unmapped route ships a favicon colliding with a real page's."""
    import main
    mine = main._TAB_COLOR[ROUTE]
    others = [r for r, c in main._TAB_COLOR.items() if c == mine and r != ROUTE]
    assert not others, f"{ROUTE} shares its favicon colour with {others}"


def test_the_page_imports_nothing_below_tier_one():
    """No engine, no proxy, no ``sys.path`` glue — the source of the documented
    cross-app ``scoring`` collision. Read off the import statements rather than
    the source text, because the prose here cites the upstream modules by name
    and a substring ban would forbid saying where a fact came from."""
    tree = ast.parse(inspect.getsource(P))
    got = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    got |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
            for a in n.names}
    # ``shell`` is the page-to-shell seam and is itself Tier 1 — it imports
    # nothing but ``nicegui`` and ``pages.ui_guard``, pinned by
    # ``test_shell_seam.test_the_shell_stays_a_leaf_module``.
    # ``pages.ui_kit`` is the page kit and is itself Tier 1 — its own docstring
    # states the allow-list it keeps (nicegui, the theme, busy, the Symbol-field
    # helpers, bus_client and shell), which is why the public live process can
    # render a page built from it. ``pages.options`` is the same theme module the
    # narrower ``pages.options.theme`` entry beside it already names, reached by
    # the import form the three sibling rotation screens use; it is narrower than
    # the bare ``pages`` this set has always allowed. The intent is unchanged: no
    # engine, no proxy, no ``sys.path`` glue.
    assert got <= {"datetime", "time", "bus_client", "nicegui", "pages", "shell",
                   "pages.options", "pages.options.theme", "pages.rotation_view",
                   "pages.ui_guard", "pages.ui_kit"}


# ── the two clocks ───────────────────────────────────────────────────────────
def test_the_clocks_date_the_scores_and_the_quotes_separately():
    """Two clocks because they move on different schedules: the cascade runs at
    16:20 CT, the quote batch every ~30 s."""
    scores, quotes = P.clocks(_payload())
    assert scores == "Scores as of 2026-08-19"
    assert quotes == "Quotes 10:15:32"


def test_a_failed_quote_call_is_named_as_such_not_as_a_stale_page():
    """``bullbear_view`` leaves ``quoted_at`` None when the quote call raised and
    publishes the tree anyway — so the cost is one column, and the line must not
    imply the scores or quadrants are affected."""
    _scores, quotes = P.clocks(_payload(quoted_at=None))
    assert "day-move" in quotes and "unaffected" in quotes
    assert "Quotes " not in quotes


def test_an_unparseable_quote_stamp_renders_verbatim_rather_than_vanishing():
    """Showing what was published beats claiming there was nothing."""
    _scores, quotes = P.clocks(_payload(quoted_at="not-a-timestamp"))
    assert quotes == "Quotes not-a-timestamp"


def test_a_cold_payload_says_the_scores_are_missing_and_the_quotes_with_them():
    scores, quotes = P.clocks(None)
    assert scores == P.NO_SCORES
    assert quotes == P.NO_QUOTES


# ── the headline and the count strip ─────────────────────────────────────────
def test_the_page_pluralises_the_noun_because_headline_will_not():
    """``B.headline`` renders ``noun`` verbatim and would emit "1 of 1 sectors"."""
    one = [_row("XLE", "Energy", 0.1, 0.1)]
    assert P.headline_line(one) == "1 of 1 sector rising and leading"
    assert P.headline_line(one * 2).endswith("2 sectors rising and leading")


def test_the_headline_is_empty_on_a_cold_payload_and_the_page_explains_instead():
    """The suppression is deliberate upstream — "0 of 0 sectors rising and
    leading" reads as a maximally bearish tape when nothing was published — so
    the page owes the reader the reason in that slot."""
    assert P.headline_line([]) == ""
    # Not the word "Waiting" — the page was reworded to say what is TRUE
    # rather than which service is cold. What must survive is the ACTIONABLE
    # half: this map comes from a nightly cascade, so the answer is "tonight"
    # rather than "refresh".
    assert P.WAITING and "16:20 CT" in P.WAITING


def test_the_count_strip_keeps_all_four_quadrants_even_at_zero():
    """An EMPTY trap bucket is a reading. Dropping it would leave a reader unable
    to tell "nothing is falling but leading" from "that bucket was not counted"."""
    strip = P.distribution({q: 0 for q in B.QUADRANTS})
    assert [q for q, _n in strip] == list(B.QUADRANTS[:4])


def test_the_count_strip_shows_the_unknown_bucket_only_when_it_is_real():
    """``unknown`` is an absence of a reading, not a fifth quadrant — a standing
    "0 No reading" chip would be noise on every normal day, and hiding a non-zero
    one would hide missing data."""
    counts = {q: 1 for q in B.QUADRANTS}
    assert ("unknown", 1) in P.distribution(counts)
    assert "unknown" not in dict(P.distribution({**counts, "unknown": 0}))


# ── the two score axes and the live column ───────────────────────────────────
def test_the_score_axes_render_as_percents_of_the_fraction_the_cascade_stores():
    """``scoring/momentum.trend_strength`` returns ``exp(slope*252)-1`` scaled by
    R² and ``relative_strength`` a difference of two ``_pct_return`` values —
    both FRACTIONS, unlike ``day_pct``, which is already a percent."""
    assert P.as_percent(0.044) == "+4.40%"
    assert P.as_percent(-0.0189) == "-1.89%"
    assert P.as_percent(None) == B.NO_READING


def test_the_day_tone_follows_the_digits_that_are_printed():
    """``signed_pct`` signs the ROUNDED value, so a tone read off the raw float
    would paint a cell green while the number beside it reads 0.00%."""
    assert P.day_tone(0.004) == "flat" and B.signed_pct(0.004) == "0.00%"
    assert P.day_tone(0.41) == "up" and P.day_tone(-0.22) == "down"
    assert P.day_tone(None) == "flat"


def test_the_live_layer_is_keyed_by_level_as_well_as_symbol():
    """An industry ETF is usually a scored stock too — ``bullbear_symbols`` dedups
    the quote call for that reason — so a symbol alone lets one row's move
    overwrite another's."""
    levels = _payload()["levels"]
    levels["stock"].append(_row("XOP", "XOP", 0.0, 0.0, day=-9.9, sector="Energy"))
    days = P.day_map(levels)
    assert days[("industry", "XOP")] == 1.0
    assert days[("stock", "XOP")] == -9.9


def test_the_scores_signature_moves_on_a_rescore_that_keeps_the_same_date():
    """The signature is what decides rebuild-vs-reprice, so a cascade that
    rescored without the date rolling must still rebuild the tree."""
    base = _payload()
    assert P.scores_signature(base) == P.scores_signature(_payload())
    rescored = _payload()
    rescored["levels"]["sector"][0]["raw"]["trend"] = 0.9
    assert P.scores_signature(rescored) != P.scores_signature(base)


def test_the_scores_signature_ignores_the_live_layer():
    """A quotes-only republish must NOT rebuild — that is what would collapse a
    sector the reader had open."""
    moved = _payload(quoted_at="2026-08-20T11:00:00-05:00")
    moved["levels"]["sector"][0]["day_pct"] = 9.9
    assert P.scores_signature(moved) == P.scores_signature(_payload())


# ── render ───────────────────────────────────────────────────────────────────
def test_render_on_a_cold_cache_explains_itself_instead_of_a_blank_strip(monkeypatch):
    texts = _texts(_render(monkeypatch, None))
    assert P.WAITING in texts
    assert not any("rising and leading" in t for t in texts)


def test_render_paints_one_row_per_sector_strongest_first(monkeypatch):
    """Ordering is ``B.by_strength`` — nightly trend, unscored last. The Desk
    strip reads the same view, so a page ordering of its own would let the two
    disagree about the same eleven sectors."""
    els = _render(monkeypatch, _payload())
    labels = [e._props.get("label") for e in els if isinstance(e, Expansion)]
    assert labels == ["Real Estate", "Energy", "Utilities"]


def test_render_shows_the_headline_the_marks_and_the_count_strip(monkeypatch):
    texts = _texts(_render(monkeypatch, _payload()))
    assert "1 of 3 sectors rising and leading" in texts
    assert "+4.40%" in texts and "-1.89%" in texts and "-0.22%" in texts
    assert B.quadrant_label("falling_leading") in texts   # the trap bucket chip
    assert B.quadrant_label("unknown") in texts           # Utilities scored None


def test_render_never_prints_a_regime_verdict(monkeypatch):
    """The design's deliberate omission: /sentiment/sectors and
    /sentiment/rotation already print contradictory risk-on/risk-off verdicts
    from quantities that are not commensurable. The payload carries ``regime``;
    this page must leave it alone."""
    blob = " ".join(_texts(_render(monkeypatch, _payload()))).lower()
    assert "risk-on" not in blob and "risk-off" not in blob
    assert "favorable" not in blob


def test_a_breadth_track_is_absent_at_none_and_empty_at_zero(monkeypatch):
    """The whole reason ``breadth_width`` returns None: a truthiness check at the
    call site would render "no constituents were usable" as "0% confirm"."""
    payload = _payload()
    payload["levels"]["sector"][0]["participation"] = 0.0
    widths = _fills(_render(monkeypatch, payload))
    assert "w-[0%]" in widths                       # Energy, a real zero
    assert len(widths) == 2                         # Utilities gets no track


def test_a_breadth_width_is_always_a_whole_percent(monkeypatch):
    """``w-[23.0%]`` is a class the bundled Tailwind JIT will not generate, which
    is why ``breadth_width`` rounds to an int."""
    for cls in _fills(_render(monkeypatch, _payload())):
        assert re.fullmatch(r"w-\[\d{1,3}%\]", cls), cls


def _bars(elements):
    """Every breadth-bar FILL element the render just emitted, in build order."""
    return [e for e in elements if any(
        c.startswith("w-[") and c.endswith("%]") for c in getattr(e, "_classes", []))]


def _fills(elements):
    """Every breadth-bar width class the render just emitted."""
    return [c for e in _bars(elements) for c in e._classes
            if c.startswith("w-[") and c.endswith("%]")]


def _by_text(elements, prefix):
    return next(e for e in elements
                if str(getattr(e, "text", "") or "").startswith(prefix))


def _grid(elements):
    """The scrolling column that holds the header row and every sector row."""
    return next(e for e in elements if P.MIN_W in e._classes)


def _scrim(elements):
    """The region's wait scrim — the element the kit hangs its spinner in.

    It used to be "the first ``ui.spinner``", which is a test of BUILD ORDER
    rather than of this page: the render now goes through ``kit.region``, and
    the kit is free to put a spinner anywhere on the page. Counting first is
    what makes taking the one safe rather than lucky — this page has exactly one
    region, and if a second ever appears the helper says so instead of silently
    answering about whichever was built first."""
    spinners = [e for e in elements if isinstance(e, ui.spinner)]
    assert len(spinners) == 1, \
        f"expected the region's one spinner, found {len(spinners)}"
    return spinners[0].parent_slot.parent


def _button(elements, text):
    """The button with exactly this label — named rather than positional, since
    the kit puts Refresh in the header's action row and a page may hold more."""
    return next(e for e in elements
                if isinstance(e, ui.button) and str(e.text) == text)


def _click_refresh(elements):
    """Press Refresh. NiceGUI wraps an ``on_click`` in a one-arg lambda taking
    the click args, so the stored handler is not the zero-arg one passed in."""
    button = _button(elements, "Refresh")
    next(listener.handler for listener in button._event_listeners.values()
         if listener.type == "click")(None)


def test_the_poll_tick_is_free_when_the_version_has_not_moved(monkeypatch):
    """Every open tab runs this every 2 s, so an unchanged version must cost the
    ``:ver`` probe and nothing else — no envelope deserialize, no repaint."""
    import bus_client
    els = _render(monkeypatch, _payload())
    probes, reads = [], []
    monkeypatch.setattr(bus_client, "read_version",
                        lambda v: probes.append(v) or 1)
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: reads.append(v) or (_payload(), 1))
    _timer(els).callback()
    assert probes == [P.VIEW] and reads == []


def test_the_poll_tick_repaints_when_the_version_moves(monkeypatch):
    import bus_client
    els = _render(monkeypatch, _payload())
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full",
                        lambda _v: (_payload(quoted_at=None), 2))
    _timer(els).callback()
    assert P.NO_QUOTES in _texts(els)


def _timer(elements):
    """The page's 2 s version poll — ``build_busy`` mounts a 1 s watchdog too,
    and it is created first, so an unfiltered ``next()`` picks the wrong one."""
    return next(e for e in elements
                if isinstance(e, ui.timer) and e.interval == 2.0)


def test_every_callback_is_guarded():
    """A timer or expand arriving after the tab closed otherwise raises."""
    src = inspect.getsource(P.render)
    for name in ("_maybe_repaint", "_request_refresh", "_expand_sector",
                 "_expand_industry"):
        assert re.search(rf"@guard\s+def {name}\(", src), name


def test_the_page_is_registered_and_guarded_against_inline_style():
    """Both are hand-maintained lists, so a new page escapes them silently."""
    tests = pathlib.Path(__file__).resolve().parent
    assert ROUTE in (tests / "test_shell.py").read_text(encoding="utf-8")
    guard_src = (tests / "test_no_inline_style.py").read_text(encoding="utf-8")
    # Quoted, because a bare "bullbear.py" is a substring of the page's own
    # filename and would pass while the pure module went unguarded.
    for quoted in ('"sentiment_bullbear.py"', '"bullbear.py"'):
        assert quoted in guard_src, quoted


# ── lazy expansion ───────────────────────────────────────────────────────────
# Pinned behaviourally rather than by the plan's ``"on_value_change" in src``
# grep: that string is present whichever way the children are built, so it
# cannot tell a lazy page from an eager one. Counting what is in the DOM can.
def test_the_default_screen_carries_sector_rows_and_nothing_below_them(monkeypatch):
    """376 rows in the DOM would make every repaint expensive and every open
    sector pointless."""
    texts = _texts(_render(monkeypatch, _payload()))
    assert "Energy" in texts
    for deeper in ("Oil & Gas E&P", "Oil Services", "XOM", "SLB"):
        assert deeper not in texts


def test_opening_a_sector_builds_its_industries_but_not_their_stocks(monkeypatch):
    els = _render(monkeypatch, _payload())
    texts = _texts(_open(els, "Energy"))
    assert "Oil & Gas E&P" in texts and "Oil Services" in texts
    assert "XOM" not in texts          # one more click away


def test_opening_an_industry_builds_its_stocks(monkeypatch):
    els = _render(monkeypatch, _payload())
    opened = _open(els, "Energy")
    assert "XOM" in _texts(_open(opened, "Oil & Gas E&P"))


def test_reopening_a_sector_builds_nothing_a_second_time(monkeypatch):
    """The cache: a body that already has children has been filled before, and a
    re-open only re-shows it."""
    els = _render(monkeypatch, _payload())
    assert _texts(_open(els, "Energy"))
    _panels(els)["Energy"].value = False
    assert _open(els, "Energy") == []


def test_two_sectors_can_be_open_at_once(monkeypatch):
    """No ``group=``: accordion behaviour would close Energy the moment you
    opened Real Estate, and comparing two sectors is the point of the tree."""
    els = _render(monkeypatch, _payload())
    for panel in _panels(els).values():
        assert "group" not in panel._props


def test_an_industry_with_no_member_stocks_says_so(monkeypatch):
    """Real: 3 of 69 industries held no admitted member stock on 2026-08-19, so
    an empty panel is a state and must not look like a broken one."""
    els = _render(monkeypatch, _payload())
    opened = _open(_open(els, "Energy"), "Oil Services")
    assert P.NO_STOCKS in _texts(opened)


def test_a_sector_with_no_scored_industries_says_so(monkeypatch):
    payload = _payload()
    payload["levels"]["industry"] = []
    payload["levels"]["stock"] = []
    els = _render(monkeypatch, payload)
    assert P.NO_INDUSTRIES in _texts(_open(els, "Energy"))


def test_an_orphan_stock_is_shown_under_its_sector_and_labelled(monkeypatch):
    """``build_tree`` files a stock here when its industry was never scored — 10
    of 296 that day. Dropping them would quietly shrink the sector."""
    texts = _texts(_open(_render(monkeypatch, _payload()), "Energy"))
    assert P.ORPHANS in texts and "SLB" in texts


def test_a_stock_row_carries_the_marks_but_no_breadth_track(monkeypatch):
    """Participation is None on every stock row — a stock has no constituents —
    and that is not zero breadth."""
    els = _render(monkeypatch, _payload())
    built = _open(_open(els, "Energy"), "Oil & Gas E&P")
    texts = _texts(built)
    assert "+3.00%" in texts and "+2.00%" in texts and "+0.50%" in texts
    assert _fills(built) == [] and B.NO_READING in texts
    # And no chevron: a leaf offering to open is a lie about the tree's depth.
    assert not [e for e in built if e._props.get("name") == "chevron_right"]


def test_an_industry_row_keeps_its_breadth_track(monkeypatch):
    """The bar is not a sector-only ornament: ``momentum.participation`` is set
    on industry rows too, and a thin industry is the same warning."""
    assert _fills(_open(_render(monkeypatch, _payload()), "Energy")) \
        == ["w-[50%]", "w-[0%]"]


def test_children_built_later_show_the_CURRENT_day_move(monkeypatch):
    """The tree survives a quotes-only republish, so a node opened afterwards
    would otherwise render the move frozen into it at build time."""
    import bus_client
    els = _render(monkeypatch, _payload())
    moved = _payload()
    moved["levels"]["stock"][0]["day_pct"] = 3.3
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (moved, 2))
    _timer(els).callback()
    assert "+3.30%" in _texts(_open(_open(els, "Energy"), "Oil & Gas E&P"))


def test_a_quotes_only_republish_reprices_without_closing_an_open_sector(monkeypatch):
    """At a ~30 s publish cadence, rebuilding on every version change would
    collapse the reader's open branches twice a minute."""
    import bus_client
    els = _render(monkeypatch, _payload())
    opened = _open(els, "Energy")
    moved = _payload()
    moved["levels"]["industry"][0]["day_pct"] = -4.5
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (moved, 2))
    _timer(els).callback()
    assert _panels(els)["Energy"].value is True
    assert "-4.50%" in _texts(opened)


def test_a_new_cascade_rebuilds_the_tree(monkeypatch):
    """The other half: new scores mean new rows, new order and new counts, so
    the tree must be rebuilt rather than repriced."""
    import bus_client
    els = _render(monkeypatch, _payload())
    _open(els, "Energy")
    rescored = _payload(session_date="2026-08-20")
    rescored["levels"]["sector"] = rescored["levels"]["sector"][:1]
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (rescored, 2))
    before = set(ui.context.client.elements)
    _timer(els).callback()
    fresh = [e for k, e in ui.context.client.elements.items() if k not in before]
    assert list(_panels(fresh)) == ["Energy"]
    assert _panels(fresh)["Energy"].value is False


def test_the_chevron_follows_the_panel(monkeypatch):
    """The only affordance saying a row opens — the header slot replaces
    Quasar's own expand icon, so nothing else points down."""
    els = _render(monkeypatch, _payload())
    icons = [e for e in els if getattr(e, "icon", None) or
             e._props.get("name") in ("chevron_right", "expand_more")]
    _open(els, "Energy")
    names = [e._props.get("name") for e in icons]
    assert names.count("expand_more") == 1 and names.count("chevron_right") == 2


def test_a_rebuilt_tree_stops_repricing_the_rows_it_replaced(monkeypatch):
    """The day-cell registry is rebuilt with the tree. Keeping the old entries
    would have every later tick write into elements no longer on the page — a
    registry growing by one whole tree per nightly cascade."""
    import bus_client
    els = _render(monkeypatch, _payload())
    dropped = next(e for e in els if getattr(e, "text", None) == "-0.22%")
    rescored = _payload(session_date="2026-08-20")
    rescored["levels"]["sector"] = rescored["levels"]["sector"][:1]
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (rescored, 2))
    _timer(els).callback()
    assert dropped.text == "-0.22%"


# ── colour, tone and visibility ──────────────────────────────────────────────
# The layer the first review found unpinned: ten of eleven surviving mutants were
# colour, tone, visibility or spinner state. On this page that is the argument
# itself, not decoration — the amber trap quadrant, the red thin-breadth bar.
def test_a_thin_breadth_bar_is_the_risk_off_hue_and_a_broad_one_the_risk_on(monkeypatch):
    """The polarity IS the qualifier. Inverted, a sector rising on a quarter of
    its constituents renders as a broadly confirmed advance — the one reading
    the design, the page help and both manuals all promise this bar prevents."""
    thin, broad = _bars(_render(monkeypatch, _payload()))
    assert TONE["down"]["fill"] in thin._classes      # Real Estate, 0.23
    assert TONE["up"]["fill"] in broad._classes       # Energy, 0.96


def test_the_breadth_polarity_holds_at_industry_level_too(monkeypatch):
    """A sector-only pin would miss an industry bar drawn from the other map."""
    broad, thin = _bars(_open(_render(monkeypatch, _payload()), "Energy"))
    assert TONE["up"]["fill"] in broad._classes       # Oil & Gas E&P, 0.50
    assert TONE["down"]["fill"] in thin._classes      # Oil Services, nothing confirms


def test_repricing_a_day_cell_replaces_its_tone_instead_of_stacking_one(monkeypatch):
    """The documented reason the ``remove=``/``add=`` idiom exists: two competing
    ``text-[…]`` classes on one element resolve by stylesheet order, not by which
    was added last, so a cell that went green stays green going down."""
    import bus_client
    els = _render(monkeypatch, _payload())
    cell = _by_text(els, "+0.41%")                    # Energy, up
    flipped = _payload()
    flipped["levels"]["sector"][0]["day_pct"] = -0.41
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (flipped, 2))
    _timer(els).callback()
    assert TONE["down"]["txt"] in cell._classes
    assert TONE["up"]["txt"] not in cell._classes


def test_the_quotes_line_turns_warning_when_the_call_failed_and_calm_again_after(monkeypatch):
    """A missing day-move column that reads in the same calm grey as a healthy
    clock is a failure the reader has no reason to look for."""
    import bus_client
    els = _render(monkeypatch, _payload(quoted_at=None))
    line = _by_text(els, "Live quotes")
    assert TONE["down"]["txt"] in line._classes
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (_payload(), 2))
    _timer(els).callback()
    # RE-AIMED, not weakened: the calm colour used to be the rotation family's
    # own ``NT["ghost"]``, a rung of the warm-neutral ladder the page kit
    # retired. It is now the app's faint text token — the colour every other
    # status line on the app wears. The warning half is unchanged, because it is
    # DATA: a failed quote call must still read red.
    assert TONE["down"]["txt"] not in line._classes and FAINT in line._classes


def test_the_subtitle_and_the_grid_appear_only_once_there_are_rows(monkeypatch):
    """Under the WAITING message, a subtitle explaining how to expand sectors and
    a column header over no rows both read as a rendering fault rather than a
    cold service."""
    cold = _render(monkeypatch, None)
    assert _by_text(cold, "Counted at sector").visible is False
    assert _grid(cold).visible is False
    warm = _render(monkeypatch, _payload())
    assert _by_text(warm, "Counted at sector").visible is True
    assert _grid(warm).visible is True


# ── the Refresh scrim ────────────────────────────────────────────────────────
def test_refresh_raises_the_scrim_and_a_landing_repaint_lowers_it(monkeypatch):
    import bus_client
    els = _render(monkeypatch, _payload())
    assert _scrim(els).visible is False
    _click_refresh(els)
    assert _scrim(els).visible is True
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (_payload(quoted_at=None), 2))
    _timer(els).callback()
    assert _scrim(els).visible is False


def test_a_refresh_that_changes_nothing_stops_waiting_on_its_own(monkeypatch):
    """``handlers.publish_bullbear`` carries the stored ``quoted_at`` forward when
    only the stamp moved, so ``cache_set(skip_unchanged=True)`` short-circuits and
    nothing on the bus moves. Off-hours that is every Refresh — and off-hours is
    when a reader presses it wondering why the map will not update."""
    els = _render(monkeypatch, _payload())
    now = [1000.0]
    monkeypatch.setattr(P, "monotonic", lambda: now[0])
    _click_refresh(els)
    _timer(els).callback()
    assert _scrim(els).visible is True                 # it could still land
    now[0] += P.REFRESH_WAIT_SEC
    _timer(els).callback()
    assert _scrim(els).visible is False


def test_the_expiry_only_fires_for_a_refresh_that_was_actually_asked_for(monkeypatch):
    """An ordinary unchanged tick must not announce a refresh nobody requested."""
    els = _render(monkeypatch, _payload())
    monkeypatch.setattr(P, "monotonic", lambda: 1e9)
    _timer(els).callback()
    assert _scrim(els).visible is False
    assert P.NOTHING_CHANGED not in _texts(els)


# ── the Bull / Bear Map on the page kit (Phase 3, Task 4) ───────────────────
def _kit_page_classes():
    """The classes ``kit.page()`` puts on a page column, read off the KIT.

    So the scope-class test below pins "the ``bullbear`` hook rides the kit's
    page column" rather than a class string copied out of ``ui_kit`` and free to
    drift from it."""
    with ui.card():
        return set(kit.page()._classes)


def test_the_bullbear_frame_is_the_kit_and_carries_no_surface_of_its_own():
    """``stale=True`` is deliberate and this page is the only one of the four
    rotation-family screens that earns it: ``_bullbear_publish_loop`` republishes
    every 30 s while the tape is open and every 5 min when it is closed
    (``sentiment_svc/scheduler.bullbear_due``), so both of ``alerts.stale_after``'s
    thresholds — 600 s in session, 45 min out of it — are honest."""
    src = inspect.getsource(P)
    assert "kit.page()" in src
    assert 'kit.header("Bull / Bear Map", view=VIEW, stale=True)' in src
    assert "kit.region(" in src
    # The page-scoped ground, the two faces and the warm-neutral ladder are
    # gone. Read off the NAMES the module binds and uses rather than the source
    # text: ``"NT["`` is a substring of ``_ROW_INDENT[``, so a text ban would
    # fail on a page that had already retired the ladder.
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
    names |= {a.asname or a.name for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.ImportFrom) for a in n.names}
    for token in ("NT", "NB", "NE", "_T", "ROTATION_TOKENS",
                  "ROTATION_FONT_HEAD_HTML"):
        assert token not in names, f"{token} is a page-scoped surface value"
    for key in ("RT_SANS", "RT_VOID_BG", "RT_MONO", "RT_PANEL_BG"):
        assert key not in src, f"{key} is a page-scoped surface value"
    # The ONE escape hatch STAYS: ``.q-item`` and ``.nicegui-expansion-content``
    # are Quasar-internal DOM that ``.classes()`` cannot reach, and the block
    # carries no colour, font or background. It is the documented exception, not
    # a palette block.
    assert "ui.add_css(_BULLBEAR_CSS)" in src


def test_the_waiting_toast_is_gone_and_the_outcome_toast_is_a_kit_toast():
    """Two toasts, two fates. "Refreshing — the page updates when the new read
    lands" only repeated what the region's spinner already says, and the standard
    keeps a toast for the OUTCOME of an action. ``NOTHING_CHANGED`` IS an
    outcome — the refresh completed and changed nothing — so it survives, as one
    of the kit's four kinds."""
    src = inspect.getsource(P)
    assert "ui.notify" not in src
    assert "Refreshing — the page updates" not in src
    assert 'kit.toast("info", NOTHING_CHANGED)' in src


def test_refresh_is_a_header_action_rather_than_a_row_in_the_page_body(monkeypatch):
    """The one page action this screen has — it commands the sentiment service —
    so it belongs beside the Updated stamp, not in a headline row of the page's
    own. Read off the DOM rather than the source: ``with head.actions:`` is in
    the source whichever row the button actually lands in."""
    els = _render(monkeypatch, _payload())
    actions = _button(els, "Refresh").parent_slot.parent
    title = _by_text(els, "Bull / Bear Map")
    assert actions.parent_slot.parent is title.parent_slot.parent, \
        "Refresh is not in the kit header's action row"


def test_the_two_clocks_stay_in_the_body_as_one_status_line(monkeypatch):
    """Neither clock is the page's own freshness, so neither is the header
    stamp's job. ``scores_lbl`` dates last night's cascade and ``quotes_lbl`` the
    live quote batch — two feeds that fail separately — and ``quotes_lbl``
    recolours when the quote call raised, a state a stamp cannot express."""
    els = _render(monkeypatch, _payload())
    scores = _by_text(els, "Scores as of")
    quotes = _by_text(els, "Quotes ")
    assert scores.parent_slot is quotes.parent_slot, \
        "the two clocks are one status-line row"
    title = _by_text(els, "Bull / Bear Map")
    assert scores.parent_slot.parent is not title.parent_slot.parent, \
        "the clocks belong to the body; the header carries the stamp"
    for cls in theme.EYEBROW.split():
        assert cls in scores._classes, f"{cls}: not the kit's status line"


def test_the_expansion_scope_class_rides_the_kit_page_column(monkeypatch):
    """The trap in this migration. ``_BULLBEAR_CSS`` is scoped under
    ``.bullbear``, so dropping the wrapper class along with the wrapper's surface
    would take the expansion padding fix with it — every child row pushed out of
    the column grid, with nothing in the source to say why."""
    els = _render(monkeypatch, _payload())
    scoped = [e for e in els if "bullbear" in getattr(e, "_classes", [])]
    assert len(scoped) == 1, f"expected one scope element, found {len(scoped)}"
    column = scoped[0]
    assert set(column._classes) - {"bullbear"} == _kit_page_classes(), \
        "the scope class must ride the kit page column, carrying no surface"
    for panel in _panels(els).values():
        node = panel
        while node is not None and node is not column:
            node = getattr(node.parent_slot, "parent", None)
        assert node is column, "an expansion outside the scope the CSS covers"


def test_the_scrim_still_survives_the_rebuild_that_clears_the_rows(monkeypatch):
    """A must-not-change guard, and it passes before and after by design.

    This page's spinner was the one of the four that was ALREADY right: the
    scrim hung on ``scroll_box`` while ``_rebuild`` cleared ``rows_box``
    underneath it, which is exactly the split ``kit.region`` formalises as
    ``outer`` / ``content``. The migration had to PRESERVE that, and mounting the
    whole grid — scroller included — inside ``region.content`` would have undone
    it."""
    import bus_client
    els = _render(monkeypatch, _payload())
    scrim = _scrim(els)
    rescored = _payload(session_date="2026-08-20")
    monkeypatch.setattr(bus_client, "read_version", lambda _v: 2)
    monkeypatch.setattr(bus_client, "read_full", lambda _v: (rescored, 2))
    _timer(els).callback()                  # → _paint → _rebuild → rows_box.clear()
    assert scrim.id in ui.context.client.elements, \
        "a tree rebuild deleted the wait scrim"


def test_the_day_tones_the_breadth_hues_and_the_quadrant_chips_are_untouched():
    """Charts keep their data colours: the half the kit may not reach. Passes
    before and after by design — it is the guard on what must NOT change."""
    for tone in ("up", "down", "flat"):
        assert P._DAY_TXT[tone] == TONE[tone]["txt"], f"the {tone} day cell"
    assert P._BREADTH_FILL[True] == TONE["down"]["fill"], "a thin breadth bar"
    assert P._BREADTH_FILL[False] == TONE["up"]["fill"], "a broad breadth bar"
    # A failed quote call is a STATE, not chrome, and keeps the risk-off hue.
    assert P._QUOTES_TXT[False] == TONE["down"]["txt"]
    # The quadrant map is reached exactly twice — a grid row and a count chip —
    # and neither may grow a colour of its own.
    src = inspect.getsource(P.render)
    assert src.count("B.quadrant_class(") == 2
