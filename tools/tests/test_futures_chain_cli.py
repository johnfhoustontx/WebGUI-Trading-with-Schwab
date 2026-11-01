"""``tools/futures_chain_cli.py`` -- the /ES and /NQ feed's two commands on vps2.

``plan`` tells the capture on the PC what to capture; ``ingest`` takes what it
captured on stdin and puts it on ``cmd:futures_chain``. Both run over ssh from
PowerShell, so the tests cover what that path does to the bytes (a byte order
mark, CRLF) and what must not happen when something is wrong (a half-written
batch). Runs on the fake bus. Design:
docs/plans/2026-10-31-futures-0dte-chain-feed-design.md, section 5.6.
"""
import ast
import io
import json
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import futures_chain  # noqa: E402
from shared import futures_chain_config as cfg  # noqa: E402
from shared.bus import Bus  # noqa: E402
from tools import futures_chain_cli as cli  # noqa: E402

STREAM = "cmd:futures_chain"
_CT = ZoneInfo("America/Chicago")


def es():
    return {"instrument": "ES", "expiry": "2026-11-02", "captured_at": 1.0,
            "descriptions": {}, "underlying_symbol": "/ESZ26:XCME",
            "underlying_price": 7600.0, "contracts": []}


def nq():
    return {**es(), "instrument": "NQ", "underlying_symbol": "/NQZ26:XCME",
            "underlying_price": 26000.0}


def huge():
    """One snapshot over the 512 KB limit: 700 rows of 1 KB of text."""
    big = {**es(), "contracts": ["x" * 1000] * 700}
    return io.StringIO(json.dumps({"snapshots": [big]}))


def stdin_of(payload):
    return io.StringIO(json.dumps(payload))


def stream_types(bus, stream):
    return [json.loads(fields["data"])["type"] for _id, fields in bus._r.xrange(stream)]


def stream_args(bus, stream):
    return [json.loads(fields["data"])["args"] for _id, fields in bus._r.xrange(stream)]


@pytest.fixture
def fake_bus():
    from shared.bus.client import reset_fake_bus
    reset_fake_bus()
    return Bus(fake=True)


@pytest.fixture
def frozen_sunday_evening(monkeypatch):
    """Sunday 2026-11-01, 18:00 Central: the session to come is Monday's."""
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 11, 1, 18, 0, tzinfo=_CT))


# ── plan ─────────────────────────────────────────────────────────────────────

def test_plan_prints_json_a_powershell_can_read(capsys, frozen_sunday_evening):
    assert cli.main(["plan"]) == 0
    assert json.loads(capsys.readouterr().out)["expiry"] == "2026-11-02"


def test_plan_is_what_the_server_builds(capsys, frozen_sunday_evening):
    cli.main(["plan"])
    want = futures_chain.build_plan(datetime(2026, 11, 1, 18, 0, tzinfo=_CT))
    assert json.loads(capsys.readouterr().out) == json.loads(json.dumps(want))


def test_plan_is_plain_ascii_with_no_byte_order_mark_and_no_carriage_return(
        capsys, frozen_sunday_evening):
    cli.main(["plan"])
    out = capsys.readouterr().out
    assert out.isascii() and "\r" not in out and not out.startswith("﻿")
    assert out.endswith("\n") and out.count("\n") == 1


def test_plan_writes_where_it_is_told(frozen_sunday_evening):
    out = io.StringIO()
    assert cli.main(["plan"], stdout=out) == 0
    assert json.loads(out.getvalue())["interval_sec"] == cfg.capture()["interval_sec"]


def test_plan_needs_no_bus(monkeypatch, frozen_sunday_evening):
    """The capture asks for its plan even while Redis is down."""
    def no_bus(*args, **kwargs):
        raise AssertionError("plan opened a bus")
    monkeypatch.setattr(cli, "Bus", no_bus)
    assert cli.main(["plan"], stdout=io.StringIO()) == 0


# ── ingest ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("instrument", [["ES"], {"ES": 1}, 5, None])
def test_an_instrument_that_cannot_be_looked_up_exits_two(fake_bus, capsys, instrument):
    """A list is unhashable: it used to raise out of ``in roots``, a traceback and exit 1."""
    code = cli.main(["ingest"], stdin=stdin_of({"snapshots": [{**es(), "instrument": instrument}]}))
    assert code == cli.EXIT_BAD_INPUT
    assert "snapshot 1" in capsys.readouterr().err
    assert stream_types(fake_bus, STREAM) == []


def test_json_nested_too_deep_exits_two_with_no_traceback(fake_bus, capsys):
    deep = '{"snapshots": ' + "[" * 200000 + "]" * 200000 + "}"
    assert cli.main(["ingest"], stdin=io.StringIO(deep)) == cli.EXIT_BAD_INPUT
    assert "nested" in capsys.readouterr().err
    assert stream_types(fake_bus, STREAM) == []


