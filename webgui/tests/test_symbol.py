"""The Symbol Dossier's "In the news" band (news feed, Task 14).

The rest of the page is pinned in ``tests/test_symbol_page.py``; this file
holds the news band alone: its pure assembly, its place in the ONE batched
poll, and the rule that a third-party headline reaches the screen only through
an escaping widget.
"""
import ast
import asyncio
import datetime
import inspect

import pytest

from pages import copy as _copy
from pages import news_view
from pages import symbol

NOW = datetime.datetime(2026, 9, 25, 15, 0, tzinfo=datetime.timezone.utc)


def _item(i, tickers=("NVDA",), title=None):
    return {"id": str(i), "title": title or f"headline {i}",
            "url": f"https://news.example/{i}", "tickers": list(tickers),
            "published_at": (NOW - datetime.timedelta(minutes=i)).isoformat(),
            "source": "S", "sources": ["S"]}


# ── the pure band ────────────────────────────────────────────────────────────
def test_news_band_is_the_symbols_items_or_the_cold_line():
    from pages import symbol
    assert symbol.news_band("NVDA", None, now=NOW)["message"] == symbol.WAITING_NEWS
    env = {"items": [{"id": "1", "title": "x", "url": "https://a", "tickers": ["NVDA"],
                      "published_at": NOW.isoformat(), "source": "S", "sources": ["S"]}]}
    band = symbol.news_band("NVDA", env, now=NOW)
    assert band["message"] == "" and band["rows"][0]["title"] == "x"
    assert symbol.news_band("AAPL", env, now=NOW)["message"] == "No headlines for AAPL in the feed."


def test_the_cold_line_is_the_one_shared_sentence():
    """Two screens (this band and the Desk strip) say it, so it lives in
    ``pages/copy.py`` — and it is not the quiet-feed line."""
    assert symbol.WAITING_NEWS is _copy.WAITING_NEWS
    assert symbol.WAITING_NEWS != "No headlines for NVDA in the feed."


@pytest.mark.parametrize("junk", ["nonsense", 7, [1, 2], ("items",)])
def test_a_payload_that_is_not_a_dict_reads_as_cold(junk):
    band = symbol.news_band("NVDA", junk, now=NOW)
    assert band == {"message": symbol.WAITING_NEWS, "rows": []}


def test_a_live_feed_with_no_items_is_quiet_not_cold():
    """A published payload with nothing in it is a still feed, not a dead one:
    the two must never read the same."""
    band = symbol.news_band("NVDA", {"items": []}, now=NOW)
    assert band == {"message": "No headlines for NVDA in the feed.", "rows": []}


def test_the_band_is_capped_at_the_symbol_limit_newest_first():
    env = {"items": [_item(i) for i in range(news_view.SYMBOL_LIMIT + 4)]}
    rows = symbol.news_band("nvda", env, now=NOW)["rows"]
    assert len(rows) == news_view.SYMBOL_LIMIT
    assert [r["title"] for r in rows[:2]] == ["headline 0", "headline 1"]


def test_only_items_that_name_the_symbol_are_shown():
    env = {"items": [_item(1, ("AAPL",)), _item(2, ("NVDA", "AMD"))]}
    rows = symbol.news_band("NVDA", env, now=NOW)["rows"]
    assert [r["title"] for r in rows] == ["headline 2"]



def test_untitled_items_are_skipped_before_the_cap():
    """A row with a blank title draws an empty link: it is dropped, and it
    must not use up one of the band's rows - the cap applies AFTER the filter."""
    blanks = [dict(_item(i), title=t) for i, t in
              enumerate(["", "   ", "\t", "\n", " ", "", "  ", ""])]
    assert len(blanks) == news_view.SYMBOL_LIMIT
    real = [_item(20, title="real one"), _item(21, title="real two")]
    rows = symbol.news_band("NVDA", {"items": blanks + real}, now=NOW)["rows"]
    assert [r["title"] for r in rows] == ["real one", "real two"]


def test_a_feed_of_only_untitled_items_is_quiet():
    env = {"items": [dict(_item(1), title=""), dict(_item(2), title="  ")]}
    band = symbol.news_band("NVDA", env, now=NOW)
    assert band == {"message": "No headlines for NVDA in the feed.", "rows": []}


