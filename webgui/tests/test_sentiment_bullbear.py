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

ROUTE = "/sentiment/bullbear"


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
    assert got <= {"datetime", "bus_client", "nicegui", "pages",
                   "pages.options.theme", "pages.rotation_view", "pages.ui_guard"}


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
    assert P.WAITING and "Waiting" in P.WAITING


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


def _fills(elements):
    """Every breadth-bar width class the render just emitted."""
    return [c for e in elements for c in getattr(e, "_classes", [])
            if c.startswith("w-[") and c.endswith("%]")]


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
    assert "sentiment_bullbear.py" in guard_src
