"""One posted trade idea's own page on the site (``trade_pages.py``).

The page lives at ``/t/<day>-<HHMM>-<SYMBOL>/`` and is never pruned, because a
Facebook or Instagram post links it for as long as the post exists. Its card
and its facts come from the manifest row ``site_ideas`` already keeps; its
result is drawn by ``ideas.js`` from the page's own ``trade.json``.
"""
import datetime as dt
import io
import json
import os
import pathlib
import shutil
from zoneinfo import ZoneInfo

import pytest

import repo_paths
from services.options_svc import site_ideas as S
from services.options_svc import trade_idea as TI
from services.options_svc import trade_pages as P

_CT = ZoneInfo("America/Chicago")
_HOST = "neuralstrike.co"
_NOW = dt.datetime(2026, 11, 23, 9, 30, tzinfo=_CT)


def _png(w=2400, h=1260):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (22, 24, 38)).save(buf, "PNG")
    return buf.getvalue()


def _idea(symbol="SPY", alt="Trade idea: SPY Put credit spread"):
    return {"symbol": symbol, "type": "PCS", "label": "Put credit spread",
            "grade": "Good", "score": 72, "pop_pct": 68.0, "bias": "Bullish",
            "legs": [{"side": "short", "kind": "put", "strike": 590, "qty": 1,
                      "expiration": "2026-11-28"},
                     {"side": "long", "kind": "put", "strike": 585, "qty": 1,
                      "expiration": "2026-11-28"}],
            "expiration": "2026-11-28", "dte": 5, "spot": 601.2,
            "entry_cash": 120.0, "max_loss": 380.0, "max_profit": 120.0,
            "breakevens": [588.8], "em": None, "_alt": alt}


def _row(symbol="SPY", alt="Trade idea: SPY Put credit spread"):
    idea = _idea(symbol, alt)
    row = S.entry(symbol, idea["label"], idea["grade"], alt, _NOW)
    row.update(S.entry_facts(idea, posted=_NOW))
    from services.options_svc import trade_idea_card
    row["card"] = json.loads(json.dumps(trade_idea_card.card_facts(idea)))
    row["shape"] = "wide"
    return row


@pytest.fixture()
def root(tmp_path):
    shutil.copy(pathlib.Path(repo_paths.SITE_ROOT) / "ideas.html", tmp_path / "ideas.html")
    return tmp_path


# ── names ───────────────────────────────────────────────────────────────────

def test_page_id_is_the_day_and_the_stem():
    assert P.page_id({"full": "ideas/2026-11-23/0930-SPY.png"}) == "2026-11-23-0930-SPY"
    assert P.page_id({"full": "../x.png"}) is None
    assert P.page_id({}) is None


def test_page_id_of_an_index_and_a_slash_symbol():
    assert P.page_id(S.entry("$SPX", "", "", "", _NOW)).endswith("-SPX")
    assert P.page_id(S.entry("BRK/B", "", "", "", _NOW)).endswith("-BRK-B")


def test_address_uses_the_site_host():
    assert P.address("2026-11-23-0930-SPY", _HOST) == \
        "https://neuralstrike.co/t/2026-11-23-0930-SPY/"


def test_page_row_points_at_the_pages_own_images():
    row = P.page_row(_row(), "2026-11-23-0930-SPY")
    assert row["img"] == "t/2026-11-23-0930-SPY/card.webp"
    assert row["full"] == row["jpg"] == "t/2026-11-23-0930-SPY/card.jpg"
    assert row["page"] == "t/2026-11-23-0930-SPY/"


# ── the page ────────────────────────────────────────────────────────────────

def _html(root, row=None, height=630):
    row = row or _row()
    pid = P.page_id(row)
    from shared import site_nav
    return pid, P.page_html(site_nav.page_parts(root, "ideas.html"), row, pid, _HOST,
                            image_height=height)


def test_page_html_carries_the_preview_tags(root):
    pid, page = _html(root, _row(alt="Trade idea: SPY <b>"))
    addr = f"https://neuralstrike.co/t/{pid}/"
    assert f'<meta property="og:url" content="{addr}">' in page
    assert f'<meta property="og:image" content="{addr}card.jpg">' in page
    assert '<meta property="og:image:width" content="1200">' in page
    assert '<meta property="og:image:height" content="630">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    assert page.index('<base href="/">') < page.index("<title>")
    assert "data-idea-page" in page
    assert f'data-src="/t/{pid}/trade.json"' in page
    assert '<script src="assets/ideas.js" defer></script>' in page
    # The menu's "new" tag on Blog, as on every page carrying the site's menu.
    assert '<script src="assets/blog-new.js" defer></script>' in page
    assert "SPY &lt;b&gt;" in page and "SPY <b>" not in page


def test_page_html_states_the_images_real_height(root):
    _, page = _html(root, height=675)
    assert '<meta property="og:image:height" content="675">' in page


