import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest

from shared.bus import Bus
from shared.notify import x_post

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 22, 10, 0, tzinfo=CT)
# Real magic prefixes: x_post refuses bytes that are neither PNG nor JPEG.
PNG = b"\x89PNG\r\n\x1a\n" + b"x"
JPEG = b"\xff\xd8\xff" + b"x"
CREDS = {"api_key": "k", "api_secret": "s", "access_token": "t", "access_secret": "a"}


def _cfg(**over):
    x = {"enabled": True, "dry_run": False, "daily_cap": 2, **CREDS,
         "kinds": {"report": {"enabled": True}, "trade_idea": {"enabled": True},
                   "marketing": {"enabled": True}}}
    x.update(over)
    return {"x": x}


class FakeSession:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.fail:
            raise self.fail
        class R:
            status_code = 200
            def __init__(s, body): s._b = body
            def json(s): return s._b
            text = ""
        if url.endswith("/media/upload"):
            return R({"data": {"id": "m1"}})
        return R({"data": {"id": "99", "text": "t"}})


@pytest.fixture(autouse=True)
def _jsonl(tmp_path, monkeypatch):
    path = tmp_path / "x.jsonl"
    monkeypatch.setattr(x_post, "X_POSTS_LOG", path)
    return path


@pytest.fixture
def bus():
    return Bus(fake=True)


def test_posts_text_and_image_and_logs(bus, monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(x_post, "_session", lambda creds: s)
    out = x_post.post(bus, "hello", PNG, kind="marketing", now=NOW, config=_cfg())
    assert out["ok"] and out["id"] == "99" and out["url"].endswith("/99")
    assert s.calls[0][0].endswith("/2/media/upload")
    up = s.calls[0][1]
    assert up["files"]["media"] == ("card.png", PNG, "image/png")
    assert up["data"]["media_category"] == "tweet_image"
    assert s.calls[1][0].endswith("/2/tweets")
    assert s.calls[1][1]["json"] == {"text": "hello", "media": {"media_ids": ["m1"]}}
    log = bus.cache_get(x_post.LOG_KEY).payload["posts"]
    assert log[0]["kind"] == "marketing" and log[0]["status"] == "posted"


def test_dry_run_calls_nothing_but_logs_and_counts_nothing(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW, config=_cfg(dry_run=True))
    assert out["ok"] and out["dry_run"]
    assert bus.cache_get(x_post.LOG_KEY).payload["posts"][0]["status"] == "dry_run"
    assert x_post.posted_today(bus, NOW) == 0


@pytest.mark.parametrize("over,reason", [
    ({"enabled": False}, "disabled"),
    ({"kinds": {"report": {"enabled": False}}}, "report disabled"),
    ({"api_key": ""}, "no credentials"),
])
def test_refusals_are_logged_with_their_reason(bus, over, reason):
    out = x_post.post(bus, "hi", None, kind="report", now=NOW, config=_cfg(**over))
    assert not out["ok"] and out["error"] == reason
    assert bus.cache_get(x_post.LOG_KEY).payload["posts"][0]["reason"] == reason


def test_the_daily_cap_refuses_the_third_post(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession())
    for _ in range(2):
        assert x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())["ok"]
    out = x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())
    assert out["error"] == "daily cap (2) reached"
    tomorrow = NOW + dt.timedelta(days=1)
    assert x_post.post(bus, "a", None, kind="marketing", now=tomorrow, config=_cfg())["ok"]


def test_a_network_error_never_raises(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession(fail=OSError("down")))
    out = x_post.post(bus, "a", PNG, kind="marketing", now=NOW, config=_cfg())
    # The upload failed, so nothing was sent: a definite failure, not counted.
    # Its reason names the exception type only - a raw message can carry secrets.
    assert not out["ok"] and "OSError" in out["error"] and "down" not in out["error"]
    assert bus.cache_get(x_post.LOG_KEY).payload["posts"][0]["status"] == "failed"
    assert x_post.posted_today(bus, NOW) == 0


