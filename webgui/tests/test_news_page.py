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




def test_a_teaser_equal_to_the_headline_adds_no_hover():
    from pages import news
    assert news.hover_text({"title": "Costco Beats", "teaser": "  costco   BEATS "}) == ""
    assert news.hover_text({"title": "X", "teaser": "", "kind": "edgar_filings",
                            "detail": {"form": "S-3"}}) == "Form S-3"
    assert news.hover_text(None) == ""




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








def test_a_cold_sec_and_calendar_say_nothing_has_been_published(monkeypatch):
    from nicegui import ui

    from pages import news, news_view as nv
    new, _ = _render_views(monkeypatch, {nv.VIEW: _items_payload(_item(1))})
    texts = [e.text for e in new if isinstance(e, ui.label)]
    assert news.SEC_WAITING in texts and news.CAL_WAITING in texts
    assert news.SEC_WAITING != news.SEC_EMPTY






def test_refresh_toast_mentions_the_calendar():
    src = (PAGES / "news.py").read_text(encoding="utf-8")
    assert "the calendar now" in src


# ── review round: phone rows, echoes, readable reasons, live repaint ────────




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













# ── high-impact calendar tiles (2026-09-26) ──────────────────────────────────

def _tile_group(*tiles):
    return [{"title": "Economic news/Calendar", "note": None, "empty": None,
             "tiles": list(tiles)}]








# ── the 2026-09-26 redesign (the mockup) ─────────────────────────────────────
# Rewritten from the one-line table with a sticky column header, the Impact
# select, the Ticker input and the three calendar tile groups.

def _click(el):
    for lst in list(el._event_listeners.values()):
        if lst.type == "click":
            lst.handler(None)


def _live_texts(pre, cls=None):
    from nicegui import ui
    cls = cls or (ui.label, ui.link)
    return [e.text for k, e in ui.context.client.elements.items()
            if k not in pre and isinstance(e, cls)]


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
    assert rows[0]["stamp"] in labels
    assert "MarketWatch" in labels
    assert "<b>hi</b>" in labels          # a label escapes; never rendered as HTML


