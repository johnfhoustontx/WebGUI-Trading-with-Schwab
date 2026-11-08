"""``tools/redraw_idea_cards.py`` -- past trade ideas redrawn in the new card."""
import copy
import io
import json

from services.options_svc import site_ideas
from tools import redraw_idea_cards as r

POSTED = "2026-11-06T09:35:00-06:00"
ALT = ("Trade idea: SPY Iron condor · Nov 13 +590P / -595P / -610C / +615C · Grade Good · "
       "Risk $350 · Profit $150 · POP 71%")


def _leg(side, kind, strike):
    return {"side": side, "kind": kind, "strike": strike, "qty": 1}


def _row():
    return {"time": "09:35", "symbol": "SPY", "label": "Iron condor", "grade": "Good",
            "alt": ALT, "img": "ideas/2026-11-06/0935-SPY.webp",
            "full": "ideas/2026-11-06/0935-SPY.png",
            "legs": [_leg("long", "put", 590.0), _leg("short", "put", 595.0),
                     _leg("short", "call", 610.0), _leg("long", "call", 615.0)],
            "type": "IC", "expiration": "2026-11-13", "entry_cash": 150.0,
            "max_loss": 350.0, "max_profit": 150.0, "spot": 602.5, "approx": False,
            "posted": POSTED}


LEDGER = {"idea_score": 74.0, "idea_grade": "Strong"}


