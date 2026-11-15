"""/ES and /NQ on Dealer Positioning: what the page says about them, and that no
existing symbol changes.

The pure pieces are tested directly. What the page wires together is driven through
the REAL render and its own timer callbacks on the fake bus, as
``test_gamma_public_page.py`` does: a source-level check of a closure proves nothing
about what it sends or hides. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, sections 5.8, 5.9 and 5.11.
"""
import ast
import asyncio
import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import bus_client
from pages.options import gamma, gamma_feed
from pages.options.gamma_feed import (feed_label, hidden_views, select_options,
                                      shows_analysis)
from shared import futures_chain, futures_chain_config, public_gamma
from shared.bus.client import reset_fake_bus
from shared.futures_chain import is_feed_symbol

_CT = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def five_minute_captures(monkeypatch):
    """These tests measure gaps against a 300-second capture with no faster cadence to
    fall back from. ``shipped_cadence`` is the one that holds what ships."""
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "interval_sec": 300, "fill_step_sec": 300,
                                 "fallback_interval_sec": 300})


@pytest.fixture
def shipped_cadence(monkeypatch):
    settings = futures_chain_config.DEFAULTS["capture"]
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture", lambda: {**real(), **{
        key: settings[key] for key in ("interval_sec", "fill_step_sec", "fallback_interval_sec")}})


def test_a_gap_at_the_fallback_is_held_and_no_outage(shipped_cadence):
    start = ct(2026, 11, 8, 9, 0).timestamp()
    rows = futures_chain.fill_rows([(start, 1.0), (start + 300, 2.0), (start + 420, 3.0)], 300, 120)
    assert [r[0] - start for r in rows] == [0, 120, 240, 300, 420]
    assert [r[1] for r in gamma_feed.with_outages(rows)] == [1.0, 1.0, 1.0, 2.0, 3.0]


def test_a_gap_past_the_fallback_is_still_an_outage(shipped_cadence):
    start = ct(2026, 11, 8, 9, 0).timestamp()
    out = gamma_feed.with_outages([(start, 1.0), (start + 600, 2.0)])
    assert [r[0] - start for r in out] == [0, 120, 240, 360, 480, 600]
    assert [r[1] for r in out] == [1.0, None, None, None, None, 2.0]


def ct(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_CT)


NOW = ct(2026, 11, 9, 10, 0).timestamp()   # a Monday morning, mid session


def _expiry_of(captured):
    return futures_chain.session_expiry(datetime.fromtimestamp(captured, _CT)).isoformat()


def snap(captured, *, stale=False, frozen=False, why="", strike_low=None,
         strike_high=None, expiry=None, symbol="FOP.ES", last_row_at=None):
    """A payload holding only what a label is built from."""
    return {"symbol": symbol, "dte": 0,
            "feed": {"label": futures_chain.display_symbol(symbol), "captured_at": captured,
                     "expiry": expiry or _expiry_of(captured), "frozen": frozen,
                     "stale": stale, "reason": "", "why": why, "strike_low": strike_low,
                     "strike_high": strike_high, "last_row_at": last_row_at}}


def _settings():
    capture = futures_chain_config.capture()
    return capture["interval_sec"], capture["fill_step_sec"]


def _row(ts, spot=7600.0):
    return (ts, spot, 7590.0, 7610.0, 7580.0, 1.0e9,
            {7600.0: {"call": 2.0, "put": -1.0, "net": 1.0},
             7605.0: {"call": 3.0, "put": -1.0, "net": 2.0}})


def _filled(*captures):
    """The rows the service publishes for captures at these epoch seconds."""
    interval, step = _settings()
    return futures_chain.fill_rows([_row(ts) for ts in captures], interval, step)


def _is_blank(row):
    return all(cell is None for cell in row[1:])


# ── the dropdown ─────────────────────────────────────────────────────────────

def test_options_stay_a_plain_list_with_no_feed_symbol():
    symbols = ["$SPX", "SPY"]
    assert select_options(symbols) == ["$SPX", "SPY"]
    assert select_options(symbols) is symbols


def test_feed_symbols_show_their_label_and_keep_their_value():
    assert select_options(["$SPX", "FOP.ES"]) == {"$SPX": "$SPX", "FOP.ES": "/ES"}


def test_the_table_of_symbols_is_validated_once_for_a_whole_list(monkeypatch):
    """``is_feed_symbol`` re-validates the config per call: never in a row loop."""
    calls = []
    real = futures_chain.feed_symbols
    monkeypatch.setattr(futures_chain, "feed_symbols",
                        lambda: calls.append(1) or real())
    monkeypatch.setattr(futures_chain, "is_feed_symbol",
                        lambda s: pytest.fail("a per-row call to is_feed_symbol"))
    select_options(["$SPX", "SPY", "QQQ", "FOP.ES", "FOP.NQ"])
    assert len(calls) == 1


def test_the_public_page_never_offers_a_feed_symbol():
    reset_fake_bus()
    bus_client.reset()
    bus_client.bus().cache_set(f"cache:{public_gamma.SYMBOLS_VIEW}", {"symbols": ["$SPX"]})
    listed = gamma.symbol_options(bus_client.read(public_gamma.SYMBOLS_VIEW))
    assert not any(is_feed_symbol(s) for s in listed)


# ── what is offered ──────────────────────────────────────────────────────────

def test_flow_and_open_interest_are_hidden_for_the_feed_only():
    assert hidden_views("FOP.NQ") == {"Flow", "Open Interest"} and hidden_views("SPY") == frozenset()


def test_analysis_is_not_offered_for_the_feed():
    assert shows_analysis("FOP.ES") is False and shows_analysis("$SPX") is True


# ── the label ────────────────────────────────────────────────────────────────

def test_stale_label_states_the_age():
    assert (feed_label(snap(NOW - 1260, stale=True), NOW)
            == "Stale: captured 21 minutes ago")


def test_frozen_label_states_the_time():
    label = feed_label(snap(ct(2026, 11, 6, 13, 55).timestamp(), frozen=True), NOW)
    assert label.startswith("Last session, as of ")
    assert "13:55 Central" in label and "Friday, November 6" in label


def test_current_label_states_the_capture_time_not_stale():
    label = feed_label(snap(NOW - 60), NOW)
    assert "Stale" not in label and "09:59 Central" in label


def test_no_feed_block_no_label():
    assert feed_label({"symbol": "SPY"}, NOW) is None
    assert feed_label(None, NOW) is None


def test_the_cause_is_carried_on_the_label():
    assert "not changing" in feed_label(
        snap(NOW - 60, stale=True, why="values not changing"), NOW)


def test_the_label_states_the_captured_range_when_it_was_narrowed():
    assert "7,400.00 to 7,800.00" in feed_label(
        snap(NOW - 60, strike_low=7400, strike_high=7800), NOW)