def test_a_row_is_one_line_stamp_band_headline_tickers_source_with_no_header():
    from nicegui import ui

    from pages import news, news_view as nv
    it = {**_item(1, tickers=["NVDA"], teaser="Chips rallied on the news."),
          "impact": {"band": "high", "score": 7, "reasons": ["kw:tier1:FOMC"]}}
    rows = nv.rows(_items_payload(it, _item(2)), now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    (card,) = box.default_slot.children                  # one card, no header row
    kids = card.default_slot.children
    assert len(kids) == 2
    for row_el in kids:
        assert isinstance(row_el, ui.row) and "flex-nowrap" in _classes(row_el)
        assert "overflow-hidden" in _classes(row_el)
        assert not [d for d in _descendants(row_el) if isinstance(d, (ui.row, ui.column))]
    first = kids[0].default_slot.children
    assert [getattr(c, "text", None) for c in first[:3]] == [rows[0]["stamp"], "HIGH",
                                                            "Headline 1"]
    assert [c.text for c in first[3].default_slot.children] == ["NVDA"]
    assert first[4].text == "MarketWatch"
    # the band word: fixed palette, its reasons as a phrase on hover
    assert "text-rose-400" in _classes(first[1])
    tip = [d for d in _descendants(first[1]) if isinstance(d, ui.tooltip)]
    assert tip and tip[0].text == "mentions FOMC"
    assert {"truncate", "min-w-0", "flex-1"} <= _classes(first[2])
    hover = [d.text for d in _descendants(first[2]) if isinstance(d, ui.label)]
    assert hover == ["Chips rallied on the news."]
    # a high row: red left border and wash; an unscored row: transparent, no word
    assert set(news.ROW_ACCENT["high"].split()) <= _classes(kids[0])
    second = kids[1].default_slot.children
    assert second[1].text == "" and "border-l-transparent" in _classes(kids[1])


def test_the_row_accents_and_band_words_are_fixed_palette_maps():
    from pages import news
    assert set(news.ROW_ACCENT) == set(news.BAND_TEXT) == {"high", "med", "low", None}
    assert news.ROW_ACCENT["med"] == "border-l-amber-400"
    assert news.ROW_ACCENT["low"] == news.ROW_ACCENT[None] == "border-l-transparent"
    assert news.BAND_WORD == {"high": "HIGH", "med": "MED", "low": "LOW"}
    for m in (news.ROW_ACCENT, news.BAND_TEXT, news.BAND_DOT, news.FORM_CLASSES,
              news.BADGE_CLASSES, news.CAL_ACCENT):
        for v in m.values():
            assert "{" not in v and "var(" not in v


def test_a_phone_keeps_the_headline_its_room():
    """Below ``sm`` the tickers and the source go - with ``max-sm:hidden``,
    never Tailwind's bare ``hidden``: Quasar's ``.hidden`` is
    ``display:none !important`` and no ``sm:`` display beats it."""
    from pages import news
    for cls in (news._TICKERS, news._SOURCE, news._SEC_SYM, news._CAL_SUB):
        assert "max-sm:hidden" in cls.split(), cls
        assert "hidden" not in cls.split(), cls
    for cls in (news._STAMP, news._BAND, news._HEADLINE):
        assert "max-sm:hidden" not in cls.split()
    assert "overflow-hidden" in news._ROW.split()


def test_a_row_shows_two_tickers_then_a_count_with_the_rest_on_hover():
    from nicegui import ui

    from pages import news, news_view as nv
    assert news.MAX_ROW_TICKERS == 2
    rows = nv.rows(_items_payload(_item(1, tickers=["NVDA", "AMD", "INTC", "MU"])),
                   now=NOW)
    box = ui.column()
    news.draw_rows(box, rows, linked=True, on_ticker=lambda t: None)
    slot = box.default_slot.children[0].default_slot.children[0].default_slot.children[3]
    assert [c.text for c in slot.default_slot.children] == ["NVDA", "AMD", "+2"]
    tips = [d for d in _descendants(slot.default_slot.children[2])
            if isinstance(d, ui.tooltip)]
    assert tips and tips[0].text == "INTC, MU"


def test_the_source_is_the_first_one_with_all_on_hover_and_empty_without():
    from nicegui import ui

    from pages import news, news_view as nv
    it = {**_item(1), "source": "Reuters", "sources": ["Reuters", "CNBC", "WSJ"]}
    box = ui.column()
    news.draw_rows(box, nv.rows(_items_payload(it), now=NOW), linked=True,
                   on_ticker=lambda t: None)
    src = box.default_slot.children[0].default_slot.children[0].default_slot.children[4]
    assert src.text == "Reuters" and "w-32" in _classes(src)
    assert [d.text for d in _descendants(src) if isinstance(d, ui.tooltip)] == \
        ["Reuters, CNBC, WSJ"]
    box = ui.column()
    news.draw_rows(box, [{"title": "X", "stamp": "9:00", "tickers": [], "sources": []}],
                   linked=True, on_ticker=lambda t: None)
    src = box.default_slot.children[0].default_slot.children[0].default_slot.children[4]
    assert src.text == "" and "w-32" in _classes(src)


def test_no_rows_draw_nothing():
    from nicegui import ui

    from pages import news
    box = ui.column()
    news.draw_rows(box, [], linked=True, on_ticker=lambda t: None)
    assert box.default_slot.children == []


def _band_items():
    items = [{**_item(i, title=f"B{band}"), "impact": {"band": band, "score": 1,
                                                       "reasons": []}}
             for i, band in enumerate(("high", "med", "low"))]
    return items + [_item(9, title="Bnone")]


def _titles(pre):
    from nicegui import ui
    return {t for t in _live_texts(pre, (ui.link,)) if t.startswith("B")}


def test_the_band_picker_shows_only_the_band_picked(monkeypatch):
    from nicegui import ui

    from pages import news, news_view as nv
    pre = set(ui.context.client.elements)
    _render_views(monkeypatch, {nv.VIEW: _items_payload(*_band_items())})
    assert list(news.BAND_OPTIONS.values()) == ["All", "High", "Med", "Low"]

    def _opt(text):
        return [e for k, e in ui.context.client.elements.items() if k not in pre
                and isinstance(e, ui.row) and "cursor-pointer" in e.classes
                and [getattr(c, "text", None) for c in e.default_slot.children][-1:] == [text]][-1]
    assert _titles(pre) == {"Bhigh", "Bmed", "Blow", "Bnone"}
    for text, want in (("High", {"Bhigh"}), ("Med", {"Bmed"}), ("Low", {"Blow"}),
                       ("All", {"Bhigh", "Bmed", "Blow", "Bnone"})):
        pre2 = set(ui.context.client.elements)
        _click(_opt(text))
        assert _titles(pre2) == want, text
        # the picked option is drawn selected
        assert set(news._SEG_ON.split()) <= set(_opt(text).classes), text


def test_the_search_box_filters_headlines_and_tickers(monkeypatch):
    from nicegui import ui

    from pages import news_view as nv
    items = [_item(1, title="Fed holds rates"), _item(2, title="Chip rally", tickers=["NVDA"])]
    new, _ = _render_views(monkeypatch, {nv.VIEW: _items_payload(*items)})
    (field,) = [e for e in new if isinstance(e, ui.input)]
    assert int(field.props["debounce"]) == 300           # each keystroke would repaint
    assert field.props.get("placeholder") == "Filter by ticker or keyword"
    pre = set(ui.context.client.elements)
    field.value = "nvd"
    assert set(_live_texts(pre, (ui.link,))) >= {"Chip rally"}
    assert "Fed holds rates" not in _live_texts(pre, (ui.link,))
    pre = set(ui.context.client.elements)
    field.value = "HOLDS"
    links = _live_texts(pre, (ui.link,))
    assert "Fed holds rates" in links and "Chip rally" not in links


def test_source_chips_carry_counts_and_toggle(monkeypatch):
    from nicegui import ui

    from pages import news_view as nv
    items = [_item(1, title="A1", source="WSJ"), _item(2, title="A2", source="WSJ"),
             _item(3, title="A3", source="Reuters")]
    pre = set(ui.context.client.elements)
    _render_views(monkeypatch, {nv.VIEW: _items_payload(*items)})

    def _chip(name):
        return [e for k, e in ui.context.client.elements.items() if k not in pre
                and isinstance(e, ui.row) and "cursor-pointer" in e.classes
                and [getattr(c, "text", None) for c in e.default_slot.children][:1] == [name]][-1]
    wsj = _chip("WSJ")
    assert [c.text for c in wsj.default_slot.children] == ["WSJ", "2"]
    pre2 = set(ui.context.client.elements)
    _click(wsj)
    assert set(_live_texts(pre2, (ui.link,))) == {"A1", "A2"}
    pre2 = set(ui.context.client.elements)
    _click(_chip("Reuters"))                              # several at once
    assert set(_live_texts(pre2, (ui.link,))) == {"A1", "A2", "A3"}
    assert "3 of 3 stories" in _live_texts(pre, (ui.label,))   # the count line
    pre2 = set(ui.context.client.elements)
    _click(_chip("WSJ"))
    assert set(_live_texts(pre2, (ui.link,))) == {"A3"}
    assert "1 of 3 stories" in _live_texts(pre, (ui.label,))


def test_a_most_mentioned_chip_filters_by_its_ticker_and_clears(monkeypatch):
    from nicegui import ui

    from pages import news, news_view as nv
    items = [_item(1, title="N1", tickers=["NVDA"]), _item(2, title="N2", tickers=["NVDA"]),
             _item(3, title="A3", tickers=["AAPL"])]
    monkeypatch.setattr(news, "trending_window_h", lambda: 6)
    monkeypatch.setattr(news.nv, "trending",
                        lambda p, now, window_h: [("NVDA", 2), ("AAPL", 1)])
    pre = set(ui.context.client.elements)
    _render_views(monkeypatch, {nv.VIEW: _items_payload(*items)})
    assert news.TRENDING_LABEL in _live_texts(pre, (ui.label,))

    def _chip(t):
        return [e for k, e in ui.context.client.elements.items() if k not in pre
                and isinstance(e, ui.row) and "cursor-pointer" in e.classes
                and [getattr(c, "text", None) for c in e.default_slot.children] == [t, "2" if t == "NVDA" else "1"]][-1]
    pre2 = set(ui.context.client.elements)
    _click(_chip("NVDA"))
    assert set(_live_texts(pre2, (ui.link,))) >= {"N1", "N2"}
    assert "A3" not in _live_texts(pre2, (ui.link,))
    assert set(news._TREND_ON.split()) <= set(_chip("NVDA").classes)
    pre2 = set(ui.context.client.elements)
    _click(_chip("NVDA"))
    assert "A3" in _live_texts(pre2, (ui.link,))


def test_the_regions_render_left_then_right_in_dom_order(monkeypatch):
    from nicegui import ui

    from pages import news, news_view as nv
    new, _ = _render_views(monkeypatch, {
        nv.VIEW: _items_payload(_item(1, title="A headline")),
        nv.VIEW_SEC: _items_payload(SEC_ITEM),
        nv.VIEW_CAL: CAL_PAYLOAD,
    })
    ids = {x.id for x in new}
    tops = [e for e in new if e.parent_slot is not None
            and e.parent_slot.parent.id not in ids]
    root = max(tops, key=lambda e: len(_descendants(e)))
    texts = [e.text for e in [root, *_descendants(root)]
             if isinstance(e, (ui.label, ui.link))]
    i_head = texts.index("A headline")
    i_next = texts.index(news.NEXT_TITLE)
    i_sec = texts.index(news.SEC_TITLE)
    i_cal = texts.index(news.CAL_TITLE)
    assert texts.index(news.SOURCES_LABEL) < i_head < i_next < i_sec < i_cal
    assert i_sec < texts.index("ACME insider buys $136.4M") < i_cal
    assert i_sec < texts.index("$136.4M") < i_cal          # the Form 4 value, in green
    assert i_next < texts.index("FOMC statement") < i_sec  # the hero
    for chip in nv.SEC_KINDS.values():
        assert i_sec < texts.index(chip, i_sec) < i_cal
    # the agenda: a day header, the event, the dividend - one list, badges apart
    assert i_cal < texts.index("JPM dividend")
    assert "DIVIDEND" in texts[i_cal:] and "FOMC" in texts[i_cal:]
    assert "WED · OCT 28" in texts[i_cal:]
    # the grid: two thirds / one third at lg
    grids = [e for e in new if "lg:grid-cols-3" in e.classes]
    assert len(grids) == 1
    assert [e for e in new if "lg:col-span-2" in e.classes]


def test_the_sec_row_links_its_symbol_and_draws_its_form_value_and_time():
    from nicegui import ui

    from pages import news, news_view as nv
    rows = nv.sec_rows(_items_payload(SEC_ITEM), now=NOW)
    box = ui.column()
    news.draw_sec_rows(box, rows, linked=True, on_ticker=lambda t: None)
    (row_el,) = box.default_slot.children[0].default_slot.children
    assert {"flex-nowrap", "overflow-hidden"} <= _classes(row_el)
    links = {d.text: d for d in _descendants(row_el) if isinstance(d, ui.link)}
    assert links["ACME"].props["href"] == "/symbol?symbol=ACME"
    labels = {d.text: d for d in _descendants(row_el) if isinstance(d, ui.label)}
    assert set(news.FORM_CLASSES["form4"].split()) <= _classes(labels["FORM 4"])
    assert news._t.TXT_POS in labels["$136.4M"].classes
    assert rows[0]["stamp"] in labels


def test_the_sec_hover_is_the_full_title_and_teaser_never_the_inline_detail():
    from nicegui import ui

    from pages import news, news_view as nv
    it = {**SEC_ITEM, "title": "ACME — Jane Doe (CEO) bought $1.0M",
          "teaser": "Nine open-market buys by the CEO."}
    box = ui.column()
    news.draw_sec_rows(box, nv.sec_rows(_items_payload(it), now=NOW),
                       linked=True, on_ticker=lambda t: None)
    names = [d for d in _descendants(box) if getattr(d, "text", None) == "Jane Doe"]
    assert names
    hover = [d.text for d in _descendants(names[0]) if isinstance(d, ui.label)]
    assert hover == ["ACME — Jane Doe (CEO) bought $1.0M — "
                     "Nine open-market buys by the CEO."]
    assert "9 purchases" not in " ".join(hover)


def test_the_sec_chips_filter_by_kind(monkeypatch):
    from nicegui import ui

    from pages import news_view as nv
    filing = {**_item(60, title="Kyndryl files 424B5 (prospectus supplement (offering))",
                      kind="edgar_filings", detail={"form": "424B5"}),
              "url": "https://www.sec.gov/x"}
    pre = set(ui.context.client.elements)
    _render_views(monkeypatch, {nv.VIEW_SEC: _items_payload(
        {**SEC_ITEM, "url": "https://www.sec.gov/y"}, filing)})

    def _chip(text):
        return [e for k, e in ui.context.client.elements.items() if k not in pre
                and isinstance(e, ui.row) and "rounded-full" in e.classes
                and [getattr(c, "text", None) for c in e.default_slot.children] == [text]][-1]
    pre2 = set(ui.context.client.elements)
    _click(_chip("Offerings"))
    shown = _live_texts(pre2, (ui.link,))
    assert "Kyndryl — prospectus supplement (offering)" in shown
    assert "ACME insider buys $136.4M" not in shown
    pre2 = set(ui.context.client.elements)
    _click(_chip("Registrations"))
    from pages import news
    assert news.SEC_NO_MATCH in _live_texts(pre2, (ui.label,))


def test_the_hero_names_the_next_timed_item_and_counts_down():
    from nicegui import ui

    from pages import news
    box = ui.column()
    news.draw_next(box, {"title": "<b>FOMC statement</b>", "badge": "FOMC",
                         "when": "Wed Oct 28 · 1:00 PM CT", "countdown": "in 1d 19h",
                         "high": True})
    texts = [d.text for d in _descendants(box) if isinstance(d, ui.label)]
    assert texts[:2] == [news.NEXT_TITLE, "in 1d 19h"]
    assert "<b>FOMC statement</b>" in texts             # a label: escaped
    assert "Wed Oct 28 · 1:00 PM CT" in texts and news.HIGH_CHIP in texts
    box = ui.column()
    news.draw_next(box, None)
    texts = [d.text for d in _descendants(box) if isinstance(d, ui.label)]
    from pages import news_view as nv
    assert texts == [news.NEXT_TITLE, nv.NOTHING_NEXT]


def test_the_agenda_draws_days_badges_subs_notes_and_escapes():
    from nicegui import ui

    from pages import news
    agenda = {"notes": ["Dividend / IPO: Source unavailable — showing the last good reading"],
              "days": [{"head": "MON · SEP 28", "date": "2026-09-28", "items": [
                  {"time": "7:15", "badge": "SPEECH", "title": "<b>Michelle W. Bowman</b>",
                   "sub": "Vice Chair", "lines": [], "high": False},
                  {"time": "7:30", "badge": "DATA", "title": "CPI", "sub": "",
                   "lines": ["CPI m/m: Awaiting the release · Prior +0.2% m/m"], "high": True}]}],
              "undated": [{"time": "", "badge": "DATA", "title": "GDP",
                           "sub": "Next date not yet published", "lines": [], "high": False}],
              "empty": ["No dividends or IPOs ahead"]}
    box = ui.column()
    news.draw_calendar(box, agenda)
    texts = [d.text for d in _descendants(box) if isinstance(d, ui.label)]
    for t in ("MON · SEP 28", "7:15", "SPEECH", "<b>Michelle W. Bowman</b>", "Vice Chair",
              "CPI m/m: Awaiting the release · Prior +0.2% m/m", news.UNDATED_HEAD, "GDP",
              "No dividends or IPOs ahead",
              "Dividend / IPO: Source unavailable — showing the last good reading"):
        assert t in texts, t
    assert texts.index("MON · SEP 28") < texts.index("7:15") < texts.index(news.UNDATED_HEAD)
    badge = [d for d in _descendants(box) if getattr(d, "text", None) == "SPEECH"][0]
    assert set(news.BADGE_CLASSES["SPEECH"].split()) <= _classes(badge)


def test_a_high_agenda_item_is_highlighted_with_a_marker_and_only_a_real_true():
    from nicegui import ui

    from pages import news

    def _draw(high):
        box = ui.column()
        item = {"time": "13:00", "badge": "FOMC", "title": "FOMC statement", "sub": "",
                "lines": []}
        if high is not None:
            item["high"] = high
        news.draw_calendar(box, {"days": [{"head": "WED · OCT 28", "items": [item]}]})
        return box
    box = _draw(True)
    rows = [d for d in _descendants(box) if isinstance(d, ui.row)
            and "border-l-amber-400" in d.classes]
    assert len(rows) == 1 and "bg-amber-400/[0.07]" in rows[0].classes
    chips = [d for d in _descendants(box) if getattr(d, "text", None) == news.HIGH_CHIP]
    assert len(chips) == 1 and set(news._CAL_CHIP.split()) <= _classes(chips[0])
    tips = [d for d in _descendants(chips[0]) if isinstance(d, ui.tooltip)]
    assert tips and tips[0].text == news.HIGH_HINT
    for bad in (None, 1, "true", False):
        box = _draw(bad)
        assert not [d for d in _descendants(box) if getattr(d, "text", None) == news.HIGH_CHIP]
        assert not [d for d in _descendants(box) if "border-l-amber-400" in d.classes]
