import pathlib

from services.news_svc.adapters import rss

FIX = pathlib.Path(__file__).parent / "fixtures"
NOW = "2026-09-26T00:00:00+00:00"
FEED = {"name": "MarketWatch", "kind": "rss", "public": True, "url": "https://x"}


def test_parses_every_entry_with_the_feeds_name_as_source():
    out = rss.parse((FIX / "marketwatch.xml").read_bytes(), FEED, NOW, universe=["AAPL"])
    assert len(out) >= 5
    it = out[0]
    assert it["source"] == "MarketWatch" and it["kind"] == "rss" and it["public"] is True
    assert it["url"].startswith("https://www.marketwatch.com/") and "mod=" not in it["url"]
    assert it["published_at"].endswith("+00:00")
    assert it["title"]


def test_entries_without_a_link_are_skipped():
    body = b"""<rss><channel><item><title>no link</title></item>
    <item><title>ok</title><link>https://a.com/x</link></item></channel></rss>"""
    out = rss.parse(body, FEED, NOW, universe=[])
    assert [i["title"] for i in out] == ["ok"]


def test_entries_with_a_whitespace_only_link_are_skipped():
    body = b"""<rss><channel><item><title>blank</title><link>   </link></item>
    <item><title>ok</title><link>https://a.com/x</link></item></channel></rss>"""
    out = rss.parse(body, FEED, NOW, universe=[])
    assert [i["title"] for i in out] == ["ok"]


def test_missing_date_falls_back_to_now():
    body = b"""<rss><channel><item><title>t</title><link>https://a.com/x</link></item></channel></rss>"""
    assert rss.parse(body, FEED, NOW, universe=[])[0]["published_at"] == NOW


def test_garbage_is_an_empty_list_not_an_exception():
    assert rss.parse(b"\x00\x01 not xml", FEED, NOW, universe=[]) == []
