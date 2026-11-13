"""Port of the Node handle.test.js and mapStatus.test.js suites."""

import json
import re
from pathlib import Path

from services.xfeed_svc.mapping import extract_cashtags, map_status, normalize_handle

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def map_all(name, watched):
    return [map_status(s, watched) for s in fixture(name)["results"]]


def test_normalizes_common_handle_forms():
    assert normalize_handle("@Name") == "name"
    assert normalize_handle("  DeItaone ") == "deitaone"
    assert normalize_handle("https://x.com/Name/status/1") == "name"
    assert normalize_handle("x.com/name") == "name"
    assert normalize_handle("https://twitter.com/zerohedge") == "zerohedge"
    assert normalize_handle("https://www.x.com/unusual_whales?s=20") == "unusual_whales"


def test_rejects_invalid_handles():
    for bad in ["", "@", "x.com/home", "has space", "a-b", "abcdefghijklmnop", "na\nme", None]:
        assert normalize_handle(bad) is None, f"expected None for {bad!r}"


def test_em_with_replies_keeps_self_replies_and_drops_others():
    em = map_all("em.json", "elonmusk")
    assert len([p for p in em if p and p["type"] == "reply"]) == 3
    assert len([p for p in em if p and p["type"] == "repost"]) == 8
    assert len([p for p in em if p and p["type"] == "post"]) == 2
    # 6 replies to other people + 8 conversation-context tweets by other authors
    assert len([p for p in em if p is None]) == 14


def test_conversation_context_tweets_by_other_authors_are_dropped():
    kept = [p for p in map_all("em.json", "elonmusk") if p]
    for p in [p for p in kept if p["type"] != "repost"]:
        assert p["author_handle"].lower() == "elonmusk"


def test_nasa_repost_keeps_original_author_and_watched_account():
    rt = next(p for p in map_all("nasa.json", "nasa") if p["type"] == "repost")
    assert rt["account_handle"] == "nasa"
    assert rt["author_handle"].lower() != "nasa"


def test_plain_post_has_basic_fields():
    p = map_status(fixture("DeItaone.json")["results"][0], "deitaone")
    assert p["type"] == "post"
    assert re.match(r"^\d+$", p["status_id"])
    assert isinstance(p["created_at"], int)
    assert p["created_at"] > 1.7e12, "created_at is in ms"
    assert re.match(r"^https://x\.com/", p["url"])
    assert sorted(p["counts"].keys()) == ["likes", "replies", "reposts", "views"]
    assert p["raw"]


def test_video_media_mapped_with_thumb():
    media = [m for p in map_all("nasa.json", "nasa") for m in p["media"]]
    vid = next(m for m in media if m["type"] == "video")
    assert re.search(r"video\.twimg\.com", vid["url"])
    assert re.match(r"^https:", vid["thumb"])


def test_photo_media_uses_url_as_thumb():
    media = [m for p in map_all("nasa.json", "nasa") for m in p["media"]]
    photo = next(m for m in media if m["type"] == "photo")
    assert photo["thumb"] == photo["url"]


def test_quote_mapped():
    quoted = [p for p in map_all("zerohedge.json", "zerohedge") if p["quote"]]
    assert len(quoted) == 3
    assert quoted[0]["quote"]["author_handle"]
    assert quoted[0]["quote"]["url"]


def test_extract_cashtags_returns_unique_uppercase_tickers_in_order():
    assert extract_cashtags("Buy $nvda and $TSLA, not $5 or $NVDA again") == ["NVDA", "TSLA"]
    assert extract_cashtags("$BRK.B up") == ["BRK.B"]
    assert extract_cashtags("") == []