@pytest.mark.parametrize("build", [
    lambda: snap(NOW - 60, strike_low=7400, strike_high=7800),
    lambda: snap(NOW - 1260, stale=True, strike_low=7400, strike_high=7800),
    lambda: snap(ct(2026, 11, 6, 13, 55).timestamp(), frozen=True,
                 strike_low=7400, strike_high=7800),
])
def test_the_captured_range_is_on_every_label_because_the_walls_see_only_those(build):
    assert "7,400.00 to 7,800.00" in feed_label(build(), NOW)


def test_a_cached_flag_saying_current_does_not_outlive_its_payload():
    """The overnight tick does not run and a page load enqueues nothing of its own:
    the operator opens the page on a payload written hours ago that says current."""
    assert feed_label(snap(NOW - 20000, stale=False), NOW).startswith("Stale: captured")


def test_a_payload_from_last_night_reads_stale_at_five_in_the_morning():
    label = feed_label(snap(ct(2026, 11, 8, 22, 5).timestamp(), stale=False),
                       ct(2026, 11, 9, 5, 0).timestamp())
    assert label.startswith("Stale: captured 6 hours 55 minutes ago")


def test_the_age_limit_comes_from_the_config(monkeypatch):
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "stale_after_sec": 60})
    assert feed_label(snap(NOW - 90), NOW).startswith("Stale")
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "stale_after_sec": 3600})
    assert "Stale" not in feed_label(snap(NOW - 90), NOW)


def test_a_session_that_has_ended_is_the_last_session_whatever_the_flags_say():
    """Written at 14:50 with both flags off, read after the 15:00 expiry."""
    captured = ct(2026, 11, 9, 14, 50).timestamp()
    label = feed_label(snap(captured), ct(2026, 11, 9, 15, 30).timestamp())
    assert label.startswith("Last session, as of 14:50 Central")


def test_a_last_session_label_says_when_the_next_session_has_no_capture():
    captured = ct(2026, 11, 6, 13, 55).timestamp()
    weekend = feed_label(snap(captured, frozen=True), ct(2026, 11, 7, 12, 0).timestamp())
    monday = feed_label(snap(captured, frozen=True), NOW)
    assert "No capture has arrived" not in weekend
    assert "No capture has arrived" in monday


def test_the_cached_age_sentence_never_contradicts_the_label():
    """``reason`` holds an age worked out when the payload was written. ``why`` is
    the cause alone, and it is what the label carries when it is there."""
    payload = snap(NOW - 20000, stale=True, why="ThinkorSwim is logged out")
    payload["feed"]["reason"] = "The last capture is 20 minutes old. ThinkorSwim is logged out"
    label = feed_label(payload, NOW)
    assert "20 minutes" not in label and "ThinkorSwim is logged out" in label
    # ``reason`` alone is never read: it could only print the cached age.
    payload["feed"]["why"] = ""
    assert "20 minutes" not in feed_label(payload, NOW)


def test_no_label_is_built_from_dte():
    """The adapter stamps ``dte`` 0 on every capture, an evening's included."""
    one = snap(NOW - 60)
    other = {**one, "dte": 1}
    assert feed_label(one, NOW) == feed_label(other, NOW)
    assert "DTE" not in feed_label(one, NOW)
    reads = [n.value for n in ast.walk(ast.parse(inspect.getsource(gamma_feed)))
             if isinstance(n, ast.Constant) and n.value == "dte"]
    assert not reads


def test_the_hedge_note_never_says_the_nearest_expiry_is_not_0_dte():
    """An evening capture's nearest expiry IS the session's. The engine's figure is
    absent then, and the note says only that."""
    note = gamma_feed.hedge_note({"hedge_pressure": None})
    assert "0-DTE" not in note and "n/a" in note
    assert gamma_feed.hedge_note({"hedge_pressure": 5.0}) is None


def test_the_empty_lines_name_the_label_and_the_real_source():
    assert "/ES" in gamma.no_spot_text("FOP.ES", True)
    assert "FOP.ES" not in gamma.no_spot_text("FOP.ES", True)
    assert "Refresh during market hours" not in gamma.no_spot_text("FOP.ES", True)
    assert "Schwab" not in gamma.no_greeks_text("FOP.NQ")
    assert "/NQ" in gamma.no_greeks_text("FOP.NQ")


def test_the_empty_lines_of_an_existing_symbol_are_unchanged():
    assert gamma.no_spot_text("SPY", True) == (
        "No spot price for SPY yet (market closed or sparse data) — try Refresh "
        "during market hours.")
    assert "Schwab sent no Greeks for SPY" in gamma.no_greeks_text("SPY")


# ── an outage is an outage ───────────────────────────────────────────────────

def test_a_five_hour_outage_leaves_a_blank_stretch_of_the_right_length():
    interval, step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    before = [start + i * interval for i in range(4)]
    after = [before[-1] + 5 * 3600 + i * interval for i in range(4)]
    rows = _filled(*before, *after)
    out = gamma_feed.with_outages(rows)
    blanks = [r for r in out if _is_blank(r)]
    last_drawn = max(r[0] for r in rows if r[0] < after[0])
    assert len(blanks) == (after[0] - last_drawn) // step - 1
    assert [r[0] for r in blanks] == [last_drawn + step * (i + 1) for i in range(len(blanks))]
    assert all(len(r) == 7 for r in blanks)
    # Everything that was drawn is still there, in order, untouched.
    assert [r for r in out if not _is_blank(r)] == rows
    assert [r[0] for r in out] == sorted(r[0] for r in out)


def test_a_gap_of_one_interval_or_less_adds_nothing():
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = _filled(*[start + i * interval for i in range(5)])
    assert gamma_feed.with_outages(rows) == rows
    # A capture arriving late, inside one interval of the last column drawn.
    late = _filled(start, start + interval + interval // 3)
    assert gamma_feed.with_outages(late) == late


def test_one_missed_capture_shows_as_a_short_blank():
    interval, step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    out = gamma_feed.with_outages(_filled(start, start + 2 * interval))
    blanks = [r for r in out if _is_blank(r)]
    assert blanks and blanks[0][0] == start + interval
    assert blanks[-1][0] < start + 2 * interval and len(blanks) == interval // step


def test_the_interval_and_step_come_from_the_config(monkeypatch):
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "interval_sec": 120, "fill_step_sec": 60})
    start = ct(2026, 11, 8, 23, 0).timestamp()
    gap = [(start, 1.0), (start + 60, 1.0), (start + 600, 1.0)]
    out = gamma_feed.with_outages(gap)
    assert [r[0] for r in out] == [start + 60 * i for i in range(11)]
    assert len(gamma_feed.with_outages([(start, 1.0), (start + 120, 1.0)])) == 2


