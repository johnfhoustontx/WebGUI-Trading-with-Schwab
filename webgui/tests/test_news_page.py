"""The /news page: route, rail, help, and the enqueue gate."""
import ast
import datetime as dt
import pathlib

PAGES = pathlib.Path(__file__).resolve().parents[1] / "pages"


def test_route_rail_and_breadcrumb():
    import main
    assert main.breadcrumb_trail("/news") == ["Markets", "Market News"]
    assert ("/news", "Market News", "newspaper") in main.OPTIONS_RAIL


def test_help_exists():
    import page_help
    assert "/news" in page_help.HELP_MD


def test_the_only_enqueue_is_gated_by_may_enqueue():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "request"]
    assert len(calls) == 1
    assert "_may_enqueue = _shell.may_enqueue()" in src
    assert "if _may_enqueue" in src


def test_public_render_hands_off_before_building():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    assert "if public:\n        from . import news_live" in src.replace("from pages import news_live", "from . import news_live")


# ── beyond the plan ──────────────────────────────────────────────────────────
def test_feed_text_never_reaches_ui_html():
    """Titles and teasers are third-party text. They go through escaping
    widgets (labels, links) only — news.py never calls ``ui.html`` at all."""
    tree = ast.parse((PAGES / "news.py").read_text(encoding="utf-8"))
    html_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute) and n.func.attr == "html"]
    assert html_calls == []


NOW = dt.datetime(2026, 9, 26, 15, 0, tzinfo=dt.timezone.utc)


def _item(i, *, tickers=(), source="MarketWatch", hours_ago=0.5, title=None,
          teaser="", kind="rss", detail=None):
    ts = (NOW - dt.timedelta(hours=hours_ago)).isoformat()
    return {"id": f"i{i}", "source": source, "sources": [source],
            "title": title or f"Headline {i}", "teaser": teaser,
            "url": f"https://example.com/{i}", "published_at": ts,
            "first_seen": ts, "tickers": list(tickers), "kind": kind,
            "topics": [], "detail": detail or {}}


def _new_elements(before):
    from nicegui import ui
    return [e for k, e in ui.context.client.elements.items() if k not in before]


def test_draw_rows_links_the_headline_and_the_tickers_when_linked():
    from nicegui import ui

    from pages import news, news_view as nv
    rows = nv.rows({"items": [_item(1, tickers=["NVDA"], teaser="<b>hi</b>")]},
                   now=NOW)
    before = set(ui.context.client.elements)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    links = [e for e in _new_elements(before) if isinstance(e, ui.link)]
    by_text = {e.text: e for e in links}
    assert by_text["Headline 1"].props["href"] == "https://example.com/1"
    assert by_text["Headline 1"].props.get("target") == "_blank"
    assert by_text["NVDA"].props["href"] == "/symbol?symbol=NVDA"
    labels = [e.text for e in _new_elements(before) if isinstance(e, ui.label)]
    assert rows[0]["when"] in labels
    assert "MarketWatch" in labels
    assert "<b>hi</b>" in labels          # a label escapes; never rendered as HTML


def test_draw_rows_unlinked_ticker_is_a_chip_that_calls_on_ticker():
    from nicegui import ui

    from pages import news, news_view as nv
    rows = nv.rows({"items": [_item(1, tickers=["AAPL"])]}, now=NOW)
    picked = []
    before = set(ui.context.client.elements)
    box = ui.column()
    news.draw_rows(box, rows, linked=False, on_ticker=picked.append)
    new = _new_elements(before)
    assert not [e for e in new if isinstance(e, ui.link) and e.text == "AAPL"]
    chips = [e for e in new if getattr(e, "text", None) == "AAPL"]
    assert chips
    for lst in chips[0]._event_listeners.values():
        if lst.type == "click":
            lst.handler(None)
    assert picked == ["AAPL"]


def test_draw_rows_shows_the_form4_detail_when_there_is_no_teaser():
    from nicegui import ui

    from pages import news, news_view as nv
    it = _item(1, tickers=["NVDA"], kind="edgar_form4",
               detail={"groups": [{}, {}], "total_value": 136_400_000.0,
                       "transaction_date": "2026-09-23"})
    rows = nv.rows({"items": [it]}, now=NOW)
    before = set(ui.context.client.elements)
    news.draw_rows(ui.column(), rows, linked=True, on_ticker=lambda t: None)
    labels = [e.text for e in _new_elements(before) if isinstance(e, ui.label)]
    assert "2 purchases · $136.4M · 2026-09-23" in labels


def _render(monkeypatch, payload):
    import app_settings
    import bus_client
    from nicegui import ui

    from pages import news
    monkeypatch.setattr(bus_client, "read", lambda v: payload)
    monkeypatch.setattr(bus_client, "read_full", lambda v: (payload, 1 if payload else None))
    monkeypatch.setattr(bus_client, "read_version", lambda v: 1 if payload else None)
    saved = {}
    monkeypatch.setattr(app_settings, "set", lambda k, v: saved.__setitem__(k, v))
    before = set(ui.context.client.elements)
    news.render()
    return _new_elements(before), saved