@pytest.mark.parametrize("depth", [20, 500, 990, 5000, 20000])
def test_a_deep_snapshot_that_parses_is_not_reported_as_redis_refusing(fake_bus, capsys, depth):
    """Deep enough to pass ``json.loads`` and fail when it is written back, which read
    as "could not write to Redis", exit 4."""
    nested = "[" * depth + "]" * depth
    text = '{"snapshots": [{"instrument": "ES", "contracts": ' + nested + "}]}"
    assert cli.main(["ingest"], stdin=io.StringIO(text)) == cli.EXIT_BAD_INPUT
    assert "nested" in capsys.readouterr().err
    assert stream_types(fake_bus, STREAM) == []


def test_a_real_captures_depth_is_well_inside_the_limit():
    row = {"series": "E1AX26", "side": "C", "strike": 7600, "mark": 1.0}
    assert not cli._too_deep({"snapshots": [{**es(), "descriptions": {"E1AX26": "text"},
                                             "contracts": [row]}],
                              "status": {"reason": "", "at": 1.0}})


def test_ingest_enqueues_one_command_per_instrument(fake_bus):
    assert cli.main(["ingest"], stdin=io.StringIO(json.dumps(
        {"snapshots": [es(), nq()], "status": {"reason": ""}}))) == 0
    assert stream_types(fake_bus, STREAM) == ["snapshot", "snapshot", "pc_status"]


def test_ingest_puts_each_snapshot_on_the_stream_untouched(fake_bus):
    cli.main(["ingest"], stdin=stdin_of({"snapshots": [es(), nq()],
                                         "status": {"reason": "Excel did not start"}}))
    assert stream_args(fake_bus, STREAM) == [es(), nq(), {"reason": "Excel did not start"}]


def test_ingest_uses_the_features_own_stream(fake_bus):
    cli.main(["ingest"], stdin=stdin_of({"snapshots": [es()]}))
    assert futures_chain.STREAM == STREAM
    assert not fake_bus._r.exists("cmd:options")


def test_ingest_refuses_an_oversized_snapshot(fake_bus):
    assert cli.main(["ingest"], stdin=huge()) != 0 and stream_types(fake_bus, STREAM) == []


def test_an_oversized_nq_does_not_block_es(fake_bus, capsys):
    """One instrument failing does not touch the other (design 5.6): /ES lands,
    the PC's status lands, the dropped one is named and the exit says so."""
    big = {**nq(), "contracts": ["x" * 1000] * 700}
    code = cli.main(["ingest"], stdin=stdin_of({"snapshots": [es(), big],
                                                "status": {"reason": ""}}))
    assert code == 3
    assert stream_types(fake_bus, STREAM) == ["snapshot", "pc_status"]
    assert stream_args(fake_bus, STREAM)[0]["instrument"] == "ES"
    err = capsys.readouterr().err
    assert "NQ" in err and "not sent" in err


def test_an_oversized_es_does_not_block_nq(fake_bus):
    big = {**es(), "contracts": ["x" * 1000] * 700}
    assert cli.main(["ingest"], stdin=stdin_of({"snapshots": [big, nq()]})) == 3
    assert [a["instrument"] for a in stream_args(fake_bus, STREAM)] == ["NQ"]


def test_an_oversized_snapshot_still_says_what_was_queued(fake_bus):
    big = {**nq(), "contracts": ["x" * 1000] * 700}
    out = io.StringIO()
    cli.main(["ingest"], stdin=stdin_of({"snapshots": [es(), big]}), stdout=out)
    assert json.loads(out.getvalue()) == {"queued": ["snapshot"]}


def test_an_oversized_snapshot_alone_opens_no_connection(monkeypatch):
    def no_bus(*args, **kwargs):
        raise AssertionError("opened a bus with nothing to send")
    monkeypatch.setattr(cli, "Bus", no_bus)
    assert cli.main(["ingest"], stdin=huge()) == 3


def test_the_size_limit_comes_from_the_config(fake_bus, monkeypatch):
    real = cfg.capture
    monkeypatch.setattr(cfg, "capture", lambda: {**real(), "max_snapshot_kb": 16})
    mid = {**es(), "contracts": ["x" * 1000] * 20}              # about 20 KB
    assert cli.main(["ingest"], stdin=stdin_of({"snapshots": [mid]})) != 0
    assert cli.main(["ingest"], stdin=stdin_of({"snapshots": [es()]})) == 0


def test_ingest_tolerates_a_bom_and_crlf_from_powershell(fake_bus):
    assert cli.main(["ingest"], stdin=io.StringIO(
        "﻿" + json.dumps({"snapshots": [es()]}, indent=2).replace("\n", "\r\n")
        + "\r\n")) == 0
    assert stream_types(fake_bus, STREAM) == ["snapshot"]


def test_ingest_reads_utf8_bytes_with_a_bom(fake_bus):
    raw = b"\xef\xbb\xbf" + json.dumps({"snapshots": [es()]}).encode("utf-8")
    assert cli.main(["ingest"], stdin=io.BytesIO(raw)) == 0
    assert stream_types(fake_bus, STREAM) == ["snapshot"]