def test_the_figure_draws_nothing_in_the_blank_stretch():
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = gamma_feed.with_outages(_filled(start, start + interval, start + 5 * 3600))
    blank_x = {i for i, r in enumerate(rows) if _is_blank(r)}
    assert blank_x
    fig = gamma_feed.break_spot_line(gamma.heatmap_figure(rows), rows)
    cells = fig["series"][0]["data"]
    assert cells and not blank_x & {x for x, _y, _v in cells}
    assert len(fig["xAxis"]["categories"]) == len(rows)
    # The price line is broken there, not ruled straight across five hours.
    spot = next(s for s in fig["series"] if s["name"] == "Spot")["data"]
    assert [x for x, y in spot if y is None] == [min(blank_x)]
    assert all(y is None for x, y in spot if x in blank_x)
    assert [p[0] for p in spot] == sorted(p[0] for p in spot)


def test_the_blank_stretch_survives_every_transform():
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = gamma_feed.with_outages(_filled(start, start + interval, start + 5 * 3600))
    blank_x = {i for i, r in enumerate(rows) if _is_blank(r)}
    for kwargs in ({"frame": "spot", "half": 2},
                   {"smooth": {"minutes": 2, "strikes": 1}},
                   {"show": "open"}, {"show": "window", "change_window_min": 30},
                   {"scale": "share"}, {"mode": "size"}):
        cells = gamma.heatmap_figure(rows, **kwargs)["series"][0]["data"]
        assert not blank_x & {x for x, _y, _v in cells}, kwargs


def test_a_blank_column_is_never_the_basis_of_a_change_view():
    """``basis_grid`` skips what holds no grid: no basis means no bars, never bars
    against nothing."""
    from pages.options import gamma_heat
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = gamma_feed.with_outages(_filled(start, start + 5 * 3600))
    assert gamma_heat.basis_grid(rows, "window", 30) == rows[0][6]


def test_the_hedge_panel_lines_up_with_the_blank_stretch():
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = gamma_feed.with_outages(_filled(start, start + 5 * 3600))
    last = rows[-1][0]
    hedge = [{"ts": start, "hedge_pressure": 1e9}, {"ts": last, "hedge_pressure": -1e9}]
    points = gamma.hedge_points(hedge, [r[0] for r in rows])
    assert [p[0] for p in points] == [0, len(rows) - 1]
    fig = gamma.hedge_figure(hedge, gamma.heatmap_categories(rows), col_ts=[r[0] for r in rows])
    assert fig["xAxis"]["max"] == len(rows) - 1


def test_the_level_tracks_stay_on_their_columns():
    """They are positional, one value per row the service published."""
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = _filled(start, start + 5 * 3600)
    values = [float(i) for i in range(len(rows))]
    shown = gamma_feed.with_outages(rows)
    tracks = gamma_feed.levels_on_columns({"flip": values, "call_wall": None}, shown)
    assert len(tracks["flip"]) == len(shown) and tracks["call_wall"] is None
    for row, level in zip(shown, tracks["flip"]):
        assert (level is None) == _is_blank(row)
    assert [v for v in tracks["flip"] if v is not None] == values


def test_the_bar_size_means_minutes_on_the_feeds_columns():
    _interval, step = _settings()
    assert gamma_feed.bar_columns(15) == round(15 * 60 / step)
    assert gamma_feed.bar_columns(1) >= 1


def test_the_heatmap_uses_the_whole_session_for_the_feed():
    """Rows at 18:00 the evening before and 09:00: both columns appear, in order."""
    rows = [_row(datetime(2026, 11, 8, 18, 0).timestamp()),
            _row(datetime(2026, 11, 9, 9, 0).timestamp())]
    assert gamma.heatmap_figure(rows)["xAxis"]["categories"] == ["18:00", "09:00"]
    shown = gamma_feed.with_outages(rows)
    categories = gamma.heatmap_figure(shown)["xAxis"]["categories"]
    assert categories[0] == "18:00" and categories[-1] == "09:00" and len(categories) > 2


# ── the module's own imports ─────────────────────────────────────────────────

def _imports_of(module):
    out = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            out |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            root = node.module or ""
            if root in ("pages", "shared"):
                out |= {f"{root}.{alias.name}" for alias in node.names}
            elif root != "__future__":
                out.add(root)
    return out


def test_the_new_module_keeps_to_the_allow_list():
    assert _imports_of(gamma_feed) <= {
        "nicegui", "shared.futures_chain", "shared.futures_chain_config", "datetime",
        "pages.fmt", "pages.ui_kit"}


def test_the_page_guide_says_what_the_two_are_and_that_they_can_go_stale():
    import page_help
    guide = page_help.help_md("/options/gamma")
    for words in ("/ES and /NQ", "ThinkorSwim", "calculated", "Stale", "No capture",
                  "one capture"):
        assert words in guide


def test_a_capture_time_reads_in_central_whatever_the_process_zone():
    assert futures_chain.central_time(NOW).strftime("%H:%M %A") == "10:00 Monday"


