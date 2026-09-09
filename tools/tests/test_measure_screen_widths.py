"""``tools/measure_screen_widths.py`` -- the pure half.

The half that drives a browser needs a running app, a network and a Chrome; none
of that belongs in a suite. What IS pinned here is every judgement the tool makes
ABOUT the numbers, because a confidently wrong number is the whole hazard: this
tool exists to decide WHICH page modules get edited, and a fabricated viewport
sends that edit at the wrong files.

The specific fiction it must refuse is documented in CLAUDE.md -- the Claude
Browser pane reports ``viewport: 0`` on this app, so every width derived from it
is invented. ``viewport_verdict`` is the guard, and the first three tests below
are the reason the module exists in two halves at all.
"""
import pytest

from tools import measure_screen_widths as m


# --- the viewport guard: a measurement you cannot trust is not a measurement --

def test_a_zero_viewport_is_untrustworthy_never_a_measurement():
    """The documented failure: a browser that reports ``innerWidth: 0`` still
    reports a scrollWidth, and the subtraction still yields a plausible-looking
    number. It must come back as a REASON, not as a width."""
    reason = m.viewport_verdict(1280, inner_width=0, client_width=0)
    assert reason is not None
    assert "0" in reason


def test_a_viewport_that_is_not_the_width_we_asked_for_is_untrustworthy():
    """Setting the window is a request, not a guarantee -- a minimum window
    size, a device-scale factor or a refused resize all silently give you a
    different page than the one you think you measured."""
    reason = m.viewport_verdict(1280, inner_width=1024, client_width=1009)
    assert reason is not None
    assert "1024" in reason and "1280" in reason


def test_the_width_we_asked_for_is_trustworthy():
    assert m.viewport_verdict(1280, inner_width=1280, client_width=1265) is None


def test_a_client_width_of_zero_is_untrustworthy_even_with_a_good_viewport():
    """``clientWidth`` is the denominator of every overflow number here. Zero
    means the document has not laid out, and ``scrollWidth - 0`` would report
    the whole document as overflow."""
    assert m.viewport_verdict(1280, inner_width=1280, client_width=0) is not None


def test_a_scrollbar_sized_gap_between_inner_and_client_is_fine():
    """A classic vertical scrollbar eats ~15px off ``documentElement.clientWidth``
    while leaving ``innerWidth`` alone. That is normal, not a failed resize."""
    assert m.viewport_verdict(1600, inner_width=1600, client_width=1585) is None


# --- overflow arithmetic and the clip threshold ------------------------------

def test_overflow_is_scroll_width_minus_client_width():
    assert m.overflow_px(1877, 1265) == 612


def test_a_sub_pixel_overflow_is_not_a_clip():
    """``scrollWidth`` is an integer rounded UP, so a fractional layout reports
    1px of overflow on a page that does not scroll. Calling that a clip would
    send Task 4 at every screen in the app."""
    assert m.is_clipping(0) is False
    assert m.is_clipping(1) is False
    assert m.is_clipping(m.CLIP_THRESHOLD_PX + 1) is True


# --- element identity + whether an element already contains itself -----------

def test_element_ident_is_tag_plus_the_first_three_classes():
    assert m.element_ident("div", ["a", "b", "c", "d", "e"]) == "div.a.b.c"


def test_element_ident_survives_an_element_with_no_classes():
    assert m.element_ident("html", []) == "html"


def test_element_ident_truncates_a_tailwind_arbitrary_value_class():
    """Measured live: one Desk grid carries a single class 180 characters long
    (``grid-cols-[64px_minmax(53px,0.8fr)_...]``). Printed whole it wraps the
    table it is supposed to explain, and the leading characters already
    identify the element."""
    monster = "grid-cols-[64px_minmax(53px,0.8fr)_minmax(42px,0.6fr)_minmax(144px,2fr)]"
    out = m.element_ident("div", ["grid", monster])
    assert len(out) < len(monster)
    assert out.startswith("div.grid.grid-cols-[64px")
    assert out.endswith("~")