def test_a_cold_feed_says_nothing_has_been_published(monkeypatch):
    from nicegui import ui

    from pages import news
    new, saved = _render(monkeypatch, None)
    texts = [e.text for e in new if isinstance(e, ui.label)]
    assert news.WAITING in texts
    from pages import copy as shared_copy
    assert news.WAITING == shared_copy.WAITING_NEWS  # the ONE shared line
    assert "news_seen_ts" not in saved              # nothing seen yet


def test_a_published_feed_draws_its_rows_and_marks_them_seen(monkeypatch):
    from nicegui import ui
    payload = {"items": [_item(1, tickers=["NVDA"]), _item(2, source="CNBC")]}
    new, saved = _render(monkeypatch, payload)
    link_texts = [e.text for e in new if isinstance(e, ui.link)]
    assert "Headline 1" in link_texts and "Headline 2" in link_texts
    assert "news_seen_ts" in saved
    dt.datetime.fromisoformat(saved["news_seen_ts"])


def test_the_filter_that_matches_nothing_says_so():
    from pages import news
    assert news.NO_MATCH != news.WAITING
    assert "filter" in news.NO_MATCH.lower()


def test_trending_window_survives_a_malformed_config(monkeypatch):
    from pages import news
    from shared import news_config as nc
    for bad in ("six", None, -3, 0, True, float("nan"), [6]):
        monkeypatch.setattr(nc, "load", lambda bad=bad: {"trending": {"window_h": bad}})
        assert news.trending_window_h() == news.DEFAULT_WINDOW_H
    monkeypatch.setattr(nc, "load", lambda: {"trending": "junk"})
    assert news.trending_window_h() == news.DEFAULT_WINDOW_H
    monkeypatch.setattr(nc, "load", lambda: {"trending": {"window_h": 12}})
    assert news.trending_window_h() == 12


def test_refresh_is_drawn_only_where_this_process_may_enqueue(monkeypatch):
    import shell
    from nicegui import ui
    payload = {"items": [_item(1)]}
    new, _ = _render(monkeypatch, payload)
    assert [e for e in new if isinstance(e, ui.button) and e.text == "Refresh"]
    monkeypatch.setattr(shell, "may_enqueue", lambda: False)
    new, _ = _render(monkeypatch, payload)
    assert not [e for e in new if isinstance(e, ui.button) and e.text == "Refresh"]


def test_only_a_web_url_becomes_a_link():
    """A feed's URL is third-party too: a ``javascript:`` or ``data:`` target
    must never become a clickable link — the title renders as plain text."""
    from nicegui import ui

    from pages import news, news_view as nv
    it = _item(1, title="Bad link")
    it["url"] = "javascript:alert(1)"
    rows = nv.rows({"items": [it]}, now=NOW)
    before = set(ui.context.client.elements)
    news.draw_rows(ui.column(), rows, linked=True, on_ticker=lambda t: None)
    new = _new_elements(before)
    assert not [e for e in new if isinstance(e, ui.link)]
    assert "Bad link" in [e.text for e in new if isinstance(e, ui.label)]


def test_refresh_holds_its_button_until_the_poll_reports(monkeypatch):
    """Refresh enqueues one poll of every feed, which takes minutes. The button
    spins until the service publishes ``news:status`` (it does at the end of
    every poll, even one that found nothing new) or the backstop passes."""
    import bus_client
    from nicegui import ui

    from pages import news
    sent = []
    monkeypatch.setattr(bus_client, "request", lambda d, c: sent.append((d, c)))
    watched = {}
    import pages.view_watch as vw
    monkeypatch.setattr(vw, "watch_view",
                        lambda view, fn, **k: watched.__setitem__(view, fn))
    new, _ = _render(monkeypatch, {"items": [_item(1)]})
    (btn,) = [e for e in new if isinstance(e, ui.button) and e.text == "Refresh"]
    for lst in btn._event_listeners.values():
        if lst.type == "click":
            lst.handler(None)
    assert sent == [("news", {"type": "news_refresh"})]
    assert "loading" in btn.props and not btn.enabled
    assert news.REFRESH_TIMEOUT_SEC >= 120
    watched[news.nv.VIEW_STATUS]()
    assert "loading" not in btn.props and btn.enabled