@pytest.mark.parametrize("raw", [None, "", "not a <ticker>!", "NV;DA", "A" * 40, 7])
def test_an_uncleanable_symbol_gets_a_neutral_line_never_none(raw):
    """``clean_symbol`` refuses it, so the band names no symbol at all rather
    than printing "None" or echoing text the allow-list rejected."""
    env = {"items": [_item(1)]}
    band = symbol.news_band(raw, env, now=NOW)
    assert band == {"message": "No headlines for this symbol in the feed.", "rows": []}
    assert "None" not in band["message"]

# ── the poll ─────────────────────────────────────────────────────────────────
def test_the_news_view_joins_the_one_batch_and_owns_its_region():
    assert news_view.VIEW in symbol.VIEWS
    assert symbol.REGION_VIEWS["news"] == (news_view.VIEW, news_view.VIEW_SEC)
    assert symbol.regions_for({news_view.VIEW}, "NVDA") == {"news"}


def test_the_news_band_adds_no_timer_of_its_own():
    src = inspect.getsource(symbol.render)
    assert src.count("ui.timer(") == 4          # seed, poll, two backstops


# ── escaping ─────────────────────────────────────────────────────────────────
def _nested_function(outer, name):
    src = inspect.getsource(outer).lstrip()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == name)
    return ast.get_source_segment(src, fn)


def _ui_calls(source):
    """The ``ui.<name>`` widgets a source segment CALLS (comments ignored)."""
    return {n.func.attr for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "ui"}


def test_the_news_painter_never_hands_feed_text_to_ui_html():
    """A headline is PLAIN TEXT from a third-party feed. ``ui.link`` and
    ``ui.label`` escape it; ``ui.html`` would render whatever it carries."""
    body = _nested_function(symbol.render, "_paint_news")
    assert "html" not in _ui_calls(body)
    assert {"link", "label"} <= _ui_calls(body) and "new_tab=True" in body
    assert not _ui_calls(inspect.getsource(symbol.news_band))


def test_a_band_row_links_only_to_a_web_address():
    """``ui.link`` escapes the text, not the href: a ``javascript:`` URL from a
    feed must come out unlinked."""
    env = {"items": [_item(1), dict(_item(2), url="javascript:alert(1)")]}
    rows = symbol.news_band("NVDA", env, now=NOW)["rows"]
    assert [r["href"] for r in rows] == ["https://news.example/1", None]


# ── mounted ──────────────────────────────────────────────────────────────────
@pytest.fixture
def world(monkeypatch):
    import bus_client
    data = {}

    def _read_gated(view, memo):
        memo["state"] = (1, data.get(view)) if view in data else None
        return data.get(view), True

    monkeypatch.setattr(bus_client, "read", lambda v: data.get(v))
    monkeypatch.setattr(bus_client, "read_full",
                        lambda v: (data.get(v), 1 if v in data else None))
    monkeypatch.setattr(bus_client, "read_gated", _read_gated)
    monkeypatch.setattr(bus_client, "read_versions",
                        lambda vs: {v: (1 if v in data else None) for v in vs})
    monkeypatch.setattr(bus_client, "request", lambda domain, cmd: "1-0")
    return data


def _built(sym):
    from nicegui import ui
    from nicegui.elements.timer import Timer
    before = set(ui.context.client.elements)
    symbol.render(sym)
    elements = [e for k, e in ui.context.client.elements.items()
                if k not in before]
    (seed,) = [e for e in elements
               if isinstance(e, Timer) and e.interval == symbol.SEED_DELAY_SEC]
    asyncio.run(seed.callback())
    return [e for k, e in ui.context.client.elements.items() if k not in before]


def test_a_cold_feed_renders_the_cold_line_in_the_band(world):
    texts = [getattr(e, "text", "") or "" for e in _built("NVDA")]
    assert "In the news" in texts
    assert symbol.WAITING_NEWS in texts