def test_an_element_that_scrolls_itself_needs_nothing():
    """The whole point of Task 4 is to give panels their own scroll. One that
    already has it is done -- and reporting it as work would be a false
    positive in the input to that task."""
    for value in ("auto", "scroll", "hidden", "clip"):
        assert m.is_self_containing(value) is True
    for value in ("visible", ""):
        assert m.is_self_containing(value) is False


# --- picking the elements worth printing -------------------------------------

def _el(tag, classes, sw, cw, ox="visible"):
    return {"tag": tag, "classes": classes, "overflow_x": ox,
            "scroll_width": sw, "client_width": cw}


def test_top_overflowing_sorts_by_overflow_and_caps_the_list():
    raw = [_el("div", ["a"], 1100, 1000),      # +100
           _el("div", ["b"], 2000, 1000),      # +1000
           _el("div", ["c"], 1300, 1000)]      # +300
    out = m.top_overflowing(raw, limit=2)
    assert [e["ident"] for e in out] == ["div.b", "div.c"]
    assert [e["overflow"] for e in out] == [1000, 300]


def test_top_overflowing_drops_zero_width_elements():
    """An inline or SVG element reports ``clientWidth`` 0 with a real
    ``scrollWidth``; every one of them would otherwise crowd out the panel that
    actually overflows."""
    raw = [_el("span", [], 400, 0), _el("div", ["real"], 1200, 1000)]
    assert [e["ident"] for e in m.top_overflowing(raw)] == ["div.real"]


def test_top_overflowing_marks_the_ones_already_containing_themselves():
    raw = [_el("div", ["scroller"], 2000, 1000, ox="auto"),
           _el("div", ["leaky"], 1500, 1000, ox="visible")]
    out = {e["ident"]: e["self_containing"] for e in m.top_overflowing(raw)}
    assert out == {"div.scroller": True, "div.leaky": False}


def test_uncontained_counts_only_the_elements_that_still_leak():
    raw = [_el("div", ["scroller"], 2000, 1000, ox="auto"),
           _el("div", ["leaky"], 1500, 1000, ox="visible")]
    assert m.uncontained(m.top_overflowing(raw)) == 1


def test_top_overflowing_collapses_repeated_identical_rows():
    """Measured live: the Desk's positions grid is one row element repeated,
    each overflowing by the same amount. Eight identical lines say nothing that
    one line and a count does not -- and they pushed the document-level row off
    the end of the list, which is the distinction the report exists to make."""
    raw = [_el("div", ["row"], 1800, 1000)] * 8 + [_el("html", [], 1200, 1000)]
    out = m.top_overflowing(raw, limit=4)
    idents = [e["ident"] for e in out]
    assert idents.count("div.row") == 1
    assert next(e for e in out if e["ident"] == "div.row")["count"] == 8


def test_top_overflowing_always_keeps_the_document_level_row():
    """``html``/``body`` overflowing IS the whole-document sideways scroll --
    the thing being fixed. A narrower but larger-overflowing panel must never
    push it out of the list, or a document-level leak reads as a panel one."""
    raw = ([_el("div", [f"p{i}"], 5000, 1000) for i in range(10)]
           + [_el("html", [], 1100, 1000)])
    out = m.top_overflowing(raw, limit=3)
    assert "html" in [e["ident"] for e in out]
    assert len([e for e in out if e["ident"] != "html"]) == 3


def test_document_level_is_flagged_as_such():
    out = m.top_overflowing([_el("body", ["x"], 1500, 1000)])
    assert out[0]["document_level"] is True
    out = m.top_overflowing([_el("div", ["x"], 1500, 1000)])
    assert out[0]["document_level"] is False


# --- "it fits" must not mean "there was nothing on the page" -----------------

def test_a_page_showing_the_waiting_line_gets_no_verdict():
    """A cold Redis view draws the shared 'no data yet' sentence and nothing
    else. It cannot overflow, so it would be filed as a screen that fits at
    1280 -- and Task 4 would skip a screen nobody has actually measured."""
    reason = m.content_warning(element_count=5000, waiting=True)
    assert reason is not None
    assert "waiting" in reason


