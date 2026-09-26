from services.news_svc import items


def test_canonical_url_strips_tracking_and_fragment():
    u = "https://www.marketwatch.com/story/x-2e7c5c10?mod=mw_rss_topstories&utm_source=a#frag"
    assert items.canonical_url(u) == "https://www.marketwatch.com/story/x-2e7c5c10"


def test_canonical_url_keeps_meaningful_query():
    u = "https://finance.yahoo.com/m/abc/x.html?p=1"
    assert items.canonical_url(u) == u


def test_item_id_is_stable_and_url_based():
    a = items.item_id("https://a.com/x?utm_source=1")
    b = items.item_id("https://a.com/x")
    assert a == b and len(a) == 16


def test_title_key_normalises_case_and_punctuation():
    assert items.title_key("Apple (AAPL) Eyes Apple Pay!") == items.title_key("apple aapl eyes apple pay")


def test_tickers_only_from_explicit_cashtags_or_parentheses():
    universe = ["AAPL", "NVDA", "MSFT"]
    assert items.extract_tickers("Apple (AAPL) and $NVDA rally; Microsoft too", universe) == ["AAPL", "NVDA"]


def test_a_company_name_never_tags():
    assert items.extract_tickers("Apple rallies", ["AAPL"]) == []


def test_tickers_outside_the_universe_are_dropped():
    assert items.extract_tickers("(ZZZZ) $SPY", ["SPY"]) == ["SPY"]


def test_make_item_fills_every_field_and_cleans_symbols():
    it = items.make_item(source="MarketWatch", title="T", url="https://a.com/x?utm_x=1",
                         published_at="2026-09-25T21:57:00+00:00", public=True,
                         tickers=["nvda", "bad symbol"], now="2026-09-25T22:00:00+00:00")
    assert it["id"] == items.item_id("https://a.com/x")
    assert it["tickers"] == ["NVDA"]
    assert it["teaser"] == "" and it["topics"] == [] and it["detail"] == {}
    assert it["first_seen"] == "2026-09-25T22:00:00+00:00"


# --- hardening beyond the plan's sketch -------------------------------------

def test_tickers_come_back_in_document_order_across_both_forms():
    # A cashtag that appears BEFORE a parenthesised ticker must come first,
    # and vice versa - order is position in the text, not which regex matched.
    universe = ["AAPL", "NVDA", "MSFT"]
    assert items.extract_tickers("$MSFT beats; Apple (AAPL) lags; $NVDA flat", universe) == ["MSFT", "AAPL", "NVDA"]
    assert items.extract_tickers("(NVDA) then $AAPL", universe) == ["NVDA", "AAPL"]


def test_exchange_suffixed_parenthesis_tags_the_ticker():
    assert items.extract_tickers("Meta (META:NASDAQ) jumps", ["META"]) == ["META"]


def test_a_ticker_named_twice_is_reported_once():
    assert items.extract_tickers("$NVDA ... (NVDA) ... $NVDA", ["NVDA"]) == ["NVDA"]


def test_extract_tickers_tolerates_no_text():
    assert items.extract_tickers(None, ["AAPL"]) == []
    assert items.extract_tickers("", ["AAPL"]) == []


def test_clean_symbol_refuses_a_spaced_string():
    from shared.symbols import clean_symbol
    assert clean_symbol("bad symbol") is None


def test_canonical_url_never_raises_on_empty_relative_or_malformed_input():
    assert items.canonical_url("") == ""
    assert items.canonical_url(None) == ""
    assert items.canonical_url("/story/x?utm_source=a#f") == "/story/x"
    # urlsplit raises ValueError on a bracketed netloc that is not an IPv6 host
    assert items.canonical_url("http://[bad/x") == "http://[bad/x"
    assert len(items.item_id("http://[bad/x")) == 16


def test_canonical_url_lowercases_only_the_host():
    assert items.canonical_url("https://WWW.A.com/Path/X") == "https://www.a.com/Path/X"


def test_make_item_public_is_a_real_bool_and_every_field_present():
    it = items.make_item(source="S", title="  T  ", url="https://a.com/x",
                         published_at=None, public=1, now="n")
    assert it["public"] is True
    assert set(it) == set(items.FIELDS)
    assert it["title"] == "T"
    off = items.make_item(source="S", title="T", url="https://a.com/x",
                          published_at=None, public=0, now="n")
    assert off["public"] is False
