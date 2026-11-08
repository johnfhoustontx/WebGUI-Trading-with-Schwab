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


def test_an_ideas_settlement_close_asks_for_a_series_fetched_now(monkeypatch):
    """AC-101: the result is permanent on the public site, so the close it
    settles on must not be a stored series from before the close."""
    from services.options_svc import compute
    seen = {}

    class _C:
        def _request(self, endpoint, params):
            seen["endpoint"], seen["params"] = endpoint, dict(params)
            return {"candles": []}

    monkeypatch.setattr(compute._proxy, "schwab_client", _C())
    compute.daily_close("SPY", "2026-10-02")
    assert seen["endpoint"] == "/pricehistory"
    assert seen["params"]["maxAge"] == 0


# ── the social card: a JPEG beside the PNG, the card's facts, the view ──────
def _card_idea():
    """A real normalized idea, so the card's facts are the card's own."""
    from services.options_svc import trade_idea as T
    from services.options_svc.tests.test_trade_idea import pcs
    return T.normalize(pcs(type="IC", call_short=775.0, call_long=780.0))


def _first_row(root):
    m = json.loads((root / "ideas.json").read_text(encoding="utf-8"))
    return m, m["days"][0]["ideas"][0]


def test_publish_writes_jpeg_beside_png(tmp_path):
    from PIL import Image
    assert S.publish(_card_idea(), _png(), "cap", NOW, root=tmp_path) is True
    jpg = tmp_path / "ideas" / "2026-09-29" / "1436-SPY.jpg"
    assert jpg.read_bytes()[:3] == b"\xff\xd8\xff"
    with Image.open(jpg) as im:
        assert im.format == "JPEG" and im.size == (2400, 1350)


def test_a_failed_jpeg_leaves_jpg_out_and_the_post_lands(tmp_path):
    assert S.publish(_card_idea(), b"\x89PNG not really", "cap", NOW, root=tmp_path) is True
    _, first = _first_row(tmp_path)
    assert "jpg" not in first and first["full"].endswith(".png")
    assert not (tmp_path / "ideas" / "2026-09-29" / "1436-SPY.jpg").exists()


def test_manifest_row_has_jpg_shape_and_card(tmp_path):
    from services.options_svc import trade_idea_card as C
    idea = _card_idea()
    S.publish(idea, _png(), "cap", NOW, root=tmp_path)
    _, first = _first_row(tmp_path)
    assert first["jpg"] == "ideas/2026-09-29/1436-SPY.jpg"
    assert first["shape"] == "wide"
    assert first["card"] == json.loads(json.dumps(C.card_facts(idea)))
    assert set(first["card"]) == set(C.CARD_FIELDS)


def test_ref_re_accepts_jpg():
    assert S._REF_RE.match("ideas/2026-09-29/1436-SPY.jpg")
    assert not S._REF_RE.match("ideas/2026-09-29/1436-SPY.gif")
    ok = dict(_entry(), jpg="ideas/2026-09-29/1435-MU.jpg")
    old = {"days": [{"date": "2026-09-29", "ideas": [ok]}]}
    assert S.merge_manifest(old, "2026-09-29", _entry("15:35"), 6, "u")["days"][0][
        "ideas"][1]["jpg"] == "ideas/2026-09-29/1435-MU.jpg"


def test_card_rows_from_manifest(tmp_path):
    import repo_paths
    S.publish(_card_idea(), _png(), "Trade idea: SPY", NOW, root=tmp_path)
    manifest, _ = _first_row(tmp_path)
    assert S.card_rows(manifest) == [{
        "key": "idea:2026-09-29-1436-SPY", "kind": "idea",
        "at": "2026-09-29T14:36:00-05:00", "title": "SPY Iron Condor",
        "caption": "Trade idea: SPY", "link": f"https://{repo_paths.SITE_HOST}/ideas.html",
        "png": "ideas/2026-09-29/1436-SPY.png", "jpg": "ideas/2026-09-29/1436-SPY.jpg",
        "listed": False}]


def test_card_rows_skip_old_rows_without_jpg():
    old = {"days": [{"date": "2026-11-10", "ideas": [_entry("10:35", "2026-11-10")]}]}
    assert S.card_rows(old) == []
    assert S.card_rows(None) == [] and S.card_rows({"days": "x"}) == []


def test_card_rows_are_listed_from_the_config_date():
    row = dict(_entry("10:35", "2026-11-10"), jpg="ideas/2026-11-10/1035-MU.jpg",
               posted=None)
    out = S.card_rows({"days": [{"date": "2026-11-10", "ideas": [row]}]})
    assert out[0]["at"] == "2026-11-10T10:35:00" and out[0]["listed"] is True


class _CardBus:
    def __init__(self):
        self.sets = []

    def cache_set(self, key, payload, **kw):
        self.sets.append((key, payload, kw))
        return 1


def test_publish_idea_cards_writes_the_view(tmp_path, monkeypatch):
    from services.options_svc import handlers
    from shared.notify import switches
    monkeypatch.setattr(S, "SITE_ROOT", tmp_path)
    monkeypatch.setattr(switches, "site_settings", lambda: {"trade_ideas": True, "keep_days": 6})
    S.publish(_card_idea(), _png(), "cap", NOW, root=tmp_path)
    bus = _CardBus()
    assert handlers.publish_idea_cards(bus) is None
    key, payload, kw = bus.sets[0]
    assert key == "cache:options:social_cards"
    assert payload["site_enabled"] is True and len(payload["cards"]) == 1
    assert kw["skip_unchanged"] is True and kw["event"]


def test_publish_idea_cards_with_site_off(tmp_path, monkeypatch):
    from services.options_svc import handlers
    from shared.notify import switches
    monkeypatch.setattr(S, "SITE_ROOT", tmp_path)
    monkeypatch.setattr(switches, "site_settings", lambda: {"trade_ideas": False, "keep_days": 6})
    S.publish(_card_idea(), _png(), "cap", NOW, root=tmp_path)
    bus = _CardBus()
    handlers.publish_idea_cards(bus)
    assert bus.sets[0][1] == {"cards": [], "site_enabled": False}


def test_publish_idea_cards_never_raises(tmp_path, monkeypatch):
    from services.options_svc import handlers

    class _Boom:
        def cache_set(self, *a, **k):
            raise RuntimeError("redis down")
    monkeypatch.setattr(S, "SITE_ROOT", tmp_path)
    assert handlers.publish_idea_cards(_Boom()) is None
    assert handlers.publish_idea_cards(None) is None
