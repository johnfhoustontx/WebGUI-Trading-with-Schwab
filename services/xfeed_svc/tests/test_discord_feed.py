"""The X feed to Discord (``discord_feed``): which posts go, once each, and how a send copes.

No network: every ``Sender`` here gets a fake ``post`` and ``webhook``, and the
real webhook resolver is shown to return nothing under pytest.
"""
import tomllib

import pytest

from repo_paths import REPO_ROOT
from services import _degrade
from services.xfeed_svc import config, discord_feed as df
from services.xfeed_svc.store import Store

HOOK = "https://discord.com/api/webhooks/1/secret-token"
MIN = 60_000


def post(sid, *, handle="reuters", origin="live", type="post", created=1, url=None):
    return {"kind": "post", "account_handle": handle, "status_id": sid, "origin": origin, "type": type,
            "created_at": created, "author_handle": handle,
            "url": url if url is not None else f"https://x.com/{handle}/status/{sid}"}


def account(handle="reuters", group="News"):
    return {"handle": handle, "group_name": group}


class Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code = code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class Wire:
    """A fake ``requests.post``: answers from a script, records each call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        a = self.answers.pop(0) if self.answers else Resp(204)
        if isinstance(a, Exception):
            raise a
        return a


def make(store=None, *, wire=None, hook=HOOK, **cfg):
    store = store or Store(":memory:")
    sleeps = []
    discord = {**config.DEFAULTS["discord"], "enabled": True, **cfg}
    s = df.Sender(store, webhook=lambda: hook, post=wire or Wire(), sleep=sleeps.append,
                  now_ms=lambda: 10 * MIN, load_cfg=lambda: {"discord": discord})
    s._ensure_thread = lambda: None      # tests drain the queue themselves
    return s, store, sleeps


def drain(s):
    out = []
    while not s._q.empty():
        out.append(s.deliver(*s._q.get()))
    return out


# -- what is picked ---------------------------------------------------------------

def test_a_live_post_in_a_chosen_group_goes_out_as_its_fxembed_link():
    wire = Wire()
    s, store, sleeps = make(wire=wire)
    assert s.offer(account(), [post("111")]) == 1
    assert drain(s) == [df.SENT]
    (url, kw), = wire.calls
    assert url == HOOK
    assert kw["json"] == {"content": "https://fixupx.com/reuters/status/111", "allowed_mentions": {"parse": []}}
    assert store.discord_rows()[0]["state"] == df.SENT
    assert sleeps == [2.0]                      # send_gap_s after the post


def test_only_live_posts_of_the_named_types_are_picked():
    s, store, _ = make()
    posts = [post("1", origin="catchup"), post("2", origin="baseline"), post("3", type="repost"),
             post("4", type="reply"), post("5"), {"kind": "gap", "id": 1}]
    assert s.offer(account(), posts) == 2
    assert sorted(r["status_id"] for r in store.discord_rows()) == ["4", "5"]


def test_the_group_rule_decides_the_account():
    s, *_ = make()
    assert s.offer(account(group="Macro"), [post("1")]) == 0
    assert s.offer(account(group="news"), [post("2")]) == 1          # any case
    assert s.offer(account(group=None), [post("3")]) == 0             # ungrouped off as shipped
    s2, *_ = make(groups=["*"])
    assert s2.offer(account(group="Macro"), [post("4")]) == 1
    s3, *_ = make(ungrouped=True)
    assert s3.offer(account(group=None), [post("5")]) == 1


def test_off_or_no_webhook_picks_nothing_and_claims_nothing():
    for kwargs in ({"enabled": False}, {"hook": ""}):
        s, store, _ = make(**kwargs)
        assert s.offer(account(), [post("1")]) == 0
        assert store.discord_rows() == []        # so turning it on later sends nothing old


def test_each_status_goes_once_across_polls_restarts_and_two_reposting_accounts():
    store = Store(":memory:")
    s, *_ = make(store)
    assert s.offer(account(), [post("7")]) == 1
    assert s.offer(account(), [post("7")]) == 0
    s2, *_ = make(store)                                              # a restarted service
    assert s2.offer(account("other"), [post("7", handle="other", url="https://x.com/reuters/status/7")]) == 0
    assert len(store.discord_rows()) == 1


def test_oldest_first_and_past_the_per_poll_cap_recorded_as_skipped():
    s, store, _ = make(max_per_poll=2)
    assert s.offer(account(), [post("3", created=3), post("1", created=1), post("2", created=2)]) == 2
    assert [job[0] for job in list(s._q.queue)] == ["1", "2"]
    states = {r["status_id"]: (r["state"], r["detail"]) for r in store.discord_rows()}
    assert states["3"] == (df.SKIPPED, "over the per-poll cap")


def test_a_full_queue_skips_and_says_so():
    s, store, _ = make(queue_max=1)
    s.offer(account(), [post("1", created=1)])
    assert s.offer(account(), [post("2", created=2)]) == 0
    assert {r["status_id"]: r["detail"] for r in store.discord_rows()}["2"] == "the send queue was full"


def test_switched_off_before_its_turn_is_dropped_not_sent():
    wire = Wire()
    s, store, _ = make(wire=wire)
    s.offer(account(), [post("1")])
    s._webhook = lambda: ""
    assert drain(s) == [df.DROPPED]
    assert wire.calls == []


def test_a_fault_while_picking_is_counted_and_never_raised(monkeypatch):
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(area))
    s, store, _ = make()

    def boom(*a, **k):
        raise RuntimeError("db gone")

    store.claim_discord = boom
    assert s.offer(account(), [post("1")]) == 0
    assert seen == [df.AREA]


# -- the link --------------------------------------------------------------------

@pytest.mark.parametrize("p, want", [
    (post("9", url="https://twitter.com/Reuters/status/9"), "https://fixupx.com/Reuters/status/9"),
    (post("9", url="https://mobile.x.com/a_b/status/9"), "https://fixupx.com/a_b/status/9"),
    (post("9", url="u"), "https://fixupx.com/reuters/status/9"),              # rebuilt from author + id
    (post("9", url="https://x.com/reuters/status/9?s=20"), "https://fixupx.com/reuters/status/9"),
    ({**post("9", url=""), "author_handle": "bad handle!"}, None),
    ({**post("9x", url="")}, None),
    (post("9", url="https://evil.example/reuters/status/9"), "https://fixupx.com/reuters/status/9"),
])
def test_link_for(p, want):
    assert df.link_for(p, "fixupx.com") == want


def test_a_bad_link_host_reads_as_the_shipped_one():
    for bad in ("", "fixupx", "https://fixupx.com", "a b.com", 5):
        assert df.settings({"link_host": bad})["link_host"] == "fixupx.com"
    assert df.settings({"link_host": " FxTwitter.com "})["link_host"] == "fxtwitter.com"


# -- one send ----------------------------------------------------------------------

def _send(wire, retries=3):
    sleeps = []
    out = df.send(HOOK, "L", post=wire, sleep=sleeps.append, retries=retries, timeout=10)
    return out, sleeps


def test_a_429_waits_discords_retry_after_then_sends():
    wire = Wire(Resp(429, {"retry_after": 1.5}), Resp(204))
    assert _send(wire) == ((df.SENT, None), [1.5])
    assert len(wire.calls) == 2


def test_a_429_without_a_body_reads_the_header_and_a_huge_wait_is_capped():
    assert _send(Wire(Resp(429, None, {"Retry-After": "3"}), Resp(200)))[1] == [3.0]
    assert _send(Wire(Resp(429, {"retry_after": 9999}), Resp(200)))[1] == [df.MAX_WAIT_S]
    assert _send(Wire(Resp(429, {"retry_after": "nan"}), Resp(200)))[1] == [1.0]


def test_rate_limited_past_the_retries_fails():
    assert _send(Wire(*[Resp(429, {"retry_after": 1})] * 3), retries=2)[0] == (df.FAILED, "HTTP 429")


def test_a_server_error_or_no_answer_backs_off_then_fails():
    out, sleeps = _send(Wire(Resp(502), ConnectionError(HOOK), Resp(503), Resp(500)), retries=3)
    assert out == (df.FAILED, "HTTP 500")
    assert sleeps == [1.0, 2.0, 4.0]


def test_another_4xx_fails_at_once():
    wire = Wire(Resp(404), Resp(204))
    assert _send(wire) == ((df.FAILED, "HTTP 404"), [])
    assert len(wire.calls) == 1


def test_the_webhook_never_reaches_the_record_or_the_log(monkeypatch):
    """A requests exception's text holds the URL, and the URL holds the token."""
    seen = []
    monkeypatch.setattr(_degrade, "degraded", lambda area, **k: seen.append(k.get("detail")))
    s, store, _ = make(wire=Wire(*[ConnectionError(f"Max retries exceeded with url: {HOOK}")] * 9))
    s.offer(account(), [post("1")])
    assert drain(s) == [df.FAILED]
    row = store.discord_rows()[0]
    assert row["detail"] == "no answer (ConnectionError)"
    assert seen and not any("secret-token" in (d or "") for d in seen)


