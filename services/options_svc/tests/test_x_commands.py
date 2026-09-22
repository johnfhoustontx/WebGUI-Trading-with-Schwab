"""The two X commands: ``x_post_report`` (a published market report, once) and
``x_post`` (an ad-hoc marketing post from the /x page).

``x_post.post`` is stubbed except in the one end-to-end test, which runs the real
poster in dry-run with the real report card. The notifications config is always
supplied by the test, and the jsonl log is redirected to ``tmp_path``.
"""
import base64
import datetime as _dt
import time

import pytest

from services.options_svc import handlers, push_notify
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from shared.notify import x_post

REPORT = {"headline": "Buyers defend the flip; breadth thin",
          "highlights": ["SPX holds 6,610 gamma flip", "Semis lead, staples lag"],
          "slot": "midday", "slot_label": "Midday Report", "report_date": "2026-09-22",
          "as_of": "11:30 CT", "report_url": "https://neuralstrike.co/report.html"}


def _cfg(**over):
    x = {"enabled": True, "dry_run": False, "daily_cap": 15, "max_tags": 4,
         "link": "https://neuralstrike.co", "report_max_age_min": 45,
         "kinds": {k: {"enabled": True} for k in ("report", "trade_idea", "marketing")},
         "hashtags": {"report": ["#markets"], "trade_idea": [], "marketing": []}}
    x.update(over)
    return {"x": x}


class _Poster:
    def __init__(self, result=None):
        self.result = result if result is not None else {"ok": True, "id": "1"}
        self.calls = []

    def __call__(self, bus, text, png=None, *, kind, now=None, config=None, meta=None):
        self.calls.append({"text": text, "png": png, "kind": kind, "meta": meta})
        return dict(self.result)


@pytest.fixture
def env(monkeypatch, tmp_path):
    reset_fake_bus()
    monkeypatch.setattr(x_post, "X_POSTS_LOG", tmp_path / "x_posts.jsonl")
    cfg = _cfg()
    monkeypatch.setattr(push_notify, "load_config", lambda: cfg)
    poster = _Poster()
    monkeypatch.setattr(x_post, "post", poster)
    return {"bus": Bus(fake=True), "cfg": cfg, "poster": poster}


def _report_cmd(mtime=None, report=REPORT):
    return Command(type="x_post_report",
                   args={"report": report, "mtime": time.time() if mtime is None else mtime})


def _log(bus):
    env = bus.cache_get(x_post.LOG_KEY)
    return (env.payload or {}).get("posts") if env is not None else []


def test_a_report_posts_once_per_identity(env):
    cmd = _report_cmd()
    handlers.handle_command(env["bus"], cmd)
    handlers.handle_command(env["bus"], cmd)
    calls = env["poster"].calls
    assert len(calls) == 1
    assert calls[0]["kind"] == "report"
    assert REPORT["headline"] in calls[0]["text"]
    assert "$SPY" in calls[0]["text"]
    assert "https://neuralstrike.co/report.html" in calls[0]["text"]


def test_a_failed_report_post_is_retried_next_time(env):
    env["poster"].result = {"ok": False, "error": "create post HTTP 503"}
    handlers.handle_command(env["bus"], _report_cmd())
    handlers.handle_command(env["bus"], _report_cmd())
    assert len(env["poster"].calls) == 2


def test_an_unconfirmed_report_post_is_never_reposted(env):
    env["poster"].result = {"ok": False, "unknown": True, "error": "sent; X did not confirm"}
    handlers.handle_command(env["bus"], _report_cmd())
    handlers.handle_command(env["bus"], _report_cmd())
    assert len(env["poster"].calls) == 1


def test_a_dry_run_report_counts_as_done(env):
    env["poster"].result = {"ok": True, "dry_run": True}
    handlers.handle_command(env["bus"], _report_cmd())
    handlers.handle_command(env["bus"], _report_cmd())
    assert len(env["poster"].calls) == 1


def test_a_stale_report_is_not_posted(env):
    old = time.time() - 46 * 60
    handlers.handle_command(env["bus"], _report_cmd(mtime=old))
    assert env["poster"].calls == []
    # remembered: the same report arriving later with a fresh mtime is not posted
    handlers.handle_command(env["bus"], _report_cmd())
    assert env["poster"].calls == []