def test_the_new_module_has_no_inline_style_and_no_colour_of_its_own():
    src = inspect.getsource(gamma_feed)
    assert ".style(" not in src and "style=" not in src and "#" + "ffd" not in src
    code = ast.parse(src)
    strings = [n.value for n in ast.walk(code)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    docs = {ast.get_docstring(n, clean=False) for n in ast.walk(code)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
    assert not [s for s in strings if s not in docs and "#" in s]


# ── no existing symbol changes ───────────────────────────────────────────────

def test_an_existing_symbols_rows_and_columns_do_not_change_at_all():
    start = ct(2026, 11, 9, 8, 30).timestamp()
    # One a minute, with a hole the page has always joined up.
    rows = [_row(start + 60 * i) for i in range(5)] + [_row(start + 7200)]
    page = _page_for("SPY")
    assert page.columns(rows) is rows
    fig = gamma.heatmap_figure(rows)
    assert page.figure(fig, rows) is fig
    tracks = {"flip": [1.0] * len(rows)}
    assert page.levels(tracks, rows) is tracks
    assert page.bar_columns(5) == 5
    assert page.strip("GEX") is None and page.hedge_note({}) is None


def test_a_page_of_an_existing_symbol_validates_the_tables_once(monkeypatch):
    page = _page_for("SPY")
    calls = []
    monkeypatch.setattr(futures_chain, "is_feed_symbol",
                        lambda s: calls.append(s) or False)
    for _ in range(5):
        page.sync(None, "SPY")
        page.paint(None)
    assert calls == []


class _Stub:
    def __init__(self, value=None):
        self.value, self.visible = value, True

    def set_visibility(self, visible):
        self.visible = visible


def _page_for(symbol, view="GEX"):
    from types import SimpleNamespace
    head = SimpleNamespace(stamp=_Stub())
    page = gamma_feed.FeedPage(
        head, _Stub(view), {v: _Stub() for v in gamma._VIEW_ORDER},
        buttons=[_Stub(), None], enabled=False)
    page.enabled = True            # the elements are the page's, not this stub's
    page._line = page._notice = _Stub()
    page._notice_text = _Stub()
    page.sync(None, symbol)
    return page


# ── the page, through the real render ────────────────────────────────────────

def _feed_payload(captured=None, symbol="FOP.ES"):
    captured = captured or datetime.now().timestamp() - 30
    data = {"spot": 7600.0, "strike_count": 2, "gex": {"7600.0": {"call": 2.0, "put": -1.0,
                                                                  "net": 1.0}}}
    return {**snap(captured, symbol=symbol), "spot": 7600.0, "term": {},
            "views": {v: {"data": data, "summary": {}, "walls": [], "flip": None,
                          "levels": {}} for v in ("GEX", "Charm", "DEX", "Vanna")}}


def _rendered(payload, **kw):
    from nicegui import ui
    reset_fake_bus()
    bus_client.reset()
    if payload is not None:
        bus_client.bus().cache_set("cache:options:gamma", payload)
    with ui.card() as card:
        gamma.render(**kw)
    return list(card.descendants())


def _timer(kids, name):
    found = [t.callback for t in kids if type(t).__name__ == "Timer"
             and getattr(t.callback, "__name__", "") == name
             and "render" in getattr(t.callback, "__qualname__", "")]
    assert len(found) == 1, f"timer {name} moved"
    return found[0]


def _load(kids):
    """The page's first paint, in the page's slot, as its timer runs it."""
    async def _go():
        with kids[0].parent_slot.parent:
            await _timer(kids, "_initial_load")()
    asyncio.run(_go())


def _tab(kids, view):
    return next(e for e in kids if type(e).__name__ == "Tab" and e.props.get("name") == view)


def _texts(kids):
    return [e.text for e in kids if type(e).__name__ == "Label"]


def _button(kids, text):
    return next(e for e in kids if type(e).__name__ == "Button"
                and e.props.get("label") == text)


@pytest.fixture
def requests(monkeypatch):
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda domain, command: sent.append(command))
    return sent


def test_the_page_offers_only_what_the_feed_can_show(requests):
    kids = _rendered(_feed_payload())
    _load(kids)
    assert _tab(kids, "Flow").visible is False and _tab(kids, "Open Interest").visible is False
    assert _tab(kids, "GEX").visible is True and _tab(kids, "Net Prem").visible is True
    for name in ("Explain", "Analyze", "Briefings"):
        assert _button(kids, name).visible is False, name


def test_the_page_prints_the_label_and_never_the_internal_name(requests):
    kids = _rendered(_feed_payload())
    _load(kids)
    texts = _texts(kids)
    assert any("Captured" in t for t in texts)
    assert not any("FOP." in t for t in texts)
    assert not any(t.startswith("Session ") or "Next scan" in t for t in texts)


def test_a_view_opened_while_hidden_falls_back_to_gex(requests):
    kids = _rendered(_feed_payload(), tab="flow")
    _load(kids)
    tabs = next(e for e in kids if type(e).__name__ == "Tabs"
                and e.value in gamma._VIEW_ORDER)
    assert tabs.value == "GEX"


def test_a_page_load_of_a_feed_symbol_asks_for_a_refresh(requests):
    kids = _rendered(_feed_payload(symbol="FOP.NQ"))
    assert requests == []
    _load(kids)
    assert requests == [{"type": "gamma_refresh", "args": {"symbol": "FOP.NQ"}}]


def test_a_page_load_of_an_existing_symbol_asks_for_nothing(requests):
    payload = {"symbol": "SPY", "spot": 500.0, "views": {}, "term": {}}
    kids = _rendered(payload)
    _load(kids)
    assert requests == []
    assert _tab(kids, "Flow").visible is True and _tab(kids, "Open Interest").visible is True
    for name in ("Explain", "Analyze", "Briefings"):
        assert _button(kids, name).visible is True, name


def test_the_label_ages_while_the_page_sits_open_with_nothing_new(requests, monkeypatch):
    captured = datetime.now().timestamp() - 30
    kids = _rendered(_feed_payload(captured))
    _load(kids)
    assert not any("Stale" in t for t in _texts(kids))
    limit = futures_chain_config.capture()["stale_after_sec"]
    monkeypatch.setattr(gamma_feed, "_now", lambda: captured + limit + 600)
    with kids[0].parent_slot.parent:
        _timer(kids, "_tick")()
    assert any(t.startswith("Stale: captured") for t in _texts(kids))


def test_the_header_stamp_does_not_call_an_old_capture_updated(requests):
    """A refresh rewrites the key from the same old capture, so its age says nothing
    about the data. The label says that instead."""
    kids = _rendered(_feed_payload())
    _load(kids)
    stamps = [e for e in kids if type(e).__name__ == "Label"
              and "text-xs" in e._classes and e.text.startswith("Updated")]
    assert not [s for s in stamps if s.visible]


def test_the_dropdown_keeps_its_labels_when_the_page_adds_a_symbol(requests):
    """``_set_symbol`` adds the cached symbol to the list. Off the list (the feed ships
    with ``show_in_dropdown`` off) it must still show its label."""
    kids = _rendered(_feed_payload())
    _load(kids)
    sel = next(e for e in kids if type(e).__name__ == "Select"
               and e.props.get("label") == "Symbol")
    assert sel.value == "FOP.ES" and sel.options["FOP.ES"] == "/ES"


def test_the_public_page_builds_no_feed_elements_and_changes_no_row():
    reset_fake_bus()
    bus_client.reset()
    bus_client.bus().cache_set(f"cache:{public_gamma.SYMBOLS_VIEW}", {"symbols": ["$SPX"]})
    from nicegui import ui
    with ui.card() as card:
        gamma.render(public=True)
    kids = list(card.descendants())
    assert not [e for e in kids if type(e).__name__ == "Icon"
                and e.props.get("name") == "schedule"]
    disabled = gamma_feed.FeedPage(None, None, {}, enabled=False)
    disabled.sync({"symbol": "FOP.ES"}, "FOP.ES")
    rows = [(1, None)]
    assert disabled.on is False and disabled.columns(rows) is rows
    assert disabled.strip("GEX") is None and disabled.bar_columns(5) == 5


# ── one column per capture ───────────────────────────────────────────────────

def _gaps(*gaps):
    start = ct(2026, 11, 8, 23, 0).timestamp()
    at = [start]
    for gap in gaps:
        at.append(at[-1] + gap)
    return _filled(*at)


@pytest.mark.parametrize("gaps,blanks", [
    ((300,) * 8, 0),
    ((300, 240, 360, 300, 240, 360, 300, 300), 0),
    ((360, 240) * 4, 0),
    ((450,), 0),
    ((600,), 1),
    ((620,), 1),
    ((900,), 2),
])
def test_only_a_capture_really_missed_is_a_blank(gaps, blanks):
    """A read crossing a minute gives 240 then 360: a capture a minute late."""
    out = gamma_feed.with_outages(_gaps(*gaps))
    assert len([r for r in out if _is_blank(r)]) == blanks
    assert [r[0] for r in out] == sorted(r[0] for r in out)


def test_a_tolerance_below_the_interval_reads_as_the_interval(monkeypatch):
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "outage_after_sec": 30})
    assert gamma_feed.with_outages(_gaps(300, 300)) == _gaps(300, 300)
    assert len(gamma_feed.with_outages(_gaps(360))) == 3


