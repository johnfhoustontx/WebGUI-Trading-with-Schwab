"""X links a trade's and a report's OWN page (trade_pages.py, report_pages.py)
rather than ideas.html / report.html, which change under a post's feet.

The site is written BEFORE X posts, so the page exists when the link goes out.
Without a page (the site switch off, a write that failed, a replaced report)
the post links what it linked before.
"""
import datetime as dt
import io
import pathlib
import shutil
import time
from zoneinfo import ZoneInfo

import pytest

import repo_paths
from services.options_svc import handlers, push_notify, site_ideas
from services.options_svc import trade_idea as T
from services.options_svc.tests.test_trade_idea import _Bus, _scan, pcs
from shared.bus import Bus
from shared.bus.client import reset_fake_bus
from shared.contracts.envelope import Command
from shared.notify import switches, x_post

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 17, 10, 35, tzinfo=_CT)
_SITE = pathlib.Path(repo_paths.SITE_ROOT)


def _card_png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1260), (22, 24, 38)).save(buf, "PNG")
    return buf.getvalue()


def _site():
    root = pathlib.Path(site_ideas.SITE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("ideas.html", "report.html"):
        shutil.copy(_SITE / name, root / name)
    return root


# ── trade ideas ─────────────────────────────────────────────────────────────

def test_x_text_links_the_trade_page():
    idea = T.normalize(pcs())
    page = "https://neuralstrike.co/t/2026-09-17-1035-SPY/"
    out = T.x_text(idea, {"link": "https://neuralstrike.co"}, today=dt.date(2026, 9, 17),
                   link=page)
    assert page in out
    plain = T.x_text(idea, {"link": "https://neuralstrike.co"}, today=dt.date(2026, 9, 17))
    assert "https://neuralstrike.co" in plain and "/t/" not in plain


@pytest.fixture
def posting(monkeypatch):
    got = {}
    monkeypatch.setattr(push_notify, "trade_idea_config", lambda config=None: {"enabled": True})
    monkeypatch.setattr(push_notify, "send_trade_idea", lambda idea, **kw: True)
    monkeypatch.setattr(push_notify, "trade_idea_png", lambda idea, **kw: _card_png())
    monkeypatch.setattr(push_notify, "load_config",
                        lambda: {"x": {"link": "https://neuralstrike.co"}})
    monkeypatch.setattr(handlers.x_post, "post",
                        lambda bus, text, png=None, **kw: got.update(text=text) or {"ok": True})
    return got


def test_trade_idea_posts_x_with_the_page(posting, monkeypatch):
    _site()
    monkeypatch.setattr(switches, "site_settings",
                        lambda: {"trade_ideas": True, "keep_days": 6, "refresh_min": 15})
    res = handlers.run_trade_idea(_Bus({"cache:options:scan": _scan()}), "h1035", now=NOW)
    assert res["status"] == "posted"
    assert res["site"] is True
    assert res["page"].startswith(f"https://{repo_paths.SITE_HOST}/t/2026-09-17-1035-")
    assert res["page"] in posting["text"]


def test_site_switch_off_links_home(posting, monkeypatch):
    root = _site()
    monkeypatch.setattr(switches, "site_settings",
                        lambda: {"trade_ideas": False, "keep_days": 6, "refresh_min": 15})
    res = handlers.run_trade_idea(_Bus({"cache:options:scan": _scan()}), "h1035", now=NOW)
    assert res["site"] is False and res["page"] is None
    assert "https://neuralstrike.co" in posting["text"] and "/t/" not in posting["text"]
    assert not (root / "t").exists()


# ── market reports ──────────────────────────────────────────────────────────

REPORT = {"headline": "Buyers defend the flip; breadth thin",
          "highlights": ["SPX holds 6,610 gamma flip"],
          "slot": "midday", "slot_label": "Midday Report", "report_date": "2026-09-22",
          "as_of": "11:30 CT", "report_url": "https://neuralstrike.co/report.html"}


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
    return {"bus": Bus(fake=True), "texts": texts, "root": root}


def _cmd():
    return Command(type="x_post_report", args={"report": REPORT, "mtime": time.time()})


def test_report_posts_x_with_its_page(report_env):
    handlers.handle_command(report_env["bus"], _cmd())
    page = f"https://{repo_paths.SITE_HOST}/r/2026-09-22-4-midday/"
    assert report_env["texts"] and page in report_env["texts"][0]
    assert (report_env["root"] / "r" / "2026-09-22-4-midday" / "index.html").is_file()


def test_a_replaced_report_links_report_html(report_env):
    (report_env["root"] / "reports" / "latest.txt").write_text(
        "2026-09-22 5 close 15:10CT\n", encoding="utf-8")
    handlers.handle_command(report_env["bus"], _cmd())
    assert "https://neuralstrike.co/report.html" in report_env["texts"][0]
    assert not (report_env["root"] / "r").exists()