def test_an_http_error_is_reported_not_raised(bus, monkeypatch):
    class Bad(FakeSession):
        def post(self, url, **kw):
            class R:
                status_code = 403; text = '{"detail":"forbidden"}'
                def json(s): return {"detail": "forbidden"}
            return R()
    monkeypatch.setattr(x_post, "_session", lambda c: Bad())
    out = x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and "403" in out["error"]


def test_the_log_keeps_the_last_100(bus):
    for i in range(105):
        x_post.post(bus, str(i), None, kind="marketing", now=NOW,
                    config=_cfg(enabled=False))
    assert len(bus.cache_get(x_post.LOG_KEY).payload["posts"]) == 100


def test_the_jsonl_gets_one_line_per_attempt(bus, monkeypatch, _jsonl):
    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession())
    x_post.post(bus, "a", None, kind="marketing", now=NOW, config=_cfg())
    x_post.post(bus, "b", None, kind="marketing", now=NOW, config=_cfg(enabled=False))
    x_post.post(bus, "c", None, kind="report", now=NOW, config=_cfg(dry_run=True))
    lines = [json.loads(ln) for ln in _jsonl.read_text(encoding="utf-8").splitlines()]
    assert [ln["status"] for ln in lines] == ["posted", "refused", "dry_run"]
    assert [ln["text"] for ln in lines] == ["a", "b", "c"]


def test_an_unknown_kind_is_refused(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="spam", now=NOW, config=_cfg())
    assert not out["ok"] and out["error"] == "spam disabled"


def test_an_empty_post_is_refused_before_any_network_call(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "   ", None, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and out["error"] == "empty post"
    # A dry run of an empty post is refused too, not reported as a success.
    out = x_post.post(bus, "", None, kind="marketing", now=NOW,
                      config=_cfg(dry_run=True))
    assert not out["ok"] and out["error"] == "empty post"


def test_an_image_alone_is_not_an_empty_post(bus, monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "", PNG, kind="marketing", now=NOW, config=_cfg())
    assert out["ok"]


@pytest.mark.parametrize("kinds", [5, "report", None, ["report"]])
def test_malformed_kinds_refuse_rather_than_raise(bus, monkeypatch, kinds):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW, config=_cfg(kinds=kinds))
    assert not out["ok"] and out["error"] == "report disabled"


def test_a_malformed_kind_entry_refuses_rather_than_raises(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW,
                      config=_cfg(kinds={"report": True}))
    assert not out["ok"] and out["error"] == "report disabled"


def test_a_malformed_daily_cap_falls_back_to_the_default(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession())
    bus.cache_set(x_post.COUNT_KEY, {"day": NOW.date().isoformat(),
                                     "count": x_post.DEFAULT_DAILY_CAP - 1})
    assert x_post.post(bus, "a", None, kind="marketing", now=NOW,
                       config=_cfg(daily_cap="lots"))["ok"]
    out = x_post.post(bus, "a", None, kind="marketing", now=NOW,
                      config=_cfg(daily_cap="lots"))
    assert out["error"] == f"daily cap ({x_post.DEFAULT_DAILY_CAP}) reached"


def test_record_refusal_writes_a_refused_entry(bus, _jsonl):
    x_post.record_refusal(bus, "trade_idea", "caption", "image over 5 MB", now=NOW,
                          image=True)
    entry = bus.cache_get(x_post.LOG_KEY).payload["posts"][0]
    assert entry["status"] == "refused" and entry["reason"] == "image over 5 MB"
    assert entry["kind"] == "trade_idea" and entry["image"] is True
    assert entry["at"] == NOW.isoformat()
    assert json.loads(_jsonl.read_text(encoding="utf-8"))["reason"] == "image over 5 MB"


