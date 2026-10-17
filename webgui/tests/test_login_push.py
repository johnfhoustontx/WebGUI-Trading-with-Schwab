"""Approving a sign-in from the phone: the pending store and the ntfy push.

The routes that use these are driven in ``test_login_push_routes.py``. The
design is docs/plans/2026-10-17-login-push-approval-design.md.
"""
import json

import pytest

import config_schema
import login_push

TOPIC = "t" * 32
APP = "https://app.example.test"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    login_push.reset_store()
    login_push.reset_cache()
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    yield
    login_push.reset_store()


def _new(store=None, now=0.0, **kw):
    store = store or login_push.STORE
    args = {"client": "203.0.113.9", "keep": False, "next_path": "/desk"}
    args.update(kw)
    return store.create(now=now, **args)


# ── the pending store ────────────────────────────────────────────────────────
def test_create_offers_three_distinct_two_digit_numbers_including_the_right_one():
    for _ in range(200):
        p = _new()
        assert len(set(p.offered)) == 3
        assert p.number in p.offered
        assert all(10 <= n <= 99 for n in p.offered)
        assert p.pending_id != p.approval_id
        assert len(p.pending_id) >= 40 and len(p.approval_id) >= 40
        assert p.state == "waiting"
        login_push.reset_store()


def test_answer_right_number_approves_and_collect_consumes():
    p = _new()
    assert login_push.STORE.answer(p.approval_id, p.number, True, confirm_id=p.confirm_id, now=1) == "approved"
    got = login_push.STORE.collect(p.pending_id, now=2)
    assert got is not None and got.pending_id == p.pending_id
    assert login_push.STORE.collect(p.pending_id, now=3) is None


def test_collect_returns_nothing_while_waiting():
    p = _new()
    assert login_push.STORE.collect(p.pending_id, now=1) is None
    assert login_push.STORE.by_pending(p.pending_id, now=1).state == "waiting"


def test_answer_wrong_number_or_deny_refuses():
    p = _new()
    wrong = next(n for n in p.offered if n != p.number)
    assert login_push.STORE.answer(p.approval_id, wrong, True, confirm_id=p.confirm_id, now=1) == "refused"
    assert login_push.STORE.collect(p.pending_id, now=1) is None
    q = _new()
    assert login_push.STORE.answer(q.approval_id, q.number, False, confirm_id=q.confirm_id, now=1) == "refused"
    assert login_push.STORE.collect(q.pending_id, now=1) is None


def test_a_refused_approval_reads_as_absent_to_the_waiting_browser():
    p = _new()
    login_push.STORE.answer(p.approval_id, p.number, False, confirm_id=p.confirm_id, now=1)
    assert login_push.STORE.by_pending(p.pending_id, now=1) is None


def test_a_second_answer_has_ended():
    p = _new()
    login_push.STORE.answer(p.approval_id, p.number, True, confirm_id=p.confirm_id, now=1)
    assert login_push.STORE.answer(p.approval_id, p.number, True, confirm_id=p.confirm_id, now=2) == "ended"
    q = _new()
    login_push.STORE.answer(q.approval_id, q.number, False, confirm_id=q.confirm_id, now=1)
    assert login_push.STORE.answer(q.approval_id, q.number, True, confirm_id=q.confirm_id, now=2) == "ended"


def test_answer_needs_the_confirm_id_the_approve_page_showed():
    """The approve page's own value: a forged POST from another page on the
    same site cannot know it, so it settles nothing."""
    p = _new()
    assert p.confirm_id not in (p.approval_id, p.pending_id)
    assert login_push.STORE.answer(p.approval_id, p.number, True,
                                   confirm_id="forged", now=1) == "ended"
    assert login_push.STORE.answer(p.approval_id, p.number, True,
                                   confirm_id=None, now=1) == "ended"
    assert login_push.STORE.by_pending(p.pending_id, now=1).state == "waiting"


def test_an_unknown_approval_id_has_ended():
    assert login_push.STORE.answer("nope", 42, True, confirm_id="x") == "ended"
    assert login_push.STORE.by_approval("nope") is None


def test_expired_approval_reads_as_absent():
    p = _new(now=0)
    assert login_push.STORE.by_pending(p.pending_id, now=121) is None
    assert login_push.STORE.answer(p.approval_id, p.number, True, confirm_id=p.confirm_id, now=121) == "ended"


def test_an_approved_but_uncollected_approval_also_expires():
    p = _new(now=0)
    login_push.STORE.answer(p.approval_id, p.number, True, confirm_id=p.confirm_id, now=1)
    assert login_push.STORE.collect(p.pending_id, now=121) is None


def test_has_room_respects_max_pending(monkeypatch):
    monkeypatch.setattr(login_push, "max_pending", lambda: 2)
    _new(now=0)
    _new(now=50)
    assert login_push.STORE.has_room(now=60) is False
    assert login_push.STORE.has_room(now=121) is True     # the first expired