def test_page_html_writes_the_trade_out(root):
    _, page = _html(root)
    assert "Put credit spread" in page
    assert TI.leg_text({"side": "short", "kind": "put", "strike": 590, "qty": 1}) in page
    assert TI.leg_text({"side": "long", "kind": "put", "strike": 585, "qty": 1}) in page
    assert "Nov 28, 2026" in page
    assert TI.money(380.0) in page and TI.money(120.0) in page
    assert "588.80" in page or "588.8" in page
    assert "68%" in page
    assert "Good" in page


# ── the folder ──────────────────────────────────────────────────────────────

def test_write_makes_the_folder(root):
    row = _row()
    pid = P.page_id(row)
    assert P.write(root, row, _png()) == f"https://{repo_paths.SITE_HOST}/t/{pid}/"
    folder = root / "t" / pid
    for name in ("index.html", "trade.json", "card.jpg", "card.webp"):
        assert (folder / name).is_file(), name
    from PIL import Image
    with Image.open(folder / "card.jpg") as im:
        assert im.format == "JPEG" and im.size == (1200, 630)
    data = json.loads((folder / "trade.json").read_text(encoding="utf-8"))
    assert data["img"] == f"t/{pid}/card.webp"
    assert data["page"] == f"t/{pid}/"
    if os.name == "posix":
        assert (folder / "index.html").stat().st_mode & 0o777 == 0o644


def test_write_twice_keeps_one_folder(root):
    row = _row()
    P.write(root, row, _png())
    first = (root / "t" / P.page_id(row) / "index.html").read_bytes()
    P.write(root, row, _png())
    assert [p.name for p in (root / "t").iterdir() if p.is_dir()] == [P.page_id(row)]
    assert (root / "t" / P.page_id(row) / "index.html").read_bytes() == first


def test_write_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert P.write(blocker, _row(), _png()) is None


def test_write_without_the_template_writes_nothing(tmp_path):
    assert P.write(tmp_path, _row(), _png()) is None
    assert not (tmp_path / "t").exists()


def test_rewrite_only_when_changed(root):
    row = _row()
    pid = P.page_id(row)
    P.write(root, row, _png())
    same = P.page_row(row, pid)
    assert P.rewrite(root, pid, same) is False
    moved = dict(same, result={"status": "open", "pnl": 40.0})
    assert P.rewrite(root, pid, moved) is True
    data = json.loads((root / "t" / pid / "trade.json").read_text(encoding="utf-8"))
    assert data["result"] == {"status": "open", "pnl": 40.0}


def test_rewrite_of_a_missing_page_is_false(root):
    assert P.rewrite(root, "2026-01-01-0930-SPY", _row()) is False


# ── when a page is final ────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["target", "stop", "trail", "expired", "closed"])
def test_a_closed_result_is_final(status):
    assert P.is_final({"result": {"status": status}}, in_manifest=True, ledger_row=None)


def test_an_open_idea_in_the_manifest_is_not_final():
    assert not P.is_final({"result": {"status": "open"}}, in_manifest=True, ledger_row=None)


def test_out_of_the_manifest_with_no_ledger_row_is_final():
    assert P.is_final({"result": {"status": "open"}}, in_manifest=False, ledger_row=None)


def test_out_of_the_manifest_with_an_open_ledger_row_is_not_final():
    assert not P.is_final({"result": {"status": "open"}}, in_manifest=False,
                          ledger_row={"status": "OPEN"})


# ── the list of pages ───────────────────────────────────────────────────────

def test_sitemap_lists_every_page_newest_first(root):
    for minute in ("0930", "1010"):
        when = _NOW.replace(hour=int(minute[:2]), minute=int(minute[2:]))
        row = S.entry("SPY", "x", "Good", "a", when)
        (root / "t" / P.page_id(row)).mkdir(parents=True)
        (root / "t" / P.page_id(row) / "index.html").write_text("x")
    P.write_sitemap(root)
    lines = (root / "t" / "sitemap.txt").read_text(encoding="utf-8").splitlines()
    assert lines == [f"https://{repo_paths.SITE_HOST}/t/2026-11-23-1010-SPY/",
                     f"https://{repo_paths.SITE_HOST}/t/2026-11-23-0930-SPY/"]


def test_sitemap_of_no_pages_is_empty(root):
    P.write_sitemap(root)
    assert (root / "t" / "sitemap.txt").read_text(encoding="utf-8") == ""


def test_open_list_round_trips(root):
    ids = ["2026-11-23-1010-SPY", "2026-11-23-0930-BRK-B"]
    assert P.open_ids(root) == []
    P.set_open(root, ids)
    assert P.open_ids(root) == ids
    P.set_open(root, ids + ["../etc", 5])
    assert P.open_ids(root) == ids
    (root / "t" / "open.json").write_text("{broken")
    assert P.open_ids(root) == []
