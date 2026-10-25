"""The regime study's live-vs-backfill agreement check (tools/scalp_regime_agree).

Every store here is a small SQLite file built in ``tmp_path``; nothing opens a live store.
"""
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.options_svc import scalp_store  # noqa: E402
from tools import scalp_regime_agree as agree  # noqa: E402


def snap(symbol="$SPX", net_gex=-1e9, state="ok", age=1.0, untrusted=False):
    return {"matrix": {symbol: {"spot": 100.0, "flip": 99.0, "call_wall": 105.0,
                                "put_wall": 95.0, "net_gex": net_gex,
                                "walls_untrusted": untrusted}},
            "sources": {"matrix": {"state": state, "age_min": age}}}


def test_compare_counts_agreement():
    live = {("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(net_gex=-1e9)},
            ("2026-10-21", "13:06", "$SPX"): {"snapshot": snap(net_gex=-1e9)}}
    recs = [(("intraday", "2026-10-21", "12:36", "$SPX"), snap(net_gex=-2e9, state="backfill", age=None)),
            (("intraday", "2026-10-21", "13:06", "$SPX"), snap(net_gex=3e9, state="backfill", age=None))]
    got = agree.compare(recs, live)
    assert (got["slots"], got["agreeing"], got["sessions"]) == (2, 1, 1)
    assert got["share"] == 0.5 and got["passed"] is False
    assert got["disagreeing"] == [["2026-10-21", "13:06", "$SPX", "negative", "positive"]]


def test_compare_skips_gaps_morning_and_in_sample_days():
    live = {("2026-10-20", "12:36", "$SPX"): {"snapshot": snap()},
            ("2026-10-21", "08:36", "$SPX"): {"snapshot": snap()},
            ("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(untrusted=True)}}
    recs = [(("intraday", "2026-10-20", "12:36", "$SPX"), snap(state="backfill", age=None)),
            (("morning", "2026-10-21", "08:36", "$SPX"), snap(state="backfill", age=None)),
            (("intraday", "2026-10-21", "12:36", "$SPX"), snap(state="backfill", age=None))]
    got = agree.compare(recs, live)
    assert got["slots"] == 0 and got["share"] is None and got["passed"] is False


def test_compare_passes_at_the_declared_thresholds():
    days = ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
    live = {(d, "12:36", "$SPX"): {"snapshot": snap()} for d in days}
    recs = [(("intraday", d, "12:36", "$SPX"), snap(state="backfill", age=None)) for d in days]
    got = agree.compare(recs, live)
    assert got["sessions"] == 5 and got["share"] == 1.0 and got["passed"] is True


def test_compare_needs_the_declared_sessions_even_at_full_agreement():
    days = ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"]
    live = {(d, "12:36", "$SPX"): {"snapshot": snap()} for d in days}
    recs = [(("intraday", d, "12:36", "$SPX"), snap(state="backfill", age=None)) for d in days]
    got = agree.compare(recs, live)
    assert got["share"] == 1.0 and got["sessions"] == 4 and got["passed"] is False


def test_compare_lists_are_sorted():
    live = {("2026-10-22", "13:06", "$SPX"): {"snapshot": snap(net_gex=-1e9)},
            ("2026-10-21", "12:36", "$SPX"): {"snapshot": snap(net_gex=-1e9)}}
    recs = [(("intraday", "2026-10-22", "13:06", "$SPX"), snap(net_gex=1e9, state="backfill", age=None)),
            (("intraday", "2026-10-21", "12:36", "$SPX"), snap(net_gex=1e9, state="backfill", age=None))]
    got = agree.compare(recs, live)
    assert got["days"] == ["2026-10-21", "2026-10-22"]
    assert [row[0] for row in got["disagreeing"]] == ["2026-10-21", "2026-10-22"]


def test_main_writes_the_result_and_never_writes_the_store(tmp_path, capsys):
    db = tmp_path / "scalp.db"
    scalp_store.save_reading("2026-10-21", "12:36", "$SPX", {"snapshot": snap()}, db_path=db)
    before = db.read_bytes()
    jsonl = tmp_path / "h.jsonl"
    jsonl.write_text(json.dumps({"kind": "intraday", "day": "2026-10-21", "slot": "12:36",
                                 "symbol": "$SPX", "snapshot": snap(state="backfill", age=None)}) + "\n"
                     + "not json\n")
    out = tmp_path / "agree.json"
    assert agree.main(["--in", str(jsonl), "--db", str(db), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["slots"] == 1
    assert db.read_bytes() == before
    text = capsys.readouterr().out
    assert "1 session" in text and "100.00%" in text and "1 unreadable line" in text
    assert "95.00%" in text and "at least 5 sessions" in text and "did not pass" in text


def test_main_exits_2_on_an_unreadable_input(tmp_path, capsys):
    db = tmp_path / "scalp.db"
    scalp_store.save_reading("2026-10-21", "12:36", "$SPX", {"snapshot": snap()}, db_path=db)
    out = tmp_path / "agree.json"
    assert agree.main(["--in", str(tmp_path / "missing.jsonl"), "--db", str(db),
                       "--out", str(out)]) == 2
    assert capsys.readouterr().err.strip()
    assert not out.exists()


def test_main_exits_2_when_the_store_is_missing_and_creates_nothing(tmp_path, capsys):
    jsonl = tmp_path / "h.jsonl"
    jsonl.write_text("")
    db = tmp_path / "nope.db"
    assert agree.main(["--in", str(jsonl), "--db", str(db),
                       "--out", str(tmp_path / "agree.json")]) == 2
    assert capsys.readouterr().err.strip()
    assert not db.exists()
