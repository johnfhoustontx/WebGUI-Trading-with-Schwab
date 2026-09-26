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


# --- review fixes: title keys ------------------------------------------------

import pytest  # noqa: E402


def test_title_key_is_none_for_empty_or_punctuation_only_titles():
    assert items.title_key("") is None
    assert items.title_key(None) is None
    assert items.title_key("!!!") is None


def test_title_key_is_none_for_a_title_too_short_to_identify_a_story():
    # "Stocks rise" names a hundred different stories; merging on it would
    # collapse unrelated items into one.
    assert items.title_key("Stocks rise") is None


def test_title_key_keeps_non_latin_and_accented_titles():
    key = items.title_key("日本株が上昇")
    assert key  # a real, non-empty key - not None, not ""
    assert items.title_key("日本株が下落") != key
    assert "nestlé" in items.title_key("Nestlé raises its outlook again")


def test_title_key_casefolds():
    assert items.title_key("STRASSE Deal Closes Today") == items.title_key("straße deal closes today")


# --- review fixes: explicit ticker forms -------------------------------------

@pytest.mark.parametrize("text", [
    "Meta (NASDAQ: META) jumps",
    "Meta (NASDAQ:META) jumps",
    "Meta (Nasdaq: META) jumps",
    "Meta (META) jumps",
    "Meta (META:NASDAQ) jumps",
])
def test_every_parenthesised_form_tags_meta(text):
    assert items.extract_tickers(text, ["META"]) == ["META"]


@pytest.mark.parametrize("text, sym", [
    ("Ford (NYSE:F) recalls", "F"),
    ("SPDR (NYSEARCA: SPY) inflows", "SPY"),
    ("US Steel (AMEX:X) bid", "X"),
    ("Something (CBOE: X) listed", "X"),
])
def test_exchange_prefixed_parentheses_tag_the_part_after_the_exchange(text, sym):
    assert items.extract_tickers(text, [sym]) == [sym]


def test_a_short_ticker_in_bare_parentheses_never_tags():
    assert items.extract_tickers("artificial intelligence (AI)", ["AI"]) == []
    assert items.extract_tickers("information technology (IT) spend", ["IT"]) == []
    assert items.extract_tickers("Ford (F) recalls", ["F"]) == []


def test_a_short_ticker_tags_from_a_cashtag_or_an_exchange_prefix():
    assert items.extract_tickers("Ford (NYSE: F) and $AI", ["F", "AI"]) == ["F", "AI"]


def test_a_class_share_cashtag_keeps_its_suffix():
    assert items.extract_tickers("$BRK.B hits a record", ["BRK.B"]) == ["BRK.B"]
    assert items.extract_tickers("Buy $NVDA.", ["NVDA"]) == ["NVDA"]


# --- review fixes: tracking keys ---------------------------------------------

def test_tracking_keys_are_case_insensitive_and_include_click_ids():
    assert items.canonical_url("https://a.com/x?UTM_Source=a&p=1") == "https://a.com/x?p=1"
    assert items.canonical_url("https://a.com/x?.tsrc=rss&p=1") == "https://a.com/x?p=1"
    assert items.canonical_url("https://a.com/x?fbclid=1&gclid=2") == "https://a.com/x"


# --- review fixes: make_item -------------------------------------------------

@pytest.mark.parametrize("url", ["", "   ", None])
def test_make_item_refuses_an_item_without_a_url(url):
    with pytest.raises(ValueError):
        items.make_item(source="S", title="T", url=url, published_at=None,
                        public=True, now="n")


def test_make_item_treats_a_string_topic_or_ticker_as_one_value():
    it = items.make_item(source="S", title="T", url="https://a.com/x",
                         published_at=None, public=True, now="n",
                         tickers="NVDA", topics="earnings")
    assert it["tickers"] == ["NVDA"]
    assert it["topics"] == ["earnings"]