def test_a_page_that_barely_rendered_gets_no_verdict():
    reason = m.content_warning(element_count=12, waiting=False)
    assert reason is not None
    assert "12" in reason


def test_a_page_that_really_painted_is_measurable():
    assert m.content_warning(element_count=2000, waiting=False) is None


def test_the_waiting_marker_is_really_the_shared_copy():
    """The check is a substring match against ``webgui/pages/copy.py``. If that
    wording changes and this marker does not, the check silently stops finding
    anything -- and every cold screen comes back as a clean measurement.

    Loaded BY PATH: the module is named ``copy``, which shadows the stdlib
    module of that name for the whole pytest session if ``webgui/pages`` goes
    on ``sys.path`` -- the documented cross-app collision, one directory down.
    """
    import importlib.util
    import pathlib

    import repo_paths
    path = pathlib.Path(repo_paths.WEBGUI) / "pages" / "copy.py"
    spec = importlib.util.spec_from_file_location("_webgui_copy_for_widths", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for name in ("WAITING_OPTIONS", "WAITING_SENTIMENT", "WAITING_MARKET"):
        assert m.WAITING_MARKER in getattr(mod, name), name


# --- per-screen summary: the sentence Task 4 actually acts on ----------------

def _row(width, overflow, trusted=True, empty=None):
    return {"width": width, "overflow": overflow, "empty": empty,
            "untrustworthy": None if trusted else "viewport reported 0"}


def test_clip_summary_names_the_widest_width_that_still_clips():
    rows = [_row(1280, 612), _row(1440, 400), _row(1600, 0), _row(1920, 0)]
    s = m.clip_summary(rows)
    assert s["clips"] is True
    assert s["worst_width"] == 1440
    assert s["clean_from"] == 1600


def test_clip_summary_says_so_when_nothing_clips():
    s = m.clip_summary([_row(1280, 0), _row(1920, 1)])
    assert s["clips"] is False
    assert s["worst_width"] is None
    assert s["clean_from"] == 1280


def test_clip_summary_ignores_untrustworthy_rows_rather_than_averaging_them_in():
    """A row we could not trust must not become evidence in either direction --
    neither a clip that was never measured nor a clean width that was not."""
    rows = [_row(1280, 9999, trusted=False), _row(1600, 0)]
    s = m.clip_summary(rows)
    assert s["clips"] is False
    assert s["untrustworthy"] == 1


def test_clip_summary_of_nothing_but_untrustworthy_rows_reports_no_verdict():
    s = m.clip_summary([_row(1280, 500, trusted=False)])
    assert s["clips"] is None          # not False -- we do not know
    assert s["untrustworthy"] == 1


def test_clip_summary_does_not_count_a_blank_page_as_a_clean_width():
    """The false negative that matters: a screen whose feed was cold reports
    zero overflow at every width, which is indistinguishable from a screen that
    fits -- unless a blank row is excluded from the evidence."""
    rows = [_row(1280, 0, empty="page shows the 'no data yet' waiting line"),
            _row(1920, 0)]
    s = m.clip_summary(rows)
    assert s["clean_from"] == 1920     # NOT 1280
    assert s["empty"] == 1


def test_clip_summary_of_nothing_but_blank_rows_reports_no_verdict():
    s = m.clip_summary([_row(1280, 0, empty="page did not paint")])
    assert s["clips"] is None
    assert s["empty"] == 1


# --- the window-sizing correction (pure arithmetic, no browser) --------------

def test_corrected_outer_adds_back_the_chrome_the_window_manager_took():
    """Set the window to 1280 and the page may get 1264 -- the outer rect
    includes borders this tool does not control. The correction is the delta."""
    assert m.corrected_outer(outer=1280, inner=1264, target=1280) == 1296


def test_corrected_outer_is_a_no_op_when_the_viewport_already_matches():
    assert m.corrected_outer(outer=1296, inner=1280, target=1280) == 1296


# --- a width that blew up is refused, not dropped ---------------------------

def test_a_failed_width_becomes_an_untrustworthy_row_not_a_missing_one():
    """Dropping it would read as a width nobody needed to test, and the verdict
    would then be drawn from whichever widths happened to work."""
    row = m.failed_row("http://x/desk", 1440, "timeout")
    assert row["width"] == 1440
    assert "timeout" in row["untrustworthy"]
    assert m.clip_summary([row])["clips"] is None


def test_a_failed_width_does_not_poison_the_widths_that_worked():
    rows = [m.failed_row("http://x/desk", 1280, "timeout"),
            _row(1920, 0)]
    s = m.clip_summary(rows)
    assert s["clips"] is False
    assert s["untrustworthy"] == 1
    assert s["clean_from"] == 1920


# --- settle detection --------------------------------------------------------

def test_a_dom_that_stopped_changing_is_settled():
    assert m.is_settled(["complete|10|1280|900"] * 3, stable_polls=3) is True


def test_a_dom_still_repainting_is_not_settled():
    assert m.is_settled(["complete|10|1280|900", "complete|11|1280|950",
                         "complete|11|1280|950"], stable_polls=3) is False


def test_a_document_that_has_not_finished_loading_is_never_settled():
    """The signature carries ``readyState`` precisely so a page that is stable
    only because it has not started yet cannot pass."""
    assert m.is_settled(["loading|10|1280|900"] * 5, stable_polls=3) is False


def test_too_few_polls_is_not_yet_settled():
    assert m.is_settled(["complete|10|1280|900"] * 2, stable_polls=3) is False


# --- CLI parsing -------------------------------------------------------------

def test_parse_widths_reads_a_comma_list():
    assert m.parse_widths("1280,1920") == [1280, 1920]


def test_parse_widths_rejects_junk_rather_than_silently_measuring_a_default():
    with pytest.raises(ValueError):
        m.parse_widths("1280,wide")


def test_default_widths_are_the_six_the_plan_names():
    assert m.DEFAULT_WIDTHS == [1280, 1366, 1440, 1600, 1920, 2560]


# --- the screen list is the published table, never a typed copy --------------

def test_the_screen_list_is_live_screens_not_a_second_copy():
    """A typed list of URLs goes stale the moment a screen is published. The
    same reasoning ``tools/capture_live_shots.py`` records for its own targets."""
    import importlib.util
    import pathlib

    import repo_paths
    path = pathlib.Path(repo_paths.WEBGUI) / "live_screens.py"
    spec = importlib.util.spec_from_file_location("_live_screens_for_widths", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    published = mod.SCREENS
    got = m.screens()
    assert [s.slug for s in got] == [s.slug for s in published]
    assert [s.route for s in got] == [s.route for s in published]


def test_select_screens_limits_by_slug():
    got = m.select_screens(m.screens(), ["desk", "flow"])
    assert [s.slug for s in got] == ["desk", "flow"]


def test_select_screens_with_no_filter_returns_everything():
    assert len(m.select_screens(m.screens(), [])) == len(m.screens())


def test_select_screens_refuses_an_unknown_slug():
    """Silently measuring nothing looks exactly like measuring a clean screen."""
    with pytest.raises(ValueError) as exc:
        m.select_screens(m.screens(), ["desk", "nosuchscreen"])
    assert "nosuchscreen" in str(exc.value)


def test_urls_are_built_against_the_base_origin():
    urls = [m.screen_url("https://live.neuralstrike.co", s) for s in m.screens()[:2]]
    assert urls[0] == "https://live.neuralstrike.co/desk"
    assert urls[1] == "https://live.neuralstrike.co/opportunity"


def test_a_trailing_slash_on_the_base_does_not_double_up():
    s = m.screens()[0]
    assert m.screen_url("https://live.neuralstrike.co/", s) == "https://live.neuralstrike.co/desk"
