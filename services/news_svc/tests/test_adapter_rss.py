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


# --- review fixes -----------------------------------------------------------

import datetime as dt
import time


def _one(item_xml: bytes) -> bytes:
    return b"<rss><channel>" + item_xml + b"</channel></rss>"


def test_a_body_naming_a_real_file_is_never_opened():
    # feedparser treats a non-URL bytes/str as a FILENAME; the adapter must
    # only ever parse the bytes it was handed.
    path = str(FIX / "marketwatch.xml").encode()
    assert rss.parse(path, FEED, NOW, universe=[]) == []
    rel = b"services/news_svc/tests/fixtures/marketwatch.xml"
    assert rss.parse(rel, FEED, NOW, universe=[]) == []


def test_a_non_bytes_body_is_an_empty_list():
    assert rss.parse(None, FEED, NOW, universe=[]) == []
    assert rss.parse(str(FIX / "marketwatch.xml"), FEED, NOW, universe=[]) == []


def test_an_atom_date_past_year_9999_falls_back_to_now_and_keeps_the_feed():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>far</title><link href="https://a.com/far"/>
      <updated>9999-12-31T23:59:59-12:00</updated></entry>
    <entry><title>ok</title><link href="https://a.com/ok"/>
      <updated>2026-09-25T12:00:00Z</updated></entry></feed>"""
    out = rss.parse(body, FEED, NOW, universe=[])
    by = {i["title"]: i for i in out}
    assert set(by) == {"far", "ok"}
    assert by["far"]["published_at"] == NOW
    assert by["ok"]["published_at"] == "2026-09-25T12:00:00+00:00"


def test_a_dc_date_before_year_1_falls_back_to_now_and_keeps_the_feed():
    body = b"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
    <item><title>ancient</title><link>https://a.com/a</link>
      <dc:date>0001-01-01T00:00:00+14:00</dc:date></item>
    <item><title>ok</title><link>https://a.com/ok</link></item></channel></rss>"""
    out = rss.parse(body, FEED, NOW, universe=[])
    by = {i["title"]: i for i in out}
    assert set(by) == {"ancient", "ok"}
    assert by["ancient"]["published_at"] == NOW


def test_an_entry_that_raises_is_skipped_and_the_rest_survive(monkeypatch):
    real = rss._link

    def boom(entry):
        if entry.get("title") == "bad":
            raise RuntimeError("unexpected")
        return real(entry)

    monkeypatch.setattr(rss, "_link", boom)
    body = _one(b"<item><title>bad</title><link>https://a.com/b</link></item>"
                b"<item><title>good</title><link>https://a.com/g</link></item>")
    assert [i["title"] for i in rss.parse(body, FEED, NOW, universe=[])] == ["good"]


def test_a_future_date_is_clamped_to_now():
    body = _one(b"<item><title>t</title><link>https://a.com/x</link>"
                b"<pubDate>Fri, 01 Jan 2100 00:00:00 GMT</pubDate></item>")
    assert rss.parse(body, FEED, NOW, universe=[])[0]["published_at"] == NOW


def test_a_date_within_the_clock_skew_is_kept():
    now = dt.datetime.fromisoformat(NOW)
    ahead = now + dt.timedelta(seconds=rss.FUTURE_SKEW_SEC - 60)
    stamp = ahead.strftime("%a, %d %b %Y %H:%M:%S GMT").encode()
    body = _one(b"<item><title>t</title><link>https://a.com/x</link><pubDate>"
                + stamp + b"</pubDate></item>")
    assert rss.parse(body, FEED, NOW, universe=[])[0]["published_at"] == ahead.isoformat()
    assert rss.FUTURE_SKEW_SEC == 600


def test_a_cdata_html_title_is_stripped_and_unescaped():
    body = _one(b"<item><title><![CDATA[<b>Bold</b> &amp; news]]></title>"
                b"<link>https://a.com/x</link></item>")
    assert rss.parse(body, FEED, NOW, universe=[])[0]["title"] == "Bold & news"


