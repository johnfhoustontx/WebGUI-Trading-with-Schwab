"""The trade ideas and market reports reach Facebook and Instagram (handlers.py).

Meta runs after X and needs the post's own page: Facebook links it, and
Instagram's image is the page's ``card.jpg``. Each network is posted at most once
per idea or report; a network that failed is tried again when the same report
comes back, the one that posted is not.
"""
import time

import pytest

import repo_paths
from services.options_svc import handlers, push_notify
from services.options_svc.tests.test_trade_idea import _Bus, _scan
from services.options_svc.tests.test_x_report_pages import (NOW, REPORT, _card_png,
                                                            _site)
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from shared.notify import meta_post, switches, x_post

HOST = repo_paths.SITE_HOST


class _Meta:
    def __init__(self, results=None, boom=None):
        self.calls, self.results, self.boom = [], list(results or []), boom

    def __call__(self, bus, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        res = self.results.pop(0) if self.results else {}
        nets = kw.get("networks", meta_post.NETWORKS)
        out = {"dry_run": False}
        for n in meta_post.NETWORKS:
            out[n] = {"status": res.get(n, "posted") if n in nets else "skipped",
                      "id": None, "reason": None}
        return out


@pytest.fixture
def idea_env(monkeypatch):
    reset_fake_bus()
    _site()
    calls = {"x": [], "cards": 0, "ledger": 0}
    monkeypatch.setattr(push_notify, "trade_idea_config", lambda config=None: {"enabled": True})
    monkeypatch.setattr(push_notify, "send_trade_idea", lambda idea, **kw: True)
    monkeypatch.setattr(push_notify, "trade_idea_png", lambda idea, **kw: _card_png())
    monkeypatch.setattr(push_notify, "load_config",
                        lambda: {"x": {"link": "https://neuralstrike.co"}})
    monkeypatch.setattr(switches, "site_settings",
                        lambda: {"trade_ideas": True, "keep_days": 6, "refresh_min": 15})
    monkeypatch.setattr(handlers.x_post, "post",
                        lambda bus, text, png=None, **kw: calls["x"].append(text)
                        or {"ok": True})

    def cards(bus):
        calls["cards"] += 1

    def ledger(idea, now):
        calls["ledger"] += 1
        return {"status": "booked"}

    monkeypatch.setattr(handlers, "publish_idea_cards", cards)
    monkeypatch.setattr(handlers, "_book_trade_idea_ledger", ledger)
    meta = _Meta()
    monkeypatch.setattr(meta_post, "post", meta)
    return {"calls": calls, "meta": meta, "monkeypatch": monkeypatch,
            "bus": _Bus({"cache:options:scan": _scan()})}


def _run(env):
    return handlers.run_trade_idea(env["bus"], "h1035", now=NOW)


def test_trade_idea_posts_to_meta_with_its_page(idea_env):
    res = _run(idea_env)
    kw, = idea_env["meta"].calls
    assert kw["link"] == res["page"]
    assert kw["link"].startswith(f"https://{HOST}/t/2026-09-17-1035-")
    assert kw["image_url"] == kw["link"] + "card.jpg"
    assert f"{HOST}/t/" in kw["ig_text"] and "https://" not in kw["ig_text"]
    assert kw["kind"] == "trade_idea"
    assert kw["networks"] == ("facebook", "instagram")
    assert res["meta"]["instagram"]["status"] == "posted"


def test_trade_idea_meta_runs_after_x_and_x_failure_does_not_stop_it(idea_env):
    def boom(*a, **k):
        raise RuntimeError("x down")

    idea_env["monkeypatch"].setattr(handlers.x_post, "post", boom)
    res = _run(idea_env)
    assert res["x"]["ok"] is False
    assert len(idea_env["meta"].calls) == 1


def test_meta_failure_does_not_touch_the_ledger_or_cards(idea_env):
    idea_env["monkeypatch"].setattr(meta_post, "post", _Meta(boom=RuntimeError("meta")))
    res = _run(idea_env)
    assert res["status"] == "posted"
    assert idea_env["calls"]["ledger"] == 1 and idea_env["calls"]["cards"] == 1
    assert res["ledger"] == {"status": "booked"}


def test_the_same_idea_is_not_posted_twice(idea_env):
    res = _run(idea_env)
    handlers._post_trade_idea_meta(idea_env["bus"], res["idea"], res["page"], NOW)
    assert len(idea_env["meta"].calls) == 1
    seen = idea_env["bus"].cache_get(handlers.META_IDEAS_KEY).payload
    pid = res["page"].rstrip("/").rsplit("/", 1)[-1]
    assert seen == {"facebook": [pid], "instagram": [pid]}


def test_a_failed_network_is_tried_again(idea_env):
    idea_env["monkeypatch"].setattr(
        meta_post, "post", meta := _Meta(results=[{"facebook": "failed"}]))
    res = _run(idea_env)
    handlers._post_trade_idea_meta(idea_env["bus"], res["idea"], res["page"], NOW)
    assert [kw["networks"] for kw in meta.calls] == [("facebook", "instagram"),
                                                     ("facebook",)]


def test_no_page_no_meta_post(idea_env):
    idea_env["monkeypatch"].setattr(
        switches, "site_settings",
        lambda: {"trade_ideas": False, "keep_days": 6, "refresh_min": 15})
    res = _run(idea_env)
    assert idea_env["meta"].calls == []
    assert res["meta"] == {"skipped": "no page"}


# ── market reports ──────────────────────────────────────────────────────────

@pytest.fixture
def report_env(monkeypatch, tmp_path):
    reset_fake_bus()
    monkeypatch.setattr(x_post, "X_POSTS_LOG", tmp_path / "x_posts.jsonl")
    cfg = {"x": {"enabled": True, "dry_run": False, "daily_cap": 15, "max_tags": 4,
                 "link": "https://neuralstrike.co", "report_max_age_min": 45,
                 "kinds": {"report": {"enabled": True}}, "hashtags": {"report": []}}}
    monkeypatch.setattr(push_notify, "load_config", lambda: cfg)
    texts = []
    monkeypatch.setattr(x_post, "post",
                        lambda bus, text, png=None, **kw: texts.append(text)
                        or {"ok": True, "id": "1"})
    root = _site()
    reports = root / "reports"
    reports.mkdir()
    (reports / "latest.html").write_text("<html><head></head><body><h1>x</h1></body></html>",
                                         encoding="utf-8")
    (reports / "latest.txt").write_text("2026-09-22 4 midday 11:30CT\n", encoding="utf-8")
    meta = _Meta(results=[{"facebook": "failed"}])
    monkeypatch.setattr(meta_post, "post", meta)
    return {"bus": Bus(fake=True), "texts": texts, "meta": meta}


def _report(bus, mtime=None):
    handlers.handle_command(bus, Command(
        type="x_post_report",
        args={"report": REPORT, "mtime": time.time() if mtime is None else mtime}))


def test_report_posts_to_meta_once_per_network(report_env):
    _report(report_env["bus"])
    _report(report_env["bus"])
    first, second = report_env["meta"].calls
    page = f"https://{HOST}/r/2026-09-22-4-midday/"
    assert first["link"] == page and first["image_url"] == page + "card.jpg"
    assert first["kind"] == "report"
    assert first["fb_text"].startswith("Midday Report: Buyers defend the flip")
    assert f"Full report: {HOST}/r/2026-09-22-4-midday/" in first["ig_text"]
    assert first["networks"] == ("facebook", "instagram")
    assert second["networks"] == ("facebook",)
    assert len(report_env["texts"]) == 1                      # X posted once
    _report(report_env["bus"])
    assert len(report_env["meta"].calls) == 2                 # both now done


def test_stale_report_not_posted_to_meta(report_env):
    _report(report_env["bus"], mtime=time.time() - 46 * 60)
    _report(report_env["bus"])                                # fresh mtime later
    assert report_env["meta"].calls == []
    assert report_env["texts"] == []


def test_a_report_without_a_page_skips_meta(report_env):
    (_site() / "reports" / "latest.txt").write_text("2026-09-22 5 close 15:10CT\n",
                                                    encoding="utf-8")
    _report(report_env["bus"])
    assert report_env["meta"].calls == []
    assert len(report_env["texts"]) == 1


def test_conftest_keeps_the_meta_jsonl_out_of_the_checkout():
    assert meta_post.META_POSTS_LOG != repo_paths.META_POSTS_LOG
