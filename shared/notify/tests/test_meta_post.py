"""The one path to Facebook and Instagram (``shared/notify/meta_post.py``).

A fake session stands in for Meta's Graph API: it records each call and answers
from a script keyed on the end of the URL.
"""
import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

import pytest
import requests

from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.notify import meta_post

CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 11, 23, 10, 35, tzinfo=CT)
TOKEN = "SECRET123"
LINK = "https://neuralstrike.co/t/2026-11-23-1035-SPY/"
IMAGE = LINK + "card.jpg"


def _cfg(**over):
    meta = {"enabled": True, "dry_run": False, "page_id": "P1", "ig_user_id": "IG1",
            "access_token": TOKEN}
    meta.update(over)
    return {"meta": meta}


def _settings(**over):
    s = {"graph": {"version": "v26.0", "timeout_sec": 5, "ig_poll_tries": 3,
                   "ig_poll_sec": 0},
         "facebook": {"enabled": True, "daily_cap": 20, "max_tags": 2},
         "instagram": {"enabled": True, "daily_cap": 20, "max_tags": 5,
                       "max_chars": 2200},
         "kinds": {"trade_idea": True, "report": True}}
    for k, v in over.items():
        s[k] = {**s[k], **v}
    return s


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body) if not isinstance(body, Exception) else ""

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    """``script`` maps a URL ending ("/feed", "/media", "/media_publish", "c1") to
    a Resp, an exception to raise, or a list of them consumed in order."""

    def __init__(self, **script):
        self.calls, self.script = [], {
            "/feed": Resp(200, {"id": "P1_77"}),
            "/media": Resp(200, {"id": "c1"}),
            "c1": Resp(200, {"status_code": "FINISHED"}),
            "/media_publish": Resp(200, {"id": "m1"}),
            **script}

    def _answer(self, method, url, kw):
        self.calls.append((method, url, kw))
        for end, out in self.script.items():
            if url.endswith(end):
                if isinstance(out, list):
                    out = out.pop(0) if len(out) > 1 else out[0]
                if isinstance(out, Exception):
                    raise out
                return out
        raise AssertionError(f"unscripted {method} {url}")

    def post(self, url, **kw):
        return self._answer("POST", url, kw)

    def get(self, url, **kw):
        return self._answer("GET", url, kw)

    def close(self):
        pass


@pytest.fixture
def bus(tmp_path, monkeypatch):
    reset_fake_bus()
    monkeypatch.setattr(meta_post, "META_POSTS_LOG", tmp_path / "meta.jsonl")
    return Bus(fake=True)


def _post(bus, s, *, config=None, settings=None, **kw):
    args = dict(kind="trade_idea", fb_text="Trade idea: SPY", ig_text="Trade idea: SPY",
                link=LINK, image_url=IMAGE, now=NOW, config=config or _cfg(),
                settings=settings or _settings(), session=s)
    args.update(kw)
    return meta_post.post(bus, **args)


def _log(bus):
    env = bus.cache_get(meta_post.LOG_KEY)
    return env.payload["posts"] if env is not None else []


def test_disabled_refuses_both(bus):
    s = FakeSession()
    out = _post(bus, s, config={"meta": {"enabled": False}})
    assert out["facebook"]["status"] == out["instagram"]["status"] == "refused"
    assert out["facebook"]["reason"] == "disabled"
    assert s.calls == []


def test_dry_run_calls_nothing(bus):
    s = FakeSession()
    out = _post(bus, s, config=_cfg(dry_run=True))
    assert out["dry_run"] is True
    assert out["facebook"]["status"] == out["instagram"]["status"] == "dry_run"
    assert s.calls == []
    assert [e["network"] for e in _log(bus)] == ["instagram", "facebook"]


def test_dry_run_still_needs_credentials(bus):
    out = _post(bus, FakeSession(), config=_cfg(dry_run=True, access_token=""))
    assert out["facebook"]["status"] == "refused"
    assert out["facebook"]["reason"] == "no credentials"


def test_facebook_link_post(bus):
    s = FakeSession()
    out = _post(bus, s, networks=("facebook",))
    (method, url, kw), = s.calls
    assert method == "POST" and url == "https://graph.facebook.com/v26.0/P1/feed"
    assert kw["data"]["link"] == LINK and kw["data"]["message"] == "Trade idea: SPY"
    assert out["facebook"] == {"status": "posted", "id": "P1_77", "reason": None}
    assert meta_post.posted_today(bus, "facebook", NOW) == 1


def test_instagram_two_steps(bus):
    s = FakeSession()
    out = _post(bus, s, networks=("instagram",))
    assert [(m, u.rsplit("/", 1)[-1]) for m, u, _ in s.calls] == [
        ("POST", "media"), ("GET", "c1"), ("POST", "media_publish")]
    media = s.calls[0][2]["data"]
    assert media["image_url"] == IMAGE and media["caption"] == "Trade idea: SPY"
    assert s.calls[1][2]["params"]["fields"] == "status_code"
    assert s.calls[2][2]["data"]["creation_id"] == "c1"
    assert out["instagram"] == {"status": "posted", "id": "m1", "reason": None}


def test_instagram_waits_for_the_container(bus):
    s = FakeSession(c1=[Resp(200, {"status_code": "IN_PROGRESS"}),
                        Resp(200, {"status_code": "FINISHED"})])
    out = _post(bus, s, networks=("instagram",))
    assert out["instagram"]["status"] == "posted"
    assert [u.rsplit("/", 1)[-1] for _, u, _ in s.calls].count("c1") == 2