def test_a_report_of_unknown_age_is_not_posted(env):
    cmd = Command(type="x_post_report", args={"report": REPORT})
    handlers.handle_command(env["bus"], cmd)
    bad = Command(type="x_post_report", args={"report": dict(REPORT, slot="close"),
                                              "mtime": "yesterday"})
    handlers.handle_command(env["bus"], bad)
    assert env["poster"].calls == []


def test_a_malformed_max_age_falls_back_to_45_minutes(env):
    env["cfg"]["x"]["report_max_age_min"] = "soon"
    handlers.handle_command(env["bus"], _report_cmd(mtime=time.time() - 44 * 60))
    assert len(env["poster"].calls) == 1
    handlers.handle_command(env["bus"], _report_cmd(
        mtime=time.time() - 46 * 60, report=dict(REPORT, slot="close")))
    assert len(env["poster"].calls) == 1


def test_a_report_without_a_headline_is_ignored(env):
    for report in (dict(REPORT, headline=""), dict(REPORT, headline=None), None, "x"):
        handlers.handle_command(env["bus"], _report_cmd(report=report))
    assert env["poster"].calls == []


def test_the_memory_of_posted_reports_is_bounded(env):
    for i in range(60):
        handlers.handle_command(env["bus"], _report_cmd(
            report=dict(REPORT, headline=f"Report {i}")))
    assert len(env["poster"].calls) == 60
    posted = env["bus"].cache_get(handlers.X_REPORTS_KEY).payload["posted"]
    assert len(posted) == 50
    assert "Report 59" in posted[0]


def test_ad_hoc_post_decodes_the_image_and_fits_the_text(env):
    args = {"text": "New: the public Gamma page", "tags": ["#options", "gamma"],
            "link": "https://neuralstrike.co/live.html",
            "image_b64": base64.b64encode(b"\x89PNG").decode()}
    handlers.handle_command(env["bus"], Command(type="x_post", args=args))
    [call] = env["poster"].calls
    assert call["png"] == b"\x89PNG"
    assert call["kind"] == "marketing"
    assert call["text"].startswith("New: the public Gamma page")
    assert "https://neuralstrike.co/live.html" in call["text"]
    assert call["text"].endswith("#options #gamma")


def test_ad_hoc_post_without_an_image_or_tags(env):
    handlers.handle_command(env["bus"], Command(
        type="x_post", args={"text": "Hello", "tags": "not-a-list", "link": None}))
    [call] = env["poster"].calls
    assert call["png"] is None
    assert call["text"] == "Hello"


def test_an_oversized_image_is_refused_and_logged(env):
    big = base64.b64encode(b"\0" * (5 * 1024 * 1024 + 1)).decode()
    handlers.handle_command(env["bus"], Command(
        type="x_post", args={"text": "Big", "image_b64": big}))
    assert env["poster"].calls == []
    [entry] = _log(env["bus"])
    assert entry["reason"] == "image over 5 MB"
    assert entry["kind"] == "marketing"
    assert entry["image"] is True


def test_an_undecodable_image_is_refused_and_logged(env):
    handlers.handle_command(env["bus"], Command(
        type="x_post", args={"text": "Bad", "image_b64": "not base64!!"}))
    assert env["poster"].calls == []
    [entry] = _log(env["bus"])
    assert entry["reason"] == "image did not decode"


def test_both_commands_are_replay_guarded():
    assert {"x_post", "x_post_report"} <= set(handlers._REPLAY_GUARDED)


def test_a_stale_x_post_command_is_dropped(env):
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)).isoformat()
    handlers.handle_command(env["bus"], Command(type="x_post", args={"text": "Hi"}, ts=old))
    handlers.handle_command(env["bus"], Command(
        type="x_post_report", args={"report": REPORT, "mtime": time.time()}, ts=old))
    assert env["poster"].calls == []


def test_the_real_poster_logs_a_dry_run_report_with_its_card(monkeypatch, tmp_path):
    reset_fake_bus()
    monkeypatch.setattr(x_post, "X_POSTS_LOG", tmp_path / "x_posts.jsonl")
    cfg = _cfg(dry_run=True)
    monkeypatch.setattr(push_notify, "load_config", lambda: cfg)
    bus = Bus(fake=True)
    handlers.handle_command(bus, _report_cmd())
    [entry] = _log(bus)
    assert entry["status"] == "dry_run"
    assert entry["kind"] == "report"
    assert entry["image"] is True
    assert REPORT["headline"] in entry["text"]
    assert (tmp_path / "x_posts.jsonl").exists()