# -- the config ----------------------------------------------------------------------

def test_bad_values_read_as_the_shipped_ones_key_by_key():
    d = config.DEFAULTS["discord"]
    got = df.settings({"max_per_poll": 0, "send_gap_s": float("inf"), "max_retries": True,
                       "timeout_s": 2.5, "queue_max": "9", "types": ["post", "bogus", "REPOST"],
                       "groups": "News", "enabled": "yes"})
    assert got["max_per_poll"] == d["max_per_poll"]
    assert got["send_gap_s"] == d["send_gap_s"]
    assert got["max_retries"] == d["max_retries"]
    assert got["timeout_s"] == d["timeout_s"]
    assert got["queue_max"] == d["queue_max"]
    assert got["types"] == ["post", "repost"]
    assert got["rule"] == {"enabled": False, "groups": ["News"], "ungrouped": False}
    assert df.settings(None)["rule"]["enabled"] is False


def test_the_shipped_file_matches_the_defaults_and_ships_off():
    shipped = tomllib.loads((REPO_ROOT / "config" / "xfeed.toml").read_text(encoding="utf-8"))["discord"]
    assert shipped == config.DEFAULTS["discord"]
    assert shipped["enabled"] is False


def test_the_real_webhook_resolver_finds_nothing_under_pytest():
    """Outside prod the notify master switch is zeroed, so even a configured
    route resolves to no webhook: the suite can never post to Discord."""
    assert df._webhook() == ""


# -- the store ------------------------------------------------------------------------

def test_prune_drops_only_old_records():
    store = Store(":memory:")
    store.claim_discord([("a", "1")], now=1)
    store.claim_discord([("a", "2")], now=100)
    assert store.prune_discord(before=50) == 1
    assert [r["status_id"] for r in store.discord_rows()] == ["2"]
    assert store.claim_discord([("a", "1")], now=200) == {"1"}     # pruned: claimable again
