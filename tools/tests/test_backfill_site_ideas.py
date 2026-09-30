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


# ── entry facts rebuilt from the caption (for the results) ─────────────────
from zoneinfo import ZoneInfo  # noqa: E402

_CT = ZoneInfo("America/Chicago")
POST = dt.datetime(2026, 9, 22, 10, 35, tzinfo=_CT)


def test_a_long_call_caption_rebuilds_its_legs_and_debit():
    f = b.facts_from_caption("Trade idea: MU Long Call · Sep 26 +190C · Grade Good · "
                             "Risk $411 · Profit Unlimited · POP 39%", POST)
    assert f["legs"] == [{"side": "long", "kind": "call", "strike": 190.0, "qty": 1}]
    assert f["expiration"] == "2026-09-26"
    assert f["entry_cash"] == -411.0 and f["max_loss"] == 411.0 and f["approx"] is True
    assert f["type"] == "LONG_CALL"          # the exit rules key on the structure
    assert f["posted"] == POST.isoformat()


def test_a_credit_spread_caption_rebuilds_its_credit():
    f = b.facts_from_caption(CAPTION, POST)       # -880P / +875P, Risk $365, Profit $135
    assert [(l["side"], l["kind"], l["strike"]) for l in f["legs"]] == [
        ("short", "put", 880.0), ("long", "put", 875.0)]
    assert f["entry_cash"] == 135.0 and f["max_loss"] == 365.0
    assert f["expiration"] == "2026-10-09"


def test_thousands_separators_and_the_new_year_rollover():
    f = b.facts_from_caption("Trade idea: $SPX Long Put · Jan 2 +6,600P · Grade Good · "
                             "Risk $2,516 · Profit $657,484 · POP 39%",
                             dt.datetime(2026, 12, 29, 9, 35, tzinfo=_CT))
    assert f["legs"][0]["strike"] == 6600.0 and f["expiration"] == "2027-01-02"


def test_a_caption_whose_numbers_do_not_agree_is_not_measured():
    # a 1:2 ratio the caption cannot show: Risk disagrees with one-lot legs
    assert b.facts_from_caption("Trade idea: MU Put Credit Spread · Oct 9 -880P / +875P · "
                                "Grade Good · Risk $365 · Profit $999 · POP 71%", POST) == {}
    assert b.facts_from_caption("something else", POST) == {}


def test_backfill_with_history_stores_the_facts_and_the_post_price(tmp_path):
    arch, site = tmp_path / "arch", tmp_path / "site"
    _archive(arch, "2026-09-29", "1035", "MU")
    stamp = int(dt.datetime(2026, 9, 29, 10, 35, tzinfo=_CT).timestamp() * 1000)
    calls = []

    def minute_fn(symbol):
        calls.append(symbol)
        return [{"datetime": stamp, "close": 890.5}]
    assert b.backfill(arch, site, keep_days=6, minute_fn=minute_fn) == 1
    idea = json.loads((site / "ideas.json").read_text(encoding="utf-8"))["days"][0]["ideas"][0]
    assert idea["spot"] == 890.5 and idea["entry_cash"] == 135.0 and idea["approx"] is True
    assert calls == ["MU"]
