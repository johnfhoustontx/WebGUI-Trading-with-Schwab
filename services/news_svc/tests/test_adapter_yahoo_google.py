import pathlib

from services.news_svc.adapters import google_news, yahoo_ticker

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"


def test_yahoo_urls_expand_over_the_ticker_set():
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker",
            "url": "https://f/rss?s={symbol}&x=1"}
    assert yahoo_ticker.urls(feed, ["SPY", "$SPX"]) == [("SPY", "https://f/rss?s=SPY&x=1")]


def test_yahoo_urls_without_a_symbol_placeholder_yield_nothing():
    # A template that cannot name a symbol would fetch the SAME page once per
    # ticker and tag each copy with a different symbol - so it yields no URLs.
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "url": "https://f/rss"}
    assert yahoo_ticker.urls(feed, ["SPY", "NVDA"]) == []
    assert yahoo_ticker.urls({"name": "Y", "kind": "yahoo_ticker"}, ["SPY"]) == []


def test_yahoo_items_carry_the_symbol_they_were_fetched_for():
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "public": True, "url": "x"}
    out = yahoo_ticker.parse((FIX / "yahoo_nvda.xml").read_bytes(), feed, NOW,
                             universe=["NVDA"], symbol="NVDA")
    assert len(out) >= 5
    assert all(i["tickers"][0] == "NVDA" for i in out)
    assert all(i["kind"] == "yahoo_ticker" and i["source"] == "Yahoo Finance" for i in out)
    assert all(i["tickers"].count("NVDA") == 1 for i in out)


def test_yahoo_keeps_other_explicit_tickers_after_the_fetched_symbol():
    body = b"""<rss><channel><item><title>Chips: $AMD and $NVDA rally</title>
    <link>https://a.com/x</link></item></channel></rss>"""
    feed = {"name": "Yahoo Finance", "kind": "yahoo_ticker", "url": "x"}
    out = yahoo_ticker.parse(body, feed, NOW, universe=["AMD", "NVDA"], symbol="NVDA")
    assert out[0]["tickers"] == ["NVDA", "AMD"]


def test_google_news_search_url_is_encoded():
    feed = {"name": "WSJ", "kind": "google_news", "query": "site:wsj.com markets when:1d"}
    assert google_news.url(feed) == ("https://news.google.com/rss/search?q=site%3Awsj.com+markets+when%3A1d"
                                     "&hl=en-US&gl=US&ceid=US%3Aen")


def test_google_news_uses_the_publisher_as_original_source_and_strips_the_suffix():
    feed = {"name": "WSJ", "kind": "google_news", "public": True, "query": "q"}
    out = google_news.parse((FIX / "google_wsj.xml").read_bytes(), feed, NOW, universe=[])
    assert out
    assert out[0]["original_source"]            # the <source> element, e.g. "WSJ"
    assert not out[0]["title"].endswith(" - WSJ")  # Google appends " - <publisher>"
    assert all(i["kind"] == "google_news" for i in out)
    assert all(not i["title"].endswith(f" - {i['original_source']}") for i in out)
    # Links are Google redirects, kept as-is in v1.
    assert out[0]["url"].startswith("https://news.google.com/")


def test_google_news_strips_only_the_exact_publisher_suffix():
    body = b"""<rss><channel>
    <item><title>Oil - WSJ desk says prices rise - Reuters</title>
      <link>https://news.google.com/a</link><source url="https://wsj.com">WSJ</source></item>
    <item><title>Stocks close higher - WSJ</title>
      <link>https://news.google.com/b</link><source url="https://wsj.com">WSJ</source></item>
    <item><title>Markets rally on WSJ</title>
      <link>https://news.google.com/c</link><source url="https://wsj.com">WSJ</source></item>
    <item><title>No publisher here - WSJ</title>
      <link>https://news.google.com/d</link></item>
    </channel></rss>"""
    feed = {"name": "WSJ", "kind": "google_news", "query": "q"}
    out = google_news.parse(body, feed, NOW, universe=[])
    assert [i["title"] for i in out] == [
        "Oil - WSJ desk says prices rise - Reuters",   # suffix is another publisher
        "Stocks close higher",                         # exact " - WSJ" suffix removed
        "Markets rally on WSJ",                        # no " - " separator
        "No publisher here - WSJ",                     # no <source>: nothing to strip
    ]


def test_google_news_strips_a_publisher_containing_an_ampersand():
    body = b"""<rss><channel>
    <item><title>X rises - AT&amp;T</title>
      <link>https://news.google.com/a</link><source url="https://att.com">AT&amp;T</source></item>
    </channel></rss>"""
    feed = {"name": "G", "kind": "google_news", "query": "q"}
    out = google_news.parse(body, feed, NOW, universe=[])
    assert out[0]["original_source"] == "AT&T"
    assert out[0]["title"] == "X rises"


# --- review fixes: dotted symbols, cleaned tags, looser suffix, bad config ---

