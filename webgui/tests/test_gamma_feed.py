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


def ct(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_CT)


NOW = ct(2026, 11, 9, 10, 0).timestamp()   # a Monday morning, mid session


def _expiry_of(captured):
    return futures_chain.session_expiry(datetime.fromtimestamp(captured, _CT)).isoformat()


def snap(captured, *, stale=False, frozen=False, reason="", strike_low=None,
         strike_high=None, expiry=None, symbol="FOP.ES"):
    """A payload holding only what a label is built from."""
    return {"symbol": symbol, "dte": 1,
            "feed": {"label": futures_chain.display_symbol(symbol), "captured_at": captured,
                     "expiry": expiry or _expiry_of(captured), "frozen": frozen,
                     "stale": stale, "reason": reason, "strike_low": strike_low,
                     "strike_high": strike_high}}


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

def test_flow_and_term_are_hidden_for_the_feed_only():
    assert hidden_views("FOP.NQ") == {"Flow", "Term"} and hidden_views("SPY") == frozenset()


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


def test_reason_is_carried_on_the_label():
    assert "not changing" in feed_label(
        snap(NOW - 60, stale=True, reason="values not changing"), NOW)


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
    payload = snap(NOW - 20000, stale=True,
                   reason="The last capture is 20 minutes old. ThinkorSwim is logged out")
    payload["feed"]["why"] = "ThinkorSwim is logged out"
    label = feed_label(payload, NOW)
    assert "20 minutes" not in label and "ThinkorSwim is logged out" in label


def test_no_label_is_built_from_dte():
    """``dte`` reads 1 on an evening capture: right for the data, wrong as words."""
    one = snap(NOW - 60)
    other = {**one, "dte": 0}
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
    guide = page_help.HELP["/options/gamma"] if hasattr(page_help, "HELP") else None
    guide = guide or inspect.getsource(page_help)
    for words in ("/ES and /NQ", "ThinkorSwim", "calculated", "Stale", "blank stretch"):
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
    assert _tab(kids, "Flow").visible is False and _tab(kids, "Term").visible is False
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
    assert _tab(kids, "Flow").visible is True and _tab(kids, "Term").visible is True
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
