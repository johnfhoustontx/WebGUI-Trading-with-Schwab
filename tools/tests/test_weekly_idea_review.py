"""The weekly trade-idea review (tools/weekly_idea_review.py).

Nothing here reaches the Claude Code CLI or Redis: every test hands ``main`` a
``CliMessagesClient`` over a fake ``run=`` transcript and its own snapshot
reader, and writes into ``tmp_path``.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "webgui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.options_svc import claude_cli  # noqa: E402
from shared import idea_review  # noqa: E402
from tools import weekly_idea_review as w  # noqa: E402

FRIDAY = "2026-10-16"          # the last trading day of ISO week 2026-W42
WEDNESDAY = "2026-10-14"
CFG = {"clean_mae_pct": 25.0, "min_n": 5}


def row(**kw):
    base = {
        "source": "trade_idea", "status": "CLOSED", "exit_reason": "TARGET",
        "realized_pnl": 100.0, "unrealized_pnl": None, "mae_pnl": -10.0,
        "quantity": 1, "max_loss_per": 5.0, "idea_id": "i1",
        "idea_posted": "2026-10-13T09:15:00-05:00", "idea_grade": "Strong",
        "idea_score": 75, "strategy": "Put Credit Spread",
        "trade_type": "SWING", "symbol": "SPY",
        "entry_time": "2026-10-13T09:16:00",
        "exit_time": "2026-10-14T12:15:00-05:00",
        "entry_credit_total": 120.0, "exit_debit_total": 20.0,
    }
    base.update(kw)
    return base


def _trades():
    return [
        row(idea_id="a"),
        row(idea_id="b", symbol="QQQ", exit_reason="STOP", realized_pnl=-250.0,
            mae_pnl=-300.0),
        row(idea_id="o1", status="OPEN", exit_reason=None, exit_time=None,
            realized_pnl=None, unrealized_pnl=-40.0, symbol="IWM",
            strategy="Long Call"),
        # a manual Ledger row is not an idea and is never reviewed
        row(idea_id=None, source="manual", symbol="ZZZZ"),
    ]


def _snapshot(trades=None):
    trades = _trades() if trades is None else trades
    return lambda: {"date": FRIDAY, "paper_trades": {"trades": trades}}


# ── the fake Claude Code CLI ──

def _stdout(structured):
    events = [
        {"type": "system", "subtype": "init", "apiKeySource": "none",
         "model": "claude-sonnet-5", "tools": []},
        {"type": "result", "subtype": "success", "is_error": False,
         "num_turns": 1, "total_cost_usd": 0.02, "result": "",
         "structured_output": structured},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


class _Runner:
    def __init__(self, structured=None, raise_exc=None):
        self.structured, self.raise_exc = structured, raise_exc
        self.calls = []

    def __call__(self, argv, *, input, env, cwd, timeout):
        system_file = argv[argv.index("--system-prompt-file") + 1]
        with open(system_file, encoding="utf-8") as f:
            self.calls.append({"argv": argv, "input": input, "system": f.read()})
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(stdout=_stdout(self.structured), stderr="",
                               returncode=0)


def _client(tmp_path, runner):
    tf = tmp_path / "claude.env"
    tf.write_text("CLAUDE_CODE_OAUTH_TOKEN=tok\n", encoding="utf-8")
    return claude_cli.CliMessagesClient(cli_path="/usr/bin/true",
                                        token_file=str(tf), run=runner)


@pytest.fixture
def fake_cli_raises(tmp_path):
    return _client(tmp_path, _Runner(raise_exc=OSError("no claude binary")))


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(w, "_cfg", lambda: dict(CFG, review_model="sonnet"))

    def _no_real_cli():
        raise AssertionError("a test reached the real Claude Code CLI client")
    monkeypatch.setattr(w, "_default_client", _no_real_cli)


def _run(tmp_path, argv, **kw):
    kw.setdefault("read_snapshot", _snapshot())
    return w.main(argv, archive_root=tmp_path / "archive", **kw)


def _report(tmp_path, week="2026-W42"):
    return (tmp_path / "archive" / week / "report.html").read_text(encoding="utf-8")


def _facts(trades=None, min_n=5):
    return idea_review.facts(_trades() if trades is None else trades,
                             date.fromisoformat(FRIDAY), dict(CFG, min_n=min_n))


# ── the gates ──

def test_not_review_day_writes_nothing(tmp_path, capsys):
    called = []
    rc = _run(tmp_path, ["--date", WEDNESDAY],
              read_snapshot=lambda: called.append(1) or {},
              client=_client(tmp_path, _Runner({"assessment": [], "suggestions": []})))
    assert rc == 0
    assert not (tmp_path / "archive").exists()
    assert called == []                      # did not even read the bus
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1 and WEDNESDAY in out


def test_force_runs_on_a_day_that_is_not_the_review_day(tmp_path):
    runner = _Runner({"assessment": ["A quiet week."], "suggestions": []})
    rc = _run(tmp_path, ["--date", WEDNESDAY, "--force"],
              client=_client(tmp_path, runner))
    assert rc == 0
    assert "A quiet week." in _report(tmp_path)


@pytest.mark.parametrize("reader", [
    lambda: {"date": FRIDAY, "paper_trades": {}},
    lambda: {"date": FRIDAY},
    lambda: (_ for _ in ()).throw(ConnectionError("bus down")),
])
def test_empty_bus_writes_nothing_and_exits_2(tmp_path, reader, capsys):
    runner = _Runner({"assessment": ["x"], "suggestions": []})
    rc = _run(tmp_path, ["--date", FRIDAY], read_snapshot=reader,
              client=_client(tmp_path, runner))
    assert rc == 2
    assert not (tmp_path / "archive").exists()
    assert runner.calls == []                # no Claude call either
    assert capsys.readouterr().err.strip()


def test_a_book_with_no_idea_rows_is_still_reviewed(tmp_path):
    """An answered bus whose Ledger holds no idea rows is a real, empty week."""
    runner = _Runner({"assessment": ["No trades this week."], "suggestions": []})
    rc = _run(tmp_path, ["--date", FRIDAY], read_snapshot=_snapshot([]),
              client=_client(tmp_path, runner))
    assert rc == 0
    assert "No trades this week." in _report(tmp_path)


# ── the Claude call ──

def test_the_request_is_the_forced_schema_on_the_configured_model(tmp_path):
    runner = _Runner({"assessment": ["Fine."], "suggestions": []})
    assert _run(tmp_path, ["--date", FRIDAY], client=_client(tmp_path, runner)) == 0
    (call,) = runner.calls
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == w.JUDGMENT_SCHEMA
    assert call["system"] == w.PROMPT_FILE.read_text(encoding="utf-8")
    sent = json.loads(call["input"])
    assert sent["week"]["total"]["id"] == "week.total"
    assert sent["week"]["total"]["n"] == 2   # the manual row is not in it


def test_the_prompt_file_carries_the_rules():
    text = w.PROMPT_FILE.read_text(encoding="utf-8").lower()
    for rule in ("fact id", "too few trades", "number", "date", "time"):
        assert rule in text, rule


def test_claude_failure_writes_numbers_only_and_exits_3(tmp_path, fake_cli_raises,
                                                        capsys):
    rc = _run(tmp_path, ["--date", FRIDAY], client=fake_cli_raises)
    assert rc == 3
    html = _report(tmp_path)
    assert w.MISSING_LINE in html
    assert "SPY" in html and "QQQ" in html      # the numbers are all there
    # the reason reaches stderr, and so the journal
    assert "CliUnusable: the CLI could not start" in capsys.readouterr().err
    # written atomically: no temp file left beside it
    assert [p.name for p in (tmp_path / "archive" / "2026-W42").iterdir()] \
        == ["report.html"]


def test_a_reply_that_is_not_the_schema_is_a_failure(tmp_path):
    runner = _Runner({"assessment": "not a list", "suggestions": None})
    rc = _run(tmp_path, ["--date", FRIDAY], client=_client(tmp_path, runner))
    assert rc == 3
    assert w.MISSING_LINE in _report(tmp_path)


def test_never_uses_fallback_client():
    src = (ROOT / "tools" / "weekly_idea_review.py").read_text(encoding="utf-8")
    assert "FallbackClient" not in src
    assert "ANTHROPIC_API_KEY" not in src
    assert "make_briefing_client" not in src
    assert "CliMessagesClient" in src


# ── verify ──

def test_unknown_citation_drops_suggestion():
    f = _facts()
    j = {"assessment": [],
         "suggestions": [
             {"text": "Keep trading SPY.", "cites": ["week.symbol.SPY"]},
             {"text": "Trade more TSLA.", "cites": ["week.symbol.TSLA"]},
             {"text": "Something.", "cites": []},
             {"text": "Half right.", "cites": ["week.symbol.SPY", "nope"]},
         ]}
    out = w.verify(j, f, 5)
    assert [s["text"] for s in out["suggestions"]] == ["Keep trading SPY."]
    assert out["dropped_suggestions"] == 3


def test_judgment_numbers_must_appear_in_facts():
    f = _facts()
    j = {"assessment": ["The ideas won 9 of 10 this week.",
                       "Two trades closed and the week made a net loss of $150.00.",
                       "Win rate was 50.00%, or 50.0 percent, or 50 percent."],
         "suggestions": [
             {"text": "SPY won 9 of 10.", "cites": ["week.symbol.SPY"]},
             {"text": "QQQ lost 250.00.", "cites": ["week.symbol.QQQ"]},
         ]}
    out = w.verify(j, f, 5)
    assert out["assessment"] == [j["assessment"][1], j["assessment"][2]]
    assert [s["text"] for s in out["suggestions"]] == ["QQQ lost 250.00."]
    assert out["dropped_paragraphs"] == 1 and out["dropped_suggestions"] == 1


def test_a_cited_fact_id_is_not_read_as_a_number():
    trades = [row(idea_id="a", idea_posted="2026-10-13T13:15:00-05:00")]
    f = _facts(trades)
    assert "week.post_hour.13" in idea_review.fact_ids(f)
    j = {"assessment": ["See week.post_hour.13 and the 70-79 band."],
         "suggestions": [{"text": "Watch week.post_hour.13.",
                          "cites": ["week.post_hour.13"]}]}
    out = w.verify(j, f, 5)
    assert out["assessment"] == j["assessment"]
    assert len(out["suggestions"]) == 1


def test_thin_suggestion_labelled(tmp_path):
    f = _facts()
    j = {"assessment": [],
         "suggestions": [
             {"text": "SPY looks good.", "cites": ["week.symbol.SPY"]},
             {"text": "All ideas overall.",
              "cites": ["week.symbol.SPY", "all_time.total"]},
         ]}
    out = w.verify(j, f, 2)
    thin, mixed = out["suggestions"]
    assert (thin["n"], thin["thin"]) == (1, True)
    assert (mixed["n"], mixed["thin"]) == (3, False)   # all_time.total n=2 >= 2
    html = w.render(f, out, "2026-W42")
    assert "based on 1 trade" in html and "based on 3 trades" in html
    assert html.count("too few trades to act on") == 1


# ── the document ──

def test_report_lists_closed_and_open_separately(tmp_path):
    runner = _Runner({"assessment": ["Two closed."],
                      "suggestions": [{"text": "Mind QQQ.",
                                       "cites": ["week.symbol.QQQ"]}]})
    assert _run(tmp_path, ["--date", FRIDAY], client=_client(tmp_path, runner)) == 0
    html = _report(tmp_path)
    closed_at = html.index("This week's closed trades")
    open_at = html.index("Open trades", closed_at)
    closed, opened = html[closed_at:open_at], html[open_at:].split("<h2")[0]
    assert "SPY" in closed and "QQQ" in closed and "IWM" not in closed
    assert "IWM" in opened and "SPY" not in opened
    assert "clean win" in closed and "stopped" in closed
    assert "$120.00" in closed and "$20.00" in closed      # entry and exit
    assert "ZZZZ" not in html                                # not an idea row
    assert html.index("Assessment") < html.index("Suggestions") < closed_at
    assert "Mind QQQ." in html and "Two closed." in html
    assert html.startswith("<!DOCTYPE html>")


def test_render_without_a_judgment_says_so_and_keeps_every_table():
    html = w.render(_facts(), None, "2026-W42")
    assert w.MISSING_LINE in html
    for heading in ("This week", "All time", "By grade", "By structure",
                    "By symbol"):
        assert heading in html, heading


def test_render_escapes_claudes_text():
    j = {"assessment": ["<script>x</script>"], "suggestions": [],
         "dropped_paragraphs": 0, "dropped_suggestions": 0}
    html = w.render(_facts(), j, "2026-W42")
    assert "<script>x" not in html and "&lt;script&gt;" in html


def test_week_label_is_iso_year_and_week():
    assert w.week_label(date(2026, 10, 16)) == "2026-W42"
    assert w.week_label(date(2027, 1, 1)) == "2026-W53"
    assert w.ARCHIVE_ROOT == ROOT / "webgui" / "data" / "idea_review"


def test_the_archive_is_gitignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "webgui/data/idea_review/" in lines
