"""Trade ideas published into the static site tree (deploy/site/ideas/)."""
import datetime as dt
import io
import json
from zoneinfo import ZoneInfo

import pytest

from services.options_svc import site_ideas as S

_CT = ZoneInfo("America/Chicago")
NOW = dt.datetime(2026, 9, 29, 14, 36, tzinfo=_CT)


def _png(w=2400, h=1350):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (22, 24, 38)).save(buf, "PNG")
    return buf.getvalue()


def _idea(symbol="MU", label="Put credit spread", grade="Good"):
    return {"symbol": symbol, "label": label, "grade": grade}


def _entry(time="14:35", day="2026-09-29", symbol="MU"):
    hhmm = time.replace(":", "")
    return {"time": time, "symbol": symbol, "label": "Long call", "grade": "Good",
            "alt": "Trade idea", "img": f"ideas/{day}/{hhmm}-{symbol}.webp",
            "full": f"ideas/{day}/{hhmm}-{symbol}.png"}


# ── names ───────────────────────────────────────────────────────────────────
def test_slug_drops_the_index_sigil_and_anything_unsafe():
    assert S.slug("$SPX") == "SPX"
    assert S.slug("BRK/B") == "BRK-B"
    assert S.slug("") == "trade"


def test_entry_points_at_the_day_folder_and_the_minute():
    e = S.entry("$SPX", "Iron condor", "Strong", "Trade idea: ...", NOW)
    assert e == {"time": "14:36", "symbol": "$SPX", "label": "Iron condor",
                 "grade": "Strong", "alt": "Trade idea: ...",
                 "img": "ideas/2026-09-29/1436-SPX.webp",
                 "full": "ideas/2026-09-29/1436-SPX.png"}


# ── the manifest (pure) ─────────────────────────────────────────────────────
def test_the_first_idea_starts_a_manifest():
    m = S.merge_manifest(None, "2026-09-29", _entry(), 6, "u")
    assert m == {"updated": "u", "days": [{"date": "2026-09-29", "ideas": [_entry()]}]}


def test_ideas_and_days_run_newest_first():
    m = S.merge_manifest(None, "2026-09-28", _entry("10:35", "2026-09-28"), 6, "u")
    m = S.merge_manifest(m, "2026-09-29", _entry("08:35"), 6, "u")
    m = S.merge_manifest(m, "2026-09-29", _entry("09:35", symbol="QQQ"), 6, "u")
    assert [d["date"] for d in m["days"]] == ["2026-09-29", "2026-09-28"]
    assert [i["time"] for i in m["days"][0]["ideas"]] == ["09:35", "08:35"]


def test_a_repost_of_one_slot_replaces_never_duplicates():
    m = S.merge_manifest(None, "2026-09-29", _entry(), 6, "u")
    again = dict(_entry(), grade="Strong")
    m = S.merge_manifest(m, "2026-09-29", again, 6, "u")
    assert m["days"][0]["ideas"] == [again]


def test_only_the_newest_keep_days_days_survive():
    m = None
    for d in range(21, 29):                       # eight posting days
        day = f"2026-09-{d:02d}"
        m = S.merge_manifest(m, day, _entry(day=day), 6, "u")
    assert [d["date"] for d in m["days"]] == [f"2026-09-{d}" for d in range(28, 22, -1)]


@pytest.mark.parametrize("bad", [None, [], "x", {"days": "x"}, {"days": [1, {"date": 5}]},
                                 {"days": [{"date": "2026-09-28", "ideas": "x"}]}])
def test_a_malformed_manifest_is_dropped_not_propagated(bad):
    m = S.merge_manifest(bad, "2026-09-29", _entry(), 6, "u")
    assert m["days"] == [{"date": "2026-09-29", "ideas": [_entry()]}]


def test_malformed_ideas_inside_a_good_day_are_dropped():
    old = {"days": [{"date": "2026-09-28", "ideas": [_entry("10:35", "2026-09-28"), "junk",
                                                     {"time": "x"}]}]}
    m = S.merge_manifest(old, "2026-09-29", _entry(), 6, "u")
    assert m["days"][1]["ideas"] == [_entry("10:35", "2026-09-28")]


# ── publish (disk) ──────────────────────────────────────────────────────────
def test_publish_writes_the_card_a_web_copy_and_the_manifest(tmp_path):
    assert S.publish(_idea(), _png(), "Trade idea: MU", NOW, root=tmp_path) is True
    day = tmp_path / "ideas" / "2026-09-29"
    assert (day / "1436-MU.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    from PIL import Image
    with Image.open(day / "1436-MU.webp") as im:
        assert im.format == "WEBP" and im.size == (1200, 675)
    m = json.loads((tmp_path / "ideas.json").read_text(encoding="utf-8"))
    first = m["days"][0]["ideas"][0]
    assert first["img"] == "ideas/2026-09-29/1436-MU.webp"
    assert first["full"] == "ideas/2026-09-29/1436-MU.png"
    assert first["label"] == "Put credit spread" and first["grade"] == "Good"
    assert m["updated"].startswith("2026-09-29T14:36")


def test_a_card_that_cannot_be_converted_still_publishes_as_the_png(tmp_path):
    assert S.publish(_idea(), b"\x89PNG not really", "cap", NOW, root=tmp_path) is True
    m = json.loads((tmp_path / "ideas.json").read_text(encoding="utf-8"))
    first = m["days"][0]["ideas"][0]
    assert first["img"] == first["full"] == "ideas/2026-09-29/1436-MU.png"
    assert not (tmp_path / "ideas" / "2026-09-29" / "1436-MU.webp").exists()


def test_publish_prunes_only_dated_folders_that_fell_out(tmp_path):
    ideas = tmp_path / "ideas"
    for name in ("2026-09-01", "2026-09-28", "keep-me"):
        (ideas / name).mkdir(parents=True)
    (ideas / "notes.txt").write_text("x")
    old = {"days": [{"date": "2026-09-28", "ideas": [_entry("10:35", "2026-09-28")]}]}
    (tmp_path / "ideas.json").write_text(json.dumps(old))
    assert S.publish(_idea(), _png(), "cap", NOW, root=tmp_path, keep_days=2)
    left = sorted(p.name for p in ideas.iterdir())
    assert left == ["2026-09-28", "2026-09-29", "keep-me", "notes.txt"]


def test_publish_never_raises_and_says_false_when_it_cannot_write(tmp_path):
    blocker = tmp_path / "site"
    blocker.write_text("a FILE where the site directory should be")
    assert S.publish(_idea(), _png(), "cap", NOW, root=blocker) is False


def test_publish_defaults_to_the_module_root(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SITE_ROOT", tmp_path)
    assert S.publish(_idea(), _png(), "cap", NOW)
    assert (tmp_path / "ideas.json").exists()
