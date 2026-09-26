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


def test_a_teaser_that_repeats_the_headline_is_not_printed_twice():
    from pages import news
    assert news.second_line({"title": "Costco Beats", "teaser": "Costco  beats WSJ"}) == ""
    assert news.second_line({"title": "Costco Beats", "teaser": "Margins widened."}) \
        == "Margins widened."
    assert news.second_line({"title": "X", "teaser": None, "kind": "edgar_filings",
                             "detail": {"form": "S-3"}}) == "Form S-3"
    assert news.second_line(None) == ""


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