def test_yahoo_urls_use_the_dash_form_for_a_dotted_symbol_but_keep_the_tag():
    # Yahoo spells class shares BRK-B; the dotted form returns an empty
    # channel silently. The pair keeps the ORIGINAL symbol as its tag.
    feed = {"name": "Y", "kind": "yahoo_ticker", "url": "https://f/rss?s={symbol}"}
    assert yahoo_ticker.urls(feed, ["BRK.B", "SPY"]) == [
        ("BRK.B", "https://f/rss?s=BRK-B"),
        ("SPY", "https://f/rss?s=SPY"),
    ]


def test_yahoo_urls_percent_encode_the_symbol():
    feed = {"name": "Y", "kind": "yahoo_ticker", "url": "https://f/rss?s={symbol}"}
    assert yahoo_ticker.urls(feed, ["A&B"]) == [("A&B", "https://f/rss?s=A%26B")]


def test_yahoo_urls_refuse_a_non_string_url():
    for bad in (["https://f/{symbol}"], 5, {"u": "{symbol}"}):
        feed = {"name": "Y", "kind": "yahoo_ticker", "url": bad}
        assert yahoo_ticker.urls(feed, ["SPY"]) == []


_ONE_ITEM = b"""<rss><channel><item><title>Something happened</title>
<link>https://a.com/x</link></item></channel></rss>"""


def test_yahoo_parse_cleans_the_fetched_symbol():
    feed = {"name": "Y", "kind": "yahoo_ticker", "url": "x"}
    out = yahoo_ticker.parse(_ONE_ITEM, feed, NOW, universe=["NVDA"], symbol="nvda")
    assert out[0]["tickers"] == ["NVDA"]


def test_yahoo_parse_does_not_force_an_unusable_symbol():
    feed = {"name": "Y", "kind": "yahoo_ticker", "url": "x"}
    for bad in (None, "", "not a ticker!", "TOOLONGSYMBOL"):
        out = yahoo_ticker.parse(_ONE_ITEM, feed, NOW, universe=["NVDA"], symbol=bad)
        assert out[0]["tickers"] == []


def test_yahoo_parse_does_not_duplicate_a_cleaned_symbol():
    body = b"""<rss><channel><item><title>$NVDA rallies</title>
    <link>https://a.com/x</link></item></channel></rss>"""
    feed = {"name": "Y", "kind": "yahoo_ticker", "url": "x"}
    out = yahoo_ticker.parse(body, feed, NOW, universe=["NVDA"], symbol="nvda")
    assert out[0]["tickers"] == ["NVDA"]


def _google_item(title, pub):
    return (f"<item><title>{title}</title><link>https://news.google.com/a</link>"
            f"<source url=\"https://p.com\">{pub}</source></item>")


def test_google_news_strips_double_spaces_and_en_or_em_dashes():
    body = ("<rss><channel>"
            + _google_item("Stocks close higher  -  WSJ", "WSJ")
            + _google_item("Oil rises – Reuters", "Reuters")
            + _google_item("Fed holds — Bloomberg ", "Bloomberg")
            + _google_item("Dow at 50000 -WSJ", "WSJ")
            + "</channel></rss>").encode()
    feed = {"name": "G", "kind": "google_news", "query": "q"}
    out = google_news.parse(body, feed, NOW, universe=[])
    assert [i["title"] for i in out] == [
        "Stocks close higher",
        "Oil rises",
        "Fed holds",
        "Dow at 50000 -WSJ",   # no whitespace before the dash: not a suffix
    ]


def test_google_news_never_strips_to_an_empty_title():
    # A title that IS only the suffix keeps the original rather than blanking.
    assert google_news._strip_publisher(" - WSJ", "WSJ") == " - WSJ"
    assert google_news._strip_publisher("\t\u2014 WSJ ", "WSJ") == "\t\u2014 WSJ "
    assert google_news._strip_publisher("Up - WSJ", "") == "Up - WSJ"


def test_google_news_publisher_is_matched_literally_not_as_a_regex():
    body = ("<rss><channel>" + _google_item("X rises - A.B", "A.B")
            + _google_item("Y rises - AxB", "A.B")
            + "</channel></rss>").encode()
    feed = {"name": "G", "kind": "google_news", "query": "q"}
    out = google_news.parse(body, feed, NOW, universe=[])
    assert [i["title"] for i in out] == ["X rises", "Y rises - AxB"]


def test_google_news_url_is_none_for_a_missing_or_unusable_query():
    # Contract: None means "skip this feed" - the poll cycle never fetches
    # "q=None" or an empty search.
    for feed in ({"name": "G", "kind": "google_news"},
                 {"name": "G", "kind": "google_news", "query": None},
                 {"name": "G", "kind": "google_news", "query": ""},
                 {"name": "G", "kind": "google_news", "query": "   "},
                 {"name": "G", "kind": "google_news", "query": ["site:wsj.com"]},
                 {"name": "G", "kind": "google_news", "query": 5}):
        assert google_news.url(feed) is None
