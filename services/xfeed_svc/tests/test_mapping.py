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


def _has_surrogate(value) -> bool:
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def test_a_lone_surrogate_becomes_the_replacement_character_everywhere_incl_raw():
    # json.loads yields a lone "\ud83d" from FxEmbed's text; Node writes U+FFFD for it,
    # and SQLite cannot store it at all (UnicodeEncodeError), so map_status scrubs it.
    status = json.loads(
        '{"id": "1", "url": "https://x.com/acct/status/1", "text": "hi \ud83d $NVDA",'
        ' "author": {"screen_name": "acct", "name": "A \ud83d", "avatar_url": null},'
        ' "created_timestamp": 1, "quote": {"text": "q \ud83d", "author": {"name": "Q", "screen_name": "q"}},'
        ' "media": {"all": [{"type": "photo", "url": "https://p/1.jpg", "altText": "alt \ud83d"}]},'
        ' "extra": ["\ud83d", {"\ud83d key": 1}], "ok": "pair \ud83d\ude00 kept"}')
    p = map_status(status, "acct")
    assert p["text"] == "hi � $NVDA"
    assert p["cashtags"] == ["NVDA"]
    assert p["author_name"] == "A �"
    assert p["quote"]["text"] == "q �"
    assert p["media"][0]["alt"] == "alt �"
    assert p["raw"]["text"] == "hi � $NVDA"
    assert p["raw"]["extra"] == ["�", {"� key": 1}]
    assert p["raw"]["ok"] == "pair \U0001F600 kept"        # a valid pair is untouched
    assert not _has_surrogate(p)