# ── review fixes (b013d39) ───────────────────────────────────────────────────
def test_an_unpublished_symbol_route_leaves_the_tickers_as_chips(monkeypatch):
    """``route_for`` answers None where this origin does not serve the dossier.
    Falling back to ``/symbol`` would link to a path that 404s here - the ticker
    becomes the filter chip instead."""
    import shell
    from nicegui import ui

    from pages import news, news_view as nv
    monkeypatch.setattr(shell, "route_for", lambda route: None)
    rows = nv.rows({"items": [_item(1, tickers=["NVDA"])]}, now=NOW)
    picked = []
    before = set(ui.context.client.elements)
    news.draw_rows(ui.column(), rows, linked=True, on_ticker=picked.append)
    new = _new_elements(before)
    assert not [e for e in new if isinstance(e, ui.link) and e.text == "NVDA"]
    assert not [e for e in new if isinstance(e, ui.link)
                and "/symbol" in str(e.props.get("href"))]
    (chip,) = [e for e in new if getattr(e, "text", None) == "NVDA"]
    for lst in chip._event_listeners.values():
        if lst.type == "click":
            lst.handler(None)
    assert picked == ["NVDA"]


def test_the_ticker_field_is_debounced(monkeypatch):
    """Each keystroke would otherwise rebuild up to 60 rows."""
    from nicegui import ui
    new, _ = _render(monkeypatch, {"items": [_item(1)]})
    (field,) = [e for e in new if isinstance(e, ui.input)]
    assert int(field.props["debounce"]) == 300


def test_the_time_column_fits_a_dated_stamp_on_one_line():
    """"Sep 25 10:43 AM" must not wrap: the column is w-28 and nowrap. (The
    second line it used to indent past is gone - see test_second_line_is_gone.)"""
    from pages import news
    assert "w-28" in news._WHEN and "whitespace-nowrap" in news._WHEN


