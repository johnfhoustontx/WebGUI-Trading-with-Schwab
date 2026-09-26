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
    assert len(kids) == 2                                   # one element per item
    for row_el in kids:
        assert isinstance(row_el, ui.row)
        assert "flex-nowrap" in _classes(row_el)             # one line, never wraps
        # no nested row or column: nothing can drop to a second line
        assert not [d for d in _descendants(row_el)
                    if isinstance(d, (ui.row, ui.column))]
    first = kids[0].default_slot.children
    texts = [getattr(c, "text", None) for c in first]
    # time · impact · ticker · headline · source, in that order
    assert texts == [rows[0]["when"], "H", "NVDA", "Headline 1", "MarketWatch"]
    pill = first[1]
    assert set(nv.BAND_CLASSES["high"].split()) <= _classes(pill)
    tip = [d for d in _descendants(pill) if isinstance(d, ui.tooltip)]
    # the reason code reads as a phrase (news_view.reason_text), not a code
    assert tip and tip[0].text == "mentions FOMC"
    headline = first[3]
    assert {"truncate", "min-w-0", "flex-1"} <= _classes(headline)
    assert "shrink-0" in _classes(first[4])                  # the source badge
    # a row with no band keeps an EMPTY slot of the pill's width, never "L"
    second = kids[1].default_slot.children
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
    # the SEC panel's own columns, and the ONE line per filing
    for col in ("Date/Time", "Symbol", "Headline/Details"):
        assert i_sec < texts.index(col) < i_cal
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


def test_refresh_toast_mentions_the_calendar():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    assert "the calendar now" in src


# ── review round: phone rows, echoes, readable reasons, live repaint ────────
def test_the_row_clips_and_source_badges_hide_on_a_phone():
    """At ~375px a nowrap row squeezed the headline to 0px: the row clips, and
    the source badges only show from ``sm`` up."""
    from pages import news
    assert "overflow-hidden" in news._ROW.split()
    src = news._SOURCE.split()
    assert "hidden" in src and "sm:inline-flex" in src


def test_a_row_shows_two_tickers_then_a_count_with_the_rest_on_hover():
    from nicegui import ui

    from pages import news, news_view as nv
    assert news.MAX_ROW_TICKERS == 2
    rows = nv.rows(_items_payload(_item(1, tickers=["NVDA", "AMD", "INTC", "MU"])),
                   now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    (row_el,) = box.default_slot.children
    texts = [getattr(c, "text", None) for c in row_el.default_slot.children]
    assert texts[2:5] == ["NVDA", "AMD", "+2"]
    assert "INTC" not in texts and "MU" not in texts
    more = row_el.default_slot.children[4]
    assert "shrink-0" in _classes(more)
    tips = [d for d in _descendants(more) if isinstance(d, ui.tooltip)]
    assert tips and tips[0].text == "INTC, MU"
    # two tickers or fewer: no count
    rows = nv.rows(_items_payload(_item(2, tickers=["NVDA", "AMD"])), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    texts = [getattr(c, "text", None) for c in box.default_slot.children[0].default_slot.children]
    assert not [t for t in texts if isinstance(t, str) and t.startswith("+")]


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
