"""``tools/backfill_site_ideas.py`` -- the site's trade ideas from the card archive."""
import datetime as dt
import io
import json

from tools import backfill_site_ideas as b

CAPTION = ("Trade idea: MU Put credit spread · Oct 9 -880P / +875P · Grade Good · "
           "Risk $365 · Profit $135 · POP 71%")


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1350), (22, 24, 38)).save(buf, "PNG")
    return buf.getvalue()


def _archive(root, day, hhmm, sym, caption=CAPTION):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    stem = f"trade-idea-{day}-{hhmm}-{sym}"
    (d / f"{stem}.png").write_bytes(_png())
    (d / f"{stem}.txt").write_text(caption + "\n", encoding="utf-8")


def test_the_archive_name_gives_the_post_time_and_symbol():
    when, sym = b.parse_archive_name("trade-idea-2026-09-29-1435-MU.png")
    assert (when.year, when.month, when.day, when.hour, when.minute) == (2026, 9, 29, 14, 35)
    assert str(when.tzinfo) == "America/Chicago" and sym == "MU"
    assert b.parse_archive_name("trade-idea-2026-09-29-1435--SPX.png")[1] == "-SPX"
    assert b.parse_archive_name("snapshot.png") is None


def test_the_caption_gives_the_symbol_label_and_grade():
    assert b.parse_caption(CAPTION) == ("MU", "Put credit spread", "Good")
    assert b.parse_caption("Trade idea: $SPX Iron condor · Oct 9 · Grade Strong") == (
        "$SPX", "Iron condor", "Strong")
    assert b.parse_caption("something else") == ("", "", "")


def test_backfill_publishes_the_newest_days_only(tmp_path):
    arch, site = tmp_path / "arch", tmp_path / "site"
    for day in ("2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"):
        _archive(arch, day, "1035", "MU")
    _archive(arch, "2026-09-29", "0835", "QQQ",
             caption="Trade idea: QQQ Long call · Oct 2 +600C · Grade Strong")
    assert b.backfill(arch, site, keep_days=3) == 4
    m = json.loads((site / "ideas.json").read_text(encoding="utf-8"))
    assert [d["date"] for d in m["days"]] == ["2026-09-29", "2026-09-28", "2026-09-25"]
    today = m["days"][0]["ideas"]
    assert [(i["time"], i["symbol"], i["grade"]) for i in today] == [
        ("10:35", "MU", "Good"), ("08:35", "QQQ", "Strong")]
    assert today[0]["alt"] == CAPTION


def test_a_card_with_no_caption_still_publishes(tmp_path):
    arch, site = tmp_path / "arch", tmp_path / "site"
    _archive(arch, "2026-09-29", "1035", "MU")
    (arch / "2026-09-29" / "trade-idea-2026-09-29-1035-MU.txt").unlink()
    assert b.backfill(arch, site, keep_days=6) == 1
    m = json.loads((site / "ideas.json").read_text(encoding="utf-8"))
    assert m["days"][0]["ideas"][0]["symbol"] == "MU"


def test_a_missing_archive_publishes_nothing(tmp_path):
    assert b.backfill(tmp_path / "nope", tmp_path / "site", keep_days=6) == 0