def test_instagram_container_never_ready_is_failed_and_publish_not_called(bus):
    s = FakeSession(c1=Resp(200, {"status_code": "IN_PROGRESS"}))
    out = _post(bus, s, networks=("instagram",))
    assert out["instagram"]["status"] == "failed"
    assert "not ready" in out["instagram"]["reason"]
    assert not any(u.endswith("/media_publish") for _, u, _ in s.calls)
    assert meta_post.posted_today(bus, "instagram", NOW) == 0


def test_an_error_container_is_failed(bus):
    s = FakeSession(c1=Resp(200, {"status_code": "ERROR"}))
    out = _post(bus, s, networks=("instagram",))
    assert out["instagram"]["status"] == "failed" and "ERROR" in out["instagram"]["reason"]


def test_publish_timeout_is_unknown_and_counted(bus):
    s = FakeSession(**{"/media_publish": requests.Timeout("slow")})
    out = _post(bus, s, networks=("instagram",))
    assert out["instagram"]["status"] == "unknown"
    assert meta_post.posted_today(bus, "instagram", NOW) == 1


def test_a_feed_5xx_is_unknown_and_a_media_5xx_failed(bus):
    s = FakeSession(**{"/feed": Resp(502, {"error": {"message": "bad gateway"}}),
                       "/media": Resp(503, {"error": {"message": "busy"}})})
    out = _post(bus, s)
    assert out["facebook"]["status"] == "unknown"
    assert out["instagram"]["status"] == "failed"
    assert meta_post.posted_today(bus, "facebook", NOW) == 1
    assert meta_post.posted_today(bus, "instagram", NOW) == 0


def test_one_network_failing_does_not_stop_the_other(bus):
    s = FakeSession(**{"/feed": Resp(400, {"error": {"code": 100, "message": "Bad link"}})})
    out = _post(bus, s)
    assert out["facebook"]["status"] == "failed"
    assert out["facebook"]["reason"] == "400: Bad link"
    assert out["instagram"]["status"] == "posted"


def test_expired_token_says_so(bus):
    s = FakeSession(**{"/feed": Resp(400, {"error": {"code": 190, "message": "expired"}})})
    out = _post(bus, s, networks=("facebook",))
    assert out["facebook"]["reason"] == "token expired or revoked"


def test_daily_cap_per_network(bus):
    s = FakeSession()
    settings = _settings(facebook={"daily_cap": 1})
    assert _post(bus, s, settings=settings, networks=("facebook",))["facebook"]["status"] \
        == "posted"
    out = _post(bus, s, settings=settings)
    assert out["facebook"]["status"] == "refused" and "cap" in out["facebook"]["reason"]
    assert out["instagram"]["status"] == "posted"


def test_a_network_switched_off_is_refused(bus):
    out = _post(bus, FakeSession(), settings=_settings(instagram={"enabled": False}))
    assert out["instagram"] == {"status": "refused", "id": None,
                                "reason": "instagram disabled"}
    assert out["facebook"]["status"] == "posted"


def test_a_kind_switched_off_is_refused(bus):
    out = _post(bus, FakeSession(), settings=_settings(kinds={"report": False}),
                kind="report")
    assert out["facebook"]["reason"] == out["instagram"]["reason"] == "report disabled"


def test_token_never_logged(bus, caplog, tmp_path):
    s = FakeSession(**{"/feed": requests.ConnectionError(f"url ?access_token={TOKEN}"),
                       "/media": Resp(400, {"error": {"code": 1,
                                                      "message": f"bad {TOKEN}"}})})
    with caplog.at_level(logging.DEBUG):
        out = _post(bus, s)
    assert TOKEN not in json.dumps(out)
    assert TOKEN not in json.dumps(_log(bus))
    assert TOKEN not in (tmp_path / "meta.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in caplog.text
    assert out["facebook"]["status"] == "unknown"


def test_unknown_kind_refused(bus):
    s = FakeSession()
    out = _post(bus, s, kind="marketing")
    assert out["facebook"]["status"] == out["instagram"]["status"] == "refused"
    assert s.calls == []


def test_networks_argument_skips(bus):
    s = FakeSession()
    out = _post(bus, s, networks=("instagram",))
    assert out["facebook"] == {"status": "skipped", "id": None, "reason": None}
    assert not any(u.endswith("/feed") for _, u, _ in s.calls)
    assert [e["network"] for e in _log(bus)] == ["instagram"]


def test_no_image_skips_instagram_with_a_reason(bus):
    out = _post(bus, FakeSession(), image_url=None)
    assert out["instagram"]["status"] == "refused"
    assert out["instagram"]["reason"] == "no image"
    assert out["facebook"]["status"] == "posted"


def test_the_log_entry_shape(bus, tmp_path):
    _post(bus, FakeSession(), networks=("facebook",), meta={"symbol": "SPY"})
    entry, = _log(bus)
    assert {k: entry[k] for k in ("at", "kind", "network", "status", "id", "link",
                                  "meta")} == {
        "at": NOW.isoformat(), "kind": "trade_idea", "network": "facebook",
        "status": "posted", "id": "P1_77", "link": LINK, "meta": {"symbol": "SPY"}}
    line = json.loads((tmp_path / "meta.jsonl").read_text(encoding="utf-8"))
    assert line["id"] == "P1_77"


def test_never_raises_on_a_broken_bus():
    class Broken:
        def cache_get(self, k):
            raise RuntimeError("down")

        def cache_set(self, *a, **k):
            raise RuntimeError("down")

    out = meta_post.post(Broken(), kind="trade_idea", fb_text="t", ig_text="t",
                         link=LINK, image_url=IMAGE, now=NOW, config=_cfg(),
                         settings=_settings(), session=FakeSession())
    assert set(out) == {"dry_run", "facebook", "instagram"}