def test_drop_and_refuse():
    p = _new()
    login_push.STORE.drop(p.pending_id)
    assert login_push.STORE.by_pending(p.pending_id) is None
    q = _new()
    login_push.STORE.refuse(q.pending_id)
    assert login_push.STORE.answer(q.approval_id, q.number, True, confirm_id=q.confirm_id) == "ended"


# ── configuration ────────────────────────────────────────────────────────────
def test_configured_needs_the_topic_and_the_switch(monkeypatch):
    assert login_push.configured() is False                 # no topic
    monkeypatch.setenv("NTFY_TOPIC", "   ")
    assert login_push.configured() is False                 # blank topic
    monkeypatch.setenv("NTFY_TOPIC", f"  {TOPIC} ")
    assert login_push.topic() == TOPIC
    assert login_push.configured() is True
    monkeypatch.setattr(login_push, "enabled", lambda: False)
    assert login_push.configured() is False


@pytest.mark.parametrize("key, junk, accessor", [
    ("enabled", "yes", "enabled"),
    ("server", "http://plain.example", "server"),
    ("server", 7, "server"),
    ("ttl_sec", 0, "ttl_sec"),
    ("ttl_sec", 99999, "ttl_sec"),
    ("ttl_sec", True, "ttl_sec"),
    ("max_pending", 0, "max_pending"),
    ("max_pending", "5", "max_pending"),
    ("publish_timeout_sec", float("nan"), "publish_timeout_sec"),
    ("publish_timeout_sec", 0, "publish_timeout_sec"),
    ("priority", 9, "priority"),
    ("priority", 0, "priority"),
])
def test_bad_config_values_fall_back(monkeypatch, key, junk, accessor):
    cfg = dict(login_push.DEFAULTS)
    cfg[key] = junk
    monkeypatch.setattr(login_push, "load", lambda: cfg)
    assert getattr(login_push, accessor)() == login_push.DEFAULTS[key]


def test_good_config_values_are_read(monkeypatch):
    cfg = dict(login_push.DEFAULTS, enabled=False, server="https://ntfy.example",
               ttl_sec=60, max_pending=3, publish_timeout_sec=2.5, priority=5)
    monkeypatch.setattr(login_push, "load", lambda: cfg)
    assert login_push.enabled() is False
    assert login_push.server() == "https://ntfy.example"
    assert (login_push.ttl_sec(), login_push.max_pending()) == (60, 3)
    assert (login_push.publish_timeout_sec(), login_push.priority()) == (2.5, 5)


def test_the_shipped_defaults():
    assert login_push.DEFAULTS == {
        "enabled": True, "server": "https://ntfy.sh", "ttl_sec": 120,
        "max_pending": 5, "publish_timeout_sec": 5, "priority": 4}


def test_every_key_is_in_the_settings_catalogue():
    entry = config_schema.BY_NAME["login_push.toml"]
    keys = {f.key for s in entry.sections for f in s.fields}
    assert keys == set(login_push.DEFAULTS)


# ── the push ─────────────────────────────────────────────────────────────────
def test_message_has_three_view_actions_and_no_pending_id():
    p = _new()
    msg = login_push.build_message(p, topic=TOPIC, app_url=APP)
    assert msg["topic"] == TOPIC
    assert msg["title"] == "Sign in to NeuralStrike?"
    assert msg["priority"] == 4
    assert p.client in msg["message"]
    actions = msg["actions"]
    assert len(actions) == 3
    prefix = f"{APP}/approve-sign-in?a={p.approval_id}&n="
    picked = set()
    for a in actions:
        assert a["action"] == "view" and a["clear"] is True
        assert a["url"].startswith(prefix)
        n = int(a["url"][len(prefix):])
        assert a["label"] == str(n)
        picked.add(n)
    assert picked == set(p.offered)
    assert p.pending_id not in json.dumps(msg)
    assert p.confirm_id not in json.dumps(msg)
    # A correct password is what triggers it: the push says so.
    assert "password was just used" in msg["message"]


class _Resp:
    def __init__(self, status):
        self.status_code = status


def test_publish_posts_json_with_the_token_and_timeout(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Resp(200)

    p = _new()
    assert login_push.publish(p, app_url=APP, post=post) is True
    assert seen["url"] == login_push.server()
    assert seen["json"]["topic"] == TOPIC
    assert seen["timeout"] == login_push.publish_timeout_sec()
    assert "Authorization" not in (seen["headers"] or {})

    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    login_push.publish(p, app_url=APP, post=post)
    assert seen["headers"]["Authorization"] == "Bearer tk_secret"


@pytest.mark.parametrize("post", [
    lambda *a, **k: _Resp(500),
    lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
])
def test_publish_never_raises(monkeypatch, post):
    monkeypatch.setenv("NTFY_TOPIC", TOPIC)
    assert login_push.publish(_new(), app_url=APP, post=post) is False


def test_publish_without_a_topic_sends_nothing():
    sent = []
    assert login_push.publish(_new(), app_url=APP,
                              post=lambda *a, **k: sent.append(1)) is False
    assert sent == []