def test_the_tolerance_comes_from_the_config(monkeypatch):
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "outage_after_sec": 700})
    assert gamma_feed.with_outages(_gaps(600)) == _gaps(600)


def test_one_jump_adds_no_more_than_the_cap():
    """A weekend inside the rows would be 780 blank columns at 300 seconds."""
    capture = futures_chain_config.capture()
    start = ct(2026, 11, 6, 15, 0).timestamp()
    monday = ct(2026, 11, 9, 8, 0).timestamp()
    rows = [_row(start), _row(monday)]
    out = gamma_feed.with_outages(rows)
    assert len([r for r in out if _is_blank(r)]) == capture["max_outage_columns"]
    assert out[0] == rows[0] and out[-1] == rows[1]
    assert [r[0] for r in out] == sorted(r[0] for r in out)


def test_a_step_wider_than_the_gap_never_draws_a_column_out_of_order(monkeypatch):
    """A step wider than the interval reads as the interval (``column_step``), so a
    jump past the tolerance has a real column to blank."""
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "fill_step_sec": 600})
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = [_row(start), _row(start + 500)]
    out = gamma_feed.with_outages(rows)
    assert [r[0] for r in out] == sorted(r[0] for r in out)
    assert out[0] == rows[0] and out[-1] == rows[1]
    assert [r[0] for r in out if _is_blank(r)] == [start + 300]


# ── a changed interval: a column is what the rows really are ─────────────────

@pytest.fixture
def two_minutes(monkeypatch):
    """The 2 minutes the design names, set alone in Settings: the step stays 300."""
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "interval_sec": 120, "fallback_interval_sec": 120})


def test_a_five_minute_bar_at_two_minute_captures_spans_its_real_columns(two_minutes):
    assert gamma_feed.bar_columns(5) == 2     # 2.5, to the nearest whole column
    assert gamma_feed.bar_columns(30) == 15


def test_smooth_at_two_minute_captures_is_as_wide_as_it_says(two_minutes):
    assert gamma_feed.smooth_on_columns({"minutes": 15}, [])["minutes"] == 7.5


def test_an_outage_at_two_minute_captures_draws_every_column_that_fits(two_minutes):
    start = ct(2026, 11, 8, 23, 0).timestamp()
    out = gamma_feed.with_outages([_row(start), _row(start + 1800)])
    blanks = [r[0] for r in out if _is_blank(r)]
    assert blanks == [start + 120 * i for i in range(1, 15)]


def test_one_missed_capture_at_two_minutes_is_an_outage(two_minutes):
    """Under the 450 a 300-second interval takes, it would hide a missed capture or two."""
    start = ct(2026, 11, 8, 23, 0).timestamp()
    out = gamma_feed.with_outages([_row(start), _row(start + 120), _row(start + 360)])
    assert [r[0] for r in out if _is_blank(r)] == [start + 240]


def test_a_capture_a_minute_late_at_two_minutes_is_not_an_outage(two_minutes):
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = [_row(start), _row(start + 60), _row(start + 240)]
    assert gamma_feed.with_outages(rows) == rows


def test_the_projection_steps_at_the_real_column_width(two_minutes, monkeypatch):
    from types import SimpleNamespace
    head = SimpleNamespace(stamp=_Stub())
    page = gamma_feed.FeedPage(head, _Stub("GEX"), {v: _Stub() for v in gamma._VIEW_ORDER},
                               enabled=False)
    page.enabled, page.on = True, True
    monkeypatch.setattr(gamma_feed, "reading", lambda _snap, _now: "current")
    assert page.forward({"ts": []}, {})["step"] == 120


def test_a_shipped_page_is_what_it_was():
    assert gamma_feed.bar_columns(5) == 1 and gamma_feed.bar_columns(30) == 6
    assert gamma_feed.smooth_on_columns({"minutes": 15}, [])["minutes"] == 3


# ── a step below the interval: a repeat is not a capture ─────────────────────

@pytest.fixture
def step_100(monkeypatch):
    real = futures_chain_config.capture
    monkeypatch.setattr(futures_chain_config, "capture",
                        lambda: {**real(), "fill_step_sec": 100})


def _on_the_step(*times):
    interval = futures_chain_config.capture()["interval_sec"]
    return futures_chain.fill_rows(
        [_row(t, spot=7600.0 + i) for i, t in enumerate(times)],
        interval, futures_chain_config.column_step())


def test_a_missed_capture_at_a_lowered_step_is_blanked(step_100):
    """Measured from the last repeat it would be 400, under the 450; it is 600."""
    start = ct(2026, 11, 8, 23, 0).timestamp()
    out = gamma_feed.with_outages(_on_the_step(start, start + 600))
    assert [r[0] for r in out if _is_blank(r)] == [start + 300, start + 400, start + 500]
    assert [r[0] for r in out] == [start + 100 * i for i in range(7)]


def test_a_late_capture_at_a_lowered_step_is_not_blanked(step_100):
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = _on_the_step(start, start + 360, start + 600)
    assert gamma_feed.with_outages(rows) == rows


def test_steady_captures_at_a_lowered_step_are_not_blanked(step_100):
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = _on_the_step(start, start + 300, start + 600)
    assert gamma_feed.with_outages(rows) == rows


def test_a_frozen_feed_at_a_lowered_step_is_not_blanked(step_100):
    """The same values capture after capture: each is still a capture. A repeat lasts
    under one interval, so the next row at a whole interval starts a new run."""
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = futures_chain.fill_rows([_row(start + 300 * i) for i in range(4)], 300, 100)
    assert gamma_feed.with_outages(rows) == rows


# ── a label does not carry an old reason through a weekend ───────────────────

def test_a_cause_is_dropped_from_a_last_session_label_between_sessions():
    """Friday's payload holds the cause it was written with, read on Saturday."""
    captured = ct(2026, 11, 6, 13, 55).timestamp()
    payload = snap(captured, frozen=True, why="ThinkorSwim is logged out")
    label = feed_label(payload, ct(2026, 11, 7, 12, 0).timestamp())
    assert label.startswith("Last session") and "logged out" not in label


def test_a_session_that_ended_while_the_payload_sat_drops_its_cause_too():
    captured = ct(2026, 11, 6, 14, 50).timestamp()
    payload = snap(captured, stale=True, why="ThinkorSwim is logged out")
    label = feed_label(payload, ct(2026, 11, 7, 12, 0).timestamp())
    assert "logged out" not in label