def test_a_headline_renders_as_an_escaping_link_to_the_article(world):
    from nicegui import ui
    world[news_view.VIEW] = {"items": [
        _item(1, title="<b>NVDA</b> beats & raises")]}
    links = [e for e in _built("NVDA") if isinstance(e, ui.link)]
    hit = [e for e in links if e.text == "<b>NVDA</b> beats & raises"]
    assert hit, [e.text for e in links]
    assert hit[0].props.get("href") == "https://news.example/1"
    assert hit[0].props.get("target") == "_blank"


# ── SEC items (news v2, Task 20) ─────────────────────────────────────────────
# Headlines and the EDGAR kinds are split at the producer (``news:feed`` /
# ``news:sec``); the dossier band reads both, because an insider buy on the
# symbol is dossier material.
FEED_WITHOUT_ACME = {"items": [_item(1, tickers=("NVDA",))]}
SEC_WITH_ACME_FORM4 = {"items": [dict(
    _item(3, tickers=("ACME",), title="ACME director purchases 10,000 shares"),
    kind="edgar_form4", source="SEC Form 4", sources=["SEC Form 4"])]}


def test_news_band_includes_an_insider_buy_for_the_symbol():
    band = symbol.news_band("ACME", FEED_WITHOUT_ACME, NOW, sec_env=SEC_WITH_ACME_FORM4)
    assert any("purchases" in r["title"] or r["kind"] == "edgar_form4" for r in band["rows"])


def test_the_band_merges_headlines_and_sec_items_newest_first():
    feed = {"items": [_item(2), _item(6)]}
    sec = {"items": [dict(_item(1), kind="edgar_form4"),
                     dict(_item(4), kind="edgar_filings")]}
    rows = symbol.news_band("NVDA", feed, NOW, sec_env=sec)["rows"]
    assert [r["id"] for r in rows] == ["1", "2", "4", "6"]


def test_the_merged_band_is_capped_at_the_symbol_limit():
    n = news_view.SYMBOL_LIMIT
    feed = {"items": [_item(2 * i) for i in range(n)]}
    sec = {"items": [dict(_item(2 * i + 1), kind="edgar_form4") for i in range(n)]}
    rows = symbol.news_band("NVDA", feed, NOW, sec_env=sec)["rows"]
    assert [r["id"] for r in rows] == [str(i) for i in range(n)]


def test_sec_items_show_even_while_the_headline_feed_is_cold():
    rows = symbol.news_band("ACME", None, NOW, sec_env=SEC_WITH_ACME_FORM4)["rows"]
    assert [r["kind"] for r in rows] == ["edgar_form4"]


def test_both_views_cold_is_still_the_cold_line():
    assert symbol.news_band("NVDA", None, NOW, sec_env=None)["message"] == symbol.WAITING_NEWS


def test_an_sec_item_for_another_symbol_stays_out():
    rows = symbol.news_band("NVDA", FEED_WITHOUT_ACME, NOW,
                            sec_env=SEC_WITH_ACME_FORM4)["rows"]
    assert [r["id"] for r in rows] == ["1"]


def test_the_sec_view_joins_the_one_batch_and_repaints_the_news_band():
    assert news_view.VIEW_SEC in symbol.VIEWS
    assert symbol.regions_for({news_view.VIEW_SEC}, "NVDA") == {"news"}


def test_the_dossier_reads_the_private_views_never_the_public_ones():
    """/symbol is private only; the public keys are the Desk's ``bus_key``
    business, and it swaps ``news:feed`` alone."""
    assert news_view.VIEW_PUBLIC not in symbol.VIEWS
    assert news_view.VIEW_SEC_PUBLIC not in symbol.VIEWS
    from pages import desk
    assert desk.bus_key(news_view.VIEW_SEC) == news_view.VIEW_SEC


def test_an_insider_buy_renders_in_the_mounted_band(world):
    from nicegui import ui
    world[news_view.VIEW] = {"items": [_item(1)]}
    world[news_view.VIEW_SEC] = {"items": [dict(
        _item(2, title="NVDA officer purchases 500 shares"), kind="edgar_form4")]}
    links = [e.text for e in _built("NVDA") if isinstance(e, ui.link)]
    assert "NVDA officer purchases 500 shares" in links
    assert "headline 1" in links