def test_the_empty_feed_line_is_one_constant_on_both_origins():
    """A feed that is up but empty reads the same on the private page and the
    public one: one module-level line in news.py, and no copy of it anywhere."""
    from pages import news, news_live
    assert isinstance(news.EMPTY_FEED, str) and news.EMPTY_FEED
    assert news_live.EMPTY_FEED is news.EMPTY_FEED
    for name in ("news.py", "news_live.py"):
        src = (PAGES / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        literals = [n for n in ast.walk(tree) if isinstance(n, ast.Constant)
                    and n.value == news.EMPTY_FEED]
        assert len(literals) == (1 if name == "news.py" else 0), name
        assert "EMPTY_FEED" in src


# ── v2: one-line rows, the SEC panel and the calendar (Task 18) ─────────────
def _classes(e):
    return set(e.classes)


def _descendants(e):
    out = []
    for slot in e.slots.values():
        for c in slot.children:
            out.append(c)
            out.extend(_descendants(c))
    return out


def _items_payload(*items):
    return {"items": list(items)}


def test_second_line_is_gone():
    from pages import news
    assert not hasattr(news, "second_line") and "_SECOND" not in vars(news)
    assert not hasattr(news, "_echoes_title")


def test_rows_draw_one_line_with_time_impact_tickers_headline_source():
    from nicegui import ui

    from pages import news, news_view as nv
    it = {**_item(1, tickers=["NVDA"], teaser="Chips rallied on the news."),
          "impact": {"band": "high", "score": 7, "reasons": ["kw:tier1:FOMC"]}}
    rows = nv.rows(_items_payload(it, _item(2)), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    kids = box.default_slot.children
    assert len(kids) == 3                     # the column header, then one per item
    for row_el in kids[1:]:
        assert isinstance(row_el, ui.row)
        assert "flex-nowrap" in _classes(row_el)             # one line, never wraps
        # no nested row or column: nothing can drop to a second line
        assert not [d for d in _descendants(row_el)
                    if isinstance(d, (ui.row, ui.column))]
    first = kids[1].default_slot.children
    texts = [getattr(c, "text", None) for c in first]
    # time · impact · [tickers] · headline · [source], in that order
    assert texts[:2] == [rows[0]["when"], "H"] and texts[3] == "Headline 1"
    assert len(first) == 5
    assert [c.text for c in first[2].default_slot.children] == ["NVDA"]
    assert [d.text for d in _descendants(first[4]) if isinstance(d, ui.label)
            and not isinstance(d.parent_slot.parent, ui.tooltip)] == ["MarketWatch"]
    pill = first[1]
    assert set(nv.BAND_CLASSES["high"].split()) <= _classes(pill)
    tip = [d for d in _descendants(pill) if isinstance(d, ui.tooltip)]
    # the reason code reads as a phrase (news_view.reason_text), not a code
    assert tip and tip[0].text == "mentions FOMC"
    headline = first[3]
    assert {"truncate", "min-w-0", "flex-1"} <= _classes(headline)
    assert "shrink-0" in _classes(first[4])                  # the source cell
    # a row with no band keeps an EMPTY slot of the pill's width, never "L"
    second = kids[2].default_slot.children
    assert getattr(second[1], "text", "") in ("", None)
    assert "w-5" in _classes(second[1])
    # the teaser is on the headline's hover, not on a second line
    hover = [d for d in _descendants(headline) if isinstance(d, ui.label)]
    assert [h.text for h in hover] == ["Chips rallied on the news."]


def test_a_teaser_equal_to_the_headline_adds_no_hover():
    from pages import news
    assert news.hover_text({"title": "Costco Beats", "teaser": "  costco   BEATS "}) == ""
    assert news.hover_text({"title": "X", "teaser": "", "kind": "edgar_filings",
                            "detail": {"form": "S-3"}}) == "Form S-3"
    assert news.hover_text(None) == ""


def test_the_band_filter_hides_lower_bands(monkeypatch):
    from nicegui import ui
    items = [{**_item(i, title=f"B{band}"), "impact": {"band": band, "score": 1,
                                                       "reasons": []}}
             for i, band in enumerate(("high", "med", "low"))]
    items.append(_item(9, title="Bnone"))
    from pages import news_view as nv
    # the feed view only: the band filter is the headline list's, not the SEC panel's
    new, _ = _render_views(monkeypatch, {nv.VIEW: _items_payload(*items)})
    sels = [e for e in new if isinstance(e, ui.select)
            and isinstance(e.options, dict) and "med" in e.options]
    assert len(sels) == 1
    band_sel = sels[0]
    assert band_sel.options == {"all": "All", "high": "High", "med": "High + Med"}
    assert band_sel.value == "all"

    def _titles():
        return {e.text for e in ui.context.client.elements.values()
                if isinstance(e, ui.link) and e.text.startswith("B")}
    assert _titles() == {"Bhigh", "Bmed", "Blow", "Bnone"}
    band_sel.value = "med"
    assert _titles() == {"Bhigh", "Bmed"}
    band_sel.value = "high"
    assert _titles() == {"Bhigh"}
    band_sel.value = "all"
    assert _titles() == {"Bhigh", "Bmed", "Blow", "Bnone"}


SEC_ITEM = {**_item(50, tickers=["ACME"], kind="edgar_form4",
                    title="ACME insider buys $136.4M",
                    detail={"groups": [{}] * 9, "total_value": 136_400_000.0,
                            "transaction_date": "2026-09-23"}),
            "impact": {"band": "med", "score": 4, "reasons": ["form4:size"]}}
CAL_PAYLOAD = {
    "events": [{"title": "FOMC statement", "at": "2026-10-28T18:00:00+00:00"}],
    "dividends": [{"symbol": "JPM", "ex_date": "2026-10-06", "amount": 1.4}],
    "ipos": [], "data": [], "sources": {"fed": "ok"},
}


def _render_views(monkeypatch, by_view):
    import app_settings
    import bus_client
    from nicegui import ui

    from pages import news
    reads = []
    monkeypatch.setattr(bus_client, "read", lambda v: reads.append(v) or by_view.get(v))
    monkeypatch.setattr(bus_client, "read_version", lambda v: 1)
    monkeypatch.setattr(app_settings, "set", lambda k, v: None)
    before = set(ui.context.client.elements)
    news.render()
    return _new_elements(before), reads


def test_the_sec_panel_reads_the_sec_view_and_the_calendar_its_view(monkeypatch):
    import pages.view_watch as vw
    from pages import news_view as nv
    watched = []
    monkeypatch.setattr(vw, "watch_view", lambda view, fn, **k: watched.append(view))
    _, reads = _render_views(monkeypatch, {})
    assert {nv.VIEW, nv.VIEW_SEC, nv.VIEW_CAL} <= set(reads)
    assert {nv.VIEW, nv.VIEW_SEC, nv.VIEW_CAL} <= set(watched)
    # never a public view, never the calendar's private error-text status
    assert not {nv.VIEW_PUBLIC, nv.VIEW_SEC_PUBLIC, nv.VIEW_CAL_PUBLIC} & set(reads)


def test_three_regions_render_in_dom_order(monkeypatch):
    from nicegui import ui

    from pages import news, news_view as nv
    new, _ = _render_views(monkeypatch, {
        nv.VIEW: _items_payload(_item(1, title="A headline")),
        nv.VIEW_SEC: _items_payload(SEC_ITEM),
        nv.VIEW_CAL: CAL_PAYLOAD,
    })
    # DOM order (a depth-first walk from the page column), not creation order:
    # the regions are built first and painted afterwards.
    ids = {x.id for x in new}
    tops = [e for e in new if e.parent_slot is not None
            and e.parent_slot.parent.id not in ids]
    root = max(tops, key=lambda e: len(_descendants(e)))     # the page column
    order = [e for e in [root, *_descendants(root)]
             if isinstance(e, (ui.label, ui.link))]
    texts = [e.text for e in order]
    i_head = texts.index("A headline")
    i_sec = texts.index(news.SEC_TITLE)
    i_cal = texts.index(news.CAL_TITLE)
    assert i_head < i_sec < i_cal
    # the headline list's own column header comes before its first row
    cols = [texts.index(c) for c in news.LIST_COLUMNS]
    assert cols == sorted(cols) and cols[-1] < i_head
    # the SEC panel's own columns (searched from the panel on: the headline
    # list has a "Symbol" column too), and the ONE line per filing
    for col in ("Date/Time", "Symbol", "Headline/Details"):
        assert i_sec < texts.index(col, i_sec) < i_cal
    assert i_sec < texts.index("ACME insider buys $136.4M") < i_cal
    assert i_sec < texts.index("9 purchases · $136.4M · 2026-09-23") < i_cal
    # the calendar's three headers, in order, after the SEC panel
    heads = [texts.index(t) for t in ("Economic news/Calendar", "Dividend / IPO",
                                      "Economic data (CPI, PPI etc)")]
    assert i_cal < heads[0] < heads[1] < heads[2]
    assert "Wed Oct 28 · 1:00 PM CT" in texts          # Central time on the tile
    assert "JPM dividend" in texts
    # the grid: headlines left (3 of 5), the two right panels scroll at lg
    grids = [e for e in new if "lg:grid-cols-5" in e.classes]
    assert len(grids) == 1
    panels = [e for e in new if "lg:h-[calc(50vh-5rem)]" in e.classes]
    assert len(panels) == 2 and all("overflow-y-auto" in p.classes for p in panels)
    # the headline column is as tall as both right panels plus their gap, and
    # its list (not the control bar) scrolls inside it
    lefts = [e for e in new if "lg:h-[calc(100vh-9.25rem)]" in e.classes]
    assert len(lefts) == 1
    lists = [e for e in new if "lg:overflow-y-auto" in e.classes]
    assert len(lists) == 1 and "lg:min-h-0" in lists[0].classes
    assert lists[0] in _descendants(lefts[0])


def test_the_left_column_height_is_the_two_panels_and_their_gap():
    import re

    from pages import news
    panel = re.search(r"lg:h-\[calc\(50vh-(\d+(?:\.\d+)?)rem\)\]", news._PANEL)
    left = re.search(r"lg:h-\[calc\(100vh-(\d+(?:\.\d+)?)rem\)\]", news._LEFT)
    assert panel and left and "gap-3" in news._RIGHT.split()
    assert float(left.group(1)) == 2 * float(panel.group(1)) - 0.75


def test_the_sec_row_links_its_symbol_and_leads_with_the_pill():
    from nicegui import ui

    from pages import news, news_view as nv
    rows = nv.sec_rows(_items_payload(SEC_ITEM), now=NOW)
    box = ui.column()
    news.draw_sec_rows(box, rows, linked=True, on_ticker=lambda t: None)
    kids = box.default_slot.children
    assert len(kids) == 2                                  # the header + one row
    row_el = kids[1]
    assert "flex-nowrap" in row_el.classes
    links = {d.text: d for d in _descendants(row_el) if isinstance(d, ui.link)}
    assert links["ACME"].props["href"] == "/symbol?symbol=ACME"
    labels = [d.text for d in _descendants(row_el) if isinstance(d, ui.label)]
    assert labels.index("M") < labels.index("9 purchases · $136.4M · 2026-09-23")


def test_a_cold_sec_and_calendar_say_nothing_has_been_published(monkeypatch):
    from nicegui import ui

    from pages import news, news_view as nv
    new, _ = _render_views(monkeypatch, {nv.VIEW: _items_payload(_item(1))})
    texts = [e.text for e in new if isinstance(e, ui.label)]
    assert news.SEC_WAITING in texts and news.CAL_WAITING in texts
    assert news.SEC_WAITING != news.SEC_EMPTY


def test_calendar_text_is_escaped_and_an_awaiting_indicator_says_so():
    from nicegui import ui

    from pages import news
    groups = [{"title": "Economic data (CPI, PPI etc)", "note": None, "empty": None,
               "tiles": [{"title": "<b>CPI</b>", "when": "Tue Oct 13 · 7:30 AM CT",
                          "lines": [], "indicators": [
                              {"label": "CPI m/m", "actual": "—", "prior": "+0.2% m/m",
                               "state": "awaiting", "next": "x"}]}]},
              {"title": "Dividend / IPO", "note": "Source unavailable — showing the "
               "last good reading", "empty": "No dividends or IPOs ahead", "tiles": []}]
    before = set(ui.context.client.elements)
    news.draw_calendar(ui.column(), groups)
    new = _new_elements(before)
    texts = [e.text for e in new if isinstance(e, ui.label)]
    assert "<b>CPI</b>" in texts                        # a label: escaped
    assert "Next Tue Oct 13 · 7:30 AM CT" in texts
    assert "Actual — · Prior +0.2% m/m" in texts
    assert "Awaiting the release" in texts
    assert "No dividends or IPOs ahead" in texts
    assert "Source unavailable — showing the last good reading" in texts


def test_a_released_indicator_shows_its_status_line():
    from nicegui import ui

    from pages import news
    groups = [{"title": "Economic data (CPI, PPI etc)", "note": None, "empty": None,
               "tiles": [{"title": "CPI", "when": "Wed Nov 11 · 7:30 AM CT",
                          "lines": [], "indicators": [
                              {"label": "CPI m/m", "actual": "+0.4% m/m", "prior": "+0.2% m/m",
                               "state": "released", "status": "Released 7:30 AM CT",
                               "next": "x"}]}]}]
    before = set(ui.context.client.elements)
    news.draw_calendar(ui.column(), groups)
    texts = [e.text for e in _new_elements(before) if isinstance(e, ui.label)]
    assert "Released 7:30 AM CT" in texts
    assert "Awaiting the release" not in texts


def test_refresh_toast_mentions_the_calendar():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    assert "the calendar now" in src


# ── review round: phone rows, echoes, readable reasons, live repaint ────────
def test_the_row_clips_and_the_symbol_and_source_columns_hide_on_a_phone():
    """At ~375px a nowrap row squeezed the headline to 0px: the row clips, and
    the Symbol and Source columns - each cell AND its header label - only show
    from ``sm`` up."""
    from pages import news
    assert "overflow-hidden" in news._ROW.split()
    for cls in (news._SOURCE_CELL, news._SOURCE_HEAD, news._TICKER_SLOT, news._TICKER_W):
        assert "max-sm:hidden" in cls.split(), cls
        # never Tailwind's bare ``hidden``: Quasar's ``.hidden`` is
        # ``display:none !important`` and no ``sm:`` display beats it
        assert "hidden" not in cls.split(), cls


def test_a_row_shows_two_tickers_then_a_count_with_the_rest_on_hover():
    from nicegui import ui

    from pages import news, news_view as nv
    assert news.MAX_ROW_TICKERS == 2
    rows = nv.rows(_items_payload(_item(1, tickers=["NVDA", "AMD", "INTC", "MU"])),
                   now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    (_head, row_el) = box.default_slot.children
    slot = row_el.default_slot.children[2]
    texts = [getattr(c, "text", None) for c in slot.default_slot.children]
    assert texts == ["NVDA", "AMD", "+2"]
    assert "INTC" not in texts and "MU" not in texts
    more = slot.default_slot.children[2]
    assert "shrink-0" in _classes(more)
    tips = [d for d in _descendants(more) if isinstance(d, ui.tooltip)]
    assert tips and tips[0].text == "INTC, MU"
    # two tickers or fewer: no count
    rows = nv.rows(_items_payload(_item(2, tickers=["NVDA", "AMD"])), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    slot = box.default_slot.children[1].default_slot.children[2]
    texts = [getattr(c, "text", None) for c in slot.default_slot.children]
    assert texts == ["NVDA", "AMD"]


def test_a_teaser_that_is_the_headline_plus_a_publisher_adds_no_hover():
    from pages import news
    assert news.hover_text({"title": "Costco Beats", "teaser": "Costco  beats WSJ"}) == ""
    assert news.hover_text({"title": "Fed holds", "teaser": "Fed holds - Reuters"}) == ""
    pub = "The Wall Street Journal Weekend Edition International"
    assert news.hover_text({"title": "Fed holds", "teaser": f"Fed holds  {pub}",
                            "original_source": pub}) == ""
    # a real first line that opens with the headline is kept
    long_teaser = ("Costco beats estimates as margins widen on record membership "
                   "renewals and a stronger holiday quarter")
    assert news.hover_text({"title": "Costco Beats", "teaser": long_teaser}) == long_teaser
    # the headline must end at a word: "Fed" is not echoed by "Federal ..."
    assert news.hover_text({"title": "Fed", "teaser": "Federal Reserve holds"}) \
        == "Federal Reserve holds"
    assert news.hover_text({"title": "Fed holds", "teaser": "Short."}) == "Short."


def test_the_sec_row_hover_does_not_repeat_its_inline_detail():
    from nicegui import ui

    from pages import news, news_view as nv
    rows = nv.sec_rows(_items_payload(SEC_ITEM), now=NOW)
    box = ui.column()
    news.draw_sec_rows(box, rows, linked=True, on_ticker=lambda t: None)
    tips = [d for d in _descendants(box) if isinstance(d, ui.tooltip)
            and d.parent_slot.parent.text == "ACME insider buys $136.4M"]
    assert tips == []                                   # no teaser: no hover
    with_teaser = {**SEC_ITEM, "teaser": "Nine open-market buys by the CEO."}
    box = ui.column()
    news.draw_sec_rows(box, nv.sec_rows(_items_payload(with_teaser), now=NOW),
                       linked=True, on_ticker=lambda t: None)
    hover = [d.text for d in _descendants(box) if isinstance(d, ui.label)
             and isinstance(d.parent_slot.parent, ui.tooltip)]
    assert hover == ["Nine open-market buys by the CEO."]


def test_the_sec_column_header_sticks_over_the_panel():
    from pages import news
    head = news._SEC_HEAD.split()
    assert {"sticky", "top-0"} <= set(head)
    assert [c for c in head if c.startswith("z-")]
    assert [c for c in head if c.startswith("bg-")]


def test_a_filing_links_only_to_https_sec_gov():
    from nicegui import ui

    from pages import news, news_view as nv
    good = {**SEC_ITEM, "url": "https://www.sec.gov/Archives/edgar/data/1/x.htm"}
    box = ui.column()
    news.draw_sec_rows(box, nv.sec_rows(_items_payload(good), now=NOW),
                       linked=True, on_ticker=lambda t: None)
    links = {d.text: d for d in _descendants(box) if isinstance(d, ui.link)}
    assert links["ACME insider buys $136.4M"].props["href"] == good["url"]
    for url in ("https://example.com/50", "http://www.sec.gov/x",
                "https://sec.gov.evil.com/x"):
        box = ui.column()
        news.draw_sec_rows(box, nv.sec_rows(_items_payload({**SEC_ITEM, "url": url}),
                                            now=NOW),
                           linked=True, on_ticker=lambda t: None)
        assert "ACME insider buys $136.4M" not in [
            d.text for d in _descendants(box) if isinstance(d, ui.link)], url
        assert "ACME insider buys $136.4M" in [
            d.text for d in _descendants(box) if isinstance(d, ui.label)], url


def test_an_open_tab_repaints_its_held_payloads_every_minute(monkeypatch):
    """Time stamps ("10:43 AM" vs "Sep 25 10:43 AM"), filings and calendar tiles
    age while a tab stays open; a slow guarded timer repaints them from the
    payloads already held - it never re-reads the bus."""
    from nicegui import ui

    from pages import news, news_view as nv
    pre = set(ui.context.client.elements)   # earlier tests share the client
    new, reads = _render_views(monkeypatch, {
        nv.VIEW: _items_payload(_item(1, title="A headline")),
        nv.VIEW_SEC: _items_payload(SEC_ITEM),
        nv.VIEW_CAL: CAL_PAYLOAD,
    })
    timers = [e for e in new if isinstance(e, ui.timer)
              and e.interval == news.REPAINT_SEC]
    assert news.REPAINT_SEC == 60 and len(timers) == 1

    def _ids(text):
        return {k for k, e in ui.context.client.elements.items()
                if k not in pre and getattr(e, "text", None) == text}
    before = {t: _ids(t) for t in ("A headline", "ACME insider buys $136.4M",
                                   "JPM dividend")}
    n = len(reads)
    timers[0].callback()
    assert len(reads) == n                                 # no bus read
    for text, ids in before.items():
        now_ids = _ids(text)
        assert now_ids and not now_ids & ids, text        # redrawn, not kept


# ── the Source column (2026-09-26) ───────────────────────────────────────────

def test_the_headline_list_opens_with_a_sticky_column_header():
    from nicegui import ui

    from pages import news, news_view as nv
    rows = nv.rows(_items_payload(_item(1, tickers=["NVDA"])), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    head = box.default_slot.children[0]
    assert isinstance(head, ui.row)
    assert [c.text for c in head.default_slot.children] == list(news.LIST_COLUMNS)
    assert news.LIST_COLUMNS == ("Time", "Imp.", "Symbol", "Headline", "Source")
    cls = news._LIST_HEAD.split()
    assert {"sticky", "top-0", "flex-nowrap"} <= set(cls)
    assert [c for c in cls if c.startswith("z-")] and [c for c in cls if c.startswith("bg-")]
    # each label sits over its cell: the same width class as the cell below
    time_h, imp_h, sym_h, head_h, src_h = head.default_slot.children
    row = box.default_slot.children[1].default_slot.children
    assert "w-28" in _classes(time_h) and "w-28" in _classes(row[0])
    assert "w-5" in _classes(imp_h) and "w-5" in _classes(row[1])
    assert "w-32" in _classes(sym_h) and "w-32" in _classes(row[2])
    assert "flex-1" in _classes(head_h) and "flex-1" in _classes(row[3])
    assert "w-32" in _classes(src_h) and "w-32" in _classes(row[4])
    tips = [d for d in _descendants(imp_h) if isinstance(d, ui.tooltip)]
    assert tips and "Impact" in tips[0].text


def test_no_rows_draw_no_column_header():
    from nicegui import ui

    from pages import news
    box = ui.column()
    news.draw_rows(box, [], linked=True, on_ticker=lambda t: None)
    assert box.default_slot.children == []


def test_the_ticker_slot_and_source_cell_have_fixed_widths_and_clip():
    from pages import news
    slot = news._TICKER_SLOT.split()
    assert {"w-32", "shrink-0", "overflow-hidden", "flex-nowrap"} <= set(slot)
    cell = news._SOURCE_CELL.split()
    assert {"w-32", "shrink-0", "overflow-hidden"} <= set(cell)
    assert {"truncate", "min-w-0"} <= set(news._SOURCE.split())


def test_a_phone_keeps_the_headline_its_room():
    """Below ``sm`` only Time, the pill and the headline are drawn; at 390px
    less two 16px gutters each side (the page's and the column's) the headline
    keeps >= 150px - measured 178px in a browser, where a one-chip Symbol slot
    left it 122px."""
    from pages import news
    phone = [c for c in (news._WHEN, news._PILL_SLOT, news._TICKER_SLOT, news._SOURCE_CELL)
             if "max-sm:hidden" not in c.split()]
    assert phone == [news._WHEN, news._PILL_SLOT]
    px = {"w-28": 112, "w-5": 20}
    assert 390 - 4 * 16 - sum(px.values()) - 2 * 8 >= 150


def test_the_source_cell_shows_the_first_source_a_count_and_all_on_hover():
    from nicegui import ui

    from pages import news, news_view as nv
    it = {**_item(1), "source": "Reuters", "sources": ["Reuters", "CNBC", "WSJ"]}
    rows = nv.rows(_items_payload(it), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    cell = box.default_slot.children[1].default_slot.children[4]
    shown = [c.text for c in cell.default_slot.children if isinstance(c, ui.label)]
    assert shown == [rows[0]["sources"][0], f"+{len(rows[0]['sources']) - 1}"]
    tips = [d for d in _descendants(cell) if isinstance(d, ui.tooltip)]
    assert tips and tips[0].text == ", ".join(rows[0]["sources"])
    # one source: its name, no count and no hover
    rows = nv.rows(_items_payload(_item(2, source="CNBC")), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    cell = box.default_slot.children[1].default_slot.children[4]
    assert [c.text for c in cell.default_slot.children] == ["CNBC"]
    assert not [d for d in _descendants(cell) if isinstance(d, ui.tooltip)]


def test_a_row_with_no_source_keeps_an_empty_source_cell():
    from nicegui import ui

    from pages import news
    rows = [{"title": "X", "when": "9:00 AM", "tickers": [], "sources": []}]
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    cell = box.default_slot.children[1].default_slot.children[4]
    assert cell.default_slot.children == [] and "w-32" in _classes(cell)


# ── high-impact calendar tiles (2026-09-26) ──────────────────────────────────

def _tile_group(*tiles):
    return [{"title": "Economic news/Calendar", "note": None, "empty": None,
             "tiles": list(tiles)}]


def test_a_high_tile_is_highlighted_with_a_chip():
    from nicegui import ui

    from pages import news
    box = ui.column()
    news.draw_calendar(box, _tile_group(
        {"title": "FOMC statement", "when": "Wed Oct 28", "lines": [], "high": True},
        {"title": "FOMC minutes", "when": "Wed Oct 7", "lines": [], "high": False}))
    tiles = [d for d in _descendants(box) if isinstance(d, ui.column)
             and "rounded-[8px]" in d.classes]
    assert len(tiles) == 2
    hi, lo = tiles
    assert set(news._CAL_TILE_HIGH.split()) <= _classes(hi)
    assert set(news._CAL_TILE.split()) <= _classes(lo)
    assert "border-amber-400/70" in _classes(hi) and "border-[#213152]" not in _classes(hi)
    chips = [d for d in _descendants(hi) if isinstance(d, ui.label)
             and d.text == news.HIGH_CHIP]
    assert len(chips) == 1 and set(news._CAL_CHIP.split()) <= _classes(chips[0])
    tips = [d for d in _descendants(chips[0]) if isinstance(d, ui.tooltip)]
    assert tips and tips[0].text == "High-impact release"
    assert not [d for d in _descendants(lo) if getattr(d, "text", None) == news.HIGH_CHIP]
    # the title still reads first, and still truncates
    titles = [d for d in _descendants(hi) if getattr(d, "text", None) == "FOMC statement"]
    assert titles and "truncate" in _classes(titles[0])


def test_a_tile_with_a_missing_or_non_bool_high_is_not_highlighted():
    from nicegui import ui

    from pages import news
    for bad in (None, 1, "true"):
        box = ui.column()
        tile = {"title": "CPI", "when": "x", "lines": []}
        if bad is not None:
            tile["high"] = bad
        news.draw_calendar(box, _tile_group(tile))
        assert not [d for d in _descendants(box) if getattr(d, "text", None) == news.HIGH_CHIP]
        assert not [d for d in _descendants(box) if "border-amber-400/70" in d.classes]


def test_the_high_classes_are_fixed_palette_classes():
    from pages import news
    assert news.HIGH_CHIP == "HIGH"
    assert "bg-amber-400/[0.08]" in news._CAL_TILE_HIGH.split()
    assert {"text-amber-300", "uppercase"} <= set(news._CAL_CHIP.split())
