"""The runnable service: what it is wired to, and that importing it does nothing.

Under pytest the scheduler is suppressed (``_scaffold._schedulers_enabled``),
so the app here is its ``/health`` route and its ONE command stream on the fake
bus.

⚠ No ``monkeypatch.undo()`` in this suite: the store and site fixtures and the
repo-root SQLite guard share that fixture. A patch that must end early uses
``monkeypatch.context()``.
"""
import importlib
import pathlib
import time

from fastapi.testclient import TestClient

import repo_paths
from services import _scaffold
from services.blog_svc import handlers, scheduler, sitewriter
from shared import blog_inbox
from shared.bus import Bus


def test_health_answers_for_the_blog():
    from services.blog_svc.app import app
    with TestClient(app) as client:
        answer = client.get("/health")
        assert answer.status_code == 200
        body = answer.json()
        assert body["domain"] == "blog" and body["up"] is True


def test_the_app_is_wired_to_the_blog_scheduler_and_handler_and_one_stream(monkeypatch):
    from services.blog_svc import app as app_mod
    seen = {}

    def fake_make_app(domain, **kw):
        seen["domain"] = domain
        seen.update(kw)
        return "sentinel"

    try:
        with monkeypatch.context() as patch:
            patch.setattr(_scaffold, "make_app", fake_make_app)
            importlib.reload(app_mod)
            assert app_mod.app == "sentinel"
    finally:
        importlib.reload(app_mod)

    assert seen["domain"] == "blog" == blog_inbox.OWNER_DOMAIN
    assert seen["scheduler"] is scheduler.loop
    assert seen["command_handler"] is handlers.handle_command
    assert seen["on_dropped"] is handlers.on_dropped
    # The connector's stream is not built: no second consumer, and nothing
    # runs at any age or on a slow lane.
    assert not seen.get("extra_consumers")
    assert set(seen) == {"domain", "scheduler", "command_handler", "on_dropped"}


def test_health_counts_dead_letters_on_cmd_blog_only(monkeypatch):
    from services.blog_svc.app import app
    asked = []
    monkeypatch.setattr(_scaffold, "_dead_letter_total",
                        lambda bus, streams: asked.append(list(streams)) or 0)
    with TestClient(app) as client:
        assert client.get("/health").json()["dead_letters"] == 0
    assert asked == [["cmd:blog"]]
    assert blog_inbox.INBOX_STREAM not in asked[0]


def test_importing_the_app_opens_no_store_and_writes_no_site():
    from services.blog_svc import app as app_mod
    importlib.reload(app_mod)
    assert not pathlib.Path(repo_paths.BLOG_DATA).exists()
    site = sitewriter.SITE_ROOT
    assert sorted(path.name for path in site.iterdir()) == ["blog.html"]


def test_a_health_read_opens_no_store_and_writes_no_site():
    from services.blog_svc.app import app
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert not pathlib.Path(repo_paths.BLOG_DATA).exists()
    assert sorted(path.name for path in sitewriter.SITE_ROOT.iterdir()) == ["blog.html"]


def test_a_command_on_the_stream_reaches_the_handler_and_is_answered():
    """The whole path once: the stream the private page writes, the scaffold's
    consumer, the handler, the answer the page reads."""
    from services.blog_svc.app import app
    rid = blog_inbox.new_id()
    command = blog_inbox.owner_command("discard", rid, draft_id="a" * 16)
    assert command is not None
    bus = Bus()
    with TestClient(app):
        bus.enqueue_command(f"cmd:{blog_inbox.OWNER_DOMAIN}", command)
        deadline = time.monotonic() + 15
        answer = None
        while answer is None and time.monotonic() < deadline:
            env = bus.cache_get(handlers.CACHE_RESULT)
            answer = env.payload if env is not None else None
            if answer is None:
                time.sleep(0.05)
    assert answer == {"request_id": rid, "command": "discard", "ok": False,
                      "message": handlers.MESSAGES["no_draft"], "draft_id": "a" * 16,
                      "slug": ""}
    assert bus.dead_letter_len("cmd:blog") == 0


def test_the_service_port_is_configured_and_the_bind_is_loopback():
    assert repo_paths.SERVICE_PORTS["blog"] == 8217
    source = (pathlib.Path(repo_paths.REPO_ROOT) / "services" / "blog_svc" / "app.py").read_text(
        encoding="utf-8")
    assert 'host="127.0.0.1"' in source and "0.0.0.0" not in source
    assert 'SERVICE_PORTS["blog"]' in source