def _site(tmp_path, row=None):
    row = row or _row()
    for rel in (row["full"], row["img"]):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"OLD")
    manifest = {"updated": POSTED, "days": [{"date": "2026-11-06", "ideas": [row]}]}
    (tmp_path / "ideas.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def _ledger_for(row=None):
    row = row or _row()
    return {site_ideas.ledger_key(row["symbol"], row["posted"]): LEDGER}


def test_facts_from_manifest_alt_and_ledger():
    facts, missing = r.facts_for(_row(), LEDGER)
    assert missing == ""
    legs = [dict(l, expiration="2026-11-13") for l in _row()["legs"]]
    assert facts == {
        "symbol": "SPY", "type": "IC", "label": "Iron condor", "bias": "neutral",
        "legs": legs, "expiration": "2026-11-13", "dte": 7, "spot": 602.5,
        "grade": "Strong", "score": 74.0, "pop_pct": 71.0, "entry_cash": 150.0,
        "max_profit": 150.0, "max_loss": 350.0, "breakevens": [593.5, 611.5], "em": None}


def test_grade_falls_back_to_the_manifest_row():
    facts, _ = r.facts_for(_row(), {"idea_score": 70})
    assert facts["grade"] == "Good"


def test_missing_score_keeps_old_image(tmp_path):
    site = _site(tmp_path)
    facts, missing = r.facts_for(_row(), {"idea_grade": "Good"})
    assert facts is None and missing == "score"
    assert r.main(["--root", str(site), "--write"], ledger={}) == 0
    assert (site / "ideas/2026-11-06/0935-SPY.png").read_bytes() == b"OLD"
    assert "jpg" not in json.loads((site / "ideas.json").read_text())["days"][0]["ideas"][0]


def test_missing_pop_or_breakeven_keeps_old_image():
    row = _row()
    row["alt"] = "Trade idea: SPY Iron condor"
    assert r.facts_for(row, LEDGER) == (None, "pop_pct")


def test_directional_bias_from_single_leg():
    row = _row()
    row.update(type="LONG_CALL", label="Long call", legs=[_leg("long", "call", 600.0)],
               entry_cash=-400.0, max_loss=400.0, max_profit=None)
    facts, missing = r.facts_for(row, LEDGER)
    assert missing == "" and facts["bias"] == "bullish" and facts["max_profit"] is None
    row["legs"] = [_leg("long", "put", 600.0)]
    row["max_profit"] = 59600.0
    assert r.facts_for(row, LEDGER)[0]["bias"] == "bearish"
    row["legs"] = [_leg("short", "call", 600.0)]
    assert r.facts_for(row, LEDGER) == (None, "bias")


def test_structure_bias_is_the_scanners_own():
    """Most posted ideas are debit spreads and condors; their bias is the one the
    scanner gives the structure (``rate_trade.CALC_TO_SCORER``)."""
    row = _row()
    row.update(type="BEAR_PUT", label="Bear Put Spread",
               legs=[_leg("long", "put", 600.0), _leg("short", "put", 590.0)],
               entry_cash=-400.0, max_loss=400.0, max_profit=600.0)
    assert r.facts_for(row, LEDGER)[0]["bias"] == "bearish"
    row.update(type="BULL_CALL", label="Bull Call Spread",
               legs=[_leg("long", "call", 600.0), _leg("short", "call", 610.0)])
    assert r.facts_for(row, LEDGER)[0]["bias"] == "bullish"
    row.update(type="CONDOR_PUT", label="Put Condor",
               legs=[_leg("long", "put", 580.0), _leg("short", "put", 590.0),
                     _leg("short", "put", 600.0), _leg("long", "put", 610.0)],
               entry_cash=-300.0, max_loss=300.0, max_profit=700.0)
    assert r.facts_for(row, LEDGER)[0]["bias"] == "neutral"
    row["type"] = "NOT_A_STRUCTURE"
    assert r.facts_for(row, LEDGER) == (None, "bias")


def test_short_single_leg_takes_its_structures_bias_only_when_the_leg_matches():
    row = _row()
    row.update(type="SHORT_PUT", label="Short Put", legs=[_leg("short", "put", 590.0)],
               entry_cash=300.0, max_loss=58700.0, max_profit=300.0)
    assert r.facts_for(row, LEDGER)[0]["bias"] == "bullish"
    row["legs"] = [_leg("short", "call", 610.0)]
    assert r.facts_for(row, LEDGER) == (None, "bias")


def test_dry_run_writes_nothing(tmp_path, capsys):
    site = _site(tmp_path)
    before = (site / "ideas.json").read_text()
    assert r.main(["--root", str(site)], ledger=_ledger_for()) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "redraw" in out and "0935-SPY" in out
    assert (site / "ideas.json").read_text() == before
    assert (site / "ideas/2026-11-06/0935-SPY.png").read_bytes() == b"OLD"
    assert not (site / "ideas/2026-11-06/0935-SPY.jpg").exists()


def test_write_replaces_files_and_updates_row(tmp_path):
    from PIL import Image
    site = _site(tmp_path)
    assert r.main(["--root", str(site), "--write"], ledger=_ledger_for()) == 0
    day = site / "ideas/2026-11-06"
    with Image.open(io.BytesIO((day / "0935-SPY.png").read_bytes())) as im:
        assert im.size == (2400, 1260)
    assert (day / "0935-SPY.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert (day / "0935-SPY.webp").read_bytes()[:4] == b"RIFF"
    row = json.loads((site / "ideas.json").read_text())["days"][0]["ideas"][0]
    assert row["jpg"] == "ideas/2026-11-06/0935-SPY.jpg" and row["shape"] == "wide"
    assert row["card"]["grade"] == "Strong" and row["card"]["score"] == 74.0
    assert row["alt"] == ALT                       # the rest of the row is untouched


def test_redrawn_row_is_never_listed(tmp_path):
    site = _site(tmp_path)
    r.main(["--root", str(site), "--write"], ledger=_ledger_for())
    manifest = json.loads((site / "ideas.json").read_text())
    rows = site_ideas.card_rows(manifest)
    assert len(rows) == 1 and rows[0]["listed"] is False


def test_facts_for_does_not_mutate_its_input():
    row = _row()
    snap = copy.deepcopy(row)
    r.facts_for(row, LEDGER)
    assert row == snap


def _png_of(site):
    return (site / "ideas/2026-11-06/0935-SPY.png").read_bytes()


def test_new_cards_are_not_redrawn(tmp_path, capsys):
    for extra in ({"shape": "wide"}, {"card": {"grade": "Good"}}):
        row = dict(_row(), **extra)
        site = _site(tmp_path / extra.keys().__iter__().__next__(), row)
        assert r.main(["--root", str(site), "--write"], ledger=_ledger_for(row)) == 0
        assert _png_of(site) == b"OLD"
        assert "skip" in capsys.readouterr().out


def test_dry_run_reports_both_kinds_of_skip(tmp_path, capsys):
    row = dict(_row(), shape="wide")
    site = _site(tmp_path, row)
    r.main(["--root", str(site)], ledger=_ledger_for(row))
    assert "skip" in capsys.readouterr().out


def test_a_post_on_or_after_listed_from_is_not_redrawn(tmp_path, monkeypatch, capsys):
    from shared import social_cards
    monkeypatch.setattr(social_cards, "config",
                        lambda: {"cards": {"jpeg_quality": 90, "listed_from": "2026-11-06"}})
    site = _site(tmp_path)
    assert r.main(["--root", str(site), "--write"], ledger=_ledger_for()) == 0
    assert _png_of(site) == b"OLD"
    assert "skip" in capsys.readouterr().out


def test_webp_is_not_written_over_the_png(tmp_path):
    row = dict(_row(), img="ideas/2026-11-06/0935-SPY.png")
    site = _site(tmp_path, row)
    assert r.main(["--root", str(site), "--write"], ledger=_ledger_for(row)) == 0
    assert _png_of(site)[:8] == b"\x89PNG\r\n\x1a\n"


def test_economics_failure_keeps_old_image_and_the_run_goes_on(tmp_path, monkeypatch):
    def boom(legs, cash):
        raise ValueError("odd legs")
    monkeypatch.setattr(r.trade_idea, "economics", boom)
    assert r.facts_for(_row(), LEDGER) == (None, "breakevens")
    site = _site(tmp_path)
    assert r.main(["--root", str(site), "--write"], ledger=_ledger_for()) == 0
    assert (site / "ideas/2026-11-06/0935-SPY.png").read_bytes() == b"OLD"