def test_ingest_with_only_a_status_enqueues_only_the_status(fake_bus):
    assert cli.main(["ingest"], stdin=stdin_of({"status": {"reason": "ThinkorSwim logged out"}})) == 0
    assert stream_types(fake_bus, STREAM) == ["pc_status"]


@pytest.mark.parametrize("text", [
    "", "   ", "not json", "[]", "[1, 2]", "null", '{"snapshots": "x"}',
    '{"snapshots": [1]}', '{"snapshots": [{"no_instrument": 1}]}',
    '{"snapshots": [{"instrument": "CL"}]}', '{"status": "fine"}',
    '{"snapshots": [], "status": 5}', "{}",
])
def test_ingest_refuses_input_it_cannot_read_and_enqueues_nothing(fake_bus, text):
    assert cli.main(["ingest"], stdin=io.StringIO(text)) != 0
    assert stream_types(fake_bus, STREAM) == []


def test_ingest_says_why_on_stderr(fake_bus, capsys):
    cli.main(["ingest"], stdin=io.StringIO("not json"))
    assert "json" in capsys.readouterr().err.lower()


def test_ingest_reads_no_more_than_a_batch_can_be(fake_bus, capsys):
    """Three instruments' worth of the limit and a little over is not a capture."""
    flood = io.StringIO("[" + "0," * 5_000_000 + "0]")
    assert cli.main(["ingest"], stdin=flood) != 0
    assert "large" in capsys.readouterr().err.lower()


def test_ingest_reports_a_bus_that_is_down_and_what_got_through(monkeypatch, capsys):
    class Down:
        def __init__(self):
            self.sent = 0

        def enqueue_command(self, stream, command):
            if self.sent:
                raise ConnectionError("Connection refused")
            self.sent += 1

    monkeypatch.setattr(cli, "Bus", Down)
    assert cli.main(["ingest"], stdin=stdin_of({"snapshots": [es(), nq()]})) != 0
    err = capsys.readouterr().err
    assert "Connection refused" in err and "1 of 2" in err


def test_ingest_names_the_missing_password(monkeypatch, capsys):
    class Locked:
        def enqueue_command(self, stream, command):
            raise RuntimeError("AuthenticationError: HELLO must be called with the "
                               "client already authenticated")

    monkeypatch.setattr(cli, "Bus", Locked)
    assert cli.main(["ingest"], stdin=stdin_of({"snapshots": [es()]})) != 0
    assert ".env" in capsys.readouterr().err


def test_ingest_says_what_it_queued(fake_bus):
    out = io.StringIO()
    cli.main(["ingest"], stdin=stdin_of({"snapshots": [es(), nq()], "status": {"reason": ""}}),
             stdout=out)
    assert json.loads(out.getvalue()) == {"queued": ["snapshot", "snapshot", "pc_status"]}


# ── the command line ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [[], ["unknown"], ["plan", "extra"], ["ingest", "--x"]])
def test_a_bad_command_line_is_a_usage_error(argv, capsys):
    assert cli.main(argv, stdin=io.StringIO("{}")) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_the_cli_calls_neither_schwab_nor_the_proxy():
    tree = ast.parse((ROOT / "tools" / "futures_chain_cli.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert not [n for n in names if any(
        part in n.lower() for part in ("requests", "urllib", "httpx", "schwab", "proxy"))], names


# ── the script the capture runs over ssh ─────────────────────────────────────

REMOTE = ROOT / "tools" / "futures_chain_remote.sh"


def test_remote_script_is_lf_and_loads_the_stack_env():
    raw = REMOTE.read_bytes()
    assert b"\r" not in raw and b". ./.env" in raw and b"set -a" in raw


def test_remote_script_starts_the_cli_from_the_checkout_with_utf8():
    text = REMOTE.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert 'cd "$(dirname "$0")/.."' in text
    assert 'exec .venv/bin/python -X utf8 tools/futures_chain_cli.py "$@"' in text


def test_remote_script_loads_the_env_before_it_starts_python():
    text = REMOTE.read_text(encoding="utf-8")
    assert text.index(". ./.env") < text.index("exec ")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_remote_script_survives_a_stray_line_in_the_env_file(tmp_path):
    # Prod's .env opens with a prose line that lost its '#'. systemd skips such a line, so
    # the services run; under `set -e` it ended this script with 127 before any plan.
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "futures_chain_remote.sh"
    script.write_bytes(REMOTE.read_bytes())
    (tmp_path / ".env").write_bytes(b"  THIS FILE CARRIES SECRETS\nLOADED_LATER=yes\n")
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b'#!/usr/bin/env bash\necho "loaded=$LOADED_LATER args=$*"\n')
    python.chmod(0o755)

    done = subprocess.run(["bash", script.as_posix(), "plan"], capture_output=True, text=True,
                          timeout=30)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "loaded=yes args=-X utf8 tools/futures_chain_cli.py plan"