def test_an_escaped_entity_atom_title_is_unescaped():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <title type="html">Q&amp;amp;A:   &lt;i&gt;Fed&lt;/i&gt;
      speaks</title><link href="https://a.com/x"/></entry></feed>"""
    assert rss.parse(body, FEED, NOW, universe=[])[0]["title"] == "Q&A: Fed speaks"


def test_teaser_whitespace_including_nbsp_and_newlines_is_collapsed():
    body = _one(b"<item><title>t</title><link>https://a.com/x</link>"
                b"<description>one&#160;&#160;two\n\n  three</description></item>")
    assert rss.parse(body, FEED, NOW, universe=[])[0]["teaser"] == "one two three"


def test_a_long_run_of_open_angles_parses_quickly():
    junk = "<" * 200_000
    body = _one(b"<item><title>t</title><link>https://a.com/x</link><description><![CDATA["
                + junk.encode() + b"]]></description></item>")
    t0 = time.perf_counter()
    out = rss.parse(body, FEED, NOW, universe=[])
    assert time.perf_counter() - t0 < 1.0
    assert len(out) == 1 and len(out[0]["teaser"]) <= 400


def test_a_feed_without_a_public_flag_is_private():
    feed = {"name": "MarketWatch", "kind": "rss", "url": "https://x"}
    body = _one(b"<item><title>t</title><link>https://a.com/x</link></item>")
    assert rss.parse(body, feed, NOW, universe=[])[0]["public"] is False


def test_only_http_and_https_links_are_kept():
    body = _one(b"<item><title>js</title><link>javascript:alert(1)</link></item>"
                b"<item><title>data</title><link>data:text/html,&lt;b&gt;x&lt;/b&gt;</link></item>"
                b"<item><title>ftp</title><link>ftp://a.com/x</link></item>"
                b"<item><title>upper</title><link>HTTPS://A.com/u</link></item>"
                b"<item><title>plain</title><link>http://a.com/p</link></item>")
    out = rss.parse(body, FEED, NOW, universe=[])
    assert [i["title"] for i in out] == ["upper", "plain"]


# --- second review round ------------------------------------------------------

import logging

import pytest


def _title(title_xml: bytes) -> str:
    body = _one(b"<item>" + title_xml + b"<link>https://a.com/x</link></item>")
    return rss.parse(body, FEED, NOW, universe=[])[0]["title"]


@pytest.mark.parametrize("title_xml, expected", [
    (b"<title>S&amp;P &lt; 5000 but &gt; 4000</title>", "S&P < 5000 but > 4000"),
    (b"<title><![CDATA[S&P < 5000 > 4000]]></title>", "S&P < 5000 > 4000"),
    (b"<title>Yields &lt;3% as SPY &gt;&gt; peers</title>", "Yields <3% as SPY >> peers"),
])
def test_a_plain_text_angle_bracket_is_kept_not_stripped_as_a_tag(title_xml, expected):
    assert _title(title_xml) == expected


def test_real_tags_and_comments_are_still_stripped():
    assert _title(b"<title>x &lt;b&gt;y&lt;/b&gt; &lt;!-- c --&gt; z</title>") == "x y z"
    assert _title(b"<title><![CDATA[<i>a</i> <br/>b <?pi x?>c]]></title>") == "a b c"


@pytest.mark.parametrize("junk", ["<" * 200_000, "<a" * 100_000])
def test_a_long_run_of_tag_openers_parses_quickly(junk):
    body = _one(b"<item><title>t</title><link>https://a.com/x</link><description><![CDATA["
                + junk.encode() + b"]]></description></item>")
    t0 = time.perf_counter()
    out = rss.parse(body, FEED, NOW, universe=[])
    assert time.perf_counter() - t0 < 1.0
    assert len(out) == 1 and len(out[0]["teaser"]) <= 400


def test_a_naive_now_is_read_as_utc_keeps_every_entry_and_still_clamps():
    naive = "2026-09-26T12:00:00"
    body = _one(b"<item><title>past</title><link>https://a.com/p</link>"
                b"<pubDate>Fri, 25 Sep 2026 12:00:00 GMT</pubDate></item>"
                b"<item><title>future</title><link>https://a.com/f</link>"
                b"<pubDate>Fri, 01 Jan 2100 00:00:00 GMT</pubDate></item>")
    by = {i["title"]: i for i in rss.parse(body, FEED, naive, universe=[])}
    assert set(by) == {"past", "future"}
    assert by["past"]["published_at"] == "2026-09-25T12:00:00+00:00"
    assert by["future"]["published_at"] == "2026-09-26T12:00:00+00:00"


def test_skipped_entries_log_one_warning_per_parse_with_feed_and_count(monkeypatch, caplog):
    real = rss._link

    def boom(entry):
        if entry.get("title", "").startswith("bad"):
            raise RuntimeError("unexpected")
        return real(entry)

    monkeypatch.setattr(rss, "_link", boom)
    body = _one(b"<item><title>bad1</title><link>https://a.com/1</link></item>"
                b"<item><title>bad2</title><link>https://a.com/2</link></item>"
                b"<item><title>good</title><link>https://a.com/g</link></item>")
    with caplog.at_level(logging.DEBUG, logger=rss.__name__):
        out = rss.parse(body, FEED, NOW, universe=[])
    assert [i["title"] for i in out] == ["good"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "MarketWatch" in msg and "2" in msg


def test_a_clean_parse_logs_no_warning(caplog):
    body = _one(b"<item><title>t</title><link>https://a.com/x</link></item>")
    with caplog.at_level(logging.DEBUG, logger=rss.__name__):
        rss.parse(body, FEED, NOW, universe=[])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