def test_a_broken_bus_never_raises(monkeypatch):
    class Broken:
        def cache_get(self, key):
            raise RuntimeError("redis gone")

        def cache_set(self, key, payload, **kw):
            raise RuntimeError("redis gone")

    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession())
    out = x_post.post(Broken(), "a", None, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and "redis gone" in out["error"]


# --- secrets stay out of the log ---------------------------------------------

def _logged(bus, jsonl):
    return (json.dumps(bus.cache_get(x_post.LOG_KEY).payload)
            + jsonl.read_text(encoding="utf-8"))


def test_a_non_string_credential_is_refused_and_never_logged(bus, monkeypatch, _jsonl):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW,
                      config=_cfg(api_secret=12345678))
    assert not out["ok"] and out["error"] == "no credentials"
    assert "12345678" not in _logged(bus, _jsonl)


def test_a_whitespace_credential_is_refused(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW,
                      config=_cfg(access_token="   "))
    assert out["error"] == "no credentials"


def test_a_foreign_exception_message_is_never_logged(bus, monkeypatch, _jsonl):
    monkeypatch.setattr(x_post, "_session",
                        lambda c: FakeSession(fail=ValueError("secret-abc")))
    out = x_post.post(bus, "hi", PNG, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and "ValueError" in out["error"]
    assert "secret-abc" not in out["error"]
    assert "secret-abc" not in _logged(bus, _jsonl)


def test_a_session_that_cannot_be_built_is_never_logged(bus, monkeypatch, _jsonl):
    def boom(c):
        raise ValueError("Only unicode objects are escapable. Got secret-abc")
    monkeypatch.setattr(x_post, "_session", boom)
    out = x_post.post(bus, "hi", None, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and "secret-abc" not in _logged(bus, _jsonl)
    assert x_post.posted_today(bus, NOW) == 0


# --- an unconfirmed post fails closed ----------------------------------------

class _Resp:
    def __init__(self, status, body=None, bad_json=False, text=""):
        self.status_code, self._b, self._bad, self.text = status, body, bad_json, text

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._b


class ScriptedSession:
    """``upload`` / ``create``: a response to return, or an exception to raise."""

    def __init__(self, upload=None, create=None):
        self.upload = upload if upload is not None else _Resp(200, {"data": {"id": "m1"}})
        self.create = create
        self.calls = []

    def post(self, url, **kw):
        self.calls.append(url)
        r = self.upload if url.endswith("/media/upload") else self.create
        if isinstance(r, BaseException):
            raise r
        return r


def _entry0(bus):
    return bus.cache_get(x_post.LOG_KEY).payload["posts"][0]


def test_a_create_timeout_is_unknown_and_counted(bus, monkeypatch):
    import requests
    s = ScriptedSession(create=requests.Timeout("read timed out token=zzz"))
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", PNG, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and out["unknown"] is True
    assert out["error"] == "sent; X did not confirm (Timeout)"
    e = _entry0(bus)
    assert e["status"] == "unknown" and e["reason"] == "sent; X did not confirm (Timeout)"
    assert x_post.posted_today(bus, NOW) == 1


def test_a_create_connection_error_is_unknown_and_counted(bus, monkeypatch):
    import requests
    s = ScriptedSession(create=requests.ConnectionError("reset"))
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", None, kind="marketing", now=NOW, config=_cfg())
    assert out["unknown"] is True and _entry0(bus)["status"] == "unknown"
    assert x_post.posted_today(bus, NOW) == 1


def test_a_non_json_2xx_create_is_unknown_and_counted(bus, monkeypatch):
    s = ScriptedSession(create=_Resp(201, bad_json=True, text="<html>"))
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", None, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and out["unknown"] is True
    assert x_post.posted_today(bus, NOW) == 1


def test_a_2xx_create_with_no_id_is_posted_without_an_id(bus, monkeypatch):
    s = ScriptedSession(create=_Resp(201, {"data": {}}))
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", None, kind="marketing", now=NOW, config=_cfg())
    assert out["ok"] and out["id"] is None and out["url"] is None
    assert not out.get("unknown")
    e = _entry0(bus)
    assert e["status"] == "posted" and e["id"] is None and e["url"] is None
    assert x_post.posted_today(bus, NOW) == 1


def test_a_create_403_is_failed_and_not_counted(bus, monkeypatch):
    s = ScriptedSession(create=_Resp(403, {"detail": "forbidden"}, text="forbidden"))
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", PNG, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and not out.get("unknown") and "403" in out["error"]
    assert _entry0(bus)["status"] == "failed"
    assert x_post.posted_today(bus, NOW) == 0


def test_an_upload_failure_is_failed_and_never_creates(bus, monkeypatch):
    s = ScriptedSession(upload=_Resp(500, text="oops"), create=_Resp(201, {"data": {"id": "9"}}))
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", PNG, kind="marketing", now=NOW, config=_cfg())
    assert not out["ok"] and not out.get("unknown") and "500" in out["error"]
    assert _entry0(bus)["status"] == "failed"
    assert s.calls == [f"{x_post.API}/media/upload"]
    assert x_post.posted_today(bus, NOW) == 0


# --- the kind allow-list -----------------------------------------------------

def test_a_kind_outside_KINDS_is_refused_even_when_enabled(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="spam", now=NOW,
                      config=_cfg(kinds={"spam": {"enabled": True}}))
    assert not out["ok"] and out["error"] == "spam disabled"


# --- gate order --------------------------------------------------------------

def test_a_dry_run_needs_no_credentials(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", None, kind="report", now=NOW,
                      config=_cfg(dry_run=True, api_key="", access_secret=""))
    assert out["ok"] and out["dry_run"]


def test_a_dry_run_ignores_a_reached_cap_and_counts_nothing(bus, monkeypatch):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    bus.cache_set(x_post.COUNT_KEY, {"day": NOW.date().isoformat(), "count": 2})
    out = x_post.post(bus, "hi", None, kind="report", now=NOW, config=_cfg(dry_run=True))
    assert out["ok"] and out["dry_run"]
    assert x_post.posted_today(bus, NOW) == 2


def test_a_failed_count_write_after_a_post_still_reports_posted(bus, monkeypatch):
    class CountFails:
        def cache_get(self, key):
            return bus.cache_get(key)

        def cache_set(self, key, payload, **kw):
            if key == x_post.COUNT_KEY:
                raise RuntimeError("redis hiccup")
            return bus.cache_set(key, payload, **kw)

    monkeypatch.setattr(x_post, "_session", lambda c: FakeSession())
    out = x_post.post(CountFails(), "hi", None, kind="marketing", now=NOW, config=_cfg())
    assert out["ok"] and out["id"] == "99"
    assert _entry0(bus)["status"] == "posted"


# --- the image type is read from its bytes -----------------------------------

def test_a_jpeg_is_uploaded_as_a_jpeg(bus, monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    out = x_post.post(bus, "hi", JPEG, kind="marketing", now=NOW, config=_cfg())
    assert out["ok"]
    up = s.calls[0][1]
    assert up["files"]["media"] == ("card.jpg", JPEG, "image/jpeg")
    assert up["data"]["media_type"] == "image/jpeg"


def test_a_png_is_uploaded_as_a_png(bus, monkeypatch):
    s = FakeSession()
    monkeypatch.setattr(x_post, "_session", lambda c: s)
    x_post.post(bus, "hi", PNG, kind="marketing", now=NOW, config=_cfg())
    assert s.calls[0][1]["data"]["media_type"] == "image/png"


@pytest.mark.parametrize("dry", [False, True])
def test_unknown_image_bytes_are_refused_before_any_network_call(bus, monkeypatch, dry):
    monkeypatch.setattr(x_post, "_session", lambda c: pytest.fail("no network"))
    out = x_post.post(bus, "hi", b"GIF89a-not-allowed", kind="marketing", now=NOW,
                      config=_cfg(dry_run=dry))
    assert not out["ok"] and out["error"] == "unsupported image type"
    e = _entry0(bus)
    assert e["status"] == "refused" and e["reason"] == "unsupported image type"
    assert x_post.posted_today(bus, NOW) == 0