def test_a_last_session_label_in_the_next_session_keeps_its_cause():
    captured = ct(2026, 11, 6, 13, 55).timestamp()
    payload = snap(captured, frozen=True, stale=True, why="ThinkorSwim is logged out")
    label = feed_label(payload, ct(2026, 11, 9, 6, 20).timestamp())
    assert "No capture has arrived" in label and "logged out" in label


@pytest.mark.parametrize("when", [(2026, 11, 8, 18, 0), (2026, 11, 9, 5, 59)])
def test_before_the_capture_starts_the_last_session_label_asks_nothing(when):
    captured = ct(2026, 11, 6, 13, 55).timestamp()
    payload = snap(captured, frozen=True, stale=True, why="ThinkorSwim is logged out")
    label = feed_label(payload, ct(*when).timestamp())
    assert "Last session" in label
    assert "No capture has arrived" not in label and "logged out" not in label


def _band(close_in=7200, marks=5, start=None):
    """A projected afternoon, as the service publishes one, from ``start`` (the
    wall clock's minute by default)."""
    start = NOW_LIVE() if start is None else start
    ts = [start + i * close_in // (marks - 1) for i in range(marks)]
    row = [1.0] * marks
    return {"ts": ts, "spot": 7600.0, "call": {"7600.0": row, "7605.0": row},
            "put": {"7600.0": row, "7605.0": row},
            "cone": {"up": [7610.0] * marks, "down": [7590.0] * marks}}


def NOW_LIVE():
    return int(datetime.now().timestamp()) // 60 * 60


def test_the_projection_is_a_minute_a_column_for_every_existing_symbol():
    """Byte for byte: the parameter's default is what the page always drew."""
    from pages.options import gamma_heat
    band = _band()
    after = band["ts"][0]
    default = gamma_heat.forward_series(band, after)
    assert default == gamma_heat.forward_series(band, after, step=60)
    assert default == gamma_heat.forward_series({**band, "step": 60}, after)
    assert [r[0] for r in default["rows"]] == list(
        range(after + 60, band["ts"][-1] + 1, 60))
    rows = [_row(after)]
    stepped = {**band, "step": 60}
    assert gamma.heatmap_categories(rows, band) == gamma.heatmap_categories(rows, stepped)
    fig, same = gamma.heatmap_figure(rows, forward=band), gamma.heatmap_figure(
        rows, forward=stepped)
    assert fig["series"][0]["data"] == same["series"][0]["data"]
    assert fig["xAxis"]["categories"] == same["xAxis"]["categories"]
    assert len(fig["xAxis"]["categories"]) == 1 + 120


def test_the_projection_can_run_at_the_feeds_step_with_the_hedge_panel_beside_it():
    band = {**_band(), "step": 300}       # the band carries it, so neither can differ
    rows = [_row(band["ts"][0])]
    fig = gamma.heatmap_figure(rows, forward=band)
    cats = gamma.heatmap_categories(rows, band)
    assert fig["xAxis"]["categories"] == cats
    assert len(cats) <= 1 + 120 // 5 + 1
    cone = next(s for s in fig["series"] if s["name"] == "EM up")["data"]
    assert len(cone) == len(cats) - 1


def test_smooth_means_the_same_minutes_on_the_feeds_columns():
    _interval, step = _settings()
    out = gamma_feed.smooth_on_columns({"minutes": 3.0, "strikes": 1.0}, [_row(1), _row(2)])
    assert out["minutes"] == 3.0 * 60 / step and out["strikes"] == 1.0


def test_smooth_does_not_cross_a_blank_column():
    from pages.options import gamma_heat
    row = [1.0, 1.0, None, 100.0, 100.0]
    kept = gamma_heat.smooth([row], minutes=2, strikes=0, breaks=frozenset({2}))
    assert kept[0] == pytest.approx([1.0, 1.0, None, 100.0, 100.0])
    # Without a break a gap is still only a gap, as it always was for every symbol.
    assert gamma_heat.smooth([row], minutes=2, strikes=0)[0][1] > 1.0


def test_a_blank_column_stays_blank_through_smoothing_on_the_page():
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = gamma_feed.with_outages(_filled(start, start + interval, start + 3 * interval))
    blank = [i for i, r in enumerate(rows) if _is_blank(r)]
    assert blank
    smooth = gamma_feed.smooth_on_columns({"minutes": 15.0, "strikes": 0.0}, rows)
    assert smooth["breaks"] == frozenset(blank)
    fig = gamma.heatmap_figure(rows, smooth=smooth)
    assert not set(blank) & {x for x, _y, _v in fig["series"][0]["data"]}


def test_the_first_candle_after_a_blank_opens_at_its_own_price():
    bars = gamma.ohlc_bars([100.0, None, 200.0, 210.0], 1, reopen_after_gap=True)
    assert [b[1] for b in bars] == [100.0, 200.0, 200.0]
    assert bars[1][3] == 200.0            # no wick reaching back across the outage
    # A blank at the end of a wider bar breaks it too.
    wide = gamma.ohlc_bars([100.0, None, 200.0, 201.0], 2, reopen_after_gap=True)
    assert wide[1][1] == 200.0


def test_candles_of_an_existing_symbol_still_carry_the_open():
    assert [b[1] for b in gamma.ohlc_bars([100.0, None, 200.0], 1)] == [100.0, 100.0]


def test_a_frozen_label_states_both_times():
    captured = ct(2026, 11, 2, 14, 0).timestamp()
    label = feed_label(snap(captured, frozen=True,
                            last_row_at=ct(2026, 11, 2, 14, 55).timestamp()),
                       ct(2026, 11, 2, 16, 0).timestamp())
    assert label.startswith("Last session. Strike bars as of 14:00 Central on Monday, "
                            "November 2; the heat map runs to 14:55")


def test_a_last_session_with_no_later_rows_states_one_time():
    captured = ct(2026, 11, 2, 14, 50).timestamp()
    label = feed_label(snap(captured, last_row_at=captured),
                       ct(2026, 11, 2, 16, 0).timestamp())
    assert label.startswith("Last session, as of 14:50 Central on Monday, November 2")


# ── the chart itself, through the real render ────────────────────────────────

def _plot(kids, title):
    for chart in kids:
        if type(chart).__name__ == "highchart" or "Highchart" in type(chart).__name__:
            text = ((chart.options.get("title") or {}).get("text") or "")
            if title in text:
                return chart
    raise AssertionError(f"no chart titled {title}")


def _heat_plot(kids):
    return _plot(kids, "intraday")


def _hedge_plot(kids):
    for chart in kids:
        if "Highchart" in type(chart).__name__ or type(chart).__name__ == "highchart":
            series = chart.options.get("series") or []
            if series and str(series[0].get("name", "")).startswith("Hedge"):
                return chart
    raise AssertionError("no hedge panel")


def _outage_rows(now=None):
    """Captures to 20 minutes before ``now`` (the wall clock by default), with a
    2-hour hole before them, as published."""
    interval, _step = _settings()
    last = (NOW_LIVE() if now is None else now) - 60
    after = [last - i * interval for i in range(4)][::-1]
    before = [after[0] - 7200 - i * interval for i in range(4)][::-1]
    return _filled(*before, *after)


def _drawn(rows, *, forward=None, symbol="FOP.ES", captured=None, hedge=None, **settings):
    from nicegui import ui
    import app_settings
    real = app_settings.get
    patched = dict(settings)
    original, app_settings.get = app_settings.get, (
        lambda key, *a: patched[key] if key in patched else real(key, *a))
    try:
        reset_fake_bus()
        bus_client.reset()
        payload = _feed_payload(captured or rows[-1][0], symbol=symbol)
        payload["feed"]["last_row_at"] = rows[-1][0]
        payload["hedge_history"] = hedge or []
        bus_client.bus().cache_set("cache:options:gamma", payload)
        bus_client.bus().cache_set(f"cache:{gamma.history_key('GEX')}", {
            "symbol": symbol, "view": "GEX", "forward": forward,
            "rows": [[*r[:6], {str(k): v for k, v in r[6].items()}] for r in rows]})
        with ui.card() as card:
            gamma.render()
        kids = list(card.descendants())
        _load(kids)
    finally:
        app_settings.get = original
    return kids


def test_an_outage_reaches_the_chart(requests):
    rows = _outage_rows()
    shown = gamma_feed.with_outages(rows)
    blank = [i for i, r in enumerate(shown) if _is_blank(r)]
    assert len(blank) > 10
    hedge = [{"ts": r[0], "hedge_pressure": 1.0e9} for r in rows]
    options = _heat_plot(_drawn(rows, hedge=hedge)).options
    assert len(options["xAxis"]["categories"]) == len(shown)
    assert not set(blank) & {x for x, _y, _v in options["series"][0]["data"]}
    spot = next(s for s in options["series"] if s["name"] == "Spot")["data"]
    assert [x for x, y in spot if y is None] == [blank[0]]
    bands = options["xAxis"]["plotBands"]
    assert [(b["from"], b["to"]) for b in bands] == [(blank[0] - 0.5, blank[-1] + 0.5)]
    assert bands[0]["label"]["text"] == "No capture" and bands[0]["color"] == gamma.OUTAGE_COLOR


def test_the_hedge_panel_has_the_heat_maps_columns(requests):
    rows = _outage_rows()
    hedge = [{"ts": r[0], "hedge_pressure": 1.0e9} for r in rows]
    kids = _drawn(rows, hedge=hedge)
    heat, panel = _heat_plot(kids).options, _hedge_plot(kids).options
    assert panel["xAxis"]["categories"] == heat["xAxis"]["categories"]
    assert len(panel["xAxis"]["categories"]) == len(gamma_feed.with_outages(rows))


def test_the_level_tracks_reach_the_chart_on_their_columns(requests):
    rows = _outage_rows()
    shown = gamma_feed.with_outages(rows)
    reset_fake_bus()
    payload_levels = {"flip": [float(i) for i in range(len(rows))]}
    original = _feed_payload
    def with_levels(*a, **k):
        out = original(*a, **k)
        out["views"]["GEX"]["levels"] = payload_levels
        return out
    globals()["_feed_payload"] = with_levels
    try:
        options = _heat_plot(_drawn(rows, gamma_level_tracks=True)).options
    finally:
        globals()["_feed_payload"] = original
    track = next(s for s in options["series"] if s["name"] == "Flip track")["data"]
    columns = {x for x, y in track if y is not None}
    assert not columns & {i for i, r in enumerate(shown) if _is_blank(r)}
    assert max(columns) == len(shown) - 1


def test_the_bar_size_reaches_the_chart_in_minutes(requests):
    rows = _outage_rows()
    options = _heat_plot(_drawn(rows, gamma_spot_style="candle", gamma_spot_interval=15)).options
    body = next(s for s in options["series"] if s["name"] == "Spot candles")["data"]
    columns = gamma_feed.bar_columns(15)
    assert columns == 3
    spots = [r[1] for r in gamma_feed.with_outages(rows)]
    assert [p["x"] for p in body] == [bar[0] for bar in gamma.ohlc_bars(spots, columns, True)]
    assert len(body) > 2                  # 15 columns would be a very different picture


def test_smooth_reaches_the_chart_in_minutes(requests):
    from pages.options import gamma_heat
    seen = {}
    real = gamma_heat.smooth
    def spy(z, **kwargs):
        seen.update(kwargs)
        return real(z, **kwargs)
    gamma_heat.smooth = spy
    try:
        _drawn(_outage_rows(), gamma_heat_smooth="medium")
    finally:
        gamma_heat.smooth = real
    _interval, step = _settings()
    assert seen["minutes"] == gamma_heat_medium_minutes() * 60 / step
    assert seen["breaks"]


def gamma_heat_medium_minutes():
    from pages.options import gamma_heat
    from shared import gamma_heat_config
    return gamma_heat.smooth_args("medium", gamma_heat_config.smooth())["minutes"]


def test_a_current_payload_draws_the_projection_at_the_feeds_step(requests):
    rows = _outage_rows()
    band = _band()
    options = _heat_plot(_drawn(rows, forward=band)).options
    shown = len(gamma_feed.with_outages(rows))
    assert any(line.get("className") == "gamma-now-divider"
               for line in options["xAxis"]["plotLines"])
    assert len(options["xAxis"]["categories"]) <= shown + 120 // 5 + 1


def test_a_stale_payload_draws_no_now_and_no_projection(requests, monkeypatch):
    """Capture stopped hours ago: those hours are not a forecast from "now".

    The clock is pinned mid-session. On the wall clock, from 15:00 to about 18:00
    Central a capture three hours old belongs to the session that has just ENDED,
    which the page rightly calls "Last session", not "Stale" (CI failed at 17:10)."""
    now = int(NOW) // 60 * 60
    monkeypatch.setattr(gamma_feed, "_now", lambda: now)
    rows = _outage_rows(now)
    old = rows[-1][0] - 10800
    kept = [r for r in rows if r[0] <= old] or rows[:1]
    assert gamma_feed.reading(_feed_payload(old), now) == "stale"
    kids = _drawn(kept, forward=_band(start=now), hedge=[{"ts": kept[0][0], "hedge_pressure": 1.0e9}],
                  captured=old)
    options = _heat_plot(kids).options
    assert not options["xAxis"]["plotLines"]
    assert next(s for s in options["series"] if s["name"] == "EM up")["data"] == []
    shown = gamma_feed.with_outages(kept)
    assert len(options["xAxis"]["categories"]) == len(shown)
    assert _hedge_plot(kids).options["xAxis"]["categories"] == options["xAxis"]["categories"]
    assert any(t.startswith("Stale") for t in _texts(kids))


def test_the_chart_redraws_when_the_payload_goes_stale_with_the_page_open(requests, monkeypatch):
    rows = _outage_rows()
    kids = _drawn(rows, forward=_band())
    plot = _heat_plot(kids)
    assert plot.options["xAxis"]["plotLines"]
    limit = futures_chain_config.capture()["stale_after_sec"]
    monkeypatch.setattr(gamma_feed, "_now", lambda: rows[-1][0] + limit + 60)
    with kids[0].parent_slot.parent:
        _timer(kids, "_tick")()
    assert not plot.options["xAxis"]["plotLines"]


def _poll(kids):
    async def _go():
        with kids[0].parent_slot.parent:
            await _timer(kids, "_poll")()
    asyncio.run(_go())


def test_the_same_capture_republished_does_not_repaint_the_chart(requests):
    rows = _outage_rows()
    kids = _drawn(rows)
    plot = _heat_plot(kids)
    before = plot.options
    payload = _feed_payload(rows[-1][0])
    payload["feed"]["last_row_at"] = rows[-1][0]
    payload["feed"]["why"] = "ThinkorSwim is logged out"
    bus_client.bus().cache_set("cache:options:gamma", payload)
    _poll(kids)
    assert plot.options is before
    assert any("ThinkorSwim is logged out" in t for t in _texts(kids))


def test_a_republish_of_the_last_symbol_ends_no_wait_for_the_one_picked(requests):
    rows = _outage_rows()
    kids = _drawn(rows)
    picker = next(e for e in kids if type(e).__name__ == "Select"
                  and e.props.get("label") == "Symbol")
    picker.value = "SPY"
    payload = _feed_payload(rows[-1][0])
    payload["feed"]["last_row_at"] = rows[-1][0]
    payload["feed"]["why"] = "ThinkorSwim is logged out"
    bus_client.bus().cache_set("cache:options:gamma", payload)
    _poll(kids)
    assert not any("ThinkorSwim is logged out" in t for t in _texts(kids))


def test_a_new_capture_does_repaint_the_chart(requests):
    rows = _outage_rows()
    kids = _drawn(rows)
    plot = _heat_plot(kids)
    before = plot.options
    newer = rows[-1][0] + 300
    payload = _feed_payload(newer)
    payload["feed"]["last_row_at"] = newer
    bus_client.bus().cache_set("cache:options:gamma", payload)
    bus_client.bus().cache_set(f"cache:{gamma.history_key('GEX', 'FOP.ES')}", {
        "symbol": "FOP.ES", "view": "GEX", "forward": None,
        "rows": [[*r[:6], {str(k): v for k, v in r[6].items()}]
                 for r in (*rows, _row(newer))]})
    _poll(kids)
    assert plot.options is not before


def test_a_repeat_of_an_existing_symbol_still_repaints():
    page = _page_for("SPY")
    state = {"snap": {"symbol": "SPY"}, "hist": {"GEX": [1]}}
    assert page.keep_chart(state, {"symbol": "SPY"}, "GEX", lambda: None) is False


def test_explain_sends_nothing_for_a_feed_symbol_picked_before_its_payload(requests):
    payload = {"symbol": "SPY", "spot": 500.0, "views": {}, "term": {}}
    kids = _rendered(payload)
    _load(kids)
    sel = next(e for e in kids if type(e).__name__ == "Select"
               and e.props.get("label") == "Symbol")
    sel.options = {**dict.fromkeys(sel.options, "x"), "FOP.ES": "/ES"} if isinstance(
        sel.options, dict) else [*sel.options, "FOP.ES"]
    sel.update()
    sel.value = "FOP.ES"
    requests.clear()
    button = _button(kids, "Explain")
    assert button.visible is True          # the page has not heard yet
    listeners = [ls.handler for ls in button._event_listeners.values() if ls.type == "click"]
    with kids[0].parent_slot.parent:
        for handler in listeners:
            handler(None) if inspect.signature(handler).parameters else handler()
    assert not [c for c in requests if c["type"] == "gamma_explain"]


def test_the_bar_pickers_tooltip_says_capture_for_the_feed_only(requests):
    def tip(kids):
        picker = next(e for e in kids if type(e).__name__ == "Select"
                      and e.props.get("label") == "Bar")
        return next(c.text for c in picker.parent_slot.children
                    if type(c).__name__ == "Tooltip"
                    and c.props.get("target") == f"#{picker.html_id}")
    feed_kids = _rendered(_feed_payload())
    _load(feed_kids)
    assert "once a minute" not in tip(feed_kids) and "capture" in tip(feed_kids)
    spy_kids = _rendered({"symbol": "SPY", "spot": 500.0, "views": {}, "term": {}})
    _load(spy_kids)
    assert "once a minute" in tip(spy_kids)


def test_a_changed_tooltip_shape_leaves_the_page_working(requests, monkeypatch):
    """The Bar picker's tooltip is found in NiceGUI's own element tree. If that shape
    changes the words stay the stock market's: the private page must still open, for
    every symbol, and not raise out of the render."""
    from types import SimpleNamespace

    class NotTheTooltip:
        pass
    monkeypatch.setattr(gamma_feed, "ui",
                        SimpleNamespace(tooltip=NotTheTooltip, label=gamma_feed.ui.label))
    for payload in (_feed_payload(), {"symbol": "SPY", "spot": 500.0, "views": {}, "term": {}}):
        kids = _rendered(payload)
        _load(kids)
        assert any(type(e).__name__ == "Select" and e.props.get("label") == "Bar" for e in kids)


def test_no_rows_never_names_a_collector_for_the_feed(requests):
    kids = _rendered(_feed_payload())
    _load(kids)
    assert not any("collector" in (t or "") for t in _texts(kids))


def test_an_existing_symbol_keeps_its_collector_sentence(requests):
    assert gamma_feed.FeedPage(None, None, {}, enabled=False).no_rows_text() is None


def test_a_switch_back_clears_the_bands_from_the_chart_in_place():
    """An in-place update merges options: a key left out keeps the last symbol's."""
    page = _page_for("FOP.ES")
    interval, _step = _settings()
    start = ct(2026, 11, 8, 23, 0).timestamp()
    rows = gamma_feed.with_outages(_filled(start, start + 3 * interval))
    assert page.figure({"xAxis": {}, "series": []}, rows)["xAxis"]["plotBands"]
    page.sync({"symbol": "SPY"}, "SPY")
    assert page.figure({"xAxis": {}, "series": []}, [_row(start)])["xAxis"]["plotBands"] == []
    fig = {"xAxis": {}, "series": []}
    assert "plotBands" not in page.figure(fig, [_row(start)])["xAxis"]
